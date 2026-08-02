"""Pytest-enforced eval gates (EVALS.md §5.4, §6).

One test per gate, each asserting the absolute threshold and — where §5.4
defines one — the margin over the live-computed baseline, plus an end-to-end
run of ``evals/run.py``.  The report is computed once per session and cached,
so every gate reads the same numbers the scorecard prints.

Test names carry FR ids per EVALS.md §7.  Two margins were re-derived during
the build against measured baselines (REVIEW.md §Build-stage): the M2 margin
is asserted on the swing cases, and the M6 utilization margin gate is 0.10.
"""

from __future__ import annotations

import subprocess
import sys
from functools import cache
from pathlib import Path

from evals import run as eval_run
from evals.metrics import EvalReport, MetricResult, build_report


@cache
def report() -> EvalReport:
    """Every metric, computed once on the committed fixtures (no network)."""
    return build_report()


def _get(name: str) -> MetricResult:
    metric = report().get(name)
    assert metric.passed, f"{name} = {metric.value:.4f} (gate {metric.comparator} {metric.gate}) — {metric.detail}"
    return metric


# --------------------------------------------------------------------------
# H1 — day-long thermal layering
# --------------------------------------------------------------------------


def test_fr5_fr6_physics_conformance_gate() -> None:
    """M1 = 1.00: published charts, ASHRAE rows, and hand-computed anchors."""
    metric = _get("M1 physics_conformance")
    assert metric.value == 1.0


def test_fr6_comfort_fit_gate() -> None:
    """M2 >= 0.88 with the swing-case margin over mean_static >= 0.15."""
    metric = _get("M2 comfort_fit")
    assert metric.value >= 0.88
    margin = _get("M2 margin over mean_static")
    assert margin.value >= 0.15
    assert margin.baseline is not None  # live-computed, never a constant


def test_fr6_comfort_worst_case_gate() -> None:
    """M2_worst >= 0.75: no scenario may collapse."""
    assert _get("M2_worst").value >= 0.75


def test_fr8_search_optimality_gate() -> None:
    """M2b: caps and bound-pruning cost <= 1% of the engine's own objective."""
    assert _get("M2b_mean search_optimality").value >= 0.99
    assert _get("M2b_min").value >= 0.97


def test_fr6_saturation_maximality_gate() -> None:
    """M2c = 1.00 on clamped hours, denominator >= 40 (never vacuous)."""
    metric = _get("M2c saturation_maximality")
    assert metric.value == 1.0
    assert report().notes["m2c"]["clamped_hours"] >= 40


def test_fr8_layering_advantage_gate() -> None:
    """M3 >= +0.20 mean and M3_min >= +0.10 over the thermally static dresser."""
    assert _get("M3 layering_advantage").value >= 0.20
    assert _get("M3_min").value >= 0.10


def test_fr8_fr9_fr15_output_validity_gate() -> None:
    """M4 = 1.00 across the independent checker's eleven families."""
    metric = _get("M4 output_validity")
    assert metric.value == 1.0
    assert report().notes["m4"]["checks"] > 1000  # the denominator is real


# --------------------------------------------------------------------------
# H2 — wardrobe-constrained outfit quality over time
# --------------------------------------------------------------------------


def test_fr10_palette_style_auc_gate() -> None:
    """M5 >= 0.90 over the hand-labelled good/bad outfit pairs."""
    assert _get("M5 palette_style_auc").value >= 0.90


def test_fr10_component_auc_gates() -> None:
    """M5_color and M5_style each >= 0.75: neither half may go inert."""
    assert _get("M5_color").value >= 0.75
    assert _get("M5_style").value >= 0.75


def test_fr10_anti_gaming_margin_gate() -> None:
    """M5b >= +0.10 over the best of the three trivial scorers."""
    metric = _get("M5b anti_gaming_margin")
    assert metric.value >= 0.10
    assert metric.baseline is not None


def test_fr10_borderline_monotonicity_gate() -> None:
    """M5_mono: borderline cases sit between good and bad, separations >= 0.10."""
    assert _get("M5_mono good-borderline").value >= 0.10
    assert _get("M5_mono borderline-bad").value >= 0.10


def test_fr11_rollout_variety_gates() -> None:
    """M6 variety gates, each asserted on the worst of the three rollouts."""
    assert _get("M6 no_repeat_window_3").value >= 0.95
    assert _get("M6 novel_item_rate").value >= 0.35
    assert _get("M6 consec_sim").value <= 0.40


def test_fr11_utilization_gate() -> None:
    """M6 utilization >= 0.60 absolute and >= 0.10 over live mean_static."""
    util = _get("M6 utilization")
    assert util.value >= 0.60
    margin = _get("M6 utilization margin")
    assert margin.value >= 0.10
    assert margin.baseline is not None  # the static baseline is live-computed


def test_fr3_fr11_rollout_comfort_gate() -> None:
    """M6 rollout_comfort >= 0.83: variety may not buy freshness with discomfort."""
    assert _get("M6 rollout_comfort").value >= 0.83


def test_fr8_component_capability_gates() -> None:
    """M8: each soft component >= halfway from random_valid to the brute max."""
    for component in ("protection", "color", "style", "variety"):
        metric = _get(f"M8 lift_{component}")
        assert metric.value >= 0.50, component
        assert metric.baseline is not None


def test_fr9_protection_response_gate() -> None:
    """M9 = 1.00: top-1 responds whenever an adequate compatible option exists."""
    assert _get("M9 protection_response").value == 1.0


def test_fr9_fr14_compliance_gate() -> None:
    """M10 = 1.00: degradation is structured, minimal, and never silent."""
    assert _get("M10 degradation_correctness").value == 1.0


# --------------------------------------------------------------------------
# FR-19 + runner
# --------------------------------------------------------------------------


def test_fr19_determinism_gate() -> None:
    """M7 = 1.00: byte-identity, recomputation, hash stability, 1-ULP."""
    assert _get("M7 determinism").value == 1.0


def test_all_gates_pass() -> None:
    """The report as a whole is green — no gate escapes an assertion above."""
    failed = [r.name for r in report().results if not r.passed]
    assert not failed, f"failing gates: {failed}"
    assert len(report().results) == 29


def test_eval_runner_passes() -> None:
    """``python evals/run.py`` prints a passing scorecard and exits 0."""
    script = Path(eval_run.__file__).resolve()
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-2000:]
    assert "ALL GATES PASS" in completed.stdout
