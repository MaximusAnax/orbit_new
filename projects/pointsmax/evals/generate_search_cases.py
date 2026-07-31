"""Solve the 64 curated scenarios with the oracle and commit the answers.

``python evals/generate_search_cases.py --seed 20260731`` writes
``fixtures/search_cases.json``.  Regeneration is byte-identical (the oracle is
deterministic and the seed only labels the fixture), so CI can re-run it and
diff.  Four self-checks must pass **before** any scenario is written
(EVALS "Files" table):

1. the oracle reproduces all 30 ``accounting_cases.json`` expected values;
2. the oracle reproduces the six hand-derived scenarios' hand-worked answers;
3. the independent M2 validator passes on every oracle-expected plan;
4. the oracle-tractability budget holds for every scenario.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import oracle
from author_fixtures import ACCOUNTING_CASES
from expected_caveats import expected_caveats
from scenarios import SEARCH_SCENARIOS, check_composition

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: EVALS "Oracle-tractability budget"
MAX_ENUMERATIONS = 2_000_000
MAX_AMOUNTS_PER_EDGE = 60
MAX_POSITIVE_BALANCES = 3
MAX_BOOKING_SETS = 12
MAX_ACTIVE_EDGES = 10
#: EVALS gates table: at least this many scenarios must break a cpp-first ranker.
MIN_CPP_CONFLICTS = 24


def load_world(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "worlds" / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Turning an oracle plan into validator-shaped steps
# --------------------------------------------------------------------------


def oracle_steps(plan: oracle.OraclePlan, world: oracle.OracleWorld) -> list[SimpleNamespace]:
    """The plan's steps in FR-9 canonical order, shaped for ``validate_plan``."""
    rows: list[tuple[tuple, SimpleNamespace]] = []
    for use in plan.transfers:
        hop = plan.hop_index.get(use.edge_id, 0)
        rows.append(
            (
                (0, hop, use.from_program, use.to_program, use.edge_id, "", "", use.sent),
                SimpleNamespace(
                    seq=0,
                    kind="transfer",
                    hop_index=hop,
                    from_program=use.from_program,
                    to_program=use.to_program,
                    edge_id=use.edge_id,
                    offer_id=None,
                    cashout_id=None,
                    points_sent=use.sent,
                    points_delivered=use.delivered,
                    fees_cents=use.fee_cents,
                    eta_days=use.time_days,
                ),
            )
        )
    rank = {"book_award": 1, "book_portal": 2, "redeem_cash": 3}
    for booking in plan.bookings:
        rows.append(
            (
                (
                    rank[booking.kind],
                    0,
                    booking.program_id,
                    "",
                    "",
                    booking.offer_id or "",
                    booking.cashout_id or "",
                    booking.points,
                ),
                SimpleNamespace(
                    seq=0,
                    kind=booking.kind,
                    hop_index=0,
                    from_program=booking.program_id,
                    to_program=None,
                    edge_id=None,
                    offer_id=booking.offer_id,
                    cashout_id=booking.cashout_id,
                    points_sent=booking.points,
                    points_delivered=None,
                    fees_cents=booking.fees_cents,
                    eta_days=0,
                ),
            )
        )
    rows.sort(key=lambda row: row[0])
    steps = []
    for index, (_key, step) in enumerate(rows, start=1):
        step.seq = index
        steps.append(step)
    _ = world
    return steps


# --------------------------------------------------------------------------
# Self-check 1: accounting cases
# --------------------------------------------------------------------------


