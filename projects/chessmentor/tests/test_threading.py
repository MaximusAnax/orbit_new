"""Regression guard — the served SQLite path must survive concurrent threads.

The served surface (FR-14) is FastAPI with **sync** handlers, which Starlette
runs in its thread pool, and the production ``get_service`` dependency is a
sync *generator* dependency: FastAPI resolves its setup and its teardown as two
separate thread-pool submissions.  Under concurrency those land on different
worker threads, so a ``sqlite3`` connection opened during setup is used (and
closed) from a thread that did not create it.  With the stdlib default
``check_same_thread=True`` that raises::

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

Measured before the fix by hammering the production wiring: 48/48 concurrent
requests failed this way.  These tests run the **real** app against a **real**
SQLite file — no in-memory repository, no pinned service, no mocks — so they
fail if anyone reinstates the default, and they also exercise one repository
shared by several threads (the shape a threaded server or a background worker
would produce).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from chessmentor.api.app import DB_PATH_ENV, create_app
from chessmentor.datasets import Datasets
from chessmentor.models import ChallengeMode, PlayerProfile, PreferredColor
from chessmentor.store import SQLiteRepository
from fastapi.testclient import TestClient

NOW = "2026-07-31T12:00:00Z"
THREADS = 6
REQUESTS_PER_THREAD = 8


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The production wiring: ``create_app()`` with no pinned service."""
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "served.db"))
    with TestClient(create_app()) as client:
        assert client.put("/profile", json={"display_name": "Owner", "at": NOW}).status_code == 200
        yield client


def _hammer(client: TestClient, path: str) -> list[str]:
    """Fire ``REQUESTS_PER_THREAD`` requests from each of ``THREADS`` threads."""
    barrier = threading.Barrier(THREADS)
    failures: list[str] = []
    lock = threading.Lock()

    def worker(_: int) -> None:
        barrier.wait(timeout=30)
        for _attempt in range(REQUESTS_PER_THREAD):
            try:
                response = client.get(path)
                if response.status_code != 200:
                    with lock:
                        failures.append(f"HTTP {response.status_code}: {response.text[:200]}")
            except Exception as exc:  # noqa: BLE001 - the crash under test
                with lock:
                    failures.append(f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        list(pool.map(worker, range(THREADS)))
    return failures


def test_sqlite_is_built_in_serialized_mode() -> None:
    """The premise of ``check_same_thread=False``: sqlite3 serialises for us."""
    assert sqlite3.threadsafety == 3


def test_served_reads_survive_concurrent_threads(served: TestClient) -> None:
    """Before the fix this produced 48 ``sqlite3.ProgrammingError``s."""
    assert _hammer(served, "/levels") == []


def test_served_rating_reads_survive_concurrent_threads(served: TestClient) -> None:
    """A second endpoint, so the guard is not one route's accident."""
    assert _hammer(served, "/rating") == []


def test_served_writes_survive_concurrent_threads(served: TestClient) -> None:
    """Concurrent profile writes: distinct connections, one file, one lock."""
    failures: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def writer(index: int) -> None:
        barrier.wait(timeout=30)
        for _attempt in range(REQUESTS_PER_THREAD):
            try:
                response = served.put(
                    "/profile",
                    json={"display_name": f"Owner-{index}", "challenge_mode": "stretch", "at": NOW},
                )
                if response.status_code != 200:
                    with lock:
                        failures.append(f"HTTP {response.status_code}: {response.text[:200]}")
            except Exception as exc:  # noqa: BLE001 - the crash under test
                with lock:
                    failures.append(f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        list(pool.map(writer, range(THREADS)))
    assert failures == []
    profile = served.get("/profile").json()["profile"]
    assert profile["display_name"].startswith("Owner-")
    assert profile["challenge_mode"] == "stretch"


def test_one_repository_shared_across_threads_reads_and_writes(
    tmp_path: Path, datasets: Datasets
) -> None:
    """Store level: a single repository object driven by several threads."""
    repo = SQLiteRepository(tmp_path / "shared.db")
    repo.initialize(datasets.levels)
    repo.save_profile(
        PlayerProfile(
            display_name="Owner",
            challenge_mode=ChallengeMode.BALANCED,
            preferred_color=PreferredColor.WHITE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    failures: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def churn(index: int) -> None:
        barrier.wait(timeout=30)
        try:
            for _attempt in range(REQUESTS_PER_THREAD):
                repo.list_levels()
                repo.get_profile()
                repo.save_profile(
                    PlayerProfile(
                        display_name=f"Owner-{index}",
                        challenge_mode=ChallengeMode.BALANCED,
                        preferred_color=PreferredColor.WHITE,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                repo.list_games()
        except Exception as exc:  # noqa: BLE001 - the crash under test
            with lock:
                failures.append(f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        list(pool.map(churn, range(THREADS)))
    assert failures == []
    profile = repo.get_profile()
    assert profile is not None and profile.display_name.startswith("Owner-")
    repo.close()


def test_single_in_progress_guard_holds_under_concurrent_game_creation(
    tmp_path: Path, datasets: Datasets
) -> None:
    """The read-modify-write composite must stay atomic across threads.

    ``create_game`` reads "is a game already in progress?" and then inserts.
    Without the repository lock two threads interleave between the two steps and
    both inserts succeed, breaking DATA_MODEL's single-in-progress invariant.
    """
    from chessmentor.models import Color, Game, GameSource, GameStatus
    from chessmentor.store import ConflictError

    repo = SQLiteRepository(tmp_path / "races.db")
    repo.initialize(datasets.levels)
    barrier = threading.Barrier(THREADS)
    conflicts = 0
    created = 0
    lock = threading.Lock()
    other: list[str] = []

    def create(index: int) -> None:
        nonlocal conflicts, created
        game = Game(
            source=GameSource.PLAYED,
            created_at=NOW,
            seed=1000 + index,
            player_color=Color.WHITE,
            level_id=5,
            level_elo=datasets.levels[4].elo_internal,
            recommended_level_id=5,
            level_overridden=False,
            status=GameStatus.IN_PROGRESS,
            ply_count=0,
            book_depth=0,
            rated=False,
        )
        barrier.wait(timeout=30)
        try:
            repo.create_game(game)
            with lock:
                created += 1
        except ConflictError:
            with lock:
                conflicts += 1
        except Exception as exc:  # noqa: BLE001 - the crash under test
            with lock:
                other.append(f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        list(pool.map(create, range(THREADS)))

    assert other == []
    assert created == 1, f"{created} games created concurrently; the invariant allows exactly one"
    assert conflicts == THREADS - 1
    assert len(repo.list_games(status=GameStatus.IN_PROGRESS)) == 1
    repo.close()
