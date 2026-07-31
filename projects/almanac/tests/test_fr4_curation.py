"""FR-1/FR-4: duplicate warnings, the text-edit rule, pinning, archiving."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.models import EntryStatus
from almanac.service import ValidationFailed

START = dt.date(2026, 1, 1)


def stamp(offset: int = 0, hour: int = 8) -> dt.datetime:
    return dt.datetime.combine(START + dt.timedelta(days=offset), dt.time(hour), dt.UTC)


def test_fr1_duplicate_capture_warns_but_does_not_block(service):
    first = service.capture("The same saved line.", captured_on=START, now=stamp())
    second = service.capture("the same saved line!", captured_on=START, now=stamp())
    assert second.duplicate_of == first.entry.id
    assert second.entry.id != first.entry.id


def test_fr4_archived_entries_are_excluded_from_duplicate_warnings(service):
    first = service.capture("A line to archive.", captured_on=START, now=stamp())
    service.archive(first.entry.id, now=stamp())
    again = service.capture("A line to archive.", captured_on=START, now=stamp())
    assert again.duplicate_of is None


def test_fr1_tags_are_normalized_and_shared(service):
    a = service.capture("First", tags=["Deep Work", " deep-work "], captured_on=START, now=stamp())
    b = service.capture("Second", tags=["DEEP WORK"], captured_on=START, now=stamp())
    assert a.tags == ["deep-work"]
    assert b.tags == ["deep-work"]
    assert [t.name for t in service.repo.list_tags()] == ["deep-work"]


def test_fr4_text_edits_are_free_before_any_surfacing(service):
    entry = service.capture("An original thought.", captured_on=START, now=stamp()).entry
    updated = service.edit_entry(entry.id, text="A completely different thought.", now=stamp())
    assert updated.text == "A completely different thought."
    assert updated.normalized_hash != entry.normalized_hash


def test_fr4_hash_preserving_edit_is_accepted_after_a_surfacing(service, clock):
    entry = service.capture(
        "You have power over your mind - not outside events",
        captured_on=START,
        now=stamp(),
    ).entry
    clock.set(START)
    service.materialize_day(START, now=stamp())
    fixed = service.edit_entry(
        entry.id, text="You have power over your mind — not outside events!", now=stamp()
    )
    assert fixed.normalized_hash == entry.normalized_hash
    assert "—" in fixed.text


def test_fr4_semantic_edit_is_rejected_after_a_surfacing(service, clock):
    entry = service.capture("A line that will be surfaced.", captured_on=START, now=stamp()).entry
    clock.set(START)
    service.materialize_day(START, now=stamp())
    with pytest.raises(ValidationFailed) as excinfo:
        service.edit_entry(entry.id, text="An entirely different line.", now=stamp())
    assert "archive" in str(excinfo.value)


def test_fr4_metadata_is_always_editable(service, clock):
    entry = service.capture("Some line.", captured_on=START, now=stamp()).entry
    clock.set(START)
    service.materialize_day(START, now=stamp())
    updated = service.edit_entry(
        entry.id, author="New Author", note="new note", themes=["humility"], now=stamp()
    )
    assert updated.author == "New Author"
    assert [link.theme_id for link in service.repo.entry_themes(entry.id)] == ["humility"]


def test_fr4_theme_cap_is_enforced(service):
    entry = service.capture("Some line.", captured_on=START, now=stamp()).entry
    with pytest.raises(ValidationFailed):
        service.edit_entry(entry.id, themes=["courage", "humility", "gratitude", "relationships"])


def test_fr4_unknown_themes_are_rejected(service):
    with pytest.raises(ValidationFailed):
        service.capture("Some line.", themes=["not_a_real_theme"])


def test_fr4_pin_warns_once_past_the_budget(service):
    ids = [
        service.capture(f"Pinned line {i}", captured_on=START, now=stamp()).entry.id
        for i in range(9)
    ]
    warnings = [service.pin(entry_id, now=stamp())[1] for entry_id in ids]
    assert all(w is None for w in warnings[:8])
    assert warnings[8] is not None and "45" in warnings[8]


def test_fr4_unpin_removes_the_guarantee(service):
    entry = service.capture("Pinned line", captured_on=START, now=stamp()).entry
    service.pin(entry.id, now=stamp())
    assert service.get_entry(entry.id).pinned is True
    service.unpin(entry.id, now=stamp())
    assert service.get_entry(entry.id).pinned is False


def test_fr4_archived_entries_never_surface_and_keep_history(service, clock):
    ids = [service.capture(f"Entry {i}", captured_on=START, now=stamp()).entry.id for i in range(3)]
    clock.set(START)
    card = service.materialize_day(START, now=stamp())[0]
    service.archive(card.surfacing.entry_id, now=stamp())
    for offset in range(1, 12):
        clock.set(START + dt.timedelta(days=offset))
        service.materialize_day(START + dt.timedelta(days=offset), now=stamp(offset))
    surfaced = {s.entry_id for s in service.repo.list_surfacings()}
    assert surfaced <= set(ids)
    later = [s for s in service.repo.list_surfacings() if s.on_date > START]
    assert card.surfacing.entry_id not in {s.entry_id for s in later}
    assert service.repo.list_surfacings(entry_id=card.surfacing.entry_id)


def test_fr4_restore_brings_an_entry_back(service):
    entry = service.capture("Entry", captured_on=START, now=stamp()).entry
    service.archive(entry.id, now=stamp())
    assert service.get_entry(entry.id).status is EntryStatus.ARCHIVED
    service.restore(entry.id, now=stamp())
    assert service.get_entry(entry.id).status is EntryStatus.ACTIVE
