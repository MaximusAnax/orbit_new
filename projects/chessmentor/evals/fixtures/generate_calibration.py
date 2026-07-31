"""FR-5 ladder calibration — the one long-running committed generator.

Plays a round of matches across the ladder at each level's *exact runtime
config*, fits an internal Elo scale anchored at ``L1 == 400``, measures each
level's ACPL against the internal analyst at ``JUDGE_BUDGET`` (book plies
excluded under exactly the FR-9 rule) and writes:

* ``data/levels.json``      — ``elo_internal``, ``acpl_mean``, ``acpl_std``
  (with ``--update-levels``)
* ``evals/fixtures/calibration.json`` — the full record M1b gates.

Per-game seeds are ``hash64(calibration_seed, level_i, level_j, game_index)``,
so the record is identical for any worker count: the script is embarrassingly
parallel and runs offline before a release, never in CI.

Usage::

    uv run python chessmentor/evals/fixtures/generate_calibration.py \
        --games-adjacent 60 --games-skip 24 --workers 4 --update-levels

The fit (specified in FR-5 / EVALS.md M1, so the generator and ``metrics.py``
agree):

* pair scores are smoothed ``S' = (points + 0.5) / (G + 1)`` before fitting —
  a sweep would otherwise drive the fitted delta to infinity;
* the per-pair Elo delta ``d = 400 * log10(S'/(1-S'))`` is clamped to +/-600;
* ratings are the weighted least-squares solution of ``r_j - r_i = d_ij`` with
  ``r_1`` pinned to 400.  The weight of an observation is the inverse of its
  sampling variance ``var(d) = (400/ln10)^2 / (G * S'(1-S'))``, which is the
  same arithmetic EVALS.md quotes when it says a 60-game pair at ``E ~ 0.703``
  has a gap standard error of ~49 Elo.
* per-level and per-adjacent-gap standard errors come from the fit covariance
  ``(X^T W X)^-1``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chessmentor import __version__
from chessmentor.adapters import CommittedBook, InternalAnalyst
from chessmentor.constants import JUDGE_BUDGET
from chessmentor.datasets import load_datasets
from chessmentor.engine.judge import judge_game
from chessmentor.models import Color, Level
from evals.fixtures import FIXTURE_DIR, hash64, load_fixture
from evals.harness import play_ladder_game

#: Committed calibration seed (fixture-seed policy: not tunable).
CALIBRATION_SEED = 20260731
#: FR-5 sample sizes.
GAMES_PER_ADJACENT_PAIR = 60
GAMES_PER_SKIP_PAIR = 24
#: Games judged per level for the ACPL anchors.
ACPL_GAMES_PER_LEVEL = 12
#: Elo scale constant: 400/ln(10).
ELO_SCALE = 400.0 / math.log(10.0)
#: FR-5 clamp on a per-pair fitted delta.
DELTA_CLAMP = 600.0
#: The internal scale is anchored here by definition (SCOPE D5).
ANCHOR_LEVEL_ID = 1
ANCHOR_ELO = 400.0

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class GameKey:
    low: int
    high: int
    index: int


def pair_list(n_levels: int, *, skip: int) -> list[tuple[int, int]]:
    """Adjacent (``skip=1``) or skip-one (``skip=2``) level pairs."""
    return [(i, i + skip) for i in range(1, n_levels + 1 - skip)]


def game_seed(calibration_seed: int, low: int, high: int, index: int) -> int:
    """FR-5: ``hash64(calibration_seed, level_i, level_j, game_index)``."""
    return hash64(calibration_seed, low, high, index)


def _play_one(args: tuple[int, int, int, int]) -> dict[str, object]:
    """Worker entry point: play one calibration game and return a plain dict."""
    low, high, index, calibration_seed = args
    data = load_datasets()
    openings = load_fixture("ladder_openings.json")["openings"]
    opening = openings[index % len(openings)]
    referee = InternalAnalyst()
    low_level = data.level_by_id(low)
    high_level = data.level_by_id(high)
    # Even indices give the lower level White, so colours are exactly balanced.
    low_is_white = index % 2 == 0
    white, black = (low_level, high_level) if low_is_white else (high_level, low_level)
    game = play_ladder_game(
        white,
        black,
        opening_uci=opening["uci"],
        opening_id=opening["id"],
        seed=game_seed(calibration_seed, low, high, index),
        book=data.book,
        referee=referee,
    )
    return {
        "low": low,
        "high": high,
        "index": index,
        "low_is_white": low_is_white,
        "high_score": game.score_for(high),
        "plies": game.plies,
        "termination": game.termination,
        "uci_moves": list(game.uci_moves),
    }


def _judge_one(args: tuple[int, list[str], bool, int]) -> dict[str, object]:
    """Worker entry point: judge one game from one level's point of view."""
    level_id, uci_moves, is_white, _ = args
    data = load_datasets()
    analyst = InternalAnalyst()
    book: CommittedBook = data.book
    opening = book.identify(uci_moves)
    analysis = judge_game(
        uci_moves=uci_moves,
        player_color=Color.WHITE if is_white else Color.BLACK,
        book_depth=opening.depth if opening else 0,
        analyst=analyst,
        levels=data.levels,
        created_at="1970-01-01T00:00:00Z",
        node_budget=JUDGE_BUDGET,
    )
    included = [m for m in analysis.moves if m.in_acpl]
    return {
        "level_id": level_id,
        "acpl": analysis.acpl,
        "n_moves": len(included),
    }


