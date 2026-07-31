# GrailTrader — Data Model

Two storage classes, per workspace conventions:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/grailtrader/data/`): brand + designer-era gazetteer
  (`brands.json`), impact priors (`impact_priors.json`), condition grades +
  platform alias map (`conditions.json`), advice templates + forbidden
  lexicon + footer (`advice_templates.json`), advisor/index configuration
  (`advisor_config.json`). Loaded and validated into SQLite at
  `grailtrader init`; the files remain the source of truth.
- **SQLite** (user + pipeline state, default `~/.grailtrader/grailtrader.db`,
  path via `GRAILTRADER_DB`; in-memory backend for tests): listings, index
  points, fashion events, garments, advice, backtest runs/results.
  Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/grailtrader/models.py`; the store maps
them to the tables below. Enumerations are Python `StrEnum`s; SQLite stores
their string values. Timestamps are ISO-8601 UTC strings supplied by callers
(the engine never reads the clock); week keys are the ISO date of the week's
Monday (`2026-07-27`). Money is USD floats > 0. JSON-typed columns hold
canonical (sorted-keys, compact) serializations so byte-identity is well
defined.

## Stratum ids (not a table)

Strata are derived path strings over gazetteer keys:

```
brand                      e.g.  helmut-lang
brand/era                  e.g.  helmut-lang/helmut
brand/era/category (leaf)  e.g.  helmut-lang/helmut/outerwear
```

Prefix relationships define the hierarchy (FR-4 parent pooling, FR-5 event
scoping, FR-7 valuation fallback). Validity: each segment must exist in the
gazetteer and the era must belong to the brand (checked wherever a stratum
is constructed).

## Id scheme (FR-14)

All primary ids are content-derived: `hex16(x) = sha256(x)[:16]` over a
canonical natural-key string — ingestion is idempotent and replays are
byte-identical.

| Entity | Id derivation |
|---|---|
| Listing | `hex16("listing|" + source + "|" + external_id)` |
| FashionEvent | `hex16("event|" + event_type + "|" + brand_id + "|" + (era_id or "") + "|" + occurred_on + "|" + source_ref)` |
| Garment | `hex16("garment|" + stratum_path + "|" + condition + "|" + acquired_on + "|" + acquisition_price + "|" + label + "|" + added_at)` |
| Advice | `hex16("advice|" + garment_id + "|" + as_of_week + "|" + horizon_weeks)` |
| BacktestRun | `hex16("bt|" + canonical params JSON + "|" + as_of)` |
| IndexPoint | natural composite key `(stratum_id, week)` |

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
| `ListingSource` | `fixture`, `csv`, `ebay` |
| `ListingStatus` | `sold`, `active` |
| `GarmentStatus` | `owned`, `watching`, `sold_archived` |
| `AdviceAction` | `buy`, `sell`, `hold` |
| `ExclusionReason` (backtest) | `insufficient_future_index`, `stale_stratum`, `no_index` |

## Committed datasets

### Brand / DesignerEra — `data/brands.json` → tables `brand`, `designer_era`

| Brand field | Type | Notes |
|---|---|---|
| `id` | str PK | slug; one per label/line (`dior-homme` is distinct from `dior`) |
| `name` | str | display name |
| `aliases` | list[str] | matching aid for CSV/eBay import queries; unique across gazetteer (checked at init) |
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

### ImpactPrior — `data/impact_priors.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `key` | str PK | `event_type.qualifier`, e.g. `designer_departure.death`, `brand_scandal.moderate`, `designer_appointment.acclaimed.brand`, `designer_appointment.*.predecessor_era`, `celebrity_cosign.*`, `runway_reception.panned` |
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

```json
{"key": "designer_departure.death", "target": "era", "direction": "bullish",
 "permanent_pct": 0.20, "transient_pct": 0.25, "half_life_weeks": 6.0,
 "base_conf": 0.75,
 "rationale": "When a designer dies, their era's supply is permanently finite and demand spikes; resale prices have jumped sharply within days and partially retraced over the following weeks.",
 "source_note": "StockX resale data on Off-White and Louis Vuitton x Nike after Virgil Abloh's death, Nov 2021: double-digit price spikes within days, partial decay over weeks."}
```

