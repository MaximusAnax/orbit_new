"""Dev-split threshold calibration -> ``data/calibration.json`` (EVALS.md).

Calibration touches **only** the 12 dev speakers; the 24 eval speakers are never
read here, exactly as NIST SRE practice requires of a threshold set that is later
measured on a disjoint population.

What this script derives (build-stage scoring deviations recorded in REVIEW.md):

* **16 feature normalization constants** from dev statistics. ``mean`` is the
  population mean of the dev enrollment takes. ``scale`` is a *robust*
  within-speaker standard deviation — ``sqrt((max_s sd_s^2 + pooled sd^2)/2)``
  over every dev speaker's clean takes — divided by an F-ratio term
  ``sqrt(clip(F-1, 0.05, 16))``. The robust sd protects the leave-one-out
  coherence check from per-speaker heteroscedasticity (one dev speaker's band
  shares swing 3x the pooled sd with her consonant draw); the F term weights
  each dimension by how much of its variance is actually between speakers,
  boosting a genuinely discriminative dimension by up to 4x and damping a
  noise-dominated one by up to ~4.5x.
* **score_scale** — the committed denominator of the distance similarity
  ``s = 1 - ||a - b||^2 / score_scale``. A design constant (1024): it fixes the
  unit of the score axis and cancels out of every rank-based metric.
* **theta_verify** — placed 60 % of the way from the maximum dev impostor score
  to the minimum dev *clean genuine* consent score, asserting a separation
  >= 0.05 in score units. Above the midpoint by design: a false accept is the
  catastrophic error (SCOPE decision 3), so the threshold sits closer to the
  genuine side. Clean-only on the genuine side is deliberate: letting harsh or
  channel-mismatched takes drag the threshold down would trade the catastrophic
  error for the benign one.
* **theta_enroll** — placed 10 % of the way from the minimum dev *pure-set*
  leave-one-out score down to the maximum dev *mixed-set* score, same margin
  assertion. Close to the pure side for the same safety asymmetry: a mixed set
  that enrolls is a partial voice theft (EVALS M6), a rejected pure set is a
  re-recording.

Everything else in ``calibration.json`` is a design constant, restated here so
the file is wholly reproducible from this script.

Usage::

    uv run python voicekin/evals/fixtures/calibrate.py           # report only
    uv run python voicekin/evals/fixtures/calibrate.py --write   # rewrite data/calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in (None, ""):  # `python voicekin/evals/fixtures/calibrate.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from voicekin.engine.enrollment import leave_one_out_scores
from voicekin.models import EMBEDDING_DIM, Calibration

from evals import corpus

CALIBRATION_PATH = Path(__file__).resolve().parents[3] / "voicekin" / "data" / "calibration.json"
EMBEDDER_ID = "spectral-v1"
SEED = 20260731

MIN_MARGIN = 0.05
"""Both thresholds must sit inside a >= 0.05 score-unit gap on the dev split."""

SCORE_SCALE = 1024.0
"""Denominator of the distance similarity (see module docstring)."""

F_DAMPING_RANGE = (0.05, 16.0)
"""``scale = robust_sd / sqrt(clip(F - 1, *F_DAMPING_RANGE))``: a dimension whose
between-speaker variance barely exceeds its within-speaker variance (F ~ 1)
contributes little; a strongly speaker-discriminative dimension (F >> 17) is
boosted by up to 4x, which the distance geometry — unlike cosine — supports
without diluting any other dimension."""

THETA_VERIFY_PLACEMENT = 0.6
THETA_ENROLL_PLACEMENT = 0.1

#: FR-2 screening limits and FR-8/FR-1 constants: design decisions, not dev-derived.
SCREENING = {
    "enroll_min_duration_s": 3.0,
    "enroll_max_duration_s": 30.0,
    "consent_min_duration_s": 5.0,
    "consent_max_duration_s": 60.0,
    "clipping_level": 0.999,
    "max_clipping_fraction": 0.005,
    "min_snr_db": 15.0,
    "min_voiced_ratio": 0.4,
    "voiced_nac_threshold": 0.5,
    "voiced_energy_margin_db": 6.0,
}
UNIT_DURATION_MS = 180
CONSENT_GRACE_DAYS = 30

#: How many non-self takes each dev speaker contributes as "random other".
RANDOM_OTHER_PROBES = 12
RANDOM_OTHER_CONSENTS = 2


def _round(value: float) -> float:
    return round(float(value), 6)


# --------------------------------------------------------------------------- #
# Dev split
# --------------------------------------------------------------------------- #


def dev_bases() -> list[dict[str, Any]]:
    return list(corpus.speakers(split="dev", role="base"))


def dev_impostors_of(speaker_id: str) -> list[dict[str, Any]]:
    return [s for s in corpus.speakers(split="dev", role="impostor") if s["base_id"] == speaker_id]


def enrollment_roles(speaker_id: str) -> list[str]:
    return corpus.roles(speaker_id, "enroll")


def clean_roles(speaker_id: str) -> list[str]:
    return [
        r for r in corpus.roles(speaker_id) if corpus.utterance(r)["variant"] == "clean"
    ]


# --------------------------------------------------------------------------- #
# Feature normalization constants
# --------------------------------------------------------------------------- #


def compute_feature_norms() -> list[dict[str, float]]:
    """Robust within-class normalization from dev statistics (FR-4)."""
    bases = dev_bases()
    per_enroll = [
        np.stack([corpus.raw_features(r) for r in enrollment_roles(s["id"])]) for s in bases
    ]
    per_clean = [
        np.stack([corpus.raw_features(r) for r in clean_roles(s["id"])]) for s in bases
    ]
    pop_mean = np.concatenate(per_enroll, axis=0).mean(axis=0)
    speaker_means = np.stack([block.mean(axis=0) for block in per_enroll])

    per_speaker_sd = np.stack([block.std(axis=0, ddof=1) for block in per_clean])
    pooled_sd = np.sqrt(np.mean(per_speaker_sd**2, axis=0))
    robust_sd = np.sqrt(0.5 * (per_speaker_sd.max(axis=0) ** 2 + pooled_sd**2))

    between_var = speaker_means.var(axis=0, ddof=1)
    f_ratio = between_var / np.maximum(robust_sd**2, 1e-18)
    damping = np.sqrt(np.clip(f_ratio - 1.0, *F_DAMPING_RANGE))
    scale = robust_sd / damping
    return [
        {"mean": _round(m), "scale": _round(s)} for m, s in zip(pop_mean, scale, strict=True)
    ]


def _calibration_with(norms: list[dict[str, float]], theta_verify: float, theta_enroll: float):
    return Calibration.model_validate(
        {
            "embedder_id": EMBEDDER_ID,
            "theta_verify": theta_verify,
            "theta_enroll": theta_enroll,
            "score_scale": SCORE_SCALE,
            "feature_norms": norms,
            "screening": SCREENING,
            "unit_duration_ms": UNIT_DURATION_MS,
            "consent_grace_days": CONSENT_GRACE_DAYS,
            "provenance": "(pending)",
        }
    )


# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ThresholdReport:
    theta: float
    lower: float
    upper: float
    n_impostor: int
    n_genuine: int

    @property
    def margin(self) -> float:
        return self.upper - self.lower


def compute_theta_verify(calibration: Calibration) -> ThresholdReport:
    """Asymmetric placement inside the dev impostor / clean-genuine gap."""
    rng = np.random.default_rng(SEED)
    bases = dev_bases()
    centroids = {
        s["id"]: corpus.centroid_of(enrollment_roles(s["id"]), calibration) for s in bases
    }

    def score(centroid, role):
        return corpus.similarity(centroid, corpus.embedding(role, calibration), calibration)

    genuine: list[float] = []
    impostor: list[float] = []
    for base in bases:
        centroid = centroids[base["id"]]
        genuine.append(score(centroid, f"{base['id']}/consent/0"))
        for impostor_speaker in dev_impostors_of(base["id"]):
            for role in corpus.roles(impostor_speaker["id"]):
                impostor.append(score(centroid, role))
        others = [s["id"] for s in bases if s["id"] != base["id"]]
        other_probes = [r for other in others for r in corpus.roles(other, "probe")]
        other_consents = [r for other in others for r in corpus.roles(other, "consent")]
        for role in rng.choice(other_probes, size=RANDOM_OTHER_PROBES, replace=False):
            impostor.append(score(centroid, str(role)))
        for role in rng.choice(other_consents, size=RANDOM_OTHER_CONSENTS, replace=False):
            impostor.append(score(centroid, str(role)))

    lower = max(impostor)
    upper = min(genuine)
    return ThresholdReport(
        theta=_round(lower + THETA_VERIFY_PLACEMENT * (upper - lower)),
        lower=lower,
        upper=upper,
        n_impostor=len(impostor),
        n_genuine=len(genuine),
    )


def _loo_min(role_list: list[str], calibration: Calibration) -> float:
    embeddings = [corpus.embedding(role, calibration) for role in role_list]
    return min(leave_one_out_scores(embeddings, score_scale=calibration.score_scale))


def dev_mixed_sets() -> list[list[str]]:
    """Mixed enrollment sets mirroring the M6 fixture composition.

    D01-D06 take their single-axis sibling's first probe as the foreign clip
    (the hardest impostor class for the coherence check); D07-D12 take the
    enrollment of an opposite-sex dev speaker. Opposite sex is deliberate: the
    unrelated-foreign rows measure the response to a *clearly* foreign voice,
    and the residual risk that two same-sex strangers genuinely sound alike is
    absorbed by theta_enroll's pure-side placement (and stated in REVIEW.md).
    """
    bases = dev_bases()
    sets: list[list[str]] = []
    for index, base in enumerate(bases):
        own = enrollment_roles(base["id"])[:2]
        singles = [
            s for s in dev_impostors_of(base["id"]) if s["relation"] == "single_axis"
        ]
        if singles:
            foreign = corpus.roles(singles[0]["id"], "probe")[0]
        else:
            opposite = [s for s in bases if s["sex"] != base["sex"]]
            foreign = enrollment_roles(opposite[index % len(opposite)]["id"])[0]
        sets.append([*own, foreign])
    return sets


def compute_theta_enroll(calibration: Calibration) -> ThresholdReport:
    """Pure-side placement inside the dev pure / mixed leave-one-out gap (FR-3)."""
    bases = dev_bases()
    pure = [_loo_min(enrollment_roles(s["id"]), calibration) for s in bases]
    mixed = [_loo_min(role_list, calibration) for role_list in dev_mixed_sets()]
    lower = max(mixed)
    upper = min(pure)
    return ThresholdReport(
        theta=_round(upper - THETA_ENROLL_PLACEMENT * (upper - lower)),
        lower=lower,
        upper=upper,
        n_impostor=len(mixed),
        n_genuine=len(pure),
    )


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_calibration() -> tuple[dict[str, Any], ThresholdReport, ThresholdReport]:
    corpus.ensure_corpus()
    corpus.warm_features(
        role for role in corpus.all_roles() if corpus.utterance(role)["split"] == "dev"
    )
    norms = compute_feature_norms()
    provisional = _calibration_with(norms, 0.0, 0.0)
    verify = compute_theta_verify(provisional)
    enroll = compute_theta_enroll(provisional)
    payload = {
        "embedder_id": EMBEDDER_ID,
        "theta_verify": verify.theta,
        "theta_enroll": enroll.theta,
        "score_scale": SCORE_SCALE,
        "feature_norms": norms,
        "screening": SCREENING,
        "unit_duration_ms": UNIT_DURATION_MS,
        "consent_grace_days": CONSENT_GRACE_DAYS,
        "provenance": (
            f"evals/fixtures/calibrate.py, corpus seed {SEED}, dev split = the 12 dev speakers "
            f"of evals/fixtures/labels.json (D01-D12, disjoint from every eval speaker) plus "
            f"their siblings. Scoring: s = 1 - d^2/{SCORE_SCALE:.0f} (REVIEW.md deviation 3). "
            f"feature_norms: mean = dev enrollment population mean; scale = robust within sd "
            f"(sqrt((max_speaker^2+pooled^2)/2) over dev clean takes) / "
            f"sqrt(clip(F-1, {F_DAMPING_RANGE[0]}, {F_DAMPING_RANGE[1]})). "
            f"theta_verify = dev impostor max {verify.lower:.6f} (over {verify.n_impostor} "
            f"comparisons) + {THETA_VERIFY_PLACEMENT} * gap to dev clean genuine consent min "
            f"{verify.upper:.6f} (over {verify.n_genuine}); margin {verify.margin:.6f}. "
            f"theta_enroll = dev pure-set LOO min {enroll.upper:.6f} - {THETA_ENROLL_PLACEMENT} "
            f"* gap to dev mixed-set LOO max {enroll.lower:.6f} (over {enroll.n_impostor} mixed "
            f"sets); margin {enroll.margin:.6f}."
        ),
    }
    return payload, verify, enroll


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute the committed calibration constants.")
    parser.add_argument("--write", action="store_true", help="rewrite data/calibration.json")
    args = parser.parse_args(argv)

    payload, verify, enroll = build_calibration()
    print(
        f"theta_verify = {payload['theta_verify']:.6f}  "
        f"(dev impostor max {verify.lower:.4f} over {verify.n_impostor}, "
        f"clean genuine min {verify.upper:.4f} over {verify.n_genuine}, "
        f"margin {verify.margin:.4f})"
    )
    print(
        f"theta_enroll = {payload['theta_enroll']:.6f}  "
        f"(mixed max {enroll.lower:.4f}, pure min {enroll.upper:.4f}, "
        f"margin {enroll.margin:.4f})"
    )
    if verify.margin < MIN_MARGIN:
        raise SystemExit(f"theta_verify margin {verify.margin:.4f} < {MIN_MARGIN}")
    if enroll.margin < MIN_MARGIN:
        raise SystemExit(f"theta_enroll margin {enroll.margin:.4f} < {MIN_MARGIN}")
    if args.write:
        CALIBRATION_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
