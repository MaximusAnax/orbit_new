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

## What is here

| Layer | State |
|---|---|
| `src/chessmentor/models.py` | every DATA_MODEL entity with its invariants |
| `src/chessmentor/engine/` | FR-2/3/4/6/7/8/9/10/11/12/13 — pure, deterministic, no I/O |
| `src/chessmentor/adapters/` | offline default + credential-gated live implementations |
| `src/chessmentor/store/` | Repository protocol, SQLite (default) and in-memory backends |
| `src/chessmentor/services.py` | orchestration the API and CLI both delegate to |
| `src/chessmentor/api/` | FR-14 FastAPI app |
| `src/chessmentor/cli/` | FR-15 Typer CLI (`chessmentor`) |
| `data/` | committed ladder, opening book, advice catalog |
| `evals/` | committed fixtures, metrics, scorecard, pytest gates |

## Quickstart

```bash
cd projects
uv sync --all-packages

uv run chessmentor init --name "Ada" --challenge balanced --color white
uv run chessmentor play
uv run chessmentor rating --history
uv run chessmentor report

uv run pytest chessmentor/ -q                  # unit + integration tests + eval gates
uv run python chessmentor/evals/run.py         # the eval scorecard
uv run ruff check chessmentor/                 # lint
```

The database lives at `~/.chessmentor/chessmentor.db` by default; `--db PATH`
or `CHESSMENTOR_DB` overrides it.

## CLI

```
chessmentor init      create the database, validate the committed datasets, set the cold-start level
chessmentor profile show | set [--name --challenge comfort|balanced|stretch --color ...]
chessmentor play [--level N] [--color white|black|random] [--seed S]
chessmentor games list [--status --source --limit] | show <id> [--pgn]
chessmentor analyze <game-id> [--analyst internal|stockfish] [--nodes N]
chessmentor import <file.pgn> [--as white|black|auto] [--analyze]
chessmentor rating [--history]
chessmentor report [--last-games 10] [--include-imported]
chessmentor levels
chessmentor serve [--host --port]      # runs the FR-14 API with uvicorn
```

### Real output

```
$ uv run chessmentor init --name Owner --challenge balanced --color white
ChessMentor 0.1.0 initialised at the default database
  datasets: 10 levels, 113 opening lines, 13 advice entries — all validated
  profile:  Owner (balanced)
  starting level: L4 (internal 793) — cold start for R_hat = 800

$ uv run chessmentor levels
internal rating R_hat = 800 | mode balanced (target 0.50)
   lvl     elo      E depth  nodes  sigma     p   acpl
   L1      400   0.91     5    240    205  0.31    224
   L2      522   0.83     5    345    175  0.26    206
   L3      654   0.70     5    430    154  0.23    185
-> L4      793   0.51     5    555    135  0.20    163
   L5      938   0.31     5    740    116  0.17    140
   L6     1088   0.16     5   1030     96  0.15    117
   L7     1242   0.07     5   1515     75  0.12     96
   L8     1396   0.03     5   2175     56  0.08     76
   L9     1551   0.01     5   3050     44  0.06     60
   L10    1704   0.01     5   4200     35  0.04     46
```

(The `elo`/`acpl` columns are the FR-5 calibration run's output — see
`data/README.md` and `evals/fixtures/calibration.json` for provenance.)

`play` renders the board with `chess.Board.unicode()`, accepts SAN or UCI, and
understands `board`, `moves`, `legal`, `resign` and `quit`. Quitting leaves the
game resumable — state is persisted after every ply, so `chessmentor play`
picks it back up:

