"""Shared synthetic fixtures.  No real photos, no personal data (EVALS.md §4)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pytest
from dresscast.engine.models import (
    CATEGORY_PRESETS,
    Color,
    DayForecast,
    Garment,
    HourlyWeather,
    RequestParams,
    WearHistory,
)

NOW = datetime(2026, 4, 14, 6, 30, 0)

#: name -> (hue, neutral)
COLOR_BOOK: dict[str, tuple[float | None, bool]] = {
    "white": (None, True),
    "black": (None, True),
    "gray": (None, True),
    "navy": (None, True),
    "beige": (None, True),
    "olive": (None, True),
    "denim": (None, True),
    "red": (0.0, False),
    "orange": (30.0, False),
    "yellow": (60.0, False),
    "green": (120.0, False),
    "teal": (180.0, False),
    "blue": (220.0, False),
    "purple": (280.0, False),
    "pink": (330.0, False),
}


def color(name: str, role: str = "main") -> Color:
    hue, neutral = COLOR_BOOK[name]
    return Color(name=name, hue=hue, neutral=neutral, role=role)  # type: ignore[arg-type]


def garment(
    gid: str,
    name: str,
    category: str,
    *,
    colors: Sequence[str] = ("gray",),
    formality: int | None = None,
    occasions: Sequence[str] = ("casual", "work"),
    style_tags: Sequence[str] = ("classic",),
    clo: float | None = None,
    waterproofness: int = 0,
    windproofness: int = 0,
    layer_role: str | None = None,
    status: str = "clean",
    wears_since_wash: int = 0,
    wears_before_laundry: int | None = None,
    accessory_class: str | None = None,
    overridden_fields: Sequence[str] = (),
) -> Garment:
    preset = CATEGORY_PRESETS[category]
    role = layer_role or preset.layer_role
    return Garment(
        id=gid,
        name=name,
        category=category,
        layer_role=role,  # type: ignore[arg-type]
        accessory_class=(accessory_class if role == "accessory" else None),  # type: ignore[arg-type]
        clo=preset.clo if clo is None else clo,
        waterproofness=waterproofness,
        windproofness=windproofness,
        formality=preset.formality if formality is None else formality,
        colors=[color(c, "main" if i == 0 else "accent") for i, c in enumerate(colors)],
        style_tags=list(style_tags),
        occasions=list(occasions),
        wears_before_laundry=(
            preset.wears_before_laundry if wears_before_laundry is None else wears_before_laundry
        ),
        wears_since_wash=wears_since_wash,
        status=status,  # type: ignore[arg-type]
        overridden_fields=list(overridden_fields),
        created_at=NOW,
        updated_at=NOW,
    )


def small_wardrobe() -> list[Garment]:
    """26 garments, complete but tight — EVALS.md §4's ``small.json`` shape.

    Deliberately built so the achievable band is EVALS.md §5.2's worked
    arithmetic: the warmest HC-4-legal stack is
    ``flannel .34 + 2x.36 mids + parka .70 + thick trousers .24 + leg base .15
    + boots .10 = 2.25`` → ``Icl 2.040``, and the coolest slot-complete pick is
    ``tee .08 + shorts .08 + sandals .02 = 0.18`` → ``Icl 0.311``.
    """
    everyday = ("casual", "work")
    return [
        # 6 base tops
        garment(
            "b1-tee",
            "white-tee",
            "tshirt",
            colors=("white",),
            formality=2,
            occasions=("casual", "sport", "outdoor"),
        ),
        garment(
            "b2-lstee",
            "gray-long-sleeve-tee",
            "long_sleeve_tee",
            colors=("gray",),
            formality=2,
            occasions=("casual", "outdoor"),
        ),
        garment("b3-polo", "navy-polo", "polo", colors=("navy",), formality=3, occasions=everyday),
        garment(
            "b4-ssshirt",
            "blue-short-sleeve-shirt",
            "shirt_short_sleeve",
            colors=("blue",),
            formality=3,
            occasions=everyday,
        ),
        garment(
            "b5-oxford",
            "white-oxford-shirt",
            "shirt_long_sleeve",
            colors=("white",),
            formality=3,
            occasions=("work", "formal", "casual"),
        ),
        garment(
            "b6-flannel",
            "red-flannel-shirt",
            "flannel_shirt",
            colors=("red",),
            formality=2,
            occasions=("casual", "outdoor"),
        ),
        # 4 mids
        garment(
            "m1-thin",
            "beige-thin-sweater",
            "sweater_thin",
            colors=("beige",),
            formality=3,
            occasions=everyday,
        ),
        garment(
            "m2-fleece",
            "olive-fleece",
            "fleece",
            colors=("olive",),
            formality=2,
            occasions=("casual", "outdoor", "work"),
            style_tags=("outdoorsy",),
        ),
        garment(
            "m3-thick",
            "gray-lambswool-sweater",
            "sweater_thick",
            colors=("gray",),
            formality=3,
            occasions=everyday,
        ),
        garment(
            "m4-hoodie",
            "navy-hoodie",
            "hoodie",
            colors=("navy",),
            formality=2,
            occasions=("casual", "outdoor", "work"),
            style_tags=("outdoorsy",),
        ),
        # 4 outers
        garment(
            "o1-shell",
            "yellow-rain-shell",
            "rain_shell",
            colors=("yellow",),
            formality=2,
            waterproofness=2,
            windproofness=2,
            occasions=("casual", "outdoor", "work"),
            style_tags=("outdoorsy",),
        ),
        garment(
            "o2-jacket",
            "denim-light-jacket",
            "light_jacket",
            colors=("denim",),
            formality=3,
            waterproofness=1,
            windproofness=1,
            occasions=everyday,
        ),
        garment(
            "o3-coat",
            "navy-wool-coat",
            "wool_coat",
            colors=("navy",),
            formality=3,
            waterproofness=1,
            windproofness=1,
            occasions=everyday,
        ),
        garment(
            "o4-parka",
            "black-parka",
            "parka",
            colors=("black",),
            formality=2,
            waterproofness=2,
            windproofness=2,
            occasions=("casual", "outdoor", "work"),
            style_tags=("outdoorsy",),
        ),
        # 5 bottoms
        garment(
            "p1-shorts",
            "beige-shorts",
            "shorts",
            colors=("beige",),
            formality=2,
            occasions=("casual", "sport", "outdoor"),
        ),
        garment(
            "p2-skirt",
            "black-thin-skirt",
            "skirt_thin",
            colors=("black",),
            formality=3,
            occasions=everyday,
        ),
        garment(
            "p3-chinos",
            "olive-chinos",
            "trousers_thin",
            colors=("olive",),
            formality=3,
            occasions=everyday,
        ),
        garment(
            "p4-jeans",
            "dark-jeans",
            "jeans",
            colors=("denim",),
            formality=2,
            occasions=("casual", "outdoor", "work"),
        ),
        garment(
            "p5-thick",
            "gray-thick-trousers",
            "trousers_thick",
            colors=("gray",),
            formality=3,
            occasions=everyday,
        ),
        # 1 leg base
        garment(
            "l1-thermal",
            "black-thermal-bottoms",
            "thermal_bottoms",
            colors=("black",),
            formality=2,
            occasions=("casual", "outdoor", "work"),
        ),
        # 3 footwear
        garment(
            "f1-sandals",
            "beige-sandals",
            "sandals",
            colors=("beige",),
            formality=2,
            occasions=("casual", "sport", "outdoor"),
        ),
        garment(
            "f2-sneakers",
            "white-sneakers",
            "sneakers",
            colors=("white",),
            formality=2,
            occasions=("casual", "sport", "outdoor", "work"),
        ),
        garment(
            "f3-boots",
            "brown-leather-boots",
            "boots",
            colors=("beige",),
            formality=3,
            occasions=everyday,
        ),
        # 3 accessories
        garment(
            "a1-umbrella",
            "black-umbrella",
            "umbrella",
            colors=("black",),
            formality=3,
            accessory_class="umbrella",
            clo=0.0,
            occasions=("casual", "work", "outdoor", "formal", "sport"),
        ),
        garment(
            "a2-hat",
            "gray-wool-hat",
            "hat",
            colors=("gray",),
            formality=2,
            accessory_class="hat",
            clo=0.0,
            occasions=("casual", "work", "outdoor", "sport"),
        ),
        garment(
            "a3-gloves",
            "black-leather-gloves",
            "gloves",
            colors=("black",),
            formality=3,
            accessory_class="gloves",
            clo=0.0,
            occasions=("casual", "work", "outdoor"),
        ),
    ]


def _column(value: float | Sequence[float], n: int) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)] * n
    values = list(value)
    assert len(values) == n, f"expected {n} values, got {len(values)}"
    return [float(v) for v in values]


def make_forecast(
    date: str = "2026-04-14",
    *,
    temps: Sequence[float] | float = 12.0,
    wind: Sequence[float] | float = 8.0,
    humidity: Sequence[float] | float = 60.0,
    precip_prob: Sequence[float] | float = 0.05,
    precip_mmh: Sequence[float] | float = 0.0,
    uv: Sequence[float] | float = 1.0,
    hours: int = 24,
    snapshot_id: str = "snap-1",
    provider: str = "fixture",
) -> DayForecast:
    """Build a full local day.  ``hours`` may be 23/24/25 to exercise FR-4."""
    t = _column(temps, hours)
    w = _column(wind, hours)
    h = _column(humidity, hours)
    pp = _column(precip_prob, hours)
    pm = _column(precip_mmh, hours)
    u = _column(uv, hours)
    rows: list[HourlyWeather] = []
    for seq in range(hours):
        if hours == 25:
            wall = seq - 1 if seq >= 2 else seq  # autumn fold repeats hour 1
        elif hours == 23:
            wall = seq + 1 if seq >= 2 else seq  # spring gap skips hour 2
        else:
            wall = seq
        rows.append(
            HourlyWeather(
                seq=seq,
                hour=wall % 24,
                temp_c=t[seq],
                wind_kmh=w[seq],
                humidity_pct=h[seq],
                precip_prob=pp[seq],
                precip_mmh=pm[seq],
                uv_index=u[seq],
            )
        )
    return DayForecast(
        id=snapshot_id,
        date=date,
        location_name="home",
        lat=40.71,
        lon=-74.01,
        timezone="America/New_York",
        provider=provider,  # type: ignore[arg-type]
        fetched_at=NOW,
        raw=None,
        hours=rows,
    )


def diurnal(low: float, high: float, hours: int = 24, minimum_at: int = 5) -> list[float]:
    """A smooth sinusoid with its minimum near sunrise — EVALS.md's ``diurnal``."""
    import math

    mid = (low + high) / 2.0
    amp = (high - low) / 2.0
    return [
        mid - amp * math.cos(2.0 * math.pi * ((h - minimum_at) % 24) / 24.0) for h in range(hours)
    ]


