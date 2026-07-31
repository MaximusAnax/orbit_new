"""Typer CLI (SCOPE.md §Architecture-CLI, FR-15).

Thin: parse arguments, call one service method, print.  Exit codes are part of
the contract — 0 success, 1 a datasweep error (unknown run, unstable file,
unsupported format…), 2 a usage error (Typer's own).  Every read command takes
``--json`` so the CLI is scriptable, and every command takes ``--db``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from .. import __version__
from ..adapters.notifier import LogNotifier
from ..engine.models import RunStatus
from ..errors import DatasweepError
from ..services import DatasweepService
from ..store.sqlite import SqliteRepository

DEFAULT_DB = "~/.datasweep/datasweep.db"

app = typer.Typer(
    name="datasweep",
    help=(
        "Watch folders for tabular files, profile them for data-quality defects, "
        "and apply a safe, confidence-tiered cleaning pipeline. "
        "The original file is never modified."
    ),
    no_args_is_help=True,
    add_completion=False,
)
watch_app = typer.Typer(help="Manage watched folders (FR-1).", no_args_is_help=True)
app.add_typer(watch_app, name="watch")


DbOption = Annotated[
    str,
    typer.Option("--db", help="SQLite database path.", envvar="DATASWEEP_DB", show_default=True),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


def build_service(db: str) -> DatasweepService:
    """The production wiring; tests build their own service directly."""
    path = Path(db).expanduser()
    repository = SqliteRepository(path)
    notifier = LogNotifier(path.parent / "notify.log") if path.name != ":memory:" else None
    return DatasweepService(repository, notifier=notifier)


def echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def fail(exc: DatasweepError) -> None:
    """Print the structured error to stderr and exit 1 (FR-15)."""
    typer.echo(f"error [{exc.code}]: {exc.message}", err=True)
    raise typer.Exit(code=1)


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json")


@app.callback()
def main_callback(
    version: Annotated[
        bool, typer.Option("--version", help="Print the datasweep version and exit.")
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


# --------------------------------------------------------------------------
# watch (FR-1)
# --------------------------------------------------------------------------


@watch_app.command("add")
def watch_add(
    path: Annotated[Path, typer.Argument(help="Folder to watch (absolute or relative).")],
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive")] = True,
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include", help="Glob to include; repeatable. Default *.csv *.tsv *.xlsx *.jsonl."
        ),
    ] = None,
    policy: Annotated[
        Path | None, typer.Option("--policy", help="Policy TOML for this folder.")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Artifact output directory.")] = None,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Register a watched folder."""
    service = build_service(db)
    try:
        folder = service.add_folder(
            str(path),
            recursive=recursive,
            include=list(include) if include else None,
            policy_path=str(policy) if policy else None,
            output_dir=str(out) if out else None,
        )
    except DatasweepError as exc:
        fail(exc)
    if as_json:
        echo_json(_dump(folder))
    else:
        typer.echo(f"{folder.id}  {folder.path}  -> {folder.output_dir}")


@watch_app.command("ls")
def watch_ls(db: DbOption = DEFAULT_DB, as_json: JsonOption = False) -> None:
    """List watched folders."""
    folders = build_service(db).list_folders()
    if as_json:
        echo_json([_dump(folder) for folder in folders])
        return
    if not folders:
        typer.echo("no watched folders")
        return
    for folder in folders:
        flags = "recursive" if folder.recursive else "flat"
        typer.echo(
            f"{folder.id}  {folder.path}  [{flags}] include={' '.join(folder.include)} "
            f"-> {folder.output_dir}"
        )


