# PointsMax — Scope

## One-liner

A single-user credit-card points and rewards maximizer: you tell it what cards
you hold, what balances you have, and what you want ("round-trip business
NYC→Paris in October", "maximize cash"), and a deterministic graph search over
a versioned rewards-world dataset (transfer partners, ratios, fees, transfer
times, award prices, portal rates, cents-per-point valuations) returns ranked,
step-by-step redemption plans with honest math — including when the right
answer is "pay cash and keep your points".

## Problem statement

Transferable points (Chase Ultimate Rewards, Amex Membership Rewards, Citi
ThankYou, Capital One, Bilt) can be cashed out at ~0.6–1.0 cents per point,
redeemed in issuer travel portals at 1.0–1.5, or transferred to airline and
hotel partners where a well-chosen award redeems at 2–6+ cents per point.
Finding the good path is genuinely hard: transfer ratios differ per partner
(1:1, 1:2, 3:1), transfers quantize to increments, some carry fees with caps
(Amex's 0.06¢/pt excise-offset fee, capped at $99), some carry tier bonuses
(Marriott adds 5,000 miles per 60,000 transferred), some take days to post,
all are **irreversible**, multi-hop chains exist (bank → Marriott → airline),
round-trips are often best booked as two one-way awards on *different*
programs, and every plan competes against the boring baselines (portal, cash
back, "just pay cash"). Hobbyists resolve this with spreadsheets, valuation
tables, and folklore; mistakes strand points in a devalued currency forever.

The hard parts, each with first-class eval gates (see EVALS.md):

- **A. Optimal, constraint-correct redemption-path search.** Given holdings,
  cards, and a goal, the engine must find the plan that truly maximizes net
  realized value over the transfer/redemption graph — respecting card gating,
  transfer increments, minimums, fee caps, bonus tiers, promo windows,
  transfer-time budgets *versus booking deadlines*, seat limits, and joint
  funding across multiple bookings from shared balances — and rank the
  alternatives correctly. A search that is merely "usually right" silently
  loses the user hundreds of dollars and cannot be trusted with irreversible
  moves.
- **B. Exact value accounting.** Realized cents-per-point, fees (including
  caps), tier bonuses, opportunity cost of points spent, and the cost of
  stranded leftovers must match hand-computed ground truth to the cent. The
  entire product is arithmetic advice; a one-off error anywhere makes every
  recommendation suspect.

## Target user

The owner: one person managing their own household's points across a handful
of issuers, planning at a terminal. Single local profile; no accounts, no
multi-tenancy, no balance scraping. The original idea's "understanding of the
user's current credit standing" was narrowed in the locked decisions to
explicit user state — holdings per currency + cards held + a goal spec; no
credit-score modeling and no card-acquisition advice (see Non-goals 4/5). The
idea's "benefits" clause is narrowed the same way (Non-goal 10): only benefits
expressible as redemption mechanics (portal rates, transfer access, rebates)
are modeled.

## User stories & acceptance criteria

**US-1 — Set up my wallet in minutes.** As the user, I add the cards I hold
and my current balances per program, and the tool knows which transfer edges
and redemption options I can actually use.
*Accept:* cards come from the committed catalog (`cards add
chase_sapphire_reserve`); balances are set per program and persist as an
append-only ledger; `wallet show` lists cards, balances, and for each balance
its baseline value, **cash floor**, and **travel floor** (FR-13); edges/options
requiring a card I do not hold are excluded from every plan (M2 validity gate:
violations = 0).

**US-2 — Tell me what my points are worth.** As the user, I can ask the value
of any balance and get three honest numbers: the baseline valuation (what a
reasonable redemption achieves), the **cash floor** (best *liquid* cash-out
available to me — statement credit / bank deposit only), and the **travel
floor** (best guaranteed-value redemption including the travel portal), with
the valuation's `as_of` date shown.
*Accept:* `value <program> <points>` returns all three in dollars using the
documented integer math (M4 gate); floors reflect card gating and quantization
(a CSR holder's UR travel floor is 1.5¢, their cash floor is the 1.0¢
statement credit — the portal is never reported as cash); output always
carries the valuation `as_of` date.

**US-3 — Plan my trip from plain words.** As the user, I type
`goal add "round-trip business NYC to Paris in October"` and get a ranked
list of concrete plans: which points to transfer where, what to book, total
fees, realized cents-per-point, and net value versus paying cash.
*Accept:* the offline parser produces the correct structured GoalSpec on the
committed utterance suite (M5 ≥ 0.90); on the eval scenario suite the top
plan matches the independent brute-force oracle's optimum exactly (M1 = 1.0)
and the full ranked output matches (M3 = 1.0); every plan step is executable
as written (amounts respect increments/minimums, and transfers land before
the booking deadline; M2 = 0 violations).

**US-4 — Be honest when points are a bad deal.** As the user, when the best
award play is worse than paying cash (or the portal beats a transfer), the
tool says so instead of flattering my balances.
*Accept:* every flight/stay plan set includes the best portal/cash comparator
plan when one is feasible, even below the top-K cutoff; when the best plan's
net value ≤ 0 the plan-set verdict is `pay_cash_keep_points` with the numbers
shown; cash-out plans below baseline valuation carry a `below_baseline`
caveat. Verdict logic is fixture-tested per scenario (part of the M3 gate).

**US-5 — Maximize cash.** As the user, I can ask for the best pure-cash
liquidation of some or all balances and get a plan ranked by cash actually
received, with the value-destroyed warning when cashing out under baseline.
*Accept:* cash plans use only **liquid** cashout options (`is_cash = true`:
statement credit, bank deposit — never the travel portal or gift cards) that
are active for my cards, respect minimums and increments, and match the
oracle's optimum on cash scenarios (in M1); the top plan covers every program
with a positive balance and an active liquid option.

**US-6 — Execute and stay in sync.** As the user, when I actually perform a
step ("transferred 60k UR → Flying Blue", "booked the award"), I record it
and my balances update exactly — including tier bonuses landing and fees
recorded.
*Accept:* `apply <plan-id> --step N` writes the corresponding ledger entries
atomically (transfer_out, transfer_in, transfer_bonus, redemption); steps
must be applied in order; irreversible steps (transfer, book_award) require
an explicit confirmation flag; replaying the ledger from zero reproduces
current balances byte-identically (D0 invariant test).

**US-7 — Warn me before I do something dumb.** As the user, every plan spells
out its risks: transfers are irreversible, this edge takes 2 days and the
offer's booking deadline is in 3, 700 points will be stranded in Flying Blue,
this promo ratio expires next week, the valuation data is 8 months old.
*Accept:* the caveat list is deterministic data (typed caveat codes with
explicit numeric thresholds + filled templates), asserted per fixture scenario
as part of the M3 gate; the stranding caveat's cent value is an M4-checked
expected field; a world dataset older than 180 days relative to `today` always
produces a `stale_world` caveat (one fixture scenario pins this).

**US-8 — Reproducible, versioned advice.** As the user, a saved plan pins the
world-dataset version it was computed against; recomputing the same goal on
the same world and `today` yields byte-identical output, and executing a plan
against a *different* world content hash is refused with a re-plan hint.
*Accept:* PlanSet stores `world_version` + `world_hash`; D0 determinism test
(byte-identical recompute) passes; content-hash-mismatch execution returns a
structured error.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-7/8/9 are
hard part A; FR-8/10 are hard part B.

- **FR-1 World dataset load & validation.** Load the committed rewards world
  from `data/world/` (programs, card products, transfer edges, cashout
  options, valuations, award offers, reference fares, gazetteer, version)
  into typed Pydantic models via the `WorldProvider` adapter. Validation at
  load — every violation is a named, actionable error; `world validate` and
  `init` both run it:
  1. unique ids; referential integrity across all files.
  2. `increment_from % ratio_from == 0` and `min_from % increment_from == 0`
     on every edge (delivered amounts are exact integers), and, when a tier
     bonus is set, `bonus_per_from % increment_from == 0` (so tier
     boundaries are themselves valid sent amounts — FR-7's lattice needs
     them).
  3. every program has exactly one Valuation.
  4. **No value-increasing transfer edge.** For every edge, the best
     achievable delivered value must not exceed the source value:
     without a tier bonus `ratio_to × mcpp_to ≤ ratio_from × mcpp_from`;
     with one `(ratio_to × bonus_per_from + ratio_from × bonus_to) × mcpp_to
     ≤ ratio_from × bonus_per_from × mcpp_from`. (Max of
     `delivered(s)/s` is `ratio_to/ratio_from + bonus_to/bonus_per_from`,
     attained at tier boundaries.) Rationale: transferable currencies are
     valued above their partners precisely because of flexibility; an edge
     that raises paper portfolio value is a data error, and this invariant is
     what makes the FR-7 candidate lattice finite (§decision 17).
  5. **Reference-fare coverage.** For every `(origin_city, dest_city, cabin)`
     appearing in any flight offer and every month `m` touched by that
     offer's travel window, `reference_fares.json` contains one-way rows in
     **both** directions and a round-trip row, all for month `m`. For every
     `city` in any stay offer, a stay row for every month touched. (This makes
     the FR-8 lookup — keyed on the *goal's* travel month — always resolvable,
     and prices portal bookings for either direction.)
  6. every award offer's origin/destination/city resolve in the gazetteer;
     airport codes unique across gazetteer entries.
  7. `version.json`'s `content_hash` matches the recomputed hash over the
     other world files (canonicalization in DATA_MODEL.md).
- **FR-2 Wallet state & ledger.** Cards held (unique per card product) and
  per-program balances maintained as an append-only ledger
  (`set`/`adjust`/`transfer_out`/`transfer_in`/`transfer_bonus`/
  `award_redeem`/`cash_redeem`/`portal_redeem`). Balance = ledger sum;
  every entry stores `post_balance`; balances never go negative (rejected at
  write). Replaying the ledger from empty reproduces stored balances exactly.
- **FR-3 Edge & option gating.** Given cards held and `today` (always an
  input), compute the *active* subgraph: transfer edges from bank programs
  require a held card with `enables_transfer` for that program; cashout
  options with `requires_card` need that card; edges/options with validity
  windows (promos) are active only when `valid_from ≤ today ≤ valid_to`.
  Pure function of (world, wallet, today); property-tested.
- **FR-4 Goal specification.** Structured `GoalSpec` with kinds `flight`
  (origin city, destination city, cabin or any, round_trip, passengers,
  travel month → window, optional `book_by` date), `stay` (city, nights,
  travel month), `cash` (optional program filter, optional per-program point
  cap). Validation: known city codes, passengers 1–8, nights 1–30, month
  resolvable. Goals persist with status lifecycle
  `active → planned → fulfilled | dropped`.
- **FR-5 Offline goal parsing.** `RuleBasedGoalParser` turns free text into a
  `GoalSpec` or a structured `ParseError` listing missing/ambiguous fields.
  Mechanics: gazetteer alias matching for cities ("NYC", "new york",
  "Paris"); cabin synonyms (economy/coach, premium economy/PE, business/biz,
  first); trip-type cues ("round-trip"/"RT"/"return" vs "one way");
  passenger counts ("for 2", "2 people"); month names resolve to the next
  occurrence on or after `today`'s month (today 2026-07-31 + "October" →
  2026-10; "March" → 2027-03); cash intent ("cash", "cash out",
  "statement credit"); stay intent ("hotel", "nights", "a week at").
  Missing origin defaults to the profile's `home_city` if set, else
  `ParseError`. Gate M5.
- **FR-6 Offer matching.** Deterministic predicate from `GoalSpec` to award
  offers: kind matches; offer origin/destination airports or city codes
  resolve (via gazetteer) to the goal's cities — for round-trip goals an
  offer may match the outbound direction, the return direction, or be a
  round-trip offer; cabin equals the goal cabin (a goal with `cabin = null`
  matches any cabin); travel windows overlap the goal window (stays: overlap
  ≥ nights); `passengers ≤ seats_available` when the offer caps seats;
  offer `bookable_until ≥ today` when present. (Arrival-time feasibility —
  can the funding transfers land before that deadline — is FR-7.)
- **FR-7 Booking sets & funding search (hard part A).**

  **(a) Booking-set enumeration.** A *booking* is a single purchase paid from
  one program's balance (decision 10). For a `flight` goal, define its legs:
  a one-way goal has one leg (origin→dest); a round-trip goal has two
  (out: origin→dest, ret: dest→origin). The **leg candidates** for a leg are
  every FR-6-matching award offer with `round_trip = false` for that
  direction, plus one *portal booking* per active `portal_travel` cashout
  option (priced by FR-8 from that leg's one-way reference fare). Booking
  sets are then:
  - every FR-6-matching award offer with `round_trip = true` (round-trip
    goals only), as a one-booking set;
  - one portal round-trip booking per active portal option (round-trip goals
    only), priced from the round-trip reference fare;
  - the Cartesian product of leg candidates over all legs — so a one-way goal
    yields every single one-way award and every single portal booking, and a
    round-trip goal yields award+award (possibly on *different* programs),
    award+portal, and portal+award mixes. **Mixing portal and award legs is
    explicitly allowed** (it is standard practice).

  For a `stay` goal: each FR-6-matching stay offer (`nights × points_price`),
  plus one portal stay booking per active portal option (priced from
  `nights × stay reference fare`). For a `cash` goal: FR-12.

  **(b) Need aggregation and edge merging.** Points are fungible within a
  program, so funding is modeled as flow over the active subgraph, not as
  independent chains: for each program `p` paying at least one booking in the
  set, the **aggregate need** `N_p = Σ(points prices of its bookings) −
  opening_balance_p`, floored at 0. All transfers over the same edge are
  merged into **one** transfer step whose `points_sent` is the total (this is
  both realistic and cost-minimal: fees are capped, hence concave, so merging
  never costs more; tier bonuses are superadditive at boundaries, so merging
  never delivers less).

  **(c) Funding search.** Recursive, memoized allocation
  `fund(program p, need n, remaining balances, hops left)` returning the
  minimum-loss inbound transfer set delivering ≥ `n` to `p`, where "loss" is
  the FR-8 portfolio-delta cost plus edge fees. Inbound candidates are the
  active edges into `p` whose source has (or can be funded within
  `hops left` ≤ `max_hops = 2` to have) a positive balance; no program is
  revisited on a path. For an inbound edge `e` from `q` with residual
  `r` and usable source balance `B`, the **candidate lattice** is
  ```
  L(e, r, B) = { s_cover }  ∪  { k × bonus_per_from : k ≥ 1 }  ∪  { s_max }
  ```
  intersected with the edge's *valid* amounts (multiples of
  `increment_from`, ≥ `min_from`, ≤ `s_max`), where `s_cover` = the smallest
  valid sent amount with `delivered_e(s) ≥ r`, and `s_max` = the largest
  valid sent amount ≤ `B`. Amounts above `s_cover` are never
  explored for the residual-covering edge (FR-1 invariant 4 makes extra
  sending weakly worse); `|L| ≤ 2 + B / bonus_per_from`. Completeness
  argument in decision 17; exactness is *gated*, not assumed (M1).

  Search over booking sets × allocations is branch-and-bound with:
  admissible lower bound `Σ_p remaining_deficit_p × min marginal loss per
  delivered point over the still-usable edges into p`; dominance pruning
  (state A prunes B when A's deficits ≤ B's, A's remaining balances ≥ B's,
  A's accumulated loss ≤ B's, and A's arrival days ≤ B's). The search is
  exact by construction and never approximates silently: on hitting the
  explicit expansion budget (`max_expansions`, default 200,000) it raises
  `SearchBudgetExceeded`. A `pruning=False` switch (evals only) disables the
  bound and dominance rules without changing results, so the eval suite can
  measure the unpruned baseline.

  **(d) Timing feasibility.** `arrival_days(p)` = 0 for a program funded
  entirely from its opening balance, else `max` over used inbound edges `e`
  of `arrival_days(source(e)) + e.time_days`. For every booking `b` paid from
  `p`, the plan is feasible only if
  ```
  today + arrival_days(p)  ≤  deadline(b)
  deadline(b) = min( goal.book_by,  offer.bookable_until,  offer.travel_window_end )
  ```
  over whichever of those are non-null (portal bookings use
  `goal.book_by` and the goal's `travel_window_end`). `travel_window_start`
  does **not** bind — booking after travel has begun is still valid for later
  dates in the window. `Plan.feasible_in_days` = max `arrival_days` over the
  set. Gates M1/M2/M6.
- **FR-8 Plan objective & value accounting (hard parts A+B).** All money in
  integer cents; all valuations in integer milli-cents per point (mcpp);
  baseline value of a holdings vector `V(H) = Σ_p (H_p × mcpp_p) // 1000`.
  Reference fares are looked up on the **goal's** travel month (FR-1
  invariant 5 guarantees a row exists): flights
  `(origin_city, dest_city, cabin, round_trip, goal travel month)`; stays
  `(city, goal travel month)`. For a plan taking holdings H0 to H1:
  - `gross_value` = Σ bookings' reference value (flight award or flight
    portal booking: `pax × ref_fare` for the booked leg or round trip; stay:
    `nights × nightly_ref`; cash: cash received);
  - `cash_outlay` = Σ award fees + Σ transfer fees, where a transfer step's
    fee is `min(fee_cap_cents, ceil(sent × fee_mcpp / 1000))` computed on the
    **merged** sent amount (FR-7b), so a cap applies once per edge;
  - `points_cost = V(H0) − V(H1)` (portfolio-delta accounting — this single
    formula prices opportunity cost, stranded leftovers at the *destination*
    program's valuation, and ratio effects, with no piecewise special cases);
  - `net_value = gross_value − cash_outlay − points_cost`.

  Per booking, `realized_cpp_milli = (value − booking fees) × 1000 //
  points_spent` — the standard hobbyist formula (cash price minus fees over
  points). **Plan-level** realized cpp is the *pooled* form, not an average
  of per-booking values:
  `realized_cpp_milli = (Σ booking value − Σ booking fees) × 1000 //
  Σ points spent on bookings`; null when no points are spent. Transfer fees
  live in net value, not in cpp (documented, matching hobby practice).
  Tier bonuses: `bonus_to × (sent // bonus_per_from)` per transfer step;
  `delivered(s) = s × ratio_to // ratio_from + bonus_to × (s //
  bonus_per_from)`, exact by FR-1 invariant 2. Portal booking points price:
  `ceil(fare_cents × 1000 / cpp_milli)` rounded up to the option's
  `increment` and at least `min_points`. Gate M4.
- **FR-9 Ranking, alternatives, verdict (hard part A).** One plan per
  feasible booking set (its optimal funding). **Canonical step order** (also
  the execution order, and `seq` is its 1-based index): sort by
  `(kind_rank, hop_index, from_program, to_program, edge_id, offer_id,
  cashout_id, points_sent)` where `kind_rank` is
  `transfer=0, book_award=1, book_portal=2, redeem_cash=3`, `hop_index` is
  the transfer's depth in the funding DAG (0 for transfers out of an opening
  balance), and null string fields sort as `""`. The **canonical step tuple**
  is `(kind, from_program, to_program, edge_id, offer_id, cashout_id,
  points_sent, points_delivered, fees_cents)` with nulls as `""`/`0`; a
  plan's **canonical form** is the list of its step tuples in canonical order.
  Ranking key: flight/stay goals by `net_value` desc; cash goals by
  `cash_received` desc; tie-break: fewer total points spent, then fewer
  steps, then canonical form ascending (lexicographic) — a total order, so
  output is byte-stable and any independent implementation reproduces it.
  `Plan.signature` = sha256 of the canonical form's canonical JSON
  (DATA_MODEL.md) and is identity/dedup metadata only, never a tie-break
  input. Top-K returned (default 5), plus the best portal/cash comparator
  appended (flagged `comparator`) if feasible and not already present.
  Plan-set verdict: `book_with_points` when the best plan has net_value > 0;
  `pay_cash_keep_points` when best ≤ 0; `insufficient_points` when offers
  match but none can be funded; `no_matching_award` when nothing matches;
  `cash_plan` for cash goals. Gate M3.
