"""Deterministic reasoning lines (SCOPE.md FR-15).

Rendering is pure template substitution over engine-computed numbers: no free
text, no garment ``notes`` passthrough, identical inputs → identical strings.
The ten line classes are emitted **iff** their trigger fired, which is the
invariant EVALS.md M4(g) gates against an independent checker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from dresscast.engine.comfort import DayContext, ThermalEval, compress_plan
from dresscast.engine.models import (
    COVER_ROLES,
    Compromise,
    CoreOutfit,
    Garment,
    HourPlanEntry,
    LayerConfig,
    Note,
    PlanSegment,
    ReasonLine,
    RequestParams,
    WearHistory,
)
from dresscast.engine.palette import color_score, hue_families
from dresscast.engine.protection import AccessoryPlan, ProtectionEval, required_cover
from dresscast.engine.variety import least_fresh

#: FR-15's table order — the order reasoning entries are emitted in.
LINE_CLASSES: tuple[str, ...] = (
    "day_thermal",
    "layer_change",
    "wardrobe_limit",
    "rain",
    "wind",
    "cold_extremities",
    "uv",
    "palette",
    "variety",
    "compromise",
)


@dataclass(slots=True)
class ExplainInput:
    """Everything FR-15's templates read.  All of it is engine-computed."""

    core: CoreOutfit
    garments: dict[str, Garment]
    ctx: DayContext
    configs: Sequence[LayerConfig]
    thermal: ThermalEval
    protection: ProtectionEval
    plan: Sequence[HourPlanEntry]
    accessories: AccessoryPlan
    history: WearHistory
    params: RequestParams
    notes: list[Note] = field(default_factory=list)
    compromises: list[Compromise] = field(default_factory=list)


def _clock(hour: int) -> str:
    return f"{hour:02d}:00"


