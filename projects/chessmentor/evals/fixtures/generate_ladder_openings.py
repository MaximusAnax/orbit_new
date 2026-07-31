"""Generate ``ladder_openings.json`` — the 8 four-ply match openings + M1a seeds.

Run: ``uv run python chessmentor/evals/fixtures/generate_ladder_openings.py``

The openings are curated (varied pawn structures so the ladder is not measured
on one position type) and their legality is verified here with python-chess.
The M1a seeds are derived from the committed ``LADDER_SEED`` so the file is
reproducible byte-for-byte and no seed is ever hand-picked (EVALS.md fixture
seed policy).
"""

from __future__ import annotations

import sys
from pathlib import Path

import chess

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.fixtures import hash64, write_fixture

#: Fixture seed.  Per EVALS.md this may not change in the same commit as an
#: engine or eval-function change.
LADDER_SEED = 20260731

#: 8 four-ply openings: open, closed, half-open, Indian, flank.
OPENINGS: list[tuple[str, str, list[str]]] = [
    ("open-e4e5", "Open Game", ["e2e4", "e7e5", "g1f3", "b8c6"]),
    ("qgd", "Queen's Gambit Declined", ["d2d4", "d7d5", "c2c4", "e7e6"]),
    ("sicilian", "Sicilian Defence", ["e2e4", "c7c5", "g1f3", "d7d6"]),
    ("kings-indian", "Indian Defence", ["d2d4", "g8f6", "c2c4", "g7g6"]),
    ("french", "French Defence", ["e2e4", "e7e6", "d2d4", "d7d5"]),
    ("english", "English Opening", ["c2c4", "e7e5", "b1c3", "g8f6"]),
    ("caro-kann", "Caro-Kann Defence", ["e2e4", "c7c6", "d2d4", "d7d5"]),
    ("reti", "Reti Opening", ["g1f3", "d7d5", "g2g3", "g8f6"]),
]

#: M1a plays this many games per adjacent pair (EVALS.md: G = 8, 4 per colour).
M1A_GAMES_PER_PAIR = 8


def _validate(uci: list[str]) -> str:
    board = chess.Board()
    for move_uci in uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"illegal opening move {move_uci}")
        board.push(move)
    return board.fen()


def build() -> dict[str, object]:
    openings = []
    for opening_id, name, uci in OPENINGS:
        openings.append({"id": opening_id, "name": name, "uci": uci, "fen": _validate(uci)})
    # One seed per (adjacent pair, game index).  Derived, never hand-picked.
    seeds = {
        f"{low}-{low + 1}": [
            hash64(LADDER_SEED, low, low + 1, index) for index in range(M1A_GAMES_PER_PAIR)
        ]
        for low in range(1, 10)
    }
    return {
        "seed": LADDER_SEED,
        "games_per_pair": M1A_GAMES_PER_PAIR,
        "openings": openings,
        "m1a_seeds": seeds,
    }


def main() -> None:
    path = write_fixture("ladder_openings.json", build())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
