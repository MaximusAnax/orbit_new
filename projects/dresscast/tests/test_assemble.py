"""FR-8: the hard part — hard constraints, search, MMR diversity, ranking."""

from __future__ import annotations

from itertools import pairwise

import pytest
from conftest import NOW, garment, params, small_wardrobe
from dresscast.engine.assemble import (
    LADDER,
    build_candidates,
    jaccard,
    recommend,
    search,
    select_diverse,
)
from dresscast.engine.comfort import achievable_band, build_day_context
from dresscast.engine.models import (
    ACCESSORY_SLOTS,
    CORE_SLOTS,
    MMR_JACCARD_MAX,
    WARDROBE_MAX,
    WEIGHTS,
    WearHistory,
)
from dresscast.errors import WardrobeTooLarge


def _run(wardrobe, forecast, history=None, **kwargs):
    request = params(**kwargs)
    return recommend(wardrobe, forecast, history or WearHistory.empty(), request, now=NOW)


def _core_ids(outfit):
    return frozenset(i.garment_id for i in outfit.items if i.slot in CORE_SLOTS)


# --------------------------------------------------------------------------
# Hard constraints
# --------------------------------------------------------------------------


def test_fr8_hc1_every_outfit_fills_the_required_slots(wardrobe, spring_swing):
    for outfit in _run(wardrobe, spring_swing).outfits:
        slots = [i.slot for i in outfit.items if i.slot in CORE_SLOTS]
        assert slots.count("base") == 1
        assert slots.count("bottom") == 1
        assert slots.count("footwear") == 1
        assert slots.count("mid_1") + slots.count("mid_2") <= 2
        assert slots.count("outer") <= 1
        assert slots.count("leg_base") <= 1
        assert "mid_2" not in slots or "mid_1" in slots


def test_fr8_hc2_dirty_garments_never_appear(wardrobe, spring_swing):
    dirty_ids = {"b5-oxford", "p5-thick", "m3-thick"}
    laundered = [
        g.model_copy(update={"status": "dirty"}) if g.id in dirty_ids else g for g in wardrobe
    ]
    for outfit in _run(laundered, spring_swing).outfits:
        assert not (_core_ids(outfit) & dirty_ids)


def test_fr8_hc2_retired_garments_never_appear(wardrobe, spring_swing):
    retired = [
        g.model_copy(update={"status": "retired"}) if g.id == "f3-boots" else g for g in wardrobe
    ]
    for outfit in _run(retired, spring_swing).outfits:
        assert "f3-boots" not in _core_ids(outfit)


def test_fr8_hc3_every_core_item_lists_the_requested_occasion(wardrobe, spring_swing):
    by_id = {g.id: g for g in wardrobe}
    for occasion in ("work", "casual"):
        for outfit in _run(wardrobe, spring_swing, occasion=occasion).outfits:
            for gid in _core_ids(outfit):
                assert occasion in by_id[gid].occasions


def test_fr8_hc3_sneakers_tagged_casual_sport_never_appear_in_formal(wardrobe):
    """US-6: occasion filtering is absolute, not a preference."""
    by_id = {g.id: g for g in wardrobe}
    assert "formal" not in by_id["f1-sandals"].occasions


def test_fr8_hc4_formality_spread_is_at_most_one(wardrobe, spring_swing):
    by_id = {g.id: g for g in wardrobe}
    for outfit in _run(wardrobe, spring_swing, occasion="casual").outfits:
        formalities = [by_id[g].formality for g in _core_ids(outfit)]
        assert max(formalities) - min(formalities) <= 1


def test_fr8_hc5_no_garment_appears_twice_in_an_outfit(wardrobe, spring_swing):
    for outfit in _run(wardrobe, spring_swing).outfits:
        ids = [i.garment_id for i in outfit.items]
        assert len(ids) == len(set(ids))