- **FR-10 Explanations & caveats (hard part B).** Every step renders a
  deterministic explanation from committed templates, every number in the
  text equal to the stored field (asserted in tests). Caveat production is a
  pure function of (plan, world, today, thresholds); thresholds are named
  constants passed in, defaults below:

  | Code | Fires when | Params |
  |---|---|---|
  | `irreversible_transfer` | any `transfer` step exists (one caveat per step) | `edge_id`, `points` |
  | `transfer_time_risk` | `deadline(b) − (today + arrival_days(p)) ≤ SLACK_DAYS` (default 2) for any booking | `program`, `arrival_days`, `deadline`, `slack_days` |
  | `stranded_points` | a program's ending balance in a program the plan transferred *into* is > 0 | `program`, `points`, `value_cents` (FR-8 mcpp math; an M4 expected field) |
  | `promo_expiring` | a used edge/option has `valid_to` and `valid_to − today ≤ PROMO_DAYS` (default 30) | `edge_id`/`option_id`, `valid_to`, `days_left` |
  | `below_baseline` | a cash/portal redemption's `cpp_milli` < the program's baseline `cpp_milli` | `program`, `option_id`, `option_cpp_milli`, `baseline_cpp_milli` |
  | `stale_world` | `today − valuations' min as_of > STALE_DAYS` (default 180) | `as_of`, `days_old` |
  | `seats_limited` | an offer has `seats_available` and `seats_available − passengers ≤ 1` | `offer_id`, `seats_available`, `passengers` |

  Caveats are emitted in `CaveatCode` declaration order, then by their params'
  canonical JSON, so the list is byte-stable.
