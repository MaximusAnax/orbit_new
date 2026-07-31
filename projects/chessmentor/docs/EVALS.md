# ChessMentor — Evals

## What this product lives or dies on

1. **A calibrated ladder and an adaptation loop that locks on fast**
   (FR-4/5/7/8): the difficulty knobs must produce genuinely distinct,
   monotonically ordered strengths on one internal Elo scale, and the
   blended estimator + controller must find the player within a handful of
   games and hold the target score band. If this is wrong, "adaptive
   difficulty" is a random-number generator with a settings page.
2. **Coaching that is correct on ground truth** (FR-9/10/11/12): severity
   tiers, mistake categories, phase tags, and suggestion priorities must
   match constructed ground truth. A coach that calls a winning sacrifice a
   blunder, or tells you to study endgames when you hang pieces, actively
   harms the user.

Everything else (CRUD, PGN plumbing, CLI rendering) is covered by ordinary
tests, not eval gates.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with offline adapters (`InternalAnalyst`, `CommittedBook`,
in-memory store). No network, no clock, fixed seeds. Metric M-numbers map to
gates in `evals/test_gates.py`; test names reference FR ids.

### M1 — Ladder separation and ordering (capability 1)

Micro-tournament from fixed seeds: every adjacent pair `(i, i+1)` for
levels 1–10 plays `G = 10` games (5 per color) starting from the 8 committed
`ladder_openings` positions, with adjudication (below) to bound runtime.
`S(i, j)` = score of level `j` against level `i` (win 1, draw 0.5).

```
M1a = mean over the 9 adjacent pairs of S(i, i+1)
M1b = Spearman rank correlation between level index and Elo fitted to the
      match results by least squares on E = 1/(1+10^(−Δ/400)), anchored L1 = 400
```

Adjudication (standard engine-testing practice, as in fishtest/OpenBench):
referee evaluates after each ply from ply 60; |eval| ≥ 500 cp for 4
consecutive plies → win for the leading side; at ply 160 → draw unless
|eval| > 150 cp. Deterministic (referee is the internal analyst at a fixed
budget).

### M2 — Rating-estimator convergence (capability 1)

Simulated players, estimator-in-the-loop (no game play — this isolates
FR-7/8 math and runs in milliseconds): each simulated player has true
internal rating `R*`; game results are Bernoulli draws with
`p = E(R*, level_elo)` (draw probability 0.08 folded in, seeded); per-game
ACPL is drawn from `N(acpl(R*), acpl_std(R*))` where `acpl(·)` inverts the
committed calibration curve. 40 players, `R*` uniform on [400, 1900],
20 games each, controller active.

```
M2a = mean over players of |R_hat_after_game_5  − R*|   (cold-start MAE)
M2b = mean over players of |R_hat_after_game_16 − R*|   in the jump scenario:
      12 players whose R* increases by +300 after game 10 (models improvement)
```

### M3 — Band adherence (capability 1)

Same simulation, `balanced` mode. For games 6–20 (post-warmup):

```
M3 = fraction of (player, game) pairs with E(R*, elo_level_played) ∈ [0.38, 0.62]
```

The gate band is wider than the FR-8 target band (0.45–0.55) because levels
are ~150 internal Elo apart: the best achievable |E − 0.5| at a band edge is
≈ 0.394 for a player exactly between two levels, so [0.38, 0.62] is the
tightest band a perfect controller can always satisfy.

### M4 — Severity-tier accuracy (capability 2)

Over the 120 judgment fixture cases (FEN + played move + ground-truth tier):

```
M4 = (# cases where predicted tier == truth tier) / 120
```

Predictions run the real FR-9 pipeline (analyst at judge budget → win-prob
model → thresholds).

### M5 — Taxonomy macro-F1 (capability 2)

Over the 63 taxonomy fixture cases (each a flagged move with ground-truth
category; ≥ 6 positives per category, 9 categories):

```
P_c = TP_c/(TP_c+FP_c)   R_c = TP_c/(TP_c+FN_c)   F1_c = 2·P_c·R_c/(P_c+R_c)
M5  = mean of F1_c over the 9 MistakeCategory classes   (F1_c = 0 on zero denominators)
```

### M6 — Phase-boundary accuracy (capability 2)

Over the 20 phase fixture games, 38 labeled boundaries (all have a
middlegame start; 18 reach an endgame):

```
M6 = (# boundaries where |pred_ply − true_ply| ≤ 2) / 38
```

### M7 — Analyst tactical adequacy (capability 2, precondition for M4/M5)

Over the 60 tactics fixture positions, analyst at the referee budget:

