"""FR-6, FR-7, FR-10: component formulas, weight algebra, flow reports, arcs."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pytest
from flowlist.engine.models import (
    CLIFF_THRESHOLD,
    COMPONENT_NAMES,
    SEAMLESS_THRESHOLD,
    ArcProfile,
    FeatureSnapshot,
    KeyRelation,
    TransitionWeights,
)
from flowlist.engine.optimizer import reorder
from flowlist.engine.scoring import (
    FOLD_PENALTY,
    bpm_component,
    bpm_deviation_score,
    build_matrix,
    danceability_component,
    energy_component,
    fold_ratio,
    loudness_component,
    order_total,
    pair_score,
    score_order,
    transition_score,
)
from flowlist.errors import InvalidWeightsError, PlaylistTooLargeError
from flowlist_testkit import make_features


def feat(**kwargs: float | int | None) -> FeatureSnapshot:
    base: dict[str, float | int | None] = {
        "bpm": 124.0,
        "key_pc": 9,
        "mode": 0,
        "energy": 0.7,
        "danceability": 0.7,
        "loudness_db": -7.0,
    }
    base.update(kwargs)
    return FeatureSnapshot(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# D3 -- BPM
# --------------------------------------------------------------------------- #


def test_fr6_bpm_piecewise_breakpoints() -> None:
    """D3's piecewise curve, checked at every documented breakpoint."""
    assert bpm_deviation_score(0.0) == 1.0
    assert bpm_deviation_score(2.0) == 1.0
    assert bpm_deviation_score(6.0) == pytest.approx(0.5)
    assert bpm_deviation_score(12.0) == pytest.approx(0.0, abs=1e-12)
    assert bpm_deviation_score(12.5) == 0.0
    assert bpm_deviation_score(4.0) == pytest.approx(1.0 - 0.125 * 2.0)
    assert bpm_deviation_score(9.0) == pytest.approx(0.5 - 3.0 / 12.0)


def test_fr6_bpm_is_non_increasing_over_the_sweep() -> None:
    """M3's monotonicity sweep: 0 -> 15% in 0.5% steps."""
    previous = 1.0
    pct = 0.0
    while pct <= 15.0 + 1e-9:
        score = bpm_deviation_score(pct)
        assert score <= previous + 1e-12, pct
        if pct <= 2.0:
            assert score == 1.0
        if pct >= 12.0:
            assert score == pytest.approx(0.0, abs=1e-12)
        previous = score
        pct += 0.5


def test_fr6_bpm_ratio_is_symmetric_in_magnitude() -> None:
    """Tempo perception is ratio-based: 124->130 and 130->124 score alike."""
    up = bpm_component(124.0, 130.0)
    down = bpm_component(130.0, 124.0)
    assert up.score == pytest.approx(down.score)
    assert up.delta_pct is not None and down.delta_pct is not None
    assert up.delta_pct > 0 > down.delta_pct


def test_fr6_octave_folding_half_time() -> None:
    """D3: 86 -> 172 BPM is a legitimate half-time blend, penalised x0.9."""
    same = bpm_component(86.0, 86.0)
    folded = bpm_component(86.0, 172.0)
    assert same.score == 1.0
    assert folded.folded is True
    assert folded.score == pytest.approx(same.score * FOLD_PENALTY)
    # ... and it beats a genuinely unmatchable tempo gap.
    assert folded.score > (bpm_component(86.0, 150.0).score or 0.0)


def test_fr6_folding_picks_the_ratio_closest_to_one() -> None:
    assert fold_ratio(2.0) == (1.0, True)
    assert fold_ratio(0.5) == (1.0, True)
    assert fold_ratio(1.02) == (1.02, False)
    # Ties prefer the unfolded ratio so near-ties stay stable (D6).
    tie = 1.0 / math.sqrt(2.0)
    ratio, folded = fold_ratio(tie)
    assert folded is False
    assert ratio == tie


def test_fr6_half_time_flag_is_set() -> None:
    transition = transition_score(feat(bpm=86.0), feat(bpm=172.0))
    assert transition.bpm_folded is True
    assert "half_time" in transition.flags


def test_fr6_bpm_missing_is_neutral_and_flagged() -> None:
    transition = transition_score(feat(bpm=None), feat())
    assert transition.components["bpm"] == 0.5
    assert "missing_bpm" in transition.flags
    assert 0.0 <= transition.score <= 1.0


# --------------------------------------------------------------------------- #
# D4 -- energy, and FR-10 arc profiles
# --------------------------------------------------------------------------- #


