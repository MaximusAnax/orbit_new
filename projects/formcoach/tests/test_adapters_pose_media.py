"""Adapter contracts: pose estimation (FR-7) and media resolution (FR-2).

The offline implementations are the default and must work with no network and
no optional dependency; the live ones must exist as real code paths that refuse
politely rather than silently degrading.
"""

from __future__ import annotations

import json

import pytest
from formcoach.adapters import FixturePoseEstimator, LocalMediaResolver
from formcoach.adapters.media import MediaResolver
from formcoach.adapters.media_wger import (
    LIVE_ENV_VAR,
    MediaResolverDisabledError,
    WgerMediaResolver,
)
from formcoach.adapters.pose import PoseEstimationError, PoseEstimator
from formcoach.adapters.pose_fixture import parse_sidecar, sidecar_path
from formcoach.adapters.pose_mediapipe import (
    BLAZEPOSE_TO_COCO,
    MediaPipePoseEstimator,
    _map_landmarks,
)
from formcoach.engine.geometry import COCO_KEYPOINTS
from formcoach.models import DeclaredView, MediaAsset, MediaKind, MediaSource, PoseSource
from formcoach_synthetic import side_squat_sequence


def write_sidecar(path, sequence, *, view: str | None = "side_left"):
    payload = {
        "fps": sequence.fps,
        "frames": [
            {
                "t_ms": frame.t_ms,
                "keypoints": {
                    name: {"x": kp.x, "y": kp.y, "conf": kp.conf}
                    for name, kp in frame.keypoints.items()
                },
            }
            for frame in sequence.frames
        ],
    }
    if view is not None:
        payload["view"] = view
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------- pose adapters


def test_fr7_offline_estimator_satisfies_the_protocol():
    assert isinstance(FixturePoseEstimator(), PoseEstimator)
    assert FixturePoseEstimator().name == PoseSource.FIXTURE.value


def test_fr7_fixture_estimator_reads_a_committed_sidecar(tmp_path):
    sequence, _ = side_squat_sequence(reps=2)
    path = write_sidecar(tmp_path / "squat_01.keypoints.json", sequence)
    loaded = FixturePoseEstimator().estimate(path)
    assert loaded.fps == sequence.fps
    assert len(loaded.frames) == len(sequence.frames)
    assert loaded.declared_view is DeclaredView.SIDE_LEFT
    assert loaded.pose_source is PoseSource.FIXTURE
    assert loaded.frames[0].keypoints["left_hip"].x == pytest.approx(
        sequence.frames[0].keypoints["left_hip"].x
    )


def test_fr7_fixture_estimator_finds_the_sidecar_beside_a_media_file(tmp_path):
    sequence, _ = side_squat_sequence(reps=1)
    write_sidecar(tmp_path / "squat_01.mp4.keypoints.json", sequence)
    (tmp_path / "squat_01.mp4").write_bytes(b"not really a video")
    loaded = FixturePoseEstimator().estimate(tmp_path / "squat_01.mp4")
    assert len(loaded.frames) == len(sequence.frames)


def test_fr7_sidecar_may_omit_the_view_to_exercise_inference(tmp_path):
    sequence, _ = side_squat_sequence(reps=1)
    path = write_sidecar(tmp_path / "clip.keypoints.json", sequence, view=None)
    assert FixturePoseEstimator().estimate(path).declared_view is None


def test_fr7_caller_declaration_overrides_the_sidecar_view(tmp_path):
    sequence, _ = side_squat_sequence(reps=1)
    path = write_sidecar(tmp_path / "clip.keypoints.json", sequence)
    loaded = FixturePoseEstimator().estimate(path, declared_view=DeclaredView.SIDE)
    assert loaded.declared_view is DeclaredView.SIDE


def test_fr7_missing_sidecar_raises_a_pointed_error(tmp_path):
    with pytest.raises(PoseEstimationError) as excinfo:
        FixturePoseEstimator().estimate(tmp_path / "nope.mp4")
    assert "no keypoint sidecar" in str(excinfo.value)
    with pytest.raises(PoseEstimationError):
        sidecar_path(tmp_path / "nope.mp4")


def test_fr7_invalid_sidecar_json_raises(tmp_path):
    path = tmp_path / "clip.keypoints.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PoseEstimationError):
        FixturePoseEstimator().estimate(path)


def test_fr7_sidecar_without_frames_is_refused():
    with pytest.raises(PoseEstimationError):
        parse_sidecar({"fps": 30.0, "frames": []}, source_ref="x")
    with pytest.raises(PoseEstimationError):
        parse_sidecar({"frames": [{"keypoints": {}}]}, source_ref="x")


def test_fr7_sidecar_timestamps_default_to_the_frame_rate():
    payload = {
        "fps": 25.0,
        "frames": [{"keypoints": {"nose": {"x": 0.5, "y": 0.2, "conf": 0.9}}} for _ in range(3)],
    }
    sequence = parse_sidecar(payload, source_ref="x")
    assert [f.t_ms for f in sequence.frames] == pytest.approx([0.0, 40.0, 80.0])


