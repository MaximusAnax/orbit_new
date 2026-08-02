"""Canonical step order, explanations, caveats, ranking and verdicts (FR-9, FR-10).

Everything here is a deterministic function of data the engine already computed,
so an independent implementation reading the same fields reproduces the same
ordering, the same texts and the same caveats.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..models import (
    CAVEAT_ORDER,
    Caveat,
    CaveatCode,
    GoalKind,
    GoalSpec,
    Plan,
    PlanParams,
    PlanStep,
    StepKind,
    Verdict,
    World,
    canonical_json,
)
from .value import Booking, PlanValue, TransferUse

# --------------------------------------------------------------------------
# Formatting helpers (no floats)
# --------------------------------------------------------------------------


def format_points(points: int) -> str:
    return f"{points:,}"


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def format_cpp(cpp_milli: int) -> str:
    """Milli-cents per point rendered as a cents-per-point figure, e.g. ``2.05``."""
    sign = "-" if cpp_milli < 0 else ""
    cpp_milli = abs(cpp_milli)
    return f"{sign}{cpp_milli // 1000}.{cpp_milli % 1000 // 10:02d}"


def _days(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"


# --------------------------------------------------------------------------
# Plan drafts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanDraft:
    """A fully solved booking set: what to buy, how to fund it, and what it costs."""

    bookings: tuple[Booking, ...]
    transfers: tuple[TransferUse, ...]
    value: PlanValue
    arrival_days: dict[str, int] = field(default_factory=dict)
    hop_index: dict[str, int] = field(default_factory=dict)
    feasible_in_days: int = 0

    def is_portal_only(self) -> bool:
        """True when no award booking is involved — the FR-9 comparator family."""
        return bool(self.bookings) and all(b.kind is StepKind.BOOK_PORTAL for b in self.bookings)


# --------------------------------------------------------------------------
# Steps (FR-9 canonical order + FR-10 explanations)
# --------------------------------------------------------------------------


def _ratio_text(ratio_from: int, ratio_to: int) -> str:
    return f"{ratio_from}:{ratio_to}"


def _transfer_explanation(world: World, step: PlanStep) -> str:
    assert step.edge_id and step.from_program and step.to_program
    edge = world.edge(step.edge_id)
    source = world.program(step.from_program).name
    target = world.program(step.to_program).name
    timing = "instant" if step.eta_days == 0 else _days(step.eta_days)
    fee = "no fee" if step.fees_cents == 0 else f"{format_cents(step.fees_cents)} fee"
    text = (
        f"Transfer {format_points(step.points_sent)} {source} to {target} "
        f"({_ratio_text(edge.ratio_from, edge.ratio_to)}, {timing}, {fee})."
    )
    if step.points_delivered is not None and step.points_delivered != step.points_sent:
        text += f" {format_points(step.points_delivered)} points land in {target}."
    return text


def _booking_explanation(world: World, step: PlanStep, booking: Booking) -> str:
    program = world.program(booking.program_id).name
    if booking.kind is StepKind.BOOK_AWARD:
        if booking.city is not None:
            where = f"{booking.city} for {booking.nights} nights"
        else:
            where = f"{booking.cabin} {booking.origin_city}->{booking.dest_city}"
            if booking.round_trip:
                where += " round trip"
        return (
            f"Book {program} {where} ({booking.month}): "
            f"{format_points(step.points_sent)} points plus "
            f"{format_cents(step.fees_cents)} in taxes and fees."
        )
    if booking.kind is StepKind.BOOK_PORTAL:
        if booking.city is not None:
            where = f"{booking.city} for {booking.nights} nights"
        else:
            where = f"{booking.origin_city}->{booking.dest_city}"
            if booking.round_trip:
                where += " round trip"
        return (
            f"Pay for {where} ({booking.month}) with {format_points(step.points_sent)} "
            f"{program} points through {booking.cashout_id}."
        )
    return (
        f"Redeem {format_points(step.points_sent)} {program} points through "
        f"{booking.cashout_id} for cash."
    )


def build_steps(
    world: World,
    bookings: tuple[Booking, ...],
    transfers: tuple[TransferUse, ...],
    hop_index: dict[str, int],
) -> list[PlanStep]:
    """Build every step, sort into FR-9 canonical order and number ``seq`` 1..N."""
    steps: list[PlanStep] = []
    explanations: dict[int, Booking] = {}
    for use in transfers:
        step = PlanStep(
            seq=1,
            kind=StepKind.TRANSFER,
            hop_index=hop_index.get(use.edge_id, 0),
            from_program=use.from_program,
            to_program=use.to_program,
            edge_id=use.edge_id,
            points_sent=use.sent,
            points_delivered=use.delivered,
            fees_cents=use.fee_cents,
            eta_days=use.time_days,
            irreversible=True,
        )
        steps.append(step)
    for booking in bookings:
        step = PlanStep(
            seq=1,
            kind=booking.kind,
            hop_index=0,
            from_program=booking.program_id,
            offer_id=booking.offer_id,
            cashout_id=booking.cashout_id,
            points_sent=booking.points,
            fees_cents=booking.fees_cents,
            eta_days=0,
            irreversible=booking.kind is StepKind.BOOK_AWARD,
        )
        explanations[id(step)] = booking
        steps.append(step)

    steps.sort(key=lambda s: s.sort_key())
    for index, step in enumerate(steps, start=1):
        step.seq = index
        if step.kind is StepKind.TRANSFER:
            step.explanation = _transfer_explanation(world, step)
        else:
            step.explanation = _booking_explanation(world, step, explanations[id(step)])
    return steps


def plan_signature(steps: list[PlanStep]) -> str:
    """sha256 of the canonical JSON of the plan's canonical step-tuple list (FR-9)."""
    canonical = [s.canonical_tuple() for s in steps]
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# FR-10 caveats
# --------------------------------------------------------------------------

