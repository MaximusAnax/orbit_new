"""Committed, seeded fixture generator (EVALS.md §4).

Run it with ``uv run python dresscast/evals/fixtures/generate.py`` — it is
idempotent, offline, and reads no clock: every timestamp is a literal.  What it
writes:

* ``wardrobes/small.json``  — the 26-garment hand-authored table below
* ``wardrobes/medium.json`` — ~70 garments drawn with ``random.Random(42)``
* ``wardrobes/edge.json``   — 18 garments with deliberate gaps (FR-14/M10)
* ``weather/NN_*.json``     — the 15 scenario days, from ``scenarios.toml``
* ``weather/dst_*.json``    — the 23-hour and 25-hour DST days (FR-4 only)
* ``weather/rollout_{a,b,c}/day_NN.json`` — 3 × 14 days, seeds 101/102/103

The wardrobes in ``SMALL``/``EDGE`` are hand-authored tables: the script only
materialises them so a reviewer reads one artifact instead of two.  ``MEDIUM``
is genuinely generated and hand-reviewed; regenerating it is a reviewed change.
"""

from __future__ import annotations

import json
import math
import random
import tomllib
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STAMP = "2026-01-01T00:00:00+00:00"

# --------------------------------------------------------------------------
# Colour book (SCOPE.md D8: the neutral set carries no hue)
# --------------------------------------------------------------------------

NEUTRAL = {"black", "white", "gray", "navy", "beige", "olive", "denim", "brown", "cream", "khaki"}
HUES: dict[str, float] = {
    "red": 0.0,
    "rust": 20.0,
    "orange": 30.0,
    "mustard": 50.0,
    "yellow": 60.0,
    "lime": 90.0,
    "green": 120.0,
    "emerald": 150.0,
    "teal": 180.0,
    "blue": 220.0,
    "cobalt": 235.0,
    "purple": 280.0,
    "magenta": 310.0,
    "pink": 330.0,
    "burgundy": 350.0,
}


def color(name: str, role: str = "main") -> dict[str, Any]:
    if name in NEUTRAL:
        return {"name": name, "hue": None, "neutral": True, "role": role}
    return {"name": name, "hue": HUES[name], "neutral": False, "role": role}


PRESETS: dict[str, tuple[float, str, int, int]] = {
    # category: (clo, layer_role, formality, wears_before_laundry) — SCOPE.md D1/D11
    "tshirt": (0.08, "base", 2, 2),
    "long_sleeve_tee": (0.12, "base", 2, 2),
    "polo": (0.17, "base", 3, 2),
    "shirt_short_sleeve": (0.19, "base", 3, 2),
    "shirt_long_sleeve": (0.25, "base", 4, 2),
    "flannel_shirt": (0.34, "base", 2, 2),
    "sweater_thin": (0.25, "mid", 3, 5),
    "cardigan": (0.25, "mid", 3, 5),
    "sweater_thick": (0.36, "mid", 3, 5),
    "hoodie": (0.36, "mid", 2, 5),
    "fleece": (0.30, "mid", 2, 5),
    "blazer": (0.36, "mid", 4, 30),
    "suit_jacket": (0.36, "mid", 5, 30),
    "thermal_top": (0.20, "mid", 2, 2),
    "rain_shell": (0.25, "outer", 2, 30),
    "light_jacket": (0.40, "outer", 3, 30),
    "wool_coat": (0.60, "outer", 4, 30),
    "parka": (0.70, "outer", 2, 30),
    "down_jacket": (0.70, "outer", 2, 30),
    "trousers_thin": (0.15, "bottom", 4, 5),
    "trousers_thick": (0.24, "bottom", 4, 5),
    "jeans": (0.24, "bottom", 2, 5),
    "shorts": (0.08, "bottom", 1, 3),
    "skirt_thin": (0.14, "bottom", 3, 3),
    "skirt_thick": (0.23, "bottom", 4, 3),
    "thermal_bottoms": (0.15, "leg_base", 2, 2),
    "dress_light": (0.23, "full_body", 3, 2),
    "dress_long_sleeve": (0.33, "full_body", 4, 2),
    "shoes": (0.02, "footwear", 4, 999),
    "sneakers": (0.02, "footwear", 2, 999),
    "sandals": (0.02, "footwear", 1, 999),
    "boots": (0.10, "footwear", 3, 999),
    "hat": (0.0, "accessory", 2, 999),
    "gloves": (0.0, "accessory", 2, 999),
    "scarf": (0.0, "accessory", 3, 999),
    "umbrella": (0.0, "accessory", 3, 999),
    "sunglasses": (0.0, "accessory", 2, 999),
}


