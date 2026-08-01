"""FastAPI application (FR-13). Edge semantics per SCOPE § API sketch:
polish without a key -> 400 polish_unavailable; FR-8 failure on the null path
-> 500 integrity_failure; refusals are 200; asked_at defaulted here, never in
the engine.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ethos.corpus import default_data_dir, load_corpus
from ethos.models import CorpusMeta
from ethos.service import (
    EthosService,
    IntegrityError,
    PolishUnavailableError,
    UnknownTopicError,
    UnknownTraditionError,
)
from ethos.store.repository import StaleCorpusError
from ethos.store.sqlite_repo import SqliteRepository


class AskRequest(BaseModel):
    text: str
    asked_at: str | None = None
    traditions: list[str] | None = None
    topic_id: str | None = None
    polish: bool = False


def default_service() -> EthosService:
    corpus = load_corpus(default_data_dir())
    db_path = Path(os.environ.get("ETHOS_DB_PATH", str(Path.home() / ".ethos" / "ethos.db")))
    repo = SqliteRepository(db_path)
    if repo.get_corpus_meta() is None:
        repo.set_corpus_meta(
            CorpusMeta(
                corpus_version=corpus.corpus_version,
                loaded_at=datetime.now(UTC).isoformat(),
            )
        )
    polisher = None
    if os.environ.get("ETHOS_LLM_API_KEY"):
        from ethos.adapters.polisher_llm import LLMPolisher

        polisher = LLMPolisher()
    return EthosService(corpus, repo, polisher=polisher)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def create_app(service: EthosService | None = None) -> FastAPI:
    app = FastAPI(title="ethos", version="0.1.0")
    app.state.service = service

    def svc(request: Request) -> EthosService:
        if request.app.state.service is None:
            request.app.state.service = default_service()
        return request.app.state.service

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/corpus/stats")
    def corpus_stats(request: Request) -> dict:
        service = svc(request)
        return {
            "stats": service.substance_stats(),
            "coverage": service.coverage_matrix(),
        }

    @app.get("/traditions")
    def traditions(request: Request) -> list:
        return list(svc(request).corpus.traditions)

    @app.get("/traditions/{tradition_id}")
    def tradition(tradition_id: str, request: Request) -> dict:
        found = svc(request).corpus.tradition_by_id.get(tradition_id)
        if found is None:
            raise _error(404, "unknown_tradition", tradition_id)
        return found.model_dump()

    @app.get("/topics")
    def topics(request: Request, tradition: str | None = None) -> list:
        try:
            return list(svc(request).topics_for_tradition(tradition))
        except UnknownTraditionError as exc:
            raise _error(404, "unknown_tradition", str(exc)) from exc

    @app.get("/topics/{topic_id}")
    def topic_detail(topic_id: str, request: Request) -> dict:
        try:
            detail = svc(request).topic_detail(topic_id)
        except UnknownTopicError as exc:
            raise _error(404, "unknown_topic", str(exc)) from exc
        return {
            "topic": detail["topic"].model_dump(),
            "covered": detail["covered"],
            "safeguards": [s.model_dump() for s in detail["safeguards"]],
            "reading": [
                {"tradition_id": r["tradition_id"], "entry": r["entry"].model_dump()}
                for r in detail["reading"]
            ],
        }

    @app.get("/topics/{topic_id}/reading")
    def topic_reading(topic_id: str, request: Request, tradition: str | None = None) -> list:
        try:
            reading = svc(request).reading_list(topic_id, tradition)
        except UnknownTopicError as exc:
            raise _error(404, "unknown_topic", str(exc)) from exc
        except UnknownTraditionError as exc:
            raise _error(404, "unknown_tradition", str(exc)) from exc
        return [
            {"tradition_id": r["tradition_id"], "entry": r["entry"].model_dump()}
            for r in reading
        ]

    @app.get("/passages/{passage_id}")
    def passage_detail(passage_id: str, request: Request) -> dict:
        service = svc(request)
        passage = service.corpus.passage_by_id.get(passage_id)
        if passage is None:
            raise _error(404, "unknown_passage", passage_id)
        source = service.corpus.source_by_id[passage.source_id]
        return {"passage": passage.model_dump(), "source": source.model_dump()}

    @app.post("/questions")
    def post_question(body: AskRequest, request: Request) -> dict:
        service = svc(request)
        asked_at = body.asked_at or datetime.now(UTC).isoformat()
        try:
            result = service.ask(
                text=body.text,
                asked_at=asked_at,
                traditions=body.traditions,
                topic_id=body.topic_id,
                polish=body.polish,
            )
        except PolishUnavailableError as exc:
            raise _error(400, "polish_unavailable", str(exc)) from exc
        except UnknownTopicError as exc:
            raise _error(404, "unknown_topic", str(exc)) from exc
        except UnknownTraditionError as exc:
            raise _error(404, "unknown_tradition", str(exc)) from exc
        except StaleCorpusError as exc:
            raise _error(409, "stale_corpus", str(exc)) from exc
        except IntegrityError as exc:
            raise _error(500, "integrity_failure", str(exc)) from exc
        payload: dict = {"question": result.question.model_dump()}
        if result.refusal is not None:
            payload["outcome"] = result.refusal.outcome
            payload["refusal"] = {
                "nearest_topics": [t.model_dump() for t in result.refusal.nearest_topics],
                "browse_hint": result.refusal.browse_hint,
                "note": result.refusal.note,
            }
        else:
            assert result.answer is not None
            payload["outcome"] = "answered"
            payload["answer"] = result.answer.model_dump()
        return payload

    @app.get("/questions")
    def list_questions(request: Request, limit: int = 20, offset: int = 0) -> list:
        service = svc(request)
        return [q.model_dump() for q in service.repo.list_questions(limit, offset)]

    @app.get("/questions/{question_id}")
    def get_question(question_id: int, request: Request) -> dict:
        service = svc(request)
        question = service.repo.get_question(question_id)
        if question is None:
            raise _error(404, "unknown_question", str(question_id))
        answer = service.repo.get_answer_for_question(question_id)
        return {
            "question": question.model_dump(),
            "answer": answer.model_dump() if answer else None,
        }

    @app.get("/answers/{answer_id}")
    def get_answer(answer_id: int, request: Request) -> dict:
        service = svc(request)
        answer = service.repo.get_answer(answer_id)
        if answer is None:
            raise _error(404, "unknown_answer", str(answer_id))
        return answer.model_dump()

    @app.get("/answers/{answer_id}/text")
    def get_answer_text(answer_id: int, request: Request) -> dict:
        service = svc(request)
        answer = service.repo.get_answer(answer_id)
        if answer is None:
            raise _error(404, "unknown_answer", str(answer_id))
        text, outdated = service.render_stored(answer)
        note = (
            "note: rendered with an older composer version; shown exactly as originally verified"
            if outdated
            else None
        )
        return {"text": text, "composer_note": note}

    return app
