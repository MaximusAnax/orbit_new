"""FR-13 — safety behaviour, implemented rather than disclaimed.

Three mechanisms live here:

(a) **Acknowledgement gating.**  Program generation is refused until the
    profile carries a ``disclaimer_acknowledged_at`` timestamp.
(b) **Pain-flag handling.**  A set logged with ``pain_flag = true`` adds its
    exercise to the profile's flags and produces a stop-and-refer
    recommendation.  While an exercise is flagged, read-time prescriptions
    substitute it with the top-ranked alternative from the FR-3 ranking,
    restricted to the same movement pattern, the profile's equipment, not
    flagged, and no harder than the original — or drop it with the reason
    ``pain_flag_no_substitute``.  Program rows are never rewritten.
(c) **Vocabulary audit.**  ``contains_diagnosis_language`` is the predicate the
    audit test uses over every cue, rationale and error message the engine can
    emit: FormCoach talks about movement faults, never about what might be
    wrong with a body.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from formcoach.engine.programming import candidate_pool, top_ranked
from formcoach.models import Datasets, Exercise, Muscle, Prescription, SetLog, UserProfile

DISCLAIMER_NOT_ACKNOWLEDGED = "disclaimer_not_acknowledged"
PAIN_FLAG_NO_SUBSTITUTE = "pain_flag_no_substitute"

#: FR-13(c) — vocabulary that would turn a coaching note into a guess about a
#: body.  Matched case-insensitively as substrings, so ``diagnos`` catches
#: "diagnose"/"diagnosis" and ``tear`` catches "tearing".
DIAGNOSIS_TERMS: tuple[str, ...] = (
    "injury",
    "injured",
    "tear",
    "hernia",
    "diagnos",
    "strain",
    "sprain",
)

STOP_AND_REFER = (
    "Stop this exercise for now and have it looked at by a qualified health "
    "professional before you load it again. FormCoach coaches movement; it does not "
    "assess what is going on in your body."
)


class SafetyGateError(RuntimeError):
    """Raised when a safety precondition blocks an operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def contains_diagnosis_language(text: str) -> list[str]:
    """Terms from :data:`DIAGNOSIS_TERMS` present in ``text`` (case-insensitive)."""
    lowered = text.lower()
    return [term for term in DIAGNOSIS_TERMS if term in lowered]


def require_disclaimer_ack(profile: UserProfile) -> None:
    """FR-13(a) — block program generation until the notice is acknowledged."""
    if not profile.disclaimer_acknowledged_at:
        raise SafetyGateError(
            DISCLAIMER_NOT_ACKNOWLEDGED,
            "Acknowledge the not-medical-advice notice before generating a program.",
        )


def pain_flags_after(profile: UserProfile, set_logs: Sequence[SetLog]) -> list[str]:
    """FR-13(b) — profile pain flags after appending a workout's sets.

    Flags are only ever added here; they are cleared exclusively by the explicit
    clear operation, never automatically.
    """
    flags = list(profile.pain_flags)
    for set_log in set_logs:
        if set_log.pain_flag and set_log.exercise_id not in flags:
            flags.append(set_log.exercise_id)
    return flags


def flagged_exercise_ids(set_logs: Sequence[SetLog]) -> list[str]:
    """Exercise ids newly flagged by a batch of sets, in first-seen order."""
    seen: list[str] = []
    for set_log in set_logs:
        if set_log.pain_flag and set_log.exercise_id not in seen:
            seen.append(set_log.exercise_id)
    return seen


def clear_pain_flag(profile: UserProfile, exercise_id: str) -> list[str]:
    """FR-13(b) — the explicit user-driven clear operation."""
    return [flag for flag in profile.pain_flags if flag != exercise_id]


@dataclass(frozen=True)
class PainResolution:
    """What FR-13(b) decided about one prescription at read time."""

    exercise: Exercise | None
    substituted_for: str | None = None
    dropped_reason: str | None = None


def find_substitute(
    original: Exercise,
    profile: UserProfile,
    datasets: Datasets,
    target_muscles: Sequence[Muscle],
    *,
    seed: int,
) -> Exercise | None:
    """Top-ranked replacement for a flagged exercise, or ``None``.

    Uses the same seeded base order and the same ranking key as FR-3 step 4, so
    the substitution is reproducible for a given program.  The pool is narrowed
    to the original's movement pattern and to exercises no harder than it; the
    ranking's "uncovered muscles" term is the program's target set, so the
    replacement is the one that best serves what the program is developing.
    """
    pool = [
        e
        for e in candidate_pool(profile, datasets, seed=seed)
        if e.id != original.id
        and e.pattern is original.pattern
        and e.difficulty <= original.difficulty
    ]
    return top_ranked(pool, set(target_muscles))


def resolve_for_pain(
    prescription: Prescription,
    profile: UserProfile,
    datasets: Datasets,
    target_muscles: Sequence[Muscle],
    *,
    seed: int,
) -> PainResolution:
    """FR-13(b) — substitute, keep, or drop one prescription at read time."""
    if prescription.exercise_id not in profile.pain_flags:
        return PainResolution(exercise=datasets.exercise(prescription.exercise_id))
    original = datasets.exercise(prescription.exercise_id)
    replacement = find_substitute(original, profile, datasets, target_muscles, seed=seed)
    if replacement is None:
        return PainResolution(exercise=None, dropped_reason=PAIN_FLAG_NO_SUBSTITUTE)
    return PainResolution(exercise=replacement, substituted_for=original.id)
