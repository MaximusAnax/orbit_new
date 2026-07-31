"""Bookings and portfolio-delta value accounting (FR-8, hard part B).

The single formula ``net_value = gross - cash_outlay - (V(H0) - V(H1))`` prices
every effect — opportunity cost, stranded increment leftovers at the
*destination* program's valuation, ratio effects and tier bonuses — with no
piecewise special cases (SCOPE decision 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from ..models import (
    AwardOffer,
    Cabin,
    CashoutOption,
    GoalSpec,
    OfferKind,
    StepKind,
    TransferEdge,
    ValueBreakdown,
    World,
)
from .goals import offer_deadline
from .money import (
    cashout_value_cents,
    delivered_points,
    portal_points_price,
    portfolio_value,
    realized_cpp_milli,
    redeemable_points,
    transfer_fee_cents,
    value_of_points,
)
from .world import ActiveWorld


@dataclass(frozen=True)
class Booking:
    """A single purchase paid from exactly one program's balance (decision 10)."""

    kind: StepKind
    program_id: str
    points: int
    fees_cents: int
    value_cents: int
    offer_id: str | None = None
    cashout_id: str | None = None
    deadline: date | None = None
    seats_available: int | None = None
    passengers: int | None = None
    origin_city: str | None = None
    dest_city: str | None = None
    cabin: Cabin | None = None
    round_trip: bool | None = None
    city: str | None = None
    nights: int | None = None
    month: str | None = None

    def sort_key(self) -> tuple:
        return (
            self.kind,
            self.program_id,
            self.offer_id or "",
            self.cashout_id or "",
            self.points,
        )


@dataclass(frozen=True)
class TransferUse:
    """One merged transfer over a single edge (FR-7b)."""

    edge_id: str
    from_program: str
    to_program: str
    sent: int
    delivered: int
    fee_cents: int
    time_days: int

    @staticmethod
    def build(edge: TransferEdge, sent: int) -> TransferUse:
        return TransferUse(
            edge_id=edge.id,
            from_program=edge.from_program,
            to_program=edge.to_program,
            sent=sent,
            delivered=delivered_points(edge, sent),
            fee_cents=transfer_fee_cents(edge, sent),
            time_days=edge.time_days,
        )


@dataclass(frozen=True)
class PlanValue:
    """The FR-8 accounting result for one (bookings, transfers) combination."""

    gross_value_cents: int
    cash_outlay_cents: int
    points_cost_cents: int
    net_value_cents: int
    cash_received_cents: int | None
    realized_cpp_milli: int | None
    points_spent: dict[str, int] = field(default_factory=dict)
    ending_holdings: dict[str, int] = field(default_factory=dict)

    def total_points_spent(self) -> int:
        return sum(self.points_spent.values())


# --------------------------------------------------------------------------
# Booking construction (pricing)
# --------------------------------------------------------------------------


def award_flight_booking(
    goal: GoalSpec,
    offer: AwardOffer,
    world: World,
    *,
    origin_city: str,
    dest_city: str,
    round_trip: bool,
) -> Booking | None:
    """Price a flight award offer against the *goal's* travel month (FR-8)."""
    month = goal.travel_month
    passengers = goal.passengers or 1
    assert month
    fare = world.find_fare(
        OfferKind.FLIGHT,
        month=month,
        origin_city=origin_city,
        dest_city=dest_city,
        cabin=offer.cabin,
        round_trip=round_trip,
    )
    if fare is None:
        return None
    return Booking(
        kind=StepKind.BOOK_AWARD,
        program_id=offer.program_id,
        points=offer.points_price * passengers,
        fees_cents=offer.fees_cents * passengers,
        value_cents=fare * passengers,
        offer_id=offer.id,
        deadline=offer_deadline(goal, offer),
        seats_available=offer.seats_available,
        passengers=passengers,
        origin_city=origin_city,
        dest_city=dest_city,
        cabin=offer.cabin,
        round_trip=round_trip,
        month=month,
    )


def portal_flight_booking(
    goal: GoalSpec,
    option: CashoutOption,
    world: World,
    *,
    origin_city: str,
    dest_city: str,
    round_trip: bool,
    cabin: Cabin,
) -> Booking | None:
    """Price a portal flight booking for one leg (or the round trip) (FR-7a/FR-8)."""
    month = goal.travel_month
    passengers = goal.passengers or 1
    assert month
    fare = world.find_fare(
        OfferKind.FLIGHT,
        month=month,
        origin_city=origin_city,
        dest_city=dest_city,
        cabin=cabin,
        round_trip=round_trip,
    )
    if fare is None:
        return None
    total_fare = fare * passengers
    return Booking(
        kind=StepKind.BOOK_PORTAL,
        program_id=option.program_id,
        points=portal_points_price(total_fare, option),
        fees_cents=0,
        value_cents=total_fare,
        cashout_id=option.id,
        deadline=offer_deadline(goal, None),
        passengers=passengers,
        origin_city=origin_city,
        dest_city=dest_city,
        cabin=cabin,
        round_trip=round_trip,
        month=month,
    )


def award_stay_booking(goal: GoalSpec, offer: AwardOffer, world: World) -> Booking | None:
    """Price a stay award offer: ``nights x points_price`` against the nightly fare."""
    month = goal.travel_month
    nights = goal.nights or 1
    assert month and goal.city
    nightly = world.find_fare(OfferKind.STAY, month=month, city=goal.city)
    if nightly is None:
        return None
    return Booking(
        kind=StepKind.BOOK_AWARD,
        program_id=offer.program_id,
        points=offer.points_price * nights,
        fees_cents=offer.fees_cents * nights,
        value_cents=nightly * nights,
        offer_id=offer.id,
        deadline=offer_deadline(goal, offer),
        seats_available=offer.seats_available,
        city=goal.city,
        nights=nights,
        month=month,
    )


