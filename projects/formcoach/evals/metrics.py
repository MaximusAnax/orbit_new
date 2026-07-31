"""Metric implementations for the FormCoach eval suite (``docs/EVALS.md``).

Every metric here is computed against the committed fixtures with the offline
adapters (``FixturePoseEstimator``, ``LocalMediaResolver``, the in-memory
repository).  No network, no wall clock, seeded randomness only.

Two design rules from EVALS.md are load-bearing and are implemented literally:

* **The eval never lets the engine define its own denominator.**  Applicability
  comes from ``fixtures/labels.json``, which enumerates every
  ``(clip, rep, rule)`` triple and marks it assessable or not from the
  *generator's* knowledge of injected occlusion, per-frame confidence and the
  rule's declared views.  The engine's own screening is never consulted.
* **Program coverage is eval-owned.**  M5 constraint 15 is checked against
  ``fixtures/required_coverage.json`` and constraint 10 against
  ``fixtures/split_templates.json``; neither is derived from ``data/``.

Two places where EVALS.md leaves a gap that a 1.0 gate would otherwise make
unmeetable are resolved here and recorded in ``docs/REVIEW.md``:

* M4b counts non-assessable triples on true reps the segmenter **matched**,
  plus the view-mismatched rules of any *extra* predicted rep.  A true rep the
  engine never produced has no finding, so it cannot have guessed; scoring it as
  a guess would make M4b a second rep-count metric.
* M7's denominator stays every assessable triple in ``labels.json`` (so
  declining to measure cannot shrink it) while its numerator counts actual
  ``not_assessed`` predictions.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from formcoach.engine.faults import evaluate_rep
from formcoach.engine.geometry import facing_sign
from formcoach.engine.poseio import resolve_view
from formcoach.engine.programming import (
    GOAL_REP_RANGES,
    MAX_DIRECT_SETS_PER_MUSCLE_PER_SESSION,
    REST_BANDS,
    SETS_PER_SESSION_BUDGET,
    WEEK_TARGET_RIR,
    choose_split,
    generate_program,
    prescription_for,
    select_target_muscles,
)
from formcoach.engine.progression import next_prescription
from formcoach.engine.report import analyze_clip, analyze_photo
from formcoach.engine.reps import extract_signal, local_maxima, segment_reps
from formcoach.engine.volume import accumulate_direct, accumulate_effective
from formcoach.models import (
    AnalysisResult,
    AnalysisStatus,
    Datasets,
    DeclaredView,
    Equipment,
    Exercise,
    Experience,
    FindingStatus,
    Goal,
    LoggedSession,
    Mechanics,
    Muscle,
    Phase,
    Prescription,
    Program,
    ProgramPlan,
    RepBoundary,
    RepDirection,
    SessionPlan,
    SetLog,
    UserProfile,
    View,
    WorkoutLog,
    round_half_up,
)
from formcoach.store.datasets import load_datasets

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CREATED_AT = "2026-07-31T09:00:00+00:00"
SEED = 20260731

#: M3 splits features by unit; the miss penalty is 3x the gate in each unit.
ANGLE_FEATURES = frozenset(
    {"trunk_lean_deg", "fppa_deg", "elbow_angle_deg", "hip_ext_angle_deg"}
)
RATIO_FEATURES = frozenset(
    {
        "depth_ratio",
        "lateral_shift_frac",
        "bar_drift_frac",
        "hip_dev_frac",
        "hip_shoulder_rise_ratio",
    }
)
M3A_PENALTY = 15.0
M3B_PENALTY = 0.09
ALIGN_TOLERANCE_S = 0.4


# --------------------------------------------------------------------------- fixtures


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_labels() -> dict:
    return load_json("labels.json")


def datasets() -> Datasets:
    return load_datasets()


# --------------------------------------------------------------------------- engine run


def analyze_fixture(
    clip: dict, data: Datasets, *, smooth: bool = True, engine_boundaries: bool = False
) -> AnalysisResult | None:
    """Run the offline pipeline over one committed fixture.

    ``smooth=False`` reproduces the M3 baseline (features read from a single raw
    extremum frame), which is the only reason this helper is not simply
    ``analyze_clip``.
    """
    from formcoach.adapters.pose_fixture import FixturePoseEstimator

    exercise = data.exercise(clip["exercise_id"])
    profile = data.form_profiles[clip["form_profile_id"]]
    sequence = FixturePoseEstimator().estimate(FIXTURES / clip["file"])
    declared = DeclaredView(clip["declared_view"]) if clip["declared_view"] else None
    if clip["kind"] == "photo":
        return analyze_photo(
            sequence,
            exercise=exercise,
            profile=profile,
            created_at=CREATED_AT,
            phase=Phase(clip["declared_phase"]),
            declared_view=declared,
        )
    if smooth:
        return analyze_clip(
            sequence,
            exercise=exercise,
            profile=profile,
            created_at=CREATED_AT,
            declared_view=declared,
        )
    return _analyze_unsmoothed(sequence, profile, declared, engine_boundaries=engine_boundaries)


def naive_boundaries(sequence, profile) -> list[RepBoundary]:
    """Segmentation with no smoothing and no prominence/duration hysteresis.

    This is the M2 baseline's segmenter, reused so the M3 baseline is the same
    naive *pipeline* rather than the engine's segmentation with one step removed.
    """
    signal = extract_signal(sequence, profile.primary_signal) or []
    work = (
        list(signal)
        if profile.direction is RepDirection.DOWN_UP
        else [-value for value in signal]
    )
    peaks = local_maxima(work)
    if not peaks:
        return []

    def argmin(lo: int, hi: int) -> int:
        best = lo
        for i in range(lo, hi + 1):
            if work[i] < work[best]:
                best = i
        return best

    n = len(work)
    turns = [argmin(0, peaks[0])]
    turns += [argmin(peaks[i - 1], peaks[i]) for i in range(1, len(peaks))]
    turns.append(argmin(peaks[-1], n - 1))
    out: list[RepBoundary] = []
    for i, extremum in enumerate(peaks):
        start = max(0, min(turns[i], extremum - 1))
        end = min(n - 1, max(turns[i + 1], extremum + 1))
        if start < extremum < end:
            out.append(
                RepBoundary(
                    rep_index=len(out),
                    start_frame=start,
                    extremum_frame=extremum,
                    end_frame=end,
                )
            )
    return out


def _analyze_unsmoothed(sequence, profile, declared, *, engine_boundaries: bool):
    """The naive pipeline: rules read off raw, unsmoothed keypoints.

    ``engine_boundaries=True`` keeps the FR-8 segmenter and removes *only* the
    measurement smoothing, which isolates what the Savitzky-Golay stage buys;
    ``False`` is the literal "no smoothing" baseline EVALS.md names, and is the
    number the scorecard gates against.
    """
    resolution = resolve_view(sequence, declared)
    facing = facing_sign(sequence.frames)
    boundaries = (
        segment_reps(sequence, profile)
        if engine_boundaries
        else naive_boundaries(sequence, profile)
    )
    reps = []
    for boundary in boundaries:
        findings, metrics = evaluate_rep(
            sequence, profile, boundary, resolution.view, facing
        )
        reps.append(
            _RawRep(
                rep_index=boundary.rep_index,
                extremum_frame=boundary.extremum_frame,
                metrics=metrics,
                findings=findings,
            )
        )
    return _RawResult(reps=reps)


@dataclass(frozen=True)
class _RawRep:
    rep_index: int
    extremum_frame: int
    metrics: dict[str, float]
    findings: list


@dataclass(frozen=True)
class _RawResult:
    reps: list[_RawRep]


class EngineRun:
    """One pass of the analysis pipeline over every committed pose fixture."""

    def __init__(
        self,
        data: Datasets,
        labels: dict,
        *,
        smooth: bool = True,
        engine_boundaries: bool = False,
    ) -> None:
        self.data = data
        self.labels = labels
        self.results: dict[str, object] = {}
        for clip in labels["clips"]:
            self.results[clip["clip_id"]] = analyze_fixture(
                clip, data, smooth=smooth, engine_boundaries=engine_boundaries
            )

    def result(self, clip_id: str):
        return self.results[clip_id]


# --------------------------------------------------------------------------- alignment


@dataclass(frozen=True)
class Alignment:
    matched: dict[int, int]
    unmatched_true: list[int]
    unmatched_pred: list[int]


def align_reps(
    true_reps: Sequence[dict], pred_reps: Sequence, fps: float | None
) -> Alignment:
    """EVALS.md § "Rep alignment" — the single shared true/predicted pairing.

    Candidate pairs are those within ``0.4 * fps`` frames of one another by
    extremum frame; they are taken greedily by ascending distance with ties
    broken by true index then predicted index.  Photos have exactly one rep on
    each side and go through the same function.
    """
    tolerance = ALIGN_TOLERANCE_S * (fps if fps else 1.0 / ALIGN_TOLERANCE_S)
    pairs: list[tuple[float, int, int]] = []
    for ti, true_rep in enumerate(true_reps):
        for pj, pred_rep in enumerate(pred_reps):
            distance = abs(true_rep["extremum_frame"] - pred_rep.extremum_frame)
            if distance <= tolerance:
                pairs.append((distance, ti, pj))
    pairs.sort()
    matched: dict[int, int] = {}
    used_pred: set[int] = set()
    for _distance, ti, pj in pairs:
        if ti in matched or pj in used_pred:
            continue
        matched[ti] = pj
        used_pred.add(pj)
    return Alignment(
        matched=matched,
        unmatched_true=[i for i in range(len(true_reps)) if i not in matched],
        unmatched_pred=[j for j in range(len(pred_reps)) if j not in used_pred],
    )


def _findings_by_id(rep) -> dict[str, object]:
    return {finding.fault_id: finding for finding in rep.findings}


def _valid_clips(labels: dict) -> list[dict]:
    return [c for c in labels["clips"] if c["valid"]]


def _alignment_for(clip: dict, run: EngineRun) -> tuple[Alignment, list]:
    result = run.result(clip["clip_id"])
    reps = list(getattr(result, "reps", []))
    if isinstance(result, AnalysisResult) and result.analysis.status is AnalysisStatus.REJECTED:
        reps = []
    return align_reps(clip["reps"], reps, clip["fps"]), reps


# --------------------------------------------------------------------------- M1 / M1b


def fault_classes(labels: dict) -> list[tuple[str, str]]:
    classes: set[tuple[str, str]] = set()
    for clip in _valid_clips(labels):
        for rep in clip["reps"]:
            for fault_id, info in rep["rules"].items():
                if info["assessable"]:
                    classes.add((clip["exercise_id"], fault_id))
    return sorted(classes)


def class_f1(counts: dict[tuple[str, str], dict[str, int]]) -> dict[tuple[str, str], float]:
    scores: dict[tuple[str, str], float] = {}
    for key, c in counts.items():
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        scores[key] = (
            2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        )
    return scores


def fault_counts(labels: dict, run: EngineRun) -> dict[tuple[str, str], dict[str, int]]:
    """TP/FP/FN per ``(exercise_id, fault_id)`` class, EVALS.md § M1."""
    counts = {key: {"tp": 0, "fp": 0, "fn": 0} for key in fault_classes(labels)}
    for clip in _valid_clips(labels):
        alignment, pred_reps = _alignment_for(clip, run)
        for ti, true_rep in enumerate(clip["reps"]):
            pj = alignment.matched.get(ti)
            predicted = _findings_by_id(pred_reps[pj]) if pj is not None else {}
            for fault_id, info in true_rep["rules"].items():
                if not info["assessable"]:
                    continue
                key = (clip["exercise_id"], fault_id)
                truth_fault = info["label"] == "fault"
                finding = predicted.get(fault_id)
                pred_fault = finding is not None and finding.status is FindingStatus.FAULT
                if truth_fault and pred_fault:
                    counts[key]["tp"] += 1
                elif truth_fault:
                    counts[key]["fn"] += 1
                elif pred_fault:
                    counts[key]["fp"] += 1
        for pj in alignment.unmatched_pred:
            for finding in pred_reps[pj].findings:
                if finding.status is FindingStatus.FAULT:
                    key = (clip["exercise_id"], finding.fault_id)
                    if key in counts:
                        counts[key]["fp"] += 1
    return counts


def m1_macro_f1(labels: dict, run: EngineRun) -> float:
    scores = class_f1(fault_counts(labels, run))
    return sum(scores.values()) / len(scores) if scores else 0.0


def m1b_worst_class_f1(labels: dict, run: EngineRun) -> float:
    scores = class_f1(fault_counts(labels, run))
    return min(scores.values()) if scores else 0.0


def _flag_all_counts(labels: dict) -> dict[tuple[str, str], dict[str, int]]:
    counts = {key: {"tp": 0, "fp": 0, "fn": 0} for key in fault_classes(labels)}
    for clip in _valid_clips(labels):
        for rep in clip["reps"]:
            for fault_id, info in rep["rules"].items():
                if not info["assessable"]:
                    continue
                key = (clip["exercise_id"], fault_id)
                counts[key]["tp" if info["label"] == "fault" else "fp"] += 1
    return counts


def baseline_m1_flag_all(labels: dict) -> float:
    scores = class_f1(_flag_all_counts(labels))
    return sum(scores.values()) / len(scores) if scores else 0.0


def baseline_m1b_flag_all(labels: dict) -> float:
    scores = class_f1(_flag_all_counts(labels))
    return min(scores.values()) if scores else 0.0


def fault_base_rate(labels: dict) -> float:
    total = faults = 0
    for clip in _valid_clips(labels):
        for rep in clip["reps"]:
            for info in rep["rules"].values():
                if info["assessable"]:
                    total += 1
                    faults += int(info["label"] == "fault")
    return faults / total if total else 0.0


# --------------------------------------------------------------------------- M2


def _clip_only(labels: dict) -> list[dict]:
    return [c for c in labels["clips"] if c["kind"] == "clip"]


def m2_rep_count_accuracy(labels: dict, run: EngineRun) -> float:
    clips = [c for c in _clip_only(labels) if c["valid"]]
    if not clips:
        return 0.0
    hits = 0
    for clip in clips:
        result = run.result(clip["clip_id"])
        predicted = (
            result.analysis.rep_count
            if isinstance(result, AnalysisResult)
            else len(result.reps)
        )
        hits += int(predicted == clip["rep_count"])
    return hits / len(clips)


def baseline_m2_raw_peaks(labels: dict, data: Datasets) -> float:
    """Naive segmentation: count raw local extrema, no smoothing or hysteresis."""
    from formcoach.adapters.pose_fixture import FixturePoseEstimator

    clips = [c for c in _clip_only(labels) if c["valid"]]
    if not clips:
        return 0.0
    estimator = FixturePoseEstimator()
    hits = 0
    for clip in clips:
        profile = data.form_profiles[clip["form_profile_id"]]
        sequence = estimator.estimate(FIXTURES / clip["file"])
        signal = extract_signal(sequence, profile.primary_signal) or []
        work = (
            list(signal)
            if profile.direction is RepDirection.DOWN_UP
            else [-value for value in signal]
        )
        hits += int(len(local_maxima(work)) == clip["rep_count"])
    return hits / len(clips)


# --------------------------------------------------------------------------- M3


def feature_errors(labels: dict, run: EngineRun) -> tuple[list[float], list[float]]:
    """Absolute errors per labelled instance, split into angle and ratio units."""
    angles: list[float] = []
    ratios: list[float] = []
    for clip in _valid_clips(labels):
        alignment, pred_reps = _alignment_for(clip, run)
        for ti, true_rep in enumerate(clip["reps"]):
            pj = alignment.matched.get(ti)
            for info in true_rep["rules"].values():
                if not info["assessable"]:
                    continue
                feature = info["feature"]
                bucket = angles if feature in ANGLE_FEATURES else ratios
                penalty = M3A_PENALTY if feature in ANGLE_FEATURES else M3B_PENALTY
                if pj is None:
                    bucket.append(penalty)
                    continue
                measured = pred_reps[pj].metrics.get(info["metric_key"])
                bucket.append(
                    penalty if measured is None else abs(measured - info["true_value"])
                )
    return angles, ratios


def m3_errors(labels: dict, run: EngineRun) -> tuple[float, float]:
    angles, ratios = feature_errors(labels, run)
    return (
        sum(angles) / len(angles) if angles else 0.0,
        sum(ratios) / len(ratios) if ratios else 0.0,
    )


# --------------------------------------------------------------------------- M4


def m4_screening_and_view(labels: dict, run: EngineRun) -> tuple[float, int, int]:
    """Clip acceptance, rejection reason and inferred-view correctness."""
    clips = _clip_only(labels)
    undeclared = [c for c in clips if c["declared_view"] is None]
    total = len(clips) + len(undeclared)
    score = 0
    for clip in clips:
        result = run.result(clip["clip_id"])
        assert isinstance(result, AnalysisResult)
        rejected = result.analysis.status is AnalysisStatus.REJECTED
        if clip["valid"] and not rejected:
            score += 1
        elif not clip["valid"] and rejected:
            score += int(result.analysis.reject_reason == clip["reject_reason"])
    for clip in undeclared:
        result = run.result(clip["clip_id"])
        assert isinstance(result, AnalysisResult)
        score += int(result.analysis.view.value == clip["view"])
    return (score / total if total else 0.0), score, total


def baseline_m4_accept_everything(labels: dict) -> float:
    """Accept every clip and always call the view ``side_left``."""
    clips = _clip_only(labels)
    undeclared = [c for c in clips if c["declared_view"] is None]
    total = len(clips) + len(undeclared)
    score = sum(1 for c in clips if c["valid"])
    score += sum(1 for c in undeclared if c["view"] == View.SIDE_LEFT.value)
    return score / total if total else 0.0


# --------------------------------------------------------------------------- M4b / M7


def _clip_view_mismatched_rules(clip: dict) -> set[str]:
    """Rules whose declared views exclude this clip's view — a clip-level fact."""
    out: set[str] = set()
    for rep in clip["reps"]:
        for fault_id, info in rep["rules"].items():
            if not info["assessable"] and info.get("reason") == "view_mismatch":
                out.add(fault_id)
    return out


