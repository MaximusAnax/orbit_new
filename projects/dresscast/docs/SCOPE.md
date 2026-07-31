# dresscast — SCOPE

## One-liner

Reads the day's hourly weather and assembles complete, ranked outfits from your
own photographed-and-tagged wardrobe — layered so a cold morning and a warm
afternoon are both comfortable, rain-proofed when it matters, occasion-
appropriate, color-coherent, laundry-aware, and never yesterday's outfit —
with the reasoning spelled out hour by hour.

## Problem statement

"What do I wear today?" is a small daily optimization problem people solve
badly. The failure modes are specific: dressing for the temperature *right
now* instead of the day's range (freezing at 07:30, sweating at 15:00);
grabbing the warm coat on a warm-rain day when what's needed is a light
waterproof shell; discovering at the door that the intended trousers are in
the hamper; wearing the same two outfits on repeat while most of the closet
goes unworn; and pairing items that clash in color or formality.

dresscast treats the day as what it actually is: an hourly sequence of
thermal and precipitation conditions, and the wardrobe as a constrained
inventory. The engine's core job — **the hard part** — is *outfit assembly
under constraints*: pick garments for each layer slot such that the ensemble,
**with layers added or shed over the day**, tracks the hour-by-hour required
insulation (computed from a heat-balance model in clo units, on feels-like
temperatures), satisfies precipitation/wind protection rules, fits the
occasion, coheres in color and formality, uses only clean garments, and
differs from what was worn recently. It returns the top-k complete outfits,
each with an hourly layer plan ("wear all three layers until ~11:00, carry
the coat after lunch") and human-readable reasoning.

Everything external sits behind offline-first adapters: hourly weather comes
from a deterministic fixture provider by default (live Open-Meteo later), and
garment attributes come from manual tagging (an optional vision extractor can
pre-fill suggestions later). The engine is pure and deterministic.

## Target user

The repo owner: a single user running the tool locally. One wardrobe, one
home location, no accounts, no multi-tenancy, no web UI. API and CLI only.
Garment photos are ordinary image files on disk; the phone-camera capture
flow is out of scope (see Non-goals).

## User stories

- **US-1: Catalog my closet fast.** As the owner, I can add a garment in
  under a minute with a photo and manual tags, with sensible defaults doing
  most of the work.
  *Acceptance:* `dresscast add --photo coat.jpg` prompts for name, category,
  and colors; category selection auto-fills clo (warmth), layer role,
  formality, and wears-before-laundry from the preset table (§Design-D1,
  D11), each overridable; the garment then appears in `dresscast ls`.
- **US-2: Photo → suggested tags.** As the owner, I can ask the attribute
  extractor to propose attributes from a garment's photo and accept or reject
  the suggestion; manual tagging always works without it.
  *Acceptance:* `dresscast suggest <garment>` stores an AttributeSuggestion
  and prints proposed fields with confidences; `--accept` merges only the
  proposed fields I confirm; with no extractor configured the command says so
  and exits non-zero without touching the garment.
- **US-3: What do I wear today?** As the owner, one command gives me ranked
  complete outfits for today with reasoning.
  *Acceptance:* `dresscast outfit --date 2026-07-31 --occasion work --seed 7`
  prints k=3 complete outfits (every required slot filled), each with a total
  score, component breakdown, an hourly layer plan, and reasoning lines; the
  same command with the same stored wardrobe/forecast reproduces byte-
  identical output.
- **US-4: Cold morning, warm afternoon.** As the owner, on a large-diurnal-
  range day I get a layered outfit plus a plan telling me when to shed
  layers, instead of one compromise ensemble.
  *Acceptance:* on a fixture day spanning 6→18 °C, the top outfit contains a
  removable mid and/or outer layer and its hourly plan shows at least two
  distinct configurations; every wear-window hour's ensemble clo is within
  the acceptable band of that hour's requirement (§Design-D5).
- **US-5: Rain handled, not overdressed.** As the owner, on rain days the
  outfit includes adequate rain cover matched to intensity, including the
  warm-rain trap (waterproof shell, *not* the warm coat).
  *Acceptance:* on a fixture day at 22→30 °C with a 14:00–17:00 downpour,
  the top outfit's plan keeps a waterproofness ≥ 2 layer (or a valid
  umbrella) on during rain hours while total insulation stays in band; the
  reasoning names the rain window.
- **US-6: Dress for the occasion.** As the owner, I can ask for `work`,
  `casual`, `sport`, `outdoor`, or `formal` and get outfits whose items all
  fit the occasion and sit within one formality step of each other.
  *Acceptance:* `dresscast outfit --occasion work` never includes a garment
  that does not list `work` in its occasions; max−min formality across core
  items ≤ 1; sneakers tagged only `casual,sport` never appear.
- **US-7: Laundry-aware.** As the owner, dirty garments are never suggested,
  wearing something advances it toward the hamper, and laundry day resets it.
  *Acceptance:* after `dresscast wear` logs a shirt for its 2nd wear (its
  `wears_before_laundry`=2), the shirt's status is `dirty` and it is absent
  from the next recommendation; `dresscast laundry --all` returns all dirty
  items to `clean` with `wears_since_wash`=0.
