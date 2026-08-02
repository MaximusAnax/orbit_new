"""TopicRouter Protocol + the offline default LexicalRouter (FR-3/FR-4).

The Protocol exists so an embedding router can be added later without a
rewrite (SCOPE non-goal 6); no live implementation ships this pass.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ethos.engine.router import TopicIndex, route
from ethos.models import RoutingResult


@runtime_checkable
class TopicRouter(Protocol):
    def route(self, question: str, index: TopicIndex) -> RoutingResult: ...


class LexicalRouter:
    """Deterministic BM25 + phrase-lexicon router (engine.router wrapper)."""

    def route(self, question: str, index: TopicIndex) -> RoutingResult:
        return route(question, index)
