"""DATA_MODEL.md invariants that the models themselves enforce."""

from __future__ import annotations

import pytest
from newsalpha.models import (
    BacktestParams,
    BacktestResult,
    Brief,
    Cluster,
    Direction,
    EventSnapshot,
    EventType,
    EvidenceSpan,
    ExclusionReason,
    LinkRole,
    Magnitude,
    PriceBar,
    Signal,
    SourceTierName,
    Stage,
    dir_sign,
    magnitude_for,
)

SNAPSHOT = EventSnapshot(
    event_type=EventType.hack_exploit,
    stage=Stage.confirmed,
    attributes={"amount_usd": 47000000.0},
    corroboration=2,
    best_tier=SourceTierName.t2_wire,
    extraction_confidence=0.95,
    link_confidence=0.85,
    evidence_article_ids=("9f2ab41c77d0e3a1",),
    event_date="2026-03-14",
)


def signal(**overrides):
    base = {
        "id": "d00d1e55aa11bb22",
        "signal_key": "5b71c0a9e4d2f318",
        "revision": 1,
        "supersedes": None,
        "supersedes_key": None,
        "event_id": "77aa0b12cd34ef56",
        "asset_id": "cx:HYPO",
        "role": LinkRole.subject,
        "direction": Direction.bearish,
        "magnitude": Magnitude.major,
        "confidence": 0.68,
        "horizon_bars": 5,
        "expected_ar_lo": -0.065,
        "expected_ar_hi": -0.025,
        "score": -0.0306,
        "prior_key": "hack_exploit.subject.*",
        "rationale_codes": ("prior:hack_exploit.subject.*",),
        "event_snapshot": SNAPSHOT,
        "observed_at": "2026-03-14T19:40:00Z",
        "created_as_of": "2026-03-15T07:00:00Z",
    }
    return Signal(**{**base, **overrides})


def test_signal_worked_example_from_the_data_model():
    row = signal()
    assert row.scored_tuple() == ("bearish", "major", 5, 0.68, "hack_exploit.subject.*")
    assert row.score == pytest.approx(-0.045 * 0.68, abs=1e-4)


def test_signal_confidence_is_bounded():
    with pytest.raises(ValueError):
        signal(confidence=0.99)
    with pytest.raises(ValueError):
        signal(confidence=0.01)


def test_signal_direction_is_never_unclear():
    with pytest.raises(ValueError, match="unclear"):
        signal(direction=Direction.unclear)


def test_signal_role_must_be_signal_bearing():
    for role in (LinkRole.mentioned, LinkRole.venue):
        with pytest.raises(ValueError, match="signal-bearing"):
            signal(role=role)


def test_signal_prior_key_never_encodes_a_stage():
    with pytest.raises(ValueError, match="stage"):
        signal(prior_key="mna.target.denied")


def test_signal_revision_chain_is_consistent():
    with pytest.raises(ValueError, match="revision 1 cannot supersede"):
        signal(revision=1, supersedes="a" * 16)
    with pytest.raises(ValueError, match="must name the revision"):
        signal(revision=2, supersedes=None)


def test_signal_is_frozen():
    row = signal()
    with pytest.raises(ValueError):
        row.confidence = 0.5


def test_cluster_invariants():
    good = Cluster(
        id="a" * 16,
        article_ids=("a1", "b2"),
        earliest_published_at="2026-03-10T08:00:00Z",
        latest_published_at="2026-03-10T20:00:00Z",
        article_count=2,
        corroboration=2,
        best_tier=SourceTierName.t2_wire,
    )
    assert good.event_date == "2026-03-10"

    with pytest.raises(ValueError, match="article_count"):
        good.model_copy(update={"article_count": 3}).model_validate(
            {**good.model_dump(), "article_count": 3}
        )
    with pytest.raises(ValueError, match="sorted"):
        Cluster(
            id="a" * 16,
            article_ids=("b2", "a1"),
            earliest_published_at="2026-03-10T08:00:00Z",
            latest_published_at="2026-03-10T20:00:00Z",
            article_count=2,
            corroboration=2,
            best_tier=SourceTierName.t2_wire,
        )
    with pytest.raises(ValueError, match="corroboration"):
        Cluster(
            id="a" * 16,
            article_ids=("a1",),
            earliest_published_at="2026-03-10T08:00:00Z",
            latest_published_at="2026-03-10T20:00:00Z",
            article_count=1,
            corroboration=2,
            best_tier=SourceTierName.t2_wire,
        )


def test_evidence_span_quote_length_must_match_offsets():
    assert EvidenceSpan(article_id="a", start=0, end=3, quote="abc").quote == "abc"
    with pytest.raises(ValueError, match="length"):
        EvidenceSpan(article_id="a", start=0, end=5, quote="abc")
    with pytest.raises(ValueError, match="end must be"):
        EvidenceSpan(article_id="a", start=5, end=5, quote="x")


def test_price_bar_ohlc_invariants():
    PriceBar(
        asset_id="cx:HYPO",
        date="2026-03-16",
        open=3.10,
        high=3.14,
        low=2.55,
        close=2.61,
        volume=8412000.0,
        source="fixture",
    )
    with pytest.raises(ValueError, match="low <= open"):
        PriceBar(
            asset_id="cx:HYPO",
            date="2026-03-16",
            open=4.0,
            high=3.14,
            low=2.55,
            close=2.61,
            volume=1.0,
            source="fixture",
        )
    with pytest.raises(ValueError):
        PriceBar(
            asset_id="cx:HYPO",
            date="2026-03-16",
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=-1.0,
            source="fixture",
        )


def test_brief_cannot_exist_unchecked():
    with pytest.raises(ValueError):
        Brief(
            signal_id="a" * 16,
            template_id="t",
            what_happened="x",
            why_it_matters="y",
            what_to_watch=("z",),
            uncertainty_note="w",
            rendered_text="x",
            frame_checked=False,
        )


def test_backtest_result_excluded_reason_is_set_iff_hit_is_null():
    BacktestResult(
        run_id="a" * 16,
        signal_id="b" * 16,
        entry_date="2026-03-11",
        exit_date="2026-03-15",
        ar=-0.01,
        hit=True,
    )
    BacktestResult(
        run_id="a" * 16, signal_id="b" * 16, excluded_reason=ExclusionReason.benchmark_gap
    )
    with pytest.raises(ValueError, match="entry/exit/ar"):
        BacktestResult(run_id="a" * 16, signal_id="b" * 16, hit=True, ar=0.1)
    with pytest.raises(ValueError, match="excluded_reason is set iff"):
        BacktestResult(
            run_id="a" * 16,
            signal_id="b" * 16,
            entry_date="2026-03-11",
            exit_date="2026-03-15",
            ar=-0.01,
            hit=True,
            excluded_reason=ExclusionReason.benchmark_gap,
        )


def test_backtest_params_range_is_ordered():
    with pytest.raises(ValueError, match="start must be"):
        BacktestParams(start="2026-03-31", end="2026-03-01")


def test_direction_sign_and_magnitude_bands():
    assert dir_sign(Direction.bullish) == 1
    assert dir_sign(Direction.bearish) == -1
    assert dir_sign(Direction.unclear) == 0
    assert magnitude_for(0.009) is Magnitude.minor
    assert magnitude_for(0.01) is Magnitude.moderate
    assert magnitude_for(-0.04) is Magnitude.moderate
    assert magnitude_for(0.041) is Magnitude.major
