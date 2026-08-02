"""The independent rule checker (EVALS.md §2, §3 M4/M9/M10).

Written directly from SCOPE.md, this module deliberately imports **nothing**
from ``dresscast.engine`` except ``models`` (the entity definitions and the
named constants).  It re-implements, from scratch:

* feels-like with FR-5's ramps and the bare/config split,
* ``required_clo`` (FR-6.1) and the ensemble regression ``Icl`` (FR-7/D2),
* FR-6.2/D17's achievable band,
* all five component scorers (``S_thermal``, ``S_protect``, ``S_color``,
  ``S_style``, ``S_variety``),
* HC-1…HC-8, FR-9's accessory attachment, and FR-15's trigger set.

A bug shared with the engine would have to be written twice.  Where a formula
is quoted verbatim from the spec the two implementations necessarily agree —
that is the point: the checker pins the *spec*, and M1 pins the spec against
published charts.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date as date_cls

from dresscast.engine.models import (
    ACCESSORY_PRIORITY,
    COLD_EXTREMITY_C,
    COMFORT_BAND_CLO,
    COVER_REQUIRED,
    EXPOSURE_BASE,
    EXPOSURE_COMMUTE,
    HUE_FAMILY_DEGREES,
    HUE_FAMILY_PENALTY,
    HUE_ZONES,
    INTENSITY_LIGHT_MAX_MMH,
    INTENSITY_MODERATE_MAX_MMH,
    LEG_BASE_MAX_BARE_C,
    MAX_ACCESSORIES,
    MAX_HUE_FAMILIES,
    MAX_MIDS,
    PENALTY_RAIN_UNCOVERED,
    PENALTY_SOFT_RAIN,
    PENALTY_WIND_PARTIAL,
    PENALTY_WIND_UNBLOCKED,
    POP_HARD,
    POP_SOFT,
    STYLE_FORMALITY_WEIGHT,
    STYLE_SPREAD_STEP,
    STYLE_TAG_WEIGHT,
    THERMAL_MEAN_WEIGHT,
    THERMAL_WORST_WEIGHT,
    UMBRELLA_MAX_WIND_KMH,
    UV_ADVISORY_INDEX,
    UV_WINDOW,
    VARIETY_HALF_LIFE_DAYS,
    WIND_PENALTY_KMH,
    DayForecast,
    Garment,
    HourlyWeather,
    RequestParams,
    WearHistory,
)

# The slot order that defines "outermost worn layer" (SCOPE.md D6).
UPPER_ORDER: tuple[str, ...] = ("base", "mid_1", "mid_2", "outer")
COVER_ROLES = frozenset({"base", "full_body", "mid", "outer"})
CORE_ROLES = frozenset({"base", "mid", "outer", "bottom", "leg_base", "full_body", "footwear"})
CORE_SLOTS = ("base", "mid_1", "mid_2", "outer", "bottom", "leg_base", "footwear")
ACCESSORY_SLOTS = ("accessory_1", "accessory_2", "accessory_3", "accessory_4")

# --------------------------------------------------------------------------
# Physics, re-derived
# --------------------------------------------------------------------------


def wind_chill(temp_c: float, wind_kmh: float) -> float:
    v = wind_kmh**0.16
    return 13.12 + 0.6215 * temp_c - 11.37 * v + 0.3965 * temp_c * v


def apparent_temperature(temp_c: float, humidity: float, wind_kmh: float) -> float:
    e = (humidity / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return temp_c + 0.33 * e - 0.70 * (wind_kmh / 3.6) - 4.00


def feels(temp_c: float, wind_kmh: float, humidity: float) -> float:
    out = temp_c
    if temp_c <= 10.0:
        cold = 1.0
    elif temp_c >= 14.0:
        cold = 0.0
    else:
        cold = (14.0 - temp_c) / 4.0
    if cold > 0.0 and wind_kmh > 4.8:
        out += cold * (wind_chill(temp_c, wind_kmh) - temp_c)
    if temp_c <= 24.0:
        heat = 0.0
    elif temp_c >= 26.0:
        heat = 1.0
    else:
        heat = (temp_c - 24.0) / 2.0
    if heat > 0.0:
        out += heat * (apparent_temperature(temp_c, humidity, wind_kmh) - temp_c)
    return out


def attenuation(windproofness: int) -> float:
    return (1.0, 0.6, 0.3)[windproofness]


def required_clo(feels_c: float, met: float) -> float:
    return min(4.5, max(0.0, (34.0 - feels_c) / (7.66 * met) - 0.7))


def icl(clos: Iterable[float]) -> float:
    values = list(clos)
    if not values:
        return 0.0
    return 0.835 * sum(values) + 0.161


def hour_score(deviation: float) -> float:
    excess = abs(deviation) - COMFORT_BAND_CLO
    if excess <= 0.0:
        return 1.0
    return max(0.0, 1.0 - excess / (1.0 - COMFORT_BAND_CLO))


def intensity_class(mmh: float) -> str:
    if mmh < INTENSITY_LIGHT_MAX_MMH:
        return "light"
    if mmh <= INTENSITY_MODERATE_MAX_MMH:
        return "moderate"
    return "heavy"


# --------------------------------------------------------------------------
# Per-hour view of a request
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hour:
    index: int
    weather: HourlyWeather
    weight: float
    bare: float
    hard_rain: bool
    soft_rain: bool
    intensity: str
    need_hard: int
    need_soft: int
    umbrella_ok: bool
    windy: bool

    @property
    def rain_any(self) -> bool:
        return self.hard_rain or self.soft_rain

    def feels_at(self, windproofness: int) -> float:
        w = self.weather
        return feels(w.temp_c, w.wind_kmh * attenuation(windproofness), w.humidity_pct)


def window_hours(forecast: DayForecast, params: RequestParams) -> list[Hour]:
    start, end = params.wear_window
    rows = [h for h in forecast.hours if start <= h.hour < end]
    out: list[Hour] = []
    for i, w in enumerate(rows):
        hard = w.precip_prob >= POP_HARD
        soft = (not hard) and w.precip_prob >= POP_SOFT
        cls = intensity_class(w.precip_mmh) if (hard or soft) else "none"
        out.append(
            Hour(
                index=i,
                weather=w,
                weight=EXPOSURE_COMMUTE if w.hour in params.commute_hours else EXPOSURE_BASE,
                bare=feels(w.temp_c, w.wind_kmh, w.humidity_pct),
                hard_rain=hard,
                soft_rain=soft,
                intensity=cls,
                need_hard=COVER_REQUIRED[cls] if hard else 0,
                need_soft=1 if soft else 0,
                umbrella_ok=(
                    cls in ("light", "moderate") and w.wind_kmh < UMBRELLA_MAX_WIND_KMH
                ),
                windy=w.wind_kmh >= WIND_PENALTY_KMH,
            )
        )
    return out


def required_cover(hour: Hour, *, lower_moderate: bool = False) -> int:
    """Worn waterproofness this hour demands (FR-9).

    ``lower_moderate`` applies FR-14's R3 relaxation: a moderate-intensity rain
    hour accepts one level lower.  Heavy rain is never relaxed.
    """
    if hour.hard_rain:
        if lower_moderate and hour.intensity == "moderate":
            return max(1, hour.need_hard - 1)
        return hour.need_hard
    if hour.soft_rain:
        return hour.need_soft
    return 0


# --------------------------------------------------------------------------
# Candidate lists and the achievable band (FR-6.2 / D17)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidates:
    base: list[Garment]
    full_body: list[Garment]
    bottom: list[Garment]
    mid: list[Garment]
    outer: list[Garment]
    leg_base: list[Garment]
    footwear: list[Garment]
    accessories: list[Garment]

    def core(self) -> list[Garment]:
        return [
            *self.base,
            *self.full_body,
            *self.bottom,
            *self.mid,
            *self.outer,
            *self.leg_base,
            *self.footwear,
        ]


def candidates(
    wardrobe: Sequence[Garment], params: RequestParams, hours: Sequence[Hour]
) -> Candidates:
    """HC-2 (clean), HC-3 (occasion) and HC-7 (leg-base gating) as filters."""
    buckets: dict[str, list[Garment]] = {
        role: []
        for role in ("base", "full_body", "bottom", "mid", "outer", "leg_base", "footwear")
    }
    accessories: list[Garment] = []
    leg_allowed = min((h.bare for h in hours), default=99.0) <= LEG_BASE_MAX_BARE_C
    for g in sorted(wardrobe, key=lambda g: g.id):
        if g.status == "retired":
            continue
        if g.layer_role == "accessory":
            if g.status == "clean":
                accessories.append(g)
            continue
        if g.status != "clean" or params.occasion not in g.occasions:
            continue
        if g.layer_role == "leg_base" and not leg_allowed:
            continue
        buckets[g.layer_role].append(g)
    return Candidates(
        base=buckets["base"],
        full_body=buckets["full_body"],
        bottom=buckets["bottom"],
        mid=buckets["mid"],
        outer=buckets["outer"],
        leg_base=buckets["leg_base"],
        footwear=buckets["footwear"],
        accessories=accessories,
    )


def cover_of(garments: Iterable[Garment]) -> int:
    return max((g.waterproofness for g in garments if g.layer_role in COVER_ROLES), default=0)


def achievable_band(
    cand: Candidates,
    hours: Sequence[Hour],
    occasion: str,
    *,
    spread: int = 1,
) -> tuple[float, list[float]]:
    """D17's five-way formality-anchor scan: ``(ceiling, floor per hour)``."""
    ceiling: float | None = None
    floors: list[float | None] = [None] * len(hours)
    fallback: list[float | None] = [None] * len(hours)
    for anchor in range(1, 6):
        allowed = set(range(anchor, anchor + spread + 1))

        def keep(items: Sequence[Garment], _allowed: set[int] = allowed) -> list[Garment]:
            return [g for g in items if g.formality in _allowed]

        base, full = keep(cand.base), keep(cand.full_body)
        bottom, foot = keep(cand.bottom), keep(cand.footwear)
        mids, outers, legs = keep(cand.mid), keep(cand.outer), keep(cand.leg_base)
        if not foot:
            continue
        warm_body = cool_body = None
        cool_items: list[Garment] = []
        if base and bottom:
            warm_body = max(g.clo for g in base) + max(g.clo for g in bottom)
            cheap_base = min(base, key=lambda g: (g.clo, g.id))
            cheap_bottom = min(bottom, key=lambda g: (g.clo, g.id))
            cool_body = cheap_base.clo + cheap_bottom.clo
            cool_items = [cheap_base, cheap_bottom]
        if full:
            warm_full = max(g.clo for g in full)
            cheap_full = min(full, key=lambda g: (g.clo, g.id))
            warm_body = warm_full if warm_body is None else max(warm_body, warm_full)
            if cool_body is None or cheap_full.clo < cool_body:
                cool_body, cool_items = cheap_full.clo, [cheap_full]
        if warm_body is None or cool_body is None:
            continue
        cheap_foot = min(foot, key=lambda g: (g.clo, g.id))
        stack = (
            warm_body
            + max(g.clo for g in foot)
            + sum(sorted((g.clo for g in mids), reverse=True)[:MAX_MIDS])
            + max((g.clo for g in outers), default=0.0)
            + max((g.clo for g in legs), default=0.0)
        )
        value = icl([stack])
        ceiling = value if ceiling is None else max(ceiling, value)

        cool_sum = cool_body + cheap_foot.clo
        base_cover = cover_of([*cool_items, cheap_foot])
        umbrella_here = any(
            g.accessory_class == "umbrella"
            and g.formality in allowed
            and occasion in g.occasions
            for g in cand.accessories
        )
        addable = mids + outers
        uncovered = icl([cool_sum])
        for i, hour in enumerate(hours):
            if fallback[i] is None or uncovered < fallback[i]:
                fallback[i] = uncovered
            extra = 0.0
            if hour.need_hard > 0 and base_cover < hour.need_hard:
                if hour.umbrella_ok and umbrella_here:
                    extra = 0.0
                else:
                    ok = [g.clo for g in addable if g.waterproofness >= hour.need_hard]
                    if not ok:
                        continue
                    extra = min(ok)
            candidate_floor = icl([cool_sum + extra])
            if floors[i] is None or candidate_floor < floors[i]:
                floors[i] = candidate_floor
    if ceiling is None:
        return 0.0, [0.0] * len(hours)
    floor = [
        (floors[i] if floors[i] is not None else (fallback[i] or 0.0)) for i in range(len(hours))
    ]
    return ceiling, floor


