"""FR-10 / FR-15 — turn a pose sequence into a persisted-shaped form report.

This module is the orchestrator for the analysis half of the product: view
resolution (FR-7) → screening (FR-7) → rep segmentation (FR-8) → the complete
rule matrix (FR-9) → scoring and the prioritized correction list (FR-10), with
the single-frame variant of the same pipeline for photos (FR-15).

It stays pure: the adapter has already produced the :class:`PoseSequence`, and
``created_at`` is supplied by the caller.
"""

from __future__ import annotations

from formcoach.engine.faults import evaluate_rep
from formcoach.engine.geometry import facing_sign
from formcoach.engine.poseio import resolve_view, screen
from formcoach.engine.reps import segment_reps, smooth_sequence, smoothing_window
from formcoach.models import (
    SEVERITY_PENALTY,
    SEVERITY_RANK,
    AnalysisKind,
    AnalysisResult,
    AnalysisStatus,
    Correction,
    Datasets,
    DeclaredView,
    Exercise,
    FaultFinding,
    FindingStatus,
    FormAnalysis,
    FormProfile,
    Phase,
    PoseSequence,
    RepAnalysis,
    RepBoundary,
    View,
)

#: Score of a completed analysis that contains no reps at all — there are no
#: rep scores to average and no faults were observed, so nothing is deducted.
EMPTY_CLIP_SCORE = 100.0


class NotAnalyzableError(ValueError):
    """Raised when an exercise has no form profile (DATA_MODEL invariant)."""


def rep_score(findings: list[FaultFinding]) -> float:
    """FR-10 per-rep score: ``max(0, 100 - sum(penalties))``."""
    penalty = sum(SEVERITY_PENALTY[f.severity] for f in findings if f.status is FindingStatus.FAULT)
    return float(max(0, 100 - penalty))


def build_corrections(reps: list[RepAnalysis], profile: FormProfile) -> list[Correction]:
    """FR-10 prioritized, de-duplicated correction list.

    Ordered by severity rank descending, then occurrence count descending, then
    ``fault_id`` ascending, so the same faults always come back in the same
    order for the same analysis.
    """
    counts: dict[str, int] = {}
    for rep in reps:
        for finding in rep.findings:
            if finding.status is FindingStatus.FAULT:
                counts[finding.fault_id] = counts.get(finding.fault_id, 0) + 1
    corrections = [
        Correction(
            fault_id=fault_id,
            severity=profile.rule(fault_id).severity,
            occurrences=count,
            cue=profile.rule(fault_id).cue,
        )
        for fault_id, count in counts.items()
    ]
    corrections.sort(key=lambda c: (-SEVERITY_RANK[c.severity], -c.occurrences, c.fault_id))
    return corrections


def _check_profile(exercise: Exercise, profile: FormProfile) -> None:
    if exercise.form_profile_id is None:
        raise NotAnalyzableError(f"{exercise.id} has no form profile")
    if exercise.form_profile_id != profile.id or profile.exercise_id != exercise.id:
        raise NotAnalyzableError(f"form profile {profile.id} does not match exercise {exercise.id}")


def _rejected(
    *,
    exercise: Exercise,
    profile: FormProfile,
    sequence: PoseSequence,
    created_at: str,
    view: View,
    view_inferred: bool,
    reason: str,
    kind: AnalysisKind,
    declared_phase: Phase | None,
    frames_total: int,
    frames_valid: int,
) -> AnalysisResult:
    analysis = FormAnalysis(
        created_at=created_at,
        analysis_kind=kind,
        exercise_id=exercise.id,
        form_profile_id=profile.id,
        source_ref=sequence.source_ref,
        pose_source=sequence.pose_source,
        view=view,
        view_inferred=view_inferred,
        declared_phase=declared_phase,
        fps=None if kind is AnalysisKind.PHOTO else sequence.fps,
        frames_total=frames_total,
        frames_valid=frames_valid,
        status=AnalysisStatus.REJECTED,
        reject_reason=reason,
        rep_count=None,
        clip_score=None,
    )
    return AnalysisResult(analysis=analysis, reps=[], corrections=[])


