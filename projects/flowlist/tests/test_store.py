"""FR-11, FR-12 and DATA_MODEL 2.x storage invariants, on both backends."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from flowlist.engine.models import (
    Algorithm,
    AudioFeatures,
    CoverageReport,
    FeatureSnapshot,
    FeatureSource,
    KeyRelation,
    PlaylistSource,
    ReorderParams,
    ReorderRun,
    RunEntry,
    Transition,
    TransitionWeights,
)
from flowlist.errors import (
    PlaylistHasRunsError,
    UnknownPlaylistError,
    UnknownRunError,
    UnknownTrackError,
)
from flowlist.store.base import Repository
from flowlist.store.memory import InMemoryRepository
from flowlist.store.sqlite import SqliteRepository
from flowlist_testkit import NOW, make_track


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Repository]:
    """Both backends must satisfy the same contract."""
    if request.param == "memory":
        repository: Repository = InMemoryRepository()
    else:
        repository = SqliteRepository(tmp_path / "flowlist.db")
    yield repository
    repository.close()


def seed_playlist(
    store: Repository, count: int = 4, name: str = "party"
) -> tuple[str, list[str], list[str]]:
    track_ids = []
    for index in range(count):
        track = make_track(f"meta:{index}", title=f"Song {index}", artist=f"Artist {index}")
        store.upsert_track(track)
        track_ids.append(track.id)
    entry_ids = [f"e{index}" for index in range(count)]
    playlist = store.create_playlist(
        playlist_id="p1",
        name=name,
        source=PlaylistSource.CSV,
        source_ref="/exports/party.csv",
        created_at=NOW,
        track_ids=track_ids,
        entry_ids=entry_ids,
    )
    return playlist.id, track_ids, entry_ids


def a_transition() -> Transition:
    a = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.7, loudness_db=-7.0)
    b = FeatureSnapshot(bpm=126.0, key_pc=4, mode=0, energy=0.72, loudness_db=-6.5)
    return Transition(
        score=0.87,
        components={
            "key": 0.85,
            "bpm": 0.95,
            "energy": 0.96,
            "loudness": 1.0,
            "danceability": None,
        },
        weights=TransitionWeights(),
        key_relation=KeyRelation.ADJACENT_FIFTH,
        camelot_from="8A",
        camelot_to="9A",
        bpm_from=124.0,
        bpm_to=126.0,
        bpm_delta_pct=1.61,
        energy_delta=0.02,
        loudness_delta_db=0.5,
        features_from=a,
        features_to=b,
    )


def a_run(playlist_id: str, run_id: str = "r1", **overrides: object) -> ReorderRun:
    payload: dict[str, object] = {
        "id": run_id,
        "playlist_id": playlist_id,
        "created_at": NOW,
        "engine_version": "0.1.0",
        "algorithm": Algorithm.GREEDY_2OPT,
        "seed": 7,
        "params": ReorderParams(seed=7),
        "coverage": CoverageReport(tracks=4, full=4, fields=["bpm"], resolved={"bpm": 4}),
        "score_mean_before": 0.47,
        "score_mean_after": 0.81,
        "score_min_before": 0.11,
        "score_min_after": 0.38,
        "score_total_before": 1.41,
        "score_total_after": 2.43,
        "seamless_before": 0,
        "seamless_after": 3,
        "cliff_before": 2,
        "cliff_after": 0,
    }
    payload.update(overrides)
    return ReorderRun(**payload)  # type: ignore[arg-type]


def run_entries(run_id: str, entry_ids: list[str]) -> list[RunEntry]:
    return [
        RunEntry(
            run_id=run_id,
            position=position,
            entry_id=entry_id,
            transition=None if position == 0 else a_transition(),
        )
        for position, entry_id in enumerate(entry_ids)
    ]


# --------------------------------------------------------------------------- #
# 2.1 tracks
# --------------------------------------------------------------------------- #


def test_track_upsert_fills_nulls_but_never_overwrites(store: Repository) -> None:
    original = make_track("meta:a", title="Night Drive", artist="Vera Lux", album="Neon")
    store.upsert_track(original)
    incoming = make_track(
        "meta:a", title="DIFFERENT", artist="DIFFERENT", album="Other", duration_ms=231000
    )
    merged = store.upsert_track(incoming)
    assert merged.title == "Night Drive"  # never overwritten
    assert merged.album == "Neon"  # non-null stays
    assert merged.duration_ms == 231000  # null filled
    assert store.get_track("meta:a") == merged


def test_track_upsert_is_idempotent(store: Repository) -> None:
    track = make_track("meta:a")
    assert store.upsert_track(track) == store.upsert_track(track)
    assert len(store.list_tracks()) == 1


def test_track_lookup(store: Repository) -> None:
    store.upsert_track(make_track("meta:a"))
    store.upsert_track(make_track("meta:b"))
    assert store.get_track("meta:zzz") is None
    assert [t.id for t in store.list_tracks(["meta:b", "meta:a"])] == ["meta:b", "meta:a"]
    assert store.list_tracks([]) == []
    assert len(store.list_tracks()) == 2


# --------------------------------------------------------------------------- #
# 2.2 / FR-4 features
# --------------------------------------------------------------------------- #


def features(track_id: str, source: FeatureSource, **values: object) -> AudioFeatures:
    payload: dict[str, object] = {"track_id": track_id, "source": source, "analyzed_at": NOW}
    payload.update(values)
    return AudioFeatures(**payload)  # type: ignore[arg-type]


def test_features_one_row_per_source(store: Repository) -> None:
    store.upsert_track(make_track("meta:a"))
    store.upsert_features(features("meta:a", FeatureSource.IMPORT, bpm=122.0))
    store.upsert_features(features("meta:a", FeatureSource.LOCAL_ANALYSIS, bpm=123.9))
    rows = store.get_features("meta:a")
    assert len(rows) == 2
    assert {r.source for r in rows} == {FeatureSource.IMPORT, FeatureSource.LOCAL_ANALYSIS}


def test_features_non_manual_sources_replace_the_whole_record(store: Repository) -> None:
    store.upsert_track(make_track("meta:a"))
    store.upsert_features(features("meta:a", FeatureSource.IMPORT, bpm=122.0, energy=0.5))
    store.upsert_features(features("meta:a", FeatureSource.IMPORT, bpm=125.0))
    rows = store.get_features("meta:a")
    assert len(rows) == 1
    assert rows[0].bpm == 125.0
    assert rows[0].energy is None  # replaced whole, not merged (2.2)


def test_fr4_manual_row_upserts_field_wise(store: Repository) -> None:
    store.upsert_track(make_track("meta:a"))
    store.upsert_features(features("meta:a", FeatureSource.MANUAL, bpm=128.0))
    merged = store.upsert_features(features("meta:a", FeatureSource.MANUAL, key_pc=9, mode=0))
    assert merged.bpm == 128.0  # earlier field preserved
    assert (merged.key_pc, merged.mode) == (9, 0)
    assert len(store.get_features("meta:a")) == 1


def test_fr4_manual_key_and_mode_upsert_as_a_pair(store: Repository) -> None:
    store.upsert_track(make_track("meta:a"))
    store.upsert_features(features("meta:a", FeatureSource.MANUAL, key_pc=0, mode=1))
    merged = store.upsert_features(features("meta:a", FeatureSource.MANUAL, key_pc=9, mode=0))
    assert (merged.key_pc, merged.mode) == (9, 0)


def test_features_require_a_known_track(store: Repository) -> None:
    with pytest.raises(UnknownTrackError) as excinfo:
        store.upsert_features(features("meta:ghost", FeatureSource.IMPORT, bpm=120.0))
    assert excinfo.value.code == "unknown_track"


def test_features_bulk_read_and_delete(store: Repository) -> None:
    store.upsert_track(make_track("meta:a"))
    store.upsert_track(make_track("meta:b"))
    store.upsert_features(features("meta:a", FeatureSource.IMPORT, bpm=120.0))
    bulk = store.get_features_for(["meta:a", "meta:b"])
    assert len(bulk["meta:a"]) == 1
    assert bulk["meta:b"] == []
    assert store.delete_features("meta:a", "import") is True
    assert store.delete_features("meta:a", "import") is False


# --------------------------------------------------------------------------- #
# 2.3 / 2.4 playlists and entries
# --------------------------------------------------------------------------- #


def test_playlist_entries_are_contiguous_from_zero(store: Repository) -> None:
    playlist_id, track_ids, entry_ids = seed_playlist(store)
    entries = store.get_entries(playlist_id)
    assert [e.position for e in entries] == [0, 1, 2, 3]
    assert [e.id for e in entries] == entry_ids
    assert [e.track_id for e in entries] == track_ids


def test_playlist_name_lookup_is_case_insensitive(store: Repository) -> None:
    seed_playlist(store, name="Party")
    assert store.get_playlist_by_name("party") is not None
    assert store.get_playlist_by_name("PARTY") is not None
    assert store.get_playlist_by_name("other") is None


def test_playlist_allows_duplicate_tracks_as_distinct_entries(store: Repository) -> None:
    """D8: the optimizer's node is the entry, not the track."""
    store.upsert_track(make_track("meta:a"))
    store.create_playlist(
        playlist_id="p1",
        name="dupes",
        source=PlaylistSource.MANUAL,
        source_ref=None,
        created_at=NOW,
        track_ids=["meta:a", "meta:a"],
        entry_ids=["e0", "e1"],
    )
    entries = store.get_entries("p1")
    assert len(entries) == 2
    assert entries[0].track_id == entries[1].track_id
    assert entries[0].id != entries[1].id


