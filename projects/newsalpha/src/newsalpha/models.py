"""Pydantic v2 domain models for NewsAlpha (DATA_MODEL.md).

Every entity in DATA_MODEL.md lives here, with its stated invariants enforced by
validators.  Models that back append-only rows (`Article`, `Signal`, `Brief`,
`PriceBar`, `BacktestRun`, `BacktestResult`) and models that are rebuilt whole by
each recompute (`Cluster`, `Event`, `EventLink`) are frozen: they are constructed
once and never mutated in place.  Timestamps are ISO-8601 UTC strings supplied by
callers -- nothing in this package reads the clock.
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# Enumerations (DATA_MODEL.md "Enumerations")
# --------------------------------------------------------------------------- #


class AssetKind(StrEnum):
    equity = "equity"
    crypto = "crypto"
    index = "index"


class EventType(StrEnum):
    earnings_surprise = "earnings_surprise"
    guidance_change = "guidance_change"
    mna = "mna"
    regulatory_action = "regulatory_action"
    listing = "listing"
    delisting = "delisting"
    hack_exploit = "hack_exploit"


class Stage(StrEnum):
    rumored = "rumored"
    confirmed = "confirmed"
    denied = "denied"


class LinkRole(StrEnum):
    subject = "subject"
    acquirer = "acquirer"
    target = "target"
    venue = "venue"
    mentioned = "mentioned"


class Direction(StrEnum):
    bullish = "bullish"
    bearish = "bearish"
    unclear = "unclear"


class Magnitude(StrEnum):
    minor = "minor"
    moderate = "moderate"
    major = "major"


class SourceTierName(StrEnum):
    t1_official = "t1_official"
    t2_wire = "t2_wire"
    t3_other = "t3_other"


class FeedKind(StrEnum):
    fixture = "fixture"
    rss = "rss"


class BarSource(StrEnum):
    fixture = "fixture"
    live = "live"


class ExclusionReason(StrEnum):
    insufficient_bars = "insufficient_bars"
    unknown_asset_bars = "unknown_asset_bars"
    benchmark_gap = "benchmark_gap"
    zero_abnormal_return = "zero_abnormal_return"
    estimated_publish_time = "estimated_publish_time"
    placebo_no_clean_window = "placebo_no_clean_window"


#: Roles that may carry a signal (FR-5/FR-6). `mentioned` and `venue` never do.
SIGNAL_ROLES: frozenset[LinkRole] = frozenset(
    {LinkRole.subject, LinkRole.acquirer, LinkRole.target}
)

#: Signal-bearing roles per event type, from the SCOPE typology table.
SIGNAL_ROLES_BY_TYPE: dict[EventType, tuple[LinkRole, ...]] = {
    EventType.earnings_surprise: (LinkRole.subject,),
    EventType.guidance_change: (LinkRole.subject,),
    EventType.mna: (LinkRole.acquirer, LinkRole.target),
    EventType.regulatory_action: (LinkRole.subject,),
    EventType.listing: (LinkRole.subject,),
    EventType.delisting: (LinkRole.subject,),
    EventType.hack_exploit: (LinkRole.subject,),
}

#: Tier ordering used by `best_tier` (higher wins).
TIER_ORDER: dict[SourceTierName, int] = {
    SourceTierName.t3_other: 0,
    SourceTierName.t2_wire: 1,
    SourceTierName.t1_official: 2,
}

#: Stage multiplier on confidence -- the *only* stage effect on confidence (FR-6).
F_STAGE: dict[Stage, float] = {
    Stage.confirmed: 1.00,
    Stage.rumored: 0.50,
    Stage.denied: 0.70,
}

#: Typed attribute schema per event type (DATA_MODEL.md Event.attributes).
#: value = allowed literal values, or None when the attribute is free-form/numeric.
ATTRIBUTE_SCHEMA: dict[EventType, dict[str, tuple[str, ...] | None]] = {
    EventType.earnings_surprise: {
        "polarity": ("beat", "miss"),
        "surprise_pct": None,
    },
    EventType.guidance_change: {
        "polarity": ("raise", "cut", "withdraw"),
    },
    EventType.mna: {
        "premium_pct": None,
        "deal_value_usd": None,
    },
    EventType.regulatory_action: {
        "polarity": ("favorable", "adverse"),
        "agency": None,
        "amount_usd": None,
    },
    EventType.listing: {"venue": None},
    EventType.delisting: {"venue": None},
    EventType.hack_exploit: {"amount_usd": None, "vector": None},
}

_ISO_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

Hex16 = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
Confidence = Annotated[float, Field(ge=0.05, le=0.95)]
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


def dir_sign(direction: Direction) -> int:
    """+1 bullish, -1 bearish, 0 unclear."""
    if direction is Direction.bullish:
        return 1
    if direction is Direction.bearish:
        return -1
    return 0


def magnitude_for(mid_ar: float) -> Magnitude:
    """Band thresholds from FR-3.3: |mid| < 1% minor, 1-4% moderate, > 4% major."""
    m = abs(mid_ar)
    if m < 0.01:
        return Magnitude.minor
    if m <= 0.04:
        return Magnitude.moderate
    return Magnitude.major


def _check_iso_utc(value: str, field: str) -> str:
    if not _ISO_DT_RE.match(value):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp, got {value!r}")
    return value


# --------------------------------------------------------------------------- #
# Committed datasets
# --------------------------------------------------------------------------- #


class Asset(BaseModel):
    """`data/assets.json` -> table `asset`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: AssetKind
    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    ambiguous: bool = False
    context_keywords: tuple[str, ...] = ()
    benchmark_id: str | None = None

    @model_validator(mode="after")
    def _invariants(self) -> Asset:
        prefix = {AssetKind.equity: "eq:", AssetKind.crypto: "cx:", AssetKind.index: "idx:"}[
            self.kind
        ]
        if not self.id.startswith(prefix):
            raise ValueError(
                f"asset id {self.id!r} must start with {prefix!r} for kind {self.kind}"
            )
        if self.id != f"{prefix}{self.symbol}":
            raise ValueError(f"asset id {self.id!r} must be {prefix}{self.symbol}")
        if bool(self.ambiguous) != bool(self.context_keywords):
            raise ValueError(
                f"asset {self.id}: context_keywords must be non-empty iff ambiguous is set"
            )
        if (self.kind is AssetKind.index) != (self.benchmark_id is None):
            raise ValueError(f"asset {self.id}: benchmark_id must be set iff kind != index")
        if "" in self.aliases:
            raise ValueError(f"asset {self.id}: empty alias")
        return self


