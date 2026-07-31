"""``spectral-v1`` — the offline default speaker embedder (FR-4).

16 dimensions of classical source-filter statistics, affinely normalized with
constants committed in ``data/calibration.json``. Scoring is by the calibrated
distance similarity in :mod:`voicekin.engine.verification` (build-stage
deviation from raw cosine, recorded in REVIEW.md), so the embedding is the
whitened feature vector itself — no L2 normalization, which would erase the
distance information the scorer needs.

No feature family may be dead weight: the pitch (dims 0-1), vocal-tract-length
(dims 3-4) and tilt/shape (dims 5-7 plus the 8 band ratios) families are gated
independently by EVALS M1b, so an embedder that silently collapses to pitch
matching fails the suite.
"""

from __future__ import annotations

import numpy as np

from voicekin.adapters.embedder import Embedding
from voicekin.engine.audio import AudioClip
from voicekin.engine.dsp import FrameAnalysis, VoiceFeatures, analyze_voice
from voicekin.models import EMBEDDING_DIM, Calibration

SPECTRAL_EMBEDDER_ID = "spectral-v1"

#: Which raw feature indices belong to which identity axis (EVALS M1b).
FEATURE_FAMILIES: dict[str, tuple[int, ...]] = {
    "f0": (0, 1),
    "vtl": (3, 4),
    "tilt": (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
}


class CalibrationMismatch(ValueError):
    """Calibration constants belong to a different embedder version."""


class SpectralStatsEmbedder:
    """Deterministic DSP embedder. Same clip, same machine, same vector."""

    def __init__(self, calibration: Calibration) -> None:
        if calibration.embedder_id != SPECTRAL_EMBEDDER_ID:
            raise CalibrationMismatch(
                f"calibration is for {calibration.embedder_id!r}, "
                f"not {SPECTRAL_EMBEDDER_ID!r} — refusing to score with foreign constants"
            )
        self._means = np.array([n.mean for n in calibration.feature_norms], dtype=np.float64)
        self._scales = np.array([n.scale for n in calibration.feature_norms], dtype=np.float64)

    @property
    def embedder_id(self) -> str:
        return SPECTRAL_EMBEDDER_ID

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    def embed_features(self, features: VoiceFeatures) -> Embedding:
        """Normalize an already-measured feature vector into an embedding."""
        raw = features.to_vector()
        if raw.shape[0] != EMBEDDING_DIM:  # pragma: no cover - guarded by VoiceFeatures
            raise ValueError(f"expected {EMBEDDING_DIM} raw features, got {raw.shape[0]}")
        return [float(v) for v in (raw - self._means) / self._scales]

    def embed(self, clip: AudioClip, *, frames: FrameAnalysis | None = None) -> Embedding:
        """Measure and normalize a clip (FR-4)."""
        return self.embed_features(analyze_voice(clip, frames))


__all__ = [
    "FEATURE_FAMILIES",
    "SPECTRAL_EMBEDDER_ID",
    "CalibrationMismatch",
    "SpectralStatsEmbedder",
]
