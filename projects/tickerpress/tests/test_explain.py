"""FR-13 — explainability, read entirely from persisted rows."""

from __future__ import annotations

from tickerpress.engine.models import MatchedVia, MentionFeatures, Strength
from tickerpress.services import TickerPressService
from tickerpress_testkit import rss_feed, rss_item

TRAP = rss_item(
    guid="growers",
    link="https://wireone.example.com/apple-growers",
    title="Apple growers brace for frost",
    description="Orchard owners expect a difficult harvest this year. Cider makers warn of shortages.",
    pub_date="Sun, 01 Mar 2026 09:10:00 GMT",
)
REAL = rss_item(
    guid="q2",
    link="https://wireone.example.com/apple-q2",
    title="Apple beats March-quarter estimates on services strength",
    description="Apple Inc. (NASDAQ: AAPL) reported quarterly revenue ahead of analyst estimates.",
    pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
)


def seed(service: TickerPressService, items: list[str]) -> None:
    service.add_company(
        "AAPL",
        "Apple Inc.",
        context_terms=["iphone", "cupertino"],
        anti_terms=["orchard", "cider", "harvest", "fruit"],
    )
    service.feed_source.add("file:///wire.xml", rss_feed(items))
    service.add_feed("Wire One", "file:///wire.xml")
    service.ingest(deliver_alerts=False)


def test_fr13_rejected_candidates_are_shown_with_their_features(
    service: TickerPressService,
) -> None:
    seed(service, [TRAP])
    explanation = service.explain(1, company="AAPL")
    assert len(explanation.candidates) == 1
    candidate = explanation.candidates[0]
    assert not candidate.mention.accepted
    assert candidate.mention.surface == "Apple"
    assert candidate.mention.score == 0.0
    assert candidate.mention.threshold == 0.35
    assert set(candidate.mention.features) == set(MentionFeatures.model_fields)
    assert candidate.mention.features["anti_terms"] >= 2
    assert explanation.appearances == ()


def test_fr13_accepted_candidates_carry_alias_kind_and_offsets(
    service: TickerPressService,
) -> None:
    seed(service, [REAL])
    explanation = service.explain(1)
    surfaces = [item.mention.surface for item in explanation.candidates]
    assert surfaces == ["Apple", "Apple Inc.", "AAPL"]
    for item in explanation.candidates:
        assert item.alias is not None
        assert item.mention.char_end > item.mention.char_start
    exchange = explanation.candidates[2]
    assert exchange.mention.matched_via is MatchedVia.EXCHANGE_QUALIFIED
    assert exchange.alias is not None and exchange.alias.text == "AAPL"


def test_fr13_strong_candidates_report_no_feature_vector(service: TickerPressService) -> None:
    seed(service, [REAL])
    explanation = service.explain(1)
    strong = [c for c in explanation.candidates if c.mention.strength is Strength.STRONG]
    assert strong
    assert all(c.mention.features == {} for c in strong)
    assert all(c.mention.score == 1.0 for c in strong)


def test_fr13_relevance_components_are_read_from_the_appearance_row(
    service: TickerPressService,
) -> None:
    seed(service, [REAL])
    explanation = service.explain(1, company="AAPL")
    assert len(explanation.appearances) == 1
    appearance = explanation.appearances[0]
    assert appearance.mention_count == 3
    assert appearance.title_hit
    assert appearance.lede_hit
    assert appearance.relevance == 94


def test_fr13_new_story_membership_has_no_similarity(service: TickerPressService) -> None:
    seed(service, [REAL])
    explanation = service.explain(1)
    assert not explanation.joined_existing_story
    assert explanation.dedup_similarity is None
    assert explanation.story_copy_count == 1
    assert explanation.story.representative_article_id == 1


def test_fr13_joined_story_reports_the_stored_similarity(service: TickerPressService) -> None:
    seed(service, [REAL])
    service.feed_source.add("file:///two.xml", rss_feed([REAL]))
    service.add_feed("Wire Two", "file:///two.xml")
    service.ingest(deliver_alerts=False)

    explanation = service.explain(2)
    assert explanation.joined_existing_story
    assert explanation.dedup_similarity == 1.0  # canonical-url fast path
    assert explanation.story_copy_count == 2


def test_fr13_explain_reads_rows_and_never_recomputes(service: TickerPressService) -> None:
    """The reported values equal the stored ones, byte for byte."""

    seed(service, [REAL, TRAP])
    stored = service.repository.list_mentions(1)
    explanation = service.explain(1)
    assert [item.mention for item in explanation.candidates] == stored

    # Mutating the watchlist afterwards cannot change an explanation.
    service.add_term("AAPL", anti="services")
    again = service.explain(1)
    assert [item.mention for item in again.candidates] == stored


def test_fr13_a_company_filter_narrows_the_report(service: TickerPressService) -> None:
    seed(service, [REAL])
    service.add_company("TSLA", "Tesla, Inc.")
    assert service.explain(1, company="TSLA").candidates == ()
    assert service.explain(1, company="AAPL").candidates != ()


def test_fr13_unknown_article_raises(service: TickerPressService) -> None:
    seed(service, [REAL])
    try:
        service.explain(999)
    except KeyError as exc:
        assert "999" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("explain should reject an unknown article id")
