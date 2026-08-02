"""Syndication dedup (SCOPE FR-7) — hard part B, first half.

w-shingling with exact Jaccard resemblance (Broder 1997). MinHash/SimHash
(Broder et al. 1998; Charikar 2002; Manku, Jain & Das Sarma 2007) exist to
*approximate* this at web scale; a personal tool comparing a new item against a
+/-7-day window of a few hundred articles can afford the exact computation, and
exactness buys bit-for-bit determinism, which the M5 gate demands.

Story assignment is incremental and never retroactive (SCOPE D6): an article
joins the argmax story or opens a new one, and stories never merge afterwards —
the delivery ledger already references them, and rewriting that history is an
exactly-once violation waiting to happen.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..resources import Lexicons
from .normalize import ArticleText, analyze_article_text

__all__ = [
    "DEDUP_THRESHOLD",
    "DEDUP_WINDOW_DAYS",
    "SHINGLE_SIZE",
    "DedupDecision",
    "PendingArticle",
    "StoredArticleView",
    "dedup_tokens",
    "jaccard",
    "shingle_set",
    "shingles_from_texts",
    "shingles_of",
]

#: Contiguous w-shingle width, in tokens.
SHINGLE_SIZE = 3
#: Resemblance at or above which a new article joins an existing story.
DEDUP_THRESHOLD = 0.60
#: Half-width of the comparison window, in days.
DEDUP_WINDOW_DAYS = 7

Shingle = tuple[str, ...]


def dedup_tokens(article: ArticleText) -> tuple[str, ...]:
    """Case-folded, punctuation-free token stream over title + summary + content."""

    tokens: list[str] = []
    for field in article.fields:
        tokens.extend(token.folded for token in field.tokens)
    return tuple(tokens)


def shingle_set(tokens: Sequence[str], size: int = SHINGLE_SIZE) -> frozenset[Shingle]:
    """Contiguous ``size``-token shingles.

    A document shorter than one shingle contributes its whole token tuple, so
    two identical short items still resemble each other at J = 1.0 instead of
    silently comparing two empty sets.
    """

    if not tokens:
        return frozenset()
    if len(tokens) < size:
        return frozenset({tuple(tokens)})
    return frozenset(tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1))


def shingles_of(article: ArticleText, size: int = SHINGLE_SIZE) -> frozenset[Shingle]:
    return shingle_set(dedup_tokens(article), size)


def shingles_from_texts(
    title: str,
    summary: str,
    content: str | None,
    lexicons: Lexicons,
    size: int = SHINGLE_SIZE,
) -> frozenset[Shingle]:
    """Convenience for callers holding normalized field text (evals, explain)."""

    return shingles_of(analyze_article_text(title, summary, content, lexicons), size)


def jaccard(left: frozenset[Shingle], right: frozenset[Shingle]) -> float:
    """Exact Jaccard resemblance: intersection size over union size.

    Two empty shingle sets resemble each other at 0.0 rather than 1.0 — an
    article with no tokens should not cluster with every other empty one.
    """

    if not left or not right:
        return 0.0
    union = len(left | right)
    if union == 0:  # pragma: no cover - unreachable given the guard above
        return 0.0
    return len(left & right) / union


@dataclass(frozen=True, slots=True)
class StoredArticleView:
    """The fields of an archived article that dedup needs."""

    article_id: int
    story_id: int
    published_at: datetime
    canonical_url: str
    content_sha256: str
    shingles: frozenset[Shingle]


@dataclass(frozen=True, slots=True)
class PendingArticle:
    """The article being ingested, before it has an id."""

    published_at: datetime
    canonical_url: str
    content_sha256: str
    shingles: frozenset[Shingle]


@dataclass(frozen=True, slots=True)
class DedupDecision:
    """Where the new article lands, and the evidence for it."""

    story_id: int | None
    similarity: float | None
    matched_article_id: int | None

    @property
    def opens_new_story(self) -> bool:
        return self.story_id is None


def _within_window(
    pending: PendingArticle,
    candidates: Iterable[StoredArticleView],
    window_days: int,
) -> list[StoredArticleView]:
    window = timedelta(days=window_days)
    inside = [
        candidate
        for candidate in candidates
        if abs(candidate.published_at - pending.published_at) <= window
    ]
    inside.sort(key=lambda candidate: candidate.article_id)
    return inside


def assign_story(
    pending: PendingArticle,
    candidates: Iterable[StoredArticleView],
    *,
    threshold: float = DEDUP_THRESHOLD,
    window_days: int = DEDUP_WINDOW_DAYS,
) -> DedupDecision:
    """Decide which story a new article joins.

    Order of resolution (FR-7, determinism block):

    1. equal ``canonical_url`` inside the window — smallest article id wins;
    2. equal ``content_sha256`` inside the window — smallest article id wins;
       ``canonical_url`` beats ``content_sha256`` when both fire on different
       articles, and both fast paths record ``dedup_similarity = 1.0``;
    3. otherwise the argmax of exact Jaccard over the window, compared in
       ascending article id so a tie resolves to the smallest id;
    4. join if ``J >= threshold``, else open a new story.

    Both fast paths are window-limited too, so a year-old identical URL cannot
    resurrect a dead story.
    """

    in_window = _within_window(pending, candidates, window_days)
    if not in_window:
        return DedupDecision(story_id=None, similarity=None, matched_article_id=None)

    for match in in_window:
        if match.canonical_url and match.canonical_url == pending.canonical_url:
            return DedupDecision(
                story_id=match.story_id, similarity=1.0, matched_article_id=match.article_id
            )
    for match in in_window:
        if match.content_sha256 and match.content_sha256 == pending.content_sha256:
            return DedupDecision(
                story_id=match.story_id, similarity=1.0, matched_article_id=match.article_id
            )

    best: StoredArticleView | None = None
    best_similarity = 0.0
    for candidate in in_window:
        similarity = jaccard(pending.shingles, candidate.shingles)
        if best is None or similarity > best_similarity:
            best = candidate
            best_similarity = similarity

    if best is not None and best_similarity >= threshold:
        return DedupDecision(
            story_id=best.story_id,
            similarity=round(best_similarity, 6),
            matched_article_id=best.article_id,
        )
    return DedupDecision(story_id=None, similarity=None, matched_article_id=None)
