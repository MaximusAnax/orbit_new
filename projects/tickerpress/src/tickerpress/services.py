"""Composition layer: adapters + repository + engine (SCOPE architecture).

Everything with a side effect lives here — fetching, transactions, delivery —
so that ``engine/`` stays a pure function of its inputs and ``api/``/``cli/``
stay thin. Business rules that need I/O (ingest ordering, the alert pass, the
compose->persist->send->mark lifecycle) are implemented here exactly once and
shared by both front ends.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .adapters.clock import Clock, SystemClock
from .adapters.feeds import FeedSource, FetchResult
from .adapters.feeds_fixture import FixtureFeedSource
from .adapters.notify import ComposedMessage, MessageItem, Notifier
from .adapters.notify_console import ConsoleNotifier
from .adapters.notify_email import EmailNotifier
from .adapters.notify_file import FileNotifier
from .adapters.notify_webhook import WebhookNotifier
from .engine import dedup, pipeline
from .engine import digest as digest_engine
from .engine.feedparse import FeedParseError, parse_feed
from .engine.models import (
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
    Mention,
    Story,
    Strength,
    normalize_ticker,
)
from .engine.watchlist import (
    DuplicateAliasError,
    check_duplicate_surface,
    compile_matcher,
    default_prior,
    default_strength,
    generate_aliases,
)
from .resources import Lexicons, load_lexicons
from .store.repository import UndeliveredStory
from .store.sqlite_store import SQLiteRepository

__all__ = [
    "DigestResult",
    "Explanation",
    "MentionExplanation",
    "NotifierRegistry",
    "TickerPressService",
    "UnknownArticleError",
    "UnknownChannelError",
    "UnknownCompanyError",
    "UnknownFeedError",
]


class UnknownCompanyError(KeyError):
    """The requested ticker is not on the watchlist."""


class UnknownFeedError(KeyError):
    """The requested feed id is not registered."""


class UnknownArticleError(KeyError):
    """The requested article id is not in the archive."""


class UnknownChannelError(RuntimeError):
    """A channel was requested that has no configured notifier."""


@dataclass(frozen=True, slots=True)
class DigestResult:
    """Outcome of one digest run.

    ``delivery is None and body is None`` means the selection was empty: FR-9
    says that composes nothing, sends nothing and records nothing.
    """

    delivery: Delivery | None
    body: str | None
    items: tuple[UndeliveredStory, ...] = ()
    dry_run: bool = False

    @property
    def empty(self) -> bool:
        return self.body is None


@dataclass(frozen=True, slots=True)
class MentionExplanation:
    """One candidate as ``explain`` reports it — read from stored rows only."""

    mention: Mention
    alias: Alias | None


@dataclass(frozen=True, slots=True)
class Explanation:
    """FR-13: why this article matched (or did not), with no recomputation."""

    article: Article
    story: Story
    joined_existing_story: bool
    dedup_similarity: float | None
    story_copy_count: int
    candidates: tuple[MentionExplanation, ...]
    appearances: tuple[Appearance, ...]


class NotifierRegistry:
    """Resolves a :class:`Channel` to a notifier.

    Offline channels are always available. The live ones activate only when
    their environment is configured, and say so clearly when it is not — the
    channel is never silently swapped for a different one.
    """

    def __init__(
        self,
        *,
        outbox_dir: Path | str = "outbox",
        overrides: Mapping[Channel, Notifier] | None = None,
    ) -> None:
        self.outbox_dir = Path(outbox_dir)
        self._overrides: dict[Channel, Notifier] = dict(overrides or {})

    def register(self, channel: Channel, notifier: Notifier) -> None:
        self._overrides[channel] = notifier

    def get(self, channel: Channel) -> Notifier:
        if channel in self._overrides:
            return self._overrides[channel]
        if channel is Channel.CONSOLE:
            return ConsoleNotifier()
        if channel is Channel.FILE:
            return FileNotifier(self.outbox_dir)
        if channel is Channel.EMAIL:
            notifier = EmailNotifier.from_env()
            if notifier is None:
                raise UnknownChannelError(
                    "email channel is not configured: set TICKERPRESS_SMTP_HOST, "
                    "TICKERPRESS_EMAIL_FROM and TICKERPRESS_EMAIL_TO"
                )
            return notifier
        webhook = WebhookNotifier.from_env()
        if webhook is None:
            raise UnknownChannelError(
                "webhook channel is not configured: set TICKERPRESS_WEBHOOK_URL"
            )
        return webhook


@dataclass
class _IngestCounters:
    articles_new: int = 0
    candidates_total: int = 0
    mentions_accepted: int = 0
    stories_new: int = 0
    alerts_sent: int = 0
    feed_results: list[FeedResult] = field(default_factory=list)


class TickerPressService:
    """The application service used by both the API and the CLI."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        clock: Clock | None = None,
        feed_source: FeedSource | None = None,
        notifiers: NotifierRegistry | None = None,
        lexicons: Lexicons | None = None,
        engine_version: str = __version__,
    ) -> None:
        self.repository = repository
        self.clock = clock or SystemClock()
        self.feed_source = feed_source or FixtureFeedSource()
        self.notifiers = notifiers or NotifierRegistry()
        self.lexicons = lexicons or load_lexicons()
        self.engine_version = engine_version
        self.template = digest_engine.parse_template(self.lexicons.digest_template)
        self._shingle_cache: dict[int, frozenset[dedup.Shingle]] = {}

    # ------------------------------------------------------------------
    # watchlist (FR-1)
    # ------------------------------------------------------------------

    def _now(self, now: datetime | None) -> datetime:
        return now if now is not None else self.clock.now()

    def add_company(
        self,
        ticker: str,
        name: str,
        *,
        mode: DeliveryMode = DeliveryMode.DIGEST,
        min_relevance: int = 20,
        alert_min_relevance: int = 60,
        context_terms: Sequence[str] = (),
        anti_terms: Sequence[str] = (),
        auto_alias: bool = True,
        extra_aliases: Sequence[str] = (),
        now: datetime | None = None,
    ) -> tuple[Company, list[Alias]]:
        """Create a company and its generated aliases (US-1)."""

        moment = self._now(now)
        company = Company(
            ticker=normalize_ticker(ticker),
            name=name,
            mode=mode,
            min_relevance=min_relevance,
            alert_min_relevance=alert_min_relevance,
            context_terms=list(context_terms),
            anti_terms=list(anti_terms),
            created_at=moment,
        )
        aliases: list[Alias] = []
        with self.repository.transaction():
            self.repository.add_company(company)
            if auto_alias:
                for alias in generate_aliases(company.ticker, company.name, moment, self.lexicons):
                    try:
                        # e.g. a name that is just the ticker collides with it
                        check_duplicate_surface(aliases, alias.text, alias.kind)
                    except DuplicateAliasError:
                        continue
                    aliases.append(self.repository.add_alias(alias))
            for text in extra_aliases:
                aliases.append(self.add_alias(company.ticker, text, existing=aliases, now=moment))
        return company, aliases

    def add_alias(
        self,
        ticker: str,
        text: str,
        *,
        kind: AliasKind | None = None,
        strength: Strength | None = None,
        prior: float | None = None,
        existing: Sequence[Alias] | None = None,
        now: datetime | None = None,
    ) -> Alias:
        """Add one alias, rejecting duplicate case-folded surfaces (FR-1)."""

        company_ticker = normalize_ticker(ticker)
        if self.repository.get_company(company_ticker) is None:
            raise UnknownCompanyError(company_ticker)
        current = (
            list(existing) if existing is not None else self.repository.list_aliases(company_ticker)
        )
        resolved_kind = kind if kind is not None else self._infer_kind(company_ticker, text)
        check_duplicate_surface(current, text, resolved_kind)
        resolved_strength = (
            strength
            if strength is not None
            else default_strength(resolved_kind, text, self.lexicons)
        )
        resolved_prior = (
            prior if prior is not None else default_prior(resolved_kind, resolved_strength)
        )
        alias = Alias(
            company_ticker=company_ticker,
            text=text,
            kind=resolved_kind,
            strength=resolved_strength,
            prior=resolved_prior,
            generated=False,
            created_at=self._now(now),
        )
        return self.repository.add_alias(alias)

    @staticmethod
    def _infer_kind(ticker: str, text: str) -> AliasKind:
        cleaned = " ".join(text.split())
        if cleaned == f"${ticker}":
            return AliasKind.CASHTAG
        if cleaned == ticker:
            return AliasKind.TICKER_SYMBOL
        return AliasKind.NICKNAME

    def remove_alias(self, ticker: str, alias_id: int) -> bool:
        alias = self.repository.get_alias(alias_id)
        if alias is None or alias.company_ticker != normalize_ticker(ticker):
            return False
        return self.repository.delete_alias(alias_id)

    def add_term(
        self, ticker: str, *, context: str | None = None, anti: str | None = None
    ) -> Company:
        """Append a per-company context or anti term (FR-1)."""

        company = self.get_company(ticker)
        updates: dict[str, list[str]] = {}
        if context:
            updates["context_terms"] = [*company.context_terms, context]
        if anti:
            updates["anti_terms"] = [*company.anti_terms, anti]
        if not updates:
            return company
        updated = company.model_copy(update=updates)
        # re-validate so the terms are normalized and de-duplicated
        updated = Company.model_validate(updated.model_dump())
        return self.repository.update_company(updated)

    def get_company(self, ticker: str) -> Company:
        company = self.repository.get_company(normalize_ticker(ticker))
        if company is None:
            raise UnknownCompanyError(ticker)
        return company

    def set_company(
        self,
        ticker: str,
        *,
        name: str | None = None,
        mode: DeliveryMode | None = None,
        min_relevance: int | None = None,
        alert_min_relevance: int | None = None,
        context_terms: Sequence[str] | None = None,
        anti_terms: Sequence[str] | None = None,
    ) -> Company:
        company = self.get_company(ticker)
        updates: dict[str, object] = {}
        if name is not None:
            updates["name"] = name
        if mode is not None:
            updates["mode"] = mode
        if min_relevance is not None:
            updates["min_relevance"] = min_relevance
        if alert_min_relevance is not None:
            updates["alert_min_relevance"] = alert_min_relevance
        if context_terms is not None:
            updates["context_terms"] = list(context_terms)
        if anti_terms is not None:
            updates["anti_terms"] = list(anti_terms)
        if not updates:
            return company
        updated = Company.model_validate({**company.model_dump(), **updates})
        return self.repository.update_company(updated)

    def remove_company(self, ticker: str) -> bool:
        return self.repository.delete_company(normalize_ticker(ticker))

    # ------------------------------------------------------------------
    # feeds (FR-2)
    # ------------------------------------------------------------------

    def add_feed(self, name: str, url: str, *, now: datetime | None = None) -> Feed:
        return self.repository.add_feed(
            Feed(name=name, url=url, enabled=True, created_at=self._now(now))
        )

    def set_feed_enabled(self, feed_id: int, enabled: bool) -> Feed:
        return self.set_feed(feed_id, enabled=enabled)

    def set_feed(
        self,
        feed_id: int,
        *,
        name: str | None = None,
        url: str | None = None,
        enabled: bool | None = None,
    ) -> Feed:
        """Update the mutable registry fields of one feed (FR-2)."""

        feed = self.repository.get_feed(feed_id)
        if feed is None:
            raise UnknownFeedError(feed_id)
        updates: dict[str, object] = {}
        if name is not None:
            updates["name"] = name
        if url is not None:
            updates["url"] = url
        if enabled is not None:
            updates["enabled"] = enabled
        if not updates:
            return feed
        updated = Feed.model_validate({**feed.model_dump(), **updates})
        return self.repository.update_feed(updated)

    # ------------------------------------------------------------------
    # ingest (FR-2 .. FR-8, FR-10)
    # ------------------------------------------------------------------

    def _shingles_for(self, article_id: int, title: str, summary: str, content: str | None):
        cached = self._shingle_cache.get(article_id)
        if cached is None:
            cached = dedup.shingles_from_texts(title, summary, content, self.lexicons)
            self._shingle_cache[article_id] = cached
        return cached

    def _dedup_views(self, published_at: datetime) -> list[dedup.StoredArticleView]:
        rows = self.repository.articles_in_window(published_at, dedup.DEDUP_WINDOW_DAYS)
        return [
            dedup.StoredArticleView(
                article_id=row.article_id,
                story_id=row.story_id,
                published_at=row.published_at,
                canonical_url=row.canonical_url,
                content_sha256=row.content_sha256,
                shingles=self._shingles_for(row.article_id, row.title, row.summary, row.content),
            )
            for row in rows
        ]

    def _archive_item(
        self,
        feed: Feed,
        prepared: pipeline.PreparedArticle,
        matcher,
        companies: Mapping[str, Company],
        now: datetime,
        counters: _IngestCounters,
    ) -> Article:
        """Insert one article with its story, mentions and appearances atomically."""

        assert feed.id is not None
        with self.repository.transaction():
            decision = dedup.assign_story(
                dedup.PendingArticle(
                    published_at=prepared.published_at,
                    canonical_url=prepared.canonical_url,
                    content_sha256=prepared.content_sha256,
                    shingles=prepared.shingles,
                ),
                self._dedup_views(prepared.published_at),
            )
            opened_new = decision.opens_new_story
            story_id = (
                self.repository.create_story(now, prepared.published_at, 0)
                if opened_new
                else int(decision.story_id or 0)
            )
            article = self.repository.insert_article(
                Article(
                    feed_id=feed.id,
                    item_guid=prepared.item_guid,
                    url=prepared.url,
                    canonical_url=prepared.canonical_url,
                    title=prepared.title,
                    summary=prepared.summary,
                    content=prepared.content,
                    published_at=prepared.published_at,
                    published_source=prepared.published_source,
                    first_seen_at=now,
                    last_seen_at=now,
                    content_sha256=prepared.content_sha256,
                    token_count=prepared.token_count,
                    content_token_count=prepared.content_token_count,
                    story_id=story_id,
                    dedup_similarity=decision.similarity,
                )
            )
            assert article.id is not None
            self._shingle_cache[article.id] = prepared.shingles

            story = self.repository.get_story(story_id)
            assert story is not None
            if opened_new:
                self.repository.update_story(
                    story.model_copy(
                        update={
                            "first_published_at": prepared.published_at,
                            "representative_article_id": article.id,
                        }
                    )
                )
                counters.stories_new += 1
            elif prepared.published_at < story.first_published_at:
                # The only permitted story mutation: an earlier copy arrived late.
                self.repository.update_story(
                    story.model_copy(
                        update={
                            "first_published_at": prepared.published_at,
                            "representative_article_id": article.id,
                        }
                    )
                )

            analysis = pipeline.analyze_article(
                article.id,
                prepared,
                matcher,
                companies,
                self.lexicons,
                engine_version=self.engine_version,
            )
            self.repository.insert_mentions(analysis.mentions)
            self.repository.insert_appearances(analysis.appearances)

        counters.articles_new += 1
        counters.candidates_total += analysis.candidates_total
        counters.mentions_accepted += analysis.mentions_accepted
        return article

    def _record_fetch_state(self, feed: Feed, result: FetchResult, now: datetime) -> Feed:
        return self.repository.update_feed(
            feed.model_copy(
                update={
                    "etag": result.etag if result.etag is not None else feed.etag,
                    "last_modified": (
                        result.last_modified
                        if result.last_modified is not None
                        else feed.last_modified
                    ),
                    "last_polled_at": now,
                    "last_status": result.status,
                }
            )
        )

    def ingest(
        self,
        *,
        now: datetime | None = None,
        feed_name: str | None = None,
        deliver_alerts: bool = True,
        alert_channel: Channel = Channel.CONSOLE,
    ) -> IngestRun:
        """One ingest pass over the enabled feeds, in ascending feed id (FR-2).

        A broken feed never aborts the run: its error is recorded in the
        IngestRun and the remaining feeds complete.
        """

        moment = self._now(now)
        companies = {company.ticker: company for company in self.repository.list_companies()}
        matcher = compile_matcher(
            list(companies.values()), self.repository.list_aliases(), self.lexicons
        )

        run = self.repository.start_ingest_run(
            IngestRun(
                started_at=moment,
                status=IngestStatus.SUCCEEDED,
                engine_version=self.engine_version,
            )
        )
        counters = _IngestCounters()

        feeds = self.repository.list_feeds(enabled_only=True)
        if feed_name is not None:
            feeds = [feed for feed in feeds if feed.name == feed_name]

        for feed in feeds:
            assert feed.id is not None
            try:
                result = self.feed_source.fetch(feed, moment)
            except Exception as exc:
                result = FetchResult.failure(f"{type(exc).__name__}: {exc}")
            self._record_fetch_state(feed, result, moment)

            if result.status is FetchStatus.NOT_MODIFIED:
                counters.feed_results.append(
                    FeedResult(feed_id=feed.id, status=FetchStatus.NOT_MODIFIED)
                )
                continue
            if result.status is FetchStatus.ERROR or result.raw_bytes is None:
                counters.feed_results.append(
                    FeedResult(
                        feed_id=feed.id,
                        status=FetchStatus.ERROR,
                        error=result.error or "no feed bytes returned",
                    )
                )
                continue

            try:
                parsed = parse_feed(result.raw_bytes)
            except FeedParseError as exc:
                counters.feed_results.append(
                    FeedResult(feed_id=feed.id, status=FetchStatus.ERROR, error=str(exc))
                )
                continue

            items_new = 0
            for item in parsed.items:
                existing = self.repository.get_article_by_guid(feed.id, item.identity)
                if existing is not None and existing.id is not None:
                    # First-ingested content wins (D16); only last_seen_at moves.
                    self.repository.touch_article(existing.id, moment)
                    continue
                prepared = pipeline.prepare_article(item, moment, self.lexicons)
                self._archive_item(feed, prepared, matcher, companies, moment, counters)
                items_new += 1

            counters.feed_results.append(
                FeedResult(
                    feed_id=feed.id,
                    status=FetchStatus.OK,
                    items_seen=len(parsed.items),
                    items_new=items_new,
                    skipped=len(parsed.skipped),
                )
            )

        if deliver_alerts:
            counters.alerts_sent = len(self.deliver_alerts(alert_channel, now=moment))

        errored = sum(1 for result in counters.feed_results if result.status is FetchStatus.ERROR)
        if errored and errored == len(counters.feed_results):
            status = IngestStatus.FAILED
        elif errored:
            status = IngestStatus.PARTIAL
        else:
            status = IngestStatus.SUCCEEDED

        finished = run.model_copy(
            update={
                "finished_at": self._now(now),
                "status": status,
                "feed_results": counters.feed_results,
                "articles_new": counters.articles_new,
                "candidates_total": counters.candidates_total,
                "mentions_accepted": counters.mentions_accepted,
                "stories_new": counters.stories_new,
                "alerts_sent": counters.alerts_sent,
            }
        )
        return self.repository.finish_ingest_run(finished)

    # ------------------------------------------------------------------
    # delivery (FR-9 .. FR-12)
    # ------------------------------------------------------------------

    def _candidates(
        self, channel: Channel, companies: Mapping[str, Company] | None = None
    ) -> tuple[list[digest_engine.DigestCandidate], dict[int, UndeliveredStory]]:
        known = companies or {c.ticker: c for c in self.repository.list_companies()}
        candidates: list[digest_engine.DigestCandidate] = []
        rows: dict[int, UndeliveredStory] = {}
        for row in self.repository.undelivered_stories(channel):
            company = known.get(row.company_ticker)
            if company is None:
                continue
            rows[row.story_id] = row
            candidates.append(
                digest_engine.DigestCandidate(
                    company=company,
                    story_id=row.story_id,
                    relevance=row.relevance,
                    article_id=row.article_id,
                    title=row.title,
                    url=row.url,
                    outlet=row.outlet,
                    published_at=row.published_at,
                    first_published_at=row.first_published_at,
                    copy_count=row.copy_count,
                    matched_surfaces=row.matched_surfaces,
                )
            )
        return candidates, rows

    def _deliver(
        self,
        body: digest_engine.ComposedBody,
        channel: Channel,
        kind: DeliveryKind,
        now: datetime,
    ) -> Delivery:
        """compose -> persist -> send -> mark (FR-11), in that order.

        A crash between persist and send leaves the row ``composed`` and its
        items uncounted: the body is retained for audit and the stories stay
        eligible, which is the failure mode a personal tool should have.

        The notifier is resolved *before* anything is persisted: an
        unconfigured live channel is a configuration error, not a delivery
        attempt, and must not leave a ``failed`` row in the audit history.
        """

        notifier = self.notifiers.get(channel)
        delivery, _ = self.repository.record_delivery(
            Delivery(
                channel=channel,
                kind=kind,
                created_at=now,
                status=DeliveryStatus.COMPOSED,
                subject=body.subject,
                body_text=body.body_text,
            ),
            [
                DeliveryItem(
                    delivery_id=0,
                    channel=channel,
                    company_ticker=item.company.ticker,
                    story_id=item.story_id,
                    article_id=item.article_id,
                    relevance=item.relevance,
                )
                for item in body.items
            ],
        )
        assert delivery.id is not None

        message = ComposedMessage(
            channel=channel,
            kind=kind,
            delivery_id=delivery.id,
            subject=body.subject,
            body_text=body.body_text,
            created_at=now,
            items=tuple(
                MessageItem(
                    ticker=item.company.ticker,
                    story_id=item.story_id,
                    article_id=item.article_id,
                    url=item.url,
                    title=item.title,
                    relevance=item.relevance,
                    published_at=item.published_at,
                )
                for item in body.items
            ),
        )
        try:
            notifier.deliver(message)
        except Exception as exc:
            return self.repository.mark_delivery_failed(delivery.id, f"{type(exc).__name__}: {exc}")
        return self.repository.mark_delivery_sent(delivery.id)

    def run_digest(
        self,
        channel: Channel = Channel.CONSOLE,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> DigestResult:
        """Compose and deliver one digest (FR-9).

        A dry run renders the body and persists **nothing** — no Delivery row,
        no DeliveryItem rows, no notifier call — so it never consumes a story.
        """

        moment = self._now(now)
        candidates, rows = self._candidates(channel)
        body = digest_engine.compose_digest(candidates, moment, self.template)
        if body is None:
            return DigestResult(delivery=None, body=None, dry_run=dry_run)
        selected = tuple(rows[item.story_id] for item in body.items)
        if dry_run:
            return DigestResult(delivery=None, body=body.body_text, items=selected, dry_run=True)
        delivery = self._deliver(body, channel, DeliveryKind.DIGEST, moment)
        return DigestResult(delivery=delivery, body=body.body_text, items=selected)

    def deliver_alerts(
        self, channel: Channel = Channel.CONSOLE, *, now: datetime | None = None
    ) -> list[Delivery]:
        """Deliver one alert per eligible (company, story) pair (FR-10).

        Alerts and digests share one ledger, so a story alerted on a channel is
        excluded from that channel's next digest for that company.
        """

        moment = self._now(now)
        candidates, _ = self._candidates(channel)
        delivered: list[Delivery] = []
        for candidate in digest_engine.select_alert_candidates(candidates):
            body = digest_engine.compose_alert(candidate, moment, self.template)
            delivered.append(self._deliver(body, channel, DeliveryKind.ALERT, moment))
        return delivered

    # ------------------------------------------------------------------
    # explain (FR-13)
    # ------------------------------------------------------------------

    def explain(self, article_id: int, *, company: str | None = None) -> Explanation:
        """Assemble the explain view from persisted rows — never recomputed."""

        article = self.repository.get_article(article_id)
        if article is None:
            raise UnknownArticleError(article_id)
        story = self.repository.get_story(article.story_id)
        if story is None:  # pragma: no cover - story_id is NOT NULL and app-enforced
            raise KeyError(f"article {article_id} references missing story {article.story_id}")
        ticker = normalize_ticker(company) if company else None
        mentions = self.repository.list_mentions(article_id, company=ticker)
        candidates = tuple(
            MentionExplanation(
                mention=mention,
                alias=(
                    self.repository.get_alias(mention.alias_id)
                    if mention.alias_id is not None
                    else None
                ),
            )
            for mention in mentions
        )
        appearances = tuple(self.repository.list_appearances(article_id=article_id, company=ticker))
        return Explanation(
            article=article,
            story=story,
            joined_existing_story=article.dedup_similarity is not None,
            dedup_similarity=article.dedup_similarity,
            story_copy_count=self.repository.story_copy_count(article.story_id),
            candidates=candidates,
            appearances=appearances,
        )

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------

    def story_relevance(self, story_id: int, ticker: str) -> int | None:
        """Derived story relevance for a company: MAX over member appearances."""

        target = normalize_ticker(ticker)
        for row in self.repository.story_relevances(company=target):
            if row.story_id == story_id:
                return row.relevance
        return None

    def compile_watchlist_matcher(self):
        """Expose the compiled matcher (used by ``explain`` tooling and evals)."""

        return compile_matcher(
            self.repository.list_companies(), self.repository.list_aliases(), self.lexicons
        )


def load_watchlist(
    service: TickerPressService, entries: Iterable[Mapping[str, object]], now: datetime
) -> None:
    """Bulk-create companies from a watchlist mapping (fixtures, eval runner).

    Each entry accepts ``ticker``, ``name`` and the optional delivery settings,
    plus an ``aliases`` list of ``{"text", "kind", "strength", "prior"}`` maps.
    """

    for entry in entries:
        aliases = entry.get("aliases") or []
        company, generated = service.add_company(
            str(entry["ticker"]),
            str(entry["name"]),
            mode=DeliveryMode(str(entry.get("mode", "digest"))),
            min_relevance=int(entry.get("min_relevance", 20)),  # type: ignore[arg-type]
            alert_min_relevance=int(entry.get("alert_min_relevance", 60)),  # type: ignore[arg-type]
            context_terms=list(entry.get("context_terms", [])),  # type: ignore[arg-type]
            anti_terms=list(entry.get("anti_terms", [])),  # type: ignore[arg-type]
            auto_alias=bool(entry.get("auto_alias", True)),
            now=now,
        )
        known = list(generated)
        for alias in aliases:  # type: ignore[assignment]
            text = str(alias["text"])
            kind = AliasKind(str(alias["kind"])) if alias.get("kind") else None
            try:
                check_duplicate_surface(known, text, kind)
            except DuplicateAliasError:
                continue
            strength = Strength(str(alias["strength"])) if alias.get("strength") else None
            prior = float(alias["prior"]) if alias.get("prior") is not None else None
            known.append(
                service.add_alias(
                    company.ticker,
                    text,
                    kind=kind,
                    strength=strength,
                    prior=prior,
                    existing=known,
                    now=now,
                )
            )
