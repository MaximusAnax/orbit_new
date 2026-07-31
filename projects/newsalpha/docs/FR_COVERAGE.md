# NewsAlpha — FR Coverage

Mapping of every functional requirement in SCOPE.md to the tests and eval
metrics that cover it. Produced during the hardening review (2026-07-31),
after `uv run python verify_all.py newsalpha` was fully green: **327 tests
passed, 21 eval gates pass, lint clean, CLI ok**. Test names carry their FR id
(`tests/test_fr4_extract.py::test_fr4_negation_beats_hedge_in_one_sentence`),
so the mapping below lists representative tests per FR clause, not an
exhaustive enumeration.

| FR | Requirement | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Ingestion & normalization (HTML strip, NFKC, content hash, idempotent re-ingest, sentence split, archive-only window flag) | `test_fr1_html_strip_entity_decode_and_whitespace_collapse`, `test_fr1_normalization_is_nfkc`, `test_fr1_content_hash_is_over_title_newline_body`, `test_fr1_reingesting_the_same_{content_hash,external_id}_is_a_no_op`, `test_fr1_sentence_split_honours_abbreviations`, `test_fr1_exclusion_flag_is_set_once_at_insert`, `test_fr1_old_articles_are_archived_and_excluded_from_analysis` | covered |
| FR-2 | Clustering & corroboration (pinned tokenization, Jaccard ≥ 0.60 ∧ ≤ 48 h, transitive closure, membership-derived cluster id, corroboration/best tier) | `test_fr2_tokenization_is_pinned_to_lowercase_alnum_and_dollar`, `test_fr2_similarity_requires_the_48_hour_window`, `test_fr2_clusters_are_the_transitive_closure`, `test_fr2_exact_cluster_reconstruction_is_order_independent` (100 % on the corpus), `test_fr2_corroboration_counts_distinct_domains_and_best_tier_wins`; committed Jaccard margins asserted by `test_fixture_cluster_jaccard_margins_are_committed` | covered |
| FR-3 | Reference datasets & init validation (alias uniqueness, ambiguity↔context, prior resolution totality, no stage tokens in keys, magnitude/sign invariants, template coverage, forbidden-lexicon check) | 30 tests in `test_fr3_datasets.py`, incl. `test_fr3_duplicate_alias_aborts_init`, `test_fr3_prior_resolution_is_total_over_reachable_combinations`, `test_fr3_prior_key_containing_a_stage_token_aborts_init`, `test_fr3_sign_mismatch_between_direction_and_band_aborts_init`, `test_fr3_every_pattern_cites_a_real_world_example`; lexicon superset gate `test_gate_m8_lexicon_superset_fr7` | covered |
| FR-4 | Event extraction — hard part A (triggers, suppressors, cue precedence, confidence ladder, merge rule) | Gated: **M1a ≥ 0.80 (VAL), M1a-floor ≥ 0.65, M1a-transfer ≤ 0.10, M1b ≥ 0.85, M1c ≥ 0.70** (`evals/test_gates.py`); unit: `test_fr4_negation_beats_hedge_in_one_sentence`, `test_fr4_negation_denies_mna_and_suppresses_every_other_type`, `test_fr4_historical_reference_guard_suppresses`, `test_fr4_metaphor_guard_suppresses`, `test_fr4_merge_stage_latest_wins`, `test_fr4_extraction_confidence_ladder` | covered |
| FR-5 | Asset linking & roles — hard part A (evidence classes, all-caps guard, venue precedence, M&A resolvers, abstention) | Gated: **M2a ≥ 0.85, M2b ≥ 0.90 (50 traps), M3 ≥ 0.85 (40 role decisions)**; unit: `test_fr5_all_caps_guard_*` (3), `test_fr5_venue_precedence_coinbase`, `test_fr5_venue_that_is_an_asset_never_signals`, `test_fr5_mna_{active,passive,bid_for,symmetric,three_parties}*`, `test_fr5_lowercase_common_word_traps_do_not_link` | covered |
| FR-6 | Signal scoring — hard part B (prior resolution, stage channels disjoint, modifier formula, clamp, no unclear signals) | Gated: **M5 ≥ 0.35, M6 ≥ 0.12 + occupancy ≥ 20, G1 ≥ 100**; unit: `test_fr6_rumored_scores_exactly_half_the_confidence_of_confirmed`, `test_fr6_stage_overrides_are_the_only_stage_effect_on_direction_and_band`, `test_fr6_denied_mna_is_bearish_for_the_target_and_silent_for_the_acquirer`, `test_fr6_worked_example_from_the_data_model`, `test_fr6_mentioned_and_venue_links_never_score` | covered |
| FR-7 | Briefs & framing safeguard (four sections, template-only text, frame check raises, footer verbatim) | Gated: **M8 = 1.0** (verdict match over 117 briefs + 16 adversarial cases); unit: `test_fr7_a_brief_that_fails_the_frame_check_is_never_returned`, `test_fr7_word_boundary_matching_spares_buyout_and_sell_off`, `test_fr7_unattributed_forbidden_quote_is_a_violation`, `test_fr7_why_it_matters_carries_the_prior_rationale_verbatim`, `test_fr7_already_priced_note_appears_when_the_announcement_band_dominates` | covered |
| FR-8 | Digest & triage (window, watchlist default, supersession filter, documented ranking, empty state) | `test_fr8_ranking_is_absolute_score_then_confidence_then_asset`, `test_fr8_only_the_latest_revision_appears`, `test_fr8_supersession_denial_outranks_rumor`, `test_fr8_watchlist_filter_is_the_default`, `test_fr8_empty_day_renders_an_explicit_state_not_an_error`, `test_fr8_window_is_exclusive_at_the_lower_bound` | covered |
| FR-9 | Market data & calendars (unique (asset, date), weekday equities / daily crypto, benchmark required, basket idx:CX) | `test_fr9_equity_series_skip_weekends`, `test_fr9_available_bar_rules_{skip_gaps_rather_than_break,step_over_the_gap}`, `test_fr9_gapped_fixture_omits_exactly_the_documented_bars`, `test_fr3_crypto_benchmark_is_a_basket_not_a_single_asset`, `test_live_crypto_benchmark_is_synthesized_from_the_basket`, `test_store_price_bars_are_unique_per_asset_and_date` | covered |
| FR-10 | Backtest harness — hard part B (strictly-after entry, date-aligned benchmark, named exclusions, placebo mode) | Gated: **M4 ≥ 0.72, M4-transfer ≤ 0.10, M7a ≤ 0.035, M7b ≤ 0.06, G2 ≤ 0.05, announcement-capture = 0**; unit: `test_fr10_entry_is_strictly_after_the_observation_on_committed_bars`, `test_fr10_benchmark_is_read_at_matching_calendar_dates`, `test_fr10_benchmark_gap_excludes`, `test_fr10_zero_abnormal_return_is_excluded_not_counted_as_a_miss`, `test_fr10_estimated_publish_time_excludes` | covered |
| FR-11 | Watchlist (add/remove/list, nearest-alias suggestion on unknown id) | `test_fr11_unknown_watchlist_id_is_rejected_with_a_suggestion`, `test_store_watchlist_add_remove_list`, `test_fr12_watchlist_put_list_delete`, CLI exercised end-to-end in this review (`watch add cx:NEARX` → `suggestion: cx:NEAR`) | covered |
| FR-12 | API (thin FastAPI per endpoint sketch) | 20 tests in `test_fr12_api.py` covering ingest, articles, events, signals + revisions, briefs, digest, assets, watchlist, prices load, backtests | covered |
| FR-13 | CLI (Typer per command sketch, `--json` escape hatch, error codes) | 20 tests in `test_fr13_cli.py`; every documented command additionally executed end-to-end on the committed fixtures in this review (init, ingest ×2, digest, articles, events show, signals show/revisions, brief, assets, watch, prices load, backtest run/placebo/show) | covered |
| FR-14 | Determinism & replay equivalence (D0/D1) | `test_fr14_d0_determinism_on_the_eval_corpus`, `test_fr14_d0_reingest_writes_no_new_revisions`, `test_fr14_d1_replay_equivalence_on_the_eval_corpus` (one batch vs five daily batches), `test_fr14_partitioning_does_not_change_cluster_identity`, `test_fr14_time_is_always_an_input`, hermeticity tests; scorecard run twice in this review → byte-identical output | covered |
| FR-15 | Ingest-run semantics, revisions & supersession (active window, derived vs durable, idempotent revisions, key continuity, supersession) | `test_fr15_articles_outside_the_active_window_take_no_part`, `test_fr15_key_continuity_alias`, `test_fr15_supersession_records_the_older_key`, `test_fr15_incremental_corroboration_raises_confidence_via_a_revision`, `test_fr15_observed_at_anchors_on_the_latest_evidence`, `test_store_derived_rows_are_replaced_wholesale`, `test_store_service_ingest_is_idempotent_end_to_end` | covered |

