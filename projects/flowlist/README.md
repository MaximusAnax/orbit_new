# flowlist

Reorders a playlist so that as many consecutive tracks as possible transition
*seamlessly* into each other, using the rules working DJs use: harmonic
(Camelot wheel) compatibility, BPM proximity with half/double-time folding,
energy continuity, and loudness matching.

A personal playlist is usually ordered by date-added or by whim. Played
straight through it lurches — a 174 BPM drum-and-bass track slams into an
82 BPM ballad, F# minor clashes into C major, a quiet acoustic song follows a
mastered-to-the-wall club track. flowlist takes the tracks as fixed, scores
every ordered pair, and searches for the ordering that maximises total
transition quality (a max-weight Hamiltonian path — open-loop TSP), then
explains every seam and exports the result.

Design, data model, evaluation plan and the scoping critique log live in
[`docs/`](docs/): [SCOPE.md](docs/SCOPE.md), [DATA_MODEL.md](docs/DATA_MODEL.md),
[EVALS.md](docs/EVALS.md), [REVIEW.md](docs/REVIEW.md).

## Quickstart

```bash
cd projects
uv sync --all-packages
uv run flowlist --help
```

Everything is local and offline: a SQLite database at `~/.flowlist/flowlist.db`
(override with `--db`), no accounts, no network calls on the default path.

```console
$ flowlist import party.csv --name party
imported party: 6 entries (e908bfb5-28ca-4f09-a32a-f467ad12a1ce)
  warning line 7: tempo 0 is outside 40-260 BPM; imported without a tempo
  warning line 7: key -1 means 'no key detected'; imported without a key
```

Real exports carry sentinels (`Key = -1`, `Tempo = 0`); flowlist maps them to
nulls with a per-row warning and imports the track anyway. Only a structurally
unparsable row is skipped, and then with its line number.

```console
$ flowlist show party
  0  Synthetic Sun - One More Hour  [124 BPM 8A e=0.71 -7.2 dB]
  1  Vera Lux - Night Drive  [126.5 BPM 9A e=0.76 -6.4 dB]
  2  Marta Quiet - Slow Burn  [86 BPM 9B e=0.42 -11.0 dB]
  3  Cyan Drift - Breakline  [174 BPM 7A e=0.88 -5.1 dB]
  4  Halcyon Bay - Paper Moon  [122 BPM 8B e=0.68 -7.9 dB]
  5  Ghost Signal - No Key Here  [no BPM no key e=0.55 -9.0 dB]
stored order: n=6 mean=0.576 min=0.417 total=2.879 seamless=1 cliffs=0

$ flowlist analyze party
5/6 tracks fully featured; 1 missing bpm; 1 missing key_pc
  spotify:6n3Ppam7vgaVa1iaRUc9Lp: missing bpm, key_pc

$ flowlist reorder party --seed 7
run d559b409-cc5f-46e6-9a66-24abe708e69b
before: n=6 mean=0.576 min=0.417 total=2.879 seamless=1 cliffs=0
after : n=6 mean=0.708 min=0.445 total=3.538 seamless=2 cliffs=0
delta: mean +0.132 min +0.027 seamless +1 cliffs +0 (seamless >= 0.70, cliff < 0.40)
```

Every reorder is an append-only run you can inspect afterwards:

