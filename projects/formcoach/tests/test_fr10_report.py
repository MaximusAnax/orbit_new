"""FR-10 and FR-15 — form reports for clips and for single photos."""

from __future__ import annotations

import pytest
from formcoach.engine.poseio import REJECT_INSUFFICIENT_VISIBILITY
from formcoach.engine.report import (
    EMPTY_CLIP_SCORE,
    NotAnalyzableError,
    analyze_clip,
    analyze_photo,
    build_corrections,
    profile_for,
    rep_score,
)
from formcoach.models import (
    AnalysisKind,
    AnalysisStatus,
    DeclaredView,
    FaultFinding,
    FindingStatus,
    NotAssessedReason,
    Phase,
    PoseFrame,
    RepAnalysis,
    Severity,
    View,
)
from formcoach_synthetic import (
    deadlift_sequence,
    front_squat_sequence,
    occlude,
    push_out_of_frame,
    pushup_sequence,
    side_squat_frame,
    side_squat_sequence,
)

CREATED = "2026-07-15T18:30:00+00:00"


@pytest.fixture
def squat(datasets):
    return datasets.exercise("barbell-back-squat"), datasets.form_profiles["squat_v1"]


@pytest.fixture
def pushup(datasets):
    return datasets.exercise("push-up"), datasets.form_profiles["pushup_v1"]


@pytest.fixture
def deadlift(datasets):
    return datasets.exercise("barbell-deadlift"), datasets.form_profiles["deadlift_v1"]


# ----------------------------------------------------------------- scoring


def _finding(fault_id: str, severity: Severity, status: FindingStatus) -> FaultFinding:
    return FaultFinding(
        fault_id=fault_id,
        status=status,
        measured=None if status is FindingStatus.NOT_ASSESSED else 1.0,
        not_assessed_reason=(
            NotAssessedReason.VIEW_MISMATCH if status is FindingStatus.NOT_ASSESSED else None
        ),
        threshold=0.5,
        severity=severity,
        frame=None if status is FindingStatus.NOT_ASSESSED else 3,
        cue="fix it" if status is FindingStatus.FAULT else None,
    )


def test_fr10_rep_score_deducts_25_15_and_8():
    findings = [
        _finding("a", Severity.MAJOR, FindingStatus.FAULT),
        _finding("b", Severity.MODERATE, FindingStatus.FAULT),
        _finding("c", Severity.MINOR, FindingStatus.FAULT),
    ]
    assert rep_score(findings) == 100 - 25 - 15 - 8
    assert rep_score([]) == 100.0


def test_fr10_rep_score_floors_at_zero():
    findings = [_finding(str(i), Severity.MAJOR, FindingStatus.FAULT) for i in range(6)]
    assert rep_score(findings) == 0.0


def test_fr10_ok_and_not_assessed_findings_cost_nothing():
    findings = [
        _finding("a", Severity.MAJOR, FindingStatus.OK),
        _finding("b", Severity.MAJOR, FindingStatus.NOT_ASSESSED),
    ]
    assert rep_score(findings) == 100.0


def test_fr10_corrections_sort_by_severity_then_count_then_id(datasets):
    profile = datasets.form_profiles["squat_v1"]

    def rep(index: int, fault_ids: list[str]) -> RepAnalysis:
        findings = [
            FaultFinding(
                fault_id=fid,
                status=FindingStatus.FAULT,
                measured=1.0,
                threshold=profile.rule(fid).threshold,
                severity=profile.rule(fid).severity,
                frame=1,
                cue=profile.rule(fid).cue,
            )
            for fid in fault_ids
        ]
        return RepAnalysis(
            rep_index=index,
            start_frame=0,
            extremum_frame=1,
            end_frame=2,
            score=rep_score(findings),
            findings=findings,
        )

    reps = [
        rep(0, ["lateral_shift", "excessive_trunk_lean", "knee_valgus"]),
        rep(1, ["lateral_shift", "insufficient_depth", "knee_valgus"]),
        rep(2, ["lateral_shift", "knee_valgus"]),
    ]
    corrections = build_corrections(reps, profile)
    assert [(c.fault_id, c.occurrences) for c in corrections] == [
        ("knee_valgus", 3),
        ("insufficient_depth", 1),
        ("excessive_trunk_lean", 1),
        ("lateral_shift", 3),
    ]
    assert corrections[0].cue == profile.rule("knee_valgus").cue


# ------------------------------------------------------------ clip analysis


def test_fr10_clean_squat_clip_scores_100(squat):
    exercise, profile = squat
    sequence, truth = side_squat_sequence(reps=4, bottom_beta_deg=100.0, bottom_trunk_deg=35.0)
    result = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    assert result.analysis.status is AnalysisStatus.COMPLETED
    assert result.analysis.analysis_kind is AnalysisKind.CLIP
    assert result.analysis.rep_count == truth.reps
    assert result.analysis.clip_score == 100.0
    assert result.corrections == []
    assert result.analysis.fps == 30.0
    assert result.analysis.declared_phase is None
    assert result.analysis.form_profile_id == "squat_v1"


