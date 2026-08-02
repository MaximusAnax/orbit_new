"""FR-8: materialization, idempotence, the arrow of time, and extra draws."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.models import SelectPool, SurfacingKind
from almanac.service import NotFound, ValidationFailed
from almanac.store.repository import MonotonicityError

START = dt.date(2026, 1, 1)


def day(offset: int) -> dt.date:
    return START + dt.timedelta(days=offset)


def stamp(offset: int, hour: int = 8) -> dt.datetime:
    return dt.datetime.combine(day(offset), dt.time(hour), dt.UTC)


def seed_library(service, count: int = 8, themes=("courage",)) -> list[str]:
    return [
        service.capture(
            f"Library entry number {index} about courage and fear",
            themes=list(themes),
            captured_on=START,
            now=stamp(0),
        ).entry.id
        for index in range(count)
    ]


def test_fr8_materialize_produces_one_card_per_slot(service, clock):
    seed_library(service)
    clock.set(day(0))
    cards = service.materialize_day(day(0), now=stamp(0))
    assert len(cards) == service.batch_k == 1
    assert cards[0].surfacing.kind is SurfacingKind.DAILY
    assert cards[0].surfacing.slot == 0


def test_fr8_materialization_is_idempotent_and_byte_identical(service, clock):
    seed_library(service)
    clock.set(day(0))
    first = service.materialize_day(day(0), now=stamp(0))
    second = service.materialize_day(day(0), now=stamp(0, hour=20))
    assert [c.surfacing.model_dump() for c in first] == [c.surfacing.model_dump() for c in second]


def test_fr8_get_day_is_read_only(service, clock):
    seed_library(service)
    clock.set(day(0))
    assert service.get_day(day(0)) == []
    service.materialize_day(day(0), now=stamp(0))
    assert len(service.get_day(day(0))) == 1


def test_fr8_future_dates_are_rejected(service, clock):
    seed_library(service)
    clock.set(day(0))
    with pytest.raises(ValidationFailed):
        service.materialize_day(day(1), now=stamp(1))


def test_fr8_missed_days_are_never_backfilled(service, clock):
    seed_library(service)
    clock.set(day(5))
    service.materialize_day(day(5), now=stamp(5))
    clock.set(day(6))
    with pytest.raises(MonotonicityError):
        service.materialize_day(day(3), now=stamp(3))


def test_fr8_a_second_daily_set_for_the_same_date_returns_the_first(service, clock):
    seed_library(service)
    clock.set(day(0))
    first = service.materialize_day(day(0), now=stamp(0))
    again = service.materialize_day(day(0), now=stamp(0))
    assert [c.surfacing.id for c in first] == [c.surfacing.id for c in again]


def test_fr8_a_backdated_draw_is_rejected(service, clock):
    seed_library(service)
    clock.set(day(5))
    service.materialize_day(day(5), now=stamp(5))
    clock.set(day(6))
    with pytest.raises(MonotonicityError):
        service.draw(day(4), now=stamp(4))


def test_fr8_draw_is_stamped_extra_and_occupies_no_daily_slot(service, clock):
    seed_library(service)
    clock.set(day(0))
    daily = service.materialize_day(day(0), now=stamp(0))
    card = service.draw(day(0), now=stamp(0, hour=12))
    assert card.surfacing.kind is SurfacingKind.EXTRA
    assert card.surfacing.select_pool is SelectPool.EXTRA
    assert card.surfacing.slot == 0
    assert card.surfacing.entry_id != daily[0].surfacing.entry_id
    assert len(service.get_day(day(0))) == 1


def test_fr8_draws_are_excluded_from_the_contested_window(service, clock):
    seed_library(service)
    clock.set(day(0))
    service.draw(day(0), now=stamp(0))
    assert service.repo.contested_pools(28) == []


def test_fr8_a_draw_counts_as_an_exposure_in_the_fold(service, clock):
    ids = seed_library(service)
    clock.set(day(0))
    card = service.draw(day(0), now=stamp(0))
    state = service.repo.get_state(card.surfacing.entry_id)
    assert state.exposure_count == 1
    assert state.last_surfaced_on == day(0)
    assert all(
        service.repo.get_state(other) is None for other in ids if other != card.surfacing.entry_id
    )


def test_fr8_draw_honours_a_theme_filter(service, clock):
    seed_library(service, count=4, themes=("courage",))
    target = service.capture(
        "A quiet line about gratitude", themes=["gratitude"], captured_on=START, now=stamp(0)
    ).entry.id
    clock.set(day(0))
    card = service.draw(day(0), theme_id="gratitude", now=stamp(0))
    assert card.surfacing.entry_id == target
    assert card.surfacing.filter_theme_id == "gratitude"


def test_fr8_draw_honours_a_collection_filter(service, clock):
    ids = seed_library(service, count=5)
    collection = service.create_collection("Favourites", now=stamp(0))
    service.add_to_collection(collection.id, ids[3])
    clock.set(day(0))
    card = service.draw(day(0), collection_id=collection.id, now=stamp(0))
    assert card.surfacing.entry_id == ids[3]
    assert card.surfacing.filter_collection_id == collection.id


def test_fr8_draw_with_an_unknown_collection_is_an_error(service, clock):
    seed_library(service)
    clock.set(day(0))
    with pytest.raises(NotFound):
        service.draw(day(0), collection_id="nope", now=stamp(0))


def test_fr8_draw_returns_nothing_when_the_filter_is_empty(service, clock):
    seed_library(service)
    clock.set(day(0))
    assert service.draw(day(0), theme_id="humility", now=stamp(0)) is None


def test_fr8_empty_library_materializes_no_cards(service, clock):
    clock.set(day(0))
    assert service.materialize_day(day(0), now=stamp(0)) == []
