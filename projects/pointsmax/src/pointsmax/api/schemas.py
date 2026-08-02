"""Request and response schemas for the REST surface (FR-14).

Pydantic v2 throughout.  Response models are built by ``from_*`` constructors so
the route handlers stay one-liners and no business rule leaks into the API.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    Cabin,
    CardProduct,
    CashoutOption,
    GoalKind,
    GoalStatus,
    LedgerEntry,
    Plan,
    PlanSet,
    PlanStep,
    Profile,
    TransferEdge,
    ValueBreakdown,
    Verdict,
)
from ..service import BalanceView, ProgramView, WalletView, WorldInfo


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ErrorOut(_Out):
    """The single error envelope every non-2xx response uses."""

    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Health & world
# --------------------------------------------------------------------------


class HealthOut(_Out):
    status: Literal["ok"]
    world_version: str
    world_hash: str


class WorldOut(_Out):
    version: str
    as_of: date
    content_hash: str
    valuations_as_of: date
    days_old: int
    stale: bool
    stale_days: int
    counts: dict[str, int]

    @classmethod
    def from_info(cls, info: WorldInfo) -> WorldOut:
        return cls(
            version=info.version,
            as_of=info.as_of,
            content_hash=info.content_hash,
            valuations_as_of=info.valuations_as_of,
            days_old=info.days_old,
            stale=info.stale,
            stale_days=info.stale_days,
            counts=dict(info.counts),
        )


class WorldIssueOut(_Out):
    code: str
    message: str


class WorldValidateOut(_Out):
    valid: bool
    issue_count: int
    issues: list[WorldIssueOut]


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


class ProfileOut(_Out):
    display_name: str
    home_city: str | None
    default_passengers: int
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, profile: Profile) -> ProfileOut:
        return cls(
            display_name=profile.display_name,
            home_city=profile.home_city,
            default_passengers=profile.default_passengers,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    home_city: str | None = None
    default_passengers: int | None = Field(default=None, ge=1, le=8)
    at: str | None = None


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


class CardOut(_Out):
    id: str
    issuer: str
    name: str
    program_id: str
    enables_transfer: bool
    annual_fee_cents: int

    @classmethod
    def from_model(cls, card: CardProduct) -> CardOut:
        return cls(
            id=card.id,
            issuer=card.issuer,
            name=card.name,
            program_id=card.program_id,
            enables_transfer=card.enables_transfer,
            annual_fee_cents=card.annual_fee_cents,
        )


class EdgeOut(_Out):
    id: str
    from_program: str
    to_program: str
    ratio_from: int
    ratio_to: int
    min_from: int
    increment_from: int
    fee_mcpp: int
    fee_cap_cents: int | None
    time_days: int
    bonus_per_from: int | None
    bonus_to: int | None
    valid_to: date | None

    @classmethod
    def from_model(cls, edge: TransferEdge) -> EdgeOut:
        return cls(
            id=edge.id,
            from_program=edge.from_program,
            to_program=edge.to_program,
            ratio_from=edge.ratio_from,
            ratio_to=edge.ratio_to,
            min_from=edge.min_from,
            increment_from=edge.increment_from,
            fee_mcpp=edge.fee_mcpp,
            fee_cap_cents=edge.fee_cap_cents,
            time_days=edge.time_days,
            bonus_per_from=edge.bonus_per_from,
            bonus_to=edge.bonus_to,
            valid_to=edge.valid_to,
        )


class OptionOut(_Out):
    id: str
    program_id: str
    method: str
    cpp_milli: int
    min_points: int
    increment: int
    is_cash: bool
    requires_card: str | None
    valid_to: date | None

    @classmethod
    def from_model(cls, option: CashoutOption) -> OptionOut:
        return cls(
            id=option.id,
            program_id=option.program_id,
            method=str(option.method),
            cpp_milli=option.cpp_milli,
            min_points=option.min_points,
            increment=option.increment,
            is_cash=option.is_cash,
            requires_card=option.requires_card,
            valid_to=option.valid_to,
        )


class ProgramOut(_Out):
    id: str
    name: str
    kind: str
    cpp_milli: int
    as_of: date
    active_edges_out: list[EdgeOut]
    active_edges_in: list[EdgeOut]
    active_options: list[OptionOut]

    @classmethod
    def from_view(cls, view: ProgramView) -> ProgramOut:
        return cls(
            id=view.program.id,
            name=view.program.name,
            kind=str(view.program.kind),
            cpp_milli=view.cpp_milli,
            as_of=view.as_of,
            active_edges_out=[EdgeOut.from_model(e) for e in view.edges_out],
            active_edges_in=[EdgeOut.from_model(e) for e in view.edges_in],
            active_options=[OptionOut.from_model(o) for o in view.options],
        )


# --------------------------------------------------------------------------
# Wallet & valuation
# --------------------------------------------------------------------------


class ValueOut(_Out):
    """FR-13: the three honest numbers, with provenance."""

    program_id: str
    points: int
    baseline_value_cents: int
    baseline_cpp_milli: int
    as_of: date
    cash_floor_cents: int
    cash_floor_option_id: str | None
    travel_floor_cents: int
    travel_floor_option_id: str | None

    @classmethod
    def from_model(cls, value: ValueBreakdown) -> ValueOut:
        return cls(**value.model_dump(mode="python"))


class BalanceOut(_Out):
    program_id: str
    program_name: str
    points: int
    value: ValueOut

    @classmethod
    def from_view(cls, view: BalanceView) -> BalanceOut:
        return cls(
            program_id=view.program_id,
            program_name=view.program_name,
            points=view.points,
            value=ValueOut.from_model(view.value),
        )


class WalletOut(_Out):
    cards: list[CardOut]
    balances: list[BalanceOut]
    baseline_total_cents: int
    cash_floor_total_cents: int
    travel_floor_total_cents: int

    @classmethod
    def from_view(cls, view: WalletView) -> WalletOut:
        return cls(
            cards=[CardOut.from_model(c) for c in view.cards],
            balances=[BalanceOut.from_view(b) for b in view.balances],
            baseline_total_cents=view.baseline_total_cents,
            cash_floor_total_cents=view.cash_floor_total_cents,
            travel_floor_total_cents=view.travel_floor_total_cents,
        )


class AddCardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_product_id: str
    at: str | None = None


class SetBalanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: int = Field(ge=0)
    at: str | None = None
    note: str | None = None


class AdjustBalanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta: int
    reason: str | None = None
    at: str | None = None


class LedgerEntryOut(_Out):
    id: int | None
    program_id: str
    delta_points: int
    post_balance: int
    reason: str
    plan_step_id: int | None
    note: str | None
    at: str

    @classmethod
    def from_model(cls, entry: LedgerEntry) -> LedgerEntryOut:
        return cls(
            id=entry.id,
            program_id=entry.program_id,
            delta_points=entry.delta_points,
            post_balance=entry.post_balance,
            reason=str(entry.reason),
            plan_step_id=entry.plan_step_id,
            note=entry.note,
            at=entry.at,
        )


# --------------------------------------------------------------------------
# Goals
# --------------------------------------------------------------------------


class GoalIn(BaseModel):
    """Either a structured goal or ``{"text": ...}`` for the FR-5 parser."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    kind: GoalKind | None = None
    origin_city: str | None = None
    dest_city: str | None = None
    cabin: Cabin | None = None
    round_trip: bool | None = None
    passengers: int | None = Field(default=None, ge=1, le=8)
    city: str | None = None
    nights: int | None = Field(default=None, ge=1, le=30)
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    book_by: date | None = None
    cash_programs: list[str] | None = None
    cash_max_points: dict[str, int] | None = None
    today: date | None = None
    at: str | None = None


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: GoalStatus


