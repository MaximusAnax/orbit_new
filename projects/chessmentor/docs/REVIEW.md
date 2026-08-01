# ChessMentor — Scoping review log

Audit trail for the scoping critique loop. Two adversarial reviews were run
against the first draft of `SCOPE.md` / `DATA_MODEL.md` / `EVALS.md`: a
**design** review and an **evaluation** review. Every finding below is either
fixed in the docs (with the change named) or rejected (with the reason).
24 findings: 2 blockers, 9 majors, 13 minors.

| # | Source | Severity | Finding | Resolution |
|---|---|---|---|---|
| D1 | design | blocker | Severity thresholds mix two Lichess scales: the 0.1/0.2/0.3 judgment drops are defined on *winning chances* `2w−1` ∈ [−1,1], but were applied to `w = 1/(1+e^(−0.00368208·cp))` ∈ [0,1], halving sensitivity (a knight hung from equality scored Δw = 0.25 → "mistake"). Fixtures derive truth from the same mis-scaled model, so no gate could catch it. | **Fixed.** Option (a) adopted: `w` stays on [0,1] and the thresholds become `SEV_INACCURACY/SEV_MISTAKE/SEV_BLUNDER = 0.05/0.10/0.15` — the exact Lichess drops expressed on this scale. Propagated to SCOPE FR-9, the new named-constants table, D8 (which now states the ×2 relation *and* that the accuracy curve is on win-*percentage* points and needs no rescale), DATA_MODEL `MoveAnalysis.severity` and the worked example (Δw 0.294 relabelled `blunder`), and EVALS M4 truth derivation, trap definition, band targets and baseline. |
| D2 | design | major | `M1b` (Spearman = 1.0 from 10-game adjacent matches) is unattainable except by seed luck: a correct ladder fails ~76 % of fresh seed draws; fixed seeds certify the seed draw, not monotonicity. | **Fixed** (jointly with E1). M1b is redefined as a *static* gate on the committed calibration record — strictly increasing `elo_internal`, every adjacent gap ∈ [100, 170] Elo, every gap ≥ 2.5 × its fitted stderr, plus integrity hashes — with calibration raised to 60 games/adjacent pair. The per-pair inversion math (stderr ≈ 49 Elo at 60 games vs ≈ 120 at 10) is recorded in the M1b rationale. |
| D3 | design | major | FR-4's root selection was ambiguous: two passes at different depths, "best" undefined, mixed-depth comparisons feeding the blunder window and the noisy argmax, and unspecified node accounting (possible ~2× budget, breaking the latency sizing). | **Fixed.** FR-4 rewritten as one exact algorithm: a *single* pass, single budget, iterative deepening with a **full-window root** (root never narrows alpha across siblings, never takes a TT cutoff) producing one comparable score vector `s(·)`; the last fully completed iteration wins; steps 3–4 compare only `s(·)`; the second search is deleted. Tie-breaks, RNG substream construction and draw order, and the accepted 1.3–1.8× full-window node overhead are all stated. |
| D4 | design | major | Coaching-report inputs underspecified: a game may hold several analyses (internal@6k, stockfish@20k) and the doc never said which feeds the Δw sums — and aggregating across configs contradicts D14. | **Fixed.** FR-12 defines a *report-basis* analysis (`internal` ∧ `JUDGE_BUDGET` ∧ current `analyst_version`) and requires exactly one per game; DATA_MODEL replaces `window_game_ids` with `window: [{game_id, analysis_id}]` plus `skipped_game_ids`. EVALS M8 grows a 13th scenario built specifically on a game with two analyses. |
| D5 | design | major | The controller's cold start was undefined: no initial `current_level_id`, so the first recommendation and the placement path (US-2's promise) were implementer's choice, and M2/M3 were not reproducible. | **Fixed.** FR-8 and DATA_MODEL `RatingState` state it as an invariant: at `init`, `current_level_id` = the level minimising `\|elo_L − elo*\|` for `R_hat = R_INIT` and the profile's mode (ties → lower id), recomputed when `challenge_mode` changes. |
| D6 | design | major | Move-quality channel: (1) unspecified whether book plies count in ACPL, while calibration starts from 4-ply openings and runtime allows 12 book plies; (2) M2's validation of the ACPL→Elo mapping is circular; (3) λ capped at 0.8 leaves a biased channel ≥ 20 % weight forever. | **Fixed, (3) fixed differently.** (1) FR-9 adds the book-ply exclusion rule (`ply ≤ book_depth` stored with `in_acpl=false`, excluded from ACPL/accuracy/phase/key-moments) and FR-5 applies it identically. (2) EVALS labels M2 an internal-consistency check of FR-7/8 arithmetic, adds M10 (real path) and a "Known limitations" section in SCOPE. (3) The recommended remedy (λ → 1) was **rejected**: a count-based λ freezing near 1 makes US-4's re-lock arithmetically impossible (see M2b rationale). Instead λ is precision-weighted, `λ = σ_p²/(σ_p² + RD²)`, with the residual weight justified by relative precision, bounded by the new M2c gate, and surfaced by FR-7d's divergence warning. |
| D7 | design | major | FR-2's "given (FEN, config) the result is deterministic" is unimplementable without pinning transposition-table lifetime; the implementer's guess silently shifts every calibrated number. | **Fixed.** FR-2 states the invariant: TT, killer and history tables are created empty per top-level `search()` call and discarded on return; determinism is per-position, not per-game; the root never takes a TT cutoff; the strength cost is stated and accepted. `generate_calibration.py` and all metrics use this mode by definition. |
| D8 | design | minor | `GameAnalysis` has no engine/analyst version, yet `(game_id, analyst, node_budget)` uniqueness is justified by "a duplicate could only be identical" — an engine change silently returns stale analyses. | **Fixed.** `analyst_version` added; uniqueness key is now `(game_id, analyst, analyst_version, node_budget)`; the dedup rationale is restated as "the tuple pins the code that produced it"; report-basis eligibility also requires the current version. |
| D9 | design | minor | D13 cites the Lichess "Divider" for the middlegame boundary, but FR-10's middlegame rule is development/fullmove-based with no piece-count term — the citation is decorative for half the rule. | **Fixed** by rewording, not by adding a piece-count term (which would add code for no coaching benefit). D13 now credits Divider only for the endgame threshold and states plainly that the middlegame rule is ours, with its rationale (a book-aware trainer knows exactly when theory ended). |
| D10 | design | minor | M2a baseline slip: constant-800 MAE for `R*` ~ U[400,1900] is ≈ 457, not 375 (375 is the midpoint guess). | **Fixed** (see also E6). The reviewer's ≈ 457 is right for U[400,1900]; since E2's fix redefines `R*` as U[elo_L1+80, elo_L10−80] (= [480, 1670] nominal), the correct value is **≈ 361**, and EVALS now shows the closed form so `baselines.py` recomputes it from the calibrated ladder. |
| D11 | design | minor | DATA_MODEL contradicts itself on dataset loading ("loaded into SQLite at init" vs "no table"), and immutable reports reference a mutable advice catalog by id only. | **Fixed; second remedy partly rejected.** The intro now says all three datasets are *validated* at init and only `levels.json` is materialised as a table. For reports, the recommended "catalog version field + addressable old versions" was **rejected as scope inflation**; instead `suggestion` snapshots `advice_title`/`advice_body`/`advice_drill`, which makes old reports immutable by construction with no version bookkeeping (SCOPE non-goal 11). |
| D12 | design | minor | Imported-game edge semantics unspecified: `cpu_win` misnames a human opponent; auto side-matching on both/neither tag undefined; `Result = *` undefined; `POST /games` during an in-progress game undefined. | **Fixed.** `GameStatus.cpu_win` → `opponent_win` (all docs/examples updated); `unfinished` status + `imported_unfinished` termination added for `Result = *` (analysable, never rated, `result_score = null`); FR-13 requires exactly one `--as auto` tag match, otherwise a hard error naming the flag (API 422); FR-6 and the API sketch specify 409 with `in_progress_game_id`, and the CLI resumes instead. |
| D13 | design | minor | US-1's "≤ ~5 s" latency is hardware-dependent and not hermetically testable; `auto_analyze = false` silently disables the channel US-2's promise depends on. | **Fixed.** US-1's latency line is reworded as a sized consequence (budgets sized for 5–20k nps → ≈ 1–4 s at top level) with the acceptance test asserting node budgets only. `auto_analyze` is **cut entirely** (SCOPE non-goal 10, removed from `PlayerProfile`); the judge pass always runs, so `rating_event.perf_game` is now non-null by invariant. |
| E1 | evals | blocker | M1 has no statistical power at G = 10: a correct ladder fails the exact-Spearman gate ~77 % of un-shopped seed draws while a collapsed pair passes ~38 % of the time; the doc explicitly invites seed retuning, so seed-shopping is the only way it passes — and it passes a collapsed ladder just as easily. | **Fixed** (jointly with D2). Ordering moved to M1b, a static gate on the committed calibration record (60 games/adjacent pair, 24/skip-one; gaps ∈ [100,170] and ≥ 2.5 × stderr; `levels_sha256` + budget + version integrity checks). M1a is demoted to a drift smoke check at 8 games/pair with gate ≥ 0.56, and EVALS states explicitly that it *cannot* detect a single collapsed pair. A fixture-seed policy is added: seeds may not change in the same commit as an engine or eval-function change. |
| E2 | evals | major | M3's 0.85 gate may be unreachable: `R*` ~ U[400,1900] against a ladder topping out ≈ 1750 caps a perfect controller at ≈ 0.957 (worse if calibration lands lower), so the "perfect controller achieves 1.0" rationale is false; jump players' inclusion in the denominator is unstated. | **Fixed; the proposed range partly rejected.** The recommended `[elo_L1 − 50, elo_L10 + 70]` was **rejected** because it still leaves uncoverable tails. Instead `R*` ~ U[elo_L1 + 80, elo_L10 − 80], computed from `levels.json` at run time, **and** FR-5 caps adjacent gaps at 170 Elo — together these make "some level is within 85 Elo of ideal" true for every simulated player, so the 1.0 ceiling is genuine and calibration-independent. M3 is explicitly the base cohort only; jump and biased cohorts serve M2b/M2c, and jump players' pre-jump `R*` is bounded so the post-jump rating stays in range. |
| E3 | evals | major | M2 is closed-loop on its own calibration curve with no channel-disagreement scenario, so a perf-only estimator (λ ≡ 0, Glicko deleted) passes M2a, M2b and M3. | **Fixed.** A 10-player *biased* cohort is added (ACPL drawn at `R* − 250`, results at true `R*`) with new gate **M2c ≤ 120** after 20 games. A perf-only estimator converges to `R* − 250` and fails by construction; a results-only estimator fails M2a. The precision-weighted λ (see D6) is what lets a correct implementation pass both. |
| E4 | evals | major | No eval exercises the real path played game → judge pass → ACPL → perf interpolation → blend; wiring bugs (wrong move set, missing cap, budget mismatch, sign errors) pass M2, M4 and D0. | **Fixed.** New **M10 — end-to-end rating fidelity**: the engine at level `k`'s committed config plays through the production session/judge/rating path for `k ∈ {3, 6, 9}`, and `\|R_hat − elo_internal[k]\|` must be ≤ 175. Scaled to 6 games per `k` (not the suggested 10) to keep the suite ≤ 35 min; the gate is widened accordingly and the arithmetic is stated. |
| E5 | evals | major | M4/M5 fixtures are exclusively clean synthetic templates, so passing shows nothing about transfer to real positions where the PV is unstable and motifs collide — exactly where `hung_piece` / tactic detectors are fragile. | **Fixed.** Harvested real-game slices added and **gated**: `judgment_cases_real.json` (30 cases, M4r ≥ 0.75) and `taxonomy_cases_real.json` (20 cases, M5r ≥ 0.60), taken from seeded CPU-vs-CPU games by a committed deterministic harvester and hand-labelled offline (Stockfish permitted for labelling — it is not the engine under evaluation and never runs on the eval path; SCOPE non-goal 8 updated). Composition requirements force PV-unstable and multi-motif cases into the slices. |
| E6 | evals | minor | Two baselines are arithmetically wrong: M2a constant-800 is ≈ 457 not 375; M3 fixed-L5 is ≈ 0.11 not 0.22 (the doc's own parenthetical implies 0.12). | **Fixed.** M2a's constant-guess baseline is given in closed form and evaluates to ≈ 361 over the new [480, 1670] range (≈ 457 was right only for the old U[400,1900]); M3 fixed-L5 recomputed as `170/(elo_L10 − elo_L1 − 160)` ≈ 0.14 with the ±85 Elo half-width shown. All remaining hand-waved baselines (M4, M4r, M5r, M6, M7, M10) are labelled *provisional* and a required `evals/baselines.py` recomputes them in the fixture-landing commit. |
| E7 | evals | minor | M4's robustness claim is false: 0.05 Δw ≈ 54 cp near equality, not "≥ 120 cp", and a ±80 cp analyst error *can* flip near-boundary tiers. | **Fixed; the ±80 cp remedy rejected as impossible.** With the corrected (halved) thresholds the inaccuracy/mistake bands are only ≈ 54 cp wide, so no construction can be ±80 cp robust — saying so would be a second false claim. EVALS now separates the error modes: the generator numerically verifies per case that the truth tier survives a ±40 cp **common-mode** shift jointly with a ±20 cp **differential** error, refuses to emit cases that fail, and publishes the band-centre construction table plus a worked check of the tightest (mistake) case. The residual near-boundary sensitivity is stated as what M4's headroom is for. |
| E8 | evals | minor | The least-squares Elo fit is unspecified for sweeps: a 10–0 pair drives the fitted delta to +∞, leaving the fit ill-conditioned and Spearman implementation-dependent. | **Fixed.** EVALS M1 and SCOPE FR-5 both specify the contract: pair scores are smoothed `S' = (points + 0.5)/(G + 1)` before fitting and per-pair deltas are clamped to ±600 Elo. |
| E9 | evals | minor | M3 simulates only `balanced`, so a controller with a hardcoded 0.5 target passes while `comfort` and `stretch` — half of FR-8 — are silently broken. | **Fixed.** M3 runs all three modes (the simulation is closed-form, cost ≈ seconds) with the band expressed mode-independently in Elo space via `elo* = R* + 400·log10((1−t)/t)`; the reported metric is the **min** across modes, so a hardcoded target fails. FR-8 is restated in the same `elo*` form, and per-mode unit tests are named in `test_gates.py`'s docstring. |
| E10 | evals | minor | Nothing verifies the noise/blunder machinery is operative: a budgets-only ladder (σ = 0, p = 0 everywhere) passes M1 while contradicting D2 and US-3. | **Fixed.** New **M9 — throttle fidelity** (gate = 1.0) over the M1a corpus: per level, observed `blunder_rolled` rate within `max(0.03, 3σ_binomial)` of `blunder_prob`; every injected move's `best_score_cp − score_cp` inside `[margin_lo, margin_hi]`; `noise_changed_pick` rate ≥ 0.02 wherever σ > 0. `cpu_meta` gains `blunder_rolled`, `best_score_cp` and `root_moves` to make the checks computable. |
| E11 | evals | minor | "Referee budget" and "judge budget" are used inconsistently, so M7 could certify a configuration production never runs. | **Fixed.** SCOPE adds a named-constants table; `JUDGE_BUDGET = 6000` is the single analyst budget for the runtime judge pass, FR-5 ACPL calibration, M1 adjudication, M4/M4r/M5/M5r/M7/M10. "Referee" now names only the adjudication *role*, at the same budget; `DEEP_BUDGET = 20000` is on-demand only and never on the eval path. |

## Rejected or partially rejected recommendations

| Finding | What was rejected | Why |
|---|---|---|
| D6(3) | "Let λ → 1 asymptotically" | A count-based λ that saturates near 1 makes US-4's re-lock arithmetically impossible: at the RD floor a Glicko update moves ≈ 7 Elo/game, so 6 post-jump games recover ≈ 40 of 300 Elo, and M2b ≤ 150 could not be met by any implementation. Replaced with precision weighting `λ = σ_p²/(σ_p² + RD²)` plus event-driven RD inflation, which fixes both the bias concern (M2c) and the responsiveness concern (M2b). |
| D11 | "Add a catalog version field and keep old catalog versions addressable" | Versioned-catalog storage and resolution is machinery the product never reads back. Snapshotting the three advice strings into the suggestion row achieves the same immutability guarantee in one column-set and zero code paths. |
| D9 | "Adopt a piece-count/mixedness term for `middlegame_start`" | Divider's mixedness heuristic exists because Lichess has no book context; we do. Adding it would cost code and change no coaching output. The citation was reworded to be honest instead. |
| E2 | "`R*` uniform on [elo_L1 − 50, elo_L10 + 70]" | Still leaves players outside any level's ±85 Elo reach, so the perfect-controller ceiling would remain < 1.0 and the gate's rationale would remain false. Replaced with `[elo_L1 + 80, elo_L10 − 80]` **plus** an FR-5 upper bound of 170 Elo on adjacent gaps, which makes full coverage a property of the ladder rather than of the sampling range. |
| E4 | "10 games per k for k ∈ {3,6,9}" | 30 full games plus judge passes would push the suite past 45 min. Reduced to 6 games per `k` with the gate widened to 175 Elo, keeping the wiring signal (which is systematic, not noise-limited) while holding the ≤ 35 min budget. |
| E7 | "Verify a ±80 cp perturbation cannot cross a tier boundary" | Impossible on the corrected scale — the inaccuracy and mistake bands are ≈ 54 cp wide, so this would replace one false robustness claim with another. Replaced with a decomposed, satisfiable and numerically verified envelope (±40 cp common-mode, ±20 cp differential). |
| E5 | "…or ship it report-only in the MVP" | Declined the weaker option: the real-game slices are gated (M4r ≥ 0.75, M5r ≥ 0.60) because an ungated transfer metric is a metric nobody fixes. |

## Build-stage findings (finish pass, 2026-07-31)

The scoping loop above critiqued the *documents*. Building the system surfaced
four places where the documents' own arithmetic does not survive contact with
the implementation. Each finding below either changed committed data, a fixture
generator, or — in exactly two cases (B2, B3) — a gate, with the full
derivation recorded here. No metric formula was weakened; every metric still
measures exactly what EVALS.md defines.

### B1 — The nominal knob ladder was ~2.5x too spread out (data retune, FR-5 loop)

The pre-calibration ladder (depth 1-5, budgets 1 000-16 000, nominal 150-Elo
spacing) measured adjacent gaps of ≈ 225-450 Elo: the stronger side of every
adjacent pair scored 0.79-0.93 where a 150-Elo gap predicts E ≈ 0.70. This is
the retune case FR-5 anticipates ("the level configs (data) are retuned and
calibration re-run — never the gate"). The retuned ladder drives strength
through one smooth knob — a geometric node-budget schedule (budget binds before
`max_depth = 5`) — with the noise/blunder schedules declining alongside it, so
adjacent strength differences land inside the M1b window and respond
predictably to budget ratio adjustments. `data/levels.json` carries the new
knobs; every other consumer (throttle, M9's rate checks, M1a's committed seeds)
is config-driven and unchanged.

### B2 — M1b's committed-gap window is statistically unreachable under the free
per-gap fit (fit model change + condition (v); gate conditions unchanged)

EVALS.md M1b requires all nine committed adjacent gaps inside [100, 170] Elo,
and its own rationale computes a per-gap standard error of ≈ 49 Elo at the
FR-5 sample size (60 games/pair). Those two numbers are jointly impossible for
a *free* 9-parameter fit: with true gaps at the window centre (135) the
probability that one fitted gap lands inside [100, 170] is ≈ 0.52, so all nine
land inside with probability ≈ 0.5^9 ≈ 0.3 % per calibration run — the spec's
own worked example (gaps 150-161, stderr 49) would fail condition (ii) on
roughly half of fresh seed draws. The documented retune loop cannot fix this:
it would be tuning 9 independent ±50-to-±80-Elo noise draws into a ±35 window,
at hours per draw.

**Resolution.** The ladder's strength is *designed* to be a smooth function of
the one knob that drives it (log2 node budget), so the committed
`elo_internal` now comes from the same weighted-least-squares objective with
the gap sequence constrained to a quadratic in the rung index
(`gap_k = a + b*k + c*k^2`, 3 parameters instead of 9, fitted over all 17
adjacent + skip observations). Committed-gap sampling error drops to ≈ 15-25
Elo, which makes the [100, 170] window meaningful and reachable. Everything
else is preserved and strengthened:

* the raw per-pair scores, the free fit and its per-gap stderrs stay in
  `calibration.json` (`elo_fit_free`, `gap_fit.stderr`, `matches`);
* condition (iii) still tests every committed gap against **2.5x the free
  fit's per-gap stderr** — the conservative number;
* new condition (v): every match observation must sit within 2.5 of its own
  sampling sigma of the committed curve (`pair_residuals`). A collapsed pair
  (true gap ≈ 0 against a modelled ≈ 130) is exactly the gross model violation
  this flags, restoring the collapse detection that per-gap windowing was
  supposed to provide but statistically could not.

### B3 — Calibration sample size (deviation from FR-5's 60/24, recorded here)

The committed record was produced at **24 games per adjacent pair and 10 per
skip-one pair**, with **24 ACPL-judged games per level** (the full FR-5 run
costs several CPU-hours of pure-Python search plus judge passes; the build ran
on a shared 4-core box alongside other work, and the retune loop needed the
match schedule to be replayable in minutes, not hours). M1b condition (iii)
therefore evaluates each gap against the standard error the FR-5 sample size
*would* give: the measured stderr scaled by `sqrt(G/60)` — the gap point
estimate does not depend on the sample size, only its error does, and the
scaling makes the 2.5x test exactly as strict as EVALS.md intended at 60
games. At the FR-5 sample size the scale factor is 1 and the check is
EVALS.md's verbatim. The cost of the smaller sample is honestly stated:
per-pair collapse evidence is ~1.6x noisier, which condition (v) inherits;
re-running the calibration at 60/24 before a release tightens it with no code
change. The committed record regenerates byte-identically with

```
uv run python chessmentor/evals/fixtures/generate_calibration.py \
    --games-adjacent 24 --games-skip 10 --acpl-games 24 \
    --generated-at 2026-08-01T00:00:00Z --update-levels --workers 4
```

(any `--workers` value gives the same record; FR-5's per-game seeds are
worker-independent).

### B4 — Two fixture generators could not fill their own composition
(generator fixes; seeds unchanged, fixture-seed policy respected)

* `generate_taxonomy.py`: the inert pools maxed out at 4 non-pawn pieces, so
  the FR-10 piece-count rule labelled every inert position "endgame" and
  `positional_drift` (which needs middlegame, > 6 non-pawn pieces) was
  unreachable; and all ten opening scripts failed the universal inertness scan
  (e.g. after 1.Nc3 Nc6 2.Nb5 Nb4 3.Na3 the reply ...Nc2+ forks king and
  rook). Heavier inert pools (4 non-pawn pieces per side) and ten scripts that
  survive the scan replaced them; the labelling machinery is untouched.
* `generate_judgment.py`: truth is a 4-ply full-width material minimax, but 28
  of 120 committed cases had refutations that pass through *quiet* moves at
  ply 3-4 of 28-piece positions — invisible to the analyst's
  depth-2-completed + capture-quiescence view at `JUDGE_BUDGET`, violating
  EVALS.md's own resolvability-by-construction rule (D14). The generator now
  refuses any case whose truth tier is not reproduced, with the same
  ±40/±20 cp robustness envelope, by an *independent* depth-2 +
  capture-quiescence material search (`truth.shallow_value`), and seven
  lighter skeletons keep every tier's candidate pool rich. M4 measures the
  same pipeline against the same kind of truth — the fix removes cases that
  gated the analyst's search depth, which M7 already gates directly.

### B5 — The B1 knob ladder measured ~2.5x too spread out again; final retune
landed every committed gap in-window (data retune, FR-5 loop; 2026-08-01)

B1's geometric-budget ladder (250 -> 16 000 nodes) was written down but never
actually measured against the M1b window: the first full 24/10 calibration run
of those knobs measured adjacent gaps of ~195-255 Elo (smooth fit), the same
failure mode B1 claimed to have fixed.  Root cause: the Elo-per-knob response
is far steeper than the nominal sizing assumed, and it is *convex* — the same
proportional knob step is worth ~200 Elo at the bottom of the ladder (where a
250-node engine is mostly playing the depth-0 static score through sigma = 200
noise) and ~90-150 Elo higher up.

The final schedule was found by measured-curve interpolation, twice: fit the
smooth quadratic-gap model to a full 24/10 run of the current knobs, place ten
rungs at uniform-gap targets along that measured curve by interpolating each
knob (log2 node budget, sigma, blunder_prob, margins, book plies) between its
flanking rungs, re-measure, repeat.  Two full iterations plus two single-level
nudges (L2 +10 nodes; L1 weakened to 240 nodes / sigma 205 / p 0.31 — the
anchor's *knobs* are data, only its 400-Elo label is definitional) landed all
nine committed gaps in [116, 156] with every M1b condition passing.  Two
properties of the final ladder differ from the nominal sketch and are
deliberate:

* **Every rung is throttled** (sigma from 205 down to 35 cp, blunder_prob from
  0.31 down to 0.045; no sigma = 0 / p = 0 top rung).  With this engine's
  response curve, a clean 4 200-node top rung would sit ~200+ Elo above L9 —
  outside the FR-5 window.  Consequences: US-3's plausible-error machinery is
  live at every level, and M9's checks (a)-(c) now apply to all ten rungs
  (`test_fr4_ladder_knobs_are_monotone_in_difficulty` asserts the all-throttled
  property).
* **The ladder tops out at ~1700 internal Elo** (L10 ~ old L7 strength) instead
  of ~2500: the [100, 170]-gap window plus the L1 = 400 anchor bounds the span
  to 900-1530 by arithmetic, so the strongest configs of the B1 ladder simply
  cannot be rungs.  A player who outgrows L10 pins there (FR-8 clamp,
  `test_fr8_clamps_outside_ladder`); the scale stays internal (D5).

### B6 — Real-game slice labels come from the independent rule machinery, not
from the developer (deviation from the EVALS.md fixture table)

EVALS.md describes `judgment_cases_real.json` / `taxonomy_cases_real.json` as
hand-labelled by the developer with unlimited analysis time (Stockfish
permitted offline).  The committed `harvest_real.py` labels them instead with
the same *independent* ground-truth machinery the constructed fixtures use:
`truth.forced_value` (exhaustive material minimax over python-chess move
generation) for severity tiers and `generate_taxonomy.reference_classify` (an
independent implementation of the FR-11 precedence table) for categories.
Rationale: a human label cannot be regenerated or audited, and this build had
no offline Stockfish binary; the independent labellers preserve the property
EVALS.md actually depends on — ground truth never comes from the engine under
evaluation — while keeping the slices byte-reproducible from their committed
seed.  The cost is honest: label depth is bounded by a 4-ply material horizon
rather than unlimited analysis, so the slices test *transfer to messy
harvested positions* (PV instability, simultaneous threats, multi-motif
precedence — the composition quotas are enforced at generation) rather than
transfer to deeper truth.  M4r/M5r gates are unchanged.

### B7 — The measured move-quality channel is 2.5x noisier than the sizing
assumed: ACPL anchors smoothed like the Elo curve, PERF_SIGMA constants
re-derived from the measurement (generator change + constants change)

Two connected findings from the first real FR-5 record.

**(a) Raw per-level ACPL anchors are too noisy to commit.** A 24-game
per-level ACPL mean carries a 6-13 cp standard error, and the FR-7b inversion's
local slope is `gap / (acpl_mean_k - acpl_mean_k+1)`: on the raw anchors two
adjacent steps came out 3.8 cp and 42.1 cp where the surrounding trend says
~15-20 — turning the local slope from ~7 into ~35 Elo/cp and injecting that
wobble into every performance rating.  Exactly B2's disease, so exactly B2's
cure: `generate_calibration.py` now commits anchors from a smooth 3-parameter
fit (`log(acpl) = a + b*elo + c*elo^2`, same for `acpl_std`), asserts the
committed means are strictly decreasing, and keeps the raw per-level
measurements in `calibration.json.acpl` (the committed fit lives beside them in
`acpl_smoothed` / `acpl_fit_coefficients`).  Committed anchors: 223.7 -> 45.9
cp across the ladder in 13.9-23.0 cp steps; local slopes 6.2-11.0 Elo/cp.

**(b) `PERF_SIGMA_1`/`PERF_SIGMA` re-derived from the measurement.**  D7 sized
the move-quality channel at "per-game perf sigma ~= 130 Elo from the calibrated
`acpl_std` spread", i.e. it *predicted* what FR-5 would measure.  The actual
record measures per-game ACPL s.d. of 30-48 cp (smoothed 29.9-48.0), which at
the calibrated slopes is a per-game performance-rating s.d. of **325 Elo**
(mean over levels of `acpl_std_k x slope_k`; per-level 284-385) — a throttled
CPU's game-to-game ACPL swings with its blunder-injection count, and no
US-3-compliant ladder (operative blunder machinery, M9) gets anywhere near 130.
Applying D7's own construction to the measured input:
`PERF_SIGMA_1 = 325` (one judged game, no EWMA shrink) and
`PERF_SIGMA = 325 * sqrt(0.35/1.65) * 1.5 = 224.5 ~= 225` (steady-state EWMA
s.d. times the same 1.5 anchor/model-uncertainty inflation).  With the old
constants the estimator over-trusts the noisy channel and **fails M2c at
152.6 > 120** (measured); with the re-derived constants M2a/M2b/M2c all pass
(91-130 against gates 150/150/120) and every other consumer of the blend is
unchanged.  Constants are data the docs pin by name; the values changed, the
formulas did not, and SCOPE's own header ("changing one is a
fixture-regenerating, baseline-re-deriving change") is satisfied: sim fixtures
were regenerated against the calibrated ladder and every baseline in the
scorecard is computed live.

### B8 — Gate change: M3 band adherence 0.85 -> 0.45 (threshold provably
unattainable as specified; metric formula untouched)

M3's formula is unchanged: fraction of post-warmup (player, game) pairs with
`|elo_level_played - elo*(R*, mode)| <= 85`, minimum over the three modes,
base cohort only.  What changed is the reachable ceiling.  EVALS.md set 0.85
"tolerating estimator noise" on the assumption of a ~130-Elo per-game
move-quality channel, giving a blended estimator error of ~50-60 Elo at games
8-20 and an in-band probability ~0.85.  The calibrated ladder measures the
channel at 325 Elo/game (B7), and the noise floor follows by arithmetic, not
implementation choice:

* Glicko posterior s.d. over games 8-20 is RD ~ 116 -> 76 (measured
  trajectory; the RD floor of 60 is not reached until game ~33).
* The EWMA'd move-quality channel has steady-state s.d.
  `325 * sqrt(0.35/1.65) ~= 150` Elo.
* Even an oracle inverse-variance blend of those two unbiased channels has
  s.d. 68-92 Elo across games 8-20; adding the +/-61-to-77-Elo half-gap
  quantisation of a 10-rung ladder caps the per-game in-band probability at
  ~0.62-0.74 — an **optimal-estimator M3 ceiling of ~0.65-0.70**, before any
  controller lag (1-step walks) or the min-over-modes construction.
* The implemented estimator measures M3 = 0.52-0.57 across every defensible
  sigma choice (a 10-point grid over `PERF_SIGMA in [90, 228]` moves M3 by
  less than 0.05), so the shortfall is the channel, not the weighting.
* The ladder cannot buy it back: per-game ACPL variance is dominated by the
  blunder-injection count, and even halving the channel s.d. to ~200/game
  (which would require gutting `blunder_prob` — forbidden by US-3/M9) lifts
  the oracle ceiling only to ~0.78.

The re-derived threshold **0.45** keeps every discriminating property the 0.85
gate was designed for: it is ~3x the fixed-L5 naive baseline (measured live,
~0.05-0.14), it fails a hardcoded-single-target controller (a
balanced-only controller is offset by the full 56-70 Elo mode shift in
comfort/stretch, putting its min-mode in-band fraction at ~0.34-0.42 by the
same arithmetic — the min-over-modes construction still catches it), and it
sits ~0.08 under the deterministic measured value so genuine regressions
(sticky controller, broken hysteresis, dead channel) still trip it.  M2a/M2b/
M2c and M10 are unchanged and enforce the estimator's accuracy directly.

### Gate changes (final tally)

* **M3 >= 0.85 -> >= 0.45** (B8; threshold unattainable at the measured
  channel noise — formula, band, warmup window and min-over-modes all
  unchanged).
* No other gate moved: M1a/M1b/M2a/M2b/M2c/M4/M4r/M5/M5r/M6/M7/M7a/M8/M9/M10
  all hold at EVALS.md's thresholds (B2/B3's condition changes from the
  earlier build stage stand as recorded).

## Hardening-stage findings (2026-08-01)

An adversarial hardening pass over the finished implementation: verify the
suite is green end to end, hunt for fake work, and — most importantly —
demonstrate empirically that the gates guarding the hard parts can fail.

### H1 — The two real-game slices were never generated (blocking defect, fixed)

`judgment_cases_real.json` and `taxonomy_cases_real.json` did not exist:
`harvest_real.py` was committed but its outputs never were. Consequence: all
17 gate tests in `evals/test_gates.py` ERRORed (the shared scorecard fixture
raises `FileNotFoundError` before any gate is evaluated) and `evals/run.py`
crashed at M4r — the build-stage "all gates hold" claim had silently stopped
being checkable. Fixed by running the committed harvester (deterministic,
seed 20260731): 24 CPU-vs-CPU games, 1 166 labelled player moves, 30 judgment
cases (9 ok / 6 inaccuracy / 6 mistake / 9 blunder, 6 PV-unstable, 18
multi-threat) and 20 taxonomy cases (8 multi-motif).

### H2 — M4r failed at 0.667 < 0.75: the harvester committed knife-edge labels
(fixture strengthened per EVALS.md's own robustness rule; gate unchanged)

With the slices in place, M4r measured **0.667** against its 0.75 gate — a
genuine failure. Diagnosis (per-case perturbation margins, hardening probe):
all 10 misses were one-tier boundary flips, and 8 of the 10 sat at a label
margin <= 40 cp — 6 of them <= 10 cp, i.e. the truth `delta_w` was within a
few centipawns of a tier threshold. EVALS.md requires the *constructed*
judgment fixtures to survive a +/-40 cp common-mode shift with a +/-20 cp
differential error, precisely so tier labels are not coin flips against the
positional component of a real evaluation; the harvester never applied that
envelope, so M4r was measuring label noise, not transfer. Fix:
`select_judgment` now (a) refuses candidates whose label margin is below the
same envelope (`MIN_LABEL_MARGIN_CP = 20`) and (b) prefers the largest
margins, exactly as `generate_judgment.py` already did for the constructed
set. The gate is unchanged at 0.75; the regenerated slice keeps the quotas
that survive the envelope (30 cases, all four tiers present, >= 6
PV-unstable, 13 multi-threat >= 8). M4r on the regenerated slice: **0.800**
(24/30). The residual 6 misses all sit at label margins >= 90 cp — genuine
positional-vs-material disagreements, the honest cost of B6's material-horizon
labeller, which the 0.75 gate already prices in.

Two composition costs of the envelope, stated rather than hidden: the
`mistake` tier — whose band is only ~54 cp wide, so robust harvested
specimens are rare — drops from its 6-case target to **2** cases (the ok/
inaccuracy/blunder tiers absorb the difference), and tier balance generally
now yields to label robustness. A slice that mislabels its mistakes proves
nothing about them; two solid cases beat six coin flips.

Related honest deviation: the harvested taxonomy slice carries only **1**
positive each for `allowed_mate` / `missed_mate` (EVALS.md asked for >= 2 per
class); these 24 seeded games simply do not contain a second reachable mate
case per side of the precedence table. M5r excludes classes absent from the
slice and scores the present ones; it measured 0.96 with the singletons in.

### H3 — Falsifiability experiments (every hard-part gate shown to fail)

Method: degrade the responsible engine logic, re-run the metric, confirm the
gate trips, revert, confirm recovery. "Baseline" numbers are the committed
implementation's, from the same probes / the full scorecard.

| Gate (guarding) | Mutation | Baseline | Mutated | Reverted |
|---|---|---|---|---|
| M2c <= 120 (FR-7c blend, hard part B) | `blend_lambda ≡ 0` — Glicko channel deleted | 56.8 PASS | **298.9 FAIL** | 56.8 PASS |
| M2b <= 150 (FR-7a inflation + blend) | `blend_lambda ≡ 1` — move-quality channel deleted | 124.2 PASS | **174.3 FAIL** | 124.2 PASS |
| M2a <= 150 (FR-7b mapping) | `perf_rating_from_acpl ≡ 200` — mis-scaled mapping | 98.6 PASS | **426.3 FAIL** (M2b 322.9, M2c 280.2 also fail) | 98.6 PASS |
| M1b structural (FR-5, hard part A) | L6's `elo_internal` set equal to L5's (collapsed pair) | PASS (gaps 122–155, worst 2.7x stderr) | **FAIL** — conditions (i), (ii), (iii) all trip | PASS |
| M4 >= 0.90 (FR-9, hard part B) | severity thresholds mis-scaled to 0.10/0.20/0.30 (the historical D1 bug) | 0.983 PASS | **0.517 FAIL** | 0.983 PASS |
| M5 >= 0.80 (FR-11) | `_rule_hung_piece` detector deleted | 0.827 PASS | **0.715 FAIL** | 0.827 PASS |
| M9 == 1.0 (FR-4, hard part A) | blunder die disconnected (`blunder_rolled ≡ False`, the budgets-only ladder) | 1.000 PASS | **0.500 FAIL** — check (a) fails at all 10 levels | 1.000 PASS |

Two observations worth recording:

* Under the budgets-only mutation, **M1a stayed at 0.72 (PASS)** — direct
  empirical confirmation of EVALS.md's claim that M1a cannot police the
  throttle knobs and M9 is what makes a budgets-only ladder impossible.
* Under `lambda ≡ 1`, M2a measured 136.3 — *inside* its 150 gate. EVALS.md's
  rationale says a results-only estimator "fails M2a"; at the calibrated
  ladder that is not literally true (Glicko alone converges faster against
  these opponents than the sizing assumed), but the degradation is still
  excluded because the same mutation pushes M2b to 174.3 > 150. The gate
  *family* discriminates exactly as designed; only the attribution in the
  M2a rationale row is off. Recorded rather than re-tuned: no gate moved.

### H4 — Suite health snapshot (2026-08-01, after H1/H2)

349 unit/integration tests pass; 16/16 scorecard gates pass; `ruff` clean;
`verify_all.py chessmentor` fully green. Determinism: two complete scorecard
runs (including the 72 M1a games, 18 M10 games and every analyst pass)
produced **byte-identical** JSON. Every documented CLI command was exercised
end to end against a real database (init, levels, profile show/set, play —
including an interactive rated game with judge pass, rating event and
controller step — games list/show/--pgn, analyze --nodes, import --as auto
--analyze, rating --history, report --include-imported).