def garment(
    gid: str,
    name: str,
    category: str,
    colors: list[str],
    occasions: list[str],
    *,
    formality: int | None = None,
    tags: list[str] | None = None,
    waterproofness: int = 0,
    windproofness: int = 0,
    clo: float | None = None,
    layer_role: str | None = None,
) -> dict[str, Any]:
    preset_clo, preset_role, preset_formality, wears = PRESETS[category]
    role = layer_role or preset_role
    return {
        "id": gid,
        "name": name,
        "category": category,
        "layer_role": role,
        "accessory_class": category if role == "accessory" else None,
        "clo": preset_clo if clo is None else round(clo, 3),
        "waterproofness": waterproofness,
        "windproofness": windproofness,
        "formality": preset_formality if formality is None else formality,
        "colors": [color(c, "main" if i == 0 else "accent") for i, c in enumerate(colors)],
        "style_tags": tags or [],
        "occasions": occasions,
        "wears_before_laundry": wears,
        "wears_since_wash": 0,
        "status": "clean",
        "overridden_fields": [],
        "photo_path": None,
        "photo_sha256": None,
        "notes": None,
        "created_at": STAMP,
        "updated_at": STAMP,
    }


# --------------------------------------------------------------------------
# small.json — hand-authored, complete but tight (EVALS.md §4)
# --------------------------------------------------------------------------
# 6 base tops, 4 mids, 4 outers (one waterproofness-2 shell, one parka),
# 5 bottoms, 1 leg base, 3 footwear, 3 accessories = 26 garments.
# Core enumeration: 6·5·3·(1+4+6)·(1+4)·(1+1) = 9,900 (metrics.py asserts it).
# Every clo sits at its category preset, so EVALS.md §5.2's band arithmetic
# holds exactly: Icl in [0.311, 2.040].

EVERYDAY = ["casual", "work"]
DRESSY = ["casual", "work", "formal"]

