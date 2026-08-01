"""In-memory Repository backend for tests and evals (FR-15)."""
from __future__ import annotations

from ethos.models import Answer, CorpusMeta, Question
from ethos.store.repository import StaleCorpusError, UnverifiedAnswerError


class MemoryRepository:
    def __init__(self) -> None:
        self._meta: CorpusMeta | None = None
        self._questions: dict[int, Question] = {}
        self._answers: dict[int, Answer] = {}
        self._next_question_id = 1
        self._next_answer_id = 1

    def set_corpus_meta(self, meta: CorpusMeta) -> None:
        self._meta = meta

    def get_corpus_meta(self) -> CorpusMeta | None:
        return self._meta

    def guard_corpus(self, current_version: str) -> None:
        if self._meta is None or self._meta.corpus_version != current_version:
            raise StaleCorpusError(
                "corpus files changed since load (or never loaded); run `ethos init`"
            )

    def add_question(self, question: Question) -> Question:
        stored = question.model_copy(update={"id": self._next_question_id})
        self._questions[self._next_question_id] = stored
        self._next_question_id += 1
        return stored

    def add_answer(self, answer: Answer) -> Answer:
        if not answer.verified:
            raise UnverifiedAnswerError("refusing to persist an unverified answer (FR-8)")
        stored = answer.model_copy(update={"id": self._next_answer_id})
        self._answers[self._next_answer_id] = stored
        self._next_answer_id += 1
        return stored

    def get_question(self, question_id: int) -> Question | None:
        question = self._questions.get(question_id)
        return question.model_copy(deep=True) if question else None

    def list_questions(self, limit: int, offset: int) -> list[Question]:
        ordered = sorted(self._questions.values(), key=lambda q: -(q.id or 0))
        return [q.model_copy(deep=True) for q in ordered[offset : offset + limit]]

    def get_answer(self, answer_id: int) -> Answer | None:
        answer = self._answers.get(answer_id)
        return answer.model_copy(deep=True) if answer else None

    def get_answer_for_question(self, question_id: int) -> Answer | None:
        for answer in self._answers.values():
            if answer.question_id == question_id:
                return answer.model_copy(deep=True)
        return None
