# FormCoach — Evals

## What this product lives or dies on

1. **Form-fault detection from pose keypoints** (FR-7/8/9/10/15): given a noisy
   keypoint time-series, segment reps correctly, measure joint geometry
   accurately, and emit exactly the faults that are really there — refusing on
   unusable input and *only* on unusable input. If this is wrong, the flagship
   feature is worse than useless (it mis-coaches).
2. **Science-compliant programming and progression** (FR-3/6): every generated
   program must satisfy the research-derived constraints it claims to encode
   (volume bands, coverage, frequency, RIR ramp, deload, rep ranges), and
   progression decisions must follow the FR-6 ladder exactly. If this is wrong,
   "science-based" is marketing.

Everything else (CRUD, media manifest plumbing, CLI) is covered by ordinary
tests, not eval gates.

## Design rule: the eval never lets the engine define its own denominator

Two loopholes are closed by construction and are the reason several metrics
below look more elaborate than usual:

- **Applicability is ground-truth-defined.** `labels.json` enumerates, for
  every valid clip, every `(clip, rep, rule)` triple and marks it
  `assessable` or `not_assessable` from the *generator's* knowledge of per-frame
  confidences, injected occlusion, and the rule's declared views. `metrics.py`
  uses that list — **never** the engine's screening — as the M1/M3/M7
  denominator. An engine cannot shrink its own exam.
- **Program coverage is eval-owned.** M5 constraint 15 checks the muscles that
  must receive real volume against `evals/fixtures/required_coverage.json`, a
  hand-authored file that is *not* derived from `data/target_muscles.json`. A
  generator that picks a convenient target set fails.

M4b (never guess where GT says you can look) and M7 (don't refuse where GT says
you can look) are deliberately adversarial to each other: no refusal policy
passes both unless it matches ground truth.

## Metrics

All metrics live in `evals/metrics.py` and are computed exclusively against
committed fixtures with the offline adapters (`FixturePoseEstimator`,
`LocalMediaResolver`, `SQLiteRepository(":memory:")`). No network, no clock,
seeds fixed.

### Rep alignment (shared by M1 and M3)

Because M2 tolerates a few wrong rep counts, predicted and true reps must be
paired explicitly. `align_reps(true_reps, pred_reps, fps)` is the single shared
function:

1. Build all `(true_i, pred_j)` pairs with
   `|extremum_frame_true − extremum_frame_pred| ≤ 0.4 · fps`.
2. Sort pairs by that absolute difference ascending, then by `true_i`, then
   `pred_j`; take greedily, skipping any pair whose true or pred rep is already
   matched.
3. **Unmatched true rep:** every one of its `assessable` triples with
   `truth = fault_c` counts as `FN_c`; every one of its ground-truth feature
   instances takes the M3 miss penalty.
4. **Unmatched predicted rep:** every fault it flags counts as `FP_c`. Its
   measurements are ignored by M3 (they have no ground truth).

Photos (`analysis_kind = photo`) have exactly one true and one predicted rep;
alignment is trivial but goes through the same function.

### M1 — Fault-detection macro-F1 (capability 1)

Unit of prediction: a `(clip, rep, rule)` triple that `labels.json` marks
**assessable**. Fault classes are the pair `(exercise_id, fault_id)` — **10
classes** (squat ×4, deadlift ×3, push-up ×3), each with ≥ 10 positive
instances in the fixture set.

```
TP_c = #(truth = fault_c ∧ pred = fault_c)
FP_c = #(truth = ok      ∧ pred = fault_c)   [+ unmatched-pred-rep flags]
FN_c = #(truth = fault_c ∧ pred ∈ {ok, not_assessed})   [+ unmatched-true-rep faults]
P_c  = TP_c/(TP_c+FP_c)   R_c = TP_c/(TP_c+FN_c)   F1_c = 2·P_c·R_c/(P_c+R_c)
M1   = mean(F1_c) over the 10 classes            (F1_c = 0 if either denominator is 0)
```

`pred = not_assessed` on an assessable triple counts as a miss here **and** is
charged separately by M7, so refusing the hard cases is doubly penalized.

### M1b — Worst-class F1 floor (capability 1)

