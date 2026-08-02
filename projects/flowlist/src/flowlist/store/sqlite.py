"""SQLite repository — the default backend (DATA_MODEL 4).

stdlib ``sqlite3``, WAL mode, foreign keys ON, every write in a transaction.
The DDL below is verbatim from DATA_MODEL 4 so a reviewer can diff it against
the spec.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from flowlist.engine.models import (
    Algorithm,
    AudioFeatures,
    CoverageReport,
    FeatureSource,
    Playlist,
    PlaylistEntry,
    PlaylistSource,
    ReorderParams,
    ReorderRun,
    RunEntry,
    Track,
    Transition,
)
from flowlist.errors import (
    PlaylistHasRunsError,
    UnknownPlaylistError,
    UnknownRunError,
    UnknownTrackError,
)
from flowlist.store.base import Repository

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  artist      TEXT NOT NULL,
  album       TEXT,
  duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms > 0),
  spotify_id  TEXT UNIQUE,
  file_path   TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_features (
  track_id     TEXT NOT NULL REFERENCES tracks(id),
  source       TEXT NOT NULL CHECK (source IN
                 ('manual','local_analysis','streaming','import','fixture')),
  bpm          REAL CHECK (bpm IS NULL OR (bpm >= 40 AND bpm <= 260)),
  key_pc       INTEGER CHECK (key_pc IS NULL OR (key_pc BETWEEN 0 AND 11)),
  mode         INTEGER CHECK (mode IS NULL OR mode IN (0,1)),
  energy       REAL CHECK (energy IS NULL OR (energy BETWEEN 0 AND 1)),
  danceability REAL CHECK (danceability IS NULL OR (danceability BETWEEN 0 AND 1)),
  loudness_db  REAL CHECK (loudness_db IS NULL OR (loudness_db BETWEEN -60 AND 0)),
  valence      REAL,
  confidence   REAL NOT NULL DEFAULT 1.0,
  analyzed_at  TEXT NOT NULL,
  PRIMARY KEY (track_id, source),
  CHECK ((key_pc IS NULL) = (mode IS NULL))
);

CREATE TABLE IF NOT EXISTS playlists (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL COLLATE NOCASE UNIQUE,
  source         TEXT NOT NULL CHECK (source IN ('csv','json','directory','manual')),
  source_ref     TEXT,
  applied_run_id TEXT REFERENCES reorder_runs(id),
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_entries (
  id          TEXT PRIMARY KEY,
  playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL CHECK (position >= 0),
  track_id    TEXT NOT NULL REFERENCES tracks(id),
  UNIQUE (playlist_id, position)
);

CREATE TABLE IF NOT EXISTS reorder_runs (
  id                 TEXT PRIMARY KEY,
  playlist_id        TEXT NOT NULL REFERENCES playlists(id),
  created_at         TEXT NOT NULL,
  engine_version     TEXT NOT NULL,
  algorithm          TEXT NOT NULL CHECK (algorithm IN ('greedy_2opt','ortools')),
  seed               INTEGER NOT NULL,
  params             TEXT NOT NULL,
  coverage           TEXT NOT NULL,
  score_mean_before  REAL NOT NULL, score_mean_after  REAL NOT NULL,
  score_min_before   REAL NOT NULL, score_min_after   REAL NOT NULL,
  score_total_before REAL NOT NULL, score_total_after REAL NOT NULL,
  seamless_before    INTEGER NOT NULL, seamless_after INTEGER NOT NULL,
  cliff_before       INTEGER NOT NULL, cliff_after    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_entries (
  run_id     TEXT NOT NULL REFERENCES reorder_runs(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL CHECK (position >= 0),
  entry_id   TEXT NOT NULL REFERENCES playlist_entries(id),
  transition TEXT,
  PRIMARY KEY (run_id, position),
  UNIQUE (run_id, entry_id)
);

CREATE INDEX IF NOT EXISTS idx_entries_playlist ON playlist_entries(playlist_id, position);
CREATE INDEX IF NOT EXISTS idx_runs_playlist    ON reorder_runs(playlist_id, created_at);
"""

_FEATURE_FIELDS = (
    "bpm",
    "key_pc",
    "mode",
    "energy",
    "danceability",
    "loudness_db",
    "valence",
)


