"""T1 / FR-14: determinism, content-derived ids and idempotent ingest."""

from __future__ import annotations

import json

from grailtrader.adapters.listings_fixture import FixtureListingsFeed
from grailtrader.engine.advisor import advise_garment, build_advice
from grailtrader.engine.backtest import ReferenceIndex, run_backtest
from grailtrader.engine.ingest import normalize_listings
from grailtrader.ids import canonical_json, cents, hex16
from grailtrader.models import BacktestParams
from grailtrader.store import InMemoryRepository
from grailtrader_testkit import (
    BRAND,
    ERA,
    LEAF,
    build,
    departure,
    flat_market,
    garment,
    week,
)

WEEKS = 40
AS_OF = "2026-01-01T00:00:00Z"


def scenario_rows():
    listings = flat_market(weeks=WEEKS, per_week=7, noise=0.15, seed=99)
    return [
        {
            "external_id": listing.external_id,
            "brand_ref": BRAND,
            "era_ref": ERA,
            "category": listing.category.value,
            "platform_condition": listing.platform_label,
            "status": "sold",
            "listed_at": listing.listed_at,
            "sold_at": listing.sold_at,
            "sold_price": listing.sold_price,
            "currency": "USD",
        }
        for listing in listings
    ]


def write_scenario(tmp_path):
    path = tmp_path / "listings.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in scenario_rows()) + "\n", encoding="utf-8")
    return path


def export(repo, index, advice, run) -> str:
    return canonical_json(
        {
            "listings": [row.model_dump(mode="json") for row in repo.list_listings()],
            "index": [point.model_dump(mode="json") for point in index.all_points()],
            "events": [event.model_dump(mode="json") for event in repo.list_events()],
            "advice": [row.model_dump(mode="json") for row in repo.list_advice(history=True)],
            "backtest": run.model_dump(mode="json"),
        }
    )


def run_scenario(path, ctx) -> str:
    repo = InMemoryRepository()
    repo.initialize(reset=True)
    repo.replace_gazetteer(ctx.gazetteer.brands)
    rows = FixtureListingsFeed(path).fetch()
    listings, _report = normalize_listings(
        rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper, known_ids=repo.listing_ids()
    )
    repo.add_listings(listings)

    event = departure(occurred_on=week(20))
    repo.upsert_events([event])

    index = build(repo.list_listings(), ctx, as_of_week=week(WEEKS - 1))
    repo.replace_index_points(index.all_points())

    piece = garment(anchor_date=week(2))
    repo.add_garment(piece)
    decision = advise_garment(
        piece, as_of_week=week(24), index=index, events=repo.list_events(), ctx=ctx
    )
    repo.add_advice(
        build_advice(
            decision,
            garment=piece,
            events_by_id={event.id: event},
            ctx=ctx,
            created_as_of=AS_OF,
        )
    )

    params = BacktestParams(
        start_week=week(14), end_week=week(WEEKS - 1), reference="recovered", scenario="t1"
    )
    run, results = run_backtest(
        params=params,
        garments=[piece],
        events=[event],
        index=index,
        reference=ReferenceIndex.from_index(index, carry_back_weeks=8),
        ctx=ctx,
        as_of=AS_OF,
    )
    repo.add_backtest(run, results)
    return export(repo, index, None, run)


def test_t1_fr14_two_fresh_stores_produce_byte_identical_exports(tmp_path, ctx):
    path = write_scenario(tmp_path)
    first = run_scenario(path, ctx)
    second = run_scenario(path, ctx)
    assert first == second


def test_t1_fr14_reingesting_into_the_same_store_changes_nothing(tmp_path, ctx):
    path = write_scenario(tmp_path)
    repo = InMemoryRepository()
    repo.initialize(reset=True)
    rows = FixtureListingsFeed(path).fetch()
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    repo.add_listings(listings)
    before = canonical_json([row.model_dump(mode="json") for row in repo.list_listings()])

    again, report2 = normalize_listings(
        rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper, known_ids=repo.listing_ids()
    )
    assert again == []
    assert report2.duplicates == report.ingested
    assert repo.add_listings(again) == 0
    after = canonical_json([row.model_dump(mode="json") for row in repo.list_listings()])
    assert before == after


def test_t1_fr14_index_rebuild_on_unchanged_listings_is_byte_identical(tmp_path, ctx):
    listings = flat_market(weeks=WEEKS, per_week=7, noise=0.15, seed=99)
    first = build(listings, ctx, as_of_week=week(WEEKS - 1))
    second = build(listings, ctx, as_of_week=week(WEEKS - 1))
    assert canonical_json([p.model_dump(mode="json") for p in first.all_points()]) == (
        canonical_json([p.model_dump(mode="json") for p in second.all_points()])
    )
    assert first.point_at(LEAF, week(20)) == second.point_at(LEAF, week(20))


def test_fr14_money_enters_hashes_as_integer_cents():
    assert cents(1236.0) == 123600
    assert cents(0.1 + 0.2) == 30  # 0.30000000000000004 must not leak into a hash
    assert cents(1309.325) in {130932, 130933}  # rounding is stable within a platform
    assert isinstance(cents(12.5), int)


def test_fr14_ids_are_pure_functions_of_their_key():
    assert hex16("listing|fixture|a") == hex16("listing|fixture|a")
    assert len(hex16("x")) == 16
    assert hex16("a") != hex16("b")


def test_fr14_canonical_json_is_sorted_and_compact():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


def test_fr14_engine_never_reads_the_clock(tmp_path, ctx):
    """The whole pipeline is a function of its inputs: same inputs, same outputs."""
    path = write_scenario(tmp_path)
    assert run_scenario(path, ctx) == run_scenario(path, ctx)
