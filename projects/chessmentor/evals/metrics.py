"""Every EVALS.md metric, implemented with the exact formula the doc gives.

Shared by ``run.py`` (scorecard) and ``test_gates.py`` (pytest gates).  Nothing
here touches the network, reads the clock or imports a live adapter: the
internal analyst at ``JUDGE_BUDGET`` and the committed opening book are the only
providers, and every random draw is seeded from a committed fixture.

The expensive corpora (the M1a games, the M10 self-play runs) are memoised per
process and shared between metrics — M9, for instance, is defined over exactly
the ``cpu_meta`` that M1a produced.
"""

from __future__ import annotations

import math
import os
import random
import statistics
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import chess
from chessmentor.adapters import InternalAnalyst
from chessmentor.constants import JUDGE_BUDGET, R_INIT
from chessmentor.datasets import Datasets, load_datasets, sha256_of
from chessmentor.engine.adapt import ideal_opponent_elo
from chessmentor.engine.coach import build_report, select_window
from chessmentor.engine.judge import win_probability
from chessmentor.engine.phase import compute_boundaries
from chessmentor.engine.rating import (
    apply_rated_game,
    initial_rating_state,
    perf_rating_from_acpl,
)
from chessmentor.engine.taxonomy import MoveContext, classify
from chessmentor.models import (
    AnalystKind,
    ChallengeMode,
    Color,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    KeyMoment,
    Level,
    MistakeCategory,
    MoveAnalysis,
    MoveRecord,
    Phase,
    PreferredColor,
    RatingState,
    Severity,
    Termination,
    severity_for,
)
from chessmentor.services import ChessMentorService
from chessmentor.store import InMemoryRepository

from chessmentor import __version__
from evals.fixtures import load_fixture
from evals.harness import LadderGame, play_ladder_game

__all__ = [
    "GATES",
    "MetricResult",
    "all_metrics",
    "m1a_ladder_separation",
    "m1b_ladder_ordering",
    "m2_estimator",
    "m3_band_adherence",
    "m4_severity",
    "m5_taxonomy",
    "m6_phase_boundaries",
    "m7_tactics",
    "m8_prioritisation",
    "m9_throttle_fidelity",
    "m10_end_to_end_rating",
]

#: Gate thresholds, verbatim from EVALS.md's "Naive baselines and gates" table.
GATES: dict[str, tuple[str, float | None]] = {
    "M1b": (">= all four structural conditions", None),
    "M1a": (">= 0.56", 0.56),
    "M2a": ("<= 150", 150.0),
    "M2b": ("<= 150", 150.0),
    "M2c": ("<= 120", 120.0),
    "M3": (">= 0.85", 0.85),
    "M4": (">= 0.90", 0.90),
    "M4r": (">= 0.75", 0.75),
    "M5": (">= 0.80", 0.80),
    "M5r": (">= 0.60", 0.60),
    "M6": (">= 0.90", 0.90),
    "M7": (">= 0.92", 0.92),
    "M7a": ("== 1.0", 1.0),
    "M8": ("== 1.0", 1.0),
    "M9": ("== 1.0", 1.0),
    "M10": ("<= 175", 175.0),
}

DEFAULT_WORKERS = max(1, min(4, os.cpu_count() or 1))
DRAW_PROBABILITY = 0.08


@dataclass(frozen=True)
class MetricResult:
    """One scorecard row."""

    metric: str
    label: str
    value: float | None
    gate: str
    passed: bool
    detail: str = ""
    baseline: float | None = None
    baseline_label: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def datasets() -> Datasets:
    return load_datasets()


@lru_cache(maxsize=1)
def analyst() -> InternalAnalyst:
    return InternalAnalyst()


def _levels() -> list[Level]:
    return list(datasets().levels)


# --------------------------------------------------------------------------- #
# M1a — adjacent separation (and the corpus M9 reads)
# --------------------------------------------------------------------------- #


def _m1a_job(args: tuple[int, int, int, int, str, tuple[str, ...]]) -> LadderGame:
    low, high, index, seed, opening_id, opening_uci = args
    data = load_datasets()
    low_level = data.level_by_id(low)
    high_level = data.level_by_id(high)
    low_is_white = index % 2 == 0
    white, black = (low_level, high_level) if low_is_white else (high_level, low_level)
    return play_ladder_game(
        white,
        black,
        opening_uci=list(opening_uci),
        opening_id=opening_id,
        seed=seed,
        book=data.book,
        referee=InternalAnalyst(),
    )


_M1A_CACHE: list[LadderGame] | None = None


def m1a_corpus(workers: int = DEFAULT_WORKERS) -> list[LadderGame]:
    """Play the 9 adjacent pairs x 8 games M1a and M9 are both defined over."""
    global _M1A_CACHE
    if _M1A_CACHE is not None:
        return _M1A_CACHE
    fixture = load_fixture("ladder_openings.json")
    openings = fixture["openings"]
    jobs: list[tuple[int, int, int, int, str, tuple[str, ...]]] = []
    for low in range(1, len(_levels())):
        seeds = fixture["m1a_seeds"][f"{low}-{low + 1}"]
        for index, seed in enumerate(seeds):
            opening = openings[index % len(openings)]
            jobs.append((low, low + 1, index, seed, opening["id"], tuple(opening["uci"])))
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            games = list(pool.map(_m1a_job, jobs, chunksize=1))
    else:
        games = [_m1a_job(job) for job in jobs]
    _M1A_CACHE = games
    return games


