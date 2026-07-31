"""Persistence behind a Repository interface."""

from voicekin.store.base import SCHEMA_VERSION, Repository
from voicekin.store.sqlite import SCHEMA_SQL, SQLiteRepository

__all__ = ["SCHEMA_SQL", "SCHEMA_VERSION", "Repository", "SQLiteRepository"]
