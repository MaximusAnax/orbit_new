"""FR-8 portfolio-delta accounting and FR-13 quick valuation.

Every expected number here is hand-computed in the test body, so a change in the
arithmetic rules fails loudly rather than silently re-baselining.
"""

from __future__ import annotations

from datetime import date

import pytest
from pointsmax.engine.goals import flight_goal, stay_goal
from pointsmax.engine.value import (
    Booking,
    TransferUse,
    award_flight_booking,
    award_stay_booking,
    booking_realized_cpp_milli,
    cash_booking,
    ending_holdings,
    portal_flight_booking,
    portal_stay_booking,
    value_breakdown,
    value_plan,
)
from pointsmax.engine.world import active_subgraph
from pointsmax.models import Cabin, StepKind

TODAY = date(2026, 7, 31)


def mcpp(world):
    return world.mcpp_map()


def test_fr8_scope_worked_example_reproduces_every_number(world):
    """DATA_MODEL's worked example, transposed onto the fixture world.

    130,000 bank_a @ 2000 mcpp funds two 60,000-point air_x awards worth
    $2,000 each with $250 of taxes; air_x opens and closes at 0.
    """
    opening = {"bank_a": 130000, "air_x": 0}
    transfers = (
        TransferUse(
            edge_id="bank_a__air_x",
            from_program="bank_a",
            to_program="air_x",
            sent=120000,
            delivered=120000,
            fee_cents=0,
            time_days=0,
        ),
    )
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=60000,
            fees_cents=25000,
            value_cents=200000,
            offer_id="x_out",
        ),
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=60000,
            fees_cents=25000,
            value_cents=200000,
            offer_id="x_back",
        ),
    )
    value = value_plan(opening, transfers, bookings, mcpp(world))

    assert value.gross_value_cents == 400000
    assert value.cash_outlay_cents == 50000
    # V(H0) - V(H1) reduces to the bank_a term: 260,000 - 20,000
    assert value.points_cost_cents == 240000
    assert value.net_value_cents == 400000 - 50000 - 240000 == 110000
    # pooled cpp: (400,000 - 50,000) x 1000 // 120,000
    assert value.realized_cpp_milli == 2916
    assert value.points_spent == {"bank_a": 120000}
    assert value.ending_holdings == {"bank_a": 10000, "air_x": 0}
    assert value.cash_received_cents is None


def test_fr8_pooled_cpp_differs_from_the_average_of_per_booking_cpps(world):
    """FR-8 / REVIEW D5: the plan-level figure is pooled, not averaged."""
    opening = {"air_x": 100000}
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=70000,
            fees_cents=0,
            value_cents=200000,
            offer_id="a",
        ),
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=30000,
            fees_cents=0,
            value_cents=30000,
            offer_id="b",
        ),
    )
    value = value_plan(opening, (), bookings, mcpp(world))
    per_booking = [booking_realized_cpp_milli(b) for b in bookings]
    assert per_booking == [2857, 1000]
    assert value.realized_cpp_milli == 230000 * 1000 // 100000 == 2300
    assert value.realized_cpp_milli != sum(per_booking) // 2


def test_fr8_stranded_leftovers_are_priced_at_the_destination_valuation(world):
    """Overshooting a transfer strands points at air_x's 1300 mcpp, not bank_a's 2000."""
    opening = {"bank_a": 100000, "air_x": 0}
    transfers = (
        TransferUse(
            edge_id="bank_a__air_x",
            from_program="bank_a",
            to_program="air_x",
            sent=61000,
            delivered=61000,
            fee_cents=0,
            time_days=0,
        ),
    )
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=60000,
            fees_cents=25000,
            value_cents=200000,
            offer_id="x_out",
        ),
    )
    value = value_plan(opening, transfers, bookings, mcpp(world))
    assert value.ending_holdings == {"bank_a": 39000, "air_x": 1000}
    # 100,000 x 2000 // 1000 = 200,000; end = 39,000 x 2 + 1,000 x 1.3 = 78,000 + 1,300
    assert value.points_cost_cents == 200000 - (78000 + 1300)
    assert value.points_spent == {"bank_a": 61000}


