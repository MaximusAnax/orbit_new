"""FR-11 execution preconditions and the ledger effects of a plan step."""

from __future__ import annotations

from datetime import date

import pytest
from conftest import make_world, world_files
from pointsmax.engine.execution import (
    ConfirmationRequired,
    ExecutionError,
    InsufficientBalance,
    StepAlreadyExecuted,
    StepOutOfOrder,
    WorldPinMismatch,
    check_step_executable,
    check_world_pin,
    ledger_entries_for_step,
    replay_balances,
)
from pointsmax.models import LedgerReason, Plan, PlanSet, PlanStep, StepKind, Verdict

AT = "2026-08-02T09:15:00Z"


def step(**kwargs) -> PlanStep:
    base = {
        "id": 17,
        "seq": 1,
        "kind": StepKind.TRANSFER,
        "from_program": "bank_a",
        "to_program": "air_x",
        "edge_id": "bank_a__air_x",
        "points_sent": 120000,
        "points_delivered": 120000,
        "irreversible": True,
    }
    base.update(kwargs)
    return PlanStep(**base)


def plan(steps) -> Plan:
    return Plan(
        id=9,
        rank=1,
        gross_value_cents=0,
        cash_outlay_cents=0,
        points_cost_cents=0,
        net_value_cents=0,
        steps=steps,
    )


def plan_set(world) -> PlanSet:
    return PlanSet(
        id=5,
        goal_id=3,
        world_version=world.version.version,
        world_hash=world.version.content_hash,
        today=date(2026, 7, 31),
        verdict=Verdict.BOOK_WITH_POINTS,
    )


def test_fr11_world_pin_uses_the_content_hash_not_the_version_string(world):
    pinned = plan_set(world)
    check_world_pin(pinned, world)  # same bytes -> fine

    files = world_files()
    files["valuations.json"][0]["cpp_milli"] = 1999
    edited = make_world(files)  # same semver "1.0.0", different content
    assert edited.version.version == world.version.version
    with pytest.raises(WorldPinMismatch) as exc:
        check_world_pin(pinned, edited)
    payload = exc.value.as_dict()
    assert payload["code"] == "world_pin_mismatch"
    assert payload["pinned_version"] == payload["loaded_version"] == "1.0.0"
    assert payload["pinned_hash"] != payload["loaded_hash"]
    assert "re-plan" in payload["message"]


def test_fr11_steps_must_be_executed_in_order(world):
    first = step(id=17, seq=1)
    second = step(
        id=18,
        seq=2,
        kind=StepKind.BOOK_AWARD,
        from_program="air_x",
        to_program=None,
        edge_id=None,
        offer_id="x_out",
        points_delivered=None,
        points_sent=60000,
    )
    with pytest.raises(StepOutOfOrder) as exc:
        check_step_executable(
            plan_set=plan_set(world),
            plan=plan([first, second]),
            step=second,
            world=world,
            balances={"air_x": 120000},
            confirm_irreversible=True,
        )
    assert exc.value.details["blocking_seq"] == 1

    first.executed_at = AT
    check_step_executable(
        plan_set=plan_set(world),
        plan=plan([first, second]),
        step=second,
        world=world,
        balances={"air_x": 120000},
        confirm_irreversible=True,
    )


def test_fr11_re_executing_a_step_is_a_structured_no_op_error(world):
    done = step(executed_at=AT)
    with pytest.raises(StepAlreadyExecuted) as exc:
        check_step_executable(
            plan_set=plan_set(world),
            plan=plan([done]),
            step=done,
            world=world,
            balances={"bank_a": 130000},
            confirm_irreversible=True,
        )
    assert exc.value.as_dict()["executed_at"] == AT


def test_fr11_irreversible_steps_need_explicit_confirmation(world):
    transfer = step()
    with pytest.raises(ConfirmationRequired):
        check_step_executable(
            plan_set=plan_set(world),
            plan=plan([transfer]),
            step=transfer,
            world=world,
            balances={"bank_a": 130000},
            confirm_irreversible=False,
        )
    check_step_executable(
        plan_set=plan_set(world),
        plan=plan([transfer]),
        step=transfer,
        world=world,
        balances={"bank_a": 130000},
        confirm_irreversible=True,
    )


