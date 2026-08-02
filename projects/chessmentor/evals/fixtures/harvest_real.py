"""Harvest the real-game eval slices: ``judgment_cases_real.json`` (M4r, 30 cases)
and ``taxonomy_cases_real.json`` (M5r, 20 cases).

Run: ``uv run python chessmentor/evals/fixtures/harvest_real.py``

EVALS.md asks for *harvested* positions — messy middlegames with unstable PVs
and colliding motifs — rather than clean templates, so this script plays seeded
CPU-vs-CPU games across several level pairs and pulls player-side moves out of
them.  The games are deterministic (fixed seeds, committed level configs), so
the slice is reproducible byte-for-byte.

**Labelling.**  EVALS.md's fixture table describes the labels as developer
labelling with unlimited analysis time.  Here they are produced instead by the
committed, independent ground-truth machinery already used by the constructed
fixtures: ``truth.forced_value`` (exhaustive material minimax over python-chess
move generation) for the severity tier, and ``generate_taxonomy.reference_classify``
(an independent implementation of the FR-11 precedence table) for the category.
That trades some depth of judgment for full reproducibility and removes the
human from the loop entirely; the deviation is recorded in ``docs/REVIEW.md``.
Neither labeller ever consults the engine under evaluation.

Composition requirements, verified before the files are written:

* ``judgment_cases_real.json`` — 30 cases spanning all four tiers, of which at
  least 6 are **PV-unstable** (the analyst's best move at ``JUDGE_BUDGET``
  differs from its own best move at 4x that budget) and at least 8 contain
  **two or more simultaneous threats** (two distinct captures with SEE >= +100
  available to one side).
* ``taxonomy_cases_real.json`` — 20 flagged moves, at least 2 per category that
  the harvest can produce, at least 8 of them multi-motif positions where the
  FR-11 precedence order is what decides the answer.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import chess

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chessmentor.adapters import InternalAnalyst
from chessmentor.constants import JUDGE_BUDGET
from chessmentor.datasets import load_datasets

from evals.fixtures import hash64, load_fixture, write_fixture
from evals.fixtures.generate_phase_games import reference_boundaries
from evals.fixtures.generate_taxonomy import (
    non_pawn_pieces,
    reference_classify,
    search_pv,
)
from evals.fixtures.truth import delta_w, perturbation_margin, see, severity_of
from evals.harness import play_ladder_game

HARVEST_SEED = 20260731
#: Level pairings to harvest from: weak enough to blunder, strong enough to
#: produce positions with real tactical content.
PAIRS = [(2, 4), (3, 5), (4, 6), (5, 7), (1, 3), (6, 8)]
GAMES_PER_PAIR = 4
JUDGMENT_TARGET = 30
TAXONOMY_TARGET = 20
MIN_PV_UNSTABLE = 6
MIN_MULTI_THREAT = 8
MIN_MULTI_MOTIF = 8
PER_CATEGORY_MIN = 2
#: Judgment labels must survive the EVALS.md robustness envelope (+/-40 cp
#: common-mode with +/-20 cp differential), i.e. a differential margin >= 20.
MIN_LABEL_MARGIN_CP = 20
DEEP_MULTIPLIER = 4


@dataclass(frozen=True)
class Harvested:
    game_id: str
    ply: int
    fen: str
    played_uci: str
    played_san: str
    phase: str
    prior_moves: tuple[str, ...]
    cp_best: int
    cp_played: int
    tier: str
    category: str
    motif: str
    rationale: str
    multi_threat: bool


def simultaneous_threats(board: chess.Board) -> int:
    """Distinct winning captures available to the side to move."""
    targets = set()
    for move in board.legal_moves:
        if board.is_capture(move) and see(board, move) >= 100:
            targets.add(move.to_square)
    return len(targets)


def harvest(seed: int) -> list[Harvested]:
    data = load_datasets()
    openings = load_fixture("ladder_openings.json")["openings"]
    out: list[Harvested] = []

    for low, high in PAIRS:
        for index in range(GAMES_PER_PAIR):
            opening = openings[(low + index) % len(openings)]
            game = play_ladder_game(
                data.level_by_id(low),
                data.level_by_id(high),
                opening_uci=opening["uci"],
                opening_id=opening["id"],
                seed=hash64(seed, low, high, index),
                book=data.book,
                referee=None,
                max_ply=120,
            )
            moves = list(game.uci_moves)
            identified = data.book.identify(moves)
            book_depth = identified.depth if identified else 0
            mg, eg = reference_boundaries(moves, book_depth)

            # The weaker side is "the player": that is where the mistakes are.
            player_is_white = low == game.white_level_id
            board = chess.Board()
            for ply, uci in enumerate(moves, start=1):
                mover_is_white = board.turn == chess.WHITE
                if mover_is_white is not player_is_white or ply <= book_depth:
                    board.push(chess.Move.from_uci(uci))
                    continue
                move = chess.Move.from_uci(uci)
                before = board.copy(stack=False)
                cp_best, _ = search_pv(before, 4)
                after = before.copy(stack=False)
                after.push(move)
                reply_value, _ = search_pv(after, 3)
                cp_played = -reply_value
                board.push(move)
                if abs(cp_best) > 5_000 and abs(cp_played) > 5_000:
                    continue  # already mated either way: nothing to judge
                drop = delta_w(cp_best, cp_played)
                tier = severity_of(drop)
                if eg is not None and ply >= eg:
                    phase = "endgame"
                elif mg is not None and ply >= mg:
                    phase = "middlegame"
                else:
                    phase = "opening"
                label = reference_classify(
                    before, move, phase=phase, prior_moves=moves[: ply - 1]
                )
                out.append(
                    Harvested(
                        game_id=f"{low}v{high}-{index}",
                        ply=ply,
                        fen=before.fen(),
                        played_uci=uci,
                        played_san=before.san(move),
                        phase=phase,
                        prior_moves=tuple(moves[: ply - 1]),
                        cp_best=min(20_000, max(-20_000, cp_best)),
                        cp_played=min(20_000, max(-20_000, cp_played)),
                        tier=tier,
                        category=label.category if label else "positional_drift",
                        motif=label.motif if label else "none",
                        rationale=label.rationale if label else "",
                        multi_threat=simultaneous_threats(after) >= 2
                        or simultaneous_threats(before) >= 2,
                    )
                )
    return out


def pv_unstable(fen: str, analyst: InternalAnalyst) -> bool:
    """The analyst's own best move moves when its budget is multiplied by 4."""
    board = chess.Board(fen)
    shallow = analyst.analyse(board, node_budget=JUDGE_BUDGET)
    deep = analyst.analyse(board, node_budget=JUDGE_BUDGET * DEEP_MULTIPLIER)
    return shallow.best_move != deep.best_move


