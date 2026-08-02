"""The Typer CLI (SCOPE.md FR-18, §Architecture-CLI).

Thin: parse arguments, call one service method, print.  Every command exits 0
on success and non-zero on failure, and every command accepts ``--json`` for
machine-readable output.  ``--date`` defaults to today read *here*, at the
edge — never inside the engine (FR-19).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

import typer

from dresscast import __version__
from dresscast.cli import render
from dresscast.engine.models import DayBrief, Garment, Recommendation
from dresscast.errors import DresscastError
from dresscast.services import (
    DresscastService,
    build_service,
    parse_hours,
    parse_window,
)

app = typer.Typer(
    name="dresscast",
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Weather-aware outfit assembly from your photographed wardrobe.\n\n"
        "Catalog your closet, ask what the day demands, and get ranked, "
        "complete outfits with an hour-by-hour layer plan and the reasoning "
        "spelled out."
    ),
)

#: Exit code for a structured domain error (FR-18: non-zero on failure).
EXIT_ERROR = 1


class Context:
    """Global options plus the lazily opened service."""

    def __init__(
        self,
        *,
        db: str | None,
        config: str | None,
        data_dir: str | None,
        as_json: bool,
        units: str,
    ) -> None:
        self.db = db
        self.config = config
        self.data_dir = data_dir
        self.as_json = as_json
        self.units = units
        self._service: DresscastService | None = None

    @property
    def service(self) -> DresscastService:
        if self._service is None:
            self._service = build_service(
                db=self.db, config_path=self.config, data_dir=self.data_dir
            )
        return self._service

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> str:
        return self.now().astimezone().date().isoformat()


def _ctx(ctx: typer.Context) -> Context:
    return ctx.obj  # type: ignore[no-any-return]


def _echo(lines: Sequence[str]) -> None:
    for line in lines:
        typer.echo(line)


def _dump(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def run(action: Callable[[], None]) -> None:
    """Execute a command body, turning domain errors into a non-zero exit."""
    try:
        action()
    except DresscastError as exc:
        typer.echo(f"error [{exc.code}]: {exc.message}", err=True)
        if exc.detail:
            typer.echo(json.dumps(exc.detail, indent=2, sort_keys=True, default=str), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _names(service: DresscastService) -> dict[str, str]:
    return {g.id: g.name for g in service.list_garments()}


# --------------------------------------------------------------------------
# Global options
# --------------------------------------------------------------------------


@app.callback()
def main_options(
    ctx: typer.Context,
    db: Annotated[
        str | None, typer.Option("--db", help="SQLite database path (default ~/.dresscast).")
    ] = None,
    config: Annotated[
        str | None, typer.Option("--config", help="Config TOML (default ~/.dresscast/config.toml).")
    ] = None,
    data_dir: Annotated[
        str | None, typer.Option("--data-dir", help="Data directory (default ~/.dresscast).")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of text.")
    ] = False,
    units: Annotated[
        str, typer.Option("--units", help="Display temperatures in 'c' or 'f'.")
    ] = "c",
) -> None:
    """dresscast — what to wear today, from the closet you actually own."""
    if units.lower() not in {"c", "f"}:
        typer.echo("error [invalid_params]: --units must be 'c' or 'f'", err=True)
        raise typer.Exit(code=EXIT_ERROR)
    ctx.obj = Context(
        db=db, config=config, data_dir=data_dir, as_json=as_json, units=units.lower()
    )


# --------------------------------------------------------------------------
# Wardrobe (FR-1, FR-2, FR-3)
# --------------------------------------------------------------------------


@app.command("add")
def add(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", prompt="Name", help="Unique garment name.")],
    category: Annotated[
        str, typer.Option("--category", prompt="Category", help="D1 preset category.")
    ],
    colors: Annotated[
        str,
        typer.Option(
            "--colors",
            prompt="Colors (comma separated, first is the main colour)",
            help="e.g. 'navy' or 'navy,white' or 'rust:20'.",
        ),
    ],
    occasions: Annotated[
        str, typer.Option("--occasions", help="Comma-separated occasions.")
    ] = "casual",
    photo: Annotated[str | None, typer.Option("--photo", help="Photo file to attach.")] = None,
    warmth: Annotated[
        int | None, typer.Option("--warmth", min=0, max=5, help="Warmth level 0-5 (maps to clo).")
    ] = None,
    clo: Annotated[float | None, typer.Option("--clo", help="Explicit clo (overrides preset.")] = None,
    role: Annotated[str | None, typer.Option("--role", help="Override the layer role.")] = None,
    formality: Annotated[
        int | None, typer.Option("--formality", min=1, max=5, help="Formality 1-5.")
    ] = None,
    waterproof: Annotated[int, typer.Option("--waterproof", min=0, max=3)] = 0,
    windproof: Annotated[int, typer.Option("--windproof", min=0, max=2)] = 0,
    wears: Annotated[
        int | None, typer.Option("--wears", min=1, help="Wears before laundry.")
    ] = None,
    tags: Annotated[str, typer.Option("--tags", help="Comma-separated style tags.")] = "",
    accessory_class: Annotated[str | None, typer.Option("--accessory-class")] = None,
) -> None:
    """Add a garment; the category preset fills clo, role, formality and laundry (FR-1)."""
    c = _ctx(ctx)

    def body() -> None:
        garment = c.service.create_garment(
            name=name,
            category=category,
            colors=_split(colors),
            occasions=_split(occasions),
            style_tags=_split(tags),
            clo=clo,
            warmth=warmth,
            layer_role=role,
            accessory_class=accessory_class,
            formality=formality,
            waterproofness=waterproof,
            windproofness=windproof,
            wears_before_laundry=wears,
            now=c.now(),
        )
        if photo:
            garment = c.service.attach_photo(garment.id, photo, now=c.now())
        _emit_garment(c, garment)

    run(body)


def _emit_garment(c: Context, garment: Garment) -> None:
    if c.as_json:
        _dump(garment.model_dump(mode="json"))
    else:
        _echo(render.garment_detail(garment, c.units))


@app.command("ls")
def ls(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option("--status", help="clean|dirty|in_laundry|retired")] = None,
    occasion: Annotated[str | None, typer.Option("--occasion")] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
) -> None:
    """List the wardrobe, optionally filtered."""
    c = _ctx(ctx)

    def body() -> None:
        garments = c.service.list_garments(status=status, occasion=occasion, category=category)
        if c.as_json:
            _dump([g.model_dump(mode="json") for g in garments])
            return
        if not garments:
            typer.echo("No garments match.")
            return
        _echo([render.garment_line(g) for g in garments])
        typer.echo(f"\n{len(garments)} garments.")

    run(body)


@app.command("show")
def show(ctx: typer.Context, garment: str) -> None:
    """Show one garment in full."""
    c = _ctx(ctx)
    run(lambda: _emit_garment(c, c.service.resolve_garment(garment)))


@app.command("edit")
def edit(
    ctx: typer.Context,
    garment: str,
    name: Annotated[str | None, typer.Option("--name")] = None,
    clo: Annotated[float | None, typer.Option("--clo")] = None,
    warmth: Annotated[int | None, typer.Option("--warmth", min=0, max=5)] = None,
    formality: Annotated[int | None, typer.Option("--formality", min=1, max=5)] = None,
    colors: Annotated[str | None, typer.Option("--colors")] = None,
    occasions: Annotated[str | None, typer.Option("--occasions")] = None,
    tags: Annotated[str | None, typer.Option("--tags")] = None,
    waterproof: Annotated[int | None, typer.Option("--waterproof", min=0, max=3)] = None,
    windproof: Annotated[int | None, typer.Option("--windproof", min=0, max=2)] = None,
    wears: Annotated[int | None, typer.Option("--wears", min=1)] = None,
    status: Annotated[str | None, typer.Option("--status", help="Transition the laundry state.")] = None,
    photo: Annotated[str | None, typer.Option("--photo", help="Attach or replace the photo.")] = None,
) -> None:
    """Edit a garment (FR-1) or move it through the laundry states (FR-3)."""
    c = _ctx(ctx)

    def body() -> None:
        updates: dict[str, Any] = {
            "name": name,
            "clo": clo,
            "warmth": warmth,
            "formality": formality,
            "colors": _split(colors) if colors is not None else None,
            "occasions": _split(occasions) if occasions is not None else None,
            "style_tags": _split(tags) if tags is not None else None,
            "waterproofness": waterproof,
            "windproofness": windproof,
            "wears_before_laundry": wears,
            "status": status,
        }
        updated = c.service.edit_garment(garment, now=c.now(), **updates)
        if photo:
            updated = c.service.attach_photo(updated.id, photo, now=c.now())
        _emit_garment(c, updated)

    run(body)


@app.command("suggest")
def suggest(
    ctx: typer.Context,
    garment: str,
    accept: Annotated[
        bool, typer.Option("--accept", help="Accept every proposed field.")
    ] = False,
    accept_fields: Annotated[
        str | None, typer.Option("--accept-fields", help="Comma-separated fields to accept.")
    ] = None,
    reject: Annotated[bool, typer.Option("--reject", help="Reject the suggestion.")] = False,
) -> None:
    """Ask the extractor for attributes and optionally accept them (FR-2)."""
    c = _ctx(ctx)

    def body() -> None:
        suggestion = c.service.suggest(garment, now=c.now())
        fields = _split(accept_fields) if accept_fields else (list(suggestion.payload) if accept else [])
        if reject:
            resolved = c.service.reject_suggestion(suggestion.id, now=c.now())
            if c.as_json:
                _dump(resolved.model_dump(mode="json"))
            else:
                typer.echo(f"Suggestion {resolved.id} rejected; the garment is untouched.")
            return
        if not fields:
            if c.as_json:
                _dump(suggestion.model_dump(mode="json"))
            else:
                typer.echo(f"Suggestion {suggestion.id} ({suggestion.source}) — pending:")
                for field, proposal in sorted(suggestion.payload.items()):
                    typer.echo(
                        f"  {field:<12} {proposal.get('value')!r} "
                        f"(confidence {proposal.get('confidence', 1.0):.2f})"
                    )
                typer.echo("Re-run with --accept or --accept-fields f1,f2 to apply.")
            return
        resolved, updated = c.service.accept_suggestion(suggestion.id, fields, now=c.now())
        if c.as_json:
            _dump(
                {
                    "suggestion": resolved.model_dump(mode="json"),
                    "garment": updated.model_dump(mode="json"),
                }
            )
            return
        typer.echo(f"Accepted {len(resolved.accepted_fields or [])} fields:")
        for entry in resolved.accepted_fields or []:
            typer.echo(f"  {entry.field:<22} {entry.old!r} -> {entry.new!r}  ({entry.via})")
        _echo(render.garment_detail(updated, c.units))

    run(body)


# --------------------------------------------------------------------------
# Weather (FR-4, FR-16)
# --------------------------------------------------------------------------


@app.command("forecast")
def forecast(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option("--date", help="YYYY-MM-DD (default today).")] = None,
) -> None:
    """Fetch (and snapshot) the day's hourly forecast (FR-4)."""
    c = _ctx(ctx)

    def body() -> None:
        day = date or c.today()
        snapshot = c.service.ensure_forecast(day, now=c.now())
        if c.as_json:
            _dump(snapshot.model_dump(mode="json"))
            return
        typer.echo(
            f"{snapshot.date}  {snapshot.location_name} ({snapshot.provider})  "
            f"{len(snapshot.hours)} hours  snapshot {snapshot.id}"
        )
        typer.echo(f"{'hour':>5} {'temp':>8} {'wind':>7} {'rh':>5} {'pop':>5} {'mm/h':>6} {'uv':>5}")
        for h in snapshot.hours:
            typer.echo(
                f"{h.hour:02d}:00 {render.temp(h.temp_c, c.units):>8} {h.wind_kmh:6.1f}k "
                f"{h.humidity_pct:4.0f}% {h.precip_prob:5.2f} {h.precip_mmh:6.1f} {h.uv_index:5.1f}"
            )

    run(body)


