# VoiceKin — FR coverage matrix

Hardening-pass audit (2026-08-01): every functional requirement in
`docs/SCOPE.md` mapped to the tests and/or eval gates that exercise it.
Test names carry FR ids by convention; run `uv run pytest voicekin/ -q`
(285 tests) and `uv run python voicekin/evals/run.py` (11 gates) from
`projects/`. Status is the observed result on the current commit.

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Instance & profile lifecycle, `awaiting-consent` flag | `test_fr1_init_creates_the_single_instance_row_and_dirs`, `test_fr1_profile_crud_and_audit`, `test_fr1_services_fail_fast_without_an_instance`, `test_fr1_awaiting_consent_flag_appears_after_the_grace_window`, `test_fr1_awaiting_consent_*` (3 unit tests in `test_consent_fr5_fr6.py`), `test_instance_is_a_single_row`, CLI `test_fr14_init_is_idempotent_only_once`, `test_fr14_profile_list_and_show` | PASS |
| FR-2 | WAV intake, resample to 16 kHz, quality screening with machine-readable reasons | `test_audio_fr2.py` (12 tests: codec, mono-mix, resample, refusal of bad formats/rates), `test_quality_fr2.py` (10 tests: every reject reason incl. boundary inclusivity), service-level `test_fr2_a_bad_recording_is_rejected_with_a_reason`, `test_fr2_non_wav_bytes_are_rejected_as_bad_format`; M3 condition 2 re-screens rendered output | PASS |
| FR-3 | Enrollment: minimums, LOO coherence, centroid, fingerprint, voice-param derivation | `test_enrollment_fr3.py` (17 tests), `test_fr3_a_mixed_speaker_set_is_refused_and_rolled_back`, `test_fr3_duplicate_audio_is_refused`, `test_fr3_removing_a_sample_changes_the_fingerprint`; gates **M6_pure/M6_mixed = 1.00**, M4 scenarios #28–29 | PASS |
| FR-4 | `SpeakerEmbedder` adapter: 16-dim DSP embedding, no dead feature family | `test_dsp_fr4.py` (21 tests: F0/LPC/formants/tilt/bands + `test_fr4_single_axis_siblings_are_rejected_no_family_is_dead`, `test_fr4_pitch_mimic_is_rejected`, calibration-mismatch refusal); gates **M1a = 0.0104 ≤ 0.05**, **M1b = 0**, **M1c = 0.059 ≤ 0.12**; tripwire `test_calibration_matches_dev_split_fr4` | PASS |
| FR-5 | Consent draft precondition + grant decision in documented order; rejected audio discarded | `test_consent_fr5_fr6.py` FR-5 block (14 tests), service tests `test_fr5_*` (10, incl. impostor-audio-discarded, second-draft-refused, below-minimum grant → `not_enrolled`); gates **M2a = 0/330**, **M2b_clean = 0.958**, **M2b_all = 0.896**, M4 #19–21, #23, #30 | PASS |
| FR-6 | `authorize()` complete mediation, governing record, 9-reason precedence | `test_consent_fr5_fr6.py` FR-6 block (23 tests incl. forge-proof `Authorization`, precedence pairs, non-resurrection), service tests `test_fr6_*` (6); gate **M4 = 35/35**, scenarios #1–17, #22, #24–27, #31–33, #35 | PASS |
| FR-7 | Revocation instant & total; purge erases everything VoiceKin controls | `test_fr7_revocation_stops_the_very_next_synthesis`, `test_fr7_revoking_twice_is_a_no_op_with_one_audit_record`, `test_fr7_purge_erases_everything_voicekin_controls`, `test_fr7_purge_is_terminal`, `test_fr7_purge_never_deletes_a_file_voicekin_did_not_write`; M4 #5, #6, #16, #17, #32, #34; CLI `test_fr14_purge_prints_counts_and_the_residual_warning` | PASS |
| FR-8 | `Synthesizer` adapter: deterministic Klatt-style stub, identity-bearing | `test_synthesis_fr8_fr9.py` FR-8 block (17 tests: byte determinism, param/seed/text sensitivity, re-embeds to own profile); gate **M3 = 0.944 ≥ 0.93** (all three conditions per trial) | PASS |
| FR-9 | Rendering pipeline: normalization, hashing, persistence, byte determinism | `test_fr9_text_normalization`, `test_fr9_number_to_words`, `test_fr9_duration_is_exactly_unit_count_times_unit_duration`, `test_fr9_package_output_hashes_the_payload`, `test_fr9_stub_output_passes_the_quality_screen`, `test_fr6_authorized_synthesis_renders_and_audits`; M3 conditions 2–3 | PASS |
| FR-10 | Output provenance by data-chunk hash; `verify-output`; sidecar manifest | `test_fr10_payload_hash_ignores_container_metadata`, `test_fr10_payload_hash_tracks_the_audio`, `test_fr10_missing_data_chunk_is_reported`, `test_fr10_verify_output_resolves_a_wav_to_its_utterance`, `test_fr10_an_unknown_wav_has_no_provenance`, `test_fr11_delivery_writes_a_wav_and_a_manifest`, CLI/API verify-output round-trips | PASS |
| FR-11 | `DeviceDeliverer` adapter; delivery re-authorizes; receipts audited | `test_fr11_delivery_re_authorizes_so_revocation_stops_replays`, `test_fr11_delivery_writes_a_wav_and_a_manifest`, `test_fr11_only_rendered_utterances_can_be_delivered`; M4 #17, #18, #34. Live `HomeAssistantDeliverer`: `test_deliver_hass_fr11.py` (6 hermetic tests added in the hardening pass — env-gating, media copy + exact `play_media` request incl. bearer token, HTTP-error/network-error/unwritable-dir receipt mapping, foreign-target and foreign-authorization refusal — transport monkeypatched); a call against a *real* Home Assistant remains unexercised (SCOPE non-goal 8) | PASS (real-HA call unexercised by design) |
| FR-12 | Append-only hash-chained audit log; `audit verify` reports first bad seq + head anchor | `test_ids_audit_fr12_fr15.py` (18 FR-12 tests incl. all 7 tamper classes and the competent-forger case), `test_fr12_repository_exposes_no_audit_mutation`, `test_fr12_audit_rollback_leaves_no_gap`, service-level chain tests; gate **M5 = 8/8** | PASS |
| FR-13 | FastAPI automation surface: 12 routes, documented error catalog | `test_api_fr13.py` (14 tests: 200/403/404/409/422 mapping, no biometric vectors in responses, revoke-by-id governing-only); live smoke `uvicorn --factory voicekin.api:create_app` exercised in the hardening pass | PASS |
| FR-14 | Typer CLI administrative surface, documented exit codes | `test_cli_fr14.py` (15 tests: exit codes 0/1/2/3, per-file enroll report, statement print, purge warning); full end-to-end walkthrough on real corpus WAVs in the hardening pass (init → enroll → draft → impostor/replay/genuine grant → say → deliver → verify-output → revoke → replay refusal → drift → disable → purge → audit verify) | PASS |
| FR-15 | Determinism & hermeticity: derived ids, nonce from persisted seed, replay contract | `test_fr15_ids_are_32_lowercase_hex_and_deterministic`, `test_fr15_each_id_material_field_changes_the_id`, `test_fr15_id_kinds_do_not_collide`, `test_fr15_nonce_is_base32_derived_from_the_persisted_seed`, `test_fr15_replaying_the_same_operations_reproduces_everything` (ids, bytes, chain hashes), `test_fr15_repeating_a_synthesis_advances_the_attempt_index`; eval suite double-run reproduces identical scorecards | PASS |

