"""FR-3 — mesocycle generation, checked against SCOPE's own constraint list.

The heart of this module is ``test_fr3_program_constraints_hold_on_the_grid``:
it generates a program for every profile in the eval's 120-cell grid and
evaluates the fifteen constraints of SCOPE.md § "Program constraints" — the
same list M5 gates at 1.0.  The other tests pin the individual algorithm steps
so a failure points at a step rather than at "somewhere in generation".
"""

from __future__ import annotations

import itertools

import pytest
from formcoach.engine.programming import (
    GOAL_REP_RANGES,
    MAX_DIRECT_SETS_PER_MUSCLE_PER_SESSION,
    REST_BANDS,
    SETS_PER_SESSION_BUDGET,
    WEEK_TARGET_RIR,
    ProgramGenerationError,
    candidate_pool,
    choose_split,
    generate_program,
    rank_key,
    select_target_muscles,
    weekly_set_plan,
)
from formcoach.engine.volume import accumulate_direct, accumulate_effective
from formcoach.models import (
    Equipment,
    Experience,
    Goal,
    Mechanics,
    Muscle,
    ProgramStatus,
    SplitName,
    UserProfile,
    round_half_up,
)

AS_OF = "2026-07-06"
ACK = "2026-07-01T08:00:00+00:00"
FULL_GYM = list(Equipment)
HOME_GYM = [Equipment.DUMBBELL, Equipment.BODYWEIGHT]


def make_profile(**overrides) -> UserProfile:
    base = {
        "goal": Goal.HYPERTROPHY,
        "experience": Experience.INTERMEDIATE,
        "days_per_week": 4,
        "equipment": FULL_GYM,
        "emphasized_muscles": [],
        "disclaimer_acknowledged_at": ACK,
        "updated_at": ACK,
    }
    return UserProfile(**{**base, **overrides})


def grid_profiles() -> list[UserProfile]:
    """The eval's 120-cell fixture grid: 3 goals x 5 day counts x 2 x 2 x 2."""
    return [
        make_profile(
            goal=goal,
            days_per_week=days,
            experience=experience,
            equipment=equipment,
            emphasized_muscles=emphasis,
        )
        for goal, days, experience, equipment, emphasis in itertools.product(
            list(Goal),
            range(2, 7),
            [Experience.BEGINNER, Experience.ADVANCED],
            [FULL_GYM, HOME_GYM],
            [[], [Muscle.QUADS, Muscle.CHEST]],
        )
    ]


# ------------------------------------------------------------------- step 1


@pytest.mark.parametrize(
    ("days", "split"),
    [
        (2, SplitName.FULL_BODY),
        (3, SplitName.FULL_BODY),
        (4, SplitName.UPPER_LOWER),
        (5, SplitName.UPPER_LOWER_PPL),
        (6, SplitName.PPL_X2),
    ],
)
def test_fr3_split_is_chosen_by_days_per_week(days, split):
    assert choose_split(days) is split


def test_fr3_unsupported_day_count_is_refused():
    with pytest.raises(ProgramGenerationError):
        choose_split(7)


# ------------------------------------------------------------------- step 2


def test_fr3_target_count_matches_count_by_days(datasets):
    for days, expected in datasets.target_policy.count_by_days.items():
        targets = select_target_muscles(make_profile(days_per_week=days), datasets)
        assert len(targets) == expected


def test_fr3_targets_follow_the_goal_priority_order(datasets):
    targets = select_target_muscles(make_profile(days_per_week=3), datasets)
    assert targets == [
        Muscle.CHEST,
        Muscle.QUADS,
        Muscle.LATS,
        Muscle.UPPER_BACK,
        Muscle.HAMSTRINGS,
        Muscle.SIDE_DELTS,
        Muscle.TRICEPS,
    ]


def test_fr3_strength_uses_its_own_priority_order(datasets):
    targets = select_target_muscles(make_profile(goal=Goal.STRENGTH, days_per_week=2), datasets)
    assert targets == [
        Muscle.QUADS,
        Muscle.CHEST,
        Muscle.UPPER_BACK,
        Muscle.LATS,
        Muscle.HAMSTRINGS,
    ]


def test_fr3_emphasized_muscles_come_first_and_the_tail_is_truncated(datasets):
    profile = make_profile(days_per_week=2, emphasized_muscles=[Muscle.CALVES, Muscle.ABS])
    targets = select_target_muscles(profile, datasets)
    assert targets[:2] == [Muscle.CALVES, Muscle.ABS]
    assert len(targets) == 5


