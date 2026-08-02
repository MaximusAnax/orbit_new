"""Persistence: the repository interface, its two backends, and dataset loading."""

from formcoach.store.datasets import DatasetError, load_datasets, validate_datasets
from formcoach.store.memory import InMemoryRepository
from formcoach.store.repository import Repository, RepositoryError
from formcoach.store.sqlite import SQLiteRepository

__all__ = [
    "DatasetError",
    "InMemoryRepository",
    "Repository",
    "RepositoryError",
    "SQLiteRepository",
    "load_datasets",
    "validate_datasets",
]
