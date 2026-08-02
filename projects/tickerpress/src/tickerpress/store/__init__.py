"""Persistence behind a repository interface (DATA_MODEL §4)."""

from __future__ import annotations

from .repository import ArticleTextRow, Repository, StoryRelevance, UndeliveredStory
from .schema import SCHEMA_SQL, SCHEMA_VERSION
from .sqlite_store import InMemoryRepository, SQLiteRepository

__all__ = [
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "ArticleTextRow",
    "InMemoryRepository",
    "Repository",
    "SQLiteRepository",
    "StoryRelevance",
    "UndeliveredStory",
]
