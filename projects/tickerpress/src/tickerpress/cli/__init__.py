"""Typer CLI (SCOPE FR-15).

Thin, like the API: every command parses its arguments, calls one
:class:`~tickerpress.services.TickerPressService` method and prints the result.
``--now ISO`` is accepted wherever time matters (SCOPE D12: time is an input),
``--json`` on every list/show command, and every failure exits non-zero.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from .. import __version__
from ..adapters.clock import SystemClock
from ..api.deps import resolve_db_path
from ..engine.models import (
    AliasKind,
    Channel,
    DeliveryMode,
    Strength,
    iso_utc,
    normalize_ticker,
    parse_iso_utc,
)
from ..resources import lexicon_digests, lexicon_entry_counts, load_lexicons
from ..services import NotifierRegistry, TickerPressService
from ..store.sqlite_store import SQLiteRepository

__all__ = ["app", "main"]

EXIT_ERROR = 1

app = typer.Typer(
    name="tickerpress",
    help=(
        "Watchlist news watcher: detect company appearances, collapse syndicated "
        "copies, deliver ranked digests exactly once. Informational only — not "
        "investment advice."
    ),
    no_args_is_help=True,
    add_completion=False,
)
company_app = typer.Typer(help="Manage the watchlist (FR-1).", no_args_is_help=True)
alias_app = typer.Typer(help="Manage a company's matchable surfaces (FR-1).", no_args_is_help=True)
term_app = typer.Typer(help="Per-company context / anti terms (FR-1, FR-6).", no_args_is_help=True)
feed_app = typer.Typer(help="Manage the feed registry (FR-2).", no_args_is_help=True)
articles_app = typer.Typer(help="Browse the article archive (FR-4).", no_args_is_help=True)
stories_app = typer.Typer(help="Browse deduplicated stories (FR-7).", no_args_is_help=True)
digest_app = typer.Typer(help="Compose and inspect digests (FR-9, FR-11).", no_args_is_help=True)

app.add_typer(company_app, name="company")
app.add_typer(alias_app, name="alias")
app.add_typer(term_app, name="term")
app.add_typer(feed_app, name="feed")
app.add_typer(articles_app, name="articles")
app.add_typer(stories_app, name="stories")
app.add_typer(digest_app, name="digest")


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@dataclass
class CliState:
    """What every command needs: where the archive is, and how to open it."""

    db_path: Path
    outbox_dir: Path
    service: TickerPressService | None = None

    def open(self, *, initialize: bool = True) -> TickerPressService:
        """Open the archive. The schema is created on demand — the DDL is
        ``CREATE TABLE IF NOT EXISTS`` throughout, so this is idempotent and a
        first command never fails with "no such table"."""

        if self.service is None:
            repository = SQLiteRepository(self.db_path)
            if initialize:
                repository.initialize()
            self.service = TickerPressService(
                repository,
                clock=SystemClock(),
                notifiers=NotifierRegistry(outbox_dir=self.outbox_dir),
            )
        return self.service


def _state(ctx: typer.Context) -> CliState:
    state = ctx.obj
    if not isinstance(state, CliState):  # pragma: no cover - callback always runs
        raise typer.Exit(code=EXIT_ERROR)
    return state


def _service(ctx: typer.Context, *, initialize: bool = True) -> TickerPressService:
    state = _state(ctx)
    try:
        return state.open(initialize=initialize)
    except Exception as exc:  # pragma: no cover - unreadable/locked database
        _fail(f"cannot open archive at {state.db_path}: {exc}")


def _fail(message: str) -> Any:
    """Print to stderr and exit non-zero (FR-15)."""

    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=EXIT_ERROR)


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=False, default=str))


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_iso_utc(value)
    except ValueError:
        return _fail(f"--now must be an ISO-8601 timestamp, got {value!r}")


def _ticker(value: str) -> str:
    try:
        return normalize_ticker(value)
    except ValueError as exc:
        return _fail(str(exc))


@app.callback()
def main_callback(
    ctx: typer.Context,
    db: str | None = typer.Option(
        None,
        "--db",
        help="SQLite archive path (default: $TICKERPRESS_DB or ~/.tickerpress/tickerpress.db).",
    ),
    outbox: str | None = typer.Option(
        None, "--outbox", help="Directory the file channel writes into (default: ./outbox)."
    ),
) -> None:
    """Set up the shared context for every command."""

    ctx.obj = CliState(
        db_path=resolve_db_path(db),
        outbox_dir=Path(outbox).expanduser() if outbox else Path.cwd() / "outbox",
    )


@app.command()
def version() -> None:
    """Print the package version."""

    typer.echo(__version__)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")) -> None:
    """Create the schema and report the committed lexicons. Idempotent."""

    state = _state(ctx)
    service = _service(ctx, initialize=True)
    service.repository.initialize()
    try:
        lexicons = load_lexicons()
        counts = lexicon_entry_counts(lexicons)
        digests = lexicon_digests()
    except (FileNotFoundError, ValueError) as exc:
        return _fail(f"lexicons failed to load: {exc}")

    if as_json:
        _echo_json(
            {
                "db": str(state.db_path),
                "engine_version": __version__,
                "lexicons": [
                    {"file": name, "entries": counts[name], "sha256": digests[name]}
                    for name in sorted(counts)
                ],
            }
        )
        return
    typer.echo(f"archive: {state.db_path}")
    typer.echo(f"engine:  {__version__}")
    typer.echo("lexicons:")
    for name in sorted(counts):
        typer.echo(f"  {name:<22} {counts[name]:>5} entries  sha256:{digests[name][:16]}…")


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------


def _company_payload(service: TickerPressService, ticker: str) -> dict[str, Any]:
    company = service.get_company(ticker)
    aliases = service.repository.list_aliases(company.ticker)
    return {
        **company.model_dump(mode="json"),
        "aliases": [alias.model_dump(mode="json") for alias in aliases],
    }


@company_app.command("add")
def company_add(
    ctx: typer.Context,
    ticker: str = typer.Argument(..., help="Ticker, e.g. TSLA or BRK.B."),
    name: str = typer.Option(..., "--name", help='Display / legal name, e.g. "Tesla, Inc."'),
    alias: list[str] = typer.Option([], "--alias", help="Extra alias surface (repeatable)."),
    mode: DeliveryMode = typer.Option(DeliveryMode.DIGEST, "--mode"),
    min_relevance: int = typer.Option(20, "--min-relevance", min=0, max=100),
    alert_min_relevance: int = typer.Option(60, "--alert-min-relevance", min=0, max=100),
    context: list[str] = typer.Option([], "--context", help="Per-company context term."),
    anti: list[str] = typer.Option([], "--anti", help="Per-company anti term."),
    auto_alias: bool = typer.Option(True, "--auto-alias/--no-auto-alias"),
    now: str | None = typer.Option(None, "--now", help="ISO timestamp to record as created_at."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Add a company and its generated aliases (US-1)."""

    service = _service(ctx, initialize=True)
    try:
        company, aliases = service.add_company(
            ticker,
            name,
            mode=mode,
            min_relevance=min_relevance,
            alert_min_relevance=alert_min_relevance,
            context_terms=context,
            anti_terms=anti,
            auto_alias=auto_alias,
            extra_aliases=alias,
            now=_parse_now(now),
        )
    except Exception as exc:
        return _fail(f"cannot add {ticker}: {exc}")

    if as_json:
        _echo_json(_company_payload(service, company.ticker))
        return
    typer.echo(f"added {company.ticker} — {company.name} (mode={company.mode.value})")
    for item in aliases:
        typer.echo(
            f"  alias {item.id:>3}  {item.text:<24} {item.kind.value:<14} "
            f"{item.strength.value:<6} prior={item.prior:.2f}"
        )


