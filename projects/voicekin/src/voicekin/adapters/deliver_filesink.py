"""Offline default deliverer: WAV + sidecar manifest into a directory (FR-10/FR-11).

Already useful on its own — Home Assistant can watch a media directory — and the
manifest is what makes a delivered copy self-describing: it names the utterance,
the consent that authorized it, the PCM-payload hash and the audit sequence, so a
file found later can be traced back without the database.
"""

from __future__ import annotations

import json
from pathlib import Path

from voicekin.adapters.deliver import DeliveryPayload, DeliveryReceipt
from voicekin.engine.consent import Authorization
from voicekin.models import DeliveryStatus, DeviceTarget, TargetKind

MANIFEST_SUFFIX = ".json"


class FileSinkDeliverer:
    """Writes ``<dir>/<utterance_id>.wav`` and ``<dir>/<utterance_id>.json``."""

    @property
    def kind(self) -> TargetKind:
        return TargetKind.FILE_SINK

    def deliver(
        self,
        payload: DeliveryPayload,
        target: DeviceTarget,
        *,
        authorization: Authorization,
    ) -> DeliveryReceipt:
        if target.kind is not TargetKind.FILE_SINK:
            raise ValueError(f"FileSinkDeliverer cannot serve a {target.kind} target")
        if authorization.profile_id != payload.manifest.get("profile_id"):
            raise ValueError("authorization does not cover this utterance's profile")

        directory = Path(str(target.config["dir"]))
        wav_path = directory / f"{payload.utterance_id}.wav"
        manifest_path = directory / f"{payload.utterance_id}{MANIFEST_SUFFIX}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            wav_path.write_bytes(payload.wav_bytes)
            manifest_path.write_text(
                json.dumps(payload.manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.FAILED,
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
        return DeliveryReceipt(
            status=DeliveryStatus.SUCCEEDED,
            detail={"path": str(wav_path), "manifest_path": str(manifest_path)},
        )


__all__ = ["MANIFEST_SUFFIX", "FileSinkDeliverer"]
