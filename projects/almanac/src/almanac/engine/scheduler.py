"""FR-6/FR-7/FR-8: the daily scheduler (hard part A).

Three pure pieces live here:

* **The fold (FR-6).** Scheduling state is *derived* — a left-fold over an
  entry's ordered surfacing events, where each step applies the grade of the
  *previous* surfacing's reflection.  The materialized ``scheduler_state`` row
  is only ever a cache of this function.
* **Daily selection (FR-7).** ``select_daily`` fills ``k`` slots sequentially
  through six branches in strict precedence order, stamping each pick with the
  branch that produced it (``select_pool``).  That stamp is the scheduler's
  only counter: the realized novelty share sigma is read back out of the event
  log, so the whole thing stays a pure function of history.
* **Extra draws (FR-8).** ``select_extra`` serves an on-demand card from a
  filtered candidate set; draws are exposures (they start the cooldown and
  shift due dates) but never occupy a daily slot and never enter sigma.

Nothing here reads a clock, the filesystem, the network or ``random``: the
date is a parameter and all variety comes from SHA-256 keyed jitter.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from almanac.engine.jitter import jitter
from almanac.models import (
    CONTESTED_POOLS,
    Entry,
    EntryStatus,
    Grade,
    SchedulerParams,
    SchedulerState,
    SelectPool,
)

_EPOCH = dt.date(1, 1, 1)


def round_half_up(value: float | Decimal) -> int:
    """Round to a whole number, halves away from zero (FR-6).

    Decimal rather than ``round()`` or ``floor(x + 0.5)``: ``15 * 1.9`` is
    exactly 28.5 in decimal but 28.499999999999996 in binary floating point,
    and that case is reachable (an ``applied`` debut followed by no
    reflection), so the two differ on a real schedule.
    """
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _mult(params: SchedulerParams, grade: Grade | None) -> Decimal:
    return Decimal(str(params.multiplier(grade)))


# --------------------------------------------------------------------------
# FR-6 — scheduling state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldEvent:
    """One surfacing in the fold: when it happened and how it was graded."""

    on_date: dt.date
    created_at: dt.datetime
    grade: Grade | None = None

    def sort_key(self) -> tuple[dt.date, dt.datetime]:
        return (self.on_date, self.created_at)


@dataclass(frozen=True)
class SchedulerEntry:
    """An entry as the scheduler sees it: identity, age, pin, derived state."""

    entry_id: str
    captured_on: dt.date
    pinned: bool
    status: EntryStatus
    state: SchedulerState

    @classmethod
    def build(cls, entry: Entry, state: SchedulerState) -> SchedulerEntry:
        return cls(
            entry_id=entry.id,
            captured_on=entry.captured_on,
            pinned=entry.pinned,
            status=entry.status,
            state=state,
        )

    @property
    def exposure_count(self) -> int:
        return self.state.exposure_count

    @property
    def last_surfaced_on(self) -> dt.date | None:
        return self.state.last_surfaced_on

    @property
    def interval_days(self) -> int:
        return self.state.interval_days

    @property
    def flat_streak(self) -> int:
        return self.state.flat_streak

    @property
    def seen(self) -> bool:
        return self.state.exposure_count > 0


def initial_state(entry_id: str, params: SchedulerParams) -> SchedulerState:
    """The state of an entry that has never been surfaced."""
    return SchedulerState(
        entry_id=entry_id,
        exposure_count=0,
        last_surfaced_on=None,
        interval_days=params.I0,
        flat_streak=0,
    )


def next_interval(
    interval_days: int, prior_grade: Grade | None, flat_streak: int, params: SchedulerParams
) -> tuple[int, int]:
    """The FR-6 table: ``(interval, flat_streak)`` after applying a grade.

    Note the deliberate inversion of SM-2 (SCOPE.md D1): a *negative* signal
    lengthens the interval, and two consecutive ``flat`` grades demote the
    entry to ``hi_flat`` outright, because the objective is portfolio value
    delivered rather than per-item retention.
    """
    if prior_grade is Grade.FLAT:
        streak = flat_streak + 1
    elif prior_grade is None:
        streak = flat_streak
    else:
        streak = 0
    if streak >= params.demote_flat_streak:
        return params.clamp.hi_flat, streak
    scaled = round_half_up(Decimal(interval_days) * _mult(params, prior_grade))
    clamped = max(params.clamp.lo, min(params.clamp.hi, scaled))
    return clamped, streak


def advance_state(
    state: SchedulerState,
    on_date: dt.date,
    prior_grade: Grade | None,
    params: SchedulerParams,
) -> SchedulerState:
    """Apply one surfacing to an entry's state (one step of the fold)."""
    if state.exposure_count == 0:
        return SchedulerState(
            entry_id=state.entry_id,
            exposure_count=1,
            last_surfaced_on=on_date,
            interval_days=params.I0,
            flat_streak=0,
        )
    interval, streak = next_interval(state.interval_days, prior_grade, state.flat_streak, params)
    return SchedulerState(
        entry_id=state.entry_id,
        exposure_count=state.exposure_count + 1,
        last_surfaced_on=on_date,
        interval_days=interval,
        flat_streak=streak,
    )


