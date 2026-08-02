"""The ``Clock`` capability.

The engine never reads a clock (FR-17): the current date is always a
parameter.  Edges (API, CLI, simulations) obtain a default date from a Clock.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Supplies today's local calendar date to the edges of the system."""

    def today(self) -> dt.date:  # pragma: no cover - protocol definition
        ...


class SystemClock:
    """Live implementation: the host's local date. Used at API/CLI edges only."""

    def today(self) -> dt.date:
        return dt.date.today()


class FixedClock:
    """Offline implementation used by tests and evals; simulations advance it."""

    def __init__(self, current: dt.date) -> None:
        self._current = current

    def today(self) -> dt.date:
        return self._current

    def set(self, value: dt.date) -> None:
        self._current = value

    def advance(self, days: int = 1) -> dt.date:
        self._current += dt.timedelta(days=days)
        return self._current