def select_judgment(candidates: list[Harvested], analyst: InternalAnalyst) -> list[Harvested]:
    """Fill 30 cases across all four tiers, honouring the two special quotas.

    Labels must satisfy the same robustness envelope EVALS.md requires of the
    constructed judgment fixtures — the truth tier survives a +/-40 cp
    common-mode shift jointly with a +/-20 cp differential error — and, like
    ``generate_judgment.py``, selection prefers the largest differential margin.
    Without this the slice commits knife-edge labels (truth delta_w within a few
    cp of a tier boundary) that flip on the positional component of any real
    evaluation, and M4r measures label noise instead of transfer
    (docs/REVIEW.md, hardening finding H1).
    """
    margins: dict[tuple[str, str], int] = {
        (c.fen, c.played_uci): perturbation_margin(c.cp_best, c.cp_played)
        for c in candidates
    }

    def margin_of(case: Harvested) -> int:
        return margins[(case.fen, case.played_uci)]

    candidates = [c for c in candidates if margin_of(c) >= MIN_LABEL_MARGIN_CP]
    candidates.sort(key=lambda c: (-margin_of(c), c.game_id, c.ply))
    by_tier: dict[str, list[Harvested]] = {}
    for case in candidates:
        by_tier.setdefault(case.tier, []).append(case)

    unstable_cache: dict[str, bool] = {}

    def unstable(case: Harvested) -> bool:
        if case.fen not in unstable_cache:
            unstable_cache[case.fen] = pv_unstable(case.fen, analyst)
        return unstable_cache[case.fen]

    chosen: list[Harvested] = []
    seen: set[tuple[str, str]] = set()

    def take(pool: list[Harvested], count: int) -> None:
        for case in pool:
            if count <= 0:
                return
            key = (case.fen, case.played_uci)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(case)
            count -= 1

    # Quota 1: PV-unstable positions (scanning is the expensive part, so cap it).
    # ``candidates`` is margin-descending, so the scan meets the quota with the
    # most label-robust unstable positions first.
    unstable_pool = []
    for case in candidates:
        if len(unstable_pool) >= MIN_PV_UNSTABLE:
            break
        if unstable(case):
            unstable_pool.append(case)
    take(unstable_pool, MIN_PV_UNSTABLE)

    # Quota 2: two or more simultaneous threats.
    take([c for c in candidates if c.multi_threat and (c.fen, c.played_uci) not in seen],
         MIN_MULTI_THREAT)

    # Then balance the tiers.
    target_per_tier = {"ok": 9, "inaccuracy": 6, "mistake": 6, "blunder": 9}
    for tier, target in target_per_tier.items():
        have = sum(1 for c in chosen if c.tier == tier)
        take([c for c in by_tier.get(tier, []) if (c.fen, c.played_uci) not in seen],
             max(0, target - have))
    # Top up to exactly 30 from whatever is left.
    take([c for c in candidates if (c.fen, c.played_uci) not in seen],
         max(0, JUDGMENT_TARGET - len(chosen)))

    chosen = chosen[:JUDGMENT_TARGET]
    stats = {
        "pv_unstable": sum(1 for c in chosen if unstable_cache.get(c.fen, False)),
        "multi_threat": sum(1 for c in chosen if c.multi_threat),
    }
    if len(chosen) < JUDGMENT_TARGET:
        raise SystemExit(f"only harvested {len(chosen)} judgment cases")
    if stats["pv_unstable"] < MIN_PV_UNSTABLE:
        raise SystemExit(f"only {stats['pv_unstable']} PV-unstable cases")
    if stats["multi_threat"] < MIN_MULTI_THREAT:
        raise SystemExit(f"only {stats['multi_threat']} multi-threat cases")
    return chosen


