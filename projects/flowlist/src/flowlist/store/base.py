"""Repository interface (SCOPE Architecture-Store).

Two backends implement it: :class:`~flowlist.store.sqlite.SqliteRepository`
(the default, schema in DATA_MODEL 4) and
:class:`~flowlist.store.memory.InMemoryRepository` (tests).  No engine logic
lives here — the store persists models and enforces the *storage* invariants
(uniqueness, contiguous positions, append-only runs, the FK order that keeps
run history intact).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from flowlist.engine.models import (
    AudioFeatures,
    Playlist,
    PlaylistEntry,
    PlaylistSource,
    ReorderRun,
    RunEntry,
    Track,
)


class Repository(ABC):
    """Persistence for tracks, playlists, features and runs."""

    # ---------------------------------------------------------------- tracks
    @abstractmethod
    def upsert_track(self, track: Track) -> Track:
        """Insert, or fill nulls on an existing row without overwriting (2.1)."""

    @abstractmethod
    def get_track(self, track_id: str) -> Track | None: ...

    @abstractmethod
    def list_tracks(self, track_ids: Sequence[str] | None = None) -> list[Track]: ...

    # -------------------------------------------------------------- features
    @abstractmethod
    def upsert_features(self, features: AudioFeatures) -> AudioFeatures:
        """One row per (track, source).

        ``manual`` merges field-wise (FR-4 upsert); every other source replaces
        the whole record on re-analysis (2.2).
        """

    @abstractmethod
    def get_features(self, track_id: str) -> list[AudioFeatures]: ...

    @abstractmethod
    def get_features_for(self, track_ids: Sequence[str]) -> dict[str, list[AudioFeatures]]: ...

    @abstractmethod
    def delete_features(self, track_id: str, source: str) -> bool: ...

    # ------------------------------------------------------------- playlists
    @abstractmethod
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
        """Create a playlist and its full entry set in one transaction."""

    @abstractmethod
    def get_playlist(self, playlist_id: str) -> Playlist | None: ...

    @abstractmethod
    def get_playlist_by_name(self, name: str) -> Playlist | None:
        """Case-insensitive lookup — playlist names address the CLI (2.3)."""

    @abstractmethod
    def list_playlists(self) -> list[Playlist]: ...

    @abstractmethod
    def get_entries(self, playlist_id: str) -> list[PlaylistEntry]:
        """Entries in stored position order."""

    @abstractmethod
    def replace_entries(
        self,
        playlist_id: str,
        *,
        track_ids: Sequence[str],
        entry_ids: Sequence[str],
        force: bool = False,
    ) -> list[PlaylistEntry]:
        """Re-import into an existing playlist (FR-1 ``--replace``).

        Refuses with :class:`~flowlist.errors.PlaylistHasRunsError` when runs
        reference the playlist unless ``force``, which deletes those runs and
        clears ``applied_run_id`` in the same transaction.
        """

    @abstractmethod
    def delete_playlist(self, playlist_id: str, *, force: bool = False) -> None:
        """Delete a playlist; refused when runs exist unless ``force`` (2.3)."""

    # ------------------------------------------------------------------ runs
    @abstractmethod
    def add_run(self, run: ReorderRun, entries: Sequence[RunEntry]) -> ReorderRun:
        """Append a run and its entries; runs are never updated afterwards."""

    @abstractmethod
    def get_run(self, run_id: str) -> ReorderRun | None: ...

    @abstractmethod
    def list_runs(self, playlist_id: str | None = None) -> list[ReorderRun]: ...

    @abstractmethod
    def get_run_entries(self, run_id: str) -> list[RunEntry]: ...

    @abstractmethod
    def apply_run(self, run_id: str) -> Playlist:
        """Rewrite entry positions to the run's ordering (FR-12).

        Entry ids are preserved so ``run_entries.entry_id`` references survive;
        the update is two-phase inside one transaction (2.4).
        """

    # ----------------------------------------------------------- lifecycle
    def close(self) -> None:
        """Release backend resources.

        The default is deliberately a no-op: an in-memory backend has nothing
        to release, and every caller may use the repository as a context
        manager regardless of backend.
        """
        return None

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["Repository"]
