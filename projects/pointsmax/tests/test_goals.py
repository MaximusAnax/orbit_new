"""FR-4 goal construction and FR-6 offer matching."""

from __future__ import annotations

from datetime import date

import pytest
from pointsmax.engine.goals import (
    cash_goal,
    flight_goal,
    matching_flight_offers,
    matching_stay_offers,
    month_bounds,
    offer_deadline,
    overlap_days,
    stay_goal,
)
from pointsmax.models import Cabin, GoalKind

TODAY = date(2026, 7, 31)


def test_fr4_month_bounds_cover_the_whole_month():
    assert month_bounds("2026-10") == (date(2026, 10, 1), date(2026, 10, 31))
    assert month_bounds("2027-02") == (date(2027, 2, 1), date(2027, 2, 28))
    assert month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))
    with pytest.raises(ValueError):
        month_bounds("2026-13")


def test_fr4_builders_produce_validated_specs():
    flight = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=True,
        passengers=2,
    )
    assert flight.kind is GoalKind.FLIGHT
    assert flight.travel_month == "2026-10"
    stay = stay_goal(city="AAA", nights=5, month="2026-10")
    assert stay.nights == 5
    cash = cash_goal(programs=["bank_a"], max_points={"bank_a": 50000})
    assert cash.cash_programs == ["bank_a"]


def test_overlap_days_counts_inclusive_days():
    assert (
        overlap_days(date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 20), date(2026, 11, 5))
        == 12
    )
    assert (
        overlap_days(date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 9))
        == 0
    )


# -- FR-6 flight matching --------------------------------------------------


def test_fr6_matches_the_requested_direction_only(world):
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    out = matching_flight_offers(
        goal, world, TODAY, origin_city="AAA", dest_city="BBB", round_trip=False
    )
    assert [o.id for o in out] == ["x_out", "x_soon"]
    back = matching_flight_offers(
        goal, world, TODAY, origin_city="BBB", dest_city="AAA", round_trip=False
    )
    assert [o.id for o in back] == ["x_back"]


def test_fr6_round_trip_offers_are_matched_separately(world):
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=True
    )
    rt = matching_flight_offers(
        goal, world, TODAY, origin_city="AAA", dest_city="BBB", round_trip=True
    )
    assert [o.id for o in rt] == ["x_rt"]


def test_fr6_null_cabin_matches_any_cabin(world):
    any_cabin = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=None, round_trip=False
    )
    ids = {
        o.id
        for o in matching_flight_offers(
            any_cabin, world, TODAY, origin_city="AAA", dest_city="BBB", round_trip=False
        )
    }
    assert {"x_out", "y_out", "x_soon"} <= ids

    economy = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.ECONOMY, round_trip=False
    )
    assert (
        matching_flight_offers(
            economy, world, TODAY, origin_city="AAA", dest_city="BBB", round_trip=False
        )
        == []
    )


def test_fr6_travel_windows_must_overlap(world):
    november = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-11", cabin=Cabin.BUSINESS, round_trip=False
    )
    assert (
        matching_flight_offers(
            november, world, TODAY, origin_city="AAA", dest_city="BBB", round_trip=False
        )
        == []
    )


def test_fr6_seat_caps_exclude_offers_below_the_party_size(world):
    goal = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=False,
        passengers=3,
    )
    ids = {
        o.id
        for o in matching_flight_offers(
            goal, world, TODAY, origin_city="AAA", dest_city="BBB", round_trip=False
        )
    }
    assert ids == {"x_out"}  # x_soon caps at 2 seats


def test_fr6_bookable_until_excludes_expired_offers(world):
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    later = matching_flight_offers(
        goal, world, date(2026, 8, 2), origin_city="AAA", dest_city="BBB", round_trip=False
    )
    assert [o.id for o in later] == ["x_out"]


def test_fr6_offers_resolve_airport_codes_through_the_gazetteer(world):
    files_offer = world.offer("x_out")
    assert world.city_of(files_offer.origin) == "AAA"
    assert world.city_of("AAX") == "AAA"
    assert world.city_of("ZZZ") is None


# -- FR-6 stay matching ----------------------------------------------------


def test_fr6_stay_needs_an_overlap_of_at_least_nights(world):
    short = stay_goal(city="AAA", nights=5, month="2026-10")
    assert [o.id for o in matching_stay_offers(short, world, TODAY)] == ["h_stay"]
    too_long = stay_goal(city="AAA", nights=30, month="2026-10")
    assert [o.id for o in matching_stay_offers(too_long, world, TODAY)] == ["h_stay"]
    wrong_city = stay_goal(city="BBB", nights=2, month="2026-10")
    assert matching_stay_offers(wrong_city, world, TODAY) == []


# -- FR-7d deadlines -------------------------------------------------------


def test_fr7d_deadline_is_the_minimum_of_the_non_null_bounds(world):
    goal = flight_goal(
        origin_city="AAA",
        dest_city="BBB",
        month="2026-10",
        cabin=Cabin.BUSINESS,
        round_trip=False,
        book_by=date(2026, 9, 1),
    )
    assert offer_deadline(goal, world.offer("x_out")) == date(2026, 9, 1)
    assert offer_deadline(goal, world.offer("x_soon")) == date(2026, 8, 1)

    no_book_by = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    assert offer_deadline(no_book_by, world.offer("x_out")) == date(2026, 10, 31)
    # Portal bookings (no offer) fall back to the goal's own window end.
    assert offer_deadline(no_book_by, None) == date(2026, 10, 31)


def test_fr7d_travel_window_start_does_not_bind(world):
    """Booking after travel has begun is still valid for later dates."""
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    deadline = offer_deadline(goal, world.offer("x_out"))
    assert deadline == world.offer("x_out").travel_window_end
    assert deadline > world.offer("x_out").travel_window_start