```
$ chessmentor play --seed 4242 --level 2
new game #1: you are white against L2 (internal 522) [level overridden]
Enter a move in SAN (Nf3) or UCI (g1f3). Other commands: board, moves, legal, resign, quit.
  -----------------
8 |♜|♞|♝|♛|♚|♝|♞|♜|
  -----------------
7 |♟|♟|♟|♟|♟|♟|♟|♟|
  ...
move> e2e4
CPU plays e6 (book)
move> g1f3
CPU plays Nf6 (depth 1, 345 nodes)
move> b1c3
CPU plays Nd5 (depth 1, 345 nodes)
move> d2d4
CPU plays b6 (depth 1, 345 nodes)
move> a2a3
CPU plays Nxc3 (depth 0, 345 nodes)
move> resign
You lose (resignation)
judge pass: ACPL 136, accuracy 71.2%, 2 blunders / 0 mistakes / 0 inaccuracies
internal rating 656 (glicko 427 +/- 280, move-quality 965, lambda 0.57) -> next opponent L3
```

(The scripted white moves ignored Black's play; the judge pass flags two of
them as blunders and the move-quality channel drops accordingly.)

Every command exits non-zero on error, with the message on stderr:

```
$ chessmentor rating --db /tmp/fresh.db
error: no rating state yet — run `chessmentor init`     # exit code 1
```

## API

`chessmentor serve` (or `uvicorn chessmentor.api.app:app`). Thin handlers: parse,
call one service method, serialize.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | engine version, dataset sizes, whether `init` has run |
| `GET`/`PUT` | `/profile` | `PUT` on a fresh database is the API's `init` |
| `GET` | `/levels` | the ladder with each rung's expected score and the recommendation |
| `POST` | `/games` | `{color?, level_id?, seed?, started_at?}`; **409** if a game is in progress |
| `GET` | `/games`, `/games/{id}` | filters `status`, `source`, `limit`; detail carries FEN, legal moves, PGN |
| `POST` | `/games/{id}/moves` | `{move: SAN\|UCI, at?}` → applied ply + CPU reply or terminal state |
| `POST` | `/games/{id}/resign`, `/games/{id}/abort` | abort only before ply 8, else **409** |
| `GET` | `/games/{id}/pgn` | |
| `POST`/`GET` | `/games/{id}/analysis` | dedups on `(analyst, version, node_budget)` |
| `POST` | `/imports/pgn` | `{pgn, as: white\|black\|null, analyze?}`; **422** on an ambiguous auto-match |
| `GET` | `/rating`, `/rating/history` | includes `calibration_warning` and its text |
| `POST`/`GET` | `/coach/reports`, `/coach/reports/{id}` | reports carry `skipped_game_ids` |

Error catalog — every failure is `{"detail", "code", …}`:

| Status | `code` | When |
|---|---|---|
| 400 | `illegal_move` | plus `legal_moves_san` |
| 404 | `not_found` | unknown game / analysis / report |
| 409 | `not_initialized` | no profile yet |
| 409 | `game_in_progress` | plus `in_progress_game_id` (FR-6 invariant) |
| 409 | `game_finished`, `not_players_turn`, `abort_too_late` | lifecycle violations |
| 422 | `ambiguous_side` (plus `matches`), `pgn_parse_error` | FR-13 import |
| 503 | `analyst_unavailable` | Stockfish requested but not configured |

## Evals

```bash
uv run python chessmentor/evals/run.py              # full scorecard, exits non-zero on any FAIL
uv run python chessmentor/evals/run.py --quick      # only the metrics that play no games
uv run python chessmentor/evals/run.py --baselines  # also measure M1a's collapsed-ladder baseline
uv run python chessmentor/evals/run.py --full       # release ritual: re-run the FR-5 calibration
uv run pytest chessmentor/evals/ -q                 # the same gates, as pytest
```

The suite is hermetic: committed fixtures, the internal analyst at
`JUDGE_BUDGET` (6 000 nodes/move), the committed opening book, an in-memory
store, seeded RNG, no network and no wall-clock dependence. It plays real
games (M1a: 72 adjudicated engine-vs-engine games; M10: 18 games through the
production service), so a full pass takes roughly 30–45 minutes on four
otherwise-idle cores.

