"""Application services — the one place the API and the CLI both delegate to.

This layer composes the pure engine (``engine/``), the adapters and the
repository.  It owns *orchestration* only: sequencing, persistence and the
cross-entity invariants DATA_MODEL.md states (one in-progress game, one rating
event per rated game, the judge pass always runs).  Every chess/rating/coaching
rule lives in ``engine/`` and is merely called from here, so the API and CLI can
stay the thin parse-delegate-serialize shells CONVENTIONS.md requires.

Time and randomness stay explicit inputs: every method that needs a timestamp
takes one, and game seeds are supplied by the caller (the API and CLI mint one
from ``os.urandom`` at the edge when the user does not).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import chess

from . import __version__
from .adapters import CommittedBook, InternalAnalyst
from .constants import DEFAULT_REPORT_WINDOW, JUDGE_BUDGET, MIN_RATED_PLIES
from .datasets import Datasets, default_datasets
from .engine.adapt import cold_start_level_id, expected_score
from .engine.coach import build_report, is_report_basis, report_deltas, select_window
from .engine.judge import included_move_count, judge_game
from .engine.pgn import (
    AmbiguousSideError,
    PgnParseError,
    import_outcome,
    parse_pgn,
    resolve_import_side,
)
from .engine.rating import apply_rated_game, blend_lambda, blended_rating, initial_rating_state
from .engine.session import (
    IllegalMoveError,
    TerminalOutcome,
    abort_outcome,
    apply_cpu_move,
    apply_player_move,
    board_from_moves,
    derive_rated,
    make_move_record,
    resignation_outcome,
    resolve_color,
    to_pgn,
)
from .models import (
    AnalystKind,
    ChallengeMode,
    CoachingReport,
    Color,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    Level,
    MistakeCategory,
    MoveRecord,
    PlayerProfile,
    PreferredColor,
    RatingEvent,
    RatingState,
)
from .store.repository import NotFoundError, Repository

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .adapters.analyst import Analyst

__all__ = [
    "AbortTooLateError",
    "AnalyzeResult",
    "ChessMentorService",
    "GameFinishedError",
    "GameInProgressError",
    "GameView",
    "ImportedGame",
    "MoveResult",
    "NotInitializedError",
    "NotPlayersTurnError",
    "RatingView",
    "ReportView",
    "ServiceError",
    "new_seed",
]


# --------------------------------------------------------------------------- #
# Error catalog (the API maps each of these to one status code)
# --------------------------------------------------------------------------- #


class ServiceError(RuntimeError):
    """Base class for every expected, user-facing service failure."""

    code = "service_error"


class NotInitializedError(ServiceError):
    """No profile exists yet — ``chessmentor init`` / ``PUT /profile`` first."""

    code = "not_initialized"


class GameInProgressError(ServiceError):
    """FR-6: at most one game may be in progress."""

    code = "game_in_progress"

    def __init__(self, game_id: int) -> None:
        super().__init__(f"a game is already in progress (id {game_id})")
        self.in_progress_game_id = game_id


class GameFinishedError(ServiceError):
    """The game is no longer accepting moves."""

    code = "game_finished"


class NotPlayersTurnError(ServiceError):
    """The side to move is not the player's."""

    code = "not_players_turn"


class AbortTooLateError(ServiceError):
    """FR-6: a game may only be aborted before ply 8."""

    code = "abort_too_late"


def new_seed() -> int:
    """Mint a 64-bit game seed at the edge (never inside the engine)."""
    return int.from_bytes(os.urandom(8), "big")


# --------------------------------------------------------------------------- #
# Read models returned to the surfaces
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GameView:
    """A game plus everything a client needs to render or resume it."""

    game: Game
    moves: tuple[MoveRecord, ...]
    fen: str
    turn: Color
    legal_moves_san: tuple[str, ...]
    player_to_move: bool


@dataclass(frozen=True)
class MoveResult:
    """One player ply, the CPU's reply (if any) and the resulting game state."""

    game: Game
    player_move: MoveRecord | None
    cpu_move: MoveRecord | None
    outcome: TerminalOutcome | None
    analysis: GameAnalysis | None
    rating_event: RatingEvent | None
    fen: str
    legal_moves_san: tuple[str, ...]


