"""In-memory Repository backend for tests and evals.

Same interface and the same invariants as the SQLite backend — including the
FR-8 arrow of time, the surfacing uniqueness rules, and FR-12's documented
token-scan search ordering.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from almanac.models import (
    CONTESTED_POOLS,
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
from almanac.store.repository import (
    DuplicateError,
    Repository,
    token_scan,
)


class MemoryRepository(Repository):
    """Dict-backed storage. Nothing is shared between instances."""

    def __init__(self) -> None:
        self._themes: list[Theme] = []
        self._templates: list[PromptTemplate] = []
        self._misattributions: list[MisattributionRecord] = []
        self._config: dict[str, str] = {}
        self._entries: dict[str, Entry] = {}
        self._tags: dict[str, Tag] = {}
        self._entry_tags: dict[str, list[str]] = {}
        self._entry_themes: dict[str, list[EntryTheme]] = {}
        self._surfacings: dict[str, Surfacing] = {}
        self._reflections: dict[str, Reflection] = {}
        self._states: dict[str, SchedulerState] = {}
        self._collections: dict[str, Collection] = {}
        self._collection_entries: dict[str, list[str]] = {}
        self._flags: dict[tuple[str, str | None], AttributionFlag] = {}

    # --- committed datasets -------------------------------------------------
    def load_datasets(
        self,
        themes: Sequence[Theme],
        templates: Sequence[PromptTemplate],
        misattributions: Sequence[MisattributionRecord],
    ) -> None:
        self._themes = list(themes)
        self._templates = list(templates)
        self._misattributions = list(misattributions)

    def list_themes(self) -> list[Theme]:
        return list(self._themes)

    def list_templates(self) -> list[PromptTemplate]:
        return list(self._templates)

    def list_misattributions(self) -> list[MisattributionRecord]:
        return list(self._misattributions)

    # --- config -------------------------------------------------------------
    def get_config(self, key: str) -> str | None:
        return self._config.get(key)

    def set_config(self, key: str, value: str) -> None:
        self._config[key] = value

    def all_config(self) -> dict[str, str]:
        return dict(self._config)

    # --- entries ------------------------------------------------------------
    def add_entry(self, entry: Entry) -> None:
        if entry.id in self._entries:
            raise DuplicateError(f"entry {entry.id} already exists")
        self._entries[entry.id] = entry

    def get_entry(self, entry_id: str) -> Entry | None:
        return self._entries.get(entry_id)

    def update_entry(self, entry: Entry) -> None:
        if entry.id not in self._entries:
            raise KeyError(f"unknown entry {entry.id}")
        self._entries[entry.id] = entry

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
    ) -> list[Entry]:
        rows = sorted(self._entries.values(), key=lambda e: e.id)
        if status is not None:
            rows = [e for e in rows if e.status is status]
        if pinned is not None:
            rows = [e for e in rows if e.pinned is pinned]
        if kind is not None:
            rows = [e for e in rows if e.kind is kind]
        if tag is not None:
            tag_row = next((t for t in self._tags.values() if t.name == tag), None)
            if tag_row is None:
                return []
            rows = [e for e in rows if tag_row.id in self._entry_tags.get(e.id, [])]
        if theme is not None:
            rows = [
                e
                for e in rows
                if any(link.theme_id == theme for link in self._entry_themes.get(e.id, []))
            ]
        rows = rows[offset:]
        return rows[:limit] if limit is not None else rows

    def entries_by_hash(
        self, normalized_hash: str, *, include_archived: bool = False
    ) -> list[Entry]:
        rows = [e for e in self._entries.values() if e.normalized_hash == normalized_hash]
        if not include_archived:
            rows = [e for e in rows if e.status is EntryStatus.ACTIVE]
        return sorted(rows, key=lambda e: e.id)

    def search_entries(
        self, query: str, *, status: EntryStatus | None = EntryStatus.ACTIVE, limit: int = 50
    ) -> list[Entry]:
        pool = self.list_entries(status=status)
        return token_scan(pool, query, limit)

    # --- tags ---------------------------------------------------------------
    def upsert_tag(self, tag: Tag) -> Tag:
        existing = next((t for t in self._tags.values() if t.name == tag.name), None)
        if existing is not None:
            return existing
        self._tags[tag.id] = tag
        return tag

    def list_tags(self) -> list[Tag]:
        return sorted(self._tags.values(), key=lambda t: t.name)

    def set_entry_tags(self, entry_id: str, tag_ids: Sequence[str]) -> None:
        self._entry_tags[entry_id] = list(dict.fromkeys(tag_ids))
        self._gc_tags()

    def entry_tag_names(self, entry_id: str) -> list[str]:
        return sorted(
            self._tags[tag_id].name
            for tag_id in self._entry_tags.get(entry_id, [])
            if tag_id in self._tags
        )

    def _gc_tags(self) -> None:
        live = {tag_id for ids in self._entry_tags.values() for tag_id in ids}
        for tag_id in list(self._tags):
            if tag_id not in live:
                del self._tags[tag_id]

    # --- themes -------------------------------------------------------------
    def set_entry_themes(self, entry_id: str, themes: Sequence[EntryTheme]) -> None:
        self._entry_themes[entry_id] = sorted(themes, key=lambda link: link.theme_id)

    def entry_themes(self, entry_id: str) -> list[EntryTheme]:
        return list(self._entry_themes.get(entry_id, []))

    def all_entry_themes(self) -> dict[str, list[EntryTheme]]:
        return {entry_id: list(links) for entry_id, links in self._entry_themes.items()}

    # --- surfacings ---------------------------------------------------------
    def add_surfacing(self, surfacing: Surfacing) -> None:
        if surfacing.id in self._surfacings:
            raise DuplicateError(f"surfacing {surfacing.id} already exists")
        self.check_monotonic(surfacing)
        for existing in self._surfacings.values():
            if existing.on_date == surfacing.on_date:
                if existing.entry_id == surfacing.entry_id:
                    raise DuplicateError(
                        f"entry {surfacing.entry_id} already surfaced on {surfacing.on_date}"
                    )
                if (
                    existing.kind is SurfacingKind.DAILY
                    and surfacing.kind is SurfacingKind.DAILY
                    and existing.slot == surfacing.slot
                ):
                    raise DuplicateError(
                        f"slot {surfacing.slot} on {surfacing.on_date} is already filled"
                    )
        self._surfacings[surfacing.id] = surfacing

    def get_surfacing(self, surfacing_id: str) -> Surfacing | None:
        return self._surfacings.get(surfacing_id)

    def list_surfacings(
        self,
        *,
        entry_id: str | None = None,
        on_date: dt.date | None = None,
        kind: SurfacingKind | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[Surfacing]:
        rows = list(self._surfacings.values())
        if entry_id is not None:
            rows = [s for s in rows if s.entry_id == entry_id]
        if on_date is not None:
            rows = [s for s in rows if s.on_date == on_date]
        if kind is not None:
            rows = [s for s in rows if s.kind is kind]
        if date_from is not None:
            rows = [s for s in rows if s.on_date >= date_from]
        if date_to is not None:
            rows = [s for s in rows if s.on_date <= date_to]
        rows.sort(key=lambda s: (s.on_date, s.created_at, s.slot, s.id))
        return rows

    def max_surfacing_date(self, *, kind: SurfacingKind | None = None) -> dt.date | None:
        rows = [s for s in self._surfacings.values() if kind is None or s.kind is kind]
        return max((s.on_date for s in rows), default=None)

    def contested_pools(self, limit: int) -> list[SelectPool]:
        rows = [
            s
            for s in self._surfacings.values()
            if s.kind is SurfacingKind.DAILY and s.select_pool in CONTESTED_POOLS
        ]
        rows.sort(key=lambda s: (s.on_date, s.slot))
        return [s.select_pool for s in rows][-limit:] if limit > 0 else []

    def last_surfacing_for_entry(self, entry_id: str) -> Surfacing | None:
        rows = self.list_surfacings(entry_id=entry_id)
        return rows[-1] if rows else None

    def recent_template_ids(self, entry_id: str, limit: int) -> list[str]:
        rows = self.list_surfacings(entry_id=entry_id)
        return [s.prompt_template_id for s in rows[-limit:]] if limit > 0 else []

    # --- reflections --------------------------------------------------------
    def add_reflection(self, reflection: Reflection) -> None:
        if any(r.surfacing_id == reflection.surfacing_id for r in self._reflections.values()):
            raise DuplicateError(
                f"surfacing {reflection.surfacing_id} already carries a reflection"
            )
        self._reflections[reflection.id] = reflection

    def get_reflection_for_surfacing(self, surfacing_id: str) -> Reflection | None:
        return next((r for r in self._reflections.values() if r.surfacing_id == surfacing_id), None)

    def list_reflections(
        self,
        *,
        entry_id: str | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[Reflection]:
        rows = list(self._reflections.values())
        if entry_id is not None:
            rows = [r for r in rows if r.entry_id == entry_id]
        if date_from is not None:
            rows = [r for r in rows if r.logged_at.date() >= date_from]
        if date_to is not None:
            rows = [r for r in rows if r.logged_at.date() <= date_to]
        rows.sort(key=lambda r: (r.logged_at, r.id))
        return rows

    def grades_by_surfacing(self) -> dict[str, Grade]:
        return {r.surfacing_id: r.grade for r in self._reflections.values()}

    # --- scheduler state ----------------------------------------------------
    def get_state(self, entry_id: str) -> SchedulerState | None:
        return self._states.get(entry_id)

    def put_state(self, state: SchedulerState) -> None:
        self._states[state.entry_id] = state

    def all_states(self) -> dict[str, SchedulerState]:
        return dict(self._states)

    # --- collections --------------------------------------------------------
    def add_collection(self, collection: Collection) -> None:
        if any(c.name == collection.name for c in self._collections.values()):
            raise DuplicateError(f"collection named {collection.name!r} already exists")
        self._collections[collection.id] = collection
        self._collection_entries[collection.id] = []

    def get_collection(self, collection_id: str) -> Collection | None:
        return self._collections.get(collection_id)

    def list_collections(self) -> list[Collection]:
        return sorted(self._collections.values(), key=lambda c: c.name)

    def update_collection(self, collection: Collection) -> None:
        if collection.id not in self._collections:
            raise KeyError(f"unknown collection {collection.id}")
        self._collections[collection.id] = collection

    def delete_collection(self, collection_id: str) -> None:
        self._collections.pop(collection_id, None)
        self._collection_entries.pop(collection_id, None)

    def add_collection_entry(self, collection_id: str, entry_id: str) -> int:
        members = self._collection_entries.setdefault(collection_id, [])
        if entry_id in members:
            return members.index(entry_id)
        members.append(entry_id)
        return len(members) - 1

    def remove_collection_entry(self, collection_id: str, entry_id: str) -> None:
        members = self._collection_entries.get(collection_id, [])
        if entry_id in members:
            members.remove(entry_id)

    def collection_entry_ids(self, collection_id: str) -> list[str]:
        return list(self._collection_entries.get(collection_id, []))

    # --- attribution flags --------------------------------------------------
    def upsert_attribution_flag(self, flag: AttributionFlag) -> None:
        self._flags[(flag.entry_id, flag.misattribution_id)] = flag

    def list_attribution_flags(self, entry_id: str) -> list[AttributionFlag]:
        rows = [f for f in self._flags.values() if f.entry_id == entry_id]
        return sorted(rows, key=lambda f: (f.misattribution_id or "", f.id))