def test_fr11_reversible_steps_do_not_need_confirmation(world):
    portal = step(
        kind=StepKind.BOOK_PORTAL,
        to_program=None,
        edge_id=None,
        cashout_id="a_portal",
        points_delivered=None,
        points_sent=1000,
        irreversible=False,
    )
    check_step_executable(
        plan_set=plan_set(world),
        plan=plan([portal]),
        step=portal,
        world=world,
        balances={"bank_a": 1000},
        confirm_irreversible=False,
    )


def test_fr11_execution_revalidates_the_balance(world):
    transfer = step()
    with pytest.raises(InsufficientBalance) as exc:
        check_step_executable(
            plan_set=plan_set(world),
            plan=plan([transfer]),
            step=transfer,
            world=world,
            balances={"bank_a": 1000},
            confirm_irreversible=True,
        )
    assert exc.value.details == {
        "seq": 1,
        "program_id": "bank_a",
        "available": 1000,
        "required": 120000,
    }


def test_fr11_transfer_writes_out_and_in_entries(world):
    entries = ledger_entries_for_step(
        step(), world, {"bank_a": 130000, "air_x": 0}, at=AT, plan_step_id=17
    )
    assert [(e.program_id, e.delta_points, e.post_balance, e.reason) for e in entries] == [
        ("bank_a", -120000, 10000, LedgerReason.TRANSFER_OUT),
        ("air_x", 120000, 120000, LedgerReason.TRANSFER_IN),
    ]
    assert all(e.plan_step_id == 17 and e.at == AT for e in entries)


def test_fr11_tier_bonus_lands_as_its_own_ledger_entry(world):
    bonus_step = step(
        from_program="hotel_h",
        to_program="air_x",
        edge_id="hotel_h__air_x",
        points_sent=60000,
        points_delivered=25000,
    )
    entries = ledger_entries_for_step(
        bonus_step, world, {"hotel_h": 100000, "air_x": 0}, at=AT, plan_step_id=21
    )
    assert [(e.program_id, e.delta_points, e.reason) for e in entries] == [
        ("hotel_h", -60000, LedgerReason.TRANSFER_OUT),
        ("air_x", 20000, LedgerReason.TRANSFER_IN),
        ("air_x", 5000, LedgerReason.TRANSFER_BONUS),
    ]
    assert entries[-1].post_balance == 25000


def test_fr11_bookings_write_the_matching_redemption_reason(world):
    cases = {
        StepKind.BOOK_AWARD: (LedgerReason.AWARD_REDEEM, {"offer_id": "x_out"}, True),
        StepKind.BOOK_PORTAL: (LedgerReason.PORTAL_REDEEM, {"cashout_id": "a_portal"}, False),
        StepKind.REDEEM_CASH: (LedgerReason.CASH_REDEEM, {"cashout_id": "a_credit"}, False),
    }
    for kind, (reason, extra, irreversible) in cases.items():
        booking = step(
            kind=kind,
            from_program="air_x",
            to_program=None,
            edge_id=None,
            points_sent=60000,
            points_delivered=None,
            irreversible=irreversible,
            **extra,
        )
        entries = ledger_entries_for_step(booking, world, {"air_x": 120000}, at=AT, plan_step_id=18)
        assert len(entries) == 1
        assert entries[0].reason is reason
        assert entries[0].delta_points == -60000
        assert entries[0].post_balance == 60000


def test_fr11_ledger_entries_refuse_to_go_negative(world):
    with pytest.raises(InsufficientBalance):
        ledger_entries_for_step(step(), world, {"bank_a": 1000}, at=AT, plan_step_id=17)


def test_fr2_replay_reproduces_balances_from_zero(world):
    entries = ledger_entries_for_step(
        step(), world, {"bank_a": 130000, "air_x": 0}, at=AT, plan_step_id=17
    )
    from pointsmax.models import LedgerEntry

    opening = LedgerEntry(
        program_id="bank_a",
        delta_points=130000,
        post_balance=130000,
        reason=LedgerReason.SET,
        at="2026-08-01T00:00:00Z",
    )
    assert replay_balances([opening, *entries]) == {"bank_a": 10000, "air_x": 120000}


def test_execution_errors_serialize_with_a_code(world):
    error = ExecutionError("boom", seq=3)
    assert error.as_dict() == {"code": "execution_error", "message": "boom", "seq": 3}
