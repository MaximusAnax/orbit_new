# FormCoach

A single-user AI workout coach that does two hard things well:

1. **Programs training the way the literature says to** — per-muscle weekly
   volume landmarks (MV/MEV/MAV/MRV), ≥ 2 sessions per muscle per week,
   goal-appropriate rep ranges, an RIR ramp of 3 → 1 across four accumulation
   weeks and a fifth deload week, with an RIR-autoregulated progression ladder
   on top of your own logs.
2. **Tells you what is wrong with your squat** — you hand it a clip (or its
   pre-extracted COCO-17 keypoints) and it segments reps, measures joint
   geometry, and emits *specific* faults with the measured value, the threshold
   that was violated, the frame it happened on, and one correction cue. When it
   cannot read a clip it says why instead of guessing.

Everything is local, deterministic and offline: no accounts, no LLM, no network.
Pose estimation and media resolution are the only external capabilities, and
both sit behind adapters with an offline default.

**Not a medical device.** Program generation is blocked until you explicitly
acknowledge that; logging a painful set flags the exercise, returns a
stop-and-refer recommendation and substitutes it in later prescriptions until
you clear the flag by hand. Form reports talk about movement faults and never
about what might be wrong with your body — a test asserts that no cue, rationale
or error message contains diagnosis vocabulary.

---

## Quickstart

```bash
cd projects
uv sync --all-packages

uv run formcoach --db /tmp/fc.db init
uv run formcoach --db /tmp/fc.db profile set \
    --goal hypertrophy --experience intermediate --days 4 \
    -e barbell -e dumbbell -e cable -e machine -e bodyweight -m chest
uv run formcoach --db /tmp/fc.db profile ack-disclaimer
uv run formcoach --db /tmp/fc.db program new --seed 20260731 --as-of 2026-07-06
```

The database defaults to `~/.formcoach/formcoach.db` (override with `--db` or
`$FORMCOACH_DB`). The committed datasets default to the packaged `data/`
directory (override with `--data-dir` or `$FORMCOACH_DATA_DIR`).

---

## CLI

```
formcoach init
formcoach profile show | set … | ack-disclaimer | clear-pain <exercise-id>
formcoach exercises list [--muscle --equipment --pattern --analyzable] | show <id>
formcoach program new [--seed --as-of --goal --days --experience] | show [<id>] | next [<id>]
formcoach log --exercise <id> --sets "100x8@2,100x8@2,100x7@1" [--pain] [--session N]
formcoach volume [--iso-week 2026-W28]
formcoach form analyze <clip-or-keypoints> --exercise <id> [--view side] [--as-of]
formcoach form photo <image-or-keypoints> --exercise <id> --phase bottom [--view]
formcoach form show <analysis-id> | list [--exercise <id>]
```

Every command takes an explicit timestamp (`--at` / `--as-of`) and falls back to
the wall clock only at that boundary; nothing deeper ever reads it. Errors print
`error <code>: <message>` on stderr and exit non-zero (2 for a malformed set
spec, 1 for everything else).

### Real output

Generating a mesocycle:

```
$ formcoach program new --seed 20260731 --as-of 2026-07-06
program 1: upper_lower, 4 days/week, 5 weeks, seed 20260731
targets: chest, quads, lats, upper_back, hamstrings, side_delts, triceps, biceps, glutes, calves
  chest        weekly effective sets [10, 10, 10, 10, 5]
  quads        weekly effective sets [10, 10, 10, 10, 5]
  lats         weekly effective sets [10, 10, 10, 10, 5]
  upper_back   weekly effective sets [9, 9, 9, 9, 4]
  hamstrings   weekly effective sets [8, 8, 8, 8, 4]
  side_delts   weekly effective sets [10, 10, 10, 10, 5]
  triceps      weekly effective sets [8, 8, 8, 8, 4]
  biceps       weekly effective sets [8, 8, 8, 8, 4]
  glutes       weekly effective sets [6, 6, 6, 6, 3]
  calves       weekly effective sets [9, 9, 9, 9, 4]
sessions: 20 (use 'formcoach program show 1')
```

(The plan is flat here because the committed landmarks make the `22 × days`
weekly budget bind by week 1 at four days a week — the ramp only has headroom at
five and six days. That clamp is FR-3 step 3 and is checked by M5 constraint 14.)

Reading a session:

```
$ formcoach program show 1 --week 1
program 1 — week 1 of 5 (upper_lower)
  day 0: Upper A
    1. dumbbell-row                   3 x 5-10 @ RIR 3, rest 150s
    2. close-grip-bench-press         4 x 5-10 @ RIR 3, rest 180s
    3. dumbbell-shoulder-press        2 x 5-10 @ RIR 3, rest 150s
    4. chin-up                        3 x 5-10 @ RIR 3, rest 180s
    5. cable-lateral-raise            3 x 8-15 @ RIR 3, rest 75s
```

