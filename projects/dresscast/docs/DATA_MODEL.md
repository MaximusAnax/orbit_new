# dresscast — DATA MODEL

> Revision 2 (post-review); audit trail in `REVIEW.md`. Notable changes:
> `overridden_fields` on Garment (FR-2 cascade), `layer_role` snapshotted on
> WearLogItem, DST-safe forecast hours keyed by `seq`, a `schema_version`
> table, classified reasoning entries, explicit float quantization, an exact
> accessory-slot ordering rule, and corrected worked examples (socks removed).

All domain models are Pydantic v2 classes in `engine/models.py`; the store
maps them to SQLite (stdlib `sqlite3`). Times are ISO-8601 UTC strings
supplied by callers (the engine never reads the clock); calendar dates are
`YYYY-MM-DD` strings in the configured local timezone. Ids are UUID4 strings
unless noted.

**Float quantization (FR-19).** Every float that crosses a persistence or
serialization boundary is rounded *inside the engine, before ranking*:
score components and `score_total` to `SCORE_DP = 6` decimals, physical
quantities in `hour_plan` / `DayBrief` to `PLAN_DP = 3`. Ranking, tie-breaks
and equality checks read the rounded values, so a last-ULP difference in
`v**0.16` or `exp()` between platforms cannot reorder outfits or change a
stored byte.

## 1. Entity overview

```
Garment 1 ──< AttributeSuggestion
Garment 1 ──< OutfitItem            Garment 1 ──< WearLogItem
Garment 1 ──< LaundryEventItem

ForecastSnapshot 1 ──< ForecastHour (23–25, DST-safe)
ForecastSnapshot 1 ──< Recommendation 1 ──< RecommendationOutfit 1 ──< OutfitItem
RecommendationOutfit 1 ──< WearLog (nullable link)
WearLog 1 ──< WearLogItem
LaundryEvent 1 ──< LaundryEventItem
```

`DayBrief` (FR-16) is a computed value object, never persisted: it is a pure
function of a ForecastSnapshot plus request params, so storing it would
duplicate derivable state. It is returned inline by `GET /brief` and embedded
in the `infeasible_wardrobe` error payload.

Config (location, defaults, occasion vocabulary) lives in a TOML file, not
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
| `accessory_class` | enum `hat \| gloves \| scarf \| umbrella \| sunglasses` \| None | required iff `layer_role = accessory`; drives FR-9 attachment |
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
| `overridden_fields` | JSON list of str | fields the user set explicitly rather than taking the category preset; FR-2's cascade never rewrites these. Subset of `{clo, layer_role, formality, wears_before_laundry}` |
| `photo_path` | str \| None | path under `~/.dresscast/photos/` after attach |
| `photo_sha256` | str \| None | set iff `photo_path` set |
| `notes` | str \| None | free text; never rendered into reasoning (FR-15) |
| `created_at` / `updated_at` | datetime | supplied by caller |

**State machine (FR-3):** allowed transitions `clean→dirty` (auto at
threshold, or manual), `dirty→in_laundry` (optional explicit step),
`dirty→clean` and `in_laundry→clean` (laundry event; resets
`wears_since_wash` to 0), any→`retired` (terminal). Anything else raises
`invalid_transition`.

**Invariants:** `name` unique (NOCASE); `status='dirty'` whenever
`wears_since_wash ≥ wears_before_laundry` (enforced transactionally at
wear-log time; a manual `dirty` mark may hold at lower counts);
`(photo_path IS NULL) = (photo_sha256 IS NULL)`; `(accessory_class IS NULL)
= (layer_role <> 'accessory')`; colors shape validated at the model layer.
**Derived, never stored:** ensemble clo, feels-like, required clo, achievable
band, eligibility for a request — all computed in the engine; recommendations
snapshot what they used (§2.5/§2.6), so garment edits never rewrite history.

### 2.2 AttributeSuggestion (append-only rows, one status transition)

