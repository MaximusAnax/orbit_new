"""Pydantic v2 domain models for PointsMax.

Every entity in ``docs/DATA_MODEL.md`` lives here, with its stated invariants
enforced by field constraints and model validators.  Two storage classes are
represented:

* **Committed world** (read-only, versioned under ``data/world/``): programs,
  card products, transfer edges, cashout options, valuations, award offers,
  reference fares, gazetteer, version.  Frozen models.
* **User state** (SQLite / in-memory): profile, wallet cards, ledger entries,
  goals, plan sets, plans, plan steps.

Conventions (FR-8/FR-16): money in integer **cents**, valuations in integer
**milli-cents per point** (``cpp_milli``), dates as ``datetime.date``,
timestamps as ISO-8601 strings supplied by callers.  No floats anywhere.
"""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

# --------------------------------------------------------------------------
# Canonical JSON (DATA_MODEL.md, "Canonical JSON")
# --------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Canonical JSON string: sorted keys, no whitespace, unicode preserved."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(obj: Any) -> bytes:
    """UTF-8 encoding of :func:`canonical_json`."""
    return canonical_json(obj).encode("utf-8")


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class ProgramKind(StrEnum):
    BANK = "bank"
    AIRLINE = "airline"
    HOTEL = "hotel"


class Cabin(StrEnum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class OfferKind(StrEnum):
    FLIGHT = "flight"
    STAY = "stay"


class GoalKind(StrEnum):
    FLIGHT = "flight"
    STAY = "stay"
    CASH = "cash"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    FULFILLED = "fulfilled"
    DROPPED = "dropped"


class CashoutMethod(StrEnum):
    STATEMENT_CREDIT = "statement_credit"
    BANK_DEPOSIT = "bank_deposit"
    PORTAL_TRAVEL = "portal_travel"
    GIFT_CARD = "gift_card"


class StepKind(StrEnum):
    TRANSFER = "transfer"
    BOOK_AWARD = "book_award"
    BOOK_PORTAL = "book_portal"
    REDEEM_CASH = "redeem_cash"


class LedgerReason(StrEnum):
    SET = "set"
    ADJUST = "adjust"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    TRANSFER_BONUS = "transfer_bonus"
    AWARD_REDEEM = "award_redeem"
    CASH_REDEEM = "cash_redeem"
    PORTAL_REDEEM = "portal_redeem"


class Verdict(StrEnum):
    BOOK_WITH_POINTS = "book_with_points"
    PAY_CASH_KEEP_POINTS = "pay_cash_keep_points"
    INSUFFICIENT_POINTS = "insufficient_points"
    NO_MATCHING_AWARD = "no_matching_award"
    CASH_PLAN = "cash_plan"


class CaveatCode(StrEnum):
    """Declaration order is the FR-10 caveat emission order."""

    IRREVERSIBLE_TRANSFER = "irreversible_transfer"
    TRANSFER_TIME_RISK = "transfer_time_risk"
    STRANDED_POINTS = "stranded_points"
    PROMO_EXPIRING = "promo_expiring"
    BELOW_BASELINE = "below_baseline"
    STALE_WORLD = "stale_world"
    SEATS_LIMITED = "seats_limited"


#: FR-10 caveat ordering: declaration order of :class:`CaveatCode`.
CAVEAT_ORDER: dict[CaveatCode, int] = {code: i for i, code in enumerate(CaveatCode)}

#: FR-9 canonical step order: ``transfer=0, book_award=1, book_portal=2, redeem_cash=3``.
STEP_KIND_RANK: dict[StepKind, int] = {
    StepKind.TRANSFER: 0,
    StepKind.BOOK_AWARD: 1,
    StepKind.BOOK_PORTAL: 2,
    StepKind.REDEEM_CASH: 3,
}

#: Ledger reasons that must reference a plan step (DATA_MODEL LedgerEntry invariant).
PLAN_LINKED_REASONS: frozenset[LedgerReason] = frozenset(
    {
        LedgerReason.TRANSFER_OUT,
        LedgerReason.TRANSFER_IN,
        LedgerReason.TRANSFER_BONUS,
        LedgerReason.AWARD_REDEEM,
        LedgerReason.CASH_REDEEM,
        LedgerReason.PORTAL_REDEEM,
    }
)

#: Normative liquidity of each cashout method (DATA_MODEL "Cashout liquidity").
#: Derived constant of the method, never a field in the dataset.
CASHOUT_IS_CASH: dict[CashoutMethod, bool] = {
    CashoutMethod.STATEMENT_CREDIT: True,
    CashoutMethod.BANK_DEPOSIT: True,
    CashoutMethod.PORTAL_TRAVEL: False,
    CashoutMethod.GIFT_CARD: False,
}


def is_cash_method(method: CashoutMethod) -> bool:
    """True when redeeming through ``method`` produces liquid cash (FR-12/FR-13)."""
    return CASHOUT_IS_CASH[CashoutMethod(method)]


#: FR-16(a): the fixed disclaimer carried by every plan payload.
DISCLAIMER = (
    "estimates from a versioned dataset, not financial advice; verify ratios and "
    "pricing with the program before moving points"
)

# --------------------------------------------------------------------------
# Constrained scalar types
# --------------------------------------------------------------------------

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]*$", min_length=1, max_length=64)]
CityCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
PlaceCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
NonNegInt = Annotated[int, Field(ge=0)]
PosInt = Annotated[int, Field(ge=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Mutable(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Committed world entities
# --------------------------------------------------------------------------


class Program(_Frozen):
    """A points currency (``programs.json``)."""

    id: Slug
    name: str = Field(min_length=1)
    kind: ProgramKind
    source_note: str = ""


class CardProduct(_Frozen):
    """A card product held by the user (``cards.json``)."""

    id: Slug
    issuer: str = Field(min_length=1)
    name: str = Field(min_length=1)
    program_id: Slug
    enables_transfer: bool
    annual_fee_cents: NonNegInt = 0
    source_note: str = ""


class TransferEdge(_Frozen):
    """A transfer relationship between two programs (``transfers.json``)."""

    id: Slug
    from_program: Slug
    to_program: Slug
    ratio_from: PosInt
    ratio_to: PosInt
    min_from: PosInt
    increment_from: PosInt
    fee_mcpp: NonNegInt = 0
    fee_cap_cents: NonNegInt | None = None
    time_days: NonNegInt = 0
    bonus_per_from: PosInt | None = None
    bonus_to: PosInt | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_note: str = ""

    @model_validator(mode="after")
    def _check(self) -> TransferEdge:
        if self.from_program == self.to_program:
            raise ValueError(f"transfer edge {self.id}: self-loop is not allowed")
        if (self.bonus_per_from is None) != (self.bonus_to is None):
            raise ValueError(
                f"transfer edge {self.id}: bonus_per_from and bonus_to must both be set or both null"
            )
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError(f"transfer edge {self.id}: valid_from is after valid_to")
        return self


class CashoutOption(_Frozen):
    """A non-transfer redemption of a currency (``cashouts.json``)."""

    id: Slug
    program_id: Slug
    method: CashoutMethod
    cpp_milli: PosInt
    min_points: PosInt = 1
    increment: PosInt = 1
    requires_card: Slug | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_note: str = ""

    @property
    def is_cash(self) -> bool:
        """Liquidity, derived from :data:`CASHOUT_IS_CASH` (never stored in data)."""
        return is_cash_method(self.method)

    @model_validator(mode="after")
    def _check(self) -> CashoutOption:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError(f"cashout option {self.id}: valid_from is after valid_to")
        return self


class Valuation(_Frozen):
    """Baseline value of a currency (``valuations.json``); exactly one per program."""

    program_id: Slug
    cpp_milli: PosInt
    as_of: date
    source_note: str = ""


class AwardOffer(_Frozen):
    """A curated award-availability snapshot (``awards.json``)."""

    id: Slug
    program_id: Slug
    kind: OfferKind
    origin: PlaceCode | None = None
    destination: PlaceCode | None = None
    cabin: Cabin | None = None
    round_trip: bool | None = None
    points_price: PosInt
    fees_cents: NonNegInt = 0
    city: PlaceCode | None = None
    travel_window_start: date
    travel_window_end: date
    seats_available: PosInt | None = None
    bookable_until: date | None = None
    source_note: str = ""

    @model_validator(mode="after")
    def _check(self) -> AwardOffer:
        if self.travel_window_start > self.travel_window_end:
            raise ValueError(f"award offer {self.id}: travel window start is after end")
        if self.kind is OfferKind.FLIGHT:
            missing = [
                f
                for f in ("origin", "destination", "round_trip")
                if getattr(self, f) is None
            ]
            if missing:
                raise ValueError(
                    f"award offer {self.id}: flight offers require {', '.join(missing)}"
                )
            if self.cabin is None:
                raise ValueError(f"award offer {self.id}: flight offers require a cabin")
            if self.city is not None:
                raise ValueError(f"award offer {self.id}: flight offers must not set city")
            if self.origin == self.destination:
                raise ValueError(f"award offer {self.id}: origin equals destination")
        else:
            if self.city is None:
                raise ValueError(f"award offer {self.id}: stay offers require city")
            extra = [
                f
                for f in ("origin", "destination", "cabin", "round_trip")
                if getattr(self, f) is not None
            ]
            if extra:
                raise ValueError(
                    f"award offer {self.id}: stay offers must not set {', '.join(extra)}"
                )
        return self


class ReferenceFare(_Frozen):
    """A "reasonable cash price" used as the value yardstick (``reference_fares.json``)."""

    id: Slug
    kind: OfferKind
    origin_city: CityCode | None = None
    dest_city: CityCode | None = None
    cabin: Cabin | None = None
    round_trip: bool | None = None
    city: CityCode | None = None
    month: Month
    fare_cents: PosInt
    source_note: str = ""

    @model_validator(mode="after")
    def _check(self) -> ReferenceFare:
        if self.kind is OfferKind.FLIGHT:
            if None in (self.origin_city, self.dest_city, self.cabin, self.round_trip):
                raise ValueError(
                    f"reference fare {self.id}: flight rows require origin_city, "
                    "dest_city, cabin and round_trip"
                )
            if self.city is not None:
                raise ValueError(f"reference fare {self.id}: flight rows must not set city")
        else:
            if self.city is None:
                raise ValueError(f"reference fare {self.id}: stay rows require city")
            if any(
                getattr(self, f) is not None
                for f in ("origin_city", "dest_city", "cabin", "round_trip")
            ):
                raise ValueError(f"reference fare {self.id}: stay rows must not set flight fields")
        return self


class GazetteerEntry(_Frozen):
    """A city and the airports/aliases that resolve to it (``gazetteer.json``)."""

    city_code: CityCode
    name: str = Field(min_length=1)
    airports: list[PlaceCode] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> GazetteerEntry:
        for alias in self.aliases:
            if alias != alias.lower() or not alias.strip():
                raise ValueError(
                    f"gazetteer {self.city_code}: aliases must be lowercase and non-blank"
                )
        return self


class WorldVersion(_Frozen):
    """Dataset version pin (``version.json``)."""

    version: str = Field(min_length=1)
    as_of: date
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class World(BaseModel):
    """The whole committed rewards world, loaded into memory by a ``WorldProvider``."""

    model_config = ConfigDict(extra="forbid")

    version: WorldVersion
    programs: list[Program]
    cards: list[CardProduct]
    edges: list[TransferEdge]
    cashouts: list[CashoutOption]
    valuations: list[Valuation]
    offers: list[AwardOffer]
    reference_fares: list[ReferenceFare]
    gazetteer: list[GazetteerEntry]

    _program_by_id: dict[str, Program] = PrivateAttr(default_factory=dict)
    _card_by_id: dict[str, CardProduct] = PrivateAttr(default_factory=dict)
    _edge_by_id: dict[str, TransferEdge] = PrivateAttr(default_factory=dict)
    _cashout_by_id: dict[str, CashoutOption] = PrivateAttr(default_factory=dict)
    _valuation_by_program: dict[str, Valuation] = PrivateAttr(default_factory=dict)
    _offer_by_id: dict[str, AwardOffer] = PrivateAttr(default_factory=dict)
    _fare_by_key: dict[tuple, ReferenceFare] = PrivateAttr(default_factory=dict)
    _city_by_code: dict[str, GazetteerEntry] = PrivateAttr(default_factory=dict)
    _city_by_place: dict[str, str] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        self._program_by_id = {p.id: p for p in self.programs}
        self._card_by_id = {c.id: c for c in self.cards}
        self._edge_by_id = {e.id: e for e in self.edges}
        self._cashout_by_id = {o.id: o for o in self.cashouts}
        self._valuation_by_program = {v.program_id: v for v in self.valuations}
        self._offer_by_id = {o.id: o for o in self.offers}
        self._fare_by_key = {_fare_key_of(f): f for f in self.reference_fares}
        self._city_by_code = {g.city_code: g for g in self.gazetteer}
        places: dict[str, str] = {}
        for g in self.gazetteer:
            places[g.city_code] = g.city_code
            for ap in g.airports:
                places[ap] = g.city_code
        self._city_by_place = places

    # -- lookups -----------------------------------------------------------

    def program(self, program_id: str) -> Program:
        return self._program_by_id[program_id]

    def has_program(self, program_id: str) -> bool:
        return program_id in self._program_by_id

    def card(self, card_id: str) -> CardProduct:
        return self._card_by_id[card_id]

    def has_card(self, card_id: str) -> bool:
        return card_id in self._card_by_id

    def edge(self, edge_id: str) -> TransferEdge:
        return self._edge_by_id[edge_id]

    def cashout(self, option_id: str) -> CashoutOption:
        return self._cashout_by_id[option_id]

    def offer(self, offer_id: str) -> AwardOffer:
        return self._offer_by_id[offer_id]

    def valuation(self, program_id: str) -> Valuation:
        return self._valuation_by_program[program_id]

    def mcpp(self, program_id: str) -> int:
        """Baseline milli-cents per point for ``program_id``."""
        return self._valuation_by_program[program_id].cpp_milli

    def mcpp_map(self) -> dict[str, int]:
        return {v.program_id: v.cpp_milli for v in self.valuations}

    def city_of(self, place_code: str | None) -> str | None:
        """Resolve an airport or city code to its gazetteer city code."""
        if place_code is None:
            return None
        return self._city_by_place.get(place_code)

    def city(self, city_code: str) -> GazetteerEntry | None:
        return self._city_by_code.get(city_code)

    def find_fare(
        self,
        kind: OfferKind,
        *,
        month: str,
        origin_city: str | None = None,
        dest_city: str | None = None,
        cabin: Cabin | None = None,
        round_trip: bool | None = None,
        city: str | None = None,
    ) -> int | None:
        """Reference fare in cents, or None when the world has no such row (FR-8)."""
        if kind is OfferKind.FLIGHT:
            key = ("flight", origin_city, dest_city, str(cabin) if cabin else None, bool(round_trip), month)
        else:
            key = ("stay", city, month)
        fare = self._fare_by_key.get(key)
        return fare.fare_cents if fare else None

    def min_valuation_as_of(self) -> date:
        return min(v.as_of for v in self.valuations)


def _fare_key_of(f: ReferenceFare) -> tuple:
    if f.kind is OfferKind.FLIGHT:
        return (
            "flight",
            f.origin_city,
            f.dest_city,
            str(f.cabin) if f.cabin else None,
            bool(f.round_trip),
            f.month,
        )
    return ("stay", f.city, f.month)


# --------------------------------------------------------------------------
# User state
# --------------------------------------------------------------------------


class Profile(_Mutable):
    """Singleton user profile (table ``profile``)."""

    id: int = Field(default=1, ge=1, le=1)
    display_name: str = "me"
    home_city: CityCode | None = None
    default_passengers: int = Field(default=1, ge=1, le=8)
    created_at: str = ""
    updated_at: str = ""


class WalletCard(_Frozen):
    """A card product the user holds (table ``wallet_card``)."""

    id: int | None = None
    card_product_id: Slug
    added_at: str


class LedgerEntry(_Frozen):
    """An append-only balance movement (table ``ledger_entry``, FR-2)."""

    id: int | None = None
    program_id: Slug
    delta_points: int
    post_balance: NonNegInt
    reason: LedgerReason
    plan_step_id: int | None = None
    note: str | None = None
    at: str

    @model_validator(mode="after")
    def _check(self) -> LedgerEntry:
        linked = self.reason in PLAN_LINKED_REASONS
        if linked and self.plan_step_id is None:
            raise ValueError(f"ledger entry ({self.reason}) requires a plan_step_id")
        if not linked and self.plan_step_id is not None:
            raise ValueError(f"ledger entry ({self.reason}) must not reference a plan step")
        return self


class Wallet(_Mutable):
    """Engine-facing view of user state: cards held plus per-program balances."""

    cards: list[Slug] = Field(default_factory=list)
    balances: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Wallet:
        if len(set(self.cards)) != len(self.cards):
            raise ValueError("wallet cards must be unique per card product")
        for program_id, points in self.balances.items():
            if points < 0:
                raise ValueError(f"wallet balance for {program_id} is negative")
        return self

    def card_set(self) -> frozenset[str]:
        return frozenset(self.cards)

    def balance(self, program_id: str) -> int:
        return self.balances.get(program_id, 0)


class GoalSpec(_Mutable):
    """A validated user goal (FR-4).  Kind-specific nullability is enforced."""

    kind: GoalKind
    raw_text: str | None = None
    origin_city: CityCode | None = None
    dest_city: CityCode | None = None
    cabin: Cabin | None = None
    round_trip: bool | None = None
    passengers: int | None = Field(default=None, ge=1, le=8)
    city: CityCode | None = None
    nights: int | None = Field(default=None, ge=1, le=30)
    travel_window_start: date | None = None
    travel_window_end: date | None = None
    book_by: date | None = None
    cash_programs: list[Slug] | None = None
    cash_max_points: dict[str, int] | None = None

    @model_validator(mode="after")
    def _check(self) -> GoalSpec:
        flight_fields = ("origin_city", "dest_city", "round_trip", "passengers")
        stay_fields = ("city", "nights")
        cash_fields = ("cash_programs", "cash_max_points")
        if self.kind is GoalKind.FLIGHT:
            missing = [f for f in flight_fields if getattr(self, f) is None]
            if missing:
                raise ValueError(f"flight goal requires {', '.join(missing)}")
            _require_null(self, stay_fields + cash_fields, "flight goal")
            _require_window(self, "flight goal")
            if self.origin_city == self.dest_city:
                raise ValueError("flight goal origin equals destination")
        elif self.kind is GoalKind.STAY:
            missing = [f for f in stay_fields if getattr(self, f) is None]
            if missing:
                raise ValueError(f"stay goal requires {', '.join(missing)}")
            _require_null(self, flight_fields + cash_fields, "stay goal")
            _require_window(self, "stay goal")
        else:
            _require_null(
                self,
                flight_fields
                + stay_fields
                + ("travel_window_start", "travel_window_end", "cabin", "book_by"),
                "cash goal",
            )
            if self.cash_max_points is not None:
                for program_id, cap in self.cash_max_points.items():
                    if cap < 0:
                        raise ValueError(f"cash_max_points[{program_id}] must be >= 0")
        return self

    @property
    def travel_month(self) -> str | None:
        """``YYYY-MM`` of the goal's travel window — the FR-8 fare lookup key."""
        if self.travel_window_start is None:
            return None
        return f"{self.travel_window_start.year:04d}-{self.travel_window_start.month:02d}"

    def legs(self) -> list[tuple[str, str]]:
        """FR-7a legs: one for a one-way flight goal, two for a round trip."""
        if self.kind is not GoalKind.FLIGHT:
            return []
        assert self.origin_city and self.dest_city
        out = [(self.origin_city, self.dest_city)]
        if self.round_trip:
            out.append((self.dest_city, self.origin_city))
        return out


def _require_null(spec: GoalSpec, fields: tuple[str, ...], label: str) -> None:
    present = [f for f in fields if getattr(spec, f) is not None]
    if present:
        raise ValueError(f"{label} must not set {', '.join(present)}")


def _require_window(spec: GoalSpec, label: str) -> None:
    if spec.travel_window_start is None or spec.travel_window_end is None:
        raise ValueError(f"{label} requires travel_window_start and travel_window_end")
    if spec.travel_window_start > spec.travel_window_end:
        raise ValueError(f"{label} travel window start is after end")
    if (spec.travel_window_start.year, spec.travel_window_start.month) != (
        spec.travel_window_end.year,
        spec.travel_window_end.month,
    ):
        raise ValueError(
            f"{label} travel window must lie inside one calendar month "
            "(the FR-8 reference-fare lookup key)"
        )


class Goal(GoalSpec):
    """A persisted :class:`GoalSpec` with identity and lifecycle (table ``goal``)."""

    id: int | None = None
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: str = ""

    def spec(self) -> GoalSpec:
        return GoalSpec.model_validate(
            self.model_dump(exclude={"id", "status", "created_at"}, mode="python")
        )


class ParseError(_Frozen):
    """Structured failure from :class:`GoalParser` (FR-5)."""

    raw_text: str
    message: str
    missing: list[str] = Field(default_factory=list)
    ambiguous: list[str] = Field(default_factory=list)


class Caveat(_Frozen):
    """A typed warning attached to a plan (FR-10)."""

    code: CaveatCode
    params: dict[str, Any] = Field(default_factory=dict)
    text: str


class PlanStep(_Mutable):
    """One executable action inside a plan (table ``plan_step``)."""

    id: int | None = None
    plan_id: int | None = None
    seq: PosInt
    kind: StepKind
    hop_index: NonNegInt = 0
    from_program: Slug | None = None
    to_program: Slug | None = None
    edge_id: Slug | None = None
    offer_id: Slug | None = None
    cashout_id: Slug | None = None
    points_sent: NonNegInt
    points_delivered: int | None = None
    fees_cents: NonNegInt = 0
    eta_days: NonNegInt = 0
    irreversible: bool = False
    explanation: str = ""
    executed_at: str | None = None

    @model_validator(mode="after")
    def _check(self) -> PlanStep:
        if self.kind is StepKind.TRANSFER:
            if not (self.from_program and self.to_program and self.edge_id):
                raise ValueError("transfer step requires from_program, to_program and edge_id")
            if self.points_delivered is None:
                raise ValueError("transfer step requires points_delivered")
            if self.offer_id or self.cashout_id:
                raise ValueError("transfer step must not reference an offer or cashout option")
        else:
            if not self.from_program:
                raise ValueError(f"{self.kind} step requires from_program")
            if self.to_program or self.edge_id:
                raise ValueError(f"{self.kind} step must not set to_program or edge_id")
            if self.points_delivered is not None:
                raise ValueError(f"{self.kind} step must not set points_delivered")
            if self.kind is StepKind.BOOK_AWARD:
                if not self.offer_id or self.cashout_id:
                    raise ValueError("book_award step requires offer_id and no cashout_id")
            elif not self.cashout_id or self.offer_id:
                raise ValueError(f"{self.kind} step requires cashout_id and no offer_id")
        expected_irreversible = self.kind in (StepKind.TRANSFER, StepKind.BOOK_AWARD)
        if self.irreversible != expected_irreversible:
            raise ValueError(
                f"{self.kind} step irreversible flag must be {expected_irreversible}"
            )
        return self

    def canonical_tuple(self) -> list:
        """FR-9 canonical step tuple; nulls render as ``""`` / ``0``."""
        return [
            str(self.kind),
            self.from_program or "",
            self.to_program or "",
            self.edge_id or "",
            self.offer_id or "",
            self.cashout_id or "",
            self.points_sent,
            self.points_delivered or 0,
            self.fees_cents,
        ]

    def sort_key(self) -> tuple:
        """FR-9 canonical step order key."""
        return (
            STEP_KIND_RANK[self.kind],
            self.hop_index,
            self.from_program or "",
            self.to_program or "",
            self.edge_id or "",
            self.offer_id or "",
            self.cashout_id or "",
            self.points_sent,
        )


class Plan(_Mutable):
    """A ranked, fully-costed redemption plan (table ``plan``)."""

    id: int | None = None
    plan_set_id: int | None = None
    rank: PosInt
    is_comparator: bool = False
    gross_value_cents: int
    cash_outlay_cents: NonNegInt
    points_cost_cents: int
    net_value_cents: int
    cash_received_cents: int | None = None
    realized_cpp_milli: int | None = None
    points_spent: dict[str, int] = Field(default_factory=dict)
    feasible_in_days: NonNegInt = 0
    signature: str = ""
    caveats: list[Caveat] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)

    def canonical_form(self) -> list[list]:
        """The list of canonical step tuples in canonical step order (FR-9)."""
        return [s.canonical_tuple() for s in self.steps]

    def total_points_spent(self) -> int:
        return sum(self.points_spent.values())


class PlanParams(_Frozen):
    """Tunables recorded on a PlanSet (DATA_MODEL ``plan_set.params``)."""

    top_k: PosInt = 5
    max_hops: NonNegInt = 2
    max_expansions: PosInt = 200_000
    slack_days: NonNegInt = 2
    promo_days: NonNegInt = 30
    stale_days: NonNegInt = 180

    def as_dict(self) -> dict[str, int]:
        return {
            "top_k": self.top_k,
            "max_hops": self.max_hops,
            "max_expansions": self.max_expansions,
            "slack_days": self.slack_days,
            "promo_days": self.promo_days,
            "stale_days": self.stale_days,
        }


class PlanSet(_Mutable):
    """An immutable, world-pinned set of ranked plans (table ``plan_set``)."""

    id: int | None = None
    goal_id: int | None = None
    world_version: str
    world_hash: str
    today: date
    params: PlanParams = PlanParams()
    expansions: NonNegInt = 0
    verdict: Verdict
    recommended_plan_id: int | None = None
    disclaimer: str = DISCLAIMER
    created_at: str = ""
    plans: list[Plan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> PlanSet:
        if self.verdict in (Verdict.INSUFFICIENT_POINTS, Verdict.NO_MATCHING_AWARD) and self.plans:
            raise ValueError(f"verdict {self.verdict} requires an empty plan list")
        ranks = [p.rank for p in self.plans]
        if len(set(ranks)) != len(ranks):
            raise ValueError("plan ranks must be unique within a plan set")
        return self

    def recommended(self) -> Plan | None:
        """Rank-1 plan when the verdict recommends acting on points."""
        if self.verdict not in (Verdict.BOOK_WITH_POINTS, Verdict.CASH_PLAN):
            return None
        for plan in self.plans:
            if plan.rank == 1:
                return plan
        return None


class ValueBreakdown(_Frozen):
    """FR-13 quick valuation of a balance."""

    program_id: str
    points: NonNegInt
    baseline_value_cents: NonNegInt
    baseline_cpp_milli: PosInt
    as_of: date
    cash_floor_cents: NonNegInt
    cash_floor_option_id: str | None = None
    travel_floor_cents: NonNegInt
    travel_floor_option_id: str | None = None
