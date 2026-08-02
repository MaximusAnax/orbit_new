"""Shared fixtures for the FormCoach engine tests."""

from __future__ import annotations

import pytest
from formcoach.models import Equipment, Experience, Goal, Muscle, UserProfile
from formcoach.store.datasets import load_datasets
from formcoach.store.memory import InMemoryRepository
from formcoach.store.sqlite import SQLiteRepository

ACK = "2026-07-01T08:00:00+00:00"
FULL_GYM = list(Equipment)
HOME_GYM = [Equipment.DUMBBELL, Equipment.BODYWEIGHT]


@pytest.fixture(scope="session")
def datasets():
    """The committed datasets, loaded and integrity-checked once per session."""
    return load_datasets()


@pytest.fixture
def profile() -> UserProfile:
    return UserProfile(
        goal=Goal.HYPERTROPHY,
        experience=Experience.INTERMEDIATE,
        days_per_week=4,
        equipment=FULL_GYM,
        emphasized_muscles=[Muscle.CHEST],
        disclaimer_acknowledged_at=ACK,
        updated_at=ACK,
    )


@pytest.fixture
def memory_repo(datasets):
    repo = InMemoryRepository()
    repo.initialize()
    repo.load_library(datasets)
    return repo


@pytest.fixture
def sqlite_repo(datasets):
    repo = SQLiteRepository(":memory:")
    repo.initialize()
    repo.load_library(datasets)
    yield repo
    repo.close()


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, datasets):
    """Every repository test runs against both backends."""
    backend = InMemoryRepository() if request.param == "memory" else SQLiteRepository(":memory:")
    backend.initialize()
    backend.load_library(datasets)
    yield backend
    backend.close()
