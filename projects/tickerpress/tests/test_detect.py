"""FR-5 — candidate scanning: case rules, recognizers, emission order."""

from __future__ import annotations

import pytest
from tickerpress.engine.models import AliasKind, MatchedVia, Strength, TextField
from tickerpress_testkit import article_of, detect_in, make_lexicons

AAPL = {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "aliases": [
        ("AAPL", AliasKind.TICKER_SYMBOL),
        ("$AAPL", AliasKind.CASHTAG),
        ("Apple Inc.", AliasKind.LEGAL_NAME),
        ("Apple", AliasKind.SHORT_NAME),
    ],
}
FORD = {
    "ticker": "F",
    "name": "Ford Motor Company",
    "aliases": [
        ("F", AliasKind.TICKER_SYMBOL),
        ("$F", AliasKind.CASHTAG),
        ("Ford", AliasKind.SHORT_NAME),
    ],
}
ALLSTATE = {
    "ticker": "ALL",
    "name": "Allstate Corporation",
    "aliases": [("ALL", AliasKind.TICKER_SYMBOL), ("Allstate", AliasKind.SHORT_NAME)],
}


@pytest.fixture
def lex():
    return make_lexicons(common_words=["all", "cat", "meta", "key"])


def test_fr5_legal_name_matches_stored_capitalization(lex) -> None:
    article = article_of("Apple Inc. beats estimates", lexicons=lex)
    (candidate,) = detect_in(article, [AAPL], lex)
    assert candidate.surface == "Apple Inc."
    assert candidate.alias_kind is AliasKind.LEGAL_NAME
    assert candidate.strength is Strength.STRONG


def test_fr5_lowercase_surfaces_never_match(lex) -> None:
    article = article_of("apple pie recipes for the holidays", lexicons=lex)
    assert detect_in(article, [AAPL], lex) == ()


def test_fr5_allcaps_variant_only_counts_inside_an_allcaps_run(lex) -> None:
    shouty = article_of("APPLE UNVEILS NEW IPHONE TODAY", lexicons=lex)
    surfaces = {c.surface for c in detect_in(shouty, [AAPL], lex)}
    assert "APPLE" in surfaces

    isolated = article_of("The word APPLE appeared once here", lexicons=lex)
    assert detect_in(isolated, [AAPL], lex) == ()


def test_fr5_length_one_ticker_never_matches_bare(lex) -> None:
    article = article_of("Shares of F rose while V slipped", lexicons=lex)
    assert detect_in(article, [FORD], lex) == ()


def test_fr5_length_one_ticker_matches_when_exchange_qualified(lex) -> None:
    article = article_of("Ford Motor Company (NYSE:F) recalled vehicles", lexicons=lex)
    candidates = detect_in(article, [FORD], lex)
    exchange = [c for c in candidates if c.matched_via is MatchedVia.EXCHANGE_QUALIFIED]
    assert [c.surface for c in exchange] == ["F"]
    assert exchange[0].strength is Strength.STRONG


def test_fr5_length_one_ticker_matches_as_a_cashtag(lex) -> None:
    article = article_of("Traders piled into $F this morning", lexicons=lex)
    (candidate,) = detect_in(article, [FORD], lex)
    assert candidate.surface == "$F"
    assert candidate.matched_via is MatchedVia.CASHTAG
    assert candidate.strength is Strength.STRONG


def test_fr5_cashtag_is_always_strong(lex) -> None:
    article = article_of("Chatter about $AAPL picked up", lexicons=lex)
    (candidate,) = detect_in(article, [AAPL], lex)
    assert candidate.matched_via is MatchedVia.CASHTAG
    assert candidate.strength is Strength.STRONG
    assert candidate.char_start == article.title.text.index("$AAPL")


@pytest.mark.parametrize(
    "text",
    [
        "The filing named (NASDAQ: AAPL) as the issuer",
        "The filing named (NASDAQ:AAPL) as the issuer",
        "The filing named (Nasdaq: AAPL) as the issuer",
        "The filing named (AAPL.O) as the issuer",
    ],
)
def test_fr5_exchange_qualified_patterns_are_strong(lex, text: str) -> None:
    article = article_of(text, lexicons=lex)
    (candidate,) = detect_in(article, [AAPL], lex)
    assert candidate.matched_via is MatchedVia.EXCHANGE_QUALIFIED
    assert candidate.strength is Strength.STRONG
    assert candidate.surface == "AAPL"


