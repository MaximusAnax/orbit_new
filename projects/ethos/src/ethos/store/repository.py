"""Repository protocol and store-level errors (FR-11).

Invariants enforced by every backend (and tested on both):
- an Answer with verified=False is rejected at insert;
- question and answer rows are append-only (no update path exists, and the
  SQLite backend additionally installs triggers that abort UPDATEs);
- the stale-corpus guard: reads/writes refuse to proceed when the recomputed
  data/ digest differs from the loaded corpus_meta until `ethos init` reloads.
"""
from __future__ import annotations

from typing import Protocol

from ethos.models import Answer, CorpusMeta, Question


class UnverifiedAnswerError(ValueError):
    """Attempt to persist an answer with verified=False (FR-8/FR-11)."""


class StaleCorpusError(RuntimeError):
    """data/ changed since `ethos init`; reload before reading or writing."""


class Repository(Protocol):
    def set_corpus_meta(self, meta: CorpusMeta) -> None: ...

    def get_corpus_meta(self) -> CorpusMeta | None: ...

    def guard_corpus(self, current_version: str) -> None:
        """Raise StaleCorpusError unless stored corpus_version matches."""
        ...

    def add_question(self, question: Question) -> Question: ...

    def add_answer(self, answer: Answer) -> Answer: ...

    def get_question(self, question_id: int) -> Question | None: ...

    def list_questions(self, limit: int, offset: int) -> list[Question]: ...

    def get_answer(self, answer_id: int) -> Answer | None: ...

    def get_answer_for_question(self, question_id: int) -> Answer | None: ...
