"""Offline default attribution checker: the committed dataset (FR-2)."""

from __future__ import annotations

from collections.abc import Sequence

from almanac.engine.attribution import find_misattributions
from almanac.models import AttributionFinding, MisattributionRecord


class LocalAttributionChecker:
    """Deterministic matching over ``data/misattributions.json``."""

    name = "local"

    def __init__(self, records: Sequence[MisattributionRecord]) -> None:
        self._records = list(records)

    @property
    def records(self) -> list[MisattributionRecord]:
        return list(self._records)

    def check(self, text: str, author: str | None) -> list[AttributionFinding]:
        return find_misattributions(self._records, text, author)
