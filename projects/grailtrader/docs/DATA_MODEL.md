# GrailTrader — Data Model

Two storage classes, per workspace conventions:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/grailtrader/data/`). `grailtrader init` validates all five and
  **materializes only `brands.json`** into the `brand` / `designer_era`
  tables (they are joined and foreign-keyed against). The other four —
  `impact_priors.json`, `conditions.json`, `advice_templates.json`,
  `advisor_config.json` — are validated at init and then **read from file at
  process start and cached in memory**; they have no tables. A stale DB is
  therefore impossible for priors/config: editing a file and re-running the
  engine takes effect immediately, while editing `brands.json` requires
  `init` to re-materialize.
- **SQLite** (user + pipeline state, default `~/.grailtrader/grailtrader.db`,
  path via `GRAILTRADER_DB`; in-memory backend for tests): listings, index
  points, fashion events, garments, advice, backtest runs/results.
  Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/grailtrader/models.py`; the store maps
them to the tables below. Enumerations are Python `StrEnum`s; SQLite stores
their string values. Timestamps are ISO-8601 UTC strings supplied by callers
(the engine never reads the clock); week keys are the ISO date of the week's
Monday (`2026-07-27`). Money is USD, stored as REAL > 0. JSON-typed columns
hold canonical (sorted-keys, compact, `separators=(",",":")`)
serializations so byte-identity is well defined.

## Stratum ids (not a table)

Strata are derived path strings over gazetteer keys:

```
brand                      e.g.  helmut-lang
brand/era                  e.g.  helmut-lang/helmut
brand/era/category (leaf)  e.g.  helmut-lang/helmut/outerwear
```

The era segment is the era id's suffix after the colon
(`helmut-lang:helmut` → `helmut`). Prefix relationships define the hierarchy
(FR-4 parent chaining, FR-5 event scoping, FR-7/FR-8 stratum fallback).
Validity: each segment must exist in the gazetteer and the era must belong to
the brand (checked wherever a stratum is constructed).

## Id scheme (FR-14)

All primary ids are content-derived: `hex16(x) = sha256(x.encode())[:16]` in
hex, over a canonical natural-key string built by joining fields with `|`.
**Money inside an id key is serialized as integer cents**
(`int(round(usd * 100))`) — no float repr ever enters a hash, so replays are
byte-identical across platforms. Absent optional fields serialize as the
empty string.

| Entity | Id derivation |
|---|---|
| Listing | `hex16("listing|" + source + "|" + external_id)` |
| FashionEvent | `hex16("event|" + event_type + "|" + brand_id + "|" + (era_id or "") + "|" + iso_week_monday(occurred_on) + "|" + identity_attrs_json)` |
| Garment | `hex16("garment|" + stratum_path + "|" + anchor_date + "|" + anchor_price_cents + "|" + added_at)` |
| Advice | `hex16("advice|" + garment_id + "|" + as_of_week + "|" + inputs_hash)` |
| BacktestRun | `hex16("bt|" + canonical params JSON + "|" + as_of)` |
| IndexPoint | natural composite key `(stratum_id, week)` |

`identity_attrs_json` is the canonical JSON of the *factual* discriminators
for the event type only — `{"designer": …}` for `designer_appointment`,
`{"counterparty": …}` for `collab_announcement`, `{"celebrity": …}` for
`celebrity_cosign`, and `{}` for `designer_departure`, `runway_reception`,
`brand_scandal`. Judgement attributes (`reason`, `acclaim`, `severity`,
`polarity`) and `source_ref` are deliberately **excluded** so that the same
real-world event reported by two feeds — which may disagree on "resigned" vs
"ousted" — resolves to one row and bumps `corroboration` instead of creating
a second row whose impact would be double-counted (FR-5, SCOPE D-20).

`inputs_hash = hex16(canonical_json({"events": [[event_id, event_week,
corroboration], …] sorted by event_id, "stratum": stratum_id,
"index_built_as_of": …, "config_version": …}))` — see `advice` below.

## Enumerations

