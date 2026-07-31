"""FR-10: the leak-free event-study harness (hard part B).

Event-study methodology (MacKinlay 1997) with a market-adjusted model (Brown &
Warner 1985 -- comparable power to the market model on daily data without
estimating beta on short histories):

    d_entry = first available bar for the asset with date > date(signal.observed_at)
    d_exit  = the (h - 1)-th available bar after d_entry, h = signal.horizon_bars
    AR_h    = log(close_a[d_exit] / open_a[d_entry])
            - log(close_b[d_exit] / open_b[d_entry])      # b = asset.benchmark_id

The benchmark leg is read at the **same calendar dates** as the asset leg -- never
at positional offsets into the benchmark's own bar sequence -- and a benchmark
missing either date excludes the signal.  `AR_h == 0` exactly is an explicit
exclusion, never a coin-flip miss.  Entry strictly after `observed_at` is an
asserted invariant: the harness cannot see the announcement bar.

Placebo mode displaces entry by a hash-derived per-signal offset, so one extra or
missing signal cannot re-roll any other signal's draw.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from dataclasses import dataclass

from ..datasets import Datasets
from ..models import (
    Aggregates,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    BucketStats,
    ExclusionReason,
    PriceBar,
    Signal,
    dir_sign,
)
from .normalize import canonical_json, hex16

PLACEBO_MIN_OFFSET = 20
PLACEBO_OFFSET_SPAN = 41  # offsets land in +/-[20, 60] bars
PLACEBO_MAX_ATTEMPTS = 8
AVOID_WINDOW_BARS = 20

BUCKET_EDGES = (0.45, 0.70)
BUCKET_NAMES = ("lo", "mid", "hi")


class LookAheadError(AssertionError):
    """Raised when an entry date is not strictly after the signal's observation."""


@dataclass(frozen=True, slots=True)
class Series:
    """One asset's available bars, indexed by date. Immutable and pre-sorted."""

    asset_id: str
    dates: tuple[str, ...]
    bars: dict[str, PriceBar]

    def index_after(self, date: str) -> int | None:
        """Position of the first available bar strictly after `date`."""
        position = bisect_right(self.dates, date)
        return position if position < len(self.dates) else None

    def at(self, date: str) -> PriceBar | None:
        return self.bars.get(date)


def build_series(bars: list[PriceBar]) -> dict[str, Series]:
    grouped: dict[str, dict[str, PriceBar]] = {}
    for bar in bars:
        grouped.setdefault(bar.asset_id, {})[bar.date] = bar
    return {
        asset_id: Series(asset_id=asset_id, dates=tuple(sorted(by_date)), bars=by_date)
        for asset_id, by_date in grouped.items()
    }


def bucket_of(confidence: float) -> str:
    if confidence < BUCKET_EDGES[0]:
        return "lo"
    if confidence < BUCKET_EDGES[1]:
        return "mid"
    return "hi"


def placebo_offset(placebo_seed: int, signal_id: str, attempt: int) -> int:
    """FR-10's hash-derived displacement: +/-[20, 60] bars, per signal, never sequential."""
    digest = hashlib.sha256(f"{placebo_seed}|{signal_id}|{attempt}".encode()).hexdigest()
    h32 = int(digest[:8], 16)
    magnitude = PLACEBO_MIN_OFFSET + h32 % PLACEBO_OFFSET_SPAN
    return magnitude if (h32 >> 31) & 1 else -magnitude


def build_avoid_windows(
    signals: list[Signal],
    series: dict[str, Series],
) -> dict[str, list[tuple[str, str]]]:
    """`[entry, entry + 20 bars]` windows of every event known to the store, per asset.

    Stated in product terms (SCOPE D-7/finding #8): the placebo re-draw avoids the
    windows of events *stored in the repository* whose signal-bearing assets include
    this asset, so `backtest placebo` behaves identically on live data.
    """
    windows: dict[str, list[tuple[str, str]]] = {}
    for signal in signals:
        asset_series = series.get(signal.asset_id)
        if asset_series is None:
            continue
        start_index = asset_series.index_after(signal.observed_at[:10])
        if start_index is None:
            continue
        end_index = min(start_index + AVOID_WINDOW_BARS, len(asset_series.dates) - 1)
        window = (asset_series.dates[start_index], asset_series.dates[end_index])
        windows.setdefault(signal.asset_id, [])
        if window not in windows[signal.asset_id]:
            windows[signal.asset_id].append(window)
    for value in windows.values():
        value.sort()
    return windows


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One signal's realized entry/exit/AR, or the reason it was excluded."""

    signal_id: str
    entry_date: str | None
    exit_date: str | None
    ar: float | None
    hit: bool | None
    excluded_reason: ExclusionReason | None
    placebo_attempts: int