```
M7 = (# positions where the analyst's move is in the verified correct-move set) / 60
M7a (sub-gate) = accuracy on the 20 mate-in-1 positions
```

### M8 — Suggestion prioritization exactness (capability 2)

Over 12 synthetic aggregation scenarios (pre-built `MoveAnalysis` sets with
hand-computed expected top-3 categories, order, and advice ids):

```
M8 = (# scenarios where ranks 1–3: category, order, and advice_id all match) / 12
```

### D0 — Determinism (plain pytest, no score)

Replay 5 fixture game scripts (seed + player move list) twice end-to-end:
CPU moves, analyses, rating events, and report JSON must be byte-identical.
Any diff fails the suite (FR-16).

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable byte-identically
by committed seeded scripts. **Ground truth never comes from the engine under
evaluation** — it comes from construction parameters, exhaustive enumeration
with python-chess, or hand-computation. Where truth interacts with the eval
function (M4), construction margins make modest disagreement harmless.

| File | Contents | Ground truth |
|---|---|---|
| `ladder_openings.json` | 8 four-ply opening positions (varied structures) + the M1 game seeds | n/a (inputs) |
| `sim_players.json` | 40 simulated player specs (R*, seed) + 12 jump-scenario specs | R* by construction |
| `judgment_cases.json` | 120 cases: FEN, played move (UCI), truth tier, rationale string. Built by `generate_judgment.py --seed 20260731` from parameterized templates: a quiet base position (generator asserts via SEE scan that no capture with \|SEE\| > 0 exists for either side) plus an injected motif whose material consequence is forced within 4 plies (hangs a rook cleanly ≈ −500 cp, drops a pawn ≈ −100, misses a knight fork ≈ −300, …). Truth cp = pre/post forced material delta; truth tier from the win-prob model applied to truth cp. Every case's Δw sits ≥ 0.05 from the nearest tier boundary (≥ 120 cp equivalent in the relevant range), so an analyst within ±80 cp of material truth in quiet positions cannot mis-tier. Composition: 40 ok / 20 inaccuracy / 25 mistake / 35 blunder; 20 of the ok/inaccuracy cases are **decided-position traps** (eval ≥ +800, drop ≤ 300 cp) that punish raw-centipawn implementations. | Construction (forced material) |
| `taxonomy_cases.json` | 63 flagged-move cases: FEN, played move, truth category, truth motif, rationale. Hand-authored from the FR-11 rule definitions so each case satisfies exactly one rule (precedence conflicts are separate deliberate cases asserting the documented order, e.g. a losing capture that also hangs the capturing piece must yield `bad_trade`). ≥ 6 positives per category; motif subtags labeled on the missed/allowed_tactic cases. | Hand-authored + generator assertions (SEE values, attack maps recomputed with python-chess at generation) |
| `phase_games.json` | 20 constructed move-lists (UCI) with truth `mg_start_ply` / `eg_start_ply`. Built by `generate_phase_games.py`: book prefix of known length, scripted development section, scripted trade sequences crossing the piece-count thresholds at known plies. | Construction (threshold-crossing plies are chosen, not discovered) |
| `tactics_suite.json` | 60 positions: 20 mate-in-1, 20 mate-in-2, 20 forced material ≥ +300 within 4 plies; each with the verified set of correct moves. `generate_tactics.py` verifies by **exhaustive minimax over python-chess move generation** (mate-in-N: exact; material: 4-ply full-width search with material-only scoring) — independent of the engine under test. Positions with multiple correct moves store the full set. | Exhaustive enumeration |
| `advice_scenarios.json` | 12 aggregation scenarios: synthetic per-move (category, phase, Δw) sets + expected ranked suggestions. Includes conflict cases: high-frequency/low-Δw vs low-frequency/high-Δw categories (frequency-only ordering gets these wrong), tie-breaks, phase-specific advice resolution. | Hand-computed from the FR-12 formula |
| `game_scripts.json` | 5 (seed, level, player move list) scripts for D0 | n/a |
| `calibration.json` | Full FR-5 calibration record (matches, Elo fit, ACPL stats) | Produced by the committed offline calibration run (see below) |