class GoalOut(_Out):
    id: int | None
    kind: str
    status: str
    raw_text: str | None
    origin_city: str | None
    dest_city: str | None
    cabin: str | None
    round_trip: bool | None
    passengers: int | None
    city: str | None
    nights: int | None
    travel_window_start: date | None
    travel_window_end: date | None
    book_by: date | None
    cash_programs: list[str] | None
    cash_max_points: dict[str, int] | None
    created_at: str

    @classmethod
    def from_model(cls, goal: Any) -> GoalOut:
        return cls(
            id=goal.id,
            kind=str(goal.kind),
            status=str(goal.status),
            raw_text=goal.raw_text,
            origin_city=goal.origin_city,
            dest_city=goal.dest_city,
            cabin=str(goal.cabin) if goal.cabin else None,
            round_trip=goal.round_trip,
            passengers=goal.passengers,
            city=goal.city,
            nights=goal.nights,
            travel_window_start=goal.travel_window_start,
            travel_window_end=goal.travel_window_end,
            book_by=goal.book_by,
            cash_programs=goal.cash_programs,
            cash_max_points=goal.cash_max_points,
            created_at=goal.created_at,
        )


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    today: date | None = None
    at: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=25)
    max_hops: int | None = Field(default=None, ge=0, le=4)
    max_expansions: int | None = Field(default=None, ge=1)
    slack_days: int | None = Field(default=None, ge=0)
    promo_days: int | None = Field(default=None, ge=0)
    stale_days: int | None = Field(default=None, ge=0)


