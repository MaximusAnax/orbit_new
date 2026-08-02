"""FR-3/FR-4: lexical topic routing — BM25 + phrase bonus, confidence,
IDF-weighted query coverage, and two-signal abstention. Pure and deterministic;
ties break lexicographically by topic id.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from ethos.engine.normalize import normalize
from ethos.models import RouterConfig, RoutingResult, Topic, TopicScore


@dataclass(frozen=True)
class TopicIndex:
    topic_ids: tuple[str, ...]
    tf: dict[str, dict[str, int]]  # topic_id -> term -> frequency
    doc_len: dict[str, int]
    avgdl: float
    df: dict[str, int]  # term -> topic-document frequency
    n_topics: int
    phrases: dict[str, tuple[tuple[tuple[str, ...], int], ...]]
    config: RouterConfig
    stopwords: frozenset[str]

    def idf(self, term: str) -> float:
        n_t = self.df.get(term, 0)
        return math.log(1.0 + (self.n_topics - n_t + 0.5) / (n_t + 0.5))


def build_index(
    topics: list[Topic], config: RouterConfig, stopwords: frozenset[str]
) -> TopicIndex:
    topic_ids = tuple(sorted(t.id for t in topics))
    by_id = {t.id: t for t in topics}
    tf: dict[str, dict[str, int]] = {}
    doc_len: dict[str, int] = {}
    phrases: dict[str, tuple[tuple[tuple[str, ...], int], ...]] = {}
    for tid in topic_ids:
        topic = by_id[tid]
        tokens: list[str] = []
        tokens.extend(normalize(topic.title, stopwords))
        tokens.extend(normalize(topic.description, stopwords))
        for form in topic.question_forms:
            tokens.extend(normalize(form, stopwords))
        phrase_list: list[tuple[tuple[str, ...], int]] = []
        for kw in topic.keywords:
            kw_tokens = normalize(kw.term, stopwords)
            for _ in range(kw.weight):
                tokens.extend(kw_tokens)
            if len(kw_tokens) >= 2:
                phrase_list.append((tuple(kw_tokens), kw.weight))
        counts: dict[str, int] = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0) + 1
        tf[tid] = counts
        doc_len[tid] = len(tokens)
        phrases[tid] = tuple(phrase_list)
    df: dict[str, int] = {}
    for tid in topic_ids:
        for term in tf[tid]:
            df[term] = df.get(term, 0) + 1
    avgdl = sum(doc_len.values()) / len(topic_ids) if topic_ids else 0.0
    return TopicIndex(
        topic_ids=topic_ids,
        tf=tf,
        doc_len=doc_len,
        avgdl=avgdl,
        df=df,
        n_topics=len(topic_ids),
        phrases=phrases,
        config=config,
        stopwords=stopwords,
    )


def index_to_json(index: TopicIndex) -> str:
    """Deterministic serialization used by the D0(c) byte-stability gate."""
    payload = {
        "topic_ids": list(index.topic_ids),
        "tf": {t: dict(sorted(c.items())) for t, c in sorted(index.tf.items())},
        "doc_len": dict(sorted(index.doc_len.items())),
        "avgdl": index.avgdl,
        "df": dict(sorted(index.df.items())),
        "n_topics": index.n_topics,
        "phrases": {
            t: [[list(seq), w] for seq, w in ph] for t, ph in sorted(index.phrases.items())
        },
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _contains_seq(haystack: list[str], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return any(tuple(haystack[i : i + n]) == needle for i in range(len(haystack) - n + 1))


def score_topic(index: TopicIndex, topic_id: str, query_tokens: list[str]) -> float:
    cfg = index.config
    counts = index.tf[topic_id]
    dl = index.doc_len[topic_id]
    norm = cfg.k1 * (1.0 - cfg.b + cfg.b * (dl / index.avgdl if index.avgdl else 0.0))
    score = 0.0
    for term in sorted(set(query_tokens)):
        f = counts.get(term, 0)
        if f:
            score += index.idf(term) * (f * (cfg.k1 + 1.0)) / (f + norm)
    for seq, weight in index.phrases[topic_id]:
        if _contains_seq(query_tokens, seq):
            score += cfg.w_phrase * weight
    return score


def coverage(index: TopicIndex, topic_id: str, query_tokens: list[str]) -> float:
    distinct = sorted(set(query_tokens))
    if not distinct:
        return 0.0
    total = sum(index.idf(t) for t in distinct)
    covered = sum(index.idf(t) for t in distinct if t in index.tf[topic_id])
    return covered / total if total else 0.0


def route(question: str, index: TopicIndex) -> RoutingResult:
    tokens = normalize(question, index.stopwords)
    scored = [(score_topic(index, tid, tokens), tid) for tid in index.topic_ids]
    ranked = sorted(
        ((s, tid) for s, tid in scored if s > 0.0), key=lambda st: (-st[0], st[1])
    )
    s1 = ranked[0][0] if ranked else 0.0
    s2 = ranked[1][0] if len(ranked) > 1 else 0.0
    s3 = ranked[2][0] if len(ranked) > 2 else 0.0
    confidence = s1 / (s1 + s2 + s3) if s1 > 0.0 else 0.0
    cov = coverage(index, ranked[0][1], tokens) if ranked else 0.0
    abstained = s1 < index.config.tau or cov < index.config.kappa
    return RoutingResult(
        ranked=[TopicScore(topic_id=tid, score=s) for s, tid in ranked[:5]],
        confidence=confidence,
        coverage=cov,
        abstained=abstained,
    )
