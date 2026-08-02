"""FR-14: the stats report, computed purely from the event log.

Everything here is a function of (entries, states, surfacings, reflections,
params, date).  The capacity block is the identity from SCOPE.md rendered as
product behaviour: the tool measures the stretch factor and *advises* a larger
batch, but never changes ``k`` on the user's behalf (non-goal 12).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence

from almanac.engine.scheduler import (
    FoldEvent,
    SchedulerEntry,
    advance_state,
    days_since_seen,
    effective_interval,
    initial_state,
    is_archive_candidate,
    novelty_share,
)
from almanac.models import (
    CONTESTED_POOLS,
    ArchiveCandidate,
    CapacityBlock,
    Entry,
    EntryStatus,
    ExposureBucket,
    Grade,
    PinnedStatus,
    SchedulerParams,
    SchedulerState,
    StatsReport,
    Surfacing,
    SurfacingKind,
)

#: Window used to estimate the capture rate A and the realized stretch factor.
CAPTURE_RATE_WINDOW_DAYS = 28
STRETCH_WINDOW_DAYS = 90
EXCERPT_WORDS = 8


def _excerpt(text: str, words: int = EXCERPT_WORDS) -> str:
    parts = text.split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]) + "…"


def median(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def fold_events_by_entry(
    surfacings: Sequence[Surfacing], grades: Mapping[str, Grade]
) -> dict[str, list[FoldEvent]]:
    """Group the event log into per-entry, chronologically sortable events."""
    out: dict[str, list[FoldEvent]] = {}
    for surfacing in surfacings:
        out.setdefault(surfacing.entry_id, []).append(
            FoldEvent(
                on_date=surfacing.on_date,
                created_at=surfacing.created_at,
                grade=grades.get(surfacing.id),
            )
        )
    for events in out.values():
        events.sort(key=FoldEvent.sort_key)
    return out


def replay_overdue_ratios(
    surfacings: Sequence[Surfacing],
    grades: Mapping[str, Grade],
    pinned: Mapping[str, bool],
    params: SchedulerParams,
    since: dt.date | None = None,
) -> list[float]:
    """Realized ``O`` at service time for every review surfacing.

    Replays the FR-6 fold so each surfacing is scored against the interval that
    was in force *before* it happened.  ``pinned`` uses the entry's current pin
    state (pin history is not part of the event log).
    """
    ratios: list[float] = []
    for entry_id, events in fold_events_by_entry(surfacings, grades).items():
        state = initial_state(entry_id, params)
        prior_grade: Grade | None = None
        is_pinned = pinned.get(entry_id, False)
        for event in events:
            if state.exposure_count >= 1 and state.last_surfaced_on is not None:
                elapsed = (event.on_date - state.last_surfaced_on).days
                base = (
                    min(state.interval_days, params.pinned_cap)
                    if is_pinned
                    else state.interval_days
                )
                interval = max(params.W, base)
                if since is None or event.on_date >= since:
                    ratios.append(elapsed / interval)
            state = advance_state(state, event.on_date, prior_grade, params)
            prior_grade = event.grade
    return ratios


def materialized_days(surfacings: Sequence[Surfacing]) -> list[dt.date]:
    """Dates on which a daily set was materialized, ascending and unique."""
    return sorted({s.on_date for s in surfacings if s.kind is SurfacingKind.DAILY})


def open_streak(days: Sequence[dt.date], on_date: dt.date) -> int:
    """Consecutive materialized days ending on ``on_date`` (0 if today is not one)."""
    present = set(days)
    streak = 0
    cursor = on_date
    while cursor in present:
        streak += 1
        cursor -= dt.timedelta(days=1)
    return streak


def reflect_streak(
    surfacings: Sequence[Surfacing], reflected_ids: set[str], on_date: dt.date
) -> int:
    """Consecutive materialized days ending today that also carry a reflection."""
    good: set[dt.date] = set()
    by_date: dict[dt.date, list[Surfacing]] = {}
    for surfacing in surfacings:
        if surfacing.kind is SurfacingKind.DAILY:
            by_date.setdefault(surfacing.on_date, []).append(surfacing)
    for day, rows in by_date.items():
        if any(row.id in reflected_ids for row in rows):
            good.add(day)
    return open_streak(sorted(good), on_date)


def contested_pools(surfacings: Sequence[Surfacing]) -> list:
    """The ordered contested-slot stamps (``novelty``/``review``) in the log."""
    daily = [s for s in surfacings if s.kind is SurfacingKind.DAILY]
    daily.sort(key=lambda s: (s.on_date, s.slot))
    return [s.select_pool for s in daily if s.select_pool in CONTESTED_POOLS]


def drain_horizon_days(
    batch: int, pinned_count: int, batch_size: int, params: SchedulerParams
) -> int | None:
    """FR-5: ``S + ceil(B / (k - r))`` days for a bulk import of ``batch`` entries.

    ``r = n_pinned / P_rescue`` is the pinned-rescue load from the capacity
    identity (SCOPE.md).  ``None`` when the rescue load alone consumes the
    daily budget — there is no honest finite horizon to quote in that case.
    """
    if batch <= 0:
        return 0
    rate = batch_size - pinned_count / params.P_rescue
    if rate <= 0:
        return None
    return params.S + math.ceil(batch / rate)


def build_stats(
    entries: Sequence[Entry],
    states: Mapping[str, SchedulerState],
    surfacings: Sequence[Surfacing],
    grades: Mapping[str, Grade],
    params: SchedulerParams,
    on_date: dt.date,
    k: int | None = None,
) -> StatsReport:
    """The whole FR-14 report."""
    batch_k = params.k if k is None else k
    active = [e for e in entries if e.status is EntryStatus.ACTIVE]
    archived = [e for e in entries if e.status is EntryStatus.ARCHIVED]
    pinned_entries = [e for e in active if e.pinned]

    def state_for(entry: Entry) -> SchedulerState:
        return states.get(entry.id) or initial_state(entry.id, params)

    scheduler_entries = [SchedulerEntry.build(e, state_for(e)) for e in active]
    surfaced = [se for se in scheduler_entries if se.seen]
    coverage = (len(surfaced) / len(active)) if active else 0.0

    histogram: dict[int, int] = {}
    for se in scheduler_entries:
        histogram[se.exposure_count] = histogram.get(se.exposure_count, 0) + 1

    days = materialized_days(surfacings)
    reflected_ids = set(grades)

    pinned_guarantee = params.P_rescue + math.ceil(len(pinned_entries) / batch_k)
    pinned_status = [
        PinnedStatus(
            entry_id=se.entry_id,
            excerpt=_excerpt(next(e.text for e in active if e.id == se.entry_id)),
            days_since_seen=days_since_seen(se, on_date),
            guarantee_days=pinned_guarantee,
        )
        for se in sorted(scheduler_entries, key=lambda s: s.entry_id)
        if se.pinned
    ]
    candidates = [
        ArchiveCandidate(
            entry_id=se.entry_id,
            excerpt=_excerpt(next(e.text for e in active if e.id == se.entry_id)),
            flat_streak=se.flat_streak,
        )
        for se in sorted(scheduler_entries, key=lambda s: s.entry_id)
        if is_archive_candidate(se, params)
    ]

    # --- capacity identity -------------------------------------------------
    rescue_load = len(pinned_entries) / params.P_rescue
    window_start = on_date - dt.timedelta(days=CAPTURE_RATE_WINDOW_DAYS)
    recent_captures = sum(1 for e in entries if window_start < e.captured_on <= on_date)
    capture_rate = recent_captures / CAPTURE_RATE_WINDOW_DAYS
    capacity = batch_k - rescue_load - capture_rate
    demand = sum(1.0 / effective_interval(se, params) for se in surfaced)
    intervals = [effective_interval(se, params) for se in surfaced]
    sustainable = (
        capacity * (sum(intervals) / len(intervals)) if intervals and capacity > 0 else None
    )
    stretch = median(
        replay_overdue_ratios(
            surfacings,
            grades,
            {e.id: e.pinned for e in entries},
            params,
            since=on_date - dt.timedelta(days=STRETCH_WINDOW_DAYS),
        )
    )
    advisory: str | None = None
    recommended_k: int | None = None
    if stretch is not None and stretch > 3.0:
        target = rescue_load + capture_rate + max(capacity, 0.0) * stretch / 3.0
        recommended_k = max(batch_k + 1, min(5, math.ceil(target)))
        advisory = (
            f"stretch factor is {stretch:.1f}x: reviews are running "
            f"{stretch:.1f} times their nominal interval — raise batch k to {recommended_k}"
        )

    return StatsReport(
        on_date=on_date,
        total_entries=len(entries),
        active_entries=len(active),
        archived_entries=len(archived),
        by_kind={
            kind: sum(1 for e in entries if e.kind.value == kind) for kind in ("quote", "idea")
        },
        pinned_count=len(pinned_entries),
        coverage=coverage,
        exposure_histogram=[
            ExposureBucket(exposures=count, entries=histogram[count]) for count in sorted(histogram)
        ],
        open_streak=open_streak(days, on_date),
        reflect_streak=reflect_streak(surfacings, reflected_ids, on_date),
        novelty_share=novelty_share(contested_pools(surfacings), params),
        rho=params.rho,
        contested_slots=len(contested_pools(surfacings)),
        pinned_status=pinned_status,
        archive_candidates=candidates,
        capacity=CapacityBlock(
            k=batch_k,
            pinned_rescue_load=rescue_load,
            capture_rate=capture_rate,
            review_capacity=capacity,
            review_demand=demand,
            stretch_lambda=stretch,
            sustainable_library=sustainable,
            recommended_k=recommended_k,
            advisory=advisory,
        ),
    )
