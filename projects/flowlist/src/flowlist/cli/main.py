"""Typer CLI (FR-14).

Thin: every command parses its options, calls one :mod:`flowlist.services`
function and renders the result.  Two rules hold everywhere —

* ``--json`` prints a machine-readable payload instead of the human table, and
* a domain error (:class:`~flowlist.errors.FlowlistError`) prints
  ``error: <code>: <message>`` on stderr and exits non-zero.

Timestamps are read here, at the process boundary, and passed into the
services layer; nothing below it reads a clock (CONVENTIONS 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from flowlist import ENGINE_VERSION, services
from flowlist.engine.explain import (
    compare_reports,
    explain_transition,
    summarize_report,
)
from flowlist.engine.keys import parse_key
from flowlist.engine.models import (
    COMPONENT_NAMES,
    ArcProfile,
    ExportFormat,
    PlaylistSource,
    ReorderParams,
    TransitionWeights,
)
from flowlist.errors import FlowlistError
from flowlist.store import DEFAULT_DB_PATH
from flowlist.store.base import Repository
from flowlist.store.sqlite import SqliteRepository

#: Exit code used for every domain failure (FR-14: non-zero on error).
ERROR_EXIT = 1

app = typer.Typer(
    name="flowlist",
    help=(
        "Reorder a playlist so consecutive tracks transition seamlessly: "
        "harmonic (Camelot) compatibility, BPM proximity, energy continuity "
        "and loudness matching."
    ),
    no_args_is_help=True,
    add_completion=False,
)
features_app = typer.Typer(help="Inspect and override a track's musical facts (FR-4).")
app.add_typer(features_app, name="features")


@dataclass
class Context:
    """Shared CLI state: where the database is and how to print."""

    db: str
    json_output: bool

    def open(self) -> Repository:
        return SqliteRepository(self.db)


def _state(ctx: typer.Context) -> Context:
    return ctx.ensure_object(Context)


def _now() -> datetime:
    return datetime.now(UTC)


def _emit(ctx: typer.Context, payload: dict[str, Any], lines: list[str]) -> None:
    """Print ``payload`` as JSON with ``--json``, otherwise ``lines``."""
    if _state(ctx).json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        for line in lines:
            typer.echo(line)


def _fail(exc: FlowlistError) -> typer.Exit:
    typer.secho(f"error: {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=ERROR_EXIT)


def parse_weights(raw: str | None) -> TransitionWeights | None:
    """``--weights key=.35,bpm=.35,energy=.2`` -> validated weights (FR-6).

    Unknown names and non-numeric values raise
    :class:`~flowlist.errors.InvalidWeightsError`, which the command turns into
    a non-zero exit.
    """
    if not raw:
        return None
    from flowlist.errors import InvalidWeightsError

    values: dict[str, float] = {}
    for chunk in raw.split(","):
        if not chunk.strip():
            continue
        name, sep, value = chunk.partition("=")
        if not sep:
            raise InvalidWeightsError(
                f"weights must look like key=0.35,bpm=0.35; got {chunk!r}",
                allowed=list(COMPONENT_NAMES),
            )
        try:
            values[name.strip()] = float(value)
        except ValueError as exc:
            raise InvalidWeightsError(
                f"weight {name.strip()!r} is not a number: {value!r}",
                allowed=list(COMPONENT_NAMES),
            ) from exc
    return TransitionWeights.parse(values)


@app.callback()
def main_callback(
    ctx: typer.Context,
    db: Annotated[
        str, typer.Option("--db", help="SQLite database path.", show_default=True)
    ] = DEFAULT_DB_PATH,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON instead of tables.")
    ] = False,
) -> None:
    ctx.obj = Context(db=db, json_output=json_output)


# --------------------------------------------------------------------------- #
# Import / listing
# --------------------------------------------------------------------------- #


@app.command("import")
def import_command(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="CSV/JSON export, or a music directory.")],
    name: Annotated[str | None, typer.Option("--name", help="Playlist name.")] = None,
    fmt: Annotated[
        str | None,
        typer.Option("--format", help="csv | json | dir (guessed from the path by default)."),
    ] = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Replace an existing playlist's entries (FR-1).")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Also delete that playlist's runs (FR-1).")
    ] = False,
) -> None:
    """Import a playlist from a CSV/JSON export or a folder of audio files (FR-1/2)."""
    source = {"dir": PlaylistSource.DIRECTORY}.get(fmt or "")
    if fmt and source is None:
        try:
            source = PlaylistSource(fmt)
        except ValueError:
            typer.secho(f"error: unknown --format {fmt!r}; use csv, json or dir", err=True)
            raise typer.Exit(code=ERROR_EXIT) from None
    try:
        with _state(ctx).open() as repo:
            imported = services.read_playlist(str(path), name=name, fmt=source)
            result = services.import_playlist(
                repo, imported, now=_now(), replace=replace, force=force
            )
            _emit(
                ctx,
                {
                    "playlist": result.playlist.model_dump(),
                    "entries": len(result.entries),
                    "replaced": result.replaced,
                    "warnings": services.issues_as_dicts(result.warnings),
                    "skipped": services.issues_as_dicts(result.skipped),
                },
                [
                    f"{'replaced' if result.replaced else 'imported'} "
                    f"{result.playlist.name}: {len(result.entries)} entries "
                    f"({result.playlist.id})",
                    *(f"  warning line {issue.line}: {issue.message}" for issue in result.warnings),
                    *(f"  skipped line {issue.line}: {issue.message}" for issue in result.skipped),
                ],
            )
    except FlowlistError as exc:
        raise _fail(exc) from exc


@app.command("ls")
def ls_command(ctx: typer.Context) -> None:
    """List stored playlists."""
    with _state(ctx).open() as repo:
        rows = [
            {
                "id": playlist.id,
                "name": playlist.name,
                "source": playlist.source.value,
                "entries": len(repo.get_entries(playlist.id)),
                "applied_run_id": playlist.applied_run_id,
                "created_at": playlist.created_at,
            }
            for playlist in repo.list_playlists()
        ]
    _emit(
        ctx,
        {"playlists": rows},
        [
            f"{row['name']:<24} {row['entries']:>4} entries  {row['source']:<9} {row['id']}"
            for row in rows
        ]
        or ["no playlists yet — try `flowlist import <file> --name <name>`"],
    )


@app.command("show")
def show_command(
    ctx: typer.Context,
    playlist: Annotated[str, typer.Argument(help="Playlist id or name.")],
    transitions: Annotated[
        bool, typer.Option("--transitions", help="Also score the stored order (FR-7).")
    ] = False,
) -> None:
    """Show a playlist's tracks in stored order with their resolved features (US-1)."""
    try:
        with _state(ctx).open() as repo:
            view, report = services.score_playlist(repo, playlist)
            rows = []
            for entry in view.entries:
                track = view.tracks[entry.track_id]
                snapshot = view.features[entry.track_id]
                rows.append(
                    {
                        "position": entry.position,
                        "entry_id": entry.id,
                        "track_id": track.id,
                        "artist": track.artist,
                        "title": track.title,
                        "features": snapshot.snapshot().model_dump(),
                    }
                )
            lines = [
                f"{row['position']:>3}  {row['artist']} - {row['title']}"
                f"  [{_feature_phrase(row['features'])}]"
                for row in rows
            ]
            lines.append(summarize_report(report, "stored order"))
            if transitions:
                lines.extend(explain_transition(t, i) for i, t in enumerate(report.transitions))
            _emit(
                ctx,
                {
                    "playlist": view.playlist.model_dump(),
                    "coverage": view.coverage.model_dump(),
                    "entries": rows,
                    "report": _report_payload(report) if transitions else None,
                },
                lines,
            )
    except FlowlistError as exc:
        raise _fail(exc) from exc


