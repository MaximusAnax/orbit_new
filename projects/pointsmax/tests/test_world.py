"""FR-1 world validation / content hashing and FR-3 card gating."""

from __future__ import annotations

import copy
from datetime import date

import pytest
from conftest import make_world, sealed, world_files
from pointsmax.adapters.world_provider import build_world
from pointsmax.engine.world import (
    HASHED_WORLD_FILES,
    WorldValidationError,
    active_subgraph,
    compute_content_hash,
    months_touched,
    validate_world,
)
from pointsmax.models import canonical_json

TODAY = date(2026, 7, 31)


def codes(files) -> list[str]:
    try:
        build_world(sealed(files))
    except WorldValidationError as exc:
        return sorted({issue.code for issue in exc.issues})
    return []


def test_fr1_valid_fixture_world_passes_every_invariant(world):
    assert validate_world(world) == []


# -- invariant 1: unique ids and referential integrity ---------------------


def test_fr1_invariant1_duplicate_program_id_is_named():
    files = world_files()
    files["programs.json"].append(dict(files["programs.json"][0]))
    assert "duplicate_id" in codes(files)


def test_fr1_invariant1_edge_referencing_unknown_program_is_named():
    files = world_files()
    files["transfers.json"][0]["to_program"] = "nowhere"
    assert "unknown_program" in codes(files)


def test_fr1_invariant1_card_must_hold_a_bank_currency():
    files = world_files()
    files["cards.json"][0]["program_id"] = "air_x"
    assert "card_program_not_bank" in codes(files)


def test_fr1_invariant1_duplicate_edge_key_is_named():
    files = world_files()
    clone = dict(files["transfers.json"][0])
    clone["id"] = "bank_a__air_x_clone"
    files["transfers.json"].append(clone)
    assert "duplicate_edge" in codes(files)


# -- invariant 2: divisibility --------------------------------------------


def test_fr1_invariant2_increment_must_divide_by_ratio_from():
    files = world_files()
    edge = next(e for e in files["transfers.json"] if e["id"] == "hotel_h__air_x")
    edge["increment_from"] = 1000  # 1000 % 3 != 0
    edge["min_from"] = 1000
    edge["bonus_per_from"] = 60000
    assert "increment_not_divisible" in codes(files)


def test_fr1_invariant2_min_must_be_a_multiple_of_increment():
    files = world_files()
    files["transfers.json"][0]["min_from"] = 1500
    assert "min_not_multiple_of_increment" in codes(files)


def test_fr1_invariant2_tier_boundary_must_be_a_valid_sent_amount():
    files = world_files()
    edge = next(e for e in files["transfers.json"] if e["id"] == "hotel_h__air_x")
    edge["bonus_per_from"] = 61000  # not a multiple of 3,000
    assert "bonus_tier_not_multiple_of_increment" in codes(files)


# -- invariant 3: one valuation per program -------------------------------


def test_fr1_invariant3_program_without_a_valuation_is_named():
    files = world_files()
    files["valuations.json"] = [v for v in files["valuations.json"] if v["program_id"] != "air_y"]
    assert "valuation_cardinality" in codes(files)


def test_fr1_invariant3_duplicate_valuation_is_named():
    files = world_files()
    files["valuations.json"].append(dict(files["valuations.json"][0]))
    assert "valuation_cardinality" in codes(files)


# -- invariant 4: no value-increasing edge --------------------------------


def test_fr1_invariant4_rejects_a_value_manufacturing_edge():
    files = world_files()
    edge = next(e for e in files["transfers.json"] if e["id"] == "bank_a__air_x")
    edge["ratio_from"], edge["ratio_to"] = 1, 2  # 2 x 1300 > 1 x 2000
    assert "value_increasing_edge" in codes(files)


def test_fr1_invariant4_uses_the_tier_boundary_form_when_a_bonus_is_set():
    files = world_files()
    edge = next(e for e in files["transfers.json"] if e["id"] == "hotel_h__air_x")
    edge["bonus_to"] = 60000  # absurd bonus makes delivered value exceed source value
    assert "value_increasing_edge" in codes(files)


def test_fr1_invariant4_holds_for_the_shipped_marriott_hub(shipped_world):
    edge = shipped_world.edge("marriott_bonvoy__alaska_mp")
    lhs = (edge.ratio_to * edge.bonus_per_from + edge.ratio_from * edge.bonus_to) * (
        shipped_world.mcpp(edge.to_program)
    )
    rhs = edge.ratio_from * edge.bonus_per_from * shipped_world.mcpp(edge.from_program)
    assert lhs <= rhs


# -- invariant 5: reference-fare coverage ---------------------------------


def test_fr1_invariant5_requires_both_directions_and_the_round_trip_row():
    files = world_files()
    files["reference_fares.json"] = [
        f for f in files["reference_fares.json"] if f["id"] != "f_ba_bus_ow"
    ]
    assert "missing_reference_fare" in codes(files)


def test_fr1_invariant5_covers_every_month_the_window_touches():
    files = world_files()
    offer = next(o for o in files["awards.json"] if o["id"] == "x_out")
    offer["travel_window_end"] = "2026-11-30"
    assert "missing_reference_fare" in codes(files)