```
M1b = min over the 10 classes of F1_c
```

Macro-F1 alone hides a dead rule: with nine classes at ~0.90 and one at 0.00,
macro-F1 is still ≈ 0.81 and would pass. M1b makes a never-implemented or
silently broken rule fail immediately.

### M2 — Rep-count exact accuracy (capability 1)

Over the `N_valid` clips that **`labels.json`** marks valid (not the clips the
engine accepts):

```
M2 = (1/N_valid) · Σ 1[pred_rep_count == true_rep_count]
```

A clip the engine wrongly rejects contributes 0 (its `pred_rep_count` is null).

### M3 — Feature-measurement error (capability 1)

Over **every** `(rep, feature)` instance that `labels.json` carries ground truth
for, on assessable triples:

```
M3a = mean |measured_deg  − true_deg |    angle features: trunk_lean_deg, fppa_deg,
                                          elbow_angle_deg, hip_ext_angle_deg
M3b = mean |measured_frac − true_frac|    ratio features: depth_ratio, lateral_shift_frac,
                                          bar_drift_frac, hip_dev_frac, hip_shoulder_rise_ratio
```

**Miss penalty:** if the engine produces no measurement for an instance
(rule reported `not_assessed`, rep unmatched, or the metric key is absent from
`RepAnalysis.metrics`), the instance enters the mean with a fixed penalty of
**15.0°** (M3a) or **0.09** (M3b) — 3× the gate. The denominator is therefore
fixed by ground truth and cannot be shrunk by declining to measure.

### M4 — Screening and view resolution (capability 1, "refuse to guess")

A composite over clip-level decisions:

```
M4 = ( #GT-valid clips accepted
     + #GT-invalid clips rejected with the correct reason
     + #undeclared-view clips whose resolved view == labels.json view ) / (58 + 4)
```

Reasons must match: `insufficient_visibility` for occlusion clips,
`insufficient_visibility` for the out-of-frame clip (the FR-7 screening rule
covers both), so the check is on reason equality with the label.

### M4b — Never guess where you cannot look (capability 1)

Over every `(clip, rep, rule)` triple `labels.json` marks **not assessable**
(wrong view for the rule, injected occlusion of the rule's
`feature_keypoints`, or `phase_not_shown` on a photo):

```
M4b = #(pred = not_assessed) / #not-assessable triples
```

This consumes the DATA_MODEL complete-matrix invariant (one `FaultFinding` row
per rule per rep). Front-view squat clips make every side-view squat rule
non-assessable and vice versa, so the material costs no extra fixtures.

### M7 — Unjustified refusal rate (capability 1)

```
M7 = #(pred = not_assessed on an assessable triple) / #assessable triples
```

Lower is better. Together with M4b this pins refusal behavior from both sides.

### M5 — Program constraint satisfaction (capability 2)

Generate a program for each of the 120 fixture profiles (fixed seed each) and
evaluate the **15 constraints listed verbatim in SCOPE.md § "Program
constraints"**:

```
M5 = Σ_programs Σ_constraints passed / (120 · 15)
```

