"""FR-15 — the Typer CLI.

Thin: every command parses its options, calls one
:class:`~chessmentor.services.ChessMentorService` method and renders the result.
Errors from the service catalog are printed to stderr and exit non-zero.

``play`` is the one interactive command: it renders the board with
``chess.Board.unicode()``, accepts SAN or UCI plus the meta-commands ``board``,
``moves``, ``legal``, ``resign`` and ``quit`` (quitting leaves the game
resumable — FR-6 persists after every ply), and resumes an in-progress game
rather than erroring.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import chess
import typer

from .. import __version__
from ..adapters.analyst import AnalystUnavailableError
from ..constants import DEEP_BUDGET, JUDGE_BUDGET
from ..datasets import DatasetError
from ..engine.adapt import expected_score, target_score
from ..engine.pgn import AmbiguousSideError, PgnParseError
from ..engine.rating import blended_rating
from ..engine.session import IllegalMoveError
from ..models import (
    AnalystKind,
    ChallengeMode,
    Color,
    GameSource,
    GameStatus,
    PreferredColor,
)
from ..services import (
    ChessMentorService,
    GameInProgressError,
    MoveResult,
    ServiceError,
    new_seed,
)
from ..store.repository import NotFoundError
from ..store.sqlite_repo import SQLiteRepository

__all__ = ["app", "main"]

app = typer.Typer(
    name="chessmentor",
    help=(
        "A chess trainer that plays at your level and coaches your mistakes. "
        "Ratings are an internal scale and are never comparable to FIDE or Lichess."
    ),
    no_args_is_help=True,
    add_completion=False,
)
profile_app = typer.Typer(help="Show or change the local profile.", no_args_is_help=True)
games_app = typer.Typer(help="List and inspect games.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")
app.add_typer(games_app, name="games")

DB_OPTION = typer.Option(
    "--db",
    help="SQLite database path (default: $CHESSMENTOR_DB or ~/.chessmentor/chessmentor.db).",
)

#: FR-7d's user-facing text.
DIVERGENCE_WARNING_TEXT = (
    "move-quality and result estimates disagree — the ACPL anchors may not describe your play"
)


def now_iso() -> str:
    """Edges may read the clock; the engine may not (CONVENTIONS.md)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _echo_err(message: str) -> None:
    typer.echo(message, err=True)


def build_service(db: Path | None) -> ChessMentorService:
    """Open the store and compose the service (schema creation is idempotent)."""
    from ..api.app import default_db_path

    path = db or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    repo = SQLiteRepository(path)
    service = ChessMentorService(repo)
    repo.initialize(service.datasets.levels)
    return service


def _fail(message: str, code: int = 1) -> None:
    _echo_err(f"error: {message}")
    raise typer.Exit(code=code)


def _render_board(board: chess.Board, player_color: Color) -> str:
    orientation = chess.WHITE if player_color is Color.WHITE else chess.BLACK
    return board.unicode(borders=True, empty_square=".", orientation=orientation)


def _result_line(status: GameStatus, termination: str | None) -> str:
    label = {
        GameStatus.PLAYER_WIN: "You win",
        GameStatus.OPPONENT_WIN: "You lose",
        GameStatus.DRAW: "Draw",
        GameStatus.ABORTED: "Aborted",
        GameStatus.UNFINISHED: "Unfinished",
        GameStatus.IN_PROGRESS: "In progress",
    }[status]
    return f"{label} ({termination})" if termination else label


# --------------------------------------------------------------------------- #
# init / levels / profile
# --------------------------------------------------------------------------- #


@app.command()
def init(
    db: Annotated[Path | None, DB_OPTION] = None,
    name: str = typer.Option("Player", "--name", help="Display name (used by PGN import)."),
    challenge: ChallengeMode = typer.Option(
        ChallengeMode.BALANCED, "--challenge", help="Target expected score band."
    ),
    color: PreferredColor = typer.Option(
        PreferredColor.RANDOM, "--color", help="Default colour for new games."
    ),
) -> None:
    """Create the database, validate the committed datasets, set the cold-start level."""
    try:
        service = build_service(db)
        profile, state = service.initialize(
            now=now_iso(), display_name=name, challenge_mode=challenge, preferred_color=color
        )
    except DatasetError as exc:
        _fail(f"dataset validation failed: {exc}")
        return
    data = service.datasets
    level = data.level_by_id(state.current_level_id)
    typer.echo(f"ChessMentor {__version__} initialised at {db or 'the default database'}")
    typer.echo(
        f"  datasets: {len(data.levels)} levels, {len(data.book.lines)} opening lines, "
        f"{len(data.advice)} advice entries — all validated"
    )
    typer.echo(f"  profile:  {profile.display_name} ({profile.challenge_mode.value})")
    typer.echo(
        f"  starting level: {level.name} (internal {level.elo_internal:.0f}) — "
        f"cold start for R_hat = {blended_rating(state):.0f}"
    )