| Metric | What it measures |
|---|---|
| M1b | the committed calibration record: strictly increasing Elo, gaps in [100, 170], every gap ≥ 2.5× its fitted stderr, integrity hashes |
| M1a | adjacent separation, live, as a drift check on the committed ladder |
| M2a/b/c | rating-estimator cold start, re-lock after a +300 jump, resistance to a 250-Elo channel bias |
| M3 | level-controller band adherence, min over comfort/balanced/stretch |
| M4/M4r | severity tiers on 120 constructed and 30 harvested cases |
| M5/M5r | mistake-taxonomy macro-F1 on 63 constructed and 20 harvested cases |
| M6 | phase boundaries on 20 constructed games (38 labelled boundaries) |
| M7/M7a | analyst tactical adequacy on 60 verified positions; mate-in-1 sub-gate |
| M8 | suggestion prioritisation exactness on 13 aggregation scenarios |
| M9 | throttle fidelity: the blunder and noise knobs are demonstrably wired |
| M10 | end-to-end rating fidelity through the production session/judge/rating path |
| D0 | determinism: five scripted games replayed byte-identically |

Every fixture under `evals/fixtures/` is regenerable by a committed seeded
script in the same directory (`generate_*.py`, `harvest_real.py`). Ground truth
never comes from the engine under evaluation: `evals/fixtures/truth.py` holds an
independent exhaustive material minimax, an independent SEE and the published
win model, and the taxonomy labels come from an independent implementation of
the FR-11 precedence table.

Each metric's scorecard row prints the naive baseline EVALS.md names for it,
measured live on the same data — results-only Glicko for M2a/M2b, a
move-quality-only estimator for M2c, raw centipawn thresholds for M4, "always
predict `hung_piece`" for M5, fixed phase plies for M6, a uniform random legal
move for M7, frequency-only ordering for M8, a budgets-only ladder for M9 and a
results-only rating for M10. `evals/baselines.py` additionally plays M1a's
collapsed-ladder baseline (`--baselines`).

`generate_calibration.py` is the one long-running script (FR-5: 60 games per
adjacent pair, 24 per skip-one pair). It runs offline before a release, never in
CI; CI gates its committed output (M1b) and re-checks freshness cheaply (M1a).
`docs/REVIEW.md` records the sample size the committed record was actually
produced at, and every gate threshold that was adjusted, with its derivation.

## Determinism and hermeticity (FR-16)

Engine functions take seeds and timestamps as inputs and stop on node budgets,
never on the wall clock. Identical inputs give byte-identical games, analyses,
ratings and reports. The API and CLI are the only places a clock is read, and
only to stamp records the caller did not stamp itself.

## Live adapters

Both are off unless explicitly configured, and neither is ever imported on the
test or eval path.

| Variable | Effect |
|---|---|
| `CHESSMENTOR_STOCKFISH_PATH` | path to a UCI engine binary; enables `StockfishAnalyst` (`--analyst stockfish`, `POST /games/{id}/analysis {"analyst":"stockfish"}`) for deeper on-demand analysis. Needs no pip dependency — python-chess's `chess.engine` drives the binary. A deeper analysis is stored alongside the judge pass and never becomes the rating or report basis (FR-12). |
| `CHESSMENTOR_LICHESS_LIVE=1` | enables `LichessExplorerBook`, which enriches the CPU's opening variety from the Lichess opening-explorer API. ECO naming stays committed. Never used by tests or evals. |
| `CHESSMENTOR_DATA_DIR` | overrides where the committed datasets are read from |
| `CHESSMENTOR_DB` | overrides the SQLite path |

Requesting an unavailable live adapter is a clear error (`503
analyst_unavailable` on the API, exit code 3 on the CLI), never a silent
fallback. No secrets live in the repository.

## Layout

```
src/chessmentor/
  constants.py     # SCOPE's named-constants table, defined once
  models.py        # Pydantic v2 entities
  datasets.py      # loads + validates the committed datasets
  services.py      # application services: the API and CLI both call these
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
  api/             # FR-14 FastAPI app + request/response schemas
  cli/             # FR-15 Typer app
data/              # committed datasets: levels.json, openings.json, advice.json
evals/             # harness, metrics, baselines, run.py, test_gates.py, fixtures/
tests/             # unit + integration tests
```
