"""External-capability adapters: pose estimation and media resolution.

Only the Protocols and the *offline* implementations are re-exported here.  The
live adapters (:mod:`formcoach.adapters.pose_mediapipe`,
:mod:`formcoach.adapters.media_wger`) must be imported explicitly, so nothing on
the default path can pull in an optional heavy dependency or reach the network.
"""

from formcoach.adapters.media import MediaResolver
from formcoach.adapters.media_local import LocalMediaResolver
from formcoach.adapters.pose import PoseEstimationError, PoseEstimator
from formcoach.adapters.pose_fixture import FixturePoseEstimator

__all__ = [
    "FixturePoseEstimator",
    "LocalMediaResolver",
    "MediaResolver",
    "PoseEstimationError",
    "PoseEstimator",
]
