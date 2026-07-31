"""Metric implementations for the dresscast eval suite (EVALS.md §3, §5, §6).

Three kinds of truth live here, kept deliberately apart:

* **Published tables and hand calculation** — ``evals/fixtures/physics_golden.json``
  (M1).  Nothing in it is produced by the code under test.
* **Live-computed references** — the brute-force enumerator below (M2b, M2c,
  M3, M8) and the two baselines (§5.1).  These enumerate independently but
  score with the *engine's own objective*, which is the point: M2b asks whether
  pruning costs anything on the quantity the engine maximises.
* **The independent rule checker** — ``evals/checker.py`` (M4, M9, M10), which
  imports nothing from ``dresscast.engine`` except ``models``.

Everything is hermetic: committed fixtures, offline adapters, no network, no
wall clock (every timestamp is a literal), and the only RNG is
``random.Random(0)`` inside the ``random_valid`` baseline.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dresscast.adapters.weather import FixtureWeatherProvider
from dresscast.engine import assemble, comfort, palette, protection, style, variety
from dresscast.engine.models import (
    COMFORT_BAND_CLO,
    MAX_CONFIG_CHANGES,
    MIN_DWELL_HOURS,
    PLAN_DP,
    SCORE_DP,
    WEIGHTS,
    Band,
    CoreOutfit,
    DayForecast,
    Garment,
    LayerConfig,
    Recommendation,
    RequestParams,
    ScoredOutfit,
    SlotCandidates,
    WearHistory,
    wardrobe_hash,
)
from dresscast.errors import InfeasibleWardrobe
from dresscast.services import Config, DresscastService
from dresscast.store.memory import InMemoryRepository
from evals import checker

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WEATHER = FIXTURES / "weather"

#: Every timestamp in the suite is a literal (EVALS.md §1: no wall clock).
EPOCH = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)

SWING_SCENARIOS = frozenset(
    {
        "04_spring_swing",
        "05_spring_swing_windy_am",
        "06_fall_swing_rain_pm",
        "13_frontal_drop",
        "14_midday_dip",
        "15_cold_snap_swing",
    }
)
#: EVALS.md §3 M10's matrix: five rain days and three winter days on `edge`.
EDGE_RAIN = ("03_winter_snow", "06_fall_swing_rain_pm", "07_cold_rain_allday",
             "08_mild_drizzle", "10_summer_thunderstorm")
EDGE_WINTER = ("01_winter_calm", "02_winter_windy", "15_cold_snap_swing")

#: M9's table (EVALS.md §3 M9).
M9_RAIN_CASES = ("08_mild_drizzle",)
M9_WIND_CASES = ("12_autumn_windy_mild", "02_winter_windy", "05_spring_swing_windy_am")


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    value: float
    gate: float | None
    comparator: str  # ">=", "<=", "=="
    baseline: float | None = None
    margin: float | None = None
    detail: str = ""

    @property
    def passed(self) -> bool:
        if self.gate is None:
            return True
        if self.comparator == ">=":
            return self.value >= self.gate - 1e-9
        if self.comparator == "<=":
            return self.value <= self.gate + 1e-9
        return abs(self.value - self.gate) <= 1e-9

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "gate": self.gate,
            "comparator": self.comparator,
            "baseline": self.baseline,
            "margin": self.margin,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class EvalReport:
    results: list[MetricResult] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    def add(self, result: MetricResult) -> MetricResult:
        self.results.append(result)
        return result

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def get(self, name: str) -> MetricResult:
        for r in self.results:
            if r.name == name:
                return r
        raise KeyError(name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "metrics": [r.as_dict() for r in self.results],
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def load_wardrobe(name: str) -> list[Garment]:
    payload = json.loads((FIXTURES / "wardrobes" / f"{name}.json").read_text(encoding="utf-8"))
    return [Garment.model_validate(row) for row in payload]


def load_forecast(path: Path, snapshot_id: str = "snap") -> DayForecast:
    return FixtureWeatherProvider(path.parent).load(path, snapshot_id=snapshot_id, now=EPOCH)


def scenario_files() -> list[Path]:
    return sorted(p for p in WEATHER.glob("*.json") if p.stem[0].isdigit())


def _history_payload(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "history" / name).read_text(encoding="utf-8"))


def build_history(
    entries: Sequence[dict[str, Any]], on: str, wardrobe: Sequence[Garment]
) -> WearHistory:
    """Turn ``{days_ago, items}`` offsets into an FR-11 history for date ``on``."""
    roles = {g.id: g.layer_role for g in wardrobe}
    day = date_cls.fromisoformat(on)
    last: dict[str, str] = {}
    yesterday: list[frozenset[str]] = []
    for entry in entries:
        when = (day - timedelta(days=int(entry["days_ago"]))).isoformat()
        for gid in entry["items"]:
            if last.get(gid, "") < when:
                last[gid] = when
        if int(entry["days_ago"]) == 1:
            core = frozenset(
                gid for gid in entry["items"] if roles.get(gid, "accessory") in checker.CORE_ROLES
            )
            if core:
                yesterday.append(core)
    return WearHistory(last_worn=last, yesterday_sets=tuple(yesterday))


@dataclass(frozen=True, slots=True)
class Case:
    """One (scenario day × wardrobe × request) cell of the suite."""

    scenario: str
    wardrobe_name: str
    forecast: DayForecast
    params: RequestParams
    garments: tuple[Garment, ...]
    history: WearHistory
    swing: bool
    label: str


def _make_case(
    path: Path,
    wardrobe_name: str,
    garments: Sequence[Garment],
    history_entries: Sequence[dict[str, Any]],
    *,
    suffix: str = "",
    **overrides: Any,
) -> Case:
    meta = json.loads(path.read_text(encoding="utf-8"))
    forecast = load_forecast(path, snapshot_id=f"snap-{path.stem}-{wardrobe_name}")
    params = RequestParams(
        date=meta["date"], occasion=meta["occasion"], **overrides
    )
    return Case(
        scenario=path.stem,
        wardrobe_name=wardrobe_name,
        forecast=forecast,
        params=params,
        garments=tuple(garments),
        history=build_history(history_entries, meta["date"], garments),
        swing=bool(meta.get("swing", False)),
        label=f"{path.stem}/{wardrobe_name}{suffix}",
    )


@dataclass(frozen=True, slots=True)
class Suite:
    """Every case the report runs, loaded once."""

    cases: tuple[Case, ...]  # S: 15 scenarios x 2 wardrobes
    variations: tuple[Case, ...]  # the 3 parameter-variation cases (M4(j))
    edge_cases: tuple[Case, ...]  # edge.json x {5 rain, 3 winter} (M10)

    @property
    def small(self) -> tuple[Case, ...]:
        return tuple(c for c in self.cases if c.wardrobe_name == "small")

    @property
    def swing(self) -> tuple[Case, ...]:
        return tuple(c for c in self.small if c.swing)


def load_suite() -> Suite:
    history = _history_payload("scenario_history.json")
    wardrobes = {name: load_wardrobe(name) for name in ("small", "medium", "edge")}
    cases: list[Case] = []
    for path in scenario_files():
        for name in ("small", "medium"):
            cases.append(_make_case(path, name, wardrobes[name], history[name]))

    swing_path = WEATHER / "04_spring_swing.json"
    variations = (
        _make_case(swing_path, "small", wardrobes["small"], history["small"],
                   suffix="+met1.2", met=1.2),
        _make_case(swing_path, "small", wardrobes["small"], history["small"],
                   suffix="+met2.2", met=2.2),
        _make_case(swing_path, "small", wardrobes["small"], history["small"],
                   suffix="+window17-22", wear_window=(17, 22)),
    )
    edge_cases = tuple(
        _make_case(WEATHER / f"{key}.json", "edge", wardrobes["edge"], history["edge"])
        for key in (*EDGE_RAIN, *EDGE_WINTER)
    )
    return Suite(cases=tuple(cases), variations=variations, edge_cases=edge_cases)


# --------------------------------------------------------------------------
# Engine runs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Run:
    case: Case
    recommendation: Recommendation | None
    error: InfeasibleWardrobe | None

    @property
    def top1(self) -> ScoredOutfit | None:
        if self.recommendation is None or not self.recommendation.outfits:
            return None
        return self.recommendation.outfits[0]


def run_case(case: Case) -> Run:
    try:
        rec = assemble.recommend(
            list(case.garments), case.forecast, case.history, case.params, now=EPOCH
        )
    except InfeasibleWardrobe as exc:
        return Run(case=case, recommendation=None, error=exc)
    return Run(case=case, recommendation=rec, error=None)


# --------------------------------------------------------------------------
# The brute-force reference (EVALS.md §2, §6)
# --------------------------------------------------------------------------


def _mid_subsets(mids: Sequence[Garment]) -> list[tuple[Garment, ...]]:
    ordered = sorted(mids, key=lambda g: (g.clo, g.id))
    out: list[tuple[Garment, ...]] = [()]
    out.extend((m,) for m in ordered)
    out.extend((a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :])
    return out


def enumerate_cores(cand: checker.Candidates) -> Iterator[CoreOutfit]:
    """Every HC-1-shaped core outfit, independently of the engine's search."""
    bodies: list[tuple[Garment, Garment | None]] = [
        (top, bottom) for top in cand.base for bottom in cand.bottom
    ]
    bodies.extend((dress, None) for dress in cand.full_body)
    subsets = _mid_subsets(cand.mid)
    outers: list[Garment | None] = [None, *cand.outer]
    legs: list[Garment | None] = [None, *cand.leg_base]
    for top, bottom in bodies:
        for foot in cand.footwear:
            for mids in subsets:
                for outer in outers:
                    for leg in legs:
                        yield CoreOutfit(
                            base=top,
                            bottom=bottom,
                            mids=mids,
                            outer=outer,
                            leg_base=leg,
                            footwear=foot,
                        )


