"""Pytest-enforced eval gates (EVALS.md §5/§6).

One test per gate, plus the fixture invariants CI has to re-assert and an
end-to-end run of ``run.py``.  Every test calls the metric functions directly
(the report is computed once and cached), so ``uv run pytest flowlist/`` fails
on any quality regression.

Test names carry FR ids per EVALS.md §7.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from evals import run as eval_run
from evals.metrics import (
    DEGENERATE_BASELINES,
    GATES,
    ExactSolverBroken,
    bpm_only_auc,
    by_id,
    construction_only_order,
    degenerate_m4,
    evaluate,
    exact_optimal,
    expected,
    fixture_invariants,
    fixtures,
    golden_orderings,
    heuristic_order,
    matrix_of,
    order_total,
    reorder,
    suite,
)

#: EVALS §4's authoring invariant: keeps M2 >= 0.90 and M2b >= 0.10
#: simultaneously satisfiable as the labelled pairs are edited.
BPM_ONLY_CEILING = 0.80
#: EVALS §4's discrimination invariant.
MIN_TRAP_INSTANCES = 3
TRAP_RATIO = 0.97


def _assert_gate(name: str) -> None:
    metric = evaluate().get(name)
    assert metric.passed, f"{name} = {metric.value:.4f} (gate {metric.gate}) — {metric.detail}"


# --------------------------------------------------------------------------- #
# H1 — transition-scoring fidelity
# --------------------------------------------------------------------------- #


def test_fr5_key_relation_accuracy_gate() -> None:
    """M1 = 1.00: a finite lookup against the public Camelot chart."""
    _assert_gate("M1_key_relation_accuracy")
    assert evaluate().get("M1_key_relation_accuracy").value == 1.0


def test_fr6_pair_ranking_auc_gate() -> None:
    """M2 >= 0.90 on the hand-labelled pairs."""
    _assert_gate("M2_pair_ranking_auc")


def test_fr6_anti_gaming_margin_gate() -> None:
    """M2b >= 0.10: harmony/energy/loudness must carry real ranking weight."""
    _assert_gate("M2b_anti_gaming_margin")
    margin = evaluate().get("M2b_anti_gaming_margin")
    assert margin.value == pytest.approx(
        evaluate().get("M2_pair_ranking_auc").value - bpm_only_auc(), abs=1e-12
    )


def test_fr6_component_monotonicity_gate() -> None:
    """M3 = 1.00: the D3-D5 formulas' exact consequences."""
    _assert_gate("M3_component_monotonicity")


# --------------------------------------------------------------------------- #
# H2 — ordering optimization quality
# --------------------------------------------------------------------------- #


def test_fr8_exact_optimality_gate() -> None:
    """M4_mean >= 0.97 against fixed-endpoint Held-Karp ground truth."""
    _assert_gate("M4_exact_optimality_mean")


def test_fr8_exact_optimality_min_gate() -> None:
    """M4_min >= 0.90: no single instance may fall off a cliff."""
    _assert_gate("M4_exact_optimality_min")


def test_fr8_degenerate_rejection_gate() -> None:
    """M4b = 1.00: the M4 thresholds must reject a construction-only flowlist."""
    _assert_gate("M4b_degenerate_rejection")


def test_fr8_local_search_is_load_bearing_on_the_exact_suite() -> None:
    """The gate above, restated as the concrete failure it prevents.

    ``optimizer.construct`` is literally what ``reorder`` returns with its local
    search deleted, so if these numbers cleared the M4 gates then M4 would
    certify nothing about the hard part.  Checked here as well as through the
    gate so the failure message names the algorithm, not a metric id.
    """
    mean, minimum = degenerate_m4()["construction_only"]
    assert mean < GATES["M4_exact_optimality_mean"], (
        f"construction-only flowlist scores M4_mean={mean:.4f} and would PASS the gate: "
        "the exact suite no longer separates construction from local search"
    )
    assert minimum < GATES["M4_exact_optimality_min"], (
        f"construction-only flowlist scores M4_min={minimum:.4f} and would PASS the gate"
    )
    # ... and the shipped optimizer must clear the same bar it rejects.
    assert evaluate().get("M4_exact_optimality_mean").value > mean
    assert evaluate().get("M4_exact_optimality_min").value > minimum


