"""FR-14: the relaxation ladder, ``partial_k`` vs compromise, infeasibility."""

from __future__ import annotations

import pytest
from conftest import NOW, garment, make_forecast, params
from dresscast.engine.assemble import LADDER, diagnose, recommend
from dresscast.engine.comfort import build_day_context
from dresscast.engine.models import CORE_SLOTS, WearHistory
from dresscast.errors import InfeasibleWardrobe


def _run(wardrobe, forecast, history=None, **kwargs):
    return recommend(wardrobe, forecast, history or WearHistory.empty(), params(**kwargs), now=NOW)


def _core_ids(outfit):
    return frozenset(i.garment_id for i in outfit.items if i.slot in CORE_SLOTS)


def _minimal_wardrobe():
    """One base, one bottom, one footwear — exactly one HC-valid outfit."""
    return [
        garment("z1-tee", "only-tee", "tshirt", formality=2, occasions=("casual",)),
        garment("z2-jeans", "only-jeans", "jeans", formality=2, occasions=("casual",)),
        garment("z3-shoes", "only-sneakers", "sneakers", formality=2, occasions=("casual",)),
    ]


def test_fr14_ladder_is_a_sequence_of_lexicographic_prefixes():
    assert len(LADDER) == 4
    assert LADDER[0].applied == ()
    assert [c.rule for c in LADDER[1].applied] == ["HC-8"]
    assert [c.rule for c in LADDER[2].applied] == ["HC-8", "HC-4"]
    assert [c.rule for c in LADDER[3].applied] == ["HC-8", "HC-4", "HC-6"]
    assert LADDER[1].drop_hc8 and LADDER[1].formality_spread == 1
    assert LADDER[2].formality_spread == 2
    assert LADDER[3].lower_moderate_cover


def test_fr14_a_healthy_wardrobe_never_relaxes(wardrobe, spring_swing):
    rec = _run(wardrobe, spring_swing, occasion="casual")
    assert rec.compromises == []
    assert all(o.compromises == [] for o in rec.outfits)


def test_fr14_r1_fires_only_when_hc8_blocks_everything():
    """One possible outfit, worn yesterday: R1 re-admits it, and says so."""
    wardrobe = _minimal_wardrobe()
    forecast = make_forecast(temps=16.0, wind=5.0)
    only = frozenset({"z1-tee", "z2-jeans", "z3-shoes"})
    history = WearHistory(last_worn={}, yesterday_sets=(only,))
    rec = _run(wardrobe, forecast, history, occasion="casual")
    assert len(rec.outfits) == 1
    assert [c.rule for c in rec.compromises] == ["HC-8"]
    assert _core_ids(rec.outfits[0]) == only
    assert "compromise" in [line.line_class for line in rec.outfits[0].reasoning]


def test_fr14_partial_k_is_a_note_not_a_compromise():
    wardrobe = _minimal_wardrobe()
    rec = _run(wardrobe, make_forecast(temps=16.0, wind=5.0), occasion="casual", k=3)
    assert len(rec.outfits) == 1
    assert rec.compromises == []
    note = next(n for n in rec.notes if n.kind == "partial_k")
    payload = note.model_dump()
    assert payload["n"] == 1
    assert payload["k"] == 3
    assert payload["reason"]


def test_fr14_partial_k_produces_a_compromise_reasoning_line():
    wardrobe = _minimal_wardrobe()
    rec = _run(wardrobe, make_forecast(temps=16.0, wind=5.0), occasion="casual", k=3)
    lines = [line for line in rec.outfits[0].reasoning if line.line_class == "compromise"]
    assert len(lines) == 1
    assert "Only 1 of 3" in lines[0].text


def test_fr14_r2_widens_formality_when_nothing_else_can_match():
    """A formality-1 bottom and a formality-4 top: only R2 admits the pair."""
    wardrobe = [
        garment("y1-shirt", "stiff-shirt", "shirt_long_sleeve", formality=4, occasions=("casual",)),
        garment("y2-shorts", "beach-shorts", "shorts", formality=2, occasions=("casual",)),
        garment("y3-shoes", "flip-flops", "sandals", formality=2, occasions=("casual",)),
    ]
    rec = _run(wardrobe, make_forecast(temps=20.0, wind=5.0), occasion="casual")
    assert [c.rule for c in rec.compromises] == ["HC-8", "HC-4"]
    assert len(rec.outfits) == 1


