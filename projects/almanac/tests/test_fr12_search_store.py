"""FR-12 search plus Repository parity between the two backends."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.adapters.attribution_local import LocalAttributionChecker
from almanac.adapters.clock import FixedClock
from almanac.adapters.ids import SequenceIdFactory
from almanac.adapters.personalizer_null import TemplatePersonalizer
from almanac.models import EntryStatus, Grade
from almanac.service import AlmanacService
from almanac.store.memory_repo import MemoryRepository
from almanac.store.repository import DuplicateError, token_scan
from almanac.store.sqlite_repo import SqliteRepository

START = dt.date(2026, 1, 1)
CORPUS = [
    ("Attention is the rarest form of generosity", "Simone Weil", "Letters", "on attention"),
    ("The unexamined life is not worth living", "Socrates", "Apology", "examine daily"),
    ("Attention without feeling is only a report", "Mary Oliver", "Upstream", None),
    ("Habits are the compound interest of self improvement", None, None, "attention to habit"),
]


def stamp(offset: int = 0) -> dt.datetime:
    return dt.datetime.combine(START + dt.timedelta(days=offset), dt.time(8), dt.UTC)


def build_service(repo, datasets):
    svc = AlmanacService(
        repo=repo,
        datasets=datasets,
        clock=FixedClock(START),
        ids=SequenceIdFactory(),
        personalizer=TemplatePersonalizer(),
        attribution=LocalAttributionChecker(datasets.misattributions),
        seed=7,
    )
    svc.initialize()
    for text, author, source, note in CORPUS:
        svc.capture(text, author=author, source=source, note=note, captured_on=START, now=stamp())
    return svc


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, datasets, tmp_path):
    repo = (
        MemoryRepository()
        if request.param == "memory"
        else SqliteRepository(tmp_path / "almanac.db")
    )
    service = build_service(repo, datasets)
    yield service
    repo.close()


def test_fr12_search_returns_entries_containing_every_token(backend):
    hits = backend.repo.search_entries("attention")
    assert len(hits) == 3
    both = backend.repo.search_entries("attention generosity")
    assert [e.text for e in both] == ["Attention is the rarest form of generosity"]


def test_fr12_search_is_stemmed(backend):
    assert backend.repo.search_entries("habits")
    assert backend.repo.search_entries("habit")


def test_fr12_search_covers_author_source_and_note(backend):
    assert backend.repo.search_entries("socrates")
    assert backend.repo.search_entries("upstream")
    assert backend.repo.search_entries("examine")


def test_fr12_search_excludes_archived_entries_by_default(backend):
    target = backend.repo.search_entries("generosity")[0]
    backend.archive(target.id, now=stamp())
    assert backend.repo.search_entries("generosity") == []
    assert backend.repo.search_entries("generosity", status=None)


def test_fr12_search_is_deterministic(backend):
    first = [e.id for e in backend.repo.search_entries("attention")]
    second = [e.id for e in backend.repo.search_entries("attention")]
    assert first == second


def test_fr12_empty_query_returns_nothing(backend):
    assert backend.repo.search_entries("   ") == []


def test_fr12_fallback_scan_requires_all_tokens_and_orders_by_id(datasets):
    repo = MemoryRepository()
    build_service(repo, datasets)
    entries = repo.list_entries(status=EntryStatus.ACTIVE)
    hits = token_scan(entries, "attention")
    assert [e.id for e in hits] == sorted(e.id for e in hits)
    assert token_scan(entries, "attention unrelatedword") == []


def test_fr12_fallback_and_fts_return_the_same_set(datasets, tmp_path):
    sqlite_repo = SqliteRepository(tmp_path / "x.db")
    build_service(sqlite_repo, datasets)
    fts = {e.text for e in sqlite_repo.search_entries("attention")}
    fallback = {
        e.text for e in token_scan(sqlite_repo.list_entries(status=EntryStatus.ACTIVE), "attention")
    }
    assert fts == fallback
    sqlite_repo.close()


# --- repository behaviour shared by both backends -------------------------


def test_store_filters_compose(backend):
    entry = backend.repo.list_entries()[0]
    backend.pin(entry.id, now=stamp())
    backend.edit_entry(entry.id, tags=["focus"], themes=["attention_and_presence"], now=stamp())
    assert [e.id for e in backend.repo.list_entries(pinned=True)] == [entry.id]
    assert [e.id for e in backend.repo.list_entries(tag="focus")] == [entry.id]
    assert [e.id for e in backend.repo.list_entries(theme="attention_and_presence")] == [entry.id]
    assert backend.repo.list_entries(tag="focus", pinned=False) == []


def test_store_rejects_a_duplicate_surfacing_for_one_entry_per_day(backend):
    backend.clock.set(START)
    card = backend.materialize_day(START, now=stamp())[0]
    clone = card.surfacing.model_copy(update={"id": "another-id", "slot": 1})
    with pytest.raises(DuplicateError):
        backend.repo.add_surfacing(clone)


def test_store_round_trips_every_entity(backend):
    backend.clock.set(START)
    card = backend.materialize_day(START, now=stamp())[0]
    backend.reflect(card.surfacing.id, Grade.APPLIED, "note", now=stamp())
    collection = backend.create_collection("c1", "desc", now=stamp())
    backend.add_to_collection(collection.id, card.surfacing.entry_id)
    assert backend.repo.get_surfacing(card.surfacing.id) == card.surfacing
    assert backend.repo.grades_by_surfacing()[card.surfacing.id] is Grade.APPLIED
    assert backend.repo.collection_entry_ids(collection.id) == [card.surfacing.entry_id]
    assert backend.repo.get_collection(collection.id).description == "desc"
    assert backend.repo.list_attribution_flags(card.surfacing.entry_id) == []


def test_store_collection_positions_stay_contiguous(backend):
    ids = [e.id for e in backend.repo.list_entries()]
    collection = backend.create_collection("c2", now=stamp())
    for entry_id in ids:
        backend.add_to_collection(collection.id, entry_id)
    backend.remove_from_collection(collection.id, ids[1])
    assert backend.repo.collection_entry_ids(collection.id) == [ids[0], ids[2], ids[3]]


def test_store_config_round_trips(backend):
    backend.repo.set_config("seed", "42")
    assert backend.repo.get_config("seed") == "42"
    assert backend.repo.all_config()["schema_version"] == "1"


def test_store_datasets_are_readable_after_load(backend, datasets):
    assert len(backend.repo.list_themes()) == len(datasets.themes)
    assert len(backend.repo.list_templates()) == len(datasets.templates)
    assert len(backend.repo.list_misattributions()) == len(datasets.misattributions)
