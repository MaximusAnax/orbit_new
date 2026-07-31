# ChessMentor — Scope

## One-liner

A single-user chess trainer that plays you with its own throttleable
negamax/alpha-beta engine, continuously estimates your strength from results
*and* move quality (Glicko + centipawn-loss performance rating), steers the CPU
difficulty into a target win-rate band — and after every game tells you, with
evidence, what you got wrong and what to work on.

## Problem statement

Playing a chess engine is either demoralizing (it never blunders, you never
win) or meaningless (it plays randomly at "easy" and you learn nothing).
Existing apps hide their difficulty logic, adapt slowly or not at all, and
their "analysis" is a raw evaluation graph with no explanation. The owner
wants an opponent that settles at *their* level within a few games, loses in
human-plausible ways, keeps games competitive as they improve, and converts
each finished game into concrete coaching: which moves were blunders, *why*
(hung a piece, missed a fork, botched a won endgame), and what to practice
next.

The hard parts are:

- **A. A calibrated, controllable opponent.** The throttle (depth, eval
  noise, blunder injection) must produce a monotonic ladder of genuinely
  distinct strengths on a consistent internal Elo scale — otherwise
  "adaptive difficulty" is steering a knob that isn't attached to anything.
- **B. A trustworthy estimator + coach.** The player-rating estimate must
  converge within a handful of games (results alone cannot do that), stay
  correctable when either channel is biased, the level controller must hold
  the target score band, and every coaching claim (severity tier, mistake
  category, phase, priority) must be correct on ground-truth fixtures — a
  coach that mislabels mistakes is worse than none.

Both get first-class eval gates (see EVALS.md).

## Named constants

These names are used verbatim across all three docs and in code
(`src/chessmentor/constants.py`). Changing one is a fixture-regenerating,
baseline-re-deriving change (see EVALS.md).

| Constant | Value | Meaning |
|---|---|---|
| `JUDGE_BUDGET` | 6 000 nodes/move | the one analyst budget used by the automatic post-game judge pass, by FR-5's ACPL calibration, by M1 adjudication, and by M4/M5/M7/M10. "Referee" and "judge" are the same configuration; the word *referee* only names its adjudication role. |
| `DEEP_BUDGET` | 20 000 nodes/move | default for on-demand re-analysis (`analyze --nodes`); never used by evals |
| `WIN_K` | 0.00368208 | win-probability model slope, `w(cp) = 1/(1+e^(−WIN_K·cp))`, range [0, 1] |
| `SEV_INACCURACY` / `SEV_MISTAKE` / `SEV_BLUNDER` | 0.05 / 0.10 / 0.15 | severity thresholds on `Δw` (see D8 for why these are the Lichess thresholds) |
| `CP_LOSS_CAP` | 1 000 cp | per-move centipawn-loss cap before averaging |
| `R_INIT` / `RD_INIT` / `RD_FLOOR` / `OPP_RD` | 800 / 350 / 60 / 30 | Glicko-1 constants (FR-7a) |
| `SURPRISE_WINDOW` / `SURPRISE_THRESHOLD` / `RD_INFLATE_TO` | 4 games / 1.5 / 150 | regime-change detector (FR-7a) |
| `EWMA_ALPHA` | 0.35 | performance-rating EWMA weight (FR-7b) |
| `PERF_SIGMA_1` / `PERF_SIGMA` | 130 / 90 Elo | assumed s.d. of the move-quality channel after 1 judged game / after ≥ 2 (FR-7c) |
| `PERF_CLAMP` | [200, 2100] | clamp on a game's performance rating |
| `DIVERGENCE_CP` / `DIVERGENCE_STREAK` | 250 Elo / 5 games | channel-disagreement warning (FR-7d) |

## Target user

The owner: one adult improver who plays casual untimed games at a terminal
and wants structured feedback. Single local profile; no accounts, no
multi-tenancy, no clocks. Ratings are an **internal scale** — useful for
adaptation and progress tracking, never presented as FIDE/Lichess-comparable
(see decision D5).

## User stories & acceptance criteria

**US-1 — Play a game now.** As a player, I start a game from the CLI, see a
readable board, enter moves in SAN or UCI, and the CPU replies promptly at
every level.
*Accept:* illegal moves are rejected with the legal-move list available on
request; game state persists after every ply (kill the process, `play`
resumes); checkmate/stalemate/resignation/insufficient material/repetition/
fifty-move endings are detected and recorded; the finished game exports as
valid PGN. **Latency is a sized consequence, not a testable input:** budgets
are chosen so that at the assumed 5–20k nodes/s (D19) the top level replies in
≈ 1–4 s. The hermetic test asserts the *node budget* was respected
(`cpu_meta.nodes ≤ level.node_budget`), never a wall-clock duration.

**US-2 — It finds my level fast.** As a new player, after 3–5 games the CPU
sits at a level where games feel close — I don't have to configure anything.
*Accept:* on the simulated-player eval suite the blended rating estimate is
within 150 internal-Elo MAE of true strength after 5 games (EVALS M2a gate);
the controller's starting level is defined (FR-8, not left to the
implementer); level changes never happen mid-game; the controller moves at
most 1 level between games (2 during the first 4 "placement" games).

**US-3 — Losing feels fair.** As a player, when I beat a low level it's
because it made human-plausible errors (a dropped piece, a missed recapture),
not because it played the worst legal move.
*Accept:* injected blunders are drawn only from moves 80–900 cp worse than
best (per-level margins, committed data); every CPU move records whether the
blunder die was rolled, whether injection fired, and whether noise altered the
choice; M9 gates that the throttle machinery is actually operative at every
level; M1 shows each level beats the one below it.