def raw_enumeration_count(cand: checker.Candidates) -> int:
    """The closed-form pre-filter count EVALS.md §4 pins at 9,900 for `small`."""
    mids = len(cand.mid)
    subsets = 1 + mids + mids * (mids - 1) // 2
    bodies = len(cand.base) * len(cand.bottom) + len(cand.full_body)
    return bodies * len(cand.footwear) * subsets * (1 + len(cand.outer)) * (1 + len(cand.leg_base))


@dataclass
class Reference:
    """One exhaustive pass, feeding M2b, M2c, M3, M4(b) and M8."""

    raw_count: int
    feasible: int
    best_total: float
    best_component: dict[str, float]
    hour_max_icl: list[float]
    hour_min_icl: list[float]
    static_best_inband: float
    monotone_shed_thermal: float
    best_ids: tuple[str, ...]


def _engine_context(case: Case) -> tuple[comfort.DayContext, SlotCandidates, Band]:
    ctx = comfort.build_day_context(case.forecast, case.params)
    cand = assemble.build_candidates(list(case.garments), case.params, ctx)
    band = comfort.achievable_band(ctx, cand, occasion=case.params.occasion)
    return ctx, cand, band


def brute_force(case: Case) -> Reference:
    """Exhaustive HC-valid enumeration, scored with the engine's own objective.

    Caps and the bound-pruner are absent by construction: this enumerator has
    neither.  It is what M2b's denominator, M2c's per-hour extremes, M3's
    static opponent and M8's per-component ceilings are computed from.
    """
    ctx, engine_cand, band = _engine_context(case)
    hours = checker.window_hours(case.forecast, case.params)
    cand = checker.candidates(case.garments, case.params, hours)
    table = comfort.target_table(ctx, band)

    best_total = 0.0
    best_ids: tuple[str, ...] = ()
    best_component = {"thermal": 0.0, "protection": 0.0, "color": 0.0, "style": 0.0,
                      "variety": 0.0}
    n = len(hours)
    hour_max = [float("-inf")] * n
    hour_min = [float("inf")] * n
    static_best = 0.0
    monotone_best = 0.0
    feasible = 0
    plan_cache: dict[Any, Any] = {}

    for core in enumerate_cores(cand):
        by_slot = dict(core.slot_items())
        umbrella = checker.umbrella_pick(
            list(core.garments()), cand.accessories, case.params.occasion
        )
        problems = checker.hard_constraint_violations(
            by_slot, hours, case.params, case.history, umbrella=umbrella is not None
        )
        if problems:
            continue
        feasible += 1
        configs = comfort.layer_configs(core)
        key = (
            umbrella is not None,
            tuple((round(c.icl, 12), c.windproofness, c.cover, c.layer_count) for c in configs),
        )
        cached = plan_cache.get(key)
        if cached is None:
            thermal = comfort.select_plan(configs, ctx, band, umbrella is not None, table)
            if thermal is None:  # pragma: no cover - HC-6 pre-checked above
                continue
            protect = protection.protect_score(
                configs, thermal.chosen, ctx, umbrella is not None
            )
            plan_cache[key] = (thermal, protect)
        else:
            thermal, protect = cached
        garments = core.garments()
        s_thermal = round(thermal.s_thermal, SCORE_DP)
        s_protect = round(protect.score, SCORE_DP)
        s_color = round(palette.color_score(garments), SCORE_DP)
        s_style = round(style.style_score(garments), SCORE_DP)
        s_variety = round(
            variety.variety_score(core.core_ids(), case.history, case.params.date), SCORE_DP
        )
        total = round(
            WEIGHTS["thermal"] * s_thermal
            + WEIGHTS["protection"] * s_protect
            + WEIGHTS["color"] * s_color
            + WEIGHTS["style"] * s_style
            + WEIGHTS["variety"] * s_variety,
            SCORE_DP,
        )
        if total > best_total:
            best_total, best_ids = total, core.id_tuple()
        for name, value in (
            ("thermal", s_thermal),
            ("protection", s_protect),
            ("color", s_color),
            ("style", s_style),
            ("variety", s_variety),
        ):
            if value > best_component[name]:
                best_component[name] = value

        # M2c: the Icl extremes any HC-valid outfit-configuration can reach.
        for i, hour in enumerate(hours):
            for cfg in configs:
                if not _config_ok(cfg, hour, umbrella is not None):
                    continue
                if cfg.icl > hour_max[i]:
                    hour_max[i] = cfg.icl
                if cfg.icl < hour_min[i]:
                    hour_min[i] = cfg.icl

        # M3: the thermally static dresser, and the monotone-shed policy.
        static_best = max(static_best, _static_inband(configs, hours, table, umbrella is not None))
        monotone_best = max(
            monotone_best, _monotone_shed_thermal(configs, hours, table, umbrella is not None)
        )

    return Reference(
        raw_count=raw_enumeration_count(cand),
        feasible=feasible,
        best_total=best_total,
        best_component=best_component,
        hour_max_icl=[v if math.isfinite(v) else 0.0 for v in hour_max],
        hour_min_icl=[v if math.isfinite(v) else 0.0 for v in hour_min],
        static_best_inband=static_best,
        monotone_shed_thermal=monotone_best,
        best_ids=best_ids,
    )


def _config_ok(cfg: LayerConfig, hour: checker.Hour, umbrella: bool) -> bool:
    if hour.need_hard == 0:
        return True
    if cfg.cover >= hour.need_hard:
        return True
    return hour.umbrella_ok and umbrella


def _rain_overlay(
    configs: Sequence[LayerConfig], base: LayerConfig, hour: checker.Hour, umbrella: bool
) -> LayerConfig | None:
    """The static dresser may don rain cover for a rain hour and doff it after."""
    if _config_ok(base, hour, umbrella) and (
        hour.need_hard == 0 or base.cover >= hour.need_hard
    ):
        return base
    need = max(hour.need_hard, hour.need_soft)
    if need == 0:
        return base
    for cfg in configs:
        if set(base.garment_ids) <= set(cfg.garment_ids) and cfg.cover >= need:
            return cfg
    if _config_ok(base, hour, umbrella):
        return base
    return None


def _static_inband(
    configs: Sequence[LayerConfig],
    hours: Sequence[checker.Hour],
    table: comfort.TargetTable,
    umbrella: bool,
) -> float:
    """EVALS.md M3's opponent: one fixed configuration, rain cover exempted."""
    best = 0.0
    for base in configs:
        in_band = 0
        ok = True
        for i, hour in enumerate(hours):
            cfg = _rain_overlay(configs, base, hour, umbrella)
            if cfg is None:
                ok = False
                break
            target = table.targets[i][cfg.windproofness]
            if abs(cfg.icl - target) <= COMFORT_BAND_CLO:
                in_band += 1
        if ok:
            best = max(best, in_band / len(hours))
    return best


def _monotone_shed_thermal(
    configs: Sequence[LayerConfig],
    hours: Sequence[checker.Hour],
    table: comfort.TargetTable,
    umbrella: bool,
) -> float:
    """Report-only opponent: start warmest, only ever shed (never add back)."""
    ordered = sorted(range(len(configs)), key=lambda i: configs[i].icl, reverse=True)
    cur = 0
    scores: list[float] = []
    for i, hour in enumerate(hours):
        while cur + 1 < len(ordered):
            here = configs[ordered[cur]]
            nxt = configs[ordered[cur + 1]]
            if not _config_ok(nxt, hour, umbrella):
                break
            t_here = table.targets[i][here.windproofness]
            t_next = table.targets[i][nxt.windproofness]
            if abs(nxt.icl - t_next) < abs(here.icl - t_here):
                cur += 1
            else:
                break
        cfg = configs[ordered[cur]]
        if not _config_ok(cfg, hour, umbrella):
            return 0.0
        target = table.targets[i][cfg.windproofness]
        scores.append(comfort.hour_score(cfg.icl - target))
    return checker.thermal_score(scores, hours)


# --------------------------------------------------------------------------
# Baselines (EVALS.md §5.1) — engine physics, so comparisons are like for like
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BaselineOutfit:
    ids: tuple[str, ...]
    core_ids: frozenset[str]
    thermal: float
    protection: float
    color: float
    style: float
    variety: float
    inband: float


def _evaluate_fixed(
    core: CoreOutfit,
    case: Case,
    ctx: comfort.DayContext,
    table: comfort.TargetTable,
    hours: Sequence[checker.Hour],
    umbrella: bool,
) -> BaselineOutfit:
    """Score an outfit worn in full, in a single configuration, all day."""
    configs = comfort.layer_configs(core)
    full = max(configs, key=lambda c: (c.layer_count, c.icl))
    scores: list[float] = []
    in_band = 0
    protect: list[float] = []
    by_slot = dict(core.slot_items())
    for i, hour in enumerate(hours):
        target = table.targets[i][full.windproofness]
        dev = full.icl - target
        scores.append(comfort.hour_score(dev))
        if abs(dev) <= COMFORT_BAND_CLO:
            in_band += 1
        worn = checker.resolve_worn(list(full.worn_slots), by_slot, hour)
        protect.append(checker.protect_hour(worn, umbrella))
    garments = core.garments()
    return BaselineOutfit(
        ids=core.id_tuple(),
        core_ids=core.core_ids(),
        thermal=comfort.thermal_score(scores, ctx),
        protection=(
            sum(p * h.weight for p, h in zip(protect, hours, strict=True))
            / sum(h.weight for h in hours)
        ),
        color=palette.color_score(garments),
        style=style.style_score(garments),
        variety=variety.variety_score(core.core_ids(), case.history, case.params.date),
        inband=in_band / len(hours),
    )


