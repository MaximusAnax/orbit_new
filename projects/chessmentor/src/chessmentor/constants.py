"""Named constants (SCOPE.md "Named constants").

These names are used verbatim in the docs, in the engine and in the eval suite.
Changing one is a fixture-regenerating, baseline-re-deriving change.
"""

from __future__ import annotations

from typing import Final

#: The one analyst budget used by the automatic post-game judge pass, by FR-5's
#: ACPL calibration, by M1 adjudication and by M4/M4r/M5/M5r/M7/M10.
JUDGE_BUDGET: Final[int] = 6_000

#: Default budget for on-demand re-analysis (``analyze --nodes``); never used by evals.
DEEP_BUDGET: Final[int] = 20_000

#: Win-probability model slope: ``w(cp) = 1/(1+e^(-WIN_K*cp))`` with range [0, 1].
WIN_K: Final[float] = 0.00368208

#: Severity thresholds on ``delta_w`` (Lichess judgment drops on the [0, 1] scale).
SEV_INACCURACY: Final[float] = 0.05
SEV_MISTAKE: Final[float] = 0.10
SEV_BLUNDER: Final[float] = 0.15

#: Per-move centipawn-loss cap applied before averaging.
CP_LOSS_CAP: Final[int] = 1_000

# --- Glicko-1 constants (FR-7a) ---------------------------------------------
R_INIT: Final[float] = 800.0
RD_INIT: Final[float] = 350.0
RD_FLOOR: Final[float] = 60.0
OPP_RD: Final[float] = 30.0

# --- Regime-change detector (FR-7a) -----------------------------------------
SURPRISE_WINDOW: Final[int] = 4
SURPRISE_THRESHOLD: Final[float] = 1.5
RD_INFLATE_TO: Final[float] = 150.0

# --- Move-quality channel (FR-7b/c) -----------------------------------------
EWMA_ALPHA: Final[float] = 0.35
#: SCOPE's D7 recipe with *measured* calibration inputs (docs/REVIEW.md B7):
#: the FR-5 record's smoothed anchors give a per-game performance-rating s.d.
#: of 325 Elo (mean over levels of acpl_std_k x local Elo-per-cp slope), not
#: the ~130 the pre-calibration sizing assumed.  PERF_SIGMA_1 is that per-game
#: s.d.; PERF_SIGMA = 325 * sqrt(EWMA_ALPHA/(2-EWMA_ALPHA)) * 1.5 = 224.5,
#: exactly D7's steady-state-EWMA-times-1.5 construction, rounded to 225.
PERF_SIGMA_1: Final[float] = 325.0
PERF_SIGMA: Final[float] = 225.0
PERF_CLAMP: Final[tuple[float, float]] = (200.0, 2100.0)

# --- Channel-disagreement warning (FR-7d) -----------------------------------
DIVERGENCE_ELO: Final[float] = 250.0
DIVERGENCE_STREAK: Final[int] = 5

# --- Adaptive controller (FR-8) ---------------------------------------------
#: Target expected score per challenge mode.
MODE_TARGETS: Final[dict[str, float]] = {
    "comfort": 0.60,
    "balanced": 0.50,
    "stretch": 0.42,
}
#: Hysteresis half-width on the expected score before the controller moves.
LEVEL_HYSTERESIS: Final[float] = 0.05
#: Maximum level steps between games (placement phase / afterwards).
PLACEMENT_GAMES: Final[int] = 4
PLACEMENT_MAX_STEP: Final[int] = 2
NORMAL_MAX_STEP: Final[int] = 1

# --- Search (FR-2) ----------------------------------------------------------
#: Mate scores are encoded as +/-(MATE_SCORE - ply) so shorter mates win.
MATE_SCORE: Final[int] = 32_000
#: Any |score| above this is a mate score.
MATE_THRESHOLD: Final[int] = MATE_SCORE - 1_000
#: Fixed transposition-table size (entries), depth-preferred replacement.
TT_SIZE: Final[int] = 1 << 16
#: Maximum search ply depth (safety bound for killer/PV tables).
MAX_PLY: Final[int] = 64

# --- Book / phase (FR-9, FR-10) ---------------------------------------------
#: ``OpeningBook.identify`` never reports a book depth deeper than this.
MAX_BOOK_DEPTH: Final[int] = 12
#: FR-10 middlegame rule: fullmove number that forces the middlegame boundary.
MIDDLEGAME_FULLMOVE: Final[int] = 10
#: FR-10 middlegame rule: minimum developed minors per side.
MIDDLEGAME_MIN_DEVELOPED_MINORS: Final[int] = 2
#: FR-10 endgame rule: max non-pawn, non-king pieces on the board (Lichess Divider).
ENDGAME_MAX_PIECES: Final[int] = 6

# --- Game lifecycle (FR-6) --------------------------------------------------
#: Games shorter than this may be aborted and are never rated.
MIN_RATED_PLIES: Final[int] = 8

# --- Coaching (FR-12) -------------------------------------------------------
DEFAULT_REPORT_WINDOW: Final[int] = 10
MAX_SUGGESTIONS: Final[int] = 3
MAX_EVIDENCE_PER_SUGGESTION: Final[int] = 3
KEY_MOMENTS: Final[int] = 3

# --- Taxonomy thresholds (FR-11) --------------------------------------------
BAD_TRADE_SEE_CP: Final[int] = -100
HUNG_PIECE_SEE_CP: Final[int] = 100
MISSED_TACTIC_CP: Final[int] = 200
MISSED_TACTIC_MATERIAL_CP: Final[int] = 200
FORK_MIN_PIECE_VALUE: Final[int] = 300
SPOILED_WIN_FROM: Final[float] = 0.70
SPOILED_WIN_TO: Final[float] = 0.55
SPOILED_DRAW_LO: Final[float] = 0.45
SPOILED_DRAW_HI: Final[float] = 0.55
SPOILED_DRAW_TO: Final[float] = 0.30

#: Golden-ratio odd constant used to derive a per-ply RNG substream (FR-4).
PLY_SEED_MULTIPLIER: Final[int] = 0x9E3779B97F4A7C15
UINT64_MASK: Final[int] = (1 << 64) - 1
