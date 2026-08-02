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


#: Analysis-by-synthesis refinement constants (FR-3). The two calibration
#: sentences are fixed and **vowel-balanced** (every vowel class appears with a
#: bounded share) so the trimmed F1/F2 statistics of a calibration render match
#: what the same analysis measures on balanced enrollment speech — a skewed
#: sentence makes the single ``formant_scale`` knob unfittable because its F1
#: and F2 targets pull in opposite directions. The seed is fixed, so the
#: derivation is a deterministic function of the enrollment analysis (FR-15).
DERIVATION_TEXTS = (
    "the calm dog and the old cat sit by a new fire but you may go up soon",
    "we can see a good film at home if you set the sofa up now",
)
DERIVATION_SEED = 20260731
DERIVATION_UNIT_MS = 180
DERIVATION_ITERATIONS = 8
DERIVATION_DAMPING = 0.6
"""Damping of every correction step. The render-to-measurement map has gains
above 1 on some voices (tilt interacts with the formant stack), so the undamped
fixed-point iteration oscillates; 0.6 keeps |1 - damp*gain| < 1 for gains < 3.3."""

DERIVATION_POLISH_THRESHOLD = 60.0
"""Whitened squared distance above which the coordinate polish runs."""
DERIVATION_POLISH_BUDGET = 44
_F0_STEP_CLIP = 0.2
_TILT_STEP_CLIP_DB = 8.0
_SCALE_STEP_CLIP = (0.75, 1.35)
_BAND_STEP_CLIP_DB = 6.0
_BAND_GAIN_CLIP_DB = 18.0

_DERIVE_CACHE: dict[tuple, VoiceParams] = {}
_DERIVE_CACHE_LIMIT = 64


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


@dataclass(frozen=True)
class _DerivationTargets:
    log_f0: float
    f1: float
    f2: float
    tilt: float
    shares: np.ndarray


def _whiten(vector: np.ndarray, norms: Sequence) -> np.ndarray:
    means = np.array([n.mean for n in norms], dtype=np.float64)
    scales = np.array([n.scale for n in norms], dtype=np.float64)
    return (vector - means) / scales


def _measure(params: VoiceParams, centroid: np.ndarray, norms: Sequence):
    """Average whitened squared distance to the centroid over both calibration
    renders, plus the first render's measurement (drives the correction steps)."""
    from voicekin.engine.dsp import analyze_voice  # local: avoids an import cycle
    from voicekin.engine.synthesis import stub_render

    total = 0.0
    first = None
    for text in DERIVATION_TEXTS:
        measured = analyze_voice(
            stub_render(
                text,
                params,
                sample_rate=16_000,
                seed=DERIVATION_SEED,
                unit_duration_ms=DERIVATION_UNIT_MS,
            )
        )
        if first is None:
            first = measured
        diff = _whiten(measured.to_vector(), norms) - centroid
        total += float(np.dot(diff, diff))
    return total / len(DERIVATION_TEXTS), first


def _step(params: VoiceParams, targets: _DerivationTargets, measured) -> VoiceParams:
    """One damped correction of every knob toward the enrollment targets."""
    damp = DERIVATION_DAMPING
    f0_step = float(
        np.clip(damp * (targets.log_f0 - measured.log_f0_median), -_F0_STEP_CLIP, _F0_STEP_CLIP)
    )
    ratio = 0.5 * (
        targets.f1 / max(measured.f1_median, 1.0) + targets.f2 / max(measured.f2_median, 1.0)
    )
    scale_step = float(np.clip(ratio**damp, *_SCALE_STEP_CLIP))
    tilt_step = float(
        np.clip(damp * (targets.tilt - measured.tilt_db_oct), -_TILT_STEP_CLIP_DB, _TILT_STEP_CLIP_DB)
    )
    measured_shares = np.maximum(np.asarray(measured.band_ratios), 1e-4)
    # Shares are sqrt energy shares, so the energy correction is 40*log10.
    band_step = np.clip(
        damp * 40.0 * np.log10(np.maximum(targets.shares, 1e-4) / measured_shares),
        -_BAND_STEP_CLIP_DB,
        _BAND_STEP_CLIP_DB,
    )
    gains = np.asarray(params.band_gains_db or (0.0,) * len(band_step)) + band_step
    gains = np.clip(gains, -_BAND_GAIN_CLIP_DB, _BAND_GAIN_CLIP_DB)
    gains -= float(np.mean(gains))  # overall level and slope belong to tilt
    return VoiceParams(
        f0_base_hz=params.f0_base_hz * float(np.exp(f0_step)),
        f0_range_hz=params.f0_range_hz,
        formant_scale=float(np.clip(params.formant_scale * scale_step, *FORMANT_SCALE_RANGE)),
        tilt_db_oct=float(np.clip(params.tilt_db_oct + tilt_step, *TILT_RANGE_DB_OCT)),
        band_gains_db=tuple(float(g) for g in gains),
    )


