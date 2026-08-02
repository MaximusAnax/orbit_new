"""FR-13 — safety as implemented behaviour.

(a) program generation is gated on an explicit acknowledgement;
(b) pain flags substitute or drop an exercise at read time and are only ever
    cleared by the user;
(c) nothing the engine can say uses diagnosis vocabulary.
"""

from __future__ import annotations

import pytest
from formcoach.engine.programming import (
    DELOAD_LOAD_NOTE,
    generate_program,
)
from formcoach.engine.progression import next_prescription
from formcoach.engine.safety import (
    DIAGNOSIS_TERMS,
    DISCLAIMER_NOT_ACKNOWLEDGED,
    PAIN_FLAG_NO_SUBSTITUTE,
    STOP_AND_REFER,
    SafetyGateError,
    clear_pain_flag,
    contains_diagnosis_language,
    find_substitute,
    flagged_exercise_ids,
    pain_flags_after,
    require_disclaimer_ack,
    resolve_for_pain,
)
from formcoach.models import (
    Equipment,
    Experience,
    Goal,
    LoggedSession,
    Muscle,
    Prescription,
    SetLog,
    UserProfile,
    WorkoutLog,
)

ACK = "2026-07-01T08:00:00+00:00"


def make_profile(**overrides) -> UserProfile:
    base = {
        "goal": Goal.HYPERTROPHY,
        "experience": Experience.INTERMEDIATE,
        "days_per_week": 4,
        "equipment": list(Equipment),
        "disclaimer_acknowledged_at": ACK,
        "updated_at": ACK,
    }
    return UserProfile(**{**base, **overrides})


# ------------------------------------------------------------------- FR-13(a)


def test_fr13a_program_generation_is_blocked_until_the_notice_is_acknowledged():
    profile = make_profile(disclaimer_acknowledged_at=None)
    with pytest.raises(SafetyGateError) as excinfo:
        require_disclaimer_ack(profile)
    assert excinfo.value.code == DISCLAIMER_NOT_ACKNOWLEDGED


def test_fr13a_acknowledged_profile_passes_the_gate():
    require_disclaimer_ack(make_profile())


def test_fr13a_empty_acknowledgement_string_does_not_count():
    with pytest.raises(SafetyGateError):
        require_disclaimer_ack(make_profile(disclaimer_acknowledged_at=""))


# ------------------------------------------------------------------- FR-13(b)


def _sets(exercise_id: str, pain: bool) -> list[SetLog]:
    return [
        SetLog(
            exercise_id=exercise_id, set_index=0, weight_kg=100.0, reps=5, rir=2.0, pain_flag=pain
        )
    ]


def test_fr13b_a_painful_set_flags_its_exercise():
    profile = make_profile()
    flags = pain_flags_after(profile, _sets("barbell-back-squat", True))
    assert flags == ["barbell-back-squat"]
    assert flagged_exercise_ids(_sets("barbell-back-squat", True)) == ["barbell-back-squat"]


def test_fr13b_a_clean_set_flags_nothing():
    profile = make_profile()
    assert pain_flags_after(profile, _sets("barbell-back-squat", False)) == []


def test_fr13b_flagging_is_idempotent_and_additive():
    profile = make_profile(pain_flags=["barbell-back-squat"])
    again = pain_flags_after(profile, _sets("barbell-back-squat", True))
    assert again == ["barbell-back-squat"]
    both = pain_flags_after(profile, _sets("barbell-bench-press", True))
    assert both == ["barbell-back-squat", "barbell-bench-press"]


def test_fr13b_pain_is_never_auto_cleared_only_explicitly():
    profile = make_profile(pain_flags=["barbell-back-squat", "dip"])
    assert pain_flags_after(profile, _sets("barbell-back-squat", False)) == [
        "barbell-back-squat",
        "dip",
    ]
    assert clear_pain_flag(profile, "dip") == ["barbell-back-squat"]
    assert clear_pain_flag(profile, "not-flagged") == ["barbell-back-squat", "dip"]


def test_fr13b_substitute_keeps_the_pattern_and_is_no_harder(datasets):
    profile = make_profile(pain_flags=["barbell-back-squat"])
    original = datasets.exercise("barbell-back-squat")
    replacement = find_substitute(original, profile, datasets, [Muscle.QUADS], seed=7)
    assert replacement is not None
    assert replacement.id != original.id
    assert replacement.pattern is original.pattern
    assert replacement.difficulty <= original.difficulty
    assert replacement.id not in profile.pain_flags