SMALL: list[dict[str, Any]] = [
    garment("b1-tee", "white-tee", "tshirt", ["white"], ["casual", "sport", "outdoor"],
            formality=2, tags=["minimal"]),
    garment("b2-lstee", "gray-long-sleeve-tee", "long_sleeve_tee", ["gray"],
            ["casual", "outdoor"], formality=2, tags=["minimal"]),
    garment("b3-polo", "navy-polo", "polo", ["navy"], EVERYDAY, formality=3, tags=["preppy"]),
    garment("b4-ssshirt", "blue-short-sleeve-shirt", "shirt_short_sleeve", ["blue"], EVERYDAY,
            formality=3, tags=["preppy"]),
    garment("b5-oxford", "white-oxford-shirt", "shirt_long_sleeve", ["white"], DRESSY,
            formality=3, tags=["preppy", "classic"]),
    garment("b6-flannel", "red-flannel-shirt", "flannel_shirt", ["red"], ["casual", "outdoor"],
            formality=2, tags=["outdoorsy"]),
    garment("m1-thin", "beige-thin-sweater", "sweater_thin", ["beige"], DRESSY, formality=3,
            tags=["classic"]),
    garment("m2-fleece", "olive-fleece", "fleece", ["olive"], ["casual", "outdoor", "work"],
            formality=2, tags=["outdoorsy"]),
    garment("m3-thick", "gray-lambswool-sweater", "sweater_thick", ["gray"], DRESSY,
            formality=3, tags=["classic"]),
    garment("m4-hoodie", "navy-hoodie", "hoodie", ["navy"],
            ["casual", "outdoor", "work", "sport"], formality=2, tags=["outdoorsy", "sporty"]),
    garment("o1-shell", "yellow-rain-shell", "rain_shell", ["yellow"],
            ["casual", "outdoor", "work", "sport"], formality=2, waterproofness=2,
            windproofness=2, tags=["outdoorsy"]),
    garment("o2-jacket", "denim-light-jacket", "light_jacket", ["denim"], EVERYDAY, formality=3,
            waterproofness=1, windproofness=1, tags=["classic"]),
    garment("o3-coat", "navy-wool-coat", "wool_coat", ["navy"], DRESSY, formality=3,
            waterproofness=1, windproofness=1, tags=["classic"]),
    garment("o4-parka", "black-parka", "parka", ["black"], ["casual", "outdoor", "work"],
            formality=2, waterproofness=2, windproofness=2, tags=["outdoorsy"]),
    garment("p1-shorts", "beige-shorts", "shorts", ["beige"], ["casual", "sport", "outdoor"],
            formality=2, tags=["minimal"]),
    garment("p2-skirt", "black-thin-skirt", "skirt_thin", ["black"], DRESSY, formality=3,
            tags=["classic"]),
    garment("p3-chinos", "olive-chinos", "trousers_thin", ["olive"], EVERYDAY, formality=3,
            tags=["preppy", "classic"]),
    garment("p4-jeans", "dark-jeans", "jeans", ["denim"], ["casual", "outdoor", "work"],
            formality=2, tags=["classic"]),
    garment("p5-thick", "gray-thick-trousers", "trousers_thick", ["gray"], DRESSY, formality=3,
            tags=["classic"]),
    garment("l1-thermal", "black-thermal-bottoms", "thermal_bottoms", ["black"],
            ["casual", "outdoor", "work"], formality=2, tags=["outdoorsy"]),
    garment("f1-sandals", "beige-sandals", "sandals", ["beige"], ["casual", "sport", "outdoor"],
            formality=2, tags=["minimal"]),
    garment("f2-sneakers", "white-sneakers", "sneakers", ["white"],
            ["casual", "sport", "outdoor", "work"], formality=2, tags=["sporty", "minimal"]),
    garment("f3-boots", "brown-leather-boots", "boots", ["beige"], DRESSY, formality=3,
            tags=["classic", "outdoorsy"]),
    garment("a1-umbrella", "black-umbrella", "umbrella", ["black"],
            ["casual", "work", "outdoor", "formal", "sport"], formality=3),
    garment("a2-hat", "gray-wool-hat", "hat", ["gray"], ["casual", "work", "outdoor", "sport"],
            formality=2),
    garment("a3-gloves", "black-leather-gloves", "gloves", ["black"],
            ["casual", "work", "outdoor"], formality=3),
]

# --------------------------------------------------------------------------
# edge.json — deliberate gaps: no waterproofness-2 layer, no umbrella,
# one footwear (EVALS.md §4; drives FR-14's ladder and M10)
# --------------------------------------------------------------------------

EDGE_OCC = ["casual", "work", "outdoor"]

EDGE: list[dict[str, Any]] = [
    garment("e-b1", "edge-white-tee", "tshirt", ["white"], EDGE_OCC, formality=2, tags=["minimal"]),
    garment("e-b2", "edge-gray-tee", "long_sleeve_tee", ["gray"], EDGE_OCC, formality=2,
            tags=["minimal"]),
    garment("e-b3", "edge-blue-oxford", "shirt_long_sleeve", ["blue"], EDGE_OCC, formality=3,
            tags=["preppy"]),
    garment("e-b4", "edge-red-flannel", "flannel_shirt", ["red"], EDGE_OCC, formality=2,
            tags=["outdoorsy"]),
    garment("e-m1", "edge-thin-sweater", "sweater_thin", ["beige"], EDGE_OCC, formality=3,
            tags=["classic"]),
    garment("e-m2", "edge-thick-sweater", "sweater_thick", ["navy"], EDGE_OCC, formality=3,
            tags=["classic"]),
    garment("e-m3", "edge-fleece", "fleece", ["olive"], EDGE_OCC, formality=2, tags=["outdoorsy"]),
    # Outers: the best cover in this closet is waterproofness 1 (DWR), so every
    # moderate-rain hour needs FR-14's R3 and every heavy-rain hour is infeasible.
    garment("e-o1", "edge-wool-coat", "wool_coat", ["navy"], EDGE_OCC, formality=3,
            waterproofness=1, windproofness=1, tags=["classic"]),
    garment("e-o2", "edge-light-jacket", "light_jacket", ["denim"], EDGE_OCC, formality=3,
            waterproofness=1, windproofness=1, tags=["classic"]),
    garment("e-p1", "edge-jeans", "jeans", ["denim"], EDGE_OCC, formality=2, tags=["classic"]),
    garment("e-p2", "edge-chinos", "trousers_thin", ["olive"], EDGE_OCC, formality=3,
            tags=["preppy"]),
    garment("e-p3", "edge-thick-trousers", "trousers_thick", ["gray"], EDGE_OCC, formality=3,
            tags=["classic"]),
    garment("e-p4", "edge-shorts", "shorts", ["beige"], EDGE_OCC, formality=2, tags=["minimal"]),
    # One footwear only: every outfit shares it (EVALS.md §4's "no spare footwear").
    garment("e-f1", "edge-boots", "boots", ["brown"], EDGE_OCC, formality=3,
            tags=["classic", "outdoorsy"]),
    garment("e-a1", "edge-hat", "hat", ["gray"], EDGE_OCC, formality=2),
    garment("e-a2", "edge-gloves", "gloves", ["black"], EDGE_OCC, formality=3),
    garment("e-a3", "edge-scarf", "scarf", ["burgundy"], EDGE_OCC, formality=3),
    garment("e-a4", "edge-sunglasses", "sunglasses", ["black"], EDGE_OCC, formality=2),
]

