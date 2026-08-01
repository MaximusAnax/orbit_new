"""Cross-project audit regression: the served app on the *real* SQLite store.

flowlist and pointsmax shipped a repository whose connection was bound (by
sqlite3's default ``check_same_thread=True``) to the thread that constructed
it, while FastAPI dispatches sync handlers — and sync generator dependencies'
``__enter__``/``__exit__`` — to anyio threadpool workers.  Every DB-touching
endpoint then dies with ``sqlite3.ProgrammingError``.  ``test_api.py`` never
sees this because it injects :class:`InMemoryRepository`; these tests wire the
real app to the real :class:`SqliteRepository` on a tmp file, exactly like
``create_app(service=...)`` and ``dresscast serve`` do.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dresscast.api import create_app
from dresscast.services import build_service
from fastapi.testclient import TestClient

NOW = datetime(2026, 4, 14, 6, 30, tzinfo=UTC)


def _body(name: str) -> dict[str, object]:
    return {
        "name": name,
        "category": "tshirt",
        "colors": ["white"],
        "occasions": ["casual"],
    }


def test_pinned_sqlite_service_survives_threadpool_dispatch(tmp_path: Path) -> None:
    """``create_app(service=...)``: one long-lived SQLite service for all requests.

    The service (and its connection) is built on this thread; the TestClient —
    like uvicorn — runs the sync handler on an anyio worker thread.  With the
    connection opened ``check_same_thread=True`` this raises
    ``sqlite3.ProgrammingError`` on the very first DB-touching request.
    """
    service = build_service(db=tmp_path / "dresscast.db", data_dir=tmp_path)
    try:
        client = TestClient(create_app(service=service, clock=lambda: NOW))
        created = client.post("/garments", json=_body("white-tee"))
        assert created.status_code == 201, created.text
        listed = client.get("/garments")
        assert listed.status_code == 200, listed.text
        assert [g["name"] for g in listed.json()] == ["white-tee"]
    finally:
        service.close()


def test_serve_factory_survives_concurrent_threadpool_requests(tmp_path: Path) -> None:
    """The actual ``dresscast serve`` wiring under concurrent load.

    ``service_factory`` opens one repository per request inside a sync
    generator dependency, but FastAPI runs its ``__enter__`` (opens the
    connection), the handler (uses it) and ``__exit__`` (closes it) as three
    separate ``to_thread`` hops on one shared event loop — under concurrent
    requests those hops land on different worker threads.  Requests must still
    succeed, and every committed write must survive.
    """
    db = tmp_path / "dresscast.db"
    app = create_app(
        service_factory=lambda: build_service(db=db, data_dir=tmp_path),
        clock=lambda: NOW,
    )
    threads, per_thread = 8, 4
    # One portal (== one event loop == one shared anyio worker pool), as uvicorn has.
    with TestClient(app) as client:
        seeded = client.post("/garments", json=_body("seed-tee"))
        assert seeded.status_code == 201, seeded.text

        def hammer(worker: int) -> None:
            for i in range(per_thread):
                created = client.post("/garments", json=_body(f"tee-{worker}-{i}"))
                assert created.status_code == 201, created.text
                listed = client.get("/garments")
                assert listed.status_code == 200, listed.text

        with ThreadPoolExecutor(max_workers=threads) as pool:
            for future in [pool.submit(hammer, w) for w in range(threads)]:
                future.result()

        final = client.get("/garments")
        assert final.status_code == 200, final.text
        assert len(final.json()) == 1 + threads * per_thread