@app.command()
def levels(db: Annotated[Path | None, DB_OPTION] = None) -> None:
    """Show the calibrated ladder and the controller's current recommendation."""
    service = build_service(db)
    try:
        profile = service.require_profile()
        state = service.require_rating_state()
    except ServiceError as exc:
        _fail(str(exc))
        return
    r_hat = blended_rating(state)
    typer.echo(
        f"internal rating R_hat = {r_hat:.0f} | mode {profile.challenge_mode.value} "
        f"(target {target_score(profile.challenge_mode):.2f})"
    )
    typer.echo(
        f"{'':2} {'lvl':<4} {'elo':>6} {'E':>6} {'depth':>5} {'nodes':>6} {'sigma':>6} {'p':>5} {'acpl':>6}"
    )
    for level in service.datasets.levels:
        mark = "->" if level.id == state.current_level_id else "  "
        typer.echo(
            f"{mark} {level.name:<4} {level.elo_internal:>6.0f} "
            f"{expected_score(r_hat, level.elo_internal):>6.2f} {level.max_depth:>5} "
            f"{level.node_budget:>6} {level.noise_sigma_cp:>6.0f} {level.blunder_prob:>5.2f} "
            f"{level.acpl_mean:>6.0f}"
        )


@profile_app.command("show")
def profile_show(db: Annotated[Path | None, DB_OPTION] = None) -> None:
    """Print the local profile."""
    service = build_service(db)
    try:
        profile = service.require_profile()
    except ServiceError as exc:
        _fail(str(exc))
        return
    typer.echo(f"name:      {profile.display_name}")
    typer.echo(f"challenge: {profile.challenge_mode.value}")
    typer.echo(f"colour:    {profile.preferred_color.value}")
    typer.echo(f"updated:   {profile.updated_at}")


@profile_app.command("set")
def profile_set(
    db: Annotated[Path | None, DB_OPTION] = None,
    name: str | None = typer.Option(None, "--name"),
    challenge: ChallengeMode | None = typer.Option(None, "--challenge"),
    color: PreferredColor | None = typer.Option(None, "--color"),
) -> None:
    """Change the profile; a new challenge mode recomputes the level recommendation."""
    service = build_service(db)
    try:
        profile, state = service.set_profile(
            now=now_iso(), display_name=name, challenge_mode=challenge, preferred_color=color
        )
    except ServiceError as exc:
        _fail(str(exc))
        return
    level = service.datasets.level_by_id(state.current_level_id)
    typer.echo(
        f"profile updated: {profile.display_name} / {profile.challenge_mode.value} / "
        f"{profile.preferred_color.value}; next opponent {level.name}"
    )


# --------------------------------------------------------------------------- #
# play
# --------------------------------------------------------------------------- #

PLAY_HELP = (
    "Enter a move in SAN (Nf3) or UCI (g1f3). Other commands: board, moves, legal, resign, quit."
)


