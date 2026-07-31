"""FR-6 — the progression decision ladder.

Every clause is exercised on its own, and the conflict cases (where two clauses
would fire and only the higher one may win) are asserted explicitly: M6 gates
the firing *clause* as well as the action, so precedence is part of the
contract, not an implementation detail.
"""

from __future__ import annotations

import pytest
from formcoach.engine.progression import (
    best_e1rm,
    is_lower_body,
    next_prescription,
    round_to_increment,
    sessions_with,
    top_set,
)
from formcoach.models import LoggedSession, Prescription, ProgressionAction, SetLog, WorkoutLog

BENCH = "barbell-bench-press"
SQUAT = "barbell-back-squat"
PUSHUP = "push-up"
DIP = "dip"


def session(
    day: str, exercise_id: str, sets: list[tuple[float, int, float | None]], *, pain: bool = False
) -> LoggedSession:
    return LoggedSession(
        workout=WorkoutLog(performed_at=f"2026-07-{day}T18:00:00+00:00"),
        sets=[
            SetLog(
                exercise_id=exercise_id,
                set_index=i,
                weight_kg=w,
                reps=r,
                rir=rir,
                pain_flag=pain,
            )
            for i, (w, r, rir) in enumerate(sets)
        ],
    )


def prescription(
    rep_low: int = 5, rep_high: int = 10, target_rir: float = 2.0, sets: int = 3
) -> Prescription:
    return Prescription(
        position=0,
        exercise_id=BENCH,
        sets=sets,
        rep_low=rep_low,
        rep_high=rep_high,
        target_rir=target_rir,
        rest_s=180,
    )


def run(datasets, history, exercise_id=BENCH, *, week=2, presc=None, variant=None):
    exercise = datasets.exercise(exercise_id)
    return next_prescription(
        history,
        presc or prescription(),
        exercise,
        week=week,
        increment_kg=datasets.increment_kg(exercise),
        harder_variant=variant,
    )


# --------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("value", "increment", "expected"),
    [
        (82.0, 2.5, 82.5),
        (81.25, 2.5, 82.5),  # exactly half a step rounds up
        (81.2, 2.5, 80.0),
        (81.0, 2.5, 80.0),
        (100.0, 2.0, 100.0),
        (103.5, 5.0, 105.0),
        (102.4, 5.0, 100.0),
        (61.0, 4.0, 60.0),
    ],
)
def test_fr6_round_to_increment_is_half_up_onto_the_equipment_step(value, increment, expected):
    assert round_to_increment(value, increment) == pytest.approx(expected)


def test_fr6_round_to_increment_rejects_a_non_positive_step():
    with pytest.raises(ValueError):
        round_to_increment(80.0, 0.0)


def test_fr6_upper_lower_split_follows_the_primary_muscles(datasets):
    assert is_lower_body(datasets.exercise(SQUAT))
    assert is_lower_body(datasets.exercise("barbell-romanian-deadlift"))
    assert not is_lower_body(datasets.exercise(BENCH))
    assert not is_lower_body(datasets.exercise("barbell-row"))


def test_fr6_top_set_is_the_heaviest_then_the_most_reps():
    sets = [
        SetLog(exercise_id=BENCH, set_index=0, weight_kg=80.0, reps=8, rir=2.0),
        SetLog(exercise_id=BENCH, set_index=1, weight_kg=90.0, reps=5, rir=1.0),
        SetLog(exercise_id=BENCH, set_index=2, weight_kg=90.0, reps=6, rir=0.0),
    ]
    assert top_set(sets).set_index == 2


def test_fr6_best_e1rm_ignores_unrated_and_bodyweight_sets():
    sets = [
        SetLog(exercise_id=BENCH, set_index=0, weight_kg=100.0, reps=10, rir=None),
        SetLog(exercise_id=BENCH, set_index=1, weight_kg=80.0, reps=8, rir=2.0),
    ]
    assert best_e1rm(sets) == pytest.approx(80.0 * (1 + 10 / 30))
    assert (
        best_e1rm([SetLog(exercise_id=PUSHUP, set_index=0, weight_kg=0.0, reps=10, rir=1.0)])
        is None
    )


