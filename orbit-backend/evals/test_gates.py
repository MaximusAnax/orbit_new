"""Eval gate tests — fail CI if metrics regress."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.maintenance import (
    ORBIT_DEFAULT_DAYS,
    parse_desired_cadence,
    rank_people,
    score_person,
    target_days_for,
)
from uuid import UUID


def test_eval_runner_passes():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "evals.run"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["passed"] is True


def test_orbit_defaults():
    assert ORBIT_DEFAULT_DAYS["inner"] == 21
    assert ORBIT_DEFAULT_DAYS["close"] == 14
    assert ORBIT_DEFAULT_DAYS["active"] == 10
    assert ORBIT_DEFAULT_DAYS["extended"] == 45
    assert ORBIT_DEFAULT_DAYS["outer"] == 120


def test_explicit_cadence_overrides_orbit():
    days, src = target_days_for("active", "monthly")
    assert days == 30
    assert src == "explicit_cadence"


def test_parse_cadence_lexicon():
    assert parse_desired_cadence("weekly") == 7
    assert parse_desired_cadence("biweekly") == 14
    assert parse_desired_cadence("every 10 days") == 10


def test_open_task_boosts_outer_over_inner_overdue():
    rows = [
        {
            "person_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "person_name": "Inner",
            "orbit": "inner",
            "desired_cadence": None,
            "direction": None,
            "days_since_contact": 25,
            "open_tasks": [],
            "has_deferred_proposals": False,
        },
        {
            "person_id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            "person_name": "OuterTask",
            "orbit": "outer",
            "desired_cadence": None,
            "direction": None,
            "days_since_contact": 3,
            "open_tasks": ["follow up"],
            "has_deferred_proposals": False,
        },
    ]
    ranked = rank_people(rows)
    assert ranked[0].person_name == "OuterTask"
    assert "open_task" in ranked[0].reason_codes


def test_drift_suppresses_relative_to_grow():
    grow = score_person(
        person_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        person_name="Grow",
        orbit="close",
        desired_cadence=None,
        direction="want to grow closer",
        days_since_contact=20,
        open_task_descriptions=[],
        has_deferred_proposals=False,
    )
    drift = score_person(
        person_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        person_name="Drift",
        orbit="close",
        desired_cadence=None,
        direction="letting drift",
        days_since_contact=20,
        open_task_descriptions=[],
        has_deferred_proposals=False,
    )
    assert grow is not None
    # drift may still surface if overdue but with lower score
    if drift:
        assert grow.score > drift.score
