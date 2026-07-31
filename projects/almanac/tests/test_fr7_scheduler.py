"""FR-7: branch precedence, pools, priorities, the sigma controller, jitter."""

from __future__ import annotations

import datetime as dt
import math

import pytest
from almanac.engine.scheduler import (
    age_days,
    decide_slot,
    is_due,
    novelty_priority,
    novelty_share,
    overdue_ratio,
    select_daily,
    select_extra,
)
from almanac.models import EntryStatus, SelectPool

START = dt.date(2026, 1, 1)


def day(offset: int) -> dt.date:
    return START + dt.timedelta(days=offset)


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------


def test_fr7_cooldown_blocks_an_entry_seen_less_than_w_days_ago(params, entry_factory):
    fresh = entry_factory("a", exposures=1, last_offset=100 - params.W + 1, interval=10)
    decision, telemetry = decide_slot([fresh], [], day(100), 7, params, 0, set())
    assert telemetry.eligible == 0
    # The only path that can still serve it is the declared tiny-library
    # fallback, which flags itself as such.
    assert decision.select_pool is SelectPool.RELAXED


def test_fr7_cooldown_admits_an_entry_seen_exactly_w_days_ago(params, entry_factory):
    ready = entry_factory("a", exposures=1, last_offset=100 - params.W, interval=10)
    decision, telemetry = decide_slot([ready], [], day(100), 7, params, 0, set())
    assert telemetry.eligible == 1
    assert decision is not None


def test_fr7_never_seen_entries_are_always_eligible(params, entry_factory):
    brand_new = entry_factory("a", captured_offset=100)
    decision, _ = decide_slot([brand_new], [], day(100), 7, params, 0, set())
    assert decision.select_pool is SelectPool.NOVELTY_ONLY


def test_fr7_archived_entries_never_surface(params, entry_factory):
    archived = entry_factory("a", captured_offset=0, status=EntryStatus.ARCHIVED)
    decision, telemetry = decide_slot([archived], [], day(10), 7, params, 0, set())
    assert decision is None and telemetry.eligible == 0


def test_fr7_an_entry_already_surfaced_today_is_excluded(params, entry_factory):
    seen_today = entry_factory("a", exposures=1, last_offset=100, interval=10)
    decision, _ = decide_slot([seen_today], [], day(100), 7, params, 0, set())
    assert decision is None


# --------------------------------------------------------------------------
# branch precedence
# --------------------------------------------------------------------------


def test_fr7_pinned_rescue_outranks_every_other_branch(params, entry_factory):
    rescue = entry_factory(
        "pinned", exposures=2, last_offset=100 - params.P_rescue, interval=21, pinned=True
    )
    stale_novelty = entry_factory("novel", captured_offset=100 - params.S - 30)
    due_review = entry_factory("review", exposures=2, last_offset=50, interval=10)
    decision, _ = decide_slot(
        [stale_novelty, due_review, rescue], [], day(100), 7, params, 0, set()
    )
    assert decision.select_pool is SelectPool.PINNED_RESCUE
    assert decision.entry_id == "pinned"


def test_fr7_pinned_rescue_needs_p_rescue_days_and_a_prior_exposure(params, entry_factory):
    too_recent = entry_factory(
        "p", exposures=2, last_offset=100 - params.P_rescue + 1, interval=21, pinned=True
    )
    never_seen_pin = entry_factory("q", captured_offset=99, pinned=True)
    decision, telemetry = decide_slot(
        [too_recent, never_seen_pin], [], day(100), 7, params, 0, set()
    )
    assert telemetry.rescue_candidates == 0
    assert decision.select_pool is not SelectPool.PINNED_RESCUE


def test_fr7_pinned_rescue_takes_the_longest_waiting_entry(params, entry_factory):
    older = entry_factory("older", exposures=1, last_offset=100 - 60, interval=21, pinned=True)
    newer = entry_factory("newer", exposures=1, last_offset=100 - 33, interval=21, pinned=True)
    decision, _ = decide_slot([newer, older], [], day(100), 7, params, 0, set())
    assert decision.entry_id == "older"