def test_fr6_energy_clamped_linear_penalty() -> None:
    assert energy_component(0.5, 0.5) == 1.0
    assert energy_component(0.5, 0.6) == pytest.approx(0.8)
    assert energy_component(0.5, 0.75) == pytest.approx(0.5)
    assert energy_component(0.2, 0.7) == pytest.approx(0.0)
    assert energy_component(0.1, 0.9) == 0.0  # clamped, never negative


def test_fr6_energy_non_increasing_in_absolute_delta() -> None:
    previous = 1.0
    for step in range(0, 51):
        delta = step / 100.0
        score = energy_component(0.5, min(1.0, 0.5 + delta))
        assert score is not None and score <= previous + 1e-12
        previous = score


def test_fr10_neutral_profile_is_symmetric() -> None:
    assert energy_component(0.5, 0.7, ArcProfile.NEUTRAL) == pytest.approx(
        energy_component(0.5, 0.3, ArcProfile.NEUTRAL)
    )


def test_fr10_build_penalises_drops_and_cool_penalises_rises() -> None:
    up = energy_component(0.5, 0.7, ArcProfile.BUILD)
    down = energy_component(0.5, 0.3, ArcProfile.BUILD)
    assert up is not None and down is not None and down < up
    assert down == pytest.approx(1.0 - 1.5 * 0.2 / 0.5)

    cool_up = energy_component(0.5, 0.7, ArcProfile.COOL)
    cool_down = energy_component(0.5, 0.3, ArcProfile.COOL)
    assert cool_up is not None and cool_down is not None and cool_up < cool_down
    # cool is build's mirror image.
    assert cool_up == pytest.approx(down)
    assert cool_down == pytest.approx(up)


def test_fr10_profile_only_touches_the_energy_component() -> None:
    a, b = feat(energy=0.4), feat(energy=0.8, key_pc=4, bpm=128.0, loudness_db=-9.0)
    neutral = transition_score(a, b, profile=ArcProfile.NEUTRAL)
    build = transition_score(a, b, profile=ArcProfile.BUILD)
    for name in ("key", "bpm", "loudness"):
        assert neutral.components[name] == build.components[name]
    assert neutral.key_relation is build.key_relation


def test_fr10_profiles_change_the_ordering_on_a_symmetric_arc() -> None:
    """US-5 acceptance, on a fixture built so a no-op profile cannot pass.

    At each step the higher- and lower-energy continuations are constructed to
    score *identically* on key, BPM and loudness, so only the energy component
    can break the tie -- ``build`` must therefore climb where ``neutral`` is
    indifferent.
    """
    # 8A -> 9A -> 10A ... : every hop is an adjacent fifth at the same tempo
    # and loudness; energies alternate high/low around a rising centre.
    features: list[FeatureSnapshot] = []
    for step in range(10):
        centre = 0.30 + 0.05 * step
        for offset in (+0.06, -0.06):
            features.append(
                FeatureSnapshot(
                    bpm=124.0,
                    key_pc=(9 + 7 * step) % 12,
                    mode=0,
                    energy=round(min(0.95, max(0.05, centre + offset)), 3),
                    loudness_db=-7.0,
                )
            )

    def mean_signed_energy_delta(profile: ArcProfile) -> float:
        matrix = build_matrix(features, profile=profile)
        order = reorder(matrix, seed=7).order
        deltas = [
            (features[b].energy or 0.0) - (features[a].energy or 0.0)
            for a, b in itertools.pairwise(order)
        ]
        return sum(deltas) / len(deltas)

    build_delta = mean_signed_energy_delta(ArcProfile.BUILD)
    neutral_delta = mean_signed_energy_delta(ArcProfile.NEUTRAL)
    cool_delta = mean_signed_energy_delta(ArcProfile.COOL)
    assert build_delta > neutral_delta
    assert cool_delta < neutral_delta


# --------------------------------------------------------------------------- #
# D5 -- loudness; danceability
# --------------------------------------------------------------------------- #


def test_fr6_loudness_dead_zone_and_ramp() -> None:
    assert loudness_component(-7.0, -7.0) == 1.0
    assert loudness_component(-7.0, -9.0) == 1.0  # exactly 2 dB, still seamless
    assert loudness_component(-7.0, -11.0) == pytest.approx(1.0 - 2.0 / 8.0)
    assert loudness_component(-4.0, -14.0) == pytest.approx(0.0)
    assert loudness_component(-2.0, -20.0) == 0.0


