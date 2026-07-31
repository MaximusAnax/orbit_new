# FormCoach — Scope

## One-liner

A single-user AI workout coach that generates genuinely science-based training
programs (goal- and muscle-targeted, volume-landmark-driven, RIR-autoregulated),
serves an exercise library with image/video references, and — the hard part —
analyzes user-supplied exercise video/photos via pose keypoints to detect
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
multi-tenancy. Not a medical device; safety behavior is implemented (see FR-13),
not just disclaimed.

## User stories & acceptance criteria

**US-1 — Program from goals.** As a lifter, I set my goal (hypertrophy /
strength / general), muscles I want to emphasize, days per week, and available
equipment, and get a 5-week program (4 accumulation weeks + deload).
*Accept:* every muscle group's planned weekly sets fall inside its configured
volume band; every trained muscle is hit ≥ 2×/week; emphasized muscles ramp
sets week-over-week; week 5 is a deload (≤ 50 % of week-4 sets, RIR ≥ 4);
rep ranges and RIR targets match the goal; only exercises matching my equipment
appear; generation is deterministic given the same profile + seed.

**US-2 — Browse the exercise library.** As a lifter, I search exercises by
muscle, equipment, or movement pattern and see instructions, cues, and image
(optionally video) references.
*Accept:* filter by any combination of primary muscle / equipment / pattern;
each result includes ≥ 1 resolvable image reference and step instructions; an
exercise with a form profile is flagged "form-analyzable".

**US-3 — Log a workout.** As a lifter, I log the session I just did (exercise,
weight, reps, RIR per set) in under a minute from the CLI.
*Accept:* sets persist append-only; estimated 1RM is computed per set; the log
can be freestyle or linked to a program session; timestamps are supplied by the
caller (engine never reads the clock).

**US-4 — Know what to do next.** As a lifter, I ask for my next session and get
concrete prescriptions — exercise, sets, target reps, target RIR, and a load
suggestion derived from my history.
*Accept:* double-progression and RIR-autoregulation rules (FR-6) are applied to
my last logs; if I hit the top of a rep range at target RIR the load increases;
if I badly missed, it holds or decreases; recommendations are reproducible from
the same history.

**US-5 — Check my weekly volume.** As a lifter, I view weekly working sets per
muscle against MEV/MAV/MRV bands so I know if I'm under- or over-shooting.
*Accept:* sets are attributed 1.0 to primary and 0.5 to secondary muscles; the
report marks each muscle below-MEV / in-band / above-MRV for a caller-supplied
ISO week.

**US-6 — Fix my form (the core feature).** As a lifter, I submit a video (or
its pre-extracted keypoints) of squat / deadlift / push-up / overhead press
with the camera view, and get a per-rep report: rep count, measured metrics
(depth, trunk angle, valgus angle, lockout…), detected faults with severity,
and one specific correction cue per fault.
*Accept:* on the committed fixture suite the analyzer meets the EVALS.md gates
(fault macro-F1 ≥ 0.80, rep-count accuracy ≥ 0.90, angle MAE ≤ 5°); every
finding carries the measured value, the threshold it violated, and a cue from
the exercise's form profile; analyses are persisted and retrievable.

**US-7 — Refuse to guess.** As a lifter, if my clip is unusable (occluded
joints, wrong view for a check), I get told *why* instead of getting confident
nonsense.
*Accept:* clips where > 30 % of frames lack required keypoints at confidence
≥ 0.3 are rejected with reason `insufficient_visibility`; checks that require a
view I didn't film are reported `not_assessed`, never guessed.

**US-8 — Train safely.** As a user, the tool never diagnoses injuries, and
reacts when I report pain.
*Accept:* program generation is blocked until I have acknowledged the
not-medical-advice notice (stored with timestamp); logging a set or note with a
pain flag triggers a stop-and-refer recommendation and excludes the flagged
exercise from the next prescription; form reports use fault/correction
language, never injury or diagnosis language (asserted by test).

## Functional requirements

Each FR is independently testable; test names reference FR ids.

- **FR-1 Profile.** CRUD a single `UserProfile`: goal (`hypertrophy` |
  `strength` | `general`), experience (`beginner`|`intermediate`|`advanced`),
  days/week (2–6), equipment set, emphasized muscles (0–3), unit system,
  disclaimer-acknowledged timestamp. Exactly one profile row may exist.
