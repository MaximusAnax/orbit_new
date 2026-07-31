# ChessMentor — Evals

## What this product lives or dies on

1. **A calibrated ladder and an adaptation loop that locks on fast**
   (FR-4/5/7/8): the difficulty knobs must produce genuinely distinct,
   monotonically ordered strengths on one internal Elo scale, and the blended
   estimator + controller must find the player within a handful of games and
   hold the target band — including when one of its two channels is wrong. If
   this is broken, "adaptive difficulty" is a random-number generator with a
   settings page.
2. **Coaching that is correct on ground truth** (FR-9/10/11/12): severity
   tiers, mistake categories, phase tags, and suggestion priorities must match
   constructed ground truth *and* transfer to messy real positions. A coach
   that calls a winning sacrifice a blunder, or tells you to study endgames
   when you hang pieces, actively harms the user.

Everything else (CRUD, PGN plumbing, CLI rendering) is covered by ordinary
tests, not eval gates.

## Ground rules

- All metrics live in `evals/metrics.py` and run exclusively against committed
  fixtures with offline adapters (`InternalAnalyst`, `CommittedBook`,
  in-memory store). No network, no clock, fixed seeds.
- **One analyst configuration.** Every metric that evaluates positions uses the
  internal analyst at `JUDGE_BUDGET` (6 000 nodes/move) — M1 adjudication, M4,
  M4r, M5, M5r, M7, M10 and the runtime judge pass are the same configuration.
  "Referee" names a *role* (adjudicator), never a different budget.
- **Ground truth never comes from the engine under evaluation.** It comes from
  construction parameters, exhaustive enumeration with python-chess,
  hand-computation, or (for the real-game slices only) developer labelling
  which may use the Stockfish adapter offline — Stockfish is never on the eval
  runtime path.
- **Fixture-seed policy.** `ladder_openings.json` seeds, `sim_players.json`
  and every generator seed may **not** change in the same commit as an
  engine/eval-function change. Seeds are inputs, not tuning knobs; changing
  both at once is how a broken ladder gets seed-shopped into passing.
- **Baseline discipline.** Baselines marked *provisional* below are estimates;
  `evals/baselines.py` computes every fixture-dependent baseline and the
  commit that lands the fixtures replaces the number and re-checks that each
  gate sits meaningfully above its baseline. If fixture composition changes,
  every baseline is re-derived in the same commit (checked in review).

## Metrics

Metric ids map to gates in `evals/test_gates.py`; test names reference FR ids.

### M1 — Ladder ordering and separation (capability 1)

Split deliberately into a **static gate on the committed calibration record**
(where the statistical power is) and a **cheap in-CI check** (where the
freshness is).

**M1b — ladder ordering (static, no games played).** Asserted directly against
`evals/fixtures/calibration.json` and `data/levels.json`:

```
M1b passes iff, over the 9 adjacent pairs:
  (i)   elo_internal is strictly increasing in level id
  (ii)  every gap ∈ [100, 170] Elo
  (iii) every gap ≥ 2.5 × its fitted stderr
  (iv)  calibration.json.levels_sha256 == sha256(data/levels.json)
        and judge_node_budget == JUDGE_BUDGET
        and engine_version == the running engine version
```

The record is produced by `generate_calibration.py` at **60 games per adjacent
pair and 24 per skip-one pair** (FR-5). Why that size: at a 150-Elo gap the
true per-game score is E ≈ 0.703; a 60-game sample has s.d. ≈ 0.059 in score
space, and `dE/dΔ = (ln10/400)·E(1−E) ≈ 0.00121`, so the gap's standard error
is ≈ 49 Elo — a genuine 150-Elo gap clears the 2.5×-stderr test with margin,
while a collapsed pair (true gap 0) cannot. At the 10 games/pair of the
previous draft the per-pair stderr is ≈ 120 Elo: an honest ladder would fail an
exact-monotonicity gate on roughly three quarters of fresh seed draws, and a
collapsed pair would pass on ~38 % of them. Statistical power, not
determinism, is what makes this gate mean something.