def test_fr6_loudness_non_increasing() -> None:
    previous = 1.0
    for tenth in range(0, 121):
        score = loudness_component(-30.0, -30.0 + tenth / 10.0)
        assert score is not None and score <= previous + 1e-12
        previous = score


def test_fr6_danceability_mirrors_neutral_energy() -> None:
    for a, b in ((0.5, 0.5), (0.5, 0.6), (0.2, 0.9)):
        assert danceability_component(a, b) == energy_component(a, b, ArcProfile.NEUTRAL)


# --------------------------------------------------------------------------- #
# FR-6 -- weights
# --------------------------------------------------------------------------- #


def test_fr6_default_weights_match_the_spec() -> None:
    weights = TransitionWeights()
    assert weights.as_dict() == {
        "key": 0.35,
        "bpm": 0.35,
        "energy": 0.20,
        "loudness": 0.10,
        "danceability": 0.0,
    }


def test_fr6_weight_normalization() -> None:
    """Scaling every weight by a positive constant is an exact no-op."""
    a, b = feat(), feat(key_pc=4, bpm=129.0, energy=0.55, loudness_db=-10.0)
    base = transition_score(a, b, TransitionWeights())
    for factor in (0.5, 2.0, 10.0, 1000.0):
        scaled = TransitionWeights(
            **{name: value * factor for name, value in TransitionWeights().as_dict().items()}
        )
        other = transition_score(a, b, scaled)
        assert other.score == base.score
        assert other.components == base.components
        assert other.weights.as_dict() == base.weights.as_dict()


def test_fr6_unnormalized_weights_still_score_in_unit_range() -> None:
    a, b = feat(), feat(key_pc=9, mode=0)
    weights = TransitionWeights(key=1.0, bpm=1.0, energy=1.0, loudness=1.0, danceability=1.0)
    transition = transition_score(a, b, weights)
    assert 0.0 <= transition.score <= 1.0
    assert sum(transition.weights.as_dict().values()) == pytest.approx(1.0)


def test_fr6_single_component_weights_isolate_that_component() -> None:
    """M3's weight algebra: {bpm: 1} makes the total equal S_bpm."""
    a, b = feat(), feat(bpm=129.0, key_pc=1, energy=0.2, loudness_db=-20.0)
    weights = TransitionWeights(key=0.0, bpm=1.0, energy=0.0, loudness=0.0, danceability=0.0)
    transition = transition_score(a, b, weights)
    expected = bpm_component(a.bpm, b.bpm).score
    assert expected is not None
    assert transition.score == pytest.approx(expected)
    for name in COMPONENT_NAMES:
        if name != "bpm":
            assert transition.components[name] is None


def test_fr6_zero_weight_component_is_reported_null_without_a_flag() -> None:
    """Matches DATA_MODEL 2.6's example: danceability null, flags empty."""
    transition = transition_score(feat(danceability=None), feat(danceability=None))
    assert transition.components["danceability"] is None
    assert transition.flags == []


def test_fr6_weighted_missing_component_is_neutral_and_flagged() -> None:
    weights = TransitionWeights(key=0.2, bpm=0.2, energy=0.2, loudness=0.2, danceability=0.2)
    transition = transition_score(feat(danceability=None), feat(), weights)
    assert transition.components["danceability"] == 0.5
    assert "missing_danceability" in transition.flags


@pytest.mark.parametrize(
    "payload",
    [
        {"key": -0.1},
        {"key": 0.0, "bpm": 0.0, "energy": 0.0, "loudness": 0.0, "danceability": 0.0},
        {"bpm": "fast"},
        {"tempo": 1.0},
    ],
)
def test_fr6_invalid_weights_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidWeightsError):
        TransitionWeights.parse(payload)  # type: ignore[arg-type]


def test_fr6_parse_accepts_partial_weights() -> None:
    weights = TransitionWeights.parse({"key": 1.0, "bpm": 1.0})
    assert weights.key == 1.0
    assert weights.energy == 0.20  # untouched default
    assert TransitionWeights.parse(None) == TransitionWeights()


