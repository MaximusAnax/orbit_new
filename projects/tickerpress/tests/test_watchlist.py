"""FR-1 — watchlist management: tickers, generated aliases, suffixes, duplicates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tickerpress.engine.models import (
    WEAK_TICKER_PRIOR,
    Alias,
    AliasKind,
    Company,
    DeliveryMode,
    Strength,
    normalize_ticker,
)
from tickerpress.engine.watchlist import (
    DuplicateAliasError,
    check_duplicate_surface,
    generate_aliases,
    short_name_of,
)
from tickerpress.services import TickerPressService
from tickerpress_testkit import NOW, make_lexicons


@pytest.mark.parametrize("raw,expected", [("aapl", "AAPL"), (" brk.b ", "BRK.B"), ("F", "F")])
def test_fr1_ticker_is_uppercased_and_validated(raw: str, expected: str) -> None:
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("bad", ["TOOLONG", "A1", "", "AA-B", "BRK.BB", "$AAPL"])
def test_fr1_invalid_tickers_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_ticker(bad)


def test_fr1_generated_aliases_match_us1_acceptance(lexicons) -> None:
    aliases = generate_aliases("TSLA", "Tesla, Inc.", NOW, lexicons)
    assert [(a.text, a.kind, a.strength) for a in aliases] == [
        ("TSLA", AliasKind.TICKER_SYMBOL, Strength.STRONG),
        ("$TSLA", AliasKind.CASHTAG, Strength.STRONG),
        ("Tesla, Inc.", AliasKind.LEGAL_NAME, Strength.STRONG),
        ("Tesla", AliasKind.SHORT_NAME, Strength.WEAK),
    ]
    assert aliases[-1].prior == 0.25
    assert all(alias.generated for alias in aliases)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Tesla, Inc.", "Tesla"),
        ("Apple Inc.", "Apple"),
        ("Alphabet Inc.", "Alphabet"),
        ("Shell plc", "Shell"),
        ("Caterpillar Inc.", "Caterpillar"),
        ("Meta Platforms, Inc.", "Meta Platforms"),
        ("Visa Inc.", "Visa"),
        ("Some Company Holdings Ltd", "Some"),
        ("Oracle", "Oracle"),
    ],
)
def test_fr1_short_name_strips_iso20275_legal_suffixes(lexicons, name: str, expected: str) -> None:
    assert short_name_of(name, lexicons) == expected


def test_fr1_short_name_alias_suppressed_when_it_equals_the_legal_name(lexicons) -> None:
    aliases = generate_aliases("ORCL", "Oracle", NOW, lexicons)
    assert [alias.kind for alias in aliases] == [
        AliasKind.TICKER_SYMBOL,
        AliasKind.CASHTAG,
        AliasKind.LEGAL_NAME,
    ]


def test_fr1_short_name_of_pure_suffix_name_is_empty(lexicons) -> None:
    assert short_name_of("Holdings Group Ltd", lexicons) == ""


def test_fr1_duplicate_case_folded_surface_is_rejected(lexicons) -> None:
    existing = generate_aliases("AAPL", "Apple Inc.", NOW, lexicons)
    with pytest.raises(DuplicateAliasError):
        check_duplicate_surface(existing, "apple")
    with pytest.raises(DuplicateAliasError):
        check_duplicate_surface(existing, "  APPLE  ")
    check_duplicate_surface(existing, "Apple Computer")


def test_fr1_common_word_tickers_are_demoted_to_weak(lexicons) -> None:
    for ticker in ("ALL", "CAT", "META", "KEY"):
        alias = generate_aliases(ticker, f"{ticker} Corp", NOW, lexicons)[0]
        assert alias.kind is AliasKind.TICKER_SYMBOL
        assert alias.strength is Strength.WEAK, ticker
        assert alias.prior == WEAK_TICKER_PRIOR


def test_fr1_non_word_tickers_stay_strong(lexicons) -> None:
    for ticker in ("AAPL", "MSFT", "TSLA", "GOOGL"):
        alias = generate_aliases(ticker, f"{ticker} Inc.", NOW, lexicons)[0]
        assert alias.strength is Strength.STRONG, ticker
        assert alias.prior == 0.0


def test_fr1_company_terms_are_case_folded_and_de_duplicated() -> None:
    company = Company(
        ticker="AAPL",
        name="Apple Inc.",
        context_terms=["Cupertino", "cupertino", "  App   Store ", ""],
        anti_terms=["Orchard"],
        created_at=NOW,
    )
    assert company.context_terms == ["cupertino", "app store"]
    assert company.anti_terms == ["orchard"]


def test_fr1_prior_is_bounded_to_zero_point_three() -> None:
    with pytest.raises(ValidationError):
        Alias(
            company_ticker="AAPL",
            text="Apple",
            kind=AliasKind.SHORT_NAME,
            strength=Strength.WEAK,
            prior=0.4,
            created_at=NOW,
        )


def test_fr1_alias_shapes_are_validated() -> None:
    with pytest.raises(ValidationError):
        Alias(
            company_ticker="AAPL",
            text="AAPL stock",
            kind=AliasKind.TICKER_SYMBOL,
            strength=Strength.STRONG,
            created_at=NOW,
        )
    with pytest.raises(ValidationError):
        Alias(
            company_ticker="AAPL",
            text="AAPL",
            kind=AliasKind.CASHTAG,
            strength=Strength.STRONG,
            created_at=NOW,
        )


def test_fr1_alias_text_is_whitespace_normalized() -> None:
    alias = Alias(
        company_ticker="AAPL",
        text="  Apple   Inc.  ",
        kind=AliasKind.LEGAL_NAME,
        strength=Strength.STRONG,
        created_at=NOW,
    )
    assert alias.text == "Apple Inc."
    assert alias.key == "apple inc."


def test_fr1_service_company_add_persists_generated_aliases(service: TickerPressService) -> None:
    company, aliases = service.add_company("TSLA", "Tesla, Inc.", mode=DeliveryMode.BOTH)
    assert company.mode is DeliveryMode.BOTH
    stored = service.repository.list_aliases("TSLA")
    assert [alias.text for alias in stored] == ["TSLA", "$TSLA", "Tesla, Inc.", "Tesla"]
    assert [alias.id for alias in stored] == sorted(alias.id for alias in aliases)


def test_fr1_service_no_auto_alias_creates_none(service: TickerPressService) -> None:
    service.add_company("TSLA", "Tesla, Inc.", auto_alias=False)
    assert service.repository.list_aliases("TSLA") == []


def test_fr1_service_rejects_duplicate_alias(service: TickerPressService) -> None:
    service.add_company("TSLA", "Tesla, Inc.")
    with pytest.raises(DuplicateAliasError):
        service.add_alias("TSLA", "tesla")


def test_fr1_service_terms_take_effect_for_later_ingests(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    updated = service.add_term("AAPL", context="Cupertino")
    assert updated.context_terms == ["cupertino"]
    updated = service.add_term("AAPL", anti="orchard")
    assert updated.anti_terms == ["orchard"]
    assert service.get_company("AAPL").context_terms == ["cupertino"]


def test_fr1_matcher_uses_committed_common_words_not_alias_row(lexicons) -> None:
    """A ticker stored strong is still demoted at match time if it is a word."""

    from tickerpress.engine.watchlist import compile_matcher

    company = Company(ticker="ALL", name="Allstate Corporation", created_at=NOW)
    alias = Alias(
        id=1,
        company_ticker="ALL",
        text="ALL",
        kind=AliasKind.TICKER_SYMBOL,
        strength=Strength.STRONG,
        created_at=NOW,
    )
    matcher = compile_matcher([company], [alias], lexicons)
    (entry,) = matcher.surfaces["ALL"]
    assert entry.strength is Strength.WEAK
    assert entry.prior == WEAK_TICKER_PRIOR


def test_fr1_synthetic_lexicon_keeps_ticker_strong_when_word_list_is_empty() -> None:
    lexicons = make_lexicons(common_words=[])
    alias = generate_aliases("ALL", "Allstate Corporation", NOW, lexicons)[0]
    assert alias.strength is Strength.STRONG
