"""SQLite repository (stdlib ``sqlite3``), schema per DATA_MODEL.md.

The invariants DATA_MODEL states as CHECK constraints are CHECK constraints here:
USD-only listings, ``sold_at >= listed_at``, the garment status/price pairings,
the advice confidence range and — the safeguard that matters — ``frame_checked =
1``, which makes a non-frame-checked advice row unrepresentable (FR-9).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ..ids import canonical_json
from ..models import (
    Advice,
    AdviceAction,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    Brand,
    DesignerEra,
    EventStatus,
    EventType,
    FashionEvent,
    Garment,
    GarmentStatus,
    IndexPoint,
    Listing,
    ListingStatus,
)
from ..weeks import week_key
from .base import RepositoryError, current_advice

__all__ = ["DB_PATH_ENV", "DEFAULT_DB_PATH", "SQLiteRepository"]

DB_PATH_ENV = "GRAILTRADER_DB"
DEFAULT_DB_PATH = Path.home() / ".grailtrader" / "grailtrader.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS brand (
  id      TEXT PRIMARY KEY,
  name    TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',
  notes   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS designer_era (
  id        TEXT PRIMARY KEY,
  brand_id  TEXT NOT NULL REFERENCES brand(id),
  designer  TEXT NOT NULL,
  label     TEXT NOT NULL,
  era_start TEXT NOT NULL,
  era_end   TEXT,
  notes     TEXT NOT NULL DEFAULT '',
  position  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS listing (
  id             TEXT PRIMARY KEY,
  source         TEXT NOT NULL,
  external_id    TEXT NOT NULL,
  brand_id       TEXT NOT NULL,
  era_id         TEXT NOT NULL,
  category       TEXT NOT NULL,
  condition      TEXT NOT NULL,
  platform_label TEXT NOT NULL,
  size           TEXT,
  title          TEXT,
  status         TEXT NOT NULL CHECK (status IN ('sold','active')),
  listed_at      TEXT NOT NULL,
  sold_at        TEXT,
  ask_price      REAL CHECK (ask_price IS NULL OR ask_price > 0),
  sold_price     REAL CHECK (sold_price IS NULL OR sold_price > 0),
  currency       TEXT NOT NULL CHECK (currency = 'USD'),
  UNIQUE (source, external_id),
  CHECK (status <> 'sold'
         OR (sold_price IS NOT NULL AND sold_at IS NOT NULL AND sold_at >= listed_at)),
  CHECK (status <> 'active'
         OR (ask_price IS NOT NULL AND sold_at IS NULL AND sold_price IS NULL))
);
CREATE INDEX IF NOT EXISTS listing_stratum ON listing (brand_id, era_id, category);

CREATE TABLE IF NOT EXISTS index_point (
  stratum_id  TEXT NOT NULL,
  week        TEXT NOT NULL,
  level_usd   REAL CHECK (level_usd IS NULL OR level_usd > 0),
  index_value REAL NOT NULL CHECK (index_value > 0),
  n_sales     INTEGER NOT NULL CHECK (n_sales >= 0),
  n_excluded  INTEGER NOT NULL CHECK (n_excluded >= 0),
  built_as_of TEXT NOT NULL,
  PRIMARY KEY (stratum_id, week)
);

CREATE TABLE IF NOT EXISTS fashion_event (
  id            TEXT PRIMARY KEY,
  event_type    TEXT NOT NULL,
  brand_id      TEXT NOT NULL,
  era_id        TEXT,
  attributes    TEXT NOT NULL DEFAULT '{}',
  occurred_on   TEXT NOT NULL,
  source        TEXT NOT NULL,
  source_refs   TEXT NOT NULL DEFAULT '[]',
  status        TEXT NOT NULL CHECK (status IN ('pending','confirmed','rejected')),
  corroboration INTEGER NOT NULL CHECK (corroboration >= 1),
  notes         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS event_brand ON fashion_event (brand_id, occurred_on);

CREATE TABLE IF NOT EXISTS garment (
  id                TEXT PRIMARY KEY,
  label             TEXT NOT NULL,
  brand_id          TEXT NOT NULL,
  era_id            TEXT NOT NULL,
  category          TEXT NOT NULL,
  condition         TEXT NOT NULL,
  anchor_condition  TEXT NOT NULL,
  size              TEXT,
  status            TEXT NOT NULL CHECK (status IN ('owned','watching','sold_archived')),
  acquisition_price REAL CHECK (acquisition_price IS NULL OR acquisition_price > 0),
  acquired_on       TEXT,
  reference_price   REAL CHECK (reference_price IS NULL OR reference_price > 0),
  reference_date    TEXT,
  disposed_price    REAL CHECK (disposed_price IS NULL OR disposed_price > 0),
  disposed_on       TEXT,
  added_at          TEXT NOT NULL,
  deleted_at        TEXT,
  notes             TEXT NOT NULL DEFAULT '',
  CHECK (status <> 'watching'
         OR (reference_price IS NOT NULL AND reference_date IS NOT NULL
             AND acquisition_price IS NULL AND acquired_on IS NULL)),
  CHECK (status = 'watching'
         OR (acquisition_price IS NOT NULL AND acquired_on IS NOT NULL
             AND reference_price IS NULL AND reference_date IS NULL)),
  CHECK (status <> 'sold_archived'
         OR (disposed_price IS NOT NULL AND disposed_on IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS advice (
  id                TEXT PRIMARY KEY,
  garment_id        TEXT NOT NULL,
  as_of_week        TEXT NOT NULL,
  inputs_hash       TEXT NOT NULL,
  config_version    TEXT NOT NULL,
  stratum_id        TEXT NOT NULL,
  action            TEXT NOT NULL CHECK (action IN ('buy','sell','hold')),
  is_candidate      INTEGER NOT NULL CHECK (is_candidate IN (0,1)),
  horizon_weeks     INTEGER NOT NULL CHECK (horizon_weeks > 0),
  expected_return   REAL,
  confidence        REAL CHECK (confidence IS NULL OR (confidence >= 0.05 AND confidence <= 0.95)),
  fair_value        REAL,
  fair_value_method TEXT NOT NULL,
  rationale_codes   TEXT NOT NULL DEFAULT '[]',
  rendered_text     TEXT NOT NULL,
  frame_checked     INTEGER NOT NULL CHECK (frame_checked = 1),
  created_as_of     TEXT NOT NULL,
  UNIQUE (garment_id, as_of_week, inputs_hash)
);
CREATE INDEX IF NOT EXISTS advice_current ON advice (garment_id, as_of_week, created_as_of);

CREATE TABLE IF NOT EXISTS backtest_run (
  id         TEXT PRIMARY KEY,
  params     TEXT NOT NULL,
  as_of      TEXT NOT NULL,
  aggregates TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_result (
  run_id           TEXT NOT NULL REFERENCES backtest_run(id),
  garment_id       TEXT NOT NULL,
  week             TEXT NOT NULL,
  action           TEXT NOT NULL,
  is_candidate     INTEGER NOT NULL CHECK (is_candidate IN (0,1)),
  horizon_weeks    INTEGER NOT NULL,
  confidence       REAL,
  expected_return  REAL,
  driver_event_ids TEXT NOT NULL DEFAULT '[]',
  entry_week       TEXT NOT NULL,
  realized_return  REAL,
  hit              INTEGER,
  excluded_reason  TEXT,
  PRIMARY KEY (run_id, garment_id, week),
  CHECK ((excluded_reason IS NULL) = (realized_return IS NOT NULL))
);
"""

