"""The ``AttributionChecker`` capability (FR-2).

Checks a (text, claimed author) pair for known misattributions.  Findings are
informational footnotes: nothing in Almanac blocks on them, and the absence of
a finding is explicitly not verification (SCOPE.md non-goal 9).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from almanac.models import AttributionFinding


@runtime_checkable
class AttributionChecker(Protocol):
    name: str

    def check(
        self, text: str, author: str | None
    ) -> list[AttributionFinding]: ...  # pragma: no cover - protocol definition


class ChainedAttributionChecker:
    """Runs several checkers and concatenates their findings, de-duplicated.

    Used when the live Wikiquote adapter is switched on: the curated dataset
    stays authoritative and the live lookup only *adds* cases.  A live checker
    that raises is skipped — attribution is a footnote and must never break a
    capture (FR-2).
    """

    name = "chained"

    def __init__(self, checkers: Sequence[AttributionChecker]) -> None:
        self._checkers = list(checkers)

    def check(self, text: str, author: str | None) -> list[AttributionFinding]:
        out: list[AttributionFinding] = []
        seen: set[tuple[str | None, str]] = set()
        for checker in self._checkers:
            try:
                findings = checker.check(text, author)
            except Exception:
                continue
            for finding in findings:
                key = (finding.misattribution_id, finding.note)
                if key not in seen:
                    seen.add(key)
                    out.append(finding)
        return out
