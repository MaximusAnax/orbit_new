"""FR-16: the Typer CLI.

Thin: every command parses arguments, calls one service method, and renders.
Errors from the service or the store are printed to stderr and exit non-zero.

The module deliberately does not use ``from __future__ import annotations``:
Typer reads the runtime annotations to build its parser.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import typer

from almanac import __version__
from almanac.cli import render
from almanac.datasets import default_datasets
from almanac.factory import build_service, db_path, starter_pack
from almanac.models import EntryKind, EntryStatus, Grade
from almanac.service import AlmanacService, ServiceError
from almanac.store.repository import RepositoryError
from almanac.store.sqlite_repo import open_repository

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    # so that `almanac --version` runs the callback instead of "Missing command"
    invoke_without_command=True,
    help=(
        "A personal quote-and-idea almanac: capture what you save, and a "
        "deterministic daily scheduler brings it back with an application prompt."
    ),
)
collection_app = typer.Typer(no_args_is_help=True, help="Named, ordered sets of entries (FR-13).")
config_app = typer.Typer(no_args_is_help=True, help="Read and write local config (seed, batch k).")
app.add_typer(collection_app, name="collection")
app.add_typer(config_app, name="config")

_STATUS = {"active": EntryStatus.ACTIVE, "archived": EntryStatus.ARCHIVED, "all": None}


class State:
    """Per-invocation CLI state: which database to open, opened at most once."""

    def __init__(self) -> None:
        self.db: Path | None = None
        self._service: AlmanacService | None = None

    def reset(self, db: Path | None) -> None:
        """Start a fresh invocation. A long-lived process (tests) reuses this module."""
        if self._service is not None:
            self._service.repo.close()
        self.db = db
        self._service = None

    def service(self) -> AlmanacService:
        if self._service is None:
            self._service = build_service(open_repository(self.db or db_path()))
        return self._service


state = State()


def fail(message: str) -> "typer.Exit":
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=1)


def _guard(func, *args, **kwargs):
    """Run a service call, mapping domain errors onto exit code 1."""
    try:
        return func(*args, **kwargs)
    except (ServiceError, RepositoryError, ValueError) as exc:
        raise fail(str(exc)) from exc


def _dump(payload: object) -> None:
    """Print a domain model (or list of them) as JSON."""
    if hasattr(payload, "model_dump_json"):
        typer.echo(payload.model_dump_json(indent=2))
        return
    if isinstance(payload, list):
        typer.echo(
            "[\n"
            + ",\n".join(
                item.model_dump_json(indent=2)
                if hasattr(item, "model_dump_json")
                else json.dumps(item)
                for item in payload
            )
            + "\n]"
        )
        return
    typer.echo(json.dumps(payload, indent=2, default=str))


def _confirm(question: str, default: bool) -> bool:
    """``typer.confirm`` that treats a closed stdin as the default answer.

    Keeps the command usable in a pipe or a cron job, where there is nobody to
    ask: the safe default stands in for the answer.
    """
    try:
        return typer.confirm(question, default=default)
    except (EOFError, typer.Abort):
        return default


@app.callback()
def main_options(
    db: Path = typer.Option(
        None,
        "--db",
        envvar="ALMANAC_DB_PATH",
        help="SQLite database to use (default ~/.almanac/almanac.db).",
    ),
    version: bool = typer.Option(False, "--version", help="Print the version and exit."),
) -> None:
    """Global options."""
    if version:
        typer.echo(f"almanac {__version__}")
        raise typer.Exit()
    state.reset(db)


# --------------------------------------------------------------------------
# setup and capture
# --------------------------------------------------------------------------


@app.command()
def init(
    seed: int = typer.Option(0, help="Seed for the scheduler's keyed jitter (FR-17)."),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Recompute every entry's scheduler state from the event log."
    ),
) -> None:
    """Create the database, load the committed datasets, and set the seed."""
    target = state.db or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    service = build_service(open_repository(target), seed=seed)
    service.initialize()
    if service.repo.get_config("seed") != str(seed):
        service.set_seed(seed)
    rebuilt = service.rebuild_scheduler_state() if rebuild else 0
    datasets = default_datasets()
    typer.echo(f"almanac database ready at {target}")
    typer.echo(
        f"  {len(datasets.themes)} themes, {len(datasets.templates)} prompt templates, "
        f"{len(datasets.misattributions)} misattribution records, "
        f"params {datasets.params.params_version}"
    )
    typer.echo(f"  seed {service.seed}   batch k {service.batch_k}")
    if rebuild:
        typer.echo(f"  rebuilt scheduler state for {rebuilt} entries")


@app.command()
def add(
    text: str = typer.Argument(..., help="The quote or idea, in full."),
    kind: EntryKind = typer.Option(EntryKind.QUOTE, "--kind"),
    author: str = typer.Option(None, "--author"),
    source: str = typer.Option(None, "--source"),
    url: str = typer.Option(None, "--url"),
    tag: list[str] = typer.Option(None, "--tag", help="Repeatable."),
    theme: list[str] = typer.Option(None, "--theme", help="Repeatable, max 3."),
    note: str = typer.Option(None, "--note", help="Why you saved it."),
    date: dt.datetime = typer.Option(
        None, "--date", formats=["%Y-%m-%d"], help="Override captured_on."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations; accept top theme."),
) -> None:
    """Capture a quote or idea (FR-1), with theme suggestions and attribution flags."""
    service = state.service()
    duplicates = _guard(service.duplicates_of, text)
    if duplicates and not yes:
        typer.echo(f"warning: {duplicates[0].id} already holds the same normalized text:")
        typer.echo(render._wrap(duplicates[0].text, indent="  "))
        if not _confirm("add it anyway?", default=False):
            raise fail("aborted: duplicate not added")
    themes = list(theme or [])
    if not themes and not yes:
        suggestions = _guard(service.suggest, text, tag or [], note)
        if suggestions:
            typer.echo("suggested themes:")
            typer.echo(render.render_suggestions(suggestions))
            if _confirm(f"apply theme '{suggestions[0].theme_id}'?", default=False):
                themes = [suggestions[0].theme_id]
    result = _guard(
        service.capture,
        text=text,
        kind=kind,
        author=author,
        source=source,
        url=url,
        note=note,
        tags=tag or [],
        themes=themes,
        captured_on=date.date() if date else None,
        accept_suggestions=yes and not themes,
    )
    typer.echo(render.render_capture(result))


@app.command("import")
def import_file(
    file: Path = typer.Argument(None, help="JSON or CSV file to import."),
    fmt: str = typer.Option(None, "--format", help="json | csv (inferred from the suffix)."),
    starter: bool = typer.Option(False, "--starter", help="Load the committed starter pack."),
) -> None:
    """Import entries (FR-5) and print the capacity-derived drain horizon."""
    service = state.service()
    if starter:
        report = _guard(service.import_starter, starter_pack())
    else:
        if file is None:
            raise fail("give a FILE to import, or use --starter")
        if not file.exists():
            raise fail(f"no such file: {file}")
        payload = file.read_text(encoding="utf-8")
        chosen = fmt or ("csv" if file.suffix.lower() == ".csv" else "json")
        if chosen not in {"json", "csv"}:
            raise fail(f"unknown format {chosen!r}; expected json or csv")
        report = _guard(service.import_csv if chosen == "csv" else service.import_json, payload)
    typer.echo(render.render_import(report))


@app.command("export")
def export_library(
    out: Path = typer.Option(None, "--out", help="Write here instead of stdout."),
) -> None:
    """Export the whole library as one JSON document (FR-5)."""
    document = _guard(state.service().export_library).model_dump_json(indent=2)
    if out is None:
        typer.echo(document)
    else:
        out.write_text(document + "\n", encoding="utf-8")
        typer.echo(f"wrote {out}")


# --------------------------------------------------------------------------
# the daily loop
# --------------------------------------------------------------------------


@app.command()
def today(
    date: dt.datetime = typer.Option(None, "--date", formats=["%Y-%m-%d"]),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Materialize today's card set once and render it (FR-8)."""
    service = state.service()
    on = date.date() if date else service.clock.today()
    cards = _guard(service.materialize_day, on)
    if as_json:
        _dump(cards)
    else:
        typer.echo(render.render_cards(cards, on))