- **US-8: Fresh outfits, whole closet.** As the owner, I never get
  yesterday's exact outfit again today, recently worn items are deprioritized,
  and over weeks my whole eligible wardrobe gets used.
  *Acceptance:* with yesterday's wear logged, today's recommendations never
  contain an identical core-item set; in the committed 14-day rollout eval,
  no consecutive-day repeat occurs and ≥ 60% of eligible garments are worn
  at least once (EVALS.md M6).

## Functional requirements

Each FR is independently testable; test/eval names reference FR ids (mapping
table in EVALS.md §7).

- **FR-1 Garment creation and validation (manual tagging).** The system
  creates/updates garment records with the full attribute set (DATA_MODEL.md
  §2.1): category, layer role, clo, waterproofness 0–3, windproofness 0–2,
  formality 1–5, colors, style tags, occasions, wears-before-laundry, photo
  reference. Validation: clo within the category's allowed range
  (§Design-D1 table ±0.15, absolute bounds [0.0, 1.5]); occasions non-empty
  for non-accessory garments; colors well-formed per DATA_MODEL.md §2.1;
  names unique case-insensitively. Choosing a category with a warmth level
  0–5 instead of an explicit clo maps to the preset table deterministically.
- **FR-2 Photo attach and attribute suggestion.** A photo file can be
  attached to a garment (copied under the data directory, SHA-256 recorded).
  `suggest` invokes the `AttributeExtractor` adapter, persisting an
  append-only AttributeSuggestion (proposed fields + per-field confidence,
  source, status `pending`). Accepting merges only confirmed fields into the
  garment and marks the suggestion `accepted`; rejecting marks it `rejected`.
  Suggestions never mutate a garment without explicit acceptance. Manual
  tagging (FR-1) is fully functional with no extractor configured.
- **FR-3 Wardrobe import/export.** The wardrobe round-trips through a
  documented JSON schema (DATA_MODEL.md §3.1). Re-importing the same file is
  idempotent (matched by garment `id`, else by unique name); malformed
  entries are reported with indexes and skipped, never silently dropped.
- **FR-4 Laundry lifecycle.** Garment status ∈ {clean, dirty, in_laundry,
  retired} with enforced transitions (DATA_MODEL.md §2.1). Logging a wear
  increments `wears_since_wash` for each worn garment (transactionally with
  the WearLog) and flips status to `dirty` when `wears_since_wash ≥
  wears_before_laundry`. A laundry event resets listed (or `--all` dirty)
  garments to clean/0. Retired garments are excluded from every
  recommendation and report. Manual `mark dirty` is allowed at any time.
- **FR-5 Forecast acquisition and snapshot.** A `WeatherProvider` adapter
  returns a 24-hour forecast for (location, date): per hour `temp_c`,
  `wind_kmh`, `humidity_pct`, `precip_prob` (0–1), `precip_mmh`, `uv_index`.
  Fetches are persisted as append-only ForecastSnapshots with provider
  provenance; recommendations reference a specific snapshot. The fixture
  provider is deterministic (committed JSON keyed by date); the live
  Open-Meteo provider maps documented API fields (§Architecture-Adapters)
  and activates only when explicitly enabled with a configured location.
  Validation: exactly 24 hours, fields within physical ranges.
- **FR-6 Feels-like computation.** The engine computes per-hour feels-like
  temperature deterministically: the JAG/TI 2001 wind-chill formula
  (`WCT = 13.12 + 0.6215·T − 11.37·v^0.16 + 0.3965·T·v^0.16`, T °C,
  v km/h) when `T ≤ 10 °C` and `v > 4.8 km/h`; Steadman's apparent
  temperature (`AT = T + 0.33·e − 0.70·ws − 4.00`, `e = (rh/100)·6.105·
  exp(17.27·T/(237.7+T))` hPa, ws m/s) when `T ≥ 26 °C`; otherwise air
  temperature. Outputs match published reference chart values within
  ±0.5 °C (EVALS.md M1). A worn windproof layer attenuates the wind input:
  effective wind = `v × {1.0, 0.6, 0.3}` for outermost-layer windproofness
  {0, 1, 2}, so feels-like is a function of (hour, configuration).
- **FR-7 Thermal requirement model.** Required intrinsic insulation
  `required_clo(T_feels, met) = clamp((34 − T_feels) / (7.66 × met) − 0.7,
  0.0, 4.5)` with constants per §Design-D3; `met` is a request parameter
  (default 1.6). Comfort band: |deviation| ≤ 0.25 clo comfortable; hour
  score `s_h = max(0, 1 − max(0, |dev_h| − 0.25)/0.75)` (§Design-D5).
- **FR-8 Ensemble insulation and layer configurations.** Ensemble intrinsic
  insulation from worn garments: `Icl = 0.835 × Σ clo_i + 0.161` (0 when no
  garments) — the ASHRAE/McCullough regression. An outfit's configuration
  set: base top, bottom, leg base, and footwear always worn; mids removable
  LIFO ordered by descending clo (thickest is outermost, shed first); outer
  independently on/off — giving ≤ 2×(m+1) ≤ 6 configurations. Per hour the
  engine selects the feasible configuration (rain-hour cover per FR-10 must
  stay on) minimizing |Icl(config) − required_clo(h, config)|, where the
  requirement itself uses the config's windproofness (FR-6). Accessories are
  excluded from Icl (advisory only, §Design-D6).
