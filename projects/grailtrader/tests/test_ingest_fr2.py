"""FR-2: listings ingestion, condition mapping and idempotency."""

from __future__ import annotations

import pytest
from grailtrader.engine.conditions import UnmappedConditionLabelError
from grailtrader.engine.ingest import normalize_listings
from grailtrader.ids import listing_id
from grailtrader.models import ConditionGrade, Listing, ListingSource, ListingStatus, RawListing
from grailtrader_testkit import BRAND, ERA, sold_listing
from pydantic import ValidationError


def raw(**overrides) -> RawListing:
    payload = {
        "source": ListingSource.CSV,
        "external_id": "grailed-1",
        "brand_ref": "Helmut Lang",
        "era_ref": "Helmut Lang era",
        "category": "outerwear",
        "platform_condition": "Gently Used",
        "status": "sold",
        "listed_at": "2024-06-03T00:00:00Z",
        "sold_at": "2024-06-10T00:00:00Z",
        "sold_price": 950.0,
        "ask_price": 1200.0,
        "currency": "USD",
    }
    payload.update(overrides)
    return RawListing(**payload)


def test_fr2_maps_platform_labels_to_the_canonical_scale(ctx):
    assert ctx.mapper.grade_for("Never worn with tag") is ConditionGrade.NEW
    assert ctx.mapper.grade_for("gently used") is ConditionGrade.EXCELLENT
    assert ctx.mapper.grade_for("Very Worn") is ConditionGrade.FAIR
    assert ctx.mapper.grade_for("excellent") is ConditionGrade.EXCELLENT


def test_fr2_unmapped_condition_label_is_rejected_naming_the_label(ctx):
    with pytest.raises(UnmappedConditionLabelError, match="Mint-ish"):
        ctx.mapper.grade_for("Mint-ish")


def test_fr2_condition_adjustment_restates_to_excellent_equivalent(ctx):
    assert ctx.mapper.adjust(1250.0, ConditionGrade.NEW) == pytest.approx(1000.0)
    assert ctx.mapper.adjust(800.0, ConditionGrade.GOOD) == pytest.approx(1000.0)
    assert ctx.mapper.restate(1000.0, ConditionGrade.FAIR) == pytest.approx(550.0)


def test_fr2_resolves_brand_and_era_against_the_gazetteer(ctx):
    listings, report = normalize_listings([raw()], gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert report.ingested == 1
    assert listings[0].brand_id == BRAND
    assert listings[0].era_id == ERA
    assert listings[0].condition is ConditionGrade.EXCELLENT
    assert listings[0].platform_label == "Gently Used"


def test_fr2_unresolvable_rows_are_skipped_and_counted_never_guessed(ctx):
    rows = [raw(), raw(external_id="x2", brand_ref="Definitely Not A Brand")]
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert len(listings) == 1
    assert report.skipped_unresolved == 1
    assert report.unresolved_refs == ("csv:x2",)


def test_fr2_non_usd_rows_are_rejected_with_a_count(ctx):
    rows = [raw(), raw(external_id="x2", currency="EUR")]
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert len(listings) == 1
    assert report.skipped_currency == 1


def test_fr2_ingestion_is_idempotent_because_ids_are_content_derived(ctx):
    rows = [raw(), raw()]
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert len(listings) == 1
    assert report.duplicates == 1
    again, report2 = normalize_listings(
        [raw()], gazetteer=ctx.gazetteer, mapper=ctx.mapper, known_ids={listings[0].id}
    )
    assert again == []
    assert report2.duplicates == 1
    assert listings[0].id == listing_id("csv", "grailed-1")


def test_fr2_sold_listing_requires_price_and_ordered_timestamps(ctx):
    rows = [
        raw(external_id="a", sold_price=None),
        raw(external_id="b", sold_at="2024-05-01T00:00:00Z"),
    ]
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert listings == []
    assert report.skipped_invalid == 2


def test_fr2_active_listing_requires_ask_price_and_carries_no_sold_fields(ctx):
    rows = [
        raw(external_id="a", status="active", sold_at=None, sold_price=None),
        raw(external_id="b", status="active", sold_at=None, sold_price=None, ask_price=None),
    ]
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert [listing.status for listing in listings] == [ListingStatus.ACTIVE]
    assert report.skipped_invalid == 1


def test_fr2_prices_must_be_positive():
    with pytest.raises(ValidationError, match="sold_price must be > 0"):
        sold_listing(external_id="neg", price=-5.0, week_key="2024-06-03")


def test_fr2_era_must_belong_to_its_brand():
    with pytest.raises(ValidationError, match="not a helmut-lang era"):
        Listing(
            id=listing_id("fixture", "x"),
            source=ListingSource.FIXTURE,
            external_id="x",
            brand_id=BRAND,
            era_id="celine:philo",
            category="outerwear",
            condition="excellent",
            platform_label="Excellent",
            status="sold",
            listed_at="2024-06-03T00:00:00Z",
            sold_at="2024-06-03T00:00:00Z",
            sold_price=100.0,
        )


def test_fr2_listing_id_must_be_content_derived():
    with pytest.raises(ValidationError, match="not content-derived"):
        Listing(
            id="deadbeefdeadbeef",
            source=ListingSource.FIXTURE,
            external_id="x",
            brand_id=BRAND,
            era_id=ERA,
            category="outerwear",
            condition="excellent",
            platform_label="Excellent",
            status="sold",
            listed_at="2024-06-03T00:00:00Z",
            sold_at="2024-06-03T00:00:00Z",
            sold_price=100.0,
        )


def test_fr2_stratum_path_is_derived_from_brand_era_suffix_and_category():
    listing = sold_listing(external_id="p", price=100.0, week_key="2024-06-03")
    assert listing.stratum_path == "helmut-lang/helmut/outerwear"
    assert listing.sold_week == "2024-06-03"
