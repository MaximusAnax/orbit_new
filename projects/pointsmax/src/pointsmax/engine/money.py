"""Integer money and points primitives (FR-8, SCOPE decision 11).

All money is in integer **cents**, all valuations in integer **milli-cents per
point** (``cpp_milli``).  There are no floats in the engine; every rounding rule
below is stated by the docs:

* baseline value floors (``// 1000``);
* transfer fees round **up** then cap;
* portal point prices round **up**, then up to the option's increment;
* delivered transfer amounts are exact by the FR-1 divisibility invariant.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..models import CashoutOption, TransferEdge


def ceil_div(numerator: int, denominator: int) -> int:
    """Ceiling division for non-negative integers."""
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return -((-numerator) // denominator)


def ceil_to_multiple(value: int, multiple: int) -> int:
    """Smallest multiple of ``multiple`` that is >= ``value``."""
    if multiple <= 0:
        raise ValueError("multiple must be positive")
    return ceil_div(value, multiple) * multiple


def floor_to_multiple(value: int, multiple: int) -> int:
    """Largest multiple of ``multiple`` that is <= ``value`` (0 when value < 0)."""
    if multiple <= 0:
        raise ValueError("multiple must be positive")
    if value < 0:
        return 0
    return (value // multiple) * multiple


def value_of_points(points: int, cpp_milli: int) -> int:
    """Baseline value in cents of ``points`` at ``cpp_milli`` — floors (FR-8)."""
    return (points * cpp_milli) // 1000


def portfolio_value(holdings: Mapping[str, int], mcpp: Mapping[str, int]) -> int:
    """``V(H) = Sum_p (H_p x mcpp_p) // 1000`` — the FR-8 portfolio valuation."""
    total = 0
    for program_id, points in holdings.items():
        if points:
            total += value_of_points(points, mcpp[program_id])
    return total


def delivered_points(edge: TransferEdge, sent: int) -> int:
    """``delivered(s) = s x ratio_to // ratio_from + bonus_to x (s // bonus_per_from)``.

    Exact for valid sent amounts by FR-1 invariant 2.
    """
    if sent < 0:
        raise ValueError("sent must be non-negative")
    base = sent * edge.ratio_to // edge.ratio_from
    if edge.bonus_per_from and edge.bonus_to:
        base += edge.bonus_to * (sent // edge.bonus_per_from)
    return base


def transfer_fee_cents(edge: TransferEdge, sent: int) -> int:
    """``min(fee_cap_cents, ceil(sent x fee_mcpp / 1000))`` on the *merged* amount."""
    if sent <= 0 or edge.fee_mcpp == 0:
        return 0
    fee = ceil_div(sent * edge.fee_mcpp, 1000)
    if edge.fee_cap_cents is not None:
        fee = min(fee, edge.fee_cap_cents)
    return fee


def is_valid_sent(edge: TransferEdge, sent: int) -> bool:
    """True when ``sent`` respects the edge's minimum and increment."""
    return sent >= edge.min_from and sent % edge.increment_from == 0


def max_valid_sent(edge: TransferEdge, balance: int) -> int | None:
    """Largest valid sent amount <= ``balance``, or None when none exists."""
    candidate = floor_to_multiple(balance, edge.increment_from)
    if candidate < edge.min_from:
        return None
    return candidate


def smallest_sent_covering(edge: TransferEdge, need: int, ceiling: int) -> int | None:
    """Smallest valid sent amount <= ``ceiling`` delivering at least ``need``.

    ``delivered`` is non-decreasing in ``sent``, so this is a binary search over
    increment multiples.  Returns None when even ``ceiling`` cannot cover.
    """
    if need <= 0:
        return None
    top = max_valid_sent(edge, ceiling)
    if top is None or delivered_points(edge, top) < need:
        return None
    inc = edge.increment_from
    lo = ceil_div(edge.min_from, inc)
    hi = top // inc
    while lo < hi:
        mid = (lo + hi) // 2
        if delivered_points(edge, mid * inc) >= need:
            hi = mid
        else:
            lo = mid + 1
    return lo * inc


def redeemable_points(points: int, option: CashoutOption) -> int:
    """Largest multiple of ``increment`` <= ``points`` and >= ``min_points`` (FR-13).

    Returns 0 when nothing is redeemable; the unredeemable remainder is worth 0,
    which is the point of a floor.
    """
    if points <= 0:
        return 0
    amount = floor_to_multiple(points, option.increment)
    if amount < option.min_points:
        return 0
    return amount


def cashout_value_cents(points: int, option: CashoutOption) -> int:
    """Cents received for redeeming exactly ``points`` through ``option``."""
    return value_of_points(points, option.cpp_milli)


def portal_points_price(fare_cents: int, option: CashoutOption) -> int:
    """Points needed to buy ``fare_cents`` of travel through a portal option (FR-8).

    ``ceil(fare_cents x 1000 / cpp_milli)`` rounded up to the option's increment
    and at least ``min_points``.
    """
    if fare_cents <= 0:
        raise ValueError("fare must be positive")
    raw = ceil_div(fare_cents * 1000, option.cpp_milli)
    return max(ceil_to_multiple(raw, option.increment), option.min_points)


def realized_cpp_milli(value_cents: int, fees_cents: int, points_spent: int) -> int | None:
    """``(value - fees) x 1000 // points`` — the hobby cpp formula (FR-8).

    Null when no points are spent.  Used both per booking and, with pooled
    numerator/denominator, for the plan-level figure.
    """
    if points_spent <= 0:
        return None
    return (value_cents - fees_cents) * 1000 // points_spent
