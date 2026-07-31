"""SQLite Repository backend (DATA_MODEL §4).

Stdlib ``sqlite3``, WAL journalling, foreign keys ON, every write in a
transaction.  The schema below is the DDL from DATA_MODEL §4 verbatim, CHECK
constraints included — the database refuses a half-modelled run rather than
trusting the caller.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..engine.models import (
    ColumnProfile,
    ColumnType,
    Disposition,
    FileFormat,
    IssueClass,
    IssueSummary,
    ReviewItem,
    ReviewStatus,
    Revision,
    Run,
    RunStatus,
    SourceFile,
    TriggerKind,
    WatchedFolder,
)
from ..errors import DatasweepError, ItemAlreadyDecidedError, UnknownRunError
from .base import SUPPRESSING_STATUSES

SCHEMA = """
CREATE TABLE IF NOT EXISTS watched_folders (
  id          TEXT PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  recursive   INTEGER NOT NULL DEFAULT 1,
  include     TEXT NOT NULL,
  policy_path TEXT,
  output_dir  TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_files (
  id            TEXT PRIMARY KEY,
  path          TEXT NOT NULL UNIQUE,
  folder_id     TEXT REFERENCES watched_folders(id) ON DELETE SET NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id              TEXT PRIMARY KEY,
  file_id         TEXT NOT NULL REFERENCES source_files(id),
  content_sha256  TEXT NOT NULL,
  policy_hash     TEXT NOT NULL,
  policy_snapshot TEXT NOT NULL,
  engine_version  TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  status          TEXT NOT NULL CHECK (status IN
                    ('succeeded','review_pending','failed','skipped')),
  trigger_kind    TEXT NOT NULL CHECK (trigger_kind IN ('scan','manual','forced')),
  format          TEXT CHECK (format IN ('csv','tsv','xlsx','jsonl')),
  encoding        TEXT,
  dialect         TEXT,
  n_rows          INTEGER, n_cols INTEGER,
  issue_counts    TEXT NOT NULL DEFAULT '{}',
  change_counts   TEXT NOT NULL DEFAULT '{}',
  artifact_dir    TEXT,
  error           TEXT,
  CHECK ((status IN ('succeeded','review_pending')) = (artifact_dir IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_runs_identity
  ON runs(content_sha256, policy_hash, engine_version);
CREATE INDEX IF NOT EXISTS idx_runs_file ON runs(file_id, started_at);

CREATE TABLE IF NOT EXISTS column_profiles (
  run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  col_index      INTEGER NOT NULL CHECK (col_index >= 0),
  name           TEXT NOT NULL,
  original_name  TEXT,
  inferred_type  TEXT NOT NULL CHECK (inferred_type IN
                   ('bool','digits','integer','float','datetime','date','categorical','text')),
  type_coverage  REAL NOT NULL,
  non_null       INTEGER NOT NULL,
  null_count     INTEGER NOT NULL DEFAULT 0,
  distinct_count INTEGER NOT NULL,
  stats          TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, col_index)
);

CREATE TABLE IF NOT EXISTS issue_summaries (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  klass       TEXT NOT NULL CHECK (klass IN
                ('ENC','STR','WS','MISS','TYPE','DATE','CAT','DUP','OUT')),
  col_index   INTEGER,
  cell_count  INTEGER NOT NULL CHECK (cell_count >= 1),
  disposition TEXT NOT NULL CHECK (disposition IN ('fixed','proposed','reported')),
  samples     TEXT NOT NULL DEFAULT '[]',
  evidence    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_issues_run ON issue_summaries(run_id, klass);

CREATE TABLE IF NOT EXISTS review_items (
  id             TEXT NOT NULL,
  run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  rule           TEXT NOT NULL,
  col_index      INTEGER,
  description    TEXT NOT NULL,
  proposal       TEXT NOT NULL,
  affected_cells INTEGER NOT NULL,
  confidence     REAL NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','accepted','rejected')),
  decided_at     TEXT,
  PRIMARY KEY (run_id, id),
  CHECK ((status = 'pending') = (decided_at IS NULL))
);

CREATE TABLE IF NOT EXISTS revisions (
  id                TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  revision_no       INTEGER NOT NULL CHECK (revision_no >= 1),
  created_at        TEXT NOT NULL,
  accepted_item_ids TEXT NOT NULL DEFAULT '[]',
  cleaned_path      TEXT NOT NULL,
  audit_path        TEXT NOT NULL,
  UNIQUE (run_id, revision_no)
);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _loads(text: str | None, default: Any) -> Any:
    return default if text is None else json.loads(text)


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class SqliteRepository:
    """Default backend.  ``path=':memory:'`` is supported for quick tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- watched folders ---------------------------------------------------

    def add_folder(self, folder: WatchedFolder) -> WatchedFolder:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO watched_folders (id, path, recursive, include, policy_path,"
                    " output_dir, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        folder.id,
                        folder.path,
                        int(folder.recursive),
                        _dumps(folder.include),
                        folder.policy_path,
                        folder.output_dir,
                        int(folder.enabled),
                        folder.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DatasweepError(f"watched folder already exists: {folder.path}") from exc
        return folder

    def list_folders(self) -> list[WatchedFolder]:
        rows = self._conn.execute("SELECT * FROM watched_folders ORDER BY path").fetchall()
        return [self._folder(row) for row in rows]

    def get_folder(self, folder_id: str) -> WatchedFolder | None:
        row = self._conn.execute(
            "SELECT * FROM watched_folders WHERE id = ?", (folder_id,)
        ).fetchone()
        return None if row is None else self._folder(row)

    def delete_folder(self, folder_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM watched_folders WHERE id = ?", (folder_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _folder(row: sqlite3.Row) -> WatchedFolder:
        return WatchedFolder(
            id=row["id"],
            path=row["path"],
            recursive=bool(row["recursive"]),
            include=_loads(row["include"], []),
            policy_path=row["policy_path"],
            output_dir=row["output_dir"],
            enabled=bool(row["enabled"]),
            created_at=_dt(row["created_at"]),
        )

    # -- source files ------------------------------------------------------

    def upsert_source_file(
        self, *, id: str, path: str, folder_id: str | None, now: datetime
    ) -> SourceFile:
        existing = self.get_source_file(path)
        with self._conn:
            if existing is None:
                self._conn.execute(
                    "INSERT INTO source_files (id, path, folder_id, first_seen_at, last_seen_at)"
                    " VALUES (?,?,?,?,?)",
                    (id, path, folder_id, now.isoformat(), now.isoformat()),
                )
                return SourceFile(
                    id=id, path=path, folder_id=folder_id, first_seen_at=now, last_seen_at=now
                )
            new_folder = folder_id if folder_id is not None else existing.folder_id
            self._conn.execute(
                "UPDATE source_files SET last_seen_at = ?, folder_id = ? WHERE path = ?",
                (now.isoformat(), new_folder, path),
            )
        return existing.model_copy(update={"last_seen_at": now, "folder_id": new_folder})

    def get_source_file(self, path: str) -> SourceFile | None:
        row = self._conn.execute("SELECT * FROM source_files WHERE path = ?", (path,)).fetchone()
        return None if row is None else self._source_file(row)

    def get_source_file_by_id(self, file_id: str) -> SourceFile | None:
        row = self._conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone()
        return None if row is None else self._source_file(row)

    @staticmethod
    def _source_file(row: sqlite3.Row) -> SourceFile:
        return SourceFile(
            id=row["id"],
            path=row["path"],
            folder_id=row["folder_id"],
            first_seen_at=_dt(row["first_seen_at"]),
            last_seen_at=_dt(row["last_seen_at"]),
        )

    # -- runs --------------------------------------------------------------

    def add_run(self, run: Run) -> Run:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO runs (id, file_id, content_sha256, policy_hash, policy_snapshot,"
                    " engine_version, started_at, finished_at, status, trigger_kind, format,"
                    " encoding, dialect, n_rows, n_cols, issue_counts, change_counts,"
                    " artifact_dir, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    self._run_params(run),
                )
        except sqlite3.IntegrityError as exc:
            raise DatasweepError(f"run {run.id} already exists (runs are append-only)") from exc
        return run

    def finalize_run(self, run: Run) -> Run:
        row = self._conn.execute("SELECT finished_at FROM runs WHERE id = ?", (run.id,)).fetchone()
        if row is None:
            raise UnknownRunError(f"unknown run: {run.id}", run_id=run.id)
        if row["finished_at"] is not None:
            raise DatasweepError(f"run {run.id} is already finalized (append-only)")
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, format = ?, encoding = ?,"
                " dialect = ?, n_rows = ?, n_cols = ?, issue_counts = ?, change_counts = ?,"
                " artifact_dir = ?, error = ? WHERE id = ? AND finished_at IS NULL",
                (
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.status.value,
                    run.format.value if run.format else None,
                    run.encoding,
                    _dumps(run.dialect) if run.dialect is not None else None,
                    run.n_rows,
                    run.n_cols,
                    _dumps(run.issue_counts),
                    _dumps(run.change_counts),
                    run.artifact_dir,
                    run.error,
                    run.id,
                ),
            )
        return run

    def get_run(self, run_id: str) -> Run | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._run(row)

    def list_runs(self, *, path: str | None = None, status: RunStatus | None = None) -> list[Run]:
        query = "SELECT r.* FROM runs r"
        params: list[Any] = []
        clauses: list[str] = []
        if path is not None:
            query += " JOIN source_files f ON f.id = r.file_id"
            clauses.append("f.path = ?")
            params.append(path)
        if status is not None:
            clauses.append("r.status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY r.started_at, r.id"
        return [self._run(row) for row in self._conn.execute(query, params).fetchall()]

    def find_completed_run(
        self, *, content_sha256: str, policy_hash: str, engine_version: str
    ) -> Run | None:
        placeholders = ",".join("?" for _ in SUPPRESSING_STATUSES)
        statuses = sorted(status.value for status in SUPPRESSING_STATUSES)
        row = self._conn.execute(
            "SELECT * FROM runs WHERE content_sha256 = ? AND policy_hash = ?"
            f" AND engine_version = ? AND status IN ({placeholders})"
            " ORDER BY started_at LIMIT 1",
            (content_sha256, policy_hash, engine_version, *statuses),
        ).fetchone()
        return None if row is None else self._run(row)

    @staticmethod
    def _run_params(run: Run) -> tuple[Any, ...]:
        return (
            run.id,
            run.file_id,
            run.content_sha256,
            run.policy_hash,
            _dumps(run.policy_snapshot),
            run.engine_version,
            run.started_at.isoformat(),
            run.finished_at.isoformat() if run.finished_at else None,
            run.status.value,
            run.trigger.value,
            run.format.value if run.format else None,
            run.encoding,
            _dumps(run.dialect) if run.dialect is not None else None,
            run.n_rows,
            run.n_cols,
            _dumps(run.issue_counts),
            _dumps(run.change_counts),
            run.artifact_dir,
            run.error,
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"],
            file_id=row["file_id"],
            content_sha256=row["content_sha256"],
            policy_hash=row["policy_hash"],
            policy_snapshot=_loads(row["policy_snapshot"], {}),
            engine_version=row["engine_version"],
            started_at=_dt(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
            status=RunStatus(row["status"]),
            trigger=TriggerKind(row["trigger_kind"]),
            format=FileFormat(row["format"]) if row["format"] else None,
            encoding=row["encoding"],
            dialect=_loads(row["dialect"], None),
            n_rows=row["n_rows"],
            n_cols=row["n_cols"],
            issue_counts=_loads(row["issue_counts"], {}),
            change_counts=_loads(row["change_counts"], {}),
            artifact_dir=row["artifact_dir"],
            error=row["error"],
        )

    # -- profiles and rollups ---------------------------------------------

    def add_column_profiles(self, run_id: str, profiles: list[ColumnProfile]) -> None:
        self._require_run(run_id)
        with self._conn:
            self._conn.executemany(
                "INSERT INTO column_profiles (run_id, col_index, name, original_name,"
                " inferred_type, type_coverage, non_null, null_count, distinct_count, stats)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        profile.col_index,
                        profile.name,
                        profile.original_name,
                        profile.inferred_type.value,
                        profile.type_coverage,
                        profile.non_null,
                        profile.null_count,
                        profile.distinct_count,
                        _dumps(profile.stats),
                    )
                    for profile in profiles
                ],
            )

    def list_column_profiles(self, run_id: str) -> list[ColumnProfile]:
        rows = self._conn.execute(
            "SELECT * FROM column_profiles WHERE run_id = ? ORDER BY col_index", (run_id,)
        ).fetchall()
        return [
            ColumnProfile(
                run_id=row["run_id"],
                col_index=row["col_index"],
                name=row["name"],
                original_name=row["original_name"],
                inferred_type=ColumnType(row["inferred_type"]),
                type_coverage=row["type_coverage"],
                non_null=row["non_null"],
                null_count=row["null_count"],
                distinct_count=row["distinct_count"],
                stats=_loads(row["stats"], {}),
            )
            for row in rows
        ]

    def add_issue_summaries(self, summaries: list[IssueSummary]) -> None:
        for summary in summaries:
            self._require_run(summary.run_id)
        with self._conn:
            self._conn.executemany(
                "INSERT INTO issue_summaries (id, run_id, klass, col_index, cell_count,"
                " disposition, samples, evidence) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        summary.id,
                        summary.run_id,
                        summary.klass.value,
                        summary.col_index,
                        summary.cell_count,
                        summary.disposition.value,
                        _dumps(summary.samples),
                        _dumps(summary.evidence),
                    )
                    for summary in summaries
                ],
            )

    def list_issue_summaries(self, run_id: str) -> list[IssueSummary]:
        rows = self._conn.execute(
            "SELECT * FROM issue_summaries WHERE run_id = ? ORDER BY klass, col_index, id",
            (run_id,),
        ).fetchall()
        return [
            IssueSummary(
                id=row["id"],
                run_id=row["run_id"],
                klass=IssueClass(row["klass"]),
                col_index=row["col_index"],
                cell_count=row["cell_count"],
                disposition=Disposition(row["disposition"]),
                samples=_loads(row["samples"], []),
                evidence=_loads(row["evidence"], {}),
            )
            for row in rows
        ]

    # -- review queue ------------------------------------------------------

    def add_review_items(self, items: list[ReviewItem]) -> None:
        for item in items:
            self._require_run(item.run_id)
        try:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO review_items (id, run_id, rule, col_index, description,"
                    " proposal, affected_cells, confidence, status, decided_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            item.id,
                            item.run_id,
                            item.rule,
                            item.col_index,
                            item.description,
                            _dumps(item.proposal),
                            item.affected_cells,
                            item.confidence,
                            item.status.value,
                            item.decided_at.isoformat() if item.decided_at else None,
                        )
                        for item in items
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise DatasweepError(f"duplicate review item for run: {exc}") from exc

    def list_review_items(self, run_id: str) -> list[ReviewItem]:
        rows = self._conn.execute(
            "SELECT * FROM review_items WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [self._item(row) for row in rows]

    def get_review_item(self, run_id: str, item_id: str) -> ReviewItem | None:
        row = self._conn.execute(
            "SELECT * FROM review_items WHERE run_id = ? AND id = ?", (run_id, item_id)
        ).fetchone()
        return None if row is None else self._item(row)

    def decide_review_item(
        self, run_id: str, item_id: str, status: ReviewStatus, now: datetime
    ) -> ReviewItem:
        item = self.get_review_item(run_id, item_id)
        if item is None:
            raise UnknownRunError(f"unknown review item: {item_id}", item_id=item_id)
        if item.status is not ReviewStatus.PENDING:
            raise ItemAlreadyDecidedError(
                f"review item {item_id} is already {item.status.value}",
                item_id=item_id,
                status=item.status.value,
            )
        decided = item.decide(status, now)
        with self._conn:
            self._conn.execute(
                "UPDATE review_items SET status = ?, decided_at = ?"
                " WHERE run_id = ? AND id = ? AND status = 'pending'",
                (decided.status.value, now.isoformat(), run_id, item_id),
            )
        return decided

    def rejected_item_ids(self, content_sha256: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT i.id FROM review_items i JOIN runs r ON r.id = i.run_id"
            " WHERE r.content_sha256 = ? AND i.status = 'rejected'",
            (content_sha256,),
        ).fetchall()
        return {row["id"] for row in rows}

    @staticmethod
    def _item(row: sqlite3.Row) -> ReviewItem:
        return ReviewItem(
            id=row["id"],
            run_id=row["run_id"],
            rule=row["rule"],
            col_index=row["col_index"],
            description=row["description"],
            proposal=_loads(row["proposal"], {}),
            affected_cells=row["affected_cells"],
            confidence=row["confidence"],
            status=ReviewStatus(row["status"]),
            decided_at=_dt(row["decided_at"]),
        )

    # -- revisions ---------------------------------------------------------

    def add_revision(self, revision: Revision) -> Revision:
        self._require_run(revision.run_id)
        existing = self.list_revisions(revision.run_id)
        expected = len(existing) + 1
        if revision.revision_no != expected:
            raise DatasweepError(
                f"revision numbers must be dense from 1: expected {expected}, "
                f"got {revision.revision_no}"
            )
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO revisions (id, run_id, revision_no, created_at,"
                    " accepted_item_ids, cleaned_path, audit_path) VALUES (?,?,?,?,?,?,?)",
                    (
                        revision.id,
                        revision.run_id,
                        revision.revision_no,
                        revision.created_at.isoformat(),
                        _dumps(revision.accepted_item_ids),
                        revision.cleaned_path,
                        revision.audit_path,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DatasweepError(f"duplicate revision: {exc}") from exc
        return revision

    def list_revisions(self, run_id: str) -> list[Revision]:
        rows = self._conn.execute(
            "SELECT * FROM revisions WHERE run_id = ? ORDER BY revision_no", (run_id,)
        ).fetchall()
        return [
            Revision(
                id=row["id"],
                run_id=row["run_id"],
                revision_no=row["revision_no"],
                created_at=_dt(row["created_at"]),
                accepted_item_ids=_loads(row["accepted_item_ids"], []),
                cleaned_path=row["cleaned_path"],
                audit_path=row["audit_path"],
            )
            for row in rows
        ]

    # -- helpers -----------------------------------------------------------

    def _require_run(self, run_id: str) -> None:
        row = self._conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise UnknownRunError(f"unknown run: {run_id}", run_id=run_id)

    def close(self) -> None:
        self._conn.close()
