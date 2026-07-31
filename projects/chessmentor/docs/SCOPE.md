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
  converge within a handful of games (results alone cannot do that), the
  level controller must hold the target score band, and every coaching claim
  (severity tier, mistake category, phase, priority) must be correct on
  ground-truth fixtures — a coach that mislabels mistakes is worse than none.

Both get first-class eval gates (see EVALS.md).

## Target user

The owner: one adult improver who plays casual untimed games at a terminal
and wants structured feedback. Single local profile; no accounts, no
multi-tenancy, no clocks. Ratings are an **internal scale** — useful for
adaptation and progress tracking, never presented as FIDE/Lichess-comparable
(see decision D5).

## User stories & acceptance criteria

**US-1 — Play a game now.** As a player, I start a game from the CLI, see a
readable board, enter moves in SAN or UCI, and the CPU replies within a few
seconds at every level.
*Accept:* illegal moves are rejected with the legal-move list available on
request; game state persists after every ply (kill the process, `play`
resumes); checkmate/stalemate/resignation/insufficient material/repetition/
fifty-move endings are detected and recorded; CPU reply latency ≤ ~5 s at the
top level with committed node budgets; the finished game exports as valid PGN.

**US-2 — It finds my level fast.** As a new player, after 3–5 games the CPU
sits at a level where games feel close — I don't have to configure anything.
*Accept:* on the simulated-player eval suite the blended rating estimate is
within 150 internal-Elo MAE of true strength after 5 games (EVALS M2 gate);
level changes never happen mid-game; the controller moves at most 1 level
between games (2 during the first 4 "placement" games).

**US-3 — Losing feels fair.** As a player, when I beat a low level it's
because it made human-plausible errors (a dropped piece, a missed recapture),
not because it played the worst legal move.
*Accept:* injected blunders are drawn only from moves 80–900 cp worse than
best (per-level margins, committed data); every CPU move records whether
noise/blunder injection altered the choice; the ladder eval (M1) shows each
level beats the one below it.

**US-4 — It keeps up as I improve.** As an improving player, when I start
winning too often the CPU steps up, and my rating history shows the climb.
*Accept:* in the improvement-jump simulation (player strength +300 at game
10) the estimate re-locks within 150 MAE by game 16 (M2b gate); each finished
rated game appends exactly one rating event with before/after state; expected
score of the chosen level stays in the target band ≥ 85 % of post-warmup
games (M3 gate).

**US-5 — Show me my mistakes.** As a player, after a game I get per-move
analysis: best move and line, centipawn loss, win-probability drop, severity
tier (ok/inaccuracy/mistake/blunder), plus my ACPL and accuracy for the game
and per phase.
*Accept:* severity tiers on the ground-truth judgment fixtures meet the M4
gate; every flagged move stores the analyst's best move and a ≥ 2-ply best
line in SAN; the three largest win-probability swings are surfaced as "key
moments"; re-running analysis with the same analyst config is byte-identical.

**US-6 — Tell me *why* and what to practice.** As a player, I get each
mistake categorized (hung piece, missed tactic, bad trade, allowed tactic,
missed/allowed mate, opening principle, endgame technique, positional drift)
and a prioritized improvement report over my recent games with concrete
evidence and a drill suggestion.
*Accept:* taxonomy macro-F1 on labeled fixtures meets the M5 gate; every
suggestion cites ≥ 1 concrete (game, move) with SAN and win-prob loss; the
priority ordering matches the documented formula exactly (M8 gate = 1.0);
advice text comes from the committed catalog, never generated.

**US-7 — Analyze my online games too.** As a player, I import a PGN from
elsewhere, mark which side I was, and get the same analysis and coaching;
imported games never touch my rating.
*Accept:* multi-game PGN files import; side selection by flag or by matching
the profile display name against PGN tags; imported games are excluded from
rating events and level selection but included in coaching reports when
requested.

**US-8 — Let me drive when I want.** As a player, I can force a specific
level for a game (trying to beat L7 for pride) without derailing adaptation.
*Accept:* an overridden game is flagged, still rated (I played it, it's
evidence), and the next non-overridden game resumes from the controller's
recommendation, not from the overridden level.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-4/5/7/8
are hard part A/B1-B2; FR-9/11 are hard part B3.

