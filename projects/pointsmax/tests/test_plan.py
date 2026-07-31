"""FR-9 canonical order / ranking and FR-10 explanations and caveats."""

from __future__ import annotations

import hashlib
import re
from datetime import date

from conftest import make_world, world_files
from pointsmax.engine.goals import cash_goal, flight_goal
from pointsmax.engine.plan import (
    PlanDraft,
    build_caveats,
    build_plan,
    build_steps,
    canonical_form_of,
    choose_verdict,
    draft_sort_key,
    format_cents,
    format_cpp,
    format_points,
    plan_signature,
)
from pointsmax.engine.value import Booking, TransferUse, value_plan
from pointsmax.models import (
    CAVEAT_ORDER,
    Cabin,
    CaveatCode,
    GoalKind,
    PlanParams,
    StepKind,
    Verdict,
    canonical_json,
)

TODAY = date(2026, 7, 31)
PARAMS = PlanParams()


def flight():
    return flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", round_trip=True, passengers=1
    )


def award(
    program_id,
    offer_id,
    points,
    *,
    fees=25000,
    value=200000,
    deadline=None,
    seats=None,
    passengers=1,
):
    return Booking(
        kind=StepKind.BOOK_AWARD,
        program_id=program_id,
        points=points,
        fees_cents=fees,
        value_cents=value,
        offer_id=offer_id,
        deadline=deadline,
        seats_available=seats,
        passengers=passengers,
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
    )


def transfer(edge_id, source, target, sent, delivered, *, fee=0, days=0):
    return TransferUse(
        edge_id=edge_id,
        from_program=source,
        to_program=target,
        sent=sent,
        delivered=delivered,
        fee_cents=fee,
        time_days=days,
    )


def draft(world, transfers, bookings, opening, *, arrival=None, hop=None):
    value = value_plan(opening, transfers, bookings, world.mcpp_map())
    return PlanDraft(
        bookings=bookings,
        transfers=transfers,
        value=value,
        arrival_days=arrival or {},
        hop_index=hop or {},
        feasible_in_days=max(arrival.values()) if arrival else 0,
    )


# -- FR-9 canonical step order --------------------------------------------


def test_fr9_transfers_sort_before_bookings_and_seq_follows_the_order(world):
    transfers = (
        transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),
        transfer("hotel_h__air_x", "hotel_h", "air_x", 60000, 25000, days=2),
    )
    bookings = (award("air_x", "x_out", 60000), award("air_x", "x_back", 25000))
    steps = build_steps(world, bookings, transfers, {"hotel_h__air_x": 1})
    assert [s.kind for s in steps] == [
        StepKind.TRANSFER,
        StepKind.TRANSFER,
        StepKind.BOOK_AWARD,
        StepKind.BOOK_AWARD,
    ]
    assert [s.seq for s in steps] == [1, 2, 3, 4]
    # hop_index 0 sorts before hop_index 1
    assert steps[0].edge_id == "bank_a__air_x"
    assert steps[1].edge_id == "hotel_h__air_x"
    # bookings sort by offer id
    assert [s.offer_id for s in steps[2:]] == ["x_back", "x_out"]


def test_fr9_step_kind_rank_orders_portal_and_cash_after_awards(world):
    bookings = (
        Booking(
            kind=StepKind.REDEEM_CASH,
            program_id="bank_a",
            points=1000,
            fees_cents=0,
            value_cents=10,
            cashout_id="a_credit",
        ),
        Booking(
            kind=StepKind.BOOK_PORTAL,
            program_id="bank_a",
            points=2000,
            fees_cents=0,
            value_cents=30,
            cashout_id="a_portal",
        ),
        award("air_x", "x_out", 60000),
    )
    steps = build_steps(world, bookings, (), {})
    assert [s.kind for s in steps] == [
        StepKind.BOOK_AWARD,
        StepKind.BOOK_PORTAL,
        StepKind.REDEEM_CASH,
    ]


def test_fr9_signature_is_the_sha256_of_the_canonical_form(world):
    steps = build_steps(world, (award("air_x", "x_out", 60000),), (), {})
    canonical = [s.canonical_tuple() for s in steps]
    expected = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    assert plan_signature(steps) == expected