def portal_stay_booking(goal: GoalSpec, option: CashoutOption, world: World) -> Booking | None:
    """Price a portal stay booking from ``nights x`` the stay reference fare."""
    month = goal.travel_month
    nights = goal.nights or 1
    assert month and goal.city
    nightly = world.find_fare(OfferKind.STAY, month=month, city=goal.city)
    if nightly is None:
        return None
    total = nightly * nights
    return Booking(
        kind=StepKind.BOOK_PORTAL,
        program_id=option.program_id,
        points=portal_points_price(total, option),
        fees_cents=0,
        value_cents=total,
        cashout_id=option.id,
        deadline=offer_deadline(goal, None),
        city=goal.city,
        nights=nights,
        month=month,
    )


def cash_booking(program_id: str, option: CashoutOption, points: int) -> Booking:
    """A liquid cash-out of ``points`` through ``option`` (FR-12)."""
    return Booking(
        kind=StepKind.REDEEM_CASH,
        program_id=program_id,
        points=points,
        fees_cents=0,
        value_cents=cashout_value_cents(points, option),
        cashout_id=option.id,
    )


# --------------------------------------------------------------------------
# Accounting
# --------------------------------------------------------------------------


def ending_holdings(
    opening: Mapping[str, int],
    transfers: tuple[TransferUse, ...] | list[TransferUse],
    bookings: tuple[Booking, ...] | list[Booking],
) -> dict[str, int]:
    """H1: opening balances after every transfer and booking in the plan."""
    holdings: dict[str, int] = {p: n for p, n in opening.items()}
    for use in transfers:
        holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
        holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered
    for booking in bookings:
        holdings[booking.program_id] = holdings.get(booking.program_id, 0) - booking.points
    return holdings


def value_plan(
    opening: Mapping[str, int],
    transfers: tuple[TransferUse, ...] | list[TransferUse],
    bookings: tuple[Booking, ...] | list[Booking],
    mcpp: Mapping[str, int],
) -> PlanValue:
    """Compute every FR-8 number for a fully specified plan."""
    holdings = ending_holdings(opening, transfers, bookings)
    if any(points < 0 for points in holdings.values()):
        raise ValueError("plan drives a balance negative")

    gross = sum(b.value_cents for b in bookings)
    booking_fees = sum(b.fees_cents for b in bookings)
    transfer_fees = sum(t.fee_cents for t in transfers)
    outlay = booking_fees + transfer_fees

    all_programs = set(opening) | set(holdings)
    opening_full = {p: opening.get(p, 0) for p in all_programs}
    holdings_full = {p: holdings.get(p, 0) for p in all_programs}
    points_cost = portfolio_value(opening_full, mcpp) - portfolio_value(holdings_full, mcpp)
    net = gross - outlay - points_cost

    booking_points = sum(b.points for b in bookings)
    pooled_cpp = realized_cpp_milli(gross, booking_fees, booking_points)

    cash_bookings = [b for b in bookings if b.kind is StepKind.REDEEM_CASH]
    cash_received = sum(b.value_cents for b in cash_bookings) if cash_bookings else None

    spent = {
        p: opening_full[p] - holdings_full[p]
        for p in sorted(all_programs)
        if opening_full[p] > holdings_full[p]
    }
    return PlanValue(
        gross_value_cents=gross,
        cash_outlay_cents=outlay,
        points_cost_cents=points_cost,
        net_value_cents=net,
        cash_received_cents=cash_received,
        realized_cpp_milli=pooled_cpp,
        points_spent=spent,
        ending_holdings=holdings_full,
    )


def booking_realized_cpp_milli(booking: Booking) -> int | None:
    """Per-booking realized cpp: ``(value - fees) x 1000 // points`` (FR-8)."""
    return realized_cpp_milli(booking.value_cents, booking.fees_cents, booking.points)


# --------------------------------------------------------------------------
# FR-13 quick valuation
# --------------------------------------------------------------------------


def value_breakdown(
    world: World, active: ActiveWorld, program_id: str, points: int
) -> ValueBreakdown:
    """Baseline value, cash floor and travel floor for a balance (FR-13).

    The **cash floor** considers only liquid (``is_cash``) options; the **travel
    floor** considers every active option, so a CSR holder's UR travel floor is
    the 1.5 cpp portal while their cash floor is the 1.0 cpp statement credit.
    Both floors respect quantization: the unredeemable remainder is worth 0.
    """
    if points < 0:
        raise ValueError("points must be non-negative")
    valuation = world.valuation(program_id)
    options = active.options_for(program_id)

    def floor_over(candidates: tuple[CashoutOption, ...]) -> tuple[int, str | None]:
        best_cents = 0
        best_id: str | None = None
        for option in sorted(candidates, key=lambda o: o.id):
            amount = redeemable_points(points, option)
            if amount <= 0:
                continue
            cents = cashout_value_cents(amount, option)
            if best_id is None or cents > best_cents:
                best_cents, best_id = cents, option.id
        return best_cents, best_id

    cash_cents, cash_id = floor_over(tuple(o for o in options if o.is_cash))
    travel_cents, travel_id = floor_over(options)
    return ValueBreakdown(
        program_id=program_id,
        points=points,
        baseline_value_cents=value_of_points(points, valuation.cpp_milli),
        baseline_cpp_milli=valuation.cpp_milli,
        as_of=valuation.as_of,
        cash_floor_cents=cash_cents,
        cash_floor_option_id=cash_id,
        travel_floor_cents=travel_cents,
        travel_floor_option_id=travel_id,
    )
