"""SQLite `Repository` (stdlib `sqlite3`), schema per DATA_MODEL.md.

Append-only invariants that the in-memory backend enforces in Python are CHECK
and UNIQUE constraints here, so the database itself refuses a bad write:
`signal.confidence` between 0.05 and 0.95, `brief.frame_checked = 1`, price bars
with `low <= open, close <= high`, one revision per (signal_key, revision), one
event per (cluster_id, event_type).

JSON columns hold canonical (sorted-keys, compact) serializations so byte
identity is well defined.  Single-user, single-process (SCOPE D-17).

Thread-safety: FastAPI runs sync endpoints in a threadpool, so the one
connection built at startup is used from many threads.  ``check_same_thread``
is disabled (CPython's ``sqlite3.threadsafety == 3`` -- the C level is
serialized) and an ``RLock`` is held across every write batch and every
read-modify-write composite, so transactions cannot interleave and one
thread's commit or rollback can never capture another's uncommitted rows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..engine.normalize import canonical_json
from ..models import (
    Article,
    Asset,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    BarSource,
    Brief,
    Cluster,
    Event,
    EventLink,
    PriceBar,
    Signal,
    SignalKeyAlias,
    WatchlistItem,
)
from .base import RepositoryError

DB_ENV = "NEWSALPHA_DB"
DEFAULT_DB_PATH = Path.home() / ".newsalpha" / "newsalpha.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS asset (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('equity','crypto','index')),
    symbol         TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    aliases        TEXT NOT NULL,
    ambiguous      INTEGER NOT NULL CHECK (ambiguous IN (0,1)),
    context_keywords TEXT NOT NULL,
    benchmark_id   TEXT REFERENCES asset(id),
    CHECK ((kind = 'index') = (benchmark_id IS NULL))
);

CREATE TABLE IF NOT EXISTS watchlist_item (
    asset_id TEXT PRIMARY KEY REFERENCES asset(id),
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article (
    id                     TEXT PRIMARY KEY,
    external_id            TEXT NOT NULL,
    url                    TEXT,
    source_domain          TEXT NOT NULL,
    tier                   TEXT NOT NULL,
    published_at           TEXT NOT NULL,
    published_at_estimated INTEGER NOT NULL CHECK (published_at_estimated IN (0,1)),
    excluded_from_analysis INTEGER NOT NULL CHECK (excluded_from_analysis IN (0,1)),
    fetched_at             TEXT NOT NULL,
    title                  TEXT NOT NULL,
    body                   TEXT NOT NULL,
    content_hash           TEXT NOT NULL UNIQUE,
    UNIQUE (source_domain, external_id)
);
CREATE INDEX IF NOT EXISTS article_published_idx ON article(published_at);

CREATE TABLE IF NOT EXISTS cluster (
    id                    TEXT PRIMARY KEY,
    earliest_published_at TEXT NOT NULL,
    latest_published_at   TEXT NOT NULL,
    article_count         INTEGER NOT NULL CHECK (article_count >= 1),
    corroboration         INTEGER NOT NULL CHECK (corroboration >= 1),
    best_tier             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_member (
    cluster_id TEXT NOT NULL REFERENCES cluster(id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES article(id),
    PRIMARY KEY (cluster_id, article_id)
);

CREATE TABLE IF NOT EXISTS event (
    id                    TEXT PRIMARY KEY,
    cluster_id            TEXT NOT NULL REFERENCES cluster(id) ON DELETE CASCADE,
    event_type            TEXT NOT NULL,
    stage                 TEXT NOT NULL CHECK (stage IN ('rumored','confirmed','denied')),
    attributes            TEXT NOT NULL,
    extraction_confidence REAL NOT NULL CHECK (extraction_confidence BETWEEN 0.05 AND 0.95),
    evidence              TEXT NOT NULL,
    notes                 TEXT NOT NULL,
    event_date            TEXT NOT NULL,
    observed_at           TEXT NOT NULL,
    UNIQUE (cluster_id, event_type)
);

CREATE TABLE IF NOT EXISTS event_link (
    event_id        TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    asset_id        TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (
                        role IN ('subject','acquirer','target','venue','mentioned')),
    link_confidence REAL NOT NULL CHECK (link_confidence BETWEEN 0.5 AND 0.95),
    evidence        TEXT NOT NULL,
    PRIMARY KEY (event_id, asset_id)
);

CREATE TABLE IF NOT EXISTS signal (
    id              TEXT PRIMARY KEY,
    signal_key      TEXT NOT NULL,
    revision        INTEGER NOT NULL CHECK (revision >= 1),
    supersedes      TEXT,
    supersedes_key  TEXT,
    event_id        TEXT NOT NULL,
    asset_id        TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('subject','acquirer','target')),
    direction       TEXT NOT NULL CHECK (direction IN ('bullish','bearish')),
    magnitude       TEXT NOT NULL CHECK (magnitude IN ('minor','moderate','major')),
    confidence      REAL NOT NULL CHECK (confidence BETWEEN 0.05 AND 0.95),
    horizon_bars    INTEGER NOT NULL CHECK (horizon_bars IN (1,5,20)),
    expected_ar_lo  REAL NOT NULL,
    expected_ar_hi  REAL NOT NULL,
    score           REAL NOT NULL,
    prior_key       TEXT NOT NULL,
    rationale_codes TEXT NOT NULL,
    event_snapshot  TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    created_as_of   TEXT NOT NULL,
    UNIQUE (signal_key, revision)
);
CREATE INDEX IF NOT EXISTS signal_key_idx ON signal(signal_key);
CREATE INDEX IF NOT EXISTS signal_asset_idx ON signal(asset_id);

CREATE TABLE IF NOT EXISTS signal_key_alias (
    from_key      TEXT PRIMARY KEY,
    to_key        TEXT NOT NULL,
    created_as_of TEXT NOT NULL,
    CHECK (from_key <> to_key)
);

CREATE TABLE IF NOT EXISTS brief (
    signal_id        TEXT PRIMARY KEY REFERENCES signal(id),
    template_id      TEXT NOT NULL,
    what_happened    TEXT NOT NULL CHECK (length(trim(what_happened)) > 0),
    why_it_matters   TEXT NOT NULL CHECK (length(trim(why_it_matters)) > 0),
    what_to_watch    TEXT NOT NULL,
    uncertainty_note TEXT NOT NULL CHECK (length(trim(uncertainty_note)) > 0),
    rendered_text    TEXT NOT NULL,
    frame_checked    INTEGER NOT NULL CHECK (frame_checked = 1)
);

CREATE TABLE IF NOT EXISTS price_bar (
    asset_id TEXT NOT NULL,
    date     TEXT NOT NULL,
    open     REAL NOT NULL CHECK (open > 0),
    high     REAL NOT NULL CHECK (high > 0),
    low      REAL NOT NULL CHECK (low > 0),
    close    REAL NOT NULL CHECK (close > 0),
    volume   REAL NOT NULL CHECK (volume >= 0),
    source   TEXT NOT NULL CHECK (source IN ('fixture','live')),
    PRIMARY KEY (asset_id, date),
    CHECK (low <= open AND open <= high AND low <= close AND close <= high)
);

CREATE TABLE IF NOT EXISTS backtest_run (
    id         TEXT PRIMARY KEY,
    params     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    aggregates TEXT NOT NULL,
    per_type   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_result (
    run_id           TEXT NOT NULL REFERENCES backtest_run(id),
    signal_id        TEXT NOT NULL REFERENCES signal(id),
    entry_date       TEXT,
    exit_date        TEXT,
    ar               REAL,
    hit              INTEGER,
    excluded_reason  TEXT,
    placebo_attempts INTEGER NOT NULL DEFAULT 0 CHECK (placebo_attempts BETWEEN 0 AND 8),
    PRIMARY KEY (run_id, signal_id),
    CHECK ((hit IS NULL) = (excluded_reason IS NOT NULL))
);
"""


