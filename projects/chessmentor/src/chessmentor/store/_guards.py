"""Invariant guards shared by every repository backend.

These are the DATA_MODEL invariants that are *behavioural* rather than
structural — the ones a store must enforce because they span rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Game, GameStatus, MoveRecord, RatingEvent, RatingState
from .repository import ConflictError

__all__ = [
    "guard_game_mutable",
    "guard_move_append",
    "guard_rating_event",
    "guard_single_in_progress",
]


def guard_single_in_progress(existing: Game | None, incoming: Game) -> None:
    """FR-6: at most one game may be ``in_progress`` across the table."""
    if incoming.status is GameStatus.IN_PROGRESS and existing is not None:
        raise ConflictError(
            f"a game is already in progress (id {existing.id}); finish, resign or abort it first"
        )


def guard_game_mutable(stored: Game) -> None:
    """Non-``in_progress`` rows are immutable."""
    if stored.status is not GameStatus.IN_PROGRESS:
        raise ConflictError(f"game {stored.id} is {stored.status} and immutable")


def guard_move_append(existing: Sequence[MoveRecord], record: MoveRecord) -> None:
    """``move_record`` is append-only with contiguous, unique, alternating plies."""
    expected_ply = len(existing) + 1
    if record.ply != expected_ply:
        raise ConflictError(
            f"move_record is append-only: expected ply {expected_ply}, got {record.ply}"
        )


def guard_rating_event(
    state: RatingState | None, existing_game_ids: Sequence[int], event: RatingEvent
) -> None:
    """One event per rated game, applied in game-termination order."""
    if event.game_id in existing_game_ids:
        raise ConflictError(f"game {event.game_id} already has a rating event")
    if state is not None and state.last_game_id is not None and event.game_id < state.last_game_id:
        raise ConflictError(
            f"rating events must be appended in termination order: game {event.game_id} "
            f"precedes {state.last_game_id}"
        )
