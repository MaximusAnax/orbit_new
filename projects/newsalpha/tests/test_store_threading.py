"""Regression: the served SQLite wiring must survive FastAPI's threadpool.

`uvicorn newsalpha.api.app:app` builds one `SQLiteRepository` in the lifespan
(on the event-loop thread) and every handler is sync `def`, so FastAPI runs it
on an anyio worker thread.  A connection opened with sqlite3's default
`check_same_thread=True` therefore crashes every DB-touching endpoint with
`sqlite3.ProgrammingError` -- a defect the API tests cannot see because they
inject an in-memory repository.  These tests wire the real app to the real
SQLite backend on a tmp file, exactly like production, and also hammer one
repository from many threads to prove write batches serialize.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from newsalpha.api.app import create_app
from newsalpha.store.sqlite import SQLiteRepository
from newsalpha_testkit import bars


def test_served_sqlite_wiring_survives_threadpool_dispatch(tmp_path):
    """The real app + real SQLite file, exercised the way uvicorn does.

    TestClient runs the lifespan (which constructs the repository) on its
    portal event-loop thread and dispatches each sync handler via anyio
    `to_thread` -- a different thread, as in the served deployment.
    """
    app = create_app(db_path=tmp_path / "newsalpha.db")
    with TestClient(app) as client:
        # Pure read path: repository.list_watchlist() on a worker thread.
        assert client.get("/watchlist").status_code == 200
        # Read-modify-write path: seeds the asset gazetteer, then inserts.
        assert client.put("/watchlist/cx:SOL").status_code == 200
        body = client.get("/watchlist").json()
        assert body["count"] == 1
        assert body["items"][0]["asset_id"] == "cx:SOL"
        # A second add is idempotent (read-then-skip on the same connection).
        assert client.put("/watchlist/cx:SOL").json()["added"] is False
        assert client.delete("/watchlist/cx:SOL").status_code == 200


def test_concurrent_read_modify_write_batches_serialize(tmp_path):
    """One shared connection, eight threads, interleaving-prone batches.

    `add_price_bars` is a read-modify-write composite (SELECT then INSERT per
    bar inside one transaction); replaying identical series exercises the
    read-then-skip branch.  Every batch must land intact and exactly once.
    """
    repository = SQLiteRepository(tmp_path / "concurrent.db")
    repository.initialize()
    workers, days, rounds = 8, 30, 5
    series = {i: bars(f"cx:A{i}", "2026-03-02", days) for i in range(workers)}

    def worker(i: int) -> None:
        for _ in range(rounds):
            repository.add_price_bars(series[i])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(worker, i) for i in range(workers)]:
            future.result()  # raises if any thread crashed

    stored = repository.list_price_bars()
    assert len(stored) == workers * days
    for i in range(workers):
        assert len(repository.list_price_bars(asset_id=f"cx:A{i}")) == days
    repository.close()