def _polish_candidates(params: VoiceParams, step_scale: float):
    """Deterministic +/- probes of every knob, for the coordinate polish."""
    yield VoiceParams(
        f0_base_hz=params.f0_base_hz,
        f0_range_hz=params.f0_range_hz,
        formant_scale=params.formant_scale,
        tilt_db_oct=float(np.clip(params.tilt_db_oct + 1.5 * step_scale, *TILT_RANGE_DB_OCT)),
        band_gains_db=params.band_gains_db,
    )
    yield VoiceParams(
        f0_base_hz=params.f0_base_hz,
        f0_range_hz=params.f0_range_hz,
        formant_scale=params.formant_scale,
        tilt_db_oct=float(np.clip(params.tilt_db_oct - 1.5 * step_scale, *TILT_RANGE_DB_OCT)),
        band_gains_db=params.band_gains_db,
    )
    for sign in (1.0, -1.0):
        yield VoiceParams(
            f0_base_hz=params.f0_base_hz,
            f0_range_hz=params.f0_range_hz,
            formant_scale=float(
                np.clip(params.formant_scale * np.exp(sign * 0.03 * step_scale), *FORMANT_SCALE_RANGE)
            ),
            tilt_db_oct=params.tilt_db_oct,
            band_gains_db=params.band_gains_db,
        )
    for sign in (1.0, -1.0):
        yield VoiceParams(
            f0_base_hz=params.f0_base_hz * float(np.exp(sign * 0.01 * step_scale)),
            f0_range_hz=params.f0_range_hz,
            formant_scale=params.formant_scale,
            tilt_db_oct=params.tilt_db_oct,
            band_gains_db=params.band_gains_db,
        )
    gains = params.band_gains_db or (0.0,) * 8
    for index in range(len(gains)):
        for sign in (1.0, -1.0):
            adjusted = np.asarray(gains, dtype=np.float64)
            adjusted[index] = np.clip(
                adjusted[index] + sign * 1.5 * step_scale, -_BAND_GAIN_CLIP_DB, _BAND_GAIN_CLIP_DB
            )
            adjusted -= float(np.mean(adjusted))
            yield VoiceParams(
                f0_base_hz=params.f0_base_hz,
                f0_range_hz=params.f0_range_hz,
                formant_scale=params.formant_scale,
                tilt_db_oct=params.tilt_db_oct,
                band_gains_db=tuple(float(g) for g in adjusted),
            )


def derive_voice_params(
    features: Sequence[VoiceFeatures],
    *,
    centroid: Sequence[float],
    feature_norms: Sequence,
) -> VoiceParams:
    """Derive the offline synthesizer's parameters from enrollment analysis (FR-3).

    Deterministic analysis-by-synthesis (REVIEW.md build deviation 4). Stage 1:
    direct estimates — pitch from the log-F0 median, declination span from the
    log-F0 IQR, the vocal-tract-length proxy from F1/F2 against the
    dev-measured anchors, source tilt from the spectral regression minus lip
    radiation. Stage 2: **damped iterative refinement** — render the two fixed
    calibration sentences through the stub, re-measure them with the same FR-4
    analysis, and correct ``f0_base``, ``formant_scale``, ``tilt_db_oct`` and
    the eight ``band_gains_db`` so the *measured* render matches the *measured*
    enrollment; among the iterates, keep the one whose renders sit closest to
    the profile's own whitened centroid (the actual objective — the feature
    matching alone is a proxy whose fixed point need not minimise it). Stage 3:
    when the selected iterate is still far (rare voices at the edges of the
    knob ranges), a budgeted deterministic coordinate polish on the same
    objective. This inversion is what makes EVALS M3 hold: the stub's
    render-to-measurement map is not the identity, and inverting it per profile
    keeps the rendered voice on top of its own enrollment instead of merely
    near it.

    ``centroid`` and ``feature_norms`` come from the same enrollment the
    features do — the derivation uses only the profile's own data (FR-15).
    Results are memoized: the derivation is a pure function of its arguments
    and enrollment re-derives it on every sample mutation.
    """
    if not features:
        raise ValueError("voice parameters need at least one analysed sample")

    key = (
        tuple(tuple(np.round(f.to_vector(), 9)) for f in features),
        tuple(float(np.round(c, 9)) for c in centroid),
        tuple((float(n.mean), float(n.scale)) for n in feature_norms),
    )
    cached = _DERIVE_CACHE.get(key)
    if cached is not None:
        return cached

    centroid_vec = np.asarray(centroid, dtype=np.float64)
    targets = _DerivationTargets(
        log_f0=float(np.median([f.log_f0_median for f in features])),
        f1=float(np.median([f.f1_median for f in features])),
        f2=float(np.median([f.f2_median for f in features])),
        tilt=float(np.median([f.tilt_db_oct for f in features])),
        shares=np.median(np.stack([np.asarray(f.band_ratios) for f in features]), axis=0),
    )

    params = _initial_voice_params(features)
    best_params, best_d2 = params, float("inf")
    for _ in range(DERIVATION_ITERATIONS):
        d2, measured = _measure(params, centroid_vec, feature_norms)
        if d2 < best_d2:
            best_params, best_d2 = params, d2
        params = _step(params, targets, measured)

    if best_d2 > DERIVATION_POLISH_THRESHOLD:
        budget = DERIVATION_POLISH_BUDGET
        for step_scale in (1.0, 0.5):
            improved = True
            while improved and budget > 0 and best_d2 > DERIVATION_POLISH_THRESHOLD / 2:
                improved = False
                for candidate in _polish_candidates(best_params, step_scale):
                    if budget <= 0:
                        break
                    d2, _ = _measure(candidate, centroid_vec, feature_norms)
                    budget -= 1
                    if d2 < best_d2 - 1e-9:
                        best_params, best_d2 = candidate, d2
                        improved = True
                        break

    if len(_DERIVE_CACHE) >= _DERIVE_CACHE_LIMIT:
        _DERIVE_CACHE.clear()
    _DERIVE_CACHE[key] = best_params
    return best_params


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
