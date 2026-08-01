"""FR-5: perspective retrieval — positions, ordered passages, reading lists.

`not_covered` (requested but no curated position) and `filtered_out` (has a
position but excluded by the caller's filter) are disjoint and never conflated.
"""
from __future__ import annotations

from dataclasses import dataclass

from ethos.corpus import Corpus
from ethos.models import Passage, PassageRef, PassageRole, Position

_ROLE_ORDER = {PassageRole.core: 0, PassageRole.supporting: 1, PassageRole.complicating: 2}


@dataclass(frozen=True)
class RetrievedPerspective:
    position: Position
    ordered_refs: tuple[PassageRef, ...]
    passages: dict[str, Passage]


@dataclass(frozen=True)
class Retrieval:
    topic_id: str
    rendered: tuple[RetrievedPerspective, ...]  # canonical Tradition.order
    not_covered: tuple[str, ...]
    filtered_out: tuple[str, ...]


def order_refs(position: Position) -> tuple[PassageRef, ...]:
    """core -> supporting -> complicating; committed list order within a role."""
    indexed = list(enumerate(position.passages))
    indexed.sort(key=lambda pair: (_ROLE_ORDER[pair[1].role], pair[0]))
    return tuple(ref for _, ref in indexed)


def retrieve(corpus: Corpus, topic_id: str, requested: list[str] | None) -> Retrieval:
    canonical = [t.id for t in corpus.traditions]  # already sorted by Tradition.order
    requested_set = set(requested) if requested is not None else set(canonical)
    have_position = {
        t for t in canonical if (topic_id, t) in corpus.position_by_cell
    }
    rendered_ids = [t for t in canonical if t in requested_set and t in have_position]
    not_covered = tuple(t for t in canonical if t in requested_set and t not in have_position)
    filtered_out = tuple(t for t in canonical if t in have_position and t not in requested_set)
    rendered = []
    for tid in rendered_ids:
        position = corpus.position_by_cell[(topic_id, tid)]
        refs = order_refs(position)
        passages = {ref.passage_id: corpus.passage_by_id[ref.passage_id] for ref in refs}
        rendered.append(
            RetrievedPerspective(position=position, ordered_refs=refs, passages=passages)
        )
    return Retrieval(
        topic_id=topic_id,
        rendered=tuple(rendered),
        not_covered=not_covered,
        filtered_out=filtered_out,
    )