@dataclass(frozen=True)
class AnalyzeResult:
    analysis: GameAnalysis
    created: bool


@dataclass(frozen=True)
class ImportedGame:
    game: Game
    analysis: GameAnalysis | None


@dataclass(frozen=True)
class RatingView:
    state: RatingState
    r_hat: float
    lambda_used: float
    expected_score_at_current_level: float
    recommended_level_id: int
    calibration_warning: bool


@dataclass(frozen=True)
class ReportView:
    report: CoachingReport
    deltas: dict[MistakeCategory, float]


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


class ChessMentorService:
    """Orchestrates the engine, the adapters and the store."""

    def __init__(
        self,
        repo: Repository,
        *,
        datasets: Datasets | None = None,
        analyst: Analyst | None = None,
        engine_version: str = __version__,
    ) -> None:
        self.repo = repo
        self.datasets = datasets or default_datasets()
        self.analyst: Analyst = analyst or InternalAnalyst()
        self.engine_version = engine_version

    # -- levels / profile --------------------------------------------------- #

    @property
    def levels(self) -> list[Level]:
        return list(self.datasets.levels)

    @property
    def book(self) -> CommittedBook:
        return self.datasets.book

    def initialize(
        self,
        *,
        now: str,
        display_name: str = "Player",
        challenge_mode: ChallengeMode = ChallengeMode.BALANCED,
        preferred_color: PreferredColor = PreferredColor.RANDOM,
    ) -> tuple[PlayerProfile, RatingState]:
        """Create the schema, validate the datasets and set the cold-start level.

        Idempotent: re-running ``init`` keeps an existing profile and rating
        state (so a user never loses their history by typing ``init`` twice).
        """
        self.repo.initialize(self.datasets.levels)
        profile = self.repo.get_profile()
        if profile is None:
            profile = self.repo.save_profile(
                PlayerProfile(
                    display_name=display_name,
                    challenge_mode=challenge_mode,
                    preferred_color=preferred_color,
                    created_at=now,
                    updated_at=now,
                )
            )
        state = self.repo.get_rating_state()
        if state is None:
            state = self.repo.save_rating_state(
                initial_rating_state(
                    current_level_id=cold_start_level_id(
                        self.datasets.levels, profile.challenge_mode
                    ),
                    updated_at=now,
                )
            )
        return profile, state

    def require_profile(self) -> PlayerProfile:
        profile = self.repo.get_profile()
        if profile is None:
            raise NotInitializedError("no profile yet — run `chessmentor init`")
        return profile

    def require_rating_state(self) -> RatingState:
        state = self.repo.get_rating_state()
        if state is None:
            raise NotInitializedError("no rating state yet — run `chessmentor init`")
        return state

    def set_profile(
        self,
        *,
        now: str,
        display_name: str | None = None,
        challenge_mode: ChallengeMode | None = None,
        preferred_color: PreferredColor | None = None,
    ) -> tuple[PlayerProfile, RatingState]:
        """Update the profile; a mode change recomputes the recommendation (FR-8)."""
        profile = self.require_profile()
        state = self.require_rating_state()
        updates: dict[str, object] = {"updated_at": now}
        if display_name is not None:
            updates["display_name"] = display_name
        if preferred_color is not None:
            updates["preferred_color"] = preferred_color
        mode_changed = challenge_mode is not None and challenge_mode != profile.challenge_mode
        if challenge_mode is not None:
            updates["challenge_mode"] = challenge_mode
        profile = self.repo.save_profile(profile.model_copy(update=updates))

        if mode_changed:
            from .engine.adapt import recommend_level_id

            state = self.repo.save_rating_state(
                state.model_copy(
                    update={
                        "current_level_id": recommend_level_id(
                            self.datasets.levels,
                            current_level_id=state.current_level_id,
                            r_hat=blended_rating(state),
                            mode=profile.challenge_mode,
                            rated_games=state.rated_games,
                        ),
                        "updated_at": now,
                    }
                )
            )
        return profile, state

    # -- games -------------------------------------------------------------- #

    def _view(self, game: Game) -> GameView:
        moves = self.repo.list_moves(game.id) if game.id is not None else []
        board = board_from_moves([m.uci for m in moves])
        turn = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        legal = tuple(sorted(board.san(m) for m in board.legal_moves))
        return GameView(
            game=game,
            moves=tuple(moves),
            fen=board.fen(),
            turn=turn,
            legal_moves_san=legal if game.status is GameStatus.IN_PROGRESS else (),
            player_to_move=turn is game.player_color,
        )

    def get_game(self, game_id: int) -> GameView:
        return self._view(self.repo.get_game(game_id))

    def list_games(
        self,
        *,
        status: GameStatus | None = None,
        source: GameSource | None = None,
        limit: int | None = None,
    ) -> list[Game]:
        return self.repo.list_games(status=status, source=source, limit=limit)

    def in_progress_game(self) -> GameView | None:
        game = self.repo.get_in_progress_game()
        return self._view(game) if game is not None else None

    def create_game(
        self,
        *,
        started_at: str,
        seed: int | None = None,
        color: PreferredColor | None = None,
        level_id: int | None = None,
    ) -> GameView:
        """FR-6: start a game.  Rejects a second in-progress game with 409."""
        profile = self.require_profile()
        state = self.require_rating_state()
        open_game = self.repo.get_in_progress_game()
        if open_game is not None and open_game.id is not None:
            raise GameInProgressError(open_game.id)

        game_seed = new_seed() if seed is None else seed
        preferred = color if color is not None else profile.preferred_color
        player_color = resolve_color(preferred, game_seed)

        recommended = state.current_level_id
        chosen_id = recommended if level_id is None else level_id
        level = self.datasets.level_by_id(chosen_id)

        game = self.repo.create_game(
            Game(
                source=GameSource.PLAYED,
                created_at=started_at,
                seed=game_seed,
                player_color=player_color,
                level_id=level.id,
                level_elo=level.elo_internal,
                recommended_level_id=recommended,
                level_overridden=level.id != recommended,
                status=GameStatus.IN_PROGRESS,
                ply_count=0,
            )
        )
        assert game.id is not None

        if player_color is Color.BLACK:
            # The CPU has White: play its opening move so the board is the
            # player's to move as soon as the game exists.
            board = chess.Board()
            reply = apply_cpu_move(
                board, level, game_seed=game_seed, player_color=player_color, game_id=game.id
            )
            self.repo.append_move(game.id, reply.record)
            game = self._persist_progress(game, [reply.record.uci], board)
        return self._view(game)

    def _opening_facts(self, uci_moves: Sequence[str]) -> tuple[str | None, str | None, int]:
        opening = self.book.identify(uci_moves)
        if opening is None:
            return None, None, 0
        return opening.eco, opening.name, opening.depth

    def _persist_progress(self, game: Game, uci_moves: Sequence[str], board: chess.Board) -> Game:
        eco, name, book_depth = self._opening_facts(uci_moves)
        return self.repo.update_game(
            game.model_copy(
                update={
                    "ply_count": len(uci_moves),
                    "final_fen": board.fen(),
                    "eco": eco,
                    "opening_name": name,
                    "book_depth": book_depth,
                }
            )
        )

    def submit_move(self, game_id: int, text: str, *, at: str) -> MoveResult:
        """FR-6: player move → validation → persistence → CPU reply → persistence."""
        game = self.repo.get_game(game_id)
        if game.status is not GameStatus.IN_PROGRESS:
            raise GameFinishedError(f"game {game_id} is {game.status.value}")
        assert game.id is not None and game.level_id is not None and game.seed is not None

        moves = self.repo.list_moves(game.id)
        board = board_from_moves([m.uci for m in moves])
        turn = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        if turn is not game.player_color:
            raise NotPlayersTurnError(f"it is {turn.value}'s move")

        level = self.datasets.level_by_id(game.level_id)
        played = apply_player_move(board, text, player_color=game.player_color, game_id=game.id)
        self.repo.append_move(game.id, played.record)
        uci_moves = [m.uci for m in moves] + [played.record.uci]

        cpu_record: MoveRecord | None = None
        outcome = played.outcome
        if outcome is None:
            reply = apply_cpu_move(
                board,
                level,
                game_seed=game.seed,
                player_color=game.player_color,
                book=self.book,
                game_id=game.id,
            )
            self.repo.append_move(game.id, reply.record)
            cpu_record = reply.record
            uci_moves.append(reply.record.uci)
            outcome = reply.outcome

        game = self._persist_progress(game, uci_moves, board)
        analysis: GameAnalysis | None = None
        event: RatingEvent | None = None
        if outcome is not None:
            game, analysis, event = self.finish_game(game, outcome, uci_moves, board, at=at)

        return MoveResult(
            game=game,
            player_move=played.record,
            cpu_move=cpu_record,
            outcome=outcome,
            analysis=analysis,
            rating_event=event,
            fen=board.fen(),
            legal_moves_san=tuple(sorted(board.san(m) for m in board.legal_moves))
            if outcome is None
            else (),
        )

    def resign(self, game_id: int, *, at: str) -> MoveResult:
        game = self.repo.get_game(game_id)
        if game.status is not GameStatus.IN_PROGRESS:
            raise GameFinishedError(f"game {game_id} is {game.status.value}")
        assert game.id is not None
        moves = self.repo.list_moves(game.id)
        board = board_from_moves([m.uci for m in moves])
        outcome = resignation_outcome(game.player_color, game.player_color)
        uci_moves = [m.uci for m in moves]
        game, analysis, event = self.finish_game(game, outcome, uci_moves, board, at=at)
        return MoveResult(
            game=game,
            player_move=None,
            cpu_move=None,
            outcome=outcome,
            analysis=analysis,
            rating_event=event,
            fen=board.fen(),
            legal_moves_san=(),
        )

    def abort(self, game_id: int, *, at: str) -> Game:
        game = self.repo.get_game(game_id)
        if game.status is not GameStatus.IN_PROGRESS:
            raise GameFinishedError(f"game {game_id} is {game.status.value}")
        assert game.id is not None
        moves = self.repo.list_moves(game.id)
        if len(moves) >= MIN_RATED_PLIES:
            raise AbortTooLateError(
                f"game {game_id} has {len(moves)} plies; abort is only allowed before ply "
                f"{MIN_RATED_PLIES}"
            )
        outcome = abort_outcome(len(moves))
        board = board_from_moves([m.uci for m in moves])
        return self.repo.update_game(
            game.model_copy(
                update={
                    "status": outcome.status,
                    "termination": outcome.termination,
                    "result_score": outcome.result_score,
                    "ply_count": len(moves),
                    "final_fen": board.fen(),
                    "rated": False,
                }
            )
        )

    def finish_game(
        self,
        game: Game,
        outcome: TerminalOutcome,
        uci_moves: Sequence[str],
        board: chess.Board,
        *,
        at: str,
    ) -> tuple[Game, GameAnalysis | None, RatingEvent | None]:
        """Freeze the game, run the mandatory judge pass, then rate it (FR-7/9).

        SCOPE non-goal 10: there is no opt-out — every finished game gets its
        ``internal @ JUDGE_BUDGET`` analysis, which is what the rating channel
        and the coaching report are built from.
        """
        assert game.id is not None
        eco, name, book_depth = self._opening_facts(uci_moves)
        rated = derive_rated(game.source, outcome.status, len(uci_moves))
        finished = self.repo.update_game(
            game.model_copy(
                update={
                    "status": outcome.status,
                    "termination": outcome.termination,
                    "result_score": outcome.result_score,
                    "ply_count": len(uci_moves),
                    "final_fen": board.fen(),
                    "eco": eco,
                    "opening_name": name,
                    "book_depth": book_depth,
                    "rated": rated,
                }
            )
        )
        if not uci_moves:
            return finished, None, None

        analysis = self.repo.add_analysis(
            judge_game(
                uci_moves=uci_moves,
                player_color=finished.player_color,
                book_depth=book_depth,
                analyst=self.analyst,
                levels=self.datasets.levels,
                created_at=at,
                node_budget=JUDGE_BUDGET,
                is_rating_basis=rated,
                game_id=finished.id,
            )
        )
        if not rated:
            return finished, analysis, None

        state = self.require_rating_state()
        profile = self.require_profile()
        assert finished.level_elo is not None and finished.level_id is not None
        assert finished.result_score is not None
        result = apply_rated_game(
            state,
            game_id=finished.id,
            result_score=finished.result_score,
            opponent_elo=finished.level_elo,
            level_played=finished.level_id,
            perf_game=analysis.perf_rating if included_move_count(analysis) else None,
            levels=self.datasets.levels,
            mode=profile.challenge_mode,
            now=at,
        )
        self.repo.save_rating_state(result.state)
        event = self.repo.append_rating_event(result.event)
        return finished, analysis, event

    def game_pgn(self, game_id: int) -> str:
        game = self.repo.get_game(game_id)
        assert game.id is not None
        moves = self.repo.list_moves(game.id)
        profile = self.repo.get_profile()
        player_name = profile.display_name if profile else "Player"
        if game.source is GameSource.IMPORTED and game.imported_tags:
            headers = dict(game.imported_tags)
        else:
            opponent = (
                self.datasets.level_by_id(game.level_id).name
                if game.level_id is not None
                else "Opponent"
            )
            white, black = (
                (player_name, opponent)
                if game.player_color is Color.WHITE
                else (opponent, player_name)
            )
            headers = {
                "Event": "ChessMentor",
                "Site": "local",
                "Date": game.created_at[:10].replace("-", "."),
                "Round": "-",
                "White": white,
                "Black": black,
            }
        headers.pop("Result", None)
        return to_pgn(
            [m.uci for m in moves],
            player_color=game.player_color,
            status=game.status,
            headers=headers,
        )

    # -- analysis ------------------------------------------------------------ #

    def analyze(
        self,
        game_id: int,
        *,
        created_at: str,
        analyst_kind: AnalystKind = AnalystKind.INTERNAL,
        node_budget: int = JUDGE_BUDGET,
    ) -> AnalyzeResult:
        """On-demand (re-)analysis; dedups on ``(analyst, version, budget)``."""
        game = self.repo.get_game(game_id)
        assert game.id is not None
        moves = self.repo.list_moves(game.id)
        if not moves:
            raise ServiceError(f"game {game_id} has no moves to analyse")

        if analyst_kind is AnalystKind.STOCKFISH:
            from .adapters import live_analyst

            analyst: Analyst = live_analyst()
        else:
            analyst = self.analyst

        existing = {
            (a.analyst, a.analyst_version, a.node_budget) for a in self.repo.list_analyses(game.id)
        }
        key = (analyst_kind, analyst.version, node_budget)
        analysis = self.repo.add_analysis(
            judge_game(
                uci_moves=[m.uci for m in moves],
                player_color=game.player_color,
                book_depth=game.book_depth,
                analyst=analyst,
                levels=self.datasets.levels,
                created_at=created_at,
                node_budget=node_budget,
                is_rating_basis=False,
                game_id=game.id,
            )
        )
        return AnalyzeResult(analysis=analysis, created=key not in existing)

    def get_analysis(self, game_id: int, *, analysis_id: int | None = None) -> GameAnalysis:
        if analysis_id is not None:
            analysis = self.repo.get_analysis(analysis_id)
            if analysis.game_id != game_id:
                raise NotFoundError(f"analysis {analysis_id} does not belong to game {game_id}")
            return analysis
        latest = self.repo.latest_analysis(game_id)
        if latest is None:
            raise NotFoundError(f"game {game_id} has no analysis")
        return latest

    # -- import -------------------------------------------------------------- #

    def import_pgn(
        self,
        text: str,
        *,
        created_at: str,
        side: Color | None = None,
        analyze: bool = False,
    ) -> list[ImportedGame]:
        """FR-13: import one or many games; imported games are never rated."""
        profile = self.require_profile()
        parsed = parse_pgn(text)
        imported: list[ImportedGame] = []
        for entry in parsed:
            player_color = resolve_import_side(
                entry.tags, requested=side, display_name=profile.display_name
            )
            result = import_outcome(entry.tags.get("Result"), player_color)
            eco, name, book_depth = self._opening_facts(entry.uci_moves)
            game = self.repo.create_game(
                Game(
                    source=GameSource.IMPORTED,
                    created_at=created_at,
                    seed=None,
                    player_color=player_color,
                    level_id=None,
                    level_elo=None,
                    status=result.status,
                    termination=result.termination,
                    result_score=result.result_score,
                    ply_count=entry.ply_count,
                    final_fen=entry.final_fen,
                    eco=eco,
                    opening_name=name,
                    book_depth=book_depth,
                    rated=False,
                    imported_tags=entry.tags,
                )
            )
            assert game.id is not None
            board = chess.Board()
            for ply, uci in enumerate(entry.uci_moves, start=1):
                move = chess.Move.from_uci(uci)
                self.repo.append_move(game.id, make_move_record(board, move, ply, game_id=game.id))
                board.push(move)

            analysis: GameAnalysis | None = None
            if analyze and entry.uci_moves:
                analysis = self.analyze(game.id, created_at=created_at).analysis
            imported.append(ImportedGame(game=game, analysis=analysis))
        return imported

    # -- rating -------------------------------------------------------------- #

    def rating(self) -> RatingView:
        state = self.require_rating_state()
        level = self.datasets.level_by_id(state.current_level_id)
        r_hat = blended_rating(state)
        return RatingView(
            state=state,
            r_hat=r_hat,
            lambda_used=blend_lambda(state.glicko_rd, state.judged_games),
            expected_score_at_current_level=expected_score(r_hat, level.elo_internal),
            recommended_level_id=state.current_level_id,
            calibration_warning=state.calibration_warning,
        )

    def rating_history(self, *, limit: int | None = None) -> list[RatingEvent]:
        return self.repo.list_rating_events(limit=limit)

    # -- coaching ------------------------------------------------------------ #

    def create_report(
        self,
        *,
        created_at: str,
        last_games: int = DEFAULT_REPORT_WINDOW,
        include_imported: bool = False,
    ) -> ReportView:
        games = self.repo.list_games()
        analyses_by_game = {
            game.id: self.repo.list_analyses(game.id) for game in games if game.id is not None
        }
        selection = select_window(
            games,
            analyses_by_game,
            engine_version=self.engine_version,
            last_games=last_games,
            include_imported=include_imported,
        )
        san_by_game = {
            entry.game.id: [m.san for m in self.repo.list_moves(entry.game.id)]
            for entry in selection.entries
            if entry.game.id is not None
        }
        previous = self.repo.list_reports(limit=1)
        prev_report = previous[0] if previous else None
        report = self.repo.add_report(
            build_report(
                selection=selection,
                san_by_game=san_by_game,
                advice_catalog=self.datasets.advice,
                created_at=created_at,
                include_imported=include_imported,
                prev_report_id=prev_report.id if prev_report else None,
            )
        )
        return ReportView(report=report, deltas=report_deltas(report, prev_report))

    def get_report(self, report_id: int) -> ReportView:
        report = self.repo.get_report(report_id)
        previous = (
            self.repo.get_report(report.prev_report_id)
            if report.prev_report_id is not None
            else None
        )
        return ReportView(report=report, deltas=report_deltas(report, previous))

    def list_reports(self, *, limit: int | None = None) -> list[CoachingReport]:
        return self.repo.list_reports(limit=limit)

    # -- misc ---------------------------------------------------------------- #

    def report_basis_analyses(self, game_id: int) -> list[GameAnalysis]:
        return [
            a for a in self.repo.list_analyses(game_id) if is_report_basis(a, self.engine_version)
        ]


# Re-exported so the surfaces can catch one import's worth of errors.
IllegalMoveError = IllegalMoveError
AmbiguousSideError = AmbiguousSideError
PgnParseError = PgnParseError
NotFoundError = NotFoundError