- **FR-1 Rules, notation, termination.** All rules/legality via
  `python-chess`: legal-move validation, SAN + UCI parse/render, FEN
  snapshots, PGN export and import. Terminal states detected and recorded:
  checkmate, stalemate, insufficient material, resignation; claimable draws
  (threefold repetition, fifty-move) end the game automatically as draws
  (decision D15). No chess rule is ever re-implemented.
- **FR-2 Search.** Own engine: negamax with alpha-beta (Knuth & Moore 1975
  formulation), iterative deepening, quiescence search over captures and
  promotions with stand-pat, transposition table keyed by
  `chess.polyglot.zobrist_hash` (Zobrist 1970; fixed entry count,
  depth-preferred replacement), move ordering = TT move, then MVV-LVA
  captures, then 2 killer moves, then history heuristic (Schaeffer 1989).
  Termination by exact node budget (never wall clock); mate scores encoded
  as ±(MATE − ply) so shorter mates win. Given (FEN, config) the result is
  deterministic: same best move, same score, same node count.
- **FR-3 Evaluation function.** Tapered evaluation (Fruit-style): material +
  piece-square tables using the published PeSTO midgame/endgame values
  (Chess Programming Wiki; lineage: Michniewski's Simplified Evaluation
  Function), game phase 0–24 with weights minor = 1, rook = 2, queen = 4;
  plus passed-pawn bonus by rank and a 10 cp tempo term. Output in
  centipawns from the side to move (negamax convention).
- **FR-4 Difficulty throttle (hard part A).** A `Level` config (committed
  `data/levels.json`, schema in DATA_MODEL.md) sets: `max_depth`,
  `node_budget`, `noise_sigma_cp`, `blunder_prob`,
  `blunder_margin_lo/hi_cp`, `book_plies`. CPU move choice at the root:
  (1) if game ply ≤ `book_plies` and the opening book has entries, play a
  seeded weighted book move; (2) otherwise score every root move with a
  reduced search (depth `max(1, max_depth − 2)`, shared node budget) and run
  the full-budget search for the best move; (3) with probability
  `blunder_prob`, if any root move scores within
  `[best − margin_hi, best − margin_lo]`, play the *worst* such move
  (blunder injection); (4) otherwise add seeded Gaussian noise
  `N(0, noise_sigma_cp)` to each root score and play the argmax (the
  full-depth move when the argmax is the unperturbed best). All randomness
  comes from a substream seeded by `(game_seed, ply)`, so replays are exact.
  Each CPU move records depth, nodes, score, whether noise changed the pick,
  and whether a blunder was injected.
- **FR-5 Ladder calibration (hard part A).** A committed, seeded script
  (`evals/fixtures/generate_calibration.py`) plays adjacent- and
  skip-one-pair matches across levels from fixed short openings with
  adjudication (see EVALS.md), fits internal Elo per level by least squares
  on the Elo expected-score model anchored at `L1 ≡ 400`, and measures each
  level's ACPL mean/std against the referee analyst **at the same judge
  budget used at runtime** (invariant — ACPL is budget-relative). Outputs
  are written into `data/levels.json` (`elo_internal`, `acpl_mean`,
  `acpl_std`) and `evals/fixtures/calibration.json` (full match record).
  Requirement: fitted Elo strictly increases with level index.
- **FR-6 Game lifecycle.** Create a game (color choice white/black/random,
  optional level override, explicit `seed` and `started_at` — time is always
  an input); at most one game may be `in_progress` (invariant — keeps rating
  updates totally ordered). Player move → validation → persistence → CPU
  reply → persistence, in one operation. Resignation supported. Games ending
  before ply 8 may be aborted (status `aborted`, never rated). Replaying the
  same seed + player moves reproduces the CPU's moves exactly.