```json
{"key": "celebrity_cosign.*", "target": "given", "direction": "bullish",
 "permanent_pct": 0.0, "transient_pct": 0.10, "half_life_weeks": 3.0,
 "base_conf": 0.50,
 "rationale": "A high-profile celebrity moment lifts search and resale demand for the worn brand/era quickly, and the effect fades within weeks unless reinforced.",
 "source_note": "Lyst Index methodology (brand heat from search/social moments); Lyst/Depop-reported search spikes for vintage Jean Paul Gaultier during the Bella Hadid-driven revival. Tier scaling a_list 1.0 / b_list 0.5 / niche 0.25 applied to T."}
```

### ConditionGrade — `data/conditions.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `grade` | ConditionGrade PK | |
| `multiplier` | float | vs `excellent` = 1.00; defaults: new 1.25, excellent 1.00, good 0.80, fair 0.55, poor 0.35; invariant: strictly decreasing |
| `platform_aliases` | list[str] | case-insensitive labels mapped at ingest, e.g. `new` ← "New/Never Worn", "Never worn with tag", "NWT", "Deadstock"; `excellent` ← "Gently Used", "Never worn", "Excellent" |

Invariant: aliases unique across grades (an ingest label maps to exactly
one grade); unmapped labels are rejected at ingest with the label named
(FR-2), never guessed.

### AdviceTemplate — `data/advice_templates.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `buy-event-driven`, `sell-into-spike`, `hold-stale-index`, `hold-quiet` |
| `action` | AdviceAction | |
| `headline` | str | format string, e.g. `Consider buying: {garment_label} — modeled {expected_move} over {horizon_weeks} weeks` |
| `drivers` | str | format string; must include `{driver_lines}` (one per active event: label, age, prior rationale) |
| `valuation_context` | str | must include `{fair_value}` and `{level_usd}` (or the explicit unavailable-reason) |
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

### AdvisorConfig — `data/advisor_config.json` (loaded at init; no table)

Single validated document; all FR-4/FR-8 constants live here, not in code:

```json
{"index": {"window_weeks": 4, "min_sales": 5, "fence_sigma": 3.5,
           "fence_floor_log": 0.7885, "stale_max_weeks": 8,
           "parent_weight_window_weeks": 8},
 "advisor": {"horizons_weeks": [4, 12, 26], "theta_buy": 0.10,
             "theta_sell": 0.10, "conf_min": 0.55, "z_half": 0.8,
             "sigma_min_changes": 12, "active_max_weeks": 26,
             "active_min_impact": 0.005,
             "q_index": {"0": 1.0, "1-2": 0.8, "3-8": 0.5},
             "source_factor": {"manual": 1.0, "news": 0.95, "social": 0.8},
             "fee_assumption_pct": 0.20}}
```

(`fence_floor_log` = ln 2.2. Ranges validated at init; e.g.
`0 < theta ≤ 0.5`, horizons ascending, `conf_min ∈ [0.05, 0.95]`.)

## SQLite entities

### Listing — `listing` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived (id scheme) |
| `source` | ListingSource | |
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
| `ask_price` | float \| null | diagnostics only — never enters the index |
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
| `n_sales` | int | surviving sales in window (≥ `min_sales` for leaves; for parents: sum over contributing children) |
| `n_excluded` | int | FR-3 fence exclusions in window (leaves; 0 for parents) |
| `built_as_of` | str | the `index build` as_of |

Invariants: a week with insufficient window sales simply has no row
(staleness is computed at query time as distance to the last row ≤ t —
never fabricated); rebuild with identical inputs is byte-identical;
rebuild with new listings replaces derived rows wholesale (the only
non-append-only table, and it is purely derived).

```json
{"stratum_id": "maison-vantorre/vantorre/outerwear", "week": "2026-07-27",
 "level_usd": 1180.0, "index_value": 127.2, "n_sales": 27, "n_excluded": 1,
 "built_as_of": "2026-07-31T08:00:00Z"}
```

