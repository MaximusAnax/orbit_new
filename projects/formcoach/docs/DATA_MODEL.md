# FormCoach — Data Model

Two storage classes, per workspace conventions:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/formcoach/data/`): exercise library, media manifest, volume
  landmarks, target-muscle priorities, split templates, load increments, form
  profiles. Loaded and validated by `store/datasets.py` at `formcoach init`
  into SQLite and into in-memory Pydantic models; the files remain the source
  of truth. **The engine never reads the filesystem** — datasets are passed to
  engine functions as models (CONVENTIONS.md layering rule).
- **SQLite** (user state, default `~/.formcoach/formcoach.db`, path
  configurable; the test backend is the same `SQLiteRepository` opened on
  `":memory:"`): profile, programs, logs, form analyses. Repository pattern;
  stdlib `sqlite3`.

All models are Pydantic v2 in `src/formcoach/models.py`; the store maps them to
the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers (engine
never reads the clock).

## Units and coordinate conventions

- **Mass is stored canonically in kilograms** (`weight_kg`, `suggested_load_kg`
  and `e1rm_kg`). `UserProfile.unit` is a *display and input* setting only:
  the API/CLI convert on the way in and out. Historical rows are therefore
  never reinterpreted when the user switches kg↔lb (invariant).
- **Keypoint coordinates** are normalized to `[0, 1]`, origin top-left, `x`
  increasing rightward, `y` increasing **downward**. All geometry in
  SCOPE.md's fault catalog is defined in these terms.
- **Effective sets** (the unit of every volume figure): 1.0 per set for each
  muscle in `Exercise.primary_muscles`, 0.5 for each in `secondary_muscles`.
  Volume landmarks, program set targets and FR-4 reports are all in effective
  sets. Per-session caps in FR-3 (≤ 10 per muscle, ≤ 22 total) are on **direct
  working sets**, which are always ≤ the effective total.

## Enumerations

| Enum | Values |
|---|---|
| `Goal` | `hypertrophy`, `strength`, `general` |
| `Experience` | `beginner`, `intermediate`, `advanced` |
| `Muscle` | `chest`, `front_delts`, `side_delts`, `rear_delts`, `lats`, `upper_back`, `lower_back`, `biceps`, `triceps`, `forearms`, `quads`, `hamstrings`, `glutes`, `calves`, `abs` |
| `Equipment` | `barbell`, `dumbbell`, `machine`, `cable`, `bodyweight`, `band`, `kettlebell` |
| `MovementPattern` | `squat`, `hinge`, `horizontal_push`, `horizontal_pull`, `vertical_push`, `vertical_pull`, `lunge`, `isolation`, `carry`, `core` |
| `Mechanics` | `compound`, `isolation` |
| `View` | `side_left`, `side_right`, `front` (the CLI/API also accept the input-only value `side`, which FR-7 resolves to `side_left`/`side_right`) |
| `Phase` | `bottom`, `top`, `ascent_early`, `whole_rep` |
| `Severity` | `major`, `moderate`, `minor` |
| `AnalysisKind` | `clip`, `photo` |
| `AnalysisStatus` | `completed`, `rejected` |
| `FindingStatus` | `ok`, `fault`, `not_assessed` |
| `NotAssessedReason` | `view_mismatch`, `keypoints_not_visible`, `phase_not_shown`, `needs_multi_frame` |
| `ProgressionAction` | `increase_load`, `decrease_load`, `hold`, `deload_recommend`, `add_set`, `add_reps`, `progress_variation` |
| `Unit` | `kg`, `lb` |

## Entities

### UserProfile — SQLite `user_profile` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1`; exactly one row (invariant) |
| `goal` | Goal | |
| `experience` | Experience | scales the week-1 start within the MEV→MAV span (FR-3 step 3) |
| `days_per_week` | int | 2–6 |
| `equipment` | JSON list[Equipment] | non-empty |
| `emphasized_muscles` | JSON list[Muscle] | 0–3 entries, unique |
| `unit` | Unit | display + input unit only; storage is kg |
| `disclaimer_acknowledged_at` | str \| null | ISO ts; program generation blocked while null (FR-13a) |
| `pain_flags` | JSON list[str] | exercise ids currently flagged; only cleared by the explicit clear operation (FR-13b) |
| `updated_at` | str | ISO ts, caller-supplied |

