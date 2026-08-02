"""FR-3: BM25 + lexicon topic routing over hand-expanded topic documents."""
from __future__ import annotations

import math

from ethos.engine.normalize import normalize
from ethos.engine.router import (
    build_index,
    coverage,
    index_to_json,
    route,
    score_topic,
)
from ethos.models import Keyword, RouterConfig, Topic

CONFIG = RouterConfig(k1=1.5, b=0.75, w_phrase=2.0, tau=1.0, kappa=0.2, tuning_note="test")


def _toy_topics() -> list[Topic]:
    def topic(tid: str, title: str, description: str, keywords: list[tuple[str, int]]) -> Topic:
        return Topic(
            id=tid, title=title, description=description,
            question_forms=[f"is {title} wrong?"],
            keywords=[Keyword(term=t, weight=w) for t, w in keywords],
            related_topics=[], sensitive=False, safeguard_ids=[],
        )

    return [
        topic("alpha", "Alpha", "lying and deceit", [("lie", 3), ("white lie", 2)]),
        topic("beta", "Beta", "stealing and property", [("steal", 3)]),
        topic("gamma", "Gamma", "lying about property", [("lie", 1), ("steal", 1)]),
    ]


def test_fr3_bm25_scores_known_case():
    """Score of a single term reproduces the Okapi formula by hand."""
    index = build_index(_toy_topics(), CONFIG, frozenset({"and"}))
    term = normalize("steal", frozenset({"and"}))[0]
    tf = index.tf["beta"][term]
    dl = index.doc_len["beta"]
    idf = math.log(1.0 + (3 - index.df[term] + 0.5) / (index.df[term] + 0.5))
    expected = idf * (tf * (CONFIG.k1 + 1)) / (
        tf + CONFIG.k1 * (1 - CONFIG.b + CONFIG.b * dl / index.avgdl)
    )
    assert score_topic(index, "beta", [term]) == expected


def test_fr3_rarer_terms_score_higher():
    index = build_index(_toy_topics(), CONFIG, frozenset())
    deceit = normalize("deceit", frozenset())[0]  # alpha only
    lie = normalize("lie", frozenset())[0]  # alpha and gamma
    assert index.df[deceit] == 1 and index.df[lie] == 2
    assert index.idf(deceit) > index.idf(lie)


def test_fr3_unseen_term_takes_maximum_idf():
    index = build_index(_toy_topics(), CONFIG, frozenset())
    assert index.idf("embryo") == math.log(1.0 + (3 + 0.5) / 0.5)


def test_fr3_phrase_bonus_fires_once_on_contiguous_match():
    index = build_index(_toy_topics(), CONFIG, frozenset())
    tokens = normalize("was that a white lie", frozenset())
    without = normalize("was that a lie about white things", frozenset())
    assert score_topic(index, "alpha", tokens) - score_topic(index, "alpha", without) > 0


def test_fr3_tie_break_lexicographic():
    """Equal scores rank by topic id ascending, never by dict order."""
    topics = _toy_topics()
    for t in topics:
        t.description = "identical text"
        t.keywords = [Keyword(term="identical", weight=1)]
        t.question_forms = ["identical text"]
    index = build_index(topics, CONFIG, frozenset())
    ranked = route("identical", index).ranked
    assert [r.topic_id for r in ranked] == ["alpha", "beta", "gamma"]


def test_fr3_zero_scoring_topics_are_omitted():
    index = build_index(_toy_topics(), CONFIG, frozenset())
    ranked = route("steal", index).ranked
    assert [r.topic_id for r in ranked] == ["beta", "gamma"]


def test_fr3_index_rebuild_is_byte_stable(corpus):
    a = build_index(corpus.topics, corpus.router_config, corpus.stopwords)
    b = build_index(list(reversed(corpus.topics)), corpus.router_config, corpus.stopwords)
    assert index_to_json(a) == index_to_json(b)


def test_fr3_coverage_is_idf_weighted(index):
    """A question of unseen vocabulary covers little, even with moral words."""
    known = coverage(index, "honesty_and_deception", normalize(
        "is it wrong to lie to a friend", index.stopwords))
    unknown = coverage(index, "honesty_and_deception", normalize(
        "is gene editing embryos with crispr wrong", index.stopwords))
    assert known > unknown


def test_fr3_routes_the_committed_corpus(index):
    result = route("is it wrong to lie?", index)
    assert result.ranked[0].topic_id == "honesty_and_deception"
