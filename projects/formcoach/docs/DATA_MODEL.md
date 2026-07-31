# FormCoach — Data Model

Two storage classes, per workspace conventions:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/formcoach/data/`): exercise library, media manifest, volume
  landmarks, form profiles. Loaded/validated into SQLite at `formcoach init`;
  the files remain the source of truth.
- **SQLite** (user state, default `~/.formcoach/formcoach.db`, path
  configurable; in-memory backend for tests): profile, programs, logs, form
  analyses. Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/formcoach/models.py` (or a `models/`
package); the store maps them to the tables below. Enumerations are Python
`StrEnum`s; SQLite stores their string values. Timestamps are ISO-8601 UTC
strings supplied by callers (engine never reads the clock).

## Enumerations

| Enum | Values |
|---|---|
| `Goal` | `hypertrophy`, `strength`, `general` |
| `Experience` | `beginner`, `intermediate`, `advanced` |
| `Muscle` | `chest`, `front_delts`, `side_delts`, `rear_delts`, `lats`, `upper_back`, `lower_back`, `biceps`, `triceps`, `forearms`, `quads`, `hamstrings`, `glutes`, `calves`, `abs` |
| `Equipment` | `barbell`, `dumbbell`, `machine`, `cable`, `bodyweight`, `band`, `kettlebell` |
| `MovementPattern` | `squat`, `hinge`, `horizontal_push`, `horizontal_pull`, `vertical_push`, `vertical_pull`, `lunge`, `isolation`, `carry`, `core` |
| `Mechanics` | `compound`, `isolation` |
| `View` | `side_left`, `side_right`, `front` |
| `Severity` | `major`, `moderate`, `minor` |
| `AnalysisStatus` | `completed`, `rejected` |
| `FindingStatus` | `ok`, `fault`, `not_assessed` |
| `Unit` | `kg`, `lb` |

## Entities

### UserProfile — SQLite `user_profile` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1`; exactly one row (invariant) |
| `goal` | Goal | |
| `experience` | Experience | scales starting volume within MEV band |
| `days_per_week` | int | 2–6 |
| `equipment` | JSON list[Equipment] | non-empty |
| `emphasized_muscles` | JSON list[Muscle] | 0–3 entries, unique |
| `unit` | Unit | display + increment rounding |
| `bodyweight` | float \| null | for bodyweight-load estimates only |
| `disclaimer_acknowledged_at` | str \| null | ISO ts; program generation blocked while null (FR-13) |
| `pain_flags` | JSON list[str] | exercise ids currently flagged; cleared explicitly |
| `updated_at` | str | ISO ts, caller-supplied |

### Exercise — committed `data/exercises.json` → table `exercise`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | slug, e.g. `barbell-back-squat` |
| `name` | str | |
| `aliases` | list[str] | |
| `primary_muscles` | list[Muscle] | ≥ 1 |
| `secondary_muscles` | list[Muscle] | may be empty; disjoint from primary (invariant) |
| `equipment` | list[Equipment] | any-of |
| `pattern` | MovementPattern | |
| `mechanics` | Mechanics | |
| `difficulty` | int | 1–3; beginner profiles avoid 3 where a 1–2 alternative covers the pattern |
| `instructions` | list[str] | ordered steps |
| `cues` | list[str] | short coaching cues |
| `form_profile_id` | str \| null | non-null ⇒ "analyzable" (squat, deadlift, push-up, ohp at MVP) |

Invariants: dataset read-only at runtime; every `pattern` × common
`Equipment` combination needed by FR-3 split templates has ≥ 1 exercise
(validated at init); every exercise has ≥ 1 `MediaAsset` (FR-2 integrity
check).

### MediaAsset — committed `data/media_manifest.json` → table `media_asset`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `<exercise_id>#<n>` |
| `exercise_id` | str FK → exercise | |
| `kind` | `image` \| `video` | |
| `source` | `local` \| `url` | |
| `ref` | str | relative path under `data/media/` or https URL |
| `license` | str | e.g. `public-domain`, `CC-BY-SA-4.0` |
| `attribution` | str \| null | required when license demands it (invariant) |