def _feature_phrase(features: dict[str, Any]) -> str:
    from flowlist.engine.keys import camelot

    parts = []
    bpm = features.get("bpm")
    parts.append(f"{bpm:g} BPM" if bpm is not None else "no BPM")
    if features.get("key_pc") is not None and features.get("mode") is not None:
        parts.append(camelot(int(features["key_pc"]), int(features["mode"])))
    else:
        parts.append("no key")
    energy = features.get("energy")
    parts.append(f"e={energy:.2f}" if energy is not None else "no energy")
    loud = features.get("loudness_db")
    parts.append(f"{loud:.1f} dB" if loud is not None else "no loudness")
    return " ".join(parts)


def _report_payload(report: Any) -> dict[str, Any]:
    return {
        "order": report.order,
        "total": report.total,
        "mean": report.mean,
        "min_score": report.min_score,
        "seamless": report.seamless,
        "cliffs": report.cliffs,
    }


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #


@app.command("analyze")
def analyze_command(
    ctx: typer.Context,
    playlist: Annotated[str, typer.Argument(help="Playlist id or name.")],
    local: Annotated[
        bool, typer.Option("--local", help="Measure owned audio files (needs the `audio` extra).")
    ] = False,
    catalog: Annotated[
        Path | None,
        typer.Option("--catalog", help="Offline JSON feature catalog to resolve from."),
    ] = None,
) -> None:
    """Resolve audio features for every track and report coverage (FR-3, US-2)."""
    analyzer = None
    if local:
        from flowlist.adapters.analyzer import LibrosaLocalAnalyzer

        analyzer = LibrosaLocalAnalyzer()
        if not analyzer.available():
            typer.secho(
                "note: local analysis needs the optional audio extra "
                "(`uv pip install 'flowlist[audio]'`); falling back to the other providers",
                fg=typer.colors.YELLOW,
                err=True,
            )
            analyzer = None
    try:
        with _state(ctx).open() as repo:
            coverage = services.analyze_playlist(
                repo,
                playlist,
                now=_now(),
                providers=services.default_providers(catalog),
                analyzer=analyzer,
            )
            _emit(
                ctx,
                coverage.model_dump(),
                [
                    coverage.summary,
                    *(
                        f"  {track_id}: missing {', '.join(fields)}"
                        for track_id, fields in sorted(coverage.per_track_missing.items())
                    ),
                ],
            )
    except FlowlistError as exc:
        raise _fail(exc) from exc


