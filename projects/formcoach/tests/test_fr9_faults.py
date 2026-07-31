"""FR-9 — rule evaluation against a form profile.

The complete-matrix invariant is what M1, M4b and M7 consume, so it is asserted
directly: one finding per rule per rep, and ``not_assessed`` exactly when the
view is wrong, the keypoints are not visible, or (for photos) the phase was not
shown.
"""

from __future__ import annotations

import pytest
from formcoach.engine.faults import (
    ascent_span,
    evaluate_rep,
    measure,
    metric_keys,
    phase_frames,
)
from formcoach.engine.geometry import facing_sign
from formcoach.engine.reps import segment_reps, smooth_sequence
from formcoach.models import (
    FindingStatus,
    NotAssessedReason,
    Phase,
    RepBoundary,
    RepDirection,
    View,
)
from formcoach_synthetic import (
    FRONT_VALGUS_DEG,
    deadlift_sequence,
    front_squat_sequence,
    occlude,
    pushup_sequence,
    side_squat_sequence,
)


def analyzed(sequence, profile, view):
    """Segment, smooth and evaluate every rep, mirroring the FR-10 pipeline."""
    boundaries = segment_reps(sequence, profile)
    measured = smooth_sequence(sequence, 9)
    facing = facing_sign(measured.frames)
    return [evaluate_rep(measured, profile, boundary, view, facing) for boundary in boundaries]


# ---------------------------------------------------------------- phase logic


def test_fr9_phase_frames_follow_the_movement_direction():
    rep = RepBoundary(rep_index=0, start_frame=10, extremum_frame=40, end_frame=70)
    assert phase_frames(rep, RepDirection.DOWN_UP, Phase.BOTTOM) == [40]
    assert phase_frames(rep, RepDirection.DOWN_UP, Phase.TOP) == [70]
    assert phase_frames(rep, RepDirection.UP_DOWN, Phase.BOTTOM) == [10]
    assert phase_frames(rep, RepDirection.UP_DOWN, Phase.TOP) == [40]
    assert phase_frames(rep, RepDirection.DOWN_UP, Phase.WHOLE_REP) == list(range(10, 71))


def test_fr9_ascent_early_is_the_first_40_percent_of_the_concentric():
    rep = RepBoundary(rep_index=0, start_frame=0, extremum_frame=30, end_frame=60)
    assert ascent_span(rep, RepDirection.UP_DOWN) == (0, 30)
    assert ascent_span(rep, RepDirection.DOWN_UP) == (30, 60)
    assert phase_frames(rep, RepDirection.UP_DOWN, Phase.ASCENT_EARLY) == list(range(0, 13))
    assert phase_frames(rep, RepDirection.DOWN_UP, Phase.ASCENT_EARLY) == list(range(30, 43))


def test_fr9_metric_keys_disambiguate_a_feature_used_at_two_phases(datasets):
    pushup = datasets.form_profiles["pushup_v1"]
    squat = datasets.form_profiles["squat_v1"]
    assert metric_keys(pushup, pushup.rule("insufficient_depth")) == ["elbow_angle_deg@bottom"]
    assert metric_keys(pushup, pushup.rule("incomplete_lockout")) == ["elbow_angle_deg@top"]
    assert metric_keys(squat, squat.rule("insufficient_depth")) == [
        "depth_ratio@bottom",
        "depth_ratio",
    ]


# ---------------------------------------------------------- complete matrix


@pytest.mark.parametrize(
    ("profile_id", "builder", "view"),
    [
        ("squat_v1", side_squat_sequence, View.SIDE_LEFT),
        ("deadlift_v1", deadlift_sequence, View.SIDE_RIGHT),
        ("pushup_v1", pushup_sequence, View.SIDE_LEFT),
    ],
)
def test_fr9_emits_one_finding_per_rule_per_rep(datasets, profile_id, builder, view):
    profile = datasets.form_profiles[profile_id]
    sequence, truth = builder(reps=3)
    per_rep = analyzed(sequence, profile, view)
    assert len(per_rep) == truth.reps
    for findings, _ in per_rep:
        assert [f.fault_id for f in findings] == [r.fault_id for r in profile.rules]


# ------------------------------------------------------------- squat rules


@pytest.mark.parametrize(
    ("beta", "expect_fault"),
    [(80.0, True), (88.0, True), (91.0, False), (100.0, False)],
)
def test_fr9_squat_depth_fault_fires_above_the_threshold(datasets, beta, expect_fault):
    profile = datasets.form_profiles["squat_v1"]
    sequence, _ = side_squat_sequence(reps=2, bottom_beta_deg=beta)
    findings, metrics = analyzed(sequence, profile, View.SIDE_LEFT)[0]
    depth = next(f for f in findings if f.fault_id == "insufficient_depth")
    assert (depth.status is FindingStatus.FAULT) is expect_fault
    assert depth.measured == pytest.approx(metrics["depth_ratio@bottom"])
    assert depth.threshold == 0.03
    if expect_fault:
        assert depth.cue and "hip crease" in depth.cue
        assert depth.frame is not None
    else:
        assert depth.cue is None


