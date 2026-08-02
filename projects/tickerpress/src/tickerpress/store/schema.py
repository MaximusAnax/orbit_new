"""SQLite DDL (DATA_MODEL.md §4), verbatim.

The exactly-once contract lives here, not in application code: the partial
unique index over counted delivery items makes a double delivery an
``IntegrityError`` instead of a code-review hope. Because the in-memory backend
is ``SQLiteRepository(":memory:")`` rather than a dict store, that constraint is
present in every test configuration.
"""

from __future__ import annotations

__all__ = ["PRAGMAS", "SCHEMA_SQL", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1

PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
  ticker              TEXT PRIMARY KEY CHECK (ticker GLOB '[A-Z]*'),
  name                TEXT NOT NULL,
  mode                TEXT NOT NULL DEFAULT 'digest'
                        CHECK (mode IN ('digest','alert','both','mute')),
  min_relevance       INTEGER NOT NULL DEFAULT 20 CHECK (min_relevance BETWEEN 0 AND 100),
  alert_min_relevance INTEGER NOT NULL DEFAULT 60 CHECK (alert_min_relevance BETWEEN 0 AND 100),
  context_terms       TEXT NOT NULL DEFAULT '[]',
  anti_terms          TEXT NOT NULL DEFAULT '[]',
  created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
  id             INTEGER PRIMARY KEY,
  company_ticker TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  text           TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN
                   ('legal_name','short_name','ticker_symbol','cashtag','nickname')),
  strength       TEXT NOT NULL CHECK (strength IN ('strong','weak')),
  prior          REAL NOT NULL DEFAULT 0.0 CHECK (prior BETWEEN 0.0 AND 0.3),
  generated      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  UNIQUE (company_ticker, text)
);
-- Alias-surface uniqueness (FR-1, docs/REVIEW.md D20): name-like kinds are
-- case-insensitive matchers, so their surfaces collide case-insensitively;
-- symbol kinds (ticker_symbol/cashtag) are matched case-exactly by FR-5 and
-- collide per-kind, letting ticker `META` coexist with short_name `Meta`.
CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_name_surface
  ON aliases(company_ticker, text COLLATE NOCASE)
  WHERE kind IN ('legal_name','short_name','nickname');
CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_symbol_surface
  ON aliases(company_ticker, kind, text COLLATE NOCASE)
  WHERE kind IN ('ticker_symbol','cashtag');

CREATE TABLE IF NOT EXISTS feeds (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL UNIQUE,
  url            TEXT NOT NULL UNIQUE,
  enabled        INTEGER NOT NULL DEFAULT 1,
  etag           TEXT,
  last_modified  TEXT,
  last_polled_at TEXT,
  last_status    TEXT CHECK (last_status IN ('ok','not_modified','error')),
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_runs (
  id                INTEGER PRIMARY KEY,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  status            TEXT NOT NULL CHECK (status IN ('succeeded','partial','failed')),
  feed_results      TEXT NOT NULL DEFAULT '[]',
  articles_new      INTEGER NOT NULL DEFAULT 0,
  candidates_total  INTEGER NOT NULL DEFAULT 0,
  mentions_accepted INTEGER NOT NULL DEFAULT 0,
  stories_new       INTEGER NOT NULL DEFAULT 0,
  alerts_sent       INTEGER NOT NULL DEFAULT 0,
  engine_version    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
  id                        INTEGER PRIMARY KEY,
  created_at                TEXT NOT NULL,
  first_published_at        TEXT NOT NULL,
  representative_article_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
  id                  INTEGER PRIMARY KEY,
  feed_id             INTEGER NOT NULL REFERENCES feeds(id),
  item_guid           TEXT NOT NULL,
  url                 TEXT NOT NULL,
  canonical_url       TEXT NOT NULL,
  title               TEXT NOT NULL,
  summary             TEXT NOT NULL DEFAULT '',
  content             TEXT,
  published_at        TEXT NOT NULL,
  published_source    TEXT NOT NULL CHECK (published_source IN ('feed','fallback')),
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  content_sha256      TEXT NOT NULL,
  token_count         INTEGER NOT NULL,
  content_token_count INTEGER NOT NULL DEFAULT 0,
  story_id            INTEGER NOT NULL REFERENCES stories(id),
  dedup_similarity    REAL CHECK (dedup_similarity BETWEEN 0.0 AND 1.0),
  UNIQUE (feed_id, item_guid),
  CHECK ((content IS NULL OR content = '') = (content_token_count = 0))
);
CREATE INDEX IF NOT EXISTS idx_articles_story     ON articles(story_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_canonical ON articles(canonical_url);

CREATE TABLE IF NOT EXISTS mentions (
  id             INTEGER PRIMARY KEY,
  article_id     INTEGER NOT NULL REFERENCES articles(id),
  company_ticker TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  alias_id       INTEGER REFERENCES aliases(id) ON DELETE SET NULL,
  field          TEXT NOT NULL CHECK (field IN ('title','summary','content')),
  char_start     INTEGER NOT NULL CHECK (char_start >= 0),
  char_end       INTEGER NOT NULL CHECK (char_end > char_start),
  surface        TEXT NOT NULL,
  matched_via    TEXT NOT NULL CHECK (matched_via IN
                   ('alias','cashtag','exchange_qualified')),
  strength       TEXT NOT NULL CHECK (strength IN ('strong','weak')),
  features       TEXT NOT NULL DEFAULT '{}',
  score          REAL NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
  threshold      REAL NOT NULL,
  accepted       INTEGER NOT NULL,
  engine_version TEXT NOT NULL,
  CHECK (accepted = (score >= threshold))
);
CREATE INDEX IF NOT EXISTS idx_mentions_article ON mentions(article_id, company_ticker);
CREATE INDEX IF NOT EXISTS idx_mentions_company ON mentions(company_ticker, accepted);

CREATE TABLE IF NOT EXISTS appearances (
  article_id     INTEGER NOT NULL REFERENCES articles(id),
  company_ticker TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  mention_count  INTEGER NOT NULL CHECK (mention_count >= 1),
  title_hit      INTEGER NOT NULL,
  lede_hit       INTEGER NOT NULL,
  relevance      INTEGER NOT NULL CHECK (relevance BETWEEN 0 AND 100),
  PRIMARY KEY (article_id, company_ticker)
);
CREATE INDEX IF NOT EXISTS idx_appearances_company ON appearances(company_ticker, relevance);

CREATE TABLE IF NOT EXISTS deliveries (
  id         INTEGER PRIMARY KEY,
  channel    TEXT NOT NULL CHECK (channel IN ('console','file','email','webhook')),
  kind       TEXT NOT NULL CHECK (kind IN ('digest','alert')),
  created_at TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('composed','sent','failed')),
  subject    TEXT NOT NULL,
  body_text  TEXT NOT NULL,
  error      TEXT,
  CHECK ((status = 'failed') = (error IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS delivery_items (
  id             INTEGER PRIMARY KEY,
  delivery_id    INTEGER NOT NULL REFERENCES deliveries(id),
  channel        TEXT NOT NULL,
  company_ticker TEXT NOT NULL,
  story_id       INTEGER NOT NULL REFERENCES stories(id),
  article_id     INTEGER NOT NULL REFERENCES articles(id),
  relevance      INTEGER NOT NULL,
  counted        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_exactly_once
  ON delivery_items(channel, company_ticker, story_id) WHERE counted = 1;
"""
