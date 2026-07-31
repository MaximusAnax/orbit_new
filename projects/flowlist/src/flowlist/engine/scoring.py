"""Pairwise transition scoring and order scoring (FR-6, FR-7, FR-10).

Four weighted components (plus an off-by-default fifth) decide whether two
tracks mix.  Each formula is the one written down in SCOPE.md D2-D5:

* **key** -- Camelot relation table (D2), implemented in :mod:`flowlist.engine.keys`.
* **bpm** -- log-tempo distance with half/double-time folding (D3).
* **energy** -- clamped linear penalty with directional arc profiles (D4).
* **loudness** -- dB difference with a 2 dB dead zone (D5).
* **danceability** -- the same clamped linear penalty as neutral energy,
  default weight 0 (see :func:`danceability_component`).

Missing inputs never crash and never impute: the component contributes the
neutral 0.5 and the transition carries a ``missing_*`` flag (D10).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

from flowlist.engine.keys import camelot, key_score
from flowlist.engine.models import (
    CLIFF_THRESHOLD,
    COMPONENT_NAMES,
    MAX_PLAYLIST_SIZE,
    NEUTRAL_COMPONENT_SCORE,
    SEAMLESS_THRESHOLD,
    ArcProfile,
    FeatureSnapshot,
    FlowReport,
    KeyRelation,
    Transition,
    TransitionWeights,
    round_score,
)
from flowlist.errors import PlaylistTooLargeError

#: Any feature carrier: :class:`FeatureSnapshot` and its subclasses
#: (:class:`AudioFeatures`, :class:`ResolvedFeatures`).
Features = FeatureSnapshot

#: Energy delta that wipes the energy component out entirely (D4).
ENERGY_SPAN = 0.5
#: Multiplier applied to the "wrong direction" energy delta under an arc
#: profile (D4).
PROFILE_PENALTY = 1.5
#: Multiplier applied when octave folding was used (D3): half-time blends work
#: but change the feel.
FOLD_PENALTY = 0.9
#: Widest tempo deviation that still scores above zero, in percent (D3).
BPM_ZERO_PCT = 12.0


class BpmOutcome(NamedTuple):
    """Result of :func:`bpm_component`."""

    score: float | None
    #: Folded ratio actually used (``bpm_to / bpm_from`` possibly x2 or /2).
    ratio: float | None
    #: Signed percentage change of the folded ratio, for display.
    delta_pct: float | None
    #: Whether octave folding changed the ratio (sets the ``half_time`` flag).
    folded: bool


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #


def fold_ratio(ratio: float) -> tuple[float, bool]:
    """Pick the octave-equivalent ratio closest to 1 (D3).

    Candidates are ``r``, ``2r`` and ``r/2``; the one minimising
    ``|log2 rho|`` wins.  Ties prefer the unfolded ratio, and the comparison is
    made on 12-decimal-rounded distances so the choice is stable (D6).
    """
    if ratio <= 0:
        raise ValueError("tempo ratio must be positive")
    best_ratio = ratio
    best_distance = round_score(abs(math.log2(ratio)))
    for candidate in (2.0 * ratio, ratio / 2.0):
        distance = round_score(abs(math.log2(candidate)))
        if distance < best_distance:
            best_ratio, best_distance = candidate, distance
    return best_ratio, best_ratio != ratio


def bpm_deviation_score(pct: float) -> float:
    """Piecewise beatmatchability of a percentage tempo deviation (D3).

    ``<=2%`` is inaudible, the +-6% CDJ range degrades linearly to 0.5, and
    nothing beyond 12% counts as a beatmatch.
    """
    if pct <= 2.0:
        return 1.0
    if pct <= 6.0:
        return 1.0 - 0.125 * (pct - 2.0)
    if pct <= BPM_ZERO_PCT:
        return 0.5 - (pct - 6.0) / 12.0
    return 0.0


def bpm_component(bpm_from: float | None, bpm_to: float | None) -> BpmOutcome:
    """Tempo proximity with octave folding (D3)."""
    if bpm_from is None or bpm_to is None or bpm_from <= 0 or bpm_to <= 0:
        return BpmOutcome(None, None, None, False)
    ratio, folded = fold_ratio(bpm_to / bpm_from)
    pct = (max(ratio, 1.0 / ratio) - 1.0) * 100.0
    score = bpm_deviation_score(pct)
    if folded:
        score *= FOLD_PENALTY
    return BpmOutcome(score, ratio, round_score((ratio - 1.0) * 100.0), folded)


def energy_component(
    energy_from: float | None,
    energy_to: float | None,
    profile: ArcProfile = ArcProfile.NEUTRAL,
) -> float | None:
    """Clamped linear energy penalty with directional profiles (D4, FR-10)."""
    if energy_from is None or energy_to is None:
        return None
    delta = energy_to - energy_from
    penalty = abs(delta)
    if profile is ArcProfile.BUILD and delta < 0:
        penalty *= PROFILE_PENALTY
    elif profile is ArcProfile.COOL and delta > 0:
        penalty *= PROFILE_PENALTY
    return max(0.0, 1.0 - penalty / ENERGY_SPAN)


def loudness_component(loud_from: float | None, loud_to: float | None) -> float | None:
    """dB matching with a 2 dB dead zone (D5)."""
    if loud_from is None or loud_to is None:
        return None
    delta = abs(loud_to - loud_from)
    if delta <= 2.0:
        return 1.0
    if delta <= 10.0:
        return 1.0 - (delta - 2.0) / 8.0
    return 0.0


def danceability_component(dance_from: float | None, dance_to: float | None) -> float | None:
    """Groove continuity, default weight 0 (FR-6).

    SCOPE.md declares the component but leaves its formula open; it uses the
    same clamped linear penalty as neutral energy (D4) so the two continuity
    components are calibrated identically.
    """
    if dance_from is None or dance_to is None:
        return None
    return max(0.0, 1.0 - abs(dance_to - dance_from) / ENERGY_SPAN)


# --------------------------------------------------------------------------- #
# Combination
# --------------------------------------------------------------------------- #


def _raw_components(
    a: Features, b: Features, profile: ArcProfile
) -> tuple[dict[str, float | None], BpmOutcome, KeyRelation, float | None]:
    """Every component's raw score (``None`` == inputs missing)."""
    key_result = key_score(a.key_pc, a.mode, b.key_pc, b.mode)
    bpm_result = bpm_component(a.bpm, b.bpm)
    raw: dict[str, float | None] = {
        "key": key_result.score,
        "bpm": bpm_result.score,
        "energy": energy_component(a.energy, b.energy, profile),
        "loudness": loudness_component(a.loudness_db, b.loudness_db),
        "danceability": danceability_component(a.danceability, b.danceability),
    }
    return raw, bpm_result, key_result.relation, key_result.score