@app.command()
def play(
    db: Annotated[Path | None, DB_OPTION] = None,
    level: int | None = typer.Option(None, "--level", help="Force a level for this game (US-8)."),
    color: PreferredColor | None = typer.Option(None, "--color"),
    seed: int | None = typer.Option(None, "--seed", help="Game seed; omitted means random."),
) -> None:
    """Play a game interactively; resumes an in-progress game instead of erroring."""
    service = build_service(db)
    try:
        view = service.in_progress_game()
        if view is None:
            view = service.create_game(
                started_at=now_iso(),
                seed=seed if seed is not None else new_seed(),
                color=color,
                level_id=level,
            )
            opponent = service.datasets.level_by_id(view.game.level_id or 1)
            typer.echo(
                f"new game #{view.game.id}: you are {view.game.player_color.value} against "
                f"{opponent.name} (internal {opponent.elo_internal:.0f})"
                + (" [level overridden]" if view.game.level_overridden else "")
            )
        else:
            typer.echo(f"resuming game #{view.game.id}")
    except GameInProgressError as exc:  # pragma: no cover - resumed above
        _fail(str(exc))
        return
    except ServiceError as exc:
        _fail(str(exc))
        return

    assert view.game.id is not None
    game_id = view.game.id
    typer.echo(PLAY_HELP)
    board = chess.Board(view.fen)
    typer.echo(_render_board(board, view.game.player_color))

    while True:
        view = service.get_game(game_id)
        if view.game.status is not GameStatus.IN_PROGRESS:
            typer.echo(_result_line(view.game.status, view.game.termination))
            return
        try:
            raw = typer.prompt("move", prompt_suffix="> ")
        except (EOFError, typer.Abort):
            typer.echo("\nleaving; the game stays resumable with `chessmentor play`")
            return
        text = raw.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"quit", "exit"}:
            typer.echo("game saved; resume it with `chessmentor play`")
            return
        if lowered == "board":
            typer.echo(_render_board(chess.Board(view.fen), view.game.player_color))
            continue
        if lowered == "moves":
            typer.echo(" ".join(f"{m.ply}.{m.san}" for m in view.moves) or "(no moves yet)")
            continue
        if lowered == "legal":
            typer.echo(" ".join(view.legal_moves_san))
            continue
        if lowered == "resign":
            result = service.resign(game_id, at=now_iso())
            _report_finish(service, result)
            return
        try:
            result = service.submit_move(game_id, text, at=now_iso())
        except IllegalMoveError as exc:
            _echo_err(f"illegal move {exc.text!r}; try `legal` for the list")
            continue
        except ServiceError as exc:
            _fail(str(exc))
            return
        board = chess.Board(result.fen)
        if result.cpu_move is not None:
            meta = result.cpu_move.cpu_meta
            tag = " (book)" if result.cpu_move.is_book else ""
            if meta is not None and not result.cpu_move.is_book:
                tag = f" (depth {meta.depth}, {meta.nodes} nodes)"
            typer.echo(f"CPU plays {result.cpu_move.san}{tag}")
        typer.echo(_render_board(board, result.game.player_color))
        if result.outcome is not None:
            _report_finish(service, result)
            return


def _report_finish(service: ChessMentorService, result: MoveResult) -> None:
    typer.echo(_result_line(result.game.status, result.game.termination))
    if result.analysis is not None:
        analysis = result.analysis
        typer.echo(
            f"judge pass: ACPL {analysis.acpl:.0f}, accuracy {analysis.accuracy:.1f}%, "
            f"{analysis.n_blunders} blunders / {analysis.n_mistakes} mistakes / "
            f"{analysis.n_inaccuracies} inaccuracies"
        )
    if result.rating_event is not None:
        event = result.rating_event
        next_level = service.datasets.level_by_id(event.level_next)
        typer.echo(
            f"internal rating {event.r_hat_after:.0f} "
            f"(glicko {event.glicko_r_after:.0f} +/- {event.glicko_rd_after:.0f}, "
            f"move-quality {event.perf_ewma_after:.0f}, lambda {event.lambda_used:.2f}) "
            f"-> next opponent {next_level.name}"
        )
    elif result.game.source is GameSource.PLAYED and not result.game.rated:
        typer.echo("not rated (aborted or under 8 plies)")


# --------------------------------------------------------------------------- #
# games / analyze / import
# --------------------------------------------------------------------------- #


@games_app.command("list")
def games_list(
    db: Annotated[Path | None, DB_OPTION] = None,
    status: GameStatus | None = typer.Option(None, "--status"),
    source: GameSource | None = typer.Option(None, "--source"),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
) -> None:
    """List games, most recent first."""
    service = build_service(db)
    rows = service.list_games(status=status, source=source, limit=limit)
    if not rows:
        typer.echo("(no games)")
        return
    typer.echo(
        f"{'id':>4} {'source':<8} {'lvl':<4} {'status':<13} {'plies':>5} {'opening':<28} rated"
    )
    for game in sorted(rows, key=lambda g: g.id or 0, reverse=True):
        level_name = f"L{game.level_id}" if game.level_id else "-"
        opening = game.opening_name or "-"
        typer.echo(
            f"{game.id:>4} {game.source.value:<8} {level_name:<4} {game.status.value:<13} "
            f"{game.ply_count:>5} {opening[:28]:<28} {'yes' if game.rated else 'no'}"
        )


