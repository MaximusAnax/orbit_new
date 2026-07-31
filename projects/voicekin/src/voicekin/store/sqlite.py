"""SQLite repository (stdlib ``sqlite3``).

One implementation serves both backends: production opens a file under the data
home, tests open ``":memory:"`` (SCOPE's budget cut — one Repository, not two).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from voicekin.engine.audit import build_record
from voicekin.models import (
    AuditEvent,
    AuditRecord,
    ConsentRecord,
    Delivery,
    DeviceTarget,
    EnrollmentSample,
    Instance,
    Utterance,
    VoiceProfile,
)
from voicekin.store.base import SCHEMA_VERSION

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instance (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    operator_name  TEXT    NOT NULL,
    data_home      TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_profile (
    id                     TEXT PRIMARY KEY,
    display_name           TEXT    NOT NULL,
    relationship           TEXT    NOT NULL,
    status                 TEXT    NOT NULL,
    enabled                INTEGER NOT NULL,
    embedder_id            TEXT,
    centroid               TEXT,
    enrollment_fingerprint TEXT,
    voice_params           TEXT,
    enrolled_at            TEXT,
    next_sample_index      INTEGER NOT NULL,
    next_draft_index       INTEGER NOT NULL,
    next_attempt_index     INTEGER NOT NULL,
    created_at             TEXT    NOT NULL,
    updated_at             TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollment_sample (
    id            TEXT PRIMARY KEY,
    profile_id    TEXT NOT NULL REFERENCES voice_profile(id),
    sample_index  INTEGER NOT NULL,
    path          TEXT,
    sha256        TEXT NOT NULL,
    duration_s    REAL NOT NULL,
    snr_db        REAL NOT NULL,
    voiced_ratio  REAL NOT NULL,
    embedding     TEXT,
    status        TEXT NOT NULL,
    reject_reason TEXT,
    added_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_accepted_audio
    ON enrollment_sample (profile_id, sha256) WHERE status = 'accepted';
CREATE INDEX IF NOT EXISTS ix_sample_profile ON enrollment_sample (profile_id, sample_index);

CREATE TABLE IF NOT EXISTS consent_record (
    id                     TEXT PRIMARY KEY,
    profile_id             TEXT NOT NULL REFERENCES voice_profile(id),
    draft_index            INTEGER NOT NULL,
    status                 TEXT NOT NULL,
    scope_contexts         TEXT NOT NULL,
    expires_at             TEXT,
    nonce_seed             INTEGER NOT NULL,
    nonce                  TEXT NOT NULL,
    statement_text         TEXT NOT NULL,
    audio_path             TEXT,
    audio_sha256           TEXT,
    similarity             REAL,
    threshold              REAL,
    embedder_id            TEXT,
    enrollment_fingerprint TEXT,
    reject_reason          TEXT,
    drafted_at             TEXT NOT NULL,
    decided_at             TEXT,
    revoked_at             TEXT,
    revocation_reason      TEXT
);
CREATE INDEX IF NOT EXISTS ix_consent_profile
    ON consent_record (profile_id, drafted_at, draft_index);

CREATE TABLE IF NOT EXISTS utterance (
    id                   TEXT PRIMARY KEY,
    profile_id           TEXT NOT NULL REFERENCES voice_profile(id),
    attempt_index        INTEGER NOT NULL,
    consent_id           TEXT REFERENCES consent_record(id),
    text                 TEXT NOT NULL,
    text_raw             TEXT NOT NULL,
    context              TEXT NOT NULL,
    status               TEXT NOT NULL,
    refusal_reason       TEXT,
    synth_id             TEXT,
    seed                 INTEGER,
    next_delivery_index  INTEGER NOT NULL,
    output_path          TEXT,
    output_sha256        TEXT,
    duration_s           REAL,
    requested_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_utterance_profile ON utterance (profile_id, attempt_index);
CREATE INDEX IF NOT EXISTS ix_utterance_output ON utterance (output_sha256);

CREATE TABLE IF NOT EXISTS delivery (
    id             TEXT PRIMARY KEY,
    utterance_id   TEXT NOT NULL REFERENCES utterance(id),
    delivery_index INTEGER NOT NULL,
    target_id      TEXT NOT NULL REFERENCES device_target(id),
    status         TEXT NOT NULL,
    refusal_reason TEXT,
    detail         TEXT NOT NULL,
    requested_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_delivery_utterance ON delivery (utterance_id, delivery_index);

CREATE TABLE IF NOT EXISTS device_target (
    id         TEXT PRIMARY KEY,
    kind       TEXT    NOT NULL,
    config     TEXT    NOT NULL,
    enabled    INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_record (
    seq          INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    event        TEXT NOT NULL,
    profile_id   TEXT,
    consent_id   TEXT,
    utterance_id TEXT,
    detail       TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    record_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_utterance ON audit_record (utterance_id);
"""