def test_fr9_signature_is_stable_across_rebuilds(world):
    bookings = (award("air_x", "x_out", 60000),)
    a = plan_signature(build_steps(world, bookings, (), {}))
    b = plan_signature(build_steps(world, bookings, (), {}))
    assert a == b


def test_fr9_sort_key_is_a_total_order_over_the_documented_fields(world):
    opening = {"bank_a": 200000}
    cheap = draft(
        world,
        (transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),),
        (award("air_x", "x_out", 60000),),
        opening,
    )
    dear = draft(
        world,
        (transfer("bank_a__air_x", "bank_a", "air_x", 61000, 61000),),
        (award("air_x", "x_out", 60000),),
        opening,
    )
    keys = [
        draft_sort_key(d, GoalKind.FLIGHT, canonical_form_of(world, d)[1]) for d in (cheap, dear)
    ]
    assert keys[0] < keys[1]  # higher net value ranks first
    assert keys[0][0] == -cheap.value.net_value_cents


def test_fr9_ties_break_on_fewer_points_then_steps_then_canonical_form(world):
    opening = {"air_x": 200000}
    one_step = draft(world, (), (award("air_x", "x_out", 60000, fees=0),), opening)
    two_steps = draft(
        world,
        (),
        (
            award("air_x", "x_out", 30000, fees=0, value=100000),
            award("air_x", "x_back", 30000, fees=0, value=100000),
        ),
        opening,
    )
    a = draft_sort_key(one_step, GoalKind.FLIGHT, canonical_form_of(world, one_step)[1])
    b = draft_sort_key(two_steps, GoalKind.FLIGHT, canonical_form_of(world, two_steps)[1])
    assert a[:2] == b[:2]  # same objective, same points spent
    assert a[2] < b[2]  # fewer steps wins
    assert a < b


def test_fr9_cash_goals_rank_by_cash_received(world):
    option = world.cashout("a_credit")
    from pointsmax.engine.value import cash_booking

    more = draft(world, (), (cash_booking("bank_a", option, 100000),), {"bank_a": 100000})
    less = draft(world, (), (cash_booking("bank_a", option, 50000),), {"bank_a": 100000})
    a = draft_sort_key(more, GoalKind.CASH, canonical_form_of(world, more)[1])
    b = draft_sort_key(less, GoalKind.CASH, canonical_form_of(world, less)[1])
    assert a[0] == -100000
    assert a < b


# -- FR-9 verdicts ---------------------------------------------------------


def test_fr9_verdicts_cover_every_branch(world):
    opening = {"bank_a": 200000}
    positive = draft(
        world,
        (transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),),
        (award("air_x", "x_out", 60000),),
        opening,
    )
    negative = draft(
        world,
        (transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),),
        (award("air_x", "x_out", 60000, value=1000),),
        opening,
    )

    assert choose_verdict(GoalKind.FLIGHT, candidate_sets=0, feasible_drafts=[]) is (
        Verdict.NO_MATCHING_AWARD
    )
    assert choose_verdict(GoalKind.FLIGHT, candidate_sets=3, feasible_drafts=[]) is (
        Verdict.INSUFFICIENT_POINTS
    )
    assert choose_verdict(GoalKind.FLIGHT, candidate_sets=3, feasible_drafts=[positive]) is (
        Verdict.BOOK_WITH_POINTS
    )
    assert choose_verdict(GoalKind.FLIGHT, candidate_sets=3, feasible_drafts=[negative]) is (
        Verdict.PAY_CASH_KEEP_POINTS
    )
    assert choose_verdict(GoalKind.CASH, candidate_sets=1, feasible_drafts=[positive]) is (
        Verdict.CASH_PLAN
    )


