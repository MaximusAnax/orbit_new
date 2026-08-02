"""Scripted, deterministic user behaviour for the simulations (EVALS.md section 4).

Every decision is a hash of (scenario, entity id, date) mapped into [0, 1) — no
``random`` anywhere, so a run is reproducible from the scenario file alone.

The persona never reads scheduler state other than the public ``stats`` output,
and it never sees an entry's planted latent quality except through the grade it
draws from it.  That separation is what makes M6 a measurement rather than a
tautology.
"""

from __future__ import annotations

import datetime as dt
import hashlib

#: Grade distribution per planted latent quality (EVALS.md section 4).
GRADE_MIX: dict[str, tuple[tuple[str, float], ...]] = {
    "gem": (("applied", 0.60), ("resonated", 0.35), ("flat", 0.05)),
    "decent": (("applied", 0.25), ("resonated", 0.50), ("flat", 0.25)),
    "dud": (("applied", 0.05), ("resonated", 0.15), ("flat", 0.80)),
}

#: How many materialized days after an archive-candidate first appears the
#: persona acts on it.
ARCHIVE_DELAY_DAYS = 7

REFLECTION_NOTES = (
    "did it before lunch",
    "wrote three lines about it",
    "nothing moved today",
    "used it in the standup",
    "read it twice, then acted",
)


def unit(*parts: object) -> float:
    """Deterministic value in [0, 1) keyed by ``parts``."""
    payload = "\x00".join(
        part.isoformat() if isinstance(part, dt.date) else str(part) for part in parts
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") / float(1 << 64)


def should_reflect(scenario: str, surfacing_id: str, p_reflect: float) -> bool:
    """Diligent personas reflect on most cards; sporadic ones on some."""
    return unit("reflect", scenario, surfacing_id) < p_reflect


def grade_for(scenario: str, surfacing_id: str, quality: str) -> str:
    """Draw a grade from the entry's planted latent quality."""
    roll = unit("grade", scenario, surfacing_id)
    cumulative = 0.0
    mix = GRADE_MIX[quality]
    for grade, share in mix:
        cumulative += share
        if roll < cumulative:
            return grade
    return mix[-1][0]


def reflection_note(scenario: str, surfacing_id: str) -> str | None:
    """Two thirds of reflections carry a note; the text is never parsed."""
    roll = unit("note", scenario, surfacing_id)
    if roll < 0.34:
        return None
    return REFLECTION_NOTES[int(roll * len(REFLECTION_NOTES)) % len(REFLECTION_NOTES)]


def should_archive(scenario: str, entry_id: str, on_date: dt.date, probability: float) -> bool:
    """Half of the entries the tool flags as archive candidates actually get archived."""
    return unit("archive", scenario, entry_id, on_date) < probability


def should_draw(scenario: str, on_date: dt.date, probability: float) -> bool:
    """Whether the user pulls an extra card on this materialized date."""
    return probability > 0 and unit("draw", scenario, on_date) < probability


def draw_filter(scenario: str, date_index: int, themes: list[str], collections: list[str]):
    """Alternate a theme filter and a collection filter by date-index parity.

    Returns ``(theme_id, collection_id)``; at most one is set.
    """
    if date_index % 2 == 0 and themes:
        return themes[
            int(unit("draw-theme", scenario, date_index) * len(themes)) % len(themes)
        ], None
    if collections:
        index = int(unit("draw-collection", scenario, date_index) * len(collections))
        return None, collections[index % len(collections)]
    return None, None
