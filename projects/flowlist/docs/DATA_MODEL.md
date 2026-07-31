# flowlist — DATA MODEL

All domain models are Pydantic v2 classes in `engine/models.py`; the store maps
them to SQLite (stdlib `sqlite3`). Times are ISO-8601 UTC strings supplied by
callers (the engine never reads the clock). Ids are UUID4 strings unless noted.

## 1. Entity overview

```
Track 1 ──< AudioFeatures            (one row per (track, source))
Track 1 ──< PlaylistEntry >── 1 Playlist
Playlist 1 ──< ReorderRun ──< RunEntry >── 1 PlaylistEntry
Playlist.applied_run_id ──> ReorderRun   (nullable)
```

## 2. Entities

### 2.1 Track

The catalog of known songs, deduplicated across playlists.

| Field | Type | Notes |
|---|---|---|
| `id` | str, PK | Stable identity (SCOPE.md D9): `spotify:<base62>` \| `file:<sha1-of-bytes>` \| `meta:<sha1(norm_artist\|norm_title)>` |
| `title` | str, required | As imported; display only, never used for matching after id creation |
| `artist` | str, required | May contain multiple names joined by `, ` |
| `album` | str \| None | |
| `duration_ms` | int \| None | > 0 when present |
| `spotify_id` | str \| None | base62 id, unique when present |
| `file_path` | str \| None | absolute path for locally-owned audio |
| `created_at` | datetime | supplied by caller at insert |

**Invariants:** `id` unique and immutable. Upsert on import: an existing id
keeps its row; non-null incoming fields fill nulls but never overwrite non-null
values (manual edits excepted, out of MVP scope). At least one of
`spotify_id`, `file_path`, or (`title`,`artist`) present — guaranteed by id
construction. Tracks are never deleted by playlist deletion (catalog is
append-mostly).

### 2.2 AudioFeatures

Musical facts about a track, one record per (track, source), so provenance is
never lost and precedence stays a pure read-time policy.

| Field | Type | Notes |
|---|---|---|
| `track_id` | str, FK Track | composite PK with `source` |
| `source` | enum `manual \| local_analysis \| streaming \| import \| fixture` | composite PK |
| `bpm` | float \| None | > 0, sane range 40–260 enforced by the store CHECK. Importers map sentinels and out-of-range source values (Exportify `Tempo = 0`, `Key = -1`) to None with per-row warnings *before* write (SCOPE.md FR-1), so the CHECK only trips on programmer error, never on real exports |
| `key_pc` | int \| None | pitch class 0–11 (C=0 … B=11), enharmonics collapsed |
| `mode` | int \| None | 1 = major, 0 = minor (Spotify convention). `key_pc` and `mode` are set/null together |
| `energy` | float \| None | [0,1] |
| `danceability` | float \| None | [0,1] |
| `loudness_db` | float \| None | [−60, 0]; integrated track loudness in dB |
| `valence` | float \| None | [0,1]; stored for display, unused in scoring |
| `confidence` | float | [0,1], default 1.0; analyzer-reported (e.g. key-detection correlation), display only in MVP |
| `analyzed_at` | datetime | supplied by caller |

**Invariants:** one row per (track_id, source); `manual` is the only source
whose row is updated in place (upsert per field, FR-4) — all others are
insert-or-replace-whole-record on re-analysis. **Derived, never stored:**
Camelot code (from `key_pc` + `mode` via SCOPE.md FR-5) — computed in the
engine so the two representations cannot drift.

**Resolution (FR-3):** effective features for a track = field-wise first
non-null walking sources in precedence order (default `manual >
local_analysis > streaming > import > fixture`). Resolution result is computed,
not stored; runs snapshot the resolved values they used (see 2.6).

### 2.3 Playlist

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `name` | str, required | unique (case-insensitive) for CLI addressing |
| `source` | enum `csv \| json \| directory \| manual` | how it was created |
| `source_ref` | str \| None | original file/directory path, for provenance |
| `applied_run_id` | str \| None, FK ReorderRun | last run applied to this playlist |
| `created_at` | datetime | |