## US-level acceptance criteria

- **US-1** zero-config `ingest && digest`, byte-identical re-run: `test_fr13_*`,
  `test_fr14_d0_reingest_writes_no_new_revisions`, verified manually end-to-end.
- **US-2** evidence spans on every claim: `test_fr4_evidence_quotes_equal_the_analysed_span`,
  `test_fr5_every_link_carries_an_evidence_span`, M1a/M2 gates.
- **US-3** one story one event, corroboration via revision: `test_fr4_at_most_one_event_per_cluster_and_type`,
  `test_fr15_incremental_corroboration_raises_confidence_via_a_revision`.
- **US-4** no imperative field, 0.95 cap, rumored = half, denied M&A behaviour:
  `test_fr8_digest_has_no_imperative_field`, `test_fr6_confidence_is_clamped_to_the_documented_bounds`,
  `test_fr6_rumored_scores_exactly_half_the_confidence_of_confirmed`,
  `test_fr6_denied_mna_is_bearish_for_the_target_and_silent_for_the_acquirer`.
- **US-5** four sections + not-advice footer, frame check blocks persistence: FR-7 rows above; M8 = 1.0.
- **US-6** leak-free backtest + placebo: FR-10 rows above; M4/M5/M6/M7/G1/G2 gates.
- **US-7** universe & watchlist: FR-11 rows above, `test_fr3_*` gazetteer validation.
- **US-8** live adapters env-gated and never imported on the eval path:
  `test_live_{rss_feed,market_data}_stays_inactive_without_the_env_var`,
  `test_hermetic_no_live_adapters_{on_the_offline_path,after_a_full_eval_run}`,
  `test_eval_conftest_blocks_the_network`. Live-path HTTP fetch itself is
  network code and is exercised only structurally
  (`test_live_market_data_routes_by_asset_kind_without_network`,
  `test_live_rss_entry_mapping_without_network`) — the actual network calls are
  untested by design (hermetic suite). This is the one deliberate coverage gap.

## Gaps

No FR is unimplemented. The only untestable-by-design surface is the live
adapters' real network I/O (US-8), stated above and in EVALS.md's residual-risk
note: passing the hermetic gates is not evidence of transfer to live RSS text.
