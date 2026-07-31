"""Weather providers (SCOPE.md FR-4, §Architecture-Adapters).

``FixtureWeatherProvider`` is the offline default every test and eval uses:
committed JSON scenario files keyed by ISO date, no network, deterministic.
``OpenMeteoWeatherProvider`` is the live adapter; it activates only when
``DRESSCAST_LIVE_WEATHER=1`` and a location is configured, and its field
mapping is a pure function that is unit-tested against a committed sample
response with no network involved.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from dresscast.engine.models import DayForecast, HourlyWeather, Location
from dresscast.errors import ForecastUnavailable

#: Environment flag that arms the live provider (D14: explicit opt-in).
LIVE_FLAG = "DRESSCAST_LIVE_WEATHER"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HOURLY = (
    "temperature_2m,relative_humidity_2m,precipitation_probability,"
    "precipitation,wind_speed_10m,uv_index,apparent_temperature"
)


@runtime_checkable
class WeatherProvider(Protocol):
    """A source of one local day's hourly forecast."""

    name: str

    def get_day(self, location: Location, date: str, *, now: datetime) -> DayForecast:
        """Return 23-25 :class:`HourlyWeather` rows, or raise ForecastUnavailable."""
        ...


def _build(
    payload: dict[str, Any],
    *,
    snapshot_id: str,
    date: str,
    location: Location,
    provider: str,
    now: datetime,
    raw: dict[str, Any] | None,
) -> DayForecast:
    try:
        return DayForecast(
            id=snapshot_id,
            date=date,
            location_name=payload.get("location_name", location.name),
            lat=payload.get("lat", location.lat),
            lon=payload.get("lon", location.lon),
            timezone=payload.get("timezone", location.timezone),
            provider=provider,  # type: ignore[arg-type]
            fetched_at=now,
            raw=raw,
            hours=[HourlyWeather(**row) for row in payload["hours"]],
        )
    except Exception as exc:
        raise ForecastUnavailable(
            f"malformed forecast for {date}: {exc}", date=date, provider=provider
        ) from exc


class FixtureWeatherProvider:
    """Offline, deterministic provider reading committed JSON scenario files.

    Files are named ``<date>.json`` (DATA_MODEL.md §3.1's shape).  A directory
    may also carry differently named scenario files; the provider indexes every
    ``*.json`` in the directory by its ``date`` field, so ``01_winter_calm.json``
    resolves as readily as ``2026-04-14.json``.
    """

    name = "fixture"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._index: dict[str, Path] | None = None

    def _scan(self) -> dict[str, Path]:
        if self._index is not None:
            return self._index
        index: dict[str, Path] = {}
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                key = data.get("date")
                if isinstance(key, str) and key not in index:
                    index[key] = path
        self._index = index
        return index

    def load(
        self, path: str | Path, *, snapshot_id: str = "", now: datetime | None = None
    ) -> DayForecast:
        """Read one fixture file directly, bypassing the date index."""
        p = Path(path)
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ForecastUnavailable(f"cannot read {p}", path=str(p)) from exc
        location = Location(
            name=payload.get("location_name", "home"),
            lat=payload.get("lat", 0.0),
            lon=payload.get("lon", 0.0),
            timezone=payload.get("timezone", "UTC"),
        )
        stamp = now or datetime.fromisoformat(f"{payload['date']}T00:00:00+00:00")
        return _build(
            payload,
            snapshot_id=snapshot_id,
            date=payload["date"],
            location=location,
            provider="fixture",
            now=stamp,
            raw=None,
        )

    def get_day(self, location: Location, date: str, *, now: datetime) -> DayForecast:
        path = self.directory / f"{date}.json"
        if not path.exists():
            path_from_index = self._scan().get(date)
            if path_from_index is None:
                raise ForecastUnavailable(
                    f"no fixture forecast for {date} in {self.directory}",
                    date=date,
                    directory=str(self.directory),
                )
            path = path_from_index
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ForecastUnavailable(
                f"cannot read fixture forecast {path}: {exc}", date=date
            ) from exc
        return _build(
            payload,
            snapshot_id="",
            date=date,
            location=location,
            provider="fixture",
            now=now,
            raw=None,
        )


