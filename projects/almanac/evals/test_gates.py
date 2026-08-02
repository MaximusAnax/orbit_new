"""Pytest-enforced eval gates — one test per threshold in EVALS.md section 5.

`uv run pytest almanac/` fails when any gate regresses.  Test names carry the FR
ids they defend so the SCOPE.md -> test mapping stays auditable.

The scenario runs are expensive (20 simulated years across four scenarios,
three seeds and two baseline policies), so they are built once per session and
every gate asserts against that one report — the same report `run.py` prints.
"""

from __future__ import annotations

import ast
import collections
import json
import subprocess
import sys
from pathlib import Path

import pytest

if __package__ in (None, ""):  # pragma: no cover - direct invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics as M
from evals.faulty_personalizer import FaultyPersonalizer, apply_case, load_cases, load_clean
from evals.run import PRIMARY_SEED, build_report, run_all
from evals.simulate import load_scenarios

EVALS_DIR = Path(__file__).resolve().parent
PROJECT = EVALS_DIR.parent
DATA = PROJECT / "data"


@pytest.fixture(scope="session")
def params() -> dict:
    return M.load_params()


@pytest.fixture(scope="session")
def runs() -> dict:
    return run_all(load_scenarios())


@pytest.fixture(scope="session")
def report(runs, params) -> M.EvalReport:
    return build_report(runs, params)


def gate(report: M.EvalReport, prefix: str) -> M.MetricResult:
    matches = [r for r in report.results if r.name.startswith(prefix)]
    assert matches, f"no metric named {prefix!r} in the report"
    return matches[0]


# --------------------------------------------------------------------------
# The gates (EVALS.md section 5)
# --------------------------------------------------------------------------


def test_fr7_constraint_compliance_gate(report):
    """M1: cooldown, uniqueness, eligibility, prompts, forcing, rescue, draws."""
    result = gate(report, "M1 constraint_compliance")
    assert result.passed, result.detail
    assert result.value == 0


def test_fr7_stream_debut_latency_gate(report):
    """M2a: US-3's 'captured this week appears within days'."""
    assert gate(report, "M2a stream_debut p50").passed
    assert gate(report, "M2a stream_debut p95").passed


def test_fr7_backlog_drain_gate(report):
    """M2b: a bulk import drains inside its capacity-derived horizon."""
    result = gate(report, "M2b backlog_drain")
    assert result.passed, result.detail
    assert result.value >= 1.0


def test_fr7_no_starvation_hard_bound(report):
    """M2 hard bound: nothing waits longer than 180 days, ever."""
    result = gate(report, "M2 hard bound")
    assert result.passed, result.detail


def test_fr7_mix_balance_gate(report):
    """M3: the novelty quota holds on the statistic the controller regulates."""
    result = gate(report, "M3 mix_balance")
    assert result.passed, result.detail
    assert result.value <= 0.12


def test_fr7_mix_balance_is_not_vacuous(report):
    """M3 non-vacuity: an empty or short window set is a FAIL, not a pass."""
    result = gate(report, "M3 qualifying windows")
    assert result.passed, result.detail
    assert result.value >= 60


def test_fr7_no_early_surfacing_gate(report):
    """M4a: nothing surfaces before its interval except the declared branches."""
    result = gate(report, "M4a early_violations")
    assert result.passed, result.detail
    assert result.value == 0


def test_fr7_proportional_spacing_gate(report):
    """M4b: relative spacing survives contention (Q3/Q1 of the overdue ratio)."""
    result = gate(report, "M4b proportional_fidelity")
    assert result.passed, result.detail
    assert result.value <= 3.0


def test_fr7_pinned_rescue_gate(report):
    """M5: every pinned interval, not most (US-5's locked promise)."""
    result = gate(report, "M5 pinned_recurrence")
    assert result.passed, result.detail
    assert result.value >= 1.0


def test_fr6_feedback_responsiveness_gate(report):
    """M6: duds fade relative to gems, measured against the planted quality."""
    result = gate(report, "M6 feedback_responsiveness")
    assert result.passed, result.detail


