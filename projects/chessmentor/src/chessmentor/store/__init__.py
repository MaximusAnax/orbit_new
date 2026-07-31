"""Persistence behind a repository interface (SQLite default, in-memory for tests)."""

from __future__ import annotations

from .memory_repo import InMemoryRepository
from .repository import ConflictError, NotFoundError, Repository
from .sqlite_repo import SCHEMA, SQLiteRepository

__all__ = [
    "SCHEMA",
    "ConflictError",
    "InMemoryRepository",
    "NotFoundError",
    "Repository",
    "SQLiteRepository",
]
