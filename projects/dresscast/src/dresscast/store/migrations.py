"""SQLite schema and forward-only migrations (DATA_MODEL.md §4).

The database is append-only by design and accumulates irreplaceable wear
history, so it carries an explicit version from day one.  Migrations may add
tables/columns and backfill; they may never drop or rewrite ``wear_logs``,
``wear_log_items``, ``recommendations`` or their children.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from dresscast.engine.models import SCHEMA_VERSION
from dresscast.errors import InvalidSchemaVersion

SCHEMA_V1 = """
CREATE TABLE schema_version (
  version    INTEGER NOT NULL,
  CHECK (version >= 1)
);
CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE garments (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL COLLATE NOCASE UNIQUE,
  category             TEXT NOT NULL,
  layer_role           TEXT NOT NULL CHECK (layer_role IN
                        ('base','mid','outer','bottom','leg_base','full_body','footwear','accessory')),
  accessory_class      TEXT CHECK (accessory_class IN
                        ('hat','gloves','scarf','umbrella','sunglasses')),
  clo                  REAL NOT NULL CHECK (clo BETWEEN 0.0 AND 1.5),
  waterproofness       INTEGER NOT NULL DEFAULT 0 CHECK (waterproofness BETWEEN 0 AND 3),
  windproofness        INTEGER NOT NULL DEFAULT 0 CHECK (windproofness BETWEEN 0 AND 2),
  formality            INTEGER NOT NULL CHECK (formality BETWEEN 1 AND 5),
  colors               TEXT NOT NULL,
  style_tags           TEXT NOT NULL DEFAULT '[]',
  occasions            TEXT NOT NULL,
  wears_before_laundry INTEGER NOT NULL CHECK (wears_before_laundry >= 1),
  wears_since_wash     INTEGER NOT NULL DEFAULT 0 CHECK (wears_since_wash >= 0),
  status               TEXT NOT NULL DEFAULT 'clean' CHECK (status IN
                        ('clean','dirty','in_laundry','retired')),
  overridden_fields    TEXT NOT NULL DEFAULT '[]',
  photo_path           TEXT,
  photo_sha256         TEXT,
  notes                TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  CHECK ((photo_path IS NULL) = (photo_sha256 IS NULL)),
  CHECK ((accessory_class IS NULL) = (layer_role <> 'accessory'))
);

CREATE TABLE attribute_suggestions (
  id              TEXT PRIMARY KEY,
  garment_id      TEXT NOT NULL REFERENCES garments(id),
  source          TEXT NOT NULL CHECK (source IN ('fixture','vision')),
  payload         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                    ('pending','accepted','rejected')),
  accepted_fields TEXT,
  created_at      TEXT NOT NULL,
  resolved_at     TEXT,
  CHECK ((status = 'pending') = (resolved_at IS NULL)),
  CHECK ((status = 'accepted') = (accepted_fields IS NOT NULL))
);

CREATE TABLE forecast_snapshots (
  id            TEXT PRIMARY KEY,
  date          TEXT NOT NULL,
  location_name TEXT NOT NULL,
  lat           REAL NOT NULL, lon REAL NOT NULL,
  timezone      TEXT NOT NULL,
  provider      TEXT NOT NULL CHECK (provider IN ('fixture','open_meteo')),
  fetched_at    TEXT NOT NULL,
  raw           TEXT
);

CREATE TABLE forecast_hours (
  snapshot_id  TEXT NOT NULL REFERENCES forecast_snapshots(id),
  seq          INTEGER NOT NULL CHECK (seq BETWEEN 0 AND 24),
  hour         INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
  temp_c       REAL NOT NULL CHECK (temp_c BETWEEN -60 AND 60),
  wind_kmh     REAL NOT NULL CHECK (wind_kmh BETWEEN 0 AND 250),
  humidity_pct REAL NOT NULL CHECK (humidity_pct BETWEEN 0 AND 100),
  precip_prob  REAL NOT NULL CHECK (precip_prob BETWEEN 0 AND 1),
  precip_mmh   REAL NOT NULL CHECK (precip_mmh >= 0),
  uv_index     REAL NOT NULL CHECK (uv_index BETWEEN 0 AND 16),
  PRIMARY KEY (snapshot_id, seq)
);

