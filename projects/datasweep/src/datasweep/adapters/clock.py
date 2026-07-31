"""The Clock port (SCOPE.md §Architecture).

The engine never sees this: services stamp times and pass them into the engine
as data, which is what keeps the engine pure and the evals hermetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:  # pragma: no cover - protocol
        ...


class SystemClock:
    """Production clock: UTC wall time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests and evals.

    Optionally advances by ``step`` seconds on every read so that a caller
    needing two distinct timestamps (started_at / finished_at) gets them
    without touching the wall clock.
    """

    def __init__(self, start: datetime | str, step_seconds: float = 0.0) -> None:
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        self._current = start
        self._step = step_seconds

    def now(self) -> datetime:
        current = self._current
        if self._step:
            from datetime import timedelta

            self._current = current + timedelta(seconds=self._step)
        return current

    def set(self, moment: datetime) -> None:
        self._current = moment
