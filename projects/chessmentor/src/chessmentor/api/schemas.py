"""Pydantic v2 request/response schemas for the REST surface (FR-14).

Domain entities (``Game``, ``GameAnalysis``, ``RatingEvent``, …) are already
Pydantic models, so responses embed them directly instead of re-describing
them; the schemas here are the request bodies and the few composite views the
API adds on top (board state, ladder + recommendation, rating summary).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    AnalystKind,
    ChallengeMode,
    CoachingReport,
    Color,
    Game,
    GameAnalysis,
    Level,
    MistakeCategory,
    MoveRecord,
    PreferredColor,
    RatingEvent,
    RatingState,
)

__all__ = [
    "AnalysisRequest",
    "CreateGameRequest",
    "ErrorBody",
    "GameDetail",
    "GameListResponse",
    "HealthResponse",
    "ImportRequest",
    "ImportResponse",
    "ImportedGameOut",
    "LadderResponse",
    "MoveRequest",
    "MoveResponse",
    "PgnResponse",
    "ProfileUpdate",
    "RatingHistoryResponse",
    "RatingResponse",
    "ReportListResponse",
    "ReportResponse",
]


class ErrorBody(BaseModel):
    """The error catalog's wire shape.

    ``detail`` is the human-readable message, ``code`` the machine-readable
    catalog entry.  ``in_progress_game_id`` is present exactly on the FR-6
    409 (SCOPE's API sketch names it explicitly).
    """

    model_config = ConfigDict(extra="allow")

    detail: str
    code: str
    in_progress_game_id: int | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    engine_version: str
    levels: int
    openings: int
    advice_entries: int
    initialized: bool


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1)
    challenge_mode: ChallengeMode | None = None
    preferred_color: PreferredColor | None = None
    at: str | None = None


class LevelOut(Level):
    """A ladder rung plus the controller's live view of it."""

    expected_score: float
    recommended: bool


class LadderResponse(BaseModel):
    levels: list[LevelOut]
    recommended_level_id: int
    r_hat: float
    challenge_mode: ChallengeMode
    target_score: float


class CreateGameRequest(BaseModel):
    color: PreferredColor | None = None
    level_id: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0)
    started_at: str | None = None


class GameDetail(BaseModel):
    game: Game
    fen: str
    turn: Color
    player_to_move: bool
    legal_moves_san: list[str]
    moves: list[MoveRecord]
    pgn: str


class GameListResponse(BaseModel):
    games: list[Game]


class MoveRequest(BaseModel):
    move: str = Field(min_length=2, description="SAN or UCI")
    at: str | None = None


class MoveResponse(BaseModel):
    game: Game
    player_move: MoveRecord | None
    cpu_move: MoveRecord | None
    fen: str
    legal_moves_san: list[str]
    status: str
    termination: str | None
    result_score: float | None
    analysis: GameAnalysis | None = None
    rating_event: RatingEvent | None = None


class PgnResponse(BaseModel):
    game_id: int
    pgn: str


class AnalysisRequest(BaseModel):
    analyst: AnalystKind = AnalystKind.INTERNAL
    node_budget: int | None = Field(default=None, ge=1)
    at: str | None = None


class ImportRequest(BaseModel):
    pgn: str = Field(min_length=1)
    side: Color | None = Field(default=None, alias="as")
    analyze: bool = False
    at: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ImportedGameOut(BaseModel):
    game: Game
    analysis: GameAnalysis | None = None


class ImportResponse(BaseModel):
    imported: list[ImportedGameOut]


class RatingResponse(BaseModel):
    state: RatingState
    r_hat: float
    lambda_used: float
    expected_score_at_current_level: float
    recommended_level_id: int
    calibration_warning: bool
    warning_text: str | None = None


class RatingHistoryResponse(BaseModel):
    events: list[RatingEvent]


class ReportRequest(BaseModel):
    last_games: int = Field(default=10, ge=1, le=100)
    include_imported: bool = False
    at: str | None = None


class ReportResponse(BaseModel):
    report: CoachingReport
    deltas: dict[MistakeCategory, float]


class ReportListResponse(BaseModel):
    reports: list[CoachingReport]
