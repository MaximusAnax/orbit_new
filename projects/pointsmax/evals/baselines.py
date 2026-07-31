"""Measured naive baselines (EVALS "Naive baselines and gates").

Every baseline in the gates table is implemented here and *measured*, never
estimated: a greedy planner (with ``quantize``/``gating`` switches), a cpp-first
re-ranker, a keyword-only parser, a fee/tier/stranding-ignoring calculator, and
the engine run with ``pruning=False``.  ``run.py`` prints their scores beside
the engine's and ``test_gates.py`` asserts each stays at or below its ceiling,
so fixture drift that erodes the engine-vs-baseline gap fails mechanically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from metrics import EngineRun, eval_world, params_of
from pointsmax.engine.goals import cash_goal, flight_goal, month_bounds
from pointsmax.engine.money import ceil_div, ceil_to_multiple
from pointsmax.engine.plan import PlanDraft, build_plan, choose_verdict
from pointsmax.engine.search import (
    _arrival_and_hops,
    aggregate_needs,
    cash_drafts,
    cash_goal_has_options,
    enumerate_booking_sets,
)
from pointsmax.engine.value import TransferUse, value_plan
from pointsmax.engine.world import active_subgraph
from pointsmax.models import (
    DISCLAIMER,
    GoalKind,
    GoalSpec,
    ParseError,
    Plan,
    PlanParams,
    PlanSet,
    TransferEdge,
    Verdict,
    Wallet,
    World,
)

# --------------------------------------------------------------------------
# Greedy planner (M1, M2, M7 baseline)
# --------------------------------------------------------------------------


def _greedy_transfers(
    world: World,
    edges_into: list[TransferEdge],
    balances: dict[str, int],
    deficit: int,
    *,
    quantize: bool,
) -> TransferUse | None:
    """Fund a deficit from the single cheapest-valued source with a direct edge."""
    for edge in sorted(edges_into, key=lambda e: (world.mcpp(e.from_program), e.id)):
        available = balances.get(edge.from_program, 0)
        if available <= 0:
            continue
        raw = ceil_div(deficit * edge.ratio_from, edge.ratio_to)
        sent = max(ceil_to_multiple(raw, edge.increment_from), edge.min_from) if quantize else raw
        if quantize and sent > available:
            continue
        return TransferUse.build(edge, sent)
    return None


def greedy_plan_set(
    case: dict[str, Any],
    *,
    quantize: bool = True,
    gating: bool = True,
    world: World | None = None,
) -> PlanSet:
    """Sequential per-booking funding, direct edges only, no splits, no hops."""
    world = world or eval_world(case["world"])
    goal = GoalSpec.model_validate(case["goal"])
    today = date.fromisoformat(case["today"])
    params = params_of(case)
    wallet = Wallet(cards=list(case["cards"]), balances=dict(case["balances"]))
    active = active_subgraph(world, wallet.cards if gating else [c.id for c in world.cards], today)
    opening = {p: n for p, n in wallet.balances.items() if n}

    drafts: list[PlanDraft] = []
    if goal.kind is GoalKind.CASH:
        from pointsmax.engine.search import SearchStats

        drafts = cash_drafts(world, active, opening, goal, params, SearchStats())
        candidate_sets = 1 if cash_goal_has_options(active, opening, goal) else 0
    else:
        booking_sets = enumerate_booking_sets(goal, world, active, today)
        candidate_sets = len(booking_sets)
        for bookings in booking_sets:
            balances = dict(opening)
            uses: list[TransferUse] = []
            feasible = True
            for program, need in sorted(aggregate_needs(bookings).items()):
                deficit = need - balances.get(program, 0)
                balances[program] = balances.get(program, 0) - need
                if deficit <= 0:
                    continue
                use = _greedy_transfers(
                    world, list(active.edges_into(program)), balances, deficit, quantize=quantize
                )
                if use is None:
                    feasible = False
                    break
                balances[use.from_program] = balances.get(use.from_program, 0) - use.sent
                balances[program] = balances.get(program, 0) + use.delivered
                uses.append(use)
            if not feasible:
                continue
            try:
                value = value_plan(opening, uses, bookings, world.mcpp_map())
                arrival, hop_index = _arrival_and_hops(tuple(uses))
            except Exception:
                continue
            drafts.append(
                PlanDraft(
                    bookings=bookings,
                    transfers=tuple(uses),
                    value=value,
                    arrival_days=dict(arrival),
                    hop_index=dict(hop_index),
                    feasible_in_days=max(
                        (arrival.get(b.program_id, 0) for b in bookings), default=0
                    ),
                )
            )

    from pointsmax.engine.advisor import rank_drafts, select_output

    ordered = rank_drafts(world, goal, drafts)
    verdict = choose_verdict(goal.kind, candidate_sets=candidate_sets, feasible_drafts=ordered)
    plans: list[Plan] = []
    if verdict not in (Verdict.INSUFFICIENT_POINTS, Verdict.NO_MATCHING_AWARD):
        for rank, (draft, is_comparator) in enumerate(
            select_output(ordered, goal, params.top_k), start=1
        ):
            plans.append(
                build_plan(
                    world, goal, draft, today, params, rank=rank, is_comparator=is_comparator
                )
            )
    return PlanSet(
        world_version=world.version.version,
        world_hash=world.version.content_hash,
        today=today,
        params=params,
        verdict=verdict,
        disclaimer=DISCLAIMER,
        plans=plans,
    )


def greedy_runner(*, quantize: bool = True, gating: bool = True) -> Any:
    """A ``score_scenarios``-compatible runner backed by the greedy planner."""

    def run(case: dict[str, Any], **kwargs: Any) -> EngineRun:
        import json

        world = kwargs.get("world")
        if world is None and "world" in case and isinstance(case["world"], dict):
            from pointsmax.adapters.world_provider import build_world

            world = build_world(case["world"], validate=False)
        plan_set = greedy_plan_set(case, quantize=quantize, gating=gating, world=world)
        objective = None
        if plan_set.plans:
            top = plan_set.plans[0]
            objective = (
                (top.cash_received_cents or 0)
                if case["goal"]["kind"] == "cash"
                else top.net_value_cents
            )
        return EngineRun(
            plan_set=plan_set,
            forms=[json.loads(json.dumps(p.canonical_form())) for p in plan_set.plans],
            comparators=[p.is_comparator for p in plan_set.plans],
            verdict=str(plan_set.verdict),
            objective_cents=objective,
        )

    return run


def greedy_random_runner(case: dict[str, Any]) -> bool:
    """M7 baseline: greedy against a committed random case's oracle answer."""
    from pointsmax.adapters.world_provider import build_world

    world = build_world(case["world"], validate=False)
    run = greedy_runner()(case, world=world)
    expected = case["expected"]
    return (
        run.verdict == expected["verdict"]
        and run.objective_cents == expected["objective_cents"]
        and len(run.forms) == expected["plan_count"]
        and (run.forms[0] if run.forms else None) == expected["top_plan_canonical_form"]
    )


