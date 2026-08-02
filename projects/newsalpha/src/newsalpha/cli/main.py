"""Typer CLI (FR-13).

Same services as the API, human-readable tables by default and a `--json`
escape hatch on every list/show command.  Business rules never live here: each
command parses, calls one service method, and renders.

Exit codes are part of the contract -- `EXIT_BY_CODE` maps the error catalog in
`newsalpha.errors` onto process statuses, so a script can tell "no such signal"
(4) from "the feed is down" (7).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from .. import __version__, errors
from ..adapters import resolve_marketdata, resolve_newsfeed
from ..adapters.newsfeed_fixture import FixtureNewsFeed
from ..engine.normalize import iso_utc
from ..factory import (
    DEFAULT_FIXTURE_FEED,
    DEFAULT_FIXTURE_MARKET,
    build_datasets,
    build_repository,
)
from ..models import BacktestParams, Signal
from ..service import NewsAlphaService

#: Error-catalog code -> process exit status (FR-13).
EXIT_BY_CODE: dict[str, int] = {
    "invalid_request": 2,
    "dataset_invalid": 3,
    "unknown_article": 4,
    "unknown_event": 4,
    "unknown_signal": 4,
    "unknown_brief": 4,
    "unknown_backtest": 4,
    "unknown_asset": 4,
    "not_found": 4,
    "conflict": 5,
    "frame_check_failed": 6,
    "feed_unavailable": 7,
    "market_data_unavailable": 7,
    "internal_error": 1,
}

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "NewsAlpha - typed financial-news events, linked assets, scored signals, "
        "and a leak-free backtest. Decision support, never advice."
    ),
)
articles_app = typer.Typer(no_args_is_help=True, help="Browse ingested articles.")
events_app = typer.Typer(no_args_is_help=True, help="Browse extracted events and their evidence.")
signals_app = typer.Typer(no_args_is_help=True, help="Browse scored signal revisions.")
assets_app = typer.Typer(no_args_is_help=True, help="Browse the committed asset gazetteer.")
watch_app = typer.Typer(no_args_is_help=True, help="Manage the watchlist (FR-11).")
prices_app = typer.Typer(no_args_is_help=True, help="Load daily bars (FR-9).")
backtest_app = typer.Typer(no_args_is_help=True, help="Run and inspect backtests (FR-10).")

app.add_typer(articles_app, name="articles")
app.add_typer(events_app, name="events")
app.add_typer(signals_app, name="signals")
app.add_typer(assets_app, name="assets")
app.add_typer(watch_app, name="watch")
app.add_typer(prices_app, name="prices")
app.add_typer(backtest_app, name="backtest")


# --------------------------------------------------------------------------- #
# Wiring & rendering helpers
# --------------------------------------------------------------------------- #


class _State:
    """The `--db` option, resolved lazily so `--help` never opens a database.

    The service is cached per target path: a real invocation builds it once, and an
    in-process test driver that runs several commands against `--db :memory:` keeps
    talking to the same store instead of silently starting over.
    """

    def __init__(self) -> None:
        self.db_path: str | None = None
        self._service: NewsAlphaService | None = None
        self._service_path: str | None = None

    def configure(self, db_path: str | None) -> None:
        if db_path != self._service_path:
            self._service = None
        self.db_path = db_path

    def reset(self) -> None:
        """Drop the cached service (tests call this between cases)."""
        self._service = None
        self._service_path = None
        self.db_path = None

    def use(self, service: NewsAlphaService) -> None:
        """Inject a pre-wired service (tests)."""
        self._service = service
        self._service_path = self.db_path

    def service(self) -> NewsAlphaService:
        if self._service is None:
            datasets = build_datasets()
            repository = build_repository(self.db_path)
            self._service = NewsAlphaService(repository, datasets)
            self._service_path = self.db_path
        return self._service


state = _State()


def _fail(exc: Exception) -> typer.Exit:
    """Print the catalog error to stderr and exit with its status."""
    error = errors.from_exception(exc)
    typer.secho(f"error [{error.code}]: {error.message}", fg=typer.colors.RED, err=True)
    for name, value in sorted(error.details.items()):
        typer.secho(f"  {name}: {value}", fg=typer.colors.RED, err=True)
    return typer.Exit(EXIT_BY_CODE.get(error.code, 1))


def _now_iso() -> str:
    return iso_utc(datetime.now(UTC))


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    typer.echo(line.rstrip())
    typer.echo("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        typer.echo("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json")


def _signal_row(signal: Signal) -> list[str]:
    return [
        signal.id,
        signal.asset_id,
        signal.event_snapshot.event_type.value,
        signal.event_snapshot.stage.value,
        signal.direction.value,
        signal.magnitude.value,
        f"{signal.confidence:.2f}",
        str(signal.horizon_bars),
        f"{signal.score:+.4f}",
        f"r{signal.revision}",
        signal.event_snapshot.event_date,
    ]


_SIGNAL_HEADERS = [
    "id",
    "asset",
    "type",
    "stage",
    "direction",
    "magnitude",
    "conf",
    "bars",
    "score",
    "rev",
    "event date",
]


@app.callback()
def main_callback(
    db: Annotated[
        str | None,
        typer.Option(help="SQLite path; ':memory:' for a throwaway store. Default $NEWSALPHA_DB."),
    ] = None,
) -> None:
    """Shared options for every command."""
    state.configure(db)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


# --------------------------------------------------------------------------- #
# init / ingest
# --------------------------------------------------------------------------- #


@app.command()
def init(
    reset: Annotated[bool, typer.Option(help="Drop and recreate the database first.")] = False,
) -> None:
    """Create the database and load + validate the committed datasets (FR-3)."""
    try:
        service = state.service()
        loaded = service.initialize(reset=reset)
    except Exception as exc:
        raise _fail(exc) from exc
    typer.echo(f"initialized: {loaded} assets, {len(service.datasets.patterns)} patterns, ")
    typer.echo(
        f"             {len(service.datasets.priors)} priors, "
        f"{len(service.datasets.templates.templates)} templates validated"
    )


@app.command()
def ingest(
    feed: Annotated[str, typer.Option(help="fixture | rss")] = "fixture",
    path: Annotated[
        Path | None, typer.Option(help="fixture corpus path (default: the committed evals corpus)")
    ] = None,
    since: Annotated[
        str | None, typer.Option(help="ISO instant; default as-of minus the window")
    ] = None,
    until: Annotated[str | None, typer.Option(help="ISO instant; default as-of")] = None,
    as_of: Annotated[
        str | None,
        typer.Option(
            "--as-of",
            help=(
                "ISO instant the recompute is anchored to. For the fixture feed it defaults to "
                "the corpus's latest publication so a canned corpus works with zero configuration."
            ),
        ),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Fetch, append and recompute the active window (FR-1/FR-15)."""
    try:
        service = state.service()
        source = resolve_newsfeed(feed, path=path or DEFAULT_FIXTURE_FEED)
        anchor = as_of or _default_as_of(source)
        result = service.ingest(source, as_of=anchor, since=since, until=until)
    except Exception as exc:
        raise _fail(exc) from exc
    payload = {
        "as_of": anchor,
        "articles": result.articles_new,
        "articles_excluded": result.articles_excluded,
        "clusters": result.clusters,
        "events": result.events,
        "signals_new": result.signals_new,
        "revisions_new": result.revisions_new,
        "briefs": result.briefs,
    }
    if json_out:
        _emit(payload)
        return
    typer.echo(f"ingest as_of {anchor}")
    _table(
        ["metric", "count"],
        [[key, str(value)] for key, value in payload.items() if key != "as_of"],
    )


