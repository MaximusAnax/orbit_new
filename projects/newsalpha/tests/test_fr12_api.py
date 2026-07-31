"""FR-12: the FastAPI surface -- routing, schemas, status codes, error catalog.

The API is thin by contract, so these tests check the contract: that each handler
delegates to the same service the CLI uses, serializes the domain model without
inventing fields, and maps every catalog code onto the documented status.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from newsalpha.api.app import STATUS_BY_CODE, create_app
from newsalpha.errors import NewsAlphaError
from newsalpha.service import NewsAlphaService
from newsalpha.store.memory import InMemoryRepository
from newsalpha_edge_kit import AS_OF, write_corpus, write_market


@pytest.fixture()
def service(datasets) -> NewsAlphaService:
    repository = InMemoryRepository()
    repository.initialize()
    service = NewsAlphaService(repository, datasets, active_window_days=30)
    service.initialize()
    return service


@pytest.fixture()
def client(service) -> TestClient:
    return TestClient(create_app(service))


@pytest.fixture()
def corpus(tmp_path):
    return write_corpus(tmp_path)


@pytest.fixture()
def market(tmp_path):
    return write_market(tmp_path)


@pytest.fixture()
def ingested(client, corpus):
    response = client.post("/ingest", json={"as_of": AS_OF, "feed": "fixture", "path": str(corpus)})
    assert response.status_code == 201, response.text
    return response.json()


def test_fr12_health_reports_the_loaded_datasets(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["assets"] > 100
    assert body["patterns"] >= 14 and body["priors"] > 0 and body["templates"] > 0


def test_fr12_ingest_returns_the_documented_counts(ingested):
    assert ingested["articles"] == 5
    assert ingested["clusters"] >= 1
    assert ingested["events"] >= 3
    assert ingested["signals_new"] >= 3
    assert ingested["revisions_new"] == 0
    assert ingested["briefs"] == ingested["signals_new"] + ingested["revisions_new"]


def test_fr12_reingest_is_idempotent_and_writes_no_revisions(client, corpus, ingested):
    again = client.post(
        "/ingest", json={"as_of": AS_OF, "feed": "fixture", "path": str(corpus)}
    ).json()
    assert again["articles"] == 0
    assert again["signals_new"] == 0 and again["revisions_new"] == 0


def test_fr12_ingest_with_an_unreadable_corpus_is_503(client, tmp_path):
    response = client.post(
        "/ingest", json={"as_of": AS_OF, "feed": "fixture", "path": str(tmp_path / "nope.jsonl")}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "feed_unavailable"


def test_fr12_ingest_rejects_an_unknown_feed_kind(client):
    response = client.post("/ingest", json={"as_of": AS_OF, "feed": "carrier-pigeon"})
    assert response.status_code == 422


def test_fr12_articles_list_and_show(client, ingested):
    listing = client.get("/articles").json()
    assert listing["count"] == 5
    article_id = listing["articles"][0]["id"]
    detail = client.get(f"/articles/{article_id}").json()
    assert detail["id"] == article_id
    assert detail["content_hash"]
    assert client.get("/articles?domain=reuters.example").json()["count"] == 1


def test_fr12_unknown_article_is_404_with_a_catalog_code(client, ingested):
    response = client.get("/articles/0000000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_article"


def test_fr12_events_show_returns_evidence_spans_and_links(client, ingested):
    listing = client.get("/events").json()
    assert listing["count"] >= 3
    hack = next(e for e in listing["events"] if e["event_type"] == "hack_exploit")
    detail = client.get(f"/events/{hack['id']}").json()
    assert detail["evidence"] and detail["evidence"][0]["quote"]
    assert any(link["role"] == "subject" for link in detail["links"])
    assert all("asset_name" in link for link in detail["links"])


def test_fr12_events_filters(client, ingested):
    assert client.get("/events?type=mna").json()["count"] == 1
    assert client.get("/events?stage=confirmed").json()["count"] >= 3
    assert client.get("/events?asset=cx:SOL").json()["count"] == 1
    assert client.get("/events/deadbeefdeadbeef").status_code == 404


def test_fr12_signals_list_show_and_revisions(client, ingested):
    listing = client.get("/signals").json()
    assert listing["count"] >= 3
    signal = listing["signals"][0]
    assert "direction" in signal and signal["direction"] in ("bullish", "bearish")
    assert not any(field.startswith("action") for field in signal)  # US-4: no imperative field
    detail = client.get(f"/signals/{signal['id']}").json()
    assert detail["signal_key"] == signal["signal_key"]
    chain = client.get(f"/signals/{signal['id']}/revisions").json()
    assert chain["count"] == 1 and chain["revisions"][0]["revision"] == 1
    assert client.get("/signals/0000000000000000/revisions").status_code == 404


def test_fr12_signals_filters(client, ingested):
    bearish = client.get("/signals?direction=bearish").json()
    assert all(s["direction"] == "bearish" for s in bearish["signals"])
    strict = client.get("/signals?min_confidence=0.9").json()
    assert all(s["confidence"] >= 0.9 for s in strict["signals"])
    assert client.get("/signals?min_confidence=3").status_code == 422


def test_fr12_brief_is_served_per_signal_revision(client, ingested):
    signal = client.get("/signals").json()["signals"][0]
    brief = client.get(f"/briefs/{signal['id']}").json()
    assert brief["frame_checked"] is True
    assert brief["what_happened"] and brief["why_it_matters"] and brief["uncertainty_note"]
    assert brief["what_to_watch"]
    assert "not investment advice" in brief["rendered_text"]
    missing = client.get("/briefs/0000000000000000")
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_brief"


def test_fr12_digest_ranks_and_renders_the_empty_state(client, ingested):
    empty = client.get("/digest?date=2026-03-19").json()
    assert empty["entries"] == [] and empty["empty_state"]

    client.put("/watchlist/cx:SOL")
    watched = client.get("/digest?date=2026-03-19").json()
    assert [entry["asset_id"] for entry in watched["entries"]] == ["cx:SOL"]

    everything = client.get("/digest?date=2026-03-19&watchlist_only=false").json()
    scores = [abs(entry["score"]) for entry in everything["entries"]]
    assert scores == sorted(scores, reverse=True)
    assert everything["entries"][0]["summary"]


def test_fr12_assets_directory(client):
    crypto = client.get("/assets?kind=crypto").json()
    assert crypto["count"] > 20
    assert client.get("/assets?q=solana").json()["count"] == 1
    assert client.get("/assets/cx:SOL").json()["symbol"] == "SOL"
    missing = client.get("/assets/cx:NOPE")
    assert missing.status_code == 404
    assert missing.json()["details"]["suggestion"]


def test_fr12_watchlist_put_list_delete(client):
    assert client.put("/watchlist/eq:MSFT").json() == {
        "asset_id": "eq:MSFT",
        "added": True,
        "removed": False,
    }
    assert client.put("/watchlist/eq:MSFT").json()["added"] is False
    assert client.get("/watchlist").json()["count"] == 1
    assert client.delete("/watchlist/eq:MSFT").json()["removed"] is True
    assert client.delete("/watchlist/eq:MSFT").status_code == 404
    unknown = client.put("/watchlist/eq:NOTREAL")
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_asset"


def test_fr12_prices_load_and_backtest_round_trip(client, ingested, market):
    loaded = client.post(
        "/prices/load",
        json={
            "source": "fixture",
            "directory": str(market),
            "assets": ["cx:SOL", "eq:MSFT", "eq:ADBE", "eq:OKTA", "idx:US", "idx:CX"],
            "start": "2026-03-01",
            "end": "2026-04-30",
        },
    )
    assert loaded.status_code == 201 and loaded.json()["bars"] > 100

    response = client.post(
        "/backtests",
        json={"start": "2026-03-01", "end": "2026-03-31", "as_of": AS_OF},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["run"]["aggregates"]["n"] >= 1
    assert body["results"]
    run_id = body["run"]["id"]

    fetched = client.get(f"/backtests/{run_id}").json()
    assert fetched["run"]["id"] == run_id
    assert client.get("/backtests?limit=5").json()["count"] == 1
    assert client.get("/backtests/0000000000000000").status_code == 404


def test_fr12_placebo_backtest_is_a_separate_run(client, ingested, market):
    client.post(
        "/prices/load",
        json={
            "source": "fixture",
            "directory": str(market),
            "start": "2026-03-01",
            "end": "2026-04-30",
            "assets": ["cx:SOL", "eq:MSFT", "eq:ADBE", "eq:OKTA", "idx:US", "idx:CX"],
        },
    )
    real = client.post(
        "/backtests", json={"start": "2026-03-01", "end": "2026-03-31", "as_of": AS_OF}
    )
    placebo = client.post(
        "/backtests",
        json={"start": "2026-03-01", "end": "2026-03-31", "placebo_seed": 20260731, "as_of": AS_OF},
    )
    assert real.json()["run"]["id"] != placebo.json()["run"]["id"]
    assert placebo.json()["run"]["params"]["placebo_seed"] == 20260731


def test_fr12_prices_load_with_a_missing_directory_is_503(client, tmp_path):
    response = client.post(
        "/prices/load",
        json={
            "source": "fixture",
            "directory": str(tmp_path / "absent"),
            "assets": ["cx:SOL"],
            "start": "2026-03-01",
            "end": "2026-03-10",
        },
    )
    assert response.status_code == 503
    assert response.json()["code"] == "market_data_unavailable"


def test_fr12_error_catalog_covers_every_declared_code():
    codes = {
        cls.code for cls in _subclasses(NewsAlphaError) if getattr(cls, "code", None) is not None
    } | {NewsAlphaError.code}
    missing = codes - set(STATUS_BY_CODE)
    assert not missing, f"error catalog codes without an HTTP status: {sorted(missing)}"


def _subclasses(root: type) -> list[type]:
    out: list[type] = []
    for cls in root.__subclasses__():
        out.append(cls)
        out.extend(_subclasses(cls))
    return out


def test_fr12_superseded_signals_are_hidden_by_default(client, corpus, tmp_path):
    """FR-8/FR-15: a denial demotes its own earlier signal on the list surfaces."""
    from newsalpha_edge_kit import write_denial

    client.post("/ingest", json={"as_of": AS_OF, "feed": "fixture", "path": str(corpus)})
    later = "2026-03-25T07:00:00Z"
    client.post(
        "/ingest", json={"as_of": later, "feed": "fixture", "path": str(write_denial(tmp_path))}
    )

    default = client.get("/signals?asset=eq:OKTA").json()
    assert default["count"] == 1
    assert default["signals"][0]["direction"] == "bearish"
    assert default["signals"][0]["supersedes_key"]

    everything = client.get("/signals?asset=eq:OKTA&include_superseded=true").json()
    assert everything["count"] == 2
    assert {s["direction"] for s in everything["signals"]} == {"bullish", "bearish"}

    digest = client.get("/digest?date=2026-03-25&watchlist_only=false").json()
    okta = [entry for entry in digest["entries"] if entry["asset_id"] == "eq:OKTA"]
    assert len(okta) == 1 and okta[0]["direction"] == "bearish"
    assert okta[0]["supersession_note"].startswith("supersedes an earlier confirmed signal")
