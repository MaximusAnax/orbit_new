"""FR-8 — relevance components, rounding and the story-level rollup."""

from __future__ import annotations

import pytest
from tickerpress.engine.models import AliasKind, round_half_up
from tickerpress.engine.relevance import (
    compute_components,
    lede_token_limit,
    relevance_of,
    story_relevance,
)
from tickerpress.services import TickerPressService
from tickerpress_testkit import article_of, detect_in, make_lexicons, rss_feed, rss_item

ATTAINABLE = {6, 13, 19, 25, 31, 38, 44, 50, 56, 63, 69, 75, 81, 88, 94, 100}

APPLE = {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "aliases": [
        ("AAPL", AliasKind.TICKER_SYMBOL),
        ("Apple Inc.", AliasKind.LEGAL_NAME),
        ("Apple", AliasKind.SHORT_NAME),
    ],
}


@pytest.fixture
def lex():
    return make_lexicons()


def test_fr8_round_half_up_is_not_bankers_rounding() -> None:
    assert round_half_up(62.5) == 63
    assert round_half_up(12.5) == 13
    assert round(62.5) == 62  # what the built-in would have done
    assert round_half_up(93.75) == 94


@pytest.mark.parametrize(
    "title,lede,count,expected",
    [
        (True, True, 4, 100),
        (True, True, 3, 94),
        (True, True, 1, 81),
        (True, False, 4, 75),
        (True, False, 1, 56),
        (False, True, 4, 50),
        (False, True, 3, 44),
        (False, False, 4, 25),
        (False, False, 3, 19),
        (False, False, 2, 13),
        (False, False, 1, 6),
    ],
)
def test_fr8_formula_values(title: bool, lede: bool, count: int, expected: int) -> None:
    assert relevance_of(title, lede, count) == expected


def test_fr8_attainable_value_set_is_exactly_the_documented_one() -> None:
    values = {
        relevance_of(title, lede, count)
        for title in (False, True)
        for lede in (False, True)
        for count in range(1, 12)
    }
    assert values == ATTAINABLE


def test_fr8_relevance_requires_at_least_one_mention() -> None:
    with pytest.raises(ValueError):
        relevance_of(True, True, 0)


def test_fr8_mention_count_saturates_at_four() -> None:
    assert relevance_of(False, False, 4) == relevance_of(False, False, 40)


@pytest.mark.parametrize("count,limit", [(0, 0), (1, 1), (4, 1), (5, 2), (40, 10), (41, 11)])
def test_fr8_lede_window_is_a_quarter_of_the_content_tokens(count: int, limit: int) -> None:
    assert lede_token_limit(count) == limit


def test_fr8_title_hit_component(lex) -> None:
    article = article_of("Apple Inc. beats estimates", "Nothing here.", lexicons=lex)
    components = compute_components(detect_in(article, [APPLE], lex), article)
    assert components is not None
    assert components.title_hit
    assert not components.lede_hit
    assert components.mention_count == 1
    assert components.relevance == 56


def test_fr8_lede_hit_from_the_first_summary_sentence(lex) -> None:
    article = article_of(
        "Markets roundup",
        "Apple Inc. led the gains. Other stocks trailed.",
        lexicons=lex,
    )
    components = compute_components(detect_in(article, [APPLE], lex), article)
    assert components is not None
    assert not components.title_hit
    assert components.lede_hit


def test_fr8_mention_in_a_later_summary_sentence_is_not_a_lede_hit(lex) -> None:
    article = article_of(
        "Markets roundup",
        "Stocks were mixed on Monday. Apple Inc. led the gains later.",
        lexicons=lex,
    )
    components = compute_components(detect_in(article, [APPLE], lex), article)
    assert components is not None
    assert not components.lede_hit


def test_fr8_lede_hit_from_the_first_quarter_of_the_content(lex) -> None:
    head = "Apple Inc. said Monday that the store would open."
    tail = " ".join(["filler"] * 60)
    article = article_of("Retail roundup", "No surface here.", f"{head} {tail}", lexicons=lex)
    components = compute_components(detect_in(article, [APPLE], lex), article)
    assert components is not None
    assert components.lede_hit


