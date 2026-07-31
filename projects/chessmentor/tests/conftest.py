"""Shared fixtures.

Tests use the offline adapters exclusively (FR-16): the internal analyst and the
committed book.  Analyst budgets here are deliberately *small* — the FR-9/FR-11
logic under test is budget-independent, and ``JUDGE_BUDGET`` belongs to the eval
suite, which is where analyst strength is actually gated (M7).
"""

from __future__ import annotations

import pytest
from chessmentor.adapters import CommittedBook, InternalAnalyst
from chessmentor.datasets import Datasets, load_datasets
from chessmentor.models import Level

#: A cheap analyst configuration for unit tests (production uses JUDGE_BUDGET).
TEST_ANALYST_BUDGET = 700


@pytest.fixture(scope="session")
def datasets() -> Datasets:
    return load_datasets()


@pytest.fixture(scope="session")
def levels(datasets: Datasets) -> list[Level]:
    return datasets.levels


@pytest.fixture(scope="session")
def book(datasets: Datasets) -> CommittedBook:
    return datasets.book


@pytest.fixture(scope="session")
def analyst() -> InternalAnalyst:
    return InternalAnalyst(max_depth=4)


@pytest.fixture
def now() -> str:
    return "2026-07-31T12:00:00Z"
