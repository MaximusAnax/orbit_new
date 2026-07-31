"""FR-11 / D10: exponential recency decay and the hard yesterday block."""

from __future__ import annotations

import pytest
from dresscast.engine.models import VARIETY_HALF_LIFE_DAYS, WearHistory
from dresscast.engine.variety import (
    days_between,
    days_since_worn,
    least_fresh,
    previous_day,
    recency_penalty,
    repeats_yesterday,
    variety_score,
)

TODAY = "2026-04-14"


def _history(**last_worn: str) -> WearHistory:
    return WearHistory(last_worn=dict(last_worn), yesterday_sets=())


def test_fr11_days_between_and_previous_day():
    assert days_between("2026-04-11", TODAY) == 3
    assert previous_day(TODAY) == "2026-04-13"
    assert previous_day("2026-03-01") == "2026-02-28"


def test_fr11_recency_penalty_halves_every_three_days():
    assert recency_penalty(0) == pytest.approx(1.0)
    assert recency_penalty(3) == pytest.approx(0.5)
    assert recency_penalty(6) == pytest.approx(0.25)
    assert recency_penalty(None) == 0.0
    assert VARIETY_HALF_LIFE_DAYS == 3.0


def test_fr11_never_worn_items_carry_no_penalty():
    assert variety_score(["a", "b"], _history(), TODAY) == pytest.approx(1.0)


def test_fr11_variety_score_matches_the_worked_example():
    """One item worn 3 days ago among five → 1 - 0.5/5 = 0.90 (DATA_MODEL §6)."""
    history = _history(sweater="2026-04-11")
    score = variety_score(["shirt", "sweater", "coat", "jeans", "boots"], history, TODAY)
    assert score == pytest.approx(0.9)


def test_fr11_wearing_everything_today_drives_the_score_to_zero():
    history = _history(a=TODAY, b=TODAY)
    assert variety_score(["a", "b"], history, TODAY) == pytest.approx(0.0)


def test_fr11_older_wears_score_better_than_recent_ones():
    recent = _history(a="2026-04-13")
    old = _history(a="2026-04-01")
    assert variety_score(["a"], old, TODAY) > variety_score(["a"], recent, TODAY)


def test_fr11_days_since_worn_never_goes_negative():
    future = _history(a="2026-04-20")
    assert days_since_worn("a", future, TODAY) == 0


def test_fr11_hc8_blocks_only_an_exact_yesterday_repeat():
    worn = frozenset({"a", "b", "c"})
    history = WearHistory(last_worn={}, yesterday_sets=(worn,))
    assert repeats_yesterday(worn, history)
    assert not repeats_yesterday(frozenset({"a", "b"}), history)
    assert not repeats_yesterday(frozenset({"a", "b", "c", "d"}), history)


def test_fr11_hc8_checks_every_log_from_yesterday():
    gym = frozenset({"tee", "shorts", "sneakers"})
    work = frozenset({"shirt", "chinos", "shoes"})
    history = WearHistory(last_worn={}, yesterday_sets=(gym, work))
    assert repeats_yesterday(gym, history)
    assert repeats_yesterday(work, history)


def test_fr11_least_fresh_reports_the_most_recently_worn_core_item():
    history = _history(a="2026-04-13", b="2026-04-01")
    assert least_fresh(["a", "b"], history, TODAY) == ("a", 1)
    assert least_fresh(["c"], _history(), TODAY) is None


def test_fr11_least_fresh_breaks_ties_on_garment_id():
    history = _history(zeta="2026-04-13", alpha="2026-04-13")
    assert least_fresh(["zeta", "alpha"], history, TODAY) == ("alpha", 1)


def test_fr11_history_uses_the_logged_layer_role_not_the_current_one():
    """Re-tagging a garment cannot rewrite what yesterday's outfit was (§2.7)."""
    from datetime import datetime

    from dresscast.engine.models import WearLog, WearLogItem

    log = WearLog(
        id="l1",
        date="2026-04-13",
        source="manual",
        created_at=datetime(2026, 4, 13, 8, 0),
        items=[
            WearLogItem(garment_id="flannel", layer_role="base"),
            WearLogItem(garment_id="hat", layer_role="accessory"),
        ],
    )
    assert log.core_ids() == frozenset({"flannel"})
