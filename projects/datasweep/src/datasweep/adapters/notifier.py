"""The Notifier port (SCOPE.md FR-13 / D15).

The offline default appends one line to ``<db_dir>/notify.log``; the live
adapter shells out to the platform's notification tool and swallows failures —
a background cleaner must never fail a run because a toast did not appear.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..engine.models import RunSummary

logger = logging.getLogger("datasweep.notify")


@runtime_checkable
class Notifier(Protocol):
    def notify(self, summary: RunSummary) -> None:  # pragma: no cover - protocol
        ...


class NullNotifier:
    """Used by evals and unit tests that do not care about notifications."""

    def notify(self, summary: RunSummary) -> None:
        return None


class LogNotifier:
    """Offline default: one line per completed run, appended atomically."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def notify(self, summary: RunSummary) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(summary.one_line() + "\n")


class DesktopNotifier:
    """Live adapter (extra ``notify``): ``notify-send`` / ``osascript``.

    Availability is decided from the platform and ``PATH`` at construction
    time; ``notify`` never raises — failures are logged and dropped.
    """

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.command: list[str] | None = None
        if sys.platform == "darwin" and shutil.which("osascript"):
            self.command = ["osascript", "-e"]
        elif shutil.which("notify-send"):
            self.command = ["notify-send"]

    @property
    def available(self) -> bool:
        return self.command is not None

    def notify(self, summary: RunSummary) -> None:  # pragma: no cover - live path
        if self.command is None:
            logger.debug("no desktop notifier available; dropping %s", summary.run_id)
            return
        line = summary.one_line()
        if self.command[0] == "osascript":
            script = f'display notification {line!r} with title "datasweep"'
            argv = [*self.command, script]
        else:
            argv = [*self.command, "datasweep", line]
        try:
            subprocess.run(argv, check=False, timeout=self.timeout_seconds, capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("desktop notification failed: %s", exc)
