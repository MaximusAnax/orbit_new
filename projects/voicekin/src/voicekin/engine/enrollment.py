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
from voicekin.engine.verification import DEFAULT_SCORE_SCALE, distance_similarity
from voicekin.engine.voicebox import RADIATION_DB_OCT
from voicekin.models import EnrollmentSample, SampleStatus, VoiceParams

MIN_ACCEPTED_SAMPLES = 3
MIN_VOICED_SECONDS = 10.0
"""FR-3 enrollment minimums."""

FORMANT_SCALE_RANGE = (0.60, 1.60)
TILT_RANGE_DB_OCT = (-24.0, 0.0)
MAX_RELATIVE_F0_RANGE = 0.5

#: What the FR-4 estimator reports for a ``formant_scale = 1.0`` voice: the
#: measured F1/F2 trimmed means over balanced pseudo-speech, per axis. Derived
#: from the dev fixture split (the measured location divided by the generating
#: vocal-tract factor is stable to a few percent across speakers), so the
#: derived ``formant_scale`` lands near the vocal-tract factor that actually
#: produced the enrollment audio.
F1_ANCHOR_HZ = 520.0
F2_ANCHOR_HZ = 1080.0

_EPS = 1e-12


def l2_normalize(vector: Sequence[float]) -> list[float]:
    """Unit-length copy of ``vector``; an all-zero vector is returned unchanged."""
    array = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(array))
    if norm < _EPS:
        return [float(v) for v in array]
    return [float(v) for v in array / norm]


def centroid(embeddings: Sequence[Sequence[float]]) -> list[float]:
    """Mean of the sample embeddings (FR-3).

    Build-stage deviation (REVIEW.md): the mean is *not* L2-normalized, because
    scoring is by calibrated Euclidean distance and normalizing the centroid
    would move it off the samples' true center.
    """
    if not embeddings:
        raise ValueError("a centroid needs at least one embedding")
    stacked = np.asarray(embeddings, dtype=np.float64)
    if stacked.ndim != 2:
        raise ValueError("embeddings must all share one dimensionality")
    return [float(v) for v in stacked.mean(axis=0)]


def leave_one_out_scores(
    embeddings: Sequence[Sequence[float]],
    *,
    score_scale: float = DEFAULT_SCORE_SCALE,
) -> list[float]:
    """Each embedding scored against the centroid of the *other* embeddings."""
    if len(embeddings) < 2:
        raise ValueError("leave-one-out coherence needs at least two embeddings")
    scores: list[float] = []
    for index in range(len(embeddings)):
        others = [e for position, e in enumerate(embeddings) if position != index]
        scores.append(
            distance_similarity(embeddings[index], centroid(others), score_scale=score_scale)
        )
    return scores


@dataclass(frozen=True)
class CoherenceResult:
    """Outcome of the FR-3 leave-one-out check."""

    coherent: bool
    scores: tuple[float, ...]
    min_score: float
    worst_index: int
    theta_enroll: float


def check_coherence(
    embeddings: Sequence[Sequence[float]],
    theta_enroll: float,
    *,
    score_scale: float = DEFAULT_SCORE_SCALE,
) -> CoherenceResult:
    """Refuse an enrollment set whose members do not agree on one speaker."""
    scores = leave_one_out_scores(embeddings, score_scale=score_scale)
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


#: Analysis-by-synthesis refinement constants (FR-3). The calibration sentence
#: is fixed, English-like, and long enough (~6 s) for stable measurements; the
#: seed is fixed, so the derivation is a deterministic function of the
#: enrollment features (FR-15).
DERIVATION_TEXT = "the house is ready and the morning is quiet now so we can begin"
DERIVATION_SEED = 20260731
DERIVATION_UNIT_MS = 180
DERIVATION_ITERATIONS = 2
_F0_STEP_CLIP = 0.2
_TILT_STEP_CLIP_DB = 8.0
_SCALE_STEP_CLIP = (0.75, 1.35)


