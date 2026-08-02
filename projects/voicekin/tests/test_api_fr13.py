"""FR-13: the FastAPI automation surface, driven through TestClient.

The app wraps a real service over ``SQLiteRepository(":memory:")`` — the same
wiring production uses, minus the disk. Enrollment and consent still happen
through the service (the API has no upload surface by design, decision 16).
"""

from __future__ import annotations

import pytest
from conftest import ALICE, BOB, enroll, grant
from fastapi.testclient import TestClient
from voicekin.api import create_app
from voicekin.engine.audio import payload_sha256
from voicekin.models import TargetKind

T0 = "2026-07-31T10:00:00Z"
T1 = "2026-07-31T11:00:00Z"
T2 = "2026-07-31T12:00:00Z"


@pytest.fixture
def client(initialized):
    return TestClient(create_app(initialized), raise_server_exceptions=True)


@pytest.fixture
def granted(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path, display_name="Amina")
    grant(initialized, "partner", ALICE, T1, tmp_path)
    return initialized


def _synthesize(client, **overrides):
    body = {
        "profile_id": "partner",
        "text": "Dinner is ready",
        "context": "announcement",
        "seed": 7,
        "now": T2,
    }
    body.update(overrides)
    return client.post("/synthesize", json=body)


def test_fr13_health_reports_the_instance(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "initialized": True,
        "operator_name": "Abdoul",
    }


def test_fr13_profiles_are_listed_without_biometric_vectors(client, granted):
    response = client.get("/profiles", params={"now": T2})
    assert response.status_code == 200
    [profile] = response.json()
    assert profile["id"] == "partner"
    assert profile["consent_status"] == "verified"
    assert profile["enrollment_fingerprint"]
    # Voiceprints never cross the socket (schemas docstring).
    assert "centroid" not in profile and "voice_params" not in profile

    detail = client.get("/profiles/partner", params={"now": T2})
    assert detail.status_code == 200
    assert detail.json()["display_name"] == "Amina"


def test_fr13_unknown_profile_is_404(client):
    response = client.get("/profiles/nobody")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_fr13_consents_are_listed(client, granted):
    response = client.get("/profiles/partner/consents")
    assert response.status_code == 200
    [consent] = response.json()
    assert consent["status"] == "verified"
    assert consent["similarity"] is not None
    assert "I, Amina," in consent["statement_text"]


def test_fr13_synthesize_renders_and_serves_audio(client, granted):
    response = _synthesize(client)
    assert response.status_code == 200
    utterance = response.json()
    assert utterance["status"] == "rendered"
    assert utterance["text"] == "dinner is ready"

    audio = client.get(f"/utterances/{utterance['id']}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert payload_sha256(audio.content) == utterance["output_sha256"]

    fetched = client.get(f"/utterances/{utterance['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == utterance


def test_fr13_gate_refusal_maps_to_403_with_the_reason(client, initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    response = _synthesize(client)
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "refused"
    assert body["reason"] == "no_consent"
    # The refusal was persisted as an utterance row (FR-6).
    fetched = client.get(f"/utterances/{body['utterance_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "refused"


def test_fr13_revoke_by_consent_id_and_replay_refusal(client, granted, tmp_path):
    rendered = _synthesize(client).json()
    [consent] = client.get("/profiles/partner/consents").json()

    revoked = client.post(
        f"/consents/{consent['id']}/revoke",
        json={"reason": "changed my mind", "now": "2026-07-31T13:00:00Z"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    # Delivery of the already-rendered utterance re-authorizes and is refused.
    granted.add_target(
        "sink", TargetKind.FILE_SINK, {"dir": str(tmp_path / "sink")}, "2026-07-31T13:01:00Z"
    )
    delivery = client.post(
        f"/utterances/{rendered['id']}/deliver",
        json={"target_id": "sink", "now": "2026-07-31T13:02:00Z"},
    )
    assert delivery.status_code == 403
    assert delivery.json()["reason"] == "consent_revoked"

    again = _synthesize(client, now="2026-07-31T13:03:00Z")
    assert again.status_code == 403
    assert again.json()["reason"] == "consent_revoked"


def test_fr13_revoking_a_non_governing_consent_is_409(client, granted, tmp_path):
    # Drift the enrollment, re-grant: the old consent is no longer governing.
    enroll(granted, "partner", ALICE, "2026-07-31T13:00:00Z", tmp_path, seed=4200)
    consents = client.get("/profiles/partner/consents").json()
    grant(granted, "partner", ALICE, "2026-07-31T14:00:00Z", tmp_path,
          nonce_seed=90210, seed=7801)
    old_id = consents[0]["id"]
    response = client.post(f"/consents/{old_id}/revoke", json={"now": "2026-07-31T15:00:00Z"})
    assert response.status_code == 409
    assert response.json()["error"] == "precondition_failed"


def test_fr13_deliver_succeeds_and_returns_the_receipt(client, granted, tmp_path):
    rendered = _synthesize(client).json()
    granted.add_target("sink", TargetKind.FILE_SINK, {"dir": str(tmp_path / "sink")}, T2)
    response = client.post(
        f"/utterances/{rendered['id']}/deliver", json={"target_id": "sink", "now": T2}
    )
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["status"] == "succeeded"
    assert receipt["detail"]["path"].endswith(f"{rendered['id']}.wav")


def test_fr13_verify_output_resolves_and_rejects_unknown(client, granted):
    rendered = _synthesize(client).json()
    response = client.post(
        "/verify-output", json={"output_sha256": rendered["output_sha256"]}
    )
    assert response.status_code == 200
    provenance = response.json()
    assert provenance["utterance"]["id"] == rendered["id"]
    assert provenance["profile_id"] == "partner"
    assert {r["event"] for r in provenance["audit_records"]} == {
        "synthesis_authorized",
        "utterance_rendered",
    }

    unknown = client.post("/verify-output", json={"output_sha256": "0" * 64})
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "unknown_output"


def test_fr13_audit_listing_and_verification(client, granted):
    _synthesize(client)
    records = client.get("/audit").json()
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    since = client.get("/audit", params={"since_seq": records[-1]["seq"] - 1}).json()
    assert len(since) == 1

    verify = client.get("/audit/verify").json()
    assert verify["ok"] is True
    assert verify["head_seq"] == len(records)
    assert verify["head_hash"] == records[-1]["record_hash"]


def test_fr13_refused_utterance_audio_is_404(client, initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    refused = _synthesize(client)
    utterance_id = refused.json()["utterance_id"]
    response = client.get(f"/utterances/{utterance_id}/audio")
    assert response.status_code == 404
    assert response.json()["error"] == "audio_unavailable"


def test_fr13_impostor_consent_never_activates_the_voice(client, initialized, tmp_path):
    """US-3 through the API: after an impostor grant attempt, synthesis is
    refused with consent_rejected."""
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", BOB, T1, tmp_path)  # impostor recording
    response = _synthesize(client)
    assert response.status_code == 403
    assert response.json()["reason"] == "consent_rejected"


def test_fr13_validation_errors_are_422(client):
    response = client.post("/synthesize", json={"profile_id": "partner"})
    assert response.status_code == 422
    bad_hash = client.post("/verify-output", json={"output_sha256": "nope"})
    assert bad_hash.status_code == 422