# --------------------------------------------------------------------------
# medium.json — generated, seed 42, hand-reviewed
# --------------------------------------------------------------------------

MEDIUM_PLAN: list[tuple[str, int]] = [
    ("tshirt", 4),
    ("long_sleeve_tee", 3),
    ("polo", 2),
    ("shirt_short_sleeve", 2),
    ("shirt_long_sleeve", 5),
    ("flannel_shirt", 2),
    ("sweater_thin", 2),
    ("sweater_thick", 2),
    ("hoodie", 2),
    ("fleece", 1),
    ("blazer", 2),
    ("cardigan", 1),
    ("rain_shell", 2),
    ("light_jacket", 2),
    ("wool_coat", 1),
    ("parka", 1),
    ("down_jacket", 1),
    ("trousers_thin", 3),
    ("trousers_thick", 2),
    ("jeans", 4),
    ("shorts", 2),
    ("skirt_thin", 1),
    ("skirt_thick", 1),
    ("thermal_bottoms", 2),
    ("dress_light", 1),
    ("dress_long_sleeve", 1),
    ("shoes", 2),
    ("sneakers", 2),
    ("sandals", 1),
    ("boots", 2),
    ("umbrella", 2),
    ("hat", 2),
    ("gloves", 2),
    ("scarf", 2),
    ("sunglasses", 1),
]

#: 60% neutral, per capsule-wardrobe practice (EVALS.md §4).
MEDIUM_NEUTRALS = ["navy", "gray", "black", "white", "beige", "olive", "denim", "brown"]
MEDIUM_ACCENTS = ["blue", "green", "burgundy", "rust", "teal", "mustard", "purple", "pink"]

#: Which occasions each category plausibly serves, most typical first.  The
#: generator always keeps the first entry and samples the rest, so a pair of
#: shorts never turns up tagged `formal` and per-occasion candidate lists stay
#: realistic rather than universal.  This table is the hand-reviewed part of
#: `medium.json`; the draws inside it are the generated part.
CATEGORY_OCCASIONS: dict[str, list[str]] = {
    "tshirt": ["casual", "sport", "outdoor"],
    "long_sleeve_tee": ["casual", "outdoor", "sport"],
    "polo": ["casual", "work", "sport"],
    "shirt_short_sleeve": ["work", "casual"],
    "shirt_long_sleeve": ["work", "formal", "casual"],
    "flannel_shirt": ["casual", "outdoor"],
    "sweater_thin": ["work", "casual", "formal"],
    "cardigan": ["casual", "work", "formal"],
    "sweater_thick": ["casual", "work", "outdoor"],
    "hoodie": ["casual", "sport", "outdoor"],
    "fleece": ["outdoor", "casual", "sport"],
    "blazer": ["work", "formal"],
    "suit_jacket": ["formal", "work"],
    "thermal_top": ["outdoor", "casual", "sport"],
    "rain_shell": ["outdoor", "casual", "work", "sport"],
    "light_jacket": ["casual", "work", "outdoor"],
    "wool_coat": ["work", "formal", "casual"],
    "parka": ["outdoor", "casual"],
    "down_jacket": ["outdoor", "casual"],
    "trousers_thin": ["work", "formal", "casual"],
    "trousers_thick": ["work", "casual", "outdoor"],
    "jeans": ["casual", "outdoor", "work"],
    "shorts": ["casual", "sport", "outdoor"],
    "skirt_thin": ["work", "casual", "formal"],
    "skirt_thick": ["work", "formal", "casual"],
    "thermal_bottoms": ["outdoor", "casual", "work"],
    "dress_light": ["casual", "work", "formal"],
    "dress_long_sleeve": ["work", "formal", "casual"],
    "shoes": ["work", "formal", "casual"],
    "sneakers": ["casual", "sport", "outdoor"],
    "sandals": ["casual", "sport"],
    "boots": ["outdoor", "casual", "work"],
    "hat": ["outdoor", "casual", "work", "sport"],
    "gloves": ["outdoor", "casual", "work"],
    "scarf": ["work", "casual", "formal"],
    "umbrella": ["work", "casual", "outdoor", "formal", "sport"],
    "sunglasses": ["casual", "outdoor", "sport"],
}