def fit_elo(
    observations: list[tuple[int, int, float, int]], level_ids: list[int]
) -> tuple[dict[int, float], np.ndarray, list[int]]:
    """Weighted least squares fit of ``r_j - r_i = d_ij`` anchored at L1.

    ``observations`` are ``(low, high, smoothed_score_of_high, games)`` tuples.
    Returns the fitted ratings, the covariance matrix of the *free* parameters
    and the free-parameter level ids (the anchor is excluded — it has no error).
    """
    free = [lid for lid in level_ids if lid != ANCHOR_LEVEL_ID]
    index_of = {lid: k for k, lid in enumerate(free)}

    rows: list[list[float]] = []
    rhs: list[float] = []
    weights: list[float] = []
    for low, high, score, games in observations:
        clipped = min(max(score, 1e-6), 1 - 1e-6)
        delta = ELO_SCALE * math.log(clipped / (1.0 - clipped))
        delta = max(-DELTA_CLAMP, min(DELTA_CLAMP, delta))
        variance = (ELO_SCALE**2) / (games * clipped * (1.0 - clipped))
        row = [0.0] * len(free)
        target = delta
        if high == ANCHOR_LEVEL_ID:
            target -= ANCHOR_ELO
        else:
            row[index_of[high]] += 1.0
        if low == ANCHOR_LEVEL_ID:
            target += ANCHOR_ELO
        else:
            row[index_of[low]] -= 1.0
        rows.append(row)
        rhs.append(target)
        weights.append(1.0 / variance)

    design = np.array(rows, dtype=float)
    target_vector = np.array(rhs, dtype=float)
    weight_matrix = np.diag(np.array(weights, dtype=float))
    normal = design.T @ weight_matrix @ design
    covariance = np.linalg.inv(normal)
    solution = covariance @ design.T @ weight_matrix @ target_vector

    ratings = {ANCHOR_LEVEL_ID: ANCHOR_ELO}
    for lid, value in zip(free, solution, strict=True):
        ratings[lid] = float(value)
    return ratings, covariance, free


def gap_stderr(covariance: np.ndarray, free: list[int], low: int, high: int) -> float:
    """Standard error of ``r_high - r_low`` from the fit covariance."""
    index_of = {lid: k for k, lid in enumerate(free)}
    vector = np.zeros(len(free))
    if high in index_of:
        vector[index_of[high]] += 1.0
    if low in index_of:
        vector[index_of[low]] -= 1.0
    variance = float(vector @ covariance @ vector)
    return math.sqrt(max(variance, 0.0))