_PROFILE_COLUMNS = (
    "id",
    "display_name",
    "relationship",
    "status",
    "enabled",
    "embedder_id",
    "centroid",
    "enrollment_fingerprint",
    "voice_params",
    "enrolled_at",
    "next_sample_index",
    "next_draft_index",
    "next_attempt_index",
    "created_at",
    "updated_at",
)
_SAMPLE_COLUMNS = (
    "id",
    "profile_id",
    "sample_index",
    "path",
    "sha256",
    "duration_s",
    "snr_db",
    "voiced_ratio",
    "embedding",
    "status",
    "reject_reason",
    "added_at",
)
_CONSENT_COLUMNS = (
    "id",
    "profile_id",
    "draft_index",
    "status",
    "scope_contexts",
    "expires_at",
    "nonce_seed",
    "nonce",
    "statement_text",
    "audio_path",
    "audio_sha256",
    "similarity",
    "threshold",
    "embedder_id",
    "enrollment_fingerprint",
    "reject_reason",
    "drafted_at",
    "decided_at",
    "revoked_at",
    "revocation_reason",
)
_UTTERANCE_COLUMNS = (
    "id",
    "profile_id",
    "attempt_index",
    "consent_id",
    "text",
    "text_raw",
    "context",
    "status",
    "refusal_reason",
    "synth_id",
    "seed",
    "next_delivery_index",
    "output_path",
    "output_sha256",
    "duration_s",
    "requested_at",
)
_DELIVERY_COLUMNS = (
    "id",
    "utterance_id",
    "delivery_index",
    "target_id",
    "status",
    "refusal_reason",
    "detail",
    "requested_at",
)
_TARGET_COLUMNS = ("id", "kind", "config", "enabled", "created_at")

_JSON_COLUMNS = frozenset(
    {"centroid", "voice_params", "embedding", "scope_contexts", "detail", "config"}
)


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, sort_keys=True)


