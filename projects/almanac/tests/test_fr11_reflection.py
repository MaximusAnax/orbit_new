"""FR-11: the reflection log and its insertion window."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.models import Grade
from almanac.service import Conflict, NotFound
from pydantic import ValidationError

START = dt.date(2026, 1, 1)


def stamp(offset: int = 0, hour: int = 8) -> dt.datetime:
    return dt.datetime.combine(START + dt.timedelta(days=offset), dt.time(hour), dt.UTC)


def surface_one(service, clock, offset: int = 0):
    clock.set(START + dt.timedelta(days=offset))
    return service.materialize_day(START + dt.timedelta(days=offset), now=stamp(offset))[0]


def test_fr11_reflection_is_appended_to_the_surfacing(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    card = surface_one(service, clock)
    reflection = service.reflect(card.surfacing.id, Grade.APPLIED, "did it", now=stamp(0, 20))
    assert reflection.entry_id == card.surfacing.entry_id
    assert service.repo.get_reflection_for_surfacing(card.surfacing.id) == reflection


def test_fr11_only_one_reflection_per_surfacing(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    card = surface_one(service, clock)
    service.reflect(card.surfacing.id, Grade.APPLIED, now=stamp(0, 20))
    with pytest.raises(Conflict):
        service.reflect(card.surfacing.id, Grade.FLAT, now=stamp(0, 21))


def test_fr11_unknown_surfacing_is_not_found(service):
    with pytest.raises(NotFound):
        service.reflect("nope", Grade.FLAT)


def test_fr11_reflecting_on_a_superseded_surfacing_is_rejected(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    first = surface_one(service, clock, 0)
    second = surface_one(service, clock, 11)
    assert first.surfacing.entry_id == second.surfacing.entry_id
    with pytest.raises(Conflict):
        service.reflect(first.surfacing.id, Grade.APPLIED, now=stamp(11, 20))
    assert service.reflect(second.surfacing.id, Grade.APPLIED, now=stamp(11, 20))


def test_fr11_grade_drives_the_next_interval(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    first = surface_one(service, clock, 0)
    service.reflect(first.surfacing.id, Grade.RESONATED, now=stamp(0, 20))
    surface_one(service, clock, 11)
    assert service.repo.get_state(first.surfacing.entry_id).interval_days == 13


def test_fr11_absent_reflection_uses_the_none_multiplier(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    first = surface_one(service, clock, 0)
    surface_one(service, clock, 11)
    assert service.repo.get_state(first.surfacing.entry_id).interval_days == 19


def test_fr11_reflections_are_browsable_per_entry_and_by_date(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    card = surface_one(service, clock)
    service.reflect(card.surfacing.id, Grade.FLAT, "meh", now=stamp(0, 20))
    entry_id = card.surfacing.entry_id
    assert len(service.repo.list_reflections(entry_id=entry_id)) == 1
    assert len(service.repo.list_reflections(date_from=START, date_to=START)) == 1
    assert service.repo.list_reflections(date_from=START + dt.timedelta(days=1)) == []


def test_fr11_reflections_are_immutable(service, clock):
    service.capture("One line", captured_on=START, now=stamp())
    card = surface_one(service, clock)
    reflection = service.reflect(card.surfacing.id, Grade.FLAT, now=stamp(0, 20))
    with pytest.raises(ValidationError):
        reflection.grade = Grade.APPLIED
