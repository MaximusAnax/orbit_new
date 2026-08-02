"""Relevance scoring (SCOPE FR-8) — hard part A, ranking facet.

Placement dominates count: a headline appearance outranks a passing list
mention. The 0-100 scale and that shape follow commercial news-analytics
relevance (RavenPack's 0-100 field, Thomson Reuters News Analytics' 0-1
equivalent); the lede weighting follows the inverted-pyramid convention that
the opening carries the story. Story relevance is the *max* over the story's
copies, so a syndicated copy that drops the headline never depresses the story.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .detect import Candidate
from .models import TextField, round_half_up
from .normalize import ArticleText

__all__ = [
    "RelevanceComponents",
    "compute_components",
    "lede_token_limit",
    "relevance_of",
    "story_relevance",
]


@dataclass(frozen=True, slots=True)
class RelevanceComponents:
    """The audit trail behind one Appearance row."""

    mention_count: int
    title_hit: bool
    lede_hit: bool
    relevance: int


def lede_token_limit(content_token_count: int) -> int:
    """``ceil(0.25 · content_token_count)`` — the content-field lede window."""

    return math.ceil(0.25 * content_token_count)


def relevance_of(title_hit: bool, lede_hit: bool, mention_count: int) -> int:
    """``round_half_up(100·(0.50·title + 0.25·lede + 0.25·min(1, n/4)))``."""

    if mention_count < 1:
        raise ValueError("relevance is defined only where at least one mention was accepted")
    return round_half_up(
        100.0
        * (0.50 * int(title_hit) + 0.25 * int(lede_hit) + 0.25 * min(1.0, mention_count / 4.0))
    )


def _in_summary_lede(candidate: Candidate, article: ArticleText) -> bool:
    sentences = article.summary.sentences
    if not sentences:
        return False
    first = sentences[0]
    return first.first_token <= candidate.first_token <= first.last_token


def _in_content_lede(candidate: Candidate, article: ArticleText) -> bool:
    content = article.content
    if content is None:
        return False
    return candidate.first_token < lede_token_limit(content.token_count)


def compute_components(
    accepted: Sequence[Candidate],
    article: ArticleText,
) -> RelevanceComponents | None:
    """FR-8 components for one (article, company).

    Returns ``None`` when no mention was accepted — DATA_MODEL §2.8 says an
    Appearance row exists exactly when ``n >= 1``.
    """

    if not accepted:
        return None
    title_hit = any(candidate.field is TextField.TITLE for candidate in accepted)
    lede_hit = any(
        (candidate.field is TextField.SUMMARY and _in_summary_lede(candidate, article))
        or (candidate.field is TextField.CONTENT and _in_content_lede(candidate, article))
        for candidate in accepted
    )
    count = len(accepted)
    return RelevanceComponents(
        mention_count=count,
        title_hit=title_hit,
        lede_hit=lede_hit,
        relevance=relevance_of(title_hit, lede_hit, count),
    )


def story_relevance(values: Iterable[int]) -> int:
    """Story-level relevance for a company: the max over its member articles."""

    scores = list(values)
    if not scores:
        raise ValueError("a story with no appearances has no relevance")
    return max(scores)