# -- FR-10 explanations ----------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def stored_numbers(world, step) -> set[str]:
    """Numbers a step's explanation may legitimately contain.

    FR-10 requires every number in the text to equal a stored field.  Ratios and
    the travel month come from world data (they are not plan arithmetic), so they
    are allowed alongside the step's own stored values.
    """
    values = {
        format_points(step.points_sent),
        str(step.points_sent),
        str(step.eta_days),
        format_cents(step.fees_cents).lstrip("$"),
    }
    if step.points_delivered is not None:
        values |= {format_points(step.points_delivered), str(step.points_delivered)}
    if step.edge_id:
        edge = world.edge(step.edge_id)
        values |= {str(edge.ratio_from), str(edge.ratio_to)}
    values |= {"2026-10", "2026", "10"}
    return values


def test_fr10_every_number_in_a_step_explanation_equals_a_stored_field(world):
    transfers = (
        transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),
        transfer("hotel_h__air_x", "hotel_h", "air_x", 60000, 25000, fee=1234, days=2),
    )
    bookings = (award("air_x", "x_out", 60000, fees=25000),)
    steps = build_steps(world, bookings, transfers, {"hotel_h__air_x": 1})
    for step in steps:
        allowed = stored_numbers(world, step)
        for raw in _NUMBER.findall(step.explanation):
            token = raw.rstrip(",.")
            assert token in allowed, f"{token!r} in {step.explanation!r} is not a stored field"


def test_fr10_transfer_explanation_names_ratio_timing_and_fee(world):
    steps = build_steps(
        world,
        (),
        (transfer("hotel_h__air_x", "hotel_h", "air_x", 60000, 25000, days=2),),
        {"hotel_h__air_x": 0},
    )
    text = steps[0].explanation
    assert "Transfer 60,000 Hotel H Points to Air X Miles" in text
    assert "3:1" in text and "2 days" in text and "no fee" in text
    assert "25,000 points land" in text


def test_fr10_instant_and_fee_bearing_transfers_read_correctly(world):
    steps = build_steps(
        world,
        (),
        (transfer("bank_a__air_y", "bank_a", "air_y", 70000, 70000, fee=4200),),
        {},
    )
    assert "instant" in steps[0].explanation
    assert "$42.00 fee" in steps[0].explanation


def test_fr10_booking_explanations_describe_the_purchase(world):
    steps = build_steps(world, (award("air_x", "x_out", 60000),), (), {})
    assert steps[0].explanation.startswith("Book Air X Miles business AAA->BBB (2026-10)")
    assert "60,000 points plus $250.00 in taxes and fees" in steps[0].explanation


def test_format_helpers_avoid_floats():
    assert format_points(1234567) == "1,234,567"
    assert format_cents(129700) == "$1,297.00"
    assert format_cents(-500) == "-$5.00"
    assert format_cpp(2050) == "2.05"
    assert format_cpp(600) == "0.60"


# -- FR-10 caveats ---------------------------------------------------------


def test_fr10_irreversible_transfer_fires_once_per_transfer_step(world):
    transfers = (
        transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),
        transfer("hotel_h__air_x", "hotel_h", "air_x", 3000, 1000),
    )
    plan = draft(
        world, transfers, (award("air_x", "x_out", 61000),), {"bank_a": 100000, "hotel_h": 100000}
    )
    caveats = build_caveats(world, flight(), plan, TODAY, PARAMS)
    fired = [c for c in caveats if c.code is CaveatCode.IRREVERSIBLE_TRANSFER]
    assert len(fired) == 2
    assert {c.params["edge_id"] for c in fired} == {"bank_a__air_x", "hotel_h__air_x"}
    assert fired[0].params["points"] in (60000, 3000)


def test_fr10_stranded_points_price_the_leftover_at_the_destination_valuation(world):
    transfers = (transfer("bank_a__air_x", "bank_a", "air_x", 61000, 61000),)
    plan = draft(world, transfers, (award("air_x", "x_out", 60000),), {"bank_a": 100000})
    caveats = build_caveats(world, flight(), plan, TODAY, PARAMS)
    stranded = next(c for c in caveats if c.code is CaveatCode.STRANDED_POINTS)
    assert stranded.params == {"program": "air_x", "points": 1000, "value_cents": 1300}
    assert "$13.00" in stranded.text


