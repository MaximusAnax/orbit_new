"""The orchestration shell: capture, materialize, draw, reflect, curate.

`service.py` sits *outside* ``engine/`` on purpose (SCOPE.md D11): it holds an
injected Repository and adapters and therefore performs I/O.  It contains no
business rules — only sequencing: load state, call the engine, persist the
result.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from collections.abc import Iterable, Sequence

from almanac.adapters.attribution import AttributionChecker
from almanac.adapters.clock import Clock
from almanac.adapters.ids import IdFactory
from almanac.adapters.personalizer import PromptPersonalizer
from almanac.engine import prompts as prompt_engine
from almanac.engine import scheduler as sched
from almanac.engine.normalize import normalize_tag, normalized_hash
from almanac.engine.stats import build_stats, drain_horizon_days, fold_events_by_entry
from almanac.engine.themes import MAX_THEMES_PER_ENTRY, suggest_themes
from almanac.engine.validate import validate_prompt
from almanac.models import (
    AttributionFlag,
    CaptureResult,
    Card,
    CardContext,
    Collection,
    DatasetBundle,
    Entry,
    EntryDetail,
    EntryKind,
    EntryStatus,
    EntryTheme,
    ExportedEntry,
    Grade,
    ImportCandidate,
    ImportIssue,
    ImportReport,
    LibraryExport,
    Reflection,
    SchedulerParams,
    SelectPool,
    StarterQuote,
    StatsReport,
    Surfacing,
    SurfacingKind,
    Tag,
    ThemeSource,
    ThemeSuggestion,
)
from almanac.store.repository import Repository

#: FR-5's documented CSV header (Readwise-style exports map onto it).
CSV_COLUMNS = ("text", "author", "source", "url", "tags", "note")

CONFIG_SEED = "seed"
CONFIG_BATCH_K = "batch_k"
CONFIG_SCHEMA_VERSION = "schema_version"
CONFIG_DATA_VERSIONS = "data_versions"

#: FR-4: `almanac pin` warns once the pinned set exceeds this multiple of k.
PIN_BUDGET_PER_SLOT = 8


class ServiceError(RuntimeError):
    """Base class for service-level rule violations."""


class NotFound(ServiceError):
    """The requested entity does not exist."""


class Conflict(ServiceError):
    """The request is valid but the current state forbids it (HTTP 409)."""


class ValidationFailed(ServiceError):
    """The request is malformed or violates an invariant (HTTP 422)."""


class AlmanacService:
    """Everything the API and CLI call into."""

    def __init__(
        self,
        repo: Repository,
        datasets: DatasetBundle,
        clock: Clock,
        ids: IdFactory,
        personalizer: PromptPersonalizer,
        attribution: AttributionChecker,
        seed: int = 0,
        batch_k: int | None = None,
    ) -> None:
        self.repo = repo
        self.datasets = datasets
        self.clock = clock
        self.ids = ids
        self.personalizer = personalizer
        self.attribution = attribution
        self._seed = seed
        self._batch_k = batch_k or datasets.params.k
        self._themes = datasets.theme_map()

    # --- configuration ------------------------------------------------------
    @property
    def params(self) -> SchedulerParams:
        return self.datasets.params

    @property
    def seed(self) -> int:
        stored = self.repo.get_config(CONFIG_SEED)
        return int(stored) if stored is not None else self._seed

    @property
    def batch_k(self) -> int:
        stored = self.repo.get_config(CONFIG_BATCH_K)
        return int(stored) if stored is not None else self._batch_k

    def initialize(self) -> None:
        """Install the committed datasets and default config (``almanac init``)."""
        self.repo.load_datasets(
            self.datasets.themes, self.datasets.templates, self.datasets.misattributions
        )
        self.repo.set_config(CONFIG_SCHEMA_VERSION, "1")
        self.repo.set_config(
            CONFIG_DATA_VERSIONS, json.dumps({"scheduler": self.params.params_version})
        )
        if self.repo.get_config(CONFIG_SEED) is None:
            self.repo.set_config(CONFIG_SEED, str(self._seed))
        if self.repo.get_config(CONFIG_BATCH_K) is None:
            self.repo.set_config(CONFIG_BATCH_K, str(self._batch_k))

    def set_seed(self, seed: int) -> None:
        self.repo.set_config(CONFIG_SEED, str(seed))

    def set_batch_k(self, k: int) -> None:
        if not 1 <= k <= 5:
            raise ValidationFailed("batch k must be between 1 and 5")
        self.repo.set_config(CONFIG_BATCH_K, str(k))

    # --- FR-1/2/3 capture ---------------------------------------------------
    def capture(
        self,
        text: str,
        kind: EntryKind = EntryKind.QUOTE,
        author: str | None = None,
        source: str | None = None,
        url: str | None = None,
        note: str | None = None,
        tags: Sequence[str] = (),
        themes: Sequence[str] = (),
        captured_on: dt.date | None = None,
        now: dt.datetime | None = None,
        accept_suggestions: bool = False,
    ) -> CaptureResult:
        """Create an entry with normalization, dedupe warning, themes and flags.

        ``captured_on`` defaults to the ``Clock`` adapter's date rather than to
        the wall clock: it is an input to the FR-7 novelty age and to the M2
        latency metric, so it must come from the same time source the scheduler
        is driven by (SCOPE.md D14).
        """
        timestamp = now or dt.datetime.now(dt.UTC)
        entry = Entry(
            id=self.ids.new_id(),
            kind=kind,
            text=text,
            normalized_hash=normalized_hash(text),
            author=author,
            source=source,
            url=url,
            note=note,
            captured_on=captured_on or self.clock.today(),
            created_at=timestamp,
            updated_at=timestamp,
        )
        duplicates = self.repo.entries_by_hash(entry.normalized_hash)
        self.repo.add_entry(entry)
        self._apply_tags(entry.id, tags)

        suggestions = suggest_themes(self.datasets.themes, text, tags, note)
        chosen = list(dict.fromkeys(themes))
        source_kind = ThemeSource.USER
        if not chosen and accept_suggestions and suggestions and suggestions[0].score > 0:
            chosen = [suggestions[0].theme_id]
            source_kind = ThemeSource.SUGGESTED
        self._apply_themes(entry.id, chosen, source_kind)

        flags = self._record_attribution(entry, timestamp)
        return CaptureResult(
            entry=entry,
            suggestions=suggestions,
            attribution_flags=flags,
            duplicate_of=duplicates[0].id if duplicates else None,
            tags=self.repo.entry_tag_names(entry.id),
            themes=[link.theme_id for link in self.repo.entry_themes(entry.id)],
        )

    def duplicates_of(self, text: str) -> list[Entry]:
        """Active entries whose normalized text already matches ``text`` (FR-1).

        The CLI calls this *before* capture so it can ask for confirmation; the
        API reports the collision after the fact in ``CaptureResult``.
        """
        return self.repo.entries_by_hash(normalized_hash(text))

    def suggest(
        self, text: str, tags: Sequence[str] = (), note: str | None = None
    ) -> list[ThemeSuggestion]:
        return suggest_themes(self.datasets.themes, text, tags, note)

    def check_attribution(self, text: str, author: str | None) -> list:
        """FR-2 on demand. Never blocks anything; returns findings."""
        return self.attribution.check(text, author)

    def _apply_tags(self, entry_id: str, tags: Iterable[str]) -> None:
        tag_ids: list[str] = []
        for raw in tags:
            name = normalize_tag(raw)
            if not name:
                continue
            stored = self.repo.upsert_tag(Tag(id=self.ids.new_id(), name=name))
            tag_ids.append(stored.id)
        self.repo.set_entry_tags(entry_id, tag_ids)

    def _apply_themes(self, entry_id: str, theme_ids: Sequence[str], source: ThemeSource) -> None:
        if len(theme_ids) > MAX_THEMES_PER_ENTRY:
            raise ValidationFailed(f"an entry may carry at most {MAX_THEMES_PER_ENTRY} themes")
        unknown = [tid for tid in theme_ids if tid not in self._themes]
        if unknown:
            raise ValidationFailed(f"unknown theme ids: {sorted(unknown)}")
        self.repo.set_entry_themes(
            entry_id,
            [EntryTheme(entry_id=entry_id, theme_id=tid, source=source) for tid in theme_ids],
        )

    def _record_attribution(self, entry: Entry, now: dt.datetime) -> list[AttributionFlag]:
        flags: list[AttributionFlag] = []
        for finding in self.attribution.check(entry.text, entry.author):
            flag = AttributionFlag(
                id=self.ids.new_id(),
                entry_id=entry.id,
                misattribution_id=finding.misattribution_id,
                verdict=finding.verdict,
                note=f"{finding.note} Likely origin: {finding.likely_origin}.",
                reference_url=finding.reference_url,
                checked_at=now,
            )
            self.repo.upsert_attribution_flag(flag)
            flags.append(flag)
        return flags

    # --- FR-4 curation ------------------------------------------------------
    def get_entry(self, entry_id: str) -> Entry:
        entry = self.repo.get_entry(entry_id)
        if entry is None:
            raise NotFound(f"unknown entry {entry_id}")
        return entry

    def edit_entry(
        self,
        entry_id: str,
        text: str | None = None,
        author: str | None = None,
        source: str | None = None,
        url: str | None = None,
        note: str | None = None,
        tags: Sequence[str] | None = None,
        themes: Sequence[str] | None = None,
        now: dt.datetime | None = None,
    ) -> Entry:
        """FR-4: a post-surfacing text edit is accepted iff the hash is unchanged."""
        entry = self.get_entry(entry_id)
        updates: dict[str, object] = {}
        if text is not None and text != entry.text:
            new_hash = normalized_hash(text)
            has_history = bool(self.repo.list_surfacings(entry_id=entry_id))
            if has_history and new_hash != entry.normalized_hash:
                raise ValidationFailed(
                    "this entry has been surfaced, so its text may only change in ways that "
                    "leave the normalized text identical (punctuation, casing, whitespace, "
                    "diacritics). For a semantic change, archive this entry and re-add it — "
                    "that starts a fresh scheduling and reflection history."
                )
            updates["text"] = text
            updates["normalized_hash"] = new_hash
        for field, value in (
            ("author", author),
            ("source", source),
            ("url", url),
            ("note", note),
        ):
            if value is not None:
                updates[field] = value
        if updates:
            updates["updated_at"] = now or dt.datetime.now(dt.UTC)
            entry = entry.model_copy(update=updates)
            self.repo.update_entry(entry)
        if tags is not None:
            self._apply_tags(entry_id, tags)
        if themes is not None:
            self._apply_themes(entry_id, themes, ThemeSource.USER)
        return entry

    def pin(self, entry_id: str, now: dt.datetime | None = None) -> tuple[Entry, str | None]:
        """Pin an entry; returns the entry and a warning when past the budget."""
        entry = self.get_entry(entry_id)
        entry = entry.model_copy(
            update={"pinned": True, "updated_at": now or dt.datetime.now(dt.UTC)}
        )
        self.repo.update_entry(entry)
        pinned = len(self.repo.list_entries(status=EntryStatus.ACTIVE, pinned=True))
        budget = PIN_BUDGET_PER_SLOT * self.batch_k
        warning = None
        if pinned > budget:
            bound = self.params.P_rescue + -(-pinned // self.batch_k)
            warning = (
                f"{pinned} pinned entries exceeds the budget of {budget} for k={self.batch_k}: "
                f"the pinned guarantee degrades from 45 to {bound} days"
            )
        return entry, warning

    def unpin(self, entry_id: str, now: dt.datetime | None = None) -> Entry:
        entry = self.get_entry(entry_id).model_copy(
            update={"pinned": False, "updated_at": now or dt.datetime.now(dt.UTC)}
        )
        self.repo.update_entry(entry)
        return entry

    def set_status(
        self, entry_id: str, status: EntryStatus, now: dt.datetime | None = None
    ) -> Entry:
        entry = self.get_entry(entry_id).model_copy(
            update={"status": status, "updated_at": now or dt.datetime.now(dt.UTC)}
        )
        self.repo.update_entry(entry)
        return entry

    def archive(self, entry_id: str, now: dt.datetime | None = None) -> Entry:
        return self.set_status(entry_id, EntryStatus.ARCHIVED, now)

    def restore(self, entry_id: str, now: dt.datetime | None = None) -> Entry:
        return self.set_status(entry_id, EntryStatus.ACTIVE, now)

    # --- FR-7/FR-8 materialization -----------------------------------------
    def scheduler_entries(self) -> list[sched.SchedulerEntry]:
        states = self.repo.all_states()
        return [
            sched.SchedulerEntry.build(
                entry, states.get(entry.id) or sched.initial_state(entry.id, self.params)
            )
            for entry in self.repo.list_entries(status=EntryStatus.ACTIVE)
        ]

    def get_day(self, on_date: dt.date) -> list[Card]:
        """Read-only view of an already materialized day."""
        rows = self.repo.list_surfacings(on_date=on_date, kind=SurfacingKind.DAILY)
        return [self.card_for(row) for row in rows]

    def materialize_day(
        self, on_date: dt.date | None = None, now: dt.datetime | None = None
    ) -> list[Card]:
        """FR-8: materialize the daily set for a date at most once."""
        on = on_date or self.clock.today()
        existing = self.repo.list_surfacings(on_date=on, kind=SurfacingKind.DAILY)
        if existing:
            return [self.card_for(row) for row in existing]
        if on > self.clock.today():
            raise ValidationFailed(f"{on} is in the future")
        self.repo.check_daily_materialization(on)
        decisions = self.select_slots(
            self.scheduler_entries(), self.repo.contested_pools(self.params.H), on
        )
        return [
            self._materialize(
                decision.entry_id,
                decision.select_pool,
                on,
                decision.slot,
                SurfacingKind.DAILY,
                now=now,
            )
            for decision in decisions
        ]

    def select_slots(
        self,
        entries: Sequence[sched.SchedulerEntry],
        contested: Sequence[SelectPool],
        on_date: dt.date,
    ) -> list[sched.SlotDecision]:
        """FR-7 selection for one date.

        Factored out as the single seam the eval suite substitutes to measure
        naive baseline policies against the same eligibility, cooldown
        bookkeeping and prompt selection (EVALS.md section 5).  Production code
        never overrides it.
        """
        return sched.select_daily(entries, contested, on_date, self.seed, self.params, self.batch_k)

    def draw(
        self,
        on_date: dt.date | None = None,
        theme_id: str | None = None,
        collection_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> Card | None:
        """FR-8 extra draw: an on-demand card, optionally filtered."""
        on = on_date or self.clock.today()
        if on > self.clock.today():
            raise ValidationFailed(f"{on} is in the future")
        allowed: set[str] | None = None
        if theme_id is not None:
            if theme_id not in self._themes:
                raise ValidationFailed(f"unknown theme {theme_id}")
            allowed = {
                entry_id
                for entry_id, links in self.repo.all_entry_themes().items()
                if any(link.theme_id == theme_id for link in links)
            }
        if collection_id is not None:
            if self.repo.get_collection(collection_id) is None:
                raise NotFound(f"unknown collection {collection_id}")
            members = set(self.repo.collection_entry_ids(collection_id))
            allowed = members if allowed is None else (allowed & members)
        decision = sched.select_extra(self.scheduler_entries(), on, self.seed, self.params, allowed)
        if decision is None:
            return None
        return self._materialize(
            decision.entry_id,
            SelectPool.EXTRA,
            on,
            0,
            SurfacingKind.EXTRA,
            filter_theme_id=theme_id,
            filter_collection_id=collection_id,
            now=now,
        )

    def _materialize(
        self,
        entry_id: str,
        pool: SelectPool,
        on_date: dt.date,
        slot: int,
        kind: SurfacingKind,
        filter_theme_id: str | None = None,
        filter_collection_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> Card:
        entry = self.get_entry(entry_id)
        timestamp = now or dt.datetime.now(dt.UTC)
        state = self.repo.get_state(entry_id) or sched.initial_state(entry_id, self.params)
        previous = self.repo.last_surfacing_for_entry(entry_id)
        prior_grade = self._grade_of(previous)
        theme_ids = [link.theme_id for link in self.repo.entry_themes(entry_id)]
        theme_names = [self._themes[tid].name for tid in theme_ids]

        rendered = prompt_engine.build_prompt(
            self.repo.list_templates() or self.datasets.templates,
            theme_ids,
            theme_names,
            state.exposure_count,
            prior_grade,
            self.repo.recent_template_ids(entry_id, self.params.prompt_reuse_window),
            self.seed,
            on_date,
            entry_id,
            entry.text,
            entry.author,
            entry.source,
        )
        text, personalized, fell_back = self._personalize(entry, rendered, theme_ids, theme_names)

        surfacing = Surfacing(
            id=self.ids.new_id(),
            entry_id=entry_id,
            on_date=on_date,
            slot=slot,
            kind=kind,
            select_pool=pool,
            prompt_template_id=rendered.template.id,
            prompt_kind=rendered.template.kind,
            prompt_text=text,
            personalized=personalized,
            personalize_fell_back=fell_back,
            relaxed_cooldown=pool is SelectPool.RELAXED,
            prompt_recency_relaxed=rendered.recency_relaxed,
            filter_theme_id=filter_theme_id,
            filter_collection_id=filter_collection_id,
            scheduler_version=self.params.params_version,
            seed=self.seed,
            created_at=timestamp,
        )
        self.repo.add_surfacing(surfacing)
        self.repo.put_state(sched.advance_state(state, on_date, prior_grade, self.params))
        return self.card_for(surfacing, entry=entry, theme_ids=theme_ids)

    def _personalize(
        self,
        entry: Entry,
        rendered: prompt_engine.RenderedPrompt,
        theme_ids: Sequence[str],
        theme_names: Sequence[str],
    ) -> tuple[str, bool, bool]:
        """Run the personalizer and the FR-10 validator; fall back on failure."""
        context = CardContext(
            entry_text=entry.text,
            entry_note=entry.note,
            entry_author=entry.author,
            entry_source=entry.source,
            theme_ids=list(theme_ids),
            theme_names=list(theme_names),
            prompt_kind=rendered.template.kind,
            rendered_prompt=rendered.text,
            text_short=rendered.excerpt,
        )
        claims_personalization = bool(getattr(self.personalizer, "personalizes", False))
        try:
            candidate = self.personalizer.personalize(context)
        except Exception:
            # Any adapter failure — credentials, network, quota, a raised
            # PersonalizerError — degrades to the template rendering.
            return rendered.text, False, True
        verdict = validate_prompt(candidate, rendered.template.kind, rendered.excerpt)
        if not verdict.ok:
            return rendered.text, False, True
        return candidate, claims_personalization, False

    def _grade_of(self, surfacing: Surfacing | None) -> Grade | None:
        if surfacing is None:
            return None
        reflection = self.repo.get_reflection_for_surfacing(surfacing.id)
        return reflection.grade if reflection else None

    def card_for(
        self,
        surfacing: Surfacing,
        entry: Entry | None = None,
        theme_ids: Sequence[str] | None = None,
    ) -> Card:
        resolved = entry or self.get_entry(surfacing.entry_id)
        themes = (
            list(theme_ids)
            if theme_ids is not None
            else [link.theme_id for link in self.repo.entry_themes(resolved.id)]
        )
        return Card(
            surfacing=surfacing,
            entry=resolved,
            themes=themes,
            tags=self.repo.entry_tag_names(resolved.id),
            attribution_flags=self.repo.list_attribution_flags(resolved.id),
        )

    # --- FR-11 reflection ---------------------------------------------------
    def latest_surfacing(self) -> Surfacing | None:
        rows = self.repo.list_surfacings()
        return rows[-1] if rows else None

    def reflect(
        self,
        surfacing_id: str,
        grade: Grade,
        text: str | None = None,
        now: dt.datetime | None = None,
    ) -> Reflection:
        surfacing = self.repo.get_surfacing(surfacing_id)
        if surfacing is None:
            raise NotFound(f"unknown surfacing {surfacing_id}")
        if self.repo.get_reflection_for_surfacing(surfacing_id) is not None:
            raise Conflict("this surfacing already carries a reflection")
        newest = self.repo.last_surfacing_for_entry(surfacing.entry_id)
        if newest is not None and newest.id != surfacing_id:
            raise Conflict(
                "this entry has been surfaced again since; its schedule has already moved on"
            )
        reflection = Reflection(
            id=self.ids.new_id(),
            surfacing_id=surfacing_id,
            entry_id=surfacing.entry_id,
            grade=grade,
            text=text,
            logged_at=now or dt.datetime.now(dt.UTC),
        )
        self.repo.add_reflection(reflection)
        return reflection

    # --- FR-13 collections --------------------------------------------------
    def create_collection(
        self, name: str, description: str | None = None, now: dt.datetime | None = None
    ) -> Collection:
        collection = Collection(
            id=self.ids.new_id(),
            name=name,
            description=description,
            created_at=now or dt.datetime.now(dt.UTC),
        )
        self.repo.add_collection(collection)
        return collection

    def add_to_collection(self, collection_id: str, entry_id: str) -> int:
        if self.repo.get_collection(collection_id) is None:
            raise NotFound(f"unknown collection {collection_id}")
        self.get_entry(entry_id)
        return self.repo.add_collection_entry(collection_id, entry_id)

    def remove_from_collection(self, collection_id: str, entry_id: str) -> None:
        self.repo.remove_collection_entry(collection_id, entry_id)

    # --- FR-14 stats and FR-6 rebuild --------------------------------------
    def stats(self, on_date: dt.date | None = None) -> StatsReport:
        return build_stats(
            self.repo.list_entries(),
            self.repo.all_states(),
            self.repo.list_surfacings(),
            self.repo.grades_by_surfacing(),
            self.params,
            on_date or self.clock.today(),
            self.batch_k,
        )

    def rebuild_scheduler_state(self) -> int:
        """Recompute every entry's state from the event log (FR-6 / M7c)."""
        grades = self.repo.grades_by_surfacing()
        events = fold_events_by_entry(self.repo.list_surfacings(), grades)
        rebuilt = 0
        for entry in self.repo.list_entries():
            state = sched.fold_scheduler_state(entry.id, events.get(entry.id, []), self.params)
            self.repo.put_state(state)
            rebuilt += 1
        return rebuilt

    # --- FR-12 browse and search -------------------------------------------
    def list_entries(
        self,
        *,
        status: EntryStatus | None = EntryStatus.ACTIVE,
        pinned: bool | None = None,
        kind: EntryKind | None = None,
        tag: str | None = None,
        theme: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Entry]:
        """Listing filters compose; ``query`` runs FR-12 search first."""
        if theme is not None and theme not in self._themes:
            raise ValidationFailed(f"unknown theme {theme}")
        if query:
            hits = self.repo.search_entries(query, status=status, limit=10_000)
            keep = {e.id for e in hits}
            rows = [
                e
                for e in self.repo.list_entries(
                    status=status, pinned=pinned, kind=kind, tag=tag, theme=theme
                )
                if e.id in keep
            ]
            order = {entry.id: rank for rank, entry in enumerate(hits)}
            rows.sort(key=lambda e: order[e.id])
        else:
            rows = self.repo.list_entries(
                status=status, pinned=pinned, kind=kind, tag=tag, theme=theme
            )
        rows = rows[offset:]
        return rows[:limit] if limit is not None else rows

    def search(self, query: str, limit: int = 50) -> list[Entry]:
        """FR-12 full-text search over text, author, source and note."""
        return self.repo.search_entries(query, status=EntryStatus.ACTIVE, limit=limit)

    def entry_detail(self, entry_id: str) -> EntryDetail:
        """One entry with its derived state and full history (US-6's journal)."""
        entry = self.get_entry(entry_id)
        state = self.repo.get_state(entry_id) or sched.initial_state(entry_id, self.params)
        return EntryDetail(
            entry=entry,
            tags=self.repo.entry_tag_names(entry_id),
            themes=[link.theme_id for link in self.repo.entry_themes(entry_id)],
            state=state,
            surfacings=self.repo.list_surfacings(entry_id=entry_id),
            reflections=self.repo.list_reflections(entry_id=entry_id),
            attribution_flags=self.repo.list_attribution_flags(entry_id),
        )

    # --- FR-5 import / export ----------------------------------------------
    def import_candidates(
        self, rows: Sequence[ImportCandidate], now: dt.datetime | None = None
    ) -> ImportReport:
        """Apply FR-1 normalization + duplicate detection per row, then report."""
        return self._ingest(list(enumerate(rows, start=1)), [], now)

    def _ingest(
        self,
        numbered: Sequence[tuple[int, ImportCandidate]],
        errors: list[ImportIssue],
        now: dt.datetime | None = None,
    ) -> ImportReport:
        """The shared import body (FR-5).

        Duplicates are *skipped* on import (unlike interactive capture, where a
        collision is only a warning): a bulk file is not a place to confirm each
        row, and the report names how many were dropped.
        """
        timestamp = now or dt.datetime.now(dt.UTC)
        created: list[str] = []
        duplicates = 0
        errors = list(errors)
        seen_hashes = {e.normalized_hash for e in self.repo.list_entries(status=EntryStatus.ACTIVE)}
        for number, row in numbered:
            digest = normalized_hash(row.text)
            if digest in seen_hashes:
                duplicates += 1
                continue
            try:
                result = self.capture(
                    text=row.text,
                    kind=row.kind,
                    author=row.author,
                    source=row.source,
                    url=row.url,
                    note=row.note,
                    tags=row.tags,
                    themes=row.themes,
                    captured_on=row.captured_on or self.clock.today(),
                    now=timestamp,
                )
            except (ServiceError, ValueError) as exc:
                errors.append(ImportIssue(row=number, message=str(exc)))
                continue
            seen_hashes.add(digest)
            created.append(result.entry.id)
        horizon = drain_horizon_days(
            len(created),
            len(self.repo.list_entries(status=EntryStatus.ACTIVE, pinned=True)),
            self.batch_k,
            self.params,
        )
        note = None
        if horizon is not None and created:
            note = (
                f"{len(created)} new entries drain within about {horizon} days at "
                f"k={self.batch_k} (S={self.params.S} forcing horizon plus the "
                f"capacity-derived queue)"
            )
        elif created:
            note = "the pinned-rescue load consumes the whole daily budget; unpin or raise k"
        return ImportReport(
            created=len(created),
            duplicates=duplicates,
            errors=sorted(errors, key=lambda issue: issue.row),
            entry_ids=created,
            drain_horizon_days=horizon,
            drain_note=note,
        )

    def import_json(self, payload: str | bytes, now: dt.datetime | None = None) -> ImportReport:
        """Import the native export format (or a bare list of entry objects)."""
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValidationFailed(f"payload is not valid JSON: {exc}") from exc
        if isinstance(document, dict):
            raw_rows = document.get("entries", [])
        elif isinstance(document, list):
            raw_rows = document
        else:
            raise ValidationFailed("expected a JSON object or a list of entries")
        rows: list[tuple[int, ImportCandidate]] = []
        errors: list[ImportIssue] = []
        for number, raw in enumerate(raw_rows, start=1):
            try:
                rows.append((number, ImportCandidate.model_validate(_export_row_to_candidate(raw))))
            except Exception as exc:  # pydantic validation error / bad shape
                errors.append(ImportIssue(row=number, message=_first_error(exc)))
        return self._ingest(rows, errors, now=now)

    def import_csv(self, payload: str, now: dt.datetime | None = None) -> ImportReport:
        """Import FR-5's documented CSV (``text, author, source, url, tags, note``)."""
        reader = csv.DictReader(io.StringIO(payload))
        if reader.fieldnames is None or "text" not in {
            (name or "").strip().casefold() for name in reader.fieldnames
        }:
            raise ValidationFailed(
                f"CSV needs a header row with at least a 'text' column; "
                f"the documented header is: {', '.join(CSV_COLUMNS)}"
            )
        rows: list[tuple[int, ImportCandidate]] = []
        errors: list[ImportIssue] = []
        for number, raw in enumerate(reader, start=1):
            record = {
                (key or "").strip().casefold(): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            tags = [part.strip() for part in record.get("tags", "").split(";") if part.strip()]
            try:
                rows.append(
                    (
                        number,
                        ImportCandidate(
                            text=record.get("text", ""),
                            author=record.get("author") or None,
                            source=record.get("source") or None,
                            url=record.get("url") or None,
                            note=record.get("note") or None,
                            tags=tags,
                        ),
                    )
                )
            except Exception as exc:
                errors.append(ImportIssue(row=number, message=_first_error(exc)))
        return self._ingest(rows, errors, now=now)

    def import_starter(
        self, quotes: Sequence[StarterQuote], now: dt.datetime | None = None
    ) -> ImportReport:
        """FR-5 ``--starter``: the committed public-domain starter pack."""
        rows = [
            ImportCandidate(
                text=quote.text,
                kind=quote.kind,
                author=quote.author,
                source=quote.source,
                tags=list(quote.tags),
                themes=list(quote.themes),
            )
            for quote in quotes
        ]
        return self.import_candidates(rows, now=now)

    def export_library(self, now: dt.datetime | None = None) -> LibraryExport:
        """FR-5 export: entries, tags, themes, collections, surfacings, reflections."""
        entries = self.repo.list_entries()
        flags = [flag for entry in entries for flag in self.repo.list_attribution_flags(entry.id)]
        collections = self.repo.list_collections()
        return LibraryExport(
            exported_at=now or dt.datetime.now(dt.UTC),
            entries=[
                ExportedEntry(
                    entry=entry,
                    tags=self.repo.entry_tag_names(entry.id),
                    themes=self.repo.entry_themes(entry.id),
                )
                for entry in entries
            ],
            tags=self.repo.list_tags(),
            collections=collections,
            collection_entries={c.id: self.repo.collection_entry_ids(c.id) for c in collections},
            surfacings=self.repo.list_surfacings(),
            reflections=self.repo.list_reflections(),
            attribution_flags=flags,
            config=self.repo.all_config(),
        )


# --------------------------------------------------------------------------
# FR-5 parsing helpers
# --------------------------------------------------------------------------

#: Fields the native export carries that an import must not replay verbatim —
#: ids, hashes and timestamps belong to the *new* library, not the old one.
_EXPORT_ONLY_FIELDS = frozenset(
    {"id", "normalized_hash", "created_at", "updated_at", "pinned", "status"}
)


def _export_row_to_candidate(raw: object) -> dict[str, object]:
    """Reshape one row of a native export (or a bare entry object) for import."""
    if not isinstance(raw, dict):
        raise ValueError("each entry must be a JSON object")
    body = raw.get("entry", raw)
    if not isinstance(body, dict):
        raise ValueError("'entry' must be a JSON object")
    out = {key: value for key, value in body.items() if key not in _EXPORT_ONLY_FIELDS}
    for field in ("tags", "themes"):
        if field in raw:
            out[field] = raw[field]
    if isinstance(out.get("themes"), list):
        out["themes"] = [
            item.get("theme_id") if isinstance(item, dict) else item for item in out["themes"]
        ]
    return out


def _first_error(exc: Exception) -> str:
    """A one-line message from a pydantic ValidationError or any other error."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        details = errors()
        if details:
            first = details[0]
            location = ".".join(str(part) for part in first.get("loc", ())) or "row"
            return f"{location}: {first.get('msg', 'invalid')}"
    return str(exc).splitlines()[0]
