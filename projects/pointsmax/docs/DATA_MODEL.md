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
| `annual_fee_cents` | int | informational only (no card advice — FR-16d) |
| `source_note` | str | |

Invariant: `program_id.kind == bank`. Portal rates are *not* stored here —
they are CashoutOptions with `requires_card`.

### TransferEdge — `transfers.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `amex_mr__flying_blue` |
| `from_program`, `to_program` | str FK | no self-loops |
| `ratio_from`, `ratio_to` | int ≥ 1 | e.g. 1:1, 1:2 (Amex→Hilton), 3:1 (Marriott→airlines); delivered = `sent × ratio_to / ratio_from` |
| `min_from` | int | minimum points per transfer (e.g. 1000) |
| `increment_from` | int | sent must be a multiple (e.g. 1000; Marriott 3) |
| `fee_mcpp` | int | milli-cents per *from*-point; 0 for most; 60 for Amex→US airlines (0.06¢/pt) |
| `fee_cap_cents` | int \| null | e.g. 9900 ($99 Amex cap); null = uncapped |
| `time_days` | int | 0 = instant; e.g. Amex→ANA 2, Marriott→airlines 2 |
| `bonus_per_from` | int \| null | tier size, e.g. 60000 (Marriott) |
| `bonus_to` | int \| null | bonus delivered per full tier, e.g. 5000; both null or both set |
| `valid_from`, `valid_to` | date \| null | promo window; null = evergreen (FR-3) |
| `source_note` | str | |

Invariants: `(from_program, to_program, valid_from)` unique;
`increment_from % ratio_from == 0` and `min_from % increment_from == 0`
(delivered amounts exact — FR-1); `from_program.kind == bank` edges are
gated by `enables_transfer` cards; bonus fields paired.

### CashoutOption — `cashouts.json`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `ur_portal_csr` |
| `program_id` | str FK | |
| `method` | CashoutMethod | `portal_travel` options can also fulfill trip goals (`book_portal` steps) |
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
| `as_of` | date | drives `stale_world` caveat (FR-10) |
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
| `travel_window_start`, `travel_window_end` | date | availability window (FR-6) |
| `seats_available` | int \| null | max passengers; null = uncapped |
| `bookable_until` | date \| null | booking deadline for the offer itself |
| `source_note` | str | e.g. award-chart / typical-pricing provenance |

Invariant: every offer is covered by ReferenceFares for each month its
travel window touches (FR-1).

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

Lookup key (FR-8): flights `(origin_city, dest_city, cabin, round_trip,
month of travel_window_start)`; a one-way booking uses the one-way fare row
(committed per direction). Stays: `(city, month)`. Missing fare at plan time
is impossible by the FR-1 coverage invariant.

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
| `version` | str | semver of the dataset, e.g. `1.0.0` |
| `as_of` | date | data snapshot date |
| `content_hash` | str | sha256 over the canonical serialization of all world files; recomputed and checked at load (FR-1) |

## SQLite entities

### Profile — table `profile` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1` |
| `display_name` | str | |
| `home_city` | str \| null | gazetteer city code; parser default origin (FR-5) |
| `default_passengers` | int | default 1 |
| `created_at`, `updated_at` | str | ISO ts, caller-supplied |

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
| `travel_window_start`, `travel_window_end` | date \| null | resolved month → first/last day (FR-5) |
| `book_by` | date \| null | transfer-time budget anchor (FR-7) |
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
| `world_version` | str | pinned `WorldVersion.version` |
| `world_hash` | str | pinned `content_hash`; execution requires match (FR-11) |
| `today` | date | the input date used (FR-16) |
| `params` | JSON | `{top_k, max_hops, max_expansions}` |
| `verdict` | Verdict | FR-9 |
| `recommended_plan_id` | int FK \| null | rank-1 plan when verdict is book_with_points / cash_plan |
| `disclaimer` | str | fixed FR-16 string, asserted in tests |
| `created_at` | str | ISO ts |

### Plan — table `plan` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `plan_set_id` | int FK | |
| `rank` | int | 1-based; unique `(plan_set_id, rank)` |
| `is_comparator` | bool | appended portal/cash comparator beyond top-K (FR-9) |
| `gross_value_cents` | int | FR-8 |
| `cash_outlay_cents` | int | award/booking fees + transfer fees |
| `points_cost_cents` | int | `V(H0) − V(H1)` |
| `net_value_cents` | int | `gross − outlay − points_cost` (ranking key for flight/stay) |
| `cash_received_cents` | int \| null | cash goals (ranking key) |
| `realized_cpp_milli` | int \| null | points-weighted over bookings (FR-8); null when no points spent |
| `points_spent` | JSON {program: int} | consumed per program |
| `feasible_in_days` | int | max cumulative chain time (0 = all instant) |
| `signature` | str | stable hash of ordered step tuples; unique per plan_set; final tie-break |
| `caveats` | JSON list[{code: CaveatCode, params: {…}, text: str}] | FR-10; text rendered from committed templates |

### PlanStep — table `plan_step`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `plan_id` | int FK | |
| `seq` | int | 1-based; unique `(plan_id, seq)`; execution strictly in order (FR-11) |
| `kind` | StepKind | |
| `from_program`, `to_program` | str \| null | transfer: both; book_award/redeem_cash/book_portal: `from_program` only |
| `edge_id` | str \| null | transfer: the TransferEdge used |
| `offer_id` | str \| null | book_award: the AwardOffer |
| `cashout_id` | str \| null | redeem_cash / book_portal: the CashoutOption |
| `points_sent` | int | transfer: sent; bookings: points spent |
| `points_delivered` | int \| null | transfer only: base delivery + tier bonus |
| `fees_cents` | int | edge fee or award fees for this step |
| `eta_days` | int | this step's posting time |
| `irreversible` | bool | true for `transfer` and `book_award`; gates confirmation (FR-11) |
| `explanation` | str | deterministic template render; every number equals a stored field (FR-10) |
| `executed_at` | str \| null | set once on execution; ledger entries reference this step |

