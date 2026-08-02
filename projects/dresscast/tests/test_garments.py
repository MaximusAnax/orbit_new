"""FR-1 garment creation/validation, FR-3 transitions, FR-19's wardrobe hash."""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import NOW, garment, small_wardrobe
from dresscast.engine.models import (
    CATEGORY_PRESETS,
    CLO_PRESET_TOLERANCE,
    PLAN_DP,
    Color,
    Garment,
    apply_wear,
    cascade_category,
    check_transition,
    clo_bounds,
    undo_wear,
    wardrobe_hash,
    warmth_to_clo,
    wash,
)
from dresscast.errors import InvalidParams, InvalidTransition


def test_fr1_category_presets_cover_the_d1_table():
    assert CATEGORY_PRESETS["tshirt"].clo == 0.08
    assert CATEGORY_PRESETS["shirt_long_sleeve"].clo == 0.25
    assert CATEGORY_PRESETS["sweater_thick"].clo == 0.36
    assert CATEGORY_PRESETS["fleece"].clo == 0.30
    assert CATEGORY_PRESETS["wool_coat"].clo == 0.60
    assert CATEGORY_PRESETS["parka"].clo == 0.70
    assert CATEGORY_PRESETS["jeans"].clo == 0.24
    assert CATEGORY_PRESETS["boots"].clo == 0.10
    assert CATEGORY_PRESETS["sneakers"].clo == 0.02


def test_fr1_no_socks_category_exists():
    """D1 revision 2: footwear presets include hosiery; there is no socks row."""
    assert "socks" not in CATEGORY_PRESETS


def test_fr1_accessory_presets_are_zero_clo():
    for name in ("hat", "gloves", "scarf", "umbrella", "sunglasses"):
        assert CATEGORY_PRESETS[name].clo == 0.0
        assert clo_bounds(name) == (0.0, 0.0)


def test_fr1_clo_must_sit_within_the_category_tolerance():
    lo, hi = clo_bounds("wool_coat")
    assert (lo, hi) == pytest.approx((0.45, 0.75))
    garment("g", "n", "wool_coat", clo=0.45)
    garment("g", "n", "wool_coat", clo=0.75)
    with pytest.raises(ValueError, match="outside"):
        garment("g", "n", "wool_coat", clo=0.76)
    with pytest.raises(ValueError, match="outside"):
        garment("g", "n", "wool_coat", clo=0.44)


def test_fr1_clo_bounds_clamp_at_the_absolute_floor():
    assert clo_bounds("tshirt")[0] == 0.0
    assert clo_bounds("tshirt")[1] == pytest.approx(0.08 + CLO_PRESET_TOLERANCE)


def test_fr1_warmth_level_maps_deterministically_into_the_allowed_range():
    for level in range(6):
        value = warmth_to_clo("wool_coat", level)
        lo, hi = clo_bounds("wool_coat")
        assert lo <= value <= hi
    assert warmth_to_clo("wool_coat", 0) == pytest.approx(0.45)
    assert warmth_to_clo("wool_coat", 5) == pytest.approx(0.75)
    assert warmth_to_clo("wool_coat", 2) < warmth_to_clo("wool_coat", 3)
    with pytest.raises(InvalidParams):
        warmth_to_clo("wool_coat", 6)
    with pytest.raises(InvalidParams):
        warmth_to_clo("no_such_category", 2)


