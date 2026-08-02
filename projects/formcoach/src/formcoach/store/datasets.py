"""Load and validate the committed datasets (the ``formcoach init`` work).

The files under ``data/`` are the source of truth for the exercise library,
media manifest, volume landmarks, target-muscle priorities, split templates,
load increments and form profiles.  This module reads and schema-validates them
into :class:`~formcoach.models.Datasets`, runs the integrity checks FR-2 and
FR-3 call for, and hands the result to the engine as models — the engine itself
never touches the filesystem.

Everything here is hermetic: ``source = url`` media assets are verified by
manifest schema only and are never fetched (FR-2), so ``init`` succeeds with no
network.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

from pydantic import ValidationError

from formcoach.adapters.media_local import LocalMediaResolver
from formcoach.engine.geometry import COCO_KEYPOINTS, FEATURES, MIDPOINTS
from formcoach.engine.programming import (
    SETS_PER_SESSION_BUDGET,
    target_muscles_for,
)
from formcoach.models import (
    Datasets,
    Equipment,
    Exercise,
    FormProfile,
    Goal,
    IncrementPair,
    MediaAsset,
    MediaSource,
    Muscle,
    SplitTemplate,
    TargetMusclePolicy,
    VolumeLandmark,
)

DATA_DIR_ENV = "FORMCOACH_DATA_DIR"

#: The tightest realistic equipment profile the product supports; every
#: movement pattern a split requires must be coverable with it.
MINIMAL_EQUIPMENT: frozenset[Equipment] = frozenset({Equipment.DUMBBELL, Equipment.BODYWEIGHT})

MAX_EMPHASIZED_MUSCLES = 3

VALID_KEYPOINT_NAMES = frozenset(COCO_KEYPOINTS) | frozenset(MIDPOINTS)


class DatasetError(ValueError):
    """Raised when the committed datasets fail an integrity check."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("dataset integrity check failed:\n  - " + "\n  - ".join(problems))


