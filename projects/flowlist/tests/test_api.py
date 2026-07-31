"""FR-13: the HTTP surface, including the full error-code catalog."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from flowlist.api import STATUS_BY_CODE, create_app
from flowlist.engine.models import MAX_PLAYLIST_SIZE
from flowlist.store.memory import InMemoryRepository

CSV = """Track URI,Track Name,Artist Name(s),Album Name,Duration (ms),Tempo,Key,Mode,Energy,Danceability,Loudness
spotify:track:3n3Ppam7vgaVa1iaRUc9Lp,One More Hour,Synthetic Sun,Neon Hours,214000,124.0,9,0,0.71,0.80,-7.2
spotify:track:1n3Ppam7vgaVa1iaRUc9Lp,Night Drive,Vera Lux,Afterglow,231000,126.5,4,0,0.76,0.78,-6.4
spotify:track:2n3Ppam7vgaVa1iaRUc9Lp,Slow Burn,Marta Quiet,Embers,198000,86.0,7,1,0.42,0.65,-11.0
spotify:track:4n3Ppam7vgaVa1iaRUc9Lp,Breakline,Cyan Drift,Signal,205000,174.0,2,0,0.88,0.72,-5.1
spotify:track:5n3Ppam7vgaVa1iaRUc9Lp,Paper Moon,Halcyon Bay,Tide,222000,122.0,0,1,0.68,0.74,-7.9
spotify:track:6n3Ppam7vgaVa1iaRUc9Lp,No Key Here,Ghost Signal,Static,180000,0,-1,1,0.55,0.60,-9.0
"""


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(InMemoryRepository())
    with TestClient(app) as test_client:
        yield test_client


def _import(client: TestClient, name: str = "party", **extra: object) -> dict:
    response = client.post(
        "/playlists/import", json={"name": name, "format": "csv", "content": CSV, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Health and import
# --------------------------------------------------------------------------- #


def test_fr13_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_fr13_import_csv_content(client: TestClient) -> None:
    body = _import(client)
    assert body["playlist"]["entries"] == 6
    assert body["replaced"] is False
    codes = {issue["code"] for issue in body["warnings"]}
    assert codes == {"bpm_sentinel", "key_sentinel"}
    assert body["skipped"] == []


def test_fr13_import_from_a_path(client: TestClient, tmp_path) -> None:
    path = tmp_path / "party.csv"
    path.write_text(CSV, encoding="utf-8")
    response = client.post(
        "/playlists/import", json={"name": "from-disk", "format": "csv", "path": str(path)}
    )
    assert response.status_code == 201
    assert response.json()["playlist"]["entries"] == 6


def test_fr13_import_requires_exactly_one_source(client: TestClient) -> None:
    response = client.post("/playlists/import", json={"name": "x", "format": "csv"})
    assert response.status_code == 400
    assert response.json()["code"] == "import_failed"

    response = client.post(
        "/playlists/import",
        json={"name": "x", "format": "csv", "content": CSV, "path": "/tmp/nope.csv"},
    )
    assert response.status_code == 400


def test_fr13_name_conflict_and_replace(client: TestClient) -> None:
    _import(client)
    conflict = client.post(
        "/playlists/import", json={"name": "party", "format": "csv", "content": CSV}
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "name_conflict"

    replaced = client.post(
        "/playlists/import",
        json={"name": "party", "format": "csv", "content": CSV, "replace": True},
    )
    assert replaced.status_code == 201
    assert replaced.json()["replaced"] is True


def test_fr13_replace_with_runs_needs_force(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    assert client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7}).status_code == 201

    blocked = client.post(
        "/playlists/import",
        json={"name": "party", "format": "csv", "content": CSV, "replace": True},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "playlist_has_runs"

    forced = client.post(
        "/playlists/import",
        json={
            "name": "party",
            "format": "csv",
            "content": CSV,
            "replace": True,
            "force": True,
        },
    )
    assert forced.status_code == 201
    assert client.get("/runs").json() == []


# --------------------------------------------------------------------------- #
# Reading playlists and tracks
# --------------------------------------------------------------------------- #


def test_fr13_list_and_get_playlist(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    assert [row["id"] for row in client.get("/playlists").json()] == [playlist_id]

    detail = client.get(f"/playlists/{playlist_id}").json()
    assert detail["entries"] == 6
    assert [row["position"] for row in detail["tracks"]] == list(range(6))
    assert detail["coverage"]["tracks"] == 6
    assert detail["coverage"]["missing"] == {"bpm": 1, "key_pc": 1}
    assert detail["tracks"][0]["feature_sources"]["bpm"] == "import"


def test_fr13_unknown_playlist_is_404(client: TestClient) -> None:
    response = client.get("/playlists/nope")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_playlist"


def test_fr13_get_track_and_manual_override(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    detail = client.get(f"/playlists/{playlist_id}").json()
    track_id = detail["tracks"][-1]["track"]["id"]

    before = client.get(f"/tracks/{track_id}").json()
    assert before["resolved"]["bpm"] is None
    assert {row["source"] for row in before["features"]} == {"import"}

    updated = client.put(f"/tracks/{track_id}/features", json={"bpm": 128.0, "key": "8A"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["resolved"]["bpm"] == 128.0
    assert (body["resolved"]["key_pc"], body["resolved"]["mode"]) == (9, 0)
    assert body["feature_sources"]["bpm"] == "manual"
    assert body["feature_sources"]["energy"] == "import"


def test_fr13_unknown_track_is_404(client: TestClient) -> None:
    assert client.get("/tracks/meta:nope").status_code == 404
    response = client.put("/tracks/meta:nope/features", json={"bpm": 120.0})
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_track"


def test_fr13_unparseable_key_is_rejected(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    track_id = client.get(f"/playlists/{playlist_id}").json()["tracks"][0]["track"]["id"]
    response = client.put(f"/tracks/{track_id}/features", json={"key": "H#dim"})
    assert response.status_code == 400
    assert "H#dim" in response.json()["message"]


def test_fr13_analyze_reports_coverage(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    response = client.post(f"/playlists/{playlist_id}/analyze", json={})
    assert response.status_code == 200
    assert response.json()["full"] == 5

    bad = client.post(f"/playlists/{playlist_id}/analyze", json={"providers": ["nope"]})
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_providers"


# --------------------------------------------------------------------------- #
# Scoring and reordering
# --------------------------------------------------------------------------- #


def test_fr13_score_current_order(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    body = client.post(f"/playlists/{playlist_id}/score", json={}).json()
    assert len(body["transitions"]) == 5
    assert body["weights"] == {
        "key": 0.35,
        "bpm": 0.35,
        "energy": 0.20,
        "loudness": 0.10,
        "danceability": 0.0,
    }
    assert 0.0 <= body["mean"] <= 1.0


def test_fr13_invalid_weights_is_422(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    all_zero = dict.fromkeys(("key", "bpm", "energy", "loudness", "danceability"), 0.0)
    for weights in ({"key": -1.0}, all_zero, {"nope": 1.0}):
        response = client.post(f"/playlists/{playlist_id}/score", json={"weights": weights})
        assert response.status_code == 422, weights
        assert response.json()["code"] == "invalid_weights"


def test_fr13_weights_are_renormalized_on_the_run(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    body = client.post(
        f"/playlists/{playlist_id}/reorder",
        json={"seed": 7, "weights": {"key": 3.5, "bpm": 3.5, "energy": 2.0, "loudness": 1.0}},
    ).json()
    stored = body["run"]["params"]["weights"]
    assert sum(stored.values()) == pytest.approx(1.0, abs=1e-9)
    assert stored["key"] == pytest.approx(0.35, abs=1e-9)


def test_fr13_reorder_creates_a_run(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    response = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7})
    assert response.status_code == 201
    body = response.json()
    assert len(body["entries"]) == 6
    assert body["entries"][0]["transition"] is None
    assert all(entry["transition"] for entry in body["entries"][1:])
    assert body["run"]["score_mean_after"] >= body["run"]["score_mean_before"]
    assert body["applied"] is False

    same = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7}).json()
    assert [e["entry_id"] for e in same["entries"]] == [e["entry_id"] for e in body["entries"]]


def test_fr13_reorder_honours_anchors(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    entries = client.get(f"/playlists/{playlist_id}").json()["tracks"]
    start, end = entries[3]["entry_id"], entries[0]["entry_id"]
    body = client.post(
        f"/playlists/{playlist_id}/reorder",
        json={"seed": 7, "start_entry": start, "end_entry": end, "profile": "build"},
    ).json()
    assert body["entries"][0]["entry_id"] == start
    assert body["entries"][-1]["entry_id"] == end
    assert body["run"]["params"]["profile"] == "build"


def test_fr13_invalid_anchor_is_422(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    response = client.post(
        f"/playlists/{playlist_id}/reorder", json={"seed": 7, "start_entry": "not-an-entry"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_anchor"


def test_fr13_playlist_too_large_is_413(client: TestClient) -> None:
    tracks = [
        {
            "title": f"Track {i}",
            "artist": f"Artist {i}",
            "features": {"bpm": 120.0 + (i % 8), "key_pc": i % 12, "mode": i % 2, "energy": 0.5},
        }
        for i in range(MAX_PLAYLIST_SIZE + 1)
    ]
    created = client.post(
        "/playlists/import",
        json={"name": "huge", "format": "json", "content": json.dumps({"tracks": tracks})},
    )
    assert created.status_code == 201
    playlist_id = created.json()["playlist"]["id"]
    response = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 1})
    assert response.status_code == 413
    body = response.json()
    assert body["code"] == "playlist_too_large"
    assert body["context"] == {"entries": MAX_PLAYLIST_SIZE + 1, "cap": MAX_PLAYLIST_SIZE}


# --------------------------------------------------------------------------- #
# Runs, apply and export
# --------------------------------------------------------------------------- #


def test_fr13_run_listing_and_detail(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    run_id = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7}).json()["run"]["id"]

    listed = client.get("/runs", params={"playlist_id": playlist_id}).json()
    assert [row["id"] for row in listed] == [run_id]
    assert listed[0]["applied"] is False

    detail = client.get(f"/runs/{run_id}").json()
    assert detail["run"]["id"] == run_id
    assert detail["entries"][1]["track"]["title"]


def test_fr13_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/runs/nope").status_code == 404
    assert client.post("/runs/nope/apply").json()["code"] == "unknown_run"
    assert client.get("/runs/nope/export").status_code == 404


def test_fr12_apply_rewrites_positions(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    run = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7}).json()
    proposed = [entry["entry_id"] for entry in run["entries"]]

    applied = client.post(f"/runs/{run['run']['id']}/apply")
    assert applied.status_code == 200
    body = applied.json()
    assert [row["entry_id"] for row in body["tracks"]] == proposed
    assert body["applied_run_id"] == run["run"]["id"]
    assert client.get("/runs").json()[0]["applied"] is True


def test_fr12_export_formats(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    run_id = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7}).json()["run"]["id"]

    m3u = client.get(f"/runs/{run_id}/export", params={"format": "m3u"})
    assert m3u.status_code == 200
    assert m3u.text.startswith("#EXTM3U")
    assert m3u.headers["content-type"].startswith("audio/x-mpegurl")
    assert 'filename="party.m3u8"' in m3u.headers["content-disposition"]

    csv_body = client.get(f"/runs/{run_id}/export", params={"format": "csv"}).text
    assert csv_body.splitlines()[0].endswith("Position,Transition Score,Key Relation")

    payload = json.loads(client.get(f"/runs/{run_id}/export", params={"format": "json"}).text)
    assert payload["playlist_name"] == "party"
    assert len(payload["entries"]) == 6

    assert client.get(f"/runs/{run_id}/export", params={"format": "wav"}).status_code == 422


# --------------------------------------------------------------------------- #
# Deletion
# --------------------------------------------------------------------------- #


def test_fr13_delete_playlist(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    assert client.delete(f"/playlists/{playlist_id}").status_code == 204
    assert client.get("/playlists").json() == []


def test_fr13_delete_is_refused_while_runs_exist(client: TestClient) -> None:
    playlist_id = _import(client)["playlist"]["id"]
    client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7})

    blocked = client.delete(f"/playlists/{playlist_id}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "playlist_has_runs"

    forced = client.delete(f"/playlists/{playlist_id}", params={"force": True})
    assert forced.status_code == 204
    assert client.get("/runs").json() == []


# --------------------------------------------------------------------------- #
# The catalog itself
# --------------------------------------------------------------------------- #


def test_fr13_error_catalog_is_complete() -> None:
    """Every code SCOPE.md FR-13 names maps to a 4xx status."""
    for code in (
        "playlist_too_large",
        "unknown_track",
        "unknown_playlist",
        "unknown_run",
        "invalid_weights",
        "name_conflict",
        "playlist_has_runs",
    ):
        assert 400 <= STATUS_BY_CODE[code] < 500, code


def test_fr13_openapi_lists_every_documented_route(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for route in (
        "/health",
        "/playlists/import",
        "/playlists",
        "/playlists/{playlist_id}",
        "/playlists/{playlist_id}/analyze",
        "/playlists/{playlist_id}/score",
        "/playlists/{playlist_id}/reorder",
        "/tracks/{track_id}",
        "/tracks/{track_id}/features",
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/apply",
        "/runs/{run_id}/export",
    ):
        assert route in paths, route


# --------------------------------------------------------------------------- #
# The served app against a real SQLite file (regression: serve was broken)
# --------------------------------------------------------------------------- #


def test_fr13_served_app_works_against_a_sqlite_file(tmp_path) -> None:
    """The app `flowlist serve` builds must work from worker threads.

    FastAPI executes sync endpoints on threadpool worker threads — a different
    thread than the one that opened the SQLite connection at startup.  With
    sqlite3's default ``check_same_thread=True`` every DB-touching endpoint
    500s under uvicorn while the InMemory-backed tests stay green; that exact
    bug shipped (harden-pass finding, REVIEW.md #27).  TestClient reproduces
    the thread hop, so this test fails if the fix regresses.
    """
    app = create_app(db_path=str(tmp_path / "serve.db"))
    with TestClient(app) as client:
        body = _import(client)
        playlist_id = body["playlist"]["id"]

        listed = client.get("/playlists")
        assert listed.status_code == 200, listed.text
        assert [p["id"] for p in listed.json()] == [playlist_id]

        run = client.post(f"/playlists/{playlist_id}/reorder", json={"seed": 7})
        assert run.status_code == 201, run.text
        run_id = run.json()["run"]["id"]

        exported = client.get(f"/runs/{run_id}/export", params={"format": "m3u"})
        assert exported.status_code == 200
        assert exported.text.startswith("#EXTM3U")
