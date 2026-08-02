"""Wiring shared by the two edges: datasets, repository, and the fixture defaults.

The API and CLI must agree on where the database lives, which committed datasets
are loaded, and what "the fixture feed" means with zero configuration (US-1), so
that agreement lives here rather than being duplicated twice.
"""

from __future__ import annotations

import os
from pathlib import Path

from .datasets import DATA_DIR, Datasets, load_datasets
from .service import NewsAlphaService, active_window_days_from_env
from .store.base import Repository
from .store.memory import InMemoryRepository
from .store.sqlite import SQLiteRepository, default_db_path

#: Project root (`projects/newsalpha/`), the anchor for the committed fixture paths.
PROJECT_ROOT = DATA_DIR.parent

#: The committed corpus `newsalpha ingest` reads when no `--path` is given (US-1).
DEFAULT_FIXTURE_FEED = PROJECT_ROOT / "evals" / "fixtures" / "articles.jsonl"

#: The committed market series `newsalpha prices load --source fixture` reads.
DEFAULT_FIXTURE_MARKET = PROJECT_ROOT / "evals" / "fixtures" / "market" / "seed_1"

MEMORY_DB = ":memory:"


def build_datasets(
    data_dir: Path | str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Datasets:
    """Load and validate the committed datasets (FR-3)."""
    return load_datasets(
        data_dir, active_window_days=active_window_days_from_env(environ, default=30)
    )


def build_repository(db_path: str | Path | None = None) -> Repository:
    """`:memory:` -> the in-memory backend; anything else -> SQLite at that path."""
    resolved = str(db_path) if db_path is not None else str(default_db_path(dict(os.environ)))
    if resolved == MEMORY_DB:
        return InMemoryRepository()
    repository = SQLiteRepository(resolved)
    repository.initialize()  # CREATE TABLE IF NOT EXISTS: safe on an existing database
    return repository


def build_service(
    *,
    db_path: str | Path | None = None,
    data_dir: Path | str | None = None,
    repository: Repository | None = None,
    datasets: Datasets | None = None,
    environ: dict[str, str] | None = None,
) -> NewsAlphaService:
    """A fully wired service; callers may inject either half for tests."""
    if datasets is None:
        datasets = build_datasets(data_dir, environ=environ)
    if repository is None:
        repository = build_repository(db_path)
    return NewsAlphaService(repository, datasets, environ=environ)


__all__ = [
    "DEFAULT_FIXTURE_FEED",
    "DEFAULT_FIXTURE_MARKET",
    "MEMORY_DB",
    "PROJECT_ROOT",
    "build_datasets",
    "build_repository",
    "build_service",
]
