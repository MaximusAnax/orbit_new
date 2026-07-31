"""``PlaylistReader`` implementations (FR-1, FR-2).

Three sources: Exportify-layout CSV, the documented JSON schema
(DATA_MODEL 3.1), and a directory of owned audio files.

The sentinel policy is the important part (FR-1, D10).  Real exports carry
``Key = -1`` ("no key detected") and sometimes ``Tempo = 0``; those become
nulls with a per-row warning and the row *still imports*.  Only a structurally
unparsable row is skipped, and then with its line number.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from flowlist.engine.identity import parse_filename, spotify_id_from_uri
from flowlist.engine.models import (
    BPM_MAX,
    BPM_MIN,
    FeatureSnapshot,
    ImportedPlaylist,
    ImportedTrack,
    ImportIssue,
    PlaylistSource,
)
from flowlist.errors import PlaylistImportError

#: Audio extensions the directory reader picks up (FR-2).
AUDIO_EXTENSIONS: tuple[str, ...] = (".mp3", ".m4a", ".flac", ".ogg", ".wav")

#: Exportify's de-facto column layout (FR-1).  Lookup is case-insensitive and
#: whitespace-tolerant because real exports vary in capitalisation.
CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "uri": ("track uri", "uri", "spotify uri", "track id"),
    "title": ("track name", "title", "name"),
    "artist": ("artist name(s)", "artist name", "artist names", "artist", "artists"),
    "album": ("album name", "album"),
    "duration_ms": ("duration (ms)", "duration_ms", "duration"),
    "bpm": ("tempo", "bpm"),
    "key_pc": ("key",),
    "mode": ("mode",),
    "energy": ("energy",),
    "danceability": ("danceability",),
    "loudness_db": ("loudness", "loudness_db"),
    "valence": ("valence",),
}


def _canonical(header: str) -> str:
    return header.strip().lower()


def _column_index(headers: list[str]) -> dict[str, int]:
    lookup = {_canonical(h): i for i, h in enumerate(headers)}
    resolved: dict[str, int] = {}
    for field, aliases in CSV_COLUMNS.items():
        for alias in aliases:
            if alias in lookup:
                resolved[field] = lookup[alias]
                break
    return resolved


def _as_float(value: str | float | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: str | float | None) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _clean_unit(value: float | None) -> float | None:
    """Clamp a [0,1] feature; anything wildly out of range is treated as absent."""
    if value is None:
        return None
    if 0.0 <= value <= 1.0:
        return value
    return None


def _build_features(
    raw: dict[str, Any], line: int | None, issues: list[ImportIssue]
) -> FeatureSnapshot | None:
    """Apply FR-1's sentinel mapping and return a snapshot (or ``None``)."""
    bpm = _as_float(raw.get("bpm"))
    if bpm is not None and not (BPM_MIN <= bpm <= BPM_MAX):
        issues.append(
            ImportIssue(
                line=line,
                code="bpm_sentinel" if bpm == 0 else "bpm_out_of_range",
                message=(
                    f"tempo {bpm:g} is outside {BPM_MIN:g}-{BPM_MAX:g} BPM; imported without a tempo"
                ),
            )
        )
        bpm = None

    key_pc = _as_int(raw.get("key_pc"))
    mode = _as_int(raw.get("mode"))
    if key_pc is not None and not (0 <= key_pc <= 11):
        issues.append(
            ImportIssue(
                line=line,
                code="key_sentinel" if key_pc == -1 else "key_out_of_range",
                message=(
                    f"key {key_pc} means 'no key detected'; imported without a key"
                    if key_pc == -1
                    else f"key {key_pc} is outside pitch classes 0-11; imported without a key"
                ),
            )
        )
        key_pc = None
    if mode is not None and mode not in (0, 1):
        mode = None
    # key_pc and mode are set or null together (DATA_MODEL 2.2).
    if key_pc is None or mode is None:
        key_pc = mode = None

    loudness = _as_float(raw.get("loudness_db"))
    if loudness is not None and not (-60.0 <= loudness <= 0.0):
        issues.append(
            ImportIssue(
                line=line,
                code="loudness_out_of_range",
                message=f"loudness {loudness:g} dB is outside -60..0; imported without loudness",
            )
        )
        loudness = None

    snapshot = FeatureSnapshot(
        bpm=bpm,
        key_pc=key_pc,
        mode=mode,
        energy=_clean_unit(_as_float(raw.get("energy"))),
        danceability=_clean_unit(_as_float(raw.get("danceability"))),
        loudness_db=loudness,
    )
    if snapshot == FeatureSnapshot():
        return None
    return snapshot


