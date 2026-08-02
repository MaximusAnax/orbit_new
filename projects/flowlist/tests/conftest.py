"""Pytest fixtures and markers for the flowlist suite.

Plain helpers live in :mod:`flowlist_testkit` (a deliberately unique module
name: the workspace runs every project's tests together, so a generic
``helpers``/``tests`` module would collide).
"""

from __future__ import annotations

import pytest
from flowlist.store.memory import InMemoryRepository


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: long-running checks (the FR-8 n=500 perf smoke); run with --runslow",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="run the slow-marked flowlist tests (FR-8 perf NFR)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """EVALS 7: the perf smoke is excluded from the default suite."""
    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="slow: pass --runslow to include the FR-8 perf smoke")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()