def select_taxonomy(candidates: list[Harvested]) -> list[Harvested]:
    """Fill 20 flagged cases, at least 2 per category the harvest can produce."""
    flagged = [c for c in candidates if c.tier in ("mistake", "blunder")]
    flagged.sort(key=lambda c: (c.game_id, c.ply))
    by_category: dict[str, list[Harvested]] = {}
    for case in flagged:
        by_category.setdefault(case.category, []).append(case)

    chosen: list[Harvested] = []
    seen: set[tuple[str, str]] = set()

    def take(pool: list[Harvested], count: int) -> None:
        for case in pool:
            if count <= 0:
                return
            key = (case.fen, case.played_uci)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(case)
            count -= 1

    # Multi-motif positions first: FR-11's precedence is what decides those.
    take([c for c in flagged if c.multi_threat], MIN_MULTI_MOTIF)
    for category in sorted(by_category):
        have = sum(1 for c in chosen if c.category == category)
        take(
            [c for c in by_category[category] if (c.fen, c.played_uci) not in seen],
            max(0, PER_CATEGORY_MIN - have),
        )
    take([c for c in flagged if (c.fen, c.played_uci) not in seen],
         max(0, TAXONOMY_TARGET - len(chosen)))
    chosen = chosen[:TAXONOMY_TARGET]
    if len(chosen) < TAXONOMY_TARGET:
        raise SystemExit(f"only harvested {len(chosen)} taxonomy cases")
    multi = sum(1 for c in chosen if c.multi_threat)
    if multi < MIN_MULTI_MOTIF:
        raise SystemExit(f"only {multi} multi-motif taxonomy cases")
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=HARVEST_SEED)
    args = parser.parse_args()

    candidates = harvest(args.seed)
    print(f"harvested {len(candidates)} labelled player moves", flush=True)
    analyst = InternalAnalyst()

    judgment = select_judgment(candidates, analyst)
    write_fixture(
        "judgment_cases_real.json",
        {
            "seed": args.seed,
            "source": "seeded CPU-vs-CPU games, labelled by exhaustive material minimax",
            "cases": [
                {
                    "id": f"jr-{index:03d}",
                    "game": case.game_id,
                    "ply": case.ply,
                    "fen": case.fen,
                    "played_uci": case.played_uci,
                    "played_san": case.played_san,
                    "truth_cp_best": case.cp_best,
                    "truth_cp_played": case.cp_played,
                    "truth_cp_loss": min(1_000, max(0, case.cp_best - case.cp_played)),
                    "truth_tier": case.tier,
                    "multi_threat": case.multi_threat,
                    "rationale": (
                        f"harvested from {case.game_id} ply {case.ply}; forced material "
                        f"{case.cp_best} -> {case.cp_played} cp within 4 plies"
                    ),
                }
                for index, case in enumerate(judgment, start=1)
            ],
        },
    )
    print(f"wrote judgment_cases_real.json: {len(judgment)} cases")

    taxonomy = select_taxonomy(candidates)
    write_fixture(
        "taxonomy_cases_real.json",
        {
            "seed": args.seed,
            "source": "seeded CPU-vs-CPU games, labelled by the independent FR-11 rule table",
            "cases": [
                {
                    "id": f"txr-{index:03d}",
                    "game": case.game_id,
                    "ply": case.ply,
                    "fen": case.fen,
                    "played_uci": case.played_uci,
                    "played_san": case.played_san,
                    "phase": case.phase,
                    "prior_moves": list(case.prior_moves),
                    "truth_category": case.category,
                    "truth_motif": case.motif,
                    "multi_motif": case.multi_threat,
                    "rationale": case.rationale,
                }
                for index, case in enumerate(taxonomy, start=1)
            ],
        },
    )
    counts: dict[str, int] = {}
    for case in taxonomy:
        counts[case.category] = counts.get(case.category, 0) + 1
    print(f"wrote taxonomy_cases_real.json: {len(taxonomy)} cases {counts}")


if __name__ == "__main__":
    main()