def test_fr14_r3_accepts_lower_cover_for_moderate_rain_only():
    """A DWR jacket (waterproofness 1) is re-admitted for 5 mm/h, never 20."""
    wardrobe = [
        garment("w1-tee", "tee", "tshirt", formality=2, occasions=("casual",)),
        garment("w2-jeans", "jeans", "jeans", formality=2, occasions=("casual",)),
        garment("w3-shoes", "sneakers", "sneakers", formality=2, occasions=("casual",)),
        garment(
            "w4-dwr",
            "dwr-jacket",
            "light_jacket",
            formality=2,
            waterproofness=1,
            occasions=("casual",),
        ),
    ]
    moderate = make_forecast(temps=12.0, wind=5.0, precip_prob=0.9, precip_mmh=5.0)
    rec = _run(wardrobe, moderate, occasion="casual")
    assert [c.rule for c in rec.compromises] == ["HC-8", "HC-4", "HC-6"]
    assert "w4-dwr" in _core_ids(rec.outfits[0])

    heavy = make_forecast(temps=12.0, wind=5.0, precip_prob=0.9, precip_mmh=20.0)
    with pytest.raises(InfeasibleWardrobe):
        _run(wardrobe, heavy, occasion="casual")


def test_fr14_infeasible_names_the_missing_capability_and_carries_a_brief():
    wardrobe = [
        garment("v1-tee", "tee", "tshirt", formality=2, occasions=("casual",)),
        garment("v2-jeans", "jeans", "jeans", formality=2, occasions=("casual",)),
        garment("v3-shoes", "sneakers", "sneakers", formality=2, occasions=("casual",)),
    ]
    heavy = make_forecast(temps=12.0, wind=5.0, precip_prob=0.9, precip_mmh=20.0)
    with pytest.raises(InfeasibleWardrobe) as excinfo:
        _run(wardrobe, heavy, occasion="casual")
    error = excinfo.value
    assert error.code == "infeasible_wardrobe"
    assert "waterproofness" in error.detail["missing"]
    brief = error.detail["brief"]
    assert brief["date"] == "2026-04-14"
    assert len(brief["hours"]) == 15
    assert all(h["rain_required"] for h in brief["hours"])


def test_fr14_infeasible_when_a_whole_slot_is_missing():
    wardrobe = [
        garment("u1-tee", "tee", "tshirt", formality=2, occasions=("casual",)),
        garment("u2-jeans", "jeans", "jeans", formality=2, occasions=("casual",)),
    ]
    with pytest.raises(InfeasibleWardrobe) as excinfo:
        _run(wardrobe, make_forecast(temps=16.0), occasion="casual")
    assert "footwear" in excinfo.value.detail["missing"]


def test_fr14_infeasible_when_the_occasion_matches_nothing(wardrobe):
    with pytest.raises(InfeasibleWardrobe) as excinfo:
        _run(wardrobe, make_forecast(temps=16.0), occasion="formal")
    assert "formal" in excinfo.value.detail["missing"]


def test_fr14_diagnose_reports_the_first_blocking_capability(wardrobe):
    request = params(occasion="formal")
    from dresscast.engine.assemble import build_candidates

    ctx = build_day_context(make_forecast(temps=16.0), request)
    candidates = build_candidates(wardrobe, request, ctx)
    assert "footwear" in diagnose(ctx, candidates, request)


def test_fr14_relaxed_outfits_still_satisfy_hc1_hc2_hc3_hc5():
    """HC-1, HC-2, HC-3 and HC-5 are never relaxed (M10(c))."""
    wardrobe = [
        garment("t1-shirt", "stiff-shirt", "shirt_long_sleeve", formality=4, occasions=("casual",)),
        garment("t2-shorts", "beach-shorts", "shorts", formality=2, occasions=("casual",)),
        garment("t3-shoes", "flip-flops", "sandals", formality=2, occasions=("casual",)),
        garment(
            "t4-hidden", "work-only-trousers", "trousers_thin", formality=4, occasions=("work",)
        ),
        garment(
            "t5-dirty", "dirty-tee", "tshirt", formality=2, occasions=("casual",), status="dirty"
        ),
    ]
    rec = _run(wardrobe, make_forecast(temps=20.0, wind=5.0), occasion="casual")
    for outfit in rec.outfits:
        ids = _core_ids(outfit)
        assert "t4-hidden" not in ids  # HC-3
        assert "t5-dirty" not in ids  # HC-2
        slots = [i.slot for i in outfit.items if i.slot in CORE_SLOTS]
        assert slots.count("base") == 1 and slots.count("footwear") == 1  # HC-1
        assert len(ids) == len(slots)  # HC-5
