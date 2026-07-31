"""FR-3 enrollment: centroid, coherence, fingerprint, derived voice parameters.

All pure. The leave-one-out coherence check is the defence the fingerprint
binding cannot provide: a fingerprint hashes whatever sample set is present, so
a *poisoned* set (two of mine, one of yours) would bind perfectly well. EVALS M6
measures this check directly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from voicekin.engine.dsp import VoiceFeatures
from voicekin.engine.verification import cosine_similarity
from voicekin.engine.voicebox import RADIATION_DB_OCT, REFERENCE_F1_HZ, REFERENCE_F2_HZ
from voicekin.models import EnrollmentSample, SampleStatus, VoiceParams

MIN_ACCEPTED_SAMPLES = 3
MIN_VOICED_SECONDS = 10.0
"""FR-3 enrollment minimums."""

FORMANT_SCALE_RANGE = (0.60, 1.60)
TILT_RANGE_DB_OCT = (-24.0, 0.0)
MAX_RELATIVE_F0_RANGE = 0.5

_EPS = 1e-12


def l2_normalize(vector: Sequence[float]) -> list[float]:
    """Unit-length copy of ``vector``; an all-zero vector is returned unchanged."""
    array = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(array))
    if norm < _EPS:
        return [float(v) for v in array]
    return [float(v) for v in array / norm]


def centroid(embeddings: Sequence[Sequence[float]]) -> list[float]:
    """L2-normalized mean of the sample embeddings (FR-3)."""
    if not embeddings:
        raise ValueError("a centroid needs at least one embedding")
    stacked = np.asarray(embeddings, dtype=np.float64)
    if stacked.ndim != 2:
        raise ValueError("embeddings must all share one dimensionality")
    return l2_normalize(stacked.mean(axis=0))


def leave_one_out_scores(embeddings: Sequence[Sequence[float]]) -> list[float]:
    """Each embedding scored against the centroid of the *other* embeddings."""
    if len(embeddings) < 2:
        raise ValueError("leave-one-out coherence needs at least two embeddings")
    scores: list[float] = []
    for index in range(len(embeddings)):
        others = [e for position, e in enumerate(embeddings) if position != index]
        scores.append(cosine_similarity(embeddings[index], centroid(others)))
    return scores


@dataclass(frozen=True)
class CoherenceResult:
    """Outcome of the FR-3 leave-one-out check."""

    coherent: bool
    scores: tuple[float, ...]
    min_score: float
    worst_index: int
    theta_enroll: float


def check_coherence(embeddings: Sequence[Sequence[float]], theta_enroll: float) -> CoherenceResult:
    """Refuse an enrollment set whose members do not agree on one speaker."""
    scores = leave_one_out_scores(embeddings)
    worst_index = int(np.argmin(scores))
    min_score = float(scores[worst_index])
    return CoherenceResult(
        coherent=min_score >= theta_enroll,
        scores=tuple(float(s) for s in scores),
        min_score=min_score,
        worst_index=worst_index,
        theta_enroll=theta_enroll,
    )


def enrollment_fingerprint(embedder_id: str, sample_sha256s: Iterable[str]) -> str:
    """``sha256(embedder_id ‖ sorted per-sample sha256s)`` (FR-3).

    Any sample add or remove, and any embedder change, produces a new value —
    which is what makes a consent record stop authorizing the moment the
    enrollment it was granted against changes.
    """
    material = "|".join([embedder_id, *sorted(sample_sha256s)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def accepted_samples(samples: Iterable[EnrollmentSample]) -> list[EnrollmentSample]:
    return [s for s in samples if s.status is SampleStatus.ACCEPTED]


def voiced_seconds(samples: Iterable[EnrollmentSample]) -> float:
    return float(sum(s.voiced_seconds for s in samples))


def is_enrolled_complete(samples: Sequence[EnrollmentSample]) -> bool:
    """≥ 3 accepted samples totalling ≥ 10 s of voiced audio (FR-3)."""
    accepted = accepted_samples(samples)
    return len(accepted) >= MIN_ACCEPTED_SAMPLES and voiced_seconds(accepted) >= MIN_VOICED_SECONDS


def derive_voice_params(features: Sequence[VoiceFeatures]) -> VoiceParams:
    """Derive the offline synthesizer's parameters from enrollment analysis (FR-3).

    Medians across samples, so one atypical take cannot drag the voice: pitch
    from the log-F0 median, declination span from the log-F0 IQR, the
    vocal-tract-length proxy from F1/F2 relative to the reference vowel space,
    and the source tilt straight from the spectral regression.
    """
    if not features:
        raise ValueError("voice parameters need at least one analysed sample")
    log_f0 = float(np.median([f.log_f0_median for f in features]))
    f0_base = float(np.exp(log_f0))
    relative_range = float(np.median([f.log_f0_iqr for f in features]))
    f0_range = f0_base * float(np.clip(relative_range, 0.0, MAX_RELATIVE_F0_RANGE))

    f1 = float(np.median([f.f1_median for f in features]))
    f2 = float(np.median([f.f2_median for f in features]))
    scale = 0.5 * (f1 / REFERENCE_F1_HZ + f2 / REFERENCE_F2_HZ)
    formant_scale = float(np.clip(scale, *FORMANT_SCALE_RANGE))

    # The measured slope includes lip radiation; the synthesizer wants the
    # *source* slope, so the round trip analyse -> derive -> synthesize -> analyse
    # is a fixed point rather than drifting +6 dB/octave each pass.
    tilt = float(np.median([f.tilt_db_oct for f in features])) - RADIATION_DB_OCT
    return VoiceParams(
        f0_base_hz=f0_base,
        f0_range_hz=f0_range,
        formant_scale=formant_scale,
        tilt_db_oct=float(np.clip(tilt, *TILT_RANGE_DB_OCT)),
    )


__all__ = [
    "MIN_ACCEPTED_SAMPLES",
    "MIN_VOICED_SECONDS",
    "CoherenceResult",
    "accepted_samples",
    "centroid",
    "check_coherence",
    "derive_voice_params",
    "enrollment_fingerprint",
    "is_enrolled_complete",
    "l2_normalize",
    "leave_one_out_scores",
    "voiced_seconds",
]
