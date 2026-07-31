# dresscast — DATA MODEL

All domain models are Pydantic v2 classes in `engine/models.py`; the store
maps them to SQLite (stdlib `sqlite3`). Times are ISO-8601 UTC strings
supplied by callers (the engine never reads the clock); calendar dates are
`YYYY-MM-DD` strings in the configured local timezone. Ids are UUID4 strings
unless noted.

## 1. Entity overview

```
Garment 1 ──< AttributeSuggestion
Garment 1 ──< OutfitItem            Garment 1 ──< WearLogItem
Garment 1 ──< LaundryEventItem

ForecastSnapshot 1 ──< ForecastHour (exactly 24)
ForecastSnapshot 1 ──< Recommendation 1 ──< RecommendationOutfit 1 ──< OutfitItem
RecommendationOutfit 1 ──< WearLog (nullable link)
WearLog 1 ──< WearLogItem
LaundryEvent 1 ──< LaundryEventItem
```

Config (location, defaults, occasion definitions) lives in a TOML file, not
the database (§5).

## 2. Entities

### 2.1 Garment

The wardrobe inventory. Mutable (attributes and state), never hard-deleted —
`retired` is the terminal state so history stays referentially intact.

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `name` | str, required | unique case-insensitive, for CLI addressing ("navy-wool-coat") |
| `category` | str, required | one of the SCOPE.md D1 preset table categories (app-validated enum) |
| `layer_role` | enum `base \| mid \| outer \| bottom \| leg_base \| full_body \| footwear \| accessory` | drives slot logic (HC-1); defaulted from category, overridable (a flannel shirt may be `base` or `mid`) |
| `clo` | float, required | [0.0, 1.5]; must be within ±0.15 of the category preset (FR-1); accessories fixed at 0.0 |
| `waterproofness` | int, required | 0–3 ordinal (SCOPE.md D7); default 0 |
| `windproofness` | int, required | 0–2 ordinal (SCOPE.md D4); default 0 |
| `formality` | int, required | 1–5 ladder (SCOPE.md D9) |
| `colors` | JSON list, required | 1–3 entries `{name: str, hue: float 0–360 \| null, neutral: bool, role: "main" \| "accent"}`; exactly one `main`; `hue` null iff `neutral` true |
| `style_tags` | JSON list of str | free vocabulary, e.g. `["preppy","outdoorsy"]`; may be empty |
| `occasions` | JSON list of str | non-empty for non-accessory garments; values from config vocabulary (default: casual, work, sport, outdoor, formal) |
| `wears_before_laundry` | int ≥ 1 | defaulted by category (SCOPE.md D11); 999 for footwear/accessories/outerwear-style "uncounted" |
| `wears_since_wash` | int ≥ 0 | default 0; incremented by wear logging |
| `status` | enum `clean \| dirty \| in_laundry \| retired` | see state machine below |
| `photo_path` | str \| None | path under `~/.dresscast/photos/` after attach |
| `photo_sha256` | str \| None | set iff `photo_path` set |
| `notes` | str \| None | |
| `created_at` / `updated_at` | datetime | supplied by caller |

**State machine (FR-4):** allowed transitions `clean→dirty` (auto at
threshold, or manual), `dirty→in_laundry` (optional explicit step),
`dirty→clean` and `in_laundry→clean` (laundry event; resets
`wears_since_wash` to 0), any→`retired` (terminal). Anything else raises
`invalid_transition`.

**Invariants:** `name` unique (NOCASE); `status='dirty'` whenever
`wears_since_wash ≥ wears_before_laundry` (enforced transactionally at
wear-log time; a manual `dirty` mark may hold at lower counts);
`(photo_path IS NULL) = (photo_sha256 IS NULL)`; colors shape validated at
the model layer (exactly one main color; hue present iff not neutral).
**Derived, never stored:** ensemble clo, feels-like, required clo,
eligibility for a request — all computed in the engine; recommendations
snapshot what they used (2.6), so garment edits never rewrite history.

