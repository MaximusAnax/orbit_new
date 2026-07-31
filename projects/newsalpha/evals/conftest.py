"""Eval-suite fixtures.

Hermeticity is enforced, not asserted in prose (EVALS.md): every eval test runs
with `socket.socket` monkeypatched to raise, so a metric that reached for the
network would fail loudly instead of quietly depending on it.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest


class NetworkAccessDenied(RuntimeError):
    """Raised when an eval touches the network."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def _blocked(*args: Any, **kwargs: Any) -> None:
        raise NetworkAccessDenied("the eval suite is hermetic: no sockets may be opened")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    yield
