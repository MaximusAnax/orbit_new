"""The orchestration layer: import, analyze idempotence, and export payloads.

These behaviours belong to no single adapter or engine module, so they are
tested where they live (SCOPE.md Architecture: ``services.py`` is the only
layer touching both I/O and the engine).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from flowlist.engine.models import (
    AudioFeatures,
    FeatureSource,
    PlaylistSource,
    ReorderParams,
    Track,
)
from flowlist.errors import PlaylistImportError
from flowlist.store.memory import InMemoryRepository
from flowlist_testkit import NOW

from flowlist import services

JSON_PLAYLIST = """
{"name": "party", "tracks": [
  {"title": "One More Hour", "artist": "Synthetic Sun",
   "features": {"bpm": 124.0, "key_pc": 9, "mode": 0, "energy": 0.71, "loudness_db": -7.2}},
  {"title": "Night Drive", "artist": "Vera Lux",
   "features": {"bpm": 126.5, "key_pc": 4, "mode": 0, "energy": 0.76, "loudness_db": -6.4}},
  {"title": "Slow Burn", "artist": "Marta Quiet"},
  {"title": "Breakline", "artist": "Cyan Drift",
   "features": {"bpm": 174.0, "key_pc": 2, "mode": 0, "energy": 0.88, "loudness_db": -5.1}}
]}
"""


class CountingProvider:
    """A fixture provider that records how many tracks it was asked about."""

    name = "streaming"

    def __init__(self, features: dict[str, dict[str, float]]) -> None:
        self._features = features
        self.calls: list[list[str]] = []

    def available(self) -> bool:
        return True

    def get_features(self, tracks: Sequence[Track]) -> dict[str, AudioFeatures]:
        self.calls.append([track.id for track in tracks])
        return {
            track.id: AudioFeatures(
                track_id=track.id,
                source=FeatureSource.STREAMING,
                analyzed_at=NOW,
                **self._features[track.id],
            )
            for track in tracks
            if track.id in self._features
        }


def _import(repo: InMemoryRepository) -> str:
    imported = services.read_playlist_content(JSON_PLAYLIST, name=None, fmt=PlaylistSource.JSON)
    return services.import_playlist(repo, imported, now=NOW).playlist.id


def test_fr1_import_is_idempotent_in_the_catalog(repo: InMemoryRepository) -> None:
    """Re-importing the same file does not duplicate catalog rows (US-1)."""
    first = _import(repo)
    catalog_after_first = {track.id for track in repo.list_tracks()}

    imported = services.read_playlist_content(
        JSON_PLAYLIST.replace('"party"', '"party-2"'), name=None, fmt=PlaylistSource.JSON
    )
    second = services.import_playlist(repo, imported, now=NOW).playlist.id

    assert first != second
    assert {track.id for track in repo.list_tracks()} == catalog_after_first
    assert len(catalog_after_first) == 4


def test_fr3_analyze_is_idempotent(repo: InMemoryRepository) -> None:
    """A fully-resolved track is never sent to a provider a second time."""
    playlist_id = _import(repo)
    view = services.load_playlist(repo, playlist_id)
    gap = next(tid for tid, resolved in view.features.items() if resolved.bpm is None)
    provider = CountingProvider(
        {gap: {"bpm": 90.0, "key_pc": 7, "mode": 1, "energy": 0.42, "loudness_db": -11.0}}
    )

    first = services.analyze_playlist(repo, playlist_id, now=NOW, providers=[provider])
    assert first.full == 4
    assert provider.calls == [[gap]], "only the track with gaps should be queried"

    second = services.analyze_playlist(repo, playlist_id, now=NOW, providers=[provider])
    assert second.full == 4
    assert len(provider.calls) == 1, "a second analyze must make no new provider calls"


def test_fr4_manual_override_beats_a_provider(repo: InMemoryRepository) -> None:
    playlist_id = _import(repo)
    view = services.load_playlist(repo, playlist_id)
    track_id = view.entries[0].track_id

    services.set_manual_features(repo, track_id, now=NOW, bpm=128.0)
    resolved = services.load_playlist(repo, playlist_id).features[track_id]
    assert resolved.bpm == 128.0
    assert resolved.field_sources["bpm"] is FeatureSource.MANUAL
    assert resolved.field_sources["energy"] is FeatureSource.IMPORT


def test_fr2_directory_import_reads_tags_or_filenames(
    repo: InMemoryRepository, tmp_path: Path
) -> None:
    """FR-2: a folder of owned files becomes a playlist in path order."""
    (tmp_path / "b").mkdir()
    (tmp_path / "Vera Lux - Night Drive.mp3").write_bytes(b"not really audio")
    (tmp_path / "b" / "Cyan Drift - Breakline.flac").write_bytes(b"also not audio")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    imported = services.read_playlist(str(tmp_path), name="gym")
    result = services.import_playlist(repo, imported, now=NOW)

    assert result.playlist.source is PlaylistSource.DIRECTORY
    # Lexicographic *path* order (FR-2): "Vera Lux - ..." sorts before "b/..."
    # because 'V' < 'b' in byte order.
    assert [track.artist for track in result.tracks] == ["Vera Lux", "Cyan Drift"]
    assert all(track.id.startswith("file:") for track in result.tracks)
    assert all(track.file_path for track in result.tracks)
    assert {issue.code for issue in result.warnings} == {"filename_fallback"}


def test_detect_format_needs_a_recognisable_source(tmp_path: Path) -> None:
    assert services.detect_format(str(tmp_path)) is PlaylistSource.DIRECTORY
    assert services.detect_format("party.csv") is PlaylistSource.CSV
    assert services.detect_format("party.json") is PlaylistSource.JSON
    with pytest.raises(PlaylistImportError):
        services.detect_format("party.xml")


def test_fr12_export_uses_the_features_the_run_was_scored_with(
    repo: InMemoryRepository,
) -> None:
    """A run's export stays self-contained after the catalog changes (2.6)."""
    playlist_id = _import(repo)
    outcome = services.reorder_playlist(repo, playlist_id, ReorderParams(seed=7), now=NOW)
    before = services.render_export(repo, outcome.run.id, "csv")

    first_track = repo.get_entries(playlist_id)[0].track_id
    services.set_manual_features(repo, first_track, now=NOW, bpm=99.0)

    assert services.render_export(repo, outcome.run.id, "csv") == before


def test_fr12_export_of_a_single_entry_playlist(repo: InMemoryRepository) -> None:
    """A run with one entry has no transitions and still exports."""
    imported = services.read_playlist_content(
        '{"name": "solo", "tracks": [{"title": "Only", "artist": "One",'
        ' "features": {"bpm": 120.0}}]}',
        name=None,
        fmt=PlaylistSource.JSON,
    )
    playlist_id = services.import_playlist(repo, imported, now=NOW).playlist.id
    outcome = services.reorder_playlist(repo, playlist_id, ReorderParams(seed=1), now=NOW)
    assert outcome.after.transitions == []
    rendered = services.render_export(repo, outcome.run.id, "m3u")
    assert rendered.splitlines()[0] == "#EXTM3U"
    assert "One - Only" in rendered


def test_playlists_are_addressable_by_name_or_id(repo: InMemoryRepository) -> None:
    playlist_id = _import(repo)
    assert services.require_playlist(repo, playlist_id).id == playlist_id
    assert services.require_playlist(repo, "PARTY").id == playlist_id
