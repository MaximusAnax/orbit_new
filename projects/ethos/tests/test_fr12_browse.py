"""FR-12: browse and corpus stats — the surfaces that make the corpus
inspectable, and the substance numbers the C-gates enforce."""
from __future__ import annotations

import pytest
from ethos.engine.corpus import corpus_stats
from ethos.service import EthosService, UnknownTopicError, UnknownTraditionError
from ethos.store.memory_repo import MemoryRepository


@pytest.fixture(scope="module")
def service(corpus):
    store = EthosService(corpus, MemoryRepository())
    store.init_store("2026-08-01T12:00:00Z")
    return store


def test_fr12_coverage_matrix(service, corpus):
    matrix = service.coverage_matrix()
    assert set(matrix) == {t.id for t in corpus.topics}
    for topic_id, row in matrix.items():
        assert set(row) == {t.id for t in corpus.traditions}
        covered = [t for t, stance in row.items() if stance is not None]
        assert len(covered) >= 6
        for tradition_id in covered:
            assert row[tradition_id] == corpus.position_by_cell[
                (topic_id, tradition_id)
            ].stance.value


def test_fr12_stats_reports_substance_ratios(service, corpus):
    stats = service.substance_stats()
    engine = corpus_stats(corpus)
    for key in ("complicating_share", "quoted_core_share", "reference_only_share"):
        assert stats[key] == engine[key]
    assert stats["max_core_reuse"] <= 3
    assert stats["distinct_cited_passages"] >= 6 * stats["topics"]
    assert stats["distinct_reading_entries"] >= 120
    assert stats["min_stances_per_topic"] >= 3
    assert stats["corpus_version"] == corpus.corpus_version


def test_fr12_reading_aggregation_dedup(service):
    entries = service.reading_list("honesty_and_deception", None)
    keys = [(e["entry"].author.casefold(), e["entry"].title.casefold(), e["entry"].year)
            for e in entries]
    assert len(keys) == len(set(keys))
    assert all(e["tradition_id"] for e in entries)


def test_fr12_topic_detail_lists_covered_traditions_and_safeguards(service):
    detail = service.topic_detail("suicide_and_self_harm")
    assert detail["topic"].id == "suicide_and_self_harm"
    assert len(detail["covered"]) >= 6
    assert [s.id for s in detail["safeguards"]] == ["crisis_resources"]
    assert detail["reading"]


def test_fr12_topics_filtered_by_tradition(service, corpus):
    stoic = service.topics_for_tradition("stoicism")
    assert stoic
    for topic in stoic:
        assert (topic.id, "stoicism") in corpus.position_by_cell
    assert len(service.topics_for_tradition(None)) == len(corpus.topics)


def test_fr12_unknown_ids_raise(service):
    with pytest.raises(UnknownTopicError):
        service.topic_detail("no_such_topic")
    with pytest.raises(UnknownTraditionError):
        service.topics_for_tradition("no_such_tradition")
