"""SQLite Repository backend (stdlib ``sqlite3``), the default for real use.

Schema is DATA_MODEL.md's DDL verbatim.  Search uses FTS5 with the porter
tokenizer and ``bm25()`` ranking where the interpreter's SQLite provides it,
and falls back to FR-12's documented token scan where it does not — same
result set, different (documented, deterministic) ordering.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

from almanac.engine.normalize import stemmed_tokens, tokenize
from almanac.models import (
    AttributionFlag,
    Collection,
    Entry,
    EntryKind,
    EntryStatus,
    EntryTheme,
    Grade,
    LexiconTerm,
    MisattributionRecord,
    PromptTemplate,
    Reflection,
    SchedulerState,
    SelectPool,
    Surfacing,
    SurfacingKind,
    Tag,
    Theme,
)
from almanac.store.repository import DuplicateError, Repository, token_scan

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('quote','idea')),
  text TEXT NOT NULL, normalized_hash TEXT NOT NULL,
  author TEXT, source TEXT, url TEXT, note TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  captured_on TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_entries_hash ON entries(normalized_hash);
CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status, pinned);

CREATE TABLE IF NOT EXISTS tags (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS entry_tags (entry_id TEXT NOT NULL REFERENCES entries(id),
  tag_id TEXT NOT NULL REFERENCES tags(id), PRIMARY KEY (entry_id, tag_id));
CREATE TABLE IF NOT EXISTS entry_themes (entry_id TEXT NOT NULL REFERENCES entries(id),
  theme_id TEXT NOT NULL REFERENCES themes(id),
  source TEXT NOT NULL CHECK (source IN ('user','suggested')),
  PRIMARY KEY (entry_id, theme_id));

CREATE TABLE IF NOT EXISTS surfacings (
  id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES entries(id),
  on_date TEXT NOT NULL, slot INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('daily','extra')),
  select_pool TEXT NOT NULL CHECK (select_pool IN
    ('pinned_rescue','forced_novelty','novelty','review',
     'novelty_only','review_only','not_due','relaxed','extra')),
  prompt_template_id TEXT NOT NULL, prompt_kind TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  personalized INTEGER NOT NULL DEFAULT 0,
  personalize_fell_back INTEGER NOT NULL DEFAULT 0,
  relaxed_cooldown INTEGER NOT NULL DEFAULT 0,
  prompt_recency_relaxed INTEGER NOT NULL DEFAULT 0,
  filter_theme_id TEXT, filter_collection_id TEXT,
  scheduler_version TEXT NOT NULL, seed INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (on_date, entry_id),
  CHECK ((kind = 'extra') = (select_pool = 'extra')),
  CHECK ((relaxed_cooldown = 1) = (select_pool = 'relaxed')));
CREATE UNIQUE INDEX IF NOT EXISTS idx_surf_daily_slot ON surfacings(on_date, slot)
  WHERE kind = 'daily';
CREATE INDEX IF NOT EXISTS idx_surf_entry ON surfacings(entry_id, on_date);
CREATE INDEX IF NOT EXISTS idx_surf_contested ON surfacings(on_date, slot)
  WHERE kind = 'daily' AND select_pool IN ('novelty','review');

CREATE TABLE IF NOT EXISTS reflections (
  id TEXT PRIMARY KEY,
  surfacing_id TEXT NOT NULL UNIQUE REFERENCES surfacings(id),
  entry_id TEXT NOT NULL REFERENCES entries(id),
  grade TEXT NOT NULL CHECK (grade IN ('applied','resonated','flat')),
  text TEXT, logged_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS scheduler_state (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id),
  exposure_count INTEGER NOT NULL, last_surfaced_on TEXT,
  interval_days INTEGER NOT NULL, flat_streak INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS collections (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  description TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_entries (
  collection_id TEXT NOT NULL REFERENCES collections(id),
  entry_id TEXT NOT NULL REFERENCES entries(id), position INTEGER NOT NULL,
  PRIMARY KEY (collection_id, entry_id),
  UNIQUE (collection_id, position));

CREATE TABLE IF NOT EXISTS attribution_flags (id TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL REFERENCES entries(id),
  misattribution_id TEXT, verdict TEXT NOT NULL, note TEXT NOT NULL,
  reference_url TEXT, checked_at TEXT NOT NULL,
  UNIQUE (entry_id, misattribution_id));

CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS themes (id TEXT PRIMARY KEY, name TEXT NOT NULL,
  description TEXT NOT NULL, lexicon_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompt_templates (id TEXT PRIMARY KEY, theme_id TEXT NOT NULL,
  kind TEXT NOT NULL, template TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS misattributions (id TEXT PRIMARY KEY, pattern TEXT NOT NULL,
  claimed_authors_json TEXT NOT NULL, verdict TEXT NOT NULL,
  likely_origin TEXT NOT NULL, note TEXT NOT NULL, reference_url TEXT NOT NULL);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  text, author, source, note, content='entries', content_rowid='rowid',
  tokenize='porter unicode61');
"""