def test_fr10_no_stranding_caveat_when_the_destination_ends_at_zero(world):
    transfers = (transfer("bank_a__air_x", "bank_a", "air_x", 60000, 60000),)
    plan = draft(world, transfers, (award("air_x", "x_out", 60000),), {"bank_a": 100000})
    caveats = build_caveats(world, flight(), plan, TODAY, PARAMS)
    assert not [c for c in caveats if c.code is CaveatCode.STRANDED_POINTS]


def test_fr10_transfer_time_risk_uses_the_slack_threshold(world):
    transfers = (transfer("bank_b__air_x", "bank_b", "air_x", 60000, 60000, days=2),)
    bookings = (award("air_x", "x_out", 60000, deadline=date(2026, 8, 4)),)
    plan = draft(world, transfers, bookings, {"bank_b": 100000}, arrival={"bank_b": 0, "air_x": 2})
    caveats = build_caveats(world, flight(), plan, TODAY, PARAMS)
    risk = next(c for c in caveats if c.code is CaveatCode.TRANSFER_TIME_RISK)
    assert risk.params == {
        "program": "air_x",
        "arrival_days": 2,
        "deadline": "2026-08-04",
        "slack_days": 2,
    }

    roomy = draft(
        world,
        transfers,
        (award("air_x", "x_out", 60000, deadline=date(2026, 10, 31)),),
        {"bank_b": 100000},
        arrival={"bank_b": 0, "air_x": 2},
    )
    assert not [
        c
        for c in build_caveats(world, flight(), roomy, TODAY, PARAMS)
        if c.code is CaveatCode.TRANSFER_TIME_RISK
    ]


def test_fr10_promo_expiring_fires_inside_the_window(world):
    transfers = (transfer("bank_b__air_y_promo", "bank_b", "air_y", 60000, 60000),)
    plan = draft(world, transfers, (award("air_y", "y_out", 60000),), {"bank_b": 100000})
    caveats = build_caveats(world, flight(), plan, TODAY, PARAMS)
    promo = next(c for c in caveats if c.code is CaveatCode.PROMO_EXPIRING)
    assert promo.params == {
        "edge_id": "bank_b__air_y_promo",
        "valid_to": "2026-08-10",
        "days_left": 10,
    }
    far = build_caveats(world, flight(), plan, date(2026, 7, 1), PlanParams(promo_days=5))
    assert not [c for c in far if c.code is CaveatCode.PROMO_EXPIRING]


def test_fr10_below_baseline_fires_on_a_cash_out_under_the_valuation(world):
    from pointsmax.engine.value import cash_booking

    booking = cash_booking("bank_a", world.cashout("a_credit"), 100000)
    plan = draft(world, (), (booking,), {"bank_a": 100000})
    caveats = build_caveats(world, cash_goal(), plan, TODAY, PARAMS)
    below = next(c for c in caveats if c.code is CaveatCode.BELOW_BASELINE)
    assert below.params == {
        "program": "bank_a",
        "option_id": "a_credit",
        "option_cpp_milli": 1000,
        "baseline_cpp_milli": 2000,
    }
    assert "1.00 cents per point" in below.text


def test_fr10_stale_world_fires_when_valuations_age_out(world):
    plan = draft(world, (), (award("air_x", "x_out", 60000),), {"air_x": 100000})
    fresh = build_caveats(world, flight(), plan, TODAY, PARAMS)
    assert not [c for c in fresh if c.code is CaveatCode.STALE_WORLD]

    late = date(2027, 6, 1)  # 335 days after the 2026-07-01 valuation date
    stale = build_caveats(world, flight(), plan, late, PARAMS)
    caveat = next(c for c in stale if c.code is CaveatCode.STALE_WORLD)
    assert caveat.params == {"as_of": "2026-07-01", "days_old": 335}


