"""SQLite repository (stdlib ``sqlite3``) — the default backend.

Schema follows DATA_MODEL.md's SQLite entities exactly; JSON-typed columns are
stored as TEXT holding canonical JSON.  Writes that must be atomic (a plan set
with its plans and steps; a step execution with its ledger entries) run inside a
single transaction.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from ..models import (
    Caveat,
    Goal,
    GoalStatus,
    LedgerEntry,
    Plan,
    PlanParams,
    PlanSet,
    PlanStep,
    Profile,
)
from .base import Repository

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    display_name       TEXT    NOT NULL,
    home_city          TEXT,
    default_passengers INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT    NOT NULL DEFAULT '',
    updated_at         TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS wallet_card (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_product_id TEXT NOT NULL UNIQUE,
    added_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT    NOT NULL,
    raw_text            TEXT,
    origin_city         TEXT,
    dest_city           TEXT,
    cabin               TEXT,
    round_trip          INTEGER,
    passengers          INTEGER,
    city                TEXT,
    nights              INTEGER,
    travel_window_start TEXT,
    travel_window_end   TEXT,
    book_by             TEXT,
    cash_programs       TEXT,
    cash_max_points     TEXT,
    status              TEXT    NOT NULL,
    created_at          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plan_set (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id             INTEGER REFERENCES goal(id),
    world_version       TEXT    NOT NULL,
    world_hash          TEXT    NOT NULL,
    today               TEXT    NOT NULL,
    params              TEXT    NOT NULL,
    expansions          INTEGER NOT NULL DEFAULT 0,
    verdict             TEXT    NOT NULL,
    recommended_plan_id INTEGER,
    disclaimer          TEXT    NOT NULL,
    created_at          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plan (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_set_id         INTEGER NOT NULL REFERENCES plan_set(id),
    rank                INTEGER NOT NULL,
    is_comparator       INTEGER NOT NULL DEFAULT 0,
    gross_value_cents   INTEGER NOT NULL,
    cash_outlay_cents   INTEGER NOT NULL,
    points_cost_cents   INTEGER NOT NULL,
    net_value_cents     INTEGER NOT NULL,
    cash_received_cents INTEGER,
    realized_cpp_milli  INTEGER,
    points_spent        TEXT    NOT NULL,
    feasible_in_days    INTEGER NOT NULL DEFAULT 0,
    signature           TEXT    NOT NULL,
    caveats             TEXT    NOT NULL,
    UNIQUE (plan_set_id, rank),
    UNIQUE (plan_set_id, signature)
);

CREATE TABLE IF NOT EXISTS plan_step (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id          INTEGER NOT NULL REFERENCES plan(id),
    seq              INTEGER NOT NULL,
    kind             TEXT    NOT NULL,
    hop_index        INTEGER NOT NULL DEFAULT 0,
    from_program     TEXT,
    to_program       TEXT,
    edge_id          TEXT,
    offer_id         TEXT,
    cashout_id       TEXT,
    points_sent      INTEGER NOT NULL,
    points_delivered INTEGER,
    fees_cents       INTEGER NOT NULL DEFAULT 0,
    eta_days         INTEGER NOT NULL DEFAULT 0,
    irreversible     INTEGER NOT NULL DEFAULT 0,
    explanation      TEXT    NOT NULL DEFAULT '',
    executed_at      TEXT,
    UNIQUE (plan_id, seq)
);

CREATE TABLE IF NOT EXISTS ledger_entry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id   TEXT    NOT NULL,
    delta_points INTEGER NOT NULL,
    post_balance INTEGER NOT NULL CHECK (post_balance >= 0),
    reason       TEXT    NOT NULL,
    plan_step_id INTEGER REFERENCES plan_step(id),
    note         TEXT,
    at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ledger_entry_program ON ledger_entry (program_id, id);
CREATE INDEX IF NOT EXISTS plan_set_goal ON plan_set (goal_id, id);
"""


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"))


