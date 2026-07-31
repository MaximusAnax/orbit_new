"""FR-12 — the Typer CLI, exercised through ``CliRunner``.

Every invocation passes an explicit ``--db`` and an explicit timestamp, so the
tests are hermetic and the clock is never consulted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from formcoach.cli.app import app, parse_sets
from typer.testing import CliRunner

POSES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "poses"
ACK = "2026-07-01T08:00:00+00:00"

runner = CliRunner()


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "formcoach.db")


def run(db: str, *args: str):
    return runner.invoke(app, ["--db", db, *args])


@pytest.fixture
def initialized(db):
    assert run(db, "init").exit_code == 0
    return db


@pytest.fixture
def configured(initialized):
    assert (
        run(
            initialized,
            "profile",
            "set",
            "--goal",
            "hypertrophy",
            "--experience",
            "intermediate",
            "--days",
            "4",
            "-e",
            "barbell",
            "-e",
            "dumbbell",
            "-e",
            "cable",
            "-e",
            "machine",
            "-e",
            "bodyweight",
            "-m",
            "chest",
            "--at",
            ACK,
        ).exit_code
        == 0
    )
    return initialized


@pytest.fixture
def acknowledged(configured):
    assert run(configured, "profile", "ack-disclaimer", "--at", ACK).exit_code == 0
    return configured


@pytest.fixture
def programmed(acknowledged):
    result = run(acknowledged, "program", "new", "--seed", "20260731", "--as-of", "2026-07-06")
    assert result.exit_code == 0, result.output
    return acknowledged


# ---------------------------------------------------------------------- basics


def test_fr12_help_lists_every_scoped_command_group():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "profile", "exercises", "program", "log", "volume", "form"):
        assert command in result.output


@pytest.mark.parametrize("group", ["profile", "exercises", "program", "form"])
def test_fr12_subcommand_help_is_useful(group):
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0
    assert result.output.strip()


def test_fr2_init_loads_the_committed_library(db):
    result = run(db, "init")
    assert result.exit_code == 0
    assert "68 exercises" in result.output
    assert Path(db).is_file()


# --------------------------------------------------------------------- profile


def test_fr1_profile_show_before_set_exits_non_zero(initialized):
    result = run(initialized, "profile", "show")
    assert result.exit_code == 1
    assert "profile_not_found" in result.output


def test_fr1_profile_set_then_show(configured):
    result = run(configured, "profile", "show")
    assert result.exit_code == 0
    assert "hypertrophy" in result.output
    assert "NOT ACKNOWLEDGED" in result.output


def test_fr13a_ack_disclaimer_records_the_timestamp(acknowledged):
    result = run(acknowledged, "profile", "show")
    assert ACK in result.output
    assert "NOT ACKNOWLEDGED" not in result.output


# --------------------------------------------------------------------- library


def test_fr2_exercises_list_filters(initialized):
    everything = run(initialized, "exercises", "list")
    analyzable = run(initialized, "exercises", "list", "--analyzable")
    assert everything.exit_code == analyzable.exit_code == 0
    assert "3 exercise(s)" in analyzable.output
    assert "barbell-back-squat" in analyzable.output


def test_fr2_exercises_show_prints_cues_and_media(initialized):
    result = run(initialized, "exercises", "show", "barbell-back-squat")
    assert result.exit_code == 0
    assert "steps:" in result.output
    assert "cues:" in result.output
    assert "media:" in result.output


def test_fr12_unknown_exercise_exits_non_zero(initialized):
    result = run(initialized, "exercises", "show", "not-a-lift")
    assert result.exit_code == 1
    assert "exercise_not_found" in result.output


# -------------------------------------------------------------------- programs


def test_fr13a_program_new_is_blocked_without_acknowledgement(configured):
    result = run(configured, "program", "new", "--as-of", "2026-07-06")
    assert result.exit_code == 1
    assert "disclaimer_not_acknowledged" in result.output


def test_fr3_program_new_prints_the_target_plan(acknowledged):
    result = run(acknowledged, "program", "new", "--seed", "7", "--as-of", "2026-07-06")
    assert result.exit_code == 0
    assert "upper_lower" in result.output
    assert "targets:" in result.output
    assert "sessions: 20" in result.output


def test_fr14_same_seed_prints_the_same_program(acknowledged):
    first = run(acknowledged, "program", "new", "--seed", "11", "--as-of", "2026-07-06")
    second = run(acknowledged, "program", "new", "--seed", "11", "--as-of", "2026-07-06")
    assert first.exit_code == second.exit_code == 0
    # The program id differs (two rows were written); everything the generator
    # decided must not.
    strip = lambda text: [  # noqa: E731
        line for line in text.splitlines() if not line.startswith(("program ", "sessions:"))
    ]
    assert strip(first.output) == strip(second.output)


def test_fr12_program_show_defaults_to_the_active_program(programmed):
    result = run(programmed, "program", "show", "--week", "1")
    assert result.exit_code == 0
    assert "day 0:" in result.output
    assert "RIR 3" in result.output


def test_us4_program_next_prints_actions_and_clauses(programmed):
    result = run(programmed, "program", "next")
    assert result.exit_code == 0
    assert "week 1, day 0" in result.output
    assert "hold/L6" in result.output


def test_fr12_program_next_without_a_program_exits_non_zero(acknowledged):
    result = run(acknowledged, "program", "next")
    assert result.exit_code == 1
    assert "no_active_program" in result.output


# ------------------------------------------------------------------------ logs


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("100x8@2", [(100.0, 8, 2.0)]),
        ("100x8@2,100x8@2,100x7@1", [(100.0, 8, 2.0), (100.0, 8, 2.0), (100.0, 7, 1.0)]),
        ("bwx12,bwx10", [(0.0, 12, None), (0.0, 10, None)]),
        ("60.5x5", [(60.5, 5, None)]),
    ],
)
def test_fr5_set_spec_parsing(spec, expected):
    assert parse_sets(spec) == expected


@pytest.mark.parametrize("spec", ["", "abc", "100@8"])
def test_fr5_bad_set_spec_is_rejected(spec):
    with pytest.raises(ValueError):
        parse_sets(spec)


def test_fr5_log_prints_derived_e1rm(configured):
    result = run(
        configured,
        "log",
        "--exercise",
        "barbell-bench-press",
        "--sets",
        "80x10@2,80x10",
        "--at",
        "2026-07-06T18:00:00+00:00",
    )
    assert result.exit_code == 0
    assert "e1RM 112.0 kg" in result.output
    assert "(unrated)" in result.output


def test_fr12_log_with_a_malformed_spec_exits_two(configured):
    result = run(configured, "log", "--exercise", "push-up", "--sets", "nonsense", "--at", ACK)
    assert result.exit_code == 2
    assert "invalid_sets" in result.output


def test_fr12_log_with_an_unknown_exercise_exits_one(configured):
    result = run(configured, "log", "--exercise", "made-up", "--sets", "10x5", "--at", ACK)
    assert result.exit_code == 1
    assert "invalid_reference" in result.output


def test_fr13b_pain_flag_prints_stop_and_refer_then_clears(configured):
    logged = run(
        configured,
        "log",
        "--exercise",
        "barbell-back-squat",
        "--sets",
        "100x5@1",
        "--pain",
        "--at",
        "2026-07-06T18:00:00+00:00",
    )
    assert logged.exit_code == 0
    assert "pain flagged: barbell-back-squat" in logged.output
    assert "qualified health professional" in logged.output
    assert "barbell-back-squat" in run(configured, "profile", "show").output

    cleared = run(
        configured,
        "profile",
        "clear-pain",
        "barbell-back-squat",
        "--at",
        "2026-07-07T08:00:00+00:00",
    )
    assert cleared.exit_code == 0
    assert "remaining: -" in cleared.output


# ---------------------------------------------------------------------- volume


def test_fr4_volume_reports_every_muscle(configured):
    run(
        configured,
        "log",
        "--exercise",
        "barbell-back-squat",
        "--sets",
        "100x5@2,100x5@2",
        "--at",
        "2026-07-06T18:00:00+00:00",
    )
    result = run(configured, "volume", "--iso-week", "2026-W28")
    assert result.exit_code == 0
    assert "week 2026-W28" in result.output
    assert result.output.count("(MEV ") == 15
    assert "quads           2.0" in result.output


# ------------------------------------------------------------------------ form


def test_fr10_form_analyze_reports_faults_and_corrections(initialized):
    result = run(
        initialized,
        "form",
        "analyze",
        str(POSES / "squat_side_02.keypoints.json"),
        "--exercise",
        "barbell-back-squat",
        "--view",
        "side_left",
        "--as-of",
        "2026-07-06T19:00:00+00:00",
    )
    assert result.exit_code == 0
    assert "insufficient_depth" in result.output
    assert "corrections, most important first:" in result.output
    assert "hip crease" in result.output


def test_us7_rejected_clip_says_why(initialized):
    result = run(
        initialized,
        "form",
        "analyze",
        str(POSES / "invalid_03.keypoints.json"),
        "--exercise",
        "barbell-deadlift",
        "--view",
        "side_right",
        "--as-of",
        "2026-07-06T19:00:00+00:00",
    )
    assert result.exit_code == 0
    assert "REJECTED (insufficient_visibility)" in result.output


def test_fr15_form_photo_gates_on_the_declared_phase(initialized):
    result = run(
        initialized,
        "form",
        "photo",
        str(POSES / "photo_squat_side_01.keypoints.json"),
        "--exercise",
        "barbell-back-squat",
        "--phase",
        "bottom",
        "--as-of",
        "2026-07-06T19:05:00+00:00",
    )
    assert result.exit_code == 0
    assert "photo" in result.output
    assert "reps 1" in result.output
    assert "view_mismatch" in result.output


def test_fr12_form_show_and_list_round_trip(initialized):
    run(
        initialized,
        "form",
        "analyze",
        str(POSES / "pushup_side_02.keypoints.json"),
        "--exercise",
        "push-up",
        "--view",
        "side_right",
        "--as-of",
        "2026-07-06T19:00:00+00:00",
    )
    listed = run(initialized, "form", "list")
    assert listed.exit_code == 0
    assert "1 analysis record(s)" in listed.output
    shown = run(initialized, "form", "show", "1")
    assert shown.exit_code == 0
    assert "push-up" in shown.output


def test_fr12_form_show_missing_analysis_exits_non_zero(initialized):
    result = run(initialized, "form", "show", "42")
    assert result.exit_code == 1
    assert "analysis_not_found" in result.output


def test_fr12_json_output_is_machine_readable(initialized):
    import json

    result = run(initialized, "exercises", "list", "--analyzable", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert {e["id"] for e in payload} == {
        "barbell-back-squat",
        "barbell-deadlift",
        "push-up",
    }


def test_fr13c_cli_help_and_error_text_never_use_diagnosis_language(initialized):
    """FR-13(c) extended to the surface: the CLI speaks of faults, never bodies."""
    from formcoach.engine.safety import contains_diagnosis_language
    from formcoach.services import ERROR_STATUS

    texts = [runner.invoke(app, ["--help"]).output]
    for group in ("profile", "exercises", "program", "form"):
        texts.append(runner.invoke(app, [group, "--help"]).output)
    texts.append(run(initialized, "profile", "show").output)
    texts.append(run(initialized, "exercises", "show", "not-a-lift").output)
    texts.extend(ERROR_STATUS)
    offenders = [text for text in texts if contains_diagnosis_language(text)]
    assert offenders == []
