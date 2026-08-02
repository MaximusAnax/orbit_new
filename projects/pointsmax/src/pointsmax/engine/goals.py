"""Goal construction and offer matching (FR-4, FR-6).

Pure predicates: a goal plus the world plus ``today`` determine, with no
ambiguity, which award offers are candidates for which leg.
"""

from __future__ import annotations

import calendar
from datetime import date

from ..models import AwardOffer, Cabin, GoalKind, GoalSpec, OfferKind, World


def month_bounds(month: str) -> tuple[date, date]:
    """First and last day of a ``YYYY-MM`` month."""
    year_s, month_s = month.split("-")
    year, month_no = int(year_s), int(month_s)
    if not 1 <= month_no <= 12:
        raise ValueError(f"invalid month {month!r}")
    last = calendar.monthrange(year, month_no)[1]
    return date(year, month_no, 1), date(year, month_no, last)


def flight_goal(
    *,
    origin_city: str,
    dest_city: str,
    month: str,
    cabin: Cabin | None = None,
    round_trip: bool = False,
    passengers: int = 1,
    book_by: date | None = None,
    raw_text: str | None = None,
) -> GoalSpec:
    """Build a validated flight :class:`GoalSpec` from a travel month."""
    start, end = month_bounds(month)
    return GoalSpec(
        kind=GoalKind.FLIGHT,
        raw_text=raw_text,
        origin_city=origin_city,
        dest_city=dest_city,
        cabin=cabin,
        round_trip=round_trip,
        passengers=passengers,
        travel_window_start=start,
        travel_window_end=end,
        book_by=book_by,
    )


def stay_goal(
    *,
    city: str,
    nights: int,
    month: str,
    book_by: date | None = None,
    raw_text: str | None = None,
) -> GoalSpec:
    """Build a validated stay :class:`GoalSpec` from a travel month."""
    start, end = month_bounds(month)
    return GoalSpec(
        kind=GoalKind.STAY,
        raw_text=raw_text,
        city=city,
        nights=nights,
        travel_window_start=start,
        travel_window_end=end,
        book_by=book_by,
    )


def cash_goal(
    *,
    programs: list[str] | None = None,
    max_points: dict[str, int] | None = None,
    raw_text: str | None = None,
) -> GoalSpec:
    """Build a validated cash :class:`GoalSpec` (FR-12)."""
    return GoalSpec(
        kind=GoalKind.CASH,
        raw_text=raw_text,
        cash_programs=programs,
        cash_max_points=max_points,
    )


def validate_goal_against_world(goal: GoalSpec, world: World) -> list[str]:
    """FR-4 world-dependent validation: every referenced code must be known.

    Returns a list of human-readable problems (empty when the goal is usable).
    Pure: the same (goal, world) always yields the same list.
    """
    problems: list[str] = []
    for field_name in ("origin_city", "dest_city", "city"):
        code = getattr(goal, field_name)
        if code is not None and world.city(code) is None:
            problems.append(f"{field_name} {code!r} is not a known city code")
    for program_id in goal.cash_programs or []:
        if not world.has_program(program_id):
            problems.append(f"cash_programs contains unknown program {program_id!r}")
    for program_id in goal.cash_max_points or {}:
        if not world.has_program(program_id):
            problems.append(f"cash_max_points contains unknown program {program_id!r}")
    return problems


def overlap_days(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
    """Number of days both windows share (0 when disjoint)."""
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if start > end:
        return 0
    return (end - start).days + 1


def _bookable(offer: AwardOffer, today: date) -> bool:
    return offer.bookable_until is None or offer.bookable_until >= today


def _seats_ok(offer: AwardOffer, passengers: int | None) -> bool:
    if offer.seats_available is None or passengers is None:
        return True
    return passengers <= offer.seats_available


def flight_offer_matches(
    goal: GoalSpec,
    offer: AwardOffer,
    world: World,
    today: date,
    *,
    origin_city: str,
    dest_city: str,
    round_trip: bool,
) -> bool:
    """FR-6 predicate for one flight direction (or the round-trip form)."""
    if offer.kind is not OfferKind.FLIGHT:
        return False
    if bool(offer.round_trip) != round_trip:
        return False
    if world.city_of(offer.origin) != origin_city:
        return False
    if world.city_of(offer.destination) != dest_city:
        return False
    if goal.cabin is not None and offer.cabin is not goal.cabin:
        return False
    assert goal.travel_window_start and goal.travel_window_end
    if not overlap_days(
        goal.travel_window_start,
        goal.travel_window_end,
        offer.travel_window_start,
        offer.travel_window_end,
    ):
        return False
    if not _seats_ok(offer, goal.passengers):
        return False
    return _bookable(offer, today)


def matching_flight_offers(
    goal: GoalSpec,
    world: World,
    today: date,
    *,
    origin_city: str,
    dest_city: str,
    round_trip: bool,
) -> list[AwardOffer]:
    """All FR-6-matching flight offers for a direction, sorted by offer id."""
    matches = [
        offer
        for offer in world.offers
        if flight_offer_matches(
            goal,
            offer,
            world,
            today,
            origin_city=origin_city,
            dest_city=dest_city,
            round_trip=round_trip,
        )
    ]
    matches.sort(key=lambda o: o.id)
    return matches


def stay_offer_matches(goal: GoalSpec, offer: AwardOffer, world: World, today: date) -> bool:
    """FR-6 predicate for stays: same city and an overlap of at least ``nights``."""
    if offer.kind is not OfferKind.STAY:
        return False
    if world.city_of(offer.city) != goal.city:
        return False
    assert goal.travel_window_start and goal.travel_window_end and goal.nights
    if (
        overlap_days(
            goal.travel_window_start,
            goal.travel_window_end,
            offer.travel_window_start,
            offer.travel_window_end,
        )
        < goal.nights
    ):
        return False
    if not _seats_ok(offer, goal.passengers):
        return False
    return _bookable(offer, today)


def matching_stay_offers(goal: GoalSpec, world: World, today: date) -> list[AwardOffer]:
    """All FR-6-matching stay offers, sorted by offer id."""
    matches = [o for o in world.offers if stay_offer_matches(goal, o, world, today)]
    matches.sort(key=lambda o: o.id)
    return matches


def offer_deadline(goal: GoalSpec, offer: AwardOffer | None) -> date | None:
    """FR-7d ``deadline(b) = min(goal.book_by, offer.bookable_until, offer.travel_window_end)``.

    ``travel_window_start`` deliberately does not bind: booking after travel has
    begun is still valid for later dates in the window.  Portal bookings (no
    offer) use ``goal.book_by`` and the goal's own window end.
    """
    bounds: list[date] = []
    if goal.book_by is not None:
        bounds.append(goal.book_by)
    if offer is not None:
        if offer.bookable_until is not None:
            bounds.append(offer.bookable_until)
        bounds.append(offer.travel_window_end)
    elif goal.travel_window_end is not None:
        bounds.append(goal.travel_window_end)
    return min(bounds) if bounds else None