- **FR-11 Plan persistence & execution loop.** Computing plans persists an
  immutable `PlanSet` (goal, `world_version` + `world_hash`, today, params)
  with ranked `Plan`s and ordered `PlanStep`s. Executing step k requires
  steps 1..k−1 executed, **the loaded world's `content_hash` to equal the
  pinned `world_hash`** (the version string is display metadata only and is
  not the guard), and `confirm_irreversible = true` for transfer/book_award
  steps; execution writes the FR-2 ledger entries atomically and stamps
  `executed_at` (caller-supplied time). Re-executing an executed step is a
  structured no-op error.
- **FR-12 Cash goal engine.** For `cash` goals, only **liquid** cashout
  options are eligible: methods with `is_cash = true` (`statement_credit`,
  `bank_deposit`). `portal_travel` and `gift_card` are excluded — they do not
  produce cash (DATA_MODEL "Cashout liquidity"). Per program (after optional
  filter/cap), choose the best active liquid option (cpp desc, then option
  id) over the redeemable amount respecting `min_points`/`increment`; the top
  plan combines every program with a positive redeemable balance.
  Alternatives swap per-program options and may include cash-improving
  transfer chains: the generic FR-7 funding search runs with the objective
  `cash_received` (in the shipped world direct options dominate; a named
  stress-world fixture scenario proves the general path). Below-baseline
  redemptions carry the `below_baseline` caveat.
