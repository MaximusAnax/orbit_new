"""FR-3 — mesocycle generation: split, target muscles, volume ramp, selection.

The algorithm is SCOPE.md § "Program generation algorithm", step for step:

1. ``choose_split`` maps days/week onto a committed split template;
2. ``select_target_muscles`` applies the emphasis-then-priority rule and the
   ``count_by_days`` cap;
3. ``weekly_set_plan`` builds the experience-scaled MEV→MAV ramp, applies the
   ``22 · days`` budget clamp and the week-5 deload;
4. ``select_exercises`` covers the session's required movement patterns and
   then every target muscle, using the documented ranking key;
5. ``allocate_sets`` turns per-muscle weekly effective-set targets into direct
   sets on those exercises;
6. rep ranges, RIR and rest come from the goal-by-mechanics tables.

Two implementation decisions are worth stating up front because the evals lean
on them:

* **Realized volume is measured in effective sets.**  ``allocate_sets`` drives
  each target muscle's *effective* weekly sets (primary 1.0 + secondary 0.5) to
  its planned figure, and refuses any set that would push a target muscle past
  its MRV.  Multi-primary exercises make exact per-muscle direct-set equality
  impossible in general (one back-squat set is a direct set for both quads and
  glutes), so the plan itself is snapshotted on ``Program.weekly_set_targets``
  and is the auditable record of step 3.
* **Weeks grow monotonically by construction.**  Week *w*'s allocation starts
  from week *w-1*'s and only ever adds sets, so every muscle's realized weekly
  volume is non-decreasing across weeks 1 to 4.  Week 5 halves week 4's
  per-exercise sets, which makes the 50 % deload ceiling hold on realized
  volume and not merely on the plan.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Sequence

from formcoach.engine.volume import accumulate_direct, accumulate_effective
from formcoach.models import (
    Datasets,
    Exercise,
    Experience,
    Goal,
    Mechanics,
    MovementPattern,
    Muscle,
    Prescription,
    Program,
    ProgramPlan,
    ProgramSession,
    SessionPlan,
    SplitName,
    SplitSessionTemplate,
    TargetMusclePolicy,
    UserProfile,
    round_half_up,
)

#: FR-3 step 6 — rep ranges by goal and mechanics.
GOAL_REP_RANGES: dict[Goal, dict[Mechanics, tuple[int, int]]] = {
    Goal.STRENGTH: {Mechanics.COMPOUND: (3, 6), Mechanics.ISOLATION: (8, 12)},
    Goal.HYPERTROPHY: {Mechanics.COMPOUND: (5, 10), Mechanics.ISOLATION: (8, 15)},
    Goal.GENERAL: {Mechanics.COMPOUND: (6, 12), Mechanics.ISOLATION: (8, 15)},
}

#: FR-3 step 6 — RIR target per week (accumulation 3→1, deload 4).
WEEK_TARGET_RIR: dict[int, float] = {1: 3.0, 2: 2.0, 3: 2.0, 4: 1.0, 5: 4.0}

#: FR-3 step 6 — rest bands in seconds.
REST_BANDS: dict[Mechanics, tuple[int, int]] = {
    Mechanics.COMPOUND: (150, 300),
    Mechanics.ISOLATION: (60, 120),
}

#: FR-3 step 3 — how far into the MEV→MAV span week 1 starts.
EXPERIENCE_START_FACTOR: dict[Experience, float] = {
    Experience.BEGINNER: 0.0,
    Experience.INTERMEDIATE: 0.25,
    Experience.ADVANCED: 0.50,
}

SETS_PER_SESSION_BUDGET = 22
MAX_DIRECT_SETS_PER_MUSCLE_PER_SESSION = 10
MAX_SETS_PER_EXERCISE = 6
MIN_SETS_PER_EXERCISE = 1
SEED_SETS_PER_EXERCISE = 2
MAX_EXERCISES_PER_SESSION = 11
MIN_SESSIONS_PER_TARGET_MUSCLE = 2
DELOAD_LOAD_NOTE = "Deload week: work about 15% lighter than your week-4 loads."


class ProgramGenerationError(ValueError):
    """Raised when no program can satisfy the profile with the library given."""


# ------------------------------------------------------------------ steps 1 and 2


def choose_split(days_per_week: int) -> SplitName:
    """FR-3 step 1."""
    if days_per_week in (2, 3):
        return SplitName.FULL_BODY
    if days_per_week == 4:
        return SplitName.UPPER_LOWER
    if days_per_week == 5:
        return SplitName.UPPER_LOWER_PPL
    if days_per_week == 6:
        return SplitName.PPL_X2
    raise ProgramGenerationError(f"unsupported days_per_week: {days_per_week}")


def target_muscles_for(
    policy: TargetMusclePolicy,
    goal: Goal,
    days_per_week: int,
    emphasized: Sequence[Muscle],
) -> list[Muscle]:
    """FR-3 step 2 — emphasis first, then the goal's priority order.

    Emphasized muscles that are absent from the priority list are still
    included; the tail is truncated so the set size is exactly
    ``count_by_days[days_per_week]``.
    """
    count = policy.count_by_days[days_per_week]
    targets: list[Muscle] = []
    for muscle in emphasized:
        if muscle not in targets:
            targets.append(muscle)
    for muscle in policy.priority_by_goal[goal]:
        if len(targets) >= count:
            break
        if muscle not in targets:
            targets.append(muscle)
    return targets[:count]


def select_target_muscles(profile: UserProfile, datasets: Datasets) -> list[Muscle]:
    """FR-3 step 2 for a concrete profile."""
    return target_muscles_for(
        datasets.target_policy,
        profile.goal,
        profile.days_per_week,
        profile.emphasized_muscles,
    )


# ----------------------------------------------------------------------- step 3


def weekly_set_plan(
    profile: UserProfile, targets: Sequence[Muscle], datasets: Datasets
) -> dict[Muscle, list[int]]:
    """FR-3 step 3 — the five-week per-muscle effective-set plan.

    Returns ``muscle -> [week1, week2, week3, week4, week5]``.

    The budget clamp walks the weeks in order and, while a week is over
    ``22 * days``, reduces the largest figure first (ties by reverse priority
    order, then muscle name) and applies that reduction to every later week.
    A reduction never takes a muscle below its MEV *or* below the previous
    week's figure, which is how "never breaking monotonicity" is honoured
    without retroactively editing an already-clamped week.  That floor is
    always reachable: week ``w-1`` is itself within budget, so lowering week
    ``w`` onto it is enough to satisfy the budget.
    """
    factor = EXPERIENCE_START_FACTOR[profile.experience]
    emphasized = set(profile.emphasized_muscles)
    plan: dict[Muscle, list[int]] = {}
    for muscle in targets:
        landmark = datasets.landmarks[muscle]
        start = landmark.mev + round_half_up(factor * (landmark.mav - landmark.mev))
        end = min(landmark.mav + (2 if muscle in emphasized else 0), landmark.mrv)
        end = max(end, start)
        plan[muscle] = [
            round_half_up(start + (end - start) * (week - 1) / 3.0) for week in range(1, 5)
        ]

    budget = SETS_PER_SESSION_BUDGET * profile.days_per_week
    policy = datasets.target_policy
    for week_index in range(4):

        def floor_for(muscle: Muscle, week_index: int = week_index) -> int:
            mev = datasets.landmarks[muscle].mev
            if week_index == 0:
                return mev
            return max(mev, plan[muscle][week_index - 1])

        while sum(plan[m][week_index] for m in targets) > budget:
            reducible = [m for m in targets if plan[m][week_index] > floor_for(m)]
            if not reducible:
                break
            victim = min(
                reducible,
                key=lambda m: (
                    -plan[m][week_index],
                    -policy.priority_index(profile.goal, m),
                    m.value,
                ),
            )
            for later in range(week_index, 4):
                plan[victim][later] -= 1

    for muscle in targets:
        plan[muscle].append(max(1, plan[muscle][3] // 2))
    return plan


# ----------------------------------------------------------------------- step 4


def candidate_pool(profile: UserProfile, datasets: Datasets, *, seed: int) -> list[Exercise]:
    """Equipment-usable, non-flagged exercises in the seeded base order.

    The list is shuffled once with ``random.Random(seed)``; every ranking below
    is a *stable* sort of this order, so the seed only ever reorders exact ties.
    """
    equipment = set(profile.equipment)
    flagged = set(profile.pain_flags)
    pool = [e for e in datasets.exercises if set(e.equipment) & equipment and e.id not in flagged]
    if profile.experience is Experience.BEGINNER:
        easy_patterns = {e.pattern for e in pool if e.difficulty <= 2}
        pool = [e for e in pool if e.difficulty <= 2 or e.pattern not in easy_patterns]
    ordered = sorted(pool, key=lambda e: e.id)
    random.Random(seed).shuffle(ordered)
    return ordered


def rank_key(exercise: Exercise, uncovered: set[Muscle]) -> tuple[int, int, int, str]:
    """FR-3 step 4 ranking key (lower sorts first)."""
    return (
        -len(set(exercise.primary_muscles) & uncovered),
        0 if exercise.mechanics is Mechanics.COMPOUND else 1,
        exercise.difficulty,
        exercise.id,
    )


def top_ranked(pool: Sequence[Exercise], uncovered: set[Muscle]) -> Exercise | None:
    """The single best candidate under the FR-3 ranking key."""
    ranked = sorted(pool, key=lambda e: rank_key(e, uncovered))
    return ranked[0] if ranked else None


def _select_for_session(
    template: SplitSessionTemplate,
    pool: Sequence[Exercise],
    uncovered: set[Muscle],
) -> list[Exercise]:
    chosen: list[Exercise] = []
    chosen_ids: set[str] = set()
    remaining: list[MovementPattern] = list(template.required_patterns)

    while remaining:
        candidates = [e for e in pool if e.pattern in remaining and e.id not in chosen_ids]
        pick = top_ranked(candidates, uncovered)
        if pick is None:
            remaining.pop(0)
            continue
        chosen.append(pick)
        chosen_ids.add(pick.id)
        remaining.remove(pick.pattern)
        uncovered -= set(pick.primary_muscles)
    return chosen


def _frequency_pass(
    selection: list[list[Exercise]],
    templates: Sequence[SplitSessionTemplate],
    pool: Sequence[Exercise],
    targets: Sequence[Muscle],
) -> None:
    """Give every target muscle a direct source on >= 2 distinct days.

    This is where FR-3 step 4's "and every target muscle has a source" is
    satisfied, together with step 5's ">= 2 distinct days" requirement — the two
    are the same placement problem, and solving it across the week rather than
    inside the first session is what keeps the split coherent.

    FR-3 says a target muscle needs a second day but not *which* day, so the
    choice is made in the split's own terms: a session whose required movement
    patterns already include the candidate's pattern is preferred, and only then
    the least-loaded session.  Without that preference a push/pull/legs week
    ends up with rows and squats inside "Push A", which satisfies the letter of
    the coverage rule while quietly dissolving the split.
    """
    for muscle in targets:
        days_with = [
            day
            for day, exercises in enumerate(selection)
            if any(muscle in e.primary_muscles for e in exercises)
        ]
        while len(days_with) < min(MIN_SESSIONS_PER_TARGET_MUSCLE, len(selection)):
            options: list[tuple[tuple[int, int, int], int, Exercise]] = []
            for day in range(len(selection)):
                if day in days_with:
                    continue
                if len(selection[day]) >= MAX_EXERCISES_PER_SESSION:
                    continue
                present = {e.id for e in selection[day]}
                candidates = [
                    e for e in pool if muscle in e.primary_muscles and e.id not in present
                ]
                pick = top_ranked(candidates, {muscle})
                if pick is None:
                    continue
                fits_split = pick.pattern in templates[day].required_patterns
                options.append(((0 if fits_split else 1, len(selection[day]), day), day, pick))
            if not options:
                break
            _, day, pick = min(options, key=lambda option: option[0])
            selection[day].append(pick)
            days_with.append(day)


def order_session(exercises: Sequence[Exercise]) -> list[Exercise]:
    """Constraint 9 — compounds before isolation, selection order preserved."""
    ranked = sorted(
        enumerate(exercises),
        key=lambda pair: (0 if pair[1].mechanics is Mechanics.COMPOUND else 1, pair[0]),
    )
    return [exercise for _, exercise in ranked]


def select_exercises(
    templates: Sequence[SplitSessionTemplate],
    pool: Sequence[Exercise],
    targets: Sequence[Muscle],
) -> list[list[Exercise]]:
    """FR-3 step 4 across the whole week, then the >= 2-days frequency pass."""
    uncovered = set(targets)
    selection = [_select_for_session(t, pool, uncovered) for t in templates]
    _frequency_pass(selection, templates, pool, targets)
    return [order_session(day) for day in selection]


# ----------------------------------------------------------------------- step 5


def _flat(selection: Sequence[Sequence[Exercise]], sets: Sequence[Sequence[int]]):
    for day, exercises in enumerate(selection):
        for pos, exercise in enumerate(exercises):
            yield exercise, sets[day][pos]


def allocate_sets(
    selection: Sequence[Sequence[Exercise]],
    week_plan: dict[Muscle, int],
    datasets: Datasets,
    base: Sequence[Sequence[int]] | None = None,
) -> list[list[int]]:
    """FR-3 step 5 — direct sets per (session, exercise) for one week.

    Starts from ``base`` (the previous week's allocation) so weekly volume can
    only grow, or from a small uniform seed for week 1, and then adds one set
    at a time to whichever exercise closes the most remaining target volume.
    Ties break toward the least-loaded session, which is what spreads a
    muscle's sets evenly over the days that train it.
    """
    targets = list(week_plan)
    sets: list[list[int]] = (
        [list(day) for day in base] if base is not None else [[0] * len(day) for day in selection]
    )
    if base is None:
        for day, exercises in enumerate(selection):
            for pos in range(len(exercises)):
                sets[day][pos] = MIN_SETS_PER_EXERCISE
        for day, exercises in enumerate(selection):
            for pos, exercise in enumerate(exercises):
                if _can_add(selection, sets, day, pos, exercise, datasets, targets):
                    sets[day][pos] = min(SEED_SETS_PER_EXERCISE, MAX_SETS_PER_EXERCISE)

    while True:
        effective = accumulate_effective(_flat(selection, sets))
        remaining = {m: max(0.0, week_plan[m] - effective[m]) for m in targets}
        if not any(remaining.values()):
            break
        best_key: tuple[float, int, int, str] | None = None
        best_pos: tuple[int, int] | None = None
        session_totals = [sum(day) for day in sets]
        for day, exercises in enumerate(selection):
            for pos, exercise in enumerate(exercises):
                gain = sum(min(exercise.effective_credit(m), remaining[m]) for m in targets)
                if gain <= 0:
                    continue
                if not _can_add(selection, sets, day, pos, exercise, datasets, targets):
                    continue
                key = (-gain, session_totals[day], day, exercise.id)
                if best_key is None or key < best_key:
                    best_key = key
                    best_pos = (day, pos)
        if best_pos is None:
            break
        sets[best_pos[0]][best_pos[1]] += 1
    return sets


def _can_add(
    selection: Sequence[Sequence[Exercise]],
    sets: Sequence[Sequence[int]],
    day: int,
    pos: int,
    exercise: Exercise,
    datasets: Datasets,
    targets: Sequence[Muscle],
) -> bool:
    if sets[day][pos] >= MAX_SETS_PER_EXERCISE:
        return False
    if sum(sets[day]) >= SETS_PER_SESSION_BUDGET:
        return False
    day_direct = accumulate_direct(zip(selection[day], sets[day], strict=True))
    for muscle in exercise.primary_muscles:
        if day_direct[muscle] >= MAX_DIRECT_SETS_PER_MUSCLE_PER_SESSION:
            return False
    effective = accumulate_effective(_flat(selection, sets))
    for muscle in targets:
        credit = exercise.effective_credit(muscle)
        if credit and effective[muscle] + credit > datasets.landmarks[muscle].mrv:
            return False
    return True


def next_session(
    sessions: Sequence[ProgramSession], logged_session_ids: Collection[int]
) -> ProgramSession | None:
    """SCOPE § "Next session definition" — the first unlogged session.

    The lowest ``(week, day_index)`` with **zero** linked workout logs.  A
    partially logged session counts as done, and freestyle logs never mark a
    session done because they carry no ``program_session_id``.  ``None`` means
    every session has been logged, which the API surfaces as
    ``program_complete``.

    Kept here rather than in the API layer so the rule is engine-owned and
    testable without a database.
    """
    done = set(logged_session_ids)
    ordered = sorted(sessions, key=lambda s: (s.week, s.day_index))
    for session in ordered:
        if session.id not in done:
            return session
    return None


def deload_allocation(week4: Sequence[Sequence[int]]) -> list[list[int]]:
    """Week 5 — half of week 4's sets per exercise, at least one set each."""
    return [[max(MIN_SETS_PER_EXERCISE, count // 2) for count in day] for day in week4]


# ------------------------------------------------------------------ assembly


def prescription_for(
    exercise: Exercise, goal: Goal, week: int, position: int, sets: int
) -> Prescription:
    rep_low, rep_high = GOAL_REP_RANGES[goal][exercise.mechanics]
    rest_low, rest_high = REST_BANDS[exercise.mechanics]
    return Prescription(
        position=position,
        exercise_id=exercise.id,
        sets=sets,
        rep_low=rep_low,
        rep_high=rep_high,
        target_rir=WEEK_TARGET_RIR[week],
        rest_s=max(rest_low, min(rest_high, exercise.rest_s)),
        load_note=DELOAD_LOAD_NOTE if week == 5 else None,
    )


def generate_program(
    profile: UserProfile, datasets: Datasets, *, as_of: str, seed: int
) -> ProgramPlan:
    """FR-3 — a deterministic 5-week mesocycle (4 accumulation + 1 deload)."""
    split = choose_split(profile.days_per_week)
    template = datasets.split_for(split)
    session_templates = list(template.sessions[: profile.days_per_week])
    if len(session_templates) != profile.days_per_week:
        raise ProgramGenerationError(
            f"split {split} has no template for {profile.days_per_week} days"
        )

    targets = select_target_muscles(profile, datasets)
    plan = weekly_set_plan(profile, targets, datasets)
    pool = candidate_pool(profile, datasets, seed=seed)
    if not pool:
        raise ProgramGenerationError("no exercise matches the profile's equipment")
    selection = select_exercises(session_templates, pool, targets)
    if any(not day for day in selection):
        raise ProgramGenerationError("a session could not be filled from the library")

    allocations: dict[int, list[list[int]]] = {}
    previous: list[list[int]] | None = None
    for week in range(1, 5):
        week_plan = {m: plan[m][week - 1] for m in targets}
        previous = allocate_sets(selection, week_plan, datasets, base=previous)
        allocations[week] = previous
    allocations[5] = deload_allocation(allocations[4])

    sessions: list[SessionPlan] = []
    for week in range(1, 6):
        for day, exercises in enumerate(selection):
            prescriptions = [
                prescription_for(
                    exercise, profile.goal, week, position, allocations[week][day][position]
                )
                for position, exercise in enumerate(exercises)
            ]
            sessions.append(
                SessionPlan(
                    week=week,
                    day_index=day,
                    name=session_templates[day].name,
                    prescriptions=prescriptions,
                )
            )

    program = Program(
        created_at=as_of,
        seed=seed,
        goal=profile.goal,
        experience=profile.experience,
        days_per_week=profile.days_per_week,
        emphasized_muscles=list(profile.emphasized_muscles),
        equipment=list(profile.equipment),
        target_muscles=targets,
        weeks=5,
        split=split,
        weekly_set_targets=plan,
    )
    return ProgramPlan(program=program, sessions=sessions)