def evaluate_signal(
    signal: Signal,
    series: dict[str, Series],
    benchmark_id: str | None,
    *,
    placebo_seed: int | None = None,
    avoid_windows: dict[str, list[tuple[str, str]]] | None = None,
    estimated_article_ids: frozenset[str] = frozenset(),
) -> Evaluation:
    """Evaluate one signal revision under FR-10's rules."""
    if estimated_article_ids and set(signal.event_snapshot.evidence_article_ids) & set(
        estimated_article_ids
    ):
        return _excluded(signal, ExclusionReason.estimated_publish_time)

    asset_series = series.get(signal.asset_id)
    if asset_series is None or not asset_series.dates:
        return _excluded(signal, ExclusionReason.unknown_asset_bars)
    if benchmark_id is None or benchmark_id not in series:
        return _excluded(signal, ExclusionReason.unknown_asset_bars)
    benchmark_series = series[benchmark_id]

    observed_date = signal.observed_at[:10]
    entry_index = asset_series.index_after(observed_date)
    if entry_index is None:
        return _excluded(signal, ExclusionReason.insufficient_bars)

    attempts = 0
    if placebo_seed is not None:
        placed = _placebo_indices(
            signal, asset_series, entry_index, placebo_seed, avoid_windows or {}
        )
        if placed is None:
            return _excluded(signal, ExclusionReason.placebo_no_clean_window, attempts=8)
        entry_index, attempts = placed

    exit_index = entry_index + signal.horizon_bars - 1
    if exit_index >= len(asset_series.dates):
        return _excluded(signal, ExclusionReason.insufficient_bars, attempts=attempts)

    entry_date = asset_series.dates[entry_index]
    exit_date = asset_series.dates[exit_index]
    if placebo_seed is None and entry_date <= observed_date:  # pragma: no cover - invariant
        raise LookAheadError(
            f"signal {signal.id}: entry {entry_date} is not strictly after {observed_date}"
        )

    benchmark_entry = benchmark_series.at(entry_date)
    benchmark_exit = benchmark_series.at(exit_date)
    if benchmark_entry is None or benchmark_exit is None:
        return _excluded(signal, ExclusionReason.benchmark_gap, attempts=attempts)

    entry_bar = asset_series.bars[entry_date]
    exit_bar = asset_series.bars[exit_date]
    asset_leg = math.log(exit_bar.close / entry_bar.open)
    benchmark_leg = math.log(benchmark_exit.close / benchmark_entry.open)
    ar = asset_leg - benchmark_leg
    if ar == 0.0:
        return _excluded(signal, ExclusionReason.zero_abnormal_return, attempts=attempts)

    return Evaluation(
        signal_id=signal.id,
        entry_date=entry_date,
        exit_date=exit_date,
        ar=ar,
        hit=(1 if ar > 0 else -1) == dir_sign(signal.direction),
        excluded_reason=None,
        placebo_attempts=attempts,
    )


def _excluded(signal: Signal, reason: ExclusionReason, *, attempts: int = 0) -> Evaluation:
    return Evaluation(
        signal_id=signal.id,
        entry_date=None,
        exit_date=None,
        ar=None,
        hit=None,
        excluded_reason=reason,
        placebo_attempts=attempts,
    )


