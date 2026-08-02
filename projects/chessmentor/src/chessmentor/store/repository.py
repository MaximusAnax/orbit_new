"""The repository interface every backend implements.

Persistence is behind this interface so the engine, the API and the CLI never
see SQL.  Two backends ship: :class:`~chessmentor.store.sqlite_repo.SQLiteRepository`
(the default, stdlib ``sqlite3``) and
:class:`~chessmentor.store.memory_repo.InMemoryRepository` (tests and evals).

Invariants the interface enforces on every backend:

* at most one game with ``status = in_progress`` (FR-6);
* non-``in_progress`` games are immutable;
* ``move_record`` and ``rating_event`` are append-only;
* ``(game_id, analyst, analyst_version, node_budget)`` is unique on analyses;
* exactly one ``rating_event`` per rated game, applied in termination order.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

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

__all__ = ["ConflictError", "NotFoundError", "Repository"]


class NotFoundError(KeyError):
    """The requested row does not exist."""


class ConflictError(RuntimeError):
    """A write would violate a stated invariant (in-progress game, immutability…)."""


@runtime_checkable
class Repository(Protocol):
    """Persistence port."""

    # -- lifecycle ---------------------------------------------------------- #

    def initialize(self, levels: Sequence[Level]) -> None:
        """Create the schema (idempotent) and materialise the committed ladder."""
        ...

    def close(self) -> None: ...

    # -- committed ladder ---------------------------------------------------- #

    def list_levels(self) -> list[Level]: ...

    def get_level(self, level_id: int) -> Level: ...

    # -- profile / rating state ---------------------------------------------- #

    def get_profile(self) -> PlayerProfile | None: ...

    def save_profile(self, profile: PlayerProfile) -> PlayerProfile: ...

    def get_rating_state(self) -> RatingState | None: ...

    def save_rating_state(self, state: RatingState) -> RatingState: ...

    # -- games --------------------------------------------------------------- #

    def create_game(self, game: Game) -> Game:
        """Insert a game; raises :class:`ConflictError` if one is already in progress."""
        ...

    def update_game(self, game: Game) -> Game:
        """Update a game; raises :class:`ConflictError` on a frozen (finished) row."""
        ...

    def get_game(self, game_id: int) -> Game: ...

    def list_games(
        self,
        *,
        status: GameStatus | None = None,
        source: GameSource | None = None,
        limit: int | None = None,
    ) -> list[Game]: ...

    def get_in_progress_game(self) -> Game | None: ...

    # -- moves ---------------------------------------------------------------- #

    def append_move(self, game_id: int, record: MoveRecord) -> MoveRecord:
        """Append one ply; raises :class:`ConflictError` on a gap or duplicate."""
        ...

    def list_moves(self, game_id: int) -> list[MoveRecord]: ...

    # -- analyses -------------------------------------------------------------- #

    def add_analysis(self, analysis: GameAnalysis) -> GameAnalysis:
        """Insert an analysis, or return the existing row for the same config tuple."""
        ...

    def get_analysis(self, analysis_id: int) -> GameAnalysis: ...

    def list_analyses(self, game_id: int) -> list[GameAnalysis]: ...

    def latest_analysis(self, game_id: int) -> GameAnalysis | None: ...

    # -- rating events ---------------------------------------------------------- #

    def append_rating_event(self, event: RatingEvent) -> RatingEvent: ...

    def list_rating_events(self, *, limit: int | None = None) -> list[RatingEvent]: ...

    # -- coaching reports -------------------------------------------------------- #

    def add_report(self, report: CoachingReport) -> CoachingReport: ...

    def get_report(self, report_id: int) -> CoachingReport: ...

    def list_reports(self, *, limit: int | None = None) -> list[CoachingReport]: ...