MEDIUM_TAGS = ["classic", "preppy", "outdoorsy", "sporty", "minimal", "tailored"]

WATERPROOF_BY_CATEGORY = {"rain_shell": 3, "parka": 2, "down_jacket": 1, "light_jacket": 1,
                          "wool_coat": 1, "boots": 1}
WINDPROOF_BY_CATEGORY = {"rain_shell": 2, "parka": 2, "down_jacket": 2, "light_jacket": 1,
                         "wool_coat": 1}


def build_medium(seed: int = 42) -> list[dict[str, Any]]:
    """~70 garments with realistic composition, drawn from a seeded RNG."""
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    index = 0
    for category, count in MEDIUM_PLAN:
        preset_clo, role, preset_formality, _ = PRESETS[category]
        for _ in range(count):
            index += 1
            neutral = rng.random() < 0.60
            main = rng.choice(MEDIUM_NEUTRALS if neutral else MEDIUM_ACCENTS)
            colors = [main]
            if rng.random() < 0.25:
                colors.append(rng.choice(MEDIUM_NEUTRALS))
            pool = CATEGORY_OCCASIONS[category]
            # Most garments serve one or two occasions.  A wardrobe where every
            # item serves every occasion is neither realistic nor a useful HC-3
            # test — and it makes the reference enumeration explode.
            n_occ = min(len(pool), rng.choice([1, 1, 1, 2, 2, 3]) if role != "accessory" else 2)
            occasions = sorted({pool[0], *rng.sample(pool, n_occ)})
            formality = preset_formality
            if role != "accessory" and rng.random() < 0.35:
                formality = max(1, min(5, preset_formality + rng.choice([-1, 1])))
            clo = None
            if role != "accessory" and rng.random() < 0.4:
                clo = round(preset_clo + rng.choice([-0.05, -0.02, 0.02, 0.05]), 3)
                clo = max(0.0, clo)
            tags = sorted(rng.sample(MEDIUM_TAGS, rng.choice([1, 1, 2])))
            out.append(
                garment(
                    f"m{index:03d}",
                    f"{main}-{category.replace('_', '-')}-{index:03d}",
                    category,
                    colors,
                    occasions,
                    formality=formality,
                    tags=tags,
                    waterproofness=WATERPROOF_BY_CATEGORY.get(category, 0),
                    windproofness=WINDPROOF_BY_CATEGORY.get(category, 0),
                    clo=clo,
                )
            )
    return out


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


def _diurnal(low: float, high: float, minimum_at: int) -> list[float]:
    mid = (low + high) / 2.0
    amp = (high - low) / 2.0
    return [mid - amp * math.cos(2.0 * math.pi * ((h - minimum_at) % 24) / 24.0) for h in range(24)]


def _ramp_down(start: float, end: float, ramp_start: int, ramp_end: int) -> list[float]:
    span = max(1, ramp_end - ramp_start)
    out: list[float] = []
    for h in range(24):
        if h <= ramp_start:
            out.append(start)
        elif h >= ramp_end:
            out.append(end)
        else:
            out.append(start + (end - start) * (h - ramp_start) / span)
    return out


