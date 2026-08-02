# FormCoach — Scope

## One-liner

A single-user AI workout coach that generates genuinely science-based training
programs (goal- and muscle-targeted, volume-landmark-driven, RIR-autoregulated),
serves an exercise library with image/video references, and — the hard part —
analyzes user-supplied exercise video or photos via pose keypoints to detect
specific form faults and emit concrete corrections.

## Problem statement

Generic workout apps hand out template programs that ignore established
training research (per-muscle weekly volume, frequency, proximity to failure,
progressive overload), and none of them can tell you *why your squat hurts your
back*. The owner wants one tool that (a) programs training the way the
hypertrophy/strength literature says to, (b) shows what each exercise looks
like, and (c) watches a clip of a set and says "your hips shot up before the
bar left the floor — brace and push the floor away" instead of "nice workout!".

The technically hard part is **(c)**: turning a noisy keypoint time-series into
segmented reps, measured joint angles, and *correct, specific, per-rep fault
findings*. The domain-hard part is **(a)**: encoding real programming science
(not bro-science) into a deterministic engine. Both get first-class eval gates.

## Target user

The owner: one adult, experienced enough to film their own lifts, training in a
home or commercial gym. Single-user profile stored locally; no accounts, no
multi-tenancy. Not a medical device; safety behavior is implemented (FR-13),
not just disclaimed.

## Conventions used throughout this document

- **Effective sets** for a muscle = Σ over logged/planned sets of 1.0 if the
  muscle is in `Exercise.primary_muscles`, 0.5 if in `secondary_muscles`, else
  0. This single definition is used by FR-3, FR-4 and every eval constraint.
- **Target muscles** = the muscles a program *commits to developing directly*
  (defined algorithmically in § "Program generation algorithm", step 2). Every
  volume and frequency guarantee in this document applies to target muscles and
  to nothing else.
- **Incidental muscles** = every other `Muscle` value. They receive whatever
  effective sets fall out of compound selection; **no band or frequency
  constraint applies to them**, and none is gated.
- `round()` means round-half-up to the nearest integer. Weekly set counts are
  integers; effective set totals may be half-integers.
- **Image coordinates** are normalized to `[0, 1]` with origin at the top-left,
  `x` increasing rightward, `y` increasing **downward** (so a lower body part
  has a *larger* `y`). Every geometric feature below is defined in these terms.

## User stories & acceptance criteria

**US-1 — Program from goals.** As a lifter, I set my goal (hypertrophy /
strength / general), muscles I want to emphasize, days per week, and available
equipment, and get a 5-week program (4 accumulation weeks + deload).
*Accept:* every **target** muscle's planned effective weekly sets fall inside
`[MEV, MRV]` in weeks 1–4; every target muscle is trained on ≥ 2 distinct days
in weeks 1–4; emphasized muscles' weekly sets are **non-decreasing** across
weeks 1→4 (they may plateau when MRV-capped); week 5 is a deload (≤ 50 % of
week-4 sets, RIR ≥ 4); rep ranges and RIR targets match the goal table;
only exercises matching my equipment appear; generation is deterministic given
the same profile + seed.

**US-2 — Browse the exercise library.** As a lifter, I search exercises by
muscle, equipment, or movement pattern and see instructions, cues, and image
(optionally video) references.
*Accept:* filter by any combination of primary muscle / equipment / pattern;
each result includes ≥ 1 media reference that passes the FR-2 integrity check
and step instructions; an exercise with a form profile is flagged
"form-analyzable".

**US-3 — Log a workout.** As a lifter, I log the session I just did (exercise,
weight, reps, RIR per set) in under a minute from the CLI.
*Accept:* sets persist append-only; weights are stored canonically in kg;
estimated 1RM is computed per set when it is meaningful; the log can be
freestyle or linked to a program session; timestamps are supplied by the caller
(engine never reads the clock).

**US-4 — Know what to do next.** As a lifter, I ask for my next session and get
concrete prescriptions — exercise, sets, target reps, target RIR, and a numeric
load suggestion derived from my history.
*Accept:* the FR-6 decision ladder is applied to my last logs and emits exactly
one action per exercise with a numeric `suggested_load_kg` rounded to that
exercise's equipment increment (or a rep/variation action for bodyweight
exercises); recommendations are reproducible from the same history.

**US-5 — Check my weekly volume.** As a lifter, I view effective weekly sets
per muscle against MEV/MAV/MRV bands so I know if I'm under- or over-shooting.
*Accept:* the report covers all 15 muscles, marks each below-MEV / in-band /
above-MRV for a caller-supplied ISO week, and labels each as target or
incidental for the active program.

**US-6 — Fix my form (the core feature).** As a lifter, I submit a video (or
its pre-extracted keypoints) of squat / deadlift / push-up with the camera
view, and get a per-rep report: rep count, measured metrics (depth, trunk
angle, valgus angle, lockout…), detected faults with severity, and one
specific correction cue per fault.
*Accept:* on the committed fixture suite the analyzer meets the EVALS.md gates
(M1 macro-F1 ≥ 0.80, M1b per-class F1 ≥ 0.55, M2 rep-count ≥ 0.90, M3 angle
MAE ≤ 5°); every finding carries the measured value, the threshold it violated,
and a cue from the exercise's form profile; analyses are persisted and
retrievable.