CAVEAT_TEMPLATES: dict[CaveatCode, str] = {
    CaveatCode.IRREVERSIBLE_TRANSFER: (
        "Transferring {points} points over {edge_id} cannot be undone."
    ),
    CaveatCode.TRANSFER_TIME_RISK: (
        "Points reach {program} in {arrival_days} day(s) and the booking deadline is "
        "{deadline} — inside the {slack_days}-day safety margin."
    ),
    CaveatCode.STRANDED_POINTS: (
        "{points} points would be left stranded in {program}, worth about {value}."
    ),
    CaveatCode.PROMO_EXPIRING: (
        "{subject} is only valid through {valid_to} ({days_left} day(s) left)."
    ),
    CaveatCode.BELOW_BASELINE: (
        "{option_id} redeems {program} at {option_cpp} cents per point, below the "
        "{baseline_cpp} cents-per-point baseline valuation."
    ),
    CaveatCode.STALE_WORLD: (
        "Valuation data is {days_old} days old (as of {as_of}); verify rates before moving points."
    ),
    CaveatCode.SEATS_LIMITED: (
        "{offer_id} shows only {seats_available} seat(s) for {passengers} passenger(s)."
    ),
}


def _caveat(code: CaveatCode, params: dict, text: str) -> Caveat:
    return Caveat(code=code, params=params, text=text)


def build_caveats(
    world: World,
    goal: GoalSpec,
    draft: PlanDraft,
    today: date,
    params: PlanParams,
) -> list[Caveat]:
    """FR-10: a pure function of (plan, world, today, thresholds)."""
    out: list[Caveat] = []

    for use in draft.transfers:
        out.append(
            _caveat(
                CaveatCode.IRREVERSIBLE_TRANSFER,
                {"edge_id": use.edge_id, "points": use.sent},
                CAVEAT_TEMPLATES[CaveatCode.IRREVERSIBLE_TRANSFER].format(
                    points=format_points(use.sent), edge_id=use.edge_id
                ),
            )
        )

    seen_timing: set[tuple[str, str]] = set()
    for booking in draft.bookings:
        if booking.deadline is None:
            continue
        arrival = draft.arrival_days.get(booking.program_id, 0)
        slack = (booking.deadline - (today + timedelta(days=arrival))).days
        if slack <= params.slack_days:
            key = (booking.program_id, booking.deadline.isoformat())
            if key in seen_timing:
                continue
            seen_timing.add(key)
            payload = {
                "program": booking.program_id,
                "arrival_days": arrival,
                "deadline": booking.deadline.isoformat(),
                "slack_days": params.slack_days,
            }
            out.append(
                _caveat(
                    CaveatCode.TRANSFER_TIME_RISK,
                    payload,
                    CAVEAT_TEMPLATES[CaveatCode.TRANSFER_TIME_RISK].format(**payload),
                )
            )

    transferred_into = {use.to_program for use in draft.transfers}
    for program_id in sorted(transferred_into):
        leftover = draft.value.ending_holdings.get(program_id, 0)
        if leftover > 0:
            value_cents = (leftover * world.mcpp(program_id)) // 1000
            payload = {
                "program": program_id,
                "points": leftover,
                "value_cents": value_cents,
            }
            out.append(
                _caveat(
                    CaveatCode.STRANDED_POINTS,
                    payload,
                    CAVEAT_TEMPLATES[CaveatCode.STRANDED_POINTS].format(
                        points=format_points(leftover),
                        program=program_id,
                        value=format_cents(value_cents),
                    ),
                )
            )

    for use in draft.transfers:
        edge = world.edge(use.edge_id)
        if edge.valid_to is not None:
            days_left = (edge.valid_to - today).days
            if days_left <= params.promo_days:
                payload = {
                    "edge_id": edge.id,
                    "valid_to": edge.valid_to.isoformat(),
                    "days_left": days_left,
                }
                out.append(
                    _caveat(
                        CaveatCode.PROMO_EXPIRING,
                        payload,
                        CAVEAT_TEMPLATES[CaveatCode.PROMO_EXPIRING].format(
                            subject=edge.id,
                            valid_to=payload["valid_to"],
                            days_left=days_left,
                        ),
                    )
                )
    for booking in draft.bookings:
        if booking.cashout_id is None:
            continue
        option = world.cashout(booking.cashout_id)
        if option.valid_to is not None:
            days_left = (option.valid_to - today).days
            if days_left <= params.promo_days:
                payload = {
                    "option_id": option.id,
                    "valid_to": option.valid_to.isoformat(),
                    "days_left": days_left,
                }
                out.append(
                    _caveat(
                        CaveatCode.PROMO_EXPIRING,
                        payload,
                        CAVEAT_TEMPLATES[CaveatCode.PROMO_EXPIRING].format(
                            subject=option.id,
                            valid_to=payload["valid_to"],
                            days_left=days_left,
                        ),
                    )
                )

    for booking in draft.bookings:
        if booking.cashout_id is None:
            continue
        option = world.cashout(booking.cashout_id)
        baseline = world.mcpp(option.program_id)
        if option.cpp_milli < baseline:
            payload = {
                "program": option.program_id,
                "option_id": option.id,
                "option_cpp_milli": option.cpp_milli,
                "baseline_cpp_milli": baseline,
            }
            out.append(
                _caveat(
                    CaveatCode.BELOW_BASELINE,
                    payload,
                    CAVEAT_TEMPLATES[CaveatCode.BELOW_BASELINE].format(
                        option_id=option.id,
                        program=option.program_id,
                        option_cpp=format_cpp(option.cpp_milli),
                        baseline_cpp=format_cpp(baseline),
                    ),
                )
            )

    as_of = world.min_valuation_as_of()
    days_old = (today - as_of).days
    if days_old > params.stale_days:
        payload = {"as_of": as_of.isoformat(), "days_old": days_old}
        out.append(
            _caveat(
                CaveatCode.STALE_WORLD,
                payload,
                CAVEAT_TEMPLATES[CaveatCode.STALE_WORLD].format(**payload),
            )
        )

    passengers = goal.passengers or 1
    for booking in draft.bookings:
        if booking.offer_id is None or booking.seats_available is None:
            continue
        if booking.seats_available - passengers <= 1:
            payload = {
                "offer_id": booking.offer_id,
                "seats_available": booking.seats_available,
                "passengers": passengers,
            }
            out.append(
                _caveat(
                    CaveatCode.SEATS_LIMITED,
                    payload,
                    CAVEAT_TEMPLATES[CaveatCode.SEATS_LIMITED].format(**payload),
                )
            )

    out.sort(key=lambda c: (CAVEAT_ORDER[c.code], canonical_json(c.params)))
    return out


