# PointsMax — Evals

## What this product lives or dies on

1. **Optimal, constraint-correct redemption-path search** (FR-7/8/9): given
   holdings, cards, and a goal, the engine must find the plan that truly
   maximizes net realized value — through multi-hop chains, transfer
   increments and minimums, fee caps, tier bonuses, promo windows, arrival
   time versus booking deadlines, and joint funding of multiple bookings from
   shared balances — and rank alternatives correctly, emitting only
   executable plans, **at the scale of the shipped dataset**. The product's
   recommendations trigger irreversible point transfers; "usually right" is
   disqualifying, and "right but too slow to answer" is equally fatal.
2. **Exact value accounting and honest warnings** (FR-8/10): net value,
   realized cents-per-point, fees with caps, tier bonuses, and stranded-point
   costs must match hand-computed ground truth to the cent, and the typed
   caveats that warn about irreversibility, timing, stranding, staleness and
   below-baseline cash-outs must fire exactly when FR-10 says they do. Every
   sentence the product emits is an arithmetic claim or a safety claim.

Supporting (gated, but smaller): the offline goal parser (FR-5) — the front
door of the loop. Everything else (CRUD, ledger plumbing, CLI rendering) is
covered by ordinary tests, not eval gates.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with offline adapters (`CommittedWorldProvider`,
`CommittedAwardSource`, `CommittedFareReference`, `RuleBasedGoalParser`,
in-memory store). No network, no clock (every scenario carries its `today`),
fixed seeds. M-numbers map to gates in `evals/test_gates.py`; test names
reference FR ids.

### M1 — Plan optimality vs oracle (capability 1)

Over the 64 curated search scenarios (each: world + cards + opening balances
+ goal + `today` + params, with oracle-established expected results):

```
M1a = (# small scenarios where the engine's objective value == the oracle's) / 52
M1b = (# stress scenarios where the engine's objective value == the oracle's) / 12
```

"Objective value" is `net_value_cents` of the top plan for flight/stay goals
and `cash_received_cents` for cash goals (FR-9). Scenarios where the oracle
finds **no** feasible plan match only if the engine also emits none *and*
reports the same verdict (`insufficient_points` / `no_matching_award`). A
`SearchBudgetExceeded` error counts as a miss. The oracle is an independent
full-lattice brute-force solver (see Fixture strategy) — agreement is
meaningful because the two implementations share no search, pruning, or
serialization code.

### M2 — Emitted-plan validity (capability 1)