| Enum | Values |
|---|---|
| `Category` | `outerwear`, `knitwear`, `tops`, `bottoms`, `denim`, `footwear`, `tailoring`, `accessories` |
| `ConditionGrade` | `new`, `excellent`, `good`, `fair`, `poor` |
| `EventType` | `designer_departure`, `designer_appointment`, `collab_announcement`, `celebrity_cosign`, `runway_reception`, `brand_scandal` |
| `DepartureReason` | `resignation`, `ousted`, `death`, `house_closure` |
| `Acclaim` | `acclaimed`, `neutral`, `unproven` |
| `CelebrityTier` | `a_list`, `b_list`, `niche` |
| `RunwayPolarity` | `acclaimed`, `panned` |
| `ScandalSeverity` | `minor`, `moderate`, `severe` |
| `EventSource` | `news`, `social`, `manual` |
| `EventStatus` | `pending`, `confirmed`, `rejected` |
| `ListingSource` | `fixture`, `csv` |
| `ListingStatus` | `sold`, `active` |
| `GarmentStatus` | `owned`, `watching`, `sold_archived` |
| `AdviceAction` | `buy`, `sell`, `hold` |
| `ValuationMethod` | `repeat_sales`, `comp_based`, `unavailable` |
| `ExclusionReason` (backtest) | `out_of_window`, `insufficient_future_index`, `stale_stratum`, `no_index` |

Hold reason codes (strings in `advice.rationale_codes`, all seven reachable
and test-covered): `hold:no_index`, `hold:stale_index`,
`hold:insufficient_history`, `hold:no_active_events`, `hold:no_baseline`,
`hold:below_threshold`, `hold:low_confidence`.

## Committed datasets

### Brand / DesignerEra — `data/brands.json` → tables `brand`, `designer_era`

| Brand field | Type | Notes |
|---|---|---|
| `id` | str PK | slug; one per label/line (`dior-homme` is distinct from `dior`) |
| `name` | str | display name |
| `aliases` | list[str] | matching aid for CSV import queries; unique across gazetteer (checked at init) |
| `notes` | str | archetype/context for the maintainer |

| DesignerEra field | Type | Notes |
|---|---|---|
| `id` | str PK | `brand:designer-slug`, e.g. `celine:philo` |
| `brand_id` | str FK → brand | |
| `designer` | str | person's name |
| `label` | str | display, e.g. "Phoebe Philo era" |
| `start` | str | ISO month `2008-10` |
| `end` | str \| null | null = current/open era |
| `notes` | str | |

Invariants (checked at init): era ids unique; within a brand, eras are
chronologically ordered and non-overlapping with at most one open era;
every brand has ≥ 1 era (founder-led single-era brands get one open
founder era). ~25 real brands / ~55 eras committed with factual tenure
dates (e.g. Helmut Lang 1986–2005; Dior Homme: Slimane 2000–2007,
Van Assche 2007–2018, Jones 2018–; Céline: Philo 2008–2018, Slimane 2018–;
Off-White: Abloh 2013–2021; Number (N)ine: Miyashita 1996–2009). Editing
this file (then `init`) is the supported way to extend the universe.

```json
{"id": "helmut-lang", "name": "Helmut Lang",
 "aliases": ["Helmut Lang", "HL"],
 "notes": "Founder-era archive brand; founder exited 2005; canonical archive premium case.",
 "eras": [
   {"id": "helmut-lang:helmut", "designer": "Helmut Lang", "label": "Helmut Lang era",
    "start": "1986-01", "end": "2005-01", "notes": "the archive era"},
   {"id": "helmut-lang:post", "designer": "various", "label": "post-founder",
    "start": "2005-02", "end": null, "notes": ""}]}
```

### ImpactPrior — `data/impact_priors.json` (validated at init; read from file; no table)

| Field | Type | Notes |
|---|---|---|
| `key` | str PK | `event_type.qualifier[.target]`, e.g. `designer_departure.death`, `brand_scandal.moderate`, `designer_appointment.acclaimed.brand`, `designer_appointment.*.predecessor_era`, `celebrity_cosign.*`, `runway_reception.panned` |
| `target` | str | scope selector: `era`, `brand`, `predecessor_era`, `given` (co-sign: most specific stratum supplied) |
| `direction` | str | `bullish` \| `bearish` (sign check against `permanent_pct`/`transient_pct`, at init) |
| `permanent_pct` | float | signed permanent component `P` (e.g. `0.12`) |
| `transient_pct` | float | signed transient component `T` at age 0 |
| `half_life_weeks` | float | `h` for the transient's geometric decay |
| `base_conf` | float | 0.05–0.95; feeds `c_event` (FR-8) |
| `rationale` | str | plain-language base-rate text, rendered verbatim into advice `drivers` |
| `source_note` | str | real-world anchor |

