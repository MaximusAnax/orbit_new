# dresscast — FR coverage

Verified during the hardening pass (2026-07-31) against the shipped test and
eval suites. "Test" names are real pytest functions (all passing; 407 total);
"Metric" names are gated rows on the eval scorecard (all passing; gates in
EVALS.md §5.4). Where a covering artifact is a whole family, the family's
representative names are listed rather than every member.

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Garment creation/validation, presets, warmth→clo, `overridden_fields` | `tests/test_garments.py` (40 asserts: preset cascade, clo ±0.15 bounds, name uniqueness, occasions non-empty, warmth mapping, override recording); CLI `add`/`edit` in `tests/test_cli.py` | Covered |
| FR-2 | Photo attach, suggestion staging, accept cascade + rollback | `tests/test_suggest.py` (staging, accept/reject one-transition, cascade skips `overridden_fields`, rollback on re-validation failure keeps `pending`, `accepted_fields` provenance incl. cascade entries, no-extractor error); `test_api.py` suggest/accept endpoints | Covered |
| FR-3 | Laundry lifecycle, transitions, auto-dirty, resets | `tests/test_laundry.py` (state machine, auto-dirty at threshold, `--all` reset, retired exclusion, `invalid_transition`); rollout side effects exercised inside **M6** (laundry on days 7/14 drives the simulation) | Covered |
| FR-4 | Forecast snapshot, 23–25 rows, `seq` ordering, Open-Meteo mapping | `tests/test_weather_adapters.py` (both DST fixtures, seq contiguity, physical ranges, append-only snapshots, committed Open-Meteo sample mapped offline, percent→fraction PoP, `apparent_temperature` ignored) | Covered |
| FR-5 | Feels-like with ramps, bare/config split, wind attenuation | **M1** wind-chill chart family (Environment Canada cells), Steadman family, Lipschitz-3 continuity family; `tests/test_comfort.py` (`test_fr5_*`: ramp endpoints, disjoint support, attenuation, worked example) | Covered |
| FR-6 | Required clo, achievable band, `S_thermal` aggregate | **M1** anchors + external calibration (±0.30 residuals), **M2/M2_worst**, **M2c**; `tests/test_comfort.py` (`test_fr6_*`: clamps, met scaling, band arithmetic vs EVALS §5.2, floor-with-cover, exposure weighting, worst-hour blend) | Covered |
| FR-7 | Icl regression, configurations, LIFO, hysteresis/dwell/cap, compression | **M1** ensemble family, **M4(h)** plan smoothness at 1.00; `tests/test_configs.py` (`test_fr7_*`: config count, total Icl order, LIFO shed order, hysteresis, dwell, change cap, forced rain switch, lossless compression, carried slots) | Covered |
| FR-8 | Assembly under HC-1…HC-8 — the hard part | **M2, M2b, M2c, M3, M4(a–d), M8**; `tests/test_assemble.py` (per-constraint tests HC-1…HC-8, tie-breaks, MMR diversity, partial_k, candidate cap, wardrobe cap, parameter response). Falsifiability of M3/M2b/M2c demonstrated in REVIEW.md §Hardening | Covered |
| FR-9 | Rain/wind adequacy, `S_protect`, accessory attachment | **M4(a)** HC-6, **M4(f)** attachment reproduced independently, **M9** soft response; `tests/test_protection.py` (intensity bands, umbrella wind cap + heavy-rain exclusion, penalty magnitudes, exposure weights, priority/4-cap, HC-4-compatible selection) | Covered |
| FR-10 | Color harmony and style scoring | **M5 (1.00), M5_color, M5_style, M5b, M5_mono, M8**; `tests/test_palette.py`, `tests/test_style.py` (hue-zone table, circularity, 3-family penalty, tightness/cohesion blend) | Covered |
| FR-11 | Variety score, HC-8, role-snapshot history | **M6** (worst-of-three rollouts), **M8 lift_variety**; `tests/test_variety.py` (half-life math, worked example, exact-set-only HC-8, snapshot stability under re-tagging) | Covered |
| FR-12 | Wear logging, transactions, same-day undo | `tests/test_wear.py` + `tests/test_store.py` (`test_fr12_*`: counter side effects in one transaction, role snapshot at log time, undo same-day only, dirty-allowed/retired-refused) | Covered |
| FR-13 | Append-only recommendation persistence, self-contained plans | `tests/test_store.py` (`test_fr13_*`: round trip with plans/reasoning, self-contained after wardrobe edit, latest-by-date); **M7** check 2 (stored numbers recomputed from stored inputs) | Covered |
| FR-14 | Relaxation ladder, partial_k vs compromise, infeasible + brief | **M10** at 1.00 (edge×8 matrix; earliest-feasible-prefix brute-forced), **M4(i)**; `tests/test_degradation.py` (ladder prefixes, R1/R2/R3 firing conditions, brief payload) | Covered |
| FR-15 | Classified reasoning, iff-emission | **M4(g)** class-set equality vs the checker's own trigger computation; `tests/test_explain.py` (one case per line class, byte stability, no `notes` passthrough, table order) | Covered |
| FR-16 | Wardrobe-free day brief | **M10(d)** (brief rides the infeasible error); `tests/test_brief.py` (empty DB, archetype brackets, advisories, met/window response, DST days, quantization) | Covered |
| FR-17 | API endpoints + structured error codes | `tests/test_api.py` (every endpoint incl. `/brief` on an empty DB; `infeasible_wardrobe` 422, `unknown_garment` 404, `invalid_transition` 409, `no_extractor_configured`, `invalid_params`, `wardrobe_too_large`) | Covered |
| FR-18 | CLI commands, exit codes, `--json`, `--units f` | `tests/test_cli.py` (Typer runner: every command, non-zero on failure, JSON mode, °F conversion at presentation only); hardening pass additionally ran every documented command end to end on a real database (REVIEW.md §Hardening) | Covered |
| FR-19 | Determinism, quantization, wardrobe_hash | **M7** at 1.00 (byte-identity, recomputation, hash request-independence, 1-ULP perturbation), **M4(k)** quantization; `tests/test_determinism.py` | Covered |

Notes:

- The only FR-level behavior that is deliberately *not* shipped live is the
  `VisionAttributeExtractor` (SCOPE.md §Non-goals: next phase, optional
  `vision` extra). The protocol, fixture extractor and confirmation flow that
  FR-2 requires this pass are implemented and tested; the live class exists
  behind a lazy import and a clear `no_extractor_configured` error, and is
  exercised only for its error paths, exactly as scoped.
- `OpenMeteoWeatherProvider` is live-but-never-eval-gated per SCOPE.md
  §Non-goals; its mapping function is unit-tested against a committed sample
  response with no network.
