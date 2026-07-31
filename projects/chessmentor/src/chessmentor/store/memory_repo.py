"""In-memory backend used by tests and evals.

Same behaviour as the SQLite backend — the shared guards in
:mod:`chessmentor.store._guards` are what make "same behaviour" true for the
invariants that span rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import (
    CoachingReport,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    Level,
    MoveRecord,
    PlayerProfile,
    RatingEvent,
    RatingState,
)
from ._guards import (
    guard_game_mutable,
    guard_move_append,
    guard_rating_event,
    guard_single_in_progress,
)
from .repository import ConflictError, NotFoundError

__all__ = ["InMemoryRepository"]


class InMemoryRepository:
    """Dict-backed repository."""

    def __init__(self) -> None:
        self._levels: dict[int, Level] = {}
        self._profile: PlayerProfile | None = None
        self._rating_state: RatingState | None = None
        self._games: dict[int, Game] = {}
        self._moves: dict[int, list[MoveRecord]] = {}
        self._analyses: dict[int, GameAnalysis] = {}
        self._rating_events: list[RatingEvent] = []
        self._reports: dict[int, CoachingReport] = {}
        self._next_game_id = 1
        self._next_move_id = 1
        self._next_analysis_id = 1
        self._next_event_id = 1
        self._next_report_id = 1

    # -- lifecycle ---------------------------------------------------------- #

    def initialize(self, levels: Sequence[Level]) -> None:
        self._levels = {level.id: level for level in levels}

    def close(self) -> None:
        return None

    # -- levels -------------------------------------------------------------- #

    def list_levels(self) -> list[Level]:
        return [self._levels[key] for key in sorted(self._levels)]

    def get_level(self, level_id: int) -> Level:
        try:
            return self._levels[level_id]
        except KeyError as exc:
            raise NotFoundError(f"no such level: {level_id}") from exc

    # -- profile / rating state ----------------------------------------------- #

    def get_profile(self) -> PlayerProfile | None:
        return self._profile

    def save_profile(self, profile: PlayerProfile) -> PlayerProfile:
        self._profile = profile.model_copy(update={"id": 1})
        return self._profile

    def get_rating_state(self) -> RatingState | None:
        return self._rating_state

    def save_rating_state(self, state: RatingState) -> RatingState:
        self._rating_state = state.model_copy(update={"id": 1})
        return self._rating_state

    # -- games ---------------------------------------------------------------- #

    def create_game(self, game: Game) -> Game:
        guard_single_in_progress(self.get_in_progress_game(), game)
        stored = game.model_copy(update={"id": self._next_game_id})
        self._games[self._next_game_id] = stored
        self._moves[self._next_game_id] = []
        self._next_game_id += 1
        return stored

    def update_game(self, game: Game) -> Game:
        if game.id is None:
            raise ConflictError("cannot update a game without an id")
        stored = self.get_game(game.id)
        guard_game_mutable(stored)
        if game.status is GameStatus.IN_PROGRESS:
            other = self.get_in_progress_game()
            if other is not None and other.id != game.id:
                guard_single_in_progress(other, game)
        self._games[game.id] = game
        return game

    def get_game(self, game_id: int) -> Game:
        try:
            return self._games[game_id]
        except KeyError as exc:
            raise NotFoundError(f"no such game: {game_id}") from exc

    def list_games(
        self,
        *,
        status: GameStatus | None = None,
        source: GameSource | None = None,
        limit: int | None = None,
    ) -> list[Game]:
        games = [self._games[key] for key in sorted(self._games, reverse=True)]
        if status is not None:
            games = [g for g in games if g.status is status]
        if source is not None:
            games = [g for g in games if g.source is source]
        return games[:limit] if limit is not None else games

    def get_in_progress_game(self) -> Game | None:
        for key in sorted(self._games):
            game = self._games[key]
            if game.status is GameStatus.IN_PROGRESS:
                return game
        return None

    # -- moves ------------------------------------------------------------------ #

    def append_move(self, game_id: int, record: MoveRecord) -> MoveRecord:
        self.get_game(game_id)
        existing = self._moves.setdefault(game_id, [])
        guard_move_append(existing, record)
        stored = record.model_copy(update={"id": self._next_move_id, "game_id": game_id})
        self._next_move_id += 1
        existing.append(stored)
        return stored

    def list_moves(self, game_id: int) -> list[MoveRecord]:
        return list(self._moves.get(game_id, []))

    # -- analyses ---------------------------------------------------------------- #

    def add_analysis(self, analysis: GameAnalysis) -> GameAnalysis:
        if analysis.game_id is None:
            raise ConflictError("an analysis must reference a game")
        key = (
            analysis.game_id,
            analysis.analyst,
            analysis.analyst_version,
            analysis.node_budget,
        )
        for stored in self._analyses.values():
            if (
                stored.game_id,
                stored.analyst,
                stored.analyst_version,
                stored.node_budget,
            ) == key:
                return stored
        stored = analysis.model_copy(
            update={
                "id": self._next_analysis_id,
                "moves": [
                    move.model_copy(update={"analysis_id": self._next_analysis_id})
                    for move in analysis.moves
                ],
            }
        )
        self._analyses[self._next_analysis_id] = stored
        self._next_analysis_id += 1
        return stored

    def get_analysis(self, analysis_id: int) -> GameAnalysis:
        try:
            return self._analyses[analysis_id]
        except KeyError as exc:
            raise NotFoundError(f"no such analysis: {analysis_id}") from exc

    def list_analyses(self, game_id: int) -> list[GameAnalysis]:
        return [
            self._analyses[key]
            for key in sorted(self._analyses)
            if self._analyses[key].game_id == game_id
        ]

    def latest_analysis(self, game_id: int) -> GameAnalysis | None:
        analyses = self.list_analyses(game_id)
        return analyses[-1] if analyses else None

    # -- rating events ------------------------------------------------------------ #

    def append_rating_event(self, event: RatingEvent) -> RatingEvent:
        guard_rating_event(self._rating_state, [e.game_id for e in self._rating_events], event)
        stored = event.model_copy(update={"id": self._next_event_id})
        self._next_event_id += 1
        self._rating_events.append(stored)
        return stored

    def list_rating_events(self, *, limit: int | None = None) -> list[RatingEvent]:
        events = list(self._rating_events)
        return events[-limit:] if limit is not None else events

    # -- reports -------------------------------------------------------------------- #

    def add_report(self, report: CoachingReport) -> CoachingReport:
        report_id = self._next_report_id
        stored = report.model_copy(
            update={
                "id": report_id,
                "suggestions": [
                    suggestion.model_copy(update={"report_id": report_id})
                    for suggestion in report.suggestions
                ],
            }
        )
        self._reports[report_id] = stored
        self._next_report_id += 1
        return stored

    def get_report(self, report_id: int) -> CoachingReport:
        try:
            return self._reports[report_id]
        except KeyError as exc:
            raise NotFoundError(f"no such report: {report_id}") from exc

    def list_reports(self, *, limit: int | None = None) -> list[CoachingReport]:
        reports = [self._reports[key] for key in sorted(self._reports, reverse=True)]
        return reports[:limit] if limit is not None else reports
