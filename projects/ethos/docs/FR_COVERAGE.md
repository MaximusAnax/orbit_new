# Ethos — FR coverage audit

Every functional requirement in SCOPE.md mapped to the tests and eval gates that
cover it, with the status observed at the hardening pass. This table is an
*audit*, not a plan: every row names artefacts that exist and run today.

Status vocabulary:

- **covered** — at least one test or gate fails if the requirement stops holding,
  and that has been demonstrated (by a negative control, a committed falsifiability
  probe, or the mutation experiments recorded in REVIEW.md § Hardening).
- **covered (regression only)** — tested, but the test would only catch a
  regression from the current behaviour; it does not price a capability.

Command to reproduce every number below:

```bash
cd projects
uv run pytest ethos/ -q                 # 299 tests
uv run python ethos/evals/run.py        # scorecard, all gates
uv run python verify_all.py ethos
```

## FR → coverage

| FR | Eval gates | Tests | Status |
|---|---|---|---|
| **FR-1** corpus schema, loading, validation, `corpus_version` | C1–C8, C10–C16 (structural + substance), C19 (freeze currency) | `test_fr1_corpus.py` (9): `test_fr1_loader_is_byte_preserving`, `test_fr1_corpus_version_covers_data_dir`, `test_fr1_corpus_version_is_stable_and_order_independent`, `test_fr1_passage_needs_exactly_one_body`, `test_fr1_models_forbid_unknown_fields`; `test_fr1_gate_predicates.py` (8) exercises each C-predicate directly; `test_negative_controls.py` — 14 committed broken corpora, one per gate | covered |
| **FR-2** text normalization (NFKC → casefold → tokenize → stopwords → Porter) | C18 (500 committed Porter pairs + the NFKC/casefold cases) | `test_fr2_normalize.py` (7): `test_fr2_porter_matches_published_sample` (parametrized over all 500 pairs), `test_fr2_c18_committed_porter_sample`, `test_fr2_nfkc_and_casefold_cases`, `test_fr2_stopwords_are_dropped_after_stemming`, `test_fr2_normalization_never_touches_corpus_text` | covered — negative control `stemmer_off_by_one` proves C18 can fail. The stopword list is pinned by `sha256:data/stopwords.txt` in `baselines.json` (C19) rather than by a `test_fr2_stopwords_pinned`, which is the stronger mechanism: an edit fails the suite until baselines are re-derived |
| **FR-3** lexical topic routing (BM25 + phrase bonus) | M1-direct 0.990 ≥ 0.97, M1-coll 0.875 ≥ 0.85, M1b 0.764 ≥ 0.70, M1b′ 0.646 ≥ 0.55, M1gap 0.118 ≤ 0.15, M1c 0.950 ≥ 0.95, M1d 0.900 ≥ 0.85, C9 (fixture lexical integrity), D0(c) | `test_fr3_router.py` (9): `test_fr3_bm25_scores_known_case` (hand-computed Okapi value), `test_fr3_rarer_terms_score_higher`, `test_fr3_unseen_term_takes_maximum_idf`, `test_fr3_phrase_bonus_fires_once_on_contiguous_match`, `test_fr3_tie_break_lexicographic`, `test_fr3_index_rebuild_is_byte_stable`, `test_fr3_coverage_is_idf_weighted` | covered — falsifiability demonstrated: degrading `score_topic` to a raw keyword count drops 7 of the 10 routing gates (REVIEW.md § Hardening, experiment 2) |
| **FR-4** confidence, coverage, abstention (τ / κ) | M2a 0.850 ≥ 0.80, M2a_near 0.846 ≥ 0.68, M2b 0.023 ≤ 0.05, C20 (≥ 25 OOS questions out-score the direct median) | `test_fr4_abstention.py` (9): `test_fr4_confidence_zero_when_s1_zero`, `test_fr4_coverage_floor_fires_on_foreign_vocabulary`, `test_fr4_forced_topic_bypasses_router`, `test_fr4_refusal_payload_shape`, `test_fr4_abstention_counts_as_a_miss` | covered — `oos_set_all_zero_score` negative control proves C20 rejects a trivially-foreign OOS set, i.e. the null `abstain iff s1 = 0` rule cannot pass M2 |
| **FR-5** perspective retrieval, `not_covered` vs `filtered_out` | M5 = 1.000 over 72 renders (24 topics × 3 filter variants) | `test_fr5_retrieve.py` (6): `test_fr5_not_covered_vs_filtered_out`, `test_fr5_passage_role_ordering`, `test_fr5_unfiltered_ask_has_empty_filtered_out`, `test_fr5_canonical_tradition_order` | covered — the filtered variants are the half of M5 that can genuinely fail (EVALS § M5) |
| **FR-6** deterministic composition | M5, D0(a) hash-seed/locale, D0(b) 9 goldens | `test_fr6_compose.py` (10): `test_fr6_marker_numbering_first_appearance`, `test_fr6_every_citation_key_has_exactly_one_quote`, `test_fr6_agreement_map_partitions_rendered_traditions`, `test_fr6_agreement_map_follows_enum_order`, `test_fr6_composition_is_order_independent`, `test_fr6_no_overall_answer_path` (non-goal 2, AST scan), `test_fr6_a_traditions_perspective_does_not_depend_on_the_others` | covered |
| **FR-7** citation & reading rendering | M3 = 1.000, measured by the stdlib-only independent checker over the printed page | `test_fr7_render.py` (7): `test_fr7_quote_block_is_verbatim_inside_typographic_quotes`, `test_fr7_source_line_forms`, `test_fr7_paraphrase_label_rendered_and_never_quoted`, `test_fr7_context_note_follows_every_citation`, `test_fr7_render_is_parseable_by_the_grammar`, `test_fr7_citations_section_lists_every_marker_once` | covered — falsifiability demonstrated: folding the en-dash in rendered locators drops M3 1.000 → 0.926 (REVIEW.md § Hardening, experiment 1), and three committed broken corpora drop it below 1.0 |
| **FR-8** citation verification gate (a)–(i) | M4a = 1.000 over 30 mutations, M4b = 0.000 over 20 clean cases, plus the new fixture-composition gate | `test_fr8_verify.py` (23): one test per check, each proving the check *rejects* a mutation; `test_fr8_every_topic_and_filter_verifies` (24 topics × 4 filters = 96 renders); `test_fr8_independent_reader_catches_print_tampering` (4 parametrized print mutations) | covered — `baseline_no_verifier` is now measured through the real pipeline (0.167, i.e. FR-8 alone accounts for 25 of the 30 detections) instead of being a hardcoded 0.0 |
| **FR-9** optional LLM polish adapter & envelope | M4a (incl. `unparseable_envelope`), M4b | `test_fr9_envelope.py` (8): `test_fr9_envelope_roundtrip`, `test_fr9_regions_are_marked_mutable_or_immutable`, `test_fr9_strict_parse_back_rejects_structural_damage` (parametrized), `test_fr9_clean_polish_is_accepted_and_used`, `test_fr9_fallback_sets_flag_and_serves_the_deterministic_render`, `test_fr9_llm_adapter_not_imported_on_the_offline_path`, `test_fr9_live_adapter_is_env_gated` | covered |
| **FR-10** sensitive-topic safeguards | C17 — 3 topics × 36 cells + 6 topics × 4 cells = 132 cells, each asserting the block is present, byte-identical to `data/safeguards.json`, first, and of the declared kinds | `test_fr10_safeguards.py` (6): `test_fr10_safeguard_first_in_render_order`, `test_fr10_safeguards_cannot_be_suppressed_by_any_option`, `test_fr10_sensitive_topics_carry_crisis_resources` | covered — the JSON half of each cell now checks that `safeguards` really is `AnswerBody`'s first key rather than asserting it in a comment |
| **FR-11** question & answer persistence | D0(d) stored render re-derives byte-identically | `test_fr11_store.py` (8, parametrized over **both** backends): `test_fr11_verified_false_rejected`, `test_fr11_answer_immutable` (incl. the SQLite UPDATE trigger), `test_fr11_stale_corpus_guard`, `test_fr11_guard_fires_before_any_load`, `test_fr11_re_asking_creates_a_new_question_and_answer`, `test_fr11_sqlite_is_safe_across_threads`, `test_fr11_rendered_text_reproduces_from_body` | covered — thread safety is falsifiable: `check_same_thread=True` makes the test red with `ProgrammingError`; removing the `RLock` produces `OperationalError` and duplicate `lastrowid` |
| **FR-12** browse & corpus stats | — (ordinary tests, per EVALS § What this product lives or dies on) | `test_fr12_browse.py` (6): `test_fr12_coverage_matrix`, `test_fr12_stats_reports_substance_ratios` (asserts the same numbers the C11–C16 gates use), `test_fr12_reading_aggregation_dedup`, `test_fr12_topic_detail_lists_covered_traditions` | covered (regression only) |
| **FR-13** API | — | `test_fr13_api.py` (11): `test_fr13_edge_polish_unavailable_400`, `test_fr13_integrity_failure_500`, `test_fr13_asked_at_defaulted_at_edge`, `test_fr13_refusal_is_200`, `test_fr13_forced_topic_and_filter`, `test_fr13_history_and_stored_render`, `test_fr13_sqlite_backed_api_is_thread_safe` (new) | covered |
| **FR-14** CLI | — | `test_fr14_cli.py` (8): `test_fr14_ask_render_matches_stored_text`, `test_fr14_json_flag_emits_answerbody`, `test_fr14_exit_codes`, `test_fr14_refusal_output`, `test_fr14_corpus_validate_and_stats` | covered — every documented command was additionally run end to end against a real SQLite database at the hardening pass (REVIEW.md § Hardening) |
| **FR-15** determinism & hermeticity | D0(a)–(d) | `test_fr15_purity.py` (5): `test_fr15_engine_has_no_io_imports` (AST scan of every `engine/*.py`), `test_fr15_engine_never_reads_the_clock`, `test_fr15_no_seeds_exist_anywhere`, `test_fr15_determinism_across_hash_seeds_and_locales` (3 fresh subprocesses), `test_fr15_index_rebuild_is_byte_identical` | covered — the eval suite was additionally run twice end to end and compared byte-for-byte (identical, sha256 `aa8c16fb…`) |

