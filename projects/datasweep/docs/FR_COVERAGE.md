# datasweep — FR coverage

Produced at the HARDEN stage. Every FR in SCOPE.md maps to the tests and/or
eval gates below; counts are FR-tagged test functions actually collected by
pytest (405 tests total including parametrized cases; all passing). Eval gates
are asserted at the EVALS.md §5 thresholds by `evals/test_gates.py` and by
`evals/run.py` (exit code). Status is **covered** only where the covering
check was seen to fail under a deliberate engine mutation or exercises the
behaviour directly; nothing below is aspirational.

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Watch config, deterministic scan, ignore patterns, settle check | `tests/test_watcher.py` — 15 tests (`test_fr1_scan_is_lexicographic_and_deterministic`, `test_fr1_output_dir_is_never_scanned`, `test_fr1_unstable_before_the_settle_window_elapses`, …), injected clock only | covered |
| FR-2 | Idempotence on (content, policy, engine); `--force`; failed/skipped never suppress | `tests/test_services.py::test_fr2_idempotent_skip`, `::test_fr2_force_rerun`, `::test_fr2_failed_run_does_not_suppress` + 4 more; exercised live by the CLI e2e (second `run --once` → 2 skipped) | covered |
| FR-3 | Defensive reading: dialects, ragged rows, dup headers, headerless heuristic, JSONL key union, XLSX | `tests/test_readers.py` — 24 tests (`test_fr3_delimiter_sniffing`, `test_fr3_headerless_file_gets_synthetic_headers_and_a_review_issue`, `test_fr3_jsonl_key_union_in_first_seen_order`, `test_fr3_xlsx_sheet_selection_by_index_and_name`, …) | covered |
| FR-4 | Encoding cascade + mojibake round-trip repair | **M2(ENC) F1 = 1.0, M4(ENC) = 1.0** + `tests/test_encoding.py` — 7 tests (`test_fr4_detection_cascade_order`, `test_fr4_latin1_never_fails`, …) | covered |
| FR-5 | Micro-parser voting, digits-vs-integer, bool vocabulary, headers never inputs | **M1 = 1.0 (gate ≥ 0.95; drops to 0.12 when inference is degraded — see REVIEW.md HARDEN)** + `tests/test_inference.py` — 40 tests incl. `test_fr5_headers_do_not_influence_type` | covered |
| FR-6 | Eight detector families at cell granularity; empty cell is not an issue; STR excluded | **M2 macro-F1 = 1.0, M2_min = 1.0 (drop to 0.50 / 0.00 under mutation)** + `tests/test_detectors.py` — 43 tests incl. `test_fr6_empty_cell_is_not_an_issue`, `test_fr6_mixed_convention_advisory` | covered |
| FR-7 | Confidence formulas, tier algebra `min(cap, conf_tier)`, policy overrides | **M3 = 1.0, M3_trap = 0, M3_clean = 0, M3_clean_findings = 0, M4 = 0.946/1.0/1.0, M7 = 1.0** (M3_trap → 11 and M7 → 0.556 under an always-auto mutation; M4_auto → 0.84 and M4_auto_min → 0.0 under a demotion mutation) + `tests/test_planner.py` — 26 tests | covered |
| FR-8 | Fixed pipeline order; audit completeness (diff == entries, no no-ops) | `tests/test_transforms.py` (7 fr8-tagged incl. completeness invariant) + `tests/test_pipeline.py` stage-order tests | covered |
| FR-9 | `revert(cleaned, audit)` reconstructs the parsed original; CLI check | **M5 = 1.0 over 19 runs (13 corrupted + 5 golden + 1 revision-2)** + `tests/test_transforms.py::test_fr9_revert_row_drop_and_headers`, `tests/test_review.py::test_fr9_revision_audit_is_complete_not_a_delta`; CLI `revert` verified e2e on r1 and r2 | covered |
| FR-10 | Four artifacts, atomic writes, source never touched, no ids/timestamps/paths | `tests/test_artifacts.py` — 21 fr10-tagged tests (`test_fr10_source_bytes_are_never_touched`, `test_fr10_writes_leave_no_partial_files_behind`, `test_fr10_findings_log_has_one_line_per_instance`, …) | covered |
| FR-11 | Review queue, single transition, complete revisions, no re-proposal after reject | `tests/test_review.py` — 8 tests (`test_fr11_item_ids_are_content_derived_and_stable`, `test_fr11_single_transition`, `test_fr11_every_review_cell_is_also_a_finding`, …) + M7's review expectations | covered |
| FR-12 | Append-only runs, profiles, summaries, count reconciliation | `tests/test_store.py` — 15 fr12-tagged tests (`test_fr12_runs_are_append_only`, `test_fr12_issue_counts_match_the_findings_log`, `test_fr12_issue_summaries_reconcile_with_run_counts`, …) | covered |
| FR-13 | Daemon loop / `--once`; notifier invoked per run | `tests/test_cli.py::test_fr13_run_once` + `tests/test_artifacts.py::test_fr13_log_notifier_appends_one_line_per_run`; the polling loop body beyond `--once` is a `time.sleep` wrapper exercised manually, not unit-tested (deterministic single-pass mode is the tested contract, per SCOPE FR-13) | covered |
| FR-14 | FastAPI endpoints, structured error codes | `tests/test_api.py` — 13 tests covering every endpoint and every documented detail code (`unknown_run` 404, `item_already_decided` 409, `unsupported_format` 415, `file_too_large` 413, `unknown_item` 404) + live `serve` smoke in HARDEN | covered |
| FR-15 | Typer CLI, exit codes 0/1/2, `--json` | `tests/test_cli.py` — 17 tests (`test_fr15_usage_error_exits_2`, `test_fr15_profile_writes_nothing`, …) + full command walkthrough on real inputs in HARDEN | covered |
| FR-16 | Byte-identical artifacts across processes and hash seeds | **M6 = 1.0** (two subprocesses, `PYTHONHASHSEED=0` vs `1`, all four artifacts byte-compared on 4 fixtures) + 8 fr16-tagged tests (`test_fr16_no_ids_timestamps_or_paths_in_the_artifacts`, …); the whole scorecard JSON is byte-identical across two consecutive full runs | covered |
| US-3 | Ambiguity surfaced, never auto-applied, both candidates attached | **M3_trap = 0 and M7 = 1.0** + `evals/test_gates.py::test_us3_ambiguous_date_carries_both_interpretations` | covered |
| US-6 | Per-folder policy tuning incl. rule `off` | `tests/` us6-tagged tests + HARDEN e2e: `fix.drop_duplicate_row = "off"` leaves duplicates in place and the report names the disabled rule; unknown policy keys fail loud (exit 1) | covered |
| US-8 | Excel in, portable out; serial dates review-proposed; leading zeros protected | **M4 repair_total (excel_serial scored via recommended candidate), M7 `trap_leading_zeros` (`digits` typing asserted)** + `tests/test_readers.py::test_us8_xlsx_serial_number_dates_survive_as_integers` | covered |

## Known gaps (stated, not papered over)

- **Live adapters are smoke-level only.** `WatchdogWatcher`, `CharsetNormalizerDetector`
  and `DesktopNotifier` activate behind optional extras and are explicitly not
  eval-gated (SCOPE.md Non-goals); the offline defaults carry all tests. This
  is the documented contract, not an omission.
- **The daemon's sleep loop** (`datasweep run` without `--once`) is not under
  test; only the single-pass body is. A latent bug in the loop wrapper
  (signal handling, drift) would not be caught. Accepted: the loop is 4 lines
  around the tested pass.
- **`repair_auto` for CAT is 0.67 by design** — the `typo_label` share is
  review-tier per D10b and is scored by `repair_total` (= 1.0), not
  `repair_auto`. Recorded here so nobody reads the per-class table as a
  regression.