def analyze_clip(
    sequence: PoseSequence,
    *,
    exercise: Exercise,
    profile: FormProfile,
    created_at: str,
    declared_view: DeclaredView | None = None,
) -> AnalysisResult:
    """FR-7/8/9/10 — analyze a multi-frame clip."""
    _check_profile(exercise, profile)
    resolution = resolve_view(sequence, declared_view)
    screening = screen(sequence, profile, resolution.view)
    if not screening.accepted:
        assert screening.reject_reason is not None
        return _rejected(
            exercise=exercise,
            profile=profile,
            sequence=sequence,
            created_at=created_at,
            view=resolution.view,
            view_inferred=resolution.view_inferred,
            reason=screening.reject_reason,
            kind=AnalysisKind.CLIP,
            declared_phase=None,
            frames_total=screening.frames_total,
            frames_valid=screening.frames_valid,
        )

    boundaries = segment_reps(sequence, profile)
    window = smoothing_window(sequence.fps, profile.smoothing_window_frac)
    measured = smooth_sequence(sequence, window)
    facing = facing_sign(measured.frames)

    reps = _build_reps(measured, profile, boundaries, resolution.view, facing)
    scores = [r.score for r in reps]
    analysis = FormAnalysis(
        created_at=created_at,
        analysis_kind=AnalysisKind.CLIP,
        exercise_id=exercise.id,
        form_profile_id=profile.id,
        source_ref=sequence.source_ref,
        pose_source=sequence.pose_source,
        view=resolution.view,
        view_inferred=resolution.view_inferred,
        declared_phase=None,
        fps=sequence.fps,
        frames_total=screening.frames_total,
        frames_valid=screening.frames_valid,
        status=AnalysisStatus.COMPLETED,
        reject_reason=None,
        rep_count=len(reps),
        clip_score=sum(scores) / len(scores) if scores else EMPTY_CLIP_SCORE,
    )
    return AnalysisResult(
        analysis=analysis, reps=reps, corrections=build_corrections(reps, profile)
    )


def analyze_photo(
    sequence: PoseSequence,
    *,
    exercise: Exercise,
    profile: FormProfile,
    created_at: str,
    phase: Phase,
    declared_view: DeclaredView | None = None,
) -> AnalysisResult:
    """FR-15 — analyze a single still frame at a caller-declared phase.

    Rep segmentation is skipped and one synthetic rep with all frame indices at
    0 is created; rules sampled at another phase report ``phase_not_shown`` and
    multi-frame rules report ``needs_multi_frame``.  Scoring and the correction
    list are exactly FR-10 over that one rep.
    """
    _check_profile(exercise, profile)
    if phase not in (Phase.BOTTOM, Phase.TOP):
        raise ValueError("photo analyses declare either the bottom or the top position")
    single = sequence.model_copy(update={"frames": sequence.frames[:1]})
    resolution = resolve_view(single, declared_view)
    screening = screen(single, profile, resolution.view)
    if not screening.accepted:
        assert screening.reject_reason is not None
        return _rejected(
            exercise=exercise,
            profile=profile,
            sequence=single,
            created_at=created_at,
            view=resolution.view,
            view_inferred=resolution.view_inferred,
            reason=screening.reject_reason,
            kind=AnalysisKind.PHOTO,
            declared_phase=phase,
            frames_total=screening.frames_total,
            frames_valid=screening.frames_valid,
        )

    facing = facing_sign(single.frames)
    boundary = RepBoundary(rep_index=0, start_frame=0, extremum_frame=0, end_frame=0)
    reps = _build_reps(
        single,
        profile,
        [boundary],
        resolution.view,
        facing,
        is_photo=True,
        declared_phase=phase,
    )
    analysis = FormAnalysis(
        created_at=created_at,
        analysis_kind=AnalysisKind.PHOTO,
        exercise_id=exercise.id,
        form_profile_id=profile.id,
        source_ref=single.source_ref,
        pose_source=single.pose_source,
        view=resolution.view,
        view_inferred=resolution.view_inferred,
        declared_phase=phase,
        fps=None,
        frames_total=screening.frames_total,
        frames_valid=screening.frames_valid,
        status=AnalysisStatus.COMPLETED,
        reject_reason=None,
        rep_count=1,
        clip_score=reps[0].score,
    )
    return AnalysisResult(
        analysis=analysis, reps=reps, corrections=build_corrections(reps, profile)
    )


def _build_reps(
    sequence: PoseSequence,
    profile: FormProfile,
    boundaries: list[RepBoundary],
    view: View,
    facing: float,
    *,
    is_photo: bool = False,
    declared_phase: Phase | None = None,
) -> list[RepAnalysis]:
    reps: list[RepAnalysis] = []
    for boundary in boundaries:
        findings, metrics = evaluate_rep(
            sequence,
            profile,
            boundary,
            view,
            facing,
            is_photo=is_photo,
            declared_phase=declared_phase,
        )
        reps.append(
            RepAnalysis(
                rep_index=boundary.rep_index,
                start_frame=boundary.start_frame,
                extremum_frame=boundary.extremum_frame,
                end_frame=boundary.end_frame,
                metrics=metrics,
                score=rep_score(findings),
                findings=findings,
            )
        )
    return reps


def profile_for(datasets: Datasets, exercise: Exercise) -> FormProfile:
    """Look up the form profile an exercise declares, raising if it has none."""
    if exercise.form_profile_id is None:
        raise NotAnalyzableError(f"{exercise.id} has no form profile")
    try:
        return datasets.form_profiles[exercise.form_profile_id]
    except KeyError as exc:  # pragma: no cover - dataset loading validates this
        raise NotAnalyzableError(f"missing form profile {exercise.form_profile_id}") from exc