def test_fr8_mention_past_the_content_quarter_is_not_a_lede_hit(lex) -> None:
    head = " ".join(["filler"] * 60)
    article = article_of(
        "Retail roundup", "No surface here.", f"{head} Apple Inc. closed the deal.", lexicons=lex
    )
    components = compute_components(detect_in(article, [APPLE], lex), article)
    assert components is not None
    assert not components.lede_hit


def test_fr8_null_content_leaves_only_the_summary_lede_path(lex) -> None:
    article = article_of("Retail roundup", "Apple Inc. led gains.", None, lexicons=lex)
    assert article.content is None
    assert article.content_token_count == 0
    components = compute_components(detect_in(article, [APPLE], lex), article)
    assert components is not None
    assert components.lede_hit


def test_fr8_no_accepted_mentions_yields_no_appearance(lex) -> None:
    assert compute_components([], article_of("Nothing", lexicons=lex)) is None


def test_fr8_story_relevance_is_the_max_over_members() -> None:
    assert story_relevance([44, 94, 6]) == 94
    with pytest.raises(ValueError):
        story_relevance([])


def test_fr8_data_model_example_article_scores_94(service: TickerPressService) -> None:
    """DATA_MODEL §5: title + lede + 3 mentions -> 0.9375 -> 94."""

    service.add_company("AAPL", "Apple Inc.", context_terms=["iphone", "cupertino"])
    service.feed_source.add(
        "file:///wire.xml",
        rss_feed(
            [
                rss_item(
                    guid="wireone-2026-4471",
                    link="https://wireone.example.com/apple-q2?utm_source=rss&amp;utm_medium=feed",
                    title="Apple beats March-quarter estimates on services strength",
                    description=(
                        "Apple Inc. (NASDAQ: AAPL) reported quarterly revenue of $96.4 billion, "
                        "ahead of analyst estimates, as services growth offset softer iPhone "
                        "sales. Shares rose 3% in extended trading."
                    ),
                    pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("Wire One", "file:///wire.xml")
    service.ingest(deliver_alerts=False)

    appearance = service.repository.get_appearance(1, "AAPL")
    assert appearance is not None
    assert appearance.mention_count == 3
    assert appearance.title_hit
    assert appearance.lede_hit
    assert appearance.relevance == 94


def test_fr8_story_relevance_survives_a_headline_less_copy(service: TickerPressService) -> None:
    service.add_company("TSLA", "Tesla, Inc.")
    body = (
        "Tesla said the recall covers vehicles built between January and March. "
        "The company will replace the seatbelt anchor free of charge."
    )
    service.feed_source.add(
        "file:///a.xml",
        rss_feed(
            [
                rss_item(
                    guid="a",
                    link="https://a.example/recall",
                    title="Tesla, Inc. recalls 12,000 vehicles",
                    description=body,
                    pub_date="Mon, 02 Mar 2026 06:05:00 GMT",
                )
            ]
        ),
    )
    service.feed_source.add(
        "file:///b.xml",
        rss_feed(
            [
                rss_item(
                    guid="b",
                    link="https://b.example/recall",
                    title="Carmaker recalls 12,000 vehicles",
                    description=body,
                    pub_date="Mon, 02 Mar 2026 07:05:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("A", "file:///a.xml")
    service.add_feed("B", "file:///b.xml")
    service.ingest(deliver_alerts=False)

    first, second = list(service.repository.iter_articles())
    assert first.story_id == second.story_id
    with_headline = service.repository.get_appearance(1, "TSLA")
    without_headline = service.repository.get_appearance(2, "TSLA")
    assert with_headline is not None and without_headline is not None
    assert with_headline.title_hit and not without_headline.title_hit
    assert service.story_relevance(first.story_id, "TSLA") == with_headline.relevance
