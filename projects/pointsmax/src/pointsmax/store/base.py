"""Repository interface and backend-independent rules (DATA_MODEL "SQLite entities").

Balances are never stored: they are the ledger's running sum (FR-2).  Everything
that changes a balance goes through :meth:`Repository.append_entries`, which
enforces the append-only invariants in one place so both backends behave
identically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models import (
    Goal,
    GoalStatus,
    LedgerEntry,
    LedgerReason,
    Plan,
    PlanSet,
    PlanStep,
    Profile,
    Wallet,
)


class StoreError(Exception):
    """A structured refusal from the persistence layer."""

    code = "store_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **self.details}


class NegativeBalance(StoreError):
    """A ledger entry would drive a program's balance below zero."""

    code = "negative_balance"


class LedgerChainBroken(StoreError):
    """A ledger entry's ``post_balance`` does not chain from the previous entry."""

    code = "ledger_chain_broken"


class NotFound(StoreError):
    """A referenced row does not exist."""

    code = "not_found"


class InvalidTransition(StoreError):
    """A goal status transition is not allowed."""

    code = "invalid_transition"


#: FR-4 goal lifecycle: ``active -> planned -> fulfilled | dropped``; terminal states frozen.
GOAL_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.ACTIVE: frozenset({GoalStatus.PLANNED, GoalStatus.DROPPED}),
    GoalStatus.PLANNED: frozenset({GoalStatus.FULFILLED, GoalStatus.DROPPED}),
    GoalStatus.FULFILLED: frozenset(),
    GoalStatus.DROPPED: frozenset(),
}


class Repository(ABC):
    """Persistence for all user state."""

    # -- profile -----------------------------------------------------------

    @abstractmethod
    def get_profile(self) -> Profile:
        """The singleton profile, created with defaults if absent."""

    @abstractmethod
    def save_profile(self, profile: Profile) -> Profile: ...

    # -- wallet cards ------------------------------------------------------

    @abstractmethod
    def list_cards(self) -> list[str]:
        """Card product ids held, sorted."""

    @abstractmethod
    def add_card(self, card_product_id: str, at: str) -> bool:
        """Add a card; returns False when already held (unique per product)."""

    @abstractmethod
    def remove_card(self, card_product_id: str) -> bool: ...

    # -- ledger ------------------------------------------------------------

    @abstractmethod
    def list_ledger(
        self, program_id: str | None = None, limit: int | None = None
    ) -> list[LedgerEntry]:
        """Entries in insertion order (oldest first)."""

    @abstractmethod
    def balances(self) -> dict[str, int]:
        """Current balance per program (last ``post_balance``), zero entries omitted."""

    @abstractmethod
    def _persist_entries(self, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        """Append validated entries atomically, assigning ids."""

    def balance(self, program_id: str) -> int:
        return self.balances().get(program_id, 0)

    def append_entries(self, entries: Iterable[LedgerEntry]) -> list[LedgerEntry]:
        """Validate and append entries atomically (FR-2).

        Each entry must chain: ``post_balance == previous post_balance + delta``
        for its program, and no balance may go negative.
        """
        pending = list(entries)
        if not pending:
            return []
        running = self.balances()
        for entry in pending:
            expected = running.get(entry.program_id, 0) + entry.delta_points
            if expected < 0:
                raise NegativeBalance(
                    f"{entry.program_id} would fall to {expected} points",
                    program_id=entry.program_id,
                    post_balance=expected,
                )
            if entry.post_balance != expected:
                raise LedgerChainBroken(
                    f"{entry.program_id}: post_balance {entry.post_balance} does not "
                    f"chain from {running.get(entry.program_id, 0)} with delta "
                    f"{entry.delta_points}",
                    program_id=entry.program_id,
                    expected=expected,
                    given=entry.post_balance,
                )
            running[entry.program_id] = expected
        return self._persist_entries(pending)

    def set_balance(
        self, program_id: str, points: int, *, at: str, note: str | None = None
    ) -> LedgerEntry:
        """Record an absolute balance (`set` reason)."""
        if points < 0:
            raise NegativeBalance(f"{program_id} cannot be set to {points}", program_id=program_id)
        current = self.balance(program_id)
        entry = LedgerEntry(
            program_id=program_id,
            delta_points=points - current,
            post_balance=points,
            reason=LedgerReason.SET,
            note=note,
            at=at,
        )
        return self.append_entries([entry])[0]

    def adjust_balance(
        self, program_id: str, delta: int, *, at: str, note: str | None = None
    ) -> LedgerEntry:
        """Record a relative correction (`adjust` reason)."""
        current = self.balance(program_id)
        if current + delta < 0:
            raise NegativeBalance(
                f"{program_id} would fall to {current + delta} points",
                program_id=program_id,
                post_balance=current + delta,
            )
        entry = LedgerEntry(
            program_id=program_id,
            delta_points=delta,
            post_balance=current + delta,
            reason=LedgerReason.ADJUST,
            note=note,
            at=at,
        )
        return self.append_entries([entry])[0]

    def wallet(self) -> Wallet:
        """The engine-facing view of user state."""
        return Wallet(cards=self.list_cards(), balances=self.balances())

    # -- goals -------------------------------------------------------------

    @abstractmethod
    def add_goal(self, goal: Goal) -> Goal: ...

    @abstractmethod
    def get_goal(self, goal_id: int) -> Goal | None: ...

    @abstractmethod
    def list_goals(self) -> list[Goal]: ...

    @abstractmethod
    def _write_goal_status(self, goal_id: int, status: GoalStatus) -> Goal: ...

    def set_goal_status(self, goal_id: int, status: GoalStatus) -> Goal:
        """Move a goal through its lifecycle; terminal states are frozen (FR-4)."""
        goal = self.get_goal(goal_id)
        if goal is None:
            raise NotFound(f"goal {goal_id} does not exist", goal_id=goal_id)
        if status == goal.status:
            return goal
        if status not in GOAL_TRANSITIONS[goal.status]:
            raise InvalidTransition(
                f"goal {goal_id} cannot move from {goal.status} to {status}",
                goal_id=goal_id,
                current=str(goal.status),
                requested=str(status),
            )
        return self._write_goal_status(goal_id, status)

    # -- plans -------------------------------------------------------------

    @abstractmethod
    def save_plan_set(self, plan_set: PlanSet) -> PlanSet:
        """Persist an immutable plan set with its plans and steps, assigning ids."""

    @abstractmethod
    def get_plan_set(self, plan_set_id: int) -> PlanSet | None: ...

    @abstractmethod
    def list_plan_sets(self, goal_id: int) -> list[PlanSet]:
        """Plan sets for a goal, newest last."""

    @abstractmethod
    def get_plan(self, plan_id: int) -> Plan | None: ...

    @abstractmethod
    def get_step(self, step_id: int) -> PlanStep | None: ...

    @abstractmethod
    def _mark_step_executed(self, step_id: int, at: str) -> PlanStep: ...

    def latest_plan_set(self, goal_id: int) -> PlanSet | None:
        sets = self.list_plan_sets(goal_id)
        return sets[-1] if sets else None

    def record_step_execution(
        self, step_id: int, entries: Iterable[LedgerEntry], *, at: str
    ) -> tuple[PlanStep, list[LedgerEntry]]:
        """Append a step's ledger entries and stamp ``executed_at`` atomically (FR-11)."""
        step = self.get_step(step_id)
        if step is None:
            raise NotFound(f"plan step {step_id} does not exist", step_id=step_id)
        written = self.append_entries(entries)
        return self._mark_step_executed(step_id, at), written
