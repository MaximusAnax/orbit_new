"""Pytest enforcement of every gate threshold in ``docs/EVALS.md``.

One test per gate, plus the two structural tests EVALS.md names
(``test_baselines_are_beaten_by_gates`` and
``test_handcheck_features_match_engine``) and the fixture-integrity tests that
keep the corpus honest: the generator may not import the engine, regeneration
from the committed seed must reproduce the committed values, and the corpus
composition (58 clips, 8 invalid, 6 photos, >= 10 positives per fault class) is
pinned.

The whole scorecard is computed once per session — it runs the analysis
pipeline over 64 pose fixtures and generates 120 programs — and every gate test
reads from that one run.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from evals import metrics as M

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GENERATOR = FIXTURES / "generate_poses.py"


@pytest.fixture(scope="session")
def scorecard():
    results, extras = M.build_scorecard()
    return {r.key: r for r in results}, extras


def _gate(scorecard, key):
    result = scorecard[0][key]
    assert result.passing, (
        f"{key} {result.name}: {result.value:.4f} vs gate {result.gate_text()}"
        + ("\n  " + "\n  ".join(result.notes) if result.notes else "")
    )
    return result


# ------------------------------------------------------------------ capability 1


def test_gate_m1_fault_macro_f1_fr9(scorecard):
    assert _gate(scorecard, "M1").value >= 0.80


def test_gate_m1b_worst_class_f1_fr9(scorecard):
    assert _gate(scorecard, "M1b").value >= 0.55


def test_gate_m2_rep_count_accuracy_fr8(scorecard):
    assert _gate(scorecard, "M2").value >= 0.90


def test_gate_m3a_angle_error_fr10(scorecard):
    assert _gate(scorecard, "M3a").value <= 5.0


def test_gate_m3b_ratio_error_fr10(scorecard):
    assert _gate(scorecard, "M3b").value <= 0.03


def test_gate_m4_screening_and_view_fr7(scorecard):
    assert _gate(scorecard, "M4").value == pytest.approx(1.0)


def test_gate_m4b_never_guess_fr9(scorecard):
    assert _gate(scorecard, "M4b").value == pytest.approx(1.0)


def test_gate_m7_refusal_rate_fr9(scorecard):
    assert _gate(scorecard, "M7").value <= 0.02


# ------------------------------------------------------------------ capability 2


def test_gate_m5_program_constraints_fr3(scorecard):
    assert _gate(scorecard, "M5").value == pytest.approx(1.0)


def test_gate_m6_progression_accuracy_fr6(scorecard):
    assert _gate(scorecard, "M6").value == pytest.approx(1.0)


# ------------------------------------------------------------------ structural


def test_baselines_are_beaten_by_gates(scorecard):
    """Every gate must be strictly harder than its naive baseline scores."""
    unbeaten = [
        f"{r.key}: gate {r.gate_text()} does not beat baseline {r.baseline:.4f} "
        f"({r.baseline_name})"
        for r in scorecard[0].values()
        if not r.baseline_beaten
    ]
    assert unbeaten == []


def test_handcheck_features_match_engine():
    """The definitional cross-check: hand-computed values vs the engine's.

    ``labels_handcheck.json`` holds feature values recomputed from the committed
    keypoints by the generator's own geometry, written independently of
    ``engine/geometry.py``.  A shared sign or normalization error fails here even
    when generator and engine agree with one another.
    """
    rows = M.handcheck_engine_values()
    assert len(rows) == 8
    for row, measured in rows:
        for feature, expected in row["features"].items():
            actual = measured.get(feature)
            assert actual is not None, f"{row['clip_id']} rep {row['rep_index']}: {feature} missing"
            tolerance = 5.0 if feature in M.ANGLE_FEATURES else 0.03
            assert abs(actual - expected) <= tolerance, (
                f"{row['clip_id']} rep {row['rep_index']} {feature}: "
                f"hand {expected} vs engine {actual}"
            )


def test_generator_does_not_import_the_engine():
    """EVALS.md's generator-independence rule, asserted by scanning imports."""
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    offenders = [m for m in modules if m.split(".")[0] in {"formcoach", "evals"}]
    assert offenders == [], f"generator imports the system under test: {offenders}"


def _load_generator():
    spec = importlib.util.spec_from_file_location("formcoach_pose_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before executing: the module defines dataclasses, whose field
    # resolution looks itself up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield path, value


def test_regeneration_reproduces_the_committed_fixtures(tmp_path):
    """CI re-runs the generator and diffs parsed values with tolerance 1e-9."""
    generator = _load_generator()
    generator.generate(generator.DEFAULT_SEED, tmp_path)
    committed = json.loads((FIXTURES / "labels.json").read_text(encoding="utf-8"))
    regenerated = json.loads((tmp_path / "labels.json").read_text(encoding="utf-8"))
    for (path_a, a), (path_b, b) in zip(
        _walk(committed), _walk(regenerated), strict=True
    ):
        assert path_a == path_b
        if isinstance(a, float) and isinstance(b, float):
            assert abs(a - b) <= 1e-9, path_a
        else:
            assert a == b, path_a
    for clip in committed["clips"][:6]:
        name = Path(clip["file"]).name
        old = json.loads((FIXTURES / "poses" / name).read_text(encoding="utf-8"))
        new = json.loads((tmp_path / "poses" / name).read_text(encoding="utf-8"))
        for (_, a), (_, b) in zip(_walk(old), _walk(new), strict=True):
            if isinstance(a, float) and isinstance(b, float):
                assert abs(a - b) <= 1e-9
            else:
                assert a == b


def test_fixture_corpus_composition_matches_evals_md():
    labels = M.load_labels()
    clips = [c for c in labels["clips"] if c["kind"] == "clip"]
    photos = [c for c in labels["clips"] if c["kind"] == "photo"]
    assert len(clips) == 58
    assert sum(1 for c in clips if c["valid"]) == 50
    assert sum(1 for c in clips if not c["valid"]) == 8
    assert len(photos) == 6
    assert sum(1 for c in clips if c["declared_view"] is None) == 4
    assert {c["fps"] for c in clips} == {24.0, 30.0, 60.0}
    assert all(c["reject_reason"] == "insufficient_visibility" for c in clips if not c["valid"])


def test_every_fault_class_has_at_least_ten_positive_instances():
    labels = M.load_labels()
    positives: dict[tuple[str, str], int] = {}
    for clip in labels["clips"]:
        if not clip["valid"]:
            continue
        for rep in clip["reps"]:
            for fault_id, info in rep["rules"].items():
                if info["assessable"] and info["label"] == "fault":
                    key = (clip["exercise_id"], fault_id)
                    positives[key] = positives.get(key, 0) + 1
    assert len(positives) == 10
    thin = {k: v for k, v in positives.items() if v < 10}
    assert thin == {}, f"fault classes with fewer than 10 positives: {thin}"


def test_labels_never_come_from_the_engine():
    """Applicability and truth are generator-side facts, not engine output."""
    labels = M.load_labels()
    assert labels["seed"] == 20260731
    for clip in labels["clips"]:
        if not clip["valid"]:
            assert clip["rep_count"] is None
            assert clip["reps"] == []
            continue
        for rep in clip["reps"]:
            for info in rep["rules"].values():
                if info["assessable"]:
                    assert info["label"] in {"ok", "fault"}
                    assert isinstance(info["true_value"], float | int)
                else:
                    assert info["reason"] in {
                        "view_mismatch",
                        "keypoints_not_visible",
                        "phase_not_shown",
                        "needs_multi_frame",
                    }