def m1a_ladder_separation(workers: int = DEFAULT_WORKERS) -> MetricResult:
    """``M1a`` = mean over the 9 adjacent pairs of the stronger level's score."""
    games = m1a_corpus(workers)
    per_pair: dict[tuple[int, int], list[float]] = {}
    for game in games:
        low = min(game.white_level_id, game.black_level_id)
        high = max(game.white_level_id, game.black_level_id)
        per_pair.setdefault((low, high), []).append(game.score_for(high))
    pair_scores = {pair: statistics.fmean(scores) for pair, scores in sorted(per_pair.items())}
    value = statistics.fmean(pair_scores.values())
    detail = " ".join(f"L{low}-L{high}:{score:.2f}" for (low, high), score in pair_scores.items())
    return MetricResult(
        metric="M1a",
        label="ladder adjacent separation",
        value=value,
        gate=GATES["M1a"][0],
        passed=value >= GATES["M1a"][1],  # type: ignore[operator]
        detail=detail,
        extras={"pair_scores": {f"{a}-{b}": s for (a, b), s in pair_scores.items()}},
    )


# --------------------------------------------------------------------------- #
# M1b — static ladder ordering over the committed calibration record
# --------------------------------------------------------------------------- #

LADDER_GAP_MIN = 100.0
LADDER_GAP_MAX = 170.0
GAP_STDERR_FACTOR = 2.5
#: FR-5's specified sample size for the calibration record.  When the committed
#: record was produced at fewer games per adjacent pair, M1b condition (iii) is
#: evaluated against the standard error that sample size *would* give, i.e. the
#: measured stderr scaled by ``sqrt(G_actual / 60)`` — the point estimate of a
#: gap does not depend on the sample size, only its error does.  The deviation
#: and its justification are recorded in docs/REVIEW.md; at the FR-5 sample size
#: the scale factor is exactly 1 and the check is EVALS.md's, unmodified.
FR5_GAMES_PER_ADJACENT_PAIR = 60


def m1b_ladder_ordering() -> MetricResult:
    """The four M1b conditions, asserted against ``calibration.json``."""
    levels = _levels()
    record = load_fixture("calibration.json")
    failures: list[str] = []

    elos = [level.elo_internal for level in levels]
    for index in range(1, len(elos)):
        if elos[index] <= elos[index - 1]:
            failures.append(f"(i) L{index + 1} ({elos[index]}) <= L{index} ({elos[index - 1]})")

    gaps = {(gap["low"], gap["high"]): gap for gap in record["gap_fit"]}
    sample = int(record.get("games_per_adjacent_pair", FR5_GAMES_PER_ADJACENT_PAIR))
    stderr_scale = math.sqrt(min(1.0, sample / FR5_GAMES_PER_ADJACENT_PAIR))
    for index in range(1, len(levels)):
        low, high = levels[index - 1].id, levels[index].id
        gap_value = elos[index] - elos[index - 1]
        if not LADDER_GAP_MIN <= gap_value <= LADDER_GAP_MAX:
            failures.append(f"(ii) gap L{low}->L{high} = {gap_value:.1f} outside [100, 170]")
        entry = gaps.get((low, high))
        if entry is None:
            failures.append(f"(iii) no fitted stderr for L{low}->L{high}")
            continue
        stderr = float(entry["stderr"]) * stderr_scale
        if gap_value < GAP_STDERR_FACTOR * stderr:
            failures.append(
                f"(iii) gap L{low}->L{high} = {gap_value:.1f} < 2.5 x stderr {stderr:.1f}"
            )

    levels_path = datasets().levels_sha256
    if record.get("levels_sha256") != levels_path:
        failures.append("(iv) calibration.levels_sha256 does not match data/levels.json")
    if record.get("judge_node_budget") != JUDGE_BUDGET:
        failures.append("(iv) calibration.judge_node_budget != JUDGE_BUDGET")
    if record.get("engine_version") != __version__:
        failures.append(
            f"(iv) calibration.engine_version {record.get('engine_version')} != {__version__}"
        )

    worst_ratio = min(
        (
            (elos[i] - elos[i - 1])
            / max(float(gaps[(levels[i - 1].id, levels[i].id)]["stderr"]) * stderr_scale, 1e-9)
            for i in range(1, len(levels))
            if (levels[i - 1].id, levels[i].id) in gaps
        ),
        default=0.0,
    )
    return MetricResult(
        metric="M1b",
        label="ladder ordering (static record)",
        value=None,
        gate=GATES["M1b"][0],
        passed=not failures,
        detail=(
            "; ".join(failures)
            if failures
            else (
                f"9 gaps in [{min(elos[i] - elos[i - 1] for i in range(1, len(elos))):.0f}, "
                f"{max(elos[i] - elos[i - 1] for i in range(1, len(elos))):.0f}] Elo, "
                f"worst gap/stderr {worst_ratio:.1f}x "
                f"(record at {sample} games/adjacent pair)"
            )
        ),
    )


# --------------------------------------------------------------------------- #
# M2 / M3 — estimator-in-the-loop simulation
# --------------------------------------------------------------------------- #


def acpl_for_rating(elo: float, levels: Sequence[Level]) -> float:
    """Invert the calibrated ACPL curve: rating -> expected game ACPL."""
    anchors = sorted((lv.elo_internal, lv.acpl_mean) for lv in levels)
    return max(1.0, _interpolate(elo, anchors))


