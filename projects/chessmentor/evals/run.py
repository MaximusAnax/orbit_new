"""ChessMentor eval scorecard.

    uv run python chessmentor/evals/run.py             # every metric + gate
    uv run python chessmentor/evals/run.py --quick     # skip the game-playing metrics
    uv run python chessmentor/evals/run.py --baselines # also measure M1a's collapsed ladder
    uv run python chessmentor/evals/run.py --full      # release ritual: re-run calibration too

Zero configuration: it loads the committed fixtures, runs M1a-M10 with the
offline adapters (internal analyst at ``JUDGE_BUDGET``, committed book,
in-memory store), prints one row per metric with its value, its naive baseline
and its gate, and exits non-zero when any gate fails.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import baselines as baselines_module
from evals import metrics as metrics_module
from evals.metrics import MetricResult

CHEAP_METRICS = ("M1b", "M2a", "M2b", "M2c", "M3", "M6", "M8")


def _format(value: float | None) -> str:
    if value is None:
        return "  --  "
    return f"{value:6.3f}" if abs(value) < 10 else f"{value:6.1f}"


def render(results: list[MetricResult], *, elapsed: float) -> str:
    lines: list[str] = []
    lines.append("=" * 108)
    lines.append("ChessMentor — eval scorecard")
    lines.append(
        "internal analyst @ JUDGE_BUDGET, committed opening book, in-memory store; "
        "no network, no wall clock"
    )
    lines.append("=" * 108)
    header = f"{'':5} {'metric':<38} {'value':>7}  {'baseline':>8}  {'gate':<24} result"
    lines.append(header)
    lines.append("-" * 108)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        baseline = _format(result.baseline) if result.baseline is not None else "   --   "
        lines.append(
            f"{result.metric:<5} {result.label:<38} {_format(result.value):>7}  "
            f"{baseline:>8}  {result.gate:<24} {status}"
        )
        if result.baseline_label:
            lines.append(f"{'':5} {'  baseline: ' + result.baseline_label:<38}")
        if result.detail:
            for chunk in _wrap(result.detail, 96):
                lines.append(f"{'':7}{chunk}")
    lines.append("-" * 108)
    failures = [result.metric for result in results if not result.passed]
    lines.append(
        f"{len(results) - len(failures)}/{len(results)} gates passed in {elapsed:.0f}s"
        + (f" — FAILED: {', '.join(failures)}" if failures else "")
    )
    lines.append("=" * 108)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split(" ")
    out: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out


def _rerun_calibration(workers: int) -> int:
    """``--full``: re-run the FR-5 calibration and diff against the committed record."""
    script = Path(__file__).resolve().parent / "fixtures" / "generate_calibration.py"
    committed = Path(__file__).resolve().parent / "fixtures" / "calibration.json"
    fresh = committed.with_suffix(".fresh.json")
    print("re-running generate_calibration.py — this takes hours, by design", flush=True)
    subprocess.run(
        [sys.executable, str(script), "--workers", str(workers), "--out", str(fresh)],
        check=True,
    )
    same = json.loads(fresh.read_text()) == json.loads(committed.read_text())
    print("calibration record matches the committed one" if same else "CALIBRATION DIFFERS")
    fresh.unlink()
    return 0 if same else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="only the metrics that need no engine games or analyst passes",
    )
    parser.add_argument(
        "--baselines",
        action="store_true",
        help="also measure M1a's collapsed-ladder baseline by playing it",
    )
    parser.add_argument("--full", action="store_true", help="release ritual: re-run calibration")
    parser.add_argument("--workers", type=int, default=metrics_module.DEFAULT_WORKERS)
    parser.add_argument("--json", type=Path, default=None, help="also write the scorecard as JSON")
    args = parser.parse_args()

    started = time.perf_counter()
    if args.quick:
        results = [
            metrics_module.m1b_ladder_ordering(),
            *metrics_module.m2_estimator(),
            metrics_module.m3_band_adherence(),
            metrics_module.m6_phase_boundaries(),
            metrics_module.m8_prioritisation(),
        ]
    else:
        results = metrics_module.all_metrics(workers=args.workers)

    if args.baselines and not args.quick:
        collapsed = baselines_module.collapsed_ladder(workers=max(1, min(3, args.workers)))
        results = [
            (
                result
                if result.metric != "M1a"
                else MetricResult(
                    metric=result.metric,
                    label=result.label,
                    value=result.value,
                    gate=result.gate,
                    passed=result.passed,
                    detail=result.detail,
                    baseline=collapsed.value,
                    baseline_label=f"{collapsed.label} [{collapsed.detail}]",
                    extras=result.extras,
                )
            )
            for result in results
        ]

    elapsed = time.perf_counter() - started
    report = render(results, elapsed=elapsed)
    print(report)

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                [
                    {
                        "metric": result.metric,
                        "label": result.label,
                        "value": result.value,
                        "baseline": result.baseline,
                        "gate": result.gate,
                        "passed": result.passed,
                        "detail": result.detail,
                    }
                    for result in results
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    exit_code = 0 if all(result.passed for result in results) else 1
    if args.full:
        exit_code |= _rerun_calibration(args.workers)
    return exit_code


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    raise SystemExit(main())