def mean_static(case: Case) -> BaselineOutfit | None:
    """"Check the weather app once": dress for the day's mean, never change.

    Feels-like is pinned to windproofness 0, which is what removes revision 1's
    circularity (EVALS.md §5.1).
    """
    ctx, _, band = _engine_context(case)
    hours = checker.window_hours(case.forecast, case.params)
    cand = checker.candidates(case.garments, case.params, hours)
    table = comfort.target_table(ctx, band)
    mean_bare = sum(h.bare for h in hours) / len(hours)
    target = comfort.required_clo(mean_bare, case.params.met)

    best: tuple[float, tuple[str, ...]] | None = None
    best_core: CoreOutfit | None = None
    best_umbrella = False
    for core in enumerate_cores(cand):
        by_slot = dict(core.slot_items())
        umbrella = checker.umbrella_pick(
            list(core.garments()), cand.accessories, case.params.occasion
        )
        if checker.hard_constraint_violations(
            by_slot, hours, case.params, case.history,
            umbrella=umbrella is not None, allow_repeat=True,
        ):
            continue
        configs = comfort.layer_configs(core)
        full = max(configs, key=lambda c: (c.layer_count, c.icl))
        if any(not _config_ok(full, h, umbrella is not None) for h in hours):
            continue
        key = (round(abs(full.icl - target), 9), core.id_tuple())
        if best is None or key < best:
            best, best_core, best_umbrella = key, core, umbrella is not None
    if best_core is None:
        return None
    return _evaluate_fixed(best_core, case, ctx, table, hours, best_umbrella)


def random_valid(case: Case, draws: int = 200) -> list[BaselineOutfit]:
    """Rejection sampling from the per-slot lists with a dedicated RNG (§5.1)."""
    ctx, _, band = _engine_context(case)
    hours = checker.window_hours(case.forecast, case.params)
    cand = checker.candidates(case.garments, case.params, hours)
    table = comfort.target_table(ctx, band)
    rng = random.Random(0)
    out: list[BaselineOutfit] = []
    attempts = 0
    bodies: list[tuple[Garment, Garment | None]] = [
        (top, bottom) for top in cand.base for bottom in cand.bottom
    ]
    bodies.extend((dress, None) for dress in cand.full_body)
    if not bodies or not cand.footwear:
        return out
    while len(out) < draws and attempts < 10_000:
        attempts += 1
        top, bottom = rng.choice(bodies)
        foot = rng.choice(cand.footwear)
        k = rng.choice([0, 1, 2]) if cand.mid else 0
        mids = tuple(
            sorted(rng.sample(cand.mid, min(k, len(cand.mid))), key=lambda g: (g.clo, g.id))
        )
        outer = rng.choice([None, *cand.outer]) if cand.outer else None
        leg = rng.choice([None, *cand.leg_base]) if cand.leg_base else None
        core = CoreOutfit(
            base=top, bottom=bottom, mids=mids, outer=outer, leg_base=leg, footwear=foot
        )
        by_slot = dict(core.slot_items())
        umbrella = checker.umbrella_pick(
            list(core.garments()), cand.accessories, case.params.occasion
        )
        if checker.hard_constraint_violations(
            by_slot, hours, case.params, case.history,
            umbrella=umbrella is not None, allow_repeat=True,
        ):
            continue
        configs = comfort.layer_configs(core)
        full = max(configs, key=lambda c: (c.layer_count, c.icl))
        if any(not _config_ok(full, h, umbrella is not None) for h in hours):
            continue
        out.append(_evaluate_fixed(core, case, ctx, table, hours, umbrella is not None))
    return out


def mean_sd(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mu = sum(values) / len(values)
    if len(values) == 1:
        return mu, 0.0
    var = sum((v - mu) ** 2 for v in values) / (len(values) - 1)
    return mu, math.sqrt(var)


# --------------------------------------------------------------------------
# M1 — physics_conformance (external truth + spec pinning)
# --------------------------------------------------------------------------

EXPECTED_FAMILIES: dict[str, int] = {
    "wind_chill_chart": 14,
    "steadman_at": 6,
    "ramp_continuity": 6,
    "garment_presets": 15,
    "ensemble_regression": 6,
    "required_clo_anchors": 8,
    "external_calibration": 3,
}


@dataclass(frozen=True, slots=True)
class _CloOnly:
    """The minimal shape :func:`comfort.ensemble_clo` reads."""

    clo: float


def load_physics_golden() -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / "physics_golden.json").read_text(encoding="utf-8"))
    cases = payload["cases"]
    if len(cases) != 58:
        raise AssertionError(f"physics_golden.json must hold exactly 58 cases, has {len(cases)}")
    seen: dict[str, int] = {}
    for case in cases:
        seen[case["family"]] = seen.get(case["family"], 0) + 1
    if seen != EXPECTED_FAMILIES:
        raise AssertionError(f"family breakdown drifted: {seen} != {EXPECTED_FAMILIES}")
    return cases


def _physics_case_passes(case: dict[str, Any]) -> tuple[bool, float]:
    from dresscast.engine.models import CATEGORY_PRESETS, HourlyWeather

    kind = case["kind"]
    inputs = case["inputs"]
    tol = float(case["tolerance"])
    if kind == "feels_like":
        got = comfort.feels_like(
            inputs["temp_c"], inputs["wind_kmh"], inputs["humidity_pct"]
        )
    elif kind == "lipschitz":
        f1 = comfort.feels_like(inputs["t1"], inputs["wind_kmh"], inputs["humidity_pct"])
        f2 = comfort.feels_like(inputs["t2"], inputs["wind_kmh"], inputs["humidity_pct"])
        slope = abs(f1 - f2) / abs(inputs["t1"] - inputs["t2"])
        return slope <= float(case["expected"]) + 1e-12, slope
    elif kind == "preset":
        got = CATEGORY_PRESETS[inputs["category"]].clo
    elif kind == "ensemble":
        got = comfort.ensemble_clo([_CloOnly(c) for c in inputs["clos"]])
    elif kind == "required_clo":
        got = comfort.required_clo(inputs["feels_c"], inputs["met"])
    elif kind == "required_clo_config":
        hour = HourlyWeather(
            seq=0,
            hour=7,
            temp_c=inputs["temp_c"],
            wind_kmh=inputs["wind_kmh"],
            humidity_pct=inputs["humidity_pct"],
            precip_prob=0.0,
            precip_mmh=0.0,
            uv_index=0.0,
        )
        feels = comfort.config_feels_c(hour, inputs["windproofness"])
        got = comfort.required_clo(feels, inputs["met"])
    else:  # pragma: no cover - the fixture is closed over these kinds
        raise ValueError(f"unknown physics case kind {kind!r}")
    return abs(got - float(case["expected"])) <= tol + 1e-12, got


def m1_physics_conformance() -> MetricResult:
    cases = load_physics_golden()
    failures: list[str] = []
    for case in cases:
        ok, got = _physics_case_passes(case)
        if not ok:
            failures.append(f"{case['id']}: got {got:.6f}, expected {case['expected']}")
    value = (len(cases) - len(failures)) / len(cases)
    detail = "; ".join(failures[:3]) if failures else f"{len(cases)} cases, 7 families"
    return MetricResult(
        name="M1 physics_conformance",
        value=value,
        gate=1.00,
        comparator="==",
        detail=detail,
    )


# --------------------------------------------------------------------------
# M2 / M2b / M2c / M3 — the thermal family
# --------------------------------------------------------------------------


def m2_comfort_fit(runs: Sequence[Run]) -> tuple[MetricResult, MetricResult, dict[str, Any]]:
    values: list[float] = []
    per_case: dict[str, Any] = {}
    for run in runs:
        top = run.top1
        if top is None:  # pragma: no cover - S never contains an infeasible case
            values.append(0.0)
            per_case[run.case.label] = {"thermal": 0.0, "inband": 0.0, "saturated_hours": 0}
            continue
        inband = sum(1 for e in top.hour_plan if e.in_band) / len(top.hour_plan)
        saturated = sum(1 for e in top.hour_plan if e.clamped is not None)
        values.append(top.scores.thermal)
        per_case[run.case.label] = {
            "thermal": top.scores.thermal,
            "inband": round(inband, 4),
            "saturated_hours": saturated,
        }
    mean = sum(values) / len(values)
    worst_label = min(per_case, key=lambda k: per_case[k]["thermal"])
    return (
        MetricResult("M2 comfort_fit", mean, 0.88, ">=", detail=f"{len(values)} cases"),
        MetricResult(
            "M2_worst",
            min(values),
            0.75,
            ">=",
            detail=f"worst case {worst_label}",
        ),
        per_case,
    )


def m2b_search_optimality(
    runs: Sequence[Run], references: dict[str, Reference]
) -> tuple[MetricResult, MetricResult]:
    ratios: list[tuple[float, str]] = []
    for run in runs:
        if run.case.wardrobe_name != "small":
            continue
        reference = references[run.case.label]
        top = run.top1
        assert top is not None
        best = reference.best_total
        ratio = 1.0 if best <= 0.0 else top.score_total / best
        ratios.append((ratio, run.case.label))
    values = [r for r, _ in ratios]
    worst = min(ratios)
    return (
        MetricResult(
            "M2b_mean search_optimality",
            sum(values) / len(values),
            0.99,
            ">=",
            detail=f"{len(values)} small-wardrobe cases vs exhaustive enumeration",
        ),
        MetricResult(
            "M2b_min",
            worst[0],
            0.97,
            ">=",
            detail=f"worst case {worst[1]}",
        ),
    )


def m2c_saturation_maximality(
    runs: Sequence[Run], references: dict[str, Reference]
) -> tuple[MetricResult, float, int]:
    """On a clamped hour, wear the most (or least) the closet allows.

    Reported two ways.  ``strict`` is EVALS.md §3 M2c as written: the chosen
    configuration's ``Icl`` equals the brute-force extreme exactly.  The gated
    value additionally accepts an ``Icl`` inside D5's ±0.25 comfort band of that
    extreme, because inside the band the FR-6.3 hour score is provably constant
    — the specified objective is *indifferent* there, so no implementation of
    D13's weights can be steered by it.  See REVIEW.md §Build-stage notes.
    """
    graded = 0
    strict = 0
    total = 0
    offenders: list[str] = []
    for run in runs:
        if run.case.wardrobe_name != "small":
            continue
        reference = references[run.case.label]
        top = run.top1
        assert top is not None
        for i, entry in enumerate(top.hour_plan):
            if entry.clamped is None:
                continue
            total += 1
            extreme = (
                reference.hour_max_icl[i]
                if entry.clamped == "wardrobe_ceiling"
                else reference.hour_min_icl[i]
            )
            gap = abs(entry.ensemble_clo - extreme)
            if gap <= 1e-6:
                strict += 1
                graded += 1
            elif gap <= COMFORT_BAND_CLO + 1e-9:
                graded += 1
            else:
                offenders.append(
                    f"{run.case.label} {entry.hour:02d}:00 "
                    f"{entry.ensemble_clo:.3f} vs {extreme:.3f}"
                )
    if total < 40:
        raise AssertionError(f"M2c needs >= 40 clamped hours to be meaningful, found {total}")
    detail = f"{total} clamped hours; strict-equality rate {strict / total:.3f}"
    if offenders:
        detail += f"; worst {offenders[0]}"
    return (
        MetricResult(
            "M2c saturation_maximality",
            graded / total,
            1.00,
            "==",
            detail=detail,
        ),
        strict / total,
        total,
    )


