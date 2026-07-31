"""FR-10 / D9: formality coherence, tag cohesion, occasion filtering."""

from __future__ import annotations

import pytest
from conftest import garment
from dresscast.engine.style import (
    formality_coherent,
    formality_spread,
    formality_tightness,
    occasion_ok,
    style_score,
    tag_cohesion,
)


def _items(*specs):
    return [
        garment(f"g{i}", f"g{i}", "tshirt", formality=f, style_tags=tags)
        for i, (f, tags) in enumerate(specs)
    ]


def test_fr10_formality_spread_and_hc4():
    tight = _items((3, ()), (3, ()), (3, ()))
    ok = _items((3, ()), (4, ()), (3, ()))
    bad = _items((1, ()), (4, ()), (3, ()))
    assert formality_spread(tight) == 0
    assert formality_spread(ok) == 1
    assert formality_spread(bad) == 3
    assert formality_coherent(tight) and formality_coherent(ok)
    assert not formality_coherent(bad)
    assert formality_coherent(_items((2, ()), (4, ())), max_spread=2)


def test_fr10_formality_tightness_endpoints():
    assert formality_tightness(0) == pytest.approx(1.0)
    assert formality_tightness(1) == pytest.approx(0.7)
    assert formality_tightness(2) == pytest.approx(0.4)
    assert formality_tightness(5) >= 0.0


def test_fr10_tag_cohesion_is_the_fraction_of_pairs_sharing_a_tag():
    none = _items((3, ("a",)), (3, ("b",)), (3, ("c",)))
    assert tag_cohesion(none) == pytest.approx(0.0)
    total = _items((3, ("a",)), (3, ("a",)), (3, ("a", "b")))
    assert tag_cohesion(total) == pytest.approx(1.0)
    partial = _items((3, ("a",)), (3, ("a",)), (3, ("b",)))
    assert tag_cohesion(partial) == pytest.approx(1 / 3)


def test_fr10_style_score_is_the_documented_blend():
    """0.6 x tightness + 0.4 x cohesion — DATA_MODEL.md §6's 0.88 case."""
    items = _items((3, ("a",)), (3, ("a",)), (3, ("a",)), (3, ("b",)))
    # spread 0 → 1.0; pairs sharing a tag: 3 of 6 → 0.5
    assert tag_cohesion(items) == pytest.approx(0.5)
    assert style_score(items) == pytest.approx(0.6 * 1.0 + 0.4 * 0.5)
    spread_one = _items((3, ("a",)), (4, ("a",)))
    assert style_score(spread_one) == pytest.approx(0.6 * 0.7 + 0.4 * 1.0)


def test_fr10_style_score_stays_inside_zero_and_one():
    assert 0.0 <= style_score(_items((1, ()), (5, ()))) <= 1.0


def test_fr10_single_item_has_perfect_cohesion():
    assert tag_cohesion(_items((3, ()))) == pytest.approx(1.0)


def test_fr8_hc3_occasion_membership(wardrobe):
    by_id = {g.id: g for g in wardrobe}
    sneakers = by_id["f2-sneakers"]
    sandals = by_id["f1-sandals"]
    assert occasion_ok(sneakers, "work")
    assert occasion_ok(sandals, "casual")
    assert not occasion_ok(sandals, "work")
    assert not occasion_ok(sandals, "formal")
