"""FR-7 — view resolution and visibility screening.

US-7 lives here: the tool must refuse clips it cannot read *and* must not hide
behind "can't tell" on clips it can.  The 30 % screening rule is pinned from
both sides, and each of the three view-resolution steps is exercised.
"""

from __future__ import annotations

import pytest
from formcoach.engine.poseio import (
    FRONT_VIEW_RATIO,
    MAX_BAD_FRAME_FRAC,
    REJECT_INSUFFICIENT_VISIBILITY,
    resolve_handedness,
    resolve_view,
    screen,
    shoulder_width_ratio,
)
from formcoach.models import DeclaredView, Keypoint, PoseFrame, View
from formcoach_synthetic import (
    deadlift_sequence,
    front_squat_sequence,
    occlude,
    push_out_of_frame,
    pushup_sequence,
    side_squat_sequence,
)


@pytest.fixture
def squat_profile(datasets):
    return datasets.form_profiles["squat_v1"]


# ------------------------------------------------------ step 1: explicit wins


@pytest.mark.parametrize(
    "declared",
    [DeclaredView.FRONT, DeclaredView.SIDE_LEFT, DeclaredView.SIDE_RIGHT],
)
def test_fr7_explicit_view_wins_and_is_not_marked_inferred(declared):
    sequence, _ = side_squat_sequence(reps=2, declared_view=None)
    resolution = resolve_view(sequence, declared)
    assert resolution.view.value == declared.value
    assert resolution.view_inferred is False


def test_fr7_caller_declaration_overrides_the_sidecar():
    sequence, _ = side_squat_sequence(reps=2, declared_view=DeclaredView.SIDE_LEFT)
    assert resolve_view(sequence, DeclaredView.FRONT).view is View.FRONT


def test_fr7_sidecar_view_is_used_when_the_caller_declares_nothing():
    sequence, _ = side_squat_sequence(reps=2, declared_view=DeclaredView.SIDE_RIGHT)
    resolution = resolve_view(sequence, None)
    assert resolution.view is View.SIDE_RIGHT
    assert resolution.view_inferred is False


# --------------------------------------------- step 2: ambiguous "side" input


def _confidence_split(sequence, left: float, right: float):
    frames = []
    for frame in sequence.frames:
        kps = {}
        for name, kp in frame.keypoints.items():
            conf = (
                left
                if name.startswith("left_")
                else right
                if name.startswith("right_")
                else kp.conf
            )
            kps[name] = Keypoint(x=kp.x, y=kp.y, conf=conf)
        frames.append(PoseFrame(t_ms=frame.t_ms, keypoints=kps))
    return sequence.model_copy(update={"frames": frames})


def test_fr7_ambiguous_side_resolves_to_the_more_confident_side():
    sequence, _ = side_squat_sequence(reps=2, declared_view=None)
    right_heavy = _confidence_split(sequence, left=0.55, right=0.95)
    left_heavy = _confidence_split(sequence, left=0.95, right=0.55)
    assert resolve_view(right_heavy, DeclaredView.SIDE).view is View.SIDE_RIGHT
    assert resolve_view(left_heavy, DeclaredView.SIDE).view is View.SIDE_LEFT


def test_fr7_ambiguous_side_is_marked_inferred():
    sequence, _ = side_squat_sequence(reps=2, declared_view=None)
    assert resolve_view(sequence, DeclaredView.SIDE).view_inferred is True


def test_fr7_exact_handedness_tie_goes_to_side_left():
    sequence, _ = side_squat_sequence(reps=2, declared_view=None)
    balanced = _confidence_split(sequence, left=0.8, right=0.8)
    assert resolve_handedness(balanced.frames) is View.SIDE_LEFT


# ------------------------------------------------- step 3: nothing declared


def test_fr7_undeclared_front_clip_is_inferred_from_shoulder_width():
    sequence = front_squat_sequence(reps=2, declared_view=None)
    ratio = shoulder_width_ratio(sequence.frames)
    assert ratio is not None and ratio > FRONT_VIEW_RATIO
    resolution = resolve_view(sequence, None)
    assert resolution.view is View.FRONT
    assert resolution.view_inferred is True


@pytest.mark.parametrize("builder", [side_squat_sequence, deadlift_sequence, pushup_sequence])
def test_fr7_undeclared_side_clips_are_inferred_as_a_side_view(builder):
    sequence, _ = builder(reps=2, declared_view=None)
    ratio = shoulder_width_ratio(sequence.frames)
    assert ratio is not None and ratio <= FRONT_VIEW_RATIO
    resolution = resolve_view(sequence, None)
    assert resolution.view in (View.SIDE_LEFT, View.SIDE_RIGHT)
    assert resolution.view_inferred is True


