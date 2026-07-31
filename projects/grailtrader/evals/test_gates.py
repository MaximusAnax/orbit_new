"""Pytest-enforced eval gates (EVALS.md "Naive baselines and gates").

One test per gate family, named after the FRs it grades. Every test asserts its
**population floor first** (EVALS' denominator rule: a metric whose population
falls below its floor is a failure, never a pass and never a division by zero),
then the rate itself.

The whole pipeline — ingest, index build, replay, three placebo runs, the render
pass and scenario B — runs once per session behind ``scorecard``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if __package__ in (None, ""):  # pragma: no cover - direct `pytest evals/test_gates.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics as M
from evals.generate_scenario import MIN_SALES, PARENT_WEIGHT_WINDOW_WEEKS, WINDOW_WEEKS
from evals.harness import PLACEBO_SEEDS, PipelineResult, load_scenario, run_pipeline


@pytest.fixture(scope="session")
def result_a() -> PipelineResult:
    return run_pipeline(load_scenario("scenario_a"), placebo_seeds=PLACEBO_SEEDS)


@pytest.fixture(scope="session")
def result_b() -> PipelineResult:
    return run_pipeline(load_scenario("scenario_b"))


@pytest.fixture(scope="session")
def scorecard(result_a: PipelineResult, result_b: PipelineResult) -> M.Scorecard:
    return M.build_scorecard(result_a, result_b)


def _check(card: M.Scorecard, key: str) -> None:
    row = card.by_key(key)
    assert row.gate is not None
    assert row.passing, (
        f"{key} ({row.gate.label}) = {row.value:.4f} violates gate {row.gate.describe()}"
        f" (n={row.n})"
    )


def test_fixture_construction_matches_the_engine_config_fr1_fr4(result_a: PipelineResult) -> None:
    """The coverage truth is only meaningful if it was derived against the real config."""
    config = result_a.data.ctx.index_config
    assert (config.window_weeks, config.min_sales) == (WINDOW_WEEKS, MIN_SALES)
    assert config.parent_weight_window_weeks == PARENT_WEIGHT_WINDOW_WEEKS
    coverage_truth = result_a.data.coverage_truth
    assert coverage_truth["window_weeks"] == config.window_weeks
    assert coverage_truth["min_sales"] == config.min_sales


def test_gate_m0_floors_fr4_fr8(scorecard: M.Scorecard) -> None:
    """M0 — coverage and activity floors: abstaining from the hard cases must not pass."""
    for key in (
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
    ):
        _check(scorecard, key)


def test_gate_m1a_index_fidelity_fr4(scorecard: M.Scorecard) -> None:
    """M1a — median scale-aligned index error against the planted latent truth."""
    row = scorecard.by_key("M1a")
    assert row.n is not None and row.n >= 2000, f"too few graded index cells: {row.n}"
    _check(scorecard, "M1a")


def test_gate_m1a_tails_fr4(scorecard: M.Scorecard) -> None:
    """M1a tails — p90 over all cells and the per-density-class per-stratum maxima."""
    for key in ("M1a-p90", "M1a-dense", "M1a-sparse"):
        _check(scorecard, key)


def test_gate_m1b_fence_fr3(scorecard: M.Scorecard) -> None:
    """M1b — the FR-3 fence runs and is not degenerate (mechanism gate)."""
    recall = scorecard.by_key("M1b-recall")
    assert recall.n is not None and recall.n >= 200, f"too few planted outliers: {recall.n}"
    clean = scorecard.by_key("M1b-false")
    assert clean.n is not None and clean.n >= 10000, f"too few clean sales: {clean.n}"
    _check(scorecard, "M1b-recall")
    _check(scorecard, "M1b-false")


def test_gate_m2a_hit_rate_fr8_fr10(scorecard: M.Scorecard) -> None:
    """M2a — directional hit rate over actionable advice, graded at each advice's own H*."""
    row = scorecard.by_key("M2a")
    assert row.n is not None and row.n >= M.GATES["M0b"].threshold, (
        f"actionable population {row.n} is below the M0b floor"
    )
    _check(scorecard, "M2a")
    _check(scorecard, "M2-excl")


def test_gate_m2b_spread_fr8(scorecard: M.Scorecard) -> None:
    """M2b — the realized buy-minus-sell spread: both legs must earn their keep."""
    _check(scorecard, "M2b")


def test_gate_m3_calibration_fr8(scorecard: M.Scorecard) -> None:
    """M3 — separation, anti-overclaim and the hi-bucket floor, over candidates."""
    assert scorecard.by_key("M0c").passing, "a confidence bucket is below its population floor"
    for key in ("M3a", "M3b", "M3c"):
        _check(scorecard, key)


def test_gate_m4_placebo_fr10(scorecard: M.Scorecard) -> None:
    """M4 — scrambled event dates must show no skill, on the worst of three seeds."""
    assert scorecard.by_key("M0e").passing, "a placebo run is below its activity floor"
    for key in ("M4a", "M4b"):
        _check(scorecard, key)


def test_gate_m5_framing_fr9(scorecard: M.Scorecard) -> None:
    """M5 — every rendered advice compliant, and every hand-authored verdict matched."""
    rendered = scorecard.by_key("M5a")
    assert rendered.n is not None and rendered.n >= 1000, (
        f"too little advice rendered: {rendered.n}"
    )
    cases = scorecard.by_key("M5b")
    assert cases.n is not None and cases.n >= 10, f"too few frame cases: {cases.n}"
    outcomes = scorecard.diagnostics["frame_cases"]
    assert sum(1 for case in outcomes if case["expected"] == "block") >= 6
    assert sum(1 for case in outcomes if case["expected"] == "render") >= 4
    _check(scorecard, "M5a")
    _check(scorecard, "M5b")


def test_gate_m6_mismatch_fr4_fr8(scorecard: M.Scorecard) -> None:
    """M6 — scenario B: skill degrades gracefully and confidence still does not overclaim."""
    row = scorecard.by_key("M6b")
    assert row.n is not None and row.n >= 100, f"scenario B actionable population {row.n} too small"
    for key in ("M6a", "M6b", "M6c"):
        _check(scorecard, key)


def test_replay_entry_is_the_following_week_fr10(result_a: PipelineResult) -> None:
    """FR-10's entry-timing invariant, re-asserted over the whole graded replay."""
    assert M.entry_invariant_holds(result_a.real)
    for replay in result_a.placebo.values():
        assert M.entry_invariant_holds(replay)


def test_every_gate_passes(scorecard: M.Scorecard) -> None:
    """A single assertion covering the whole table, so a new gate cannot be forgotten."""
    failures = [f"{row.key}={row.value:.4f}" for row in scorecard.failures]
    assert not failures, f"failing gates: {failures}"
