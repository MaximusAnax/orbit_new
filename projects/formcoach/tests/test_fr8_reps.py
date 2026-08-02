"""FR-8 — signal conditioning and rep segmentation.

The gates that depend on this are M2 (rep-count accuracy) and, through rep
alignment, M1 and M3.  The cases below are the ones the fixture corpus is built
to stress: varying fps, high-confidence teleport frames, camera drift and
sub-threshold bobs that must *not* be counted as reps.
"""

from __future__ import annotations

import pytest
from formcoach.engine.reps import (
    extract_signal,
    median3,
    moving_average,
    peak_prominence,
    savgol,
    savgol_coefficients,
    segment_reps,
    smooth_sequence,
    smoothing_window,
)
from formcoach.models import Keypoint, PoseFrame, RepDirection
from formcoach_synthetic import (
    deadlift_sequence,
    pushup_sequence,
    raised_cosine,
    side_squat_frame,
    side_squat_sequence,
)


@pytest.fixture
def squat_profile(datasets):
    return datasets.form_profiles["squat_v1"]


# ------------------------------------------------------------------- filters


@pytest.mark.parametrize(
    ("fps", "frac", "expected"),
    [(30.0, 0.25, 9), (24.0, 0.25, 7), (60.0, 0.25, 15), (10.0, 0.05, 3)],
)
def test_fr8_smoothing_window_is_derived_from_the_clips_own_fps(fps, frac, expected):
    window = smoothing_window(fps, frac)
    assert window == expected
    assert window % 2 == 1  # centred means an odd window


def test_fr8_median_filter_removes_a_single_frame_teleport():
    values = [0.5, 0.5, 0.5, 0.62, 0.5, 0.5]
    assert median3(values) == [0.5] * 6


def test_fr8_moving_average_preserves_a_constant_signal():
    assert moving_average([0.4] * 10, 5) == pytest.approx([0.4] * 10)


def test_fr8_savgol_coefficients_are_the_classic_kernels():
    assert savgol_coefficients(5) == pytest.approx([-3 / 35, 12 / 35, 17 / 35, 12 / 35, -3 / 35])
    assert sum(savgol_coefficients(9)) == pytest.approx(1.0)


def test_fr8_savgol_reproduces_a_parabola_exactly():
    """This is why measurement smoothing is SG and not a moving average."""
    xs = [i / 50 for i in range(-25, 26)]
    ys = [0.5 - 2.0 * x * x for x in xs]
    smoothed = savgol(ys, 9)
    assert smoothed[25] == pytest.approx(ys[25], abs=1e-9)
    assert moving_average(ys, 9)[25] != pytest.approx(ys[25], abs=1e-6)


def test_fr8_peak_prominence_matches_the_topographic_definition():
    values = [0.0, 1.0, 0.4, 0.9, 0.0]
    assert peak_prominence(values, 1) == pytest.approx(1.0)
    assert peak_prominence(values, 3) == pytest.approx(0.5)


def test_fr8_signal_extraction_fills_gaps_rather_than_dropping_frames():
    frames = [
        PoseFrame(
            t_ms=0.0,
            keypoints={
                "left_hip": Keypoint(x=0.5, y=0.4, conf=0.9),
                "right_hip": Keypoint(x=0.5, y=0.4, conf=0.9),
            },
        ),
        PoseFrame(t_ms=33.0, keypoints={}),
        PoseFrame(
            t_ms=66.0,
            keypoints={
                "left_hip": Keypoint(x=0.5, y=0.6, conf=0.9),
                "right_hip": Keypoint(x=0.5, y=0.6, conf=0.9),
            },
        ),
    ]
    sequence, _ = side_squat_sequence(reps=1)
    sequence = sequence.model_copy(update={"frames": frames})
    assert extract_signal(sequence, "mid_hip.y") == pytest.approx([0.4, 0.5, 0.6])


def test_fr8_smoothing_never_touches_reported_confidence():
    sequence, _ = side_squat_sequence(reps=2, noise_sigma=0.004, seed=3)
    smoothed = smooth_sequence(sequence, 9)
    for raw, clean in zip(sequence.frames, smoothed.frames, strict=True):
        for name, kp in raw.keypoints.items():
            assert clean.keypoints[name].conf == kp.conf


# --------------------------------------------------------------- segmentation


@pytest.mark.parametrize("reps", [1, 3, 5, 8])
def test_fr8_counts_clean_squat_reps(reps, squat_profile):
    sequence, truth = side_squat_sequence(reps=reps)
    found = segment_reps(sequence, squat_profile)
    assert len(found) == truth.reps


@pytest.mark.parametrize("fps", [24.0, 30.0, 60.0])
def test_fr8_rep_count_is_stable_across_frame_rates(fps, squat_profile):
    frames_per_rep = round(fps * 2)
    sequence, truth = side_squat_sequence(reps=4, fps=fps, frames_per_rep=frames_per_rep)
    assert len(segment_reps(sequence, squat_profile)) == truth.reps


