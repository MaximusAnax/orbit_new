"""Pure domain logic: deterministic, no network, no filesystem, no clock reads."""

from dresscast.engine.assemble import recommend
from dresscast.engine.comfort import (
    achievable_band,
    build_day_context,
    day_brief,
    ensemble_clo,
    feels_like,
    hourly_plan,
    layer_configs,
    required_clo,
    thermal_score,
)
from dresscast.engine.palette import color_score
from dresscast.engine.protection import attach_accessories, protect_score
from dresscast.engine.style import style_score
from dresscast.engine.variety import variety_score

__all__ = [
    "achievable_band",
    "attach_accessories",
    "build_day_context",
    "color_score",
    "day_brief",
    "ensemble_clo",
    "feels_like",
    "hourly_plan",
    "layer_configs",
    "protect_score",
    "recommend",
    "required_clo",
    "style_score",
    "thermal_score",
    "variety_score",
]
