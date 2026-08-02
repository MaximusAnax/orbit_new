"""Pydantic v2 domain models for datasweep.

Mirrors ``docs/DATA_MODEL.md``.  Everything here is pure data: no I/O, no clock
reads, no randomness.  Times always arrive as data from the caller (the Clock
port lives in ``adapters/``).

Two identifier families, deliberately split (SCOPE.md FR-16):

* database surrogate keys (``Run.id``, ``IssueSummary.id``, ``Revision.id``)
  are UUID4 and never appear in an artifact;
* content-derived ids (``ReviewItem.id``) are deterministic hashes, because
  they are printed into ``report.md`` which is byte-identity gated.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------


class Tier(StrEnum):
    """Confidence tier of a proposed fix (SCOPE.md FR-7)."""

    AUTO = "auto"
    REVIEW = "review"
    REPORT = "report"


#: Safety ranking.  Higher = more conservative = fewer things happen silently.
TIER_RANK: dict[Tier, int] = {Tier.AUTO: 0, Tier.REVIEW: 1, Tier.REPORT: 2}


def stricter(a: Tier, b: Tier) -> Tier:
    """Combine two tiers, keeping the more conservative one.

    SCOPE.md FR-7 writes the combination as ``min(safety_cap, conf_tier)``
    "under the order auto < review < report", but the behaviour the same
    paragraph (and D12, and D1's whole safety argument) requires is the
    opposite of a literal ``min``: "a rule whose cap is auto but whose
    confidence falls below 0.95 lands in review automatically", and D10's
    edit-distance merges are "review cap always" even at confidence 1.0.
    A literal ``min`` under auto < review < report would auto-apply both.
    The FR's *intent* — always take the more restrictive of the two — is what
    is implemented here (resolution recorded in the CORE stage notes).
    """
    return a if TIER_RANK[a] >= TIER_RANK[b] else b


class IssueClass(StrEnum):
    """Detector classes (SCOPE.md FR-6) plus reader-emitted ``STR``."""

    ENC = "ENC"
    STR = "STR"
    WS = "WS"
    MISS = "MISS"
    TYPE = "TYPE"
    DATE = "DATE"
    CAT = "CAT"
    DUP = "DUP"
    OUT = "OUT"


#: Canonical ordering used whenever findings are serialized (DATA_MODEL §3.2).
ISSUE_CLASS_ORDER: dict[IssueClass, int] = {k: i for i, k in enumerate(list(IssueClass))}


class ColumnType(StrEnum):
    """Semantic column types (SCOPE.md FR-5)."""

    BOOL = "bool"
    DIGITS = "digits"
    INTEGER = "integer"
    FLOAT = "float"
    DATETIME = "datetime"
    DATE = "date"
    CATEGORICAL = "categorical"
    TEXT = "text"


#: Vote priority: ``bool > digits > integer > float > datetime > date >
#: categorical > text`` (SCOPE.md FR-5).
TYPE_PRIORITY: tuple[ColumnType, ...] = (
    ColumnType.BOOL,
    ColumnType.DIGITS,
    ColumnType.INTEGER,
    ColumnType.FLOAT,
    ColumnType.DATETIME,
    ColumnType.DATE,
    ColumnType.CATEGORICAL,
    ColumnType.TEXT,
)

#: Types that carry structure beyond "some string".  Used by the headerless
#: heuristic (FR-3): a header cell sitting in a ``text``/``categorical`` column
#: is not evidence that the header row is data.
STRUCTURED_TYPES: frozenset[ColumnType] = frozenset(
    {
        ColumnType.BOOL,
        ColumnType.DIGITS,
        ColumnType.INTEGER,
        ColumnType.FLOAT,
        ColumnType.DATETIME,
        ColumnType.DATE,
    }
)


class Stage(IntEnum):
    """Fixed pipeline order (SCOPE.md D13).  The integer value *is* the order."""

    ENC = 0
    STR = 1
    WS = 2
    MISS = 3
    TYPE = 4
    DATE = 5
    CAT = 6
    DUP = 7
    OUT = 8


class AuditKind(StrEnum):
    """Audit entry kinds (DATA_MODEL §3.1)."""

    CELL_CHANGE = "cell_change"
    ROW_DROP = "row_drop"
    ROW_PAD = "row_pad"
    HEADER_RENAME = "header_rename"
    COLUMN_ADD = "column_add"


#: Application order inside one stage, so the audit is replayable.
KIND_ORDER: dict[AuditKind, int] = {
    AuditKind.HEADER_RENAME: 0,
    AuditKind.ROW_PAD: 1,
    AuditKind.COLUMN_ADD: 2,
    AuditKind.CELL_CHANGE: 3,
    AuditKind.ROW_DROP: 4,
}


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REVIEW_PENDING = "review_pending"
    FAILED = "failed"
    SKIPPED = "skipped"


class TriggerKind(StrEnum):
    SCAN = "scan"
    MANUAL = "manual"
    FORCED = "forced"


class FileFormat(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    XLSX = "xlsx"
    JSONL = "jsonl"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Disposition(StrEnum):
    FIXED = "fixed"
    PROPOSED = "proposed"
    REPORTED = "reported"


TierOverride = Literal["auto", "review", "report", "off"]


# --------------------------------------------------------------------------
# Rule registry (SCOPE.md D12)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleSpec:
    """Static description of a rule: where it runs and how safe it may ever be."""

    rule: str
    klass: IssueClass
    stage: Stage
    cap: Tier
    #: Rules whose disposition the docs name outright ("a review item is
    #: raised").  For those the confidence→tier mapping is bypassed; the tier
    #: is the fixed one, still combined with the (policy-overridable) cap.
    fixed_tier: Tier | None = None
    #: Structural repairs that must be applied for the table to exist at all
    #: (the ``_overflow`` column).  Applied in every revision regardless of
    #: tier; the tier then only governs whether the user is asked to confirm.
    mandatory: bool = False
    #: Rules that only ever report - they never produce a table change.
    detect_only: bool = False
    #: Rules whose finding stands even when the proposed value equals the
    #: current one: the ambiguity itself is what the user must decide (D7.5).
    allow_identity: bool = False


def _rules(*specs: RuleSpec) -> dict[str, RuleSpec]:
    out: dict[str, RuleSpec] = {}
    for spec in specs:
        out[spec.rule] = spec
    return out


A, R, P = Tier.AUTO, Tier.REVIEW, Tier.REPORT

RULES: dict[str, RuleSpec] = _rules(
    RuleSpec("fix.mojibake", IssueClass.ENC, Stage.ENC, A),
    RuleSpec("fix.pad_row", IssueClass.STR, Stage.STR, A),
    RuleSpec("fix.dedupe_header", IssueClass.STR, Stage.STR, A),
    RuleSpec("fix.overflow_column", IssueClass.STR, Stage.STR, R, R, mandatory=True),
    RuleSpec("fix.synthetic_headers", IssueClass.STR, Stage.STR, R, R, detect_only=True),
    RuleSpec("detect.nested_value", IssueClass.STR, Stage.STR, P, P, detect_only=True),
    RuleSpec("fix.trim", IssueClass.WS, Stage.WS, A),
    RuleSpec("fix.nbsp", IssueClass.WS, Stage.WS, A),
    RuleSpec("fix.zero_width", IssueClass.WS, Stage.WS, A),
    RuleSpec("fix.nfc", IssueClass.WS, Stage.WS, A),
    RuleSpec("fix.collapse_spaces", IssueClass.WS, Stage.WS, A),
    RuleSpec("fix.sentinel_null_hard", IssueClass.MISS, Stage.MISS, A),
    RuleSpec("fix.sentinel_null_soft", IssueClass.MISS, Stage.MISS, R, R),
    RuleSpec("detect.numeric_sentinel", IssueClass.MISS, Stage.MISS, P, P, detect_only=True),
    RuleSpec("fix.number_canon", IssueClass.TYPE, Stage.TYPE, A),
    RuleSpec("fix.number_canon_ambiguous", IssueClass.TYPE, Stage.TYPE, A, allow_identity=True),
    RuleSpec("fix.currency_strip", IssueClass.TYPE, Stage.TYPE, A),
    RuleSpec("fix.currency_mixed", IssueClass.TYPE, Stage.TYPE, R, R),
    RuleSpec("fix.strip_apostrophe", IssueClass.TYPE, Stage.TYPE, A),
    RuleSpec(
        "detect.mixed_number_conventions",
        IssueClass.TYPE,
        Stage.TYPE,
        P,
        P,
        detect_only=True,
    ),
    RuleSpec("detect.coercion_failure", IssueClass.TYPE, Stage.TYPE, P, P, detect_only=True),
    RuleSpec("fix.date_canon", IssueClass.DATE, Stage.DATE, A),
    RuleSpec("fix.date_canon_ambiguous", IssueClass.DATE, Stage.DATE, R, R),
    RuleSpec("fix.excel_serial", IssueClass.DATE, Stage.DATE, R),
    RuleSpec("fix.two_digit_year", IssueClass.DATE, Stage.DATE, R),
    RuleSpec("fix.label_merge_fingerprint", IssueClass.CAT, Stage.CAT, A),
    RuleSpec("fix.label_merge_nn", IssueClass.CAT, Stage.CAT, R),
    RuleSpec("fix.drop_duplicate_row", IssueClass.DUP, Stage.DUP, A),
    RuleSpec("detect.outlier", IssueClass.OUT, Stage.OUT, P, P, detect_only=True),
)

del A, R, P

RULE_IDS: frozenset[str] = frozenset(RULES)


def rule_spec(rule: str) -> RuleSpec:
    try:
        return RULES[rule]
    except KeyError as exc:  # pragma: no cover - guarded by callers
        raise KeyError(f"unknown rule id: {rule!r}") from exc


# --------------------------------------------------------------------------
# Policy (DATA_MODEL §3.3)
# --------------------------------------------------------------------------


class GeneralPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settle_seconds: int = Field(default=5, ge=0)
    poll_interval_seconds: int = Field(default=5, ge=1)
    max_file_mb: int = Field(default=100, ge=1)
    max_rows: int = Field(default=500_000, ge=1)
    xlsx_sheet: int | str = 1


class DetectorPolicy(BaseModel):
    """Per-family switches.  Field names mirror the TOML keys exactly."""

    model_config = ConfigDict(extra="forbid")

    enc: bool = True
    ws: bool = True
    miss: bool = True
    type: bool = True
    date: bool = True
    cat: bool = True
    dup: bool = True
    out: bool = True

    def enabled(self, klass: IssueClass) -> bool:
        if klass is IssueClass.STR:
            return True  # structural repairs are not a switchable family (FR-6)
        return bool(getattr(self, klass.value.lower()))


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_majority: float = Field(default=0.90, ge=0.0, le=1.0)
    auto_confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    convention_agree: float = Field(default=0.95, ge=0.0, le=1.0)
    convention_min_decisive: int = Field(default=3, ge=0)
    merge_dominance: float = Field(default=0.80, ge=0.0, le=1.0)
    nn_max_ratio: float = Field(default=0.05, gt=0.0, le=1.0)
    nn_min_majority: int = Field(default=20, ge=0)
    outlier_iqr_k: float = Field(default=3.0, ge=0.0)
    outlier_mad_z: float = Field(default=3.5, ge=0.0)
    outlier_min_n: int = Field(default=20, ge=1)
    #: Distinct-count ceiling for the ``categorical`` type (FR-5).
    categorical_max_distinct: int = Field(default=50, ge=1)
    #: Distinct-ratio ceiling for the ``categorical`` type (FR-5).
    categorical_max_ratio: float = Field(default=0.10, ge=0.0, le=1.0)
    #: Distinct-ratio floor for fixed-width ``digits`` columns (FR-5).
    digits_min_distinct_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    #: Minimum width for the fixed-width ``digits`` rule (FR-5).
    digits_min_width: int = Field(default=4, ge=1)


class Policy(BaseModel):
    """Effective policy: built-in defaults ⊕ policy file ⊕ CLI overrides."""

    model_config = ConfigDict(extra="forbid")

    general: GeneralPolicy = Field(default_factory=GeneralPolicy)
    detectors: DetectorPolicy = Field(default_factory=DetectorPolicy)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    tiers: dict[str, TierOverride] = Field(default_factory=dict)

    @field_validator("tiers")
    @classmethod
    def _known_rule_ids(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - RULE_IDS)
        if unknown:
            raise ValueError(f"unknown rule id(s) in [tiers]: {', '.join(unknown)}")
        return value

    # -- helpers -----------------------------------------------------------

    @classmethod
    def from_toml(cls, text: str) -> Policy:
        """Parse a policy TOML document.  Unknown keys are a hard error."""
        return cls.model_validate(tomllib.loads(text))

    def merged(self, other: Policy | dict[str, Any] | None) -> Policy:
        """Deep-merge ``other`` (file or CLI overrides) over this policy."""
        if other is None:
            return self
        payload = other.model_dump(mode="json") if isinstance(other, Policy) else other
        base = self.model_dump(mode="json")
        for section, value in payload.items():
            if isinstance(value, dict) and isinstance(base.get(section), dict):
                base[section] = {**base[section], **value}
            else:
                base[section] = value
        return Policy.model_validate(base)

    def canonical_json(self) -> str:
        """Canonical JSON: sorted keys, no incidental whitespace."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def policy_hash(self) -> str:
        """sha256[:16] of the canonical-JSON effective policy (DATA_MODEL §2.3)."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]

    def rule_enabled(self, rule: str) -> bool:
        spec = rule_spec(rule)
        if not self.detectors.enabled(spec.klass):
            return False
        return self.tiers.get(rule) != "off"

    def disabled_rules(self) -> list[str]:
        return sorted(rule for rule, value in self.tiers.items() if value == "off")


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class RawTable(BaseModel):
    """The file's cell texts, verbatim (SCOPE.md D3).

    Cells are ``str | None``; ``None`` is the canonical null (D8).  Rows may be
    ragged: the *parsed original* keeps short and long rows exactly as the file
    had them, and the STR pipeline stage repairs them under audit (FR-3/FR-8),
    which is what makes ``revert`` reproduce the parsed original exactly.
    """

    model_config = ConfigDict(frozen=True)

    headers: list[str]
    rows: list[list[str | None]] = Field(default_factory=list)

    @field_validator("headers")
    @classmethod
    def _headers_are_strings(cls, value: list[str]) -> list[str]:
        for name in value:
            if not isinstance(name, str):  # pragma: no cover - pydantic coerces
                raise ValueError("header names must be strings")
        return value

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.headers)

    @property
    def width(self) -> int:
        """Widest extent of the table: headers or the longest row."""
        return max([len(self.headers), *(len(r) for r in self.rows)], default=0)

    @property
    def is_rectangular(self) -> bool:
        return all(len(row) == len(self.headers) for row in self.rows)

    def cell(self, row: int, col: int) -> str | None:
        if 0 <= row < len(self.rows):
            line = self.rows[row]
            if 0 <= col < len(line):
                return line[col]
        return None

    def column(self, col: int) -> list[str | None]:
        """Column values, padding short rows with ``None``."""
        return [row[col] if col < len(row) else None for row in self.rows]

    def header(self, col: int) -> str:
        if 0 <= col < len(self.headers):
            return self.headers[col]
        return f"col_{col}"

    def padded(self) -> RawTable:
        """A rectangular copy (short rows padded, long rows truncated)."""
        width = len(self.headers)
        rows = [
            list(row[:width]) + [None] * (width - len(row)) if len(row) != width else list(row)
            for row in self.rows
        ]
        return RawTable(headers=list(self.headers), rows=rows)


class ColumnProfile(BaseModel):
    """Per-column summary (DATA_MODEL §2.4), computed on the cleaned table."""

    model_config = ConfigDict(frozen=True)

    run_id: str = ""
    col_index: int = Field(ge=0)
    name: str
    original_name: str | None = None
    inferred_type: ColumnType
    type_coverage: float = Field(ge=0.0, le=1.0)
    non_null: int = Field(ge=0)
    null_count: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    stats: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _distinct_within_non_null(self) -> ColumnProfile:
        if self.distinct_count > self.non_null:
            raise ValueError("distinct_count cannot exceed non_null")
        return self


class TableProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    n_rows: int = Field(ge=0)
    n_cols: int = Field(ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)

    def type_of(self, col: int) -> ColumnType:
        for profile in self.columns:
            if profile.col_index == col:
                return profile.inferred_type
        return ColumnType.TEXT

    def name_of(self, col: int) -> str:
        for profile in self.columns:
            if profile.col_index == col:
                return profile.name
        return f"col_{col}"


# --------------------------------------------------------------------------
# Detection / planning
# --------------------------------------------------------------------------


class Proposal(BaseModel):
    """A detector's raw output: what is wrong, and what the repair would be.

    Detectors decide *what* changes; ``planner`` decides the confidence, the
    tier and the ordering (SCOPE.md FR-7).  ``evidence`` carries the raw inputs
    the per-rule confidence formulas need (share, dominance, agree, ratio…).
    """

    model_config = ConfigDict(frozen=True)

    rule: str
    kind: AuditKind = AuditKind.CELL_CHANGE
    row: int | None = None
    col: int | None = None
    before: str | None = None
    after: str | None = None
    before_row: list[str | None] | None = None
    cols: list[int] | None = None
    column_name: str | None = None
    column_cells: list[str | None] | None = None
    #: Groups cells into one human decision unit ("merge U.S.A. → USA").
    group: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class Fix(BaseModel):
    """A planned change with its tier decided (SCOPE.md FR-7)."""

    model_config = ConfigDict(frozen=True)

    rule: str
    klass: IssueClass
    stage: Stage
    kind: AuditKind
    tier: Tier
    confidence: float = Field(ge=0.0, le=1.0)
    row: int | None = None
    col: int | None = None
    col_name: str | None = None
    before: str | None = None
    after: str | None = None
    before_row: list[str | None] | None = None
    cols: list[int] | None = None
    column_name: str | None = None
    column_cells: list[str | None] | None = None
    mandatory: bool = False
    detect_only: bool = False
    group: str | None = None
    item_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[int, int, int, int, str]:
        return (
            int(self.stage),
            KIND_ORDER[self.kind],
            -1 if self.col is None else self.col,
            -1 if self.row is None else self.row,
            self.rule,
        )

    @property
    def changes_table(self) -> bool:
        return not self.detect_only


class Issue(BaseModel):
    """One detected issue instance — a ``findings.jsonl`` line (DATA_MODEL §3.2)."""

    model_config = ConfigDict(frozen=True)

    klass: IssueClass
    rule: str
    tier: Tier
    row: int | None = None
    col: int | None = None
    col_name: str | None = None
    value: str | None = None
    item_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        return (
            ISSUE_CLASS_ORDER[self.klass],
            -1 if self.col is None else self.col,
            -1 if self.row is None else self.row,
            self.rule,
        )

    def to_json_obj(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "klass": self.klass.value,
            "rule": self.rule,
            "tier": self.tier.value,
            "row": self.row,
            "col": self.col,
            "col_name": self.col_name,
            "value": self.value,
        }
        if self.item_id is not None:
            payload["item_id"] = self.item_id
        payload["evidence"] = self.evidence
        return payload


def select_applicable(fixes: list[Fix], accepted: frozenset[str] = frozenset()) -> list[Fix]:
    """Fixes applied for a revision: auto plan ⊕ mandatory ⊕ accepted items.

    The single gate through which anything reaches the table.  A review-tier
    fix is applied only once its item id appears in ``accepted`` — that is the
    whole safety contract (D1), so it lives in one place.
    """
    chosen = [
        fix
        for fix in fixes
        if fix.changes_table
        and (
            fix.tier is Tier.AUTO
            or fix.mandatory
            or (fix.item_id is not None and fix.item_id in accepted)
        )
    ]
    return sorted(chosen, key=lambda fix: fix.sort_key)


class FixPlan(BaseModel):
    """The full set of planned changes plus every detected instance."""

    model_config = ConfigDict(frozen=True)

    fixes: list[Fix] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    disabled_rules: list[str] = Field(default_factory=list)

    def applicable(self, accepted: frozenset[str] = frozenset()) -> list[Fix]:
        return select_applicable(self.fixes, accepted)

    def by_tier(self, tier: Tier) -> list[Fix]:
        return [fix for fix in self.fixes if fix.tier is tier]


class AuditEntry(BaseModel):
    """One applied change (DATA_MODEL §3.1).  Coordinates are original-table."""

    model_config = ConfigDict(frozen=True)

    kind: AuditKind
    rule: str
    tier: Tier
    confidence: float = Field(ge=0.0, le=1.0)
    row: int | None = None
    col: int | None = None
    col_name: str | None = None
    before: str | None = None
    after: str | None = None
    before_row: list[str | None] | None = None
    cols: list[int] | None = None
    column_name: str | None = None
    column_cells: list[str | None] | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    def to_json_obj(self) -> dict[str, Any]:
        """Per-kind serialization — only the fields that kind defines.

        ``exclude_none`` would be wrong: a ``sentinel → null`` fix is a
        ``cell_change`` whose ``after`` is JSON ``null`` and must stay visible.
        """
        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "rule": self.rule,
            "tier": self.tier.value,
            "confidence": self.confidence,
        }
        if self.kind is AuditKind.CELL_CHANGE:
            payload |= {
                "row": self.row,
                "col": self.col,
                "col_name": self.col_name,
                "before": self.before,
                "after": self.after,
            }
        elif self.kind is AuditKind.ROW_DROP:
            payload |= {"row": self.row, "before_row": self.before_row}
        elif self.kind is AuditKind.ROW_PAD:
            payload |= {"row": self.row, "cols": self.cols}
        elif self.kind is AuditKind.HEADER_RENAME:
            payload |= {"col": self.col, "before": self.before, "after": self.after}
        elif self.kind is AuditKind.COLUMN_ADD:
            payload |= {
                "col": self.col,
                "column_name": self.column_name,
                "column_cells": self.column_cells,
            }
        payload["evidence"] = self.evidence
        return payload


class ReviewItem(BaseModel):
    """One human decision unit (DATA_MODEL §2.6)."""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str = ""
    rule: str
    col_index: int | None = None
    description: str
    proposal: dict[str, Any] = Field(default_factory=dict)
    affected_cells: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    status: ReviewStatus = ReviewStatus.PENDING
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def _decided_at_iff_decided(self) -> ReviewItem:
        if (self.status is ReviewStatus.PENDING) != (self.decided_at is None):
            raise ValueError("decided_at is set exactly when status is not pending")
        cells = self.proposal.get("cells")
        if cells is not None and len(cells) != self.affected_cells:
            raise ValueError("affected_cells must equal len(proposal.cells)")
        return self

    def decide(self, status: ReviewStatus, now: datetime) -> ReviewItem:
        """Single legal transition: ``pending → accepted | rejected``."""
        if self.status is not ReviewStatus.PENDING:
            raise ValueError(f"review item {self.id} is already {self.status.value}")
        if status is ReviewStatus.PENDING:
            raise ValueError("cannot transition back to pending")
        return self.model_copy(update={"status": status, "decided_at": now})


def review_item_id(
    *,
    content_sha256: str,
    policy_hash: str,
    engine_version: str,
    rule: str,
    col_index: int | None,
    first_cell_row: int | None,
) -> str:
    """Content-derived 8-hex item id (DATA_MODEL §2.6).

    Contains no run id and no timestamp, so re-running the same content under
    the same policy yields the same ids — which is what lets ``report.md``
    (which prints them) stay byte-identical under FR-16.
    """
    payload = "|".join(
        [
            content_sha256,
            policy_hash,
            engine_version,
            rule,
            str(col_index),
            str(first_cell_row),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


class CleanResult(BaseModel):
    """Everything one cleaning pass produced (SCOPE.md §Architecture)."""

    model_config = ConfigDict(frozen=True)

    original: RawTable
    cleaned: RawTable
    profiles: list[ColumnProfile] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    plan: FixPlan = Field(default_factory=FixPlan)
    issue_counts: dict[str, int] = Field(default_factory=dict)
    change_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def has_review_queue(self) -> bool:
        return bool(self.review_items)


# --------------------------------------------------------------------------
# Persistent entities (DATA_MODEL §2)
# --------------------------------------------------------------------------

DEFAULT_INCLUDE: list[str] = ["*.csv", "*.tsv", "*.xlsx", "*.jsonl"]


class WatchedFolder(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    recursive: bool = True
    include: list[str] = Field(default_factory=lambda: list(DEFAULT_INCLUDE))
    policy_path: str | None = None
    output_dir: str
    enabled: bool = True
    created_at: datetime

    @field_validator("path", "output_dir")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("paths must be absolute and normalized")
        return value.rstrip("/") or "/"


class SourceFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    folder_id: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime


class Run(BaseModel):
    """One processing attempt of one content version under one policy."""

    model_config = ConfigDict(frozen=True)

    id: str
    file_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    engine_version: str
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus
    trigger: TriggerKind
    format: FileFormat | None = None
    encoding: str | None = None
    dialect: dict[str, Any] | None = None
    n_rows: int | None = None
    n_cols: int | None = None
    issue_counts: dict[str, int] = Field(default_factory=dict)
    change_counts: dict[str, int] = Field(default_factory=dict)
    artifact_dir: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _artifact_dir_iff_produced(self) -> Run:
        produced = self.status in (RunStatus.SUCCEEDED, RunStatus.REVIEW_PENDING)
        if produced != (self.artifact_dir is not None):
            raise ValueError("artifact_dir is non-null iff status is succeeded or review_pending")
        if (self.status is RunStatus.FAILED) != (self.error is not None):
            raise ValueError("error is set iff status is failed")
        for klass in self.issue_counts:
            if klass not in IssueClass.__members__:
                raise ValueError(f"unknown issue class in issue_counts: {klass}")
        return self


class IssueSummary(BaseModel):
    """DB-side rollup for queryability (DATA_MODEL §2.5)."""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    klass: IssueClass
    col_index: int | None = None
    cell_count: int = Field(ge=1)
    disposition: Disposition
    samples: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("samples")
    @classmethod
    def _cap_samples(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > 10:
            raise ValueError("IssueSummary.samples is capped at 10 examples")
        return value


class Revision(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    revision_no: int = Field(ge=1)
    created_at: datetime
    accepted_item_ids: list[str] = Field(default_factory=list)
    cleaned_path: str
    audit_path: str

    @model_validator(mode="after")
    def _revision_one_has_no_accepted_items(self) -> Revision:
        if self.revision_no == 1 and self.accepted_item_ids:
            raise ValueError("revision 1 is the auto-clean: it accepts no items")
        return self


class RunSummary(BaseModel):
    """One-line summary handed to the Notifier port (SCOPE.md FR-13)."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    source_name: str
    status: RunStatus
    n_rows: int | None = None
    n_cols: int | None = None
    issue_counts: dict[str, int] = Field(default_factory=dict)
    change_counts: dict[str, int] = Field(default_factory=dict)
    artifact_dir: str | None = None

    def one_line(self) -> str:
        issues = sum(self.issue_counts.values())
        auto = self.change_counts.get("auto", 0)
        review = self.change_counts.get("review", 0)
        return (
            f"datasweep {self.status.value}: {self.source_name} — "
            f"{issues} issues, {auto} auto changes, {review} awaiting review"
        )