def fit_gap_model(
    observations: list[tuple[int, int, float, int]], level_ids: list[int]
) -> tuple[dict[int, float], list[dict[str, float]], list[dict[str, float]], list[dict[str, float]]]:
    """Parametric ladder fit: ``gap_k = a + b*k + c*k^2`` by the same WLS.

    The free per-level fit (``fit_elo``) estimates nine independent gaps from
    seventeen match observations, which leaves each gap with a sampling error
    of ~50-80 Elo at practical sample sizes — far wider than M1b's [100, 170]
    committed-gap window (see docs/REVIEW.md, build-stage finding B2).  The
    ladder's strength is *designed* to move smoothly with the one knob that
    drives it (log2 node budget), so the committed curve is this 3-parameter
    model: the same weighted-least-squares objective, the same smoothed and
    clamped per-pair deltas, with the gap sequence constrained to a quadratic
    in the rung index.  The free fit and per-pair residuals are kept in the
    record as the collapse diagnostic.

    Returns ``(ratings, gap_model, level_model, residuals)``.
    """
    basis = {k: np.array([1.0, float(k), float(k) ** 2]) for k in range(1, len(level_ids))}
    rows: list[np.ndarray] = []
    rhs: list[float] = []
    weights: list[float] = []
    metadata: list[tuple[int, int, float]] = []
    for low, high, score, games in observations:
        clipped = min(max(score, 1e-6), 1 - 1e-6)
        delta = ELO_SCALE * math.log(clipped / (1.0 - clipped))
        delta = max(-DELTA_CLAMP, min(DELTA_CLAMP, delta))
        variance = (ELO_SCALE**2) / (games * clipped * (1.0 - clipped))
        rows.append(np.sum([basis[k] for k in range(low, high)], axis=0))
        rhs.append(delta)
        weights.append(1.0 / variance)
        metadata.append((low, high, math.sqrt(variance)))

    design = np.array(rows, dtype=float)
    target = np.array(rhs, dtype=float)
    weight_matrix = np.diag(np.array(weights, dtype=float))
    covariance = np.linalg.inv(design.T @ weight_matrix @ design)
    params = covariance @ design.T @ weight_matrix @ target

    ratings: dict[int, float] = {ANCHOR_LEVEL_ID: ANCHOR_ELO}
    gap_model: list[dict[str, float]] = []
    level_model: list[dict[str, float]] = []
    accumulated = np.zeros(3)
    for k in range(1, len(level_ids)):
        vector = basis[k]
        gap_value = float(vector @ params)
        gap_err = math.sqrt(max(float(vector @ covariance @ vector), 0.0))
        gap_model.append({"low": level_ids[k - 1], "high": level_ids[k], "gap": gap_value,
                          "stderr": gap_err})
        accumulated = accumulated + vector
        ratings[level_ids[k]] = ANCHOR_ELO + float(accumulated @ params)
        level_err = math.sqrt(max(float(accumulated @ covariance @ accumulated), 0.0))
        level_model.append({"level_id": level_ids[k], "elo_internal": ratings[level_ids[k]],
                            "stderr": level_err})

    residuals: list[dict[str, float]] = []
    fitted = design @ params
    for (low, high, sigma), observed, modeled in zip(metadata, rhs, fitted, strict=True):
        residuals.append(
            {
                "low": low,
                "high": high,
                "observed_delta": observed,
                "model_delta": float(modeled),
                "sigma": sigma,
                "z": (observed - float(modeled)) / sigma if sigma > 0 else 0.0,
            }
        )
    return ratings, gap_model, level_model, residuals


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    *,
    calibration_seed: int,
    games_adjacent: int,
    games_skip: int,
    acpl_games: int,
    workers: int,
    update_levels: bool,
    generated_at: str,
) -> dict[str, object]:
    data = load_datasets()
    levels: list[Level] = list(data.levels)
    level_ids = [lv.id for lv in levels]

    jobs: list[tuple[int, int, int, int]] = []
    for low, high in pair_list(len(levels), skip=1):
        jobs += [(low, high, index, calibration_seed) for index in range(games_adjacent)]
    for low, high in pair_list(len(levels), skip=2):
        jobs += [(low, high, index, calibration_seed) for index in range(games_skip)]

    started = time.perf_counter()
    print(f"calibration: {len(jobs)} games on {workers} worker(s)", flush=True)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_play_one, jobs, chunksize=1))
    else:
        results = [_play_one(job) for job in jobs]
    print(f"  matches done in {time.perf_counter() - started:.0f}s", flush=True)

    # --- pair aggregation -------------------------------------------------- #
    by_pair: dict[tuple[int, int], list[dict[str, object]]] = {}
    for result in results:
        by_pair.setdefault((int(result["low"]), int(result["high"])), []).append(result)

    matches: list[dict[str, object]] = []
    observations: list[tuple[int, int, float, int]] = []
    for (low, high), games in sorted(by_pair.items()):
        games.sort(key=lambda g: int(g["index"]))
        points = math.fsum(float(g["high_score"]) for g in games)
        count = len(games)
        smoothed = (points + 0.5) / (count + 1)
        matches.append(
            {
                "low": low,
                "high": high,
                "games": count,
                "points_high": points,
                "smoothed_score_high": smoothed,
                "terminations": [str(g["termination"]) for g in games],
                "plies": [int(g["plies"]) for g in games],
            }
        )
        observations.append((low, high, smoothed, count))

    ratings, covariance, free = fit_elo(observations, level_ids)

    index_of = {lid: k for k, lid in enumerate(free)}
    elo_fit = []
    for lid in level_ids:
        if lid == ANCHOR_LEVEL_ID:
            stderr = 0.0
        else:
            stderr = math.sqrt(max(float(covariance[index_of[lid], index_of[lid]]), 0.0))
        elo_fit.append({"level_id": lid, "elo_internal": ratings[lid], "stderr": stderr})

    gap_fit = []
    for low, high in pair_list(len(levels), skip=1):
        gap_fit.append(
            {
                "low": low,
                "high": high,
                "gap": ratings[high] - ratings[low],
                "stderr": gap_stderr(covariance, free, low, high),
            }
        )

    # --- ACPL anchors ------------------------------------------------------- #
    judge_jobs: list[tuple[int, list[str], bool, int]] = []
    for lid in level_ids:
        played = [
            result
            for result in results
            if lid in (int(result["low"]), int(result["high"])) and int(result["plies"]) >= 12
        ]
        played.sort(key=lambda g: (int(g["low"]), int(g["high"]), int(g["index"])))
        # Deterministic spread over the level's games rather than the first N.
        if not played:  # pragma: no cover - only if a pair produced no games
            continue
        step = max(1, len(played) // acpl_games)
        sample = played[::step][:acpl_games]
        for result in sample:
            is_low = lid == int(result["low"])
            is_white = bool(result["low_is_white"]) == is_low
            judge_jobs.append((lid, list(result["uci_moves"]), is_white, 0))

    started = time.perf_counter()
    print(f"calibration: judging {len(judge_jobs)} games at JUDGE_BUDGET", flush=True)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            judged = list(pool.map(_judge_one, judge_jobs, chunksize=1))
    else:
        judged = [_judge_one(job) for job in judge_jobs]
    print(f"  judging done in {time.perf_counter() - started:.0f}s", flush=True)

    acpl_records = []
    acpl_by_level: dict[int, tuple[float, float]] = {}
    for lid in level_ids:
        samples = [j for j in judged if int(j["level_id"]) == lid and int(j["n_moves"]) > 0]
        values = [float(j["acpl"]) for j in samples]
        if not values:  # pragma: no cover - defensive
            continue
        mean = statistics.fmean(values)
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        acpl_by_level[lid] = (mean, std)
        acpl_records.append(
            {
                "level_id": lid,
                "mean": mean,
                "std": std,
                "n_games": len(values),
                "n_moves": sum(int(j["n_moves"]) for j in samples),
            }
        )

    # --- write levels.json (calibrated fields only) ------------------------- #
    levels_path = DATA_DIR / "levels.json"
    if update_levels:
        payload = json.loads(levels_path.read_text(encoding="utf-8"))
        for entry in payload:
            lid = int(entry["id"])
            entry["elo_internal"] = round(ratings[lid], 1)
            if lid in acpl_by_level:
                mean, std = acpl_by_level[lid]
                entry["acpl_mean"] = round(mean, 1)
                entry["acpl_std"] = round(max(std, 1.0), 1)
            entry["calibration_seed"] = calibration_seed
            entry["calibrated_at"] = generated_at
            entry["engine_version"] = __version__
        levels_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"  updated {levels_path}", flush=True)

    record = {
        "seed": calibration_seed,
        "generated_at": generated_at,
        "engine_version": __version__,
        "judge_node_budget": JUDGE_BUDGET,
        "games_per_adjacent_pair": games_adjacent,
        "games_per_skip_pair": games_skip,
        "acpl_games_per_level": acpl_games,
        "matches": matches,
        "elo_fit": elo_fit,
        "gap_fit": gap_fit,
        "acpl": acpl_records,
        "levels_sha256": sha256_of(levels_path),
    }
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=CALIBRATION_SEED)
    parser.add_argument("--games-adjacent", type=int, default=GAMES_PER_ADJACENT_PAIR)
    parser.add_argument("--games-skip", type=int, default=GAMES_PER_SKIP_PAIR)
    parser.add_argument("--acpl-games", type=int, default=ACPL_GAMES_PER_LEVEL)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--update-levels", action="store_true")
    parser.add_argument("--generated-at", default="2026-07-31T00:00:00Z")
    parser.add_argument("--out", default=str(FIXTURE_DIR / "calibration.json"))
    args = parser.parse_args()

    record = run(
        calibration_seed=args.seed,
        games_adjacent=args.games_adjacent,
        games_skip=args.games_skip,
        acpl_games=args.acpl_games,
        workers=args.workers,
        update_levels=args.update_levels,
        generated_at=args.generated_at,
    )
    Path(args.out).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    for gap in record["gap_fit"]:  # type: ignore[union-attr]
        print(
            f"  L{gap['low']}->L{gap['high']}: gap {gap['gap']:7.1f}  stderr {gap['stderr']:5.1f}"
        )


if __name__ == "__main__":
    main()
