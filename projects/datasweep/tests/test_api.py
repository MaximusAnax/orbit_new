"""FastAPI surface (SCOPE.md FR-14) via TestClient.

The app is thin, so these tests check the contract rather than the behaviour:
status codes, the structured error catalog, and that every endpoint sketched in
SCOPE.md §Architecture-API is wired to the right service call.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from datasweep.api.app import create_app
from datasweep.services import DatasweepService
from fastapi.testclient import TestClient
from support_datasweep import SAMPLE_ROWS, service_for, write_sample

from datasweep import __version__


@pytest.fixture
def service(tmp_path: Path) -> DatasweepService:
    return service_for(tmp_path)


@pytest.fixture
def client(service: DatasweepService) -> Iterator[TestClient]:
    with TestClient(create_app(lambda: service)) as test_client:
        yield test_client


@pytest.fixture
def run_id(client: TestClient, tmp_path: Path) -> str:
    source = write_sample(tmp_path)
    response = client.post("/clean", json={"path": source, "out": str(tmp_path / "out")})
    assert response.status_code == 201
    return response.json()["id"]


def test_fr14_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_fr14_folder_crud(client: TestClient, tmp_path: Path) -> None:
    watched = tmp_path / "drop"
    watched.mkdir()
    created = client.post("/folders", json={"path": str(watched), "recursive": False})
    assert created.status_code == 201
    body = created.json()
    assert body["path"] == str(watched.resolve())
    assert body["recursive"] is False
    assert body["include"] == ["*.csv", "*.tsv", "*.xlsx", "*.jsonl"]

    assert [folder["id"] for folder in client.get("/folders").json()] == [body["id"]]
    assert client.delete(f"/folders/{body['id']}").status_code == 204
    assert client.get("/folders").json() == []
    assert client.delete(f"/folders/{body['id']}").status_code == 404


def test_fr14_clean_creates_a_run_and_artifacts(client: TestClient, tmp_path: Path) -> None:
    source = write_sample(tmp_path)
    response = client.post("/clean", json={"path": source, "out": str(tmp_path / "out")})
    assert response.status_code == 201
    run = response.json()
    assert run["status"] in {"succeeded", "review_pending"}
    assert run["format"] == "csv"
    assert run["n_rows"] == SAMPLE_ROWS
    assert Path(run["artifact_dir"], "report.md").is_file()


def test_fr14_run_detail_and_artifact_downloads(client: TestClient, run_id: str) -> None:
    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["run"]["id"] == run_id
    assert len(payload["columns"]) == 4
    assert payload["revisions"][0]["revision_no"] == 1

    audit = client.get(f"/runs/{run_id}/audit")
    assert audit.status_code == 200
    first = json.loads(audit.text.splitlines()[0])
    assert first["kind"] == "header" and "run_id" not in first

    findings = client.get(f"/runs/{run_id}/findings")
    assert findings.status_code == 200
    assert all(json.loads(line)["klass"] for line in findings.text.splitlines())

    report = client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.text.startswith("# datasweep report — sales.csv")


def test_fr14_list_runs_filters(client: TestClient, run_id: str, tmp_path: Path) -> None:
    listed = client.get("/runs").json()
    assert [item["run_id"] for item in listed] == [run_id]
    assert listed[0]["source_name"] == "sales.csv"

    by_path = client.get("/runs", params={"path": str(tmp_path / "sales.csv")}).json()
    assert len(by_path) == 1
    assert client.get("/runs", params={"status": "failed"}).json() == []
    assert client.get("/runs", params={"path": "/nowhere/x.csv"}).json() == []


def test_fr14_review_decisions_and_revert(client: TestClient, run_id: str) -> None:
    items = client.get(f"/runs/{run_id}/review").json()
    assert items and items[0]["status"] == "pending"

    created = client.post(f"/runs/{run_id}/decisions", json={"accept": [items[0]["id"]]})
    assert created.status_code == 201
    assert created.json()["revision_no"] == 2

    check = client.post(f"/runs/{run_id}/revert")
    assert check.status_code == 200
    assert check.json() == {
        "run_id": run_id,
        "revision": 2,
        "match": True,
        "mismatches": [],
    }


def test_fr14_scan_and_profile(client: TestClient, tmp_path: Path) -> None:
    watched = tmp_path / "drop"
    watched.mkdir()
    source = write_sample(watched)
    client.post("/folders", json={"path": str(watched)})

    scan = client.post("/scan", json={})
    assert scan.status_code == 200
    assert set(scan.json()) == {"processed", "skipped", "deferred", "failed"}

    profile = client.post("/profile", json={"path": source})
    assert profile.status_code == 200
    body = profile.json()
    assert [column["name"] for column in body["columns"]] == [
        "name",
        "country",
        "amount",
        "signup",
    ]


# -- the error catalog (SCOPE.md FR-14) -----------------------------------


def test_fr14_unknown_run_maps_to_404(client: TestClient) -> None:
    response = client.get("/runs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_run"


def test_fr14_unknown_item_maps_to_404(client: TestClient, run_id: str) -> None:
    response = client.post(f"/runs/{run_id}/decisions", json={"accept": ["00000000"]})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_item"


def test_fr14_item_already_decided_maps_to_409(client: TestClient, run_id: str) -> None:
    item = client.get(f"/runs/{run_id}/review").json()[0]["id"]
    client.post(f"/runs/{run_id}/decisions", json={"accept": [item]})
    response = client.post(f"/runs/{run_id}/decisions", json={"reject": [item]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "item_already_decided"


def test_fr14_unsupported_format_maps_to_415(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    path.write_bytes(b"PAR1")
    response = client.post("/clean", json={"path": str(path)})
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_format"


def test_fr14_file_too_large_maps_to_413(client: TestClient, tmp_path: Path) -> None:
    policy = tmp_path / "tiny.toml"
    policy.write_text("[general]\nmax_rows = 2\n", encoding="utf-8")
    source = write_sample(tmp_path)
    response = client.post("/clean", json={"path": source, "policy_path": str(policy)})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"


def test_fr14_unknown_body_field_is_a_422(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/clean", json={"path": "x.csv", "nonsense": 1})
    assert response.status_code == 422