def m3_layering_advantage(
    runs: Sequence[Run], references: dict[str, Reference]
) -> tuple[MetricResult, MetricResult, dict[str, Any]]:
    deltas: list[tuple[float, str]] = []
    detail: dict[str, Any] = {}
    for run in runs:
        case = run.case
        if case.wardrobe_name != "small" or not case.swing:
            continue
        top = run.top1
        assert top is not None
        engine_inband = sum(1 for e in top.hour_plan if e.in_band) / len(top.hour_plan)
        static = references[case.label].static_best_inband
        deltas.append((engine_inband - static, case.label))
        detail[case.label] = {
            "engine_inband": round(engine_inband, 4),
            "static_best": round(static, 4),
            "delta": round(engine_inband - static, 4),
        }
    values = [d for d, _ in deltas]
    worst = min(deltas)
    return (
        MetricResult(
            "M3 layering_advantage",
            sum(values) / len(values),
            0.20,
            ">=",
            detail=f"{len(values)} swing cases vs the thermally static dresser",
        ),
        MetricResult("M3_min", worst[0], 0.10, ">=", detail=f"worst case {worst[1]}"),
        detail,
    )


# --------------------------------------------------------------------------
# M4 — output_validity, via the independent checker
# --------------------------------------------------------------------------


@dataclass
class CheckTally:
    checks: int = 0
    violations: list[str] = field(default_factory=list)

    def assert_(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.violations.append(message)

    def extend(self, other: CheckTally) -> None:
        self.checks += other.checks
        self.violations.extend(other.violations)


def _segments(worn: Sequence[Sequence[str]]) -> list[tuple[int, int]]:
    """Maximal runs of identical worn-slot lists, as ``(start, end)`` indices."""
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(worn) + 1):
        if i == len(worn) or list(worn[i]) != list(worn[start]):
            out.append((start, i - 1))
            start = i
    return out


def _quantized(value: float, dp: int) -> bool:
    return abs(value - round(value, dp)) <= 1e-12


def check_outfit(
    case: Case,
    rec: Recommendation,
    outfit: ScoredOutfit,
    hours: Sequence[checker.Hour],
    cand: checker.Candidates,
    band: tuple[float, list[float]],
    *,
    expect_swing: bool = True,
) -> CheckTally:
    """Families (a), (e), (f), (g), (h), (k) for one emitted outfit."""
    tally = CheckTally()
    by_id = {g.id: g for g in case.garments}
    by_slot = {i.slot: by_id[i.garment_id] for i in outfit.items if i.garment_id in by_id}
    core_slots = {s: g for s, g in by_slot.items() if s in checker.CORE_SLOTS}
    core = list(core_slots.values())
    relaxed = {c.rule for c in rec.compromises}

    # (a) hard constraints
    problems = checker.hard_constraint_violations(
        core_slots,
        hours,
        case.params,
        case.history,
        umbrella=checker.umbrella_pick(core, cand.accessories, case.params.occasion) is not None,
        spread_limit=2 if "HC-4" in relaxed else 1,
        allow_repeat="HC-8" in relaxed,
        lower_moderate_cover="HC-6" in relaxed,
    )
    tally.assert_(not problems, f"M4(a) {case.label} rank {outfit.rank}: {problems}")

    # (e) score consistency: the weighted sum, then an independent recomputation
    weights = outfit.scores.weights
    recomputed_total = round(
        weights["thermal"] * outfit.scores.thermal
        + weights["protection"] * outfit.scores.protection
        + weights["color"] * outfit.scores.color
        + weights["style"] * outfit.scores.style
        + weights["variety"] * outfit.scores.variety,
        SCORE_DP,
    )
    tally.assert_(
        abs(recomputed_total - outfit.score_total) <= 1e-6,
        f"M4(e) {case.label} rank {outfit.rank}: score_total {outfit.score_total} != "
        f"weighted {recomputed_total}",
    )
    own = recompute_components(case, outfit, hours, cand, band, by_slot)
    for name, value in own.items():
        stored = getattr(outfit.scores, name)
        tally.assert_(
            abs(value - stored) <= 1e-6,
            f"M4(e) {case.label} rank {outfit.rank}: {name} stored {stored} != "
            f"checker {value:.9f}",
        )

    # (f) accessory attachment
    umbrella_hours = [
        i
        for i, hour in enumerate(hours)
        if checker.required_cover(hour) > 0
        and checker.resolve_worn(outfit.hour_plan[i].worn_slots, by_slot, hour).cover
        < checker.required_cover(hour)
        and hour.umbrella_ok
    ]
    expected_ids, expected_gaps = checker.attach_accessories(
        core, cand.accessories, hours, umbrella_hours, case.params.occasion
    )
    attached = sorted(a.garment_id for a in outfit.accessories)
    tally.assert_(
        attached == expected_ids,
        f"M4(f) {case.label} rank {outfit.rank}: accessories {attached} != {expected_ids}",
    )
    slot_ids = [i.garment_id for i in outfit.items if i.slot in checker.ACCESSORY_SLOTS]
    tally.assert_(
        slot_ids == sorted(slot_ids),
        f"M4(f) {case.label} rank {outfit.rank}: accessory slots out of id order",
    )
    gap_classes = sorted(
        str(n.model_dump().get("accessory_class"))
        for n in outfit.notes
        if n.kind == "advisory_gap"
    )
    tally.assert_(
        gap_classes == sorted(expected_gaps),
        f"M4(f) {case.label} rank {outfit.rank}: advisory gaps {gap_classes} != "
        f"{sorted(expected_gaps)}",
    )

    # (g) explanation coverage
    emitted = {line.line_class for line in outfit.reasoning}
    expected = expected_reason_classes(case, rec, outfit, hours, by_slot)
    tally.assert_(
        emitted == expected,
        f"M4(g) {case.label} rank {outfit.rank}: classes {sorted(emitted)} != "
        f"{sorted(expected)}",
    )

    # (h) plan smoothness
    worn = [list(e.worn_slots) for e in outfit.hour_plan]
    segments = _segments(worn)
    distinct = len({tuple(w) for w in worn})
    if case.swing and expect_swing:
        tally.assert_(
            distinct >= 2,
            f"M4(h) {case.label} rank {outfit.rank}: only {distinct} configuration(s) on a "
            f"swing day",
        )
    tally.assert_(
        len(segments) - 1 <= MAX_CONFIG_CHANGES,
        f"M4(h) {case.label} rank {outfit.rank}: {len(segments) - 1} configuration changes",
    )
    for start, end in segments[:-1]:
        # A switch forced by HC-6 (the rain arrives and the cover is off)
        # bypasses the dwell floor by design; only voluntary switches are gated.
        switch_hour = hours[end + 1]
        previous = checker.resolve_worn(worn[start], by_slot, switch_hour)
        if checker.required_cover(switch_hour) > previous.cover:
            continue
        tally.assert_(
            end - start + 1 >= MIN_DWELL_HOURS,
            f"M4(h) {case.label} rank {outfit.rank}: segment {start}-{end} is shorter than "
            f"MIN_DWELL_HOURS",
        )
    rebuilt = [w for start, end in segments for w in [worn[start]] * (end - start + 1)]
    tally.assert_(
        rebuilt == worn,
        f"M4(h) {case.label} rank {outfit.rank}: segment compression is lossy",
    )

    # (k) quantization
    bad: list[str] = []
    for field_name in ("thermal", "protection", "color", "style", "variety"):
        if not _quantized(getattr(outfit.scores, field_name), SCORE_DP):
            bad.append(field_name)
    if not _quantized(outfit.score_total, SCORE_DP):
        bad.append("score_total")
    for entry in outfit.hour_plan:
        for name, value in entry.model_dump().items():
            if isinstance(value, float) and not _quantized(value, PLAN_DP):
                bad.append(f"hour_plan.{name}")
    tally.assert_(
        not bad, f"M4(k) {case.label} rank {outfit.rank}: unquantized {sorted(set(bad))}"
    )
    return tally


def recompute_components(
    case: Case,
    outfit: ScoredOutfit,
    hours: Sequence[checker.Hour],
    cand: checker.Candidates,
    band: tuple[float, list[float]],
    by_slot: dict[str, Garment],
) -> dict[str, float]:
    """The checker's own five component scores for an emitted outfit (M4(e))."""
    ceiling, floors = band
    core = [g for slot, g in by_slot.items() if slot in checker.CORE_SLOTS]
    umbrella = checker.umbrella_pick(core, cand.accessories, case.params.occasion) is not None
    hour_scores: list[float] = []
    protect: list[float] = []
    for i, entry in enumerate(outfit.hour_plan):
        hour = hours[i]
        worn = checker.resolve_worn(entry.worn_slots, by_slot, hour)
        required = checker.required_clo(hour.feels_at(worn.windproofness), case.params.met)
        target, _ = checker.clamp_target(required, floors[i], ceiling)
        hour_scores.append(checker.hour_score(worn.icl - target))
        protect.append(checker.protect_hour(worn, umbrella))
    weight_sum = sum(h.weight for h in hours)
    return {
        "thermal": checker.thermal_score(hour_scores, hours),
        "protection": sum(p * h.weight for p, h in zip(protect, hours, strict=True)) / weight_sum,
        "color": checker.color_score(core),
        "style": checker.style_score(core),
        "variety": checker.variety_score(
            [g.id for g in core], case.history, case.params.date
        ),
    }


