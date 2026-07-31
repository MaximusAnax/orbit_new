"""FR-11 — the FastAPI surface, exercised through ``TestClient``.

The app is built around a service on ``SQLiteRepository(":memory:")`` with the
offline adapters, so these tests are as hermetic as the engine tests: no
network, no wall clock (every timestamp is supplied by the request).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from formcoach.api import create_app
from formcoach.services import FormCoachService
from formcoach.store.sqlite import SQLiteRepository

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "poses"
ACK = "2026-07-01T08:00:00+00:00"
AS_OF = "2026-07-06"

PROFILE_BODY = {
    "goal": "hypertrophy",
    "experience": "intermediate",
    "days_per_week": 4,
    "equipment": ["barbell", "dumbbell", "cable", "machine", "bodyweight"],
    "emphasized_muscles": ["chest"],
    "unit": "kg",
    "updated_at": ACK,
}


@pytest.fixture
def client(datasets):
    repo = SQLiteRepository(":memory:")
    repo.initialize()
    service = FormCoachService(repo, datasets)
    service.initialize()
    with TestClient(create_app(service)) as test_client:
        yield test_client
    repo.close()


@pytest.fixture
def ready(client):
    """A client whose profile exists and has acknowledged the disclaimer."""
    client.put("/profile", json=PROFILE_BODY)
    client.post("/profile/acknowledge-disclaimer", json={"acknowledged_at": ACK})
    return client


def sidecar(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.keypoints.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------ meta


def test_fr11_health_reports_the_loaded_library(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["exercises"] >= 60
    assert body["form_profiles"] == 3
    assert body["has_profile"] is False


# ------------------------------------------------------------------- FR-1 profile


def test_fr11_profile_is_404_until_it_is_created(client):
    response = client.get("/profile")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "profile_not_found"


def test_fr1_put_profile_then_get_round_trips(client):
    created = client.put("/profile", json=PROFILE_BODY)
    assert created.status_code == 200
    assert created.json()["disclaimer_acknowledged_at"] is None
    fetched = client.get("/profile").json()
    assert fetched["goal"] == "hypertrophy"
    assert fetched["emphasized_muscles"] == ["chest"]


def test_fr1_put_profile_rejects_more_than_three_emphasized_muscles(client):
    body = {**PROFILE_BODY, "emphasized_muscles": ["chest", "quads", "lats", "biceps"]}
    assert client.put("/profile", json=body).status_code == 422


def test_fr13a_acknowledgement_is_recorded_with_its_timestamp(client):
    client.put("/profile", json=PROFILE_BODY)
    body = client.post("/profile/acknowledge-disclaimer", json={"acknowledged_at": ACK}).json()
    assert body["disclaimer_acknowledged_at"] == ACK


# ------------------------------------------------------------------- FR-2 library


def test_fr2_exercise_list_filters_compose(client):
    everything = client.get("/exercises").json()
    chest = client.get("/exercises", params={"muscle": "chest"}).json()
    barbell_chest = client.get(
        "/exercises", params={"muscle": "chest", "equipment": "barbell"}
    ).json()
    assert len(everything) > len(chest) >= len(barbell_chest) >= 1
    assert all("chest" in e["primary_muscles"] for e in chest)
    assert all("barbell" in e["equipment"] for e in barbell_chest)


def test_fr2_analyzable_filter_returns_the_three_form_profiles(client):
    rows = client.get("/exercises", params={"analyzable": True}).json()
    assert {e["id"] for e in rows} == {"barbell-back-squat", "barbell-deadlift", "push-up"}
    assert all(e["analyzable"] for e in rows)


def test_fr2_exercise_detail_includes_resolved_media(client):
    body = client.get("/exercises/barbell-back-squat").json()
    assert body["instructions"]
    assert body["cues"]
    assert len(body["media"]) >= 1
    assert all(a["license"] for a in body["media"])


def test_fr11_unknown_exercise_is_404_with_a_code(client):
    response = client.get("/exercises/not-a-lift")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "exercise_not_found"


# ------------------------------------------------------------------ FR-3 programs


def test_fr13a_program_creation_is_blocked_until_the_disclaimer_is_acknowledged(client):
    client.put("/profile", json=PROFILE_BODY)
    response = client.post("/programs", json={"as_of": AS_OF, "seed": 1})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "disclaimer_not_acknowledged"


def test_fr3_program_creation_returns_201_and_five_weeks(ready):
    response = ready.post("/programs", json={"as_of": AS_OF, "seed": 20260731})
    assert response.status_code == 201
    body = response.json()
    assert body["weeks"] == 5
    assert body["split"] == "upper_lower"
    assert len(body["sessions"]) == 5 * 4
    assert body["target_muscles"]


def test_fr3_program_overrides_apply_without_touching_the_stored_profile(ready):
    body = ready.post(
        "/programs", json={"as_of": AS_OF, "seed": 3, "days_per_week": 6, "goal": "strength"}
    ).json()
    assert body["days_per_week"] == 6
    assert body["split"] == "ppl_x2"
    assert ready.get("/profile").json()["days_per_week"] == 4


def test_fr11_session_lookup_and_missing_session(ready):
    program_id = ready.post("/programs", json={"as_of": AS_OF, "seed": 1}).json()["id"]
    ok = ready.get(f"/programs/{program_id}/sessions/1/0")
    assert ok.status_code == 200
    assert ok.json()["prescriptions"]
    missing = ready.get(f"/programs/{program_id}/sessions/1/9")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "session_not_found"


def test_us4_next_session_applies_the_progression_ladder(ready):
    program_id = ready.post("/programs", json={"as_of": AS_OF, "seed": 1}).json()["id"]
    body = ready.get(f"/programs/{program_id}/next").json()
    assert body["week"] == 1
    assert body["day_index"] == 0
    assert body["prescriptions"]
    for entry in body["prescriptions"]:
        assert entry["action"] in {
            "increase_load",
            "decrease_load",
            "hold",
            "deload_recommend",
            "add_set",
            "add_reps",
            "progress_variation",
        }
        assert entry["clause"]
        assert entry["rationale"]


def test_us4_a_logged_session_advances_next_to_the_following_day(ready):
    program_id = ready.post("/programs", json={"as_of": AS_OF, "seed": 1}).json()["id"]
    session = ready.get(f"/programs/{program_id}/sessions/1/0").json()
    ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "program_session_id": session["id"],
            "sets": [
                {
                    "exercise_id": session["prescriptions"][0]["exercise_id"],
                    "weight": 60.0,
                    "reps": 8,
                    "rir": 2.0,
                }
            ],
        },
    )
    assert ready.get(f"/programs/{program_id}/next").json()["day_index"] == 1


def test_us4_program_complete_is_404_once_every_session_is_logged(ready):
    program_id = ready.post(
        "/programs", json={"as_of": AS_OF, "seed": 1, "days_per_week": 2}
    ).json()["id"]
    for week in range(1, 6):
        for day in range(2):
            session = ready.get(f"/programs/{program_id}/sessions/{week}/{day}").json()
            ready.post(
                "/workouts",
                json={
                    "performed_at": f"2026-07-{6 + week:02d}T18:00:00+00:00",
                    "program_session_id": session["id"],
                    "sets": [
                        {
                            "exercise_id": session["prescriptions"][0]["exercise_id"],
                            "weight": 40.0,
                            "reps": 8,
                            "rir": 2.0,
                        }
                    ],
                },
            )
    response = ready.get(f"/programs/{program_id}/next")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "program_complete"


# -------------------------------------------------------------------- FR-5 logs


def test_fr5_workout_is_created_with_derived_e1rm(ready):
    response = ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "sets": [
                {"exercise_id": "barbell-bench-press", "weight": 80.0, "reps": 10, "rir": 2.0},
                {"exercise_id": "barbell-bench-press", "weight": 80.0, "reps": 10},
            ],
        },
    )
    assert response.status_code == 201
    sets = response.json()["workout"]["sets"]
    assert sets[0]["e1rm_kg"] == pytest.approx(80.0 * (1 + 12 / 30))
    assert sets[1]["e1rm_kg"] is None
    assert sets[1]["set_index"] == 1


def test_fr5_pounds_are_converted_to_kilograms_at_the_boundary(ready):
    body = ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "unit": "lb",
            "sets": [{"exercise_id": "barbell-bench-press", "weight": 220.462262, "reps": 5}],
        },
    ).json()
    assert body["workout"]["sets"][0]["weight_kg"] == pytest.approx(100.0, abs=1e-4)


def test_fr5_unknown_exercise_reference_is_rejected(ready):
    response = ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "sets": [{"exercise_id": "made-up", "reps": 5}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reference"


def test_fr13b_pain_flag_returns_stop_and_refer_and_flags_the_profile(ready):
    body = ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "sets": [
                {
                    "exercise_id": "barbell-back-squat",
                    "weight": 100.0,
                    "reps": 5,
                    "rir": 1.0,
                    "pain_flag": True,
                }
            ],
        },
    ).json()
    assert body["pain_flagged"] == ["barbell-back-squat"]
    assert "qualified health professional" in body["recommendation"]
    assert ready.get("/profile").json()["pain_flags"] == ["barbell-back-squat"]


def test_fr13b_flagged_exercise_is_substituted_then_restored_after_clearing(ready):
    program_id = ready.post("/programs", json={"as_of": AS_OF, "seed": 1}).json()["id"]
    first = ready.get(f"/programs/{program_id}/next").json()
    flagged = first["prescriptions"][0]["exercise_id"]
    ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "sets": [{"exercise_id": flagged, "weight": 50.0, "reps": 5, "pain_flag": True}],
        },
    )
    after = ready.get(f"/programs/{program_id}/next").json()
    substituted = [p for p in after["prescriptions"] if p["substituted_for"] == flagged]
    dropped = [d for d in after["dropped"] if d["exercise_id"] == flagged]
    assert substituted or dropped
    if substituted:
        assert substituted[0]["exercise_id"] != flagged
    else:
        assert dropped[0]["reason"] == "pain_flag_no_substitute"

    cleared = ready.delete(
        f"/profile/pain-flags/{flagged}", params={"cleared_at": "2026-07-07T08:00:00+00:00"}
    )
    assert cleared.status_code == 200
    assert cleared.json()["pain_flags"] == []
    restored = ready.get(f"/programs/{program_id}/next").json()
    assert any(p["exercise_id"] == flagged for p in restored["prescriptions"])


def test_fr13b_clearing_an_unflagged_exercise_is_a_400(ready):
    response = ready.delete(
        "/profile/pain-flags/push-up", params={"cleared_at": "2026-07-07T08:00:00+00:00"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reference"


def test_fr11_workout_list_filters_by_since(ready):
    for day in (5, 9):
        ready.post(
            "/workouts",
            json={
                "performed_at": f"2026-07-0{day}T18:00:00+00:00",
                "sets": [{"exercise_id": "push-up", "reps": 12}],
            },
        )
    assert len(ready.get("/workouts").json()) == 2
    assert len(ready.get("/workouts", params={"since": "2026-07-07"}).json()) == 1


# ------------------------------------------------------------------- FR-4 volume


def test_fr4_volume_report_covers_all_fifteen_muscles(ready):
    ready.post("/programs", json={"as_of": AS_OF, "seed": 1})
    ready.post(
        "/workouts",
        json={
            "performed_at": "2026-07-06T18:00:00+00:00",
            "sets": [{"exercise_id": "barbell-back-squat", "weight": 100.0, "reps": 5, "rir": 2.0}]
            * 1,
        },
    )
    body = ready.get("/analytics/volume", params={"iso_week": "2026-W28"}).json()
    assert body["iso_week"] == "2026-W28"
    assert len(body["rows"]) == 15
    quads = next(r for r in body["rows"] if r["muscle"] == "quads")
    assert quads["effective_sets"] == 1.0
    assert quads["is_target"] is True
    assert quads["band"] == "below_mev"


def test_fr11_volume_needs_a_week_or_a_timestamp(ready):
    response = ready.get("/analytics/volume")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reference"


# --------------------------------------------------------- FR-10 / FR-15 form


def test_fr10_clip_analysis_is_created_and_retrievable(ready):
    created = ready.post(
        "/form/analyses",
        json={
            "exercise_id": "barbell-back-squat",
            "created_at": "2026-07-06T19:00:00+00:00",
            "keypoints": sidecar("squat_side_02"),
            "view": "side_left",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "completed"
    assert body["rep_count"] >= 3
    assert body["reps"]
    # complete-matrix invariant: one finding per profile rule per rep
    assert all(len(rep["findings"]) == 4 for rep in body["reps"])
    fetched = ready.get(f"/form/analyses/{body['id']}").json()
    assert fetched["id"] == body["id"]
    assert fetched["rep_count"] == body["rep_count"]


def test_fr10_rejected_clip_persists_with_its_reason(ready):
    body = ready.post(
        "/form/analyses",
        json={
            "exercise_id": "barbell-back-squat",
            "created_at": "2026-07-06T19:00:00+00:00",
            "keypoints": sidecar("invalid_01"),
            "view": "side_left",
        },
    ).json()
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "insufficient_visibility"
    assert body["rep_count"] is None
    assert body["clip_score"] is None
    assert ready.get("/form/analyses").json()[0]["reject_reason"] == "insufficient_visibility"


def test_fr15_photo_analysis_reports_one_rep_and_phase_gating(ready):
    body = ready.post(
        "/form/photo-analyses",
        json={
            "exercise_id": "push-up",
            "created_at": "2026-07-06T19:05:00+00:00",
            "keypoints": sidecar("photo_pushup_bottom_01"),
            "view": "side_right",
            "phase": "bottom",
        },
    ).json()
    assert body["analysis_kind"] == "photo"
    assert body["declared_phase"] == "bottom"
    assert body["fps"] is None
    assert body["rep_count"] == 1
    findings = {f["fault_id"]: f for f in body["reps"][0]["findings"]}
    assert findings["incomplete_lockout"]["not_assessed_reason"] == "phase_not_shown"
    assert findings["insufficient_depth"]["status"] in {"ok", "fault"}


def test_fr11_analysis_list_filters_by_exercise(ready):
    for exercise, name in (
        ("barbell-back-squat", "squat_side_02"),
        ("push-up", "pushup_side_02"),
    ):
        ready.post(
            "/form/analyses",
            json={
                "exercise_id": exercise,
                "created_at": "2026-07-06T19:00:00+00:00",
                "keypoints": sidecar(name),
            },
        )
    assert len(ready.get("/form/analyses").json()) == 2
    only = ready.get("/form/analyses", params={"exercise_id": "push-up"}).json()
    assert [a["exercise_id"] for a in only] == ["push-up"]


def test_fr11_non_analyzable_exercise_is_a_400(ready):
    response = ready.post(
        "/form/analyses",
        json={
            "exercise_id": "barbell-curl",
            "created_at": "2026-07-06T19:00:00+00:00",
            "keypoints": sidecar("squat_side_02"),
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_analyzable"


def test_fr11_missing_pose_source_is_a_400(ready):
    response = ready.post(
        "/form/analyses",
        json={"exercise_id": "push-up", "created_at": "2026-07-06T19:00:00+00:00"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "pose_unavailable"


def test_fr11_unknown_analysis_is_a_404(ready):
    response = ready.get("/form/analyses/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "analysis_not_found"


def test_fr11_openapi_documents_every_scoped_endpoint(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/health",
        "/profile",
        "/profile/acknowledge-disclaimer",
        "/profile/pain-flags/{exercise_id}",
        "/exercises",
        "/exercises/{exercise_id}",
        "/programs",
        "/programs/{program_id}",
        "/programs/{program_id}/sessions/{week}/{day_index}",
        "/programs/{program_id}/next",
        "/workouts",
        "/analytics/volume",
        "/form/analyses",
        "/form/analyses/{analysis_id}",
        "/form/photo-analyses",
    }


def test_fr13c_api_surface_text_never_uses_diagnosis_language(ready):
    """FR-13(c) extended to the surface: schema docs and error bodies stay clean."""
    from formcoach.engine.safety import contains_diagnosis_language

    schema = json.dumps(ready.get("/openapi.json").json())
    errors = [
        ready.get("/programs/999").json(),
        ready.get("/form/analyses/999").json(),
        ready.post(
            "/workouts",
            json={
                "performed_at": "2026-07-06T18:00:00+00:00",
                "sets": [{"exercise_id": "nope", "reps": 5}],
            },
        ).json(),
    ]
    payload = schema + json.dumps(errors)
    assert contains_diagnosis_language(payload) == []