def test_fr3_emphasis_outside_the_priority_list_is_still_a_target(datasets):
    profile = make_profile(days_per_week=2, emphasized_muscles=[Muscle.FOREARMS])
    targets = select_target_muscles(profile, datasets)
    assert Muscle.FOREARMS in targets
    assert len(targets) == 5


# ------------------------------------------------------------------- step 3


@pytest.mark.parametrize(
    ("experience", "factor"),
    [(Experience.BEGINNER, 0.0), (Experience.INTERMEDIATE, 0.25), (Experience.ADVANCED, 0.5)],
)
def test_fr3_week1_start_is_scaled_by_experience(datasets, experience, factor):
    """Six days leaves week-1 headroom under the budget, so the ramp is unclamped."""
    profile = make_profile(days_per_week=6, experience=experience)
    targets = select_target_muscles(profile, datasets)
    plan = weekly_set_plan(profile, targets, datasets)
    assert sum(plan[m][0] for m in targets) <= SETS_PER_SESSION_BUDGET * 6
    for muscle in targets:
        landmark = datasets.landmarks[muscle]
        expected = landmark.mev + round_half_up(factor * (landmark.mav - landmark.mev))
        assert plan[muscle][0] == expected


def test_fr3_beginner_starts_at_mev_and_advanced_starts_higher(datasets):
    profile = make_profile(days_per_week=6)
    targets = select_target_muscles(profile, datasets)
    beginner = weekly_set_plan(
        make_profile(days_per_week=6, experience=Experience.BEGINNER), targets, datasets
    )
    advanced = weekly_set_plan(
        make_profile(days_per_week=6, experience=Experience.ADVANCED), targets, datasets
    )
    assert beginner[Muscle.CHEST][0] == datasets.landmarks[Muscle.CHEST].mev
    assert advanced[Muscle.CHEST][0] > beginner[Muscle.CHEST][0]


def _flat_landmarks(datasets, mev: int, mav: int, mrv: int):
    """A dataset copy whose landmarks leave the weekly budget slack."""
    landmarks = {
        muscle: landmark.model_copy(update={"mv": 0, "mev": mev, "mav": mav, "mrv": mrv})
        for muscle, landmark in datasets.landmarks.items()
    }
    return datasets.model_copy(update={"landmarks": landmarks})


def test_fr3_week4_end_is_mav_plus_two_for_emphasized_muscles(datasets):
    """Checked with slack landmarks so the budget clamp cannot mask the formula."""
    slack = _flat_landmarks(datasets, mev=2, mav=4, mrv=10)
    profile = make_profile(
        days_per_week=6, experience=Experience.ADVANCED, emphasized_muscles=[Muscle.CHEST]
    )
    targets = select_target_muscles(profile, slack)
    plan = weekly_set_plan(profile, targets, slack)
    assert sum(plan[m][3] for m in targets) < SETS_PER_SESSION_BUDGET * 6
    assert plan[Muscle.CHEST][3] == 4 + 2
    assert plan[Muscle.QUADS][3] == 4
    assert plan[Muscle.CHEST][0] == 2 + round_half_up(0.5 * (4 - 2))


def test_fr3_week4_end_is_capped_at_mrv(datasets):
    slack = _flat_landmarks(datasets, mev=2, mav=5, mrv=5)
    profile = make_profile(days_per_week=6, emphasized_muscles=[Muscle.CHEST])
    targets = select_target_muscles(profile, slack)
    plan = weekly_set_plan(profile, targets, slack)
    assert plan[Muscle.CHEST][3] == 5  # mav + 2 would be 7, MRV wins


def test_fr3_plan_is_monotone_and_bounded_by_the_ceiling(datasets):
    profile = make_profile(days_per_week=5, emphasized_muscles=[Muscle.CHEST])
    targets = select_target_muscles(profile, datasets)
    plan = weekly_set_plan(profile, targets, datasets)
    for muscle in targets:
        landmark = datasets.landmarks[muscle]
        bonus = 2 if muscle in profile.emphasized_muscles else 0
        weeks = plan[muscle][:4]
        assert weeks == sorted(weeks)
        assert weeks[0] >= landmark.mev
        assert weeks[3] <= min(landmark.mav + bonus, landmark.mrv)