_ENTRY_COLUMNS = (
    "id, kind, text, normalized_hash, author, source, url, note, pinned, status, "
    "captured_on, created_at, updated_at"
)
_SURFACING_COLUMNS = (
    "id, entry_id, on_date, slot, kind, select_pool, prompt_template_id, prompt_kind, "
    "prompt_text, personalized, personalize_fell_back, relaxed_cooldown, "
    "prompt_recency_relaxed, filter_theme_id, filter_collection_id, scheduler_version, "
    "seed, created_at"
)


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        kind=EntryKind(row["kind"]),
        text=row["text"],
        normalized_hash=row["normalized_hash"],
        author=row["author"],
        source=row["source"],
        url=row["url"],
        note=row["note"],
        pinned=bool(row["pinned"]),
        status=EntryStatus(row["status"]),
        captured_on=dt.date.fromisoformat(row["captured_on"]),
        created_at=dt.datetime.fromisoformat(row["created_at"]),
        updated_at=dt.datetime.fromisoformat(row["updated_at"]),
    )


def _surfacing(row: sqlite3.Row) -> Surfacing:
    return Surfacing(
        id=row["id"],
        entry_id=row["entry_id"],
        on_date=dt.date.fromisoformat(row["on_date"]),
        slot=row["slot"],
        kind=SurfacingKind(row["kind"]),
        select_pool=SelectPool(row["select_pool"]),
        prompt_template_id=row["prompt_template_id"],
        prompt_kind=row["prompt_kind"],
        prompt_text=row["prompt_text"],
        personalized=bool(row["personalized"]),
        personalize_fell_back=bool(row["personalize_fell_back"]),
        relaxed_cooldown=bool(row["relaxed_cooldown"]),
        prompt_recency_relaxed=bool(row["prompt_recency_relaxed"]),
        filter_theme_id=row["filter_theme_id"],
        filter_collection_id=row["filter_collection_id"],
        scheduler_version=row["scheduler_version"],
        seed=row["seed"],
        created_at=dt.datetime.fromisoformat(row["created_at"]),
    )


