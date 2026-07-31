"""FR-2 audio quality screening. Deterministic, threshold-driven, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from voicekin.engine.audio import AudioClip
from voicekin.engine.dsp import FrameAnalysis, analyze_frames
from voicekin.models import SampleRejectReason, ScreeningLimits


class ClipKind(StrEnum):
    """Which duration window applies (FR-2)."""

    ENROLLMENT = "enrollment"
    CONSENT = "consent"


@dataclass(frozen=True)
class QualityReport:
    """The measurements FR-2 screens on, plus the verdict."""

    kind: ClipKind
    duration_s: float
    clipping_fraction: float
    snr_db: float
    voiced_ratio: float
    reason: SampleRejectReason | None

    @property
    def ok(self) -> bool:
        return self.reason is None


def clipping_fraction(clip: AudioClip, level: float) -> float:
    """Fraction of samples at or beyond ``level`` of full scale."""
    if clip.n_samples == 0:
        return 0.0
    samples = np.abs(np.asarray(clip.samples, dtype=np.float64))
    return float(np.count_nonzero(samples >= level) / samples.shape[0])


def duration_window(kind: ClipKind, limits: ScreeningLimits) -> tuple[float, float]:
    if kind is ClipKind.ENROLLMENT:
        return limits.enroll_min_duration_s, limits.enroll_max_duration_s
    return limits.consent_min_duration_s, limits.consent_max_duration_s


def screen_clip(
    clip: AudioClip,
    kind: ClipKind,
    limits: ScreeningLimits,
    frames: FrameAnalysis | None = None,
) -> QualityReport:
    """Screen a clip for enrollment or consent use (FR-2).

    Checks run in ``SampleRejectReason`` declaration order — duration, clipping,
    SNR, voiced ratio — so the reported reason is the first thing actually wrong
    with the recording.
    """
    analysis = (
        frames
        if frames is not None
        else analyze_frames(
            clip,
            nac_threshold=limits.voiced_nac_threshold,
            energy_margin_db=limits.voiced_energy_margin_db,
        )
    )
    duration = clip.duration_s
    clipped = clipping_fraction(clip, limits.clipping_level)
    snr_db = analysis.snr_db
    voiced_ratio = analysis.voiced_ratio
    min_duration, max_duration = duration_window(kind, limits)

    reason: SampleRejectReason | None = None
    if duration < min_duration:
        reason = SampleRejectReason.TOO_SHORT
    elif duration > max_duration:
        reason = SampleRejectReason.TOO_LONG
    elif clipped > limits.max_clipping_fraction:
        reason = SampleRejectReason.CLIPPED
    elif snr_db < limits.min_snr_db:
        reason = SampleRejectReason.LOW_SNR
    elif voiced_ratio < limits.min_voiced_ratio:
        reason = SampleRejectReason.LOW_VOICED_RATIO

    return QualityReport(
        kind=kind,
        duration_s=duration,
        clipping_fraction=clipped,
        snr_db=snr_db,
        voiced_ratio=voiced_ratio,
        reason=reason,
    )


__all__ = ["ClipKind", "QualityReport", "clipping_fraction", "duration_window", "screen_clip"]