@watch_app.command("rm")
def watch_rm(
    folder_id: Annotated[str, typer.Argument(help="Watched-folder id.")],
    db: DbOption = DEFAULT_DB,
) -> None:
    """Stop watching a folder.  Runs and source files survive as history."""
    if not build_service(db).delete_folder(folder_id):
        typer.echo(f"error [unknown_folder]: no watched folder {folder_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"removed {folder_id}")


# --------------------------------------------------------------------------
# processing (FR-13, US-5)
# --------------------------------------------------------------------------


@app.command("run")
def run_command(
    once: Annotated[bool, typer.Option("--once", help="Single scan pass, then exit.")] = False,
    interval: Annotated[
        int | None, typer.Option("--interval", help="Seconds between passes (daemon mode).")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Reprocess even if already done.")] = False,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Scan the watched folders — once, or in a polling loop (FR-13)."""
    service = build_service(db)
    poll = interval or service.base_policy.general.poll_interval_seconds
    while True:
        try:
            result = service.scan_once(force=force)
        except DatasweepError as exc:
            fail(exc)
        if as_json:
            echo_json(_dump(result))
        else:
            typer.echo(
                f"processed={len(result.processed)} skipped={len(result.skipped)} "
                f"deferred={len(result.deferred)} failed={len(result.failed)}"
            )
        if once:
            break
        time.sleep(poll)


@app.command("clean")
def clean_command(
    file: Annotated[Path, typer.Argument(help="CSV / TSV / XLSX / JSONL file to clean.")],
    out: Annotated[Path | None, typer.Option("--out", help="Artifact output directory.")] = None,
    policy: Annotated[Path | None, typer.Option("--policy", help="Policy TOML.")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Bypass the idempotence check (FR-2).")
    ] = False,
    sheet: Annotated[
        str | None, typer.Option("--sheet", help="XLSX sheet: 1-based index or name.")
    ] = None,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Clean one file: cleaned copy, audit log, findings log, report."""
    service = build_service(db)
    selector: int | str | None = None
    if sheet is not None:
        selector = int(sheet) if sheet.isdigit() else sheet
    try:
        run = service.clean_file(
            str(file),
            out=str(out) if out else None,
            policy_path=str(policy) if policy else None,
            force=force,
            sheet=selector,
        )
    except DatasweepError as exc:
        fail(exc)
    if as_json:
        echo_json(_dump(run))
        return
    typer.echo(f"run {run.id}  {run.status.value}")
    if run.status is RunStatus.SKIPPED:
        typer.echo("already processed for this content + policy + engine version (use --force)")
        return
    typer.echo(f"rows={run.n_rows} cols={run.n_cols} encoding={run.encoding} format={run.format}")
    typer.echo(f"issues={run.issue_counts}")
    typer.echo(f"changes={run.change_counts}")
    typer.echo(f"artifacts: {run.artifact_dir}")


@app.command("profile")
def profile_command(
    file: Annotated[Path, typer.Argument(help="File to profile.")],
    policy: Annotated[Path | None, typer.Option("--policy", help="Policy TOML.")] = None,
    sheet: Annotated[str | None, typer.Option("--sheet")] = None,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Profile a file and list its issues.  Writes nothing (US-5)."""
    service = build_service(db)
    selector: int | str | None = None
    if sheet is not None:
        selector = int(sheet) if sheet.isdigit() else sheet
    try:
        result = service.profile_file(
            str(file), policy_path=str(policy) if policy else None, sheet=selector
        )
    except DatasweepError as exc:
        fail(exc)
    if as_json:
        echo_json(_dump(result))
        return
    typer.echo(f"{result.path}  {result.format.value}  encoding={result.encoding}")
    typer.echo(f"rows={result.n_rows} cols={result.n_cols}")
    typer.echo("")
    typer.echo(f"{'#':>3}  {'column':<24} {'type':<12} {'cover':>6} {'non-null':>8} {'nulls':>6}")
    for column in result.columns:
        typer.echo(
            f"{column.col_index:>3}  {column.name[:24]:<24} {column.inferred_type.value:<12} "
            f"{column.type_coverage:>6.3f} {column.non_null:>8} {column.null_count:>6}"
        )
    typer.echo("")
    typer.echo(f"issues={result.issue_counts}")
    typer.echo(f"changes={result.change_counts}")


# --------------------------------------------------------------------------
# history and inspection (FR-12, US-2)
# --------------------------------------------------------------------------


@app.command("runs")
def runs_command(
    path: Annotated[str | None, typer.Option("--path", help="Filter by source path.")] = None,
    status: Annotated[
        RunStatus | None, typer.Option("--status", help="Filter by run status.")
    ] = None,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """List runs, newest last (FR-12)."""
    service = build_service(db)
    runs = service.list_runs(path=path, status=status)
    if as_json:
        echo_json([_dump(run) for run in runs])
        return
    if not runs:
        typer.echo("no runs")
        return
    for run in runs:
        summary = service.run_summary(run)
        typer.echo(
            f"{run.id}  {run.status.value:<15} {summary.source_name:<28} "
            f"rows={run.n_rows} auto={run.change_counts.get('auto', 0)} "
            f"review={run.change_counts.get('review', 0)}"
        )


@app.command("show")
def show_command(
    run_ref: Annotated[str, typer.Argument(metavar="RUN", help="Run id or unique prefix.")],
    changes: Annotated[
        bool, typer.Option("--changes", help="List audited cell changes (US-2).")
    ] = False,
    issues: Annotated[
        bool, typer.Option("--issues", help="List detected issue instances.")
    ] = False,
    columns: Annotated[bool, typer.Option("--columns", help="List column profiles.")] = False,
    paths: Annotated[bool, typer.Option("--paths", help="Print artifact paths.")] = False,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Show a run: summary by default, or the requested detail sections."""
    service = build_service(db)
    try:
        run = service.resolve_run(run_ref)
        detail = service.run_detail(run.id)
        artifacts = service.artifact_paths(run)
        audit_lines: list[dict[str, Any]] = []
        finding_lines: list[dict[str, Any]] = []
        if changes and artifacts.get("audit"):
            audit_lines = [
                json.loads(line)
                for line in Path(artifacts["audit"]).read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("kind") != "header"
            ]
        if issues and artifacts.get("findings"):
            finding_lines = [
                json.loads(line)
                for line in Path(artifacts["findings"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    except DatasweepError as exc:
        fail(exc)

    if as_json:
        payload: dict[str, Any] = {"run": _dump(detail.run)}
        if columns:
            payload["columns"] = [_dump(column) for column in detail.columns]
        if changes:
            payload["changes"] = audit_lines
        if issues:
            payload["issues"] = finding_lines
        if paths:
            payload["paths"] = artifacts
        echo_json(payload)
        return

    typer.echo(f"run {detail.run.id}  {detail.run.status.value}")
    typer.echo(f"sha256={detail.run.content_sha256[:16]}…  policy={detail.run.policy_hash}")
    typer.echo(f"issues={detail.run.issue_counts}")
    typer.echo(f"changes={detail.run.change_counts}")
    if columns:
        typer.echo("")
        for column in detail.columns:
            typer.echo(
                f"  [{column.col_index}] {column.name}: {column.inferred_type.value} "
                f"coverage={column.type_coverage:.3f} nulls={column.null_count}"
            )
    if changes:
        typer.echo("")
        typer.echo(f"{len(audit_lines)} audited change(s):")
        for entry in audit_lines:
            typer.echo(
                f"  row={entry.get('row')} col={entry.get('col')} "
                f"{entry.get('col_name') or ''} {entry.get('rule')} "
                f"[{entry.get('tier')} {entry.get('confidence')}] "
                f"{entry.get('before')!r} -> {entry.get('after')!r}"
            )
    if issues:
        typer.echo("")
        typer.echo(f"{len(finding_lines)} detected issue instance(s):")
        for line in finding_lines:
            typer.echo(
                f"  {line['klass']:<5} row={line.get('row')} col={line.get('col')} "
                f"{line['rule']} [{line['tier']}] {line.get('value')!r}"
            )
    if paths:
        typer.echo("")
        for key, value in artifacts.items():
            typer.echo(f"  {key}: {value}")


# --------------------------------------------------------------------------
# review workflow (FR-11)
# --------------------------------------------------------------------------


@app.command("review")
def review_command(
    run_ref: Annotated[str, typer.Argument(metavar="RUN")],
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """List the run's review queue with proposals and confidence."""
    service = build_service(db)
    try:
        run = service.resolve_run(run_ref)
        items = service.review_items(run.id)
    except DatasweepError as exc:
        fail(exc)
    if as_json:
        echo_json([_dump(item) for item in items])
        return
    if not items:
        typer.echo("nothing awaiting review")
        return
    for item in items:
        typer.echo(
            f"{item.id}  [{item.status.value}] {item.rule}  conf={item.confidence:.2f}  "
            f"cells={item.affected_cells}"
        )
        typer.echo(f"    {item.description}")


def _decide(
    service: DatasweepService,
    run_ref: str,
    *,
    accept: list[str],
    reject: list[str],
    as_json: bool,
) -> None:
    try:
        run = service.resolve_run(run_ref)
        revision = service.decide(run.id, accept=accept, reject=reject)
    except DatasweepError as exc:
        fail(exc)
    if as_json:
        echo_json(_dump(revision))
        return
    typer.echo(f"revision {revision.revision_no}")
    typer.echo(f"  accepted: {', '.join(revision.accepted_item_ids) or '(none)'}")
    typer.echo(f"  cleaned:  {revision.cleaned_path}")
    typer.echo(f"  audit:    {revision.audit_path}")


@app.command("accept")
def accept_command(
    run_ref: Annotated[str, typer.Argument(metavar="RUN")],
    item: Annotated[
        list[str] | None, typer.Option("--item", help="Review item id; repeatable.")
    ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="Accept every pending item.")] = False,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Accept review items and write the next complete revision (FR-11)."""
    service = build_service(db)
    ids = list(item or [])
    if all_items:
        try:
            run = service.resolve_run(run_ref)
        except DatasweepError as exc:
            fail(exc)
        ids = [entry.id for entry in service.review_items(run.id) if entry.status == "pending"]
    if not ids:
        typer.echo("error [no_items]: pass --item ID (repeatable) or --all", err=True)
        raise typer.Exit(code=1)
    _decide(service, run_ref, accept=ids, reject=[], as_json=as_json)


@app.command("reject")
def reject_command(
    run_ref: Annotated[str, typer.Argument(metavar="RUN")],
    item: Annotated[
        list[str] | None, typer.Option("--item", help="Review item id; repeatable.")
    ] = None,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Reject review items; they are never re-proposed for the same content."""
    ids = list(item or [])
    if not ids:
        typer.echo("error [no_items]: pass --item ID (repeatable)", err=True)
        raise typer.Exit(code=1)
    _decide(build_service(db), run_ref, accept=[], reject=ids, as_json=as_json)


@app.command("revert")
def revert_command(
    run_ref: Annotated[str, typer.Argument(metavar="RUN")],
    revision: Annotated[int | None, typer.Option("--revision", help="Revision number.")] = None,
    db: DbOption = DEFAULT_DB,
    as_json: JsonOption = False,
) -> None:
    """Verify FR-9: revert(cleaned, audit) reproduces the parsed original."""
    service = build_service(db)
    try:
        run = service.resolve_run(run_ref)
        check = service.revert_check(run.id, revision)
    except DatasweepError as exc:
        fail(exc)
    if as_json:
        echo_json(_dump(check))
    else:
        typer.echo(
            f"revision {check.revision}: "
            + ("match — the audit reconstructs the parsed original" if check.match else "MISMATCH")
        )
        for problem in check.mismatches:
            typer.echo(f"  {problem}")
    if not check.match:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8787,
    db: DbOption = DEFAULT_DB,
) -> None:
    """Run the FastAPI app with uvicorn."""
    import os

    import uvicorn

    from ..api.app import DB_ENV, create_app

    os.environ.setdefault(DB_ENV, str(Path(db).expanduser()))
    uvicorn.run(create_app(), host=host, port=port)


def main() -> None:
    """Console-script entry point (``datasweep``)."""
    try:
        app()
    except DatasweepError as exc:  # pragma: no cover - handlers catch these first
        typer.echo(f"error [{exc.code}]: {exc.message}", err=True)
        sys.exit(1)


__all__ = ["app", "build_service", "main"]