def m4b_never_guess(labels: dict, run: EngineRun) -> tuple[float, int, int]:
    hits = total = 0
    for clip in _valid_clips(labels):
        alignment, pred_reps = _alignment_for(clip, run)
        for ti, true_rep in enumerate(clip["reps"]):
            pj = alignment.matched.get(ti)
            if pj is None:
                continue
            predicted = _findings_by_id(pred_reps[pj])
            for fault_id, info in true_rep["rules"].items():
                if info["assessable"]:
                    continue
                total += 1
                finding = predicted.get(fault_id)
                hits += int(
                    finding is not None and finding.status is FindingStatus.NOT_ASSESSED
                )
        mismatched = _clip_view_mismatched_rules(clip)
        for pj in alignment.unmatched_pred:
            predicted = _findings_by_id(pred_reps[pj])
            for fault_id in mismatched:
                total += 1
                finding = predicted.get(fault_id)
                hits += int(
                    finding is not None and finding.status is FindingStatus.NOT_ASSESSED
                )
    return (hits / total if total else 0.0), hits, total


def m7_refusal_rate(labels: dict, run: EngineRun) -> tuple[float, int, int]:
    refusals = total = 0
    for clip in _valid_clips(labels):
        alignment, pred_reps = _alignment_for(clip, run)
        for ti, true_rep in enumerate(clip["reps"]):
            pj = alignment.matched.get(ti)
            predicted = _findings_by_id(pred_reps[pj]) if pj is not None else {}
            for fault_id, info in true_rep["rules"].items():
                if not info["assessable"]:
                    continue
                total += 1
                finding = predicted.get(fault_id)
                refusals += int(
                    finding is not None and finding.status is FindingStatus.NOT_ASSESSED
                )
    return (refusals / total if total else 0.0), refusals, total