def fold_scheduler_state(
    entry_id: str, events: Iterable[FoldEvent], params: SchedulerParams
) -> SchedulerState:
    """Recompute an entry's scheduler state from its event log (FR-6, M7c)."""
    ordered = sorted(events, key=FoldEvent.sort_key)
    state = initial_state(entry_id, params)
    prior_grade: Grade | None = None
    for event in ordered:
        state = advance_state(state, event.on_date, prior_grade, params)
        prior_grade = event.grade
    return state


def effective_interval(entry: SchedulerEntry, params: SchedulerParams) -> int:
    """``I_eff(e) = max(W, min(I, pinned_cap) if pinned else I)``."""
    base = min(entry.interval_days, params.pinned_cap) if entry.pinned else entry.interval_days
    return max(params.W, base)


def is_archive_candidate(entry: SchedulerEntry, params: SchedulerParams) -> bool:
    """FR-6/FR-14: ``flat_streak >= 3`` suggests archiving. Never automatic."""
    return entry.flat_streak >= params.archive_flat_streak


# --------------------------------------------------------------------------
# FR-7 — priorities and the novelty share
# --------------------------------------------------------------------------


def age_days(entry: SchedulerEntry, on_date: dt.date) -> int:
    return max(0, (on_date - entry.captured_on).days)


def days_since_seen(entry: SchedulerEntry, on_date: dt.date) -> int | None:
    if entry.last_surfaced_on is None:
        return None
    return (on_date - entry.last_surfaced_on).days


def novelty_priority(age: int, params: SchedulerParams) -> float:
    """``n(e) = exp(-age / tau) + (age / S)**2`` — U-shaped in capture age.

    This week's captures surface hot; never-seen entries then climb back past
    fresh arrivals as they approach the forcing horizon (``n > 1`` once
    ``age > S``), so forced drains run oldest-first.
    """
    return math.exp(-age / params.tau) + (age / params.S) ** 2


def overdue_ratio(entry: SchedulerEntry, on_date: dt.date, params: SchedulerParams) -> float:
    """``O(e) = (D - last_surfaced_on) / I_eff(e)`` — maximum relative delay."""
    elapsed = days_since_seen(entry, on_date)
    if elapsed is None:
        return math.inf
    return elapsed / effective_interval(entry, params)


def is_due(entry: SchedulerEntry, on_date: dt.date, params: SchedulerParams) -> bool:
    elapsed = days_since_seen(entry, on_date)
    return elapsed is not None and elapsed >= effective_interval(entry, params)


def novelty_share(contested: Sequence[SelectPool], params: SchedulerParams) -> float:
    """Realized novelty share over the trailing ``H`` **contested** slots.

    Measuring over contested slots only (rather than all slots or all days) is
    what makes the controller immune to supply droughts and forced drains, and
    what makes EVALS M3 compute literally the same statistic (SCOPE.md D3/D5).
    """
    window = list(contested[-params.H :])
    if not window:
        return 0.0
    denominator = params.H if len(window) >= params.H else len(window)
    return sum(1 for pool in window if pool is SelectPool.NOVELTY) / denominator


