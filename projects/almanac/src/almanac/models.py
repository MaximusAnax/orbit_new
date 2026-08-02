"""Pydantic v2 domain models for Almanac.

Every entity in ``docs/DATA_MODEL.md`` lives here, with its stated invariants
enforced by field constraints and validators.  Ids are opaque strings at this
layer (26-char ULIDs are minted by ``adapters.ids.UlidFactory``; the engine
orders by id only for tie-breaks, so it never interprets them).
"""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------
# Enums and constants
# --------------------------------------------------------------------------


class EntryKind(StrEnum):
    QUOTE = "quote"
    IDEA = "idea"


class EntryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ThemeSource(StrEnum):
    USER = "user"
    SUGGESTED = "suggested"


class PromptKind(StrEnum):
    REFLECT = "reflect"
    ACT = "act"
    REFRAME = "reframe"
    CONNECT = "connect"


class SurfacingKind(StrEnum):
    DAILY = "daily"
    EXTRA = "extra"


class SelectPool(StrEnum):
    """Which FR-7 branch produced a pick (DATA_MODEL.md §Surfacing)."""

    PINNED_RESCUE = "pinned_rescue"
    FORCED_NOVELTY = "forced_novelty"
    NOVELTY = "novelty"
    REVIEW = "review"
    NOVELTY_ONLY = "novelty_only"
    REVIEW_ONLY = "review_only"
    NOT_DUE = "not_due"
    RELAXED = "relaxed"
    EXTRA = "extra"


class Grade(StrEnum):
    APPLIED = "applied"
    RESONATED = "resonated"
    FLAT = "flat"


class Verdict(StrEnum):
    MISATTRIBUTED = "misattributed"
    DISPUTED = "disputed"
    UNVERIFIED = "unverified"


#: FR-9 base kind rotation, indexed by prior exposure count mod 4.
KIND_ROTATION: tuple[PromptKind, ...] = (
    PromptKind.REFLECT,
    PromptKind.ACT,
    PromptKind.REFRAME,
    PromptKind.CONNECT,
)

#: Reserved template-pool id; not an assignable Theme (SCOPE.md D8).
GENERAL_THEME_ID = "general"

#: Multiplier key used by the FR-6 fold when a surfacing carried no reflection.
NO_GRADE = "none"

#: The only slot names a PromptTemplate may use (FR-9 step 4).
TEMPLATE_SLOTS = frozenset({"author", "theme_name", "source", "text_short"})

#: Contested slots are the ones where the FR-7 quota rule made a free choice.
CONTESTED_POOLS = frozenset({SelectPool.NOVELTY, SelectPool.REVIEW})

_SLOT_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_TAG_RE = re.compile(r"^[^\s]+(?:-[^\s]+)*$")


# --------------------------------------------------------------------------
# User data
# --------------------------------------------------------------------------


class Entry(BaseModel):
    """A captured quote or idea (DATA_MODEL.md §Entry)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: EntryKind
    text: str = Field(min_length=1, max_length=2000)
    normalized_hash: str
    author: str | None = None
    source: str | None = None
    url: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    pinned: bool = False
    status: EntryStatus = EntryStatus.ACTIVE
    captured_on: dt.date
    created_at: dt.datetime
    updated_at: dt.datetime

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must contain at least one non-whitespace character")
        return v

    @field_validator("normalized_hash")
    @classmethod
    def _hash_shape(cls, v: str) -> str:
        if not _HEX64_RE.match(v):
            raise ValueError("normalized_hash must be a 64-char lowercase sha-256 hex digest")
        return v

    @field_validator("author", "source", "url", "note")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v or None


class Tag(BaseModel):
    """A normalized free-form tag (DATA_MODEL.md §Tag)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def _normalized(cls, v: str) -> str:
        if v != v.casefold() or " " in v or not _TAG_RE.match(v):
            raise ValueError("tag names are casefolded with spaces replaced by hyphens")
        return v