def test_fr14_archive_candidate_subcheck(report):
    """M6 sub-check: the archive-candidate list is exactly the flat_streak >= 3 set."""
    result = gate(report, "M6 archive sub-check")
    assert result.passed, result.detail


def test_fr17_determinism_replay_gate(report):
    """M7: identical seeds replay byte-identically; the fold equals its cache."""
    result = gate(report, "M7 determinism_replay")
    assert result.passed, result.detail
    assert result.value >= 1.0


def test_fr3_theme_suggestion_gate(report):
    """M8: held-split accuracy of the FR-3 lexicon suggester."""
    for prefix in ("M8 top1", "M8 hit3", "M8 worst_theme_hit3"):
        result = gate(report, prefix)
        assert result.passed, f"{prefix}: {result.value} ({result.detail})"


def test_fr10_personalizer_tamper_gate(report):
    """M9: every scripted malformation is rejected, no clean output is."""
    assert gate(report, "M9 recall").passed
    assert gate(report, "M9 fpr").passed


def test_fr6_cohort_sizes_are_large_enough(report):
    """M6 fails by emptiness if a cohort is thin; assert the fixture guarantees them."""
    sizes = report.diagnostics["almanac"]["m6_detail"]["cohort_sizes"]
    assert sizes["gem"] >= 10 and sizes["dud"] >= 10, sizes
    assert report.diagnostics["almanac"]["m6_detail"]["rates"]["gem"] > 0


def test_fr8_tiny_library_fallback_is_flagged(runs):
    """EVALS.md section 4: S3's fallback picks least-recently-seen and says so."""
    timeline = runs[("S3", PRIMARY_SEED, "almanac")]
    rows = M.ordered_surfacings(timeline)
    relaxed = [s for s in rows if s.select_pool.value == "relaxed"]
    assert relaxed, "S3 must exercise the tiny-library fallback"
    assert all(s.relaxed_cooldown for s in relaxed)
    assert len(rows) == len(timeline.calendar), "the tool never returns an empty card"

    last_seen: dict[str, object] = {}
    for s in rows:
        if s.select_pool.value == "relaxed":
            oldest = min(last_seen.values())
            assert last_seen[s.entry_id] == oldest, (
                f"{s.entry_id} on {s.on_date} was not the oldest"
            )
        last_seen[s.entry_id] = s.on_date


def test_fr10_rejected_personalization_falls_back(datasets_for_fallback):
    """M9's service half: a rejected output serves the template and records it."""
    service, on_date = datasets_for_fallback
    cards = service.materialize_day(on_date)
    assert cards
    for card in cards:
        assert card.surfacing.personalize_fell_back is True
        assert card.surfacing.personalized is False
        assert "https://" not in card.surfacing.prompt_text


@pytest.fixture
def datasets_for_fallback():
    import datetime as dt

    from almanac.factory import memory_service

    service = memory_service(seed=7, today=dt.date(2026, 1, 1))
    service.personalizer = FaultyPersonalizer("url_injected")
    service.capture(
        "The impediment to action advances action.",
        themes=["decision_and_action"],
        captured_on=dt.date(2026, 1, 1),
        now=dt.datetime(2026, 1, 1, 8, tzinfo=dt.UTC),
    )
    return service, dt.date(2026, 1, 1)


# --------------------------------------------------------------------------
# The runner itself
# --------------------------------------------------------------------------


def test_eval_runner_passes():
    """`python evals/run.py` runs with zero configuration and exits 0."""
    completed = subprocess.run(
        [sys.executable, str(EVALS_DIR / "run.py")],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-2000:]
    assert "ALL GATES PASS" in completed.stdout
    payload = json.loads(completed.stdout[completed.stdout.rindex("\n{\n") + 1 :])
    assert payload["passed"] is True
    assert all(entry["passed"] for entry in payload["metrics"].values())


# --------------------------------------------------------------------------
# Independence rule (EVALS.md sections 3 and 6)
# --------------------------------------------------------------------------


