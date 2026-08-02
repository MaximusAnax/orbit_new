"""Provider interfaces — one Protocol per external capability (CONVENTIONS 3).

Offline implementations are the defaults and are what tests and evals
exercise; live implementations activate only when credentials or optional
dependencies are present and must never be imported by the offline path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from flowlist.engine.models import (
    AudioFeatures,
    ExportFormat,
    ImportedPlaylist,
    PlaylistExport,
    Track,
)


@runtime_checkable
class MetadataProvider(Protocol):
    """Bulk audio-feature lookup for known tracks."""

    #: Which :class:`~flowlist.engine.models.FeatureSource` rows this provider
    #: writes, so resolution precedence can address it.
    name: str

    def available(self) -> bool:
        """Whether this provider can serve requests right now."""

    def get_features(self, tracks: Sequence[Track]) -> dict[str, AudioFeatures]:
        """Features keyed by track id; an absent key means "unknown track"."""


@runtime_checkable
class LocalAudioAnalyzer(Protocol):
    """Feature extraction from audio files the user owns (US-6)."""

    name: str

    def available(self) -> bool: ...

    def analyze(self, file_path: str) -> AudioFeatures | None:
        """Analyse one file, or return ``None`` if it cannot be decoded."""


@runtime_checkable
class PlaylistReader(Protocol):
    """Turn an external playlist representation into catalog-ready rows."""

    #: The :class:`~flowlist.engine.models.PlaylistSource` this reader produces.
    source: str

    def read(self, source: str, *, name: str | None = None) -> ImportedPlaylist:
        """Parse ``source`` (a path) into catalog-ready rows.

        ``name`` overrides whatever name the source carries; readers whose
        format has no name of its own (CSV) require it.
        """


@runtime_checkable
class PlaylistWriter(Protocol):
    """Serialise a run's ordering to a portable file (FR-12)."""

    format: ExportFormat

    def render(self, export: PlaylistExport) -> str: ...

    def write(self, export: PlaylistExport, path: str) -> str:
        """Write ``export`` to ``path`` and return the path written."""


__all__ = [
    "LocalAudioAnalyzer",
    "MetadataProvider",
    "PlaylistReader",
    "PlaylistWriter",
]
