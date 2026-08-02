"""Offline ``ListingsFeed``: committed JSONL fixtures (the default for tests and evals).

Each line is one JSON object with the ``RawListing`` fields. Output order is
deterministic — ``(sold_at or listed_at, external_id)`` — so two runs over the
same file produce byte-identical ingests (FR-14).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..models import ListingSource, RawListing

__all__ = ["FixtureListingsFeed"]


class FixtureListingsFeed:
    """Reads a committed JSONL file of raw listings."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def fetch(self) -> list[RawListing]:
        rows: list[RawListing] = []
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{self.path}:{number}: invalid JSON ({exc})") from None
                payload.setdefault("source", ListingSource.FIXTURE.value)
                try:
                    rows.append(RawListing.model_validate(payload))
                except ValueError as exc:
                    raise ValueError(f"{self.path}:{number}: {exc}") from None
        rows.sort(key=lambda row: (row.sold_at or row.listed_at, row.external_id))
        return rows
