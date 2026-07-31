"""Print the dresscast eval scorecard (EVALS.md §6).

    uv run python dresscast/evals/run.py [--json] [--write-expected]

Zero configuration, no network, no wall clock: offline adapters and committed
fixtures only.  Exit code 0 iff every gate passes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # running the file directly, not as `evals.run`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.metrics import FIXTURES, EvalReport, build_report  # noqa: E402

RULE = "-" * 108


def format_scorecard(report: EvalReport, elapsed: float) -> str:
    lines: list[str] = []
    lines.append("dresscast — eval scorecard")
    lines.append(RULE)
    lines.append(
        f"{'metric':<34}{'value':>10}{'gate':>12}{'baseline':>11}{'margin':>10}"
        f"  {'result':<6} detail"
    )
    lines.append(RULE)
    for result in report.results:
        gate = (
            f"{result.comparator} {result.gate:.3f}" if result.gate is not None else "-"
        )
        baseline = f"{result.baseline:.4f}" if result.baseline is not None else "-"
        margin = f"{result.margin:+.4f}" if result.margin is not None else "-"
        status = "PASS" if result.passed else "FAIL"
        lines.append(
            f"{result.name:<34}{result.value:>10.4f}{gate:>12}{baseline:>11}{margin:>10}"
            f"  {status:<6} {result.detail}"
        )
    lines.append(RULE)

    notes = report.notes
    baselines = notes.get("baselines", {})
    lines.append("baselines (live-computed, EVALS.md §5.1)")
    for name, row in baselines.items():
        parts = ", ".join(f"{k} {v}" for k, v in row.items())
        lines.append(f"  {name:<14} {parts}")

    lines.append("")
    lines.append("per-scenario comfort (S_thermal / inband / saturated hours)")
    for label, row in sorted(notes.get("m2", {}).items()):
        lines.append(
            f"  {label:<34} {row['thermal']:.4f}   {row['inband']:.3f}   "
            f"{row['saturated_hours']:>2} h"
        )

    lines.append("")
    lines.append("layering advantage over the thermally static dresser (M3)")
    for label, row in sorted(notes.get("m3", {}).items()):
        lines.append(
            f"  {label:<34} engine {row['engine_inband']:.3f}  static "
            f"{row['static_best']:.3f}  delta {row['delta']:+.3f}"
        )

    lines.append("")
    lines.append("rollouts (M6, worst of three is gated)")
    for name, row in sorted(notes.get("m6", {}).items()):
        lines.append(
            f"  {name}: |E| {row['eligible']}  util {row['utilization']:.3f} "
            f"(static {row['static_utilization']:.3f})  no-repeat-3 "
            f"{row['no_repeat_window_3']:.3f}  novel {row['novel_item_rate']:.3f}  "
            f"consec {row['consec_sim']:.3f}  comfort {row['comfort']:.3f}  "
            f"repeat_free {row['repeat_free']:.3f}"
        )

    lines.append("")
    lines.append("component capability (M8)")
    for name, row in sorted(notes.get("m8", {}).items()):
        lines.append(
            f"  {name:<12} top1 {row['top1']:.4f}  random_valid {row['random_valid']:.4f}  "
            f"brute max {row['brute_max']:.4f}  denominator {row['denominator']:.4f}"
        )

    lines.append("")
    lines.append("report-only")
    lines.append(f"  component contributions   {notes.get('component_contributions')}")
    lines.append(f"  plan shape                {notes.get('plan_shape')}")
    lines.append(f"  monotone_shed_delta       {notes.get('monotone_shed_delta')}")
    lines.append(f"  relaxation_rate           {notes.get('relaxation_rate')}")
    lines.append(f"  M2c clamped hours         {notes.get('m2c')}")
    lines.append(f"  M5 trivial scorers        {notes.get('m5', {}).get('neutral_auc')} neutral, "
                 f"{notes.get('m5', {}).get('formality_only_auc')} formality, "
                 f"{notes.get('m5', {}).get('hue_only_auc')} hue")
    lines.append(f"  M9 detail                 {notes.get('m9', {}).get('random_valid_rate')} "
                 f"random_valid protection rate")
    lines.append(f"  M4 independent checks     {notes.get('m4', {}).get('checks')}")
    lines.append(RULE)
    failed = [r.name for r in report.results if not r.passed]
    verdict = "ALL GATES PASS" if report.passed else f"FAILED: {', '.join(failed)}"
    lines.append(f"{verdict}   ({len(report.results)} gates, {elapsed:.1f}s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the JSON summary only")
    parser.add_argument(
        "--write-expected",
        action="store_true",
        help="refresh evals/fixtures/expected.json (informational anchors only)",
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    report = build_report()
    elapsed = time.perf_counter() - started

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
    else:
        print(format_scorecard(report, elapsed))
        print()
        print("JSON summary:")
        print(
            json.dumps(
                {
                    "passed": report.passed,
                    "metrics": {
                        r.name: {"value": round(r.value, 6), "gate": r.gate, "passed": r.passed}
                        for r in report.results
                    },
                },
                indent=2,
            )
        )

    if args.write_expected:
        payload = {
            "note": (
                "Author-measured anchors, informational only. Gates are asserted "
                "against live-computed values, never against this file (EVALS.md §4)."
            ),
            "metrics": {r.name: round(r.value, 6) for r in report.results},
            "baselines": report.notes.get("baselines"),
            "per_scenario": report.notes.get("m2"),
            "layering": report.notes.get("m3"),
            "rollouts": report.notes.get("m6"),
            "components": report.notes.get("m8"),
            "enumeration": report.notes.get("enumeration"),
        }
        (FIXTURES / "expected.json").write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {FIXTURES / 'expected.json'}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