@pytest.mark.parametrize(("tau", "expect_fault"), [(35.0, False), (56.0, True)])
def test_fr9_squat_trunk_lean_fault(datasets, tau, expect_fault):
    profile = datasets.form_profiles["squat_v1"]
    sequence, _ = side_squat_sequence(reps=2, bottom_trunk_deg=tau)
    findings, _ = analyzed(sequence, profile, View.SIDE_LEFT)[0]
    lean = next(f for f in findings if f.fault_id == "excessive_trunk_lean")
    assert (lean.status is FindingStatus.FAULT) is expect_fault


def test_fr9_front_view_flags_valgus_and_leaves_side_rules_unassessed(datasets):
    profile = datasets.form_profiles["squat_v1"]
    sequence = front_squat_sequence(reps=2, knee_medial_offset=0.10)
    findings, metrics = analyzed(sequence, profile, View.FRONT)[0]
    by_id = {f.fault_id: f for f in findings}
    assert by_id["knee_valgus"].status is FindingStatus.FAULT
    assert metrics["fppa_deg@bottom"] == pytest.approx(FRONT_VALGUS_DEG, abs=1.0)
    for fault_id in ("insufficient_depth", "excessive_trunk_lean"):
        assert by_id[fault_id].status is FindingStatus.NOT_ASSESSED
        assert by_id[fault_id].not_assessed_reason is NotAssessedReason.VIEW_MISMATCH


def test_fr9_us7_side_view_never_guesses_the_front_view_rules(datasets):
    profile = datasets.form_profiles["squat_v1"]
    sequence, _ = side_squat_sequence(reps=2)
    findings, metrics = analyzed(sequence, profile, View.SIDE_LEFT)[0]
    by_id = {f.fault_id: f for f in findings}
    for fault_id in ("knee_valgus", "lateral_shift"):
        assert by_id[fault_id].status is FindingStatus.NOT_ASSESSED
        assert by_id[fault_id].not_assessed_reason is NotAssessedReason.VIEW_MISMATCH
        assert by_id[fault_id].measured is None
        assert by_id[fault_id].frame is None
    assert "fppa_deg@bottom" not in metrics


# ---------------------------------------------------------- deadlift rules


@pytest.mark.parametrize(("ratio", "expect_fault"), [(1.1, False), (2.2, True)])
def test_fr9_deadlift_hips_rise_early(datasets, ratio, expect_fault):
    profile = datasets.form_profiles["deadlift_v1"]
    sequence, truth = deadlift_sequence(reps=3, rise_ratio=ratio)
    findings, metrics = analyzed(sequence, profile, View.SIDE_RIGHT)[0]
    finding = next(f for f in findings if f.fault_id == "hips_rise_early")
    assert (finding.status is FindingStatus.FAULT) is expect_fault
    assert metrics["hip_shoulder_rise_ratio@ascent_early"] == pytest.approx(
        truth.hip_shoulder_rise_ratio, abs=0.03
    )


@pytest.mark.parametrize("drift", [0.28, -0.28])
def test_fr9_deadlift_bar_drift_is_absolute_in_either_direction(datasets, drift):
    profile = datasets.form_profiles["deadlift_v1"]
    sequence, _ = deadlift_sequence(reps=2, bar_drift=drift)
    findings, metrics = analyzed(sequence, profile, View.SIDE_RIGHT)[0]
    finding = next(f for f in findings if f.fault_id == "bar_drift")
    assert finding.status is FindingStatus.FAULT
    assert metrics["bar_drift_frac@whole_rep"] == pytest.approx(drift, abs=1e-3)


@pytest.mark.parametrize(("lockout", "expect_fault"), [(178.0, False), (162.0, True)])
def test_fr9_deadlift_lockout_uses_a_less_than_comparator(datasets, lockout, expect_fault):
    profile = datasets.form_profiles["deadlift_v1"]
    sequence, truth = deadlift_sequence(reps=2, lockout_deg=lockout)
    findings, metrics = analyzed(sequence, profile, View.SIDE_RIGHT)[0]
    finding = next(f for f in findings if f.fault_id == "incomplete_lockout")
    assert (finding.status is FindingStatus.FAULT) is expect_fault
    assert metrics["hip_ext_angle_deg@top"] == pytest.approx(truth.hip_ext_angle_deg, abs=0.5)


# ----------------------------------------------------------- push-up rules


