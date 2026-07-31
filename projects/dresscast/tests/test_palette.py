"""FR-10 / D8: the Itten hue-zone table, neutrals, and the three-family rule."""

from __future__ import annotations

import pytest
from conftest import color, garment
from dresscast.engine.models import HUE_FAMILY_PENALTY
from dresscast.engine.palette import color_score, hue_distance, hue_families, pair_harmony


def _tops(*specs):
    """Build throwaway garments carrying the given colour names."""
    return [garment(f"g{i}", f"g{i}", "tshirt", colors=spec) for i, spec in enumerate(specs)]


def test_fr10_hue_distance_is_circular():
    assert hue_distance(10.0, 350.0) == pytest.approx(20.0)
    assert hue_distance(0.0, 180.0) == pytest.approx(180.0)
    assert hue_distance(200.0, 200.0) == 0.0


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (0.0, 0.90),  # monochromatic
        (15.0, 0.90),
        (16.0, 0.85),  # analogous
        (45.0, 0.85),
        (60.0, 0.35),  # clash
        (105.0, 0.35),
        (120.0, 0.70),  # triadic zone
        (150.0, 0.70),
        (170.0, 0.80),  # complementary
        (180.0, 0.80),
    ],
)
def test_fr10_hue_zone_table(delta, expected):
    a = color("red")
    b = color("red").model_copy(update={"hue": delta})
    assert pair_harmony(a, b) == pytest.approx(expected)


def test_fr10_neutrals_pair_with_anything():
    assert pair_harmony(color("navy"), color("orange")) == 1.0
    assert pair_harmony(color("orange"), color("navy")) == 1.0
    assert pair_harmony(color("navy"), color("gray")) == 1.0


def test_fr10_all_neutral_outfit_scores_one():
    assert color_score(_tops(("navy",), ("gray",), ("white",))) == pytest.approx(1.0)


def test_fr10_clashing_pair_drags_the_mean_down():
    clashing = _tops(("red",), ("green",), ("navy",))
    # red/green = 0.70 (triadic zone), red/navy = green/navy = 1.0
    assert color_score(clashing) == pytest.approx((0.70 + 1.0 + 1.0) / 3.0)
    worse = _tops(("red",), ("yellow",), ("navy",))
    assert color_score(worse) < color_score(clashing)


def test_fr10_hue_families_count_every_listed_colour_not_just_the_main():
    items = _tops(("red", "blue"), ("green", "yellow"), ("navy",))
    assert hue_families(items) == {0, 7, 4, 2}
    assert len(hue_families(items)) == 4


def test_fr10_more_than_three_hue_families_costs_a_fixed_penalty():
    three = _tops(("red", "blue"), ("green",), ("navy",))
    four = _tops(("red", "blue"), ("green", "yellow"), ("navy",))
    assert len(hue_families(three)) == 3
    assert len(hue_families(four)) == 4
    base_three = (
        sum(
            pair_harmony(a.main_color, b.main_color)
            for i, a in enumerate(three)
            for b in three[i + 1 :]
        )
        / 3.0
    )
    base_four = (
        sum(
            pair_harmony(a.main_color, b.main_color)
            for i, a in enumerate(four)
            for b in four[i + 1 :]
        )
        / 3.0
    )
    assert color_score(three) == pytest.approx(base_three)
    assert color_score(four) == pytest.approx(base_four - HUE_FAMILY_PENALTY)


def test_fr10_color_score_stays_inside_zero_and_one():
    assert 0.0 <= color_score(_tops(("red",), ("yellow",), ("green",), ("blue",))) <= 1.0


def test_fr10_single_item_outfit_has_no_pairs_and_scores_one():
    assert color_score(_tops(("red",))) == pytest.approx(1.0)
    assert color_score([]) == pytest.approx(1.0)
