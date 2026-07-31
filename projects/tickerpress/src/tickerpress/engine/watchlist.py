"""Watchlist compilation: alias generation and the multi-pattern matcher.

SCOPE FR-1 (alias defaults, legal-suffix stripping, duplicate rejection) and the
compilation half of FR-5. Matching itself lives in :mod:`.detect`.

The matcher is a single case-sensitive regex alternation over every literal
surface, longest alternative first, plus a small committed set of newswire
parenthetical patterns. Aho-Corasick (1975) is the named scale path if a
watchlist ever outgrows regex alternation; at 5-50 companies it does not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..resources import Lexicons
from .models import (
    KIND_DEFAULT_PRIOR,
    KIND_DEFAULT_STRENGTH,
    WEAK_TICKER_PRIOR,
    Alias,
    AliasKind,
    Company,
    MatchedVia,
    Strength,
)

__all__ = [
    "EXCHANGE_PREFIXES",
    "DuplicateAliasError",
    "Matcher",
    "SurfaceEntry",
    "check_duplicate_surface",
    "compile_matcher",
    "default_prior",
    "default_strength",
    "generate_aliases",
    "short_name_of",
]

#: Exchange labels accepted inside the newswire parenthetical (FR-5).
#: Committed data: `(NASDAQ: AAPL)`, `(NYSE:F)` and friends are style
#: conventions that exist precisely to be unambiguous.
EXCHANGE_PREFIXES: tuple[str, ...] = (
    "NASDAQ",
    "NASDAQ GS",
    "NASDAQ GM",
    "NASDAQ CM",
    "NYSE",
    "NYSE AMERICAN",
    "NYSE ARCA",
    "NYSEARCA",
    "NYSEAMERICAN",
    "AMEX",
    "BATS",
    "CBOE",
    "OTC",
    "OTCMKTS",
    "OTCQB",
    "OTCQX",
    "LSE",
    "LON",
    "TSX",
    "TSXV",
    "ASX",
    "ETR",
    "FRA",
    "EPA",
    "BIT",
    "BME",
    "SWX",
    "HKG",
    "TYO",
    "SHA",
    "SHE",
    "NSE",
    "BSE",
)

#: Reuters RIC suffix, e.g. `(AAPL.O)`.
_RIC_SUFFIX = r"[A-Z]{1,3}"


class DuplicateAliasError(ValueError):
    """Raised when a company would get two aliases with the same surface."""


# --------------------------------------------------------------------------
# alias generation (FR-1)
# --------------------------------------------------------------------------


def short_name_of(name: str, lexicons: Lexicons) -> str:
    """Strip trailing legal-form suffixes: ``"Tesla, Inc." -> "Tesla"``.

    Suffixes come from the committed ISO 20275-derived list and are stripped
    repeatedly from the end. Returns ``""`` when nothing but suffixes remains.
    """

    suffixes = set(lexicons.legal_suffixes)
    tokens = name.split()
    while tokens:
        key = tokens[-1].casefold().strip(".,;:()")
        dotted = f"{key}."
        if key and (key in suffixes or dotted in suffixes):
            tokens.pop()
            continue
        break
    return " ".join(tokens).strip(" ,;:-&")


def default_strength(kind: AliasKind, text: str, lexicons: Lexicons) -> Strength:
    """Kind defaults, with the common-word demotion for tickers (§2.2)."""

    if kind is AliasKind.TICKER_SYMBOL and text.casefold() in lexicons.common_words:
        return Strength.WEAK
    return KIND_DEFAULT_STRENGTH[kind]


def default_prior(kind: AliasKind, strength: Strength) -> float:
    """Kind default prior; a demoted ticker gets the weak-ticker prior."""

    if kind is AliasKind.TICKER_SYMBOL and strength is Strength.WEAK:
        return WEAK_TICKER_PRIOR
    return KIND_DEFAULT_PRIOR[kind]


def _make_alias(
    ticker: str,
    text: str,
    kind: AliasKind,
    now: datetime,
    lexicons: Lexicons,
    *,
    generated: bool = True,
) -> Alias:
    strength = default_strength(kind, text, lexicons)
    return Alias(
        company_ticker=ticker,
        text=text,
        kind=kind,
        strength=strength,
        prior=default_prior(kind, strength),
        generated=generated,
        created_at=now,
    )


def generate_aliases(ticker: str, name: str, now: datetime, lexicons: Lexicons) -> list[Alias]:
    """The four aliases ``company add`` creates (FR-1, US-1).

    Order is fixed — ticker, cashtag, legal name, short name — because alias ids
    are assigned in insertion order and the alias-attribution rule (DATA_MODEL
    §2.7) and every deterministic tie-break reference them.
    """

    aliases = [
        _make_alias(ticker, ticker, AliasKind.TICKER_SYMBOL, now, lexicons),
        _make_alias(ticker, f"${ticker}", AliasKind.CASHTAG, now, lexicons),
        _make_alias(ticker, name, AliasKind.LEGAL_NAME, now, lexicons),
    ]
    short = short_name_of(name, lexicons)
    if short and short.casefold() != name.casefold():
        aliases.append(_make_alias(ticker, short, AliasKind.SHORT_NAME, now, lexicons))
    return aliases


def check_duplicate_surface(existing: Iterable[Alias], text: str) -> None:
    """Reject a duplicate case-folded surface within one company (FR-1)."""

    key = " ".join(text.split()).casefold()
    for alias in existing:
        if alias.key == key:
            raise DuplicateAliasError(
                f"company {alias.company_ticker} already has alias {alias.text!r}"
            )


# --------------------------------------------------------------------------
# matcher compilation (FR-5)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SurfaceEntry:
    """One compiled surface: what a literal hit means."""

    company_ticker: str
    alias_id: int | None
    alias_kind: AliasKind
    alias_text: str
    matched_via: MatchedVia
    strength: Strength
    prior: float
    requires_allcaps_run: bool = False

    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.company_ticker, self.alias_id if self.alias_id is not None else -1)


@dataclass(frozen=True)
class Matcher:
    """Compiled watchlist: literal alternation plus the parenthetical patterns."""

    surfaces: Mapping[str, tuple[SurfaceEntry, ...]]
    surface_re: re.Pattern[str] | None
    exchange_re: re.Pattern[str] | None
    ric_re: re.Pattern[str] | None
    exchange_entries: Mapping[str, SurfaceEntry]
    companies: Mapping[str, Company]

    @property
    def is_empty(self) -> bool:
        return self.surface_re is None and self.exchange_re is None


def _effective_ticker_strength(alias: Alias, lexicons: Lexicons) -> tuple[Strength, float]:
    """Common-word tickers are weak candidates however the row was stored."""

    if alias.text.casefold() in lexicons.common_words:
        prior = alias.prior if alias.prior > 0.0 else WEAK_TICKER_PRIOR
        return Strength.WEAK, prior
    return alias.strength, alias.prior


def compile_matcher(
    companies: Sequence[Company],
    aliases: Sequence[Alias],
    lexicons: Lexicons,
) -> Matcher:
    """Compile the watchlist into a deterministic multi-pattern matcher.

    Rules encoded here (FR-5):

    * name-like surfaces match their stored capitalization, plus an ALL-CAPS
      variant that only counts inside an all-caps run;
    * a length-1 ticker never enters the literal alternation — it is reachable
      only as a cashtag or exchange-qualified;
    * a ticker whose lower-cased form is a common word is demoted to weak;
    * cashtags and the exchange parentheticals are always strong.
    """

    by_ticker = {company.ticker: company for company in companies}
    surfaces: dict[str, list[SurfaceEntry]] = {}
    exchange_entries: dict[str, SurfaceEntry] = {}

    def add(text: str, entry: SurfaceEntry) -> None:
        surfaces.setdefault(text, []).append(entry)

    ordered_aliases = sorted(
        (a for a in aliases if a.company_ticker in by_ticker),
        key=lambda a: (a.company_ticker, a.id if a.id is not None else -1),
    )
    for alias in ordered_aliases:
        if alias.kind is AliasKind.TICKER_SYMBOL:
            strength, prior = _effective_ticker_strength(alias, lexicons)
            entry = SurfaceEntry(
                company_ticker=alias.company_ticker,
                alias_id=alias.id,
                alias_kind=alias.kind,
                alias_text=alias.text,
                matched_via=MatchedVia.ALIAS,
                strength=strength,
                prior=prior,
            )
            # Exchange-qualified hits are attributed to the ticker alias row.
            exchange_entries.setdefault(
                alias.text,
                SurfaceEntry(
                    company_ticker=alias.company_ticker,
                    alias_id=alias.id,
                    alias_kind=alias.kind,
                    alias_text=alias.text,
                    matched_via=MatchedVia.EXCHANGE_QUALIFIED,
                    strength=Strength.STRONG,
                    prior=0.0,
                ),
            )
            if len(alias.text) > 1:
                add(alias.text, entry)
            continue

        if alias.kind is AliasKind.CASHTAG:
            add(
                alias.text,
                SurfaceEntry(
                    company_ticker=alias.company_ticker,
                    alias_id=alias.id,
                    alias_kind=alias.kind,
                    alias_text=alias.text,
                    matched_via=MatchedVia.CASHTAG,
                    strength=Strength.STRONG,
                    prior=0.0,
                ),
            )
            continue

        base = SurfaceEntry(
            company_ticker=alias.company_ticker,
            alias_id=alias.id,
            alias_kind=alias.kind,
            alias_text=alias.text,
            matched_via=MatchedVia.ALIAS,
            strength=alias.strength,
            prior=alias.prior,
        )
        add(alias.text, base)
        upper = alias.text.upper()
        if upper != alias.text:
            add(
                upper,
                SurfaceEntry(
                    company_ticker=base.company_ticker,
                    alias_id=base.alias_id,
                    alias_kind=base.alias_kind,
                    alias_text=base.alias_text,
                    matched_via=base.matched_via,
                    strength=base.strength,
                    prior=base.prior,
                    requires_allcaps_run=True,
                ),
            )

    literal_order = sorted(surfaces, key=lambda text: (-len(text), text))
    surface_re = (
        re.compile("|".join(re.escape(text) for text in literal_order)) if literal_order else None
    )

    tickers = sorted(exchange_entries, key=lambda text: (-len(text), text))
    if tickers:
        ticker_alt = "|".join(re.escape(t) for t in tickers)
        prefix_alt = "|".join(
            re.escape(p) for p in sorted(EXCHANGE_PREFIXES, key=lambda p: (-len(p), p))
        )
        exchange_re = re.compile(rf"\(\s*(?i:{prefix_alt})\s*[:\-]\s*(?P<ticker>{ticker_alt})\s*\)")
        ric_re = re.compile(rf"\(\s*(?P<ticker>{ticker_alt})\.{_RIC_SUFFIX}\s*\)")
    else:
        exchange_re = None
        ric_re = None

    return Matcher(
        surfaces={
            text: tuple(sorted(entries, key=lambda e: e.sort_key))
            for text, entries in surfaces.items()
        },
        surface_re=surface_re,
        exchange_re=exchange_re,
        ric_re=ric_re,
        exchange_entries=dict(exchange_entries),
        companies=by_ticker,
    )