def test_fr1_replace_entries_swaps_the_whole_set(store: Repository) -> None:
    playlist_id, _, _ = seed_playlist(store)
    store.upsert_track(make_track("meta:new"))
    entries = store.replace_entries(playlist_id, track_ids=["meta:new"], entry_ids=["n0"])
    assert [e.id for e in entries] == ["n0"]
    assert [e.position for e in entries] == [0]
    assert store.get_track("meta:0") is not None  # the catalog is append-mostly


def test_fr1_replace_refuses_when_runs_exist_without_force(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    store.add_run(a_run(playlist_id), run_entries("r1", entry_ids))
    store.upsert_track(make_track("meta:new"))
    with pytest.raises(PlaylistHasRunsError) as excinfo:
        store.replace_entries(playlist_id, track_ids=["meta:new"], entry_ids=["n0"])
    assert excinfo.value.code == "playlist_has_runs"
    assert store.list_runs(playlist_id)  # nothing was destroyed


def test_fr1_force_replace_deletes_runs_and_clears_applied_run(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    store.add_run(a_run(playlist_id), run_entries("r1", entry_ids))
    store.apply_run("r1")
    assert store.get_playlist(playlist_id).applied_run_id == "r1"  # type: ignore[union-attr]

    store.upsert_track(make_track("meta:new"))
    store.replace_entries(playlist_id, track_ids=["meta:new"], entry_ids=["n0"], force=True)
    assert store.list_runs(playlist_id) == []
    assert store.get_playlist(playlist_id).applied_run_id is None  # type: ignore[union-attr]
    assert store.get_run_entries("r1") == []


def test_playlist_deletion_refused_while_runs_exist(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    store.add_run(a_run(playlist_id), run_entries("r1", entry_ids))
    with pytest.raises(PlaylistHasRunsError):
        store.delete_playlist(playlist_id)
    store.delete_playlist(playlist_id, force=True)
    assert store.get_playlist(playlist_id) is None
    assert store.get_entries(playlist_id) == []
    assert store.list_runs(playlist_id) == []
    # Tracks are never deleted by playlist deletion (2.1).
    assert len(store.list_tracks()) == 4


def test_playlist_deletion_without_runs(store: Repository) -> None:
    playlist_id, _, _ = seed_playlist(store)
    store.delete_playlist(playlist_id)
    assert store.get_playlist(playlist_id) is None


def test_unknown_playlist_errors(store: Repository) -> None:
    with pytest.raises(UnknownPlaylistError):
        store.delete_playlist("nope")
    with pytest.raises(UnknownPlaylistError):
        store.replace_entries("nope", track_ids=[], entry_ids=[])


# --------------------------------------------------------------------------- #
# FR-11 runs
# --------------------------------------------------------------------------- #


def test_fr11_run_round_trips_with_its_breakdown(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    stored = store.add_run(a_run(playlist_id), run_entries("r1", entry_ids))
    read = store.get_run("r1")
    assert read == stored
    assert read is not None
    assert read.params.seed == 7
    assert read.coverage.tracks == 4

    entries = store.get_run_entries("r1")
    assert [e.position for e in entries] == [0, 1, 2, 3]
    assert entries[0].transition is None  # null iff position 0 (2.6)
    assert entries[1].transition is not None
    assert entries[1].transition.key_relation is KeyRelation.ADJACENT_FIFTH
    assert entries[1].transition.features_from.bpm == 124.0


def test_fr11_runs_are_self_contained_after_features_change(store: Repository) -> None:
    """2.6: the stored breakdown snapshots the resolved values it used."""
    playlist_id, track_ids, entry_ids = seed_playlist(store)
    store.upsert_features(features(track_ids[0], FeatureSource.IMPORT, bpm=124.0))
    store.add_run(a_run(playlist_id), run_entries("r1", entry_ids))
    store.upsert_features(features(track_ids[0], FeatureSource.MANUAL, bpm=200.0))
    entries = store.get_run_entries("r1")
    assert entries[1].transition is not None
    assert entries[1].transition.features_from.bpm == 124.0


def test_fr11_run_entries_must_be_a_permutation(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    with pytest.raises(ValueError):
        store.add_run(a_run(playlist_id), run_entries("r1", entry_ids[:2]))
    with pytest.raises(ValueError):
        store.add_run(a_run(playlist_id), run_entries("r1", [entry_ids[0]] * 4))


def test_fr11_runs_are_append_only(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    store.add_run(a_run(playlist_id), run_entries("r1", entry_ids))
    with pytest.raises(Exception):  # noqa: B017 - backend-specific integrity error
        store.add_run(a_run(playlist_id, score_mean_after=0.99), run_entries("r1", entry_ids))
    read = store.get_run("r1")
    assert read is not None and read.score_mean_after == 0.81


def test_fr11_runs_need_a_playlist(store: Repository) -> None:
    with pytest.raises(UnknownPlaylistError):
        store.add_run(a_run("ghost"), [])


def test_fr11_list_runs_is_ordered_and_filtered(store: Repository) -> None:
    playlist_id, _, entry_ids = seed_playlist(store)
    later = NOW + timedelta(hours=1)
    store.add_run(a_run(playlist_id, run_id="r1"), run_entries("r1", entry_ids))
    store.add_run(a_run(playlist_id, run_id="r2", created_at=later), run_entries("r2", entry_ids))
    assert [r.id for r in store.list_runs(playlist_id)] == ["r1", "r2"]
    assert [r.id for r in store.list_runs()] == ["r1", "r2"]
    assert store.list_runs("other") == []


# --------------------------------------------------------------------------- #
# FR-12 apply
# --------------------------------------------------------------------------- #


def test_fr12_apply_rewrites_positions_in_place(store: Repository) -> None:
    playlist_id, track_ids, entry_ids = seed_playlist(store)
    proposed = ["e2", "e0", "e3", "e1"]
    store.add_run(a_run(playlist_id), run_entries("r1", proposed))

    playlist = store.apply_run("r1")
    assert playlist.applied_run_id == "r1"
    entries = store.get_entries(playlist_id)
    assert [e.id for e in entries] == proposed
    assert [e.position for e in entries] == [0, 1, 2, 3]
    # Entry ids are immutable, so the run's references survive apply (2.4).
    assert [e.entry_id for e in store.get_run_entries("r1")] == proposed
    assert {e.track_id for e in entries} == set(track_ids)
    assert set(entry_ids) == {e.id for e in entries}


def test_fr12_apply_is_idempotent(store: Repository) -> None:
    playlist_id, _, _ = seed_playlist(store)
    store.add_run(a_run(playlist_id), run_entries("r1", ["e3", "e2", "e1", "e0"]))
    store.apply_run("r1")
    first = [e.id for e in store.get_entries(playlist_id)]
    store.apply_run("r1")
    assert [e.id for e in store.get_entries(playlist_id)] == first


def test_fr12_apply_handles_a_full_reversal(store: Repository) -> None:
    """The transient-UNIQUE case the two-phase update exists for (2.4)."""
    playlist_id, _, entry_ids = seed_playlist(store, count=6)
    reversed_ids = list(reversed(entry_ids))
    store.add_run(a_run(playlist_id), run_entries("r1", reversed_ids))
    store.apply_run("r1")
    assert [e.id for e in store.get_entries(playlist_id)] == reversed_ids
    assert [e.position for e in store.get_entries(playlist_id)] == list(range(6))


def test_fr12_apply_unknown_run(store: Repository) -> None:
    with pytest.raises(UnknownRunError) as excinfo:
        store.apply_run("nope")
    assert excinfo.value.code == "unknown_run"


# --------------------------------------------------------------------------- #
# SQLite specifics
# --------------------------------------------------------------------------- #


def test_sqlite_persists_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "flowlist.db"
    with SqliteRepository(path) as first:
        seed_playlist(first)
    with SqliteRepository(path) as second:
        playlist = second.get_playlist_by_name("party")
        assert playlist is not None
        assert len(second.get_entries(playlist.id)) == 4


def test_sqlite_enforces_the_schema_checks(tmp_path: Path) -> None:
    """The DDL CHECKs are a backstop for programmer error (2.2)."""
    import sqlite3

    store = SqliteRepository(tmp_path / "db.sqlite")
    store.upsert_track(make_track("meta:a"))
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO audio_features (track_id, source, bpm, analyzed_at)"
            " VALUES ('meta:a', 'import', 999.0, ?)",
            (NOW.isoformat(),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO audio_features (track_id, source, key_pc, analyzed_at)"
            " VALUES ('meta:a', 'import', 4, ?)",  # mode missing
            (NOW.isoformat(),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO audio_features (track_id, source, analyzed_at)"
            " VALUES ('meta:ghost', 'import', ?)",  # unknown FK
            (NOW.isoformat(),),
        )
    store.close()


def test_sqlite_name_uniqueness_is_case_insensitive(tmp_path: Path) -> None:
    import sqlite3

    store = SqliteRepository(tmp_path / "db.sqlite")
    seed_playlist(store, name="Party")
    store.upsert_track(make_track("meta:x"))
    with pytest.raises(sqlite3.IntegrityError):
        store.create_playlist(
            playlist_id="p2",
            name="party",
            source=PlaylistSource.CSV,
            source_ref=None,
            created_at=NOW,
            track_ids=["meta:x"],
            entry_ids=["z0"],
        )
    store.close()


def test_sqlite_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "flowlist.db"
    store = SqliteRepository(path)
    store.close()
    assert path.exists()


def test_sqlite_timestamps_round_trip_as_iso_utc(tmp_path: Path) -> None:
    store = SqliteRepository(tmp_path / "db.sqlite")
    created = datetime(2026, 7, 31, 18, 2, 11, tzinfo=UTC)
    store.upsert_track(make_track("meta:a", created_at=created))
    read = store.get_track("meta:a")
    assert read is not None and read.created_at == created
    store.close()
