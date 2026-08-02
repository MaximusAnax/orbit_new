"""FR-19: determinism, quantization and the 1-ULP robustness the docs promise."""

from __future__ import annotations

import json
import math

import pytest
from conftest import NOW, params, small_wardrobe
from dresscast.engine.assemble import recommend
from dresscast.engine.comfort import day_brief
from dresscast.engine.models import (
    PLAN_DP,
    SCORE_DP,
    Recommendation,
    WearHistory,
    wardrobe_hash,
)

EXCLUDED = {"id", "created_at", "fetched_at", "snapshot_id"}


def _canonical(rec: Recommendation) -> str:
    payload = rec.model_dump(mode="json")
    for key in EXCLUDED:
        payload.pop(key, None)
    for outfit in payload["outfits"]:
        outfit.pop("id", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _run(wardrobe, forecast, history=None, **kwargs):
    return recommend(wardrobe, forecast, history or WearHistory.empty(), params(**kwargs), now=NOW)


def test_fr19_identical_inputs_give_byte_identical_output(wardrobe, spring_swing):
    first = _run(wardrobe, spring_swing, occasion="casual")
    second = _run(small_wardrobe(), spring_swing, occasion="casual")
    assert _canonical(first) == _canonical(second)


def test_fr19_wardrobe_order_does_not_affect_the_result(wardrobe, spring_swing):
    shuffled = list(reversed(wardrobe))
    assert _canonical(_run(wardrobe, spring_swing, occasion="casual")) == _canonical(
        _run(shuffled, spring_swing, occasion="casual")
    )


def test_fr19_every_score_is_rounded_to_score_dp(wardrobe, spring_swing):
    for outfit in _run(wardrobe, spring_swing, occasion="casual").outfits:
        assert outfit.score_total == round(outfit.score_total, SCORE_DP)
        for value in (
            outfit.scores.thermal,
            outfit.scores.protection,
            outfit.scores.color,
            outfit.scores.style,
            outfit.scores.variety,
        ):
            assert value == round(value, SCORE_DP)


def test_fr19_every_plan_float_is_rounded_to_plan_dp(wardrobe, spring_swing):
    for outfit in _run(wardrobe, spring_swing, occasion="casual").outfits:
        for entry in outfit.hour_plan:
            for value in (
                entry.temp_c,
                entry.wind_kmh,
                entry.humidity_pct,
                entry.precip_prob,
                entry.precip_mmh,
                entry.uv_index,
                entry.bare_feels_c,
                entry.effective_wind_kmh,
                entry.feels_c,
                entry.required_clo,
                entry.target_clo,
                entry.ensemble_clo,
                entry.deviation,
                entry.hour_score,
                entry.protect_score,
            ):
                assert value == round(value, PLAN_DP)


def test_fr19_one_ulp_input_perturbation_changes_nothing(wardrobe, spring_swing):
    """The property FR-19's quantization exists to buy (EVALS.md M7 check 4)."""
    nudged_hours = [
        h.model_copy(
            update={
                "temp_c": math.nextafter(h.temp_c, math.inf),
                "wind_kmh": math.nextafter(h.wind_kmh, math.inf),
                "humidity_pct": math.nextafter(h.humidity_pct, math.inf),
            }
        )
        for h in spring_swing.hours
    ]
    nudged = spring_swing.model_copy(update={"hours": nudged_hours})
    baseline = _run(wardrobe, spring_swing, occasion="casual")
    perturbed = _run(wardrobe, nudged, occasion="casual")
    assert _canonical(baseline) == _canonical(perturbed)


def test_fr19_wardrobe_hash_is_request_independent(wardrobe, spring_swing):
    work = _run(wardrobe, spring_swing, occasion="work")
    casual = _run(wardrobe, spring_swing, occasion="casual")
    assert work.wardrobe_hash == casual.wardrobe_hash
    assert work.wardrobe_hash == wardrobe_hash(wardrobe)


def test_fr19_wardrobe_hash_uses_the_documented_serialization(wardrobe):
    import hashlib

    records = sorted(
        (g.hash_record() for g in wardrobe if g.status != "retired"),
        key=lambda r: r["id"],
    )
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"))
    assert wardrobe_hash(wardrobe) == hashlib.sha256(blob.encode()).hexdigest()


def test_fr19_the_engine_reads_no_clock(wardrobe, spring_swing):
    """``now`` only lands in ``created_at``; nothing else may depend on it."""
    from datetime import datetime

    early = recommend(
        wardrobe, spring_swing, WearHistory.empty(), params(occasion="casual"), now=NOW
    )
    late = recommend(
        wardrobe,
        spring_swing,
        WearHistory.empty(),
        params(occasion="casual"),
        now=datetime(2031, 12, 31, 23, 59),
    )
    assert _canonical(early) == _canonical(late)
    assert early.created_at != late.created_at


def test_fr19_score_total_recomputes_from_the_stored_components(wardrobe, spring_swing):
    for outfit in _run(wardrobe, spring_swing, occasion="casual").outfits:
        weights = outfit.scores.weights
        recomputed = (
            weights["thermal"] * outfit.scores.thermal
            + weights["protection"] * outfit.scores.protection
            + weights["color"] * outfit.scores.color
            + weights["style"] * outfit.scores.style
            + weights["variety"] * outfit.scores.variety
        )
        assert outfit.score_total == pytest.approx(recomputed, abs=1e-6)


def test_fr19_day_brief_is_deterministic_under_one_ulp_perturbation(spring_swing):
    nudged_hours = [
        h.model_copy(update={"temp_c": math.nextafter(h.temp_c, math.inf)})
        for h in spring_swing.hours
    ]
    nudged = spring_swing.model_copy(update={"hours": nudged_hours})
    assert day_brief(spring_swing, params()).model_dump(mode="json") == day_brief(
        nudged, params()
    ).model_dump(mode="json")


def test_fr19_history_changes_the_result_deterministically(wardrobe, spring_swing):
    history = WearHistory(last_worn={"f3-boots": "2026-04-13"}, yesterday_sets=())
    a = _run(wardrobe, spring_swing, history, occasion="casual")
    b = _run(wardrobe, spring_swing, history, occasion="casual")
    assert _canonical(a) == _canonical(b)


def test_fr19_no_rng_is_constructed_on_the_core_path():
    """D12: ``seed`` is persisted for forward compatibility and does nothing."""
    import inspect

    from dresscast.engine import assemble, comfort, explain, palette, protection, style
    from dresscast.engine import variety as variety_module

    for module in (assemble, comfort, explain, palette, protection, style, variety_module):
        source = inspect.getsource(module)
        assert "random" not in source, f"{module.__name__} references randomness"


def test_fr19_ranking_reads_the_rounded_scores(wardrobe, spring_swing):
    rec = _run(wardrobe, spring_swing, occasion="casual")
    scores = [o.score_total for o in rec.outfits]
    assert scores == sorted(scores, reverse=True)
    assert all(s == round(s, SCORE_DP) for s in scores)