def default_data_dir() -> Path:
    """``projects/formcoach/data``, overridable with ``FORMCOACH_DATA_DIR``."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def _read_json(path: Path):
    if not path.is_file():
        raise DatasetError([f"missing dataset file: {path}"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError([f"{path} is not valid JSON: {exc}"]) from exc


def load_datasets(data_dir: str | Path | None = None, *, validate: bool = True) -> Datasets:
    """Read every committed dataset, schema-validating as we go."""
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    try:
        exercises = [Exercise(**row) for row in _read_json(root / "exercises.json")]
        media_assets = [MediaAsset(**row) for row in _read_json(root / "media_manifest.json")]
        landmark_rows = [
            VolumeLandmark(**row) for row in _read_json(root / "volume_landmarks.json")
        ]
        target_policy = TargetMusclePolicy(**_read_json(root / "target_muscles.json"))
        split_templates = [
            SplitTemplate(**row) for row in _read_json(root / "split_templates.json")
        ]
        increments = {
            Equipment(key): IncrementPair(**value)
            for key, value in _read_json(root / "load_increments.json").items()
        }
    except ValidationError as exc:
        raise DatasetError([f"schema error in data/: {exc}"]) from exc

    profiles_dir = root / "form_profiles"
    form_profiles: dict[str, FormProfile] = {}
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            profile = FormProfile(**_read_json(path))
        except ValidationError as exc:
            raise DatasetError([f"schema error in {path}: {exc}"]) from exc
        form_profiles[profile.id] = profile

    datasets = Datasets(
        exercises=exercises,
        media_assets=media_assets,
        landmarks={lm.muscle: lm for lm in landmark_rows},
        target_policy=target_policy,
        split_templates=split_templates,
        load_increments=increments,
        form_profiles=form_profiles,
    )
    if validate:
        problems = validate_datasets(datasets, root)
        if problems:
            raise DatasetError(problems)
    return datasets


# ------------------------------------------------------------------- validation


def validate_datasets(datasets: Datasets, data_dir: str | Path) -> list[str]:
    """Every integrity check ``formcoach init`` runs, as a list of problems."""
    problems: list[str] = []
    problems += _check_exercises(datasets)
    problems += _check_media(datasets, data_dir)
    problems += _check_landmarks(datasets)
    problems += _check_feasibility(datasets)
    problems += _check_splits(datasets)
    problems += _check_form_profiles(datasets)
    return problems


def _check_exercises(datasets: Datasets) -> list[str]:
    problems: list[str] = []
    ids = [e.id for e in datasets.exercises]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    problems += [f"duplicate exercise id: {i}" for i in duplicates]
    known = set(ids)
    for exercise in datasets.exercises:
        variant = exercise.harder_variant_id
        if variant is None:
            continue
        if variant not in known:
            problems.append(f"{exercise.id}: harder_variant_id {variant} does not exist")
            continue
        if datasets.exercise(variant).pattern is not exercise.pattern:
            problems.append(f"{exercise.id}: harder_variant_id {variant} has a different pattern")
    return problems


def _check_media(datasets: Datasets, data_dir: str | Path) -> list[str]:
    problems: list[str] = []
    resolver = LocalMediaResolver(datasets.media_assets, data_dir)
    known = {e.id for e in datasets.exercises}
    for asset in datasets.media_assets:
        if asset.exercise_id not in known:
            problems.append(f"media asset {asset.id} references unknown exercise")
            continue
        if not resolver.verify(asset):
            detail = (
                "file is missing"
                if asset.source is MediaSource.LOCAL
                else "url or license is not schema-valid"
            )
            problems.append(f"media asset {asset.id}: {detail}")
    for exercise in datasets.exercises:
        if not datasets.assets_for(exercise.id):
            problems.append(f"{exercise.id}: no media asset (FR-2 requires at least one)")
    return problems


def _check_landmarks(datasets: Datasets) -> list[str]:
    return [
        f"volume landmarks missing muscle: {muscle.value}"
        for muscle in Muscle
        if muscle not in datasets.landmarks
    ]


def _check_feasibility(datasets: Datasets) -> list[str]:
    """FR-3 step 2 — ``sum(MEV(target)) <= 22 * days`` for every combination.

    Checked over every goal, every supported days/week and every emphasis set of
    up to three muscles, so a landmark or priority edit that makes some corner
    of the product unsatisfiable fails ``init`` loudly instead of producing an
    unreachable program later.
    """
    problems: list[str] = []
    policy = datasets.target_policy
    muscles = list(Muscle)
    emphasis_sets = [
        combo
        for size in range(MAX_EMPHASIZED_MUSCLES + 1)
        for combo in itertools.combinations(muscles, size)
    ]
    for goal in Goal:
        for days in range(2, 7):
            budget = SETS_PER_SESSION_BUDGET * days
            worst = 0
            worst_set: tuple[Muscle, ...] = ()
            for emphasis in emphasis_sets:
                targets = target_muscles_for(policy, goal, days, emphasis)
                total = sum(datasets.landmarks[m].mev for m in targets)
                if total > worst:
                    worst, worst_set = total, emphasis
            if worst > budget:
                problems.append(
                    f"feasibility: {goal.value} at {days} days needs {worst} MEV sets "
                    f"(budget {budget}); worst emphasis {[m.value for m in worst_set]}"
                )
    return problems


def _check_splits(datasets: Datasets) -> list[str]:
    problems: list[str] = []
    covered_days: set[int] = set()
    for template in datasets.split_templates:
        covered_days |= set(template.days)
        for session in template.sessions:
            for pattern in session.required_patterns:
                library = [e for e in datasets.exercises if e.pattern is pattern]
                if not library:
                    problems.append(
                        f"split {template.split.value}/{session.name}: no exercise with "
                        f"pattern {pattern.value}"
                    )
                    continue
                if not [e for e in library if set(e.equipment) & MINIMAL_EQUIPMENT]:
                    problems.append(
                        f"split {template.split.value}/{session.name}: pattern "
                        f"{pattern.value} is not coverable with dumbbell + bodyweight"
                    )
    missing = sorted(set(range(2, 7)) - covered_days)
    problems += [f"no split template covers {d} days/week" for d in missing]
    return problems


def _check_form_profiles(datasets: Datasets) -> list[str]:
    problems: list[str] = []
    by_id = {e.id: e for e in datasets.exercises}
    for profile in datasets.form_profiles.values():
        exercise = by_id.get(profile.exercise_id)
        if exercise is None:
            problems.append(f"form profile {profile.id}: unknown exercise")
        elif exercise.form_profile_id != profile.id:
            problems.append(
                f"form profile {profile.id}: {exercise.id} declares {exercise.form_profile_id}"
            )
        for names in profile.required_keypoints.values():
            problems += [
                f"form profile {profile.id}: unknown required keypoint {name}"
                for name in names
                if name not in VALID_KEYPOINT_NAMES
            ]
        for rule in profile.rules:
            if rule.feature not in FEATURES:
                problems.append(
                    f"form profile {profile.id}/{rule.fault_id}: feature "
                    f"{rule.feature} is not implemented in geometry.py"
                )
            problems += [
                f"form profile {profile.id}/{rule.fault_id}: unknown keypoint {name}"
                for name in rule.feature_keypoints
                if name not in VALID_KEYPOINT_NAMES
            ]
    for exercise in datasets.exercises:
        if exercise.form_profile_id and exercise.form_profile_id not in datasets.form_profiles:
            problems.append(
                f"{exercise.id}: form profile {exercise.form_profile_id} is not committed"
            )
    return problems