## Honest gaps

- **`EcapaEmbedder` / `XttsSynthesizer` (FR-4/FR-8 live adapters)** are
  interface-fixed stubs behind optional extras, per SCOPE non-goal 2: they
  raise a clear error naming the missing extra and are never imported on the
  test/eval path. They are *not* implemented — that is the documented plan,
  not an omission.
- **`HomeAssistantDeliverer`'s HTTP POST** is unit-covered against a
  monkeypatched transport (`test_deliver_hass_fr11.py`, added in this pass),
  but no test performs a real network call — the eval/test environment is
  hermetic by rule, so whether an actual Home Assistant accepts the request
  shape has never been observed in CI.
- **Real-voice error rates are unmeasured** (EVALS "what passing does and
  does not certify"): all speaker-verification gates run on the synthetic
  source-filter corpus. This is the documented, deliberate limit of the MVP.

## Falsifiability audit (hardening pass, 2026-08-01)

Each safety-critical gate was empirically shown to fail under a targeted
engine mutation and to recover exactly on revert (measured numbers and the
mutations in `docs/REVIEW.md`, "Hardening pass"):

- dead formant family in `engine/dsp.py` (constant F1/F2) →
  **M1b 0.0000 → 0.1250** (FAR_vtl 2/16), **M2a 0 → 2/330** impostor
  accepts, **M6_mixed 1.0000 → 0.9583**; all three back to gate-passing
  values after revert;
- `authorize()` without the revocation check (`engine/consent.py`) →
  **M4 1.0000 → 0.9143** (32/35; the failures are exactly the revocation
  scenarios #5, #17, #26); 35/35 after revert;
- `verify_chain` without content re-hashing (`engine/audit.py`,
  linkage-only) → **M5 1.0000 → 0.6250** (5/8; the misses are exactly the
  content-tamper cases: detail edit, ts edit, prev-hash-ignoring rehash);
  8/8 after revert.
