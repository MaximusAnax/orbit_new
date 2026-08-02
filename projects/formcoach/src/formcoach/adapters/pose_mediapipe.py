"""Live :class:`PoseEstimator` — MediaPipe Pose mapped onto COCO-17.

Activation is dependency-gated: the optional extra ``formcoach[pose]`` brings in
``mediapipe`` and ``opencv-python``.  Neither import happens at module load —
they are deferred into :meth:`MediaPipePoseEstimator.estimate` — so importing
this module never drags a heavy dependency into the offline path, and the
offline path never imports it at all.

BlazePose emits 33 landmarks (Bazarevsky et al. 2020); :data:`BLAZEPOSE_TO_COCO`
selects the 17 that make up the COCO topology (Lin et al. 2014), which is the
contract the rest of FormCoach is written against.  MediaPipe already reports
landmark ``x``/``y`` normalized to ``[0, 1]`` with ``y`` increasing downward,
which is exactly FormCoach's convention, and ``visibility`` becomes ``conf``.
"""

from __future__ import annotations

from pathlib import Path

from formcoach.adapters.pose import PoseEstimationError
from formcoach.models import DeclaredView, Keypoint, PoseFrame, PoseSequence, PoseSource

#: COCO-17 keypoint name -> BlazePose landmark index.
BLAZEPOSE_TO_COCO: dict[str, int] = {
    "nose": 0,
    "left_eye": 2,
    "right_eye": 5,
    "left_ear": 7,
    "right_ear": 8,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}

MISSING_DEPENDENCY = (
    "MediaPipe pose estimation needs the optional extra: "
    "install formcoach[pose] (mediapipe + opencv-python). "
    "Offline analysis works without it via committed .keypoints.json sidecars."
)


class MediaPipePoseEstimator:
    """Extracts COCO-17 keypoints from a video file with MediaPipe Pose."""

    name = PoseSource.MEDIAPIPE.value

    def __init__(
        self,
        *,
        model_complexity: int = 1,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self.model_complexity = model_complexity
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence

    @staticmethod
    def is_available() -> bool:
        """Whether the optional extra is importable in this interpreter."""
        try:  # pragma: no cover - depends on an optional heavy dependency
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
        except ImportError:
            return False
        return True

    def estimate(
        self, media_path: str | Path, *, declared_view: DeclaredView | None = None
    ) -> PoseSequence:
        try:  # pragma: no cover - exercised only with the optional extra installed
            import cv2
            import mediapipe as mp
        except ImportError as exc:  # pragma: no cover
            raise PoseEstimationError(MISSING_DEPENDENCY) from exc

        path = Path(media_path)
        if not path.is_file():  # pragma: no cover - trivial guard
            raise PoseEstimationError(f"no such media file: {path}")

        capture = cv2.VideoCapture(str(path))  # pragma: no cover
        if not capture.isOpened():  # pragma: no cover
            raise PoseEstimationError(f"could not open media file: {path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0  # pragma: no cover

        frames: list[PoseFrame] = []  # pragma: no cover
        try:  # pragma: no cover
            with mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=self.model_complexity,
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence,
            ) as pose:
                index = 0
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    frames.append(
                        PoseFrame(
                            t_ms=index * 1000.0 / fps,
                            keypoints=_map_landmarks(result.pose_landmarks),
                        )
                    )
                    index += 1
        finally:  # pragma: no cover
            capture.release()

        if not frames:  # pragma: no cover
            raise PoseEstimationError(f"no decodable frames in {path}")
        return PoseSequence(  # pragma: no cover
            fps=fps,
            frames=frames,
            declared_view=declared_view,
            source_ref=str(path),
            pose_source=PoseSource.MEDIAPIPE,
        )


def _map_landmarks(landmarks) -> dict[str, Keypoint]:
    """Project BlazePose's 33 landmarks onto the COCO-17 contract."""
    if landmarks is None:
        return {name: Keypoint(x=0.0, y=0.0, conf=0.0) for name in BLAZEPOSE_TO_COCO}
    points = landmarks.landmark
    mapped: dict[str, Keypoint] = {}
    for name, index in BLAZEPOSE_TO_COCO.items():
        landmark = points[index]
        mapped[name] = Keypoint(
            x=float(landmark.x),
            y=float(landmark.y),
            conf=float(max(0.0, min(1.0, getattr(landmark, "visibility", 0.0)))),
        )
    return mapped