- **FR-2 Exercise library.** Load the committed exercise dataset (≈ 60
  exercises) into the store at init; query by muscle / equipment / movement
  pattern / analyzable-flag; return media references resolved through the
  `MediaResolver` adapter. A library integrity check validates that every
  exercise has ≥ 1 media asset and every asset reference resolves (offline:
  file exists in the committed manifest).
- **FR-3 Program generation.** Given a profile and seed, emit a 5-week
  mesocycle: split chosen by days/week (2–3 → full-body, 4 → upper/lower,
  5 → upper/lower + push/pull/legs hybrid, 6 → push/pull/legs ×2); exercises
  selected to cover required movement patterns within equipment constraints
  (seeded deterministic tie-breaks); per-muscle weekly sets start at MEV and
  ramp toward MAV over weeks 1–4 (emphasized muscles ramp faster, capped at
  MRV; non-emphasized may hold at MEV–MV band); per-session per-muscle sets
  capped at 10; RIR targets descend 3 → 1 across weeks 1–4; week 5 deload
  (sets halved, RIR 4–5, load basis −15 %); rep ranges by goal and exercise
  role (strength compounds 3–6, hypertrophy compounds 5–10, isolation 8–15,
  general 6–12); rest prescriptions (compound 150–300 s, isolation 60–120 s).
  Output violating any of these constraints is a defect (eval-gated at 100 %).
- **FR-4 Volume accounting.** Compute weekly working sets per muscle from set
  logs (primary 1.0 / secondary 0.5), classify each muscle against the
  committed volume-landmark dataset (below-MEV / MEV–MAV / MAV–MRV /
  above-MRV) for a caller-supplied week.
- **FR-5 Workout logging.** Append-only `WorkoutLog` + `SetLog` records;
  per-set `e1rm` derived via RIR-adjusted Epley: `e1rm = w * (1 + (reps +
  rir)/30)`; logs may reference a program session or be freestyle; invalid
  references and negative weights/reps rejected.
- **FR-6 Progression.** Pure function `next_prescription(history, prescription,
  week) -> Prescription`: (a) double progression — all sets at top of rep range
  with reported RIR ≥ target → increase load by configured increment (default
  +2.5 % upper-body, +5 % lower-body, rounded to equipment increment);
  (b) autoregulation — mean reported RIR < target − 1 → decrease load 5 %;
  mean reported RIR > target + 1 → increase 2.5 %; (c) missed volume (any set
  ≥ 2 reps under range bottom) → hold load, hold sets; (d) reactive deload
  recommendation when a lift's best e1rm drops > 5 % across two consecutive
  sessions. Deterministic; golden-scenario eval-gated.
- **FR-7 Pose ingestion.** `PoseEstimator` adapter converts a media file into a
  `PoseSequence` (COCO-17 keypoints, per-frame `(x, y, conf)` in normalized
  image coordinates, fps, declared or inferred view). Offline implementation
  reads committed `.keypoints.json` sidecars byte-deterministically; live
  implementation wraps MediaPipe Pose (optional extra). View: user declaration
  wins; heuristic fallback = normalized shoulder-width / torso-length ratio
  (front if > 0.45).
- **FR-8 Rep segmentation.** From the exercise's primary signal (squat &
  deadlift: mid-hip y; push-up: mid-shoulder y; overhead press: mid-wrist y):
  moving-average smoothing (window = fps/4), peak/valley detection with
  prominence ≥ 15 % of signal range and minimum rep duration ≥ 0.8 s. Emits
  rep boundaries (start, bottom/top extremum, end frame). Deterministic;
  eval-gated on rep-count accuracy.
- **FR-9 Fault detection.** For each rep, evaluate the exercise's
  `FormProfile` rules (geometry + thresholds committed as data, per-exercise
  catalog below). Each firing yields: fault id, severity, measured value,
  threshold, frame index, correction cue. Rules whose required view/keypoints
  are unavailable emit `not_assessed`. Eval-gated on per-fault macro-F1 and
  angle MAE.
- **FR-10 Form report.** Persist `FormAnalysis` (+ per-rep `RepAnalysis`,
  per-fault `FaultFinding`): rep count, per-rep metrics and faults, clip score
  (per-rep 100 − Σ severity penalties: major 25 / moderate 15 / minor 8, floor
  0; clip = mean), and a prioritized correction list (highest severity ×
  frequency first, deduplicated by fault id). Rejections (FR-7/US-7) persist
  with status `rejected` + reason.
