"""FR-11 live deliverer: HomeAssistantDeliverer, hermetically (no network).

The HTTP transport is monkeypatched — these tests pin the env-gating, the WAV
copy into the shared media dir, the exact ``play_media`` request (URL, bearer
token, entity id, media_content_id) and the receipt mapping for success and for
each failure class. The one thing they cannot certify is a real Home Assistant
accepting the call (SCOPE non-goal 8; recorded in docs/FR_COVERAGE.md).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest
from voicekin.adapters.deliver import DeliveryPayload
from voicekin.adapters.deliver_hass import (
    HA_TOKEN_ENV,
    HA_URL_ENV,
    MEDIA_SOURCE_PREFIX,
    HomeAssistantDeliverer,
    home_assistant_configured,
)
from voicekin.engine.consent import SynthesisRequest, authorize
from voicekin.models import (
    ConsentRecord,
    ConsentStatus,
    Context,
    DeliveryStatus,
    DeviceTarget,
    TargetKind,
    VoiceProfile,
)

T0 = "2026-07-31T12:00:00Z"
FINGERPRINT = "a" * 64


def _authorization(profile_id: str = "partner"):
    """A genuine Authorization — only authorize() can construct one (FR-6)."""
    profile = VoiceProfile(
        id=profile_id,
        display_name="Amina",
        embedder_id="spectral-v1",
        centroid=[0.0] * 16,
        enrollment_fingerprint=FINGERPRINT,
        created_at=T0,
        updated_at=T0,
    )
    consent = ConsentRecord(
        id="0" * 32,
        profile_id=profile_id,
        draft_index=0,
        status=ConsentStatus.VERIFIED,
        scope_contexts=[Context.ANNOUNCEMENT],
        nonce_seed=1,
        nonce="AAAAAAAA",
        statement_text="I consent.",
        audio_sha256="c" * 64,
        similarity=0.99,
        threshold=0.8,
        embedder_id="spectral-v1",
        enrollment_fingerprint=FINGERPRINT,
        drafted_at=T0,
        decided_at=T0,
    )
    outcome = authorize(
        profile, [consent], SynthesisRequest(profile_id, Context.ANNOUNCEMENT), T0
    )
    assert not hasattr(outcome, "reason"), outcome
    return outcome


def _payload(utterance_id: str = "u" * 32, profile_id: str = "partner") -> DeliveryPayload:
    return DeliveryPayload(
        utterance_id=utterance_id,
        wav_bytes=b"RIFFfakewav",
        output_sha256="b" * 64,
        manifest={"profile_id": profile_id, "utterance_id": utterance_id},
    )


def _target(tmp_path) -> DeviceTarget:
    return DeviceTarget(
        id="living-room",
        kind=TargetKind.HOME_ASSISTANT,
        config={"entity_id": "media_player.living_room", "media_dir": str(tmp_path / "media")},
        created_at=T0,
    )


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_fr11_hass_env_gating(monkeypatch):
    monkeypatch.delenv(HA_URL_ENV, raising=False)
    monkeypatch.delenv(HA_TOKEN_ENV, raising=False)
    assert not home_assistant_configured()
    assert HomeAssistantDeliverer.from_env() is None
    with pytest.raises(RuntimeError, match=HA_URL_ENV):
        HomeAssistantDeliverer()
    monkeypatch.setenv(HA_URL_ENV, "http://ha.local:8123/")
    monkeypatch.delenv(HA_TOKEN_ENV, raising=False)
    assert not home_assistant_configured()
    monkeypatch.setenv(HA_TOKEN_ENV, "sekrit")
    assert home_assistant_configured()
    deliverer = HomeAssistantDeliverer.from_env()
    assert deliverer is not None
    assert deliverer.base_url == "http://ha.local:8123"  # trailing slash stripped


def test_fr11_hass_success_copies_wav_and_posts_play_media(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    deliverer = HomeAssistantDeliverer("http://ha.local:8123", "sekrit")
    payload = _payload()
    target = _target(tmp_path)
    receipt = deliverer.deliver(payload, target, authorization=_authorization())

    assert receipt.status is DeliveryStatus.SUCCEEDED
    media_path = tmp_path / "media" / f"{payload.utterance_id}.wav"
    assert media_path.read_bytes() == payload.wav_bytes
    assert receipt.detail == {
        "path": str(media_path),
        "entity_id": "media_player.living_room",
        "http_status": 200,
    }
    assert captured["url"] == "http://ha.local:8123/api/services/media_player/play_media"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer sekrit"
    assert captured["body"] == {
        "entity_id": "media_player.living_room",
        "media_content_id": f"{MEDIA_SOURCE_PREFIX}/{payload.utterance_id}.wav",
        "media_content_type": "music",
    }


def test_fr11_hass_http_error_maps_to_failed_with_status(tmp_path, monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    deliverer = HomeAssistantDeliverer("http://ha.local:8123", "expired")
    payload = _payload()
    receipt = deliverer.deliver(payload, _target(tmp_path), authorization=_authorization())
    assert receipt.status is DeliveryStatus.FAILED
    assert receipt.detail["http_status"] == 401
    assert "Unauthorized" in receipt.detail["error"]
    # The WAV copy happened before the call failed — the receipt still names it.
    assert receipt.detail["path"].endswith(f"{payload.utterance_id}.wav")


def test_fr11_hass_network_error_maps_to_failed(tmp_path, monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    deliverer = HomeAssistantDeliverer("http://ha.local:8123", "sekrit")
    receipt = deliverer.deliver(_payload(), _target(tmp_path), authorization=_authorization())
    assert receipt.status is DeliveryStatus.FAILED
    assert "URLError" in receipt.detail["error"]


def test_fr11_hass_unwritable_media_dir_fails_without_posting(tmp_path, monkeypatch):
    def exploding_urlopen(request, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("no HTTP call may happen when the media copy fails")

    monkeypatch.setattr(urllib.request, "urlopen", exploding_urlopen)
    blocker = tmp_path / "media"
    blocker.write_text("a file where the media dir should be")
    deliverer = HomeAssistantDeliverer("http://ha.local:8123", "sekrit")
    receipt = deliverer.deliver(_payload(), _target(tmp_path), authorization=_authorization())
    assert receipt.status is DeliveryStatus.FAILED
    assert "error" in receipt.detail


def test_fr11_hass_refuses_foreign_targets_and_foreign_authorizations(tmp_path):
    deliverer = HomeAssistantDeliverer("http://ha.local:8123", "sekrit")
    file_sink = DeviceTarget(
        id="sink", kind=TargetKind.FILE_SINK, config={"dir": str(tmp_path)}, created_at=T0
    )
    with pytest.raises(ValueError, match="cannot serve"):
        deliverer.deliver(_payload(), file_sink, authorization=_authorization())
    with pytest.raises(ValueError, match="does not cover"):
        deliverer.deliver(
            _payload(profile_id="somebody-else"),
            _target(tmp_path),
            authorization=_authorization("partner"),
        )
