"""One pytest per gate, at its EVALS threshold; test names reference FR ids.

The whole suite shares a single session-scoped measurement so the ~1,000 routings
and ~600 compositions happen once.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import corpus_gates as gates  # noqa: E402
from evals import run as runner  # noqa: E402


@pytest.fixture(scope="session")
def measured() -> dict:
    return runner.measure()


@pytest.fixture(scope="session")
def values(measured: dict) -> dict:
    return measured["values"]


@pytest.fixture(scope="session")
def gate_results(measured: dict) -> dict:
    return measured["gates"]


# --- M1 (FR-3) --------------------------------------------------------------


def test_gate_m1_direct_fr3(values: dict) -> None:
    assert values["M1-direct"] >= 0.97


def test_gate_m1_colloquial_fr3(values: dict) -> None:
    assert values["M1-coll"] >= 0.85


def test_gate_m1b_oblique_fr3(values: dict) -> None:
    assert values["M1b"] >= 0.70


def test_gate_m1b_holdout_fr3(values: dict) -> None:
    assert values["M1b'"] >= 0.55


def test_gate_m1gap_holdout_fr3(values: dict) -> None:
    """The anti-memorisation instrument: lexicon stuffing raises M1b, not M1b'."""
    assert values["M1gap"] <= 0.15


def test_gate_m1c_recall_at_3_fr3(values: dict) -> None:
    assert values["M1c"] >= 0.95


def test_gate_m1d_dual_home_fr3(values: dict) -> None:
    assert values["M1d"] >= 0.85


# --- M2 (FR-4) --------------------------------------------------------------


def test_gate_m2a_refusal_fr4(values: dict) -> None:
    assert values["M2a"] >= 0.80


def test_gate_m2a_near_refusal_fr4(values: dict) -> None:
    assert values["M2a_near"] >= 0.68


def test_gate_m2b_false_refusal_fr4(values: dict) -> None:
    assert values["M2b"] <= 0.05


# --- M3-M5 (FR-6/7/8/9) -----------------------------------------------------


def test_gate_m3_citation_integrity_fr7(values: dict, measured: dict) -> None:
    assert values["M3"] == pytest.approx(1.0), measured["problems"]["M3"]


def test_gate_m4_tamper_fr8_fr9(values: dict, measured: dict) -> None:
    assert values["M4a"] == pytest.approx(1.0), measured["problems"]["M4"]
    assert values["M4b"] == pytest.approx(0.0), measured["problems"]["M4"]


def test_gate_metric_diagnostics_are_empty_fr8(measured: dict) -> None:
    """A stale clean polish case, a fallback body that differs from the
    deterministic one, or a persisted unverified answer all leave M4a/M4b at
    their passing values while hollowing the metric out. The diagnostics are
    therefore gates, not commentary."""
    assert measured["problems"] == {"M3": [], "M4": [], "M5": []}, measured["problems"]


def test_gate_m4_baselines_are_measured_not_asserted_fr8(measured: dict) -> None:
    """`baseline_no_verifier` and `baseline_reject_all` must come from running
    the same 50 cases through the pipeline with the FR-8 gate swapped out — if
    they were constants the two exact M4 gates would be self-certifying."""
    baselines = measured["baselines"]
    assert baselines["baseline_reject_all:M4b"] == pytest.approx(1.0)
    # FR-9 envelope parse-back is a separate mechanism from FR-8, so disabling
    # the verifier still catches the mutations that break the strict envelope
    # grammar (measured: 5/30). The other 25 are FR-8's alone.
    assert baselines["baseline_no_verifier:M4a"] <= 0.20
    assert baselines["baseline_no_verifier:M4a"] < measured["values"]["M4a"]


def test_gate_m5_completeness_fr5_fr6(values: dict, measured: dict) -> None:
    assert values["M5"] == pytest.approx(1.0), measured["problems"]["M5"]


# --- C-gates ----------------------------------------------------------------


