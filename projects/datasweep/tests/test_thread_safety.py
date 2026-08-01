"""Cross-thread SQLite regression tests (cross-project audit, REVIEW.md).

FastAPI dispatches sync handlers and sync generator dependencies to anyio
threadpool workers.  Under ``datasweep serve`` that means each request's
``SqliteRepository`` is *constructed* in one worker thread (dependency
``__enter__`` via ``contextmanager_in_threadpool``), *used* by the handler in
another (``run_in_threadpool``), and *closed* in a third.  With sqlite3's
default ``check_same_thread=True`` any concurrent load misaligns those workers
and every DB-touching endpoint dies with ``sqlite3.ProgrammingError`` — while
the API tests stay green because they inject an in-memory repository.  The
same defect shipped in the flowlist and pointsmax siblings.

These tests exercise the real app wired to the real SQLite store on a tmp
file, the way the threadpool does.  TestClient reproduces the thread hop, so
they fail if the fix (``check_same_thread=False`` + the store's RLock around
write batches and read-modify-write composites) regresses.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from datasweep.adapters.notifier import NullNotifier
from datasweep.api.app import DB_ENV, create_app
from datasweep.engine.models import WatchedFolder
from datasweep.services import DatasweepService
from datasweep.store.sqlite import SqliteRepository
from fastapi.testclient import TestClient
from support_datasweep import FIXED_NOW


def test_sqlite_store_serializes_read_modify_write_across_threads(tmp_path: Path) -> None:
    """A repository built on one thread must survive worker-thread batches.

    ``upsert_source_file`` is a read-modify-write composite (SELECT, then
    INSERT or UPDATE).  Raced from many threads against one connection it
    needs both halves of the fix: ``check_same_thread=False`` so the calls do
    not raise ``sqlite3.ProgrammingError``, and the store lock so two threads
    cannot both observe "missing" and both INSERT (UNIQUE violation).
    """
    repo = SqliteRepository(tmp_path / "threads.db")  # built on the test thread
    shared_path = str(tmp_path / "incoming" / "sales.csv")
    workers = 8
    barrier = threading.Barrier(workers)

    def touch(n: int) -> None:
        barrier.wait()  # maximize interleaving
        repo.add_folder(
            WatchedFolder(
                id=f"folder-{n}",
                path=str(tmp_path / f"watch-{n}"),
                output_dir=str(tmp_path / f"watch-{n}" / ".datasweep"),
                created_at=FIXED_NOW,
            )
        )
        for i in range(5):
            assert (
                repo.upsert_source_file(
                    id=f"src-{n}-{i}", path=shared_path, folder_id=None, now=FIXED_NOW
                )
                is not None
            )

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in [pool.submit(touch, n) for n in range(workers)]:
                future.result()  # re-raises ProgrammingError/IntegrityError
        assert len(repo.list_folders()) == workers
        assert repo.get_source_file(shared_path) is not None
    finally:
        repo.close()


def test_served_default_wiring_survives_concurrent_threadpool_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact app ``datasweep serve`` builds must work under concurrency.

    ``create_app()`` with no override uses ``default_service_factory``: a sync
    generator dependency opening a per-request ``SqliteRepository`` on the
    ``DATASWEEP_DB`` file.  anyio's LIFO worker reuse lets a *lone* request
    sneak through on one thread, so this test fires overlapping requests —
    exactly what uvicorn serves — which scrambles the enter/handler/close
    worker assignment.
    """
    monkeypatch.setenv(DB_ENV, str(tmp_path / "served.db"))
    app = create_app()  # production wiring, no factory injected
    client_threads = 6
    barrier = threading.Barrier(client_threads)

    with TestClient(app) as client:
        assert client.get("/folders").json() == []  # warm-up creates the DB

        def hammer(n: int) -> None:
            barrier.wait()
            created = client.post("/folders", json={"path": str(tmp_path / f"drop-{n}")})
            assert created.status_code == 201, created.text
            for _ in range(8):
                listed = client.get("/folders")
                assert listed.status_code == 200, listed.text

        with ThreadPoolExecutor(max_workers=client_threads) as pool:
            for future in [pool.submit(hammer, n) for n in range(client_threads)]:
                future.result()  # re-raises sqlite3.ProgrammingError pre-fix

        paths = {folder["path"] for folder in client.get("/folders").json()}
    assert paths == {str(tmp_path / f"drop-{n}") for n in range(client_threads)}


def test_shared_sqlite_service_wiring_survives_thread_hop(tmp_path: Path) -> None:
    """A single shared SQLite-backed service must serve from worker threads.

    ``create_app(lambda: service)`` is the documented injection wiring; bound
    to a SQLite store it shares one connection, opened on the composition
    root's thread, across every threadpool worker.  Pre-fix even this single
    request crashed with ``sqlite3.ProgrammingError``.
    """
    repo = SqliteRepository(tmp_path / "shared.db")  # opened on the test thread
    service = DatasweepService(repo, notifier=NullNotifier())
    app = create_app(lambda: service)
    threads = 4
    barrier = threading.Barrier(threads)

    try:
        with TestClient(app) as client:
            listed = client.get("/folders")  # deterministic pre-fix crash
            assert listed.status_code == 200, listed.text

            def hammer(n: int) -> None:
                barrier.wait()
                created = client.post("/folders", json={"path": str(tmp_path / f"shared-{n}")})
                assert created.status_code == 201, created.text
                for _ in range(5):
                    assert client.get("/folders").status_code == 200

            with ThreadPoolExecutor(max_workers=threads) as pool:
                for future in [pool.submit(hammer, n) for n in range(threads)]:
                    future.result()

            assert len(client.get("/folders").json()) == threads
    finally:
        repo.close()