class CaveatOut(_Out):
    code: str
    params: dict[str, Any]
    text: str


class StepOut(_Out):
    id: int | None
    seq: int
    kind: str
    hop_index: int
    from_program: str | None
    to_program: str | None
    edge_id: str | None
    offer_id: str | None
    cashout_id: str | None
    points_sent: int
    points_delivered: int | None
    fees_cents: int
    eta_days: int
    irreversible: bool
    explanation: str
    executed_at: str | None

    @classmethod
    def from_model(cls, step: PlanStep) -> StepOut:
        return cls(
            id=step.id,
            seq=step.seq,
            kind=str(step.kind),
            hop_index=step.hop_index,
            from_program=step.from_program,
            to_program=step.to_program,
            edge_id=step.edge_id,
            offer_id=step.offer_id,
            cashout_id=step.cashout_id,
            points_sent=step.points_sent,
            points_delivered=step.points_delivered,
            fees_cents=step.fees_cents,
            eta_days=step.eta_days,
            irreversible=step.irreversible,
            explanation=step.explanation,
            executed_at=step.executed_at,
        )


class PlanOut(_Out):
    id: int | None
    plan_set_id: int | None
    rank: int
    is_comparator: bool
    gross_value_cents: int
    cash_outlay_cents: int
    points_cost_cents: int
    net_value_cents: int
    cash_received_cents: int | None
    realized_cpp_milli: int | None
    points_spent: dict[str, int]
    feasible_in_days: int
    signature: str
    caveats: list[CaveatOut]
    steps: list[StepOut]
    disclaimer: str

    @classmethod
    def from_model(cls, plan: Plan, disclaimer: str) -> PlanOut:
        return cls(
            id=plan.id,
            plan_set_id=plan.plan_set_id,
            rank=plan.rank,
            is_comparator=plan.is_comparator,
            gross_value_cents=plan.gross_value_cents,
            cash_outlay_cents=plan.cash_outlay_cents,
            points_cost_cents=plan.points_cost_cents,
            net_value_cents=plan.net_value_cents,
            cash_received_cents=plan.cash_received_cents,
            realized_cpp_milli=plan.realized_cpp_milli,
            points_spent=dict(plan.points_spent),
            feasible_in_days=plan.feasible_in_days,
            signature=plan.signature,
            caveats=[
                CaveatOut(code=str(c.code), params=c.params, text=c.text) for c in plan.caveats
            ],
            steps=[StepOut.from_model(s) for s in plan.steps],
            disclaimer=disclaimer,
        )


class PlanSetOut(_Out):
    id: int | None
    goal_id: int | None
    world_version: str
    world_hash: str
    today: date
    params: dict[str, int]
    expansions: int
    verdict: Verdict
    recommended_plan_id: int | None
    disclaimer: str
    created_at: str
    plans: list[PlanOut]

    @classmethod
    def from_model(cls, plan_set: PlanSet) -> PlanSetOut:
        return cls(
            id=plan_set.id,
            goal_id=plan_set.goal_id,
            world_version=plan_set.world_version,
            world_hash=plan_set.world_hash,
            today=plan_set.today,
            params=plan_set.params.as_dict(),
            expansions=plan_set.expansions,
            verdict=plan_set.verdict,
            recommended_plan_id=plan_set.recommended_plan_id,
            disclaimer=plan_set.disclaimer,
            created_at=plan_set.created_at,
            plans=[PlanOut.from_model(p, plan_set.disclaimer) for p in plan_set.plans],
        )


class ExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: str | None = None
    confirm_irreversible: bool = False


class ExecuteOut(_Out):
    step: StepOut
    entries: list[LedgerEntryOut]
    balances: dict[str, int]
