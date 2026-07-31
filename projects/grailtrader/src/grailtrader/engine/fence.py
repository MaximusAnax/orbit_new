"""FR-3: the MAD outlier fence.

Within an index window a sold listing is excluded iff::

    |ln(p_adj) - median_w| > max(fence_sigma * sigma_hat_w, fence_floor_log)

with ``sigma_hat_w = 1.4826 * MAD`` of ``ln p_adj`` over the window. The MAD term
adapts to genuinely volatile strata; the floor keeps the fence meaningful in
small windows.

This is a *price-implausibility* defence, not an authenticity check (SCOPE
non-goal 3): a counterfeit priced near market survives it by construction.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["MAD_TO_SIGMA", "FenceResult", "apply_fence", "fence_cutoff"]

#: Consistency constant making the MAD an unbiased sigma estimator for a normal.
MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class FenceResult:
    """Which window members survived the fence, and the cutoff that was applied."""

    kept: tuple[int, ...]
    excluded: tuple[int, ...]
    median_log: float
    sigma_hat: float
    cutoff: float

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_excluded(self) -> int:
        return len(self.excluded)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def fence_cutoff(sigma_hat: float, *, fence_sigma: float, fence_floor_log: float) -> float:
    """``max(fence_sigma * sigma_hat, fence_floor_log)`` — the effective log cutoff."""
    return max(fence_sigma * sigma_hat, fence_floor_log)


def apply_fence(
    prices: Sequence[float], *, fence_sigma: float, fence_floor_log: float
) -> FenceResult:
    """Apply the FR-3 fence to condition-adjusted prices, returning kept/excluded indices.

    ``prices`` are ``p_adj`` values and must all be strictly positive.
    """
    if not prices:
        return FenceResult((), (), 0.0, 0.0, fence_floor_log)
    if any(p <= 0 for p in prices):
        raise ValueError("FR-3 fence requires strictly positive condition-adjusted prices")
    logs = [math.log(p) for p in prices]
    median_log = _median(logs)
    mad = _median([abs(x - median_log) for x in logs])
    sigma_hat = MAD_TO_SIGMA * mad
    cutoff = fence_cutoff(sigma_hat, fence_sigma=fence_sigma, fence_floor_log=fence_floor_log)
    kept: list[int] = []
    excluded: list[int] = []
    for position, value in enumerate(logs):
        (excluded if abs(value - median_log) > cutoff else kept).append(position)
    return FenceResult(tuple(kept), tuple(excluded), median_log, sigma_hat, cutoff)