def _placebo_indices(
    signal: Signal,
    asset_series: Series,
    entry_index: int,
    placebo_seed: int,
    avoid_windows: dict[str, list[tuple[str, str]]],
) -> tuple[int, int] | None:
    """Displace entry, re-drawing while the displaced window is contaminated."""
    windows = avoid_windows.get(signal.asset_id, [])
    for attempt in range(PLACEBO_MAX_ATTEMPTS):
        offset = placebo_offset(placebo_seed, signal.id, attempt)
        candidate = entry_index + offset
        exit_candidate = candidate + signal.horizon_bars - 1
        if candidate < 0 or exit_candidate >= len(asset_series.dates):
            continue
        start = asset_series.dates[candidate]
        end = asset_series.dates[exit_candidate]
        if any(start <= window_end and window_start <= end for window_start, window_end in windows):
            continue
        return candidate, attempt
    return None


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation with average ranks for ties. None when undefined."""
    if len(xs) != len(ys):  # pragma: no cover - callers pass paired lists
        raise ValueError("spearman needs paired inputs")
    if len(xs) < 2:
        return None
    rank_x = _average_ranks(xs)
    rank_y = _average_ranks(ys)
    mean_x = sum(rank_x) / len(rank_x)
    mean_y = sum(rank_y) / len(rank_y)
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rank_x, rank_y, strict=True))
    var_x = sum((a - mean_x) ** 2 for a in rank_x)
    var_y = sum((b - mean_y) ** 2 for b in rank_y)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2 + 1
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def aggregate(
    evaluations: list[Evaluation],
    signals_by_id: dict[str, Signal],
    superseded_keys: set[str],
) -> Aggregates:
    """N, exclusions by reason, hit rate, mean AR, Spearman IC and calibration buckets."""
    included = [e for e in evaluations if e.excluded_reason is None]
    excluded_by_reason: dict[str, int] = {}
    for evaluation in evaluations:
        if evaluation.excluded_reason is not None:
            reason = evaluation.excluded_reason.value
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1

    buckets: dict[str, BucketStats] = {}
    for name in BUCKET_NAMES:
        members = [e for e in included if bucket_of(signals_by_id[e.signal_id].confidence) == name]
        hit_rate = sum(1 for e in members if e.hit) / len(members) if members else None
        buckets[name] = BucketStats(n=len(members), hit=hit_rate)

    hits = [e for e in included if e.hit]
    scores = [signals_by_id[e.signal_id].score for e in included]
    ars = [e.ar for e in included if e.ar is not None]
    return Aggregates(
        n=len(included),
        n_excluded=len(evaluations) - len(included),
        excluded_by_reason=dict(sorted(excluded_by_reason.items())),
        n_superseded=sum(
            1 for e in evaluations if signals_by_id[e.signal_id].signal_key in superseded_keys
        ),
        hit_rate=len(hits) / len(included) if included else None,
        mean_ar=sum(ars) / len(ars) if ars else None,
        ic_spearman=spearman(scores, ars) if included else None,
        buckets=buckets,
    )


def run_backtest(
    signals: list[Signal],
    bars: list[PriceBar],
    datasets: Datasets,
    params: BacktestParams,
    as_of: str,
    *,
    superseded_keys: set[str] | None = None,
    estimated_article_ids: frozenset[str] = frozenset(),
) -> tuple[BacktestRun, list[BacktestResult]]:
    """Evaluate every eligible signal revision and aggregate overall + per event type.

    Superseded signals are **included** -- they were real statements at the time --
    and additionally counted in `aggregates.n_superseded`; excluding them would let
    a denominator dodge inflate the hit rate.
    """
    series = build_series(bars)
    considered = sorted(
        (
            s
            for s in signals
            if s.confidence >= params.min_confidence
            and params.start <= s.event_snapshot.event_date <= params.end
        ),
        key=lambda s: (s.event_snapshot.event_date, s.asset_id, s.id),
    )
    avoid_windows = (
        build_avoid_windows(signals, series) if params.placebo_seed is not None else None
    )

    evaluations: list[Evaluation] = []
    for signal in considered:
        asset = datasets.assets.get(signal.asset_id)
        benchmark_id = asset.benchmark_id if asset is not None else None
        evaluations.append(
            evaluate_signal(
                signal,
                series,
                benchmark_id,
                placebo_seed=params.placebo_seed,
                avoid_windows=avoid_windows,
                estimated_article_ids=estimated_article_ids,
            )
        )

    signals_by_id = {s.id: s for s in considered}
    keys = superseded_keys or set()
    overall = aggregate(evaluations, signals_by_id, keys)
    per_type: dict[str, Aggregates] = {}
    for event_type in sorted({s.event_snapshot.event_type.value for s in considered}):
        subset = [
            e
            for e in evaluations
            if signals_by_id[e.signal_id].event_snapshot.event_type.value == event_type
        ]
        per_type[event_type] = aggregate(subset, signals_by_id, keys)

    run_id = hex16("bt|" + canonical_json(params.model_dump(mode="json")) + "|" + as_of)
    run = BacktestRun(id=run_id, params=params, as_of=as_of, aggregates=overall, per_type=per_type)
    results = [
        BacktestResult(
            run_id=run_id,
            signal_id=e.signal_id,
            entry_date=e.entry_date,
            exit_date=e.exit_date,
            ar=e.ar,
            hit=e.hit,
            excluded_reason=e.excluded_reason,
            placebo_attempts=e.placebo_attempts,
        )
        for e in evaluations
    ]
    return run, results


__all__ = [
    "AVOID_WINDOW_BARS",
    "BUCKET_EDGES",
    "BUCKET_NAMES",
    "PLACEBO_MAX_ATTEMPTS",
    "Evaluation",
    "LookAheadError",
    "Series",
    "aggregate",
    "bucket_of",
    "build_avoid_windows",
    "build_series",
    "evaluate_signal",
    "placebo_offset",
    "run_backtest",
    "spearman",
]
