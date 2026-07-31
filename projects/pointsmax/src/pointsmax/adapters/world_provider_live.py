"""Live ``WorldProvider``: refresh the rewards world from a remote feed.

Non-goal 2 keeps the *shipped* world a committed dataset; this adapter exists so
that refreshing it is a configuration change rather than a code change.  It is
never imported by the offline path, by tests, or by evals.

Configuration (documented in the project README):

* ``POINTSMAX_WORLD_FEED_URL`` — base URL serving the same file names as
  ``data/world/`` (``programs.json``, ``transfers.json``, ...).
* ``POINTSMAX_WORLD_FEED_TOKEN`` — optional bearer token.

The feed is validated with exactly the same FR-1 code path as the committed
dataset, so a bad feed fails loudly instead of poisoning recommendations.
"""

from __future__ import annotations

import os
from typing import Any

from ..models import World
from ._http import LiveAdapterUnavailable, fetch_json
from .world_provider import WORLD_FILE_MODELS, build_world


class LiveWorldProvider:
    """Fetch and validate the world from ``POINTSMAX_WORLD_FEED_URL``."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = 20.0,
        validate: bool = True,
    ) -> None:
        self.base_url = (base_url or os.environ.get("POINTSMAX_WORLD_FEED_URL", "")).rstrip("/")
        self.token = token or os.environ.get("POINTSMAX_WORLD_FEED_TOKEN")
        self.timeout = timeout
        self.validate = validate
        if not self.base_url:
            raise LiveAdapterUnavailable(
                "LiveWorldProvider needs POINTSMAX_WORLD_FEED_URL (or an explicit "
                "base_url); use CommittedWorldProvider for the shipped dataset."
            )

    def raw_files(self) -> dict[str, Any]:
        return {
            file_name: fetch_json(
                f"{self.base_url}/{file_name}", token=self.token, timeout=self.timeout
            )
            for file_name in [*WORLD_FILE_MODELS, "version.json"]
        }

    def load(self) -> World:
        return build_world(self.raw_files(), validate=self.validate)
