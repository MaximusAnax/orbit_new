"""FastAPI application (FR-14) — thin: validate, delegate, serialize.

Every route reads its ``today``/``at`` from the request (defaulting at this edge
only) and hands off to :class:`~pointsmax.service.PointsMaxService`.  No ranking,
no arithmetic and no gating decision is made in this module.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Query, Request, status

from ..adapters.world_provider import CommittedWorldProvider
from ..engine.goals import cash_goal, flight_goal, stay_goal
from ..models import GoalKind, GoalSpec, PlanParams
from ..service import InvalidGoal, PointsMaxService
from ..store import Repository, SQLiteRepository
from .errors import install_error_handlers
from .schemas import (
    AddCardIn,
    AdjustBalanceIn,
    CardOut,
    ExecuteIn,
    ExecuteOut,
    GoalIn,
    GoalOut,
    GoalPatch,
    HealthOut,
    LedgerEntryOut,
    PlanOut,
    PlanRequest,
    PlanSetOut,
    ProfileIn,
    ProfileOut,
    ProgramOut,
    SetBalanceIn,
    StepOut,
    ValueOut,
    WalletOut,
    WorldIssueOut,
    WorldOut,
    WorldValidateOut,
)

#: Default database location (DATA_MODEL "SQLite entities"); override with
#: ``POINTSMAX_DB`` or by constructing the app with your own repository.
DEFAULT_DB_PATH = Path.home() / ".pointsmax" / "pointsmax.db"


def default_db_path() -> Path:
    override = os.environ.get("POINTSMAX_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def utc_now() -> str:
    """ISO-8601 UTC timestamp — the only clock read in the whole package."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today(value: date | None) -> date:
    return value if value is not None else date.today()


def _at(value: str | None) -> str:
    return value if value is not None else utc_now()


def get_service(request: Request) -> PointsMaxService:
    service = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - only when the app is misconstructed
        raise RuntimeError("no PointsMaxService is attached to this app")
    return service


def _params(service: PointsMaxService, request: PlanRequest) -> PlanParams:
    base = service.default_params
    return PlanParams(
        top_k=request.top_k if request.top_k is not None else base.top_k,
        max_hops=request.max_hops if request.max_hops is not None else base.max_hops,
        max_expansions=(
            request.max_expansions if request.max_expansions is not None else base.max_expansions
        ),
        slack_days=request.slack_days if request.slack_days is not None else base.slack_days,
        promo_days=request.promo_days if request.promo_days is not None else base.promo_days,
        stale_days=request.stale_days if request.stale_days is not None else base.stale_days,
    )


def _spec_from_request(payload: GoalIn) -> GoalSpec:
    """Build a structured :class:`GoalSpec` from an explicit (non-text) request."""
    if payload.kind is None:
        raise InvalidGoal("a goal needs either 'text' or 'kind'")
    if payload.kind is GoalKind.CASH:
        return cash_goal(programs=payload.cash_programs, max_points=payload.cash_max_points)
    if payload.month is None:
        raise InvalidGoal(f"{payload.kind} goals require 'month' (YYYY-MM)")
    if payload.kind is GoalKind.FLIGHT:
        if not payload.origin_city or not payload.dest_city:
            raise InvalidGoal("flight goals require 'origin_city' and 'dest_city'")
        return flight_goal(
            origin_city=payload.origin_city,
            dest_city=payload.dest_city,
            month=payload.month,
            cabin=payload.cabin,
            round_trip=bool(payload.round_trip),
            passengers=payload.passengers or 1,
            book_by=payload.book_by,
        )
    if not payload.city or not payload.nights:
        raise InvalidGoal("stay goals require 'city' and 'nights'")
    return stay_goal(
        city=payload.city,
        nights=payload.nights,
        month=payload.month,
        book_by=payload.book_by,
    )


