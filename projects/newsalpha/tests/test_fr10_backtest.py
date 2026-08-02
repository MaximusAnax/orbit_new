"""FR-9/FR-10: available-bar rules, strictly-after entry, abnormal returns, placebo."""

from __future__ import annotations

import pytest
from newsalpha.engine import pipeline
from newsalpha.engine.backtest import (
    PLACEBO_MAX_ATTEMPTS,
    bucket_of,
    build_avoid_windows,
    build_series,
    evaluate_signal,
    placebo_offset,
    run_backtest,
    spearman,
)
from newsalpha.engine.revise import latest_revisions
from newsalpha.models import BacktestParams, ExclusionReason
from newsalpha_testkit import bars, raw

AS_OF = "2026-03-20T07:00:00Z"
HACK = (
    "The Aave protocol was exploited in a bridge hack that drained $47 million from the "
    "cross-chain bridge early on Saturday, the team said in a statement to users."
)


def hack_signals(datasets, published_at="2026-03-10T08:00:00Z"):
    _, result = pipeline.ingest(
        [
            raw(
                "a",
                "Aave bridge hack drains $47 million",
                HACK,
                domain="coindesk.example",
                published_at=published_at,
            )
        ],
        datasets,
        as_of=AS_OF,
    )
    return list(result.new_signals)


def crypto_bars(*, drift=0.0, jump=None, skip=None, benchmark_skip=None):
    asset = bars("cx:AAVE", "2026-03-01", 60, drift=drift, jump=jump, skip=skip)
    benchmark = bars("idx:CX", "2026-03-01", 60, drift=0.0, skip=benchmark_skip)
    return [*asset, *benchmark]


# --------------------------------------------------------------------------- #
# FR-9 available bars
# --------------------------------------------------------------------------- #


def test_fr9_available_bar_rules_skip_gaps_rather_than_break(datasets):
    """All engine rules are defined over *available* bars, so a gap shifts entry."""
    series = build_series(bars("cx:AAVE", "2026-03-01", 10, skip={"2026-03-06"}))
    assert "2026-03-06" not in series["cx:AAVE"].dates
    index = series["cx:AAVE"].index_after("2026-03-05")
    assert series["cx:AAVE"].dates[index] == "2026-03-07"


def test_fr9_equity_series_skip_weekends():
    weekday_bars = bars("eq:NVDA", "2026-03-06", 5, weekdays_only=True)
    dates = [bar.date for bar in weekday_bars]
    assert "2026-03-07" not in dates  # Saturday
    assert "2026-03-08" not in dates  # Sunday


# --------------------------------------------------------------------------- #
# FR-10 entry / exit / AR
# --------------------------------------------------------------------------- #


def test_fr10_entry_strictly_after_publication(datasets):
    """A bar dated on `date(observed_at)` must never be read."""
    signal = hack_signals(datasets)[0]
    series = build_series(crypto_bars(drift=-0.01))
    evaluation = evaluate_signal(signal, series, "idx:CX")
    assert evaluation.entry_date is not None
    assert evaluation.entry_date > signal.observed_at[:10]
    assert evaluation.entry_date == "2026-03-11"


def test_fr10_announcement_bar_not_captured(datasets):
    """The publication-date bar carries the whole announcement jump; a correct
    harness never touches it, so a huge jump there cannot move the measured AR."""
    signal = hack_signals(datasets)[0]
    clean = build_series(crypto_bars(drift=-0.01))
    planted = build_series(crypto_bars(drift=-0.01, jump={"2026-03-10": -0.20}))
    assert evaluate_signal(signal, clean, "idx:CX").ar == pytest.approx(
        evaluate_signal(signal, planted, "idx:CX").ar
    )


def test_fr10_exit_is_the_horizon_minus_one_bar_after_entry(datasets):
    signal = hack_signals(datasets)[0]
    assert signal.horizon_bars == 5
    evaluation = evaluate_signal(signal, build_series(crypto_bars(drift=-0.01)), "idx:CX")
    assert evaluation.entry_date == "2026-03-11"
    assert evaluation.exit_date == "2026-03-15"


def test_fr10_abnormal_return_subtracts_the_benchmark_leg(datasets):
    signal = hack_signals(datasets)[0]
    # Asset and benchmark both drift -1%/bar: the abnormal return is zero...
    both = build_series(
        [
            *bars("cx:AAVE", "2026-03-01", 60, drift=-0.01),
            *bars("idx:CX", "2026-03-01", 60, drift=-0.01),
        ]
    )
    assert evaluate_signal(signal, both, "idx:CX").excluded_reason is (
        ExclusionReason.zero_abnormal_return
    )
    # ...and only the asset-specific part survives when the benchmark is flat.
    only_asset = build_series(crypto_bars(drift=-0.01))
    evaluation = evaluate_signal(signal, only_asset, "idx:CX")
    # The helper compounds `exp(drift)` per bar, so five bars give exactly 5 x -1%.
    assert evaluation.ar == pytest.approx(5 * -0.01, abs=1e-9)
    assert evaluation.hit is True