**US-7 — Refuse to guess, but only when justified.** As a lifter, if my clip is
unusable (occluded joints, wrong view for a check), I get told *why* instead of
confident nonsense — and the tool does not hide behind "can't tell" on clips it
*can* read.
*Accept:* clips where > 30 % of frames lack required keypoints at confidence
≥ 0.3 are rejected with reason `insufficient_visibility`; a rule whose declared
views exclude the resolved view, or whose keypoints are not visible for that
rep, reports `not_assessed`, never a guess (gated at 1.0 by M4b); the rate of
`not_assessed` on triples that ground truth says *are* assessable is gated at
≤ 2 % (M7).

**US-8 — Train safely.** As a user, the tool never diagnoses injuries, and
reacts when I report pain.
*Accept:* program generation is blocked until I have acknowledged the
not-medical-advice notice (stored with timestamp); logging a set with
`pain_flag=true` adds that exercise to `UserProfile.pain_flags`, returns a
stop-and-refer recommendation, and causes FR-13(b) substitution in later
prescriptions until I clear the flag; form reports use fault/correction
language, never injury or diagnosis language (asserted by test).

**US-9 — Check a single photo.** As a lifter, I submit one still frame plus the
exercise, view, and which phase it shows (`bottom` or `top`), and get the
fault findings for the rules that are sampled at that phase.
*Accept:* rep segmentation is skipped; only rules whose `phase` equals the
declared phase and whose views include the resolved view are evaluated; all
other rules report `not_assessed`; the analysis persists with
`analysis_kind = photo`.

## Functional requirements

Each FR is independently testable; test names reference FR ids.

- **FR-1 Profile.** CRUD a single `UserProfile`: goal (`hypertrophy` |
  `strength` | `general`), experience (`beginner`|`intermediate`|`advanced`),
  days/week (2–6), equipment set, emphasized muscles (0–3), unit system,
  disclaimer-acknowledged timestamp, pain flags. Exactly one profile row may
  exist.
- **FR-2 Exercise library.** Load the committed exercise dataset (≈ 60
  exercises) into the store at `init`; query by muscle / equipment / movement
  pattern / analyzable-flag; return media references resolved through the
  `MediaResolver` adapter. The `init` integrity check validates that every
  exercise has ≥ 1 `MediaAsset`, that every `source = local` asset's file
  exists under `data/media/`, and that every `source = url` asset is
  schema-valid (well-formed https URL, license present) — **URL assets are
  verified-by-manifest offline and are never fetched**; HTTP verification
  happens only in the env-gated live resolver. `init` therefore succeeds
  hermetically.
- **FR-3 Program generation.** Given a profile, an `as_of` date and a seed,
  emit a 5-week mesocycle. Fully specified in § "Program generation
  algorithm". Output violating any § "Program constraints" item is a defect
  (eval-gated at 1.0 by M5).
- **FR-4 Volume accounting.** Compute effective weekly sets per muscle from set
  logs, classify each of the 15 muscles against the committed volume-landmark
  dataset (below-MEV / MEV–MAV / MAV–MRV / above-MRV) for a caller-supplied ISO
  week, and mark each muscle target/incidental relative to the active program.
- **FR-5 Workout logging.** Append-only `WorkoutLog` + `SetLog` records; weight
  converted to kg at insert; per-set `e1rm_kg` derived via RIR-adjusted Epley
  `e1rm_kg = weight_kg · (1 + (reps + rir)/30)` **only when `w > 0` and `rir` is not
  null**, else null; logs may reference a program session or be freestyle;
  invalid references and negative weights/reps rejected.
- **FR-6 Progression.** Pure function
  `next_prescription(history, prescription, week, increments) -> NextPrescription`
  emitting **exactly one** action. Fully specified in § "Progression decision
  ladder". Deterministic; golden-scenario eval-gated (M6 = 1.0).
- **FR-7 Pose ingestion.** `PoseEstimator` adapter converts a media file into a
  `PoseSequence` (COCO-17 keypoints, per-frame `(x, y, conf)` in normalized
  image coordinates as defined above, fps, resolved view). Offline
  implementation reads committed `.keypoints.json` sidecars deterministically;
  live implementation wraps MediaPipe Pose (optional extra `formcoach[pose]`).
  **View resolution** (recorded together with `view_inferred`):
  1. If the caller declares `front`, `side_left` or `side_right`, that wins
     (`view_inferred = false`).
  2. If the caller declares the ambiguous value `side` (what the CLI's
     `--view side` sends), the **handedness** is resolved to the side whose
     COCO-17 keypoint set (`{side}_shoulder|hip|knee|ankle|elbow|wrist`) has
     the higher mean confidence over all frames; exact tie → `side_left`.
     `view_inferred = true`.
  3. If the caller declares nothing, front-vs-side is inferred from
     `mean(|left_shoulder.x − right_shoulder.x|) / mean(torso_length)` over
     frames where both shoulders have conf ≥ 0.3: ratio > 0.45 → `front`, else
     side, then handedness per step 2. `view_inferred = true`.
  Screening: a clip is rejected with `insufficient_visibility` when > 30 % of
  frames have any keypoint in `required_keypoints[resolved_view]` below
  conf 0.3, or when > 30 % of frames have any required keypoint outside
  `[0, 1]` (partial body out of frame).
