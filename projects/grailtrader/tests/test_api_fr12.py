"""FR-12: the FastAPI surface — happy paths, status codes and the error catalog."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from grailtrader.api import create_app, get_service
from grailtrader.service import GrailTraderService
from grailtrader.store import InMemoryRepository

CLOCK = "2026-07-31T08:00:00Z"


@pytest.fixture
def service() -> GrailTraderService:
    repo = InMemoryRepository()
    repo.initialize(reset=True)
    api = GrailTraderService(repo, clock=lambda: CLOCK)
    api.initialize()
    return api


@pytest.fixture
def client(service: GrailTraderService) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def loaded(
    client: TestClient, mini_feed: tuple[Path, Path], mini_weeks: list[str]
) -> TestClient:
    """A client whose store has listings, an index and one confirmed event."""
    listings, events = mini_feed
    assert client.post(
        "/listings/load", json={"source": "fixture", "path": str(listings)}
    ).status_code == 200
    assert client.post(
        "/events/ingest", json={"source": "fixture", "path": str(events)}
    ).status_code == 200
    assert client.post("/index/build", json={"as_of": mini_weeks[-1]}).status_code == 200
    return client


def test_fr12_health_reports_the_loaded_state(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["config_version"] == "1.0.0"
    assert body["brands"] > 0
    assert body["listings"] == 0


def test_fr12_listings_load_is_idempotent(
    client: TestClient, mini_feed: tuple[Path, Path]
) -> None:
    listings, _ = mini_feed
    first = client.post("/listings/load", json={"path": str(listings)}).json()
    assert first["ingested"] == 60 * 3 * 9
    assert first["skipped_unresolved"] == 0
    second = client.post("/listings/load", json={"path": str(listings)}).json()
    assert second["ingested"] == 0
    assert second["duplicates"] == first["ingested"]


def test_fr12_listings_list_and_show(loaded: TestClient) -> None:
    rows = loaded.get("/listings", params={"stratum": "helmut-lang", "limit": 5}).json()
    assert len(rows) == 5
    assert all(row["brand_id"] == "helmut-lang" for row in rows)
    detail = loaded.get(f"/listings/{rows[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == rows[0]["id"]


def test_fr12_index_build_and_series(loaded: TestClient) -> None:
    strata = loaded.get("/strata", params={"level": "leaf"}).json()["strata"]
    assert "helmut-lang/helmut/outerwear" in strata
    assert loaded.get("/strata", params={"level": "brand"}).json()["strata"] == [
        "celine",
        "helmut-lang",
    ]
    series = loaded.get(
        "/index/helmut-lang/helmut/outerwear", params={"weeks": 6, "excluded": True}
    ).json()
    assert series["stratum_id"] == "helmut-lang/helmut/outerwear"
    assert len(series["points"]) == 6
    assert all(point["n_sales"] >= 5 for point in series["points"])
    assert all(point["level_usd"] is not None for point in series["points"])
    parent = loaded.get("/index/helmut-lang").json()
    assert parent["points"][0]["index_value"] == pytest.approx(100.0)
    assert parent["points"][0]["level_usd"] is None


def test_fr12_events_ingest_add_and_detail(loaded: TestClient) -> None:
    rows = loaded.get("/events").json()
    assert len(rows) == 1
    detail = loaded.get(f"/events/{rows[0]['id']}").json()
    assert detail["targets"] == [
        {"kind": "era", "stratum": "helmut-lang/helmut"}
    ]
    assert detail["priors"][0]["key"] == "designer_departure.resignation"
    assert detail["retirement_age_weeks"] == pytest.approx(26.0)
    assert detail["corroboration"] == 1

    created = loaded.post(
        "/events",
        json={
            "event_type": "brand_scandal",
            "brand": "Céline",
            "occurred_on": "2025-06-02",
            "attributes": {"severity": "moderate"},
        },
    )
    assert created.status_code == 201
    assert created.json()["brand_id"] == "celine"
    # The same fact from a second source corroborates instead of duplicating (FR-5).
    again = loaded.post(
        "/events",
        json={
            "event_type": "brand_scandal",
            "brand": "celine",
            "occurred_on": "2025-06-02",
            "attributes": {"severity": "severe"},
            "source": "news",
            "source_ref": "https://www.other-news.test/celine",
        },
    )
    assert again.status_code == 200
    assert again.json()["id"] == created.json()["id"]
    assert again.json()["corroboration"] == 2
    assert again.json()["attributes"]["severity"] == "moderate"  # first-seen wins


def test_fr12_portfolio_crud_and_valuation(loaded: TestClient, mini_weeks: list[str]) -> None:
    created = loaded.post(
        "/portfolio",
        json={
            "label": "HL astro moto",
            "brand": "helmut-lang",
            "era": "helmut",
            "category": "outerwear",
            "condition": "excellent",
            "price": 1200.0,
            "date": mini_weeks[10],
        },
    )
    assert created.status_code == 201
    garment_id = created.json()["id"]

    valuation = loaded.get(f"/portfolio/{garment_id}/valuation").json()
    assert valuation["valuation_method"] == "repeat_sales"
    assert valuation["fair_value"] > 0
    assert valuation["level_usd"] > 0

    patched = loaded.patch(f"/portfolio/{garment_id}", json={"condition": "good"})
    assert patched.status_code == 200
    assert patched.json()["condition"] == "good"
    assert patched.json()["anchor_condition"] == "excellent"

    worse = loaded.get(f"/portfolio/{garment_id}/valuation").json()
    assert worse["fair_value"] < valuation["fair_value"]

    total = loaded.get("/portfolio/value").json()
    assert total["total_fair_value"] == pytest.approx(worse["fair_value"])

    removed = loaded.delete(f"/portfolio/{garment_id}")
    assert removed.status_code == 200
    assert removed.json()["deleted_at"] == CLOCK
    assert loaded.get("/portfolio").json()["garments"] == []


def test_fr12_advise_and_advice_history(loaded: TestClient, mini_weeks: list[str]) -> None:
    loaded.post(
        "/portfolio",
        json={
            "label": "HL astro moto",
            "brand": "helmut-lang",
            "era": "helmut",
            "category": "outerwear",
            "condition": "excellent",
            "price": 1200.0,
            "date": mini_weeks[10],
        },
    )
    week = mini_weeks[47]
    body = loaded.post("/advise", json={"as_of": week}).json()
    assert body["as_of_week"] == week
    assert sum(body["counts"].values()) == 1
    advice = body["advice"][0]
    assert advice["action"] == "buy"
    assert advice["frame_checked"] is True
    assert advice["expected_return"] >= 0.12
    assert "not investment advice" in advice["rendered_text"]
    assert any(code.startswith("driver:event:") for code in advice["rationale_codes"])

    # Re-running with unchanged inputs is a no-op: same id, still one current row.
    repeat = loaded.post("/advise", json={"as_of": week}).json()
    assert repeat["advice"][0]["id"] == advice["id"]
    assert len(loaded.get("/advice", params={"as_of": week}).json()) == 1
    assert loaded.get(f"/advice/{advice['id']}").status_code == 200
    assert loaded.get("/advice", params={"action": "sell"}).json() == []


def test_fr12_backtest_run_and_fetch(loaded: TestClient, mini_weeks: list[str]) -> None:
    loaded.post(
        "/portfolio",
        json={
            "label": "HL astro moto",
            "brand": "helmut-lang",
            "era": "helmut",
            "category": "outerwear",
            "condition": "excellent",
            "price": 1200.0,
            "date": mini_weeks[10],
        },
    )
    created = loaded.post(
        "/backtests", json={"start": mini_weeks[20], "end": mini_weeks[-1]}
    )
    assert created.status_code == 201
    run = created.json()
    assert run["aggregates"]["n_decisions"] == 40
    assert run["n_results"] == 40
    assert loaded.get(f"/backtests/{run['id']}").json()["id"] == run["id"]
    assert loaded.get("/backtests").json()[0]["id"] == run["id"]

    placebo = loaded.post(
        "/backtests",
        json={"start": mini_weeks[20], "end": mini_weeks[-1], "placebo_seed": 20260731},
    ).json()
    assert placebo["params"]["placebo_seed"] == 20260731
    assert placebo["id"] != run["id"]


def test_fr12_error_catalog(client: TestClient, mini_feed: tuple[Path, Path]) -> None:
    missing = client.get("/listings/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    # advise before an index exists -> precondition, not a 500
    early = client.post("/advise", json={})
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "precondition_failed"

    unknown_brand = client.post(
        "/portfolio",
        json={
            "label": "x",
            "brand": "helmut lanng",
            "era": "helmut",
            "category": "outerwear",
            "condition": "excellent",
            "price": 10.0,
            "date": "2025-01-06",
        },
    )
    assert unknown_brand.status_code == 422
    body = unknown_brand.json()["error"]
    assert body["code"] == "unknown_reference"
    assert body["detail"]["suggestion"] == "helmut lang"  # the gazetteer alias

    bad_body = client.post("/portfolio", json={"label": "x"})
    assert bad_body.status_code == 422
    assert bad_body.json()["error"]["code"] == "invalid_request"

    unknown_stratum = client.get("/index/not-a-brand")
    assert unknown_stratum.status_code == 422
    assert unknown_stratum.json()["error"]["code"] == "unknown_reference"

    missing_feed = client.post("/listings/load", json={"path": "/nope/listings.jsonl"})
    assert missing_feed.status_code == 422
    assert missing_feed.json()["error"]["code"] == "feed_unreadable"


def test_fr12_openapi_documents_every_sketched_endpoint(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    for path in (
        "/health",
        "/listings/load",
        "/listings",
        "/listings/{listing_id}",
        "/index/build",
        "/index/{stratum_id}",
        "/strata",
        "/events/ingest",
        "/events",
        "/events/{event_id}",
        "/events/{event_id}/confirm",
        "/events/{event_id}/reject",
        "/brands",
        "/brands/{brand_id}",
        "/portfolio",
        "/portfolio/{garment_id}",
        "/portfolio/{garment_id}/valuation",
        "/advise",
        "/advice",
        "/advice/{advice_id}",
        "/backtests",
        "/backtests/{run_id}",
    ):
        assert path in paths, path