def test_fr6_history_is_ordered_most_recent_first():
    history = [
        session("01", BENCH, [(80.0, 8, 2.0)]),
        session("08", BENCH, [(82.5, 8, 2.0)]),
        session("05", SQUAT, [(100.0, 5, 2.0)]),
    ]
    ordered = sessions_with(history, BENCH)
    assert [s.workout.performed_at[8:10] for s in ordered] == ["08", "01"]


# ---------------------------------------------------------------- loaded branch


def test_fr6_l1_reactive_deload_needs_two_consecutive_5_percent_drops(datasets):
    history = [
        session("01", BENCH, [(100.0, 8, 2.0)]),
        session("08", BENCH, [(100.0, 8, 2.0)]),
        session("15", BENCH, [(88.0, 8, 2.0)]),
    ]
    result = run(datasets, history)
    assert result.action is ProgressionAction.DELOAD_RECOMMEND
    assert result.clause == "L1"
    assert result.suggested_load_kg == round_to_increment(88.0 * 0.90, 2.5)


def test_fr6_l1_cannot_fire_with_only_two_sessions(datasets):
    history = [
        session("08", BENCH, [(100.0, 8, 2.0)]),
        session("15", BENCH, [(80.0, 8, 2.0)]),
    ]
    result = run(datasets, history)
    assert result.clause != "L1"


def test_fr6_l1_cannot_fire_when_a_session_was_left_unrated(datasets):
    """An unrated session has no e1RM, so it cannot masquerade as a regression."""
    history = [
        session("01", BENCH, [(100.0, 8, 2.0)]),
        session("08", BENCH, [(100.0, 8, None)]),
        session("15", BENCH, [(85.0, 8, 2.0)]),
    ]
    result = run(datasets, history)
    assert result.clause != "L1"


def test_fr6_l1_does_not_fire_on_a_single_bad_session(datasets):
    history = [
        session("01", BENCH, [(100.0, 8, 2.0)]),
        session("08", BENCH, [(85.0, 8, 2.0)]),
        session("15", BENCH, [(84.0, 8, 2.0)]),
    ]
    result = run(datasets, history)
    assert result.clause != "L1"  # only one of the two comparisons regresses


def test_fr6_l2_missed_volume_holds_the_load(datasets):
    history = [session("15", BENCH, [(100.0, 8, 2.0), (100.0, 3, 0.0)])]
    result = run(datasets, history)
    assert result.action is ProgressionAction.HOLD
    assert result.clause == "L2"
    assert result.suggested_load_kg == 100.0


@pytest.mark.parametrize(("exercise_id", "factor"), [(BENCH, 1.025), (SQUAT, 1.05)])
def test_fr6_l3_double_progression_steps_upper_and_lower_differently(datasets, exercise_id, factor):
    history = [session("15", exercise_id, [(100.0, 10, 2.0)] * 3)]
    result = run(datasets, history, exercise_id)
    assert result.action is ProgressionAction.INCREASE_LOAD
    assert result.clause == "L3"
    assert result.suggested_load_kg == round_to_increment(100.0 * factor, 2.5)
    assert result.target_reps == 10


def test_fr6_l3_requires_every_set_to_reach_the_top_of_the_range(datasets):
    history = [session("15", BENCH, [(100.0, 10, 2.0), (100.0, 9, 2.0)])]
    assert run(datasets, history).clause != "L3"


def test_fr6_l3_requires_every_reported_rir_to_meet_the_target(datasets):
    history = [session("15", BENCH, [(100.0, 10, 2.0), (100.0, 10, 1.0)])]
    assert run(datasets, history).clause != "L3"