def _initial_voice_params(features: Sequence[VoiceFeatures]) -> VoiceParams:
    """Direct estimates: the starting point of the refinement."""
    log_f0 = float(np.median([f.log_f0_median for f in features]))
    f0_base = float(np.exp(log_f0))
    relative_range = float(np.median([f.log_f0_iqr for f in features]))
    f0_range = f0_base * float(np.clip(relative_range, 0.0, MAX_RELATIVE_F0_RANGE))
    f1 = float(np.median([f.f1_median for f in features]))
    f2 = float(np.median([f.f2_median for f in features]))
    scale = 0.5 * (f1 / F1_ANCHOR_HZ + f2 / F2_ANCHOR_HZ)
    # The measured slope includes lip radiation; the synthesizer wants the
    # *source* slope.
    tilt = float(np.median([f.tilt_db_oct for f in features])) - RADIATION_DB_OCT
    return VoiceParams(
        f0_base_hz=f0_base,
        f0_range_hz=f0_range,
        formant_scale=float(np.clip(scale, *FORMANT_SCALE_RANGE)),
        tilt_db_oct=float(np.clip(tilt, *TILT_RANGE_DB_OCT)),
    )


def derive_voice_params(features: Sequence[VoiceFeatures]) -> VoiceParams:
    """Derive the offline synthesizer's parameters from enrollment analysis (FR-3).

    Two stages, both deterministic. First, direct estimates: pitch from the
    log-F0 median across samples, declination span from the log-F0 IQR, the
    vocal-tract-length proxy from F1/F2 against the dev-measured anchors, source
    tilt from the spectral regression minus lip radiation. Then
    **analysis-by-synthesis refinement**: render a fixed calibration sentence
    through the stub, re-measure it with the same FR-4 analysis, and correct
    ``f0_base``, ``formant_scale`` and ``tilt`` so the *measured* render matches
    the *measured* enrollment. The refinement is what makes EVALS M3 hold: the
    stub's render-to-measurement map is not the identity (its F1 shifts with
    text content, and its formant stack tilts the 450-1600 Hz band by an amount
    that depends on the formant scale), and inverting it per profile keeps the
    rendered voice on top of its own enrollment instead of merely near it.
    """
    if not features:
        raise ValueError("voice parameters need at least one analysed sample")
    from voicekin.engine.dsp import analyze_voice  # local: avoids an import cycle
    from voicekin.engine.synthesis import stub_render

    target_log_f0 = float(np.median([f.log_f0_median for f in features]))
    target_f1 = float(np.median([f.f1_median for f in features]))
    target_f2 = float(np.median([f.f2_median for f in features]))
    target_tilt = float(np.median([f.tilt_db_oct for f in features]))

    params = _initial_voice_params(features)
    for _ in range(DERIVATION_ITERATIONS):
        rendered = stub_render(
            DERIVATION_TEXT,
            params,
            sample_rate=16_000,
            seed=DERIVATION_SEED,
            unit_duration_ms=DERIVATION_UNIT_MS,
        )
        measured = analyze_voice(rendered)
        f0_step = float(
            np.clip(target_log_f0 - measured.log_f0_median, -_F0_STEP_CLIP, _F0_STEP_CLIP)
        )
        scale_step = float(
            np.clip(
                0.5 * (target_f1 / max(measured.f1_median, 1.0)
                       + target_f2 / max(measured.f2_median, 1.0)),
                *_SCALE_STEP_CLIP,
            )
        )
        tilt_step = float(
            np.clip(
                target_tilt - measured.tilt_db_oct, -_TILT_STEP_CLIP_DB, _TILT_STEP_CLIP_DB
            )
        )
        params = VoiceParams(
            f0_base_hz=params.f0_base_hz * float(np.exp(f0_step)),
            f0_range_hz=params.f0_range_hz,
            formant_scale=float(
                np.clip(params.formant_scale * scale_step, *FORMANT_SCALE_RANGE)
            ),
            tilt_db_oct=float(np.clip(params.tilt_db_oct + tilt_step, *TILT_RANGE_DB_OCT)),
        )
    return params


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
