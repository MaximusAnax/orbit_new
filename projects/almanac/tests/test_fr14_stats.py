"""FR-14: the stats report, including the capacity block."""

from __future__ import annotations

import datetime as dt

from almanac.models import Grade

START = dt.date(2026, 1, 1)


def stamp(offset: int = 0, hour: int = 8) -> dt.datetime:
    return dt.datetime.combine(START + dt.timedelta(days=offset), dt.time(hour), dt.UTC)


def day(offset: int) -> dt.date:
    return START + dt.timedelta(days=offset)


def run(service, clock, days: int, grade: Grade | None = None, reflect_every: int = 1):
    for offset in range(days):
        clock.set(day(offset))
        for card in service.materialize_day(day(offset), now=stamp(offset)):
            if grade is not None and offset % reflect_every == 0:
                service.reflect(card.surfacing.id, grade, now=stamp(offset, 20))


def seed(service, count: int = 10):
    return [
        service.capture(
            f"Entry {index} about courage", themes=["courage"], captured_on=START, now=stamp()
        ).entry.id
        for index in range(count)
    ]


def test_fr14_counts_and_coverage(service, clock):
    seed(service, 6)
    service.capture("An idea", kind="idea", captured_on=START, now=stamp())
    run(service, clock, 3)
    report = service.stats(day(2))
    assert report.total_entries == 7
    assert report.active_entries == 7
    assert report.by_kind == {"quote": 6, "idea": 1}
    assert report.coverage == 3 / 7
    assert sum(bucket.entries for bucket in report.exposure_histogram) == 7


def test_fr14_archived_entries_are_counted_separately(service, clock):
    ids = seed(service, 4)
    service.archive(ids[0], now=stamp())
    report = service.stats(day(0))
    assert (report.active_entries, report.archived_entries) == (3, 1)


def test_fr14_open_streak_counts_consecutive_materialized_days(service, clock):
    seed(service, 8)
    run(service, clock, 5)
    assert service.stats(day(4)).open_streak == 5
    # a gap breaks the streak
    assert service.stats(day(6)).open_streak == 0


def test_fr14_reflect_streak_requires_a_reflection(service, clock):
    seed(service, 8)
    run(service, clock, 4, grade=Grade.APPLIED)
    report = service.stats(day(3))
    assert report.open_streak == 4 and report.reflect_streak == 4


def test_fr14_reflect_streak_stops_at_an_unreflected_day(service, clock):
    seed(service, 8)
    run(service, clock, 4, grade=Grade.APPLIED, reflect_every=2)
    report = service.stats(day(3))
    assert report.open_streak == 4 and report.reflect_streak == 0


def test_fr14_novelty_share_is_reported_against_rho(service, clock):
    seed(service, 12)
    run(service, clock, 30)
    report = service.stats(day(29))
    assert report.rho == service.params.rho
    assert 0.0 <= report.novelty_share <= 1.0
    assert report.contested_slots >= 0


def test_fr14_pinned_entries_carry_their_guarantee(service, clock):
    ids = seed(service, 6)
    service.pin(ids[0], now=stamp())
    service.pin(ids[1], now=stamp())
    run(service, clock, 4)
    report = service.stats(day(3))
    assert {p.entry_id for p in report.pinned_status} == {ids[0], ids[1]}
    assert report.pinned_count == 2
    assert all(p.guarantee_days == service.params.P_rescue + 2 for p in report.pinned_status)


def test_fr14_archive_candidates_appear_at_three_flats(service, clock):
    seed(service, 4)
    run(service, clock, 40, grade=Grade.FLAT)
    report = service.stats(day(39))
    states = service.repo.all_states()
    expected = {eid for eid, st in states.items() if st.flat_streak >= 3}
    assert {c.entry_id for c in report.archive_candidates} == expected
    assert expected


def test_fr14_capacity_block_reports_the_identity(service, clock):
    ids = seed(service, 12)
    service.pin(ids[0], now=stamp())
    run(service, clock, 40)
    block = service.stats(day(39)).capacity
    assert block.k == 1
    assert block.pinned_rescue_load == 1 / service.params.P_rescue
    assert block.review_demand > 0
    assert block.stretch_lambda is not None


def test_fr14_advisory_only_appears_past_the_stretch_threshold(service, clock):
    seed(service, 6)
    run(service, clock, 20)
    block = service.stats(day(19)).capacity
    if block.stretch_lambda is not None and block.stretch_lambda > 3.0:
        assert block.advisory and "raise batch k" in block.advisory
        assert block.recommended_k and block.recommended_k > block.k
    else:
        assert block.advisory is None and block.recommended_k is None


def test_fr14_empty_library_reports_zeroes(service, clock):
    report = service.stats(day(0))
    assert report.total_entries == 0
    assert report.coverage == 0.0
    assert report.open_streak == 0
    assert report.capacity.review_demand == 0.0
