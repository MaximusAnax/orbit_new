"""Session fixtures. Factory helpers live in `newsalpha_testkit` (uniquely named so
several projects' test suites can share one workspace-wide pytest run)."""

from __future__ import annotations

import pytest
from newsalpha.datasets import Datasets, load_datasets


@pytest.fixture(scope="session")
def datasets() -> Datasets:
    return load_datasets()