Invariants: `ref` must resolve via the active `MediaResolver` (offline: file
exists; live: HTTP 200) — checked by `formcoach init` integrity check, not per
request. Assets are references only; no bytes in the DB.

### VolumeLandmark — committed `data/volume_landmarks.json` → table `volume_landmark`

Per-muscle weekly-set bands adapted from the RP volume-landmark framework
(Israetel et al.); values are data, not code, so the owner can tune them.

| Field | Type | Notes |
|---|---|---|
| `muscle` | Muscle PK | |
| `mv` | int | maintenance volume |
| `mev` | int | minimum effective volume |
| `mav` | int | maximum adaptive volume (ramp target) |
| `mrv` | int | maximum recoverable volume |

Invariant: `mv ≤ mev < mav ≤ mrv`.

### FormProfile — committed `data/form_profiles/<exercise>.json` (not a table; loaded by engine)

| Field | Type | Notes |
|---|---|---|
| `id` | str | e.g. `squat_v1`; version suffix bumps when thresholds change |
| `exercise_id` | str | matches `exercise.form_profile_id` back-reference |
| `primary_signal` | str | keypoint expression for rep segmentation, e.g. `mid_hip.y` |
| `direction` | `down_up` \| `up_down` | whether a rep starts by descending or ascending |
| `min_rep_duration_s` | float | default 0.8 |
| `prominence_frac` | float | default 0.15 |
| `smoothing_window_frac` | float | window = `fps * frac`, default 0.25 |
| `required_keypoints` | dict[View, list[str]] | visibility screening set per view |
| `rules` | list[FaultRule] | see below |

`FaultRule` (embedded):

| Field | Type | Notes |
|---|---|---|
| `fault_id` | str | e.g. `knee_valgus` |
| `views` | list[View] | rule only evaluated in these views, else `not_assessed` |
| `feature` | str | named geometric feature computed by `engine/geometry.py` (e.g. `fppa_deg`, `trunk_lean_deg`, `depth_ratio`, `hip_shoulder_rise_ratio`) |
| `phase` | `bottom` \| `top` \| `ascent_early` \| `whole_rep` | frame(s) the feature is sampled at |
| `comparator` | `gt` \| `lt` \| `abs_gt` | |
| `threshold` | float | grounded defaults per SCOPE.md fault catalog |
| `severity` | Severity | |
| `cue` | str | the correction sentence emitted verbatim |
| `citation` | str \| null | source note for the threshold |

Invariant: `fault_id` unique within a profile; `feature` must name a feature
implemented in `geometry.py` (validated at load).

### Program — SQLite `program`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `created_at` | str | ISO ts (input) |
| `seed` | int | reproducibility |
| `goal`, `days_per_week`, `emphasized_muscles`, `equipment` | snapshot of profile at generation | programs are immutable snapshots (invariant) |
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
| `sets` | int | |
| `rep_low`, `rep_high` | int | `rep_low ≤ rep_high` |
| `target_rir` | float | week-dependent (3 → 1; deload 4–5) |
| `rest_s` | int | |
| `load_note` | str \| null | e.g. `~72% e1RM` when history exists; null week 1 for new lifts |

Derived, never stored: next-session load suggestions (FR-6 computes from logs
at read time).

### WorkoutLog — SQLite `workout_log` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `performed_at` | str | ISO ts (input) |
| `program_session_id` | int FK \| null | null = freestyle |
| `notes` | str \| null | |
| `pain_flag` | bool | triggers FR-13(b) |

### SetLog — SQLite `set_log` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `workout_id` | int FK | |
| `exercise_id` | str FK | |
| `set_index` | int | 0-based per exercise within workout |
| `weight` | float | ≥ 0; unit from profile; 0 allowed for bodyweight |
| `reps` | int | ≥ 1 |
| `rir` | float \| null | reported; null = not rated |
| `pain_flag` | bool | per-set pain (FR-13) |
| `e1rm` | float \| null | **derived** at insert: `weight * (1 + (reps + (rir or 0))/30)`; null for bodyweight (weight 0) |

