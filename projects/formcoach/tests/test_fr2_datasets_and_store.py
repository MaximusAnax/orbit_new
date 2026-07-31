"""FR-2 and FR-5 — committed datasets, their integrity checks, and persistence.

Repository tests run against both backends through the ``repo`` fixture, so the
SQLite schema and the in-memory implementation cannot drift apart.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from formcoach.adapters.media_local import LocalMediaResolver
from formcoach.engine.programming import SETS_PER_SESSION_BUDGET, generate_program
from formcoach.models import (
    AnalysisKind,
    AnalysisResult,
    AnalysisStatus,
    Equipment,
    Experience,
    FaultFinding,
    FindingStatus,
    FormAnalysis,
    Goal,
    MediaSource,
    MovementPattern,
    Muscle,
    NotAssessedReason,
    PoseSource,
    ProgramStatus,
    RepAnalysis,
    SetLog,
    Severity,
    UserProfile,
    View,
    WorkoutLog,
)
from formcoach.store.datasets import (
    DatasetError,
    default_data_dir,
    load_datasets,
    validate_datasets,
)
from formcoach.store.repository import RepositoryError

ACK = "2026-07-01T08:00:00+00:00"


# ------------------------------------------------------------------ datasets


def test_fr2_library_is_loaded_and_looks_like_a_gym(datasets):
    assert len(datasets.exercises) >= 55
    patterns = {e.pattern for e in datasets.exercises}
    assert patterns >= {
        MovementPattern.SQUAT,
        MovementPattern.HINGE,
        MovementPattern.HORIZONTAL_PUSH,
        MovementPattern.HORIZONTAL_PULL,
        MovementPattern.VERTICAL_PUSH,
        MovementPattern.VERTICAL_PULL,
        MovementPattern.LUNGE,
        MovementPattern.ISOLATION,
        MovementPattern.CORE,
        MovementPattern.CARRY,
    }
    primaries = {m for e in datasets.exercises for m in e.primary_muscles}
    assert primaries == set(Muscle)


def test_fr2_committed_datasets_pass_every_integrity_check(datasets):
    assert validate_datasets(datasets, default_data_dir()) == []


def test_fr2_every_exercise_has_at_least_one_media_asset(datasets):
    for exercise in datasets.exercises:
        assert datasets.assets_for(exercise.id)


def test_fr2_local_assets_are_verified_by_file_existence(datasets):
    resolver = LocalMediaResolver(datasets.media_assets, default_data_dir())
    local = [a for a in datasets.media_assets if a.source is MediaSource.LOCAL]
    assert local
    for asset in local:
        path = resolver.local_path(asset)
        assert path is not None and path.is_file()
        assert resolver.verify(asset)


def test_fr2_url_assets_are_verified_by_manifest_schema_only(datasets):
    resolver = LocalMediaResolver(datasets.media_assets, default_data_dir())
    urls = [a for a in datasets.media_assets if a.source is MediaSource.URL]
    assert urls, "at least one url asset should exercise the schema-only path"
    for asset in urls:
        assert asset.ref.startswith("https://")
        assert asset.license.strip()
        assert resolver.verify(asset)
        assert resolver.local_path(asset) is None


def test_fr2_missing_local_file_fails_the_integrity_check(datasets, tmp_path):
    problems = validate_datasets(datasets, tmp_path)
    assert any("file is missing" in p for p in problems)


def test_fr2_harder_variants_reference_a_real_exercise_of_the_same_pattern(datasets):
    chains = [e for e in datasets.exercises if e.harder_variant_id]
    assert chains, "the bodyweight progression chain should exist for FR-6 clause B3"
    for exercise in chains:
        variant = datasets.exercise(exercise.harder_variant_id or "")
        assert variant.pattern is exercise.pattern


def test_fr2_broken_variant_reference_is_reported(datasets):
    broken = datasets.exercises[0].model_copy(update={"harder_variant_id": "nope"})
    mutated = datasets.model_copy(update={"exercises": [broken, *datasets.exercises[1:]]})
    problems = validate_datasets(mutated, default_data_dir())
    assert any("harder_variant_id nope does not exist" in p for p in problems)


def test_fr3_feasibility_invariant_holds_for_every_combination(datasets):
    problems = [p for p in validate_datasets(datasets, default_data_dir()) if "feasibility" in p]
    assert problems == []


def test_fr3_feasibility_invariant_fails_loudly_when_landmarks_are_edited(datasets):
    inflated = {
        muscle: landmark.model_copy(update={"mev": 20, "mav": 24, "mrv": 30})
        for muscle, landmark in datasets.landmarks.items()
    }
    mutated = datasets.model_copy(update={"landmarks": inflated})
    problems = validate_datasets(mutated, default_data_dir())
    assert any("feasibility" in p for p in problems)


def test_fr2_form_profile_features_must_exist_in_geometry(datasets):
    profile = datasets.form_profiles["squat_v1"]
    broken_rule = profile.rules[0].model_copy(update={"feature": "vibes"})
    broken = profile.model_copy(update={"rules": [broken_rule, *profile.rules[1:]]})
    mutated = datasets.model_copy(
        update={"form_profiles": {**datasets.form_profiles, "squat_v1": broken}}
    )
    problems = validate_datasets(mutated, default_data_dir())
    assert any("not implemented in geometry.py" in p for p in problems)


def test_fr2_form_profile_keypoints_must_be_real(datasets):
    profile = datasets.form_profiles["squat_v1"]
    broken_rule = profile.rules[0].model_copy(update={"feature_keypoints": ["left_tail"]})
    broken = profile.model_copy(update={"rules": [broken_rule, *profile.rules[1:]]})
    mutated = datasets.model_copy(
        update={"form_profiles": {**datasets.form_profiles, "squat_v1": broken}}
    )
    assert any(
        "unknown keypoint left_tail" in p for p in validate_datasets(mutated, default_data_dir())
    )


def test_fr2_analyzable_exercises_and_profiles_agree(datasets):
    analyzable = {e.id for e in datasets.exercises if e.is_analyzable}
    assert analyzable == {"barbell-back-squat", "barbell-deadlift", "push-up"}
    for exercise_id in analyzable:
        exercise = datasets.exercise(exercise_id)
        profile = datasets.form_profiles[exercise.form_profile_id or ""]
        assert profile.exercise_id == exercise_id


def test_fr2_split_templates_cover_every_supported_day_count(datasets):
    covered = {d for t in datasets.split_templates for d in t.days}
    assert covered == {2, 3, 4, 5, 6}
    for template in datasets.split_templates:
        assert len(template.sessions) >= max(template.days)


def test_fr2_load_increments_match_the_committed_table(datasets):
    expected = {
        Equipment.BARBELL: (2.5, 5.0),
        Equipment.DUMBBELL: (2.0, 5.0),
        Equipment.MACHINE: (5.0, 10.0),
        Equipment.CABLE: (2.5, 5.0),
        Equipment.KETTLEBELL: (4.0, 10.0),
        Equipment.BAND: (None, None),
        Equipment.BODYWEIGHT: (None, None),
    }
    for equipment, (kg, lb) in expected.items():
        pair = datasets.load_increments[equipment]
        assert (pair.kg, pair.lb) == (kg, lb)


def test_fr2_missing_dataset_file_is_a_clear_error(tmp_path):
    with pytest.raises(DatasetError) as excinfo:
        load_datasets(tmp_path)
    assert "missing dataset file" in str(excinfo.value)


def test_fr2_malformed_json_is_a_clear_error(tmp_path):
    (tmp_path / "exercises.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(DatasetError) as excinfo:
        load_datasets(tmp_path)
    assert "not valid JSON" in str(excinfo.value)


def test_fr2_exercise_lookup_by_id_alias_and_name(datasets):
    assert datasets.find_exercise("barbell-back-squat").id == "barbell-back-squat"
    assert datasets.find_exercise("squat").id == "barbell-back-squat"
    assert datasets.find_exercise("Deadlift").id == "barbell-deadlift"
    assert datasets.find_exercise("Push-Up").id == "push-up"
    assert datasets.find_exercise("nonsense") is None


def test_fr14_dataset_loading_is_hermetic_and_repeatable(tmp_path):
    first = load_datasets()
    second = load_datasets()
    assert first == second


# ---------------------------------------------------------------- repository


def test_fr2_library_query_filters_combine(repo):
    barbell_pushes = repo.list_exercises(
        equipment=Equipment.BARBELL, pattern=MovementPattern.HORIZONTAL_PUSH
    )
    assert barbell_pushes
    for exercise in barbell_pushes:
        assert Equipment.BARBELL in exercise.equipment
        assert exercise.pattern is MovementPattern.HORIZONTAL_PUSH

    chest = repo.list_exercises(muscle=Muscle.CHEST)
    assert all(Muscle.CHEST in e.primary_muscles for e in chest)

    analyzable = repo.list_exercises(analyzable=True)
    assert {e.id for e in analyzable} == {"barbell-back-squat", "barbell-deadlift", "push-up"}
    assert repo.list_exercises(analyzable=False)


def test_fr2_media_and_landmarks_round_trip(repo, datasets):
    assets = repo.list_media("barbell-back-squat")
    assert assets == datasets.assets_for("barbell-back-squat")
    assert repo.get_landmark(Muscle.CHEST) == datasets.landmarks[Muscle.CHEST]
    assert repo.get_exercise("not-real") is None


def test_fr1_profile_round_trips_and_stays_a_singleton(repo):
    assert repo.get_profile() is None
    profile = UserProfile(
        goal=Goal.STRENGTH,
        experience=Experience.ADVANCED,
        days_per_week=5,
        equipment=[Equipment.BARBELL, Equipment.MACHINE],
        emphasized_muscles=[Muscle.QUADS],
        disclaimer_acknowledged_at=ACK,
        pain_flags=["dip"],
        updated_at=ACK,
    )
    repo.save_profile(profile)
    stored = repo.get_profile()
    assert stored == profile
    updated = profile.model_copy(update={"days_per_week": 3, "pain_flags": []})
    repo.save_profile(updated)
    assert repo.get_profile() == updated


def test_fr3_program_round_trips_with_sessions_and_prescriptions(repo, datasets):
    profile = UserProfile(
        goal=Goal.HYPERTROPHY,
        experience=Experience.INTERMEDIATE,
        days_per_week=3,
        equipment=list(Equipment),
        disclaimer_acknowledged_at=ACK,
        updated_at=ACK,
    )
    plan = generate_program(profile, datasets, as_of="2026-07-06", seed=5)
    stored = repo.save_program(plan)
    assert stored.program.id is not None

    fetched = repo.get_program(stored.program.id)
    assert fetched == stored.program
    assert fetched.weekly_set_targets == plan.program.weekly_set_targets
    assert fetched.target_muscles == plan.program.target_muscles

    sessions = repo.list_sessions(stored.program.id)
    assert len(sessions) == 15
    first = repo.get_session(stored.program.id, 1, 0)
    assert first is not None and first.name == plan.sessions[0].name
    prescriptions = repo.list_prescriptions(first.id or 0)
    assert [p.position for p in prescriptions] == list(range(len(prescriptions)))
    assert prescriptions[0].exercise_id == plan.sessions[0].prescriptions[0].exercise_id


def test_fr3_only_one_program_is_active_at_a_time(repo, datasets):
    profile = UserProfile(
        goal=Goal.GENERAL,
        experience=Experience.BEGINNER,
        days_per_week=2,
        equipment=list(Equipment),
        disclaimer_acknowledged_at=ACK,
        updated_at=ACK,
    )
    first = repo.save_program(generate_program(profile, datasets, as_of="2026-07-06", seed=1))
    second = repo.save_program(generate_program(profile, datasets, as_of="2026-08-10", seed=2))
    active = [p for p in repo.list_programs() if p.status is ProgramStatus.ACTIVE]
    assert [p.id for p in active] == [second.program.id]
    assert repo.active_program().id == second.program.id

    repo.set_program_status(first.program.id or 0, ProgramStatus.ACTIVE)
    assert repo.active_program().id == first.program.id
    assert len([p for p in repo.list_programs() if p.status is ProgramStatus.ACTIVE]) == 1


def test_fr5_workouts_and_sets_round_trip_with_derived_e1rm(repo):
    workout = WorkoutLog(performed_at="2026-07-13T18:00:00+00:00", notes="felt good")
    sets = [
        SetLog(exercise_id="barbell-bench-press", set_index=0, weight_kg=80.0, reps=8, rir=2.0),
        SetLog(exercise_id="barbell-bench-press", set_index=1, weight_kg=80.0, reps=7, rir=None),
        SetLog(exercise_id="push-up", set_index=0, weight_kg=0.0, reps=20, rir=1.0),
    ]
    stored = repo.add_workout(workout, sets)
    assert stored.workout.id is not None
    assert all(s.workout_id == stored.workout.id for s in stored.sets)

    listed = repo.list_workouts()
    assert len(listed) == 1
    fetched = listed[0].sets
    by_index = {(s.exercise_id, s.set_index): s for s in fetched}
    assert by_index[("barbell-bench-press", 0)].e1rm_kg == pytest.approx(80 * (1 + 10 / 30))
    assert by_index[("barbell-bench-press", 1)].e1rm_kg is None
    assert by_index[("push-up", 0)].e1rm_kg is None


def test_fr5_workouts_can_be_freestyle_or_linked_to_a_session(repo, datasets):
    profile = UserProfile(
        goal=Goal.GENERAL,
        experience=Experience.BEGINNER,
        days_per_week=2,
        equipment=list(Equipment),
        disclaimer_acknowledged_at=ACK,
        updated_at=ACK,
    )
    plan = repo.save_program(generate_program(profile, datasets, as_of="2026-07-06", seed=1))
    session = repo.get_session(plan.program.id or 0, 1, 0)
    assert session is not None

    repo.add_workout(
        WorkoutLog(performed_at="2026-07-13T18:00:00+00:00", program_session_id=session.id),
        [SetLog(exercise_id="barbell-bench-press", set_index=0, weight_kg=80.0, reps=8, rir=2.0)],
    )
    repo.add_workout(
        WorkoutLog(performed_at="2026-07-14T18:00:00+00:00"),
        [SetLog(exercise_id="barbell-bench-press", set_index=0, weight_kg=80.0, reps=8, rir=2.0)],
    )
    linked = repo.workouts_for_session(session.id or 0)
    assert len(linked) == 1
    assert len(repo.list_workouts()) == 2


def test_fr5_workouts_can_be_filtered_by_date(repo):
    for day in ("10", "13", "16"):
        repo.add_workout(
            WorkoutLog(performed_at=f"2026-07-{day}T18:00:00+00:00"),
            [SetLog(exercise_id="push-up", set_index=0, weight_kg=0.0, reps=10)],
        )
    recent = repo.list_workouts(since="2026-07-13T00:00:00+00:00")
    assert [s.workout.performed_at[8:10] for s in recent] == ["13", "16"]


def test_fr5_an_empty_workout_is_refused(repo):
    with pytest.raises(RepositoryError):
        repo.add_workout(WorkoutLog(performed_at="2026-07-13T18:00:00+00:00"), [])


def test_fr5_set_log_is_append_only_in_sqlite(sqlite_repo):
    """The append-only rule is enforced by the schema, not just by the API."""
    sqlite_repo.add_workout(
        WorkoutLog(performed_at="2026-07-13T18:00:00+00:00"),
        [SetLog(exercise_id="push-up", set_index=0, weight_kg=0.0, reps=10)],
    )
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_repo.connection.execute("UPDATE set_log SET reps = 99")
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_repo.connection.execute("DELETE FROM set_log")
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_repo.connection.execute("DELETE FROM workout_log")


def test_fr5_duplicate_set_index_is_rejected_by_sqlite(sqlite_repo):
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_repo.add_workout(
            WorkoutLog(performed_at="2026-07-13T18:00:00+00:00"),
            [
                SetLog(exercise_id="push-up", set_index=0, weight_kg=0.0, reps=10),
                SetLog(exercise_id="push-up", set_index=0, weight_kg=0.0, reps=10),
            ],
        )


def test_fr5_sqlite_stores_weight_in_kilograms(sqlite_repo):
    sqlite_repo.add_workout(
        WorkoutLog(performed_at="2026-07-13T18:00:00+00:00"),
        [SetLog(exercise_id="barbell-bench-press", set_index=0, weight_kg=100.0, reps=5, rir=1.0)],
    )
    row = sqlite_repo.connection.execute("SELECT weight_kg, e1rm_kg FROM set_log").fetchone()
    assert row["weight_kg"] == 100.0
    assert row["e1rm_kg"] == pytest.approx(100 * (1 + 6 / 30))


def _analysis_result() -> AnalysisResult:
    findings = [
        FaultFinding(
            fault_id="insufficient_depth",
            status=FindingStatus.FAULT,
            measured=0.061,
            threshold=0.03,
            severity=Severity.MAJOR,
            frame=84,
            cue="sit lower",
        ),
        FaultFinding(
            fault_id="knee_valgus",
            status=FindingStatus.NOT_ASSESSED,
            not_assessed_reason=NotAssessedReason.VIEW_MISMATCH,
            threshold=12.0,
            severity=Severity.MAJOR,
        ),
    ]
    rep = RepAnalysis(
        rep_index=0,
        start_frame=10,
        extremum_frame=84,
        end_frame=140,
        metrics={"depth_ratio@bottom": 0.061},
        score=75.0,
        findings=findings,
    )
    analysis = FormAnalysis(
        created_at="2026-07-15T18:30:00+00:00",
        analysis_kind=AnalysisKind.CLIP,
        exercise_id="barbell-back-squat",
        form_profile_id="squat_v1",
        source_ref="squat_01.keypoints.json",
        pose_source=PoseSource.FIXTURE,
        view=View.SIDE_LEFT,
        view_inferred=True,
        fps=30.0,
        frames_total=150,
        frames_valid=150,
        status=AnalysisStatus.COMPLETED,
        rep_count=1,
        clip_score=75.0,
    )
    return AnalysisResult(analysis=analysis, reps=[rep])


def test_fr10_analysis_round_trips_with_reps_and_the_full_finding_matrix(repo):
    stored = repo.save_analysis(_analysis_result())
    assert stored.analysis.id is not None
    assert stored.reps[0].id is not None
    assert all(f.rep_analysis_id == stored.reps[0].id for f in stored.reps[0].findings)

    fetched = repo.get_analysis(stored.analysis.id or 0)
    assert fetched is not None
    assert fetched.analysis == stored.analysis
    assert fetched.reps[0].metrics == {"depth_ratio@bottom": 0.061}
    assert {f.fault_id for f in fetched.reps[0].findings} == {"insufficient_depth", "knee_valgus"}
    not_assessed = next(f for f in fetched.reps[0].findings if f.fault_id == "knee_valgus")
    assert not_assessed.not_assessed_reason is NotAssessedReason.VIEW_MISMATCH
    assert not_assessed.measured is None


def test_fr10_analyses_can_be_listed_and_filtered(repo):
    repo.save_analysis(_analysis_result())
    other = _analysis_result()
    other = AnalysisResult(
        analysis=other.analysis.model_copy(
            update={"exercise_id": "push-up", "form_profile_id": "pushup_v1"}
        ),
        reps=other.reps,
    )
    repo.save_analysis(other)
    assert len(repo.list_analyses()) == 2
    assert len(repo.list_analyses("push-up")) == 1
    assert repo.get_analysis(999) is None


def test_fr10_rejected_analysis_persists_with_null_reps(repo):
    base = _analysis_result().analysis
    rejected = base.model_copy(
        update={
            "status": AnalysisStatus.REJECTED,
            "reject_reason": "insufficient_visibility",
            "rep_count": None,
            "clip_score": None,
        }
    )
    stored = repo.save_analysis(AnalysisResult(analysis=rejected, reps=[]))
    fetched = repo.get_analysis(stored.analysis.id or 0)
    assert fetched is not None
    assert fetched.analysis.status is AnalysisStatus.REJECTED
    assert fetched.analysis.rep_count is None
    assert fetched.reps == []


def test_fr1_sqlite_rejects_a_second_profile_row(sqlite_repo):
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_repo.connection.execute(
            "INSERT INTO user_profile VALUES (2,'general','beginner',3,'[]','[]','kg',NULL,'[]','x')"
        )


def test_fr4_sqlite_enforces_the_landmark_ordering(sqlite_repo):
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_repo.connection.execute("INSERT INTO volume_landmark VALUES ('bogus', 5, 4, 3, 2)")


def test_fr2_sqlite_file_backend_creates_its_parent_directory(tmp_path, datasets):
    from formcoach.store.sqlite import SQLiteRepository

    path = tmp_path / "nested" / "formcoach.db"
    repo = SQLiteRepository(path)
    repo.initialize()
    repo.load_library(datasets)
    assert path.is_file()
    assert repo.get_exercise("push-up") is not None
    repo.close()


def test_fr3_weekly_set_targets_survive_the_json_round_trip(sqlite_repo, datasets):
    profile = UserProfile(
        goal=Goal.STRENGTH,
        experience=Experience.ADVANCED,
        days_per_week=4,
        equipment=list(Equipment),
        disclaimer_acknowledged_at=ACK,
        updated_at=ACK,
    )
    plan = generate_program(profile, datasets, as_of="2026-07-06", seed=8)
    stored = sqlite_repo.save_program(plan)
    raw = sqlite_repo.connection.execute(
        "SELECT weekly_set_targets FROM program WHERE id = ?", (stored.program.id,)
    ).fetchone()[0]
    decoded = json.loads(raw)
    assert set(decoded) == {m.value for m in plan.program.target_muscles}
    for muscle, weeks in plan.program.weekly_set_targets.items():
        assert decoded[muscle.value] == weeks
        assert len(weeks) == 5
        assert sum(weeks[:4]) <= SETS_PER_SESSION_BUDGET * 4 * 4