def test_fr8_boundaries_are_strictly_increasing_and_bracket_the_bottom(squat_profile):
    sequence, truth = side_squat_sequence(reps=3, frames_per_rep=60)
    reps = segment_reps(sequence, squat_profile)
    for rep, expected in zip(reps, truth.extremum_frames, strict=True):
        assert rep.start_frame < rep.extremum_frame < rep.end_frame
        assert abs(rep.extremum_frame - expected) <= 2
    assert [r.rep_index for r in reps] == [0, 1, 2]


def test_fr8_deadlift_extremum_is_the_lockout_not_the_floor(datasets):
    profile = datasets.form_profiles["deadlift_v1"]
    assert profile.direction is RepDirection.UP_DOWN
    sequence, truth = deadlift_sequence(reps=3, frames_per_rep=60)
    reps = segment_reps(sequence, profile)
    assert len(reps) == truth.reps
    signal = extract_signal(sequence, profile.primary_signal)
    assert signal is not None
    for rep in reps:
        # the extremum is where the hip is highest, i.e. smallest y
        assert signal[rep.extremum_frame] < signal[rep.start_frame]
        assert signal[rep.extremum_frame] < signal[rep.end_frame]


def test_fr8_pushup_uses_the_shoulder_signal(datasets):
    profile = datasets.form_profiles["pushup_v1"]
    assert profile.primary_signal == "mid_shoulder.y"
    sequence, truth = pushup_sequence(reps=4)
    assert len(segment_reps(sequence, profile)) == truth.reps


def test_fr8_small_bobs_below_the_prominence_floor_are_not_reps(squat_profile):
    """A 5 % wobble between reps must not be counted as a repetition."""
    sequence, truth = side_squat_sequence(reps=2, frames_per_rep=60)
    frames = list(sequence.frames)
    # insert a shallow dip that is far below 15 % of the signal range
    for i in range(120, 130):
        weight = raised_cosine((i - 120) / 10)
        beta = 5.0 + 6.0 * weight
        frames.insert(i, PoseFrame(t_ms=i * 33.0, keypoints=side_squat_frame(beta, 5.0, 1.0)))
    noisy = sequence.model_copy(update={"frames": frames})
    assert len(segment_reps(noisy, squat_profile)) == truth.reps


def test_fr8_two_bottoms_closer_than_the_minimum_duration_count_once(squat_profile):
    """A double-bounce inside 0.8 s is one rep, not two."""
    frames = []
    for i in range(180):
        phase = i / 60
        weight = raised_cosine(phase % 1.0)
        if 28 <= i <= 34:  # a brief bounce out of and back into the hole
            weight = 0.86
        frames.append(
            PoseFrame(
                t_ms=i * 1000 / 30,
                keypoints=side_squat_frame(5 + 95 * weight, 5 + 30 * weight, 20 * weight),
            )
        )
    sequence, _ = side_squat_sequence(reps=1)
    sequence = sequence.model_copy(update={"frames": frames})
    reps = segment_reps(sequence, squat_profile)
    assert len(reps) == 3


def test_fr8_high_confidence_teleport_frames_do_not_create_reps(squat_profile):
    sequence, truth = side_squat_sequence(reps=3, frames_per_rep=60)
    frames = []
    for index, frame in enumerate(sequence.frames):
        if index % 37 == 5:  # ~1 in 37 frames teleports at full confidence
            kps = dict(frame.keypoints)
            hip = kps["left_hip"]
            kps["left_hip"] = Keypoint(x=hip.x, y=hip.y + 0.12, conf=0.9)
            frames.append(PoseFrame(t_ms=frame.t_ms, keypoints=kps))
        else:
            frames.append(frame)
    spiked = sequence.model_copy(update={"frames": frames})
    assert len(segment_reps(spiked, squat_profile)) == truth.reps


def test_fr8_slow_camera_drift_does_not_change_the_rep_count(squat_profile):
    sequence, truth = side_squat_sequence(reps=4, frames_per_rep=60)
    total = len(sequence.frames)
    frames = []
    for index, frame in enumerate(sequence.frames):
        shift = 0.03 * index / total
        kps = {
            name: Keypoint(x=kp.x + shift, y=kp.y + shift, conf=kp.conf)
            for name, kp in frame.keypoints.items()
        }
        frames.append(PoseFrame(t_ms=frame.t_ms, keypoints=kps))
    drifted = sequence.model_copy(update={"frames": frames})
    assert len(segment_reps(drifted, squat_profile)) == truth.reps


def test_fr8_a_still_clip_has_no_reps(squat_profile):
    frames = [
        PoseFrame(t_ms=i * 33.0, keypoints=side_squat_frame(5.0, 5.0, 0.0)) for i in range(90)
    ]
    sequence, _ = side_squat_sequence(reps=1)
    assert segment_reps(sequence.model_copy(update={"frames": frames}), squat_profile) == []


def test_fr8_segmentation_is_deterministic(squat_profile):
    sequence, _ = side_squat_sequence(reps=4, noise_sigma=0.005, seed=11)
    assert segment_reps(sequence, squat_profile) == segment_reps(sequence, squat_profile)
