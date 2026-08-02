"""The Repository interface plus the search helper both backends share.

SQLite is the default backend; the in-memory backend implements the identical
interface for tests and evals.  FR-8's arrow-of-time rule is enforced here (in
repository code) because SQLite ``CHECK`` cannot express a cross-row
constraint.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from almanac.engine.normalize import stemmed_tokens
from almanac.models import (
    AttributionFlag,
    Collection,
    Entry,
    EntryKind,
    EntryStatus,
    EntryTheme,
    Grade,
    MisattributionRecord,
    PromptTemplate,
    Reflection,
    SchedulerState,
    SelectPool,
    Surfacing,
    SurfacingKind,
    Tag,
    Theme,
)


class RepositoryError(RuntimeError):
    """Base class for storage-level rule violations."""


class MonotonicityError(RepositoryError):
    """A surfacing would break the FR-8 arrow of time."""


class DuplicateError(RepositoryError):
    """A uniqueness constraint would be violated."""


# --------------------------------------------------------------------------
# Shared search helper (FR-12 fallback ordering)
# --------------------------------------------------------------------------


def matched_token_count(entry: Entry, query_stems: Sequence[str]) -> int:
    """Distinct query tokens (stemmed) present in the entry's searchable text."""
    haystack = set(
        stemmed_tokens(
            " ".join(part for part in (entry.text, entry.author, entry.source, entry.note) if part)
        )
    )
    return sum(1 for stem in set(query_stems) if stem in haystack)


def token_scan(entries: Iterable[Entry], query: str, limit: int | None = None) -> list[Entry]:
    """FR-12's documented non-FTS fallback.

    Result set: entries containing **all** query tokens after FR-1
    normalization plus porter stemming.  Order: descending count of distinct
    matched query tokens, then ascending entry id.  This is deliberately *not*
    bm25 parity — set membership matches, relevance ranking does not.
    """
    stems = stemmed_tokens(query)
    if not stems:
        return []
    wanted = set(stems)
    scored: list[tuple[int, str, Entry]] = []
    for entry in entries:
        count = matched_token_count(entry, stems)
        if count == len(wanted):
            scored.append((count, entry.id, entry))
    scored.sort(key=lambda row: (-row[0], row[1]))
    results = [row[2] for row in scored]
    return results[:limit] if limit is not None else results


# --------------------------------------------------------------------------
# Repository interface
# --------------------------------------------------------------------------


