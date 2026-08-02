"""DATA_MODEL invariants enforced by the Pydantic layer."""

from __future__ import annotations

from datetime import date

import pytest
from pointsmax.models import (
    CASHOUT_IS_CASH,
    DISCLAIMER,
    STEP_KIND_RANK,
    AwardOffer,
    CashoutMethod,
    CashoutOption,
    Caveat,
    CaveatCode,
    GoalKind,
    GoalSpec,
    LedgerEntry,
    LedgerReason,
    Plan,
    PlanSet,
    PlanStep,
    StepKind,
    TransferEdge,
    Verdict,
    Wallet,
    canonical_json,
    is_cash_method,
)
from pydantic import ValidationError


def test_canonical_json_is_sorted_and_compact():
    assert canonical_json({"b": 1, "a": [3, 2]}) == '{"a":[3,2],"b":1}'
    assert canonical_json({"n": "café"}) == '{"n":"café"}'


def test_data_model_cashout_liquidity_is_derived_from_the_method():
    assert CASHOUT_IS_CASH == {
        CashoutMethod.STATEMENT_CREDIT: True,
        CashoutMethod.BANK_DEPOSIT: True,
        CashoutMethod.PORTAL_TRAVEL: False,
        CashoutMethod.GIFT_CARD: False,
    }
    assert is_cash_method(CashoutMethod.STATEMENT_CREDIT)
    assert not is_cash_method(CashoutMethod.PORTAL_TRAVEL)
    portal = CashoutOption(
        id="p", program_id="bank_a", method=CashoutMethod.PORTAL_TRAVEL, cpp_milli=1500
    )
    assert portal.is_cash is False
    # The dataset cannot contradict the mapping: is_cash is not a field.
    with pytest.raises(ValidationError):
        CashoutOption(
            id="p", program_id="bank_a", method="portal_travel", cpp_milli=1500, is_cash=True
        )


def test_transfer_edge_rejects_self_loops_and_unpaired_bonus_fields():
    with pytest.raises(ValidationError):
        TransferEdge(
            id="e",
            from_program="a",
            to_program="a",
            ratio_from=1,
            ratio_to=1,
            min_from=1000,
            increment_from=1000,
        )
    with pytest.raises(ValidationError):
        TransferEdge(
            id="e",
            from_program="a",
            to_program="b",
            ratio_from=1,
            ratio_to=1,
            min_from=1000,
            increment_from=1000,
            bonus_per_from=60000,
        )
    with pytest.raises(ValidationError):
        TransferEdge(
            id="e",
            from_program="a",
            to_program="b",
            ratio_from=1,
            ratio_to=1,
            min_from=1000,
            increment_from=1000,
            valid_from=date(2026, 9, 1),
            valid_to=date(2026, 8, 1),
        )


def test_world_entities_are_frozen():
    edge = TransferEdge(
        id="e",
        from_program="a",
        to_program="b",
        ratio_from=1,
        ratio_to=1,
        min_from=1000,
        increment_from=1000,
    )
    with pytest.raises(ValidationError):
        edge.min_from = 2000


def test_award_offer_kind_specific_nullability():
    with pytest.raises(ValidationError):
        AwardOffer(
            id="o",
            program_id="air_x",
            kind="flight",
            points_price=1,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 10, 31),
        )
    with pytest.raises(ValidationError):
        AwardOffer(
            id="o",
            program_id="hotel_h",
            kind="stay",
            city="AAA",
            cabin="business",
            points_price=1,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 10, 31),
        )
    with pytest.raises(ValidationError):
        AwardOffer(
            id="o",
            program_id="air_x",
            kind="flight",
            origin="AAA",
            destination="BBB",
            cabin="business",
            round_trip=False,
            points_price=1,
            travel_window_start=date(2026, 10, 31),
            travel_window_end=date(2026, 10, 1),
        )


# -- FR-2 ledger -----------------------------------------------------------


