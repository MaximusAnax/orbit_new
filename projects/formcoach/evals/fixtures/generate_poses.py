#!/usr/bin/env python3
"""Seeded generator for the FormCoach pose-fixture corpus (EVALS.md § Fixture strategy).

Run with::

    python evals/fixtures/generate_poses.py --seed 20260731

It writes ``poses/*.keypoints.json``, ``labels.json`` and ``labels_handcheck.json``
next to itself.  Everything is deterministic given the seed and every float is
rounded to six decimals at generation time, so a regeneration diff is a
parsed-value comparison with tolerance 1e-9 rather than a byte comparison.

**Generator independence (hard rule).**  This module must not import anything
from ``src/formcoach`` — ``evals/test_gates.py::test_generator_does_not_import_the_engine``
scans its imports and fails if it ever does.  Ground-truth feature values are
computed *analytically from the injection parameters*: the skeletons are built
so that the quantity under test is a construction parameter.

    depth_ratio        == cos(femur angle from vertical) at the bottom frame
    trunk_lean_deg     == the prescribed trunk pitch at the bottom frame
    fppa_deg           == the apex angle of an isoceles hip-knee-ankle triangle
    lateral_shift_frac == the prescribed mid-hip offset in hip-width units
    hip_shoulder_rise  == the prescribed slope of an exactly affine hip/shoulder
                          relation that holds over the whole early ascent
    bar_drift_frac     == the peak of the prescribed wrist-offset profile
    hip_ext_angle_deg  == 180 - (femur pitch + trunk pitch) at lockout
    elbow_angle_deg    == the prescribed elbow angle (the arm is built from it)
    hip_dev_frac       == sin(the prescribed hip offset angle off the
                          shoulder-ankle line)

Labels are computed *before* noise, so the AR(1) jitter, teleport frames,
perspective scaling and camera drift below never move ground truth.

``labels_handcheck.json`` is the definitional cross-check: for eight committed
reps it stores feature values recomputed from the **committed (noisy)**
keypoints by this file's own local geometry implementation
(``handcheck_features``), which is written independently of ``engine/geometry.py``
(law of cosines rather than dot products, explicit sign reasoning).  A shared
sign or normalization error between generator and engine therefore fails the
hand-check even when the two agree with each other.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- constants

DEFAULT_SEED = 20260731

COCO_NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)

#: de Leva (1996) segment lengths as fractions of stature.
SEG = {
    "shank": 0.246,
    "femur": 0.245,
    "trunk": 0.288,
    "upper_arm": 0.186,
    "forearm": 0.146,
    "head": 0.130,
    "shoulder_width": 0.220,
    "hip_width": 0.180,
    "stance": 0.110,
}

#: Three body sizes (stature in normalized image units).
HEIGHTS = (0.82, 0.87, 0.92)

FLOOR_Y = 0.940
AR_RHO = 0.6
MIN_CONF = 0.3
CONF_NEAR = 0.93
CONF_FAR = 0.64
CONF_FRONT = 0.90
CONF_DIP_PROB = 0.02
CONF_OCCLUDED = 0.10
TELEPORT_PROB = 0.01
TELEPORT_CONF = 0.86
LATERAL = 0.006

#: Rules per exercise, mirroring the committed form profiles.
RULES = {
    "squat": (
        ("insufficient_depth", "depth_ratio", "bottom", ("side_left", "side_right")),
        ("excessive_trunk_lean", "trunk_lean_deg", "bottom", ("side_left", "side_right")),
        ("knee_valgus", "fppa_deg", "bottom", ("front",)),
        ("lateral_shift", "lateral_shift_frac", "bottom", ("front",)),
    ),
    "deadlift": (
        ("hips_rise_early", "hip_shoulder_rise_ratio", "ascent_early", ("side_left", "side_right")),
        ("bar_drift", "bar_drift_frac", "whole_rep", ("side_left", "side_right")),
        ("incomplete_lockout", "hip_ext_angle_deg", "top", ("side_left", "side_right")),
    ),
    "pushup": (
        ("insufficient_depth", "elbow_angle_deg", "bottom", ("side_left", "side_right")),
        ("hip_sag_or_pike", "hip_dev_frac", "bottom", ("side_left", "side_right")),
        ("incomplete_lockout", "elbow_angle_deg", "top", ("side_left", "side_right")),
    ),
}

#: The keypoints each rule's feature needs, mirroring the committed profiles.
FEATURE_KEYPOINTS = {
    ("squat", "insufficient_depth"): {"left_hip", "right_hip", "left_knee", "right_knee"},
    ("squat", "excessive_trunk_lean"): {
        "left_shoulder", "right_shoulder", "left_hip", "right_hip",
    },
    ("squat", "knee_valgus"): {
        "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
    },
    ("squat", "lateral_shift"): {"left_hip", "right_hip", "left_ankle", "right_ankle"},
    ("deadlift", "hips_rise_early"): {
        "left_hip", "right_hip", "left_shoulder", "right_shoulder",
    },
    ("deadlift", "bar_drift"): {
        "left_wrist", "right_wrist", "left_knee", "right_knee", "left_ankle", "right_ankle",
    },
    ("deadlift", "incomplete_lockout"): {
        "left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_knee", "right_knee",
    },
    ("pushup", "insufficient_depth"): {
        "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist",
    },
    ("pushup", "hip_sag_or_pike"): {
        "left_shoulder", "right_shoulder", "left_hip", "right_hip",
        "left_ankle", "right_ankle",
    },
    ("pushup", "incomplete_lockout"): {
        "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist",
    },
}

THRESHOLDS = {
    ("squat", "insufficient_depth"): (0.03, "gt"),
    ("squat", "excessive_trunk_lean"): (55.0, "gt"),
    ("squat", "knee_valgus"): (12.0, "gt"),
    ("squat", "lateral_shift"): (0.15, "gt"),
    ("deadlift", "hips_rise_early"): (1.5, "gt"),
    ("deadlift", "bar_drift"): (0.20, "abs_gt"),
    ("deadlift", "incomplete_lockout"): (170.0, "lt"),
    ("pushup", "insufficient_depth"): (100.0, "gt"),
    ("pushup", "hip_sag_or_pike"): (0.08, "abs_gt"),
    ("pushup", "incomplete_lockout"): (160.0, "lt"),
}

EXERCISE_IDS = {
    "squat": "barbell-back-squat",
    "deadlift": "barbell-deadlift",
    "pushup": "push-up",
}
PROFILE_IDS = {"squat": "squat_v1", "deadlift": "deadlift_v1", "pushup": "pushup_v1"}

#: Keypoints the visibility screen requires, per exercise and view family.
REQUIRED = {
    ("squat", "side"): (
        "left_shoulder",
        "right_shoulder",
        "left_hip",
        "right_hip",
        "left_knee",
        "right_knee",
        "left_ankle",
        "right_ankle",
    ),
    ("squat", "front"): (
        "left_shoulder",
        "right_shoulder",
        "left_hip",
        "right_hip",
        "left_knee",
        "right_knee",
        "left_ankle",
        "right_ankle",
    ),
    ("deadlift", "side"): (
        "left_shoulder",
        "right_shoulder",
        "left_hip",
        "right_hip",
        "left_knee",
        "right_knee",
        "left_ankle",
        "right_ankle",
        "left_wrist",
        "right_wrist",
    ),
    ("pushup", "side"): (
        "left_shoulder",
        "right_shoulder",
        "left_elbow",
        "right_elbow",
        "left_wrist",
        "right_wrist",
        "left_hip",
        "right_hip",
        "left_ankle",
        "right_ankle",
    ),
}


def label_for(exercise: str, fault_id: str, value: float) -> str:
    threshold, comparator = THRESHOLDS[(exercise, fault_id)]
    if comparator == "gt":
        return "fault" if value > threshold else "ok"
    if comparator == "lt":
        return "fault" if value < threshold else "ok"
    return "fault" if abs(value) > abs(threshold) else "ok"


# --------------------------------------------------------------------------- geometry


def raised(u: float) -> float:
    """Raised-cosine easing, 0 -> 1."""
    return (1.0 - math.cos(math.pi * max(0.0, min(1.0, u)))) / 2.0


def held(u: float) -> float:
    """Raised cosine with a genuine hold at both ends of the travel.

    ``e`` is exactly 0 for the first 10 % and exactly 1 for the last 15 % of the
    travel, which turns each turnaround into a static pose several frames wide.
    The engine's detected extremum can then land anywhere in that window without
    changing the measured geometry, exactly as a real lifter's pause would.
    """
    return raised(min(1.0, max(0.0, (u - 0.10) / 0.75)))


def front_loaded(u: float) -> float:
    """Front-loaded easing: most of the travel happens in the first half."""
    return 1.0 - (1.0 - max(0.0, min(1.0, u))) ** 2.2


def front_hold(
    u: float,
    lead: float = 0.06,
    top: float = 0.80,
    knee: float = 0.62,
    level: float = 0.88,
    power: float = 2.2,
) -> float:
    """Deadlift hip-travel profile: fast off the floor, then a lockout hold.

    Two properties the fixture needs and a plain raised cosine cannot give at
    once.  (a) Most of the travel happens early, so the FR-9 early-ascent window
    — only 40 % of the ascent — sees a large hip and shoulder excursion and the
    regression that measures ``hip_shoulder_rise_ratio`` has a usable
    signal-to-noise ratio.  (b) The last stretch is *linear* into an exact hold,
    so hip height leaves the lockout plateau steeply; deadlift hip height is
    otherwise almost stationary with respect to joint angle near lockout (both
    segments are vertical), which would smear the detected extremum across a
    dozen frames.  Both halves are still smooth interpolations between the
    start, extremum and end postures.
    """
    if u <= lead:
        return 0.0
    if u >= top:
        return 1.0
    mid = lead + knee * (top - lead)
    if u <= mid:
        q = (u - lead) / (mid - lead)
        return level * (1.0 - (1.0 - q) ** power)
    return level + (1.0 - level) * (u - mid) / (top - mid)


def smoothstep(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def circle_intersection(
    c1: tuple[float, float], r1: float, c2: tuple[float, float], r2: float, pick: str
) -> tuple[float, float]:
    """Intersection of two circles; ``pick`` selects ``up``/``down``/``left``/``right``."""
    (x1, y1), (x2, y2) = c1, c2
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy)
    if d < 1e-12 or d > r1 + r2 or d < abs(r1 - r2):
        raise ValueError(f"circles do not intersect: d={d:.4f} r1={r1:.4f} r2={r2:.4f}")
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, r1 * r1 - a * a))
    mx, my = x1 + a * dx / d, y1 + a * dy / d
    ox, oy = -dy * h / d, dx * h / d
    p, q = (mx + ox, my + oy), (mx - ox, my - oy)
    if pick == "up":
        return p if p[1] <= q[1] else q
    if pick == "down":
        return p if p[1] > q[1] else q
    if pick == "right":
        return p if p[0] >= q[0] else q
    return p if p[0] < q[0] else q


def unit(v: tuple[float, float]) -> tuple[float, float]:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


# --------------------------------------------------------------------------- skeletons

Point = tuple[float, float]
Skeleton = dict[str, Point]


def _head_points(nose: Point, out: Skeleton) -> None:
    nx, ny = nose
    out["nose"] = (nx, ny)
    out["left_eye"] = (nx - 0.010, ny - 0.010)
    out["right_eye"] = (nx + 0.010, ny - 0.010)
    out["left_ear"] = (nx - 0.022, ny - 0.004)
    out["right_ear"] = (nx + 0.022, ny - 0.004)


def side_skeleton(mid: dict[str, Point], nose: Point) -> Skeleton:
    """Split a midline (sagittal) pose into left/right COCO keypoints."""
    out: Skeleton = {}
    for name in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"):
        x, y = mid[name]
        out[f"left_{name}"] = (x - LATERAL / 2, y)
        out[f"right_{name}"] = (x + LATERAL / 2, y)
    _head_points(nose, out)
    return out


# --------------------------------------------------------------------------- squat side


@dataclass
class RepTiming:
    start: int
    extremum: int
    end: int


def _cycle_layout(fps: float, n_reps: int, rng: random.Random) -> dict:
    """Frame counts for pauses and travel phases, with per-rep tempo variation."""
    pause_out = max(3, int(round(0.14 * fps)))
    pause_mid = max(3, int(round(0.13 * fps)))
    reps = []
    for _ in range(n_reps):
        tempo = rng.uniform(0.88, 1.12)
        down = max(4, int(round(0.55 * tempo * fps)))
        up = max(4, int(round(0.62 * tempo * fps)))
        reps.append((down, up))
    return {"pause_out": pause_out, "pause_mid": pause_mid, "reps": reps}


def _phase_track(layout: dict) -> tuple[list[tuple[int, float]], list[RepTiming]]:
    """Return ``[(rep_index, phase)]`` per frame plus the true rep timings.

    ``phase`` runs 0 -> 1 over the outbound travel, sits at 1 through the middle
    pause, and returns 1 -> 0 over the return travel.  For squats and push-ups
    the outbound travel is the descent; for deadlifts it is the ascent.
    """
    track: list[tuple[int, float]] = []
    timings: list[RepTiming] = []
    po, pm = layout["pause_out"], layout["pause_mid"]
    for _ in range(po):
        track.append((0, 0.0))
    for rep_index, (down, up) in enumerate(layout["reps"]):
        start_center = len(track) - po + po // 2
        for i in range(1, down + 1):
            track.append((rep_index, i / down))
        extremum_first = len(track) - 1
        for _ in range(pm):
            track.append((rep_index, 1.0))
        extremum = extremum_first + pm // 2
        for i in range(1, up + 1):
            track.append((rep_index, 1.0 - i / up))
        for _ in range(po - 1):
            track.append((rep_index, 0.0))
        end_center = len(track) - (po - 1) + po // 2 - 1
        timings.append(RepTiming(start=start_center, extremum=extremum, end=end_center))
    return track, timings


def build_squat_side(
    height: float, facing: int, levels: list[dict], layout: dict
) -> tuple[list[Skeleton], list[RepTiming], list[dict]]:
    shank, femur = SEG["shank"] * height, SEG["femur"] * height
    trunk, head = SEG["trunk"] * height, SEG["head"] * height
    upper_arm = SEG["upper_arm"] * height
    ankle = (0.500, FLOOR_Y)
    track, timings = _phase_track(layout)

    theta_s_top, theta_f_top, tau_top = math.radians(4.0), math.radians(4.0), math.radians(8.0)
    frames: list[Skeleton] = []
    for rep_index, phase in track:
        lv = levels[min(rep_index, len(levels) - 1)]
        theta_f_bot = math.acos(max(-1.0, min(1.0, lv["depth_ratio"])))
        tau_bot = math.radians(lv["trunk_lean_deg"])
        theta_s_bot = math.radians(30.0)
        e = held(phase)
        theta_s = theta_s_top + (theta_s_bot - theta_s_top) * e
        theta_f = theta_f_top + (theta_f_bot - theta_f_top) * e
        tau = tau_top + (tau_bot - tau_top) * e
        knee = (ankle[0] + facing * shank * math.sin(theta_s), ankle[1] - shank * math.cos(theta_s))
        hip = (knee[0] - facing * femur * math.sin(theta_f), knee[1] - femur * math.cos(theta_f))
        shoulder = (hip[0] + facing * trunk * math.sin(tau), hip[1] - trunk * math.cos(tau))
        wrist = (shoulder[0] - facing * 0.30 * upper_arm, shoulder[1] + 0.16 * upper_arm)
        elbow = (
            shoulder[0] - facing * 0.55 * upper_arm,
            shoulder[1] + 0.62 * upper_arm,
        )
        nose = (shoulder[0] + facing * 0.42 * head, shoulder[1] - 0.78 * head)
        frames.append(
            side_skeleton(
                {
                    "shoulder": shoulder,
                    "elbow": elbow,
                    "wrist": wrist,
                    "hip": hip,
                    "knee": knee,
                    "ankle": ankle,
                },
                nose,
            )
        )
    truths = [
        {
            "depth_ratio": levels[min(i, len(levels) - 1)]["depth_ratio"],
            "trunk_lean_deg": levels[min(i, len(levels) - 1)]["trunk_lean_deg"],
        }
        for i in range(len(timings))
    ]
    return frames, timings, truths


def build_squat_front(
    height: float, levels: list[dict], layout: dict, narrow: float = 1.0
) -> tuple[list[Skeleton], list[RepTiming], list[dict]]:
    shank, femur = SEG["shank"] * height, SEG["femur"] * height
    trunk, head = SEG["trunk"] * height, SEG["head"] * height
    hip_w = SEG["hip_width"] * height * narrow
    sh_w = SEG["shoulder_width"] * height * narrow
    stance = SEG["stance"] * height * narrow
    upper_arm = SEG["upper_arm"] * height * narrow
    cx = 0.500
    stand_hip_y = FLOOR_Y - shank - femur
    bottom_hip_y = FLOOR_Y - 0.60 * (shank + femur)
    track, timings = _phase_track(layout)

    frames: list[Skeleton] = []
    for rep_index, phase in track:
        lv = levels[min(rep_index, len(levels) - 1)]
        e = held(phase)
        valgus = math.radians(lv["fppa_deg"] * e)
        shift = lv["lateral_shift_frac"] * e
        hip_y = stand_hip_y + (bottom_hip_y - stand_hip_y) * e
        mid_hip_x = cx + shift * hip_w
        out: Skeleton = {}
        for side, sign in (("left", -1.0), ("right", 1.0)):
            hip = (mid_hip_x + sign * hip_w / 2.0, hip_y)
            ankle = (cx + sign * stance, FLOOR_Y)
            mx, my = (hip[0] + ankle[0]) / 2.0, (hip[1] + ankle[1]) / 2.0
            half = math.hypot(ankle[0] - hip[0], ankle[1] - hip[1]) / 2.0
            nx, ny = unit((-(ankle[1] - hip[1]), ankle[0] - hip[0]))
            # Point the normal toward the midline so a positive angle is medial.
            if (mx + nx) * sign > mx * sign:
                nx, ny = -nx, -ny
            offset = half * math.tan(valgus / 2.0)
            knee = (mx + nx * offset, my + ny * offset)
            out[f"{side}_hip"] = hip
            out[f"{side}_ankle"] = ankle
            out[f"{side}_knee"] = knee
        shoulder_y = hip_y - trunk * (0.97 - 0.10 * e)
        for side, sign in (("left", -1.0), ("right", 1.0)):
            out[f"{side}_shoulder"] = (cx + sign * sh_w / 2.0, shoulder_y)
            out[f"{side}_elbow"] = (
                cx + sign * (sh_w / 2.0 + 0.22 * upper_arm),
                shoulder_y + 0.85 * upper_arm,
            )
            out[f"{side}_wrist"] = (
                cx + sign * (sh_w / 2.0 + 0.05 * upper_arm),
                shoulder_y - 0.10 * upper_arm,
            )
        _head_points((mid_hip_x + 0.004, shoulder_y - 0.78 * head), out)
        frames.append(out)

    truths = [
        {
            "fppa_deg": levels[min(i, len(levels) - 1)]["fppa_deg"],
            "lateral_shift_frac": levels[min(i, len(levels) - 1)]["lateral_shift_frac"],
        }
        for i in range(len(timings))
    ]
    return frames, timings, truths


def build_deadlift(
    height: float, facing: int, levels: list[dict], layout: dict
) -> tuple[list[Skeleton], list[RepTiming], list[dict]]:
    shank, femur = SEG["shank"] * height, SEG["femur"] * height
    trunk, head = SEG["trunk"] * height, SEG["head"] * height
    upper_arm = SEG["upper_arm"] * height
    ankle = (0.480, FLOOR_Y)
    track, timings = _phase_track(layout)

    theta_s_bot, psi_bot = math.radians(20.0), math.radians(74.0)
    theta_s_top, psi_top = math.radians(3.0), math.radians(3.0)
    theta_t_bot = math.radians(70.0)

    def hip_at(e: float) -> tuple[Point, Point, float]:
        theta_s = theta_s_bot + (theta_s_top - theta_s_bot) * e
        psi = psi_bot + (psi_top - psi_bot) * e
        knee = (ankle[0] + facing * shank * math.sin(theta_s), ankle[1] - shank * math.cos(theta_s))
        hip = (knee[0] - facing * femur * math.sin(psi), knee[1] - femur * math.cos(psi))
        return hip, knee, psi

    hip0, _, _ = hip_at(0.0)
    hip1, _, psi1 = hip_at(1.0)
    shoulder0_y = hip0[1] - trunk * math.cos(theta_t_bot)

    def pose_at(fraction: float) -> tuple[Point, Point, float]:
        """Knee and hip for a prescribed fraction of the total hip travel."""
        theta_s = theta_s_bot + (theta_s_top - theta_s_bot) * fraction
        knee = (ankle[0] + facing * shank * math.sin(theta_s), ankle[1] - shank * math.cos(theta_s))
        hip_y = hip0[1] + (hip1[1] - hip0[1]) * fraction
        psi = math.acos(max(-1.0, min(1.0, (knee[1] - hip_y) / femur)))
        return (knee[0] - facing * femur * math.sin(psi), hip_y), knee, psi

    frames: list[Skeleton] = []
    for rep_index, phase in track:
        lv = levels[min(rep_index, len(levels) - 1)]
        ratio = lv["hip_shoulder_rise_ratio"]
        theta_t_lock = math.radians(180.0 - lv["hip_ext_angle_deg"]) - psi1
        shoulder_lock_y = hip1[1] - trunk * math.cos(theta_t_lock)
        fraction = front_hold(phase)
        hip, knee, _ = pose_at(fraction)
        affine_y = shoulder0_y + (hip[1] - hip0[1]) / ratio
        w = smoothstep((fraction - 0.90) / 0.10) if fraction > 0.90 else 0.0
        shoulder_y = (1.0 - w) * affine_y + w * shoulder_lock_y
        cos_t = max(-1.0, min(1.0, (hip[1] - shoulder_y) / trunk))
        theta_t = math.acos(cos_t)
        shoulder = (hip[0] + facing * trunk * math.sin(theta_t), shoulder_y)
        s_bump = (phase - 0.62) / 0.30
        bump = max(0.0, 1.0 - s_bump * s_bump)
        drift = lv["bar_drift_base"] + (lv["bar_drift_frac"] - lv["bar_drift_base"]) * bump
        bar_y = (FLOOR_Y - 0.030) + (hip1[1] + 0.035 - (FLOOR_Y - 0.030)) * fraction
        wrist = (ankle[0] + facing * drift * shank, bar_y)
        arm = unit((wrist[0] - shoulder[0], wrist[1] - shoulder[1]))
        elbow = (shoulder[0] + arm[0] * upper_arm, shoulder[1] + arm[1] * upper_arm)
        nose = (shoulder[0] + facing * 0.46 * head, shoulder[1] - 0.72 * head)
        frames.append(
            side_skeleton(
                {
                    "shoulder": shoulder,
                    "elbow": elbow,
                    "wrist": wrist,
                    "hip": hip,
                    "knee": knee,
                    "ankle": ankle,
                },
                nose,
            )
        )
    truths = [
        {
            "hip_shoulder_rise_ratio": levels[min(i, len(levels) - 1)]["hip_shoulder_rise_ratio"],
            "bar_drift_frac": levels[min(i, len(levels) - 1)]["bar_drift_frac"],
            "hip_ext_angle_deg": levels[min(i, len(levels) - 1)]["hip_ext_angle_deg"],
        }
        for i in range(len(timings))
    ]
    return frames, timings, truths


def build_pushup(
    height: float, levels: list[dict], layout: dict
) -> tuple[list[Skeleton], list[RepTiming], list[dict]]:
    trunk, head = SEG["trunk"] * height, SEG["head"] * height
    femur, shank = SEG["femur"] * height, SEG["shank"] * height
    upper_arm, forearm = SEG["upper_arm"] * height, SEG["forearm"] * height
    body = trunk + femur + shank
    wrist = (0.150, FLOOR_Y)
    ankle = (wrist[0] + 0.905 * body, FLOOR_Y)
    track, timings = _phase_track(layout)

    def arm_span(angle_deg: float) -> float:
        a = math.radians(angle_deg)
        return math.sqrt(upper_arm**2 + forearm**2 - 2 * upper_arm * forearm * math.cos(a))

    frames: list[Skeleton] = []
    for rep_index, phase in track:
        lv = levels[min(rep_index, len(levels) - 1)]
        e = held(phase)
        elbow_deg = lv["elbow_top"] + (lv["elbow_bottom"] - lv["elbow_top"]) * e
        span = arm_span(elbow_deg)
        shoulder = circle_intersection(wrist, span, ankle, body, pick="up")
        elbow_pt = circle_intersection(shoulder, upper_arm, wrist, forearm, pick="right")
        u = unit((ankle[0] - shoulder[0], ankle[1] - shoulder[1]))
        n = (-u[1], u[0])
        dev = lv["hip_dev_frac"] * (0.35 + 0.65 * e)
        alpha = math.asin(max(-0.9, min(0.9, dev)))
        hip = (
            shoulder[0] + trunk * (math.cos(alpha) * u[0] + math.sin(alpha) * n[0]),
            shoulder[1] + trunk * (math.cos(alpha) * u[1] + math.sin(alpha) * n[1]),
        )
        knee_t = femur / (femur + shank)
        knee = (hip[0] + (ankle[0] - hip[0]) * knee_t, hip[1] + (ankle[1] - hip[1]) * knee_t)
        nose = (shoulder[0] - 0.55 * head, shoulder[1] - 0.30 * head)
        frames.append(
            side_skeleton(
                {
                    "shoulder": shoulder,
                    "elbow": elbow_pt,
                    "wrist": wrist,
                    "hip": hip,
                    "knee": knee,
                    "ankle": ankle,
                },
                nose,
            )
        )
    truths = [
        {
            "elbow_angle_deg@bottom": levels[min(i, len(levels) - 1)]["elbow_bottom"],
            "elbow_angle_deg@top": levels[min(i, len(levels) - 1)]["elbow_top"],
            "hip_dev_frac": levels[min(i, len(levels) - 1)]["hip_dev_frac"],
        }
        for i in range(len(timings))
    ]
    return frames, timings, truths


# --------------------------------------------------------------------------- degradation


def apply_perspective(frames: list[Skeleton], track: list[tuple[int, float]], amp: float) -> None:
    """Isotropic +-``amp`` size change tied to rep phase, about the body centroid."""
    cx = sum(p[0] for f in frames for p in f.values()) / (len(frames) * len(frames[0]))
    cy = sum(p[1] for f in frames for p in f.values()) / (len(frames) * len(frames[0]))
    for frame, (_, phase) in zip(frames, track):
        s = 1.0 + amp * (2.0 * phase - 1.0)
        for name, (x, y) in frame.items():
            frame[name] = (cx + s * (x - cx), cy + s * (y - cy))


def apply_drift(frames: list[Skeleton], dx: float, dy: float) -> None:
    n = max(1, len(frames) - 1)
    for i, frame in enumerate(frames):
        t = i / n - 0.5
        for name, (x, y) in frame.items():
            frame[name] = (x + dx * t, y + dy * t)


def apply_jitter(frames: list[Skeleton], sigma: float, rng: random.Random) -> None:
    """Temporally correlated AR(1) jitter, independent per keypoint and axis."""
    scale = math.sqrt(1.0 - AR_RHO * AR_RHO)
    state = {name: [rng.gauss(0.0, sigma), rng.gauss(0.0, sigma)] for name in COCO_NAMES}
    for frame in frames:
        for name in COCO_NAMES:
            s = state[name]
            s[0] = AR_RHO * s[0] + scale * rng.gauss(0.0, sigma)
            s[1] = AR_RHO * s[1] + scale * rng.gauss(0.0, sigma)
            x, y = frame[name]
            frame[name] = (x + s[0], y + s[1])


def apply_teleports(frames: list[Skeleton], rng: random.Random) -> set[tuple[int, str]]:
    """One keypoint jumps 0.05-0.15 units on ~1 % of frames, still reporting high conf."""
    hits: set[tuple[int, str]] = set()
    for index in range(len(frames)):
        if rng.random() >= TELEPORT_PROB:
            continue
        name = rng.choice(COCO_NAMES)
        angle = rng.uniform(0.0, 2.0 * math.pi)
        jump = rng.uniform(0.05, 0.15)
        x, y = frames[index][name]
        frames[index][name] = (x + jump * math.cos(angle), y + jump * math.sin(angle))
        hits.add((index, name))
    return hits


def confidences(
    n_frames: int,
    view: str,
    rng: random.Random,
    teleported: set[tuple[int, str]],
) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    near = "left" if view == "side_left" else "right"
    for index in range(n_frames):
        row: dict[str, float] = {}
        for name in COCO_NAMES:
            if view == "front":
                base = CONF_FRONT
            elif name.startswith(near):
                base = CONF_NEAR
            elif name.startswith("left") or name.startswith("right"):
                base = CONF_FAR
            else:
                base = CONF_NEAR
            value = base + rng.gauss(0.0, 0.015)
            if rng.random() < CONF_DIP_PROB:
                value = rng.uniform(0.36, 0.55)
            if (index, name) in teleported:
                value = TELEPORT_CONF
            row[name] = max(0.05, min(0.99, value))
        out.append(row)
    return out


# --------------------------------------------------------------------------- handcheck


def _mid(frame: dict[str, dict[str, float]], part: str) -> Point:
    a, b = frame[f"left_{part}"], frame[f"right_{part}"]
    return ((a["x"] + b["x"]) / 2.0, (a["y"] + b["y"]) / 2.0)


def _len(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _cosine_angle(a: Point, vertex: Point, c: Point) -> float:
    """Interior angle at ``vertex`` in degrees, via the law of cosines."""
    p, q = _len(a, vertex), _len(c, vertex)
    r = _len(a, c)
    cos = (p * p + q * q - r * r) / (2.0 * p * q)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def handcheck_features(frame: dict[str, dict[str, float]], names: list[str]) -> dict[str, float]:
    """Recompute features from committed keypoints, independently of the engine.

    Deliberately written from a different starting point than
    ``engine/geometry.py``: angles come from the law of cosines rather than dot
    products, the trunk pitch from an explicit rise/run, and the hip deviation
    from an explicit line equation.  A shared sign or normalization mistake
    would not survive both implementations.
    """
    out: dict[str, float] = {}
    for name in names:
        if name == "depth_ratio":
            hip, knee = _mid(frame, "hip"), _mid(frame, "knee")
            out[name] = (knee[1] - hip[1]) / _len(hip, knee)
        elif name == "trunk_lean_deg":
            sh, hip = _mid(frame, "shoulder"), _mid(frame, "hip")
            out[name] = math.degrees(math.atan(abs(hip[0] - sh[0]) / abs(hip[1] - sh[1])))
        elif name == "fppa_deg":
            mid_hip = _mid(frame, "hip")
            values = []
            for side in ("left", "right"):
                hip = (frame[f"{side}_hip"]["x"], frame[f"{side}_hip"]["y"])
                knee = (frame[f"{side}_knee"]["x"], frame[f"{side}_knee"]["y"])
                ankle = (frame[f"{side}_ankle"]["x"], frame[f"{side}_ankle"]["y"])
                magnitude = 180.0 - _cosine_angle(hip, knee, ankle)
                t = (knee[1] - hip[1]) / (ankle[1] - hip[1])
                line_x = hip[0] + t * (ankle[0] - hip[0])
                medial = (line_x - knee[0]) * (1.0 if hip[0] >= mid_hip[0] else -1.0)
                values.append(magnitude if medial >= 0 else -magnitude)
            out[name] = sum(values) / 2.0
        elif name == "lateral_shift_frac":
            hip, ankle = _mid(frame, "hip"), _mid(frame, "ankle")
            width = _len(
                (frame["left_hip"]["x"], frame["left_hip"]["y"]),
                (frame["right_hip"]["x"], frame["right_hip"]["y"]),
            )
            out[name] = abs(hip[0] - ankle[0]) / width
        elif name == "hip_ext_angle_deg":
            out[name] = _cosine_angle(_mid(frame, "shoulder"), _mid(frame, "hip"), _mid(frame, "knee"))
        elif name == "elbow_angle_deg":
            out[name] = _cosine_angle(_mid(frame, "shoulder"), _mid(frame, "elbow"), _mid(frame, "wrist"))
        elif name == "hip_dev_frac":
            sh, hip, ankle = _mid(frame, "shoulder"), _mid(frame, "hip"), _mid(frame, "ankle")
            ux, uy = ankle[0] - sh[0], ankle[1] - sh[1]
            length = math.hypot(ux, uy)
            # Signed distance from the shoulder-ankle line, positive below it.
            signed = ((hip[1] - sh[1]) * ux - (hip[0] - sh[0]) * uy) / length
            out[name] = (signed if ux >= 0 else -signed) / _len(sh, hip)
        elif name == "bar_drift_frac":
            wrist, ankle, knee = _mid(frame, "wrist"), _mid(frame, "ankle"), _mid(frame, "knee")
            nose = (frame["nose"]["x"], frame["nose"]["y"])
            hip = _mid(frame, "hip")
            facing = -1.0 if nose[0] - hip[0] < 0 else 1.0
            out[name] = ((wrist[0] - ankle[0]) / _len(knee, ankle)) * facing
        else:  # pragma: no cover - guarded by the caller
            raise KeyError(name)
    return out


# --------------------------------------------------------------------------- clip specs


@dataclass
class ClipSpec:
    clip_id: str
    exercise: str
    view: str
    declared: str | None
    fps: float
    height: float
    sigma: float
    n_reps: int
    facing: int = 1
    levels: list[dict] = field(default_factory=list)
    narrow: float = 1.0
    perspective: float = 0.02
    occlusions: list[tuple[int, str, str]] = field(default_factory=list)
    degrade: str | None = None
    degrade_frac: float = 0.0
    degrade_keypoint: str = "left_ankle"
    valid: bool = True


def _levels_squat_side(rng, depth_kind, lean_kind, n):
    out = []
    for _ in range(n):
        if depth_kind == "fault":
            d = rng.uniform(0.085, 0.150)
        elif depth_kind == "near":
            d = rng.uniform(0.022, 0.038)
        else:
            d = rng.uniform(-0.115, -0.045)
        if lean_kind == "fault":
            t = rng.uniform(61.0, 72.0)
        elif lean_kind == "near":
            t = rng.uniform(53.5, 56.5)
        else:
            t = rng.uniform(38.0, 49.0)
        out.append({"depth_ratio": round(d, 6), "trunk_lean_deg": round(t, 6)})
    return out


def _levels_squat_front(rng, valgus_kind, shift_kind, n):
    out = []
    for _ in range(n):
        if valgus_kind == "fault":
            v = rng.uniform(20.0, 28.0)
        elif valgus_kind == "near":
            v = rng.uniform(11.2, 12.8)
        else:
            v = rng.uniform(1.5, 5.0)
        if shift_kind == "fault":
            s = rng.uniform(0.23, 0.34)
        elif shift_kind == "near":
            s = rng.uniform(0.142, 0.158)
        else:
            s = rng.uniform(0.015, 0.070)
        out.append({"fppa_deg": round(v, 6), "lateral_shift_frac": round(s, 6)})
    return out


def _levels_deadlift(rng, rise_kind, drift_kind, lock_kind, n, negative_drift=False):
    out = []
    for _ in range(n):
        if rise_kind == "fault":
            r = rng.uniform(1.75, 2.10)
        elif rise_kind == "near":
            r = rng.uniform(1.46, 1.54)
        else:
            r = rng.uniform(0.70, 1.18)
        if drift_kind == "fault":
            d = rng.uniform(0.30, 0.42)
        elif drift_kind == "near":
            d = rng.uniform(0.192, 0.208)
        else:
            d = rng.uniform(0.020, 0.095)
        if negative_drift:
            d = -d
        if lock_kind == "fault":
            a = rng.uniform(148.0, 163.0)
        elif lock_kind == "near":
            a = rng.uniform(169.0, 171.0)
        else:
            a = rng.uniform(173.5, 177.0)
        out.append(
            {
                "hip_shoulder_rise_ratio": round(r, 6),
                "bar_drift_frac": round(d, 6),
                "bar_drift_base": round(d * 0.18, 6),
                "hip_ext_angle_deg": round(a, 6),
            }
        )
    return out


def _levels_pushup(rng, depth_kind, sag_kind, lock_kind, n, pike=False):
    out = []
    for _ in range(n):
        if depth_kind == "fault":
            b = rng.uniform(110.0, 124.0)
        elif depth_kind == "near":
            b = rng.uniform(98.5, 101.5)
        else:
            b = rng.uniform(70.0, 86.0)
        if lock_kind == "fault":
            t = rng.uniform(142.0, 154.0)
        elif lock_kind == "near":
            t = rng.uniform(159.0, 161.0)
        else:
            t = rng.uniform(169.0, 178.0)
        if t - b < 46.0:
            t = min(178.0, b + 46.0)
        if sag_kind == "fault":
            h = rng.uniform(0.135, 0.200)
        elif sag_kind == "near":
            h = rng.uniform(0.076, 0.084)
        else:
            h = rng.uniform(-0.030, 0.030)
        if pike:
            h = -h
        out.append(
            {
                "elbow_bottom": round(b, 6),
                "elbow_top": round(t, 6),
                "hip_dev_frac": round(h, 6),
            }
        )
    return out


def clip_specs(rng: random.Random) -> list[ClipSpec]:
    """The committed 58-clip corpus (50 valid + 8 invalid)."""
    specs: list[ClipSpec] = []
    fps_cycle = [30.0, 24.0, 30.0, 60.0, 24.0, 30.0]

    # --- squat, side view: 12 clips ------------------------------------
    plan = [
        ("clean", "clean"),
        ("fault", "clean"),
        ("clean", "fault"),
        ("fault", "fault"),
        ("clean", "clean"),
        ("fault", "clean"),
        ("near", "clean"),
        ("clean", "near"),
        ("clean", "clean"),
        ("fault", "fault"),
        ("clean", "fault"),
        ("fault", "clean"),
    ]
    for i, (depth, lean) in enumerate(plan, start=1):
        view = "side_left" if i % 2 else "side_right"
        declared: str | None = view
        if i in (10, 11):
            declared = "side"
        if i == 12:
            declared = None
        n_reps = 5 if i % 4 == 0 else 4
        specs.append(
            ClipSpec(
                clip_id=f"squat_side_{i:02d}",
                exercise="squat",
                view=view,
                declared=declared,
                fps=fps_cycle[i % len(fps_cycle)],
                height=HEIGHTS[i % 3],
                sigma=round(rng.uniform(0.004, 0.0085), 6),
                n_reps=n_reps,
                facing=1 if view == "side_left" else -1,
                levels=_levels_squat_side(rng, depth, lean, n_reps),
                degrade="boundary_valid" if i == 9 else None,
                degrade_frac=0.28 if i == 9 else 0.0,
            )
        )

    # --- squat, front view: 12 clips -----------------------------------
    plan = [
        ("clean", "clean"),
        ("fault", "clean"),
        ("clean", "fault"),
        ("fault", "fault"),
        ("near", "clean"),
        ("fault", "clean"),
        ("clean", "near"),
        ("clean", "clean"),
        ("fault", "fault"),
        ("clean", "clean"),
        ("fault", "clean"),
        ("clean", "fault"),
    ]
    for i, (valgus, shift) in enumerate(plan, start=1):
        n_reps = 5 if i % 4 == 0 else 4
        specs.append(
            ClipSpec(
                clip_id=f"squat_front_{i:02d}",
                exercise="squat",
                view="front",
                declared=None if i == 10 else "front",
                fps=fps_cycle[(i + 2) % len(fps_cycle)],
                height=HEIGHTS[(i + 1) % 3],
                sigma=round(rng.uniform(0.004, 0.007), 6),
                n_reps=n_reps,
                levels=_levels_squat_front(rng, valgus, shift, n_reps),
                narrow=0.62 if i == 10 else 1.0,
            )
        )

    # --- deadlift, side view: 14 clips ---------------------------------
    plan = [
        ("clean", "clean", "clean"),
        ("fault", "clean", "clean"),
        ("clean", "fault", "clean"),
        ("clean", "clean", "fault"),
        ("fault", "fault", "clean"),
        ("clean", "clean", "clean"),
        ("fault", "clean", "fault"),
        ("near", "clean", "clean"),
        ("clean", "near", "clean"),
        ("clean", "clean", "near"),
        ("fault", "clean", "clean"),
        ("clean", "fault", "fault"),
        ("fault", "fault", "fault"),
        ("clean", "clean", "clean"),
    ]
    for i, (rise, drift, lock) in enumerate(plan, start=1):
        view = "side_left" if i % 2 else "side_right"
        declared: str | None = view
        if i == 11:
            declared = None
        if i == 6:
            declared = "side"
        n_reps = 5 if i % 4 == 0 else 4
        specs.append(
            ClipSpec(
                clip_id=f"deadlift_side_{i:02d}",
                exercise="deadlift",
                view=view,
                declared=declared,
                fps=fps_cycle[(i + 1) % len(fps_cycle)],
                height=HEIGHTS[(i + 2) % 3],
                sigma=round(rng.uniform(0.004, 0.007), 6),
                n_reps=n_reps,
                facing=1 if view == "side_left" else -1,
                levels=_levels_deadlift(
                    rng, rise, drift, lock, n_reps, negative_drift=i in (3, 12)
                ),
                perspective=0.010,
            )
        )

    # --- push-up, side view: 12 clips ----------------------------------
    plan = [
        ("clean", "clean", "clean"),
        ("fault", "clean", "clean"),
        ("clean", "fault", "clean"),
        ("clean", "clean", "fault"),
        ("fault", "fault", "clean"),
        ("clean", "clean", "clean"),
        ("near", "clean", "clean"),
        ("clean", "near", "clean"),
        ("fault", "clean", "clean"),
        ("clean", "fault", "fault"),
        ("fault", "clean", "fault"),
        ("clean", "clean", "near"),
    ]
    for i, (depth, sag, lock) in enumerate(plan, start=1):
        view = "side_left" if i % 2 else "side_right"
        n_reps = 5 if i % 4 == 0 else 4
        specs.append(
            ClipSpec(
                clip_id=f"pushup_side_{i:02d}",
                exercise="pushup",
                view=view,
                declared=None if i == 9 else view,
                fps=fps_cycle[(i + 4) % len(fps_cycle)],
                height=HEIGHTS[i % 3],
                sigma=round(rng.uniform(0.004, 0.006), 6),
                n_reps=n_reps,
                levels=_levels_pushup(rng, depth, sag, lock, n_reps, pike=i in (8, 10)),
            )
        )

    # --- injected per-rep occlusions on otherwise valid clips -----------
    by_id = {s.clip_id: s for s in specs}
    by_id["squat_side_02"].occlusions = [(1, "left_knee", "bottom")]
    by_id["squat_side_05"].occlusions = [(2, "right_shoulder", "bottom")]
    by_id["squat_front_03"].occlusions = [(0, "right_knee", "bottom")]
    by_id["squat_front_08"].occlusions = [(3, "left_ankle", "bottom")]
    by_id["deadlift_side_04"].occlusions = [(1, "right_knee", "top")]
    by_id["deadlift_side_09"].occlusions = [(2, "left_wrist", "whole_rep")]
    by_id["deadlift_side_13"].occlusions = [(0, "left_shoulder", "ascent_early")]
    by_id["pushup_side_03"].occlusions = [(1, "left_elbow", "bottom")]
    by_id["pushup_side_07"].occlusions = [(2, "right_ankle", "bottom")]
    by_id["pushup_side_11"].occlusions = [(0, "right_wrist", "top")]

    # --- 8 invalid clips ------------------------------------------------
    invalids = [
        ("invalid_01", "squat", "side_left", "occlusion", 0.55, "left_knee"),
        ("invalid_02", "squat", "front", "occlusion", 0.48, "right_ankle"),
        ("invalid_03", "deadlift", "side_right", "occlusion", 0.62, "left_wrist"),
        ("invalid_04", "pushup", "side_left", "occlusion", 0.45, "right_elbow"),
        ("invalid_05", "deadlift", "side_left", "occlusion", 0.40, "right_hip"),
        ("invalid_06", "pushup", "side_right", "occlusion", 0.52, "left_shoulder"),
        ("invalid_07", "squat", "side_right", "out_of_frame", 0.40, "left_ankle"),
        ("invalid_08", "squat", "side_left", "occlusion", 0.32, "right_hip"),
    ]
    for index, (clip_id, exercise, view, mode, frac, keypoint) in enumerate(invalids):
        if exercise == "squat" and view == "front":
            levels = _levels_squat_front(rng, "clean", "clean", 4)
        elif exercise == "squat":
            levels = _levels_squat_side(rng, "clean", "clean", 4)
        elif exercise == "deadlift":
            levels = _levels_deadlift(rng, "clean", "clean", "clean", 4)
        else:
            levels = _levels_pushup(rng, "clean", "clean", "clean", 4)
        specs.append(
            ClipSpec(
                clip_id=clip_id,
                exercise=exercise,
                view=view,
                declared=view,
                fps=30.0,
                height=HEIGHTS[index % 3],
                sigma=0.005,
                n_reps=4,
                facing=1 if view != "side_right" else -1,
                levels=levels,
                degrade=mode,
                degrade_frac=frac,
                degrade_keypoint=keypoint,
                valid=False,
            )
        )
    return specs


# --------------------------------------------------------------------------- assembly


def build_clip(spec: ClipSpec, rng: random.Random) -> dict:
    layout = _cycle_layout(spec.fps, spec.n_reps, rng)
    track, _ = _phase_track(layout)
    if spec.exercise == "squat" and spec.view == "front":
        frames, timings, truths = build_squat_front(spec.height, spec.levels, layout, spec.narrow)
    elif spec.exercise == "squat":
        frames, timings, truths = build_squat_side(spec.height, spec.facing, spec.levels, layout)
    elif spec.exercise == "deadlift":
        frames, timings, truths = build_deadlift(spec.height, spec.facing, spec.levels, layout)
    else:
        frames, timings, truths = build_pushup(spec.height, spec.levels, layout)

    apply_perspective(frames, track, spec.perspective)
    apply_drift(frames, rng.uniform(-0.024, 0.024), rng.uniform(-0.009, 0.009))
    apply_jitter(frames, spec.sigma, rng)
    teleported = apply_teleports(frames, rng)
    confs = confidences(len(frames), spec.view, rng, teleported)

    n = len(frames)
    view_family = "front" if spec.view == "front" else "side"
    required = REQUIRED[(spec.exercise, view_family)]

    if spec.degrade == "occlusion":
        count = int(round(spec.degrade_frac * n))
        for index in range(count):
            confs[index][spec.degrade_keypoint] = CONF_OCCLUDED
    elif spec.degrade == "out_of_frame":
        count = int(round(spec.degrade_frac * n))
        for index in range(count):
            for name in ("left_ankle", "right_ankle"):
                x, y = frames[index][name]
                frames[index][name] = (x, y + 0.09)
    elif spec.degrade == "boundary_valid":
        # 28 % of frames, all taken from mid-travel so no sampled phase frame is
        # touched: the clip must be accepted and every rule stays assessable.
        extremes = {t.extremum for t in timings} | {t.start for t in timings} | {
            t.end for t in timings
        }
        candidates = [
            i for i in range(n) if all(abs(i - e) > max(4, int(0.12 * spec.fps)) for e in extremes)
        ]
        target = int(round(spec.degrade_frac * n))
        for index in candidates[:target]:
            confs[index][spec.degrade_keypoint] = CONF_OCCLUDED

    # Per-rep occlusion windows.  A window is always at least as wide as the
    # tolerance band a rule is sampled over, so "the engine cannot look here" is
    # unambiguous however its detected frames land; which rules that makes
    # non-assessable is then *derived* from the profiles' feature keypoints
    # rather than asserted, so an occlusion can never silently kill a rule the
    # labels still claim is answerable.
    half = max(4, int(round(0.14 * spec.fps)))

    def rule_band(timing: RepTiming, phase: str) -> tuple[int, int]:
        if phase == "bottom":
            centre = timing.start if spec.exercise == "deadlift" else timing.extremum
            return max(0, centre - half), min(n - 1, centre + half)
        if phase == "top":
            centre = timing.extremum if spec.exercise == "deadlift" else timing.end
            return max(0, centre - half), min(n - 1, centre + half)
        if phase == "ascent_early":
            return timing.start, timing.start + max(1, (timing.extremum - timing.start) // 2)
        return timing.start, timing.end

    occluded_rules: dict[tuple[int, str], str] = {}
    for rep_index, keypoint, band in spec.occlusions:
        if rep_index >= len(timings):
            continue
        timing = timings[rep_index]
        lo, hi = rule_band(timing, band)
        if band in ("whole_rep", "ascent_early"):
            lo, hi = max(0, timing.start - 2), min(n - 1, timing.end + 2)
        for index in range(lo, hi + 1):
            confs[index][keypoint] = CONF_OCCLUDED
        for fault_id, _feature, phase, _views in RULES[spec.exercise]:
            if keypoint not in FEATURE_KEYPOINTS[(spec.exercise, fault_id)]:
                continue
            r_lo, r_hi = rule_band(timing, phase)
            covered = min(hi, r_hi) - max(lo, r_lo) + 1
            width = r_hi - r_lo + 1
            if phase == "whole_rep":
                if covered > 0.5 * width:
                    occluded_rules[(rep_index, fault_id)] = keypoint
            elif covered >= width:
                occluded_rules[(rep_index, fault_id)] = keypoint

    low_frac = sum(
        1 for row in confs if any(row[name] < MIN_CONF for name in required)
    ) / float(n)

    payload_frames = []
    for index, frame in enumerate(frames):
        keypoints = {}
        for name in COCO_NAMES:
            x, y = frame[name]
            keypoints[name] = {
                "x": round(x, 6),
                "y": round(y, 6),
                "conf": round(confs[index][name], 3),
            }
        payload_frames.append({"t_ms": round(index * 1000.0 / spec.fps, 3), "keypoints": keypoints})

    sidecar: dict = {"fps": spec.fps, "frames": payload_frames}
    if spec.declared is not None:
        sidecar = {"fps": spec.fps, "view": spec.declared, "frames": payload_frames}

    reps_label = []
    for rep_index, timing in enumerate(timings):
        rules: dict[str, dict] = {}
        for fault_id, feature, phase, views in RULES[spec.exercise]:
            if spec.view not in views:
                rules[fault_id] = {"assessable": False, "reason": "view_mismatch"}
                continue
            if (rep_index, fault_id) in occluded_rules:
                rules[fault_id] = {"assessable": False, "reason": "keypoints_not_visible"}
                continue
            key = f"{feature}@{phase}"
            truth = truths[rep_index]
            value = truth.get(key, truth.get(feature))
            rules[fault_id] = {
                "assessable": True,
                "label": label_for(spec.exercise, fault_id, value),
                "feature": feature,
                "metric_key": key,
                "true_value": round(value, 6),
            }
        reps_label.append(
            {
                "rep_index": rep_index,
                "start_frame": timing.start,
                "extremum_frame": timing.extremum,
                "end_frame": timing.end,
                "rules": rules,
            }
        )

    label = {
        "clip_id": spec.clip_id,
        "file": f"poses/{spec.clip_id}.keypoints.json",
        "kind": "clip",
        "exercise": spec.exercise,
        "exercise_id": EXERCISE_IDS[spec.exercise],
        "form_profile_id": PROFILE_IDS[spec.exercise],
        "declared_view": spec.declared,
        "view": spec.view,
        "fps": spec.fps,
        "noise_sigma": spec.sigma,
        "frames": n,
        "low_confidence_frac": round(low_frac, 6),
        "valid": spec.valid,
        "reject_reason": None if spec.valid else "insufficient_visibility",
        "rep_count": len(timings) if spec.valid else None,
        "reps": reps_label if spec.valid else [],
    }
    return {"sidecar": sidecar, "label": label, "timings": timings, "truths": truths}


PHOTO_PLAN = [
    ("photo_squat_side_01", "squat_side_01", 1, "bottom"),
    ("photo_squat_side_02", "squat_side_04", 2, "bottom"),
    ("photo_squat_front_01", "squat_front_02", 1, "bottom"),
    ("photo_deadlift_top_01", "deadlift_side_01", 2, "top"),
    ("photo_deadlift_top_02", "deadlift_side_02", 1, "top"),
    ("photo_pushup_bottom_01", "pushup_side_02", 1, "bottom"),
]


def build_photo(photo_id: str, source: dict, rep_index: int, phase: str) -> dict:
    """A single-frame sidecar lifted from a committed clip frame (FR-15)."""
    label = source["label"]
    exercise = label["exercise"]
    timing = source["timings"][rep_index]
    frame_index = timing.extremum if phase == "bottom" or exercise == "deadlift" else timing.end
    if exercise == "deadlift" and phase == "bottom":
        frame_index = timing.start
    frame = source["sidecar"]["frames"][frame_index]
    sidecar = {"fps": label["fps"], "view": label["view"], "frames": [{"t_ms": 0.0, **frame}]}
    sidecar["frames"][0]["t_ms"] = 0.0

    truth = source["truths"][rep_index]
    rules: dict[str, dict] = {}
    for fault_id, feature, rule_phase, views in RULES[exercise]:
        if label["view"] not in views:
            rules[fault_id] = {"assessable": False, "reason": "view_mismatch"}
            continue
        if feature == "hip_shoulder_rise_ratio" or rule_phase == "ascent_early":
            rules[fault_id] = {"assessable": False, "reason": "needs_multi_frame"}
            continue
        if rule_phase not in (phase, "whole_rep"):
            rules[fault_id] = {"assessable": False, "reason": "phase_not_shown"}
            continue
        key = f"{feature}@{rule_phase}"
        if feature == "bar_drift_frac":
            # A photo sees one frame, so the "max over the rep" collapses onto
            # the drift at that frame; recomputed from the committed keypoints.
            value = handcheck_features(frame["keypoints"], ["bar_drift_frac"])["bar_drift_frac"]
        else:
            value = truth.get(key, truth.get(feature))
        rules[fault_id] = {
            "assessable": True,
            "label": label_for(exercise, fault_id, value),
            "feature": feature,
            "metric_key": key,
            "true_value": round(value, 6),
        }
    photo_label = {
        "clip_id": photo_id,
        "file": f"poses/{photo_id}.keypoints.json",
        "kind": "photo",
        "exercise": exercise,
        "exercise_id": label["exercise_id"],
        "form_profile_id": label["form_profile_id"],
        "declared_view": label["view"],
        "view": label["view"],
        "declared_phase": phase,
        "fps": None,
        "noise_sigma": label["noise_sigma"],
        "frames": 1,
        "valid": True,
        "reject_reason": None,
        "rep_count": 1,
        "reps": [
            {
                "rep_index": 0,
                "start_frame": 0,
                "extremum_frame": 0,
                "end_frame": 0,
                "rules": rules,
            }
        ],
    }
    return {"sidecar": sidecar, "label": photo_label}


#: Eight committed reps whose feature values are recomputed from the committed
#: keypoints by ``handcheck_features`` above.  Drawn from the lowest-noise clips
#: so the residual gap between a raw frame and the engine's smoothed frame stays
#: well inside the M3 gate, and chosen to cover every single-frame feature in the
#: catalog; the two window features (``bar_drift_frac``,
#: ``hip_shoulder_rise_ratio``) are not single-frame quantities and are pinned by
#: M3 instead.
HANDCHECK_PLAN = [
    ("squat_side_01", 0, ["depth_ratio", "trunk_lean_deg"], "bottom"),
    ("squat_side_04", 2, ["depth_ratio", "trunk_lean_deg"], "bottom"),
    ("squat_front_12", 0, ["fppa_deg", "lateral_shift_frac"], "bottom"),
    ("squat_front_02", 1, ["fppa_deg", "lateral_shift_frac"], "bottom"),
    ("deadlift_side_06", 1, ["hip_ext_angle_deg"], "top"),
    ("deadlift_side_10", 2, ["hip_ext_angle_deg"], "top"),
    ("pushup_side_12", 0, ["elbow_angle_deg", "hip_dev_frac"], "bottom"),
    ("pushup_side_01", 1, ["elbow_angle_deg", "hip_dev_frac"], "bottom"),
]


def build_handcheck(built: dict[str, dict]) -> list[dict]:
    rows = []
    for clip_id, rep_index, features, phase in HANDCHECK_PLAN:
        source = built[clip_id]
        timing = source["timings"][rep_index]
        exercise = source["label"]["exercise"]
        if phase == "bottom":
            frame_index = timing.start if exercise == "deadlift" else timing.extremum
        else:
            frame_index = timing.extremum if exercise == "deadlift" else timing.end
        frame = source["sidecar"]["frames"][frame_index]["keypoints"]
        values = handcheck_features(frame, features)
        rows.append(
            {
                "clip_id": clip_id,
                "rep_index": rep_index,
                "phase": phase,
                "frame": frame_index,
                "features": {k: round(v, 6) for k, v in values.items()},
            }
        )
    return rows


def generate(seed: int, out_dir: Path) -> dict:
    rng = random.Random(seed)
    poses_dir = out_dir / "poses"
    poses_dir.mkdir(parents=True, exist_ok=True)

    built: dict[str, dict] = {}
    labels: list[dict] = []
    for index, spec in enumerate(clip_specs(rng)):
        clip_rng = random.Random(seed * 1000 + index)
        result = build_clip(spec, clip_rng)
        built[spec.clip_id] = result
        labels.append(result["label"])
        _write_json(poses_dir / f"{spec.clip_id}.keypoints.json", result["sidecar"])

    for photo_id, clip_id, rep_index, phase in PHOTO_PLAN:
        photo = build_photo(photo_id, built[clip_id], rep_index, phase)
        labels.append(photo["label"])
        _write_json(poses_dir / f"{photo_id}.keypoints.json", photo["sidecar"])

    document = {
        "seed": seed,
        "generator": "evals/fixtures/generate_poses.py",
        "note": (
            "Ground truth comes from the generator's construction parameters, "
            "computed before any noise is applied."
        ),
        "clips": labels,
    }
    _write_json(out_dir / "labels.json", document)
    _write_json(out_dir / "labels_handcheck.json", {"reps": build_handcheck(built)})
    return document


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the FormCoach pose fixtures.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    document = generate(args.seed, args.out)
    valid = sum(1 for c in document["clips"] if c["valid"] and c["kind"] == "clip")
    photos = sum(1 for c in document["clips"] if c["kind"] == "photo")
    print(
        f"wrote {len(document['clips'])} fixtures "
        f"({valid} valid clips, {len(document['clips']) - valid - photos} invalid, {photos} photos)"
    )


if __name__ == "__main__":
    main()