def test_fr9_pushup_depth_and_lockout_are_measured_at_different_phases(datasets):
    profile = datasets.form_profiles["pushup_v1"]
    sequence, truth = pushup_sequence(reps=3, bottom_elbow_deg=112.0, top_elbow_deg=150.0)
    findings, metrics = analyzed(sequence, profile, View.SIDE_LEFT)[0]
    by_id = {f.fault_id: f for f in findings}
    assert by_id["insufficient_depth"].status is FindingStatus.FAULT
    assert by_id["incomplete_lockout"].status is FindingStatus.FAULT
    assert metrics["elbow_angle_deg@bottom"] == pytest.approx(truth.elbow_angle_bottom_deg, abs=0.5)
    assert metrics["elbow_angle_deg@top"] == pytest.approx(truth.elbow_angle_top_deg, abs=0.5)
    assert metrics["elbow_angle_deg@bottom"] != metrics["elbow_angle_deg@top"]


@pytest.mark.parametrize(("dev", "expect_fault"), [(0.0, False), (0.14, True), (-0.14, True)])
def test_fr9_pushup_hip_sag_or_pike_is_symmetric(datasets, dev, expect_fault):
    profile = datasets.form_profiles["pushup_v1"]
    sequence, _ = pushup_sequence(reps=2, hip_dev_frac=dev)
    findings, _ = analyzed(sequence, profile, View.SIDE_LEFT)[0]
    finding = next(f for f in findings if f.fault_id == "hip_sag_or_pike")
    assert (finding.status is FindingStatus.FAULT) is expect_fault


# ------------------------------------------------------------ not-assessed


def test_fr9_us7_occluded_feature_keypoints_are_not_assessed(datasets):
    """Losing the knees kills the depth rule but not the trunk-lean rule."""
    profile = datasets.form_profiles["squat_v1"]
    sequence, _ = side_squat_sequence(reps=2, frames_per_rep=60)
    degraded = occlude(sequence, ["left_knee", "right_knee"], fraction=1.0)
    findings, metrics = analyzed(degraded, profile, View.SIDE_LEFT)[0]
    by_id = {f.fault_id: f for f in findings}
    assert by_id["insufficient_depth"].status is FindingStatus.NOT_ASSESSED
    assert (
        by_id["insufficient_depth"].not_assessed_reason is NotAssessedReason.KEYPOINTS_NOT_VISIBLE
    )
    assert by_id["excessive_trunk_lean"].status is not FindingStatus.NOT_ASSESSED
    assert "depth_ratio@bottom" not in metrics
    assert "trunk_lean_deg@bottom" in metrics


def test_fr9_window_rule_survives_a_minority_of_dropped_frames(datasets):
    """One flickering frame must not silence a multi-frame rule (M7)."""
    profile = datasets.form_profiles["deadlift_v1"]
    sequence, truth = deadlift_sequence(reps=2, rise_ratio=2.0)
    boundaries = segment_reps(sequence, profile)
    measured = smooth_sequence(sequence, 9)
    frames = list(measured.frames)
    from formcoach.models import Keypoint, PoseFrame

    target = boundaries[0].start_frame + 3
    kps = dict(frames[target].keypoints)
    kps["left_hip"] = Keypoint(x=kps["left_hip"].x, y=kps["left_hip"].y, conf=0.05)
    frames[target] = PoseFrame(t_ms=frames[target].t_ms, keypoints=kps)
    patched = measured.model_copy(update={"frames": frames})

    findings, _ = evaluate_rep(
        patched, profile, boundaries[0], View.SIDE_RIGHT, facing_sign(frames)
    )
    finding = next(f for f in findings if f.fault_id == "hips_rise_early")
    assert finding.status is FindingStatus.FAULT
    assert finding.measured == pytest.approx(truth.hip_shoulder_rise_ratio, abs=0.05)


def test_fr9_window_rule_refuses_when_most_of_the_window_is_gone(datasets):
    profile = datasets.form_profiles["deadlift_v1"]
    sequence, _ = deadlift_sequence(reps=2)
    degraded = occlude(sequence, ["left_hip", "right_hip"], fraction=1.0)
    boundaries = segment_reps(sequence, profile)
    findings, _ = evaluate_rep(degraded, profile, boundaries[0], View.SIDE_RIGHT, 1.0)
    finding = next(f for f in findings if f.fault_id == "hips_rise_early")
    assert finding.status is FindingStatus.NOT_ASSESSED
    assert finding.not_assessed_reason is NotAssessedReason.KEYPOINTS_NOT_VISIBLE


def test_fr9_unknown_feature_names_are_a_loud_error(datasets):
    profile = datasets.form_profiles["squat_v1"]
    broken = profile.rules[0].model_copy(update={"feature": "vibes"})
    sequence, _ = side_squat_sequence(reps=1)
    rep = segment_reps(sequence, profile)[0]
    with pytest.raises(KeyError):
        measure(sequence, broken, rep, profile.direction, 1.0)
