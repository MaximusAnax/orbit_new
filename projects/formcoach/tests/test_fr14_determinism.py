"""FR-14 — determinism and hermeticity, plus the US-4 "next session" rule.

The engine is only reproducible if it never reads the clock, the filesystem or
the network, so this module audits the engine sources for those calls in
addition to asserting reproducibility end to end.
"""

from __future__ import annotations

import ast
from pathlib import Path

import formcoach.engine as engine_package
import pytest
from formcoach.engine.programming import generate_program, next_session
from formcoach.engine.report import analyze_clip
from formcoach.models import (
    Equipment,
    Experience,
    Goal,
    Muscle,
    ProgramSession,
    UserProfile,
)
from formcoach_synthetic import side_squat_sequence

ENGINE_DIR = Path(engine_package.__file__).parent

#: Calls that would make an engine function depend on something other than its
#: arguments.  ``datetime.fromisoformat`` is fine — parsing a caller-supplied
#: timestamp is not reading the clock.
FORBIDDEN_CALLS = {
    "datetime.now",
    "datetime.utcnow",
    "date.today",
    "time.time",
    "time.monotonic",
    "random.random",
    "random.shuffle",
    "random.choice",
    "random.randint",
    "random.seed",
    "open",
    "urlopen",
}

FORBIDDEN_IMPORTS = {"urllib", "requests", "httpx", "socket", "os", "pathlib", "sqlite3"}


def engine_modules() -> list[Path]:
    return sorted(p for p in ENGINE_DIR.glob("*.py") if p.name != "__init__.py")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_fr14_engine_modules_never_read_the_clock_or_the_world(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in FORBIDDEN_CALLS or name.split(".")[-1] in {"now", "utcnow", "today"}:
                offenders.append(f"{path.name}: {name}()")
    assert offenders == []


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_fr14_engine_modules_import_nothing_that_touches_the_outside(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported & FORBIDDEN_IMPORTS == set()


def test_fr14_engine_never_imports_the_store_or_the_adapters():
    for path in engine_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            if module:
                assert not module.startswith("formcoach.store"), path.name
                assert not module.startswith("formcoach.adapters"), path.name


def test_fr14_random_is_only_used_through_a_seeded_generator():
    source = (ENGINE_DIR / "programming.py").read_text(encoding="utf-8")
    assert "random.Random(seed)" in source
    assert "random.seed(" not in source


def test_fr14_seeded_generation_and_analysis_are_bit_for_bit_reproducible(datasets):
    profile = UserProfile(
        goal=Goal.STRENGTH,
        experience=Experience.ADVANCED,
        days_per_week=6,
        equipment=list(Equipment),
        emphasized_muscles=[Muscle.QUADS],
        disclaimer_acknowledged_at="2026-07-01T08:00:00+00:00",
        updated_at="2026-07-01T08:00:00+00:00",
    )
    a = generate_program(profile, datasets, as_of="2026-07-06", seed=20260731)
    b = generate_program(profile, datasets, as_of="2026-07-06", seed=20260731)
    assert a.model_dump_json() == b.model_dump_json()

    sequence, _ = side_squat_sequence(reps=3, noise_sigma=0.007, seed=5)
    exercise = datasets.exercise("barbell-back-squat")
    form_profile = datasets.form_profiles["squat_v1"]
    first = analyze_clip(sequence, exercise=exercise, profile=form_profile, created_at="t")
    second = analyze_clip(sequence, exercise=exercise, profile=form_profile, created_at="t")
    assert first.model_dump_json() == second.model_dump_json()


# ------------------------------------------------------------------------ US-4


def sessions(count: int, days: int) -> list[ProgramSession]:
    return [
        ProgramSession(
            id=i + 1, program_id=1, week=(i // days) + 1, day_index=i % days, name=f"S{i}"
        )
        for i in range(count)
    ]


def test_us4_next_session_is_the_first_with_no_linked_logs():
    plan = sessions(10, 2)
    assert next_session(plan, []).id == 1
    assert next_session(plan, [1]).id == 2
    assert next_session(plan, [1, 2, 3]).id == 4


def test_us4_a_partially_logged_session_counts_as_done():
    plan = sessions(4, 2)
    # two workouts against the same session still mark it done exactly once
    assert next_session(plan, [1, 1]).id == 2


def test_us4_ordering_is_by_week_then_day_not_by_row_order():
    plan = list(reversed(sessions(6, 2)))
    assert next_session(plan, []).id == 1
    assert next_session(plan, [1, 2]).id == 3


def test_us4_program_complete_returns_nothing():
    plan = sessions(4, 2)
    assert next_session(plan, [1, 2, 3, 4]) is None


def test_us4_freestyle_logs_never_mark_a_session_done():
    """Freestyle workouts carry no session id, so they cannot appear in the set."""
    plan = sessions(4, 2)
    assert next_session(plan, [None]).id == 1  # type: ignore[list-item]
