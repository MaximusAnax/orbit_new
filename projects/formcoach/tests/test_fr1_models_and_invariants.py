"""FR-1 / FR-5 / FR-10 — the model-level invariants DATA_MODEL.md states."""

from __future__ import annotations

import pytest
from formcoach.models import (
    AnalysisKind,
    AnalysisStatus,
    Comparator,
    Equipment,
    Exercise,
    Experience,
    FaultFinding,
    FaultRule,
    FindingStatus,
    FormAnalysis,
    Goal,
    Mechanics,
    MediaAsset,
    MediaKind,
    MediaSource,
    MovementPattern,
    Muscle,
    NotAssessedReason,
    Phase,
    PoseSource,
    Prescription,
    SetLog,
    Severity,
    UserProfile,
    View,
    VolumeLandmark,
    kg_to_lb,
    lb_to_kg,
    round_half_up,
)
from pydantic import ValidationError

TS = "2026-07-01T08:00:00+00:00"


def _profile(**overrides) -> UserProfile:
    base = {
        "goal": Goal.HYPERTROPHY,
        "experience": Experience.INTERMEDIATE,
        "days_per_week": 4,
        "equipment": [Equipment.BARBELL],
        "updated_at": TS,
    }
    return UserProfile(**{**base, **overrides})


# --------------------------------------------------------------------- FR-1


def test_fr1_profile_is_a_singleton_row():
    assert _profile().id == 1
    with pytest.raises(ValidationError):
        _profile(id=2)


@pytest.mark.parametrize("days", [1, 0, 7, 12])
def test_fr1_days_per_week_must_be_two_to_six(days):
    with pytest.raises(ValidationError):
        _profile(days_per_week=days)


def test_fr1_equipment_must_be_non_empty():
    with pytest.raises(ValidationError):
        _profile(equipment=[])


def test_fr1_emphasized_muscles_capped_at_three_and_unique():
    _profile(emphasized_muscles=[Muscle.CHEST, Muscle.QUADS, Muscle.LATS])
    with pytest.raises(ValidationError):
        _profile(emphasized_muscles=[Muscle.CHEST] * 4)
    with pytest.raises(ValidationError):
        _profile(emphasized_muscles=[Muscle.CHEST, Muscle.CHEST])


def test_fr1_units_are_display_only_and_convert_round_trip():
    assert lb_to_kg(kg_to_lb(100.0)) == pytest.approx(100.0)
    assert lb_to_kg(220.462262185) == pytest.approx(100.0, abs=1e-6)


def test_conventions_round_is_half_up_not_bankers():
    assert round_half_up(2.5) == 3
    assert round_half_up(3.5) == 4
    assert round_half_up(2.4999) == 2
    assert round(2.5) == 2  # the built-in is why round_half_up exists


# ----------------------------------------------------------------- exercises


def _exercise(**overrides) -> Exercise:
    base = {
        "id": "x",
        "name": "X",
        "primary_muscles": [Muscle.CHEST],
        "secondary_muscles": [Muscle.TRICEPS],
        "equipment": [Equipment.BARBELL],
        "pattern": MovementPattern.HORIZONTAL_PUSH,
        "mechanics": Mechanics.COMPOUND,
        "difficulty": 2,
        "rest_s": 180,
    }
    return Exercise(**{**base, **overrides})


def test_fr2_secondary_muscles_are_disjoint_from_primary():
    with pytest.raises(ValidationError):
        _exercise(secondary_muscles=[Muscle.CHEST])


def test_fr2_effective_credit_matches_the_scope_convention():
    exercise = _exercise()
    assert exercise.effective_credit(Muscle.CHEST) == 1.0
    assert exercise.effective_credit(Muscle.TRICEPS) == 0.5
    assert exercise.effective_credit(Muscle.CALVES) == 0.0


def test_fr2_analyzable_flag_follows_form_profile_id():
    assert not _exercise().is_analyzable
    assert _exercise(form_profile_id="squat_v1").is_analyzable


