"""FR-13: collections — ordering, membership, and draw scoping."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.models import SelectPool, SurfacingKind
from almanac.service import NotFound
from almanac.store.repository import DuplicateError

START = dt.date(2026, 1, 1)


@pytest.fixture
def library(service, at_time):
    """Six entries, two of them in a collection, all captured on day 0."""
    ids = []
    for index in range(6):
        result = service.capture(
            f"Collection candidate number {index}.",
            themes=["courage"] if index % 2 else ["humility"],
            captured_on=START,
            now=at_time(0),
        )
        ids.append(result.entry.id)
    collection = service.create_collection("Mornings", "before the inbox", now=at_time(0))
    service.add_to_collection(collection.id, ids[4])
    service.add_to_collection(collection.id, ids[2])
    return ids, collection


def test_fr13_membership_is_ordered_and_idempotent(service, library):
    ids, collection = library
    assert service.repo.collection_entry_ids(collection.id) == [ids[4], ids[2]]
    assert service.add_to_collection(collection.id, ids[4]) == 0
    assert service.repo.collection_entry_ids(collection.id) == [ids[4], ids[2]]


def test_fr13_removal_repacks_positions(service, library):
    ids, collection = library
    service.add_to_collection(collection.id, ids[0])
    service.remove_from_collection(collection.id, ids[2])
    remaining = service.repo.collection_entry_ids(collection.id)
    assert remaining == [ids[4], ids[0]]


def test_fr13_duplicate_collection_names_are_rejected(service, at_time):
    service.create_collection("Mornings", now=at_time(0))
    with pytest.raises(DuplicateError):
        service.create_collection("Mornings", now=at_time(0))


def test_fr13_unknown_collection_or_entry_is_not_found(service, library):
    ids, collection = library
    with pytest.raises(NotFound):
        service.add_to_collection("nope", ids[0])
    with pytest.raises(NotFound):
        service.add_to_collection(collection.id, "nope")


def test_fr13_draw_is_scoped_to_the_collection(service, library, at_time):
    _, collection = library
    members = set(service.repo.collection_entry_ids(collection.id))
    card = service.draw(on_date=START, collection_id=collection.id, now=at_time(0))
    assert card is not None
    assert card.entry.id in members
    assert card.surfacing.kind is SurfacingKind.EXTRA
    assert card.surfacing.select_pool is SelectPool.EXTRA
    assert card.surfacing.filter_collection_id == collection.id


def test_fr13_archived_members_are_excluded_from_draws(service, library, at_time):
    _, collection = library
    for entry_id in service.repo.collection_entry_ids(collection.id):
        service.archive(entry_id, now=at_time(0))
    assert service.draw(on_date=START, collection_id=collection.id, now=at_time(0)) is None
    # The membership rows survive archiving (DATA_MODEL.md section Collection).
    assert len(service.repo.collection_entry_ids(collection.id)) == 2


def test_fr13_draw_never_consumes_a_daily_slot(service, library, at_time):
    _, collection = library
    daily = service.materialize_day(START, now=at_time(0))
    drawn = service.draw(on_date=START, collection_id=collection.id, now=at_time(0))
    assert drawn is not None
    assert drawn.entry.id != daily[0].entry.id
    rows = service.repo.list_surfacings(on_date=START)
    assert [r.kind.value for r in rows] == ["daily", "extra"]
    assert service.repo.contested_pools(28) == [
        p for p in service.repo.contested_pools(28) if p is not SelectPool.EXTRA
    ]


def test_fr13_theme_filtered_draw_only_serves_matching_entries(service, library, at_time):
    card = service.draw(on_date=START, theme_id="courage", now=at_time(0))
    assert card is not None
    assert "courage" in [link.theme_id for link in service.repo.entry_themes(card.entry.id)]


def test_fr13_deleting_a_collection_keeps_its_entries(service, library, at_time):
    _, collection = library
    service.repo.delete_collection(collection.id)
    assert service.repo.get_collection(collection.id) is None
    assert service.repo.collection_entry_ids(collection.id) == []
    assert len(service.list_entries()) == 6