Logging a set block (weights go in in your display unit, storage is always kg):

```
$ formcoach log --exercise barbell-bench-press --sets "80x10@2,80x10@2,80x9@1"
logged workout 1 (3 sets)
  set 1: 80 kg x 10 @ RIR 2, e1RM 112.0 kg
  set 2: 80 kg x 10 @ RIR 2, e1RM 112.0 kg
  set 3: 80 kg x 9 @ RIR 1, e1RM 106.7 kg
```

Fixing your form:

```
$ formcoach form analyze evals/fixtures/poses/squat_side_02.keypoints.json \
      --exercise barbell-back-squat --view side_left
analysis 1: barbell-back-squat (clip, profile squat_v1)
  view side_left, reps 4, score 81.2/100
  rep 1: score 75
    major     insufficient_depth     measured 0.139 vs threshold 0.03 (frame 22)
    not assessed knee_valgus            (view_mismatch)
    not assessed lateral_shift          (view_mismatch)
  rep 2: score 100
    not assessed insufficient_depth     (keypoints_not_visible)
    not assessed knee_valgus            (view_mismatch)
    not assessed lateral_shift          (view_mismatch)
  rep 3: score 75
    major     insufficient_depth     measured 0.143 vs threshold 0.03 (frame 105)
    not assessed knee_valgus            (view_mismatch)
    not assessed lateral_shift          (view_mismatch)
  rep 4: score 75
    major     insufficient_depth     measured 0.070 vs threshold 0.03 (frame 149)
    not assessed knee_valgus            (view_mismatch)
    not assessed lateral_shift          (view_mismatch)
  corrections, most important first:
    [major] insufficient_depth x3: Sit down another few centimetres - the hip crease must drop below the top of the knee.
```

Refusing to guess, with a reason:

```
$ formcoach form analyze evals/fixtures/poses/invalid_03.keypoints.json \
      --exercise barbell-deadlift --view side_right
analysis 1: REJECTED (insufficient_visibility)
  view side_right, 65/170 frames usable
```

Weekly volume against the landmarks:

```
$ formcoach volume --iso-week 2026-W28
week 2026-W28
muscle          eff  direct  band        target
chest           3.0       3  below MEV   yes   (MEV 8, MAV 16, MRV 22)
front_delts     1.5       0  MEV-MAV     -   (MEV 0, MAV 6, MRV 12)
side_delts      0.0       0  below MEV   yes   (MEV 8, MAV 16, MRV 26)
…
```

---

## API

```bash
uv run uvicorn formcoach.api.app:app --reload
# interactive docs at http://127.0.0.1:8000/docs
```

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | library and profile status |
| GET / PUT | `/profile` | 404 `profile_not_found` until created |
| POST | `/profile/acknowledge-disclaimer` | FR-13(a) gate |
| DELETE | `/profile/pain-flags/{exercise_id}?cleared_at=` | the only way a flag clears |
| GET | `/exercises?muscle=&equipment=&pattern=&analyzable=` | library query |
| GET | `/exercises/{id}` | includes resolved media assets |
| POST | `/programs` | 201; 409 `disclaimer_not_acknowledged` |
| GET | `/programs/{id}` · `/programs/{id}/sessions/{week}/{day}` | |
| GET | `/programs/{id}/next` | FR-6 ladder + FR-13(b) substitution; 404 `program_complete` |
| POST | `/workouts` | 201, append-only; converts lb → kg |
| GET | `/workouts?since=` | |
| GET | `/analytics/volume?iso_week=` | all 15 muscles vs MEV/MAV/MRV |
| POST | `/form/analyses` · `/form/photo-analyses` | 201; body carries `source` (path) or inline `keypoints` |
| GET | `/form/analyses?exercise_id=` · `/form/analyses/{id}` | |

Errors are `{"error": {"code": …, "message": …}}`. The catalog:
`profile_not_found`, `exercise_not_found`, `program_not_found`,
`session_not_found`, `analysis_not_found`, `program_complete`,
`no_active_program` (404); `disclaimer_not_acknowledged` (409);
`invalid_reference`, `not_analyzable`, `pose_unavailable`,
`program_generation_failed` (400); `dataset_error` (500). Request-shape errors
are FastAPI's own 422.

---

## Evals

```bash
cd projects
uv run python formcoach/evals/run.py     # scorecard, exits non-zero on any failed gate
uv run pytest formcoach/                 # unit + integration tests and the gate tests
```

The suite is hermetic: committed fixtures, offline adapters, seeded randomness,
every timestamp supplied by the fixture. Current scorecard:

