"""FR-6 — the progression decision ladder.

``next_prescription`` evaluates the clauses in the order SCOPE.md lays out and
**stops at the first match**, emitting exactly one action.  The firing clause id
is returned alongside the action so that precedence — not just outcomes — is
part of the contract (M6 asserts action, clause *and* load).

``ref`` is the most recent logged session containing the exercise and ``prev``
the one before it; clauses L1 and B1 additionally need the session before that,
so they cannot fire on fewer than three sessions of history.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from formcoach.models import (
    LOWER_BODY_MUSCLES,
    Exercise,
    LoggedSession,
    NextPrescription,
    Prescription,
    ProgressionAction,
    SetLog,
)

#: FR-3 step 7 — week 5 works from a 15 % lighter basis than week 4.
DELOAD_BASIS = 0.85

UPPER_BODY_INCREASE = 1.025
LOWER_BODY_INCREASE = 1.05
AUTOREGULATION_INCREASE = 1.025
AUTOREGULATION_DECREASE = 0.95
REACTIVE_DELOAD_FACTOR = 0.90
E1RM_REGRESSION_RATIO = 0.95
BODYWEIGHT_REGRESSION_RATIO = 0.90
BODYWEIGHT_MAX_SETS = 5
BODYWEIGHT_VARIATION_MIN_SETS = 3


def round_to_increment(value: float, increment: float) -> float:
    """``i * floor(x / i + 0.5)`` — round-half-up onto the equipment's step."""
    if increment <= 0:
        raise ValueError("increment must be positive")
    steps = math.floor(value / increment + 0.5 + 1e-9)
    return round(steps * increment, 6)


def is_lower_body(exercise: Exercise) -> bool:
    """FR-6 note (iii) — every primary muscle in the lower-body set."""
    return all(m in LOWER_BODY_MUSCLES for m in exercise.primary_muscles)


def sessions_with(history: Sequence[LoggedSession], exercise_id: str) -> list[LoggedSession]:
    """History containing the exercise, most recent first."""
    matching = [s for s in history if s.sets_for(exercise_id)]
    return sorted(matching, key=lambda s: s.workout.performed_at, reverse=True)


def top_set(sets: Sequence[SetLog]) -> SetLog:
    """Heaviest set; ties resolved by more reps, then by earliest set index."""
    return max(sets, key=lambda s: (s.weight_kg, s.reps, -s.set_index))


def best_e1rm(sets: Sequence[SetLog]) -> float | None:
    """Best estimated 1RM among loaded *and* RIR-rated sets, else ``None``."""
    values = [s.e1rm_kg for s in sets if s.e1rm_kg is not None]
    return max(values) if values else None


def _mean_rir(sets: Sequence[SetLog]) -> float | None:
    rated = [s.rir for s in sets if s.rir is not None]
    return sum(rated) / len(rated) if rated else None


def _all_rated_at_least(sets: Sequence[SetLog], target: float) -> bool:
    """True when no *reported* RIR is below the target (unrated sets abstain)."""
    return all(s.rir >= target for s in sets if s.rir is not None)


def _all_reached(sets: Sequence[SetLog], rep_high: int) -> bool:
    return bool(sets) and all(s.reps >= rep_high for s in sets)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def next_prescription(
    history: Sequence[LoggedSession],
    prescription: Prescription,
    exercise: Exercise,
    *,
    week: int,
    increment_kg: float | None,
    harder_variant: Exercise | None = None,
) -> NextPrescription:
    """FR-6 — one action, one clause, one numeric load suggestion.

    ``increment_kg`` is the smallest non-null kg increment among the exercise's
    equipment; when it is ``None`` the loaded branch is not applicable and the
    bodyweight branch runs (DATA_MODEL § LoadIncrement).
    """
    logs = sessions_with(history, exercise.id)
    rep_low, rep_high = prescription.rep_low, prescription.rep_high
    target_rir = prescription.target_rir

    if not logs:
        return _build(
            prescription,
            exercise.id,
            ProgressionAction.HOLD,
            "L6",
            None,
            rep_high,
            "No logged history for this exercise yet — pick a load you can keep in the "
            f"{rep_low}-{rep_high} rep range at RIR {target_rir:g}.",
            week,
        )

    ref = logs[0].sets_for(exercise.id)
    prev = logs[1].sets_for(exercise.id) if len(logs) > 1 else []
    prev2 = logs[2].sets_for(exercise.id) if len(logs) > 2 else []

    use_loaded = increment_kg is not None and any(s.weight_kg > 0 for s in ref)
    if use_loaded:
        assert increment_kg is not None
        return _loaded_branch(prescription, exercise, ref, prev, prev2, increment_kg, week)
    return _bodyweight_branch(prescription, exercise, ref, prev, prev2, harder_variant, week)