Invariants: kind-specific nullability; executing writes the matching ledger
entries atomically (transfer → `transfer_out` + `transfer_in` + optional
`transfer_bonus`; book_award/book_portal/redeem_cash → one redemption
entry); a step executes at most once; execution re-validates balances and
world hash at apply time.

## Relationships (summary)

```
World (committed, read-only):
  Program (18) ◀── CardProduct.program_id
  Program ◀─from/to── TransferEdge (~60)      Program ◀── CashoutOption (~25)
  Program ◀── Valuation (1:1)                 Program ◀── AwardOffer (~80)
  AwardOffer ▶ covered-by ▶ ReferenceFare     Gazetteer ◀ resolves offers & goals
  WorldVersion pins it all (content_hash)

SQLite (user state):
  Profile (1)     WalletCard (N, unique product)
  LedgerEntry (N, append-only) ──▶ PlanStep (0..1)
  Goal (N) ──▶ PlanSet (0..N, immutable) ──▶ Plan (1..K+1) ──▶ PlanStep (1..N)
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
  "ratio_from": 3, "ratio_to": 1, "min_from": 3000, "increment_from": 3,
  "fee_mcpp": 0, "fee_cap_cents": null, "time_days": 2,
  "bonus_per_from": 60000, "bonus_to": 5000,
  "valid_from": null, "valid_to": null,
  "source_note": "Bonvoy 3:1 airline transfers, +5k miles per 60k" }

{ "id": "amex_mr__delta_skymiles",
  "from_program": "amex_mr", "to_program": "delta_skymiles",
  "ratio_from": 1, "ratio_to": 1, "min_from": 1000, "increment_from": 1000,
  "fee_mcpp": 60, "fee_cap_cents": 9900, "time_days": 0,
  "bonus_per_from": null, "bonus_to": null,
  "valid_from": null, "valid_to": null,
  "source_note": "Amex excise-tax offset fee: 0.06 cents/pt, $99 cap, US airlines" }
```

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
```

`award_offer` and its `reference_fare`:

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

`plan` with three steps (fund Flying Blue from MR, book outbound + return
one-way awards; holdings before: MR 130,000 @ 2000 mcpp, UR 210,000 @ 2050
mcpp, FB 0 @ 1300 mcpp):

```json
{ "plan_set_id": 5, "rank": 1, "is_comparator": false,
  "gross_value_cents": 420000, "cash_outlay_cents": 50300,
  "points_cost_cents": 240000, "net_value_cents": 129700,
  "cash_received_cents": null, "realized_cpp_milli": 3080,
  "points_spent": { "amex_mr": 120000 }, "feasible_in_days": 0,
  "signature": "a41f…", "caveats": [
    { "code": "irreversible_transfer",
      "params": { "edge_id": "amex_mr__flying_blue", "points": 120000 },
      "text": "Transferring 120,000 Membership Rewards to Flying Blue cannot be undone." } ] }
```

```json
{ "plan_id": 9, "seq": 1, "kind": "transfer",
  "from_program": "amex_mr", "to_program": "flying_blue",
  "edge_id": "amex_mr__flying_blue", "points_sent": 120000,
  "points_delivered": 120000, "fees_cents": 0, "eta_days": 0,
  "irreversible": true,
  "explanation": "Transfer 120,000 Membership Rewards to Flying Blue (1:1, instant, no fee).",
  "executed_at": null }

{ "plan_id": 9, "seq": 2, "kind": "book_award",
  "from_program": "flying_blue", "to_program": null,
  "offer_id": "fb_biz_nyc_par_oct26", "points_sent": 60000,
  "points_delivered": null, "fees_cents": 25100, "eta_days": 0,
  "irreversible": true,
  "explanation": "Book Flying Blue business NYC->PAR (Oct 2026): 60,000 miles + $251.00 in taxes/fees. Realized ~3.08 cpp against a $2,100.00 reference fare.",
  "executed_at": null }

{ "plan_id": 9, "seq": 3, "kind": "book_award",
  "from_program": "flying_blue", "to_program": null,
  "offer_id": "fb_biz_par_nyc_oct26", "points_sent": 60000,
  "points_delivered": null, "fees_cents": 25100, "eta_days": 0,
  "irreversible": true,
  "explanation": "Book Flying Blue business PAR->NYC (Oct 2026): 60,000 miles + $251.00 in taxes/fees. Realized ~3.08 cpp against a $2,100.00 reference fare.",
  "executed_at": null }
```

(Math check for the example, per FR-8: gross = 2 × 210,000 = 420,000;
outlay = 2 × 25,100 = 50,300; points_cost = V(H0) − V(H1) =
(130,000×2000)//1000 − (10,000×2000)//1000 = 260,000 − 20,000 = 240,000;
net = 420,000 − 50,300 − 240,000 = 129,700 = **+$1,297 vs paying cash**;
realized cpp = (420,000 − 50,300) × 1000 // 120,000 = 3,080 → 3.08¢/pt.)

`ledger_entry` sequence after executing step 1:

```json
{ "program_id": "amex_mr", "delta_points": -120000, "post_balance": 10000,
  "reason": "transfer_out", "plan_step_id": 17, "note": null,
  "at": "2026-08-02T09:15:00Z" }

{ "program_id": "flying_blue", "delta_points": 120000, "post_balance": 120000,
  "reason": "transfer_in", "plan_step_id": 17, "note": null,
  "at": "2026-08-02T09:15:00Z" }
```
