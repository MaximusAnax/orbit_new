# PointsMax — FR Coverage

Every functional requirement in SCOPE.md mapped to the tests and eval metrics
that guard it. Status: **covered** = implemented and guarded by the listed
checks; there are no unimplemented FRs. Test names live in `tests/` and
`evals/test_gates.py`; metric implementations in `evals/metrics.py`.
Falsifiability of the load-bearing gates was demonstrated empirically at the
hardening stage (mutation experiments in docs/REVIEW.md §Hardening-stage
record).

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | World load & 7 validation invariants | `test_world.py` (33 tests: invariants 1–7 each with a violating fixture — duplicate ids, broken divisibility, missing/duplicate valuation, value-increasing edge, fare-coverage holes, gazetteer resolution, content-hash mismatch); `test_gate_m6_shipped_world_fr1_fr7` (shipped `data/world/` loads with validation on); CLI `init`/`world validate` (`test_init_validates_the_world_fr1`, `test_world_info_and_validate_fr1`); API (`test_health_and_world_fr1`, `test_world_validate_fr1`) | covered |
| FR-2 | Wallet cards + append-only ledger, replay invariant | `test_store.py` (chain integrity, negative-balance rejection, `post_balance` chaining, both backends agree, cross-thread SQLite use); `test_d0_ledger_replay_fr2` (D0(b): replay from zero reproduces every balance); `test_wallet_adjust_accepts_negative_delta_fr2` | covered |
| FR-3 | Card/promo gating of edges & options | `test_world.py::test_fr3_*` (property-tested active subgraph: bank edges need `enables_transfer`, `requires_card` options, promo windows); M2 independent validator re-checks gating on every emitted plan (mutation "gating disabled" → M2 1.000→0.963, M1a→0.981) | covered |
| FR-4 | GoalSpec kinds, validation, lifecycle | `test_goals.py` + `test_models.py` (kind-specific nullability, passengers 1–8, nights 1–30, month windows); `test_store.py` goal lifecycle transitions (`active→planned→fulfilled\|dropped`, terminal frozen) | covered |
| FR-5 | Rule-based goal parser | `test_parser.py` (19 tests: aliases, cabins, trip type, passengers, month wrap `test_fr5_months_resolve_to_the_next_occurrence`, home-city default, error typing); gate **M5 ≥ 0.90** over 60 committed utterances (`test_gate_m5_parser_fr5`; alias mutation → 0.25) | covered |
| FR-6 | Offer matching predicate | `test_goals.py::test_fr6_*` (direction/round-trip/cabin-null/window-overlap/seats/`bookable_until` matching, gazetteer resolution); exercised by every M1/M7 scenario | covered |
| FR-7a | Booking-set enumeration incl. award/portal leg mixes | `test_search.py::test_fr7a_*` (single one-ways, RT awards, portal RT, Cartesian leg mixes across programs, null-cabin portal pricing, stays, determinism); M1/M3 scenarios include mixed award+portal round trips | covered |
| FR-7b | Need aggregation + edge merging | `test_search.py::test_fr7b_*` (aggregated needs, one merged transfer per edge, capped fee paid once); M2 validator asserts ≤ 1 transfer step per `(plan, edge)` | covered |
| FR-7c | Candidate lattice + branch-and-bound exactness | `test_search.py::test_fr7c_*` (cover stop, tier boundary beats cover, cheapest source, multi-source splits, hub chains, hop cap, budget raises `SearchBudgetExceeded`, `pruning=False` unchanged results, solution cache); gates **M1a = M1b = 1.0** vs independent oracle and **M7 = 1.0** on 200 random worlds (lattice mutation → M1a 0.808 / M1b 0.917 / M7 0.885) | covered |
| FR-7d | Arrival-vs-deadline feasibility | `test_search.py::test_fr7d_*` (late transfer infeasible, deadline forces instant plan, chain arrival accumulation); 2 deadline-binding fixture scenarios; M2 validator re-derives arrival days from raw files | covered |
| FR-8 | Integer value accounting (portfolio delta, fees, tiers, pooled cpp) | `test_money.py` + `test_value.py` (floor/ceil rules, cap on merged amount, tier delivery, portal price rounding, pooled vs per-booking cpp, stranded leftovers); gate **M4 = 1.0** over 30 hand-computed cases (fee-floor mutation → 0.933; tier-drop mutation → 0.767 with M1a 0.596, M2 0.652) | covered |
| FR-9 | Canonical order, total-order ranking, comparator, verdicts | `test_plan.py` (canonical sort key, seq numbering, tie-breaks, signature identity-only); `test_advisor.py` (duplicate collapse, comparator append, verdict branches incl. both negative verdicts); gate **M3 = 1.0** (full ranked canonical forms + comparator flags + verdicts) | covered |
| FR-10 | Deterministic explanations + typed caveats | `test_plan.py` (each caveat's firing threshold on/off, byte-stable ordering, every number in an explanation equals a stored field); M3 compares caveat codes **and key params** per plan (stranding-silenced mutation → M3 0.672 while M1a/M2 stayed 1.0); stranding `value_cents` is an M4 expected field | covered |
| FR-11 | PlanSet persistence + guarded execution loop | `test_store.py` (immutable plan sets round-trip, atomic step execution, rejected execution writes nothing); `test_execution.py` (order enforced, re-execute refused, confirmation gate, balance re-check); `test_d0_world_pin_fr11` (content-hash pin incl. same-semver case); API/CLI execute tests | covered |
| FR-12 | Cash goals: liquid-only, chains when better | `test_search.py::test_fr12_*` (liquid filter — CSR portal rejected for cash, per-program option choice, filter/cap, quantization, chain-beats-direct case); 6 cash scenarios in M1 incl. below-baseline verdicts; stress scenario where a transfer chain beats every direct cashout; M2 liquid-method check | covered |
| FR-13 | Quick valuation: baseline + cash floor + travel floor | `test_value.py::test_fr13_*` (floors respect gating and quantization, remainder worth 0, provenance ids); CLI `value` / `wallet show` tests; API `/valuations` test | covered |
| FR-14 | FastAPI surface | `test_api.py` (21 tests: every sketched route, status codes, error catalog — 404/409/422/428, time as request input); cross-thread SQLite regression test (`test_sqlite_is_usable_from_worker_threads_fr14`) | covered |
| FR-15 | Typer CLI | `test_cli.py` (17 tests: every sketched command end to end on a temp DB, confirmation prompt, exit codes 0/1/2, negative-delta adjust, vendored-usage-error mapping) | covered |
| FR-16 | Determinism, hermeticity, safeguards | `test_d0_determinism_fr16` (byte-identical recompute, stable signatures); `test_advisor.py::test_fr16_*` (pruning switch changes only expansions, `today` is an input, disclaimer on every plan set, no card-acquisition phrase anywhere); engine imports contain no clock/network/filesystem/random; full eval suite runs byte-identically twice (hardening check) | covered |

## Notes

- **M6 (shipped-world operability)** guards FR-1 + FR-7 at real scale: its
  baseline — the engine with `pruning=False` — scores 0.583 against the 1.0
  gate on every run, which doubles as the standing demonstration that M6 can
  fail.
- **M5 slack**: the ≥ 0.90 gate tolerates up to 6 parser misses by design. A
  targeted "no year-wrap" mutation scores 0.95 and passes M5 alone, but is
  still caught by `test_parser.py::test_fr5_months_resolve_to_the_next_occurrence`
  (2 failures), which runs in the same suite. Recorded in REVIEW.md.
- Non-goals (LLM parser in the loop, live adapters) are enforced by
  construction: the eval path never imports `goal_parser_llm` or the `_live`
  adapters (`evals/` imports are offline-only).