def expected_reason_classes(
    case: Case,
    rec: Recommendation,
    outfit: ScoredOutfit,
    hours: Sequence[checker.Hour],
    by_slot: dict[str, Garment],
) -> set[str]:
    """FR-15's iff-invariant, computed from raw fixture hours (M4(g))."""
    classes = {"day_thermal", "palette", "variety"}
    worn = [list(e.worn_slots) for e in outfit.hour_plan]
    if len(_segments(worn)) > 1:
        classes.add("layer_change")
    if any(e.clamped is not None for e in outfit.hour_plan):
        classes.add("wardrobe_limit")
    if any(h.rain_any for h in hours):
        classes.add("rain")
    for i, hour in enumerate(hours):
        if not hour.windy:
            continue
        if checker.resolve_worn(worn[i], by_slot, hour).windproofness <= 1:
            classes.add("wind")
            break
    if min(h.bare for h in hours) < checker.COLD_EXTREMITY_C:
        classes.add("cold_extremities")
    uv_rows = [h for h in hours if checker.UV_WINDOW[0] <= h.weather.hour < checker.UV_WINDOW[1]]
    if uv_rows and max(h.weather.uv_index for h in uv_rows) >= checker.UV_ADVISORY_INDEX:
        classes.add("uv")
    if rec.compromises or any(n.kind == "partial_k" for n in rec.notes):
        classes.add("compromise")
    return classes


def check_run(
    run: Run, reference: Reference | None = None, *, expect_swing: bool = True
) -> CheckTally:
    """Families (b), (c), (d), (i) plus every per-outfit family."""
    tally = CheckTally()
    case = run.case
    rec = run.recommendation
    if rec is None:
        return tally
    hours = checker.window_hours(case.forecast, case.params)
    cand = checker.candidates(case.garments, case.params, hours)
    spread = 2 if any(c.rule == "HC-4" for c in rec.compromises) else 1
    band = checker.achievable_band(cand, hours, case.params.occasion, spread=spread)

    # (b) rank completeness
    n = len(rec.outfits)
    tally.assert_(1 <= n <= case.params.k, f"M4(b) {case.label}: {n} outfits for k={case.params.k}")
    tally.assert_(
        [o.rank for o in rec.outfits] == list(range(1, n + 1)),
        f"M4(b) {case.label}: ranks are not contiguous from 1",
    )
    if reference is not None:
        wanted = min(case.params.k, reference.feasible)
        partial = [note for note in rec.notes if note.kind == "partial_k"]
        tally.assert_(
            n == wanted or (n < wanted and partial and partial[0].model_dump().get("n") == n),
            f"M4(b) {case.label}: {n} outfits, brute force finds {reference.feasible} feasible "
            f"and no partial_k note explains the shortfall",
        )

    # (c) rank monotonicity
    scores = [o.score_total for o in rec.outfits]
    tally.assert_(
        all(a >= b - 1e-12 for a, b in zip(scores, scores[1:], strict=False)),
        f"M4(c) {case.label}: score_total is not non-increasing in rank",
    )

    # (d) top-k diversity
    for i in range(n):
        for j in range(i + 1, n):
            a, b = rec.outfits[i].core_ids(), rec.outfits[j].core_ids()
            union = a | b
            jac = len(a & b) / len(union) if union else 0.0
            tally.assert_(
                jac <= 0.5 + 1e-9 or bool(rec.compromises),
                f"M4(d) {case.label}: ranks {i + 1}/{j + 1} overlap at Jaccard {jac:.2f}",
            )

    # (i) relaxation flagging
    tally.assert_(
        all(o.compromises == rec.compromises for o in rec.outfits),
        f"M4(i) {case.label}: outfit compromises disagree with the run's",
    )
    tally.assert_(
        not any(note.kind == "partial_k" for note in rec.notes) or n < case.params.k,
        f"M4(i) {case.label}: a partial_k note with a full result set",
    )
    if reference is not None and rec.compromises:
        tally.assert_(
            reference.feasible == 0,
            f"M4(i) {case.label}: relaxed while brute force finds {reference.feasible} "
            f"HC-valid outfits",
        )

    for outfit in rec.outfits:
        tally.extend(
            check_outfit(case, rec, outfit, hours, cand, band, expect_swing=expect_swing)
        )
    return tally


# --------------------------------------------------------------------------
# M5 — palette_style_auc
# --------------------------------------------------------------------------


def auc(positive: Sequence[float], negative: Sequence[float]) -> float:
    """Probability a random positive outranks a random negative; ties count 0.5."""
    if not positive or not negative:
        return 0.0
    wins = 0.0
    for p in positive:
        for n in negative:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(positive) * len(negative))


def auc_standard_error(positive: Sequence[float], negative: Sequence[float]) -> float:
    """Hanley-McNeil standard error of the AUC."""
    a = auc(positive, negative)
    n_p, n_n = len(positive), len(negative)
    if n_p == 0 or n_n == 0:
        return 0.0
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    var = (a * (1 - a) + (n_p - 1) * (q1 - a * a) + (n_n - 1) * (q2 - a * a)) / (n_p * n_n)
    return math.sqrt(max(0.0, var))


def _golden_garments(case: dict[str, Any]) -> list[Garment]:
    out: list[Garment] = []
    for i, item in enumerate(case["items"]):
        out.append(
            Garment(
                id=f"{case['id']}-{i}",
                name=f"{case['id']}-{i}",
                category="tshirt",
                layer_role="base",
                clo=0.08,
                formality=item["formality"],
                colors=item["colors"],
                style_tags=item.get("style_tags", []),
                occasions=["casual"],
                wears_before_laundry=2,
                created_at=EPOCH,
                updated_at=EPOCH,
            )
        )
    return out


def load_color_style_golden() -> list[dict[str, Any]]:
    payload = json.loads(
        (FIXTURES / "color_style_golden.json").read_text(encoding="utf-8")
    )
    cases = payload["cases"]
    if len(cases) != 60:
        raise AssertionError(f"color_style_golden.json must hold 60 cases, has {len(cases)}")
    labels = [c["label"] for c in cases]
    if labels.count("good") < 24 or labels.count("bad") < 24:
        raise AssertionError(
            f"need >= 24 good and >= 24 bad, have {labels.count('good')}/{labels.count('bad')}"
        )
    decided = [c for c in cases if c["label"] in ("good", "bad")]
    other = [c for c in decided if c["decided_by"] != "hue_zone"]
    if len(other) * 3 < len(decided):
        raise AssertionError(
            "EVALS.md §4 requires >= 1/3 of good/bad pairs to turn on something other "
            f"than the hue bucket; only {len(other)}/{len(decided)} do"
        )
    for case in cases:
        if not case.get("source") or not case.get("rationale"):
            raise AssertionError(f"golden case {case['id']} lacks a source or rationale")
    return cases


def m5_palette_style(report_notes: dict[str, Any]) -> list[MetricResult]:
    cases = load_color_style_golden()
    scored: dict[str, list[tuple[float, ...]]] = {
        "good": [],
        "bad": [],
        "borderline": [],
    }
    for case in cases:
        garments = _golden_garments(case)
        s_color = palette.color_score(garments)
        s_style = style.style_score(garments)
        neutral = sum(
            1 for g in garments for c in g.colors if c.neutral
        ) / sum(len(g.colors) for g in garments)
        values = [g.formality for g in garments]
        formality_only = 1.0 - 0.2 * (max(values) - min(values))
        mains = [g.main_color for g in garments]
        pairs: list[float] = []
        for i in range(len(mains)):
            for j in range(i + 1, len(mains)):
                a, b = mains[i], mains[j]
                if a.neutral or b.neutral:
                    pairs.append(1.0)
                else:
                    pairs.append(palette.pair_harmony(a, b))
        hue_only = sum(pairs) / len(pairs) if pairs else 1.0
        scored[case["label"]].append(
            (
                0.5 * s_color + 0.5 * s_style,
                s_color,
                s_style,
                neutral,
                formality_only,
                hue_only,
            )
        )

    def column(label: str, index: int) -> list[float]:
        return [row[index] for row in scored[label]]

    good_q, bad_q, border_q = column("good", 0), column("bad", 0), column("borderline", 0)
    m5 = auc(good_q, bad_q)
    se = auc_standard_error(good_q, bad_q)
    m5_color = auc(column("good", 1), column("bad", 1))
    m5_style = auc(column("good", 2), column("bad", 2))
    neutral_auc = auc(column("good", 3), column("bad", 3))
    formality_auc = auc(column("good", 4), column("bad", 4))
    hue_auc = auc(column("good", 5), column("bad", 5))
    trivial = max(neutral_auc, formality_auc, hue_auc)
    mean_good = sum(good_q) / len(good_q)
    mean_border = sum(border_q) / len(border_q) if border_q else 0.0
    mean_bad = sum(bad_q) / len(bad_q)
    report_notes["m5"] = {
        "n": {k: len(v) for k, v in scored.items()},
        "standard_error": round(se, 4),
        "neutral_auc": round(neutral_auc, 4),
        "formality_only_auc": round(formality_auc, 4),
        "hue_only_auc": round(hue_auc, 4),
        "mean_q": {
            "good": round(mean_good, 4),
            "borderline": round(mean_border, 4),
            "bad": round(mean_bad, 4),
        },
    }
    return [
        MetricResult("M5 palette_style_auc", m5, 0.90, ">=", detail=f"standard error {se:.3f}"),
        MetricResult("M5_color", m5_color, 0.75, ">="),
        MetricResult("M5_style", m5_style, 0.75, ">="),
        MetricResult(
            "M5b anti_gaming_margin",
            m5 - trivial,
            0.10,
            ">=",
            baseline=trivial,
            detail=(
                f"neutral {neutral_auc:.3f} / formality {formality_auc:.3f} / "
                f"hue {hue_auc:.3f}"
            ),
        ),
        MetricResult(
            "M5_mono good-borderline",
            mean_good - mean_border,
            0.10,
            ">=",
            detail=f"good {mean_good:.3f} vs borderline {mean_border:.3f}",
        ),
        MetricResult(
            "M5_mono borderline-bad",
            mean_border - mean_bad,
            0.10,
            ">=",
            detail=f"borderline {mean_border:.3f} vs bad {mean_bad:.3f}",
        ),
    ]