def test_fr10_faulty_squat_clip_reports_measurement_threshold_and_cue(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=3, bottom_beta_deg=80.0, bottom_trunk_deg=60.0)
    result = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    assert result.analysis.clip_score == pytest.approx(100 - 25 - 15)
    faults = {c.fault_id for c in result.corrections}
    assert faults == {"insufficient_depth", "excessive_trunk_lean"}
    for rep in result.reps:
        depth = next(f for f in rep.findings if f.fault_id == "insufficient_depth")
        assert depth.status is FindingStatus.FAULT
        assert depth.measured is not None and depth.measured > depth.threshold
        assert depth.frame == rep.extremum_frame
        assert depth.cue


def test_fr10_clip_score_is_the_mean_of_rep_scores(pushup):
    exercise, profile = pushup
    sequence, _ = pushup_sequence(reps=3, bottom_elbow_deg=112.0, top_elbow_deg=150.0)
    result = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    expected = sum(r.score for r in result.reps) / len(result.reps)
    assert result.analysis.clip_score == pytest.approx(expected)
    assert all(r.score == pytest.approx(100 - 15 - 8) for r in result.reps)


def test_fr10_every_rep_carries_the_full_finding_matrix(deadlift):
    exercise, profile = deadlift
    sequence, _ = deadlift_sequence(reps=3)
    result = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    for rep in result.reps:
        assert len(rep.findings) == len(profile.rules)
        assert rep.start_frame < rep.extremum_frame < rep.end_frame


def test_fr10_rejected_clip_has_null_reps_and_score(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=3)
    degraded = occlude(sequence, ["left_knee"], fraction=0.5)
    result = analyze_clip(degraded, exercise=exercise, profile=profile, created_at=CREATED)
    assert result.analysis.status is AnalysisStatus.REJECTED
    assert result.analysis.reject_reason == REJECT_INSUFFICIENT_VISIBILITY
    assert result.analysis.rep_count is None
    assert result.analysis.clip_score is None
    assert result.reps == []
    assert result.corrections == []


def test_fr7_out_of_frame_clip_is_rejected_with_the_same_reason(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=3)
    degraded = push_out_of_frame(sequence, ["left_ankle", "right_ankle"], fraction=0.5)
    result = analyze_clip(degraded, exercise=exercise, profile=profile, created_at=CREATED)
    assert result.analysis.status is AnalysisStatus.REJECTED
    assert result.analysis.reject_reason == REJECT_INSUFFICIENT_VISIBILITY


def test_fr10_a_motionless_clip_completes_with_zero_reps(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=1)
    still = sequence.model_copy(
        update={
            "frames": [
                PoseFrame(t_ms=i * 33.0, keypoints=side_squat_frame(5.0, 5.0, 0.0))
                for i in range(90)
            ]
        }
    )
    result = analyze_clip(still, exercise=exercise, profile=profile, created_at=CREATED)
    assert result.analysis.status is AnalysisStatus.COMPLETED
    assert result.analysis.rep_count == 0
    assert result.analysis.clip_score == EMPTY_CLIP_SCORE


def test_fr14_analysis_is_deterministic(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=3, noise_sigma=0.006, seed=42)
    first = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    second = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    assert first == second


def test_fr10_view_resolution_is_recorded_on_the_analysis(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=2, declared_view=None)
    inferred = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    assert inferred.analysis.view_inferred is True
    declared = analyze_clip(
        sequence,
        exercise=exercise,
        profile=profile,
        created_at=CREATED,
        declared_view=DeclaredView.SIDE_LEFT,
    )
    assert declared.analysis.view_inferred is False
    assert declared.analysis.view is View.SIDE_LEFT


def test_fr10_front_clip_of_the_same_exercise_scores_the_front_rules(squat):
    exercise, profile = squat
    sequence = front_squat_sequence(reps=3, knee_medial_offset=0.10)
    result = analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    assert result.analysis.view is View.FRONT
    assert {c.fault_id for c in result.corrections} == {"knee_valgus"}
    assert result.analysis.clip_score == pytest.approx(75.0)


def test_fr10_non_analyzable_exercise_is_refused(datasets):
    exercise = datasets.exercise("barbell-curl")
    profile = datasets.form_profiles["squat_v1"]
    sequence, _ = side_squat_sequence(reps=1)
    with pytest.raises(NotAnalyzableError):
        analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)
    with pytest.raises(NotAnalyzableError):
        profile_for(datasets, exercise)


def test_fr10_mismatched_profile_is_refused(datasets):
    exercise = datasets.exercise("barbell-back-squat")
    profile = datasets.form_profiles["pushup_v1"]
    sequence, _ = side_squat_sequence(reps=1)
    with pytest.raises(NotAnalyzableError):
        analyze_clip(sequence, exercise=exercise, profile=profile, created_at=CREATED)


# ----------------------------------------------------------- photo analysis


