"""Geometric primitives and the nine named form features.

These back FR-9 and FR-10: if the geometry is wrong, every fault finding and
every measured value downstream is wrong in the same direction, which is
exactly the failure mode M3 exists to catch.  Values here are asserted against
closed-form expectations, not against the engine's own output.
"""

from __future__ import annotations

import math

import pytest
from formcoach.engine.geometry import (
    FEATURES,
    FeatureContext,
    angle_at,
    angle_from_vertical,
    confidence,
    depth_ratio,
    elbow_angle_deg,
    expand_keypoints,
    facing_sign,
    fppa_deg,
    hip_dev_frac,
    hip_ext_angle_deg,
    lateral_shift_frac,
    least_squares_slope,
    point,
    signal_value,
    signed_perpendicular,
    trunk_lean_deg,
)
from formcoach.models import Keypoint, PoseFrame
from formcoach_synthetic import (
    FRONT_VALGUS_DEG,
    front_squat_frame,
    pushup_frame,
    side_squat_frame,
)

CTX = FeatureContext(facing=1.0)


def frame_of(keypoints) -> PoseFrame:
    return PoseFrame(t_ms=0.0, keypoints=keypoints)


# ----------------------------------------------------------------- primitives


def test_angle_at_right_angle_and_straight_line():
    assert angle_at((0.0, 1.0), (0.0, 0.0), (1.0, 0.0)) == pytest.approx(90.0)
    assert angle_at((0.0, 1.0), (0.0, 0.0), (0.0, -1.0)) == pytest.approx(180.0)
    assert angle_at((0.0, 0.0), (0.0, 0.0), (1.0, 0.0)) is None


def test_angle_from_vertical_uses_image_axes():
    assert angle_from_vertical((0.5, 0.2), (0.5, 0.8)) == pytest.approx(0.0)
    assert angle_from_vertical((0.5, 0.2), (0.9, 0.6)) == pytest.approx(45.0)
    assert angle_from_vertical((0.5, 0.2), (0.1, 0.6)) == pytest.approx(45.0)


def test_signed_perpendicular_is_positive_below_the_line():
    below = signed_perpendicular((0.5, 0.6), (0.0, 0.5), (1.0, 0.5))
    above = signed_perpendicular((0.5, 0.4), (0.0, 0.5), (1.0, 0.5))
    assert below == pytest.approx(0.1)
    assert above == pytest.approx(-0.1)
    # Reversing the line's direction must not flip the meaning of the sign.
    assert signed_perpendicular((0.5, 0.6), (1.0, 0.5), (0.0, 0.5)) == pytest.approx(0.1)


def test_least_squares_slope_recovers_an_exact_line():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [1.0, 3.0, 5.0, 7.0]
    assert least_squares_slope(xs, ys) == pytest.approx(2.0)
    assert least_squares_slope([1.0, 1.0], [3.0, 4.0]) is None


def test_midpoints_expand_to_the_real_keypoints_they_need():
    assert expand_keypoints(["mid_hip"]) == ["left_hip", "right_hip"]
    assert expand_keypoints(["mid_hip", "left_hip"]) == ["left_hip", "right_hip"]
    assert expand_keypoints(["nose"]) == ["nose"]


def test_midpoint_confidence_is_the_weaker_of_the_pair():
    frame = frame_of(
        {
            "left_hip": Keypoint(x=0.4, y=0.5, conf=0.9),
            "right_hip": Keypoint(x=0.6, y=0.5, conf=0.2),
        }
    )
    assert confidence(frame, "mid_hip") == pytest.approx(0.2)
    assert point(frame, "mid_hip") == (pytest.approx(0.5), pytest.approx(0.5))
    assert point(frame, "mid_knee") is None


def test_signal_expression_reads_the_named_axis():
    frame = frame_of(
        {
            "left_hip": Keypoint(x=0.4, y=0.5, conf=0.9),
            "right_hip": Keypoint(x=0.6, y=0.7, conf=0.9),
        }
    )
    assert signal_value(frame, "mid_hip.y") == pytest.approx(0.6)
    assert signal_value(frame, "mid_hip.x") == pytest.approx(0.5)
    with pytest.raises(ValueError):
        signal_value(frame, "mid_hip.z")


# ------------------------------------------------------------- facing / frame


@pytest.mark.parametrize("facing", [1.0, -1.0])
def test_fr9_facing_sign_follows_the_nose(facing):
    frame = frame_of(side_squat_frame(90.0, 20.0, 10.0, facing=facing))
    assert facing_sign([frame]) == pytest.approx(facing)


