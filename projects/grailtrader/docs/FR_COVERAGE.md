# GrailTrader — FR coverage

FR id → the tests and eval gates that cover it → status. Test ids are
`tests/<file>::<name>` unless prefixed `evals/`; gate names (M0a … M6c) are the
rows of `evals/run.py` / `evals/test_gates.py`. Final counts: **295 tests
passing, 29/29 eval gates passing** (see docs/REVIEW.md "Hardening-stage
findings" for the falsifiability record).

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Dataset validation at init (brand/era invariants, prior reachability, decreasing multipliers, template sections/lexicon, config ranges incl. `theta == fee_assumption_pct`) | `test_datasets_fr1.py` (24 tests: one accept path + one reject path per invariant); `test_cli_fr13.py::test_fr13_init_validates_and_materialises`; every eval run revalidates via `harness.fixture_context` | **Covered** |
| FR-2 | Listings ingestion: alias mapping, gazetteer resolution with skip-and-count, USD-only, idempotent content-derived ids, sold/active field invariants | `test_ingest_fr2.py` (13); `test_adapters_fr2_fr5.py` (CSV column map, ask-only exclusion, fixture ordering); `test_service_fr8_fr14.py::test_fr5_event_feed_rows_with_unknown_brand_or_era_are_skipped_and_counted` (same rule applied to event feeds — added in hardening) | **Covered** |
| FR-3 | MAD outlier fence: `max(fence_sigma·σ̂, fence_floor_log)` on log deviations, counted + inspectable exclusions | `test_fence_fr3.py` (9: floor binds, MAD binds, strict boundary, near-market fake survives by construction); gates **M1b-recall ≥ 0.90** (falsified: 0.994 → 0.000 with fence disabled), **M1b-false ≤ 0.05**; ungated D-fence diagnostic | **Covered** |
| FR-4 | Index construction: windowed condition-adjusted medians, `min_sales` gate, base-100, chain-linked parents, pure/idempotent build | `test_index_fr4.py` (15, incl. T4 no-step-on-child-birth, restrict≡prefix-build); gates **M0a ≥ 0.95**, **M1a ≤ 0.05**, **M1a-p90 ≤ 0.14**, **M1a-dense ≤ 0.06 / M1a-sparse ≤ 0.12** (falsified: 0.043/0.072 → 0.069/0.153 without condition adjustment), parent cells graded against generator-side chain-linked truth inside M1a | **Covered** |
| FR-5 | Event identity/dedup (factual attrs only, week-keyed, corroboration = distinct domains), scope resolution per typology, pending/rejected inert | `test_events_fr5.py` (25, incl. T5 `Σ m_e` unchanged on duplicate feed); `test_adapters_fr2_fr5.py` (RSS keyword rules, env gating, pending candidates); `test_service_fr8_fr14.py::test_fr5_pending_events_do_not_move_advice` | **Covered** |
| FR-6 | Impact model: adstock `m_e(a)`, tier scaling, log-additive combination, conjunctive bounded retirement, re-anchoring, no hard-coded moves | `test_impact_fr6.py` (18, incl. `test_fr6_engine_hard_codes_no_expected_move`); skill gates M2a/M2b depend on it end-to-end (falsified: E3 below) | **Covered** |
| FR-7 | Valuation ladder: repeat_sales → comp_based → unavailable with named reason, anchor pair per status | `test_valuation_fr7.py` (10: all three methods, all three unavailable reasons, condition rescale, watching anchors); rendered into every advice (M5a population) | **Covered** |
| FR-8 | Advisor: stratum fallback, λ dilution, baseline B, r̂ vs observed O_t, gap-normalised σ_w, argmax-z horizon, confidence product, action rule, candidate flag, `inputs_hash` identity/supersession, all 7 hold reasons | `test_advisor_fr8.py` (26, incl. the DATA_MODEL worked example reproduced to 4 decimals and T6 — one test per hold reason); `test_service_fr8_fr14.py` (no-op re-run, supersession); gates **M2a ≥ 0.70** (falsified: 0.834 → 0.326 momentum-chasing degrade), **M2b ≥ 0.08** (0.108 → −0.032), **M3a/M3b/M3c** (falsified: constant-0.85 confidence fails M0c and M6c), **M0b–M0d** activity floors | **Covered** |
| FR-9 | Frame check both directions: forbidden lexicon outside quoted user text, required sections, verbatim footer, fee ≥ threshold coherence, raise-before-persist, store CHECK | `test_frame_fr9.py` (20, incl. quote-smuggling and straddling-phrase cases); `test_store_datamodel.py::test_sqlite_schema_enforces_the_frame_check_constraint`; gates **M5a = 1.0** (n = 3,888, checker re-implemented independently in `evals/metrics.py`) and **M5b = 1.0** (13 hand-authored cases, 8 must-block + 5 must-render) | **Covered** |
| FR-10 | Backtest: strictly-prior replay, entry t+1, horizon-graded realized returns, out_of_window vs budget exclusions, live baselines, placebo displacement with intersect-aware redraw | `test_backtest_fr10.py` (18, incl. T3 canaries: entry-timing, future-listing peek, prefix-index equivalence); gates **M4a ≤ 0.07 / M4b ≤ 0.04** worst-of-3-seeds, **M0e ≥ 400/seed**, **M0f** baseline bands, **M2-excl ≤ 0.05**; `evals/test_gates.py::test_replay_entry_is_the_following_week_fr10` | **Covered** |
| FR-11 | Portfolio CRUD: status-dependent required pairs, immutable id fields, soft delete leaves pipeline but keeps advice readable, watching in pipeline | `test_portfolio_fr11.py` (12); `test_service_fr8_fr14.py::test_fr11_*` (2); `test_cli_fr13.py::test_fr13_portfolio_lifecycle_and_valuation`; `test_frame_fr9.py::test_fr9_watching_garments_render_sell_as_avoid_or_wait` | **Covered** |
| FR-12 | FastAPI app per endpoint sketch, thin | `test_api_fr12.py` (10, incl. `test_fr12_openapi_documents_every_sketched_endpoint` and the error catalog) | **Covered** |
| FR-13 | Typer CLI per command sketch, `--json` escape hatch, non-zero exits | `test_cli_fr13.py` (10); full command surface additionally exercised end-to-end on the committed `examples/` sample during hardening (init → listings → index → events → portfolio → advise → advice → backtest run/placebo/show/list) | **Covered** |
| FR-14 | Determinism & hermeticity: content-derived ids, integer-cents hashing, no clock in engine, byte-identical replays, offline-only test/eval path | `test_determinism_fr14.py` (7, incl. T1 byte-identical dual-store export and `test_fr14_engine_never_reads_the_clock`); `test_hermeticity_fr14.py` (3, incl. T2 socket-patched eval run asserting `news_rss` never imported); `test_weeks_fr14.py` (4); eval suite run twice → identical scorecards | **Covered** |

## Notes

- **US-2's "zero configuration" fixture flow** is served by the committed
  `examples/` sample (real brands), not by `evals/fixtures/` (fictional brands,
  which by SCOPE D-14 cannot exist in the runtime gazetteer). The README states
  this split explicitly; the eval fixtures run through the same ingest code via
  the harness with the fixture gazetteer.
- **Non-goals are not covered by tests** (no web UI, no transactions, no
  authenticity check, no NLP extraction, no live marketplace API) — by design;
  the fence's inability to catch near-market fakes is *positively* tested
  (`test_fr3_keeps_a_fake_priced_near_market_by_construction`) and reported
  ungated (D-fence).
- No FR is unimplemented or partially implemented as of this hardening pass.