### 2.2 AttributeSuggestion (append-only rows, one status transition)

Staged output of the `AttributeExtractor` adapter (FR-2). Provenance for
every machine-suggested attribute.

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `garment_id` | str, FK Garment | |
| `source` | enum `fixture \| vision` | adapter that produced it |
| `payload` | JSON | proposed fields + confidences, e.g. `{"category": {"value": "wool_coat", "confidence": 0.91}, "colors": {"value": [...], "confidence": 0.84}}`; only keys from the suggestible set (category, layer_role, colors, style_tags, formality) |
| `status` | enum `pending \| accepted \| rejected` | |
| `accepted_fields` | JSON list \| None | which payload keys were merged; set iff `accepted` |
| `created_at` / `resolved_at` | datetime / datetime \| None | `resolved_at` set iff status ≠ pending |

**Invariants:** rows are never updated except the single transition
`pending → accepted \| rejected` (sets `resolved_at`, and `accepted_fields`
on accept); accepting merges *only* `accepted_fields` into the garment in
the same transaction. Suggestions are never deleted.

### 2.3 ForecastSnapshot + ForecastHour (append-only)

One fetched day forecast. Multiple snapshots per date are allowed (refetches
append); a recommendation pins the exact snapshot it used.

**ForecastSnapshot**

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `date` | str `YYYY-MM-DD` | forecast target date (local) |
| `location_name` | str | from config, e.g. "home" |
| `lat` / `lon` | float | WGS-84 |
| `timezone` | str | IANA name, e.g. `America/New_York` |
| `provider` | enum `fixture \| open_meteo` | |
| `fetched_at` | datetime | supplied by caller |
| `raw` | JSON \| None | verbatim provider payload for audit (fixture: null) |

**ForecastHour**

| Field | Type | Notes |
|---|---|---|
| `snapshot_id` | str, FK | composite PK with `hour` |
| `hour` | int 0–23 | local hour |
| `temp_c` | float | [−60, 60] |
| `wind_kmh` | float ≥ 0 | ≤ 250 |
| `humidity_pct` | float | [0, 100] |
| `precip_prob` | float | [0, 1] |
| `precip_mmh` | float ≥ 0 | liquid-equivalent intensity |
| `uv_index` | float ≥ 0 | ≤ 16 |

**Invariants:** exactly 24 hour rows per snapshot (verified on write);
snapshots and hours are never mutated or deleted. **Derived, never
stored:** feels-like and required clo (config-dependent, SCOPE.md FR-6/D4 —
storing one number would be wrong for windproof configurations).

### 2.4 Recommendation (append-only)

One assembly run (SCOPE.md FR-9/FR-14).

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `date` | str `YYYY-MM-DD` | day being dressed |
| `snapshot_id` | str, FK ForecastSnapshot | |
| `created_at` | datetime | |
| `engine_version` | str | package version |
| `seed` | int | recorded for API stability (SCOPE.md D12) |
| `params` | JSON | full request: `{occasion, wear_window: [7, 22], met, k, weights, thresholds_version}` |
| `wardrobe_hash` | str | SHA-256 over the sorted serialized eligible-garment records at run time (reproducibility audit, FR-18) |
| `compromises` | JSON list | run-level relaxations applied (FR-15), e.g. `[{"rule": "HC-8", "detail": "variety relaxed: only 2 valid outfits"}]`; empty when none |

**Invariants:** append-only; `(params, seed, wardrobe_hash, snapshot_id,
engine_version)` suffice to reproduce the outfits byte-identically (FR-18);
deleting is not supported by the application.

### 2.5 RecommendationOutfit

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `recommendation_id` | str, FK | |
| `rank` | int ≥ 1 | unique per recommendation, contiguous from 1 |
| `score_total` | float [0,1] | |
| `scores` | JSON | components + weights used: `{"thermal": 0.94, "protection": 1.0, "color": 0.90, "style": 0.88, "variety": 0.97, "weights": {"thermal": 0.40, ...}}` |
| `hour_plan` | JSON list | one entry per wear-window hour (shape below) |
| `reasoning` | JSON list of str | deterministic template lines (FR-13) |
| `compromises` | JSON list | outfit-level relaxations; empty when none |