- **FR-8 Rep segmentation.** From the exercise's `primary_signal` (squat &
  deadlift: mid-hip `y`; push-up: mid-shoulder `y`): centred moving-average
  smoothing over `max(3, round(fps · smoothing_window_frac))` frames,
  peak/valley detection with prominence ≥ `prominence_frac` (0.15) of the
  smoothed signal's range and minimum rep duration ≥ `min_rep_duration_s`
  (0.8 s, converted to frames using the clip's actual fps). Emits rep
  boundaries `(start_frame, extremum_frame, end_frame)`. Deterministic;
  eval-gated on rep-count accuracy (M2).
- **FR-9 Fault detection.** For each rep, evaluate **every** rule in the
  exercise's `FormProfile` and emit one `FaultFinding` per rule per rep
  (complete matrix). A rule yields `not_assessed` iff the resolved view is not
  in `rule.views` **or** any keypoint its feature needs is below conf 0.3 at
  the sampled phase frame(s). Otherwise it yields `fault` or `ok` by comparing
  the measured feature to the threshold with the rule's comparator, and a
  `fault` carries measured value, threshold, frame index, severity and cue.
  Eval-gated on M1, M1b, M3, M4b, M7.
- **FR-10 Form report.** Persist `FormAnalysis` (+ per-rep `RepAnalysis`,
  per-rule `FaultFinding`): rep count, per-rep metrics and findings, per-rep
  score `max(0, 100 − Σ penalties)` with penalties major 25 / moderate 15 /
  minor 8, clip score = mean of rep scores, and a prioritized correction list
  (sorted by severity rank desc, then occurrence count desc, then `fault_id`
  ascending; deduplicated by `fault_id`). Rejections persist with
  `status = rejected`, `reject_reason` set, and null `rep_count`/`clip_score`.
- **FR-11 API.** FastAPI app exposing the endpoint list below; thin layer —
  validation + service calls only.
- **FR-12 CLI.** Typer app exposing the command list below; same services as
  the API.
- **FR-13 Safety behavior (implemented, not disclaimed).**
  (a) `POST /programs` and `formcoach program new` fail with error
  `disclaimer_not_acknowledged` until `disclaimer_acknowledged_at` is set via
  an explicit ack step.
  (b) A `SetLog` with `pain_flag = true` adds its `exercise_id` to
  `UserProfile.pain_flags` and the response carries a stop-and-refer
  recommendation. While an exercise is flagged, `GET /programs/{id}/next`
  **substitutes** it with the top-ranked alternative from the FR-3 exercise
  ranking restricted to the same `pattern`, the profile's equipment, not
  flagged, and `difficulty ≤` the original's; the substitution is deterministic
  (same ranking key and seed as the program). If no alternative exists, the
  prescription is dropped and the response lists it under `dropped` with
  reason `pain_flag_no_substitute`. Pain is *never* auto-cleared; the user
  clears it via `DELETE /profile/pain-flags/{exercise_id}` /
  `formcoach profile clear-pain <exercise-id>`. Program rows are immutable
  snapshots — substitution happens at read time only.
  (c) A static-string audit test asserts no engine output template, cue, or
  error message contains diagnosis vocabulary (`injury`, `injured`, `tear`,
  `hernia`, `diagnos`, `strain`, `sprain`); form findings speak only of
  movement faults.
- **FR-14 Determinism & hermeticity.** Engine functions take time and seed as
  inputs; all tests and evals run with the offline adapters, no network, no
  clock; identical inputs produce identical program, prescription, and
  analysis outputs.
- **FR-15 Single-frame (photo) analysis.** `analyze_photo(image_or_keypoints,
  exercise_id, view, phase)` where `phase ∈ {bottom, top}`. The pose adapter
  returns a one-frame `PoseSequence`; FR-8 is skipped; a single synthetic
  `RepAnalysis` with `rep_index = 0` and `start_frame = extremum_frame =
  end_frame = 0` is created; FR-9 runs with the declared phase substituted for
  phase resolution — rules whose `phase` is neither the declared phase nor
  `whole_rep` yield `not_assessed` with reason `phase_not_shown`. Persisted
  with `analysis_kind = photo`; scoring and correction lists are identical to
  FR-10 over that one rep. Rules requiring multi-frame features
  (`hip_shoulder_rise_ratio`, phase `ascent_early`) are structurally
  `not_assessed` for photos.

### Program generation algorithm (FR-3)

1. **Split by days/week:** 2–3 → `full_body`; 4 → `upper_lower`; 5 →
   `upper_lower_ppl` (Upper, Lower, Push, Pull, Legs); 6 → `ppl_x2`. The
   split → required-movement-pattern map is committed data
   (`data/split_templates.json`); the eval owns an independent copy.
2. **Target-muscle selection.** `data/target_muscles.json` commits, per goal, a
   **priority-ordered** muscle list, and a `count_by_days` map
   `{2:5, 3:7, 4:10, 5:11, 6:12}`. The target set is
   `emphasized_muscles`, then muscles taken in priority order until the set
   size equals `count_by_days[days_per_week]` (emphasized muscles that are not
   in the priority list are still included; the tail is truncated to keep the
   size fixed). Priority orders:
   - *hypertrophy* and *general*: chest, quads, lats, upper_back, hamstrings,
     side_delts, triceps, biceps, glutes, calves, rear_delts, abs.
   - *strength*: quads, chest, upper_back, lats, hamstrings, lower_back,
     triceps, glutes, side_delts, biceps, calves, abs.
   **Feasibility invariant (validated at `init`):** for every
   (goal, days, emphasis) combination, `Σ MEV(target) ≤ 22 · days_per_week`.
   With the committed landmarks this holds with margin (worst case 40 ≤ 44 at
   2 days); a landmark or priority edit that breaks it fails `init`.
3. **Weekly set targets** (in *effective* sets — see § Conventions; the
   per-session caps in step 5 are in *direct* sets, which are always fewer).
   For each target muscle `m`:
   `start(m) = MEV(m) + round(f_exp · (MAV(m) − MEV(m)))` with
   `f_exp = 0.0 / 0.25 / 0.50` for beginner / intermediate / advanced;
   `end(m) = min(MAV(m) + (2 if m emphasized else 0), MRV(m))`, and
   `end(m) = max(end(m), start(m))`;
   `sets(m, w) = round(start(m) + (end(m) − start(m)) · (w − 1)/3)` for
   `w ∈ 1..4` (monotone non-decreasing by construction).
   **Budget clamp:** if `Σ_m sets(m, w) > 22 · days_per_week`, reduce sets
   largest-first (ties by reverse priority order, then muscle name) until the
   budget holds, never below `MEV(m)`, and never breaking monotonicity
   (a reduction in week `w` is applied to all weeks `≥ w`).
   Week 5 deload: `sets(m, 5) = max(1, floor(0.5 · sets(m, 4)))`.
4. **Exercise selection.** Candidates = library exercises where
   `exercise.equipment ∩ profile.equipment ≠ ∅` and the exercise is not
   pain-flagged; for `beginner`, `difficulty = 3` exercises are excluded unless
   no `difficulty ≤ 2` candidate covers that movement pattern. Candidates are
   shuffled with `random.Random(seed)` then **stable-sorted** by the key
   `(−|primary_muscles ∩ still-uncovered targets|, 0 if compound else 1,
   difficulty, exercise_id)`; the top candidate is taken, its targets marked
   covered, and the loop repeats until every required pattern of the session is
   covered and every target muscle has a source. Seed therefore only reorders
   exact ties.
5. **Set distribution.** A muscle's weekly effective-set target is met by
   assigning direct sets to the selected exercises, split as evenly as possible
   across the sessions that train it (largest-remainder), respecting: ≥ 2
   distinct days for every target muscle in weeks 1–4; ≤ 10 **direct** sets per
   muscle per session; ≤ 22 **direct** working sets per session. A muscle's
   realized effective sets may exceed its target by the secondary credit of
   other exercises; only the `[MEV, MRV]` band is gated (constraint 1).
6. **Reps, RIR, rest.** Rep ranges by goal × mechanics:

   | goal | compound | isolation |
   |---|---|---|
   | strength | 3–6 | 8–12 |
   | hypertrophy | 5–10 | 8–15 |
   | general | 6–12 | 8–15 |

   `target_rir` by week: `w1 = 3, w2 = 2, w3 = 2, w4 = 1, w5 = 4`.
   Rest: compound 150–300 s, isolation 60–120 s (assigned per exercise from
   the dataset's `rest_s` hint, clamped into the band).
7. **Load basis.** Week-5 `load_note` uses a −15 % basis versus week 4.
   Numeric loads are not stored on `Prescription` — they are produced at read
   time by FR-6 (see `NextPrescription`).

### Program constraints (gated by M5)

Restated here so SCOPE and EVALS cannot drift; EVALS.md M5 checks exactly this
list. For every generated program:

1. Every **target** muscle's effective weekly sets ∈ `[MEV, MRV]`, weeks 1–4.
2. Every **target** muscle is trained on ≥ 2 distinct days, weeks 1–4.
3. Emphasized muscles' weekly sets are non-decreasing weeks 1→4.
4. Week 5 sets ≤ 50 % of week 4 per muscle, and `target_rir ≥ 4`.
5. `target_rir` non-increasing weeks 1→4, all values within `[1, 3]`.
6. Rep ranges match the goal × mechanics table exactly.
7. Every prescribed exercise's equipment intersects the profile's equipment.
8. Per-session per-muscle sets ≤ 10; per-session total working sets ≤ 22.
9. Compounds ordered before isolation within each session.
10. Every required movement pattern of the split appears in the right session
    (checked against the **eval's own** copy of the split template).
11. Rest seconds within the compound/isolation bands.
12. Same profile + seed ⇒ identical program (run twice, compare).
13. `beginner` programs contain no `difficulty = 3` exercise when a
    `difficulty ≤ 2` candidate covers that pattern within the profile's
    equipment.
14. Week-1 sets per target muscle equal the experience-scaled `start(m)` of
    step 3 (post-clamp), so the experience dimension is load-bearing.
15. **Coverage (eval-owned):** the eval's own required-muscle list for the goal
    (independent of `data/target_muscles.json`) must be a subset of the muscles
    receiving ≥ MEV effective weekly sets in week 4. This is what stops a
    minimal pattern-covering generator from passing with zero arm/delt/calf
    volume.

### Progression decision ladder (FR-6)

`next_prescription` evaluates the clauses **in this order and stops at the
first match**, emitting exactly one `action`. `ref` = the most recent
`WorkoutLog` containing that exercise; `prev` = the one before it.

**Loaded branch** (any set of `ref` for this exercise has `weight_kg > 0`):

| # | Clause | Condition | Action | Load |
|---|---|---|---|---|
| L1 | Reactive deload | best `e1rm_kg` of `ref` < 0.95 × best `e1rm_kg` of `prev`, **and** < 0.95 × best of the session before `prev` — computed only over sets with non-null `e1rm_kg` (i.e. RIR-rated and loaded) | `deload_recommend` | 0.90 × last top-set load |
| L2 | Missed volume | any set in `ref` has `reps ≤ rep_low − 2` | `hold` | last top-set load |
| L3 | Double progression | every set in `ref` reached `rep_high` **and** every reported `rir ≥ target_rir` | `increase_load` | ×1.025 upper-body, ×1.05 lower-body |
| L4 | Autoregulation — undershoot | mean reported `rir` < `target_rir − 1` | `decrease_load` | ×0.95 |
| L5 | Autoregulation — overshoot | mean reported `rir` > `target_rir + 1` | `increase_load` | ×1.025 |
| L6 | Otherwise | — | `hold` | last top-set load |

**Bodyweight branch** (every set of `ref` for this exercise has
`weight_kg = 0`):

| # | Clause | Condition | Action |
|---|---|---|---|
| B1 | Reactive deload | total reps in `ref` < 0.90 × total reps in `prev`, and < 0.90 × the session before `prev` | `deload_recommend` |
| B2 | Missed volume | any set has `reps ≤ rep_low − 2` | `hold` |
| B3 | Variation progression | every set reached `rep_high` at `rir ≥ target_rir` **and** `Exercise.harder_variant_id` is not null **and** `sets ≥ 3` | `progress_variation` (swap to the variant, reset target to `rep_low`) |
| B4 | Add a set | every set reached `rep_high` at `rir ≥ target_rir`, no harder variant, `sets < 5` | `add_set` |
| B5 | Add reps | every set reached `rep_high` at `rir ≥ target_rir`, `sets = 5` | `hold` |
| B6 | Otherwise | — | `add_reps` (target reps + 1, capped at `rep_high`) |

Notes: (i) L1/B1 need three sessions of history; with fewer, the clause cannot
fire. (ii) `suggested_load_kg` is `round_to_increment(raw, inc)` with
`round_to_increment(x, i) = i · floor(x/i + 0.5)` and `inc` from
`data/load_increments.json` (§ Committed datasets); bodyweight actions carry
`suggested_load_kg = null`. (iii) Upper/lower is decided by whether the
exercise's primary muscles are all in
`{quads, hamstrings, glutes, calves, lower_back}` (lower) or not (upper).
(iv) Actions are `increase_load`, `decrease_load`, `hold`, `deload_recommend`,
`add_set`, `add_reps`, `progress_variation` — this is exactly M6's action
space.

### "Next session" definition (US-4, FR-11)

`GET /programs/{id}/next` returns the `ProgramSession` with the lowest
`(week, day_index)` that has **zero** linked `WorkoutLog` rows. A partially
logged session counts as done. Freestyle logs never mark a session done. If
every session is logged, the endpoint returns 404 with
`program_complete`. Each prescription in the response is passed through FR-6
and FR-13(b), producing `NextPrescription` objects.

### Fault catalog (form profiles — the analyzable three)

Thresholds are data (per-profile JSON); defaults below with grounding. A
**fault class** is the pair `(exercise_id, fault_id)` — 10 classes, which is
what M1 averages over. All side-view signed features are computed in a
canonical anterior frame: with
`facing = sign(mean(nose.x) − mean(mid_hip.x))` over valid frames, any signed
feature is multiplied by `facing` so that **positive means anterior** (in front
of the body). This makes `side_left` and `side_right` clips interchangeable.

| Exercise | View | Fault id | Geometry (image coords, y down) | Default threshold | Severity |
|---|---|---|---|---|---|
| squat | side | `insufficient_depth` | `depth_ratio = (knee_y − hip_y)/femur_len` at bottom frame; positive = hip above knee | `> 0.03` → fault | major |
| squat | side | `excessive_trunk_lean` | `trunk_lean_deg` = angle of the mid-shoulder→mid-hip line from vertical at bottom | `> 55°` | moderate |
| squat | front | `knee_valgus` | `fppa_deg` = frontal-plane projection angle hip→knee vs knee→ankle at bottom; positive = knee medial | `> 12°` | major |
| squat | front | `lateral_shift` | `lateral_shift_frac = \|mid_hip.x − mid_ankle.x\| / hip_width` at bottom | `> 0.15` | minor |
| deadlift | side | `hips_rise_early` | `hip_shoulder_rise_ratio = Δhip_y / Δshoulder_y` over the first 40 % of the ascent | `> 1.5` | major |
| deadlift | side | `bar_drift` | `bar_drift_frac` = signed wrist-x deviation from mid-ankle-x / shank_len, max over the rep | `abs > 0.20` | moderate |
| deadlift | side | `incomplete_lockout` | `hip_ext_angle_deg` = shoulder-hip-knee angle at top | `< 170°` | moderate |
| push-up | side | `insufficient_depth` | `elbow_angle_deg` at bottom | `> 100°` | moderate |
| push-up | side | `hip_sag_or_pike` | `hip_dev_frac` = signed perpendicular distance of mid-hip from the shoulder–ankle line / trunk_len; positive = sag (hip toward larger y) | `abs > 0.08` | moderate |
| push-up | side | `incomplete_lockout` | `elbow_angle_deg` at top | `< 160°` | minor |

Threshold provenance caveat: the `knee_valgus` 12° default is adapted from
FPPA norms measured on **single-leg squat and landing** tasks (Munro,
Herrington & Carolan 2012); no equivalent norm exists for loaded bilateral
barbell squats, so 12° is an honest, tunable starting point rather than a
validated bilateral cut-off, and it lives in data for exactly that reason.

Known, honest limitation: COCO-17 has no spine keypoints, so lumbar
flexion/"butt wink" is **out of scope** (non-goal #5), surfaced as
`not_assessed`, never guessed.

### Committed datasets (`data/`)

| File | Contents |
|---|---|
| `exercises.json` | ≈ 60 exercises (see DATA_MODEL Exercise) |
| `media_manifest.json` | `MediaAsset` rows |
| `volume_landmarks.json` | MV/MEV/MAV/MRV per muscle (table in DATA_MODEL) |
| `target_muscles.json` | per-goal priority order + `count_by_days` |
| `split_templates.json` | split → sessions → required movement patterns |
| `load_increments.json` | `{equipment: {kg: float\|null, lb: float\|null}}` — barbell 2.5/5, dumbbell 2.0/5, machine 5/10, cable 2.5/5, kettlebell 4/10, band null, bodyweight null |
| `form_profiles/{squat,deadlift,pushup}.json` | `FormProfile` documents |

All are loaded and validated by `store/datasets.py` at `init` and handed to the
engine as in-memory Pydantic models — the engine never touches the filesystem.

## Non-goals (this pass)

1. **No web/mobile UI** — API + CLI only (workspace-wide decision).
2. **No media hosting or transcoding.** The library stores references
   (paths/URLs, license, attribution); the live `MediaResolver` can refresh
   from an open exercise DB, but we never serve or store video bytes beyond
   user-supplied analysis clips on local disk.
3. **No live pose estimation in CI.** MediaPipe is an optional extra; tests and
   evals use committed keypoint fixtures exclusively.
4. **No real-time (in-set) coaching.** Analysis is post-hoc.
5. **No spine/foot-pressure faults** (butt wink, heel rise) — invisible to
   COCO-17; revisit if the pose adapter moves to a 33-point topology.
6. **No nutrition, cardio programming, or bodyweight tracking.** The
   `UserProfile.bodyweight` field is *removed*: no defensible per-movement load
   fraction exists to convert bodyweight to an effective load, so bodyweight
   exercises progress by reps/sets/variation (FR-6 bodyweight branch) instead
   of by an invented load number.
7. **No LLM anywhere.** Cues are curated strings in the form profiles;
   deterministic and hermetic by construction.
8. **No exercise-form ML classifier.** Fault detection is an interpretable
   geometric rules engine; a learned model is a possible later adapter behind
   the same interface.
9. **Multi-user, auth, sync — out.** Single local profile.
10. **No overhead-press form profile.** Cut from the MVP to hold the line
    budget (§ below). Squat, deadlift and push-up cover side and front views,
    angle and ratio features, and multi-frame (`ascent_early`) features — the
    rules engine is fully exercised. OHP is a data-only addition later (one
    JSON profile + fixtures, no engine change).
11. **No volume or frequency guarantee for incidental muscles.** They get what
    compound selection gives them; nothing is gated on them.

## Architecture

```
projects/formcoach/
  src/formcoach/
    models.py            # all Pydantic v2 domain models
    engine/
      programming.py     # FR-3 split, target muscles, ramp, selection, distribution
      progression.py     # FR-6 decision ladder, e1RM math, increment rounding
      volume.py          # FR-4 effective-set attribution vs landmarks
      poseio.py          # PoseSequence handling, view resolution, screening (FR-7)
      reps.py            # FR-8 smoothing + prominence/duration segmentation
      geometry.py        # angle/ratio primitives, canonical anterior frame
      faults.py          # FR-9 rule evaluation against FormProfile models
      report.py          # FR-10/FR-15 scoring + correction prioritization
      safety.py          # FR-13 gating, pain-flag substitution, vocabulary audit
    adapters/
      pose.py            # PoseEstimator Protocol
      pose_fixture.py    #   offline: FixturePoseEstimator (*.keypoints.json sidecars)
      pose_mediapipe.py  #   live: MediaPipePoseEstimator (extra: `pose`)
      media.py           # MediaResolver Protocol
      media_local.py     #   offline: LocalMediaResolver (manifest + local files)
      media_wger.py      #   live: WgerMediaResolver (env-gated URL refresh)
    store/
      repository.py      # Repository Protocol
      sqlite.py          # SQLiteRepository (stdlib sqlite3); ":memory:" is the test backend
      datasets.py        # committed-dataset loader + validation
    api/                 # FastAPI app
    cli/                 # Typer app
  data/                  # committed datasets (table above) + data/media/
  evals/                 # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated) |
|---|---|---|
| `PoseEstimator.estimate(media_path, *, declared_view) -> PoseSequence` | `FixturePoseEstimator` — loads `<media>.keypoints.json` sidecar; raises if missing | `MediaPipePoseEstimator` — MediaPipe Pose → COCO-17 mapping; extra `formcoach[pose]` |
| `MediaResolver.resolve(exercise_id) -> list[MediaAsset]`, `.verify(asset) -> bool` | `LocalMediaResolver` — `source=local` verified by file existence; `source=url` verified by manifest schema only | `WgerMediaResolver` — refreshes URLs from wger / free-exercise-db and HTTP-verifies; `FORMCOACH_MEDIA_LIVE=1` |

### API (FastAPI)

```
GET  /health
GET  /profile                          PUT  /profile
POST /profile/acknowledge-disclaimer
DELETE /profile/pain-flags/{exercise_id}
GET  /exercises?muscle=&equipment=&pattern=&analyzable=
GET  /exercises/{id}                          # includes resolved media assets
POST /programs                                # overrides + seed + as_of → mesocycle
GET  /programs/{id}                    GET  /programs/{id}/sessions/{week}/{day}
GET  /programs/{id}/next?as_of=               # FR-6 + FR-13(b) applied
POST /workouts                                # log session + sets (append-only)
GET  /workouts?since=                  GET  /analytics/volume?iso_week=
POST /form/analyses                           # clip or keypoints + exercise_id + view
POST /form/photo-analyses                     # FR-15: image/keypoints + exercise_id + view + phase
GET  /form/analyses/{id}               GET  /form/analyses?exercise_id=
```

### CLI (Typer)

```
formcoach init                                  # create DB, load datasets, integrity check
formcoach profile show|set …|ack-disclaimer|clear-pain <exercise-id>
formcoach exercises list [--muscle --equipment --pattern --analyzable] | show <id>
formcoach program new [--seed --goal --days …] | show <id> | next [--as-of]
formcoach log --exercise <id> --sets "100x8@2,100x8@2,100x7@1" [--pain] [--session …]
formcoach volume [--iso-week]
formcoach form analyze <clip-or-keypoints> --exercise squat --view side [--as-of]
formcoach form photo <image-or-keypoints> --exercise squat --view side --phase bottom
formcoach form show <analysis-id> | list
```

## Line budget

Python source lines (`.py` only; committed JSON data and fixtures excluded).
Ceiling 4,000; the plan below totals **≈ 3,880**.

| Area | Budget |
|---|---|
| `models.py` | 250 |
| `engine/` (programming 280, progression 210, volume 70, poseio 120, reps 100, geometry 150, faults 130, report 90, safety 50) | 1,200 |
| `adapters/` (pose ×3 ≈ 150, media ×3 ≈ 90) | 240 |
| `store/` (repository 60, sqlite 220, datasets 60) | 340 |
| `api/` | 190 |
| `cli/` | 180 |
| **src subtotal** | **2,400** |
| `tests/` | 580 |
| `evals/metrics.py` (10 metrics + `align_reps` + baselines) + `run.py` + `test_gates.py` | 480 |
| `evals/fixtures/generate_poses.py` (3 exercises, hardened noise model) | 420 |
| **Total** | **3,880** |

Trim levers, in the order they should be pulled if the budget is breached:
(1) drop the push-up form profile and its fixtures (−250, still leaves two
exercises and both views); (2) drop `WgerMediaResolver` to a documented stub
(−60); (3) drop `GET /workouts` and `GET /form/analyses` list endpoints (−40);
(4) drop the photo path FR-15 to a non-goal (−120, but this re-opens the
locked-decision deviation and must be recorded).

## Key design decisions & assumptions

1. **Weekly per-muscle volume is the programming backbone.** Dose-response
   meta-analysis shows ≥ 10 weekly sets per muscle outperforms fewer for
   hypertrophy (Schoenfeld, Ogborn & Krieger 2017, *J Sports Sci*). Per-muscle
   bands use the MV/MEV/MAV/MRV volume-landmark framework (Israetel et al.,
   *Scientific Principles of Hypertrophy Training*), committed in
   `data/volume_landmarks.json` and tunable without code changes.
2. **Not every muscle can be a priority.** With 2 training days you cannot put
   fifteen muscles at MEV; pretending otherwise is what makes "science-based"
   apps incoherent. Hence the explicit target/incidental split, the
   priority-ordered target lists, and the feasibility invariant
   `Σ MEV(target) ≤ 22 · days`. The eval owns an independent coverage list so
   the generator cannot pass by choosing a convenient target set.
3. **Frequency ≥ 2×/week per target muscle.** Training a muscle twice weekly
   beats once at equal volume trends (Schoenfeld, Ogborn & Krieger 2016,
   *Sports Med*); even where later work (Schoenfeld, Grgic & Krieger 2019)
   shows volume dominates, splitting volume across ≥ 2 sessions keeps
   per-session sets ≤ 10 for rep quality.
4. **Rep ranges by goal, not myths.** The repetition-continuum re-examination
   (Schoenfeld et al. 2021, *Sports*) shows hypertrophy across ~30–85 % 1RM
   when sets approach failure, while maximal strength needs heavy loading — so
   strength compounds get 3–6 reps, hypertrophy work 5–15 with effort equalized
   by RIR, per ACSM progression-model guidance (ACSM Position Stand 2009).
5. **Effort is prescribed as RIR.** The resistance-training-specific RPE/RIR
   scale (Zourdos et al. 2016, *JSCR*; Helms et al. 2016) anchors targets:
   accumulation weeks descend RIR 3 → 1 — stimulative (proximity-to-failure
   meta: Refalo et al. 2023, *Sports Med*) without week-1 failure training.
6. **Load math via RIR-adjusted Epley.** `e1rm = w(1 + (reps + rir)/30)` (Epley
   1985; RIR extension per Helms/RTS practice). It is only computed when both
   `w > 0` and `rir` is reported — an unrated set is *not* treated as RIR 0,
   because that would inflate e1RM ~6.7 % at RIR 2 and manufacture a spurious
   reactive deload the next time the user skips rating.
7. **Mesocycle = 4 + 1 deload, set-ramp progression.** Accumulation-then-deload
   periodization per RP practice and ACSM progression principles. The reactive
   deload trigger requires two consecutive ≥ 5 % e1RM regressions over
   like-for-like (RIR-rated, loaded) sets.
8. **Form-fix is an interpretable geometric rules engine over keypoints, not a
   classifier.** Faults must come with *measured value + violated threshold +
   cue* to be coaching, not vibes. Thresholds are grounded where literature
   exists — FPPA valgus norms (Munro, Herrington & Carolan 2012, with the
   task-context caveat noted above), squat depth = hip crease below knee
   (powerlifting standard), squat trunk-lean kinematics (Escamilla 2001; Fry,
   Smith & Schilling 2003), deadlift bar path over mid-foot (Hales 2010) — and
   live as per-profile data so they can be tuned without touching code.
9. **COCO-17 topology** (Lin et al. 2014) is the pose contract: MoveNet emits it
   natively and MediaPipe Pose (BlazePose, Bazarevsky et al. 2020) maps onto
   it, so the fixture format outlives any one estimator. Cost: no spine/foot
   detail (non-goal #5).
10. **Rejection over hallucination — but refusal is itself gated.** Screening
    and `not_assessed` are core behavior (M4, M4b), and the *rate* of refusal
    on ground-truth-assessable work is capped (M7) so "I can't tell" cannot
    become a way to dodge the accuracy gates.
11. **Pose estimation and media resolution are the only external
    capabilities**, hence exactly two adapter pairs. No LLM adapter: curated
    cue strings are better coaching *and* keep evals hermetic.
12. **Library seeded from open data.** ~60 exercises adapted from the
    public-domain free-exercise-db dataset (images included) with wger.de as
    the live refresh source (CC-licensed); license + attribution stored per
    asset. Video URL slots exist but ship empty except where wger provides
    them.
13. **Determinism everywhere** (FR-14): seeds and timestamps are inputs;
    generation uses a seeded RNG only to reorder exact ranking ties.
14. **Health safeguard is behavior** (FR-13): ack-gating, pain-flag
    substitution with an explicit clear operation, and a vocabulary audit test.
15. **Assumption: single camera, roughly stationary, whole body in frame, one
    person.** Multi-person disambiguation is out of scope; screening catches
    partial-body and occlusion violations.
16. **Assumption: 2-D analysis is sufficient for the shipped fault set.** All
    ten catalog faults are measurable in the resolved 2-D view; 3-D
    lifting-plane faults are deferred with the topology upgrade (non-goal #5).
17. **Photos are supported but narrow** (FR-15/US-9). A still frame cannot
    yield reps, tempo, or `ascent_early` features, so the photo path evaluates
    only the rules sampled at the phase the user declares. This honours the
    locked "video/photo" decision without pretending a photo is a clip.
