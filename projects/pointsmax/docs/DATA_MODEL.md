# PointsMax — Data Model

Two storage classes, per workspace conventions:

- **Committed dataset** (read-only at runtime, versioned in git under
  `projects/pointsmax/data/world/`): the rewards world — programs, card
  products, transfer edges, cashout options, valuations, award offers,
  reference fares, gazetteer, version. Loaded and validated (FR-1) by
  `CommittedWorldProvider` into in-memory Pydantic models at startup; the
  files are the source of truth and are **not** mirrored into SQLite. User
  state references world entities by their string ids; saved plans pin the
  world `version` + `content_hash`.
- **SQLite** (user state, default `~/.pointsmax/pointsmax.db`, path
  configurable; in-memory backend for tests): profile, wallet cards, ledger,
  goals, plan sets, plans, plan steps. Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/pointsmax/models.py`. Enumerations are
`StrEnum`s; SQLite stores string values. Conventions (FR-8/FR-16):

- Money: integer **cents**. Points: integers. Valuations: integer
  **milli-cents per point** (`cpp_milli`; 1.85¢/pt = 1850).
- Baseline value of `n` points at `m` mcpp = `(n × m) // 1000` cents (floor).
  Fees round up: `ceil(sent × fee_mcpp / 1000)`, then capped.
- Dates are ISO `YYYY-MM-DD` strings; timestamps ISO-8601 UTC strings;
  both always supplied by callers (the engine never reads the clock).
- Ids are lowercase snake-case slugs (`chase_ur`, `flying_blue`).
- **Canonical JSON** (used for the content hash, plan signatures, and caveat
  ordering): `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)` encoded UTF-8.

## Enumerations

| Enum | Values |
|---|---|
| `ProgramKind` | `bank`, `airline`, `hotel` |
| `Cabin` | `economy`, `premium_economy`, `business`, `first` |
| `OfferKind` | `flight`, `stay` |
| `GoalKind` | `flight`, `stay`, `cash` |
| `GoalStatus` | `active`, `planned`, `fulfilled`, `dropped` |
| `CashoutMethod` | `statement_credit`, `bank_deposit`, `portal_travel`, `gift_card` |
| `StepKind` | `transfer`, `book_award`, `book_portal`, `redeem_cash` |
| `LedgerReason` | `set`, `adjust`, `transfer_out`, `transfer_in`, `transfer_bonus`, `award_redeem`, `cash_redeem`, `portal_redeem` |
| `Verdict` | `book_with_points`, `pay_cash_keep_points`, `insufficient_points`, `no_matching_award`, `cash_plan` |
| `CaveatCode` | `irreversible_transfer`, `transfer_time_risk`, `stranded_points`, `promo_expiring`, `below_baseline`, `stale_world`, `seats_limited` |

### Cashout liquidity (normative)

`is_cash` is a **derived constant of `CashoutMethod`**, implemented as a
module-level mapping in `models.py` — not a field in the JSON data, so the
dataset cannot contradict it:

| Method | `is_cash` | Meaning |
|---|---|---|
| `statement_credit` | **true** | points → money against the card balance |
| `bank_deposit` | **true** | points → money in an account |
| `portal_travel` | false | points buy travel at a fixed rate; no cash is produced |
| `gift_card` | false | scrip, not cash; not liquid at face value |

Consumers: FR-12 (cash goals consider only `is_cash = true` options),
FR-13 (`cash_floor_cents` uses only `is_cash = true`; `travel_floor_cents`
uses all active options). `portal_travel` options additionally serve as
*portal bookings* for flight/stay goals (`book_portal` steps, FR-7a).

## Committed world entities (`data/world/`)

### Program — `programs.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | slug, e.g. `flying_blue` |
| `name` | str | e.g. `Air France-KLM Flying Blue` |
| `kind` | ProgramKind | `bank` currencies are transferable; `airline`/`hotel` are terminal spend programs |
| `source_note` | str | provenance/grounding |

Invariants: unique ids; every program referenced by any edge/option/offer
exists; every program has exactly one Valuation.