**M1a — adjacent separation (in CI).** Every adjacent pair `(i, i+1)` plays
`G = 8` games (4 per colour) from the 8 committed `ladder_openings` positions
with adjudication; `S(i, i+1)` = the stronger level's score.

```
M1a = mean over the 9 adjacent pairs of S(i, i+1)
```

M1a exists to catch *drift* — an engine change that silently breaks the
committed ladder — not to certify ordering. Its s.d. is ≈ 0.054, so it detects
a wholesale collapse (all levels identical → 0.50) but **cannot** detect a
single collapsed pair (mean would be 0.68). That is M1b's job, and the fixture
seed policy above is what stops M1a from being tuned.

*Adjudication* (standard engine-testing practice, as in fishtest/OpenBench):
the referee (internal analyst at `JUDGE_BUDGET`) evaluates after each ply from
ply 60; |eval| ≥ 500 cp for 4 consecutive plies → win for the leading side; at
ply 160 → draw unless |eval| > 150 cp. Deterministic.

*Fit conditioning (used by `generate_calibration.py`, specified here so
`metrics.py` and the generator agree):* pair scores are smoothed
`S' = (points + 0.5)/(G + 1)` before fitting — a sweep would otherwise drive
the fitted delta to infinity — and per-pair deltas are clamped to ±600 Elo.

### M2 — Rating-estimator behaviour (capability 1)

Simulated players, **estimator-in-the-loop, no game play**. This isolates the
FR-7/8 arithmetic and runs in milliseconds. Each simulated player has a true
internal rating `R*`; results are Bernoulli draws with `p = E(R*, level_elo)`
(draw probability 0.08 folded in, seeded); per-game ACPL is drawn from
`N(acpl(R̃), acpl_std(R̃))` where `acpl(·)` inverts the committed calibration
curve and `R̃` is the *channel* rating defined per cohort below.

`R*` is drawn uniform on **[elo_L1 + 80, elo_L10 − 80]**, computed from
`levels.json` at run time rather than hardcoded, so the ladder's actual
calibrated ends define the population (a fixed [400, 1900] range would put
players above the top level, where no controller can comply).

**This is an internal-consistency check of FR-7/FR-8, not a validation of the
ACPL→Elo mapping.** The mapping is assumed by construction here; M10 is what
tests it against real judged games, and SCOPE's "Known limitations" records
that no offline eval can validate it against *humans*.

Cohorts in `sim_players.json`:

| Cohort | n | Games | `R̃` (ACPL channel) | Results channel |
|---|---|---|---|---|
| base | 40 | 20 | `R*` | `R*` |
| jump | 12 | 20 | `R*`, which increases by +300 after game 10 | same `R*` |
| biased | 10 | 20 | `R* − 250` (systematic analyst-model bias) | `R*` |

Jump players' pre-jump `R*` is drawn from `[elo_L1 + 80, elo_L10 − 380]` so the
post-jump rating still lies inside the covered range.

```
M2a = mean over base players of |R_hat_after_game_5  − R*|      (cold start)
M2b = mean over jump players of |R_hat_after_game_16 − R*_post| (re-lock)
M2c = mean over biased players of |R_hat_after_game_20 − R*|    (channel disagreement)
```

M2c is the anti-degeneracy gate: an estimator that deletes the Glicko channel
(λ ≡ 0) converges to `R* − 250` and fails it by construction, while an
estimator that ignores move quality (λ ≡ 1) fails M2a. Only a real blend
passes both.

### M3 — Band adherence, all three modes (capability 1)

