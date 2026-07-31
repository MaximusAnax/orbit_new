"""Synthesize 200 seeded random scenarios and commit the oracle's answers (M7).

``python evals/generate_random_cases.py --seeds 1..10`` writes
``fixtures/random_cases.json``: 10 seeds x 20 scenarios, each a freshly
generated small world (random ratios, increments, fees and caps, tier bonuses,
posting times, balances and goals) solved exhaustively by ``oracle.py``.

The point (EVALS finding E6) is to convert "exact on the curated cases" into
"exact on the scenario distribution": an unsound bound or an over-aggressive
dominance rule whose trigger conditions the curated cases happen to miss will
show up here.  Because the answers are committed, the gate runs engine-only.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import oracle
from worldgen import assemble, card, cashout, edge, fare_table, flight, program, valuation

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MAX_ENUMERATIONS = 2_000_000
MAX_AMOUNTS_PER_EDGE = 60
TODAY = "2026-07-31"
MONTH = "2026-10"


def build_random_world(rng: random.Random) -> dict[str, Any]:
    """A valid random world: 2 banks, 1 hotel, 2 airlines, DAG-shaped edges."""
    bank_mcpp = [rng.randrange(1800, 2100, 50), rng.randrange(1800, 2100, 50)]
    hotel_mcpp = rng.randrange(700, 900, 50)
    air_mcpp = [rng.randrange(1000, 1500, 50), rng.randrange(1000, 1500, 50)]

    programs = [
        program("b0", "Bank Zero", "bank"),
        program("b1", "Bank One", "bank"),
        program("ho", "Hotel", "hotel"),
        program("x0", "Air Zero", "airline"),
        program("x1", "Air One", "airline"),
    ]
    valuations = [
        valuation("b0", bank_mcpp[0]),
        valuation("b1", bank_mcpp[1]),
        valuation("ho", hotel_mcpp),
        valuation("x0", air_mcpp[0]),
        valuation("x1", air_mcpp[1]),
    ]
    cards = [card("cb0", "b0"), card("cb1", "b1"), card("cb0_basic", "b0", enables_transfer=False)]

    edges: list[dict[str, Any]] = []
    for bank in ("b0", "b1"):
        for air in ("x0", "x1"):
            if rng.random() < 0.75:
                fee = 60 if rng.random() < 0.35 else 0
                edges.append(
                    edge(
                        f"{bank}__{air}",
                        bank,
                        air,
                        min_from=2000,
                        increment_from=2000,
                        fee_mcpp=fee,
                        fee_cap_cents=rng.choice([3000, 6000, 9900]) if fee else None,
                        time_days=rng.choice([0, 0, 1, 2]),
                    )
                )
        if rng.random() < 0.8:
            edges.append(edge(f"{bank}__ho", bank, "ho", min_from=2000, increment_from=2000))
    for air in ("x0", "x1"):
        if rng.random() < 0.7:
            bonus = rng.random() < 0.5
            edges.append(
                edge(
                    f"ho__{air}",
                    "ho",
                    air,
                    ratio_from=3,
                    ratio_to=1,
                    min_from=3000,
                    increment_from=3000,
                    time_days=rng.choice([1, 2]),
                    bonus_per_from=60000 if bonus else None,
                    bonus_to=5000 if bonus else None,
                )
            )
    if not edges:  # pragma: no cover - the probabilities make this effectively impossible
        edges.append(edge("b0__x0", "b0", "x0", min_from=2000, increment_from=2000))

    cashouts = [
        cashout("b0_credit", "b0", "statement_credit", rng.randrange(400, 1000, 50)),
        cashout("b1_credit", "b1", "statement_credit", rng.randrange(400, 1000, 50)),
        cashout("b0_portal", "b0", "portal_travel", rng.randrange(1000, 1500, 50),
                requires_card="cb0"),
    ]
    if rng.random() < 0.6:
        # A liquid hotel option makes bank -> hotel chains cash-improving whenever
        # its cpp beats the bank's own credit, which a direct-only cash planner
        # never notices (FR-12's "cash-improving transfer chains").
        cashouts.append(
            cashout(
                "ho_credit", "ho", "statement_credit", rng.randrange(500, 900, 50),
                min_points=5000, increment=1000,
            )
        )

    offers = []
    for index, air in enumerate(("x0", "x1")):
        points = rng.randrange(14000, 46000, 2000)
        fees = rng.randrange(2000, 30000, 100)
        seats = rng.choice([None, 2, 4, 6])
        for origin, dest in (("NYC", "PAR"), ("PAR", "NYC")):
            offers.append(
                flight(
                    f"{air}_{origin.lower()}_{dest.lower()}",
                    air,
                    origin,
                    dest,
                    points_price=points,
                    fees_cents=fees,
                    seats_available=seats,
                    bookable_until="2026-08-01" if index == 1 and rng.random() < 0.2 else None,
                )
            )
    one_way = rng.randrange(80000, 250000, 5000)
    fares = fare_table({("NYC", "PAR", "business"): (one_way, one_way * 19 // 10)}, [MONTH])
    return assemble(
        programs=programs,
        valuations=valuations,
        cards=cards,
        edges=edges,
        cashouts=cashouts,
        offers=offers,
        fares=fares,
    )


def build_case(rng: random.Random, case_id: str) -> dict[str, Any] | None:
    """One random scenario, or None when it violates the tractability budget.

    The distribution is deliberately adversarial to naive planners (EVALS: the
    greedy baseline must stay well below the engine): balances usually sit just
    *under* the need-sized amount of the relevant award, so optimal funding
    needs multi-source splits, hub top-ups with tier-boundary sizing, or
    fee-aware source choice; round trips share sources across legs; some goals
    carry a tight ``book_by`` that timed edges violate; cash worlds often
    contain a cash-improving bank->hotel chain.
    """
    files = build_random_world(rng)
    cards = [c for c in ("cb0", "cb1") if rng.random() < 0.8] or ["cb0"]
    kind = rng.choices(["flight_ow", "flight_rt", "cash"], weights=[4, 4, 2])[0]
    pax = 1 if kind == "cash" else rng.choices([1, 2], weights=[3, 1])[0]

    # The typical points need this scenario must fund: the cheapest matching
    # award times passengers (times two legs for a round trip).
    prices = sorted(
        o["points_price"] for o in files["awards.json"] if o["kind"] == "flight"
    )
    need = prices[0] * pax * (2 if kind == "flight_rt" else 1)

    # Fund 2-3 programs whose balances individually undershoot the need but
    # jointly overshoot it, so single-source need-sized funding usually fails
    # while splits, hub chains and tier boundaries succeed.
    pool = ["b0", "b1", "ho"]
    rng.shuffle(pool)
    count = rng.choices([1, 2, 3], weights=[1, 6, 5])[0]
    balances: dict[str, int] = {}
    for index, name in enumerate(pool[:count]):
        step = 3000 if name == "ho" else 2000
        if name == "ho":
            # Hotel balances hover around the 60k tier boundary of the 3:1 edge.
            points = rng.randrange(step * 10, step * 51, step)
        else:
            # The first bank drawn holds most of the need (but usually not all
            # of it); later programs top the pool up past the need, so most
            # scenarios are fundable yet rarely from a single source.
            share = (0.55, 0.95) if index == 0 else (0.35, 0.75)
            low = max(step * 4, int(need * share[0]) // step * step)
            high = max(low + step * 2, min(step * 55, int(need * share[1])) // step * step)
            points = rng.randrange(low, high + step, step)
        balances[name] = points
    if rng.random() < 0.25:  # a partial opening balance in a paying program
        balances["x0" if rng.random() < 0.5 else "x1"] = rng.randrange(2000, need + 2000, 2000)
    if kind == "cash":
        goal: dict[str, Any] = {"kind": "cash", "cash_programs": None, "cash_max_points": None}
    else:
        goal = {
            "kind": "flight",
            "origin_city": "NYC",
            "dest_city": "PAR",
            "cabin": "business",
            "round_trip": kind == "flight_rt",
            "passengers": pax,
            "travel_window_start": f"{MONTH}-01",
            "travel_window_end": f"{MONTH}-31",
            "book_by": "2026-08-01" if rng.random() < 0.2 else None,
        }

    for name, points in balances.items():
        step = 3000 if name == "ho" else 2000
        if points // step > MAX_AMOUNTS_PER_EDGE:
            return None
    try:
        result = oracle.solve(
            files,
            cards=cards,
            balances=balances,
            goal=goal,
            today=TODAY,
            params={},
            enumeration_ceiling=MAX_ENUMERATIONS,
        )
    except oracle.OracleTooBig:
        return None
    return {
        "id": case_id,
        "world": files,
        "cards": cards,
        "balances": balances,
        "goal": goal,
        "today": TODAY,
        "params": {},
        "expected": {
            "verdict": result.verdict,
            "objective_cents": result.objective_cents,
            "plan_count": len(result.plans),
            "top_plan_canonical_form": (
                result.plans[0].canonical_form if result.plans else None
            ),
            "oracle_enumerations": result.enumerations,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="1..10", help="inclusive seed range, e.g. 1..10")
    parser.add_argument("--per-seed", type=int, default=20)
    parser.add_argument("--out", type=Path, default=FIXTURES / "random_cases.json")
    args = parser.parse_args(argv)
    low, high = (int(part) for part in args.seeds.split(".."))

    cases: list[dict[str, Any]] = []
    total = 0
    biggest = 0
    for seed in range(low, high + 1):
        rng = random.Random(seed)
        made = 0
        attempts = 0
        while made < args.per_seed:
            attempts += 1
            assert attempts < 500, f"seed {seed}: could not build {args.per_seed} cases"
            case = build_case(rng, f"rnd_{seed:02d}_{made + 1:02d}")
            if case is None:
                continue
            made += 1
            enumerations = case["expected"]["oracle_enumerations"]
            total += enumerations
            biggest = max(biggest, enumerations)
            cases.append(case)
        print(f"  seed {seed}: {made} scenarios ({attempts} attempts)")

    args.out.write_text(
        json.dumps({"seeds": args.seeds, "cases": cases}, indent=1) + "\n", encoding="utf-8"
    )
    verdicts: dict[str, int] = {}
    for case in cases:
        verdicts[case["expected"]["verdict"]] = verdicts.get(case["expected"]["verdict"], 0) + 1
    print(
        f"wrote {args.out.name}: {len(cases)} scenarios, verdicts {verdicts}, "
        f"oracle enumerations max={biggest} total={total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
