"""SQLite repository (stdlib ``sqlite3``) implementing DATA_MODEL §4.

Writes are transactional and explicit: ``transaction()`` opens ``BEGIN
IMMEDIATE`` and nests by depth, so ingest can wrap one article's insert, story
assignment, mentions and appearances in a single unit and readers never observe
a half-scored article.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from ..engine.models import (
    Alias,
    AliasKind,
    Appearance,
    Article,
    Channel,
    Company,
    Delivery,
    DeliveryItem,
    DeliveryKind,
    DeliveryMode,
    DeliveryStatus,
    Feed,
    FeedResult,
    FetchStatus,
    IngestRun,
    IngestStatus,
    MatchedVia,
    Mention,
    PublishedSource,
    Story,
    Strength,
    TextField,
    iso_utc,
    parse_iso_utc,
)
from .repository import ArticleTextRow, StoryRelevance, UndeliveredStory
from .schema import PRAGMAS, SCHEMA_SQL

__all__ = ["InMemoryRepository", "SQLiteRepository"]

_MEMORY = ":memory:"

#: FR-5 emission order, expressed for SQL ``ORDER BY``.
_FIELD_RANK_SQL = "CASE field WHEN 'title' THEN 0 WHEN 'summary' THEN 1 ELSE 2 END"


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SQLiteRepository:
    """The one repository implementation (DATA_MODEL §4)."""

    def __init__(self, path: str | Path = _MEMORY) -> None:
        self.path = str(path)
        if self.path != _MEMORY:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.path = str(Path(self.path).expanduser())
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        for pragma in PRAGMAS:
            self._conn.execute(pragma)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the schema. Idempotent — ``tickerpress init`` may re-run it."""

        with self._lock:
            self._conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Explicit unit of work; nests by depth (innermost blocks are no-ops)."""

        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self._conn.execute("COMMIT")

    def _execute(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def _query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        return self._execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # companies and aliases
    # ------------------------------------------------------------------

    @staticmethod
    def _company(row: sqlite3.Row) -> Company:
        return Company(
            ticker=row["ticker"],
            name=row["name"],
            mode=DeliveryMode(row["mode"]),
            min_relevance=row["min_relevance"],
            alert_min_relevance=row["alert_min_relevance"],
            context_terms=json.loads(row["context_terms"]),
            anti_terms=json.loads(row["anti_terms"]),
            created_at=parse_iso_utc(row["created_at"]),
        )

    def add_company(self, company: Company) -> Company:
        self._execute(
            """INSERT INTO companies
               (ticker, name, mode, min_relevance, alert_min_relevance,
                context_terms, anti_terms, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                company.ticker,
                company.name,
                company.mode.value,
                company.min_relevance,
                company.alert_min_relevance,
                _dumps(company.context_terms),
                _dumps(company.anti_terms),
                iso_utc(company.created_at),
            ),
        )
        return company

    def get_company(self, ticker: str) -> Company | None:
        row = self._query_one("SELECT * FROM companies WHERE ticker = ?", (ticker.upper(),))
        return self._company(row) if row else None

    def list_companies(self) -> list[Company]:
        return [
            self._company(row) for row in self._query("SELECT * FROM companies ORDER BY ticker ASC")
        ]

    def update_company(self, company: Company) -> Company:
        self._execute(
            """UPDATE companies
               SET name = ?, mode = ?, min_relevance = ?, alert_min_relevance = ?,
                   context_terms = ?, anti_terms = ?
               WHERE ticker = ?""",
            (
                company.name,
                company.mode.value,
                company.min_relevance,
                company.alert_min_relevance,
                _dumps(company.context_terms),
                _dumps(company.anti_terms),
                company.ticker,
            ),
        )
        return company

    def delete_company(self, ticker: str) -> bool:
        """Cascades to aliases, mentions and appearances; the ledger survives."""

        cursor = self._execute("DELETE FROM companies WHERE ticker = ?", (ticker.upper(),))
        return cursor.rowcount > 0

    @staticmethod
    def _alias(row: sqlite3.Row) -> Alias:
        return Alias(
            id=row["id"],
            company_ticker=row["company_ticker"],
            text=row["text"],
            kind=AliasKind(row["kind"]),
            strength=Strength(row["strength"]),
            prior=row["prior"],
            generated=bool(row["generated"]),
            created_at=parse_iso_utc(row["created_at"]),
        )

    def add_alias(self, alias: Alias) -> Alias:
        cursor = self._execute(
            """INSERT INTO aliases (company_ticker, text, kind, strength, prior, generated, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                alias.company_ticker,
                alias.text,
                alias.kind.value,
                alias.strength.value,
                alias.prior,
                int(alias.generated),
                iso_utc(alias.created_at),
            ),
        )
        return alias.model_copy(update={"id": int(cursor.lastrowid or 0)})

    def get_alias(self, alias_id: int) -> Alias | None:
        row = self._query_one("SELECT * FROM aliases WHERE id = ?", (alias_id,))
        return self._alias(row) if row else None

    def list_aliases(self, ticker: str | None = None) -> list[Alias]:
        if ticker is None:
            rows = self._query("SELECT * FROM aliases ORDER BY company_ticker ASC, id ASC")
        else:
            rows = self._query(
                "SELECT * FROM aliases WHERE company_ticker = ? ORDER BY id ASC",
                (ticker.upper(),),
            )
        return [self._alias(row) for row in rows]

    def delete_alias(self, alias_id: int) -> bool:
        cursor = self._execute("DELETE FROM aliases WHERE id = ?", (alias_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # feeds
    # ------------------------------------------------------------------

    @staticmethod
    def _feed(row: sqlite3.Row) -> Feed:
        return Feed(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            enabled=bool(row["enabled"]),
            etag=row["etag"],
            last_modified=row["last_modified"],
            last_polled_at=(
                parse_iso_utc(row["last_polled_at"]) if row["last_polled_at"] else None
            ),
            last_status=FetchStatus(row["last_status"]) if row["last_status"] else None,
            created_at=parse_iso_utc(row["created_at"]),
        )

    def add_feed(self, feed: Feed) -> Feed:
        cursor = self._execute(
            """INSERT INTO feeds (name, url, enabled, etag, last_modified,
                                  last_polled_at, last_status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                feed.name,
                feed.url,
                int(feed.enabled),
                feed.etag,
                feed.last_modified,
                iso_utc(feed.last_polled_at) if feed.last_polled_at else None,
                feed.last_status.value if feed.last_status else None,
                iso_utc(feed.created_at),
            ),
        )
        return feed.model_copy(update={"id": int(cursor.lastrowid or 0)})

    def get_feed(self, feed_id: int) -> Feed | None:
        row = self._query_one("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        return self._feed(row) if row else None

    def get_feed_by_name(self, name: str) -> Feed | None:
        row = self._query_one("SELECT * FROM feeds WHERE name = ?", (name,))
        return self._feed(row) if row else None

    def list_feeds(self, *, enabled_only: bool = False) -> list[Feed]:
        sql = "SELECT * FROM feeds"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id ASC"
        return [self._feed(row) for row in self._query(sql)]

    def update_feed(self, feed: Feed) -> Feed:
        if feed.id is None:
            raise ValueError("cannot update a feed without an id")
        self._execute(
            """UPDATE feeds
               SET name = ?, url = ?, enabled = ?, etag = ?, last_modified = ?,
                   last_polled_at = ?, last_status = ?
               WHERE id = ?""",
            (
                feed.name,
                feed.url,
                int(feed.enabled),
                feed.etag,
                feed.last_modified,
                iso_utc(feed.last_polled_at) if feed.last_polled_at else None,
                feed.last_status.value if feed.last_status else None,
                feed.id,
            ),
        )
        return feed

    def delete_feed(self, feed_id: int) -> bool:
        """Refused while the feed still has archived articles (§2.3)."""

        row = self._query_one("SELECT COUNT(*) AS n FROM articles WHERE feed_id = ?", (feed_id,))
        if row is not None and row["n"]:
            raise ValueError(
                f"feed {feed_id} has {row['n']} archived articles; disable it instead of deleting"
            )
        cursor = self._execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # stories and articles
    # ------------------------------------------------------------------

    @staticmethod
    def _story(row: sqlite3.Row) -> Story:
        return Story(
            id=row["id"],
            created_at=parse_iso_utc(row["created_at"]),
            first_published_at=parse_iso_utc(row["first_published_at"]),
            representative_article_id=row["representative_article_id"],
        )

    def create_story(
        self,
        created_at: datetime,
        first_published_at: datetime,
        representative_article_id: int = 0,
    ) -> int:
        """Insert a story shell.

        ``representative_article_id`` is set to the real article id moments
        later, inside the same transaction: the story<->article reference is a
        cycle, which is why DATA_MODEL leaves that FK app-enforced.
        """

        cursor = self._execute(
            """INSERT INTO stories (created_at, first_published_at, representative_article_id)
               VALUES (?,?,?)""",
            (iso_utc(created_at), iso_utc(first_published_at), representative_article_id),
        )
        return int(cursor.lastrowid or 0)

    def get_story(self, story_id: int) -> Story | None:
        row = self._query_one("SELECT * FROM stories WHERE id = ?", (story_id,))
        return self._story(row) if row else None

    def update_story(self, story: Story) -> Story:
        if story.id is None:
            raise ValueError("cannot update a story without an id")
        self._execute(
            """UPDATE stories SET first_published_at = ?, representative_article_id = ?
               WHERE id = ?""",
            (iso_utc(story.first_published_at), story.representative_article_id, story.id),
        )
        return story

    def list_stories(
        self,
        *,
        company: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Story]:
        clauses: list[str] = []
        params: list[object] = []
        if company is not None:
            clauses.append(
                """s.id IN (SELECT a.story_id FROM articles a
                            JOIN appearances ap ON ap.article_id = a.id
                            WHERE ap.company_ticker = ?)"""
            )
            params.append(company.upper())
        if since is not None:
            clauses.append("s.first_published_at >= ?")
            params.append(iso_utc(since))
        sql = "SELECT s.* FROM stories s"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY s.first_published_at DESC, s.id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._story(row) for row in self._query(sql, params)]

    def story_members(self, story_id: int) -> list[Article]:
        return [
            self._article(row)
            for row in self._query(
                "SELECT * FROM articles WHERE story_id = ? ORDER BY id ASC", (story_id,)
            )
        ]

    def story_copy_count(self, story_id: int) -> int:
        row = self._query_one("SELECT COUNT(*) AS n FROM articles WHERE story_id = ?", (story_id,))
        return int(row["n"]) if row else 0

    @staticmethod
    def _article(row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"],
            feed_id=row["feed_id"],
            item_guid=row["item_guid"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            summary=row["summary"],
            content=row["content"],
            published_at=parse_iso_utc(row["published_at"]),
            published_source=PublishedSource(row["published_source"]),
            first_seen_at=parse_iso_utc(row["first_seen_at"]),
            last_seen_at=parse_iso_utc(row["last_seen_at"]),
            content_sha256=row["content_sha256"],
            token_count=row["token_count"],
            content_token_count=row["content_token_count"],
            story_id=row["story_id"],
            dedup_similarity=row["dedup_similarity"],
        )

    def insert_article(self, article: Article) -> Article:
        cursor = self._execute(
            """INSERT INTO articles
               (feed_id, item_guid, url, canonical_url, title, summary, content,
                published_at, published_source, first_seen_at, last_seen_at,
                content_sha256, token_count, content_token_count, story_id, dedup_similarity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                article.feed_id,
                article.item_guid,
                article.url,
                article.canonical_url,
                article.title,
                article.summary,
                article.content,
                iso_utc(article.published_at),
                article.published_source.value,
                iso_utc(article.first_seen_at),
                iso_utc(article.last_seen_at),
                article.content_sha256,
                article.token_count,
                article.content_token_count,
                article.story_id,
                article.dedup_similarity,
            ),
        )
        return article.model_copy(update={"id": int(cursor.lastrowid or 0)})

    def get_article(self, article_id: int) -> Article | None:
        row = self._query_one("SELECT * FROM articles WHERE id = ?", (article_id,))
        return self._article(row) if row else None

    def get_article_by_guid(self, feed_id: int, item_guid: str) -> Article | None:
        row = self._query_one(
            "SELECT * FROM articles WHERE feed_id = ? AND item_guid = ?", (feed_id, item_guid)
        )
        return self._article(row) if row else None

    def touch_article(self, article_id: int, last_seen_at: datetime) -> None:
        """The only permitted article mutation besides the one-time story id."""

        self._execute(
            "UPDATE articles SET last_seen_at = ? WHERE id = ?",
            (iso_utc(last_seen_at), article_id),
        )

    def articles_in_window(self, published_at: datetime, window_days: int) -> list[ArticleTextRow]:
        low = iso_utc(published_at - timedelta(days=window_days))
        high = iso_utc(published_at + timedelta(days=window_days))
        rows = self._query(
            """SELECT id, story_id, published_at, canonical_url, content_sha256,
                      title, summary, content
               FROM articles
               WHERE published_at >= ? AND published_at <= ?
               ORDER BY id ASC""",
            (low, high),
        )
        return [
            ArticleTextRow(
                article_id=row["id"],
                story_id=row["story_id"],
                published_at=parse_iso_utc(row["published_at"]),
                canonical_url=row["canonical_url"],
                content_sha256=row["content_sha256"],
                title=row["title"],
                summary=row["summary"],
                content=row["content"],
            )
            for row in rows
        ]

    def list_articles(
        self,
        *,
        company: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        min_relevance: int | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        clauses: list[str] = []
        params: list[object] = []
        sql = "SELECT a.* FROM articles a"
        if company is not None or min_relevance is not None:
            sql += " JOIN appearances ap ON ap.article_id = a.id"
            if company is not None:
                clauses.append("ap.company_ticker = ?")
                params.append(company.upper())
            if min_relevance is not None:
                clauses.append("ap.relevance >= ?")
                params.append(min_relevance)
        if since is not None:
            clauses.append("a.published_at >= ?")
            params.append(iso_utc(since))
        if until is not None:
            clauses.append("a.published_at <= ?")
            params.append(iso_utc(until))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " GROUP BY a.id ORDER BY a.published_at DESC, a.id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._article(row) for row in self._query(sql, params)]

    def iter_articles(self) -> Iterator[Article]:
        for row in self._query("SELECT * FROM articles ORDER BY id ASC"):
            yield self._article(row)

    # ------------------------------------------------------------------
    # mentions and appearances
    # ------------------------------------------------------------------

    @staticmethod
    def _mention(row: sqlite3.Row) -> Mention:
        return Mention(
            id=row["id"],
            article_id=row["article_id"],
            company_ticker=row["company_ticker"],
            alias_id=row["alias_id"],
            field=TextField(row["field"]),
            char_start=row["char_start"],
            char_end=row["char_end"],
            surface=row["surface"],
            matched_via=MatchedVia(row["matched_via"]),
            strength=Strength(row["strength"]),
            features=json.loads(row["features"]),
            score=row["score"],
            threshold=row["threshold"],
            accepted=bool(row["accepted"]),
            engine_version=row["engine_version"],
        )

    def insert_mentions(self, mentions: Sequence[Mention]) -> list[Mention]:
        stored: list[Mention] = []
        for mention in mentions:
            cursor = self._execute(
                """INSERT INTO mentions
                   (article_id, company_ticker, alias_id, field, char_start, char_end,
                    surface, matched_via, strength, features, score, threshold,
                    accepted, engine_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mention.article_id,
                    mention.company_ticker,
                    mention.alias_id,
                    mention.field.value,
                    mention.char_start,
                    mention.char_end,
                    mention.surface,
                    mention.matched_via.value,
                    mention.strength.value,
                    _dumps(mention.features),
                    mention.score,
                    mention.threshold,
                    int(mention.accepted),
                    mention.engine_version,
                ),
            )
            stored.append(mention.model_copy(update={"id": int(cursor.lastrowid or 0)}))
        return stored

    def list_mentions(
        self, article_id: int, *, company: str | None = None, accepted_only: bool = False
    ) -> list[Mention]:
        clauses = ["article_id = ?"]
        params: list[object] = [article_id]
        if company is not None:
            clauses.append("company_ticker = ?")
            params.append(company.upper())
        if accepted_only:
            clauses.append("accepted = 1")
        sql = (
            "SELECT * FROM mentions WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY {_FIELD_RANK_SQL} ASC, char_start ASC, COALESCE(alias_id, -1) ASC, id ASC"
        )
        return [self._mention(row) for row in self._query(sql, params)]

    @staticmethod
    def _appearance(row: sqlite3.Row) -> Appearance:
        return Appearance(
            article_id=row["article_id"],
            company_ticker=row["company_ticker"],
            mention_count=row["mention_count"],
            title_hit=bool(row["title_hit"]),
            lede_hit=bool(row["lede_hit"]),
            relevance=row["relevance"],
        )

    def insert_appearances(self, appearances: Sequence[Appearance]) -> list[Appearance]:
        for appearance in appearances:
            self._execute(
                """INSERT INTO appearances
                   (article_id, company_ticker, mention_count, title_hit, lede_hit, relevance)
                   VALUES (?,?,?,?,?,?)""",
                (
                    appearance.article_id,
                    appearance.company_ticker,
                    appearance.mention_count,
                    int(appearance.title_hit),
                    int(appearance.lede_hit),
                    appearance.relevance,
                ),
            )
        return list(appearances)

    def get_appearance(self, article_id: int, company: str) -> Appearance | None:
        row = self._query_one(
            "SELECT * FROM appearances WHERE article_id = ? AND company_ticker = ?",
            (article_id, company.upper()),
        )
        return self._appearance(row) if row else None

    def list_appearances(
        self, *, article_id: int | None = None, company: str | None = None
    ) -> list[Appearance]:
        clauses: list[str] = []
        params: list[object] = []
        if article_id is not None:
            clauses.append("article_id = ?")
            params.append(article_id)
        if company is not None:
            clauses.append("company_ticker = ?")
            params.append(company.upper())
        sql = "SELECT * FROM appearances"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY article_id ASC, company_ticker ASC"
        return [self._appearance(row) for row in self._query(sql, params)]

    def story_relevances(self, *, company: str | None = None) -> list[StoryRelevance]:
        """The derived story-level view of DATA_MODEL §4 (a query, not a table)."""

        sql = """SELECT a.story_id AS story_id, ap.company_ticker AS company_ticker,
                        MAX(ap.relevance) AS relevance
                 FROM appearances ap JOIN articles a ON a.id = ap.article_id"""
        params: list[object] = []
        if company is not None:
            sql += " WHERE ap.company_ticker = ?"
            params.append(company.upper())
        sql += (
            " GROUP BY a.story_id, ap.company_ticker ORDER BY a.story_id ASC, ap.company_ticker ASC"
        )
        return [
            StoryRelevance(
                story_id=row["story_id"],
                company_ticker=row["company_ticker"],
                relevance=row["relevance"],
            )
            for row in self._query(sql, params)
        ]

    # ------------------------------------------------------------------
    # ingest runs
    # ------------------------------------------------------------------

    @staticmethod
    def _ingest_run(row: sqlite3.Row) -> IngestRun:
        return IngestRun(
            id=row["id"],
            started_at=parse_iso_utc(row["started_at"]),
            finished_at=parse_iso_utc(row["finished_at"]) if row["finished_at"] else None,
            status=IngestStatus(row["status"]),
            feed_results=[FeedResult(**item) for item in json.loads(row["feed_results"])],
            articles_new=row["articles_new"],
            candidates_total=row["candidates_total"],
            mentions_accepted=row["mentions_accepted"],
            stories_new=row["stories_new"],
            alerts_sent=row["alerts_sent"],
            engine_version=row["engine_version"],
        )

    def start_ingest_run(self, run: IngestRun) -> IngestRun:
        cursor = self._execute(
            """INSERT INTO ingest_runs (started_at, finished_at, status, feed_results,
                                        articles_new, candidates_total, mentions_accepted,
                                        stories_new, alerts_sent, engine_version)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                iso_utc(run.started_at),
                None,
                run.status.value,
                _dumps([result.model_dump(mode="json") for result in run.feed_results]),
                run.articles_new,
                run.candidates_total,
                run.mentions_accepted,
                run.stories_new,
                run.alerts_sent,
                run.engine_version,
            ),
        )
        return run.model_copy(update={"id": int(cursor.lastrowid or 0)})

    def finish_ingest_run(self, run: IngestRun) -> IngestRun:
        if run.id is None:
            raise ValueError("cannot finish an ingest run without an id")
        self._execute(
            """UPDATE ingest_runs
               SET finished_at = ?, status = ?, feed_results = ?, articles_new = ?,
                   candidates_total = ?, mentions_accepted = ?, stories_new = ?, alerts_sent = ?
               WHERE id = ?""",
            (
                iso_utc(run.finished_at) if run.finished_at else None,
                run.status.value,
                _dumps([result.model_dump(mode="json") for result in run.feed_results]),
                run.articles_new,
                run.candidates_total,
                run.mentions_accepted,
                run.stories_new,
                run.alerts_sent,
                run.id,
            ),
        )
        return run

    def get_ingest_run(self, run_id: int) -> IngestRun | None:
        row = self._query_one("SELECT * FROM ingest_runs WHERE id = ?", (run_id,))
        return self._ingest_run(row) if row else None

    def list_ingest_runs(self, *, limit: int | None = None) -> list[IngestRun]:
        sql = "SELECT * FROM ingest_runs ORDER BY id DESC"
        params: list[object] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._ingest_run(row) for row in self._query(sql, params)]

    # ------------------------------------------------------------------
    # delivery ledger
    # ------------------------------------------------------------------

    def _matched_surfaces(self, article_id: int, company: str) -> tuple[str, ...]:
        rows = self._query(
            f"""SELECT surface FROM mentions
                WHERE article_id = ? AND company_ticker = ? AND accepted = 1
                ORDER BY {_FIELD_RANK_SQL} ASC, char_start ASC, COALESCE(alias_id, -1) ASC""",
            (article_id, company.upper()),
        )
        seen: dict[str, None] = {}
        for row in rows:
            seen.setdefault(row["surface"], None)
        return tuple(seen)

    def _relevance_carrier(self, story_id: int, company: str) -> int | None:
        row = self._query_one(
            """SELECT ap.article_id AS article_id
               FROM appearances ap JOIN articles a ON a.id = ap.article_id
               WHERE a.story_id = ? AND ap.company_ticker = ?
               ORDER BY ap.relevance DESC, ap.article_id ASC LIMIT 1""",
            (story_id, company.upper()),
        )
        return int(row["article_id"]) if row else None

    def undelivered_stories(
        self, channel: Channel, *, company: str | None = None
    ) -> list[UndeliveredStory]:
        """(company, story) pairs with no *counted* ledger item on this channel.

        Only ``counted = 1`` rows suppress a story, so a failed send releases
        its stories for retry (FR-11).
        """

        params: list[object] = [channel.value]
        sql = """
            SELECT sr.story_id AS story_id, sr.company_ticker AS company_ticker,
                   sr.relevance AS relevance, s.representative_article_id AS article_id,
                   s.first_published_at AS first_published_at,
                   ra.title AS title,
                   COALESCE(NULLIF(ra.canonical_url, ''), ra.url) AS url,
                   ra.published_at AS published_at,
                   f.name AS outlet,
                   (SELECT COUNT(*) FROM articles m WHERE m.story_id = sr.story_id) AS copy_count
            FROM (SELECT a.story_id AS story_id, ap.company_ticker AS company_ticker,
                         MAX(ap.relevance) AS relevance
                  FROM appearances ap JOIN articles a ON a.id = ap.article_id
                  GROUP BY a.story_id, ap.company_ticker) sr
            JOIN stories s ON s.id = sr.story_id
            JOIN articles ra ON ra.id = s.representative_article_id
            JOIN feeds f ON f.id = ra.feed_id
            WHERE NOT EXISTS (
                SELECT 1 FROM delivery_items di
                WHERE di.counted = 1 AND di.channel = ?
                  AND di.company_ticker = sr.company_ticker
                  AND di.story_id = sr.story_id)
        """
        if company is not None:
            sql += " AND sr.company_ticker = ?"
            params.append(company.upper())
        sql += " ORDER BY sr.company_ticker ASC, sr.story_id ASC"

        results: list[UndeliveredStory] = []
        for row in self._query(sql, params):
            ticker = row["company_ticker"]
            surfaces = self._matched_surfaces(row["article_id"], ticker)
            if not surfaces:
                carrier = self._relevance_carrier(row["story_id"], ticker)
                if carrier is not None:
                    surfaces = self._matched_surfaces(carrier, ticker)
            results.append(
                UndeliveredStory(
                    company_ticker=ticker,
                    story_id=row["story_id"],
                    relevance=row["relevance"],
                    article_id=row["article_id"],
                    title=row["title"],
                    url=row["url"],
                    outlet=row["outlet"],
                    published_at=parse_iso_utc(row["published_at"]),
                    first_published_at=parse_iso_utc(row["first_published_at"]),
                    copy_count=int(row["copy_count"]),
                    matched_surfaces=surfaces,
                )
            )
        return results

    @staticmethod
    def _delivery(row: sqlite3.Row) -> Delivery:
        return Delivery(
            id=row["id"],
            channel=Channel(row["channel"]),
            kind=DeliveryKind(row["kind"]),
            created_at=parse_iso_utc(row["created_at"]),
            status=DeliveryStatus(row["status"]),
            subject=row["subject"],
            body_text=row["body_text"],
            error=row["error"],
        )

    @staticmethod
    def _delivery_item(row: sqlite3.Row) -> DeliveryItem:
        return DeliveryItem(
            id=row["id"],
            delivery_id=row["delivery_id"],
            channel=Channel(row["channel"]),
            company_ticker=row["company_ticker"],
            story_id=row["story_id"],
            article_id=row["article_id"],
            relevance=row["relevance"],
            counted=bool(row["counted"]),
        )

    def record_delivery(
        self, delivery: Delivery, items: Sequence[DeliveryItem]
    ) -> tuple[Delivery, list[DeliveryItem]]:
        """Persist ``composed`` + uncounted items in one transaction (FR-11)."""

        if delivery.status is not DeliveryStatus.COMPOSED:
            raise ValueError("a delivery is always persisted in status 'composed' first")
        with self.transaction():
            cursor = self._execute(
                """INSERT INTO deliveries (channel, kind, created_at, status, subject, body_text, error)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    delivery.channel.value,
                    delivery.kind.value,
                    iso_utc(delivery.created_at),
                    delivery.status.value,
                    delivery.subject,
                    delivery.body_text,
                    None,
                ),
            )
            delivery_id = int(cursor.lastrowid or 0)
            stored_items: list[DeliveryItem] = []
            for item in items:
                item_cursor = self._execute(
                    """INSERT INTO delivery_items
                       (delivery_id, channel, company_ticker, story_id, article_id, relevance, counted)
                       VALUES (?,?,?,?,?,?,0)""",
                    (
                        delivery_id,
                        delivery.channel.value,
                        item.company_ticker,
                        item.story_id,
                        item.article_id,
                        item.relevance,
                    ),
                )
                stored_items.append(
                    item.model_copy(
                        update={
                            "id": int(item_cursor.lastrowid or 0),
                            "delivery_id": delivery_id,
                            "counted": False,
                        }
                    )
                )
        return delivery.model_copy(update={"id": delivery_id}), stored_items

    def mark_delivery_sent(self, delivery_id: int) -> Delivery:
        """Flip status to ``sent`` and count its items in one transaction.

        A second counted row for an already delivered (channel, company, story)
        violates ``idx_ledger_exactly_once`` and raises ``IntegrityError``
        instead of silently double-delivering.
        """

        with self.transaction():
            self._execute(
                "UPDATE deliveries SET status = 'sent', error = NULL WHERE id = ?",
                (delivery_id,),
            )
            self._execute(
                "UPDATE delivery_items SET counted = 1 WHERE delivery_id = ?", (delivery_id,)
            )
        delivery = self.get_delivery(delivery_id)
        if delivery is None:  # pragma: no cover - caller always passes a live id
            raise ValueError(f"unknown delivery {delivery_id}")
        return delivery

    def mark_delivery_failed(self, delivery_id: int, error: str) -> Delivery:
        """Record the failure; items stay uncounted, so the stories are re-eligible."""

        with self.transaction():
            self._execute(
                "UPDATE deliveries SET status = 'failed', error = ? WHERE id = ?",
                (error or "delivery failed", delivery_id),
            )
        delivery = self.get_delivery(delivery_id)
        if delivery is None:  # pragma: no cover - caller always passes a live id
            raise ValueError(f"unknown delivery {delivery_id}")
        return delivery

    def get_delivery(self, delivery_id: int) -> Delivery | None:
        row = self._query_one("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))
        return self._delivery(row) if row else None

    def list_deliveries(
        self, *, channel: Channel | None = None, limit: int | None = None
    ) -> list[Delivery]:
        sql = "SELECT * FROM deliveries"
        params: list[object] = []
        if channel is not None:
            sql += " WHERE channel = ?"
            params.append(channel.value)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._delivery(row) for row in self._query(sql, params)]

    def list_delivery_items(
        self,
        *,
        delivery_id: int | None = None,
        channel: Channel | None = None,
        company: str | None = None,
        story_id: int | None = None,
    ) -> list[DeliveryItem]:
        clauses: list[str] = []
        params: list[object] = []
        if delivery_id is not None:
            clauses.append("delivery_id = ?")
            params.append(delivery_id)
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel.value)
        if company is not None:
            clauses.append("company_ticker = ?")
            params.append(company.upper())
        if story_id is not None:
            clauses.append("story_id = ?")
            params.append(story_id)
        sql = "SELECT * FROM delivery_items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC"
        return [self._delivery_item(row) for row in self._query(sql, params)]


def InMemoryRepository(
    *,
    initialize: bool = True,
) -> SQLiteRepository:
    """A repository backed by ``sqlite3(":memory:")``.

    Not a second implementation: the exactly-once partial unique index must
    exist in every configuration, so tests get the same engine production does
    (DATA_MODEL §4, SCOPE D8).
    """

    repository = SQLiteRepository(_MEMORY)
    if initialize:
        repository.initialize()
    return repository
