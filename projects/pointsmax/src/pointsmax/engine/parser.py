"""Rule-based offline goal parsing (FR-5).

Free-text goals in this domain are formulaic ("<trip type> <cabin> <A> to <B>
in <month>"), so the offline parser is rules plus the committed gazetteer — not
ML.  It is deterministic and hermetic: ``today`` is an explicit input and the
only vocabulary is the world's own gazetteer (SCOPE decision 19).

Resolutions of cases FR-5 leaves open (documented so fixtures can pin them):

* a flight goal with no trip-type cue defaults to **one-way**;
* passenger count defaults to the profile's ``default_passengers``;
* a cash utterance never infers a program filter — FR-5's mechanics list does
  not include program extraction, so ``cash_programs`` stays null;
* ``ParseError.missing`` uses the names ``origin_city``, ``dest_city``,
  ``city``, ``nights`` and ``travel_month``; ``ambiguous`` uses ``cities``.
"""

from __future__ import annotations

import re
from datetime import date

from ..models import Cabin, GoalSpec, ParseError
from ..models import World as _World
from .goals import cash_goal, flight_goal, stay_goal

_MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

# Longest phrases first so "premium economy" wins over "economy"/"premium".
_CABIN_SYNONYMS: tuple[tuple[str, Cabin], ...] = (
    ("premium economy", Cabin.PREMIUM_ECONOMY),
    ("premium-economy", Cabin.PREMIUM_ECONOMY),
    ("prem econ", Cabin.PREMIUM_ECONOMY),
    ("premium cabin", Cabin.PREMIUM_ECONOMY),
    ("first class", Cabin.FIRST),
    ("business class", Cabin.BUSINESS),
    ("economy class", Cabin.ECONOMY),
    ("main cabin", Cabin.ECONOMY),
    ("business", Cabin.BUSINESS),
    ("economy", Cabin.ECONOMY),
    ("coach", Cabin.ECONOMY),
    ("first", Cabin.FIRST),
    ("biz", Cabin.BUSINESS),
    ("pe", Cabin.PREMIUM_ECONOMY),
)

_ROUND_TRIP_CUES = ("round trip", "round-trip", "roundtrip", "return", "there and back", "rt")
_ONE_WAY_CUES = ("one way", "one-way", "oneway", "single leg", "ow")

_CASH_CUES = (
    "cash out",
    "cash-out",
    "cashout",
    "statement credit",
    "liquidate",
    "into cash",
    "for cash",
    "to cash",
    "cash back",
    "cash",
)
_STAY_CUES = ("hotel", "nights", "night", "stay", "resort")

_WORD_NUMBERS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _find_phrase(text: str, phrase: str) -> int | None:
    match = re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text)
    return match.start() if match else None


def _alias_table(world: _World) -> list[tuple[str, str]]:
    """(alias, city_code) pairs, longest alias first, all lowercase."""
    pairs: list[tuple[str, str]] = []
    for entry in world.gazetteer:
        seen: set[str] = set()
        for alias in [entry.city_code.lower(), *[a.lower() for a in entry.aliases]] + [
            ap.lower() for ap in entry.airports
        ]:
            if alias and alias not in seen:
                seen.add(alias)
                pairs.append((alias, entry.city_code))
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    return pairs