```console
$ flowlist explain d559b409-cc5f-46e6-9a66-24abe708e69b
  0      Cyan Drift - Breakline
  0 -> 1   7A -> 9A (energy boost (+2)); tempo -27.3%; energy -0.12; loudness -1.3 dB; [key=0.55 bpm=0.00 energy=0.76 loudness=1.00]; total 0.445
  1      Vera Lux - Night Drive
  1 -> 2   9A -> 8A (adjacent fifth/fourth); tempo -2.0%; energy -0.05; loudness -0.8 dB; [key=0.85 bpm=1.00 energy=0.90 loudness=1.00]; total 0.927
  2      Synthetic Sun - One More Hour
  2 -> 3   8A -> 8B (relative major/minor); tempo -1.6%; energy -0.03; loudness -0.7 dB; [key=0.95 bpm=1.00 energy=0.94 loudness=1.00]; total 0.971
  3      Halcyon Bay - Paper Moon
  3 -> 4   key unknown; tempo n/a; energy -0.13; loudness -1.1 dB; [key=0.50 bpm=0.50 energy=0.74 loudness=1.00]; total 0.598  <no key on one side, no tempo on one side>
  4      Ghost Signal - No Key Here
  4 -> 5   key unknown; tempo n/a; energy -0.13; loudness -2.0 dB; [key=0.50 bpm=0.50 energy=0.74 loudness=1.00]; total 0.598  <no key on one side, no tempo on one side>
  5      Marta Quiet - Slow Burn

$ flowlist export d559b409-... --format m3u --out party.m3u8
wrote party.m3u8
```

Missing data degrades visibly, never silently: a component with no inputs
scores a neutral 0.5 and the transition carries a `missing_*` flag, so the
unknown-key track above is placed on its merits rather than banished to an end.

## CLI

All commands accept `--db PATH` (default `~/.flowlist/flowlist.db`) and
`--json` for machine-readable output, exit 0 on success and non-zero on
failure.

| Command | What it does |
|---|---|
| `flowlist import PATH --name NAME [--format csv\|json\|dir] [--replace] [--force]` | Import an Exportify-layout CSV, the documented JSON schema, or a folder of owned audio files (FR-1/FR-2) |
| `flowlist ls` | List stored playlists |
| `flowlist show PLAYLIST [--transitions]` | Tracks in stored order with resolved features; `--transitions` scores that order (FR-7) |
| `flowlist analyze PLAYLIST [--local] [--catalog PATH]` | Resolve features from providers in precedence order and report coverage (FR-3) |
| `flowlist features set TRACK [--bpm F] [--key 8A\|Am] [--energy F] [--loudness F] [--danceability F]` | Manual override; wins over every provider from then on (FR-4) |
| `flowlist reorder PLAYLIST [--seed N] [--profile neutral\|build\|cool] [--start-track T] [--end-track T] [--weights key=.35,bpm=.35,…] [--max-passes N] [--apply]` | Reorder for flow and persist a run (FR-8/9/10/11) |
| `flowlist runs [PLAYLIST]` | List stored runs |
| `flowlist explain RUN` | One line per transition: relation, deltas, component scores, flags (US-4) |
| `flowlist compare RUN` | The run's before/after scorecard plus its coverage snapshot |
| `flowlist apply RUN` | Rewrite the playlist's entry positions to the run's order (FR-12) |
| `flowlist export RUN --format m3u\|csv\|json [--out PATH]` | Export the ordering; prints to stdout without `--out` (FR-12) |
| `flowlist delete PLAYLIST [--force]` | Delete a playlist; refused while runs reference it unless `--force` |
| `flowlist serve [--host H] [--port N]` | Serve the REST API with uvicorn against the same database |

## API

`flowlist serve` (or `uvicorn flowlist.api:default_app --factory`) exposes:

| Method | Path | Returns |
|---|---|---|
| `GET` | `/health` | `{status, version}` |
| `POST` | `/playlists/import` | 201 — body `{name, format, content \| path, replace?, force?}` |
| `GET` | `/playlists` | playlist summaries |
| `GET` | `/playlists/{id}` | playlist with entries, resolved features and coverage |
| `DELETE` | `/playlists/{id}[?force=true]` | 204; 409 `playlist_has_runs` without `force` |
| `POST` | `/playlists/{id}/analyze` | coverage report — body `{providers?, catalog?, local?}` |
| `POST` | `/playlists/{id}/score` | flow report for the stored order |
| `POST` | `/playlists/{id}/reorder` | 201 run — body `{seed, weights?, profile?, start_entry?, end_entry?, max_passes?, apply?}` |
| `GET` | `/tracks/{track_id}` | track with per-source feature rows and the resolved view |
| `PUT` | `/tracks/{track_id}/features` | manual override |
| `GET` | `/runs?playlist_id=…` | run summaries |
| `GET` | `/runs/{run_id}` | run with entries and per-transition breakdowns |
| `POST` | `/runs/{run_id}/apply` | the playlist, reordered |
| `GET` | `/runs/{run_id}/export?format=m3u\|csv\|json` | file response |