**US-4 — It keeps up as I improve.** As an improving player, when I start
winning too often the CPU steps up, and my rating history shows the climb.
*Accept:* in the improvement-jump simulation (player strength +300 at game
10) the estimate re-locks within 150 MAE by game 16 (M2b gate); each finished
rated game appends exactly one rating event with before/after state; the level
played stays within ±85 internal Elo of the mode's ideal opponent ≥ 85 % of
post-warmup games (M3 gate).

**US-5 — Show me my mistakes.** As a player, after a game I get per-move
analysis: best move and line, centipawn loss, win-probability drop, severity
tier (ok/inaccuracy/mistake/blunder), plus my ACPL and accuracy for the game
and per phase.
*Accept:* severity tiers meet the M4 gate on constructed fixtures and the M4r
gate on the harvested real-game slice; every flagged move stores the analyst's
best move and a ≥ 2-ply best line in SAN; the three largest win-probability
swings are surfaced as "key moments"; re-running analysis with the same
analyst config and engine version is byte-identical.

**US-6 — Tell me *why* and what to practice.** As a player, I get each
mistake categorized (hung piece, missed tactic, bad trade, allowed tactic,
missed/allowed mate, opening principle, endgame technique, positional drift)
and a prioritized improvement report over my recent games with concrete
evidence and a drill suggestion.
*Accept:* taxonomy macro-F1 meets the M5 gate on constructed cases and M5r on
the real-game slice; every suggestion cites ≥ 1 concrete (game, move) with SAN
and win-prob loss; the priority ordering matches the documented formula
exactly (M8 gate = 1.0); advice text comes from the committed catalog, never
generated, and is snapshotted into the report so old reports never change.

**US-7 — Analyze my online games too.** As a player, I import a PGN from
elsewhere, mark which side I was, and get the same analysis and coaching;
imported games never touch my rating.
*Accept:* multi-game PGN files import; side selection by flag or by
unambiguous match of the profile display name against PGN tags (ambiguous or
missing → hard error naming the required flag); imported games are excluded
from rating events and level selection, and enter coaching windows only if
they carry an `internal @ JUDGE_BUDGET` analysis (FR-12).

**US-8 — Let me drive when I want.** As a player, I can force a specific
level for a game (trying to beat L7 for pride) without derailing adaptation.
*Accept:* an overridden game is flagged, still rated (I played it, it's
evidence), and the next non-overridden game resumes from the controller's
recommendation, not from the overridden level.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-4/5 are
hard part A; FR-7/8 are hard part B1–B2; FR-9/10/11/12 are hard part B3.

- **FR-1 Rules, notation, termination.** All rules/legality via
  `python-chess`: legal-move validation, SAN + UCI parse/render, FEN
  snapshots, PGN export and import. Terminal states detected and recorded:
  checkmate, stalemate, insufficient material, resignation; claimable draws
  (threefold repetition, fifty-move) end the game automatically as draws
  (decision D15). No chess rule is ever re-implemented.

- **FR-2 Search.** Own engine: negamax with alpha-beta (Knuth & Moore 1975
  formulation), iterative deepening, quiescence search over captures and
  promotions with stand-pat, transposition table keyed by
  `chess.polyglot.zobrist_hash` (Zobrist 1970; fixed 2^16 entries,
  depth-preferred replacement), move ordering = TT move, then MVV-LVA
  captures, then 2 killer moves, then history heuristic (Schaeffer 1989).
  Termination by exact node budget (never wall clock); mate scores encoded
  as ±(32000 − ply) so shorter mates win.
  **Table lifetime (invariant):** the TT, killer table and history table are
  created empty at every top-level `search()` call and discarded when it
  returns — nothing carries across moves or across analyst calls.
  **Root TT rule:** interior nodes may take TT cutoffs; the root never does,
  because FR-4 needs a score for *every* root move.
  **Determinism contract:** `search(fen, config, node_budget)` returns the
  same best move, the same root score vector and the same node count for the
  same arguments, independent of anything searched before it — the contract is
  per *position*, not per game. Cost: repeated positions inside a game are
  re-searched (a little strength left on the table); accepted because it makes
  the contract literally true and makes analyst calls order-independent, which
  every eval and the calibration record depend on.

- **FR-3 Evaluation function.** Tapered evaluation (Fruit-style): material +
  piece-square tables using the published PeSTO midgame/endgame values
  (Chess Programming Wiki; lineage: Michniewski's Simplified Evaluation
  Function), game phase 0–24 with weights minor = 1, rook = 2, queen = 4;
  plus passed-pawn bonus by rank and a 10 cp tempo term. Output in
  centipawns from the side to move (negamax convention).

