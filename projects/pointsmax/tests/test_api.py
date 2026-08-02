"""FastAPI surface tests (FR-14) — status codes, schemas and the error catalog."""

from __future__ import annotations

from typing import Any

TODAY = "2026-07-31"


def _plan_goal(client: Any) -> tuple[int, dict[str, Any]]:
    """Create a round-trip goal and compute its plans; returns (goal_id, plan set)."""
    created = client.post(
        "/goals",
        json={
            "kind": "flight",
            "origin_city": "AAA",
            "dest_city": "BBB",
            "cabin": "business",
            "round_trip": True,
            "passengers": 1,
            "month": "2026-10",
            "at": "2026-07-31T00:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    response = client.post(
        f"/goals/{goal_id}/plans", json={"today": TODAY, "at": "2026-07-31T00:00:01Z"}
    )
    assert response.status_code == 201, response.text
    return goal_id, response.json()


# --------------------------------------------------------------------------
# FR-1 / FR-14: health and world
# --------------------------------------------------------------------------


def test_health_and_world_fr1(client: Any) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert len(health.json()["world_hash"]) == 64

    world = client.get("/world", params={"today": TODAY})
    assert world.status_code == 200
    body = world.json()
    assert body["counts"]["programs"] == 5
    assert body["stale"] is False
    assert body["days_old"] == 30


def test_world_validate_fr1(client: Any) -> None:
    response = client.post("/world/validate")
    assert response.status_code == 200
    assert response.json() == {"valid": True, "issue_count": 0, "issues": []}


# --------------------------------------------------------------------------
# FR-5 / DATA_MODEL: profile
# --------------------------------------------------------------------------


def test_profile_roundtrip_fr5(client: Any) -> None:
    response = client.put(
        "/profile", json={"home_city": "AAA", "default_passengers": 2, "display_name": "owner"}
    )
    assert response.status_code == 200
    assert response.json()["home_city"] == "AAA"
    assert client.get("/profile").json()["default_passengers"] == 2


def test_profile_rejects_unknown_city_fr5(client: Any) -> None:
    response = client.put("/profile", json={"home_city": "ZZZ"})
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_entity"


# --------------------------------------------------------------------------
# FR-3: catalog and gating
# --------------------------------------------------------------------------


def test_cards_catalog_fr3(client: Any) -> None:
    every = client.get("/cards").json()
    assert {c["id"] for c in every} == {"card_a", "card_a_basic", "card_b"}
    filtered = client.get("/cards", params={"issuer": "bank b"}).json()
    assert [c["id"] for c in filtered] == ["card_b"]


def test_program_shows_only_active_edges_fr3(client: Any) -> None:
    body = client.get("/programs/bank_a", params={"today": TODAY}).json()
    assert body["cpp_milli"] == 2000
    assert {e["id"] for e in body["active_edges_out"]} == {
        "bank_a__air_x",
        "bank_a__air_y",
        "bank_a__hotel_h",
    }
    assert {o["id"] for o in body["active_options"]} == {"a_credit", "a_gift", "a_portal"}


def test_unknown_program_is_unprocessable_fr14(client: Any) -> None:
    response = client.get("/programs/nope")
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_entity"


# --------------------------------------------------------------------------
# FR-2 / FR-13: wallet, ledger, valuation
# --------------------------------------------------------------------------


def test_wallet_reports_three_numbers_fr13(client: Any) -> None:
    body = client.get("/wallet", params={"today": TODAY}).json()
    assert {c["id"] for c in body["cards"]} == {"card_a", "card_b"}
    rows = {row["program_id"]: row for row in body["balances"]}
    bank_a = rows["bank_a"]["value"]
    assert bank_a["baseline_value_cents"] == 200000
    assert bank_a["cash_floor_cents"] == 100000
    assert bank_a["cash_floor_option_id"] == "a_credit"
    assert bank_a["travel_floor_cents"] == 150000
    assert bank_a["travel_floor_option_id"] == "a_portal"
    assert body["baseline_total_cents"] == 200000 + 144000


def test_add_and_remove_card_fr2(client: Any) -> None:
    assert client.post("/wallet/cards", json={"card_product_id": "card_a"}).status_code == 409
    assert client.post("/wallet/cards", json={"card_product_id": "nope"}).status_code == 422
    added = client.post("/wallet/cards", json={"card_product_id": "card_a_basic"})
    assert added.status_code == 201
    assert client.delete("/wallet/cards/card_a_basic").status_code == 204
    missing = client.delete("/wallet/cards/card_a_basic")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_balance_updates_and_ledger_fr2(client: Any) -> None:
    set_response = client.put(
        "/wallet/balances/bank_a", json={"points": 50000, "at": "2026-07-02T00:00:00Z"}
    )
    assert set_response.status_code == 200
    assert set_response.json()["post_balance"] == 50000
    assert set_response.json()["delta_points"] == -50000

    adjust = client.post(
        "/wallet/balances/bank_a/adjust", json={"delta": -60000, "reason": "typo"}
    )
    assert adjust.status_code == 409
    assert adjust.json()["code"] == "negative_balance"

    ledger = client.get("/wallet/ledger", params={"program": "bank_a"}).json()
    assert [entry["reason"] for entry in ledger] == ["set", "set"]


def test_valuation_endpoint_fr13(client: Any) -> None:
    response = client.get("/valuations/bank_a", params={"points": 2499, "today": TODAY})
    body = response.json()
    assert body["baseline_value_cents"] == 4998
    assert body["cash_floor_cents"] == 2499
    # a_gift needs 2,500-point multiples, so it cannot beat the portal here
    assert body["travel_floor_option_id"] == "a_portal"
    listing = client.get("/valuations", params={"today": TODAY}).json()
    assert {row["program_id"] for row in listing} == {
        "bank_a",
        "bank_b",
        "air_x",
        "air_y",
        "hotel_h",
    }


# --------------------------------------------------------------------------
# FR-4 / FR-5: goals
# --------------------------------------------------------------------------


def test_create_goal_from_text_fr5(client: Any) -> None:
    response = client.post(
        "/goals", json={"text": "round-trip business Alfaville to Betatown in October",
                        "today": TODAY}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["origin_city"], body["dest_city"], body["round_trip"]) == ("AAA", "BBB", True)
    assert body["travel_window_start"] == "2026-10-01"


def test_unparseable_goal_is_structured_fr5(client: Any) -> None:
    response = client.post("/goals", json={"text": "somewhere warm", "today": TODAY})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "goal_parse_failed"
    assert "dest_city" in body["detail"]["missing"]


def test_goal_status_transitions_fr4(client: Any) -> None:
    goal_id, _ = _plan_goal(client)
    assert client.get(f"/goals/{goal_id}").json()["status"] == "planned"
    assert client.patch(f"/goals/{goal_id}", json={"status": "fulfilled"}).status_code == 200
    bad = client.patch(f"/goals/{goal_id}", json={"status": "active"})
    assert bad.status_code == 409
    assert bad.json()["code"] == "invalid_transition"


def test_missing_goal_is_404_fr14(client: Any) -> None:
    response = client.get("/goals/999")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --------------------------------------------------------------------------
# FR-7 .. FR-11: plans and execution
# --------------------------------------------------------------------------


def test_plan_set_shape_and_disclaimer_fr9_fr16(client: Any) -> None:
    _goal_id, plan_set = _plan_goal(client)
    assert plan_set["verdict"] in {"book_with_points", "pay_cash_keep_points"}
    assert plan_set["world_hash"] and plan_set["world_version"] == "1.0.0"
    assert plan_set["disclaimer"].startswith("estimates from a versioned dataset")
    ranks = [plan["rank"] for plan in plan_set["plans"]]
    assert ranks == sorted(ranks)
    top = plan_set["plans"][0]
    assert top["disclaimer"] == plan_set["disclaimer"]
    assert [step["seq"] for step in top["steps"]] == list(range(1, len(top["steps"]) + 1))
    assert all(step["explanation"] for step in top["steps"])


def test_plan_lookup_endpoints_fr11(client: Any) -> None:
    goal_id, plan_set = _plan_goal(client)
    fetched = client.get(f"/plan-sets/{plan_set['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == plan_set["id"]

    latest = client.get(f"/goals/{goal_id}/plans", params={"latest": 1}).json()
    assert len(latest) == 1 and latest[0]["id"] == plan_set["id"]

    plan_id = plan_set["plans"][0]["id"]
    plan = client.get(f"/plans/{plan_id}").json()
    assert plan["signature"] and plan["rank"] == 1


def test_execution_requires_confirmation_fr11(client: Any) -> None:
    _goal_id, plan_set = _plan_goal(client)
    plan = plan_set["plans"][0]
    response = client.post(f"/plans/{plan['id']}/steps/1/execute", json={"at": "2026-08-01T00:00:00Z"})
    assert response.status_code == 428
    assert response.json()["code"] == "confirmation_required"


def test_execution_order_and_ledger_fr11_fr2(client: Any) -> None:
    _goal_id, plan_set = _plan_goal(client)
    plan = plan_set["plans"][0]
    last = len(plan["steps"])
    out_of_order = client.post(
        f"/plans/{plan['id']}/steps/{last}/execute",
        json={"at": "2026-08-01T00:00:00Z", "confirm_irreversible": True},
    )
    assert out_of_order.status_code == 409
    assert out_of_order.json()["code"] == "step_out_of_order"

    first = client.post(
        f"/plans/{plan['id']}/steps/1/execute",
        json={"at": "2026-08-01T00:00:00Z", "confirm_irreversible": True},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["step"]["executed_at"] == "2026-08-01T00:00:00Z"
    assert body["entries"], "a step must write ledger entries"
    assert body["balances"] == {
        entry["program_id"]: entry["post_balance"] for entry in body["entries"]
    } | {
        p: n
        for p, n in body["balances"].items()
        if p not in {entry["program_id"] for entry in body["entries"]}
    }

    again = client.post(
        f"/plans/{plan['id']}/steps/1/execute",
        json={"at": "2026-08-01T00:01:00Z", "confirm_irreversible": True},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "step_already_executed"


def test_execution_refuses_a_different_world_fr11(client: Any, world) -> None:
    from conftest import make_world, world_files

    _goal_id, plan_set = _plan_goal(client)
    plan_id = plan_set["plans"][0]["id"]
    edited = world_files()
    edited["reference_fares.json"][0]["fare_cents"] += 1
    client.app.state.service.world = make_world(edited)
    response = client.post(
        f"/plans/{plan_id}/steps/1/execute",
        json={"at": "2026-08-01T00:00:00Z", "confirm_irreversible": True},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "world_pin_mismatch"
    assert response.json()["detail"]["pinned_hash"] == world.version.content_hash


def test_cash_goal_never_uses_the_portal_fr12(client: Any) -> None:
    created = client.post("/goals", json={"kind": "cash", "at": "2026-07-31T00:00:00Z"})
    assert created.status_code == 201
    plan_set = client.post(
        f"/goals/{created.json()['id']}/plans", json={"today": TODAY}
    ).json()
    assert plan_set["verdict"] == "cash_plan"
    used = {
        step["cashout_id"]
        for plan in plan_set["plans"]
        for step in plan["steps"]
        if step["cashout_id"]
    }
    assert "a_portal" not in used
    assert used <= {"a_credit", "b_deposit"}