def test_fr1_invariant5_requires_a_stay_fare_for_every_stay_month():
    files = world_files()
    files["reference_fares.json"] = [
        f for f in files["reference_fares.json"] if f["id"] != "f_stay_aaa"
    ]
    assert "missing_reference_fare" in codes(files)


def test_months_touched_spans_year_boundaries():
    assert months_touched(date(2026, 11, 15), date(2027, 2, 3)) == [
        "2026-11",
        "2026-12",
        "2027-01",
        "2027-02",
    ]


# -- invariant 6: gazetteer resolution ------------------------------------


def test_fr1_invariant6_offer_place_must_resolve():
    files = world_files()
    files["awards.json"][0]["origin"] = "ZZZ"
    assert "unresolved_place" in codes(files)


def test_fr1_invariant6_airport_codes_are_unique_across_cities():
    files = world_files()
    files["gazetteer.json"][1]["airports"] = ["AAX"]
    assert "duplicate_airport" in codes(files)


# -- invariant 7: content hash --------------------------------------------


def test_fr1_invariant7_content_hash_must_match():
    payload = sealed(world_files())
    payload["version.json"]["content_hash"] = "0" * 64
    with pytest.raises(WorldValidationError) as exc:
        build_world(payload)
    assert any(issue.code == "content_hash_mismatch" for issue in exc.value.issues)


def test_fr1_content_hash_ignores_formatting_but_not_data():
    files = world_files()
    baseline = compute_content_hash(files)
    reordered_keys = copy.deepcopy(files)
    reordered_keys["programs.json"][0] = dict(
        reversed(list(reordered_keys["programs.json"][0].items()))
    )
    assert compute_content_hash(reordered_keys) == baseline

    edited = copy.deepcopy(files)
    edited["valuations.json"][0]["cpp_milli"] = 2001
    assert compute_content_hash(edited) != baseline


def test_fr1_content_hash_excludes_version_json():
    files = world_files()
    with pytest.raises(ValueError):
        compute_content_hash({**files, "version.json": {"version": "1.0.0"}})


def test_fr1_content_hash_definition_matches_the_documented_recipe():
    import hashlib

    files = world_files()
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\n")
        digest.update(canonical_json(files[name]).encode("utf-8"))
        digest.update(b"\n")
    assert compute_content_hash(files) == digest.hexdigest()
    assert set(HASHED_WORLD_FILES) == set(files)


# -- FR-3 gating -----------------------------------------------------------


def test_fr3_bank_edges_need_a_card_that_enables_transfer(world):
    active = active_subgraph(world, [], TODAY)
    assert [e.id for e in active.edges] == ["hotel_h__air_x"]

    basic_only = active_subgraph(world, ["card_a_basic"], TODAY)
    assert [e.id for e in basic_only.edges] == ["hotel_h__air_x"]

    premium = active_subgraph(world, ["card_a"], TODAY)
    assert "bank_a__air_x" in {e.id for e in premium.edges}
    assert "bank_b__air_x" not in {e.id for e in premium.edges}


def test_fr3_non_bank_edges_need_no_card(world):
    active = active_subgraph(world, [], TODAY)
    assert active.has_edge("hotel_h__air_x")


def test_fr3_promo_windows_gate_on_today(world):
    inside = active_subgraph(world, ["card_b"], date(2026, 8, 1))
    outside = active_subgraph(world, ["card_b"], date(2026, 8, 11))
    assert inside.has_edge("bank_b__air_y_promo")
    assert not outside.has_edge("bank_b__air_y_promo")


def test_fr3_cashout_options_require_their_card(world):
    without = active_subgraph(world, ["card_a_basic"], TODAY)
    with_card = active_subgraph(world, ["card_a"], TODAY)
    assert not without.has_option("a_portal")
    assert with_card.has_option("a_portal")
    assert without.has_option("a_credit")


def test_fr3_liquidity_split_between_cash_and_portal_options(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    assert {o.id for o in active.cash_options_for("bank_a")} == {"a_credit"}
    assert {o.id for o in active.options_for("bank_a")} == {"a_credit", "a_portal", "a_gift"}
    assert {o.id for o in active.portal_options()} == {"a_portal"}


def test_fr3_is_a_pure_function_of_world_wallet_and_today(world):
    first = active_subgraph(world, ["card_a", "card_b"], TODAY)
    second = active_subgraph(world, ["card_b", "card_a", "card_a"], TODAY)
    assert [e.id for e in first.edges] == [e.id for e in second.edges]
    assert [o.id for o in first.options] == [o.id for o in second.options]


def test_fr3_unknown_cards_are_ignored(world):
    active = active_subgraph(world, ["card_a", "not_a_real_card"], TODAY)
    assert active.cards == frozenset({"card_a"})


def test_fr1_shipped_dataset_uses_increments_of_at_least_1000(shipped_world):
    """DATA_MODEL dataset design rule (SCOPE decision 18)."""
    assert all(edge.increment_from >= 1000 for edge in shipped_world.edges)


def test_fr1_shipped_dataset_is_realistically_sized(shipped_world):
    assert len(shipped_world.programs) >= 15
    assert len(shipped_world.edges) >= 40
    assert len(shipped_world.cards) >= 10
    assert len(shipped_world.offers) >= 30
    assert all(v.source_note for v in shipped_world.valuations)


def test_make_world_helper_round_trips(world):
    assert make_world().version.content_hash == world.version.content_hash