class Repository(ABC):
    """Everything the service layer needs from storage."""

    # --- committed datasets -------------------------------------------------
    @abstractmethod
    def load_datasets(
        self,
        themes: Sequence[Theme],
        templates: Sequence[PromptTemplate],
        misattributions: Sequence[MisattributionRecord],
    ) -> None:
        """Install the read-only committed data (``almanac init``)."""

    @abstractmethod
    def list_themes(self) -> list[Theme]: ...

    @abstractmethod
    def list_templates(self) -> list[PromptTemplate]: ...

    @abstractmethod
    def list_misattributions(self) -> list[MisattributionRecord]: ...

    # --- config -------------------------------------------------------------
    @abstractmethod
    def get_config(self, key: str) -> str | None: ...

    @abstractmethod
    def set_config(self, key: str, value: str) -> None: ...

    @abstractmethod
    def all_config(self) -> dict[str, str]: ...

    # --- entries ------------------------------------------------------------
    @abstractmethod
    def add_entry(self, entry: Entry) -> None: ...

    @abstractmethod
    def get_entry(self, entry_id: str) -> Entry | None: ...

    @abstractmethod
    def update_entry(self, entry: Entry) -> None: ...

    @abstractmethod
    def list_entries(
        self,
        *,
        status: EntryStatus | None = None,
        pinned: bool | None = None,
        kind: EntryKind | None = None,
        tag: str | None = None,
        theme: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Entry]: ...

    @abstractmethod
    def entries_by_hash(
        self, normalized_hash: str, *, include_archived: bool = False
    ) -> list[Entry]: ...

    @abstractmethod
    def search_entries(
        self, query: str, *, status: EntryStatus | None = EntryStatus.ACTIVE, limit: int = 50
    ) -> list[Entry]: ...

    # --- tags ---------------------------------------------------------------
    @abstractmethod
    def upsert_tag(self, tag: Tag) -> Tag:
        """Insert the tag, or return the existing row with the same name."""

    @abstractmethod
    def list_tags(self) -> list[Tag]: ...

    @abstractmethod
    def set_entry_tags(self, entry_id: str, tag_ids: Sequence[str]) -> None: ...

    @abstractmethod
    def entry_tag_names(self, entry_id: str) -> list[str]: ...

    # --- themes -------------------------------------------------------------
    @abstractmethod
    def set_entry_themes(self, entry_id: str, themes: Sequence[EntryTheme]) -> None: ...

    @abstractmethod
    def entry_themes(self, entry_id: str) -> list[EntryTheme]: ...

    @abstractmethod
    def all_entry_themes(self) -> dict[str, list[EntryTheme]]: ...

    # --- surfacings ---------------------------------------------------------
    @abstractmethod
    def add_surfacing(self, surfacing: Surfacing) -> None:
        """Append a surfacing, enforcing the FR-8 arrow of time."""

    @abstractmethod
    def get_surfacing(self, surfacing_id: str) -> Surfacing | None: ...

    @abstractmethod
    def list_surfacings(
        self,
        *,
        entry_id: str | None = None,
        on_date: dt.date | None = None,
        kind: SurfacingKind | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[Surfacing]:
        """Ordered by (on_date, created_at, slot)."""

    @abstractmethod
    def max_surfacing_date(self, *, kind: SurfacingKind | None = None) -> dt.date | None: ...

    @abstractmethod
    def contested_pools(self, limit: int) -> list[SelectPool]:
        """The trailing ``limit`` contested-slot stamps in (on_date, slot) order."""

    @abstractmethod
    def last_surfacing_for_entry(self, entry_id: str) -> Surfacing | None: ...

    @abstractmethod
    def recent_template_ids(self, entry_id: str, limit: int) -> list[str]: ...

    # --- reflections --------------------------------------------------------
    @abstractmethod
    def add_reflection(self, reflection: Reflection) -> None: ...

    @abstractmethod
    def get_reflection_for_surfacing(self, surfacing_id: str) -> Reflection | None: ...

    @abstractmethod
    def list_reflections(
        self,
        *,
        entry_id: str | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[Reflection]: ...

    @abstractmethod
    def grades_by_surfacing(self) -> dict[str, Grade]: ...

    # --- scheduler state ----------------------------------------------------
    @abstractmethod
    def get_state(self, entry_id: str) -> SchedulerState | None: ...

    @abstractmethod
    def put_state(self, state: SchedulerState) -> None: ...

    @abstractmethod
    def all_states(self) -> dict[str, SchedulerState]: ...

    # --- collections --------------------------------------------------------
    @abstractmethod
    def add_collection(self, collection: Collection) -> None: ...

    @abstractmethod
    def get_collection(self, collection_id: str) -> Collection | None: ...

    @abstractmethod
    def list_collections(self) -> list[Collection]: ...

    @abstractmethod
    def update_collection(self, collection: Collection) -> None: ...

    @abstractmethod
    def delete_collection(self, collection_id: str) -> None: ...

    @abstractmethod
    def add_collection_entry(self, collection_id: str, entry_id: str) -> int:
        """Append the entry; returns its position. Idempotent."""

    @abstractmethod
    def remove_collection_entry(self, collection_id: str, entry_id: str) -> None:
        """Remove and re-pack positions so they stay contiguous from 0."""

    @abstractmethod
    def collection_entry_ids(self, collection_id: str) -> list[str]: ...

    # --- attribution flags --------------------------------------------------
    @abstractmethod
    def upsert_attribution_flag(self, flag: AttributionFlag) -> None: ...

    @abstractmethod
    def list_attribution_flags(self, entry_id: str) -> list[AttributionFlag]: ...

    # --- lifecycle ----------------------------------------------------------
    def close(self) -> None:  # noqa: B027 - optional hook, not every backend holds resources
        """Release any resources held by the backend. Backends without any may ignore it."""

    # --- shared helpers -----------------------------------------------------
    def check_monotonic(self, surfacing: Surfacing) -> None:
        """FR-8: a new surfacing's ``on_date`` is >= every existing one's.

        Applies to daily *and* extra rows: without it a backdated draw would
        reorder the FR-6 fold and retroactively change past scheduler states.
        The stricter "strictly greater than the newest *daily* date" rule is a
        property of a whole daily materialization, not of one row, so it lives
        in :meth:`check_daily_materialization`.
        """
        newest = self.max_surfacing_date()
        if newest is not None and surfacing.on_date < newest:
            raise MonotonicityError(
                f"surfacing on {surfacing.on_date} precedes the newest surfacing ({newest})"
            )

    def check_daily_materialization(self, on_date: dt.date) -> None:
        """FR-8: a daily set may only be materialized after the last one."""
        newest_daily = self.max_surfacing_date(kind=SurfacingKind.DAILY)
        if newest_daily is not None and on_date <= newest_daily:
            raise MonotonicityError(
                f"a daily set was already materialized on {newest_daily}; "
                f"missed days are never backfilled"
            )