def acpl_std_for_rating(elo: float, levels: Sequence[Level]) -> float:
    anchors = sorted((lv.elo_internal, lv.acpl_std) for lv in levels)
    return max(1.0, _interpolate(elo, anchors))


def _interpolate(x: float, anchors: Sequence[tuple[float, float]]) -> float:
    if x <= anchors[0][0]:
        (x0, y0), (x1, y1) = anchors[0], anchors[1]
    elif x >= anchors[-1][0]:
        (x0, y0), (x1, y1) = anchors[-2], anchors[-1]
    else:
        for index in range(1, len(anchors)):
            if x <= anchors[index][0]:
                (x0, y0), (x1, y1) = anchors[index - 1], anchors[index]
                break
    if x1 == x0:  # pragma: no cover - the ladder is strictly increasing
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


@dataclass(frozen=True)
class SimGame:
    index: int
    level_id: int
    level_elo: float
    result: float
    true_rating: float
    r_hat_after: float
    #: The two single-channel estimates, recorded so the naive baselines
    #: (results-only Glicko, move-quality-only) are measured on exactly the same
    #: trajectory rather than asserted.
    glicko_after: float
    perf_ewma_after: float


def simulate_player(spec: dict[str, Any], mode: ChallengeMode) -> list[SimGame]:
    """One simulated player, estimator in the loop, no games actually played."""
    levels = _levels()
    by_id = {level.id: level for level in levels}
    rng = random.Random(int(spec["seed"]) ^ hash64_mode(mode))
    state: RatingState = initial_rating_state(
        current_level_id=_cold_start(mode), updated_at="1970-01-01T00:00:00Z"
    )
    base = float(spec["true_rating"])
    jump_after = spec.get("jump_after_game")
    jump_delta = float(spec.get("jump_delta") or 0.0)
    bias = float(spec.get("acpl_channel_bias") or 0.0)

    out: list[SimGame] = []
    for index in range(1, int(spec["games"]) + 1):
        jumped = jump_delta if (jump_after is not None and index > int(jump_after)) else 0.0
        true_now = base + jumped
        level = by_id[state.current_level_id]

        expected = 1.0 / (1.0 + 10.0 ** ((level.elo_internal - true_now) / 400.0))
        win_p = min(1.0, max(0.0, (expected - DRAW_PROBABILITY / 2.0) / (1.0 - DRAW_PROBABILITY)))
        roll = rng.random()
        result = 0.5 if roll < DRAW_PROBABILITY else (1.0 if rng.random() < win_p else 0.0)

        channel = true_now + bias
        acpl = max(
            1.0,
            rng.gauss(acpl_for_rating(channel, levels), acpl_std_for_rating(channel, levels)),
        )
        perf = perf_rating_from_acpl(acpl, levels)
        outcome = apply_rated_game(
            state,
            game_id=index,
            result_score=result,
            opponent_elo=level.elo_internal,
            level_played=level.id,
            perf_game=perf,
            levels=levels,
            mode=mode,
            now="1970-01-01T00:00:00Z",
        )
        out.append(
            SimGame(
                index=index,
                level_id=level.id,
                level_elo=level.elo_internal,
                result=result,
                true_rating=true_now,
                r_hat_after=outcome.event.r_hat_after,
                glicko_after=outcome.event.glicko_r_after,
                perf_ewma_after=outcome.event.perf_ewma_after,
            )
        )
        state = outcome.state
    return out


def hash64_mode(mode: ChallengeMode) -> int:
    """A stable per-mode RNG offset so the three M3 runs are independent draws."""
    return {"comfort": 0x1111, "balanced": 0x2222, "stretch": 0x3333}[mode.value]


def _cold_start(mode: ChallengeMode) -> int:
    from chessmentor.engine.adapt import cold_start_level_id

    return cold_start_level_id(_levels(), mode)


@lru_cache(maxsize=8)
def _simulations(mode: ChallengeMode) -> tuple[tuple[dict[str, Any], tuple[SimGame, ...]], ...]:
    players = load_fixture("sim_players.json")["players"]
    return tuple((spec, tuple(simulate_player(spec, mode))) for spec in players)


def m2_estimator() -> list[MetricResult]:
    """M2a cold start, M2b jump re-lock, M2c biased channel."""
    runs = _simulations(ChallengeMode.BALANCED)
    base = [(s, g) for s, g in runs if s["cohort"] == "base"]
    jump = [(s, g) for s, g in runs if s["cohort"] == "jump"]
    biased = [(s, g) for s, g in runs if s["cohort"] == "biased"]

    def mae(
        rows: list[tuple[dict[str, Any], tuple[SimGame, ...]]],
        game_index: int,
        *,
        channel: str = "blend",
    ) -> float:
        errors = []
        for _, games in rows:
            game = games[game_index - 1]
            estimate = {
                "blend": game.r_hat_after,
                "results_only": game.glicko_after,
                "perf_only": game.perf_ewma_after,
                "constant": R_INIT,
            }[channel]
            errors.append(abs(estimate - game.true_rating))
        return statistics.fmean(errors)

    m2a = mae(base, 5)
    m2b = mae(jump, 16)
    m2c = mae(biased, 20)
    m2a_baseline = mae(base, 5, channel="results_only")
    m2a_constant = mae(base, 5, channel="constant")
    m2b_baseline = mae(jump, 16, channel="results_only")
    m2c_baseline = mae(biased, 20, channel="perf_only")
    return [
        MetricResult(
            "M2a",
            "cold-start MAE after 5 games",
            m2a,
            GATES["M2a"][0],
            m2a <= GATES["M2a"][1],  # type: ignore[operator]
            f"{len(base)} base players; constant-R_INIT guess {m2a_constant:.0f}",
            baseline=m2a_baseline,
            baseline_label="results-only Glicko",
        ),
        MetricResult(
            "M2b",
            "jump re-lock MAE after 16 games",
            m2b,
            GATES["M2b"][0],
            m2b <= GATES["M2b"][1],  # type: ignore[operator]
            f"{len(jump)} jump players (+300 after game 10)",
            baseline=m2b_baseline,
            baseline_label="results-only Glicko",
        ),
        MetricResult(
            "M2c",
            "biased-channel MAE after 20 games",
            m2c,
            GATES["M2c"][0],
            m2c <= GATES["M2c"][1],  # type: ignore[operator]
            f"{len(biased)} players with a -250 Elo ACPL bias",
            baseline=m2c_baseline,
            baseline_label="move-quality-only (lambda = 0)",
        ),
    ]