def map_open_meteo(
    payload: dict[str, Any], date: str, *, timezone_fallback: str = "UTC"
) -> dict[str, Any]:
    """Map an Open-Meteo ``/v1/forecast`` response onto DATA_MODEL.md §3.1.

    Pure: no network, no clock.  Rows are filtered to ``date`` in the response's
    own timezone and re-sequenced from 0, so a DST day yields 23 or 25 rows and
    FR-4's relaxed invariant holds.  The provider's ``apparent_temperature`` is
    deliberately *not* used — the engine always computes its own feels-like
    (FR-5) so results stay deterministic and testable.
    """
    hourly = payload.get("hourly") or {}
    times: list[str] = list(hourly.get("time") or [])
    if not times:
        raise ForecastUnavailable("Open-Meteo response carries no hourly block", date=date)

    def column(key: str) -> list[Any]:
        values = hourly.get(key)
        if values is None:
            return [None] * len(times)
        return list(values)

    temps = column("temperature_2m")
    humidity = column("relative_humidity_2m")
    pop = column("precipitation_probability")
    precip = column("precipitation")
    wind = column("wind_speed_10m")
    uv = column("uv_index")

    rows: list[dict[str, Any]] = []
    for i, stamp in enumerate(times):
        if not stamp.startswith(date):
            continue
        rows.append(
            {
                "seq": len(rows),
                "hour": int(stamp[11:13]),
                "temp_c": float(temps[i] if temps[i] is not None else 0.0),
                "wind_kmh": float(wind[i] if wind[i] is not None else 0.0),
                "humidity_pct": float(humidity[i] if humidity[i] is not None else 50.0),
                # Open-Meteo reports probability in percent; the model wants 0-1.
                "precip_prob": float(pop[i] if pop[i] is not None else 0.0) / 100.0,
                "precip_mmh": float(precip[i] if precip[i] is not None else 0.0),
                "uv_index": float(uv[i] if uv[i] is not None else 0.0),
            }
        )
    if not rows:
        raise ForecastUnavailable(f"Open-Meteo response has no rows for {date}", date=date)
    return {
        "date": date,
        "location_name": "home",
        "lat": float(payload.get("latitude", 0.0)),
        "lon": float(payload.get("longitude", 0.0)),
        "timezone": payload.get("timezone", timezone_fallback),
        "hours": rows,
    }


class OpenMeteoWeatherProvider:
    """Live provider over Open-Meteo's free, keyless forecast API (D14).

    Activation is an explicit opt-in: ``DRESSCAST_LIVE_WEATHER=1`` plus a
    configured location.  Nothing about this class is imported or executed on
    the offline path.
    """

    name = "open_meteo"

    def __init__(self, *, timeout: float = 10.0, url: str = OPEN_METEO_URL) -> None:
        self.timeout = timeout
        self.url = url

    @staticmethod
    def enabled() -> bool:
        return os.environ.get(LIVE_FLAG, "") == "1"

    def request_url(self, location: Location, date: str) -> str:
        query = urllib.parse.urlencode(
            {
                "latitude": location.lat,
                "longitude": location.lon,
                "hourly": OPEN_METEO_HOURLY,
                "timezone": location.timezone,
                "start_date": date,
                "end_date": date,
            }
        )
        return f"{self.url}?{query}"

    def fetch(self, location: Location, date: str) -> dict[str, Any]:
        """Perform the HTTP GET.  Separated so the mapping can be tested offline."""
        url = self.request_url(location, date)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ForecastUnavailable(
                f"Open-Meteo request failed: {exc}", date=date, url=url
            ) from exc

    def get_day(self, location: Location, date: str, *, now: datetime) -> DayForecast:
        if not self.enabled():
            raise ForecastUnavailable(
                f"the live weather provider needs {LIVE_FLAG}=1",
                flag=LIVE_FLAG,
            )
        payload = self.fetch(location, date)
        mapped = map_open_meteo(payload, date, timezone_fallback=location.timezone)
        return _build(
            mapped,
            snapshot_id="",
            date=date,
            location=location,
            provider="open_meteo",
            now=now,
            raw=payload,
        )