Constraint 10 is checked against `evals/fixtures/split_templates.json` (the
eval's own copy) and constraint 15 against
`evals/fixtures/required_coverage.json` — both independent of `data/`, so a
divergence between engine data and the eval's expectation is a *failure*, which
is the intent.

`required_coverage.json` (hand-authored, week-4, effective sets ≥ MEV):

| goal | muscles that must reach ≥ MEV by week 4, for every profile with days ≥ 4 |
|---|---|
| hypertrophy | chest, lats, upper_back, side_delts, biceps, triceps, quads, hamstrings, glutes, calves |
| general | chest, lats, upper_back, side_delts, biceps, triceps, quads, hamstrings |
| strength | quads, hamstrings, glutes, lower_back, chest, upper_back, lats, triceps |

For profiles with days 2–3 the required list is the first 5 (days 2) or 7
(days 3) entries of the same row — matching what `count_by_days` makes
feasible, and pinned independently in the fixture.

### M6 — Progression decision accuracy (capability 2)

28 golden scenarios (history + current prescription + week → expected `action`,
expected `clause`, and expected `suggested_load_kg` to the rounding
increment):

```
M6 = (#scenarios where action AND clause AND load all match) / 28
```

Action space is exactly SCOPE.md FR-6's: `increase_load`, `decrease_load`,
`hold`, `deload_recommend`, `add_set`, `add_reps`, `progress_variation`.
Asserting `clause` as well as `action` is what makes the ladder's precedence
order (not just its outcomes) part of the contract.

Scenario composition: 6 loaded happy-path (one per L-clause), 4 conflict cases
where two clauses would fire and only the higher one may win (L1+L2, L2+L3,
L3+L5, L1+L4), 6 bodyweight (one per B-clause), 2 bodyweight conflict cases,
4 increment-rounding cases (barbell kg, dumbbell kg, machine lb, kettlebell
kg), 3 insufficient-history cases (L1/B1 cannot fire), 3 deload-week cases.

### M8 — Real-clip agreement (reported, **not gated**)

8 clips of the owner's own lifts (2 squat side, 2 squat front, 2 deadlift side,
2 push-up side). Keypoints are extracted **once** with the live MediaPipe
adapter and the resulting `.keypoints.json` files are committed; fault labels
and rep counts are hand-annotated. No angle ground truth. `run.py` prints
per-clip rep-count agreement and fault-set agreement (Jaccard over flagged
`(rep, fault_id)`), plus the aggregate.

M8 is **not** a gate: 8 hand-labeled clips cannot support a threshold, and
hermeticity forbids re-running MediaPipe in CI. It exists so the
synthetic-to-real gap is visible from day one instead of being discovered after
shipping. A regression here is a review conversation, not a red build. If
`evals/fixtures/real/` is empty (before the owner has filmed and labeled the
clips), `run.py` prints the row as `NOT AVAILABLE` and continues — the suite
never blocks on it, preserving zero-config runnability.

## Fixture strategy

Everything committed under `evals/fixtures/`. Nothing is produced by running
the engine — ground truth always comes from generation parameters or
hand-authoring, so the evals cannot be circular.

**Generator independence (hard rule).** `generate_poses.py` must not import
anything from `src/formcoach`, and a test asserts this by scanning its imports.
Ground-truth feature values are computed **analytically from the generation
parameters** (the joint angles and offsets that were injected), not by running a
geometry routine over the rendered skeleton. Where a projected value must be
computed from coordinates (e.g. `fppa_deg` after orthographic projection), the
generator uses its own local implementation. As a definitional cross-check,
`evals/fixtures/labels_handcheck.json` commits **8 reps** (one per fault class
plus two clean) whose feature values were computed by hand from the committed
keypoints and written into the file; `test_gates.py` asserts the engine's
measurements match them within the M3 gate. A shared sign or normalization
error therefore fails the hand-check even if generator and engine agree.

### Pose clips — `evals/fixtures/poses/*.keypoints.json` + `labels.json`

Generated by `evals/fixtures/generate_poses.py --seed 20260731` (committed).
A 2-D kinematic stick-figure simulator:

- **Skeleton:** COCO-17 keypoints from body-segment lengths per de Leva (1996)
  anthropometric parameters; three body sizes.
- **Motion:** per-exercise joint-angle trajectories (raised-cosine
  interpolation between start/extremum/end angles per rep phase), 4–6 reps per
  clip, small per-rep tempo variation (seeded).
- **Fault injection:** faults are *parameters* — bottom hip/knee angles for
  shallow depth, trunk-pitch profile for lean, frontal knee-x offset for valgus,
  hip-lead timing offset for `hips_rise_early`, wrist-x offset for `bar_drift`,
  elbow-extension ceiling for lockout faults, mid-hip perpendicular offset for
  `hip_sag_or_pike`. Each injected fault clears its threshold by ≥ 2× the
  noise-induced feature σ; each clean rep sits the same margin inside. ~10 % of
  reps are deliberately **near-threshold**, labeled by their exact pre-noise
  value — these keep the gates honest about measurement precision.
