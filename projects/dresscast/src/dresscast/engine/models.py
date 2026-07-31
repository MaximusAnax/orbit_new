"""Domain models and every named constant (SCOPE.md §Architecture, DATA_MODEL.md §2).

This module is the single place where the physical constants, the objective
weights, the thresholds and the D1/D11 preset tables live.  Nothing here reads
the clock, the filesystem or the network: timestamps and calendar dates are
always supplied by the caller.

Two value objects on the assembly hot path (:class:`LayerConfig`,
:class:`CoreOutfit`) are frozen dataclasses rather than Pydantic models.  They
are neither persisted nor serialized — they are recomputed for every one of the
~10^4 candidate outfits an assembly run enumerates — and Pydantic validation of
60k throwaway objects per scenario would blow EVALS.md §6's runtime budget.
Every entity that crosses a persistence or serialization boundary is a
Pydantic v2 model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dresscast.errors import InvalidParams

# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

ENGINE_VERSION = "0.1.0"
SCHEMA_VERSION = 1
#: Pins the D5/D7/D10 constant set (DATA_MODEL.md §2.4). Bump on any change.
THRESHOLDS_VERSION = "2026.07-a"

# --------------------------------------------------------------------------
# Quantization (FR-19)
# --------------------------------------------------------------------------

#: Decimals for score components and ``score_total``, applied before ranking.
SCORE_DP = 6
#: Decimals for physical floats written into ``hour_plan`` and the day brief.
PLAN_DP = 3
#: Resolution of internal argmin/argmax comparisons.  Coarser than a float ULP
#: so a libm last-bit difference cannot reorder a selection, finer than any
#: distinction the model makes (FR-19's cross-platform claim).
SELECT_DP = 9
SELECT_EPS = 10.0**-SELECT_DP

# --------------------------------------------------------------------------
# Physics (D2, D3, D4, D5)
# --------------------------------------------------------------------------

#: ASHRAE/McCullough ensemble regression ``Icl = 0.835·Σclo + 0.161`` (D2).
ICL_SLOPE = 0.835
ICL_INTERCEPT = 0.161

#: ``required_clo = (34 - T)/(7.66·met) - 0.7`` clamped to [0, 4.5] (D3).
SKIN_TEMP_C = 34.0
CLO_MET_SLOPE = 7.66
BOUNDARY_LAYER_CLO = 0.7
REQUIRED_CLO_MIN = 0.0
REQUIRED_CLO_MAX = 4.5

#: Wind chill (JAG/TI 2001) is applied only above this wind speed (D4).
WIND_CHILL_MIN_KMH = 4.8
#: FR-5's ramps.  ``ramp_cold`` fades the wind-chill delta out over 10→14 °C;
#: ``ramp_heat`` fades the apparent-temperature delta in over 24→26 °C.
RAMP_COLD_LOW_C = 10.0
RAMP_COLD_HIGH_C = 14.0
RAMP_HEAT_LOW_C = 24.0
RAMP_HEAT_HIGH_C = 26.0
#: Effective wind multiplier by outermost-worn-layer windproofness 0/1/2 (D4).
WIND_ATTENUATION = (1.0, 0.6, 0.3)

#: Comfort band and the deviation at which an hour scores zero (D5).
COMFORT_BAND_CLO = 0.25
ZERO_SCORE_DEV_CLO = 1.0

#: Exposure weights (D18).
EXPOSURE_COMMUTE = 3.0
EXPOSURE_BASE = 1.0
#: ``S_thermal`` blends the weighted mean with the worst hour (FR-6.4).
THERMAL_MEAN_WEIGHT = 0.75
THERMAL_WORST_WEIGHT = 0.25

# --------------------------------------------------------------------------
# Plan smoothing (FR-7)
# --------------------------------------------------------------------------

CONFIG_SWITCH_HYSTERESIS = 0.10
MIN_DWELL_HOURS = 2
MAX_CONFIG_CHANGES = 3
MAX_MIDS = 2

# --------------------------------------------------------------------------
# Precipitation and wind rules (FR-9, D7)
# --------------------------------------------------------------------------

POP_HARD = 0.5
POP_SOFT = 0.3
INTENSITY_LIGHT_MAX_MMH = 2.5
INTENSITY_MODERATE_MAX_MMH = 10.0
UMBRELLA_MAX_WIND_KMH = 35.0
WIND_PENALTY_KMH = 30.0
PENALTY_RAIN_UNCOVERED = 0.50
PENALTY_SOFT_RAIN = 0.25
PENALTY_WIND_UNBLOCKED = 0.20
PENALTY_WIND_PARTIAL = 0.10
#: Worn waterproofness that each intensity class demands.
COVER_REQUIRED = {"light": 1, "moderate": 2, "heavy": 2}
#: Classes an umbrella may cover (never heavy, D7).
UMBRELLA_COVERS = ("light", "moderate")

# --------------------------------------------------------------------------
# Advisories (FR-9, FR-16)
# --------------------------------------------------------------------------

COLD_EXTREMITY_C = 5.0
UV_ADVISORY_INDEX = 6.0
UV_WINDOW = (10, 16)
LEG_BASE_MAX_BARE_C = 0.0
#: FR-16 layer archetypes, bracketing required clo.
ARCHETYPE_BOUNDS = (0.55, 1.10, 1.70)
ARCHETYPE_NAMES = ("base", "base+mid", "base+mid+shell", "base+2mid+insulated shell")

# --------------------------------------------------------------------------
# Objective and search (D12, D13)
# --------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "thermal": 0.40,
    "protection": 0.15,
    "color": 0.15,
    "style": 0.15,
    "variety": 0.15,
}
VARIETY_HALF_LIFE_DAYS = 3.0
CANDIDATE_CAP = 40
MMR_JACCARD_MAX = 0.5
#: How many scored outfits the bound-pruner keeps live.  MMR selects k ≤ 10
#: outfits from this pool; pruning can only discard outfits outside the top
#: ``MMR_POOL_SIZE`` by score.
MMR_POOL_SIZE = 200
WARDROBE_MAX = 500
MAX_ACCESSORIES = 4
ACCESSORY_PRIORITY = ("umbrella", "gloves", "hat", "scarf", "sunglasses")

# --------------------------------------------------------------------------
# Request defaults (D15, DATA_MODEL.md §5)
# --------------------------------------------------------------------------

MET_DEFAULT = 1.6
DEFAULT_WEAR_WINDOW = (7, 22)
DEFAULT_COMMUTE_HOURS = (7, 8, 9, 17, 18, 19)
DEFAULT_K = 3
DEFAULT_OCCASIONS = ("casual", "work", "sport", "outdoor", "formal")

# --------------------------------------------------------------------------
# Colour harmony table (D8)
# --------------------------------------------------------------------------

#: ``(exclusive upper bound on Δh, score)`` — Itten zones, D8.
HUE_ZONES: tuple[tuple[float, float], ...] = (
    (15.0, 0.90),  # monochromatic
    (45.0, 0.85),  # analogous
    (105.0, 0.35),  # clash
    (150.0, 0.70),  # triadic zone
    (180.0, 0.80),  # complementary
)
HUE_FAMILY_DEGREES = 30.0
MAX_HUE_FAMILIES = 3
HUE_FAMILY_PENALTY = 0.15
STYLE_FORMALITY_WEIGHT = 0.6
STYLE_TAG_WEIGHT = 0.4
#: Formality tightness: spread 0 → 1.0, spread 1 → 0.7 (FR-10), -0.3 per step.
STYLE_SPREAD_STEP = 0.3

# --------------------------------------------------------------------------
# Type aliases
# --------------------------------------------------------------------------

LayerRole = Literal[
    "base", "mid", "outer", "bottom", "leg_base", "full_body", "footwear", "accessory"
]
AccessoryClass = Literal["hat", "gloves", "scarf", "umbrella", "sunglasses"]
GarmentStatus = Literal["clean", "dirty", "in_laundry", "retired"]
Slot = Literal[
    "base",
    "mid_1",
    "mid_2",
    "outer",
    "bottom",
    "leg_base",
    "footwear",
    "accessory_1",
    "accessory_2",
    "accessory_3",
    "accessory_4",
]
Provider = Literal["fixture", "open_meteo"]
SuggestionSource = Literal["fixture", "vision"]
SuggestionStatus = Literal["pending", "accepted", "rejected"]
IntensityClass = Literal["none", "light", "moderate", "heavy"]

CORE_SLOTS: tuple[str, ...] = (
    "base",
    "mid_1",
    "mid_2",
    "outer",
    "bottom",
    "leg_base",
    "footwear",
)
ACCESSORY_SLOTS: tuple[str, ...] = (
    "accessory_1",
    "accessory_2",
    "accessory_3",
    "accessory_4",
)
CORE_ROLES: frozenset[str] = frozenset(
    {"base", "mid", "outer", "bottom", "leg_base", "full_body", "footwear"}
)
#: Roles whose waterproofness counts as rain cover for the torso (FR-9).
COVER_ROLES: frozenset[str] = frozenset({"base", "full_body", "mid", "outer"})
#: Fields FR-1 records in ``overridden_fields`` and FR-2's cascade skips.
OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "clo",
    "layer_role",
    "formality",
    "wears_before_laundry",
)
#: Keys an :class:`AttributeSuggestion` payload may propose (DATA_MODEL.md §2.2).
SUGGESTIBLE_FIELDS: tuple[str, ...] = (
    "category",
    "layer_role",
    "colors",
    "style_tags",
    "formality",
)

# --------------------------------------------------------------------------
# Category presets (D1 warmth/role, D11 laundry)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CategoryPreset:
    """One row of SCOPE.md D1 plus its D11 laundry threshold."""

    clo: float
    layer_role: str
    formality: int
    wears_before_laundry: int
    source: str


_A55 = "ASHRAE 55"
_PRAC = "practice-calibrated"
_ISO = "ISO 9920-consistent"

CATEGORY_PRESETS: dict[str, CategoryPreset] = {
    # base tops
    "tshirt": CategoryPreset(0.08, "base", 2, 2, _A55),
    "long_sleeve_tee": CategoryPreset(0.12, "base", 2, 2, _A55),
    "polo": CategoryPreset(0.17, "base", 3, 2, _A55),
    "shirt_short_sleeve": CategoryPreset(0.19, "base", 3, 2, _A55),
    "shirt_long_sleeve": CategoryPreset(0.25, "base", 4, 2, _A55),
    "flannel_shirt": CategoryPreset(0.34, "base", 2, 2, _A55),
    # mids
    "sweater_thin": CategoryPreset(0.25, "mid", 3, 5, _A55),
    "cardigan": CategoryPreset(0.25, "mid", 3, 5, _A55),
    "sweater_thick": CategoryPreset(0.36, "mid", 3, 5, _A55),
    "hoodie": CategoryPreset(0.36, "mid", 2, 5, _A55),
    "fleece": CategoryPreset(0.30, "mid", 2, 5, _PRAC),
    "blazer": CategoryPreset(0.36, "mid", 4, 30, _A55),
    "suit_jacket": CategoryPreset(0.36, "mid", 5, 30, _A55),
    "thermal_top": CategoryPreset(0.20, "mid", 2, 2, _A55),
    # outers
    "rain_shell": CategoryPreset(0.25, "outer", 2, 30, _PRAC),
    "light_jacket": CategoryPreset(0.40, "outer", 3, 30, _PRAC),
    "wool_coat": CategoryPreset(0.60, "outer", 4, 30, _PRAC),
    "parka": CategoryPreset(0.70, "outer", 2, 30, _ISO),
    "down_jacket": CategoryPreset(0.70, "outer", 2, 30, _ISO),
    # bottoms
    "trousers_thin": CategoryPreset(0.15, "bottom", 4, 5, _A55),
    "trousers_thick": CategoryPreset(0.24, "bottom", 4, 5, _A55),
    "jeans": CategoryPreset(0.24, "bottom", 2, 5, _A55),
    "shorts": CategoryPreset(0.08, "bottom", 1, 3, _A55),
    "skirt_thin": CategoryPreset(0.14, "bottom", 3, 3, _A55),
    "skirt_thick": CategoryPreset(0.23, "bottom", 4, 3, _A55),
    # leg base
    "thermal_bottoms": CategoryPreset(0.15, "leg_base", 2, 2, _A55),
    # full body
    "dress_light": CategoryPreset(0.23, "full_body", 3, 2, _A55),
    "dress_long_sleeve": CategoryPreset(0.33, "full_body", 4, 2, _A55),
    # footwear (hosiery included, D1)
    "shoes": CategoryPreset(0.02, "footwear", 4, 999, _A55),
    "sneakers": CategoryPreset(0.02, "footwear", 2, 999, _A55),
    "sandals": CategoryPreset(0.02, "footwear", 1, 999, _A55),
    "boots": CategoryPreset(0.10, "footwear", 3, 999, _A55),
    # accessories (advisory only, D6)
    "hat": CategoryPreset(0.0, "accessory", 2, 999, "advisory"),
    "gloves": CategoryPreset(0.0, "accessory", 2, 999, "advisory"),
    "scarf": CategoryPreset(0.0, "accessory", 3, 999, "advisory"),
    "umbrella": CategoryPreset(0.0, "accessory", 3, 999, "advisory"),
    "sunglasses": CategoryPreset(0.0, "accessory", 2, 999, "advisory"),
}

#: Category → accessory class, for the five accessory categories.
ACCESSORY_CATEGORY_CLASS: dict[str, str] = {
    "hat": "hat",
    "gloves": "gloves",
    "scarf": "scarf",
    "umbrella": "umbrella",
    "sunglasses": "sunglasses",
}

#: Maximum allowed deviation of a garment's clo from its category preset (FR-1).
CLO_PRESET_TOLERANCE = 0.15
CLO_ABS_MIN = 0.0
CLO_ABS_MAX = 1.5

#: Warmth level 0-5 maps linearly onto the category preset ±0.15 (FR-1).  The
#: level is a coarse alternative to typing a clo; omitting it yields the preset
#: exactly, and supplying it counts as an explicit override.
WARMTH_LEVEL_OFFSETS: tuple[float, ...] = (-0.15, -0.09, -0.03, 0.03, 0.09, 0.15)


def clo_bounds(category: str) -> tuple[float, float]:
    """Allowed clo interval for ``category`` (FR-1: preset ±0.15 ∩ [0, 1.5])."""
    preset = CATEGORY_PRESETS[category]
    if preset.layer_role == "accessory":
        return (0.0, 0.0)
    lo = max(CLO_ABS_MIN, round(preset.clo - CLO_PRESET_TOLERANCE, 10))
    hi = min(CLO_ABS_MAX, round(preset.clo + CLO_PRESET_TOLERANCE, 10))
    return (lo, hi)


def warmth_to_clo(category: str, level: int) -> float:
    """Map a warmth level 0-5 to a clo inside the category's allowed range."""
    if category not in CATEGORY_PRESETS:
        raise InvalidParams(f"unknown category {category!r}", field="category")
    if not 0 <= level <= 5:
        raise InvalidParams("warmth level must be 0-5", field="warmth", value=level)
    preset = CATEGORY_PRESETS[category]
    lo, hi = clo_bounds(category)
    return round(min(hi, max(lo, preset.clo + WARMTH_LEVEL_OFFSETS[level])), 6)


