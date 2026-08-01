"""SQLite Repository backend (stdlib sqlite3), default ~/.ethos/ethos.db.

Append-only tables with UPDATE-abort triggers; verified answers only, enforced
both at the application layer and by a CHECK constraint.

Threading: the connection is opened with ``check_same_thread=False`` because a
served API hands requests to a thread pool, and every statement runs under a
re-entrant lock. Both halves are needed: without the flag sqlite3 refuses the
cross-thread call, and without the lock two concurrent writes can interleave
between the INSERT and the ``lastrowid`` read and hand back the wrong id.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ethos.models import Answer, CorpusMeta, Question
from ethos.store.repository import StaleCorpusError, UnverifiedAnswerError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    corpus_version TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    forced_topic_id TEXT,
    routing TEXT
);
CREATE TABLE IF NOT EXISTS answer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES question(id),
    created_at TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    options TEXT NOT NULL,
    corpus_version TEXT NOT NULL,
    composer_version TEXT NOT NULL,
    polish_used INTEGER NOT NULL,
    polish_fell_back INTEGER NOT NULL,
    verified INTEGER NOT NULL CHECK (verified = 1),
    body TEXT NOT NULL,
    rendered_text TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS question_immutable
    BEFORE UPDATE ON question
    BEGIN SELECT RAISE(ABORT, 'question rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS answer_immutable
    BEFORE UPDATE ON answer
    BEGIN SELECT RAISE(ABORT, 'answer rows are immutable'); END;
"""


class SqliteRepository:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_corpus_meta(self, meta: CorpusMeta) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM corpus_meta")
            self._conn.execute(
                "INSERT INTO corpus_meta (id, corpus_version, loaded_at) VALUES (1, ?, ?)",
                (meta.corpus_version, meta.loaded_at),
            )
            self._conn.commit()

    def get_corpus_meta(self) -> CorpusMeta | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT corpus_version, loaded_at FROM corpus_meta WHERE id = 1"
            ).fetchone()
        return CorpusMeta(corpus_version=row[0], loaded_at=row[1]) if row else None

    def guard_corpus(self, current_version: str) -> None:
        meta = self.get_corpus_meta()
        if meta is None or meta.corpus_version != current_version:
            raise StaleCorpusError(
                "corpus files changed since load (or never loaded); run `ethos init`"
            )

    def add_question(self, question: Question) -> Question:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO question (text, asked_at, outcome, forced_topic_id, routing)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    question.text,
                    question.asked_at,
                    question.outcome.value,
                    question.forced_topic_id,
                    question.routing.model_dump_json() if question.routing else None,
                ),
            )
            self._conn.commit()
            return question.model_copy(update={"id": cursor.lastrowid})

    def add_answer(self, answer: Answer) -> Answer:
        if not answer.verified:
            raise UnverifiedAnswerError("refusing to persist an unverified answer (FR-8)")
        with self._lock:
            return self._insert_answer(answer)

    def _insert_answer(self, answer: Answer) -> Answer:
        cursor = self._conn.execute(
            "INSERT INTO answer (question_id, created_at, topic_id, options, corpus_version,"
            " composer_version, polish_used, polish_fell_back, verified, body, rendered_text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                answer.question_id,
                answer.created_at,
                answer.topic_id,
                answer.options.model_dump_json(),
                answer.corpus_version,
                answer.composer_version,
                int(answer.polish_used),
                int(answer.polish_fell_back),
                int(answer.verified),
                answer.body.model_dump_json(),
                answer.rendered_text,
            ),
        )
        self._conn.commit()
        return answer.model_copy(update={"id": cursor.lastrowid})

    def _question_from_row(self, row: tuple) -> Question:
        return Question(
            id=row[0],
            text=row[1],
            asked_at=row[2],
            outcome=row[3],
            forced_topic_id=row[4],
            routing=json.loads(row[5]) if row[5] else None,
        )

    def get_question(self, question_id: int) -> Question | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, text, asked_at, outcome, forced_topic_id, routing"
                " FROM question WHERE id = ?",
                (question_id,),
            ).fetchone()
        return self._question_from_row(row) if row else None

    def list_questions(self, limit: int, offset: int) -> list[Question]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, text, asked_at, outcome, forced_topic_id, routing"
                " FROM question ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._question_from_row(r) for r in rows]

    def _answer_from_row(self, row: tuple) -> Answer:
        return Answer(
            id=row[0],
            question_id=row[1],
            created_at=row[2],
            topic_id=row[3],
            options=json.loads(row[4]),
            corpus_version=row[5],
            composer_version=row[6],
            polish_used=bool(row[7]),
            polish_fell_back=bool(row[8]),
            verified=bool(row[9]),
            body=json.loads(row[10]),
            rendered_text=row[11],
        )

    _ANSWER_COLS = (
        "id, question_id, created_at, topic_id, options, corpus_version, composer_version,"
        " polish_used, polish_fell_back, verified, body, rendered_text"
    )

    def get_answer(self, answer_id: int) -> Answer | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._ANSWER_COLS} FROM answer WHERE id = ?", (answer_id,)
            ).fetchone()
        return self._answer_from_row(row) if row else None

    def get_answer_for_question(self, question_id: int) -> Answer | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._ANSWER_COLS} FROM answer WHERE question_id = ? ORDER BY id LIMIT 1",
                (question_id,),
            ).fetchone()
        return self._answer_from_row(row) if row else None