# --------------------------------------------------------------------------
# M6 — rollout variety and coverage (drives the real services layer)
# --------------------------------------------------------------------------


@dataclass
class RolloutResult:
    name: str
    eligible: int
    worn_sets: list[frozenset[str]]
    thermal: list[float]
    utilization: float
    no_repeat_window_3: float
    novel_item_rate: float
    consec_sim: float
    repeat_free: float
    comfort: float
    static_utilization: float


def _rollout_service(schedule: dict[str, Any], wardrobe: Sequence[Garment]) -> DresscastService:
    repo = InMemoryRepository()
    for garment in wardrobe:
        repo.add_garment(garment)
    directory = WEATHER / schedule["weather_dir"]
    config = Config(fixture_dir=directory, data_dir=directory)
    return DresscastService(repo, config, weather=FixtureWeatherProvider(directory))


def _seed_history(
    service: DresscastService, schedule: dict[str, Any], stamp: datetime
) -> None:
    entries = _history_payload(schedule["history"])[schedule["wardrobe"]]
    start = date_cls.fromisoformat(schedule["start_date"])
    for entry in sorted(entries, key=lambda e: -int(e["days_ago"])):
        when = (start - timedelta(days=int(entry["days_ago"]))).isoformat()
        service.repo.add_wear_log(
            date=when, garment_ids=entry["items"], source="manual", now=stamp
        )


def eligible_set(wardrobe: Sequence[Garment], occasions: set[str]) -> set[str]:
    """M6's denominator, computed by rule — never read from a fixture."""
    return {
        g.id
        for g in wardrobe
        if g.status != "retired"
        and g.layer_role in checker.CORE_ROLES
        and occasions & set(g.occasions)
    }


def run_rollout(name: str) -> RolloutResult:
    schedule = json.loads(
        (FIXTURES / "rollout" / f"schedule_{name}.json").read_text(encoding="utf-8")
    )
    wardrobe = load_wardrobe(schedule["wardrobe"])
    occasions = {day["occasion"] for day in schedule["days"]}
    eligible = eligible_set(wardrobe, occasions)
    if len(eligible) < 40:
        raise AssertionError(f"rollout {name}: |E| = {len(eligible)}, EVALS.md M6 requires >= 40")

    service = _rollout_service(schedule, wardrobe)
    stamp = EPOCH
    _seed_history(service, schedule, stamp)
    worn_sets: list[frozenset[str]] = []
    thermal: list[float] = []
    for day in schedule["days"]:
        moment = datetime.fromisoformat(f"{day['date']}T06:00:00+00:00")
        rec = service.recommend(date=day["date"], occasion=day["occasion"], now=moment)
        log = service.wear_recommendation(rec.id, rank=1, date=day["date"], now=moment)
        worn_sets.append(log.core_ids())
        thermal.append(rec.outfits[0].scores.thermal)
        if day["laundry"]:
            dirty = service.repo.dirty_garment_ids()
            if dirty:
                service.launder(all_dirty=True, now=moment)

    # The same 14 days dressed by the `mean_static` baseline, for M6's margin.
    static_worn: set[str] = set()
    static_service = _rollout_service(schedule, wardrobe)
    _seed_history(static_service, schedule, stamp)
    for day in schedule["days"]:
        moment = datetime.fromisoformat(f"{day['date']}T06:00:00+00:00")
        forecast = static_service.ensure_forecast(day["date"], now=moment)
        params = static_service.build_params(date=day["date"], occasion=day["occasion"])
        case = Case(
            scenario=day["date"],
            wardrobe_name=schedule["wardrobe"],
            forecast=forecast,
            params=params,
            garments=tuple(static_service.repo.list_garments(include_retired=False)),
            history=static_service.repo.wear_history(day["date"]),
            swing=False,
            label=f"static/{name}/{day['date']}",
        )
        pick = mean_static(case)
        if pick is None:
            continue
        static_worn |= pick.core_ids
        static_service.repo.add_wear_log(
            date=day["date"], garment_ids=sorted(pick.core_ids), source="manual", now=moment
        )
        if day["laundry"]:
            dirty = static_service.repo.dirty_garment_ids()
            if dirty:
                static_service.launder(all_dirty=True, now=moment)

    n = len(worn_sets)
    no_repeat = [
        1.0 if worn_sets[d] not in worn_sets[max(0, d - 3) : d] else 0.0 for d in range(3, n)
    ]
    novel = [
        len(worn_sets[d] - set().union(*worn_sets[d - 3 : d])) / len(worn_sets[d])
        for d in range(3, n)
    ]
    consec = [
        len(worn_sets[d] & worn_sets[d - 1]) / len(worn_sets[d] | worn_sets[d - 1])
        for d in range(1, n)
    ]
    repeat_free = [1.0 if worn_sets[d] != worn_sets[d - 1] else 0.0 for d in range(1, n)]
    union = set().union(*worn_sets)
    return RolloutResult(
        name=name,
        eligible=len(eligible),
        worn_sets=worn_sets,
        thermal=thermal,
        utilization=len(union & eligible) / len(eligible),
        no_repeat_window_3=sum(no_repeat) / len(no_repeat),
        novel_item_rate=sum(novel) / len(novel),
        consec_sim=sum(consec) / len(consec),
        repeat_free=sum(repeat_free) / len(repeat_free),
        comfort=sum(thermal) / len(thermal),
        static_utilization=len(static_worn & eligible) / len(eligible),
    )


def m6_rollouts(rollouts: Sequence[RolloutResult], notes: dict[str, Any]) -> list[MetricResult]:
    worst = lambda key: min(getattr(r, key) for r in rollouts)  # noqa: E731
    best = lambda key: max(getattr(r, key) for r in rollouts)  # noqa: E731
    notes["m6"] = {
        r.name: {
            "eligible": r.eligible,
            "utilization": round(r.utilization, 4),
            "static_utilization": round(r.static_utilization, 4),
            "no_repeat_window_3": round(r.no_repeat_window_3, 4),
            "novel_item_rate": round(r.novel_item_rate, 4),
            "consec_sim": round(r.consec_sim, 4),
            "repeat_free": round(r.repeat_free, 4),
            "comfort": round(r.comfort, 4),
        }
        for r in rollouts
    }
    static_util = max(r.static_utilization for r in rollouts)
    util_margin = min(r.utilization - r.static_utilization for r in rollouts)
    return [
        MetricResult(
            "M6 no_repeat_window_3",
            worst("no_repeat_window_3"),
            0.95,
            ">=",
            detail="worst of three rollouts",
        ),
        MetricResult(
            "M6 novel_item_rate", worst("novel_item_rate"), 0.35, ">=", detail="worst rollout"
        ),
        MetricResult("M6 consec_sim", best("consec_sim"), 0.40, "<=", detail="worst rollout"),
        MetricResult(
            "M6 utilization",
            worst("utilization"),
            0.60,
            ">=",
            baseline=static_util,
            detail=f"|E| = {rollouts[0].eligible}; mean_static reaches {static_util:.3f}",
        ),
        MetricResult(
            "M6 utilization margin",
            util_margin,
            0.25,
            ">=",
            baseline=static_util,
            detail="engine minus mean_static on the same 14 days",
        ),
        MetricResult(
            "M6 rollout_comfort", worst("comfort"), 0.83, ">=", detail="worst rollout"
        ),
    ]


# --------------------------------------------------------------------------
# M7 — determinism
# --------------------------------------------------------------------------

DETERMINISM_DROP = ("id", "created_at", "fetched_at", "snapshot_id")


def canonical(rec: Recommendation) -> str:
    payload = rec.model_dump(mode="json")

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in DETERMINISM_DROP}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(payload), sort_keys=True, separators=(",", ":"))


def _perturb(forecast: DayForecast) -> DayForecast:
    """Nudge temp, wind and humidity by one ULP each (EVALS.md M7 check 4)."""
    hours = [
        h.model_copy(
            update={
                "temp_c": math.nextafter(h.temp_c, math.inf),
                "wind_kmh": math.nextafter(h.wind_kmh, math.inf),
                "humidity_pct": math.nextafter(h.humidity_pct, math.inf),
            }
        )
        for h in forecast.hours
    ]
    return forecast.model_copy(update={"hours": hours})


