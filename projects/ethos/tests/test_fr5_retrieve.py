"""FR-5: perspective retrieval, canonical ordering, and the two disjoint
"missing tradition" lists that must never be conflated."""
from __future__ import annotations

from ethos.engine.retrieve import order_refs, reading_list, retrieve
from ethos.models import PassageRole


def test_fr5_unfiltered_ask_renders_every_covered_tradition(corpus):
    result = retrieve(corpus, "honesty_and_deception", None)
    covered = {
        t.id for t in corpus.traditions
        if ("honesty_and_deception", t.id) in corpus.position_by_cell
    }
    assert {r.position.tradition_id for r in result.rendered} == covered
    assert result.filtered_out == ()


def test_fr5_not_covered_vs_filtered_out(corpus):
    """`not_covered` means the corpus has nothing; `filtered_out` means you
    asked me to leave it out. Conflating them tells the user a falsehood."""
    topic = "honesty_and_deception"
    covered = [
        t.id for t in corpus.traditions if (topic, t.id) in corpus.position_by_cell
    ]
    missing = [
        t.id for t in corpus.traditions if (topic, t.id) not in corpus.position_by_cell
    ]
    requested = [covered[0], missing[0]]
    result = retrieve(corpus, topic, requested)
    assert [r.position.tradition_id for r in result.rendered] == [covered[0]]
    assert result.not_covered == (missing[0],)
    assert set(result.filtered_out) == set(covered[1:])
    assert not set(result.not_covered) & set(result.filtered_out)


def test_fr5_rendered_traditions_follow_canonical_order(corpus):
    order = {t.id: t.order for t in corpus.traditions}
    for topic in corpus.topics:
        rendered = [r.position.tradition_id for r in retrieve(corpus, topic.id, None).rendered]
        assert rendered == sorted(rendered, key=lambda t: order[t])


def test_fr5_passage_role_ordering(corpus):
    """core -> supporting -> complicating, committed list order within a role."""
    for position in corpus.positions:
        roles = [ref.role for ref in order_refs(position)]
        ranks = [
            {PassageRole.core: 0, PassageRole.supporting: 1, PassageRole.complicating: 2}[r]
            for r in roles
        ]
        assert ranks == sorted(ranks)
    position = next(p for p in corpus.positions if len(p.passages) > 2)
    committed = [ref.passage_id for ref in position.passages if ref.role == PassageRole.core]
    ordered = [
        ref.passage_id for ref in order_refs(position) if ref.role == PassageRole.core
    ]
    assert ordered == committed


def test_fr5_nothing_is_improvised(corpus):
    """Retrieval returns committed records only — never a synthesized stand-in."""
    result = retrieve(corpus, "divorce", None)
    for retrieved in result.rendered:
        assert retrieved.position is corpus.position_by_cell[
            ("divorce", retrieved.position.tradition_id)
        ]
        for ref in retrieved.ordered_refs:
            assert retrieved.passages[ref.passage_id] is corpus.passage_by_id[ref.passage_id]


def test_fr5_reading_list_aggregates_and_dedupes(corpus):
    entries = reading_list(corpus, "honesty_and_deception", None)
    keys = [(e.author.casefold(), e.title.casefold(), e.year) for e in entries]
    assert len(keys) == len(set(keys))
    single = reading_list(corpus, "honesty_and_deception", ["christianity"])
    assert set(e.title for e in single) <= set(e.title for e in entries)