def _load(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class SQLiteRepository:
    """The single Repository implementation (file-backed or ``":memory:"``)."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._depth = 0

    @classmethod
    def in_memory(cls) -> SQLiteRepository:
        """The test backend: same class, transient database."""
        repository = cls(":memory:")
        repository.initialize()
        return repository

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def initialize(self) -> None:
        self._connection.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self._connection.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Escape hatch for maintenance tooling; services use the typed API."""
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomic unit of work. Nested uses join the outermost transaction."""
        if self._depth > 0:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._connection.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")
        finally:
            self._depth = 0

    # ------------------------------------------------------------------ #
    # Generic helpers
    # ------------------------------------------------------------------ #

    def _upsert(self, table: str, columns: Sequence[str], values: Sequence[Any]) -> None:
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != "id")
        sql = (
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        self._connection.execute(sql, tuple(values))

    @staticmethod
    def _row_to_fields(row: sqlite3.Row) -> dict[str, Any]:
        fields = dict(row)
        for column in _JSON_COLUMNS & fields.keys():
            fields[column] = _load(fields[column])
        return fields

    def _model_values(self, model: Any, columns: Sequence[str]) -> list[Any]:
        payload = model.model_dump()
        values: list[Any] = []
        for column in columns:
            value = payload[column]
            if column in _JSON_COLUMNS:
                values.append(_dump(value))
            elif isinstance(value, bool):
                values.append(int(value))
            else:
                values.append(value)
        return values

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._connection.execute(sql, tuple(params)).fetchone()

    def _query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._connection.execute(sql, tuple(params)).fetchall())

    # ------------------------------------------------------------------ #
    # Instance
    # ------------------------------------------------------------------ #

    def get_instance(self) -> Instance | None:
        row = self._query_one("SELECT * FROM instance WHERE id = 1")
        return None if row is None else Instance(**dict(row))

    def put_instance(self, instance: Instance) -> None:
        if instance.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"instance schema_version {instance.schema_version} != {SCHEMA_VERSION}"
            )
        self._upsert(
            "instance",
            ("id", "operator_name", "data_home", "schema_version", "created_at"),
            (
                1,
                instance.operator_name,
                instance.data_home,
                instance.schema_version,
                instance.created_at,
            ),
        )

    # ------------------------------------------------------------------ #
    # Profiles
    # ------------------------------------------------------------------ #

    def get_profile(self, profile_id: str) -> VoiceProfile | None:
        row = self._query_one("SELECT * FROM voice_profile WHERE id = ?", (profile_id,))
        return None if row is None else VoiceProfile(**self._row_to_fields(row))

    def list_profiles(self) -> list[VoiceProfile]:
        rows = self._query_all("SELECT * FROM voice_profile ORDER BY id")
        return [VoiceProfile(**self._row_to_fields(r)) for r in rows]

    def save_profile(self, profile: VoiceProfile) -> None:
        self._upsert(
            "voice_profile", _PROFILE_COLUMNS, self._model_values(profile, _PROFILE_COLUMNS)
        )

    # ------------------------------------------------------------------ #
    # Enrollment samples
    # ------------------------------------------------------------------ #

    def save_sample(self, sample: EnrollmentSample) -> None:
        self._upsert(
            "enrollment_sample", _SAMPLE_COLUMNS, self._model_values(sample, _SAMPLE_COLUMNS)
        )

    def get_sample(self, sample_id: str) -> EnrollmentSample | None:
        row = self._query_one("SELECT * FROM enrollment_sample WHERE id = ?", (sample_id,))
        return None if row is None else EnrollmentSample(**self._row_to_fields(row))

    def list_samples(self, profile_id: str) -> list[EnrollmentSample]:
        rows = self._query_all(
            "SELECT * FROM enrollment_sample WHERE profile_id = ? ORDER BY sample_index",
            (profile_id,),
        )
        return [EnrollmentSample(**self._row_to_fields(r)) for r in rows]

    def delete_sample(self, sample_id: str) -> None:
        self._connection.execute("DELETE FROM enrollment_sample WHERE id = ?", (sample_id,))

    # ------------------------------------------------------------------ #
    # Consent records
    # ------------------------------------------------------------------ #

    def save_consent(self, consent: ConsentRecord) -> None:
        self._upsert(
            "consent_record", _CONSENT_COLUMNS, self._model_values(consent, _CONSENT_COLUMNS)
        )

    def get_consent(self, consent_id: str) -> ConsentRecord | None:
        row = self._query_one("SELECT * FROM consent_record WHERE id = ?", (consent_id,))
        return None if row is None else ConsentRecord(**self._row_to_fields(row))

    def list_consents(self, profile_id: str) -> list[ConsentRecord]:
        rows = self._query_all(
            "SELECT * FROM consent_record WHERE profile_id = ? ORDER BY drafted_at, draft_index",
            (profile_id,),
        )
        return [ConsentRecord(**self._row_to_fields(r)) for r in rows]

    def delete_consent(self, consent_id: str) -> None:
        self._connection.execute("DELETE FROM consent_record WHERE id = ?", (consent_id,))

    # ------------------------------------------------------------------ #
    # Utterances
    # ------------------------------------------------------------------ #

    def save_utterance(self, utterance: Utterance) -> None:
        self._upsert(
            "utterance", _UTTERANCE_COLUMNS, self._model_values(utterance, _UTTERANCE_COLUMNS)
        )

    def get_utterance(self, utterance_id: str) -> Utterance | None:
        row = self._query_one("SELECT * FROM utterance WHERE id = ?", (utterance_id,))
        return None if row is None else Utterance(**self._row_to_fields(row))

    def list_utterances(self, profile_id: str) -> list[Utterance]:
        rows = self._query_all(
            "SELECT * FROM utterance WHERE profile_id = ? ORDER BY attempt_index", (profile_id,)
        )
        return [Utterance(**self._row_to_fields(r)) for r in rows]

    def find_utterance_by_output_sha256(self, output_sha256: str) -> Utterance | None:
        row = self._query_one(
            "SELECT * FROM utterance WHERE output_sha256 = ? ORDER BY requested_at, attempt_index",
            (output_sha256,),
        )
        return None if row is None else Utterance(**self._row_to_fields(row))

    # ------------------------------------------------------------------ #
    # Deliveries
    # ------------------------------------------------------------------ #

    def save_delivery(self, delivery: Delivery) -> None:
        self._upsert("delivery", _DELIVERY_COLUMNS, self._model_values(delivery, _DELIVERY_COLUMNS))

    def list_deliveries(self, utterance_id: str) -> list[Delivery]:
        rows = self._query_all(
            "SELECT * FROM delivery WHERE utterance_id = ? ORDER BY delivery_index", (utterance_id,)
        )
        return [Delivery(**self._row_to_fields(r)) for r in rows]

    def list_profile_deliveries(self, profile_id: str) -> list[Delivery]:
        rows = self._query_all(
            "SELECT d.* FROM delivery d JOIN utterance u ON u.id = d.utterance_id "
            "WHERE u.profile_id = ? ORDER BY d.requested_at, d.delivery_index",
            (profile_id,),
        )
        return [Delivery(**self._row_to_fields(r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Device targets
    # ------------------------------------------------------------------ #

    def save_target(self, target: DeviceTarget) -> None:
        self._upsert("device_target", _TARGET_COLUMNS, self._model_values(target, _TARGET_COLUMNS))

    def get_target(self, target_id: str) -> DeviceTarget | None:
        row = self._query_one("SELECT * FROM device_target WHERE id = ?", (target_id,))
        return None if row is None else DeviceTarget(**self._row_to_fields(row))

    def list_targets(self) -> list[DeviceTarget]:
        rows = self._query_all("SELECT * FROM device_target ORDER BY id")
        return [DeviceTarget(**self._row_to_fields(r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Audit chain — append and read only (FR-12)
    # ------------------------------------------------------------------ #

    def append_audit(
        self,
        *,
        ts: str,
        event: AuditEvent,
        profile_id: str | None = None,
        consent_id: str | None = None,
        utterance_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> AuditRecord:
        """Link one record onto the chain head, inside the caller's transaction."""
        record = build_record(
            self.audit_head(),
            ts=ts,
            event=event,
            profile_id=profile_id,
            consent_id=consent_id,
            utterance_id=utterance_id,
            detail=detail,
        )
        self._connection.execute(
            "INSERT INTO audit_record "
            "(seq, ts, event, profile_id, consent_id, utterance_id, detail, prev_hash, record_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.seq,
                record.ts,
                str(record.event),
                record.profile_id,
                record.consent_id,
                record.utterance_id,
                json.dumps(record.detail, sort_keys=True),
                record.prev_hash,
                record.record_hash,
            ),
        )
        return record

    @staticmethod
    def _audit_from_row(row: sqlite3.Row) -> AuditRecord:
        fields = dict(row)
        fields["detail"] = _load(fields["detail"])
        return AuditRecord(**fields)

    def audit_head(self) -> AuditRecord | None:
        row = self._query_one("SELECT * FROM audit_record ORDER BY seq DESC LIMIT 1")
        return None if row is None else self._audit_from_row(row)

    def list_audit(self, since_seq: int = 0) -> list[AuditRecord]:
        rows = self._query_all(
            "SELECT * FROM audit_record WHERE seq > ? ORDER BY seq", (since_seq,)
        )
        return [self._audit_from_row(r) for r in rows]

    def iter_audit(self) -> Iterator[AuditRecord]:
        """Stream the whole chain in order — what ``audit verify`` walks."""
        for row in self._connection.execute("SELECT * FROM audit_record ORDER BY seq"):
            yield self._audit_from_row(row)

    def list_audit_for_utterance(self, utterance_id: str) -> list[AuditRecord]:
        rows = self._query_all(
            "SELECT * FROM audit_record WHERE utterance_id = ? ORDER BY seq", (utterance_id,)
        )
        return [self._audit_from_row(r) for r in rows]


__all__ = ["SCHEMA_SQL", "SQLiteRepository"]