CREATE TABLE recommendations (
  id             TEXT PRIMARY KEY,
  date           TEXT NOT NULL,
  snapshot_id    TEXT NOT NULL REFERENCES forecast_snapshots(id),
  created_at     TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  seed           INTEGER NOT NULL,
  params         TEXT NOT NULL,
  wardrobe_hash  TEXT NOT NULL,
  notes          TEXT NOT NULL DEFAULT '[]',
  compromises    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE recommendation_outfits (
  id                TEXT PRIMARY KEY,
  recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
  rank              INTEGER NOT NULL CHECK (rank >= 1),
  score_total       REAL NOT NULL CHECK (score_total BETWEEN 0 AND 1),
  scores            TEXT NOT NULL,
  hour_plan         TEXT NOT NULL,
  reasoning         TEXT NOT NULL,
  accessories       TEXT NOT NULL DEFAULT '[]',
  notes             TEXT NOT NULL DEFAULT '[]',
  compromises       TEXT NOT NULL DEFAULT '[]',
  UNIQUE (recommendation_id, rank)
);

CREATE TABLE outfit_items (
  outfit_id  TEXT NOT NULL REFERENCES recommendation_outfits(id),
  slot       TEXT NOT NULL CHECK (slot IN
               ('base','mid_1','mid_2','outer','bottom','leg_base','footwear',
                'accessory_1','accessory_2','accessory_3','accessory_4')),
  garment_id TEXT NOT NULL REFERENCES garments(id),
  PRIMARY KEY (outfit_id, slot),
  UNIQUE (outfit_id, garment_id)
);

CREATE TABLE wear_logs (
  id         TEXT PRIMARY KEY,
  date       TEXT NOT NULL,
  source     TEXT NOT NULL CHECK (source IN ('recommendation','manual')),
  outfit_id  TEXT REFERENCES recommendation_outfits(id),
  created_at TEXT NOT NULL,
  CHECK ((source = 'recommendation') = (outfit_id IS NOT NULL))
);

CREATE TABLE wear_log_items (
  wear_log_id TEXT NOT NULL REFERENCES wear_logs(id) ON DELETE CASCADE,
  garment_id  TEXT NOT NULL REFERENCES garments(id),
  layer_role  TEXT NOT NULL,
  PRIMARY KEY (wear_log_id, garment_id)
);

CREATE TABLE laundry_events (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  note       TEXT
);

CREATE TABLE laundry_event_items (
  event_id   TEXT NOT NULL REFERENCES laundry_events(id),
  garment_id TEXT NOT NULL REFERENCES garments(id),
  PRIMARY KEY (event_id, garment_id)
);

CREATE INDEX idx_garments_status     ON garments(status);
CREATE INDEX idx_hours_snapshot      ON forecast_hours(snapshot_id, seq);
CREATE INDEX idx_recs_date           ON recommendations(date, created_at);
CREATE INDEX idx_wear_date           ON wear_logs(date);
CREATE INDEX idx_wear_items_garment  ON wear_log_items(garment_id);
"""


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V1)


#: Ordered migrations: ``version -> callable``.  Version 1 creates the schema.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _create_v1}


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    value = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(value[0]) if value else 0


def migrate(conn: sqlite3.Connection, *, now: datetime) -> int:
    """Bring ``conn`` up to :data:`SCHEMA_VERSION`, in one transaction."""
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise InvalidSchemaVersion(
            f"database schema version {version} is newer than this build's "
            f"{SCHEMA_VERSION}; refusing to open it",
            found=version,
            supported=SCHEMA_VERSION,
        )
    if version == SCHEMA_VERSION:
        return version
    with conn:
        for target in range(version + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[target](conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (target, now.isoformat()),
            )
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    return SCHEMA_VERSION