class CsvPlaylistReader:
    """Exportify-layout CSV (FR-1)."""

    source = PlaylistSource.CSV

    def read(self, source: str, *, name: str | None = None) -> ImportedPlaylist:
        """``source`` is a file path; use :meth:`read_text` for in-memory content.

        A CSV export carries no playlist name of its own, so ``name`` is
        required here even though the protocol makes it optional.
        """
        if not name:
            raise PlaylistImportError("CSV import requires a playlist name (--name)")
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise PlaylistImportError(f"cannot read CSV playlist: {exc}", path=str(path)) from exc
        return self.read_text(text, name=name, source_ref=str(path))

    def read_text(self, text: str, *, name: str, source_ref: str | None = None) -> ImportedPlaylist:
        reader = csv.reader(io.StringIO(text))
        try:
            headers = next(reader)
        except StopIteration:
            raise PlaylistImportError("CSV playlist is empty", source=source_ref) from None

        columns = _column_index(headers)
        if "title" not in columns:
            raise PlaylistImportError(
                "CSV playlist has no recognisable track-name column",
                headers=headers,
            )

        tracks: list[ImportedTrack] = []
        issues: list[ImportIssue] = []
        for line, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue

            def cell(field: str, row: list[str] = row) -> str | None:
                index = columns.get(field)
                if index is None or index >= len(row):
                    return None
                value = row[index].strip()
                return value or None

            title = cell("title")
            if not title:
                issues.append(
                    ImportIssue(
                        line=line,
                        code="malformed_row",
                        message="row has no track name; skipped",
                        skipped=True,
                    )
                )
                continue

            uri = cell("uri")
            spotify_id = spotify_id_from_uri(uri) if uri else None
            raw = {field: cell(field) for field in CSV_COLUMNS if field not in ("uri", "title")}
            features = _build_features(raw, line, issues)
            duration = _as_int(cell("duration_ms"))
            tracks.append(
                ImportedTrack(
                    title=title,
                    artist=cell("artist") or "",
                    album=cell("album"),
                    duration_ms=duration if duration and duration > 0 else None,
                    spotify_id=spotify_id,
                    features=features,
                )
            )

        return ImportedPlaylist(
            name=name,
            source=self.source,
            source_ref=source_ref,
            tracks=tracks,
            issues=issues,
        )


class JsonPlaylistReader:
    """The documented JSON schema (DATA_MODEL 3.1)."""

    source = PlaylistSource.JSON

    def read(self, source: str, *, name: str | None = None) -> ImportedPlaylist:
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PlaylistImportError(f"cannot read JSON playlist: {exc}", path=str(path)) from exc
        return self.read_text(text, name=name, source_ref=str(path))

    def read_text(
        self, text: str, *, name: str | None = None, source_ref: str | None = None
    ) -> ImportedPlaylist:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlaylistImportError(
                f"playlist JSON is not valid: {exc}", source=source_ref
            ) from exc
        if not isinstance(payload, dict) or "tracks" not in payload:
            raise PlaylistImportError(
                "playlist JSON must be an object with a 'tracks' array", source=source_ref
            )

        playlist_name = name or payload.get("name")
        if not playlist_name:
            raise PlaylistImportError("playlist JSON has no name and none was supplied")

        tracks: list[ImportedTrack] = []
        issues: list[ImportIssue] = []
        for index, row in enumerate(payload["tracks"]):
            line = index + 1
            if not isinstance(row, dict) or not row.get("title"):
                issues.append(
                    ImportIssue(
                        line=line,
                        code="malformed_row",
                        message="track object has no title; skipped",
                        skipped=True,
                    )
                )
                continue
            features = _build_features(dict(row.get("features") or {}), line, issues)
            duration = _as_int(row.get("duration_ms"))
            spotify_id = row.get("spotify_id")
            if spotify_id:
                spotify_id = spotify_id_from_uri(str(spotify_id)) or None
            tracks.append(
                ImportedTrack(
                    title=str(row["title"]),
                    artist=str(row.get("artist") or ""),
                    album=row.get("album"),
                    duration_ms=duration if duration and duration > 0 else None,
                    spotify_id=spotify_id,
                    file_path=row.get("file_path"),
                    features=features,
                )
            )

        return ImportedPlaylist(
            name=str(playlist_name),
            source=self.source,
            source_ref=source_ref,
            tracks=tracks,
            issues=issues,
        )


