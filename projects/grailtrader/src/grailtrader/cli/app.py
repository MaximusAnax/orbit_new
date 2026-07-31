"""The GrailTrader command line (FR-13) — thin: parse, delegate, render.

Same services as the API. Every list/show command takes ``--json`` so the human
tables never become the only interface, and every failure exits non-zero with a
message on stderr.
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import typer

from ..engine.conditions import UnmappedConditionLabelError
from ..engine.frame import FrameCheckError
from ..engine.strata import UnknownReferenceError
from ..engine.validate import DatasetValidationError
from ..models import (
    AdviceAction,
    Category,
    ConditionGrade,
    EventSource,
    EventStatus,
    EventType,
    GarmentStatus,
    ListingSource,
    ListingStatus,
    ValuationMethod,
)
from ..service import GrailTraderService, NotFoundError, PreconditionError, open_repository
from ..store.base import RepositoryError

__all__ = ["app", "main"]

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "GrailTrader — treat designer clothing like a tradable asset class: build "
        "price indices from secondhand sold comps, ingest typed fashion events, and "
        "get buy/sell/hold advice with confidence, drivers and a fee/illiquidity "
        "frame. It never buys or sells anything, and it is not investment advice."
    ),
)
listings_app = typer.Typer(no_args_is_help=True, help="Load and inspect marketplace listings.")
index_app = typer.Typer(no_args_is_help=True, help="Build and inspect the weekly price indices.")
events_app = typer.Typer(no_args_is_help=True, help="Ingest, add and review typed fashion events.")
brands_app = typer.Typer(no_args_is_help=True, help="Inspect the brand / designer-era gazetteer.")
portfolio_app = typer.Typer(no_args_is_help=True, help="Manage and value the garment portfolio.")
advice_app = typer.Typer(no_args_is_help=True, help="Read stored advice.")
backtest_app = typer.Typer(no_args_is_help=True, help="Replay the advisor over history.")
app.add_typer(listings_app, name="listings")
app.add_typer(index_app, name="index")
app.add_typer(events_app, name="events")
app.add_typer(brands_app, name="brands")
app.add_typer(portfolio_app, name="portfolio")
app.add_typer(advice_app, name="advice")
app.add_typer(backtest_app, name="backtest")

_STATE: dict[str, Any] = {"db": None, "service": None}
F = TypeVar("F", bound=Callable[..., Any])

_HANDLED = (
    NotFoundError,
    PreconditionError,
    UnknownReferenceError,
    UnmappedConditionLabelError,
    DatasetValidationError,
    FrameCheckError,
    RepositoryError,
    FileNotFoundError,
    ValueError,
    RuntimeError,
)


def guard(func: F) -> F:
    """Turn a known failure into a one-line stderr message and a non-zero exit."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except _HANDLED as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None

    return wrapper  # type: ignore[return-value]


def service() -> GrailTraderService:
    if _STATE["service"] is None:
        repo = open_repository(_STATE["db"] or os.environ.get("GRAILTRADER_DB"))
        repo.initialize()
        _STATE["service"] = GrailTraderService(repo)
    return _STATE["service"]


def set_service(instance: GrailTraderService | None) -> None:
    """Injection seam for tests (Typer's CliRunner drives the same code path)."""
    _STATE["service"] = instance


@app.callback()
def _root(
    db: str = typer.Option(
        None, "--db", help="SQLite path (default: $GRAILTRADER_DB or ~/.grailtrader)"
    ),
) -> None:
    if db:
        _STATE["db"] = db
        _STATE["service"] = None


# --------------------------------------------------------------------------- #
# Rendering helpers                                                             #
# --------------------------------------------------------------------------- #


def _dump(payload: Any) -> str:
    def default(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, set | tuple):
            return list(value)
        return str(value)

    return json.dumps(payload, indent=2, default=default)


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    cells = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for position, value in enumerate(row):
            widths[position] = max(widths[position], len(value))
    line = "  ".join(header.ljust(widths[position]) for position, header in enumerate(headers))
    out = [line, "  ".join("-" * width for width in widths)]
    out.extend(
        "  ".join(value.ljust(widths[position]) for position, value in enumerate(row))
        for row in cells
    )
    return "\n".join(out)