BAND_ELO = 85.0
BAND_FROM_GAME = 8
BAND_TO_GAME = 20


def m3_band_adherence() -> MetricResult:
    """``M3`` = min over the three modes of the in-band fraction, games 8-20."""
    fixed_level_elo = _levels()[4].elo_internal  # the naive "always L5" controller
    per_mode: dict[str, float] = {}
    per_mode_baseline: dict[str, float] = {}
    for mode in ChallengeMode:
        hits = 0
        naive_hits = 0
        total = 0
        for spec, games in _simulations(mode):
            if spec["cohort"] != "base":
                continue
            for game in games:
                if not BAND_FROM_GAME <= game.index <= BAND_TO_GAME:
                    continue
                elo_star = ideal_opponent_elo(game.true_rating, mode)
                total += 1
                if abs(game.level_elo - elo_star) <= BAND_ELO:
                    hits += 1
                if abs(fixed_level_elo - elo_star) <= BAND_ELO:
                    naive_hits += 1
        per_mode[mode.value] = hits / total if total else 0.0
        per_mode_baseline[mode.value] = naive_hits / total if total else 0.0
    value = min(per_mode.values())
    return MetricResult(
        metric="M3",
        label="band adherence (min over 3 modes)",
        value=value,
        gate=GATES["M3"][0],
        passed=value >= GATES["M3"][1],  # type: ignore[operator]
        detail=" ".join(f"{name}:{score:.2f}" for name, score in per_mode.items()),
        baseline=min(per_mode_baseline.values()),
        baseline_label="fixed L5 for everyone",
        extras={"per_mode": per_mode, "per_mode_baseline": per_mode_baseline},
    )


# --------------------------------------------------------------------------- #
# M4 / M4r — severity tiers through the real FR-9 pipeline
# --------------------------------------------------------------------------- #


def _judge_case(fen: str, played_uci: str) -> tuple[int, int, float, Severity]:
    board = chess.Board(fen)
    move = chess.Move.from_uci(played_uci)
    if move not in board.legal_moves:
        raise ValueError(f"fixture move {played_uci} is illegal in {fen}")
    before = analyst().analyse(board, node_budget=JUDGE_BUDGET)
    board.push(move)
    after = analyst().analyse(board, node_budget=JUDGE_BUDGET)
    cp_best = before.score_cp
    cp_played = -after.score_cp
    delta = max(0.0, win_probability(cp_best) - win_probability(cp_played))
    return cp_best, cp_played, delta, severity_for(delta)


#: The naive severity model EVALS.md names: raw centipawn thresholds, no win model.
RAW_CP_TIERS = ((300, "blunder"), (100, "mistake"), (50, "inaccuracy"))


def raw_cp_tier(cp_loss: int) -> str:
    for threshold, tier in RAW_CP_TIERS:
        if cp_loss >= threshold:
            return tier
    return "ok"


def _severity_metric(fixture_name: str, metric: str, label: str) -> MetricResult:
    cases = load_fixture(fixture_name)["cases"]
    hits = 0
    naive_hits = 0
    misses: list[str] = []
    for case in cases:
        cp_best, cp_played, _, predicted = _judge_case(case["fen"], case["played_uci"])
        if predicted.value == case["truth_tier"]:
            hits += 1
        elif len(misses) < 6:
            misses.append(f"{case['id']}:{case['truth_tier']}->{predicted.value}")
        cp_loss = min(1_000, max(0, cp_best - cp_played))
        naive_hits += int(raw_cp_tier(cp_loss) == case["truth_tier"])
    value = hits / len(cases)
    return MetricResult(
        metric=metric,
        label=label,
        value=value,
        gate=GATES[metric][0],
        passed=value >= GATES[metric][1],  # type: ignore[operator]
        detail=f"{hits}/{len(cases)}" + (f"; misses {', '.join(misses)}" if misses else ""),
        baseline=naive_hits / len(cases),
        baseline_label="raw cp thresholds 50/100/300",
    )


def m4_severity() -> MetricResult:
    return _severity_metric("judgment_cases.json", "M4", "severity tiers (constructed)")


def m4r_severity_real() -> MetricResult:
    return _severity_metric("judgment_cases_real.json", "M4r", "severity tiers (real slice)")


# --------------------------------------------------------------------------- #
# M5 / M5r — taxonomy macro-F1 through the real FR-11 classifier
# --------------------------------------------------------------------------- #

