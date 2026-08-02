"""FR-4 — effective-set attribution and the weekly volume report.

Effective sets (SCOPE § Conventions, DATA_MODEL § Units): every logged or
planned set counts 1.0 toward each muscle in ``Exercise.primary_muscles`` and
0.5 toward each muscle in ``Exercise.secondary_muscles``.  *Direct* sets count
only the primary attribution, and are the unit the FR-3 per-session caps and
the M5 constraint-14 check use.

This module is the single implementation of that attribution; programming,
reporting and the evals all route through it so the numbers cannot drift.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from formcoach.models import (
    Datasets,
    Exercise,
    LoggedSession,
    Muscle,
    MuscleVolumeRow,
    VolumeReport,
)

SetAllocation = tuple[Exercise, int]


def iso_week_of(timestamp: str) -> str:
    """ISO week label (``"2026-W31"``) of a caller-supplied ISO-8601 timestamp."""
    moment = datetime.fromisoformat(timestamp)
    year, week, _ = moment.isocalendar()
    return f"{year:04d}-W{week:02d}"


def accumulate_effective(allocations: Iterable[SetAllocation]) -> dict[Muscle, float]:
    """Effective sets per muscle for ``(exercise, sets)`` pairs."""
    totals: dict[Muscle, float] = dict.fromkeys(Muscle, 0.0)
    for exercise, sets in allocations:
        for muscle in exercise.primary_muscles:
            totals[muscle] += float(sets)
        for muscle in exercise.secondary_muscles:
            totals[muscle] += 0.5 * float(sets)
    return totals


def accumulate_direct(allocations: Iterable[SetAllocation]) -> dict[Muscle, int]:
    """Direct (primary-attribution) sets per muscle for ``(exercise, sets)`` pairs."""
    totals: dict[Muscle, int] = dict.fromkeys(Muscle, 0)
    for exercise, sets in allocations:
        for muscle in exercise.primary_muscles:
            totals[muscle] += int(sets)
    return totals


def allocations_from_logs(
    sessions: Sequence[LoggedSession], datasets: Datasets, iso_week: str | None = None
) -> list[SetAllocation]:
    """Flatten logged sets into ``(exercise, 1)`` allocations, optionally by week.

    Sets whose exercise is not in the library are skipped rather than guessed
    at: an unknown exercise has no muscle attribution, so counting it would
    invent volume.
    """
    out: list[SetAllocation] = []
    for session in sessions:
        if iso_week is not None and iso_week_of(session.workout.performed_at) != iso_week:
            continue
        for set_log in session.sets:
            try:
                exercise = datasets.exercise(set_log.exercise_id)
            except KeyError:
                continue
            out.append((exercise, 1))
    return out


def weekly_volume_report(
    sessions: Sequence[LoggedSession],
    datasets: Datasets,
    iso_week: str,
    target_muscles: Sequence[Muscle] = (),
) -> VolumeReport:
    """FR-4 — one row per muscle for a caller-supplied ISO week.

    Every one of the fifteen muscles appears, classified against the committed
    volume landmarks and labelled target or incidental relative to the active
    program's target set.
    """
    allocations = allocations_from_logs(sessions, datasets, iso_week)
    effective = accumulate_effective(allocations)
    direct = accumulate_direct(allocations)
    targets = set(target_muscles)

    rows: list[MuscleVolumeRow] = []
    for muscle in Muscle:
        landmark = datasets.landmarks[muscle]
        sets = effective[muscle]
        rows.append(
            MuscleVolumeRow(
                muscle=muscle,
                effective_sets=sets,
                direct_sets=direct[muscle],
                band=landmark.band(sets),
                is_target=muscle in targets,
                mv=landmark.mv,
                mev=landmark.mev,
                mav=landmark.mav,
                mrv=landmark.mrv,
            )
        )
    return VolumeReport(iso_week=iso_week, rows=rows)
