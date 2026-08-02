"""Thermal physics, the achievable band, layer plans and the day brief.

Implements SCOPE.md FR-5 (feels-like with ramps and the bare/config split),
FR-6 (required insulation, achievable band, ``S_thermal``), FR-7 (ensemble
insulation, configuration enumeration, per-hour selection with hysteresis and
segment compression) and FR-16 (the wardrobe-free day brief).

Everything here is pure: same inputs, same outputs, no clock, no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dresscast.engine.models import (
    ARCHETYPE_BOUNDS,
    ARCHETYPE_NAMES,
    BOUNDARY_LAYER_CLO,
    CLO_MET_SLOPE,
    COLD_EXTREMITY_C,
    COMFORT_BAND_CLO,
    CONFIG_SWITCH_HYSTERESIS,
    COVER_REQUIRED,
    COVER_ROLES,
    ICL_INTERCEPT,
    ICL_SLOPE,
    INTENSITY_LIGHT_MAX_MMH,
    INTENSITY_MODERATE_MAX_MMH,
    LEG_BASE_MAX_BARE_C,
    MAX_CONFIG_CHANGES,
    MAX_MIDS,
    MIN_DWELL_HOURS,
    POP_HARD,
    POP_SOFT,
    RAMP_COLD_HIGH_C,
    RAMP_COLD_LOW_C,
    RAMP_HEAT_HIGH_C,
    RAMP_HEAT_LOW_C,
    REQUIRED_CLO_MAX,
    REQUIRED_CLO_MIN,
    SELECT_EPS,
    SKIN_TEMP_C,
    THERMAL_MEAN_WEIGHT,
    THERMAL_WORST_WEIGHT,
    UMBRELLA_COVERS,
    UMBRELLA_MAX_WIND_KMH,
    UV_ADVISORY_INDEX,
    UV_WINDOW,
    WIND_ATTENUATION,
    WIND_CHILL_MIN_KMH,
    WIND_PENALTY_KMH,
    ZERO_SCORE_DEV_CLO,
    Advisory,
    Band,
    BriefHour,
    CoreOutfit,
    DayBrief,
    DayForecast,
    Garment,
    HourlyWeather,
    HourPlanEntry,
    LayerConfig,
    PlanSegment,
    RequestParams,
    SlotCandidates,
    q_plan,
)
from dresscast.errors import InvalidParams

# --------------------------------------------------------------------------
# FR-5 — feels-like
# --------------------------------------------------------------------------


def wind_chill(temp_c: float, wind_kmh: float) -> float:
    """JAG/TI 2001 wind-chill temperature (°C), as adopted by the US NWS."""
    v16 = wind_kmh**0.16
    return 13.12 + 0.6215 * temp_c - 11.37 * v16 + 0.3965 * temp_c * v16


def wind_delta(temp_c: float, wind_kmh: float) -> float:
    """``WCT(T, v) - T``, zero at or below :data:`WIND_CHILL_MIN_KMH`."""
    if wind_kmh <= WIND_CHILL_MIN_KMH:
        return 0.0
    return wind_chill(temp_c, wind_kmh) - temp_c


def apparent_temperature(temp_c: float, humidity_pct: float, wind_kmh: float) -> float:
    """Steadman (1984) apparent temperature, non-radiant form (°C)."""
    vapour_pressure = (humidity_pct / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    wind_ms = wind_kmh / 3.6
    return temp_c + 0.33 * vapour_pressure - 0.70 * wind_ms - 4.00


def heat_delta(temp_c: float, humidity_pct: float, wind_kmh: float) -> float:
    """``AT(T, rh, v) - T``."""
    return apparent_temperature(temp_c, humidity_pct, wind_kmh) - temp_c


def ramp_cold(temp_c: float) -> float:
    """1.0 at or below 10 °C, 0.0 at or above 14 °C, linear between (FR-5)."""
    if temp_c <= RAMP_COLD_LOW_C:
        return 1.0
    if temp_c >= RAMP_COLD_HIGH_C:
        return 0.0
    return (RAMP_COLD_HIGH_C - temp_c) / (RAMP_COLD_HIGH_C - RAMP_COLD_LOW_C)


def ramp_heat(temp_c: float) -> float:
    """0.0 at or below 24 °C, 1.0 at or above 26 °C, linear between (FR-5)."""
    if temp_c <= RAMP_HEAT_LOW_C:
        return 0.0
    if temp_c >= RAMP_HEAT_HIGH_C:
        return 1.0
    return (temp_c - RAMP_HEAT_LOW_C) / (RAMP_HEAT_HIGH_C - RAMP_HEAT_LOW_C)


def feels_like(temp_c: float, wind_kmh: float, humidity_pct: float) -> float:
    """FR-5's continuous feels-like temperature (°C).

    The two ramps have disjoint support, so at most one delta is active; each
    is faded rather than switched at its formula's validity edge, which makes
    ``feels`` Lipschitz-3 in ``T`` (EVALS.md M1's continuity family).
    """
    out = temp_c
    rc = ramp_cold(temp_c)
    if rc > 0.0:
        out += rc * wind_delta(temp_c, wind_kmh)
    rh = ramp_heat(temp_c)
    if rh > 0.0:
        out += rh * heat_delta(temp_c, humidity_pct, wind_kmh)
    return out


def effective_wind(wind_kmh: float, windproofness: int) -> float:
    """Wind reaching the body through an outermost layer of this windproofness."""
    return wind_kmh * WIND_ATTENUATION[windproofness]


def bare_feels_c(hour: HourlyWeather) -> float:
    """FR-5's configuration-independent feels-like (windproofness 0)."""
    return feels_like(hour.temp_c, hour.wind_kmh, hour.humidity_pct)


def config_feels_c(hour: HourlyWeather, windproofness: int) -> float:
    """FR-5's configuration-dependent feels-like for a windproofness class."""
    return feels_like(hour.temp_c, effective_wind(hour.wind_kmh, windproofness), hour.humidity_pct)


# --------------------------------------------------------------------------
# FR-6.1 / FR-7 — required insulation and ensemble insulation
# --------------------------------------------------------------------------


def required_clo(feels_c: float, met: float) -> float:
    """FR-6.1: ``clamp((34 - T)/(7.66·met) - 0.7, 0.0, 4.5)`` (D3)."""
    raw = (SKIN_TEMP_C - feels_c) / (CLO_MET_SLOPE * met) - BOUNDARY_LAYER_CLO
    return min(REQUIRED_CLO_MAX, max(REQUIRED_CLO_MIN, raw))


def ensemble_clo(garments: Iterable[Garment]) -> float:
    """FR-7 / D2: ``Icl = 0.835·Σ clo + 0.161``; 0 for the empty ensemble."""
    total = 0.0
    empty = True
    for g in garments:
        empty = False
        total += g.clo
    if empty:
        return 0.0
    return ICL_SLOPE * total + ICL_INTERCEPT


def _icl_from_sum(clo_sum: float) -> float:
    return ICL_SLOPE * clo_sum + ICL_INTERCEPT


def hour_score(deviation: float) -> float:
    """FR-6.3: ``max(0, 1 - max(0, |dev| - 0.25)/0.75)``."""
    excess = abs(deviation) - COMFORT_BAND_CLO
    if excess <= 0.0:
        return 1.0
    span = ZERO_SCORE_DEV_CLO - COMFORT_BAND_CLO
    return max(0.0, 1.0 - excess / span)


def intensity_class(precip_mmh: float) -> str:
    """WMO/Met Office intensity bands (D7)."""
    if precip_mmh < INTENSITY_LIGHT_MAX_MMH:
        return "light"
    if precip_mmh <= INTENSITY_MODERATE_MAX_MMH:
        return "moderate"
    return "heavy"


# --------------------------------------------------------------------------
# Per-hour context — the memoisation EVALS.md §6's runtime budget assumes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HourContext:
    """Everything the engine needs about one wear-window hour."""

    index: int
    weather: HourlyWeather
    weight: float
    bare_feels_c: float
    feels_by_w: tuple[float, float, float]
    required_by_w: tuple[float, float, float]
    intensity: str  # "none" | "light" | "moderate" | "heavy"
    hard_rain: bool
    soft_rain: bool
    needed_cover: int  # worn waterproofness HC-6 demands, 0 when no hard rain
    umbrella_ok: bool  # an umbrella is valid cover this hour (class + wind)
    windy: bool

    @property
    def rain_any(self) -> bool:
        return self.hard_rain or self.soft_rain


@dataclass(frozen=True, slots=True)
class DayContext:
    """The wear window's hours plus the day-level quantities every rule reads."""

    params: RequestParams
    hours: tuple[HourContext, ...]
    weight_sum: float
    bare_min: float
    bare_max: float
    bare_min_hour: int
    bare_max_hour: int
    required_min: float
    required_max: float
    wind_max: float
    wind_max_hour: int
    uv_max: float
    uv_max_hour: int
    leg_base_allowed: bool

    @property
    def cold_extremities(self) -> bool:
        return self.bare_min < COLD_EXTREMITY_C

    @property
    def uv_advisory(self) -> bool:
        return self.uv_max >= UV_ADVISORY_INDEX


def build_day_context(forecast: DayForecast, params: RequestParams) -> DayContext:
    """Precompute FR-5/FR-6 quantities for every wear-window hour."""
    rows = forecast.window_hours(params.wear_window)
    if not rows:
        raise InvalidParams(
            "the wear window contains no forecast hours",
            wear_window=list(params.wear_window),
        )
    hours: list[HourContext] = []
    for i, w in enumerate(rows):
        feels = tuple(config_feels_c(w, k) for k in range(3))
        required = tuple(required_clo(f, params.met) for f in feels)
        hard = w.precip_prob >= POP_HARD
        soft = (not hard) and w.precip_prob >= POP_SOFT
        cls = intensity_class(w.precip_mmh) if (hard or soft) else "none"
        hours.append(
            HourContext(
                index=i,
                weather=w,
                weight=params.exposure_weight(w.hour),
                bare_feels_c=feels[0],
                feels_by_w=feels,  # type: ignore[arg-type]
                required_by_w=required,  # type: ignore[arg-type]
                intensity=cls,
                hard_rain=hard,
                soft_rain=soft,
                needed_cover=COVER_REQUIRED[cls] if hard else 0,
                umbrella_ok=(cls in UMBRELLA_COVERS and w.wind_kmh < UMBRELLA_MAX_WIND_KMH),
                windy=w.wind_kmh >= WIND_PENALTY_KMH,
            )
        )
    bare = [h.bare_feels_c for h in hours]
    req = [h.required_by_w[0] for h in hours]
    i_min = min(range(len(hours)), key=lambda i: (bare[i], i))
    i_max = max(range(len(hours)), key=lambda i: (bare[i], -i))
    i_wind = max(range(len(hours)), key=lambda i: (hours[i].weather.wind_kmh, -i))
    uv_rows = [h for h in hours if UV_WINDOW[0] <= h.weather.hour < UV_WINDOW[1]]
    if uv_rows:
        uv_best = max(uv_rows, key=lambda h: (h.weather.uv_index, -h.index))
        uv_max, uv_hour = uv_best.weather.uv_index, uv_best.weather.hour
    else:
        uv_max, uv_hour = 0.0, UV_WINDOW[0]
    return DayContext(
        params=params,
        hours=tuple(hours),
        weight_sum=sum(h.weight for h in hours),
        bare_min=bare[i_min],
        bare_max=bare[i_max],
        bare_min_hour=hours[i_min].weather.hour,
        bare_max_hour=hours[i_max].weather.hour,
        required_min=min(req),
        required_max=max(req),
        wind_max=hours[i_wind].weather.wind_kmh,
        wind_max_hour=hours[i_wind].weather.hour,
        uv_max=uv_max,
        uv_max_hour=uv_hour,
        leg_base_allowed=min(bare) <= LEG_BASE_MAX_BARE_C,
    )


# --------------------------------------------------------------------------
# FR-6.2 / D17 — the achievable band
# --------------------------------------------------------------------------


def _cover_level(garments: Iterable[Garment]) -> int:
    return max((g.waterproofness for g in garments if g.layer_role in COVER_ROLES), default=0)


def achievable_band(
    ctx: DayContext,
    candidates: SlotCandidates,
    *,
    formality_spread: int = 1,
    occasion: str | None = None,
) -> Band:
    """FR-6.2: the exact insulation band this wardrobe can reach, in O(n).

    For each formality anchor ``f`` the candidate set ``A_f`` is the core
    candidates with formality in ``{f … f+spread}`` — every such set satisfies
    HC-4 by construction.  The ceiling is the warmest slot-complete pick over
    all anchors; the per-hour floor is the coolest slot-complete pick plus the
    cheapest adequate worn cover on hours that mandate one (FR-9).
    """
    n = len(ctx.hours)
    ceiling: float | None = None
    floors: list[float | None] = [None] * n
    floors_uncovered: list[float | None] = [None] * n

    for f in range(1, 6):
        allowed = set(range(f, f + formality_spread + 1))

        def keep(items: Sequence[Garment], allowed: set[int] = allowed) -> list[Garment]:
            return [g for g in items if g.formality in allowed]

        base = keep(candidates.base)
        full = keep(candidates.full_body)
        bottom = keep(candidates.bottom)
        foot = keep(candidates.footwear)
        mids = keep(candidates.mid)
        outers = keep(candidates.outer)
        legs = keep(candidates.leg_base) if ctx.leg_base_allowed else []
        if not foot:
            continue

        warm_body: float | None = None
        cool_body: float | None = None
        cool_body_items: tuple[Garment, ...] = ()
        if base and bottom:
            warm_body = max(g.clo for g in base) + max(g.clo for g in bottom)
            cheap_base = min(base, key=lambda g: (g.clo, g.id))
            cheap_bottom = min(bottom, key=lambda g: (g.clo, g.id))
            cool_body = cheap_base.clo + cheap_bottom.clo
            cool_body_items = (cheap_base, cheap_bottom)
        if full:
            warm_full = max(g.clo for g in full)
            cheap_full = min(full, key=lambda g: (g.clo, g.id))
            warm_body = warm_full if warm_body is None else max(warm_body, warm_full)
            if cool_body is None or cheap_full.clo < cool_body:
                cool_body = cheap_full.clo
                cool_body_items = (cheap_full,)
        if warm_body is None or cool_body is None:
            continue

        warm_foot = max(g.clo for g in foot)
        cheap_foot = min(foot, key=lambda g: (g.clo, g.id))

        # -- ceiling: everything the anchor allows, worn at once ------------
        top_mids = sorted((g.clo for g in mids), reverse=True)[:MAX_MIDS]
        stack = (
            warm_body
            + warm_foot
            + sum(top_mids)
            + (max((g.clo for g in outers), default=0.0))
            + (max((g.clo for g in legs), default=0.0))
        )
        anchor_ceiling = _icl_from_sum(stack)
        ceiling = anchor_ceiling if ceiling is None else max(ceiling, anchor_ceiling)

        # -- floor: the coolest slot-complete pick, plus mandatory cover ----
        cool_items = (*cool_body_items, cheap_foot)
        cool_sum = cool_body + cheap_foot.clo
        anchor_floor_uncovered = _icl_from_sum(cool_sum)
        base_cover = _cover_level(cool_items)
        umbrella_here = any(
            g.accessory_class == "umbrella"
            and g.formality in allowed
            and (occasion is None or occasion in g.occasions)
            for g in candidates.accessories
        )
        addable = mids + outers
        for i, hc in enumerate(ctx.hours):
            if floors_uncovered[i] is None or anchor_floor_uncovered < floors_uncovered[i]:
                floors_uncovered[i] = anchor_floor_uncovered
            extra = 0.0
            if hc.needed_cover > 0 and base_cover < hc.needed_cover:
                if hc.umbrella_ok and umbrella_here:
                    extra = 0.0
                else:
                    adequate = [g.clo for g in addable if g.waterproofness >= hc.needed_cover]
                    if not adequate:
                        continue  # this anchor cannot dress this hour at all
                    extra = min(adequate)
            value = _icl_from_sum(cool_sum + extra)
            if floors[i] is None or value < floors[i]:
                floors[i] = value

    if ceiling is None:
        # No anchor is slot-complete: HC-1/HC-4 admit nothing.  Return a
        # degenerate band; the caller's hard-constraint pass reports it.
        return Band(ceiling=0.0, floor=tuple(0.0 for _ in range(n)))
    floor = tuple(
        (floors[i] if floors[i] is not None else (floors_uncovered[i] or 0.0)) for i in range(n)
    )
    return Band(ceiling=ceiling, floor=floor)


# --------------------------------------------------------------------------
# FR-7 — configurations
# --------------------------------------------------------------------------


def layer_configs(outfit: CoreOutfit) -> tuple[LayerConfig, ...]:
    """Enumerate the ≤ 2·(m+1) ≤ 6 configurations of an outfit (FR-7/D6).

    Base, bottom, leg base and footwear are always worn.  Mids are shed
    outermost-first (LIFO, thickest first — ``mids`` is ordered ascending by
    ``(clo, id)`` so ``mids[:j]`` is the set still worn after shedding).  The
    outer goes on and off independently.  The result is ordered by ``Icl``, so
    per-hour selection walks a chain rather than scanning (EVALS.md §6).
    """
    always: list[tuple[str, Garment]] = [("base", outfit.base)]
    if outfit.bottom is not None:
        always.append(("bottom", outfit.bottom))
    if outfit.leg_base is not None:
        always.append(("leg_base", outfit.leg_base))
    always.append(("footwear", outfit.footwear))
    base_sum = 0.0
    base_cover = 0
    base_ids: list[str] = []
    for _, g in always:
        base_sum += g.clo
        base_ids.append(g.id)
        if g.layer_role in COVER_ROLES and g.waterproofness > base_cover:
            base_cover = g.waterproofness
    mid_slots = ("mid_1", "mid_2")
    outer = outfit.outer

    raw: list[tuple[float, int, tuple[str, ...], tuple[str, ...], int, int]] = []
    running_sum = base_sum
    running_cover = base_cover
    worn_slots: list[str] = [slot for slot, _ in always]
    for j in range(len(outfit.mids) + 1):
        if j > 0:
            mid = outfit.mids[j - 1]
            running_sum += mid.clo
            running_cover = max(running_cover, mid.waterproofness)
            worn_slots.append(mid_slots[j - 1])
        outermost = outfit.mids[j - 1] if j > 0 else outfit.base
        ids = tuple(sorted(base_ids + [m.id for m in outfit.mids[:j]]))
        raw.append(
            (
                _icl_from_sum(running_sum),
                outermost.windproofness,
                tuple(worn_slots),
                ids,
                running_cover,
                len(ids),
            )
        )
        if outer is not None:
            raw.append(
                (
                    _icl_from_sum(running_sum + outer.clo),
                    outer.windproofness,
                    (*worn_slots, "outer"),
                    tuple(sorted((*ids, outer.id))),
                    max(running_cover, outer.waterproofness),
                    len(ids) + 1,
                )
            )
    raw.sort(key=lambda c: (c[0], c[5], c[3]))
    return tuple(
        LayerConfig(
            index=i,
            worn_slots=item[2],
            garment_ids=item[3],
            icl=item[0],
            windproofness=item[1],
            cover=item[4],
            layer_count=item[5],
        )
        for i, item in enumerate(raw)
    )


def config_feasible(cfg: LayerConfig, hc: HourContext, umbrella: bool) -> bool:
    """HC-6: does this configuration carry adequate cover for this hour?"""
    if hc.needed_cover == 0:
        return True
    if cfg.cover >= hc.needed_cover:
        return True
    return hc.umbrella_ok and umbrella


def config_target(cfg: LayerConfig, hc: HourContext, band: Band) -> tuple[float, float, str | None]:
    """Return ``(required_clo, target_clo, clamped)`` for a configuration-hour."""
    required = hc.required_by_w[cfg.windproofness]
    target, clamped = band.clamp(required, hc.index)
    return required, target, clamped


@dataclass(frozen=True, slots=True)
class TargetTable:
    """``target_clo`` memoised per ``(hour, windproofness)`` — FR-6.2's clamp.

    The clamp reads only the hour's required clo and the band, never the
    configuration's garments, so the whole table is computed once per search
    and the inner selection loop becomes a lookup (EVALS.md §6's 45 values).
    """

    targets: tuple[tuple[float, float, float], ...]

    def of(self, index: int, windproofness: int) -> float:
        return self.targets[index][windproofness]


def target_table(ctx: DayContext, band: Band) -> TargetTable:
    return TargetTable(
        targets=tuple(
            tuple(band.clamp(hc.required_by_w[w], hc.index)[0] for w in range(3))
            for hc in ctx.hours
        )  # type: ignore[arg-type]
    )


@dataclass(frozen=True, slots=True)
class ThermalEval:
    """The per-hour outcome of FR-7's selection plus FR-6.4's aggregate."""

    chosen: tuple[int, ...]
    deviations: tuple[float, ...]
    targets: tuple[float, ...]
    hour_scores: tuple[float, ...]
    s_thermal: float
    changes: int


def _best_config(
    configs: Sequence[LayerConfig],
    hc: HourContext,
    table: TargetTable,
    umbrella: bool,
) -> int | None:
    """FR-7's ``best(h)``: minimise ``|Icl(c) - target_clo(h, c)|``.

    Ties break on fewer worn layers, then the garment-id tuple.  Two
    deviations within :data:`SELECT_EPS` count as tied, so a last-ULP
    difference in ``v**0.16`` between platforms cannot flip the choice — the
    comparison resolution matches FR-19's ``SELECT_DP``.
    """
    best_d = 0.0
    best_key: tuple[int, tuple[str, ...]] | None = None
    best_i: int | None = None
    row = table.targets[hc.index]
    for i, cfg in enumerate(configs):
        if not config_feasible(cfg, hc, umbrella):
            continue
        d = cfg.icl - row[cfg.windproofness]
        if d < 0.0:
            d = -d
        if best_i is None or d < best_d - SELECT_EPS:
            best_d, best_i, best_key = d, i, None
        elif d <= best_d + SELECT_EPS:
            if best_key is None:
                best_key = (configs[best_i].layer_count, configs[best_i].garment_ids)
            key = (cfg.layer_count, cfg.garment_ids)
            if key < best_key:
                best_d, best_i, best_key = d, i, key
    return best_i


def select_plan(
    configs: Sequence[LayerConfig],
    ctx: DayContext,
    band: Band,
    umbrella: bool,
    table: TargetTable | None = None,
) -> ThermalEval | None:
    """FR-7's single deterministic left-to-right smoothing pass.

    Returns ``None`` when some wear-window hour has no feasible configuration,
    which is exactly an HC-6 violation for this outfit.

    One refinement the pseudocode leaves implicit: when the current
    configuration stops being feasible (a rain hour arrives and the cover is
    off) the switch is forced, bypassing hysteresis, the dwell floor and the
    change cap — HC-6 is a hard constraint and cannot be traded against plan
    smoothness.
    """
    tbl = table if table is not None else target_table(ctx, band)
    hours = ctx.hours
    best_idx: list[int] = []
    for hc in hours:
        i = _best_config(configs, hc, tbl, umbrella)
        if i is None:
            return None
        best_idx.append(i)

    cur = best_idx[0]
    changes = 0
    dwell = 1
    chosen = [cur]
    for pos in range(1, len(hours)):
        hc = hours[pos]
        cand = best_idx[pos]
        cfg_cur = configs[cur]
        if not config_feasible(cfg_cur, hc, umbrella):
            cur = cand
            changes += 1
            dwell = 1
        else:
            row = tbl.targets[pos]
            cfg_cand = configs[cand]
            gain = abs(cfg_cur.icl - row[cfg_cur.windproofness]) - abs(
                cfg_cand.icl - row[cfg_cand.windproofness]
            )
            if (
                cand != cur
                and gain > CONFIG_SWITCH_HYSTERESIS
                and dwell >= MIN_DWELL_HOURS
                and changes < MAX_CONFIG_CHANGES
            ):
                cur = cand
                changes += 1
                dwell = 1
            else:
                dwell += 1
        chosen.append(cur)

    devs: list[float] = []
    targets: list[float] = []
    scores: list[float] = []
    for pos in range(len(hours)):
        cfg = configs[chosen[pos]]
        target = tbl.targets[pos][cfg.windproofness]
        dev = cfg.icl - target
        devs.append(dev)
        targets.append(target)
        scores.append(hour_score(dev))
    return ThermalEval(
        chosen=tuple(chosen),
        deviations=tuple(devs),
        targets=tuple(targets),
        hour_scores=tuple(scores),
        s_thermal=thermal_score(scores, ctx),
        changes=changes,
    )


def thermal_score(hour_scores: Sequence[float], ctx: DayContext) -> float:
    """FR-6.4: ``0.75·(Σ w_h s_h / Σ w_h) + 0.25·min_h s_h``.

    Aggregated from unrounded hour scores; only the result is quantized by the
    caller, so an independent recomputation agrees to well within 1e-6.
    """
    if not hour_scores:
        return 0.0
    weighted = sum(s * hc.weight for s, hc in zip(hour_scores, ctx.hours, strict=True))
    mean = weighted / ctx.weight_sum
    return THERMAL_MEAN_WEIGHT * mean + THERMAL_WORST_WEIGHT * min(hour_scores)


# --------------------------------------------------------------------------
# FR-7 — plan materialisation and segment compression
# --------------------------------------------------------------------------


def _carried_slots(
    outfit_slots: Sequence[str], worn_history: Sequence[frozenset[str]], pos: int
) -> list[str]:
    """Slots off this hour that were worn earlier and so must be carried."""
    worn_now = worn_history[pos]
    seen_earlier: set[str] = set()
    for j in range(pos):
        seen_earlier |= worn_history[j]
    return [s for s in outfit_slots if s not in worn_now and s in seen_earlier]


def hourly_plan(
    outfit: CoreOutfit,
    ctx: DayContext,
    band: Band,
    configs: Sequence[LayerConfig],
    thermal: ThermalEval,
    protect_hours: Sequence[float],
    umbrella: bool,
) -> list[HourPlanEntry]:
    """Materialise DATA_MODEL.md §2.5's hour plan for one outfit."""
    outfit_slots = [slot for slot, _ in outfit.slot_items()]
    worn_history = [frozenset(configs[i].worn_slots) for i in thermal.chosen]
    entries: list[HourPlanEntry] = []
    for pos, hc in enumerate(ctx.hours):
        cfg = configs[thermal.chosen[pos]]
        w = hc.weather
        required, target, clamped = config_target(cfg, hc, band)
        dev = thermal.deviations[pos]
        carried = _carried_slots(outfit_slots, worn_history, pos)
        notes: list[str] = []
        if clamped:
            notes.append(clamped)
        if abs(dev) > COMFORT_BAND_CLO:
            notes.append("slightly_cool" if dev < 0 else "slightly_warm")
        if hc.hard_rain:
            notes.append("rain_cover_required")
        if hc.rain_any and cfg.cover < max(hc.needed_cover, 1) and umbrella and hc.umbrella_ok:
            notes.append("umbrella_in_use")
        if pos > 0:
            prev = worn_history[pos - 1]
            now = worn_history[pos]
            if now < prev:
                notes.append("shed_layer")
            elif now > prev:
                notes.append("add_layer")
        if carried:
            notes.append("carrying")
        if hc.windy and cfg.windproofness == 0:
            notes.append("wind_exposed")
        entries.append(
            HourPlanEntry(
                seq=w.seq,
                hour=w.hour,
                temp_c=q_plan(w.temp_c),
                wind_kmh=q_plan(w.wind_kmh),
                humidity_pct=q_plan(w.humidity_pct),
                precip_prob=q_plan(w.precip_prob),
                precip_mmh=q_plan(w.precip_mmh),
                uv_index=q_plan(w.uv_index),
                bare_feels_c=q_plan(hc.bare_feels_c),
                effective_wind_kmh=q_plan(effective_wind(w.wind_kmh, cfg.windproofness)),
                feels_c=q_plan(hc.feels_by_w[cfg.windproofness]),
                required_clo=q_plan(required),
                target_clo=q_plan(target),
                clamped=clamped,  # type: ignore[arg-type]
                worn_slots=list(cfg.worn_slots),
                carried_slots=carried,
                ensemble_clo=q_plan(cfg.icl),
                deviation=q_plan(dev),
                in_band=abs(dev) <= COMFORT_BAND_CLO,
                hour_score=q_plan(thermal.hour_scores[pos]),
                exposure_weight=hc.weight,
                rain_cover_on=hc.rain_any and cfg.cover >= 1,
                protect_score=q_plan(protect_hours[pos]),
                notes=notes,
            )
        )
    return entries


def compress_plan(plan: Sequence[HourPlanEntry]) -> list[PlanSegment]:
    """Compress the hour plan into maximal contiguous same-configuration runs."""
    segments: list[PlanSegment] = []
    for entry in plan:
        worn = list(entry.worn_slots)
        if segments and segments[-1].worn_slots == worn:
            last = segments[-1]
            segments[-1] = PlanSegment(
                start_hour=last.start_hour,
                end_hour=entry.hour,
                start_seq=last.start_seq,
                end_seq=entry.seq,
                worn_slots=last.worn_slots,
                carried_slots=list(entry.carried_slots),
            )
        else:
            segments.append(
                PlanSegment(
                    start_hour=entry.hour,
                    end_hour=entry.hour,
                    start_seq=entry.seq,
                    end_seq=entry.seq,
                    worn_slots=worn,
                    carried_slots=list(entry.carried_slots),
                )
            )
    return segments


# --------------------------------------------------------------------------
# FR-16 — the wardrobe-free day brief
# --------------------------------------------------------------------------


def layer_archetype(required: float) -> str:
    """Bracket a required-clo value with FR-16's layer archetype."""
    for bound, name in zip(ARCHETYPE_BOUNDS, ARCHETYPE_NAMES, strict=False):
        if required < bound:
            return name
    return ARCHETYPE_NAMES[-1]


def day_brief(forecast: DayForecast, params: RequestParams) -> DayBrief:
    """FR-16: what the day demands, with no wardrobe at all."""
    ctx = build_day_context(forecast, params)
    hours: list[BriefHour] = []
    for hc in ctx.hours:
        w = hc.weather
        required = hc.required_by_w[0]
        hours.append(
            BriefHour(
                seq=w.seq,
                hour=w.hour,
                temp_c=q_plan(w.temp_c),
                wind_kmh=q_plan(w.wind_kmh),
                humidity_pct=q_plan(w.humidity_pct),
                precip_prob=q_plan(w.precip_prob),
                precip_mmh=q_plan(w.precip_mmh),
                uv_index=q_plan(w.uv_index),
                bare_feels_c=q_plan(hc.bare_feels_c),
                required_clo=q_plan(required),
                archetype=layer_archetype(required),
                rain_cover_class=hc.intensity,  # type: ignore[arg-type]
                rain_required=hc.hard_rain,
            )
        )
    advisories: list[Advisory] = []
    if ctx.cold_extremities:
        advisories.append(
            Advisory(
                kind="cold_extremities",
                text=(
                    f"Coldest wear-window hour feels like {ctx.bare_min:.1f}°C at "
                    f"{ctx.bare_min_hour:02d}:00 — cover hands, head and neck."
                ),
                value=q_plan(ctx.bare_min),
            )
        )
    if ctx.wind_max >= WIND_PENALTY_KMH:
        advisories.append(
            Advisory(
                kind="wind",
                text=(
                    f"Wind peaks at {ctx.wind_max:.0f} km/h at {ctx.wind_max_hour:02d}:00 — "
                    "a windproof outer layer is worth its weight."
                ),
                value=q_plan(ctx.wind_max),
            )
        )
    if ctx.uv_advisory:
        advisories.append(
            Advisory(
                kind="uv",
                text=(
                    f"UV index peaks at {ctx.uv_max:.1f} at {ctx.uv_max_hour:02d}:00 — "
                    "sun protection advised."
                ),
                value=q_plan(ctx.uv_max),
            )
        )
    archetypes = sorted({h.archetype for h in hours}, key=lambda a: ARCHETYPE_NAMES.index(a))
    return DayBrief(
        date=params.date,
        wear_window=params.wear_window,
        met=params.met,
        hours=hours,
        required_clo_min=q_plan(ctx.required_min),
        required_clo_max=q_plan(ctx.required_max),
        bare_feels_min=q_plan(ctx.bare_min),
        bare_feels_max=q_plan(ctx.bare_max),
        archetype_range=archetypes,
        advisories=advisories,
    )
