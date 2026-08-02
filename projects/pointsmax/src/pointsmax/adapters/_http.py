"""Minimal stdlib HTTP helper shared by the live adapters.

Kept in its own module so the offline path never imports networking code.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class LiveAdapterUnavailable(RuntimeError):
    """A live adapter was used without the configuration or credentials it needs."""


def fetch_json(url: str, *, token: str | None = None, timeout: float = 20.0) -> Any:
    """GET ``url`` and parse the JSON body, raising :class:`LiveAdapterUnavailable`."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:  # pragma: no cover - network path
        raise LiveAdapterUnavailable(f"could not fetch {url}: {exc}") from exc