def test_fr8_tier_bonus_delivery_and_fee_cap_flow_into_net_value(world):
    """60,000 hotel_h at 3:1 + 5k tier bonus delivers 25,000 air_x."""
    opening = {"hotel_h": 60000, "air_x": 0}
    transfers = (
        TransferUse(
            edge_id="hotel_h__air_x",
            from_program="hotel_h",
            to_program="air_x",
            sent=60000,
            delivered=25000,
            fee_cents=0,
            time_days=2,
        ),
    )
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=25000,
            fees_cents=1000,
            value_cents=60000,
            offer_id="x",
        ),
    )
    value = value_plan(opening, transfers, bookings, mcpp(world))
    assert value.points_cost_cents == (60000 * 800) // 1000  # 48,000
    assert value.net_value_cents == 60000 - 1000 - 48000
    assert value.realized_cpp_milli == (60000 - 1000) * 1000 // 25000


def test_fr8_transfer_fees_are_cash_outlay_and_not_part_of_cpp(world):
    opening = {"bank_a": 200000, "air_y": 0}
    transfers = (
        TransferUse(
            edge_id="bank_a__air_y",
            from_program="bank_a",
            to_program="air_y",
            sent=70000,
            delivered=70000,
            fee_cents=4200,
            time_days=0,
        ),
    )
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_y",
            points=70000,
            fees_cents=9000,
            value_cents=200000,
            offer_id="y_out",
        ),
    )
    value = value_plan(opening, transfers, bookings, mcpp(world))
    assert value.cash_outlay_cents == 4200 + 9000
    # cpp excludes the transfer fee (documented hobby practice)
    assert value.realized_cpp_milli == (200000 - 9000) * 1000 // 70000


def test_fr8_cash_bookings_report_cash_received(world):
    option = world.cashout("a_credit")
    booking = cash_booking("bank_a", option, 100000)
    value = value_plan({"bank_a": 100000}, (), (booking,), mcpp(world))
    assert booking.value_cents == 100000
    assert value.cash_received_cents == 100000
    assert value.gross_value_cents == 100000
    assert value.points_cost_cents == 200000
    assert value.net_value_cents == -100000  # cashing out at 1.0 against a 2.0 baseline


def test_fr8_value_plan_rejects_a_plan_that_drives_a_balance_negative(world):
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=60000,
            fees_cents=0,
            value_cents=1,
            offer_id="x",
        ),
    )
    with pytest.raises(ValueError):
        value_plan({"air_x": 10}, (), bookings, mcpp(world))


def test_ending_holdings_applies_transfers_then_bookings():
    transfers = (
        TransferUse(
            edge_id="e",
            from_program="bank_a",
            to_program="air_x",
            sent=10000,
            delivered=10000,
            fee_cents=0,
            time_days=0,
        ),
    )
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=5000,
            fees_cents=0,
            value_cents=0,
            offer_id="x",
        ),
    )
    assert ending_holdings({"bank_a": 30000}, transfers, bookings) == {
        "bank_a": 20000,
        "air_x": 5000,
    }


# -- FR-8 booking pricing --------------------------------------------------


def test_fr8_award_flight_booking_prices_per_passenger_on_the_goal_month(world):
    goal = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=False,
        passengers=2,
    )
    booking = award_flight_booking(
        goal, world.offer("x_out"), world, origin_city="AAA", dest_city="BBB", round_trip=False
    )
    assert booking is not None
    assert booking.points == 120000
    assert booking.fees_cents == 50000
    assert booking.value_cents == 400000  # 2 x the 200,000-cent one-way fare
    assert booking.month == "2026-10"
    assert booking.deadline == date(2026, 10, 31)


def test_fr8_round_trip_award_uses_the_round_trip_fare_row(world):
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=True
    )
    booking = award_flight_booking(
        goal, world.offer("x_rt"), world, origin_city="AAA", dest_city="BBB", round_trip=True
    )
    assert booking is not None
    assert booking.value_cents == 380000  # not 2 x 200,000