def test_fr10_seats_limited_uses_the_documented_threshold(world):
    tight = draft(world, (), (award("air_x", "x_out", 60000, seats=2),), {"air_x": 100000})
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", round_trip=False, passengers=1
    )
    caveat = next(
        c
        for c in build_caveats(world, goal, tight, TODAY, PARAMS)
        if c.code is CaveatCode.SEATS_LIMITED
    )
    assert caveat.params == {"offer_id": "x_out", "seats_available": 2, "passengers": 1}

    roomy = draft(world, (), (award("air_x", "x_out", 60000, seats=6),), {"air_x": 100000})
    assert not [
        c
        for c in build_caveats(world, goal, roomy, TODAY, PARAMS)
        if c.code is CaveatCode.SEATS_LIMITED
    ]


def test_fr10_caveats_are_ordered_by_declaration_then_params(world):
    transfers = (transfer("bank_b__air_y_promo", "bank_b", "air_y", 61000, 61000),)
    bookings = (award("air_y", "y_out", 60000, seats=2, deadline=date(2026, 8, 1)),)
    plan = draft(world, transfers, bookings, {"bank_b": 100000}, arrival={"bank_b": 0, "air_y": 0})
    caveats = build_caveats(world, flight(), plan, TODAY, PARAMS)
    keys = [(CAVEAT_ORDER[c.code], canonical_json(c.params)) for c in caveats]
    assert keys == sorted(keys)
    assert {c.code for c in caveats} >= {
        CaveatCode.IRREVERSIBLE_TRANSFER,
        CaveatCode.TRANSFER_TIME_RISK,
        CaveatCode.STRANDED_POINTS,
        CaveatCode.PROMO_EXPIRING,
        CaveatCode.SEATS_LIMITED,
    }


def test_fr10_caveat_thresholds_are_inputs_not_constants(world):
    plan = draft(world, (), (award("air_x", "x_out", 60000),), {"air_x": 100000})
    lenient = build_caveats(world, flight(), plan, date(2027, 1, 1), PlanParams(stale_days=365))
    strict = build_caveats(world, flight(), plan, date(2027, 1, 1), PlanParams(stale_days=30))
    assert not [c for c in lenient if c.code is CaveatCode.STALE_WORLD]
    assert [c for c in strict if c.code is CaveatCode.STALE_WORLD]


# -- FR-9 plan assembly ----------------------------------------------------


def test_fr9_build_plan_materialises_every_documented_field(world):
    transfers = (transfer("bank_a__air_x", "bank_a", "air_x", 120000, 120000),)
    bookings = (award("air_x", "x_out", 60000), award("air_x", "x_back", 60000))
    plan_draft = draft(world, transfers, bookings, {"bank_a": 130000, "air_x": 0})
    plan = build_plan(world, flight(), plan_draft, TODAY, PARAMS, rank=1)

    assert plan.rank == 1
    assert plan.is_comparator is False
    assert plan.gross_value_cents == 400000
    assert plan.cash_outlay_cents == 50000
    assert plan.points_cost_cents == 240000
    assert plan.net_value_cents == 110000
    assert plan.realized_cpp_milli == 2916
    assert plan.points_spent == {"bank_a": 120000}
    assert len(plan.signature) == 64
    assert [s.seq for s in plan.steps] == [1, 2, 3]
    assert plan.canonical_form() == [s.canonical_tuple() for s in plan.steps]


def test_fr9_portal_only_plans_are_flagged_as_the_comparator_family(world):
    portal = Booking(
        kind=StepKind.BOOK_PORTAL,
        program_id="bank_a",
        points=140000,
        fees_cents=0,
        value_cents=200000,
        cashout_id="a_portal",
    )
    portal_only = draft(world, (), (portal,), {"bank_a": 200000})
    assert portal_only.is_portal_only() is True

    mixed = draft(world, (), (portal, award("air_x", "x_out", 1)), {"bank_a": 200000, "air_x": 1})
    assert mixed.is_portal_only() is False


def test_fr10_stale_world_relative_to_a_world_with_older_valuations():
    files = world_files()
    for valuation in files["valuations.json"]:
        valuation["as_of"] = "2025-01-01"
    old_world = make_world(files)
    plan = draft(old_world, (), (award("air_x", "x_out", 60000),), {"air_x": 100000})
    caveats = build_caveats(old_world, flight(), plan, TODAY, PARAMS)
    assert any(c.code is CaveatCode.STALE_WORLD for c in caveats)