def test_fr9_facing_defaults_to_plus_one_when_the_nose_is_invisible():
    frame = frame_of(
        {
            "left_hip": Keypoint(x=0.5, y=0.5, conf=0.9),
            "right_hip": Keypoint(x=0.5, y=0.5, conf=0.9),
            "nose": Keypoint(x=0.1, y=0.2, conf=0.05),
        }
    )
    assert facing_sign([frame]) == 1.0


# ---------------------------------------------------------------- squat side


@pytest.mark.parametrize("beta", [70.0, 88.0, 90.0, 92.0, 110.0])
def test_fr9_depth_ratio_equals_cosine_of_the_femur_angle(beta):
    frame = frame_of(side_squat_frame(beta, 20.0, 10.0))
    assert depth_ratio(frame, CTX) == pytest.approx(math.cos(math.radians(beta)), abs=1e-9)


def test_fr9_depth_ratio_sign_means_hip_above_knee():
    shallow = frame_of(side_squat_frame(70.0, 20.0, 10.0))
    deep = frame_of(side_squat_frame(110.0, 20.0, 10.0))
    assert depth_ratio(shallow, CTX) > 0  # hip still above the knee: too shallow
    assert depth_ratio(deep, CTX) < 0


@pytest.mark.parametrize("tau", [0.0, 15.0, 35.0, 60.0])
def test_fr9_trunk_lean_equals_the_injected_angle(tau):
    frame = frame_of(side_squat_frame(95.0, tau, 10.0))
    assert trunk_lean_deg(frame, CTX) == pytest.approx(tau, abs=1e-9)


@pytest.mark.parametrize("facing", [1.0, -1.0])
def test_fr9_trunk_lean_is_view_independent(facing):
    frame = frame_of(side_squat_frame(95.0, 35.0, 10.0, facing=facing))
    assert trunk_lean_deg(frame, CTX) == pytest.approx(35.0, abs=1e-9)


# --------------------------------------------------------------- squat front


def test_fr9_fppa_is_zero_for_a_straight_leg():
    frame = frame_of(front_squat_frame(0.0))
    assert fppa_deg(frame, CTX) == pytest.approx(0.0, abs=1e-9)


def test_fr9_fppa_is_positive_when_the_knees_cave_inward():
    frame = frame_of(front_squat_frame(0.10))
    assert fppa_deg(frame, CTX) == pytest.approx(FRONT_VALGUS_DEG, abs=1e-9)
    assert pytest.approx(53.13010, abs=1e-4) == FRONT_VALGUS_DEG


def test_fr9_fppa_is_negative_when_the_knees_track_outside():
    frame = frame_of(front_squat_frame(-0.10))
    assert fppa_deg(frame, CTX) == pytest.approx(-FRONT_VALGUS_DEG, abs=1e-9)


def test_fr9_lateral_shift_is_hip_offset_over_hip_width():
    base = front_squat_frame(0.0)
    shifted = dict(base)
    for name in ("left_hip", "right_hip"):
        kp = base[name]
        shifted[name] = Keypoint(x=kp.x + 0.02, y=kp.y, conf=kp.conf)
    frame = frame_of(shifted)
    # hips are 0.10 apart, so a 0.02 shift is 0.20 of hip width
    assert lateral_shift_frac(frame, CTX) == pytest.approx(0.20, abs=1e-9)
    assert lateral_shift_frac(frame_of(base), CTX) == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------- push-up


@pytest.mark.parametrize("angle", [75.0, 100.0, 160.0, 178.0])
def test_fr9_elbow_angle_equals_the_injected_angle(angle):
    frame = frame_of(pushup_frame(angle, 0.0))
    assert elbow_angle_deg(frame, CTX) == pytest.approx(angle, abs=1e-6)


@pytest.mark.parametrize("dev", [-0.20, -0.05, 0.0, 0.05, 0.20])
def test_fr9_hip_dev_frac_equals_the_injected_offset(dev):
    frame = frame_of(pushup_frame(120.0, dev))
    assert hip_dev_frac(frame, CTX) == pytest.approx(dev, abs=1e-9)


def test_fr9_hip_dev_positive_is_a_sag_toward_larger_y():
    sag = frame_of(pushup_frame(120.0, 0.15))
    pike = frame_of(pushup_frame(120.0, -0.15))
    sag_hip = point(sag, "mid_hip")
    pike_hip = point(pike, "mid_hip")
    assert sag_hip is not None and pike_hip is not None
    assert sag_hip[1] > pike_hip[1]


# ------------------------------------------------------------------ deadlift