def clamp_target(required: float, floor: float, ceiling: float) -> tuple[float, str | None]:
    high = max(ceiling, floor)
    if required < floor:
        return floor, "wardrobe_floor"
    if required > high:
        return high, "wardrobe_ceiling"
    return required, None


# --------------------------------------------------------------------------
# Component scorers, re-derived
# --------------------------------------------------------------------------


def hue_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def color_score(garments: Sequence[Garment]) -> float:
    mains = [next(c for c in g.colors if c.role == "main") for g in garments]
    pairs: list[float] = []
    for i in range(len(mains)):
        for j in range(i + 1, len(mains)):
            a, b = mains[i], mains[j]
            if a.neutral or b.neutral:
                pairs.append(1.0)
                continue
            delta = hue_distance(a.hue or 0.0, b.hue or 0.0)
            for bound, value in HUE_ZONES:
                if delta <= bound:
                    pairs.append(value)
                    break
            else:  # pragma: no cover - delta never exceeds 180
                pairs.append(HUE_ZONES[-1][1])
    score = sum(pairs) / len(pairs) if pairs else 1.0
    families = {
        int((c.hue or 0.0) // HUE_FAMILY_DEGREES) % 12
        for g in garments
        for c in g.colors
        if not c.neutral and c.hue is not None
    }
    if len(families) > MAX_HUE_FAMILIES:
        score -= HUE_FAMILY_PENALTY
    return min(1.0, max(0.0, score))


def style_score(garments: Sequence[Garment]) -> float:
    values = [g.formality for g in garments]
    spread = max(values) - min(values) if values else 0
    tight = max(0.0, 1.0 - STYLE_SPREAD_STEP * spread)
    n = len(garments)
    if n < 2:
        cohesion = 1.0
    else:
        shared = total = 0
        for i in range(n):
            tags = set(garments[i].style_tags)
            for j in range(i + 1, n):
                total += 1
                if tags & set(garments[j].style_tags):
                    shared += 1
        cohesion = shared / total
    return min(1.0, max(0.0, STYLE_FORMALITY_WEIGHT * tight + STYLE_TAG_WEIGHT * cohesion))


def variety_score(ids: Iterable[str], history: WearHistory, today: str) -> float:
    items = list(ids)
    if not items:
        return 1.0
    total = 0.0
    for gid in items:
        last = history.last_worn.get(gid)
        if last is None:
            continue
        days = max(
            0, (date_cls.fromisoformat(today) - date_cls.fromisoformat(last)).days
        )
        total += 2.0 ** (-days / VARIETY_HALF_LIFE_DAYS)
    return min(1.0, max(0.0, 1.0 - total / len(items)))


def thermal_score(hour_scores: Sequence[float], hours: Sequence[Hour]) -> float:
    if not hour_scores:
        return 0.0
    weight_sum = sum(h.weight for h in hours)
    weighted = sum(s * h.weight for s, h in zip(hour_scores, hours, strict=True))
    return THERMAL_MEAN_WEIGHT * (weighted / weight_sum) + THERMAL_WORST_WEIGHT * min(hour_scores)


# --------------------------------------------------------------------------
# Reading an emitted outfit back
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WornHour:
    """One hour of an emitted plan, resolved back to garments."""

    hour: Hour
    worn: list[Garment]
    slots: list[str]
    windproofness: int
    cover: int
    icl: float


def resolve_worn(
    slots: Sequence[str], by_slot: dict[str, Garment], hour: Hour
) -> WornHour:
    worn = [by_slot[s] for s in slots if s in by_slot]
    upper = [s for s in UPPER_ORDER if s in slots and s in by_slot]
    windproofness = by_slot[upper[-1]].windproofness if upper else 0
    return WornHour(
        hour=hour,
        worn=worn,
        slots=list(slots),
        windproofness=windproofness,
        cover=cover_of(worn),
        icl=icl([g.clo for g in worn]),
    )


def protect_hour(worn: WornHour, umbrella: bool, *, lower_moderate: bool = False) -> float:
    hour = worn.hour
    p = 1.0
    need = required_cover(hour, lower_moderate=lower_moderate)
    worn_ok = need == 0 or worn.cover >= need
    umbrella_ok = (not worn_ok) and hour.umbrella_ok and umbrella
    if not (worn_ok or umbrella_ok):
        if hour.hard_rain:
            p -= PENALTY_RAIN_UNCOVERED
        elif hour.soft_rain:
            p -= PENALTY_SOFT_RAIN
    if hour.windy:
        if worn.windproofness == 0:
            p -= PENALTY_WIND_UNBLOCKED
        elif worn.windproofness == 1:
            p -= PENALTY_WIND_PARTIAL
    return min(1.0, max(0.0, p))


# --------------------------------------------------------------------------
# FR-9 accessory attachment, re-derived
# --------------------------------------------------------------------------


def triggered_classes(
    hours: Sequence[Hour], umbrella_hours: Sequence[int]
) -> list[tuple[str, str]]:
    fired: list[tuple[str, str]] = []
    if umbrella_hours:
        fired.append(("umbrella", "rain"))
    if min((h.bare for h in hours), default=99.0) < COLD_EXTREMITY_C:
        fired.extend([("gloves", "cold"), ("hat", "cold"), ("scarf", "cold")])
    uv_rows = [h for h in hours if UV_WINDOW[0] <= h.weather.hour < UV_WINDOW[1]]
    if uv_rows and max(h.weather.uv_index for h in uv_rows) >= UV_ADVISORY_INDEX:
        fired.append(("sunglasses", "uv"))
    order = {c: i for i, c in enumerate(ACCESSORY_PRIORITY)}
    fired.sort(key=lambda item: order[item[0]])
    return fired


def attach_accessories(
    core: Sequence[Garment],
    accessories: Sequence[Garment],
    hours: Sequence[Hour],
    umbrella_hours: Sequence[int],
    occasion: str,
) -> tuple[list[str], list[str]]:
    """Return ``(garment ids in ascending order, classes that found nothing)``."""
    values = [g.formality for g in core]
    lo, hi = max(values) - 1, min(values) + 1
    centre = sorted(values)[len(values) // 2] if len(values) % 2 else (
        sorted(values)[len(values) // 2 - 1] + sorted(values)[len(values) // 2]
    ) / 2
    picked: list[Garment] = []
    gaps: list[str] = []
    for cls, _ in triggered_classes(hours, umbrella_hours):
        if len(picked) >= MAX_ACCESSORIES:
            break
        pool = [
            g
            for g in accessories
            if g.accessory_class == cls
            and g.status == "clean"
            and occasion in g.occasions
            and lo <= g.formality <= hi
        ]
        if not pool:
            gaps.append(cls)
            continue
        picked.append(min(pool, key=lambda g: (abs(g.formality - centre), g.id)))
    return sorted(g.id for g in picked), gaps


def umbrella_pick(
    core: Sequence[Garment], accessories: Sequence[Garment], occasion: str
) -> Garment | None:
    values = [g.formality for g in core]
    lo, hi = max(values) - 1, min(values) + 1
    ordered = sorted(values)
    centre = (
        ordered[len(ordered) // 2]
        if len(ordered) % 2
        else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    )
    pool = [
        g
        for g in accessories
        if g.accessory_class == "umbrella"
        and g.status == "clean"
        and occasion in g.occasions
        and lo <= g.formality <= hi
    ]
    if not pool:
        return None
    return min(pool, key=lambda g: (abs(g.formality - centre), g.id))


# --------------------------------------------------------------------------
# Hard constraints (HC-1 … HC-8)
# --------------------------------------------------------------------------


def hard_constraint_violations(
    by_slot: dict[str, Garment],
    hours: Sequence[Hour],
    params: RequestParams,
    history: WearHistory,
    *,
    umbrella: bool,
    spread_limit: int = 1,
    allow_repeat: bool = False,
    lower_moderate_cover: bool = False,
) -> list[str]:
    """Every HC-1…HC-8 violation of one emitted outfit, named."""
    problems: list[str] = []
    core = {slot: g for slot, g in by_slot.items() if slot in CORE_SLOTS}
    items = list(core.values())

    # HC-1 slot coverage and role compatibility
    if "base" not in core:
        problems.append("HC-1: no base slot")
    else:
        role = core["base"].layer_role
        if role == "full_body":
            if "bottom" in core:
                problems.append("HC-1: a full_body dress may not be paired with a bottom")
        elif role != "base":
            problems.append(f"HC-1: slot base holds a {role!r} garment")
        elif "bottom" not in core:
            problems.append("HC-1: base top without a bottom")
    if "footwear" not in core:
        problems.append("HC-1: no footwear")
    elif core["footwear"].layer_role != "footwear":
        problems.append("HC-1: footwear slot holds a non-footwear garment")
    if "mid_2" in core and "mid_1" not in core:
        problems.append("HC-1: mid_2 without mid_1")
    for slot, role in (("mid_1", "mid"), ("mid_2", "mid"), ("outer", "outer"),
                       ("bottom", "bottom"), ("leg_base", "leg_base")):
        if slot in core and core[slot].layer_role != role:
            problems.append(f"HC-1: slot {slot} holds a {core[slot].layer_role!r} garment")
    if "mid_1" in core and "mid_2" in core:
        a, b = core["mid_1"], core["mid_2"]
        if (b.clo, b.id) < (a.clo, a.id):
            problems.append("HC-1: mid_2 must be the outermost mid (LIFO order)")

    # HC-2 cleanliness / HC-3 occasion
    for g in items:
        if g.status != "clean":
            problems.append(f"HC-2: {g.name} is {g.status}")
        if params.occasion not in g.occasions:
            problems.append(f"HC-3: {g.name} does not list {params.occasion!r}")

    # HC-4 formality coherence
    if items:
        values = [g.formality for g in items]
        if max(values) - min(values) > spread_limit:
            problems.append(f"HC-4: formality spread {max(values) - min(values)}")

    # HC-5 no duplicates
    ids = [g.id for g in by_slot.values()]
    if len(ids) != len(set(ids)):
        problems.append("HC-5: a garment appears twice")

    # HC-7 leg-base gating
    if "leg_base" in core and min(h.bare for h in hours) > LEG_BASE_MAX_BARE_C:
        problems.append("HC-7: leg base worn on a day whose minimum bare feels-like is above 0 C")

    # HC-6 rain cover: some configuration must cover every hard-rain hour, and
    # cover is monotone in worn layers, so the fullest configuration decides.
    full_cover = cover_of(items)
    for hour in hours:
        need = hour.need_hard
        if need == 0:
            continue
        if lower_moderate_cover and hour.intensity == "moderate":
            need = max(1, need - 1)
        if full_cover >= need:
            continue
        if hour.umbrella_ok and umbrella:
            continue
        problems.append(f"HC-6: hour {hour.weather.hour:02d}:00 needs waterproofness {need}")
        break

    # HC-8 no repeat of yesterday
    if not allow_repeat:
        core_ids = frozenset(g.id for g in items)
        if any(core_ids == worn for worn in history.yesterday_sets):
            problems.append("HC-8: the core-item set repeats yesterday's")
    return problems