# --------------------------------------------------------------------------
# FR-9 ranking
# --------------------------------------------------------------------------


def objective_value(draft: PlanDraft, goal_kind: GoalKind) -> int:
    """The ranking objective: ``cash_received`` for cash goals, else ``net_value``."""
    if goal_kind is GoalKind.CASH:
        return draft.value.cash_received_cents or 0
    return draft.value.net_value_cents


def draft_sort_key(draft: PlanDraft, goal_kind: GoalKind, canonical: list[list]) -> tuple:
    """FR-9 total order: objective desc, fewer points, fewer steps, canonical form asc."""
    return (
        -objective_value(draft, goal_kind),
        draft.value.total_points_spent(),
        len(draft.bookings) + len(draft.transfers),
        canonical_json(canonical),
    )


def canonical_form_of(world: World, draft: PlanDraft) -> tuple[list[PlanStep], list[list]]:
    steps = build_steps(world, draft.bookings, draft.transfers, draft.hop_index)
    return steps, [s.canonical_tuple() for s in steps]


def build_plan(
    world: World,
    goal: GoalSpec,
    draft: PlanDraft,
    today: date,
    params: PlanParams,
    *,
    rank: int,
    is_comparator: bool = False,
) -> Plan:
    """Materialise a :class:`Plan` (steps, math, caveats) from a solved draft."""
    steps = build_steps(world, draft.bookings, draft.transfers, draft.hop_index)
    value = draft.value
    return Plan(
        rank=rank,
        is_comparator=is_comparator,
        gross_value_cents=value.gross_value_cents,
        cash_outlay_cents=value.cash_outlay_cents,
        points_cost_cents=value.points_cost_cents,
        net_value_cents=value.net_value_cents,
        cash_received_cents=value.cash_received_cents,
        realized_cpp_milli=value.realized_cpp_milli,
        points_spent=dict(value.points_spent),
        feasible_in_days=draft.feasible_in_days,
        signature=plan_signature(steps),
        caveats=build_caveats(world, goal, draft, today, params),
        steps=steps,
    )


def choose_verdict(
    goal_kind: GoalKind,
    *,
    candidate_sets: int,
    feasible_drafts: list[PlanDraft],
) -> Verdict:
    """FR-9 plan-set verdict."""
    if not candidate_sets:
        return Verdict.NO_MATCHING_AWARD
    if not feasible_drafts:
        return Verdict.INSUFFICIENT_POINTS
    if goal_kind is GoalKind.CASH:
        return Verdict.CASH_PLAN
    best = feasible_drafts[0]
    if best.value.net_value_cents > 0:
        return Verdict.BOOK_WITH_POINTS
    return Verdict.PAY_CASH_KEEP_POINTS
