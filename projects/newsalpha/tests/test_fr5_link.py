"""FR-5: gazetteer evidence classes, the all-caps guard, venue precedence, roles."""

from __future__ import annotations

from newsalpha.engine.cluster import build_clusters
from newsalpha.engine.extract import build_events
from newsalpha.engine.link import (
    NOTE_AMBIGUOUS_SUBJECT,
    NOTE_MNA_ROLE_UNRESOLVED,
    apply_links,
    caps_ratio,
    is_all_caps_sentence,
    scan_mentions,
)
from newsalpha.models import EventType, LinkRole
from newsalpha_testkit import article


def linked(datasets, title, body, event_type=None):
    """Run the real pipeline for one article and return (event, {asset_id: role})."""
    row = article(datasets, "a", title, body)
    clusters = build_clusters([row])
    extracted = build_events(clusters, {row.id: row}, datasets)
    if event_type is not None:
        extracted = [e for e in extracted if e.event.event_type is event_type]
    assert extracted, "no event extracted"
    event, links = apply_links(extracted[0], {row.id: row}, datasets)
    return event, {link.asset_id: link.role for link in links}, links


def mentions_of(datasets, title, body):
    row = article(datasets, "a", title, body)
    return {m.asset_id for m in scan_mentions(row, datasets)}


# --------------------------------------------------------------------------- #
# Evidence classes
# --------------------------------------------------------------------------- #


def test_fr5_cashtag_is_a_strong_pattern(datasets):
    row = article(datasets, "a", "Update", "Traders watched $COIN closely all afternoon.")
    hits = [m for m in scan_mentions(row, datasets) if m.asset_id == "eq:COIN"]
    assert hits and hits[0].confidence == 0.95
    assert hits[0].evidence_class == "cashtag"


def test_fr5_exchange_prefix_is_a_strong_pattern(datasets):
    row = article(datasets, "a", "Update", "The filing named NASDAQ: AAPL as the issuer.")
    hits = [m for m in scan_mentions(row, datasets) if m.asset_id == "eq:AAPL"]
    assert hits and hits[0].confidence == 0.95


def test_fr5_parenthesized_ticker_after_a_name_is_strong(datasets):
    row = article(datasets, "a", "Update", "Shares of Netflix (NFLX) climbed on Tuesday.")
    hits = sorted(
        (m for m in scan_mentions(row, datasets) if m.asset_id == "eq:NFLX"),
        key=lambda m: -m.confidence,
    )
    assert hits[0].confidence == 0.95


def test_fr5_plain_ticker_needs_all_caps_standalone_token(datasets):
    assert "eq:NFLX" in mentions_of(datasets, "Update", "The NFLX pair traded thinly.")
    assert "eq:NFLX" not in mentions_of(datasets, "Update", "The nflx pair traded thinly.")


def test_fr5_ambiguous_ticker_requires_same_sentence_context(datasets):
    assert "cx:NEAR" not in mentions_of(datasets, "Update", "The exit is NEAR the terminal.")
    assert "cx:NEAR" in mentions_of(
        datasets, "Update", "The NEAR protocol added validators this week."
    )


def test_fr5_alias_matching_is_case_sensitive(datasets):
    """ "Near" the word never matches "NEAR" the protocol."""
    assert "cx:NEAR" not in mentions_of(
        datasets, "Update", "The runner was near the end of the race."
    )


def test_fr5_ambiguous_alias_requires_context(datasets):
    assert "eq:AAPL" not in mentions_of(
        datasets, "Recipe", "She ate an Apple after lunch on the terrace."
    )
    assert "eq:AAPL" in mentions_of(
        datasets, "Markets", "Apple shares rose after the iPhone launch."
    )


def test_fr5_unflagged_alias_links_without_context_at_lower_confidence(datasets):
    row = article(datasets, "a", "Update", "Netflix said subscriber growth continued.")
    hit = next(m for m in scan_mentions(row, datasets) if m.asset_id == "eq:NFLX")
    assert hit.confidence == 0.70


def test_fr5_lowercase_common_word_traps_do_not_link(datasets):
    for body in (
        "The team ran a hackathon last spring.",
        "At one point the meta of the game changed.",
        "He flipped a coin to decide.",
        "The oracle of the village spoke in riddles.",
    ):
        assert mentions_of(datasets, "Trap", body) == set(), body


# --------------------------------------------------------------------------- #
# All-caps guard
# --------------------------------------------------------------------------- #


def test_fr5_caps_ratio_counts_alphabetic_tokens_of_length_two_or_more():
    ratio, count = caps_ratio("APPLE NEAR DEAL TO ACQUIRE BETA")
    assert ratio == 1.0 and count == 6
    assert is_all_caps_sentence("APPLE NEAR DEAL TO ACQUIRE BETA")
    assert not is_all_caps_sentence("Apple is near a deal to acquire Beta")
    assert not is_all_caps_sentence("SEC SUED")  # fewer than 4 uppercase tokens


def test_fr5_all_caps_guard_disables_plain_tickers(datasets):
    """Wire headline: NEAR/ONE/APE are English words here, not tickers."""
    linked_assets = mentions_of(
        datasets, "APPLE NEAR DEAL TO ACQUIRE ONE MORE RIVAL", "Details follow later today."
    )
    assert "cx:NEAR" not in linked_assets
    assert "cx:ONE" not in linked_assets


def test_fr5_all_caps_guard_still_links_alias_plus_context(datasets):
    hits = {
        m.asset_id: m
        for m in scan_mentions(
            article(
                datasets,
                "a",
                "NEAR PROTOCOL TOKEN JUMPS AFTER STAKING UPGRADE LANDS",
                "Details follow later today.",
            ),
            datasets,
        )
    }
    assert "cx:NEAR" in hits
    assert hits["cx:NEAR"].confidence == 0.70


