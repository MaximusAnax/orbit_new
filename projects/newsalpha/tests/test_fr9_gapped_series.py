"""FR-9/FR-10 against the committed `evals/fixtures/gapped/` corpus.

EVALS.md keeps this tiny series out of the main market fixture so N stays constant
across seeds, and uses it to pin two behaviours on *real committed data* rather
than on hand-built bars: an asset series with holes degrades to the next available
bar, and a benchmark missing a bar on an entry date excludes the signal instead of
silently comparing different weeks.
"""

from __future__ import annotations

from datetime import date

import pytest
from newsalpha.adapters.marketdata_fixture import FixtureMarketData
from newsalpha.datasets import DATA_DIR
from newsalpha.engine.backtest import build_series, evaluate_signal
from newsalpha.models import (
    Direction,
    EventSnapshot,
    EventType,
    ExclusionReason,
    LinkRole,
    Magnitude,
    Signal,
    SourceTierName,
    Stage,
)

GAPPED = DATA_DIR.parent / "evals" / "fixtures" / "gapped"
MISSING_ASSET_BARS = ("2026-02-03", "2026-02-04", "2026-02-05")
MISSING_BENCHMARK_BAR = "2026-02-10"


@pytest.fixture()
def gapped_series():
    market = FixtureMarketData(GAPPED)
    bars = market.daily_bars("eq:AAPL", date(2026, 1, 1), date(2026, 4, 1))
    bars += market.daily_bars("idx:US", date(2026, 1, 1), date(2026, 4, 1))
    return build_series(bars)


def signal(observed_at: str, horizon: int = 5) -> Signal:
    return Signal(
        id="a" * 16,
        signal_key="b" * 16,
        revision=1,
        event_id="c" * 16,
        asset_id="eq:AAPL",
        role=LinkRole.subject,
        direction=Direction.bearish,
        magnitude=Magnitude.moderate,
        confidence=0.5,
        horizon_bars=horizon,
        expected_ar_lo=-0.03,
        expected_ar_hi=-0.01,
        score=-0.01,
        prior_key="hack_exploit.subject.*",
        rationale_codes=("prior:hack_exploit.subject.*",),
        event_snapshot=EventSnapshot(
            event_type=EventType.hack_exploit,
            stage=Stage.confirmed,
            attributes={},
            corroboration=1,
            best_tier=SourceTierName.t2_wire,
            extraction_confidence=0.9,
            link_confidence=0.85,
            evidence_article_ids=("d" * 16,),
            event_date=observed_at[:10],
        ),
        observed_at=observed_at,
        created_as_of="2026-03-01T00:00:00Z",
    )


def test_fr9_gapped_fixture_omits_exactly_the_documented_bars():
    bars = FixtureMarketData(GAPPED).daily_bars("eq:AAPL", date(2026, 1, 1), date(2026, 4, 1))
    dates = {bar.date for bar in bars}
    assert dates.isdisjoint(MISSING_ASSET_BARS)
    assert "2026-02-02" in dates and "2026-02-06" in dates
    benchmark = FixtureMarketData(GAPPED).daily_bars("idx:US", date(2026, 1, 1), date(2026, 4, 1))
    assert MISSING_BENCHMARK_BAR not in {bar.date for bar in benchmark}


def test_fr9_available_bar_rules_step_over_the_gap(gapped_series):
    """A hole in the asset series shifts entry/exit to the next available bars."""
    evaluation = evaluate_signal(signal("2026-01-30T18:00:00Z"), gapped_series, "idx:US")
    assert evaluation.entry_date == "2026-02-02"
    assert evaluation.exit_date == "2026-02-11"  # 02-03/04/05 are absent, so the window steps over
    assert evaluation.excluded_reason is None


def test_fr10_benchmark_gap_on_the_entry_date_excludes(gapped_series):
    """The benchmark leg is read at the asset's own calendar dates, or not at all."""
    evaluation = evaluate_signal(signal("2026-02-09T18:00:00Z"), gapped_series, "idx:US")
    assert evaluation.entry_date is None
    assert evaluation.excluded_reason is ExclusionReason.benchmark_gap


def test_fr10_entry_is_strictly_after_the_observation_on_committed_bars(gapped_series):
    evaluation = evaluate_signal(signal("2026-02-02T23:59:00Z"), gapped_series, "idx:US")
    assert evaluation.entry_date is not None
    assert evaluation.entry_date > "2026-02-02"
