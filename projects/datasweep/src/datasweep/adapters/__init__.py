"""Provider interfaces plus their offline (default) and live implementations.

Offline adapters are what tests and evals exercise; live adapters activate only
when their optional extra is installed and are never imported at module load of
the offline path (CONVENTIONS.md §3).
"""

from .artifacts import (
    ArtifactPaths,
    ArtifactWriter,
    LocalArtifactWriter,
    artifact_dir_name,
    audit_filename,
    cleaned_filename,
    serialize_audit,
    serialize_findings,
    serialize_table,
)
from .clock import Clock, FixedClock, SystemClock
from .encoding import (
    CharsetNormalizerDetector,
    DetectedEncoding,
    EncodingDetector,
    SimpleEncodingDetector,
)
from .notifier import DesktopNotifier, LogNotifier, Notifier, NullNotifier
from .readers import (
    CsvReader,
    FileMeta,
    JsonlReader,
    ReadResult,
    TableReader,
    XlsxReader,
    default_readers,
    reader_for,
)
from .watcher import FileObservation, PollingScanner, WatchdogWatcher, Watcher

__all__ = [
    "ArtifactPaths",
    "ArtifactWriter",
    "CharsetNormalizerDetector",
    "Clock",
    "CsvReader",
    "DesktopNotifier",
    "DetectedEncoding",
    "EncodingDetector",
    "FileMeta",
    "FileObservation",
    "FixedClock",
    "JsonlReader",
    "LocalArtifactWriter",
    "LogNotifier",
    "Notifier",
    "NullNotifier",
    "PollingScanner",
    "ReadResult",
    "SimpleEncodingDetector",
    "SystemClock",
    "TableReader",
    "WatchdogWatcher",
    "Watcher",
    "XlsxReader",
    "artifact_dir_name",
    "audit_filename",
    "cleaned_filename",
    "default_readers",
    "reader_for",
    "serialize_audit",
    "serialize_findings",
    "serialize_table",
]
