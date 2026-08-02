"""FR-5 offline goal parsing (the rule-based parser behind gate M5)."""

from __future__ import annotations

from datetime import date

import pytest
from pointsmax.adapters.goal_parser import GoalParser, RuleBasedGoalParser
from pointsmax.engine.parser import parse_goal_text
from pointsmax.models import Cabin, GoalKind, GoalSpec, ParseError

TODAY = date(2026, 7, 31)


def parse(world, text, *, today=TODAY, home_city=None, default_passengers=1):
    return parse_goal_text(
        text,
        world=world,
        today=today,
        home_city=home_city,
        default_passengers=default_passengers,
    )


def test_fr5_parses_the_scope_example(world):
    goal = parse(world, "round-trip business Alfaville to Betatown in October")
    assert isinstance(goal, GoalSpec)
    assert goal.kind is GoalKind.FLIGHT
    assert (goal.origin_city, goal.dest_city) == ("AAA", "BBB")
    assert goal.cabin is Cabin.BUSINESS
    assert goal.round_trip is True
    assert goal.passengers == 1
    assert goal.travel_window_start == date(2026, 10, 1)
    assert goal.travel_window_end == date(2026, 10, 31)
    assert goal.raw_text == "round-trip business Alfaville to Betatown in October"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("AAA to BBB in October", ("AAA", "BBB")),
        ("alfaville to betatown in october", ("AAA", "BBB")),
        ("AAX to BBX in October", ("AAA", "BBB")),  # airport codes
        ("from Betatown to Alfaville in October", ("BBB", "AAA")),
    ],
)
def test_fr5_gazetteer_aliases_airports_and_codes_all_resolve(world, text, expected):
    goal = parse(world, text)
    assert isinstance(goal, GoalSpec)
    assert (goal.origin_city, goal.dest_city) == expected


@pytest.mark.parametrize(
    "text,cabin",
    [
        ("economy Alfaville to Betatown in October", Cabin.ECONOMY),
        ("coach Alfaville to Betatown in October", Cabin.ECONOMY),
        ("premium economy Alfaville to Betatown in October", Cabin.PREMIUM_ECONOMY),
        ("PE Alfaville to Betatown in October", Cabin.PREMIUM_ECONOMY),
        ("biz Alfaville to Betatown in October", Cabin.BUSINESS),
        ("business class Alfaville to Betatown in October", Cabin.BUSINESS),
        ("first class Alfaville to Betatown in October", Cabin.FIRST),
        ("Alfaville to Betatown in October", None),
    ],
)
def test_fr5_cabin_synonyms(world, text, cabin):
    goal = parse(world, text)
    assert isinstance(goal, GoalSpec)
    assert goal.cabin is cabin


@pytest.mark.parametrize(
    "text,round_trip",
    [
        ("round-trip Alfaville to Betatown in October", True),
        ("round trip Alfaville to Betatown in October", True),
        ("RT Alfaville to Betatown in October", True),
        ("Alfaville to Betatown in October with return", True),
        ("one way Alfaville to Betatown in October", False),
        ("one-way Alfaville to Betatown in October", False),
        ("Alfaville to Betatown in October", False),  # documented default
    ],
)
def test_fr5_trip_type_cues(world, text, round_trip):
    goal = parse(world, text)
    assert isinstance(goal, GoalSpec)
    assert goal.round_trip is round_trip


@pytest.mark.parametrize(
    "text,passengers",
    [
        ("Alfaville to Betatown in October for 2", 2),
        ("Alfaville to Betatown in October for two", 2),
        ("Alfaville to Betatown in October, 3 people", 3),
        ("Alfaville to Betatown in October for 4 passengers", 4),
        ("Alfaville to Betatown in October", 1),
    ],
)
def test_fr5_passenger_counts(world, text, passengers):
    goal = parse(world, text)
    assert isinstance(goal, GoalSpec)
    assert goal.passengers == passengers


def test_fr5_passenger_default_comes_from_the_profile(world):
    goal = parse(world, "Alfaville to Betatown in October", default_passengers=2)
    assert isinstance(goal, GoalSpec)
    assert goal.passengers == 2


