"""Persistence behind a repository interface (SQLite default, in-memory for tests)."""

from dresscast.store.memory import InMemoryRepository
from dresscast.store.migrations import MIGRATIONS, SCHEMA_V1, migrate
from dresscast.store.repository import Repository, merge_suggestion
from dresscast.store.sqlite_repo import SqliteRepository

__all__ = [
    "MIGRATIONS",
    "SCHEMA_V1",
    "InMemoryRepository",
    "Repository",
    "SqliteRepository",
    "merge_suggestion",
    "migrate",
]