- **FR-9 Outfit assembly under constraints — THE HARD PART.** Given a
  wardrobe, a forecast snapshot, request params (date, occasion, wear
  window, met, k, seed), and wear history, the engine returns the top-k
  complete outfits maximizing
  `total = 0.40·S_thermal + 0.15·S_protect + 0.15·S_color + 0.15·S_style +
  0.15·S_variety`, subject to hard constraints HC-1…HC-8:
  - **HC-1 slot coverage:** exactly one base top and one bottom (or one
    `full_body` dress replacing both), exactly one footwear, 0–2 mids, 0–1
    outer, 0–1 leg base, 0–4 accessories.
  - **HC-2 cleanliness:** every item has status `clean`.
  - **HC-3 occasion:** every core (non-accessory) item lists the requested
    occasion.
  - **HC-4 formality coherence:** max − min formality over core items ≤ 1.
  - **HC-5 no duplicate garment within an outfit.**
  - **HC-6 rain cover:** every hour with `precip_prob ≥ 0.5` has a
    feasible configuration carrying adequate cover for its intensity class
    (FR-10), and the hourly plan uses one.
  - **HC-7 leg base gating:** a `leg_base` item is allowed only when the
    day's minimum feels-like ≤ 0 °C.
  - **HC-8 no-repeat:** the core-item set differs from every outfit worn
    yesterday.
  Search: per-slot candidate filtering, capped enumeration with an
  admissible partial-score bound, then greedy selection of k outfits with
  pairwise core-item Jaccard ≤ 0.5 (MMR-style diversity), deterministic
  tie-breaks (§Design-D12). Wardrobe cap 500 garments with a clear error.
- **FR-10 Weather protection rules.** Rain: hours with `precip_prob ≥ 0.5`
  are *rain hours* (hard, HC-6); `0.3 ≤ p < 0.5` hours apply a soft
  `S_protect` penalty when no cover is available. Adequacy by intensity
  class: `< 2.5 mm/h` (light) needs waterproofness ≥ 1 or umbrella;
  `2.5–10` (moderate) needs ≥ 2 or umbrella; `> 10` (heavy) needs
  waterproofness ≥ 2 worn — umbrella alone insufficient. An umbrella is
  invalid as cover in any hour with wind ≥ 35 km/h. Wind: max hourly wind
  ≥ 30 km/h without a windproofness ≥ 1 outer → `S_protect` penalty. Cold
  extremities: min feels-like < 5 °C → advisory to add hat/gloves/scarf if
  present in wardrobe. UV: max `uv_index ≥ 6` during a 10:00–16:00 overlap
  with the wear window → advisory (hat/sunglasses/sunscreen). Advisories
  appear in reasoning; they never gate.
- **FR-11 Color and style scoring.** `S_color`: mean pairwise harmony over
  core items' main colors using the hue-zone table in §Design-D8 (neutrals
  score 1.0 with anything), minus 0.15 if distinct non-neutral hue families
  (30° buckets over all listed colors) exceed 3. `S_style` = 0.6 ×
  formality tightness (1.0 if spread 0, 0.7 if spread 1) + 0.4 × tag
  cohesion (fraction of core-item pairs sharing ≥ 1 style tag).
- **FR-12 Variety and history.** `S_variety = 1 − mean_i 2^(−d_i / 3)` over
  core items, `d_i` = days since item i was last worn (term 0 if never
  worn); yesterday-identical sets are hard-blocked (HC-8). Wear history
  comes from WearLogs (FR-13); the half-life (3 days) is a named constant.
- **FR-13 Wear logging.** A recommended outfit (`--rank r`) or an arbitrary
  item list can be logged as worn on a date; multiple logs per date are
  allowed (e.g., gym + work). Logging drives FR-4 counters and FR-12
  history in one transaction. A mistaken log can be undone on the same
  calendar day only, reversing the counter/status side effects in one
  transaction (DATA_MODEL.md §2.7); after that day logs are immutable.
- **FR-14 Recommendation persistence.** Every recommendation run is
  append-only: params, seed, engine version, forecast snapshot id, a
  `wardrobe_hash` (SHA-256 over the sorted eligible-garment records), the
  ranked outfits with full score breakdowns, hourly plans, reasoning, and
  any compromises. Stored plans are self-contained (they snapshot the
  numbers used), so later wardrobe edits never corrupt history.
- **FR-15 Graceful degradation.** When zero outfits satisfy HC-1…HC-8, the
  engine relaxes in a fixed lexicographic ladder — R1 drop HC-8 (variety);
  R2 widen HC-4 to spread ≤ 2; R3 accept one-level-lower rain cover for
  moderate intensity — recording each applied relaxation as a structured
  `compromise` on the output. HC-2 (cleanliness), HC-3 (occasion), HC-1
  (slot coverage), and HC-5 are never relaxed; if the ladder is exhausted
  the result is a structured `infeasible_wardrobe` error naming the missing
  capability (e.g., "no waterproof outer for 6 rain hours").