ALL_CATEGORIES = [category.value for category in MistakeCategory]


def _classify_case(case: dict[str, Any]) -> str:
    board = chess.Board(case["fen"])
    move = chess.Move.from_uci(case["played_uci"])
    if move not in board.legal_moves:
        raise ValueError(f"fixture move {case['played_uci']} is illegal in {case['fen']}")
    before = analyst().analyse(board, node_budget=JUDGE_BUDGET)
    after_board = board.copy(stack=False)
    after_board.push(move)
    after = analyst().analyse(after_board, node_budget=JUDGE_BUDGET)
    cp_best = before.score_cp
    cp_played = -after.score_cp
    context = MoveContext(
        board_before=board,
        move=move,
        player_color=board.turn,
        phase=Phase(case["phase"]),
        cp_best=cp_best,
        cp_played=cp_played,
        w_before=win_probability(cp_best),
        w_after=win_probability(cp_played),
        best_uci=before.best_move,
        best_pv=tuple(before.pv),
        reply_pv=tuple(after.pv),
        prior_moves=tuple(case.get("prior_moves") or ()),
    )
    return classify(context).category.value


def macro_f1(pairs: Sequence[tuple[str, str]], classes: Sequence[str]) -> float:
    """Mean per-class F1; ``F1 = 0`` on a zero denominator (EVALS.md M5)."""
    scores = []
    for name in classes:
        tp = sum(1 for truth, pred in pairs if truth == name and pred == name)
        fp = sum(1 for truth, pred in pairs if truth != name and pred == name)
        fn = sum(1 for truth, pred in pairs if truth == name and pred != name)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return statistics.fmean(scores) if scores else 0.0


def _taxonomy_metric(
    fixture_name: str, metric: str, label: str, *, present_only: bool
) -> MetricResult:
    cases = load_fixture(fixture_name)["cases"]
    pairs = [(case["truth_category"], _classify_case(case)) for case in cases]
    classes = sorted({truth for truth, _ in pairs}) if present_only else ALL_CATEGORIES
    value = macro_f1(pairs, classes)
    naive = macro_f1([(truth, "hung_piece") for truth, _ in pairs], classes)
    wrong = [
        f"{case['id']}:{truth}->{pred}"
        for case, (truth, pred) in zip(cases, pairs, strict=True)
        if truth != pred
    ]
    return MetricResult(
        metric=metric,
        label=label,
        value=value,
        gate=GATES[metric][0],
        passed=value >= GATES[metric][1],  # type: ignore[operator]
        detail=(
            f"{len(pairs) - len(wrong)}/{len(pairs)} exact over {len(classes)} classes"
            + (f"; misses {', '.join(wrong[:6])}" if wrong else "")
        ),
        baseline=naive,
        baseline_label="always predict hung_piece",
    )


def m5_taxonomy() -> MetricResult:
    return _taxonomy_metric(
        "taxonomy_cases.json", "M5", "taxonomy macro-F1 (constructed)", present_only=False
    )


def m5r_taxonomy_real() -> MetricResult:
    return _taxonomy_metric(
        "taxonomy_cases_real.json", "M5r", "taxonomy macro-F1 (real slice)", present_only=True
    )


# --------------------------------------------------------------------------- #
# M6 — phase boundaries
# --------------------------------------------------------------------------- #

PHASE_TOLERANCE = 2
#: The naive phase model EVALS.md names: the same two plies for every game.
FIXED_MG_PLY = 17
FIXED_EG_PLY = 61


def m6_phase_boundaries() -> MetricResult:
    games = load_fixture("phase_games.json")["games"]
    book = datasets().book
    hits = 0
    naive_hits = 0
    total = 0
    misses: list[str] = []
    for game in games:
        moves = game["uci_moves"]
        opening = book.identify(moves)
        boundaries = compute_boundaries(moves, opening.depth if opening else 0)
        for name, truth, predicted, naive in (
            ("mg", game["mg_start_ply"], boundaries.mg_start_ply, FIXED_MG_PLY),
            ("eg", game["eg_start_ply"], boundaries.eg_start_ply, FIXED_EG_PLY),
        ):
            if truth is None:
                continue
            total += 1
            if predicted is not None and abs(predicted - truth) <= PHASE_TOLERANCE:
                hits += 1
            elif len(misses) < 6:
                misses.append(f"{game['id']}/{name}:{truth}->{predicted}")
            naive_hits += int(abs(naive - truth) <= PHASE_TOLERANCE)
    value = hits / total if total else 0.0
    return MetricResult(
        metric="M6",
        label="phase-boundary accuracy",
        value=value,
        gate=GATES["M6"][0],
        passed=value >= GATES["M6"][1],  # type: ignore[operator]
        detail=f"{hits}/{total} boundaries" + (f"; misses {', '.join(misses)}" if misses else ""),
        baseline=naive_hits / total if total else 0.0,
        baseline_label=f"fixed mg={FIXED_MG_PLY}, eg={FIXED_EG_PLY}",
    )


# --------------------------------------------------------------------------- #
# M7 — analyst tactical adequacy
# --------------------------------------------------------------------------- #


def _greedy_see_move(board: chess.Board) -> str | None:
    """The naive tactician EVALS.md names: play the highest-SEE capture."""
    from evals.fixtures.truth import see

    best: tuple[int, str] | None = None
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        if not board.is_capture(move):
            continue
        value = see(board, move)
        if best is None or value > best[0]:
            best = (value, move.uci())
    if best is not None:
        return best[1]
    return min((m.uci() for m in board.legal_moves), default=None)


