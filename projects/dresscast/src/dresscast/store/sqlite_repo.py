"""SQLite repository (stdlib ``sqlite3``), schema per DATA_MODEL.md §4."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from dresscast.engine.models import (
    AcceptedField,
    AttributeSuggestion,
    Compromise,
    DayForecast,
    Garment,
    HourlyWeather,
    HourPlanEntry,
    LaundryEvent,
    Note,
    OutfitItem,
    ReasonLine,
    Recommendation,
    RequestParams,
    ScoreBreakdown,
    ScoredOutfit,
    WearHistory,
    WearLog,
    WearLogItem,
    apply_wear,
    check_transition,
    undo_wear,
    wash,
)
from dresscast.engine.variety import previous_day
from dresscast.errors import InvalidParams, InvalidTransition, UnknownGarment
from dresscast.store.migrations import migrate
from dresscast.store.repository import merge_suggestion


def _new_id() -> str:
    return str(uuid.uuid4())


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteRepository:
    """The default backend.  WAL, foreign keys on, every multi-row effect atomic.

    Thread-safety: FastAPI runs sync handlers — and sync generator
    dependencies' ``__enter__``/``__exit__`` — on anyio threadpool workers, so
    the connection is touched from threads other than the one that built it.
    ``check_same_thread`` is therefore disabled (CPython's
    ``sqlite3.threadsafety == 3``: the C level is serialized) and ``self.lock``
    is held from the first read of every read-modify-write composite through
    its commit, so two threads' transactions can never interleave on the one
    connection.
    """

    def __init__(self, path: str | Path = ":memory:", *, now: datetime | None = None) -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        migrate(self.conn, now=now or datetime(2026, 1, 1))

    def close(self) -> None:
        self.conn.close()

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        return int(row["version"]) if row else 0

    # ------------------------------------------------------------------
    # Garments
    # ------------------------------------------------------------------

    @staticmethod
    def _garment_row(g: Garment) -> tuple[Any, ...]:
        return (
            g.id,
            g.name,
            g.category,
            g.layer_role,
            g.accessory_class,
            g.clo,
            g.waterproofness,
            g.windproofness,
            g.formality,
            json.dumps([c.model_dump() for c in g.colors]),
            json.dumps(g.style_tags),
            json.dumps(g.occasions),
            g.wears_before_laundry,
            g.wears_since_wash,
            g.status,
            json.dumps(g.overridden_fields),
            g.photo_path,
            g.photo_sha256,
            g.notes,
            g.created_at.isoformat(),
            g.updated_at.isoformat(),
        )

    @staticmethod
    def _garment(row: sqlite3.Row) -> Garment:
        return Garment(
            id=row["id"],
            name=row["name"],
            category=row["category"],
            layer_role=row["layer_role"],
            accessory_class=row["accessory_class"],
            clo=row["clo"],
            waterproofness=row["waterproofness"],
            windproofness=row["windproofness"],
            formality=row["formality"],
            colors=json.loads(row["colors"]),
            style_tags=json.loads(row["style_tags"]),
            occasions=json.loads(row["occasions"]),
            wears_before_laundry=row["wears_before_laundry"],
            wears_since_wash=row["wears_since_wash"],
            status=row["status"],
            overridden_fields=json.loads(row["overridden_fields"]),
            photo_path=row["photo_path"],
            photo_sha256=row["photo_sha256"],
            notes=row["notes"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def add_garment(self, garment: Garment) -> Garment:
        stored = garment if garment.id else garment.model_copy(update={"id": _new_id()})
        try:
            with self.lock, self.conn:
                self.conn.execute(
                    "INSERT INTO garments VALUES (" + ",".join("?" * 21) + ")",
                    self._garment_row(stored),
                )
        except sqlite3.IntegrityError as exc:
            raise InvalidParams(
                f"a garment named {stored.name!r} already exists", field="name"
            ) from exc
        return stored

    def get_garment(self, garment_id: str) -> Garment:
        row = self.conn.execute("SELECT * FROM garments WHERE id = ?", (garment_id,)).fetchone()
        if row is None:
            raise UnknownGarment(f"no garment {garment_id!r}", garment_id=garment_id)
        return self._garment(row)

    def find_garment(self, needle: str) -> Garment | None:
        row = self.conn.execute(
            "SELECT * FROM garments WHERE id = ? OR name = ? COLLATE NOCASE",
            (needle, needle),
        ).fetchone()
        return self._garment(row) if row else None

    def list_garments(
        self,
        *,
        status: str | None = None,
        occasion: str | None = None,
        category: str | None = None,
        include_retired: bool = True,
    ) -> list[Garment]:
        sql = "SELECT * FROM garments"
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if category:
            clauses.append("category = ?")
            args.append(category)
        if not include_retired:
            clauses.append("status <> 'retired'")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        rows = [self._garment(r) for r in self.conn.execute(sql, args)]
        if occasion:
            rows = [g for g in rows if occasion in g.occasions]
        return rows

    def update_garment(self, garment: Garment) -> Garment:
        with self.lock:
            self.get_garment(garment.id)
            with self.conn:
                self.conn.execute(
                    """UPDATE garments SET name=?, category=?, layer_role=?, accessory_class=?,
                           clo=?, waterproofness=?, windproofness=?, formality=?, colors=?,
                           style_tags=?, occasions=?, wears_before_laundry=?,
                           wears_since_wash=?, status=?, overridden_fields=?, photo_path=?,
                           photo_sha256=?, notes=?, created_at=?, updated_at=?
                       WHERE id=?""",
                    (*self._garment_row(garment)[1:], garment.id),
                )
        return garment

    def set_status(self, garment_id: str, status: str, *, now: datetime) -> Garment:
        with self.lock:
            garment = self.get_garment(garment_id)
            check_transition(garment.status, status)
            update: dict[str, Any] = {"status": status, "updated_at": now}
            if status == "clean":
                update["wears_since_wash"] = 0
            return self.update_garment(garment.model_copy(update=update))

    # ------------------------------------------------------------------
    # Attribute suggestions
    # ------------------------------------------------------------------

    @staticmethod
    def _suggestion(row: sqlite3.Row) -> AttributeSuggestion:
        return AttributeSuggestion(
            id=row["id"],
            garment_id=row["garment_id"],
            source=row["source"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            accepted_fields=(
                [AcceptedField.model_validate(f) for f in json.loads(row["accepted_fields"])]
                if row["accepted_fields"] is not None
                else None
            ),
            created_at=_dt(row["created_at"]),
            resolved_at=_dt(row["resolved_at"]) if row["resolved_at"] else None,
        )

    def add_suggestion(self, suggestion: AttributeSuggestion) -> AttributeSuggestion:
        stored = suggestion if suggestion.id else suggestion.model_copy(update={"id": _new_id()})
        with self.lock:
            self.get_garment(stored.garment_id)
            with self.conn:
                self.conn.execute(
                    "INSERT INTO attribute_suggestions VALUES (?,?,?,?,?,?,?,?)",
                    (
                        stored.id,
                        stored.garment_id,
                        stored.source,
                        json.dumps(stored.payload),
                        stored.status,
                        None
                        if stored.accepted_fields is None
                        else json.dumps([f.model_dump() for f in stored.accepted_fields]),
                        stored.created_at.isoformat(),
                        stored.resolved_at.isoformat() if stored.resolved_at else None,
                    ),
                )
        return stored

    def get_suggestion(self, suggestion_id: str) -> AttributeSuggestion:
        row = self.conn.execute(
            "SELECT * FROM attribute_suggestions WHERE id = ?", (suggestion_id,)
        ).fetchone()
        if row is None:
            raise InvalidParams(f"no suggestion {suggestion_id!r}", field="suggestion_id")
        return self._suggestion(row)

    def list_suggestions(self, garment_id: str | None = None) -> list[AttributeSuggestion]:
        if garment_id:
            rows = self.conn.execute(
                "SELECT * FROM attribute_suggestions WHERE garment_id = ? ORDER BY created_at, id",
                (garment_id,),
            )
        else:
            rows = self.conn.execute("SELECT * FROM attribute_suggestions ORDER BY created_at, id")
        return [self._suggestion(r) for r in rows]

    def accept_suggestion(
        self, suggestion_id: str, fields: Sequence[str], *, now: datetime
    ) -> tuple[AttributeSuggestion, Garment]:
        with self.lock:
            suggestion = self.get_suggestion(suggestion_id)
            if suggestion.status != "pending":
                raise InvalidParams(
                    f"suggestion {suggestion_id!r} is already {suggestion.status}",
                    field="status",
                )
            garment = self.get_garment(suggestion.garment_id)
            merged, accepted = merge_suggestion(garment, suggestion.payload, fields, now=now)
            resolved = suggestion.model_copy(
                update={
                    "status": "accepted",
                    "accepted_fields": accepted,
                    "resolved_at": now,
                }
            )
            with self.conn:
                self.conn.execute(
                    "UPDATE attribute_suggestions SET status=?, accepted_fields=?, resolved_at=? "
                    "WHERE id=?",
                    (
                        "accepted",
                        json.dumps([f.model_dump() for f in accepted]),
                        now.isoformat(),
                        suggestion_id,
                    ),
                )
                self.conn.execute(
                    """UPDATE garments SET name=?, category=?, layer_role=?, accessory_class=?,
                           clo=?, waterproofness=?, windproofness=?, formality=?, colors=?,
                           style_tags=?, occasions=?, wears_before_laundry=?,
                           wears_since_wash=?, status=?, overridden_fields=?, photo_path=?,
                           photo_sha256=?, notes=?, created_at=?, updated_at=?
                       WHERE id=?""",
                    (*self._garment_row(merged)[1:], merged.id),
                )
        return resolved, merged

    def reject_suggestion(self, suggestion_id: str, *, now: datetime) -> AttributeSuggestion:
        with self.lock:
            suggestion = self.get_suggestion(suggestion_id)
            if suggestion.status != "pending":
                raise InvalidParams(
                    f"suggestion {suggestion_id!r} is already {suggestion.status}",
                    field="status",
                )
            with self.conn:
                self.conn.execute(
                    "UPDATE attribute_suggestions SET status='rejected', resolved_at=? WHERE id=?",
                    (now.isoformat(), suggestion_id),
                )
        return suggestion.model_copy(update={"status": "rejected", "resolved_at": now})

    # ------------------------------------------------------------------
    # Forecast snapshots
    # ------------------------------------------------------------------

    def add_snapshot(self, forecast: DayForecast) -> DayForecast:
        stored = forecast if forecast.id else forecast.model_copy(update={"id": _new_id()})
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO forecast_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    stored.id,
                    stored.date,
                    stored.location_name,
                    stored.lat,
                    stored.lon,
                    stored.timezone,
                    stored.provider,
                    stored.fetched_at.isoformat(),
                    json.dumps(stored.raw) if stored.raw is not None else None,
                ),
            )
            self.conn.executemany(
                "INSERT INTO forecast_hours VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        stored.id,
                        h.seq,
                        h.hour,
                        h.temp_c,
                        h.wind_kmh,
                        h.humidity_pct,
                        h.precip_prob,
                        h.precip_mmh,
                        h.uv_index,
                    )
                    for h in stored.hours
                ],
            )
        return stored

    def _snapshot(self, row: sqlite3.Row) -> DayForecast:
        hours = [
            HourlyWeather(
                seq=h["seq"],
                hour=h["hour"],
                temp_c=h["temp_c"],
                wind_kmh=h["wind_kmh"],
                humidity_pct=h["humidity_pct"],
                precip_prob=h["precip_prob"],
                precip_mmh=h["precip_mmh"],
                uv_index=h["uv_index"],
            )
            for h in self.conn.execute(
                "SELECT * FROM forecast_hours WHERE snapshot_id = ? ORDER BY seq",
                (row["id"],),
            )
        ]
        return DayForecast(
            id=row["id"],
            date=row["date"],
            location_name=row["location_name"],
            lat=row["lat"],
            lon=row["lon"],
            timezone=row["timezone"],
            provider=row["provider"],
            fetched_at=_dt(row["fetched_at"]),
            raw=json.loads(row["raw"]) if row["raw"] else None,
            hours=hours,
        )

    def get_snapshot(self, snapshot_id: str) -> DayForecast:
        row = self.conn.execute(
            "SELECT * FROM forecast_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise InvalidParams(f"no snapshot {snapshot_id!r}", field="snapshot_id")
        return self._snapshot(row)

    def latest_snapshot(self, date: str) -> DayForecast | None:
        row = self.conn.execute(
            "SELECT * FROM forecast_snapshots WHERE date = ? "
            "ORDER BY fetched_at DESC, id DESC LIMIT 1",
            (date,),
        ).fetchone()
        return self._snapshot(row) if row else None

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def add_recommendation(self, recommendation: Recommendation) -> Recommendation:
        rec_id = recommendation.id or _new_id()
        outfits: list[ScoredOutfit] = []
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO recommendations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rec_id,
                    recommendation.date,
                    recommendation.snapshot_id,
                    recommendation.created_at.isoformat(),
                    recommendation.engine_version,
                    recommendation.seed,
                    json.dumps(recommendation.params.model_dump(mode="json")),
                    recommendation.wardrobe_hash,
                    json.dumps([n.model_dump() for n in recommendation.notes]),
                    json.dumps([c.model_dump() for c in recommendation.compromises]),
                ),
            )
            for outfit in recommendation.outfits:
                outfit_id = outfit.id or _new_id()
                self.conn.execute(
                    "INSERT INTO recommendation_outfits VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        outfit_id,
                        rec_id,
                        outfit.rank,
                        outfit.score_total,
                        json.dumps(outfit.scores.model_dump()),
                        json.dumps([e.model_dump() for e in outfit.hour_plan]),
                        json.dumps([r.as_dict() for r in outfit.reasoning]),
                        json.dumps([a.as_dict() for a in outfit.accessories]),
                        json.dumps([n.model_dump() for n in outfit.notes]),
                        json.dumps([c.model_dump() for c in outfit.compromises]),
                    ),
                )
                self.conn.executemany(
                    "INSERT INTO outfit_items VALUES (?,?,?)",
                    [(outfit_id, i.slot, i.garment_id) for i in outfit.items],
                )
                outfits.append(outfit.model_copy(update={"id": outfit_id}))
        return recommendation.model_copy(update={"id": rec_id, "outfits": outfits})

    def _outfits(self, recommendation_id: str) -> list[ScoredOutfit]:
        outfits: list[ScoredOutfit] = []
        for row in self.conn.execute(
            "SELECT * FROM recommendation_outfits WHERE recommendation_id = ? ORDER BY rank",
            (recommendation_id,),
        ):
            items = [
                OutfitItem(slot=i["slot"], garment_id=i["garment_id"])
                for i in self.conn.execute(
                    "SELECT * FROM outfit_items WHERE outfit_id = ? ORDER BY slot",
                    (row["id"],),
                )
            ]
            outfits.append(
                ScoredOutfit(
                    id=row["id"],
                    rank=row["rank"],
                    score_total=row["score_total"],
                    scores=ScoreBreakdown.model_validate(json.loads(row["scores"])),
                    items=items,
                    hour_plan=[
                        HourPlanEntry.model_validate(e) for e in json.loads(row["hour_plan"])
                    ],
                    reasoning=[ReasonLine.model_validate(r) for r in json.loads(row["reasoning"])],
                    accessories=json.loads(row["accessories"]),
                    notes=[Note.model_validate(n) for n in json.loads(row["notes"])],
                    compromises=[
                        Compromise.model_validate(c) for c in json.loads(row["compromises"])
                    ],
                )
            )
        return outfits

    def _recommendation(self, row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            id=row["id"],
            date=row["date"],
            snapshot_id=row["snapshot_id"],
            created_at=_dt(row["created_at"]),
            engine_version=row["engine_version"],
            seed=row["seed"],
            params=RequestParams.model_validate(json.loads(row["params"])),
            wardrobe_hash=row["wardrobe_hash"],
            outfits=self._outfits(row["id"]),
            notes=[Note.model_validate(n) for n in json.loads(row["notes"])],
            compromises=[Compromise.model_validate(c) for c in json.loads(row["compromises"])],
        )

    def get_recommendation(self, recommendation_id: str) -> Recommendation:
        row = self.conn.execute(
            "SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)
        ).fetchone()
        if row is None:
            raise InvalidParams(
                f"no recommendation {recommendation_id!r}", field="recommendation_id"
            )
        return self._recommendation(row)

    def latest_recommendation(self, date: str | None = None) -> Recommendation | None:
        if date:
            row = self.conn.execute(
                "SELECT * FROM recommendations WHERE date = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (date,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM recommendations ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return self._recommendation(row) if row else None

    def outfit_garment_ids(self, outfit_id: str) -> list[str]:
        return [
            r["garment_id"]
            for r in self.conn.execute(
                "SELECT garment_id FROM outfit_items WHERE outfit_id = ? ORDER BY slot",
                (outfit_id,),
            )
        ]

    # ------------------------------------------------------------------
    # Wear logs
    # ------------------------------------------------------------------

    def add_wear_log(
        self,
        *,
        date: str,
        garment_ids: Sequence[str],
        source: str = "manual",
        outfit_id: str | None = None,
        now: datetime,
    ) -> WearLog:
        if not garment_ids:
            raise InvalidParams("a wear log must list at least one garment", field="garment_ids")
        unique = list(dict.fromkeys(garment_ids))
        with self.lock:
            garments = [self.get_garment(g) for g in unique]
            for g in garments:
                if g.status == "retired":
                    raise InvalidTransition(
                        f"{g.name!r} is retired and cannot be worn", garment_id=g.id
                    )
            log = WearLog(
                id=_new_id(),
                date=date,
                source=source,  # type: ignore[arg-type]
                outfit_id=outfit_id,
                created_at=now,
                items=[WearLogItem(garment_id=g.id, layer_role=g.layer_role) for g in garments],
            )
            with self.conn:
                self.conn.execute(
                    "INSERT INTO wear_logs VALUES (?,?,?,?,?)",
                    (log.id, log.date, log.source, log.outfit_id, log.created_at.isoformat()),
                )
                self.conn.executemany(
                    "INSERT INTO wear_log_items VALUES (?,?,?)",
                    [(log.id, i.garment_id, i.layer_role) for i in log.items],
                )
                for g in garments:
                    worn = apply_wear(g, now)
                    self.conn.execute(
                        "UPDATE garments SET wears_since_wash=?, status=?, updated_at=? "
                        "WHERE id=?",
                        (worn.wears_since_wash, worn.status, worn.updated_at.isoformat(), g.id),
                    )
        return log

    def _wear_log(self, row: sqlite3.Row) -> WearLog:
        items = [
            WearLogItem(garment_id=i["garment_id"], layer_role=i["layer_role"])
            for i in self.conn.execute(
                "SELECT * FROM wear_log_items WHERE wear_log_id = ? ORDER BY garment_id",
                (row["id"],),
            )
        ]
        return WearLog(
            id=row["id"],
            date=row["date"],
            source=row["source"],
            outfit_id=row["outfit_id"],
            created_at=_dt(row["created_at"]),
            items=items,
        )

    def get_wear_log(self, log_id: str) -> WearLog:
        row = self.conn.execute("SELECT * FROM wear_logs WHERE id = ?", (log_id,)).fetchone()
        if row is None:
            raise InvalidParams(f"no wear log {log_id!r}", field="log_id")
        return self._wear_log(row)

    def list_wear_logs(
        self, *, since: str | None = None, until: str | None = None
    ) -> list[WearLog]:
        sql = "SELECT * FROM wear_logs"
        clauses: list[str] = []
        args: list[Any] = []
        if since:
            clauses.append("date >= ?")
            args.append(since)
        if until:
            clauses.append("date <= ?")
            args.append(until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY date, created_at, id"
        return [self._wear_log(r) for r in self.conn.execute(sql, args)]

    def undo_wear_log(self, log_id: str, *, today: str, now: datetime) -> None:
        """FR-12: reverse a mistaken log, on its own calendar day only."""
        with self.lock:
            log = self.get_wear_log(log_id)
            if log.created_at.date().isoformat() != today:
                raise InvalidParams(
                    "a wear log may only be undone on the calendar day it was created",
                    log_id=log_id,
                    created=log.created_at.date().isoformat(),
                    today=today,
                )
            with self.conn:
                for item in log.items:
                    garment = self.get_garment(item.garment_id)
                    reverted = undo_wear(garment, now)
                    self.conn.execute(
                        "UPDATE garments SET wears_since_wash=?, status=?, updated_at=? "
                        "WHERE id=?",
                        (
                            reverted.wears_since_wash,
                            reverted.status,
                            reverted.updated_at.isoformat(),
                            garment.id,
                        ),
                    )
                self.conn.execute("DELETE FROM wear_log_items WHERE wear_log_id = ?", (log_id,))
                self.conn.execute("DELETE FROM wear_logs WHERE id = ?", (log_id,))

    def wear_history(self, date: str) -> WearHistory:
        """FR-11/HC-8's projection: last-worn dates and yesterday's core sets."""
        last: dict[str, str] = {}
        for row in self.conn.execute(
            """SELECT i.garment_id AS gid, MAX(l.date) AS last_date
                 FROM wear_log_items i JOIN wear_logs l ON l.id = i.wear_log_id
                WHERE l.date <= ?
             GROUP BY i.garment_id""",
            (date,),
        ):
            last[row["gid"]] = row["last_date"]
        yesterday = previous_day(date)
        sets = [
            log.core_ids()
            for log in self.list_wear_logs(since=yesterday, until=yesterday)
            if log.core_ids()
        ]
        return WearHistory(last_worn=last, yesterday_sets=tuple(sets))

    # ------------------------------------------------------------------
    # Laundry
    # ------------------------------------------------------------------

    def dirty_garment_ids(self) -> list[str]:
        return [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM garments WHERE status IN ('dirty','in_laundry') ORDER BY id"
            )
        ]

    def add_laundry_event(
        self,
        garment_ids: Sequence[str],
        *,
        now: datetime,
        note: str | None = None,
    ) -> LaundryEvent:
        unique = list(dict.fromkeys(garment_ids))
        with self.lock:
            garments = [self.get_garment(g) for g in unique]
            for g in garments:
                if g.status not in {"dirty", "in_laundry"}:
                    raise InvalidTransition(
                        f"{g.name!r} is {g.status} and cannot be laundered",
                        garment_id=g.id,
                        status=g.status,
                    )
            event = LaundryEvent(
                id=_new_id(), created_at=now, note=note, garment_ids=[g.id for g in garments]
            )
            with self.conn:
                self.conn.execute(
                    "INSERT INTO laundry_events VALUES (?,?,?)",
                    (event.id, event.created_at.isoformat(), event.note),
                )
                self.conn.executemany(
                    "INSERT INTO laundry_event_items VALUES (?,?)",
                    [(event.id, g.id) for g in garments],
                )
                for g in garments:
                    cleaned = wash(g, now)
                    self.conn.execute(
                        "UPDATE garments SET wears_since_wash=?, status=?, updated_at=? "
                        "WHERE id=?",
                        (
                            cleaned.wears_since_wash,
                            cleaned.status,
                            cleaned.updated_at.isoformat(),
                            g.id,
                        ),
                    )
        return event