Staged output of the `AttributeExtractor` adapter (FR-2). Provenance for
every machine-suggested attribute, **including the values FR-2's category
cascade derives**.

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `garment_id` | str, FK Garment | |
| `source` | enum `fixture \| vision` | adapter that produced it |
| `payload` | JSON | proposed fields + confidences, e.g. `{"category": {"value": "wool_coat", "confidence": 0.91}}`; only keys from the suggestible set (category, layer_role, colors, style_tags, formality) |
| `status` | enum `pending \| accepted \| rejected` | |
| `accepted_fields` | JSON list \| None | set iff `accepted`; entries are `{"field": str, "via": "explicit" \| "cascade", "old": any, "new": any}` |
| `created_at` / `resolved_at` | datetime / datetime \| None | `resolved_at` set iff status ≠ pending |

**Invariants:** rows are never updated except the single transition
`pending → accepted \| rejected`. Accepting applies FR-2's three-step merge
(explicit keys → category cascade skipping `overridden_fields` → full
re-validation) in one transaction; if re-validation fails the transaction
rolls back and the row stays `pending`. Suggestions are never deleted.

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
| `snapshot_id` | str, FK | composite PK with `seq` |
| `seq` | int ≥ 0 | position in the local day, contiguous from 0; **the ordering key everywhere in the engine** |
| `hour` | int 0–23 | local wall-clock hour; not unique within a snapshot (an autumn DST fold repeats one hour) |
| `temp_c` | float | [−60, 60] |
| `wind_kmh` | float ≥ 0 | ≤ 250 |
| `humidity_pct` | float | [0, 100] |
| `precip_prob` | float | [0, 1] |
| `precip_mmh` | float ≥ 0 | liquid-equivalent intensity |
| `uv_index` | float ≥ 0 | ≤ 16 |

**Invariants (FR-4):** 23–25 hour rows per snapshot, `seq` contiguous from 0
(a normal local day has 24; DST transitions produce 23 or 25 and are
accepted); snapshots and hours are never mutated or deleted. Wear-window
membership is tested on `hour`; a folded hour appearing twice is simply two
wear-window hours, which is correct — the user does live through both.
**Derived, never stored:** `bare_feels_c`, `feels_c`, `required_clo`,
`target_clo` (SCOPE.md FR-5/FR-6 — storing one number would be wrong for
windproof configurations).

### 2.4 Recommendation (append-only)

One assembly run (SCOPE.md FR-8/FR-13).

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `date` | str `YYYY-MM-DD` | day being dressed |
| `snapshot_id` | str, FK ForecastSnapshot | |
| `created_at` | datetime | |
| `engine_version` | str | package version |
| `seed` | int | accepted and persisted for forward compatibility; no effect this pass (FR-19) |
| `params` | JSON | full request: `{occasion, wear_window: [7, 22], commute_hours: [7,8,9,17,18,19], met, k, weights, thresholds_version}` |
| `wardrobe_hash` | str | see below |
| `notes` | JSON list | run-level non-relaxation notes, e.g. `[{"kind": "partial_k", "n": 2, "reason": "only 2 outfits satisfy HC-1…HC-8"}]`; empty when none |
| `compromises` | JSON list | run-level relaxations actually applied (FR-14), e.g. `[{"rule": "HC-8", "detail": "no HC-valid outfit existed; yesterday's set re-admitted"}]`; empty when none |

`params.weights` records the D13 constants in force at run time (they are not
request-settable). `params.thresholds_version` is a short string pinning the
D5/D7/D10 constant set (band ±0.25, PoP 0.5/0.3, intensity 2.5/10, umbrella
wind 35, variety half-life 3, hysteresis 0.10, max changes 3); it is bumped
whenever any of those constants changes, so an old recommendation is legible
against the rules that produced it.

`wardrobe_hash` (FR-19) = SHA-256 of
`json.dumps(records, sort_keys=True, separators=(',',':'))` where `records`
is **every non-retired garment** (not the request-filtered subset — the hash
is request-independent), each reduced to exactly these fields with floats
rounded to `PLAN_DP`: `id, name, category, layer_role, accessory_class, clo,
waterproofness, windproofness, formality, colors, style_tags, occasions,
wears_before_laundry, wears_since_wash, status`. Timestamps, photo fields and
`notes` are excluded so the hash tracks recommendation-relevant state only.