_DROP = """
DROP TABLE IF EXISTS backtest_result;
DROP TABLE IF EXISTS backtest_run;
DROP TABLE IF EXISTS advice;
DROP TABLE IF EXISTS garment;
DROP TABLE IF EXISTS fashion_event;
DROP TABLE IF EXISTS index_point;
DROP TABLE IF EXISTS listing;
DROP TABLE IF EXISTS designer_era;
DROP TABLE IF EXISTS brand;
"""


def default_db_path() -> Path:
    override = os.environ.get(DB_PATH_ENV)
    return Path(override) if override else DEFAULT_DB_PATH


class SQLiteRepository:
    """The default backend: one local SQLite file, single writer (SCOPE D-17)."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    # -- lifecycle ----------------------------------------------------------- #

    def initialize(self, *, reset: bool = False) -> None:
        with self._conn:
            if reset:
                self._conn.executescript(_DROP)
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- gazetteer ----------------------------------------------------------- #

    def replace_gazetteer(self, brands: Sequence[Brand]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM designer_era")
            self._conn.execute("DELETE FROM brand")
            for brand in brands:
                self._conn.execute(
                    "INSERT INTO brand (id, name, aliases, notes) VALUES (?,?,?,?)",
                    (brand.id, brand.name, canonical_json(list(brand.aliases)), brand.notes),
                )
                for position, era in enumerate(brand.eras):
                    self._conn.execute(
                        "INSERT INTO designer_era "
                        "(id, brand_id, designer, label, era_start, era_end, notes, position) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            era.id,
                            era.brand_id,
                            era.designer,
                            era.label,
                            era.start,
                            era.end,
                            era.notes,
                            position,
                        ),
                    )

    def list_brands(self) -> list[Brand]:
        rows = self._conn.execute("SELECT * FROM brand ORDER BY id").fetchall()
        return [self._brand_from_row(row) for row in rows]

    def get_brand(self, brand_id: str) -> Brand | None:
        row = self._conn.execute("SELECT * FROM brand WHERE id = ?", (brand_id,)).fetchone()
        return None if row is None else self._brand_from_row(row)

    def _brand_from_row(self, row: sqlite3.Row) -> Brand:
        eras = self._conn.execute(
            "SELECT * FROM designer_era WHERE brand_id = ? ORDER BY position", (row["id"],)
        ).fetchall()
        return Brand(
            id=row["id"],
            name=row["name"],
            aliases=tuple(json.loads(row["aliases"])),
            notes=row["notes"],
            eras=tuple(
                DesignerEra(
                    id=era["id"],
                    brand_id=era["brand_id"],
                    designer=era["designer"],
                    label=era["label"],
                    start=era["era_start"],
                    end=era["era_end"],
                    notes=era["notes"],
                )
                for era in eras
            ),
        )

    # -- listings ------------------------------------------------------------ #

    def add_listings(self, listings: Iterable[Listing]) -> int:
        added = 0
        with self._conn:
            for listing in listings:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO listing (id, source, external_id, brand_id, era_id,"
                    " category, condition, platform_label, size, title, status, listed_at,"
                    " sold_at, ask_price, sold_price, currency)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        listing.id,
                        listing.source.value,
                        listing.external_id,
                        listing.brand_id,
                        listing.era_id,
                        listing.category.value,
                        listing.condition.value,
                        listing.platform_label,
                        listing.size,
                        listing.title,
                        listing.status.value,
                        listing.listed_at,
                        listing.sold_at,
                        listing.ask_price,
                        listing.sold_price,
                        listing.currency,
                    ),
                )
                added += cursor.rowcount if cursor.rowcount > 0 else 0
        return added

    def listing_ids(self) -> set[str]:
        return {row["id"] for row in self._conn.execute("SELECT id FROM listing")}

    def get_listing(self, listing_id: str) -> Listing | None:
        row = self._conn.execute("SELECT * FROM listing WHERE id = ?", (listing_id,)).fetchone()
        return None if row is None else _listing(row)

    def list_listings(
        self,
        *,
        stratum: str | None = None,
        status: ListingStatus | None = None,
        limit: int | None = None,
    ) -> list[Listing]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        sql = "SELECT * FROM listing"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(sold_at, listed_at), id"
        rows = [_listing(row) for row in self._conn.execute(sql, params)]
        if stratum is not None:
            rows = [row for row in rows if row.stratum_path.startswith(stratum)]
        return rows[:limit] if limit is not None else rows

    # -- index --------------------------------------------------------------- #

    def replace_index_points(self, points: Iterable[IndexPoint]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM index_point")
            self._conn.executemany(
                "INSERT INTO index_point (stratum_id, week, level_usd, index_value, n_sales,"
                " n_excluded, built_as_of) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        p.stratum_id,
                        p.week,
                        p.level_usd,
                        p.index_value,
                        p.n_sales,
                        p.n_excluded,
                        p.built_as_of,
                    )
                    for p in points
                ],
            )

    def list_index_points(
        self,
        *,
        stratum_id: str | None = None,
        from_week: str | None = None,
        to_week: str | None = None,
    ) -> list[IndexPoint]:
        clauses: list[str] = []
        params: list[Any] = []
        if stratum_id is not None:
            clauses.append("stratum_id = ?")
            params.append(stratum_id)
        if from_week is not None:
            clauses.append("week >= ?")
            params.append(week_key(from_week))
        if to_week is not None:
            clauses.append("week <= ?")
            params.append(week_key(to_week))
        sql = "SELECT * FROM index_point"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY stratum_id, week"
        return [
            IndexPoint(
                stratum_id=row["stratum_id"],
                week=row["week"],
                level_usd=row["level_usd"],
                index_value=row["index_value"],
                n_sales=row["n_sales"],
                n_excluded=row["n_excluded"],
                built_as_of=row["built_as_of"],
            )
            for row in self._conn.execute(sql, params)
        ]

    # -- events -------------------------------------------------------------- #

    def upsert_events(self, events: Iterable[FashionEvent]) -> None:
        with self._conn:
            for event in events:
                self._conn.execute(
                    "INSERT INTO fashion_event (id, event_type, brand_id, era_id, attributes,"
                    " occurred_on, source, source_refs, status, corroboration, notes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET source_refs = excluded.source_refs,"
                    " status = excluded.status, corroboration = excluded.corroboration,"
                    " notes = excluded.notes",
                    (
                        event.id,
                        event.event_type.value,
                        event.brand_id,
                        event.era_id,
                        canonical_json(event.attributes),
                        event.occurred_on,
                        event.source.value,
                        canonical_json(list(event.source_refs)),
                        event.status.value,
                        event.corroboration,
                        event.notes,
                    ),
                )

    def get_event(self, event_id: str) -> FashionEvent | None:
        row = self._conn.execute("SELECT * FROM fashion_event WHERE id = ?", (event_id,)).fetchone()
        return None if row is None else _event(row)

    def list_events(
        self,
        *,
        event_type: EventType | None = None,
        brand_id: str | None = None,
        status: EventStatus | None = None,
        since: str | None = None,
    ) -> list[FashionEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type.value)
        if brand_id is not None:
            clauses.append("brand_id = ?")
            params.append(brand_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if since is not None:
            clauses.append("occurred_on >= ?")
            params.append(since)
        sql = "SELECT * FROM fashion_event"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_on, id"
        return [_event(row) for row in self._conn.execute(sql, params)]

    # -- garments ------------------------------------------------------------ #

    def add_garment(self, garment: Garment) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO garment (id, label, brand_id, era_id, category, condition,"
                    " anchor_condition, size, status, acquisition_price, acquired_on,"
                    " reference_price, reference_date, disposed_price, disposed_on, added_at,"
                    " deleted_at, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _garment_params(garment),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"garment {garment.id}: {exc}") from None

    def update_garment(self, garment: Garment) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE garment SET label = ?, condition = ?, size = ?, status = ?,"
                " acquisition_price = ?, acquired_on = ?, reference_price = ?,"
                " reference_date = ?, disposed_price = ?, disposed_on = ?, deleted_at = ?,"
                " notes = ? WHERE id = ?",
                (
                    garment.label,
                    garment.condition.value,
                    garment.size,
                    garment.status.value,
                    garment.acquisition_price,
                    garment.acquired_on,
                    garment.reference_price,
                    garment.reference_date,
                    garment.disposed_price,
                    garment.disposed_on,
                    garment.deleted_at,
                    garment.notes,
                    garment.id,
                ),
            )
        if cursor.rowcount == 0:
            raise RepositoryError(f"unknown garment {garment.id}")

    def get_garment(self, garment_id: str) -> Garment | None:
        row = self._conn.execute("SELECT * FROM garment WHERE id = ?", (garment_id,)).fetchone()
        return None if row is None else _garment(row)

    def list_garments(
        self, *, include_deleted: bool = False, status: GarmentStatus | None = None
    ) -> list[Garment]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        sql = "SELECT * FROM garment"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY added_at, id"
        return [_garment(row) for row in self._conn.execute(sql, params)]

    # -- advice --------------------------------------------------------------- #

    def add_advice(self, advice: Advice) -> bool:
        if not advice.frame_checked:
            raise RepositoryError("FR-9: a non-frame-checked advice cannot be stored")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO advice (id, garment_id, as_of_week, inputs_hash,"
                " config_version, stratum_id, action, is_candidate, horizon_weeks,"
                " expected_return, confidence, fair_value, fair_value_method, rationale_codes,"
                " rendered_text, frame_checked, created_as_of)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    advice.id,
                    advice.garment_id,
                    advice.as_of_week,
                    advice.inputs_hash,
                    advice.config_version,
                    advice.stratum_id,
                    advice.action.value,
                    int(advice.is_candidate),
                    advice.horizon_weeks,
                    advice.expected_return,
                    advice.confidence,
                    advice.fair_value,
                    advice.fair_value_method.value,
                    canonical_json(list(advice.rationale_codes)),
                    advice.rendered_text,
                    1,
                    advice.created_as_of,
                ),
            )
        return cursor.rowcount > 0

    def get_advice(self, advice_id: str) -> Advice | None:
        row = self._conn.execute("SELECT * FROM advice WHERE id = ?", (advice_id,)).fetchone()
        return None if row is None else _advice(row)

    def list_advice(
        self,
        *,
        garment_id: str | None = None,
        action: AdviceAction | None = None,
        as_of_week: str | None = None,
        history: bool = False,
    ) -> list[Advice]:
        clauses: list[str] = []
        params: list[Any] = []
        if garment_id is not None:
            clauses.append("garment_id = ?")
            params.append(garment_id)
        if as_of_week is not None:
            clauses.append("as_of_week = ?")
            params.append(week_key(as_of_week))
        sql = "SELECT * FROM advice"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY as_of_week, garment_id, created_as_of"
        rows = [_advice(row) for row in self._conn.execute(sql, params)]
        if not history:
            rows = current_advice(rows)
        if action is not None:
            rows = [row for row in rows if row.action is action]
        return sorted(rows, key=lambda a: (a.as_of_week, a.garment_id, a.created_as_of))

    # -- backtests ------------------------------------------------------------ #

    def add_backtest(self, run: BacktestRun, results: Iterable[BacktestResult]) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO backtest_run (id, params, as_of, aggregates) VALUES (?,?,?,?)",
                    (
                        run.id,
                        canonical_json(run.params.model_dump(mode="json")),
                        run.as_of,
                        canonical_json(run.aggregates),
                    ),
                )
                self._conn.executemany(
                    "INSERT INTO backtest_result (run_id, garment_id, week, action,"
                    " is_candidate, horizon_weeks, confidence, expected_return,"
                    " driver_event_ids, entry_week, realized_return, hit, excluded_reason)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            r.run_id,
                            r.garment_id,
                            r.week,
                            r.action.value,
                            int(r.is_candidate),
                            r.horizon_weeks,
                            r.confidence,
                            r.expected_return,
                            canonical_json(list(r.driver_event_ids)),
                            r.entry_week,
                            r.realized_return,
                            None if r.hit is None else int(r.hit),
                            None if r.excluded_reason is None else r.excluded_reason.value,
                        )
                        for r in results
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"backtest run {run.id}: {exc}") from None

    def get_backtest_run(self, run_id: str) -> BacktestRun | None:
        row = self._conn.execute("SELECT * FROM backtest_run WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else _run(row)

    def list_backtest_runs(self, *, limit: int | None = None) -> list[BacktestRun]:
        sql = "SELECT * FROM backtest_run ORDER BY as_of DESC, id DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [_run(row) for row in self._conn.execute(sql)]

    def list_backtest_results(self, run_id: str) -> list[BacktestResult]:
        rows = self._conn.execute(
            "SELECT * FROM backtest_result WHERE run_id = ? ORDER BY week, garment_id",
            (run_id,),
        )
        return [
            BacktestResult(
                run_id=row["run_id"],
                garment_id=row["garment_id"],
                week=row["week"],
                action=AdviceAction(row["action"]),
                is_candidate=bool(row["is_candidate"]),
                horizon_weeks=row["horizon_weeks"],
                confidence=row["confidence"],
                expected_return=row["expected_return"],
                driver_event_ids=tuple(json.loads(row["driver_event_ids"])),
                entry_week=row["entry_week"],
                realized_return=row["realized_return"],
                hit=None if row["hit"] is None else bool(row["hit"]),
                excluded_reason=row["excluded_reason"],
            )
            for row in rows
        ]


def _listing(row: sqlite3.Row) -> Listing:
    return Listing(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        brand_id=row["brand_id"],
        era_id=row["era_id"],
        category=row["category"],
        condition=row["condition"],
        platform_label=row["platform_label"],
        size=row["size"],
        title=row["title"],
        status=row["status"],
        listed_at=row["listed_at"],
        sold_at=row["sold_at"],
        ask_price=row["ask_price"],
        sold_price=row["sold_price"],
        currency=row["currency"],
    )


def _event(row: sqlite3.Row) -> FashionEvent:
    return FashionEvent(
        id=row["id"],
        event_type=row["event_type"],
        brand_id=row["brand_id"],
        era_id=row["era_id"],
        attributes=json.loads(row["attributes"]),
        occurred_on=row["occurred_on"],
        source=row["source"],
        source_refs=tuple(json.loads(row["source_refs"])),
        status=row["status"],
        corroboration=row["corroboration"],
        notes=row["notes"],
    )


def _garment_params(garment: Garment) -> tuple[Any, ...]:
    return (
        garment.id,
        garment.label,
        garment.brand_id,
        garment.era_id,
        garment.category.value,
        garment.condition.value,
        garment.anchor_condition.value,
        garment.size,
        garment.status.value,
        garment.acquisition_price,
        garment.acquired_on,
        garment.reference_price,
        garment.reference_date,
        garment.disposed_price,
        garment.disposed_on,
        garment.added_at,
        garment.deleted_at,
        garment.notes,
    )


def _garment(row: sqlite3.Row) -> Garment:
    return Garment(
        id=row["id"],
        label=row["label"],
        brand_id=row["brand_id"],
        era_id=row["era_id"],
        category=row["category"],
        condition=row["condition"],
        anchor_condition=row["anchor_condition"],
        size=row["size"],
        status=row["status"],
        acquisition_price=row["acquisition_price"],
        acquired_on=row["acquired_on"],
        reference_price=row["reference_price"],
        reference_date=row["reference_date"],
        disposed_price=row["disposed_price"],
        disposed_on=row["disposed_on"],
        added_at=row["added_at"],
        deleted_at=row["deleted_at"],
        notes=row["notes"],
    )


def _advice(row: sqlite3.Row) -> Advice:
    return Advice(
        id=row["id"],
        garment_id=row["garment_id"],
        as_of_week=row["as_of_week"],
        inputs_hash=row["inputs_hash"],
        config_version=row["config_version"],
        stratum_id=row["stratum_id"],
        action=row["action"],
        is_candidate=bool(row["is_candidate"]),
        horizon_weeks=row["horizon_weeks"],
        expected_return=row["expected_return"],
        confidence=row["confidence"],
        fair_value=row["fair_value"],
        fair_value_method=row["fair_value_method"],
        rationale_codes=tuple(json.loads(row["rationale_codes"])),
        rendered_text=row["rendered_text"],
        frame_checked=bool(row["frame_checked"]),
        created_as_of=row["created_as_of"],
    )


def _run(row: sqlite3.Row) -> BacktestRun:
    return BacktestRun(
        id=row["id"],
        params=BacktestParams.model_validate(json.loads(row["params"])),
        as_of=row["as_of"],
        aggregates=json.loads(row["aggregates"]),
    )