`hour_plan` entry shape (serialized `HourPlanEntry`):

```json
{
  "hour": 7, "temp_c": 6.0, "wind_kmh": 15.0, "humidity_pct": 70.0,
  "precip_prob": 0.1, "precip_mmh": 0.0,
  "effective_wind_kmh": 9.0, "feels_c": 4.1, "required_clo": 1.74,
  "config": ["base", "mid_1", "outer"], "ensemble_clo": 1.48,
  "deviation": -0.26, "in_band": false, "hour_score": 0.99,
  "rain_cover_on": false, "notes": ["slightly_cool"]
}
```

`config` values are slot names (2.6). The plan snapshots every computed
number (feels-like, required, ensemble clo), making runs self-contained even
if garments are later re-tagged. Note vocabulary: `slightly_cool`,
`slightly_warm`, `rain_cover_required`, `umbrella_in_use`, `shed_layer`,
`add_layer`.

**Invariants:** ranks contiguous from 1; `score_total` recomputable from
`scores` + weights (evals assert consistency); pairwise core-item Jaccard
between outfits of one recommendation ≤ 0.5 unless a compromise says
otherwise (FR-9 diversity).

### 2.6 OutfitItem

| Field | Type | Notes |
|---|---|---|
| `outfit_id` | str, FK RecommendationOutfit | composite PK with `slot` |
| `slot` | enum `base \| mid_1 \| mid_2 \| outer \| bottom \| leg_base \| footwear \| accessory_1..accessory_4` | |
| `garment_id` | str, FK Garment | unique per outfit |

**Invariants:** slot coverage per HC-1 (exactly one of `base`+`bottom` or a
`full_body` garment in `base` with no `bottom`; exactly one `footwear`;
`mid_2` present only if `mid_1` present, with `clo(mid_2) ≥ clo(mid_1)` —
mid_2 is the outermost mid per SCOPE.md D6); `(outfit_id, garment_id)`
unique; garment `layer_role` must be compatible with the slot
(`full_body` fills `base`; accessories only in accessory slots).

### 2.7 WearLog + WearLogItem

What was actually worn. Multiple logs per date allowed (gym + work).

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `date` | str `YYYY-MM-DD` | day worn |
| `source` | enum `recommendation \| manual` | |
| `outfit_id` | str \| None, FK RecommendationOutfit | set iff source=recommendation |
| `created_at` | datetime | |

**WearLogItem:** `(wear_log_id FK, garment_id FK)` composite PK.

**Invariants:** a wear log and its items are written in one transaction
together with the FR-4 side effects (each item's `wears_since_wash` += 1;
status flip to `dirty` when threshold reached). Items must be non-retired at
log time (dirty is allowed — the user wore it anyway; the log records
reality). Logs are append-only, with one sanctioned mutation: a mistaken log
may be deleted via `dresscast wear --undo LOG` on the same calendar day it
was created, and the deletion reverses the counter/status side effects in
the same transaction. After that day the log is immutable.

### 2.8 LaundryEvent + LaundryEventItem (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `created_at` | datetime | |
| `note` | str \| None | |

**LaundryEventItem:** `(event_id FK, garment_id FK)` composite PK.

**Invariants:** written transactionally with the garment resets
(`wears_since_wash` → 0, status → `clean`); only `dirty`/`in_laundry`
garments may appear in an event (`invalid_transition` otherwise);
append-only.

## 3. Interchange schemas

### 3.1 Wardrobe import/export JSON (FR-3)

