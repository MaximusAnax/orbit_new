"""FR-12: wear logging at the service layer (plus FR-11's HC-8 consequence).

Repository transaction mechanics are covered in ``test_store.py``; these tests
exercise the service flows the CLI/API call: logging a stored recommendation
by rank, logging arbitrary items by name, the same-day undo window, and the
effect a logged wear has on the next day's recommendation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import diurnal, make_forecast, small_wardrobe
from dresscast.errors import InvalidParams
from dresscast.services import Config, DresscastService
from dresscast.store.memory import InMemoryRepository

NOW = datetime(2026, 4, 14, 6, 30, tzinfo=UTC)
DATE = "2026-04-14"
NEXT = "2026-04-15"


def _write_weather(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for date in (DATE, NEXT):
        forecast = make_forecast(date=date, temps=diurnal(5.0, 18.0), wind=10.0, humidity=55.0)
        payload = {
            "date": date,
            "location_name": "home",
            "lat": 40.71,
            "lon": -74.01,
            "timezone": "America/New_York",
            "hours": [h.model_dump() for h in forecast.hours],
        }
        (directory / f"{date}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def service(tmp_path: Path) -> DresscastService:
    weather = tmp_path / "weather"
    _write_weather(weather)
    repo = InMemoryRepository()
    for garment in small_wardrobe():
        repo.add_garment(garment)
    return DresscastService(repo, Config(data_dir=tmp_path, fixture_dir=weather))


def test_fr12_wear_recommendation_logs_the_ranked_outfit(service: DresscastService) -> None:
    rec = service.recommend(date=DATE, occasion="work", now=NOW)
    outfit = next(o for o in rec.outfits if o.rank == 2)

    log = service.wear_recommendation(rec.id, rank=2, now=NOW)
    assert log.source == "recommendation"
    assert log.outfit_id == outfit.id
    assert log.date == DATE
    assert {i.garment_id for i in log.items} == {i.garment_id for i in outfit.items}
    for item in log.items:
        assert service.repo.get_garment(item.garment_id).wears_since_wash >= 1


def test_fr12_wear_latest_resolves_the_most_recent_run(service: DresscastService) -> None:
    service.recommend(date=DATE, occasion="casual", now=NOW - timedelta(minutes=5))
    newest = service.recommend(date=DATE, occasion="work", now=NOW)
    log = service.wear_recommendation("latest", rank=1, now=NOW)
    assert log.outfit_id == newest.outfits[0].id


def test_fr12_wear_rank_out_of_range_is_invalid_params(service: DresscastService) -> None:
    rec = service.recommend(date=DATE, occasion="work", now=NOW)
    with pytest.raises(InvalidParams):
        service.wear_recommendation(rec.id, rank=9, now=NOW)


def test_fr12_wear_items_by_name_logs_a_manual_entry(service: DresscastService) -> None:
    log = service.wear_items(["white-tee", "dark-jeans"], date=DATE, now=NOW)
    assert log.source == "manual"
    assert log.outfit_id is None
    names = {service.repo.get_garment(i.garment_id).name for i in log.items}
    assert names == {"white-tee", "dark-jeans"}


def test_fr12_multiple_logs_per_date_are_allowed(service: DresscastService) -> None:
    service.wear_items(["white-tee"], date=DATE, now=NOW)  # gym
    service.wear_items(["white-oxford-shirt"], date=DATE, now=NOW)  # work
    assert len(service.history(until=DATE, days=1)) == 2


def test_fr12_same_day_undo_reverses_counters_and_deletes_the_log(
    service: DresscastService,
) -> None:
    before = service.resolve_garment("dark-jeans").wears_since_wash
    log = service.wear_items(["dark-jeans"], date=DATE, now=NOW)
    assert service.resolve_garment("dark-jeans").wears_since_wash == before + 1

    service.undo_wear(log.id, today=NOW.date().isoformat(), now=NOW)
    assert service.resolve_garment("dark-jeans").wears_since_wash == before
    assert service.history(until=DATE, days=1) == []


def test_fr12_undo_after_the_calendar_day_is_refused(service: DresscastService) -> None:
    log = service.wear_items(["dark-jeans"], date=DATE, now=NOW)
    with pytest.raises(InvalidParams):
        service.undo_wear(log.id, today=NEXT, now=NOW + timedelta(days=1))


def test_fr12_wear_log_snapshots_the_layer_role_at_log_time(
    service: DresscastService,
) -> None:
    """Re-tagging a flannel from base to mid never rewrites what was worn."""
    log = service.wear_items(["red-flannel-shirt"], date=DATE, now=NOW)
    service.edit_garment("red-flannel-shirt", now=NOW)  # no-op keeps the garment valid
    flannel = service.resolve_garment("red-flannel-shirt")
    service.repo.update_garment(flannel.model_copy(update={"layer_role": "mid"}))

    stored = service.history(until=DATE, days=1)[0]
    assert stored.id == log.id
    (item,) = stored.items
    assert item.layer_role == "base"


def test_fr11_hc8_yesterdays_outfit_is_not_repeated_today(service: DresscastService) -> None:
    rec = service.recommend(date=DATE, occasion="work", now=NOW)
    service.wear_recommendation(rec.id, rank=1, date=DATE, now=NOW)
    worn = {
        i.garment_id for i in rec.outfits[0].items if not i.slot.startswith("accessory")
    }

    tomorrow = service.recommend(date=NEXT, occasion="work", now=NOW + timedelta(days=1))
    for outfit in tomorrow.outfits:
        core = {i.garment_id for i in outfit.items if not i.slot.startswith("accessory")}
        assert core != worn
