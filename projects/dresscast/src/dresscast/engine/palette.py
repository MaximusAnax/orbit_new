"""Colour harmony (SCOPE.md FR-10, D8).

Each colour is (name, hue, neutral).  Neutrals pair with anything; non-neutral
pairs score by circular hue distance on Itten's wheel.  The stylists' "no more
than three colours" rule of thumb becomes a penalty on the number of distinct
non-neutral 30° hue families across every colour the outfit lists.
"""

from __future__ import annotations

from collections.abc import Sequence

from dresscast.engine.models import (
    HUE_FAMILY_DEGREES,
    HUE_FAMILY_PENALTY,
    HUE_ZONES,
    MAX_HUE_FAMILIES,
    Color,
    Garment,
)


def hue_distance(a: float, b: float) -> float:
    """Circular distance between two hues, in degrees (0-180)."""
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def pair_harmony(a: Color, b: Color) -> float:
    """D8's pairwise harmony: neutrals 1.0, otherwise the hue-zone table."""
    if a.neutral or b.neutral:
        return 1.0
    delta = hue_distance(a.hue or 0.0, b.hue or 0.0)
    for bound, score in HUE_ZONES:
        if delta <= bound:
            return score
    return HUE_ZONES[-1][1]


def hue_families(garments: Sequence[Garment]) -> set[int]:
    """Distinct non-neutral 30° hue families over **all** listed colours."""
    families: set[int] = set()
    for g in garments:
        for c in g.colors:
            if c.neutral or c.hue is None:
                continue
            families.add(int(c.hue // HUE_FAMILY_DEGREES) % 12)
    return families


def color_score(garments: Sequence[Garment]) -> float:
    """FR-10's ``S_color`` over an outfit's core items."""
    mains = [g.main_color for g in garments]
    pairs = [
        pair_harmony(mains[i], mains[j])
        for i in range(len(mains))
        for j in range(i + 1, len(mains))
    ]
    score = sum(pairs) / len(pairs) if pairs else 1.0
    if len(hue_families(garments)) > MAX_HUE_FAMILIES:
        score -= HUE_FAMILY_PENALTY
    return min(1.0, max(0.0, score))