def test_fr3_committed_landmarks_make_the_budget_bind_by_week_four(datasets):
    """A documented property of the committed numbers, not an accident."""
    for days in range(2, 7):
        profile = make_profile(days_per_week=days, experience=Experience.ADVANCED)
        targets = select_target_muscles(profile, datasets)
        plan = weekly_set_plan(profile, targets, datasets)
        assert sum(plan[m][3] for m in targets) == SETS_PER_SESSION_BUDGET * days


def test_fr3_budget_clamp_never_drops_a_muscle_below_mev(datasets):
    profile = make_profile(days_per_week=2, experience=Experience.ADVANCED)
    targets = select_target_muscles(profile, datasets)
    plan = weekly_set_plan(profile, targets, datasets)
    budget = SETS_PER_SESSION_BUDGET * 2
    for week in range(4):
        assert sum(plan[m][week] for m in targets) <= budget
        for muscle in targets:
            assert plan[muscle][week] >= datasets.landmarks[muscle].mev


def test_fr3_clamp_keeps_the_series_non_decreasing(datasets):
    for days in range(2, 7):
        profile = make_profile(
            days_per_week=days,
            experience=Experience.ADVANCED,
            emphasized_muscles=[Muscle.QUADS, Muscle.CHEST],
        )
        targets = select_target_muscles(profile, datasets)
        plan = weekly_set_plan(profile, targets, datasets)
        for muscle in targets:
            assert plan[muscle][:4] == sorted(plan[muscle][:4])