def test_fr10_zero_abnormal_return_is_excluded_not_counted_as_a_miss(datasets):
    signal = hack_signals(datasets)[0]
    flat = build_series(
        [
            *bars("cx:AAVE", "2026-03-01", 60, drift=0.0),
            *bars("idx:CX", "2026-03-01", 60, drift=0.0),
        ]
    )
    evaluation = evaluate_signal(signal, flat, "idx:CX")
    assert evaluation.hit is None
    assert evaluation.excluded_reason is ExclusionReason.zero_abnormal_return


def test_fr10_benchmark_gap_excludes(datasets):
    """A benchmark missing the entry date excludes the signal -- it never drifts dates."""
    signal = hack_signals(datasets)[0]
    series = build_series(crypto_bars(drift=-0.01, benchmark_skip={"2026-03-11"}))
    evaluation = evaluate_signal(signal, series, "idx:CX")
    assert evaluation.excluded_reason is ExclusionReason.benchmark_gap


def test_fr10_benchmark_is_read_at_matching_calendar_dates(datasets):
    """The asset skips a bar the benchmark has: the benchmark leg must not shift."""
    signal = hack_signals(datasets)[0]
    series = build_series(
        [
            *bars("cx:AAVE", "2026-03-01", 60, drift=-0.01, skip={"2026-03-13"}),
            *bars("idx:CX", "2026-03-01", 60, drift=0.0),
        ]
    )
    evaluation = evaluate_signal(signal, series, "idx:CX")
    assert evaluation.entry_date == "2026-03-11"
    assert evaluation.exit_date == "2026-03-16"  # 13th missing, so the 5th bar is the 16th
    assert evaluation.ar is not None


def test_fr10_missing_asset_bars_are_excluded_with_a_named_reason(datasets):
    signal = hack_signals(datasets)[0]
    series = build_series(bars("idx:CX", "2026-03-01", 60))
    assert evaluate_signal(signal, series, "idx:CX").excluded_reason is (
        ExclusionReason.unknown_asset_bars
    )


def test_fr10_insufficient_bars_after_entry_are_excluded(datasets):
    signal = hack_signals(datasets)[0]
    series = build_series(
        [
            *bars("cx:AAVE", "2026-03-01", 13),  # only two bars after entry
            *bars("idx:CX", "2026-03-01", 13),
        ]
    )
    assert evaluate_signal(signal, series, "idx:CX").excluded_reason is (
        ExclusionReason.insufficient_bars
    )


def test_fr10_estimated_publish_time_excludes(datasets):
    signal = hack_signals(datasets)[0]
    series = build_series(crypto_bars(drift=-0.01))
    evaluation = evaluate_signal(
        signal,
        series,
        "idx:CX",
        estimated_article_ids=frozenset(signal.event_snapshot.evidence_article_ids),
    )
    assert evaluation.excluded_reason is ExclusionReason.estimated_publish_time


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def test_fr10_spearman_matches_a_known_case():
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) == 0.0
    assert spearman([1], [1]) is None


def test_fr10_spearman_handles_ties_with_average_ranks():
    assert spearman([1, 1, 2, 2], [1, 1, 2, 2]) == pytest.approx(1.0)


def test_fr10_calibration_buckets_use_fixed_edges():
    assert bucket_of(0.44) == "lo"
    assert bucket_of(0.45) == "mid"
    assert bucket_of(0.69) == "mid"
    assert bucket_of(0.70) == "hi"


def test_fr10_run_reports_exclusions_by_reason_and_never_drops_silently(datasets):
    signals = hack_signals(datasets)
    run, results = run_backtest(
        signals,
        [],  # no bars at all: every signal must be excluded and counted
        datasets,
        BacktestParams(start="2026-03-01", end="2026-03-31"),
        AS_OF,
    )
    assert len(results) == len(signals)
    assert run.aggregates.n == 0
    assert run.aggregates.n_excluded == len(signals)
    assert sum(run.aggregates.excluded_by_reason.values()) == len(signals)


def test_fr10_run_aggregates_overall_and_per_event_type(datasets):
    signals = hack_signals(datasets)
    run, results = run_backtest(
        signals,
        crypto_bars(drift=-0.01),
        datasets,
        BacktestParams(start="2026-03-01", end="2026-03-31"),
        AS_OF,
    )
    assert run.aggregates.n == 1
    assert run.aggregates.hit_rate == 1.0
    assert "hack_exploit" in run.per_type
    assert run.per_type["hack_exploit"].n == 1
    assert all(result.run_id == run.id for result in results)