def emit(as_json: bool, payload: Any, human: Callable[[], None]) -> None:
    if as_json:
        typer.echo(_dump(payload))
    else:
        human()


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:+.1f}%"


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


# --------------------------------------------------------------------------- #
# init                                                                          #
# --------------------------------------------------------------------------- #


@app.command()
@guard
def init(
    reset: bool = typer.Option(False, "--reset", help="recreate the database from scratch"),
) -> None:
    """Validate the committed datasets and materialise the gazetteer (FR-1)."""
    report = service().initialize(reset=reset)
    typer.echo(
        f"validated datasets (config {report.config_version}): "
        f"{report.brands} brands / {report.eras} eras materialised, "
        f"{report.priors} impact priors, {report.templates} advice templates"
        + (" — database reset" if report.reset else "")
    )


# --------------------------------------------------------------------------- #
# listings                                                                      #
# --------------------------------------------------------------------------- #


@listings_app.command("load")
@guard
def listings_load(
    path: str = typer.Option(..., "--path", "-p", help="fixture JSONL or exported CSV"),
    source: ListingSource = typer.Option(ListingSource.FIXTURE, "--source", "-s"),
) -> None:
    """Ingest marketplace listings (FR-2). Idempotent: re-loading a feed changes nothing."""
    report = service().load_listings(source=source, path=path)
    typer.echo(
        f"ingested {report.ingested} · duplicates {report.duplicates} · "
        f"unresolved {report.skipped_unresolved} · non-USD {report.skipped_currency} · "
        f"invalid {report.skipped_invalid}"
    )
    if report.unresolved_refs:
        sample = ", ".join(report.unresolved_refs[:5])
        typer.echo(f"  unresolved sample: {sample}")


