"""Day-by-day simulation driver (EVALS.md section 6).

`simulate()` drives the *real* service — memory repository, `FixedClock`,
`TemplatePersonalizer`, `LocalAttributionChecker` — across a scenario's calendar,
applying the capture stream, the persona's reflections and curation actions and
the draw stream, and returns the resulting timeline.

The timeline is deliberately *raw*: entries, surfacings, reflections, curation
events and final states.  Every predicate in `metrics.py` is re-derived from it
rather than reported by the engine, so no metric's truth is produced by the code
path it gates (EVALS.md section 2).

Also hosts the two naive baseline policies of EVALS.md section 5.  They swap only
the *choice* of entry: eligibility, cooldown bookkeeping, `select_pool` stamping,
prompt selection and the personas are shared with the real scheduler.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from almanac.adapters.attribution_local import LocalAttributionChecker
from almanac.adapters.clock import FixedClock
from almanac.adapters.ids import SequenceIdFactory
from almanac.adapters.personalizer_null import TemplatePersonalizer
from almanac.datasets import default_datasets
from almanac.engine import scheduler as sched
from almanac.engine.jitter import hash_u64
from almanac.models import (
    EntryKind,
    EntryStatus,
    Grade,
    SchedulerParams,
    SchedulerState,
    SelectPool,
    Surfacing,
)
from almanac.service import AlmanacService, Conflict
from almanac.store.memory_repo import MemoryRepository
from almanac.store.sqlite_repo import SqliteRepository

from evals import personas

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_scenarios() -> dict[str, dict]:
    document = json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))
    return {row["id"]: {**row, "start": document["start"]} for row in document["scenarios"]}


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryRecord:
    """What the metrics know about an entry: identity, age, planted quality."""

    entry_id: str
    ref: str
    captured_on: dt.date
    quality: str
    themes: tuple[str, ...]
    initially_pinned: bool


@dataclass(frozen=True)
class CurationEvent:
    on_date: dt.date
    entry_id: str
    action: str  # pin | unpin | archive


@dataclass
class Timeline:
    scenario: str
    policy: str
    seed: int
    start: dt.date
    days: int
    batch_k: int
    params: SchedulerParams
    calendar: list[dt.date]
    entries: list[EntryRecord]
    surfacings: list[Surfacing]
    reflections: dict[str, str]  # surfacing id -> grade
    curation: list[CurationEvent]
    final_states: dict[str, SchedulerState]
    collections: dict[str, list[str]] = field(default_factory=dict)
    idempotence_violations: int = 0
    final_archive_candidates: list[str] = field(default_factory=list)
    final_flat_streaks: dict[str, int] = field(default_factory=dict)
    stats_lambda: float | None = None

    @property
    def end(self) -> dt.date:
        return self.start + dt.timedelta(days=self.days - 1)

    def by_id(self) -> dict[str, EntryRecord]:
        return {row.entry_id: row for row in self.entries}

    def fingerprint(self) -> list[tuple]:
        """The comparable shape used by M7a/M7b/M7d."""
        return [
            (
                s.on_date.isoformat(),
                s.slot,
                s.kind.value,
                s.entry_id,
                s.select_pool.value,
                s.prompt_template_id,
                s.prompt_text,
                s.relaxed_cooldown,
                s.prompt_recency_relaxed,
            )
            for s in sorted(self.surfacings, key=lambda s: (s.on_date, s.kind.value, s.slot))
        ]


# --------------------------------------------------------------------------
# Baseline selection policies (EVALS.md section 5)
# --------------------------------------------------------------------------


def _classify(
    chosen: sched.SchedulerEntry,
    never: Sequence[sched.SchedulerEntry],
    due: Sequence[sched.SchedulerEntry],
    eligible: Sequence[sched.SchedulerEntry],
) -> SelectPool:
    """Stamp the pool a pick came from, using the same taxonomy as FR-7.

    Baselines must be measurable by the same predicates as the real scheduler,
    so a baseline pick is labelled by what the pools looked like, not by which
    branch produced it (baselines have only one branch).
    """
    if chosen not in eligible:
        return SelectPool.RELAXED
    if not chosen.seen:
        return SelectPool.NOVELTY if due else SelectPool.NOVELTY_ONLY
    if chosen in due:
        return SelectPool.REVIEW if never else SelectPool.REVIEW_ONLY
    return SelectPool.NOT_DUE


def _pools(
    entries: Sequence[sched.SchedulerEntry],
    on_date: dt.date,
    params: SchedulerParams,
    picked: set[str],
) -> tuple[list, list, list, list]:
    active = [
        e
        for e in entries
        if e.status is EntryStatus.ACTIVE
        and e.entry_id not in picked
        and e.last_surfaced_on != on_date
    ]
    eligible = [
        e
        for e in active
        if e.last_surfaced_on is None or (on_date - e.last_surfaced_on).days >= params.W
    ]
    never = [e for e in eligible if not e.seen]
    due = [e for e in eligible if sched.is_due(e, on_date, params)]
    return active, eligible, never, due


def random_eligible(
    entries: Sequence[sched.SchedulerEntry],
    contested: Sequence[SelectPool],
    on_date: dt.date,
    seed: int,
    params: SchedulerParams,
    k: int,
) -> list[sched.SlotDecision]:
    """Uniform seeded choice among eligible entries — "just show me something"."""
    out: list[sched.SlotDecision] = []
    picked: set[str] = set()
    for slot in range(k):
        active, eligible, never, due = _pools(entries, on_date, params, picked)
        pool = eligible or active
        if not pool:
            break
        ordered = sorted(pool, key=lambda e: e.entry_id)
        index = hash_u64(seed, on_date, slot, "random-eligible") % len(ordered)
        chosen = ordered[index]
        out.append(
            sched.SlotDecision(
                slot=slot,
                entry_id=chosen.entry_id,
                select_pool=_classify(chosen, never, due, eligible),
            )
        )
        picked.add(chosen.entry_id)
    return out


def fifo_rotation(
    entries: Sequence[sched.SchedulerEntry],
    contested: Sequence[SelectPool],
    on_date: dt.date,
    seed: int,
    params: SchedulerParams,
    k: int,
) -> list[sched.SlotDecision]:
    """Cycle entries in capture order, skipping those inside the cooldown."""
    out: list[sched.SlotDecision] = []
    picked: set[str] = set()
    for slot in range(k):
        active, eligible, never, due = _pools(entries, on_date, params, picked)
        pool = eligible or active
        if not pool:
            break
        order = sorted(active, key=lambda e: (e.captured_on, e.entry_id))
        seen_dates = [e.last_surfaced_on for e in order if e.last_surfaced_on is not None]
        cursor = 0
        if seen_dates:
            newest = max(seen_dates)
            positions = [i for i, e in enumerate(order) if e.last_surfaced_on == newest]
            cursor = (positions[-1] + 1) % len(order)
        allowed = {e.entry_id for e in pool}
        chosen = next(
            (
                order[(cursor + step) % len(order)]
                for step in range(len(order))
                if order[(cursor + step) % len(order)].entry_id in allowed
            ),
            pool[0],
        )
        out.append(
            sched.SlotDecision(
                slot=slot,
                entry_id=chosen.entry_id,
                select_pool=_classify(chosen, never, due, eligible),
            )
        )
        picked.add(chosen.entry_id)
    return out


POLICIES = {
    "almanac": None,
    "random_eligible": random_eligible,
    "fifo_rotation": fifo_rotation,
}


class PolicyService(AlmanacService):
    """The real service with only the FR-7 choice replaced (EVALS.md section 5)."""

    policy = None

    def select_slots(self, entries, contested, on_date):  # type: ignore[override]
        if self.policy is None:
            return super().select_slots(entries, contested, on_date)
        return self.policy(entries, contested, on_date, self.seed, self.params, self.batch_k)


# --------------------------------------------------------------------------
# The driver
# --------------------------------------------------------------------------


def _build_service(seed: int, start: dt.date, policy_name: str, repo=None) -> PolicyService:
    datasets = default_datasets()
    service = PolicyService(
        repo=repo if repo is not None else MemoryRepository(),
        datasets=datasets,
        clock=FixedClock(start),
        ids=SequenceIdFactory(prefix="E"),
        personalizer=TemplatePersonalizer(),
        attribution=LocalAttributionChecker(datasets.misattributions),
        seed=seed,
    )
    service.policy = POLICIES[policy_name]  # type: ignore[assignment]
    service.initialize()
    return service


def _copy_repository(source, target) -> None:
    """Move a whole library between backends (M7d's SQLite round-trip)."""
    datasets = default_datasets()
    target.load_datasets(datasets.themes, datasets.templates, datasets.misattributions)
    for key, value in source.all_config().items():
        target.set_config(key, value)
    tags = {tag.id: tag for tag in source.list_tags()}
    for tag in tags.values():
        target.upsert_tag(tag)
    by_name = {tag.name: tag.id for tag in target.list_tags()}
    for entry in source.list_entries():
        target.add_entry(entry)
        names = source.entry_tag_names(entry.id)
        target.set_entry_tags(entry.id, [by_name[name] for name in names if name in by_name])
        target.set_entry_themes(entry.id, source.entry_themes(entry.id))
        for flag in source.list_attribution_flags(entry.id):
            target.upsert_attribution_flag(flag)
    for collection in source.list_collections():
        target.add_collection(collection)
        for entry_id in source.collection_entry_ids(collection.id):
            target.add_collection_entry(collection.id, entry_id)
    for surfacing in source.list_surfacings():
        target.add_surfacing(surfacing)
    for reflection in source.list_reflections():
        target.add_reflection(reflection)
    for state in source.all_states().values():
        target.put_state(state)


def simulate(
    scenario: dict,
    seed: int,
    policy: str = "almanac",
    roundtrip_path: Path | None = None,
    roundtrip_day: int | None = None,
) -> Timeline:
    """Run one scenario end to end and return its timeline."""
    start = dt.date.fromisoformat(scenario["start"])
    scenario_id = scenario["id"]
    service = _build_service(seed, start, policy)
    params = service.params
    calendar = {start + dt.timedelta(days=offset) for offset in scenario["calendar"]}

    refs: dict[str, str] = {}
    quality: dict[str, str] = {}
    records: list[EntryRecord] = []
    curation: list[CurationEvent] = []
    collections: dict[str, list[str]] = {}
    pending_archive: dict[str, int] = {}
    flagged: set[str] = set()
    idempotence_violations = 0
    materialized_index = 0

    by_capture: dict[int, list[dict]] = {}
    for row in scenario["entries"]:
        by_capture.setdefault(max(row["captured_day"], 0), []).append(row)
    curation_by_day: dict[int, list[dict]] = {}
    for event in scenario["curation"]:
        curation_by_day.setdefault(event["day"], []).append(event)

    def capture(row: dict, on: dt.date) -> None:
        captured_on = start + dt.timedelta(days=row["captured_day"])
        result = service.capture(
            text=row["text"],
            kind=EntryKind.IDEA,
            themes=row["themes"],
            captured_on=captured_on,
            now=dt.datetime.combine(on, dt.time(7), dt.UTC),
        )
        refs[row["ref"]] = result.entry.id
        quality[result.entry.id] = row["quality"]
        if row["pinned"]:
            service.pin(result.entry.id, now=dt.datetime.combine(on, dt.time(7), dt.UTC))
            curation.append(CurationEvent(on, result.entry.id, "pin"))
        records.append(
            EntryRecord(
                entry_id=result.entry.id,
                ref=row["ref"],
                captured_on=captured_on,
                quality=row["quality"],
                themes=tuple(row["themes"]),
                initially_pinned=bool(row["pinned"]),
            )
        )

    for day in range(scenario["days"]):
        on = start + dt.timedelta(days=day)
        service.clock.set(on)
        stamp = dt.datetime.combine(on, dt.time(8), dt.UTC)

        for row in by_capture.get(day, []):
            capture(row, on)
        for spec in scenario["collections"]:
            if spec["create_day"] == day:
                created = service.create_collection(spec["name"], None, now=stamp)
                for ref in spec["entry_refs"]:
                    service.add_to_collection(created.id, refs[ref])
                collections[created.id] = [refs[ref] for ref in spec["entry_refs"]]

        for event in curation_by_day.get(day, []):
            entry_id = refs[event["ref"]]
            if event["action"] == "pin":
                service.pin(entry_id, now=stamp)
            else:
                service.unpin(entry_id, now=stamp)
            curation.append(CurationEvent(on, entry_id, event["action"]))

        if on not in calendar:
            continue

        for entry_id, due_index in list(pending_archive.items()):
            if materialized_index >= due_index:
                service.archive(entry_id, now=stamp)
                curation.append(CurationEvent(on, entry_id, "archive"))
                pending_archive.pop(entry_id)

        if roundtrip_day is not None and day == roundtrip_day and roundtrip_path is not None:
            fresh = SqliteRepository(roundtrip_path)
            _copy_repository(service.repo, fresh)
            service.repo = fresh

        cards = service.materialize_day(on, now=stamp)
        replay = service.materialize_day(on, now=stamp)
        if [c.surfacing.model_dump() for c in replay] != [c.surfacing.model_dump() for c in cards]:
            idempotence_violations += 1

        for card in cards:
            surfacing_id = card.surfacing.id
            if personas.should_reflect(scenario_id, surfacing_id, scenario["p_reflect"]):
                # A superseded surfacing cannot be reflected on (FR-11); with k=1
                # that cannot happen, but the persona must not depend on it.
                with contextlib.suppress(Conflict):
                    service.reflect(
                        surfacing_id,
                        Grade(
                            personas.grade_for(scenario_id, surfacing_id, quality[card.entry.id])
                        ),
                        personas.reflection_note(scenario_id, surfacing_id),
                        now=dt.datetime.combine(on, dt.time(21), dt.UTC),
                    )

        report = service.stats(on)
        for candidate in report.archive_candidates:
            if candidate.entry_id in flagged:
                continue
            flagged.add(candidate.entry_id)
            if personas.should_archive(
                scenario_id, candidate.entry_id, on, scenario["archive_probability"]
            ):
                pending_archive[candidate.entry_id] = (
                    materialized_index + personas.ARCHIVE_DELAY_DAYS
                )

        if personas.should_draw(scenario_id, on, scenario["draw_probability"]):
            theme_id, collection_id = personas.draw_filter(
                scenario_id,
                materialized_index,
                sorted({t for record in records for t in record.themes}),
                sorted(collections),
            )
            service.draw(
                on_date=on,
                theme_id=theme_id,
                collection_id=collection_id,
                now=dt.datetime.combine(on, dt.time(12), dt.UTC),
            )

        materialized_index += 1

    final_report = service.stats(service.clock.today())
    return Timeline(
        scenario=scenario_id,
        policy=policy,
        seed=seed,
        start=start,
        days=scenario["days"],
        batch_k=service.batch_k,
        params=params,
        calendar=sorted(calendar),
        entries=records,
        surfacings=service.repo.list_surfacings(),
        reflections={sid: grade.value for sid, grade in service.repo.grades_by_surfacing().items()},
        curation=curation,
        final_states=service.repo.all_states(),
        collections=collections,
        idempotence_violations=idempotence_violations,
        final_archive_candidates=[c.entry_id for c in final_report.archive_candidates],
        final_flat_streaks={
            entry_id: state.flat_streak for entry_id, state in service.repo.all_states().items()
        },
        stats_lambda=final_report.capacity.stretch_lambda,
    )
