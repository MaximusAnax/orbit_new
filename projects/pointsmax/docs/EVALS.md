# PointsMax — Evals

## What this product lives or dies on

1. **Optimal, constraint-correct redemption-path search** (FR-7/8/9): given
   holdings, cards, and a goal, the engine must find the plan that truly
   maximizes net realized value — through multi-hop chains, transfer
   increments and minimums, fee caps, tier bonuses, promo windows, time
   budgets, and joint funding of multiple bookings from shared balances —
   and rank alternatives correctly, emitting only executable plans. The
   product's recommendations trigger irreversible point transfers; "usually
   right" is disqualifying.
2. **Exact value accounting** (FR-8/10): net value, realized cents-per-point,
   fees with caps, tier bonuses, and stranded-point costs must match
   hand-computed ground truth to the cent. Every sentence the product emits
   is an arithmetic claim.

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

Over the search scenarios (each: world + cards + opening balances + goal +
`today` + params, with oracle-established expected results):

```
M1a = (# small scenarios where engine top-plan objective value == oracle optimum) / 40
M1b = (# stress scenarios where engine top-plan objective value == oracle optimum) / 12
```

"Objective value" is `net_value_cents` for flight/stay goals and
`cash_received_cents` for cash goals (FR-9). A `SearchBudgetExceeded` error
counts as a miss. The oracle is an independent full-lattice brute-force
solver (see Fixture strategy) — agreement is meaningful because the two
implementations share no search or pruning code.

### M2 — Emitted-plan validity (capability 1)

Every plan emitted across all 52 scenarios (top-K + comparators) is
re-checked by an independent validator in `evals/metrics.py` that reads the
raw world files directly: card gating honored; promo/availability windows
vs `today`; sent amounts respect `min_from`/`increment_from`; delivered
amounts and tier bonuses recomputed exactly; balances never negative when
steps are applied in sequence; cumulative chain time ≤ `book_by` budget;
`passengers ≤ seats_available`; each booking paid from a single program.

```
M2 = (# emitted plans passing all validator checks) / (# emitted plans)
```

### M3 — Ranking exactness (capability 1)

Per scenario, compare the engine's full ranked output (top-K order,
comparator flag, and verdict) against the oracle's, using the canonical
tie-break both sides implement from FR-9:

```
M3 = (# scenarios where ranked signatures, order, comparator flags, and verdict all match) / 52
```

### M4 — Value-accounting exactness (capability 2)

