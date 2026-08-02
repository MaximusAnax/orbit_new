"""FR-2: pure matching of an entry against the curated misattribution dataset.

The match rule is deterministic and deliberately narrow (SCOPE.md non-goal 9):
flag iff the record's ``pattern`` appears as a *contiguous* run of normalized
tokens inside the entry text AND (the record lists no claimed authors OR the
entry's normalized author is one of them).  Flags are informational; nothing
in the system may block on them.
"""

from __future__ import annotations

from collections.abc import Sequence

from almanac.engine.normalize import contains_phrase, normalize_author, tokenize
from almanac.models import AttributionFinding, MisattributionRecord


def matches(record: MisattributionRecord, text: str, author: str | None) -> bool:
    """True when ``record`` fires for this (text, claimed author) pair."""
    if record.claimed_authors:
        claimed = {normalize_author(name) for name in record.claimed_authors}
        if normalize_author(author) not in claimed:
            return False
    return contains_phrase(tokenize(text), tokenize(record.pattern))


def find_misattributions(
    records: Sequence[MisattributionRecord], text: str, author: str | None
) -> list[AttributionFinding]:
    """All findings for an entry, ordered by record id for determinism."""
    hits = [record for record in records if matches(record, text, author)]
    hits.sort(key=lambda record: record.id)
    return [
        AttributionFinding(
            misattribution_id=record.id,
            verdict=record.verdict,
            likely_origin=record.likely_origin,
            note=record.note,
            reference_url=record.reference_url,
        )
        for record in hits
    ]