def _iso(value: datetime) -> str:
    return value.isoformat()


class SqliteRepository(Repository):
    """Repository backed by stdlib ``sqlite3``."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path not in (":memory:", ""):
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.path = str(Path(self.path).expanduser())
        # check_same_thread=False: FastAPI runs sync endpoints on threadpool
        # worker threads, so under `flowlist serve` the connection is used from
        # a different thread than the one that opened it.  Safe because CPython
        # ships SQLite in serialized mode (sqlite3.threadsafety == 3) and the
        # API serializes whole requests with a lock (api.app.get_repository);
        # the CLI is single-threaded.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- tracks

    def upsert_track(self, track: Track) -> Track:
        existing = self.get_track(track.id)
        if existing is None:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO tracks (id, title, artist, album, duration_ms, spotify_id,"
                    " file_path, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        track.id,
                        track.title,
                        track.artist,
                        track.album,
                        track.duration_ms,
                        track.spotify_id,
                        track.file_path,
                        _iso(track.created_at),
                    ),
                )
            return track

        # Non-null incoming fields fill nulls but never overwrite (2.1).
        updates: dict[str, Any] = {}
        for field in ("album", "duration_ms", "spotify_id", "file_path"):
            if getattr(existing, field) is None and getattr(track, field) is not None:
                updates[field] = getattr(track, field)
        if not updates:
            return existing
        assignments = ", ".join(f"{field} = ?" for field in updates)
        with self._conn:
            self._conn.execute(
                f"UPDATE tracks SET {assignments} WHERE id = ?",
                (*updates.values(), track.id),
            )
        return existing.model_copy(update=updates)

    def get_track(self, track_id: str) -> Track | None:
        row = self._conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return None if row is None else self._track(row)

    def list_tracks(self, track_ids: Sequence[str] | None = None) -> list[Track]:
        if track_ids is None:
            rows = self._conn.execute("SELECT * FROM tracks ORDER BY id").fetchall()
            return [self._track(row) for row in rows]
        if not track_ids:
            return []
        placeholders = ",".join("?" * len(track_ids))
        rows = self._conn.execute(
            f"SELECT * FROM tracks WHERE id IN ({placeholders})", tuple(track_ids)
        ).fetchall()
        found = {row["id"]: self._track(row) for row in rows}
        return [found[tid] for tid in track_ids if tid in found]

    @staticmethod
    def _track(row: sqlite3.Row) -> Track:
        return Track(
            id=row["id"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            duration_ms=row["duration_ms"],
            spotify_id=row["spotify_id"],
            file_path=row["file_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # -------------------------------------------------------------- features

    def upsert_features(self, features: AudioFeatures) -> AudioFeatures:
        if self.get_track(features.track_id) is None:
            raise UnknownTrackError(
                f"no track {features.track_id!r} in the catalog", track_id=features.track_id
            )
        merged = features
        if features.source is FeatureSource.MANUAL:
            existing = self._get_feature_row(features.track_id, FeatureSource.MANUAL)
            if existing is not None:
                merged = _merge_manual(existing, features)
        with self._conn:
            self._conn.execute(
                "INSERT INTO audio_features (track_id, source, bpm, key_pc, mode, energy,"
                " danceability, loudness_db, valence, confidence, analyzed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(track_id, source) DO UPDATE SET"
                " bpm=excluded.bpm, key_pc=excluded.key_pc, mode=excluded.mode,"
                " energy=excluded.energy, danceability=excluded.danceability,"
                " loudness_db=excluded.loudness_db, valence=excluded.valence,"
                " confidence=excluded.confidence, analyzed_at=excluded.analyzed_at",
                (
                    merged.track_id,
                    merged.source.value,
                    merged.bpm,
                    merged.key_pc,
                    merged.mode,
                    merged.energy,
                    merged.danceability,
                    merged.loudness_db,
                    merged.valence,
                    merged.confidence,
                    _iso(merged.analyzed_at),
                ),
            )
        return merged

    def _get_feature_row(self, track_id: str, source: FeatureSource) -> AudioFeatures | None:
        row = self._conn.execute(
            "SELECT * FROM audio_features WHERE track_id = ? AND source = ?",
            (track_id, source.value),
        ).fetchone()
        return None if row is None else self._features(row)

    def get_features(self, track_id: str) -> list[AudioFeatures]:
        rows = self._conn.execute(
            "SELECT * FROM audio_features WHERE track_id = ? ORDER BY source", (track_id,)
        ).fetchall()
        return [self._features(row) for row in rows]

    def get_features_for(self, track_ids: Sequence[str]) -> dict[str, list[AudioFeatures]]:
        result: dict[str, list[AudioFeatures]] = {tid: [] for tid in track_ids}
        if not track_ids:
            return result
        placeholders = ",".join("?" * len(track_ids))
        rows = self._conn.execute(
            f"SELECT * FROM audio_features WHERE track_id IN ({placeholders})"
            " ORDER BY track_id, source",
            tuple(track_ids),
        ).fetchall()
        for row in rows:
            result[row["track_id"]].append(self._features(row))
        return result

    def delete_features(self, track_id: str, source: str) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM audio_features WHERE track_id = ? AND source = ?",
                (track_id, str(FeatureSource(source).value)),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _features(row: sqlite3.Row) -> AudioFeatures:
        return AudioFeatures(
            track_id=row["track_id"],
            source=FeatureSource(row["source"]),
            bpm=row["bpm"],
            key_pc=row["key_pc"],
            mode=row["mode"],
            energy=row["energy"],
            danceability=row["danceability"],
            loudness_db=row["loudness_db"],
            valence=row["valence"],
            confidence=row["confidence"],
            analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
        )

    # ------------------------------------------------------------- playlists

    def create_playlist(
        self,
        *,
        playlist_id: str,
        name: str,
        source: PlaylistSource,
        source_ref: str | None,
        created_at: datetime,
        track_ids: Sequence[str],
        entry_ids: Sequence[str],
    ) -> Playlist:
        if len(track_ids) != len(entry_ids):
            raise ValueError("track_ids and entry_ids must be the same length")
        with self._conn:
            self._conn.execute(
                "INSERT INTO playlists (id, name, source, source_ref, applied_run_id, created_at)"
                " VALUES (?,?,?,?,NULL,?)",
                (playlist_id, name, source.value, source_ref, _iso(created_at)),
            )
            self._conn.executemany(
                "INSERT INTO playlist_entries (id, playlist_id, position, track_id)"
                " VALUES (?,?,?,?)",
                [
                    (entry_id, playlist_id, position, track_id)
                    for position, (entry_id, track_id) in enumerate(
                        zip(entry_ids, track_ids, strict=True)
                    )
                ],
            )
        return Playlist(
            id=playlist_id,
            name=name,
            source=source,
            source_ref=source_ref,
            applied_run_id=None,
            created_at=created_at,
        )

    def get_playlist(self, playlist_id: str) -> Playlist | None:
        row = self._conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return None if row is None else self._playlist(row)

    def get_playlist_by_name(self, name: str) -> Playlist | None:
        row = self._conn.execute("SELECT * FROM playlists WHERE name = ?", (name,)).fetchone()
        return None if row is None else self._playlist(row)

    def list_playlists(self) -> list[Playlist]:
        rows = self._conn.execute("SELECT * FROM playlists ORDER BY created_at, id").fetchall()
        return [self._playlist(row) for row in rows]

    def get_entries(self, playlist_id: str) -> list[PlaylistEntry]:
        rows = self._conn.execute(
            "SELECT * FROM playlist_entries WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        return [
            PlaylistEntry(
                id=row["id"],
                playlist_id=row["playlist_id"],
                position=row["position"],
                track_id=row["track_id"],
            )
            for row in rows
        ]

    def replace_entries(
        self,
        playlist_id: str,
        *,
        track_ids: Sequence[str],
        entry_ids: Sequence[str],
        force: bool = False,
    ) -> list[PlaylistEntry]:
        if self.get_playlist(playlist_id) is None:
            raise UnknownPlaylistError(f"no playlist {playlist_id!r}", playlist_id=playlist_id)
        if len(track_ids) != len(entry_ids):
            raise ValueError("track_ids and entry_ids must be the same length")
        run_ids = self._run_ids(playlist_id)
        if run_ids and not force:
            raise PlaylistHasRunsError(
                f"playlist {playlist_id!r} has {len(run_ids)} run(s); pass --force to replace",
                playlist_id=playlist_id,
                runs=len(run_ids),
            )
        with self._conn:
            if run_ids:
                self._delete_runs(playlist_id)
            self._conn.execute("DELETE FROM playlist_entries WHERE playlist_id = ?", (playlist_id,))
            self._conn.executemany(
                "INSERT INTO playlist_entries (id, playlist_id, position, track_id)"
                " VALUES (?,?,?,?)",
                [
                    (entry_id, playlist_id, position, track_id)
                    for position, (entry_id, track_id) in enumerate(
                        zip(entry_ids, track_ids, strict=True)
                    )
                ],
            )
        return self.get_entries(playlist_id)

    def delete_playlist(self, playlist_id: str, *, force: bool = False) -> None:
        if self.get_playlist(playlist_id) is None:
            raise UnknownPlaylistError(f"no playlist {playlist_id!r}", playlist_id=playlist_id)
        run_ids = self._run_ids(playlist_id)
        if run_ids and not force:
            raise PlaylistHasRunsError(
                f"playlist {playlist_id!r} has {len(run_ids)} run(s); pass --force to delete",
                playlist_id=playlist_id,
                runs=len(run_ids),
            )
        with self._conn:
            if run_ids:
                self._delete_runs(playlist_id)
            # playlist_entries cascade from playlists; runs had to go first so
            # run_entries.entry_id (no ON DELETE) never dangles.
            self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def _run_ids(self, playlist_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT id FROM reorder_runs WHERE playlist_id = ?", (playlist_id,)
        ).fetchall()
        return [row["id"] for row in rows]

    def _delete_runs(self, playlist_id: str) -> None:
        """Sanctioned deletion path (2.3): clear the pointer, then drop runs."""
        self._conn.execute(
            "UPDATE playlists SET applied_run_id = NULL WHERE id = ?", (playlist_id,)
        )
        self._conn.execute(
            "DELETE FROM run_entries WHERE run_id IN"
            " (SELECT id FROM reorder_runs WHERE playlist_id = ?)",
            (playlist_id,),
        )
        self._conn.execute("DELETE FROM reorder_runs WHERE playlist_id = ?", (playlist_id,))

    @staticmethod
    def _playlist(row: sqlite3.Row) -> Playlist:
        return Playlist(
            id=row["id"],
            name=row["name"],
            source=PlaylistSource(row["source"]),
            source_ref=row["source_ref"],
            applied_run_id=row["applied_run_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # ------------------------------------------------------------------ runs

    def add_run(self, run: ReorderRun, entries: Sequence[RunEntry]) -> ReorderRun:
        if self.get_playlist(run.playlist_id) is None:
            raise UnknownPlaylistError(
                f"no playlist {run.playlist_id!r}", playlist_id=run.playlist_id
            )
        known = {entry.id for entry in self.get_entries(run.playlist_id)}
        proposed = [entry.entry_id for entry in entries]
        if len(proposed) != len(set(proposed)) or set(proposed) != known:
            raise ValueError("run entries must be a permutation of the playlist's entries")
        with self._conn:
            self._conn.execute(
                "INSERT INTO reorder_runs (id, playlist_id, created_at, engine_version, algorithm,"
                " seed, params, coverage, score_mean_before, score_mean_after, score_min_before,"
                " score_min_after, score_total_before, score_total_after, seamless_before,"
                " seamless_after, cliff_before, cliff_after)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.playlist_id,
                    _iso(run.created_at),
                    run.engine_version,
                    run.algorithm.value,
                    run.seed,
                    run.params.model_dump_json(),
                    run.coverage.model_dump_json(),
                    run.score_mean_before,
                    run.score_mean_after,
                    run.score_min_before,
                    run.score_min_after,
                    run.score_total_before,
                    run.score_total_after,
                    run.seamless_before,
                    run.seamless_after,
                    run.cliff_before,
                    run.cliff_after,
                ),
            )
            self._conn.executemany(
                "INSERT INTO run_entries (run_id, position, entry_id, transition) VALUES (?,?,?,?)",
                [
                    (
                        entry.run_id,
                        entry.position,
                        entry.entry_id,
                        None if entry.transition is None else entry.transition.model_dump_json(),
                    )
                    for entry in entries
                ],
            )
        return run

    def get_run(self, run_id: str) -> ReorderRun | None:
        row = self._conn.execute("SELECT * FROM reorder_runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._run(row)

    def list_runs(self, playlist_id: str | None = None) -> list[ReorderRun]:
        if playlist_id is None:
            rows = self._conn.execute(
                "SELECT * FROM reorder_runs ORDER BY created_at, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM reorder_runs WHERE playlist_id = ? ORDER BY created_at, id",
                (playlist_id,),
            ).fetchall()
        return [self._run(row) for row in rows]

    def get_run_entries(self, run_id: str) -> list[RunEntry]:
        rows = self._conn.execute(
            "SELECT * FROM run_entries WHERE run_id = ? ORDER BY position", (run_id,)
        ).fetchall()
        return [
            RunEntry(
                run_id=row["run_id"],
                position=row["position"],
                entry_id=row["entry_id"],
                transition=(
                    None
                    if row["transition"] is None
                    else Transition.model_validate_json(row["transition"])
                ),
            )
            for row in rows
        ]

    def apply_run(self, run_id: str) -> Playlist:
        run = self.get_run(run_id)
        if run is None:
            raise UnknownRunError(f"no run {run_id!r}", run_id=run_id)
        entries = self.get_run_entries(run_id)
        count = len(entries)
        with self._conn:
            # Phase 1: shift every position into a disjoint range so the
            # immediate UNIQUE(playlist_id, position) check never trips (2.4).
            self._conn.execute(
                "UPDATE playlist_entries SET position = position + ? WHERE playlist_id = ?",
                (count, run.playlist_id),
            )
            # Phase 2: write the final contiguous 0..n-1 map.
            self._conn.executemany(
                "UPDATE playlist_entries SET position = ? WHERE id = ?",
                [(entry.position, entry.entry_id) for entry in entries],
            )
            self._conn.execute(
                "UPDATE playlists SET applied_run_id = ? WHERE id = ?",
                (run_id, run.playlist_id),
            )
        playlist = self.get_playlist(run.playlist_id)
        if playlist is None:  # pragma: no cover - FK guarantees the parent row
            raise UnknownPlaylistError(
                f"no playlist {run.playlist_id!r}", playlist_id=run.playlist_id
            )
        return playlist

    @staticmethod
    def _run(row: sqlite3.Row) -> ReorderRun:
        return ReorderRun(
            id=row["id"],
            playlist_id=row["playlist_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            engine_version=row["engine_version"],
            algorithm=Algorithm(row["algorithm"]),
            seed=row["seed"],
            params=ReorderParams.model_validate(json.loads(row["params"])),
            coverage=CoverageReport.model_validate(json.loads(row["coverage"])),
            score_mean_before=row["score_mean_before"],
            score_mean_after=row["score_mean_after"],
            score_min_before=row["score_min_before"],
            score_min_after=row["score_min_after"],
            score_total_before=row["score_total_before"],
            score_total_after=row["score_total_after"],
            seamless_before=row["seamless_before"],
            seamless_after=row["seamless_after"],
            cliff_before=row["cliff_before"],
            cliff_after=row["cliff_after"],
        )


def _merge_manual(existing: AudioFeatures, incoming: AudioFeatures) -> AudioFeatures:
    """FR-4 upsert: a manual row accepts any subset of fields.

    ``key_pc``/``mode`` merge as a pair so the set-together invariant survives
    a partial override.
    """
    values: dict[str, Any] = {}
    for field in _FEATURE_FIELDS:
        if field in ("key_pc", "mode"):
            continue
        incoming_value = getattr(incoming, field)
        values[field] = incoming_value if incoming_value is not None else getattr(existing, field)
    if incoming.key_pc is not None:
        values["key_pc"], values["mode"] = incoming.key_pc, incoming.mode
    else:
        values["key_pc"], values["mode"] = existing.key_pc, existing.mode
    return AudioFeatures(
        track_id=existing.track_id,
        source=FeatureSource.MANUAL,
        confidence=incoming.confidence,
        analyzed_at=incoming.analyzed_at,
        **values,
    )


__all__ = ["SCHEMA", "SqliteRepository"]
