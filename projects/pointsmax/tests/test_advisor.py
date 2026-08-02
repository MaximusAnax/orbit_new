"""FR-9 / FR-16 orchestration: ranking, comparators, verdicts, determinism."""

from __future__ import annotations

from datetime import date

import pytest
from conftest import make_world, world_files
from pointsmax.engine.advisor import compute_plan_set, plan_set_fingerprint, rank_drafts
from pointsmax.engine.goals import cash_goal, flight_goal, stay_goal
from pointsmax.engine.search import SearchBudgetExceeded
from pointsmax.models import (
    DISCLAIMER,
    Cabin,
    CaveatCode,
    PlanParams,
    StepKind,
    Verdict,
    Wallet,
)

TODAY = date(2026, 7, 31)


def plan_set(world, wallet, goal, *, today=TODAY, params=None, pruning=True):
    return compute_plan_set(
        world=world, wallet=wallet, goal=goal, today=today, params=params, pruning=pruning
    )


def rt_business():
    return flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=True
    )


def test_fr9_ranked_output_is_sorted_by_net_value_descending(world, wallet):
    result = plan_set(world, wallet, rt_business())
    assert result.verdict is Verdict.BOOK_WITH_POINTS
    values = [p.net_value_cents for p in result.plans if not p.is_comparator]
    assert values == sorted(values, reverse=True)
    assert [p.rank for p in result.plans] == list(range(1, len(result.plans) + 1))


def test_fr9_top_k_limits_the_ranked_list(world, wallet):
    result = plan_set(world, wallet, rt_business(), params=PlanParams(top_k=2))
    ranked = [p for p in result.plans if not p.is_comparator]
    assert len(ranked) == 2


def test_fr9_plans_are_unique_by_signature(world, wallet):
    result = plan_set(world, wallet, rt_business())
    signatures = [p.signature for p in result.plans]
    assert len(set(signatures)) == len(signatures)


def test_fr9_comparator_is_appended_below_the_cutoff_when_feasible(world):
    """US-4: the portal comparator is shown even when it does not make the top-K."""
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 600000, "air_x": 200000})
    result = plan_set(world, wallet, rt_business(), params=PlanParams(top_k=1))
    assert len(result.plans) == 2
    comparator = result.plans[-1]
    assert comparator.is_comparator is True
    assert {s.kind for s in comparator.steps} == {StepKind.BOOK_PORTAL}
    assert comparator.net_value_cents <= result.plans[0].net_value_cents


def test_fr9_no_comparator_when_no_portal_plan_is_fundable(world):
    wallet = Wallet(cards=["card_a"], balances={"air_x": 200000})
    result = plan_set(world, wallet, rt_business())
    assert all(not p.is_comparator for p in result.plans)


def test_fr9_comparator_not_duplicated_when_already_in_the_top_k(world):
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 600000})
    result = plan_set(world, wallet, rt_business())
    portal_only = [p for p in result.plans if {s.kind for s in p.steps} == {StepKind.BOOK_PORTAL}]
    assert len(portal_only) == 1


def test_fr9_verdict_is_pay_cash_when_the_best_plan_destroys_value(world):
    """A stay funded from a 2.0-cent bank into an 0.8-cent hotel loses money."""
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 400000})
    result = plan_set(world, wallet, stay_goal(city="AAA", nights=5, month="2026-10"))
    assert result.verdict is Verdict.PAY_CASH_KEEP_POINTS
    assert result.plans[0].net_value_cents <= 0
    assert result.recommended() is None


def test_fr9_verdict_insufficient_points_emits_no_plans(world):
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 1000})
    result = plan_set(world, wallet, rt_business())
    assert result.verdict is Verdict.INSUFFICIENT_POINTS
    assert result.plans == []


def test_fr9_verdict_no_matching_award_when_nothing_can_be_booked(world):
    wallet = Wallet(cards=["card_a_basic"], balances={"bank_a": 500000})
    goal = flight_goal(
        origin_city="AAA", dest_city="CCC", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    result = plan_set(world, wallet, goal)
    assert result.verdict is Verdict.NO_MATCHING_AWARD
    assert result.plans == []


def test_fr12_cash_goal_produces_a_cash_plan_verdict(world):
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 100000, "bank_b": 50000})
    result = plan_set(world, wallet, cash_goal())
    assert result.verdict is Verdict.CASH_PLAN
    top = result.plans[0]
    assert top.cash_received_cents == 100000 + 40000
    assert all(s.kind is StepKind.REDEEM_CASH for s in top.steps)
    assert all(not p.is_comparator for p in result.plans)


def test_fr12_cash_goal_with_nothing_redeemable_says_so(world):
    wallet = Wallet(cards=["card_a"], balances={"air_x": 100000})
    result = plan_set(world, wallet, cash_goal())
    assert result.verdict is Verdict.NO_MATCHING_AWARD
    assert result.plans == []


def test_fr12_below_baseline_caveat_rides_on_cash_plans(world):
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 100000})
    result = plan_set(world, wallet, cash_goal())
    codes = {c.code for c in result.plans[0].caveats}
    assert CaveatCode.BELOW_BASELINE in codes


# -- FR-16 determinism, pinning and safeguards ----------------------------


def test_fr16_recomputation_is_byte_identical(world, wallet):
    a = plan_set(world, wallet, rt_business())
    b = plan_set(world, wallet, rt_business())
    assert plan_set_fingerprint(a) == plan_set_fingerprint(b)