def test_fr7_forced_novelty_fires_past_the_starvation_horizon(params, entry_factory):
    starved = entry_factory("starved", captured_offset=100 - params.S - 1)
    due_review = entry_factory("review", exposures=2, last_offset=50, interval=10)
    decision, telemetry = decide_slot([starved, due_review], [], day(100), 7, params, 0, set())
    assert telemetry.forced_candidates == 1
    assert decision.select_pool is SelectPool.FORCED_NOVELTY
    assert decision.entry_id == "starved"


def test_fr7_forced_novelty_does_not_fire_at_exactly_the_horizon(params, entry_factory):
    borderline = entry_factory("edge", captured_offset=100 - params.S)
    due_review = entry_factory("review", exposures=2, last_offset=50, interval=10)
    decision, telemetry = decide_slot([borderline, due_review], [], day(100), 7, params, 0, set())
    assert telemetry.forced_candidates == 0
    assert decision.select_pool in {SelectPool.NOVELTY, SelectPool.REVIEW}


def test_fr7_forced_drains_run_oldest_first(params, entry_factory):
    old = entry_factory("old", captured_offset=100 - params.S - 40)
    older = entry_factory("older", captured_offset=100 - params.S - 80)
    yesterday = entry_factory("yesterday", captured_offset=99)
    decision, _ = decide_slot([yesterday, old, older], [], day(100), 7, params, 0, set())
    assert decision.select_pool is SelectPool.FORCED_NOVELTY
    assert decision.entry_id == "older"


# --------------------------------------------------------------------------
# the contested branch and the sigma controller
# --------------------------------------------------------------------------


def _contested_library(entry_factory):
    return [
        entry_factory("novel", captured_offset=98),
        entry_factory("review", exposures=2, last_offset=60, interval=10),
    ]


def test_fr7_contested_slot_picks_novelty_below_the_target_share(params, entry_factory):
    history = [SelectPool.REVIEW] * params.H
    decision, telemetry = decide_slot(
        _contested_library(entry_factory), history, day(100), 7, params, 0, set()
    )
    assert telemetry.sigma == 0.0
    assert decision.select_pool is SelectPool.NOVELTY


def test_fr7_contested_slot_picks_review_at_or_above_the_target_share(params, entry_factory):
    history = [SelectPool.NOVELTY] * params.H
    decision, telemetry = decide_slot(
        _contested_library(entry_factory), history, day(100), 7, params, 0, set()
    )
    assert telemetry.sigma == 1.0
    assert decision.select_pool is SelectPool.REVIEW


def test_fr7_controller_is_bang_bang_around_rho(params, entry_factory):
    """rho * H = 9.8, so 9 novelty slots still buys novelty and 10 does not."""
    below = [SelectPool.NOVELTY] * 9 + [SelectPool.REVIEW] * 19
    above = [SelectPool.NOVELTY] * 10 + [SelectPool.REVIEW] * 18
    first, _ = decide_slot(_contested_library(entry_factory), below, day(100), 7, params, 0, set())
    second, _ = decide_slot(_contested_library(entry_factory), above, day(100), 7, params, 0, set())
    assert first.select_pool is SelectPool.NOVELTY
    assert second.select_pool is SelectPool.REVIEW


def test_fr7_novelty_share_over_contested_slots(params):
    assert novelty_share([], params) == 0.0
    assert novelty_share([SelectPool.NOVELTY], params) == 1.0
    assert novelty_share([SelectPool.NOVELTY, SelectPool.REVIEW], params) == 0.5
    window = [SelectPool.NOVELTY] * 10 + [SelectPool.REVIEW] * 18
    assert novelty_share(window, params) == pytest.approx(10 / 28)
    # only the trailing H slots count
    assert novelty_share([SelectPool.NOVELTY] * 100 + window, params) == pytest.approx(10 / 28)