@games_app.command("show")
def games_show(
    game_id: int,
    db: Annotated[Path | None, DB_OPTION] = None,
    pgn: bool = typer.Option(False, "--pgn", help="Print the PGN instead of a summary."),
) -> None:
    """Show one game: status, board, moves — or its PGN."""
    service = build_service(db)
    try:
        view = service.get_game(game_id)
    except NotFoundError as exc:
        _fail(str(exc).strip("'"))
        return
    if pgn:
        typer.echo(service.game_pgn(game_id))
        return
    game = view.game
    typer.echo(
        f"game #{game.id} ({game.source.value}) — {_result_line(game.status, game.termination)}"
    )
    typer.echo(
        f"  you played {game.player_color.value}; opening {game.opening_name or '-'} ({game.eco or '-'}), book depth {game.book_depth}"
    )
    if game.level_id is not None:
        typer.echo(
            f"  opponent L{game.level_id} at internal {game.level_elo:.0f}"
            + (" [overridden]" if game.level_overridden else "")
        )
    typer.echo(f"  {game.ply_count} plies; FEN {view.fen}")
    typer.echo(_render_board(chess.Board(view.fen), game.player_color))


@app.command()
def analyze(
    game_id: int,
    db: Annotated[Path | None, DB_OPTION] = None,
    analyst: AnalystKind = typer.Option(AnalystKind.INTERNAL, "--analyst"),
    nodes: int | None = typer.Option(
        None, "--nodes", help=f"Analyst node budget (default {DEEP_BUDGET} on demand)."
    ),
) -> None:
    """Re-analyse a game; a deeper pass never becomes the rating or report basis."""
    service = build_service(db)
    budget = nodes if nodes is not None else DEEP_BUDGET
    try:
        result = service.analyze(
            game_id, created_at=now_iso(), analyst_kind=analyst, node_budget=budget
        )
    except AnalystUnavailableError as exc:
        _fail(str(exc), code=3)
        return
    except NotFoundError as exc:
        _fail(str(exc).strip("'"))
        return
    except ServiceError as exc:
        _fail(str(exc))
        return
    analysis = result.analysis
    kind = "new" if result.created else "cached"
    typer.echo(
        f"analysis #{analysis.id} ({kind}) — {analysis.analyst.value} "
        f"@{analysis.node_budget} nodes, engine {analysis.analyst_version}"
    )
    typer.echo(
        f"  ACPL {analysis.acpl:.1f} | accuracy {analysis.accuracy:.1f}% | "
        f"performance {analysis.perf_rating:.0f} internal"
    )
    typer.echo(
        f"  {analysis.n_blunders} blunders, {analysis.n_mistakes} mistakes, "
        f"{analysis.n_inaccuracies} inaccuracies"
        + (" | rating basis" if analysis.is_rating_basis else "")
    )
    for moment in analysis.key_moments:
        move = next((m for m in analysis.moves if m.ply == moment.ply), None)
        line = " ".join(move.best_line_san) if move else ""
        typer.echo(
            f"  key moment ply {moment.ply}: dW {moment.dw:.3f} ({moment.severity.value})"
            + (f" — best was {line}" if line else "")
        )
    flagged = [m for m in analysis.moves if m.category is not None]
    for move in flagged[:10]:
        typer.echo(
            f"  ply {move.ply:>3} {move.severity.value:<11} {move.category.value:<18} "
            f"dW {move.delta_w:.3f} cp_loss {move.cp_loss}"
        )


@app.command("import")
def import_pgn(
    path: Path,
    db: Annotated[Path | None, DB_OPTION] = None,
    as_side: str = typer.Option(
        "auto", "--as", help="Which side you were: white | black | auto (match display name)."
    ),
    analyze_flag: bool = typer.Option(
        False, "--analyze", help=f"Also run the internal analyst at {JUDGE_BUDGET} nodes."
    ),
) -> None:
    """Import one or many games from a PGN file; imported games are never rated."""
    service = build_service(db)
    if not path.exists():
        _fail(f"no such file: {path}")
        return
    side_value = as_side.strip().lower()
    if side_value not in {"white", "black", "auto"}:
        _fail("--as must be white, black or auto")
        return
    side = None if side_value == "auto" else Color(side_value)
    try:
        imported = service.import_pgn(
            path.read_text(encoding="utf-8"),
            created_at=now_iso(),
            side=side,
            analyze=analyze_flag,
        )
    except AmbiguousSideError as exc:
        _fail(f"{exc}; pass --as white|black")
        return
    except PgnParseError as exc:
        _fail(str(exc))
        return
    except ServiceError as exc:
        _fail(str(exc))
        return
    for item in imported:
        line = (
            f"imported game #{item.game.id}: you were {item.game.player_color.value}, "
            f"{item.game.ply_count} plies, {item.game.status.value}"
        )
        if item.analysis is not None:
            line += f" — ACPL {item.analysis.acpl:.1f}, accuracy {item.analysis.accuracy:.1f}%"
        typer.echo(line)
    typer.echo(f"{len(imported)} game(s) imported; imported games never affect your rating")


