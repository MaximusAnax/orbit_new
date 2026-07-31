"""Metric implementations and the independent plan validator (EVALS.md).

Every metric here runs the engine through the offline adapters against committed
fixtures and scores it against committed ground truth.  The validator used by M2
and M6 is a **third** implementation: it reads the raw world JSON (never the
engine's loaded models, never the oracle's) and re-derives gating, quantization,
delivery, fees, arrival times and the canonical step order from the spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from expected_caveats import normalize as normalize_caveats
from pointsmax.adapters.goal_parser import RuleBasedGoalParser
from pointsmax.adapters.world_provider import CommittedWorldProvider, build_world
from pointsmax.engine.advisor import compute_plan_set, plan_set_fingerprint
from pointsmax.engine.money import delivered_points, transfer_fee_cents
from pointsmax.engine.search import SearchBudgetExceeded
from pointsmax.engine.value import Booking, TransferUse, value_plan
from pointsmax.models import (
    GoalSpec,
    ParseError,
    Plan,
    PlanParams,
    PlanSet,
    StepKind,
    Wallet,
    World,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
KIND_RANK = {"transfer": 0, "book_award": 1, "book_portal": 2, "redeem_cash": 3}
LIQUID_METHODS = {"statement_credit", "bank_deposit"}


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


@lru_cache(maxsize=None)
def world_files(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "worlds" / f"{name}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def eval_world(name: str) -> World:
    return build_world(world_files(name), validate=True)


@lru_cache(maxsize=None)
def shipped_files() -> dict[str, Any]:
    return CommittedWorldProvider().raw_files()


@lru_cache(maxsize=None)
def shipped_world() -> World:
    return CommittedWorldProvider().load()


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def search_cases() -> list[dict[str, Any]]:
    return _load("search_cases.json")["cases"]


def random_cases() -> list[dict[str, Any]]:
    return _load("random_cases.json")["cases"]


def accounting_cases() -> list[dict[str, Any]]:
    return _load("accounting_cases.json")


def parser_cases() -> list[dict[str, Any]]:
    return _load("parser_cases.json")


def shipped_goals() -> list[dict[str, Any]]:
    return _load("shipped_world_goals.json")


def exec_scripts() -> list[dict[str, Any]]:
    return _load("exec_scripts.json")


# --------------------------------------------------------------------------
# Running the engine on a scenario
# --------------------------------------------------------------------------


@dataclass
class EngineRun:
    """One engine invocation, reduced to what the metrics compare."""

    plan_set: PlanSet | None
    error: str | None = None
    forms: list[list[list]] = field(default_factory=list)
    comparators: list[bool] = field(default_factory=list)
    verdict: str = ""
    objective_cents: int | None = None
    expansions: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def params_of(case: dict[str, Any]) -> PlanParams:
    return PlanParams(**case.get("params", {}))


def run_engine(
    case: dict[str, Any],
    *,
    world: World | None = None,
    pruning: bool = True,
) -> EngineRun:
    """Compute the engine's plan set for a scenario, capturing budget failures."""
    world = world or eval_world(case["world"])
    goal = GoalSpec.model_validate(case["goal"])
    wallet = Wallet(cards=list(case["cards"]), balances=dict(case["balances"]))
    try:
        plan_set = compute_plan_set(
            world=world,
            wallet=wallet,
            goal=goal,
            today=date.fromisoformat(case["today"]),
            params=params_of(case),
            pruning=pruning,
        )
    except SearchBudgetExceeded as exc:
        return EngineRun(plan_set=None, error=f"search_budget_exceeded: {exc.expansions}")
    objective = None
    if plan_set.plans:
        top = plan_set.plans[0]
        objective = (
            top.cash_received_cents or 0
            if goal.kind.value == "cash"
            else top.net_value_cents
        )
    return EngineRun(
        plan_set=plan_set,
        forms=[json.loads(json.dumps(p.canonical_form())) for p in plan_set.plans],
        comparators=[p.is_comparator for p in plan_set.plans],
        verdict=str(plan_set.verdict),
        objective_cents=objective,
        expansions=plan_set.expansions,
    )


def plan_caveats(plan: Plan) -> list[dict[str, Any]]:
    """The engine's caveats reduced to the multiset M3 compares."""
    return normalize_caveats([{"code": str(c.code), "params": c.params} for c in plan.caveats])


