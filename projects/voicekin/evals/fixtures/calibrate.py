"""Dev-split threshold calibration -> ``data/calibration.json`` (EVALS.md).

Calibration touches **only** the 12 dev speakers; the 24 eval speakers are never
read here, exactly as NIST SRE practice requires of a threshold set that is later
measured on a disjoint population.

What this script derives:

* **16 feature normalization constants** from dev *enrollment* statistics.
  ``scale`` is the pooled within-speaker standard deviation of a dimension,
  damped by that dimension's F-ratio so a dimension whose between-speaker
  variance does not exceed its within-speaker variance cannot dominate the
  cosine; ``mean`` is offset below the population mean by
  :data:`MEAN_OFFSET_SCALES` scales, which is what makes cosine similarity rank
  by within-class Mahalanobis distance rather than by direction alone.
* **theta_verify** — midpoint between the maximum dev *impostor* score and the
  minimum dev *clean genuine* consent score, asserting a margin >= 0.05 cosine.
  Clean-only on the genuine side is deliberate: letting harsh or channel
  mismatched takes drag the threshold down would trade the catastrophic error
  (a consent forgery) for the benign one (SCOPE decision 3).
* **theta_enroll** — midpoint between the maximum dev *mixed-set* leave-one-out
  score and the minimum dev *pure-set* leave-one-out score, same margin
  assertion.

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
"""Both thresholds must sit in a >= 0.05 cosine gap on the dev split."""

MEAN_OFFSET_SCALES = 8.0
"""How far below the population mean each dimension's origin is placed.