### FashionEvent — `fashion_event` (append-only; status transition only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived (id scheme) |
| `event_type` | EventType | |
| `brand_id` | str FK → brand | |
| `era_id` | str FK \| null | required for `designer_departure` (the closing era) and optional for `celebrity_cosign`; must belong to brand |
| `attributes` | JSON | typed per event type: `{reason}`, `{designer, acclaim}`, `{counterparty, counterparty_brand_id?}`, `{celebrity, tier, category?}`, `{polarity}`, `{severity}`; unknown keys rejected |
| `occurred_on` | str | ISO date; the event's week = ISO week containing it |
| `source` | EventSource | |
| `source_ref` | str | feed URL/guid, or `manual:<slug>` — part of the id's natural key |
| `status` | EventStatus | fixture/manual → `confirmed`; RSS candidates → `pending` |
| `corroboration` | int | ≥ 1; bumped when re-ingest sees the same natural key from a distinct source_ref domain |
| `notes` | str | free text (quoted-only in renders) |

Invariants: append-only except the `pending → confirmed|rejected`
transition and `corroboration` bumps; `pending`/`rejected` events never
affect impacts, advice, or backtests (enforced at query layer, covered by
tests); attribute schema per type validated at write.

Resolved target strata are computed by FR-5, not stored (pure function of
the event + gazetteer).

```json
{"id": "b7c31d90aa25e4f8", "event_type": "designer_departure",
 "brand_id": "maison-vantorre", "era_id": "maison-vantorre:vantorre",
 "attributes": {"reason": "resignation"},
 "occurred_on": "2026-06-29", "source": "manual",
 "source_ref": "manual:vantorre-departure", "status": "confirmed",
 "corroboration": 1, "notes": "Founder announced exit effective FW26."}
```

(Fictional brand by design — fixture and doc examples never attach
scandal/death/departure events to real brands or people; SCOPE D-14.)

### Garment — `garment`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived (id scheme) |
| `label` | str | user's name for the piece ("HL astro moto jacket") |
| `brand_id` | str FK | |
| `era_id` | str FK | must belong to brand |
| `category` | Category | |
| `condition` | ConditionGrade | |
| `size` | str \| null | |
| `acquisition_price` | float | > 0, USD |
| `acquired_on` | str | ISO date |
| `status` | GarmentStatus | `owned` \| `watching` \| `sold_archived` |
| `disposed_price`, `disposed_on` | float \| null, str \| null | both set iff `sold_archived` (CHECK) |
| `added_at` | str | edge-supplied; part of the id natural key |
| `notes` | str | |

`stratum_path` is derived: `brand/era/category`. Editable fields:
`label`, `condition` (rescales fair value by multiplier ratio, FR-7),
`size`, `status` (+ disposal fields), `notes`. Brand/era/category/
acquisition fields are immutable (they define the id) — a mis-entered
garment is removed and re-added.

```json
{"id": "9ab04c11de77f203", "label": "HL astro moto jacket",
 "brand_id": "helmut-lang", "era_id": "helmut-lang:helmut",
 "category": "outerwear", "condition": "excellent", "size": "48",
 "acquisition_price": 840.0, "acquired_on": "2024-03-06",
 "status": "owned", "disposed_price": null, "disposed_on": null,
 "added_at": "2026-07-31T08:00:00Z", "notes": "bought on Grailed"}
```

(Valuation check per FR-7: index `helmut-lang/helmut/outerwear` was 118.4
in acquisition week 2024-03-04 and 141.2 in week 2026-07-27 → fair value
= 840 × 141.2 / 118.4 = **$1,001.76**. Example records must stay
formula-verifiable.)

### Advice — `advice` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from (garment, as_of week, horizon) |
| `garment_id` | str FK | |
| `as_of_week` | str | ISO Monday |
| `stratum_id` | str | the stratum actually used (most specific usable) |
| `action` | AdviceAction | |
| `horizon_weeks` | int | H* chosen by argmax z (FR-8) |
| `expected_return` | float | r̂ at H* (signed fraction) |
| `confidence` | float | CHECK `0.05 ≤ confidence ≤ 0.95` |
| `fair_value` | float \| null | FR-7 value at as_of (null with reason code when unavailable) |
| `rationale_codes` | JSON list[str] | e.g. `["driver:event:b7c31d90aa25e4f8", "mod:z=2.47@h4", "mod:conf_event=0.70", "mod:q_index=1.0"]`; hold reasons: `hold:stale_index`, `hold:no_active_events`, `hold:below_threshold`, `hold:low_confidence`, `hold:no_baseline` |
| `rendered_text` | str | full rendering incl. footer |
| `frame_checked` | bool | CHECK `frame_checked = 1` — a failing advice cannot exist in the store (FR-9) |
| `created_as_of` | str | ISO UTC |

