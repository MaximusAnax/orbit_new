"""FR-11: portfolio garments — status/price pairings, soft delete, gazetteer validation."""

from __future__ import annotations

import pytest
from grailtrader.engine.strata import UnknownReferenceError, ancestors, is_prefix
from grailtrader.ids import garment_id
from grailtrader.models import Category, ConditionGrade, GarmentStatus
from grailtrader_testkit import BRAND, ERA, START, garment
from pydantic import ValidationError


def test_fr11_owned_requires_the_acquisition_pair():
    piece = garment(status=GarmentStatus.OWNED, price=840.0, anchor_date=START)
    assert piece.acquisition_price == 840.0
    assert piece.reference_price is None
    with pytest.raises(ValidationError, match="require acquisition_price"):
        piece.__class__(**{**piece.model_dump(), "acquisition_price": None, "acquired_on": None})


def test_fr11_watching_requires_the_reference_pair_and_no_acquisition():
    piece = garment(status=GarmentStatus.WATCHING, price=1200.0)
    assert piece.reference_price == 1200.0
    assert piece.acquisition_price is None
    with pytest.raises(ValidationError, match="watching garments require"):
        piece.__class__(**{**piece.model_dump(), "acquisition_price": 500.0, "acquired_on": START})


def test_fr11_sold_archived_requires_the_disposal_pair():
    piece = garment(status=GarmentStatus.SOLD_ARCHIVED)
    assert piece.disposed_price is not None
    with pytest.raises(ValidationError, match="disposed_price"):
        piece.__class__(**{**piece.model_dump(), "disposed_price": None, "disposed_on": None})


def test_fr11_owned_garments_carry_no_disposal_fields():
    piece = garment(status=GarmentStatus.OWNED)
    with pytest.raises(ValidationError, match="no disposal fields"):
        piece.__class__(**{**piece.model_dump(), "disposed_price": 100.0, "disposed_on": START})


def test_fr11_id_covers_only_immutable_fields():
    piece = garment(label="HL moto", condition=ConditionGrade.EXCELLENT)
    edited = piece.model_copy(
        update={"label": "renamed", "condition": ConditionGrade.FAIR, "notes": "worn hard"}
    )
    assert edited.id == piece.id
    assert edited.id == garment_id(
        piece.stratum_path, piece.anchor_date, piece.anchor_price, piece.added_at
    )


def test_fr11_id_must_be_content_derived():
    piece = garment()
    with pytest.raises(ValidationError, match="not content-derived"):
        piece.__class__(**{**piece.model_dump(), "id": "0" * 16})


def test_fr11_anchor_pair_needs_no_status_special_casing():
    owned = garment(status=GarmentStatus.OWNED, price=800.0, anchor_date=START)
    watching = garment(status=GarmentStatus.WATCHING, price=800.0, anchor_date=START)
    assert owned.anchor_price == watching.anchor_price == 800.0
    assert owned.anchor_date == watching.anchor_date == START
    assert owned.id == watching.id  # same stratum, anchor pair and added_at


def test_fr11_soft_delete_is_a_field_not_a_deletion():
    piece = garment()
    removed = piece.model_copy(update={"deleted_at": "2026-02-01T00:00:00Z"})
    assert removed.id == piece.id
    assert removed.deleted_at is not None


def test_fr11_era_must_belong_to_the_brand():
    with pytest.raises(ValidationError, match="not a helmut-lang era"):
        garment(brand_id=BRAND, era_id="celine:philo")


def test_fr11_gazetteer_rejects_unknown_values_with_a_suggestion(ctx):
    with pytest.raises(UnknownReferenceError) as excinfo:
        ctx.gazetteer.resolve_brand("Helmut Lng")
    assert excinfo.value.suggestion is not None

    with pytest.raises(UnknownReferenceError) as excinfo:
        ctx.gazetteer.resolve_era(BRAND, "Helmut Lang eraa")
    assert excinfo.value.suggestion is not None

    assert ctx.gazetteer.suggest_category("outewear") == "outerwear"


def test_fr11_gazetteer_resolves_aliases_case_insensitively(ctx):
    assert ctx.gazetteer.resolve_brand("HL") == BRAND
    assert ctx.gazetteer.resolve_brand("helmut lang") == BRAND
    assert ctx.gazetteer.resolve_era(BRAND, "helmut") == ERA
    assert ctx.gazetteer.resolve_era(BRAND, "Helmut Lang") == ERA


def test_fr11_stratum_paths_and_prefixes():
    piece = garment(category=Category.DENIM)
    assert piece.stratum_path == "helmut-lang/helmut/denim"
    assert ancestors(piece.stratum_path) == [
        "helmut-lang/helmut/denim",
        "helmut-lang/helmut",
        "helmut-lang",
    ]
    assert is_prefix("helmut-lang", piece.stratum_path)
    assert not is_prefix("helmut-lang/post", piece.stratum_path)


def test_fr11_stratum_validation_checks_every_segment(ctx):
    ctx.gazetteer.validate_stratum("helmut-lang/helmut/outerwear")
    with pytest.raises(UnknownReferenceError, match="category"):
        ctx.gazetteer.validate_stratum("helmut-lang/helmut/hats")
    with pytest.raises(UnknownReferenceError, match="era"):
        ctx.gazetteer.validate_stratum("helmut-lang/philo")
    with pytest.raises(UnknownReferenceError, match="brand"):
        ctx.gazetteer.validate_stratum("nonexistent-brand")
