"""Persistence behind a repository interface: SQLite by default, in-memory for tests."""

from .base import Repository, RepositoryError
from .memory import InMemoryRepository
from .sqlite import DB_ENV, DEFAULT_DB_PATH, SQLiteRepository, default_db_path

__all__ = [
    "DB_ENV",
    "DEFAULT_DB_PATH",
    "InMemoryRepository",
    "Repository",
    "RepositoryError",
    "SQLiteRepository",
    "default_db_path",
]