@features_app.command("set")
def features_set_command(
    ctx: typer.Context,
    track: Annotated[str, typer.Argument(help="Track id (see `flowlist show`).")],
    bpm: Annotated[float | None, typer.Option("--bpm", help="Tempo in BPM (40-260).")] = None,
    key: Annotated[
        str | None, typer.Option("--key", help="Camelot code (8A) or musical name (Am, F#m).")
    ] = None,
    energy: Annotated[float | None, typer.Option("--energy", help="0-1.")] = None,
    loudness: Annotated[
        float | None, typer.Option("--loudness", help="Integrated loudness in dB (-60..0).")
    ] = None,
    danceability: Annotated[float | None, typer.Option("--danceability", help="0-1.")] = None,
) -> None:
    """Store a manual override; it wins over every provider from then on (FR-4)."""
    key_pc = mode = None
    if key is not None:
        try:
            key_pc, mode = parse_key(key)
        except ValueError as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=ERROR_EXIT) from exc
    try:
        with _state(ctx).open() as repo:
            stored = services.set_manual_features(
                repo,
                track,
                now=_now(),
                bpm=bpm,
                key_pc=key_pc,
                mode=mode,
                energy=energy,
                loudness_db=loudness,
                danceability=danceability,
            )
            _emit(
                ctx,
                stored.model_dump(),
                [f"manual override for {track}: {_feature_phrase(stored.model_dump())}"],
            )
    except FlowlistError as exc:
        raise _fail(exc) from exc


# --------------------------------------------------------------------------- #
# Reordering
# --------------------------------------------------------------------------- #


