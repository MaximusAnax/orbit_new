"""The Watcher port: which files are candidates, and which are settled (FR-1).

``PollingScanner`` is the deterministic default (D15): a lexicographic
``os.scandir`` walk with the ignore patterns applied.  It never sleeps and
never reads the clock — the settle check compares two observations supplied by
the caller, so tests inject time instead of waiting for it.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from ..engine.models import WatchedFolder
from ..errors import MissingDependencyError

#: Temp/partial-write patterns skipped by every scan (FR-1).
IGNORE_PATTERNS: tuple[str, ...] = ("~$*", "*.partial", "*.tmp", ".*")


class FileObservation(BaseModel):
    """One (path, size, mtime) sighting, stamped with the caller's clock."""

    model_config = ConfigDict(frozen=True)

    path: str
    size: int
    mtime: float
    observed_at: datetime

    def same_content_signature(self, other: FileObservation) -> bool:
        return self.size == other.size and self.mtime == other.mtime


@runtime_checkable
class Watcher(Protocol):
    def scan(
        self, folders: Sequence[WatchedFolder], now: datetime
    ) -> list[FileObservation]:  # pragma: no cover - protocol
        ...


def is_ignored(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in IGNORE_PATTERNS)


def matches_include(name: str, include: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in include)


class PollingScanner:
    """Deterministic filesystem walk in lexicographic path order."""

    def scan(self, folders: Sequence[WatchedFolder], now: datetime) -> list[FileObservation]:
        observations: list[FileObservation] = []
        for folder in sorted(folders, key=lambda item: item.path):
            if not folder.enabled:
                continue
            observations.extend(self._scan_folder(folder, now))
        return sorted(observations, key=lambda item: item.path)

    def _scan_folder(self, folder: WatchedFolder, now: datetime) -> list[FileObservation]:
        root = Path(folder.path)
        output_dir = os.path.normpath(folder.output_dir)
        found: list[FileObservation] = []
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue
            for entry in entries:
                if is_ignored(entry.name):
                    continue
                full = os.path.normpath(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if folder.recursive and full != output_dir:
                        stack.append(Path(full))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if os.path.normpath(os.path.dirname(full)) == output_dir:
                    continue
                if not matches_include(entry.name, folder.include):
                    continue
                info = entry.stat()
                found.append(
                    FileObservation(
                        path=full, size=info.st_size, mtime=info.st_mtime, observed_at=now
                    )
                )
        return found

    @staticmethod
    def is_stable(
        previous: FileObservation | None, current: FileObservation, settle_seconds: float
    ) -> bool:
        """FR-1: size and mtime unchanged across ≥ ``settle_seconds``.

        A file seen for the first time is never stable — that is the whole
        point: an event-driven watcher fires on create, before the writer has
        finished (D15).
        """
        if previous is None or previous.path != current.path:
            return False
        if not previous.same_content_signature(current):
            return False
        elapsed = (current.observed_at - previous.observed_at).total_seconds()
        return elapsed >= settle_seconds

    @classmethod
    def partition(
        cls,
        previous: dict[str, FileObservation],
        current: Sequence[FileObservation],
        settle_seconds: float,
    ) -> tuple[list[FileObservation], list[FileObservation]]:
        """Split a scan into (ready, deferred) using the settle check."""
        ready: list[FileObservation] = []
        deferred: list[FileObservation] = []
        for observation in current:
            target = (
                ready
                if cls.is_stable(previous.get(observation.path), observation, settle_seconds)
                else deferred
            )
            target.append(observation)
        return ready, deferred


class WatchdogWatcher:
    """Live adapter (extra ``watch``): OS filesystem events via ``watchdog``.

    Event delivery is inherently non-deterministic, so this is smoke-tested
    only and never eval-gated (SCOPE.md §Non-goals).  Events are debounced into
    the same :class:`FileObservation` shape the polling scanner produces, and
    the settle check still applies — an event fires when a write *starts*.
    """

    def __init__(self) -> None:
        try:
            from watchdog.observers import Observer  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MissingDependencyError(
                "WatchdogWatcher needs the 'watch' extra: uv pip install 'datasweep[watch]'"
            ) from exc
        self._pending: set[str] = set()
        self._observer: object | None = None
        self._fallback = PollingScanner()

    def start(self, folders: Sequence[WatchedFolder]) -> None:  # pragma: no cover - live path
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        pending = self._pending

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: object) -> None:
                path = getattr(event, "dest_path", "") or getattr(event, "src_path", "")
                if path and not getattr(event, "is_directory", False):
                    pending.add(os.path.normpath(path))

        observer = Observer()
        for folder in folders:
            observer.schedule(_Handler(), folder.path, recursive=folder.recursive)
        observer.start()
        self._observer = observer

    def scan(
        self, folders: Sequence[WatchedFolder], now: datetime
    ) -> list[FileObservation]:  # pragma: no cover - live path
        if self._observer is None:
            self.start(folders)
        drained, self._pending = sorted(self._pending), set()
        allowed = {
            observation.path: observation for observation in self._fallback.scan(folders, now)
        }
        return [allowed[path] for path in drained if path in allowed]

    def stop(self) -> None:  # pragma: no cover - live path
        observer = self._observer
        if observer is not None:
            observer.stop()  # type: ignore[attr-defined]
            observer.join()  # type: ignore[attr-defined]
            self._observer = None
