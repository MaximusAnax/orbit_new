"""Live deliverer: Home Assistant ``media_player.play_media`` (FR-11).

Unlike the other two live adapters this one ships: it is a small REST call over
stdlib ``urllib``, with no heavy dependency. It activates only when both
``VOICEKIN_HA_URL`` and ``VOICEKIN_HA_TOKEN`` are set — credentials come from the
environment and are never stored in the database (DATA_MODEL ``device_target``).

Securing the network path to Home Assistant is out of scope (SCOPE threat model):
the token is bearer-authenticated and TLS is Home Assistant's job.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from voicekin.adapters.deliver import DeliveryPayload, DeliveryReceipt
from voicekin.engine.consent import Authorization
from voicekin.models import DeliveryStatus, DeviceTarget, TargetKind

HA_URL_ENV = "VOICEKIN_HA_URL"
HA_TOKEN_ENV = "VOICEKIN_HA_TOKEN"
PLAY_MEDIA_PATH = "/api/services/media_player/play_media"
DEFAULT_TIMEOUT_S = 10.0
MEDIA_SOURCE_PREFIX = "media-source://media_source/local"


def home_assistant_configured() -> bool:
    """True when both credentials are present in the environment."""
    return bool(os.environ.get(HA_URL_ENV)) and bool(os.environ.get(HA_TOKEN_ENV))


class HomeAssistantDeliverer:
    """Copies the WAV into HA's media directory and asks a player to play it."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        resolved_url = base_url or os.environ.get(HA_URL_ENV)
        resolved_token = token or os.environ.get(HA_TOKEN_ENV)
        if not resolved_url or not resolved_token:
            raise RuntimeError(
                f"HomeAssistantDeliverer needs {HA_URL_ENV} and {HA_TOKEN_ENV} to be set"
            )
        self.base_url = resolved_url.rstrip("/")
        self._token = resolved_token
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> HomeAssistantDeliverer | None:
        """Build the deliverer, or ``None`` when it is not configured."""
        if not home_assistant_configured():
            return None
        return cls()

    @property
    def kind(self) -> TargetKind:
        return TargetKind.HOME_ASSISTANT

    def _post_play_media(self, entity_id: str, media_content_id: str) -> int:
        body = json.dumps(
            {
                "entity_id": entity_id,
                "media_content_id": media_content_id,
                "media_content_type": "music",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.base_url}{PLAY_MEDIA_PATH}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return int(response.status)

    def deliver(
        self,
        payload: DeliveryPayload,
        target: DeviceTarget,
        *,
        authorization: Authorization,
    ) -> DeliveryReceipt:
        if target.kind is not TargetKind.HOME_ASSISTANT:
            raise ValueError(f"HomeAssistantDeliverer cannot serve a {target.kind} target")
        if authorization.profile_id != payload.manifest.get("profile_id"):
            raise ValueError("authorization does not cover this utterance's profile")

        media_dir = Path(str(target.config["media_dir"]))
        entity_id = str(target.config["entity_id"])
        filename = f"{payload.utterance_id}.wav"
        media_path = media_dir / filename
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            media_path.write_bytes(payload.wav_bytes)
        except OSError as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.FAILED,
                detail={"error": f"{type(exc).__name__}: {exc}", "entity_id": entity_id},
            )

        media_content_id = f"{MEDIA_SOURCE_PREFIX}/{filename}"
        try:
            status = self._post_play_media(entity_id, media_content_id)
        except urllib.error.HTTPError as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.FAILED,
                detail={
                    "path": str(media_path),
                    "entity_id": entity_id,
                    "http_status": int(exc.code),
                    "error": f"HTTPError: {exc.reason}",
                },
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.FAILED,
                detail={
                    "path": str(media_path),
                    "entity_id": entity_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        return DeliveryReceipt(
            status=DeliveryStatus.SUCCEEDED,
            detail={"path": str(media_path), "entity_id": entity_id, "http_status": status},
        )


__all__ = [
    "HA_TOKEN_ENV",
    "HA_URL_ENV",
    "MEDIA_SOURCE_PREFIX",
    "PLAY_MEDIA_PATH",
    "HomeAssistantDeliverer",
    "home_assistant_configured",
]
