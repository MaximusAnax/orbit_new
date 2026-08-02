"""Generate ``game_scripts.json`` (D0) and ``selfplay_scripts.json`` (M10).

Run: ``uv run python chessmentor/evals/fixtures/generate_scripts.py``

Both files are pure *inputs* — seeds and move lists, no ground truth:

* ``game_scripts.json`` — 5 ``(seed, level_id, player_moves)`` scripts.  The
  player's moves are produced here by a seeded fixture policy (a deterministic
  choice among safe legal moves), then replayed through the production service
  twice by D0; the CPU's answers, the analyses, the rating events and the report
  JSON must come out byte-identical.
* ``selfplay_scripts.json`` — the 18 ``(k, game_index, seed)`` triples M10 uses
  to make the engine at level ``k``'s committed config play through the real
  session/judge/rating path.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import chess

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chessmentor.datasets import load_datasets
from chessmentor.engine.throttle import choose_cpu_move
from evals.fixtures import hash64, write_fixture
from evals.fixtures.truth import see

SCRIPT_SEED = 20260731
N_SCRIPTS = 5
PLAYER_MOVES = 12
SCRIPT_LEVELS = (2, 3, 4, 5, 6)

M10_LEVELS = (3, 6, 9)
M10_GAMES_PER_LEVEL = 6


def _safe_player_move(board: chess.Board, rng: random.Random) -> chess.Move | None:
    """Fixture player policy: a seeded choice among moves that do not hang material."""
    options = []
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        probe = board.copy(stack=False)
        probe.push(move)
        if probe.is_game_over(claim_draw=True):
            continue
        if any(
            probe.is_capture(reply) and reply.to_square == move.to_square and see(probe, reply) > 0
            for reply in probe.legal_moves
        ):
            continue
        options.append(move)
    if not options:
        options = [m for m in sorted(board.legal_moves, key=lambda m: m.uci())]
    if not options:
        return None
    return options[rng.randrange(len(options))]


def build_game_scripts(seed: int) -> dict[str, object]:
    data = load_datasets()
    scripts: list[dict[str, object]] = []
    for index, level_id in enumerate(SCRIPT_LEVELS[:N_SCRIPTS]):
        level = data.level_by_id(level_id)
        game_seed = hash64(seed, level_id, index, 7)
        rng = random.Random(hash64(seed, level_id, index, 9))
        board = chess.Board()
        player_moves: list[str] = []
        while len(player_moves) < PLAYER_MOVES:
            move = _safe_player_move(board, rng)
            if move is None:
                break
            player_moves.append(move.uci())
            board.push(move)
            if board.is_game_over(claim_draw=True):
                break
            reply = choose_cpu_move(
                board, level, game_seed=game_seed, ply=len(board.move_stack) + 1, book=data.book
            )
            board.push(reply.move)
            if board.is_game_over(claim_draw=True):
                break
        scripts.append(
            {
                "id": f"gs-{index + 1}",
                "seed": game_seed,
                "level_id": level_id,
                "player_color": "white",
                "player_moves": player_moves,
                "finish": "resign",
            }
        )
    return {"seed": seed, "scripts": scripts}


def build_selfplay_scripts(seed: int) -> dict[str, object]:
    triples = [
        {
            "k": level_id,
            "game_index": index,
            "seed": hash64(seed, level_id, index, 11),
        }
        for level_id in M10_LEVELS
        for index in range(M10_GAMES_PER_LEVEL)
    ]
    return {"seed": seed, "levels": list(M10_LEVELS), "games_per_level": M10_GAMES_PER_LEVEL, "scripts": triples}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SCRIPT_SEED)
    args = parser.parse_args()
    games = build_game_scripts(args.seed)
    path = write_fixture("game_scripts.json", games)
    print(f"wrote {path}: {len(games['scripts'])} scripts")
    selfplay = build_selfplay_scripts(args.seed)
    path = write_fixture("selfplay_scripts.json", selfplay)
    print(f"wrote {path}: {len(selfplay['scripts'])} triples")


if __name__ == "__main__":
    main()