@app.command("reorder")
def reorder_command(
    ctx: typer.Context,
    playlist: Annotated[str, typer.Argument(help="Playlist id or name.")],
    seed: Annotated[int, typer.Option("--seed", help="Seed; same seed, same order (FR-15).")] = 0,
    profile: Annotated[
        ArcProfile, typer.Option("--profile", help="Energy arc bias (FR-10).")
    ] = ArcProfile.NEUTRAL,
    start_track: Annotated[
        str | None, typer.Option("--start-track", help="Entry id pinned to position 0 (FR-9).")
    ] = None,
    end_track: Annotated[
        str | None, typer.Option("--end-track", help="Entry id pinned to the last position.")
    ] = None,
    weights: Annotated[
        str | None,
        typer.Option("--weights", help="e.g. key=0.35,bpm=0.35,energy=0.2,loudness=0.1"),
    ] = None,
    max_passes: Annotated[
        int, typer.Option("--max-passes", help="Local-search pass cap (FR-8).")
    ] = 50,
    apply_now: Annotated[
        bool, typer.Option("--apply", help="Write the new order back to the playlist (FR-12).")
    ] = False,
) -> None:
    """Reorder for flow and print a before/after scorecard (FR-8/9/10, US-3)."""
    try:
        params = ReorderParams(
            seed=seed,
            weights=parse_weights(weights) or TransitionWeights(),
            profile=profile,
            start_entry=start_track,
            end_entry=end_track,
            max_passes=max_passes,
        )
        with _state(ctx).open() as repo:
            outcome = services.reorder_playlist(repo, playlist, params, now=_now(), apply=apply_now)
            _emit(
                ctx,
                {
                    "run_id": outcome.run.id,
                    "applied": outcome.applied,
                    "params": outcome.run.params.model_dump(mode="json"),
                    "before": _report_payload(outcome.before),
                    "after": _report_payload(outcome.after),
                    "order": [entry.entry_id for entry in outcome.run_entries],
                },
                [
                    f"run {outcome.run.id}",
                    *compare_reports(outcome.before, outcome.after),
                    *(["applied to the playlist"] if outcome.applied else []),
                ],
            )
    except FlowlistError as exc:
        raise _fail(exc) from exc


@app.command("runs")
def runs_command(
    ctx: typer.Context,
    playlist: Annotated[
        str | None, typer.Argument(help="Playlist id or name; omit for every run.")
    ] = None,
) -> None:
    """List stored reorder runs (append-only history, FR-11)."""
    try:
        with _state(ctx).open() as repo:
            playlist_id = None
            if playlist is not None:
                playlist_id = services.require_playlist(repo, playlist).id
            rows = [
                {
                    "id": run.id,
                    "playlist_id": run.playlist_id,
                    "created_at": run.created_at,
                    "seed": run.seed,
                    "profile": run.params.profile.value,
                    "mean_before": run.score_mean_before,
                    "mean_after": run.score_mean_after,
                }
                for run in repo.list_runs(playlist_id)
            ]
    except FlowlistError as exc:
        raise _fail(exc) from exc
    _emit(
        ctx,
        {"runs": rows},
        [
            f"{row['id']}  seed={row['seed']:<4} {row['profile']:<7} "
            f"mean {row['mean_before']:.3f} -> {row['mean_after']:.3f}"
            for row in rows
        ]
        or ["no runs yet — try `flowlist reorder <playlist>`"],
    )


@app.command("explain")
def explain_command(
    ctx: typer.Context,
    run: Annotated[str, typer.Argument(help="Run id (see `flowlist runs`).")],
) -> None:
    """Show every transition of a run with its component breakdown (US-4)."""
    try:
        with _state(ctx).open() as repo:
            view = services.load_run(repo, run)
    except FlowlistError as exc:
        raise _fail(exc) from exc
    lines: list[str] = []
    payload: list[dict[str, Any]] = []
    for run_entry in view.run_entries:
        entry = view.entries.get(run_entry.entry_id)
        track = view.tracks.get(entry.track_id) if entry else None
        label = track.display if track else run_entry.entry_id
        if run_entry.transition is None:
            lines.append(f"{run_entry.position:>3}      {label}")
        else:
            lines.append(explain_transition(run_entry.transition, run_entry.position - 1))
            lines.append(f"{run_entry.position:>3}      {label}")
        payload.append(
            {
                "position": run_entry.position,
                "entry_id": run_entry.entry_id,
                "track": label,
                "transition": None
                if run_entry.transition is None
                else run_entry.transition.model_dump(mode="json"),
            }
        )
    _emit(ctx, {"run_id": view.run.id, "entries": payload}, lines)