@app.command()
def draw(
    theme: str = typer.Option(None, "--theme"),
    collection: str = typer.Option(None, "--collection"),
    date: dt.datetime = typer.Option(None, "--date", formats=["%Y-%m-%d"]),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Pull an extra card on demand (FR-8/FR-13). It never uses a daily slot."""
    service = state.service()
    card = _guard(
        service.draw,
        on_date=date.date() if date else None,
        theme_id=theme,
        collection_id=collection,
    )
    if card is None:
        raise fail("no eligible entry matches that draw")
    if as_json:
        _dump(card)
    else:
        typer.echo(render.render_card(card))


@app.command()
def reflect(
    surfacing_id: str = typer.Argument(None, help="Surfacing to reflect on."),
    last: bool = typer.Option(False, "--last", help="Reflect on the most recent card."),
    grade: Grade = typer.Option(..., "--grade", help="applied | resonated | flat"),
    text: str = typer.Option(None, "--text", help="How it actually landed."),
) -> None:
    """Log an immutable reflection against a surfacing (FR-11)."""
    service = state.service()
    target = surfacing_id
    if last:
        newest = service.latest_surfacing()
        if newest is None:
            raise fail("nothing has been surfaced yet")
        target = newest.id
    if not target:
        raise fail("give a SURFACING_ID or use --last")
    reflection = _guard(service.reflect, target, grade, text)
    entry = service.get_entry(reflection.entry_id)
    state_after = service.repo.get_state(reflection.entry_id)
    typer.echo(f"logged {reflection.grade} on {target}")
    typer.echo(render._wrap(entry.text, indent="  "))
    if state_after is not None:
        typer.echo(
            f"  the next surfacing will apply this grade; current interval "
            f"{state_after.interval_days}d, flat streak {state_after.flat_streak}"
        )


# --------------------------------------------------------------------------
# browsing and curation
# --------------------------------------------------------------------------


@app.command("list")
def list_entries(
    status: str = typer.Option("active", "--status", help="active | archived | all"),
    tag: str = typer.Option(None, "--tag"),
    theme: str = typer.Option(None, "--theme"),
    kind: EntryKind = typer.Option(None, "--kind"),
    pinned: bool = typer.Option(None, "--pinned/--not-pinned"),
    limit: int = typer.Option(50, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List entries; every filter composes (FR-12)."""
    if status not in _STATUS:
        raise fail(f"unknown status {status!r}; expected active, archived or all")
    rows = _guard(
        state.service().list_entries,
        status=_STATUS[status],
        pinned=pinned,
        kind=kind,
        tag=tag,
        theme=theme,
        limit=limit,
    )
    _dump(rows) if as_json else typer.echo(render.render_entries(rows))


@app.command()
def search(
    query: str = typer.Argument(..., help="Words to look for in text, author, source and note."),
    limit: int = typer.Option(20, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Full-text search, deterministically ranked (FR-12)."""
    rows = _guard(state.service().search, query, limit)
    _dump(rows) if as_json else typer.echo(render.render_entries(rows))


@app.command()
def show(
    entry_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one entry with its scheduler state and full history."""
    detail = _guard(state.service().entry_detail, entry_id)
    _dump(detail) if as_json else typer.echo(render.render_detail(detail))


@app.command()
def edit(
    entry_id: str = typer.Argument(...),
    text: str = typer.Option(None, "--text", help="Only typo-level edits once surfaced (FR-4)."),
    author: str = typer.Option(None, "--author"),
    source: str = typer.Option(None, "--source"),
    url: str = typer.Option(None, "--url"),
    note: str = typer.Option(None, "--note"),
    tag: list[str] = typer.Option(None, "--tag", help="Replaces the whole tag set."),
    theme: list[str] = typer.Option(None, "--theme", help="Replaces the whole theme set."),
) -> None:
    """Edit entry fields (FR-4)."""
    entry = _guard(
        state.service().edit_entry,
        entry_id,
        text=text,
        author=author,
        source=source,
        url=url,
        note=note,
        tags=list(tag) if tag else None,
        themes=list(theme) if theme else None,
    )
    typer.echo(f"updated {entry.id}")
    typer.echo(render._wrap(entry.text, indent="  "))


@app.command()
def pin(entry_id: str = typer.Argument(...)) -> None:
    """Pin an entry: it comes back inside the FR-7 rescue guarantee."""
    entry, warning = _guard(state.service().pin, entry_id)
    typer.echo(f"pinned {entry.id}")
    if warning:
        typer.echo(f"warning: {warning}")


@app.command()
def unpin(entry_id: str = typer.Argument(...)) -> None:
    """Remove the pinned-rescue guarantee from an entry."""
    typer.echo(f"unpinned {_guard(state.service().unpin, entry_id).id}")


@app.command()
def archive(entry_id: str = typer.Argument(...)) -> None:
    """Archive an entry: it stops surfacing but keeps all its history."""
    typer.echo(f"archived {_guard(state.service().archive, entry_id).id}")


@app.command()
def restore(entry_id: str = typer.Argument(...)) -> None:
    """Return an archived entry to the rotation."""
    typer.echo(f"restored {_guard(state.service().restore, entry_id).id}")


@app.command()
def tags(as_json: bool = typer.Option(False, "--json")) -> None:
    """List every tag in use."""
    names = [tag.name for tag in state.service().repo.list_tags()]
    _dump(names) if as_json else typer.echo("\n".join(names) or "(no tags yet)")


@app.command()
def themes(as_json: bool = typer.Option(False, "--json")) -> None:
    """List the fixed 16-theme taxonomy (FR-3)."""
    rows = default_datasets().themes
    if as_json:
        _dump(rows)
        return
    for theme in rows:
        typer.echo(f"{theme.id:<26} {theme.name}")
        typer.echo(render._wrap(theme.description, indent="    "))


@app.command()
def stats(
    date: dt.datetime = typer.Option(None, "--date", formats=["%Y-%m-%d"]),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Coverage, streaks, novelty share, pinned guarantee and the capacity block (FR-14)."""
    report = _guard(state.service().stats, date.date() if date else None)
    _dump(report) if as_json else typer.echo(render.render_stats(report))


@app.command("check-attribution")
def check_attribution(
    entry_id: str = typer.Option(None, "--entry"),
    text: str = typer.Option(None, "--text"),
    author: str = typer.Option(None, "--author"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Check a quote against the curated misattribution dataset (FR-2)."""
    service = state.service()
    if entry_id:
        entry = _guard(service.get_entry, entry_id)
        text, author = entry.text, entry.author
    if not text:
        raise fail("give --entry ID or --text TEXT")
    findings = _guard(service.check_attribution, text, author)
    if as_json:
        _dump(findings)
        return
    if not findings:
        typer.echo("no known misattribution matches (absence of a flag is not verification)")
        return
    for finding in findings:
        typer.echo(f"[{finding.verdict}] {finding.note}")
        typer.echo(f"  likely origin: {finding.likely_origin}")
        if finding.reference_url:
            typer.echo(f"  {finding.reference_url}")


# --------------------------------------------------------------------------
# collections and config
# --------------------------------------------------------------------------


@collection_app.command("create")
def collection_create(
    name: str = typer.Argument(...),
    desc: str = typer.Option(None, "--desc"),
) -> None:
    """Create a named collection."""
    created = _guard(state.service().create_collection, name, desc)
    typer.echo(f"created collection {created.id}  {created.name}")


@collection_app.command("list")
def collection_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List collections with their sizes."""
    service = state.service()
    rows = service.repo.list_collections()
    if as_json:
        _dump(rows)
        return
    for row in rows:
        size = len(service.repo.collection_entry_ids(row.id))
        typer.echo(f"{row.id}  {row.name}  ({size} entries)")
    if not rows:
        typer.echo("(no collections yet)")


@collection_app.command("show")
def collection_show(collection_id: str = typer.Argument(...)) -> None:
    """Show a collection's ordered members."""
    service = state.service()
    row = service.repo.get_collection(collection_id)
    if row is None:
        raise fail(f"unknown collection {collection_id}")
    typer.echo(f"{row.name} — {row.description or 'no description'}")
    for position, entry_id in enumerate(service.repo.collection_entry_ids(collection_id)):
        entry = service.repo.get_entry(entry_id)
        marker = " (archived)" if entry and entry.status == EntryStatus.ARCHIVED else ""
        typer.echo(
            f"  {position}. {entry_id}{marker}  {render._excerpt(entry.text) if entry else ''}"
        )


@collection_app.command("add")
def collection_add(
    collection_id: str = typer.Argument(...), entry_id: str = typer.Argument(...)
) -> None:
    """Append an entry to a collection."""
    position = _guard(state.service().add_to_collection, collection_id, entry_id)
    typer.echo(f"added {entry_id} at position {position}")


@collection_app.command("rm")
def collection_rm(
    collection_id: str = typer.Argument(...), entry_id: str = typer.Argument(...)
) -> None:
    """Remove an entry from a collection and re-pack positions."""
    _guard(state.service().remove_from_collection, collection_id, entry_id)
    typer.echo(f"removed {entry_id} from {collection_id}")


@collection_app.command("delete")
def collection_delete(collection_id: str = typer.Argument(...)) -> None:
    """Delete a collection (its entries are untouched)."""
    service = state.service()
    if service.repo.get_collection(collection_id) is None:
        raise fail(f"unknown collection {collection_id}")
    service.repo.delete_collection(collection_id)
    typer.echo(f"deleted {collection_id}")


@config_app.command("get")
def config_get(key: str = typer.Argument(None)) -> None:
    """Print one config key, or all of them."""
    service = state.service()
    if key is None:
        for name, value in sorted(service.repo.all_config().items()):
            typer.echo(f"{name} = {value}")
        return
    value = service.repo.get_config(key)
    if value is None:
        raise fail(f"no config key {key!r}")
    typer.echo(value)


@config_app.command("set")
def config_set(key: str = typer.Argument(...), value: str = typer.Argument(...)) -> None:
    """Set ``seed`` or ``batch_k``. Every surfacing stamps them, so changes stay auditable."""
    service = state.service()
    if key == "seed":
        _guard(service.set_seed, int(value))
    elif key == "batch_k":
        _guard(service.set_batch_k, int(value))
    else:
        raise fail(f"{key!r} is not user-settable; try seed or batch_k")
    typer.echo(f"{key} = {value}")


def main() -> None:
    """Console-script entry point (``almanac``)."""
    try:
        app()
    except (ServiceError, RepositoryError) as exc:  # pragma: no cover - safety net
        typer.echo(f"error: {exc}", err=True)
        sys.exit(1)
