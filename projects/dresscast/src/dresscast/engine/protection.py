"""Rain, wind, UV and cold-extremity rules; ``S_protect``; accessory attachment.

Implements SCOPE.md FR-9 in full: the intensity/adequacy table and the umbrella
wind cap (the hard half, HC-6), the exposure-weighted protection score (the
soft half), and the deterministic post-assembly accessory attachment that makes
the cold-extremity and UV rules real rather than decorative.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from dresscast.engine.comfort import DayContext, HourContext
from dresscast.engine.models import (
    ACCESSORY_PRIORITY,
    MAX_ACCESSORIES,
    PENALTY_RAIN_UNCOVERED,
    PENALTY_SOFT_RAIN,
    PENALTY_WIND_PARTIAL,
    PENALTY_WIND_UNBLOCKED,
    AccessoryAttachment,
    CoreOutfit,
    Garment,
    LayerConfig,
)

#: What the soft-rain rule demands: at least light-class cover (FR-9).
SOFT_RAIN_COVER = 1


def rain_hours(ctx: DayContext) -> list[HourContext]:
    """Wear-window hours with ``precip_prob ≥ 0.5`` — HC-6's hard rain hours."""
    return [h for h in ctx.hours if h.hard_rain]


def soft_rain_hours(ctx: DayContext) -> list[HourContext]:
    """Wear-window hours with ``0.3 ≤ precip_prob < 0.5``."""
    return [h for h in ctx.hours if h.soft_rain]


def required_cover(hc: HourContext) -> int:
    """Worn waterproofness this hour demands: HC-6's class, or light when soft."""
    if hc.hard_rain:
        return hc.needed_cover
    if hc.soft_rain:
        return SOFT_RAIN_COVER
    return 0


def cover_adequate(cfg: LayerConfig, hc: HourContext, umbrella: bool) -> bool:
    """Is this configuration adequately covered for this hour (FR-9)?

    An umbrella counts for light and moderate intensity below 35 km/h of wind
    and never for heavy rain — :attr:`HourContext.umbrella_ok` encodes both.
    """
    need = required_cover(hc)
    if need == 0:
        return True
    if cfg.cover >= need:
        return True
    return hc.umbrella_ok and umbrella


@dataclass(frozen=True, slots=True)
class ProtectionEval:
    """``S_protect`` plus the triggers FR-15 renders and FR-9 attaches on."""

    score: float
    hour_scores: tuple[float, ...]
    umbrella_hours: tuple[int, ...]  # positions whose adequacy needs the umbrella
    uncovered_rain_hours: tuple[int, ...]
    uncovered_soft_hours: tuple[int, ...]
    wind_penalty_hours: tuple[int, ...]

    @property
    def wind_penalty_fired(self) -> bool:
        return bool(self.wind_penalty_hours)


def protect_score(
    configs: Sequence[LayerConfig],
    chosen: Sequence[int],
    ctx: DayContext,
    umbrella: bool,
) -> ProtectionEval:
    """FR-9's exposure-weighted protection score over wear-window hours."""
    if not any(hc.rain_any or hc.windy for hc in ctx.hours):
        # Nothing can fire: every hour scores a clean 1.0 (FR-9's table).
        return ProtectionEval(
            score=1.0,
            hour_scores=tuple(1.0 for _ in ctx.hours),
            umbrella_hours=(),
            uncovered_rain_hours=(),
            uncovered_soft_hours=(),
            wind_penalty_hours=(),
        )
    hour_scores: list[float] = []
    umbrella_hours: list[int] = []
    uncovered_rain: list[int] = []
    uncovered_soft: list[int] = []
    windy_hours: list[int] = []
    weighted = 0.0
    for pos, hc in enumerate(ctx.hours):
        cfg = configs[chosen[pos]]
        p = 1.0
        need = required_cover(hc)
        worn_ok = need == 0 or cfg.cover >= need
        umbrella_ok = (not worn_ok) and hc.umbrella_ok and umbrella
        if umbrella_ok:
            umbrella_hours.append(pos)
        if not (worn_ok or umbrella_ok):
            if hc.hard_rain:
                p -= PENALTY_RAIN_UNCOVERED
                uncovered_rain.append(pos)
            elif hc.soft_rain:
                p -= PENALTY_SOFT_RAIN
                uncovered_soft.append(pos)
        if hc.windy:
            if cfg.windproofness == 0:
                p -= PENALTY_WIND_UNBLOCKED
                windy_hours.append(pos)
            elif cfg.windproofness == 1:
                p -= PENALTY_WIND_PARTIAL
                windy_hours.append(pos)
        p = min(1.0, max(0.0, p))
        hour_scores.append(p)
        weighted += p * hc.weight
    return ProtectionEval(
        score=weighted / ctx.weight_sum,
        hour_scores=tuple(hour_scores),
        umbrella_hours=tuple(umbrella_hours),
        uncovered_rain_hours=tuple(uncovered_rain),
        uncovered_soft_hours=tuple(uncovered_soft),
        wind_penalty_hours=tuple(windy_hours),
    )


