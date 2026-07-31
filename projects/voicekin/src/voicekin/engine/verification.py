"""FR-5(b) speaker verification scoring and the consent grant decision. Pure."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

import numpy as np

from voicekin.engine.quality import QualityReport
from voicekin.models import ConsentRejectReason

_EPS = 1e-12


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity — the scoring rule for every speaker comparison."""
    left = np.asarray(a, dtype=np.float64)
    right = np.asarray(b, dtype=np.float64)
    if left.shape != right.shape:
        raise ValueError(f"embedding shape mismatch: {left.shape} vs {right.shape}")
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator < _EPS:
        return 0.0
    return float(np.dot(left, right) / denominator)


@dataclass(frozen=True)
class ConsentDecision:
    """Outcome of FR-5(b): verified, or rejected with the first failing check."""

    verified: bool
    reject_reason: ConsentRejectReason | None
    similarity: float | None
    threshold: float | None

    def __post_init__(self) -> None:
        if self.verified and self.reject_reason is not None:
            raise ValueError("a verified consent has no reject reason")
        if not self.verified and self.reject_reason is None:
            raise ValueError("a rejected consent must name a reason")


def evaluate_consent_grant(
    *,
    enrolled_complete: bool,
    quality: QualityReport | None,
    payload_sha256: str | None,
    enrollment_sha256s: Collection[str],
    embed_probe: Callable[[], Sequence[float]],
    centroid: Sequence[float] | None,
    theta_verify: float,
) -> ConsentDecision:
    """Decide a consent grant in the exact FR-5(b) order.

    The checks short-circuit, and ``embed_probe`` is only invoked once the
    preceding checks pass — which is what makes the DATA_MODEL invariant
    ("score fields are non-null exactly when a score was computed") hold.
    """
    if not enrolled_complete or centroid is None:
        return ConsentDecision(False, ConsentRejectReason.NOT_ENROLLED, None, None)
    if quality is None or not quality.ok:
        return ConsentDecision(False, ConsentRejectReason.AUDIO_QUALITY, None, None)
    if payload_sha256 is not None and payload_sha256 in set(enrollment_sha256s):
        return ConsentDecision(False, ConsentRejectReason.REUSED_ENROLLMENT_AUDIO, None, None)
    similarity = cosine_similarity(embed_probe(), centroid)
    if similarity >= theta_verify:
        return ConsentDecision(True, None, similarity, theta_verify)
    return ConsentDecision(False, ConsentRejectReason.SPEAKER_MISMATCH, similarity, theta_verify)


__all__ = ["ConsentDecision", "cosine_similarity", "evaluate_consent_grant"]
