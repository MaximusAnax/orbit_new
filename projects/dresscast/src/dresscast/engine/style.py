"""Formality coherence, tag cohesion and the occasion filters (FR-10, HC-3/HC-4).

Formality enforces *internal* coherence (D9's 5-step dress-code ladder);
occasions enforce *external* fit.  There is deliberately no occasion→formality
band — the two mechanisms together are sufficient (D9).
"""

from __future__ import annotations

from collections.abc import Sequence

from dresscast.engine.models import (
    STYLE_FORMALITY_WEIGHT,
    STYLE_SPREAD_STEP,
    STYLE_TAG_WEIGHT,
    Garment,
)


def formality_spread(garments: Sequence[Garment]) -> int:
    """``max - min`` formality over the given garments (HC-4's quantity)."""
    if not garments:
        return 0
    values = [g.formality for g in garments]
    return max(values) - min(values)


def formality_coherent(garments: Sequence[Garment], max_spread: int = 1) -> bool:
    """HC-4: is the formality spread within ``max_spread`` (2 under FR-14 R2)?"""
    return formality_spread(garments) <= max_spread


def occasion_ok(garment: Garment, occasion: str) -> bool:
    """HC-3: does this garment list the requested occasion?"""
    return occasion in garment.occasions


def formality_tightness(spread: int) -> float:
    """FR-10: 1.0 at spread 0, 0.7 at spread 1, -0.3 per further step."""
    return max(0.0, 1.0 - STYLE_SPREAD_STEP * spread)


def tag_cohesion(garments: Sequence[Garment]) -> float:
    """Fraction of core-item pairs sharing at least one style tag."""
    n = len(garments)
    if n < 2:
        return 1.0
    shared = 0
    total = 0
    for i in range(n):
        tags_i = set(garments[i].style_tags)
        for j in range(i + 1, n):
            total += 1
            if tags_i & set(garments[j].style_tags):
                shared += 1
    return shared / total


def style_score(garments: Sequence[Garment]) -> float:
    """FR-10's ``S_style`` = 0.6·formality tightness + 0.4·tag cohesion."""
    tight = formality_tightness(formality_spread(garments))
    cohesion = tag_cohesion(garments)
    return min(1.0, max(0.0, STYLE_FORMALITY_WEIGHT * tight + STYLE_TAG_WEIGHT * cohesion))