### CardProduct — `cards.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `chase_sapphire_reserve` |
| `issuer` | str | `Chase`, `Amex`, `Citi`, `Capital One`, `Bilt` |
| `name` | str | display name |
| `program_id` | str FK → Program | the currency this card holds/earns |
| `enables_transfer` | bool | holding it unlocks partner transfers from `program_id` (FR-3) |
| `annual_fee_cents` | int | display metadata only; never enters an objective (Non-goal 10) |
| `source_note` | str | |

Invariant: `program_id.kind == bank`. Portal rates are *not* stored here —
they are CashoutOptions with `requires_card`. Card *benefits* other than
transfer access and redemption rates are out of scope (Non-goal 10).

### TransferEdge — `transfers.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `amex_mr__flying_blue` |
| `from_program`, `to_program` | str FK | no self-loops |
| `ratio_from`, `ratio_to` | int ≥ 1 | e.g. 1:1, 1:2 (Amex→Hilton), 3:1 (Marriott→airlines); delivered = `sent × ratio_to // ratio_from` |
| `min_from` | int | minimum points per transfer (e.g. 1000) |
| `increment_from` | int | sent must be a multiple (e.g. 1000; Marriott 3000) |
| `fee_mcpp` | int | milli-cents per *from*-point; 0 for most; 60 for Amex→US airlines (0.06¢/pt) |
| `fee_cap_cents` | int \| null | e.g. 9900 ($99 Amex cap); null = uncapped |
| `time_days` | int | 0 = instant; e.g. Amex→ANA 2, Marriott→airlines 2 |
| `bonus_per_from` | int \| null | tier size, e.g. 60000 (Marriott) |
| `bonus_to` | int \| null | bonus delivered per full tier, e.g. 5000; both null or both set |
| `valid_from`, `valid_to` | date \| null | promo window; null = evergreen (FR-3) |
| `source_note` | str | |

Invariants (all FR-1 checked): `(from_program, to_program, valid_from)`
unique; `increment_from % ratio_from == 0` and
`min_from % increment_from == 0` (delivered amounts exact); when a bonus is
set, `bonus_per_from % increment_from == 0`; `from_program.kind == bank`
edges are gated by `enables_transfer` cards; bonus fields paired;
**no value-increasing edge** (FR-1 invariant 4) — without a bonus
`ratio_to × mcpp_to ≤ ratio_from × mcpp_from`, with one
`(ratio_to × bonus_per_from + ratio_from × bonus_to) × mcpp_to ≤
ratio_from × bonus_per_from × mcpp_from`.

Delivered points for a sent amount `s`:
`delivered(s) = s × ratio_to // ratio_from + bonus_to × (s // bonus_per_from)`.

**Dataset design rule (not a schema constraint):** every edge in the shipped
`data/world/` has `increment_from ≥ 1000`, matching real programs (banks
transfer in 1,000-point increments, Marriott in 3,000). This bounds a
brute-force enumerator to ≤ balance/1000 amounts per edge (SCOPE decision 18)
and is asserted by a dataset test.

### CashoutOption — `cashouts.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `ur_portal_csr` |
| `program_id` | str FK | |
| `method` | CashoutMethod | liquidity is derived from the method (see above), not stored |
| `cpp_milli` | int | e.g. UR statement credit 1000; CSR portal 1500; MR statement credit 600; Amex BizPlat pay-with-points effective 1538 |
| `min_points`, `increment` | int | quantization (e.g. 1) |
| `requires_card` | str FK → CardProduct \| null | null = any holder of the currency |
| `valid_from`, `valid_to` | date \| null | |
| `source_note` | str | |

### Valuation — `valuations.json`

| Field | Type | Notes |
|---|---|---|
| `program_id` | str PK/FK | one per program (invariant) |
| `cpp_milli` | int | baseline value, e.g. `chase_ur` 2050, `hilton_honors` 500 |
| `as_of` | date | the minimum `as_of` across valuations drives `stale_world` (FR-10) |
| `source_note` | str | e.g. `modeled on Frequent Miler RRV / TPG valuations, 2026-07` |