**Invariants:** `name` unique; deleting a playlist cascades to its entries and
is refused if any ReorderRun references it (runs are the audit trail; forced
deletion removes runs too and is available as CLI `--force` and API
`DELETE /playlists/{id}?force=true` returning 409 `playlist_has_runs` without
it — the one sanctioned deletion path, SCOPE.md FR-13). Importing to an
existing name is refused with a hint unless `--replace` is passed (SCOPE.md
FR-1); `--replace` replaces entries in one transaction, and if runs exist it
additionally requires `--force`, which deletes those runs and clears
`applied_run_id` in the same transaction.

### 2.4 PlaylistEntry

An occurrence of a track at a position. This is the optimizer's node (SCOPE.md
D8): duplicate tracks are distinct entries.

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | stable across reorders — RunEntry references survive apply |
| `playlist_id` | str, FK Playlist | |
| `position` | int ≥ 0 | contiguous 0..n−1 |
| `track_id` | str, FK Track | duplicates allowed within a playlist |

**Invariants:** (`playlist_id`, `position`) unique; positions contiguous from 0
(enforced on every write: import creates the full entry set, apply rewrites the
full position map, each in one transaction). Entry `id` is immutable;
`position` is the only mutable field (via apply) — apply never deletes or
re-inserts entries, because `run_entries.entry_id` references them (RESTRICT)
and RunEntry rows must survive apply. Because (`playlist_id`, `position`) is
UNIQUE and SQLite checks the constraint immediately (not deferred), apply uses
a two-phase transactional update: first shift all positions into a disjoint
range (e.g. `position + n`), then write the final 0..n−1 positions — both
phases inside the same transaction, so no transient UNIQUE violation and no
observable intermediate state.

### 2.5 ReorderRun (append-only)

One invocation of the optimizer against a playlist snapshot.

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `playlist_id` | str, FK Playlist | |
| `created_at` | datetime | |
| `engine_version` | str | package version, for reproducibility audits |
| `algorithm` | enum `greedy_2opt \| ortools` | |
| `seed` | int | |
| `params` | JSON | `TransitionWeights`, `profile`, `start_entry`, `end_entry`, `max_passes` — the full `ReorderParams` dump. Weights are stored *normalized* (post FR-6 validation), i.e. exactly what the engine used, so runs are self-contained |
| `coverage` | JSON | per-field resolved/missing counts at run time (FR-3 snapshot) |
| `score_mean_before` / `after` | float | mean transition score of original vs proposed order |
| `score_min_before` / `after` | float | |
| `score_total_before` / `after` | float | |
| `seamless_before` / `after` | int | transitions ≥ 0.70 |
| `cliff_before` / `after` | int | transitions < 0.40 |

**Invariants:** append-only — never updated or deleted except via playlist
`--force` delete; `after` metrics are recomputed-verifiable from RunEntry
breakdowns (evals assert consistency); `seed`, `params`, `engine_version`
suffice to reproduce `RunEntry` orderings exactly (FR-15).

### 2.6 RunEntry

The proposed ordering plus the stored explanation.

| Field | Type | Notes |
|---|---|---|
| `run_id` | str, FK ReorderRun | composite PK with `position` |
| `position` | int ≥ 0 | 0..n−1 in the *proposed* order |
| `entry_id` | str, FK PlaylistEntry | unique per run |
| `transition` | JSON \| None | breakdown of the transition *into* this entry; None at position 0 |

`transition` JSON shape (serialized `Transition` model):

```json
{
  "score": 0.86,
  "components": {"key": 0.85, "bpm": 0.95, "energy": 0.9, "loudness": 1.0, "danceability": null},
  "weights": {"key": 0.35, "bpm": 0.35, "energy": 0.2, "loudness": 0.1, "danceability": 0.0},
  "key_relation": "adjacent_fifth",
  "camelot_from": "8A", "camelot_to": "9A",
  "bpm_from": 124.0, "bpm_to": 126.5, "bpm_delta_pct": 2.02, "bpm_folded": false,
  "energy_delta": 0.05, "loudness_delta_db": 0.8,
  "features_from": {"bpm": 124.0, "key_pc": 9, "mode": 0, "energy": 0.71, "loudness_db": -7.2},
  "features_to":   {"bpm": 126.5, "key_pc": 4, "mode": 0, "energy": 0.76, "loudness_db": -6.4},
  "flags": []
}
```