# ------------------------------------------------------------------ loaded branch


def _loaded_branch(
    prescription: Prescription,
    exercise: Exercise,
    ref: list[SetLog],
    prev: list[SetLog],
    prev2: list[SetLog],
    increment_kg: float,
    week: int,
) -> NextPrescription:
    rep_low, rep_high = prescription.rep_low, prescription.rep_high
    target_rir = prescription.target_rir
    last_load = top_set(ref).weight_kg
    step = LOWER_BODY_INCREASE if is_lower_body(exercise) else UPPER_BODY_INCREASE

    def emit(action: ProgressionAction, clause: str, factor: float, why: str):
        return _build(
            prescription,
            exercise.id,
            action,
            clause,
            _load(last_load * factor, increment_kg, week),
            rep_high,
            why,
            week,
        )

    ref_best, prev_best, prev2_best = best_e1rm(ref), best_e1rm(prev), best_e1rm(prev2)
    if (
        ref_best is not None
        and prev_best is not None
        and prev2_best is not None
        and ref_best < E1RM_REGRESSION_RATIO * prev_best
        and ref_best < E1RM_REGRESSION_RATIO * prev2_best
    ):
        return emit(
            ProgressionAction.DELOAD_RECOMMEND,
            "L1",
            REACTIVE_DELOAD_FACTOR,
            "Estimated 1RM has fallen more than 5% for two sessions running — drop to "
            "90% and rebuild.",
        )

    if any(s.reps <= rep_low - 2 for s in ref):
        return emit(
            ProgressionAction.HOLD,
            "L2",
            1.0,
            f"A set finished 2 or more reps under {rep_low} — repeat this load until the "
            "range is complete.",
        )

    if _all_reached(ref, rep_high) and _all_rated_at_least(ref, target_rir):
        pct = (step - 1.0) * 100.0
        return emit(
            ProgressionAction.INCREASE_LOAD,
            "L3",
            step,
            f"All sets hit {rep_high} reps at RIR >= {target_rir:g} — add {pct:.1f}%.",
        )

    mean_rir = _mean_rir(ref)
    if mean_rir is not None and mean_rir < target_rir - 1:
        return emit(
            ProgressionAction.DECREASE_LOAD,
            "L4",
            AUTOREGULATION_DECREASE,
            f"Average RIR {mean_rir:.1f} sat more than one below the {target_rir:g} "
            "target — take 5% off.",
        )

    if mean_rir is not None and mean_rir > target_rir + 1:
        return emit(
            ProgressionAction.INCREASE_LOAD,
            "L5",
            AUTOREGULATION_INCREASE,
            f"Average RIR {mean_rir:.1f} sat more than one above the {target_rir:g} "
            "target — add 2.5%.",
        )

    return emit(
        ProgressionAction.HOLD,
        "L6",
        1.0,
        "Reps and effort are where they should be — repeat this load.",
    )


# -------------------------------------------------------------- bodyweight branch