def test_fr5_exchange_hit_is_attributed_to_the_ticker_alias(lex) -> None:
    """DATA_MODEL §2.7 alias-attribution rule (mention 903)."""

    article = article_of("Apple Inc. (NASDAQ: AAPL) reported", lexicons=lex)
    candidates = detect_in(article, [AAPL], lex)
    exchange = next(c for c in candidates if c.matched_via is MatchedVia.EXCHANGE_QUALIFIED)
    assert exchange.alias_id == 1  # the ticker_symbol alias
    assert exchange.alias_kind is AliasKind.TICKER_SYMBOL


def test_fr5_common_word_ticker_is_a_weak_candidate(lex) -> None:
    article = article_of("Shares of ALL rose after the report", lexicons=lex)
    (candidate,) = detect_in(article, [ALLSTATE], lex)
    assert candidate.surface == "ALL"
    assert candidate.strength is Strength.WEAK


def test_fr5_nested_alias_hit_is_suppressed(lex) -> None:
    """`Apple` inside `Apple Inc.` is one mention, not two (DATA_MODEL §2.7)."""

    article = article_of("", "Apple Inc. reported revenue today", lexicons=lex)
    candidates = detect_in(article, [AAPL], lex)
    assert [(c.surface, c.char_start, c.char_end) for c in candidates] == [("Apple Inc.", 0, 10)]


def test_fr5_bare_ticker_inside_the_parenthetical_is_suppressed(lex) -> None:
    article = article_of("", "Apple Inc. (NASDAQ: AAPL) reported", lexicons=lex)
    candidates = detect_in(article, [AAPL], lex)
    assert [(c.surface, c.matched_via) for c in candidates] == [
        ("Apple Inc.", MatchedVia.ALIAS),
        ("AAPL", MatchedVia.EXCHANGE_QUALIFIED),
    ]


def test_fr5_bare_ticker_inside_a_cashtag_is_suppressed(lex) -> None:
    article = article_of("Traders bought $AAPL heavily", lexicons=lex)
    candidates = detect_in(article, [AAPL], lex)
    assert [c.matched_via for c in candidates] == [MatchedVia.CASHTAG]


@pytest.mark.parametrize("text", ["Applesauce sales rose", "MetaMask released an update"])
def test_fr5_matches_must_respect_token_boundaries(lex, text: str) -> None:
    meta = {
        "ticker": "META",
        "name": "Meta Platforms, Inc.",
        "aliases": [("Meta", AliasKind.SHORT_NAME)],
    }
    article = article_of(text, lexicons=lex)
    assert detect_in(article, [AAPL, meta], lex) == ()


def test_fr5_hyphen_is_a_token_boundary_so_meta_still_matches(lex) -> None:
    meta = {
        "ticker": "META",
        "name": "Meta Platforms, Inc.",
        "aliases": [("Meta", AliasKind.SHORT_NAME)],
    }
    article = article_of("Meta-analysis finds statin benefit overstated", lexicons=lex)
    (candidate,) = detect_in(article, [meta], lex)
    assert candidate.surface == "Meta"
    assert candidate.char_start == 0


def test_fr5_candidates_are_emitted_in_field_offset_alias_order(lex) -> None:
    article = article_of(
        "Apple and Ford both rose",
        "Apple Inc. led gains; Ford trailed.",
        "Later, Apple slipped while Ford held.",
        lexicons=lex,
    )
    candidates = detect_in(article, [AAPL, FORD], lex)
    keys = [(c.field, c.char_start, c.alias_id) for c in candidates]
    assert keys == sorted(
        keys,
        key=lambda k: (
            [TextField.TITLE, TextField.SUMMARY, TextField.CONTENT].index(k[0]),
            k[1],
            k[2],
        ),
    )
    assert [c.field for c in candidates][:2] == [TextField.TITLE, TextField.TITLE]


def test_fr5_two_companies_sharing_a_span_are_both_reported(lex) -> None:
    """Overlap resolution is per company, not global."""

    other = {
        "ticker": "APLE",
        "name": "Apple Hospitality REIT",
        "aliases": [("Apple", AliasKind.NICKNAME)],
    }
    article = article_of("Apple posted results", lexicons=lex)
    candidates = detect_in(article, [AAPL, other], lex)
    assert {c.company_ticker for c in candidates} == {"AAPL", "APLE"}


def test_fr5_empty_watchlist_matches_nothing(lex) -> None:
    article = article_of("Apple Inc. beats estimates", lexicons=lex)
    assert detect_in(article, [], lex) == ()


def test_fr5_offsets_index_into_the_normalized_field_text(lex) -> None:
    article = article_of("Apple beats estimates", "Apple Inc. reported.", lexicons=lex)
    for candidate in detect_in(article, [AAPL], lex):
        field = article.field_of(candidate.field)
        assert field is not None
        assert field.text[candidate.char_start : candidate.char_end] == candidate.surface