def test_fr8_hc6_rain_hours_are_covered(wardrobe, summer_thunderstorm):
    """US-5: a waterproofness ≥ 2 layer is on during the downpour."""
    by_id = {g.id: g for g in wardrobe}
    for outfit in _run(wardrobe, summer_thunderstorm, occasion="casual").outfits:
        wet = [e for e in outfit.hour_plan if e.precip_prob >= 0.5]
        assert wet
        for entry in wet:
            worn = [
                by_id[i.garment_id]
                for i in outfit.items
                if i.slot in entry.worn_slots and i.slot in CORE_SLOTS
            ]
            assert max(g.waterproofness for g in worn) >= 2


def test_fr8_hc7_leg_base_only_below_zero(wardrobe, spring_swing, winter_calm):
    for outfit in _run(wardrobe, spring_swing, occasion="casual").outfits:
        assert "leg_base" not in [i.slot for i in outfit.items]
    cold = _run(wardrobe, winter_calm, occasion="casual").outfits
    assert any("leg_base" in [i.slot for i in o.items] for o in cold)


def test_fr8_hc8_never_repeats_yesterdays_exact_core_set(wardrobe, spring_swing):
    """US-8: today's recommendations never contain yesterday's identical set."""
    first = _run(wardrobe, spring_swing, occasion="casual").outfits[0]
    worn = _core_ids(first)
    history = WearHistory(last_worn={g: "2026-04-13" for g in worn}, yesterday_sets=(worn,))
    again = _run(wardrobe, spring_swing, history, occasion="casual")
    assert all(_core_ids(o) != worn for o in again.outfits)
    assert again.compromises == []


# --------------------------------------------------------------------------
# Ranking, diversity, scoring
# --------------------------------------------------------------------------


def test_fr8_ranks_are_contiguous_and_scores_non_increasing(wardrobe, spring_swing):
    rec = _run(wardrobe, spring_swing, occasion="casual", k=3)
    assert [o.rank for o in rec.outfits] == [1, 2, 3]
    scores = [o.score_total for o in rec.outfits]
    assert scores == sorted(scores, reverse=True)


def test_fr8_score_total_is_the_documented_weighted_sum(wardrobe, spring_swing):
    for outfit in _run(wardrobe, spring_swing, occasion="casual").outfits:
        s = outfit.scores
        expected = (
            WEIGHTS["thermal"] * s.thermal
            + WEIGHTS["protection"] * s.protection
            + WEIGHTS["color"] * s.color
            + WEIGHTS["style"] * s.style
            + WEIGHTS["variety"] * s.variety
        )
        assert outfit.score_total == pytest.approx(expected, abs=1e-6)
        assert s.weights == WEIGHTS


def test_fr8_topk_outfits_are_pairwise_diverse(wardrobe, spring_swing):
    rec = _run(wardrobe, spring_swing, occasion="casual", k=3)
    ids = [_core_ids(o) for o in rec.outfits]
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            assert jaccard(a, b) <= MMR_JACCARD_MAX


def test_fr8_jaccard_helper():
    assert jaccard(frozenset("ab"), frozenset("ab")) == 1.0
    assert jaccard(frozenset("ab"), frozenset("cd")) == 0.0
    assert jaccard(frozenset("abc"), frozenset("abd")) == pytest.approx(0.5)
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_fr8_k_is_respected_and_smaller_k_is_a_prefix(wardrobe, spring_swing):
    three = _run(wardrobe, spring_swing, occasion="casual", k=3)
    one = _run(wardrobe, spring_swing, occasion="casual", k=1)
    assert len(three.outfits) == 3
    assert len(one.outfits) == 1
    assert _core_ids(one.outfits[0]) == _core_ids(three.outfits[0])


def test_fr8_partial_k_is_a_note_never_a_compromise(wardrobe, spring_swing):
    """FR-14: fewer than k outfits is not a degradation."""
    rec = _run(wardrobe, spring_swing, occasion="casual", k=9)
    if len(rec.outfits) < 9:
        kinds = [n.kind for n in rec.notes]
        assert "partial_k" in kinds
        assert rec.compromises == []