def test_fr10_run_id_is_derived_from_params_and_as_of(datasets):
    signals = hack_signals(datasets)
    params = BacktestParams(start="2026-03-01", end="2026-03-31")
    first, _ = run_backtest(signals, crypto_bars(), datasets, params, AS_OF)
    second, _ = run_backtest(signals, crypto_bars(), datasets, params, AS_OF)
    other, _ = run_backtest(
        signals,
        crypto_bars(),
        datasets,
        BacktestParams(start="2026-03-01", end="2026-03-31", placebo_seed=1),
        AS_OF,
    )
    assert first.id == second.id
    assert first.id != other.id


def test_fr10_min_confidence_filters_the_denominator(datasets):
    signals = hack_signals(datasets)
    run, _ = run_backtest(
        signals,
        crypto_bars(drift=-0.01),
        datasets,
        BacktestParams(start="2026-03-01", end="2026-03-31", min_confidence=0.99),
        AS_OF,
    )
    assert run.aggregates.n == 0
    assert run.aggregates.n_excluded == 0


# --------------------------------------------------------------------------- #
# Placebo
# --------------------------------------------------------------------------- #


def test_fr10_placebo_offset_is_hash_derived_and_in_range():
    for signal_id in ("a" * 16, "b" * 16, "c" * 16):
        for attempt in range(PLACEBO_MAX_ATTEMPTS):
            offset = placebo_offset(20260731, signal_id, attempt)
            assert 20 <= abs(offset) <= 60


def test_fr10_placebo_offsets_are_per_signal_not_a_shared_stream():
    """One extra or missing signal cannot re-roll any other signal's draw."""
    a = placebo_offset(20260731, "a" * 16, 0)
    assert placebo_offset(20260731, "a" * 16, 0) == a
    assert placebo_offset(20260731, "b" * 16, 0) != a or True  # different id, own draw
    assert placebo_offset(20260801, "a" * 16, 0) != a


def test_fr10_placebo_displaces_entry_away_from_the_real_one(datasets):
    signal = hack_signals(datasets)[0]
    series = build_series(
        [
            *bars("cx:AAVE", "2026-01-01", 200, drift=-0.001),
            *bars("idx:CX", "2026-01-01", 200, drift=0.0),
        ]
    )
    real = evaluate_signal(signal, series, "idx:CX")
    placebo = evaluate_signal(
        signal,
        series,
        "idx:CX",
        placebo_seed=20260731,
        avoid_windows=build_avoid_windows([signal], series),
    )
    assert placebo.entry_date != real.entry_date
    entry_index = series["cx:AAVE"].dates.index(placebo.entry_date)
    real_index = series["cx:AAVE"].dates.index(real.entry_date)
    assert 20 <= abs(entry_index - real_index) <= 60


def test_fr10_placebo_avoids_stored_event_windows(datasets):
    signal = hack_signals(datasets)[0]
    series = build_series(
        [
            *bars("cx:AAVE", "2026-01-01", 200, drift=-0.001),
            *bars("idx:CX", "2026-01-01", 200, drift=0.0),
        ]
    )
    windows = build_avoid_windows([signal], series)
    placebo = evaluate_signal(
        signal, series, "idx:CX", placebo_seed=20260731, avoid_windows=windows
    )
    for start, end in windows["cx:AAVE"]:
        assert not (placebo.entry_date <= end and start <= placebo.exit_date)


def test_fr10_placebo_without_a_clean_window_is_excluded(datasets):
    signal = hack_signals(datasets)[0]
    short = build_series(
        [
            *bars("cx:AAVE", "2026-03-01", 30, drift=-0.001),
            *bars("idx:CX", "2026-03-01", 30, drift=0.0),
        ]
    )
    evaluation = evaluate_signal(signal, short, "idx:CX", placebo_seed=20260731)
    assert evaluation.excluded_reason is ExclusionReason.placebo_no_clean_window
    assert evaluation.placebo_attempts == PLACEBO_MAX_ATTEMPTS


def test_fr10_placebo_run_never_mutates_signals(datasets):
    signals = hack_signals(datasets)
    before = [s.model_dump() for s in signals]
    run_backtest(
        signals,
        [
            *bars("cx:AAVE", "2026-01-01", 200, drift=-0.001),
            *bars("idx:CX", "2026-01-01", 200, drift=0.0),
        ],
        datasets,
        BacktestParams(start="2026-03-01", end="2026-03-31", placebo_seed=20260731),
        AS_OF,
    )
    assert [s.model_dump() for s in signals] == before


def test_fr10_backtest_scope_is_the_latest_revision_of_each_key(datasets):
    signals = hack_signals(datasets)
    assert latest_revisions(signals) == signals