- **FR-4 Difficulty throttle (hard part A).** A `Level` config (committed
  `data/levels.json`, schema in DATA_MODEL.md) sets `max_depth`,
  `node_budget`, `noise_sigma_cp`, `blunder_prob`, `blunder_margin_lo_cp`,
  `blunder_margin_hi_cp`, `book_plies`. **Exactly one code path chooses every
  CPU move**, in this order:

  0. If exactly one legal move exists, play it (`depth = 0`, `nodes = 0`).
  1. **Book.** If `ply ≤ book_plies` and `OpeningBook.probe` returns ≥ 1
     entry, play a seeded weighted choice among them; `is_book = true`; no
     search runs.
  2. **Root scoring — one pass, one budget.** Iterative deepening from depth 1
     to `max_depth` under the single budget `node_budget`. At the root every
     legal move is searched with a **full window** `(−∞, +∞)`: the root never
     narrows alpha across siblings and never takes a TT cutoff, so after each
     completed iteration every root move has a *comparable* score. Root move
     order is the FR-2 ordering (deterministic). Below the root, ordinary
     alpha-beta applies. If the budget is exhausted mid-iteration, the **last
     fully completed iteration's** score vector `s(·)` is used and `depth`
     records that iteration (a partial iteration is discarded, never merged).
  3. **Blunder injection.** Draw `u ~ U(0,1)`; `blunder_rolled = u <
     blunder_prob`. If rolled, form
     `C = {m : best − margin_hi ≤ s(m) ≤ best − margin_lo}` where
     `best = max_m s(m)`. If `C ≠ ∅`, play `argmin_{m ∈ C} s(m)` (ties → lowest
     UCI), set `blunder_injected = true` and **skip step 4**.
  4. **Noise.** Otherwise draw `ε_m ~ N(0, noise_sigma_cp)` for each root move
     in ascending-UCI order and play `argmax_m (s(m) + ε_m)` (ties → lowest
     UCI); `noise_changed_pick = (chosen ≠ argmax s)`. When
     `noise_sigma_cp = 0` no draws are consumed and the unperturbed best move
     is played.

  **Every comparison in steps 3–4 uses the step-2 score vector `s(·)`** — one
  depth, one budget, no mixed-depth comparisons and no second search. All
  randomness comes from a fresh `random.Random(seed_ply)` per ply with
  `seed_ply = (game_seed XOR (ply · 0x9E3779B97F4A7C15)) mod 2^64`, consumed in
  exactly the order above (book choice, blunder roll, noise vector), so replays
  are exact. `cpu_meta` records
  `{depth, nodes, root_moves, score_cp, best_score_cp, blunder_rolled,
  blunder_injected, noise_changed_pick}`.

  Full-window root costs ≈ 1.3–1.8× the nodes of a pruned root at equal depth;
  that is accepted (one code path, comparable sibling scores — which the
  throttle requires) and is absorbed by calibration, which measures whatever
  strength each level actually has.