def _combine(
    raw: dict[str, float | None], weights: TransitionWeights
) -> tuple[float, dict[str, float | None], list[str]]:
    """Weighted sum over normalized weights.

    A component with weight 0 is reported as ``None`` and contributes nothing:
    it cannot move the score, so flagging its missing inputs would be noise
    (this is why a default transition carries ``"danceability": null`` and no
    flag, DATA_MODEL 2.6).  A weighted component with missing inputs takes the
    D10 neutral and flags the transition.
    """
    weight_map = weights.as_dict()
    total = 0.0
    components: dict[str, float | None] = {}
    flags: list[str] = []
    for name in COMPONENT_NAMES:
        weight = weight_map[name]
        if weight == 0.0:
            components[name] = None
            continue
        score = raw[name]
        if score is None:
            score = NEUTRAL_COMPONENT_SCORE
            flags.append(f"missing_{name}")
        components[name] = round_score(score)
        total += weight * score
    # Rounded normalized weights can sum to 1 +- 5e-12; clamp so the FR-6
    # guarantee score in [0, 1] holds exactly.
    return min(1.0, max(0.0, round_score(total))), components, flags


def pair_score(a: Features, b: Features, weights: TransitionWeights, profile: ArcProfile) -> float:
    """Total score only — the hot path used by :func:`build_matrix`.

    Shares :func:`_raw_components` and :func:`_combine` with
    :func:`transition_score`, so the matrix can never disagree with the stored
    breakdown.  ``weights`` must already be normalized.
    """
    raw, _, _, _ = _raw_components(a, b, profile)
    total, _, _ = _combine(raw, weights)
    return total


def transition_score(
    a: Features,
    b: Features,
    weights: TransitionWeights | None = None,
    profile: ArcProfile = ArcProfile.NEUTRAL,
    *,
    from_id: str | None = None,
    to_id: str | None = None,
    anchored: bool = False,
) -> Transition:
    """Full breakdown for one directed seam (FR-6)."""
    normalized = (weights or TransitionWeights()).normalized()
    raw, bpm_result, relation, _ = _raw_components(a, b, profile)
    total, components, flags = _combine(raw, normalized)

    if bpm_result.folded:
        flags.append("half_time")
    if total < CLIFF_THRESHOLD:
        flags.append("cliff")
    if anchored:
        flags.append("anchored")

    from flowlist.engine.keys import camelot  # local import: keeps keys import cycle-free

    camelot_from = camelot(a.key_pc, a.mode) if a.has_key else None
    camelot_to = camelot(b.key_pc, b.mode) if b.has_key else None

    energy_delta = (
        round_score(b.energy - a.energy) if a.energy is not None and b.energy is not None else None
    )
    loudness_delta = (
        round_score(b.loudness_db - a.loudness_db)
        if a.loudness_db is not None and b.loudness_db is not None
        else None
    )

    return Transition(
        score=total,
        components=components,
        weights=normalized,
        key_relation=relation,
        camelot_from=camelot_from,
        camelot_to=camelot_to,
        bpm_from=a.bpm,
        bpm_to=b.bpm,
        bpm_delta_pct=bpm_result.delta_pct,
        bpm_folded=bpm_result.folded,
        energy_delta=energy_delta,
        loudness_delta_db=loudness_delta,
        features_from=_as_snapshot(a),
        features_to=_as_snapshot(b),
        flags=flags,
        from_id=from_id,
        to_id=to_id,
    )