def _raw(**overrides):
    data = {
        "id": "g",
        "name": "n",
        "category": "tshirt",
        "layer_role": "base",
        "clo": 0.08,
        "formality": 2,
        "colors": [Color(name="navy", neutral=True)],
        "occasions": ["casual"],
        "wears_before_laundry": 2,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return Garment(**data)


def test_fr1_unknown_category_is_rejected():
    with pytest.raises(ValueError, match="unknown category"):
        _raw(category="spacesuit")


def test_fr1_occasions_are_required_for_non_accessories():
    with pytest.raises(ValueError, match="occasions"):
        garment("g", "n", "tshirt", occasions=())
    garment("a", "hat", "hat", accessory_class="hat", clo=0.0, occasions=())


def test_fr1_accessory_class_is_set_iff_the_role_is_accessory():
    with pytest.raises(ValueError, match="accessory_class"):
        _raw(accessory_class="hat")
    with pytest.raises(ValueError, match="accessory_class"):
        _raw(category="hat", layer_role="accessory", clo=0.0, accessory_class=None)
    _raw(category="hat", layer_role="accessory", clo=0.0, accessory_class="hat")


def test_fr1_colors_need_exactly_one_main():
    with pytest.raises(ValueError, match="main"):
        Garment(
            id="g",
            name="n",
            category="tshirt",
            layer_role="base",
            clo=0.08,
            formality=2,
            colors=[
                Color(name="red", hue=0.0),
                Color(name="blue", hue=220.0),
            ],
            occasions=["casual"],
            wears_before_laundry=2,
            created_at=NOW,
            updated_at=NOW,
        )


def test_fr1_color_hue_is_null_iff_neutral():
    Color(name="navy", neutral=True)
    Color(name="red", hue=10.0)
    with pytest.raises(ValueError, match="neutral"):
        Color(name="navy", neutral=True, hue=220.0)
    with pytest.raises(ValueError, match="non-neutral"):
        Color(name="red", neutral=False)


def test_fr1_hue_360_normalises_to_zero():
    assert Color(name="red", hue=360.0).hue == 0.0


def test_fr1_photo_path_and_sha_travel_together():
    with pytest.raises(ValueError, match="photo"):
        Garment(
            id="g",
            name="n",
            category="tshirt",
            layer_role="base",
            clo=0.08,
            formality=2,
            colors=[Color(name="navy", neutral=True)],
            occasions=["casual"],
            wears_before_laundry=2,
            photo_path="/tmp/x.jpg",
            created_at=NOW,
            updated_at=NOW,
        )


def test_fr1_overridden_fields_are_restricted_and_sorted():
    g = garment("g", "n", "tshirt", overridden_fields=("formality", "clo", "clo"))
    assert g.overridden_fields == ["clo", "formality"]
    with pytest.raises(ValueError, match="overridable"):
        garment("g", "n", "tshirt", overridden_fields=("colors",))


def test_fr1_tags_are_normalised_and_deduplicated():
    g = garment("g", "n", "tshirt", style_tags=(" Preppy ", "preppy"), occasions=("Work",))
    assert g.style_tags == ["preppy"]
    assert g.occasions == ["work"]


# --------------------------------------------------------------------------
# FR-2's cascade helper
# --------------------------------------------------------------------------


def test_fr2_cascade_rederives_the_four_preset_fields():
    g = garment("g", "n", "fleece")
    assert cascade_category(g, "wool_coat") == {
        "clo": 0.60,
        "layer_role": "outer",
        "formality": 4,
        "wears_before_laundry": 30,
    }


def test_fr2_cascade_skips_user_overridden_fields():
    g = garment("g", "n", "fleece", clo=0.30, overridden_fields=("clo",))
    derived = cascade_category(g, "wool_coat")
    assert "clo" not in derived
    assert derived["layer_role"] == "outer"


def test_fr2_cascade_rejects_an_unknown_category():
    with pytest.raises(InvalidParams):
        cascade_category(garment("g", "n", "fleece"), "spacesuit")


# --------------------------------------------------------------------------
# FR-3 — the laundry state machine
# --------------------------------------------------------------------------


def test_fr3_allowed_transitions():
    for old, new in (
        ("clean", "dirty"),
        ("dirty", "in_laundry"),
        ("dirty", "clean"),
        ("in_laundry", "clean"),
        ("clean", "retired"),
        ("dirty", "retired"),
    ):
        check_transition(old, new)
    check_transition("clean", "clean")


def test_fr3_forbidden_transitions_raise():
    for old, new in (
        ("clean", "in_laundry"),
        ("retired", "clean"),
        ("in_laundry", "dirty"),
    ):
        with pytest.raises(InvalidTransition):
            check_transition(old, new)


def test_fr3_wearing_advances_the_counter_and_flips_at_threshold():
    shirt = garment("g", "shirt", "shirt_long_sleeve")
    assert shirt.wears_before_laundry == 2
    once = apply_wear(shirt, NOW)
    assert (once.wears_since_wash, once.status) == (1, "clean")
    twice = apply_wear(once, NOW)
    assert (twice.wears_since_wash, twice.status) == (2, "dirty")


def test_fr3_undo_reverses_the_counter_and_the_flip():
    shirt = apply_wear(apply_wear(garment("g", "s", "shirt_long_sleeve"), NOW), NOW)
    assert shirt.status == "dirty"
    back = undo_wear(shirt, NOW)
    assert (back.wears_since_wash, back.status) == (1, "clean")


def test_fr3_laundry_resets_the_counter_and_the_status():
    dirty = apply_wear(apply_wear(garment("g", "s", "shirt_long_sleeve"), NOW), NOW)
    cleaned = wash(dirty, NOW)
    assert (cleaned.wears_since_wash, cleaned.status) == (0, "clean")


def test_fr3_retired_garments_cannot_be_washed():
    retired = garment("g", "s", "tshirt", status="retired")
    with pytest.raises(InvalidTransition):
        wash(retired, NOW)


def test_fr3_uncounted_categories_never_go_dirty_by_wear():
    boots = garment("g", "boots", "boots")
    assert boots.wears_before_laundry == 999
    worn = boots
    for _ in range(10):
        worn = apply_wear(worn, NOW)
    assert worn.status == "clean"


# --------------------------------------------------------------------------
# FR-19 — the wardrobe hash
# --------------------------------------------------------------------------


def test_fr19_wardrobe_hash_is_stable_and_order_independent():
    w = small_wardrobe()
    assert wardrobe_hash(w) == wardrobe_hash(list(reversed(w)))


def test_fr19_wardrobe_hash_ignores_timestamps_photos_and_notes():
    w = small_wardrobe()
    tweaked = [
        g.model_copy(
            update={
                "updated_at": datetime(2030, 1, 1),
                "notes": "bought in Rome",
            }
        )
        for g in w
    ]
    assert wardrobe_hash(w) == wardrobe_hash(tweaked)


def test_fr19_wardrobe_hash_tracks_recommendation_relevant_state():
    w = small_wardrobe()
    dirty = [g.model_copy(update={"status": "dirty"}) if g.id == "b1-tee" else g for g in w]
    assert wardrobe_hash(w) != wardrobe_hash(dirty)


def test_fr19_wardrobe_hash_excludes_retired_garments():
    w = small_wardrobe()
    without = [g for g in w if g.id != "b1-tee"]
    retired = [g.model_copy(update={"status": "retired"}) if g.id == "b1-tee" else g for g in w]
    assert wardrobe_hash(retired) == wardrobe_hash(without)


def test_fr19_wardrobe_hash_rounds_floats_to_plan_dp():
    w = small_wardrobe()
    nudged = [g.model_copy(update={"clo": g.clo + 1e-9}) if g.id == "b1-tee" else g for g in w]
    assert PLAN_DP == 3
    assert wardrobe_hash(w) == wardrobe_hash(nudged)