`features_from/to` snapshot the *resolved* values used, making runs
self-contained even if features are later re-analyzed or overridden. Flag
vocabulary: `missing_key`, `missing_bpm`, `missing_energy`, `missing_loudness`,
`half_time`, `cliff`, `anchored`.

**Invariants:** (`run_id`, `position`) PK, positions contiguous from 0;
(`run_id`, `entry_id`) unique; the set of `entry_id`s equals the playlist's
entry set at run time (a permutation, verified at write); `transition` is null
iff `position = 0`.

## 3. Interchange schemas

### 3.1 Playlist JSON import (FR-1)

```json
{
  "name": "party",
  "tracks": [
    {
      "title": "One More Hour", "artist": "Synthetic Sun",
      "spotify_id": "3n3Ppam7vgaVa1iaRUc9Lp",
      "duration_ms": 214000,
      "features": {"bpm": 124.0, "key_pc": 9, "mode": 0, "energy": 0.71,
                    "danceability": 0.8, "loudness_db": -7.2}
    },
    {"title": "Night Drive", "artist": "Vera Lux", "file_path": "/music/night_drive.flac"}
  ]
}
```

`features` optional per track → stored as `source=import`. CSV import (FR-1)
maps the Exportify columns onto the same shape (`Tempo`→bpm, `Key`→key_pc,
`Mode`→mode, `Loudness`→loudness_db, etc.) with sentinel mapping (SCOPE.md
FR-1): `Key = -1` ("no key detected") → key_pc null with mode nulled alongside
it (set-together invariant, §2.2); `Tempo = 0` or outside 40–260 → bpm null.
Each sentinel mapping emits a per-row warning; the row is still imported.
Structurally unparsable rows — and only those — are skipped with line numbers.

### 3.2 Export formats (FR-12)

- **M3U8:** `#EXTM3U` header; per entry `#EXTINF:<seconds>,<Artist> - <Title>`
  then `file_path` if known else a comment line `# no local file: <artist> - <title>`.
- **CSV:** import columns + `Position`, `Transition Score`, `Key Relation`.
- **JSON:** full `ReorderRun` + `RunEntry` dump (the `GET /runs/{id}` payload).

## 4. Storage mapping (SQLite)

Database default `~/.flowlist/flowlist.db`; tests and evals use `:memory:` or a
temp file. WAL mode; foreign keys ON; all writes transactional.

```sql
CREATE TABLE tracks (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  artist      TEXT NOT NULL,
  album       TEXT,
  duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms > 0),
  spotify_id  TEXT UNIQUE,
  file_path   TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE audio_features (
  track_id     TEXT NOT NULL REFERENCES tracks(id),
  source       TEXT NOT NULL CHECK (source IN
                 ('manual','local_analysis','streaming','import','fixture')),
  bpm          REAL CHECK (bpm IS NULL OR (bpm >= 40 AND bpm <= 260)),
  key_pc       INTEGER CHECK (key_pc IS NULL OR (key_pc BETWEEN 0 AND 11)),
  mode         INTEGER CHECK (mode IS NULL OR mode IN (0,1)),
  energy       REAL CHECK (energy IS NULL OR (energy BETWEEN 0 AND 1)),
  danceability REAL CHECK (danceability IS NULL OR (danceability BETWEEN 0 AND 1)),
  loudness_db  REAL CHECK (loudness_db IS NULL OR (loudness_db BETWEEN -60 AND 0)),
  valence      REAL,
  confidence   REAL NOT NULL DEFAULT 1.0,
  analyzed_at  TEXT NOT NULL,
  PRIMARY KEY (track_id, source),
  CHECK ((key_pc IS NULL) = (mode IS NULL))
);

CREATE TABLE playlists (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL COLLATE NOCASE UNIQUE,
  source         TEXT NOT NULL CHECK (source IN ('csv','json','directory','manual')),
  source_ref     TEXT,
  applied_run_id TEXT REFERENCES reorder_runs(id),
  created_at     TEXT NOT NULL
);

CREATE TABLE playlist_entries (
  id          TEXT PRIMARY KEY,
  playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL CHECK (position >= 0),
  track_id    TEXT NOT NULL REFERENCES tracks(id),
  UNIQUE (playlist_id, position)
);

CREATE TABLE reorder_runs (
  id                 TEXT PRIMARY KEY,
  playlist_id        TEXT NOT NULL REFERENCES playlists(id),
  created_at         TEXT NOT NULL,
  engine_version     TEXT NOT NULL,
  algorithm          TEXT NOT NULL CHECK (algorithm IN ('greedy_2opt','ortools')),
  seed               INTEGER NOT NULL,
  params             TEXT NOT NULL,          -- JSON ReorderParams
  coverage           TEXT NOT NULL,          -- JSON CoverageReport
  score_mean_before  REAL NOT NULL, score_mean_after  REAL NOT NULL,
  score_min_before   REAL NOT NULL, score_min_after   REAL NOT NULL,
  score_total_before REAL NOT NULL, score_total_after REAL NOT NULL,
  seamless_before    INTEGER NOT NULL, seamless_after INTEGER NOT NULL,
  cliff_before       INTEGER NOT NULL, cliff_after    INTEGER NOT NULL
);

CREATE TABLE run_entries (
  run_id     TEXT NOT NULL REFERENCES reorder_runs(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL CHECK (position >= 0),
  entry_id   TEXT NOT NULL REFERENCES playlist_entries(id),
  transition TEXT,                            -- JSON Transition, NULL iff position=0
  PRIMARY KEY (run_id, position),
  UNIQUE (run_id, entry_id)
);

CREATE INDEX idx_entries_playlist ON playlist_entries(playlist_id, position);
CREATE INDEX idx_runs_playlist    ON reorder_runs(playlist_id, created_at);
```

