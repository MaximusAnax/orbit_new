"""Print the flowlist eval scorecard (EVALS.md §6).

    uv run python flowlist/evals/run.py

Zero configuration, no network, no wall-clock dependence: everything comes
from the committed fixtures in ``evals/fixtures/`` through the offline
adapters and the pure engine.  Exit code 0 iff every gate passes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python flowlist/evals/run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.metrics import (
    EvalReport,
    MetricResult,
    evaluate,
    fixture_invariants,
    fixtures,
)

WIDTH = 96
GATED_HEADER = f"{'METRIC':<32}{'VALUE':>10}  {'GATE':>10}  {'RESULT':<7} DETAIL"


def _format(metric: MetricResult) -> str:
    # M6_min's gate is a strict "> 0" (EVALS §5); every other gate is ">=".
    comparison = ">" if metric.name == "M6_baseline_margin_min" else ">="
    gate = "" if metric.gate is None else f"{comparison} {metric.gate:.2f}"
    result = "-" if metric.report_only else ("PASS" if metric.passed else "FAIL")
    return f"{metric.name:<32}{metric.value:>10.4f}  {gate:>10}  {result:<7} {metric.detail}"


def render(report: EvalReport) -> str:
    counts: dict[str, int] = {}
    for fixture in fixtures():
        counts[fixture.suite] = counts.get(fixture.suite, 0) + 1
    suites = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))

    lines = [
        "=" * WIDTH,
        "flowlist eval scorecard",
        f"fixtures: {len(fixtures())} playlists ({suites}); hermetic: offline adapters, "
        "seeded RNG, literal timestamps",
        "=" * WIDTH,
        GATED_HEADER,
        "-" * WIDTH,
    ]
    gated = [m for m in report.metrics if not m.report_only]
    extras = [m for m in report.metrics if m.report_only]
    lines.extend(_format(metric) for metric in gated)

    lines += ["-" * WIDTH, "report-only rows (not gated)", "-" * WIDTH]
    lines.extend(_format(metric) for metric in extras)

    invariants = fixture_invariants()
    lines += [
        "-" * WIDTH,
        "fixture invariants (EVALS.md §4) — all recomputed live from the committed fixtures",
        "-" * WIDTH,
        f"bpm_only_auc                    {invariants['bpm_only_auc']:>10.4f}  "
        f"<=     0.80  {'PASS' if invariants['bpm_only_auc'] <= 0.80 else 'FAIL':<7} "
        "keeps M2 and the M2b margin simultaneously satisfiable",
        f"exact instances below 0.97      {invariants['instances_below_0.97']:>10d}  "
        f"{'>=       3':>10}  "
        f"{'PASS' if invariants['instances_below_0.97'] >= 3 else 'FAIL':<7} "
        "a degenerate baseline provably fails on them",
    ]
    for name, (mean, minimum) in sorted(invariants["degenerate_m4"].items()):
        rejected = mean < 0.97 and minimum < 0.90
        lines.append(
            f"{name + ' M4_mean/min':<32}{mean:>10.4f}  {'<   0.97':>10}  "
            f"{'PASS' if rejected else 'FAIL':<7} "
            f"min {minimum:.4f} < 0.90 — the degenerate baseline the M4 gates must reject"
        )

    failures = [m.name for m in gated if not m.passed]
    lines += ["=" * WIDTH]
    if failures:
        lines.append(f"RESULT: FAIL ({len(failures)} gate(s): {', '.join(failures)})")
    else:
        lines.append(f"RESULT: PASS ({len(gated)} gates)")
    lines.append("=" * WIDTH)
    return "\n".join(lines)


def main() -> int:
    report = evaluate()
    print(render(report))
    print()
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
