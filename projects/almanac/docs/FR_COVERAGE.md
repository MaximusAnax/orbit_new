# Almanac — FR Coverage

Hardening-pass audit (see REVIEW.md, Hardening-phase log): every functional
requirement in SCOPE.md mapped to the tests and eval metrics that cover it,
with the *real* module names (EVALS.md §7's table used illustrative file
names written before the build; the content mapping is unchanged).

Status legend: **covered** = implemented, with at least one test or gated
metric that fails if the behaviour regresses (falsifiability demonstrated
for the gated metrics — REVIEW.md H-table).

| FR | Requirement | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Capture, normalization, `normalized_hash`, duplicate warning, tag normalization | `tests/test_fr1_normalize.py` (11 tests: NFKC/casefold/diacritics/smart quotes, hash stability under punctuation-only edits, SHA-256, tokenizer + Porter stemmer vs reference vocabulary, tag/author normalization); `tests/test_fr4_curation.py::test_fr1_duplicate_capture_warns_but_does_not_block`, `::test_fr1_tags_are_normalized_and_shared`; API/CLI halves in `test_fr15_api.py::test_fr15_duplicate_capture_is_a_warning_not_a_block`, `test_fr16_cli.py::test_fr16_duplicate_capture_needs_confirmation_and_exits_nonzero` | covered |
| FR-2 | Attribution check against the committed misattribution dataset; flags never block | `tests/test_fr2_attribution.py` (11 tests: contiguous-token + author match rule, ordering, non-blocking, dataset spot check); data floor `evals/test_gates.py::test_fr2_misattribution_records_cite_a_source`; CLI/API surfaces in `test_fr16_cli.py::test_fr16_check_attribution_by_text_and_by_entry`, `test_fr15_api.py::test_fr15_attribution_check_endpoint` | covered |
| FR-3 | Theme taxonomy + deterministic keyword suggester; suggestions never silently assigned | **M8** (held-split top1 0.7396 ≥ 0.55, hit3 0.8438 ≥ 0.80, worst-theme 0.50 ≥ 0.50; falsified: constant-score mutation → 0.0625/0.1875/0.0); `tests/test_fr3_themes.py` (13 tests incl. never-silent + accept-on-request); `tests/test_lexicon_hygiene.py` (held-split tuning tripwire) | covered |
| FR-4 | Curation: hash-preserving text edits only after surfacing; pin budget warning; archive semantics | `tests/test_fr4_curation.py` (13 tests); **M1c** (archived entries never surface, live via persona archiving); API/CLI: `test_fr15_api.py::test_fr15_patch_rejects_semantic_text_edit_after_surfacing`, `::test_fr15_pin_returns_warning_past_the_budget`, `test_fr16_cli.py::test_fr16_edit_rejects_a_semantic_change_after_surfacing` | covered |
| FR-5 | Import (JSON/CSV/starter) with row-error report + drain horizon; full-library export | `tests/test_fr5_import_export.py` (11 tests incl. export→import round-trip and the capacity-derived horizon); `test_fr15_api.py::test_fr15_import_and_export_round_trip`, `::test_fr15_import_starter_pack`; `test_fr16_cli.py::test_fr16_import_csv_reports_rows_and_drain_horizon` | covered |
| FR-6 | Scheduling-state fold: grade table, demotion, archive flag, clamps, `I_eff` | `tests/test_fr6_fold.py` (15 tests incl. the four distinct first-review intervals, decimal half-up rounding, DATA_MODEL worked example); **M7c** independent re-fold (falsified: demotion-only mutation → 5 cache≠fold offenders, M7 = 0); **M6** (falsified: streak-tracking removed → 0.718 > 0.60; grade table flattened → 0.883) | covered |
| FR-7 | Daily selection: 6 branches, σ controller, priorities, jitter, telemetry | `tests/test_fr7_scheduler.py` (31 FR-7 tests: branch precedence, bang-bang controller, U-shaped novelty priority, jitter bounds, batch semantics); **M1a/b/g/h**, **M2a** (2/12 vs gates 7/21), **M2b** (1.0), **M3** (0.0286 ≤ 0.12; falsified: greedy-novelty → 0.65), **M4a/M4b** (0, 1.66 ≤ 3.0; falsified: interval-blind LRU → 3.57), **M5** (1.0; falsified: rescue disabled → 0.059 + 635 M1h violations) | covered |
| FR-8 | Materialization idempotence, arrow of time (all kinds), extra draws | `tests/test_fr8_materialize.py` (15 tests) + 5 `test_fr8_*` draw tests in `test_fr7_scheduler.py`; **M1c/M1f/M1i**; API monotonicity/422s in `test_fr15_api.py` | covered |
| FR-9 | Prompt kind rotation + grade overrides, candidate pools, recency exclusion, slot render | `tests/test_fr9_prompts.py` (20 tests); **M1d** (reuse predicate re-derived engine-free), **M1e** (theme match, gate 0); `kind_coverage` report (S1 = 0.98) | covered |
| FR-10 | Personalizer validator + silent template fallback | **M9** (recall 1.0, FPR 0.0; falsified: URL check disabled → recall 0.925); `tests/test_fr10_validator.py` (14 tests, one per check plus boundary honesty); `tests/test_fr10_personalizer_fallback.py` (5 tests incl. raising adapter); `evals/test_gates.py::test_fr10_rejected_personalization_falls_back` | covered |
| FR-11 | Reflection log: one per surfacing, 409 after supersession, immutable, browsable | `tests/test_fr11_reflection.py` (8 tests); API 409s in `test_fr15_api.py::test_fr15_reflection_is_created_once_then_409`; CLI in `test_fr16_cli.py::test_fr16_reflect_last_then_reflecting_again_exits_nonzero` | covered |
| FR-12 | FTS5 porter search, bm25 + id tie-break; LIKE fallback with same result set | `tests/test_fr12_search_store.py` (8 search tests incl. fallback set-equality and its documented ordering, + 6 store tests) | covered |
| FR-13 | Collections: ordered membership, draw scoping only | `tests/test_fr13_collections.py` (9 tests); **M1i** (draw accounting, live via S1's draw stream) | covered |
| FR-14 | Stats: counts, coverage, streaks, novelty share, pinned status, archive candidates, capacity block + advisory | `tests/test_fr14_stats.py` (11 tests incl. the `lambda > 3` advisory); **M6 sub-check** (archive-candidate list ≡ `flat_streak ≥ 3` set, gate 1.0) | covered |
| FR-15 | FastAPI surface: thin routes, error mapping | `tests/test_fr15_api.py` (26 tests: 201/404/409/422 per sketch) | covered |
| FR-16 | Typer CLI: same service layer, `--json` everywhere, plain-text card | `tests/test_fr16_cli.py` (25 tests); hardening pass additionally exercised every documented command against a real SQLite file end to end (REVIEW.md H3) | covered |
| FR-17 | Determinism and hermeticity of the engine | **M7a–d** (replay, seed sensitivity, fold equality, SQLite round-trip); `tests/test_fr17_determinism.py` (9 tests incl. the no-I/O import scan of `engine/`); whole-suite determinism re-verified: two full `run.py` runs byte-identical | covered |

Cross-cutting checks that guard the data deliverables (not FRs but load-bearing):
`tests/test_data_and_models.py` (19 tests: dataset floors, parameter
constraints `I0 == W`, `lo >= W`, model invariants), plus the data-floor
tests in `evals/test_gates.py`.

No FR is unimplemented; no FR relies on an unfalsifiable metric alone. The
weakest coverage is FR-6's `archive_flat_streak = 3` path at timeline scale
(REVIEW.md B2: only pinned entries realistically reach streak 3, so M1c and
the M6 sub-check see few live cases — 2 candidates / 1 archiving across the
seed-7 scenarios; the unit tests cover the branch directly), and M6's gate
specifically does not fail when *only* the demotion branch is removed
(REVIEW.md H1: that mutation is caught by M7c instead).
