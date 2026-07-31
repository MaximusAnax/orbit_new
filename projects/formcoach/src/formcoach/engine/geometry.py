"""Angle/ratio primitives and the nine named form features (FR-9 support).

All coordinates are normalized image coordinates: origin top-left, ``x``
increasing rightward, ``y`` increasing **downward**.  A lower body part
therefore has a *larger* ``y``.

Signed side-view features are reported in the **canonical anterior frame**
(SCOPE § Fault catalog): with ``facing = sign(mean(nose.x) - mean(mid_hip.x))``
a signed feature is multiplied by ``facing`` so positive always means
*anterior* (in front of the body).  That makes ``side_left`` and ``side_right``
clips interchangeable.

Features come in two sampling kinds:

``frame``
    computed from a single frame (``bottom`` / ``top`` phases);
``window``
    computed from a span of frames (``ascent_early`` / ``whole_rep`` phases).

Every feature returns ``None`` when it cannot be computed from the frames it
was given (degenerate geometry, zero-length segments); callers turn that into a
``not_assessed`` finding rather than a guess.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from formcoach.models import Keypoint, PoseFrame

#: The COCO-17 topology (Lin et al. 2014) — the pose contract for FormCoach.
COCO_KEYPOINTS: tuple[str, ...] = (
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

#: Virtual midpoints usable in ``primary_signal`` and feature definitions.
MIDPOINTS: dict[str, tuple[str, str]] = {
    "mid_shoulder": ("left_shoulder", "right_shoulder"),
    "mid_hip": ("left_hip", "right_hip"),
    "mid_knee": ("left_knee", "right_knee"),
    "mid_ankle": ("left_ankle", "right_ankle"),
    "mid_elbow": ("left_elbow", "right_elbow"),
    "mid_wrist": ("left_wrist", "right_wrist"),
    "mid_ear": ("left_ear", "right_ear"),
}

MIN_CONF = 0.3

Point = tuple[float, float]


# ------------------------------------------------------------------ keypoint access


def expand_keypoints(names: Sequence[str]) -> list[str]:
    """Expand virtual midpoint names into the real COCO keypoints they need."""
    out: list[str] = []
    for name in names:
        if name in MIDPOINTS:
            out.extend(MIDPOINTS[name])
        else:
            out.append(name)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in out:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def keypoint(frame: PoseFrame, name: str) -> Keypoint | None:
    return frame.keypoints.get(name)


def confidence(frame: PoseFrame, name: str) -> float:
    """Confidence of a real or virtual keypoint (midpoints take the minimum)."""
    if name in MIDPOINTS:
        a, b = MIDPOINTS[name]
        ka, kb = frame.keypoints.get(a), frame.keypoints.get(b)
        if ka is None or kb is None:
            return 0.0
        return min(ka.conf, kb.conf)
    kp = frame.keypoints.get(name)
    return kp.conf if kp is not None else 0.0


def point(frame: PoseFrame, name: str) -> Point | None:
    """Resolve a real or virtual keypoint to an ``(x, y)`` pair."""
    if name in MIDPOINTS:
        a, b = MIDPOINTS[name]
        ka, kb = frame.keypoints.get(a), frame.keypoints.get(b)
        if ka is None or kb is None:
            return None
        return ((ka.x + kb.x) / 2.0, (ka.y + kb.y) / 2.0)
    kp = frame.keypoints.get(name)
    return None if kp is None else (kp.x, kp.y)


def frame_visible(frame: PoseFrame, names: Sequence[str], min_conf: float = MIN_CONF) -> bool:
    """True when every (expanded) keypoint is present at >= ``min_conf``."""
    for name in expand_keypoints(names):
        kp = frame.keypoints.get(name)
        if kp is None or kp.conf < min_conf:
            return False
    return True


def signal_value(frame: PoseFrame, expression: str) -> float | None:
    """Evaluate a ``"<point>.<axis>"`` expression such as ``"mid_hip.y"``."""
    name, _, axis = expression.partition(".")
    if axis not in {"x", "y"}:
        raise ValueError(f"unsupported signal expression: {expression!r}")
    p = point(frame, name)
    if p is None:
        return None
    return p[0] if axis == "x" else p[1]


# ------------------------------------------------------------------- vector helpers


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angle_at(a: Point, vertex: Point, c: Point) -> float | None:
    """Interior angle ``a-vertex-c`` in degrees, in ``[0, 180]``."""
    v1 = (a[0] - vertex[0], a[1] - vertex[1])
    v2 = (c[0] - vertex[0], c[1] - vertex[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 <= 1e-9 or n2 <= 1e-9:
        return None
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def angle_from_vertical(a: Point, b: Point) -> float | None:
    """Unsigned angle of segment ``a -> b`` away from the image vertical."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if math.hypot(dx, dy) <= 1e-9:
        return None
    return math.degrees(math.atan2(abs(dx), abs(dy)))