Errors are structured (`{code, message, context}`) with stable codes:

| Code | Status | When |
|---|---|---|
| `import_failed` | 400 | the source could not be parsed at all |
| `unknown_playlist` / `unknown_track` / `unknown_run` | 404 | no such resource |
| `name_conflict` | 409 | import onto an existing name without `replace` |
| `playlist_has_runs` | 409 | destructive op blocked by run history (`force` overrides) |
| `playlist_too_large` | 413 | more than 500 entries (the optimizer's cap) |
| `invalid_weights` | 422 | negative, non-numeric, or all-zero weights |
| `invalid_anchor` | 422 | `start_entry`/`end_entry` is not an entry of this playlist |
| `invalid_providers` | 422 | unknown feature source in `providers` |
| `adapter_unavailable` | 503 | a live adapter's credentials or optional dependency are missing |

## How the score works

`score(a → b) ∈ [0,1]` is a weighted sum of four components (default
`key 0.35, bpm 0.35, energy 0.20, loudness 0.10`; a fifth, danceability
continuity, exists at weight 0). Weights must be ≥ 0 with at least one > 0 and
are renormalized to sum 1 before scoring, so scaling them all is a no-op and
the seamless/cliff constants stay meaningful.

- **key** — the Camelot wheel (`n_major(pc) = ((7·pc + 7) mod 12) + 1`), scored
  by named relation: same 1.00, relative 0.95, adjacent fifth 0.85, diagonal
  0.75, parallel 0.65, +2 energy boost 0.55, −2 drop 0.45, semitone lift 0.30,
  clash 0.10.
- **bpm** — log-tempo distance with octave folding, so 86 → 172 BPM is a
  beatmatch (×0.9 for the change of feel). ≤ 2% scores 1.0, degrades through
  the ±6% CDJ range, and hits 0 beyond 12%.
- **energy** — a clamped linear penalty; `build` multiplies drops by 1.5,
  `cool` mirrors it.
- **loudness** — a 2 dB dead zone (about the just-noticeable difference)
  ramping to 0 at 10 dB (roughly "twice as loud").

Ordering is multi-start greedy construction plus first-improvement Or-opt and
2-opt local search, deterministic given `(inputs, seed)`. Reordering is capped
at 500 entries; `exact_optimal` (fixed-endpoint Held-Karp) is available up to
n = 14 and is what the evals score against.

## Evals

```bash
cd projects
uv run python flowlist/evals/run.py     # scorecard, exit 0 iff every gate passes
uv run pytest flowlist/evals -q         # the same gates, enforced by pytest
```

Hermetic by construction: committed fixtures, offline adapters, seeded RNG,
literal timestamps. Current measured scores:

| Metric | Value | Gate | |
|---|---|---|---|
| M1 key_relation_accuracy | 1.0000 | ≥ 1.00 | PASS |
| M2 pair_ranking_auc | 0.9894 | ≥ 0.90 | PASS |
| M2b anti_gaming_margin | 0.2780 | ≥ 0.10 | PASS |
| M3 component_monotonicity | 1.0000 | ≥ 1.00 | PASS |
| M4 exact_optimality_mean | 1.0000 | ≥ 0.97 | PASS |
| M4 exact_optimality_min | 1.0000 | ≥ 0.90 | PASS |
| M5 planted_chain_recovery | 1.0000 | ≥ 0.92 | PASS |
| M5 planted_chain_recovery_min | 1.0312 | ≥ 0.85 | PASS |
| M6 baseline_margin | 0.1224 | ≥ 0.08 | PASS |
| M6 baseline_margin_min | 0.0731 | > 0 | PASS |
| M7 determinism | 1.0000 | ≥ 1.00 | PASS |

Naive baselines on the same messy playlists, computed live in the run:
`random 0.442`, `identity 0.422`, `bpm_sort 0.693` — the strongest naive
strategy — against the heuristic's `0.815`.

What makes the numbers mean something:

- **M1/M2 truth is hand-authored data.** `fixtures/key_relations.json` (79
  cases) is written from the published Camelot compatibility chart with a
  rationale per row; `fixtures/transition_pairs.json` (120 pairs) is labelled
  `seamless`/`workable`/`clash` from published DJ guidance, deliberately mixing
  components so no single one predicts the label. The bpm-only scorer reaches
  only 0.711 on the same fixture, and CI asserts it stays ≤ 0.80 so the M2 and
  M2b gates remain simultaneously satisfiable.
- **M4 truth is recomputed, not stored.** Held-Karp runs at eval time under the
  same anchors the heuristic receives, and the run hard-fails if any ratio
  exceeds 1 — a heuristic "beating" the exact optimum proves the DP is broken.
  The DP is independently pinned by
  `tests/test_optimizer.py::test_fr8_exact_matches_bruteforce` (brute-force
  permutation enumeration at n ≤ 7, including anchored variants).
- **The exact suite provably rejects construction-only greedy.** The generator
  runs a standalone all-starts best-next greedy (no imports from
  `src/flowlist`) over every instance and records its ratios; six of the ten
  instances defeat it, and it would score **M4_mean 0.9646 / M4_min 0.8625** —
  failing both gates. Passing M4 therefore requires the local search to work,
  and `evals/test_gates.py::test_fixture_invariants` re-asserts that property
  so a later regeneration cannot quietly drop it.

To regenerate fixtures (a reviewed change — the committed files *are* the
ground truth):

```bash
uv run python flowlist/evals/fixtures/generate.py --goldens
```

## Tests

```bash
uv run pytest flowlist/ -q              # 413 tests + the eval gates
uv run pytest flowlist/ --runslow       # adds the FR-8 n=500 performance smoke
uv run ruff check flowlist/
```

Every FR in SCOPE.md maps to at least one test or eval case, and test names
carry FR ids (`test_fr8_exact_matches_bruteforce`, `test_fr13_name_conflict…`);
the mapping table is EVALS.md §7.

## Live adapters

The default path is entirely offline. Two live adapters exist behind the same
Protocols and activate only when their credentials or optional dependency are
present; neither is exercised by tests or eval gates.

- **`SpotifyMetadataProvider`** — needs `SPOTIFY_CLIENT_ID` and
  `SPOTIFY_CLIENT_SECRET`. Client-credentials flow, batches of 100 ids against
  `GET /v1/audio-features`, degrading to "unknown" per track on API errors.
  Best-effort: Spotify deprecated that endpoint for new third-party apps in
  November 2024, which is exactly why the core loop is import/offline-first.
  The interface is provider-agnostic, so another backend can be slotted in
  without engine changes.
- **`LibrosaLocalAnalyzer`** (`flowlist analyze … --local`) — needs the
  optional `audio` extra (`uv pip install 'flowlist[audio]'`, not installed by
  default). BPM from `librosa.beat.beat_track`, key from a chroma histogram
  correlated against the 24 Krumhansl–Kessler profiles, energy from normalized
  RMS, loudness as dBFS RMS. librosa is imported lazily; without it the command
  prints what to install and falls back to the other providers.

Directory import reads file *tags* with `mutagen` when it is available
(`flowlist[tags]` — pure Python, it parses tags without decoding audio) and
falls back to the `Artist - Title` filename pattern otherwise. Neither extra is
installed by `uv sync --all-packages`, and no test or eval depends on either;
`tests/test_adapters.py::test_fr2_tag_reading_degrades_without_mutagen` pins the
fallback.

## Not in scope

No web UI, no audio playback or crossfade rendering, no beat-grid alignment, no
write-back to streaming services, no learned similarity, no global energy-curve
fitting, and no cross-source track merging (the same song imported once as
`meta:<hash>` and later as `spotify:<id>` stays two catalog rows). See
SCOPE.md's non-goals for the reasoning.