def baseline_m4b_never_refuse() -> float:
    return 0.0


def baseline_m7_refuse_everything() -> float:
    return 1.0


# --------------------------------------------------------------------------- M5


def grid_profiles() -> list[UserProfile]:
    payload = load_json("profiles_grid.json")
    return [
        UserProfile(
            goal=Goal(p["goal"]),
            experience=Experience(p["experience"]),
            days_per_week=p["days_per_week"],
            equipment=[Equipment(e) for e in p["equipment"]],
            emphasized_muscles=[Muscle(m) for m in p["emphasized_muscles"]],
            disclaimer_acknowledged_at=p["disclaimer_acknowledged_at"],
            updated_at=p["updated_at"],
        )
        for p in payload["profiles"]
    ]


def grid_as_of() -> str:
    return load_json("profiles_grid.json")["as_of"]


def eval_split_template(days_per_week: int) -> list[dict]:
    payload = load_json("split_templates.json")
    split = payload["day_to_split"][str(days_per_week)]
    template = next(t for t in payload["templates"] if t["split"] == split)
    return template["sessions"][:days_per_week]


def eval_required_coverage(goal: Goal, days_per_week: int) -> list[Muscle]:
    payload = load_json("required_coverage.json")["goals"][goal.value]
    key = "days_2" if days_per_week == 2 else "days_3" if days_per_week == 3 else "days_4_plus"
    return [Muscle(m) for m in payload[key]]