- **FR-13 Quick valuation.** `value(program, points)` returns three numbers
  with provenance (option id / valuation `as_of`):
  - `baseline_value_cents = (points × mcpp) // 1000`;
  - `cash_floor_cents` — over active options with `is_cash = true`:
    `max((redeemable × cpp_milli) // 1000)` where `redeemable` is the largest
    multiple of `increment` ≤ `points` and ≥ `min_points`; 0 if none active
    (the unredeemable remainder is valued at 0 — that is the point of a
    floor);
  - `travel_floor_cents` — the same computation over **all** active options
    regardless of liquidity (so a CSR holder's UR travel floor is the 1.5¢
    portal).
  Pure function; used by `wallet show` totals.
- **FR-14 API.** FastAPI app per the sketch below; thin: validate,
  call services, serialize. All engine-relevant time comes in as request
  fields (`today`, `at`), defaulted at the edge.
- **FR-15 CLI.** Typer app per the sketch below; same services; interactive
  confirmation prompt (or `--yes-irreversible`) before executing
  irreversible steps.
- **FR-16 Determinism, hermeticity & safeguards.** Engine is pure: same
  (world, wallet, goal, today, params) → byte-identical PlanSet content; no
  network, clock, or filesystem reads inside the engine. Tests and evals use
  only offline adapters. Safeguards implemented as behavior, not prose:
  (a) every plan payload carries the fixed `disclaimer` field ("estimates
  from a versioned dataset, not financial advice; verify ratios and pricing
  with the program before moving points" — asserted present in tests);
  (b) irreversible-step confirmation gating (FR-11/15); (c) `stale_world`
  caveat (FR-10); (d) no card-acquisition recommendations exist anywhere —
  no endpoint, no advice strings.

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision); the API is
   shaped so a web planner is a pure client later.