# --------------------------------------------------------------------------
# FR-7 — daily selection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotDecision:
    slot: int
    entry_id: str
    select_pool: SelectPool


@dataclass(frozen=True)
class SlotTelemetry:
    """Pool sizes at a slot's decision point (what EVALS M1g/M1h check against)."""

    slot: int
    eligible: int
    novelty_pool: int
    review_pool: int
    rescue_candidates: int
    forced_candidates: int
    sigma: float


def _active(
    entries: Sequence[SchedulerEntry], on_date: dt.date, picked: set[str]
) -> list[SchedulerEntry]:
    return [
        e
        for e in entries
        if e.status is EntryStatus.ACTIVE
        and e.entry_id not in picked
        and e.last_surfaced_on != on_date
    ]


def _eligible(
    candidates: Sequence[SchedulerEntry], on_date: dt.date, params: SchedulerParams
) -> list[SchedulerEntry]:
    """Cooldown filter: never-seen entries are always eligible (FR-7)."""
    out = []
    for entry in candidates:
        elapsed = days_since_seen(entry, on_date)
        if elapsed is None or elapsed >= params.W:
            out.append(entry)
    return out


def _pick(
    candidates: Sequence[SchedulerEntry],
    priority: Callable[[SchedulerEntry], float],
    seed: int,
    on_date: dt.date,
    slot: int,
    params: SchedulerParams,
) -> SchedulerEntry:
    """Argmax of ``priority + jitter``; exact ties break by ascending entry id."""
    return min(
        candidates,
        key=lambda e: (
            -(priority(e) + jitter(params.jitter, seed, on_date, slot, e.entry_id)),
            e.entry_id,
        ),
    )


def decide_slot(
    entries: Sequence[SchedulerEntry],
    contested: Sequence[SelectPool],
    on_date: dt.date,
    seed: int,
    params: SchedulerParams,
    slot: int,
    picked: set[str],
) -> tuple[SlotDecision | None, SlotTelemetry]:
    """One slot of FR-7, branches in strict precedence order."""
    active = _active(entries, on_date, picked)
    eligible = _eligible(active, on_date, params)
    never = [e for e in eligible if not e.seen]
    seen = [e for e in eligible if e.seen]
    rescue = [e for e in seen if e.pinned and (days_since_seen(e, on_date) or 0) >= params.P_rescue]
    forced = [e for e in never if age_days(e, on_date) > params.S]
    due = [e for e in seen if is_due(e, on_date, params)]
    sigma = novelty_share(contested, params)
    telemetry = SlotTelemetry(
        slot=slot,
        eligible=len(eligible),
        novelty_pool=len(never),
        review_pool=len(due),
        rescue_candidates=len(rescue),
        forced_candidates=len(forced),
        sigma=sigma,
    )

    def decision(entry: SchedulerEntry, pool: SelectPool) -> tuple[SlotDecision, SlotTelemetry]:
        return SlotDecision(slot=slot, entry_id=entry.entry_id, select_pool=pool), telemetry

    # 1. pinned rescue — delivers the US-5 promise rather than hoping for it.
    if rescue:
        chosen = _pick(
            rescue, lambda e: float(days_since_seen(e, on_date) or 0), seed, on_date, slot, params
        )
        return decision(chosen, SelectPool.PINNED_RESCUE)

    novelty_priority_of = _novelty_key(on_date, params)

    # 2. forced novelty — starvation aging; the only branch that may exceed
    #    the novelty quota, and what bounds bulk-import drain.
    if forced:
        chosen = _pick(never, novelty_priority_of, seed, on_date, slot, params)
        return decision(chosen, SelectPool.FORCED_NOVELTY)

    overdue_of = _overdue_key(on_date, params)

    # 3. contested — both pools non-empty, so the quota rule chooses freely.
    if never and due:
        if sigma < params.rho:
            return decision(
                _pick(never, novelty_priority_of, seed, on_date, slot, params), SelectPool.NOVELTY
            )
        return decision(_pick(due, overdue_of, seed, on_date, slot, params), SelectPool.REVIEW)

    # 4. exactly one pool has supply.
    if never:
        return decision(
            _pick(never, novelty_priority_of, seed, on_date, slot, params),
            SelectPool.NOVELTY_ONLY,
        )
    if due:
        return decision(_pick(due, overdue_of, seed, on_date, slot, params), SelectPool.REVIEW_ONLY)

    # 5. not due — the only path that surfaces an entry before its interval.
    if seen:
        return decision(_pick(seen, overdue_of, seed, on_date, slot, params), SelectPool.NOT_DUE)

    # 6. tiny-library fallback — least recently seen, flagged for what it is.
    if active:
        chosen = min(active, key=lambda e: (e.last_surfaced_on or _EPOCH, e.entry_id))
        return decision(chosen, SelectPool.RELAXED)

    return None, telemetry


