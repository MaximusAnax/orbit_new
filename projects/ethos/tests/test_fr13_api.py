"""FR-13: the FastAPI surface and its edge semantics (SCOPE § API sketch)."""
from __future__ import annotations

import threading

import pytest
from ethos.api.app import create_app
from ethos.service import EthosService, IntegrityError
from ethos.store.memory_repo import MemoryRepository
from ethos.store.sqlite_repo import SqliteRepository
from fastapi.testclient import TestClient

TS = "2026-08-01T12:00:00Z"
DIRECT_QUESTION = "Is lying always wrong, or are there exceptions?"


@pytest.fixture()
def client(corpus):
    service = EthosService(corpus, MemoryRepository())
    service.init_store(TS)
    return TestClient(create_app(service))


def test_fr13_health_and_stats(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    payload = client.get("/corpus/stats").json()
    assert payload["stats"]["traditions"] == 10
    assert payload["stats"]["topics"] >= 24
    assert len(payload["coverage"]) >= 24


def test_fr13_browse_endpoints(client) -> None:
    traditions = client.get("/traditions").json()
    assert len(traditions) == 10
    assert client.get("/traditions/stoicism").json()["name"]
    assert client.get("/traditions/nope").status_code == 404
    topics = client.get("/topics", params={"tradition": "buddhism"}).json()
    assert topics and all(topic["id"] for topic in topics)
    detail = client.get("/topics/honesty_and_deception").json()
    assert detail["topic"]["title"]
    assert detail["covered"] and detail["reading"]
    assert client.get("/topics/not_a_topic").status_code == 404


def test_fr13_reading_and_passage(client) -> None:
    reading = client.get("/topics/honesty_and_deception/reading").json()
    titles = [row["entry"]["title"] for row in reading]
    assert len(titles) == len(set(titles))  # de-duplicated across traditions
    passage = client.get("/passages/kjv-matthew-5-37").json()
    assert passage["passage"]["locator"] == "Matthew 5:37"
    assert passage["source"]["license"] == "public_domain"
    assert client.get("/passages/no-such-passage").status_code == 404


def test_fr13_ask_returns_question_and_answer(client) -> None:
    response = client.post("/questions", json={"text": DIRECT_QUESTION, "asked_at": TS})
    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "answered"
    body = payload["answer"]["body"]
    assert body["routing"]["topic_id"] == "honesty_and_deception"
    assert len(body["perspectives"]) >= 6
    assert payload["answer"]["verified"] is True


def test_fr13_refusal_is_200(client) -> None:
    """A refusal is a successful answer to a question the corpus cannot address."""
    response = client.post(
        "/questions",
        json={
            "text": "Is CRISPR editing of an embryo's genome killing an unborn person, "
            "murder, or self-defense against inherited disease?",
            "asked_at": TS,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "refused_out_of_scope"
    assert len(payload["refusal"]["nearest_topics"]) <= 3
    assert "gene editing" in payload["refusal"]["note"]
    assert "answer" not in payload


def test_fr13_edge_polish_unavailable_400(client) -> None:
    response = client.post("/questions", json={"text": DIRECT_QUESTION, "polish": True})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "polish_unavailable"


def test_fr13_integrity_failure_500(corpus, monkeypatch) -> None:
    """FR-8 failure on the null path is a 500 naming the failing check."""
    service = EthosService(corpus, MemoryRepository())
    service.init_store(TS)
    from ethos.engine.verify import Failure

    def boom(*_args, **_kwargs):
        raise IntegrityError([Failure("b", "quote text does not match kjv-matthew-5-37")])

    monkeypatch.setattr(service, "_compose_verified", boom)
    client = TestClient(create_app(service), raise_server_exceptions=False)
    response = client.post("/questions", json={"text": DIRECT_QUESTION, "asked_at": TS})
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "integrity_failure"
    assert "kjv-matthew-5-37" in response.json()["detail"]["message"]


def test_fr13_asked_at_defaulted_at_edge(client) -> None:
    """The engine never reads a clock; the edge fills asked_at in."""
    payload = client.post("/questions", json={"text": DIRECT_QUESTION}).json()
    assert payload["question"]["asked_at"]


def test_fr13_forced_topic_and_filter(client) -> None:
    payload = client.post(
        "/questions",
        json={
            "text": "anything at all",
            "asked_at": TS,
            "topic_id": "divorce",
            "traditions": ["christianity", "islam"],
        },
    ).json()
    body = payload["answer"]["body"]
    assert body["routing"] == {
        "topic_id": "divorce",
        "confidence": None,
        "alternates": [],
        "forced": True,
    }
    assert [p["tradition_id"] for p in body["perspectives"]] == ["christianity", "islam"]
    assert body["filtered_out"] and "christianity" not in body["filtered_out"]
    assert client.post(
        "/questions", json={"text": "x", "asked_at": TS, "topic_id": "nope"}
    ).status_code == 404


def test_fr13_sqlite_backed_api_is_thread_safe(corpus, tmp_path) -> None:
    """The app holds one service — and therefore one sqlite connection — for its
    whole lifetime, while Starlette runs every sync endpoint on a threadpool.

    Both halves of `SqliteRepository`'s threading contract are load-bearing and
    this test is red without either. Measured with the guard removed:
    `check_same_thread=True` -> every worker but the connection's creator raises
    `ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread`; dropping the `RLock` -> `OperationalError: cannot start a
    transaction within a transaction` plus duplicate `lastrowid` values, i.e.
    one asker is handed another asker's answer id.
    """
    store = SqliteRepository(tmp_path / "ethos.db")
    service = EthosService(corpus, store)
    service.init_store(TS)
    client = TestClient(create_app(service))
    payloads: list[dict] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=30)
            # Same text on purpose: re-asking must create a new question and a
            # new answer (FR-11), which is what makes the id check meaningful.
            response = client.post(
                "/questions", json={"text": DIRECT_QUESTION, "asked_at": TS}
            )
            with lock:
                payloads.append({"status": response.status_code, **response.json()})
        except BaseException as exc:  # pragma: no cover - only on a regression
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], errors
    assert [p["status"] for p in payloads] == [200] * 8
    question_ids = [p["question"]["id"] for p in payloads]
    answer_ids = [p["answer"]["id"] for p in payloads]
    assert len(set(question_ids)) == 8, question_ids
    assert len(set(answer_ids)) == 8, answer_ids
    # Every id the API handed back resolves to the row that writer actually wrote.
    for payload in payloads:
        stored = client.get(f"/answers/{payload['answer']['id']}/text").json()
        assert stored["text"] == payload["answer"]["rendered_text"]
        detail = client.get(f"/questions/{payload['question']['id']}").json()
        assert detail["question"]["text"] == payload["question"]["text"]
        assert detail["answer"]["id"] == payload["answer"]["id"]
    assert len(store.list_questions(100, 0)) == 8
    store.close()


def test_fr13_history_and_stored_render(client) -> None:
    posted = client.post("/questions", json={"text": DIRECT_QUESTION, "asked_at": TS}).json()
    question_id = posted["question"]["id"]
    answer_id = posted["answer"]["id"]
    listed = client.get("/questions", params={"limit": 5}).json()
    assert any(row["id"] == question_id for row in listed)
    detail = client.get(f"/questions/{question_id}").json()
    assert detail["answer"]["id"] == answer_id
    text = client.get(f"/answers/{answer_id}/text").json()
    assert text["text"] == posted["answer"]["rendered_text"]
    assert text["composer_note"] is None
    assert client.get("/answers/9999").status_code == 404
