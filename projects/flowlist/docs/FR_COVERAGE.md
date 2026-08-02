# flowlist — FR COVERAGE

Every functional requirement in `SCOPE.md` mapped to the test or eval metric
that exercises it. Produced during the harden pass and kept accurate by
`evals/test_gates.py` + `tests/`; run `uv run pytest flowlist/ -q` to execute
everything named here except the two rows marked *slow-marked* / *optional
extra*, which say plainly what is and is not run.

Status legend:

- **covered** — at least one test or gated metric fails if the requirement
  breaks.
- **covered (not gated)** — exercised by tests but deliberately outside the
  eval gates (EVALS.md §1: gates measure the two hard capabilities only).
- **partial** — implemented and tested on the offline path; the live path is a
  declared non-goal for this pass and is exercised only by
  skip-if-unavailable / absence-of-dependency tests.

`test_*` names below are given without the `test_` prefix that pytest requires,
exactly as they appear in the files.

## Functional requirements

| FR | What covers it | Status |
|---|---|---|
| **FR-1** Playlist import (CSV/JSON), sentinel mapping, `--replace`/`--force` | `tests/test_adapters.py`: `test_fr1_csv_exportify_layout`, `test_fr1_key_minus_one_sentinel_nulls_key_and_mode_with_a_warning`, `test_fr1_tempo_sentinel_and_out_of_range_null_the_bpm`, `test_fr1_malformed_rows_are_skipped_with_line_numbers`, `test_fr1_missing_features_are_not_malformed`, `test_fr1_csv_column_lookup_is_case_and_alias_tolerant`, `test_fr1_csv_blank_lines_ignored`, `test_fr1_csv_rejects_unusable_files`, `test_fr1_csv_reads_from_disk`, `test_fr1_json_schema_from_the_data_model`, `test_fr1_json_applies_the_same_sentinel_rules`, `test_fr1_json_errors`. Replace/force semantics: `tests/test_store.py::test_fr1_replace_entries_swaps_the_whole_set`, `::test_fr1_replace_refuses_when_runs_exist_without_force`, `::test_fr1_force_replace_deletes_runs_and_clears_applied_run`; `tests/test_cli.py::test_fr1_import_refuses_a_duplicate_name_without_replace`, `::test_fr1_replace_with_runs_needs_force`; `tests/test_api.py::test_fr13_name_conflict_and_replace`, `::test_fr13_replace_with_runs_needs_force`. Catalog dedupe: `tests/test_services.py::test_fr1_import_is_idempotent_in_the_catalog` | covered (not gated) |
| **FR-2** Directory import, tag-or-filename identity | `tests/test_adapters.py::test_fr2_directory_scan`, `::test_fr2_filename_fallback_when_tags_are_absent`, `::test_fr2_default_name_is_the_folder`, `::test_fr2_extensions`, `::test_fr2_rejects_non_directories`, `::test_fr2_file_sha1_is_the_content_hash`, `::test_fr2_tag_reading_degrades_without_mutagen`; `tests/test_services.py::test_fr2_directory_import_reads_tags_or_filenames`; `tests/test_identity.py::test_fr2_filename_fallback` | partial — see *Known gaps* (1) |
| **FR-3** Feature resolution, precedence, coverage report, idempotence | `tests/test_resolution.py` (all 17 `test_fr3_*`); `tests/test_services.py::test_fr3_analyze_is_idempotent`; `tests/test_cli.py::test_fr14_analyze_reports_coverage`; `tests/test_api.py::test_fr13_analyze_reports_coverage` | covered (not gated) |
| **FR-4** Manual override, top precedence, field-wise upsert | `tests/test_resolution.py::test_fr4_manual_override_wins`; `tests/test_store.py::test_fr4_manual_row_upserts_field_wise`, `::test_fr4_manual_key_and_mode_upsert_as_a_pair`; `tests/test_services.py::test_fr4_manual_override_beats_a_provider`; `tests/test_cli.py::test_fr4_features_set_overrides_a_provider_value`; `tests/test_api.py::test_fr13_get_track_and_manual_override` | covered (not gated) |
| **FR-5** Key math and relation classification | **metric M1 `key_relation_accuracy` (gate = 1.00)** + `tests/test_keys.py` (all 21 `test_fr5_*`, incl. `::test_fr5_every_pair_is_classified_exactly_once` over all 576 ordered key pairs) | covered — gated |
| **FR-6** Pairwise transition score, weights, normalization | **metrics M2 `pair_ranking_auc` (≥ 0.90), M2b anti-gaming margin (≥ 0.10), M3 `component_monotonicity` (= 1.00)** + `tests/test_scoring.py` (all 30 `test_fr6_*`, incl. `::test_fr6_weight_normalization`, `::test_fr6_invalid_weights_rejected`, `::test_fr6_d12_seamless_calibration`) | covered — gated |
| **FR-7** Order scoring / `FlowReport` | `tests/test_scoring.py::test_fr7_flow_report`, `::test_fr7_flow_report_uses_supplied_entry_ids`, `::test_fr7_flow_report_degenerate_sizes`, `::test_fr7_anchored_flag_marks_pinned_endpoints`, `::test_fr7_rejects_malformed_orders`, `::test_fr7_order_total_matches_score_order` + **M7's run-aggregate consistency check** (stored aggregates must be recomputable from the persisted per-transition breakdowns) | covered — gated |
| **FR-8** Reordering (the hard part) | **metrics M4 `exact_optimality` mean ≥ 0.97 / min ≥ 0.90, M4b `degenerate_rejection` = 1.00, M5 `planted_chain_recovery` ≥ 0.92 / min ≥ 0.85, M6 `baseline_margin` ≥ +0.08 / min > 0** + `tests/test_optimizer.py` (`test_fr8_exact_matches_bruteforce`, `test_fr8_result_is_a_genuine_local_optimum`, `test_fr8_escapes_a_nearest_neighbour_trap`, `test_fr8_construct_is_the_construction_phase`, `test_fr8_enforces_the_size_cap`, and 14 more) + `evals/test_gates.py::test_fr8_local_search_is_load_bearing_on_the_exact_suite`, `::test_fr8_local_search_lifts_every_hard_instance` | covered — gated |
| **FR-8 NFR** n = 500 reorder ≤ 60 s | `tests/test_optimizer.py::test_fr8_perf_smoke` | covered — **slow-marked, excluded from the default suite** and not eval-gated (EVALS.md §7). Run it with `uv run pytest flowlist/ --runslow -m slow` (the `--runslow` flag is what un-skips it; `-m slow` alone selects-then-skips). Verified in the hardening review: passes in ~13 s, well inside the 60 s budget. |
| **FR-9** Anchors | `tests/test_optimizer.py::test_fr9_anchors` (start-only / end-only / both / n=2), `::test_fr9_anchors_survive_local_search`, `::test_fr9_anchored_quality_matches_anchored_ground_truth`, `::test_fr9_anchored_construction_uses_seeded_diversification`, `::test_fr9_invalid_anchors_rejected`, `::test_fr9_exact_accepts_the_same_anchors`, `::test_fr8_anchored_result_is_a_local_optimum_within_its_constraints` + `evals/test_gates.py::test_fr9_anchored_instance_is_scored_under_its_anchors` + the **anchored instances in the M4 exact suite**, scored against fixed-endpoint Held-Karp | covered — gated |
| **FR-10** Arc profiles | **M3's build/cool property checks** (part of the `= 1.00` gate) + `tests/test_scoring.py::test_fr10_profiles` on `arc_01.json`, `::test_fr10_build_penalises_drops_and_cool_penalises_rises`, `::test_fr10_neutral_profile_is_symmetric`, `::test_fr10_profile_only_touches_the_energy_component`, `::test_fr10_profiles_change_the_ordering_on_a_symmetric_arc`, `::test_fr10_arc_fixture_is_symmetric_outside_energy`; `tests/test_cli.py::test_fr10_reorder_accepts_an_arc_profile` | covered — gated |
| **FR-11** Run persistence (append-only) | `tests/test_store.py::test_fr11_run_round_trips_with_its_breakdown`, `::test_fr11_runs_are_self_contained_after_features_change`, `::test_fr11_run_entries_must_be_a_permutation`, `::test_fr11_runs_are_append_only`, `::test_fr11_runs_need_a_playlist`, `::test_fr11_list_runs_is_ordered_and_filtered` + **M7's run-aggregate consistency check** | covered — gated |
| **FR-12** Apply & export | `tests/test_adapters.py::test_fr12_m3u_export`, `::test_fr12_m3u_handles_unknown_durations`, `::test_fr12_csv_export_columns`, `::test_fr12_json_export_is_the_run_payload`, `::test_fr12_writers_write_files`, `::test_fr12_unknown_format_rejected`; `tests/test_store.py::test_fr12_apply_rewrites_positions_in_place`, `::test_fr12_apply_is_idempotent`, `::test_fr12_apply_handles_a_full_reversal`, `::test_fr12_apply_unknown_run`; `tests/test_services.py::test_fr12_export_uses_the_features_the_run_was_scored_with`, `::test_fr12_export_of_a_single_entry_playlist`; `tests/test_cli.py::test_fr12_apply_command`, `::test_fr12_export_to_stdout_and_file`; `tests/test_api.py::test_fr12_apply_rewrites_positions`, `::test_fr12_export_formats` | covered (not gated) |
| **FR-13** API | `tests/test_api.py` (27 tests, incl. `::test_fr13_error_catalog_is_complete` — every code in the FR-13 catalog is reachable and mapped — `::test_fr13_openapi_lists_every_documented_route`, and `::test_fr13_served_app_works_against_a_sqlite_file`, the regression test for the served-SQLite thread bug REVIEW.md #27: it builds the app exactly as `flowlist serve` does, against a real SQLite file, and exercises it through TestClient's worker-thread hop) | covered (not gated) |
| **FR-14** CLI | `tests/test_cli.py` (29 tests, incl. `::test_fr14_help_lists_every_documented_command`, exit codes on every failure path, `--json` output, `--db` isolation) | covered (not gated) |
| **FR-15** Determinism | **metric M7 `determinism` (= 1.00)** — seed-7 repeatability, run-aggregate consistency, committed `golden_orderings.json`, and the seed-sensitivity vacuity guard — + `tests/test_optimizer.py::test_fr15_same_seed_same_order`, `::test_fr15_determinism_holds_under_anchors_and_profiles`, `::test_fr15_different_seeds_may_differ_but_stay_valid`, `::test_fr15_total_agrees_with_independent_rescoring`, `::test_fr15_rounding_makes_near_ties_stable` + `evals/test_gates.py::test_fr15_m7_repeatability_check_is_not_vacuous` | covered — gated |

## User stories (acceptance criteria)

| US | What covers it |
|---|---|
| US-1 import and list | `tests/test_cli.py::test_fr14_ls_and_show`, `::test_fr1_import_refuses_a_duplicate_name_without_replace`; `tests/test_services.py::test_fr1_import_is_idempotent_in_the_catalog` |
| US-2 coverage report | `tests/test_cli.py::test_fr14_analyze_reports_coverage`; `tests/test_explain.py::test_us2_coverage_summary` |
| US-3 reorder scorecard, reproducible | `tests/test_cli.py::test_fr14_reorder_prints_a_scorecard_and_is_reproducible`, `::test_us3_compare_reports_stored_and_recomputed_aggregates`; `tests/test_explain.py::test_us3_scorecard_shows_before_and_after` |
| US-4 explain every transition | `tests/test_cli.py::test_us4_explain_shows_one_line_per_transition`; `tests/test_explain.py::test_us4_transition_line_carries_every_documented_field`, `::test_us4_flags_are_rendered_with_labels`, `::test_us4_missing_values_read_as_na_not_zero`, `::test_us4_every_flag_has_a_label` |
| US-5 pinned endpoints + build profile | `tests/test_cli.py::test_fr9_reorder_honours_pinned_endpoints`, `::test_fr10_reorder_accepts_an_arc_profile`; `tests/test_scoring.py::test_fr10_profiles` (strictly-greater mean signed energy delta on `arc_01`) |
| US-6 local audio analysis | `tests/test_adapters.py::test_us6_fixture_analyzer_keys_on_basename`, `::test_us6_krumhansl_schmuckler_recovers_a_planted_key`, `::test_us6_key_estimation_validates_its_input`, `::test_us6_flat_chroma_does_not_crash`, `::test_us6_librosa_analyzer_explains_the_missing_extra` — see *Known gaps* (2) |
| US-7 export and apply | see FR-12 |
| US-8 fix bad data | see FR-4 |

## Known gaps — stated plainly

1. **FR-2's tag reading is not exercised against a real tag library.** FR-2
   names `mutagen` as a core dependency, but it is not installed in this
   workspace (REVIEW.md #23), so it ships as the optional extra
   `flowlist[tags]`. `adapters/readers.py::read_tags` imports it lazily and
   falls back to the documented `Artist - Title` filename pattern; that
   *fallback* is what the tests pin
   (`test_fr2_tag_reading_degrades_without_mutagen`, plus a monkeypatched
   fake-mutagen path in `test_fr2_directory_scan`). The tag-reading branch
   against genuine ID3/FLAC files is untested here. Everything else in FR-2 —
   recursive scan, extension filter, lexicographic order, sha1 identity, no
   audio decoding — is fully covered.

2. **US-6's live analyzer (`LibrosaLocalAnalyzer`) is not exercised end to
   end.** SCOPE.md's non-goals make live adapters best-effort and explicitly
   not eval-gated, and CONVENTIONS.md forbids adding heavy ML dependencies this
   pass. What *is* tested is the part that can be: the pure
   Krumhansl–Schmuckler key finder (`estimate_key`) recovers a planted key from
   a synthetic chroma histogram, validates its input, and survives a flat
   histogram; and `available()`/`analyze()` raise a clear
   `AdapterUnavailableError` naming the extra when librosa is absent. The
   librosa-dependent code path (`beat_track`, `chroma_cqt`, RMS → energy/dBFS)
   has no automated coverage in this workspace.

3. **`SpotifyMetadataProvider` is credential-gated and never contacted.**
   Tested for credential gating, for the FR-1 sentinel translation applied to a
   canned Spotify payload, and for making no network call without credentials
   (`test_live_spotify_never_touches_the_network_without_use`). No live HTTP is
   exercised — by design (CONVENTIONS.md §3, and the endpoint was deprecated
   for new apps in Nov 2024).

4. **Test-file names drifted from EVALS.md §7.** The plan named
   `tests/test_import.py` and `tests/test_export.py`; the build put the reader
   tests in `tests/test_adapters.py` alongside the other adapters and split the
   export coverage across `test_adapters.py` (writers), `test_store.py` (apply
   transaction), `test_services.py`, `test_cli.py` and `test_api.py`. The
   coverage is present and named above; EVALS.md §7 now points here. No FR is
   uncovered by this drift.

5. **Cross-platform determinism is a design goal, not a verified claim.**
   FR-15 says so explicitly; M7 certifies *within*-platform determinism and the
   committed `golden_orderings.json` is the mechanism that would surface drift
   as a CI diff on another machine. Nothing in this repo can prove the
   cross-platform half from a single-machine suite.