def test_fr7_single_pool_branches_are_stamped_distinctly(params, entry_factory):
    novelty_only, _ = decide_slot(
        [entry_factory("n", captured_offset=99)], [], day(100), 7, params, 0, set()
    )
    review_only, _ = decide_slot(
        [entry_factory("r", exposures=2, last_offset=60, interval=10)],
        [],
        day(100),
        7,
        params,
        0,
        set(),
    )
    assert novelty_only.select_pool is SelectPool.NOVELTY_ONLY
    assert review_only.select_pool is SelectPool.REVIEW_ONLY


def test_fr7_not_due_is_the_only_early_branch(params, entry_factory):
    not_due = entry_factory("a", exposures=2, last_offset=88, interval=60)
    assert not is_due(not_due, day(100), params)
    decision, telemetry = decide_slot([not_due], [], day(100), 7, params, 0, set())
    assert telemetry.review_pool == 0
    assert decision.select_pool is SelectPool.NOT_DUE


def test_fr7_not_due_takes_the_largest_overdue_ratio(params, entry_factory):
    nearer = entry_factory("nearer", exposures=2, last_offset=88, interval=60)
    further = entry_factory("further", exposures=2, last_offset=70, interval=60)
    decision, _ = decide_slot([nearer, further], [], day(100), 7, params, 0, set())
    assert decision.entry_id == "further"


def test_fr7_relaxed_fallback_takes_the_least_recently_seen(params, entry_factory):
    a = entry_factory("a", exposures=1, last_offset=99, interval=10)
    b = entry_factory("b", exposures=1, last_offset=95, interval=10)
    decision, _ = decide_slot([a, b], [], day(100), 7, params, 0, set())
    assert decision.select_pool is SelectPool.RELAXED
    assert decision.entry_id == "b"


def test_fr7_relaxed_fallback_breaks_ties_by_entry_id(params, entry_factory):
    a = entry_factory("aaa", exposures=1, last_offset=95, interval=10)
    b = entry_factory("bbb", exposures=1, last_offset=95, interval=10)
    decision, _ = decide_slot([b, a], [], day(100), 7, params, 0, set())
    assert decision.entry_id == "aaa"


def test_fr7_no_active_entry_yields_no_card(params):
    decision, _ = decide_slot([], [], day(100), 7, params, 0, set())
    assert decision is None


# --------------------------------------------------------------------------
# priorities
# --------------------------------------------------------------------------


def test_fr7_novelty_priority_is_u_shaped(params):
    fresh = novelty_priority(0, params)
    trough = novelty_priority(30, params)
    at_horizon = novelty_priority(params.S, params)
    past_horizon = novelty_priority(params.S + 20, params)
    assert fresh == pytest.approx(1.0)
    assert trough < fresh
    assert at_horizon == pytest.approx(1.0, abs=1e-3)
    assert past_horizon > 1.0


def test_fr7_novelty_priority_matches_the_declared_formula(params):
    age = 12
    expected = math.exp(-age / params.tau) + (age / params.S) ** 2
    assert novelty_priority(age, params) == pytest.approx(expected)


def test_fr7_age_is_never_negative(params, entry_factory):
    future_capture = entry_factory("a", captured_offset=110)
    assert age_days(future_capture, day(100)) == 0


def test_fr7_overdue_ratio_is_relative_delay(params, entry_factory):
    entry = entry_factory("a", exposures=2, last_offset=70, interval=20)
    assert overdue_ratio(entry, day(100), params) == pytest.approx(30 / 20)
    unseen = entry_factory("b")
    assert overdue_ratio(unseen, day(100), params) == math.inf


def test_fr7_jitter_is_small_relative_to_a_meaningful_priority_gap(params, entry_factory):
    """0.05 must not let a fresh capture outrank a clearly older backlog entry."""
    fresh = entry_factory("fresh", captured_offset=100)
    ancient = entry_factory("ancient", captured_offset=100 - params.S - 30)
    for seed in range(20):
        decision, _ = decide_slot([fresh, ancient], [], day(100), seed, params, 0, set())
        assert decision.entry_id == "ancient"