class EntryTheme(BaseModel):
    """Link row between an Entry and one of the 16 fixed themes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str = Field(min_length=1)
    theme_id: str = Field(min_length=1)
    source: ThemeSource

    @field_validator("theme_id")
    @classmethod
    def _not_general(cls, v: str) -> str:
        if v == GENERAL_THEME_ID:
            raise ValueError("'general' is a template pool, not an assignable theme")
        return v


class Surfacing(BaseModel):
    """An immutable card snapshot (DATA_MODEL.md §Surfacing). Append-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    on_date: dt.date
    slot: int = Field(ge=0)
    kind: SurfacingKind
    select_pool: SelectPool
    prompt_template_id: str = Field(min_length=1)
    prompt_kind: PromptKind
    prompt_text: str = Field(min_length=1)
    personalized: bool = False
    personalize_fell_back: bool = False
    relaxed_cooldown: bool = False
    prompt_recency_relaxed: bool = False
    filter_theme_id: str | None = None
    filter_collection_id: str | None = None
    scheduler_version: str = Field(min_length=1)
    seed: int
    created_at: dt.datetime

    @model_validator(mode="after")
    def _pool_invariants(self) -> Surfacing:
        is_extra_kind = self.kind is SurfacingKind.EXTRA
        is_extra_pool = self.select_pool is SelectPool.EXTRA
        if is_extra_kind != is_extra_pool:
            raise ValueError("kind == 'extra' iff select_pool == 'extra'")
        if is_extra_kind and self.slot != 0:
            raise ValueError("extra surfacings always occupy slot 0")
        if self.relaxed_cooldown != (self.select_pool is SelectPool.RELAXED):
            raise ValueError("relaxed_cooldown is true iff select_pool == 'relaxed'")
        if not is_extra_kind and (
            self.filter_theme_id is not None or self.filter_collection_id is not None
        ):
            raise ValueError("filters are recorded on extra draws only")
        return self


class Reflection(BaseModel):
    """An immutable journal entry attached to exactly one Surfacing (FR-11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    surfacing_id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    grade: Grade
    text: str | None = Field(default=None, max_length=4000)
    logged_at: dt.datetime


class SchedulerState(BaseModel):
    """Materialized cache of the FR-6 fold. Derived, never authoritative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str = Field(min_length=1)
    exposure_count: int = Field(default=0, ge=0)
    last_surfaced_on: dt.date | None = None
    interval_days: int = Field(ge=1)
    flat_streak: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _exposure_consistency(self) -> SchedulerState:
        if (self.exposure_count == 0) != (self.last_surfaced_on is None):
            raise ValueError("last_surfaced_on is set iff exposure_count > 0")
        return self


class Collection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None
    created_at: dt.datetime


class CollectionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    collection_id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    position: int = Field(ge=0)


class AttributionFlag(BaseModel):
    """A persisted attribution finding on an entry (FR-2). Informational only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    misattribution_id: str | None = None
    verdict: Verdict
    note: str = Field(min_length=1)
    reference_url: str | None = None
    checked_at: dt.datetime


class ConfigEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    value: str


# --------------------------------------------------------------------------
# Committed datasets
# --------------------------------------------------------------------------


class LexiconTerm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    term: str = Field(min_length=1)
    weight: float = Field(gt=0.0)

    @field_validator("term")
    @classmethod
    def _lower(cls, v: str) -> str:
        if v != v.casefold().strip():
            raise ValueError("lexicon terms are stored casefolded and stripped")
        return v


class Theme(BaseModel):
    """One of the 16 fixed themes (data/themes.json)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    lexicon: list[LexiconTerm]

    @field_validator("id")
    @classmethod
    def _not_general(cls, v: str) -> str:
        if v == GENERAL_THEME_ID:
            raise ValueError("'general' is a template pool, not a theme id")
        return v

    @field_validator("lexicon")
    @classmethod
    def _lexicon_floor(cls, v: list[LexiconTerm]) -> list[LexiconTerm]:
        if len(v) < 8:
            raise ValueError("every theme lexicon needs at least 8 terms")
        seen = {t.term for t in v}
        if len(seen) != len(v):
            raise ValueError("lexicon terms must be unique within a theme")
        return v


class PromptTemplate(BaseModel):
    """A committed application-prompt template (data/prompts.json)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    theme_id: str = Field(min_length=1)
    kind: PromptKind
    template: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _kind_signature(self) -> PromptTemplate:
        unknown = set(_SLOT_RE.findall(self.template)) - TEMPLATE_SLOTS
        if unknown:
            raise ValueError(f"template {self.id} uses unfillable slots: {sorted(unknown)}")
        body = self.template.strip()
        if self.kind in (PromptKind.REFLECT, PromptKind.CONNECT) and not body.endswith("?"):
            raise ValueError(f"{self.kind} template {self.id} must end with '?'")
        if self.kind is PromptKind.ACT and ("When " not in body or " I will " not in body):
            raise ValueError(f"act template {self.id} must carry the 'When … I will …' scaffold")
        if self.kind is PromptKind.REFRAME and body.count("?") > (1 if body.endswith("?") else 0):
            raise ValueError(f"reframe template {self.id} may only end with a question mark")
        if "\n" in self.template:
            raise ValueError(f"template {self.id} must be a single line")
        return self


class MisattributionRecord(BaseModel):
    """A curated misattribution case (data/misattributions.json)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    claimed_authors: list[str] = Field(default_factory=list)
    verdict: Verdict
    likely_origin: str = Field(min_length=1)
    note: str = Field(min_length=1)
    reference_url: str = Field(min_length=1)

    @field_validator("reference_url")
    @classmethod
    def _real_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("every misattribution record carries an https reference url")
        return v