# --------------------------------------------------------------------------
# cpp-first re-ranker (M3 baseline)
# --------------------------------------------------------------------------


def cpp_first(plans: list[Plan]) -> list[Plan]:
    """Correct plans, wrong objective: rank by realized cents-per-point, ties by rank."""
    ordered = sorted(
        enumerate(plans), key=lambda item: (-(item[1].realized_cpp_milli or -1), item[0])
    )
    return [plan for _, plan in ordered]


# --------------------------------------------------------------------------
# Keyword-only parser (M5 baseline)
# --------------------------------------------------------------------------

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


@dataclass
class KeywordParser:
    """Exact city-name tokens only: no aliases, no month-year resolution, no error typing."""

    world: World
    names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.names = {entry.name.lower(): entry.city_code for entry in self.world.gazetteer}

    def parse(
        self,
        text: str,
        *,
        today: date,
        home_city: str | None = None,
        default_passengers: int = 1,
    ) -> GoalSpec | ParseError:
        lowered = text.lower()
        found: list[tuple[int, str]] = []
        for name, code in sorted(self.names.items(), key=lambda kv: -len(kv[0])):
            position = lowered.find(name)
            if position >= 0 and all(code != c for _, c in found):
                found.append((position, code))
        found.sort()
        month = None
        for name, number in _MONTH_NAMES.items():
            if name in lowered:
                month = f"{today.year:04d}-{number:02d}"  # no next-occurrence resolution
                break
        if "cash" in lowered:
            return cash_goal(raw_text=text)
        if len(found) < 2 or month is None:
            return ParseError(raw_text=text, message="unparsed")
        cabin = None
        for word in ("business", "economy", "first"):
            if re.search(rf"\b{word}\b", lowered):
                cabin = word
                break
        start, _end = month_bounds(month)
        _ = start
        return flight_goal(
            origin_city=found[0][1],
            dest_city=found[1][1],
            month=month,
            cabin=cabin,  # type: ignore[arg-type]
            round_trip="round" in lowered,
            passengers=default_passengers,
            raw_text=text,
        )