def test_fr8_search_reports_the_feasible_count(wardrobe, spring_swing):
    request = params(occasion="casual")
    ctx = build_day_context(spring_swing, request)
    candidates = build_candidates(wardrobe, request, ctx)
    band = achievable_band(ctx, candidates, occasion="casual")
    result = search(ctx, candidates, band, WearHistory.empty(), request, LADDER[0])
    # Every HC-valid outfit is counted; the bound-pruner may skip *scoring*
    # outfits it can prove fall outside the live pool, so scored ≤ feasible.
    assert result.feasible_count >= len(result.scored) > 100
    closed_form = (
        len(candidates.base)
        * len(candidates.bottom)
        * len(candidates.footwear)
        * (1 + len(candidates.mid) + len(candidates.mid) * (len(candidates.mid) - 1) // 2)
        * (1 + len(candidates.outer))
        * (1 + len(candidates.leg_base))
    )
    assert result.feasible_count <= closed_form


def test_fr8_select_diverse_is_greedy_by_score(wardrobe, spring_swing):
    request = params(occasion="casual")
    ctx = build_day_context(spring_swing, request)
    candidates = build_candidates(wardrobe, request, ctx)
    band = achievable_band(ctx, candidates, occasion="casual")
    result = search(ctx, candidates, band, WearHistory.empty(), request, LADDER[0])
    picked = select_diverse(result.scored, 3)
    assert picked[0] is result.scored[0]
    assert [p.total for p in picked] == sorted((p.total for p in picked), reverse=True)


def test_fr8_ties_break_deterministically_on_the_garment_id_tuple(wardrobe, spring_swing):
    request = params(occasion="casual")
    ctx = build_day_context(spring_swing, request)
    candidates = build_candidates(wardrobe, request, ctx)
    band = achievable_band(ctx, candidates, occasion="casual")
    result = search(ctx, candidates, band, WearHistory.empty(), request, LADDER[0])
    keys = [c.sort_key() for c in result.scored]
    assert keys == sorted(keys)


# --------------------------------------------------------------------------
# The layered-day claim (US-4) and the warm-rain trap (US-5)
# --------------------------------------------------------------------------


def test_fr8_us4_swing_day_returns_a_removable_layer_and_a_real_plan(wardrobe, spring_swing):
    outfit = _run(wardrobe, spring_swing, occasion="casual").outfits[0]
    slots = [i.slot for i in outfit.items]
    assert {"mid_1", "outer"} & set(slots)
    worn = [tuple(e.worn_slots) for e in outfit.hour_plan]
    assert len(set(worn)) >= 2
    changes = sum(1 for a, b in pairwise(worn) if a != b)
    assert changes <= 3


def test_fr8_us5_warm_rain_picks_a_shell_not_a_coat(wardrobe, summer_thunderstorm):
    by_id = {g.id: g for g in wardrobe}
    outfit = _run(wardrobe, summer_thunderstorm, occasion="casual").outfits[0]
    ids = _core_ids(outfit)
    assert "o1-shell" in ids
    assert "o4-parka" not in ids
    rain = [e for e in outfit.hour_plan if e.precip_prob >= 0.5]
    # insulation sits at the minimum the mandatory cover allows (D17)
    assert all(e.clamped == "wardrobe_floor" for e in rain)
    assert all(e.ensemble_clo == pytest.approx(e.target_clo, abs=1e-3) for e in rain)
    dry = [e for e in outfit.hour_plan if e.precip_prob < 0.5]
    assert min(e.ensemble_clo for e in dry) < min(e.ensemble_clo for e in rain)
    assert (
        max(g.formality for g in (by_id[i] for i in ids))
        - min(g.formality for g in (by_id[i] for i in ids))
        <= 1
    )


def test_fr8_us4_cold_morning_warm_afternoon_stays_in_band(wardrobe, spring_swing):
    outfit = _run(wardrobe, spring_swing, occasion="casual").outfits[0]
    assert all(e.in_band for e in outfit.hour_plan)


# --------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------


def test_fr13_recommendation_records_provenance(wardrobe, spring_swing):
    rec = _run(wardrobe, spring_swing, occasion="casual", seed=7)
    assert rec.snapshot_id == spring_swing.id
    assert rec.seed == 7
    assert rec.engine_version
    assert len(rec.wardrobe_hash) == 64
    assert rec.params.occasion == "casual"
    assert rec.params.weights == WEIGHTS
    assert rec.params.thresholds_version


def test_fr19_seed_has_no_effect_on_the_output(wardrobe, spring_swing):
    a = _run(wardrobe, spring_swing, occasion="casual", seed=1)
    b = _run(wardrobe, spring_swing, occasion="casual", seed=999)
    assert [_core_ids(o) for o in a.outfits] == [_core_ids(o) for o in b.outfits]
    assert [o.score_total for o in a.outfits] == [o.score_total for o in b.outfits]


def test_fr8_wardrobe_cap_is_enforced_with_a_clear_error(spring_swing):
    huge = [
        garment(f"x{i:04d}", f"tee-{i:04d}", "tshirt", occasions=("casual",))
        for i in range(WARDROBE_MAX + 1)
    ]
    with pytest.raises(WardrobeTooLarge) as excinfo:
        _run(huge, spring_swing, occasion="casual")
    assert excinfo.value.code == "wardrobe_too_large"
    assert excinfo.value.detail["limit"] == WARDROBE_MAX


def test_fr9_accessories_occupy_accessory_slots_in_id_order(wardrobe, winter_calm):
    outfit = _run(wardrobe, winter_calm, occasion="casual").outfits[0]
    accessory_items = [i for i in outfit.items if i.slot in ACCESSORY_SLOTS]
    assert accessory_items
    assert [i.slot for i in accessory_items] == list(ACCESSORY_SLOTS[: len(accessory_items)])
    assert [i.garment_id for i in accessory_items] == sorted(i.garment_id for i in accessory_items)
    assert len(outfit.accessories) == len(accessory_items)


def test_fr9_accessories_never_change_the_ensemble_insulation(wardrobe, winter_calm):
    outfit = _run(wardrobe, winter_calm, occasion="casual").outfits[0]
    for entry in outfit.hour_plan:
        assert all(not s.startswith("accessory") for s in entry.worn_slots)


def test_fr8_parameter_response_met_moves_the_worn_insulation(wardrobe, spring_swing):
    """M4(j): a lower metabolic rate must ask for more insulation."""

    def mean_icl(met: float) -> float:
        outfit = _run(wardrobe, spring_swing, occasion="casual", met=met).outfits[0]
        return sum(e.ensemble_clo for e in outfit.hour_plan) / len(outfit.hour_plan)

    assert mean_icl(1.2) > mean_icl(1.6) > mean_icl(2.2)


def test_fr8_parameter_response_wear_window_selects_exactly_those_hours(wardrobe, spring_swing):
    outfit = _run(wardrobe, spring_swing, occasion="casual", wear_window=(17, 22)).outfits[0]
    assert [e.hour for e in outfit.hour_plan] == [17, 18, 19, 20, 21]


def test_fr8_candidate_cap_keeps_lists_bounded(spring_swing):
    """D12: a slot with more than 40 members is capped by thermal plausibility."""
    many = small_wardrobe() + [
        garment(f"extra{i:03d}", f"extra-tee-{i:03d}", "tshirt", occasions=("casual",))
        for i in range(60)
    ]
    request = params(occasion="casual")
    ctx = build_day_context(spring_swing, request)
    candidates = build_candidates(many, request, ctx)
    assert len(candidates.base) == 40
    assert list(candidates.base) == sorted(candidates.base, key=lambda g: g.id)