Cosine similarity compares *directions*, so with an origin at the population
mean two speakers who differ only in magnitude score 1.0. Shifting the origin
well outside the cloud turns the angle between two normalized vectors into a
monotone function of their normalized Euclidean distance, which is the quantity
that actually separates speakers here."""

F_RATIO_DAMPING = (0.05, 1.0)
"""``scale = within_sd / sqrt(clip(F - 1, *F_RATIO_DAMPING))``: a dimension whose
between-speaker variance barely exceeds its within-speaker variance (F ~ 1) gets
its scale inflated ~4.5x, so it contributes little; a genuinely discriminative
dimension (F >> 2) is left at its within-speaker sd."""

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
    return [s for s in corpus.speakers(split="dev", role="base")]


def dev_impostors_of(speaker_id: str) -> list[dict[str, Any]]:
    return [s for s in corpus.speakers(split="dev", role="impostor") if s["base_id"] == speaker_id]


def enrollment_roles(speaker_id: str) -> list[str]:
    return corpus.roles(speaker_id, "enroll")


# --------------------------------------------------------------------------- #
# Feature normalization constants
# --------------------------------------------------------------------------- #


def compute_feature_norms() -> list[dict[str, float]]:
    """Within-class variance normalization from dev enrollment takes (FR-4)."""
    per_speaker = [
        np.stack([corpus.raw_features(role) for role in enrollment_roles(s["id"])])
        for s in dev_bases()
    ]
    stacked = np.concatenate(per_speaker, axis=0)
    speaker_means = np.stack([block.mean(axis=0) for block in per_speaker])

    # Pooled within-speaker variance (each speaker contributes n-1 dof).
    within_ss = np.zeros(EMBEDDING_DIM, dtype=np.float64)
    dof = 0
    for block in per_speaker:
        within_ss += ((block - block.mean(axis=0)) ** 2).sum(axis=0)
        dof += block.shape[0] - 1
    within_var = within_ss / max(dof, 1)
    between_var = speaker_means.var(axis=0, ddof=1)

    f_ratio = between_var / np.maximum(within_var, 1e-18)
    damping = np.sqrt(np.clip(f_ratio - 1.0, *F_RATIO_DAMPING))
    scale = np.sqrt(np.maximum(within_var, 1e-18)) / damping
    mean = stacked.mean(axis=0) - MEAN_OFFSET_SCALES * scale
    return [{"mean": _round(m), "scale": _round(s)} for m, s in zip(mean, scale, strict=True)]


def _calibration_with(norms: list[dict[str, float]], theta_verify: float, theta_enroll: float):
    return Calibration.model_validate(
        {
            "embedder_id": EMBEDDER_ID,
            "theta_verify": theta_verify,
            "theta_enroll": theta_enroll,
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
    """Midpoint of the dev impostor / dev clean-genuine gap (SCOPE decision 3)."""
    rng = np.random.default_rng(SEED)
    bases = dev_bases()
    centroids = {
        s["id"]: corpus.centroid_of(enrollment_roles(s["id"]), calibration) for s in bases
    }
    genuine: list[float] = []
    impostor: list[float] = []

    for base in bases:
        centroid = centroids[base["id"]]
        genuine.append(corpus.cosine(centroid, corpus.embedding(f"{base['id']}/consent/0", calibration)))

        for impostor_speaker in dev_impostors_of(base["id"]):
            for role in corpus.roles(impostor_speaker["id"]):
                impostor.append(corpus.cosine(centroid, corpus.embedding(role, calibration)))

        others = [s["id"] for s in bases if s["id"] != base["id"]]
        other_probes = [r for other in others for r in corpus.roles(other, "probe")]
        other_consents = [r for other in others for r in corpus.roles(other, "consent")]
        for role in rng.choice(other_probes, size=RANDOM_OTHER_PROBES, replace=False):
            impostor.append(corpus.cosine(centroid, corpus.embedding(str(role), calibration)))
        for role in rng.choice(other_consents, size=RANDOM_OTHER_CONSENTS, replace=False):
            impostor.append(corpus.cosine(centroid, corpus.embedding(str(role), calibration)))

    lower = max(impostor)
    upper = min(genuine)
    return ThresholdReport(
        theta=_round(0.5 * (lower + upper)),
        lower=lower,
        upper=upper,
        n_impostor=len(impostor),
        n_genuine=len(genuine),
    )


def _loo_min(role_list: list[str], calibration: Calibration) -> float:
    embeddings = [corpus.embedding(role, calibration) for role in role_list]
    return min(leave_one_out_scores(embeddings))


def compute_theta_enroll(calibration: Calibration) -> ThresholdReport:
    """Midpoint of the dev mixed-set / pure-set leave-one-out gap (FR-3)."""
    bases = dev_bases()
    pure = [_loo_min(enrollment_roles(s["id"]), calibration) for s in bases]

    mixed: list[float] = []
    for position, base in enumerate(bases):
        own = enrollment_roles(base["id"])[:2]
        impostors = dev_impostors_of(base["id"])
        if impostors:
            foreign = corpus.roles(impostors[position % len(impostors)]["id"], "probe")[0]
        else:
            other = bases[(position + 1) % len(bases)]
            foreign = enrollment_roles(other["id"])[0]
        mixed.append(_loo_min([*own, foreign], calibration))

    lower = max(mixed)
    upper = min(pure)
    return ThresholdReport(
        theta=_round(0.5 * (lower + upper)),
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
        role
        for role in corpus.all_roles()
        if corpus.utterance(role)["split"] == "dev"
    )
    norms = compute_feature_norms()
    provisional = _calibration_with(norms, 0.0, 0.0)
    verify = compute_theta_verify(provisional)
    enroll = compute_theta_enroll(provisional)
    payload = {
        "embedder_id": EMBEDDER_ID,
        "theta_verify": verify.theta,
        "theta_enroll": enroll.theta,
        "feature_norms": norms,
        "screening": SCREENING,
        "unit_duration_ms": UNIT_DURATION_MS,
        "consent_grace_days": CONSENT_GRACE_DAYS,
        "provenance": (
            f"evals/fixtures/calibrate.py, corpus seed {SEED}, dev split = the 12 dev speakers "
            f"of evals/fixtures/labels.json (D01-D12, disjoint from every eval speaker) plus "
            f"their siblings. feature_norms: scale = pooled within-speaker sd / "
            f"sqrt(clip(F-1, {F_RATIO_DAMPING[0]}, {F_RATIO_DAMPING[1]})) over the 36 dev "
            f"enrollment takes; mean = population mean - {MEAN_OFFSET_SCALES} * scale. "
            f"theta_verify = midpoint({verify.lower:.6f} max dev impostor over "
            f"{verify.n_impostor} comparisons, {verify.upper:.6f} min dev clean genuine consent "
            f"over {verify.n_genuine}); margin {verify.margin:.6f}. theta_enroll = "
            f"midpoint({enroll.lower:.6f} max dev mixed-set LOO, {enroll.upper:.6f} min dev "
            f"pure-set LOO over {enroll.n_genuine} sets); margin {enroll.margin:.6f}."
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