| metric | what it measures | naive baseline | value | gate |
|---|---|---|---|---|
| M1 | fault-detection macro-F1 over 10 `(exercise, fault)` classes | 0.537 | **0.957** | ≥ 0.80 |
| M1b | worst-class F1 floor | 0.361 | **0.833** | ≥ 0.55 |
| M2 | rep-count exact accuracy over 50 valid clips | 0.000 | **1.000** | ≥ 0.90 |
| M3a | angle MAE (262 instances) | 14.91° | **1.93°** | ≤ 5° |
| M3b | ratio MAE (272 instances) | 0.520 | **0.026** | ≤ 0.03 |
| M4 | screening + view resolution (62 decisions) | 0.839 | **1.000** | = 1.0 |
| M4b | never guess where you cannot look (225 triples) | 0.000 | **1.000** | = 1.0 |
| M7 | unjustified refusal rate (534 assessable triples) | 1.000 | **0.000** | ≤ 0.02 |
| M5 | program constraints (120 profiles × 15) | 0.411 | **1.000** | = 1.0 |
| M6 | progression decisions (28 golden scenarios) | 0.107 | **1.000** | = 1.0 |
| M8 | real-clip agreement | — | *not available* | reported only |

Baselines are computed live, not hardcoded, and `test_baselines_are_beaten_by_gates`
fails if any gate stops being strictly harder than its naive baseline.

### Fixtures

`evals/fixtures/generate_poses.py --seed 20260731` builds the whole pose corpus
and is committed alongside its output: 58 clips (50 valid, of which 4 omit the
camera view and one sits at exactly 0.28 invisible frames; 8 invalid, one of
them at exactly 0.32) plus 6 single-frame photo fixtures. The generator
**must not import `src/formcoach`** — a test scans its imports — and every
ground-truth value is computed analytically from the injection parameters before
any noise is applied. Noise is deliberately unkind: AR(1) jitter (ρ = 0.6,
per-clip σ), clips at 24/30/60 fps, high-confidence single-frame teleports,
slow camera drift and depth-dependent perspective scaling.

`evals/fixtures/labels_handcheck.json` closes the circularity loophole from the
other side: for eight committed reps it stores feature values recomputed from
the committed keypoints by the generator's *own* geometry (law of cosines rather
than dot products), and `test_handcheck_features_match_engine` checks the engine
against them inside the M3 gate. A shared sign or normalization error fails
there even when generator and engine agree with one another.

`profiles_grid.json`, `split_templates.json`, `required_coverage.json` and
`progression_golden.json` are hand-authored. The split table and the coverage
table are deliberately *not* derived from `data/`, so a divergence between the
engine's datasets and the eval's expectations is a failure.

---

## Live adapters

Both external capabilities default to an offline implementation. Tests and
evals never touch the live ones.

| Capability | Offline (default) | Live | Activation |
|---|---|---|---|
| `PoseEstimator` | `FixturePoseEstimator` — reads `*.keypoints.json` sidecars | `MediaPipePoseEstimator` — MediaPipe Pose → COCO-17 | install `mediapipe` into the environment; the adapter raises a clear error if it is missing, and is never imported by the offline path |
| `MediaResolver` | `LocalMediaResolver` — `source=local` verified by file existence, `source=url` by manifest schema only, never fetched | `WgerMediaResolver` — refreshes URLs from wger and HTTP-verifies them | `FORMCOACH_MEDIA_LIVE=1` (optionally `FORMCOACH_WGER_BASE_URL`) |

MediaPipe is intentionally **not** a declared dependency: it is a large binary
wheel and the workspace shares one virtualenv. Install it yourself when you want
live pose extraction (`uv pip install mediapipe`), then pass
`MediaPipePoseEstimator()` to `FormCoachService(..., pose_estimator=…)`. Nothing
else changes: the adapter emits the same `PoseSequence` the fixtures do.

No credentials are needed anywhere — wger's exercise API is public — and no
secret is read from anywhere but the environment variables named above.

---

## Layout

```
src/formcoach/
  models.py      all Pydantic v2 domain models and their invariants
  engine/        pure domain logic: programming, progression, volume, poseio,
                 reps, geometry, faults, report, safety
  adapters/      PoseEstimator and MediaResolver, offline + live
  store/         Repository protocol, SQLite and in-memory backends, dataset loader
  services.py    the application service both surfaces call
  api/ cli/      thin FastAPI and Typer surfaces
data/            committed datasets: 68 exercises, media manifest + files, volume
                 landmarks, target-muscle policy, split templates, load
                 increments, three form profiles
evals/           fixtures, metrics, scorecard, gate tests
docs/            SCOPE.md, DATA_MODEL.md, EVALS.md, REVIEW.md
```

Read `docs/SCOPE.md` for the functional requirements and the algorithms, and
`docs/REVIEW.md` for the decisions taken where the frozen specs left a gap.