`generate_calibration.py` is the one long-running script (full-budget
matches, ~1–2 h): it is run offline before release, its output is committed,
and CI **verifies** the ladder cheaply via M1 rather than re-deriving the
calibration. All other generators run in seconds and are re-run + diffed in
CI to prove fixture integrity.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1a adjacent score | all levels share one config (knobs disconnected) | ≈ 0.50 | **≥ 0.60** | Calibrated ~150-Elo spacing predicts E ≈ 0.70 per step; 10-game samples are noisy but seeded/fixed, and a collapsed step (two levels playing identically) sits at ≈ 0.50 — 0.60 separates real ladders from broken ones without demanding fixture-luck. |
| M1b ordering | same | ≈ 0 (rank order arbitrary) | **= 1.0** | Strict monotonicity is the entire point of the ladder; any inversion makes the controller steer backwards. Deterministic fixture, so exactness is fair. |
| M2a cold-start MAE | results-only Elo (K = 32, start 800), no move-quality channel | ≈ 280 (5 games moves the estimate ≤ 160 points even when perfectly right) — constant-800 guess scores ≈ 375 | **≤ 150** | The move-quality channel is the product's "after a few games" promise: per-game ACPL noise maps to ~120–150 Elo σ, so a correct blend reaches ≈ 90–120 MAE by game 5. 150 fails any implementation that ignores or mis-scales move quality, passes an honest one. |
| M2b jump re-lock MAE | results-only Elo as above | ≈ 260 | **≤ 150** | Same signal argument after a regime change; also fails an estimator whose λ freezes (over-trusting stale EWMA). |
| M3 band adherence | fixed L5 for everyone | ≈ 0.22 (only players within ~±90 Elo of L5 comply) | **≥ 0.85** | A perfect controller achieves 1.0 by construction of the band (see M3 note); 0.85 tolerates estimator noise in early post-warmup games while failing sticky or oscillating controllers. |
| M4 severity accuracy | raw-cp thresholds (50/100/300 cp) without the win-prob model | ≈ 0.62 (fails the 20 decided-position traps + boundary cases); all-"ok" scores 0.33 | **≥ 0.90** | Fixture margins (≥ 0.05 Δw from every boundary) mean a correct pipeline mis-tiers only when the analyst mis-solves a ≤ 4-ply forced sequence; M7 gates that independently. 0.90 leaves room for ≤ 12 hard cases while sitting far above the cp-threshold shortcut. |
| M5 taxonomy macro-F1 | always predict `hung_piece` (the most common amateur error) | ≈ 0.04 macro (0.33 on one class, 0 on eight) | **≥ 0.80** | Cases are constructed to satisfy exactly one deterministic rule, so a correct implementation scores ≈ 0.9+; 0.80 tolerates a few precedence/geometry edge cases while failing any missing or mis-ordered detector. |
| M6 phase boundaries | fixed boundaries (mg at ply 17, eg at ply 61) | ≈ 0.26 | **≥ 0.90** | The rule is deterministic over constructed games; ±2-ply tolerance already absorbs boundary ambiguity, so near-exactness is achievable; 0.90 allows 3 misses out of 38. |
| M7 tactics | uniform random legal move ≈ 0.03; greedy highest-SEE capture ≈ 0.28 | 0.03 / 0.28 | **≥ 0.92, M7a = 1.0** | Every position is solvable inside the referee's depth-4 + quiescence horizon by construction; mate-in-1 is table stakes (sub-gate 1.0). 0.92 (≤ 4 misses) covers deep-ish mate-in-2 ordering edge cases without excusing a broken quiescence or TT. |
| M8 prioritization | frequency-only ordering (ignore Δw weighting) | ≈ 0.58 (7/12; conflict scenarios are built to break it) | **= 1.0** | Hand-computed truth for a deterministic formula — partial credit would hide a wrong weighting, same rationale as FormCoach's M6. |

If fixture composition changes, every baseline number in this table must be
re-derived in the same commit (checked in review).

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python chessmentor/evals/run.py        # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest chessmentor/                    # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads fixtures, runs M1–M8 + D0 with offline
  adapters, prints the table with actual values, exits non-zero on any gate
  failure. `--full` additionally re-runs the deep calibration
  (`generate_calibration.py`) and diffs against the committed
  `calibration.json` — release ritual, not CI.
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1a_ladder_separation_fr4_fr5`,
  `test_gate_m2a_cold_start_fr7`, `test_gate_m5_taxonomy_fr11`, …); names
  reference FR ids so the FR → test mapping is auditable.
- `evals/metrics.py` — pure metric functions shared by both entry points.
- **Runtime budget:** M2/M3/M6/M8/D0 run in seconds; M4/M5/M7 evaluate ~240
  positions at the 6k-node judge budget (≈ 3–5 min at 5–20k nps); M1 plays
  90 adjudicated games with full committed level budgets (≈ 8–15 min, the
  top pairs dominating). Whole suite target ≤ 20 min on a laptop; budgets
  and game counts are fixture data, retunable without touching metric code.
- Hermetic: no network, no wall clock (all timestamps from fixtures), seeded
  randomness only; the Stockfish and Lichess live adapters are never
  imported on the eval path.