def _bodyweight_branch(
    prescription: Prescription,
    exercise: Exercise,
    ref: list[SetLog],
    prev: list[SetLog],
    prev2: list[SetLog],
    harder_variant: Exercise | None,
    week: int,
) -> NextPrescription:
    rep_low, rep_high = prescription.rep_low, prescription.rep_high
    target_rir = prescription.target_rir
    achieved = _clamp(max(s.reps for s in ref), rep_low, rep_high)
    set_count = len(ref)

    def emit(action, clause, target_reps, why, exercise_id=None):
        return _build(
            prescription,
            exercise_id or exercise.id,
            action,
            clause,
            None,
            target_reps,
            why,
            week,
        )

    total, total_prev, total_prev2 = (
        sum(s.reps for s in ref),
        sum(s.reps for s in prev),
        sum(s.reps for s in prev2),
    )
    if (
        prev
        and prev2
        and total < BODYWEIGHT_REGRESSION_RATIO * total_prev
        and total < BODYWEIGHT_REGRESSION_RATIO * total_prev2
    ):
        return emit(
            ProgressionAction.DELOAD_RECOMMEND,
            "B1",
            rep_low,
            "Total reps have fallen more than 10% for two sessions running — take an "
            "easier week before pushing again.",
        )

    if any(s.reps <= rep_low - 2 for s in ref):
        return emit(
            ProgressionAction.HOLD,
            "B2",
            achieved,
            f"A set finished 2 or more reps under {rep_low} — repeat this session as is.",
        )

    topped_out = _all_reached(ref, rep_high) and _all_rated_at_least(ref, target_rir)
    if topped_out and harder_variant is not None and set_count >= BODYWEIGHT_VARIATION_MIN_SETS:
        return emit(
            ProgressionAction.PROGRESS_VARIATION,
            "B3",
            rep_low,
            f"Every set reached {rep_high} reps at RIR >= {target_rir:g} — step up to "
            f"{harder_variant.name} and restart at {rep_low} reps.",
            exercise_id=harder_variant.id,
        )

    if topped_out and harder_variant is None and set_count < BODYWEIGHT_MAX_SETS:
        return emit(
            ProgressionAction.ADD_SET,
            "B4",
            rep_high,
            f"Every set reached {rep_high} reps at RIR >= {target_rir:g} and there is no "
            f"harder variation — add a {set_count + 1}th set.",
        )

    if topped_out and set_count == BODYWEIGHT_MAX_SETS:
        return emit(
            ProgressionAction.HOLD,
            "B5",
            rep_high,
            f"Five sets at {rep_high} reps is the ceiling for this movement — hold and "
            "keep the tempo strict.",
        )

    return emit(
        ProgressionAction.ADD_REPS,
        "B6",
        _clamp(achieved + 1, rep_low, rep_high),
        f"Add a rep: aim for {_clamp(achieved + 1, rep_low, rep_high)} on every set.",
    )


# ------------------------------------------------------------------------ helpers


def _load(raw: float, increment_kg: float, week: int) -> float:
    """Round onto the equipment step, applying the week-5 deload basis."""
    basis = DELOAD_BASIS if week == 5 else 1.0
    return round_to_increment(raw * basis, increment_kg)


def _build(
    prescription: Prescription,
    exercise_id: str,
    action: ProgressionAction,
    clause: str,
    suggested_load_kg: float | None,
    target_reps: int,
    rationale: str,
    week: int,
) -> NextPrescription:
    sets = prescription.sets + 1 if action is ProgressionAction.ADD_SET else prescription.sets
    return NextPrescription(
        exercise_id=exercise_id,
        position=prescription.position,
        sets=sets,
        rep_low=prescription.rep_low,
        rep_high=prescription.rep_high,
        target_rir=prescription.target_rir,
        rest_s=prescription.rest_s,
        load_note=prescription.load_note,
        action=action,
        clause=clause,
        suggested_load_kg=suggested_load_kg,
        target_reps=_clamp(target_reps, prescription.rep_low, prescription.rep_high),
        substituted_for=None,
        dropped_reason=None,
        rationale=rationale,
    )