2. **No live rate/valuation updates.** Per the locked decision, the rewards
   world ships as a committed, versioned dataset. The `WorldProvider`
   interface is frozen now; a `LiveWorldProvider` (remote feed refresh) is a
   future adapter. Staleness is surfaced, not silently fixed.
3. **No live award availability search.** Award space is dynamic; the
   real-world analog is a seats.aero-style API. MVP treats award offers as
   curated snapshots with travel windows and seat caps; the `AwardSource`
   interface is defined so a live adapter can slot in.
4. **No credit-score modeling.** "Credit standing" from the original idea was
   locked down to explicit holdings + cards + goal.
5. **No card-acquisition or churning advice.** Recommending new cards or
   welcome bonuses is adjacent-product territory (and finance-sensitive);
   explicitly out, enforced by FR-16(d).
6. **No earn-side optimization** ("which card do I swipe for groceries" —
   CardPointers/MaxRewards territory). Redemption-side only, per the
   original idea.
7. **No balance auto-sync** (AwardWallet-style scraping). Balances are
   manual, ledger-tracked.
8. **No cash+points hybrid awards, award change/cancel modeling, points
   expiry tracking, household pooling between people, or destination
   discovery** ("where can I go with 80k?") — all real, all deferred; the
   data model does not preclude them.
