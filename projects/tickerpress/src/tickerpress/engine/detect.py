"""Candidate mention scanning (SCOPE FR-5) — hard part A, first half.

Scanning is case-sensitive by construction: stored surfaces carry their intended
capitalization, so a lower-cased surface can never match. The three recognizers
(literal alias, cashtag, newswire parenthetical) are resolved against each other
by an explicit precedence so that ``Apple Inc. (NASDAQ: AAPL)`` yields one
legal-name hit and one exchange-qualified hit — not four overlapping ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .models import FIELD_ORDER, AliasKind, MatchedVia, Strength, TextField
from .normalize import FieldText
from .watchlist import Matcher, SurfaceEntry

__all__ = ["Candidate", "scan"]

#: Recognizer precedence when two hits cover exactly the same span.
_VIA_RANK = {
    MatchedVia.EXCHANGE_QUALIFIED: 0,
    MatchedVia.CASHTAG: 1,
    MatchedVia.ALIAS: 2,
}

_FIELD_RANK = {field: index for index, field in enumerate(FIELD_ORDER)}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One surface hit, before disambiguation."""

    company_ticker: str
    alias_id: int | None
    alias_kind: AliasKind
    alias_text: str
    field: TextField
    char_start: int
    char_end: int
    surface: str
    matched_via: MatchedVia
    strength: Strength
    prior: float
    first_token: int
    last_token: int

    @property
    def order_key(self) -> tuple[int, int, int]:
        """FR-5 emission order: field, then char_start, then alias_id."""

        return (
            _FIELD_RANK[self.field],
            self.char_start,
            self.alias_id if self.alias_id is not None else -1,
        )


def _is_word_char(text: str, index: int) -> bool:
    return 0 <= index < len(text) and (text[index].isalnum() or text[index] == "_")


def _token_bounded(text: str, start: int, end: int) -> bool:
    """A hit must not continue a token on either side."""

    return not _is_word_char(text, start - 1) and not _is_word_char(text, end)


def _build(
    entry: SurfaceEntry,
    field: FieldText,
    start: int,
    end: int,
) -> Candidate | None:
    span = field.token_span(start, end)
    if span is None:
        return None
    first_token, last_token = span
    if entry.requires_allcaps_run and not field.in_allcaps_run(first_token, last_token):
        return None
    return Candidate(
        company_ticker=entry.company_ticker,
        alias_id=entry.alias_id,
        alias_kind=entry.alias_kind,
        alias_text=entry.alias_text,
        field=field.field,
        char_start=start,
        char_end=end,
        surface=field.text[start:end],
        matched_via=entry.matched_via,
        strength=entry.strength,
        prior=entry.prior,
        first_token=first_token,
        last_token=last_token,
    )


def _scan_field(field: FieldText, matcher: Matcher) -> list[Candidate]:
    found: list[Candidate] = []
    text = field.text
    if not text:
        return found

    if matcher.surface_re is not None:
        for match in matcher.surface_re.finditer(text):
            start, end = match.start(), match.end()
            if not _token_bounded(text, start, end):
                continue
            for entry in matcher.surfaces.get(match.group(), ()):
                candidate = _build(entry, field, start, end)
                if candidate is not None:
                    found.append(candidate)

    for pattern in (matcher.exchange_re, matcher.ric_re):
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            ticker = match.group("ticker")
            entry = matcher.exchange_entries.get(ticker)
            if entry is None:  # pragma: no cover - alternation is built from these keys
                continue
            candidate = _build(entry, field, match.start("ticker"), match.end("ticker"))
            if candidate is not None:
                found.append(candidate)

    return found


def _resolve_overlaps(candidates: list[Candidate]) -> list[Candidate]:
    """Keep the strongest, longest hit when hits for one company overlap.

    Longest span wins (so ``Apple`` inside ``Apple Inc.`` is not a second
    mention); on an equal span the recognizer precedence decides (so the ticker
    inside ``(NASDAQ: AAPL)`` is recorded as ``exchange_qualified``), then the
    smallest alias id. Companies are resolved independently — an overlap
    between two different companies' surfaces is not a conflict.
    """

    ordered = sorted(
        candidates,
        key=lambda c: (
            _FIELD_RANK[c.field],
            c.char_start,
            -(c.char_end - c.char_start),
            _VIA_RANK[c.matched_via],
            c.alias_id if c.alias_id is not None else -1,
        ),
    )
    kept: list[Candidate] = []
    last_end: dict[tuple[str, TextField], int] = {}
    for candidate in ordered:
        key = (candidate.company_ticker, candidate.field)
        if candidate.char_start < last_end.get(key, -1):
            continue
        kept.append(candidate)
        last_end[key] = candidate.char_end
    return kept


def scan(fields: Sequence[FieldText], matcher: Matcher) -> tuple[Candidate, ...]:
    """Scan the article's fields and emit candidates in FR-5 order."""

    if matcher.is_empty:
        return ()
    found: list[Candidate] = []
    for field in fields:
        found.extend(_scan_field(field, matcher))
    return tuple(sorted(_resolve_overlaps(found), key=lambda c: c.order_key))
