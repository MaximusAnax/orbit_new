"""Print the GrailTrader eval scorecard; exit non-zero on any gate failure.

Zero configuration::

    uv run python grailtrader/evals/run.py
    uv run python grailtrader/evals/run.py --regen-check   # fixtures match their generator
    uv run python grailtrader/evals/run.py --json          # machine-readable

Hermetic by construction: committed fixtures, offline adapters, the in-memory
store, no network, no wall clock, and no randomness beyond the three committed
placebo seeds.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct `python evals/run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics as M
from evals.harness import PLACEBO_SEEDS, load_scenario, run_pipeline

VALIDITY_CAVEAT = (
    "These gates certify pipeline correctness, coverage, leak-freedom, framing and\n"
    "graceful degradation under prior/world mismatch on committed fixtures. They do\n"
    "NOT certify real-world predictive skill, and hermetically cannot: scenario A is\n"
    "drawn from the engine's own model family. Real skill is only measurable by\n"
    "running `grailtrader backtest run` on your own imported comps and event history\n"
    "(reference: recovered)."
)

SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "M0",
        "Coverage and activity floors (anti-abstention)",
        (
            "M0a",
            "M0b",
            "M0c",
            "M0d-sell",
            "M0d-buy",
            "M0e",
            "M0f-m1a",
            "M0f-mom",
            "M0f-evt",
            "M0f-hold",
        ),
    ),
    (
        "M1",
        "Index fidelity from hostile listings (hard part A, FR-3/FR-4)",
        ("M1a", "M1a-p90", "M1a-dense", "M1a-sparse", "M1b-recall", "M1b-false"),
    ),
    ("M2", "Advisor directional skill (hard part B, FR-8/FR-10)", ("M2a", "M2b", "M2-excl")),
    ("M3", "Confidence quality (hard part B, FR-8)", ("M3a", "M3b", "M3c")),
    ("M4", "Placebo honesty — worst of three seeds (FR-10)", ("M4a", "M4b")),
    ("M5", "Advice framing compliance, both directions (FR-9)", ("M5a", "M5b")),
    ("M6", "Mismatch scenario B (validity)", ("M6a", "M6b", "M6c")),
)


def _fmt(value: float) -> str:
    if value == int(value) and abs(value) >= 1:
        return f"{int(value):d}"
    return f"{value:.4f}"


def print_scorecard(card: M.Scorecard, *, elapsed: float) -> None:
    width = 78
    print("=" * width)
    print("GrailTrader — eval scorecard")
    print("=" * width)
    header = f"{'metric':<12}{'value':>10}  {'n':>7}  {'gate':<16}{'result':<6} baseline"
    for code, title, keys in SECTIONS:
        print(f"\n{code} — {title}")
        print("-" * width)
        print(header)
        for key in keys:
            row = card.by_key(key)
            gate = row.gate
            n = "-" if row.n is None else f"{row.n:d}"
            verdict = "PASS" if row.passing else "FAIL"
            print(
                f"{row.key:<12}{_fmt(row.value):>10}  {n:>7}  "
                f"{(gate.describe() if gate else '-'):<16}{verdict:<6} "
                f"{gate.baseline if gate else ''}"
            )
            if row.note:
                print(f"{'':<12}{row.note}")

    diagnostics = card.diagnostics
    print("\nDiagnostics (printed, ungated)")
    print("-" * width)
    fence = diagnostics["D-fence"]
    print(
        f"D-fence     ambiguous 0.40-0.60x band excluded: "
        f"{fence['ambiguous_band_excluded']:.3f} (n={fence['n_ambiguous']}); "
        f"near-boundary clean excluded: {fence['near_boundary_clean_excluded']:.3f} "
        f"(n={fence['n_near_boundary']})"
    )
    print(
        "            no price-only rule can separate a fake at 0.5x from a sniped "
        "grail (SCOPE non-goal 3)."
    )
    contra = diagnostics["D-contra"]
    print(
        f"D-contra    scenario B, candidates driven only by prior-contradicting events: "
        f"n={int(contra['n'])} hit={contra['hit_rate']:.3f} mean_conf={contra['mean_conf']:.3f}"
    )
    print("            the advisor cannot detect a wrong prior; this shows how")
    print("            confidently wrong it is when its priors are wrong.")
    print(f"\nConfidence buckets (A): {json.dumps(diagnostics['buckets_a'], default=float)}")
    print(f"Confidence buckets (B): {json.dumps(diagnostics['buckets_b'], default=float)}")
    print(f"Spread by horizon:      {json.dumps(diagnostics['spread_by_horizon'])}")
    print(f"By driving event type:  {json.dumps(diagnostics['by_event_type'])}")
    print(f"Backtest exclusions:    {json.dumps(diagnostics['excluded'])}")
    print(f"Placebo per seed:       {json.dumps(diagnostics['placebo_per_seed'])}")
    print(f"Parent index max median error: {diagnostics['parent_index_max_median_error']:.4f}")
    print(f"Advice the engine refused to render: {diagnostics['render_refusals']}")

    print("\nNaive baselines (computed live in this run)")
    print("-" * width)
    for name, value in card.baselines.items():
        print(f"  {name:<32} {json.dumps(value, default=float)}")

    print("\nValidity")
    print("-" * width)
    print(VALIDITY_CAVEAT)

    failures = card.failures
    print("\n" + "=" * width)
    print(
        f"{len(card.rows) - len(failures)}/{len(card.rows)} gates PASS "
        f"in {elapsed:.1f}s"
        + ("" if not failures else "  FAILING: " + ", ".join(row.key for row in failures))
    )
    print("=" * width)


def regen_check(root: Path) -> list[str]:
    """Re-run the committed generator into a temp dir and diff (EVALS ``--regen-check``)."""
    from evals import generate_scenario

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        for name in ("scenario_a", "scenario_b"):
            generate_scenario.build(name, target)
            committed = root / name
            for path in sorted(committed.iterdir()):
                fresh = target / name / path.name
                if not fresh.exists():
                    problems.append(f"{name}/{path.name}: not produced by the generator")
                elif not filecmp.cmp(path, fresh, shallow=False):
                    problems.append(f"{name}/{path.name}: differs from the generator's output")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit the scorecard as JSON")
    parser.add_argument(
        "--regen-check",
        action="store_true",
        help="re-run generate_scenario.py and diff against the committed fixtures",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    if args.regen_check:
        problems = regen_check(M.FIXTURES)
        if problems:
            print("REGEN CHECK FAILED:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("regen check: committed fixtures match generate_scenario.py")

    result_a = run_pipeline(load_scenario("scenario_a"), placebo_seeds=PLACEBO_SEEDS)
    result_b = run_pipeline(load_scenario("scenario_b"))
    card = M.build_scorecard(result_a, result_b)
    elapsed = time.monotonic() - started

    if args.json:
        print(
            json.dumps(
                {
                    "metrics": [
                        {
                            "key": row.key,
                            "value": row.value,
                            "n": row.n,
                            "gate": row.gate.describe() if row.gate else None,
                            "pass": row.passing,
                        }
                        for row in card.rows
                    ],
                    "diagnostics": card.diagnostics,
                    "baselines": card.baselines,
                    "elapsed_seconds": round(elapsed, 2),
                },
                indent=2,
                default=float,
            )
        )
    else:
        print_scorecard(card, elapsed=elapsed)
    return 1 if card.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