- **FR-7 Rating estimation (hard part B).** After each rated game finishes:
  (a) **Glicko-1 update** (Glickman 1999) of the player's (rating, RD)
  from the game result against the CPU's `elo_internal` with opponent
  RD = 30; constants: `q = ln 10 / 400`, initial (800, 350), RD floor 50,
  no time-based RD inflation (no clock — decision D6).
  (b) **Move-quality performance rating**: the automatic post-game judge
  pass (FR-9) yields the player's ACPL; `perf_game` is the piecewise-linear
  interpolation of ACPL through the calibrated `(acpl_mean_L, elo_L)`
  anchors, clamped to [200, 2100]; `perf_ewma ← 0.35·perf_game +
  0.65·perf_ewma` (first observation initializes).
  (c) **Blend**: `R_hat = λ·R_glicko + (1 − λ)·perf_ewma` with
  `λ = min(0.8, n / (n + 4))`, `n` = rated games. Move quality dominates
  early (converges in 2–3 games; Regan & Haworth 2011), results dominate
  late (unbiased against eval-model quirks). One append-only rating event
  per rated game records every component before/after.
- **FR-8 Adaptive level controller (hard part B).** Target expected score
  for the player by challenge mode: `comfort` 0.60, `balanced` 0.50,
  `stretch` 0.42, band = target ± 0.05. Before each new game compute
  `E = 1 / (1 + 10^((elo_level − R_hat)/400))` (Elo 1978) for the current
  level; if `E` is outside the band, recommend the level whose `E` is
  nearest the target, moving at most 1 step from the current level (2 steps
  while `n < 4`, the placement phase). Never changes level mid-game.
  Override (US-8) plays any level without moving the controller's state.
- **FR-9 Post-game judgment.** For each player move, the `Analyst` adapter
  evaluates the pre-move position (best move, score, PV) and the post-move
  position: `cp_loss = max(0, cp_best − cp_played)` capped at 1000;
  win probability `w(cp) = 1 / (1 + e^(−0.00368208·cp))` (Lichess win model
  constant; mate scores map to w ≈ 0/1); `Δw = max(0, w_before − w_after)`
  from the player's perspective; severity: blunder `Δw ≥ 0.30`, mistake
  `≥ 0.20`, inaccuracy `≥ 0.10`, else ok (Lichess judgment thresholds).
  Per game: ACPL (mean capped cp_loss), accuracy per move
  `clamp(103.1668·e^(−0.04354·Δwp) − 3.1669, 0, 100)` with Δwp in
  win-percentage points (Lichess accuracy formula; we aggregate by plain
  mean — documented simplification, D9), per-phase splits, and the top-3
  `Δw` "key moments". A cheap judge pass (default 6 000 nodes/move) runs
  automatically at game end and is the rating basis (FR-7b); deeper
  re-analysis on demand stores a separate immutable analysis.
- **FR-10 Phase tagging.** Deterministic boundaries per game (thresholds
  are data, defaults follow the Lichess "Divider" concept):
  `middlegame_start` = first ply where the position is out of book AND
  (each side has ≥ 2 minor pieces off their home squares OR fullmove ≥ 10);
  `endgame_start` = first ply ≥ middlegame_start where the total count of
  non-pawn, non-king pieces (both sides) ≤ 6; absent if never reached.
  Every analyzed move carries its phase.
- **FR-11 Mistake taxonomy (hard part B).** Moves with severity ≥ mistake
  get exactly one category, decided by the first matching rule in fixed
  precedence order (detectors use the analyst PV, static exchange
  evaluation (SEE), and python-chess attack maps — see table below).
  Motif subtags (fork/pin/skewer/discovered/mate_threat) annotate
  missed/allowed tactics. Each categorization stores machine-readable
  evidence (the capture, SEE value, attacked pieces, template facts).
  Taxonomy only ever labels moves already flagged by severity — it explains
  losses, it does not police style.
- **FR-12 Coaching report.** Over a window of the last N analyzed games
  (default 10, played + optionally imported): per-category priority
  `score(c) = Σ Δw` over flagged moves in the window (win-probability mass
  lost — severity-weighted by construction), tie-broken by count then by
  recency of the worst instance. Top 3 categories become suggestions, each
  bound to an entry from the committed advice catalog (`data/advice.json`,
  keyed by category and optionally phase) with the 3 worst instances as
  evidence (game, ply, SAN, Δw) plus per-phase Δw shares and the delta vs
  the previous report. Fully deterministic (M8 gate = 1.0).