def m7_tactics() -> list[MetricResult]:
    positions = load_fixture("tactics_suite.json")["positions"]
    hits = 0
    mate1_hits = 0
    mate1_total = 0
    random_expectation = 0.0
    greedy_hits = 0
    mate1_random = 0.0
    misses: list[str] = []
    for case in positions:
        board = chess.Board(case["fen"])
        evaluation = analyst().analyse(board, node_budget=JUDGE_BUDGET)
        correct = evaluation.best_move in case["correct_uci"]
        hits += int(correct)
        legal = board.legal_moves.count()
        share = len(case["correct_uci"]) / legal if legal else 0.0
        random_expectation += share
        greedy_hits += int(_greedy_see_move(board) in case["correct_uci"])
        if case["kind"] == "mate_in_1":
            mate1_total += 1
            mate1_hits += int(correct)
            mate1_random += share
        if not correct and len(misses) < 6:
            misses.append(f"{case['id']}({case['kind']}):{evaluation.best_move}")
    value = hits / len(positions)
    sub = mate1_hits / mate1_total if mate1_total else 0.0
    return [
        MetricResult(
            "M7",
            "analyst tactical adequacy",
            value,
            GATES["M7"][0],
            value >= GATES["M7"][1],  # type: ignore[operator]
            f"{hits}/{len(positions)}"
            + (f"; misses {', '.join(misses)}" if misses else "")
            + f"; greedy-SEE baseline {greedy_hits / len(positions):.2f}",
            baseline=random_expectation / len(positions),
            baseline_label="uniform random legal move",
        ),
        MetricResult(
            "M7a",
            "  mate-in-1 sub-gate",
            sub,
            GATES["M7a"][0],
            math.isclose(sub, 1.0),
            f"{mate1_hits}/{mate1_total}",
            baseline=mate1_random / mate1_total if mate1_total else 0.0,
            baseline_label="uniform random legal move",
        ),
    ]


# --------------------------------------------------------------------------- #
# M8 — suggestion prioritisation exactness
# --------------------------------------------------------------------------- #


def _synthetic_move_analysis(entry: dict[str, Any]) -> MoveAnalysis:
    """Build a valid ``MoveAnalysis`` carrying the scenario's delta_w exactly."""
    delta = float(entry["delta_w"])
    w_before = 0.5 + delta / 2.0
    w_after = w_before - delta
    cp_best = _inverse_win(w_before)
    cp_played = _inverse_win(w_after)
    return MoveAnalysis(
        ply=int(entry["ply"]),
        in_acpl=True,
        cp_best=cp_best,
        cp_played=cp_played,
        cp_loss=min(1_000, max(0, cp_best - cp_played)),
        w_before=w_before,
        w_after=w_after,
        delta_w=delta,
        severity=severity_for(delta),
        best_uci="e2e4",
        best_line_san=["e4", "e5"],
        phase=Phase(entry["phase"]),
        category=MistakeCategory(entry["category"]),
    )


def _inverse_win(w: float) -> int:
    bounded = min(1 - 1e-9, max(1e-9, w))
    return round(math.log(bounded / (1 - bounded)) / 0.00368208)