@app.command("brief")
def brief(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option("--date")] = None,
    met: Annotated[float | None, typer.Option("--met", help="Metabolic rate (default 1.6).")] = None,
    window: Annotated[
        str | None, typer.Option("--window", help="Wear window, e.g. 07:00-22:00.")
    ] = None,
) -> None:
    """What the day demands — no wardrobe required (FR-16, US-9)."""
    c = _ctx(ctx)

    def body() -> None:
        day = date or c.today()
        payload: DayBrief = c.service.brief(
            date=day,
            met=met,
            wear_window=parse_window(window) if window else None,
            now=c.now(),
        )
        if c.as_json:
            _dump(payload.model_dump(mode="json"))
        else:
            _echo(render.brief_lines(payload, c.units))

    run(body)


# --------------------------------------------------------------------------
# Recommendations (FR-8, FR-15)
# --------------------------------------------------------------------------


@app.command("outfit")
def outfit(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option("--date")] = None,
    occasion: Annotated[str | None, typer.Option("--occasion")] = None,
    k: Annotated[int | None, typer.Option("--k", min=1, max=10)] = None,
    seed: Annotated[int, typer.Option("--seed", help="Persisted; no effect this pass.")] = 0,
    met: Annotated[float | None, typer.Option("--met")] = None,
    window: Annotated[str | None, typer.Option("--window")] = None,
    commute: Annotated[
        str | None, typer.Option("--commute", help="Commute hours, e.g. 7-9,17-19.")
    ] = None,
    plan: Annotated[bool, typer.Option("--plan", help="Print the hourly plan table.")] = False,
) -> None:
    """Rank complete outfits for the day, with reasoning (FR-8, US-3/4/5/6)."""
    c = _ctx(ctx)

    def body() -> None:
        day = date or c.today()
        rec: Recommendation = c.service.recommend(
            date=day,
            occasion=occasion,
            wear_window=parse_window(window) if window else None,
            commute_hours=parse_hours(commute) if commute else None,
            met=met,
            k=k,
            seed=seed,
            now=c.now(),
        )
        if c.as_json:
            _dump(rec.model_dump(mode="json"))
        else:
            _echo(render.recommendation_lines(rec, _names(c.service), units=c.units, plan=plan))

    run(body)


