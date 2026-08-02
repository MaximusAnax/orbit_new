"""Pytest-enforced eval gates (EVALS.md §5/§6).

One test per gate row in EVALS §5 — names carry the FR ids they defend, so the
SCOPE.md → test mapping stays auditable — plus the fixture-integrity tests:
margin assertions against the live naive baseline, byte-identical fixture
regeneration, expected-digest re-derivation, golden hashes, and an end-to-end
``run.py`` execution. The full report is computed once per session; every gate
asserts against that one report — the same numbers ``run.py`` prints.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

EVALS_DIR = Path(__file__).resolve().parent
FIXTURES = EVALS_DIR / "fixtures"
if str(EVALS_DIR) not in sys.path:  # match run.py's own import setup
    sys.path.insert(0, str(EVALS_DIR))

import run as eval_run  # noqa: E402
from metrics import EvalReport, MetricResult, load_fixtures  # noqa: E402


def _load_script(name: str) -> ModuleType:
    """Import a fixtures/ script (generate.py, derive_expected.py) by path."""

    spec = importlib.util.spec_from_file_location(
        f"tickerpress_fixture_{name}", FIXTURES / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve via sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def report() -> EvalReport:
    return eval_run.evaluate()


def gate(report: EvalReport, name: str) -> MetricResult:
    result = report.by_name(name)
    assert result.passed, f"{result.name}: {result.value} vs gate {result.gate} — {result.detail}"
    return result


# ---------------------------------------------------------------------------
# H1 — mention disambiguation (FR-5/FR-6)
# ---------------------------------------------------------------------------


def test_fr6_mention_f1_gate(report: EvalReport) -> None:
    """M1: pair-level F1 over the full 108-item corpus."""

    assert gate(report, "M1 mention_f1").value >= 0.92


def test_fr6_ambiguous_f1_gate(report: EvalReport) -> None:
    """M1_amb: the headline gate, scored only where disambiguation decides."""

    assert gate(report, "M1_amb ambiguous_f1").value >= 0.85


def test_fr6_ambiguous_f1_base_gate(report: EvalReport) -> None:
    """M1_amb_base: one vote per independent authoring decision."""

    assert gate(report, "M1_amb_base").value >= 0.82


def test_fr6_ambiguous_f1_ablated_gate(report: EvalReport) -> None:
    """M1_amb_abl: the general mechanism with all per-company terms emptied."""

    assert gate(report, "M1_amb_abl (no terms)").value >= 0.75


def test_fr6_lexicon_specificity_gate(report: EvalReport) -> None:
    """M6_lex: frozen manifest hashes match and no cue occurs in exactly one base."""

    assert gate(report, "M6_lex specificity").value == 1.0


# ---------------------------------------------------------------------------
# H2 — syndication dedup (FR-7)
# ---------------------------------------------------------------------------


def test_fr7_dedup_precision_gate(report: EvalReport) -> None:
    assert gate(report, "M2 dedup P_pair").value >= 0.95


def test_fr7_dedup_recall_gate(report: EvalReport) -> None:
    assert gate(report, "M2 dedup R_pair").value >= 0.85


def test_fr7_no_link_zero_merges(report: EvalReport) -> None:
    """M2_trap: zero, not small — a merge is a design bug, not noise."""

    assert gate(report, "M2_trap merged no-link").value == 0


def test_fr7_no_link_near_band(report: EvalReport) -> None:
    """M2_near: >=4 of the 5 near-band pairs compute J >= 0.40 yet stay apart."""

    assert gate(report, "M2_near J>=0.40").value >= 4


# ---------------------------------------------------------------------------
# Relevance ranking (FR-8)
# ---------------------------------------------------------------------------


def test_fr8_relevance_ordering_gate(report: EvalReport) -> None:
    assert gate(report, "M3 relevance_ordering").value >= 0.95


def test_fr8_ordering_coverage_gate(report: EvalReport) -> None:
    """M3_cov: |O| >= 55 and all 12 designed count-vs-placement traps present."""

    assert gate(report, "M3_cov |O|").value >= 55
    assert gate(report, "M3_cov traps in O").value >= 12


# ---------------------------------------------------------------------------
# Delivery ledger + determinism (FR-11, FR-16)
# ---------------------------------------------------------------------------


def test_fr11_exactly_once_gate(report: EvalReport) -> None:
    """M4: all six scripted exactly-once checks pass."""

    assert gate(report, "M4 delivery_exactly_once").value == 1.0
    checks = report.extras.get("m4_checks", [])
    assert checks, "M4 scenario recorded no checks"
    for check in checks:
        assert check["passed"], f"M4 check failed: {check['check']} — {check['detail']}"


def test_fr16_determinism_gate(report: EvalReport) -> None:
    """M5: cross-process identity under differing PYTHONHASHSEED + goldens."""

    assert gate(report, "M5 determinism").value == 1.0


# ---------------------------------------------------------------------------
# Margins against the live naive baseline (EVALS §5)
# ---------------------------------------------------------------------------


def test_naive_margins(report: EvalReport) -> None:
    """Every gate must clear the live-computed naive baseline by its margin."""

    margins = [metric for metric in report.metrics if metric.name.startswith("margin ")]
    assert len(margins) == len(eval_run.NAIVE_MARGINS)
    for metric in margins:
        assert metric.passed, f"{metric.name}: {metric.value:.3f} < {metric.gate} ({metric.detail})"


# ---------------------------------------------------------------------------
# Fixture integrity (EVALS §4/§6)
# ---------------------------------------------------------------------------


def test_fixtures_regenerate_identically(tmp_path: Path) -> None:
    """generate.py --seed 4242 reproduces the committed feeds and story groups."""

    generate = _load_script("generate")
    generate.write_fixtures(tmp_path, seed=4242)
    for feed in sorted((FIXTURES / "feeds").iterdir()):
        regenerated = tmp_path / "feeds" / feed.name
        assert regenerated.read_bytes() == feed.read_bytes(), f"{feed.name} drifted"
    committed = (FIXTURES / "labels" / "story_groups.json").read_bytes()
    assert (tmp_path / "labels" / "story_groups.json").read_bytes() == committed


def test_expected_digest_derivation_matches() -> None:
    """labels/expected_digest.json equals a fresh engine-free derivation."""

    derive_expected = _load_script("derive_expected")
    fresh = derive_expected.derive()
    committed = json.loads(
        (FIXTURES / "labels" / "expected_digest.json").read_text(encoding="utf-8")
    )
    assert fresh["expected"] == committed["expected"]
    assert fresh["alerted"] == committed["alerted"]
    assert fresh["story_relevance"] == committed["story_relevance"]


def test_golden_hashes() -> None:
    """The committed golden hashes match a live in-process pipeline run (M5)."""

    payload = eval_run.dump_state(load_fixtures(FIXTURES))
    live = eval_run.golden_hashes(payload)
    for name, digest in sorted(live.items()):
        stored = (eval_run.GOLDEN / f"{name}.sha256").read_text(encoding="utf-8").strip()
        assert stored == digest, f"golden {name}: stored {stored[:16]} != live {digest[:16]}"


def test_eval_runner_passes() -> None:
    """run.py end-to-end: scorecard prints and exits 0 with zero configuration."""

    completed = subprocess.run(
        [sys.executable, str(EVALS_DIR / "run.py")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "RESULT: PASS" in completed.stdout
