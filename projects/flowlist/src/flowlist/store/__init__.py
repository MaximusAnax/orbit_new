"""Persistence behind a repository interface.

``SqliteRepository`` is the default backend (stdlib ``sqlite3``, schema in
DATA_MODEL 4); ``InMemoryRepository`` is the test backend.
"""

from flowlist.store.base import Repository
from flowlist.store.memory import InMemoryRepository
from flowlist.store.sqlite import SCHEMA, SqliteRepository

#: Default database location (SCOPE Architecture-CLI).
DEFAULT_DB_PATH = "~/.flowlist/flowlist.db"

__all__ = [
    "DEFAULT_DB_PATH",
    "SCHEMA",
    "InMemoryRepository",
    "Repository",
    "SqliteRepository",
]
