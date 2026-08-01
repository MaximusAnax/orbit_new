"""VoiceKin eval scorecard (EVALS.md "How the suite runs").

Zero-config: materializes the fixture corpus into ``evals/fixtures/.cache/`` if
absent, computes M1-M6 against the committed fixtures with the offline
adapters, prints each metric with its gate, its live-computed naive baseline
and PASS/FAIL, and exits non-zero on any gate failure.

    uv run python voicekin/evals/run.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # `python voicekin/evals/run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics  # noqa: E402


def main() -> int:
    started = time.time()
    cal = metrics.calibration()
    print("VoiceKin eval scorecard")
    print(
        f"  embedder {cal.embedder_id}  theta_verify {cal.theta_verify:.4f}  "
        f"theta_enroll {cal.theta_enroll:.4f}  (offline adapters, committed fixtures)"
    )
    print()

    rows = metrics.compute_scorecard()
    width_metric = max(len(r.metric) for r in rows)
    width_gate = max(len(r.gate) for r in rows)
    header = (
        f"{'metric':<{width_metric}}  {'value':>8}  {'gate':<{width_gate}}  "
        f"{'':<4}  {'naive baseline (computed live)':<44}  detail"
    )
    print(header)
    print("-" * len(header))
    failed = 0
    for row in rows:
        verdict = "PASS" if row.passed else "FAIL"
        failed += 0 if row.passed else 1
        value = f"{row.value:8.4f}" if row.metric != "M2a" else f"{row.value:8.0f}"
        print(
            f"{row.metric:<{width_metric}}  {value}  {row.gate:<{width_gate}}  "
            f"{verdict:<4}  {row.baseline:<44}  {row.note}"
        )

    m3 = metrics.compute_m3()
    if m3.failures:
        print()
        print(f"M3 near-twin misattributions ({len(m3.failures)} of {m3.trials} trials):")
        for speaker, text, winner in m3.failures:
            print(f"  {speaker} rendering {text!r}... attributed to {winner}")

    m4 = metrics.compute_m4()
    broken = [o for o in m4 if not o.passed]
    if broken:
        print()
        print("M4 scenario failures:")
        for outcome in broken:
            print(f"  #{outcome.scenario_id} {outcome.name}")
            for mismatch in outcome.mismatches:
                print(f"    - {mismatch}")

    m5 = metrics.compute_m5()
    missed = [name for name, ok in m5.outcomes if not ok]
    if missed or not m5.clean_ok:
        print()
        print(f"M5 mismatches: clean_ok={m5.clean_ok} cases={missed}")

    print()
    print(f"{len(rows) - failed}/{len(rows)} gates pass   ({time.time() - started:.1f}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