```json
{
  "version": 1,
  "garments": [
    {
      "id": "0b6c1f2e-…",
      "name": "navy-wool-coat",
      "category": "wool_coat", "layer_role": "outer",
      "clo": 0.60, "waterproofness": 1, "windproofness": 1, "formality": 4,
      "colors": [{"name": "navy", "hue": null, "neutral": true, "role": "main"}],
      "style_tags": ["classic"], "occasions": ["work", "casual", "formal"],
      "wears_before_laundry": 30, "wears_since_wash": 4, "status": "clean",
      "photo_path": null, "notes": null
    }
  ]
}
```

`id` optional on import (generated when absent; matched by `id`, else unique
`name`, for idempotent re-import). Export emits the full field set.
`wears_since_wash`/`status` are included so a wardrobe move preserves
laundry state.

### 3.2 Fixture weather file (FR-5, one per scenario day)

```json
{
  "date": "2026-04-14", "location_name": "home",
  "lat": 40.71, "lon": -74.01, "timezone": "America/New_York",
  "hours": [
    {"hour": 0, "temp_c": 7.1, "wind_kmh": 9.0, "humidity_pct": 72,
     "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 0.0}
  ]
}
```

Exactly 24 `hours` entries; the fixture provider validates and serves this
verbatim as a `DayForecast`.

## 4. Storage mapping (SQLite)

Database default `~/.dresscast/dresscast.db`; photos under
`~/.dresscast/photos/<garment_id><ext>`; tests and evals use `:memory:` or a
temp dir. WAL mode; foreign keys ON; all multi-row effects transactional.
JSON columns are TEXT validated at the model layer.