@pytest.mark.parametrize(
    "text,today,month",
    [
        ("Alfaville to Betatown in October", date(2026, 7, 31), "2026-10"),
        ("Alfaville to Betatown in March", date(2026, 7, 31), "2027-03"),
        ("Alfaville to Betatown in July", date(2026, 7, 31), "2026-07"),
        ("Alfaville to Betatown in October", date(2026, 11, 2), "2027-10"),
        ("Alfaville to Betatown in October 2028", date(2026, 7, 31), "2028-10"),
        ("Alfaville to Betatown 2027-01", date(2026, 7, 31), "2027-01"),
        ("Alfaville to Betatown in Dec", date(2026, 7, 31), "2026-12"),
    ],
)
def test_fr5_months_resolve_to_the_next_occurrence(world, text, today, month):
    goal = parse(world, text, today=today)
    assert isinstance(goal, GoalSpec)
    assert goal.travel_month == month


@pytest.mark.parametrize(
    "text",
    [
        "turn everything into cash",
        "cash out my points",
        "I want a statement credit",
        "maximize cash",
    ],
)
def test_fr5_cash_intent(world, text):
    goal = parse(world, text)
    assert isinstance(goal, GoalSpec)
    assert goal.kind is GoalKind.CASH
    assert goal.cash_programs is None  # FR-5 does not extract a program filter


@pytest.mark.parametrize(
    "text,nights",
    [
        ("hotel in Alfaville for 5 nights in October", 5),
        ("a week at a hotel in Alfaville in October", 7),
        ("two weeks at a hotel in Alfaville in October", 14),
        ("hotel in Alfaville, three nights in October", 3),
    ],
)
def test_fr5_stay_intent_and_nights(world, text, nights):
    goal = parse(world, text)
    assert isinstance(goal, GoalSpec)
    assert goal.kind is GoalKind.STAY
    assert goal.city == "AAA"
    assert goal.nights == nights
    assert goal.travel_month == "2026-10"


def test_fr5_night_counts_are_not_read_as_passengers(world):
    goal = parse(world, "hotel in Alfaville for 5 nights in October")
    assert isinstance(goal, GoalSpec)
    assert goal.passengers is None


def test_fr5_missing_origin_falls_back_to_the_profile_home_city(world):
    goal = parse(world, "business to Betatown in October", home_city="AAA")
    assert isinstance(goal, GoalSpec)
    assert goal.origin_city == "AAA"
    assert goal.dest_city == "BBB"


def test_fr5_missing_origin_without_a_home_city_is_a_parse_error(world):
    result = parse(world, "business to Betatown in October")
    assert isinstance(result, ParseError)
    assert result.missing == ["origin_city"]
    assert "origin_city" in result.message


@pytest.mark.parametrize(
    "text,missing",
    [
        ("business class somewhere warm", ["dest_city", "origin_city", "travel_month"]),
        ("next spring", ["dest_city", "origin_city", "travel_month"]),
        ("Alfaville to Betatown", ["travel_month"]),
    ],
)
def test_fr5_negative_cases_name_the_missing_fields(world, text, missing):
    result = parse(world, text)
    assert isinstance(result, ParseError)
    assert result.missing == missing


def test_fr5_ambiguous_city_lists_are_reported(world):
    result = parse(world, "Alfaville to Betatown or Gammaport in October")
    assert isinstance(result, ParseError)
    assert result.ambiguous == ["cities"]


def test_fr5_same_city_both_ends_is_rejected(world):
    """A home-city default that equals the destination is ambiguous, not a goal."""
    result = parse(world, "business to Alfaville in October", home_city="AAA")
    assert isinstance(result, ParseError)
    assert result.ambiguous == ["cities"]


def test_fr5_repeated_mentions_of_one_city_collapse(world):
    result = parse(world, "Alfaville to AAX in October")
    assert isinstance(result, ParseError)
    assert result.missing == ["origin_city"]


def test_fr5_empty_text_is_a_parse_error(world):
    result = parse(world, "   ")
    assert isinstance(result, ParseError)
    assert result.missing == ["kind"]


def test_fr5_is_deterministic_and_clock_free(world):
    text = "round-trip business Alfaville to Betatown in October for 2"
    first = parse(world, text)
    second = parse(world, text)
    assert first == second


def test_fr5_adapter_satisfies_the_protocol_and_delegates(world):
    parser = RuleBasedGoalParser(world)
    assert isinstance(parser, GoalParser)
    direct = parse(world, "Alfaville to Betatown in October", home_city="AAA")
    through = parser.parse("Alfaville to Betatown in October", today=TODAY, home_city="AAA")
    assert direct == through
