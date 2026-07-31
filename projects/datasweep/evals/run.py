"""Print the datasweep eval scorecard (EVALS.md §6).

Runnable with zero configuration and no network::

    uv run python datasweep/evals/run.py

Exit code 0 iff every gate passes.  M6 is the one metric that shells out: it
re-invokes the CLI in two subprocesses with different ``PYTHONHASHSEED``
values, which is what makes every other number trustworthy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics as M

WIDTH = 78


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _format_value(metric: M.MetricResult) -> str:
    if metric.comparison == "==" and float(metric.gate or 0) == 0.0:
        return f"{int(metric.value):d}"
    return f"{metric.value:.4f}"


def _format_gate(metric: M.MetricResult) -> str:
    if metric.gate is None:
        return "—"
    if metric.comparison == "==" and float(metric.gate) == 0.0:
        return "= 0"
    if metric.comparison == "==":
        return f"= {metric.gate:.2f}"
    return f"≥ {metric.gate:.2f}"


def render(report: M.EvalReport) -> str:
    lines: list[str] = []
    lines.append(_rule("="))
    lines.append("datasweep — eval scorecard")
    lines.append(
        f"{len(M.corrupted_fixtures())} corrupted + {len(M.golden_fixtures())} golden + "
        f"{len(M.trap_fixtures())} trap fixtures, "
        f"{M.expected()['totals']['n_ops']} injected ops, seed "
        f"{M.expected()['seed']}"
    )
    lines.append(_rule("="))
    lines.append("")
    lines.append(f"{'metric':<32}{'value':>10}{'gate':>10}{'naive':>10}{'result':>10}")
    lines.append(_rule())
    for metric in report.metrics:
        naive = report.naive.get(M._gate_key(metric.name), 0.0)
        naive_text = (
            f"{int(naive):d}" if metric.comparison == "==" and metric.gate == 0 else f"{naive:.4f}"
        )
        lines.append(
            f"{metric.name:<32}{_format_value(metric):>10}{_format_gate(metric):>10}"
            f"{naive_text:>10}{'PASS' if metric.passed else 'FAIL':>10}"
        )
    lines.append(_rule())
    lines.append("")

    detection = report.by_name("M2_detection")
    lines.append("M2 — per-class detection (findings.jsonl vs the corruption manifests)")
    lines.append(
        f"  {'class':<8}{'injected':>10}{'detected':>10}{'reports':>10}"
        f"{'precision':>11}{'recall':>9}{'F1':>8}"
    )
    for klass, row in detection.detail["per_class"].items():
        lines.append(
            f"  {klass:<8}{row['injected']:>10}{row['detected']:>10}{row['reports']:>10}"
            f"{row['precision']:>11.4f}{row['recall']:>9.4f}{row['f1']:>8.4f}"
        )
    lines.append("")

    repair = report.by_name("M4_repair_auto")
    lines.append("M4 — auto repair rate per class (OUT is report-only by design)")
    for klass, value in repair.detail["per_class"].items():
        lines.append(f"  {klass:<8}{value:>8.4f}")
    lines.append("")

    lines.append("Report-only diagnostics")
    review = report.report_only["review_precision"]
    lines.append(f"  review_precision (recommended candidate == golden): {review['overall']:.4f}")
    for rule, value in review["per_rule"].items():
        lines.append(f"    {rule:<34}{value:>8.4f}")
    lines.append("  tier_distribution (findings by class and tier)")
    for klass, tiers in report.report_only["tier_distribution"].items():
        rendered = "  ".join(f"{tier}={count}" for tier, count in tiers.items())
        lines.append(f"    {klass:<6}{rendered}")
    lines.append("")

    lines.append("Per-fixture (auto precision / repair_total / revert)")
    lines.append(f"  {'fixture':<24}{'ops':>6}{'auto':>7}{'prec':>8}{'repair':>8}{'revert':>8}")
    for row in report.report_only["per_file_table"]:
        lines.append(
            f"  {row['fixture']:<24}{row['ops']:>6}{row['auto_changes']:>7}"
            f"{row['auto_precision']:>8.4f}{row['repair_total']:>8.4f}"
            f"{'ok' if row['revert'] else 'FAIL':>8}"
        )
    lines.append("")

    failures = [metric for metric in report.metrics if not metric.passed]
    if failures:
        lines.append("FAILURES")
        for metric in failures:
            lines.append(f"  {metric.name}: {json.dumps(metric.detail, default=str)[:600]}")
        lines.append("")

    lines.append(_rule("="))
    lines.append("ALL GATES PASS" if report.passed else "GATES FAILED")
    lines.append(_rule("="))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the datasweep eval scorecard.")
    parser.add_argument(
        "--skip-determinism",
        action="store_true",
        help="Skip M6 (which spawns 8 subprocesses); useful for a fast inner loop.",
    )
    parser.add_argument("--json", action="store_true", help="Print only the JSON summary.")
    args = parser.parse_args(argv)

    report = M.evaluate(include_m6=not args.skip_determinism)
    if args.json:
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    else:
        print(render(report))
        print()
        print(json.dumps(report.to_json()["naive_deltas"], indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