```sql
CREATE TABLE garments (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL COLLATE NOCASE UNIQUE,
  category             TEXT NOT NULL,
  layer_role           TEXT NOT NULL CHECK (layer_role IN
                        ('base','mid','outer','bottom','leg_base','full_body','footwear','accessory')),
  clo                  REAL NOT NULL CHECK (clo BETWEEN 0.0 AND 1.5),
  waterproofness       INTEGER NOT NULL DEFAULT 0 CHECK (waterproofness BETWEEN 0 AND 3),
  windproofness        INTEGER NOT NULL DEFAULT 0 CHECK (windproofness BETWEEN 0 AND 2),
  formality            INTEGER NOT NULL CHECK (formality BETWEEN 1 AND 5),
  colors               TEXT NOT NULL,               -- JSON list
  style_tags           TEXT NOT NULL DEFAULT '[]',  -- JSON list
  occasions            TEXT NOT NULL,               -- JSON list
  wears_before_laundry INTEGER NOT NULL CHECK (wears_before_laundry >= 1),
  wears_since_wash     INTEGER NOT NULL DEFAULT 0 CHECK (wears_since_wash >= 0),
  status               TEXT NOT NULL DEFAULT 'clean' CHECK (status IN
                        ('clean','dirty','in_laundry','retired')),
  photo_path           TEXT,
  photo_sha256         TEXT,
  notes                TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  CHECK ((photo_path IS NULL) = (photo_sha256 IS NULL))
);

CREATE TABLE attribute_suggestions (
  id              TEXT PRIMARY KEY,
  garment_id      TEXT NOT NULL REFERENCES garments(id),
  source          TEXT NOT NULL CHECK (source IN ('fixture','vision')),
  payload         TEXT NOT NULL,                    -- JSON
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                    ('pending','accepted','rejected')),
  accepted_fields TEXT,                             -- JSON list, iff accepted
  created_at      TEXT NOT NULL,
  resolved_at     TEXT,
  CHECK ((status = 'pending') = (resolved_at IS NULL)),
  CHECK ((status = 'accepted') = (accepted_fields IS NOT NULL))
);

CREATE TABLE forecast_snapshots (
  id            TEXT PRIMARY KEY,
  date          TEXT NOT NULL,                      -- YYYY-MM-DD
  location_name TEXT NOT NULL,
  lat           REAL NOT NULL, lon REAL NOT NULL,
  timezone      TEXT NOT NULL,
  provider      TEXT NOT NULL CHECK (provider IN ('fixture','open_meteo')),
  fetched_at    TEXT NOT NULL,
  raw           TEXT                                -- JSON or NULL
);

CREATE TABLE forecast_hours (
  snapshot_id  TEXT NOT NULL REFERENCES forecast_snapshots(id),
  hour         INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
  temp_c       REAL NOT NULL CHECK (temp_c BETWEEN -60 AND 60),
  wind_kmh     REAL NOT NULL CHECK (wind_kmh BETWEEN 0 AND 250),
  humidity_pct REAL NOT NULL CHECK (humidity_pct BETWEEN 0 AND 100),
  precip_prob  REAL NOT NULL CHECK (precip_prob BETWEEN 0 AND 1),
  precip_mmh   REAL NOT NULL CHECK (precip_mmh >= 0),
  uv_index     REAL NOT NULL CHECK (uv_index BETWEEN 0 AND 16),
  PRIMARY KEY (snapshot_id, hour)
);

CREATE TABLE recommendations (
  id             TEXT PRIMARY KEY,
  date           TEXT NOT NULL,
  snapshot_id    TEXT NOT NULL REFERENCES forecast_snapshots(id),
  created_at     TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  seed           INTEGER NOT NULL,
  params         TEXT NOT NULL,                     -- JSON RequestParams
  wardrobe_hash  TEXT NOT NULL,
  compromises    TEXT NOT NULL DEFAULT '[]'         -- JSON list
);

CREATE TABLE recommendation_outfits (
  id                TEXT PRIMARY KEY,
  recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
  rank              INTEGER NOT NULL CHECK (rank >= 1),
  score_total       REAL NOT NULL CHECK (score_total BETWEEN 0 AND 1),
  scores            TEXT NOT NULL,                  -- JSON components + weights
  hour_plan         TEXT NOT NULL,                  -- JSON list of HourPlanEntry
  reasoning         TEXT NOT NULL,                  -- JSON list of strings
  compromises       TEXT NOT NULL DEFAULT '[]',
  UNIQUE (recommendation_id, rank)
);

CREATE TABLE outfit_items (
  outfit_id  TEXT NOT NULL REFERENCES recommendation_outfits(id),
  slot       TEXT NOT NULL CHECK (slot IN
               ('base','mid_1','mid_2','outer','bottom','leg_base','footwear',
                'accessory_1','accessory_2','accessory_3','accessory_4')),
  garment_id TEXT NOT NULL REFERENCES garments(id),
  PRIMARY KEY (outfit_id, slot),
  UNIQUE (outfit_id, garment_id)
);

CREATE TABLE wear_logs (
  id         TEXT PRIMARY KEY,
  date       TEXT NOT NULL,
  source     TEXT NOT NULL CHECK (source IN ('recommendation','manual')),
  outfit_id  TEXT REFERENCES recommendation_outfits(id),
  created_at TEXT NOT NULL,
  CHECK ((source = 'recommendation') = (outfit_id IS NOT NULL))
);

CREATE TABLE wear_log_items (
  wear_log_id TEXT NOT NULL REFERENCES wear_logs(id) ON DELETE CASCADE,
  garment_id  TEXT NOT NULL REFERENCES garments(id),
  PRIMARY KEY (wear_log_id, garment_id)
);

CREATE TABLE laundry_events (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  note       TEXT
);

CREATE TABLE laundry_event_items (
  event_id   TEXT NOT NULL REFERENCES laundry_events(id),
  garment_id TEXT NOT NULL REFERENCES garments(id),
  PRIMARY KEY (event_id, garment_id)
);

CREATE INDEX idx_garments_status     ON garments(status);
CREATE INDEX idx_hours_snapshot      ON forecast_hours(snapshot_id, hour);
CREATE INDEX idx_recs_date           ON recommendations(date, created_at);
CREATE INDEX idx_wear_date           ON wear_logs(date);
CREATE INDEX idx_wear_items_garment  ON wear_log_items(garment_id);
```