Note: `playlist_entries` has no FK from `run_entries` cascade-protection
issue — deleting a playlist with `--force` deletes runs first, then entries,
in one transaction (2.3 invariant).

## 5. Example records

**Track**

```json
{"id": "meta:0f8c2a…", "title": "Night Drive", "artist": "Vera Lux",
 "album": null, "duration_ms": 231000, "spotify_id": null,
 "file_path": "/music/night_drive.flac", "created_at": "2026-07-31T18:02:11Z"}
```

**AudioFeatures** (two sources for the same track; `local_analysis` wins over
`import` at read time)

```json
{"track_id": "meta:0f8c2a…", "source": "import", "bpm": 122.0, "key_pc": 4,
 "mode": 0, "energy": 0.64, "danceability": 0.71, "loudness_db": -8.1,
 "valence": 0.4, "confidence": 1.0, "analyzed_at": "2026-07-31T18:02:11Z"}
```
```json
{"track_id": "meta:0f8c2a…", "source": "local_analysis", "bpm": 123.9,
 "key_pc": 4, "mode": 0, "energy": 0.61, "danceability": null,
 "loudness_db": -8.9, "valence": null, "confidence": 0.83,
 "analyzed_at": "2026-07-31T18:10:40Z"}
```

**Playlist / PlaylistEntry**

```json
{"id": "b4e7…", "name": "party", "source": "csv", "source_ref": "/exports/party.csv",
 "applied_run_id": "9d1c…", "created_at": "2026-07-31T18:02:11Z"}
```
```json
{"id": "e991…", "playlist_id": "b4e7…", "position": 0, "track_id": "spotify:3n3Ppam…"}
```

**ReorderRun** (abbreviated)

```json
{"id": "9d1c…", "playlist_id": "b4e7…", "created_at": "2026-07-31T18:15:00Z",
 "engine_version": "0.1.0", "algorithm": "greedy_2opt", "seed": 7,
 "params": {"weights": {"key": 0.35, "bpm": 0.35, "energy": 0.2, "loudness": 0.1,
             "danceability": 0.0}, "profile": "build", "start_entry": "e991…",
             "end_entry": null, "max_passes": 50},
 "coverage": {"tracks": 44, "full": 41, "missing": {"key_pc": 3}},
 "score_mean_before": 0.47, "score_mean_after": 0.81,
 "score_min_before": 0.11, "score_min_after": 0.38,
 "score_total_before": 20.2, "score_total_after": 34.8,
 "seamless_before": 9, "seamless_after": 33, "cliff_before": 14, "cliff_after": 1}
```

**RunEntry** — see the `transition` JSON example in 2.6.
