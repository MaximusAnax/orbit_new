"""In-memory repository for tests (SCOPE Architecture-Store).

A real second implementation rather than a SQLite alias, so the tests exercise
the :class:`~flowlist.store.base.Repository` contract itself: same uniqueness
rules, same contiguous positions, same refusal to drop a playlist that runs
reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from flowlist.engine.models import (
    AudioFeatures,
    FeatureSource,
    Playlist,
    PlaylistEntry,
    PlaylistSource,
    ReorderRun,
    RunEntry,
    Track,
)
from flowlist.errors import (
    PlaylistHasRunsError,
    UnknownPlaylistError,
    UnknownRunError,
    UnknownTrackError,
)
from flowlist.store.base import Repository
from flowlist.store.sqlite import _merge_manual


class InMemoryRepository(Repository):
    """Dict-backed repository with the same invariants as the SQLite one."""

    def __init__(self) -> None:
        self._tracks: dict[str, Track] = {}
        self._features: dict[tuple[str, FeatureSource], AudioFeatures] = {}
        self._playlists: dict[str, Playlist] = {}
        self._entries: dict[str, PlaylistEntry] = {}
        self._runs: dict[str, ReorderRun] = {}
        self._run_entries: dict[str, list[RunEntry]] = {}

    # ---------------------------------------------------------------- tracks

    def upsert_track(self, track: Track) -> Track:
        existing = self._tracks.get(track.id)
        if existing is None:
            if track.spotify_id and any(
                other.spotify_id == track.spotify_id for other in self._tracks.values()
            ):
                raise ValueError(
                    f"spotify_id {track.spotify_id!r} already belongs to another track"
                )
            self._tracks[track.id] = track
            return track
        updates = {
            field: getattr(track, field)
            for field in ("album", "duration_ms", "spotify_id", "file_path")
            if getattr(existing, field) is None and getattr(track, field) is not None
        }
        if not updates:
            return existing
        merged = existing.model_copy(update=updates)
        self._tracks[track.id] = merged
        return merged

    def get_track(self, track_id: str) -> Track | None:
        return self._tracks.get(track_id)

    def list_tracks(self, track_ids: Sequence[str] | None = None) -> list[Track]:
        if track_ids is None:
            return [self._tracks[tid] for tid in sorted(self._tracks)]
        return [self._tracks[tid] for tid in track_ids if tid in self._tracks]

    # -------------------------------------------------------------- features

    def upsert_features(self, features: AudioFeatures) -> AudioFeatures:
        if features.track_id not in self._tracks:
            raise UnknownTrackError(
                f"no track {features.track_id!r} in the catalog", track_id=features.track_id
            )
        key = (features.track_id, features.source)
        merged = features
        if features.source is FeatureSource.MANUAL and key in self._features:
            merged = _merge_manual(self._features[key], features)
        self._features[key] = merged
        return merged

    def get_features(self, track_id: str) -> list[AudioFeatures]:
        return [
            self._features[key]
            for key in sorted(self._features, key=lambda k: (k[0], k[1].value))
            if key[0] == track_id
        ]

    def get_features_for(self, track_ids: Sequence[str]) -> dict[str, list[AudioFeatures]]:
        return {track_id: self.get_features(track_id) for track_id in track_ids}

    def delete_features(self, track_id: str, source: str) -> bool:
        return self._features.pop((track_id, FeatureSource(source)), None) is not None

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
        if playlist_id in self._playlists:
            raise ValueError(f"playlist {playlist_id!r} already exists")
        if self.get_playlist_by_name(name) is not None:
            raise ValueError(f"playlist name {name!r} is already taken")
        if len(track_ids) != len(entry_ids):
            raise ValueError("track_ids and entry_ids must be the same length")
        playlist = Playlist(
            id=playlist_id,
            name=name,
            source=source,
            source_ref=source_ref,
            applied_run_id=None,
            created_at=created_at,
        )
        self._playlists[playlist_id] = playlist
        self._write_entries(playlist_id, track_ids=track_ids, entry_ids=entry_ids)
        return playlist

    def _write_entries(
        self, playlist_id: str, *, track_ids: Sequence[str], entry_ids: Sequence[str]
    ) -> None:
        for position, (entry_id, track_id) in enumerate(zip(entry_ids, track_ids, strict=True)):
            if track_id not in self._tracks:
                raise UnknownTrackError(f"no track {track_id!r} in the catalog", track_id=track_id)
            if entry_id in self._entries:
                raise ValueError(f"entry id {entry_id!r} already exists")
            self._entries[entry_id] = PlaylistEntry(
                id=entry_id, playlist_id=playlist_id, position=position, track_id=track_id
            )

    def get_playlist(self, playlist_id: str) -> Playlist | None:
        return self._playlists.get(playlist_id)

    def get_playlist_by_name(self, name: str) -> Playlist | None:
        folded = name.casefold()
        for playlist in self._playlists.values():
            if playlist.name.casefold() == folded:
                return playlist
        return None

    def list_playlists(self) -> list[Playlist]:
        return sorted(self._playlists.values(), key=lambda p: (p.created_at, p.id))

    def get_entries(self, playlist_id: str) -> list[PlaylistEntry]:
        return sorted(
            (e for e in self._entries.values() if e.playlist_id == playlist_id),
            key=lambda e: e.position,
        )

    def replace_entries(
        self,
        playlist_id: str,
        *,
        track_ids: Sequence[str],
        entry_ids: Sequence[str],
        force: bool = False,
    ) -> list[PlaylistEntry]:
        if playlist_id not in self._playlists:
            raise UnknownPlaylistError(f"no playlist {playlist_id!r}", playlist_id=playlist_id)
        if len(track_ids) != len(entry_ids):
            raise ValueError("track_ids and entry_ids must be the same length")
        run_ids = [r.id for r in self._runs.values() if r.playlist_id == playlist_id]
        if run_ids and not force:
            raise PlaylistHasRunsError(
                f"playlist {playlist_id!r} has {len(run_ids)} run(s); pass --force to replace",
                playlist_id=playlist_id,
                runs=len(run_ids),
            )
        self._drop_runs(playlist_id)
        for entry in self.get_entries(playlist_id):
            del self._entries[entry.id]
        self._write_entries(playlist_id, track_ids=track_ids, entry_ids=entry_ids)
        return self.get_entries(playlist_id)

    def delete_playlist(self, playlist_id: str, *, force: bool = False) -> None:
        if playlist_id not in self._playlists:
            raise UnknownPlaylistError(f"no playlist {playlist_id!r}", playlist_id=playlist_id)
        run_ids = [r.id for r in self._runs.values() if r.playlist_id == playlist_id]
        if run_ids and not force:
            raise PlaylistHasRunsError(
                f"playlist {playlist_id!r} has {len(run_ids)} run(s); pass --force to delete",
                playlist_id=playlist_id,
                runs=len(run_ids),
            )
        self._drop_runs(playlist_id)
        for entry in self.get_entries(playlist_id):
            del self._entries[entry.id]
        del self._playlists[playlist_id]

    def _drop_runs(self, playlist_id: str) -> None:
        """Sanctioned deletion path (2.3): clear the pointer, then drop runs."""
        playlist = self._playlists.get(playlist_id)
        if playlist is not None and playlist.applied_run_id is not None:
            self._playlists[playlist_id] = playlist.model_copy(update={"applied_run_id": None})
        for run_id in [r.id for r in self._runs.values() if r.playlist_id == playlist_id]:
            self._runs.pop(run_id, None)
            self._run_entries.pop(run_id, None)

    # ------------------------------------------------------------------ runs

    def add_run(self, run: ReorderRun, entries: Sequence[RunEntry]) -> ReorderRun:
        if run.playlist_id not in self._playlists:
            raise UnknownPlaylistError(
                f"no playlist {run.playlist_id!r}", playlist_id=run.playlist_id
            )
        if run.id in self._runs:
            raise ValueError(f"run {run.id!r} already exists; runs are append-only")
        known = {entry.id for entry in self.get_entries(run.playlist_id)}
        proposed = [entry.entry_id for entry in entries]
        if len(proposed) != len(set(proposed)) or set(proposed) != known:
            raise ValueError("run entries must be a permutation of the playlist's entries")
        self._runs[run.id] = run
        self._run_entries[run.id] = sorted(entries, key=lambda e: e.position)
        return run

    def get_run(self, run_id: str) -> ReorderRun | None:
        return self._runs.get(run_id)

    def list_runs(self, playlist_id: str | None = None) -> list[ReorderRun]:
        runs = [
            run
            for run in self._runs.values()
            if playlist_id is None or run.playlist_id == playlist_id
        ]
        return sorted(runs, key=lambda r: (r.created_at, r.id))

    def get_run_entries(self, run_id: str) -> list[RunEntry]:
        return list(self._run_entries.get(run_id, []))

    def apply_run(self, run_id: str) -> Playlist:
        run = self._runs.get(run_id)
        if run is None:
            raise UnknownRunError(f"no run {run_id!r}", run_id=run_id)
        for run_entry in self._run_entries[run_id]:
            entry = self._entries[run_entry.entry_id]
            # Positions change in place; entry ids are immutable so RunEntry
            # references survive apply (2.4).
            self._entries[entry.id] = entry.model_copy(update={"position": run_entry.position})
        playlist = self._playlists[run.playlist_id].model_copy(update={"applied_run_id": run_id})
        self._playlists[run.playlist_id] = playlist
        return playlist


__all__ = ["InMemoryRepository"]
