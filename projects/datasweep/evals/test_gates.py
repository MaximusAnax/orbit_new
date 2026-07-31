"""Pytest-enforced eval gates — one test per threshold in EVALS.md §5.

``uv run pytest datasweep/`` therefore fails on any quality regression, not
just on a broken unit test.  Test names carry the FR ids they defend so the
SCOPE.md → test mapping stays auditable (EVALS.md §7).

The whole suite is computed once per session (``metrics.evaluate`` is cached)
and every gate asserts against that one report — the same report ``run.py``
prints.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

if __package__ in (None, ""):  # pragma: no cover - direct invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics as M

EVALS_DIR = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def report() -> M.EvalReport:
    return M.evaluate()


def _assert_gate(report: M.EvalReport, name: str) -> M.MetricResult:
    metric = report.by_name(name)
    assert metric.passed, (
        f"{metric.name} = {metric.value} fails its gate "
        f"{metric.comparison} {metric.gate}: {json.dumps(metric.detail, default=str)[:1500]}"
    )
    return metric


# -- H1: detection fidelity ------------------------------------------------


def test_fr5_type_inference_gate(report: M.EvalReport) -> None:
    """M1 ≥ 0.95 over 91 labeled column instances of the corrupted fixtures."""
    metric = _assert_gate(report, "M1_type_inference_accuracy")
    assert metric.detail["labeled_columns"] == 91


def test_fr6_detection_macro_f1_gate(report: M.EvalReport) -> None:
    """M2 ≥ 0.85: real recall *and* precision on all eight classes at once."""
    metric = _assert_gate(report, "M2_detection_macro_f1")
    assert set(metric.detail["per_class"]) == set(M.M2_CLASSES)


def test_fr6_min_class_f1_gate(report: M.EvalReport) -> None:
    """M2_min ≥ 0.70: the macro mean must not hide a dead detector."""
    _assert_gate(report, "M2_min_class_f1")


def test_fr6_every_class_has_enough_instances(report: M.EvalReport) -> None:
    """Per-class F1 must not be sampling noise (EVALS.md §5)."""
    per_class = report.by_name("M2_detection").detail["per_class"]
    for klass, row in per_class.items():
        assert row["injected"] >= 100, f"{klass} has only {row['injected']} injected instances"


# -- H2: safe-fix decisioning ---------------------------------------------


def test_fr7_auto_precision_gate(report: M.EvalReport) -> None:
    """M3 ≥ 0.98 — the headline safety gate (SCOPE.md D1)."""
    metric = _assert_gate(report, "M3_auto_fix_precision")
    assert metric.detail["auto_changes"] > 1000, "the gate must be measured on real volume"


def test_fr7_trap_zero_auto(report: M.EvalReport) -> None:
    """M3_trap = 0: hand-vetted ambiguity may never be auto-touched (US-3)."""
    _assert_gate(report, "M3_trap_auto_changes")


def test_fr7_clean_file_zero_auto(report: M.EvalReport) -> None:
    """M3_clean = 0: a clean file passes through byte-equivalent."""
    _assert_gate(report, "M3_clean_auto_changes")


def test_fr6_clean_file_zero_findings(report: M.EvalReport) -> None:
    """M3_clean_findings = 0: no false alarms on alarm-free goldens."""
    metric = _assert_gate(report, "M3_clean_findings")
    assert len(metric.detail["per_file"]) == 5


def test_fr7_repair_auto_gate(report: M.EvalReport) -> None:
    """M4 repair_auto ≥ 0.85 blocks "route everything hard to review"."""
    _assert_gate(report, "M4_repair_auto")


def test_fr7_repair_auto_min_gate(report: M.EvalReport) -> None:
    """M4 repair_auto_min ≥ 0.85 over the five fully-auto classes."""
    metric = _assert_gate(report, "M4_repair_auto_min")
    assert set(metric.detail["classes"]) == set(M.REPAIR_MIN_CLASSES)


def test_fr7_repair_total_gate(report: M.EvalReport) -> None:
    """M4 repair_total ≥ 0.90: the review tier carries real proposals."""
    _assert_gate(report, "M4_repair_total")


def test_fr7_trap_disposition_gate(report: M.EvalReport) -> None:
    """M7 = 1.0: every hand-authored trap disposition is actually produced."""
    metric = _assert_gate(report, "M7_trap_disposition")
    assert metric.detail["expectations"] == metric.detail["satisfied"] >= 9


def test_us3_ambiguous_date_carries_both_interpretations(report: M.EvalReport) -> None:
    """The flagship `03/04/2021` case: both readings attached, nothing applied."""
    trap = next(o for o in report.outcomes if o.name == "trap_ambiguous_dates.csv")
    items = [i for i in trap.review_items if i["rule"] == "fix.date_canon_ambiguous"]
    assert len(items) == 1, "D9 requires exactly one column-level review item"
    assert set(items[0]["proposal"]["formats"]) == {"%d/%m/%Y", "%m/%d/%Y"}
    assert not trap.auto_changes


# -- FR-9 / FR-16 ----------------------------------------------------------


def test_fr9_reversibility_gate(report: M.EvalReport) -> None:
    """M5 = 1.0 over every corrupted run, every golden run, and a revision 2."""
    metric = _assert_gate(report, "M5_reversibility")
    assert metric.detail["runs"] == 19  # 13 corrupted + 5 golden + 1 revision


def test_fr16_determinism_gate(report: M.EvalReport) -> None:
    """M6 = 1.0 across two processes with different PYTHONHASHSEED values."""
    metric = _assert_gate(report, "M6_determinism")
    assert len(metric.detail["fixtures"]) == 4


# -- fixture invariants CI has to re-assert --------------------------------


def test_fixture_class_shares_are_pinned() -> None:
    """The M4 arithmetic depends on the op mix, so CI re-checks it (EVALS §4.2)."""
    totals = M.expected()["totals"]
    sys.path.insert(0, str(EVALS_DIR / "fixtures"))
    import generate

    for klass, target in generate.CLASS_SHARES.items():
        realized = totals["class_shares"][klass]
        assert abs(realized - target) <= generate.SHARE_TOLERANCE, (
            f"class {klass} share {realized} drifted from the pinned {target}"
        )


def test_fixture_counts_match_evals_doc() -> None:
    """13 corrupted / 5 golden / 7 trap fixtures and 91 labeled columns."""
    assert len(M.corrupted_fixtures()) == 13
    assert len(M.golden_fixtures()) == 5
    assert len(M.trap_fixtures()) == 7
    labels = M.type_labels()
    total = sum(len(labels[M.manifest_for(path)["golden"]]) for path in M.corrupted_fixtures())
    assert total == 91


def test_forbidden_json_covers_every_trap() -> None:
    """Every trap file carries at least one hand-authored expectation."""
    names = {path.name for path in M.trap_fixtures()}
    assert set(M.forbidden()) == names


# -- the runner ------------------------------------------------------------


def test_eval_runner_passes() -> None:
    """``run.py`` is runnable with zero configuration and exits 0.

    Determinism is skipped here only because it is already gated by
    ``test_fr16_determinism_gate`` and spawns eight subprocesses of its own;
    every other metric is recomputed end-to-end in this child process.
    """
    result = subprocess.run(
        [sys.executable, str(EVALS_DIR / "run.py"), "--skip-determinism"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    assert "ALL GATES PASS" in result.stdout