# --------------------------------------------------------------------------
# multi-slot batches and determinism
# --------------------------------------------------------------------------


def test_fr7_batch_slots_are_filled_sequentially_without_repeats(params, entry_factory):
    library = [entry_factory(f"e{i}", captured_offset=90) for i in range(5)]
    decisions = select_daily(library, [], day(100), 7, params, k=3)
    assert [d.slot for d in decisions] == [0, 1, 2]
    assert len({d.entry_id for d in decisions}) == 3


def test_fr7_batch_stops_when_candidates_run_out(params, entry_factory):
    library = [entry_factory("only", captured_offset=90)]
    decisions = select_daily(library, [], day(100), 7, params, k=3)
    assert len(decisions) == 1


def test_fr7_selection_is_reproducible_for_a_given_seed(params, entry_factory):
    library = [entry_factory(f"e{i}", captured_offset=90 - i) for i in range(12)]
    first = select_daily(library, [], day(100), 7, params, k=3)
    second = select_daily(library, [], day(100), 7, params, k=3)
    assert first == second


def test_fr7_a_different_seed_can_change_the_selection(params, entry_factory):
    library = [entry_factory(f"e{i}", captured_offset=90) for i in range(20)]
    seven = select_daily(library, [], day(100), 7, params, k=1)
    eight = select_daily(library, [], day(100), 8, params, k=1)
    assert seven != eight


def test_fr7_within_day_contested_stamps_feed_the_next_slot(params, entry_factory):
    library = [
        entry_factory("n1", captured_offset=98),
        entry_factory("n2", captured_offset=97),
        entry_factory("r1", exposures=2, last_offset=60, interval=10),
        entry_factory("r2", exposures=2, last_offset=59, interval=10),
    ]
    history = [SelectPool.REVIEW] * 27
    decisions = select_daily(library, history, day(100), 7, params, k=2)
    # first slot lifts sigma from 0/28 to 1/28, still below rho, so novelty again
    assert [d.select_pool for d in decisions] == [SelectPool.NOVELTY, SelectPool.NOVELTY]


# --------------------------------------------------------------------------
# FR-8 extra draws
# --------------------------------------------------------------------------


def test_fr8_extra_draw_is_always_stamped_extra(params, entry_factory):
    decision = select_extra([entry_factory("a", captured_offset=99)], day(100), 7, params)
    assert decision.select_pool is SelectPool.EXTRA
    assert decision.slot == 0


def test_fr8_extra_draw_prefers_never_seen_then_due_then_not_due(params, entry_factory):
    never = entry_factory("never", captured_offset=99)
    due = entry_factory("due", exposures=2, last_offset=60, interval=10)
    not_due = entry_factory("notdue", exposures=2, last_offset=88, interval=60)
    assert select_extra([not_due, due, never], day(100), 7, params).entry_id == "never"
    assert select_extra([not_due, due], day(100), 7, params).entry_id == "due"
    assert select_extra([not_due], day(100), 7, params).entry_id == "notdue"


def test_fr8_extra_draw_honours_the_filter(params, entry_factory):
    a = entry_factory("a", captured_offset=99)
    b = entry_factory("b", captured_offset=99)
    decision = select_extra([a, b], day(100), 7, params, allowed_ids={"b"})
    assert decision.entry_id == "b"
    assert select_extra([a, b], day(100), 7, params, allowed_ids=set()) is None


def test_fr8_extra_draw_falls_back_within_the_filter(params, entry_factory):
    cooling = entry_factory("cool", exposures=1, last_offset=99, interval=10)
    decision = select_extra([cooling], day(100), 7, params)
    assert decision is not None and decision.entry_id == "cool"
    assert decision.select_pool is SelectPool.EXTRA


def test_fr8_extra_draw_skips_entries_already_surfaced_today(params, entry_factory):
    today = entry_factory("today", exposures=1, last_offset=100, interval=10)
    assert select_extra([today], day(100), 7, params) is None