def _default_as_of(source: Any) -> str:
    """A canned corpus carries its own 'today'; a live feed uses the wall clock."""
    if isinstance(source, FixtureNewsFeed):
        published = [article.published_at for article in source.load()]
        if published:
            return max(published)
    return _now_iso()


# --------------------------------------------------------------------------- #
# articles
# --------------------------------------------------------------------------- #


@articles_app.command("list")
def articles_list(
    domain: Annotated[str | None, typer.Option(help="Filter by source domain.")] = None,
    since: Annotated[str | None, typer.Option(help="ISO instant lower bound.")] = None,
    limit: Annotated[int | None, typer.Option(help="Maximum rows.")] = 20,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List ingested articles, newest last."""
    try:
        rows = state.service().repository.list_articles(since=since, domain=domain, limit=limit)
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit([_dump(a) for a in rows])
        return
    if not rows:
        typer.echo("no articles stored")
        return
    _table(
        ["id", "published", "domain", "tier", "title"],
        [[a.id, a.published_at, a.source_domain, a.tier.value, _clip(a.title, 60)] for a in rows],
    )


@articles_app.command("show")
def articles_show(
    article_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print one article in full."""
    try:
        article = state.service().repository.get_article(article_id)
        if article is None:
            raise errors.UnknownArticleError(f"no article {article_id!r}")
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(article))
        return
    typer.echo(
        f"{article.id}  {article.published_at}  {article.source_domain} [{article.tier.value}]"
    )
    typer.echo(f"url: {article.url or '-'}")
    typer.echo(f"excluded_from_analysis: {article.excluded_from_analysis}")
    typer.echo("")
    typer.echo(article.title)
    typer.echo("")
    typer.echo(article.body)


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #


@events_app.command("list")
def events_list(
    type_: Annotated[str | None, typer.Option("--type", help="Event type.")] = None,
    asset: Annotated[str | None, typer.Option(help="Linked asset id.")] = None,
    stage: Annotated[str | None, typer.Option(help="rumored | confirmed | denied")] = None,
    since: Annotated[str | None, typer.Option(help="ISO date lower bound.")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List derived events."""
    try:
        rows = state.service().repository.list_events(
            event_type=type_, asset_id=asset, stage=stage, since=since
        )
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit([_dump(e) for e in rows])
        return
    if not rows:
        typer.echo("no events derived")
        return
    _table(
        ["id", "date", "type", "stage", "conf", "attributes", "notes"],
        [
            [
                e.id,
                e.event_date,
                e.event_type.value,
                e.stage.value,
                f"{e.extraction_confidence:.2f}",
                json.dumps(e.attributes, sort_keys=True),
                ",".join(e.notes) or "-",
            ]
            for e in rows
        ],
    )


@events_app.command("show")
def events_show(
    event_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print one event with its evidence spans and asset links (US-2)."""
    try:
        service = state.service()
        event = service.repository.get_event(event_id)
        if event is None:
            raise errors.UnknownEventError(f"no event {event_id!r}")
        links = service.repository.list_links(event_id)
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit({"event": _dump(event), "links": [_dump(link) for link in links]})
        return
    typer.echo(
        f"{event.id}  {event.event_type.value}  stage={event.stage.value}  {event.event_date}"
    )
    typer.echo(
        f"cluster: {event.cluster_id}   extraction_confidence: {event.extraction_confidence:.2f}"
    )
    typer.echo(f"attributes: {json.dumps(event.attributes, sort_keys=True)}")
    if event.notes:
        typer.echo(f"notes: {', '.join(event.notes)}")
    typer.echo("")
    typer.echo("evidence")
    _table(
        ["article", "start", "end", "quote"],
        [[s.article_id, str(s.start), str(s.end), _clip(s.quote, 80)] for s in event.evidence],
    )
    typer.echo("")
    typer.echo("links")
    _table(
        ["asset", "role", "conf", "quote"],
        [
            [
                link.asset_id,
                link.role.value,
                f"{link.link_confidence:.2f}",
                _clip(link.evidence.quote, 50),
            ]
            for link in links
        ],
    )


# --------------------------------------------------------------------------- #
# signals & briefs
# --------------------------------------------------------------------------- #


@signals_app.command("list")
def signals_list(
    asset: Annotated[str | None, typer.Option(help="Asset id.")] = None,
    min_confidence: Annotated[float | None, typer.Option(help="Confidence floor.")] = None,
    direction: Annotated[str | None, typer.Option(help="bullish | bearish")] = None,
    since: Annotated[str | None, typer.Option(help="Event-date lower bound.")] = None,
    include_superseded: Annotated[
        bool, typer.Option("--include-superseded", help="Show demoted signal keys too.")
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List the latest revision of each signal key, ranked like the digest."""
    try:
        rows = state.service().signals(
            asset_id=asset,
            direction=direction,
            min_confidence=min_confidence,
            since=since,
            include_superseded=include_superseded,
        )
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit([_dump(s) for s in rows])
        return
    if not rows:
        typer.echo("no signals stored")
        return
    _table(_SIGNAL_HEADERS, [_signal_row(s) for s in rows])


@signals_app.command("show")
def signals_show(
    signal_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print one signal revision with its rationale codes and event snapshot."""
    try:
        signal = state.service().repository.get_signal(signal_id)
        if signal is None:
            raise errors.UnknownSignalError(f"no signal {signal_id!r}")
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(signal))
        return
    _table(_SIGNAL_HEADERS, [_signal_row(signal)])
    typer.echo("")
    typer.echo(f"signal_key : {signal.signal_key}")
    typer.echo(f"prior_key  : {signal.prior_key}")
    typer.echo(
        f"band       : {signal.expected_ar_lo:+.3%} to {signal.expected_ar_hi:+.3%} (post-entry)"
    )
    typer.echo(f"observed_at: {signal.observed_at}")
    typer.echo(
        f"supersedes : {signal.supersedes or '-'}  supersedes_key: {signal.supersedes_key or '-'}"
    )
    typer.echo("rationale  : " + ", ".join(signal.rationale_codes))
    typer.echo("snapshot   : " + json.dumps(_dump(signal.event_snapshot), sort_keys=True))


@signals_app.command("revisions")
def signals_revisions(
    signal_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print the full revision chain for a signal's key (FR-15)."""
    try:
        service = state.service()
        if service.repository.get_signal(signal_id) is None:
            raise errors.UnknownSignalError(f"no signal {signal_id!r}")
        chain = service.signal_revisions(signal_id)
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit([_dump(s) for s in chain])
        return
    _table(_SIGNAL_HEADERS, [_signal_row(s) for s in chain])


@app.command()
def brief(
    signal_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print the brief attached to one signal revision (FR-7)."""
    try:
        row = state.service().repository.get_brief(signal_id)
        if row is None:
            raise errors.UnknownBriefError(f"no brief for signal {signal_id!r}")
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(row))
        return
    typer.echo(row.rendered_text)


# --------------------------------------------------------------------------- #
# digest
# --------------------------------------------------------------------------- #


@app.command()
def digest(
    date: Annotated[
        str | None,
        typer.Option(
            help="ISO date; default: the most recent event date in the store, else today."
        ),
    ] = None,
    all_assets: Annotated[
        bool, typer.Option("--all-assets", help="Ignore the watchlist filter.")
    ] = False,
    include_superseded: Annotated[
        bool, typer.Option("--include-superseded", help="Show demoted signals too.")
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Ranked digest of the last 5 days (FR-8)."""
    try:
        service = state.service()
        as_of_date = date or _default_digest_date(service)
        result = service.digest(
            as_of_date=as_of_date,
            watchlist_only=not all_assets,
            include_superseded=include_superseded,
        )
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(result))
        return
    typer.echo(
        f"digest {result.date}  "
        f"({'watchlist' if result.watchlist_only else 'all assets'}, "
        f"{'incl. superseded' if result.include_superseded else 'latest only'})"
    )
    if not result.entries:
        typer.echo(result.empty_state)
        return
    _table(
        ["#", "asset", "type", "stage", "dir", "mag", "conf", "bars", "score", "summary"],
        [
            [
                str(index),
                entry.asset_id,
                entry.event_type.value,
                entry.stage.value,
                entry.direction.value,
                entry.magnitude.value,
                f"{entry.confidence:.2f}",
                str(entry.horizon_bars),
                f"{entry.score:+.4f}",
                entry.summary
                + (f" [{entry.supersession_note}]" if entry.supersession_note else ""),
            ]
            for index, entry in enumerate(result.entries, start=1)
        ],
    )


def _default_digest_date(service: NewsAlphaService) -> str:
    dates = [s.event_snapshot.event_date for s in service.repository.list_signals()]
    return max(dates) if dates else _now_iso()[:10]


# --------------------------------------------------------------------------- #
# assets & watchlist
# --------------------------------------------------------------------------- #


@assets_app.command("list")
def assets_list(
    kind: Annotated[str | None, typer.Option(help="equity | crypto | index")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q", help="Substring filter.")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List the committed gazetteer."""
    try:
        assets = state.service().datasets.assets
    except Exception as exc:
        raise _fail(exc) from exc
    rows = [
        asset
        for asset in assets.values()
        if (kind is None or asset.kind.value == kind)
        and (
            not query
            or any(
                query.casefold() in surface.casefold()
                for surface in (asset.id, asset.name, asset.symbol, *asset.aliases)
            )
        )
    ]
    rows.sort(key=lambda a: a.id)
    if json_out:
        _emit([_dump(a) for a in rows])
        return
    _table(
        ["id", "kind", "symbol", "name", "ambiguous", "benchmark"],
        [
            [
                a.id,
                a.kind.value,
                a.symbol,
                a.name,
                "yes" if a.ambiguous else "no",
                a.benchmark_id or "-",
            ]
            for a in rows
        ],
    )


@assets_app.command("show")
def assets_show(
    asset_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print one gazetteer entry."""
    try:
        service = state.service()
        asset = service.datasets.assets.get(asset_id)
        if asset is None:
            raise errors.UnknownAssetError(
                f"no asset {asset_id!r} in the committed gazetteer",
                suggestion=service.suggest_asset(asset_id),
            )
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(asset))
        return
    typer.echo(f"{asset.id}  {asset.name}  ({asset.kind.value})")
    typer.echo(f"symbol   : {asset.symbol}")
    typer.echo(f"aliases  : {', '.join(asset.aliases) or '-'}")
    typer.echo(f"ambiguous: {asset.ambiguous}")
    typer.echo(f"context  : {', '.join(asset.context_keywords) or '-'}")
    typer.echo(f"benchmark: {asset.benchmark_id or '-'}")


@watch_app.command("add")
def watch_add(asset_id: str) -> None:
    """Add an asset to the watchlist (unknown ids suggest the nearest, FR-11)."""
    try:
        service = state.service()
        if asset_id not in service.datasets.assets:
            raise errors.UnknownAssetError(
                f"no asset {asset_id!r} in the committed gazetteer",
                suggestion=service.suggest_asset(asset_id),
            )
        service.repository.replace_assets(list(service.datasets.assets.values()))
        added = service.add_watch(asset_id, _now_iso())
    except Exception as exc:
        raise _fail(exc) from exc
    typer.echo(f"{'added' if added else 'already watching'} {asset_id}")


@watch_app.command("remove")
def watch_remove(asset_id: str) -> None:
    """Remove an asset from the watchlist."""
    try:
        removed = state.service().remove_watch(asset_id)
        if not removed:
            raise errors.UnknownAssetError(f"{asset_id!r} is not on the watchlist")
    except Exception as exc:
        raise _fail(exc) from exc
    typer.echo(f"removed {asset_id}")


@watch_app.command("list")
def watch_list(
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List watchlist entries."""
    try:
        service = state.service()
        rows = service.repository.list_watchlist()
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit([_dump(item) for item in rows])
        return
    if not rows:
        typer.echo("watchlist is empty")
        return
    _table(
        ["asset", "name", "added"],
        [
            [
                item.asset_id,
                service.datasets.assets[item.asset_id].name
                if item.asset_id in service.datasets.assets
                else "-",
                item.added_at,
            ]
            for item in rows
        ],
    )


# --------------------------------------------------------------------------- #
# prices
# --------------------------------------------------------------------------- #


@prices_app.command("load")
def prices_load(
    source: Annotated[str, typer.Option(help="fixture | live")] = "fixture",
    directory: Annotated[Path | None, typer.Option("--dir", help="Fixture CSV directory.")] = None,
    start: Annotated[str | None, typer.Option(help="ISO date.")] = None,
    end: Annotated[str | None, typer.Option(help="ISO date.")] = None,
    assets: Annotated[
        str | None, typer.Option(help="Comma-separated asset ids; default the whole gazetteer.")
    ] = None,
) -> None:
    """Load daily bars into the store (FR-9)."""
    try:
        service = state.service()
        market = resolve_marketdata(
            source,
            directory=directory or DEFAULT_FIXTURE_MARKET,
            benchmarks=service.datasets.benchmarks,
        )
        wanted = (
            [a.strip() for a in assets.split(",")] if assets else service.available_assets(market)
        )
        loaded = service.load_prices(
            market,
            assets=wanted,
            start=start or "1970-01-01",
            end=end or _now_iso()[:10],
        )
    except Exception as exc:
        raise _fail(exc) from exc
    typer.echo(f"loaded {loaded} bars for {len(wanted)} assets from {source}")


# --------------------------------------------------------------------------- #
# backtests
# --------------------------------------------------------------------------- #


@backtest_app.command("run")
def backtest_run(
    start: Annotated[str | None, typer.Option(help="Event-date lower bound.")] = None,
    end: Annotated[str | None, typer.Option(help="Event-date upper bound.")] = None,
    min_confidence: Annotated[float, typer.Option(help="Confidence floor.")] = 0.0,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Backtest the latest revision of every signal key (FR-10)."""
    _run_backtest(start, end, min_confidence, None, json_out)


@backtest_app.command("placebo")
def backtest_placebo(
    seed: Annotated[int, typer.Option(help="Placebo seed (hash-derived offsets).")] = 20260731,
    start: Annotated[str | None, typer.Option(help="Event-date lower bound.")] = None,
    end: Annotated[str | None, typer.Option(help="Event-date upper bound.")] = None,
    min_confidence: Annotated[float, typer.Option(help="Confidence floor.")] = 0.0,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """The same harness with displaced entries: it must find no skill (FR-10)."""
    _run_backtest(start, end, min_confidence, seed, json_out)


def _run_backtest(
    start: str | None,
    end: str | None,
    min_confidence: float,
    placebo_seed: int | None,
    json_out: bool,
) -> None:
    try:
        service = state.service()
        params = BacktestParams(
            start=start or "1970-01-01",
            end=end or _now_iso()[:10],
            min_confidence=min_confidence,
            placebo_seed=placebo_seed,
        )
        run, _results = service.run_backtest(params, _now_iso())
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(run))
        return
    _render_run(run)


@backtest_app.command("show")
def backtest_show(
    run_id: str,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print a stored backtest run."""
    try:
        service = state.service()
        run = service.repository.get_backtest(run_id)
        if run is None:
            raise errors.UnknownBacktestError(f"no backtest run {run_id!r}")
    except Exception as exc:
        raise _fail(exc) from exc
    if json_out:
        _emit(_dump(run))
        return
    _render_run(run)


def _render_run(run: Any) -> None:
    aggregates = run.aggregates
    kind = "placebo" if run.params.placebo_seed is not None else "real"
    typer.echo(f"backtest {run.id}  ({kind})  as_of {run.as_of}")
    typer.echo(
        f"N={aggregates.n}  excluded={aggregates.n_excluded}  superseded={aggregates.n_superseded}"
    )
    typer.echo(
        f"hit_rate={_fmt(aggregates.hit_rate)}  mean_ar={_fmt(aggregates.mean_ar, pct=True)}  "
        f"IC={_fmt(aggregates.ic_spearman)}"
    )
    if aggregates.excluded_by_reason:
        typer.echo(
            "exclusions: " + ", ".join(f"{k}={v}" for k, v in aggregates.excluded_by_reason.items())
        )
    typer.echo("")
    _table(
        ["bucket", "n", "hit"],
        [[name, str(stats.n), _fmt(stats.hit)] for name, stats in aggregates.buckets.items()],
    )
    typer.echo("")
    _table(
        ["event type", "n", "hit", "mean AR", "IC"],
        [
            [
                name,
                str(agg.n),
                _fmt(agg.hit_rate),
                _fmt(agg.mean_ar, pct=True),
                _fmt(agg.ic_spearman),
            ]
            for name, agg in sorted(run.per_type.items())
        ],
    )


def _fmt(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.2%}" if pct else f"{value:.3f}"


def _clip(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def main() -> None:
    """Console-script entry point (`newsalpha`)."""
    try:
        app()
    except errors.NewsAlphaError as exc:  # pragma: no cover - defensive
        typer.secho(f"error [{exc.code}]: {exc.message}", fg=typer.colors.RED, err=True)
        sys.exit(EXIT_BY_CODE.get(exc.code, 1))


__all__ = ["EXIT_BY_CODE", "app", "main"]
