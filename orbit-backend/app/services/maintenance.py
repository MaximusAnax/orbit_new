"""Deterministic maintenance cadence scorer (plan Phase 5 / ADR)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

ORBIT_DEFAULT_DAYS: dict[str | None, int] = {
    "inner": 21,
    "close": 14,
    "active": 10,
    "extended": 45,
    "outer": 120,
    None: 45,
}

CADENCE_LEXICON: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\bweekly\b", re.I), 7),
    (re.compile(r"\bbi-?weekly\b", re.I), 14),
    (re.compile(r"\bevery\s*2\s*weeks?\b", re.I), 14),
    (re.compile(r"\bmonthly\b", re.I), 30),
    (re.compile(r"\bquarterly\b", re.I), 90),
    (re.compile(r"\brarely\b|\binfrequent", re.I), 180),
    (re.compile(r"\bevery\s*(\d+)\s*days?\b", re.I), -1),  # special: group 1
]


def parse_desired_cadence(text: str | None) -> int | None:
    if not text:
        return None
    for pattern, days in CADENCE_LEXICON:
        m = pattern.search(text)
        if not m:
            continue
        if days == -1:
            return int(m.group(1))
        return days
    # bare number of days
    m = re.search(r"\b(\d+)\s*days?\b", text, re.I)
    if m:
        return int(m.group(1))
    return None


def target_days_for(orbit: str | None, desired_cadence: str | None) -> tuple[int, str]:
    parsed = parse_desired_cadence(desired_cadence)
    if parsed is not None:
        return parsed, "explicit_cadence"
    key = orbit if orbit in ORBIT_DEFAULT_DAYS else None
    return ORBIT_DEFAULT_DAYS[key], "orbit_default"


def _direction_boost(direction: str | None) -> tuple[float, str | None]:
    if not direction:
        return 0.0, None
    d = direction.lower()
    if any(x in d for x in ("grow closer", "want to be closer", "strengthen", "invest")):
        return 0.35, "grow_closer"
    if any(x in d for x in ("letting drift", "drift", "naturally stable", "stable", "no maintenance")):
        return -0.50, "drift_or_stable"
    return 0.0, None


@dataclass
class MaintenanceSuggestion:
    person_id: UUID
    person_name: str
    score: float
    target_days: int
    days_since_contact: int | None
    reasons: list[str]
    reason_codes: list[str]


def score_person(
    *,
    person_id: UUID,
    person_name: str,
    orbit: str | None,
    desired_cadence: str | None,
    direction: str | None,
    days_since_contact: int | None,
    open_task_descriptions: list[str],
    has_deferred_proposals: bool,
    now: datetime | None = None,
) -> MaintenanceSuggestion | None:
    """Score one person. Exclude if never contacted and no open tasks."""
    if days_since_contact is None and not open_task_descriptions:
        return None

    target, cadence_source = target_days_for(orbit, desired_cadence)
    reasons: list[str] = []
    codes: list[str] = []

    overdue_ratio = 0.0
    if days_since_contact is not None:
        overdue_ratio = days_since_contact / max(target, 1)
        if days_since_contact >= target:
            reasons.append(
                f"{orbit or 'extended'} orbit — no contact in {days_since_contact} days "
                f"(target {target}d via {cadence_source.replace('_', ' ')})"
            )
            codes.append("overdue")

    score = overdue_ratio
    boost, dir_code = _direction_boost(direction)
    if dir_code == "drift_or_stable":
        # Soften overdue pressure — resilient / intentional distance is not neglect
        score = overdue_ratio * 0.25 + boost
    else:
        score = overdue_ratio + boost

    if open_task_descriptions:
        # Context before contact: open threads dominate mild overdue
        score += 2.0
        reasons.insert(0, f"Open thread: {open_task_descriptions[0]}")
        codes.insert(0, "open_task")
    if dir_code and dir_code != "drift_or_stable":
        codes.append(dir_code)
        if boost > 0:
            reasons.append("You want to grow closer")
    elif dir_code == "drift_or_stable":
        codes.append(dir_code)
        reasons.append("Marked as stable / letting drift")
    if has_deferred_proposals:
        score += 0.25
        reasons.append("Deferred profile updates waiting")
        codes.append("deferred_proposals")

    if not reasons and days_since_contact is not None:
        reasons.append(f"Last contact {days_since_contact} days ago (target {target}d)")
        codes.append("approaching")

    has_open = "open_task" in codes
    has_overdue = "overdue" in codes
    if score < 0.9 and not has_open and not has_overdue:
        if "grow_closer" in codes and days_since_contact is not None and days_since_contact >= target * 0.7:
            codes.append("grow_closer_nudge")
        else:
            return None
    # Drift with only mild overdue may still appear but ranks low
    if dir_code == "drift_or_stable" and not has_open and overdue_ratio < 2.0:
        return None

    return MaintenanceSuggestion(
        person_id=person_id,
        person_name=person_name,
        score=round(score, 4),
        target_days=target,
        days_since_contact=days_since_contact,
        reasons=reasons,
        reason_codes=codes,
    )


def rank_people(rows: list[dict[str, Any]], limit: int = 10) -> list[MaintenanceSuggestion]:
    suggestions: list[MaintenanceSuggestion] = []
    for row in rows:
        s = score_person(
            person_id=row["person_id"] if isinstance(row["person_id"], UUID) else UUID(str(row["person_id"])),
            person_name=row["person_name"],
            orbit=row.get("orbit"),
            desired_cadence=row.get("desired_cadence"),
            direction=row.get("direction"),
            days_since_contact=row.get("days_since_contact"),
            open_task_descriptions=row.get("open_tasks") or [],
            has_deferred_proposals=bool(row.get("has_deferred_proposals")),
        )
        if s:
            suggestions.append(s)
    # Open threads first, then score — never pure longest-silence ranking
    suggestions.sort(
        key=lambda x: (1 if "open_task" in x.reason_codes else 0, x.score),
        reverse=True,
    )
    return suggestions[:limit]
