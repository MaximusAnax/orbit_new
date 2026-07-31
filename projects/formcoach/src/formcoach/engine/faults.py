"""FR-9 — evaluate every :class:`FaultRule` of a form profile against every rep.

The output is a *complete matrix*: one :class:`FaultFinding` per rule per rep,
with status ``ok``, ``fault`` or ``not_assessed``.  Nothing is silently
omitted — the evals consume exactly this matrix (M1, M4b, M7), and an engine
that dropped inconvenient rows would be scoring itself.

A rule is ``not_assessed`` when, in precedence order:

1. the resolved view is not one of ``rule.views`` (``view_mismatch``);
2. the analysis is a photo and the rule needs several frames
   (``needs_multi_frame``);
3. the analysis is a photo and the rule samples a phase the caller did not
   declare (``phase_not_shown``);
4. a keypoint the feature needs is below confidence 0.3 at the sampled frames,
   or the geometry is degenerate (``keypoints_not_visible``).

**Sampling rule for multi-frame features (implementation choice).** FR-9 says a
rule is not assessed when any needed keypoint is invisible "at the sampled
phase frame(s)".  For a single-frame phase that is literal.  For a window phase
(``ascent_early``, ``whole_rep``) a strictly literal reading would refuse the
whole window because of one dropped frame, which under any realistic confidence
model would make multi-frame rules permanently unassessable.  Instead the
feature is computed over the window's *usable* frames and the rule is refused
only when fewer than half of them are usable (or fewer than three are, for
windows of three frames or more).
"""

from __future__ import annotations

from dataclasses import dataclass

from formcoach.engine.geometry import (
    FEATURES,
    MIN_CONF,
    MULTI_FRAME_FEATURES,
    FeatureContext,
    expand_keypoints,
)
from formcoach.models import (
    FaultFinding,
    FaultRule,
    FindingStatus,
    FormProfile,
    NotAssessedReason,
    Phase,
    PoseSequence,
    RepBoundary,
    RepDirection,
    View,
    round_half_up,
)

#: Fraction of the ascent that ``ascent_early`` samples (SCOPE fault catalog).
ASCENT_EARLY_FRAC = 0.4


@dataclass(frozen=True)
class Measurement:
    """A feature value plus the frame it was read at."""

    value: float
    frame: int


def ascent_span(rep: RepBoundary, direction: RepDirection) -> tuple[int, int]:
    """Frame range of the concentric (upward) portion of a rep."""
    if direction is RepDirection.DOWN_UP:
        return rep.extremum_frame, rep.end_frame
    return rep.start_frame, rep.extremum_frame


def phase_frames(rep: RepBoundary, direction: RepDirection, phase: Phase) -> list[int]:
    """The frame indices a phase samples, in ascending order."""
    if phase is Phase.BOTTOM:
        index = rep.extremum_frame if direction is RepDirection.DOWN_UP else rep.start_frame
        return [index]
    if phase is Phase.TOP:
        index = rep.end_frame if direction is RepDirection.DOWN_UP else rep.extremum_frame
        return [index]
    if phase is Phase.WHOLE_REP:
        return list(range(rep.start_frame, rep.end_frame + 1))
    lo, hi = ascent_span(rep, direction)
    if hi <= lo:
        return [lo]
    end = lo + max(1, round_half_up(ASCENT_EARLY_FRAC * (hi - lo)))
    return list(range(lo, min(end, hi) + 1))


def _usable_frames(sequence: PoseSequence, indices: list[int], keypoints: list[str]) -> list[int]:
    names = expand_keypoints(keypoints)
    usable: list[int] = []
    for i in indices:
        frame = sequence.frames[i]
        if all(
            (kp := frame.keypoints.get(name)) is not None and kp.conf >= MIN_CONF for name in names
        ):
            usable.append(i)
    return usable


def _window_is_readable(total: int, usable: int) -> bool:
    """At least half the window, and at least three frames when it has three."""
    if total == 0:
        return False
    if total < 3:
        return usable == total
    return usable >= 3 and usable * 2 >= total