# --------------------------------------------------------------------------- #
# rating / report
# --------------------------------------------------------------------------- #


@app.command()
def rating(
    db: Annotated[Path | None, DB_OPTION] = None,
    history: bool = typer.Option(False, "--history", help="Print the rating event log."),
) -> None:
    """Show the internal rating estimate (never a FIDE/Lichess-comparable number)."""
    service = build_service(db)
    try:
        view = service.rating()
    except ServiceError as exc:
        _fail(str(exc))
        return
    state = view.state
    level = service.datasets.level_by_id(view.recommended_level_id)
    typer.echo(f"internal rating (R_hat): {view.r_hat:.0f}")
    typer.echo(
        f"  results channel (Glicko): {state.glicko_rating:.0f} +/- {state.glicko_rd:.0f} RD "
        f"over {state.rated_games} rated game(s)"
    )
    ewma = f"{state.perf_ewma:.0f}" if state.perf_ewma is not None else "-"
    typer.echo(f"  move-quality channel:     {ewma} over {state.judged_games} judged game(s)")
    typer.echo(f"  blend lambda:             {view.lambda_used:.2f} (results weight)")
    typer.echo(
        f"  next opponent: {level.name} at internal {level.elo_internal:.0f} "
        f"(expected score {view.expected_score_at_current_level:.2f})"
    )
    if view.calibration_warning:
        _echo_err(f"warning: {DIVERGENCE_WARNING_TEXT}")
    if history:
        events = service.rating_history()
        if not events:
            typer.echo("(no rated games yet)")
            return
        typer.echo(
            f"{'game':>5} {'res':>4} {'opp':>6} {'E':>5} {'glicko':>7} {'RD':>5} {'perf':>6} {'lam':>5} {'R_hat':>7} lvl"
        )
        for event in events:
            typer.echo(
                f"{event.game_id:>5} {event.result_score:>4.1f} {event.opponent_elo:>6.0f} "
                f"{event.expected_score:>5.2f} {event.glicko_r_after:>7.0f} "
                f"{event.glicko_rd_after:>5.0f} {event.perf_game:>6.0f} "
                f"{event.lambda_used:>5.2f} {event.r_hat_after:>7.0f} "
                f"{event.level_played}->{event.level_next}"
                + (" [RD inflated]" if event.rd_inflated else "")
            )


@app.command()
def report(
    db: Annotated[Path | None, DB_OPTION] = None,
    last_games: int = typer.Option(10, "--last-games", min=1, max=100),
    include_imported: bool = typer.Option(False, "--include-imported"),
) -> None:
    """Build a coaching report over the recent games that carry a report-basis analysis."""
    service = build_service(db)
    try:
        view = service.create_report(
            created_at=now_iso(), last_games=last_games, include_imported=include_imported
        )
    except ServiceError as exc:
        _fail(str(exc))
        return
    rep = view.report
    typer.echo(f"coaching report #{rep.id} over {len(rep.window)} game(s)")
    if rep.skipped_game_ids:
        typer.echo(
            "  skipped (no internal @ "
            f"{JUDGE_BUDGET} analysis at this engine version): "
            + ", ".join(str(gid) for gid in rep.skipped_game_ids)
        )
    if not rep.suggestions:
        typer.echo("  no mistakes flagged in this window — nothing to work on yet")
        return
    for suggestion in rep.suggestions:
        delta = view.deltas.get(suggestion.category, 0.0)
        arrow = f" ({delta:+.2f} vs last report)" if delta else ""
        typer.echo("")
        typer.echo(
            f"{suggestion.rank}. {suggestion.category.value} "
            f"[lost win-probability {suggestion.priority_score:.2f}{arrow}]"
        )
        typer.echo(f"   {suggestion.advice_title}")
        typer.echo(f"   {suggestion.advice_body}")
        typer.echo(f"   drill: {suggestion.advice_drill}")
        for item in suggestion.evidence:
            typer.echo(f"   - game {item.game_id} ply {item.ply}: {item.san} (dW {item.dw:.3f})")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run the REST API (FR-14) with uvicorn."""
    import uvicorn

    uvicorn.run("chessmentor.api.app:app", host=host, port=port, log_level="info")


def main() -> None:
    """Console-script entry point (``chessmentor``)."""
    try:
        app()
    except DatasetError as exc:  # pragma: no cover - guarded per command
        _echo_err(f"error: {exc}")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
