"""FR-15: the FastAPI edge — routing, status codes and the error catalog."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.models import EntryStatus

MARCUS = "You have power over your mind - not outside events."
DUD = "Insanity is doing the same thing over and over again and expecting different results."


def create(client, text=MARCUS, **kwargs):
    body = {"text": text, **kwargs}
    response = client.post("/entries", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_fr15_health_reports_versions_and_config(api_client):
    body = api_client.get("/health").json()
    assert body["status"] == "ok"
    assert body["scheduler_version"] == "sched-1"
    assert body["seed"] == 7
    assert body["batch_k"] == 1


def test_fr15_create_entry_returns_201_with_suggestions_and_flags(api_client):
    body = create(
        api_client,
        text=DUD,
        author="Albert Einstein",
        tags=["Mental Models"],
        note="I keep repeating this pattern with the weekly report.",
    )
    assert body["entry"]["normalized_hash"]
    assert body["tags"] == ["mental-models"]
    assert [f["verdict"] for f in body["attribution_flags"]] == ["misattributed"]
    assert body["attribution_flags"][0]["reference_url"].startswith("https://")


def test_fr15_duplicate_capture_is_a_warning_not_a_block(api_client):
    first = create(api_client)
    second = create(api_client, text="you have power over your MIND — not outside events!!")
    assert second["duplicate_of"] == first["entry"]["id"]


def test_fr15_create_entry_with_unknown_theme_is_422(api_client):
    response = api_client.post("/entries", json={"text": "x", "themes": ["nope"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


def test_fr15_malformed_body_is_422_validation_error(api_client):
    response = api_client.post("/entries", json={"text": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_fr15_unknown_entry_is_404(api_client):
    response = api_client.get("/entries/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_fr15_list_entries_filters_compose(api_client):
    quote = create(api_client, tags=["stoicism"], themes=["equanimity_and_anger"])
    create(api_client, text="Write the dreaded email first.", kind="idea")
    api_client.post(f"/entries/{quote['entry']['id']}/pin")

    assert api_client.get("/entries", params={"kind": "idea"}).json()["total"] == 1
    assert api_client.get("/entries", params={"pinned": True}).json()["total"] == 1
    assert api_client.get("/entries", params={"theme": "equanimity_and_anger"}).json()["total"] == 1
    assert (
        api_client.get("/entries", params={"tag": "stoicism", "pinned": False}).json()["total"] == 0
    )
    assert api_client.get("/entries", params={"q": "dreaded"}).json()["total"] == 1


def test_fr15_patch_rejects_semantic_text_edit_after_surfacing(api_client):
    entry_id = create(api_client)["entry"]["id"]
    api_client.post("/today", json={"date": "2026-01-01"})

    ok = api_client.patch(f"/entries/{entry_id}", json={"text": MARCUS.replace("-", "—")})
    assert ok.status_code == 200

    bad = api_client.patch(f"/entries/{entry_id}", json={"text": "Something else entirely."})
    assert bad.status_code == 422
    assert "archive" in bad.json()["error"]["message"]


def test_fr15_pin_returns_warning_past_the_budget(api_client):
    warnings = []
    for index in range(9):
        entry_id = create(api_client, text=f"Entry number {index} about courage.")["entry"]["id"]
        warnings.append(api_client.post(f"/entries/{entry_id}/pin").json()["warning"])
    assert warnings[:8] == [None] * 8
    assert "degrades from 45" in warnings[8]


def test_fr15_archive_and_restore_round_trip(api_client):
    entry_id = create(api_client)["entry"]["id"]
    assert api_client.post(f"/entries/{entry_id}/archive").json()["status"] == "archived"
    assert api_client.get("/entries").json()["total"] == 0
    assert api_client.get("/entries", params={"status": "archived"}).json()["total"] == 1
    assert api_client.post(f"/entries/{entry_id}/restore").json()["status"] == EntryStatus.ACTIVE


def test_fr15_today_is_idempotent_and_byte_identical(api_client):
    create(api_client)
    first = api_client.post("/today", json={"date": "2026-01-01"})
    second = api_client.post("/today", json={"date": "2026-01-01"})
    assert first.json()["materialized_now"] is True
    assert second.json()["materialized_now"] is False
    assert first.json()["cards"] == second.json()["cards"]


def test_fr15_today_in_the_future_is_422(api_client):
    create(api_client)
    response = api_client.post("/today", json={"date": "2030-01-01"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


def test_fr15_get_today_before_materialization_is_404(api_client):
    response = api_client.get("/today", params={"date": "2026-01-01"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_materialized"


def test_fr15_backdated_daily_materialization_is_422_out_of_order(api_client, clock):
    create(api_client)
    clock.set(dt.date(2026, 3, 1))
    api_client.post("/today", json={"date": "2026-03-01"})
    response = api_client.post("/today", json={"date": "2026-02-01"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "out_of_order"


def test_fr15_draw_without_candidates_is_409(api_client):
    response = api_client.post("/draws", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_candidate"


def test_fr15_draw_is_an_extra_surfacing(api_client):
    create(api_client, themes=["equanimity_and_anger"])
    card = api_client.post("/draws", json={"theme": "equanimity_and_anger"}).json()
    assert card["surfacing"]["kind"] == "extra"
    assert card["surfacing"]["select_pool"] == "extra"
    assert card["surfacing"]["filter_theme_id"] == "equanimity_and_anger"


def test_fr15_reflection_is_created_once_then_409(api_client):
    create(api_client)
    card = api_client.post("/today", json={"date": "2026-01-01"}).json()["cards"][0]
    surfacing_id = card["surfacing"]["id"]

    first = api_client.post(
        f"/surfacings/{surfacing_id}/reflection", json={"grade": "applied", "text": "did it"}
    )
    assert first.status_code == 201

    second = api_client.post(f"/surfacings/{surfacing_id}/reflection", json={"grade": "flat"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_fr15_reflection_on_unknown_surfacing_is_404(api_client):
    response = api_client.post("/surfacings/nope/reflection", json={"grade": "flat"})
    assert response.status_code == 404


def test_fr15_history_endpoints_expose_the_journal(api_client):
    entry_id = create(api_client)["entry"]["id"]
    card = api_client.post("/today", json={"date": "2026-01-01"}).json()["cards"][0]
    api_client.post(
        f"/surfacings/{card['surfacing']['id']}/reflection",
        json={"grade": "resonated", "text": "named the feeling"},
    )
    detail = api_client.get(f"/entries/{entry_id}").json()
    assert detail["state"]["exposure_count"] == 1
    assert len(detail["surfacings"]) == 1
    assert detail["reflections"][0]["grade"] == "resonated"
    assert api_client.get("/surfacings", params={"entry_id": entry_id}).json()
    assert api_client.get("/reflections", params={"entry_id": entry_id}).json()
    assert api_client.get(f"/surfacings/{card['surfacing']['id']}").status_code == 200


def test_fr15_taxonomy_endpoints(api_client):
    themes = api_client.get("/themes").json()
    assert len(themes) == 16
    templates = api_client.get("/themes/mortality_and_time/templates").json()
    assert len(templates) >= 6
    assert api_client.get("/themes/general/templates").json()
    assert api_client.get("/themes/nope/templates").status_code == 404
    suggested = api_client.post(
        "/themes/suggest", json={"text": "Remember that you will die; time is the only currency."}
    ).json()["suggestions"]
    assert suggested[0]["theme_id"] == "mortality_and_time"


def test_fr15_collections_crud_and_membership(api_client):
    entry_id = create(api_client)["entry"]["id"]
    created = api_client.post("/collections", json={"name": "Mornings"})
    assert created.status_code == 201
    collection_id = created.json()["id"]

    assert api_client.post("/collections", json={"name": "Mornings"}).status_code == 409
    view = api_client.put(f"/collections/{collection_id}/entries/{entry_id}").json()
    assert view["entry_ids"] == [entry_id]
    assert (
        api_client.patch(f"/collections/{collection_id}", json={"description": "am"}).json()[
            "description"
        ]
        == "am"
    )
    assert (
        api_client.delete(f"/collections/{collection_id}/entries/{entry_id}").json()["entry_ids"]
        == []
    )
    assert api_client.delete(f"/collections/{collection_id}").status_code == 204
    assert api_client.get(f"/collections/{collection_id}").status_code == 404


def test_fr15_import_and_export_round_trip(api_client):
    csv_payload = "text,author,source,url,tags,note\nA short saying.,Anon,Somewhere,,a;b,my note\n"
    report = api_client.post("/entries/import", json={"format": "csv", "payload": csv_payload})
    assert report.status_code == 200
    assert report.json()["created"] == 1
    assert report.json()["drain_horizon_days"] == 91

    exported = api_client.get("/entries/export").json()
    assert exported["format"] == "almanac-export"
    assert len(exported["entries"]) == 1

    again = api_client.post("/entries/import", json={"format": "json", "payload": str(exported)})
    assert again.status_code == 422  # a python repr is not JSON


def test_fr15_import_starter_pack(api_client):
    report = api_client.post("/entries/import", json={"format": "starter"}).json()
    assert report["created"] >= 50
    assert report["duplicates"] == 0
    repeat = api_client.post("/entries/import", json={"format": "starter"}).json()
    assert repeat["created"] == 0 and repeat["duplicates"] >= 50


def test_fr15_stats_carries_the_capacity_block(api_client):
    create(api_client)
    body = api_client.get("/stats", params={"date": "2026-01-01"}).json()
    assert body["rho"] == 0.35
    assert body["capacity"]["k"] == 1
    assert "review_demand" in body["capacity"]


def test_fr15_attribution_check_endpoint(api_client):
    findings = api_client.get(
        "/attribution/check", params={"text": DUD, "author": "Albert Einstein"}
    ).json()
    assert findings and findings[0]["verdict"] == "misattributed"
    assert api_client.get("/attribution/check", params={"text": "no match here"}).json() == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/entries/nope/pin"),
        ("delete", "/entries/nope/pin"),
        ("post", "/entries/nope/archive"),
        ("post", "/entries/nope/restore"),
        ("patch", "/entries/nope"),
    ],
)
def test_fr15_unknown_entry_paths_all_404(api_client, method, path):
    response = api_client.request(method.upper(), path, json={} if method != "delete" else None)
    assert response.status_code == 404