def measure(
    sequence: PoseSequence,
    rule: FaultRule,
    rep: RepBoundary,
    direction: RepDirection,
    facing: float,
) -> Measurement | None:
    """Compute a rule's feature for one rep, or ``None`` if it is unreadable."""
    spec = FEATURES.get(rule.feature)
    if spec is None:
        raise KeyError(f"unknown feature {rule.feature!r}")
    ctx = FeatureContext(facing=facing)
    indices = phase_frames(rep, direction, rule.phase)
    usable = _usable_frames(sequence, indices, rule.feature_keypoints)

    if spec.kind == "frame":
        if len(usable) != len(indices) or not usable:
            return None
        frame_index = usable[-1]
        assert spec.frame_fn is not None
        value = spec.frame_fn(sequence.frames[frame_index], ctx)
        return None if value is None else Measurement(value=value, frame=frame_index)

    if not _window_is_readable(len(indices), len(usable)):
        return None
    assert spec.window_fn is not None
    result = spec.window_fn([sequence.frames[i] for i in usable], ctx)
    if result is None:
        return None
    value, offset = result
    return Measurement(value=value, frame=usable[min(offset, len(usable) - 1)])


def _not_assessed(rule: FaultRule, reason: NotAssessedReason) -> FaultFinding:
    return FaultFinding(
        fault_id=rule.fault_id,
        status=FindingStatus.NOT_ASSESSED,
        not_assessed_reason=reason,
        measured=None,
        threshold=rule.threshold,
        severity=rule.severity,
        frame=None,
        cue=None,
    )


def metric_keys(profile: FormProfile, rule: FaultRule) -> list[str]:
    """Keys a measured value is published under in ``RepAnalysis.metrics``.

    Always ``"<feature>@<phase>"``; additionally the bare ``"<feature>"`` when
    the profile uses that feature in exactly one rule.  Push-ups measure
    ``elbow_angle_deg`` at both the bottom and the top, so the bare key would
    otherwise be ambiguous.
    """
    keys = [f"{rule.feature}@{rule.phase.value}"]
    if sum(1 for r in profile.rules if r.feature == rule.feature) == 1:
        keys.append(rule.feature)
    return keys


def evaluate_rep(
    sequence: PoseSequence,
    profile: FormProfile,
    rep: RepBoundary,
    view: View,
    facing: float,
    *,
    is_photo: bool = False,
    declared_phase: Phase | None = None,
) -> tuple[list[FaultFinding], dict[str, float]]:
    """Evaluate every rule of ``profile`` for one rep.

    Returns the complete finding matrix for the rep together with the metric
    dictionary of every value that could be measured.
    """
    findings: list[FaultFinding] = []
    metrics: dict[str, float] = {}

    for rule in profile.rules:
        if view not in rule.views:
            findings.append(_not_assessed(rule, NotAssessedReason.VIEW_MISMATCH))
            continue
        if is_photo and (rule.feature in MULTI_FRAME_FEATURES or rule.phase is Phase.ASCENT_EARLY):
            findings.append(_not_assessed(rule, NotAssessedReason.NEEDS_MULTI_FRAME))
            continue
        if is_photo and rule.phase not in (declared_phase, Phase.WHOLE_REP):
            findings.append(_not_assessed(rule, NotAssessedReason.PHASE_NOT_SHOWN))
            continue

        measurement = measure(sequence, rule, rep, profile.direction, facing)
        if measurement is None:
            findings.append(_not_assessed(rule, NotAssessedReason.KEYPOINTS_NOT_VISIBLE))
            continue

        for key in metric_keys(profile, rule):
            metrics[key] = measurement.value
        is_fault = rule.violated_by(measurement.value)
        findings.append(
            FaultFinding(
                fault_id=rule.fault_id,
                status=FindingStatus.FAULT if is_fault else FindingStatus.OK,
                not_assessed_reason=None,
                measured=measurement.value,
                threshold=rule.threshold,
                severity=rule.severity,
                frame=measurement.frame,
                cue=rule.cue if is_fault else None,
            )
        )
    return findings, metrics
