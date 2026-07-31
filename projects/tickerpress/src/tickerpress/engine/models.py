"""Domain models (DATA_MODEL.md §2) as Pydantic v2 classes.

Every invariant the data model states is enforced here rather than left to the
store: ticker shape, alias uniqueness inputs, ``accepted == (score >= θ)``,
``relevance`` agreeing with its stored components, ``content_token_count == 0``
exactly when there is no content, and ``error is not None`` exactly when a
delivery failed. Append-only entities are ``frozen``.

Times are timezone-aware UTC and truncated to whole seconds. Truncation is
deliberate: it makes the ISO-8601 text written to SQLite uniform, so
lexicographic comparison of the stored strings is chronological, and it makes
``FileNotifier`` outbox names (``…-<utc-ts>-…``) deterministic.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

__all__ = [
    "TICKER_RE",
    "Alias",
    "AliasKind",
    "Appearance",
    "Article",
    "Channel",
    "Company",
    "DeliveryItem",
    "DeliveryKind",
    "DeliveryMode",
    "DeliveryStatus",
    "Delivery",
    "FeedResult",
    "Feed",
    "FetchStatus",
    "IngestRun",
    "IngestStatus",
    "MentionFeatures",
    "Mention",
    "MatchedVia",
    "PublishedSource",
    "Story",
    "Strength",
    "TextField",
    "ensure_utc",
    "iso_utc",
    "normalize_ticker",
    "parse_iso_utc",
    "round_half_up",
]

TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------
# scalar helpers
# --------------------------------------------------------------------------


def round_half_up(value: float) -> int:
    """``floor(x + 0.5)`` — SCOPE FR-8.

    Explicitly *not* Python's :func:`round`, whose banker's rounding maps
    62.5 -> 62 and 12.5 -> 12 and would break the attainable-relevance set the
    eval band rule depends on.
    """

    return math.floor(value + 0.5)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as a whole-second UTC datetime."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def iso_utc(value: datetime) -> str:
    """Canonical storage form: ``2026-03-02T13:00:00Z``."""

    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(text: str) -> datetime:
    """Inverse of :func:`iso_utc`, tolerant of ``+00:00`` and offsets."""

    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(raw))


def normalize_ticker(value: str) -> str:
    """Upper-case and validate a ticker (SCOPE FR-1)."""

    ticker = value.strip().upper()
    if not TICKER_RE.match(ticker):
        raise ValueError(
            f"invalid ticker {value!r}: expected ^[A-Z]{{1,5}}(\\.[A-Z])?$ (e.g. AAPL, BRK.B)"
        )
    return ticker


def _normalize_terms(values: list[str]) -> list[str]:
    """Case-fold, strip, drop empties, de-duplicate, preserve order."""

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = " ".join(value.split()).casefold()
        if not term or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc)]
Ticker = Annotated[str, AfterValidator(normalize_ticker)]
Relevance = Annotated[int, Field(ge=0, le=100)]


# --------------------------------------------------------------------------
# enumerations
# --------------------------------------------------------------------------


class DeliveryMode(str, Enum):
    DIGEST = "digest"
    ALERT = "alert"
    BOTH = "both"
    MUTE = "mute"


class AliasKind(str, Enum):
    LEGAL_NAME = "legal_name"
    SHORT_NAME = "short_name"
    TICKER_SYMBOL = "ticker_symbol"
    CASHTAG = "cashtag"
    NICKNAME = "nickname"


class Strength(str, Enum):
    STRONG = "strong"
    WEAK = "weak"


class TextField(str, Enum):
    TITLE = "title"
    SUMMARY = "summary"
    CONTENT = "content"


class MatchedVia(str, Enum):
    ALIAS = "alias"
    CASHTAG = "cashtag"
    EXCHANGE_QUALIFIED = "exchange_qualified"


class FetchStatus(str, Enum):
    OK = "ok"
    NOT_MODIFIED = "not_modified"
    ERROR = "error"


class IngestStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class PublishedSource(str, Enum):
    FEED = "feed"
    FALLBACK = "fallback"


class Channel(str, Enum):
    CONSOLE = "console"
    FILE = "file"
    EMAIL = "email"
    WEBHOOK = "webhook"


class DeliveryKind(str, Enum):
    DIGEST = "digest"
    ALERT = "alert"


class DeliveryStatus(str, Enum):
    COMPOSED = "composed"
    SENT = "sent"
    FAILED = "failed"


#: Field scan order — SCOPE FR-5 candidate emission order.
FIELD_ORDER: tuple[TextField, ...] = (TextField.TITLE, TextField.SUMMARY, TextField.CONTENT)

#: Default strength / prior per alias kind (DATA_MODEL §2.2).
KIND_DEFAULT_STRENGTH: dict[AliasKind, Strength] = {
    AliasKind.LEGAL_NAME: Strength.STRONG,
    AliasKind.TICKER_SYMBOL: Strength.STRONG,
    AliasKind.CASHTAG: Strength.STRONG,
    AliasKind.SHORT_NAME: Strength.WEAK,
    AliasKind.NICKNAME: Strength.WEAK,
}
KIND_DEFAULT_PRIOR: dict[AliasKind, float] = {
    AliasKind.LEGAL_NAME: 0.0,
    AliasKind.TICKER_SYMBOL: 0.0,
    AliasKind.CASHTAG: 0.0,
    AliasKind.SHORT_NAME: 0.25,
    AliasKind.NICKNAME: 0.10,
}
#: Prior for a ticker alias demoted to weak by the common-word list.
WEAK_TICKER_PRIOR = 0.05


# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------


class Company(BaseModel):
    """Watchlist entry (DATA_MODEL §2.1)."""

    model_config = ConfigDict(extra="forbid")

    ticker: Ticker
    name: str = Field(min_length=1)
    mode: DeliveryMode = DeliveryMode.DIGEST
    min_relevance: Relevance = 20
    alert_min_relevance: Relevance = 60
    context_terms: list[str] = Field(default_factory=list)
    anti_terms: list[str] = Field(default_factory=list)
    created_at: UtcDatetime

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if not name:
            raise ValueError("company name must not be blank")
        return name

    @field_validator("context_terms", "anti_terms")
    @classmethod
    def _clean_terms(cls, value: list[str]) -> list[str]:
        return _normalize_terms(value)


class Alias(BaseModel):
    """One matchable surface form (DATA_MODEL §2.2)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    company_ticker: Ticker
    text: str
    kind: AliasKind
    strength: Strength
    prior: float = Field(default=0.0, ge=0.0, le=0.3)
    generated: bool = False
    created_at: UtcDatetime

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("alias text must not be blank")
        return text

    @model_validator(mode="after")
    def _check_shape(self) -> Alias:
        if self.kind is AliasKind.TICKER_SYMBOL and not TICKER_RE.match(self.text):
            raise ValueError(f"ticker_symbol alias {self.text!r} is not a valid ticker")
        if self.kind is AliasKind.CASHTAG and not (
            self.text.startswith("$") and TICKER_RE.match(self.text[1:])
        ):
            raise ValueError(f"cashtag alias {self.text!r} must look like $TICKER")
        return self

    @property
    def key(self) -> str:
        """Uniqueness key within a company: case-folded surface."""

        return self.text.casefold()