def _ratio_clip(ratio: float):
    torso = 0.30
    width = ratio * torso
    frames = [
        PoseFrame(
            t_ms=i * 33.0,
            keypoints={
                "left_shoulder": Keypoint(x=0.5 - width / 2, y=0.30, conf=0.9),
                "right_shoulder": Keypoint(x=0.5 + width / 2, y=0.30, conf=0.9),
                "left_hip": Keypoint(x=0.5, y=0.30 + torso, conf=0.9),
                "right_hip": Keypoint(x=0.5, y=0.30 + torso, conf=0.9),
            },
        )
        for i in range(10)
    ]
    sequence, _ = side_squat_sequence(reps=1, declared_view=None)
    return sequence.model_copy(update={"frames": frames})


@pytest.mark.parametrize(
    ("ratio", "expected_front"),
    [(0.30, False), (0.44, False), (0.46, True), (0.70, True)],
)
def test_fr7_front_test_is_the_shoulder_width_over_torso_ratio(ratio, expected_front):
    clip = _ratio_clip(ratio)
    assert shoulder_width_ratio(clip.frames) == pytest.approx(ratio)
    assert (resolve_view(clip, None).view is View.FRONT) is expected_front


# ------------------------------------------------------------- FR-7 screening


def test_fr7_clean_clip_is_accepted_with_every_frame_valid(squat_profile):
    sequence, _ = side_squat_sequence(reps=3)
    result = screen(sequence, squat_profile, View.SIDE_LEFT)
    assert result.accepted
    assert result.reject_reason is None
    assert result.frames_valid == result.frames_total


def test_fr7_us7_occlusion_above_30_percent_is_rejected(squat_profile):
    sequence, _ = side_squat_sequence(reps=3)
    degraded = occlude(sequence, ["left_knee"], fraction=0.32)
    result = screen(degraded, squat_profile, View.SIDE_LEFT)
    assert not result.accepted
    assert result.reject_reason == REJECT_INSUFFICIENT_VISIBILITY
    assert result.low_confidence_frac > MAX_BAD_FRAME_FRAC


def test_fr7_us7_occlusion_below_30_percent_is_accepted(squat_profile):
    """The 0.28 boundary clip must be read, not refused."""
    sequence, _ = side_squat_sequence(reps=3)
    degraded = occlude(sequence, ["left_knee"], fraction=0.28)
    result = screen(degraded, squat_profile, View.SIDE_LEFT)
    assert result.accepted
    assert result.low_confidence_frac == pytest.approx(0.28, abs=0.01)


def test_fr7_partial_body_out_of_frame_is_its_own_rejection_path(squat_profile):
    sequence, _ = side_squat_sequence(reps=3)
    degraded = push_out_of_frame(sequence, ["left_ankle", "right_ankle"], fraction=0.4)
    result = screen(degraded, squat_profile, View.SIDE_LEFT)
    assert not result.accepted
    assert result.reject_reason == REJECT_INSUFFICIENT_VISIBILITY
    assert result.out_of_frame_frac > MAX_BAD_FRAME_FRAC
    assert result.low_confidence_frac == 0.0


def test_fr7_screening_only_looks_at_the_resolved_view_requirements(squat_profile):
    """Wrists are not required for a squat, so losing them changes nothing."""
    sequence, _ = side_squat_sequence(reps=3)
    degraded = occlude(sequence, ["left_wrist", "right_wrist"], fraction=1.0)
    assert screen(degraded, squat_profile, View.SIDE_LEFT).accepted


def test_fr7_screening_counts_a_missing_keypoint_as_both_failures(squat_profile):
    sequence, _ = side_squat_sequence(reps=1)
    stripped = sequence.model_copy(
        update={
            "frames": [
                PoseFrame(
                    t_ms=f.t_ms,
                    keypoints={k: v for k, v in f.keypoints.items() if k != "left_hip"},
                )
                for f in sequence.frames
            ]
        }
    )
    result = screen(stripped, squat_profile, View.SIDE_LEFT)
    assert not result.accepted
    assert result.low_confidence_frac == 1.0
    assert result.out_of_frame_frac == 1.0
    assert result.frames_valid == 0
