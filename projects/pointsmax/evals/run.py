"""Print the PointsMax eval scorecard.

``uv run python pointsmax/evals/run.py`` — zero configuration: loads the
committed fixtures, runs M1-M7 and D0 with the offline adapters and the
in-memory store, prints every metric with its measured naive baseline and its
gate, and exits non-zero if any gate fails.

``--regen`` re-runs the two committed generators into a temporary directory and
diffs them against the committed fixtures, proving the answers were not
hand-edited.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import baselines  # noqa: E402
import metrics  # noqa: E402
from d0 import run_d0_checks  # noqa: E402


@dataclass
class Row:
    name: str
    value: float
    baseline: float
    baseline_ceiling: float
    gate: float
    exact: bool

    @property
    def passed(self) -> bool:
        return self.value >= self.gate if not self.exact else self.value == self.gate

    @property
    def baseline_ok(self) -> bool:
        return self.baseline <= self.baseline_ceiling


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def build_rows() -> tuple[list[Row], dict[str, object]]:
    """Measure the engine and every baseline; returns the rows plus extra detail."""
    cases = metrics.search_cases()
    engine_scores = metrics.score_scenarios(cases)
    greedy_scores = metrics.score_scenarios(cases, runner=baselines.greedy_runner())
    ungated_scores = metrics.score_scenarios(
        cases, runner=baselines.greedy_runner(quantize=False, gating=False)
    )
    cpp_scores = metrics.score_scenarios(cases, reranker=baselines.cpp_first)

    shipped = metrics.score_shipped()
    shipped_unpruned = baselines.unpruned_shipped_results()

    random_results = metrics.score_random()
    random_greedy = metrics.score_random(runner=baselines.greedy_random_runner)

    keyword = baselines.KeywordParser(metrics.eval_world("small_a"))

    rows = [
        Row("M1a plan optimality (small, 52)", metrics.m1(engine_scores, "small"),
            metrics.m1(greedy_scores, "small"), 0.45, 1.0, True),
        Row("M1b plan optimality (stress, 12)", metrics.m1(engine_scores, "stress"),
            metrics.m1(greedy_scores, "stress"), 0.25, 1.0, True),
        Row("M2 emitted-plan validity", metrics.m2(engine_scores),
            metrics.m2(ungated_scores), 0.85, 1.0, True),
        Row("M3 ranking + verdict + caveats", metrics.m3(engine_scores),
            metrics.m3(cpp_scores), 0.70, 1.0, True),
        Row("M4 value accounting (30)", metrics.m4(),
            metrics.m4(calculator=baselines.NaiveCalculator), 0.45, 1.0, True),
        Row("M5 goal parser (60)", metrics.m5(),
            metrics.m5(parser=keyword), 0.60, 0.90, False),
        Row("M6 shipped-world operability (12)", metrics.m6(shipped),
            metrics.m6(shipped_unpruned), 0.60, 1.0, True),
        Row("M7 randomized differential (200)", metrics.m7(random_results),
            metrics.m7(random_greedy), 0.45, 1.0, True),
    ]
    detail = {
        "shipped": shipped,
        "emitting": sum(1 for r in shipped if r.plans > 0),
        "search_enumerations": [
            case["expected"]["oracle_enumerations"] for case in cases
        ],
        "random_enumerations": [
            case["expected"]["oracle_enumerations"] for case in metrics.random_cases()
        ],
    }
    return rows, detail


def regen_and_diff() -> int:
    """Re-run both generators into a temp directory and diff against the fixtures."""
    import shutil
    import subprocess
    import tempfile

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for script, name, extra in (
            ("generate_search_cases.py", "search_cases.json", []),
            ("generate_random_cases.py", "random_cases.json", ["--seeds", "1..10"]),
        ):
            out = Path(tmp) / name
            proc = subprocess.run(
                [sys.executable, str(HERE / script), "--out", str(out), *extra],
                capture_output=True,
                text=True,
                cwd=HERE,
                check=False,
            )
            if proc.returncode != 0:
                print(f"FAIL {script}: {proc.stderr.strip().splitlines()[-1:]}")
                failures += 1
                continue
            committed = (HERE / "fixtures" / name).read_text(encoding="utf-8")
            if out.read_text(encoding="utf-8") != committed:
                print(f"FAIL {name} differs from the committed fixture")
                shutil.copy(out, Path(tmp) / f"regen-{name}")
                failures += 1
            else:
                print(f"OK   {name} regenerates byte-identically")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regen", action="store_true", help="diff regenerated fixtures")
    parser.add_argument("--json", action="store_true", help="emit machine-readable scores")
    args = parser.parse_args(argv)

    if args.regen:
        return 1 if regen_and_diff() else 0

    rows, detail = build_rows()
    d0 = run_d0_checks()

    if args.json:
        print(
            json.dumps(
                {
                    "metrics": [
                        {
                            "name": r.name,
                            "value": r.value,
                            "baseline": r.baseline,
                            "gate": r.gate,
                            "passed": r.passed,
                        }
                        for r in rows
                    ],
                    "d0": [{"name": n, "passed": ok, "detail": d} for n, ok, d in d0],
                },
                indent=2,
            )
        )
        return 0 if all(r.passed for r in rows) and all(ok for _, ok, _ in d0) else 1

    print("PointsMax — eval scorecard")
    print("=" * 96)
    print(f"{'metric':<36}{'score':>8}{'baseline':>10}{'ceiling':>9}{'gate':>10}{'result':>10}")
    print("-" * 96)
    for row in rows:
        gate = f"= {_fmt(row.gate)}" if row.exact else f">= {_fmt(row.gate)}"
        flag = "PASS" if row.passed else "FAIL"
        print(
            f"{row.name:<36}{_fmt(row.value):>8}{_fmt(row.baseline):>10}"
            f"{_fmt(row.baseline_ceiling):>9}{gate:>10}{flag:>10}"
        )
    print("-" * 96)
    for row in rows:
        if not row.baseline_ok:
            print(
                f"BASELINE CEILING BREACHED: {row.name} baseline {_fmt(row.baseline)} > "
                f"{_fmt(row.baseline_ceiling)}"
            )

    print("\nD0 — determinism & state integrity")
    for name, ok, note in d0:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if note else ''}")

    shipped = detail["shipped"]
    print("\nM6 — shipped-world goals (wall-clock is reported, never gated)")
    print(f"  {'goal':<8}{'plans':>6}{'expansions':>12}{'seconds':>10}   result")
    for result in shipped:  # type: ignore[union-attr]
        note = f"   {result.detail}" if result.detail else ""
        print(
            f"  {result.goal_id:<8}{result.plans:>6}{result.expansions:>12}"
            f"{result.seconds:>10.3f}   {'ok' if result.passed else 'FAIL'}{note}"
        )
    print(f"  goals emitting at least one plan: {detail['emitting']}/12 (hard gate: >= 9)")

    search_enums = detail["search_enumerations"]
    random_enums = detail["random_enumerations"]
    print("\nOracle enumerations recorded in the fixtures (budget: 2,000,000 per scenario)")
    print(
        f"  curated  max={max(search_enums):,}  total={sum(search_enums):,}\n"
        f"  random   max={max(random_enums):,}  total={sum(random_enums):,}"
    )

    ok = all(r.passed for r in rows) and all(o for _, o, _ in d0) and all(r.baseline_ok for r in rows)
    print("\n" + ("ALL GATES PASS" if ok else "GATE FAILURES — see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