def _unjson(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


class SQLiteRepository(Repository):
    """Durable repository backed by a SQLite file (or ``:memory:``).

    Thread-safety: FastAPI runs sync endpoints in a threadpool, so the one
    connection built at startup is used from many threads.  ``check_same_thread``
    is disabled (CPython's ``sqlite3.threadsafety == 3`` — the C level is
    serialized) and every write path funnels through :meth:`_batch`, which holds
    an ``RLock`` from the first read of a read-modify-write composite to the
    commit, so ledger chains stay intact and one thread's rollback can never
    discard another's uncommitted rows.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._depth = 0
        self._lock = threading.RLock()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transactions ------------------------------------------------------

    @contextmanager
    def _batch(self) -> Iterator[None]:
        """Group writes into one commit; nested batches join the outermost one.

        The re-entrant lock is held for the whole batch, serializing writers
        across threads (see the class docstring).
        """
        with self._lock:
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.rollback()
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.commit()

    def _commit(self) -> None:
        if self._depth == 0:
            self._conn.commit()

    def append_entries(self, entries):
        """Hold the write lock across validate-then-persist (FR-2 chain integrity)."""
        with self._batch():
            return super().append_entries(entries)

    def set_balance(self, program_id, points, *, at, note=None):
        with self._batch():
            return super().set_balance(program_id, points, at=at, note=note)

    def adjust_balance(self, program_id, delta, *, at, note=None):
        with self._batch():
            return super().adjust_balance(program_id, delta, at=at, note=note)

    def set_goal_status(self, goal_id, status):
        with self._batch():
            return super().set_goal_status(goal_id, status)

    def record_step_execution(self, step_id, entries, *, at):
        """FR-11: the ledger entries and the ``executed_at`` stamp land together."""
        with self._batch():
            return super().record_step_execution(step_id, entries, at=at)

    # -- profile -----------------------------------------------------------

    def get_profile(self) -> Profile:
        row = self._conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if row is None:
            return Profile()
        return Profile(
            id=row["id"],
            display_name=row["display_name"],
            home_city=row["home_city"],
            default_passengers=row["default_passengers"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_profile(self, profile: Profile) -> Profile:
        with self._batch():
            self._conn.execute(
                "INSERT INTO profile (id, display_name, home_city, default_passengers, "
                "created_at, updated_at) VALUES (1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name, "
                "home_city = excluded.home_city, "
                "default_passengers = excluded.default_passengers, "
                "created_at = excluded.created_at, updated_at = excluded.updated_at",
                (
                    profile.display_name,
                    profile.home_city,
                    profile.default_passengers,
                    profile.created_at,
                    profile.updated_at,
                ),
            )
        return self.get_profile()

    # -- cards -------------------------------------------------------------

    def list_cards(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT card_product_id FROM wallet_card ORDER BY card_product_id"
        ).fetchall()
        return [row["card_product_id"] for row in rows]

    def add_card(self, card_product_id: str, at: str) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO wallet_card (card_product_id, added_at) VALUES (?, ?)",
                    (card_product_id, at),
                )
            except sqlite3.IntegrityError:
                return False
            self._commit()
            return True

    def remove_card(self, card_product_id: str) -> bool:
        with self._batch():
            cursor = self._conn.execute(
                "DELETE FROM wallet_card WHERE card_product_id = ?", (card_product_id,)
            )
            return cursor.rowcount > 0

    # -- ledger ------------------------------------------------------------

    def list_ledger(
        self, program_id: str | None = None, limit: int | None = None
    ) -> list[LedgerEntry]:
        sql = "SELECT * FROM ledger_entry"
        params: list[Any] = []
        if program_id is not None:
            sql += " WHERE program_id = ?"
            params.append(program_id)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        if limit is not None:
            rows = rows[-limit:]
        return [
            LedgerEntry(
                id=row["id"],
                program_id=row["program_id"],
                delta_points=row["delta_points"],
                post_balance=row["post_balance"],
                reason=row["reason"],
                plan_step_id=row["plan_step_id"],
                note=row["note"],
                at=row["at"],
            )
            for row in rows
        ]

    def balances(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT program_id, post_balance FROM ledger_entry e WHERE id = "
            "(SELECT MAX(id) FROM ledger_entry x WHERE x.program_id = e.program_id) "
            "ORDER BY program_id"
        ).fetchall()
        return {row["program_id"]: row["post_balance"] for row in rows if row["post_balance"]}

    def _persist_entries(self, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        written: list[LedgerEntry] = []
        with self._batch():
            for entry in entries:
                cursor = self._conn.execute(
                    "INSERT INTO ledger_entry (program_id, delta_points, post_balance, "
                    "reason, plan_step_id, note, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry.program_id,
                        entry.delta_points,
                        entry.post_balance,
                        str(entry.reason),
                        entry.plan_step_id,
                        entry.note,
                        entry.at,
                    ),
                )
                written.append(entry.model_copy(update={"id": cursor.lastrowid}))
        return written

    # -- goals -------------------------------------------------------------

    def _insert_goal(self, goal: Goal) -> Goal:
        cursor = self._conn.execute(
            "INSERT INTO goal (kind, raw_text, origin_city, dest_city, cabin, round_trip, "
            "passengers, city, nights, travel_window_start, travel_window_end, book_by, "
            "cash_programs, cash_max_points, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(goal.kind),
                goal.raw_text,
                goal.origin_city,
                goal.dest_city,
                str(goal.cabin) if goal.cabin else None,
                None if goal.round_trip is None else int(goal.round_trip),
                goal.passengers,
                goal.city,
                goal.nights,
                goal.travel_window_start.isoformat() if goal.travel_window_start else None,
                goal.travel_window_end.isoformat() if goal.travel_window_end else None,
                goal.book_by.isoformat() if goal.book_by else None,
                _json(goal.cash_programs),
                _json(goal.cash_max_points),
                str(goal.status),
                goal.created_at,
            ),
        )
        self._commit()
        stored = self.get_goal(cursor.lastrowid)
        assert stored is not None
        return stored

    def add_goal(self, goal: Goal) -> Goal:
        with self._batch():
            return self._insert_goal(goal)

    def _goal_from_row(self, row: sqlite3.Row) -> Goal:
        return Goal(
            id=row["id"],
            kind=row["kind"],
            raw_text=row["raw_text"],
            origin_city=row["origin_city"],
            dest_city=row["dest_city"],
            cabin=row["cabin"],
            round_trip=None if row["round_trip"] is None else bool(row["round_trip"]),
            passengers=row["passengers"],
            city=row["city"],
            nights=row["nights"],
            travel_window_start=_date(row["travel_window_start"]),
            travel_window_end=_date(row["travel_window_end"]),
            book_by=_date(row["book_by"]),
            cash_programs=_unjson(row["cash_programs"]),
            cash_max_points=_unjson(row["cash_max_points"]),
            status=row["status"],
            created_at=row["created_at"],
        )

    def get_goal(self, goal_id: int) -> Goal | None:
        row = self._conn.execute("SELECT * FROM goal WHERE id = ?", (goal_id,)).fetchone()
        return self._goal_from_row(row) if row else None

    def list_goals(self) -> list[Goal]:
        rows = self._conn.execute("SELECT * FROM goal ORDER BY id").fetchall()
        return [self._goal_from_row(row) for row in rows]

    def _write_goal_status(self, goal_id: int, status: GoalStatus) -> Goal:
        self._conn.execute("UPDATE goal SET status = ? WHERE id = ?", (str(status), goal_id))
        self._commit()
        stored = self.get_goal(goal_id)
        assert stored is not None
        return stored

    # -- plans -------------------------------------------------------------

    def save_plan_set(self, plan_set: PlanSet) -> PlanSet:
        with self._batch():
            cursor = self._conn.execute(
                "INSERT INTO plan_set (goal_id, world_version, world_hash, today, params, "
                "expansions, verdict, recommended_plan_id, disclaimer, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    plan_set.goal_id,
                    plan_set.world_version,
                    plan_set.world_hash,
                    plan_set.today.isoformat(),
                    _json(plan_set.params.as_dict()),
                    plan_set.expansions,
                    str(plan_set.verdict),
                    plan_set.disclaimer,
                    plan_set.created_at,
                ),
            )
            set_id = cursor.lastrowid
            recommended: int | None = None
            for plan in plan_set.plans:
                plan_cursor = self._conn.execute(
                    "INSERT INTO plan (plan_set_id, rank, is_comparator, gross_value_cents, "
                    "cash_outlay_cents, points_cost_cents, net_value_cents, "
                    "cash_received_cents, realized_cpp_milli, points_spent, "
                    "feasible_in_days, signature, caveats) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        set_id,
                        plan.rank,
                        int(plan.is_comparator),
                        plan.gross_value_cents,
                        plan.cash_outlay_cents,
                        plan.points_cost_cents,
                        plan.net_value_cents,
                        plan.cash_received_cents,
                        plan.realized_cpp_milli,
                        _json(plan.points_spent),
                        plan.feasible_in_days,
                        plan.signature,
                        _json([c.model_dump(mode="json") for c in plan.caveats]),
                    ),
                )
                plan_id = plan_cursor.lastrowid
                if plan.rank == 1:
                    recommended = plan_id
                for step in plan.steps:
                    self._conn.execute(
                        "INSERT INTO plan_step (plan_id, seq, kind, hop_index, from_program, "
                        "to_program, edge_id, offer_id, cashout_id, points_sent, "
                        "points_delivered, fees_cents, eta_days, irreversible, explanation, "
                        "executed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            plan_id,
                            step.seq,
                            str(step.kind),
                            step.hop_index,
                            step.from_program,
                            step.to_program,
                            step.edge_id,
                            step.offer_id,
                            step.cashout_id,
                            step.points_sent,
                            step.points_delivered,
                            step.fees_cents,
                            step.eta_days,
                            int(step.irreversible),
                            step.explanation,
                            step.executed_at,
                        ),
                    )
            if recommended is not None and plan_set.recommended() is not None:
                self._conn.execute(
                    "UPDATE plan_set SET recommended_plan_id = ? WHERE id = ?",
                    (recommended, set_id),
                )
        stored = self.get_plan_set(set_id)
        assert stored is not None
        return stored

    def _plan_from_row(self, row: sqlite3.Row) -> Plan:
        steps = self._conn.execute(
            "SELECT * FROM plan_step WHERE plan_id = ? ORDER BY seq", (row["id"],)
        ).fetchall()
        return Plan(
            id=row["id"],
            plan_set_id=row["plan_set_id"],
            rank=row["rank"],
            is_comparator=bool(row["is_comparator"]),
            gross_value_cents=row["gross_value_cents"],
            cash_outlay_cents=row["cash_outlay_cents"],
            points_cost_cents=row["points_cost_cents"],
            net_value_cents=row["net_value_cents"],
            cash_received_cents=row["cash_received_cents"],
            realized_cpp_milli=row["realized_cpp_milli"],
            points_spent=_unjson(row["points_spent"]) or {},
            feasible_in_days=row["feasible_in_days"],
            signature=row["signature"],
            caveats=[Caveat.model_validate(c) for c in _unjson(row["caveats"]) or []],
            steps=[self._step_from_row(step) for step in steps],
        )

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> PlanStep:
        return PlanStep(
            id=row["id"],
            plan_id=row["plan_id"],
            seq=row["seq"],
            kind=row["kind"],
            hop_index=row["hop_index"],
            from_program=row["from_program"],
            to_program=row["to_program"],
            edge_id=row["edge_id"],
            offer_id=row["offer_id"],
            cashout_id=row["cashout_id"],
            points_sent=row["points_sent"],
            points_delivered=row["points_delivered"],
            fees_cents=row["fees_cents"],
            eta_days=row["eta_days"],
            irreversible=bool(row["irreversible"]),
            explanation=row["explanation"],
            executed_at=row["executed_at"],
        )

    def get_plan_set(self, plan_set_id: int) -> PlanSet | None:
        row = self._conn.execute("SELECT * FROM plan_set WHERE id = ?", (plan_set_id,)).fetchone()
        if row is None:
            return None
        plan_rows = self._conn.execute(
            "SELECT * FROM plan WHERE plan_set_id = ? ORDER BY rank", (plan_set_id,)
        ).fetchall()
        return PlanSet(
            id=row["id"],
            goal_id=row["goal_id"],
            world_version=row["world_version"],
            world_hash=row["world_hash"],
            today=date.fromisoformat(row["today"]),
            params=PlanParams.model_validate(_unjson(row["params"])),
            expansions=row["expansions"],
            verdict=row["verdict"],
            recommended_plan_id=row["recommended_plan_id"],
            disclaimer=row["disclaimer"],
            created_at=row["created_at"],
            plans=[self._plan_from_row(plan_row) for plan_row in plan_rows],
        )

    def list_plan_sets(self, goal_id: int) -> list[PlanSet]:
        rows = self._conn.execute(
            "SELECT id FROM plan_set WHERE goal_id = ? ORDER BY id", (goal_id,)
        ).fetchall()
        out = []
        for row in rows:
            plan_set = self.get_plan_set(row["id"])
            if plan_set is not None:
                out.append(plan_set)
        return out

    def get_plan(self, plan_id: int) -> Plan | None:
        row = self._conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
        return self._plan_from_row(row) if row else None

    def get_step(self, step_id: int) -> PlanStep | None:
        row = self._conn.execute("SELECT * FROM plan_step WHERE id = ?", (step_id,)).fetchone()
        return self._step_from_row(row) if row else None

    def _mark_step_executed(self, step_id: int, at: str) -> PlanStep:
        self._conn.execute("UPDATE plan_step SET executed_at = ? WHERE id = ?", (at, step_id))
        self._commit()
        stored = self.get_step(step_id)
        assert stored is not None
        return stored