def test_fr8_portal_booking_points_price_uses_the_option_rate(world):
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    booking = portal_flight_booking(
        goal,
        world.cashout("a_portal"),
        world,
        origin_city="AAA",
        dest_city="BBB",
        round_trip=False,
        cabin=Cabin.BUSINESS,
    )
    assert booking is not None
    assert booking.kind is StepKind.BOOK_PORTAL
    assert booking.points == 200000 * 1000 // 1500 + 1  # ceil(200,000,000 / 1500)
    assert booking.fees_cents == 0
    assert booking.value_cents == 200000


def test_fr8_stay_bookings_multiply_by_nights(world):
    goal = stay_goal(city="AAA", nights=4, month="2026-10")
    award = award_stay_booking(goal, world.offer("h_stay"), world)
    assert award is not None
    assert award.points == 80000
    assert award.value_cents == 120000  # 4 x 30,000-cent nightly fare
    portal = portal_stay_booking(goal, world.cashout("a_portal"), world)
    assert portal is not None
    assert portal.value_cents == 120000
    assert portal.points == 80000  # 120,000 cents at 1.5 cpp


def test_fr8_unpriceable_bookings_return_none(world):
    goal = flight_goal(
        origin_city="AAA", dest_city="CCC", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    assert (
        portal_flight_booking(
            goal,
            world.cashout("a_portal"),
            world,
            origin_city="AAA",
            dest_city="CCC",
            round_trip=False,
            cabin=Cabin.BUSINESS,
        )
        is None
    )


# -- FR-13 quick valuation -------------------------------------------------


def test_fr13_three_numbers_with_provenance(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    breakdown = value_breakdown(world, active, "bank_a", 210000)
    assert breakdown.baseline_value_cents == 420000  # 210,000 @ 2000 mcpp
    assert breakdown.baseline_cpp_milli == 2000
    assert breakdown.as_of == date(2026, 7, 1)
    # travel floor is the 1.5-cent portal; cash floor is the 1.0-cent credit
    assert breakdown.travel_floor_cents == 315000
    assert breakdown.travel_floor_option_id == "a_portal"
    assert breakdown.cash_floor_cents == 210000
    assert breakdown.cash_floor_option_id == "a_credit"


def test_fr13_the_portal_is_never_reported_as_cash(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    breakdown = value_breakdown(world, active, "bank_a", 100000)
    assert breakdown.cash_floor_option_id == "a_credit"
    assert breakdown.travel_floor_cents > breakdown.cash_floor_cents


def test_fr13_gift_cards_count_for_travel_floor_but_not_cash_floor(world):
    """gift_card is is_cash = false, so it can only raise the travel floor."""
    active = active_subgraph(world, [], TODAY)  # no card_a -> no portal
    breakdown = value_breakdown(world, active, "bank_a", 100000)
    assert breakdown.cash_floor_option_id == "a_credit"
    assert breakdown.cash_floor_cents == 100000
    assert breakdown.travel_floor_option_id == "a_gift"
    assert breakdown.travel_floor_cents == 120000


def test_fr13_floors_respect_quantization_and_value_the_remainder_at_zero(world):
    active = active_subgraph(world, [], TODAY)
    breakdown = value_breakdown(world, active, "bank_b", 1500)
    # b_deposit redeems in 1,000-point increments: only 1,000 points are liquid
    assert breakdown.cash_floor_cents == 800
    assert breakdown.baseline_value_cents == 2700


def test_fr13_no_active_option_means_a_zero_floor(world):
    active = active_subgraph(world, [], TODAY)
    breakdown = value_breakdown(world, active, "air_x", 50000)
    assert breakdown.cash_floor_cents == 0
    assert breakdown.cash_floor_option_id is None
    assert breakdown.travel_floor_cents == 0
    assert breakdown.baseline_value_cents == 65000


def test_fr13_rejects_negative_points(world):
    active = active_subgraph(world, [], TODAY)
    with pytest.raises(ValueError):
        value_breakdown(world, active, "bank_a", -1)