## Eval design rules

| Rule | Enforced by | Status |
|---|---|---|
| **R1** no instrument grades itself | `test_independent_checker_isolation` — AST-scans `evals/independent_check.py` and fails on any import outside `json`, `pathlib`, `re`, `sys` | covered |
| **R2** every exact gate is provably able to fail | `evals/fixtures/negative_controls/` (22 artefacts) + `test_negative_controls.py` (8 tests, most parametrized) | covered |
| **R3** gates check substance, not only shape | C11–C16, each with its own negative control | covered |
| Metric diagnostics are gates, not commentary | `test_gate_metric_diagnostics_are_empty_fr8`; `run.py` counts a non-empty M3/M4/M5 problem list as a failure | covered (added at the hardening pass) |
| M4 baselines are measured, not asserted | `test_gate_m4_baselines_are_measured_not_asserted_fr8`; `m4_baselines` runs the same 50 cases through the real pipeline with the FR-8 gate swapped | covered (added at the hardening pass) |
| `polish_cases.json` composition | `test_gate_m4_fixture_composition_fr8_fr9` / `m4_case_composition` | covered (added at the hardening pass — it had already eroded) |

## Non-goals asserted as behaviour

| Non-goal | Assertion |
|---|---|
| 2 — no verdicts, no synthesis, no ranking | `test_fr6_no_overall_answer_path` AST-scans the composer for any aggregate-answer path; the `AnswerBody` schema has no field for one |
| 3 — no generated moral content | FR-8 (e) rejects polish-introduced locator-shaped text and bulk prose; the polisher only ever sees `[[M:…]]` regions |
| 5 — no ambiguity flag | absent from `models.py`; alternates are always printed instead |
| 6 — no embedding router | only `TopicRouter` Protocol + `LexicalRouter`; `test_fr15_engine_has_no_io_imports` forbids network imports in the engine |
| 14 — live adapters are optional extras | `test_fr9_llm_adapter_not_imported_on_the_offline_path` |

## Known gaps (stated, not hidden)

1. **FR-12 is regression-only.** Browse and stats have no capability metric, by
   EVALS' explicit design ("everything else is covered by ordinary tests, not
   eval gates"). A stats bug that agreed with the gates would be invisible —
   mitigated by `substance_stats` delegating to `engine.corpus.corpus_stats`,
   the same function C11–C16 use, so the CLI cannot print a number the gates
   disagree with.
2. **Transcription fidelity is a claim, not a measurement.** `transcription_checked`
   asserts a human compared the committed text to the printed edition. M3 proves
   the *render* matches the *committed file*; nothing offline proves the committed
   file matches the page. Spot-checked by hand at the hardening pass (Matthew 5:37,
   Exodus 1:19–20, Qur'an 33:70, Leviticus 19:11, Analects II.22 and XIII.18,
   NE II.6 / IV.6 / IV.7 / VI.5) — all correct for their named editions.
3. **Theological fidelity is out of reach of any offline gate**, as EVALS § What
   the evals cannot see states. C12–C15 force variety and grounding, not truth.