def test_fr2_ledger_entry_links_to_a_plan_step_iff_the_reason_requires_it():
    with pytest.raises(ValidationError):
        LedgerEntry(
            program_id="bank_a",
            delta_points=-1000,
            post_balance=0,
            reason=LedgerReason.TRANSFER_OUT,
            at="2026-08-01T00:00:00Z",
        )
    with pytest.raises(ValidationError):
        LedgerEntry(
            program_id="bank_a",
            delta_points=1000,
            post_balance=1000,
            reason=LedgerReason.SET,
            plan_step_id=3,
            at="2026-08-01T00:00:00Z",
        )
    ok = LedgerEntry(
        program_id="bank_a",
        delta_points=-1000,
        post_balance=0,
        reason=LedgerReason.TRANSFER_OUT,
        plan_step_id=3,
        at="2026-08-01T00:00:00Z",
    )
    assert ok.plan_step_id == 3


def test_fr2_post_balance_may_never_be_negative():
    with pytest.raises(ValidationError):
        LedgerEntry(
            program_id="bank_a",
            delta_points=-10,
            post_balance=-10,
            reason=LedgerReason.ADJUST,
            at="2026-08-01T00:00:00Z",
        )


def test_fr2_wallet_cards_are_unique_and_balances_non_negative():
    with pytest.raises(ValidationError):
        Wallet(cards=["card_a", "card_a"])
    with pytest.raises(ValidationError):
        Wallet(balances={"bank_a": -1})


# -- FR-4 goals ------------------------------------------------------------


def test_fr4_flight_goal_requires_its_fields_and_forbids_others():
    with pytest.raises(ValidationError):
        GoalSpec(kind=GoalKind.FLIGHT, origin_city="AAA")
    with pytest.raises(ValidationError):
        GoalSpec(
            kind=GoalKind.FLIGHT,
            origin_city="AAA",
            dest_city="BBB",
            round_trip=False,
            passengers=1,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 10, 31),
            nights=3,
        )
    with pytest.raises(ValidationError):
        GoalSpec(
            kind=GoalKind.FLIGHT,
            origin_city="AAA",
            dest_city="AAA",
            round_trip=False,
            passengers=1,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 10, 31),
        )


def test_fr4_passenger_and_night_bounds():
    with pytest.raises(ValidationError):
        GoalSpec(
            kind=GoalKind.FLIGHT,
            origin_city="AAA",
            dest_city="BBB",
            round_trip=False,
            passengers=9,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 10, 31),
        )
    with pytest.raises(ValidationError):
        GoalSpec(
            kind=GoalKind.STAY,
            city="AAA",
            nights=31,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 10, 31),
        )


def test_fr4_travel_window_must_lie_in_one_month():
    with pytest.raises(ValidationError):
        GoalSpec(
            kind=GoalKind.FLIGHT,
            origin_city="AAA",
            dest_city="BBB",
            round_trip=False,
            passengers=1,
            travel_window_start=date(2026, 10, 1),
            travel_window_end=date(2026, 11, 30),
        )


def test_fr4_cash_goal_forbids_trip_fields():
    with pytest.raises(ValidationError):
        GoalSpec(kind=GoalKind.CASH, origin_city="AAA")
    goal = GoalSpec(kind=GoalKind.CASH, cash_programs=["bank_a"], cash_max_points={"bank_a": 10})
    assert goal.travel_month is None
    assert goal.legs() == []


def test_fr7a_legs_are_one_for_one_way_and_two_for_round_trip():
    one_way = GoalSpec(
        kind=GoalKind.FLIGHT,
        origin_city="AAA",
        dest_city="BBB",
        round_trip=False,
        passengers=1,
        travel_window_start=date(2026, 10, 1),
        travel_window_end=date(2026, 10, 31),
    )
    assert one_way.legs() == [("AAA", "BBB")]
    assert one_way.travel_month == "2026-10"
    round_trip = one_way.model_copy(update={"round_trip": True})
    assert round_trip.legs() == [("AAA", "BBB"), ("BBB", "AAA")]