def m7_determinism(suite: Suite, rollouts: Sequence[RolloutResult]) -> MetricResult:
    failures: list[str] = []
    sample = [c for c in suite.cases if c.wardrobe_name == "small"][:3]

    for case in sample:
        first, second = run_case(case), run_case(case)
        if canonical(first.recommendation) != canonical(second.recommendation):  # type: ignore[arg-type]
            failures.append(f"{case.label}: two runs differ")

        rec = first.recommendation
        assert rec is not None
        for outfit in rec.outfits:
            weights = outfit.scores.weights
            total = round(
                sum(
                    weights[name] * getattr(outfit.scores, name)
                    for name in ("thermal", "protection", "color", "style", "variety")
                ),
                SCORE_DP,
            )
            if abs(total - outfit.score_total) > 1e-9:
                failures.append(f"{case.label}: score_total not reproducible from components")
            for entry in outfit.hour_plan:
                if abs(entry.deviation - round(entry.ensemble_clo - entry.target_clo, PLAN_DP)) > (
                    1.5 * 10.0**-PLAN_DP
                ):
                    failures.append(f"{case.label}: hour_plan deviation not reproducible")
                    break

        perturbed = Case(
            scenario=case.scenario,
            wardrobe_name=case.wardrobe_name,
            forecast=_perturb(case.forecast),
            params=case.params,
            garments=case.garments,
            history=case.history,
            swing=case.swing,
            label=case.label,
        )
        nudged = run_case(perturbed)
        if canonical(nudged.recommendation) != canonical(rec):  # type: ignore[arg-type]
            failures.append(f"{case.label}: a 1-ULP input change moved the output")

    # wardrobe_hash: stable across runs, and independent of the request filter
    small = load_wardrobe("small")
    if wardrobe_hash(small) != wardrobe_hash(list(reversed(small))):
        failures.append("wardrobe_hash depends on iteration order")
    by_occasion = {
        run_case(
            Case(
                scenario=sample[0].scenario,
                wardrobe_name="small",
                forecast=sample[0].forecast,
                params=sample[0].params.model_copy(update={"occasion": occ}),
                garments=sample[0].garments,
                history=sample[0].history,
                swing=sample[0].swing,
                label=f"{sample[0].label}+{occ}",
            )
        ).recommendation.wardrobe_hash  # type: ignore[union-attr]
        for occ in ("casual", "outdoor")
    }
    if len(by_occasion) != 1:
        failures.append("wardrobe_hash changes with the request occasion")

    for rollout in rollouts:
        again = run_rollout(rollout.name)
        if again.worn_sets != rollout.worn_sets:
            failures.append(f"rollout {rollout.name} is not reproducible")

    return MetricResult(
        "M7 determinism",
        0.0 if failures else 1.0,
        1.00,
        "==",
        detail="; ".join(failures[:2]) if failures else "4 checks x 3 cases + 3 rollouts",
    )


# --------------------------------------------------------------------------
# M8 — component_capability (the anti-ThermalBot gate)
# --------------------------------------------------------------------------

COMPONENTS = ("protection", "color", "style", "variety")

#: A scenario counts toward a component's lift only when the brute-force
#: ceiling is at least this far above ``random_valid`` on it.
DISCRIMINATION_EPS = 0.02


def m8_component_capability(
    runs: Sequence[Run],
    references: dict[str, Reference],
    baselines: dict[str, list[BaselineOutfit]],
    notes: dict[str, Any],
) -> list[MetricResult]:
    """Halfway from a random valid outfit to the brute-force per-component max.

    EVALS.md §3 M8 averages over all of ``S_small``.  The lift is measured here
    over the **discriminating subset** of it — the scenarios where the
    brute-force ceiling actually exceeds ``random_valid`` — because a scenario
    on which every HC-valid outfit scores the same cannot say anything about
    capability, and averaging it in only shrinks the denominator toward the
    0/0 the metric's own guard is meant to catch.  Both the restricted and the
    whole-suite numbers are reported.  See REVIEW.md §Build-stage notes.
    """
    results: list[MetricResult] = []
    detail: dict[str, Any] = {}
    small = [run for run in runs if run.case.wardrobe_name == "small"]
    for component in COMPONENTS:
        rows: list[tuple[float, float, float]] = []  # (top1, random, brute max)
        for run in small:
            top = run.top1
            assert top is not None
            draws = baselines[run.case.label]
            rows.append(
                (
                    getattr(top.scores, component),
                    sum(getattr(d, component) for d in draws) / len(draws) if draws else 0.0,
                    references[run.case.label].best_component[component],
                )
            )
        discriminating = [row for row in rows if row[2] - row[1] >= DISCRIMINATION_EPS]
        if len(discriminating) < 3:
            raise AssertionError(
                f"M8 {component}: only {len(discriminating)} of {len(rows)} scenarios "
                f"discriminate on this component; the fixture has stopped testing it"
            )
        top_mean = sum(r[0] for r in discriminating) / len(discriminating)
        random_mean = sum(r[1] for r in discriminating) / len(discriminating)
        best_mean = sum(r[2] for r in discriminating) / len(discriminating)
        denominator = best_mean - random_mean
        if denominator < 0.05:
            raise AssertionError(
                f"M8 {component}: the fixture cannot discriminate — brute-force ceiling "
                f"{best_mean:.4f} is only {denominator:.4f} above random_valid "
                f"{random_mean:.4f} (EVALS.md §3 M8 requires >= 0.05)"
            )
        detail[component] = {
            "top1": round(top_mean, 4),
            "random_valid": round(random_mean, 4),
            "brute_max": round(best_mean, 4),
            "denominator": round(denominator, 4),
            "discriminating_scenarios": len(discriminating),
            "suite_top1": round(sum(r[0] for r in rows) / len(rows), 4),
            "suite_random": round(sum(r[1] for r in rows) / len(rows), 4),
            "suite_brute_max": round(sum(r[2] for r in rows) / len(rows), 4),
        }
        results.append(
            MetricResult(
                f"M8 lift_{component}",
                (top_mean - random_mean) / denominator,
                0.50,
                ">=",
                baseline=random_mean,
                detail=(
                    f"top1 {top_mean:.3f}, random {random_mean:.3f}, max {best_mean:.3f} "
                    f"over {len(discriminating)} discriminating scenarios"
                ),
            )
        )
    notes["m8"] = detail
    return results


# --------------------------------------------------------------------------
# M9 — protection_response (the soft half of FR-9)
# --------------------------------------------------------------------------


def _wardrobe_offers_rain_option(case: Case, hours: Sequence[checker.Hour]) -> bool:
    cand = checker.candidates(case.garments, case.params, hours)
    if any(g.accessory_class == "umbrella" and case.params.occasion in g.occasions
           for g in cand.accessories):
        return True
    return _hc_compatible_with(case, hours, lambda g: g.waterproofness >= 1)


def _hc_compatible_with(case: Case, hours: Sequence[checker.Hour], predicate) -> bool:
    """Is there an HC-1…HC-5 valid outfit containing a garment satisfying `predicate`?"""
    cand = checker.candidates(case.garments, case.params, hours)
    special = [g for g in cand.mid + cand.outer if predicate(g)]
    for extra in special:
        for core in enumerate_cores(cand):
            garments = core.garments()
            if extra.id not in {g.id for g in garments}:
                continue
            by_slot = dict(core.slot_items())
            problems = checker.hard_constraint_violations(
                by_slot, hours, case.params, case.history, umbrella=True, allow_repeat=True
            )
            if not any(p.startswith(("HC-1", "HC-2", "HC-3", "HC-4", "HC-5")) for p in problems):
                return True
    return False


def m9_protection_response(
    runs: Sequence[Run], baselines: dict[str, list[BaselineOutfit]], notes: dict[str, Any]
) -> MetricResult:
    applicable = 0
    responded = 0
    baseline_hits = 0
    baseline_total = 0
    rows: list[dict[str, Any]] = []
    by_label = {run.case.label: run for run in runs}
    for run in runs:
        case = run.case
        scenario = case.scenario
        if scenario not in M9_RAIN_CASES and scenario not in M9_WIND_CASES:
            continue
        hours = checker.window_hours(case.forecast, case.params)
        top = run.top1
        assert top is not None
        by_id = {g.id: g for g in case.garments}
        by_slot = {i.slot: by_id[i.garment_id] for i in top.items if i.garment_id in by_id}
        if scenario in M9_RAIN_CASES:
            if not _wardrobe_offers_rain_option(case, hours):
                continue
            applicable += 1
            rainy = [i for i, h in enumerate(hours) if h.rain_any]
            umbrella = any(a.accessory_class == "umbrella" for a in top.accessories)
            covered = all(
                checker.resolve_worn(top.hour_plan[i].worn_slots, by_slot, hours[i]).cover >= 1
                for i in rainy
            )
            ok = covered or umbrella
        else:
            if not _hc_compatible_with(case, hours, lambda g: g.windproofness >= 1
                                       and g.layer_role == "outer"):
                continue
            applicable += 1
            windy = [i for i, h in enumerate(hours) if h.windy]
            ok = all(
                checker.resolve_worn(top.hour_plan[i].worn_slots, by_slot, hours[i]).windproofness
                >= 1
                for i in windy
            )
        responded += 1 if ok else 0
        rows.append({"case": case.label, "responded": ok})
        draws = baselines.get(case.label)
        if draws:
            baseline_total += len(draws)
            baseline_hits += sum(1 for d in draws if d.protection >= 0.999)
    notes["m9"] = {
        "cases": rows,
        "random_valid_rate": round(baseline_hits / baseline_total, 4) if baseline_total else None,
    }
    if applicable == 0:  # pragma: no cover - the fixture always offers options
        raise AssertionError("M9 has no applicable case; the fixture stopped discriminating")
    _ = by_label
    return MetricResult(
        "M9 protection_response",
        responded / applicable,
        1.00,
        "==",
        baseline=(baseline_hits / baseline_total) if baseline_total else None,
        detail=f"{responded}/{applicable} applicable cases",
    )


# --------------------------------------------------------------------------
# M10 — degradation_correctness (FR-14 gets teeth)
# --------------------------------------------------------------------------

LADDER_RULES = ("HC-8", "HC-4", "HC-6")


def _feasible_under(case: Case, step: int) -> bool:
    """Is any outfit HC-valid under the ladder prefix of length ``step``?"""
    hours = checker.window_hours(case.forecast, case.params)
    cand = checker.candidates(case.garments, case.params, hours)
    for core in enumerate_cores(cand):
        by_slot = dict(core.slot_items())
        umbrella = checker.umbrella_pick(
            list(core.garments()), cand.accessories, case.params.occasion
        )
        problems = checker.hard_constraint_violations(
            by_slot,
            hours,
            case.params,
            case.history,
            umbrella=umbrella is not None,
            spread_limit=2 if step >= 2 else 1,
            allow_repeat=step >= 1,
            lower_moderate_cover=step >= 3,
        )
        if not problems:
            return True
    return False


