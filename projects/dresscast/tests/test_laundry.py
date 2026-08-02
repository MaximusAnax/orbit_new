"""FR-3: the laundry lifecycle at the service layer (US-7).

Repository-level transition mechanics live in ``test_store.py``; these tests
exercise the service flows the CLI/API call: wear-to-threshold auto-dirty,
``laundry --all`` resets, the state machine's refusals, and the guarantee that
dirty or retired garments never reach a recommendation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import diurnal, make_forecast, small_wardrobe
from dresscast.errors import InfeasibleWardrobe, InvalidParams, InvalidTransition
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


def test_fr3_us7_second_wear_flips_dirty_and_recommendation_excludes_it(
    service: DresscastService,
) -> None:
    """US-7: a 2-wear shirt worn twice is dirty and absent from the next run."""
    oxford = service.resolve_garment("white-oxford-shirt")
    assert oxford.wears_before_laundry == 2
    service.wear_items(["white-oxford-shirt"], date="2026-04-12", now=NOW - timedelta(days=2))
    assert service.resolve_garment("white-oxford-shirt").status == "clean"
    service.wear_items(["white-oxford-shirt"], date="2026-04-13", now=NOW - timedelta(days=1))
    worn = service.resolve_garment("white-oxford-shirt")
    assert worn.status == "dirty"
    assert worn.wears_since_wash == 2

    rec = service.recommend(date=DATE, occasion="work", now=NOW)
    recommended = {item.garment_id for outfit in rec.outfits for item in outfit.items}
    assert oxford.id not in recommended


def test_fr3_us7_launder_all_resets_every_dirty_garment(service: DresscastService) -> None:
    service.wear_items(["white-tee"], date="2026-04-13", now=NOW - timedelta(days=1))
    service.wear_items(["white-tee"], date=DATE, now=NOW)  # tee threshold is 2 wears
    service.edit_garment("dark-jeans", status="dirty", now=NOW)
    assert service.resolve_garment("white-tee").status == "dirty"

    event = service.launder(all_dirty=True, note="sunday wash", now=NOW)
    laundered = set(event.garment_ids)
    assert service.resolve_garment("white-tee").id in laundered
    assert service.resolve_garment("dark-jeans").id in laundered
    for name in ("white-tee", "dark-jeans"):
        garment = service.resolve_garment(name)
        assert garment.status == "clean"
        assert garment.wears_since_wash == 0


def test_fr3_launder_named_garment_from_in_laundry(service: DresscastService) -> None:
    service.edit_garment("navy-hoodie", status="dirty", now=NOW)
    service.edit_garment("navy-hoodie", status="in_laundry", now=NOW)
    event = service.launder(["navy-hoodie"], now=NOW)
    assert event.garment_ids == [service.resolve_garment("navy-hoodie").id]
    assert service.resolve_garment("navy-hoodie").status == "clean"


def test_fr3_laundering_a_clean_garment_is_an_invalid_transition(
    service: DresscastService,
) -> None:
    with pytest.raises(InvalidTransition):
        service.launder(["white-tee"], now=NOW)


def test_fr3_launder_all_with_nothing_dirty_is_invalid_params(
    service: DresscastService,
) -> None:
    with pytest.raises(InvalidParams):
        service.launder(all_dirty=True, now=NOW)


def test_fr3_manual_dirty_is_allowed_below_the_threshold(service: DresscastService) -> None:
    garment = service.edit_garment("gray-lambswool-sweater", status="dirty", now=NOW)
    assert garment.status == "dirty"
    assert garment.wears_since_wash == 0  # a manual mark may hold at lower counts


def test_fr3_clean_to_in_laundry_is_refused(service: DresscastService) -> None:
    with pytest.raises(InvalidTransition):
        service.edit_garment("white-tee", status="in_laundry", now=NOW)


def test_fr3_retired_is_terminal(service: DresscastService) -> None:
    service.edit_garment("white-tee", status="retired", now=NOW)
    with pytest.raises(InvalidTransition):
        service.edit_garment("white-tee", status="clean", now=NOW)


def test_fr3_retired_garments_never_reach_a_recommendation(
    service: DresscastService,
) -> None:
    """Retiring every footwear leaves HC-1 unfillable — never silently ignored."""
    for name in ("white-sneakers", "brown-leather-boots", "beige-sandals"):
        service.edit_garment(name, status="retired", now=NOW)
    with pytest.raises(InfeasibleWardrobe) as excinfo:
        service.recommend(date=DATE, occasion="casual", now=NOW)
    assert "brief" in excinfo.value.detail  # FR-14: the FR-16 payload rides along