class Feed(BaseModel):
    """Registered feed and its HTTP polling state (DATA_MODEL §2.3)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    enabled: bool = True
    etag: str | None = None
    last_modified: str | None = None
    last_polled_at: UtcDatetime | None = None
    last_status: FetchStatus | None = None
    created_at: UtcDatetime

    @field_validator("name", "url")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("url")
    @classmethod
    def _known_scheme(cls, value: str) -> str:
        scheme = value.split(":", 1)[0].lower() if ":" in value else ""
        if scheme not in {"file", "http", "https"}:
            raise ValueError(f"unsupported feed url scheme in {value!r}: expected file/http/https")
        return value


class Story(BaseModel):
    """A deduplicated story: a cluster of syndicated copies (DATA_MODEL §2.6)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    created_at: UtcDatetime
    first_published_at: UtcDatetime
    representative_article_id: int


class Article(BaseModel):
    """Archived feed item (DATA_MODEL §2.5). Append-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    feed_id: int
    item_guid: str = Field(min_length=1)
    url: str
    canonical_url: str
    title: str
    summary: str = ""
    content: str | None = None
    published_at: UtcDatetime
    published_source: PublishedSource
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime
    content_sha256: str
    token_count: int = Field(ge=0)
    content_token_count: int = Field(ge=0)
    story_id: int
    dedup_similarity: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("content_sha256")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("content_sha256 must be 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _content_tokens_match_content(self) -> Article:
        empty = not self.content
        if empty != (self.content_token_count == 0):
            raise ValueError(
                "content_token_count must be 0 exactly when content is empty (DATA_MODEL §2.5)"
            )
        return self

    def field_text(self, field: TextField) -> str:
        if field is TextField.TITLE:
            return self.title
        if field is TextField.SUMMARY:
            return self.summary
        return self.content or ""


class MentionFeatures(BaseModel):
    """The FR-6 feature vector, stored verbatim on every weak candidate."""

    model_config = ConfigDict(extra="forbid")

    prior: float = 0.0
    coref_strong: int = 0
    case_signal: int = 0
    window_cues: int = 0
    window_antis: int = 0
    doc_cues: int = 0
    doc_antis: int = 0
    ctx_terms: int = 0
    anti_terms: int = 0
    hyphen_compound: int = 0
    allcaps_run: int = 0

    def as_dict(self) -> dict[str, float]:
        """JSON-ready mapping in the DATA_MODEL §2.7 key order."""

        return {name: getattr(self, name) for name in MentionFeatures.model_fields}


class Mention(BaseModel):
    """One candidate detection, accepted or rejected (DATA_MODEL §2.7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    article_id: int
    company_ticker: Ticker
    alias_id: int | None = None
    field: TextField
    char_start: int = Field(ge=0)
    char_end: int
    surface: str = Field(min_length=1)
    matched_via: MatchedVia
    strength: Strength
    features: dict[str, float] = Field(default_factory=dict)
    score: float = Field(ge=0.0, le=1.0)
    threshold: float
    accepted: bool
    engine_version: str

    @model_validator(mode="after")
    def _check_invariants(self) -> Mention:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.accepted != (self.score >= self.threshold):
            raise ValueError("accepted must equal (score >= threshold) (DATA_MODEL §2.7)")
        if self.strength is Strength.STRONG:
            if self.features:
                raise ValueError("strong candidates are not scored and carry no feature vector")
            if self.score != 1.0:
                raise ValueError("strong candidates score 1.0")
        return self


class Appearance(BaseModel):
    """(article, company) rollup of accepted mentions (DATA_MODEL §2.8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    article_id: int
    company_ticker: Ticker
    mention_count: int = Field(ge=1)
    title_hit: bool
    lede_hit: bool
    relevance: Relevance

    @model_validator(mode="after")
    def _relevance_matches_components(self) -> Appearance:
        expected = round_half_up(
            100.0
            * (
                0.50 * int(self.title_hit)
                + 0.25 * int(self.lede_hit)
                + 0.25 * min(1.0, self.mention_count / 4.0)
            )
        )
        if self.relevance != expected:
            raise ValueError(
                f"relevance {self.relevance} disagrees with its components (expected {expected})"
            )
        return self


class FeedResult(BaseModel):
    """Per-feed outcome recorded in an IngestRun (DATA_MODEL §2.4)."""

    model_config = ConfigDict(extra="forbid")

    feed_id: int
    status: FetchStatus
    items_seen: int = Field(default=0, ge=0)
    items_new: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    error: str | None = None


class IngestRun(BaseModel):
    """One ``ingest`` invocation (DATA_MODEL §2.4). Append-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    status: IngestStatus
    feed_results: list[FeedResult] = Field(default_factory=list)
    articles_new: int = Field(default=0, ge=0)
    candidates_total: int = Field(default=0, ge=0)
    mentions_accepted: int = Field(default=0, ge=0)
    stories_new: int = Field(default=0, ge=0)
    alerts_sent: int = Field(default=0, ge=0)
    engine_version: str


class Delivery(BaseModel):
    """One composed message on one channel (DATA_MODEL §2.9). Append-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    channel: Channel
    kind: DeliveryKind
    created_at: UtcDatetime
    status: DeliveryStatus
    subject: str
    body_text: str
    error: str | None = None

    @model_validator(mode="after")
    def _error_iff_failed(self) -> Delivery:
        if (self.status is DeliveryStatus.FAILED) != (self.error is not None):
            raise ValueError("error must be set exactly when status is 'failed'")
        return self


class DeliveryItem(BaseModel):
    """One (channel, company, story) row of the exactly-once ledger (§2.10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    delivery_id: int
    channel: Channel
    company_ticker: Ticker
    story_id: int
    article_id: int
    relevance: Relevance
    counted: bool = False