def test_metrics_module_does_not_import_the_engine():
    """M1d/M1e and M7c must not inherit the code path they gate."""
    tree = ast.parse((EVALS_DIR / "metrics.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [name for name in imported if name.startswith("almanac")]
    assert offenders == [], f"metrics.py must stay engine-free, found {offenders}"


# --------------------------------------------------------------------------
# Data floors for the committed datasets (EVALS.md section 6)
# --------------------------------------------------------------------------


def test_fr9_prompt_bank_floors():
    templates = json.loads((DATA / "prompts.json").read_text(encoding="utf-8"))
    themes = {row["id"] for row in json.loads((DATA / "themes.json").read_text(encoding="utf-8"))}
    by_pool = collections.defaultdict(list)
    for row in templates:
        by_pool[row["theme_id"]].append(row)
    kinds = {"reflect", "act", "reframe", "connect"}
    for theme in themes:
        pool = by_pool[theme]
        assert len(pool) >= 6, f"{theme} has {len(pool)} templates, needs 6"
        assert {row["kind"] for row in pool} == kinds, theme
    general = by_pool["general"]
    assert len(general) >= 8
    assert {row["kind"] for row in general} == kinds
    assert len({row["id"] for row in templates}) == len(templates)


def test_fr2_misattribution_records_cite_a_source():
    records = json.loads((DATA / "misattributions.json").read_text(encoding="utf-8"))
    assert len(records) >= 40
    for record in records:
        assert record["reference_url"].startswith("https://"), record["id"]
        assert record["verdict"] in {"misattributed", "disputed", "unverified"}
        assert record["pattern"].strip()
        assert record["likely_origin"].strip()


def test_fr3_theme_lexicon_floors():
    themes = json.loads((DATA / "themes.json").read_text(encoding="utf-8"))
    assert len(themes) == 16
    for theme in themes:
        assert len(theme["lexicon"]) >= 8, theme["id"]
        terms = [term["term"] for term in theme["lexicon"]]
        assert len(set(terms)) == len(terms), theme["id"]
        assert all(term["weight"] > 0 for term in theme["lexicon"])


def test_m8_labeled_fixture_is_balanced():
    rows = M.load_labeled()
    assert len(rows) == 192
    per_theme = collections.Counter(row["theme"] for row in rows)
    assert set(per_theme) == {t["id"] for t in json.loads((DATA / "themes.json").read_text())}
    assert set(per_theme.values()) == {12}
    for theme in per_theme:
        splits = collections.Counter(r["split"] for r in rows if r["theme"] == theme)
        assert splits == {"dev": 6, "held": 6}, (theme, splits)
    assert len({row["text"] for row in rows}) == 192


def test_data_model_parameter_constraints():
    """DATA_MODEL.md section SchedulerParams: every interval must be reachable."""
    params = M.load_params()
    assert params["I0"] == params["W"]
    assert params["clamp"]["lo"] >= params["W"]
    assert params["clamp"]["lo"] <= params["clamp"]["hi"] <= params["clamp"]["hi_flat"]
    assert set(params["multipliers"]) == {"resonated", "applied", "none", "flat"}
    assert params["archive_flat_streak"] >= params["demote_flat_streak"]


def test_m9_fixture_shape():
    cases = load_cases()
    mutations = [c for c in cases if c["mutation"] != "off_spec"]
    off_spec = [c for c in cases if c["mutation"] == "off_spec"]
    assert len(mutations) == 40
    assert len(off_spec) == 10
    assert len({c["mutation"] for c in mutations}) == 8
    assert len(load_clean()) == 20
    for case in mutations:
        assert case["expected_check"] in {"a", "b", "c", "d", "e"}
        assert apply_case(case) != case["base_prompt"] or case["mutation"] == "empty"


def test_scenarios_stay_inside_the_novelty_budget():
    """EVALS.md section 4: every scenario satisfies capture rate <= rho * k * d."""
    params = M.load_params()
    for name, scenario in load_scenarios().items():
        stream = [row for row in scenario["entries"] if row["captured_day"] > 0]
        rate = len(stream) / scenario["days"]
        density = len(scenario["calendar"]) / scenario["days"]
        budget = params["rho"] * scenario["batch_k"] * density
        assert rate <= budget, f"{name}: capture {rate:.3f}/day exceeds budget {budget:.3f}"