# --------------------------------------------------------------------------
# The independent plan validator (M2, M6b)
# --------------------------------------------------------------------------


class RawWorld:
    """Indexes the raw world JSON — no engine or oracle types involved."""

    def __init__(self, files: dict[str, Any]) -> None:
        self.programs = {p["id"]: p for p in files["programs.json"]}
        self.cards = {c["id"]: c for c in files["cards.json"]}
        self.edges = {e["id"]: e for e in files["transfers.json"]}
        self.options = {o["id"]: o for o in files["cashouts.json"]}
        self.offers = {o["id"]: o for o in files["awards.json"]}
        self.mcpp = {v["program_id"]: v["cpp_milli"] for v in files["valuations.json"]}
        self.place: dict[str, str] = {}
        for entry in files["gazetteer.json"]:
            self.place[entry["city_code"]] = entry["city_code"]
            for airport in entry["airports"]:
                self.place[airport] = entry["city_code"]
        self.fares: dict[tuple, int] = {}
        for row in files["reference_fares.json"]:
            if row["kind"] == "flight":
                key: tuple = (
                    "flight",
                    row["origin_city"],
                    row["dest_city"],
                    row["cabin"],
                    bool(row["round_trip"]),
                    row["month"],
                )
            else:
                key = ("stay", row["city"], row["month"])
            self.fares[key] = row["fare_cents"]


def _active(row: dict[str, Any], today: date) -> bool:
    start = row.get("valid_from")
    end = row.get("valid_to")
    if start and today < date.fromisoformat(start):
        return False
    return not (end and today > date.fromisoformat(end))