def test_fr9_hip_extension_angle_is_180_when_shoulder_hip_knee_are_in_line():
    frame = frame_of(
        {
            "left_shoulder": Keypoint(x=0.5, y=0.2, conf=0.9),
            "right_shoulder": Keypoint(x=0.5, y=0.2, conf=0.9),
            "left_hip": Keypoint(x=0.5, y=0.5, conf=0.9),
            "right_hip": Keypoint(x=0.5, y=0.5, conf=0.9),
            "left_knee": Keypoint(x=0.5, y=0.7, conf=0.9),
            "right_knee": Keypoint(x=0.5, y=0.7, conf=0.9),
        }
    )
    assert hip_ext_angle_deg(frame, CTX) == pytest.approx(180.0)


def test_fr9_hip_extension_angle_drops_as_the_torso_stays_folded():
    frame = frame_of(
        {
            "left_shoulder": Keypoint(x=0.65, y=0.35, conf=0.9),
            "right_shoulder": Keypoint(x=0.65, y=0.35, conf=0.9),
            "left_hip": Keypoint(x=0.5, y=0.5, conf=0.9),
            "right_hip": Keypoint(x=0.5, y=0.5, conf=0.9),
            "left_knee": Keypoint(x=0.5, y=0.7, conf=0.9),
            "right_knee": Keypoint(x=0.5, y=0.7, conf=0.9),
        }
    )
    assert hip_ext_angle_deg(frame, CTX) == pytest.approx(135.0)


@pytest.mark.parametrize("facing", [1.0, -1.0])
def test_fr9_bar_drift_is_reported_in_the_canonical_anterior_frame(facing):
    """A bar 25 % of a shank ahead of mid-foot reads +0.25 in either side view."""
    spec = FEATURES["bar_drift_frac"]
    assert spec.window_fn is not None
    shank = 0.20
    frame = frame_of(
        {
            "left_ankle": Keypoint(x=0.50, y=0.90, conf=0.9),
            "right_ankle": Keypoint(x=0.50, y=0.90, conf=0.9),
            "left_knee": Keypoint(x=0.50, y=0.90 - shank, conf=0.9),
            "right_knee": Keypoint(x=0.50, y=0.90 - shank, conf=0.9),
            "left_wrist": Keypoint(x=0.50 + 0.25 * shank * facing, y=0.70, conf=0.9),
            "right_wrist": Keypoint(x=0.50 + 0.25 * shank * facing, y=0.70, conf=0.9),
        }
    )
    result = spec.window_fn([frame], FeatureContext(facing=facing))
    assert result is not None
    assert result[0] == pytest.approx(0.25, abs=1e-9)


def test_fr9_bar_drift_keeps_the_largest_magnitude_over_the_rep():
    spec = FEATURES["bar_drift_frac"]
    assert spec.window_fn is not None
    shank = 0.20

    def at(drift: float) -> PoseFrame:
        return frame_of(
            {
                "left_ankle": Keypoint(x=0.50, y=0.90, conf=0.9),
                "right_ankle": Keypoint(x=0.50, y=0.90, conf=0.9),
                "left_knee": Keypoint(x=0.50, y=0.70, conf=0.9),
                "right_knee": Keypoint(x=0.50, y=0.70, conf=0.9),
                "left_wrist": Keypoint(x=0.50 + drift * shank, y=0.70, conf=0.9),
                "right_wrist": Keypoint(x=0.50 + drift * shank, y=0.70, conf=0.9),
            }
        )

    result = spec.window_fn([at(0.05), at(-0.31), at(0.12)], CTX)
    assert result is not None
    assert result[0] == pytest.approx(-0.31, abs=1e-9)
    assert result[1] == 1


def test_fr9_every_catalog_feature_is_registered():
    expected = {
        "depth_ratio",
        "trunk_lean_deg",
        "fppa_deg",
        "lateral_shift_frac",
        "hip_shoulder_rise_ratio",
        "bar_drift_frac",
        "hip_ext_angle_deg",
        "elbow_angle_deg",
        "hip_dev_frac",
    }
    assert set(FEATURES) == expected


def test_fr9_features_return_none_rather_than_guessing_on_missing_joints():
    bare = frame_of({"nose": Keypoint(x=0.5, y=0.2, conf=0.9)})
    assert depth_ratio(bare, CTX) is None
    assert trunk_lean_deg(bare, CTX) is None
    assert fppa_deg(bare, CTX) is None
    assert lateral_shift_frac(bare, CTX) is None
    assert elbow_angle_deg(bare, CTX) is None
    assert hip_dev_frac(bare, CTX) is None
    assert hip_ext_angle_deg(bare, CTX) is None