Invariants: every (event type × attribute combination reachable from the
FR-5 typology) resolves to exactly one prior per target; `|P| ≤ 0.35`,
`|T| ≤ 0.40`, `1 ≤ h ≤ 26` (sanity bounds, checked at init).

Committed `base_conf` values (the full ladder; SCOPE typology table repeats
the summary). Because `conf ≤ c_event ≤ max_e base_conf_e` and `conf_min =
0.55`, only the rows at 0.58 and above can produce actionable advice on
their own — intended (FR-8):

| key | P | T | h | base_conf |
|---|---|---|---|---|
| `designer_departure.resignation` | +0.12 | +0.10 | 8 | 0.72 |
| `designer_departure.ousted` | +0.12 | +0.14 | 7 | 0.70 |
| `designer_departure.death` | +0.20 | +0.25 | 6 | 0.78 |
| `designer_departure.house_closure` | +0.18 | +0.12 | 10 | 0.75 |
| `designer_appointment.acclaimed.brand` | +0.06 | +0.08 | 8 | 0.62 |
| `designer_appointment.neutral.brand` | +0.02 | +0.04 | 8 | 0.52 |
| `designer_appointment.unproven.brand` | 0.00 | +0.03 | 8 | 0.46 |
| `designer_appointment.*.predecessor_era` | +0.06 | +0.04 | 8 | 0.64 |
| `collab_announcement.*` | +0.02 | +0.10 | 4 | 0.60 |
| `celebrity_cosign.*` | 0.00 | +0.10 ·tier | 3 | 0.50 |
| `runway_reception.acclaimed` | +0.02 | +0.05 | 6 | 0.52 |
| `runway_reception.panned` | 0.00 | −0.04 | 6 | 0.50 |
| `brand_scandal.minor` | −0.05 | −0.05 | 6 | 0.58 |
| `brand_scandal.moderate` | −0.10 | −0.10 | 8 | 0.68 |
| `brand_scandal.severe` | −0.15 | −0.15 | 10 | 0.74 |

```json
{"key": "designer_departure.death", "target": "era", "direction": "bullish",
 "permanent_pct": 0.20, "transient_pct": 0.25, "half_life_weeks": 6.0,
 "base_conf": 0.78,
 "rationale": "When a designer dies, their era's supply is permanently finite and demand spikes; resale prices have jumped sharply within days and partially retraced over the following weeks.",
 "source_note": "StockX resale data on Off-White and Louis Vuitton x Nike after Virgil Abloh's death, Nov 2021: double-digit price spikes within days, partial decay over weeks."}
```

```json
{"key": "celebrity_cosign.*", "target": "given", "direction": "bullish",
 "permanent_pct": 0.0, "transient_pct": 0.10, "half_life_weeks": 3.0,
 "base_conf": 0.50,
 "rationale": "A high-profile celebrity moment lifts search and resale demand for the worn brand/era quickly, and the effect fades within weeks unless reinforced.",
 "source_note": "Lyst Index methodology (brand heat from search/social moments); Lyst/Depop-reported search spikes for vintage Jean Paul Gaultier during the Bella Hadid-driven revival. Tier scaling a_list 1.0 / b_list 0.5 / niche 0.25 applied to T. base_conf sits below conf_min by design: a co-sign is context, not a trade."}
```

### ConditionGrade — `data/conditions.json` (validated at init; read from file; no table)

| Field | Type | Notes |
|---|---|---|
| `grade` | ConditionGrade PK | |
| `multiplier` | float | vs `excellent` = 1.00; defaults: new 1.25, excellent 1.00, good 0.80, fair 0.55, poor 0.35; invariant: strictly decreasing |
| `platform_aliases` | list[str] | case-insensitive labels mapped at ingest, e.g. `new` ← "New/Never Worn", "Never worn with tag", "NWT", "Deadstock"; `excellent` ← "Gently Used", "Never worn", "Excellent" |

Invariant: aliases unique across grades (an ingest label maps to exactly
one grade); unmapped labels are rejected at ingest with the label named
(FR-2), never guessed.

### AdviceTemplate — `data/advice_templates.json` (validated at init; read from file; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `buy-event-driven`, `sell-into-spike`, `hold-stale-index`, `hold-quiet`, `hold-low-confidence` |
| `action` | AdviceAction | |
| `headline` | str | format string, e.g. `Consider buying: {garment_label} — modeled {expected_move} over {horizon_weeks} weeks` |
| `drivers` | str | format string; must include `{driver_lines}` (one per active event: label, age, scope-dilution λ, prior rationale) |
| `valuation_context` | str | must include `{fair_value}`, `{valuation_method}`, and `{level_usd}` (or the explicit unavailable-reason) |
| `uncertainty` | str | must include `{confidence}` and `{falsifier}` (e.g. "weakens if the market has already fully priced this event") |
| `fee_note` | str | must include `{fee_assumption_pct}` |