def sha1_of_file(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    """sha1 of a file's bytes — the ``file:`` half of D9's identity scheme."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def read_tags(path: str | Path) -> tuple[str | None, str | None, str | None, int | None]:
    """``(title, artist, album, duration_ms)`` from file tags, if readable.

    FR-2 names ``mutagen`` for this (pure Python: it parses tags without
    decoding audio).  It is imported lazily and its absence is not an error —
    the caller falls back to the ``Artist - Title`` filename pattern, which is
    exactly the documented degradation path.
    """
    try:
        import mutagen
    except ImportError:
        return None, None, None, None
    try:
        audio = mutagen.File(str(path), easy=True)
    except Exception:
        return None, None, None, None
    if audio is None:
        return None, None, None, None

    def first(key: str) -> str | None:
        values = audio.get(key) if hasattr(audio, "get") else None
        if not values:
            return None
        value = values[0] if isinstance(values, list) else values
        text = str(value).strip()
        return text or None

    duration = None
    info = getattr(audio, "info", None)
    if info is not None and getattr(info, "length", None):
        duration = int(float(info.length) * 1000)
    return first("title"), first("artist"), first("album"), duration


class DirectoryPlaylistReader:
    """Recursive scan of a music folder (FR-2).

    Files are ordered lexicographically by path, identity is the sha1 of the
    file's bytes (D9), and *no audio is decoded* — tags are read with mutagen
    when available, otherwise the ``Artist - Title`` filename pattern is used.
    """

    source = PlaylistSource.DIRECTORY

    def __init__(self, extensions: Iterable[str] = AUDIO_EXTENSIONS) -> None:
        self._extensions = {ext.lower() for ext in extensions}

    def read(self, source: str, *, name: str | None = None) -> ImportedPlaylist:
        root = Path(source)
        if not root.is_dir():
            raise PlaylistImportError(f"not a directory: {source}", path=str(root))

        paths = sorted(
            (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in self._extensions),
            key=lambda p: str(p),
        )
        tracks: list[ImportedTrack] = []
        issues: list[ImportIssue] = []
        for line, path in enumerate(paths, start=1):
            try:
                digest = sha1_of_file(path)
            except OSError as exc:
                issues.append(
                    ImportIssue(
                        line=line,
                        code="unreadable_file",
                        message=f"{path}: {exc}",
                        skipped=True,
                    )
                )
                continue
            tag_title, tag_artist, album, duration = read_tags(path)
            file_artist, file_title = parse_filename(path.stem)
            title = tag_title or file_title
            artist = tag_artist or file_artist
            if not tag_title and not tag_artist:
                issues.append(
                    ImportIssue(
                        line=line,
                        code="filename_fallback",
                        message=f"{path.name}: no tags found, using the filename",
                    )
                )
            tracks.append(
                ImportedTrack(
                    title=title,
                    artist=artist,
                    album=album,
                    duration_ms=duration if duration and duration > 0 else None,
                    file_path=str(path.resolve()),
                    file_sha1=digest,
                )
            )

        return ImportedPlaylist(
            name=name or root.name,
            source=self.source,
            source_ref=str(root.resolve()),
            tracks=tracks,
            issues=issues,
        )


__all__ = [
    "AUDIO_EXTENSIONS",
    "CSV_COLUMNS",
    "CsvPlaylistReader",
    "DirectoryPlaylistReader",
    "JsonPlaylistReader",
    "read_tags",
    "sha1_of_file",
]
