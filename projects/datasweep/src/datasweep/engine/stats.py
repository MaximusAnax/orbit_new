"""Robust univariate statistics (SCOPE.md D11).

Mean/sigma are useless for outlier hunting because both are corrupted by the very
values being hunted (breakdown point 0).  Everything here is median/MAD based
(breakdown point 50%) or a quantile, and every function is pure and
deterministic — no numpy, no random tie-breaking.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Iglewicz & Hoaglin's consistency constant for the modified z-score.
MAD_SCALE = 0.6745
#: Fallback constant used when MAD is 0 (Iglewicz & Hoaglin 1993, eq. 3).
MEAN_AD_SCALE = 1.253314


def median(values: list[float]) -> float:
    """Median with the usual mean-of-two-middles rule for even counts."""
    if not values:
        raise ValueError("median of an empty sequence")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolation quantile (the ``numpy.percentile`` default method).

    ``q`` is in [0, 1].  Chosen over Tukey's hinges because it is the
    convention every downstream reader will expect from an "IQR" column stat.
    """
    if not values:
        raise ValueError("quantile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be within [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def mad(values: list[float], center: float | None = None) -> float:
    """Median absolute deviation."""
    if not values:
        raise ValueError("mad of an empty sequence")
    mid = median(values) if center is None else center
    return median([abs(v - mid) for v in values])


def mean_absolute_deviation(values: list[float], center: float) -> float:
    return sum(abs(v - center) for v in values) / len(values)


@dataclass(frozen=True)
class RobustSummary:
    """Everything the OUT detector and the numeric column profile need."""

    n: int
    minimum: float
    q1: float
    median: float
    q3: float
    maximum: float
    iqr: float
    mad: float
    lower_fence: float
    upper_fence: float

    def modified_z(self, value: float) -> float:
        """Iglewicz-Hoaglin modified z-score ``0.6745 (x - median) / MAD``.

        When MAD is 0 (more than half the values are identical) the score is
        undefined; the published fallback substitutes the mean absolute
        deviation.  With both at 0 every value equals the median, so the score
        is 0 by definition.
        """
        if self.mad > 0:
            return MAD_SCALE * (value - self.median) / self.mad
        if self._mean_ad > 0:
            return (value - self.median) / (MEAN_AD_SCALE * self._mean_ad)
        return 0.0

    def outside_fences(self, value: float) -> bool:
        return value < self.lower_fence or value > self.upper_fence

    _mean_ad: float = 0.0


def summarize(values: list[float], iqr_k: float) -> RobustSummary:
    """Quantiles, MAD and Tukey fences at multiplier ``iqr_k`` (D11: k = 3.0)."""
    if not values:
        raise ValueError("summarize of an empty sequence")
    q1 = quantile(values, 0.25)
    q3 = quantile(values, 0.75)
    med = median(values)
    iqr = q3 - q1
    dispersion = mad(values, med)
    return RobustSummary(
        n=len(values),
        minimum=min(values),
        q1=q1,
        median=med,
        q3=q3,
        maximum=max(values),
        iqr=iqr,
        mad=dispersion,
        lower_fence=q1 - iqr_k * iqr,
        upper_fence=q3 + iqr_k * iqr,
        _mean_ad=mean_absolute_deviation(values, med),
    )
