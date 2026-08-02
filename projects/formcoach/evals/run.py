#!/usr/bin/env python3
"""Print the FormCoach eval scorecard.

    cd projects && uv run python formcoach/evals/run.py

Zero configuration: it loads the committed fixtures, runs the engine through the
offline adapters (no network, no clock, seeded randomness), prints every metric
with its naive baseline and its gate, and exits non-zero if any gate fails.
M8's row prints without a verdict — eight hand-labelled clips cannot support a
threshold — and prints ``NOT AVAILABLE`` while ``evals/fixtures/real/`` is empty.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.metrics import build_scorecard

HEADER = f"{'metric':<7}{'what it measures':<36}{'baseline':>10}{'value':>11}{'gate':>10}   verdict"
LINE = "-" * len(HEADER)


def _fmt(value: float, unit: str) -> str:
    return f"{value:.3f}{unit}" if unit else f"{value:.3f}"


def main() -> int:
    results, extras = build_scorecard()

    print("FormCoach eval scorecard")
    print("fixtures: evals/fixtures (committed, seed 20260731); adapters: offline only")
    print()
    print(HEADER)
    print(LINE)
    failures = []
    for result in results:
        verdict = "PASS" if result.passing else "FAIL"
        if not result.passing:
            failures.append(result)
        print(
            f"{result.key:<7}{result.name:<36}"
            f"{_fmt(result.baseline, result.unit):>10}"
            f"{_fmt(result.value, result.unit):>11}"
            f"{result.gate_text():>10}   {verdict}"
        )
    print(LINE)

    print()
    print("detail")
    for result in results:
        print(f"  {result.key:<5} {result.detail}  (baseline: {result.baseline_name})")
        for note in result.notes:
            print(f"        ! {note}")

    m8 = extras.get("m8")
    print()
    if m8 is None:
        print(
            "M8     Real-clip agreement                       NOT AVAILABLE (reported, never gated)"
        )
        print("      Drop hand-labelled clips into evals/fixtures/real/ to populate this row.")
    else:
        print(
            f"M8     Real-clip agreement: {m8['clips']} clips, "
            f"rep-count agreement {m8['rep_count_agreement']:.3f}, "
            f"fault Jaccard {m8['fault_jaccard']:.3f}  (reported, never gated)"
        )

    print()
    print("per-class F1 (M1 averages these)")
    for (exercise_id, fault_id), score in sorted(extras["classes"].items()):
        print(f"  {exercise_id:<20} {fault_id:<22} {score:.3f}")

    print()
    unbeaten = [r for r in results if not r.baseline_beaten]
    if unbeaten:
        print(
            "WARNING: gate no longer strictly beats its naive baseline: "
            + ", ".join(r.key for r in unbeaten)
        )
    if failures:
        print(f"FAILED {len(failures)} gate(s): " + ", ".join(r.key for r in failures))
        return 1
    print(f"All {len(results)} gates pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