Every plan emitted across all 64 scenarios (top-K + comparators) is
re-checked by an independent validator in `evals/metrics.py` that reads the
raw world files directly (never the engine's loaded models):

- card gating honored (transfer edges and `requires_card` options);
- promo/availability windows active at the scenario's `today`;
- sent amounts respect `min_from`/`increment_from`; at most one transfer step
  per `(plan, edge_id)` (FR-7b merging);
- delivered amounts and tier bonuses recomputed exactly;
- fees recomputed on the merged sent amount, caps applied once per edge;
- balances never negative when steps are applied in `seq` order;
- **arrival-vs-deadline**: for each booking paid from program `p`,
  `today + arrival_days(p) ≤ min(book_by, offer.bookable_until,
  offer.travel_window_end)` over the non-null bounds (FR-7d);
- `passengers ≤ seats_available`; each booking paid from a single program;
- cash goals use only `is_cash = true` cashout methods (FR-12);
- steps are in FR-9 canonical order and `seq` matches that order.

```
M2 = ( Σ_scenarios valid_s ) / ( Σ_scenarios expected_s )
```

where `expected_s` is the oracle's expected plan count for the scenario and
`valid_s` is the number of emitted plans passing every check, capped at
`expected_s` — and `valid_s = 0` for the whole scenario if *any* emitted plan
fails a check. Emitting fewer plans therefore lowers M2 rather than
flattering it, and a scenario the oracle expects to be non-empty but the
engine leaves empty scores 0.

### M3 — Ranking, verdict & caveat exactness (capabilities 1+2)

Per scenario, compare the engine's full ranked output against the fixture's
expected output on four things:

1. the ordered list of **canonical plan forms** (each plan's canonical step
   tuples in canonical step order, per FR-9) — structural comparison, not
   hash comparison, so no serialization code is shared between engine and
   oracle;
2. `is_comparator` flags, in order;
3. the plan-set `verdict`;
4. per plan, the **multiset of caveat codes plus their key params**
   (`stranded_points.points`/`.program`/`.value_cents`,
   `transfer_time_risk.arrival_days`/`.deadline`, `promo_expiring.valid_to`,
   `below_baseline.option_id`, `seats_limited.seats_available`,
   `stale_world.days_old`).

```
M3 = (# scenarios where all four match) / 64
```

Ranking/plan truth comes from the oracle; **caveat truth is hand-authored in
the fixture** (ground truth by construction), so the oracle does not
duplicate FR-10 logic and cannot share its blind spots.

### M4 — Value-accounting exactness (capability 2)

Over 30 hand-computed accounting cases (each a fully specified plan +
holdings + world fragment, with expected `gross_value_cents`,
`cash_outlay_cents`, `points_cost_cents`, `net_value_cents`, the **pooled**
`realized_cpp_milli` (FR-8), per-step fee/bonus/delivery numbers, and — for
the 8 stranding cases — the expected `stranded_points.value_cents` caveat
parameter, all worked out by hand in the case's `rationale` field):

```
M4 = (# cases where every expected number matches exactly) / 30
```

### M5 — Goal-parser exactness (supporting)

Over 60 committed utterances with ground-truth `GoalSpec` (or ground-truth
`ParseError` naming the missing/ambiguous fields):

```
M5 = (# utterances where parse output == truth, field-for-field) / 60
```

### M6 — Shipped-world operability (capability 1, no oracle)

The fixture worlds are deliberately small; this gate is the only thing that
proves the engine is usable on the dataset the product actually ships. Run
the 12 committed goals in `fixtures/shipped_world_goals.json` — each with its
own fixed `today`, wallet, and balances, and including at least one
2-passenger round-trip needing multi-hop funding, one multi-source split, one
mixed award/portal round trip, and one cash goal — against
`data/world/` (loaded through `CommittedWorldProvider`, FR-1 validation on).
A goal **passes** when all of:

(a) no `SearchBudgetExceeded`, and `PlanSet.expansions ≤ max_expansions`;
(b) every emitted plan passes the M2 independent validator;
(c) recomputing the PlanSet yields byte-identical serialized content.

```
M6 = (# of the 12 goals passing (a)+(b)+(c)) / 12
```

Additionally asserted (not part of the ratio, but a hard gate assertion):
at least 9 of the 12 goals emit ≥ 1 plan, so the metric cannot be satisfied
by a world in which nothing matches. `run.py` prints each goal's expansion
count and wall-clock time; wall-clock is **reported, never gated** (it is not
deterministic). This gate needs no oracle and also catches dataset
regressions that make the shipped world unplannable.

### M7 — Randomized differential optimality (capability 1)

52+12 hand-composed scenarios are thin coverage for a combinatorial search;
an unsound bound or over-aggressive dominance rule can miss all of them.
`generate_random_cases.py --seeds 1..10` synthesizes 10 × 20 = 200 small
random scenarios (random programs, edges with random ratios/increments/
fees/caps/tiers/times, random balances, random goals) inside the
oracle-tractability budget below, solves each with `oracle.py`, and commits
them to `random_cases.json` with expected objective value, expected plan
count, and expected canonical form of the top plan. The gate runs the
**engine only** against the committed answers, so it is fast and hermetic:

```
M7 = (# of 200 random scenarios where the engine's objective value,
       plan count, and top-plan canonical form match the committed oracle
       answer) / 200
```

Seeds and the generator are committed; regeneration is byte-identical and
diffed in CI (see Fixture strategy).

### D0 — Determinism & state integrity (plain pytest, no score)

(a) Recompute the PlanSet for 5 designated scenarios twice: serialized
output must be byte-identical, and each plan's `signature` must be stable
across runs (FR-16/FR-9). (b) Execute a fixture plan's steps, then replay the
full ledger from zero: balances and every `post_balance` must reproduce
exactly (FR-2). (c) Executing a step against a world whose `content_hash`
differs from the PlanSet's pinned `world_hash` must fail with the structured
version-mismatch error — including the case where the semver `version` string
is unchanged but file contents differ (FR-11). Any failure fails the suite.

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable
byte-identically by committed seeded scripts. **Ground truth never comes
from the engine under evaluation**: search optima come from an independent
brute-force oracle; accounting truth and caveat truth are hand-computed;
parser truth is by construction. Eval worlds are synthetic-but-realistic
(real mechanics — gating, 3:1 ratios, $99 fee caps, 60k tiers, 2-day posting
times — with made-up balances and prices) so truth is fully determined by the
fixture, not by the shipped `data/world/` snapshot, which evolves
independently and is gated separately by M6.

### Oracle-tractability budget (fixture design invariant)

The oracle is exhaustive and unpruned; that is only meaningful if it is also
*feasible*. Every scenario in `search_cases.json` and `random_cases.json`
must satisfy, and `generate_search_cases.py` / `generate_random_cases.py`
**assert**, all of:

- ≤ 60 valid sent amounts per edge, i.e. `min(source balance, need-covering
  max) / increment_from ≤ 60`;
- ≤ 3 programs with a positive opening balance;
- ≤ 3 destination (paying) programs and ≤ 2 bookings per booking set;
- ≤ 10 active edges reachable within `max_hops`;
- ≤ 12 candidate booking sets;
- the oracle's total enumeration count for the scenario ≤ 2,000,000, recorded
  in the fixture as `oracle_enumerations` and printed by `run.py` (max and
  total).

These bounds are what keeps full regeneration (64 curated + 200 random + the
self-check) under ~5 minutes. Any future oracle optimization must be
**lossless** — memoization and canonical-state deduplication only, never
bounds-based or dominance pruning — because M1/M7's independence argument
rests on the oracle having no pruning logic to get wrong.

### Files

| File | Contents | Ground truth |
|---|---|---|
| `worlds/small_a..d.json`, `worlds/stress_a.json` | 4 small worlds (5–8 programs, 8–15 edges, 6–12 offers) + 1 stress world (12 programs, ~30 edges incl. active and expired promos, ~25 offers). Schema-identical to `data/world/`, and each passes FR-1 validation (including invariant 4 and reference-fare coverage). | n/a (inputs) |
| `search_cases.json` | **64 scenarios**: world ref, cards held, opening balances, goal, `today`, params; expected: optimal objective value, full ranked plan list (canonical forms, values, comparator flags), verdict, per-plan caveat codes **and key params**, `oracle_enumerations`. Composition — 52 small: 6 straightforward single-source direct transfers; 8 multi-hop-required (target reachable, or cheapest, only via a hotel-hub chain); 8 multi-source splits (no single balance covers the deficit); 6 fee/tier boundary cases (Amex $99 cap straddles incl. one where merging two bookings' needs into a single transfer beats two transfers, Marriott 60k tier straddles); 6 round-trip pairs with shared-source conflicts (greedy sequential funding is suboptimal by construction, incl. one mixed award+portal round trip); 6 cash goals (liquid-method filter — a CSR-holding scenario where the 1.5¢ portal must be *rejected* for a cash goal — plus gating, min/increment quantization, below-baseline verdicts); **2 deadline-binding** (a cheaper plan exists but its 2-day transfer would land after `offer.bookable_until` / `travel_window_end`, so the correct answer is the more expensive instant plan; greedy fails only on this constraint); **3 negative-verdict** (2 `insufficient_points` — offers match, nothing fundable; 1 `no_matching_award`); **1 stale-world** (valuations `as_of` > 180 days before the scenario's `today`; every plan must carry `stale_world`); **6 hand-derived** (small enough that the full optimal plan — steps, amounts, ranking — is worked out by hand in a `rationale` field, exactly as `accounting_cases` does for arithmetic). 12 stress scenarios combine several mechanics at once and must include ≥ 1 cash goal where a transfer chain beats every direct liquid cashout, ≥ 1 mixed award/portal round trip, and ≥ 1 fee-cap merging case. 24 of the 64 additionally embed a cpp-vs-net-value ranking conflict (a high-cpp small redemption vs a lower-cpp higher-net plan) to break cpp-first rankers. | `generate_search_cases.py --seed 20260731` runs `oracle.py`: an independent exhaustive solver enumerating every booking set (single one-way award, single portal booking, round-trip award, one-way pairs incl. award+portal mixes, stays, cash-option choices), every active edge subgraph within `max_hops`, and every sent amount on the **full** increment lattice from `min_from` to balance, scoring with its own straightforward FR-8 arithmetic and its own FR-9 canonical ordering. No pruning, no shared search or serialization code. **Generator self-checks, all must pass before scenarios are written:** (1) the oracle reproduces all 30 `accounting_cases.json` expected values; (2) the oracle reproduces all 6 hand-derived scenarios' hand-worked plans exactly; (3) the M2 independent validator (a third implementation, reading raw world files) passes on **every** oracle-expected plan, so the oracle's feasibility semantics are anchored outside itself; (4) the tractability budget holds. CI re-runs the generator and diffs. |
| `random_cases.json` | **200 scenarios** from 10 committed seeds × 20, generated by `generate_random_cases.py`, each within the tractability budget; expected objective value, plan count, and top-plan canonical form. Purpose: convert "exact on 52 curated cases" into "exact on the scenario distribution" and defeat fixture-answer memorization (M7). | Same `oracle.py`, same self-checks (3) and (4). |
| `accounting_cases.json` | 30 cases: fixed plan + holdings + world fragment + expected numbers + a human-readable `rationale` showing the arithmetic. Composition: 8 pure-formula (multi-currency funding, portal point math, per-booking *and* pooled realized cpp per FR-8); 8 fee cases (cap boundary at exactly 165,000 pts × 60 mcpp = $99, one above, one below, ceiling-rounding edges, one merged-amount case where the cap applies once); 6 tier cases (57,000 vs 60,000 vs 120,000 through Marriott-style 3:1 +5k/60k in 3,000-pt increments); 8 stranding/increment cases (leftovers credited at destination mcpp, incl. a 1:2-ratio case where destination value per source point exceeds face intuition) — each of the 8 also carries the expected `stranded_points.value_cents`. | Hand-computed; every case's `rationale` shows the full working so review can re-derive it |
| `parser_cases.json` | 60 utterances + truth. Composition: 25 plain-form trip requests; 15 alias/gazetteer forms ("nyc", "new york city", "Paris", airport codes); 10 relative-month cases pinned to each case's `today` ("October" before/after October, "March" wrapping the year); 5 cash/stay intents ("turn everything into cash", "a week at a hotel in Paris in December"); 5 negative cases whose truth is a `ParseError` with named missing fields ("business class somewhere warm", "next spring"). | By construction (each case authored with its spec/error) |
| `shipped_world_goals.json` | 12 goals against `data/world/` for M6: wallet (cards + balances), goal spec, fixed `today`, params. Includes a 2-pax round trip needing hub funding, a multi-source split, a mixed award/portal round trip, a stay, and a cash goal. | n/a — M6 asserts operability, validity and determinism, not optimal values, so no oracle run on the shipped world is needed |
| `exec_scripts.json` | For D0: 2 scenario refs + the step-execution sequences and `at` timestamps to apply | n/a |

## Naive baselines and gates

Baselines are **implemented and measured**, not estimated: `evals/baselines.py`
contains a greedy planner (single cheapest-mcpp source, direct edges only,
need-sized amounts, sequential per booking) with `quantize`/`gating` switches,
a cpp-first re-ranker over the engine's emitted plans, a keyword-only parser,
a fee/tier/stranding-ignoring calculator, and the engine run with
`pruning=False` (the "exact but unpruned" degenerate implementation). `run.py`
prints each baseline's measured score next to the engine's, and
`test_gates.py` asserts each baseline stays **at or below its ceiling** — so
fixture-composition drift that erodes the engine-vs-baseline gap fails the
suite mechanically instead of relying on review.

| Metric | Naive baseline (implemented in `evals/baselines.py`) | Measured baseline score | Baseline ceiling (asserted) | Gate | Rationale |
|---|---|---|---|---|---|
| M1a optimality (small, 52) | greedy planner | 0.346 (18/52: the 6 straightforward, the easy cash goals, the 3 negative-verdict and the 1 stale-world scenario, plus the single-source cases where greedy coincides with the optimum) | ≤ 0.45 | **= 1.0** | The FR-7 search is exact by construction (complete candidate lattice + admissible bound + lossless dominance, argued in SCOPE decision 17), fixtures fit the expansion budget, and the oracle is independent — any mismatch is a real bug, and partial credit would hide it. |
| M1b optimality (stress, 12) | greedy planner | 0.083 (1/12) | ≤ 0.25 | **= 1.0** | Stress worlds exist to catch complexity blowups and pruning bugs at scale; they are sized within the documented budget, so exactness remains fair. |
| M2 plan validity | greedy with `quantize=False, gating=False` | 0.393 (53/135 oracle-expected plans) | ≤ 0.85 | **= 1.0** | An invalid plan tells the user to make a transfer the program will reject, or one that lands after the booking deadline and strands the points. Zero tolerance is the product premise; the validator is a third implementation reading raw files. |
| M3 ranking + verdict + caveats | cpp-first re-ranker (correct plans, wrong objective; caveats as emitted) | 0.516 (33/64: fails the built-in cpp-vs-net conflicts plus verdict misses where cpp is high but net ≤ 0) | ≤ 0.70 | **= 1.0** | Ranking is a deterministic formula with a canonical total order derivable from data by any implementation; caveat firing is a pure function with explicit thresholds (FR-10). Exact match is achievable and anything less hides a wrong objective or a missing warning. |
| M4 accounting | fee/tier/stranding-ignoring calculator (what casual cpp spreadsheets do) | 0.300 (9/30: the 8 pure-formula cases plus the one fee case whose edge fee is zero) | ≤ 0.45 | **= 1.0** | Hand-computed integer ground truth for deterministic arithmetic; a single cent of drift means a rule (cap, ceiling, tier, strand credit) is implemented wrong. |
| M5 parser | keyword matcher: exact city-name tokens only, no gazetteer aliases, no month-year resolution, no error typing | 0.300 (18/60: plain-form cases whose month needs no year wrap) | ≤ 0.60 | **≥ 0.90** | The parser is heuristic NL, not exact math: 0.90 (≤ 6 misses) demands the gazetteer, month-resolution and error-typing machinery actually work while leaving room for genuinely ambiguous phrasings; far above the keyword shortcut. |
| M6 shipped-world operability | the engine with `pruning=False` — exact, passes every fixture gate, and is exactly the implementation that dies on the real dataset | 0.583 (7/12: the 2-pax hub-funded round trip, the multi-source splits and the other multi-transfer goals all exceed the 200,000-expansion budget unpruned) | ≤ 0.60 | **= 1.0** | This is the gate that stops a green scorecard on toy worlds from certifying a product that raises `SearchBudgetExceeded` on every real goal. It costs no oracle run and doubles as a dataset-regression check. |
| M7 randomized differential (200) | greedy planner | 0.165 (33/200) | ≤ 0.45 | **= 1.0** | Converts the exactness claim from "correct on 52 curated cases" to "correct on the scenario distribution"; catches unsound bounds/dominance whose trigger conditions the curated cases happen to miss. Reuses `oracle.py` and committed seeds, so it stays hermetic and fast. |

If fixture composition changes, every expected baseline number in this table
must be re-derived in the same commit — and the asserted ceilings make that
enforceable rather than advisory.

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python pointsmax/evals/run.py     # scorecard: metric | value | baseline | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest pointsmax/                 # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads fixtures, runs M1–M7 + D0 with offline
  adapters and the in-memory store, prints the table with actual values,
  measured baselines, M6 expansion counts and timings, and the max/total
  `oracle_enumerations` across fixtures; exits non-zero on any gate failure.
  `--regen` re-runs `generate_search_cases.py` and
  `generate_random_cases.py` and diffs against the committed fixtures (also
  exercised in CI to prove fixture integrity).
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1a_optimality_small_fr7_fr8`,
  `test_gate_m1b_optimality_stress_fr7`, `test_gate_m2_validity_fr7_fr3_fr12`,
  `test_gate_m3_ranking_verdict_caveats_fr9_fr10`,
  `test_gate_m4_accounting_fr8`, `test_gate_m5_parser_fr5`,
  `test_gate_m6_shipped_world_fr1_fr7`,
  `test_gate_m7_random_differential_fr7`,
  `test_baseline_ceilings`, plus `test_d0_determinism_fr16`,
  `test_d0_ledger_replay_fr2`, `test_d0_world_pin_fr11`); names reference
  FR ids so the FR → test mapping is auditable.
- `evals/metrics.py` — pure metric functions (including the independent plan
  validator) shared by both entry points; `evals/oracle.py` — the brute-force
  solver, imported only by the generators and their self-checks;
  `evals/baselines.py` — the measured naive baselines.
- **Runtime budget:** the engine-side suite (M1–M7 + D0) runs in seconds —
  eval worlds are small, M6 is 12 goals on the shipped world, and M7 reads
  committed oracle answers rather than re-solving. Oracle-based fixture
  regeneration is ~5 minutes (bounded by the tractability budget above) and
  runs in CI as a diff check, not on every pytest.
- Hermetic: no network, no wall clock in any assertion (every scenario and
  execution script carries its own dates; M6 reports timing but never gates
  on it), seeded randomness only; the `LLMGoalParser` and all deferred live
  adapters are never imported on the eval path.
