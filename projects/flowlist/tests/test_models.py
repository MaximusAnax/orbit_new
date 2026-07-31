"""DATA_MODEL invariants enforced by the Pydantic layer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from flowlist.engine.models import (
    CLIFF_THRESHOLD,
    SEAMLESS_THRESHOLD,
    Algorithm,
    ArcProfile,
    AudioFeatures,
    CoverageReport,
    FeatureSnapshot,
    FeatureSource,
    KeyRelation,
    PlaylistEntry,
    ReorderParams,
    ReorderRun,
    RunEntry,
    Track,
    Transition,
    TransitionWeights,
    validate_entry_set,
)
from flowlist_testkit import NOW, make_track
from pydantic import ValidationError


def features(**overrides: object) -> AudioFeatures:
    payload: dict[str, object] = {
        "track_id": "meta:abc",
        "source": FeatureSource.IMPORT,
        "analyzed_at": NOW,
    }
    payload.update(overrides)
    return AudioFeatures(**payload)  # type: ignore[arg-type]


def test_d12_thresholds_live_in_one_place() -> None:
    assert SEAMLESS_THRESHOLD == 0.70
    assert CLIFF_THRESHOLD == 0.40


# --------------------------------------------------------------------------- #
# 2.2 AudioFeatures
# --------------------------------------------------------------------------- #


def test_key_and_mode_are_set_together() -> None:
    features(key_pc=5, mode=1)
    features(key_pc=None, mode=None)
    with pytest.raises(ValidationError):
        features(key_pc=5)
    with pytest.raises(ValidationError):
        features(mode=1)


@pytest.mark.parametrize(
    "payload",
    [
        {"bpm": 39.0},
        {"bpm": 261.0},
        {"bpm": 0.0},
        {"key_pc": 12, "mode": 0},
        {"key_pc": -1, "mode": 0},
        {"key_pc": 0, "mode": 2},
        {"energy": 1.5},
        {"energy": -0.1},
        {"danceability": 2.0},
        {"loudness_db": 0.5},
        {"loudness_db": -61.0},
        {"valence": 1.2},
        {"confidence": 1.4},
    ],
)
def test_feature_ranges_enforced(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        features(**payload)


def test_feature_boundaries_accepted() -> None:
    features(bpm=40.0)
    features(bpm=260.0)
    features(energy=0.0, danceability=1.0)
    features(loudness_db=-60.0)
    features(loudness_db=0.0)
    features(key_pc=11, mode=1)


def test_features_snapshot_narrows_to_the_scoring_fields() -> None:
    row = features(bpm=128.0, key_pc=1, mode=1, energy=0.5, loudness_db=-6.0, valence=0.4)
    snapshot = row.snapshot()
    assert type(snapshot) is FeatureSnapshot
    assert snapshot.bpm == 128.0
    assert not hasattr(snapshot, "valence")


def test_features_are_frozen() -> None:
    row = features(bpm=120.0)
    with pytest.raises(ValidationError):
        row.bpm = 130.0  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 2.1 Track
# --------------------------------------------------------------------------- #


def test_track_requires_an_identity_source() -> None:
    make_track("meta:x")
    Track(id="file:1", title="", artist="", file_path="/a.flac", created_at=NOW)
    with pytest.raises(ValidationError):
        Track(id="meta:x", title="  ", artist="", created_at=NOW)


def test_track_duration_must_be_positive() -> None:
    make_track("meta:x", duration_ms=1)
    with pytest.raises(ValidationError):
        make_track("meta:x", duration_ms=0)
    with pytest.raises(ValidationError):
        make_track("meta:x", duration_ms=-5)


def test_track_display() -> None:
    assert make_track("meta:x", title="Night Drive", artist="Vera Lux").display == (
        "Vera Lux - Night Drive"
    )
    assert make_track("meta:x", title="Untitled", artist="").display == "Untitled"


# --------------------------------------------------------------------------- #
# 2.4 / 2.6 positions and run entries
# --------------------------------------------------------------------------- #


def test_entry_position_must_be_non_negative() -> None:
    PlaylistEntry(id="e", playlist_id="p", position=0, track_id="t")
    with pytest.raises(ValidationError):
        PlaylistEntry(id="e", playlist_id="p", position=-1, track_id="t")


def transition() -> Transition:
    blank = FeatureSnapshot()
    return Transition(
        score=0.5,
        components={"key": 0.5},
        weights=TransitionWeights(),
        key_relation=KeyRelation.UNKNOWN,
        features_from=blank,
        features_to=blank,
    )


def test_run_entry_transition_is_null_iff_first() -> None:
    RunEntry(run_id="r", position=0, entry_id="e0")
    RunEntry(run_id="r", position=1, entry_id="e1", transition=transition())
    with pytest.raises(ValidationError):
        RunEntry(run_id="r", position=0, entry_id="e0", transition=transition())
    with pytest.raises(ValidationError):
        RunEntry(run_id="r", position=1, entry_id="e1")


def test_validate_entry_set() -> None:
    validate_entry_set(["a", "b"], {"a", "b"})
    with pytest.raises(ValueError):
        validate_entry_set(["a", "a"], {"a", "b"})
    with pytest.raises(ValueError):
        validate_entry_set(["a", "c"], {"a", "b"})


def test_transition_score_must_stay_in_unit_range() -> None:
    with pytest.raises(ValidationError):
        Transition(
            score=1.2,
            components={},
            weights=TransitionWeights(),
            key_relation=KeyRelation.CLASH,
            features_from=FeatureSnapshot(),
            features_to=FeatureSnapshot(),
        )


# --------------------------------------------------------------------------- #
# 2.5 ReorderParams / ReorderRun
# --------------------------------------------------------------------------- #


def test_params_store_normalized_weights() -> None:
    """DATA_MODEL 2.5: runs persist exactly the weights the engine used."""
    params = ReorderParams(weights=TransitionWeights(key=7.0, bpm=7.0, energy=4.0, loudness=2.0))
    assert sum(params.weights.as_dict().values()) == pytest.approx(1.0)
    assert params.weights.key == pytest.approx(0.35)
    # Default weights already sum to 1 and are left untouched.
    assert ReorderParams().weights == TransitionWeights()


def test_params_round_trip_through_json() -> None:
    params = ReorderParams(
        seed=7,
        profile=ArcProfile.BUILD,
        start_entry="e1",
        end_entry=None,
        max_passes=25,
    )
    assert ReorderParams.model_validate_json(params.model_dump_json()) == params


def test_params_max_passes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ReorderParams(max_passes=0)


def test_run_is_frozen_append_only() -> None:
    run = ReorderRun(
        id="r1",
        playlist_id="p1",
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
        engine_version="0.1.0",
        algorithm=Algorithm.GREEDY_2OPT,
        seed=7,
        params=ReorderParams(),
        coverage=CoverageReport(tracks=0, full=0, fields=[]),
        score_mean_before=0.1,
        score_mean_after=0.9,
        score_min_before=0.0,
        score_min_after=0.5,
        score_total_before=1.0,
        score_total_after=9.0,
        seamless_before=0,
        seamless_after=5,
        cliff_before=3,
        cliff_after=0,
    )
    with pytest.raises(ValidationError):
        run.seed = 8  # type: ignore[misc]


def test_coverage_summary_reads_like_us2() -> None:
    coverage = CoverageReport(
        tracks=44,
        full=41,
        fields=["bpm", "key_pc", "energy", "loudness_db"],
        resolved={"bpm": 44, "key_pc": 41, "energy": 44, "loudness_db": 44},
        missing={"key_pc": 3},
    )
    assert coverage.summary == "41/44 tracks fully featured; 3 missing key_pc"
