"""Seeded generator for the four simulation scenarios (EVALS.md section 4).

``uv run python almanac/evals/fixtures/generate.py`` rewrites ``scenarios.json``
and ``expected.json``.  Both outputs are committed; regenerating them is a
reviewed change, because every gate in EVALS.md section 5 is derived from the
scenario sizes.

Entry texts are *invented* one-liners: content is irrelevant to H1 (scheduling),
and inventing them keeps the fixture licence-clean and small.  Themes are
assigned directly here, bypassing the FR-3 suggester, which is evaluated
separately on real quotes (M8).

The one place the generator does more than hash: latent quality over the
``captured_on <= day 90`` cohort is *stratified* to exactly 25 % gem / 55 %
decent / 20 % dud, so M6's cohort sizes are guaranteed rather than left to hash
luck (EVALS.md section 4).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

GENERATOR_SEED = 42
START = dt.date(2026, 1, 1)  # a Thursday
HERE = Path(__file__).resolve().parent

THEME_IDS = [
    "courage",
    "discipline_and_habit",
    "mortality_and_time",
    "gratitude",
    "honesty_and_integrity",
    "attention_and_presence",
    "relationships",
    "adversity_and_resilience",
    "creativity_and_craft",
    "humility",
    "purpose_and_ambition",
    "simplicity_and_frugality",
    "equanimity_and_anger",
    "generosity_and_service",
    "learning_and_growth",
    "decision_and_action",
]

VERBS = [
    "Answer",
    "Finish",
    "Name",
    "Close",
    "Start",
    "Cut",
    "Keep",
    "Refuse",
    "Measure",
    "Rehearse",
    "Return to",
    "Ask about",
    "Write down",
    "Put away",
    "Walk to",
    "Sit with",
]
OBJECTS = [
    "the hardest message",
    "the second draft",
    "the standing meeting",
    "the open tab",
    "the unpaid favour",
    "the half-read book",
    "the borrowed excuse",
    "the loudest worry",
    "the smallest promise",
    "the oldest grudge",
    "the unopened letter",
    "the untried route",
    "the quiet colleague",
    "the missing apology",
    "the extra chair",
    "the unfinished list",
    "the early alarm",
    "the empty calendar block",
    "the untouched notebook",
    "the difficult paragraph",
    "the unreturned call",
    "the parked decision",
    "the crowded shelf",
    "the long walk",
    "the plain meal",
]
TAILS = [
    "before the easy ones",
    "while the kettle boils",
    "and then stop",
    "without checking anything else",
    "in one sitting",
    "before the day opens",
    "with the door shut",
    "on paper, not on screen",
    "out loud, once",
    "and let that be enough",
    "before anyone asks",
    "at the same hour tomorrow",
    "and say why",
    "slowly, on purpose",
    "then put it down",
    "and tell one person",
    "without apologising",
]

QUALITIES = ("gem", "decent", "dud")
QUALITY_MIX = {"gem": 0.25, "decent": 0.55, "dud": 0.20}


def u(*parts: object) -> float:
    """Deterministic value in [0, 1) keyed by the generator seed and ``parts``."""
    payload = "\x00".join([str(GENERATOR_SEED), *(str(p) for p in parts)]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") / float(1 << 64)


def entry_text(index: int) -> str:
    """A distinct invented one-liner. The mapping is injective for index < 6800."""
    verb = VERBS[(index * 7) % len(VERBS)]
    obj = OBJECTS[(index * 11) % len(OBJECTS)]
    tail = TAILS[(index * 13) % len(TAILS)]
    return f"{verb} {obj} {tail}."


def themes_for(ref: str) -> list[str]:
    """One or two themes, hash-chosen; a tenth of entries carry none."""
    roll = u("themes", ref)
    if roll < 0.10:
        return []
    first = THEME_IDS[int(u("theme-a", ref) * len(THEME_IDS))]
    if roll < 0.55:
        return [first]
    second = THEME_IDS[int(u("theme-b", ref) * len(THEME_IDS))]
    return [first] if second == first else [first, second]


def stratified_qualities(count: int, key: str) -> list[str]:
    """Exactly ``round(share * count)`` of each quality, hash-shuffled."""
    counts = {q: int(math.floor(QUALITY_MIX[q] * count)) for q in QUALITIES}
    while sum(counts.values()) < count:
        counts["decent"] += 1
    pool = [q for q in QUALITIES for _ in range(counts[q])]
    return [q for _, q in sorted(enumerate(pool), key=lambda kv: u(key, "strat", kv[0]))]


def stream_days(days: int, per_cycle: int, cycle: int, offsets: list[int]) -> list[int]:
    """Fixed-offset capture stream: ``per_cycle`` captures every ``cycle`` days."""
    assert len(offsets) == per_cycle
    return [c * cycle + off for c in range(days // cycle + 1) for off in offsets if c * cycle + off < days]


def sporadic_calendar(days: int, miss_rate: float, max_gap: int) -> list[int]:
    """Mon/Wed/Sat with a deterministic miss, repaired so no gap exceeds ``max_gap``.

    The repair is what makes SCOPE.md's pinned guarantee provable on a sporadic
    calendar: M5's worst case is ``P_rescue + max_gap * n_pinned`` (EVALS.md
    section 4), so an unbounded gap would break the guarantee by construction.
    """
    scheduled = [d for d in range(days) if (START + dt.timedelta(days=d)).weekday() in (0, 2, 5)]
    kept: list[int] = []
    for position, day in enumerate(scheduled):
        following = scheduled[position + 1] if position + 1 < len(scheduled) else day + max_gap
        previous = kept[-1] if kept else day - 1
        skipping_is_safe = (following - previous) <= max_gap
        if u("calendar", day) < miss_rate and skipping_is_safe and kept:
            continue
        kept.append(day)
    return kept


def build_entries(
    scenario: str,
    initial: list[int],
    stream: list[int],
    start_index: int = 0,
) -> list[dict]:
    """Initial library (possibly back-dated) plus the ongoing capture stream."""
    rows: list[dict] = []
    for position, captured_day in enumerate([*initial, *stream]):
        ref = f"{scenario}-{position:03d}"
        rows.append(
            {
                "ref": ref,
                "text": entry_text(start_index + position),
                "themes": themes_for(ref),
                "captured_day": captured_day,
                "quality": "decent",
                "pinned": False,
            }
        )
    cohort = [row for row in rows if row["captured_day"] <= 90]
    for row, quality in zip(cohort, stratified_qualities(len(cohort), scenario), strict=True):
        row["quality"] = quality
    for row in rows:
        if row["captured_day"] > 90:
            roll = u("quality", row["ref"])
            row["quality"] = (
                "gem" if roll < 0.25 else ("decent" if roll < 0.80 else "dud")
            )
    return rows


def pin_initial(rows: list[dict], count: int, scenario: str) -> list[str]:
    """Pin ``count`` of the initial library, chosen by hash for spread."""
    initial = [row for row in rows if row["captured_day"] <= 0]
    ordered = sorted(initial, key=lambda row: u("pin", scenario, row["ref"]))
    chosen = [row["ref"] for row in ordered[:count]]
    for row in rows:
        row["pinned"] = row["ref"] in chosen
    return chosen


def scenario_s1() -> dict:
    initial = list(range(-12, 0))
    stream = stream_days(365, 3, 14, [1, 6, 10])
    rows = build_entries("s1", initial, stream)
    pinned = pin_initial(rows, 8, "s1")
    later = [row["ref"] for row in rows if row["captured_day"] > 0][:3]
    curation = [{"day": 120, "action": "pin", "ref": ref} for ref in later]
    curation += [{"day": 200, "action": "unpin", "ref": ref} for ref in pinned[:2]]
    ordered = sorted(rows, key=lambda row: (row["captured_day"], row["ref"]))
    members = [row["ref"] for row in ordered[:16]]
    create_day = max(0, max(row["captured_day"] for row in ordered[:16]))
    return {
        "id": "S1",
        "name": "steady",
        "purpose": "the daily user; primary for M2a/M3/M4/M5/M6",
        "days": 365,
        "batch_k": 1,
        "p_reflect": 0.8,
        "archive_probability": 0.5,
        "draw_probability": 0.06,
        "entries": rows,
        "curation": curation,
        "collections": [
            {"name": "Mornings", "entry_refs": members[:8], "create_day": create_day},
            {"name": "Hard conversations", "entry_refs": members[8:16], "create_day": create_day},
        ],
        "calendar": list(range(365)),
    }


def scenario_s2() -> dict:
    rows = build_entries("s2", [0] * 40, stream_days(365, 1, 7, [3]), start_index=500)
    pin_initial(rows, 4, "s2")
    return {
        "id": "S2",
        "name": "burst",
        "purpose": "bulk import drain; M2b, M3, M6",
        "days": 365,
        "batch_k": 1,
        "p_reflect": 0.8,
        "archive_probability": 0.5,
        "draw_probability": 0.0,
        "entries": rows,
        "curation": [],
        "collections": [],
        "calendar": list(range(365)),
    }


def scenario_s3() -> dict:
    rows = build_entries("s3", [0] * 6, [], start_index=1000)
    pin_initial(rows, 1, "s3")
    return {
        "id": "S3",
        "name": "tiny",
        "purpose": "relaxed-cooldown fallback",
        "days": 60,
        "batch_k": 1,
        "p_reflect": 0.8,
        "archive_probability": 0.5,
        "draw_probability": 0.0,
        "entries": rows,
        "curation": [],
        "collections": [],
        "calendar": list(range(60)),
    }


def scenario_s4() -> dict:
    initial = list(range(-12, 0))
    rows = build_entries("s4", initial, stream_days(365, 3, 35, [4, 15, 27]), start_index=1200)
    pin_initial(rows, 2, "s4")
    later = [row["ref"] for row in rows if row["captured_day"] > 0][:1]
    return {
        "id": "S4",
        "name": "sporadic",
        "purpose": "sporadic calendar; M4, M5",
        "days": 365,
        "batch_k": 1,
        "p_reflect": 0.4,
        "archive_probability": 0.5,
        "draw_probability": 0.0,
        "entries": rows,
        "curation": [{"day": 100, "action": "pin", "ref": ref} for ref in later],
        "collections": [],
        "calendar": sporadic_calendar(365, 0.12, 4),
    }


def build() -> dict:
    return {
        "generator_seed": GENERATOR_SEED,
        "start": START.isoformat(),
        "scenarios": [scenario_s1(), scenario_s2(), scenario_s3(), scenario_s4()],
    }


def anchors(document: dict) -> dict:
    """Generator-measured facts. Informational only — never asserted against."""
    out = {}
    for scenario in document["scenarios"]:
        rows = scenario["entries"]
        cohort = [r for r in rows if r["captured_day"] <= 90]
        day0 = [r for r in rows if r["captured_day"] <= 0]
        materialized = len(scenario["calendar"])
        pinned = sum(1 for r in rows if r["pinned"])
        rate = scenario["batch_k"] - pinned / 32
        density = materialized / scenario["days"]
        out[scenario["id"]] = {
            "entries": len(rows),
            "day0_cohort": len(day0),
            "stream": len(rows) - len(day0),
            "capture_rate_per_day": round((len(rows) - len(day0)) / scenario["days"], 4),
            "materialized_days": materialized,
            "materialized_density": round(density, 4),
            "max_calendar_gap": max(
                (b - a for a, b in zip(scenario["calendar"], scenario["calendar"][1:], strict=False)),
                default=1,
            ),
            "pinned": pinned,
            "m6_cohort": len(cohort),
            "m6_gems": sum(1 for r in cohort if r["quality"] == "gem"),
            "m6_duds": sum(1 for r in cohort if r["quality"] == "dud"),
            "l_bound": 90 + math.ceil(len(day0) / (rate * density)) + 10,
            "m5_worst_case_gap": 32
            + max(
                (b - a for a, b in zip(scenario["calendar"], scenario["calendar"][1:], strict=False)),
                default=1,
            )
            * max(pinned, 1),
        }
    return out


def main() -> None:
    document = build()
    (HERE / "scenarios.json").write_text(
        json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (HERE / "expected.json").write_text(
        json.dumps(anchors(document), indent=1) + "\n", encoding="utf-8"
    )
    for name, data in anchors(document).items():
        print(name, json.dumps(data))


if __name__ == "__main__":
    main()
