"""FR-8 integer money and points primitives (SCOPE decision 11)."""

from __future__ import annotations

import pytest
from pointsmax.engine.money import (
    ceil_div,
    ceil_to_multiple,
    delivered_points,
    floor_to_multiple,
    max_valid_sent,
    portal_points_price,
    portfolio_value,
    realized_cpp_milli,
    redeemable_points,
    smallest_sent_covering,
    transfer_fee_cents,
    value_of_points,
)
from pointsmax.models import CashoutOption, TransferEdge


def edge(**kwargs) -> TransferEdge:
    base = {
        "id": "e",
        "from_program": "bank_a",
        "to_program": "air_x",
        "ratio_from": 1,
        "ratio_to": 1,
        "min_from": 1000,
        "increment_from": 1000,
    }
    base.update(kwargs)
    return TransferEdge(**base)


def option(**kwargs) -> CashoutOption:
    base = {
        "id": "o",
        "program_id": "bank_a",
        "method": "statement_credit",
        "cpp_milli": 1000,
        "min_points": 1,
        "increment": 1,
    }
    base.update(kwargs)
    return CashoutOption(**base)


def test_fr8_baseline_value_floors():
    assert value_of_points(210000, 2050) == 430500
    assert value_of_points(1, 1500) == 1  # 1.5 cents floors to 1
    assert value_of_points(0, 2050) == 0


def test_fr8_portfolio_value_sums_per_program_floors():
    holdings = {"bank_a": 130000, "air_x": 10000}
    mcpp = {"bank_a": 2000, "air_x": 1300}
    assert portfolio_value(holdings, mcpp) == 260000 + 13000


def test_fr8_delivered_points_exact_for_ratio_and_tier_bonus():
    plain = edge()
    assert delivered_points(plain, 60000) == 60000
    marriott = edge(
        ratio_from=3,
        ratio_to=1,
        min_from=3000,
        increment_from=3000,
        bonus_per_from=60000,
        bonus_to=5000,
    )
    assert delivered_points(marriott, 57000) == 19000
    assert delivered_points(marriott, 60000) == 25000  # 20,000 + 5,000 tier bonus
    assert delivered_points(marriott, 120000) == 50000  # 40,000 + two tiers
    # 57,000 -> 19,000 but 60,000 -> 25,000: sending 3,000 more delivers 6,000 more.
    assert delivered_points(marriott, 60000) - delivered_points(marriott, 57000) == 6000


def test_fr8_transfer_fee_ceils_then_caps():
    amex = edge(fee_mcpp=60, fee_cap_cents=9900)
    # 165,000 x 60 mcpp = 9,900,000 milli-cents = $99.00 exactly at the cap.
    assert transfer_fee_cents(amex, 165000) == 9900
    assert transfer_fee_cents(amex, 164000) == 9840
    assert transfer_fee_cents(amex, 300000) == 9900  # capped
    assert transfer_fee_cents(amex, 1) == 1  # ceil(60/1000) = 1
    assert transfer_fee_cents(edge(), 500000) == 0


def test_fr8_fee_cap_applies_once_per_merged_transfer():
    """FR-7b: merging two needs into one transfer pays the cap once."""
    amex = edge(fee_mcpp=60, fee_cap_cents=9900)
    merged = transfer_fee_cents(amex, 400000)
    split = transfer_fee_cents(amex, 200000) + transfer_fee_cents(amex, 200000)
    assert merged == 9900
    assert split == 19800
    assert merged < split


def test_fr8_portal_points_price_ceils_to_increment_and_minimum():
    portal = option(method="portal_travel", cpp_milli=1500)
    assert portal_points_price(210000, portal) == 140000
    # 1 cent at 1.5 cpp needs ceil(1000/1500) = 1 point.
    assert portal_points_price(1, portal) == 1
    chunky = option(method="portal_travel", cpp_milli=1500, increment=500, min_points=1000)
    assert portal_points_price(210000, chunky) == 140000
    assert portal_points_price(150, chunky) == 1000  # min_points floor


def test_fr13_redeemable_points_respects_min_and_increment():
    gift = option(method="gift_card", min_points=2500, increment=2500)
    assert redeemable_points(6000, gift) == 5000
    assert redeemable_points(2400, gift) == 0  # below minimum -> unredeemable
    assert redeemable_points(0, gift) == 0
    assert redeemable_points(12345, option()) == 12345


def test_fr8_realized_cpp_is_value_minus_fees_over_points():
    assert realized_cpp_milli(420000, 50300, 120000) == 3080
    assert realized_cpp_milli(1000, 0, 0) is None


def test_fr7_smallest_sent_covering_uses_tier_boundaries():
    marriott = edge(
        ratio_from=3,
        ratio_to=1,
        min_from=3000,
        increment_from=3000,
        bonus_per_from=60000,
        bonus_to=5000,
    )
    assert smallest_sent_covering(marriott, 25000, 150000) == 60000
    assert smallest_sent_covering(marriott, 19000, 150000) == 57000
    assert smallest_sent_covering(marriott, 30000, 150000) == 75000
    assert smallest_sent_covering(marriott, 999999, 150000) is None


def test_fr7_max_valid_sent_respects_minimum():
    assert max_valid_sent(edge(), 4500) == 4000
    assert max_valid_sent(edge(), 999) is None


def test_rounding_helpers():
    assert ceil_div(7, 3) == 3
    assert ceil_to_multiple(4001, 1000) == 5000
    assert floor_to_multiple(4999, 1000) == 4000
    assert floor_to_multiple(-5, 1000) == 0
    with pytest.raises(ValueError):
        ceil_div(1, 0)