The 24-rows-per-snapshot invariant and JSON shapes are enforced at the
repository/model layer (SQLite CHECKs cannot count rows).

## 5. Config file (`~/.dresscast/config.toml`)

Not a database entity; documented here because the engine consumes it as
input. All keys have defaults; the file may be absent.

```toml
[location]                    # required for live weather only
name = "home"
lat = 40.71
lon = -74.01
timezone = "America/New_York"

[defaults]
met = 1.6                     # metabolic rate, SCOPE.md D3
wear_window = [7, 22]         # inclusive start hour, exclusive end hour
k = 3                         # outfits per recommendation
occasion_weekday = "work"     # default --occasion Mon-Fri
occasion_weekend = "casual"

[weather]
provider = "fixture"          # or "open_meteo" (requires DRESSCAST_LIVE_WEATHER=1)
fixture_dir = "~/.dresscast/weather"

[occasions.work]              # extends/overrides the built-in vocabulary
formality_band = [3, 4]
```

## 6. Example records

**Garment** (core loop example; see also import example §3.1)

```json
{"id": "6f2a…", "name": "gray-lambswool-sweater", "category": "sweater_thick",
 "layer_role": "mid", "clo": 0.36, "waterproofness": 0, "windproofness": 0,
 "formality": 3,
 "colors": [{"name": "gray", "hue": null, "neutral": true, "role": "main"}],
 "style_tags": ["classic"], "occasions": ["work", "casual"],
 "wears_before_laundry": 5, "wears_since_wash": 2, "status": "clean",
 "photo_path": "/home/user/.dresscast/photos/6f2a….jpg",
 "photo_sha256": "9c31…", "notes": null,
 "created_at": "2026-07-30T21:14:02Z", "updated_at": "2026-07-31T07:55:10Z"}
```

**AttributeSuggestion**

```json
{"id": "c8d0…", "garment_id": "6f2a…", "source": "fixture",
 "payload": {"category": {"value": "sweater_thick", "confidence": 0.88},
             "colors": {"value": [{"name": "gray", "hue": null,
                        "neutral": true, "role": "main"}], "confidence": 0.93},
             "formality": {"value": 3, "confidence": 0.61}},
 "status": "accepted", "accepted_fields": ["category", "colors"],
 "created_at": "2026-07-30T21:14:30Z", "resolved_at": "2026-07-30T21:15:02Z"}
```

**ForecastSnapshot + two ForecastHours** (spring swing day)

```json
{"id": "a1b2…", "date": "2026-04-14", "location_name": "home",
 "lat": 40.71, "lon": -74.01, "timezone": "America/New_York",
 "provider": "fixture", "fetched_at": "2026-04-14T06:30:00Z", "raw": null}
```
```json
{"snapshot_id": "a1b2…", "hour": 7, "temp_c": 6.0, "wind_kmh": 15.0,
 "humidity_pct": 70.0, "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 0.5}
```
```json
{"snapshot_id": "a1b2…", "hour": 15, "temp_c": 17.0, "wind_kmh": 10.0,
 "humidity_pct": 45.0, "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 4.2}
```

**Recommendation + top outfit** (abbreviated; numbers follow SCOPE.md
D3/D4 exactly: at hour 7, wool coat windproofness 1 → effective wind
9.0 km/h → wind chill 4.1 °C → required 1.74 clo; ensemble
shirt 0.25 + sweater 0.36 + coat 0.60 + jeans 0.24 + boots 0.10 + socks 0.03
→ Σ 1.58 → Icl = 0.835×1.58 + 0.161 = 1.48)

