"""FR-11: persistence. Both backends run the same contract, because the
verified-only rule and the stale-corpus guard are storage invariants."""
from __future__ import annotations

import sqlite3
import threading

import pytest
from ethos.models import Answer, AnswerOptions, CorpusMeta, Question, QuestionOutcome
from ethos.service import EthosService
from ethos.store.memory_repo import MemoryRepository
from ethos.store.repository import StaleCorpusError, UnverifiedAnswerError
from ethos.store.sqlite_repo import SqliteRepository

TS = "2026-08-01T12:00:00Z"


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    if request.param == "memory":
        yield MemoryRepository()
        return
    store = SqliteRepository(tmp_path / "ethos.db")
    yield store
    store.close()


def _answer(corpus, compose, verified: bool = True) -> tuple[Question, Answer]:
    body, rendered = compose("honesty_and_deception")
    question = Question(
        text="is lying wrong?", asked_at=TS, outcome=QuestionOutcome.answered,
        forced_topic_id="honesty_and_deception", routing=None,
    )
    answer = Answer(
        question_id=1, created_at=TS, topic_id="honesty_and_deception",
        options=AnswerOptions(traditions=None, polish=False),
        corpus_version=corpus.corpus_version, composer_version=body.composer_version,
        polish_used=False, polish_fell_back=False, verified=verified,
        body=body, rendered_text=rendered,
    )
    return question, answer


def test_fr11_verified_false_rejected(repo, corpus, compose):
    question, answer = _answer(corpus, compose, verified=False)
    stored = repo.add_question(question)
    with pytest.raises(UnverifiedAnswerError):
        repo.add_answer(answer.model_copy(update={"question_id": stored.id}))
    assert repo.get_answer_for_question(stored.id) is None


def test_fr11_answer_round_trips(repo, corpus, compose):
    question, answer = _answer(corpus, compose)
    stored_q = repo.add_question(question)
    stored_a = repo.add_answer(answer.model_copy(update={"question_id": stored_q.id}))
    fetched = repo.get_answer(stored_a.id)
    assert fetched is not None
    assert fetched.rendered_text == answer.rendered_text
    assert fetched.body.model_dump_json() == answer.body.model_dump_json()


def test_fr11_answer_immutable(repo, corpus, compose, tmp_path):
    question, answer = _answer(corpus, compose)
    stored_q = repo.add_question(question)
    stored_a = repo.add_answer(answer.model_copy(update={"question_id": stored_q.id}))
    fetched = repo.get_answer(stored_a.id)
    fetched.rendered_text = "tampered"  # a local copy, never the stored row
    assert repo.get_answer(stored_a.id).rendered_text == answer.rendered_text
    if isinstance(repo, SqliteRepository):
        with pytest.raises(sqlite3.IntegrityError):
            repo._conn.execute("UPDATE answer SET rendered_text = 'x' WHERE id = 1")


def test_fr11_stale_corpus_guard(repo, corpus):
    repo.set_corpus_meta(CorpusMeta(corpus_version=corpus.corpus_version, loaded_at=TS))
    repo.guard_corpus(corpus.corpus_version)
    with pytest.raises(StaleCorpusError):
        repo.guard_corpus("a-different-digest")


def test_fr11_guard_fires_before_any_load(repo):
    with pytest.raises(StaleCorpusError):
        repo.guard_corpus("anything")


def test_fr11_re_asking_creates_a_new_question_and_answer(corpus, tmp_path):
    store = SqliteRepository(tmp_path / "ethos.db")
    service = EthosService(corpus, store)
    service.init_store(TS)
    first = service.ask("is lying wrong?", TS, topic_id="honesty_and_deception")
    second = service.ask("is lying wrong?", TS, topic_id="honesty_and_deception")
    assert first.question.id != second.question.id
    assert first.answer.id != second.answer.id
    assert first.answer.rendered_text == second.answer.rendered_text
    store.close()


def test_fr11_sqlite_is_safe_across_threads(corpus, compose, tmp_path):
    """check_same_thread=False plus an RLock: a served API answers on a pool."""
    store = SqliteRepository(tmp_path / "ethos.db")
    question, answer = _answer(corpus, compose)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for _ in range(10):
                stored = store.add_question(question)
                store.add_answer(answer.model_copy(update={"question_id": stored.id}))
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(store.list_questions(1000, 0)) == 40
    ids = {a.id for a in (store.get_answer(i) for i in range(1, 41)) if a}
    assert len(ids) == 40
    store.close()


def test_fr11_rendered_text_reproduces_from_body(corpus, compose, tmp_path):
    """D0(d): same composer_version => re-render is byte-identical."""
    from ethos.engine.compose import COMPOSER_VERSION, render_text

    store = SqliteRepository(tmp_path / "ethos.db")
    service = EthosService(corpus, store)
    service.init_store(TS)
    result = service.ask("is lying wrong?", TS, topic_id="honesty_and_deception")
    stored = store.get_answer(result.answer.id)
    assert stored.composer_version == COMPOSER_VERSION
    again = render_text(
        stored.body,
        {t.id: t.title for t in corpus.topics},
        {t.id: t.name for t in corpus.traditions},
    )
    assert again == stored.rendered_text
    store.close()