def test_fr3_deload_week_halves_the_plan(datasets):
    profile = make_profile(days_per_week=4)
    targets = select_target_muscles(profile, datasets)
    plan = weekly_set_plan(profile, targets, datasets)
    for muscle in targets:
        assert plan[muscle][4] == max(1, plan[muscle][3] // 2)


# ------------------------------------------------------------------- step 4


def test_fr3_candidate_pool_respects_equipment_and_pain_flags(datasets):
    profile = make_profile(equipment=[Equipment.BODYWEIGHT], pain_flags=["push-up"])
    pool = candidate_pool(profile, datasets, seed=1)
    assert pool
    assert all(Equipment.BODYWEIGHT in e.equipment for e in pool)
    assert "push-up" not in {e.id for e in pool}


def test_fr3_beginner_pool_drops_difficulty_three_where_an_easier_option_exists(datasets):
    profile = make_profile(experience=Experience.BEGINNER)
    pool = candidate_pool(profile, datasets, seed=1)
    easy_patterns = {e.pattern for e in pool if e.difficulty <= 2}
    for exercise in pool:
        if exercise.difficulty == 3:
            assert exercise.pattern not in easy_patterns


def test_fr3_ranking_key_prefers_uncovered_targets_then_compounds(datasets):
    squat = datasets.exercise("barbell-back-squat")
    curl = datasets.exercise("dumbbell-biceps-curl")
    uncovered = {Muscle.QUADS, Muscle.GLUTES, Muscle.BICEPS}
    assert rank_key(squat, uncovered) < rank_key(curl, uncovered)
    assert rank_key(squat, {Muscle.BICEPS}) > rank_key(curl, {Muscle.BICEPS})


def test_fr3_seed_only_reorders_exact_ties(datasets):
    profile = make_profile()
    a = generate_program(profile, datasets, as_of=AS_OF, seed=1)
    b = generate_program(profile, datasets, as_of=AS_OF, seed=2)
    assert a.program.target_muscles == b.program.target_muscles
    assert a.program.weekly_set_targets == b.program.weekly_set_targets
    assert [s.name for s in a.sessions] == [s.name for s in b.sessions]


def test_fr3_generation_fails_loudly_when_nothing_matches(datasets):
    profile = make_profile(equipment=[Equipment.BAND], days_per_week=2)
    forbidden = [e.id for e in datasets.exercises]
    profile = profile.model_copy(update={"pain_flags": forbidden})
    with pytest.raises(ProgramGenerationError):
        generate_program(profile, datasets, as_of=AS_OF, seed=1)


# --------------------------------------------------------------- whole program


def test_fr3_program_shape_is_five_weeks_of_the_right_number_of_days(datasets):
    for days in range(2, 7):
        plan = generate_program(make_profile(days_per_week=days), datasets, as_of=AS_OF, seed=5)
        assert plan.program.weeks == 5
        assert len(plan.sessions) == 5 * days
        assert {s.week for s in plan.sessions} == {1, 2, 3, 4, 5}
        assert {s.day_index for s in plan.sessions} == set(range(days))


def test_fr3_program_snapshots_the_profile(datasets):
    profile = make_profile(emphasized_muscles=[Muscle.CHEST], days_per_week=3)
    program = generate_program(profile, datasets, as_of=AS_OF, seed=9).program
    assert program.goal is profile.goal
    assert program.experience is profile.experience
    assert program.days_per_week == profile.days_per_week
    assert program.emphasized_muscles == profile.emphasized_muscles
    assert program.equipment == profile.equipment
    assert program.created_at == AS_OF
    assert program.seed == 9
    assert program.status is ProgramStatus.ACTIVE


def test_fr14_same_profile_and_seed_produce_an_identical_program(datasets):
    profile = make_profile(days_per_week=5, emphasized_muscles=[Muscle.LATS])
    first = generate_program(profile, datasets, as_of=AS_OF, seed=20260731)
    second = generate_program(profile, datasets, as_of=AS_OF, seed=20260731)
    assert first == second


# --------------------------------------------------- the fifteen constraints


def evaluate_constraints(plan, profile, datasets) -> list[str]:
    """SCOPE.md § "Program constraints", 1-15, as a list of violations."""
    problems: list[str] = []
    targets = plan.program.target_muscles
    by_week: dict[int, list] = {}
    for session in plan.sessions:
        by_week.setdefault(session.week, []).append(session)

    def allocations(sessions):
        return [
            (datasets.exercise(p.exercise_id), p.sets) for s in sessions for p in s.prescriptions
        ]

    effective = {w: accumulate_effective(allocations(by_week[w])) for w in range(1, 6)}

    # 1 — target muscles inside [MEV, MRV] in weeks 1-4
    for week in range(1, 5):
        for muscle in targets:
            landmark = datasets.landmarks[muscle]
            value = effective[week][muscle]
            if not landmark.mev <= value <= landmark.mrv:
                problems.append(f"c1 w{week} {muscle.value}={value}")

    # 2 — every target muscle trained on >= 2 distinct days
    for week in range(1, 5):
        for muscle in targets:
            days = sum(
                1
                for s in by_week[week]
                if any(
                    datasets.exercise(p.exercise_id).effective_credit(muscle) > 0
                    for p in s.prescriptions
                )
            )
            if days < 2:
                problems.append(f"c2 w{week} {muscle.value} on {days} day(s)")

    # 3 — emphasized muscles are non-decreasing
    for muscle in profile.emphasized_muscles:
        series = [effective[w][muscle] for w in range(1, 5)]
        if any(series[i] > series[i + 1] for i in range(3)):
            problems.append(f"c3 {muscle.value} {series}")

    # 4 — deload week
    for muscle in Muscle:
        if effective[5][muscle] > 0.5 * effective[4][muscle] + 1e-9:
            problems.append(f"c4 {muscle.value}")
    for session in by_week[5]:
        if any(p.target_rir < 4 for p in session.prescriptions):
            problems.append("c4 deload rir")

    # 5 — RIR ramp
    rirs = [by_week[w][0].prescriptions[0].target_rir for w in range(1, 5)]
    if any(rirs[i] < rirs[i + 1] for i in range(3)) or not all(1 <= r <= 3 for r in rirs):
        problems.append(f"c5 {rirs}")

    for session in plan.sessions:
        seen_isolation = False
        total_sets = sum(p.sets for p in session.prescriptions)
        for prescription in session.prescriptions:
            exercise = datasets.exercise(prescription.exercise_id)
            # 6 — rep ranges
            if (prescription.rep_low, prescription.rep_high) != GOAL_REP_RANGES[profile.goal][
                exercise.mechanics
            ]:
                problems.append(f"c6 {exercise.id}")
            # 7 — equipment
            if not set(exercise.equipment) & set(profile.equipment):
                problems.append(f"c7 {exercise.id}")
            # 9 — compounds before isolation
            is_isolation = exercise.mechanics is Mechanics.ISOLATION
            if seen_isolation and not is_isolation:
                problems.append(f"c9 w{session.week}d{session.day_index}")
            seen_isolation = seen_isolation or is_isolation
            # 11 — rest bands
            low, high = REST_BANDS[exercise.mechanics]
            if not low <= prescription.rest_s <= high:
                problems.append(f"c11 {exercise.id} {prescription.rest_s}")
            # 13 — beginner difficulty
            if profile.experience is Experience.BEGINNER and exercise.difficulty == 3:
                alternatives = [
                    e
                    for e in datasets.exercises
                    if e.pattern is exercise.pattern
                    and e.difficulty <= 2
                    and set(e.equipment) & set(profile.equipment)
                ]
                if alternatives:
                    problems.append(f"c13 {exercise.id}")
        # 8 — session caps
        if total_sets > SETS_PER_SESSION_BUDGET:
            problems.append(f"c8 w{session.week}d{session.day_index} total={total_sets}")
        direct = accumulate_direct(
            [(datasets.exercise(p.exercise_id), p.sets) for p in session.prescriptions]
        )
        for muscle, count in direct.items():
            if count > MAX_DIRECT_SETS_PER_MUSCLE_PER_SESSION:
                problems.append(f"c8 {muscle.value}={count}")

    # 10 — required movement patterns per session
    template = datasets.split_for(plan.program.split).sessions[: profile.days_per_week]
    for session in plan.sessions:
        required = set(template[session.day_index].required_patterns)
        present = {datasets.exercise(p.exercise_id).pattern for p in session.prescriptions}
        if not required <= present:
            problems.append(f"c10 w{session.week}d{session.day_index} {required - present}")

    # 12 — determinism
    again = generate_program(
        profile, datasets, as_of=plan.program.created_at, seed=plan.program.seed
    )
    if again != plan:
        problems.append("c12 not reproducible")

    # 14 — week-1 plan equals the post-clamp experience-scaled start
    expected_plan = weekly_set_plan(profile, targets, datasets)
    for muscle in targets:
        if plan.program.weekly_set_targets[muscle][0] != expected_plan[muscle][0]:
            problems.append(f"c14 {muscle.value}")

    # 15 — coverage: every target reaches MEV by week 4
    for muscle in targets:
        if effective[4][muscle] < datasets.landmarks[muscle].mev:
            problems.append(f"c15 {muscle.value}")
    return problems


@pytest.mark.parametrize(
    "profile",
    grid_profiles(),
    ids=lambda p: (
        f"{p.goal.value}-{p.days_per_week}d-{p.experience.value}-"
        f"{'full' if len(p.equipment) > 2 else 'home'}-{len(p.emphasized_muscles)}emph"
    ),
)
def test_fr3_program_constraints_hold_on_the_grid(datasets, profile):
    plan = generate_program(profile, datasets, as_of=AS_OF, seed=20260731)
    assert evaluate_constraints(plan, profile, datasets) == []


def test_fr3_target_rir_table_matches_scope(datasets):
    assert WEEK_TARGET_RIR == {1: 3.0, 2: 2.0, 3: 2.0, 4: 1.0, 5: 4.0}
    plan = generate_program(make_profile(), datasets, as_of=AS_OF, seed=3)
    for session in plan.sessions:
        for prescription in session.prescriptions:
            assert prescription.target_rir == WEEK_TARGET_RIR[session.week]


def test_fr3_deload_prescriptions_carry_the_15_percent_load_note(datasets):
    plan = generate_program(make_profile(), datasets, as_of=AS_OF, seed=3)
    for session in plan.sessions:
        for prescription in session.prescriptions:
            if session.week == 5:
                assert prescription.load_note and "15%" in prescription.load_note
            else:
                assert prescription.load_note is None


def test_fr3_goal_rep_ranges_match_the_scope_table():
    assert GOAL_REP_RANGES[Goal.STRENGTH][Mechanics.COMPOUND] == (3, 6)
    assert GOAL_REP_RANGES[Goal.STRENGTH][Mechanics.ISOLATION] == (8, 12)
    assert GOAL_REP_RANGES[Goal.HYPERTROPHY][Mechanics.COMPOUND] == (5, 10)
    assert GOAL_REP_RANGES[Goal.HYPERTROPHY][Mechanics.ISOLATION] == (8, 15)
    assert GOAL_REP_RANGES[Goal.GENERAL][Mechanics.COMPOUND] == (6, 12)
    assert GOAL_REP_RANGES[Goal.GENERAL][Mechanics.ISOLATION] == (8, 15)