def default_db_path(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else dict(os.environ)
    return Path(env.get(DB_ENV, str(DEFAULT_DB_PATH)))


def _json(value: Any) -> str:
    return canonical_json(value)


class SQLiteRepository:
    """Default backend. `path=":memory:"` gives a throwaway database."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None and str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            check_same_thread=False,  # threadpool handlers share it; see module docstring
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()  # serializes write batches across threads

    # -- schema ------------------------------------------------------------ #

    def initialize(self) -> None:
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    def reset(self) -> None:
        tables = [
            "backtest_result",
            "backtest_run",
            "brief",
            "signal_key_alias",
            "signal",
            "event_link",
            "event",
            "cluster_member",
            "cluster",
            "price_bar",
            "watchlist_item",
            "article",
            "asset",
        ]
        with self._lock:
            self.initialize()
            for table in tables:
                self._connection.execute(f"DELETE FROM {table}")
            self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    # -- assets & watchlist ------------------------------------------------ #

    def replace_assets(self, assets: list[Asset]) -> int:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM asset")
            # Indexes first so equity/crypto benchmark_id foreign keys resolve.
            ordered = sorted(assets, key=lambda a: (a.kind.value != "index", a.id))
            self._connection.executemany(
                "INSERT INTO asset (id, kind, symbol, name, aliases, ambiguous, "
                "context_keywords, benchmark_id) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        a.id,
                        a.kind.value,
                        a.symbol,
                        a.name,
                        _json(list(a.aliases)),
                        int(a.ambiguous),
                        _json(list(a.context_keywords)),
                        a.benchmark_id,
                    )
                    for a in ordered
                ],
            )
        return len(assets)

    def get_asset(self, asset_id: str) -> Asset | None:
        row = self._connection.execute("SELECT * FROM asset WHERE id = ?", (asset_id,)).fetchone()
        return _row_to_asset(row) if row else None

    def list_assets(self, *, kind: str | None = None, query: str | None = None) -> list[Asset]:
        sql = "SELECT * FROM asset"
        params: list[Any] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY id"
        rows = [_row_to_asset(row) for row in self._connection.execute(sql, params)]
        if query:
            needle = query.casefold()
            rows = [
                a
                for a in rows
                if needle in a.id.casefold()
                or needle in a.name.casefold()
                or needle in a.symbol.casefold()
                or any(needle in alias.casefold() for alias in a.aliases)
            ]
        return rows

    def add_watchlist(self, item: WatchlistItem) -> bool:
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO watchlist_item (asset_id, added_at) VALUES (?,?)",
                    (item.asset_id, item.added_at),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"unknown asset {item.asset_id!r}: {exc}") from exc
        return cursor.rowcount > 0

    def remove_watchlist(self, asset_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM watchlist_item WHERE asset_id = ?", (asset_id,)
            )
        return cursor.rowcount > 0

    def list_watchlist(self) -> list[WatchlistItem]:
        return [
            WatchlistItem(asset_id=row["asset_id"], added_at=row["added_at"])
            for row in self._connection.execute("SELECT * FROM watchlist_item ORDER BY asset_id")
        ]

    # -- articles ---------------------------------------------------------- #

    def add_articles(self, articles: list[Article]) -> int:
        added = 0
        with self._lock, self._connection:
            for article in articles:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO article (id, external_id, url, source_domain, tier, "
                    "published_at, published_at_estimated, excluded_from_analysis, fetched_at, "
                    "title, body, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        article.id,
                        article.external_id,
                        article.url,
                        article.source_domain,
                        article.tier.value,
                        article.published_at,
                        int(article.published_at_estimated),
                        int(article.excluded_from_analysis),
                        article.fetched_at,
                        article.title,
                        article.body,
                        article.content_hash,
                    ),
                )
                added += cursor.rowcount
        return added

    def get_article(self, article_id: str) -> Article | None:
        row = self._connection.execute(
            "SELECT * FROM article WHERE id = ?", (article_id,)
        ).fetchone()
        return _row_to_article(row) if row else None

    def list_articles(
        self,
        *,
        since: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        sql = "SELECT * FROM article"
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("published_at >= ?")
            params.append(since)
        if domain is not None:
            clauses.append("source_domain = ?")
            params.append(domain.lower())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY published_at, id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_article(row) for row in self._connection.execute(sql, params)]

    # -- derived rows ------------------------------------------------------ #

    def replace_derived(
        self,
        clusters: list[Cluster],
        events: list[Event],
        links: list[EventLink],
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM event_link")
            self._connection.execute("DELETE FROM event")
            self._connection.execute("DELETE FROM cluster_member")
            self._connection.execute("DELETE FROM cluster")
            self._connection.executemany(
                "INSERT INTO cluster (id, earliest_published_at, latest_published_at, "
                "article_count, corroboration, best_tier) VALUES (?,?,?,?,?,?)",
                [
                    (
                        c.id,
                        c.earliest_published_at,
                        c.latest_published_at,
                        c.article_count,
                        c.corroboration,
                        c.best_tier.value,
                    )
                    for c in clusters
                ],
            )
            self._connection.executemany(
                "INSERT INTO cluster_member (cluster_id, article_id) VALUES (?,?)",
                [(c.id, article_id) for c in clusters for article_id in c.article_ids],
            )
            self._connection.executemany(
                "INSERT INTO event (id, cluster_id, event_type, stage, attributes, "
                "extraction_confidence, evidence, notes, event_date, observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        e.id,
                        e.cluster_id,
                        e.event_type.value,
                        e.stage.value,
                        _json(e.attributes),
                        e.extraction_confidence,
                        _json([span.model_dump(mode="json") for span in e.evidence]),
                        _json(list(e.notes)),
                        e.event_date,
                        e.observed_at,
                    )
                    for e in events
                ],
            )
            self._connection.executemany(
                "INSERT INTO event_link (event_id, asset_id, role, link_confidence, evidence) "
                "VALUES (?,?,?,?,?)",
                [
                    (
                        link.event_id,
                        link.asset_id,
                        link.role.value,
                        link.link_confidence,
                        _json(link.evidence.model_dump(mode="json")),
                    )
                    for link in links
                ],
            )

    def list_clusters(self) -> list[Cluster]:
        members: dict[str, list[str]] = {}
        for row in self._connection.execute(
            "SELECT cluster_id, article_id FROM cluster_member ORDER BY article_id"
        ):
            members.setdefault(row["cluster_id"], []).append(row["article_id"])
        clusters: list[Cluster] = []
        for row in self._connection.execute("SELECT * FROM cluster ORDER BY id"):
            clusters.append(
                Cluster(
                    id=row["id"],
                    article_ids=tuple(sorted(members.get(row["id"], []))),
                    earliest_published_at=row["earliest_published_at"],
                    latest_published_at=row["latest_published_at"],
                    article_count=row["article_count"],
                    corroboration=row["corroboration"],
                    best_tier=row["best_tier"],
                )
            )
        return clusters

    def get_event(self, event_id: str) -> Event | None:
        row = self._connection.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def list_events(
        self,
        *,
        event_type: str | None = None,
        asset_id: str | None = None,
        stage: str | None = None,
        since: str | None = None,
    ) -> list[Event]:
        sql = "SELECT event.* FROM event"
        clauses: list[str] = []
        params: list[Any] = []
        if asset_id is not None:
            sql += " JOIN event_link ON event_link.event_id = event.id"
            clauses.append("event_link.asset_id = ?")
            params.append(asset_id)
        if event_type is not None:
            clauses.append("event.event_type = ?")
            params.append(event_type)
        if stage is not None:
            clauses.append("event.stage = ?")
            params.append(stage)
        if since is not None:
            clauses.append("event.event_date >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY event.event_date, event.id"
        return [_row_to_event(row) for row in self._connection.execute(sql, params)]

    def list_links(self, event_id: str | None = None) -> list[EventLink]:
        sql = "SELECT * FROM event_link"
        params: list[Any] = []
        if event_id is not None:
            sql += " WHERE event_id = ?"
            params.append(event_id)
        sql += " ORDER BY event_id, asset_id"
        return [_row_to_link(row) for row in self._connection.execute(sql, params)]

    # -- signals ----------------------------------------------------------- #

    def add_signals(self, signals: list[Signal]) -> int:
        added = 0
        try:
            with self._lock, self._connection:
                for signal in signals:
                    if self._connection.execute(
                        "SELECT 1 FROM signal WHERE id = ?", (signal.id,)
                    ).fetchone():
                        continue  # append-only: the same revision is written once
                    cursor = self._connection.execute(
                        "INSERT INTO signal (id, signal_key, revision, supersedes, "
                        "supersedes_key, event_id, asset_id, role, direction, magnitude, "
                        "confidence, horizon_bars, expected_ar_lo, expected_ar_hi, score, "
                        "prior_key, rationale_codes, event_snapshot, observed_at, created_as_of) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            signal.id,
                            signal.signal_key,
                            signal.revision,
                            signal.supersedes,
                            signal.supersedes_key,
                            signal.event_id,
                            signal.asset_id,
                            signal.role.value,
                            signal.direction.value,
                            signal.magnitude.value,
                            signal.confidence,
                            signal.horizon_bars,
                            signal.expected_ar_lo,
                            signal.expected_ar_hi,
                            signal.score,
                            signal.prior_key,
                            _json(list(signal.rationale_codes)),
                            _json(signal.event_snapshot.model_dump(mode="json")),
                            signal.observed_at,
                            signal.created_as_of,
                        ),
                    )
                    added += cursor.rowcount
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"signal write violates an invariant: {exc}") from exc
        return added

    def get_signal(self, signal_id: str) -> Signal | None:
        row = self._connection.execute("SELECT * FROM signal WHERE id = ?", (signal_id,)).fetchone()
        return _row_to_signal(row) if row else None

    def list_signals(
        self,
        *,
        asset_id: str | None = None,
        direction: str | None = None,
        min_confidence: float | None = None,
        since: str | None = None,
    ) -> list[Signal]:
        sql = "SELECT * FROM signal"
        clauses: list[str] = []
        params: list[Any] = []
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        if direction is not None:
            clauses.append("direction = ?")
            params.append(direction)
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(min_confidence)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY signal_key, revision"
        rows = [_row_to_signal(row) for row in self._connection.execute(sql, params)]
        if since is not None:
            rows = [s for s in rows if s.event_snapshot.event_date >= since]
        return rows

    def signals_for_key(self, signal_key: str) -> list[Signal]:
        return [
            _row_to_signal(row)
            for row in self._connection.execute(
                "SELECT * FROM signal WHERE signal_key = ? ORDER BY revision", (signal_key,)
            )
        ]

    def add_aliases(self, aliases: list[SignalKeyAlias]) -> int:
        added = 0
        with self._lock, self._connection:
            for alias in aliases:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO signal_key_alias (from_key, to_key, created_as_of) "
                    "VALUES (?,?,?)",
                    (alias.from_key, alias.to_key, alias.created_as_of),
                )
                added += cursor.rowcount
        return added

    def list_aliases(self) -> list[SignalKeyAlias]:
        return [
            SignalKeyAlias(
                from_key=row["from_key"],
                to_key=row["to_key"],
                created_as_of=row["created_as_of"],
            )
            for row in self._connection.execute("SELECT * FROM signal_key_alias ORDER BY from_key")
        ]

    # -- briefs ------------------------------------------------------------ #

    def add_briefs(self, briefs: list[Brief]) -> int:
        added = 0
        try:
            with self._lock, self._connection:
                for brief in briefs:
                    if self._connection.execute(
                        "SELECT 1 FROM brief WHERE signal_id = ?", (brief.signal_id,)
                    ).fetchone():
                        continue  # one brief per signal revision, written once
                    cursor = self._connection.execute(
                        "INSERT INTO brief (signal_id, template_id, what_happened, "
                        "why_it_matters, what_to_watch, uncertainty_note, rendered_text, "
                        "frame_checked) VALUES (?,?,?,?,?,?,?,1)",
                        (
                            brief.signal_id,
                            brief.template_id,
                            brief.what_happened,
                            brief.why_it_matters,
                            _json(list(brief.what_to_watch)),
                            brief.uncertainty_note,
                            brief.rendered_text,
                        ),
                    )
                    added += cursor.rowcount
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"brief write violates an invariant: {exc}") from exc
        return added

    def get_brief(self, signal_id: str) -> Brief | None:
        row = self._connection.execute(
            "SELECT * FROM brief WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            return None
        return Brief(
            signal_id=row["signal_id"],
            template_id=row["template_id"],
            what_happened=row["what_happened"],
            why_it_matters=row["why_it_matters"],
            what_to_watch=tuple(json.loads(row["what_to_watch"])),
            uncertainty_note=row["uncertainty_note"],
            rendered_text=row["rendered_text"],
            frame_checked=True,
        )

    # -- prices ------------------------------------------------------------ #

    def add_price_bars(self, bars: list[PriceBar]) -> int:
        added = 0
        with self._lock, self._connection:
            for bar in bars:
                row = self._connection.execute(
                    "SELECT * FROM price_bar WHERE asset_id = ? AND date = ?",
                    (bar.asset_id, bar.date),
                ).fetchone()
                if row is not None:
                    if row["source"] == BarSource.fixture.value and bar.source is BarSource.live:
                        continue  # fixture bars are never overwritten by live loads
                    if _bar_row_matches(row, bar):
                        continue  # re-loading identical bars is a no-op
                self._connection.execute(
                    "INSERT OR REPLACE INTO price_bar (asset_id, date, open, high, low, close, "
                    "volume, source) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        bar.asset_id,
                        bar.date,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.source.value,
                    ),
                )
                added += 1
        return added

    def list_price_bars(
        self,
        *,
        asset_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[PriceBar]:
        sql = "SELECT * FROM price_bar"
        clauses: list[str] = []
        params: list[Any] = []
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        if start is not None:
            clauses.append("date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("date <= ?")
            params.append(end)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY asset_id, date"
        return [
            PriceBar(
                asset_id=row["asset_id"],
                date=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                source=row["source"],
            )
            for row in self._connection.execute(sql, params)
        ]

    # -- backtests --------------------------------------------------------- #

    def add_backtest(self, run: BacktestRun, results: list[BacktestResult]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO backtest_run (id, params, as_of, aggregates, per_type) "
                "VALUES (?,?,?,?,?)",
                (
                    run.id,
                    _json(run.params.model_dump(mode="json")),
                    run.as_of,
                    _json(run.aggregates.model_dump(mode="json")),
                    _json({k: v.model_dump(mode="json") for k, v in run.per_type.items()}),
                ),
            )
            self._connection.execute("DELETE FROM backtest_result WHERE run_id = ?", (run.id,))
            self._connection.executemany(
                "INSERT INTO backtest_result (run_id, signal_id, entry_date, exit_date, ar, hit, "
                "excluded_reason, placebo_attempts) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        result.run_id,
                        result.signal_id,
                        result.entry_date,
                        result.exit_date,
                        result.ar,
                        None if result.hit is None else int(result.hit),
                        None if result.excluded_reason is None else result.excluded_reason.value,
                        result.placebo_attempts,
                    )
                    for result in results
                ],
            )

    def get_backtest(self, run_id: str) -> BacktestRun | None:
        row = self._connection.execute(
            "SELECT * FROM backtest_run WHERE id = ?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row else None

    def list_backtests(self, limit: int | None = None) -> list[BacktestRun]:
        sql = "SELECT * FROM backtest_run ORDER BY as_of DESC, id DESC"
        params: list[Any] = []
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_run(row) for row in self._connection.execute(sql, params)]

    def list_backtest_results(self, run_id: str) -> list[BacktestResult]:
        return [
            BacktestResult(
                run_id=row["run_id"],
                signal_id=row["signal_id"],
                entry_date=row["entry_date"],
                exit_date=row["exit_date"],
                ar=row["ar"],
                hit=None if row["hit"] is None else bool(row["hit"]),
                excluded_reason=row["excluded_reason"],
                placebo_attempts=row["placebo_attempts"],
            )
            for row in self._connection.execute(
                "SELECT * FROM backtest_result WHERE run_id = ? ORDER BY signal_id", (run_id,)
            )
        ]


# --------------------------------------------------------------------------- #
# Row mappers
# --------------------------------------------------------------------------- #


def _bar_row_matches(row: sqlite3.Row, bar: PriceBar) -> bool:
    return (
        row["open"] == bar.open
        and row["high"] == bar.high
        and row["low"] == bar.low
        and row["close"] == bar.close
        and row["volume"] == bar.volume
        and row["source"] == bar.source.value
    )


def _row_to_asset(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        kind=row["kind"],
        symbol=row["symbol"],
        name=row["name"],
        aliases=tuple(json.loads(row["aliases"])),
        ambiguous=bool(row["ambiguous"]),
        context_keywords=tuple(json.loads(row["context_keywords"])),
        benchmark_id=row["benchmark_id"],
    )


def _row_to_article(row: sqlite3.Row) -> Article:
    return Article(
        id=row["id"],
        external_id=row["external_id"],
        url=row["url"],
        source_domain=row["source_domain"],
        tier=row["tier"],
        published_at=row["published_at"],
        published_at_estimated=bool(row["published_at_estimated"]),
        excluded_from_analysis=bool(row["excluded_from_analysis"]),
        fetched_at=row["fetched_at"],
        title=row["title"],
        body=row["body"],
        content_hash=row["content_hash"],
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        cluster_id=row["cluster_id"],
        event_type=row["event_type"],
        stage=row["stage"],
        attributes=json.loads(row["attributes"]),
        extraction_confidence=row["extraction_confidence"],
        evidence=tuple(json.loads(row["evidence"])),
        notes=tuple(json.loads(row["notes"])),
        event_date=row["event_date"],
        observed_at=row["observed_at"],
    )


def _row_to_link(row: sqlite3.Row) -> EventLink:
    return EventLink(
        event_id=row["event_id"],
        asset_id=row["asset_id"],
        role=row["role"],
        link_confidence=row["link_confidence"],
        evidence=json.loads(row["evidence"]),
    )


def _row_to_signal(row: sqlite3.Row) -> Signal:
    return Signal(
        id=row["id"],
        signal_key=row["signal_key"],
        revision=row["revision"],
        supersedes=row["supersedes"],
        supersedes_key=row["supersedes_key"],
        event_id=row["event_id"],
        asset_id=row["asset_id"],
        role=row["role"],
        direction=row["direction"],
        magnitude=row["magnitude"],
        confidence=row["confidence"],
        horizon_bars=row["horizon_bars"],
        expected_ar_lo=row["expected_ar_lo"],
        expected_ar_hi=row["expected_ar_hi"],
        score=row["score"],
        prior_key=row["prior_key"],
        rationale_codes=tuple(json.loads(row["rationale_codes"])),
        event_snapshot=json.loads(row["event_snapshot"]),
        observed_at=row["observed_at"],
        created_as_of=row["created_as_of"],
    )


def _row_to_run(row: sqlite3.Row) -> BacktestRun:
    return BacktestRun(
        id=row["id"],
        params=BacktestParams.model_validate(json.loads(row["params"])),
        as_of=row["as_of"],
        aggregates=json.loads(row["aggregates"]),
        per_type=json.loads(row["per_type"]),
    )


__all__ = ["DB_ENV", "DEFAULT_DB_PATH", "SCHEMA", "SQLiteRepository", "default_db_path"]