def test_fr16_pruning_switch_changes_only_the_expansion_count(world, wallet):
    import json

    fast = plan_set(world, wallet, rt_business(), pruning=True)
    slow = plan_set(world, wallet, rt_business(), pruning=False)
    a = json.loads(plan_set_fingerprint(fast))
    b = json.loads(plan_set_fingerprint(slow))
    assert a.pop("expansions") <= b.pop("expansions")
    assert a == b


def test_fr16_plan_sets_pin_the_world_version_and_hash(world, wallet):
    result = plan_set(world, wallet, rt_business())
    assert result.world_version == world.version.version
    assert result.world_hash == world.version.content_hash


def test_fr16_every_plan_set_carries_the_disclaimer(world, wallet):
    for goal in (rt_business(), cash_goal(), stay_goal(city="AAA", nights=2, month="2026-10")):
        assert plan_set(world, wallet, goal).disclaimer == DISCLAIMER


def test_fr16_today_is_an_input_that_changes_the_answer(world):
    """The promo edge is active on 2026-08-01 and gone by 2026-08-11."""
    wallet = Wallet(cards=["card_b"], balances={"bank_b": 200000})
    goal = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.PREMIUM_ECONOMY,
        round_trip=False,
    )
    early = plan_set(world, wallet, goal, today=date(2026, 8, 1))
    late = plan_set(world, wallet, goal, today=date(2026, 8, 11))
    early_edges = {s.edge_id for p in early.plans for s in p.steps if s.edge_id}
    late_edges = {s.edge_id for p in late.plans for s in p.steps if s.edge_id}
    assert "bank_b__air_y_promo" in early_edges
    assert "bank_b__air_y_promo" not in late_edges


def test_fr16_no_card_acquisition_advice_appears_anywhere(world, wallet):
    """Non-goal 5 / FR-16(d), enforced as behaviour rather than prose."""
    result = plan_set(world, wallet, rt_business())
    blob = plan_set_fingerprint(result).lower()
    for phrase in ("apply for", "sign up", "welcome bonus", "open a card", "new card"):
        assert phrase not in blob


def test_fr16_expansions_are_reported_and_bounded(world, wallet):
    result = plan_set(world, wallet, rt_business())
    assert 0 < result.expansions <= PlanParams().max_expansions


def test_fr7c_budget_exhaustion_surfaces_from_the_advisor(world, wallet):
    with pytest.raises(SearchBudgetExceeded):
        plan_set(world, wallet, rt_business(), params=PlanParams(max_expansions=2))


def test_fr9_rank_drafts_collapses_duplicate_canonical_forms(world, wallet):
    from pointsmax.engine.search import (
        FundingSearch,
        enumerate_booking_sets,
        solve_booking_set,
    )
    from pointsmax.engine.world import active_subgraph

    goal = rt_business()
    active = active_subgraph(world, wallet.cards, TODAY)
    search = FundingSearch(world, active, wallet.balances, TODAY, PlanParams())
    drafts = [
        d
        for bookings in enumerate_booking_sets(goal, world, active, TODAY)
        if (d := solve_booking_set(search, bookings)) is not None
    ]
    ranked = rank_drafts(world, goal, drafts + drafts)
    assert len(ranked) == len(rank_drafts(world, goal, drafts))


def test_fr7d_deadline_binding_scenario_picks_the_instant_plan(world):
    """A cheaper slow plan exists, but only the instant one lands before book_by."""
    wallet = Wallet(cards=["card_a", "card_b"], balances={"bank_a": 200000, "bank_b": 200000})
    # 3 passengers rules out the seat-capped x_soon offer, leaving one award.
    relaxed = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=False,
        passengers=3,
    )
    tight = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=False,
        passengers=3,
        book_by=date(2026, 8, 1),
    )

    slow_sources = {
        s.from_program
        for s in plan_set(world, wallet, relaxed).plans[0].steps
        if s.kind is StepKind.TRANSFER
    }
    fast_sources = {
        s.from_program
        for s in plan_set(world, wallet, tight).plans[0].steps
        if s.kind is StepKind.TRANSFER
    }
    assert slow_sources == {"bank_b"}  # cheaper per delivered point, 2 days
    assert fast_sources == {"bank_a"}  # instant, the only one that lands in time
    assert plan_set(world, wallet, tight).plans[0].feasible_in_days == 0


def test_fr10_stale_world_caveat_reaches_every_plan(world, wallet):
    files = world_files()
    for valuation in files["valuations.json"]:
        valuation["as_of"] = "2025-01-01"
    stale = make_world(files)
    result = plan_set(stale, wallet, rt_business())
    assert result.plans
    for plan in result.plans:
        assert any(c.code is CaveatCode.STALE_WORLD for c in plan.caveats)


def test_engine_runs_on_the_shipped_dataset(shipped_world):
    """M6's premise: the real dataset must be plannable, not just the toy worlds."""
    wallet = Wallet(
        cards=["chase_sapphire_reserve", "amex_platinum", "capital_one_venture_x"],
        balances={
            "chase_ur": 210000,
            "amex_mr": 130000,
            "capital_one": 90000,
            "marriott_bonvoy": 150000,
        },
    )
    goal = flight_goal(
        origin_city="NYC",
        dest_city="PAR",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=True,
        passengers=2,
    )
    result = compute_plan_set(
        world=shipped_world, wallet=wallet, goal=goal, today=date(2026, 7, 31)
    )
    assert result.verdict is Verdict.BOOK_WITH_POINTS
    assert result.plans
    assert result.expansions < PlanParams().max_expansions
    top = result.plans[0]
    assert top.net_value_cents > 0
    assert all(step.explanation for step in top.steps)
    assert any(c.code is CaveatCode.IRREVERSIBLE_TRANSFER for c in top.caveats)
