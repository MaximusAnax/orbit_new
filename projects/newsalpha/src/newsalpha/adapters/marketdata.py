"""The `MarketData` capability: daily OHLCV bars (FR-9)."""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..models import PriceBar


@runtime_checkable
class MarketData(Protocol):
    """Yields daily bars for one asset over `[start, end]`, ascending by date."""

    def daily_bars(self, asset_id: str, start: date, end: date) -> list[PriceBar]: ...


class MarketDataError(RuntimeError):
    """A price series could not be read. Never degrade to silently stale analysis."""


def is_weekday(day: date) -> bool:
    """Equities trade weekdays; there is no holiday table (documented, SCOPE D-11)."""
    return day.weekday() < 5


__all__ = ["MarketData", "MarketDataError", "is_weekday"]