class EventPattern(BaseModel):
    """`data/patterns.json` -> one extraction pattern (FR-4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    event_type: EventType
    triggers: tuple[str, ...] = Field(min_length=1)
    required_context: tuple[str, ...] = ()
    attribute_extractors: dict[str, str] = Field(default_factory=dict)
    polarity: str | None = None
    real_world_example: str = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _invariants(self) -> EventPattern:
        if not self.real_world_example.strip():
            raise ValueError(f"pattern {self.id}: real_world_example must be non-empty (FR-3.2)")
        allowed = ATTRIBUTE_SCHEMA[self.event_type]
        for name, pattern in self.attribute_extractors.items():
            if name not in allowed:
                raise ValueError(
                    f"pattern {self.id}: attribute {name!r} is not in the schema for "
                    f"{self.event_type}"
                )
            try:
                compiled = re.compile(pattern)
            except re.error as exc:  # pragma: no cover - defensive
                raise ValueError(
                    f"pattern {self.id}: regex for {name!r} does not compile: {exc}"
                ) from exc
            if compiled.groups > 1:
                raise ValueError(
                    f"pattern {self.id}: regex for {name!r} has {compiled.groups} capture groups "
                    "(max 1)"
                )
        if self.polarity is not None:
            values = allowed.get("polarity")
            if values is None or self.polarity not in values:
                raise ValueError(
                    f"pattern {self.id}: polarity {self.polarity!r} not valid for {self.event_type}"
                )
        if any(not t.strip() for t in self.triggers):
            raise ValueError(f"pattern {self.id}: empty trigger lexeme")
        return self

    def declared_attributes(self) -> frozenset[str]:
        """Attributes this pattern claims it can fill (regex extractors + fixed polarity)."""
        names = set(self.attribute_extractors)
        if self.polarity is not None:
            names.add("polarity")
        return frozenset(names)


class StageOverride(BaseModel):
    """Per-stage override of direction/band/horizon -- the only stage effect on those (FR-6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: Direction
    magnitude: Magnitude
    expected_ar_lo: float
    expected_ar_hi: float
    horizon_bars: Literal[1, 5, 20]

    @property
    def mid_expected_ar(self) -> float:
        return (self.expected_ar_lo + self.expected_ar_hi) / 2

    @model_validator(mode="after")
    def _invariants(self) -> StageOverride:
        if self.expected_ar_lo > self.expected_ar_hi:
            raise ValueError("expected_ar_lo must be <= expected_ar_hi")
        mid = self.mid_expected_ar
        want = dir_sign(self.direction)
        got = 0 if math.isclose(mid, 0.0, abs_tol=1e-12) else (1 if mid > 0 else -1)
        if got != want:
            raise ValueError(
                f"sign(mid_expected_ar)={got} does not match direction {self.direction}"
            )
        if magnitude_for(mid) is not self.magnitude:
            raise ValueError(
                f"magnitude {self.magnitude} does not match band midpoint {mid:.4f} "
                f"(expected {magnitude_for(mid)})"
            )
        return self


class EventPrior(BaseModel):
    """`data/priors.json` -> one scoring prior (FR-6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    kind: Literal["equity", "crypto", "*"]
    direction: Direction
    magnitude: Magnitude
    expected_ar_lo: float
    expected_ar_hi: float
    announcement_ar_lo: float
    announcement_ar_hi: float
    horizon_bars: Literal[1, 5, 20]
    base_conf: Confidence
    supersession_days: int = Field(ge=1, le=365)
    stage_overrides: dict[Stage, StageOverride] | None = None
    rationale: str = Field(min_length=1)
    source_note: str = Field(min_length=1)

    @property
    def mid_expected_ar(self) -> float:
        return (self.expected_ar_lo + self.expected_ar_hi) / 2

    @property
    def mid_announcement_ar(self) -> float:
        return (self.announcement_ar_lo + self.announcement_ar_hi) / 2

    @model_validator(mode="after")
    def _invariants(self) -> EventPrior:
        parts = self.key.split(".")
        if len(parts) != 3:
            raise ValueError(f"prior key {self.key!r} must be event_type.role.polarity")
        stage_values = {s.value for s in Stage}
        for token in parts:
            if token in stage_values:
                raise ValueError(
                    f"prior key {self.key!r} contains stage token {token!r}; stage is never part "
                    "of a prior key (FR-3.3)"
                )
        if parts[0] not in {t.value for t in EventType}:
            raise ValueError(f"prior key {self.key!r}: unknown event type {parts[0]!r}")
        if parts[1] not in {r.value for r in SIGNAL_ROLES}:
            raise ValueError(f"prior key {self.key!r}: {parts[1]!r} is not a signal-bearing role")
        if self.expected_ar_lo > self.expected_ar_hi:
            raise ValueError(f"prior {self.key}: expected_ar_lo must be <= expected_ar_hi")
        if self.announcement_ar_lo > self.announcement_ar_hi:
            raise ValueError(f"prior {self.key}: announcement_ar_lo must be <= announcement_ar_hi")
        mid = self.mid_expected_ar
        want = dir_sign(self.direction)
        got = 0 if math.isclose(mid, 0.0, abs_tol=1e-12) else (1 if mid > 0 else -1)
        if got != want:
            raise ValueError(
                f"prior {self.key}/{self.kind}: sign(mid_expected_ar)={got} does not match "
                f"direction {self.direction}"
            )
        if magnitude_for(mid) is not self.magnitude:
            raise ValueError(
                f"prior {self.key}/{self.kind}: magnitude {self.magnitude} does not match "
                f"midpoint {mid:.4f} (expected {magnitude_for(mid)})"
            )
        return self

    @property
    def event_type(self) -> EventType:
        return EventType(self.key.split(".")[0])

    @property
    def role(self) -> LinkRole:
        return LinkRole(self.key.split(".")[1])

    @property
    def polarity(self) -> str:
        return self.key.split(".")[2]

    def effective(self, stage: Stage) -> StageOverride:
        """`eff` from FR-6: the stage override when present, else the base row."""
        if self.stage_overrides and stage in self.stage_overrides:
            return self.stage_overrides[stage]
        return StageOverride(
            direction=self.direction,
            magnitude=self.magnitude,
            expected_ar_lo=self.expected_ar_lo,
            expected_ar_hi=self.expected_ar_hi,
            horizon_bars=self.horizon_bars,
        )


class BriefTemplate(BaseModel):
    """`data/templates.json` -> one brief template (FR-7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    event_type: EventType
    direction: Direction | None = None
    what_happened: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    what_to_watch: tuple[str, ...] = Field(min_length=2, max_length=4)
    uncertainty_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _invariants(self) -> BriefTemplate:
        if "{prior_rationale}" not in self.why_it_matters:
            raise ValueError(f"template {self.id}: why_it_matters must include {{prior_rationale}}")
        for slot in ("{confidence}", "{falsifier}", "{already_priced_note}"):
            if slot not in self.uncertainty_note:
                raise ValueError(f"template {self.id}: uncertainty_note must include {slot}")
        if any(not item.strip() for item in self.what_to_watch):
            raise ValueError(f"template {self.id}: empty what_to_watch item")
        return self


# --------------------------------------------------------------------------- #
# Ingestion / articles
# --------------------------------------------------------------------------- #


class RawArticle(BaseModel):
    """What a `NewsFeed` adapter yields, before normalization (FR-1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str = Field(min_length=1)
    url: str | None = None
    source_domain: str = Field(min_length=1)
    published_at: str
    published_at_estimated: bool = False
    fetched_at: str
    title: str
    body: str

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _check_iso_utc(v, "timestamp")

    @field_validator("source_domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        return v.strip().lower()


class Article(BaseModel):
    """`article` table -- durable, append-only (DATA_MODEL.md)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Hex16
    external_id: str = Field(min_length=1)
    url: str | None
    source_domain: str = Field(min_length=1)
    tier: SourceTierName
    published_at: str
    published_at_estimated: bool = False
    excluded_from_analysis: bool = False
    fetched_at: str
    title: str
    body: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _check_iso_utc(v, "timestamp")

    @property
    def analysis_text(self) -> str:
        """The text all char offsets index into: normalized title + newline + body.

        DATA_MODEL.md phrases the evidence invariant as ``quote == body[start:end]``.
        SCOPE FR-5's all-caps guard is defined over *wire-style headlines*, which are
        titles, so headline text has to be inside the analyzed string for the rule to
        be implementable at all; SCOPE's numbered FRs are authoritative over
        DATA_MODEL (DATA_MODEL.md, Event section).  The invariant is therefore
        enforced as ``quote == article.analysis_text[start:end]`` -- the same string
        that `content_hash` is taken over.
        """
        return f"{self.title}\n{self.body}"


# --------------------------------------------------------------------------- #
# Derived: clusters, events, links
# --------------------------------------------------------------------------- #


class Cluster(BaseModel):
    """`cluster` + `cluster_member` -- derived (FR-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Hex16
    article_ids: tuple[str, ...] = Field(min_length=1)
    earliest_published_at: str
    latest_published_at: str
    article_count: int = Field(ge=1)
    corroboration: int = Field(ge=1)
    best_tier: SourceTierName

    @model_validator(mode="after")
    def _invariants(self) -> Cluster:
        if self.article_count != len(self.article_ids):
            raise ValueError(f"cluster {self.id}: article_count != len(article_ids)")
        if len(set(self.article_ids)) != len(self.article_ids):
            raise ValueError(f"cluster {self.id}: duplicate member")
        if tuple(sorted(self.article_ids)) != self.article_ids:
            raise ValueError(f"cluster {self.id}: article_ids must be sorted for canonical export")
        if self.earliest_published_at > self.latest_published_at:
            raise ValueError(f"cluster {self.id}: earliest_published_at > latest_published_at")
        if self.corroboration > self.article_count:
            raise ValueError(f"cluster {self.id}: corroboration exceeds member count")
        return self

    @property
    def event_date(self) -> str:
        return self.earliest_published_at[:10]


class EvidenceSpan(BaseModel):
    """A quoted character span of an article's analysis text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    article_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _invariants(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("evidence span end must be > start")
        if len(self.quote) != self.end - self.start:
            raise ValueError("evidence quote length must equal end - start")
        return self


class Event(BaseModel):
    """`event` table -- derived, re-created by every active-window recompute (FR-15)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Hex16
    cluster_id: Hex16
    event_type: EventType
    stage: Stage
    attributes: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: float = Field(ge=0.05, le=0.95)
    evidence: tuple[EvidenceSpan, ...] = Field(min_length=1)
    notes: tuple[str, ...] = ()
    event_date: IsoDate
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _check_iso_utc(v, "observed_at")

    @model_validator(mode="after")
    def _invariants(self) -> Event:
        allowed = ATTRIBUTE_SCHEMA[self.event_type]
        for name, value in self.attributes.items():
            if name not in allowed:
                raise ValueError(
                    f"event {self.id}: attribute {name!r} is not valid for {self.event_type}"
                )
            values = allowed[name]
            if values is not None and value not in values:
                raise ValueError(f"event {self.id}: attribute {name}={value!r} not in {values}")
            if value is None:
                raise ValueError(f"event {self.id}: attribute {name!r} must not be null")
        return self

    @property
    def polarity(self) -> str:
        """The polarity token used in prior keys (`*` when the type has none)."""
        value = self.attributes.get("polarity")
        return str(value) if value is not None else "*"


class EventLink(BaseModel):
    """`event_link` table -- derived (FR-5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: Hex16
    asset_id: str
    role: LinkRole
    link_confidence: float = Field(ge=0.5, le=0.95)
    evidence: EvidenceSpan


# --------------------------------------------------------------------------- #
# Durable analysis rows
# --------------------------------------------------------------------------- #


class EventSnapshot(BaseModel):
    """Frozen copy of the event state a signal revision was scored from (FR-15)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType
    stage: Stage
    attributes: dict[str, Any] = Field(default_factory=dict)
    corroboration: int = Field(ge=1)
    best_tier: SourceTierName
    extraction_confidence: float
    link_confidence: float
    evidence_article_ids: tuple[str, ...] = Field(min_length=1)
    event_date: IsoDate


class Signal(BaseModel):
    """`signal` table -- durable, append-only, versioned (FR-15)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Hex16
    signal_key: Hex16
    revision: int = Field(ge=1)
    supersedes: str | None = None
    supersedes_key: str | None = None
    event_id: Hex16
    asset_id: str
    role: LinkRole
    direction: Direction
    magnitude: Magnitude
    confidence: Confidence
    horizon_bars: Literal[1, 5, 20]
    expected_ar_lo: float
    expected_ar_hi: float
    score: float
    prior_key: str
    rationale_codes: tuple[str, ...] = Field(min_length=1)
    event_snapshot: EventSnapshot
    observed_at: str
    created_as_of: str

    @field_validator("observed_at", "created_as_of")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _check_iso_utc(v, "timestamp")

    @model_validator(mode="after")
    def _invariants(self) -> Signal:
        if self.role not in SIGNAL_ROLES:
            raise ValueError(f"signal {self.id}: role {self.role} is never signal-bearing")
        if self.direction is Direction.unclear:
            raise ValueError(
                f"signal {self.id}: an unclear resolution emits no signal at all (FR-6)"
            )
        if self.revision == 1 and self.supersedes is not None:
            raise ValueError(f"signal {self.id}: revision 1 cannot supersede a previous revision")
        if self.revision > 1 and self.supersedes is None:
            raise ValueError(f"signal {self.id}: revision > 1 must name the revision it supersedes")
        for token in self.prior_key.split("."):
            if token in {s.value for s in Stage}:
                raise ValueError(f"signal {self.id}: prior_key must not encode a stage")
        return self

    def scored_tuple(self) -> tuple[str, str, int, float, str]:
        """The tuple FR-15 compares for idempotency."""
        return (
            self.direction.value,
            self.magnitude.value,
            self.horizon_bars,
            round(self.confidence, 4),
            self.prior_key,
        )


class SignalKeyAlias(BaseModel):
    """`signal_key_alias` -- FR-15 key continuity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_key: Hex16
    to_key: Hex16
    created_as_of: str

    @model_validator(mode="after")
    def _invariants(self) -> SignalKeyAlias:
        if self.from_key == self.to_key:
            raise ValueError("signal key alias must point at a different key")
        return self


class Brief(BaseModel):
    """`brief` table -- durable, immutable, 1:1 with a signal revision (FR-7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: Hex16
    template_id: str = Field(min_length=1)
    what_happened: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    what_to_watch: tuple[str, ...] = Field(min_length=1)
    uncertainty_note: str = Field(min_length=1)
    rendered_text: str = Field(min_length=1)
    frame_checked: Literal[True] = True

    @model_validator(mode="after")
    def _invariants(self) -> Brief:
        for name in ("what_happened", "why_it_matters", "uncertainty_note"):
            if not getattr(self, name).strip():
                raise ValueError(f"brief {self.signal_id}: section {name} is empty")
        if any(not item.strip() for item in self.what_to_watch):
            raise ValueError(f"brief {self.signal_id}: empty what_to_watch item")
        return self


# --------------------------------------------------------------------------- #
# Market data & backtests
# --------------------------------------------------------------------------- #


class PriceBar(BaseModel):
    """`price_bar` table -- durable (FR-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: str
    date: IsoDate
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    source: BarSource

    @model_validator(mode="after")
    def _invariants(self) -> PriceBar:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"bar {self.asset_id}@{self.date}: require low <= open, close <= high")
        return self


class BacktestParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: IsoDate
    end: IsoDate
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    placebo_seed: int | None = None

    @model_validator(mode="after")
    def _invariants(self) -> BacktestParams:
        if self.start > self.end:
            raise ValueError("backtest start must be <= end")
        return self


class BucketStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int = Field(ge=0)
    hit: float | None = None


class Aggregates(BaseModel):
    """`backtest_run.aggregates` -- overall or per-event-type (FR-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int = Field(ge=0)
    n_excluded: int = Field(ge=0)
    excluded_by_reason: dict[str, int] = Field(default_factory=dict)
    n_superseded: int = Field(ge=0, default=0)
    hit_rate: float | None = None
    mean_ar: float | None = None
    ic_spearman: float | None = None
    buckets: dict[str, BucketStats] = Field(default_factory=dict)


class BacktestRun(BaseModel):
    """`backtest_run` table -- durable (FR-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Hex16
    params: BacktestParams
    as_of: str
    aggregates: Aggregates
    per_type: dict[str, Aggregates] = Field(default_factory=dict)

    @field_validator("as_of")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _check_iso_utc(v, "as_of")


class BacktestResult(BaseModel):
    """`backtest_result` table -- one row per considered signal revision (FR-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: Hex16
    signal_id: Hex16
    entry_date: IsoDate | None = None
    exit_date: IsoDate | None = None
    ar: float | None = None
    hit: bool | None = None
    excluded_reason: ExclusionReason | None = None
    placebo_attempts: int = Field(ge=0, le=8, default=0)

    @model_validator(mode="after")
    def _invariants(self) -> BacktestResult:
        if (self.hit is None) != (self.excluded_reason is not None):
            raise ValueError(
                f"result {self.signal_id}: excluded_reason is set iff hit is null (FR-10)"
            )
        if self.excluded_reason is None:
            if self.entry_date is None or self.exit_date is None or self.ar is None:
                raise ValueError(f"result {self.signal_id}: an included result needs entry/exit/ar")
            if self.exit_date < self.entry_date:
                raise ValueError(f"result {self.signal_id}: exit_date precedes entry_date")
        return self


class WatchlistItem(BaseModel):
    """`watchlist_item` table -- durable, deletable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: str
    added_at: str

    @field_validator("added_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _check_iso_utc(v, "added_at")


# --------------------------------------------------------------------------- #
# Read models (digest)
# --------------------------------------------------------------------------- #


class DigestEntry(BaseModel):
    """One ranked row of the digest (FR-8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: Hex16
    signal_key: Hex16
    asset_id: str
    asset_name: str
    event_type: EventType
    stage: Stage
    direction: Direction
    magnitude: Magnitude
    confidence: float
    horizon_bars: int
    score: float
    event_date: IsoDate
    summary: str
    supersession_note: str | None = None


class Digest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: IsoDate
    watchlist_only: bool
    include_superseded: bool
    entries: tuple[DigestEntry, ...] = ()
    empty_state: str | None = None


__all__ = [
    "ATTRIBUTE_SCHEMA",
    "F_STAGE",
    "SIGNAL_ROLES",
    "SIGNAL_ROLES_BY_TYPE",
    "TIER_ORDER",
    "Aggregates",
    "Article",
    "Asset",
    "AssetKind",
    "BacktestParams",
    "BacktestResult",
    "BacktestRun",
    "BarSource",
    "Brief",
    "BriefTemplate",
    "BucketStats",
    "Cluster",
    "Digest",
    "DigestEntry",
    "Direction",
    "Event",
    "EventLink",
    "EventPattern",
    "EventPrior",
    "EventSnapshot",
    "EventType",
    "EvidenceSpan",
    "ExclusionReason",
    "FeedKind",
    "LinkRole",
    "Magnitude",
    "PriceBar",
    "RawArticle",
    "Signal",
    "SignalKeyAlias",
    "SourceTierName",
    "Stage",
    "StageOverride",
    "WatchlistItem",
    "dir_sign",
    "magnitude_for",
]