def _photo(sequence, index: int):
    return sequence.model_copy(update={"frames": sequence.frames[index : index + 1]})


def test_fr15_photo_creates_one_synthetic_rep_at_frame_zero(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=1, bottom_beta_deg=80.0)
    result = analyze_photo(
        _photo(sequence, 30),
        exercise=exercise,
        profile=profile,
        created_at=CREATED,
        phase=Phase.BOTTOM,
    )
    assert result.analysis.analysis_kind is AnalysisKind.PHOTO
    assert result.analysis.declared_phase is Phase.BOTTOM
    assert result.analysis.fps is None
    assert result.analysis.rep_count == 1
    rep = result.reps[0]
    assert (rep.rep_index, rep.start_frame, rep.extremum_frame, rep.end_frame) == (0, 0, 0, 0)


def test_fr15_photo_scores_the_declared_phase_and_refuses_the_others(pushup):
    exercise, profile = pushup
    sequence, _ = pushup_sequence(reps=1, bottom_elbow_deg=115.0, top_elbow_deg=150.0)
    result = analyze_photo(
        _photo(sequence, 30),
        exercise=exercise,
        profile=profile,
        created_at=CREATED,
        phase=Phase.BOTTOM,
    )
    by_id = {f.fault_id: f for f in result.reps[0].findings}
    assert by_id["insufficient_depth"].status is FindingStatus.FAULT
    assert by_id["hip_sag_or_pike"].status is not FindingStatus.NOT_ASSESSED
    assert by_id["incomplete_lockout"].status is FindingStatus.NOT_ASSESSED
    assert by_id["incomplete_lockout"].not_assessed_reason is NotAssessedReason.PHASE_NOT_SHOWN
    assert result.analysis.clip_score == pytest.approx(100 - 15)


def test_fr15_photo_at_the_top_scores_the_lockout_rule_instead(pushup):
    exercise, profile = pushup
    sequence, _ = pushup_sequence(reps=1, bottom_elbow_deg=115.0, top_elbow_deg=150.0)
    result = analyze_photo(
        _photo(sequence, 0),
        exercise=exercise,
        profile=profile,
        created_at=CREATED,
        phase=Phase.TOP,
    )
    by_id = {f.fault_id: f for f in result.reps[0].findings}
    assert by_id["incomplete_lockout"].status is FindingStatus.FAULT
    assert by_id["insufficient_depth"].not_assessed_reason is NotAssessedReason.PHASE_NOT_SHOWN


def test_fr15_multi_frame_rules_are_structurally_unassessable_on_a_photo(deadlift):
    exercise, profile = deadlift
    sequence, _ = deadlift_sequence(reps=1, rise_ratio=2.4, lockout_deg=178.0)
    result = analyze_photo(
        _photo(sequence, 30),
        exercise=exercise,
        profile=profile,
        created_at=CREATED,
        phase=Phase.TOP,
    )
    by_id = {f.fault_id: f for f in result.reps[0].findings}
    assert by_id["hips_rise_early"].status is FindingStatus.NOT_ASSESSED
    assert by_id["hips_rise_early"].not_assessed_reason is NotAssessedReason.NEEDS_MULTI_FRAME
    # a whole_rep rule still resolves on the single frame it was given
    assert by_id["bar_drift"].status is not FindingStatus.NOT_ASSESSED
    assert by_id["incomplete_lockout"].status is not FindingStatus.NOT_ASSESSED


def test_fr15_photo_keeps_the_view_rules(squat):
    exercise, profile = squat
    sequence = front_squat_sequence(reps=1, knee_medial_offset=0.10)
    result = analyze_photo(
        _photo(sequence, 30),
        exercise=exercise,
        profile=profile,
        created_at=CREATED,
        phase=Phase.BOTTOM,
        declared_view=DeclaredView.FRONT,
    )
    by_id = {f.fault_id: f for f in result.reps[0].findings}
    assert by_id["knee_valgus"].status is FindingStatus.FAULT
    assert by_id["insufficient_depth"].not_assessed_reason is NotAssessedReason.VIEW_MISMATCH


def test_fr15_photo_rejects_an_unreadable_frame(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=1)
    blind = occlude(_photo(sequence, 30), ["left_knee"], fraction=1.0)
    result = analyze_photo(
        blind, exercise=exercise, profile=profile, created_at=CREATED, phase=Phase.BOTTOM
    )
    assert result.analysis.status is AnalysisStatus.REJECTED
    assert result.analysis.reject_reason == REJECT_INSUFFICIENT_VISIBILITY
    assert result.analysis.rep_count is None
    assert result.analysis.declared_phase is Phase.BOTTOM
    assert result.analysis.fps is None


def test_fr15_photo_phase_must_be_bottom_or_top(squat):
    exercise, profile = squat
    sequence, _ = side_squat_sequence(reps=1)
    with pytest.raises(ValueError):
        analyze_photo(
            _photo(sequence, 30),
            exercise=exercise,
            profile=profile,
            created_at=CREATED,
            phase=Phase.WHOLE_REP,
        )
