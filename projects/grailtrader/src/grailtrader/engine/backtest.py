"""FR-10: the backtest harness — leak-free weekly replay, baselines and placebo.

At each week ``t`` the advisor runs with **strictly-prior data only** (index
points at weeks ``<= t``, events with ``occurred_on <= end of week t``). Entry is
week ``t + 1`` ("you act over the following week"), asserted by
``BacktestResult``'s own validator, and the realized return is measured at the
advice's own horizon::

    R = I_ref(t + 1 + H*) / I_ref(t + 1) - 1

``I_ref`` is the planted truth index in eval runs and the recovered index in live
runs. ``hit`` is computed for every directional *candidate*, so calibration can
be scored below the action threshold.

Placebo mode displaces every event's week by a seeded uniform ±[26, 52] weeks,
redrawing while the displaced active window overlaps the true active window of
**any** event whose target strata intersect the displaced event's (REVIEW D13).
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from ..ids import backtest_run_id
from ..models import (
    AdviceAction,
    AdviceDecision,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    EventStatus,
    ExclusionReason,
    FashionEvent,
    Garment,
    HoldReason,
    ImpactDirection,
    IndexPoint,
)
from ..weeks import add_weeks, parse_date, week_key, week_range, week_start, weeks_between
from .advisor import advise_garment
from .context import EngineContext
from .events import make_event, target_strata
from .impact import legs_for_event, retirement_age
from .index import IndexView
from .strata import is_prefix

__all__ = [
    "ReferenceIndex",
    "displace_events",
    "run_backtest",
]

#: Placebo displacement magnitudes, in weeks (SCOPE FR-10 / EVALS M4).
PLACEBO_MIN_WEEKS = 26
PLACEBO_MAX_WEEKS = 52
#: A buy counts as a "phase-in" buy when its youngest driver is this recent.
PHASE_IN_MAX_AGE_WEEKS = 3
#: Baseline grading horizons (theta-momentum reads a 4-week trend; event-naive buys 12 weeks).
MOMENTUM_LOOKBACK_WEEKS = 4
MOMENTUM_HORIZON_WEEKS = 4
EVENT_NAIVE_HORIZON_WEEKS = 12


class ReferenceIndex:
    """``I_ref`` — the series realized returns are measured against."""

    def __init__(
        self,
        series: Mapping[str, Mapping[str, float]],
        *,
        carry_back_weeks: int = 0,
    ) -> None:
        self._series = {stratum: dict(points) for stratum, points in series.items()}
        self._weeks = {stratum: sorted(points) for stratum, points in self._series.items()}
        self._carry_back_weeks = carry_back_weeks

    @classmethod
    def from_index(cls, index: IndexView, *, carry_back_weeks: int) -> ReferenceIndex:
        """The recovered index as a reference (live runs)."""
        series: dict[str, dict[str, float]] = {}
        for stratum in index.strata:
            series[stratum] = {p.week: p.index_value for p in index.points(stratum)}
        return cls(series, carry_back_weeks=carry_back_weeks)

    @classmethod
    def from_truth(cls, truth: Mapping[str, Mapping[str, float]]) -> ReferenceIndex:
        """A planted truth index as a reference (eval runs); exact weeks, no carry-back."""
        return cls(truth, carry_back_weeks=0)

    def value(self, stratum: str, week: str) -> float | None:
        points = self._series.get(stratum)
        if not points:
            return None
        exact = points.get(week)
        if exact is not None:
            return exact
        if self._carry_back_weeks <= 0:
            return None
        best: str | None = None
        for candidate in self._weeks[stratum]:
            if candidate <= week:
                best = candidate
            else:
                break
        if best is None or weeks_between(best, week) > self._carry_back_weeks:
            return None
        return points[best]


@dataclass
class _Tally:
    n: int = 0
    hits: int = 0
    realized: list[float] = field(default_factory=list)

    def add(self, realized: float, hit: bool) -> None:
        self.n += 1
        self.hits += int(hit)
        self.realized.append(realized)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def mean_realized(self) -> float:
        return sum(self.realized) / len(self.realized) if self.realized else 0.0


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _week_index(week: str) -> int:
    return week_start(week).toordinal() // 7


def _targets_intersect(a: Sequence[str], b: Sequence[str]) -> bool:
    return any(is_prefix(x, y) or is_prefix(y, x) for x in a for y in b)


def displace_events(
    events: Sequence[FashionEvent],
    *,
    seed: int,
    ctx: EngineContext,
    span: tuple[str, str] | None = None,
) -> list[FashionEvent]:
    """Placebo displacement (FR-10).

    Every event's week moves by a seeded uniform ±[26, 52] weeks. A draw is
    redrawn while the displaced active window overlaps the *true* active window
    of any event whose target strata intersect the displaced event's target
    strata — so placebo decisions cannot ride real planted moves.

    ``span`` is the observable week range (first week that can carry a baseline,
    last replayed week). Offsets that would push an event outside it are not
    drawn: a displaced event that lands before the index exists or after the
    replay ends produces no decisions at all, which would silently shrink the
    placebo population instead of testing it (EVALS M0e requires the placebo runs
    to be as busy as the real one). If every offset leaves the span the full set
    is used, so behaviour without ``span`` is unchanged.
    """
    rng = random.Random(seed)
    all_offsets = [
        sign * magnitude
        for magnitude in range(PLACEBO_MIN_WEEKS, PLACEBO_MAX_WEEKS + 1)
        for sign in (-1, 1)
    ]
    bounds = (_week_index(span[0]), _week_index(span[1])) if span is not None else None

    spans: dict[str, float] = {}
    targets: dict[str, tuple[str, ...]] = {}
    for event in events:
        legs = legs_for_event(event, ctx.gazetteer, ctx.priors)
        spans[event.id] = max((retirement_age(leg, ctx.settings) for leg in legs), default=0.0)
        targets[event.id] = tuple(t.stratum for t in target_strata(event, ctx.gazetteer))

    displaced: list[FashionEvent] = []
    for event in sorted(events, key=lambda e: (e.week, e.id)):
        start = _week_index(event.week)
        width = spans[event.id]
        offsets = all_offsets
        if bounds is not None:
            inside = [o for o in all_offsets if bounds[0] <= start + o <= bounds[1]]
            offsets = inside or all_offsets
        blockers = [
            (_week_index(other.week), _week_index(other.week) + spans[other.id])
            for other in events
            if _targets_intersect(targets[event.id], targets[other.id])
        ]

        chosen: int | None = None
        for _ in range(200):
            candidate = rng.choice(offsets)
            if _window_is_clear(start + candidate, width, blockers):
                chosen = candidate
                break
        if chosen is None:
            for candidate in sorted(offsets, key=lambda o: (abs(o), o)):
                if _window_is_clear(start + candidate, width, blockers):
                    chosen = candidate
                    break
        if chosen is None:
            # Every offset overlaps something: take the one that overlaps least,
            # rather than a uniform draw that may sit squarely on a planted move.
            chosen = min(
                sorted(offsets), key=lambda o: (_overlap(start + o, width, blockers), abs(o), o)
            )

        occurred = parse_date(event.occurred_on) + timedelta(weeks=chosen)
        displaced.append(
            make_event(
                event_type=event.event_type,
                brand_id=event.brand_id,
                occurred_on=occurred.isoformat(),
                source=event.source,
                source_refs=event.source_refs,
                status=event.status,
                era_id=event.era_id,
                attributes=event.attributes,
                notes=event.notes,
            )
        )
    return displaced


def _window_is_clear(low: int, span: float, windows: Sequence[tuple[int, float]]) -> bool:
    """True when ``[low, low + span]`` overlaps none of ``windows``."""
    high = low + span
    return all(high < other_low or low > other_high for other_low, other_high in windows)


def _overlap(low: int, span: float, windows: Sequence[tuple[int, float]]) -> float:
    """Total weeks of overlap between ``[low, low + span]`` and ``windows``."""
    high = low + span
    return sum(
        max(0.0, min(high, other_high) - max(low, other_low)) for other_low, other_high in windows
    )


def run_backtest(
    *,
    params: BacktestParams,
    garments: Sequence[Garment],
    events: Sequence[FashionEvent],
    index: IndexView,
    reference: ReferenceIndex,
    ctx: EngineContext,
    as_of: str,
) -> tuple[BacktestRun, list[BacktestResult]]:
    """Replay ``params.start_week`` .. ``params.end_week`` week by week (FR-10)."""
    run_id = backtest_run_id(params.model_dump(mode="json"), as_of)
    active_garments = [
        g for g in garments if g.deleted_at is None and g.status.value in {"owned", "watching"}
    ]
    confirmed = [e for e in events if e.status is EventStatus.CONFIRMED]
    if params.placebo_seed is not None:
        earliest = min((point.week for point in index.all_points()), default=params.start_week)
        confirmed = displace_events(
            confirmed,
            seed=params.placebo_seed,
            ctx=ctx,
            span=(add_weeks(earliest, 1), params.end_week),
        )

    results: list[BacktestResult] = []
    candidates = _Tally()
    actionable = _Tally()
    per_action: dict[AdviceAction, _Tally] = {
        AdviceAction.BUY: _Tally(),
        AdviceAction.SELL: _Tally(),
    }
    per_horizon: dict[int, dict[AdviceAction, _Tally]] = {
        horizon: {AdviceAction.BUY: _Tally(), AdviceAction.SELL: _Tally()}
        for horizon in ctx.settings.horizons_weeks
    }
    buckets: dict[str, _Tally] = {"lo": _Tally(), "mid": _Tally(), "hi": _Tally()}
    bucket_conf: dict[str, list[float]] = {"lo": [], "mid": [], "hi": []}
    by_event_type: dict[str, _Tally] = {}
    excluded_counts: dict[str, int] = {reason.value: 0 for reason in ExclusionReason}
    regimes = {"sell_into_decay": 0, "phase_in_buy": 0}
    baselines = {
        "always_hold": _Tally(),
        "theta_momentum": _Tally(),
        "event_naive": _Tally(),
    }
    baseline_actions: dict[str, dict[AdviceAction, _Tally]] = {
        name: {AdviceAction.BUY: _Tally(), AdviceAction.SELL: _Tally()}
        for name in ("theta_momentum", "event_naive")
    }
    lo_edge, hi_edge = ctx.calibration.bucket_edges

    for week in week_range(params.start_week, params.end_week):
        sliced = index.restrict(week)
        visible = [event for event in confirmed if event.week <= week]
        entry_week = add_weeks(week, 1)
        for garment in active_garments:
            decision = advise_garment(
                garment, as_of_week=week, index=sliced, events=visible, ctx=ctx
            )
            realized, excluded = _grade(
                decision, entry_week=entry_week, end_week=params.end_week, reference=reference
            )
            hit: bool | None = None
            if realized is not None and decision.is_candidate:
                assert decision.expected_return is not None
                hit = _sign(realized) == _sign(decision.expected_return)
            results.append(
                BacktestResult(
                    run_id=run_id,
                    garment_id=garment.id,
                    week=week,
                    action=decision.action,
                    is_candidate=decision.is_candidate,
                    horizon_weeks=decision.horizon_weeks,
                    confidence=decision.confidence,
                    expected_return=decision.expected_return,
                    driver_event_ids=decision.driver_event_ids,
                    entry_week=entry_week,
                    realized_return=realized,
                    hit=hit,
                    excluded_reason=excluded,
                )
            )
            if excluded is not None:
                excluded_counts[excluded.value] += 1
            baselines["always_hold"].n += 1

            if realized is not None and decision.is_candidate:
                assert hit is not None
                candidates.add(realized, hit)
                bucket = _bucket_of(decision.confidence, lo_edge, hi_edge)
                buckets[bucket].add(realized, hit)
                bucket_conf[bucket].append(decision.confidence or 0.0)
                for event_type in {d.event_type.value for d in decision.drivers}:
                    by_event_type.setdefault(event_type, _Tally()).add(realized, hit)
                if decision.action in per_action:
                    actionable.add(realized, hit)
                    per_action[decision.action].add(realized, hit)
                    per_horizon[decision.horizon_weeks][decision.action].add(realized, hit)
                    _count_regimes(decision, regimes)

            _grade_baselines(
                decision,
                garment=garment,
                week=week,
                entry_week=entry_week,
                end_week=params.end_week,
                sliced=sliced,
                visible=visible,
                reference=reference,
                ctx=ctx,
                baselines=baselines,
                baseline_actions=baseline_actions,
            )

    aggregates = {
        "n_decisions": len(results),
        "n_candidates": candidates.n,
        "n_actionable": actionable.n,
        "excluded": excluded_counts,
        "hit_rate": actionable.hit_rate,
        "mean_realized": {
            "buy": per_action[AdviceAction.BUY].mean_realized,
            "sell": per_action[AdviceAction.SELL].mean_realized,
        },
        "spread_buy_minus_sell": _spread(per_action),
        "spread_by_horizon": {
            str(horizon): _spread(per_horizon[horizon]) for horizon in per_horizon
        },
        "buckets": {
            name: {
                "n": buckets[name].n,
                "hit_rate": buckets[name].hit_rate,
                "mean_conf": (
                    sum(bucket_conf[name]) / len(bucket_conf[name]) if bucket_conf[name] else 0.0
                ),
            }
            for name in ("lo", "mid", "hi")
        },
        "by_event_type": {
            event_type: {"n": tally.n, "hit_rate": tally.hit_rate}
            for event_type, tally in sorted(by_event_type.items())
        },
        "regimes": regimes,
        "baselines": {
            "always_hold": {
                "n": baselines["always_hold"].n,
                "hit_rate": None,
                "mean_realized": 0.0,
                "spread": 0.0,
            },
            **{
                name: {
                    "n": baselines[name].n,
                    "hit_rate": baselines[name].hit_rate,
                    "mean_realized": baselines[name].mean_realized,
                    "spread": _spread(baseline_actions[name]),
                }
                for name in ("theta_momentum", "event_naive")
            },
        },
    }
    run = BacktestRun(id=run_id, params=params, as_of=as_of, aggregates=aggregates)
    return run, results


def _bucket_of(confidence: float | None, lo_edge: float, hi_edge: float) -> str:
    value = confidence or 0.0
    if value < lo_edge:
        return "lo"
    if value < hi_edge:
        return "mid"
    return "hi"


def _spread(per_action: Mapping[AdviceAction, _Tally]) -> float:
    buys = per_action[AdviceAction.BUY]
    sells = per_action[AdviceAction.SELL]
    if not buys.n or not sells.n:
        return 0.0
    return buys.mean_realized - sells.mean_realized


def _count_regimes(decision: AdviceDecision, regimes: dict[str, int]) -> None:
    if (
        decision.action is AdviceAction.SELL
        and decision.drivers
        and all(d.direction is ImpactDirection.BULLISH for d in decision.drivers)
    ):
        regimes["sell_into_decay"] += 1
    if (
        decision.action is AdviceAction.BUY
        and decision.drivers
        and min(d.age_weeks for d in decision.drivers) <= PHASE_IN_MAX_AGE_WEEKS
    ):
        regimes["phase_in_buy"] += 1


def _grade(
    decision: AdviceDecision,
    *,
    entry_week: str,
    end_week: str,
    reference: ReferenceIndex,
) -> tuple[float | None, ExclusionReason | None]:
    if not decision.stratum_selected:
        reason = (
            ExclusionReason.NO_INDEX
            if decision.hold_reason is HoldReason.NO_INDEX
            else ExclusionReason.STALE_STRATUM
        )
        return None, reason
    target_week = add_weeks(entry_week, decision.horizon_weeks)
    if target_week > end_week:
        return None, ExclusionReason.OUT_OF_WINDOW
    start = reference.value(decision.stratum_id, entry_week)
    finish = reference.value(decision.stratum_id, target_week)
    if start is None or finish is None or start <= 0:
        return None, ExclusionReason.INSUFFICIENT_FUTURE_INDEX
    return finish / start - 1.0, None


def _grade_baselines(
    decision: AdviceDecision,
    *,
    garment: Garment,
    week: str,
    entry_week: str,
    end_week: str,
    sliced: IndexView,
    visible: Sequence[FashionEvent],
    reference: ReferenceIndex,
    ctx: EngineContext,
    baselines: Mapping[str, _Tally],
    baseline_actions: Mapping[str, dict[AdviceAction, _Tally]],
) -> None:
    """Score the three naive baselines over the same weeks and garments (FR-10)."""
    if not decision.stratum_selected:
        return
    stratum = decision.stratum_id

    momentum = _momentum_action(sliced, stratum, week, ctx)
    if momentum is not None:
        _score_baseline(
            "theta_momentum",
            momentum,
            MOMENTUM_HORIZON_WEEKS,
            entry_week,
            end_week,
            stratum,
            reference,
            baselines,
            baseline_actions,
        )

    if _event_naive_buys(garment, visible, week, ctx):
        _score_baseline(
            "event_naive",
            AdviceAction.BUY,
            EVENT_NAIVE_HORIZON_WEEKS,
            entry_week,
            end_week,
            stratum,
            reference,
            baselines,
            baseline_actions,
        )


def _momentum_action(
    sliced: IndexView, stratum: str, week: str, ctx: EngineContext
) -> AdviceAction | None:
    now = sliced.carried(stratum, week)
    before = sliced.carried(stratum, add_weeks(week, -MOMENTUM_LOOKBACK_WEEKS))
    if now is None or before is None:
        return None
    trailing = now[0].index_value / before[0].index_value - 1.0
    if trailing >= ctx.settings.theta_buy:
        return AdviceAction.BUY
    if trailing <= -ctx.settings.theta_sell:
        return AdviceAction.SELL
    return None


def _event_naive_buys(
    garment: Garment, visible: Sequence[FashionEvent], week: str, ctx: EngineContext
) -> bool:
    leaf = garment.stratum_path
    for event in visible:
        age = weeks_between(event.week, week)
        if 0 <= age < EVENT_NAIVE_HORIZON_WEEKS and any(
            is_prefix(target.stratum, leaf) for target in target_strata(event, ctx.gazetteer)
        ):
            return True
    return False


def _score_baseline(
    name: str,
    action: AdviceAction,
    horizon: int,
    entry_week: str,
    end_week: str,
    stratum: str,
    reference: ReferenceIndex,
    baselines: Mapping[str, _Tally],
    baseline_actions: Mapping[str, dict[AdviceAction, _Tally]],
) -> None:
    target_week = add_weeks(entry_week, horizon)
    if target_week > end_week:
        return
    start = reference.value(stratum, entry_week)
    finish = reference.value(stratum, target_week)
    if start is None or finish is None or start <= 0:
        return
    realized = finish / start - 1.0
    expected_sign = 1 if action is AdviceAction.BUY else -1
    hit = _sign(realized) == expected_sign
    baselines[name].add(realized, hit)
    baseline_actions[name][action].add(realized, hit)


def truth_series_from_points(points: Iterable[IndexPoint]) -> dict[str, dict[str, float]]:
    """Helper for eval fixtures: index points -> the mapping ``ReferenceIndex`` wants."""
    series: dict[str, dict[str, float]] = {}
    for point in points:
        series.setdefault(point.stratum_id, {})[week_key(point.week)] = point.index_value
    return series