- **FR-11 API.** FastAPI app exposing the endpoint sketch below; thin layer —
  validation + engine calls only.
- **FR-12 CLI.** Typer app exposing the command sketch below; same services as
  the API.
- **FR-13 Safety behavior (implemented, not disclaimed).** (a) `POST
  /programs` and `formcoach program new` fail with a specific error until
  `disclaimer_acknowledged_at` is set via an explicit ack step; (b) a set or
  workout note flagged `pain=true` produces a stop-and-refer recommendation
  and removes that exercise from the next generated prescription until the
  user clears the flag; (c) a static-string audit test asserts no engine
  output template contains diagnosis vocabulary ("injury", "tear", "hernia",
  "diagnos-"); form findings speak only of movement faults.
- **FR-14 Determinism & hermeticity.** Engine functions take time and seed as
  inputs; all tests and evals run with the offline adapters, no network, no
  clock; identical inputs produce identical program, prescription, and
  analysis outputs.

### Fault catalog (initial form profiles — the analyzable four)

Thresholds are data (per-profile JSON), defaults below with grounding.

| Exercise | View | Fault id | Geometry | Default threshold | Severity |
|---|---|---|---|---|---|
| squat | side | `insufficient_depth` | at bottom, hip-crease vs top-of-knee: `(hip_y − knee_y)/femur_len` | < 0.03 → fault (hip above knee; powerlifting depth standard) | major |
| squat | side | `excessive_trunk_lean` | shoulder→hip line vs vertical at bottom | > 55° (beyond low-bar norms; Escamilla 2001, Fry 2003) | moderate |
| squat | front | `knee_valgus` | FPPA: hip→knee vs knee→ankle frontal angle, medial | > 12° (Munro & Herrington 2012 FPPA norms) | major |
| squat | front | `lateral_shift` | mid-hip x vs mid-ankle x, normalized by hip width | > 0.15 | minor |
| deadlift | side | `hips_rise_early` | first 40 % of ascent: Δhip_y / Δshoulder_y | > 1.5 (hips rising ~without shoulder rise) | major |
| deadlift | side | `bar_drift` | wrist-x deviation from mid-ankle-x, normalized by shank length | > 0.20 (bar away from mid-foot; Hales 2010) | moderate |
| deadlift | side | `incomplete_lockout` | shoulder-hip-knee angle at top | < 170° | moderate |
| push-up | side | `insufficient_depth` | elbow angle at bottom | > 100° | moderate |
| push-up | side | `hip_sag_or_pike` | hip deviation from shoulder-ankle line, normalized by trunk length | > 0.08 (sag +, pike −) | moderate |
| push-up | side | `incomplete_lockout` | elbow angle at top | < 160° | minor |
| ohp | side | `excessive_layback` | shoulder→hip line vs vertical at top | > 20° | moderate |
| ohp | side/front | `incomplete_lockout` | elbow angle at top, wrist above head | elbow < 165° or wrist_y > nose_y | moderate |
| ohp | front | `asymmetric_press` | left/right wrist-y gap at top, normalized by torso length | > 0.10 | minor |

Known, honest limitation: COCO-17 has no spine keypoints, so lumbar
flexion/"butt wink" is **out of scope** (recorded as a non-goal, surfaced as
`not_assessed`, never guessed).

## Non-goals (this pass)

1. **No web/mobile UI** — API + CLI only (workspace-wide decision).
2. **No media hosting or transcoding.** The library stores references
   (paths/URLs, license, attribution); the live `MediaResolver` can refresh
   from an open exercise DB, but we never serve or store video bytes beyond
   user-supplied analysis clips on local disk.
3. **No live pose estimation in CI.** MediaPipe is an optional extra; tests
   and evals use committed keypoint fixtures exclusively.
4. **No real-time (in-set) coaching.** Analysis is post-hoc on a finished clip.
5. **No spine/foot-pressure faults** (butt wink, heel rise) — invisible to
   COCO-17; revisit if the pose adapter moves to a 33-point topology.
6. **No nutrition, cardio programming, or bodyweight tracking beyond the
   profile field.**