def _as_snapshot(features: Features) -> FeatureSnapshot:
    if isinstance(features, FeatureSnapshot) and type(features) is FeatureSnapshot:
        return features
    return FeatureSnapshot(
        bpm=features.bpm,
        key_pc=features.key_pc,
        mode=features.mode,
        energy=features.energy,
        danceability=features.danceability,
        loudness_db=features.loudness_db,
    )


# --------------------------------------------------------------------------- #
# Matrices and order scoring
# --------------------------------------------------------------------------- #


def build_matrix(
    features: Sequence[Features],
    weights: TransitionWeights | None = None,
    profile: ArcProfile = ArcProfile.NEUTRAL,
) -> list[list[float]]:
    """Full O(n^2) directed score matrix (FR-8).

    The diagonal is 0.0 and is never read by the optimizer (a node cannot
    follow itself).
    """
    n = len(features)
    if n > MAX_PLAYLIST_SIZE:
        raise PlaylistTooLargeError(
            f"playlist has {n} entries; the optimizer caps at {MAX_PLAYLIST_SIZE}",
            entries=n,
            cap=MAX_PLAYLIST_SIZE,
        )
    normalized = (weights or TransitionWeights()).normalized()
    matrix = [[0.0] * n for _ in range(n)]
    for i, a in enumerate(features):
        row = matrix[i]
        for j, b in enumerate(features):
            if i != j:
                row[j] = pair_score(a, b, normalized, profile)
    return matrix


def order_total(order: Sequence[int], matrix: Sequence[Sequence[float]]) -> float:
    """Sum of the consecutive edges of ``order`` in ``matrix``."""
    total = 0.0
    for i in range(len(order) - 1):
        total += matrix[order[i]][order[i + 1]]
    return round_score(total)


def score_order(
    order: Sequence[int],
    features: Sequence[Features],
    weights: TransitionWeights | None = None,
    profile: ArcProfile = ArcProfile.NEUTRAL,
    *,
    ids: Sequence[str] | None = None,
    anchored: Sequence[int] = (),
) -> FlowReport:
    """Per-transition breakdowns plus aggregates for one ordering (FR-7).

    ``order`` holds indices into ``features``; ``ids`` maps those indices to
    entry ids (defaulting to the stringified index so the engine is usable
    without a store).
    """
    if len(set(order)) != len(order):
        raise ValueError("order contains duplicate indices")
    if any(i < 0 or i >= len(features) for i in order):
        raise ValueError("order references an index outside the feature list")

    normalized = (weights or TransitionWeights()).normalized()
    labels = [str(i) for i in range(len(features))] if ids is None else list(ids)
    anchored_set = set(anchored)

    transitions: list[Transition] = []
    for position in range(len(order) - 1):
        i, j = order[position], order[position + 1]
        transitions.append(
            transition_score(
                features[i],
                features[j],
                normalized,
                profile,
                from_id=labels[i],
                to_id=labels[j],
                anchored=i in anchored_set or j in anchored_set,
            )
        )

    scores = [t.score for t in transitions]
    total = round_score(sum(scores))
    return FlowReport(
        order=[labels[i] for i in order],
        transitions=transitions,
        total=total,
        mean=round_score(total / len(scores)) if scores else 0.0,
        min_score=min(scores) if scores else 0.0,
        seamless=sum(1 for s in scores if s >= SEAMLESS_THRESHOLD),
        cliffs=sum(1 for s in scores if s < CLIFF_THRESHOLD),
    )


__all__ = [
    "BPM_ZERO_PCT",
    "ENERGY_SPAN",
    "FOLD_PENALTY",
    "PROFILE_PENALTY",
    "BpmOutcome",
    "bpm_component",
    "bpm_deviation_score",
    "build_matrix",
    "danceability_component",
    "energy_component",
    "fold_ratio",
    "loudness_component",
    "order_total",
    "pair_score",
    "score_order",
    "transition_score",
]