### AwardOffer — `awards.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `fb_biz_nyc_par_oct26` |
| `program_id` | str FK | paying program (single-program payment — decision 10) |
| `kind` | OfferKind | flight fields null for stays and vice versa (invariant) |
| `origin`, `destination` | str \| null | airport or city code; must resolve in gazetteer |
| `cabin` | Cabin \| null | flights only |
| `round_trip` | bool \| null | flights only |
| `points_price` | int | flights: per passenger; stays: per night |
| `fees_cents` | int | flights: per passenger (taxes/surcharges); stays: per night |
| `city` | str \| null | stays only; gazetteer city code |
| `travel_window_start`, `travel_window_end` | date | availability window (FR-6); `travel_window_end` also bounds the booking deadline (FR-7d) |
| `seats_available` | int \| null | max passengers; null = uncapped |
| `bookable_until` | date \| null | booking deadline for the offer itself (FR-6 and FR-7d) |
| `source_note` | str | e.g. award-chart / typical-pricing provenance |

Invariant: reference-fare coverage per FR-1 invariant 5 — for every
`(origin_city, dest_city, cabin)` a flight offer touches, both one-way
directions **and** the round-trip row must exist for every month the offer's
travel window touches (so portal bookings and mixed award/portal round trips
are always priceable).

### ReferenceFare — `reference_fares.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | |
| `kind` | OfferKind | |
| `origin_city`, `dest_city` | str \| null | flight; city codes |
| `cabin` | Cabin \| null | flight |
| `round_trip` | bool \| null | flight |
| `city` | str \| null | stay |
| `month` | str | `YYYY-MM` |
| `fare_cents` | int | flight: per passenger; stay: per night ("reasonable cash price", not rack rate — SCOPE decision 2) |
| `source_note` | str | |

**Lookup key (FR-8): the month of the *goal's* travel window**, not the
offer's — flights `(origin_city, dest_city, cabin, round_trip, goal month)`;
stays `(city, goal month)`. A round-trip award or portal booking uses the
`round_trip = true` row; each leg of a one-way pair uses that direction's
`round_trip = false` row. This is why FR-1 invariant 5 requires coverage for
every month an offer's travel window touches: an offer whose window spans
October–November must price correctly for a November goal. Missing fares at
plan time are impossible by that invariant.

### GazetteerEntry — `gazetteer.json`

| Field | Type | Notes |
|---|---|---|
| `city_code` | str PK | e.g. `NYC`, `PAR`, `TYO` |
| `name` | str | `New York` |
| `airports` | list[str] | `["JFK", "EWR", "LGA"]`; airport codes unique across entries |
| `aliases` | list[str] | lowercase match strings for FR-5 (`"nyc"`, `"new york"`, `"new york city"`) |

### WorldVersion — `version.json`

| Field | Type | Notes |
|---|---|---|
| `version` | str | semver of the dataset, e.g. `1.0.0`; **display metadata** — not the execution guard |
| `as_of` | date | data snapshot date |
| `content_hash` | str | sha256 hex; recomputed and checked at load (FR-1), and the authoritative pin for plan execution (FR-11) |

**Hash definition (non-circular):** `content_hash` = sha256 over the
concatenation of, for each world file **except `version.json`** in ascending
filename order, the bytes `filename + "\n" + canonical_json(parsed_contents)
+ "\n"`. Canonical JSON is defined at the top of this document; lists keep
their file order, so the hash is stable across formatting-only edits but
changes on any data edit.

## SQLite entities

### Profile — table `profile` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1` |
| `display_name` | str | |
| `home_city` | str \| null | gazetteer city code; parser default origin (FR-5) |
| `default_passengers` | int | default 1 |
| `created_at`, `updated_at` | str | ISO ts, caller-supplied |

Exposed by `GET/PUT /profile` and `pointsmax profile show|set` (SCOPE
FR-14/FR-15), so the FR-5 default-origin behavior is configurable end-to-end.

### WalletCard — table `wallet_card`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `card_product_id` | str | FK-by-slug into the world (validated on write) |
| `added_at` | str | ISO ts |