7. **No LLM anywhere.** Cues are curated strings in the form profiles;
   deterministic and hermetic by construction.
8. **No exercise-form ML classifier.** Fault detection is an interpretable
   geometric rules engine; a learned model is a possible later adapter behind
   the same interface.
9. **Multi-user, auth, sync — out.** Single local profile.

## Architecture

```
projects/formcoach/
  src/formcoach/
    engine/
      programming.py     # split selection, exercise selection, volume ramp, rep/RIR/rest assignment
      progression.py     # FR-6 next_prescription, e1RM math, deload triggers
      volume.py          # FR-4 weekly set attribution vs landmarks
      poseio.py          # PoseSequence model, view inference, visibility screening
      reps.py            # FR-8 smoothing + hysteresis rep segmentation
      geometry.py        # angles, FPPA, line-deviation primitives (pure numpy-free math)
      faults.py          # FR-9 rule evaluation against FormProfile data
      report.py          # FR-10 scoring + correction prioritization
      safety.py          # FR-13 gating + pain-flag logic
    adapters/
      pose.py            # PoseEstimator Protocol
      pose_fixture.py    #   offline: FixturePoseEstimator (reads *.keypoints.json sidecars)
      pose_mediapipe.py  #   live: MediaPipePoseEstimator (extra: `pose`)
      media.py           # MediaResolver Protocol
      media_local.py     #   offline: LocalMediaResolver (committed manifest + local files)
      media_wger.py      #   live: WgerMediaResolver (wger.de / free-exercise-db refresh; env-configured)
    store/               # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/                 # FastAPI app
    cli/                 # Typer app
  data/                  # committed datasets: exercises.json, media_manifest.json,
                         # volume_landmarks.json, form_profiles/*.json
  evals/                 # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated) |
|---|---|---|
| `PoseEstimator.estimate(media_path, *, declared_view) -> PoseSequence` | `FixturePoseEstimator` — loads `<media>.keypoints.json` sidecar; raises if missing | `MediaPipePoseEstimator` — MediaPipe Pose → COCO-17 mapping; extra `formcoach[pose]` |
| `MediaResolver.resolve(exercise_id) -> list[MediaAsset]`, `.verify(asset) -> bool` | `LocalMediaResolver` — committed `media_manifest.json`, verifies local placeholder files | `WgerMediaResolver` — refreshes image/video URLs from the open wger / free-exercise-db datasets; `FORMCOACH_MEDIA_LIVE=1` |

### API sketch (FastAPI)

```
GET  /health
GET  /profile                          PUT /profile          POST /profile/acknowledge-disclaimer
GET  /exercises?muscle=&equipment=&pattern=&analyzable=
GET  /exercises/{id}                   # includes resolved media assets
POST /programs                         # body: overrides + seed + as_of date → generated mesocycle
GET  /programs/{id}                    GET /programs/{id}/sessions/{week}/{day}
GET  /programs/{id}/next?as_of=        # next session with FR-6-adjusted prescriptions
POST /workouts                         # log session + sets (append-only)
GET  /workouts?since=                  GET /analytics/volume?iso_week=
POST /form/analyses                    # multipart clip OR keypoints JSON + exercise_id + view
GET  /form/analyses/{id}               GET /form/analyses?exercise_id=
```

### CLI sketch (Typer)

```
formcoach init                                  # create DB, seed library, run integrity check
formcoach profile show|set ... |ack-disclaimer
formcoach exercises list [--muscle --equipment --pattern --analyzable] | show <id>
formcoach program new [--seed --goal --days ...] | show <id> | next [--as-of]
formcoach log --exercise <id> --sets "100x8@2,100x8@2,100x7@1" [--pain] [--session ...]
formcoach volume [--iso-week]
formcoach form analyze <clip-or-keypoints> --exercise squat --view side [--as-of]
formcoach form show <analysis-id> | list
```

## Key design decisions & assumptions

1. **Weekly per-muscle volume is the programming backbone.** Dose-response
   meta-analysis shows ≥ 10 weekly sets per muscle outperforms fewer for
   hypertrophy (Schoenfeld, Ogborn & Krieger 2017, *J Sports Sci*). We encode
   per-muscle bands as data using the MV/MEV/MAV/MRV volume-landmark framework
   (Israetel et al., Renaissance Periodization / *Scientific Principles of
   Hypertrophy Training*), committed in `data/volume_landmarks.json` and
   tunable without code changes.
2. **Frequency ≥ 2×/week per muscle.** Training a muscle twice weekly beats
   once at equal volume trends (Schoenfeld, Ogborn & Krieger 2016, *Sports
   Med*); even where later work (Schoenfeld, Grgic & Krieger 2019) shows
   volume dominates, splitting volume across ≥ 2 sessions keeps per-session
   sets ≤ 10 for rep quality. Hence the split table in FR-3.
3. **Rep ranges by goal, not myths.** The "repetition continuum" re-examination
   (Schoenfeld et al. 2021, *Sports*) shows hypertrophy across ~30–85 % 1RM
   when sets approach failure, while maximal strength needs heavy loading —
   so strength compounds get 3–6 reps, hypertrophy work 5–15 with effort
   equalized by RIR, per ACSM progression-model guidance (ACSM Position Stand
   2009).
4. **Effort is prescribed as RIR.** The resistance-training-specific RPE/RIR
   scale (Zourdos et al. 2016, *JSCR*; Helms et al. 2016) anchors targets:
   accumulation weeks descend RIR 3 → 1 — close enough to failure to be
   stimulative (proximity-to-failure meta: Refalo et al. 2023, *Sports Med*)
   without week-1 failure training. Autoregulation compares reported vs
   target RIR (FR-6).
5. **Load math via RIR-adjusted Epley.** `e1rm = w(1 + (reps + rir)/30)`
   (Epley 1985 formula; RIR extension per common Helms/RTS practice). Chosen
   over Brzycki for stability at higher reps; a pure function so swapping is
   trivial.
6. **Mesocycle = 4 + 1 deload, set-ramp progression.** Accumulation-then-
   deload periodization per RP practice and ACSM progression principles;
   deloads implement fatigue management (halved sets, RIR 4–5, −15 % load).
   Reactive deload trigger uses e1rm regression (> 5 % drop twice) as a
   simple overreach proxy.
7. **Form-fix is an interpretable geometric rules engine over keypoints, not a
   classifier.** Faults must come with *measured value + violated threshold +
   cue* to be coaching, not vibes. Thresholds are grounded where literature
   exists — FPPA valgus norms (Munro, Herrington & Carolan 2012), squat
   depth = hip crease below knee (powerlifting standard), squat trunk-lean
   kinematics (Escamilla 2001; Fry, Smith & Schilling 2003), deadlift bar
   path over mid-foot (Hales 2010) — and live as per-profile data so they can
   be tuned per user without touching code.
8. **COCO-17 topology** (Lin et al. 2014, COCO) is the pose contract: MoveNet
   emits it natively and MediaPipe Pose (BlazePose, Bazarevsky et al. 2020)
   maps onto it, so the fixture format outlives any one estimator. Cost:
   no spine/foot detail (non-goal #5).
9. **Rejection over hallucination.** Visibility screening (FR-7/US-7) and
   `not_assessed` statuses are core behavior with their own eval metric —
   a coach that guesses on occluded video is worse than none.
10. **Pose estimation and media resolution are the only external
    capabilities**, hence exactly two adapter pairs. No LLM adapter: curated
    cue strings are better coaching *and* keep evals hermetic.
11. **Library seeded from open data.** ~60 exercises adapted from the
    public-domain free-exercise-db dataset (images included) with wger.de as
    the live refresh source (CC-licensed); license + attribution stored per
    asset. This satisfies "images and maybe videos" with references, not
    hosting; video URL slots exist but ship empty except where wger provides
    them.
12. **Determinism everywhere** (FR-14): seeds and timestamps are inputs;
    program generation uses a seeded RNG only for tie-breaking equivalent
    exercises. Required by workspace conventions and what makes the eval
    gates meaningful.
13. **Health safeguard is behavior** (FR-13), per the workspace quality bar:
    ack-gating, pain-flag consequences, and a vocabulary audit test — not a
    footer string.
14. **Assumption: single camera, roughly stationary, whole body in frame,
    one person.** Multi-person disambiguation and camera motion compensation
    are out of scope; visibility screening catches most violations.
15. **Assumption: 2-D analysis is sufficient for the shipped fault set.** All
    catalog faults are measurable in the declared 2-D view; 3-D lifting-plane
    faults are deferred with the topology upgrade (non-goal #5).