def eval_mev(muscle: Muscle) -> int:
    return load_json("required_coverage.json")["landmark_mev"][muscle.value]


_EXPERIENCE_FACTOR = {
    Experience.BEGINNER: 0.0,
    Experience.INTERMEDIATE: 0.25,
    Experience.ADVANCED: 0.50,
}


def eval_weekly_plan(
    profile: UserProfile, targets: Sequence[Muscle], data: Datasets
) -> dict[Muscle, list[int]]:
    """An independent implementation of SCOPE.md FR-3 step 3, for constraint 14.

    Written from the specification rather than reused from the engine so that
    constraint 14 genuinely pins the experience dimension: if the engine's ramp
    or budget clamp drifts, the two disagree.
    """
    factor = _EXPERIENCE_FACTOR[profile.experience]
    emphasized = set(profile.emphasized_muscles)
    priority = data.target_policy.priority_by_goal[profile.goal]
    plan: dict[Muscle, list[int]] = {}
    for muscle in targets:
        lm = data.landmarks[muscle]
        start = lm.mev + round_half_up(factor * (lm.mav - lm.mev))
        end = max(min(lm.mav + (2 if muscle in emphasized else 0), lm.mrv), start)
        plan[muscle] = [
            round_half_up(start + (end - start) * (week - 1) / 3.0) for week in range(1, 5)
        ]
    budget = SETS_PER_SESSION_BUDGET * profile.days_per_week
    for week in range(4):
        while sum(plan[m][week] for m in targets) > budget:
            floors = {
                m: data.landmarks[m].mev
                if week == 0
                else max(data.landmarks[m].mev, plan[m][week - 1])
                for m in targets
            }
            reducible = [m for m in targets if plan[m][week] > floors[m]]
            if not reducible:
                break
            victim = min(
                reducible,
                key=lambda m: (
                    -plan[m][week],
                    -(priority.index(m) if m in priority else len(priority)),
                    m.value,
                ),
            )
            for later in range(week, 4):
                plan[victim][later] -= 1
    return plan