def test_fr5_all_caps_guard_leaves_strong_patterns_alone(datasets):
    assert "eq:COIN" in mentions_of(
        datasets, "TRADERS PILE INTO $COIN AFTER THE OPEN TODAY", "Details follow."
    )


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


def test_fr5_mna_active_construction_assigns_acquirer_and_target(datasets):
    _, roles, _ = linked(
        datasets,
        "Deal",
        "Microsoft agreed to acquire Datadog in an all-cash deal.",
        EventType.mna,
    )
    assert roles["eq:MSFT"] is LinkRole.acquirer
    assert roles["eq:DDOG"] is LinkRole.target


def test_fr5_mna_passive_construction_flips_the_roles(datasets):
    _, roles, _ = linked(
        datasets,
        "Deal",
        "Datadog is to be acquired by Microsoft in an all-cash deal.",
        EventType.mna,
    )
    assert roles["eq:MSFT"] is LinkRole.acquirer
    assert roles["eq:DDOG"] is LinkRole.target


def test_fr5_mna_bid_for_resolver(datasets):
    _, roles, _ = linked(
        datasets,
        "Deal",
        "Microsoft launched a takeover bid for Datadog on Tuesday.",
        EventType.mna,
    )
    assert roles["eq:MSFT"] is LinkRole.acquirer
    assert roles["eq:DDOG"] is LinkRole.target


def test_fr5_mna_symmetric_abstains(datasets):
    """ "merger between A and B" resolves nothing: every party is `mentioned`."""
    event, roles, _ = linked(
        datasets, "Deal", "A merger between Microsoft and Datadog was announced.", EventType.mna
    )
    assert set(roles.values()) == {LinkRole.mentioned}
    assert NOTE_MNA_ROLE_UNRESOLVED in event.notes


def test_fr5_mna_merger_of_equals_abstains(datasets):
    event, roles, _ = linked(
        datasets, "Deal", "Microsoft and Datadog announced a merger of equals.", EventType.mna
    )
    assert LinkRole.acquirer not in roles.values()
    assert LinkRole.target not in roles.values()
    assert NOTE_MNA_ROLE_UNRESOLVED in event.notes


def test_fr5_mna_three_parties_abstains_rather_than_guessing(datasets):
    event, roles, _ = linked(
        datasets,
        "Deal",
        "Oracle Corp and Microsoft agreed to acquire Datadog together, shares of Netflix fell.",
        EventType.mna,
    )
    assert NOTE_MNA_ROLE_UNRESOLVED in event.notes
    assert set(roles.values()) <= {LinkRole.mentioned}


def test_fr5_venue_precedence_coinbase(datasets):
    """Coinbase is the venue, never the subject -- the listed token is the subject."""
    event, roles, links = linked(
        datasets, "Listing", "Solana is now available on Coinbase for trading.", EventType.listing
    )
    assert roles["eq:COIN"] is LinkRole.venue
    assert roles["cx:SOL"] is LinkRole.subject
    assert NOTE_AMBIGUOUS_SUBJECT not in event.notes
    venue_link = next(link for link in links if link.asset_id == "eq:COIN")
    assert venue_link.evidence.quote == "Coinbase"


def test_fr5_venue_that_is_an_asset_never_signals(datasets):
    from newsalpha.engine.score import score_event

    row = article(datasets, "a", "Listing", "Solana is now available on Coinbase for trading.")
    clusters = build_clusters([row])
    extracted = [
        e
        for e in build_events(clusters, {row.id: row}, datasets)
        if e.event.event_type is EventType.listing
    ]
    event, links = apply_links(extracted[0], {row.id: row}, datasets)
    scored, _ = score_event(event, links, clusters[0], datasets)
    assert {s.asset_id for s in scored} == {"cx:SOL"}


def test_fr5_listing_with_two_candidate_subjects_abstains(datasets):
    event, roles, _ = linked(
        datasets,
        "Listing",
        "Solana and Cardano are now available on Coinbase for trading.",
        EventType.listing,
    )
    assert LinkRole.subject not in roles.values()
    assert NOTE_AMBIGUOUS_SUBJECT in event.notes


def test_fr5_nasdaq_delisting_venue_is_the_exchange_operator(datasets):
    _, roles, _ = linked(
        datasets,
        "Delisting",
        "Lucid Group will be delisted from Nasdaq at the end of the month.",
        EventType.delisting,
    )
    assert roles["eq:NDAQ"] is LinkRole.venue
    assert roles["eq:LCID"] is LinkRole.subject


def test_fr5_other_types_use_subject_and_mentioned(datasets):
    _, roles, _ = linked(
        datasets,
        "Earnings",
        "Nvidia beat estimates for the quarter. Analysts at Goldman Sachs raised targets.",
        EventType.earnings_surprise,
    )
    assert roles["eq:NVDA"] is LinkRole.subject
    assert roles["eq:GS"] is LinkRole.mentioned


def test_fr5_every_link_carries_an_evidence_span(datasets):
    row = article(datasets, "a", "Deal", "Microsoft agreed to acquire Datadog today.")
    clusters = build_clusters([row])
    extracted = build_events(clusters, {row.id: row}, datasets)
    _, links = apply_links(extracted[0], {row.id: row}, datasets)
    text = row.analysis_text
    assert links
    for link in links:
        assert link.evidence.quote == text[link.evidence.start : link.evidence.end]
        assert 0.5 <= link.link_confidence <= 0.95