def _join(names: Sequence[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _slot_names(data: ExplainInput, slots: Sequence[str]) -> list[str]:
    lookup = dict(data.core.slot_items())
    return [lookup[s].name for s in slots if s in lookup]


def _runs(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive true positions, as ``(start, end)`` pairs."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


# --------------------------------------------------------------------------
# Line classes
# --------------------------------------------------------------------------


def _day_thermal(data: ExplainInput) -> list[ReasonLine]:
    ctx = data.ctx
    return [
        ReasonLine(
            line_class="day_thermal",
            text=(
                f"Feels like {ctx.bare_min:.2f}°C at {_clock(ctx.bare_min_hour)} and "
                f"{ctx.bare_max:.2f}°C at {_clock(ctx.bare_max_hour)} — the day asks "
                f"for {ctx.required_max:.2f} clo down to {ctx.required_min:.2f} clo."
            ),
        )
    ]


def _layer_changes(data: ExplainInput, segments: Sequence[PlanSegment]) -> list[ReasonLine]:
    lines: list[ReasonLine] = []
    for prev, cur in pairwise(segments):
        added = [s for s in cur.worn_slots if s not in prev.worn_slots]
        removed = [s for s in prev.worn_slots if s not in cur.worn_slots]
        parts: list[str] = []
        if removed:
            parts.append(f"shed the {_join(_slot_names(data, removed))}")
        if added:
            parts.append(f"put on the {_join(_slot_names(data, added))}")
        if not parts:
            continue
        text = f"{_clock(cur.start_hour)} — {'; '.join(parts)}."
        if cur.carried_slots:
            carried = _join(_slot_names(data, cur.carried_slots))
            text += f" You are carrying the {carried} from here on."
        lines.append(ReasonLine(line_class="layer_change", text=text))
    return lines


def _wardrobe_limits(data: ExplainInput) -> list[ReasonLine]:
    found: list[tuple[int, ReasonLine]] = []
    for kind, label, direction, extreme in (
        ("wardrobe_ceiling", "colder", "short", "warmest"),
        ("wardrobe_floor", "warmer", "over", "lightest"),
    ):
        flags = [entry.clamped == kind for entry in data.plan]
        for start, end in _runs(flags):
            window = data.plan[start : end + 1]
            worst = max(window, key=lambda e: abs(e.required_clo - e.target_clo))
            shortfall = abs(worst.required_clo - worst.target_clo)
            found.append(
                (
                    start,
                    ReasonLine(
                        line_class="wardrobe_limit",
                        text=(
                            f"{_clock(window[0].hour)}-{_clock(window[-1].hour)} is "
                            f"{label} than anything you own: the day asks for "
                            f"{worst.required_clo:.2f} clo and your {extreme} legal "
                            f"combination reaches {worst.target_clo:.2f} — you are "
                            f"{shortfall:.2f} clo {direction}."
                        ),
                    ),
                )
            )
    found.sort(key=lambda item: item[0])
    return [line for _, line in found]


def _cover_name(data: ExplainInput, entry: HourPlanEntry, need: int) -> str | None:
    lookup = dict(data.core.slot_items())
    worn = [
        lookup[s] for s in entry.worn_slots if s in lookup and lookup[s].layer_role in COVER_ROLES
    ]
    adequate = [g for g in worn if g.waterproofness >= need]
    if adequate:
        return max(adequate, key=lambda g: (g.waterproofness, g.id)).name
    return None


def _rain(data: ExplainInput) -> list[ReasonLine]:
    lines: list[ReasonLine] = []
    flags = [hc.rain_any for hc in data.ctx.hours]
    umbrella_attached = "umbrella" in data.accessories.classes
    for start, end in _runs(flags):
        hours = data.ctx.hours[start : end + 1]
        window = data.plan[start : end + 1]
        peak = max(hours, key=lambda hc: (hc.weather.precip_mmh, -hc.index))
        prob = max(hc.weather.precip_prob for hc in hours)
        need = max(required_cover(hc) for hc in hours)
        cover = None
        for entry, hc in zip(window, hours, strict=True):
            cover = _cover_name(data, entry, required_cover(hc))
            if cover is not None:
                break
        if cover is not None:
            how = f"covered by the {cover}"
        elif umbrella_attached:
            how = "covered by your umbrella"
        else:
            how = "nothing you are wearing covers it"
        lines.append(
            ReasonLine(
                line_class="rain",
                text=(
                    f"{_clock(hours[0].weather.hour)}-{_clock(hours[-1].weather.hour)}: "
                    f"{peak.intensity} rain up to {peak.weather.precip_mmh:.1f} mm/h at "
                    f"{round(prob * 100)}% probability (needs waterproofness "
                    f"{need}) — {how}."
                ),
            )
        )
    return lines


def _wind(data: ExplainInput) -> list[ReasonLine]:
    if not data.protection.wind_penalty_fired:
        return []
    pos = min(
        data.protection.wind_penalty_hours,
        key=lambda p: (data.plan[p].protect_score, p),
    )
    config = data.configs[data.thermal.chosen[pos]]
    if config.windproofness == 0:
        blocking = "nothing you are wearing blocks it"
    else:
        blocking = "your outer layer only partly blocks it"
    ctx = data.ctx
    return [
        ReasonLine(
            line_class="wind",
            text=(
                f"Wind peaks at {ctx.wind_max:.0f} km/h at {_clock(ctx.wind_max_hour)} — "
                f"{blocking}."
            ),
        )
    ]


def _cold_extremities(data: ExplainInput) -> list[ReasonLine]:
    ctx = data.ctx
    if not ctx.cold_extremities:
        return []
    cold_classes = ("gloves", "hat", "scarf")
    taken = [
        data.garments[a.garment_id].name
        for a in data.accessories.attachments
        if a.accessory_class in cold_classes
    ]
    missing = [cls for cls, _ in data.accessories.gaps if cls in cold_classes]
    if taken:
        tail = f"taking the {_join(sorted(taken))}"
        if missing:
            tail += f"; no {_join(sorted(missing))} in the wardrobe"
    elif missing:
        tail = f"no {_join(sorted(missing))} in the wardrobe"
    else:  # pragma: no cover - one of the two branches always holds
        tail = "no cold-weather accessories triggered"
    return [
        ReasonLine(
            line_class="cold_extremities",
            text=(f"{_clock(ctx.bare_min_hour)} feels like {ctx.bare_min:.2f}°C — {tail}."),
        )
    ]


def _uv(data: ExplainInput) -> list[ReasonLine]:
    ctx = data.ctx
    if not ctx.uv_advisory:
        return []
    worn = [
        data.garments[a.garment_id].name
        for a in data.accessories.attachments
        if a.accessory_class == "sunglasses"
    ]
    tail = f"wearing the {_join(worn)}" if worn else "no sunglasses in the wardrobe"
    return [
        ReasonLine(
            line_class="uv",
            text=(f"UV index peaks at {ctx.uv_max:.1f} at {_clock(ctx.uv_max_hour)} — {tail}."),
        )
    ]


def _palette(data: ExplainInput) -> list[ReasonLine]:
    garments = data.core.garments()
    mains = [g.main_color for g in garments]
    neutrals = [c.name for c in mains if c.neutral]
    accents = [c.name for c in mains if not c.neutral]
    families = len(hue_families(garments))
    score = color_score(garments)
    if accents:
        summary = (
            f"{len(neutrals)} neutral and {len(accents)} accent colour"
            f"{'s' if len(accents) != 1 else ''} ({_join(sorted(accents))})"
        )
    else:
        summary = f"all-neutral palette ({_join(sorted(neutrals))})"
    text = f"Palette: {summary} — harmony {score:.2f}."
    if families > 3:
        text += f" {families} distinct hue families is above the three-colour rule."
    return [ReasonLine(line_class="palette", text=text)]


def _variety(data: ExplainInput) -> list[ReasonLine]:
    freshest = least_fresh(data.core.core_ids(), data.history, data.params.date)
    if freshest is None:
        text = "Nothing in this outfit has been worn before — fully fresh."
    else:
        gid, days = freshest
        name = data.garments[gid].name if gid in data.garments else gid
        when = "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")
        text = f"Least-fresh item: the {name}, last worn {when}."
    return [ReasonLine(line_class="variety", text=text)]


def _compromises(data: ExplainInput) -> list[ReasonLine]:
    lines = [
        ReasonLine(
            line_class="compromise",
            text=f"{c.rule} relaxed — {c.detail}.",
        )
        for c in data.compromises
    ]
    for note in data.notes:
        if note.kind != "partial_k":
            continue
        payload = note.model_dump()
        lines.append(
            ReasonLine(
                line_class="compromise",
                text=(
                    f"Only {payload.get('n')} of {payload.get('k')} outfits could be "
                    f"assembled: {payload.get('reason')}."
                ),
            )
        )
    return lines


def build_reasoning(data: ExplainInput) -> list[ReasonLine]:
    """FR-15: the outfit's ordered, classified reasoning entries."""
    segments = compress_plan(data.plan)
    lines: list[ReasonLine] = []
    lines.extend(_day_thermal(data))
    lines.extend(_layer_changes(data, segments))
    lines.extend(_wardrobe_limits(data))
    lines.extend(_rain(data))
    lines.extend(_wind(data))
    lines.extend(_cold_extremities(data))
    lines.extend(_uv(data))
    lines.extend(_palette(data))
    lines.extend(_variety(data))
    lines.extend(_compromises(data))
    return lines


def render_plan_table(plan: Sequence[HourPlanEntry]) -> list[str]:
    """A compact, deterministic textual rendering of the hourly plan."""
    header = (
        f"{'hour':>5} {'feels':>7} {'req':>6} {'target':>7} {'Icl':>6} "
        f"{'dev':>7} {'score':>6}  worn"
    )
    rows = [header]
    for e in plan:
        rows.append(
            f"{_clock(e.hour):>5} {e.feels_c:7.2f} {e.required_clo:6.2f} "
            f"{e.target_clo:7.2f} {e.ensemble_clo:6.2f} {e.deviation:7.2f} "
            f"{e.hour_score:6.2f}  {'+'.join(e.worn_slots)}"
        )
    return rows
