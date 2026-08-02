"""Persistence behind a repository interface.

``SQLiteRepository`` is the default backend (stdlib ``sqlite3``, DDL per
DATA_MODEL.md); ``InMemoryRepository`` is the tests' and evals' backend.
"""

from .base import Repository, RepositoryError
from .memory import InMemoryRepository
from .sqlite import DB_PATH_ENV, DEFAULT_DB_PATH, SQLiteRepository

__all__ = [
    "DB_PATH_ENV",
    "DEFAULT_DB_PATH",
    "InMemoryRepository",
    "Repository",
    "RepositoryError",
    "SQLiteRepository",
]
