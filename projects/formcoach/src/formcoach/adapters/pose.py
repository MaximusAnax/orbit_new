"""The ``PoseEstimator`` capability interface (FR-7).

Turning pixels into keypoints is the one place FormCoach depends on something
heavy and external, so it sits behind this Protocol.  The offline
implementation (:mod:`formcoach.adapters.pose_fixture`) reads committed
``*.keypoints.json`` sidecars and is what every test and eval uses; the live
implementation (:mod:`formcoach.adapters.pose_mediapipe`) wraps MediaPipe Pose
and is only importable when the ``pose`` extra is installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from formcoach.models import DeclaredView, PoseSequence


class PoseEstimationError(RuntimeError):
    """Raised when a media file cannot be turned into keypoints."""


@runtime_checkable
class PoseEstimator(Protocol):
    """Convert a media file into a :class:`PoseSequence` of COCO-17 keypoints."""

    #: Value stored on ``FormAnalysis.pose_source``.
    name: str

    def estimate(
        self, media_path: str | Path, *, declared_view: DeclaredView | None = None
    ) -> PoseSequence:
        """Return normalized COCO-17 keypoints for every frame of ``media_path``.

        ``declared_view`` is passed through onto the sequence; FR-7's view
        resolution runs in the engine, not in the adapter, so an adapter never
        decides what it is looking at.
        """
        ...
