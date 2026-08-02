"""Execution rules for plan steps (FR-11) — pure preconditions and ledger effects.

This module decides *whether* a step may be executed and *what* it does to the
ledger.  Actually writing the entries (atomically) is the store's job, so the
rules stay testable without a database.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..models import LedgerEntry, LedgerReason, Plan, PlanSet, PlanStep, StepKind, World
from .money import delivered_points


class ExecutionError(Exception):
    """A structured refusal to execute a plan step."""

    code = "execution_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **self.details}


class WorldPinMismatch(ExecutionError):
    """The loaded world's ``content_hash`` differs from the plan set's pin."""

    code = "world_pin_mismatch"


class StepOutOfOrder(ExecutionError):
    """An earlier step has not been executed yet."""

    code = "step_out_of_order"


class StepAlreadyExecuted(ExecutionError):
    """The step has already been executed; re-execution is a structured no-op."""

    code = "step_already_executed"


class ConfirmationRequired(ExecutionError):
    """An irreversible step needs an explicit confirmation flag."""

    code = "confirmation_required"


class InsufficientBalance(ExecutionError):
    """The step would drive a program's balance below zero."""

    code = "insufficient_balance"


def check_world_pin(plan_set: PlanSet, world: World) -> None:
    """FR-11: the *content hash* is the guard; the version string is display only."""
    if world.version.content_hash != plan_set.world_hash:
        raise WorldPinMismatch(
            "the loaded rewards world differs from the one this plan was computed "
            "against; re-plan the goal before executing",
            pinned_hash=plan_set.world_hash,
            pinned_version=plan_set.world_version,
            loaded_hash=world.version.content_hash,
            loaded_version=world.version.version,
        )


def check_step_executable(
    *,
    plan_set: PlanSet,
    plan: Plan,
    step: PlanStep,
    world: World,
    balances: Mapping[str, int],
    confirm_irreversible: bool,
) -> None:
    """Raise :class:`ExecutionError` unless ``step`` may be executed right now."""
    check_world_pin(plan_set, world)
    if step.executed_at is not None:
        raise StepAlreadyExecuted(
            f"step {step.seq} was already executed at {step.executed_at}",
            seq=step.seq,
            executed_at=step.executed_at,
        )
    for earlier in plan.steps:
        if earlier.seq < step.seq and earlier.executed_at is None:
            raise StepOutOfOrder(
                f"step {earlier.seq} must be executed before step {step.seq}",
                seq=step.seq,
                blocking_seq=earlier.seq,
            )
    if step.irreversible and not confirm_irreversible:
        raise ConfirmationRequired(
            f"step {step.seq} ({step.kind}) is irreversible and needs explicit confirmation",
            seq=step.seq,
            kind=str(step.kind),
        )
    assert step.from_program
    available = balances.get(step.from_program, 0)
    if available < step.points_sent:
        raise InsufficientBalance(
            f"{step.from_program} holds {available} points but step {step.seq} spends "
            f"{step.points_sent}",
            seq=step.seq,
            program_id=step.from_program,
            available=available,
            required=step.points_sent,
        )


_REDEMPTION_REASONS = {
    StepKind.BOOK_AWARD: LedgerReason.AWARD_REDEEM,
    StepKind.BOOK_PORTAL: LedgerReason.PORTAL_REDEEM,
    StepKind.REDEEM_CASH: LedgerReason.CASH_REDEEM,
}


def ledger_entries_for_step(
    step: PlanStep,
    world: World,
    balances: Mapping[str, int],
    *,
    at: str,
    plan_step_id: int,
) -> list[LedgerEntry]:
    """The exact ledger entries a step writes (FR-2/FR-11), with chained balances.

    A transfer writes ``transfer_out``, ``transfer_in`` and — when the edge has a
    tier bonus — a separate ``transfer_bonus`` entry, so the bonus is auditable.
    """
    running: dict[str, int] = dict(balances)
    entries: list[LedgerEntry] = []

    def emit(program_id: str, delta: int, reason: LedgerReason) -> None:
        post = running.get(program_id, 0) + delta
        if post < 0:
            raise InsufficientBalance(
                f"{program_id} would fall to {post} points",
                program_id=program_id,
                post_balance=post,
            )
        running[program_id] = post
        entries.append(
            LedgerEntry(
                program_id=program_id,
                delta_points=delta,
                post_balance=post,
                reason=reason,
                plan_step_id=plan_step_id,
                at=at,
            )
        )

    assert step.from_program
    if step.kind is StepKind.TRANSFER:
        assert step.to_program and step.edge_id and step.points_delivered is not None
        edge = world.edge(step.edge_id)
        base = delivered_points(
            edge.model_copy(update={"bonus_per_from": None, "bonus_to": None}),
            step.points_sent,
        )
        bonus = step.points_delivered - base
        emit(step.from_program, -step.points_sent, LedgerReason.TRANSFER_OUT)
        emit(step.to_program, base, LedgerReason.TRANSFER_IN)
        if bonus:
            emit(step.to_program, bonus, LedgerReason.TRANSFER_BONUS)
    else:
        emit(step.from_program, -step.points_sent, _REDEMPTION_REASONS[step.kind])
    return entries


def replay_balances(entries: list[LedgerEntry]) -> dict[str, int]:
    """Replay an append-only ledger from zero (FR-2 D0 invariant)."""
    balances: dict[str, int] = {}
    for entry in entries:
        balances[entry.program_id] = balances.get(entry.program_id, 0) + entry.delta_points
        if balances[entry.program_id] < 0:
            raise InsufficientBalance(
                f"ledger replay drives {entry.program_id} negative",
                program_id=entry.program_id,
            )
    return balances