def program_constraint_results(
    plan: ProgramPlan,
    profile: UserProfile,
    data: Datasets,
    regenerate: Callable[[], ProgramPlan],
) -> list[bool]:
    """The fifteen SCOPE.md § "Program constraints" items, in order."""
    targets = plan.program.target_muscles
    by_week: dict[int, list[SessionPlan]] = {}
    for session in plan.sessions:
        by_week.setdefault(session.week, []).append(session)

    def allocations(sessions):
        return [
            (data.exercise(p.exercise_id), p.sets) for s in sessions for p in s.prescriptions
        ]

    effective = {w: accumulate_effective(allocations(by_week.get(w, []))) for w in range(1, 6)}
    ok = [True] * 15

    for week in range(1, 5):
        for muscle in targets:
            lm = data.landmarks[muscle]
            if not lm.mev <= effective[week][muscle] <= lm.mrv:
                ok[0] = False
            days = sum(
                1
                for s in by_week.get(week, [])
                if any(
                    data.exercise(p.exercise_id).effective_credit(muscle) > 0
                    for p in s.prescriptions
                )
            )
            if days < 2:
                ok[1] = False
    for muscle in profile.emphasized_muscles:
        series = [effective[w][muscle] for w in range(1, 5)]
        if any(series[i] > series[i + 1] + 1e-9 for i in range(3)):
            ok[2] = False
    for muscle in Muscle:
        if effective[5][muscle] > 0.5 * effective[4][muscle] + 1e-9:
            ok[3] = False
    for session in by_week.get(5, []):
        if any(p.target_rir < 4 for p in session.prescriptions):
            ok[3] = False
    rirs = [by_week[w][0].prescriptions[0].target_rir for w in range(1, 5)]
    if any(rirs[i] < rirs[i + 1] for i in range(3)) or not all(1 <= r <= 3 for r in rirs):
        ok[4] = False

    eval_sessions = eval_split_template(profile.days_per_week)
    for session in plan.sessions:
        seen_isolation = False
        total_sets = sum(p.sets for p in session.prescriptions)
        for prescription in session.prescriptions:
            exercise = data.exercise(prescription.exercise_id)
            if (prescription.rep_low, prescription.rep_high) != GOAL_REP_RANGES[profile.goal][
                exercise.mechanics
            ]:
                ok[5] = False
            if not set(exercise.equipment) & set(profile.equipment):
                ok[6] = False
            is_isolation = exercise.mechanics is Mechanics.ISOLATION
            if seen_isolation and not is_isolation:
                ok[8] = False
            seen_isolation = seen_isolation or is_isolation
            low, high = REST_BANDS[exercise.mechanics]
            if not low <= prescription.rest_s <= high:
                ok[10] = False
            if profile.experience is Experience.BEGINNER and exercise.difficulty == 3:
                easier = [
                    e
                    for e in data.exercises
                    if e.pattern is exercise.pattern
                    and e.difficulty <= 2
                    and set(e.equipment) & set(profile.equipment)
                ]
                if easier:
                    ok[12] = False
        if total_sets > SETS_PER_SESSION_BUDGET:
            ok[7] = False
        direct = accumulate_direct(
            [(data.exercise(p.exercise_id), p.sets) for p in session.prescriptions]
        )
        if any(count > MAX_DIRECT_SETS_PER_MUSCLE_PER_SESSION for count in direct.values()):
            ok[7] = False
        required = {p for p in eval_sessions[session.day_index]["required_patterns"]}
        present = {data.exercise(p.exercise_id).pattern.value for p in session.prescriptions}
        if not required <= present:
            ok[9] = False

    if regenerate() != plan:
        ok[11] = False

    expected = eval_weekly_plan(profile, targets, data)
    for muscle in targets:
        if plan.program.weekly_set_targets.get(muscle, [None])[0] != expected[muscle][0]:
            ok[13] = False

    for muscle in eval_required_coverage(profile.goal, profile.days_per_week):
        if effective[4][muscle] < eval_mev(muscle):
            ok[14] = False
    return ok