@app.command("explain")
def explain(
    ctx: typer.Context,
    recommendation: Annotated[str, typer.Argument(help="Recommendation id, or 'latest'.")] = "latest",
    rank: Annotated[int, typer.Option("--rank", min=1)] = 1,
) -> None:
    """Print a stored outfit's reasoning and its hourly plan (FR-13, FR-15)."""
    c = _ctx(ctx)

    def body() -> None:
        rec = c.service.resolve_recommendation(recommendation)
        matches = [o for o in rec.outfits if o.rank == rank]
        if not matches:
            typer.echo(f"error [invalid_params]: no rank {rank} in {rec.id}", err=True)
            raise typer.Exit(code=EXIT_ERROR)
        chosen = matches[0]
        if c.as_json:
            _dump(chosen.model_dump(mode="json"))
            return
        typer.echo(f"Recommendation {rec.id} — {rec.date}, occasion {rec.params.occasion}")
        _echo(render.outfit_lines(chosen, _names(c.service), units=c.units, plan=True))

    run(body)


# --------------------------------------------------------------------------
# Wear and laundry (FR-12, FR-3, FR-11)
# --------------------------------------------------------------------------


@app.command("wear")
def wear(
    ctx: typer.Context,
    recommendation: Annotated[
        str | None, typer.Argument(help="Recommendation id (or 'latest').")
    ] = None,
    rank: Annotated[int, typer.Option("--rank", min=1)] = 1,
    items: Annotated[
        str | None, typer.Option("--items", help="Comma-separated garment ids or names.")
    ] = None,
    date: Annotated[str | None, typer.Option("--date")] = None,
    undo: Annotated[str | None, typer.Option("--undo", help="Wear-log id to undo (same day).")] = None,
) -> None:
    """Log what you wore, or undo today's mistaken log (FR-12)."""
    c = _ctx(ctx)

    def body() -> None:
        if undo:
            c.service.undo_wear(undo, today=date or c.today(), now=c.now())
            typer.echo(f"Wear log {undo} undone; counters reversed.")
            return
        if items:
            log = c.service.wear_items(_split(items), date=date or c.today(), now=c.now())
        else:
            log = c.service.wear_recommendation(
                recommendation or "latest", rank=rank, date=date, now=c.now()
            )
        if c.as_json:
            _dump(log.model_dump(mode="json"))
            return
        names = _names(c.service)
        typer.echo(f"Logged {log.date} ({log.source}) as {log.id}:")
        for item in log.items:
            garment = c.service.repo.get_garment(item.garment_id)
            typer.echo(
                f"  {names.get(item.garment_id, item.garment_id):<28} "
                f"{garment.wears_since_wash}/{garment.wears_before_laundry} {garment.status}"
            )

    run(body)