def test_fr7_live_estimator_is_dependency_gated_not_stubbed():
    estimator = MediaPipePoseEstimator()
    assert isinstance(estimator, PoseEstimator)
    assert estimator.name == PoseSource.MEDIAPIPE.value
    if not MediaPipePoseEstimator.is_available():
        with pytest.raises(PoseEstimationError) as excinfo:
            estimator.estimate("clip.mp4")
        assert "formcoach[pose]" in str(excinfo.value)


def test_fr7_blazepose_maps_onto_the_full_coco_topology():
    assert set(BLAZEPOSE_TO_COCO) == set(COCO_KEYPOINTS)
    assert len(set(BLAZEPOSE_TO_COCO.values())) == 17


def test_fr7_missing_landmarks_map_to_zero_confidence_keypoints():
    mapped = _map_landmarks(None)
    assert set(mapped) == set(COCO_KEYPOINTS)
    assert all(kp.conf == 0.0 for kp in mapped.values())


def test_fr7_blazepose_landmarks_are_converted_to_keypoints():
    class Landmark:
        def __init__(self, index: int) -> None:
            self.x = 0.01 * index
            self.y = 0.02 * index
            self.visibility = 0.5

    class Landmarks:
        landmark = [Landmark(i) for i in range(33)]

    mapped = _map_landmarks(Landmarks())
    assert mapped["nose"].x == pytest.approx(0.0)
    assert mapped["left_hip"].x == pytest.approx(0.23)
    assert mapped["right_ankle"].y == pytest.approx(0.56)
    assert all(kp.conf == 0.5 for kp in mapped.values())


# --------------------------------------------------------------- media adapters


def test_fr2_local_resolver_satisfies_the_protocol(datasets):
    resolver = LocalMediaResolver(datasets.media_assets, "data")
    assert isinstance(resolver, MediaResolver)
    assert resolver.name == "local"


def test_fr2_local_resolver_groups_assets_by_exercise(datasets, tmp_path):
    resolver = LocalMediaResolver(datasets.media_assets, tmp_path)
    assets = resolver.resolve("barbell-back-squat")
    assert assets
    assert {a.exercise_id for a in assets} == {"barbell-back-squat"}
    assert resolver.resolve("not-an-exercise") == []


def test_fr2_local_resolver_verifies_files_and_never_fetches_urls(tmp_path):
    local = MediaAsset(
        id="x#1",
        exercise_id="x",
        kind=MediaKind.IMAGE,
        source=MediaSource.LOCAL,
        ref="media/x/0.svg",
        license="CC0-1.0",
    )
    url = MediaAsset(
        id="x#2",
        exercise_id="x",
        kind=MediaKind.IMAGE,
        source=MediaSource.URL,
        ref="https://example.invalid/never-fetched.png",
        license="CC-BY-4.0",
        attribution="someone",
    )
    resolver = LocalMediaResolver([local, url], tmp_path)
    assert resolver.verify(local) is False  # the file does not exist yet
    target = tmp_path / "media" / "x" / "0.svg"
    target.parent.mkdir(parents=True)
    target.write_text("<svg/>", encoding="utf-8")
    assert resolver.verify(local) is True
    # An unreachable host still verifies: url assets are schema-checked only.
    assert resolver.verify(url) is True


def test_fr2_live_media_resolver_refuses_to_run_unless_enabled(monkeypatch, datasets):
    monkeypatch.delenv(LIVE_ENV_VAR, raising=False)
    resolver = WgerMediaResolver(datasets.media_assets)
    assert WgerMediaResolver.is_enabled() is False
    assert isinstance(resolver, MediaResolver)
    with pytest.raises(MediaResolverDisabledError) as excinfo:
        resolver.resolve("barbell-back-squat")
    assert LIVE_ENV_VAR in str(excinfo.value)
    with pytest.raises(MediaResolverDisabledError):
        resolver.verify(datasets.media_assets[0])


def test_fr2_live_media_resolver_falls_back_to_the_manifest_when_offline(monkeypatch, datasets):
    monkeypatch.setenv(LIVE_ENV_VAR, "1")
    resolver = WgerMediaResolver(datasets.media_assets, base_url="https://example.invalid/api")

    def explode(*_args, **_kwargs):
        raise OSError("no network in tests")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    assert resolver.resolve("barbell-back-squat") == datasets.assets_for("barbell-back-squat")
    assert resolver.verify(datasets.assets_for("barbell-back-squat")[1]) is False


def test_fr2_live_media_resolver_only_verifies_url_assets(monkeypatch, datasets):
    monkeypatch.setenv(LIVE_ENV_VAR, "1")
    resolver = WgerMediaResolver(datasets.media_assets)
    local = next(a for a in datasets.media_assets if a.source is MediaSource.LOCAL)
    assert resolver.verify(local) is False


def test_fr2_live_media_resolver_reads_its_base_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("FORMCOACH_WGER_BASE_URL", "https://example.test/api/v2/")
    assert WgerMediaResolver().base_url == "https://example.test/api/v2"