def create_app(service: PointsMaxService | None = None) -> FastAPI:
    """Build the app.  Pass a service for tests; otherwise SQLite + the committed world."""
    app = FastAPI(
        title="PointsMax",
        version="0.1.0",
        summary="Ranked, step-by-step credit-card points redemption plans with honest math.",
    )
    if service is None:
        provider = CommittedWorldProvider()
        path = default_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        repo: Repository = SQLiteRepository(path)
        service = PointsMaxService(repo, provider.load(), provider=provider)
    app.state.service = service
    install_error_handlers(app)
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    # -- health & world ----------------------------------------------------

    @app.get("/health", response_model=HealthOut, tags=["world"])
    def health(service: PointsMaxService = Depends(get_service)) -> HealthOut:
        return HealthOut(
            status="ok",
            world_version=service.world.version.version,
            world_hash=service.world.version.content_hash,
        )

    @app.get("/world", response_model=WorldOut, tags=["world"])
    def world_info(
        today: date | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> WorldOut:
        return WorldOut.from_info(service.world_info(_today(today)))

    @app.post("/world/validate", response_model=WorldValidateOut, tags=["world"])
    def world_validate(service: PointsMaxService = Depends(get_service)) -> WorldValidateOut:
        issues = service.validate()
        return WorldValidateOut(
            valid=not issues,
            issue_count=len(issues),
            issues=[WorldIssueOut(code=i.code, message=i.message) for i in issues],
        )

    # -- profile -----------------------------------------------------------

    @app.get("/profile", response_model=ProfileOut, tags=["profile"])
    def get_profile(service: PointsMaxService = Depends(get_service)) -> ProfileOut:
        return ProfileOut.from_model(service.get_profile())

    @app.put("/profile", response_model=ProfileOut, tags=["profile"])
    def put_profile(
        payload: ProfileIn = Body(default_factory=ProfileIn),
        service: PointsMaxService = Depends(get_service),
    ) -> ProfileOut:
        return ProfileOut.from_model(
            service.set_profile(
                display_name=payload.display_name,
                home_city=payload.home_city,
                default_passengers=payload.default_passengers,
                at=_at(payload.at),
            )
        )

    # -- catalog -----------------------------------------------------------

    @app.get("/cards", response_model=list[CardOut], tags=["catalog"])
    def list_cards(
        issuer: str | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> list[CardOut]:
        return [CardOut.from_model(c) for c in service.list_card_products(issuer)]

    @app.get("/programs", response_model=list[ProgramOut], tags=["catalog"])
    def list_programs(
        today: date | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> list[ProgramOut]:
        return [ProgramOut.from_view(v) for v in service.list_programs(_today(today))]

    @app.get("/programs/{program_id}", response_model=ProgramOut, tags=["catalog"])
    def get_program(
        program_id: str,
        today: date | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> ProgramOut:
        return ProgramOut.from_view(service.get_program(program_id, _today(today)))

    # -- wallet ------------------------------------------------------------

    @app.get("/wallet", response_model=WalletOut, tags=["wallet"])
    def get_wallet(
        today: date | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> WalletOut:
        return WalletOut.from_view(service.wallet_view(_today(today)))

    @app.post(
        "/wallet/cards",
        response_model=CardOut,
        status_code=status.HTTP_201_CREATED,
        tags=["wallet"],
    )
    def add_card(
        payload: AddCardIn,
        service: PointsMaxService = Depends(get_service),
    ) -> CardOut:
        return CardOut.from_model(service.add_card(payload.card_product_id, at=_at(payload.at)))

    @app.delete(
        "/wallet/cards/{card_product_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["wallet"],
    )
    def remove_card(card_product_id: str, service: PointsMaxService = Depends(get_service)) -> None:
        service.remove_card(card_product_id)

    @app.put("/wallet/balances/{program_id}", response_model=LedgerEntryOut, tags=["wallet"])
    def set_balance(
        program_id: str,
        payload: SetBalanceIn,
        service: PointsMaxService = Depends(get_service),
    ) -> LedgerEntryOut:
        entry = service.set_balance(
            program_id, payload.points, at=_at(payload.at), note=payload.note
        )
        return LedgerEntryOut.from_model(entry)

    @app.post(
        "/wallet/balances/{program_id}/adjust", response_model=LedgerEntryOut, tags=["wallet"]
    )
    def adjust_balance(
        program_id: str,
        payload: AdjustBalanceIn,
        service: PointsMaxService = Depends(get_service),
    ) -> LedgerEntryOut:
        entry = service.adjust_balance(
            program_id, payload.delta, at=_at(payload.at), note=payload.reason
        )
        return LedgerEntryOut.from_model(entry)

    @app.get("/wallet/ledger", response_model=list[LedgerEntryOut], tags=["wallet"])
    def get_ledger(
        program: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1),
        service: PointsMaxService = Depends(get_service),
    ) -> list[LedgerEntryOut]:
        return [LedgerEntryOut.from_model(e) for e in service.ledger(program, limit)]

    # -- valuation (FR-13) -------------------------------------------------

    @app.get("/valuations", response_model=list[ValueOut], tags=["valuation"])
    def valuations(
        today: date | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> list[ValueOut]:
        return [ValueOut.from_model(v) for v in service.valuations(_today(today))]

    @app.get("/valuations/{program_id}", response_model=ValueOut, tags=["valuation"])
    def value_of(
        program_id: str,
        points: int = Query(ge=0),
        today: date | None = Query(default=None),
        service: PointsMaxService = Depends(get_service),
    ) -> ValueOut:
        return ValueOut.from_model(service.value(program_id, points, _today(today)))

    # -- goals -------------------------------------------------------------

    @app.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED, tags=["goals"])
    def create_goal(
        payload: GoalIn,
        service: PointsMaxService = Depends(get_service),
    ) -> GoalOut:
        at = _at(payload.at)
        if payload.text is not None:
            goal = service.create_goal_from_text(payload.text, today=_today(payload.today), at=at)
        else:
            goal = service.create_goal(_spec_from_request(payload), at=at)
        return GoalOut.from_model(goal)

    @app.get("/goals", response_model=list[GoalOut], tags=["goals"])
    def list_goals(service: PointsMaxService = Depends(get_service)) -> list[GoalOut]:
        return [GoalOut.from_model(g) for g in service.list_goals()]

    @app.get("/goals/{goal_id}", response_model=GoalOut, tags=["goals"])
    def get_goal(goal_id: int, service: PointsMaxService = Depends(get_service)) -> GoalOut:
        return GoalOut.from_model(service.get_goal(goal_id))

    @app.patch("/goals/{goal_id}", response_model=GoalOut, tags=["goals"])
    def patch_goal(
        goal_id: int,
        payload: GoalPatch,
        service: PointsMaxService = Depends(get_service),
    ) -> GoalOut:
        return GoalOut.from_model(service.set_goal_status(goal_id, payload.status))

    # -- plans -------------------------------------------------------------

    @app.post(
        "/goals/{goal_id}/plans",
        response_model=PlanSetOut,
        status_code=status.HTTP_201_CREATED,
        tags=["plans"],
    )
    def compute_plans(
        goal_id: int,
        payload: PlanRequest = Body(default_factory=PlanRequest),
        service: PointsMaxService = Depends(get_service),
    ) -> PlanSetOut:
        plan_set = service.compute_plans(
            goal_id,
            today=_today(payload.today),
            at=_at(payload.at),
            params=_params(service, payload),
        )
        return PlanSetOut.from_model(plan_set)

    @app.get("/goals/{goal_id}/plans", response_model=list[PlanSetOut], tags=["plans"])
    def list_plan_sets(
        goal_id: int,
        latest: int = Query(default=0, ge=0, le=1),
        service: PointsMaxService = Depends(get_service),
    ) -> list[PlanSetOut]:
        if latest:
            return [PlanSetOut.from_model(service.latest_plan_set(goal_id))]
        return [PlanSetOut.from_model(ps) for ps in service.list_plan_sets(goal_id)]

    @app.get("/plan-sets/{plan_set_id}", response_model=PlanSetOut, tags=["plans"])
    def get_plan_set(
        plan_set_id: int, service: PointsMaxService = Depends(get_service)
    ) -> PlanSetOut:
        return PlanSetOut.from_model(service.get_plan_set(plan_set_id))

    @app.get("/plans/{plan_id}", response_model=PlanOut, tags=["plans"])
    def get_plan(plan_id: int, service: PointsMaxService = Depends(get_service)) -> PlanOut:
        plan = service.get_plan(plan_id)
        assert plan.plan_set_id is not None
        plan_set = service.get_plan_set(plan.plan_set_id)
        return PlanOut.from_model(plan, plan_set.disclaimer)

    @app.post("/plans/{plan_id}/steps/{seq}/execute", response_model=ExecuteOut, tags=["plans"])
    def execute_step(
        plan_id: int,
        seq: int,
        payload: ExecuteIn = Body(default_factory=ExecuteIn),
        service: PointsMaxService = Depends(get_service),
    ) -> ExecuteOut:
        step, entries = service.execute_step(
            plan_id,
            seq,
            at=_at(payload.at),
            confirm_irreversible=payload.confirm_irreversible,
        )
        return ExecuteOut(
            step=StepOut.from_model(step),
            entries=[LedgerEntryOut.from_model(e) for e in entries],
            balances=service.repo.balances(),
        )


__all__ = ["create_app", "default_db_path", "get_service", "utc_now"]