# --------------------------------------------------------------------------
# Accessory attachment (FR-9, deterministic, post-assembly)
# --------------------------------------------------------------------------


def _formality_window(core: CoreOutfit) -> tuple[int, int]:
    """The formality interval an accessory may occupy without breaking HC-4."""
    values = [g.formality for g in core.garments()]
    return max(values) - 1, min(values) + 1


def eligible_accessories(
    accessories: Sequence[Garment], core: CoreOutfit, occasion: str, cls: str
) -> list[Garment]:
    """Clean, non-retired, occasion-matching, HC-4-compatible accessories."""
    lo, hi = _formality_window(core)
    return [
        g
        for g in accessories
        if g.accessory_class == cls
        and g.status == "clean"
        and occasion in g.occasions
        and lo <= g.formality <= hi
    ]


def _pick(candidates: Sequence[Garment], core: CoreOutfit) -> Garment | None:
    """FR-9's ``min by (|formality - median core formality|, garment_id)``."""
    if not candidates:
        return None
    centre = median(g.formality for g in core.garments())
    return min(candidates, key=lambda g: (abs(g.formality - centre), g.id))


def select_umbrella(
    accessories: Sequence[Garment], core: CoreOutfit, occasion: str
) -> Garment | None:
    """The one umbrella HC-6 and ``S_protect`` may count on for this outfit."""
    return _pick(eligible_accessories(accessories, core, occasion, "umbrella"), core)


@dataclass(frozen=True, slots=True)
class AccessoryPlan:
    """The attached accessories plus the classes that fired with nothing to fill."""

    attachments: tuple[AccessoryAttachment, ...]
    gaps: tuple[tuple[str, str], ...]  # (class, trigger)

    @property
    def classes(self) -> frozenset[str]:
        return frozenset(a.accessory_class for a in self.attachments)


def triggered_classes(ctx: DayContext, protection: ProtectionEval) -> list[tuple[str, str]]:
    """FR-9's trigger table, in priority order: ``[(class, trigger), …]``."""
    fired: list[tuple[str, str]] = []
    if protection.umbrella_hours:
        hours = [ctx.hours[p].weather.hour for p in protection.umbrella_hours]
        fired.append(
            (
                "umbrella",
                f"rain cover at {min(hours):02d}:00-{max(hours):02d}:00 depends on it",
            )
        )
    if ctx.cold_extremities:
        trigger = f"minimum feels-like {ctx.bare_min:.1f}°C is below 5°C"
        fired.extend([("gloves", trigger), ("hat", trigger), ("scarf", trigger)])
    if ctx.uv_advisory:
        fired.append(("sunglasses", f"UV index peaks at {ctx.uv_max:.1f}"))
    order = {c: i for i, c in enumerate(ACCESSORY_PRIORITY)}
    fired.sort(key=lambda item: order[item[0]])
    return fired


def attach_accessories(
    core: CoreOutfit,
    accessories: Sequence[Garment],
    ctx: DayContext,
    protection: ProtectionEval,
    occasion: str,
) -> AccessoryPlan:
    """FR-9: fill the triggered accessory classes, at most four, by rule.

    Attachment never changes ``Icl``, never changes ranking and never enters
    ``S_color``/``S_style``/``S_variety``; it runs after selection.  The chosen
    garments are placed in ``accessory_1..4`` in ascending garment-id order,
    which is what makes FR-19's byte-identity guarantee hold.
    """
    attached: list[tuple[Garment, str, str]] = []
    gaps: list[tuple[str, str]] = []
    for cls, trigger in triggered_classes(ctx, protection):
        if len(attached) >= MAX_ACCESSORIES:
            break
        pick = _pick(eligible_accessories(accessories, core, occasion, cls), core)
        if pick is None:
            gaps.append((cls, trigger))
        else:
            attached.append((pick, cls, trigger))
    attached.sort(key=lambda item: item[0].id)
    return AccessoryPlan(
        attachments=tuple(
            AccessoryAttachment(garment_id=g.id, accessory_class=cls, trigger=trigger)
            for g, cls, trigger in attached
        ),
        gaps=tuple(gaps),
    )