class StarterQuote(BaseModel):
    """One entry of the committed starter pack (data/starter_quotes.json).

    Public-domain only: pre-1929 authors and translations (SCOPE.md D19).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=2000)
    author: str | None = None
    source: str | None = None
    kind: EntryKind = EntryKind.QUOTE
    themes: list[str] = Field(default_factory=list, max_length=3)
    tags: list[str] = Field(default_factory=list)


class Clamp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lo: int = Field(ge=1)
    hi: int = Field(ge=1)
    hi_flat: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Clamp:
        if not self.lo <= self.hi <= self.hi_flat:
            raise ValueError("clamp requires lo <= hi <= hi_flat")
        return self


class SchedulerParams(BaseModel):
    """Committed scheduler parameters (data/scheduler.json)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    params_version: str = Field(min_length=1)
    W: int = Field(ge=1)
    I0: int = Field(ge=1)
    clamp: Clamp
    multipliers: dict[str, float]
    demote_flat_streak: int = Field(ge=1)
    archive_flat_streak: int = Field(ge=1)
    pinned_cap: int = Field(ge=1)
    P_rescue: int = Field(ge=1)
    rho: float = Field(ge=0.0, le=1.0)
    H: int = Field(ge=1)
    S: int = Field(ge=1)
    tau: float = Field(gt=0.0)
    k: int = Field(ge=1, le=5)
    jitter: float = Field(ge=0.0)
    prompt_reuse_window: int = Field(ge=0)

    @model_validator(mode="after")
    def _reachability(self) -> SchedulerParams:
        # DATA_MODEL.md §SchedulerParams: the two constraints that keep every
        # interval the FR-6 table can produce realizable rather than silently
        # clipped by the cooldown.
        if self.I0 != self.W:
            raise ValueError("I0 must equal W so the debut interval is reachable")
        if self.clamp.lo < self.W:
            raise ValueError("clamp.lo must be >= W so no interval is swallowed by the cooldown")
        expected = {Grade.RESONATED.value, Grade.APPLIED.value, Grade.FLAT.value, NO_GRADE}
        if set(self.multipliers) != expected:
            raise ValueError(f"multipliers must be keyed exactly by {sorted(expected)}")
        if any(m <= 0 for m in self.multipliers.values()):
            raise ValueError("multipliers must be positive")
        if self.archive_flat_streak < self.demote_flat_streak:
            raise ValueError("archive_flat_streak must be >= demote_flat_streak")
        return self

    def multiplier(self, grade: Grade | None) -> float:
        """The FR-6 multiplier for the grade of the previous surfacing."""
        return self.multipliers[NO_GRADE if grade is None else grade.value]


class DatasetBundle(BaseModel):
    """All committed data, loaded and validated together."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    themes: list[Theme]
    templates: list[PromptTemplate]
    misattributions: list[MisattributionRecord]
    params: SchedulerParams

    @model_validator(mode="after")
    def _floors(self) -> DatasetBundle:
        theme_ids = {t.id for t in self.themes}
        if len(theme_ids) != len(self.themes):
            raise ValueError("theme ids must be unique")
        template_ids = {t.id for t in self.templates}
        if len(template_ids) != len(self.templates):
            raise ValueError("template ids must be unique")
        record_ids = {r.id for r in self.misattributions}
        if len(record_ids) != len(self.misattributions):
            raise ValueError("misattribution ids must be unique")
        for tpl in self.templates:
            if tpl.theme_id != GENERAL_THEME_ID and tpl.theme_id not in theme_ids:
                raise ValueError(f"template {tpl.id} references unknown theme {tpl.theme_id}")
        # FR-9 authoring floors: >= 6 templates per theme covering all 4 kinds,
        # >= 8 'general' templates covering all 4 kinds.
        for pool_id, floor in [(tid, 6) for tid in sorted(theme_ids)] + [(GENERAL_THEME_ID, 8)]:
            pool = [t for t in self.templates if t.theme_id == pool_id]
            if len(pool) < floor:
                raise ValueError(f"pool {pool_id} has {len(pool)} templates, needs {floor}")
            kinds = {t.kind for t in pool}
            if kinds != set(PromptKind):
                missing = sorted(k.value for k in set(PromptKind) - kinds)
                raise ValueError(f"pool {pool_id} is missing template kinds: {missing}")
        return self

    def theme_map(self) -> dict[str, Theme]:
        return {t.id: t for t in self.themes}


# --------------------------------------------------------------------------
# Engine / service value objects
# --------------------------------------------------------------------------


class ThemeSuggestion(BaseModel):
    """A scored theme proposal (FR-3). Proposed, never silently assigned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    theme_id: str
    name: str
    score: float