def test_fr6_normalization_is_idempotent() -> None:
    once = TransitionWeights(key=2.0, bpm=2.0).normalized()
    assert once.normalized().as_dict() == once.as_dict()
    assert sum(once.as_dict().values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# D12 -- calibration of the seamless/cliff constants
# --------------------------------------------------------------------------- #


def test_fr6_d12_seamless_calibration() -> None:
    """D12: relative key, 3% tempo, 0.10 energy, 3 dB loudness ~= 0.89."""
    a = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.60, loudness_db=-8.0)
    b = FeatureSnapshot(bpm=124.0 * 1.03, key_pc=0, mode=1, energy=0.70, loudness_db=-5.0)
    transition = transition_score(a, b)
    assert transition.key_relation is KeyRelation.RELATIVE
    assert transition.score == pytest.approx(0.886, abs=0.005)
    assert transition.is_seamless


def test_fr6_d12_key_clash_denies_seamless_narrowly() -> None:
    """D12: a clash with everything else perfect scores ~0.69 -- just under."""
    a = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.60, loudness_db=-8.0)
    b = FeatureSnapshot(bpm=124.0, key_pc=8, mode=0, energy=0.60, loudness_db=-8.0)
    transition = transition_score(a, b)
    assert transition.key_relation is KeyRelation.CLASH
    assert transition.score == pytest.approx(0.685, abs=0.002)
    assert not transition.is_seamless
    assert transition.score > SEAMLESS_THRESHOLD - 0.05  # "narrowly"


def test_fr6_score_bounds_and_extremes() -> None:
    perfect = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.6, loudness_db=-8.0)
    assert transition_score(perfect, perfect).score == 1.0
    worst = FeatureSnapshot(bpm=124.0 * 1.3, key_pc=8, mode=0, energy=0.05, loudness_db=-1.0)
    transition = transition_score(perfect, worst)
    assert 0.0 <= transition.score <= 1.0
    assert transition.is_cliff
    assert "cliff" in transition.flags


# --------------------------------------------------------------------------- #
# Missing data (D10)
# --------------------------------------------------------------------------- #


def test_fr6_missing_key_is_neutral_and_flagged() -> None:
    transition = transition_score(feat(key_pc=None, mode=None), feat())
    assert transition.components["key"] == 0.5
    assert transition.key_relation is KeyRelation.UNKNOWN
    assert transition.camelot_from is None
    assert "missing_key" in transition.flags
    assert 0.0 <= transition.score <= 1.0


def test_fr6_all_components_missing_scores_neutral() -> None:
    blank = FeatureSnapshot()
    transition = transition_score(blank, blank)
    assert transition.score == pytest.approx(0.5)
    assert set(transition.flags) >= {
        "missing_key",
        "missing_bpm",
        "missing_energy",
        "missing_loudness",
    }


def test_fr6_transition_snapshots_the_features_it_used() -> None:
    a, b = feat(), feat(bpm=130.0)
    transition = transition_score(a, b)
    assert transition.features_from.bpm == a.bpm
    assert transition.features_to.bpm == b.bpm
    assert transition.energy_delta == pytest.approx(0.0)
    assert transition.loudness_delta_db == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# FR-7 -- flow reports and matrices
# --------------------------------------------------------------------------- #


def test_fr7_flow_report() -> None:
    features = make_features(12, seed=3)
    report = score_order(list(range(12)), features)
    assert len(report.transitions) == 11
    assert report.order == [str(i) for i in range(12)]
    assert report.total == pytest.approx(sum(t.score for t in report.transitions))
    assert report.mean == pytest.approx(report.total / 11)
    assert report.min_score == min(t.score for t in report.transitions)
    assert report.seamless == sum(1 for t in report.transitions if t.score >= SEAMLESS_THRESHOLD)
    assert report.cliffs == sum(1 for t in report.transitions if t.score < CLIFF_THRESHOLD)
    assert report.n_entries == 12


def test_fr7_flow_report_uses_supplied_entry_ids() -> None:
    features = make_features(4, seed=5)
    ids = ["e0", "e1", "e2", "e3"]
    report = score_order([2, 0, 3, 1], features, ids=ids)
    assert report.order == ["e2", "e0", "e3", "e1"]
    assert report.transitions[0].from_id == "e2"
    assert report.transitions[0].to_id == "e0"


def test_fr7_flow_report_degenerate_sizes() -> None:
    features = make_features(1, seed=1)
    report = score_order([0], features)
    assert report.transitions == []
    assert (report.total, report.mean, report.min_score) == (0.0, 0.0, 0.0)
    assert score_order([], []).n_entries == 0


def test_fr7_anchored_flag_marks_pinned_endpoints() -> None:
    features = make_features(5, seed=9)
    report = score_order([0, 1, 2, 3, 4], features, anchored=[0, 4])
    assert "anchored" in report.transitions[0].flags
    assert "anchored" in report.transitions[-1].flags
    assert "anchored" not in report.transitions[2].flags