@app.command("laundry")
def laundry(
    ctx: typer.Context,
    garments: Annotated[list[str] | None, typer.Argument(help="Garments to wash.")] = None,
    all_dirty: Annotated[bool, typer.Option("--all", help="Wash every dirty garment.")] = False,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Return garments to clean and reset their wear counters (FR-3)."""
    c = _ctx(ctx)

    def body() -> None:
        event = c.service.launder(
            garments or [], all_dirty=all_dirty, note=note, now=c.now()
        )
        if c.as_json:
            _dump(event.model_dump(mode="json"))
            return
        names = _names(c.service)
        typer.echo(f"Laundered {len(event.garment_ids)} garments ({event.id}):")
        for gid in event.garment_ids:
            typer.echo(f"  {names.get(gid, gid)}")

    run(body)


@app.command("history")
def history(
    ctx: typer.Context,
    days: Annotated[int, typer.Option("--days", min=1, help="Window length in days.")] = 14,
    until: Annotated[str | None, typer.Option("--until", help="Last day (default today).")] = None,
) -> None:
    """Show what was worn recently — the input to FR-11's variety score."""
    c = _ctx(ctx)

    def body() -> None:
        logs = c.service.history(until=until or c.today(), days=days)
        if c.as_json:
            _dump([log.model_dump(mode="json") for log in logs])
            return
        _echo(render.history_lines(logs, _names(c.service)))

    run(body)


@app.command("serve")
def serve(
    ctx: typer.Context,
    port: Annotated[int, typer.Option("--port")] = 8000,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
) -> None:
    """Run the REST API (FR-17) with uvicorn."""
    c = _ctx(ctx)

    def body() -> None:
        import uvicorn

        from dresscast.api import create_app

        db, config, data_dir = c.db, c.config, c.data_dir
        api = create_app(
            service_factory=lambda: build_service(db=db, config_path=config, data_dir=data_dir)
        )
        typer.echo(f"dresscast {__version__} serving on http://{host}:{port}")
        uvicorn.run(api, host=host, port=port, log_level="info")

    run(body)


@app.command("version")
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point (``dresscast``)."""
    app()


if __name__ == "__main__":  # pragma: no cover - module execution path
    main()