**Invariants:** append-only; `(params, seed, wardrobe_hash, snapshot_id,
engine_version)` plus the wear history suffice to reproduce the outfits
byte-identically (FR-19); a run has `compromises` non-empty **only if** the
FR-14 ladder fired, which requires that zero outfits satisfied HC-1…HC-8.
Returning fewer than k outfits is recorded in `notes`, never in
`compromises`. Deleting is not supported by the application.

### 2.5 RecommendationOutfit

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `recommendation_id` | str, FK | |
| `rank` | int ≥ 1 | unique per recommendation, contiguous from 1 |
| `score_total` | float [0,1] | rounded to `SCORE_DP` |
| `scores` | JSON | `{"thermal": …, "protection": …, "color": …, "style": …, "variety": …, "weights": {…}}`, each rounded to `SCORE_DP`. `thermal` is exactly SCOPE.md FR-6.4's `S_thermal`; `protection` is exactly FR-9's `S_protect`. These are the values the engine ranked on — not a separate reporting computation |
| `hour_plan` | JSON list | one entry per wear-window hour (shape below) |
| `reasoning` | JSON list | classified entries `{"class": <FR-15 class>, "text": str}`, in FR-15's table order |
| `accessories` | JSON list | `[{"garment_id": …, "class": …, "trigger": …}]` for FR-9 attachments; empty when none triggered |
| `notes` | JSON list | outfit-level notes (`partial_k` context, `advisory_gap`) |
| `compromises` | JSON list | outfit-level relaxations; empty when none |

`hour_plan` entry shape (serialized `HourPlanEntry`):

```json
{
  "seq": 7, "hour": 7,
  "temp_c": 6.0, "wind_kmh": 15.0, "humidity_pct": 70.0,
  "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 0.5,
  "bare_feels_c": 2.982,
  "effective_wind_kmh": 9.0, "feels_c": 4.071,
  "required_clo": 1.742, "target_clo": 1.742, "clamped": null,
  "worn_slots": ["base", "mid_1", "outer", "bottom", "footwear"],
  "carried_slots": [],
  "ensemble_clo": 1.455,
  "deviation": -0.287, "in_band": false, "hour_score": 0.951,
  "exposure_weight": 3.0,
  "rain_cover_on": false, "protect_score": 1.0,
  "notes": ["slightly_cool"]
}
```

Field semantics that were ambiguous in revision 1 and are now pinned:

- **`worn_slots` lists every slot actually worn that hour**, including the
  always-worn `bottom` / `leg_base` / `footwear`. (Revision 1's `config`
  field listed only removable upper layers while claiming to list slots, so
  the worn set could not be reconstructed from the plan. The field is renamed
  and its contents completed.) `carried_slots` lists slots belonging to the
  outfit that are *off* this hour and must therefore be carried.
- **`bare_feels_c` vs `feels_c`** — the former is FR-5's
  configuration-independent value (windproofness 0) used by HC-7, the
  advisories and the brief; the latter is this hour's chosen configuration's
  value, used for `required_clo`.
- **`target_clo` vs `required_clo`** — `required_clo` is the physical demand;
  `target_clo` is FR-6.2's achievable-band clamp of it, and is what
  `deviation` and `hour_score` are measured against. `clamped` is `null`,
  `"wardrobe_floor"` or `"wardrobe_ceiling"`; when non-null,
  `required_clo − target_clo` is the signed shortfall FR-15 reports.

Note vocabulary: `slightly_cool`, `slightly_warm`, `wardrobe_floor`,
`wardrobe_ceiling`, `rain_cover_required`, `umbrella_in_use`, `shed_layer`,
`add_layer`, `carrying`, `wind_exposed`.