def m5_program_constraints(data: Datasets) -> tuple[float, list[str]]:
    as_of = grid_as_of()
    passed = total = 0
    failures: list[str] = []
    for profile in grid_profiles():
        plan = generate_program(profile, data, as_of=as_of, seed=SEED)
        results = program_constraint_results(
            plan, profile, data, lambda p=profile: generate_program(p, data, as_of=as_of, seed=SEED)
        )
        passed += sum(results)
        total += len(results)
        for index, value in enumerate(results):
            if not value:
                failures.append(
                    f"{profile.goal.value}/{profile.days_per_week}d/"
                    f"{profile.experience.value}: constraint {index + 1}"
                )
    return (passed / total if total else 0.0), failures


def naive_program(profile: UserProfile, data: Datasets) -> ProgramPlan:
    """The M5 baseline: 3x10 on a fixed session repeated daily, no deload, no RIR ramp."""
    targets = select_target_muscles(profile, data)
    equipment = set(profile.equipment)
    pool = [e for e in data.exercises if set(e.equipment) & equipment]
    pool.sort(key=lambda e: e.id)
    chosen = pool[:6]
    sessions: list[SessionPlan] = []
    for week in range(1, 6):
        for day in range(profile.days_per_week):
            sessions.append(
                SessionPlan(
                    week=week,
                    day_index=day,
                    name="Full Body",
                    prescriptions=[
                        Prescription(
                            position=i,
                            exercise_id=e.id,
                            sets=3,
                            rep_low=8,
                            rep_high=12,
                            target_rir=2.0,
                            rest_s=120,
                        )
                        for i, e in enumerate(chosen)
                    ],
                )
            )
    program = Program(
        created_at=grid_as_of(),
        seed=SEED,
        goal=profile.goal,
        experience=profile.experience,
        days_per_week=profile.days_per_week,
        emphasized_muscles=list(profile.emphasized_muscles),
        equipment=list(profile.equipment),
        target_muscles=targets,
        weeks=5,
        split=choose_split(profile.days_per_week),
        weekly_set_targets={m: [3, 3, 3, 3, 3] for m in targets},
    )
    return ProgramPlan(program=program, sessions=sessions)


def baseline_m5_fixed_template(data: Datasets) -> float:
    passed = total = 0
    for profile in grid_profiles():
        plan = naive_program(profile, data)
        results = program_constraint_results(
            plan, profile, data, lambda p=profile: naive_program(p, data)
        )
        passed += sum(results)
        total += len(results)
    return passed / total if total else 0.0


# --------------------------------------------------------------------------- M6


def _scenario_history(scenario: dict) -> list[LoggedSession]:
    sessions: list[LoggedSession] = []
    for entry in scenario["history"]:
        sets = [
            SetLog(
                exercise_id=scenario["exercise_id"],
                set_index=index,
                weight_kg=weight,
                reps=reps,
                rir=rir,
            )
            for index, (weight, reps, rir) in enumerate(entry["sets"])
        ]
        sessions.append(
            LoggedSession(workout=WorkoutLog(performed_at=entry["performed_at"]), sets=sets)
        )
    return sessions