def check_accounting() -> None:
    for case in ACCOUNTING_CASES:
        files = load_world(case["world"])
        world = oracle.OracleWorld(files)
        edges = {e["id"]: e for e in world.edges}
        transfers = tuple(
            oracle.Transfer(
                edge_id=row["edge_id"],
                from_program=edges[row["edge_id"]]["from_program"],
                to_program=edges[row["edge_id"]]["to_program"],
                sent=row["sent"],
                delivered=oracle.delivered(edges[row["edge_id"]], row["sent"]),
                fee_cents=oracle.transfer_fee(edges[row["edge_id"]], row["sent"]),
                time_days=edges[row["edge_id"]]["time_days"],
            )
            for row in case["transfers"]
        )
        bookings = tuple(
            oracle.Booking(
                kind=row["kind"],
                program_id=row["program_id"],
                points=row["points"],
                fees_cents=row["fees_cents"],
                value_cents=row["value_cents"],
                offer_id=row["offer_id"],
                cashout_id=row["cashout_id"],
            )
            for row in case["bookings"]
        )
        plan = oracle.price_plan(world, case["opening"], transfers, bookings)
        assert plan is not None, f"{case['id']}: the oracle could not price the plan"
        exp = case["expected"]
        for field, actual in (
            ("gross_value_cents", plan.gross_value_cents),
            ("cash_outlay_cents", plan.cash_outlay_cents),
            ("points_cost_cents", plan.points_cost_cents),
            ("net_value_cents", plan.net_value_cents),
            ("realized_cpp_milli", plan.realized_cpp_milli),
            ("cash_received_cents", plan.cash_received_cents),
        ):
            assert actual == exp[field], (
                f"{case['id']}: oracle {field} {actual} != hand-computed {exp[field]}"
            )
        for use, row in zip(transfers, exp["transfers"], strict=True):
            assert use.delivered == row["delivered"], f"{case['id']}: delivered mismatch"
            assert use.fee_cents == row["fee_cents"], f"{case['id']}: fee mismatch"
        stranded = [
            {
                "program": program,
                "points": plan.ending_holdings.get(program, 0),
                "value_cents": (plan.ending_holdings.get(program, 0) * world.mcpp[program]) // 1000,
            }
            for program in sorted({t.to_program for t in transfers})
            if plan.ending_holdings.get(program, 0) > 0
        ]
        assert stranded == exp["stranded"], (
            f"{case['id']}: oracle stranding {stranded} != hand-computed {exp['stranded']}"
        )
    print(f"self-check 1: oracle reproduces all {len(ACCOUNTING_CASES)} accounting cases")


# --------------------------------------------------------------------------
# Self-check 4: tractability budget
# --------------------------------------------------------------------------