9. **No LLM in the deterministic loop.** The optional `LLMGoalParser` live
   adapter only parses text into the same validated GoalSpec; it never
   ranks, values, or explains, and is never on the eval path.
10. **No card-benefit modeling beyond redemption mechanics.** The owner's
    idea says "points *and benefits*"; this pass models only benefits that
    are redemption mechanics — portal earn rates, transfer access
    (`enables_transfer`), and pay-with-points rebates expressed as cashout
    options. Travel credits, lounge access, FHR/hotel-collection perks, elite
    status, and insurance are **out**: they are per-user, per-spend
    entitlements with no place in a redemption-path graph, and the locked
    decision's data model (currencies / edges / redemption options) is the
    agreed narrowing. `CardProduct.annual_fee_cents` is stored as display
    metadata only and never enters any objective.

## Architecture

```
projects/pointsmax/
  src/pointsmax/
    models.py            # Pydantic v2 entities + enums (DATA_MODEL.md)
    engine/
      world.py           # FR-1/FR-3: dataset validation, graph build, gating
      money.py           # FR-8: integer money/points primitives (floor/ceil rules)
      goals.py           # FR-4/FR-6: GoalSpec validation, offer matching
      parser.py          # FR-5: rule-based NL parsing over the gazetteer
      search.py          # FR-7: booking sets, candidate lattice, branch-and-bound
      value.py           # FR-8: portfolio-delta net value, realized cpp, fees, tiers
      plan.py            # FR-9/FR-10: canonical order, ranking, steps, templates, caveats
      advisor.py         # orchestration: (world, wallet, goal, today) -> PlanSet (pure)
    adapters/
      world_provider.py  # WorldProvider Protocol + CommittedWorldProvider
      award_source.py    # AwardSource Protocol + CommittedAwardSource
      fare_reference.py  # FareReference Protocol + CommittedFareReference
      goal_parser.py     # GoalParser Protocol + RuleBasedGoalParser
      goal_parser_llm.py # live: LLMGoalParser (optional extra)
    store/               # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/                 # FastAPI app
    cli/                 # Typer app
  data/world/            # committed dataset: programs.json, cards.json, transfers.json,
                         #   cashouts.json, valuations.json, awards.json,
                         #   reference_fares.json, gazetteer.json, version.json
  evals/                 # fixtures/, metrics.py, oracle.py, baselines.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default; tests/evals) | Live (env-gated) |
|---|---|---|
| `WorldProvider.load() -> World` (programs, cards, edges, cashouts, valuations, version) | `CommittedWorldProvider` — reads `data/world/`, runs FR-1 validation | `LiveWorldProvider` — **deferred** (Non-goal 2); would refresh from `POINTSMAX_WORLD_FEED_URL` and revalidate identically |
| `AwardSource.offers() -> list[AwardOffer]` | `CommittedAwardSource` — `data/world/awards.json` | `SeatsAeroAwardSource` — **deferred** (Non-goal 3); `POINTSMAX_SEATSAERO_KEY` |
| `FareReference.fare(kind, origin, dest, cabin, rt, month) -> cents \| None` | `CommittedFareReference` — `data/world/reference_fares.json` | `LiveFareReference` — **deferred**; flight-offers API analog (e.g. Amadeus Self-Service), `POINTSMAX_AMADEUS_KEY` |
| `GoalParser.parse(text, *, today, home_city) -> GoalSpec \| ParseError` | `RuleBasedGoalParser` — gazetteer + rules (FR-5) | `LLMGoalParser` — **in scope** as optional extra `llm`; Anthropic API via `ANTHROPIC_API_KEY`; output validated by the same Pydantic schema; never used in evals |

### API sketch (FastAPI)

```
GET  /health
GET  /world                          # version, as_of, entity counts, staleness
POST /world/validate
GET  /profile        PUT /profile                # {display_name, home_city, default_passengers}
GET  /programs        GET /programs/{id}      # incl. valuation + user's active edges/options
GET  /cards                                   # card-product catalog
GET  /wallet                                  # cards held, balances, baseline totals, both floors
POST /wallet/cards {card_product_id}          DELETE /wallet/cards/{card_product_id}
PUT  /wallet/balances/{program_id}            # {points, at} -> ledger 'set'
POST /wallet/balances/{program_id}/adjust     # {delta, reason, at}
GET  /wallet/ledger?program=&limit=
GET  /valuations                              # per program: baseline + this user's cash & travel floors
POST /goals                                   # structured GoalSpec | {text} (parser)
GET  /goals           GET /goals/{id}         PATCH /goals/{id}   # status transitions
POST /goals/{id}/plans                        # {today, top_k?, max_hops?} -> computed PlanSet
GET  /plan-sets/{id}                          GET /goals/{id}/plans?latest=1
GET  /plans/{id}                              # steps, math breakdown, caveats, disclaimer
POST /plans/{id}/steps/{seq}/execute          # {at, confirm_irreversible} -> ledger entries
```

### CLI sketch (Typer)

```
pointsmax init                                   # create DB, load + validate world
pointsmax world info|validate
pointsmax profile show | set [--home-city NYC] [--default-pax N] [--name NAME]
pointsmax cards list [--issuer chase]
pointsmax wallet show
pointsmax wallet add-card <card-id> | remove-card <card-id>
pointsmax wallet set <program> <points> | adjust <program> <delta> [--reason r]
pointsmax wallet ledger [--program p]
pointsmax value <program> <points>               # baseline, cash floor, travel floor
pointsmax goal add "<free text>" | goal add --kind flight --from NYC --to PAR
             --cabin business --rt --month 2026-10 [--pax 2] [--book-by DATE]