def test_fr8_local_search_lifts_every_hard_instance() -> None:
    """On every instance the degenerate baseline fails, local search recovers it."""
    lifted = 0
    for fixture in suite("exact"):
        matrix = matrix_of(fixture.id)
        degenerate = order_total(construction_only_order(fixture), matrix)
        found = order_total(heuristic_order(fixture), matrix)
        assert found >= degenerate - 1e-12, (
            f"{fixture.id}: local search made the construction worse "
            f"({found:.6f} < {degenerate:.6f})"
        )
        lifted += found > degenerate + 1e-12
    assert lifted >= MIN_TRAP_INSTANCES, (
        f"local search improved only {lifted} of {len(suite('exact'))} exact instances; "
        "the exact suite is too easy to exercise it"
    )


def test_fr8_heuristic_never_beats_the_exact_optimum() -> None:
    """EVALS §3-M4's hard sanity assert, restated as its own failure mode.

    A ratio above 1 would mean ``exact_optimal`` is not exact — the degenerate
    "implement the ground truth as the heuristic" case.  ``m4_exact_optimality``
    raises :class:`ExactSolverBroken` in that situation, so simply reaching a
    computed M4 value is the assertion; this test also checks the guard is live.
    """
    assert evaluate().get("M4_exact_optimality_mean").value <= 1.0 + 1e-9
    assert issubclass(ExactSolverBroken, AssertionError)


def test_fr9_anchored_instance_is_scored_under_its_anchors() -> None:
    """The anchored M4 instance compares like with like (EVALS §3-M4, FR-9)."""
    anchored = [f for f in suite("exact") if f.start is not None or f.end is not None]
    assert anchored, "the exact suite must contain an anchored instance"
    for fixture in anchored:
        order = heuristic_order(fixture)
        assert order[0] == fixture.start
        assert order[-1] == fixture.end
        # Ground truth is computed under the same constraints.
        truth = exact_optimal(matrix_of(fixture.id), fixture.start, fixture.end)
        assert truth.order[0] == fixture.start
        assert truth.order[-1] == fixture.end


def test_fr8_planted_chain_recovery_gate() -> None:
    """M5 >= 0.92 (per-playlist ratios clamped at 1.0 before averaging)."""
    _assert_gate("M5_planted_chain_recovery")


def test_fr8_planted_chain_recovery_min_gate() -> None:
    """M5_min >= 0.85: one collapsed playlist must fail loudly."""
    _assert_gate("M5_planted_chain_recovery_min")


def test_fr8_baseline_margin_gate() -> None:
    """M6 >= +0.08 over bpm_sort — the product claim itself."""
    _assert_gate("M6_baseline_margin")


def test_fr8_baseline_margin_min_gate() -> None:
    """M6_min > 0: beat bpm_sort on *every* messy playlist."""
    _assert_gate("M6_baseline_margin_min")
    assert evaluate().get("M6_baseline_margin_min").value > 0.0


def test_fr8_baseline_margin_is_not_vacuous() -> None:
    """bpm_sort scores margin 0 against itself (EVALS §5's vacuity check)."""
    report = evaluate()
    bpm_sort = report.get("baseline_bpm_sort").value
    heuristic = report.get("heuristic_messy_mean").value
    assert heuristic - bpm_sort == pytest.approx(report.get("M6_baseline_margin").value, abs=1e-9)
    assert bpm_sort - bpm_sort == 0.0
    assert report.get("baseline_random").value < bpm_sort < heuristic


# --------------------------------------------------------------------------- #
# FR-15 — determinism
# --------------------------------------------------------------------------- #


def test_fr15_determinism_gate() -> None:
    """M7 = 1.00: repeatable, self-consistent, and matching the goldens."""
    _assert_gate("M7_determinism")


def test_fr15_golden_orderings_cover_every_suite() -> None:
    """The three M7 fixtures span the exact, planted and messy suites."""
    goldens = golden_orderings()
    covered = {by_id(fixture_id).suite for fixture_id in goldens}
    assert covered == {"exact", "planted", "messy"}
    for fixture_id, ordering in goldens.items():
        fixture = by_id(fixture_id)
        assert sorted(ordering) == sorted(fixture.track_ids)


# --------------------------------------------------------------------------- #
# Fixture invariants (EVALS §4)
# --------------------------------------------------------------------------- #


