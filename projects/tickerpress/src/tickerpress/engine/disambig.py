"""Disambiguation scoring (SCOPE FR-6) — hard part A, second half.

Strong surfaces (legal names, non-word tickers, cashtags, exchange
parentheticals) are accepted outright: newswire style invented them to be
unambiguous. Every weak surface must earn acceptance from a linear evidence
score over committed lexicons — a hand-auditable Yarowsky-style decision score
rather than a learned model, so a single user can read exactly why "Apple
growers brace for frost" was rejected and add an anti-term the moment a false
positive appears.

The five FR-6 scoring rules are implemented literally:

1. fields are sentence containers, and the title is one sentence — so a
   title-initial surface has ``case_signal = 0``;
2. the +/-12-token window never crosses a field boundary, and ``doc_*`` counts
   the occurrences the window did not (disjoint *by position*);
3. the global cue lexicons and the per-company term lists are independent
   features — a token in both contributes to both;
4. ``coref_strong`` is resolved after every strong candidate in the article is
   known, so scoring order within an article is irrelevant;
5. every candidate, accepted or rejected, keeps its full feature vector.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ..resources import Lexicons
from .detect import Candidate
from .models import AliasKind, Company, MentionFeatures, Strength, TextField
from .normalize import ArticleText, FieldText, tokenize

__all__ = [
    "THETA",
    "WINDOW_TOKENS",
    "ScoredCandidate",
    "TermIndex",
    "build_term_index",
    "extract_features",
    "score_article",
    "score_features",
]

#: The single committed acceptance threshold. There is deliberately no
#: per-alias or per-company override (SCOPE FR-6, non-goal 10); `prior`,
#: `context_terms` and `anti_terms` are the per-surface tuning axes.
THETA = 0.35

#: Half-width, in tokens, of the cue/anti-cue window around a candidate.
WINDOW_TOKENS = 12


@dataclass(frozen=True, slots=True)
class _Occurrence:
    entry: str
    field: TextField
    first_token: int
    last_token: int


@dataclass(frozen=True)
class TermIndex:
    """Lexicon compiled for whole-token, case-folded, multi-word matching."""

    by_first_token: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]

    @property
    def is_empty(self) -> bool:
        return not self.by_first_token


def build_term_index(entries: Iterable[str], lexicons: Lexicons) -> TermIndex:
    """Compile lexicon entries into token sequences keyed by first token."""

    grouped: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for entry in entries:
        tokens = tuple(token.folded for token in tokenize(entry, lexicons))
        if not tokens:
            continue
        grouped.setdefault(tokens[0], []).append((entry, tokens))
    return TermIndex(
        by_first_token={
            first: tuple(sorted(items, key=lambda item: (-len(item[1]), item[0])))
            for first, items in grouped.items()
        }
    )


def _scan_field(field: FieldText, index: TermIndex) -> list[_Occurrence]:
    if index.is_empty:
        return []
    occurrences: list[_Occurrence] = []
    folded = [token.folded for token in field.tokens]
    for position, first in enumerate(folded):
        for entry, tokens in index.by_first_token.get(first, ()):
            end = position + len(tokens)
            if end <= len(folded) and tuple(folded[position:end]) == tokens:
                occurrences.append(
                    _Occurrence(
                        entry=entry,
                        field=field.field,
                        first_token=position,
                        last_token=end - 1,
                    )
                )
    return occurrences


def _scan_article(article: ArticleText, index: TermIndex) -> tuple[_Occurrence, ...]:
    found: list[_Occurrence] = []
    for field in article.fields:
        found.extend(_scan_field(field, index))
    return tuple(found)


def _window_split(
    occurrences: Sequence[_Occurrence],
    field: TextField,
    low: int,
    high: int,
) -> tuple[int, int]:
    """Distinct entries inside the window, and distinct entries outside it.

    Positional disjointness (FR-6 rule 2): an entry occurring both inside and
    outside the window is counted once on each side.
    """

    inside: set[str] = set()
    outside: set[str] = set()
    for occurrence in occurrences:
        in_window = (
            occurrence.field is field
            and occurrence.last_token >= low
            and occurrence.first_token <= high
        )
        (inside if in_window else outside).add(occurrence.entry)
    return len(inside), len(outside)


def _hyphen_compound(field: FieldText, char_end: int) -> int:
    """1 when the surface is immediately followed by ``-`` + a lowercase word."""

    text = field.text
    if char_end < len(text) and text[char_end] == "-":
        nxt = char_end + 1
        if nxt < len(text) and text[nxt].isalpha() and text[nxt].islower():
            return 1
    return 0


def extract_features(
    candidate: Candidate,
    article: ArticleText,
    company: Company | None,
    *,
    coref_strong: bool,
    cue_occurrences: Sequence[_Occurrence],
    anti_occurrences: Sequence[_Occurrence],
    context_term_count: int,
    anti_term_count: int,
) -> MentionFeatures:
    """Build the FR-6 feature vector for one weak candidate."""

    field = article.field_of(candidate.field)
    if field is None:  # pragma: no cover - candidates always come from a field
        raise ValueError(f"candidate references missing field {candidate.field}")

    low = candidate.first_token - WINDOW_TOKENS
    high = candidate.last_token + WINDOW_TOKENS
    window_cues, doc_cues = _window_split(cue_occurrences, candidate.field, low, high)
    window_antis, doc_antis = _window_split(anti_occurrences, candidate.field, low, high)

    in_allcaps = field.in_allcaps_run(candidate.first_token, candidate.last_token)
    case_signal = int(
        candidate.surface == candidate.alias_text
        and not field.is_sentence_initial(candidate.first_token)
        and not in_allcaps
    )
    allcaps_run = int(candidate.alias_kind is AliasKind.TICKER_SYMBOL and in_allcaps)

    return MentionFeatures(
        prior=candidate.prior,
        coref_strong=int(coref_strong),
        case_signal=case_signal,
        window_cues=window_cues,
        window_antis=window_antis,
        doc_cues=doc_cues,
        doc_antis=doc_antis,
        ctx_terms=context_term_count if company is not None else 0,
        anti_terms=anti_term_count if company is not None else 0,
        hyphen_compound=_hyphen_compound(field, candidate.char_end),
        allcaps_run=allcaps_run,
    )


def score_features(features: MentionFeatures) -> float:
    """The FR-6 linear score, clamped to ``[0, 1]``.

    Rounded to six decimals so the stored value — and therefore the
    ``accepted == (score >= threshold)`` invariant — is free of float noise and
    identical across processes.
    """

    raw = (
        features.prior
        + 0.50 * features.coref_strong
        + 0.10 * features.case_signal
        + 0.10 * min(3, features.window_cues)
        - 0.15 * min(3, features.window_antis)
        + 0.05 * min(3, features.doc_cues)
        - 0.10 * min(2, features.doc_antis)
        + 0.15 * min(2, features.ctx_terms)
        - 0.20 * min(2, features.anti_terms)
        - 0.20 * features.hyphen_compound
        - 0.20 * features.allcaps_run
    )
    return round(min(1.0, max(0.0, raw)), 6)


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """A candidate with its verdict; ``features`` is ``None`` when strong."""

    candidate: Candidate
    features: MentionFeatures | None
    score: float
    threshold: float
    accepted: bool


def _distinct_term_hits(terms: Sequence[str], article: ArticleText, lexicons: Lexicons) -> int:
    if not terms:
        return 0
    index = build_term_index(terms, lexicons)
    return len({occurrence.entry for occurrence in _scan_article(article, index)})


def score_article(
    article: ArticleText,
    candidates: Sequence[Candidate],
    companies: Mapping[str, Company],
    lexicons: Lexicons,
    *,
    threshold: float = THETA,
) -> tuple[ScoredCandidate, ...]:
    """Score every candidate of one article, preserving FR-5 emission order."""

    if not candidates:
        return ()

    coref = {c.company_ticker for c in candidates if c.strength is Strength.STRONG}
    cue_occurrences = _scan_article(article, build_term_index(lexicons.corporate_cues, lexicons))
    anti_occurrences = _scan_article(article, build_term_index(lexicons.anti_cues, lexicons))

    term_hits: dict[str, tuple[int, int]] = {}
    for ticker in sorted({c.company_ticker for c in candidates}):
        company = companies.get(ticker)
        if company is None:
            term_hits[ticker] = (0, 0)
            continue
        term_hits[ticker] = (
            _distinct_term_hits(company.context_terms, article, lexicons),
            _distinct_term_hits(company.anti_terms, article, lexicons),
        )

    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        if candidate.strength is Strength.STRONG:
            scored.append(
                ScoredCandidate(
                    candidate=candidate,
                    features=None,
                    score=1.0,
                    threshold=threshold,
                    accepted=threshold <= 1.0,
                )
            )
            continue
        context_hits, anti_hits = term_hits[candidate.company_ticker]
        features = extract_features(
            candidate,
            article,
            companies.get(candidate.company_ticker),
            coref_strong=candidate.company_ticker in coref,
            cue_occurrences=cue_occurrences,
            anti_occurrences=anti_occurrences,
            context_term_count=context_hits,
            anti_term_count=anti_hits,
        )
        score = score_features(features)
        scored.append(
            ScoredCandidate(
                candidate=candidate,
                features=features,
                score=score,
                threshold=threshold,
                accepted=score >= threshold,
            )
        )
    return tuple(scored)