pointsmax goal list | show <id> | drop <id>
pointsmax plan <goal-id> [--top 5] [--today YYYY-MM-DD]   # prints ranked plans + verdict
pointsmax show <plan-id>                                  # full steps, math, caveats
pointsmax apply <plan-id> --step N [--yes-irreversible] [--at TS]
```

## Key design decisions & assumptions

1. **Deterministic exact search, not an LLM.** The "AI" in the original idea
   lands at the natural-language edge only (goal parsing). The core is exact
   combinatorial optimization because the domain *is* exact arithmetic over
   published rates — the successful real-world tools (AwardHacker, point.me,
   seats.aero) are databases plus search, not generative systems, and an
   irreversible-transfer recommender must be provably right, not plausible.
2. **Two-sided value framework, grounded in hobby practice.** Realized value
   uses the standard formula `cpp = (cash price − taxes/fees) / points` used
   by The Points Guy, Frequent Miler, and One Mile at a Time. Opportunity
   cost uses committed baseline valuations per program modeled on Frequent
   Miler's *Reasonable Redemption Values* and TPG's monthly points
   valuations (e.g. UR ≈ 2.05¢, MR ≈ 2.0¢, Flying Blue ≈ 1.3¢, Hyatt ≈
   1.7¢, Hilton ≈ 0.5¢ as of mid-2026) — the RRV concept also supplies the
   guardrail against the classic "6 cpp!" fallacy of valuing first-class at
   retail: reference fares are *reasonable* cash prices, not rack rates.
3. **Portfolio-delta net value.** `net_value = gross − cash_outlay −
   (V(H0) − V(H1))` prices every effect — opportunity cost, increment
   leftovers stranded at the destination's (usually lower) valuation, 1:2 or
   3:1 ratio effects, tier bonuses — through one formula instead of
   special-cased adjustments. This is the single most bug-resistant design
   choice in the product and is what M4 hand-computed cases pin down.
4. **The world is committed, versioned data with provenance.** Fixture-grade
   but realistic: ~18 programs, ~60 transfer edges, ~15 cards, ~80 award
   offers modeled on real mid-2026 mechanics; every record carries a
   `source_note`; `version.json` has semver + `as_of` + content hash.
   Staleness is surfaced (FR-10) — the honest alternative to pretending
   rates cannot drift. Live refresh is a frozen interface, future adapter
   (locked decision). M6 gates that the engine actually runs on it.
5. **Cards gate the graph.** Chase UR transfers to partners require holding
   a premium card (Sapphire Preferred/Reserve, Ink Business Preferred);
   portal rates depend on the card (CSP 1.25¢, CSR 1.5¢); Amex Business
   Platinum's 35% pay-with-points rebate is modeled as an effective
   ~1.54¢ portal option requiring that card. Modeled as
   `enables_transfer` on cards and `requires_card` on cashout options.
6. **Fees are per-edge (rate, cap) pairs**, grounded in Amex's excise-tax
   offset fee: 0.06¢ per point (60 mcpp) capped at $99, applied on transfers
   to US airline programs. Fee math uses ceiling division (conservative) and
   is computed on the merged per-edge sent amount (FR-7b), so combining two
   needs into one transfer correctly pays the cap once.
7. **Tier bonuses are step functions**, grounded in Marriott Bonvoy's
   +5,000 miles per 60,000 points transferred to airlines (on top of 3:1, in
   3,000-point increments). Tier boundaries are explicit candidate amounts in
   the FR-7 lattice because sending exactly 60,000 can beat sending 57,000.
8. **Hop cap = 2.** The only real-world multi-hop family is bank → hotel →
   airline (the "Marriott hub": both Chase and Amex transfer to Marriott,
   Marriott transfers to ~40 airlines including otherwise-unreachable ones
   like Alaska). Chains longer than two transfers do not exist in practice;
   the cap is config, not code.
9. **Round-trips may mix one-way awards across programs, and may mix award
   and portal legs** — standard maximizer practice (e.g. outbound on Flying
   Blue, return on Aeroplan; or award out, portal back). This forces *joint*
   funding optimization over shared source balances (greedy sequential
   funding is provably suboptimal when two bookings compete for the cheapest
   source); eval scenarios include exactly these conflicts.
10. **An award is paid from a single program's balance** (no cross-program
    pooling at checkout) — true of every real program; funding aggregates
    *into* that program via transfers, and per-program needs are summed
    before funding (FR-7b).
11. **Integer math conventions:** money in cents; valuations in milli-cents
    per point; baseline value floors (`// 1000`); fees ceil; portal points
    ceil then round up to increment; delivered transfer amounts exact by the
    FR-1 divisibility invariant. No floats anywhere in the engine.