Invariants: immutable; unique (garment_id, as_of_week, horizon_weeks) via
the id; re-running `advise` for the same key is a no-op.

```json
{"id": "5d2e88f0c3a1b964", "garment_id": "41f6a9d2077cbe58",
 "as_of_week": "2026-07-27",
 "stratum_id": "maison-vantorre/vantorre/outerwear",
 "action": "buy", "horizon_weeks": 4, "expected_return": 0.1037,
 "confidence": 0.62, "fair_value": 1310.0,
 "rationale_codes": ["driver:event:b7c31d90aa25e4f8",
                     "prior:designer_departure.resignation",
                     "mod:z=2.47@h4", "mod:conf_event=0.70",
                     "mod:q_index=1.0", "mod:src=manual"],
 "rendered_text": "Consider buying: Vantorre curved-zip coat — modeled +10.4% over 4 weeks…\n\nGrailTrader models a collectibles market — unregulated, illiquid (sales take weeks to months), with authenticity risk. This is information about that model, not investment advice.",
 "frame_checked": true, "created_as_of": "2026-07-31T08:05:00Z"}
```

(Arithmetic check, per FR-6/8 with prior `designer_departure.resignation`
P = 0.12, T = 0.10, h = 8; event week 2026-06-29, age 4; baseline
B = 120.0 at week 2026-06-22; observed O = 127.2 → ln(O/B) = 0.0583;
m(8) = ln(1 + 0.12 + 0.10·0.5^1) = ln 1.17 = 0.1570 →
r̂₄ = e^(0.1570 − 0.0583) − 1 = 0.1037; σ_w = 0.02 →
z₄ = 0.0987/(0.02·2) = 2.47 (vs z₁₂ = 1.11, z₂₆ = 0.60, so H* = 4);
conf = (1 − 0.5^(2.47/0.8)) · 1.0 · 0.70 = 0.882 · 0.70 = 0.617 → 0.62;
r̂ ≥ 0.10 and conf ≥ 0.55 → buy.)

### BacktestRun — `backtest_run` / BacktestResult — `backtest_result`

`backtest_run` (append-only):

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from canonical params + as_of |
| `params` | JSON | `{start_week, end_week, placebo_seed: int \| null, reference: "truth" \| "recovered"}` |
| `as_of` | str | |
| `aggregates` | JSON | overall + per action + per driving event type: `{n, n_excluded, hit_rate, mean_realized, spread_buy_minus_sell, buckets: {lo: {n, hit}, mid: …, hi: …}}` plus the three baselines' `{n, hit_rate, mean_realized}` computed in the same run |

`backtest_result` (one row per decision considered):

| Field | Type | Notes |
|---|---|---|
| `run_id` | str FK PK-part | |
| `garment_id` | str PK-part | |
| `week` | str PK-part | advice week t |
| `action` | AdviceAction | |
| `horizon_weeks` | int | |
| `confidence` | float | |
| `expected_return` | float | |
| `entry_week` | str | invariant: `entry_week = t + 1 week` (strictly-after rule, asserted) |
| `realized_return` | float \| null | `I_ref(entry + H)/I_ref(entry) − 1` |
| `hit` | bool \| null | actionable advice only; null for holds and exclusions |
| `excluded_reason` | ExclusionReason \| null | set ⇔ realized_return is null; counted in aggregates |

Invariants: runs and results are append-only; a run never mutates advice,
listings, or index points; placebo runs are flagged by non-null seed and
never mixed into non-placebo aggregates.

## Relationships (summary)

```
brand 1—* designer_era
brand 1—* listing *—1 designer_era        (era belongs to same brand)
brand 1—* fashion_event (era_id optional, same-brand)
brand 1—* garment *—1 designer_era
garment 1—* advice
index_point: keyed by derived stratum path (no FK table; validated against gazetteer)
backtest_run 1—* backtest_result *—1 garment
```

Deletion policy: garments are the only user-deletable entity (removing one
keeps its historical advice rows, which reference the id); `init --reset`
recreates the DB wholesale; `index build` replaces only derived
`index_point` rows. Everything else is append-only.
