"""Pure domain logic for FormCoach.

Every function here is deterministic: time and randomness arrive as explicit
arguments (``as_of``/``created_at``, ``seed``), committed datasets arrive as
Pydantic models, and nothing reads the clock, the filesystem or the network.

Modules map one-to-one onto the FRs:

============================  ==========================================
:mod:`.programming`           FR-3 mesocycle generation
:mod:`.progression`           FR-6 decision ladder and load rounding
:mod:`.volume`                FR-4 effective-set attribution
:mod:`.poseio`                FR-7 view resolution and screening
:mod:`.reps`                  FR-8 signal conditioning and segmentation
:mod:`.geometry`              the measured features the rules compare
:mod:`.faults`                FR-9 rule evaluation
:mod:`.report`                FR-10/FR-15 scoring and correction lists
:mod:`.safety`                FR-13 gating, pain substitution, vocabulary
============================  ==========================================
"""

from formcoach.engine.faults import evaluate_rep
from formcoach.engine.geometry import FEATURES, facing_sign
from formcoach.engine.poseio import resolve_view, screen
from formcoach.engine.programming import generate_program, select_target_muscles, weekly_set_plan
from formcoach.engine.progression import next_prescription, round_to_increment
from formcoach.engine.report import analyze_clip, analyze_photo, build_corrections, rep_score
from formcoach.engine.reps import segment_reps
from formcoach.engine.safety import (
    contains_diagnosis_language,
    require_disclaimer_ack,
    resolve_for_pain,
)
from formcoach.engine.volume import weekly_volume_report

__all__ = [
    "FEATURES",
    "analyze_clip",
    "analyze_photo",
    "build_corrections",
    "contains_diagnosis_language",
    "evaluate_rep",
    "facing_sign",
    "generate_program",
    "next_prescription",
    "rep_score",
    "require_disclaimer_ack",
    "resolve_for_pain",
    "resolve_view",
    "round_to_increment",
    "screen",
    "segment_reps",
    "select_target_muscles",
    "weekly_set_plan",
    "weekly_volume_report",
]