def _scenario_prescription(scenario: dict) -> Prescription:
    payload = scenario["prescription"]
    return Prescription(
        position=payload["position"],
        exercise_id=scenario["exercise_id"],
        sets=payload["sets"],
        rep_low=payload["rep_low"],
        rep_high=payload["rep_high"],
        target_rir=payload["target_rir"],
        rest_s=payload["rest_s"],
    )


def run_scenario(scenario: dict, data: Datasets):
    exercise = data.exercise(scenario["exercise_id"])
    variant = (
        data.exercise(exercise.harder_variant_id) if exercise.harder_variant_id else None
    )
    return next_prescription(
        _scenario_history(scenario),
        _scenario_prescription(scenario),
        exercise,
        week=scenario["week"],
        increment_kg=data.increment_kg(exercise),
        harder_variant=variant,
    )


def _scenario_matches(scenario: dict, result) -> bool:
    expected = scenario["expected"]
    if result.action.value != expected["action"] or result.clause != expected["clause"]:
        return False
    want = expected["suggested_load_kg"]
    got = result.suggested_load_kg
    if want is None or got is None:
        if (want is None) != (got is None):
            return False
    elif abs(want - got) > 1e-6:
        return False
    if "exercise_id" in expected and result.exercise_id != expected["exercise_id"]:
        return False
    if "target_reps" in expected and result.target_reps != expected["target_reps"]:
        return False
    return "sets" not in expected or result.sets == expected["sets"]


def m6_progression(data: Datasets) -> tuple[float, list[str]]:
    scenarios = load_json("progression_golden.json")["scenarios"]
    failures: list[str] = []
    hits = 0
    for scenario in scenarios:
        result = run_scenario(scenario, data)
        if _scenario_matches(scenario, result):
            hits += 1
        else:
            failures.append(
                f"{scenario['id']}: expected {scenario['expected']['action']}/"
                f"{scenario['expected']['clause']}/{scenario['expected']['suggested_load_kg']} "
                f"got {result.action.value}/{result.clause}/{result.suggested_load_kg}"
            )
    return (hits / len(scenarios) if scenarios else 0.0), failures


def baseline_m6_always_increase(data: Datasets) -> float:
    """Always ``increase_load`` by one increment from the last top-set load."""
    scenarios = load_json("progression_golden.json")["scenarios"]
    hits = 0
    for scenario in scenarios:
        expected = scenario["expected"]
        if expected["action"] != "increase_load":
            continue
        exercise = data.exercise(scenario["exercise_id"])
        increment = data.increment_kg(exercise)
        history = _scenario_history(scenario)
        if increment is None or not history:
            continue
        last = max(s.weight_kg for s in history[-1].sets)
        guess = last + increment
        want = expected["suggested_load_kg"]
        hits += int(want is not None and abs(guess - want) <= 1e-6)
    return hits / len(scenarios) if scenarios else 0.0


# --------------------------------------------------------------------------- M8


def m8_real_clips(data: Datasets) -> dict | None:
    """Reported, never gated: agreement on hand-labelled clips of real lifts."""
    manifest = FIXTURES / "real" / "labels.json"
    if not manifest.is_file():
        return None
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    clips = payload.get("clips", [])
    if not clips:
        return None
    rep_hits = 0
    jaccards: list[float] = []
    for clip in clips:
        result = analyze_fixture(
            {
                "kind": "clip",
                "file": f"real/{clip['file']}",
                "exercise_id": clip["exercise_id"],
                "form_profile_id": clip["form_profile_id"],
                "declared_view": clip.get("declared_view"),
            },
            data,
        )
        assert isinstance(result, AnalysisResult)
        rep_hits += int(result.analysis.rep_count == clip["rep_count"])
        predicted = {
            (rep.rep_index, finding.fault_id)
            for rep in result.reps
            for finding in rep.findings
            if finding.status is FindingStatus.FAULT
        }
        truth = {(int(r), f) for r, f in clip["faults"]}
        union = predicted | truth
        jaccards.append(len(predicted & truth) / len(union) if union else 1.0)
    return {
        "clips": len(clips),
        "rep_count_agreement": rep_hits / len(clips),
        "fault_jaccard": sum(jaccards) / len(jaccards),
    }


# --------------------------------------------------------------------------- scorecard


@dataclass(frozen=True)
class MetricResult:
    key: str
    name: str
    value: float
    gate: float
    direction: str  # "min" | "max"
    baseline: float
    baseline_name: str
    unit: str = ""
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def passing(self) -> bool:
        if self.direction == "min":
            return self.value >= self.gate - 1e-9
        return self.value <= self.gate + 1e-9

    @property
    def baseline_beaten(self) -> bool:
        """Whether the gate is strictly harder than the naive baseline scores."""
        if self.direction == "min":
            return self.gate > self.baseline + 1e-9
        return self.gate < self.baseline - 1e-9

    def gate_text(self) -> str:
        symbol = ">=" if self.direction == "min" else "<="
        return f"{symbol} {self.gate:g}{self.unit}"