```json
{"id": "9e4f…", "date": "2026-04-14", "snapshot_id": "a1b2…",
 "created_at": "2026-04-14T06:31:00Z", "engine_version": "0.1.0", "seed": 7,
 "params": {"occasion": "work", "wear_window": [7, 22], "met": 1.6, "k": 3,
            "weights": {"thermal": 0.40, "protection": 0.15, "color": 0.15,
                        "style": 0.15, "variety": 0.15}},
 "wardrobe_hash": "5b09…", "compromises": []}
```
```json
{"id": "77aa…", "recommendation_id": "9e4f…", "rank": 1, "score_total": 0.955,
 "scores": {"thermal": 0.97, "protection": 1.0, "color": 1.0, "style": 0.88,
            "variety": 0.90,
            "weights": {"thermal": 0.40, "protection": 0.15, "color": 0.15,
                        "style": 0.15, "variety": 0.15}},
 "hour_plan": [
   {"hour": 7, "temp_c": 6.0, "wind_kmh": 15.0, "humidity_pct": 70.0,
    "precip_prob": 0.05, "precip_mmh": 0.0, "effective_wind_kmh": 9.0,
    "feels_c": 4.1, "required_clo": 1.74, "config": ["base","mid_1","outer"],
    "ensemble_clo": 1.48, "deviation": -0.26, "in_band": false,
    "hour_score": 0.99, "rain_cover_on": false, "notes": ["slightly_cool"]},
   {"hour": 15, "temp_c": 17.0, "wind_kmh": 10.0, "humidity_pct": 45.0,
    "precip_prob": 0.05, "precip_mmh": 0.0, "effective_wind_kmh": 10.0,
    "feels_c": 17.0, "required_clo": 0.69, "config": ["base"],
    "ensemble_clo": 0.68, "deviation": -0.01, "in_band": true,
    "hour_score": 1.0, "rain_cover_on": false, "notes": ["shed_layer"]}
 ],
 "reasoning": [
   "Cold start (feels like 4.1°C at 07:00) -> wear all three layers; the coat's wind resistance is doing real work against 15 km/h wind.",
   "Warms to 17°C by 15:00 -> shed coat and sweater around 11:00; the shirt alone sits within 0.01 clo of ideal.",
   "No rain expected (max probability 5%).",
   "All-neutral palette (white/gray/navy/denim) -> no clashes.",
   "Sweater last worn 3 days ago; everything else fresh this week."
 ],
 "compromises": []}
```

**OutfitItems** (for outfit 77aa…)

```json
[{"outfit_id": "77aa…", "slot": "base",     "garment_id": "11..white-oxford-shirt"},
 {"outfit_id": "77aa…", "slot": "mid_1",    "garment_id": "6f2a..gray-lambswool-sweater"},
 {"outfit_id": "77aa…", "slot": "outer",    "garment_id": "0b6c..navy-wool-coat"},
 {"outfit_id": "77aa…", "slot": "bottom",   "garment_id": "3d81..dark-jeans"},
 {"outfit_id": "77aa…", "slot": "footwear", "garment_id": "b2c4..brown-leather-boots"}]
```

**WearLog + items**

```json
{"id": "eeb1…", "date": "2026-04-14", "source": "recommendation",
 "outfit_id": "77aa…", "created_at": "2026-04-14T07:05:00Z"}
```
```json
[{"wear_log_id": "eeb1…", "garment_id": "11…"}, {"wear_log_id": "eeb1…", "garment_id": "6f2a…"},
 {"wear_log_id": "eeb1…", "garment_id": "0b6c…"}, {"wear_log_id": "eeb1…", "garment_id": "3d81…"},
 {"wear_log_id": "eeb1…", "garment_id": "b2c4…"}]
```

Side effect: `6f2a…` (sweater) moves to `wears_since_wash` 3 of 5; the shirt
(`wears_before_laundry` 2) reaches 2 of 2 and flips to `dirty`.

**LaundryEvent + items**

```json
{"id": "f00d…", "created_at": "2026-04-19T18:00:00Z", "note": "sunday wash"}
```
```json
[{"event_id": "f00d…", "garment_id": "11…"}, {"event_id": "f00d…", "garment_id": "6f2a…"}]
```