class AttributionFinding(BaseModel):
    """What an ``AttributionChecker`` returns before it becomes a stored flag.

    SCOPE.md's adapter table types ``check()`` as returning ``AttributionFlag``;
    a stored flag additionally needs an id, an entry id and a check timestamp,
    none of which a pure checker can know.  FR-2 names the payload fields
    (verdict, likely origin, note, reference url), which is exactly this model;
    ``service.capture`` turns findings into ``AttributionFlag`` rows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    misattribution_id: str | None
    verdict: Verdict
    likely_origin: str
    note: str
    reference_url: str | None = None


class CardContext(BaseModel):
    """Everything a ``PromptPersonalizer`` may see (FR-10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_text: str
    entry_note: str | None
    entry_author: str | None
    entry_source: str | None
    theme_ids: list[str]
    theme_names: list[str]
    prompt_kind: PromptKind
    rendered_prompt: str
    text_short: str | None = None


class PromptValidation(BaseModel):
    """Result of the FR-10 validator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    reason: str | None = None
    check: str | None = None


class Card(BaseModel):
    """What ``today`` / ``draw`` renders (the shape a later UI consumes)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surfacing: Surfacing
    entry: Entry
    themes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    attribution_flags: list[AttributionFlag] = Field(default_factory=list)


class ExposureBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exposures: int = Field(ge=0)
    entries: int = Field(ge=0)


class PinnedStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str
    excerpt: str
    days_since_seen: int | None
    guarantee_days: int


class ArchiveCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str
    excerpt: str
    flat_streak: int


class CapacityBlock(BaseModel):
    """FR-14's capacity block — the capacity identity, reported to the user."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    k: int
    pinned_rescue_load: float
    capture_rate: float
    review_capacity: float
    review_demand: float
    stretch_lambda: float | None = None
    sustainable_library: float | None = None
    recommended_k: int | None = None
    advisory: str | None = None


class StatsReport(BaseModel):
    """FR-14 stats."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    on_date: dt.date
    total_entries: int
    active_entries: int
    archived_entries: int
    by_kind: dict[str, int]
    pinned_count: int
    coverage: float
    exposure_histogram: list[ExposureBucket]
    open_streak: int
    reflect_streak: int
    novelty_share: float
    rho: float
    contested_slots: int
    pinned_status: list[PinnedStatus]
    archive_candidates: list[ArchiveCandidate]
    capacity: CapacityBlock


class CaptureResult(BaseModel):
    """FR-1/FR-2/FR-3 capture response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: Entry
    suggestions: list[ThemeSuggestion] = Field(default_factory=list)
    attribution_flags: list[AttributionFlag] = Field(default_factory=list)
    duplicate_of: str | None = None
    tags: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


class EntryDetail(BaseModel):
    """One entry with its scheduler state and full history (FR-4/FR-11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: Entry
    tags: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    state: SchedulerState
    surfacings: list[Surfacing] = Field(default_factory=list)
    reflections: list[Reflection] = Field(default_factory=list)
    attribution_flags: list[AttributionFlag] = Field(default_factory=list)


class ImportCandidate(BaseModel):
    """One inbound row of an FR-5 import, before normalization."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    kind: EntryKind = EntryKind.QUOTE
    author: str | None = None
    source: str | None = None
    url: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list, max_length=3)
    captured_on: dt.date | None = None


class ImportIssue(BaseModel):
    """A row that could not be imported, named by its 1-based row number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    row: int
    message: str


class ImportReport(BaseModel):
    """FR-5: what an import did, plus the honest drain horizon for the batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    created: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    errors: list[ImportIssue] = Field(default_factory=list)
    entry_ids: list[str] = Field(default_factory=list)
    drain_horizon_days: int | None = None
    drain_note: str | None = None


class ExportedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: Entry
    tags: list[str] = Field(default_factory=list)
    themes: list[EntryTheme] = Field(default_factory=list)


class LibraryExport(BaseModel):
    """FR-5 export: the whole library as one JSON document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str = "almanac-export"
    version: str = "1"
    exported_at: dt.datetime
    entries: list[ExportedEntry] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    collections: list[Collection] = Field(default_factory=list)
    collection_entries: dict[str, list[str]] = Field(default_factory=dict)
    surfacings: list[Surfacing] = Field(default_factory=list)
    reflections: list[Reflection] = Field(default_factory=list)
    attribution_flags: list[AttributionFlag] = Field(default_factory=list)
    config: dict[str, str] = Field(default_factory=dict)