def build_scorecard(data: Datasets | None = None) -> tuple[list[MetricResult], dict]:
    """Compute every gated metric plus its naive baseline."""
    data = data or datasets()
    labels = load_labels()
    run = EngineRun(data, labels)
    naive_run = EngineRun(data, labels, smooth=False)
    measure_only_run = EngineRun(data, labels, smooth=False, engine_boundaries=True)

    m1 = m1_macro_f1(labels, run)
    m1b = m1b_worst_class_f1(labels, run)
    m2 = m2_rep_count_accuracy(labels, run)
    m3a, m3b = m3_errors(labels, run)
    base_m3a, base_m3b = m3_errors(labels, naive_run)
    lazy_m3a, lazy_m3b = m3_errors(labels, measure_only_run)
    m4, m4_hits, m4_total = m4_screening_and_view(labels, run)
    m4b, m4b_hits, m4b_total = m4b_never_guess(labels, run)
    m7, m7_hits, m7_total = m7_refusal_rate(labels, run)
    m5, m5_failures = m5_program_constraints(data)
    m6, m6_failures = m6_progression(data)

    classes = class_f1(fault_counts(labels, run))
    worst = min(classes, key=lambda k: classes[k]) if classes else ("", "")

    results = [
        MetricResult(
            "M1",
            "Fault-detection macro-F1",
            m1,
            0.80,
            "min",
            baseline_m1_flag_all(labels),
            "flag every assessable triple",
            detail=f"{len(classes)} classes, fault base rate {fault_base_rate(labels):.2f}",
        ),
        MetricResult(
            "M1b",
            "Worst-class F1 floor",
            m1b,
            0.55,
            "min",
            baseline_m1b_flag_all(labels),
            "flag every assessable triple",
            detail=f"worst class {worst[0]}/{worst[1]}",
        ),
        MetricResult(
            "M2",
            "Rep-count exact accuracy",
            m2,
            0.90,
            "min",
            baseline_m2_raw_peaks(labels, data),
            "raw peak count, no hysteresis",
            detail=f"{len([c for c in _clip_only(labels) if c['valid']])} valid clips",
        ),
        MetricResult(
            "M3a",
            "Angle MAE",
            m3a,
            5.0,
            "max",
            base_m3a,
            "no smoothing anywhere (raw signal, raw frames)",
            unit=" deg",
            detail=(
                f"{len(feature_errors(labels, run)[0])} instances; "
                f"smoothing removed but FR-8 boundaries kept scores {lazy_m3a:.3f} deg"
            ),
        ),
        MetricResult(
            "M3b",
            "Ratio MAE",
            m3b,
            0.03,
            "max",
            base_m3b,
            "no smoothing anywhere (raw signal, raw frames)",
            detail=(
                f"{len(feature_errors(labels, run)[1])} instances; "
                f"smoothing removed but FR-8 boundaries kept scores {lazy_m3b:.4f}"
            ),
        ),
        MetricResult(
            "M4",
            "Screening + view resolution",
            m4,
            1.0,
            "min",
            baseline_m4_accept_everything(labels),
            "accept everything, always side_left",
            detail=f"{m4_hits}/{m4_total}",
        ),
        MetricResult(
            "M4b",
            "Never guess where you cannot look",
            m4b,
            1.0,
            "min",
            baseline_m4b_never_refuse(),
            "never emit not_assessed",
            detail=f"{m4b_hits}/{m4b_total} triples",
        ),
        MetricResult(
            "M7",
            "Unjustified refusal rate",
            m7,
            0.02,
            "max",
            baseline_m7_refuse_everything(),
            "refuse everything",
            detail=f"{m7_hits}/{m7_total} assessable triples",
        ),
        MetricResult(
            "M5",
            "Program constraint satisfaction",
            m5,
            1.0,
            "min",
            baseline_m5_fixed_template(data),
            "3x10 fixed template, no deload",
            detail=f"120 profiles x 15 constraints; {len(m5_failures)} violations",
            notes=m5_failures[:8],
        ),
        MetricResult(
            "M6",
            "Progression decision accuracy",
            m6,
            1.0,
            "min",
            baseline_m6_always_increase(data),
            "always increase by one increment",
            detail="28 golden scenarios",
            notes=m6_failures[:8],
        ),
    ]
    extras = {"m8": m8_real_clips(data), "classes": classes}
    return results, extras


def handcheck_rows() -> list[dict]:
    return load_json("labels_handcheck.json")["reps"]


def handcheck_engine_values(data: Datasets | None = None) -> list[tuple[dict, dict]]:
    """Pair each hand-computed row with the engine's measurement of the same rep."""
    data = data or datasets()
    labels = load_labels()
    by_id = {c["clip_id"]: c for c in labels["clips"]}
    out: list[tuple[dict, dict]] = []
    for row in handcheck_rows():
        clip = by_id[row["clip_id"]]
        result = analyze_fixture(clip, data)
        assert isinstance(result, AnalysisResult)
        alignment = align_reps(clip["reps"], result.reps, clip["fps"])
        pj = alignment.matched.get(row["rep_index"])
        metrics = result.reps[pj].metrics if pj is not None else {}
        measured = {
            feature: metrics.get(f"{feature}@{row['phase']}", metrics.get(feature))
            for feature in row["features"]
        }
        out.append((row, measured))
    return out