- **Noise & realism** (hardened so the gates do not certify a
  smoothing-window-fitted-to-iid-Gaussian implementation):
  - orthographic projection to normalized image coords, plus **mild perspective
    scaling** (±4 % depth-dependent size change across the rep);
  - **temporally correlated jitter**: AR(1) with ρ = 0.6 and per-clip
    σ ∈ [0.004, 0.010] normalized units (recorded per clip);
  - **fps variation**: clips at 24, 30 and 60 fps (recorded; FR-8 must derive
    its window and minimum-duration in frames from the clip's own fps);
  - **outlier frames**: on ~1 % of frames a single keypoint teleports by
    0.05–0.15 units *while reporting conf ≥ 0.8* — the nastiest real
    pose-estimator failure, and the one a naive smoother mishandles;
  - **slow camera drift**: a per-clip linear translation of up to 0.03 units
    over the clip;
  - per-keypoint confidence model with occasional dips.
  Labels are computed pre-noise, so none of this moves ground truth.
- **Degraded / invalid clips (8):** 6 with sustained conf < 0.3 on required
  keypoints in > 30 % of frames; 1 with part of the body outside `[0, 1]` in
  > 30 % of frames; 1 **boundary** clip at exactly 0.32 invisible-frame
  fraction (must reject). One *valid* clip sits at 0.28 (must accept), so the
  30 % rule is pinned from both sides.
- **Undeclared-view clips (4):** the sidecar omits `"view"` — one squat side,
  one squat front, one deadlift side, one push-up side, with the squat-front
  one placed near the 0.45 shoulder-width/torso-length boundary. Checked by
  M4's view-resolution term.
- **Composition:** 58 clips = squat side ×12, squat front ×12, deadlift side
  ×14, push-up side ×12 (50 valid, of which 4 are the undeclared-view clips and
  1 is the 0.28 boundary clip) + 8 invalid. Plus **6 photo fixtures**
  (single-frame sidecars derived from committed clip frames: 2 squat side
  bottom, 1 squat front bottom, 2 deadlift side top, 1 push-up side bottom) for
  FR-15. Every fault class has ≥ 10 positive instances; ≈ 30 % of assessable
  triples are faults.
- **Ground truth (`labels.json`):** per clip — exercise, view, fps, validity and
  expected reject reason; per rep — true boundary frames and exact pre-noise
  feature values; per `(rep, rule)` — `assessable` flag and `ok`/`fault` label.
  All derived from generator parameters at generation time.

**Regeneration check.** All floats are rounded to 6 decimals at generation
time; CI re-runs the generator and diffs **parsed values with tolerance 1e-9**,
not raw bytes — byte-identity across platforms and library versions is not a
promise a float-heavy generator can keep.

### Programming fixtures

- `evals/fixtures/profiles_grid.json` — 120 profiles: goals(3) ×
  days_per_week(2–6) × experience(2: beginner, advanced) ×
  equipment({full gym}, {dumbbell, bodyweight}) × emphasis({}, {quads, chest}).
- `evals/fixtures/required_coverage.json` — the eval-owned coverage table above.
- `evals/fixtures/split_templates.json` — the eval-owned split → required
  patterns table.
- `evals/fixtures/progression_golden.json` — 28 hand-authored scenarios with
  expected action + clause + load, each annotated with the FR-6 clause and the
  source concept it exercises.

## Naive baselines and gates

Every baseline in this table is **implemented in `evals/metrics.py`** (a few
lines each), printed as its own row in `run.py`'s scorecard, and asserted in
`test_gates.py` to be strictly worse than the gate. The table is therefore
self-updating and "meaningfully above a naive baseline" is self-enforcing.

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1 macro-F1 | flag every assessable triple as fault | ≈ 0.46 (recall 1.0, precision ≈ fault base rate 0.30); never-flag = 0.00 | **≥ 0.80** | Near-threshold reps, AR(1) jitter and outlier frames make 1.0 unreachable without fixture overfitting; design margins put a correct implementation at ≈ 0.84–0.92. 0.80 is far above flag-all and cannot be reached by refusing hard cases (M7 blocks that). |
| M1b worst-class F1 | flag-all | ≈ 0.31 (the lowest-base-rate class) | **≥ 0.55** | With ≥ 10 positives per class a floor is statistically meaningful; a dead or inverted rule scores ~0 and fails outright, which macro-F1 alone would hide. |
| M2 rep count | peak-count on the raw unsmoothed signal, no prominence/duration hysteresis | ≈ 0.45 | **≥ 0.90** | Hysteresis segmentation should miss only the harshest-jitter/outlier clips; 1.0 is not demanded so the worst-σ 24 fps clip may genuinely be hard. |
| M3a angle MAE | feature from a single raw extremum frame, no smoothing | ≈ 9° | **≤ 5°** | Post-smoothing noise floor ≈ 2–3° by construction; 5° passes a correct pipeline, fails a broken one, and is tighter than the smallest threshold margin (valgus 12°). |
| M3b ratio MAE | same | ≈ 0.06 | **≤ 0.03** | Same argument in ratio units (smallest margin: `depth_ratio` 0.03). |
| M4 screening + view | accept everything, always resolve `side_left` | ≈ 0.82 (50/58 clips + 1/4 views = 51/62) | **= 1.0** | Deterministic rules on committed clips with boundary cases on both sides of 30 % and 0.45; any miss means US-7 behavior broke. |
| M4b never-guess | never emit `not_assessed` | 0.00 | **= 1.0** | The complete-matrix invariant makes this exactly checkable; guessing a side-view rule from a front view is the failure this exists to catch. |
| M7 refusal rate | refuse everything (the M4b-passing degenerate) | 1.00 | **≤ 0.02** | Closes the loophole where an engine refuses near-threshold triples to keep only easy ones in M1/M3. 2 % ≈ 5 triples of ~250 — enough for genuine edge cases, not enough to duck the exam. |
| M5 constraints | "3×10 everything, same full-body session daily, no deload, fixed rep range, no RIR" | ≈ 0.47 (fails volume bands, coverage, frequency, deload, RIR ramp, goal rep ranges, ordering, experience scaling) | **= 1.0** | The generator is deterministic and the constraints are stated in SCOPE.md; any violation is a bug — the same rationale as the workspace's other 100 % safety gates. |
| M6 progression | always `increase_load` by one increment | ≈ 0.29 (8/28) | **= 1.0** | 28 hand-authored cases of a deterministic ladder; partial credit would hide broken autoregulation or precedence. |
| M8 real clips | — | — | *reported only* | See M8; 8 hand-labeled clips cannot support a threshold. |

Fault base rate, clip counts and the 0.84–0.92 expectation are fixed by the
generator's committed parameters. Because the baseline rows are computed rather
than asserted, a fixture-composition change updates them automatically and
`test_gates.py` fails if any gate stops beating its baseline.

## FR → gate mapping

| FR | Gate(s) |
|---|---|
| FR-3 | M5 |
| FR-4 | M5 constraints 1, 15 (shared attribution code path) |
| FR-6 | M6 |
| FR-7 | M4 (screening + view resolution) |
| FR-8 | M2, and M1/M3 via rep alignment |
| FR-9 | M1, M1b, M4b, M7 |
| FR-10 | M1, M3 |
| FR-15 | M1/M4b over the 6 photo fixtures |

FR-1, FR-2, FR-5, FR-11, FR-12, FR-13, FR-14 are covered by ordinary tests
named for their FR ids (e.g. `test_fr13b_pain_flag_substitutes_exercise`).

## How the suite runs

```bash
cd projects
uv run python formcoach/evals/run.py     # scorecard: metric | baseline | value | gate | PASS/FAIL
uv run pytest formcoach/                 # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads fixtures, runs the engine through the
  offline adapters, prints the table above with actual and baseline values,
  exits non-zero if any gate fails. M8's row prints without a verdict.
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1_fault_macro_f1_fr9`, `test_gate_m1b_worst_class_f1_fr9`,
  `test_gate_m5_program_constraints_fr3`, …), plus
  `test_baselines_are_beaten_by_gates` and
  `test_handcheck_features_match_engine`.
- `evals/metrics.py` — pure metric functions (including `align_reps` and the
  baselines) shared by both entry points.
- Hermetic: no network, no wall clock (all timestamps come from fixtures),
  seeded randomness only. The optional live pose adapter is never imported by
  the eval path — including for M8, whose keypoints are committed.