Over 30 hand-computed accounting cases (each a fully specified plan +
holdings + world fragment, with expected `gross_value_cents`,
`cash_outlay_cents`, `points_cost_cents`, `net_value_cents`,
`realized_cpp_milli`, and per-step fee/bonus/delivery numbers, worked out
by hand in the case's `rationale` field):

```
M4 = (# cases where every expected number matches exactly) / 30
```

### M5 — Goal-parser exactness (supporting)

Over 60 committed utterances with ground-truth `GoalSpec` (or ground-truth
`ParseError` naming the missing/ambiguous fields):

```
M5 = (# utterances where parse output == truth, field-for-field) / 60
```

### D0 — Determinism & state integrity (plain pytest, no score)

(a) Recompute the PlanSet for 5 designated scenarios twice: serialized
output must be byte-identical (FR-16). (b) Execute a fixture plan's steps,
then replay the full ledger from zero: balances and every `post_balance`
must reproduce exactly (FR-2). (c) Executing a step against a world whose
`content_hash` differs from the PlanSet's pinned hash must fail with the
structured version-mismatch error (FR-11). Any failure fails the suite.

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable
byte-identically by committed seeded scripts. **Ground truth never comes
from the engine under evaluation**: search optima come from an independent
brute-force oracle; accounting truth is hand-computed; parser truth is by
construction. Eval worlds are synthetic-but-realistic (real mechanics —
gating, 3:1 ratios, $99 fee caps, 60k tiers — with made-up balances and
prices) so truth is fully determined by the fixture, not by the shipped
`data/world/` snapshot, which evolves independently.

| File | Contents | Ground truth |
|---|---|---|
| `worlds/small_a..d.json`, `worlds/stress_a.json` | 4 small worlds (5–8 programs, 8–15 edges, 6–12 offers) + 1 stress world (12 programs, ~30 edges incl. active and expired promos, ~25 offers). Schema-identical to `data/world/`. | n/a (inputs) |
| `search_cases.json` | 52 scenarios: world ref, cards held, opening balances, goal, `today`, params; expected: optimal objective value, full ranked plan list (signatures, values, comparator flags), verdict, per-plan caveat codes. Composition (drives the baseline numbers): 6 straightforward single-source direct transfers; 8 multi-hop-required (target reachable, or cheapest, only via a hotel-hub chain); 8 multi-source splits (no single balance covers the deficit); 6 fee/tier boundary cases (Amex $99 cap straddles, Marriott 60k tier straddles); 6 round-trip pairs with shared-source conflicts (greedy sequential funding is suboptimal by construction); 6 cash goals (gating, min/increment quantization, below-baseline verdicts); 12 stress scenarios combining several mechanics at once, sized within the FR-7 expansion budget. 24 of the 52 additionally embed a cpp-vs-net-value ranking conflict (a high-cpp small redemption vs a lower-cpp higher-net plan) to break cpp-first rankers. | `generate_search_cases.py --seed 20260731` runs `oracle.py`: an independent exhaustive solver enumerating every active chain (≤ max_hops), every sent amount on the full increment lattice from `min_from` to balance, every booking set (single RT, one-way pairs, stays, portal, cash option choices), scoring with its own straightforward FR-8 arithmetic. No pruning, no shared search code. Generator self-check: the oracle must reproduce all 30 `accounting_cases.json` expected values before scenarios are written. CI re-runs the generator and diffs (small worlds keep it under ~2 min). |
| `accounting_cases.json` | 30 cases: fixed plan + holdings + world fragment + expected numbers + a human-readable `rationale` showing the arithmetic. Composition: 8 pure-formula (multi-currency funding, portal point math, realized cpp incl. the FR-8 per-booking definition); 8 fee cases (cap boundary at exactly 165,000 pts × 60 mcpp = $99, one above, one below, ceiling-rounding edges); 6 tier cases (59,997 vs 60,000 vs 120,000 through Marriott-style 3:1 +5k/60k); 8 stranding/increment cases (leftovers credited at destination mcpp, incl. a 1:2-ratio case where destination value per source point exceeds face intuition). | Hand-computed; every case's `rationale` shows the full working so review can re-derive it |
| `parser_cases.json` | 60 utterances + truth. Composition: 25 plain-form trip requests; 15 alias/gazetteer forms ("nyc", "new york city", "Paris", airport codes); 10 relative-month cases pinned to each case's `today` ("October" before/after October, "March" wrapping the year); 5 cash/stay intents ("turn everything into cash", "a week at a hotel in Paris in December"); 5 negative cases whose truth is a `ParseError` with named missing fields ("business class somewhere warm", "next spring"). | By construction (each case authored with its spec/error) |
| `exec_scripts.json` | For D0: 2 scenario refs + the step-execution sequences and `at` timestamps to apply | n/a |

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1a optimality (small) | greedy planner: single cheapest-mcpp source, direct edges only, need-sized amounts, sequential per booking | ≈ 0.25 (solves the 6 straightforward + ~4 easy cash cases; multi-hop, splits, tiers, and joint-funding scenarios are constructed to defeat it) | **= 1.0** | The FR-7 search is exact by design (complete candidate lattice, admissible bound, lossless dominance pruning), fixtures fit the expansion budget, and the oracle is independent — any mismatch is a real bug, and partial credit would hide it. Same exactness rationale as ChessMentor M8. |
| M1b optimality (stress) | same greedy | ≈ 0.08 (1/12) | **= 1.0** | Stress worlds exist to catch complexity blowups and pruning bugs at scale; they are sized within the documented budget, so exactness remains fair. |
| M2 plan validity | search without quantization/gating checks | ≈ 0.6 of emitted plans valid | **= 1.0** | An invalid plan tells the user to make a transfer the program will reject (or worse, one that strands points). Zero tolerance is the product premise; the validator is independent of engine code. |
| M3 ranking + verdict | rank by realized cpp instead of net value | ≈ 0.5 (fails the 24 built-in cpp-vs-net conflicts, plus verdict misses where cpp is high but net ≤ 0) | **= 1.0** | Ranking is a deterministic formula with a canonical total order shared by oracle and engine; exact match is achievable and anything less hides a wrong objective. |
| M4 accounting | fee/tier/stranding-ignoring calculator (what casual cpp spreadsheets do) | ≈ 0.27 (8/30 pure-formula cases only) | **= 1.0** | Hand-computed integer ground truth for deterministic arithmetic; a single cent of drift means a rule (cap, ceiling, tier, strand credit) is implemented wrong. |
| M5 parser | keyword matcher: exact city-name tokens only, no gazetteer aliases, no month-year resolution, no error typing | ≈ 0.42 (25/60: plain-form cases only) | **≥ 0.90** | The parser is heuristic NL, not exact math: 0.90 (≤ 6 misses) demands the gazetteer, month-resolution, and error-typing machinery actually work while leaving room for genuinely ambiguous phrasings; far above the keyword shortcut. |

If fixture composition changes, every baseline number in this table must be
re-derived in the same commit (checked in review).

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python pointsmax/evals/run.py     # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest pointsmax/                 # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads fixtures, runs M1–M5 + D0 with offline
  adapters and the in-memory store, prints the table with actual values,
  exits non-zero on any gate failure. `--regen` re-runs
  `generate_search_cases.py` and diffs against the committed
  `search_cases.json` (also exercised in CI to prove fixture integrity).
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1a_optimality_small_fr7_fr8`,
  `test_gate_m1b_optimality_stress_fr7`, `test_gate_m2_validity_fr7_fr3`,
  `test_gate_m3_ranking_fr9`, `test_gate_m4_accounting_fr8`,
  `test_gate_m5_parser_fr5`, plus `test_d0_determinism_fr16`,
  `test_d0_ledger_replay_fr2`, `test_d0_world_pin_fr11`); names reference
  FR ids so the FR → test mapping is auditable.
- `evals/metrics.py` — pure metric functions (including the independent plan
  validator) shared by both entry points.
- **Runtime budget:** the engine-side suite runs in seconds (worlds are
  small and the search is bounded); oracle-based fixture regeneration is
  ~1–2 minutes and runs in CI as a diff check, not on every pytest.
- Hermetic: no network, no wall clock (every scenario and execution script
  carries its own dates), seeded randomness only; the `LLMGoalParser` and
  all deferred live adapters are never imported on the eval path.