Top-level file also carries `forbidden_lexicon` (["guaranteed", "can't
lose", "sure thing", "risk-free", "will definitely", "easy money", …]) and
`footer` (single constant, rendered verbatim: "GrailTrader models a
collectibles market — unregulated, illiquid (sales take weeks to months),
with authenticity risk. This is information about that model, not
investment advice."). Invariants at init: every (action × hold-reason
family) resolvable; all sections non-empty; no forbidden word in any
template.

**Quoting rule (FR-9).** Every user-supplied string interpolated into a
render (`garment.label`, `garment.notes`, `event.notes`, `listing.title`) is
wrapped in typographic quotes `“…”` by the renderer and is exempt from the
forbidden-lexicon scan inside those quotes. The scan therefore runs over the
rendered text with quoted spans masked out. This is what makes a real
Grailed title such as *“guaranteed authentic Helmut Lang”* renderable rather
than blocked — over-blocking is a compliance failure (EVALS M5b), not
caution.

### AdvisorConfig — `data/advisor_config.json` (validated at init; read from file; no table)

Single validated document; all FR-4/FR-6/FR-8 constants live here, not in
code:

```json
{"config_version": "1.0.0",
 "index": {"window_weeks": 4, "min_sales": 5, "fence_sigma": 3.5,
           "fence_floor_log": 0.7885, "stale_max_weeks": 8,
           "parent_weight_window_weeks": 8},
 "advisor": {"horizons_weeks": [4, 12, 26],
             "theta_buy": 0.12, "theta_sell": 0.12,
             "fee_assumption_pct": 0.12,
             "conf_min": 0.55, "conf_floor": 0.05, "conf_cap": 0.95,
             "z_half": 0.8, "sigma_min_changes": 12,
             "active_max_weeks": 26, "active_transient_min": 0.005,
             "q_index": [{"max_stale_weeks": 0, "factor": 1.0},
                         {"max_stale_weeks": 2, "factor": 0.8},
                         {"max_stale_weeks": 8, "factor": 0.5}],
             "source_factor": {"manual": 1.0, "news": 0.95, "social": 0.8},
             "corroboration_step": 0.05, "corroboration_max_steps": 2},
 "calibration": {"bucket_edges": [0.45, 0.70]}}
```

Init-time range checks: `fence_floor_log = ln 2.2 = 0.7885`;
`0 < theta ≤ 0.5`; **`theta_buy == theta_sell == fee_assumption_pct`**
(SCOPE D-12 coherence — a config where the advice threshold is below the fee
it quotes is rejected); horizons strictly ascending; `q_index` entries
ascending in `max_stale_weeks` with the last equal to `stale_max_weeks`,
factors non-increasing; `conf_floor < conf_min < conf_cap`; `bucket_edges`
ascending and strictly inside `(conf_floor, conf_cap)`.

## SQLite entities

### Listing — `listing` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived (id scheme) |
| `source` | ListingSource | `fixture` \| `csv` |
| `external_id` | str | platform id or CSV row key; unique with `source` |
| `brand_id` | str FK → brand | |
| `era_id` | str FK → designer_era | must belong to `brand_id` (invariant) |
| `category` | Category | |
| `condition` | ConditionGrade | mapped at ingest from `platform_label` |
| `platform_label` | str | raw label as received (provenance) |
| `size` | str \| null | stored, never indexed on |
| `title` | str \| null | raw listing title (provenance; quoted-only in renders) |
| `status` | ListingStatus | |
| `listed_at` | str | ISO UTC |
| `sold_at` | str \| null | required iff `status = sold`; `≥ listed_at` (CHECK) |
| `ask_price` | float \| null | required iff `status = active`; diagnostics only — never enters the index |
| `sold_price` | float \| null | required iff `status = sold`; > 0 |
| `currency` | str | `USD` only (CHECK; others rejected at ingest) |

Invariants: append-only; unique (`source`, `external_id`); sold listings
immutable. Outlier status is *not* stored here — the FR-3 fence is applied
per index build and recorded on `index_point.n_excluded` (a listing can be
an outlier in one window and not another as the window median moves).

```json
{"id": "3e1f9a02bc44d715", "source": "csv", "external_id": "grailed-31882045",
 "brand_id": "helmut-lang", "era_id": "helmut-lang:helmut",
 "category": "outerwear", "condition": "excellent",
 "platform_label": "Gently Used", "size": "48",
 "title": "Helmut Lang AW99 astro moto leather jacket",
 "status": "sold", "listed_at": "2026-06-02T14:00:00Z",
 "sold_at": "2026-07-18T09:30:00Z", "ask_price": 1200.0,
 "sold_price": 950.0, "currency": "USD"}
```

### IndexPoint — `index_point` (derived; rebuilt idempotently)

| Field | Type | Notes |
|---|---|---|
| `stratum_id` | str PK-part | path string (leaf, era, or brand level) |
| `week` | str PK-part | ISO Monday date; unique (stratum_id, week) |
| `level_usd` | float \| null | leaf strata only: window median of condition-adjusted sold prices; null for parent strata (invariant) |
| `index_value` | float | base 100 at the stratum's first eligible week; > 0 |
| `n_sales` | int | leaves: surviving sales in the window (≥ `min_sales`); parents: sum over the children that contributed to the chain step |
| `n_excluded` | int | FR-3 fence exclusions in window (leaves; always 0 for parents) |
| `built_as_of` | str | the `index build` as_of; also an input to `advice.inputs_hash` |

Invariants: a week with insufficient window sales simply has no row
(staleness is computed at query time as distance to the last row ≤ t —
never fabricated); parents are **chain-linked** per FR-4, so a child
becoming eligible never produces a step in the parent (unit-tested);
rebuild with identical inputs is byte-identical; rebuild with new listings
replaces derived rows wholesale (the only non-append-only table, and it is
purely derived).

```json
{"stratum_id": "maison-vantorre/vantorre/outerwear", "week": "2026-07-27",
 "level_usd": 1180.0, "index_value": 125.0, "n_sales": 27, "n_excluded": 1,
 "built_as_of": "2026-07-31T08:00:00Z"}
```

### FashionEvent — `fashion_event` (append-only; status/corroboration updates only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived; **excludes** `source_ref` and judgement attributes (id scheme) |
| `event_type` | EventType | |
| `brand_id` | str FK → brand | |
| `era_id` | str FK \| null | required for `designer_departure` (the closing era) and optional for `celebrity_cosign`; must belong to brand |
| `attributes` | JSON | typed per event type: `{reason}`, `{designer, acclaim}`, `{counterparty, counterparty_brand_id?}`, `{celebrity, tier, category?}`, `{polarity}`, `{severity}`; unknown keys rejected. First-seen wins on judgement attributes when a second feed reports the same event |
| `occurred_on` | str | ISO date, first-seen wins; the event's week = ISO Monday of the week containing it (that week is what the id keys on) |
| `source` | EventSource | source of the first sighting |
| `source_refs` | JSON list[str] | every feed URL/guid or `manual:<slug>` seen for this id, insertion-ordered, deduplicated |
| `status` | EventStatus | fixture/manual → `confirmed`; RSS candidates → `pending` |
| `corroboration` | int | ≥ 1; the number of **distinct registrable domains** among `source_refs` (a `manual:` ref counts as the domain `manual`) |
| `notes` | str | free text (quoted-only in renders) |

Invariants: append-only except the `pending → confirmed|rejected`
transition, `source_refs` appends and the derived `corroboration` recount;
`pending`/`rejected` events never affect impacts, advice, or backtests
(enforced at query layer, covered by tests); attribute schema per type
validated at write. **Duplicate-feed invariant (FR-5):** ingesting the same
real-world event from a second feed must leave `Σ_e m_e` on every stratum
unchanged while raising `corroboration` — a dedicated test asserts both.

Resolved target strata and the retirement age `A_e` are computed by FR-5/FR-6,
not stored (pure functions of the event + gazetteer + priors).

```json
{"id": "b7c31d90aa25e4f8", "event_type": "designer_departure",
 "brand_id": "maison-vantorre", "era_id": "maison-vantorre:vantorre",
 "attributes": {"reason": "resignation"},
 "occurred_on": "2026-06-29", "source": "manual",
 "source_refs": ["manual:vantorre-departure"], "status": "confirmed",
 "corroboration": 1, "notes": "Founder announced exit effective FW26."}
```

(Fictional brand by design — fixture and doc examples never attach
scandal/death/departure events to real brands or people; SCOPE D-14.)

### Garment — `garment`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived from `(stratum_path, anchor_date, anchor_price_cents, added_at)` only — no editable field is in the key |
| `label` | str | user's name for the piece ("HL astro moto jacket"); **editable** |
| `brand_id` | str FK | |
| `era_id` | str FK | must belong to brand |
| `category` | Category | |
| `condition` | ConditionGrade | current grade; **editable** (FR-7 rescales fair value by the multiplier ratio vs `anchor_condition`) |
| `anchor_condition` | ConditionGrade | grade at entry; immutable |
| `size` | str \| null | editable |
| `status` | GarmentStatus | `owned` \| `watching` \| `sold_archived` |
| `acquisition_price`, `acquired_on` | float \| null, str \| null | both set iff `status ∈ {owned, sold_archived}` (CHECK); price > 0 |
| `reference_price`, `reference_date` | float \| null, str \| null | both set iff `status = watching` (CHECK); price > 0 — "the price I saw and the day I saw it" |
| `disposed_price`, `disposed_on` | float \| null, str \| null | both set iff `status = sold_archived` (CHECK) |
| `added_at` | str | edge-supplied; part of the id natural key |
| `deleted_at` | str \| null | soft delete (FR-11); non-null rows are excluded from listings, valuation and `advise` |
| `notes` | str | editable |

Derived, not stored: `stratum_path = brand/era/category`; the **anchor pair**
`(anchor_price, anchor_date) = (acquisition_price, acquired_on)` for
`owned`/`sold_archived` and `(reference_price, reference_date)` for
`watching`. FR-7 valuation and the id both use the anchor pair, so the two
statuses need no special-casing downstream.

Editable fields: `label`, `condition`, `size`, `notes`, `status` (+ the
matching price/date pair). Brand / era / category / anchor pair / `added_at`
are immutable — they define the id; a mis-entered garment is soft-deleted
and re-added.

```json
{"id": "9ab04c11de77f203", "label": "HL astro moto jacket",
 "brand_id": "helmut-lang", "era_id": "helmut-lang:helmut",
 "category": "outerwear", "condition": "excellent",
 "anchor_condition": "excellent", "size": "48",
 "status": "owned", "acquisition_price": 840.0, "acquired_on": "2024-03-06",
 "reference_price": null, "reference_date": null,
 "disposed_price": null, "disposed_on": null,
 "added_at": "2026-07-31T08:00:00Z", "deleted_at": null,
 "notes": "bought on Grailed"}
```

(Valuation check per FR-7 method 1: index `helmut-lang/helmut/outerwear` was
118.4 in the carried anchor week 2024-03-04 and 141.2 in week 2026-07-27,
condition unchanged → fair value = 840 × 141.2 / 118.4 = **$1,001.76**,
`valuation_method = repeat_sales`. Had the index not reached back to 2024,
method 2 would give `level_usd(leaf, 2026-07-27) × multiplier[excellent]`,
labeled `comp_based`. Example records must stay formula-verifiable.)

### Advice — `advice` (immutable, append-only, superseding)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `hex16("advice|" + garment_id + "|" + as_of_week + "|" + inputs_hash)` |
| `garment_id` | str | not an SQL FK (see deletion policy) |
| `as_of_week` | str | ISO Monday |
| `inputs_hash` | str | hash of the exact decision inputs (id scheme): active confirmed event ids + weeks + corroboration, `stratum_id`, index `built_as_of`, `config_version` |
| `config_version` | str | copied from `advisor_config.json` |
| `stratum_id` | str | the stratum actually used (most specific with a point within `stale_max_weeks`) |
| `action` | AdviceAction | |
| `is_candidate` | bool | `|expected_return| ≥ theta` — true for buy/sell and for `hold:low_confidence`; the population EVALS M3 calibrates over |
| `horizon_weeks` | int | H* chosen by argmax z (FR-8) — an **output**, never part of the key |
| `expected_return` | float \| null | r̂ at H* (signed fraction); null only for holds with no computable r̂ |
| `confidence` | float \| null | CHECK `confidence IS NULL OR 0.05 ≤ confidence ≤ 0.95` |
| `fair_value` | float \| null | FR-7 value at as_of |
| `fair_value_method` | ValuationMethod | `repeat_sales` \| `comp_based` \| `unavailable` |
| `rationale_codes` | JSON list[str] | e.g. `["driver:event:b7c31d90aa25e4f8", "prior:designer_departure.resignation", "mod:lambda=1.00@b7c31d90aa25e4f8", "mod:z=2.90@h4", "mod:conf_event=0.72", "mod:q_index=1.0", "mod:src=manual"]`; holds carry exactly one `hold:<reason>` code |
| `rendered_text` | str | full rendering incl. footer |
| `frame_checked` | bool | CHECK `frame_checked = 1` — a failing advice cannot exist in the store (FR-9) |
| `created_as_of` | str | ISO UTC; the row with the greatest value per `(garment_id, as_of_week)` is the **current** advice |

Invariants: immutable and append-only; unique on the id, i.e. on
`(garment_id, as_of_week, inputs_hash)`. Re-running `advise` with unchanged
inputs recomputes the same id and is a byte-identical no-op; re-running
after a new confirmed event changes `inputs_hash` and writes a new current
row, superseding (never mutating) the earlier one. `GET /advice` and
`advice list` return current rows only unless `history=true`. Keying on the
horizon is explicitly forbidden — the horizon is chosen by argmax-z and
would let two contradictory rows coexist as "current".

```json
{"id": "5d2e88f0c3a1b964", "garment_id": "41f6a9d2077cbe58",
 "as_of_week": "2026-07-27", "inputs_hash": "a1c07e5566b3d284",
 "config_version": "1.0.0",
 "stratum_id": "maison-vantorre/vantorre/outerwear",
 "action": "buy", "is_candidate": true, "horizon_weeks": 4,
 "expected_return": 0.1232, "confidence": 0.66,
 "fair_value": 1309.32, "fair_value_method": "repeat_sales",
 "rationale_codes": ["driver:event:b7c31d90aa25e4f8",
                     "prior:designer_departure.resignation",
                     "mod:lambda=1.00@b7c31d90aa25e4f8",
                     "mod:z=2.90@h4", "mod:conf_event=0.72",
                     "mod:q_index=1.0", "mod:src=manual"],
 "rendered_text": "Consider buying: “Vantorre curved-zip coat” — modeled +12.3% over 4 weeks…\n\nGrailTrader models a collectibles market — unregulated, illiquid (sales take weeks to months), with authenticity risk. This is information about that model, not investment advice.",
 "frame_checked": true, "created_as_of": "2026-07-31T08:05:00Z"}
```

**Arithmetic check** (FR-6/FR-8, prior `designer_departure.resignation`
P = 0.12, T = 0.10, h = 8, base_conf = 0.72; source manual ⇒ s_e = 1.0,
corroboration 1 ⇒ f_e = 1.0; event week 2026-06-29; as_of week 2026-07-27 so
age = 4; the departure targets `maison-vantorre/vantorre`, which is a prefix
of the leaf stratum used, so λ = 1):

```
A_e     = min(26, 8 · log2(0.10/0.005)) = min(26, 34.6) = 26   → active at age 4
B       = 120.0  (last point strictly before the event week, 2026-06-22)
O_t     = 125.0  (observed point at 2026-07-27) ; ln(O/B) = 0.040822
m(8)    = ln(1 + 0.12 + 0.10·0.5^(8/8))  = ln 1.170000 = 0.157004   → r̂₄  = e^0.116182 − 1 = 0.12320
m(16)   = ln(1 + 0.12 + 0.10·0.5^(16/8)) = ln 1.145000 = 0.135405   → r̂₁₂ = e^0.094583 − 1 = 0.09920
m(30)   = ln(1 + 0.12 + 0.10·0.5^(30/8)) = ln 1.127433 = 0.119942   → r̂₂₆ = e^0.079120 − 1 = 0.08234
σ_w     = 0.02
z₄      = 0.116182/(0.02·√4)  = 2.9045      ← argmax ⇒ H* = 4
z₁₂     = 0.094583/(0.02·√12) = 1.3652
z₂₆     = 0.079120/(0.02·√26) = 0.7758
z_term  = 1 − 0.5^(2.9045/0.8) = 0.9193
conf    = 0.9193 · q_index(1.0) · c_event(0.72) = 0.6619 → 0.66
action  : r̂ = 0.1232 ≥ theta_buy 0.12  and  conf 0.66 ≥ conf_min 0.55  ⇒ buy
fair_value: anchor 1,236.00 at carried anchor week (index 118.0) → 1236 · 125.0/118.0 = 1,309.32
```

### BacktestRun — `backtest_run` / BacktestResult — `backtest_result`

`backtest_run` (append-only):

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from canonical params + as_of |
| `params` | JSON | `{start_week, end_week, placebo_seed: int \| null, reference: "truth" \| "recovered", scenario: str}` |
| `as_of` | str | |
| `aggregates` | JSON | see below |

`aggregates` JSON shape:

```json
{"n_candidates": 0, "n_actionable": 0,
 "excluded": {"out_of_window": 0, "insufficient_future_index": 0,
              "stale_stratum": 0, "no_index": 0},
 "hit_rate": 0.0, "mean_realized": {"buy": 0.0, "sell": 0.0},
 "spread_buy_minus_sell": 0.0,
 "spread_by_horizon": {"4": 0.0, "12": 0.0, "26": 0.0},
 "buckets": {"lo": {"n": 0, "hit_rate": 0.0, "mean_conf": 0.0},
             "mid": {"n": 0, "hit_rate": 0.0, "mean_conf": 0.0},
             "hi": {"n": 0, "hit_rate": 0.0, "mean_conf": 0.0}},
 "by_event_type": {"designer_departure": {"n": 0, "hit_rate": 0.0}},
 "regimes": {"sell_into_decay": 0, "phase_in_buy": 0},
 "baselines": {"always_hold": {"n": 0, "hit_rate": null, "mean_realized": 0.0, "spread": 0.0},
               "theta_momentum": {"n": 0, "hit_rate": 0.0, "mean_realized": 0.0, "spread": 0.0},
               "event_naive": {"n": 0, "hit_rate": 0.0, "mean_realized": 0.0, "spread": 0.0}}}
```

Bucket edges come from `advisor_config.calibration.bucket_edges`
(`lo < 0.45 ≤ mid < 0.70 ≤ hi`) and are computed over **candidates**
(`is_candidate = true`), not over actionable advice only — otherwise the
`lo` bucket is empty by construction, since `conf_min = 0.55` (this was the
blocker in the first scoping round). `regimes.sell_into_decay` counts sell
advice whose driving events are all bullish; `regimes.phase_in_buy` counts
buys issued within 3 weeks of a driving event's week.

`backtest_result` (one row per decision considered):

| Field | Type | Notes |
|---|---|---|
| `run_id` | str FK PK-part | |
| `garment_id` | str PK-part | |
| `week` | str PK-part | advice week t |
| `action` | AdviceAction | |
| `is_candidate` | bool | `|r̂| ≥ theta`; the M3 population |
| `horizon_weeks` | int | |
| `confidence` | float \| null | |
| `expected_return` | float \| null | |
| `driver_event_ids` | JSON list[str] | active drivers, for regime and per-type breakdowns |
| `entry_week` | str | invariant: `entry_week = t + 1 week` (strictly-after rule, asserted) |
| `realized_return` | float \| null | `I_ref(entry + H)/I_ref(entry) − 1` |
| `hit` | bool \| null | `sign(r̂) == sign(realized)`, computed for every graded **candidate** (including `hold:low_confidence`); null for non-candidates and exclusions |
| `excluded_reason` | ExclusionReason \| null | set ⇔ `realized_return` is null. `out_of_window` (`t + 1 + H > end_week`) is reported but excluded from every rate's numerator *and* denominator and from the exclusion budget; the other three count against it |

Invariants: runs and results are append-only; a run never mutates advice,
listings, or index points; placebo runs are flagged by non-null seed and
never mixed into non-placebo aggregates.

## Relationships (summary)

```
brand 1—* designer_era
brand 1—* listing *—1 designer_era        (era belongs to same brand)
brand 1—* fashion_event (era_id optional, same-brand)
brand 1—* garment *—1 designer_era
garment 1—* advice                        (app-level reference, no SQL FK)
index_point: keyed by derived stratum path (no FK table; validated against gazetteer)
backtest_run 1—* backtest_result *—1 garment
```

Deletion policy: garments are the only user-removable entity and removal is
a **soft delete** (`deleted_at` set) — so `advice.garment_id` and
`backtest_result.garment_id` never dangle and no `ON DELETE` behaviour is
needed; those columns carry no SQL FK constraint by design, and readers join
against `garment` including soft-deleted rows. `init --reset` recreates the
DB wholesale; `index build` replaces only derived `index_point` rows.
Everything else is append-only.