- **FR-13 PGN import.** Parse single- or multi-game PGN files
  (python-chess); the player's side comes from `--as white|black` or by
  matching profile `display_name` against the White/Black tags; imported
  games store their tags, are never rated, and are analyzable/coachable
  like played games.
- **FR-14 API.** FastAPI app per the endpoint sketch below; thin —
  validation, service calls, serialization only.
- **FR-15 CLI.** Typer app per the command sketch below; same services as
  the API; `play` is an interactive loop rendering the board
  (`Board.unicode()`), accepting SAN/UCI plus `board`, `moves`, `resign`,
  `quit` (game stays resumable).
- **FR-16 Determinism & hermeticity.** Engine functions take seeds and
  timestamps as inputs; node budgets, never wall clock; all tests and evals
  run with offline adapters (internal analyst, committed book), no network;
  identical inputs give byte-identical games, analyses, ratings, and
  reports.

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
   when a binary is configured; tests and evals use the internal analyst
   exclusively.
9. **Multi-user, auth, sync, concurrent games — out.** One profile, one
   in-progress game.

## Architecture

```
projects/chessmentor/
  src/chessmentor/
    engine/
      search.py      # FR-2: negamax αβ, ID, quiescence, TT, ordering, node budgets
      evaluate.py    # FR-3: tapered material + PeSTO PSTs, passed pawns, tempo
      throttle.py    # FR-4: level configs, root scoring, seeded noise + blunder injection
      rating.py      # FR-7: Glicko-1, expected score, ACPL→perf interpolation, blend
      adapt.py       # FR-8: band controller, hysteresis, placement, override handling
      judge.py       # FR-9: cp loss, win-prob model, severity, ACPL, accuracy, key moments
      phase.py       # FR-10: boundary rules (thresholds as data)
      taxonomy.py    # FR-11: SEE, motif detectors, precedence classifier, evidence
      coach.py       # FR-12: window aggregation, priority formula, advice selection
      session.py     # FR-6: pure game-flow orchestration (no I/O)
    adapters/
      analyst.py           # Analyst Protocol
      analyst_internal.py  #   offline: InternalAnalyst (own search, referee config)
      analyst_stockfish.py #   live: StockfishAnalyst (chess.engine UCI)
      book.py              # OpeningBook Protocol
      book_committed.py    #   offline: CommittedBook (data/openings.json trie + ECO naming)
      book_lichess.py      #   live: LichessExplorerBook (opening-explorer API)
    store/           # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/             # FastAPI app
    cli/             # Typer app
  data/              # committed datasets: levels.json, openings.json, advice.json
  evals/             # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated) |
|---|---|---|
| `Analyst.analyse(board, *, node_budget) -> MoveEval(best_move, score_cp, pv, nodes)` | `InternalAnalyst` — own search at referee config (depth ≤ 6, no noise); fully deterministic | `StockfishAnalyst` — python-chess `chess.engine` UCI wrapper; activates only when `CHESSMENTOR_STOCKFISH_PATH` points to a binary; no pip dependency |
| `OpeningBook.probe(board) -> list[BookMove(uci, weight)]`, `.identify(moves) -> Opening \| None` | `CommittedBook` — trie over `data/openings.json` (ECO code + name per line); seeded weighted choice | `LichessExplorerBook` — Lichess opening-explorer API; `CHESSMENTOR_LICHESS_LIVE=1` |

### API sketch (FastAPI)

```
GET  /health
GET  /profile                        PUT /profile
GET  /levels                         # ladder incl. elo_internal + current recommendation
POST /games                          # {color?, level_id?, seed, started_at} → game (+ CPU's first move if it is White)
GET  /games?status=&source=&limit=   GET /games/{id}        # status, FEN, PGN, legal moves
POST /games/{id}/moves               # {move: SAN|UCI, at} → applied ply + CPU reply or terminal state
POST /games/{id}/resign              POST /games/{id}/abort # abort only while ply < 8
GET  /games/{id}/pgn
POST /games/{id}/analysis            # {analyst?: internal|stockfish, node_budget?} → analysis
GET  /games/{id}/analysis            # latest (or ?analysis_id=)
POST /imports/pgn                    # body: pgn text + {as: white|black|auto} → imported game ids
GET  /rating                         GET /rating/history
POST /coach/reports                  # {last_games?, include_imported?} → persisted report
GET  /coach/reports/{id}             GET /coach/reports?limit=
```

### CLI sketch (Typer)

```
chessmentor init                                    # create DB, load datasets, integrity check
chessmentor profile show|set [--name --challenge comfort|balanced|stretch --color ...]
chessmentor play [--level N] [--color white|black|random] [--seed S]   # interactive loop; resumes an in-progress game
chessmentor games list [--status --source] | show <id> [--pgn]
chessmentor analyze <game-id> [--analyst internal|stockfish] [--nodes N]
chessmentor import <file.pgn> [--as white|black] [--analyze]
chessmentor rating [--history]
chessmentor report [--last-games 10] [--include-imported]
chessmentor levels
```

## Key design decisions & assumptions

1. **python-chess (Fiekas) is the rules substrate** — legality, SAN/UCI/FEN/
   PGN, terminal detection, attack maps, Zobrist hashing. It is pure Python
   and lightweight, so it is a core dependency, not an extra; we never
   re-implement a chess rule.
2. **Own search, not a wrapped engine, for the opponent.** The throttle needs
   direct control of depth/noise/blunder knobs, exact node-budget
   determinism, and hermetic evals. Prior art: Stockfish's `Skill Level` /
   `UCI_LimitStrength` weaken via bounded-suboptimal root choice — our
   FR-4 scheme is the same idea made deterministic and inspectable. Maia
   (McIlroy-Young, Sen, Kleinberg & Anderson, KDD 2020) showed pure
   depth-limiting blunders un-humanly, which is why noise + margin-bounded
   blunder injection are separate knobs rather than "just search less".
3. **Evaluation = tapered material + PeSTO PSTs.** Material values in the
   Shannon (1950) lineage; PST values from the published PeSTO tables
   (Chess Programming Wiki, successor to Michniewski's Simplified Evaluation
   Function); Fruit-style phase interpolation. Cheap enough for pure Python,
   strong enough for a ~1750-internal-Elo ceiling — the product is a trainer,
   not an engine-strength contest.
4. **Node budgets, never wall clock.** Search halts on an exact node count;
   identical hardware-independent games and evals (workspace rule: time is an
   input). Latency numbers in US-1 are consequences of budgets, not inputs.
5. **The Elo scale is internal and anchored by definition (L1 ≡ 400).**
   Absolute anchoring to human scales is unknowable offline; every consumer
   of the scale (controller, estimator, evals) needs only *expected-score
   consistency* (Elo 1978 model), which is exactly what M1/M2 measure. The
   docs and CLI say "internal rating" everywhere.
6. **Glicko-1 over plain Elo for the results channel.** RD gives principled
   fast movement while provisional and stability later (Glickman 1999),
   replacing ad-hoc K-factor schedules. We use single-game updates, opponent
   RD 30 (calibrated CPU), RD floor 50, and *no* time-based RD inflation —
   inflation needs a clock, and conventions forbid clock reads; the cost
   (slower re-convergence after long layoffs) is acceptable for a personal
   tool and documented.
7. **Move quality is the fast rating signal.** A game's ACPL maps to a
   performance rating through the ladder's own calibrated ACPL curve —
   the same idea as Regan & Haworth's intrinsic ratings (AAAI 2011) and
   Lichess/Chess.com per-game rating estimates, but self-calibrated so the
   two channels share one scale. Blended with weight `λ = min(0.8, n/(n+4))`
   so 2–3 games of move evidence dominate a cold start ("after a few games",
   per the owner's brief) while long-run results dominate any eval-model
   bias. The judge pass that feeds it runs at the *same node budget* used in
   calibration — ACPL is meaningless across different analyst strengths.
8. **Severity lives in win-probability space, not raw centipawns.** Raw cp
   thresholds mislabel already-decided positions (dropping 300 cp at +800 is
   not a blunder). We adopt the Lichess win model
   `w = 1/(1+e^(−0.00368208·cp))` and its judgment drops (0.10/0.20/0.30)
   and accuracy curve constants (103.1668, 0.04354, 3.1669) — published in
   the lila source and accuracy documentation — rather than inventing our
   own; the eval fixtures include decided-position cases specifically to
   punish raw-cp implementations.
9. **Documented simplification:** game accuracy is the plain mean of
   per-move accuracies (Lichess uses windowed harmonic weighting). Stated
   here so nobody "fixes" it into non-reproducibility; revisit post-MVP.
10. **Target-band adaptation, not "always 50 %".** Flow theory
    (Csikszentmihalyi 1990: challenge slightly above skill) and matchmaking
    practice (TrueSkill targets even matches; Herbrich, Minka & Graepel,
    NIPS 2006) motivate steering expected score into a band. Modes: comfort
    0.60, balanced 0.50, stretch 0.42, ± 0.05. Hysteresis (≤ 1 level/game)
    prevents oscillation; ladder spacing (~150 internal Elo) bounds the
    achievable band, which is why M3 gates on [0.38, 0.62] adherence.
11. **Coaching is an interpretable rules engine over search output** — SEE,
    attack maps, PV geometry — never an ML classifier or LLM. A coaching
    claim must carry evidence (measured Δw, the violated line, the capture
    that refutes the move) to be trustworthy, and rules keep evals hermetic.
    Same rationale as FormCoach's fault engine.
12. **Advice is curated data grounded in coaching practice.** Catalog
    entries cite their lineage in `source_note`: hung pieces/board vision →
    Heisman's "counting" and checks-captures-threats discipline (Novice
    Nook); missed tactics → the primacy of tactics at amateur level
    (Teichmann's "chess is 99 % tactics") and repetition-based drilling
    (Smith & Tikkanen, *The Woodpecker Method*, 2018); endgame technique →
    rating-tiered endgame study (Silman, *Complete Endgame Course*, 2007);
    opening principles → classical development/center/castling rules.
    The catalog is data; the engine never invents advice text.
13. **Phase boundaries follow the Lichess Divider concept** (piece-count
    thresholds: ≤ 10 majors+minors toward middlegame, ≤ 6 for endgame),
    adapted to be fully deterministic and committed as data. Chosen over
    move-number heuristics because coaching by phase is worthless if a
    queen endgame at move 25 is called "middlegame".
14. **Analyst honesty.** The internal analyst at the referee budget is
    club-strength, not oracle-strength. Judgment fixtures are constructed to
    be resolvable within its horizon (forced material within 4 plies +
    quiescence); the Stockfish live adapter exists precisely for deeper
    truth on demand and is never part of evals. Analyses record their
    analyst + budget so numbers are never compared across configs.
15. **Claimable draws auto-terminate.** Threefold/fifty-move draws are
    applied automatically (python-chess `can_claim_draw`) instead of
    modeling claims — a single-player fairness simplification, recorded in
    the termination field.
16. **One in-progress game** (invariant) totally orders rating events and
    makes `play` resumption unambiguous.
17. **Games aborted before ply 8 never rate** — misclicks and abandoned
    starts must not pollute the estimator.
18. **The opening book is small, committed, and dual-purpose:** variety for
    the CPU's first moves (seeded weighted choice) and ECO naming for
    coaching ("you left book on move 6 of the Italian Game"). ~120 curated
    ECO-tagged lines; the Lichess explorer adapter can enrich live, never in
    evals.
19. **Assumption: pure-Python search runs ~5–20k nodes/s.** All budgets are
    sized for that: top level ≈ 16k nodes ≈ ≤ 5 s/move; judge pass 6k
    nodes/move ≈ ≤ 1 min/game; deep analysis 20k ≈ few minutes/game. If nps
    lands outside this window, budgets (data) get retuned, not code.
20. **Assumption: implementation lands in ~2,500–3,500 lines** across
    engine/adapters/store/api/cli, within the 2–4k mandate; the taxonomy
    detector set (9 categories, 5 motifs) is the scope valve if pressure
    appears — categories degrade gracefully into `positional_drift`.
