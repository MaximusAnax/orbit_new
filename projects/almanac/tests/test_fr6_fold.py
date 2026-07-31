"""FR-6: the scheduling-state fold and the interval-update table."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.engine.scheduler import (
    FoldEvent,
    advance_state,
    effective_interval,
    fold_scheduler_state,
    initial_state,
    is_archive_candidate,
    next_interval,
    round_half_up,
)
from almanac.models import Grade

START = dt.date(2026, 1, 1)


def on(offset: int) -> dt.date:
    return START + dt.timedelta(days=offset)


def _fold(params, grades, start=0, spacing=15):
    """Fold a run of surfacings whose i-th reflection carries ``grades[i]``."""
    events = [
        FoldEvent(
            on_date=on(start + index * spacing),
            created_at=dt.datetime(2026, 1, 1) + dt.timedelta(days=index),
            grade=grade,
        )
        for index, grade in enumerate(grades)
    ]
    return fold_scheduler_state("e1", events, params)


def test_fr6_initial_state_is_unseen():
    from almanac.datasets import load_datasets

    params = load_datasets().params
    state = initial_state("e1", params)
    assert state.exposure_count == 0
    assert state.last_surfaced_on is None
    assert state.interval_days == params.I0


def test_fr6_debut_sets_interval_to_i0(params):
    state = _fold(params, [None])
    assert (state.exposure_count, state.interval_days, state.flat_streak) == (1, params.I0, 0)


@pytest.mark.parametrize(
    ("grade", "expected"),
    [(Grade.RESONATED, 13), (Grade.APPLIED, 15), (None, 19), (Grade.FLAT, 30)],
)
def test_fr6_four_grades_produce_four_distinct_first_review_intervals(params, grade, expected):
    """Every branch of the table is realizable: none is swallowed by the cooldown."""
    state = _fold(params, [grade, None])
    assert state.interval_days == expected
    assert state.interval_days >= params.W


def test_fr6_first_review_intervals_are_monotone_in_how_well_it_is_working(params):
    intervals = [
        _fold(params, [grade, None]).interval_days
        for grade in (Grade.RESONATED, Grade.APPLIED, None, Grade.FLAT)
    ]
    assert intervals == sorted(intervals)
    assert len(set(intervals)) == 4


def test_fr6_rounding_is_half_up_in_decimal_not_binary(params):
    # 15 * 1.9 is exactly 28.5 in decimal but 28.499999999999996 in float.
    assert round_half_up(15 * 1.9) in (28, 29)  # float path is ambiguous...
    interval, _ = next_interval(15, None, 0, params)
    assert interval == 29  # ...the Decimal path is not.


def test_fr6_two_consecutive_flats_demote_to_hi_flat(params):
    state = _fold(params, [Grade.FLAT, Grade.FLAT, None])
    assert state.flat_streak == 2
    assert state.interval_days == params.clamp.hi_flat


def test_fr6_three_flats_flag_an_archive_candidate(params, entry_factory):
    state = _fold(params, [Grade.FLAT, Grade.FLAT, Grade.FLAT, None])
    entry = entry_factory("e1", exposures=4, last_offset=0, flat_streak=state.flat_streak)
    assert state.flat_streak == 3
    assert is_archive_candidate(entry, params)


def test_fr6_a_non_flat_grade_resets_the_streak_and_re_enters_rotation(params):
    demoted = _fold(params, [Grade.FLAT, Grade.FLAT, None])
    assert demoted.interval_days == 240
    revived = _fold(params, [Grade.FLAT, Grade.FLAT, Grade.APPLIED, None])
    assert revived.flat_streak == 0
    assert revived.interval_days == params.clamp.hi == 60


def test_fr6_none_grade_leaves_the_flat_streak_unchanged(params):
    state = _fold(params, [Grade.FLAT, None, None])
    assert state.flat_streak == 1
    # one flat then silence: 10 -> 30 -> clamp(30 * 1.9) = 57
    assert state.interval_days == 57


def test_fr6_interval_is_clamped_to_hi(params):
    state = _fold(params, [None] * 8)
    assert state.interval_days == params.clamp.hi


def test_fr6_effective_interval_caps_pinned_entries(params, entry_factory):
    pinned = entry_factory("p", exposures=3, last_offset=0, interval=60, pinned=True)
    unpinned = entry_factory("u", exposures=3, last_offset=0, interval=60)
    assert effective_interval(pinned, params) == params.pinned_cap == 21
    assert effective_interval(unpinned, params) == 60


def test_fr6_effective_interval_never_falls_below_the_cooldown(params, entry_factory):
    tiny = entry_factory("t", exposures=1, last_offset=0, interval=1)
    assert effective_interval(tiny, params) == params.W


def test_fr6_fold_matches_the_incremental_advance(params):
    grades = [Grade.APPLIED, None, Grade.FLAT, Grade.RESONATED, None]
    folded = _fold(params, grades)
    state = initial_state("e1", params)
    prior = None
    for index, grade in enumerate(grades):
        state = advance_state(state, on(index * 15), prior, params)
        prior = grade
    assert state == folded


def test_fr6_fold_orders_events_by_date_then_creation(params):
    events = [
        FoldEvent(on_date=on(30), created_at=dt.datetime(2026, 3, 1), grade=None),
        FoldEvent(on_date=on(0), created_at=dt.datetime(2026, 1, 1), grade=Grade.APPLIED),
        FoldEvent(on_date=on(15), created_at=dt.datetime(2026, 2, 1), grade=None),
    ]
    state = fold_scheduler_state("e1", events, params)
    assert state.exposure_count == 3
    assert state.last_surfaced_on == on(30)
    # applied (x1.5) then none (x1.9): 10 -> 15 -> 29
    assert state.interval_days == 29


def test_fr6_worked_example_from_the_data_model(params, entry_factory):
    """DATA_MODEL.md §SchedulerState trace: 10 -> 13 -> 25, I_eff 21 when pinned."""
    state = _fold(params, [Grade.RESONATED, None, Grade.APPLIED])
    assert (state.exposure_count, state.interval_days, state.flat_streak) == (3, 25, 0)
    entry = entry_factory("e", exposures=3, last_offset=0, interval=25, pinned=True)
    assert effective_interval(entry, params) == 21