def _find_cities(text: str, world: _World) -> list[tuple[int, str]]:
    """Non-overlapping city mentions as ``(position, city_code)`` in text order."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    for alias, city_code in _alias_table(world):
        for match in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
            start, end = match.span()
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            taken.append((start, end))
            found.append((start, city_code))
    found.sort()
    deduped: list[tuple[int, str]] = []
    for position, city_code in found:
        if not any(city_code == c for _, c in deduped):
            deduped.append((position, city_code))
    return deduped


def _find_month(text: str, today: date) -> str | None:
    """Resolve a month mention to ``YYYY-MM`` — next occurrence on or after today."""
    explicit = re.search(r"\b(\d{4})-(0[1-9]|1[0-2])\b", text)
    if explicit:
        return f"{int(explicit.group(1)):04d}-{int(explicit.group(2)):02d}"
    best: tuple[int, str] | None = None
    for name, number in _MONTHS.items():
        position = _find_phrase(text, name)
        if position is None:
            continue
        year_match = re.match(r"\s+(\d{4})", text[position + len(name) :])
        if year_match:
            year = int(year_match.group(1))
        else:
            year = today.year if number >= today.month else today.year + 1
        candidate = (position, f"{year:04d}-{number:02d}")
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best[1] if best else None


def _find_cabin(text: str) -> Cabin | None:
    for phrase, cabin in _CABIN_SYNONYMS:
        if _has_phrase(text, phrase):
            return cabin
    return None


def _find_round_trip(text: str) -> bool | None:
    for cue in _ROUND_TRIP_CUES:
        if _has_phrase(text, cue):
            return True
    for cue in _ONE_WAY_CUES:
        if _has_phrase(text, cue):
            return False
    return None


def _find_nights(text: str) -> int | None:
    match = re.search(r"(?<![a-z0-9])(\d{1,2})\s+nights?(?![a-z0-9])", text)
    if match:
        return int(match.group(1))
    match = re.search(rf"(?<![a-z0-9])({'|'.join(_WORD_NUMBERS)})\s+nights?(?![a-z0-9])", text)
    if match:
        return _WORD_NUMBERS[match.group(1)]
    weeks = re.search(r"(?<![a-z0-9])(\d{1,2}|a|one|two|three)\s+weeks?(?![a-z0-9])", text)
    if weeks:
        token = weeks.group(1)
        count = 1 if token == "a" else _WORD_NUMBERS.get(token, 0) or int(token)
        return count * 7
    if _has_phrase(text, "a night"):
        return 1
    return None


def _find_passengers(text: str) -> int | None:
    patterns = (
        r"(?<![a-z0-9])for\s+(\d{1,2})(?![a-z0-9])",
        r"(?<![a-z0-9])(\d{1,2})\s+(?:people|passengers|pax|adults|travell?ers|of\s+us)(?![a-z0-9])",
        r"(?<![a-z0-9])party\s+of\s+(\d{1,2})(?![a-z0-9])",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    word_patterns = (
        rf"(?<![a-z0-9])for\s+({'|'.join(_WORD_NUMBERS)})(?![a-z0-9])",
        rf"(?<![a-z0-9])({'|'.join(_WORD_NUMBERS)})\s+(?:people|passengers|adults|travell?ers|of\s+us)(?![a-z0-9])",
    )
    for pattern in word_patterns:
        match = re.search(pattern, text)
        if match:
            return _WORD_NUMBERS[match.group(1)]
    return None


def _mask(text: str, pattern: str) -> str:
    return re.sub(pattern, lambda m: " " * len(m.group(0)), text)


def parse_goal_text(
    text: str,
    *,
    world: _World,
    today: date,
    home_city: str | None = None,
    default_passengers: int = 1,
) -> GoalSpec | ParseError:
    """Turn free text into a :class:`GoalSpec` or a structured :class:`ParseError`."""
    raw = text.strip()
    lowered = raw.lower()
    if not lowered:
        return ParseError(
            raw_text=raw,
            message="empty request",
            missing=["kind"],
        )

    if any(_has_phrase(lowered, cue) for cue in _CASH_CUES):
        return cash_goal(raw_text=raw)

    stay_intent = any(_has_phrase(lowered, cue) for cue in _STAY_CUES)
    # Mask night/week counts so "5 nights" is never read as a passenger count.
    scratch = _mask(lowered, r"(?<![a-z0-9])\d{1,2}\s+(?:nights?|weeks?)(?![a-z0-9])")
    cities = _find_cities(lowered, world)
    month = _find_month(lowered, today)

    if stay_intent:
        missing: list[str] = []
        nights = _find_nights(lowered)
        city = cities[0][1] if cities else None
        if city is None:
            missing.append("city")
        if nights is None:
            missing.append("nights")
        if month is None:
            missing.append("travel_month")
        if missing:
            return ParseError(
                raw_text=raw,
                message="could not resolve " + ", ".join(missing),
                missing=missing,
            )
        assert city and nights and month
        if len(cities) > 2:
            return ParseError(
                raw_text=raw,
                message="more than one destination city mentioned",
                ambiguous=["cities"],
            )
        return stay_goal(city=city, nights=nights, month=month, raw_text=raw)

    missing = []
    ambiguous: list[str] = []
    origin: str | None = None
    dest: str | None = None
    if len(cities) > 2:
        ambiguous.append("cities")
    elif len(cities) == 2:
        first, second = cities[0], cities[1]
        prefix = lowered[: first[0]]
        between = lowered[first[0] : second[0]]
        if _has_phrase(prefix, "to") and _has_phrase(between, "from"):
            origin, dest = second[1], first[1]
        else:
            origin, dest = first[1], second[1]
    elif len(cities) == 1:
        dest = cities[0][1]
        origin = home_city
        if origin is None:
            missing.append("origin_city")
    else:
        missing.append("dest_city")
        if home_city is None:
            missing.append("origin_city")

    if month is None:
        missing.append("travel_month")
    if missing or ambiguous:
        parts = []
        if missing:
            parts.append("could not resolve " + ", ".join(missing))
        if ambiguous:
            parts.append("ambiguous " + ", ".join(ambiguous))
        return ParseError(
            raw_text=raw,
            message="; ".join(parts),
            missing=missing,
            ambiguous=ambiguous,
        )

    assert origin and dest and month
    if origin == dest:
        return ParseError(
            raw_text=raw,
            message="origin and destination resolve to the same city",
            ambiguous=["cities"],
        )
    passengers = _find_passengers(scratch) or default_passengers
    passengers = min(max(passengers, 1), 8)
    round_trip = _find_round_trip(lowered)
    return flight_goal(
        origin_city=origin,
        dest_city=dest,
        month=month,
        cabin=_find_cabin(lowered),
        round_trip=bool(round_trip),
        passengers=passengers,
        raw_text=raw,
    )