def signed_perpendicular(p: Point, a: Point, b: Point) -> float | None:
    """Perpendicular distance of ``p`` from line ``a-b``, positive for larger y.

    "Larger y" means *below* the line in image coordinates, so a positive value
    is a sag rather than a pike regardless of which way the athlete faces.
    """
    ux, uy = b[0] - a[0], b[1] - a[1]
    norm = math.hypot(ux, uy)
    if norm <= 1e-9:
        return None
    cross = ux * (p[1] - a[1]) - uy * (p[0] - a[0])
    orient = 1.0 if ux >= 0 else -1.0
    return (cross / norm) * orient


def least_squares_slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Slope of ``y = m*x + c`` — a noise-robust stand-in for ``Δy / Δx``."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / sxx


# ------------------------------------------------------------------- feature context


@dataclass(frozen=True)
class FeatureContext:
    """Everything a feature needs beyond the frames it is sampled on."""

    facing: float = 1.0


def facing_sign(frames: Sequence[PoseFrame], min_conf: float = MIN_CONF) -> float:
    """``sign(mean(nose.x) - mean(mid_hip.x))`` over frames where both are visible.

    Returns ``+1.0`` when the athlete faces image-right (and on an exact tie),
    ``-1.0`` otherwise.  Multiplying a signed feature by this makes "positive"
    mean anterior in both side views.
    """
    noses: list[float] = []
    hips: list[float] = []
    for f in frames:
        if confidence(f, "nose") < min_conf or confidence(f, "mid_hip") < min_conf:
            continue
        nose = point(f, "nose")
        hip = point(f, "mid_hip")
        if nose is None or hip is None:
            continue
        noses.append(nose[0])
        hips.append(hip[0])
    if not noses:
        return 1.0
    delta = (sum(noses) / len(noses)) - (sum(hips) / len(hips))
    return -1.0 if delta < 0 else 1.0


# ------------------------------------------------------------------ frame features


