"""Presentation helpers for the CLI (SCOPE.md FR-18).

Everything here is formatting only: no business rule, no recomputation of an
engine quantity.  °F is a display conversion applied at this edge (D15) — the
engine and the store are °C throughout.
"""

from __future__ import annotations

from collections.abc import Sequence

from dresscast.engine.explain import render_plan_table
from dresscast.engine.models import (
    ARCHETYPE_NAMES,
    DayBrief,
    Garment,
    Recommendation,
    ScoredOutfit,
    WearLog,
)


def to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def temp(value: float, units: str) -> str:
    """Render a temperature in the requested display units."""
    if units == "f":
        return f"{to_f(value):.1f}°F"
    return f"{value:.1f}°C"


def garment_line(g: Garment) -> str:
    colors = "/".join(c.name for c in g.colors)
    flags = []
    if g.waterproofness:
        flags.append(f"wp{g.waterproofness}")
    if g.windproofness:
        flags.append(f"wind{g.windproofness}")
    tail = (" " + ",".join(flags)) if flags else ""
    return (
        f"{g.name:<28} {g.category:<20} {g.layer_role:<9} clo {g.clo:4.2f} "
        f"f{g.formality} {g.status:<10} {g.wears_since_wash}/{g.wears_before_laundry} "
        f"{colors}{tail}"
    )


def garment_detail(g: Garment, units: str = "c") -> list[str]:
    rows = [
        f"{g.name}  ({g.id})",
        f"  category    {g.category}   layer role {g.layer_role}"
        + (f"   accessory class {g.accessory_class}" if g.accessory_class else ""),
        f"  clo         {g.clo:.2f}   formality {g.formality}   "
        f"waterproofness {g.waterproofness}   windproofness {g.windproofness}",
        f"  colors      {', '.join(f'{c.name}({c.role})' for c in g.colors)}",
        f"  occasions   {', '.join(g.occasions) or '-'}",
        f"  style tags  {', '.join(g.style_tags) or '-'}",
        f"  laundry     {g.status}, {g.wears_since_wash} of {g.wears_before_laundry} wears",
        f"  overridden  {', '.join(g.overridden_fields) or '-'}",
    ]
    if g.photo_path:
        rows.append(f"  photo       {g.photo_path}")
    if g.notes:
        rows.append(f"  notes       {g.notes}")
    return rows


def brief_lines(brief: DayBrief, units: str = "c") -> list[str]:
    rows = [
        f"Day brief for {brief.date}  (wear window {brief.wear_window[0]:02d}:00-"
        f"{brief.wear_window[1]:02d}:00, met {brief.met})",
        f"  feels like {temp(brief.bare_feels_min, units)} to "
        f"{temp(brief.bare_feels_max, units)}; the day asks for "
        f"{brief.required_clo_min:.2f}-{brief.required_clo_max:.2f} clo",
        f"  layer archetypes: {', '.join(brief.archetype_range)}",
        "",
        f"  {'hour':>5} {'feels':>9} {'req clo':>8}  {'archetype':<28} rain",
    ]
    for h in brief.hours:
        rain = "-" if h.rain_cover_class == "none" else h.rain_cover_class
        if h.rain_required:
            rain += " (cover required)"
        rows.append(
            f"  {h.hour:02d}:00 {temp(h.bare_feels_c, units):>9} {h.required_clo:8.2f}  "
            f"{h.archetype:<28} {rain}"
        )
    if brief.advisories:
        rows.append("")
        rows.extend(f"  ! {a.text}" for a in brief.advisories)
    else:
        rows.append("")
        rows.append("  no advisories (no cold extremities, no strong wind, no high UV)")
    return rows


def archetype_legend() -> str:
    return " < ".join(ARCHETYPE_NAMES)


def outfit_lines(
    outfit: ScoredOutfit,
    names: dict[str, str],
    *,
    units: str = "c",
    plan: bool = False,
) -> list[str]:
    rows = [
        f"#{outfit.rank}  score {outfit.score_total:.4f}   "
        f"thermal {outfit.scores.thermal:.3f} protection {outfit.scores.protection:.3f} "
        f"color {outfit.scores.color:.3f} style {outfit.scores.style:.3f} "
        f"variety {outfit.scores.variety:.3f}"
    ]
    for item in outfit.items:
        rows.append(f"    {item.slot:<12} {names.get(item.garment_id, item.garment_id)}")
    for line in outfit.reasoning:
        rows.append(f"    - [{line.line_class}] {line.text}")
    for note in outfit.notes:
        rows.append(f"    ! note {note.model_dump()}")
    for compromise in outfit.compromises:
        rows.append(f"    ! compromise {compromise.rule}: {compromise.detail}")
    if plan:
        rows.append("")
        rows.extend("    " + row for row in render_plan_table(outfit.hour_plan))
    return rows


def recommendation_lines(
    rec: Recommendation,
    names: dict[str, str],
    *,
    units: str = "c",
    plan: bool = False,
) -> list[str]:
    rows = [
        f"Recommendation {rec.id or '(unsaved)'} for {rec.date} — occasion "
        f"{rec.params.occasion}, k={rec.params.k}, met {rec.params.met}, "
        f"window {rec.params.wear_window[0]:02d}:00-{rec.params.wear_window[1]:02d}:00",
    ]
    for note in rec.notes:
        rows.append(f"  ! note {note.model_dump()}")
    for compromise in rec.compromises:
        rows.append(f"  ! compromise {compromise.rule}: {compromise.detail}")
    for outfit in rec.outfits:
        rows.append("")
        rows.extend("  " + line for line in outfit_lines(outfit, names, units=units, plan=plan))
    return rows


def history_lines(logs: Sequence[WearLog], names: dict[str, str]) -> list[str]:
    if not logs:
        return ["No wear logs in this window."]
    rows = []
    for log in logs:
        worn = ", ".join(names.get(i.garment_id, i.garment_id) for i in log.items)
        rows.append(f"{log.date}  {log.source:<14} {log.id[:8]}  {worn}")
    return rows
