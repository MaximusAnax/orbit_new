"""Provider interfaces plus their offline and live implementations.

Importing this package pulls in the offline implementations only; the live
ones (:class:`~flowlist.adapters.metadata.SpotifyMetadataProvider`,
:class:`~flowlist.adapters.analyzer.LibrosaLocalAnalyzer`) are importable but
load their credentials/optional dependencies lazily, so nothing here requires
network access or the ``audio`` extra.
"""

from flowlist.adapters.analyzer import FixtureLocalAnalyzer, LibrosaLocalAnalyzer
from flowlist.adapters.base import (
    LocalAudioAnalyzer,
    MetadataProvider,
    PlaylistReader,
    PlaylistWriter,
)
from flowlist.adapters.metadata import (
    ChainedMetadataProvider,
    FixtureMetadataProvider,
    SpotifyMetadataProvider,
)
from flowlist.adapters.readers import (
    CsvPlaylistReader,
    DirectoryPlaylistReader,
    JsonPlaylistReader,
)
from flowlist.adapters.writers import CsvWriter, JsonWriter, M3uWriter, writer_for

__all__ = [
    "ChainedMetadataProvider",
    "CsvPlaylistReader",
    "CsvWriter",
    "DirectoryPlaylistReader",
    "FixtureLocalAnalyzer",
    "FixtureMetadataProvider",
    "JsonPlaylistReader",
    "JsonWriter",
    "LibrosaLocalAnalyzer",
    "LocalAudioAnalyzer",
    "M3uWriter",
    "MetadataProvider",
    "PlaylistReader",
    "PlaylistWriter",
    "SpotifyMetadataProvider",
    "writer_for",
]
