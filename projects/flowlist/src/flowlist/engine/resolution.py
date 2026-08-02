"""Feature resolution and coverage reporting (FR-3, FR-4, D10).

Resolution is a pure read-time policy (DATA_MODEL 2.2): nothing is written
back, so provenance is never lost and a manual override can be removed without
rewriting history.  Effective features are the field-wise first non-null value
walking sources in precedence order, default
``manual > local_analysis > streaming > import > fixture``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from flowlist.engine.models import (
    DEFAULT_PRECEDENCE,
    AudioFeatures,
    CoverageReport,
    FeatureSource,
    ResolvedFeatures,
)

#: Fields whose presence coverage reports on.  ``mode`` is omitted because
#: DATA_MODEL 2.2 makes it set/null together with ``key_pc``, so it would only
#: ever duplicate that row; ``danceability`` is omitted because its component
#: is off by default (FR-6).
COVERAGE_FIELDS: tuple[str, ...] = ("bpm", "key_pc", "energy", "loudness_db")

#: Fields resolved independently of one another.
_SCALAR_FIELDS: tuple[str, ...] = (
    "bpm",
    "energy",
    "danceability",
    "loudness_db",
    "valence",
)


def resolve_features(
    rows: Iterable[AudioFeatures],
    precedence: Sequence[FeatureSource] = DEFAULT_PRECEDENCE,
    *,
    track_id: str | None = None,
) -> ResolvedFeatures:
    """Merge per-source rows into one effective feature record.

    ``key_pc`` and ``mode`` are resolved as a *pair* so a high-precedence key
    can never be paired with a lower-precedence mode — the set-together
    invariant of DATA_MODEL 2.2 holds through resolution, not just at write.
    Rows whose source is absent from ``precedence`` are ignored, which is what
    makes ``--providers`` a real filter rather than a hint.
    """
    by_source: dict[FeatureSource, AudioFeatures] = {}
    resolved_track_id = track_id
    for row in rows:
        # Later rows for the same source would be a store bug (composite PK);
        # keep the first so resolution stays deterministic regardless.
        by_source.setdefault(row.source, row)
        resolved_track_id = resolved_track_id or row.track_id

    ordered = [by_source[source] for source in precedence if source in by_source]

    values: dict[str, object] = {}
    field_sources: dict[str, FeatureSource] = {}

    for field in _SCALAR_FIELDS:
        for row in ordered:
            value = getattr(row, field)
            if value is not None:
                values[field] = value
                field_sources[field] = row.source
                break

    for row in ordered:
        if row.key_pc is not None and row.mode is not None:
            values["key_pc"] = row.key_pc
            values["mode"] = row.mode
            field_sources["key_pc"] = row.source
            field_sources["mode"] = row.source
            break

    confidence = ordered[0].confidence if ordered else 1.0
    return ResolvedFeatures(
        track_id=resolved_track_id,
        confidence=confidence,
        field_sources=field_sources,
        **values,  # type: ignore[arg-type]
    )


def resolve_many(
    rows_by_track: Mapping[str, Iterable[AudioFeatures]],
    precedence: Sequence[FeatureSource] = DEFAULT_PRECEDENCE,
) -> dict[str, ResolvedFeatures]:
    """Resolve a whole playlist's worth of tracks in one call."""
    return {
        track_id: resolve_features(rows, precedence, track_id=track_id)
        for track_id, rows in rows_by_track.items()
    }


def missing_fields(
    features: ResolvedFeatures, fields: Sequence[str] = COVERAGE_FIELDS
) -> list[str]:
    """The coverage fields this record still lacks."""
    return [field for field in fields if getattr(features, field) is None]


def coverage_report(
    resolved: Mapping[str, ResolvedFeatures],
    fields: Sequence[str] = COVERAGE_FIELDS,
) -> CoverageReport:
    """Per-field resolved/missing counts plus per-track gaps (FR-3).

    Counted over *tracks*, not playlist entries: a duplicated track has one
    set of facts (D8 keeps entries distinct only for the optimizer).
    """
    fields = tuple(fields)
    resolved_counts = dict.fromkeys(fields, 0)
    missing_counts: dict[str, int] = {}
    per_track: dict[str, list[str]] = {}
    full = 0

    for track_id in sorted(resolved):
        gaps = missing_fields(resolved[track_id], fields)
        for field in fields:
            if field in gaps:
                missing_counts[field] = missing_counts.get(field, 0) + 1
            else:
                resolved_counts[field] += 1
        if gaps:
            per_track[track_id] = gaps
        else:
            full += 1

    return CoverageReport(
        tracks=len(resolved),
        full=full,
        fields=list(fields),
        resolved=resolved_counts,
        missing=missing_counts,
        per_track_missing=per_track,
    )


def fully_resolved(features: ResolvedFeatures, fields: Sequence[str] = COVERAGE_FIELDS) -> bool:
    """Whether a track needs no further provider calls (FR-3 idempotence)."""
    return not missing_fields(features, fields)


__all__ = [
    "COVERAGE_FIELDS",
    "coverage_report",
    "fully_resolved",
    "missing_fields",
    "resolve_features",
    "resolve_many",
]