def test_fr2_url_media_must_be_https_and_cc_licenses_need_attribution():
    MediaAsset(
        id="a#1",
        exercise_id="a",
        kind=MediaKind.IMAGE,
        source=MediaSource.URL,
        ref="https://example.org/a.png",
        license="CC-BY-SA-4.0",
        attribution="somebody",
    )
    with pytest.raises(ValidationError):
        MediaAsset(
            id="a#2",
            exercise_id="a",
            kind=MediaKind.IMAGE,
            source=MediaSource.URL,
            ref="http://example.org/a.png",
            license="public-domain",
        )
    with pytest.raises(ValidationError):
        MediaAsset(
            id="a#3",
            exercise_id="a",
            kind=MediaKind.IMAGE,
            source=MediaSource.URL,
            ref="https://example.org/a.png",
            license="CC-BY-4.0",
        )


def test_fr4_volume_landmarks_must_be_ordered():
    VolumeLandmark(muscle=Muscle.CHEST, mv=4, mev=8, mav=16, mrv=22)
    with pytest.raises(ValidationError):
        VolumeLandmark(muscle=Muscle.CHEST, mv=4, mev=16, mav=8, mrv=22)
    with pytest.raises(ValidationError):
        VolumeLandmark(muscle=Muscle.CHEST, mv=4, mev=8, mav=8, mrv=22)


def test_fr4_landmark_band_classification():
    landmark = VolumeLandmark(muscle=Muscle.CHEST, mv=4, mev=8, mav=16, mrv=22)
    assert landmark.band(7.5).value == "below_mev"
    assert landmark.band(8.0).value == "mev_mav"
    assert landmark.band(16.0).value == "mev_mav"
    assert landmark.band(16.5).value == "mav_mrv"
    assert landmark.band(22.5).value == "above_mrv"


# --------------------------------------------------------------------- FR-5


def test_fr5_e1rm_is_rir_adjusted_epley_when_loaded_and_rated():
    set_log = SetLog(exercise_id="bench", set_index=0, weight_kg=80.0, reps=8, rir=2.0)
    assert set_log.e1rm_kg == pytest.approx(80.0 * (1 + 10 / 30))


def test_fr5_e1rm_is_null_for_unrated_sets():
    assert SetLog(exercise_id="b", set_index=0, weight_kg=80.0, reps=8, rir=None).e1rm_kg is None


def test_fr5_e1rm_is_null_for_bodyweight_sets():
    assert SetLog(exercise_id="b", set_index=0, weight_kg=0.0, reps=8, rir=2.0).e1rm_kg is None


def test_fr5_negative_weight_and_zero_reps_are_rejected():
    with pytest.raises(ValidationError):
        SetLog(exercise_id="b", set_index=0, weight_kg=-1.0, reps=5)
    with pytest.raises(ValidationError):
        SetLog(exercise_id="b", set_index=0, weight_kg=10.0, reps=0)


def test_fr5_set_logs_are_frozen_append_only_records():
    set_log = SetLog(exercise_id="b", set_index=0, weight_kg=80.0, reps=8, rir=2.0)
    with pytest.raises(ValidationError):
        set_log.reps = 9


def test_fr3_prescription_rep_range_must_be_ordered():
    Prescription(
        position=0, exercise_id="b", sets=3, rep_low=5, rep_high=10, target_rir=2.0, rest_s=180
    )
    with pytest.raises(ValidationError):
        Prescription(
            position=0, exercise_id="b", sets=3, rep_low=10, rep_high=5, target_rir=2.0, rest_s=180
        )


# -------------------------------------------------------------------- FR-10


def _analysis(**overrides) -> FormAnalysis:
    base = {
        "created_at": TS,
        "analysis_kind": AnalysisKind.CLIP,
        "exercise_id": "barbell-back-squat",
        "form_profile_id": "squat_v1",
        "source_ref": "clip.json",
        "pose_source": PoseSource.FIXTURE,
        "view": View.SIDE_LEFT,
        "view_inferred": False,
        "fps": 30.0,
        "frames_total": 100,
        "frames_valid": 100,
        "status": AnalysisStatus.COMPLETED,
        "rep_count": 5,
        "clip_score": 92.0,
    }
    return FormAnalysis(**{**base, **overrides})


