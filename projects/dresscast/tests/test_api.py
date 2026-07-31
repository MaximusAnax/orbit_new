"""FR-17: the REST surface, its status codes and its error catalog."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dresscast.api import create_app
from dresscast.services import Config, DresscastService
from dresscast.store.memory import InMemoryRepository
from fastapi.testclient import TestClient

from conftest import diurnal, make_forecast, small_wardrobe

NOW = datetime(2026, 4, 14, 6, 30, tzinfo=timezone.utc)
DATE = "2026-04-14"


def _write_weather(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for date, forecast in (
        (DATE, make_forecast(date=DATE, temps=diurnal(5.0, 18.0), wind=10.0, humidity=55.0, uv=3.0)),
        (
            "2026-07-28",
            make_forecast(
                date="2026-07-28",
                temps=diurnal(22.0, 30.0),
                wind=12.0,
                humidity=70.0,
                precip_prob=[0.7 if 14 <= h < 18 else 0.05 for h in range(24)],
                precip_mmh=[12.0 if 14 <= h < 18 else 0.0 for h in range(24)],
                uv=7.0,
            ),
        ),
    ):
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
def client(tmp_path: Path) -> TestClient:
    weather = tmp_path / "weather"
    _write_weather(weather)
    repo = InMemoryRepository()
    service = DresscastService(repo, Config(data_dir=tmp_path, fixture_dir=weather))
    return TestClient(create_app(service=service, clock=lambda: NOW))


def _stock(client: TestClient) -> None:
    """Load the shared synthetic wardrobe through the public API."""
    for garment in small_wardrobe():
        body = {
            "name": garment.name,
            "category": garment.category,
            "colors": [c.model_dump(exclude_none=True) for c in garment.colors],
            "occasions": garment.occasions,
            "style_tags": garment.style_tags,
            "clo": garment.clo if garment.layer_role != "accessory" else None,
            "layer_role": garment.layer_role,
            "accessory_class": garment.accessory_class,
            "formality": garment.formality,
            "waterproofness": garment.waterproofness,
            "windproofness": garment.windproofness,
        }
        body = {k: v for k, v in body.items() if v is not None}
        response = client.post("/garments", json=body)
        assert response.status_code == 201, response.text


def test_fr17_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_version"] >= 1


def test_fr1_create_applies_the_category_preset(client: TestClient) -> None:
    response = client.post(
        "/garments",
        json={
            "name": "navy-wool-coat",
            "category": "wool_coat",
            "colors": ["navy"],
            "occasions": ["work", "casual"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["clo"] == pytest.approx(0.60)
    assert body["layer_role"] == "outer"
    assert body["formality"] == 4
    assert body["wears_before_laundry"] == 30
    assert body["overridden_fields"] == []


def test_fr1_explicit_clo_is_recorded_as_an_override(client: TestClient) -> None:
    response = client.post(
        "/garments",
        json={
            "name": "light-wool-coat",
            "category": "wool_coat",
            "colors": ["gray"],
            "occasions": ["work"],
            "clo": 0.5,
        },
    )
    assert response.status_code == 201
    assert response.json()["overridden_fields"] == ["clo"]


def test_fr1_invalid_params_carries_the_error_catalog_code(client: TestClient) -> None:
    response = client.post(
        "/garments",
        json={"name": "x", "category": "not-a-category", "colors": ["navy"], "occasions": ["work"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_params"


def test_fr1_clo_outside_the_preset_band_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/garments",
        json={
            "name": "impossible-tee",
            "category": "tshirt",
            "colors": ["white"],
            "occasions": ["casual"],
            "clo": 0.9,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_params"


def test_fr17_unknown_garment_is_404(client: TestClient) -> None:
    response = client.get("/garments/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_garment"


def test_fr3_status_transitions_and_invalid_transition_is_409(client: TestClient) -> None:
    created = client.post(
        "/garments",
        json={"name": "tee", "category": "tshirt", "colors": ["white"], "occasions": ["casual"]},
    ).json()
    gid = created["id"]
    assert client.patch(f"/garments/{gid}", json={"status": "dirty"}).json()["status"] == "dirty"
    assert client.patch(f"/garments/{gid}", json={"status": "retired"}).status_code == 200
    clash = client.patch(f"/garments/{gid}", json={"status": "clean"})
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "invalid_transition"


def test_fr1_list_filters(client: TestClient) -> None:
    _stock(client)
    everything = client.get("/garments").json()
    assert len(everything) == 26
    work = client.get("/garments", params={"occasion": "work"}).json()
    assert work and all("work" in g["occasions"] for g in work)
    tees = client.get("/garments", params={"category": "tshirt"}).json()
    assert [g["category"] for g in tees] == ["tshirt"]


def test_fr16_brief_works_on_an_empty_database(client: TestClient) -> None:
    response = client.get("/brief", params={"date": DATE})
    assert response.status_code == 200
    brief = response.json()
    assert len(brief["hours"]) == 15
    assert brief["required_clo_max"] > brief["required_clo_min"]
    assert brief["archetype_range"]
    assert all(h["archetype"] for h in brief["hours"])


def test_fr16_brief_honours_met_and_window(client: TestClient) -> None:
    default = client.get("/brief", params={"date": DATE}).json()
    warm = client.get("/brief", params={"date": DATE, "met": 2.2}).json()
    assert warm["required_clo_max"] < default["required_clo_max"]
    late = client.get("/brief", params={"date": DATE, "window": "17:00-22:00"}).json()
    assert [h["hour"] for h in late["hours"]] == [17, 18, 19, 20, 21]


def test_fr4_forecast_snapshot_is_persisted_and_reused(client: TestClient) -> None:
    first = client.get("/forecast", params={"date": DATE})
    assert first.status_code == 200
    assert len(first.json()["hours"]) == 24
    second = client.get("/forecast", params={"date": DATE})
    assert second.json()["id"] == first.json()["id"]


def test_fr4_missing_forecast_is_503(client: TestClient) -> None:
    response = client.get("/forecast", params={"date": "1999-01-01"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "forecast_unavailable"


def test_fr8_recommendation_round_trip(client: TestClient) -> None:
    _stock(client)
    response = client.post("/recommendations", json={"date": DATE, "occasion": "work", "k": 3})
    assert response.status_code == 201
    rec = response.json()
    assert [o["rank"] for o in rec["outfits"]] == [1, 2, 3]
    assert rec["outfits"][0]["score_total"] >= rec["outfits"][1]["score_total"]
    assert rec["outfits"][0]["reasoning"]
    assert len(rec["outfits"][0]["hour_plan"]) == 15
    fetched = client.get(f"/recommendations/{rec['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["wardrobe_hash"] == rec["wardrobe_hash"]


def test_fr8_unknown_occasion_is_invalid_params(client: TestClient) -> None:
    _stock(client)
    response = client.post("/recommendations", json={"date": DATE, "occasion": "gala"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_params"


def test_fr14_infeasible_wardrobe_is_422_and_carries_the_brief(client: TestClient) -> None:
    client.post(
        "/garments",
        json={"name": "tee", "category": "tshirt", "colors": ["white"], "occasions": ["work"]},
    )
    response = client.post("/recommendations", json={"date": DATE, "occasion": "work"})
    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "infeasible_wardrobe"
    assert payload["detail"]["missing"]
    assert payload["detail"]["brief"]["hours"]


def test_fr12_wear_from_a_recommendation_then_fr3_laundry(client: TestClient) -> None:
    _stock(client)
    rec = client.post("/recommendations", json={"date": DATE, "occasion": "work"}).json()
    worn = client.post(f"/recommendations/{rec['id']}/wear", json={"rank": 1})
    assert worn.status_code == 201
    log = worn.json()
    assert log["source"] == "recommendation"
    assert log["items"]
    first_id = log["items"][0]["garment_id"]
    before = client.get(f"/garments/{first_id}").json()
    assert before["wears_since_wash"] == 1

    undo = client.delete(f"/wear/{log['id']}")
    assert undo.status_code == 204
    assert client.get(f"/garments/{first_id}").json()["wears_since_wash"] == 0


def test_fr12_manual_wear_and_fr3_laundry_reset(client: TestClient) -> None:
    _stock(client)
    tee = next(g for g in client.get("/garments").json() if g["name"] == "white-tee")
    for _ in range(tee["wears_before_laundry"]):
        assert (
            client.post("/wear", json={"garment_ids": [tee["id"]], "date": DATE}).status_code == 201
        )
    dirty = client.get(f"/garments/{tee['id']}").json()
    assert dirty["status"] == "dirty"

    event = client.post("/laundry", json={"all_dirty": True})
    assert event.status_code == 201
    assert tee["id"] in event.json()["garment_ids"]
    clean = client.get(f"/garments/{tee['id']}").json()
    assert clean["status"] == "clean" and clean["wears_since_wash"] == 0


def test_fr3_laundering_a_clean_garment_is_409(client: TestClient) -> None:
    _stock(client)
    tee = next(g for g in client.get("/garments").json() if g["name"] == "white-tee")
    response = client.post("/laundry", json={"garment_ids": [tee["id"]]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


def test_fr2_suggest_without_an_extractor_is_501(client: TestClient, tmp_path: Path) -> None:
    created = client.post(
        "/garments",
        json={"name": "tee", "category": "tshirt", "colors": ["white"], "occasions": ["casual"]},
    ).json()
    photo = tmp_path / "tee.jpg"
    photo.write_bytes(b"not-a-real-photo")
    attached = client.post(f"/garments/{created['id']}/photo", json={"path": str(photo)})
    assert attached.status_code == 200
    assert attached.json()["photo_sha256"]

    response = client.post(f"/garments/{created['id']}/suggest")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "no_extractor_configured"


def test_fr2_suggest_accept_applies_the_cascade(tmp_path: Path) -> None:
    from dresscast.adapters.extractor import FixtureAttributeExtractor, photo_sha256

    weather = tmp_path / "weather"
    _write_weather(weather)
    photo = tmp_path / "fleece.jpg"
    photo.write_bytes(b"fixture")
    service = DresscastService(
        InMemoryRepository(),
        Config(data_dir=tmp_path, fixture_dir=weather),
        extractor=FixtureAttributeExtractor(
            {
                photo_sha256(photo): {
                    "fields": {"category": "wool_coat"},
                    "confidences": {"category": 0.91},
                }
            }
        ),
    )
    client = TestClient(create_app(service=service, clock=lambda: NOW))
    created = client.post(
        "/garments",
        json={"name": "fleece", "category": "fleece", "colors": ["olive"], "occasions": ["casual"]},
    ).json()
    client.post(f"/garments/{created['id']}/photo", json={"path": str(photo)})

    suggested = client.post(f"/garments/{created['id']}/suggest")
    assert suggested.status_code == 201
    suggestion = suggested.json()
    assert suggestion["status"] == "pending"
    assert suggestion["payload"]["category"]["value"] == "wool_coat"

    accepted = client.post(
        f"/suggestions/{suggestion['id']}/accept", json={"fields": ["category"]}
    )
    assert accepted.status_code == 200
    garment = accepted.json()
    assert garment["category"] == "wool_coat"
    assert garment["clo"] == pytest.approx(0.60)
    assert garment["layer_role"] == "outer"

    listed = client.get(f"/garments/{created['id']}/suggestions").json()
    assert listed[0]["status"] == "accepted"
    vias = {entry["via"] for entry in listed[0]["accepted_fields"]}
    assert vias == {"explicit", "cascade"}


def test_fr2_reject_leaves_the_garment_untouched(tmp_path: Path) -> None:
    from dresscast.adapters.extractor import FixtureAttributeExtractor, photo_sha256

    weather = tmp_path / "weather"
    _write_weather(weather)
    photo = tmp_path / "f.jpg"
    photo.write_bytes(b"a different fixture photo")
    service = DresscastService(
        InMemoryRepository(),
        Config(data_dir=tmp_path, fixture_dir=weather),
        extractor=FixtureAttributeExtractor({photo_sha256(photo): {"fields": {"formality": 5}}}),
    )
    client = TestClient(create_app(service=service, clock=lambda: NOW))
    created = client.post(
        "/garments",
        json={"name": "fleece", "category": "fleece", "colors": ["olive"], "occasions": ["casual"]},
    ).json()
    client.post(f"/garments/{created['id']}/photo", json={"path": str(photo)})
    suggestion = client.post(f"/garments/{created['id']}/suggest").json()
    rejected = client.post(f"/suggestions/{suggestion['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.get(f"/garments/{created['id']}").json()["formality"] == 2


def test_fr19_two_identical_requests_produce_identical_outfits(client: TestClient) -> None:
    _stock(client)
    a = client.post("/recommendations", json={"date": DATE, "occasion": "work"}).json()
    b = client.post("/recommendations", json={"date": DATE, "occasion": "work"}).json()
    strip = lambda rec: [  # noqa: E731
        (o["rank"], o["score_total"], sorted(i["garment_id"] for i in o["items"]))
        for o in rec["outfits"]
    ]
    assert strip(a) == strip(b)
    assert a["wardrobe_hash"] == b["wardrobe_hash"]


def test_fr8_openapi_documents_the_endpoint_set(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for expected in (
        "/health",
        "/garments",
        "/garments/{garment_id}",
        "/garments/{garment_id}/photo",
        "/garments/{garment_id}/suggest",
        "/suggestions/{suggestion_id}/accept",
        "/suggestions/{suggestion_id}/reject",
        "/forecast",
        "/brief",
        "/recommendations",
        "/recommendations/{recommendation_id}",
        "/recommendations/{recommendation_id}/wear",
        "/wear",
        "/laundry",
    ):
        assert expected in paths, expected
