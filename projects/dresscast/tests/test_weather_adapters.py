"""FR-4: forecast acquisition, snapshot validation, DST days, Open-Meteo mapping."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from conftest import NOW, diurnal, make_forecast
from dresscast.adapters.weather import (
    LIVE_FLAG,
    FixtureWeatherProvider,
    OpenMeteoWeatherProvider,
    map_open_meteo,
)
from dresscast.engine.models import DayForecast, HourlyWeather, Location
from dresscast.errors import ForecastUnavailable

HOME = Location(name="home", lat=40.71, lon=-74.01, timezone="America/New_York")

SAMPLE_RESPONSE = {
    "latitude": 40.71,
    "longitude": -74.01,
    "timezone": "America/New_York",
    "hourly": {
        "time": [f"2026-04-14T{h:02d}:00" for h in range(24)] + ["2026-04-15T00:00"],
        "temperature_2m": [6.0 + h * 0.5 for h in range(24)] + [5.0],
        "relative_humidity_2m": [70] * 24 + [80],
        "precipitation_probability": [5] * 24 + [10],
        "precipitation": [0.0] * 24 + [0.2],
        "wind_speed_10m": [15.0] * 24 + [12.0],
        "uv_index": [0.0] * 8 + [3.0] * 8 + [0.0] * 8 + [0.0],
        "apparent_temperature": [4.0] * 24 + [3.0],
    },
}


def _write(tmp_path, forecast: DayForecast, name: str | None = None):
    payload = {
        "date": forecast.date,
        "location_name": forecast.location_name,
        "lat": forecast.lat,
        "lon": forecast.lon,
        "timezone": forecast.timezone,
        "hours": [h.model_dump() for h in forecast.hours],
    }
    path = tmp_path / (name or f"{forecast.date}.json")
    path.write_text(json.dumps(payload))
    return path


# --------------------------------------------------------------------------
# Snapshot validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hours", [23, 24, 25])
def test_fr4_a_local_day_may_have_23_24_or_25_rows(hours):
    forecast = make_forecast(temps=diurnal(4.0, 12.0, hours=hours), hours=hours)
    assert len(forecast.hours) == hours


def test_fr4_fewer_than_23_or_more_than_25_rows_is_rejected():
    row = HourlyWeather(
        seq=0,
        hour=0,
        temp_c=5.0,
        wind_kmh=5.0,
        humidity_pct=50.0,
        precip_prob=0.0,
        precip_mmh=0.0,
        uv_index=0.0,
    )
    with pytest.raises(ValueError, match="23-25"):
        DayForecast(
            id="x",
            date="2026-04-14",
            location_name="home",
            lat=0.0,
            lon=0.0,
            timezone="UTC",
            provider="fixture",
            fetched_at=NOW,
            hours=[row.model_copy(update={"seq": i, "hour": i}) for i in range(22)],
        )


def test_fr4_seq_must_be_contiguous_from_zero():
    rows = [
        HourlyWeather(
            seq=i if i < 5 else i + 1,
            hour=i % 24,
            temp_c=5.0,
            wind_kmh=5.0,
            humidity_pct=50.0,
            precip_prob=0.0,
            precip_mmh=0.0,
            uv_index=0.0,
        )
        for i in range(24)
    ]
    with pytest.raises(ValueError, match="contiguous"):
        DayForecast(
            id="x",
            date="2026-04-14",
            location_name="home",
            lat=0.0,
            lon=0.0,
            timezone="UTC",
            provider="fixture",
            fetched_at=NOW,
            hours=rows,
        )


def test_fr4_an_autumn_fold_repeats_a_wall_clock_hour():
    forecast = make_forecast(temps=diurnal(4.0, 12.0, hours=25), hours=25)
    hours = [h.hour for h in forecast.hours]
    assert len(hours) == 25
    assert len(set(hours)) == 24
    assert [h.seq for h in forecast.hours] == list(range(25))
    # both copies of the folded hour are in the wear window; the user lives both
    window = forecast.window_hours((7, 22))
    assert len(window) == 15
    assert [h.seq for h in window] == sorted(h.seq for h in window)


def test_fr4_physical_ranges_are_enforced():
    for bad in (
        {"temp_c": 90.0},
        {"wind_kmh": -1.0},
        {"humidity_pct": 120.0},
        {"precip_prob": 1.5},
        {"precip_mmh": -0.1},
        {"uv_index": 20.0},
    ):
        base = {
            "seq": 0,
            "hour": 0,
            "temp_c": 5.0,
            "wind_kmh": 5.0,
            "humidity_pct": 50.0,
            "precip_prob": 0.0,
            "precip_mmh": 0.0,
            "uv_index": 0.0,
        }
        base.update(bad)
        with pytest.raises(ValueError):
            HourlyWeather(**base)


def test_fr4_window_hours_reads_only_the_wear_window():
    forecast = make_forecast(temps=12.0)
    assert [h.hour for h in forecast.window_hours((7, 22))] == list(range(7, 22))
    assert forecast.window_hours((0, 1))[0].hour == 0


# --------------------------------------------------------------------------
# The fixture provider
# --------------------------------------------------------------------------


def test_fr4_fixture_provider_serves_a_committed_day(tmp_path):
    _write(tmp_path, make_forecast(temps=diurnal(5.0, 18.0)))
    provider = FixtureWeatherProvider(tmp_path)
    forecast = provider.get_day(HOME, "2026-04-14", now=NOW)
    assert forecast.provider == "fixture"
    assert len(forecast.hours) == 24
    assert forecast.timezone == "America/New_York"
    assert forecast.raw is None


def test_fr4_fixture_provider_indexes_scenario_filenames_by_date(tmp_path):
    _write(tmp_path, make_forecast(temps=12.0), name="01_winter_calm.json")
    provider = FixtureWeatherProvider(tmp_path)
    assert provider.get_day(HOME, "2026-04-14", now=NOW).date == "2026-04-14"


def test_fr4_fixture_provider_is_deterministic(tmp_path):
    _write(tmp_path, make_forecast(temps=diurnal(5.0, 18.0)))
    provider = FixtureWeatherProvider(tmp_path)
    first = provider.get_day(HOME, "2026-04-14", now=NOW)
    second = provider.get_day(HOME, "2026-04-14", now=NOW)
    assert first.model_dump() == second.model_dump()


def test_fr4_missing_fixture_raises_a_structured_error(tmp_path):
    provider = FixtureWeatherProvider(tmp_path)
    with pytest.raises(ForecastUnavailable) as excinfo:
        provider.get_day(HOME, "2026-04-14", now=NOW)
    assert excinfo.value.code == "forecast_unavailable"


def test_fr4_malformed_fixture_raises_a_structured_error(tmp_path):
    (tmp_path / "2026-04-14.json").write_text(json.dumps({"date": "2026-04-14", "hours": []}))
    provider = FixtureWeatherProvider(tmp_path)
    with pytest.raises(ForecastUnavailable):
        provider.get_day(HOME, "2026-04-14", now=NOW)


def test_fr4_fixture_provider_can_load_a_file_directly(tmp_path):
    path = _write(tmp_path, make_forecast(temps=12.0), name="scenario.json")
    forecast = FixtureWeatherProvider(tmp_path).load(path, snapshot_id="s1")
    assert forecast.id == "s1"
    assert len(forecast.hours) == 24


# --------------------------------------------------------------------------
# The live provider — mapping only, never a network call
# --------------------------------------------------------------------------


def test_fr4_open_meteo_mapping_against_a_committed_sample_response():
    mapped = map_open_meteo(SAMPLE_RESPONSE, "2026-04-14")
    assert len(mapped["hours"]) == 24
    assert [h["seq"] for h in mapped["hours"]] == list(range(24))
    assert mapped["hours"][0]["hour"] == 0
    assert mapped["hours"][0]["temp_c"] == pytest.approx(6.0)
    assert mapped["hours"][0]["precip_prob"] == pytest.approx(0.05)
    assert mapped["hours"][0]["wind_kmh"] == pytest.approx(15.0)
    assert mapped["timezone"] == "America/New_York"
    forecast = DayForecast(
        id="s", provider="open_meteo", fetched_at=NOW, raw=SAMPLE_RESPONSE, **mapped
    )
    assert len(forecast.hours) == 24


def test_fr4_open_meteo_probability_is_converted_from_percent():
    mapped = map_open_meteo(SAMPLE_RESPONSE, "2026-04-14")
    assert all(0.0 <= h["precip_prob"] <= 1.0 for h in mapped["hours"])


def test_fr4_open_meteo_ignores_the_providers_apparent_temperature():
    """The engine always computes its own feels-like (FR-5)."""
    mapped = map_open_meteo(SAMPLE_RESPONSE, "2026-04-14")
    assert all("apparent_temperature" not in h for h in mapped["hours"])


def test_fr4_open_meteo_rejects_a_response_without_the_target_date():
    with pytest.raises(ForecastUnavailable):
        map_open_meteo(SAMPLE_RESPONSE, "2026-04-20")
    with pytest.raises(ForecastUnavailable):
        map_open_meteo({"hourly": {}}, "2026-04-14")


def test_fr4_open_meteo_tolerates_missing_optional_columns():
    payload = {
        "latitude": 1.0,
        "longitude": 2.0,
        "timezone": "UTC",
        "hourly": {
            "time": [f"2026-04-14T{h:02d}:00" for h in range(24)],
            "temperature_2m": [10.0] * 24,
        },
    }
    mapped = map_open_meteo(payload, "2026-04-14")
    assert mapped["hours"][0]["uv_index"] == 0.0
    assert mapped["hours"][0]["humidity_pct"] == 50.0


def test_fr4_live_provider_is_disabled_without_the_opt_in(monkeypatch):
    monkeypatch.delenv(LIVE_FLAG, raising=False)
    provider = OpenMeteoWeatherProvider()
    assert provider.enabled() is False
    with pytest.raises(ForecastUnavailable) as excinfo:
        provider.get_day(HOME, "2026-04-14", now=NOW)
    assert LIVE_FLAG in str(excinfo.value)


def test_fr4_live_provider_builds_the_documented_request_url(monkeypatch):
    monkeypatch.setenv(LIVE_FLAG, "1")
    url = OpenMeteoWeatherProvider().request_url(HOME, "2026-04-14")
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    for fragment in (
        "latitude=40.71",
        "longitude=-74.01",
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation_probability",
        "wind_speed_10m",
        "uv_index",
        "start_date=2026-04-14",
    ):
        assert fragment in url


def test_fr4_live_provider_maps_without_touching_the_network(monkeypatch):
    monkeypatch.setenv(LIVE_FLAG, "1")
    provider = OpenMeteoWeatherProvider()
    monkeypatch.setattr(provider, "fetch", lambda location, date: SAMPLE_RESPONSE)
    forecast = provider.get_day(HOME, "2026-04-14", now=NOW)
    assert forecast.provider == "open_meteo"
    assert len(forecast.hours) == 24
    assert forecast.raw == SAMPLE_RESPONSE
    assert forecast.fetched_at == NOW


def test_fr4_snapshots_are_never_mutated(tmp_path):
    _write(tmp_path, make_forecast(temps=12.0))
    forecast = FixtureWeatherProvider(tmp_path).get_day(HOME, "2026-04-14", now=NOW)
    with pytest.raises(ValueError):
        forecast.hours[0].temp_c = 99.0
    with pytest.raises(ValueError):
        forecast.date = "2026-04-15"


def test_fr4_fetched_at_is_supplied_by_the_caller(tmp_path):
    _write(tmp_path, make_forecast(temps=12.0))
    stamp = datetime(2030, 1, 2, 3, 4, 5)
    forecast = FixtureWeatherProvider(tmp_path).get_day(HOME, "2026-04-14", now=stamp)
    assert forecast.fetched_at == stamp