def test_fr10_reject_reason_rep_count_and_score_are_null_iff_rejected():
    _analysis()
    _analysis(
        status=AnalysisStatus.REJECTED,
        reject_reason="insufficient_visibility",
        rep_count=None,
        clip_score=None,
    )
    with pytest.raises(ValidationError):
        _analysis(status=AnalysisStatus.REJECTED, rep_count=None, clip_score=None)
    with pytest.raises(ValidationError):
        _analysis(status=AnalysisStatus.REJECTED, reject_reason="x", clip_score=None)
    with pytest.raises(ValidationError):
        _analysis(reject_reason="x")


def test_fr15_declared_phase_is_set_iff_photo_and_fps_is_null():
    _analysis(analysis_kind=AnalysisKind.PHOTO, declared_phase=Phase.BOTTOM, fps=None, rep_count=1)
    with pytest.raises(ValidationError):
        _analysis(analysis_kind=AnalysisKind.PHOTO, fps=None, rep_count=1)
    with pytest.raises(ValidationError):
        _analysis(declared_phase=Phase.BOTTOM)
    with pytest.raises(ValidationError):
        _analysis(
            analysis_kind=AnalysisKind.PHOTO, declared_phase=Phase.BOTTOM, fps=30.0, rep_count=1
        )


def test_fr9_finding_iff_invariants():
    FaultFinding(
        fault_id="d",
        status=FindingStatus.OK,
        measured=0.0,
        threshold=0.03,
        severity=Severity.MAJOR,
        frame=4,
    )
    FaultFinding(
        fault_id="d",
        status=FindingStatus.FAULT,
        measured=0.5,
        threshold=0.03,
        severity=Severity.MAJOR,
        frame=4,
        cue="fix it",
    )
    FaultFinding(
        fault_id="d",
        status=FindingStatus.NOT_ASSESSED,
        threshold=0.03,
        severity=Severity.MAJOR,
        not_assessed_reason=NotAssessedReason.VIEW_MISMATCH,
    )
    with pytest.raises(ValidationError):  # fault needs a cue
        FaultFinding(
            fault_id="d",
            status=FindingStatus.FAULT,
            measured=0.5,
            threshold=0.03,
            severity=Severity.MAJOR,
            frame=1,
        )
    with pytest.raises(ValidationError):  # ok must not carry a cue
        FaultFinding(
            fault_id="d",
            status=FindingStatus.OK,
            measured=0.0,
            threshold=0.03,
            severity=Severity.MAJOR,
            frame=1,
            cue="fix it",
        )
    with pytest.raises(ValidationError):  # not_assessed must not carry a measurement
        FaultFinding(
            fault_id="d",
            status=FindingStatus.NOT_ASSESSED,
            measured=1.0,
            threshold=0.03,
            severity=Severity.MAJOR,
            not_assessed_reason=NotAssessedReason.VIEW_MISMATCH,
        )
    with pytest.raises(ValidationError):  # not_assessed needs a reason
        FaultFinding(
            fault_id="d", status=FindingStatus.NOT_ASSESSED, threshold=0.03, severity=Severity.MAJOR
        )


@pytest.mark.parametrize(
    ("comparator", "threshold", "value", "expected"),
    [
        (Comparator.GT, 0.03, 0.04, True),
        (Comparator.GT, 0.03, 0.03, False),
        (Comparator.LT, 170.0, 169.0, True),
        (Comparator.LT, 170.0, 170.0, False),
        (Comparator.ABS_GT, 0.2, -0.25, True),
        (Comparator.ABS_GT, 0.2, 0.19, False),
    ],
)
def test_fr9_rule_comparators(comparator, threshold, value, expected):
    rule = FaultRule(
        fault_id="f",
        views=[View.SIDE_LEFT],
        feature="depth_ratio",
        feature_keypoints=["left_hip"],
        phase=Phase.BOTTOM,
        comparator=comparator,
        threshold=threshold,
        severity=Severity.MAJOR,
        cue="c",
    )
    assert rule.violated_by(value) is expected
