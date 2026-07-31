"""FR-4 — effective-set attribution and the weekly volume report."""

from __future__ import annotations

import pytest
from formcoach.engine.volume import (
    accumulate_direct,
    accumulate_effective,
    allocations_from_logs,
    iso_week_of,
    weekly_volume_report,
)
from formcoach.models import LoggedSession, Muscle, SetLog, VolumeBand, WorkoutLog

WEEK = "2026-W29"


def logged(day: str, entries: list[tuple[str, int]]) -> LoggedSession:
    sets: list[SetLog] = []
    for exercise_id, count in entries:
        for index in range(count):
            sets.append(
                SetLog(exercise_id=exercise_id, set_index=index, weight_kg=60.0, reps=8, rir=2.0)
            )
    return LoggedSession(
        workout=WorkoutLog(performed_at=f"2026-07-{day}T18:00:00+00:00"), sets=sets
    )


def test_fr4_iso_week_label_is_derived_from_the_supplied_timestamp():
    assert iso_week_of("2026-07-13T18:00:00+00:00") == "2026-W29"
    assert iso_week_of("2026-07-19T23:59:00+00:00") == "2026-W29"
    assert iso_week_of("2026-07-20T00:01:00+00:00") == "2026-W30"


def test_fr4_effective_sets_are_one_for_primary_and_half_for_secondary(datasets):
    bench = datasets.exercise("barbell-bench-press")
    totals = accumulate_effective([(bench, 4)])
    assert totals[Muscle.CHEST] == 4.0
    assert totals[Muscle.TRICEPS] == 2.0
    assert totals[Muscle.FRONT_DELTS] == 2.0
    assert totals[Muscle.CALVES] == 0.0


def test_fr4_direct_sets_only_count_the_primary_attribution(datasets):
    bench = datasets.exercise("barbell-bench-press")
    direct = accumulate_direct([(bench, 4)])
    assert direct[Muscle.CHEST] == 4
    assert direct[Muscle.TRICEPS] == 0


def test_fr4_multi_primary_exercises_credit_every_primary_muscle(datasets):
    squat = datasets.exercise("barbell-back-squat")
    totals = accumulate_effective([(squat, 3)])
    assert totals[Muscle.QUADS] == 3.0
    assert totals[Muscle.GLUTES] == 3.0
    assert totals[Muscle.HAMSTRINGS] == 1.5


def test_fr4_report_covers_all_fifteen_muscles(datasets):
    report = weekly_volume_report([logged("13", [("barbell-bench-press", 4)])], datasets, WEEK)
    assert len(report.rows) == 15
    assert {row.muscle for row in report.rows} == set(Muscle)
    assert report.iso_week == WEEK


def test_fr4_report_classifies_each_muscle_against_its_landmarks(datasets):
    sessions = [
        logged("13", [("barbell-bench-press", 6)]),
        logged("16", [("dumbbell-chest-fly", 6)]),
    ]
    report = weekly_volume_report(sessions, datasets, WEEK)
    chest = report.row(Muscle.CHEST)
    assert chest.effective_sets == 12.0
    assert chest.direct_sets == 12
    assert chest.band is VolumeBand.MEV_MAV
    assert report.row(Muscle.CALVES).band is VolumeBand.BELOW_MEV


def test_fr4_above_mrv_is_flagged(datasets):
    sessions = [logged("13", [("barbell-bench-press", 10)]) for _ in range(3)]
    report = weekly_volume_report(sessions, datasets, WEEK)
    assert report.row(Muscle.CHEST).effective_sets == 30.0
    assert report.row(Muscle.CHEST).band is VolumeBand.ABOVE_MRV


def test_fr4_target_and_incidental_muscles_are_labelled(datasets):
    report = weekly_volume_report(
        [logged("13", [("barbell-bench-press", 4)])],
        datasets,
        WEEK,
        target_muscles=[Muscle.CHEST, Muscle.LATS],
    )
    assert report.row(Muscle.CHEST).is_target is True
    assert report.row(Muscle.TRICEPS).is_target is False


def test_fr4_only_the_requested_iso_week_is_counted(datasets):
    sessions = [
        logged("13", [("barbell-bench-press", 4)]),  # 2026-W29
        logged("21", [("barbell-bench-press", 4)]),  # 2026-W30
    ]
    assert (
        weekly_volume_report(sessions, datasets, "2026-W29").row(Muscle.CHEST).effective_sets == 4.0
    )
    assert (
        weekly_volume_report(sessions, datasets, "2026-W30").row(Muscle.CHEST).effective_sets == 4.0
    )
    assert (
        weekly_volume_report(sessions, datasets, "2026-W31").row(Muscle.CHEST).effective_sets == 0.0
    )


def test_fr4_unknown_exercises_contribute_no_invented_volume(datasets):
    session = LoggedSession(
        workout=WorkoutLog(performed_at="2026-07-13T18:00:00+00:00"),
        sets=[SetLog(exercise_id="not-a-real-exercise", set_index=0, weight_kg=50.0, reps=8)],
    )
    assert allocations_from_logs([session], datasets) == []
    report = weekly_volume_report([session], datasets, WEEK)
    assert all(row.effective_sets == 0.0 for row in report.rows)


def test_fr4_report_carries_the_landmark_numbers_for_display(datasets):
    report = weekly_volume_report([], datasets, WEEK)
    chest = report.row(Muscle.CHEST)
    landmark = datasets.landmarks[Muscle.CHEST]
    assert (chest.mv, chest.mev, chest.mav, chest.mrv) == (
        landmark.mv,
        landmark.mev,
        landmark.mav,
        landmark.mrv,
    )


def test_fr4_missing_muscle_lookup_is_an_error(datasets):
    report = weekly_volume_report([], datasets, WEEK)
    assert report.row(Muscle.ABS).muscle is Muscle.ABS
    with pytest.raises(KeyError):
        report.row("not-a-muscle")  # type: ignore[arg-type]
