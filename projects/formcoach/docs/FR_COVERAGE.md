# FormCoach — FR coverage matrix

Produced at the hardening stage (2026-07-31). Every FR in `docs/SCOPE.md` maps
to the tests and/or eval gates that exercise it. Test counts are test
*functions*; the suite expands to 598 passing tests with parametrization.
Gate values are from the committed-fixture scorecard (`evals/run.py`), which is
deterministic — two consecutive runs produce byte-identical output.

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Singleton profile CRUD | `tests/test_fr1_models_and_invariants.py` (22 fns: field bounds, emphasized ≤ 3, singleton row, e1RM nullability); API `test_fr1_*`; CLI `test_fr1_*` | Implemented, tested |
| FR-2 | Exercise library + hermetic init integrity | `tests/test_fr2_datasets_and_store.py` (38 fns: dataset validation, media integrity local-by-existence / url-by-schema, filters, feasibility invariant); API/CLI `test_fr2_*` | Implemented, tested |
| FR-3 | Program generation per the documented algorithm | **Gate M5 = 1.000** (120 profiles × 15 constraints, eval-owned split + coverage fixtures); `tests/test_fr3_programming.py` (28 fns) | Implemented, gated |
| FR-4 | Volume accounting vs landmarks | `tests/test_fr4_volume.py` (12 fns: attribution, bands, target/incidental, all 15 muscles); M5 constraints 1 & 15 route through the same `accumulate_effective` | Implemented, tested |
| FR-5 | Append-only logging, kg canonical, RIR-adjusted Epley | `test_fr5_*` in `tests/test_fr2_datasets_and_store.py` (sqlite append-only, kg storage, date filter) and `tests/test_fr1_models_and_invariants.py` (e1RM iff loaded ∧ rated); API `test_fr5_*` (lb→kg at boundary); CLI `test_fr5_*` (set-spec parsing) | Implemented, tested |
| FR-6 | Progression decision ladder | **Gate M6 = 1.000** (28 golden scenarios asserting action + clause + load); `tests/test_fr6_progression.py` (42 fns, both branches, precedence conflicts, rounding) | Implemented, gated |
| FR-7 | Pose ingestion, view resolution, screening | **Gate M4 = 1.000** (62/62: acceptance, reject reasons, 4 undeclared-view clips, 0.28/0.32 boundary pair); `tests/test_fr7_poseio.py` (15 fns); `tests/test_adapters_pose_media.py` (sidecar parsing, live-adapter import error path) | Implemented, gated |
| FR-8 | Rep segmentation (smoothing + prominence + duration) | **Gate M2 = 1.000** (≥ 0.90) over 50 valid clips at 24/30/60 fps; `tests/test_fr8_reps.py` (19 fns: window from fps, median/SG filters, prominence, min separation) | Implemented, gated |
| FR-9 | Complete fault-rule matrix per rep | **Gates M1 = 0.957 (≥ 0.80), M1b = 0.833 (≥ 0.55), M4b = 1.000, M7 = 0.000 (≤ 0.02)**; `tests/test_fr9_faults.py` (17 fns) + `tests/test_geometry_features.py` (26 fns, incl. anterior-frame sign checks) | Implemented, gated |
| FR-10 | Persisted report, scoring, prioritized corrections | **Gates M3a = 1.93° (≤ 5°), M3b = 0.026 (≤ 0.03)**; `tests/test_fr10_report.py` (23 fns: penalties, correction ordering, rejected-analysis invariants) | Implemented, gated |
| FR-11 | FastAPI surface | `tests/test_fr11_api.py` (34 fns), including `test_fr11_openapi_documents_every_scoped_endpoint` which pins the SCOPE endpoint list | Implemented, tested |
| FR-12 | Typer CLI | `tests/test_fr12_cli.py` (29 fns); every documented command additionally executed end-to-end on a real SQLite DB during hardening (init → profile → program → log → next → volume → analyze/photo/show/list) | Implemented, tested |
| FR-13 | Safety behavior (ack gate, pain substitution, vocabulary audit) | `tests/test_fr13_safety.py` (23 fns) + API/CLI `test_fr13a/b/c_*`; substitution and `pain_flag_no_substitute` drop both covered; static audit walks every cue/rationale/error template | Implemented, tested |
| FR-14 | Determinism & hermeticity | `tests/test_fr14_determinism.py` (10 fns: AST audit of engine sources for clock/fs/network/unseeded RNG, same-seed reproducibility); constraint 12 inside M5 (regenerate-and-compare per profile); scorecard run twice → identical output | Implemented, tested |
| FR-15 | Single-frame photo analysis | `test_fr15_*` in `tests/test_fr10_report.py` (7 fns: synthetic rep at frame 0, phase gating, `needs_multi_frame`, rejection), API/CLI `test_fr15_*`; M1/M4b consume the 6 photo fixtures | Implemented, gated |

Non-gated, reported-only: **M8** (real-clip agreement) prints `NOT AVAILABLE`
because `evals/fixtures/real/` contains no hand-labelled clips of the owner's
lifts yet. This is the behavior EVALS.md specifies for the pre-filming state;
the synthetic-to-real transfer gap therefore remains unmeasured (see
REVIEW.md § Hardening, "weakest part").

No FR is unimplemented or partially implemented.
