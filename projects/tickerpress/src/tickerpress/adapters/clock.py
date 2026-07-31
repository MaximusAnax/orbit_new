"""Clock port (SCOPE D12): time is an input everywhere.

Engine functions take ``now`` explicitly; services get theirs from a
:class:`Clock`. Tests and evals run under :class:`FixedClock`, which is what
makes ``ingest`` + ``digest`` byte-reproducible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ..engine.models import ensure_utc, parse_iso_utc

__all__ = ["Clock", "FixedClock", "SystemClock"]


@runtime_checkable
class Clock(Protocol):
    """Supplies the current instant as an aware UTC datetime."""

    def now(self) -> datetime: ...


class SystemClock:
    """Real time, truncated to whole seconds (see DATA_MODEL note on storage)."""

    def now(self) -> datetime:
        return ensure_utc(datetime.now(UTC))


class FixedClock:
    """A clock frozen at one instant; accepts an ISO-8601 string or datetime."""

    def __init__(self, moment: datetime | str) -> None:
        self._moment = parse_iso_utc(moment) if isinstance(moment, str) else ensure_utc(moment)

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime | str) -> None:
        """Move the clock — used by scenario tests that need two instants."""

        self._moment = parse_iso_utc(moment) if isinstance(moment, str) else ensure_utc(moment)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FixedClock({self._moment.isoformat()})"