12. **Canonical total ordering** (FR-9) is derived from data — canonical step
    tuples, not a hash — so an independent implementation (the eval oracle)
    reproduces the ranking without sharing serialization code. That is what
    lets M3 demand exact ranking matches instead of fuzzy correlation.
13. **Time is an input.** `today` drives promo windows, month resolution,
    staleness, arrival-vs-deadline feasibility, and `book_by` budgets; `at`
    timestamps ledger entries. Edges (CLI/API) may default from the system
    clock; the engine never reads it.
14. **Irreversibility is a first-class safeguard.** Every program treats
    outbound transfers as final, and "never transfer speculatively" is the
    core hobby maxim — hence confirmation-gated execution (FR-11),
    `irreversible_transfer` caveats on every transfer step, arrival-time
    feasibility against real booking deadlines (FR-7d), and stranding costs
    priced into net value rather than footnoted.
15. **Not-financial-advice is implemented behavior** (FR-16): disclaimer
    field asserted by tests, no card-acquisition surface, staleness caveats.
    This satisfies the workspace rule that finance-adjacent tools carry
    safeguards as behavior, not prose.
16. **Ledger-event-sourced balances** with replay invariant: manual edits,
    transfers, and redemptions all flow through one append-only mechanism, so
    plan execution and reality cannot drift apart silently.
17. **Search exactness strategy.** Bounded exhaustive branch-and-bound with
    an admissible lower bound and lossless dominance pruning over a finite
    candidate lattice (FR-7c). The completeness argument has three parts:
    - *Waste monotonicity.* By FR-1 invariant 4, delivered value never
      exceeds source value, and fees are non-decreasing in `sent`. So for the
      edge that covers a residual, no amount above `s_cover` can improve the
      objective — the lattice may stop there.
    - *Exchange argument for splits.* In an optimal multi-edge allocation, at
      most one used edge sends an amount that is neither `s_max` (source
      exhausted) nor a tier boundary: if two did, shifting one increment from
      the edge with the higher marginal loss per delivered point to the other
      keeps delivery ≥ need and does not raise loss, and can be repeated
      until one of them hits `s_max`, a tier boundary, or the residual.
      Hence `{s_cover} ∪ {tier boundaries} ∪ {s_max}` is complete.
    - *Fee-cap concavity.* Caps make per-edge fee concave, so splitting one
      program's need across two transfers on the *same* edge is never better
      than one merged transfer; FR-7b merges by construction, removing the
      case entirely.
    Exactness is *gated*, not assumed: M1 compares against an independent
    full-lattice brute-force oracle on 64 curated scenarios, and M7 does the
    same on 200 seeded randomized scenarios.
18. **Worlds are small, and the lattice is small.** ~18 programs; every
    transfer edge in the shipped world has `increment_from ≥ 1,000` (bank
    programs transfer in 1,000-point increments; Marriott in 3,000), so a 2M
    balance yields ≤ 2,000 valid amounts per edge for a *brute-force*
    enumerator — and the engine's need-based lattice (FR-7c) is far smaller:
    `≤ 2 + balance / bonus_per_from`, i.e. ≤ ~35 amounts even for a 2M
    Marriott balance. With ≤ 2 hops and ≤ 6 usable edges per destination,
    real goals sit well under the 200,000-expansion budget (M6 asserts this
    against the shipped dataset and records the observed counts). The budget
    errors loudly rather than degrading silently (FR-7c).
19. **The offline parser is rules + gazetteer, not ML.** Free-text goals in
    this domain are formulaic ("<trip type> <cabin> <A> to <B> in
    <month>"); a rule parser is deterministic, hermetic, and evaluatable
    (M5). The LLM adapter must emit the identical schema and is validated
    by the same code path.
20. **Sizing.** The 2,000–4,000-line mandate is measured over `src/` only.
    Estimate: engine ≈ 1,600 (search 500, value 250, plan 300, world 220,
    goals/parser 330); adapters ≈ 250; store ≈ 350; api ≈ 320; cli ≈ 320;
    models ≈ 300 → **≈ 3,150**. Outside that budget, and expected to be of
    comparable size: `tests/` ≈ 900–1,200 lines and `evals/` ≈ 900–1,300
    lines (oracle ≈ 350, independent validator ≈ 200, metrics ≈ 200,
    baselines ≈ 150, generators ≈ 200, run/gates ≈ 150), plus hand-authored
    fixture JSON (not counted as code). Fixture authoring — especially the 30
    hand-worked accounting cases and 6 hand-derived search cases — is the
    real schedule risk. **Scope valves, in order:** (1) drop the `llm`
    optional extra; (2) drop the `stay` goal kind (machinery is shared; only
    data and matching shrink); (3) reduce curated search scenarios 64 → 48
    and randomized scenarios 200 → 100 and re-derive baselines. Never cut:
    the funding search, the accounting rules, or any eval gate.
