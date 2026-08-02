"""Synthetic COCO-17 skeletons with analytically exact geometry.

These builders exist so the engine tests can assert *numbers*, not just shapes:
each one is parameterized directly by the quantity the feature under test
measures, so the expected value is known in closed form rather than read back
out of the code being tested.

* ``side_squat_sequence`` places the hip so that ``depth_ratio == cos(beta)``
  and the trunk so that ``trunk_lean_deg == tau`` exactly.
* ``front_squat_frame`` uses a 3-4-5 style triangle whose frontal-plane
  projection angle is an exact ``atan``-free value.
* ``deadlift_sequence`` moves hip and shoulder along the same raised-cosine, so
  ``hip_shoulder_rise_ratio`` equals the ratio of their travels exactly, and
  places the wrist so ``bar_drift_frac`` equals its parameter exactly.
* ``pushup_sequence`` builds the arm from fixed-length segments rotated by the
  requested elbow angle, and offsets the hip along the normal of the
  shoulder-ankle line so ``hip_dev_frac`` equals its parameter exactly.

They are *not* the eval fixtures — the eval owns a separately generated,
noise-hardened corpus.  These are unit-test instruments.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from formcoach.models import DeclaredView, Keypoint, PoseFrame, PoseSequence

SHANK = 0.16
FEMUR = 0.18
TRUNK = 0.24
UPPER_ARM = 0.15
FOREARM = 0.15
LATERAL_OFFSET = 0.006  # how far the far-side joints sit behind the near ones

Point = tuple[float, float]


def raised_cosine(phase: float) -> float:
    """0 at the ends of a rep, 1 at its extremum."""
    return (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0


def _pair(name: str, point: Point, conf: float, out: dict[str, Keypoint]) -> None:
    """Emit ``left_<name>``/``right_<name>`` around a mid-line point."""
    out[f"left_{name}"] = Keypoint(x=point[0] - LATERAL_OFFSET / 2, y=point[1], conf=conf)
    out[f"right_{name}"] = Keypoint(x=point[0] + LATERAL_OFFSET / 2, y=point[1], conf=conf)


def _head(nose: Point, conf: float, out: dict[str, Keypoint]) -> None:
    out["nose"] = Keypoint(x=nose[0], y=nose[1], conf=conf)
    out["left_eye"] = Keypoint(x=nose[0] - 0.01, y=nose[1] - 0.01, conf=conf)
    out["right_eye"] = Keypoint(x=nose[0] + 0.01, y=nose[1] - 0.01, conf=conf)
    out["left_ear"] = Keypoint(x=nose[0] - 0.02, y=nose[1] - 0.005, conf=conf)
    out["right_ear"] = Keypoint(x=nose[0] + 0.02, y=nose[1] - 0.005, conf=conf)


# ------------------------------------------------------------------ side squat


@dataclass
class SquatTruth:
    """Exact feature values injected into a synthetic squat clip."""

    depth_ratio: float
    trunk_lean_deg: float
    reps: int
    frames_per_rep: int
    extremum_frames: list[int] = field(default_factory=list)


def side_squat_frame(
    beta_deg: float,
    tau_deg: float,
    shank_deg: float,
    *,
    facing: float = 1.0,
    conf: float = 0.95,
) -> dict[str, Keypoint]:
    """One side-view squat frame.

    ``beta_deg`` is the femur's angle from vertical, which makes
    ``depth_ratio == cos(beta)``; ``tau_deg`` is the trunk's angle from
    vertical, which is exactly ``trunk_lean_deg``.
    """
    beta, tau, shank_angle = map(math.radians, (beta_deg, tau_deg, shank_deg))
    ankle = (0.50, 0.90)
    knee = (
        ankle[0] + SHANK * math.sin(shank_angle) * facing,
        ankle[1] - SHANK * math.cos(shank_angle),
    )
    hip = (knee[0] - FEMUR * math.sin(beta) * facing, knee[1] - FEMUR * math.cos(beta))
    shoulder = (hip[0] + TRUNK * math.sin(tau) * facing, hip[1] - TRUNK * math.cos(tau))
    elbow = (shoulder[0] - 0.02 * facing, shoulder[1] + 0.11)
    wrist = (shoulder[0] - 0.05 * facing, shoulder[1] + 0.02)

    out: dict[str, Keypoint] = {}
    _pair("ankle", ankle, conf, out)
    _pair("knee", knee, conf, out)
    _pair("hip", hip, conf, out)
    _pair("shoulder", shoulder, conf, out)
    _pair("elbow", elbow, conf, out)
    _pair("wrist", wrist, conf, out)
    _head((shoulder[0] + 0.04 * facing, shoulder[1] - 0.10), conf, out)
    return out


def side_squat_sequence(
    *,
    reps: int = 3,
    fps: float = 30.0,
    frames_per_rep: int = 60,
    bottom_beta_deg: float = 100.0,
    bottom_trunk_deg: float = 35.0,
    standing_beta_deg: float = 5.0,
    standing_trunk_deg: float = 5.0,
    facing: float = 1.0,
    declared_view: DeclaredView | None = DeclaredView.SIDE_LEFT,
    noise_sigma: float = 0.0,
    seed: int = 0,
    conf: float = 0.95,
) -> tuple[PoseSequence, SquatTruth]:
    """A clean multi-rep side-view squat with known bottom-position geometry."""
    rng = random.Random(seed)
    frames: list[PoseFrame] = []
    extrema: list[int] = []
    for rep in range(reps):
        for i in range(frames_per_rep):
            phase = i / frames_per_rep
            weight = raised_cosine(phase)
            beta = standing_beta_deg + (bottom_beta_deg - standing_beta_deg) * weight
            tau = standing_trunk_deg + (bottom_trunk_deg - standing_trunk_deg) * weight
            shank = 20.0 * weight
            keypoints = side_squat_frame(beta, tau, shank, facing=facing, conf=conf)
            if noise_sigma:
                keypoints = _jitter(keypoints, rng, noise_sigma)
            index = rep * frames_per_rep + i
            frames.append(PoseFrame(t_ms=index * 1000.0 / fps, keypoints=keypoints))
        extrema.append(rep * frames_per_rep + frames_per_rep // 2)
    # A trailing standing frame so the last rep has a closing boundary.
    frames.append(
        PoseFrame(
            t_ms=len(frames) * 1000.0 / fps,
            keypoints=side_squat_frame(
                standing_beta_deg, standing_trunk_deg, 0.0, facing=facing, conf=conf
            ),
        )
    )
    truth = SquatTruth(
        depth_ratio=math.cos(math.radians(bottom_beta_deg)),
        trunk_lean_deg=bottom_trunk_deg,
        reps=reps,
        frames_per_rep=frames_per_rep,
        extremum_frames=extrema,
    )
    sequence = PoseSequence(
        fps=fps,
        frames=frames,
        declared_view=declared_view,
        source_ref="synthetic-squat",
    )
    return sequence, truth


def _jitter(
    keypoints: dict[str, Keypoint], rng: random.Random, sigma: float
) -> dict[str, Keypoint]:
    return {
        name: Keypoint(x=kp.x + rng.gauss(0.0, sigma), y=kp.y + rng.gauss(0.0, sigma), conf=kp.conf)
        for name, kp in keypoints.items()
    }


# ----------------------------------------------------------------- front squat


#: A knee displaced 0.10 medially over a 0.20 half-leg span gives an interior
#: knee angle whose cosine is exactly -0.6; the deviation from straight — which
#: is what ``fppa_deg`` reports — is therefore 180 - acos(-0.6) = 53.13 degrees.
FRONT_VALGUS_DEG = 180.0 - math.degrees(math.acos(-0.6))


def front_squat_frame(
    knee_medial_offset: float = 0.0, *, conf: float = 0.95
) -> dict[str, Keypoint]:
    """A front-view squat bottom position with a symmetric knee offset.

    With ``knee_medial_offset = 0.10`` both legs form the exact triangle above,
    so ``fppa_deg == FRONT_VALGUS_DEG``.
    """
    out: dict[str, Keypoint] = {}
    out["left_hip"] = Keypoint(x=0.45, y=0.50, conf=conf)
    out["right_hip"] = Keypoint(x=0.55, y=0.50, conf=conf)
    out["left_knee"] = Keypoint(x=0.45 + knee_medial_offset, y=0.70, conf=conf)
    out["right_knee"] = Keypoint(x=0.55 - knee_medial_offset, y=0.70, conf=conf)
    out["left_ankle"] = Keypoint(x=0.45, y=0.90, conf=conf)
    out["right_ankle"] = Keypoint(x=0.55, y=0.90, conf=conf)
    out["left_shoulder"] = Keypoint(x=0.42, y=0.26, conf=conf)
    out["right_shoulder"] = Keypoint(x=0.58, y=0.26, conf=conf)
    out["left_elbow"] = Keypoint(x=0.38, y=0.38, conf=conf)
    out["right_elbow"] = Keypoint(x=0.62, y=0.38, conf=conf)
    out["left_wrist"] = Keypoint(x=0.40, y=0.28, conf=conf)
    out["right_wrist"] = Keypoint(x=0.60, y=0.28, conf=conf)
    _head((0.50, 0.16), conf, out)
    return out


def front_squat_sequence(
    *,
    reps: int = 3,
    fps: float = 30.0,
    frames_per_rep: int = 60,
    knee_medial_offset: float = 0.0,
    declared_view: DeclaredView | None = DeclaredView.FRONT,
    conf: float = 0.95,
) -> PoseSequence:
    """Front-view squat clip.

    The bottom position (``weight == 1``) is exactly :func:`front_squat_frame`,
    so the injected FPPA is exact where the rule samples it; standing lifts the
    hip by 0.18 and the knee by 0.09 with the ankles pinned to the floor, which
    keeps the knee on the hip-ankle mid-height the exact triangle assumes.
    """
    hip_rise, knee_rise = 0.18, 0.09
    frames: list[PoseFrame] = []
    for rep in range(reps):
        for i in range(frames_per_rep):
            weight = raised_cosine(i / frames_per_rep)
            base = front_squat_frame(knee_medial_offset * weight, conf=conf)
            moved: dict[str, Keypoint] = {}
            for name, kp in base.items():
                if name.endswith("_ankle"):
                    lift = 0.0
                elif name.endswith("_knee"):
                    lift = knee_rise * (1.0 - weight)
                else:
                    lift = hip_rise * (1.0 - weight)
                moved[name] = Keypoint(x=kp.x, y=kp.y - lift, conf=kp.conf)
            index = rep * frames_per_rep + i
            frames.append(PoseFrame(t_ms=index * 1000.0 / fps, keypoints=moved))
    frames.append(
        PoseFrame(
            t_ms=len(frames) * 1000.0 / fps,
            keypoints={
                name: Keypoint(
                    x=kp.x,
                    y=kp.y
                    - (
                        0.0
                        if name.endswith("_ankle")
                        else knee_rise
                        if name.endswith("_knee")
                        else hip_rise
                    ),
                    conf=kp.conf,
                )
                for name, kp in front_squat_frame(0.0, conf=conf).items()
            },
        )
    )
    return PoseSequence(
        fps=fps, frames=frames, declared_view=declared_view, source_ref="synthetic-front"
    )


# -------------------------------------------------------------------- deadlift


@dataclass
class DeadliftTruth:
    hip_shoulder_rise_ratio: float
    bar_drift_frac: float
    hip_ext_angle_deg: float
    reps: int
    frames_per_rep: int


#: Fraction of the ascent that the ``ascent_early`` phase samples.
_ASCENT_EARLY_FRAC = 0.4

SHOULDER_TRAVEL = 0.20
HIP_TRAVEL = 0.24


def _hip_travel_profile(weight: float, rise_ratio: float) -> float:
    """Fraction of the hip's total travel completed at raised-cosine ``weight``.

    Piecewise linear in ``weight``: slope ``a`` while the early ascent is being
    sampled, then whatever slope closes the remaining travel.  Because both the
    hip and the shoulder are affine in ``weight`` across the sampled window, the
    least-squares slope of hip against shoulder there is exactly
    ``a * HIP_TRAVEL / SHOULDER_TRAVEL == rise_ratio``.
    """
    a = rise_ratio * SHOULDER_TRAVEL / HIP_TRAVEL
    knot = 0.4
    if weight <= knot:
        return a * weight
    b = (1.0 - knot * a) / (1.0 - knot)
    return knot * a + (weight - knot) * b


def deadlift_sequence(
    *,
    reps: int = 3,
    fps: float = 30.0,
    frames_per_rep: int = 60,
    rise_ratio: float = 1.0,
    bar_drift: float = 0.0,
    lockout_deg: float = 178.0,
    facing: float = 1.0,
    declared_view: DeclaredView | None = DeclaredView.SIDE_RIGHT,
    conf: float = 0.95,
) -> tuple[PoseSequence, DeadliftTruth]:
    """A side-view deadlift whose early-ascent rise ratio is exactly ``rise_ratio``.

    The shoulder rises linearly in the rep's raised-cosine weight and the hip
    rises with the piecewise profile above, so the measured hip/shoulder rise
    ratio over the sampled early ascent is exactly the requested one while both
    joints still finish the pull in a physically sane lockout.
    """
    ankle = (0.50, 0.90)
    knee_bottom = (0.50 + 0.05 * facing, 0.72)
    knee_top = (0.50, 0.70)
    hip_bottom_y = 0.66
    shoulder_bottom_y = 0.42
    delta = math.radians(180.0 - lockout_deg)

    frames: list[PoseFrame] = []
    for rep in range(reps):
        for i in range(frames_per_rep):
            weight = raised_cosine(i / frames_per_rep)
            hip_y = hip_bottom_y - HIP_TRAVEL * _hip_travel_profile(weight, rise_ratio)
            shoulder_y = shoulder_bottom_y - SHOULDER_TRAVEL * weight
            knee = (
                knee_bottom[0] + (knee_top[0] - knee_bottom[0]) * weight,
                knee_bottom[1] + (knee_top[1] - knee_bottom[1]) * weight,
            )
            hip = (0.50 - (0.05 - 0.05 * weight) * facing, hip_y)
            # The trunk is placed by angle so the shoulder keeps its exact
            # height: at the top the knee sits directly below the hip, making
            # the shoulder-hip-knee angle exactly `lockout_deg`.
            lean = delta * weight
            trunk_len = max(1e-6, hip_y - shoulder_y) / math.cos(lean)
            shoulder = (hip[0] + trunk_len * math.sin(lean) * facing, shoulder_y)
            shank = math.dist(knee, ankle)
            wrist = (ankle[0] + bar_drift * shank * facing, shoulder[1] + 0.30)
            elbow = (wrist[0], (shoulder[1] + wrist[1]) / 2.0)

            out: dict[str, Keypoint] = {}
            _pair("ankle", ankle, conf, out)
            _pair("knee", knee, conf, out)
            _pair("hip", hip, conf, out)
            _pair("shoulder", shoulder, conf, out)
            _pair("elbow", elbow, conf, out)
            _pair("wrist", wrist, conf, out)
            _head((shoulder[0] + 0.05 * facing, shoulder[1] - 0.08), conf, out)
            index = rep * frames_per_rep + i
            frames.append(PoseFrame(t_ms=index * 1000.0 / fps, keypoints=out))

    truth = DeadliftTruth(
        hip_shoulder_rise_ratio=rise_ratio,
        bar_drift_frac=bar_drift,
        hip_ext_angle_deg=lockout_deg,
        reps=reps,
        frames_per_rep=frames_per_rep,
    )
    sequence = PoseSequence(
        fps=fps, frames=frames, declared_view=declared_view, source_ref="synthetic-deadlift"
    )
    return sequence, truth


# --------------------------------------------------------------------- push-up


@dataclass
class PushupTruth:
    elbow_angle_bottom_deg: float
    elbow_angle_top_deg: float
    hip_dev_frac: float
    reps: int
    frames_per_rep: int


def _rotate(vector: Point, radians: float) -> Point:
    cos, sin = math.cos(radians), math.sin(radians)
    return (
        vector[0] * cos - vector[1] * sin,
        vector[0] * sin + vector[1] * cos,
    )


def pushup_frame(
    elbow_angle_deg: float, hip_dev_frac: float, *, conf: float = 0.95
) -> dict[str, Keypoint]:
    """One side-view push-up frame with an exact elbow angle and hip offset."""
    shoulder_drop = 0.14 * (1.0 - (elbow_angle_deg - 40.0) / 140.0)
    shoulder = (0.30, 0.58 + max(0.0, shoulder_drop))
    ankle = (0.86, 0.86)

    span = (ankle[0] - shoulder[0], ankle[1] - shoulder[1])
    span_len = math.hypot(*span)
    unit = (span[0] / span_len, span[1] / span_len)
    normal = (-unit[1], unit[0])
    if normal[1] < 0:  # keep "positive normal" pointing toward larger y (a sag)
        normal = (-normal[0], -normal[1])
    base_len = 0.45 * span_len
    frac = max(-0.95, min(0.95, hip_dev_frac))
    deviation = frac * base_len / math.sqrt(1.0 - frac * frac)
    mid = (shoulder[0] + unit[0] * base_len, shoulder[1] + unit[1] * base_len)
    hip = (mid[0] + normal[0] * deviation, mid[1] + normal[1] * deviation)

    upper = (-0.20, 0.98)
    upper_len = math.hypot(*upper)
    upper_unit = (upper[0] / upper_len, upper[1] / upper_len)
    elbow = (shoulder[0] + UPPER_ARM * upper_unit[0], shoulder[1] + UPPER_ARM * upper_unit[1])
    back_to_shoulder = (-upper_unit[0], -upper_unit[1])
    forearm_dir = _rotate(back_to_shoulder, math.radians(elbow_angle_deg))
    wrist = (elbow[0] + FOREARM * forearm_dir[0], elbow[1] + FOREARM * forearm_dir[1])

    out: dict[str, Keypoint] = {}
    _pair("shoulder", shoulder, conf, out)
    _pair("elbow", elbow, conf, out)
    _pair("wrist", wrist, conf, out)
    _pair("hip", hip, conf, out)
    _pair("knee", ((hip[0] + ankle[0]) / 2, (hip[1] + ankle[1]) / 2), conf, out)
    _pair("ankle", ankle, conf, out)
    _head((shoulder[0] - 0.06, shoulder[1] - 0.05), conf, out)
    return out


def pushup_sequence(
    *,
    reps: int = 3,
    fps: float = 30.0,
    frames_per_rep: int = 60,
    bottom_elbow_deg: float = 80.0,
    top_elbow_deg: float = 175.0,
    hip_dev_frac: float = 0.0,
    declared_view: DeclaredView | None = DeclaredView.SIDE_LEFT,
    conf: float = 0.95,
) -> tuple[PoseSequence, PushupTruth]:
    """A side-view push-up clip with exact elbow angles at both extremes."""
    frames: list[PoseFrame] = []
    for rep in range(reps):
        for i in range(frames_per_rep):
            weight = raised_cosine(i / frames_per_rep)
            angle = top_elbow_deg + (bottom_elbow_deg - top_elbow_deg) * weight
            index = rep * frames_per_rep + i
            frames.append(
                PoseFrame(
                    t_ms=index * 1000.0 / fps,
                    keypoints=pushup_frame(angle, hip_dev_frac, conf=conf),
                )
            )
    frames.append(
        PoseFrame(
            t_ms=len(frames) * 1000.0 / fps,
            keypoints=pushup_frame(top_elbow_deg, hip_dev_frac, conf=conf),
        )
    )
    truth = PushupTruth(
        elbow_angle_bottom_deg=bottom_elbow_deg,
        elbow_angle_top_deg=top_elbow_deg,
        hip_dev_frac=hip_dev_frac,
        reps=reps,
        frames_per_rep=frames_per_rep,
    )
    sequence = PoseSequence(
        fps=fps, frames=frames, declared_view=declared_view, source_ref="synthetic-pushup"
    )
    return sequence, truth


# ---------------------------------------------------------------- degradations


def occlude(
    sequence: PoseSequence,
    names: list[str],
    *,
    fraction: float,
    conf: float = 0.1,
) -> PoseSequence:
    """Drop the confidence of ``names`` on the first ``fraction`` of frames."""
    count = round(len(sequence.frames) * fraction)
    frames = []
    for index, frame in enumerate(sequence.frames):
        if index < count:
            kps = {
                name: (Keypoint(x=kp.x, y=kp.y, conf=conf) if name in names else kp)
                for name, kp in frame.keypoints.items()
            }
        else:
            kps = dict(frame.keypoints)
        frames.append(PoseFrame(t_ms=frame.t_ms, keypoints=kps))
    return sequence.model_copy(update={"frames": frames})


def push_out_of_frame(
    sequence: PoseSequence, names: list[str], *, fraction: float, offset: float = 0.4
) -> PoseSequence:
    """Move ``names`` outside ``[0, 1]`` on the first ``fraction`` of frames."""
    count = round(len(sequence.frames) * fraction)
    frames = []
    for index, frame in enumerate(sequence.frames):
        if index < count:
            kps = {
                name: (Keypoint(x=kp.x, y=kp.y + offset, conf=kp.conf) if name in names else kp)
                for name, kp in frame.keypoints.items()
            }
        else:
            kps = dict(frame.keypoints)
        frames.append(PoseFrame(t_ms=frame.t_ms, keypoints=kps))
    return sequence.model_copy(update={"frames": frames})