def _dip(start: float, trough: float, trough_hour: int, end: float, end_hour: int) -> list[float]:
    out: list[float] = []
    for h in range(24):
        if h <= trough_hour:
            frac = h / max(1, trough_hour)
            out.append(start + (trough - start) * frac)
        elif h <= end_hour:
            frac = (h - trough_hour) / max(1, end_hour - trough_hour)
            out.append(trough + (end - trough) * frac)
        else:
            out.append(end - 0.4 * (h - end_hour))
    return out


def _uv_curve(peak: float) -> list[float]:
    out: list[float] = []
    for h in range(24):
        if 6 <= h <= 19:
            out.append(max(0.0, peak * math.cos(math.pi * (h - 13) / 13.0)))
        else:
            out.append(0.0)
    return out


def scenario_rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one hand-authored parameter block into 24 forecast rows."""
    shape = block["shape"]
    if shape == "diurnal":
        temps = _diurnal(block["low"], block["high"], int(block.get("minimum_at", 5)))
    elif shape == "ramp_down":
        temps = _ramp_down(
            block["start_temp"], block["end_temp"], int(block["ramp_start"]), int(block["ramp_end"])
        )
    elif shape == "dip":
        temps = _dip(
            block["start_temp"],
            block["trough_temp"],
            int(block["trough_hour"]),
            block["end_temp"],
            int(block["end_hour"]),
        )
    else:  # pragma: no cover - scenarios.toml is the only caller
        raise ValueError(f"unknown shape {shape!r}")

    wind_base = float(block["wind_base"])
    wind_peak = float(block.get("wind_peak", wind_base))
    wind_window = block.get("wind_window")
    wind_rise = bool(block.get("wind_rise", False))
    humidity = float(block["humidity"])
    uv = _uv_curve(float(block.get("uv_peak", 0.0)))
    precip_window = block.get("precip_window")
    precip_prob = float(block.get("precip_prob", 0.0))
    mmh_min = float(block.get("precip_mmh_min", 0.0))
    mmh_max = float(block.get("precip_mmh_max", 0.0))

    rows: list[dict[str, Any]] = []
    for h in range(24):
        if wind_rise:
            wind = wind_base + (wind_peak - wind_base) * h / 23.0
        elif wind_window and wind_window[0] <= h < wind_window[1]:
            wind = wind_peak
        else:
            wind = wind_base
        rain = bool(precip_window and precip_window[0] <= h < precip_window[1])
        if rain:
            span = max(1, precip_window[1] - precip_window[0] - 1)
            frac = (h - precip_window[0]) / span
            mmh = mmh_min + (mmh_max - mmh_min) * frac
            prob = precip_prob
            rh = min(100.0, humidity + 8.0)
        else:
            mmh = 0.0
            prob = 0.05
            rh = humidity
        rows.append(
            {
                "seq": h,
                "hour": h,
                "temp_c": round(temps[h], 1),
                "wind_kmh": round(wind, 1),
                "humidity_pct": round(rh, 1),
                "precip_prob": round(prob, 2),
                "precip_mmh": round(mmh, 2),
                "uv_index": round(uv[h] if not rain else uv[h] * 0.4, 1),
            }
        )
    return rows


def day_file(date: str, rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "date": date,
        "location_name": "home",
        "lat": 40.71,
        "lon": -74.01,
        "timezone": "America/New_York",
        "hours": rows,
        **extra,
    }


def build_dst_days() -> list[tuple[str, dict[str, Any]]]:
    """One 23-hour spring day and one 25-hour autumn day (FR-4's relaxed rule)."""
    spring_temps = _diurnal(6.0, 14.0, 5)
    rows: list[dict[str, Any]] = []
    for seq, hour in enumerate([h for h in range(24) if h != 2]):
        rows.append(
            {
                "seq": seq,
                "hour": hour,
                "temp_c": round(spring_temps[hour], 1),
                "wind_kmh": 11.0,
                "humidity_pct": 62.0,
                "precip_prob": 0.05,
                "precip_mmh": 0.0,
                "uv_index": round(_uv_curve(3.5)[hour], 1),
            }
        )
    spring = day_file("2026-03-08", rows, note="spring-forward: local hour 02 does not exist")

    autumn_temps = _diurnal(4.0, 12.0, 5)
    wall = [0, 1, 1, *range(2, 24)]
    rows = []
    for seq, hour in enumerate(wall):
        rows.append(
            {
                "seq": seq,
                "hour": hour,
                "temp_c": round(autumn_temps[hour], 1),
                "wind_kmh": 9.0,
                "humidity_pct": 71.0,
                "precip_prob": 0.05,
                "precip_mmh": 0.0,
                "uv_index": round(_uv_curve(2.5)[hour], 1),
            }
        )
    autumn = day_file("2026-11-01", rows, note="fall-back: local hour 01 occurs twice")
    return [("dst_spring_23h.json", spring), ("dst_autumn_25h.json", autumn)]


ROLLOUTS: dict[str, tuple[int, str, tuple[float, float], tuple[float, float]]] = {
    # name: (seed, first date, (low range), (amplitude range))
    "a": (101, "2026-03-02", (2.0, 11.0), (5.0, 11.0)),
    "b": (102, "2026-10-05", (5.0, 14.0), (4.0, 9.0)),
    "c": (103, "2026-01-05", (-7.0, 1.0), (3.0, 7.0)),
}


def _date_plus(start: str, days: int) -> str:
    from datetime import date as date_cls
    from datetime import timedelta

    return (date_cls.fromisoformat(start) + timedelta(days=days)).isoformat()


def build_rollout(name: str) -> list[tuple[str, dict[str, Any]]]:
    """14 consecutive days for one rollout, from its own seed (EVALS.md §3 M6)."""
    seed, start, low_range, amp_range = ROLLOUTS[name]
    rng = random.Random(seed)
    out: list[tuple[str, dict[str, Any]]] = []
    for day in range(14):
        low = rng.uniform(*low_range)
        amp = rng.uniform(*amp_range)
        wind = rng.uniform(4.0, 34.0)
        wet = rng.random() < 0.35
        block: dict[str, Any] = {
            "shape": rng.choice(["diurnal", "diurnal", "diurnal", "ramp_down", "dip"]),
            "low": round(low, 1),
            "high": round(low + amp, 1),
            "minimum_at": 5,
            "start_temp": round(low + amp, 1),
            "end_temp": round(low, 1),
            "ramp_start": rng.choice([8, 9, 10]),
            "ramp_end": rng.choice([15, 16, 17]),
            "trough_temp": round(low, 1),
            "trough_hour": rng.choice([12, 13, 14]),
            "end_hour": 18,
            "wind_base": round(wind, 1),
            "humidity": round(rng.uniform(50.0, 88.0), 1),
            "uv_peak": round(rng.uniform(0.5, 6.5), 1),
        }
        if wet:
            begin = rng.choice([6, 8, 11, 14])
            block["precip_window"] = [begin, min(24, begin + rng.choice([3, 5, 8]))]
            block["precip_prob"] = rng.choice([0.35, 0.55, 0.7, 0.85])
            block["precip_mmh_min"] = rng.choice([0.4, 1.5, 2.5])
            block["precip_mmh_max"] = rng.choice([1.0, 3.0, 6.0])
        date = _date_plus(start, day)
        out.append((f"day_{day + 1:02d}.json", day_file(date, scenario_rows(block))))
    return out


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> None:
    write_json(HERE / "wardrobes" / "small.json", SMALL)
    write_json(HERE / "wardrobes" / "edge.json", EDGE)
    write_json(HERE / "wardrobes" / "medium.json", build_medium())

    blocks = tomllib.loads((HERE / "weather" / "scenarios.toml").read_text(encoding="utf-8"))
    for block in blocks["scenario"]:
        payload = day_file(
            block["date"],
            scenario_rows(block),
            scenario=block["id"],
            shape=block["shape"],
            swing=bool(block.get("swing", False)),
            occasion=block["occasion"],
            description=block["description"],
            designed_to_catch=block["designed_to_catch"],
        )
        write_json(HERE / "weather" / f"{block['id']}.json", payload)

    for filename, payload in build_dst_days():
        write_json(HERE / "weather" / filename, payload)

    for name in ROLLOUTS:
        for filename, payload in build_rollout(name):
            write_json(HERE / "weather" / f"rollout_{name}" / filename, payload)

    print(f"wrote fixtures under {HERE}")


if __name__ == "__main__":
    main()
