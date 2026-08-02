"""Pytest-enforced gates — one test per threshold in EVALS.md.

Test names reference the FR ids they cover so the FR -> test mapping is
auditable, as CONVENTIONS.md requires.
"""

from __future__ import annotations

import baselines
import metrics
import pytest
from d0 import check_determinism, check_ledger_replay, check_world_pin


@pytest.fixture(scope="module")
def scenario_scores() -> list[metrics.ScenarioScore]:
    return metrics.score_scenarios()


@pytest.fixture(scope="module")
def shipped_results() -> list[metrics.ShippedResult]:
    return metrics.score_shipped()


def _failures(scores: list[metrics.ScenarioScore], attr: str) -> list[str]:
    return [s.case_id for s in scores if not getattr(s, attr)]


# --------------------------------------------------------------------------
# M1 — plan optimality against the independent oracle (FR-7, FR-8)
# --------------------------------------------------------------------------


def test_gate_m1a_optimality_small_fr7_fr8(scenario_scores: list[metrics.ScenarioScore]) -> None:
    small = [s for s in scenario_scores if s.tier == "small"]
    assert len(small) == 52
    assert metrics.m1(scenario_scores, "small") == 1.0, (
        f"suboptimal on {_failures(small, 'optimal')}"
    )


def test_gate_m1b_optimality_stress_fr7(scenario_scores: list[metrics.ScenarioScore]) -> None:
    stress = [s for s in scenario_scores if s.tier == "stress"]
    assert len(stress) == 12
    assert metrics.m1(scenario_scores, "stress") == 1.0, (
        f"suboptimal on {_failures(stress, 'optimal')}"
    )


# --------------------------------------------------------------------------
# M2 — every emitted plan is executable as written (FR-7, FR-3, FR-12)
# --------------------------------------------------------------------------


def test_gate_m2_validity_fr7_fr3_fr12(scenario_scores: list[metrics.ScenarioScore]) -> None:
    invalid = [s.case_id for s in scenario_scores if s.valid_plans != s.expected_plans]
    assert metrics.m2(scenario_scores) == 1.0, f"invalid or missing plans in {invalid}"


# --------------------------------------------------------------------------
# M3 — ranking, verdict and caveats (FR-9, FR-10)
# --------------------------------------------------------------------------


def test_gate_m3_ranking_verdict_caveats_fr9_fr10(
    scenario_scores: list[metrics.ScenarioScore],
) -> None:
    assert metrics.m3(scenario_scores) == 1.0, (
        f"ranking/verdict/caveat mismatch on {_failures(scenario_scores, 'ranking_ok')}"
    )


# --------------------------------------------------------------------------
# M4 — value accounting to the cent (FR-8)
# --------------------------------------------------------------------------


def test_gate_m4_accounting_fr8() -> None:
    cases = metrics.accounting_cases()
    assert len(cases) == 30
    failures = [
        (case["id"], metrics.score_accounting(case)[1])
        for case in cases
        if not metrics.score_accounting(case)[0]
    ]
    assert metrics.m4(cases) == 1.0, f"accounting mismatches: {failures}"


# --------------------------------------------------------------------------
# M5 — offline goal parsing (FR-5)
# --------------------------------------------------------------------------


def test_gate_m5_parser_fr5() -> None:
    cases = metrics.parser_cases()
    assert len(cases) == 60
    assert metrics.m5(cases) >= 0.90


# --------------------------------------------------------------------------
# M6 — the engine is usable on the dataset that actually ships (FR-1, FR-7)
# --------------------------------------------------------------------------


def test_gate_m6_shipped_world_fr1_fr7(shipped_results: list[metrics.ShippedResult]) -> None:
    assert len(shipped_results) == 12
    failures = [(r.goal_id, r.detail) for r in shipped_results if not r.passed]
    assert metrics.m6(shipped_results) == 1.0, f"shipped-world failures: {failures}"
    emitting = sum(1 for r in shipped_results if r.plans > 0)
    assert emitting >= 9, f"only {emitting}/12 shipped-world goals emit a plan"


# --------------------------------------------------------------------------
# M7 — randomized differential optimality (FR-7)
# --------------------------------------------------------------------------


def test_gate_m7_random_differential_fr7() -> None:
    cases = metrics.random_cases()
    assert len(cases) == 200
    results = metrics.score_random(cases)
    misses = [case["id"] for case, ok in zip(cases, results, strict=True) if not ok]
    assert metrics.m7(results) == 1.0, f"random-scenario mismatches: {misses}"


# --------------------------------------------------------------------------
# Baseline ceilings — the engine-vs-baseline gap is asserted, not assumed
# --------------------------------------------------------------------------


def test_baseline_ceilings(shipped_results: list[metrics.ShippedResult]) -> None:
    cases = metrics.search_cases()
    greedy = metrics.score_scenarios(cases, runner=baselines.greedy_runner())
    ungated = metrics.score_scenarios(
        cases, runner=baselines.greedy_runner(quantize=False, gating=False)
    )
    cpp = metrics.score_scenarios(cases, reranker=baselines.cpp_first)
    assert metrics.m1(greedy, "small") <= 0.45
    assert metrics.m1(greedy, "stress") <= 0.25
    assert metrics.m2(ungated) <= 0.85
    assert metrics.m3(cpp) <= 0.70
    assert metrics.m4(calculator=baselines.NaiveCalculator) <= 0.45
    assert metrics.m5(parser=baselines.KeywordParser(metrics.eval_world("small_a"))) <= 0.60
    assert metrics.m7(metrics.score_random(runner=baselines.greedy_random_runner)) <= 0.45
    assert metrics.m6(baselines.unpruned_shipped_results()) <= 0.60
    _ = shipped_results


# --------------------------------------------------------------------------
# D0 — determinism and state integrity (no score)
# --------------------------------------------------------------------------


def test_d0_determinism_fr16() -> None:
    ok, detail = check_determinism()
    assert ok, detail


def test_d0_ledger_replay_fr2() -> None:
    ok, detail = check_ledger_replay()
    assert ok, detail


def test_d0_world_pin_fr11() -> None:
    ok, detail = check_world_pin()
    assert ok, detail
