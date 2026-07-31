"""Offline :class:`PoseEstimator` — committed ``*.keypoints.json`` sidecars.

This is the default implementation: tests, evals and the CLI's fixture mode all
run through it, so every result is reproducible from files in the repository
with no network, no clock and no heavy dependency.

A sidecar looks like::

    {
      "fps": 30.0,
      "view": "side_left",                # optional: omit to exercise FR-7 inference
      "frames": [
        {"t_ms": 0, "keypoints": {"left_hip": {"x": 0.512, "y": 0.548, "conf": 0.97}}}
      ]
    }

``estimate`` accepts either the sidecar itself or the media file it sits beside
(``squat_01.mp4`` -> ``squat_01.mp4.keypoints.json`` or
``squat_01.keypoints.json``).
"""

from __future__ import annotations

import json
from pathlib import Path

from formcoach.adapters.pose import PoseEstimationError
from formcoach.models import DeclaredView, Keypoint, PoseFrame, PoseSequence, PoseSource

SIDECAR_SUFFIX = ".keypoints.json"


def sidecar_path(media_path: str | Path) -> Path:
    """Locate the sidecar for a media path, or raise a pointed error."""
    path = Path(media_path)
    if path.name.endswith(SIDECAR_SUFFIX):
        candidates = [path]
    else:
        candidates = [
            path.with_name(path.name + SIDECAR_SUFFIX),
            path.with_suffix(SIDECAR_SUFFIX),
            path.with_name(path.stem + SIDECAR_SUFFIX),
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PoseEstimationError(
        f"no keypoint sidecar for {path}; expected one of " + ", ".join(str(c) for c in candidates)
    )


def parse_sidecar(payload: dict, *, source_ref: str) -> PoseSequence:
    """Validate a decoded sidecar document into a :class:`PoseSequence`."""
    try:
        fps = float(payload["fps"])
        raw_frames = payload["frames"]
    except (KeyError, TypeError, ValueError) as exc:
        raise PoseEstimationError(f"malformed keypoint sidecar: {source_ref}") from exc
    if not raw_frames:
        raise PoseEstimationError(f"keypoint sidecar has no frames: {source_ref}")

    frames: list[PoseFrame] = []
    for index, raw in enumerate(raw_frames):
        keypoints = {
            name: Keypoint(x=float(kp["x"]), y=float(kp["y"]), conf=float(kp["conf"]))
            for name, kp in raw["keypoints"].items()
        }
        t_ms = float(raw.get("t_ms", index * 1000.0 / fps))
        frames.append(PoseFrame(t_ms=t_ms, keypoints=keypoints))

    declared = payload.get("view")
    return PoseSequence(
        fps=fps,
        frames=frames,
        declared_view=DeclaredView(declared) if declared else None,
        source_ref=source_ref,
        pose_source=PoseSource.FIXTURE,
    )


class FixturePoseEstimator:
    """Reads keypoints from a committed sidecar; never touches the network."""

    name = PoseSource.FIXTURE.value

    def estimate(
        self, media_path: str | Path, *, declared_view: DeclaredView | None = None
    ) -> PoseSequence:
        path = sidecar_path(media_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PoseEstimationError(f"keypoint sidecar is not valid JSON: {path}") from exc
        sequence = parse_sidecar(payload, source_ref=str(media_path))
        if declared_view is not None:
            sequence = sequence.model_copy(update={"declared_view": declared_view})
        return sequence