`bodyweight` was deliberately removed (SCOPE non-goal #6): no defensible
per-movement load fraction exists, so bodyweight work progresses via the FR-6
bodyweight branch instead of an invented effective load.

### Exercise — committed `data/exercises.json` → table `exercise`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | slug, e.g. `barbell-back-squat` |
| `name` | str | |
| `aliases` | list[str] | |
| `primary_muscles` | list[Muscle] | ≥ 1 |
| `secondary_muscles` | list[Muscle] | may be empty; disjoint from primary (invariant) |
| `equipment` | list[Equipment] | any-of: usable if it intersects the profile's equipment |
| `pattern` | MovementPattern | |
| `mechanics` | Mechanics | |
| `difficulty` | int | 1–3; beginner programs avoid 3 where a ≤ 2 alternative covers the pattern (FR-3 step 4) |
| `rest_s` | int | rest hint, clamped into the FR-3 band for its mechanics |
| `harder_variant_id` | str \| null | next step in a bodyweight progression chain (FR-6 clause B3); must reference an existing exercise with the same `pattern` (invariant) |
| `instructions` | list[str] | ordered steps |
| `cues` | list[str] | short coaching cues |
| `form_profile_id` | str \| null | non-null ⇒ "analyzable" (squat, deadlift, push-up at MVP) |

Invariants: dataset read-only at runtime; every `pattern` × equipment
combination needed by `data/split_templates.json` has ≥ 1 exercise (validated
at `init`); every exercise has ≥ 1 `MediaAsset` (FR-2 integrity check).

### MediaAsset — committed `data/media_manifest.json` → table `media_asset`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `<exercise_id>#<n>` |
| `exercise_id` | str FK → exercise | |
| `kind` | `image` \| `video` | |
| `source` | `local` \| `url` | |
| `ref` | str | relative path under `data/media/` or an https URL |
| `license` | str | e.g. `public-domain`, `CC-BY-SA-4.0` |
| `attribution` | str \| null | required when the license demands it (invariant) |

Verification invariant (FR-2): `source = local` assets are verified offline by
**file existence**; `source = url` assets are verified offline by **manifest
schema only** (well-formed https URL, non-empty license) and are never
fetched — so `formcoach init` is hermetic. HTTP-200 verification exists only in
the env-gated `WgerMediaResolver`. Assets are references; no bytes in the DB.

### VolumeLandmark — committed `data/volume_landmarks.json` → table `volume_landmark`

Per-muscle **weekly effective-set** bands adapted from the RP volume-landmark
framework (Israetel et al.). Committed values (tunable data, not code):

| muscle | mv | mev | mav | mrv |
|---|---|---|---|---|
| chest | 4 | 8 | 16 | 22 |
| front_delts | 0 | 0 | 6 | 12 |
| side_delts | 4 | 8 | 16 | 26 |
| rear_delts | 0 | 6 | 12 | 20 |
| lats | 4 | 8 | 16 | 22 |
| upper_back | 4 | 6 | 16 | 25 |
| lower_back | 0 | 4 | 8 | 14 |
| biceps | 4 | 6 | 14 | 20 |
| triceps | 4 | 6 | 14 | 18 |
| forearms | 0 | 2 | 8 | 15 |
| quads | 6 | 8 | 16 | 20 |
| hamstrings | 3 | 6 | 12 | 20 |
| glutes | 0 | 4 | 12 | 16 |
| calves | 6 | 8 | 16 | 20 |
| abs | 0 | 4 | 12 | 25 |

Invariants: `mv ≤ mev < mav ≤ mrv` per row; and the FR-3 feasibility invariant
— for every (goal, days_per_week, emphasis ≤ 3) combination,
`Σ MEV(target set) ≤ 22 · days_per_week`. With these values the worst case is
5 targets × MEV 8 = 40 ≤ 44 at 2 days/week; both invariants are checked at
`init` so a landmark edit that breaks feasibility fails loudly.

### TargetMusclePolicy — committed `data/target_muscles.json` (in-memory only)

| Field | Type | Notes |
|---|---|---|
| `priority_by_goal` | dict[Goal, list[Muscle]] | ordered; the FR-3 step-2 lists |
| `count_by_days` | dict[int, int] | `{2:5, 3:7, 4:10, 5:11, 6:12}` |

### SplitTemplate — committed `data/split_templates.json` (in-memory only)

| Field | Type | Notes |
|---|---|---|
| `split` | str | `full_body`, `upper_lower`, `upper_lower_ppl`, `ppl_x2` |
| `days` | list[int] | which `days_per_week` values map to this split |
| `sessions` | list[{name, required_patterns: list[MovementPattern]}] | one entry per day of the week; drives constraint 10 |

### LoadIncrement — committed `data/load_increments.json` (in-memory only)

`{Equipment: {"kg": float | null, "lb": float | null}}` — barbell 2.5/5,
dumbbell 2.0/5, machine 5/10, cable 2.5/5, kettlebell 4/10, band null,
bodyweight null. FR-6 rounds with
`round_to_increment(x, i) = i · floor(x/i + 0.5)`; when the increment is null
the loaded branch is not applicable and the bodyweight branch runs. An exercise
with several equipment values uses the **smallest** non-null increment among
them.

### FormProfile — committed `data/form_profiles/<exercise>.json` (in-memory only)

Loaded and schema-validated by `store/datasets.py`; passed to
`engine/faults.py` as models.

| Field | Type | Notes |
|---|---|---|
| `id` | str | e.g. `squat_v1`; version suffix bumps when thresholds change |
| `exercise_id` | str | matches `exercise.form_profile_id` back-reference |
| `primary_signal` | str | keypoint expression for rep segmentation, e.g. `mid_hip.y` |
| `direction` | `down_up` \| `up_down` | whether a rep starts by descending or ascending |
| `min_rep_duration_s` | float | default 0.8 |
| `prominence_frac` | float | default 0.15 |
| `smoothing_window_frac` | float | window = `max(3, round(fps · frac))`, default 0.25 |
| `required_keypoints` | dict[View, list[str]] | visibility-screening set per view |
| `rules` | list[FaultRule] | see below |

`FaultRule` (embedded):

| Field | Type | Notes |
|---|---|---|
| `fault_id` | str | e.g. `knee_valgus`; the eval's fault **class** is `(exercise_id, fault_id)` |
| `views` | list[View] | rule evaluated only in these views, else `not_assessed`/`view_mismatch` |
| `feature` | str | named feature computed by `engine/geometry.py`: `depth_ratio`, `trunk_lean_deg`, `fppa_deg`, `lateral_shift_frac`, `hip_shoulder_rise_ratio`, `bar_drift_frac`, `hip_ext_angle_deg`, `elbow_angle_deg`, `hip_dev_frac` |
| `feature_keypoints` | list[str] | the keypoints the feature needs; drives per-rep `keypoints_not_visible` |
| `phase` | Phase | frame(s) the feature is sampled at |
| `comparator` | `gt` \| `lt` \| `abs_gt` | |
| `threshold` | float | grounded defaults per the SCOPE.md fault catalog |
| `severity` | Severity | |
| `cue` | str | the correction sentence emitted verbatim |
| `citation` | str \| null | source note for the threshold |

Invariants: `fault_id` unique within a profile; `feature` must name a feature
implemented in `geometry.py` (validated at load); signed features are reported
in the canonical anterior frame defined in SCOPE.md's fault catalog.

### Program — SQLite `program`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `created_at` | str | ISO ts (input) |
| `seed` | int | reproducibility |
| `goal`, `experience`, `days_per_week`, `emphasized_muscles`, `equipment` | snapshot of the profile at generation | programs are immutable snapshots (invariant) |
| `target_muscles` | JSON list[Muscle] | the FR-3 step-2 result, snapshotted so reports and evals can read it without recomputation |
| `weeks` | int | 5 (4 accumulation + 1 deload) |
| `split` | str | e.g. `upper_lower` |
| `status` | `active` \| `archived` | only mutable field; ≤ 1 active program (invariant) |

### ProgramSession — SQLite `program_session`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `program_id` | int FK | |
| `week` | int | 1–5 |
| `day_index` | int | 0-based within week |
| `name` | str | e.g. `Upper A` |

Invariant: unique `(program_id, week, day_index)`.

### Prescription — SQLite `prescription`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `session_id` | int FK → program_session | |
| `position` | int | order in session; compounds before isolation (invariant) |
| `exercise_id` | str FK | |
| `sets` | int | direct working sets |
| `rep_low`, `rep_high` | int | `rep_low ≤ rep_high` |
| `target_rir` | float | week-dependent (3, 2, 2, 1, 4) |
| `rest_s` | int | |
| `load_note` | str \| null | human-readable rendering, e.g. `~72% e1RM`; null when no history |

### NextPrescription — derived, never stored (FR-6 return type)

Produced at read time by `GET /programs/{id}/next` and
`formcoach program next`. Carries every `Prescription` field plus:

| Field | Type | Notes |
|---|---|---|
| `action` | ProgressionAction | exactly one, from the FR-6 ladder |
| `clause` | str | the ladder clause id that fired (`L1`…`L6`, `B1`…`B6`) — makes M6 failures debuggable |
| `suggested_load_kg` | float \| null | rounded to the exercise's equipment increment; null for bodyweight actions |
| `target_reps` | int | within `[rep_low, rep_high]`; moved by `add_reps` |
| `substituted_for` | str \| null | original exercise id when FR-13(b) substituted |
| `dropped_reason` | str \| null | e.g. `pain_flag_no_substitute`; when set, the entry is informational only |
| `rationale` | str | one sentence, e.g. "All sets hit 10 reps at RIR ≥ 2 — add 2.5 %." |

### WorkoutLog — SQLite `workout_log` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `performed_at` | str | ISO ts (input) |
| `program_session_id` | int FK \| null | null = freestyle |
| `notes` | str \| null | |

Pain is recorded **only at set level** (`SetLog.pain_flag`), so the flagged
exercise is always unambiguous; the former workout-level `pain_flag` field is
removed.

### SetLog — SQLite `set_log` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `workout_id` | int FK | |
| `exercise_id` | str FK | |
| `set_index` | int | 0-based per exercise within workout |
| `weight_kg` | float | ≥ 0, canonical kg; 0 means bodyweight |
| `reps` | int | ≥ 1 |
| `rir` | float \| null | reported; null = not rated |
| `pain_flag` | bool | per-set pain → FR-13(b) |
| `e1rm_kg` | float \| null | **derived** at insert: `weight_kg · (1 + (reps + rir)/30)` **iff `weight_kg > 0` and `rir` is not null**; otherwise null |

Invariants: append-only (no UPDATE/DELETE in the repository API; corrections
are new workouts with notes); `(workout_id, exercise_id, set_index)` unique.
The e1RM nullability rule is load-bearing: FR-6 clause L1 compares only
non-null `e1rm_kg` values, so an unrated session can never masquerade as a
5 % regression.

### FormAnalysis — SQLite `form_analysis` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `created_at` | str | ISO ts (input) |
| `analysis_kind` | AnalysisKind | `clip` (FR-10) or `photo` (FR-15) |
| `exercise_id` | str FK | must be analyzable (invariant) |
| `form_profile_id` | str | profile version used — findings stay interpretable after threshold tuning |
| `source_ref` | str | path of the clip/keypoints/image supplied |
| `pose_source` | `fixture` \| `mediapipe` | adapter used |
| `view` | View | the **resolved** view (`side_left`/`side_right`/`front`, never `side`) |
| `view_inferred` | bool | true when FR-7 step 2 or 3 resolved it |
| `declared_phase` | Phase \| null | non-null iff `analysis_kind = photo` (invariant) |
| `fps` | float \| null | null for photos |
| `frames_total`, `frames_valid` | int | visibility accounting |
| `status` | AnalysisStatus | |
| `reject_reason` | str \| null | non-null iff `status = rejected` (invariant), e.g. `insufficient_visibility` |
| `rep_count` | int \| null | **null iff `status = rejected`** (invariant); always 1 for photos |
| `clip_score` | float \| null | **derived**, null iff `status = rejected`: mean of rep scores (FR-10) |

### RepAnalysis — SQLite `rep_analysis`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `analysis_id` | int FK | |
| `rep_index` | int | 0-based; unique per analysis |
| `start_frame`, `extremum_frame`, `end_frame` | int | `start ≤ extremum ≤ end`; all 0 for photos, strictly increasing for clips (invariant) |
| `metrics` | JSON dict[str, float] | every computed feature value, faulted or not (e.g. `depth_ratio: 0.07`) |
| `score` | float | **derived**: `max(0, 100 − Σ penalties)` (major 25 / moderate 15 / minor 8) |

### FaultFinding — SQLite `fault_finding`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `rep_analysis_id` | int FK | |
| `fault_id` | str | from the form profile |
| `status` | FindingStatus | **complete matrix invariant:** exactly one row per profile rule per rep, including `ok` and `not_assessed` — this is the data M1/M4b/M7 consume |
| `not_assessed_reason` | NotAssessedReason \| null | non-null iff `status = not_assessed` (invariant) |
| `measured` | float \| null | null iff `status = not_assessed` |
| `threshold` | float | copied from the profile at analysis time |
| `severity` | Severity | |
| `frame` | int \| null | frame the feature was sampled at; null when not assessed |
| `cue` | str \| null | non-null iff `status = fault` (invariant) |

## Relationships (summary)

```
UserProfile (1) ──snapshot──▶ Program (N) ──▶ ProgramSession (N) ──▶ Prescription (N) ──▶ Exercise
Exercise (1) ──▶ MediaAsset (N)          Exercise (0..1) ──▶ FormProfile (dataset)
Exercise (0..1) ──harder_variant──▶ Exercise
WorkoutLog (N) ──▶ SetLog (N) ──▶ Exercise     WorkoutLog (0..1) ──▶ ProgramSession
FormAnalysis (N) ──▶ RepAnalysis (N) ──▶ FaultFinding (N)   FormAnalysis ──▶ Exercise
Derived, never stored: NextPrescription (FR-6 over SetLog history),
                       weekly volume report (SetLog × Exercise.muscles × VolumeLandmark)
```

## Example records

`exercise` (from `data/exercises.json`):

```json
{
  "id": "barbell-back-squat",
  "name": "Barbell Back Squat",
  "aliases": ["back squat", "squat"],
  "primary_muscles": ["quads", "glutes"],
  "secondary_muscles": ["hamstrings", "lower_back", "abs"],
  "equipment": ["barbell"],
  "pattern": "squat",
  "mechanics": "compound",
  "difficulty": 2,
  "rest_s": 210,
  "harder_variant_id": null,
  "instructions": ["Set the bar on your upper traps…", "Brace, sit down between your hips…", "Drive the floor away to stand."],
  "cues": ["big breath and brace", "knees track over toes", "hip crease below the knee"],
  "form_profile_id": "squat_v1"
}
```

`media_asset`:

```json
{
  "id": "barbell-back-squat#1",
  "exercise_id": "barbell-back-squat",
  "kind": "image",
  "source": "local",
  "ref": "media/barbell-back-squat/0.jpg",
  "license": "public-domain",
  "attribution": "free-exercise-db"
}
```

`FaultRule` inside `data/form_profiles/squat.json`:

```json
{
  "fault_id": "knee_valgus",
  "views": ["front"],
  "feature": "fppa_deg",
  "feature_keypoints": ["left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"],
  "phase": "bottom",
  "comparator": "gt",
  "threshold": 12.0,
  "severity": "major",
  "cue": "Screw your feet into the floor and push your knees out over your toes on the way up.",
  "citation": "Munro, Herrington & Carolan 2012 (FPPA norms; single-leg task context — tunable)"
}
```

`prescription` (week 2, Upper A):

```json
{
  "session_id": 7, "position": 1, "exercise_id": "barbell-bench-press",
  "sets": 4, "rep_low": 5, "rep_high": 10, "target_rir": 2.0,
  "rest_s": 180, "load_note": "~72% e1RM (est. 100 kg)"
}
```

`NextPrescription` (derived):

```json
{
  "exercise_id": "barbell-bench-press", "sets": 4, "rep_low": 5, "rep_high": 10,
  "target_rir": 2.0, "rest_s": 180, "target_reps": 10,
  "action": "increase_load", "clause": "L3", "suggested_load_kg": 82.5,
  "substituted_for": null, "dropped_reason": null,
  "rationale": "All sets hit 10 reps at RIR ≥ 2 — add 2.5%."
}
```

`set_log`:

```json
{
  "workout_id": 12, "exercise_id": "barbell-bench-press", "set_index": 0,
  "weight_kg": 80.0, "reps": 8, "rir": 2.0, "pain_flag": false, "e1rm_kg": 106.7
}
```

`fault_finding` (with its `rep_analysis`):

```json
{
  "rep_analysis_id": 31, "fault_id": "insufficient_depth", "status": "fault",
  "not_assessed_reason": null, "measured": 0.061, "threshold": 0.03,
  "severity": "major", "frame": 84,
  "cue": "Sit down another few centimetres — the hip crease must drop below the top of the knee."
}
```

`PoseSequence` frame (fixture sidecar `squat_clean_01.keypoints.json`, excerpt):

```json
{
  "fps": 30.0, "view": "side_left",
  "frames": [
    { "t_ms": 0, "keypoints": { "left_hip": {"x": 0.512, "y": 0.548, "conf": 0.97},
                                 "left_knee": {"x": 0.505, "y": 0.716, "conf": 0.95},
                                 "left_ankle": {"x": 0.501, "y": 0.884, "conf": 0.93},
                                 "left_shoulder": {"x": 0.520, "y": 0.302, "conf": 0.98} } }
  ]
}
```

A fixture sidecar may omit `"view"` entirely — those clips exercise the FR-7
inference path and are checked by M4's view-resolution term.