def params(**kwargs: object) -> RequestParams:
    base: dict[str, object] = {"date": "2026-04-14", "occasion": "work"}
    base.update(kwargs)
    return RequestParams(**base)  # type: ignore[arg-type]


@pytest.fixture()
def wardrobe() -> list[Garment]:
    return small_wardrobe()


@pytest.fixture()
def empty_history() -> WearHistory:
    return WearHistory.empty()


@pytest.fixture()
def spring_swing() -> DayForecast:
    """5 → 18 °C dry — the canonical shed-layers day (EVALS.md scenario 04)."""
    return make_forecast(temps=diurnal(5.0, 18.0), wind=10.0, humidity=55.0, uv=3.0)


@pytest.fixture()
def winter_calm() -> DayForecast:
    """-6 → -2 °C, calm — ceiling-saturated (EVALS.md scenario 01)."""
    return make_forecast(temps=diurnal(-6.0, -2.0), wind=5.0, humidity=70.0, uv=0.5)


@pytest.fixture()
def summer_thunderstorm() -> DayForecast:
    """22 → 30 °C with a 14:00-17:00 downpour (EVALS.md scenario 10)."""
    prob = [0.05] * 24
    mmh = [0.0] * 24
    for hour in range(14, 18):
        prob[hour] = 0.7
        mmh[hour] = 12.0
    return make_forecast(
        temps=diurnal(22.0, 30.0),
        wind=12.0,
        humidity=70.0,
        precip_prob=prob,
        precip_mmh=mmh,
        uv=7.0,
    )
