"""Cross-project audit regression: the served SQLite path must survive thread hops.

FastAPI executes sync (``def``) route handlers — and the sync ``get_service``
dependency — on anyio threadpool worker threads, while the process-wide service
holds ONE long-lived ``sqlite3`` connection for its whole lifetime.  With
sqlite3's default ``check_same_thread=True`` every DB-touching endpoint served
on a worker other than the connection's creator crashes with
``sqlite3.ProgrammingError`` under uvicorn, while the FR-12 API tests stay green
because they inject an ``InMemoryRepository``.  That exact defect shipped in two
sibling projects (flowlist, pointsmax).  These tests drive the real app wired to
the real ``SQLiteRepository`` on a tmp file, so they fail if the hardening
(``check_same_thread=False`` plus the repository ``RLock``) regresses.
"""

from __future__ import annotations

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from grailtrader.api import create_app, get_service
from grailtrader.store import SQLiteRepository
from grailtrader_testkit import garment

api_module = importlib.import_module("grailtrader.api.app")

WORKERS = 6
ROUNDS = 3


def test_served_sqlite_path_survives_threadpool_dispatch(tmp_path, monkeypatch):
    """The real app + real SQLite store, exercised the way uvicorn does.

    The singleton service (and its connection) is created on this test's thread
    — the same topology as any startup-path or CLI-warmed process — and every
    TestClient request is then dispatched via anyio ``to_thread`` on a DIFFERENT
    worker thread.  The barrier-synchronized barrage additionally forces several
    workers to touch the shared connection concurrently, covering the purely
    lazy path where the connection is born on one worker and used by others.
    """
    monkeypatch.setenv("GRAILTRADER_DB", str(tmp_path / "api.db"))
    monkeypatch.setattr(api_module, "_service", None)
    service = get_service()  # opens the sqlite3 connection on the test thread
    try:
        app = create_app()
        with TestClient(app) as client:
            # Single request: handler runs on an anyio worker, not this thread.
            first = client.get("/health")
            assert first.status_code == 200, first.text

            barrier = threading.Barrier(WORKERS)

            def hammer(worker: int) -> list[str]:
                created: list[str] = []
                for round_no in range(ROUNDS):
                    barrier.wait(timeout=30)
                    posted = client.post(
                        "/portfolio",
                        json={
                            "label": f"HL moto {worker}-{round_no}",
                            "brand": "helmut-lang",
                            "era": "helmut",
                            "category": "outerwear",
                            "condition": "excellent",
                            "price": 1000.0 + worker * 100 + round_no,
                            "date": "2024-01-06",
                        },
                    )
                    assert posted.status_code == 201, posted.text
                    created.append(posted.json()["id"])
                    listed = client.get("/portfolio")
                    assert listed.status_code == 200, listed.text
                return created

            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                batches = [pool.submit(hammer, worker) for worker in range(WORKERS)]
                ids = [gid for batch in batches for gid in batch.result(timeout=120)]

            assert len(set(ids)) == WORKERS * ROUNDS
            final = client.get("/portfolio")
            assert final.status_code == 200
            assert {row["id"] for row in final.json()["garments"]} == set(ids)
            health = client.get("/health")
            assert health.status_code == 200
            assert health.json()["garments"] == WORKERS * ROUNDS
    finally:
        service.repo.close()


def test_sqlite_repository_read_modify_write_across_threads(tmp_path):
    """The store itself, driven from foreign threads with contending RMW batches.

    The connection is created on the pytest thread; every batch below runs on a
    ThreadPoolExecutor worker.  Each batch is add -> get -> update -> get, so
    concurrent batches also exercise the lock that keeps one thread's
    transaction from interleaving with (or rolling back) another's.
    """
    with SQLiteRepository(tmp_path / "threads.db") as repo:
        repo.initialize(reset=True)
        barrier = threading.Barrier(WORKERS)

        def batch(worker: int) -> list[str]:
            barrier.wait(timeout=30)
            created: list[str] = []
            for index in range(ROUNDS):
                piece = garment(price=1000.0 + worker * 100 + index, notes="v0")
                repo.add_garment(piece)
                stored = repo.get_garment(piece.id)
                assert stored is not None
                repo.update_garment(stored.model_copy(update={"notes": "v1"}))
                assert repo.get_garment(piece.id).notes == "v1"
                repo.list_garments()  # concurrent reads against in-flight writers
                created.append(piece.id)
            return created

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(batch, worker) for worker in range(WORKERS)]
            ids = [gid for future in futures for gid in future.result(timeout=120)]

        rows = repo.list_garments()
        assert {row.id for row in rows} == set(ids)
        assert len(rows) == WORKERS * ROUNDS
        assert all(row.notes == "v1" for row in rows)