# --------------------------------------------------------------------------
# Garment state machine (FR-3)
# --------------------------------------------------------------------------

ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("clean", "dirty"),
        ("dirty", "in_laundry"),
        ("dirty", "clean"),
        ("in_laundry", "clean"),
        ("clean", "retired"),
        ("dirty", "retired"),
        ("in_laundry", "retired"),
    }
)


def check_transition(old: str, new: str) -> None:
    """Raise :class:`InvalidTransition` unless ``old → new`` is sanctioned."""
    from dresscast.errors import InvalidTransition

    if old == new:
        return
    if (old, new) not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"cannot move a garment from {old!r} to {new!r}", old=old, new=new)


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


class Color(BaseModel):
    """One entry of ``Garment.colors`` (DATA_MODEL.md §2.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    hue: float | None = Field(default=None, ge=0.0, le=360.0)
    neutral: bool = False
    role: Literal["main", "accent"] = "main"

    @model_validator(mode="after")
    def _hue_iff_not_neutral(self) -> Color:
        if self.neutral and self.hue is not None:
            raise ValueError("a neutral color must not carry a hue")
        if not self.neutral and self.hue is None:
            raise ValueError("a non-neutral color must carry a hue")
        if self.hue == 360.0:
            self.hue = 0.0
        return self


class Garment(BaseModel):
    """The wardrobe inventory record (DATA_MODEL.md §2.1)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str
    layer_role: LayerRole
    accessory_class: AccessoryClass | None = None
    clo: float = Field(ge=CLO_ABS_MIN, le=CLO_ABS_MAX)
    waterproofness: int = Field(default=0, ge=0, le=3)
    windproofness: int = Field(default=0, ge=0, le=2)
    formality: int = Field(ge=1, le=5)
    colors: list[Color] = Field(min_length=1, max_length=3)
    style_tags: list[str] = Field(default_factory=list)
    occasions: list[str] = Field(default_factory=list)
    wears_before_laundry: int = Field(ge=1)
    wears_since_wash: int = Field(default=0, ge=0)
    status: GarmentStatus = "clean"
    overridden_fields: list[str] = Field(default_factory=list)
    photo_path: str | None = None
    photo_sha256: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORY_PRESETS:
            raise ValueError(f"unknown category {v!r}")
        return v

    @field_validator("style_tags", "occasions")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for tag in v:
            t = tag.strip().lower()
            if not t:
                raise ValueError("tags must be non-empty")
            if t not in out:
                out.append(t)
        return out

    @field_validator("overridden_fields")
    @classmethod
    def _known_overrides(cls, v: list[str]) -> list[str]:
        for f in v:
            if f not in OVERRIDABLE_FIELDS:
                raise ValueError(f"{f!r} is not an overridable field")
        return sorted(set(v))

    @model_validator(mode="after")
    def _invariants(self) -> Garment:
        mains = [c for c in self.colors if c.role == "main"]
        if len(mains) != 1:
            raise ValueError("exactly one color must have role 'main'")
        is_accessory = self.layer_role == "accessory"
        if is_accessory != (self.accessory_class is not None):
            raise ValueError("accessory_class is set iff layer_role is 'accessory'")
        if (self.photo_path is None) != (self.photo_sha256 is None):
            raise ValueError("photo_path and photo_sha256 must be set together")
        if not is_accessory and not self.occasions:
            raise ValueError("occasions must be non-empty for non-accessory garments")
        lo, hi = clo_bounds(self.category)
        if not (lo - 1e-9 <= self.clo <= hi + 1e-9):
            raise ValueError(
                f"clo {self.clo} outside {lo:.2f}-{hi:.2f} for category {self.category}"
            )
        return self

    # -- helpers -----------------------------------------------------------

    @property
    def main_color(self) -> Color:
        return next(c for c in self.colors if c.role == "main")

    @property
    def is_core(self) -> bool:
        return self.layer_role in CORE_ROLES

    def hash_record(self) -> dict[str, Any]:
        """The FR-19 ``wardrobe_hash`` projection of this garment."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "layer_role": self.layer_role,
            "accessory_class": self.accessory_class,
            "clo": round(self.clo, PLAN_DP),
            "waterproofness": self.waterproofness,
            "windproofness": self.windproofness,
            "formality": self.formality,
            "colors": [c.model_dump() for c in self.colors],
            "style_tags": list(self.style_tags),
            "occasions": list(self.occasions),
            "wears_before_laundry": self.wears_before_laundry,
            "wears_since_wash": self.wears_since_wash,
            "status": self.status,
        }


def wardrobe_hash(garments: list[Garment]) -> str:
    """FR-19: SHA-256 over every **non-retired** garment, request-independent."""
    records = sorted(
        (g.hash_record() for g in garments if g.status != "retired"),
        key=lambda r: r["id"],
    )
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cascade_category(garment: Garment, new_category: str) -> dict[str, Any]:
    """FR-2 step 2: fields re-derived from ``new_category``'s D1/D11 preset.

    Fields listed in the garment's ``overridden_fields`` are skipped, so an
    explicitly user-set clo is never silently rewritten.
    """
    if new_category not in CATEGORY_PRESETS:
        raise InvalidParams(f"unknown category {new_category!r}", field="category")
    preset = CATEGORY_PRESETS[new_category]
    derived: dict[str, Any] = {
        "clo": preset.clo,
        "layer_role": preset.layer_role,
        "formality": preset.formality,
        "wears_before_laundry": preset.wears_before_laundry,
    }
    overridden = set(garment.overridden_fields)
    return {k: v for k, v in derived.items() if k not in overridden}


def apply_wear(garment: Garment, now: datetime) -> Garment:
    """FR-3: advance a garment one wear, flipping to ``dirty`` at threshold."""
    worn = garment.wears_since_wash + 1
    status = garment.status
    if status == "clean" and worn >= garment.wears_before_laundry:
        status = "dirty"
    return garment.model_copy(
        update={"wears_since_wash": worn, "status": status, "updated_at": now}
    )


def undo_wear(garment: Garment, now: datetime) -> Garment:
    """Reverse :func:`apply_wear` for a same-day undo (FR-12)."""
    worn = max(0, garment.wears_since_wash - 1)
    status = garment.status
    if status == "dirty" and worn < garment.wears_before_laundry:
        status = "clean"
    return garment.model_copy(
        update={"wears_since_wash": worn, "status": status, "updated_at": now}
    )


def wash(garment: Garment, now: datetime) -> Garment:
    """FR-3 laundry event: reset counters and return the garment to clean."""
    check_transition(garment.status, "clean")
    return garment.model_copy(update={"wears_since_wash": 0, "status": "clean", "updated_at": now})


class AttributeSuggestionPayload(BaseModel):
    """What an :class:`~dresscast.adapters.extractor.AttributeExtractor` proposes."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]
    confidences: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _suggestible(self) -> AttributeSuggestionPayload:
        for key in self.fields:
            if key not in SUGGESTIBLE_FIELDS:
                raise ValueError(f"{key!r} is not a suggestible field")
        for key, conf in self.confidences.items():
            if key not in self.fields:
                raise ValueError(f"confidence for unproposed field {key!r}")
            if not 0.0 <= conf <= 1.0:
                raise ValueError("confidence must lie in [0, 1]")
        return self

    def as_payload(self) -> dict[str, Any]:
        """DATA_MODEL.md §2.2's stored shape: ``{field: {value, confidence}}``."""
        return {
            k: {"value": v, "confidence": self.confidences.get(k, 1.0)}
            for k, v in self.fields.items()
        }


class AcceptedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    via: Literal["explicit", "cascade"]
    old: Any = None
    new: Any = None


class AttributeSuggestion(BaseModel):
    """Append-only staged extractor output (DATA_MODEL.md §2.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    garment_id: str
    source: SuggestionSource
    payload: dict[str, Any]
    status: SuggestionStatus = "pending"
    accepted_fields: list[AcceptedField] | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    @model_validator(mode="after")
    def _invariants(self) -> AttributeSuggestion:
        if (self.status == "pending") != (self.resolved_at is None):
            raise ValueError("resolved_at is set iff the status is not pending")
        if (self.status == "accepted") != (self.accepted_fields is not None):
            raise ValueError("accepted_fields is set iff the status is accepted")
        for key in self.payload:
            if key not in SUGGESTIBLE_FIELDS:
                raise ValueError(f"{key!r} is not a suggestible field")
        return self


class HourlyWeather(BaseModel):
    """One forecast hour (DATA_MODEL.md §2.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=0, le=24)
    hour: int = Field(ge=0, le=23)
    temp_c: float = Field(ge=-60.0, le=60.0)
    wind_kmh: float = Field(ge=0.0, le=250.0)
    humidity_pct: float = Field(ge=0.0, le=100.0)
    precip_prob: float = Field(ge=0.0, le=1.0)
    precip_mmh: float = Field(ge=0.0)
    uv_index: float = Field(ge=0.0, le=16.0)


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "home"
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    timezone: str = "UTC"


