"""Cross-project audit: the SQLite store must survive FastAPI's threadpool.

Uvicorn dispatches sync dependencies and sync handlers through anyio
``to_thread`` workers, so the connection a :class:`SqliteRepository` holds is
used on threads other than the one that opened it: the default wiring opens it
inside the ``get_service`` dependency (one worker) and uses it in the handler
(another worker, under concurrent traffic), and the documented
``create_app(lambda: service)`` override opens it on the composition root's
thread and uses it on every worker.  With sqlite3's default
``check_same_thread=True`` both wirings die with ``sqlite3.ProgrammingError``.
The in-memory ``api_client`` fixture never sees this; these tests wire the real
app to the real SQLite store on a tmp file.
"""

from __future__ import annotations

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from almanac.api.routes import create_app
from almanac.engine.normalize import normalized_hash
from almanac.factory import build_service
from almanac.models import Collection, Entry, EntryKind
from almanac.store.sqlite_repo import SqliteRepository, open_repository
from fastapi.testclient import TestClient

NOW = dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC)


@pytest.fixture
def offline_env(monkeypatch):
    """Keep the composition root offline regardless of ambient env vars."""
    monkeypatch.delenv("ALMANAC_LLM_API_KEY", raising=False)
    monkeypatch.delenv("ALMANAC_WIKIQUOTE", raising=False)


def test_shared_sqlite_service_survives_threadpool_dispatch(tmp_path, offline_env):
    """A service opened on this thread must serve handlers on worker threads.

    ``create_app(lambda: service)`` is the documented override wiring (conftest
    uses it with the memory repo); with SQLite the connection is opened on the
    test thread while TestClient runs each sync handler on an anyio worker
    thread.  Before the fix the very first DB-touching request raised
    ``sqlite3.ProgrammingError`` (cross-thread use of the connection).
    """
    service = build_service(open_repository(tmp_path / "almanac.db"))
    app = create_app(lambda: service)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        created = client.post(
            "/entries", json={"text": "The obstacle is the way.", "kind": "quote"}
        )
        assert created.status_code == 201, created.text
        listed = client.get("/entries")
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1
    service.repo.close()


def test_default_wiring_survives_concurrent_requests(tmp_path, monkeypatch, offline_env):
    """The production default (``create_app()`` -> ``open_service`` per request).

    The sync ``get_service`` dependency opens the connection on one anyio
    worker; under concurrent traffic the handler runs on a different worker,
    which raised ``sqlite3.ProgrammingError`` before the fix (sequential
    requests sneak through only because anyio reuses the last idle worker).
    """
    monkeypatch.setenv("ALMANAC_DB_PATH", str(tmp_path / "almanac.db"))
    app = create_app()
    threads = 6
    barrier = threading.Barrier(threads)

    def hammer(client: TestClient, lane: int) -> None:
        barrier.wait(timeout=30)
        for i in range(8):
            created = client.post(
                "/entries",
                json={"text": f"concurrent lane {lane} iteration {i}", "kind": "idea"},
            )
            assert created.status_code == 201, created.text
            listed = client.get("/entries", params={"limit": 5})
            assert listed.status_code == 200, listed.text

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(hammer, client, lane) for lane in range(threads)]
        for future in futures:
            future.result()  # re-raises sqlite3.ProgrammingError pre-fix

    with TestClient(app) as client:
        assert client.get("/entries").json()["total"] == threads * 8


def _entry(n: int) -> Entry:
    text = f"threading probe entry number {n}"
    return Entry(
        id=f"e{n:02d}",
        kind=EntryKind.IDEA,
        text=text,
        normalized_hash=normalized_hash(text),
        captured_on=NOW.date(),
        created_at=NOW,
        updated_at=NOW,
    )


def test_repository_composites_serialize_across_threads(tmp_path):
    """Read-modify-write batches from worker threads stay atomic.

    The repository is built on the test thread and driven from a thread pool,
    the way a shared service is in deployment.  Before the fix the first worker
    call raised ``sqlite3.ProgrammingError``; with ``check_same_thread=False``
    alone (no lock) concurrent ``add_collection_entry`` composites would read
    the same position and collide on ``UNIQUE (collection_id, position)``.
    """
    repo = SqliteRepository(tmp_path / "almanac.db")
    entries = [_entry(n) for n in range(12)]
    for entry in entries:
        repo.add_entry(entry)
    repo.add_collection(Collection(id="c1", name="probe", description=None, created_at=NOW))
    barrier = threading.Barrier(6)

    def join_collection(entry_id: str) -> None:
        barrier.wait(timeout=30)
        repo.add_collection_entry("c1", entry_id)  # read positions, then insert

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(join_collection, entry.id) for entry in entries]
        for future in futures:
            future.result()  # re-raises sqlite3.ProgrammingError pre-fix

    members = repo.collection_entry_ids("c1")
    assert sorted(members) == [entry.id for entry in entries]
    assert len(set(members)) == len(entries)  # distinct, gap-free positions
    repo.close()
