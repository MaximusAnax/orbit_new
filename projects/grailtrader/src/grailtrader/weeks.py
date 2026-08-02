"""ISO-week arithmetic.

Every quantity in GrailTrader is computed at ISO-week grain (SCOPE D-16). A week
is identified by the ISO date of its Monday (``"2026-07-27"``). Nothing in this
module reads the clock: callers always supply the timestamps (FR-14).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

__all__ = [
    "add_weeks",
    "parse_date",
    "parse_datetime",
    "week_end_exclusive",
    "week_key",
    "week_range",
    "week_start",
    "weeks_between",
]


def parse_datetime(value: str | datetime | date) -> datetime:
    """Parse an ISO-8601 timestamp (or bare date) into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_date(value: str | datetime | date) -> date:
    """Parse an ISO-8601 date or timestamp into a UTC calendar date."""
    if isinstance(value, datetime):
        return parse_datetime(value).date()
    if isinstance(value, date):
        return value
    return parse_datetime(value).date()


@lru_cache(maxsize=16384)
def _week_start_of_text(value: str) -> date:
    d = parse_date(value)
    return d - timedelta(days=d.weekday())


def week_start(value: str | datetime | date) -> date:
    """Return the Monday of the ISO week containing ``value``.

    Week keys are re-derived from the same handful of date strings millions of
    times during a replay, so the string path is memoised. The function stays
    pure: the cache is keyed on the exact input text.
    """
    if isinstance(value, str):
        return _week_start_of_text(value)
    d = parse_date(value)
    return d - timedelta(days=d.weekday())


def week_key(value: str | datetime | date) -> str:
    """Return the canonical week key (ISO date of the week's Monday)."""
    return week_start(value).isoformat()


def week_end_exclusive(week: str) -> datetime:
    """Return the exclusive upper bound of a week: the following Monday, 00:00 UTC."""
    monday = week_start(week)
    return datetime(monday.year, monday.month, monday.day, tzinfo=UTC) + timedelta(days=7)


def add_weeks(week: str, n: int) -> str:
    """Shift a week key by ``n`` whole weeks (negative shifts backwards)."""
    return (week_start(week) + timedelta(weeks=n)).isoformat()


def weeks_between(earlier: str, later: str) -> int:
    """Whole weeks from ``earlier`` to ``later``; negative when ``later`` precedes it."""
    delta = week_start(later) - week_start(earlier)
    return delta.days // 7


def week_range(start: str, end: str) -> list[str]:
    """All week keys from ``start`` to ``end`` inclusive; empty when end < start."""
    n = weeks_between(start, end)
    if n < 0:
        return []
    first = week_start(start)
    return [(first + timedelta(weeks=i)).isoformat() for i in range(n + 1)]