class ForecastSnapshot(BaseModel):
    """Append-only snapshot metadata (DATA_MODEL.md §2.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    date: str
    location_name: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    timezone: str
    provider: Provider
    fetched_at: datetime
    raw: dict[str, Any] | None = None

    @field_validator("date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        date_cls.fromisoformat(v)
        return v


class DayForecast(ForecastSnapshot):
    """A snapshot together with its 23-25 hourly rows (FR-4)."""

    hours: list[HourlyWeather]

    @model_validator(mode="after")
    def _hours_valid(self) -> DayForecast:
        n = len(self.hours)
        if not 23 <= n <= 25:
            raise ValueError(f"a local day must have 23-25 hourly rows, got {n}")
        for i, h in enumerate(self.hours):
            if h.seq != i:
                raise ValueError(f"seq must be contiguous from 0; row {i} has seq {h.seq}")
        return self

    def window_hours(self, window: tuple[int, int]) -> list[HourlyWeather]:
        """Rows whose wall-clock hour lies in ``[start, end)`` — ordered by seq.

        A DST fold repeats a wall-clock hour; both copies are returned, which
        is correct: the user lives through both (DATA_MODEL.md §2.3).
        """
        start, end = window
        return [h for h in self.hours if start <= h.hour < end]


class RequestParams(BaseModel):
    """A recommendation request (DATA_MODEL.md §2.4 ``params``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: str
    occasion: str
    wear_window: tuple[int, int] = DEFAULT_WEAR_WINDOW
    commute_hours: tuple[int, ...] = DEFAULT_COMMUTE_HOURS
    met: float = Field(default=MET_DEFAULT, gt=0.0, le=5.0)
    k: int = Field(default=DEFAULT_K, ge=1, le=10)
    seed: int = 0
    weights: dict[str, float] = Field(default_factory=lambda: dict(WEIGHTS))
    thresholds_version: str = THRESHOLDS_VERSION

    @field_validator("date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        date_cls.fromisoformat(v)
        return v

    @field_validator("occasion")
    @classmethod
    def _occasion(cls, v: str) -> str:
        out = v.strip().lower()
        if not out:
            raise ValueError("occasion must be non-empty")
        return out

    @field_validator("wear_window")
    @classmethod
    def _window(cls, v: tuple[int, int]) -> tuple[int, int]:
        start, end = v
        if not (0 <= start < end <= 24):
            raise ValueError("wear_window must satisfy 0 <= start < end <= 24")
        return v

    @field_validator("commute_hours")
    @classmethod
    def _commute(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        for h in v:
            if not 0 <= h <= 23:
                raise ValueError("commute hours must lie in 0-23")
        return tuple(sorted(set(v)))

    def exposure_weight(self, hour: int) -> float:
        """FR-6.4's ``w_h`` — commute hours count triple (D18)."""
        return EXPOSURE_COMMUTE if hour in self.commute_hours else EXPOSURE_BASE


# --------------------------------------------------------------------------
# Hot-path value objects (frozen dataclasses — see the module docstring)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerConfig:
    """One on/off configuration of an outfit's removable layers (FR-7)."""

    index: int
    worn_slots: tuple[str, ...]
    garment_ids: tuple[str, ...]
    icl: float
    windproofness: int
    cover: int
    layer_count: int


@dataclass(frozen=True, slots=True)
class CoreOutfit:
    """The core (non-accessory) garments of one candidate outfit (HC-1)."""

    base: Garment  # a ``base`` top or a ``full_body`` dress
    bottom: Garment | None
    mids: tuple[Garment, ...]  # ascending (clo, id): mid_1 then mid_2
    outer: Garment | None
    leg_base: Garment | None
    footwear: Garment

    def slot_items(self) -> tuple[tuple[str, Garment], ...]:
        out: list[tuple[str, Garment]] = [("base", self.base)]
        if len(self.mids) > 0:
            out.append(("mid_1", self.mids[0]))
        if len(self.mids) > 1:
            out.append(("mid_2", self.mids[1]))
        if self.outer is not None:
            out.append(("outer", self.outer))
        if self.bottom is not None:
            out.append(("bottom", self.bottom))
        if self.leg_base is not None:
            out.append(("leg_base", self.leg_base))
        out.append(("footwear", self.footwear))
        return tuple(out)

    def garments(self) -> tuple[Garment, ...]:
        out: list[Garment] = [self.base, *self.mids]
        if self.outer is not None:
            out.append(self.outer)
        if self.bottom is not None:
            out.append(self.bottom)
        if self.leg_base is not None:
            out.append(self.leg_base)
        out.append(self.footwear)
        return tuple(out)

    def core_ids(self) -> frozenset[str]:
        return frozenset(g.id for g in self.garments())

    def id_tuple(self) -> tuple[str, ...]:
        return tuple(sorted(g.id for g in self.garments()))

    def formality_spread(self) -> int:
        vals = [g.formality for g in self.garments()]
        return max(vals) - min(vals)


@dataclass(frozen=True, slots=True)
class SlotCandidates:
    """Per-slot candidate lists after HC-2/HC-3 filtering and D12's cap."""

    base: tuple[Garment, ...]
    full_body: tuple[Garment, ...]
    bottom: tuple[Garment, ...]
    mid: tuple[Garment, ...]
    outer: tuple[Garment, ...]
    leg_base: tuple[Garment, ...]
    footwear: tuple[Garment, ...]
    accessories: tuple[Garment, ...]

    def core_pool(self) -> tuple[Garment, ...]:
        return (
            self.base
            + self.full_body
            + self.bottom
            + self.mid
            + self.outer
            + self.leg_base
            + self.footwear
        )


@dataclass(frozen=True, slots=True)
class Band:
    """FR-6.2's achievable insulation band, in Icl clo."""

    ceiling: float
    floor: tuple[float, ...]  # one entry per wear-window hour

    def clamp(self, required: float, index: int) -> tuple[float, str | None]:
        lo = self.floor[index]
        hi = max(self.ceiling, lo)
        if required < lo:
            return lo, "wardrobe_floor"
        if required > hi:
            return hi, "wardrobe_ceiling"
        return required, None


@dataclass(frozen=True, slots=True)
class WearHistory:
    """Wear history projected for FR-11 and HC-8."""

    last_worn: dict[str, str]  # garment id -> ISO date of its most recent wear
    yesterday_sets: tuple[frozenset[str], ...]  # core-item sets worn yesterday

    @staticmethod
    def empty() -> WearHistory:
        return WearHistory(last_worn={}, yesterday_sets=())


# --------------------------------------------------------------------------
# Output models
# --------------------------------------------------------------------------


class HourPlanEntry(BaseModel):
    """One hour of an outfit's plan (DATA_MODEL.md §2.5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    hour: int
    temp_c: float
    wind_kmh: float
    humidity_pct: float
    precip_prob: float
    precip_mmh: float
    uv_index: float
    bare_feels_c: float
    effective_wind_kmh: float
    feels_c: float
    required_clo: float
    target_clo: float
    clamped: Literal["wardrobe_floor", "wardrobe_ceiling"] | None = None
    worn_slots: list[str]
    carried_slots: list[str]
    ensemble_clo: float
    deviation: float
    in_band: bool
    hour_score: float
    exposure_weight: float
    rain_cover_on: bool
    protect_score: float
    notes: list[str] = Field(default_factory=list)


class PlanSegment(BaseModel):
    """A maximal run of hours sharing one configuration (FR-7 compression)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_hour: int
    end_hour: int
    start_seq: int
    end_seq: int
    worn_slots: list[str]
    carried_slots: list[str]


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thermal: float
    protection: float
    color: float
    style: float
    variety: float
    weights: dict[str, float] = Field(default_factory=lambda: dict(WEIGHTS))


class ReasonLine(BaseModel):
    """One classified reasoning entry (FR-15)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    line_class: str = Field(alias="class")
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"class": self.line_class, "text": self.text}


class Note(BaseModel):
    """A run- or outfit-level note, e.g. ``partial_k`` (DATA_MODEL.md §2.4)."""

    model_config = ConfigDict(extra="allow", frozen=True)

    kind: str


class Compromise(BaseModel):
    """One applied FR-14 relaxation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    detail: str


class AccessoryAttachment(BaseModel):
    """One accessory attached by FR-9's post-assembly rule."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    garment_id: str
    accessory_class: str = Field(alias="class")
    trigger: str

    def as_dict(self) -> dict[str, str]:
        return {
            "garment_id": self.garment_id,
            "class": self.accessory_class,
            "trigger": self.trigger,
        }


class OutfitItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: str
    garment_id: str


class ScoredOutfit(BaseModel):
    """One ranked outfit (DATA_MODEL.md §2.5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = ""
    rank: int = Field(ge=1)
    score_total: float = Field(ge=0.0, le=1.0)
    scores: ScoreBreakdown
    items: list[OutfitItem]
    hour_plan: list[HourPlanEntry]
    reasoning: list[ReasonLine]
    accessories: list[AccessoryAttachment] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    compromises: list[Compromise] = Field(default_factory=list)

    def core_ids(self) -> frozenset[str]:
        return frozenset(i.garment_id for i in self.items if i.slot in CORE_SLOTS)


class Recommendation(BaseModel):
    """One assembly run (DATA_MODEL.md §2.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = ""
    date: str
    snapshot_id: str
    created_at: datetime
    engine_version: str = ENGINE_VERSION
    seed: int = 0
    params: RequestParams
    wardrobe_hash: str
    outfits: list[ScoredOutfit] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    compromises: list[Compromise] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ranks(self) -> Recommendation:
        for i, o in enumerate(self.outfits, start=1):
            if o.rank != i:
                raise ValueError("ranks must be contiguous from 1")
        scores = [o.score_total for o in self.outfits]
        if any(a < b - 1e-12 for a, b in pairwise(scores)):
            raise ValueError("score_total must be non-increasing in rank")
        return self


class WearLogItem(BaseModel):
    """One garment of a wear log, with its ``layer_role`` at log time (§2.7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    garment_id: str
    layer_role: LayerRole


class WearLog(BaseModel):
    """What was actually worn on a date (DATA_MODEL.md §2.7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = ""
    date: str
    source: Literal["recommendation", "manual"]
    outfit_id: str | None = None
    created_at: datetime
    items: list[WearLogItem] = Field(default_factory=list)

    @field_validator("date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        date_cls.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _invariants(self) -> WearLog:
        if (self.source == "recommendation") != (self.outfit_id is not None):
            raise ValueError("outfit_id is set iff the source is 'recommendation'")
        ids = [i.garment_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("a wear log may not list a garment twice")
        return self

    def core_ids(self) -> frozenset[str]:
        """Core items as recorded at log time — never re-derived (FR-11)."""
        return frozenset(i.garment_id for i in self.items if i.layer_role in CORE_ROLES)


class LaundryEvent(BaseModel):
    """A wash (DATA_MODEL.md §2.8), append-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = ""
    created_at: datetime
    note: str | None = None
    garment_ids: list[str] = Field(default_factory=list)


class Advisory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cold_extremities", "wind", "uv", "rain"]
    text: str
    value: float


class BriefHour(BaseModel):
    """One hour of the wardrobe-free day brief (FR-16)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    hour: int
    temp_c: float
    wind_kmh: float
    humidity_pct: float
    precip_prob: float
    precip_mmh: float
    uv_index: float
    bare_feels_c: float
    required_clo: float
    archetype: str
    rain_cover_class: IntensityClass
    rain_required: bool


class DayBrief(BaseModel):
    """FR-16's wardrobe-free answer to "what does today demand?"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: str
    wear_window: tuple[int, int]
    met: float
    hours: list[BriefHour]
    required_clo_min: float
    required_clo_max: float
    bare_feels_min: float
    bare_feels_max: float
    archetype_range: list[str]
    advisories: list[Advisory] = Field(default_factory=list)


def q_score(value: float) -> float:
    """Round a score component to ``SCORE_DP`` — applied before ranking (FR-19)."""
    return round(value, SCORE_DP)


def q_plan(value: float) -> float:
    """Round a physical plan/brief quantity to ``PLAN_DP`` (FR-19)."""
    return round(value, PLAN_DP)
