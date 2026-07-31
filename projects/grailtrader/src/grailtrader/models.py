"""Pydantic v2 domain models and enumerations (DATA_MODEL.md).

Every entity in DATA_MODEL.md lives here with its stated invariants enforced by
validators. Models that DATA_MODEL declares append-only/immutable are frozen;
``FashionEvent`` is not frozen at the type level because DATA_MODEL permits
exactly three mutations (``pending -> confirmed|rejected``, ``source_refs``
appends and the derived ``corroboration`` recount) — those go through
``engine.events.merge_event``/``transition_status``, which rebuild the row.
"""

from __future__ import annotations

import math
from enum import StrEnum
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ids import advice_id, compute_inputs_hash, event_id, garment_id, listing_id
from .weeks import parse_date, parse_datetime, week_key

# --------------------------------------------------------------------------- #
# Enumerations                                                                  #
# --------------------------------------------------------------------------- #


class Category(StrEnum):
    OUTERWEAR = "outerwear"
    KNITWEAR = "knitwear"
    TOPS = "tops"
    BOTTOMS = "bottoms"
    DENIM = "denim"
    FOOTWEAR = "footwear"
    TAILORING = "tailoring"
    ACCESSORIES = "accessories"


class ConditionGrade(StrEnum):
    NEW = "new"
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


#: Canonical worst-to-best ordering used by the strictly-decreasing invariant.
CONDITION_ORDER: tuple[ConditionGrade, ...] = (
    ConditionGrade.NEW,
    ConditionGrade.EXCELLENT,
    ConditionGrade.GOOD,
    ConditionGrade.FAIR,
    ConditionGrade.POOR,
)


class EventType(StrEnum):
    DESIGNER_DEPARTURE = "designer_departure"
    DESIGNER_APPOINTMENT = "designer_appointment"
    COLLAB_ANNOUNCEMENT = "collab_announcement"
    CELEBRITY_COSIGN = "celebrity_cosign"
    RUNWAY_RECEPTION = "runway_reception"
    BRAND_SCANDAL = "brand_scandal"


class DepartureReason(StrEnum):
    RESIGNATION = "resignation"
    OUSTED = "ousted"
    DEATH = "death"
    HOUSE_CLOSURE = "house_closure"


class Acclaim(StrEnum):
    ACCLAIMED = "acclaimed"
    NEUTRAL = "neutral"
    UNPROVEN = "unproven"


class CelebrityTier(StrEnum):
    A_LIST = "a_list"
    B_LIST = "b_list"
    NICHE = "niche"


#: FR-6: celebrity co-sign transients scale by tier before the path is built.
TIER_SCALE: dict[CelebrityTier, float] = {
    CelebrityTier.A_LIST: 1.0,
    CelebrityTier.B_LIST: 0.5,
    CelebrityTier.NICHE: 0.25,
}


class RunwayPolarity(StrEnum):
    ACCLAIMED = "acclaimed"
    PANNED = "panned"