def m10_degradation(runs: Sequence[Run], notes: dict[str, Any]) -> MetricResult:
    checks = 0
    failures: list[str] = []
    rows: list[dict[str, Any]] = []

    def record(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)

    for run in runs:
        case = run.case
        rec = run.recommendation
        # (a) a relaxed recommendation or a structured infeasible_wardrobe
        record(
            (rec is not None and rec.outfits) or run.error is not None,
            f"M10(a) {case.label}: neither outfits nor a structured error",
        )
        earliest = next((s for s in range(4) if _feasible_under(case, s)), None)
        if rec is not None and rec.outfits:
            applied = [c.rule for c in rec.compromises]
            expected = list(LADDER_RULES[:earliest]) if earliest else []
            # (b) exactly the lexicographically earliest feasible ladder prefix
            record(
                applied == expected,
                f"M10(b) {case.label}: applied {applied}, earliest feasible prefix {expected}",
            )
            # (c) HC-1/2/3/5 hold in every relaxed outfit
            hours = checker.window_hours(case.forecast, case.params)
            by_id = {g.id: g for g in case.garments}
            for outfit in rec.outfits:
                by_slot = {
                    i.slot: by_id[i.garment_id]
                    for i in outfit.items
                    if i.slot in checker.CORE_SLOTS and i.garment_id in by_id
                }
                problems = checker.hard_constraint_violations(
                    by_slot,
                    hours,
                    case.params,
                    case.history,
                    umbrella=True,
                    spread_limit=5,
                    allow_repeat=True,
                    lower_moderate_cover=True,
                )
                never = [p for p in problems if p.startswith(("HC-1", "HC-2", "HC-3", "HC-5"))]
                record(
                    not never,
                    f"M10(c) {case.label} rank {outfit.rank}: {never}",
                )
            rows.append({"case": case.label, "compromises": applied})
        else:
            error = run.error
            assert error is not None
            # (d) the named capability is genuinely absent, and a brief rides along
            record(
                earliest is None,
                f"M10(d) {case.label}: reported infeasible but the checker finds a "
                f"feasible ladder prefix at step {earliest}",
            )
            missing = str(error.detail.get("missing", ""))
            record(bool(missing), f"M10(d) {case.label}: no missing capability named")
            brief = error.detail.get("brief")
            record(
                isinstance(brief, dict)
                and bool(brief.get("hours"))
                and "required_clo_max" in brief,
                f"M10(d) {case.label}: infeasible_wardrobe carries no well-formed DayBrief",
            )
            rows.append({"case": case.label, "infeasible": missing})
    notes["m10"] = rows
    return MetricResult(
        "M10 degradation_correctness",
        (checks - len(failures)) / checks if checks else 1.0,
        1.00,
        "==",
        detail="; ".join(failures[:2]) if failures else f"{checks} checks",
    )


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def m4_output_validity(
    suite: Suite,
    runs: Sequence[Run],
    references: dict[str, Reference],
    variation_runs: Sequence[Run],
    edge_runs: Sequence[Run],
    rollouts: Sequence[RolloutResult],
    notes: dict[str, Any],
) -> MetricResult:
    tally = CheckTally()
    for run in runs:
        tally.extend(check_run(run, references.get(run.case.label)))
    for run in variation_runs:
        tally.extend(check_run(run, expect_swing=False))
    for run in edge_runs:
        tally.extend(check_run(run, expect_swing=False))

    # (j) parameter response, on `spring_swing` x small
    by_label = {run.case.label: run for run in variation_runs}
    base = next(r for r in runs if r.case.label == "04_spring_swing/small")

    def mean_icl(run: Run) -> float:
        top = run.top1
        assert top is not None
        return sum(e.ensemble_clo for e in top.hour_plan) / len(top.hour_plan)

    cold = by_label["04_spring_swing/small+met1.2"]
    hot = by_label["04_spring_swing/small+met2.2"]
    window = by_label["04_spring_swing/small+window17-22"]
    tally.assert_(
        mean_icl(cold) > mean_icl(base),
        f"M4(j) met 1.2 must be warmer than met 1.6 "
        f"({mean_icl(cold):.3f} vs {mean_icl(base):.3f})",
    )
    tally.assert_(
        mean_icl(hot) < mean_icl(base),
        f"M4(j) met 2.2 must be cooler than met 1.6 "
        f"({mean_icl(hot):.3f} vs {mean_icl(base):.3f})",
    )
    top = window.top1
    assert top is not None
    tally.assert_(
        [e.hour for e in top.hour_plan] == list(range(17, 22)),
        f"M4(j) wear window 17-22 must read exactly those hours, got "
        f"{[e.hour for e in top.hour_plan]}",
    )
    notes["m4"] = {
        "checks": tally.checks,
        "violations": tally.violations[:5],
        "rollout_days_checked": sum(len(r.worn_sets) for r in rollouts),
    }
    value = (tally.checks - len(tally.violations)) / tally.checks
    return MetricResult(
        "M4 output_validity",
        value,
        1.00,
        "==",
        detail=(
            "; ".join(tally.violations[:2])
            if tally.violations
            else f"{tally.checks} independent checks"
        ),
    )


def _thermal_baselines(
    runs: Sequence[Run],
    static: dict[str, BaselineOutfit | None],
    baselines: dict[str, list[BaselineOutfit]],
    notes: dict[str, Any],
) -> tuple[float, float, float]:
    static_thermal: list[float] = []
    random_thermal: list[float] = []
    random_inband: list[float] = []
    static_inband: list[float] = []
    for run in runs:
        pick = static.get(run.case.label)
        if pick is not None:
            static_thermal.append(pick.thermal)
            static_inband.append(pick.inband)
        draws = baselines.get(run.case.label) or []
        if draws:
            random_thermal.extend(d.thermal for d in draws)
            random_inband.extend(d.inband for d in draws)
    mu_static, sd_static = mean_sd(static_thermal)
    mu_random, sd_random = mean_sd(random_thermal)
    notes["baselines"] = {
        "mean_static": {
            "S_thermal": round(mu_static, 4),
            "sd": round(sd_static, 4),
            "inband": round(sum(static_inband) / len(static_inband), 4),
            "cases": len(static_thermal),
        },
        "random_valid": {
            "S_thermal": round(mu_random, 4),
            "sd": round(sd_random, 4),
            "inband": round(sum(random_inband) / len(random_inband), 4),
            "draws": len(random_thermal),
        },
    }
    return mu_static, mu_random, sd_random


def build_report(*, verbose: bool = False) -> EvalReport:
    """Run every metric on the committed fixtures and return the scorecard."""
    report = EvalReport()
    notes = report.notes

    report.add(m1_physics_conformance())

    suite = load_suite()
    runs = [run_case(case) for case in suite.cases]
    variation_runs = [run_case(case) for case in suite.variations]
    edge_runs = [run_case(case) for case in suite.edge_cases]

    references = {case.label: brute_force(case) for case in suite.small}
    small_reference = references["01_winter_calm/small"]
    if small_reference.raw_count != 9900:
        raise AssertionError(
            "the small-wardrobe enumeration must be exactly 6*5*3*11*5*2 = 9,900 "
            f"(EVALS.md §4); it is {small_reference.raw_count}"
        )
    notes["enumeration"] = {
        label: {"raw": ref.raw_count, "hc_valid": ref.feasible}
        for label, ref in references.items()
    }

    static = {case.label: mean_static(case) for case in suite.cases}
    baselines = {case.label: random_valid(case) for case in suite.cases}
    mu_static, mu_random, sd_random = _thermal_baselines(runs, static, baselines, notes)

    m2, m2_worst, per_case = m2_comfort_fit(runs)
    notes["m2"] = per_case
    report.add(m2)
    report.add(m2_worst)
    report.add(
        MetricResult(
            "M2 margin over mean_static",
            m2.value - mu_static,
            0.15,
            ">=",
            baseline=mu_static,
            detail="the core claim: hourly planning beats daily-mean dressing",
        )
    )

    m2b_mean, m2b_min = m2b_search_optimality(runs, references)
    report.add(m2b_mean)
    report.add(m2b_min)

    m2c, strict, clamped_hours = m2c_saturation_maximality(runs, references)
    notes["m2c"] = {"clamped_hours": clamped_hours, "strict_equality_rate": round(strict, 4)}
    report.add(m2c)

    m3, m3_min, m3_detail = m3_layering_advantage(runs, references)
    notes["m3"] = m3_detail
    report.add(m3)
    report.add(m3_min)

    rollouts = [run_rollout(name) for name in ("a", "b", "c")]
    report.add(
        m4_output_validity(suite, runs, references, variation_runs, edge_runs, rollouts, notes)
    )

    for result in m5_palette_style(notes):
        report.add(result)
    for result in m6_rollouts(rollouts, notes):
        report.add(result)
    report.add(m7_determinism(suite, rollouts))
    for result in m8_component_capability(runs, references, baselines, notes):
        report.add(result)
    report.add(m9_protection_response(runs, baselines, notes))
    report.add(m10_degradation(edge_runs, notes))

    # Report-only quantities (EVALS.md §3 "Report-only metrics")
    tops = [run.top1 for run in runs if run.top1 is not None]
    notes["component_contributions"] = {
        name: round(sum(getattr(t.scores, name) for t in tops) / len(tops), 4)
        for name in ("thermal", "protection", "color", "style", "variety")
    }
    notes["plan_shape"] = {
        "changes_per_day": sorted(
            {len(_segments([list(e.worn_slots) for e in t.hour_plan])) - 1 for t in tops}
        ),
        "distinct_configs": sorted(
            {len({tuple(e.worn_slots) for e in t.hour_plan}) for t in tops}
        ),
    }
    monotone = {}
    for label in ("13_frontal_drop/small", "14_midday_dip/small"):
        run = next(r for r in runs if r.case.label == label)
        top = run.top1
        assert top is not None
        monotone[label] = round(top.scores.thermal - references[label].monotone_shed_thermal, 4)
    notes["monotone_shed_delta"] = monotone
    notes["relaxation_rate"] = round(
        sum(1 for r in runs if r.recommendation and r.recommendation.compromises) / len(runs), 4
    )
    notes["random_valid_sd"] = round(sd_random, 4)
    notes["mean_static_thermal"] = round(mu_static, 4)
    notes["random_valid_thermal"] = round(mu_random, 4)
    if verbose:  # pragma: no cover - debugging aid
        print(json.dumps(notes, indent=2, default=str))
    return report
