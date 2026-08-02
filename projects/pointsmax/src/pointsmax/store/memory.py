"""In-memory repository — the backend tests and evals use."""

from __future__ import annotations

from ..models import (
    Goal,
    GoalStatus,
    LedgerEntry,
    Plan,
    PlanSet,
    PlanStep,
    Profile,
)
from .base import Repository


class InMemoryRepository(Repository):
    """A dict-backed :class:`Repository` with the same semantics as SQLite."""

    def __init__(self) -> None:
        self._profile = Profile()
        self._cards: dict[str, str] = {}
        self._ledger: list[LedgerEntry] = []
        self._goals: dict[int, Goal] = {}
        self._plan_sets: dict[int, PlanSet] = {}
        self._plans: dict[int, Plan] = {}
        self._steps: dict[int, PlanStep] = {}
        self._next_ids: dict[str, int] = {}

    def _next_id(self, table: str) -> int:
        value = self._next_ids.get(table, 0) + 1
        self._next_ids[table] = value
        return value

    # -- profile -----------------------------------------------------------

    def get_profile(self) -> Profile:
        return self._profile.model_copy(deep=True)

    def save_profile(self, profile: Profile) -> Profile:
        self._profile = profile.model_copy(deep=True)
        return self.get_profile()

    # -- cards -------------------------------------------------------------

    def list_cards(self) -> list[str]:
        return sorted(self._cards)

    def add_card(self, card_product_id: str, at: str) -> bool:
        if card_product_id in self._cards:
            return False
        self._cards[card_product_id] = at
        return True

    def remove_card(self, card_product_id: str) -> bool:
        return self._cards.pop(card_product_id, None) is not None

    # -- ledger ------------------------------------------------------------

    def list_ledger(
        self, program_id: str | None = None, limit: int | None = None
    ) -> list[LedgerEntry]:
        rows = [e for e in self._ledger if program_id is None or e.program_id == program_id]
        if limit is not None:
            rows = rows[-limit:]
        return list(rows)

    def balances(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self._ledger:
            out[entry.program_id] = entry.post_balance
        return {p: n for p, n in out.items() if n}

    def _persist_entries(self, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        written = []
        for entry in entries:
            stored = entry.model_copy(update={"id": self._next_id("ledger_entry")})
            self._ledger.append(stored)
            written.append(stored)
        return written

    # -- goals -------------------------------------------------------------

    def add_goal(self, goal: Goal) -> Goal:
        stored = goal.model_copy(update={"id": self._next_id("goal")}, deep=True)
        self._goals[stored.id] = stored
        return stored.model_copy(deep=True)

    def get_goal(self, goal_id: int) -> Goal | None:
        goal = self._goals.get(goal_id)
        return goal.model_copy(deep=True) if goal else None

    def list_goals(self) -> list[Goal]:
        return [self._goals[key].model_copy(deep=True) for key in sorted(self._goals)]

    def _write_goal_status(self, goal_id: int, status: GoalStatus) -> Goal:
        stored = self._goals[goal_id].model_copy(update={"status": status}, deep=True)
        self._goals[goal_id] = stored
        return stored.model_copy(deep=True)

    # -- plans -------------------------------------------------------------

    def save_plan_set(self, plan_set: PlanSet) -> PlanSet:
        set_id = self._next_id("plan_set")
        plans: list[Plan] = []
        recommended: int | None = None
        for plan in plan_set.plans:
            plan_id = self._next_id("plan")
            steps = []
            for step in plan.steps:
                steps.append(
                    step.model_copy(update={"id": self._next_id("plan_step"), "plan_id": plan_id})
                )
            stored_plan = plan.model_copy(
                update={"id": plan_id, "plan_set_id": set_id, "steps": steps}, deep=False
            )
            plans.append(stored_plan)
            self._plans[plan_id] = stored_plan
            for step in steps:
                assert step.id is not None
                self._steps[step.id] = step
            if plan.rank == 1:
                recommended = plan_id
        stored = plan_set.model_copy(
            update={
                "id": set_id,
                "plans": plans,
                "recommended_plan_id": recommended if plan_set.recommended() else None,
            }
        )
        self._plan_sets[set_id] = stored
        return stored

    def get_plan_set(self, plan_set_id: int) -> PlanSet | None:
        return self._plan_sets.get(plan_set_id)

    def list_plan_sets(self, goal_id: int) -> list[PlanSet]:
        return [
            self._plan_sets[key]
            for key in sorted(self._plan_sets)
            if self._plan_sets[key].goal_id == goal_id
        ]

    def get_plan(self, plan_id: int) -> Plan | None:
        return self._plans.get(plan_id)

    def get_step(self, step_id: int) -> PlanStep | None:
        return self._steps.get(step_id)

    def _mark_step_executed(self, step_id: int, at: str) -> PlanStep:
        step = self._steps[step_id]
        step.executed_at = at
        return step