def test_fr13b_substitution_is_deterministic_for_a_given_seed(datasets):
    profile = make_profile(pain_flags=["barbell-back-squat"])
    original = datasets.exercise("barbell-back-squat")
    first = find_substitute(original, profile, datasets, [Muscle.QUADS], seed=11)
    second = find_substitute(original, profile, datasets, [Muscle.QUADS], seed=11)
    assert first == second


def test_fr13b_substitute_respects_the_profiles_equipment(datasets):
    profile = make_profile(equipment=[Equipment.BODYWEIGHT], pain_flags=["barbell-back-squat"])
    original = datasets.exercise("barbell-back-squat")
    replacement = find_substitute(original, profile, datasets, [Muscle.QUADS], seed=3)
    assert replacement is not None
    assert Equipment.BODYWEIGHT in replacement.equipment


def test_fr13b_resolve_substitutes_a_flagged_prescription(datasets):
    profile = make_profile(pain_flags=["barbell-back-squat"])
    prescription = Prescription(
        position=0,
        exercise_id="barbell-back-squat",
        sets=3,
        rep_low=5,
        rep_high=10,
        target_rir=2.0,
        rest_s=210,
    )
    resolution = resolve_for_pain(prescription, profile, datasets, [Muscle.QUADS], seed=7)
    assert resolution.exercise is not None
    assert resolution.substituted_for == "barbell-back-squat"
    assert resolution.dropped_reason is None


def test_fr13b_unflagged_prescription_is_left_alone(datasets):
    profile = make_profile()
    prescription = Prescription(
        position=0,
        exercise_id="barbell-back-squat",
        sets=3,
        rep_low=5,
        rep_high=10,
        target_rir=2.0,
        rest_s=210,
    )
    resolution = resolve_for_pain(prescription, profile, datasets, [Muscle.QUADS], seed=7)
    assert resolution.exercise is not None
    assert resolution.exercise.id == "barbell-back-squat"
    assert resolution.substituted_for is None


def test_fr13b_prescription_is_dropped_when_no_alternative_exists(datasets):
    squat_ids = [e.id for e in datasets.exercises if e.pattern.value == "squat"]
    profile = make_profile(pain_flags=squat_ids)
    prescription = Prescription(
        position=0,
        exercise_id="barbell-back-squat",
        sets=3,
        rep_low=5,
        rep_high=10,
        target_rir=2.0,
        rest_s=210,
    )
    resolution = resolve_for_pain(prescription, profile, datasets, [Muscle.QUADS], seed=7)
    assert resolution.exercise is None
    assert resolution.dropped_reason == PAIN_FLAG_NO_SUBSTITUTE


def test_fr13b_program_rows_are_immutable_substitution_happens_at_read_time(datasets):
    """A flag raised after generation must not rewrite the stored program."""
    profile = make_profile()
    plan = generate_program(profile, datasets, as_of="2026-07-06", seed=5)
    before = [p.exercise_id for s in plan.sessions for p in s.prescriptions]
    flagged = profile.model_copy(update={"pain_flags": [before[0]]})
    assert [p.exercise_id for s in plan.sessions for p in s.prescriptions] == before
    resolution = resolve_for_pain(
        plan.sessions[0].prescriptions[0],
        flagged,
        datasets,
        plan.program.target_muscles,
        seed=plan.program.seed,
    )
    assert resolution.substituted_for == before[0]


def test_fr13b_flagged_exercises_are_excluded_from_new_programs(datasets):
    profile = make_profile(pain_flags=["barbell-back-squat"])
    plan = generate_program(profile, datasets, as_of="2026-07-06", seed=5)
    used = {p.exercise_id for s in plan.sessions for p in s.prescriptions}
    assert "barbell-back-squat" not in used


# ------------------------------------------------------------------- FR-13(c)


def test_fr13c_detector_finds_every_listed_term():
    for term in DIAGNOSIS_TERMS:
        assert contains_diagnosis_language(f"you may have a {term} here") == [term]
    assert contains_diagnosis_language("Push your knees out over your toes.") == []


def test_fr13c_detector_is_case_insensitive_and_matches_stems():
    assert "diagnos" in contains_diagnosis_language("We will DIAGNOSE it")
    assert "tear" in contains_diagnosis_language("possible tearing")


def test_fr13c_stop_and_refer_message_is_referral_not_diagnosis():
    assert contains_diagnosis_language(STOP_AND_REFER) == []
    assert "health professional" in STOP_AND_REFER