def test_fr6_l4_undershoot_takes_five_percent_off(datasets):
    history = [session("15", BENCH, [(100.0, 8, 0.0), (100.0, 8, 0.0)])]
    result = run(datasets, history)
    assert result.action is ProgressionAction.DECREASE_LOAD
    assert result.clause == "L4"
    assert result.suggested_load_kg == round_to_increment(95.0, 2.5)


def test_fr6_l5_overshoot_adds_two_and_a_half_percent(datasets):
    history = [session("15", BENCH, [(100.0, 8, 4.0), (100.0, 8, 4.0)])]
    result = run(datasets, history)
    assert result.action is ProgressionAction.INCREASE_LOAD
    assert result.clause == "L5"
    assert result.suggested_load_kg == round_to_increment(102.5, 2.5)


def test_fr6_l6_holds_when_nothing_else_applies(datasets):
    history = [session("15", BENCH, [(100.0, 8, 2.0), (100.0, 7, 2.0)])]
    result = run(datasets, history)
    assert result.action is ProgressionAction.HOLD
    assert result.clause == "L6"
    assert result.suggested_load_kg == 100.0


def test_fr6_no_history_holds_without_inventing_a_load(datasets):
    result = run(datasets, [])
    assert result.action is ProgressionAction.HOLD
    assert result.suggested_load_kg is None
    assert "history" in result.rationale


# ------------------------------------------------------ loaded conflict cases


def test_fr6_conflict_l1_beats_l2(datasets):
    """A regressing lifter who also missed reps gets the deload, not a hold."""
    history = [
        session("01", BENCH, [(100.0, 8, 2.0)]),
        session("08", BENCH, [(100.0, 8, 2.0)]),
        session("15", BENCH, [(88.0, 8, 2.0), (88.0, 3, 0.0)]),
    ]
    assert run(datasets, history).clause == "L1"


def test_fr6_conflict_l2_beats_l3(datasets):
    """One collapsed set outranks the other sets hitting the top of the range."""
    history = [session("15", BENCH, [(100.0, 10, 3.0), (100.0, 10, 3.0), (100.0, 3, 3.0)])]
    result = run(datasets, history)
    assert result.clause == "L2"
    assert result.action is ProgressionAction.HOLD


def test_fr6_conflict_l3_beats_l5(datasets):
    """Both would add load, but double progression owns the decision."""
    history = [session("15", BENCH, [(100.0, 10, 4.0)] * 3)]
    result = run(datasets, history)
    assert result.clause == "L3"
    assert result.suggested_load_kg == round_to_increment(102.5, 2.5)


def test_fr6_conflict_l1_beats_l4(datasets):
    history = [
        session("01", BENCH, [(100.0, 8, 2.0)]),
        session("08", BENCH, [(100.0, 8, 2.0)]),
        session("15", BENCH, [(85.0, 8, 0.0)]),
    ]
    assert run(datasets, history).clause == "L1"


# ------------------------------------------------------------ increment rounding


@pytest.mark.parametrize(
    ("exercise_id", "increment"),
    [
        (BENCH, 2.5),
        ("dumbbell-bench-press", 2.0),
        ("machine-chest-press", 5.0),
        ("kettlebell-swing", 4.0),
    ],
)
def test_fr6_load_is_rounded_to_the_exercises_own_increment(datasets, exercise_id, increment):
    exercise = datasets.exercise(exercise_id)
    assert datasets.increment_kg(exercise) == increment
    history = [session("15", exercise_id, [(60.0, 8, 2.0), (60.0, 7, 2.0)])]
    result = run(datasets, history, exercise_id)
    assert result.suggested_load_kg is not None
    assert result.suggested_load_kg % increment == pytest.approx(0.0, abs=1e-6)


def test_fr6_multi_equipment_exercises_use_the_smallest_increment(datasets):
    goblet = datasets.exercise("goblet-squat")
    assert set(e.value for e in goblet.equipment) == {"dumbbell", "kettlebell"}
    assert datasets.increment_kg(goblet) == 2.0


# ------------------------------------------------------------ bodyweight branch