@company_app.command("list")
def company_list(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")) -> None:
    """List the watchlist."""

    service = _service(ctx)
    companies = service.repository.list_companies()
    if as_json:
        _echo_json([company.model_dump(mode="json") for company in companies])
        return
    if not companies:
        typer.echo("watchlist is empty — try: tickerpress company add AAPL --name 'Apple Inc.'")
        return
    typer.echo(f"{'TICKER':<8}{'MODE':<8}{'MIN':>4}{'ALERT':>7}  NAME")
    for company in companies:
        typer.echo(
            f"{company.ticker:<8}{company.mode.value:<8}{company.min_relevance:>4}"
            f"{company.alert_min_relevance:>7}  {company.name}"
        )


@company_app.command("show")
def company_show(
    ctx: typer.Context,
    ticker: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one company with every alias, kind, strength and prior (US-1)."""

    service = _service(ctx)
    try:
        payload = _company_payload(service, ticker)
    except KeyError:
        return _fail(f"unknown company {_ticker(ticker)}")
    if as_json:
        _echo_json(payload)
        return
    typer.echo(f"{payload['ticker']} — {payload['name']}")
    typer.echo(
        f"  mode={payload['mode']}  min_relevance={payload['min_relevance']}"
        f"  alert_min_relevance={payload['alert_min_relevance']}"
    )
    typer.echo(f"  context_terms: {', '.join(payload['context_terms']) or '-'}")
    typer.echo(f"  anti_terms:    {', '.join(payload['anti_terms']) or '-'}")
    typer.echo("  aliases:")
    for alias in payload["aliases"]:
        typer.echo(
            f"    {alias['id']:>3}  {alias['text']:<24} {alias['kind']:<14} "
            f"{alias['strength']:<6} prior={alias['prior']:.2f}"
            f"{'  (generated)' if alias['generated'] else ''}"
        )


@company_app.command("set")
def company_set(
    ctx: typer.Context,
    ticker: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name"),
    mode: DeliveryMode | None = typer.Option(None, "--mode"),
    min_relevance: int | None = typer.Option(None, "--min-relevance", min=0, max=100),
    alert_min_relevance: int | None = typer.Option(None, "--alert-min-relevance", min=0, max=100),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Change delivery settings for one company."""

    service = _service(ctx)
    try:
        company = service.set_company(
            ticker,
            name=name,
            mode=mode,
            min_relevance=min_relevance,
            alert_min_relevance=alert_min_relevance,
        )
    except KeyError:
        return _fail(f"unknown company {_ticker(ticker)}")
    if as_json:
        _echo_json(company.model_dump(mode="json"))
        return
    typer.echo(
        f"{company.ticker}: mode={company.mode.value} min_relevance={company.min_relevance} "
        f"alert_min_relevance={company.alert_min_relevance}"
    )


@company_app.command("remove")
def company_remove(ctx: typer.Context, ticker: str = typer.Argument(...)) -> None:
    """Remove a company (the delivery ledger is history and survives)."""

    service = _service(ctx)
    if not service.remove_company(ticker):
        return _fail(f"unknown company {_ticker(ticker)}")
    typer.echo(f"removed {_ticker(ticker)}")


@alias_app.command("add")
def alias_add(
    ctx: typer.Context,
    ticker: str = typer.Argument(...),
    text: str = typer.Argument(..., help="Surface form, with its intended capitalization."),
    kind: AliasKind | None = typer.Option(None, "--kind"),
    strength: Strength | None = typer.Option(None, "--strength"),
    prior: float | None = typer.Option(None, "--prior", min=0.0, max=0.3),
    now: str | None = typer.Option(None, "--now"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Add one alias. Null strength/prior take the kind defaults."""

    service = _service(ctx)
    try:
        alias = service.add_alias(
            ticker, text, kind=kind, strength=strength, prior=prior, now=_parse_now(now)
        )
    except KeyError:
        return _fail(f"unknown company {_ticker(ticker)}")
    except ValueError as exc:
        return _fail(str(exc))
    if as_json:
        _echo_json(alias.model_dump(mode="json"))
        return
    typer.echo(
        f"{alias.company_ticker}: alias {alias.id} {alias.text!r} "
        f"({alias.kind.value}, {alias.strength.value}, prior={alias.prior:.2f})"
    )


@alias_app.command("rm")
def alias_rm(
    ctx: typer.Context,
    ticker: str = typer.Argument(...),
    alias_id: int = typer.Argument(...),
) -> None:
    """Remove one alias by id (existing mentions keep their history)."""

    service = _service(ctx)
    if not service.remove_alias(ticker, alias_id):
        return _fail(f"unknown alias {alias_id} for {_ticker(ticker)}")
    typer.echo(f"removed alias {alias_id} from {_ticker(ticker)}")


@term_app.command("add")
def term_add(
    ctx: typer.Context,
    ticker: str = typer.Argument(...),
    context: str | None = typer.Option(None, "--context", help="Positive evidence term."),
    anti: str | None = typer.Option(None, "--anti", help="Negative evidence term."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Append a per-company context or anti term (FR-6 features 7 and 8)."""

    if context is None and anti is None:
        return _fail("pass --context WORD and/or --anti WORD")
    service = _service(ctx)
    try:
        company = service.add_term(ticker, context=context, anti=anti)
    except KeyError:
        return _fail(f"unknown company {_ticker(ticker)}")
    if as_json:
        _echo_json(company.model_dump(mode="json"))
        return
    typer.echo(
        f"{company.ticker}: context_terms={company.context_terms} anti_terms={company.anti_terms}"
    )


# ---------------------------------------------------------------------------
# feeds
# ---------------------------------------------------------------------------


@feed_app.command("add")
def feed_add(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="file:// for fixtures, http(s):// for the live adapter."),
    name: str = typer.Option(..., "--name", help="Outlet name shown in digests."),
    now: str | None = typer.Option(None, "--now"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Register a feed."""

    service = _service(ctx, initialize=True)
    try:
        feed = service.add_feed(name, url, now=_parse_now(now))
    except Exception as exc:
        return _fail(f"cannot add feed: {exc}")
    if as_json:
        _echo_json(feed.model_dump(mode="json"))
        return
    typer.echo(f"added feed {feed.id}: {feed.name} -> {feed.url}")


@feed_app.command("list")
def feed_list(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")) -> None:
    """List registered feeds and their polling state."""

    service = _service(ctx)
    feeds = service.repository.list_feeds()
    if as_json:
        _echo_json([feed.model_dump(mode="json") for feed in feeds])
        return
    if not feeds:
        typer.echo("no feeds registered — try: tickerpress feed add <url> --name 'Wire One'")
        return
    typer.echo(f"{'ID':>3}  {'ENABLED':<8}{'STATUS':<14}{'NAME':<20}URL")
    for feed in feeds:
        typer.echo(
            f"{feed.id:>3}  {'yes' if feed.enabled else 'no':<8}"
            f"{(feed.last_status.value if feed.last_status else '-'):<14}{feed.name:<20}{feed.url}"
        )


@feed_app.command("rm")
def feed_rm(ctx: typer.Context, feed_id: int = typer.Argument(...)) -> None:
    """Delete a feed (refused while it still has archived articles)."""

    service = _service(ctx)
    try:
        removed = service.repository.delete_feed(feed_id)
    except ValueError as exc:
        return _fail(str(exc))
    if not removed:
        return _fail(f"unknown feed {feed_id}")
    typer.echo(f"removed feed {feed_id}")


@feed_app.command("enable")
def feed_enable(ctx: typer.Context, feed_id: int = typer.Argument(...)) -> None:
    """Enable a feed."""

    _set_feed_enabled(ctx, feed_id, True)


@feed_app.command("disable")
def feed_disable(ctx: typer.Context, feed_id: int = typer.Argument(...)) -> None:
    """Disable a feed (ingest skips it; its archive stays)."""

    _set_feed_enabled(ctx, feed_id, False)


def _set_feed_enabled(ctx: typer.Context, feed_id: int, enabled: bool) -> None:
    service = _service(ctx)
    try:
        feed = service.set_feed_enabled(feed_id, enabled)
    except KeyError:
        _fail(f"unknown feed {feed_id}")
        return
    typer.echo(f"feed {feed.id} ({feed.name}) {'enabled' if feed.enabled else 'disabled'}")


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    ctx: typer.Context,
    feed: str | None = typer.Option(None, "--feed", help="Restrict the run to one feed name."),
    now: str | None = typer.Option(None, "--now", help="ISO timestamp used as the ingest instant."),
    alerts: bool = typer.Option(True, "--alerts/--no-alerts"),
    alert_channel: Channel = typer.Option(Channel.CONSOLE, "--alert-channel"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch every enabled feed, archive, score and alert (FR-2 … FR-10)."""

    service = _service(ctx)
    if feed is not None and service.repository.get_feed_by_name(feed) is None:
        return _fail(f"unknown feed {feed!r}")
    try:
        run = service.ingest(
            now=_parse_now(now),
            feed_name=feed,
            deliver_alerts=alerts,
            alert_channel=alert_channel,
        )
    except Exception as exc:
        return _fail(f"ingest failed: {exc}")

    if as_json:
        _echo_json(run.model_dump(mode="json"))
        return
    typer.echo(
        f"run {run.id} {run.status.value}: {run.articles_new} new articles, "
        f"{run.stories_new} new stories, {run.candidates_total} candidates "
        f"({run.mentions_accepted} accepted), {run.alerts_sent} alerts"
    )
    for result in run.feed_results:
        detail = f" error={result.error}" if result.error else ""
        typer.echo(
            f"  feed {result.feed_id}: {result.status.value} seen={result.items_seen} "
            f"new={result.items_new} skipped={result.skipped}{detail}"
        )
    if run.status is not run.status.SUCCEEDED:
        raise typer.Exit(code=EXIT_ERROR)


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


@articles_app.command("list")
def articles_list(
    ctx: typer.Context,
    company: str | None = typer.Option(None, "--company"),
    since: str | None = typer.Option(None, "--since", help="ISO lower bound on published_at."),
    until: str | None = typer.Option(None, "--until"),
    min_relevance: int | None = typer.Option(None, "--min-relevance", min=0, max=100),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List archived articles, newest first."""

    service = _service(ctx)
    articles = service.repository.list_articles(
        company=_ticker(company) if company else None,
        since=_parse_now(since),
        until=_parse_now(until),
        min_relevance=min_relevance,
        limit=limit,
    )
    if as_json:
        _echo_json([article.model_dump(mode="json") for article in articles])
        return
    if not articles:
        typer.echo("no articles match")
        return
    for article in articles:
        typer.echo(
            f"{article.id:>5}  {iso_utc(article.published_at)}  story={article.story_id:<5} "
            f"{article.title}"
        )


@articles_app.command("show")
def articles_show(
    ctx: typer.Context,
    article_id: int = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one archived article and its appearances."""

    service = _service(ctx)
    article = service.repository.get_article(article_id)
    if article is None:
        return _fail(f"unknown article {article_id}")
    appearances = service.repository.list_appearances(article_id=article_id)
    if as_json:
        _echo_json(
            {
                **article.model_dump(mode="json"),
                "appearances": [a.model_dump(mode="json") for a in appearances],
            }
        )
        return
    typer.echo(f"article {article.id}: {article.title}")
    typer.echo(f"  url:        {article.url}")
    typer.echo(f"  canonical:  {article.canonical_url}")
    typer.echo(f"  published:  {iso_utc(article.published_at)} ({article.published_source.value})")
    typer.echo(f"  story:      {article.story_id} (similarity {article.dedup_similarity})")
    typer.echo(f"  summary:    {article.summary}")
    if article.content:
        typer.echo(f"  content:    {article.content}")
    for appearance in appearances:
        typer.echo(
            f"  appearance {appearance.company_ticker}: relevance {appearance.relevance} "
            f"(title_hit={appearance.title_hit}, lede_hit={appearance.lede_hit}, "
            f"mentions={appearance.mention_count})"
        )


@stories_app.command("list")
def stories_list(
    ctx: typer.Context,
    company: str | None = typer.Option(None, "--company"),
    since: str | None = typer.Option(None, "--since"),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List deduplicated stories, newest first."""

    service = _service(ctx)
    stories = service.repository.list_stories(
        company=_ticker(company) if company else None, since=_parse_now(since), limit=limit
    )
    payload = [
        {
            **story.model_dump(mode="json"),
            "article_count": service.repository.story_copy_count(int(story.id or 0)),
        }
        for story in stories
    ]
    if as_json:
        _echo_json(payload)
        return
    if not payload:
        typer.echo("no stories match")
        return
    for row in payload:
        typer.echo(
            f"{row['id']:>5}  {row['first_published_at']}  copies={row['article_count']:<3} "
            f"representative={row['representative_article_id']}"
        )


@stories_app.command("show")
def stories_show(
    ctx: typer.Context,
    story_id: int = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a story: its members and its per-company relevance."""

    service = _service(ctx)
    story = service.repository.get_story(story_id)
    if story is None:
        return _fail(f"unknown story {story_id}")
    members = service.repository.story_members(story_id)
    relevance = {
        row.company_ticker: row.relevance
        for row in service.repository.story_relevances()
        if row.story_id == story_id
    }
    if as_json:
        _echo_json(
            {
                **story.model_dump(mode="json"),
                "members": [member.model_dump(mode="json") for member in members],
                "relevance": relevance,
            }
        )
        return
    typer.echo(f"story {story.id}: first published {iso_utc(story.first_published_at)}")
    typer.echo(f"  representative article: {story.representative_article_id}")
    for ticker, value in sorted(relevance.items()):
        typer.echo(f"  relevance {ticker}: {value}")
    for member in members:
        typer.echo(f"  article {member.id:>5}  {iso_utc(member.published_at)}  {member.title}")


@app.command()
def explain(
    ctx: typer.Context,
    article_id: int = typer.Argument(...),
    company: str | None = typer.Option(None, "--company", help="Restrict to one ticker."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Why an article matched (or did not) — read from stored rows (FR-13)."""

    service = _service(ctx)
    try:
        explanation = service.explain(article_id, company=company)
    except KeyError:
        return _fail(f"unknown article {article_id}")

    if as_json:
        _echo_json(
            {
                "article_id": explanation.article.id,
                "title": explanation.article.title,
                "story_id": explanation.story.id,
                "joined_existing_story": explanation.joined_existing_story,
                "dedup_similarity": explanation.dedup_similarity,
                "story_copy_count": explanation.story_copy_count,
                "candidates": [
                    {
                        **item.mention.model_dump(mode="json"),
                        "alias_text": item.alias.text if item.alias else None,
                    }
                    for item in explanation.candidates
                ],
                "appearances": [a.model_dump(mode="json") for a in explanation.appearances],
            }
        )
        return

    typer.echo(f"article {explanation.article.id}: {explanation.article.title}")
    if explanation.joined_existing_story:
        typer.echo(
            f"  story {explanation.story.id}: joined at J={explanation.dedup_similarity} "
            f"({explanation.story_copy_count} copies)"
        )
    else:
        typer.echo(
            f"  story {explanation.story.id}: new story ({explanation.story_copy_count} copies)"
        )
    if not explanation.candidates:
        typer.echo("  no candidates scanned for this article")
    for item in explanation.candidates:
        mention = item.mention
        verdict = "ACCEPT" if mention.accepted else "reject"
        typer.echo(
            f"  [{verdict}] {mention.company_ticker} {mention.surface!r} "
            f"({mention.field.value} {mention.char_start}:{mention.char_end}, "
            f"{mention.matched_via.value}, {mention.strength.value}) "
            f"score={mention.score:.3f} threshold={mention.threshold:.2f}"
        )
        if item.alias is not None:
            typer.echo(f"      alias {item.alias.id} {item.alias.text!r} ({item.alias.kind.value})")
        if mention.features:
            parts = ", ".join(f"{key}={value}" for key, value in mention.features.items())
            typer.echo(f"      features: {parts}")
    for appearance in explanation.appearances:
        typer.echo(
            f"  relevance {appearance.company_ticker} = {appearance.relevance} "
            f"(0.50·title {int(appearance.title_hit)} + 0.25·lede {int(appearance.lede_hit)} "
            f"+ 0.25·min(1, {appearance.mention_count}/4))"
        )


# ---------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------


@digest_app.command("run")
def digest_run(
    ctx: typer.Context,
    channel: Channel = typer.Option(Channel.CONSOLE, "--channel"),
    now: str | None = typer.Option(None, "--now"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Render only; persist nothing."),
) -> None:
    """Compose and deliver one digest (FR-9). Empty selections send nothing."""

    service = _service(ctx)
    try:
        result = service.run_digest(channel, now=_parse_now(now), dry_run=dry_run)
    except Exception as exc:
        return _fail(f"digest failed: {exc}")

    if result.empty:
        typer.echo("nothing new to deliver")
        return
    if dry_run:
        typer.echo(result.body or "", nl=False)
        typer.echo(f"[dry run] {len(result.items)} item(s); nothing persisted")
        return
    delivery = result.delivery
    assert delivery is not None
    if delivery.status.value == "failed":
        typer.secho(
            f"delivery {delivery.id} failed: {delivery.error} "
            f"({len(result.items)} stories remain eligible)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)
    typer.echo(
        f"delivery {delivery.id} {delivery.status.value} on {delivery.channel.value}: "
        f"{len(result.items)} item(s) — {delivery.subject}"
    )


@digest_app.command("list")
def digest_list(
    ctx: typer.Context,
    channel: Channel | None = typer.Option(None, "--channel"),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List past deliveries (digests and alerts share one ledger)."""

    service = _service(ctx)
    deliveries = service.repository.list_deliveries(channel=channel, limit=limit)
    if as_json:
        _echo_json([delivery.model_dump(mode="json") for delivery in deliveries])
        return
    if not deliveries:
        typer.echo("no deliveries yet")
        return
    for delivery in deliveries:
        typer.echo(
            f"{delivery.id:>5}  {iso_utc(delivery.created_at)}  {delivery.channel.value:<8}"
            f"{delivery.kind.value:<7}{delivery.status.value:<10}{delivery.subject}"
        )


@digest_app.command("show")
def digest_show(
    ctx: typer.Context,
    delivery_id: int = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one delivery, including the exact body that was sent."""

    service = _service(ctx)
    delivery = service.repository.get_delivery(delivery_id)
    if delivery is None:
        return _fail(f"unknown delivery {delivery_id}")
    items = service.repository.list_delivery_items(delivery_id=delivery_id)
    if as_json:
        _echo_json(
            {
                **delivery.model_dump(mode="json"),
                "items": [item.model_dump(mode="json") for item in items],
            }
        )
        return
    typer.echo(f"delivery {delivery.id} [{delivery.status.value}] {delivery.subject}")
    for item in items:
        typer.echo(
            f"  {item.company_ticker:<8} story={item.story_id:<5} relevance={item.relevance:>3} "
            f"counted={int(item.counted)}"
        )
    typer.echo("")
    typer.echo(delivery.body_text, nl=False)


def main() -> None:
    """Console-script entry point (``tickerpress``)."""

    try:
        app()
    except BrokenPipeError:  # pragma: no cover - piping into head/less
        sys.stderr.close()
        raise SystemExit(EXIT_ERROR) from None
