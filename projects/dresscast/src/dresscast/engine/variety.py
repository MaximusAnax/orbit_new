"""Recency decay and repeat detection (SCOPE.md FR-11, HC-8, D10).

Wearing an item halves its penalty every three days; identical core sets on
consecutive days are blocked outright, because "don't repeat yesterday" is a
constraint the owner stated, not a preference.

History is read from the ``layer_role`` snapshotted on each WearLogItem at log
time (DATA_MODEL.md §2.7), so re-tagging a garment can never rewrite what
yesterday's outfit was — that projection happens in the store, and this module
consumes its output.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date as date_cls
from datetime import timedelta

from dresscast.engine.models import VARIETY_HALF_LIFE_DAYS, WearHistory


def days_between(earlier: str, later: str) -> int:
    """Whole days from ``earlier`` to ``later`` (both ``YYYY-MM-DD``)."""
    return (date_cls.fromisoformat(later) - date_cls.fromisoformat(earlier)).days


def previous_day(day: str) -> str:
    """The calendar day before ``day`` — HC-8's "yesterday"."""
    return (date_cls.fromisoformat(day) - timedelta(days=1)).isoformat()


def recency_penalty(days: int | None) -> float:
    """``2^(-d/3)`` for a garment last worn ``d`` days ago; 0 if never worn."""
    if days is None:
        return 0.0
    return 2.0 ** (-max(0, days) / VARIETY_HALF_LIFE_DAYS)


def days_since_worn(garment_id: str, history: WearHistory, today: str) -> int | None:
    """Days since ``garment_id`` was last worn on or before ``today``."""
    last = history.last_worn.get(garment_id)
    if last is None:
        return None
    return max(0, days_between(last, today))


def variety_score(garment_ids: Iterable[str], history: WearHistory, today: str) -> float:
    """FR-11's ``S_variety = 1 - mean_i 2^(-d_i/3)`` over core items."""
    ids = list(garment_ids)
    if not ids:
        return 1.0
    total = sum(recency_penalty(days_since_worn(g, history, today)) for g in ids)
    return min(1.0, max(0.0, 1.0 - total / len(ids)))


def repeats_yesterday(core_ids: frozenset[str], history: WearHistory) -> bool:
    """HC-8: does this core-item set match any set worn yesterday exactly?"""
    return any(core_ids == worn for worn in history.yesterday_sets)


def least_fresh(
    garment_ids: Iterable[str], history: WearHistory, today: str
) -> tuple[str, int] | None:
    """The core item worn most recently, with its age — FR-15's variety line."""
    best: tuple[str, int] | None = None
    for gid in garment_ids:
        days = days_since_worn(gid, history, today)
        if days is None:
            continue
        if best is None or days < best[1] or (days == best[1] and gid < best[0]):
            best = (gid, days)
    return best