# --------------------------------------------------------------------------
# Fee/tier/stranding-ignoring calculator (M4 baseline)
# --------------------------------------------------------------------------


@dataclass
class NaivePlanValue:
    gross_value_cents: int
    cash_outlay_cents: int
    points_cost_cents: int
    net_value_cents: int
    cash_received_cents: int | None
    realized_cpp_milli: int | None
    ending_holdings: dict[str, int]


class NaiveCalculator:
    """What a casual cpp spreadsheet computes: no transfer fees, no tier bonuses,
    and leftovers written off instead of credited at the destination valuation."""

    @staticmethod
    def value(
        opening: dict[str, int], transfers: list[TransferUse], bookings: list[Any], mcpp: Any
    ) -> NaivePlanValue:
        gross = sum(b.value_cents for b in bookings)
        booking_fees = sum(b.fees_cents for b in bookings)
        spent: dict[str, int] = {}
        for use in transfers:
            spent[use.from_program] = spent.get(use.from_program, 0) + use.sent
        paid_from_balance = {b.program_id for b in bookings} - {t.to_program for t in transfers}
        for booking in bookings:
            if booking.program_id in paid_from_balance:
                spent[booking.program_id] = spent.get(booking.program_id, 0) + booking.points
        points_cost = sum((n * mcpp[p]) // 1000 for p, n in spent.items())
        holdings = dict(opening)
        for use in transfers:
            holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
            holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered
        for booking in bookings:
            holdings[booking.program_id] = holdings.get(booking.program_id, 0) - booking.points
        booking_points = sum(b.points for b in bookings)
        cash = [b for b in bookings if str(b.kind) == "redeem_cash"]
        return NaivePlanValue(
            gross_value_cents=gross,
            cash_outlay_cents=booking_fees,
            points_cost_cents=points_cost,
            net_value_cents=gross - booking_fees - points_cost,
            cash_received_cents=sum(b.value_cents for b in cash) if cash else None,
            realized_cpp_milli=(
                (gross - booking_fees) * 1000 // booking_points if booking_points else None
            ),
            ending_holdings=holdings,
        )

    @staticmethod
    def delivered(edge: TransferEdge, sent: int) -> int:
        return sent * edge.ratio_to // edge.ratio_from  # tier bonus ignored

    @staticmethod
    def fee(edge: TransferEdge, sent: int) -> int:
        return 0  # transfer fees ignored


# --------------------------------------------------------------------------
# The exact-but-unpruned engine (M6 baseline)
# --------------------------------------------------------------------------


def unpruned_shipped_results() -> list[Any]:
    from metrics import score_shipped

    return score_shipped(pruning=False)


__all__ = [
    "KeywordParser",
    "NaiveCalculator",
    "cpp_first",
    "greedy_plan_set",
    "greedy_random_runner",
    "greedy_runner",
    "unpruned_shipped_results",
]


_ = PlanParams  # re-exported for callers building custom baseline params