def test_fr7_rejects_malformed_orders() -> None:
    features = make_features(3, seed=1)
    with pytest.raises(ValueError):
        score_order([0, 0, 1], features)
    with pytest.raises(ValueError):
        score_order([0, 5], features)


def test_fr6_matrix_agrees_with_the_full_breakdown() -> None:
    """The optimizer's matrix and the stored explanation cannot diverge."""
    features = make_features(9, seed=17)
    matrix = build_matrix(features)
    for i in range(9):
        assert matrix[i][i] == 0.0
        for j in range(9):
            if i != j:
                assert matrix[i][j] == transition_score(features[i], features[j]).score


def test_fr6_matrix_is_asymmetric() -> None:
    """Direction matters: the whole reason 2-opt must recompute reversals."""
    features = make_features(20, seed=23)
    matrix = build_matrix(features)
    assert any(matrix[i][j] != matrix[j][i] for i in range(20) for j in range(20) if i != j)


def test_fr7_order_total_matches_score_order() -> None:
    features = make_features(15, seed=31)
    matrix = build_matrix(features)
    order = [7, 3, 11, 0, 14, 2, 9, 1, 5, 13, 4, 8, 12, 6, 10]
    assert order_total(order, matrix) == pytest.approx(score_order(order, features).total)


def test_fr8_matrix_enforces_the_size_cap() -> None:
    features = [FeatureSnapshot(bpm=120.0)] * 501
    with pytest.raises(PlaylistTooLargeError) as excinfo:
        build_matrix(features)
    assert excinfo.value.code == "playlist_too_large"


def test_fr6_pair_score_requires_normalized_weights_to_match() -> None:
    a, b = feat(), feat(bpm=127.0, key_pc=2)
    weights = TransitionWeights(key=7.0, bpm=7.0, energy=4.0, loudness=2.0).normalized()
    assert pair_score(a, b, weights, ArcProfile.NEUTRAL) == transition_score(a, b, weights).score


# --------------------------------------------------------------------------- #
# FR-10 on the committed arc fixture (EVALS.md §4/§7, US-5 acceptance)
# --------------------------------------------------------------------------- #


def _arc_features() -> list[FeatureSnapshot]:
    """Load ``evals/fixtures/playlists/arc_01.json`` through the offline catalog."""
    root = Path(__file__).resolve().parents[1] / "evals" / "fixtures"
    catalog = {
        row["id"]: row
        for row in json.loads((root / "catalog.json").read_text(encoding="utf-8"))["tracks"]
    }
    payload = json.loads((root / "playlists" / "arc_01.json").read_text(encoding="utf-8"))
    return [FeatureSnapshot(**catalog[track_id]["features"]) for track_id in payload["tracks"]]


def _mean_signed_energy_delta(features: list[FeatureSnapshot], profile: ArcProfile) -> float:
    order = reorder(build_matrix(features, profile=profile), seed=7).order
    deltas = [
        (features[b].energy or 0.0) - (features[a].energy or 0.0)
        for a, b in itertools.pairwise(order)
    ]
    return sum(deltas) / len(deltas)


def test_fr10_profiles() -> None:
    """US-5 acceptance on ``arc_01``: a no-op profile cannot pass.

    The fixture's twenty tracks differ *only* in energy, so key/BPM/loudness
    score identically for every pair and the neutral objective is invariant
    under reversing the path -- ascending and descending tie exactly.  ``build``
    multiplies every drop's penalty by 1.5, which must break that tie upwards
    (and ``cool`` downwards), so the mean signed energy delta has to move.
    """
    features = _arc_features()
    assert len(features) == 20
    energies = [f.energy for f in features]
    assert energies == sorted(energies, reverse=True), "arc_01 is listed high energy first"

    neutral = _mean_signed_energy_delta(features, ArcProfile.NEUTRAL)
    build = _mean_signed_energy_delta(features, ArcProfile.BUILD)
    cool = _mean_signed_energy_delta(features, ArcProfile.COOL)

    assert build > neutral, "the build profile must climb where neutral is indifferent"
    assert cool < neutral or cool == neutral < build
    assert build > 0.0


def test_fr10_arc_fixture_is_symmetric_outside_energy() -> None:
    """Only the energy component can distinguish arc_01's candidates."""
    features = _arc_features()
    assert len({(f.bpm, f.key_pc, f.mode, f.loudness_db) for f in features}) == 1
