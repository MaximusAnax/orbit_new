"""FR-6: prior resolution, the two disjoint stage channels, the modifier formula."""

from __future__ import annotations

import pytest
from newsalpha.engine.cluster import build_clusters
from newsalpha.engine.extract import build_events
from newsalpha.engine.link import apply_links
from newsalpha.engine.score import (
    NOTE_UNCLEAR_ABSTAIN,
    confidence_for,
    corroboration_factor,
    score_event,
)
from newsalpha.models import Direction, EventType, LinkRole, Magnitude, Stage
from newsalpha_testkit import article


def score(datasets, title, body, event_type=None, **kwargs):
    row = article(datasets, kwargs.pop("external_id", "a"), title, body, **kwargs)
    clusters = build_clusters([row])
    extracted = build_events(clusters, {row.id: row}, datasets)
    if event_type is not None:
        extracted = [e for e in extracted if e.event.event_type is event_type]
    assert extracted, "no event extracted"
    event, links = apply_links(extracted[0], {row.id: row}, datasets)
    scored, notes = score_event(event, links, clusters[0], datasets)
    return {(s.asset_id, s.role): s for s in scored}, notes, event


# --------------------------------------------------------------------------- #
# Prior resolution
# --------------------------------------------------------------------------- #


def test_fr6_polarity_selects_the_prior_row_and_flips_direction(datasets):
    beat, _, _ = score(datasets, "Earnings", "Nvidia beat estimates for the quarter.")
    miss, _, _ = score(datasets, "Earnings", "Nvidia missed estimates for the quarter.")
    assert beat[("eq:NVDA", LinkRole.subject)].direction is Direction.bullish
    assert miss[("eq:NVDA", LinkRole.subject)].direction is Direction.bearish


def test_fr6_kind_specific_priors_beat_the_wildcard(datasets):
    crypto = datasets.resolve_prior("listing.subject.*", "crypto")
    equity = datasets.resolve_prior("listing.subject.*", "equity")
    assert crypto.magnitude is Magnitude.major
    assert equity.magnitude is Magnitude.minor
    assert crypto.mid_expected_ar > equity.mid_expected_ar


def test_fr6_prior_keys_never_encode_a_stage(datasets):
    stage_values = {s.value for s in Stage}
    for key, _ in datasets.priors:
        assert not (set(key.split(".")) & stage_values)


def test_fr6_mentioned_and_venue_links_never_score(datasets):
    scored, _, _ = score(
        datasets,
        "Listing",
        "Solana is now available on Coinbase for trading.",
        EventType.listing,
    )
    assert set(scored) == {("cx:SOL", LinkRole.subject)}


# --------------------------------------------------------------------------- #
# The two stage channels
# --------------------------------------------------------------------------- #


def test_fr6_rumored_scores_exactly_half_the_confidence_of_confirmed(datasets):
    """US-4: `f_stage` is the only stage effect on confidence."""
    confirmed = confidence_for(
        base_conf=0.8,
        tier_weight=0.85,
        stage=Stage.confirmed,
        corroboration=2,
        extraction_confidence=0.9,
        link_confidence=0.85,
    )
    rumored = confidence_for(
        base_conf=0.8,
        tier_weight=0.85,
        stage=Stage.rumored,
        corroboration=2,
        extraction_confidence=0.9,
        link_confidence=0.85,
    )
    assert rumored == pytest.approx(confirmed * 0.5, abs=1e-4)


def test_fr6_stage_overrides_are_the_only_stage_effect_on_direction_and_band(datasets):
    prior = datasets.resolve_prior("mna.target.*", "equity")
    assert prior.effective(Stage.confirmed).direction is Direction.bullish
    assert prior.effective(Stage.rumored).direction is Direction.bullish
    assert prior.effective(Stage.denied).direction is Direction.bearish
    assert prior.effective(Stage.rumored).horizon_bars == 5
    assert prior.effective(Stage.confirmed).horizon_bars == 20


def test_fr6_denied_mna_is_bearish_for_the_target_and_silent_for_the_acquirer(datasets):
    scored, notes, _ = score(
        datasets,
        "Denial",
        "Microsoft denied reports it is in talks to acquire Datadog.",
        EventType.mna,
    )
    assert scored[("eq:DDOG", LinkRole.target)].direction is Direction.bearish
    assert ("eq:MSFT", LinkRole.acquirer) not in scored
    assert NOTE_UNCLEAR_ABSTAIN in notes


