# flowlist — SCOPE

## One-liner

Reorders a playlist so that as many consecutive tracks as possible transition
seamlessly into each other, using the same rules working DJs use: harmonic (key)
compatibility, BPM proximity, energy continuity, and loudness matching.

## Problem statement

A personal playlist ("liked songs", a party mix, a running playlist) is usually
ordered by date-added or by whim. Played straight through, it lurches: a 174 BPM
drum-and-bass track slams into an 82 BPM ballad, a track in F# minor clashes
into one in C major, a quiet acoustic song follows a mastered-to-the-wall club
track. DJs solve this by hand with harmonic mixing and beatmatching; nobody does
that for their own playlists. flowlist takes the playlist's tracks as fixed,
computes a pairwise *transition-compatibility score* between every pair of
tracks, and finds an ordering that maximizes total transition quality — a
max-weight Hamiltonian-path problem solved with a principled heuristic
(multi-start greedy construction + 2-opt/Or-opt local search). It then explains
every transition ("8A → 9A, adjacent on the Camelot wheel; +2.1% tempo; energy
+0.05") and exports the new order.

The product is metadata-driven, not audio-driven, in its core loop: track
features (BPM, key, mode, energy, danceability, loudness) come through adapters
— an offline catalog/import adapter by default, a streaming-metadata live
adapter, and a local-audio analyzer (librosa-style) for files the user owns.

## Target user

The repo owner: a single user running the tool locally against playlist exports
(CSV/JSON) and folders of owned audio files. No accounts, no multi-tenancy, no
web UI. API and CLI only.

## User stories

- **US-1: Import and see my playlist.** As the owner, I can import a playlist
  from a CSV/JSON export or a local music folder and list its tracks.
  *Acceptance:* `flowlist import party.csv --name party` creates a playlist;
  `flowlist show party` lists tracks in stored order with any known features;
  re-importing the same file does not duplicate tracks in the track catalog.
- **US-2: Fill in the musical facts.** As the owner, I can resolve audio
  features for every track in a playlist and see what's missing.
  *Acceptance:* `flowlist analyze party` reports coverage (e.g. "41/44 tracks
  fully featured; 3 missing key"), pulling from configured providers in
  precedence order; missing values never crash scoring.
- **US-3: Reorder for flow.** As the owner, I can reorder a playlist to
  maximize transition quality and see before/after numbers.
  *Acceptance:* `flowlist reorder party --seed 7` prints a scorecard (mean/min
  transition score before and after, count of seamless transitions and cliffs)
  and persists a run; the same command with the same seed reproduces the exact
  same order.
- **US-4: Understand why.** As the owner, I can inspect any run and see, for
  every consecutive pair, the component breakdown and the named DJ relation.
  *Acceptance:* `flowlist explain <run>` shows one line per transition with key
  relation name (e.g. "relative major"), BPM delta %, energy delta, loudness
  delta, component scores, total, and flags (`cliff`, `half_time`,
  `missing_key`).
- **US-5: Control the endpoints and the arc.** As the owner, I can pin the
  opening and/or closing track and bias the order toward rising energy for a
  party set.
  *Acceptance:* `flowlist reorder party --start-track <id> --profile build`
  returns an order that starts with the pinned track, and mean signed energy
  delta across transitions is ≥ the neutral profile's on the same input.
- **US-6: Use it with files I own.** As the owner, I can point flowlist at a
  directory of audio files; with the optional audio extra installed it measures
  BPM/key/energy/loudness itself.
  *Acceptance:* `flowlist import ~/Music/gym --name gym` then
  `flowlist analyze gym --local` produces features with `source=local_analysis`
  for decodable files; without the extra installed the command explains what to
  install and falls back to other providers.
- **US-7: Take the order with me.** As the owner, I can export a run's ordering
  as M3U (for local players), CSV, or JSON, and apply it back to the stored
  playlist.
  *Acceptance:* `flowlist export <run> --format m3u --out party.m3u` writes a
  valid M3U8 file referencing file paths where known; `--apply` on reorder (or
  `flowlist apply <run>`) rewrites stored playlist positions and records which
  run produced them.
- **US-8: Fix bad data.** As the owner, I can override any track's features
  (wrong BPM from a provider, missing key) and my override wins from then on.
  *Acceptance:* `flowlist features set <track> --bpm 128 --key 8A` stores a
  `manual` feature row; subsequent analyze/reorder uses it over all providers.

## Functional requirements

Each FR is independently testable; test/eval names reference FR ids (mapping
table in EVALS.md §7).

- **FR-1 Playlist import (CSV/JSON).** The system parses (a) CSV in the
  de-facto Exportify column layout (`Track URI, Track Name, Artist Name(s),
  Album Name, Duration (ms)` plus optional feature columns `Tempo, Key, Mode,
  Energy, Danceability, Loudness`) and (b) a documented JSON schema
  (DATA_MODEL.md §3.1). Import creates/updates Track rows keyed by a stable
  identity (DATA_MODEL.md §2.1), creates a Playlist with entries in file order,
  and stores any features carried by the file as `source=import`. Malformed
  rows are reported with line numbers and skipped, never silently dropped.
- **FR-2 Directory import.** Given a directory path, the system scans
  recursively for audio extensions (`.mp3 .m4a .flac .ogg .wav`), creates
  file-identified tracks (title/artist from filename pattern `Artist - Title`
  when tags are unavailable), and builds a playlist in lexicographic path
  order. No audio decoding happens at import time.
- **FR-3 Feature resolution.** `analyze` resolves an `AudioFeatures` record per
  track by querying providers in configurable precedence (default: `manual >
  local_analysis > streaming > import > fixture`), caching results in the
  store, and returns a coverage report: per-field counts of resolved/missing
  and per-track missing-field lists. Resolution is idempotent: a second run
  with the same providers performs no new provider calls for fully-resolved
  tracks.
- **FR-4 Manual override.** A manual feature record can be set per track (any
  subset of fields); it participates in resolution with top precedence and is
  the only mutable feature source (upsert semantics).
- **FR-5 Key math.** The engine converts between (pitch class 0–11, mode
  major/minor) and Camelot codes (`1A`–`12A` minor, `1B`–`12B` major) both
  directions, using `n_major(pc) = ((7·pc + 7) mod 12) + 1` and
  `n_minor(pc) = n_major((pc + 3) mod 12)`, and classifies any ordered key pair
  into exactly one named relation from the table in §Design-D2 (same key,
  relative, adjacent fifth, diagonal, energy boost +2, energy drop −2,
  parallel, semitone lift, clash), handling the 12↔1 wraparound.
- **FR-6 Pairwise transition score.** The engine computes
  `score(a→b) ∈ [0,1]` as a weighted sum of four component scores — key
  compatibility, BPM proximity (with half/double-time folding), energy
  continuity (with arc-profile asymmetry), loudness matching — using the exact
  formulas in §Design-D2–D5. Weights are configurable, default
  `key=0.35, bpm=0.35, energy=0.20, loudness=0.10` (a fifth component,
  danceability continuity, exists with default weight 0). Any component with
  missing inputs contributes a neutral 0.5 and adds a `missing_*` flag to the
  transition.
- **FR-7 Order scoring.** Given any ordering of a playlist's entries, the
  engine produces a `FlowReport`: per-transition breakdowns (component scores,
  relation name, deltas, flags) plus aggregates — total, mean, min, seamless
  count (score ≥ 0.70), cliff count (score < 0.40).
- **FR-8 Reordering (the hard part).** The engine finds a high-quality
  Hamiltonian path over playlist entries maximizing total transition score:
  full O(n²) score matrix (n ≤ 500 enforced with a clear error), multi-start
  greedy construction (start set per §Design-D6), then first-improvement
  local search with Or-opt (segment relocation, lengths 1–3) and 2-opt
  (segment reversal with recomputation, since the score is directional), until
  a local optimum or `max_passes` (default 50). Fully deterministic given
  (inputs, seed): seeded `random.Random`, fixed scan order, index tie-breaks.
- **FR-9 Anchors.** Reordering accepts optional fixed start and/or end entries;
  the returned order honors them, and local-search moves that would displace an
  anchor are rejected. Anchoring both endpoints with n=2 degenerates correctly.
- **FR-10 Arc profiles.** Reordering accepts `profile ∈ {neutral, build,
  cool}`; `build` multiplies the energy-component penalty for negative energy
  deltas by 1.5 (and `cool` for positive deltas), per §Design-D4. Profile
  affects only the energy component.
- **FR-11 Run persistence.** Every reorder creates an append-only `ReorderRun`
  (params, seed, engine version, before/after aggregates, coverage) with
  `RunEntry` rows storing the ordering and per-transition breakdown JSON. Runs
  are never mutated or deleted by the application.
- **FR-12 Apply & export.** A run's ordering can be (a) applied back to its
  playlist (rewrites entry positions atomically, records `applied_run_id`) and
  (b) exported as M3U8 (file-path entries where known, EXTINF with duration
  and `Artist - Title`), CSV (same columns as import), or JSON.
- **FR-13 API.** A FastAPI app exposes the endpoints in §Architecture-API with
  Pydantic request/response schemas; engine errors map to 4xx with structured
  detail (e.g. `playlist_too_large`, `unknown_track`).
- **FR-14 CLI.** A Typer CLI exposes the commands in §Architecture-CLI; every
  command returns exit code 0 on success, non-zero on failure, and supports
  `--json` for machine-readable output.
- **FR-15 Determinism.** For fixed inputs (playlist, features, params, seed),
  `reorder` returns byte-identical orderings across runs and platforms. No
  wall-clock reads inside the engine; timestamps are passed in by callers.

## Non-goals (this pass)

- **No web UI** (workspace-wide decision). CLI + API only.
- **No audio playback, crossfade rendering, or beat-grid alignment.** flowlist
  orders tracks; it does not perform the mix. Phrase alignment, cue points, and
  actual beatmatched transitions are DJ-software territory (rekordbox, Mixxx)
  and out of scope. We say *these two tracks can transition well*, not *here is
  the transition audio*.
- **No live playlist write-back to streaming services.** Export is file-based
  (M3U/CSV/JSON). Pushing reordered playlists to a streaming account via API is
  deferred to a future live adapter.
- **Live adapters are best-effort and not eval-gated.** The streaming-metadata
  live adapter and the librosa analyzer activate only with
  credentials/extras; all tests and evals run against offline adapters
  (CONVENTIONS.md §3). Notably, Spotify deprecated its public
  `audio-features` endpoint for new third-party apps in November 2024, which is
  exactly why the core loop is import/offline-first.
- **No learned similarity** (timbre embeddings, genre/lyric/"vibe" matching,
  collaborative-filtering signals). The score is rule-grounded DJ practice; an
  ML-learned transition model is a possible later phase once this baseline
  exists to evaluate against.
- **No global energy-arc shaping** (fitting the order to a target energy
  curve over set position). Arc handling is limited to the pairwise directional
  bias of FR-10, which keeps the objective pairwise-decomposable (§Design-D7).
- **No tempo-adjustment planning** beyond reporting the % pitch/tempo change a
  DJ would need. No sync instructions, no key-shift suggestions.
- **No multi-user, auth, or hosting concerns.**
- **No audio files or copyrighted metadata dumps committed to the repo.** All
  fixtures are synthetic metadata (EVALS.md §4).

## Architecture

Package `flowlist` at `projects/flowlist/src/flowlist/`. Layering per
CONVENTIONS.md: pure engine, Protocol-based adapters, Repository store, thin
API/CLI. A thin `services.py` orchestrates adapters + store + engine (the only
layer that touches both I/O and engine); API and CLI call services only.

### Engine modules (`engine/`, pure, deterministic)

- `models.py` — Pydantic domain models: `Track`, `AudioFeatures`,
  `TransitionWeights`, `ReorderParams`, `Transition`, `FlowReport`,
  `ReorderResult`.
- `keys.py` — pitch-class/mode ↔ Camelot conversion (FR-5), key-relation
  classification, `key_score(a, b) -> tuple[float, KeyRelation]`.
- `scoring.py` — component formulas (BPM folding, energy w/ profile, loudness,
  danceability), `transition_score(a, b, weights, profile) -> Transition`,
  `build_matrix(features, weights, profile) -> list[list[float]]`,
  `score_order(order, matrix | features) -> FlowReport` (FR-6, FR-7, FR-10).
- `optimizer.py` — `reorder(matrix, seed, anchors, max_passes) ->
  ReorderResult` implementing multi-start greedy + Or-opt + 2-opt (FR-8, FR-9);
  also `exact_optimal(matrix)` (Held-Karp dynamic programming, guarded to
  n ≤ 14) used by evals as ground truth.
- `explain.py` — render `Transition`/`FlowReport` into human-readable lines and
  the flag vocabulary (FR-11's stored breakdown, US-4).

### Adapter interfaces (`adapters/`)

Each is a `typing.Protocol`; offline implementations are the defaults used by
tests and evals.

- **`MetadataProvider`** — `get_features(tracks: Sequence[Track]) ->
  dict[str, AudioFeatures]` (keyed by track id; absent key = unknown track).
  - `FixtureMetadataProvider` (offline): serves a committed JSON catalog
    (`evals/fixtures/` for evals; user-suppliable catalog path in production).
    Deterministic, no network.
  - `SpotifyMetadataProvider` (live): Spotify Web API
    `GET /v1/audio-features` batch lookup by Spotify track id; activates only
    when `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` are set; degrades to
    "unknown" per-track on API errors. Marked best-effort due to the 2024
    endpoint deprecation for new apps; the interface is provider-agnostic so an
    alternative backend (e.g. a GetSongBPM-style service or an AcousticBrainz
    dump) can be slotted in without engine changes.
- **`LocalAudioAnalyzer`** — `analyze(file_path: str) -> AudioFeatures | None`.
  - `FixtureLocalAnalyzer` (offline): precomputed features keyed by file
    basename from a committed JSON map; deterministic.
  - `LibrosaLocalAnalyzer` (live, optional extra `audio`): BPM via
    `librosa.beat.beat_track` (Ellis's 2007 dynamic-programming beat tracker);
    key via chroma histogram correlated against the 24 Krumhansl–Kessler major/
    minor key profiles (Krumhansl–Schmuckler key-finding algorithm); energy
    from normalized RMS; loudness as dBFS RMS approximating integrated
    loudness. Imports librosa lazily; absence of the extra yields a clear
    error, never a crash of unrelated commands.
- **`PlaylistReader`** — `read(source) -> ImportedPlaylist` with
  implementations `CsvPlaylistReader` (Exportify layout), `JsonPlaylistReader`,
  `DirectoryPlaylistReader` (FR-1, FR-2).
- **`PlaylistWriter`** — `write(run, fmt, path)` with `M3uWriter`, `CsvWriter`,
  `JsonWriter` (FR-12).

### Store (`store/`)

`Repository` protocol with `SqliteRepository` (stdlib `sqlite3`, schema in
DATA_MODEL.md §4) and `InMemoryRepository` for tests. Operations: track upsert,
feature upsert per (track, source), playlist CRUD (entries replace-all on
apply), run append + read. No engine logic.

### API (FastAPI, `api/`)

```
GET    /health                          -> {status, version}
POST   /playlists/import                -> 201 Playlist        (FR-1/2; body: {name, format: csv|json|directory, content|path})
GET    /playlists                       -> [PlaylistSummary]
GET    /playlists/{id}                  -> Playlist with entries + features
DELETE /playlists/{id}                  -> 204
POST   /playlists/{id}/analyze          -> CoverageReport      (FR-3; body: {providers?: [...], local?: bool})
PUT    /tracks/{track_id}/features      -> AudioFeatures       (FR-4; manual override)
POST   /playlists/{id}/score            -> FlowReport          (FR-7; scores current order)
POST   /playlists/{id}/reorder          -> 201 ReorderRun      (FR-8/9/10/11; body: ReorderParams {seed, weights?, profile?, start_entry?, end_entry?, max_passes?})
GET    /runs/{run_id}                   -> ReorderRun with entries + transitions
GET    /runs?playlist_id={id}           -> [RunSummary]
POST   /runs/{run_id}/apply             -> Playlist            (FR-12)
GET    /runs/{run_id}/export?format=m3u|csv|json -> file response (FR-12)
```

### CLI (Typer, `cli/`)

```
flowlist import PATH --name NAME [--format csv|json|dir]        # FR-1/2
flowlist ls                                                      # list playlists
flowlist show PLAYLIST [--transitions]                           # FR-7 view
flowlist analyze PLAYLIST [--local] [--catalog PATH]             # FR-3
flowlist features set TRACK [--bpm F] [--key 8A|Am] [--energy F]
                            [--loudness F] [--danceability F]    # FR-4
flowlist reorder PLAYLIST [--seed N] [--profile neutral|build|cool]
                          [--start-track T] [--end-track T]
                          [--weights key=.35,bpm=.35,...] [--apply]  # FR-8/9/10
flowlist explain RUN                                             # US-4
flowlist compare RUN                                             # before/after scorecard
flowlist apply RUN                                               # FR-12
flowlist export RUN --format m3u|csv|json --out PATH             # FR-12
flowlist serve [--port 8000]                                     # uvicorn wrapper
```

All commands accept `--db PATH` (default `~/.flowlist/flowlist.db`) and
`--json`.

### Size budget (implementation phase)

engine ≈ 900 lines (keys 150, scoring 250, optimizer 350, models/explain 150);
adapters ≈ 500; store ≈ 350; api ≈ 300; cli ≈ 350; tests + evals ≈ 1,200.
Total ≈ 3,600 — within the 2,000–4,000 band, with the optimizer and scoring
(the hard part) getting the deepest treatment.

## Key design decisions and assumptions

- **D1 — Feature vocabulary is the Spotify audio-features schema.** `tempo`
  (BPM), `key` (pitch class 0–11), `mode` (0 minor / 1 major), `energy` (0–1),
  `danceability` (0–1), `loudness` (dB, typically −60..0). This is the de-facto
  interchange vocabulary: playlist CSV exports carry it, and librosa-derived
  measures map onto it. Assumption: imported feature values are trustworthy
  enough for ordering; FR-4 overrides handle the exceptions.
- **D2 — Key compatibility follows the Camelot wheel** (Mark Davis / Camelot
  Sound's "Easymix" system, popularized by Mixed In Key; Beatport/Traktor's
  Open Key notation is the same wheel relabeled). The wheel arranges keys by
  circle of fifths; +1 step = +7 semitones. Score table for
  `Δ = (n_b − n_a) mod 12` and letter change, grounded in the standard
  compatibility chart and diatonic note overlap (out of 7 scale tones):

  | Relation (a→b) | Condition | Shared tones | S_key |
  |---|---|---|---|
  | same key | Δ=0, same letter | 7/7 | 1.00 |
  | relative major/minor | Δ=0, letter swap | 7/7 | 0.95 |
  | adjacent fifth/fourth | Δ=±1, same letter | 6/7 | 0.85 |
  | diagonal | Δ=±1, letter swap | 6/7 | 0.75 |
  | energy boost (+2 semitones) | Δ=+2, same letter | 5/7 | 0.55 |
  | energy drop (−2 semitones) | Δ=−2, same letter | 5/7 | 0.45 |
  | parallel major/minor | letter swap, A→B Δ=+3 or B→A Δ=−3 | 4/7 but same tonic | 0.65 |
  | semitone lift (+1 semitone) | Δ=+7, same letter | 2/7, deliberate lift | 0.30 |
  | clash | anything else | ≤4/7 | 0.10 |

  Rationale: same/relative/adjacent are the canonical "safe" harmonic mixes;
  diagonal shares as many tones as adjacent but adds a mode flip (mood change),
  so it scores slightly lower; +2 ("energy boost") and the parallel-key mix are
  Mixed In Key's documented "advanced" moves; the semitone lift is a real but
  harmonically rough end-of-track trick. The 0.10 floor (not 0) keeps the
  optimizer's landscape informative. Asymmetry is intentional: boosts (+2)
  outrank drops (−2). Camelot relations are octave-agnostic; enharmonics
  (F#=Gb) collapse to one pitch class.
- **D3 — BPM proximity uses log-tempo distance with octave folding.** Tempo
  perception is ratio-based, so compare `log2(bpm_b / bpm_a)`. Fold by trying
  `ρ ∈ {r, 2r, r/2}` and keep the ρ minimizing `|log2 ρ|` — this legitimizes
  half/double-time mixing (an 86 BPM hip-hop groove into 172 BPM
  drum-and-bass is standard practice). Let `p = (max(ρ, 1/ρ) − 1) × 100` (%
  deviation). Piecewise score: `p ≤ 2 → 1.0`; `2 < p ≤ 6 → 1 − 0.125(p−2)`;
  `6 < p ≤ 12 → 0.5 − (p−6)/12`; `p > 12 → 0`. Grounding: classic turntable
  pitch faders run ±8% (Technics SL-1200 lineage) and CDJs default to a ±6%
  tempo range — beyond ~6% the adjustment is audible even with key-lock, and
  beyond ~12% no DJ calls it a beatmatch. A ×0.9 multiplier applies when
  folding was used (ρ ≠ r): half-time blends work but change the feel.
- **D4 — Energy continuity is a clamped linear penalty with directional
  profiles.** `S_energy = max(0, 1 − pen/0.5)` where `pen = |Δe|` under
  `neutral`; under `build`, `pen = 1.5·|Δe|` when `Δe < 0` (drops hurt more);
  `cool` mirrors it. Grounding: DJ practice manages a set's energy arc in
  small steps (Mixed In Key even assigns 1–10 "energy level" tags for this);
  a 0.5 jump on Spotify's energy scale is roughly ballad→club-banger, which no
  transition survives. Kept pairwise (not a global target curve) per D7.
- **D5 — Loudness matching uses dB difference with a 2 dB dead zone.**
  `Δl ≤ 2 → 1.0`; `2 < Δl ≤ 10 → 1 − (Δl−2)/8`; `> 10 → 0`. Grounding: the
  just-noticeable difference for broadband level is ~1 dB, so ≤2 dB is
  effectively seamless; +10 dB is perceived as roughly *twice as loud*
  (Stevens' power law), an unmistakable jump. This mirrors why streaming
  services loudness-normalize at all (EBU R 128 / ITU-R BS.1770 loudness
  measurement; Spotify normalizes playback to about −14 LUFS). Loudness gets
  the smallest default weight because normalization already compresses the
  practical range.
- **D6 — The optimizer is multi-start greedy + Or-opt + 2-opt, not exact and
  not learned.** Maximizing total transition quality over all orderings is the
  maximum-weight Hamiltonian path problem — equivalent to open-loop TSP, NP-
  hard. Playlist sizes (10–500) make exact solving infeasible but make O(n²)
  matrices and O(n²)-per-pass local search cheap. Construction: greedy
  best-next from k = min(n, 12) starts — any anchors, plus the ⌈k/2⌉ entries
  with the lowest best-incoming score (natural path endpoints), plus seeded
  random picks. Improvement: first-improvement scans alternating Or-opt
  (relocate segments of length 1–3, direction preserved — valid under an
  asymmetric score) and 2-opt (Croes 1958; reversal requires recomputing the
  reversed segment's internal edges because the score is directional), to a
  local optimum or `max_passes`. Expected quality, from TSP literature:
  nearest-neighbor alone lands well above optimum cost; NN + 2-opt typically
  lands within ~5% of optimum on random instances — our eval gates (EVALS.md
  §5) check exactly this against Held-Karp exact solutions on small instances.
  Google OR-Tools routing is an optional extra (`flowlist[ortools]`) behind the
  same interface, not required by tests or gates.
- **D7 — The objective stays pairwise-decomposable.** Global constraints
  (target energy curves, "no two songs by the same artist back-to-back") would
  break the TSP structure and the clean eval story. The only concession is the
  directional energy bias (D4), which is still an edge weight. Assumption: for
  a personal playlist, maximizing mean pairwise quality is the right proxy for
  "feels seamless end to end"; the min-score and cliff-count reporting exposes
  the residual worst cases.
- **D8 — Node = playlist entry, not track.** Duplicate tracks in a playlist are
  distinct nodes (each occurrence needs a place in the path). Feature lookups
  key on track id; the optimizer keys on entry.
- **D9 — Track identity.** `spotify:<id>` when a Spotify URI is present, else
  `file:<sha1 of file bytes>` for local files, else
  `meta:<sha1(normalize(artist) + "|" + normalize(title))>` where `normalize`
  = casefold → strip a trailing `feat./ft. …` clause → collapse whitespace →
  strip punctuation. Deterministic and collision-safe enough for a single
  user's library; collisions are repairable via FR-4 overrides.
- **D10 — Missing data degrades gracefully, never silently.** A component with
  missing inputs scores a neutral 0.5 (so unknown-key tracks aren't banished to
  the ends of every path) and flags the transition; coverage is a first-class
  report (FR-3) and is stored on every run. Assumption: for a personal tool,
  visible honesty beats imputation cleverness.
- **D11 — Hermetic evals, offline default.** Per CONVENTIONS.md: evals use
  only committed fixtures and the offline adapters; seeded randomness; time is
  an input. The librosa adapter is an optional extra exercised by
  skip-if-not-installed unit tests, not by eval gates.
- **D12 — Thresholds: seamless ≥ 0.70, cliff < 0.40.** Calibrated against D2–D5:
  a relative-key mix at 3% tempo delta, Δenergy 0.10, Δloudness 3 dB scores
  ≈ 0.89 (clearly seamless); a key clash with everything else perfect scores
  ≈ 0.69 — a key clash alone should deny "seamless", and does, narrowly. These
  two constants are shared by reporting and evals and live in one place
  (`engine/models.py`).