def test_fixture_invariants() -> None:
    """The fixture properties the gates' meaning depends on.

    * ``bpm_only_auc <= 0.80`` keeps M2 and M2b simultaneously satisfiable, so
      a label-mix drift fails CI instead of silently making M2b impossible.
    * At least three exact instances defeat a degenerate baseline, and — the
      property EVALS §4 actually claims — *both* degenerate baselines fail both
      M4 gates, so passing M4 requires the local search to work.
    """
    invariants = fixture_invariants()
    assert invariants["bpm_only_auc"] <= BPM_ONLY_CEILING, (
        f"bpm_only_auc drifted to {invariants['bpm_only_auc']:.3f}; the M2/M2b gates "
        "are no longer simultaneously satisfiable — rebalance transition_pairs.json"
    )
    below = invariants["instances_below_0.97"]
    assert below >= MIN_TRAP_INSTANCES, (
        f"only {below} exact instance(s) score below {TRAP_RATIO} against a degenerate "
        f"baseline: {invariants['worst_degenerate_ratios']}"
    )
    for name in DEGENERATE_BASELINES:
        mean, minimum = invariants["degenerate_m4"][name]
        assert mean < GATES["M4_exact_optimality_mean"], (
            f"{name} would PASS M4_mean ({mean:.4f}); the exact suite no longer "
            "discriminates construction from local search"
        )
        assert minimum < GATES["M4_exact_optimality_min"], (
            f"{name} would PASS M4_min ({minimum:.4f})"
        )


def test_fixture_expected_json_is_not_stale() -> None:
    """The committed generator measurements still describe the committed fixtures.

    Gates are asserted on live values (EVALS §5), but ``expected.json`` is
    printed and reviewed, so a fixture edit that left it behind would publish
    numbers that are no longer true.  Reconciling the two is cheap and turns
    that into a CI failure.
    """
    invariants = fixture_invariants()
    for name, committed in invariants["committed_ratios"].items():
        live = invariants["degenerate_ratios"][name]
        assert set(committed) == set(live), f"{name}: expected.json covers a different suite"
        for fixture_id, value in committed.items():
            assert value == pytest.approx(live[fixture_id], abs=1e-6), (
                f"expected.json {name} ratio for {fixture_id} is {value}, but the committed "
                f"fixtures score {live[fixture_id]:.6f} — regenerate the fixtures"
            )


def test_fr15_m7_repeatability_check_is_not_vacuous() -> None:
    """At least one M7 fixture must actually depend on the seed.

    "Same seed, same order" is free for an implementation that ignores its
    seed; without a seed-sensitive fixture in the golden set that half of M7
    can never fail (REVIEW.md #26).
    """
    varies = []
    for fixture_id in golden_orderings():
        fixture = by_id(fixture_id)
        matrix = matrix_of(fixture_id)
        seven = reorder(matrix, seed=7, start=fixture.start, end=fixture.end).order
        eight = reorder(matrix, seed=8, start=fixture.start, end=fixture.end).order
        if seven != eight:
            varies.append(fixture_id)
    assert varies, (
        "no golden fixture changes with the seed, so M7's repeatability check "
        "cannot fail — swap in a seed-sensitive fixture"
    )


def test_fixture_planted_chains_are_engineered_as_documented() -> None:
    """Every planted transition clears 0.80 and the chains average ~0.88."""
    rows = expected()["playlists"]
    planted = {pid: row for pid, row in rows.items() if "planted_mean" in row}
    assert len(planted) == 6
    for pid, row in planted.items():
        assert row["planted_min"] >= 0.80, f"{pid} has a planted transition below 0.80"
        assert 0.85 <= row["planted_mean"] <= 0.92, f"{pid} planted mean {row['planted_mean']}"


def test_fixture_suites_match_the_eval_plan() -> None:
    """Suite sizes and instance sizes are the ones EVALS §4 specifies."""
    sizes = {name: [f.n for f in suite(name)] for name in ("exact", "planted", "messy", "arc")}
    assert len(sizes["exact"]) == 10 and all(8 <= n <= 14 for n in sizes["exact"])
    assert len(sizes["planted"]) == 6 and all(50 <= n <= 150 for n in sizes["planted"])
    assert len(sizes["messy"]) == 4 and all(30 <= n <= 80 for n in sizes["messy"])
    assert sizes["arc"] == [20]
    assert len(fixtures()) == 21


def test_fixtures_are_hermetic_and_committed() -> None:
    """No fixture file is generated at import time; all are on disk."""
    root = Path(__file__).resolve().parent / "fixtures"
    for name in (
        "catalog.json",
        "expected.json",
        "golden_orderings.json",
        "key_relations.json",
        "transition_pairs.json",
        "generate.py",
    ):
        assert (root / name).is_file(), f"missing committed fixture {name}"
    assert len(list((root / "playlists").glob("*.json"))) == 21


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


def test_eval_runner_passes() -> None:
    """``python evals/run.py`` prints a passing scorecard and exits 0."""
    script = Path(eval_run.__file__).resolve()
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-2000:]
    assert "RESULT: PASS" in completed.stdout
    for name, gate in GATES.items():
        if gate is not None:
            assert name in completed.stdout, f"{name} missing from the scorecard"
