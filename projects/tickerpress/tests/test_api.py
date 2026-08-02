"""FR-14: the FastAPI surface — status codes, error catalog, thin behaviour."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tickerpress.adapters.clock import FixedClock
from tickerpress.adapters.feeds_fixture import FixtureFeedSource
from tickerpress.api.app import create_app
from tickerpress.engine.models import Channel
from tickerpress.resources import Lexicons
from tickerpress.services import NotifierRegistry, TickerPressService
from tickerpress.store import InMemoryRepository, SQLiteRepository
from tickerpress_testkit import NOW, rss_feed, rss_item

FEED_URL = "file:///fixtures/wire_one.xml"

WIRE_ONE = rss_feed(
    [
        rss_item(
            guid="wireone-1",
            link="https://wireone.example.com/apple-q2?utm_source=rss",
            title="Apple beats March-quarter estimates on services strength",
            description=(
                "Apple Inc. (NASDAQ: AAPL) reported quarterly revenue of $96.4 billion, ahead "
                "of analyst estimates, as services growth offset softer iPhone sales. Shares "
                "rose 3% in extended trading."
            ),
            pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
        ),
        rss_item(
            guid="wireone-2",
            link="https://wireone.example.com/apple-growers",
            title="Apple growers brace for frost across the valley",
            description=(
                "Growers across the valley expect a difficult harvest after an early frost "
                "damaged the orchard blossom. The cooperative said the crop may fall by a fifth."
            ),
            pub_date="Sun, 01 Mar 2026 10:00:00 GMT",
        ),
    ]
)


@pytest.fixture
def api_repository() -> Iterator[SQLiteRepository]:
    repo = InMemoryRepository()
    try:
        yield repo
    finally:
        repo.close()


@pytest.fixture
def api_service(
    api_repository: SQLiteRepository, lexicons: Lexicons, tmp_path: Path
) -> TickerPressService:
    return TickerPressService(
        api_repository,
        clock=FixedClock(NOW),
        feed_source=FixtureFeedSource(documents={FEED_URL: WIRE_ONE}),
        notifiers=NotifierRegistry(outbox_dir=tmp_path / "outbox"),
        lexicons=lexicons,
    )


@pytest.fixture
def client(api_service: TickerPressService) -> Iterator[TestClient]:
    with TestClient(create_app(api_service)) as test_client:
        yield test_client


def _seed_watchlist(client: TestClient) -> None:
    client.post(
        "/companies",
        json={
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "context_terms": ["cupertino", "iphone"],
            "anti_terms": ["orchard", "harvest", "frost"],
        },
    )
    client.post("/feeds", json={"name": "Wire One", "url": FEED_URL})


def _ingest(client: TestClient) -> dict:
    response = client.post("/ingest/runs", json={"now": "2026-03-02T13:00:00Z"})
    assert response.status_code == 201
    return response.json()


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


def test_fr14_health_reports_archive_size(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body == {
        "status": "ok",
        "version": body["version"],
        "companies": 0,
        "feeds": 0,
        "articles": 0,
        "stories": 0,
    }


# ---------------------------------------------------------------------------
# watchlist (FR-1)
# ---------------------------------------------------------------------------


def test_fr14_create_company_returns_201_with_generated_aliases(client: TestClient) -> None:
    response = client.post("/companies", json={"ticker": "tsla", "name": "Tesla, Inc."})
    assert response.status_code == 201
    body = response.json()
    assert body["ticker"] == "TSLA"
    assert [(a["text"], a["kind"], a["strength"]) for a in body["aliases"]] == [
        ("TSLA", "ticker_symbol", "strong"),
        ("$TSLA", "cashtag", "strong"),
        ("Tesla, Inc.", "legal_name", "strong"),
        ("Tesla", "short_name", "weak"),
    ]


def test_fr14_duplicate_company_is_409_conflict(client: TestClient) -> None:
    client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    response = client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_fr14_invalid_ticker_is_422_validation_error(client: TestClient) -> None:
    response = client.post("/companies", json={"ticker": "not a ticker", "name": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_fr14_unknown_company_is_404_not_found(client: TestClient) -> None:
    response = client.get("/companies/MSFT")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": "unknown company MSFT",
        "detail": {"ticker": "MSFT"},
    }


def test_fr14_patch_company_updates_delivery_settings(client: TestClient) -> None:
    client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    response = client.patch(
        "/companies/TSLA",
        json={"mode": "both", "alert_min_relevance": 60, "context_terms": ["Gigafactory"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "both"
    assert body["context_terms"] == ["gigafactory"]


def test_fr14_delete_company_is_204_then_404(client: TestClient) -> None:
    client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    assert client.delete("/companies/TSLA").status_code == 204
    assert client.delete("/companies/TSLA").status_code == 404


def test_fr14_alias_post_applies_kind_defaults_when_null(client: TestClient) -> None:
    client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    response = client.post(
        "/companies/TSLA/aliases",
        json={"text": "Tesla Motors", "kind": "nickname", "strength": None, "prior": None},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["strength"], body["prior"], body["generated"]) == ("weak", 0.10, False)


def test_fr14_duplicate_alias_surface_is_409(client: TestClient) -> None:
    client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    response = client.post("/companies/TSLA/aliases", json={"text": "tesla", "kind": "nickname"})
    assert response.status_code == 409


def test_fr14_delete_alias_204_and_unknown_alias_404(client: TestClient) -> None:
    client.post("/companies", json={"ticker": "TSLA", "name": "Tesla, Inc."})
    alias_id = client.get("/companies/TSLA").json()["aliases"][-1]["id"]
    assert client.delete(f"/companies/TSLA/aliases/{alias_id}").status_code == 204
    assert client.delete(f"/companies/TSLA/aliases/{alias_id}").status_code == 404


# ---------------------------------------------------------------------------
# feeds (FR-2)
# ---------------------------------------------------------------------------


def test_fr14_feed_lifecycle(client: TestClient) -> None:
    created = client.post("/feeds", json={"name": "Wire One", "url": FEED_URL})
    assert created.status_code == 201
    feed_id = created.json()["id"]
    assert client.get("/feeds").json()[0]["enabled"] is True
    patched = client.patch(f"/feeds/{feed_id}", json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False
    assert client.delete(f"/feeds/{feed_id}").status_code == 204
    assert client.patch(f"/feeds/{feed_id}", json={"enabled": True}).status_code == 404


def test_fr14_deleting_a_feed_with_articles_is_409(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    feed_id = client.get("/feeds").json()[0]["id"]
    response = client.delete(f"/feeds/{feed_id}")
    assert response.status_code == 409
    assert "disable it instead" in response.json()["error"]["message"]


def test_fr14_unsupported_feed_scheme_is_422(client: TestClient) -> None:
    response = client.post("/feeds", json={"name": "bad", "url": "ftp://example.com/feed.xml"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# ingest (FR-2, FR-10)
# ---------------------------------------------------------------------------


def test_fr14_ingest_run_returns_summary_and_is_listable(client: TestClient) -> None:
    _seed_watchlist(client)
    run = _ingest(client)
    assert run["articles_new"] == 2
    assert run["status"] == "succeeded"
    assert run["feed_results"][0]["items_seen"] == 2
    assert client.get("/ingest/runs").json()[0]["id"] == run["id"]
    assert client.get(f"/ingest/runs/{run['id']}").json()["id"] == run["id"]
    assert client.get("/ingest/runs/9999").status_code == 404


def test_fr14_ingest_with_unknown_feed_id_is_404(client: TestClient) -> None:
    assert client.post("/ingest/runs", json={"feed_id": 42}).status_code == 404


def test_fr14_reingest_is_idempotent(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    second = _ingest(client)
    assert second["articles_new"] == 0
    assert client.get("/health").json()["articles"] == 2


# ---------------------------------------------------------------------------
# archive + explain (FR-4, FR-13)
# ---------------------------------------------------------------------------


def test_fr14_articles_can_be_filtered_by_company_and_relevance(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    all_articles = client.get("/articles").json()
    assert len(all_articles) == 2
    relevant = client.get("/articles", params={"company": "aapl", "min_relevance": 90}).json()
    assert [a["title"] for a in relevant] == [
        "Apple beats March-quarter estimates on services strength"
    ]


def test_fr14_article_detail_carries_appearances(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    article_id = client.get("/articles", params={"company": "AAPL"}).json()[0]["id"]
    body = client.get(f"/articles/{article_id}").json()
    assert body["appearances"][0]["company_ticker"] == "AAPL"
    assert body["appearances"][0]["relevance"] == 94
    assert client.get("/articles/9999").status_code == 404


def test_fr14_explain_shows_accepted_and_rejected_candidates(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    trap = next(
        article
        for article in client.get("/articles").json()
        if article["title"].startswith("Apple growers")
    )
    body = client.get(f"/articles/{trap['id']}/explain", params={"company": "AAPL"}).json()
    assert body["candidates"], "the rejected candidate must still be visible"
    candidate = body["candidates"][0]
    assert candidate["accepted"] is False
    assert candidate["alias_text"] == "Apple"
    assert candidate["features"]["anti_terms"] >= 1
    assert body["appearances"] == []
    assert client.get("/articles/9999/explain").status_code == 404


def test_fr14_stories_list_and_detail(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    stories = client.get("/stories", params={"company": "AAPL"}).json()
    assert len(stories) == 1
    detail = client.get(f"/stories/{stories[0]['id']}").json()
    assert detail["relevance"] == {"AAPL": 94}
    assert len(detail["members"]) == 1
    assert client.get("/stories/9999").status_code == 404


# ---------------------------------------------------------------------------
# delivery (FR-9, FR-11)
# ---------------------------------------------------------------------------


def test_fr14_empty_digest_is_204_in_both_modes(client: TestClient) -> None:
    assert client.post("/digests", json={"channel": "console"}).status_code == 204
    assert client.post("/digests", json={"channel": "console", "dry_run": True}).status_code == 204
    assert client.get("/digests").json() == []


def test_fr14_digest_returns_body_and_ledger_items(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    response = client.post("/digests", json={"channel": "file"})
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False
    assert body["delivery"]["status"] == "sent"
    assert "Not investment advice." in body["body_text"]
    assert [item["company_ticker"] for item in body["items"]] == ["AAPL"]
    # exactly once: the same digest a second time selects nothing
    assert client.post("/digests", json={"channel": "file"}).status_code == 204
    items = client.get("/deliveries", params={"company": "AAPL"}).json()
    assert len(items) == 1 and items[0]["counted"] is True


def test_fr14_dry_run_persists_nothing(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    response = client.post("/digests", json={"channel": "file", "dry_run": True})
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["delivery"] is None
    assert client.get("/digests").json() == []
    assert client.get("/deliveries").json() == []
    # and the story is still eligible afterwards
    assert client.post("/digests", json={"channel": "file"}).status_code == 200


def test_fr14_digest_detail_includes_body_text(client: TestClient) -> None:
    _seed_watchlist(client)
    _ingest(client)
    delivery_id = client.post("/digests", json={"channel": "file"}).json()["delivery"]["id"]
    listed = client.get("/digests", params={"channel": "file"}).json()
    assert listed[0]["body_text"] is None, "the list projection stays compact"
    detail = client.get(f"/digests/{delivery_id}").json()
    assert detail["body_text"].startswith("# TickerPress digest")
    assert client.get("/digests/9999").status_code == 404


def test_fr14_unconfigured_live_channel_is_503(
    api_repository: SQLiteRepository, lexicons: Lexicons, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TICKERPRESS_SMTP_HOST", raising=False)
    service = TickerPressService(
        api_repository,
        clock=FixedClock(NOW),
        feed_source=FixtureFeedSource(documents={FEED_URL: WIRE_ONE}),
        notifiers=NotifierRegistry(outbox_dir="unused"),
        lexicons=lexicons,
    )
    with TestClient(create_app(service)) as client:
        _seed_watchlist(client)
        _ingest(client)
        response = client.post("/digests", json={"channel": "email"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "channel_unavailable"
        # nothing was counted, so the story stays eligible on that channel
        assert client.get("/deliveries", params={"channel": Channel.EMAIL.value}).json() == []