def test_fr6_bodyweight_branch_is_chosen_when_the_increment_is_null(datasets):
    assert datasets.increment_kg(datasets.exercise(PUSHUP)) is None
    history = [session("15", PUSHUP, [(0.0, 12, 2.0), (0.0, 11, 2.0)])]
    result = run(datasets, history, PUSHUP)
    assert result.clause.startswith("B")
    assert result.suggested_load_kg is None


def test_fr6_b1_bodyweight_deload_on_two_rep_regressions(datasets):
    history = [
        session("01", PUSHUP, [(0.0, 20, 2.0)] * 3),
        session("08", PUSHUP, [(0.0, 20, 2.0)] * 3),
        session("15", PUSHUP, [(0.0, 12, 0.0)] * 3),
    ]
    result = run(datasets, history, PUSHUP)
    assert result.action is ProgressionAction.DELOAD_RECOMMEND
    assert result.clause == "B1"


def test_fr6_b1_cannot_fire_with_two_sessions(datasets):
    history = [
        session("08", PUSHUP, [(0.0, 20, 2.0)] * 3),
        session("15", PUSHUP, [(0.0, 10, 0.0)] * 3),
    ]
    assert run(datasets, history, PUSHUP).clause != "B1"


def test_fr6_b2_missed_volume_holds(datasets):
    presc = prescription(rep_low=8, rep_high=15)
    history = [session("15", PUSHUP, [(0.0, 12, 2.0), (0.0, 6, 0.0)])]
    result = run(datasets, history, PUSHUP, presc=presc)
    assert result.action is ProgressionAction.HOLD
    assert result.clause == "B2"


def test_fr6_b3_progresses_to_the_harder_variation(datasets):
    presc = prescription(rep_low=8, rep_high=15)
    variant = datasets.exercise("decline-push-up")
    history = [session("15", PUSHUP, [(0.0, 15, 3.0)] * 3)]
    result = run(datasets, history, PUSHUP, presc=presc, variant=variant)
    assert result.action is ProgressionAction.PROGRESS_VARIATION
    assert result.clause == "B3"
    assert result.exercise_id == "decline-push-up"
    assert result.target_reps == 8
    assert result.suggested_load_kg is None


def test_fr6_b3_needs_at_least_three_sets(datasets):
    presc = prescription(rep_low=8, rep_high=15)
    variant = datasets.exercise("decline-push-up")
    history = [session("15", PUSHUP, [(0.0, 15, 3.0)] * 2)]
    assert run(datasets, history, PUSHUP, presc=presc, variant=variant).clause != "B3"


def test_fr6_b4_adds_a_set_when_there_is_no_harder_variation(datasets):
    presc = prescription(rep_low=8, rep_high=15, sets=3)
    history = [session("15", DIP, [(0.0, 15, 3.0)] * 3)]
    result = run(datasets, history, DIP, presc=presc)
    assert result.action is ProgressionAction.ADD_SET
    assert result.clause == "B4"
    assert result.sets == 4


def test_fr6_b5_holds_at_five_sets(datasets):
    presc = prescription(rep_low=8, rep_high=15, sets=5)
    history = [session("15", DIP, [(0.0, 15, 3.0)] * 5)]
    result = run(datasets, history, DIP, presc=presc)
    assert result.action is ProgressionAction.HOLD
    assert result.clause == "B5"


def test_fr6_b6_adds_a_rep_by_default(datasets):
    presc = prescription(rep_low=8, rep_high=15)
    history = [session("15", PUSHUP, [(0.0, 11, 2.0), (0.0, 10, 2.0)])]
    result = run(datasets, history, PUSHUP, presc=presc)
    assert result.action is ProgressionAction.ADD_REPS
    assert result.clause == "B6"
    assert result.target_reps == 12


