"""pytest fixtures for the datasweep tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from datasweep.engine.models import Policy
from support_datasweep import FIXED_NOW


@pytest.fixture
def policy() -> Policy:
    return Policy()


@pytest.fixture
def now() -> datetime:
    return FIXED_NOW
