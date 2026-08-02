# ChessMentor — Data Model

Two storage classes, per workspace conventions:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/chessmentor/data/`): difficulty ladder (`levels.json`), opening
  book (`openings.json`), advice catalog (`advice.json`). All three are
  **validated** at `chessmentor init`; **only `levels.json` is materialized as
  a SQLite table** (`level`) because game rows take an FK to it. The book is
  loaded into an in-memory trie and the advice catalog into an in-memory dict
  on every process start; the JSON files remain the source of truth.
  `evals/fixtures/calibration.json` is the full calibration record from which
  `levels.json`'s calibrated fields are derived.
- **SQLite** (user state, default `~/.chessmentor/chessmentor.db`, path
  configurable; in-memory backend for tests): profile, rating state, games,
  moves, analyses, rating events, coaching reports. Repository pattern;
  stdlib `sqlite3`.

All models are Pydantic v2 in `src/chessmentor/models.py`; the store maps them
to the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers (the
engine never reads the clock). Seeds are 64-bit ints supplied at game creation
(CLI/API generate one from `os.urandom` at the edge if omitted — edges may be
non-deterministic, the engine may not). Constant names (`JUDGE_BUDGET`,
`RD_FLOOR`, `SEV_BLUNDER`, …) are defined once in SCOPE.md's constants table.

## Enumerations

| Enum | Values |
|---|---|
| `Color` | `white`, `black` |
| `ChallengeMode` | `comfort`, `balanced`, `stretch` |
| `GameSource` | `played`, `imported` |
| `GameStatus` | `in_progress`, `player_win`, `opponent_win`, `draw`, `aborted`, `unfinished` |
| `Termination` | `checkmate`, `stalemate`, `resignation`, `insufficient_material`, `threefold_repetition`, `fifty_move_rule`, `imported_result`, `imported_unfinished`, `aborted` |
| `AnalystKind` | `internal`, `stockfish` |
| `Severity` | `ok`, `inaccuracy`, `mistake`, `blunder` |
| `Phase` | `opening`, `middlegame`, `endgame` |
| `MistakeCategory` | `allowed_mate`, `missed_mate`, `bad_trade`, `hung_piece`, `missed_tactic`, `allowed_tactic`, `endgame_technique`, `opening_principle`, `positional_drift` |
| `Motif` | `fork`, `pin`, `skewer`, `discovered`, `mate_threat`, `hanging_capture`, `other`, `none` |

`opponent_win` (not `cpu_win`) is the losing-side status: for played games the
opponent is the CPU, for imported games it is whoever the human faced.
`unfinished` is used **only** for imported games whose PGN `Result` tag is `*`
or unparseable (FR-13); such games are never rated and carry
`result_score = null`.

## Entities

### PlayerProfile — SQLite `player_profile` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1`; exactly one row (invariant) |
| `display_name` | str | used by PGN import side-matching (FR-13) |
| `challenge_mode` | ChallengeMode | sets the FR-8 target score; changing it recomputes the level recommendation before the next new game |
| `preferred_color` | `white` \| `black` \| `random` | default for new games |
| `created_at`, `updated_at` | str | ISO ts, caller-supplied |

There is no `auto_analyze` flag: the judge pass always runs at game end
(SCOPE non-goal 10), so every rated game has a rating-basis analysis.

### RatingState — SQLite `rating_state` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1` |
| `glicko_rating` | float | init `R_INIT` = 800.0 |
| `glicko_rd` | float | init `RD_INIT` = 350.0; invariant `RD_FLOOR (60) ≤ rd ≤ 350` |
| `perf_ewma` | float \| null | null until the first judged rated game |
| `judged_games` | int | number of games contributing to `perf_ewma`; selects `PERF_SIGMA_1` (=1) vs `PERF_SIGMA` (≥2) |
| `rated_games` | int | `n`; drives the FR-8 placement phase only (λ no longer depends on it) |
| `surprise_window` | JSON list[float] | the last ≤ `SURPRISE_WINDOW` values of `s_i − E_i` (FR-7a) |
| `games_since_rd_inflation` | int | ≥ `SURPRISE_WINDOW` before inflation may re-fire |
| `divergence_streak` | int | consecutive rated games with `\|perf_ewma − glicko_rating\| > DIVERGENCE_ELO` |
| `calibration_warning` | bool | set when `divergence_streak ≥ DIVERGENCE_STREAK` (FR-7d); diagnostic only |
| `current_level_id` | int FK → level | controller's standing recommendation |
| `last_game_id` | int FK \| null | last rated game applied (ordering check) |
| `updated_at` | str | ISO ts |