Same simulation, base cohort only (jump and biased players are excluded from
the denominator and used only by M2b/M2c). Run once per `ChallengeMode`. For
games 8–20 (post-warmup — the controller may need up to ~7 games to walk from
the cold-start level to a ladder end under FR-8's step limit):

```
elo*(R*, mode) = R* + 400·log10((1 − target_mode)/target_mode)
M3_mode = fraction of (player, game) pairs with |elo_level_played − elo*| ≤ 85
M3      = min over the three modes of M3_mode
```

±85 Elo is exactly the `E ∈ [target ± 0.12]` band; for `balanced` it is the
`[0.38, 0.62]` band of the previous draft. Because FR-5 requires adjacent gaps
≤ 170 Elo and `R*` is drawn 80 Elo inside the ladder ends, **some level is
always within 85 Elo of `elo*`** — so a perfect controller scores 1.0 in every
mode, and that ceiling does not depend on where calibration happens to land
the ladder. Taking the min across modes means a controller with a hardcoded
0.5 target fails M3 outright.

A separate named unit test (`test_fr8_clamps_outside_ladder`) covers players
below L1 and above L10: the recommendation must pin to the ladder end.

### M4 — Severity-tier accuracy (capability 2)

```
M4  = (# of the 120 constructed judgment cases where predicted tier == truth) / 120
M4r = (# of the 30 harvested real-game judgment cases where tier == truth) / 30
```

Predictions run the real FR-9 pipeline (analyst at `JUDGE_BUDGET` → win-prob
model → `SEV_*` thresholds).

### M5 — Taxonomy macro-F1 (capability 2)

Over the 63 constructed taxonomy cases (each a flagged move with ground-truth
category; ≥ 6 positives per category, 9 categories) and the 20 harvested
real-game cases:

```
P_c = TP_c/(TP_c+FP_c)   R_c = TP_c/(TP_c+FN_c)   F1_c = 2·P_c·R_c/(P_c+R_c)
M5  = mean of F1_c over the 9 MistakeCategory classes   (F1_c = 0 on zero denominators)
M5r = the same macro-F1 over the real-game cases (classes absent from the
      slice are excluded from the mean; the slice guarantees ≥ 2 per class)
```

### M6 — Phase-boundary accuracy (capability 2, FR-10)

Over the 20 phase fixture games, 38 labelled boundaries (all have a middlegame
start; 18 reach an endgame):

```
M6 = (# boundaries where |pred_ply − true_ply| ≤ 2) / 38
```

### M7 — Analyst tactical adequacy (capability 2, precondition for M4/M5)

Over the 60 tactics fixture positions, internal analyst at `JUDGE_BUDGET`
(the same configuration M4/M5 and production judging use — otherwise this
metric would certify a config nobody runs):

```
M7  = (# positions where the analyst's move is in the verified correct-move set) / 60
M7a (sub-gate) = accuracy on the 20 mate-in-1 positions
```

### M8 — Suggestion prioritization exactness (capability 2)

Over 13 synthetic aggregation scenarios (pre-built `MoveAnalysis` sets with
hand-computed expected top-3 categories, order, and advice ids):

```
M8 = (# scenarios where ranks 1–3: category, order, and advice_id all match) / 13
```

Scenario 13 is the **analysis-selection** case: a game carrying both an
`internal @ JUDGE_BUDGET` analysis and a `stockfish @ DEEP_BUDGET` re-analysis
with deliberately different Δw values. The expected output is the one computed
from the report-basis analysis only (FR-12); an implementation that takes
"latest" or "deepest" produces a different top-3 and fails.

### M9 — Throttle fidelity (capability 1)

Over every CPU move recorded in the M1a corpus (`cpu_meta`), per level `L`
with `n_L` non-book CPU moves:

```
check (a)  |rate(blunder_rolled) − blunder_prob_L| ≤ max(0.03, 3·√(p(1−p)/n_L))
check (b)  every move with blunder_injected has
           best_score_cp − score_cp ∈ [blunder_margin_lo_cp, blunder_margin_hi_cp]
check (c)  for levels with noise_sigma_cp > 0: rate(noise_changed_pick) ≥ 0.02
M9 = (# checks passing) / (# applicable checks)
```

Without M9 a ladder built purely on node budgets — every `noise_sigma_cp = 0`
and `blunder_prob = 0` — passes M1 while contradicting D2's whole argument
(Maia: depth-limiting alone blunders un-humanly) and US-3's acceptance
criteria.

### M10 — End-to-end rating fidelity (capabilities 1 + 2 composed)

The only metric that exercises the *production* path
`played game → judge pass → ACPL → perf interpolation → Glicko → blend →
controller`. A scripted "player" is literally the engine at level `k`'s
committed config; it plays **6 seeded games** through the real session service
against the controller-selected opponent, for `k ∈ {3, 6, 9}`, starting from a
fresh in-memory store each time.

```
M10 = max over k ∈ {3,6,9} of |R_hat after the 6 games − elo_internal[k]|
```

By construction the level-`k` player's true internal rating *is*
`elo_internal[k]`, so this simultaneously checks the wiring (ACPL over the
right move set, book plies excluded, cap applied, judge budget equal to the
calibration budget, no sign/perspective error) and that the calibrated ACPL
anchors describe real judged games. M2 fabricates ACPL and M4 works on
isolated FENs; neither can catch a wiring bug that corrupts every production
rating.

### D0 — Determinism (plain pytest, no score)

Replay 5 fixture game scripts (seed + player move list) twice end-to-end: CPU
moves, analyses, rating events, and report JSON must be byte-identical. Any
diff fails the suite (FR-16). A companion case asserts FR-2's per-position
contract directly: `search(fen, config, budget)` called before and after an
unrelated search returns the same move, score vector and node count — the
check that the per-call table lifetime is actually implemented.

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable byte-identically
by committed seeded scripts.

| File | Contents | Ground truth |
|---|---|---|
| `ladder_openings.json` | 8 four-ply opening positions (varied structures) + the M1a game seeds | n/a (inputs) |
| `sim_players.json` | 62 simulated player specs: 40 base, 12 jump, 10 biased (see M2 table), each with `R*` and seed; `R*` bounds are expressed relative to `levels.json` and materialised at generation | `R*` by construction |
| `judgment_cases.json` | 120 constructed cases: FEN, played move (UCI), truth tier, rationale. `generate_judgment.py --seed 20260731` composes a quiet base position (generator asserts by SEE scan that no capture with \|SEE\| > 0 exists for either side) at a chosen material offset, plus an injected motif whose material consequence is forced within 4 plies. Truth cp = pre/post forced material delta; truth tier = `SEV_*` applied to `Δw` of the truth cps. **Robustness is verified numerically per case, not asserted in prose** (see below). Composition: 40 ok / 20 inaccuracy / 25 mistake / 35 blunder; 20 cases are **decided-position traps** (`\|cp_best\| ≥ 700`, truth tier ok or inaccuracy, while raw-cp thresholds 50/100/300 would assign a strictly higher tier). | Construction (forced material) |
| `judgment_cases_real.json` | 30 harvested cases: flagged player moves taken from seeded CPU-vs-CPU games (committed `harvest_real.py`, deterministic), hand-labelled by the developer with unlimited analysis time (Stockfish permitted offline). ≥ 6 cases must be ones where the `JUDGE_BUDGET` best move differs from the same analyst's best move at 4× budget (PV instability), and ≥ 8 must contain two or more simultaneous threats. | Developer labelling (documented per case) |
| `taxonomy_cases.json` | 63 flagged-move cases: FEN, played move, truth category, truth motif, rationale. Hand-authored from the FR-11 rule definitions so each case satisfies exactly one rule; precedence conflicts are separate deliberate cases asserting the documented order (e.g. a losing capture that also hangs the capturing piece must yield `bad_trade`). ≥ 6 positives per category. | Hand-authored + generator assertions (SEE values, attack maps recomputed with python-chess at generation) |
| `taxonomy_cases_real.json` | 20 harvested flagged moves from the same seeded games, hand-labelled; ≥ 2 per category, ≥ 8 multi-motif positions where FR-11's precedence order decides the answer. | Developer labelling |
| `phase_games.json` | 20 constructed move-lists (UCI) with truth `mg_start_ply` / `eg_start_ply`. `generate_phase_games.py`: book prefix of known length, scripted development section, scripted trade sequences crossing the piece-count thresholds at chosen plies. | Construction |
| `tactics_suite.json` | 60 positions: 20 mate-in-1, 20 mate-in-2, 20 forced material ≥ +300 within 4 plies; each with the verified set of correct moves. `generate_tactics.py` verifies by **exhaustive minimax over python-chess move generation** (mate-in-N exact; material by 4-ply full-width search with material-only scoring) — independent of the engine under test. | Exhaustive enumeration |
| `advice_scenarios.json` | 13 aggregation scenarios: synthetic per-move (category, phase, Δw) sets + expected ranked suggestions. Includes high-frequency/low-Δw vs low-frequency/high-Δw conflicts (frequency-only ordering gets these wrong), tie-breaks, phase-specific advice resolution, and the two-analyses selection case. | Hand-computed from the FR-12 formula |
| `selfplay_scripts.json` | 18 (k, game_index, seed) triples for M10 | n/a (inputs) |
| `game_scripts.json` | 5 (seed, level, player move list) scripts for D0 | n/a (inputs) |
| `calibration.json` | Full FR-5 calibration record (matches, Elo fit + stderrs, gap stderrs, ACPL stats, integrity hashes) | Produced by the committed offline calibration run |

### Judgment-fixture robustness (replaces the old "≥ 120 cp equivalent" claim)

The previous draft claimed a 0.05 Δw margin was "≥ 120 cp equivalent" and that
an analyst within ±80 cp could not mis-tier. Both are false: at `w ≈ 0.5` the
slope is `WIN_K·w(1−w) ≈ 0.00092` per cp, so 0.05 Δw ≈ 54 cp — and with the
corrected thresholds (0.05/0.10/0.15) the inaccuracy and mistake bands are
only ≈ 54 cp wide, so no construction can be ±80 cp robust. The honest
requirement, which the generator **verifies numerically per case and refuses
to emit otherwise**, separates the two error modes:

- **Common-mode error** (both evaluations shifted together, e.g. a PST bias in
  the base position) barely matters: it moves `w_before` and `w_after`
  together and changes `Δw` only to second order. Requirement: the truth tier
  survives a ±40 cp common-mode shift.
- **Differential error** (the analyst mis-measures the *loss*) is what matters.
  Requirement: the truth tier survives a ±20 cp differential error, evaluated
  jointly with the ±40 cp common-mode shift in all four worst-case sign
  combinations.

This is satisfiable, and the generator's committed (base offset, motif delta)
table hits band centres: `ok` |delta| ≤ 20 cp; `inaccuracy` ≈ 85 cp from a
level base (Δw ≈ 0.078); `mistake` ≈ 150 cp from a −100 cp base (Δw ≈ 0.123);
`blunder` ≥ 250 cp (Δw ≥ 0.21). Worked check for the tightest case (mistake):
truth Δw = 0.123; worst combination (base −140, loss 130) gives 0.104 > 0.10,
and (base −60, loss 170) gives 0.145 < 0.15. The residual sensitivity — a
differential error above ~20 cp on a near-equal position can flip
inaccuracy↔mistake — is real, is what the M4 gate's headroom is for, and is
independently bounded by M7.

`generate_calibration.py` is the one long-running script: full-budget matches
at 60/24 games per pair, ≈ 3–6 h on 4 cores, embarrassingly parallel with
per-game seeds `hash64(calibration_seed, i, j, game_index)` so the record is
identical for any worker count. It runs offline before a release; its output is
committed; CI gates the *record* (M1b) and verifies freshness cheaply (M1a).
All other generators run in seconds and are re-run + diffed in CI to prove
fixture integrity.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1b ladder ordering | all levels share one config (knobs disconnected) → gaps ≈ 0, fail (ii)/(iii) | fails | **must pass** | Strict monotonicity with statistically excluded inversions is the entire point of the ladder; at 60 games/pair a real 150-Elo gap is ≈ 3× its stderr while a collapsed pair cannot clear 2.5×. |
| M1a adjacent separation | same | ≈ 0.50 | **≥ 0.56** | 150-Elo spacing predicts E ≈ 0.703; the 9-pair mean over 8 games/pair has s.d. ≈ 0.054, so 0.56 is 2.65 s.d. below truth (≈ 0.4 % spurious failures) and 1.1 s.d. above a fully collapsed ladder. It intentionally does *not* claim to detect a single collapsed pair (that mean is ≈ 0.68) — M1b does. |
| M2a cold-start MAE | results-only Glicko (no move-quality channel) | ≈ 280 *(provisional)*; constant-`R_INIT` (800) guess ≈ **361** — exactly `[(800−lo)² + (hi−800)²] / (2·(hi−lo))` for `R*` uniform on `[lo, hi] = [elo_L1+80, elo_L10−80]` = [480, 1670] nominal | **≤ 150** | The move-quality channel is the "after a few games" promise. With RD ≈ 143 after 5 games, λ ≈ 0.28, so the estimate is move-quality-dominated and lands ≈ 90–120 MAE. 150 fails any implementation that ignores or mis-scales move quality. |
| M2b jump re-lock MAE | results-only Glicko | ≈ 260 *(provisional)* | **≤ 150** | Needs the FR-7a surprise detector: at the RD floor a Glicko update moves ≈ 20·(s−E) Elo, i.e. ≈ 7 Elo/game at the typical post-jump surprise of s−E ≈ 0.35, so 6 post-jump games recover ≈ 40 of 300 — infeasible without RD re-inflation. After inflation to 150 the update is ≈ 38 Elo/game and λ hands weight back to the fast channel, giving ≈ 100 MAE. This gate is precisely what makes a count-based λ (which freezes near its cap) fail. |
| M2c biased-channel MAE | perf-only estimator (λ ≡ 0) | ≈ 250 (converges to the bias) | **≤ 120** | At the RD floor λ = 90²/(90²+60²) ≈ 0.69, so a 250-Elo channel bias leaves ≈ 77 Elo of offset plus noise. Passing requires a real results channel *and* a λ that grows as RD shrinks; it is the gate that makes deleting Glicko impossible. |
| M3 band adherence (min over 3 modes) | fixed L5 for everyone | ≈ 0.14 (`170 / (elo_L10 − elo_L1 − 160)`, nominal ladder) — *re-derived from calibrated values by `baselines.py`* | **≥ 0.85** | A perfect controller scores 1.0 by construction (gaps ≤ 170 Elo, `R*` drawn 80 Elo inside the ends), so the ceiling is real and not calibration-dependent. 0.85 tolerates estimator noise in early post-warmup games while failing sticky, oscillating, or single-mode controllers. |
| M4 severity accuracy | raw-cp thresholds (50/100/300 cp), no win-prob model | ≈ 0.45 *(provisional — `baselines.py` recomputes)*; all-"ok" scores 0.33 | **≥ 0.90** | Per-case robustness is verified by construction (±40 common-mode / ±20 differential), so a correct pipeline mis-tiers only when the analyst mis-solves a ≤ 4-ply forced sequence (M7 gates that) or lands in the residual near-boundary band. 0.90 leaves 12 cases of headroom. |
| M4r severity accuracy, real slice | same raw-cp baseline | ≈ 0.40 *(provisional)* | **≥ 0.75** | Harvested positions have simultaneous threats and unstable PVs; a lower gate is honest about that while still proving transfer. Constructed-only fixtures cannot show transfer at all. |
| M5 taxonomy macro-F1 | always predict `hung_piece` | ≈ 0.04 macro | **≥ 0.80** | Cases satisfy exactly one deterministic rule, so a correct implementation scores ≈ 0.9+; 0.80 tolerates a few precedence/geometry edge cases while failing any missing or mis-ordered detector. |
| M5r taxonomy macro-F1, real slice | same | ≈ 0.04 macro | **≥ 0.60** | Multi-motif positions are where precedence actually bites; 0.60 macro over ≥ 2-per-class is a meaningful transfer bar without pretending template accuracy carries over. |
| M6 phase boundaries | fixed boundaries (mg at ply 17, eg at ply 61) | ≈ 0.26 *(provisional)* | **≥ 0.90** | Deterministic rule over constructed games; ±2-ply tolerance absorbs boundary ambiguity, so 0.90 (3 misses of 38) is achievable and still strict. |
| M7 tactics | uniform random legal move ≈ 0.03; greedy highest-SEE capture ≈ 0.28 *(provisional)* | 0.03 / 0.28 | **≥ 0.92, M7a = 1.0** | Every position is solvable inside the analyst's depth-6 + quiescence horizon at `JUDGE_BUDGET` by construction; mate-in-1 is table stakes. 0.92 (≤ 4 misses) covers mate-in-2 ordering edge cases without excusing a broken quiescence or TT. |
| M8 prioritization | frequency-only ordering (ignores Δw weighting) | ≈ 0.54 (7/13; conflict scenarios are built to break it) | **= 1.0** | Hand-computed truth for a deterministic formula — partial credit would hide a wrong weighting or a wrong analysis-selection rule. |
| M9 throttle fidelity | budgets-only ladder (σ = 0, p = 0 everywhere) | fails checks (a) and (c) at every throttled level | **= 1.0** | The checks are exact statements about recorded metadata; any failure means a knob the design depends on is not wired. |
| M10 end-to-end rating error | rating from results only, no judge wiring | ≈ 250 Elo after 6 games *(provisional)* | **≤ 175** | The level-`k` player's true rating is `elo_internal[k]` by definition. 175 accommodates 6 games of Glicko noise plus one EWMA'd ACPL channel while failing every wiring bug that shifts ACPL systematically (wrong move set, missing cap, book plies counted, budget mismatch). |

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python chessmentor/evals/run.py        # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest chessmentor/                    # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads fixtures, runs M1a–M10 + D0 with offline
  adapters, prints the table with actual values, exits non-zero on any gate
  failure. `--full` additionally re-runs `generate_calibration.py` and diffs
  against the committed `calibration.json` — release ritual, not CI.
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1b_ladder_ordering_fr5`, `test_gate_m1a_ladder_separation_fr4`,
  `test_gate_m2c_biased_channel_fr7`, `test_gate_m9_throttle_fidelity_fr4`,
  `test_gate_m10_end_to_end_rating_fr7_fr9`, …); names reference FR ids so the
  FR → test mapping is auditable. FRs without a gate (FR-1/6/13/14/15) map to
  named unit tests listed in the module docstring, including
  `test_fr8_clamps_outside_ladder` and the per-mode FR-8 target tests.
- `evals/metrics.py` — pure metric functions shared by both entry points.
- `evals/baselines.py` — computes every fixture-dependent baseline in the table
  above; run in the fixture-landing commit, not in CI.
- **Runtime budget:** M1b/M2/M3/M6/M8/M9/D0 run in seconds (M1b is pure
  assertion over JSON; M9 reads the M1a corpus). M4/M4r/M5/M5r/M7 evaluate
  ≈ 290 positions at `JUDGE_BUDGET` (≈ 4–6 min at 5–20k nps). M1a plays 72
  adjudicated games with committed level budgets (≈ 6–12 min, top pairs
  dominating). M10 plays 18 games plus judge passes (≈ 8–15 min). Whole-suite
  target ≤ 35 min on a laptop; budgets and game counts are fixture data,
  retunable without touching metric code — but never in the same commit as an
  engine change (see fixture-seed policy).
- Hermetic: no network, no wall clock (all timestamps from fixtures), seeded
  randomness only; the Stockfish and Lichess live adapters are never imported
  on the eval path.