**Invariants:** ranks contiguous from 1 and `score_total` non-increasing in
rank; `score_total` equals the weighted recomputation from `scores` to
1e-6 (evals assert this *and* independently recompute the components);
pairwise core-item Jaccard between outfits of one recommendation ≤ 0.5
unless a compromise says otherwise (FR-8 diversity); the set of `reasoning`
classes equals the set of FR-15 triggers that fired (gated by EVALS.md M4).

### 2.6 OutfitItem

| Field | Type | Notes |
|---|---|---|
| `outfit_id` | str, FK RecommendationOutfit | composite PK with `slot` |
| `slot` | enum `base \| mid_1 \| mid_2 \| outer \| bottom \| leg_base \| footwear \| accessory_1..accessory_4` | |
| `garment_id` | str, FK Garment | unique per outfit |

**Invariants:** slot coverage per HC-1 (exactly one of `base`+`bottom` or a
`full_body` garment in `base` with no `bottom`; exactly one `footwear`;
`mid_2` present only if `mid_1` present, with `clo(mid_2) ≥ clo(mid_1)` and
`garment_id(mid_2) > garment_id(mid_1)` when the clo values are equal —
`mid_2` is the outermost mid per SCOPE.md D6 and its LIFO shed order must be
total); `(outfit_id, garment_id)` unique; garment `layer_role` must be
compatible with the slot (`full_body` fills `base`; accessories only in
accessory slots). **Accessory slots are filled in ascending `garment_id`
order** — with attachment itself deterministic (FR-9), this closes the last
gap in FR-19's byte-identity guarantee. There is no socks slot (SCOPE.md D1:
footwear presets include hosiery).

### 2.7 WearLog + WearLogItem

What was actually worn. Multiple logs per date allowed (gym + work).

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `date` | str `YYYY-MM-DD` | day worn |
| `source` | enum `recommendation \| manual` | |
| `outfit_id` | str \| None, FK RecommendationOutfit | set iff source=recommendation |
| `created_at` | datetime | |

**WearLogItem:** `(wear_log_id FK, garment_id FK)` composite PK, plus
`layer_role` (str, NOT NULL) — the garment's role **at log time**. HC-8 and
FR-11 derive "core items" from this snapshot, never from the garment's
current `layer_role`, so re-tagging a flannel shirt from `base` to `mid`
cannot silently rewrite what yesterday's outfit was.