**Cold-start invariant (FR-8):** at `chessmentor init`, `current_level_id` =
the level minimising `|elo_internal − elo*|` where
`elo* = R_INIT + 400·log10((1 − target)/target)` for the profile's mode (ties →
lower id). No other initial value is permitted; M2/M3 fixtures assume it.

Derived, never stored: `λ = σ_p²/(σ_p² + glicko_rd²)` with
`σ_p = PERF_SIGMA_1` if `judged_games = 1` else `PERF_SIGMA`; and
`r_hat = λ·glicko_rating + (1−λ)·perf_ewma`, falling back to `glicko_rating`
while `perf_ewma` is null. Recomputed on read; historical values live in
`rating_event`, and replaying the event log from the initial state reproduces
every field above (including `surprise_window`, which is the last
`SURPRISE_WINDOW` events' `s − expected_score`).

### Level — committed `data/levels.json` → table `level`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | 1–10, contiguous (invariant) |
| `name` | str | e.g. `"L4"` (display) |
| `max_depth` | int | 1–5 |
| `node_budget` | int | exact search stop (FR-2) |
| `noise_sigma_cp` | float | Gaussian root-score noise σ |
| `blunder_prob` | float | 0 ≤ p ≤ 0.35 |
| `blunder_margin_lo_cp`, `blunder_margin_hi_cp` | int \| null | injection window; null iff `blunder_prob = 0`; `lo < hi` |
| `book_plies` | int | max plies the CPU follows the book |
| `elo_internal` | float | **calibrated** (FR-5) |
| `acpl_mean`, `acpl_std` | float | **calibrated** at `JUDGE_BUDGET`, book plies excluded |
| `calibration_seed`, `calibrated_at`, `engine_version` | int, str, str | provenance of the calibrated fields |

Invariants (all asserted at `init` and gated by M1b):

- `elo_internal` strictly increasing in `id`;
- every adjacent gap `elo_internal[i+1] − elo_internal[i]` ∈ **[100, 170]**
  (lower bound = genuinely distinct levels; upper bound = every rating is
  within 85 Elo of some level, which is what makes the FR-8 band reachable and
  M3's perfect-controller ceiling equal to 1.0);
- `acpl_mean` strictly decreasing in `id` (required for the FR-7b
  interpolation to be single-valued);
- `engine_version` equals the running engine version, and
  `calibration_seed` matches `calibration.json`.

Nominal pre-calibration ladder (data, retuned by FR-5, targeting ~150-Elo
spacing): depth 1→5, nodes 250→16 000, σ 200→0 cp, `blunder_prob` 0.30→0,
margins narrowing 80–900 → 100–300 cp, book 2→12 plies, nominal Elo 400→1750.

### OpeningLine — committed `data/openings.json` (in-memory trie; no table)

| Field | Type | Notes |
|---|---|---|
| `eco` | str | ECO code, e.g. `C50` |
| `name` | str | e.g. `Italian Game` |
| `uci` | list[str] | move sequence from the start position; 4–12 plies |
| `weight` | int | ≥ 1; seeded weighted choice among continuations |

Invariants: every sequence legal from the start position (validated at init);
no duplicate sequences; ~120 lines covering the common ECO A–E families.
`identify(moves)` returns the longest-prefix match; its length is the game's
`book_depth` used by the FR-9 ACPL exclusion.

### AdviceEntry — committed `data/advice.json` (in-memory dict; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `hung-piece-board-vision` |
| `category` | MistakeCategory | selection key |
| `phase` | Phase \| null | optional phase-specific variant; exact (category, phase) beats (category, null) |
| `title` | str | short heading |
| `body` | str | the advice text, emitted verbatim |
| `drill` | str | one concrete practice action |
| `source_note` | str | grounding, e.g. `Heisman, Novice Nook: counting / checks-captures-threats` |

Invariant: every `MistakeCategory` has ≥ 1 entry with `phase = null`
(validated at init) so suggestion selection is total. The catalog is mutable
between releases; reports do not depend on it after creation because
`suggestion` snapshots the rendered text (below).

### Game — SQLite `game`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `source` | GameSource | |
| `created_at` | str | ISO ts (input) |
| `seed` | int \| null | non-null iff `source = played` (invariant) |
| `player_color` | Color | |
| `level_id` | int FK \| null | null iff imported (invariant) |
| `level_elo` | float \| null | snapshot of `elo_internal` at creation — rating math stays reproducible after recalibration |
| `recommended_level_id` | int \| null | what the controller wanted (FR-8) |
| `level_overridden` | bool | `level_id != recommended_level_id` (US-8) |
| `status` | GameStatus | `in_progress` mutable; all other states frozen (invariant) |
| `termination` | Termination \| null | null while in progress |
| `result_score` | float \| null | player's score 1 / 0.5 / 0; null iff status ∈ {in_progress, aborted, unfinished}; otherwise consistent with `status` (invariant) |
| `ply_count` | int | |
| `final_fen` | str \| null | |
| `eco`, `opening_name` | str \| null | from `OpeningBook.identify` |
| `book_depth` | int | plies matched by `identify` (0–12); drives the FR-9 ACPL exclusion |
| `rated` | bool | **derived at termination**: `source = played` AND status ∈ {player_win, opponent_win, draw} AND `ply_count ≥ 8` |
| `imported_tags` | JSON \| null | raw PGN headers for imported games |

Invariants: at most one row with `status = in_progress` across the table (a
create request while one exists is rejected, FR-6); non-`in_progress` rows are
immutable; PGN is derived from `move_record` on demand, never stored
redundantly.

### MoveRecord — SQLite `move_record` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `game_id` | int FK | |
| `ply` | int | 1-based; unique `(game_id, ply)` (invariant); contiguous |
| `color` | Color | alternates from White at ply 1 (invariant) |
| `san`, `uci` | str | both stored |
| `fen_after` | str | position after the move |
| `is_book` | bool | CPU book move (FR-4 step 1) |
| `cpu_meta` | JSON \| null | CPU moves only; null for player/imported moves (invariant) |

`cpu_meta` schema (FR-4), null on book moves except `depth`/`nodes` = 0:

```json
{ "depth": 3, "nodes": 1487, "root_moves": 31,
  "score_cp": -35, "best_score_cp": 15,
  "blunder_rolled": false, "blunder_injected": false,
  "noise_changed_pick": true }
```

`best_score_cp` is `max s(·)` from the step-2 root pass and `score_cp` is the
played move's score in the same vector; M9 uses the pair to verify that every
injected blunder sits inside `[blunder_margin_lo_cp, blunder_margin_hi_cp]`.

### GameAnalysis — SQLite `game_analysis` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `game_id` | int FK | |
| `analyst` | AnalystKind | |
| `analyst_version` | str | engine semver for `internal`; the UCI engine's `id name` for `stockfish` |
| `node_budget` | int | per-move analyst budget |
| `created_at` | str | ISO ts (input) |
| `book_depth` | int | plies excluded from the aggregates (copy of `game.book_depth`) |
| `is_rating_basis` | bool | exactly one true per rated game; always the automatic judge pass (`internal`, `JUDGE_BUDGET`, current version) (invariant) |
| `acpl` | float | player moves with `in_acpl = true` only; per-move cp loss capped at `CP_LOSS_CAP` |
| `accuracy` | float | mean per-move accuracy over the same move set, 0–100 (decision D9) |
| `perf_rating` | float | ACPL interpolated through calibration anchors, clamped to `PERF_CLAMP` |
| `n_blunders`, `n_mistakes`, `n_inaccuracies` | int | player moves, included moves only |
| `mg_start_ply`, `eg_start_ply` | int \| null | FR-10 boundaries; `eg_start_ply` null if never reached |
| `per_phase` | JSON | `{phase: {acpl, accuracy, n_blunders, n_mistakes, n_inaccuracies, dw_sum}}` |
| `key_moments` | JSON | top-3 by Δw over included moves: `[{ply, dw, severity}]` |

Invariants: immutable once written; re-analysis inserts a new row;
**`(game_id, analyst, analyst_version, node_budget)` is unique** — a repeat
request returns the existing row, which is sound only because the tuple pins
the code that produced it (analyses are deterministic per FR-2/FR-16). A
version bump therefore produces a *new* row rather than silently returning
stale numbers.

**Report basis (FR-12):** an analysis is report-eligible iff
`analyst = internal AND node_budget = JUDGE_BUDGET AND analyst_version =
current engine version`. For a played rated game that is the same row as the
rating basis. This is a derived predicate, not a stored flag.

### MoveAnalysis — SQLite `move_analysis`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `analysis_id` | int FK → game_analysis | |
| `ply` | int | unique `(analysis_id, ply)`; player moves only |
| `in_acpl` | bool | false iff `ply ≤ game.book_depth`; excluded moves are stored but never aggregated (FR-9) |
| `cp_best`, `cp_played` | int | analyst scores, player's perspective; mates encoded ±(32000 − ply) |
| `cp_loss` | int | `min(CP_LOSS_CAP, max(0, cp_best − cp_played))` |
| `w_before`, `w_after` | float | `w(cp) = 1/(1+e^(−WIN_K·cp))` ∈ [0, 1] (FR-9) |
| `delta_w` | float | `max(0, w_before − w_after)` (invariant: consistent) |
| `severity` | Severity | thresholds `SEV_INACCURACY/SEV_MISTAKE/SEV_BLUNDER` = 0.05 / 0.10 / 0.15 on `delta_w` (invariant: consistent) |
| `best_uci` | str | analyst best move |
| `best_line_san` | JSON list[str] | PV, 2–6 plies |
| `phase` | Phase | from FR-10 boundaries |
| `category` | MistakeCategory \| null | non-null iff `in_acpl` AND severity ∈ {mistake, blunder} (invariant) |
| `motif` | Motif | `none` unless category ∈ {missed_tactic, allowed_tactic} |
| `evidence` | JSON \| null | machine-readable rule facts, e.g. `{rule: "hung_piece", capture_uci, see_cp, pre_move_see_cp}` |

### RatingEvent — SQLite `rating_event` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `game_id` | int FK | unique — one event per rated game (invariant) |
| `created_at` | str | ISO ts (input) |
| `result_score` | float | 1 / 0.5 / 0 |
| `opponent_elo` | float | `game.level_elo` snapshot |
| `expected_score` | float | `E_i` computed from `glicko_r_before` vs `opponent_elo` (FR-7a) |
| `surprise_after` | float | `Σ` of the surprise window after appending `result_score − expected_score` |
| `rd_inflated` | bool | whether the regime-change detector fired for this game |
| `glicko_r_before`, `glicko_rd_before` | float | |
| `glicko_r_after`, `glicko_rd_after` | float | Glicko-1 single-game update (FR-7a) |
| `perf_game` | float | from the rating-basis analysis; never null (the judge pass always runs) |
| `perf_ewma_after` | float | |
| `lambda_used` | float | `σ_p²/(σ_p² + glicko_rd_after²)` (FR-7c) |
| `r_hat_after` | float | blended estimate after this game |
| `level_played` | int | |
| `level_next` | int | controller's recommendation going forward (FR-8) |

Invariants: append-only; `game_id` values are applied in game-termination
order (checked against `rating_state.last_game_id`); replaying the event log
from the initial state reproduces `rating_state` exactly (tested).

### CoachingReport — SQLite `coaching_report` (immutable) + `suggestion`

`coaching_report`:

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `created_at` | str | ISO ts (input) |
| `window` | JSON list[{game_id, analysis_id}] | the exact analyses aggregated — one per game, always report-basis (FR-12); provenance for every number in the report |
| `skipped_game_ids` | JSON list[int] | games in the requested range with no report-basis analysis (surfaced to the user) |
| `include_imported` | bool | |
| `totals` | JSON | per category `{count, dw_sum}`; per phase `{dw_sum}` |
| `prev_report_id` | int FK \| null | trend deltas computed on read, never stored |

`suggestion`:

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `report_id` | int FK | |
| `rank` | int | 1–3; unique `(report_id, rank)` (invariant) |
| `category` | MistakeCategory | |
| `priority_score` | float | `Σ delta_w` for the category over the window (FR-12) |
| `advice_id` | str | resolved from the catalog; (category, dominant phase) preferred over (category, null) |
| `advice_title`, `advice_body`, `advice_drill` | str | **snapshot** of the catalog entry at report time, so an immutable report never changes when `advice.json` is edited |
| `evidence` | JSON | 3 worst instances: `[{game_id, ply, san, dw}]` (≥ 1 required — invariant) |

### CalibrationRecord — committed `evals/fixtures/calibration.json` (no table)

| Field | Type | Notes |
|---|---|---|
| `seed`, `generated_at`, `engine_version` | int, str, str | provenance |
| `judge_node_budget` | int | must equal `JUDGE_BUDGET` (invariant, asserted at init and by M1b) |
| `games_per_adjacent_pair`, `games_per_skip_pair` | int | 60 / 24 (FR-5) |
| `matches` | list | per pair `(i, j)`: games, smoothed score `S'`, raw points, per-game terminations |
| `elo_fit` | list | per level: `elo_internal`, `stderr` |
| `gap_fit` | list | per adjacent pair: `gap`, `stderr` (M1b gates `gap ≥ 2.5·stderr` and `gap ∈ [100, 170]`) |
| `acpl` | list | per level: mean, std, `n_moves` (book plies excluded) |
| `levels_sha256` | str | hash of `data/levels.json` this record produced; init and M1b assert the two are in sync |

## Relationships (summary)

```
PlayerProfile (1)      RatingState (1) ──▶ Level (current recommendation)
Game (N) ──▶ Level (0..1)          Game ──▶ MoveRecord (N, append-only)
Game ──▶ GameAnalysis (0..N, immutable) ──▶ MoveAnalysis (N)
Game (rated) ──▶ RatingEvent (1, append-only)
CoachingReport (N) ──▶ Suggestion (1..3)   [advice text snapshotted, not referenced]
CoachingReport.window ──▶ (Game, GameAnalysis) pairs, exactly one analysis per game
Level / OpeningLine / AdviceEntry: committed datasets, read-only at runtime
r_hat, λ, PGN text, report trends: always derived, never stored
```

## Example records

`level` (from `data/levels.json`, post-calibration):

```json
{
  "id": 4, "name": "L4",
  "max_depth": 2, "node_budget": 1500,
  "noise_sigma_cp": 100.0, "blunder_prob": 0.12,
  "blunder_margin_lo_cp": 80, "blunder_margin_hi_cp": 600,
  "book_plies": 6,
  "elo_internal": 861.0, "acpl_mean": 118.4, "acpl_std": 31.2,
  "calibration_seed": 20260731, "calibrated_at": "2026-07-31T00:00:00Z",
  "engine_version": "0.1.0"
}
```

`opening line` (from `data/openings.json`):

```json
{
  "eco": "C50", "name": "Italian Game",
  "uci": ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
  "weight": 4
}
```

`advice entry` (from `data/advice.json`):

```json
{
  "id": "hung-piece-board-vision",
  "category": "hung_piece", "phase": null,
  "title": "Stop dropping pieces: check your opponent's replies",
  "body": "Before committing a move, name every check, capture and threat your opponent has in reply, and count attackers vs defenders on the piece you just touched.",
  "drill": "For one week, write down your opponent's best reply before playing each move of a daily game.",
  "source_note": "Heisman, Novice Nook: counting / checks-captures-threats discipline"
}
```

`game` (finished, rated):

```json
{
  "id": 17, "source": "played", "created_at": "2026-07-31T18:02:11Z",
  "seed": 902144117, "player_color": "white",
  "level_id": 4, "level_elo": 861.0,
  "recommended_level_id": 4, "level_overridden": false,
  "status": "opponent_win", "termination": "checkmate", "result_score": 0.0,
  "ply_count": 62, "final_fen": "6k1/5ppp/8/8/8/5q2/6PP/5rK1 w - - 4 32",
  "eco": "C50", "opening_name": "Italian Game", "book_depth": 5, "rated": true
}
```

`move_record` (a CPU move with throttle metadata):

```json
{
  "game_id": 17, "ply": 14, "color": "black",
  "san": "Na5", "uci": "c6a5",
  "fen_after": "r1bqk2r/pppp1ppp/5n2/n3p3/2B1P3/2N2N2/PPPP1PPP/R1BQ1RK1 w kq - 6 8",
  "is_book": false,
  "cpu_meta": { "depth": 2, "nodes": 1500, "root_moves": 34,
                "score_cp": -35, "best_score_cp": 15,
                "blunder_rolled": false, "blunder_injected": false,
                "noise_changed_pick": true }
}
```

`move_analysis` (a flagged player move — note the tier: Δw = 0.294 is far
above `SEV_BLUNDER` = 0.15):

```json
{
  "analysis_id": 9, "ply": 23, "in_acpl": true,
  "cp_best": 40, "cp_played": -310, "cp_loss": 350,
  "w_before": 0.537, "w_after": 0.243, "delta_w": 0.294,
  "severity": "blunder",
  "best_uci": "c1e3", "best_line_san": ["Be3", "Ng4", "Bd4"],
  "phase": "middlegame",
  "category": "hung_piece", "motif": "none",
  "evidence": { "rule": "hung_piece", "capture_uci": "f6d5",
                "see_cp": 320, "pre_move_see_cp": -10 }
}
```

`rating_event`:

```json
{
  "game_id": 17, "created_at": "2026-07-31T18:40:05Z",
  "result_score": 0.0, "opponent_elo": 861.0,
  "expected_score": 0.571, "surprise_after": -0.94, "rd_inflated": false,
  "glicko_r_before": 912.0, "glicko_rd_before": 96.0,
  "glicko_r_after": 891.3, "glicko_rd_after": 92.4,
  "perf_game": 845.0, "perf_ewma_after": 866.2,
  "lambda_used": 0.487, "r_hat_after": 878.4,
  "level_played": 4, "level_next": 4
}
```

`suggestion` (inside a report, with the advice snapshot):

```json
{
  "report_id": 3, "rank": 1,
  "category": "hung_piece", "priority_score": 1.84,
  "advice_id": "hung-piece-board-vision",
  "advice_title": "Stop dropping pieces: check your opponent's replies",
  "advice_body": "Before committing a move, name every check, capture and threat your opponent has in reply, and count attackers vs defenders on the piece you just touched.",
  "advice_drill": "For one week, write down your opponent's best reply before playing each move of a daily game.",
  "evidence": [
    { "game_id": 17, "ply": 23, "san": "Nd5??", "dw": 0.294 },
    { "game_id": 15, "ply": 41, "san": "Rc8", "dw": 0.410 },
    { "game_id": 12, "ply": 19, "san": "Bg4", "dw": 0.322 }
  ]
}
```