def _novelty_key(on_date: dt.date, params: SchedulerParams) -> Callable[[SchedulerEntry], float]:
    return lambda e: novelty_priority(age_days(e, on_date), params)


def _overdue_key(on_date: dt.date, params: SchedulerParams) -> Callable[[SchedulerEntry], float]:
    return lambda e: overdue_ratio(e, on_date, params)


def select_daily_with_telemetry(
    entries: Sequence[SchedulerEntry],
    contested_tail: Sequence[SelectPool],
    on_date: dt.date,
    seed: int,
    params: SchedulerParams,
    k: int | None = None,
) -> list[tuple[SlotDecision, SlotTelemetry]]:
    """Fill the day's slots sequentially; earlier slots constrain later ones."""
    slots = params.k if k is None else k
    contested = list(contested_tail)
    picked: set[str] = set()
    out: list[tuple[SlotDecision, SlotTelemetry]] = []
    for slot in range(slots):
        found, telemetry = decide_slot(entries, contested, on_date, seed, params, slot, picked)
        if found is None:
            break
        out.append((found, telemetry))
        picked.add(found.entry_id)
        if found.select_pool in CONTESTED_POOLS:
            contested.append(found.select_pool)
    return out


def select_daily(
    entries: Sequence[SchedulerEntry],
    contested_tail: Sequence[SelectPool],
    on_date: dt.date,
    seed: int,
    params: SchedulerParams,
    k: int | None = None,
) -> list[SlotDecision]:
    """FR-7: today's card set, one ``SlotDecision`` per filled slot."""
    return [
        decision
        for decision, _ in select_daily_with_telemetry(
            entries, contested_tail, on_date, seed, params, k
        )
    ]


# --------------------------------------------------------------------------
# FR-8 — extra draws
# --------------------------------------------------------------------------


def select_extra(
    entries: Sequence[SchedulerEntry],
    on_date: dt.date,
    seed: int,
    params: SchedulerParams,
    allowed_ids: Iterable[str] | None = None,
) -> SlotDecision | None:
    """Serve one on-demand card from the (optionally filtered) library.

    Pick order per FR-8: never-seen by ``n(e)``, else due reviews by ``O(e)``,
    else eligible not-due reviews by ``O(e)``, else the relaxed fallback within
    the filter.  The result is always stamped ``select_pool = extra`` — draws
    are exposures in the fold but are excluded from sigma by construction.
    """
    candidates = _active(entries, on_date, set())
    if allowed_ids is not None:
        allowed = set(allowed_ids)
        candidates = [e for e in candidates if e.entry_id in allowed]
    if not candidates:
        return None
    eligible = _eligible(candidates, on_date, params)
    never = [e for e in eligible if not e.seen]
    seen = [e for e in eligible if e.seen]
    due = [e for e in seen if is_due(e, on_date, params)]

    chosen: SchedulerEntry | None = None
    if never:
        chosen = _pick(never, _novelty_key(on_date, params), seed, on_date, 0, params)
    elif due:
        chosen = _pick(due, _overdue_key(on_date, params), seed, on_date, 0, params)
    elif seen:
        chosen = _pick(seen, _overdue_key(on_date, params), seed, on_date, 0, params)
    else:
        chosen = min(candidates, key=lambda e: (e.last_surfaced_on or _EPOCH, e.entry_id))
    return SlotDecision(slot=0, entry_id=chosen.entry_id, select_pool=SelectPool.EXTRA)