Invariants: append-only (no UPDATE/DELETE in repository API; corrections are
new workouts with notes); `(workout_id, exercise_id, set_index)` unique.

### FormAnalysis — SQLite `form_analysis` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `created_at` | str | ISO ts (input) |
| `exercise_id` | str FK | must be analyzable (invariant) |
| `form_profile_id` | str | profile version used — findings stay interpretable after threshold tuning |
| `source_ref` | str | path of the clip/keypoints file supplied |
| `pose_source` | `fixture` \| `mediapipe` | adapter used |
| `view` | View | declared or inferred (recorded which via `view_inferred` bool) |
| `fps` | float | |
| `frames_total`, `frames_valid` | int | visibility accounting |
| `status` | AnalysisStatus | `rejected` ⇒ `reject_reason` set, no reps (invariant) |
| `reject_reason` | str \| null | e.g. `insufficient_visibility` |
| `rep_count` | int | |
| `clip_score` | float | **derived**: mean of rep scores (see FR-10) |

### RepAnalysis — SQLite `rep_analysis`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `analysis_id` | int FK | |
| `rep_index` | int | 0-based; unique per analysis |
| `start_frame`, `extremum_frame`, `end_frame` | int | `start < extremum < end` (invariant) |
| `metrics` | JSON dict[str, float] | every computed feature value, faulted or not (e.g. `depth_ratio: 0.07`) |
| `score` | float | **derived**: `max(0, 100 − Σ penalties)` |

### FaultFinding — SQLite `fault_finding`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `rep_analysis_id` | int FK | |
| `fault_id` | str | from the form profile |
| `status` | FindingStatus | one row per rule per rep, including `ok` / `not_assessed` (invariant: complete matrix — makes evals and trend queries honest) |
| `measured` | float \| null | null when `not_assessed` |
| `threshold` | float | copied from profile at analysis time |
| `severity` | Severity | |
| `frame` | int \| null | frame where measured |
| `cue` | str \| null | only when `status = fault` |

## Relationships (summary)

```
UserProfile (1) ──snapshot──▶ Program (N) ──▶ ProgramSession (N) ──▶ Prescription (N) ──▶ Exercise
Exercise (1) ──▶ MediaAsset (N)          Exercise (0..1) ──▶ FormProfile (dataset)
WorkoutLog (N) ──▶ SetLog (N) ──▶ Exercise     WorkoutLog (0..1) ──▶ ProgramSession
FormAnalysis (N) ──▶ RepAnalysis (N) ──▶ FaultFinding (N)   FormAnalysis ──▶ Exercise
Weekly volume report: derived from SetLog × Exercise.muscles × VolumeLandmark (never stored)
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

`volume_landmark`:

```json
{ "muscle": "quads", "mv": 6, "mev": 8, "mav": 16, "mrv": 20 }
```

`FaultRule` inside `data/form_profiles/squat.json`:

```json
{
  "fault_id": "knee_valgus",
  "views": ["front"],
  "feature": "fppa_deg",
  "phase": "bottom",
  "comparator": "gt",
  "threshold": 12.0,
  "severity": "major",
  "cue": "Screw your feet into the floor and push your knees out over your toes on the way up.",
  "citation": "Munro, Herrington & Carolan 2012 (FPPA norms)"
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

`set_log`:

```json
{
  "workout_id": 12, "exercise_id": "barbell-bench-press", "set_index": 0,
  "weight": 80.0, "reps": 8, "rir": 2.0, "pain_flag": false, "e1rm": 106.7
}
```

`fault_finding` (with its `rep_analysis`):

```json
{
  "rep_analysis_id": 31, "fault_id": "insufficient_depth", "status": "fault",
  "measured": -0.06, "threshold": 0.03, "severity": "major", "frame": 84,
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
