"""Persistence behind a repository interface (SCOPE.md §Store)."""

from .base import SUPPRESSING_STATUSES, Repository
from .memory import InMemoryRepository
from .sqlite import SCHEMA, SqliteRepository

__all__ = [
    "SCHEMA",
    "SUPPRESSING_STATUSES",
    "InMemoryRepository",
    "Repository",
    "SqliteRepository",
]
