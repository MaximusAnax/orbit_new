"""ISO-week arithmetic (every quantity in the product is weekly, SCOPE D-16)."""

from __future__ import annotations

import pytest
from grailtrader.weeks import (
    add_weeks,
    parse_datetime,
    week_end_exclusive,
    week_key,
    week_range,
    week_start,
    weeks_between,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-27", "2026-07-27"),  # a Monday
        ("2026-07-31", "2026-07-27"),  # the Friday of the same week
        ("2026-08-02T23:59:59Z", "2026-07-27"),  # the Sunday of the same week
        ("2026-08-03", "2026-08-03"),  # the next Monday
    ],
)
def test_week_key_snaps_to_the_iso_monday(value, expected):
    assert week_key(value) == expected


def test_add_weeks_and_weeks_between_are_inverses():
    assert add_weeks("2026-07-27", 3) == "2026-08-17"
    assert add_weeks("2026-07-27", -3) == "2026-07-06"
    assert weeks_between("2026-07-27", "2026-08-17") == 3
    assert weeks_between("2026-08-17", "2026-07-27") == -3
    assert weeks_between("2026-07-27", "2026-07-27") == 0


def test_week_range_is_inclusive_and_empty_when_reversed():
    assert week_range("2026-07-27", "2026-08-10") == ["2026-07-27", "2026-08-03", "2026-08-10"]
    assert week_range("2026-08-10", "2026-07-27") == []


def test_week_end_is_the_following_monday_midnight_utc():
    end = week_end_exclusive("2026-07-27")
    assert end.isoformat() == "2026-08-03T00:00:00+00:00"
    assert parse_datetime("2026-08-02T23:59:59Z") < end


def test_timestamps_are_normalised_to_utc():
    assert parse_datetime("2026-07-27T02:00:00+02:00").isoformat() == "2026-07-27T00:00:00+00:00"
    assert parse_datetime("2026-07-27").isoformat() == "2026-07-27T00:00:00+00:00"
    assert week_start("2026-07-29").isoformat() == "2026-07-27"
