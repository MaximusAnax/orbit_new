"""FR-4: confidence, IDF-weighted coverage, and the two-signal reject option."""
from __future__ import annotations

import dataclasses

import pytest
from ethos.engine.router import build_index, route
from ethos.service import EthosService
from ethos.store.memory_repo import MemoryRepository

TS = "2026-08-01T12:00:00Z"


def _index_with(corpus, **overrides):
    config = corpus.router_config.model_copy(update=overrides)
    return build_index(corpus.topics, config, corpus.stopwords)


def test_fr4_confidence_zero_when_s1_zero(index):
    result = route("qqqq zzzz xxxx", index)
    assert result.ranked == []
    assert result.confidence == 0.0
    assert result.coverage == 0.0
    assert result.abstained is True


def test_fr4_confidence_is_share_of_top_three(index):
    result = route("is it wrong to lie to a friend?", index)
    top = [t.score for t in result.ranked[:3]]
    expected = top[0] / sum(top) if top else 0.0
    assert result.confidence == pytest.approx(expected)


def test_fr4_coverage_floor_refuses_a_near_miss_moral_question(corpus):
    """A moral question built from vocabulary the taxonomy never heard of is
    refused by kappa even when it scores well on generic moral words."""
    index = _index_with(corpus, tau=0.0, kappa=0.60)
    oos = route("is it wrong to edit the genome of an embryo?", index)
    in_scope = route("is it wrong to lie to spare feelings?", index)
    assert oos.coverage < in_scope.coverage
    assert oos.abstained is True
    assert in_scope.abstained is False


def test_fr4_tau_floor_refuses_a_weak_match(corpus):
    strict = _index_with(corpus, tau=10_000.0)
    assert route("is it wrong to lie?", strict).abstained is True


def test_fr4_abstention_never_depends_on_a_clock_or_randomness(index):
    first = route("should I report my father's tax fraud?", index)
    second = route("should I report my father's tax fraud?", index)
    assert first.model_dump() == second.model_dump()


def test_fr4_index_is_frozen_dataclass(index):
    """No routing state can be mutated after the index is built."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        index.n_topics = 1
    assert dataclasses.replace(index, n_topics=99).n_topics == 99  # copies, never mutates


def test_fr4_forced_topic_bypasses_router(corpus):
    service = EthosService(corpus, MemoryRepository())
    service.init_store(TS)
    result = service.ask("anything at all", TS, topic_id="divorce")
    assert result.answer is not None
    assert result.question.routing is None
    assert result.question.forced_topic_id == "divorce"
    assert result.answer.body.routing.forced is True
    assert result.answer.body.routing.confidence is None
    assert result.answer.body.routing.alternates == []


def test_fr4_refusal_payload_shape(corpus):
    service = EthosService(corpus, MemoryRepository())
    service.init_store(TS)
    result = service.ask(
        "Is CRISPR editing of an embryo's genome killing an unborn person, "
        "murder, or self-defense against inherited disease?",
        TS,
    )
    assert result.answer is None
    assert result.refusal is not None
    assert result.question.outcome.value == "refused_out_of_scope"
    assert len(result.refusal.nearest_topics) <= 3
    assert "ethos topics" in result.refusal.browse_hint
    assert "gene editing" in result.refusal.note
    assert result.question.routing is not None  # routing is recorded for refusals


def test_fr4_refused_question_has_no_answer(corpus):
    repo = MemoryRepository()
    service = EthosService(corpus, repo)
    service.init_store(TS)
    result = service.ask(
        "Is CRISPR editing of an embryo's genome killing an unborn person, "
        "murder, or self-defense against inherited disease?",
        TS,
    )
    assert repo.get_answer_for_question(result.question.id) is None