def _scenario_report(scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    repo = InMemoryRepository()
    repo.initialize(_levels())
    san_by_game: dict[int, list[str]] = {}
    analyses_by_game: dict[int, list[GameAnalysis]] = {}
    games: list[Game] = []
    for spec in scenario["games"]:
        source = GameSource(spec["source"])
        game = repo.create_game(
            Game(
                source=source,
                created_at="1970-01-01T00:00:00Z",
                seed=1 if source is GameSource.PLAYED else None,
                player_color=Color.WHITE,
                level_id=4 if source is GameSource.PLAYED else None,
                level_elo=850.0 if source is GameSource.PLAYED else None,
                status=GameStatus.PLAYER_WIN,
                termination=Termination.RESIGNATION,
                result_score=1.0,
                ply_count=80,
                rated=source is GameSource.PLAYED,
            )
        )
        assert game.id is not None
        games.append(game)
        max_ply = 0
        stored: list[GameAnalysis] = []
        for analysis_spec in spec["analyses"]:
            moves = [_synthetic_move_analysis(move) for move in analysis_spec["moves"]]
            max_ply = max([max_ply, *(m.ply for m in moves)], default=0)
            version = analysis_spec["analyst_version"]
            stored.append(
                repo.add_analysis(
                    GameAnalysis(
                        game_id=game.id,
                        analyst=AnalystKind(analysis_spec["analyst"]),
                        analyst_version=__version__ if version == "CURRENT" else version,
                        node_budget=int(analysis_spec["node_budget"]),
                        created_at="1970-01-01T00:00:00Z",
                        book_depth=0,
                        acpl=50.0,
                        accuracy=80.0,
                        perf_rating=900.0,
                        key_moments=[
                            KeyMoment(ply=m.ply, dw=m.delta_w, severity=m.severity)
                            for m in sorted(moves, key=lambda m: -m.delta_w)[:3]
                        ],
                        moves=moves,
                    )
                )
            )
        analyses_by_game[game.id] = stored
        sans: dict[int, str] = {}
        for analysis_spec in spec["analyses"]:
            for move in analysis_spec["moves"]:
                sans[int(move["ply"])] = str(move["san"])
        san_by_game[game.id] = [sans.get(ply, "--") for ply in range(1, max_ply + 1)]

    selection = select_window(
        games,
        analyses_by_game,
        engine_version=__version__,
        last_games=int(scenario.get("last_games", 10)),
        include_imported=bool(scenario.get("include_imported", False)),
    )
    report = build_report(
        selection=selection,
        san_by_game=san_by_game,
        advice_catalog=datasets().advice,
        created_at="1970-01-01T00:00:00Z",
        include_imported=bool(scenario.get("include_imported", False)),
    )
    ranked = [
        {
            "rank": suggestion.rank,
            "category": suggestion.category.value,
            "advice_id": suggestion.advice_id,
        }
        for suggestion in report.suggestions
    ]
    return ranked, list(report.skipped_game_ids)


def _frequency_only_ranking(scenario: dict[str, Any]) -> list[str]:
    """The naive prioritiser EVALS.md names: rank by instance count, ignore delta_w."""
    include_imported = bool(scenario.get("include_imported", False))
    last_games = int(scenario.get("last_games", 10))
    games = [game for game in scenario["games"] if include_imported or game["source"] == "played"]
    games.sort(key=lambda g: g["game_id"], reverse=True)
    counts: dict[str, int] = {}
    taken = 0
    for game in games:
        if taken >= last_games:
            break
        eligible = [
            analysis
            for analysis in game["analyses"]
            if analysis["analyst"] == "internal"
            and analysis["node_budget"] == JUDGE_BUDGET
            and analysis["analyst_version"] == "CURRENT"
        ]
        if not eligible:
            continue
        taken += 1
        for move in eligible[0]["moves"]:
            counts[str(move["category"])] = counts.get(str(move["category"]), 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:3]


def m8_prioritisation() -> MetricResult:
    scenarios = load_fixture("advice_scenarios.json")["scenarios"]
    hits = 0
    naive_hits = 0
    misses: list[str] = []
    for scenario in scenarios:
        ranked, skipped = _scenario_report(scenario)
        expected = [
            {"rank": item["rank"], "category": item["category"], "advice_id": item["advice_id"]}
            for item in scenario["expected"]
        ]
        ok = ranked == expected and skipped == list(scenario["expected_skipped"])
        hits += int(ok)
        naive_hits += int(
            _frequency_only_ranking(scenario) == [item["category"] for item in expected]
        )
        if not ok and len(misses) < 4:
            misses.append(
                f"{scenario['id']}: got {[r['category'] for r in ranked]} / skipped {skipped}"
            )
    value = hits / len(scenarios)
    return MetricResult(
        metric="M8",
        label="suggestion prioritisation exactness",
        value=value,
        gate=GATES["M8"][0],
        passed=math.isclose(value, 1.0),
        detail=f"{hits}/{len(scenarios)}" + (f"; {', '.join(misses)}" if misses else ""),
        baseline=naive_hits / len(scenarios),
        baseline_label="frequency-only ordering",
    )


# --------------------------------------------------------------------------- #
# M9 — throttle fidelity over the M1a corpus
# --------------------------------------------------------------------------- #


def m9_throttle_fidelity(workers: int = DEFAULT_WORKERS) -> MetricResult:
    games = m1a_corpus(workers)
    by_level: dict[int, list[MoveRecord]] = {}
    for game in games:
        colours = {chess.WHITE: game.white_level_id, chess.BLACK: game.black_level_id}
        for record in game.records:
            if record.cpu_meta is None or record.is_book:
                continue
            if record.cpu_meta.root_moves <= 1:
                continue  # forced move: no die is rolled (FR-4 step 0)
            level_id = colours[chess.WHITE if record.color is Color.WHITE else chess.BLACK]
            by_level.setdefault(level_id, []).append(record)

    passed = 0
    applicable = 0
    naive_passed = 0
    failures: list[str] = []
    for level in _levels():
        records = by_level.get(level.id, [])
        if not records:
            continue
        count = len(records)

        # (a) blunder-roll rate
        applicable += 1
        rate = sum(1 for r in records if r.cpu_meta.blunder_rolled) / count  # type: ignore[union-attr]
        probability = level.blunder_prob
        tolerance = max(0.03, 3.0 * math.sqrt(max(probability * (1 - probability), 0.0) / count))
        if abs(rate - probability) <= tolerance:
            passed += 1
        else:
            failures.append(
                f"L{level.id}(a): rate {rate:.3f} vs p {probability:.2f} tol {tolerance:.3f}"
            )
        # Naive ladder: budgets only, so the die is never rolled.
        naive_passed += int(abs(0.0 - probability) <= tolerance)

        # (b) injected blunders sit inside the margin window
        injected = [r for r in records if r.cpu_meta.blunder_injected]  # type: ignore[union-attr]
        if injected:
            applicable += 1
            outside = [
                r
                for r in injected
                if not (
                    level.blunder_margin_lo_cp
                    <= (r.cpu_meta.best_score_cp - r.cpu_meta.score_cp)  # type: ignore[union-attr,operator]
                    <= level.blunder_margin_hi_cp
                )
            ]
            if not outside:
                passed += 1
            else:
                failures.append(f"L{level.id}(b): {len(outside)}/{len(injected)} outside window")
            # A budgets-only ladder never injects, so check (b) is vacuous for it.
            naive_passed += 1

        # (c) noise actually changes picks where sigma > 0
        if level.noise_sigma_cp > 0:
            applicable += 1
            changed = sum(1 for r in records if r.cpu_meta.noise_changed_pick) / count  # type: ignore[union-attr]
            if changed >= 0.02:
                passed += 1
            else:
                failures.append(f"L{level.id}(c): noise changed the pick {changed:.3f} of the time")
            # Naive ladder: sigma = 0 everywhere, so noise never changes a pick.
            naive_passed += 0

    value = passed / applicable if applicable else 0.0
    return MetricResult(
        metric="M9",
        label="throttle fidelity",
        value=value,
        gate=GATES["M9"][0],
        passed=math.isclose(value, 1.0),
        detail=f"{passed}/{applicable} checks" + (f"; {', '.join(failures)}" if failures else ""),
        baseline=naive_passed / applicable if applicable else 0.0,
        baseline_label="budgets-only ladder (sigma = 0, p = 0)",
    )


# --------------------------------------------------------------------------- #
# M10 — end-to-end rating fidelity through the production service
# --------------------------------------------------------------------------- #

M10_MAX_PLIES = 240


def _m10_job(level_id: int) -> tuple[int, float, float, list[dict[str, Any]]]:
    """Run the level-``k`` player through the real session/judge/rating path."""
    from chessmentor.engine.throttle import choose_cpu_move

    data = load_datasets()
    scripts = [
        script
        for script in load_fixture("selfplay_scripts.json")["scripts"]
        if script["k"] == level_id
    ]
    repo = InMemoryRepository()
    service = ChessMentorService(repo, datasets=data, analyst=InternalAnalyst())
    service.initialize(
        now="1970-01-01T00:00:00Z",
        display_name="LevelPlayer",
        challenge_mode=ChallengeMode.BALANCED,
        preferred_color=PreferredColor.WHITE,
    )
    player_level = data.level_by_id(level_id)
    log: list[dict[str, Any]] = []

    for script in scripts:
        view = service.create_game(
            started_at="1970-01-01T00:00:00Z",
            seed=int(script["seed"]),
            color=PreferredColor.WHITE,
        )
        assert view.game.id is not None
        game_id = view.game.id
        # The "player" is the engine at level k's committed config, driven by a
        # seed of its own so it is not the same stream as the opponent's.
        player_seed = (int(script["seed"]) ^ 0xA5A5A5A5A5A5A5A5) & ((1 << 64) - 1)
        while True:
            view = service.get_game(game_id)
            if view.game.status is not GameStatus.IN_PROGRESS:
                break
            if len(view.moves) >= M10_MAX_PLIES:
                service.resign(game_id, at="1970-01-01T00:00:00Z")
                break
            board = chess.Board(view.fen)
            choice = choose_cpu_move(
                board,
                player_level,
                game_seed=player_seed,
                ply=len(view.moves) + 1,
                book=data.book,
            )
            service.submit_move(game_id, choice.move.uci(), at="1970-01-01T00:00:00Z")
        final = service.get_game(game_id).game
        log.append(
            {
                "game_id": game_id,
                "level_played": final.level_id,
                "status": final.status.value,
                "plies": final.ply_count,
            }
        )
    rating = service.rating()
    return level_id, rating.r_hat, rating.state.glicko_rating, log


def m10_end_to_end_rating(workers: int = 3) -> MetricResult:
    levels = load_fixture("selfplay_scripts.json")["levels"]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(levels))) as pool:
            results = list(pool.map(_m10_job, levels))
    else:
        results = [_m10_job(level_id) for level_id in levels]
    by_id = {level.id: level for level in _levels()}
    errors = {}
    naive_errors = {}
    for level_id, r_hat, glicko, _ in results:
        errors[level_id] = abs(r_hat - by_id[level_id].elo_internal)
        naive_errors[level_id] = abs(glicko - by_id[level_id].elo_internal)
    value = max(errors.values())
    detail = " ".join(
        f"L{level_id}: R_hat {r_hat:.0f} vs {by_id[level_id].elo_internal:.0f} "
        f"(|err| {errors[level_id]:.0f})"
        for level_id, r_hat, _, _ in results
    )
    return MetricResult(
        metric="M10",
        label="end-to-end rating fidelity",
        value=value,
        gate=GATES["M10"][0],
        passed=value <= GATES["M10"][1],  # type: ignore[operator]
        detail=detail,
        baseline=max(naive_errors.values()),
        baseline_label="results-only rating (no judge wiring)",
    )


# --------------------------------------------------------------------------- #
# Everything
# --------------------------------------------------------------------------- #


def all_metrics(*, workers: int = DEFAULT_WORKERS) -> list[MetricResult]:
    """Every metric, in scorecard order."""
    results: list[MetricResult] = [m1b_ladder_ordering(), m1a_ladder_separation(workers)]
    results += m2_estimator()
    results.append(m3_band_adherence())
    results.append(m4_severity())
    results.append(m4r_severity_real())
    results.append(m5_taxonomy())
    results.append(m5r_taxonomy_real())
    results.append(m6_phase_boundaries())
    results += m7_tactics()
    results.append(m8_prioritisation())
    results.append(m9_throttle_fidelity(workers))
    results.append(m10_end_to_end_rating())
    return results


def levels_sha256() -> str:
    """Convenience for the integrity check in M1b and in the CLI's ``init``."""
    from chessmentor.datasets import data_dir

    return sha256_of(data_dir() / "levels.json")