def test_fr13c_no_form_profile_cue_or_citation_uses_diagnosis_language(datasets):
    offenders: list[str] = []
    for profile in datasets.form_profiles.values():
        for rule in profile.rules:
            for text in (rule.cue, rule.citation or ""):
                if contains_diagnosis_language(text):
                    offenders.append(f"{profile.id}/{rule.fault_id}: {text}")
    assert offenders == []


def test_fr13c_no_exercise_cue_or_instruction_uses_diagnosis_language(datasets):
    offenders: list[str] = []
    for exercise in datasets.exercises:
        for text in [exercise.name, *exercise.cues, *exercise.instructions]:
            if contains_diagnosis_language(text):
                offenders.append(f"{exercise.id}: {text}")
    assert offenders == []


def test_fr13c_progression_rationales_never_use_diagnosis_language(datasets):
    """Every clause of the ladder is rendered and audited."""
    bench = datasets.exercise("barbell-bench-press")
    pushup = datasets.exercise("push-up")
    variant = datasets.exercise("decline-push-up")

    def logs(exercise_id, rows):
        return [
            LoggedSession(
                workout=WorkoutLog(performed_at=f"2026-07-{day}T18:00:00+00:00"),
                sets=[
                    SetLog(exercise_id=exercise_id, set_index=i, weight_kg=w, reps=r, rir=rir)
                    for i, (w, r, rir) in enumerate(sets)
                ],
            )
            for day, sets in rows
        ]

    loaded = Prescription(
        position=0, exercise_id=bench.id, sets=3, rep_low=5, rep_high=10, target_rir=2.0, rest_s=180
    )
    body = Prescription(
        position=0, exercise_id=pushup.id, sets=3, rep_low=8, rep_high=15, target_rir=2.0, rest_s=90
    )

    scenarios = [
        (
            bench,
            loaded,
            logs(
                bench.id,
                [("01", [(100.0, 8, 2.0)]), ("08", [(100.0, 8, 2.0)]), ("15", [(85.0, 8, 2.0)])],
            ),
            None,
        ),
        (bench, loaded, logs(bench.id, [("15", [(100.0, 8, 2.0), (100.0, 3, 0.0)])]), None),
        (bench, loaded, logs(bench.id, [("15", [(100.0, 10, 2.0)] * 3)]), None),
        (bench, loaded, logs(bench.id, [("15", [(100.0, 8, 0.0)] * 2)]), None),
        (bench, loaded, logs(bench.id, [("15", [(100.0, 8, 4.0)] * 2)]), None),
        (bench, loaded, logs(bench.id, [("15", [(100.0, 8, 2.0), (100.0, 7, 2.0)])]), None),
        (bench, loaded, [], None),
        (
            pushup,
            body,
            logs(
                pushup.id,
                [
                    ("01", [(0.0, 20, 2.0)] * 3),
                    ("08", [(0.0, 20, 2.0)] * 3),
                    ("15", [(0.0, 10, 0.0)] * 3),
                ],
            ),
            None,
        ),
        (pushup, body, logs(pushup.id, [("15", [(0.0, 15, 3.0)] * 3)]), variant),
        (pushup, body, logs(pushup.id, [("15", [(0.0, 15, 3.0)] * 3)]), None),
        (pushup, body, logs(pushup.id, [("15", [(0.0, 15, 3.0)] * 5)]), None),
        (pushup, body, logs(pushup.id, [("15", [(0.0, 11, 2.0)] * 3)]), None),
        (pushup, body, logs(pushup.id, [("15", [(0.0, 12, 2.0), (0.0, 5, 2.0)])]), None),
    ]
    clauses = set()
    for exercise, prescription, history, harder in scenarios:
        result = next_prescription(
            history,
            prescription,
            exercise,
            week=2,
            increment_kg=datasets.increment_kg(exercise),
            harder_variant=harder,
        )
        clauses.add(result.clause)
        assert contains_diagnosis_language(result.rationale) == [], result.rationale
    assert clauses >= {"L1", "L2", "L3", "L4", "L5", "L6", "B1", "B2", "B3", "B4", "B5", "B6"}


def test_fr13c_program_load_notes_never_use_diagnosis_language():
    assert contains_diagnosis_language(DELOAD_LOAD_NOTE) == []


def test_fr13c_safety_error_messages_never_use_diagnosis_language():
    profile = make_profile(disclaimer_acknowledged_at=None)
    with pytest.raises(SafetyGateError) as excinfo:
        require_disclaimer_ack(profile)
    assert contains_diagnosis_language(str(excinfo.value)) == []