def _raw_delivered(edge: dict[str, Any], sent: int) -> int:
    out = sent * edge["ratio_to"] // edge["ratio_from"]
    if edge["bonus_per_from"] and edge["bonus_to"]:
        out += edge["bonus_to"] * (sent // edge["bonus_per_from"])
    return out


def _raw_fee(edge: dict[str, Any], sent: int) -> int:
    if sent <= 0 or not edge["fee_mcpp"]:
        return 0
    fee = -((-sent * edge["fee_mcpp"]) // 1000)
    cap = edge["fee_cap_cents"]
    return min(fee, cap) if cap is not None else fee


def _raw_portal_points(fare_cents: int, option: dict[str, Any]) -> int:
    raw = -((-fare_cents * 1000) // option["cpp_milli"])
    inc = option["increment"]
    return max(-((-raw) // inc) * inc, option["min_points"])


def validate_plan(  # noqa: PLR0912, PLR0915 - one branch per documented check
    files: dict[str, Any],
    case: dict[str, Any],
    plan: Plan,
) -> list[str]:
    """Re-check an emitted plan against the raw world; returns the violations found."""
    raw = RawWorld(files)
    today = date.fromisoformat(case["today"])
    goal = case["goal"]
    held = set(case["cards"])
    enabled = {
        raw.cards[c]["program_id"] for c in held if c in raw.cards and raw.cards[c]["enables_transfer"]
    }
    problems: list[str] = []
    steps = list(plan.steps)

    # -- canonical order and seq numbering (FR-9) --------------------------
    def sort_key(step: Any) -> tuple:
        return (
            KIND_RANK[str(step.kind)],
            step.hop_index,
            step.from_program or "",
            step.to_program or "",
            step.edge_id or "",
            step.offer_id or "",
            step.cashout_id or "",
            step.points_sent,
        )

    if [s.seq for s in steps] != list(range(1, len(steps) + 1)):
        problems.append("step seq numbers are not 1..N")
    if [sort_key(s) for s in steps] != sorted(sort_key(s) for s in steps):
        problems.append("steps are not in FR-9 canonical order")

    # -- transfers ---------------------------------------------------------
    seen_edges: set[str] = set()
    arrival: dict[str, int] = {}
    for step in steps:
        if str(step.kind) != "transfer":
            continue
        edge = raw.edges.get(step.edge_id or "")
        if edge is None:
            problems.append(f"unknown edge {step.edge_id}")
            continue
        if step.edge_id in seen_edges:
            problems.append(f"more than one transfer step on edge {step.edge_id} (FR-7b)")
        seen_edges.add(str(step.edge_id))
        if not _active(edge, today):
            problems.append(f"edge {edge['id']} is not active on {today}")
        source_kind = raw.programs[edge["from_program"]]["kind"]
        if source_kind == "bank" and edge["from_program"] not in enabled:
            problems.append(f"edge {edge['id']} needs a card enabling {edge['from_program']}")
        if step.points_sent < edge["min_from"]:
            problems.append(f"edge {edge['id']}: {step.points_sent} is below min_from")
        if step.points_sent % edge["increment_from"] != 0:
            problems.append(f"edge {edge['id']}: {step.points_sent} breaks increment_from")
        expected_delivered = _raw_delivered(edge, step.points_sent)
        if step.points_delivered != expected_delivered:
            problems.append(
                f"edge {edge['id']}: delivered {step.points_delivered} != {expected_delivered}"
            )
        expected_fee = _raw_fee(edge, step.points_sent)
        if step.fees_cents != expected_fee:
            problems.append(f"edge {edge['id']}: fee {step.fees_cents} != {expected_fee}")
        if step.eta_days != edge["time_days"]:
            problems.append(f"edge {edge['id']}: eta_days {step.eta_days} != {edge['time_days']}")

    # arrival_days over the funding DAG, in canonical (source-before-use) order
    for step in steps:
        if str(step.kind) != "transfer":
            continue
        edge = raw.edges[str(step.edge_id)]
        start = arrival.get(edge["from_program"], 0)
        arrival[edge["to_program"]] = max(
            arrival.get(edge["to_program"], 0), start + edge["time_days"]
        )

    # -- bookings ----------------------------------------------------------
    pax = goal.get("passengers") or 1
    nights = goal.get("nights") or 1
    month = str(goal.get("travel_window_start") or "")[:7]
    for step in steps:
        kind = str(step.kind)
        if kind == "transfer":
            continue
        program = step.from_program
        deadline: date | None = None
        if kind == "book_award":
            offer = raw.offers.get(step.offer_id or "")
            if offer is None:
                problems.append(f"unknown offer {step.offer_id}")
                continue
            if offer["program_id"] != program:
                problems.append(f"offer {offer['id']} is not paid from {program}")
            if offer["kind"] == "flight":
                if step.points_sent != offer["points_price"] * pax:
                    problems.append(f"offer {offer['id']}: wrong points for {pax} passengers")
                if step.fees_cents != offer["fees_cents"] * pax:
                    problems.append(f"offer {offer['id']}: wrong fees for {pax} passengers")
                if offer["seats_available"] is not None and pax > offer["seats_available"]:
                    problems.append(f"offer {offer['id']}: {pax} passengers exceed seats")
            else:
                if step.points_sent != offer["points_price"] * nights:
                    problems.append(f"offer {offer['id']}: wrong points for {nights} nights")
                if step.fees_cents != offer["fees_cents"] * nights:
                    problems.append(f"offer {offer['id']}: wrong fees for {nights} nights")
            bounds = [date.fromisoformat(offer["travel_window_end"])]
            if offer.get("bookable_until"):
                bounds.append(date.fromisoformat(offer["bookable_until"]))
            if goal.get("book_by"):
                bounds.append(date.fromisoformat(goal["book_by"]))
            deadline = min(bounds)
        else:
            option = raw.options.get(step.cashout_id or "")
            if option is None:
                problems.append(f"unknown cashout option {step.cashout_id}")
                continue
            if option["program_id"] != program:
                problems.append(f"option {option['id']} is not on {program}")
            if not _active(option, today):
                problems.append(f"option {option['id']} is not active on {today}")
            if option["requires_card"] is not None and option["requires_card"] not in held:
                problems.append(f"option {option['id']} requires a card that is not held")
            if kind == "redeem_cash":
                if option["method"] not in LIQUID_METHODS:
                    problems.append(f"cash goal used non-liquid method {option['method']}")
                if step.points_sent % option["increment"] != 0:
                    problems.append(f"option {option['id']}: breaks redemption increment")
                if step.points_sent < option["min_points"]:
                    problems.append(f"option {option['id']}: below min_points")
            else:
                if option["method"] != "portal_travel":
                    problems.append(f"portal booking used method {option['method']}")
                if goal["kind"] == "flight":
                    fare = raw.fares.get(
                        (
                            "flight",
                            goal["origin_city"],
                            goal["dest_city"],
                            goal.get("cabin"),
                            bool(goal.get("round_trip")),
                            month,
                        )
                    )
                    reverse = raw.fares.get(
                        (
                            "flight",
                            goal["dest_city"],
                            goal["origin_city"],
                            goal.get("cabin"),
                            False,
                            month,
                        )
                    )
                    one_way = raw.fares.get(
                        (
                            "flight",
                            goal["origin_city"],
                            goal["dest_city"],
                            goal.get("cabin"),
                            False,
                            month,
                        )
                    )
                    prices = {
                        _raw_portal_points(f * pax, option)
                        for f in (fare, reverse, one_way)
                        if f is not None
                    }
                    if step.points_sent not in prices:
                        problems.append(
                            f"option {option['id']}: portal price {step.points_sent} "
                            "matches no reference fare for this goal"
                        )
                else:
                    fare = raw.fares.get(("stay", goal["city"], month))
                    if fare is None or step.points_sent != _raw_portal_points(
                        fare * nights, option
                    ):
                        problems.append(f"option {option['id']}: wrong portal stay price")
                deadline = (
                    date.fromisoformat(goal["travel_window_end"])
                    if goal.get("travel_window_end")
                    else None
                )
                if goal.get("book_by"):
                    book_by = date.fromisoformat(goal["book_by"])
                    deadline = book_by if deadline is None else min(deadline, book_by)
        if deadline is not None:
            landed = today + timedelta(days=arrival.get(str(program), 0))
            if landed > deadline:
                problems.append(
                    f"step {step.seq}: points land {landed}, after the deadline {deadline}"
                )

    # -- balances never go negative in seq order ---------------------------
    balances = dict(case["balances"])
    for step in steps:
        program = str(step.from_program)
        balances[program] = balances.get(program, 0) - step.points_sent
        if balances[program] < 0:
            problems.append(f"step {step.seq}: {program} goes negative")
        if str(step.kind) == "transfer":
            target = str(step.to_program)
            balances[target] = balances.get(target, 0) + int(step.points_delivered or 0)

    return problems


# --------------------------------------------------------------------------
# M1 / M2 / M3 — the curated search scenarios
# --------------------------------------------------------------------------


@dataclass
class ScenarioScore:
    case_id: str
    tier: str
    optimal: bool
    valid_plans: int
    expected_plans: int
    ranking_ok: bool
    detail: str = ""


def score_scenarios(
    cases: list[dict[str, Any]] | None = None,
    *,
    runner: Any = None,
    reranker: Any = None,
) -> list[ScenarioScore]:
    """Score every curated scenario for M1, M2 and M3 in one pass.

    ``runner`` defaults to the engine; baselines pass their own.  ``reranker``
    (used by the cpp-first baseline) may reorder the emitted plans before the M3
    comparison.
    """
    cases = cases if cases is not None else search_cases()
    runner = runner or run_engine
    scores: list[ScenarioScore] = []
    for case in cases:
        expected = case["expected"]
        run = runner(case)
        expected_plans = len(expected["plans"])
        if not run.ok or run.plan_set is None:
            scores.append(
                ScenarioScore(case["id"], case["tier"], False, 0, expected_plans, False, run.error or "")
            )
            continue

        optimal = run.verdict == expected["verdict"] and (
            run.objective_cents == expected["objective_cents"]
        )

        files = world_files(case["world"])
        valid = 0
        broke = False
        for plan in run.plan_set.plans:
            if validate_plan(files, case, plan):
                broke = True
                break
            valid += 1
        valid_plans = 0 if broke else min(valid, expected_plans)

        plans = list(run.plan_set.plans)
        if reranker is not None:
            plans = reranker(plans)
        forms = [json.loads(json.dumps(p.canonical_form())) for p in plans]
        ranking_ok = (
            forms == [p["canonical_form"] for p in expected["plans"]]
            and [p.is_comparator for p in plans] == [p["is_comparator"] for p in expected["plans"]]
            and run.verdict == expected["verdict"]
            and all(
                plan_caveats(plan) == exp["caveats"]
                for plan, exp in zip(plans, expected["plans"], strict=False)
            )
        )
        scores.append(
            ScenarioScore(case["id"], case["tier"], optimal, valid_plans, expected_plans, ranking_ok)
        )
    return scores


def m1(scores: list[ScenarioScore], tier: str) -> float:
    subset = [s for s in scores if s.tier == tier]
    return (sum(1 for s in subset if s.optimal) / len(subset)) if subset else 0.0


def m2(scores: list[ScenarioScore]) -> float:
    expected = sum(s.expected_plans for s in scores)
    if expected == 0:
        return 1.0
    return sum(s.valid_plans for s in scores) / expected


def m3(scores: list[ScenarioScore]) -> float:
    return (sum(1 for s in scores if s.ranking_ok) / len(scores)) if scores else 0.0


# --------------------------------------------------------------------------
# M4 — value-accounting exactness
# --------------------------------------------------------------------------


def _accounting_inputs(case: dict[str, Any]) -> tuple[World, list[TransferUse], list[Booking]]:
    world = eval_world(case["world"])
    transfers = [
        TransferUse.build(world.edge(row["edge_id"]), row["sent"]) for row in case["transfers"]
    ]
    bookings = [
        Booking(
            kind=StepKind(row["kind"]),
            program_id=row["program_id"],
            points=row["points"],
            fees_cents=row["fees_cents"],
            value_cents=row["value_cents"],
            offer_id=row["offer_id"],
            cashout_id=row["cashout_id"],
        )
        for row in case["bookings"]
    ]
    return world, transfers, bookings


class EngineCalculator:
    """The FR-8 accounting under test: the engine's own value module."""

    @staticmethod
    def value(opening: dict[str, int], transfers: list[Any], bookings: list[Any], mcpp: Any) -> Any:
        return value_plan(opening, transfers, bookings, mcpp)

    @staticmethod
    def delivered(edge: Any, sent: int) -> int:
        return delivered_points(edge, sent)

    @staticmethod
    def fee(edge: Any, sent: int) -> int:
        return transfer_fee_cents(edge, sent)


def score_accounting(case: dict[str, Any], *, calculator: Any = None) -> tuple[bool, str]:
    """True when every expected number matches exactly."""
    world, transfers, bookings = _accounting_inputs(case)
    expected = case["expected"]
    calc = calculator or EngineCalculator
    result = calc.value(case["opening"], transfers, bookings, world.mcpp_map())
    checks = [
        ("gross_value_cents", result.gross_value_cents),
        ("cash_outlay_cents", result.cash_outlay_cents),
        ("points_cost_cents", result.points_cost_cents),
        ("net_value_cents", result.net_value_cents),
        ("realized_cpp_milli", result.realized_cpp_milli),
        ("cash_received_cents", result.cash_received_cents),
    ]
    for name, actual in checks:
        if actual != expected[name]:
            return False, f"{name}: {actual} != {expected[name]}"
    for use, row in zip(transfers, expected["transfers"], strict=True):
        edge = world.edge(use.edge_id)
        if use.sent != row["sent"]:
            return False, f"transfer {row['edge_id']}: sent {use.sent} != {row['sent']}"
        if calc.delivered(edge, use.sent) != row["delivered"]:
            return False, f"transfer {row['edge_id']}: delivered != {row['delivered']}"
        if calc.fee(edge, use.sent) != row["fee_cents"]:
            return False, f"transfer {row['edge_id']}: fee != {row['fee_cents']}"
    stranded = []
    for program in sorted({use.to_program for use in transfers}):
        leftover = result.ending_holdings.get(program, 0)
        if leftover > 0:
            stranded.append(
                {
                    "program": program,
                    "points": leftover,
                    "value_cents": (leftover * world.mcpp(program)) // 1000,
                }
            )
    if stranded != expected["stranded"]:
        return False, f"stranded {stranded} != {expected['stranded']}"
    return True, ""


def m4(cases: list[dict[str, Any]] | None = None, *, calculator: Any = None) -> float:
    cases = cases if cases is not None else accounting_cases()
    passed = sum(1 for case in cases if score_accounting(case, calculator=calculator)[0])
    return passed / len(cases) if cases else 0.0


# --------------------------------------------------------------------------
# M5 — parser exactness
# --------------------------------------------------------------------------

_SPEC_FIELDS = (
    "kind",
    "origin_city",
    "dest_city",
    "cabin",
    "round_trip",
    "passengers",
    "city",
    "nights",
    "travel_window_start",
    "travel_window_end",
    "cash_programs",
    "cash_max_points",
)


def parse_matches(result: GoalSpec | ParseError, truth: dict[str, Any]) -> bool:
    if truth["outcome"] == "error":
        return (
            isinstance(result, ParseError)
            and sorted(result.missing) == sorted(truth["missing"])
            and sorted(result.ambiguous) == sorted(truth["ambiguous"])
        )
    if not isinstance(result, GoalSpec):
        return False
    payload = json.loads(result.model_dump_json())
    spec = truth["spec"]
    for field_name in _SPEC_FIELDS:
        if field_name in spec and payload.get(field_name) != spec[field_name]:
            return False
    return True


def m5(cases: list[dict[str, Any]] | None = None, *, parser: Any = None) -> float:
    cases = cases if cases is not None else parser_cases()
    engine_parser = parser or RuleBasedGoalParser(eval_world("small_a"))
    passed = 0
    for case in cases:
        result = engine_parser.parse(
            case["text"],
            today=date.fromisoformat(case["today"]),
            home_city=case.get("home_city"),
            default_passengers=case.get("default_passengers", 1),
        )
        if parse_matches(result, case["truth"]):
            passed += 1
    return passed / len(cases) if cases else 0.0


# --------------------------------------------------------------------------
# M6 — shipped-world operability
# --------------------------------------------------------------------------


@dataclass
class ShippedResult:
    goal_id: str
    passed: bool
    plans: int
    expansions: int
    seconds: float
    detail: str = ""


def score_shipped(
    goals: list[dict[str, Any]] | None = None, *, pruning: bool = True
) -> list[ShippedResult]:
    """Run the 12 M6 goals against ``data/world/`` and check (a), (b) and (c)."""
    import time

    goals = goals if goals is not None else shipped_goals()
    world = shipped_world()
    files = shipped_files()
    results: list[ShippedResult] = []
    for goal_case in goals:
        params = params_of(goal_case)
        started = time.perf_counter()
        try:
            plan_set = compute_plan_set(
                world=world,
                wallet=Wallet(cards=list(goal_case["cards"]), balances=dict(goal_case["balances"])),
                goal=GoalSpec.model_validate(goal_case["goal"]),
                today=date.fromisoformat(goal_case["today"]),
                params=params,
                pruning=pruning,
            )
        except SearchBudgetExceeded as exc:
            results.append(
                ShippedResult(
                    goal_case["id"], False, 0, exc.expansions, time.perf_counter() - started,
                    "search budget exceeded",
                )
            )
            continue
        elapsed = time.perf_counter() - started
        detail = ""
        ok = plan_set.expansions <= params.max_expansions
        if not ok:
            detail = "expansions above budget"
        for plan in plan_set.plans:
            issues = validate_plan(files, goal_case, plan)
            if issues:
                ok, detail = False, f"plan {plan.rank}: {issues[0]}"
                break
        if ok:
            again = compute_plan_set(
                world=world,
                wallet=Wallet(cards=list(goal_case["cards"]), balances=dict(goal_case["balances"])),
                goal=GoalSpec.model_validate(goal_case["goal"]),
                today=date.fromisoformat(goal_case["today"]),
                params=params,
                pruning=pruning,
            )
            if plan_set_fingerprint(again) != plan_set_fingerprint(plan_set):
                ok, detail = False, "recompute is not byte-identical"
        results.append(
            ShippedResult(
                goal_case["id"], ok, len(plan_set.plans), plan_set.expansions, elapsed, detail
            )
        )
    return results


def m6(results: list[ShippedResult]) -> float:
    return (sum(1 for r in results if r.passed) / len(results)) if results else 0.0


# --------------------------------------------------------------------------
# M7 — randomized differential optimality
# --------------------------------------------------------------------------


def score_random(cases: list[dict[str, Any]] | None = None, *, runner: Any = None) -> list[bool]:
    cases = cases if cases is not None else random_cases()
    runner = runner or _run_random
    return [runner(case) for case in cases]


def _run_random(case: dict[str, Any]) -> bool:
    world = build_world(case["world"], validate=True)
    run = run_engine(case, world=world)
    if not run.ok or run.plan_set is None:
        return False
    expected = case["expected"]
    if run.verdict != expected["verdict"]:
        return False
    if run.objective_cents != expected["objective_cents"]:
        return False
    if len(run.forms) != expected["plan_count"]:
        return False
    top = run.forms[0] if run.forms else None
    return top == expected["top_plan_canonical_form"]


def m7(results: list[bool]) -> float:
    return (sum(1 for r in results if r) / len(results)) if results else 0.0
