# ChessMentor

A single-user chess trainer that plays you with its own throttleable
negamax/alpha-beta engine, continuously estimates your strength from results
*and* move quality, steers the CPU difficulty into a target win-rate band, and
after every game tells you — with evidence — what you got wrong and what to work
on.

Ratings are an **internal scale**. They are useful for adaptation and progress
tracking and are never presented as FIDE/Lichess-comparable (SCOPE decision D5).

The specification lives in [`docs/`](docs/) and is frozen:
[SCOPE.md](docs/SCOPE.md) (numbered FRs), [DATA_MODEL.md](docs/DATA_MODEL.md),
[EVALS.md](docs/EVALS.md), [REVIEW.md](docs/REVIEW.md).

## Status

This tree currently contains the **core**: domain models, the engine, the
adapters and the store, with unit and integration tests.

| Layer | State |
|---|---|
| `src/chessmentor/models.py` | complete — every DATA_MODEL entity with its invariants |
| `src/chessmentor/engine/` | complete — FR-2/3/4/6/7/8/9/10/11/12/13 |
| `src/chessmentor/adapters/` | complete — offline default + credential-gated live |
| `src/chessmentor/store/` | complete — repository + SQLite + in-memory |
| `data/` | committed ladder, opening book, advice catalog (see [data/README.md](data/README.md)) |
| `src/chessmentor/api/`, `src/chessmentor/cli/` | not built yet |
| `evals/` | not built yet |

`data/levels.json`'s calibrated fields (`elo_internal`, `acpl_mean`, `acpl_std`)
are the nominal pre-calibration ladder, not yet the output of the FR-5
calibration run. [data/README.md](data/README.md) records exactly what is and is
not calibrated, and why the node budgets were retuned.

## Quickstart

```bash
cd projects
uv sync --all-packages
uv run pytest chessmentor/ -q     # unit + integration tests
uv run ruff check chessmentor/    # lint
```

```python
import chess

from chessmentor.adapters import InternalAnalyst
from chessmentor.datasets import load_datasets
from chessmentor.engine.session import apply_cpu_move, apply_player_move
from chessmentor.models import Color

data = load_datasets()
level = data.level_by_id(4)
board = chess.Board()

apply_player_move(board, "e4", player_color=Color.WHITE)
reply = apply_cpu_move(board, level, game_seed=20260731, player_color=Color.WHITE, book=data.book)
print(reply.record.san, reply.record.cpu_meta)
```

## Layout

```
src/chessmentor/
  constants.py     # SCOPE's named-constants table, defined once
  models.py        # Pydantic v2 entities
  datasets.py      # loads + validates the committed datasets
  engine/          # pure domain logic: no network, no filesystem, no clock reads
    search.py      # FR-2  negamax alpha-beta, ID, quiescence, per-call TT, node budgets
    evaluate.py    # FR-3  tapered material + PeSTO PSTs, passed pawns, tempo
    throttle.py    # FR-4  single-pass full-window root, seeded noise + blunder injection
    session.py     # FR-6  pure game flow, terminal detection, PGN export
    rating.py      # FR-7  Glicko-1 + surprise inflation, ACPL→perf, precision blend
    adapt.py       # FR-8  cold start, ideal opponent, hysteresis, placement, clamping
    judge.py       # FR-9  cp loss, win model, severity, book exclusion, ACPL, accuracy
    phase.py       # FR-10 boundary rules
    taxonomy.py    # FR-11 SEE, motif detectors, precedence classifier, evidence
    coach.py       # FR-12 window selection, priority formula, advice snapshot
    pgn.py         # FR-13 PGN parsing, side resolution, result mapping
  adapters/        # a Protocol per capability + offline (default) and live impls
  store/           # Repository protocol, SQLite (default) and in-memory backends
data/              # committed datasets: levels.json, openings.json, advice.json
```

## Determinism and hermeticity (FR-16)

Engine functions take seeds and timestamps as inputs and stop on node budgets,
never on the wall clock. Identical inputs give byte-identical games, analyses,
ratings and reports. Tests run entirely on the offline adapters.

## Environment variables

Both live adapters are off unless explicitly configured; neither is ever used by
tests or evals.

| Variable | Effect |
|---|---|
| `CHESSMENTOR_STOCKFISH_PATH` | path to a UCI engine binary; enables `StockfishAnalyst` for deeper on-demand analysis |
| `CHESSMENTOR_LICHESS_LIVE=1` | enables `LichessExplorerBook` for richer CPU opening variety (ECO naming stays committed) |
| `CHESSMENTOR_DATA_DIR` | overrides where the committed datasets are read from |

No secrets are stored in the repository.