@pytest.mark.parametrize(
    "gate",
    [f"C{n}" for n in range(1, 19) if n != 19] + ["C20"],
)
def test_gate_corpus_c_gates_fr1(gate: str, gate_results: dict) -> None:
    assert gate_results.get(gate) == [], gate_results.get(gate)


def test_gate_c17_safeguard_matrix_fr10(gate_results: dict) -> None:
    assert gate_results["C17"] == [], gate_results["C17"]


def test_gate_c18_normalization_fr2(gate_results: dict) -> None:
    assert gate_results["C18"] == [], gate_results["C18"]


def test_gate_c19_baselines_current_fr1(measured: dict) -> None:
    assert gates.check_c19(measured["freeze"]) == []


def test_gate_c20_out_of_scope_composition_fr4(gate_results: dict) -> None:
    assert gate_results["C20"] == [], gate_results["C20"]


# --- R1: no instrument grades itself ----------------------------------------


def test_independent_checker_isolation() -> None:
    """M3's checker may import nothing but json, pathlib, re and sys — if it shared
    a loader or a model with the verifier it certifies, it would stop being
    independent evidence (EVALS R1)."""
    import ast

    allowed = {"json", "pathlib", "re", "sys"}
    source = (ROOT / "evals" / "independent_check.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import would reach back into the package
                imported.add(f".{node.module or ''}")
            elif node.module:
                imported.add(node.module.split(".")[0])
    assert imported <= allowed | {"__future__"}, sorted(imported - allowed)


# --- D0 (FR-15) -------------------------------------------------------------


def test_gate_d0_determinism_fr15(gate_results: dict) -> None:
    """D0(c) index byte-stability and D0(d) stored-render reproduction."""
    assert gate_results["D0"] == [], gate_results["D0"]


DETERMINISM_SCRIPT = """
import json, sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {src!r})
from ethos.corpus import default_data_dir, load_corpus
from ethos.engine.router import build_index, index_to_json
from evals.metrics import RouterHarness, compose_unverified
from evals.corpus_gates import load_fixture
corpus = load_corpus(default_data_dir())
harness = RouterHarness(corpus)
out = {{"index": index_to_json(harness.index), "routes": [], "answers": []}}
for question in load_fixture("routing_questions.json"):
    routed = harness.route(question["text"])
    out["routes"].append([question["id"], routed.top1, list(routed.top3),
                          round(routed.s1, 9), round(routed.coverage, 9)])
for topic in corpus.topics:
    body, text = compose_unverified(corpus, topic.id, None)
    out["answers"].append([body.model_dump_json(), text])
sys.stdout.write(json.dumps(out, sort_keys=True, ensure_ascii=False))
"""


def _run_determinism(env_extra: dict[str, str]) -> str:
    import os

    env = dict(os.environ)
    env.update(env_extra)
    script = DETERMINISM_SCRIPT.format(root=str(ROOT), src=str(ROOT / "src"))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def test_gate_d0_goldens_regenerate_fr15() -> None:
    """D0(b): the nine goldens regenerate byte-identically from the committed corpus,
    and their embedded composer_version matches the code constant."""
    from evals.fixtures import regenerate_golden

    assert regenerate_golden.main(["--check"]) == 0


def test_gate_d0_hash_seed_and_locale_fr15() -> None:
    """D0(a): identical routing, bodies and renders across hash seeds and locales."""
    base = _run_determinism({"PYTHONHASHSEED": "0", "LC_ALL": "en_US.UTF-8"})
    other_seed = _run_determinism({"PYTHONHASHSEED": "1", "LC_ALL": "en_US.UTF-8"})
    c_locale = _run_determinism({"PYTHONHASHSEED": "0", "LC_ALL": "C"})
    assert base == other_seed
    assert base == c_locale
    payload = json.loads(base)
    assert len(payload["routes"]) == 240
    assert len(payload["answers"]) == 24