- **FR-5 Ladder calibration (hard part A).** A committed, seeded script
  (`evals/fixtures/generate_calibration.py`) plays a round of matches across
  levels at their exact runtime configs: **60 games per adjacent pair** (30 per
  colour) and **24 games per skip-one pair**, from the 8 committed 4-ply
  `ladder_openings`, with adjudication (EVALS.md) to bound length. Per-game
  seeds are `hash64(calibration_seed, level_i, level_j, game_index)` so the
  record is identical regardless of worker count — the script is embarrassingly
  parallel and runs offline (≈ 3–6 h on 4 cores), never in CI.
  The fit: pair scores are smoothed `S' = (points + 0.5)/(G + 1)` before
  fitting (prevents a sweep driving a delta to infinity), Elo is fitted by
  least squares on `E = 1/(1+10^(−Δ/400))` anchored at `L1 ≡ 400`, per-pair
  deltas are clamped to ±600, and the fit reports a standard error per level
  and per adjacent gap.
  Each level's ACPL mean/std is measured against the internal analyst **at
  `JUDGE_BUDGET`** — the same budget the runtime judge pass uses (invariant:
  ACPL is meaningless across analyst strengths) — and **excludes book plies**
  under exactly the FR-9 rule.
  Outputs go to `data/levels.json` (`elo_internal`, `acpl_mean`, `acpl_std`)
  and `evals/fixtures/calibration.json` (full record + integrity hashes).
  **Requirements on the fitted ladder** (gated by M1b):
  `elo_internal` strictly increasing; every adjacent gap in **[100, 170]
  Elo** (≥ 100 = genuinely distinct levels; ≤ 170 = every player rating is
  within 85 Elo of some level, which is what makes M3's band achievable); and
  every adjacent gap ≥ 2.5 × its fitted standard error. If a gap falls outside
  the window, the *level configs* (data) are retuned and calibration re-run —
  never the gate.

- **FR-6 Game lifecycle.** Create a game (color choice white/black/random,
  optional level override, explicit `seed` and `started_at` — time is always
  an input); **at most one game may be `in_progress`** (invariant — keeps
  rating updates totally ordered). A create request while one is in progress
  is rejected (HTTP 409 naming the open game id; the CLI resumes it instead).
  Player move → validation → persistence → CPU reply → persistence, in one
  operation. Resignation supported. Games ending before ply 8 may be aborted
  (status `aborted`, never rated). Replaying the same seed + player moves
  reproduces the CPU's moves exactly.

- **FR-7 Rating estimation (hard part B).** After each rated game finishes,
  in this exact order:

  **(a) Results channel — Glicko-1 (Glickman 1999)** against the CPU's
  `game.level_elo` snapshot with opponent RD = `OPP_RD`; `q = ln 10 / 400`;
  initial (`R_INIT`, `RD_INIT`); RD clamped to [`RD_FLOOR`, `RD_INIT`].
  Before the update: compute `E_i = 1/(1+10^((level_elo − glicko_rating)/400))`
  and push `s_i − E_i` onto a rolling `surprise_window` of the last
  `SURPRISE_WINDOW` rated games. **Regime-change detector:** if the window is
  full, `|Σ(s_i − E_i)| ≥ SURPRISE_THRESHOLD`, and no inflation has fired
  within the last `SURPRISE_WINDOW` rated games, then set
  `rd ← min(RD_INIT, max(rd, RD_INFLATE_TO))` and clear the window. This is
  Glickman's RD-inflation step with *games* as the clock instead of wall time
  (conventions forbid clock reads, D6) and it is driven by the results channel
  alone — a persistently biased move-quality channel produces no surprise and
  therefore never inflates RD. Then apply the standard single-game update.

  **(b) Move-quality channel.** The automatic post-game judge pass (FR-9, run
  at `JUDGE_BUDGET`, always — there is no opt-out) yields the player's ACPL
  over non-book plies. `perf_game` = piecewise-linear interpolation of that
  ACPL through the calibrated `(acpl_mean_L, elo_L)` anchors (well-defined
  because `acpl_mean` is strictly decreasing in level), clamped to
  `PERF_CLAMP`. `perf_ewma ← EWMA_ALPHA·perf_game + (1−EWMA_ALPHA)·perf_ewma`
  (the first observation initializes it).

  **(c) Precision-weighted blend.**
  `λ = σ_p² / (σ_p² + rd_after²)`, `σ_p = PERF_SIGMA_1` when exactly one
  judged game exists and `PERF_SIGMA` thereafter;
  `R_hat = λ·glicko_rating + (1 − λ)·perf_ewma`.
  Each channel is weighted by its own precision, so the cold start (RD 350 →
  λ ≈ 0.06–0.22) is dominated by move quality — 2–3 games of evidence, the
  owner's "after a few games" — while a settled RD (floor 60 → λ ≈ 0.69) lets
  results dominate. λ is *not* a function of game count: a regime change
  inflates RD, which automatically hands weight back to the fast channel.
  Before any rated game, `R_hat = glicko_rating = R_INIT`.

  **(d) Divergence warning.** If `|perf_ewma − glicko_rating| > DIVERGENCE_CP`
  for `DIVERGENCE_STREAK` consecutive rated games, set
  `rating_state.calibration_warning`; the CLI (`rating`) and
  `GET /rating` surface it as "move-quality and result estimates disagree —
  the ACPL anchors may not describe your play". It is a diagnostic only; it
  changes no math. The residual weight the move-quality channel keeps forever
  (≈ 0.31 at the RD floor) is bounded by M2c, which gates total error under a
  250-Elo channel bias.

  One append-only `RatingEvent` per rated game records every component
  before/after, including `expected_score`, `surprise_after`, `lambda_used`
  and `rd_inflated`, so replaying the log reproduces `rating_state` exactly.

- **FR-8 Adaptive level controller (hard part B).** Per challenge mode the
  target expected score is `comfort` 0.60, `balanced` 0.50, `stretch` 0.42.
  Define the **ideal opponent rating**
  `elo* = R_hat + 400·log10((1 − target)/target)`
  (balanced → `R_hat`; comfort → `R_hat − 70.4`; stretch → `R_hat + 56.0`).

  - **Cold start (invariant):** at `chessmentor init`,
    `rating_state.current_level_id` = the level minimising `|elo_L − elo*|`
    for `R_hat = R_INIT` and the profile's mode (ties → lower id). Changing
    `challenge_mode` recomputes the recommendation before the next new game.
    Nothing about the starting level is left to the implementer.
  - **Before each new game:** compute `E` for the current level; if
    `|E − target| ≤ 0.05` keep it (hysteresis). Otherwise recommend the level
    minimising `|elo_L − elo*|`, moving at most **1 step** from the current
    level (**2 steps** while `rated_games < 4`, the placement phase), clamped
    to the ladder ends. Never changes level mid-game.
  - **Override (US-8)** plays any level for one game and does not move
    `current_level_id`.

- **FR-9 Post-game judgment.** For each player move the `Analyst` adapter
  evaluates the pre-move position (best move, score, PV) and the post-move
  position, from the player's perspective:
  `cp_loss = min(CP_LOSS_CAP, max(0, cp_best − cp_played))`;
  `w(cp) = 1/(1 + e^(−WIN_K·cp))` on **[0, 1]** (mate scores map to w ≈ 0/1);
  `Δw = max(0, w_before − w_after)`; severity: blunder `Δw ≥ SEV_BLUNDER`,
  mistake `≥ SEV_MISTAKE`, inaccuracy `≥ SEV_INACCURACY`, else ok.

  **Book-ply exclusion rule (invariant, applied identically here and in
  FR-5):** let `book_depth` = the length of the longest prefix of the game
  matched by `OpeningBook.identify` (0 if none, capped at 12 plies). Moves at
  `ply ≤ book_depth` are still analysed and stored (with `in_acpl = false`)
  but are **excluded** from ACPL, accuracy, per-phase aggregates and key
  moments. Without this, a player who knows theory gets an ACPL deflated
  relative to the calibration anchors, which are measured the same way.

  Per game: ACPL = mean capped `cp_loss` over included player moves; per-move
  accuracy `clamp(103.1668·e^(−0.04354·Δwp) − 3.1669, 0, 100)` with
  `Δwp = 100·Δw` in win-*percentage* points (Lichess accuracy formula),
  aggregated by plain mean (documented simplification, D9); per-phase splits;
  and the top-3 `Δw` "key moments". The judge pass at `JUDGE_BUDGET` runs
  automatically at the end of every finished game and is the rating basis
  (FR-7b); deeper re-analysis on demand stores a separate immutable analysis
  and never becomes the rating or report basis.

- **FR-10 Phase tagging.** Deterministic boundaries per game (thresholds are
  data): `middlegame_start` = first ply where the position is out of book AND
  (each side has ≥ 2 minor pieces off their home squares OR fullmove ≥ 10);
  `endgame_start` = first ply ≥ `middlegame_start` where the total count of
  non-pawn, non-king pieces (both sides) ≤ 6; absent if never reached. Every
  analysed move carries its phase.

- **FR-11 Mistake taxonomy (hard part B).** Moves with severity ≥ mistake get
  exactly one category, decided by the first matching rule in fixed precedence
  order (detectors use the analyst PV, static exchange evaluation (SEE), and
  python-chess attack maps — see table below). Motif subtags
  (fork/pin/skewer/discovered/mate_threat/hanging_capture) annotate
  missed/allowed tactics. Each categorization stores machine-readable evidence
  (the capture, SEE value, attacked pieces, template facts). Taxonomy only ever
  labels moves already flagged by severity — it explains losses, it does not
  police style.

- **FR-12 Coaching report.** Window = the most recent N games (default 10)
  that have a **report-basis analysis**, defined as
  `analyst = internal AND node_budget = JUDGE_BUDGET AND
  engine_version = current`. For a played rated game this is exactly the
  rating-basis analysis; an imported game qualifies only if it was analysed at
  that configuration (`import --analyze` does this). Exactly one analysis per
  game enters a report, and the chosen `(game_id, analysis_id)` pairs are
  stored on the report; games with no qualifying analysis are listed in
  `skipped_game_ids` and reported to the user. Mixing analyst configs inside a
  window is forbidden (D14 — cross-config numbers are not comparable).
  Priority per category `score(c) = Σ Δw` over flagged moves in the window
  (win-probability mass lost — severity-weighted by construction), tie-broken
  by count, then by recency of the worst instance, then by category name.
  Top 3 categories become suggestions, each bound to an entry from the
  committed advice catalog (`data/advice.json`, keyed by category and
  optionally phase) with the 3 worst instances as evidence (game, ply, SAN,
  Δw) plus per-phase Δw shares and the delta vs the previous report. The
  advice `title`/`body`/`drill` are **snapshotted into the suggestion row** so
  an immutable report never re-renders differently after the catalog is
  edited. Fully deterministic (M8 gate = 1.0).

- **FR-13 PGN import.** Parse single- or multi-game PGN files (python-chess).
  The player's side comes from `--as white|black`, or with `--as auto` by
  case-insensitive match of profile `display_name` against the White/Black
  tags: **exactly one** match selects that side; zero or two matches is a hard
  error naming `--as` (API 422) — never a silent guess. Games whose `Result`
  tag is `*` or unparseable are imported with status `unfinished`,
  `result_score = null`, and are analysable but never rated. Imported games
  store their tags, are never rated, and never affect level selection.

- **FR-14 API.** FastAPI app per the endpoint sketch below; thin —
  validation, service calls, serialization only.

- **FR-15 CLI.** Typer app per the command sketch below; same services as the
  API; `play` is an interactive loop rendering the board (`Board.unicode()`),
  accepting SAN/UCI plus `board`, `moves`, `resign`, `quit` (game stays
  resumable). `play` resumes an in-progress game rather than erroring.

- **FR-16 Determinism & hermeticity.** Engine functions take seeds and
  timestamps as inputs; node budgets, never wall clock; all tests and evals
  run with offline adapters (internal analyst, committed book), no network;
  identical inputs give byte-identical games, analyses, ratings, and reports.

### Taxonomy rules (FR-11, precedence order)

| # | Category | Detection rule (on a flagged player move) |
|---|---|---|
| 1 | `allowed_mate` | After the played move the analyst PV is a forced mate against the player within its horizon, and the pre-move best line was not mated. |
| 2 | `missed_mate` | Pre-move analyst best line is a forced mate for the player; the played move's line is not. |
| 3 | `bad_trade` | The played move is itself a capture with SEE ≤ −100 (initiated a losing exchange). |
| 4 | `hung_piece` | Opponent's PV reply is a capture with SEE ≥ +100, and in the *pre-move* position that capture was impossible or had SEE < +100 (the move created the loss: moved piece en prise, or removed/blocked a defender). |
| 5 | `missed_tactic` | Pre-move best move gains ≥ 200 cp over the played move AND the best PV's material delta over its first 4 plies is ≥ +200 cp. Subtag by best-move geometry: `fork` (moved piece attacks ≥ 2 enemy pieces of value ≥ 300, or any piece + king), `pin`/`skewer` (sliding piece newly aligned through two enemy pieces; pin if front < back value, skewer otherwise), `hanging_capture` (best move captures with SEE ≥ +100), else `other`. |
| 6 | `allowed_tactic` | Opponent's PV reply creates fork/pin/skewer geometry (same detectors, opponent side) accounting for the loss. |
| 7 | `endgame_technique` | Phase = endgame and no rule above matched (evidence notes `spoiled_win` when w dropped from ≥ 0.70 to ≤ 0.55, `spoiled_draw` when from [0.45, 0.55] to ≤ 0.30). |
| 8 | `opening_principle` | Phase = opening and no rule above matched, and one of: (a) ≥ 3rd move of the same piece while ≥ 2 own minors are undeveloped; (b) queen beyond its 3rd rank while ≥ 3 own minors are undeveloped; (c) king uncastled after fullmove 10 with castling rights intact. |
| 9 | `positional_drift` | Fallback: evaluation loss with no detected tactical/material pattern. |

## Non-goals (this pass)

1. **No web/GUI board** — API + CLI only (workspace-wide decision). The API
   is shaped so a later web board is a pure client.
2. **No clocks or time controls.** Untimed play; "time trouble" coaching is
   impossible by construction and out of scope.
3. **No human-likeness ML.** Maia-style move-matching models (McIlroy-Young
   et al., KDD 2020) are explicitly deferred; our blunder injection is a
   pragmatic approximation, and its plausibility is bounded by the margin
   window, not learned.
4. **No absolute rating claims.** Internal Elo only; never mapped to
   FIDE/USCF/Lichess numbers anywhere in output.
5. **No opening trainer, puzzle mode, or spaced repetition** — the tactics
   fixture format is designed so a puzzle mode could reuse it later.
6. **No endgame tablebases** (python-chess supports Syzygy; deferred).
7. **No LLM anywhere.** Advice is a curated catalog; deterministic and
   hermetic by construction.
8. **Stockfish is never required.** It is a live adapter for deeper analysis
   when a binary is configured, and it may be used *offline by the developer
   when hand-labelling the real-game eval slices* (label creation is not the
   eval path). Tests and evals at runtime use the internal analyst
   exclusively.
9. **Multi-user, auth, sync, concurrent games — out.** One profile, one
   in-progress game.
10. **No opt-out of post-game analysis.** The judge pass always runs on a
    finished game; a toggle would silently disable the rating channel the
    product's convergence promise depends on. (Cut deliberately; see D20.)
11. **No versioned advice-catalog history.** Reports snapshot their advice
    text instead; catalog archaeology is not a product requirement.

## Known limitations (stated, not hidden)

- **The ACPL → internal-Elo mapping is calibrated on CPU self-play, not on
  humans.** M2 verifies the estimator's arithmetic and M10 verifies the whole
  loop against the ladder itself; neither validates that a human with a given
  ACPL truly plays at the mapped strength (Regan & Haworth 2011 control for
  effects — opponent-dependence, position difficulty — that we do not). This
  is why the results channel exists, why λ is precision-weighted rather than
  fixed, and why FR-7d surfaces persistent disagreement.
- **The internal analyst at `JUDGE_BUDGET` is club-strength, not oracle.**
  Judgments in positions whose truth needs more than ~4 plies plus quiescence
  may be wrong; the Stockfish live adapter exists for deeper truth on demand.
- **Blunder injection is not human error modelling.** It is bounded-margin
  sampling; it makes losses plausible, not human.

## Architecture

```
projects/chessmentor/
  src/chessmentor/
    constants.py   # the named-constants table above
    engine/
      search.py      # FR-2: negamax αβ, ID, quiescence, per-call TT, ordering, node budgets
      evaluate.py    # FR-3: tapered material + PeSTO PSTs, passed pawns, tempo
      throttle.py    # FR-4: level configs, single-pass full-window root, seeded noise + injection
      rating.py      # FR-7: Glicko-1 + surprise inflation, ACPL→perf interpolation, λ blend
      adapt.py       # FR-8: cold start, ideal-opponent selection, hysteresis, placement, override
      judge.py       # FR-9: cp loss, win-prob model, severity, book exclusion, ACPL, accuracy
      phase.py       # FR-10: boundary rules (thresholds as data)
      taxonomy.py    # FR-11: SEE, motif detectors, precedence classifier, evidence
      coach.py       # FR-12: window/analysis selection, priority formula, advice snapshot
      session.py     # FR-6: pure game-flow orchestration (no I/O)
    adapters/
      analyst.py           # Analyst Protocol
      analyst_internal.py  #   offline: InternalAnalyst (own search, no noise/injection)
      analyst_stockfish.py #   live: StockfishAnalyst (chess.engine UCI)
      book.py              # OpeningBook Protocol
      book_committed.py    #   offline: CommittedBook (data/openings.json trie + ECO naming)
      book_lichess.py      #   live: LichessExplorerBook (opening-explorer API)
    store/           # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/             # FastAPI app
    cli/             # Typer app
  data/              # committed datasets: levels.json, openings.json, advice.json
  evals/             # fixtures/, metrics.py, baselines.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated) |
|---|---|---|
| `Analyst.analyse(board, *, node_budget) -> MoveEval(best_move, score_cp, pv, nodes)` | `InternalAnalyst` — own search with `noise_sigma_cp = 0`, `blunder_prob = 0`, `max_depth = 6`, budget supplied by the caller (`JUDGE_BUDGET` by default); fully deterministic per FR-2 | `StockfishAnalyst` — python-chess `chess.engine` UCI wrapper; activates only when `CHESSMENTOR_STOCKFISH_PATH` points to a binary; no pip dependency |
| `OpeningBook.probe(board) -> list[BookMove(uci, weight)]`, `.identify(moves) -> Opening \| None` | `CommittedBook` — trie over `data/openings.json` (ECO code + name per line); seeded weighted choice | `LichessExplorerBook` — Lichess opening-explorer API; `CHESSMENTOR_LICHESS_LIVE=1` |

### API sketch (FastAPI)

```
GET  /health
GET  /profile                        PUT /profile
GET  /levels                         # ladder incl. elo_internal + current recommendation
POST /games                          # {color?, level_id?, seed, started_at} → game (+ CPU's first move if it is White)
                                     #   409 {detail, in_progress_game_id} if a game is already in progress
GET  /games?status=&source=&limit=   GET /games/{id}        # status, FEN, PGN, legal moves
POST /games/{id}/moves               # {move: SAN|UCI, at} → applied ply + CPU reply or terminal state
POST /games/{id}/resign              POST /games/{id}/abort # abort only while ply < 8, else 409
GET  /games/{id}/pgn
POST /games/{id}/analysis            # {analyst?: internal|stockfish, node_budget?} → analysis (dedup on (analyst, version, budget))
GET  /games/{id}/analysis            # latest (or ?analysis_id=)
POST /imports/pgn                    # pgn text + {as: white|black|auto}; 422 on ambiguous auto-match
GET  /rating                         GET /rating/history    # includes calibration_warning
POST /coach/reports                  # {last_games?, include_imported?} → persisted report (+ skipped_game_ids)
GET  /coach/reports/{id}             GET /coach/reports?limit=
```

### CLI sketch (Typer)

```
chessmentor init                                    # create DB, load+validate datasets, set cold-start level, integrity check
chessmentor profile show|set [--name --challenge comfort|balanced|stretch --color ...]
chessmentor play [--level N] [--color white|black|random] [--seed S]   # interactive loop; resumes an in-progress game
chessmentor games list [--status --source] | show <id> [--pgn]
chessmentor analyze <game-id> [--analyst internal|stockfish] [--nodes N]
chessmentor import <file.pgn> [--as white|black|auto] [--analyze]
chessmentor rating [--history]
chessmentor report [--last-games 10] [--include-imported]
chessmentor levels
```

## Key design decisions & assumptions

1. **python-chess (Fiekas) is the rules substrate** — legality, SAN/UCI/FEN/
   PGN, terminal detection, attack maps, Zobrist hashing. Pure Python and
   lightweight, so it is a core dependency, not an extra; we never
   re-implement a chess rule.
2. **Own search, not a wrapped engine, for the opponent.** The throttle needs
   direct control of depth/noise/blunder knobs, exact node-budget determinism,
   and hermetic evals. Prior art: Stockfish's `Skill Level` /
   `UCI_LimitStrength` weaken via bounded-suboptimal root choice — FR-4 is the
   same idea made deterministic and inspectable. Maia (McIlroy-Young, Sen,
   Kleinberg & Anderson, KDD 2020) showed pure depth-limiting blunders
   un-humanly, which is why noise and margin-bounded injection are separate
   knobs rather than "just search less" — and why M9 gates that both knobs are
   actually operative.
3. **Evaluation = tapered material + PeSTO PSTs.** Material values in the
   Shannon (1950) lineage; PST values from the published PeSTO tables (Chess
   Programming Wiki, successor to Michniewski's Simplified Evaluation
   Function); Fruit-style phase interpolation. Cheap enough for pure Python,
   strong enough for a ~1750-internal-Elo ceiling — the product is a trainer,
   not an engine-strength contest.
4. **Node budgets, never wall clock.** Search halts on an exact node count;
   identical hardware-independent games and evals (workspace rule: time is an
   input). Latency numbers are consequences of budgets, never acceptance
   inputs (US-1).
5. **The Elo scale is internal and anchored by definition (L1 ≡ 400).**
   Absolute anchoring to human scales is unknowable offline; every consumer of
   the scale (controller, estimator, evals) needs only *expected-score
   consistency* (Elo 1978), which is what M1/M2/M10 measure. Docs and CLI say
   "internal rating" everywhere.
6. **Glicko-1 for the results channel, with games as the clock.** RD gives
   principled fast movement while provisional and stability later (Glickman
   1999), replacing ad-hoc K-factor schedules. Single-game updates, opponent
   RD 30, RD floor 60. Glickman's time-based RD inflation is impossible here
   (conventions forbid clock reads), so inflation is **event-driven**: a
   4-game CUSUM of `s − E` above 1.5 re-inflates RD to 150 (FR-7a). This keys
   on the *results* channel only, so a genuine strength jump re-inflates
   (US-4, M2b) while a persistently biased move-quality channel does not
   (M2c) — the two failure modes are distinguished by construction. The RD
   floor is 60 rather than 50 so a settled estimate still responds to
   evidence; the cost (slower re-convergence after a long layoff with no
   surprising results) is acceptable for a personal tool.
7. **Move quality is the fast rating signal, and the blend is
   precision-weighted.** A game's ACPL maps to a performance rating through
   the ladder's own calibrated ACPL curve — the idea of Regan & Haworth's
   intrinsic ratings (AAAI 2011) and of Lichess/Chess.com per-game estimates,
   but self-calibrated so both channels share one scale. Instead of a
   count-based λ, each channel is weighted by its own variance:
   `λ = σ_p²/(σ_p² + RD²)`. This is the standard inverse-variance combination
   and it fixes two things a `min(0.8, n/(n+4))` schedule got wrong — it makes
   the cold start depend on actual uncertainty rather than on a game counter,
   and it hands weight back to move quality automatically whenever RD
   re-inflates after a regime change (a count-based λ freezes near its cap and
   makes US-4's re-lock arithmetically impossible). `σ_p = 90` is the assumed
   s.d. of the EWMA'd performance channel: per-game perf σ ≈ 130 Elo from the
   calibrated `acpl_std` spread, EWMA at α = 0.35 has steady-state s.d.
   ≈ 130·√(α/(2−α)) ≈ 60, inflated to 90 for anchor/model uncertainty
   (`σ_p = 130` while only one judged game exists). At the RD floor the move
   quality channel retains ≈ 0.31 weight forever; that residual is *justified
   by its precision*, bounded by the M2c gate (total error ≤ 120 under a
   250-Elo channel bias), and surfaced by FR-7d when it is real.
8. **Severity lives in win-probability space, not raw centipawns, on one
   scale.** Raw cp thresholds mislabel already-decided positions (dropping
   300 cp at +800 is not a blunder). We use the Lichess win model
   `w = 1/(1+e^(−0.00368208·cp))`, whose range is **[0, 1]**. Lichess's
   published judgment drops (0.1 / 0.2 / 0.3) are defined on its *winning
   chances* scale `2w − 1`, whose range is **[−1, 1]**; expressed on our
   [0, 1] scale they are exactly **0.05 / 0.10 / 0.15**, which is what
   `SEV_*` holds. (An earlier draft applied 0.1/0.2/0.3 directly to `w` and
   thereby halved sensitivity — hanging a knight from equality scored
   Δw = 0.25 and was labelled a *mistake*.) The accuracy formula is a separate
   published curve defined on win-*percentage* points (`Δwp = 100·Δw`), so it
   needs no rescaling — that asymmetry is deliberate, not an oversight. The
   M4 fixtures include decided-position traps specifically to punish raw-cp
   implementations.
9. **Documented simplification:** game accuracy is the plain mean of per-move
   accuracies (Lichess uses windowed harmonic weighting). Stated here so
   nobody "fixes" it into non-reproducibility; revisit post-MVP.
10. **Target-band adaptation, not "always 50 %".** Flow theory
    (Csikszentmihalyi 1990: challenge slightly above skill) and matchmaking
    practice (TrueSkill targets even matches; Herbrich, Minka & Graepel, NIPS
    2006) motivate steering expected score into a band. Modes: comfort 0.60,
    balanced 0.50, stretch 0.42, hysteresis ±0.05. The controller works in
    Elo space (`elo*`) so all three modes use one formula and one gate (M3
    runs all three); the ladder-gap ceiling of 170 Elo (FR-5) is exactly what
    makes "some level is within 85 Elo of ideal" true for every player, which
    is what makes M3's perfect-controller ceiling genuinely 1.0.
11. **Coaching is an interpretable rules engine over search output** — SEE,
    attack maps, PV geometry — never an ML classifier or LLM. A coaching claim
    must carry evidence (measured Δw, the violated line, the capture that
    refutes the move) to be trustworthy, and rules keep evals hermetic.
12. **Advice is curated data grounded in coaching practice.** Catalog entries
    cite lineage in `source_note`: hung pieces/board vision → Heisman's
    "counting" and checks-captures-threats discipline (Novice Nook); missed
    tactics → the primacy of tactics at amateur level (Teichmann) and
    repetition drilling (Smith & Tikkanen, *The Woodpecker Method*, 2018);
    endgame technique → rating-tiered endgame study (Silman, *Complete Endgame
    Course*, 2007); opening principles → classical development/center/castling
    rules. The engine never invents advice text, and reports snapshot it.
13. **Phase boundaries: the endgame rule follows the Lichess "Divider"
    piece-count idea** (≤ 6 non-pawn, non-king pieces), chosen over move-number
    heuristics because coaching by phase is worthless if a queen endgame at
    move 25 is called "middlegame". The **middlegame rule is ours** — a
    development/fullmove heuristic (out of book AND minors developed OR
    fullmove ≥ 10), not Divider's mixedness measure, because a book-aware
    trainer already knows exactly when theory ended and that is the more
    meaningful boundary for coaching. Divider is not cited for it.
14. **Analyst honesty.** The internal analyst at `JUDGE_BUDGET` is
    club-strength, not oracle-strength. Judgment fixtures are constructed to
    be resolvable within its horizon (forced material within 4 plies +
    quiescence); the Stockfish adapter exists for deeper truth on demand and
    is never on the eval runtime path. Analyses record analyst, budget **and
    engine version**, so numbers are never compared across configs — which is
    also why FR-12 pins one analysis configuration per report window.
15. **Claimable draws auto-terminate.** Threefold/fifty-move draws are applied
    automatically (python-chess `can_claim_draw`) instead of modelling claims —
    a single-player fairness simplification, recorded in the termination field.
16. **One in-progress game** (invariant) totally orders rating events and makes
    `play` resumption unambiguous; concurrent creation is a 409, not a
    silent abandon.
17. **Games aborted before ply 8 never rate** — misclicks and abandoned starts
    must not pollute the estimator.
18. **The opening book is small, committed, and dual-purpose:** variety for the
    CPU's first moves (seeded weighted choice) and ECO naming for coaching
    ("you left book on move 6 of the Italian Game"), and it defines
    `book_depth` for the FR-9 ACPL exclusion. ~120 curated ECO-tagged lines;
    the Lichess explorer adapter can enrich live, never in evals.
19. **Assumption: pure-Python search runs ~5–20k nodes/s.** All budgets are
    sized for that: top level 16k nodes ≈ 1–4 s/move; judge pass
    `JUDGE_BUDGET` = 6k nodes/move ≈ ≤ 1 min/game; deep analysis
    `DEEP_BUDGET` = 20k ≈ a few minutes/game. If nps lands outside this
    window, budgets (data) get retuned, not code.
20. **Scope valves, chosen deliberately.** The `auto_analyze` toggle is cut
    (non-goal 10) — it existed only to skip the judge pass and silently
    degraded hard part B. Advice-catalog versioning is cut (non-goal 11) in
    favour of snapshotting. If line pressure appears, the taxonomy detector set
    (9 categories, 5 motifs) is the valve: categories degrade gracefully into
    `positional_drift`.
21. **Assumption: implementation lands in ~2,600–3,600 lines** of
    `src/chessmentor/`, within the 2–4k mandate. Tests and the eval suite
    (fixtures excluded — they are data) add ~1,200–1,600 lines and sit outside
    that envelope, per workspace conventions.