class ScandalSeverity(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


class EventSource(StrEnum):
    NEWS = "news"
    SOCIAL = "social"
    MANUAL = "manual"


class EventStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ListingSource(StrEnum):
    FIXTURE = "fixture"
    CSV = "csv"


class ListingStatus(StrEnum):
    SOLD = "sold"
    ACTIVE = "active"


class GarmentStatus(StrEnum):
    OWNED = "owned"
    WATCHING = "watching"
    SOLD_ARCHIVED = "sold_archived"


class AdviceAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class ValuationMethod(StrEnum):
    REPEAT_SALES = "repeat_sales"
    COMP_BASED = "comp_based"
    UNAVAILABLE = "unavailable"


class ValuationReason(StrEnum):
    NO_INDEX = "no_index"
    STALE_INDEX = "stale_index"
    NO_INDEX_AT_ANCHOR = "no_index_at_anchor"


class HoldReason(StrEnum):
    NO_INDEX = "no_index"
    STALE_INDEX = "stale_index"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NO_ACTIVE_EVENTS = "no_active_events"
    NO_BASELINE = "no_baseline"
    BELOW_THRESHOLD = "below_threshold"
    LOW_CONFIDENCE = "low_confidence"


class ExclusionReason(StrEnum):
    OUT_OF_WINDOW = "out_of_window"
    INSUFFICIENT_FUTURE_INDEX = "insufficient_future_index"
    STALE_STRATUM = "stale_stratum"
    NO_INDEX = "no_index"


class TargetKind(StrEnum):
    """Scope selector on an impact prior (DATA_MODEL ImpactPrior.target)."""

    ERA = "era"
    BRAND = "brand"
    PREDECESSOR_ERA = "predecessor_era"
    GIVEN = "given"


class ImpactDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


# --------------------------------------------------------------------------- #
# Committed datasets                                                            #
# --------------------------------------------------------------------------- #


class DesignerEra(BaseModel):
    """A designer's tenure at a brand (``data/brands.json``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    designer: str = Field(min_length=1)
    label: str = Field(min_length=1)
    start: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    end: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> DesignerEra:
        if not self.id.startswith(f"{self.brand_id}:"):
            raise ValueError(f"era id {self.id!r} must start with {self.brand_id + ':'!r}")
        if ":" not in self.id or not self.id.split(":", 1)[1]:
            raise ValueError(f"era id {self.id!r} must be 'brand:designer-slug'")
        if "/" in self.id:
            raise ValueError(f"era id {self.id!r} must not contain '/'")
        if self.end is not None and self.end < self.start:
            raise ValueError(f"era {self.id}: end {self.end} precedes start {self.start}")
        return self

    @property
    def suffix(self) -> str:
        """The era segment used in stratum paths (``helmut-lang:helmut`` -> ``helmut``)."""
        return self.id.split(":", 1)[1]


class Brand(BaseModel):
    """A brand/line with its designer eras (``data/brands.json``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    notes: str = ""
    eras: tuple[DesignerEra, ...]

    @model_validator(mode="before")
    @classmethod
    def _inject_brand_id(cls, data: Any) -> Any:
        """``brands.json`` nests eras without repeating ``brand_id``; fill it in."""
        if isinstance(data, dict) and "eras" in data and "id" in data:
            eras = []
            for era in data["eras"]:
                if isinstance(era, dict) and "brand_id" not in era:
                    era = {**era, "brand_id": data["id"]}
                eras.append(era)
            data = {**data, "eras": eras}
        return data

    @model_validator(mode="after")
    def _check(self) -> Brand:
        if not self.eras:
            raise ValueError(f"brand {self.id}: every brand needs at least one era")
        seen: set[str] = set()
        for era in self.eras:
            if era.brand_id != self.id:
                raise ValueError(f"era {era.id} does not belong to brand {self.id}")
            if era.id in seen:
                raise ValueError(f"brand {self.id}: duplicate era id {era.id}")
            seen.add(era.id)
        open_eras = [e for e in self.eras if e.end is None]
        if len(open_eras) > 1:
            raise ValueError(
                f"brand {self.id}: at most one open era allowed, found "
                + ", ".join(e.id for e in open_eras)
            )
        ordered = sorted(self.eras, key=lambda e: e.start)
        if [e.id for e in ordered] != [e.id for e in self.eras]:
            raise ValueError(f"brand {self.id}: eras must be listed in chronological order")
        for prev, nxt in pairwise(ordered):
            if prev.end is None:
                raise ValueError(f"brand {self.id}: open era {prev.id} must be the last era")
            if nxt.start <= prev.end:
                raise ValueError(
                    f"brand {self.id}: eras {prev.id} and {nxt.id} overlap "
                    f"({prev.end} >= {nxt.start})"
                )
        return self


class ImpactPrior(BaseModel):
    """A row of ``data/impact_priors.json`` (FR-6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    target: TargetKind
    direction: ImpactDirection
    permanent_pct: float = Field(ge=-0.35, le=0.35)
    transient_pct: float = Field(ge=-0.40, le=0.40)
    half_life_weeks: float = Field(ge=1.0, le=26.0)
    base_conf: float = Field(ge=0.05, le=0.95)
    rationale: str = Field(min_length=1)
    source_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_direction(self) -> ImpactPrior:
        total = self.permanent_pct + self.transient_pct
        if self.direction is ImpactDirection.BULLISH:
            if self.permanent_pct < 0 or self.transient_pct < 0 or total <= 0:
                raise ValueError(f"prior {self.key}: bullish row with non-positive components")
        elif self.permanent_pct > 0 or self.transient_pct > 0 or total >= 0:
            raise ValueError(f"prior {self.key}: bearish row with non-negative components")
        if 1.0 + self.permanent_pct + self.transient_pct <= 0.0:
            raise ValueError(f"prior {self.key}: 1 + P + T must stay positive")
        return self


class ConditionRow(BaseModel):
    """A row of ``data/conditions.json`` (FR-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grade: ConditionGrade
    multiplier: float = Field(gt=0.0)
    platform_aliases: tuple[str, ...] = ()


class ConditionTable(BaseModel):
    """The five-grade condition scale plus its platform alias table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grades: tuple[ConditionRow, ...]

    @model_validator(mode="after")
    def _check(self) -> ConditionTable:
        present = [row.grade for row in self.grades]
        if present != list(CONDITION_ORDER):
            raise ValueError(
                "conditions.json must list exactly the five grades in order "
                f"{[g.value for g in CONDITION_ORDER]}, got {[g.value for g in present]}"
            )
        multipliers = [row.multiplier for row in self.grades]
        for prev, nxt in pairwise(multipliers):
            if nxt >= prev:
                raise ValueError(f"condition multipliers must strictly decrease: {prev} -> {nxt}")
        seen: dict[str, ConditionGrade] = {}
        for row in self.grades:
            for alias in row.platform_aliases:
                folded = alias.strip().casefold()
                if not folded:
                    raise ValueError(f"condition {row.grade}: empty platform alias")
                if folded in seen:
                    raise ValueError(
                        f"platform alias {alias!r} maps to both {seen[folded]} and {row.grade}"
                    )
                seen[folded] = row.grade
        return self

    @property
    def multipliers(self) -> dict[ConditionGrade, float]:
        return {row.grade: row.multiplier for row in self.grades}

    @property
    def alias_map(self) -> dict[str, ConditionGrade]:
        out: dict[str, ConditionGrade] = {}
        for row in self.grades:
            out[row.grade.value.casefold()] = row.grade
            for alias in row.platform_aliases:
                out[alias.strip().casefold()] = row.grade
        return out


class RequiredMarker(BaseModel):
    """A literal string that must appear in every rendered advice (FR-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: str = Field(min_length=1)
    marker: str = Field(min_length=1)


class AdviceTemplate(BaseModel):
    """A row of ``data/advice_templates.json`` (FR-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    action: AdviceAction
    hold_reasons: tuple[HoldReason, ...] = ()
    garment_statuses: tuple[GarmentStatus, ...] = ()
    headline: str = Field(min_length=1)
    drivers: str = Field(min_length=1)
    valuation_context: str = Field(min_length=1)
    uncertainty: str = Field(min_length=1)
    falsifier: str = Field(min_length=1)
    fee_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> AdviceTemplate:
        if self.action is AdviceAction.HOLD and not self.hold_reasons:
            raise ValueError(f"template {self.id}: hold templates must name their hold reasons")
        if self.action is not AdviceAction.HOLD and self.hold_reasons:
            raise ValueError(f"template {self.id}: only hold templates carry hold reasons")
        return self

    @property
    def sections(self) -> tuple[str, ...]:
        return (
            self.headline,
            self.drivers,
            self.valuation_context,
            self.uncertainty,
            self.fee_note,
        )


class AdviceTemplateCatalog(BaseModel):
    """``data/advice_templates.json`` as a whole (FR-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    footer: str = Field(min_length=1)
    forbidden_lexicon: tuple[str, ...]
    required_markers: tuple[RequiredMarker, ...]
    driver_line_format: str = Field(min_length=1)
    no_drivers_line: str = Field(min_length=1)
    templates: tuple[AdviceTemplate, ...]

    @model_validator(mode="after")
    def _check(self) -> AdviceTemplateCatalog:
        if not self.forbidden_lexicon:
            raise ValueError("advice_templates.json: forbidden_lexicon must be non-empty")
        if not self.required_markers:
            raise ValueError("advice_templates.json: required_markers must be non-empty")
        seen: set[str] = set()
        for tpl in self.templates:
            if tpl.id in seen:
                raise ValueError(f"duplicate advice template id {tpl.id}")
            seen.add(tpl.id)
        return self


class QIndexStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_stale_weeks: int = Field(ge=0)
    factor: float = Field(gt=0.0, le=1.0)


class IndexConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    window_weeks: int = Field(ge=1)
    min_sales: int = Field(ge=1)
    fence_sigma: float = Field(gt=0.0)
    fence_floor_log: float = Field(gt=0.0)
    stale_max_weeks: int = Field(ge=0)
    parent_weight_window_weeks: int = Field(ge=1)


class AdvisorSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    horizons_weeks: tuple[int, ...]
    theta_buy: float = Field(gt=0.0, le=0.5)
    theta_sell: float = Field(gt=0.0, le=0.5)
    fee_assumption_pct: float = Field(gt=0.0, le=0.5)
    conf_min: float = Field(gt=0.0, lt=1.0)
    conf_floor: float = Field(gt=0.0, lt=1.0)
    conf_cap: float = Field(gt=0.0, le=1.0)
    z_half: float = Field(gt=0.0)
    sigma_min_changes: int = Field(ge=2)
    active_max_weeks: int = Field(ge=1)
    active_transient_min: float = Field(gt=0.0)
    q_index: tuple[QIndexStep, ...]
    source_factor: dict[EventSource, float]
    corroboration_step: float = Field(ge=0.0)
    corroboration_max_steps: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> AdvisorSettings:
        if not self.horizons_weeks:
            raise ValueError("advisor.horizons_weeks must be non-empty")
        for prev, nxt in zip(self.horizons_weeks, self.horizons_weeks[1:], strict=False):
            if nxt <= prev:
                raise ValueError("advisor.horizons_weeks must be strictly ascending")
        if min(self.horizons_weeks) < 1:
            raise ValueError("advisor.horizons_weeks must be positive")
        if not (self.theta_buy == self.theta_sell == self.fee_assumption_pct):
            raise ValueError(
                "FR-1/FR-8 coherence: theta_buy == theta_sell == fee_assumption_pct required, got "
                f"{self.theta_buy}, {self.theta_sell}, {self.fee_assumption_pct}"
            )
        if not (self.conf_floor < self.conf_min < self.conf_cap):
            raise ValueError("advisor: conf_floor < conf_min < conf_cap required")
        if not self.q_index:
            raise ValueError("advisor.q_index must be non-empty")
        for prev, nxt in zip(self.q_index, self.q_index[1:], strict=False):
            if nxt.max_stale_weeks <= prev.max_stale_weeks:
                raise ValueError("advisor.q_index must ascend in max_stale_weeks")
            if nxt.factor > prev.factor:
                raise ValueError("advisor.q_index factors must be non-increasing")
        missing = {s for s in EventSource} - set(self.source_factor)
        if missing:
            raise ValueError(f"advisor.source_factor missing sources: {sorted(missing)}")
        for source, factor in self.source_factor.items():
            if not 0.0 < factor <= 1.0:
                raise ValueError(f"advisor.source_factor[{source}] must be in (0, 1]")
        return self


class CalibrationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket_edges: tuple[float, ...]

    @model_validator(mode="after")
    def _check(self) -> CalibrationConfig:
        if len(self.bucket_edges) != 2:
            raise ValueError("calibration.bucket_edges must have exactly two edges")
        lo, hi = self.bucket_edges
        if not lo < hi:
            raise ValueError("calibration.bucket_edges must be ascending")
        return self


class AdvisorConfig(BaseModel):
    """``data/advisor_config.json`` — every FR-4/6/8 constant lives here, not in code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_version: str = Field(min_length=1)
    index: IndexConfig
    advisor: AdvisorSettings
    calibration: CalibrationConfig

    @model_validator(mode="after")
    def _check(self) -> AdvisorConfig:
        expected_floor = round(math.log(2.2), 4)
        if round(self.index.fence_floor_log, 4) != expected_floor:
            raise ValueError(
                f"index.fence_floor_log must be ln(2.2) = {expected_floor}, "
                f"got {self.index.fence_floor_log}"
            )
        if self.advisor.q_index[-1].max_stale_weeks != self.index.stale_max_weeks:
            raise ValueError(
                "advisor.q_index's last max_stale_weeks must equal index.stale_max_weeks"
            )
        lo, hi = self.calibration.bucket_edges
        if not (self.advisor.conf_floor < lo and hi < self.advisor.conf_cap):
            raise ValueError("calibration.bucket_edges must sit strictly inside the conf range")
        return self


# --------------------------------------------------------------------------- #
# Adapter DTOs                                                                  #
# --------------------------------------------------------------------------- #


class RawListing(BaseModel):
    """What a ``ListingsFeed`` yields, before normalisation (FR-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: ListingSource
    external_id: str = Field(min_length=1)
    brand_ref: str = Field(min_length=1)
    era_ref: str = Field(min_length=1)
    category: str = Field(min_length=1)
    platform_condition: str = Field(min_length=1)
    status: str = Field(min_length=1)
    listed_at: str = Field(min_length=1)
    sold_at: str | None = None
    ask_price: float | None = None
    sold_price: float | None = None
    currency: str = "USD"
    size: str | None = None
    title: str | None = None


class IngestReport(BaseModel):
    """Counts returned by listing ingestion (FR-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ingested: int = 0
    duplicates: int = 0
    skipped_unresolved: int = 0
    skipped_currency: int = 0
    skipped_invalid: int = 0
    unresolved_refs: tuple[str, ...] = ()

    def merged(self, other: IngestReport) -> IngestReport:
        return IngestReport(
            ingested=self.ingested + other.ingested,
            duplicates=self.duplicates + other.duplicates,
            skipped_unresolved=self.skipped_unresolved + other.skipped_unresolved,
            skipped_currency=self.skipped_currency + other.skipped_currency,
            skipped_invalid=self.skipped_invalid + other.skipped_invalid,
            unresolved_refs=tuple(dict.fromkeys(self.unresolved_refs + other.unresolved_refs)),
        )


class EventIngestReport(BaseModel):
    """Counts returned by event ingestion (FR-5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    created: int = 0
    corroborated: int = 0
    unchanged: int = 0
    by_status: dict[EventStatus, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# SQLite entities                                                               #
# --------------------------------------------------------------------------- #


class Listing(BaseModel):
    """A normalised marketplace listing (append-only, FR-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    source: ListingSource
    external_id: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    era_id: str = Field(min_length=1)
    category: Category
    condition: ConditionGrade
    platform_label: str
    size: str | None = None
    title: str | None = None
    status: ListingStatus
    listed_at: str
    sold_at: str | None = None
    ask_price: float | None = None
    sold_price: float | None = None
    currency: str = "USD"

    @model_validator(mode="after")
    def _check(self) -> Listing:
        if self.currency != "USD":
            raise ValueError(
                f"listing {self.external_id}: only USD is supported, got {self.currency}"
            )
        if not self.era_id.startswith(f"{self.brand_id}:"):
            raise ValueError(
                f"listing {self.external_id}: era {self.era_id} is not a {self.brand_id} era"
            )
        for name, price in (("ask_price", self.ask_price), ("sold_price", self.sold_price)):
            if price is not None and not price > 0:
                raise ValueError(f"listing {self.external_id}: {name} must be > 0")
        if self.status is ListingStatus.SOLD:
            if self.sold_price is None or self.sold_at is None:
                raise ValueError(
                    f"listing {self.external_id}: sold listings need sold_price and sold_at"
                )
            if parse_datetime(self.sold_at) < parse_datetime(self.listed_at):
                raise ValueError(f"listing {self.external_id}: sold_at precedes listed_at")
        else:
            if self.ask_price is None:
                raise ValueError(f"listing {self.external_id}: active listings need ask_price")
            if self.sold_at is not None or self.sold_price is not None:
                raise ValueError(
                    f"listing {self.external_id}: active listings carry no sold fields"
                )
        expected = listing_id(self.source.value, self.external_id)
        if self.id != expected:
            raise ValueError(
                f"listing {self.external_id}: id {self.id} is not content-derived "
                f"(expected {expected})"
            )
        return self

    @property
    def era_suffix(self) -> str:
        return self.era_id.split(":", 1)[1]

    @property
    def stratum_path(self) -> str:
        return f"{self.brand_id}/{self.era_suffix}/{self.category.value}"

    @property
    def sold_week(self) -> str | None:
        return None if self.sold_at is None else week_key(self.sold_at)


class IndexPoint(BaseModel):
    """One weekly index observation for one stratum (derived, FR-4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stratum_id: str = Field(min_length=1)
    week: str
    level_usd: float | None = None
    index_value: float = Field(gt=0.0)
    n_sales: int = Field(ge=0)
    n_excluded: int = Field(ge=0)
    built_as_of: str

    @model_validator(mode="after")
    def _check(self) -> IndexPoint:
        if week_key(self.week) != self.week:
            raise ValueError(f"index point week {self.week} is not an ISO Monday")
        depth = len(self.stratum_id.split("/"))
        if depth == 3:
            if self.level_usd is None or not self.level_usd > 0:
                raise ValueError(f"leaf stratum {self.stratum_id} needs a positive level_usd")
        else:
            if self.level_usd is not None:
                raise ValueError(f"parent stratum {self.stratum_id} must carry level_usd = null")
            if self.n_excluded != 0:
                raise ValueError(f"parent stratum {self.stratum_id} must carry n_excluded = 0")
        return self


class FashionEvent(BaseModel):
    """A typed fashion event (FR-5)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    event_type: EventType
    brand_id: str = Field(min_length=1)
    era_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    occurred_on: str
    source: EventSource
    source_refs: tuple[str, ...] = ()
    status: EventStatus
    corroboration: int = Field(default=1, ge=1)
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> FashionEvent:
        parse_date(self.occurred_on)
        if self.era_id is not None and not self.era_id.startswith(f"{self.brand_id}:"):
            raise ValueError(f"event era {self.era_id} is not a {self.brand_id} era")
        validate_event_attributes(self.event_type, self.era_id, self.attributes)
        if not self.source_refs:
            raise ValueError("event needs at least one source_ref")
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("event source_refs must be deduplicated")
        expected = event_id(
            self.event_type.value,
            self.brand_id,
            self.era_id,
            self.occurred_on,
            self.identity_attrs,
        )
        if self.id != expected:
            raise ValueError(f"event id {self.id} is not content-derived (expected {expected})")
        return self

    @property
    def week(self) -> str:
        """The ISO Monday of the week containing ``occurred_on`` (the id keys on it)."""
        return week_key(self.occurred_on)

    @property
    def identity_attrs(self) -> dict[str, Any]:
        """Factual discriminators only — never the judgement attributes (FR-5)."""
        return identity_attrs_for(self.event_type, self.attributes)


#: Which attribute keys are *factual* discriminators, per event type (FR-5).
IDENTITY_ATTR_KEYS: dict[EventType, tuple[str, ...]] = {
    EventType.DESIGNER_DEPARTURE: (),
    EventType.DESIGNER_APPOINTMENT: ("designer",),
    EventType.COLLAB_ANNOUNCEMENT: ("counterparty",),
    EventType.CELEBRITY_COSIGN: ("celebrity",),
    EventType.RUNWAY_RECEPTION: (),
    EventType.BRAND_SCANDAL: (),
}

#: Required and optional attributes per event type (FR-5 typology table).
_EVENT_ATTR_SCHEMA: dict[EventType, tuple[dict[str, type[StrEnum] | type[str]], set[str]]] = {
    EventType.DESIGNER_DEPARTURE: ({"reason": DepartureReason}, set()),
    EventType.DESIGNER_APPOINTMENT: ({"designer": str, "acclaim": Acclaim}, set()),
    EventType.COLLAB_ANNOUNCEMENT: ({"counterparty": str}, {"counterparty_brand_id"}),
    EventType.CELEBRITY_COSIGN: ({"celebrity": str, "tier": CelebrityTier}, {"category"}),
    EventType.RUNWAY_RECEPTION: ({"polarity": RunwayPolarity}, set()),
    EventType.BRAND_SCANDAL: ({"severity": ScandalSeverity}, set()),
}


def validate_event_attributes(
    event_type: EventType, era_id: str | None, attributes: dict[str, Any]
) -> None:
    """Validate an event's typed attribute bag; unknown keys are rejected (FR-5)."""
    required, optional = _EVENT_ATTR_SCHEMA[event_type]
    unknown = set(attributes) - set(required) - optional
    if unknown:
        raise ValueError(f"{event_type}: unknown attributes {sorted(unknown)}")
    for key, kind in required.items():
        if key not in attributes:
            raise ValueError(f"{event_type}: missing required attribute {key!r}")
        value = attributes[key]
        if kind is str:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{event_type}.{key} must be a non-empty string")
        else:
            kind(value)  # raises ValueError on an out-of-enum value
    if "category" in attributes and attributes["category"] is not None:
        Category(attributes["category"])
    if event_type is EventType.DESIGNER_DEPARTURE and era_id is None:
        raise ValueError("designer_departure requires the era_id it closes")
    if event_type is EventType.CELEBRITY_COSIGN and era_id is None and attributes.get("category"):
        raise ValueError("celebrity_cosign with a category must also name the era")


def identity_attrs_for(event_type: EventType, attributes: dict[str, Any]) -> dict[str, Any]:
    """Project an attribute bag onto its factual discriminators (FR-5 identity)."""
    keys = IDENTITY_ATTR_KEYS[event_type]
    return {k: attributes[k] for k in keys if k in attributes}


class Garment(BaseModel):
    """A portfolio garment (FR-11)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    era_id: str = Field(min_length=1)
    category: Category
    condition: ConditionGrade
    anchor_condition: ConditionGrade
    size: str | None = None
    status: GarmentStatus
    acquisition_price: float | None = None
    acquired_on: str | None = None
    reference_price: float | None = None
    reference_date: str | None = None
    disposed_price: float | None = None
    disposed_on: str | None = None
    added_at: str
    deleted_at: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Garment:
        if not self.era_id.startswith(f"{self.brand_id}:"):
            raise ValueError(f"garment era {self.era_id} is not a {self.brand_id} era")
        acquisition_set = self.acquisition_price is not None and self.acquired_on is not None
        acquisition_clear = self.acquisition_price is None and self.acquired_on is None
        reference_set = self.reference_price is not None and self.reference_date is not None
        reference_clear = self.reference_price is None and self.reference_date is None
        disposed_set = self.disposed_price is not None and self.disposed_on is not None
        disposed_clear = self.disposed_price is None and self.disposed_on is None
        if self.status is GarmentStatus.WATCHING:
            if not (reference_set and acquisition_clear and disposed_clear):
                raise ValueError(
                    "watching garments require reference_price + reference_date and no "
                    "acquisition/disposal fields"
                )
        else:
            if not (acquisition_set and reference_clear):
                raise ValueError(
                    f"{self.status} garments require acquisition_price + acquired_on and no "
                    "reference fields"
                )
            if self.status is GarmentStatus.SOLD_ARCHIVED and not disposed_set:
                raise ValueError("sold_archived garments require disposed_price + disposed_on")
            if self.status is GarmentStatus.OWNED and not disposed_clear:
                raise ValueError("owned garments carry no disposal fields")
        for name, price in (
            ("acquisition_price", self.acquisition_price),
            ("reference_price", self.reference_price),
            ("disposed_price", self.disposed_price),
        ):
            if price is not None and not price > 0:
                raise ValueError(f"garment {self.label}: {name} must be > 0")
        expected = garment_id(self.stratum_path, self.anchor_date, self.anchor_price, self.added_at)
        if self.id != expected:
            raise ValueError(f"garment id {self.id} is not content-derived (expected {expected})")
        return self

    @property
    def era_suffix(self) -> str:
        return self.era_id.split(":", 1)[1]

    @property
    def stratum_path(self) -> str:
        return f"{self.brand_id}/{self.era_suffix}/{self.category.value}"

    @property
    def anchor_price(self) -> float:
        """The anchor pair's price (DATA_MODEL "Derived, not stored")."""
        if self.status is GarmentStatus.WATCHING:
            assert self.reference_price is not None
            return self.reference_price
        assert self.acquisition_price is not None
        return self.acquisition_price

    @property
    def anchor_date(self) -> str:
        if self.status is GarmentStatus.WATCHING:
            assert self.reference_date is not None
            return self.reference_date
        assert self.acquired_on is not None
        return self.acquired_on


class Advice(BaseModel):
    """A rendered, frame-checked advice row (immutable, append-only, FR-8/FR-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    garment_id: str = Field(min_length=1)
    as_of_week: str
    inputs_hash: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    stratum_id: str = Field(min_length=1)
    action: AdviceAction
    is_candidate: bool
    horizon_weeks: int = Field(gt=0)
    expected_return: float | None = None
    confidence: float | None = None
    fair_value: float | None = None
    fair_value_method: ValuationMethod
    rationale_codes: tuple[str, ...]
    rendered_text: str = Field(min_length=1)
    frame_checked: bool
    created_as_of: str

    @model_validator(mode="after")
    def _check(self) -> Advice:
        if not self.frame_checked:
            raise ValueError("FR-9: a non-frame-checked advice cannot exist")
        if self.confidence is not None and not 0.05 <= self.confidence <= 0.95:
            raise ValueError("advice confidence must lie in [0.05, 0.95]")
        if week_key(self.as_of_week) != self.as_of_week:
            raise ValueError(f"advice as_of_week {self.as_of_week} is not an ISO Monday")
        holds = [c for c in self.rationale_codes if c.startswith("hold:")]
        if self.action is AdviceAction.HOLD:
            if len(holds) != 1:
                raise ValueError("a hold advice carries exactly one hold: rationale code")
        elif holds:
            raise ValueError("a buy/sell advice carries no hold: rationale code")
        expected = advice_id(self.garment_id, self.as_of_week, self.inputs_hash)
        if self.id != expected:
            raise ValueError(f"advice id {self.id} is not content-derived (expected {expected})")
        return self


class Driver(BaseModel):
    """One active event leg behind a decision (FR-6/FR-8), rendered into the rationale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    event_type: EventType
    event_week: str
    target_stratum: str
    prior_key: str
    direction: ImpactDirection
    permanent_pct: float
    transient_pct: float
    half_life_weeks: float
    base_conf: float
    source: EventSource
    corroboration: int
    age_weeks: int
    retirement_age_weeks: float
    lam: float
    m_now: float
    m_at_horizon: float


class AdviceDecision(BaseModel):
    """The advisor's pure output for one garment-week, before rendering (FR-8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    garment_id: str
    as_of_week: str
    stratum_id: str
    action: AdviceAction
    hold_reason: HoldReason | None = None
    is_candidate: bool = False
    horizon_weeks: int
    expected_return: float | None = None
    confidence: float | None = None
    z_score: float | None = None
    sigma_w: float | None = None
    baseline_index: float | None = None
    observed_index: float | None = None
    q_index: float | None = None
    c_event: float | None = None
    stratum_staleness_weeks: int | None = None
    stratum_selected: bool = True
    drivers: tuple[Driver, ...] = ()
    rationale_codes: tuple[str, ...] = ()
    inputs_hash: str = ""
    fair_value: float | None = None
    fair_value_method: ValuationMethod = ValuationMethod.UNAVAILABLE
    fair_value_reason: ValuationReason | None = None
    level_usd: float | None = None

    @property
    def driver_event_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(d.event_id for d in self.drivers))


class ValuationResult(BaseModel):
    """FR-7 output: the value plus the method that produced it, never a silent guess."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: ValuationMethod
    fair_value: float | None = None
    stratum_id: str | None = None
    reason: ValuationReason | None = None
    level_usd: float | None = None
    unrealized_gain: float | None = None

    @model_validator(mode="after")
    def _check(self) -> ValuationResult:
        if self.method is ValuationMethod.UNAVAILABLE:
            if self.fair_value is not None:
                raise ValueError("unavailable valuations carry no fair value")
            if self.reason is None:
                raise ValueError("an unavailable valuation must name its reason")
        elif self.fair_value is None:
            raise ValueError(f"{self.method} valuation must carry a fair value")
        return self


class FrameCheckResult(BaseModel):
    """FR-9 two-directional frame check verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    violations: tuple[str, ...] = ()


class RenderedAdvice(BaseModel):
    """Rendered advice text plus its named sections (FR-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str
    text: str
    sections: dict[str, str]


class BacktestParams(BaseModel):
    """``backtest_run.params`` (FR-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_week: str
    end_week: str
    placebo_seed: int | None = None
    reference: str = "recovered"
    scenario: str = ""

    @field_validator("reference")
    @classmethod
    def _reference(cls, value: str) -> str:
        if value not in {"truth", "recovered"}:
            raise ValueError("backtest reference must be 'truth' or 'recovered'")
        return value


class BacktestResult(BaseModel):
    """One decision considered by a replay (FR-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    garment_id: str
    week: str
    action: AdviceAction
    is_candidate: bool
    horizon_weeks: int
    confidence: float | None = None
    expected_return: float | None = None
    driver_event_ids: tuple[str, ...] = ()
    entry_week: str
    realized_return: float | None = None
    hit: bool | None = None
    excluded_reason: ExclusionReason | None = None

    @model_validator(mode="after")
    def _check(self) -> BacktestResult:
        if self.entry_week != _next_week(self.week):
            raise ValueError(
                f"FR-10 invariant: entry_week must be week + 1 "
                f"({self.week} -> {_next_week(self.week)}), got {self.entry_week}"
            )
        if (self.excluded_reason is None) != (self.realized_return is not None):
            raise ValueError(
                "FR-10 invariant: excluded_reason is set exactly when realized_return is null"
            )
        if self.hit is not None and (not self.is_candidate or self.realized_return is None):
            raise ValueError("hit is only defined for graded candidates")
        return self


class BacktestRun(BaseModel):
    """A persisted replay (append-only, FR-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    params: BacktestParams
    as_of: str
    aggregates: dict[str, Any]


def _next_week(week: str) -> str:
    from .weeks import add_weeks

    return add_weeks(week, 1)


def build_inputs_hash(
    drivers: tuple[Driver, ...], stratum_id: str, index_built_as_of: str, config_version: str
) -> str:
    """FR-8 identity: hash the exact decision inputs (deduplicated by event id)."""
    per_event: dict[str, tuple[str, str, int]] = {}
    for driver in drivers:
        per_event[driver.event_id] = (driver.event_id, driver.event_week, driver.corroboration)
    return compute_inputs_hash(per_event.values(), stratum_id, index_built_as_of, config_version)
