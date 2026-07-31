"""FR-3/FR-12/FR-13 persistence — both backends, one contract."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from conftest import NOW, garment, make_forecast, params, small_wardrobe
from dresscast.engine.models import SCHEMA_VERSION, WearHistory
from dresscast.errors import (
    InvalidParams,
    InvalidSchemaVersion,
    InvalidTransition,
    UnknownGarment,
)
from dresscast.store.memory import InMemoryRepository
from dresscast.store.migrations import current_version, migrate
from dresscast.store.sqlite_repo import SqliteRepository

LATER = datetime(2026, 4, 14, 20, 0, 0)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    if request.param == "memory":
        store = InMemoryRepository()
    else:
        store = SqliteRepository(tmp_path / "dresscast.db", now=NOW)
    yield store
    store.close()


def _seed(repo, wardrobe=None):
    for g in wardrobe or small_wardrobe():
        repo.add_garment(g)
    return repo


# --------------------------------------------------------------------------
# Garment CRUD
# --------------------------------------------------------------------------


def test_fr1_round_trip_preserves_every_field(repo):
    original = small_wardrobe()[0]
    repo.add_garment(original)
    assert repo.get_garment(original.id) == original


def test_fr1_names_are_unique_case_insensitively(repo):
    repo.add_garment(garment("g1", "Navy-Coat", "wool_coat"))
    with pytest.raises(InvalidParams):
        repo.add_garment(garment("g2", "navy-coat", "wool_coat"))


def test_fr1_find_by_id_or_name(repo):
    g = garment("g1", "navy-coat", "wool_coat")
    repo.add_garment(g)
    assert repo.find_garment("g1").id == "g1"
    assert repo.find_garment("NAVY-COAT").id == "g1"
    assert repo.find_garment("nope") is None


def test_unknown_garment_raises_the_documented_code(repo):
    with pytest.raises(UnknownGarment) as excinfo:
        repo.get_garment("nope")
    assert excinfo.value.code == "unknown_garment"


def test_fr1_listing_filters_are_composable(repo):
    _seed(repo)
    assert len(repo.list_garments()) == 26
    assert all(g.status == "clean" for g in repo.list_garments(status="clean"))
    work = repo.list_garments(occasion="work")
    assert all("work" in g.occasions for g in work)
    assert [g.category for g in repo.list_garments(category="boots")] == ["boots"]


def test_fr3_retired_garments_are_excluded_on_request(repo):
    _seed(repo)
    repo.set_status("b1-tee", "retired", now=LATER)
    ids = {g.id for g in repo.list_garments(include_retired=False)}
    assert "b1-tee" not in ids
    assert "b1-tee" in {g.id for g in repo.list_garments()}


def test_fr3_invalid_transitions_are_refused(repo):
    _seed(repo)
    repo.set_status("b1-tee", "retired", now=LATER)
    with pytest.raises(InvalidTransition) as excinfo:
        repo.set_status("b1-tee", "clean", now=LATER)
    assert excinfo.value.code == "invalid_transition"


def test_fr3_marking_clean_resets_the_wear_counter(repo):
    _seed(repo)
    repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    assert repo.get_garment("b5-oxford").status == "dirty"
    repo.set_status("b5-oxford", "clean", now=LATER)
    assert repo.get_garment("b5-oxford").wears_since_wash == 0


# --------------------------------------------------------------------------
# FR-3 / FR-12 — wear logging and laundry
# --------------------------------------------------------------------------


def test_fr12_wear_log_advances_counters_transactionally(repo):
    _seed(repo)
    log = repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford", "p4-jeans"], now=NOW)
    assert {i.garment_id for i in log.items} == {"b5-oxford", "p4-jeans"}
    assert repo.get_garment("b5-oxford").wears_since_wash == 1
    assert repo.get_garment("p4-jeans").wears_since_wash == 1


def test_fr3_us7_second_wear_of_a_two_wear_shirt_makes_it_dirty(repo):
    _seed(repo)
    for _ in range(2):
        repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    shirt = repo.get_garment("b5-oxford")
    assert shirt.wears_since_wash == 2
    assert shirt.status == "dirty"


def test_fr12_wear_log_snapshots_the_layer_role(repo):
    _seed(repo)
    log = repo.add_wear_log(date="2026-04-14", garment_ids=["b6-flannel"], now=NOW)
    assert log.items[0].layer_role == "base"
    flannel = repo.get_garment("b6-flannel")
    repo.update_garment(flannel.model_copy(update={"layer_role": "mid"}))
    stored = repo.get_wear_log(log.id)
    assert stored.items[0].layer_role == "base"


def test_fr12_retired_garments_cannot_be_logged(repo):
    _seed(repo)
    repo.set_status("b1-tee", "retired", now=LATER)
    with pytest.raises(InvalidTransition):
        repo.add_wear_log(date="2026-04-14", garment_ids=["b1-tee"], now=NOW)


def test_fr12_dirty_garments_may_still_be_logged(repo):
    """The log records reality — the user wore it anyway (DATA_MODEL §2.7)."""
    _seed(repo)
    repo.set_status("b1-tee", "dirty", now=LATER)
    log = repo.add_wear_log(date="2026-04-14", garment_ids=["b1-tee"], now=NOW)
    assert log.items


def test_fr12_same_day_undo_reverses_the_side_effects(repo):
    _seed(repo)
    log = repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    mistake = repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    assert repo.get_garment("b5-oxford").status == "dirty"
    repo.undo_wear_log(mistake.id, today="2026-04-14", now=LATER)
    shirt = repo.get_garment("b5-oxford")
    assert (shirt.wears_since_wash, shirt.status) == (1, "clean")
    assert len(repo.list_wear_logs()) == 1
    assert repo.get_wear_log(log.id)


def test_fr12_undo_is_refused_after_the_calendar_day(repo):
    _seed(repo)
    log = repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    with pytest.raises(InvalidParams):
        repo.undo_wear_log(log.id, today="2026-04-15", now=LATER)
    assert repo.get_wear_log(log.id)


def test_fr12_multiple_logs_per_day_are_allowed(repo):
    _seed(repo)
    repo.add_wear_log(date="2026-04-14", garment_ids=["b1-tee", "p1-shorts"], now=NOW)
    repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford", "p4-jeans"], now=NOW)
    assert len(repo.list_wear_logs(since="2026-04-14", until="2026-04-14")) == 2


def test_fr12_an_empty_wear_log_is_refused(repo):
    _seed(repo)
    with pytest.raises(InvalidParams):
        repo.add_wear_log(date="2026-04-14", garment_ids=[], now=NOW)


def test_fr11_wear_history_projects_last_worn_and_yesterday(repo):
    _seed(repo)
    repo.add_wear_log(date="2026-04-11", garment_ids=["m3-thick"], now=NOW)
    repo.add_wear_log(date="2026-04-13", garment_ids=["b5-oxford", "p4-jeans", "a2-hat"], now=NOW)
    history = repo.wear_history("2026-04-14")
    assert history.last_worn["m3-thick"] == "2026-04-11"
    assert history.last_worn["b5-oxford"] == "2026-04-13"
    # the hat is an accessory: it is not part of the core-item set HC-8 compares
    assert history.yesterday_sets == (frozenset({"b5-oxford", "p4-jeans"}),)


def test_fr11_wear_history_ignores_the_future(repo):
    _seed(repo)
    repo.add_wear_log(date="2026-04-20", garment_ids=["m3-thick"], now=NOW)
    assert repo.wear_history("2026-04-14").last_worn == {}


def test_fr3_us7_laundry_returns_everything_dirty_to_clean(repo):
    _seed(repo)
    for _ in range(2):
        repo.add_wear_log(date="2026-04-14", garment_ids=["b5-oxford"], now=NOW)
    assert repo.dirty_garment_ids() == ["b5-oxford"]
    event = repo.add_laundry_event(repo.dirty_garment_ids(), now=LATER, note="sunday wash")
    assert event.garment_ids == ["b5-oxford"]
    shirt = repo.get_garment("b5-oxford")
    assert (shirt.status, shirt.wears_since_wash) == ("clean", 0)
    assert repo.dirty_garment_ids() == []


def test_fr3_laundering_a_clean_garment_is_refused(repo):
    _seed(repo)
    with pytest.raises(InvalidTransition):
        repo.add_laundry_event(["b1-tee"], now=LATER)


def test_fr3_in_laundry_is_an_allowed_intermediate_state(repo):
    _seed(repo)
    repo.set_status("b1-tee", "dirty", now=LATER)
    repo.set_status("b1-tee", "in_laundry", now=LATER)
    repo.add_laundry_event(["b1-tee"], now=LATER)
    assert repo.get_garment("b1-tee").status == "clean"


# --------------------------------------------------------------------------
# FR-4 / FR-13 — snapshots and recommendations
# --------------------------------------------------------------------------


def test_fr4_snapshots_round_trip_with_all_their_hours(repo):
    forecast = make_forecast(temps=12.0, snapshot_id="")
    stored = repo.add_snapshot(forecast)
    assert stored.id
    read = repo.get_snapshot(stored.id)
    assert len(read.hours) == 24
    assert read.hours == forecast.hours
    assert repo.latest_snapshot("2026-04-14").id == stored.id


def test_fr4_refetching_a_date_appends_a_second_snapshot(repo):
    first = repo.add_snapshot(make_forecast(temps=12.0, snapshot_id=""))
    second = repo.add_snapshot(
        make_forecast(temps=14.0, snapshot_id="").model_copy(update={"fetched_at": LATER})
    )
    assert first.id != second.id
    assert repo.latest_snapshot("2026-04-14").id == second.id


def test_fr13_recommendations_round_trip_with_plans_and_reasoning(repo, spring_swing):
    from dresscast.engine.assemble import recommend

    _seed(repo)
    repo.add_snapshot(spring_swing)
    rec = recommend(
        repo.list_garments(),
        spring_swing,
        WearHistory.empty(),
        params(occasion="casual"),
        now=NOW,
    )
    stored = repo.add_recommendation(rec)
    assert stored.id
    read = repo.get_recommendation(stored.id)
    assert [o.rank for o in read.outfits] == [o.rank for o in rec.outfits]
    assert [o.score_total for o in read.outfits] == [o.score_total for o in rec.outfits]
    assert read.outfits[0].hour_plan == rec.outfits[0].hour_plan
    assert [line.as_dict() for line in read.outfits[0].reasoning] == [
        line.as_dict() for line in rec.outfits[0].reasoning
    ]
    assert read.wardrobe_hash == rec.wardrobe_hash
    assert read.params == rec.params


def test_fr13_stored_plans_are_self_contained_after_a_wardrobe_edit(repo, spring_swing):
    from dresscast.engine.assemble import recommend

    _seed(repo)
    repo.add_snapshot(spring_swing)
    stored = repo.add_recommendation(
        recommend(
            repo.list_garments(),
            spring_swing,
            WearHistory.empty(),
            params(occasion="casual"),
            now=NOW,
        )
    )
    before = repo.get_recommendation(stored.id).outfits[0].hour_plan
    coat = repo.get_garment("o3-coat")
    repo.update_garment(coat.model_copy(update={"clo": 0.50}))
    assert repo.get_recommendation(stored.id).outfits[0].hour_plan == before


def test_fr13_latest_recommendation_for_a_date(repo, spring_swing):
    from dresscast.engine.assemble import recommend

    _seed(repo)
    repo.add_snapshot(spring_swing)
    rec = recommend(
        repo.list_garments(),
        spring_swing,
        WearHistory.empty(),
        params(occasion="casual"),
        now=NOW,
    )
    first = repo.add_recommendation(rec)
    second = repo.add_recommendation(rec.model_copy(update={"created_at": LATER}))
    assert repo.latest_recommendation("2026-04-14").id == second.id
    assert repo.latest_recommendation().id == second.id
    assert repo.outfit_garment_ids(first.outfits[0].id)


def test_schema_version_is_reported(repo):
    assert repo.schema_version() == SCHEMA_VERSION


# --------------------------------------------------------------------------
# SQLite specifics
# --------------------------------------------------------------------------


def test_sqlite_enforces_foreign_keys(tmp_path):
    store = SqliteRepository(tmp_path / "db.sqlite", now=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO wear_log_items VALUES ('missing','missing','base')")
    store.close()


def test_sqlite_migration_is_idempotent(tmp_path):
    path = tmp_path / "db.sqlite"
    first = SqliteRepository(path, now=NOW)
    first.add_garment(small_wardrobe()[0])
    first.close()
    second = SqliteRepository(path, now=LATER)
    assert second.schema_version() == SCHEMA_VERSION
    assert len(second.list_garments()) == 1
    second.close()


def test_sqlite_refuses_a_newer_schema(tmp_path):
    path = tmp_path / "db.sqlite"
    store = SqliteRepository(path, now=NOW)
    store.conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 5,))
    store.conn.commit()
    store.close()
    with pytest.raises(InvalidSchemaVersion):
        SqliteRepository(path, now=NOW)


def test_sqlite_migration_records_each_applied_version(tmp_path):
    store = SqliteRepository(tmp_path / "db.sqlite", now=NOW)
    rows = store.conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    assert [r["version"] for r in rows] == list(range(1, SCHEMA_VERSION + 1))
    assert current_version(store.conn) == SCHEMA_VERSION
    assert migrate(store.conn, now=NOW) == SCHEMA_VERSION
    store.close()


def test_sqlite_check_constraints_mirror_the_model(tmp_path):
    store = SqliteRepository(tmp_path / "db.sqlite", now=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO forecast_hours VALUES ('s',0,0,999,0,0,0,0,0)")
    store.close()