# -- FR-9 steps and plans --------------------------------------------------


def test_fr9_step_kind_specific_nullability():
    with pytest.raises(ValidationError):
        PlanStep(
            seq=1, kind=StepKind.TRANSFER, from_program="a", points_sent=1000, irreversible=True
        )
    with pytest.raises(ValidationError):
        PlanStep(
            seq=1,
            kind=StepKind.BOOK_AWARD,
            from_program="a",
            to_program="b",
            offer_id="o",
            points_sent=1,
            irreversible=True,
        )
    with pytest.raises(ValidationError):
        PlanStep(
            seq=1,
            kind=StepKind.BOOK_AWARD,
            from_program="a",
            offer_id="o",
            points_sent=1,
            irreversible=False,
        )
    with pytest.raises(ValidationError):
        PlanStep(
            seq=1,
            kind=StepKind.REDEEM_CASH,
            from_program="a",
            offer_id="o",
            points_sent=1,
            irreversible=False,
        )


def test_fr9_canonical_step_tuple_renders_nulls_as_empty_and_zero():
    step = PlanStep(
        seq=2,
        kind=StepKind.BOOK_AWARD,
        from_program="air_x",
        offer_id="x_out",
        points_sent=60000,
        fees_cents=25000,
        irreversible=True,
    )
    assert step.canonical_tuple() == ["book_award", "air_x", "", "", "x_out", "", 60000, 0, 25000]


def test_fr9_step_kind_rank_order():
    assert STEP_KIND_RANK[StepKind.TRANSFER] == 0
    assert STEP_KIND_RANK[StepKind.BOOK_AWARD] == 1
    assert STEP_KIND_RANK[StepKind.BOOK_PORTAL] == 2
    assert STEP_KIND_RANK[StepKind.REDEEM_CASH] == 3


def test_fr10_caveat_declaration_order_is_the_documented_order():
    assert [str(code) for code in CaveatCode] == [
        "irreversible_transfer",
        "transfer_time_risk",
        "stranded_points",
        "promo_expiring",
        "below_baseline",
        "stale_world",
        "seats_limited",
    ]


def test_fr9_negative_verdicts_must_carry_no_plans():
    plan = Plan(
        rank=1, gross_value_cents=0, cash_outlay_cents=0, points_cost_cents=0, net_value_cents=0
    )
    with pytest.raises(ValidationError):
        PlanSet(
            world_version="1.0.0",
            world_hash="a" * 64,
            today=date(2026, 7, 31),
            verdict=Verdict.NO_MATCHING_AWARD,
            plans=[plan],
        )
    empty = PlanSet(
        world_version="1.0.0",
        world_hash="a" * 64,
        today=date(2026, 7, 31),
        verdict=Verdict.INSUFFICIENT_POINTS,
    )
    assert empty.recommended() is None
    assert empty.disclaimer == DISCLAIMER


def test_fr9_plan_ranks_are_unique_within_a_set():
    plan = Plan(
        rank=1, gross_value_cents=0, cash_outlay_cents=0, points_cost_cents=0, net_value_cents=0
    )
    with pytest.raises(ValidationError):
        PlanSet(
            world_version="1.0.0",
            world_hash="a" * 64,
            today=date(2026, 7, 31),
            verdict=Verdict.BOOK_WITH_POINTS,
            plans=[plan, plan.model_copy()],
        )


def test_fr16_disclaimer_text_is_the_fixed_scope_string():
    assert "not financial advice" in DISCLAIMER
    assert "verify ratios and pricing" in DISCLAIMER


def test_caveat_is_frozen():
    caveat = Caveat(code=CaveatCode.STALE_WORLD, params={"days_old": 200}, text="old")
    with pytest.raises(ValidationError):
        caveat.text = "new"
