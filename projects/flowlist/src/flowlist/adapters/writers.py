"""``PlaylistWriter`` implementations — M3U8, CSV and JSON export (FR-12).

Each writer renders a :class:`~flowlist.engine.models.PlaylistExport` to text;
:meth:`write` is the only method that touches the filesystem, so rendering
stays unit-testable without temp files.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from flowlist.engine.keys import relation_label
from flowlist.engine.models import ExportFormat, ExportRow, PlaylistExport

#: CSV export columns: the import layout plus the three run columns (3.2).
EXPORT_COLUMNS: tuple[str, ...] = (
    "Track URI",
    "Track Name",
    "Artist Name(s)",
    "Album Name",
    "Duration (ms)",
    "Tempo",
    "Key",
    "Mode",
    "Energy",
    "Danceability",
    "Loudness",
    "Position",
    "Transition Score",
    "Key Relation",
)


class M3uWriter:
    """Extended M3U8 (DATA_MODEL 3.2).

    Entries without a known local file become a comment line rather than a
    broken path, so the file stays valid for players that ignore comments.
    """

    format = ExportFormat.M3U

    def render(self, export: PlaylistExport) -> str:
        lines = ["#EXTM3U"]
        for row in export.rows:
            track = row.track
            seconds = round(track.duration_ms / 1000) if track.duration_ms else -1
            lines.append(f"#EXTINF:{seconds},{track.artist} - {track.title}")
            if track.file_path:
                lines.append(track.file_path)
            else:
                lines.append(f"# no local file: {track.artist} - {track.title}")
        return "\n".join(lines) + "\n"

    def write(self, export: PlaylistExport, path: str) -> str:
        Path(path).write_text(self.render(export), encoding="utf-8")
        return path


class CsvWriter:
    """Import columns plus ``Position``, ``Transition Score``, ``Key Relation``."""

    format = ExportFormat.CSV

    @staticmethod
    def _row(row: ExportRow) -> list[str]:
        track = row.track
        features = row.features
        transition = row.transition

        def number(value: float | int | None) -> str:
            return "" if value is None else f"{value:g}"

        return [
            f"spotify:track:{track.spotify_id}" if track.spotify_id else "",
            track.title,
            track.artist,
            track.album or "",
            number(track.duration_ms),
            number(features.bpm if features else None),
            number(features.key_pc if features else None),
            number(features.mode if features else None),
            number(features.energy if features else None),
            number(features.danceability if features else None),
            number(features.loudness_db if features else None),
            str(row.position),
            "" if transition is None else f"{transition.score:.4f}",
            "" if transition is None else relation_label(transition.key_relation),
        ]

    def render(self, export: PlaylistExport) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(EXPORT_COLUMNS)
        for row in export.rows:
            writer.writerow(self._row(row))
        return buffer.getvalue()

    def write(self, export: PlaylistExport, path: str) -> str:
        Path(path).write_text(self.render(export), encoding="utf-8")
        return path


class JsonWriter:
    """Full run + entries dump — the ``GET /runs/{id}`` payload (3.2)."""

    format = ExportFormat.JSON

    def render(self, export: PlaylistExport) -> str:
        payload = {
            "playlist_name": export.playlist_name,
            "run": json.loads(export.run.model_dump_json()) if export.run else None,
            "entries": [
                {
                    "position": row.position,
                    "entry_id": row.entry_id,
                    "track": json.loads(row.track.model_dump_json()),
                    "features": json.loads(row.features.model_dump_json())
                    if row.features
                    else None,
                    "transition": json.loads(row.transition.model_dump_json())
                    if row.transition
                    else None,
                }
                for row in export.rows
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"

    def write(self, export: PlaylistExport, path: str) -> str:
        Path(path).write_text(self.render(export), encoding="utf-8")
        return path


WRITERS: dict[ExportFormat, type[M3uWriter] | type[CsvWriter] | type[JsonWriter]] = {
    ExportFormat.M3U: M3uWriter,
    ExportFormat.CSV: CsvWriter,
    ExportFormat.JSON: JsonWriter,
}


def writer_for(fmt: ExportFormat | str) -> M3uWriter | CsvWriter | JsonWriter:
    """Look up a writer by format name (FR-12's ``--format``)."""
    key = ExportFormat(fmt) if not isinstance(fmt, ExportFormat) else fmt
    return WRITERS[key]()


__all__ = [
    "EXPORT_COLUMNS",
    "WRITERS",
    "CsvWriter",
    "JsonWriter",
    "M3uWriter",
    "writer_for",
]