def check_budget(case: dict[str, Any], result: oracle.OracleResult, files: dict[str, Any]) -> None:
    world = oracle.OracleWorld(files)
    today = oracle._date(case["today"])
    edges, options = oracle.active_subgraph(world, case["cards"], today)
    positive = [p for p, n in case["balances"].items() if n > 0]
    assert len(positive) <= MAX_POSITIVE_BALANCES, (
        f"{case['id']}: {len(positive)} programs with a positive balance"
    )
    assert result.enumerations <= MAX_ENUMERATIONS, (
        f"{case['id']}: {result.enumerations} oracle enumerations"
    )
    if case["goal"]["kind"] != "cash":
        sets = oracle.enumerate_booking_sets(world, options, case["goal"], today)
        assert len(sets) <= MAX_BOOKING_SETS, f"{case['id']}: {len(sets)} candidate booking sets"
    reachable = {p for p in positive}
    for _ in range(case.get("params", {}).get("max_hops", 2)):
        for edge in edges:
            if edge["from_program"] in reachable:
                reachable.add(edge["to_program"])
    usable = [e for e in edges if e["from_program"] in reachable]
    assert len(usable) <= MAX_ACTIVE_EDGES, f"{case['id']}: {len(usable)} reachable active edges"
    for edge in usable:
        balance = case["balances"].get(edge["from_program"], 0)
        amounts = balance // edge["increment_from"]
        assert amounts <= MAX_AMOUNTS_PER_EDGE, (
            f"{case['id']}: edge {edge['id']} admits {amounts} valid sent amounts"
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def solve_case(case: dict[str, Any]) -> tuple[dict[str, Any], oracle.OracleResult]:
    files = load_world(case["world"])
    result = oracle.solve(
        files,
        cards=case["cards"],
        balances=case["balances"],
        goal=case["goal"],
        today=case["today"],
        params=case.get("params"),
        enumeration_ceiling=MAX_ENUMERATIONS,
    )
    world = oracle.OracleWorld(files)
    today = oracle._date(case["today"])
    plans = []
    for plan in result.plans:
        plans.append(
            {
                "canonical_form": plan.canonical_form,
                "is_comparator": plan.is_comparator,
                "net_value_cents": plan.net_value_cents,
                "cash_received_cents": plan.cash_received_cents,
                "realized_cpp_milli": plan.realized_cpp_milli,
                "points_spent": plan.points_spent,
                "feasible_in_days": plan.feasible_in_days,
                "caveats": expected_caveats(
                    world, plan, case["goal"], today, case.get("params", {})
                ),
            }
        )
    payload = dict(case)
    payload["expected"] = {
        "verdict": result.verdict,
        "objective_cents": result.objective_cents,
        "plans": plans,
        "candidate_sets": result.candidate_sets,
        "oracle_enumerations": result.enumerations,
    }
    return payload, result


def cpp_conflict(payload: dict[str, Any]) -> bool:
    """True when ranking by realized cpp would reorder this scenario's plans."""
    plans = payload["expected"]["plans"]
    if len(plans) < 2:
        return False
    order = list(range(len(plans)))
    by_cpp = sorted(
        order, key=lambda i: (-(plans[i]["realized_cpp_milli"] or -1), i)
    )
    return by_cpp != order


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--out", type=Path, default=FIXTURES / "search_cases.json")
    args = parser.parse_args(argv)

    check_composition()
    check_accounting()

    from metrics import validate_plan  # imported late: needs the package installed

    payloads: list[dict[str, Any]] = []
    total_enumerations = 0
    max_enumerations = 0
    conflicts = 0
    for case in SEARCH_SCENARIOS:
        payload, result = solve_case(case)
        files = load_world(case["world"])
        check_budget(case, result, files)
        world = oracle.OracleWorld(files)
        for plan in result.plans:
            steps = oracle_steps(plan, world)
            issues = validate_plan(files, case, SimpleNamespace(steps=steps))
            assert not issues, f"{case['id']}: oracle plan fails the validator: {issues}"
        if "hand" in case:
            hand = case["hand"]
            assert result.verdict == hand["verdict"], (
                f"{case['id']}: oracle verdict {result.verdict} != hand {hand['verdict']}"
            )
            assert result.objective_cents == hand["objective_cents"], (
                f"{case['id']}: oracle objective {result.objective_cents} != hand "
                f"{hand['objective_cents']}"
            )
            assert len(result.plans) == hand["plan_count"], (
                f"{case['id']}: oracle emitted {len(result.plans)} plans, hand says "
                f"{hand['plan_count']}"
            )
        total_enumerations += result.enumerations
        max_enumerations = max(max_enumerations, result.enumerations)
        conflicts += cpp_conflict(payload)
        payloads.append(payload)
        print(
            f"  {case['id']:<10} {result.verdict:<22} plans={len(result.plans):<2} "
            f"enum={result.enumerations}"
        )

    print("self-check 2: the oracle reproduces all 6 hand-derived answers")
    print("self-check 3: the M2 validator accepts every oracle-expected plan")
    print("self-check 4: every scenario is inside the tractability budget")
    assert conflicts >= MIN_CPP_CONFLICTS, (
        f"only {conflicts} scenarios break a cpp-first ranker; EVALS requires "
        f"at least {MIN_CPP_CONFLICTS}"
    )
    args.out.write_text(
        json.dumps({"seed": args.seed, "cases": payloads}, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {args.out.name}: {len(payloads)} scenarios, {conflicts} cpp-vs-net conflicts, "
        f"oracle enumerations max={max_enumerations} total={total_enumerations}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