@app.command("compare")
def compare_command(
    ctx: typer.Context,
    run: Annotated[str, typer.Argument(help="Run id.")],
) -> None:
    """Print a run's before/after scorecard (US-3)."""
    try:
        with _state(ctx).open() as repo:
            view = services.load_run(repo, run)
            rebuilt = services.rebuild_after_report(view)
    except FlowlistError as exc:
        raise _fail(exc) from exc
    run_row = view.run
    _emit(
        ctx,
        {
            "run_id": run_row.id,
            "before": {
                "mean": run_row.score_mean_before,
                "min": run_row.score_min_before,
                "total": run_row.score_total_before,
                "seamless": run_row.seamless_before,
                "cliffs": run_row.cliff_before,
            },
            "after": {
                "mean": run_row.score_mean_after,
                "min": run_row.score_min_after,
                "total": run_row.score_total_after,
                "seamless": run_row.seamless_after,
                "cliffs": run_row.cliff_after,
            },
            "recomputed_after": _report_payload(rebuilt),
            "coverage": run_row.coverage.model_dump(),
        },
        [
            f"run {run_row.id} on playlist {view.playlist.name}",
            f"before: mean={run_row.score_mean_before:.3f} min={run_row.score_min_before:.3f} "
            f"total={run_row.score_total_before:.3f} "
            f"seamless={run_row.seamless_before} cliffs={run_row.cliff_before}",
            f"after : mean={run_row.score_mean_after:.3f} min={run_row.score_min_after:.3f} "
            f"total={run_row.score_total_after:.3f} "
            f"seamless={run_row.seamless_after} cliffs={run_row.cliff_after}",
            f"coverage: {run_row.coverage.summary}",
        ],
    )


@app.command("apply")
def apply_command(
    ctx: typer.Context,
    run: Annotated[str, typer.Argument(help="Run id.")],
) -> None:
    """Write a run's ordering back to its playlist (FR-12)."""
    try:
        with _state(ctx).open() as repo:
            playlist = services.apply_run(repo, run)
    except FlowlistError as exc:
        raise _fail(exc) from exc
    _emit(
        ctx,
        {"playlist": playlist.model_dump(), "applied_run_id": playlist.applied_run_id},
        [f"applied run {run} to {playlist.name}"],
    )


@app.command("export")
def export_command(
    ctx: typer.Context,
    run: Annotated[str, typer.Argument(help="Run id.")],
    fmt: Annotated[ExportFormat, typer.Option("--format", help="m3u | csv | json.")] = (
        ExportFormat.M3U
    ),
    out: Annotated[
        Path | None, typer.Option("--out", help="Destination file; omit to print to stdout.")
    ] = None,
) -> None:
    """Export a run's ordering as M3U8, CSV or JSON (FR-12, US-7)."""
    try:
        with _state(ctx).open() as repo:
            if out is None:
                typer.echo(services.render_export(repo, run, fmt), nl=False)
                return
            written = services.write_export(repo, run, fmt, str(out))
    except FlowlistError as exc:
        raise _fail(exc) from exc
    _emit(ctx, {"path": written, "format": fmt.value}, [f"wrote {written}"])


@app.command("delete")
def delete_command(
    ctx: typer.Context,
    playlist: Annotated[str, typer.Argument(help="Playlist id or name.")],
    force: Annotated[
        bool, typer.Option("--force", help="Also delete the playlist's runs (DATA_MODEL 2.3).")
    ] = False,
) -> None:
    """Delete a playlist; refused while runs reference it unless --force."""
    try:
        with _state(ctx).open() as repo:
            services.delete_playlist(repo, playlist, force=force)
    except FlowlistError as exc:
        raise _fail(exc) from exc
    _emit(ctx, {"deleted": playlist}, [f"deleted {playlist}"])


@app.command("serve")
def serve_command(
    ctx: typer.Context,
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
) -> None:
    """Run the FastAPI app with uvicorn against the same database (FR-13)."""
    import uvicorn

    from flowlist.api import create_app

    state = _state(ctx)
    typer.echo(f"flowlist {ENGINE_VERSION} serving {state.db} on http://{host}:{port}")
    uvicorn.run(create_app(db_path=state.db), host=host, port=port)


def main() -> None:
    """Console-script entry point declared in ``pyproject.toml``."""
    app()


__all__ = ["ERROR_EXIT", "app", "main", "parse_weights"]