- **FR-16 API.** A FastAPI app exposes the endpoints in §Architecture-API
  with Pydantic schemas; engine/store errors map to 4xx with structured
  detail codes (`infeasible_wardrobe`, `unknown_garment`,
  `invalid_transition`, `no_extractor_configured`, `wardrobe_too_large`,
  `invalid_params`).
- **FR-17 CLI.** A Typer CLI exposes the commands in §Architecture-CLI;
  every command exits 0 on success, non-zero on failure, and supports
  `--json`. Temperatures display in °C by default with `--units f`
  conversion at the presentation layer only.
- **FR-18 Determinism.** For fixed (wardrobe state, forecast snapshot, wear
  history, params, seed), recommendation output is byte-identical across
  runs and platforms. The engine never reads the clock, filesystem, or
  network; dates and "today" are inputs supplied by the CLI/API edge.

## Non-goals (this pass)

- **No web or mobile UI** (workspace-wide decision). Photos are image files
  referenced by path; there is no camera capture flow, background removal,
  or gallery. CLI + API only.
- **Live vision extractor deferred.** This pass ships the
  `AttributeExtractor` protocol, the deterministic fixture implementation,
  and the human-confirmation flow. The live implementation (zero-shot
  CLIP-style classifier or hosted vision-language model, optional extra) is
  next phase. Manual tagging is the product's floor and is fully supported.
- **Live weather is shipped but best-effort and never eval-gated.** All
  tests and evals run against the fixture provider (CONVENTIONS.md §3);
  Open-Meteo mapping is unit-tested against a committed sample response,
  no network.
- **No learned personalization.** Wear logs are collected now precisely so a
  future phase can learn preferences ("you never wear the orange sweater"),
  but this pass is rule-grounded only — rules first, ML later, so there is
  a baseline to evaluate against.
- **No fit/size/body modeling, no shopping or purchase suggestions, no
  social features.**
- **No indoor comfort modeling.** The tool optimizes for outdoor exposure
  during the wear window at a configurable metabolic rate; indoors you shed
  layers, which the layer plan already supports.
- **No fabric physiology beyond the ordinal attributes.** Moisture-vapor
  resistance (ISO 11092 Ret), breathability, and wicking are out of scope;
  waterproofness/windproofness are user-tagged ordinals (§Design-D7).
- **No precipitation-type distinction.** Snow, sleet, and rain all count as
  precipitation with intensity in mm/h liquid-equivalent; a snow-specific
  traction/footwear rule is a future refinement.
- **No multi-segment days or travel mode.** One occasion per request (run
  twice for office-then-dinner); one home location in config.
- **No multi-user, auth, or hosting concerns.**
- **No real personal photos or branded garment data committed to the
  repo.** All fixtures are synthetic wardrobes and synthetic weather
  (EVALS.md §4).

## Architecture

Package `dresscast` at `projects/dresscast/src/dresscast/`. Layering per
CONVENTIONS.md: pure engine, Protocol-based adapters, Repository store, thin
API/CLI. A thin `services.py` orchestrates adapters + store + engine (the
only layer touching both I/O and the engine); API and CLI call services only.

### Engine modules (`engine/`, pure, deterministic)

- `models.py` — Pydantic domain models: `Garment`, `HourlyWeather`,
  `DayForecast`, `RequestParams`, `LayerConfig`, `HourPlanEntry`,
  `ScoredOutfit`, `Recommendation`, `Compromise`; all named constants
  (weights, bands, thresholds, half-life) in one place.
- `comfort.py` — feels-like (FR-6), `required_clo` (FR-7), ensemble
  insulation and configuration enumeration, per-hour config selection and
  thermal scoring (FR-8): `hourly_plan(outfit, forecast, params) ->
  list[HourPlanEntry]`, `thermal_score(plan) -> float`.
- `protection.py` — rain/wind/UV/cold-extremity rules (FR-10):
  `rain_hours(forecast)`, `cover_adequate(outfit, hour) -> bool`,
  `protect_score(outfit, forecast) -> tuple[float, list[str]]`.
- `palette.py` — color harmony (FR-11): hue-zone pairwise table, hue-family
  counting, `color_score(outfit) -> float`.
- `style.py` — formality coherence and tag cohesion (FR-11), occasion
  filtering helpers (HC-3, HC-4).
- `variety.py` — recency penalty and repeat detection (FR-12, HC-8):
  `variety_score(outfit, history, today) -> float`.
- `assemble.py` — the hard part (FR-9, FR-15): per-slot candidate
  filtering, bounded enumeration, hard-constraint checking, scalarized
  scoring, MMR diversity selection, relaxation ladder:
  `recommend(wardrobe, forecast, history, params) -> Recommendation`.