**Invariants:** a wear log and its items are written in one transaction
together with the FR-3 side effects (each item's `wears_since_wash` += 1;
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

*(Revision 2: the wardrobe import/export schema was removed together with
revision 1's FR-3 "Wardrobe import/export" — see SCOPE.md §Non-goals. The
only interchange format that remains is the weather fixture, which the
offline adapter must read. FR numbers were compacted in revision 2; all
references in these three documents use the new numbering.)*

### 3.1 Fixture weather file (FR-4, one per scenario day)

```json
{
  "date": "2026-04-14", "location_name": "home",
  "lat": 40.71, "lon": -74.01, "timezone": "America/New_York",
  "hours": [
    {"seq": 0, "hour": 0, "temp_c": 7.1, "wind_kmh": 9.0, "humidity_pct": 72,
     "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 0.0}
  ]
}
```

23–25 `hours` entries with contiguous `seq` from 0; the fixture provider
validates and serves this verbatim as a `DayForecast`. One committed fixture
exercises a 25-hour autumn DST day (repeated `hour`) and one a 23-hour spring
day, so FR-4's relaxed invariant is tested rather than assumed.

## 4. Storage mapping (SQLite)

Database default `~/.dresscast/dresscast.db`; photos under
`~/.dresscast/photos/<garment_id><ext>`; tests and evals use `:memory:` or a
temp dir. WAL mode; foreign keys ON; all multi-row effects transactional.
JSON columns are TEXT validated at the model layer.

**Schema versioning.** The database is append-only by design and accumulates
irreplaceable wear history, so it carries an explicit version from day one.
`schema_version` holds a single row. On open, the store compares it to the
package's `SCHEMA_VERSION`: equal → proceed; lower → apply the ordered
migration functions in `store/migrations.py` inside one transaction, each
appending a row to `schema_migrations`; higher → refuse to open with
`invalid_schema_version` rather than risk writing a newer database with older
code. Migrations may add tables/columns and backfill; they may never drop or
rewrite `wear_logs`, `wear_log_items`, `recommendations` or their children.

```sql
CREATE TABLE schema_version (
  version    INTEGER NOT NULL,
  CHECK (version >= 1)
);
CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE garments (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL COLLATE NOCASE UNIQUE,
  category             TEXT NOT NULL,
  layer_role           TEXT NOT NULL CHECK (layer_role IN
                        ('base','mid','outer','bottom','leg_base','full_body','footwear','accessory')),
  accessory_class      TEXT CHECK (accessory_class IN
                        ('hat','gloves','scarf','umbrella','sunglasses')),
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
  overridden_fields    TEXT NOT NULL DEFAULT '[]',  -- JSON list
  photo_path           TEXT,
  photo_sha256         TEXT,
  notes                TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  CHECK ((photo_path IS NULL) = (photo_sha256 IS NULL)),
  CHECK ((accessory_class IS NULL) = (layer_role <> 'accessory'))
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
  seq          INTEGER NOT NULL CHECK (seq BETWEEN 0 AND 24),
  hour         INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
  temp_c       REAL NOT NULL CHECK (temp_c BETWEEN -60 AND 60),
  wind_kmh     REAL NOT NULL CHECK (wind_kmh BETWEEN 0 AND 250),
  humidity_pct REAL NOT NULL CHECK (humidity_pct BETWEEN 0 AND 100),
  precip_prob  REAL NOT NULL CHECK (precip_prob BETWEEN 0 AND 1),
  precip_mmh   REAL NOT NULL CHECK (precip_mmh >= 0),
  uv_index     REAL NOT NULL CHECK (uv_index BETWEEN 0 AND 16),
  PRIMARY KEY (snapshot_id, seq)
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
  notes          TEXT NOT NULL DEFAULT '[]',        -- JSON list
  compromises    TEXT NOT NULL DEFAULT '[]'         -- JSON list
);

CREATE TABLE recommendation_outfits (
  id                TEXT PRIMARY KEY,
  recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
  rank              INTEGER NOT NULL CHECK (rank >= 1),
  score_total       REAL NOT NULL CHECK (score_total BETWEEN 0 AND 1),
  scores            TEXT NOT NULL,                  -- JSON components + weights
  hour_plan         TEXT NOT NULL,                  -- JSON list of HourPlanEntry
  reasoning         TEXT NOT NULL,                  -- JSON list of {class, text}
  accessories       TEXT NOT NULL DEFAULT '[]',
  notes             TEXT NOT NULL DEFAULT '[]',
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
  layer_role  TEXT NOT NULL,                        -- snapshot at log time (FR-11)
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
CREATE INDEX idx_hours_snapshot      ON forecast_hours(snapshot_id, seq);
CREATE INDEX idx_recs_date           ON recommendations(date, created_at);
CREATE INDEX idx_wear_date           ON wear_logs(date);
CREATE INDEX idx_wear_items_garment  ON wear_log_items(garment_id);
```

The 23–25-rows-per-snapshot invariant, `seq` contiguity, and JSON shapes are
enforced at the repository/model layer (SQLite CHECKs cannot count rows).

## 5. Config file (`~/.dresscast/config.toml`)

Not a database entity; documented here because the engine consumes it as
input. All keys have defaults; the file may be absent. Every key below is
read by named code — there are no decorative keys.

```toml
[location]                    # required for live weather only
name = "home"
lat = 40.71
lon = -74.01
timezone = "America/New_York"

[defaults]
met = 1.6                     # metabolic rate, SCOPE.md D3          -> RequestParams.met
wear_window = [7, 22]         # inclusive start hour, exclusive end  -> RequestParams.wear_window
commute_hours = [7, 8, 9, 17, 18, 19]  # SCOPE.md D18                -> RequestParams.commute_hours
k = 3                         # outfits per recommendation           -> RequestParams.k
occasion_weekday = "work"     # default --occasion Mon-Fri           -> CLI edge only
occasion_weekend = "casual"   #                                      -> CLI edge only

[weather]
provider = "fixture"          # or "open_meteo" (requires DRESSCAST_LIVE_WEATHER=1)
fixture_dir = "~/.dresscast/weather"

[occasions]
vocabulary = ["casual", "work", "sport", "outdoor", "formal"]  # HC-3 validation set
```

*(Revision 2 removed `[occasions.<name>] formality_band`: no FR consumed it,
and SCOPE.md D9 states why occasion→formality bands are deliberately absent.)*

## 6. Example records

**Garment** (core loop example)

```json
{"id": "6f2a…", "name": "gray-lambswool-sweater", "category": "sweater_thick",
 "layer_role": "mid", "accessory_class": null, "clo": 0.36,
 "waterproofness": 0, "windproofness": 0, "formality": 3,
 "colors": [{"name": "gray", "hue": null, "neutral": true, "role": "main"}],
 "style_tags": ["classic"], "occasions": ["work", "casual"],
 "wears_before_laundry": 5, "wears_since_wash": 2, "status": "clean",
 "overridden_fields": [],
 "photo_path": "/home/user/.dresscast/photos/6f2a….jpg",
 "photo_sha256": "9c31…", "notes": null,
 "created_at": "2026-07-30T21:14:02Z", "updated_at": "2026-07-31T07:55:10Z"}
```

**AttributeSuggestion** (accepted, showing the FR-2 cascade)

The garment was tagged `fleece` (clo 0.30, formality 2, wears 5) with no
explicit overrides. The extractor proposed `category: wool_coat`. Accepting
`category` alone cascades `clo → 0.60`, `layer_role → outer`,
`formality → 4`, `wears_before_laundry → 30`:

```json
{"id": "c8d0…", "garment_id": "0b6c…", "source": "fixture",
 "payload": {"category": {"value": "wool_coat", "confidence": 0.91},
             "colors": {"value": [{"name": "navy", "hue": null,
                        "neutral": true, "role": "main"}], "confidence": 0.84}},
 "status": "accepted",
 "accepted_fields": [
   {"field": "category", "via": "explicit", "old": "fleece", "new": "wool_coat"},
   {"field": "clo", "via": "cascade", "old": 0.30, "new": 0.60},
   {"field": "layer_role", "via": "cascade", "old": "mid", "new": "outer"},
   {"field": "formality", "via": "cascade", "old": 2, "new": 4},
   {"field": "wears_before_laundry", "via": "cascade", "old": 5, "new": 30}],
 "created_at": "2026-07-30T21:14:30Z", "resolved_at": "2026-07-30T21:15:02Z"}
```

Had the user previously set `clo` by hand (`overridden_fields: ["clo"]`),
`clo` would stay 0.30 — which is more than ±0.15 from the `wool_coat` preset
0.60 — so FR-2's step 3 re-validation fails, the transaction rolls back, and
the caller gets `invalid_params: clo 0.30 outside 0.45–0.75 for category
wool_coat (user-overridden; accept 'clo' together with 'category')`.

**ForecastSnapshot + two ForecastHours** (spring swing day)

```json
{"id": "a1b2…", "date": "2026-04-14", "location_name": "home",
 "lat": 40.71, "lon": -74.01, "timezone": "America/New_York",
 "provider": "fixture", "fetched_at": "2026-04-14T06:30:00Z", "raw": null}
```
```json
{"snapshot_id": "a1b2…", "seq": 7, "hour": 7, "temp_c": 6.0, "wind_kmh": 15.0,
 "humidity_pct": 70.0, "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 0.5}
```
```json
{"snapshot_id": "a1b2…", "seq": 15, "hour": 15, "temp_c": 17.0, "wind_kmh": 10.0,
 "humidity_pct": 45.0, "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 4.2}
```

**Recommendation + top outfit.** Every number below is reproducible from
SCOPE.md FR-5/FR-6/FR-7 with a calculator; the arithmetic is spelled out so a
reviewer can check it without running code.

- 07:00, worn config base+mid_1+outer, coat windproofness 1 → effective wind
  `15.0 × 0.6 = 9.0`; `9^0.16 = 1.42125`;
  `WCT = 13.12 + 0.6215·6 − 11.37·1.42125 + 0.3965·6·1.42125 = 4.0703` →
  `feels_c 4.070`. Bare (wind 15.0, `15^0.16 = 1.54233`) → `2.982`.
- `required_clo = (34 − 4.070)/(7.66·1.6) − 0.7 = 29.930/12.256 − 0.7 = 1.742`.
  This wardrobe's achievable band is [0.334, 1.910], so no clamp:
  `target_clo = 1.742`.
- Ensemble: shirt 0.25 + sweater 0.36 + coat 0.60 + jeans 0.24 + boots 0.10
  (hosiery included, D1) = Σ 1.55 → `Icl = 0.835·1.55 + 0.161 = 1.455`.
- `deviation = 1.455 − 1.742 = −0.287`; `|dev| > 0.25` so
  `hour_score = 1 − (0.287 − 0.25)/0.75 = 0.951`; `in_band false`.
- 15:00, worn config base+bottom+footwear, no windproof layer → effective
  wind 10.0; T = 17 sits between both ramps → `feels_c = bare = 17.0`;
  `required = 17/12.256 − 0.7 = 0.687`; Σ = 0.25 + 0.24 + 0.10 = 0.59 →
  `Icl = 0.654`; `deviation = −0.033`; `in_band true`; `hour_score 1.0`.

```json
{"id": "9e4f…", "date": "2026-04-14", "snapshot_id": "a1b2…",
 "created_at": "2026-04-14T06:31:00Z", "engine_version": "0.1.0", "seed": 7,
 "params": {"occasion": "work", "wear_window": [7, 22],
            "commute_hours": [7, 8, 9, 17, 18, 19], "met": 1.6, "k": 3,
            "weights": {"thermal": 0.40, "protection": 0.15, "color": 0.15,
                        "style": 0.15, "variety": 0.15},
            "thresholds_version": "2026.07-a"},
 "wardrobe_hash": "5b09…", "notes": [], "compromises": []}
```
```json
{"id": "77aa…", "recommendation_id": "9e4f…", "rank": 1,
 "score_total": 0.951800,
 "scores": {"thermal": 0.962000, "protection": 1.0, "color": 1.0,
            "style": 0.880000, "variety": 0.900000,
            "weights": {"thermal": 0.40, "protection": 0.15, "color": 0.15,
                        "style": 0.15, "variety": 0.15}},
 "hour_plan": [
   {"seq": 7, "hour": 7, "temp_c": 6.0, "wind_kmh": 15.0, "humidity_pct": 70.0,
    "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 0.5,
    "bare_feels_c": 2.982, "effective_wind_kmh": 9.0, "feels_c": 4.070,
    "required_clo": 1.742, "target_clo": 1.742, "clamped": null,
    "worn_slots": ["base","mid_1","outer","bottom","footwear"],
    "carried_slots": [], "ensemble_clo": 1.455,
    "deviation": -0.287, "in_band": false, "hour_score": 0.951,
    "exposure_weight": 3.0, "rain_cover_on": false, "protect_score": 1.0,
    "notes": ["slightly_cool"]},
   {"seq": 15, "hour": 15, "temp_c": 17.0, "wind_kmh": 10.0, "humidity_pct": 45.0,
    "precip_prob": 0.05, "precip_mmh": 0.0, "uv_index": 4.2,
    "bare_feels_c": 17.0, "effective_wind_kmh": 10.0, "feels_c": 17.0,
    "required_clo": 0.687, "target_clo": 0.687, "clamped": null,
    "worn_slots": ["base","bottom","footwear"],
    "carried_slots": ["mid_1","outer"], "ensemble_clo": 0.654,
    "deviation": -0.033, "in_band": true, "hour_score": 1.0,
    "exposure_weight": 1.0, "rain_cover_on": false, "protect_score": 1.0,
    "notes": ["shed_layer","carrying"]}
 ],
 "reasoning": [
   {"class": "day_thermal",  "text": "Feels like 2.98°C at 07:00 rising to 17.0°C by 15:00 — the day asks for 1.74 clo down to 0.69 clo."},
   {"class": "layer_change", "text": "Shed the coat and sweater at 11:00; carry them — you will want the coat again after 20:00."},
   {"class": "rain",         "text": "No rain expected (max probability 5%)."},
   {"class": "palette",      "text": "All-neutral palette (white / gray / navy / denim) — no clashes."},
   {"class": "variety",      "text": "Least-fresh item: the sweater, last worn 3 days ago."}
 ],
 "accessories": [], "notes": [], "compromises": []}
```

`score_total` check: `0.40·0.962 + 0.15·(1.0 + 1.0 + 0.880 + 0.900) =
0.3848 + 0.5670 = 0.9518`. Note that no `wind`, `cold_extremities`, `uv`,
`wardrobe_limit` or `compromise` line appears, because none of those triggers
fired — FR-15's iff-invariant, which EVALS.md M4 checks.

**OutfitItems** (for outfit 77aa…)

```json
[{"outfit_id": "77aa…", "slot": "base",     "garment_id": "11..white-oxford-shirt"},
 {"outfit_id": "77aa…", "slot": "mid_1",    "garment_id": "6f2a..gray-lambswool-sweater"},
 {"outfit_id": "77aa…", "slot": "outer",    "garment_id": "0b6c..navy-wool-coat"},
 {"outfit_id": "77aa…", "slot": "bottom",   "garment_id": "3d81..dark-jeans"},
 {"outfit_id": "77aa…", "slot": "footwear", "garment_id": "b2c4..brown-leather-boots"}]
```

**A clamped, degraded run** (edge wardrobe on `winter_windy`) — shows the
`wardrobe_ceiling` path and the FR-14 vocabulary:

```json
{"seq": 7, "hour": 7, "temp_c": -4.0, "wind_kmh": 40.0, "humidity_pct": 65.0,
 "precip_prob": 0.0, "precip_mmh": 0.0, "uv_index": 0.0,
 "bare_feels_c": -12.744, "effective_wind_kmh": 24.0, "feels_c": -10.909,
 "required_clo": 2.964, "target_clo": 2.040, "clamped": "wardrobe_ceiling",
 "worn_slots": ["base","mid_1","mid_2","outer","bottom","leg_base","footwear"],
 "carried_slots": [], "ensemble_clo": 2.040,
 "deviation": 0.0, "in_band": true, "hour_score": 1.0,
 "exposure_weight": 3.0, "rain_cover_on": false, "protect_score": 1.0,
 "notes": ["wardrobe_ceiling"]}
```
```json
{"class": "wardrobe_limit",
 "text": "07:00–10:00 is colder than anything you own: the day asks for 2.96 clo and your warmest legal combination reaches 2.04 — you are 0.92 clo short. Consider a heavier parka or a second mid layer."}
```

**WearLog + items**

```json
{"id": "eeb1…", "date": "2026-04-14", "source": "recommendation",
 "outfit_id": "77aa…", "created_at": "2026-04-14T07:05:00Z"}
```
```json
[{"wear_log_id": "eeb1…", "garment_id": "11…",   "layer_role": "base"},
 {"wear_log_id": "eeb1…", "garment_id": "6f2a…", "layer_role": "mid"},
 {"wear_log_id": "eeb1…", "garment_id": "0b6c…", "layer_role": "outer"},
 {"wear_log_id": "eeb1…", "garment_id": "3d81…", "layer_role": "bottom"},
 {"wear_log_id": "eeb1…", "garment_id": "b2c4…", "layer_role": "footwear"}]
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