def test_fr6_b6_caps_added_reps_at_the_top_of_the_range(datasets):
    presc = prescription(rep_low=8, rep_high=12)
    history = [session("15", PUSHUP, [(0.0, 12, 1.0), (0.0, 11, 1.0)])]
    result = run(datasets, history, PUSHUP, presc=presc)
    assert result.clause == "B6"
    assert result.target_reps == 12


def test_fr6_bodyweight_conflict_b1_beats_b2(datasets):
    presc = prescription(rep_low=8, rep_high=15)
    history = [
        session("01", PUSHUP, [(0.0, 20, 2.0)] * 3),
        session("08", PUSHUP, [(0.0, 20, 2.0)] * 3),
        session("15", PUSHUP, [(0.0, 6, 0.0)] * 3),
    ]
    assert run(datasets, history, PUSHUP, presc=presc).clause == "B1"


def test_fr6_bodyweight_conflict_b2_beats_b3(datasets):
    presc = prescription(rep_low=8, rep_high=15)
    variant = datasets.exercise("decline-push-up")
    history = [session("15", PUSHUP, [(0.0, 15, 3.0), (0.0, 15, 3.0), (0.0, 6, 3.0)])]
    result = run(datasets, history, PUSHUP, presc=presc, variant=variant)
    assert result.clause == "B2"
    assert result.exercise_id == PUSHUP


# --------------------------------------------------------------- deload weeks


def test_fr6_week5_applies_the_15_percent_deload_basis(datasets):
    history = [session("15", BENCH, [(100.0, 8, 4.0), (100.0, 8, 4.0)])]
    presc = prescription(target_rir=4.0)
    week4 = run(datasets, history, presc=presc, week=4)
    week5 = run(datasets, history, presc=presc, week=5)
    assert week4.suggested_load_kg == round_to_increment(100.0, 2.5)
    assert week5.suggested_load_kg == round_to_increment(100.0 * 0.85, 2.5)


def test_fr6_deload_week_rir_target_changes_which_clause_fires(datasets):
    history = [session("15", BENCH, [(100.0, 8, 2.5), (100.0, 8, 2.5)])]
    week4 = run(datasets, history, presc=prescription(target_rir=1.0), week=4)
    week5 = run(datasets, history, presc=prescription(target_rir=4.0), week=5)
    assert week4.clause == "L5"  # RIR 2.5 against a target of 1 is an overshoot
    assert week5.clause == "L4"  # the same RIR against a target of 4 undershoots


def test_fr6_autoregulation_bands_are_strict_inequalities(datasets):
    """Exactly one RIR away from target is on target, not an over/undershoot."""
    on_high = [session("15", BENCH, [(100.0, 8, 3.0), (100.0, 8, 3.0)])]
    on_low = [session("15", BENCH, [(100.0, 8, 1.0), (100.0, 8, 1.0)])]
    assert run(datasets, on_high, presc=prescription(target_rir=2.0)).clause == "L6"
    assert run(datasets, on_low, presc=prescription(target_rir=2.0)).clause == "L6"


def test_fr6_week5_bodyweight_actions_still_carry_no_load(datasets):
    presc = prescription(rep_low=8, rep_high=15, target_rir=4.0)
    history = [session("15", PUSHUP, [(0.0, 11, 4.0)] * 3)]
    result = run(datasets, history, PUSHUP, presc=presc, week=5)
    assert result.suggested_load_kg is None


# ------------------------------------------------------------------ determinism


def test_fr14_progression_is_reproducible(datasets):
    history = [
        session("01", BENCH, [(100.0, 8, 2.0)]),
        session("08", BENCH, [(102.5, 8, 2.0)]),
        session("15", BENCH, [(105.0, 10, 2.0)] * 3),
    ]
    assert run(datasets, history) == run(datasets, history)


def test_fr6_emits_exactly_one_action_with_a_rationale(datasets):
    history = [session("15", BENCH, [(100.0, 10, 2.0)] * 3)]
    result = run(datasets, history)
    assert isinstance(result.action, ProgressionAction)
    assert result.rationale
    assert result.rep_low <= result.target_reps <= result.rep_high
