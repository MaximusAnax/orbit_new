"""Live `MarketData`: Stooq for equities, CoinGecko for crypto (FR-9).

Activates only when `NEWSALPHA_LIVE=1`.  Uses `urllib.request` from the stdlib --
no heavy dependency -- and is never imported on the test/eval path (FR-14
hermeticity, asserted by `test_hermetic_no_live_adapters`).

Environment:
    NEWSALPHA_LIVE=1            required to activate
    NEWSALPHA_COINGECKO_KEY     optional; raises CoinGecko rate limits
    NEWSALPHA_HTTP_TIMEOUT      optional request timeout in seconds (default 20)

`idx:CX` is synthesized as the equal-weighted mean of the daily log returns of
the basket in `data/benchmarks.json`, so no crypto universe asset is its own
benchmark (SCOPE D-5).  `idx:US` follows the `live_proxy` symbol in the same
file through the Stooq route.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import urllib.error
import urllib.request
from datetime import UTC, date, datetime

from ..models import BarSource, PriceBar
from .marketdata import MarketDataError

LIVE_ENV = "NEWSALPHA_LIVE"
COINGECKO_KEY_ENV = "NEWSALPHA_COINGECKO_KEY"
TIMEOUT_ENV = "NEWSALPHA_HTTP_TIMEOUT"
DEFAULT_TIMEOUT = 20.0

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&d1={start}&d2={end}&i=d"
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/{coin}/market_chart/range"
    "?vs_currency=usd&from={start}&to={end}"
)

#: Minimal symbol map for the live crypto route. Extending it is a data edit in
#: spirit; unknown ids raise instead of guessing a CoinGecko slug.
COINGECKO_IDS: dict[str, str] = {
    "cx:BTC": "bitcoin",
    "cx:ETH": "ethereum",
    "cx:SOL": "solana",
    "cx:XRP": "ripple",
    "cx:ADA": "cardano",
    "cx:AVAX": "avalanche-2",
    "cx:LINK": "chainlink",
    "cx:DOT": "polkadot",
    "cx:DOGE": "dogecoin",
    "cx:LTC": "litecoin",
    "cx:MATIC": "matic-network",
    "cx:NEAR": "near",
    "cx:APT": "aptos",
    "cx:ARB": "arbitrum",
    "cx:OP": "optimism",
    "cx:UNI": "uniswap",
    "cx:ATOM": "cosmos",
    "cx:XLM": "stellar",
}


def is_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else dict(os.environ)
    return env.get(LIVE_ENV, "").strip() == "1"


class LiveMarketData:
    """Routes by asset kind; raises a clear error rather than returning stale data."""

    def __init__(
        self,
        benchmarks: dict[str, list[str]],
        *,
        environ: dict[str, str] | None = None,
        opener=None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        if not is_enabled(env):
            raise MarketDataError(
                f"{LIVE_ENV}=1 is required before the live market-data adapter will run"
            )
        self.benchmarks = benchmarks
        self.api_key = env.get(COINGECKO_KEY_ENV) or None
        try:
            self.timeout = float(env.get(TIMEOUT_ENV, DEFAULT_TIMEOUT))
        except ValueError as exc:
            raise MarketDataError(f"{TIMEOUT_ENV} must be a number: {exc}") from exc
        self._open = opener or self._urlopen

    # -- transport --------------------------------------------------------- #

    def _urlopen(self, url: str, headers: dict[str, str]) -> str:  # pragma: no cover - network
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise MarketDataError(f"live fetch failed for {url}: {exc}") from exc

    # -- routing ----------------------------------------------------------- #

    def daily_bars(self, asset_id: str, start: date, end: date) -> list[PriceBar]:
        if asset_id == "idx:CX":
            return self._synthesize_crypto_benchmark(start, end)
        if asset_id.startswith("cx:"):
            return self._coingecko(asset_id, start, end)
        if asset_id.startswith("eq:"):
            return self._stooq(asset_id, asset_id.split(":", 1)[1].lower() + ".us", start, end)
        if asset_id == "idx:US":
            return self._stooq(asset_id, self._us_proxy_symbol(), start, end)
        raise MarketDataError(f"no live route for asset {asset_id!r}")

    def _us_proxy_symbol(self) -> str:
        for member in self.benchmarks.get("idx:US", []):
            if member.startswith("live_proxy:"):
                return member.split(":", 1)[1].strip().lower() + ".us"
        raise MarketDataError("benchmarks.json does not name a live_proxy symbol for idx:US")

    # -- equities ---------------------------------------------------------- #

    def _stooq(self, asset_id: str, symbol: str, start: date, end: date) -> list[PriceBar]:
        url = STOOQ_URL.format(
            symbol=symbol,
            start=start.strftime("%Y%m%d"),
            end=end.strftime("%Y%m%d"),
        )
        payload = self._open(url, {"User-Agent": "newsalpha/0.1"})
        reader = csv.DictReader(io.StringIO(payload))
        if reader.fieldnames is None or "Close" not in reader.fieldnames:
            raise MarketDataError(f"unexpected Stooq payload for {asset_id}: {payload[:120]!r}")
        bars: list[PriceBar] = []
        for row in reader:
            try:
                bars.append(
                    PriceBar(
                        asset_id=asset_id,
                        date=row["Date"],
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0.0),
                        source=BarSource.live,
                    )
                )
            except (KeyError, ValueError) as exc:
                raise MarketDataError(
                    f"malformed Stooq row for {asset_id}: {row!r} ({exc})"
                ) from exc
        return sorted(bars, key=lambda bar: bar.date)

    # -- crypto ------------------------------------------------------------ #

    def _coingecko(self, asset_id: str, start: date, end: date) -> list[PriceBar]:
        coin = COINGECKO_IDS.get(asset_id)
        if coin is None:
            raise MarketDataError(f"no CoinGecko id mapped for {asset_id}; add it to COINGECKO_IDS")
        url = COINGECKO_URL.format(
            coin=coin,
            start=int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp()),
            end=int(datetime.combine(end, datetime.max.time(), tzinfo=UTC).timestamp()),
        )
        headers = {"User-Agent": "newsalpha/0.1", "Accept": "application/json"}
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key
        try:
            payload = json.loads(self._open(url, headers))
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"CoinGecko returned invalid JSON for {asset_id}: {exc}") from exc
        prices = payload.get("prices")
        if not prices:
            raise MarketDataError(f"CoinGecko returned no prices for {asset_id}")

        by_day: dict[str, list[float]] = {}
        for milliseconds, price in prices:
            day = datetime.fromtimestamp(milliseconds / 1000, tz=UTC).date().isoformat()
            by_day.setdefault(day, []).append(float(price))
        volumes = {
            datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat(): float(value)
            for ms, value in payload.get("total_volumes", [])
        }
        bars: list[PriceBar] = []
        for day, series in sorted(by_day.items()):
            bars.append(
                PriceBar(
                    asset_id=asset_id,
                    date=day,
                    open=series[0],
                    high=max(series),
                    low=min(series),
                    close=series[-1],
                    volume=volumes.get(day, 0.0),
                    source=BarSource.live,
                )
            )
        return bars

    def _synthesize_crypto_benchmark(self, start: date, end: date) -> list[PriceBar]:
        """Equal-weighted mean of member daily log returns, rebased to 100 (FR-9)."""
        members = [m for m in self.benchmarks.get("idx:CX", []) if not m.startswith("live_proxy:")]
        if not members:
            raise MarketDataError("benchmarks.json defines no idx:CX basket")
        series = {member: self._coingecko(member, start, end) for member in members}
        common = sorted(set.intersection(*({bar.date for bar in bars} for bars in series.values())))
        if len(common) < 2:
            raise MarketDataError("idx:CX basket has fewer than two shared dates")

        closes = {member: {bar.date: bar.close for bar in bars} for member, bars in series.items()}
        level = 100.0
        bars: list[PriceBar] = []
        for index, day in enumerate(common):
            open_level = level
            if index > 0:
                previous = common[index - 1]
                returns = [
                    math.log(closes[m][day] / closes[m][previous])
                    for m in members
                    if closes[m][previous] > 0
                ]
                level = level * math.exp(sum(returns) / len(returns))
            bars.append(
                PriceBar(
                    asset_id="idx:CX",
                    date=day,
                    open=open_level,
                    high=max(open_level, level),
                    low=min(open_level, level),
                    close=level,
                    volume=0.0,
                    source=BarSource.live,
                )
            )
        return bars


__all__ = ["COINGECKO_IDS", "LIVE_ENV", "LiveMarketData", "is_enabled"]
