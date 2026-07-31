"""FR-3, FR-4: feature resolution precedence, idempotence, coverage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from flowlist.engine.models import DEFAULT_PRECEDENCE, AudioFeatures, FeatureSource
from flowlist.engine.resolution import (
    COVERAGE_FIELDS,
    coverage_report,
    fully_resolved,
    missing_fields,
    resolve_features,
    resolve_many,
)
from flowlist_testkit import NOW


def row(source: FeatureSource, **values: object) -> AudioFeatures:
    payload: dict[str, object] = {"track_id": "t1", "source": source, "analyzed_at": NOW}
    payload.update(values)
    return AudioFeatures(**payload)  # type: ignore[arg-type]


def test_fr3_default_precedence_matches_the_spec() -> None:
    assert [s.value for s in DEFAULT_PRECEDENCE] == [
        "manual",
        "local_analysis",
        "streaming",
        "import",
        "fixture",
    ]


def test_fr3_resolution_is_field_wise() -> None:
    """Each field independently takes the highest-precedence non-null value."""
    resolved = resolve_features(
        [
            row(FeatureSource.IMPORT, bpm=122.0, energy=0.64, loudness_db=-8.1),
            row(FeatureSource.LOCAL_ANALYSIS, bpm=123.9, loudness_db=-8.9),
        ]
    )
    assert resolved.bpm == 123.9  # local_analysis wins
    assert resolved.loudness_db == -8.9
    assert resolved.energy == 0.64  # only import had it
    assert resolved.field_sources["bpm"] is FeatureSource.LOCAL_ANALYSIS
    assert resolved.field_sources["energy"] is FeatureSource.IMPORT


def test_fr4_manual_override_wins() -> None:
    resolved = resolve_features(
        [
            row(FeatureSource.FIXTURE, bpm=100.0, key_pc=0, mode=1),
            row(FeatureSource.STREAMING, bpm=110.0, key_pc=2, mode=1),
            row(FeatureSource.LOCAL_ANALYSIS, bpm=120.0),
            row(FeatureSource.MANUAL, bpm=128.0, key_pc=9, mode=0),
        ]
    )
    assert resolved.bpm == 128.0
    assert (resolved.key_pc, resolved.mode) == (9, 0)
    assert resolved.field_sources["bpm"] is FeatureSource.MANUAL
    assert resolved.field_sources["key_pc"] is FeatureSource.MANUAL


def test_fr3_key_and_mode_resolve_as_a_pair() -> None:
    """A high-precedence key can never be paired with a lower-precedence mode."""
    resolved = resolve_features(
        [
            row(FeatureSource.IMPORT, key_pc=0, mode=1),
            row(FeatureSource.MANUAL, key_pc=9, mode=0),
        ]
    )
    assert (resolved.key_pc, resolved.mode) == (9, 0)
    assert resolved.field_sources["mode"] is resolved.field_sources["key_pc"]


def test_fr3_key_falls_through_when_the_top_source_has_none() -> None:
    resolved = resolve_features(
        [
            row(FeatureSource.MANUAL, bpm=128.0),
            row(FeatureSource.IMPORT, key_pc=4, mode=0),
        ]
    )
    assert (resolved.key_pc, resolved.mode) == (4, 0)
    assert resolved.field_sources["key_pc"] is FeatureSource.IMPORT
    assert resolved.field_sources["bpm"] is FeatureSource.MANUAL


def test_fr3_precedence_is_configurable() -> None:
    rows = [
        row(FeatureSource.IMPORT, bpm=122.0),
        row(FeatureSource.LOCAL_ANALYSIS, bpm=123.9),
    ]
    flipped = (FeatureSource.IMPORT, FeatureSource.LOCAL_ANALYSIS)
    assert resolve_features(rows, flipped).bpm == 122.0


def test_fr3_sources_outside_the_precedence_list_are_ignored() -> None:
    """``--providers`` is a real filter, not a hint."""
    rows = [row(FeatureSource.STREAMING, bpm=110.0), row(FeatureSource.IMPORT, bpm=122.0)]
    assert resolve_features(rows, (FeatureSource.IMPORT,)).bpm == 122.0
    assert resolve_features(rows, (FeatureSource.MANUAL,)).bpm is None


def test_fr3_resolution_of_nothing_is_empty_not_an_error() -> None:
    resolved = resolve_features([], track_id="t1")
    assert resolved.track_id == "t1"
    assert resolved.bpm is None
    assert resolved.field_sources == {}
    assert resolved.confidence == 1.0


def test_fr3_confidence_comes_from_the_winning_source() -> None:
    resolved = resolve_features(
        [
            row(FeatureSource.IMPORT, bpm=122.0, confidence=1.0),
            row(FeatureSource.LOCAL_ANALYSIS, bpm=123.9, confidence=0.83),
        ]
    )
    assert resolved.confidence == 0.83


def test_fr3_resolution_is_pure_and_repeatable() -> None:
    rows = [row(FeatureSource.IMPORT, bpm=122.0), row(FeatureSource.MANUAL, energy=0.5)]
    first = resolve_features(rows)
    assert resolve_features(rows) == first
    assert resolve_features(list(reversed(rows))) == first


def test_fr3_resolve_many() -> None:
    rows = {
        "t1": [row(FeatureSource.IMPORT, bpm=120.0)],
        "t2": [],
    }
    resolved = resolve_many(rows)
    assert resolved["t1"].bpm == 120.0
    assert resolved["t1"].track_id == "t1"
    assert resolved["t2"].bpm is None
    assert resolved["t2"].track_id == "t2"


# --------------------------------------------------------------------------- #
# Coverage (FR-3)
# --------------------------------------------------------------------------- #


def test_fr3_coverage_report() -> None:
    resolved = resolve_many(
        {
            "full": [
                row(
                    FeatureSource.IMPORT,
                    bpm=120.0,
                    key_pc=0,
                    mode=1,
                    energy=0.5,
                    loudness_db=-8.0,
                )
            ],
            "no_key": [row(FeatureSource.IMPORT, bpm=120.0, energy=0.5, loudness_db=-8.0)],
            "empty": [],
        }
    )
    coverage = coverage_report(resolved)
    assert coverage.tracks == 3
    assert coverage.full == 1
    assert coverage.fields == list(COVERAGE_FIELDS)
    assert coverage.missing["key_pc"] == 2
    assert coverage.missing["bpm"] == 1
    assert coverage.resolved["bpm"] == 2
    assert coverage.per_track_missing["no_key"] == ["key_pc"]
    assert set(coverage.per_track_missing["empty"]) == set(COVERAGE_FIELDS)
    assert "full" not in coverage.per_track_missing


def test_fr3_coverage_omits_zero_counts() -> None:
    resolved = resolve_many(
        {
            "t": [
                row(
                    FeatureSource.IMPORT,
                    bpm=120.0,
                    key_pc=0,
                    mode=1,
                    energy=0.5,
                    loudness_db=-8.0,
                )
            ]
        }
    )
    coverage = coverage_report(resolved)
    assert coverage.missing == {}
    assert coverage.full == 1


def test_fr3_coverage_of_an_empty_playlist() -> None:
    coverage = coverage_report({})
    assert (coverage.tracks, coverage.full) == (0, 0)
    assert coverage.summary.startswith("0/0")


def test_fr3_missing_fields_and_fully_resolved() -> None:
    complete = resolve_features(
        [
            row(
                FeatureSource.IMPORT,
                bpm=120.0,
                key_pc=0,
                mode=1,
                energy=0.5,
                loudness_db=-8.0,
            )
        ]
    )
    assert missing_fields(complete) == []
    assert fully_resolved(complete)

    partial = resolve_features([row(FeatureSource.IMPORT, bpm=120.0)])
    assert set(missing_fields(partial)) == {"key_pc", "energy", "loudness_db"}
    assert not fully_resolved(partial)


def test_fr3_coverage_fields_exclude_mode_and_danceability() -> None:
    """``mode`` duplicates ``key_pc`` (2.2); danceability is off by default."""
    assert COVERAGE_FIELDS == ("bpm", "key_pc", "energy", "loudness_db")


def test_fr3_duplicate_source_rows_resolve_deterministically() -> None:
    """The composite PK forbids this, but resolution must not depend on order."""
    early = AudioFeatures(track_id="t1", source=FeatureSource.IMPORT, bpm=120.0, analyzed_at=NOW)
    late = AudioFeatures(
        track_id="t1",
        source=FeatureSource.IMPORT,
        bpm=130.0,
        analyzed_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert resolve_features([early, late]).bpm == 120.0
    assert resolve_features([early, late]).bpm == resolve_features([early]).bpm


def test_fr3_resolution_never_invents_a_value() -> None:
    """D10: missing stays missing; nothing is imputed."""
    resolved = resolve_features([row(FeatureSource.IMPORT, energy=0.5)])
    assert resolved.bpm is None
    assert resolved.key_pc is None and resolved.mode is None
    assert resolved.loudness_db is None
    with pytest.raises(KeyError):
        resolved.field_sources["bpm"]
