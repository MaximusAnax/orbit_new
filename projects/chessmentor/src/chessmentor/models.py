"""Pydantic v2 domain entities (DATA_MODEL.md).

Every entity in DATA_MODEL.md lives here with its stated invariants enforced by
validators.  Frozen models are the append-only / immutable ones (``MoveRecord``,
``GameAnalysis``, ``MoveAnalysis``, ``RatingEvent``, ``CoachingReport``,
``Suggestion``, ``Level``, ``OpeningLine``, ``AdviceEntry``).

Timestamps are ISO-8601 strings supplied by callers — the engine never reads a
clock.  Seeds are 64-bit ints supplied at game creation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from .constants import (
    CP_LOSS_CAP,
    MAX_BOOK_DEPTH,
    MIN_RATED_PLIES,
    RD_FLOOR,
    RD_INIT,
    SEV_BLUNDER,
    SEV_INACCURACY,
    SEV_MISTAKE,
    SURPRISE_WINDOW,
)

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class Color(StrEnum):
    WHITE = "white"
    BLACK = "black"

    @property
    def opposite(self) -> Color:
        return Color.BLACK if self is Color.WHITE else Color.WHITE


class PreferredColor(StrEnum):
    WHITE = "white"
    BLACK = "black"
    RANDOM = "random"


class ChallengeMode(StrEnum):
    COMFORT = "comfort"
    BALANCED = "balanced"
    STRETCH = "stretch"


class GameSource(StrEnum):
    PLAYED = "played"
    IMPORTED = "imported"


class GameStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PLAYER_WIN = "player_win"
    OPPONENT_WIN = "opponent_win"
    DRAW = "draw"
    ABORTED = "aborted"
    UNFINISHED = "unfinished"


class Termination(StrEnum):
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    RESIGNATION = "resignation"
    INSUFFICIENT_MATERIAL = "insufficient_material"
    THREEFOLD_REPETITION = "threefold_repetition"
    FIFTY_MOVE_RULE = "fifty_move_rule"
    IMPORTED_RESULT = "imported_result"
    IMPORTED_UNFINISHED = "imported_unfinished"
    ABORTED = "aborted"


class AnalystKind(StrEnum):
    INTERNAL = "internal"
    STOCKFISH = "stockfish"


class Severity(StrEnum):
    OK = "ok"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.OK: 0,
    Severity.INACCURACY: 1,
    Severity.MISTAKE: 2,
    Severity.BLUNDER: 3,
}

#: Severities that FR-11 asks the taxonomy to explain.
FLAGGED_SEVERITIES: frozenset[Severity] = frozenset({Severity.MISTAKE, Severity.BLUNDER})


class Phase(StrEnum):
    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


class MistakeCategory(StrEnum):
    ALLOWED_MATE = "allowed_mate"
    MISSED_MATE = "missed_mate"
    BAD_TRADE = "bad_trade"
    HUNG_PIECE = "hung_piece"
    MISSED_TACTIC = "missed_tactic"
    ALLOWED_TACTIC = "allowed_tactic"
    ENDGAME_TECHNIQUE = "endgame_technique"
    OPENING_PRINCIPLE = "opening_principle"
    POSITIONAL_DRIFT = "positional_drift"


class Motif(StrEnum):
    FORK = "fork"
    PIN = "pin"
    SKEWER = "skewer"
    DISCOVERED = "discovered"
    MATE_THREAT = "mate_threat"
    HANGING_CAPTURE = "hanging_capture"
    OTHER = "other"
    NONE = "none"


#: Categories that carry a motif subtag (FR-11).
MOTIF_CATEGORIES: frozenset[MistakeCategory] = frozenset(
    {MistakeCategory.MISSED_TACTIC, MistakeCategory.ALLOWED_TACTIC}
)

Uint64 = Annotated[int, Field(ge=0, le=(1 << 64) - 1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
Accuracy = Annotated[float, Field(ge=0.0, le=100.0)]


def severity_for(delta_w: float) -> Severity:
    """FR-9 severity tiers on ``delta_w`` (single source of truth)."""
    if delta_w >= SEV_BLUNDER:
        return Severity.BLUNDER
    if delta_w >= SEV_MISTAKE:
        return Severity.MISTAKE
    if delta_w >= SEV_INACCURACY:
        return Severity.INACCURACY
    return Severity.OK


# --------------------------------------------------------------------------- #
# Committed datasets
# --------------------------------------------------------------------------- #


class Level(BaseModel):
    """A rung of the difficulty ladder (``data/levels.json`` → table ``level``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PositiveInt
    name: str = Field(min_length=1)
    max_depth: int = Field(ge=1, le=5)
    node_budget: PositiveInt
    noise_sigma_cp: NonNegativeFloat
    blunder_prob: float = Field(ge=0.0, le=0.35)
    blunder_margin_lo_cp: int | None = None
    blunder_margin_hi_cp: int | None = None
    book_plies: int = Field(ge=0, le=MAX_BOOK_DEPTH)
    elo_internal: float
    acpl_mean: float = Field(gt=0.0)
    acpl_std: float = Field(gt=0.0)
    calibration_seed: int
    calibrated_at: str
    engine_version: str

    @model_validator(mode="after")
    def _check_margins(self) -> Self:
        lo, hi = self.blunder_margin_lo_cp, self.blunder_margin_hi_cp
        if self.blunder_prob == 0.0:
            if lo is not None or hi is not None:
                raise ValueError("blunder margins must be null when blunder_prob == 0")
        else:
            if lo is None or hi is None:
                raise ValueError("blunder margins are required when blunder_prob > 0")
            if not 0 <= lo < hi:
                raise ValueError("blunder margins must satisfy 0 <= lo < hi")
        return self


class BookMove(BaseModel):
    """One weighted continuation returned by ``OpeningBook.probe``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uci: str = Field(min_length=4, max_length=5)
    weight: PositiveInt


class OpeningLine(BaseModel):
    """A committed opening line (``data/openings.json``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eco: str = Field(pattern=r"^[A-E][0-9]{2}$")
    name: str = Field(min_length=1)
    uci: list[str] = Field(min_length=4, max_length=MAX_BOOK_DEPTH)
    weight: PositiveInt


class Opening(BaseModel):
    """Result of ``OpeningBook.identify`` — the longest prefix match of a game."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eco: str
    name: str
    depth: int = Field(ge=0, le=MAX_BOOK_DEPTH)


class AdviceEntry(BaseModel):
    """A curated coaching entry (``data/advice.json``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    category: MistakeCategory
    phase: Phase | None = None
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    drill: str = Field(min_length=1)
    source_note: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# User state
# --------------------------------------------------------------------------- #


class PlayerProfile(BaseModel):
    """The singleton local profile (table ``player_profile``)."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(default=1, ge=1, le=1)
    display_name: str = Field(min_length=1)
    challenge_mode: ChallengeMode = ChallengeMode.BALANCED
    preferred_color: PreferredColor = PreferredColor.RANDOM
    created_at: str
    updated_at: str


class RatingState(BaseModel):
    """The singleton rating state (table ``rating_state``).

    ``r_hat`` and ``lambda_`` are derived on read and never stored.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(default=1, ge=1, le=1)
    glicko_rating: float
    glicko_rd: float = Field(ge=RD_FLOOR, le=RD_INIT)
    perf_ewma: float | None = None
    judged_games: NonNegativeInt = 0
    rated_games: NonNegativeInt = 0
    surprise_window: list[float] = Field(default_factory=list, max_length=SURPRISE_WINDOW)
    games_since_rd_inflation: NonNegativeInt = 0
    divergence_streak: NonNegativeInt = 0
    calibration_warning: bool = False
    current_level_id: PositiveInt
    last_game_id: int | None = None
    updated_at: str

    @model_validator(mode="after")
    def _check_perf(self) -> Self:
        if self.perf_ewma is None and self.judged_games != 0:
            raise ValueError("judged_games must be 0 while perf_ewma is null")
        if self.perf_ewma is not None and self.judged_games == 0:
            raise ValueError("perf_ewma requires judged_games >= 1")
        return self


# --------------------------------------------------------------------------- #
# Games
# --------------------------------------------------------------------------- #

#: Statuses whose ``result_score`` must be null.
_NULL_RESULT_STATUSES: frozenset[GameStatus] = frozenset(
    {GameStatus.IN_PROGRESS, GameStatus.ABORTED, GameStatus.UNFINISHED}
)
_STATUS_SCORE: dict[GameStatus, float] = {
    GameStatus.PLAYER_WIN: 1.0,
    GameStatus.DRAW: 0.5,
    GameStatus.OPPONENT_WIN: 0.0,
}
#: Statuses that make a played game eligible for rating (FR-6).
DECISIVE_STATUSES: frozenset[GameStatus] = frozenset(_STATUS_SCORE)


class CpuMeta(BaseModel):
    """FR-4 throttle metadata recorded on every CPU move."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    depth: NonNegativeInt
    nodes: NonNegativeInt
    root_moves: NonNegativeInt
    score_cp: int | None = None
    best_score_cp: int | None = None
    blunder_rolled: bool = False
    blunder_injected: bool = False
    noise_changed_pick: bool = False

    @model_validator(mode="after")
    def _check_injection(self) -> Self:
        if self.blunder_injected and not self.blunder_rolled:
            raise ValueError("blunder_injected requires blunder_rolled")
        if self.blunder_injected and self.noise_changed_pick:
            raise ValueError("noise is skipped when a blunder is injected (FR-4 step 3)")
        return self


class MoveRecord(BaseModel):
    """One ply of a game (table ``move_record``, append-only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    game_id: int | None = None
    ply: PositiveInt
    color: Color
    san: str = Field(min_length=1)
    uci: str = Field(min_length=4, max_length=5)
    fen_after: str = Field(min_length=1)
    is_book: bool = False
    cpu_meta: CpuMeta | None = None

    @model_validator(mode="after")
    def _check_color_alternates(self) -> Self:
        expected = Color.WHITE if self.ply % 2 == 1 else Color.BLACK
        if self.color is not expected:
            raise ValueError(f"ply {self.ply} must be played by {expected}")
        return self


class Game(BaseModel):
    """A played or imported game (table ``game``)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    source: GameSource
    created_at: str
    seed: Uint64 | None = None
    player_color: Color
    level_id: int | None = None
    level_elo: float | None = None
    recommended_level_id: int | None = None
    level_overridden: bool = False
    status: GameStatus
    termination: Termination | None = None
    result_score: float | None = None
    ply_count: NonNegativeInt = 0
    final_fen: str | None = None
    eco: str | None = None
    opening_name: str | None = None
    book_depth: int = Field(default=0, ge=0, le=MAX_BOOK_DEPTH)
    rated: bool = False
    imported_tags: dict[str, str] | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        played = self.source is GameSource.PLAYED
        if played and self.seed is None:
            raise ValueError("played games require a seed")
        if not played and self.seed is not None:
            raise ValueError("imported games must not carry a seed")
        if played and self.level_id is None:
            raise ValueError("played games require a level_id")
        if not played and self.level_id is not None:
            raise ValueError("imported games must not carry a level_id")

        if self.status in _NULL_RESULT_STATUSES:
            if self.result_score is not None:
                raise ValueError(f"result_score must be null for status {self.status}")
        else:
            if self.result_score != _STATUS_SCORE[self.status]:
                raise ValueError(
                    f"result_score {self.result_score} inconsistent with {self.status}"
                )

        if self.status is GameStatus.IN_PROGRESS and self.termination is not None:
            raise ValueError("an in-progress game has no termination")
        if self.status is not GameStatus.IN_PROGRESS and self.termination is None:
            raise ValueError("a finished game must record a termination")

        expected_rated = (
            played and self.status in DECISIVE_STATUSES and self.ply_count >= MIN_RATED_PLIES
        )
        if self.rated != expected_rated:
            raise ValueError("rated is derived: played AND decisive AND ply_count >= 8")
        if self.level_overridden and self.recommended_level_id == self.level_id:
            raise ValueError("level_overridden requires level_id != recommended_level_id")
        return self


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #


class MoveEval(BaseModel):
    """What an ``Analyst`` returns for one position."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    best_move: str | None
    score_cp: int
    pv: list[str] = Field(default_factory=list)
    nodes: NonNegativeInt = 0
    depth: NonNegativeInt = 0


class PhaseStats(BaseModel):
    """Per-phase aggregate inside ``GameAnalysis.per_phase``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acpl: float
    accuracy: Accuracy
    n_blunders: NonNegativeInt
    n_mistakes: NonNegativeInt
    n_inaccuracies: NonNegativeInt
    dw_sum: NonNegativeFloat
    n_moves: NonNegativeInt


class KeyMoment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ply: PositiveInt
    dw: NonNegativeFloat
    severity: Severity


class MoveAnalysis(BaseModel):
    """Per-move judgment (table ``move_analysis``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    analysis_id: int | None = None
    ply: PositiveInt
    in_acpl: bool
    cp_best: int
    cp_played: int
    cp_loss: int = Field(ge=0, le=CP_LOSS_CAP)
    w_before: Probability
    w_after: Probability
    delta_w: NonNegativeFloat
    severity: Severity
    best_uci: str = Field(min_length=4, max_length=5)
    best_line_san: list[str] = Field(default_factory=list)
    phase: Phase
    category: MistakeCategory | None = None
    motif: Motif = Motif.NONE
    evidence: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        expected_loss = min(CP_LOSS_CAP, max(0, self.cp_best - self.cp_played))
        if self.cp_loss != expected_loss:
            raise ValueError("cp_loss must be min(CP_LOSS_CAP, max(0, cp_best - cp_played))")
        expected_dw = max(0.0, self.w_before - self.w_after)
        if abs(self.delta_w - expected_dw) > 1e-9:
            raise ValueError("delta_w must be max(0, w_before - w_after)")
        if self.severity is not severity_for(self.delta_w):
            raise ValueError("severity must follow the SEV_* thresholds on delta_w")
        should_have_category = self.in_acpl and self.severity in FLAGGED_SEVERITIES
        if should_have_category and self.category is None:
            raise ValueError("flagged, included moves must carry a category")
        if not should_have_category and self.category is not None:
            raise ValueError("only flagged, included moves may carry a category")
        if self.motif is not Motif.NONE and self.category not in MOTIF_CATEGORIES:
            raise ValueError("motifs only annotate missed_tactic / allowed_tactic")
        return self


class GameAnalysis(BaseModel):
    """An immutable analysis of one game (table ``game_analysis``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    game_id: int | None = None
    analyst: AnalystKind
    analyst_version: str = Field(min_length=1)
    node_budget: PositiveInt
    created_at: str
    book_depth: int = Field(ge=0, le=MAX_BOOK_DEPTH)
    is_rating_basis: bool = False
    acpl: NonNegativeFloat
    accuracy: Accuracy
    perf_rating: float
    n_blunders: NonNegativeInt = 0
    n_mistakes: NonNegativeInt = 0
    n_inaccuracies: NonNegativeInt = 0
    mg_start_ply: int | None = None
    eg_start_ply: int | None = None
    per_phase: dict[Phase, PhaseStats] = Field(default_factory=dict)
    key_moments: list[KeyMoment] = Field(default_factory=list, max_length=3)
    moves: list[MoveAnalysis] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_boundaries(self) -> Self:
        if self.eg_start_ply is not None:
            if self.mg_start_ply is None:
                raise ValueError("eg_start_ply requires mg_start_ply")
            if self.eg_start_ply < self.mg_start_ply:
                raise ValueError("eg_start_ply must be >= mg_start_ply")
        plies = [m.ply for m in self.moves]
        if len(plies) != len(set(plies)):
            raise ValueError("(analysis_id, ply) must be unique")
        return self


# --------------------------------------------------------------------------- #
# Rating events
# --------------------------------------------------------------------------- #


class RatingEvent(BaseModel):
    """One append-only rating event per rated game (table ``rating_event``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    game_id: int
    created_at: str
    result_score: float
    opponent_elo: float
    expected_score: Probability
    surprise_after: float
    rd_inflated: bool
    glicko_r_before: float
    glicko_rd_before: float
    glicko_r_after: float
    glicko_rd_after: float
    perf_game: float
    perf_ewma_after: float
    lambda_used: Probability
    r_hat_after: float
    level_played: int
    level_next: int

    @model_validator(mode="after")
    def _check_score(self) -> Self:
        if self.result_score not in (0.0, 0.5, 1.0):
            raise ValueError("result_score must be 1, 0.5 or 0")
        return self


# --------------------------------------------------------------------------- #
# Coaching
# --------------------------------------------------------------------------- #


class WindowEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    game_id: int
    analysis_id: int


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    game_id: int
    ply: PositiveInt
    san: str
    dw: NonNegativeFloat


class CategoryTotal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    count: NonNegativeInt
    dw_sum: NonNegativeFloat


class Suggestion(BaseModel):
    """A ranked coaching suggestion with a snapshot of its advice text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    report_id: int | None = None
    rank: int = Field(ge=1, le=3)
    category: MistakeCategory
    priority_score: NonNegativeFloat
    advice_id: str = Field(min_length=1)
    advice_title: str = Field(min_length=1)
    advice_body: str = Field(min_length=1)
    advice_drill: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=3)


class CoachingReport(BaseModel):
    """An immutable coaching report (table ``coaching_report`` + ``suggestion``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    created_at: str
    window: list[WindowEntry] = Field(default_factory=list)
    skipped_game_ids: list[int] = Field(default_factory=list)
    include_imported: bool = False
    totals_by_category: dict[MistakeCategory, CategoryTotal] = Field(default_factory=dict)
    totals_by_phase: dict[Phase, float] = Field(default_factory=dict)
    prev_report_id: int | None = None
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _check_ranks(self) -> Self:
        ranks = [s.rank for s in self.suggestions]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("suggestion ranks must be 1..n contiguous and ordered")
        game_ids = [w.game_id for w in self.window]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("exactly one analysis per game may enter a report window")
        return self