def depth_ratio(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """``(knee_y - hip_y) / femur_len``; positive means the hip is above the knee."""
    hip = point(frame, "mid_hip")
    knee = point(frame, "mid_knee")
    if hip is None or knee is None:
        return None
    femur = distance(hip, knee)
    if femur <= 1e-6:
        return None
    return (knee[1] - hip[1]) / femur


def trunk_lean_deg(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """Angle of the mid-shoulder -> mid-hip line away from vertical, in degrees."""
    shoulder = point(frame, "mid_shoulder")
    hip = point(frame, "mid_hip")
    if shoulder is None or hip is None:
        return None
    return angle_from_vertical(shoulder, hip)


def fppa_deg(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """Frontal-plane projection angle, mean of both legs; positive = knee medial.

    For each leg the magnitude is the deviation of the hip->knee->ankle chain
    from straight; the sign is positive when the knee sits on the *midline*
    side of the hip-ankle line.  The two legs are averaged because the fault is
    a bilateral cave; averaging also halves the measurement noise.
    """
    mid_hip = point(frame, "mid_hip")
    if mid_hip is None:
        return None
    values: list[float] = []
    for side in ("left", "right"):
        hip = point(frame, f"{side}_hip")
        knee = point(frame, f"{side}_knee")
        ankle = point(frame, f"{side}_ankle")
        if hip is None or knee is None or ankle is None:
            return None
        interior = angle_at(hip, knee, ankle)
        if interior is None:
            return None
        magnitude = 180.0 - interior
        span_y = ankle[1] - hip[1]
        if abs(span_y) <= 1e-6:
            return None
        t = (knee[1] - hip[1]) / span_y
        line_x = hip[0] + t * (ankle[0] - hip[0])
        lateral_offset = knee[0] - line_x
        leg_sign = 1.0 if hip[0] >= mid_hip[0] else -1.0
        medial_offset = -lateral_offset * leg_sign
        values.append(magnitude if medial_offset >= 0 else -magnitude)
    return sum(values) / len(values)


def lateral_shift_frac(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """``|mid_hip.x - mid_ankle.x| / hip_width`` — sideways drift at the bottom."""
    hip = point(frame, "mid_hip")
    ankle = point(frame, "mid_ankle")
    left_hip = point(frame, "left_hip")
    right_hip = point(frame, "right_hip")
    if hip is None or ankle is None or left_hip is None or right_hip is None:
        return None
    hip_width = distance(left_hip, right_hip)
    if hip_width <= 1e-6:
        return None
    return abs(hip[0] - ankle[0]) / hip_width


def hip_ext_angle_deg(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """Shoulder-hip-knee angle; 180 degrees is a fully extended hip."""
    shoulder = point(frame, "mid_shoulder")
    hip = point(frame, "mid_hip")
    knee = point(frame, "mid_knee")
    if shoulder is None or hip is None or knee is None:
        return None
    return angle_at(shoulder, hip, knee)


def elbow_angle_deg(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """Shoulder-elbow-wrist angle; 180 degrees is a locked-out elbow."""
    shoulder = point(frame, "mid_shoulder")
    elbow = point(frame, "mid_elbow")
    wrist = point(frame, "mid_wrist")
    if shoulder is None or elbow is None or wrist is None:
        return None
    return angle_at(shoulder, elbow, wrist)


def hip_dev_frac(frame: PoseFrame, ctx: FeatureContext) -> float | None:
    """Signed mid-hip offset from the shoulder-ankle line / trunk length.

    Positive means the hip has drifted toward larger ``y`` (a sag); negative is
    a pike.  Sign comes from the image ``y`` axis, so it needs no anterior
    correction.
    """
    shoulder = point(frame, "mid_shoulder")
    hip = point(frame, "mid_hip")
    ankle = point(frame, "mid_ankle")
    if shoulder is None or hip is None or ankle is None:
        return None
    trunk_len = distance(shoulder, hip)
    if trunk_len <= 1e-6:
        return None
    perp = signed_perpendicular(hip, shoulder, ankle)
    if perp is None:
        return None
    return perp / trunk_len


# ----------------------------------------------------------------- window features


def hip_shoulder_rise_ratio(
    frames: Sequence[PoseFrame], ctx: FeatureContext
) -> tuple[float, int] | None:
    """``Δhip_y / Δshoulder_y`` over the frames given (the early ascent).

    Implemented as the least-squares slope of hip ``y`` against shoulder ``y``
    across the window rather than a two-frame difference: the two are identical
    on noiseless input, but the regression averages the estimator jitter over
    every frame of the window instead of resting on two samples.

    Returns ``(ratio, frame_offset)`` where the offset indexes ``frames``.
    """
    hip_ys: list[float] = []
    sh_ys: list[float] = []
    for f in frames:
        hip = point(f, "mid_hip")
        shoulder = point(f, "mid_shoulder")
        if hip is None or shoulder is None:
            return None
        hip_ys.append(hip[1])
        sh_ys.append(shoulder[1])
    slope = least_squares_slope(sh_ys, hip_ys)
    if slope is None:
        return None
    return slope, len(frames) - 1


def bar_drift_frac(frames: Sequence[PoseFrame], ctx: FeatureContext) -> tuple[float, int] | None:
    """Signed wrist-x deviation from mid-ankle-x over shank length.

    Evaluated on every frame of the rep; the returned value is the one with the
    largest magnitude (with its sign kept), together with the frame offset it
    was measured at.  Positive means the bar drifted *anterior*.
    """
    best: tuple[float, int] | None = None
    for idx, f in enumerate(frames):
        wrist = point(f, "mid_wrist")
        ankle = point(f, "mid_ankle")
        knee = point(f, "mid_knee")
        if wrist is None or ankle is None or knee is None:
            continue
        shank = distance(knee, ankle)
        if shank <= 1e-6:
            continue
        value = ((wrist[0] - ankle[0]) / shank) * ctx.facing
        if best is None or abs(value) > abs(best[0]):
            best = (value, idx)
    return best


# ------------------------------------------------------------------ feature registry


@dataclass(frozen=True)
class FeatureSpec:
    """How a named feature is sampled and computed."""

    name: str
    kind: str  # "frame" | "window"
    frame_fn: Callable[[PoseFrame, FeatureContext], float | None] | None = None
    window_fn: Callable[[Sequence[PoseFrame], FeatureContext], tuple[float, int] | None] | None = (
        None
    )


FEATURES: dict[str, FeatureSpec] = {
    spec.name: spec
    for spec in (
        FeatureSpec("depth_ratio", "frame", frame_fn=depth_ratio),
        FeatureSpec("trunk_lean_deg", "frame", frame_fn=trunk_lean_deg),
        FeatureSpec("fppa_deg", "frame", frame_fn=fppa_deg),
        FeatureSpec("lateral_shift_frac", "frame", frame_fn=lateral_shift_frac),
        FeatureSpec("hip_ext_angle_deg", "frame", frame_fn=hip_ext_angle_deg),
        FeatureSpec("elbow_angle_deg", "frame", frame_fn=elbow_angle_deg),
        FeatureSpec("hip_dev_frac", "frame", frame_fn=hip_dev_frac),
        FeatureSpec("hip_shoulder_rise_ratio", "window", window_fn=hip_shoulder_rise_ratio),
        FeatureSpec("bar_drift_frac", "window", window_fn=bar_drift_frac),
    )
}

#: Features that cannot be computed from a single frame (FR-15).
MULTI_FRAME_FEATURES: frozenset[str] = frozenset({"hip_shoulder_rise_ratio"})