class SqliteRepository(Repository):
    """SQLite-backed storage. ``path=":memory:"`` gives an ephemeral database.

    Thread-safety: FastAPI runs sync dependencies and sync handlers on anyio
    threadpool workers, so the connection this class holds is used from threads
    other than the one that opened it.  ``check_same_thread`` is therefore
    disabled — safe because CPython's ``sqlite3.threadsafety == 3`` serializes
    the C level — and a re-entrant lock is held across every write batch and
    every read-modify-write composite so transactions cannot interleave.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self.fts_available = self._try_create_fts()
        self._conn.commit()

    # --- infrastructure -----------------------------------------------------
    def _try_create_fts(self) -> bool:
        try:
            self._conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError:
            return False
        return True

    def close(self) -> None:
        self._conn.close()

    def _fts_delete(self, entry_id: str) -> None:
        if not self.fts_available:
            return
        row = self._conn.execute(
            "SELECT rowid, text, author, source, note FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return
        self._conn.execute(
            "INSERT INTO entries_fts(entries_fts, rowid, text, author, source, note) "
            "VALUES ('delete', ?, ?, ?, ?, ?)",
            (row["rowid"], row["text"], row["author"], row["source"], row["note"]),
        )

    def _fts_insert(self, entry_id: str) -> None:
        if not self.fts_available:
            return
        row = self._conn.execute(
            "SELECT rowid, text, author, source, note FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return
        self._conn.execute(
            "INSERT INTO entries_fts(rowid, text, author, source, note) VALUES (?, ?, ?, ?, ?)",
            (row["rowid"], row["text"], row["author"], row["source"], row["note"]),
        )

    # --- committed datasets -------------------------------------------------
    def load_datasets(
        self,
        themes: Sequence[Theme],
        templates: Sequence[PromptTemplate],
        misattributions: Sequence[MisattributionRecord],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM prompt_templates")
            self._conn.execute("DELETE FROM misattributions")
            self._conn.executemany(
                "INSERT OR REPLACE INTO themes(id, name, description, lexicon_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        theme.id,
                        theme.name,
                        theme.description,
                        json.dumps([term.model_dump() for term in theme.lexicon]),
                    )
                    for theme in themes
                ],
            )
            self._conn.executemany(
                "INSERT INTO prompt_templates(id, theme_id, kind, template) VALUES (?, ?, ?, ?)",
                [(t.id, t.theme_id, t.kind.value, t.template) for t in templates],
            )
            self._conn.executemany(
                "INSERT INTO misattributions(id, pattern, claimed_authors_json, verdict, "
                "likely_origin, note, reference_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.id,
                        r.pattern,
                        json.dumps(r.claimed_authors),
                        r.verdict.value,
                        r.likely_origin,
                        r.note,
                        r.reference_url,
                    )
                    for r in misattributions
                ],
            )

    def list_themes(self) -> list[Theme]:
        rows = self._conn.execute(
            "SELECT id, name, description, lexicon_json FROM themes ORDER BY id"
        ).fetchall()
        return [
            Theme(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                lexicon=[LexiconTerm(**term) for term in json.loads(row["lexicon_json"])],
            )
            for row in rows
        ]

    def list_templates(self) -> list[PromptTemplate]:
        rows = self._conn.execute(
            "SELECT id, theme_id, kind, template FROM prompt_templates ORDER BY id"
        ).fetchall()
        return [PromptTemplate(**dict(row)) for row in rows]

    def list_misattributions(self) -> list[MisattributionRecord]:
        rows = self._conn.execute("SELECT * FROM misattributions ORDER BY id").fetchall()
        return [
            MisattributionRecord(
                id=row["id"],
                pattern=row["pattern"],
                claimed_authors=json.loads(row["claimed_authors_json"]),
                verdict=row["verdict"],
                likely_origin=row["likely_origin"],
                note=row["note"],
                reference_url=row["reference_url"],
            )
            for row in rows
        ]

    # --- config -------------------------------------------------------------
    def get_config(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_config(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO config(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def all_config(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM config").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # --- entries ------------------------------------------------------------
    def add_entry(self, entry: Entry) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    f"INSERT INTO entries({_ENTRY_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry.id,
                        entry.kind.value,
                        entry.text,
                        entry.normalized_hash,
                        entry.author,
                        entry.source,
                        entry.url,
                        entry.note,
                        int(entry.pinned),
                        entry.status.value,
                        entry.captured_on.isoformat(),
                        entry.created_at.isoformat(),
                        entry.updated_at.isoformat(),
                    ),
                )
                self._fts_insert(entry.id)
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(str(exc)) from exc

    def get_entry(self, entry_id: str) -> Entry | None:
        row = self._conn.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _entry(row) if row else None

    def update_entry(self, entry: Entry) -> None:
        with self._lock, self._conn:
            self._fts_delete(entry.id)
            cursor = self._conn.execute(
                "UPDATE entries SET kind = ?, text = ?, normalized_hash = ?, author = ?, "
                "source = ?, url = ?, note = ?, pinned = ?, status = ?, captured_on = ?, "
                "created_at = ?, updated_at = ? WHERE id = ?",
                (
                    entry.kind.value,
                    entry.text,
                    entry.normalized_hash,
                    entry.author,
                    entry.source,
                    entry.url,
                    entry.note,
                    int(entry.pinned),
                    entry.status.value,
                    entry.captured_on.isoformat(),
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                    entry.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown entry {entry.id}")
            self._fts_insert(entry.id)

    def list_entries(
        self,
        *,
        status: EntryStatus | None = None,
        pinned: bool | None = None,
        kind: EntryKind | None = None,
        tag: str | None = None,
        theme: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Entry]:
        sql = [f"SELECT DISTINCT e.{_ENTRY_COLUMNS.replace(', ', ', e.')} FROM entries e"]
        params: list[object] = []
        if tag is not None:
            sql.append("JOIN entry_tags et ON et.entry_id = e.id JOIN tags t ON t.id = et.tag_id")
        if theme is not None:
            sql.append("JOIN entry_themes eh ON eh.entry_id = e.id")
        where: list[str] = []
        if status is not None:
            where.append("e.status = ?")
            params.append(status.value)
        if pinned is not None:
            where.append("e.pinned = ?")
            params.append(int(pinned))
        if kind is not None:
            where.append("e.kind = ?")
            params.append(kind.value)
        if tag is not None:
            where.append("t.name = ?")
            params.append(tag)
        if theme is not None:
            where.append("eh.theme_id = ?")
            params.append(theme)
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY e.id")
        if limit is not None:
            sql.append("LIMIT ? OFFSET ?")
            params.extend([limit, offset])
        elif offset:
            sql.append("LIMIT -1 OFFSET ?")
            params.append(offset)
        rows = self._conn.execute(" ".join(sql), params).fetchall()
        return [_entry(row) for row in rows]

    def entries_by_hash(
        self, normalized_hash: str, *, include_archived: bool = False
    ) -> list[Entry]:
        sql = f"SELECT {_ENTRY_COLUMNS} FROM entries WHERE normalized_hash = ?"
        params: list[object] = [normalized_hash]
        if not include_archived:
            sql += " AND status = ?"
            params.append(EntryStatus.ACTIVE.value)
        rows = self._conn.execute(sql + " ORDER BY id", params).fetchall()
        return [_entry(row) for row in rows]

    def search_entries(
        self, query: str, *, status: EntryStatus | None = EntryStatus.ACTIVE, limit: int = 50
    ) -> list[Entry]:
        stems = stemmed_tokens(query)
        if not stems:
            return []
        if not self.fts_available:
            return token_scan(self.list_entries(status=status), query, limit)
        # Feed FTS5 the normalized (unstemmed) tokens: its own porter tokenizer
        # does the stemming, on both sides of the match.
        match = " AND ".join(f'"{token}"' for token in tokenize(query))
        sql = (
            f"SELECT e.{_ENTRY_COLUMNS.replace(', ', ', e.')} "
            "FROM entries_fts f JOIN entries e ON e.rowid = f.rowid "
            "WHERE entries_fts MATCH ?"
        )
        params: list[object] = [match]
        if status is not None:
            sql += " AND e.status = ?"
            params.append(status.value)
        sql += " ORDER BY bm25(entries_fts), e.id LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return token_scan(self.list_entries(status=status), query, limit)
        return [_entry(row) for row in rows]

    # --- tags ---------------------------------------------------------------
    def upsert_tag(self, tag: Tag) -> Tag:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name FROM tags WHERE name = ?", (tag.name,)
            ).fetchone()
            if row is not None:
                return Tag(id=row["id"], name=row["name"])
            with self._conn:
                self._conn.execute("INSERT INTO tags(id, name) VALUES (?, ?)", (tag.id, tag.name))
            return tag

    def list_tags(self) -> list[Tag]:
        rows = self._conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
        return [Tag(id=row["id"], name=row["name"]) for row in rows]

    def set_entry_tags(self, entry_id: str, tag_ids: Sequence[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
            self._conn.executemany(
                "INSERT INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
                [(entry_id, tag_id) for tag_id in dict.fromkeys(tag_ids)],
            )
            self._conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM entry_tags)")

    def entry_tag_names(self, entry_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT t.name FROM tags t JOIN entry_tags et ON et.tag_id = t.id "
            "WHERE et.entry_id = ? ORDER BY t.name",
            (entry_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    # --- themes -------------------------------------------------------------
    def set_entry_themes(self, entry_id: str, themes: Sequence[EntryTheme]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM entry_themes WHERE entry_id = ?", (entry_id,))
            self._conn.executemany(
                "INSERT INTO entry_themes(entry_id, theme_id, source) VALUES (?, ?, ?)",
                [(link.entry_id, link.theme_id, link.source.value) for link in themes],
            )

    def entry_themes(self, entry_id: str) -> list[EntryTheme]:
        rows = self._conn.execute(
            "SELECT entry_id, theme_id, source FROM entry_themes WHERE entry_id = ? "
            "ORDER BY theme_id",
            (entry_id,),
        ).fetchall()
        return [EntryTheme(**dict(row)) for row in rows]

    def all_entry_themes(self) -> dict[str, list[EntryTheme]]:
        rows = self._conn.execute(
            "SELECT entry_id, theme_id, source FROM entry_themes ORDER BY entry_id, theme_id"
        ).fetchall()
        out: dict[str, list[EntryTheme]] = {}
        for row in rows:
            out.setdefault(row["entry_id"], []).append(EntryTheme(**dict(row)))
        return out

    # --- surfacings ---------------------------------------------------------
    def add_surfacing(self, surfacing: Surfacing) -> None:
        with self._lock:
            self.check_monotonic(surfacing)
            try:
                with self._conn:
                    self._conn.execute(
                        f"INSERT INTO surfacings({_SURFACING_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            surfacing.id,
                            surfacing.entry_id,
                            surfacing.on_date.isoformat(),
                            surfacing.slot,
                            surfacing.kind.value,
                            surfacing.select_pool.value,
                            surfacing.prompt_template_id,
                            surfacing.prompt_kind.value,
                            surfacing.prompt_text,
                            int(surfacing.personalized),
                            int(surfacing.personalize_fell_back),
                            int(surfacing.relaxed_cooldown),
                            int(surfacing.prompt_recency_relaxed),
                            surfacing.filter_theme_id,
                            surfacing.filter_collection_id,
                            surfacing.scheduler_version,
                            surfacing.seed,
                            surfacing.created_at.isoformat(),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(str(exc)) from exc

    def get_surfacing(self, surfacing_id: str) -> Surfacing | None:
        row = self._conn.execute(
            f"SELECT {_SURFACING_COLUMNS} FROM surfacings WHERE id = ?", (surfacing_id,)
        ).fetchone()
        return _surfacing(row) if row else None

    def list_surfacings(
        self,
        *,
        entry_id: str | None = None,
        on_date: dt.date | None = None,
        kind: SurfacingKind | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[Surfacing]:
        sql = f"SELECT {_SURFACING_COLUMNS} FROM surfacings"
        where: list[str] = []
        params: list[object] = []
        if entry_id is not None:
            where.append("entry_id = ?")
            params.append(entry_id)
        if on_date is not None:
            where.append("on_date = ?")
            params.append(on_date.isoformat())
        if kind is not None:
            where.append("kind = ?")
            params.append(kind.value)
        if date_from is not None:
            where.append("on_date >= ?")
            params.append(date_from.isoformat())
        if date_to is not None:
            where.append("on_date <= ?")
            params.append(date_to.isoformat())
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY on_date, created_at, slot, id"
        return [_surfacing(row) for row in self._conn.execute(sql, params).fetchall()]

    def max_surfacing_date(self, *, kind: SurfacingKind | None = None) -> dt.date | None:
        sql = "SELECT MAX(on_date) AS newest FROM surfacings"
        params: list[object] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind.value)
        row = self._conn.execute(sql, params).fetchone()
        return _date(row["newest"]) if row else None

    def contested_pools(self, limit: int) -> list[SelectPool]:
        if limit <= 0:
            return []
        rows = self._conn.execute(
            "SELECT select_pool FROM surfacings WHERE kind = 'daily' "
            "AND select_pool IN ('novelty','review') ORDER BY on_date DESC, slot DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [SelectPool(row["select_pool"]) for row in reversed(rows)]

    def last_surfacing_for_entry(self, entry_id: str) -> Surfacing | None:
        rows = self.list_surfacings(entry_id=entry_id)
        return rows[-1] if rows else None

    def recent_template_ids(self, entry_id: str, limit: int) -> list[str]:
        if limit <= 0:
            return []
        rows = self._conn.execute(
            "SELECT prompt_template_id FROM surfacings WHERE entry_id = ? "
            "ORDER BY on_date DESC, created_at DESC LIMIT ?",
            (entry_id, limit),
        ).fetchall()
        return [row["prompt_template_id"] for row in reversed(rows)]

    # --- reflections --------------------------------------------------------
    def add_reflection(self, reflection: Reflection) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO reflections(id, surfacing_id, entry_id, grade, text, logged_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        reflection.id,
                        reflection.surfacing_id,
                        reflection.entry_id,
                        reflection.grade.value,
                        reflection.text,
                        reflection.logged_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(str(exc)) from exc

    def _reflection(self, row: sqlite3.Row) -> Reflection:
        return Reflection(
            id=row["id"],
            surfacing_id=row["surfacing_id"],
            entry_id=row["entry_id"],
            grade=Grade(row["grade"]),
            text=row["text"],
            logged_at=dt.datetime.fromisoformat(row["logged_at"]),
        )

    def get_reflection_for_surfacing(self, surfacing_id: str) -> Reflection | None:
        row = self._conn.execute(
            "SELECT * FROM reflections WHERE surfacing_id = ?", (surfacing_id,)
        ).fetchone()
        return self._reflection(row) if row else None

    def list_reflections(
        self,
        *,
        entry_id: str | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[Reflection]:
        sql = "SELECT * FROM reflections"
        where: list[str] = []
        params: list[object] = []
        if entry_id is not None:
            where.append("entry_id = ?")
            params.append(entry_id)
        if date_from is not None:
            where.append("logged_at >= ?")
            params.append(date_from.isoformat())
        if date_to is not None:
            where.append("logged_at < ?")
            params.append((date_to + dt.timedelta(days=1)).isoformat())
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY logged_at, id"
        return [self._reflection(row) for row in self._conn.execute(sql, params).fetchall()]

    def grades_by_surfacing(self) -> dict[str, Grade]:
        rows = self._conn.execute("SELECT surfacing_id, grade FROM reflections").fetchall()
        return {row["surfacing_id"]: Grade(row["grade"]) for row in rows}

    # --- scheduler state ----------------------------------------------------
    def _state(self, row: sqlite3.Row) -> SchedulerState:
        return SchedulerState(
            entry_id=row["entry_id"],
            exposure_count=row["exposure_count"],
            last_surfaced_on=_date(row["last_surfaced_on"]),
            interval_days=row["interval_days"],
            flat_streak=row["flat_streak"],
        )

    def get_state(self, entry_id: str) -> SchedulerState | None:
        row = self._conn.execute(
            "SELECT * FROM scheduler_state WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return self._state(row) if row else None

    def put_state(self, state: SchedulerState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO scheduler_state(entry_id, exposure_count, last_surfaced_on, "
                "interval_days, flat_streak) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(entry_id) DO UPDATE SET exposure_count = excluded.exposure_count, "
                "last_surfaced_on = excluded.last_surfaced_on, "
                "interval_days = excluded.interval_days, flat_streak = excluded.flat_streak",
                (
                    state.entry_id,
                    state.exposure_count,
                    state.last_surfaced_on.isoformat() if state.last_surfaced_on else None,
                    state.interval_days,
                    state.flat_streak,
                ),
            )

    def all_states(self) -> dict[str, SchedulerState]:
        rows = self._conn.execute("SELECT * FROM scheduler_state").fetchall()
        return {row["entry_id"]: self._state(row) for row in rows}

    # --- collections --------------------------------------------------------
    def add_collection(self, collection: Collection) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO collections(id, name, description, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        collection.id,
                        collection.name,
                        collection.description,
                        collection.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(str(exc)) from exc

    def _collection(self, row: sqlite3.Row) -> Collection:
        return Collection(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=dt.datetime.fromisoformat(row["created_at"]),
        )

    def get_collection(self, collection_id: str) -> Collection | None:
        row = self._conn.execute(
            "SELECT * FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        return self._collection(row) if row else None

    def list_collections(self) -> list[Collection]:
        rows = self._conn.execute("SELECT * FROM collections ORDER BY name").fetchall()
        return [self._collection(row) for row in rows]

    def update_collection(self, collection: Collection) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE collections SET name = ?, description = ? WHERE id = ?",
                (collection.name, collection.description, collection.id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown collection {collection.id}")

    def delete_collection(self, collection_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM collection_entries WHERE collection_id = ?", (collection_id,)
            )
            self._conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    def add_collection_entry(self, collection_id: str, entry_id: str) -> int:
        with self._lock:
            existing = self.collection_entry_ids(collection_id)
            if entry_id in existing:
                return existing.index(entry_id)
            position = len(existing)
            with self._conn:
                self._conn.execute(
                    "INSERT INTO collection_entries(collection_id, entry_id, position) "
                    "VALUES (?, ?, ?)",
                    (collection_id, entry_id, position),
                )
            return position

    def remove_collection_entry(self, collection_id: str, entry_id: str) -> None:
        with self._lock:
            members = [eid for eid in self.collection_entry_ids(collection_id) if eid != entry_id]
            with self._conn:
                self._conn.execute(
                    "DELETE FROM collection_entries WHERE collection_id = ?", (collection_id,)
                )
                self._conn.executemany(
                    "INSERT INTO collection_entries(collection_id, entry_id, position) "
                    "VALUES (?, ?, ?)",
                    [(collection_id, eid, index) for index, eid in enumerate(members)],
                )

    def collection_entry_ids(self, collection_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT entry_id FROM collection_entries WHERE collection_id = ? ORDER BY position",
            (collection_id,),
        ).fetchall()
        return [row["entry_id"] for row in rows]

    # --- attribution flags --------------------------------------------------
    def upsert_attribution_flag(self, flag: AttributionFlag) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM attribution_flags WHERE entry_id = ? AND misattribution_id IS ?",
                (flag.entry_id, flag.misattribution_id),
            )
            self._conn.execute(
                "INSERT INTO attribution_flags(id, entry_id, misattribution_id, verdict, note, "
                "reference_url, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    flag.id,
                    flag.entry_id,
                    flag.misattribution_id,
                    flag.verdict.value,
                    flag.note,
                    flag.reference_url,
                    flag.checked_at.isoformat(),
                ),
            )

    def list_attribution_flags(self, entry_id: str) -> list[AttributionFlag]:
        rows = self._conn.execute(
            "SELECT * FROM attribution_flags WHERE entry_id = ? "
            "ORDER BY COALESCE(misattribution_id, ''), id",
            (entry_id,),
        ).fetchall()
        return [
            AttributionFlag(
                id=row["id"],
                entry_id=row["entry_id"],
                misattribution_id=row["misattribution_id"],
                verdict=row["verdict"],
                note=row["note"],
                reference_url=row["reference_url"],
                checked_at=dt.datetime.fromisoformat(row["checked_at"]),
            )
            for row in rows
        ]


def default_db_path() -> Path:
    """``~/.almanac/almanac.db`` — the single local profile (SCOPE.md D18)."""
    return Path.home() / ".almanac" / "almanac.db"


def open_repository(path: str | Path | None = None) -> SqliteRepository:
    """Open the default database (``~/.almanac/almanac.db``) or an override."""
    import os

    target = path or os.environ.get("ALMANAC_DB_PATH") or default_db_path()
    return SqliteRepository(target)


def iter_entries(repo: Repository) -> Iterable[Entry]:
    """Convenience iterator used by rebuild flows."""
    return repo.list_entries()
