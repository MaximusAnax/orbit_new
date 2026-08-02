"""Offline `MarketData`: committed per-asset CSVs. The default everywhere.

Layout: one file per asset under the given directory, named after the asset id
with `:` replaced by `_` (`cx_BTC.csv`, `idx_US.csv`), with the header
`date,open,high,low,close,volume`.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..models import BarSource, PriceBar
from .marketdata import MarketDataError

HEADER = ("date", "open", "high", "low", "close", "volume")


def filename_for(asset_id: str) -> str:
    return asset_id.replace(":", "_") + ".csv"


class FixtureMarketData:
    """Reads committed CSV series; results are cached per asset so runs stay cheap."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, list[PriceBar]] = {}

    def available_assets(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(path.stem.replace("_", ":", 1) for path in self.directory.glob("*.csv"))

    def daily_bars(self, asset_id: str, start: date, end: date) -> list[PriceBar]:
        series = self._load(asset_id)
        lo, hi = start.isoformat(), end.isoformat()
        return [bar for bar in series if lo <= bar.date <= hi]

    def _load(self, asset_id: str) -> list[PriceBar]:
        cached = self._cache.get(asset_id)
        if cached is not None:
            return cached
        path = self.directory / filename_for(asset_id)
        if not path.exists():
            raise MarketDataError(f"no committed price series for {asset_id}: {path}")
        bars: list[PriceBar] = []
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or tuple(reader.fieldnames) != HEADER:
                raise MarketDataError(
                    f"{path}: expected header {','.join(HEADER)}, got {reader.fieldnames}"
                )
            for lineno, row in enumerate(reader, start=2):
                try:
                    bars.append(
                        PriceBar(
                            asset_id=asset_id,
                            date=row["date"],
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            source=BarSource.fixture,
                        )
                    )
                except ValueError as exc:
                    raise MarketDataError(f"{path}:{lineno} is not a valid bar: {exc}") from exc
        bars.sort(key=lambda bar: bar.date)
        self._cache[asset_id] = bars
        return bars


class StaticMarketData:
    """In-memory `MarketData` over a bar list -- for tests."""

    def __init__(self, bars: list[PriceBar]) -> None:
        self._by_asset: dict[str, list[PriceBar]] = {}
        for bar in sorted(bars, key=lambda b: (b.asset_id, b.date)):
            self._by_asset.setdefault(bar.asset_id, []).append(bar)

    def daily_bars(self, asset_id: str, start: date, end: date) -> list[PriceBar]:
        lo, hi = start.isoformat(), end.isoformat()
        return [bar for bar in self._by_asset.get(asset_id, []) if lo <= bar.date <= hi]


__all__ = ["FixtureMarketData", "StaticMarketData", "filename_for"]