def test_fr6_unclear_abstention_is_recorded_on_the_event(datasets):
    from newsalpha.engine import pipeline
    from newsalpha_testkit import raw

    _, result = pipeline.ingest(
        [raw("d", "Denial", "Microsoft denied reports it is in talks to acquire Datadog.")],
        datasets,
        as_of="2026-03-20T07:00:00Z",
    )
    mna = next(e for e in result.events if e.event_type is EventType.mna)
    assert NOTE_UNCLEAR_ABSTAIN in mna.notes


def test_fr6_rationale_codes_record_every_factor(datasets):
    scored, _, _ = score(
        datasets,
        "Denial",
        "Microsoft denied reports it is in talks to acquire Datadog.",
        EventType.mna,
    )
    codes = scored[("eq:DDOG", LinkRole.target)].rationale_codes
    assert codes[0] == "prior:mna.target.*"
    assert "mod:kind=equity" in codes
    assert "mod:stage_override=denied" in codes
    assert "mod:f_stage=0.70" in codes
    assert any(code.startswith("mod:tier=") for code in codes)
    assert any(code.startswith("mod:corroboration=") for code in codes)
    assert any(code.startswith("mod:extraction=") for code in codes)
    assert any(code.startswith("mod:link=") for code in codes)


# --------------------------------------------------------------------------- #
# The modifier formula
# --------------------------------------------------------------------------- #


def test_fr6_corroboration_factor_caps_at_four_domains():
    assert corroboration_factor(1) == 1.0
    assert corroboration_factor(2) == pytest.approx(1.1)
    assert corroboration_factor(4) == pytest.approx(1.3)
    assert corroboration_factor(9) == pytest.approx(1.3)


def test_fr6_worked_example_from_the_data_model(datasets):
    """base 0.90 x tier 0.85 x stage 1.00 x corroboration 1.10 x 0.95 x 0.85."""
    value = confidence_for(
        base_conf=0.90,
        tier_weight=0.85,
        stage=Stage.confirmed,
        corroboration=2,
        extraction_confidence=0.95,
        link_confidence=0.85,
    )
    assert value == pytest.approx(0.6795, abs=1e-4)
    assert round(value, 2) == 0.68


def test_fr6_confidence_is_clamped_to_the_documented_bounds():
    assert (
        confidence_for(
            base_conf=0.95,
            tier_weight=1.0,
            stage=Stage.confirmed,
            corroboration=9,
            extraction_confidence=0.95,
            link_confidence=0.95,
        )
        <= 0.95
    )
    assert (
        confidence_for(
            base_conf=0.05,
            tier_weight=0.6,
            stage=Stage.rumored,
            corroboration=1,
            extraction_confidence=0.7,
            link_confidence=0.7,
        )
        >= 0.05
    )


def test_fr6_score_is_signed_mid_band_times_confidence(datasets):
    scored, _, _ = score(datasets, "Earnings", "Nvidia beat estimates for the quarter.")
    signal = scored[("eq:NVDA", LinkRole.subject)]
    mid = (signal.expected_ar_lo + signal.expected_ar_hi) / 2
    assert signal.score == pytest.approx(mid * signal.confidence)
    assert signal.score > 0


def test_fr6_magnitude_matches_the_band_for_every_committed_prior(datasets):
    from newsalpha.models import magnitude_for

    for prior in datasets.priors.values():
        assert magnitude_for(prior.mid_expected_ar) is prior.magnitude
        for override in (prior.stage_overrides or {}).values():
            assert magnitude_for(override.mid_expected_ar) is override.magnitude


def test_fr6_tier_weight_lowers_confidence_for_low_tier_sources(datasets):
    wire, _, _ = score(datasets, "Earnings", "Nvidia beat estimates.", domain="reuters.example")
    blog, _, _ = score(datasets, "Earnings", "Nvidia beat estimates.", domain="someblog.example")
    assert (
        blog[("eq:NVDA", LinkRole.subject)].confidence
        < wire[("eq:NVDA", LinkRole.subject)].confidence
    )


def test_fr6_event_snapshot_makes_a_revision_auditable(datasets):
    scored, _, event = score(datasets, "Earnings", "Nvidia beat estimates.")
    snapshot = scored[("eq:NVDA", LinkRole.subject)].event_snapshot
    assert snapshot.event_type is EventType.earnings_surprise
    assert snapshot.stage is Stage.confirmed
    assert snapshot.evidence_article_ids
    assert snapshot.event_date == event.event_date