Invariant: `card_product_id` unique (you hold a product or you don't).

### LedgerEntry — table `ledger_entry` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `program_id` | str | world slug, validated |
| `delta_points` | int | signed; never brings balance below 0 (rejected) |
| `post_balance` | int | balance after applying (audit; replay invariant) |
| `reason` | LedgerReason | |
| `plan_step_id` | int FK \| null | non-null iff reason ∈ {transfer_out, transfer_in, transfer_bonus, award_redeem, cash_redeem, portal_redeem} (invariant) |
| `note` | str \| null | free text for manual entries |
| `at` | str | ISO ts (input) |

Invariants: append-only; per program, entries totally ordered by `id` and
`post_balance` chains exactly (`post = prev_post + delta`); replaying all
entries from zero reproduces every `post_balance` (D0 test). Derived, never
stored: current balance per program = last `post_balance`.

### Goal — table `goal`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `kind` | GoalKind | |
| `raw_text` | str \| null | original utterance when parsed (FR-5) |
| `origin_city`, `dest_city` | str \| null | flight |
| `cabin` | Cabin \| null | flight; null = any cabin |
| `round_trip` | bool \| null | flight |
| `passengers` | int \| null | flight; 1–8 |
| `city` | str \| null | stay |
| `nights` | int \| null | stay; 1–30 |
| `travel_window_start`, `travel_window_end` | date \| null | resolved month → first/last day (FR-5); the window's month is the FR-8 fare lookup key |
| `book_by` | date \| null | booking deadline the user imposes (FR-7d) |
| `cash_programs` | JSON list[str] \| null | cash: optional program filter |
| `cash_max_points` | JSON {program: int} \| null | cash: optional per-program cap |
| `status` | GoalStatus | `active` on create; `planned` when a PlanSet exists; terminal states frozen |
| `created_at` | str | ISO ts |

Invariant: kind-specific nullability (flight fields ⇔ kind=flight, etc.),
enforced by the Pydantic model.

### PlanSet — table `plan_set` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `goal_id` | int FK | |
| `world_version` | str | pinned `WorldVersion.version`; display only |
| `world_hash` | str | pinned `content_hash`; **execution requires this to match** (FR-11) |
| `today` | date | the input date used (FR-16) |
| `params` | JSON | `{top_k, max_hops, max_expansions, slack_days, promo_days, stale_days}` |
| `expansions` | int | search expansions consumed (M6 records it; informational elsewhere) |
| `verdict` | Verdict | FR-9 |
| `recommended_plan_id` | int FK \| null | rank-1 plan when verdict is book_with_points / cash_plan |
| `disclaimer` | str | fixed FR-16 string, asserted in tests |
| `created_at` | str | ISO ts |

A PlanSet with verdict `insufficient_points` or `no_matching_award` contains
**zero** plans and only the verdict.

### Plan — table `plan` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `plan_set_id` | int FK | |
| `rank` | int | 1-based; unique `(plan_set_id, rank)` |
| `is_comparator` | bool | appended portal/cash comparator beyond top-K (FR-9) |
| `gross_value_cents` | int | FR-8 |
| `cash_outlay_cents` | int | award/booking fees + transfer fees (merged-amount fee math) |
| `points_cost_cents` | int | `V(H0) − V(H1)` |
| `net_value_cents` | int | `gross − outlay − points_cost` (ranking key for flight/stay) |
| `cash_received_cents` | int \| null | cash goals (ranking key) |
| `realized_cpp_milli` | int \| null | **pooled** form (FR-8): `(Σ booking value − Σ booking fees) × 1000 // Σ points spent on bookings`; null when no points are spent |
| `points_spent` | JSON {program: int} | consumed per program |
| `feasible_in_days` | int | max `arrival_days` over the plan's bookings (FR-7d); 0 = all instant |
| `signature` | str | sha256 of the canonical JSON of the plan's canonical step-tuple list (FR-9); unique per plan_set; identity/dedup only, never a tie-break input |
| `caveats` | JSON list[{code: CaveatCode, params: {…}, text: str}] | FR-10; ordered by `CaveatCode` declaration order then params' canonical JSON; text rendered from committed templates |

### PlanStep — table `plan_step`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `plan_id` | int FK | |
| `seq` | int | 1-based index in the FR-9 **canonical step order**; unique `(plan_id, seq)`; execution strictly in this order (FR-11) |
| `kind` | StepKind | |
| `hop_index` | int | transfers: depth in the funding DAG (0 = out of an opening balance); bookings: 0. Part of the canonical sort key so any implementation reproduces the order |
| `from_program`, `to_program` | str \| null | transfer: both; book_award/redeem_cash/book_portal: `from_program` only |
| `edge_id` | str \| null | transfer: the TransferEdge used |
| `offer_id` | str \| null | book_award: the AwardOffer |
| `cashout_id` | str \| null | redeem_cash / book_portal: the CashoutOption |
| `points_sent` | int | transfer: merged sent amount (FR-7b); bookings: points spent |
| `points_delivered` | int \| null | transfer only: base delivery + tier bonus |
| `fees_cents` | int | edge fee (on the merged amount) or award fees for this step |
| `eta_days` | int | this step's posting time |
| `irreversible` | bool | true for `transfer` and `book_award`; gates confirmation (FR-11) |
| `explanation` | str | deterministic template render; every number equals a stored field (FR-10) |
| `executed_at` | str \| null | set once on execution; ledger entries reference this step |

Canonical step tuple (FR-9, used by M3 and by `signature`):
`(kind, from_program, to_program, edge_id, offer_id, cashout_id,
points_sent, points_delivered, fees_cents)` with nulls rendered as `""` for
strings and `0` for integers.

Invariants: kind-specific nullability; at most one transfer step per
`(plan_id, edge_id)` (FR-7b merging); executing writes the matching ledger
entries atomically (transfer → `transfer_out` + `transfer_in` + optional
`transfer_bonus`; book_award/book_portal/redeem_cash → one redemption
entry); a step executes at most once; execution re-validates balances and
the world `content_hash` at apply time.

## Relationships (summary)

```
World (committed, read-only):
  Program (18) ◀── CardProduct.program_id
  Program ◀─from/to── TransferEdge (~60)      Program ◀── CashoutOption (~25)
  Program ◀── Valuation (1:1)                 Program ◀── AwardOffer (~80)
  AwardOffer ▶ covered-by ▶ ReferenceFare     Gazetteer ◀ resolves offers & goals
  WorldVersion pins it all (content_hash over all files except version.json)

SQLite (user state):
  Profile (1)     WalletCard (N, unique product)
  LedgerEntry (N, append-only) ──▶ PlanStep (0..1)
  Goal (N) ──▶ PlanSet (0..N, immutable) ──▶ Plan (0..K+1) ──▶ PlanStep (1..N)
      Plan count is 0 for insufficient_points / no_matching_award verdicts
  Plan.signature: canonical identity; balances, floors, V(H): derived, never stored
```

## Example records

`program` + `valuation` (from `programs.json` / `valuations.json`):

```json
{ "id": "flying_blue", "name": "Air France-KLM Flying Blue",
  "kind": "airline", "source_note": "SkyTeam FFP; Amex/Chase/Citi/C1 partner" }

{ "program_id": "flying_blue", "cpp_milli": 1300, "as_of": "2026-07-01",
  "source_note": "modeled on Frequent Miler RRV / TPG valuations, 2026-07" }
```

`transfer_edge` — the Marriott hub with tier bonus, and an Amex fee edge:

```json
{ "id": "marriott_bonvoy__alaska_mp",
  "from_program": "marriott_bonvoy", "to_program": "alaska_mp",
  "ratio_from": 3, "ratio_to": 1, "min_from": 3000, "increment_from": 3000,
  "fee_mcpp": 0, "fee_cap_cents": null, "time_days": 2,
  "bonus_per_from": 60000, "bonus_to": 5000,
  "valid_from": null, "valid_to": null,
  "source_note": "Bonvoy 3:1 airline transfers in 3,000-pt increments, +5k miles per 60k" }

{ "id": "amex_mr__delta_skymiles",
  "from_program": "amex_mr", "to_program": "delta_skymiles",
  "ratio_from": 1, "ratio_to": 1, "min_from": 1000, "increment_from": 1000,
  "fee_mcpp": 60, "fee_cap_cents": 9900, "time_days": 0,
  "bonus_per_from": null, "bonus_to": null,
  "valid_from": null, "valid_to": null,
  "source_note": "Amex excise-tax offset fee: 0.06 cents/pt, $99 cap, US airlines" }
```

Invariant 4 check for the Marriott edge (Marriott 800 mcpp, Alaska 1400
mcpp): `(1×60000 + 3×5000) × 1400 = 105,000,000 ≤ 3×60000×800 =
144,000,000` ✓ — the edge cannot manufacture paper value, so FR-7's lattice
may stop at `s_cover`.

`card_product` and a gated `cashout_option`:

```json
{ "id": "chase_sapphire_reserve", "issuer": "Chase", "name": "Sapphire Reserve",
  "program_id": "chase_ur", "enables_transfer": true,
  "annual_fee_cents": 79500, "source_note": "premium UR card; unlocks transfers" }

{ "id": "ur_portal_csr", "program_id": "chase_ur", "method": "portal_travel",
  "cpp_milli": 1500, "min_points": 1, "increment": 1,
  "requires_card": "chase_sapphire_reserve",
  "valid_from": null, "valid_to": null,
  "source_note": "Chase travel portal at 1.5 cpp with CSR" }

{ "id": "ur_statement_credit", "program_id": "chase_ur", "method": "statement_credit",
  "cpp_milli": 1000, "min_points": 1, "increment": 1,
  "requires_card": null, "valid_from": null, "valid_to": null,
  "source_note": "UR cash-out at 1.0 cpp" }
```

For a CSR holder with 210,000 UR (FR-13): `baseline_value_cents = 430,500`
(2050 mcpp), `travel_floor_cents = 315,000` (portal, 1500 mcpp),
`cash_floor_cents = 210,000` (statement credit, 1000 mcpp). A `cash` goal
(FR-12) may only use the statement credit.

`award_offer` and its `reference_fare`s:

```json
{ "id": "fb_biz_nyc_par_oct26", "program_id": "flying_blue", "kind": "flight",
  "origin": "NYC", "destination": "PAR", "cabin": "business",
  "round_trip": false, "points_price": 60000, "fees_cents": 25100,
  "city": null, "travel_window_start": "2026-10-01",
  "travel_window_end": "2026-10-31", "seats_available": 2,
  "bookable_until": null,
  "source_note": "Flying Blue standard-level business NYC-Paris one-way" }

{ "id": "rf_nyc_par_biz_ow_2026_10", "kind": "flight",
  "origin_city": "NYC", "dest_city": "PAR", "cabin": "business",
  "round_trip": false, "city": null, "month": "2026-10",
  "fare_cents": 210000,
  "source_note": "reasonable one-way business fare, not rack rate" }
```

(FR-1 invariant 5 additionally requires `rf_par_nyc_biz_ow_2026_10` and
`rf_nyc_par_biz_rt_2026_10` for the same month.)

`goal` (parsed from "round-trip business NYC to Paris in October",
today = 2026-07-31):

```json
{ "id": 3, "kind": "flight",
  "raw_text": "round-trip business NYC to Paris in October",
  "origin_city": "NYC", "dest_city": "PAR", "cabin": "business",
  "round_trip": true, "passengers": 1,
  "travel_window_start": "2026-10-01", "travel_window_end": "2026-10-31",
  "book_by": null, "status": "planned", "created_at": "2026-07-31T14:02:00Z" }
```

`plan` with three steps (one merged transfer funding both one-way awards;
holdings before: MR 130,000 @ 2000 mcpp, UR 210,000 @ 2050 mcpp, FB 0 @
1300 mcpp):

```json
{ "plan_set_id": 5, "rank": 1, "is_comparator": false,
  "gross_value_cents": 420000, "cash_outlay_cents": 50200,
  "points_cost_cents": 240000, "net_value_cents": 129800,
  "cash_received_cents": null, "realized_cpp_milli": 3081,
  "points_spent": { "amex_mr": 120000 }, "feasible_in_days": 0,
  "signature": "a41f…", "caveats": [
    { "code": "irreversible_transfer",
      "params": { "edge_id": "amex_mr__flying_blue", "points": 120000 },
      "text": "Transferring 120,000 Membership Rewards to Flying Blue cannot be undone." },
    { "code": "seats_limited",
      "params": { "offer_id": "fb_biz_nyc_par_oct26", "seats_available": 2, "passengers": 1 },
      "text": "fb_biz_nyc_par_oct26 shows only 2 seat(s) for 1 passenger(s)." },
    { "code": "seats_limited",
      "params": { "offer_id": "fb_biz_par_nyc_oct26", "seats_available": 2, "passengers": 1 },
      "text": "fb_biz_par_nyc_oct26 shows only 2 seat(s) for 1 passenger(s)." } ] }
```

```json
{ "plan_id": 9, "seq": 1, "kind": "transfer", "hop_index": 0,
  "from_program": "amex_mr", "to_program": "flying_blue",
  "edge_id": "amex_mr__flying_blue", "points_sent": 120000,
  "points_delivered": 120000, "fees_cents": 0, "eta_days": 0,
  "irreversible": true,
  "explanation": "Transfer 120,000 Membership Rewards to Flying Blue (1:1, instant, no fee).",
  "executed_at": null }

{ "plan_id": 9, "seq": 2, "kind": "book_award", "hop_index": 0,
  "from_program": "flying_blue", "to_program": null,
  "offer_id": "fb_biz_nyc_par_oct26", "points_sent": 60000,
  "points_delivered": null, "fees_cents": 25100, "eta_days": 0,
  "irreversible": true,
  "explanation": "Book Flying Blue business NYC->PAR (Oct 2026): 60,000 miles + $251.00 in taxes/fees. Realized ~3.08 cpp against a $2,100.00 reference fare.",
  "executed_at": null }

{ "plan_id": 9, "seq": 3, "kind": "book_award", "hop_index": 0,
  "from_program": "flying_blue", "to_program": null,
  "offer_id": "fb_biz_par_nyc_oct26", "points_sent": 60000,
  "points_delivered": null, "fees_cents": 25100, "eta_days": 0,
  "irreversible": true,
  "explanation": "Book Flying Blue business PAR->NYC (Oct 2026): 60,000 miles + $251.00 in taxes/fees. Realized ~3.08 cpp against a $2,100.00 reference fare.",
  "executed_at": null }
```

Math check, per FR-7b/FR-8. Aggregate need at `flying_blue` is
60,000 + 60,000 = 120,000 with an opening balance of 0, so **one** merged
transfer step funds both bookings (`fb_biz_nyc_par_oct26` matches the
outbound leg, `fb_biz_par_nyc_oct26` the return; both one-way fares for the
goal's month 2026-10 are $2,100).

- `gross = 2 × 210,000 = 420,000`
- `cash_outlay = 2 × 25,100 = 50,200` (edge fee 0)
- `points_cost = V(H0) − V(H1)`; the UR term (210,000 @ 2050) is unchanged
  and cancels, FB ends at 0, so it reduces to
  `(130,000×2000)//1000 − (10,000×2000)//1000 = 260,000 − 20,000 = 240,000`
- `net = 420,000 − 50,200 − 240,000 = 129,800` = **+$1,298 vs paying cash**
- pooled `realized_cpp_milli = (420,000 − 50,200) × 1000 // 120,000 = 3,081`
  → 3.08¢/pt
- `feasible_in_days = 0`; no `stranded_points` caveat because the FB balance
  ends at 0, but each 2-seat offer booked for 1 passenger fires `seats_limited`
  (`seats_available − passengers = 1 ≤ 1`, FR-10).

`ledger_entry` sequence after executing step 1:

```json
{ "program_id": "amex_mr", "delta_points": -120000, "post_balance": 10000,
  "reason": "transfer_out", "plan_step_id": 17, "note": null,
  "at": "2026-08-02T09:15:00Z" }

{ "program_id": "flying_blue", "delta_points": 120000, "post_balance": 120000,
  "reason": "transfer_in", "plan_step_id": 17, "note": null,
  "at": "2026-08-02T09:15:00Z" }
```
