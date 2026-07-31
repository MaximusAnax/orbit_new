"""FR-17: hermetic determinism, replay, and the derived-state cache."""

from __future__ import annotations

import datetime as dt
import pathlib

from almanac.adapters.attribution_local import LocalAttributionChecker
from almanac.adapters.clock import FixedClock
from almanac.adapters.ids import SequenceIdFactory, encode_crockford
from almanac.adapters.personalizer_null import TemplatePersonalizer
from almanac.engine.jitter import hash_u64, jitter
from almanac.engine.scheduler import fold_scheduler_state
from almanac.engine.stats import fold_events_by_entry
from almanac.models import Grade
from almanac.service import AlmanacService
from almanac.store.memory_repo import MemoryRepository
from almanac.store.sqlite_repo import SqliteRepository

START = dt.date(2026, 1, 1)
GRADES = [Grade.APPLIED, Grade.RESONATED, Grade.FLAT, None]


def stamp(offset: int, hour: int = 8) -> dt.datetime:
    return dt.datetime.combine(START + dt.timedelta(days=offset), dt.time(hour), dt.UTC)


def build(repo, datasets, seed: int) -> AlmanacService:
    service = AlmanacService(
        repo=repo,
        datasets=datasets,
        clock=FixedClock(START),
        ids=SequenceIdFactory(),
        personalizer=TemplatePersonalizer(),
        attribution=LocalAttributionChecker(datasets.misattributions),
        seed=seed,
    )
    service.initialize()
    return service


def simulate(service, days: int = 90, library: int = 20) -> list[tuple]:
    for index in range(library):
        service.capture(
            f"Simulated entry {index} about courage, habit and time",
            themes=["courage"] if index % 2 else ["discipline_and_habit"],
            captured_on=START,
            now=stamp(0),
        )
    timeline: list[tuple] = []
    for offset in range(days):
        date = START + dt.timedelta(days=offset)
        service.clock.set(date)
        if offset and offset % 9 == 0:
            service.capture(
                f"Stream capture {offset}",
                themes=["gratitude"],
                captured_on=date,
                now=stamp(offset),
            )
        for card in service.materialize_day(date, now=stamp(offset)):
            timeline.append(
                (
                    card.surfacing.on_date,
                    card.surfacing.slot,
                    card.surfacing.entry_id,
                    card.surfacing.select_pool.value,
                    card.surfacing.prompt_template_id,
                    card.surfacing.prompt_text,
                    card.surfacing.relaxed_cooldown,
                    card.surfacing.prompt_recency_relaxed,
                )
            )
            grade = GRADES[offset % len(GRADES)]
            if grade is not None:
                service.reflect(card.surfacing.id, grade, now=stamp(offset, 20))
    return timeline


def test_fr17_same_seed_replays_byte_identically(datasets):
    first = simulate(build(MemoryRepository(), datasets, 7))
    second = simulate(build(MemoryRepository(), datasets, 7))
    assert first == second
    assert len(first) > 60


def test_fr17_a_different_seed_changes_at_least_one_selection(datasets):
    seven = simulate(build(MemoryRepository(), datasets, 7))
    eight = simulate(build(MemoryRepository(), datasets, 8))
    assert seven != eight


def test_fr17_sqlite_and_memory_backends_agree(datasets, tmp_path: pathlib.Path):
    memory = simulate(build(MemoryRepository(), datasets, 7))
    sqlite_repo = SqliteRepository(tmp_path / "replay.db")
    disk = simulate(build(sqlite_repo, datasets, 7))
    sqlite_repo.close()
    assert memory == disk


def test_fr17_state_cache_equals_the_fold_of_the_event_log(datasets):
    service = build(MemoryRepository(), datasets, 7)
    simulate(service, days=120, library=25)
    events = fold_events_by_entry(
        service.repo.list_surfacings(), service.repo.grades_by_surfacing()
    )
    for entry in service.repo.list_entries():
        cached = service.repo.get_state(entry.id)
        folded = fold_scheduler_state(entry.id, events.get(entry.id, []), service.params)
        if cached is None:
            assert folded.exposure_count == 0
        else:
            assert cached == folded


def test_fr17_rebuild_is_a_no_op_on_a_healthy_cache(datasets):
    service = build(MemoryRepository(), datasets, 7)
    simulate(service, days=60, library=15)
    before = service.repo.all_states()
    service.rebuild_scheduler_state()
    after = {k: v for k, v in service.repo.all_states().items() if v.exposure_count > 0}
    assert before == after


def test_fr17_jitter_is_a_pure_function_of_its_key():
    assert hash_u64(7, START, 0, "e1") == hash_u64(7, START, 0, "e1")
    assert hash_u64(7, START, 0, "e1") != hash_u64(7, START, 1, "e1")
    assert hash_u64(7, START, 0, "e1") != hash_u64(8, START, 0, "e1")


def test_fr17_jitter_stays_inside_its_amplitude():
    values = [jitter(0.05, 7, START, 0, f"e{i}") for i in range(500)]
    assert all(0.0 <= v < 0.05 for v in values)
    assert max(values) > 0.04 and min(values) < 0.01


def test_fr17_engine_modules_perform_no_io():
    """The purity boundary is testable by inspection (SCOPE.md D11)."""
    engine_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "almanac" / "engine"
    banned = ("import sqlite3", "import random", "import httpx", "from pathlib", "date.today")
    for module in engine_dir.glob("*.py"):
        source = module.read_text()
        for needle in banned:
            assert needle not in source, f"{module.name} must not use {needle}"


def test_fr17_id_factory_emits_sortable_ulid_shaped_ids():
    factory = SequenceIdFactory()
    ids = [factory.new_id() for _ in range(300)]
    assert ids == sorted(ids)
    assert all(len(i) == 26 for i in ids)
    assert encode_crockford(0, 4) == "0000"
