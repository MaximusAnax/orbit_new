"""Application services shared by the API (FR-14) and the CLI (FR-15).

This layer is *composition only*: it wires a :class:`~pointsmax.store.Repository`,
a loaded :class:`~pointsmax.models.World` and the pure engine together, and
translates domain failures into structured, coded errors.  Every arithmetic and
ranking rule lives in ``engine/``; nothing here decides value.

Time is always an explicit input (``today`` / ``at``); the surfaces default it
from the system clock at their edge, never here (SCOPE decision 13).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .adapters.goal_parser import GoalParser, RuleBasedGoalParser
from .adapters.world_provider import CommittedWorldProvider, WorldProvider
from .engine.advisor import compute_plan_set
from .engine.execution import (
    ExecutionError,
    check_step_executable,
    ledger_entries_for_step,
)
from .engine.goals import validate_goal_against_world
from .engine.search import SearchBudgetExceeded
from .engine.value import value_breakdown
from .engine.world import ActiveWorld, WorldIssue, active_subgraph, validate_world
from .models import (
    CardProduct,
    CashoutOption,
    Goal,
    GoalSpec,
    GoalStatus,
    LedgerEntry,
    ParseError,
    Plan,
    PlanParams,
    PlanSet,
    PlanStep,
    Profile,
    Program,
    TransferEdge,
    ValueBreakdown,
    Wallet,
    World,
)
from .store import NotFound, Repository

# --------------------------------------------------------------------------
# Structured errors (the API error catalog maps these codes to status codes)
# --------------------------------------------------------------------------


class ServiceError(Exception):
    """A structured, coded refusal from the service layer."""

    code = "service_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **self.details}


class UnknownEntity(ServiceError):
    """A referenced world entity (card, program, city) does not exist."""

    code = "unknown_entity"


class InvalidGoal(ServiceError):
    """A goal is well-formed but references something the world does not know."""

    code = "invalid_goal"


class GoalParseFailed(ServiceError):
    """Free text could not be parsed into a goal (FR-5)."""

    code = "goal_parse_failed"


class SearchBudgetError(ServiceError):
    """The funding search hit ``max_expansions`` (FR-7c)."""

    code = "search_budget_exceeded"


class DuplicateCard(ServiceError):
    """The wallet already holds this card product."""

    code = "duplicate_card"


class WorldInvalid(ServiceError):
    """The committed world failed FR-1 validation."""

    code = "world_invalid"


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WorldInfo:
    """Version, provenance and staleness of the loaded dataset."""

    version: str
    as_of: date
    content_hash: str
    valuations_as_of: date
    days_old: int
    stale: bool
    stale_days: int
    counts: dict[str, int]


@dataclass(frozen=True)
class BalanceView:
    """One program balance with its FR-13 three numbers."""

    program_id: str
    program_name: str
    points: int
    value: ValueBreakdown


@dataclass(frozen=True)
class WalletView:
    """Cards held, balances, and the portfolio totals ``wallet show`` prints."""

    cards: list[CardProduct]
    balances: list[BalanceView]
    baseline_total_cents: int
    cash_floor_total_cents: int
    travel_floor_total_cents: int


@dataclass(frozen=True)
class ProgramView:
    """A program plus the edges and options *this* wallet can use today (FR-3)."""

    program: Program
    cpp_milli: int
    as_of: date
    edges_out: list[TransferEdge]
    edges_in: list[TransferEdge]
    options: list[CashoutOption]


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


class PointsMaxService:
    """Everything the API and CLI need, with no HTTP or terminal concerns."""

    def __init__(
        self,
        repo: Repository,
        world: World,
        *,
        provider: WorldProvider | None = None,
        parser: GoalParser | None = None,
        params: PlanParams | None = None,
    ) -> None:
        self.repo = repo
        self.world = world
        self.provider = provider
        self.parser = parser or RuleBasedGoalParser(world)
        self.default_params = params or PlanParams()

    # -- world -------------------------------------------------------------

    def world_info(self, today: date, *, stale_days: int | None = None) -> WorldInfo:
        """FR-1/FR-10: version, entity counts and staleness relative to ``today``."""
        threshold = self.default_params.stale_days if stale_days is None else stale_days
        as_of = self.world.min_valuation_as_of()
        days_old = (today - as_of).days
        return WorldInfo(
            version=self.world.version.version,
            as_of=self.world.version.as_of,
            content_hash=self.world.version.content_hash,
            valuations_as_of=as_of,
            days_old=days_old,
            stale=days_old > threshold,
            stale_days=threshold,
            counts={
                "programs": len(self.world.programs),
                "cards": len(self.world.cards),
                "transfer_edges": len(self.world.edges),
                "cashout_options": len(self.world.cashouts),
                "valuations": len(self.world.valuations),
                "award_offers": len(self.world.offers),
                "reference_fares": len(self.world.reference_fares),
                "gazetteer_cities": len(self.world.gazetteer),
            },
        )

    def validate(self) -> list[WorldIssue]:
        """Re-run FR-1 validation, including the content hash when a provider is set."""
        computed = None
        if self.provider is not None and isinstance(self.provider, CommittedWorldProvider):
            computed = self.provider.content_hash()
        return validate_world(self.world, computed_hash=computed)

    def active_world(self, today: date) -> ActiveWorld:
        """FR-3 gated subgraph for the wallet's cards on ``today``."""
        return active_subgraph(self.world, self.repo.list_cards(), today)

    # -- profile -----------------------------------------------------------

    def get_profile(self) -> Profile:
        return self.repo.get_profile()

    def set_profile(
        self,
        *,
        display_name: str | None = None,
        home_city: str | None = None,
        default_passengers: int | None = None,
        at: str,
    ) -> Profile:
        """Update the singleton profile; ``home_city`` must be a known city (FR-5)."""
        profile = self.repo.get_profile()
        if home_city is not None and self.world.city(home_city) is None:
            raise UnknownEntity(f"unknown city code {home_city!r}", city=home_city)
        updated = profile.model_copy(
            update={
                "display_name": display_name if display_name is not None else profile.display_name,
                "home_city": home_city if home_city is not None else profile.home_city,
                "default_passengers": (
                    default_passengers
                    if default_passengers is not None
                    else profile.default_passengers
                ),
                "created_at": profile.created_at or at,
                "updated_at": at,
            }
        )
        return self.repo.save_profile(updated)

    # -- catalog -----------------------------------------------------------

    def list_card_products(self, issuer: str | None = None) -> list[CardProduct]:
        cards = sorted(self.world.cards, key=lambda c: c.id)
        if issuer is None:
            return cards
        needle = issuer.strip().lower()
        return [c for c in cards if c.issuer.lower() == needle]

    def list_programs(self, today: date) -> list[ProgramView]:
        active = self.active_world(today)
        return [
            self._program_view(p, active) for p in sorted(self.world.programs, key=lambda p: p.id)
        ]

    def get_program(self, program_id: str, today: date) -> ProgramView:
        if not self.world.has_program(program_id):
            raise UnknownEntity(f"unknown program {program_id!r}", program_id=program_id)
        return self._program_view(self.world.program(program_id), self.active_world(today))

    def _program_view(self, program: Program, active: ActiveWorld) -> ProgramView:
        valuation = self.world.valuation(program.id)
        return ProgramView(
            program=program,
            cpp_milli=valuation.cpp_milli,
            as_of=valuation.as_of,
            edges_out=list(active.edges_from(program.id)),
            edges_in=list(active.edges_into(program.id)),
            options=list(active.options_for(program.id)),
        )

    # -- wallet ------------------------------------------------------------

    def wallet(self) -> Wallet:
        return self.repo.wallet()

    def add_card(self, card_product_id: str, *, at: str) -> CardProduct:
        if not self.world.has_card(card_product_id):
            raise UnknownEntity(
                f"unknown card product {card_product_id!r}", card_product_id=card_product_id
            )
        if not self.repo.add_card(card_product_id, at):
            raise DuplicateCard(
                f"{card_product_id!r} is already in the wallet", card_product_id=card_product_id
            )
        return self.world.card(card_product_id)

    def remove_card(self, card_product_id: str) -> None:
        if not self.repo.remove_card(card_product_id):
            raise NotFound(
                f"{card_product_id!r} is not in the wallet", card_product_id=card_product_id
            )

    def set_balance(
        self, program_id: str, points: int, *, at: str, note: str | None = None
    ) -> LedgerEntry:
        self._require_program(program_id)
        return self.repo.set_balance(program_id, points, at=at, note=note)

    def adjust_balance(
        self, program_id: str, delta: int, *, at: str, note: str | None = None
    ) -> LedgerEntry:
        self._require_program(program_id)
        return self.repo.adjust_balance(program_id, delta, at=at, note=note)

    def ledger(self, program_id: str | None = None, limit: int | None = None) -> list[LedgerEntry]:
        if program_id is not None:
            self._require_program(program_id)
        return self.repo.list_ledger(program_id, limit)

    def wallet_view(self, today: date) -> WalletView:
        """Cards, balances and the FR-13 totals (US-1)."""
        active = self.active_world(today)
        balances = self.repo.balances()
        rows: list[BalanceView] = []
        for program_id in sorted(balances):
            if not self.world.has_program(program_id):
                continue
            points = balances[program_id]
            rows.append(
                BalanceView(
                    program_id=program_id,
                    program_name=self.world.program(program_id).name,
                    points=points,
                    value=value_breakdown(self.world, active, program_id, points),
                )
            )
        return WalletView(
            cards=[self.world.card(c) for c in self.repo.list_cards() if self.world.has_card(c)],
            balances=rows,
            baseline_total_cents=sum(r.value.baseline_value_cents for r in rows),
            cash_floor_total_cents=sum(r.value.cash_floor_cents for r in rows),
            travel_floor_total_cents=sum(r.value.travel_floor_cents for r in rows),
        )

    # -- valuation (FR-13) -------------------------------------------------

    def value(self, program_id: str, points: int, today: date) -> ValueBreakdown:
        self._require_program(program_id)
        if points < 0:
            raise InvalidGoal("points must be non-negative", points=points)
        return value_breakdown(self.world, self.active_world(today), program_id, points)

    def valuations(self, today: date) -> list[ValueBreakdown]:
        """Per-program baseline plus this wallet's floors, sized by held balances."""
        active = self.active_world(today)
        balances = self.repo.balances()
        return [
            value_breakdown(self.world, active, program.id, balances.get(program.id, 0))
            for program in sorted(self.world.programs, key=lambda p: p.id)
        ]

    # -- goals (FR-4/FR-5) -------------------------------------------------

    def parse_goal(
        self, text: str, *, today: date, home_city: str | None = None, default_passengers: int = 1
    ) -> GoalSpec:
        result = self.parser.parse(
            text, today=today, home_city=home_city, default_passengers=default_passengers
        )
        if isinstance(result, ParseError):
            raise GoalParseFailed(
                result.message,
                raw_text=result.raw_text,
                missing=list(result.missing),
                ambiguous=list(result.ambiguous),
            )
        return result

    def create_goal(self, spec: GoalSpec, *, at: str) -> Goal:
        problems = validate_goal_against_world(spec, self.world)
        if problems:
            raise InvalidGoal("; ".join(problems), problems=problems)
        goal = Goal(**spec.model_dump(mode="python"), created_at=at)
        return self.repo.add_goal(goal)

    def create_goal_from_text(
        self,
        text: str,
        *,
        today: date,
        at: str,
        home_city: str | None = None,
        default_passengers: int | None = None,
    ) -> Goal:
        profile = self.repo.get_profile()
        spec = self.parse_goal(
            text,
            today=today,
            home_city=home_city if home_city is not None else profile.home_city,
            default_passengers=(
                default_passengers if default_passengers is not None else profile.default_passengers
            ),
        )
        return self.create_goal(spec, at=at)

    def list_goals(self) -> list[Goal]:
        return self.repo.list_goals()

    def get_goal(self, goal_id: int) -> Goal:
        goal = self.repo.get_goal(goal_id)
        if goal is None:
            raise NotFound(f"goal {goal_id} does not exist", goal_id=goal_id)
        return goal

    def set_goal_status(self, goal_id: int, status: GoalStatus) -> Goal:
        return self.repo.set_goal_status(goal_id, status)

    # -- planning (FR-7 .. FR-11) -----------------------------------------

    def compute_plans(
        self,
        goal_id: int,
        *,
        today: date,
        at: str,
        params: PlanParams | None = None,
    ) -> PlanSet:
        """Compute, persist and return the ranked plan set for a goal."""
        goal = self.get_goal(goal_id)
        wallet = self.repo.wallet()
        try:
            plan_set = compute_plan_set(
                world=self.world,
                wallet=wallet,
                goal=goal.spec(),
                today=today,
                params=params or self.default_params,
                goal_id=goal_id,
                created_at=at,
            )
        except SearchBudgetExceeded as exc:
            raise SearchBudgetError(
                str(exc), expansions=exc.expansions, max_expansions=exc.budget
            ) from exc
        stored = self.repo.save_plan_set(plan_set)
        if goal.status is GoalStatus.ACTIVE:
            self.repo.set_goal_status(goal_id, GoalStatus.PLANNED)
        return stored

    def get_plan_set(self, plan_set_id: int) -> PlanSet:
        plan_set = self.repo.get_plan_set(plan_set_id)
        if plan_set is None:
            raise NotFound(f"plan set {plan_set_id} does not exist", plan_set_id=plan_set_id)
        return plan_set

    def list_plan_sets(self, goal_id: int) -> list[PlanSet]:
        self.get_goal(goal_id)
        return self.repo.list_plan_sets(goal_id)

    def latest_plan_set(self, goal_id: int) -> PlanSet:
        self.get_goal(goal_id)
        plan_set = self.repo.latest_plan_set(goal_id)
        if plan_set is None:
            raise NotFound(f"goal {goal_id} has no computed plans yet", goal_id=goal_id)
        return plan_set

    def get_plan(self, plan_id: int) -> Plan:
        plan = self.repo.get_plan(plan_id)
        if plan is None:
            raise NotFound(f"plan {plan_id} does not exist", plan_id=plan_id)
        return plan

    def execute_step(
        self,
        plan_id: int,
        seq: int,
        *,
        at: str,
        confirm_irreversible: bool = False,
    ) -> tuple[PlanStep, list[LedgerEntry]]:
        """FR-11: check the preconditions, then write the ledger entries atomically."""
        plan = self.get_plan(plan_id)
        assert plan.plan_set_id is not None
        plan_set = self.get_plan_set(plan.plan_set_id)
        step = next((s for s in plan.steps if s.seq == seq), None)
        if step is None:
            raise NotFound(f"plan {plan_id} has no step {seq}", plan_id=plan_id, seq=seq)
        balances = self.repo.balances()
        check_step_executable(
            plan_set=plan_set,
            plan=plan,
            step=step,
            world=self.world,
            balances=balances,
            confirm_irreversible=confirm_irreversible,
        )
        assert step.id is not None
        entries = ledger_entries_for_step(step, self.world, balances, at=at, plan_step_id=step.id)
        return self.repo.record_step_execution(step.id, entries, at=at)

    # -- helpers -----------------------------------------------------------

    def _require_program(self, program_id: str) -> None:
        if not self.world.has_program(program_id):
            raise UnknownEntity(f"unknown program {program_id!r}", program_id=program_id)


__all__ = [
    "BalanceView",
    "DuplicateCard",
    "ExecutionError",
    "GoalParseFailed",
    "InvalidGoal",
    "PointsMaxService",
    "ProgramView",
    "SearchBudgetError",
    "ServiceError",
    "UnknownEntity",
    "WalletView",
    "WorldInfo",
    "WorldInvalid",
]