@listings_app.command("list")
@guard
def listings_list(
    stratum: str = typer.Option(None, "--stratum", help="brand[/era[/category]] prefix"),
    status: ListingStatus = typer.Option(None, "--status"),
    limit: int = typer.Option(20, "--limit", min=1, max=1000),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List stored listings."""
    rows = service().list_listings(stratum=stratum, status=status, limit=limit)
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "stratum", "condition", "status", "sold_at", "price"],
                [
                    [
                        row.id,
                        row.stratum_path,
                        row.condition.value,
                        row.status.value,
                        row.sold_at or "-",
                        _money(row.sold_price if row.sold_price else row.ask_price),
                    ]
                    for row in rows
                ],
            )
            if rows
            else "no listings"
        ),
    )


@listings_app.command("show")
@guard
def listings_show(
    listing_id: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Show one listing."""
    row = service().get_listing(listing_id)
    emit(
        as_json,
        row,
        lambda: typer.echo(
            "\n".join(f"{key:16s} {value}" for key, value in row.model_dump(mode="json").items())
        ),
    )


# --------------------------------------------------------------------------- #
# index                                                                         #
# --------------------------------------------------------------------------- #


@index_app.command("build")
@guard
def index_build(
    as_of: str = typer.Option(None, "--as-of", help="ISO date; defaults to the newest sale"),
) -> None:
    """Build every leaf and chain-linked parent index series (FR-4)."""
    report = service().build_index(as_of=as_of)
    typer.echo(
        f"built {report.strata_built} strata ({report.leaf_strata} leaf) as of {report.as_of}: "
        f"{report.points_written} weekly points, {report.excluded_total} sales fenced out"
    )


@index_app.command("show")
@guard
def index_show(
    stratum_id: str = typer.Argument(..., help="e.g. helmut-lang/helmut/outerwear"),
    weeks: int = typer.Option(12, "--weeks", min=1),
    excluded: bool = typer.Option(False, "--excluded", help="list the fenced-out listings"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a weekly index series, with sample sizes and fence exclusions."""
    api = service()
    points = api.index_points(stratum_id, weeks=weeks)
    payload: dict[str, Any] = {"stratum_id": stratum_id, "points": points}
    if excluded:
        payload["excluded"] = {
            point.week: api.excluded_listings(stratum_id, point.week)
            for point in points
            if point.n_excluded
        }

    def human() -> None:
        typer.echo(
            table(
                ["week", "index", "level_usd", "n_sales", "n_excluded"],
                [
                    [
                        point.week,
                        f"{point.index_value:.2f}",
                        _money(point.level_usd),
                        point.n_sales,
                        point.n_excluded,
                    ]
                    for point in points
                ],
            )
        )
        if excluded:
            for week, rows in payload["excluded"].items():
                for row in rows:
                    typer.echo(
                        f"  fenced {week}: {row.id} {_money(row.sold_price)} "
                        f"({row.condition.value})"
                    )

    emit(as_json, payload, human)


@index_app.command("strata")
@guard
def index_strata(
    level: str = typer.Option(None, "--level", help="leaf | era | brand"),
    brand: str = typer.Option(None, "--brand"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List the strata the index covers."""
    rows = service().strata(level=level, brand=brand)
    emit(as_json, {"strata": rows}, lambda: typer.echo("\n".join(rows) or "no strata"))


# --------------------------------------------------------------------------- #
# events                                                                        #
# --------------------------------------------------------------------------- #


@events_app.command("ingest")
@guard
def events_ingest(
    source: str = typer.Option("fixture", "--source", help="fixture | rss"),
    path: str = typer.Option(None, "--path", "-p"),
    social: bool = typer.Option(False, "--social", help="read the file as a social feed"),
    since: str = typer.Option("1970-01-01", "--since"),
    until: str = typer.Option("2999-12-31", "--until"),
) -> None:
    """Ingest typed events (FR-5). Live RSS yields pending candidates for review."""
    report = service().ingest_event_feed(
        source=source, path=path, since=since, until=until, social=social
    )
    by_status = ", ".join(f"{key.value}={value}" for key, value in sorted(report.by_status.items()))
    typer.echo(
        f"created {report.created} · corroborated {report.corroborated} · "
        f"unchanged {report.unchanged} · unresolved {report.skipped_unresolved} · "
        f"totals: {by_status}"
    )
    if report.unresolved_refs:
        sample = ", ".join(report.unresolved_refs[:5])
        typer.echo(f"  unresolved sample: {sample}")


@events_app.command("add")
@guard
def events_add(
    event_type: EventType = typer.Option(..., "--type", "-t"),
    brand: str = typer.Option(..., "--brand", "-b"),
    occurred_on: str = typer.Option(..., "--occurred-on", help="ISO date"),
    era: str = typer.Option(None, "--era", help="era id, suffix, designer or label"),
    source: EventSource = typer.Option(EventSource.MANUAL, "--source"),
    source_ref: str = typer.Option(None, "--source-ref"),
    reason: str = typer.Option(
        None, "--reason", help="departure: resignation|ousted|death|house_closure"
    ),
    designer: str = typer.Option(None, "--designer", help="appointment: the incoming designer"),
    acclaim: str = typer.Option(None, "--acclaim", help="appointment: acclaimed|neutral|unproven"),
    counterparty: str = typer.Option(None, "--counterparty", help="collab: the other party"),
    celebrity: str = typer.Option(None, "--celebrity", help="co-sign: who wore it"),
    tier: str = typer.Option(None, "--tier", help="co-sign: a_list|b_list|niche"),
    category: str = typer.Option(None, "--category", help="co-sign: narrow the scope"),
    polarity: str = typer.Option(None, "--polarity", help="runway: acclaimed|panned"),
    severity: str = typer.Option(None, "--severity", help="scandal: minor|moderate|severe"),
    notes: str = typer.Option("", "--notes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Add one typed event by hand — a first-class source (FR-5)."""
    attributes = {
        key: value
        for key, value in {
            "reason": reason,
            "designer": designer,
            "acclaim": acclaim,
            "counterparty": counterparty,
            "celebrity": celebrity,
            "tier": tier,
            "category": category,
            "polarity": polarity,
            "severity": severity,
        }.items()
        if value is not None
    }
    event, created = service().add_event(
        event_type=event_type,
        brand=brand,
        occurred_on=occurred_on,
        era=era,
        source=source,
        source_ref=source_ref,
        attributes=attributes,
        notes=notes,
    )
    emit(
        as_json,
        event,
        lambda: typer.echo(
            f"{'created' if created else 'corroborated'} {event.id}: {event.event_type.value} "
            f"@ {event.brand_id} on {event.occurred_on} "
            f"(corroboration {event.corroboration}, status {event.status.value})"
        ),
    )


@events_app.command("list")
@guard
def events_list(
    event_type: EventType = typer.Option(None, "--type", "-t"),
    brand: str = typer.Option(None, "--brand", "-b"),
    status: EventStatus = typer.Option(None, "--status"),
    since: str = typer.Option(None, "--since"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List events."""
    rows = service().list_events(event_type=event_type, brand=brand, status=status, since=since)
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "date", "type", "brand", "era", "status", "corrob", "source"],
                [
                    [
                        row.id,
                        row.occurred_on,
                        row.event_type.value,
                        row.brand_id,
                        row.era_id or "-",
                        row.status.value,
                        row.corroboration,
                        row.source.value,
                    ]
                    for row in rows
                ],
            )
            if rows
            else "no events"
        ),
    )


@events_app.command("show")
@guard
def events_show(
    event_id: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Show an event with its resolved scopes, priors, sources and retirement age (FR-5/FR-6)."""
    detail = service().event_detail(event_id)

    def human() -> None:
        event = detail.event
        typer.echo(f"{event.id}  {event.event_type.value}  {event.brand_id}  {event.occurred_on}")
        typer.echo(f"status {event.status.value} · corroboration {detail.corroboration}")
        typer.echo(f"sources: {', '.join(detail.source_refs)}")
        if event.notes:
            typer.echo(f"notes: {event.notes}")
        typer.echo("targets: " + ", ".join(f"{t['stratum']} ({t['kind']})" for t in detail.targets))
        typer.echo(f"retires at age {detail.retirement_age_weeks:.1f} weeks")
        for prior in detail.priors:
            typer.echo(
                f"  prior {prior['key']} -> {prior['target_stratum']}: "
                f"P={prior['permanent_pct']:+.2f} T={prior['transient_pct']:+.2f} "
                f"h={prior['half_life_weeks']:g}w base_conf={prior['base_conf']:.2f}"
            )
            typer.echo(f"    {prior['rationale']}")
            typer.echo(f"    source: {prior['source_note']}")

    emit(as_json, detail.__dict__, human)


@events_app.command("review")
@guard
def events_review(
    confirm: str = typer.Option(None, "--confirm", help="event id to confirm"),
    reject: str = typer.Option(None, "--reject", help="event id to reject"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Review pending candidates: a pending event influences nothing until confirmed."""
    api = service()
    if confirm and reject:
        raise ValueError("pass either --confirm or --reject, not both")
    if confirm or reject:
        event = api.set_event_status(
            confirm or reject, EventStatus.CONFIRMED if confirm else EventStatus.REJECTED
        )
        emit(as_json, event, lambda: typer.echo(f"{event.id} is now {event.status.value}"))
        return
    rows = api.list_events(status=EventStatus.PENDING)
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "date", "type", "brand", "notes"],
                [
                    [row.id, row.occurred_on, row.event_type.value, row.brand_id, row.notes[:48]]
                    for row in rows
                ],
            )
            if rows
            else "no pending events"
        ),
    )


# --------------------------------------------------------------------------- #
# brands                                                                        #
# --------------------------------------------------------------------------- #


@brands_app.command("list")
@guard
def brands_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List the gazetteer's brands."""
    rows = service().brands()
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "name", "eras", "aliases"],
                [[b.id, b.name, len(b.eras), ", ".join(b.aliases)] for b in rows],
            )
        ),
    )


@brands_app.command("show")
@guard
def brands_show(
    brand_id: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Show one brand and its designer eras."""
    brand = service().brand(brand_id)

    def human() -> None:
        typer.echo(f"{brand.id}  {brand.name}")
        if brand.notes:
            typer.echo(brand.notes)
        typer.echo(
            table(
                ["era id", "designer", "label", "start", "end"],
                [[e.id, e.designer, e.label, e.start, e.end or "open"] for e in brand.eras],
            )
        )

    emit(as_json, brand, human)


# --------------------------------------------------------------------------- #
# portfolio                                                                     #
# --------------------------------------------------------------------------- #


@portfolio_app.command("add")
@guard
def portfolio_add(
    label: str = typer.Option(..., "--label", "-l"),
    brand: str = typer.Option(..., "--brand", "-b"),
    era: str = typer.Option(..., "--era", "-e"),
    category: Category = typer.Option(..., "--category", "-c"),
    condition: ConditionGrade = typer.Option(..., "--condition"),
    price: float = typer.Option(..., "--price", help="acquisition price (or reference price)"),
    date: str = typer.Option(..., "--date", help="acquisition date (or reference date)"),
    status: GarmentStatus = typer.Option(GarmentStatus.OWNED, "--status"),
    size: str = typer.Option(None, "--size"),
    notes: str = typer.Option("", "--notes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Register a garment (FR-11). Unknown brand/era/category is rejected with a suggestion."""
    garment = service().add_garment(
        label=label,
        brand=brand,
        era=era,
        category=category,
        condition=condition,
        price=price,
        date=date,
        status=status,
        size=size,
        notes=notes,
    )
    emit(
        as_json,
        garment,
        lambda: typer.echo(f"added {garment.id}: {garment.label} ({garment.stratum_path})"),
    )


@portfolio_app.command("edit")
@guard
def portfolio_edit(
    garment_id: str = typer.Argument(...),
    label: str = typer.Option(None, "--label"),
    condition: ConditionGrade = typer.Option(None, "--condition"),
    size: str = typer.Option(None, "--size"),
    notes: str = typer.Option(None, "--notes"),
    status: GarmentStatus = typer.Option(None, "--status"),
    disposed_price: float = typer.Option(None, "--disposed-price"),
    disposed_on: str = typer.Option(None, "--disposed-on"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Edit the mutable fields (brand/era/category/anchor are immutable by design)."""
    garment = service().edit_garment(
        garment_id,
        label=label,
        condition=condition,
        size=size,
        notes=notes,
        status=status,
        disposed_price=disposed_price,
        disposed_on=disposed_on,
    )
    emit(as_json, garment, lambda: typer.echo(f"updated {garment.id}"))


@portfolio_app.command("remove")
@guard
def portfolio_remove(
    garment_id: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Soft delete a garment: it leaves the pipeline, its advice stays readable."""
    garment = service().remove_garment(garment_id)
    emit(
        as_json,
        garment,
        lambda: typer.echo(f"removed {garment.id} (soft delete at {garment.deleted_at})"),
    )


@portfolio_app.command("list")
@guard
def portfolio_list(
    include_deleted: bool = typer.Option(False, "--include-deleted"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List the portfolio."""
    rows = service().list_garments(include_deleted=include_deleted)
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "label", "stratum", "condition", "status", "anchor"],
                [
                    [
                        g.id,
                        g.label,
                        g.stratum_path,
                        g.condition.value,
                        g.status.value,
                        f"{_money(g.anchor_price)} @ {g.anchor_date}",
                    ]
                    for g in rows
                ],
            )
            if rows
            else "portfolio is empty"
        ),
    )


@portfolio_app.command("show")
@guard
def portfolio_show(
    garment_id: str = typer.Argument(...),
    as_of: str = typer.Option(None, "--as-of"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one garment with its current valuation and stratum fallback path."""
    api = service()
    garment = api.get_garment(garment_id)
    valuation = api.value_one(garment, as_of=as_of)
    payload = {
        "garment": garment,
        "valuation": valuation,
        "stratum_path": api.stratum_context(garment.stratum_path),
    }

    def human() -> None:
        typer.echo(f"{garment.id}  {garment.label}")
        typer.echo(
            f"{garment.stratum_path} · {garment.condition.value} · {garment.status.value} · "
            f"anchor {_money(garment.anchor_price)} @ {garment.anchor_date}"
        )
        typer.echo(
            f"fair value {_money(valuation.fair_value)} "
            f"({valuation.method.value}"
            + (f", {valuation.reason.value}" if valuation.reason else "")
            + ")"
        )
        if valuation.level_usd is not None:
            typer.echo(f"typical excellent-condition comp: {_money(valuation.level_usd)}")

    emit(as_json, payload, human)


@portfolio_app.command("value")
@guard
def portfolio_value(
    as_of: str = typer.Option(None, "--as-of"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark the closet to market (FR-7), always naming the valuation method."""
    rows = service().value_portfolio(as_of=as_of)
    total = sum(result.fair_value or 0.0 for _, result in rows)
    anchor = sum(garment.anchor_price for garment, _ in rows)
    unavailable = sum(1 for _, r in rows if r.method is ValuationMethod.UNAVAILABLE)

    def human() -> None:
        typer.echo(
            table(
                ["label", "stratum", "anchor", "fair value", "gain", "method"],
                [
                    [
                        garment.label,
                        garment.stratum_path,
                        _money(garment.anchor_price),
                        _money(result.fair_value),
                        _money(result.unrealized_gain),
                        result.method.value
                        + (f" ({result.reason.value})" if result.reason else ""),
                    ]
                    for garment, result in rows
                ],
            )
            if rows
            else "portfolio is empty"
        )
        typer.echo(
            f"\ntotal fair value {_money(total)} vs anchors {_money(anchor)} "
            f"({_money(total - anchor)}); {unavailable} unavailable"
        )

    emit(
        as_json,
        {
            "total_fair_value": total,
            "total_anchor_price": anchor,
            "unavailable": unavailable,
            "garments": [{"garment": garment, "valuation": result} for garment, result in rows],
        },
        human,
    )


# --------------------------------------------------------------------------- #
# advise / advice                                                               #
# --------------------------------------------------------------------------- #


@app.command()
@guard
def advise(
    as_of: str = typer.Option(None, "--as-of", help="ISO date; defaults to the latest index week"),
    show_text: bool = typer.Option(False, "--text", help="print the full rendered advice"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Produce buy/sell/hold advice for every active garment (FR-8/FR-9)."""
    rows = service().advise(as_of=as_of)

    def human() -> None:
        typer.echo(
            table(
                ["garment", "action", "H*", "r_hat", "conf", "fair value", "method", "why"],
                [
                    [
                        row.garment_id,
                        row.action.value,
                        row.horizon_weeks,
                        _pct(row.expected_return),
                        "-" if row.confidence is None else f"{row.confidence:.2f}",
                        _money(row.fair_value),
                        row.fair_value_method.value,
                        next(
                            (c for c in row.rationale_codes if c.startswith("hold:")),
                            f"{len([c for c in row.rationale_codes if c.startswith('driver:')])} drivers",
                        ),
                    ]
                    for row in rows
                ],
            )
            if rows
            else "no active garments"
        )
        if show_text:
            for row in rows:
                typer.echo("\n" + "-" * 72)
                typer.echo(row.rendered_text)

    emit(as_json, rows, human)


@advice_app.command("list")
@guard
def advice_list(
    garment: str = typer.Option(None, "--garment"),
    action: AdviceAction = typer.Option(None, "--action"),
    as_of: str = typer.Option(None, "--as-of"),
    history: bool = typer.Option(False, "--history", help="include superseded rows"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List stored advice (current rows only unless --history)."""
    rows = service().list_advice(garment=garment, action=action, as_of=as_of, history=history)
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "week", "garment", "action", "H*", "r_hat", "conf", "created"],
                [
                    [
                        row.id,
                        row.as_of_week,
                        row.garment_id,
                        row.action.value,
                        row.horizon_weeks,
                        _pct(row.expected_return),
                        "-" if row.confidence is None else f"{row.confidence:.2f}",
                        row.created_as_of,
                    ]
                    for row in rows
                ],
            )
            if rows
            else "no advice yet"
        ),
    )


@advice_app.command("show")
@guard
def advice_show(
    advice_id: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Show one advice: its rendered text and every rationale code behind it."""
    row = service().get_advice(advice_id)

    def human() -> None:
        typer.echo(row.rendered_text)
        typer.echo("\nrationale codes:")
        for code in row.rationale_codes:
            typer.echo(f"  {code}")
        typer.echo(
            f"\nstratum {row.stratum_id} · inputs_hash {row.inputs_hash} · "
            f"config {row.config_version} · frame_checked {row.frame_checked}"
        )

    emit(as_json, row, human)


# --------------------------------------------------------------------------- #
# backtest                                                                      #
# --------------------------------------------------------------------------- #


def _print_run(run: Any, n_results: int) -> None:
    aggregates = run.aggregates
    typer.echo(f"run {run.id} ({run.params.start_week} .. {run.params.end_week})")
    if run.params.placebo_seed is not None:
        typer.echo(f"PLACEBO seed {run.params.placebo_seed} — event dates scrambled")
    typer.echo(
        f"decisions {aggregates['n_decisions']} · candidates {aggregates['n_candidates']} · "
        f"actionable {aggregates['n_actionable']} · hit rate {aggregates['hit_rate']:.3f}"
    )
    typer.echo(
        f"mean realized buy {aggregates['mean_realized']['buy']:+.4f} / "
        f"sell {aggregates['mean_realized']['sell']:+.4f} · "
        f"spread {aggregates['spread_buy_minus_sell']:+.4f}"
    )
    typer.echo(
        "buckets: "
        + " · ".join(
            f"{name} n={row['n']} hit={row['hit_rate']:.2f} conf={row['mean_conf']:.2f}"
            for name, row in aggregates["buckets"].items()
        )
    )
    typer.echo(
        f"regimes: sell-into-decay {aggregates['regimes']['sell_into_decay']} · "
        f"phase-in buys {aggregates['regimes']['phase_in_buy']}"
    )
    baselines = []
    for name, row in aggregates["baselines"].items():
        rate = "n/a" if row["hit_rate"] is None else f"{row['hit_rate']:.3f}"
        baselines.append(f"{name} n={row['n']} hit={rate} spread={row['spread']:+.4f}")
    typer.echo("baselines: " + " · ".join(baselines))
    typer.echo(f"excluded: {aggregates['excluded']} · results stored {n_results}")


@backtest_app.command("run")
@guard
def backtest_run(
    start: str = typer.Option(None, "--start"),
    end: str = typer.Option(None, "--end"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Replay the advisor week by week on strictly-prior data (FR-10)."""
    run, results = service().backtest(start=start, end=end)
    emit(as_json, run, lambda: _print_run(run, len(results)))


@backtest_app.command("placebo")
@guard
def backtest_placebo(
    seed: int = typer.Option(..., "--seed", help="displaces every event by +/-[26,52] weeks"),
    start: str = typer.Option(None, "--start"),
    end: str = typer.Option(None, "--end"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Re-run the backtest with scrambled event dates: skill here means leakage."""
    run, results = service().backtest(start=start, end=end, placebo_seed=seed)
    emit(as_json, run, lambda: _print_run(run, len(results)))


@backtest_app.command("show")
@guard
def backtest_show(
    run_id: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Show a stored backtest run."""
    run, results = service().get_backtest(run_id)
    emit(as_json, run, lambda: _print_run(run, len(results)))


@backtest_app.command("list")
@guard
def backtest_list(
    limit: int = typer.Option(10, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List stored backtest runs, newest first."""
    rows = service().list_backtests(limit=limit)
    emit(
        as_json,
        rows,
        lambda: typer.echo(
            table(
                ["id", "start", "end", "placebo", "hit rate", "actionable"],
                [
                    [
                        run.id,
                        run.params.start_week,
                        run.params.end_week,
                        run.params.placebo_seed if run.params.placebo_seed is not None else "-",
                        f"{run.aggregates['hit_rate']:.3f}",
                        run.aggregates["n_actionable"],
                    ]
                    for run in rows
                ],
            )
            if rows
            else "no backtest runs"
        ),
    )


def main() -> None:  # pragma: no cover - console entry point
    app()
