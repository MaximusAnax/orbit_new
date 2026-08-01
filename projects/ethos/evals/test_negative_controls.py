"""R2 — every exact gate is observed rejecting a committed broken artefact.

Without these, a validator that returns True on empty input, a regex never wired
into its loop, or a metric with a zero denominator is indistinguishable from a
healthy one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ethos.corpus import default_data_dir, load_corpus  # noqa: E402
from ethos.engine.corpus import (  # noqa: E402
    check_c9_fixture_questions,
    check_c18_stemmer,
    check_c20_oos_composition,
    validate_corpus,
)
from ethos.engine.normalize import porter_stem  # noqa: E402

from evals import corpus_gates as gates  # noqa: E402
from evals import metrics as M  # noqa: E402
from evals import negative_controls as NC  # noqa: E402

CORPUS_CONTROLS = json.loads(
    (NC.CONTROLS / "corpus_controls.json").read_text(encoding="utf-8")
)
M3_CONTROLS = json.loads((NC.CONTROLS / "m3_controls.json").read_text(encoding="utf-8"))
FIXTURE_CONTROLS = {
    control["name"]: control
    for control in json.loads(
        (NC.CONTROLS / "fixture_controls.json").read_text(encoding="utf-8")
    )
}


@pytest.fixture(scope="module")
def healthy():
    return load_corpus(default_data_dir())


@pytest.mark.parametrize(
    "control", CORPUS_CONTROLS, ids=[c["name"] for c in CORPUS_CONTROLS]
)
def test_negative_control_corpus_gate_rejects(control: dict) -> None:
    """Each artefact must be rejected by the gate it was authored to violate."""
    broken = NC.broken_corpus(default_data_dir(), control)
    results = validate_corpus(broken, gates.near_unanimous_topics())
    failures = results.get(control["gate"], [])
    assert failures, f"{control['name']} was not rejected by {control['gate']}"


def test_negative_control_healthy_corpus_passes(healthy) -> None:
    """The same predicates accept the committed corpus — the controls are not
    rejecting everything."""
    results = validate_corpus(healthy, gates.near_unanimous_topics())
    assert all(not errors for errors in results.values()), results


@pytest.mark.parametrize("control", M3_CONTROLS, ids=[c["name"] for c in M3_CONTROLS])
def test_negative_control_m3_drops_below_one(control: dict, healthy, tmp_path) -> None:
    """M3 is falsifiable: a corrupted corpus on disk drops it below 1.0 even though
    composer and verifier still agree with each other."""
    broken_dir = NC.broken_data_dir(default_data_dir(), control, tmp_path / control["name"])
    score = M.m3_over_corpus_dir(healthy, broken_dir)
    assert score < 1.0, f"{control['name']} left M3 at {score}"


def test_negative_control_c9_rejects_leaky_oblique_question(healthy) -> None:
    control = FIXTURE_CONTROLS["oblique_question_with_title_word"]
    errors = check_c9_fixture_questions(healthy, control["questions"])
    assert errors, "C9 accepted an oblique question built from its topic's own keywords"


def test_negative_control_c20_rejects_trivially_foreign_oos_set(healthy) -> None:
    control = FIXTURE_CONTROLS["oos_set_all_zero_score"]
    harness = M.RouterHarness(healthy)
    fixtures = {
        "oos_questions": control["questions"],
        "routing_questions": gates.load_fixture("routing_questions.json"),
    }
    oos_s1, direct_s1 = M.c20_scores(harness, fixtures)
    assert check_c20_oos_composition(oos_s1, direct_s1)


def test_negative_control_c18_rejects_wrong_stem() -> None:
    control = FIXTURE_CONTROLS["stemmer_off_by_one"]
    pairs = [(word, expected) for word, expected in control["pairs"]]
    assert check_c18_stemmer(pairs, porter_stem)


def test_negative_control_m4a_rejects_duplicated_marker(healthy) -> None:
    control = FIXTURE_CONTROLS["answer_with_duplicated_marker"]
    result = M.m4_tamper(healthy, [control["case"]])
    assert result["M4a"] == 1.0, result["problems"]


def test_negative_control_m5_drops_when_reading_is_missing(tmp_path) -> None:
    control = FIXTURE_CONTROLS["answer_missing_reading"]
    broken = NC.broken_corpus(default_data_dir(), control)
    score, problems = M.m5_completeness(broken)
    assert score < 1.0, problems