- `explain.py` — deterministic template rendering of reasoning lines and
  the hourly plan table (FR-13's stored text, US-3/4/5).

### Adapter interfaces (`adapters/`)

Each is a `typing.Protocol`; offline implementations are the defaults used
by tests and evals.

- **`WeatherProvider`** — `get_day(location: Location, date: date) ->
  DayForecast` (24 `HourlyWeather` rows; raises `ForecastUnavailable`).
  - `FixtureWeatherProvider` (offline, default): reads committed JSON
    scenario files keyed by ISO date from a configured directory
    (`evals/fixtures/weather/` for evals; a user-suppliable path in
    production). Deterministic, no network.
  - `OpenMeteoWeatherProvider` (live): Open-Meteo forecast API (free, no
    API key) — `GET /v1/forecast` with `hourly=temperature_2m,
    relative_humidity_2m,precipitation_probability,precipitation,
    wind_speed_10m,uv_index&timezone=<tz>`; activates only when
    `DRESSCAST_LIVE_WEATHER=1` and a location (lat/lon/tz) is configured.
    Provider `apparent_temperature` is stored for display comparison only;
    the engine always computes its own feels-like (FR-6) so results are
    deterministic and testable.
- **`AttributeExtractor`** — `extract(photo_path: str) ->
  AttributeSuggestionPayload | None` (proposed category, colors, style
  tags, formality; per-field confidence 0–1).
  - `FixtureAttributeExtractor` (offline, default): committed JSON map
    keyed by photo SHA-256 (falling back to basename); deterministic; used
    by tests/evals of the suggestion flow.
  - `VisionAttributeExtractor` (live, next phase, optional extra
    `vision`): zero-shot open-vocabulary classification (CLIP-style,
    Radford et al. 2021) over the category/color/style vocabularies, or a
    hosted vision-language model; credentials/weights via documented env
    vars. Never required by core tests or evals; suggestions always pass
    through human confirmation (FR-2).

### Store (`store/`)

`Repository` protocol with `SqliteRepository` (stdlib `sqlite3`, schema in
DATA_MODEL.md §4) and `InMemoryRepository` for tests. Operations: garment
CRUD + state transitions, suggestion append/resolve, snapshot append + read,
recommendation append + read, wear-log append (with counter side effects in
the same transaction), laundry append, history queries (`last_worn_dates`,
`worn_yesterday_sets`). No engine logic.

### API (FastAPI, `api/`)

```
GET    /health                                  -> {status, version}
POST   /garments                                -> 201 Garment            (FR-1)
GET    /garments?status=&occasion=&category=    -> [Garment]
GET    /garments/{id}                           -> Garment
PATCH  /garments/{id}                           -> Garment                (FR-1; includes status transitions, FR-4)
POST   /garments/{id}/photo                     -> Garment                (FR-2; multipart upload)
POST   /garments/{id}/suggest                   -> 201 AttributeSuggestion (FR-2)
POST   /suggestions/{id}/accept                 -> Garment                (FR-2; body: {fields: [...]})
POST   /suggestions/{id}/reject                 -> AttributeSuggestion
POST   /wardrobe/import                         -> ImportReport           (FR-3)
GET    /wardrobe/export                         -> wardrobe JSON          (FR-3)
GET    /forecast?date=YYYY-MM-DD                -> ForecastSnapshot       (FR-5; fetches via adapter + snapshots)
POST   /recommendations                         -> 201 Recommendation     (FR-9; body: {date, occasion, wear_window?, met?, k?, seed?})
GET    /recommendations/{id}                    -> Recommendation (outfits, plans, reasoning)
GET    /recommendations?date=YYYY-MM-DD         -> [RecommendationSummary]
POST   /recommendations/{id}/wear               -> 201 WearLog            (FR-13; body: {rank, date?})
POST   /wear                                    -> 201 WearLog            (FR-13; body: {garment_ids, date?})
GET    /wear?from=&to=                          -> [WearLog]
POST   /laundry                                 -> 201 LaundryEvent       (FR-4; body: {garment_ids | all_dirty: true})
```

### CLI (Typer, `cli/`)

```
dresscast add [--photo PATH] [--category C] [--warmth 0-5 | --clo F] [...]   # FR-1/2
dresscast ls [--status clean|dirty] [--occasion O] [--category C]
dresscast show GARMENT
dresscast edit GARMENT [--clo F] [--formality N] [--colors ...] [...]        # FR-1
dresscast suggest GARMENT [--accept | --accept-fields f1,f2]                 # FR-2
dresscast import PATH | export [--out PATH]                                  # FR-3
dresscast forecast [--date YYYY-MM-DD]                                       # FR-5
dresscast outfit [--date D] [--occasion work] [--k 3] [--seed 7]
                 [--met 1.6] [--window 07:00-22:00]                          # FR-9 (US-3/4/5/6)
dresscast explain REC [--rank 1]                                             # stored reasoning + hourly plan
dresscast wear [REC --rank 1 | --items id,id,...] [--date D] [--undo LOG]    # FR-13
dresscast laundry [--all | GARMENT...]                                       # FR-4
dresscast history [--days 14]                                                # FR-12 view
dresscast serve [--port 8000]                                                # uvicorn wrapper
```

All commands accept `--db PATH` (default `~/.dresscast/dresscast.db`),
`--config PATH` (default `~/.dresscast/config.toml`, DATA_MODEL.md §5), and
`--json`. `--date` defaults to today read at the CLI edge (never inside the
engine, FR-18).

### Size budget (implementation phase)

engine ≈ 1,250 lines (models 220, comfort 230, protection 120, palette 100,
style 80, variety 70, assemble 300, explain 130); adapters ≈ 420; store
≈ 380; api ≈ 300; cli ≈ 420; tests + evals ≈ 1,250. Total ≈ 4,000 — at the
top of the 2,000–4,000 band, with comfort + assembly (the hard part) getting
the deepest treatment.

## Key design decisions and assumptions

- **D1 — Warmth is measured in clo, taken from the standard tables.** The
  clo is the standard unit of clothing insulation (Gagge, Burton & Bazett,
  1941): 1 clo = 0.155 m²·K/W, the ensemble keeping a resting person
  comfortable at 21 °C. Per-garment values come from the ASHRAE Standard 55
  / ASHRAE Fundamentals ch. 9 / ISO 9920 garment tables; the locked
  "warmth rating" attribute *is* the garment's clo, entered directly or via
  a warmth level 0–5 that maps to the category preset. Category presets
  (defaults; user-overridable within ±0.15):

  | Category | clo | Role | Source |
  |---|---|---|---|
  | t-shirt | 0.08 | base | ASHRAE 55 |
  | long-sleeve tee / polo | 0.12 / 0.17 | base | ASHRAE 55 |
  | short-sleeve shirt | 0.19 | base | ASHRAE 55 |
  | long-sleeve shirt | 0.25 | base | ASHRAE 55 |
  | flannel shirt | 0.34 | base or mid | ASHRAE 55 |
  | thin sweater / cardigan | 0.25 | mid | ASHRAE 55 |
  | thick sweater / hoodie | 0.36 | mid | ASHRAE 55 |
  | fleece | 0.30 | mid | practice-calibrated |
  | blazer / suit jacket | 0.36 | mid | ASHRAE 55 |
  | rain shell (unlined) | 0.25 | outer | practice-calibrated |
  | light jacket | 0.40 | outer | practice-calibrated |
  | wool coat | 0.60 | outer | practice-calibrated (ISO 9920-consistent) |
  | parka / down jacket | 0.70 | outer | ISO 9920-consistent |
  | thermal top / bottoms | 0.20 / 0.15 | mid / leg_base | ASHRAE 55 (long underwear) |
  | thin trousers / thick trousers / jeans | 0.15 / 0.24 / 0.24 | bottom | ASHRAE 55 |
  | shorts | 0.08 | bottom | ASHRAE 55 |
  | skirt (thin/thick) | 0.14 / 0.23 | bottom | ASHRAE 55 |
  | dress (light/long-sleeve) | 0.23 / 0.33 | full_body | ASHRAE 55 |
  | shoes / sneakers / sandals / boots | 0.02 / 0.02 / 0.02 / 0.10 | footwear | ASHRAE 55 |
  | socks (ankle/calf) | 0.02 / 0.03 | (folded into footwear clo) | ASHRAE 55 |
  | hat / gloves / scarf / umbrella / sunglasses | 0.0 (advisory) | accessory | see D6 |

  "Practice-calibrated" rows are outerwear values consistent with ISO 9920
  ensemble tables where ASHRAE's garment list is sparse; they are pinned by
  the eval golden table so they cannot drift silently.
- **D2 — Ensemble insulation uses the ASHRAE summation regression.**
  `Icl = 0.835 × Σ Iclu,i + 0.161` clo (McCullough, Jones & Huck's
  regression as adopted in ASHRAE Fundamentals / ISO 9920), which corrects
  the simple sum for garment overlap and compression; defined as 0 for the
  empty set (the regression's intercept models a minimal clothed body, not
  bare skin). Assumption: the regression is accurate enough (±0.1 clo)
  across our ensembles; evals pin hand-computed cases.
- **D3 — Required insulation from a heat-balance-lite model.**
  `required_clo(T_feels, met) = (34 − T_feels)/(7.66·met) − 0.7`, clamped
  to [0, 4.5]. Derivation, in the spirit of ISO 11079's IREQ (required
  clothing insulation) and Fanger's comfort equation (ISO 7730): required
  total insulation ≈ (T_skin − T_air)/(0.155 · H_dry), with comfort mean
  skin temperature 34 °C (Fanger's regression at ~1 met), metabolic heat
  58.15 W/m² per met (ASHRAE definition), a 0.85 factor for the fraction
  of metabolic heat lost as dry heat through clothing (the rest is
  respiratory + evaporative), hence the constant 0.155 × 58.15 × 0.85 ≈
  7.66; subtracting the still-air boundary-layer insulation Ia ≈ 0.7 clo
  (ISO 9920) converts total to intrinsic clothing insulation. Sanity
  anchor: at 21 °C and met 1.0 the formula returns 0.997 ≈ 1.0 clo — the
  *definition* of the clo — and at 22 °C / met 1.1 it returns ≈ 0.7 clo,
  matching ASHRAE 55's canonical winter-clothing operating point. Default
  met = 1.6 (assumption: a mixed urban wear-window of walking ~2.0 met and
  standing/transit ~1.2 met; ASHRAE met table). All four constants are
  named calibration constants pinned by EVALS.md M1.
- **D4 — Feels-like uses the operational formulas, and wind acts through
  it exactly once.** Wind chill: the 2001 JAG/TI formula adopted by the US
  NWS and Environment Canada (validity T ≤ 10 °C, v > 4.8 km/h). Heat:
  Steadman's (1984) apparent temperature as used by the Australian Bureau
  of Meteorology (non-radiant form), applied at T ≥ 26 °C. In between,
  air temperature stands. To avoid double-counting wind we do *not* also
  modulate the D3 boundary-layer term with wind; instead a windproof
  outermost layer attenuates the wind-chill input wind ({1.0, 0.6, 0.3}
  for windproofness 0/1/2 — calibration constants reflecting that laminate
  shells largely stop convective stripping of the boundary layer). This
  makes required clo config-dependent, which is exactly the real
  phenomenon ("windbreaker weather").
- **D5 — Comfort band ±0.25 clo, hour score linear to zero at ±1.0.**
  ±0.25 clo ≈ ±3.1 °C at met 1.6 (via D3's slope 1/(7.66·met)). Grounding:
  ASHRAE 55's indoor comfort zone is roughly ±1.5–2 °C (≈ ±0.2 clo) for a
  given ensemble; we widen slightly for outdoor, transient exposure per
  the adaptive-comfort literature (de Dear & Brager's adaptive model,
  adopted as ASHRAE 55's adaptive method) — people tolerate more swing
  outdoors, where exposure is minutes not hours. |dev| = 1.0 clo (≈ 12 °C
  mismatch at met 1.6) scores zero: that is coat-missing-in-winter
  territory. Deviation is signed in reports (too warm vs too cold) but
  scored symmetrically; asymmetric discomfort weighting is a possible
  refinement once wear feedback exists.
- **D6 — Slots follow the mountaineers' three-layer doctrine.** Base
  (moisture/next-to-skin), mid (insulation), shell/outer (wind + rain) —
  the layering system as codified in *Mountaineering: The Freedom of the
  Hills* and every outdoor-retailer layering guide — extended with bottom,
  leg base, footwear, full-body (dress), and accessory roles to cover a
  whole wardrobe. Configurations model what people actually do (carry the
  coat, tie the sweater): outer independently on/off, mids shed
  outermost-first (LIFO by clo descending — thickest worn outermost).
  Accessories contribute advisory value, not ensemble clo: their tabulated
  insulation (~0.02–0.05) is below the model's resolution, but their
  *protective* role (rain, UV, extremities) is rule-relevant, so they enter
  FR-10, not FR-8. Assumption: 0–2 mids suffice for a personal wardrobe's
  realistic stacks (base + 2 mids + shell covers ~2.0+ clo ensembles).
- **D7 — Precipitation semantics follow NWS PoP language, WMO/Met Office
  intensity classes, and hydrostatic-head practice.** Probability: NWS
  categorical PoP language puts "likely" at ≥ 60% and "chance" at 30–50%;
  we draw the hard line at 0.5 (miss a "likely" rain and the product has
  failed; 0.3–0.49 is a soft penalty). Intensity: light < 2.5 mm/h,
  moderate 2.5–10, heavy > 10 (WMO / UK Met Office rain-intensity
  definitions). Waterproofness ordinal ↔ industry hydrostatic-head ratings
  (ISO 811 test): 0 = untreated; 1 ≈ DWR/water-resistant (~1,500 mm,
  shower-proof); 2 ≈ waterproof taped shell (≥ 5,000 mm); 3 ≈ storm shell
  (≥ 10,000 mm). Umbrellas: valid cover for light/moderate rain but not
  heavy rain nor wind ≥ 35 km/h (≈ Beaufort 5 "fresh breeze", where
  umbrella use becomes difficult — practice-based constant).
- **D8 — Color harmony from the Itten wheel plus the stylists' neutral
  doctrine.** Each color is (name, hue 0–360 or null, neutral flag). The
  neutral set follows menswear/capsule-wardrobe practice (the capsule
  concept popularized by Susie Faux and echoed in every wardrobe guide):
  black, white, gray, navy, beige/tan/khaki, denim-blue, olive — neutrals
  pair with anything (score 1.0). Non-neutral pairs score by circular hue
  distance Δh on Johannes Itten's 12-hue wheel (*The Art of Color*):
  Δh ≤ 15 monochromatic 0.90; 15–45 analogous 0.85; 45–105 clash 0.35;
  105–150 triadic-zone 0.70; 150–180 complementary 0.80. The "no more than
  three colors" rule of thumb becomes: > 3 distinct non-neutral 30° hue
  families → −0.15 on `S_color`. Known limitation (accepted): no
  lightness/saturation axis, so "two slightly different reds" lands in the
  monochromatic bucket; the golden eval set (EVALS.md M5) therefore tests
  hue-level judgments only.
- **D9 — Formality is a 5-step ladder mapped to dress codes.** 1 =
  athleisure/beach, 2 = casual, 3 = smart casual, 4 = business casual/
  business, 5 = formal — the conventional dress-code ladder. Hard rule
  HC-4 (spread ≤ 1) encodes the classic mismatch failure (running shoes
  with suit trousers spans 1↔4). Occasions are a separate, user-owned tag
  list on each garment (HC-3 filters on it); formality enforces *internal*
  coherence, occasions enforce *external* fit. Default occasion
  vocabulary: casual, work, sport, outdoor, formal (config-extensible).
- **D10 — Variety is exponential recency decay with a hard yesterday
  block.** Wearing an item today halves its penalty every 3 days
  (half-life constant, assumption tuned for a ~50–150 item wardrobe);
  identical core sets on consecutive days are blocked outright (HC-8) —
  the owner's stated requirement "don't repeat yesterday" is a constraint,
  not a preference. Utilization (whole closet gets used) is measured in
  the 14-day rollout eval rather than optimized directly — recency decay
  plus MMR diversity is the mechanism; the eval verifies it suffices.
- **D11 — Laundry thresholds follow garment-care guidance.** Default
  `wears_before_laundry` by category: tees/base layers 1–2, shirts 2,
  knitwear 5, jeans/trousers 5, shorts/skirts 3, outerwear 30, footwear/
  accessories uncounted (999) — consistent with American Cleaning
  Institute-style wash-frequency guidance (underlayers every wear, jeans
  ~5 wears, sweaters ~5, outerwear seasonally). Auto-dirty at threshold is
  deliberate: a tool that recommends a shirt already worn twice silently
  loses trust. Manual override always available (FR-4).
- **D12 — Assembly is filter → bounded enumeration → scalarized scoring →
  MMR, not ILP and not learned.** Hard constraints are filters
  (constraint-satisfaction), soft qualities are a weighted scalarization —
  the standard multi-criteria pattern that keeps every outfit's score
  explainable component-by-component (a must for FR-13 reasoning).
  Search: per-slot candidate lists (occasion + cleanliness + role filtered)
  are capped at 40 per slot when larger (kept by slot-local thermal
  plausibility: |clo − slot target| ascending, id tie-break; slot targets
  derived from the day's required-clo range); enumeration over base ×
  bottom × footwear × mid-subsets (≤ 2) × outer proceeds with an
  admissible optimistic bound (thermal component bounded by best-case
  config coverage; other components bounded by 1.0) pruning partial
  outfits that cannot beat the current k-th best. Wardrobe-scale math: a
  large personal wardrobe (~30 bases × 20 bottoms × 8 shoes × 16
  mid-subsets × 8 outers ≈ 600k raw) reduces under caps and pruning to
  ≪ 100k full evaluations, each over ≤ 16 hours × ≤ 6 configs — well
  under a second in practice; the eval suite brute-forces the small
  fixture wardrobe exactly to verify the pruned search loses ≤ 3% quality
  (EVALS.md M2b). Diversity: greedy maximal-marginal-relevance-style
  selection (Carbonell & Goldstein 1998) with a Jaccard ≤ 0.5 overlap cap
  between returned outfits. Determinism: no randomness in the core path;
  ties break on (score desc, sorted garment-id tuple asc); `seed` is
  reserved for the optional `--surprise` jitter (post-MVP) and recorded
  for API stability.
- **D13 — Objective weights: thermal 0.40, protection 0.15, color 0.15,
  style 0.15, variety 0.15.** Thermal dominates because being cold or wet
  is the failure the product exists to prevent (and protection's hard core
  is already a constraint, HC-6); the four remaining qualities are
  deliberately equal — with a personal wardrobe the data cannot justify
  finer distinctions yet. Weights are engine constants in `models.py`,
  overridable per request (recorded in params), and the eval gates must
  hold at the defaults.
- **D14 — Offline-first adapters, snapshots, and human-in-the-loop
  extraction.** Weather: fixture provider is the default; Open-Meteo
  chosen for the live adapter because it is free, keyless, and serves all
  required hourly variables — activation is an explicit opt-in
  (`DRESSCAST_LIVE_WEATHER=1` + configured location), satisfying
  CONVENTIONS.md §3 even though no credential exists. Every fetch is
  snapshotted append-only so recommendations are reproducible after the
  fact. Extraction: suggestions are always staged and human-confirmed
  (FR-2) — a mistagged clo poisons every future recommendation, so no
  adapter writes garment attributes directly.
- **D15 — Units and time.** SI internally: °C, km/h wind (converted to m/s
  inside Steadman's AT), mm/h precipitation, hours 0–23 local to the
  configured timezone. °F is a CLI display conversion only (FR-17). Time
  is always an input; the engine is clock-free (FR-18); default wear
  window 07:00–22:00 (assumption: covers commute-to-evening for a typical
  day; configurable per request).
- **D16 — Trust boundaries.** No likeness/finance/health surface: garment
  photos are the owner's own property, stay on local disk, and are never
  transmitted by offline adapters; the live vision extractor (next phase)
  must document exactly what leaves the machine before it can be enabled.
