"""FR-7 — view resolution and visibility screening over a :class:`PoseSequence`.

Pure functions: the adapter has already turned a media file into keypoints; this
module decides *which view we are looking at* and *whether the clip is readable
at all*.  Nothing here reads the clock, the network or the filesystem.
"""

from __future__ import annotations

from collections.abc import Sequence

from formcoach.engine.geometry import MIN_CONF, confidence, distance, point
from formcoach.models import (
    DeclaredView,
    FormProfile,
    PoseFrame,
    PoseSequence,
    ScreeningResult,
    View,
    ViewResolution,
)

#: FR-7 step 3: shoulder width over torso length above this reads as a front view.
FRONT_VIEW_RATIO = 0.45

#: FR-7 screening: more than this fraction of bad frames rejects the clip.
MAX_BAD_FRAME_FRAC = 0.30

REJECT_INSUFFICIENT_VISIBILITY = "insufficient_visibility"

#: The COCO-17 keypoints whose mean confidence decides side handedness.
_HANDEDNESS_KEYPOINTS = ("shoulder", "hip", "knee", "ankle", "elbow", "wrist")


def _mean_side_confidence(frames: Sequence[PoseFrame], side: str) -> float:
    total = 0.0
    count = 0
    for frame in frames:
        for part in _HANDEDNESS_KEYPOINTS:
            kp = frame.keypoints.get(f"{side}_{part}")
            total += kp.conf if kp is not None else 0.0
            count += 1
    return total / count if count else 0.0


def resolve_handedness(frames: Sequence[PoseFrame]) -> View:
    """Pick the side whose keypoint set is more confident; exact tie -> left."""
    left = _mean_side_confidence(frames, "left")
    right = _mean_side_confidence(frames, "right")
    return View.SIDE_RIGHT if right > left else View.SIDE_LEFT


def shoulder_width_ratio(frames: Sequence[PoseFrame]) -> float | None:
    """``mean(|ls.x - rs.x|) / mean(torso_length)`` over frames with both shoulders."""
    widths: list[float] = []
    torsos: list[float] = []
    for frame in frames:
        ls = frame.keypoints.get("left_shoulder")
        rs = frame.keypoints.get("right_shoulder")
        if ls is None or rs is None or ls.conf < MIN_CONF or rs.conf < MIN_CONF:
            continue
        mid_shoulder = point(frame, "mid_shoulder")
        mid_hip = point(frame, "mid_hip")
        if mid_shoulder is None or mid_hip is None:
            continue
        torso = distance(mid_shoulder, mid_hip)
        if torso <= 1e-6:
            continue
        widths.append(abs(ls.x - rs.x))
        torsos.append(torso)
    if not widths:
        return None
    return (sum(widths) / len(widths)) / (sum(torsos) / len(torsos))


def resolve_view(
    sequence: PoseSequence, declared: DeclaredView | None = None
) -> ViewResolution:
    """FR-7 three-step view resolution.

    1. an explicit ``front`` / ``side_left`` / ``side_right`` wins outright;
    2. the ambiguous ``side`` resolves by handedness confidence;
    3. nothing declared falls back to the shoulder-width/torso-length test and
       then to handedness.

    The caller's declaration takes precedence over the sequence's own
    ``declared_view`` (a fixture sidecar may carry one, and may omit it).
    """
    value = declared if declared is not None else sequence.declared_view
    frames = sequence.frames

    if value in (DeclaredView.FRONT, DeclaredView.SIDE_LEFT, DeclaredView.SIDE_RIGHT):
        return ViewResolution(view=View(value.value), view_inferred=False)

    if value is DeclaredView.SIDE:
        return ViewResolution(view=resolve_handedness(frames), view_inferred=True)

    ratio = shoulder_width_ratio(frames)
    if ratio is not None and ratio > FRONT_VIEW_RATIO:
        return ViewResolution(view=View.FRONT, view_inferred=True)
    return ViewResolution(view=resolve_handedness(frames), view_inferred=True)


def _frame_flags(frame: PoseFrame, required: Sequence[str]) -> tuple[bool, bool]:
    """Return ``(has_low_confidence, has_out_of_frame)`` for one frame."""
    low = False
    out = False
    for name in required:
        kp = frame.keypoints.get(name)
        if kp is None:
            low = True
            out = True
            continue
        if kp.conf < MIN_CONF:
            low = True
        if not kp.in_frame:
            out = True
    return low, out


def screen(
    sequence: PoseSequence, profile: FormProfile, view: View
) -> ScreeningResult:
    """FR-7 screening — reject clips we cannot honestly read.

    A clip is rejected with ``insufficient_visibility`` when more than 30 % of
    frames have *any* required keypoint below confidence 0.3, or when more than
    30 % of frames have any required keypoint outside ``[0, 1]``.  The two
    fractions are counted independently so a partially out-of-frame body is
    caught even when confidences stay high.
    """
    required = profile.required_keypoints[view]
    total = len(sequence.frames)
    low_count = 0
    out_count = 0
    valid = 0
    for frame in sequence.frames:
        low, out = _frame_flags(frame, required)
        low_count += int(low)
        out_count += int(out)
        valid += int(not low and not out)

    low_frac = low_count / total if total else 1.0
    out_frac = out_count / total if total else 1.0
    rejected = low_frac > MAX_BAD_FRAME_FRAC or out_frac > MAX_BAD_FRAME_FRAC
    return ScreeningResult(
        accepted=not rejected,
        reject_reason=REJECT_INSUFFICIENT_VISIBILITY if rejected else None,
        frames_total=total,
        frames_valid=valid,
        low_confidence_frac=low_frac,
        out_of_frame_frac=out_frac,
    )


def visible_at(frame: PoseFrame, names: Sequence[str], min_conf: float = MIN_CONF) -> bool:
    """Whether every named keypoint (real or virtual) clears ``min_conf``."""
    return all(confidence(frame, name) >= min_conf for name in names)
