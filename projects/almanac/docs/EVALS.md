# Almanac — Evals

## 1. What this product lives or dies on

Two capabilities, per SCOPE.md:

- **H1 — Scheduling quality under realistic use.** The daily scheduler must
  *simultaneously* hold every promise it makes over months of simulated
  usage: hard no-repeat cooldown, prompt novelty/review balance near the
  target, per-entry spacing that tracks the reflection-driven intervals,
  fast debuts for new captures, no starvation, pinned favorites recurring,
  duds fading after flat reflections, graceful behavior on tiny libraries
  and sporadic users — all byte-reproducible from a seed. Each property is
  trivial alone; the product is the conjunction, so the evals simulate full
  years and gate every property on the same timelines.
- **H2 — Application-prompt pairing.** Theme suggestions must be right
  (else prompts are misfiled forever), surfaced prompts must match the
  entry's themes, rotate kinds without repeating templates, and the
  optional personalizer must be structurally unable to corrupt a prompt.

Capture, search, collections, import/export, API/CLI plumbing are covered
by ordinary pytest unit/integration tests, not eval gates.

All evals are hermetic: offline adapters only (`TemplatePersonalizer`,
`LocalAttributionChecker`, `FixedClock`), in-memory store, committed
fixtures, hash-keyed determinism, no network, no wall clock.

## 2. A note on circularity, and how we avoid it

The interval-update table (FR-6) is a *design parameter* — there is no
external ground truth for "the correct interval after a `resonated`
grade". So we do not pretend to measure interval correctness; the FR-6
table is pinned by unit tests and the M7 replay gate. What the eval suite
measures is **whether the system delivers the contract it declares**, on
timelines it cannot see at design time:

- Truths that are *predicates* (M1, M7): cooldown, uniqueness, theme
  match, idempotence, replay equality — checkable by definition against
  the produced timeline.
- Truths that are *planted* (M6): each fixture entry carries a hidden
  latent quality (`gem`/`decent`/`dud`) that drives the simulated
  reflection grades; the scheduler never sees it. Responsiveness is
  measured against the plant, not against the engine's own bookkeeping.
- Truths that are *hand-labeled and held out* (M8): theme labels on real
  public-domain quotes, with the gate applied only to the half the lexicon
  authors never tuned against.
- Truths by *construction* (M9): scripted mutations that are invalid by
  the FR-10 contract.
- Contract targets under contention (M2–M5): latency, mix, spacing, and
  recurrence are measured properties of the timeline compared to the
  numbers SCOPE.md promises the user; two live-computed naive baselines
  (§5) show the gates are not vacuous.

## 3. Metrics

Vocabulary: a *run* is `simulate(scenario, seed)` — day-by-day driving of
the real service (materialize daily set on each date in the scenario's
usage calendar, apply the persona's reflections, apply the capture
stream). `T(run)` is the resulting surfacing/reflection timeline.

### M1 — constraint_compliance (H1 + H2, predicate gate)

Over all runs (S1–S4, seed 7), count violations of:

- (a) cooldown: entry surfaced with `D − last_surfaced_on < W` without
  `relaxed_cooldown` flag;
- (b) same-day duplicate: entry appearing twice on one date;
- (c) ineligible: archived entry surfaced, or `daily` set materialized for
  a date ≤ a previously materialized daily date;
- (d) prompt reuse: template id repeating within an entry's last 6
  surfacings while ≥ 1 alternative candidate existed (the exhaustion
  retry in FR-9 step 2 is logged and exempt);
- (e) theme mismatch: prompt's theme ∉ entry.themes, or theme = `general`
  while entry has ≥ 1 theme;
- (f) idempotence: re-invoking materialization for an already-materialized
  date yields anything but the stored, byte-identical card set.

```
M1 = 1.0 if total violations across all runs == 0 else 0.0
```

### M2 — debut_latency (H1)

Over S1 + S2 (pooled), for every entry captured ≥ 60 days before run end
(censoring guard): `latency(e) = first_surfaced_on − captured_on` in days.

```
M2_p50 = median latency        M2_p95 = 95th percentile latency
```

Also checked inside M2: zero entries with `latency > 180` (hard starvation
bound; the S = 90 forcing rule plus queueing slack makes 180 safe).

### M3 — mix_balance (H1)

Over S1 and S2: for every window of 28 *consecutive materialized* days in
which both pools were non-empty on every day (pool emptiness is recorded
by the simulator), let `σ_w` = fraction of `daily` surfacings in the
window whose entry had `exposure_count = 0` at selection.

```
M3 = max_w | σ_w − ρ |        (ρ = 0.35)
```

### M4 — spacing_fidelity (H1)

Over S1, S2, S4: for each review surfacing sᵢ (i ≥ 2) of entry e:
scheduled due date `d* = last_surfaced_on + I_eff`; achievable date
`a* = min materialized date ≥ d*` (accounts for days the user never opened
the tool — crucial for S4); error

```
err(sᵢ) = (on_date − a*) / I_eff   if on_date ≥ a*   (lateness under contention)
        = (d* − on_date) / I_eff   if on_date < d*   (early surfacing, fallback paths only)
M4 = mean over all review surfacings of err
```

### M5 — pinned_recurrence (H1)

Over S1 (8 pinned entries), steady-state region (day 60 → end): for each
pinned active entry, `maxgap(e)` = the largest gap in days between
consecutive exposures (and from region start to first exposure in region).

```
M5 = fraction of pinned entries with maxgap(e) ≤ 45
```

45 = pinned cap 21 × ~2 contention slack; a pinned favorite you haven't
seen in a month and a half is a broken promise.

### M6 — feedback_responsiveness (H1, planted truth)

Over S1 + S2: cohorts by planted latent quality — `dud` entries that
accumulated `flat_streak ≥ 3` by day 180, vs `gem` entries with ≥ 3
exposures by day 180. In the tail window [day 185, end]:

```
rate(cohort) = mean over entries of (# exposures in tail window)
M6 = rate(dud cohort) / rate(gem cohort)
```

Sub-check (gated with M6): every dud with `flat_streak ≥ 3` appears in the
final `stats` archive-candidate list (fraction = 1.0).

### M7 — determinism_replay (H1, FR-17)

- (a) run S1 twice with seed 7: full timelines (entry ids, template ids,
  prompt texts, flags) byte-identical;
- (b) run S1 with seed 8: differs from seed 7 in ≥ 1 selection (proves
  jitter is live);
- (c) at 12 monthly checkpoints in S1, recompute every entry's
  scheduler state by the FR-6 fold from the event log and compare to the
  incrementally maintained cache: all equal;
- (d) serialize/reload the store mid-run (SQLite round-trip for this check
  only, tmp path): continuation identical to uninterrupted run.

```
M7 = 1.0 if a–d all hold else 0.0
```

### M8 — theme_suggestion_accuracy (H2, held-out labels)

Over `themes_labeled.json` **held split only** (40 quotes; the dev split
is for lexicon tuning and reported but ungated): suggester returns ranked
themes; label = hand-assigned primary theme.

```
M8_top1 = (# quotes where rank-1 suggestion == label) / 40
M8_hit3 = (# quotes where label ∈ top-3 suggestions) / 40
```

### M9 — personalizer_tamper (H2, truth by construction)

`FaultyPersonalizer` (evals/) wraps the template renderer and applies one
scripted mutation per case — 8 classes × 5 cases = 40: empty output;
> 400 chars / > 60 words; embedded newline; unfilled `{author}` slot left
in; `{text_short}` excerpt altered when the template embeds it; URL
injected; `act` scaffold ("When … I will …") removed; trailing "?"
stripped from a `reflect` prompt. Plus 20 committed *clean* outputs
(`clean_personalizations.json` — valid paraphrases that must pass).

```
M9_recall = (# mutations rejected by the FR-10 validator) / 40
M9_fpr    = (# clean outputs rejected) / 20
```

Every rejection must also produce the template fallback with
`personalize_fell_back = true` (asserted in the same test).

### Report-only (printed, not gated)

- `kind_coverage`: fraction of entries with ≥ 4 exposures in S1 that saw
  ≥ 3 distinct prompt kinds (expected ≈ 1.0 by rotation; diagnostic for
  candidate-pool exhaustion in thin themes).
- `novelty_share_curve`: σ per 28-day window, plotted as a sparkline row.
- `seed_sensitivity`: fraction of days on which seeds 7 vs 8 disagree.
- `forced_novelty_days`: how often the S = 90 aging rule actually fired.
- `M8_dev`: suggester accuracy on the dev split (tuning visibility).

## 4. Fixture strategy

Everything lives under `evals/fixtures/`, committed and deterministic.
Simulation libraries use *synthetic* entry texts (invented one-liners) —
content is irrelevant to H1 and this keeps fixtures license-clean and
small; themes are assigned directly in the fixture (bypassing the
suggester, which is evaluated separately on real quotes in M8).

```
evals/fixtures/
  library_steady.json          # S1: 40 initial entries + capture stream (3/wk, 365 days),
                               #     8 pinned, latent quality per entry (25% gem, 55% decent, 20% dud)
  library_burst.json           # S2: 150 entries imported day 0 + 1/wk trickle, 365 days
  library_tiny.json            # S3: 6 entries, 60 days — exercises the relaxed-cooldown fallback
  usage_calendars.json         # per-scenario materialized dates: S1/S2/S3 daily;
                               #     S4 = S1's library with a Mon/Wed/Sat + occasional-miss calendar
                               #     (deterministic pattern, 152 open days/365)
  personas.json                # per-scenario reflection policy params (see below)
  themes_labeled.json          # 80 real public-domain quotes (pre-1929 authors: Marcus Aurelius,
                               #     Seneca, Epictetus, Franklin, Emerson, Thoreau, La Rochefoucauld,
                               #     Goethe, Proverbs…), hand-labeled primary theme,
                               #     near-balanced (4–6 per theme), split dev/held 40/40 marked in-file
  clean_personalizations.json  # 20 valid personalizer outputs for M9 FPR
  generate.py                  # seeded generator (--seed 42) for the synthetic libraries,
                               #     streams, and calendars; committed; regeneration is a reviewed change
  expected.json                # generator-measured informational anchors (baseline stats);
                               #     never asserted against (§5)
```

- **Personas** (`evals/personas.py`) are deterministic: on each surfacing,
  reflect iff `u(hash(scenario, entry_id, date)) < p_reflect` (S1/S2
  diligent p = 0.8; S4 sporadic p = 0.4); grade drawn from the entry's
  planted latent quality via the same hash stream — gem →
  {applied .60, resonated .35, flat .05}, decent → {.25, .50, .25},
  dud → {.05, .15, .80}. The scheduler never sees latent quality; that is
  what makes M6 a real measurement.
- **Ground truth summary:** M1/M7 = predicates on the timeline; M2–M5 =
  contract targets vs measured timeline; M6 = planted latent quality;
  M8 = hand labels on a held-out split (labels assigned before lexicon
  tuning; dev/held split frozen in the fixture); M9 = construction. No
  metric's truth is produced by the code path it gates.
- **S3 (tiny)** additionally asserts, inside M1's run: the fallback picks
  the least-recently-surfaced entry, every such surfacing carries
  `relaxed_cooldown = true`, and the tool never returns an empty card
  while ≥ 1 active entry exists.

## 5. Baselines and gates

Two naive baselines are *computed live in the eval run* by swapping only
the selection policy (same eligibility, cooldown bookkeeping, prompt
selection, personas):

- `random_eligible` — uniform seeded choice among eligible entries. The
  honest null model of "just show me something I saved".
- `fifo_rotation` — cycle entries in capture order, skipping cooldown.
  Perfect fairness and debut latency, zero adaptivity — included so no
  single-trick strategy can look good across the gate *set*.

Expected levels (from generator design; `generate.py` freezes measured
values into `expected.json`, and the build phase reconciles drift before
gates are finalized in review):

| Metric | random_eligible | fifo_rotation | gate |
|---|---|---|---|
| M1 | 1.0 (respects eligibility by construction — M1 is a regression predicate, not a differentiator) | 1.0 | — |
| M2_p50 / M2_p95 | ≈ 60 / ≥ 200 days (S2's 150-entry backlog gets no debut preference) | ≈ 2 / ≈ 40 (its one trick) | see below |
| M3 | ≥ 0.25 (share drifts with pool sizes: ≈ 0.8 early, ≈ 0.05 late) | ≥ 0.30 | ≤ 0.12 |
| M4 | ≫ 1 (mean gap ≈ eligible-pool size ≈ 100+ days regardless of I ∈ [2, 21+]) | ≫ 1 (gap = library size for everyone) | ≤ 0.25 |
| M5 | ≈ 0.0 (expected gap ≈ 100+ days > 45) | 0.0 | = 1.0 |
| M6 | ≈ 1.0 (feedback ignored) | ≈ 1.0 | ≤ 0.50 |
| M8_top1 baselines | uniform-random 1/16 = 0.06; majority-theme ≈ 0.10–0.15 (near-balanced fixture) | — | ≥ 0.55 |

| Gate | Threshold | Rationale |
|---|---|---|
| M1 constraint_compliance | = 1.0 | Every component is a hard product promise (US-2, US-4) or an invariant; one violation across ~3 simulated years is a bug, not noise. |
| M2_p50 | ≤ 10 days | US-3's "captured this week appears within days"; freshness term τ = 7 makes this achievable; random baseline sits ≈ 60. |
| M2_p95 | ≤ 45 days | Even in S2's 150-entry burst, the trailing-share rule + aging must drain the backlog within a season; random ≥ 200. Includes the hard 180-day starvation check. |
| M3 mix_balance | ≤ 0.12 | The trailing-share rule's whole job (D3); ±0.12 tolerates forced-novelty bursts and pool droughts, while both baselines exceed 0.25. |
| M4 spacing_fidelity | ≤ 0.25 | Spacing is the product's core claim (D1): mean drift ≤ a quarter of the scheduled interval under contention and sporadic usage; baselines are off by multiples, not fractions. |
| M5 pinned_recurrence | = 1.0 | "Respects pinned favorites" is a locked owner decision; every pinned entry, not most. Baselines score ≈ 0. |
| M6 feedback_responsiveness | ≤ 0.50 (and archive-candidate sub-check = 1.0) | The reflection loop must visibly matter: three flats at multiplier 3.0 vs resonant multipliers 1.6–2.3 should at least halve the exposure rate; baselines sit at ≈ 1.0. |
| M7 determinism_replay | = 1.0 | CONVENTIONS.md hermeticity; also what makes every other number trustworthy. |
| M8_top1 (held) | ≥ 0.55 | ~6× the majority-class baseline on real quotes the lexicons were never tuned on; honest about lexicon limits on metaphorical text (D9 keeps a human in the loop). |
| M8_hit3 (held) | ≥ 0.80 | The CLI shows top-3 for confirmation; the right theme must be on screen 4 times in 5. |
| M9_recall | = 1.0 | The validator is the trust boundary for the LLM adapter (D7); any escaped mutation class is a hole, not a miss. |
| M9_fpr | = 0.0 | A validator that rejects valid personalizations silently degrades the feature to templates-only; zero tolerance on the committed clean set. |

Gates are asserted on live-computed values inside the eval run — never
against `expected.json`.

## 6. How the suite runs

Mirrors `orbit-backend/evals/` and the sibling projects:

- **`evals/simulate.py`** — the day-by-day driver: builds a memory-repo
  service with `FixedClock`, applies the scenario's capture stream, usage
  calendar, and persona, and returns the timeline plus per-day pool
  telemetry. Also hosts the baseline selection policies.
- **`evals/metrics.py`** — `MetricResult(name, value, gate, passed,
  detail)`, `EvalReport`, and one function per metric M1–M9 operating on
  timelines; percentile/window helpers.
- **`evals/run.py`** — `python -m` runnable with zero configuration and no
  network. Runs S1–S4 (seed 7, plus seed 8 for M7b), computes M1–M9 and
  the report-only diagnostics, prints a scorecard — one line per metric
  with value, gate, PASS/FAIL, and detail (per-scenario breakdowns,
  baseline comparisons) — then a JSON summary. Exit code 0 iff all gates
  pass.
- **`evals/test_gates.py`** — pytest gates: one test per gate in §5, test
  names carrying FR ids (e.g. `test_fr7_mix_balance_gate`,
  `test_fr10_personalizer_tamper_gate`), each calling the metric functions
  directly; plus `test_eval_runner_passes` executing `run.py` end-to-end
  asserting exit code 0; plus data-floor tests for the committed datasets
  (template floors per FR-9, misattribution reference URLs per FR-2,
  theme lexicon floors per FR-3).

Runtime budget: 4 scenarios × ≤ 365 simulated days × O(library) selection
≈ well under 20 s on a laptop; runs in every CI invocation via
`uv run pytest almanac/`.

## 7. FR → test/eval mapping

| FR | Covered by |
|---|---|
| FR-1 | `tests/test_capture.py` (normalization, hash dedupe warning, validation) |
| FR-2 | `tests/test_attribution.py` (match rule, non-blocking) + dataset floor test |
| FR-3 | **M8** + `tests/test_themes.py` (suggester determinism, tie-breaks, cap 3) |
| FR-4 | `tests/test_curation.py` (pin/archive semantics, text-immutability rule) |
| FR-5 | `tests/test_import_export.py` (JSON/CSV/starter, row-error report, round-trip) |
| FR-6 | `tests/test_fold.py` (grade table per branch, clamps, pinned cap) + **M7c** |
| FR-7 | **M1a–c, M2, M3, M4, M5, M6** + `tests/test_scheduler.py` (slot rule branches, forced novelty, fallback order) |
| FR-8 | **M1c, M1f** + `tests/test_materialize.py` (idempotence, monotonicity, extra draws excluded from σ) |
| FR-9 | **M1d, M1e** + `tests/test_prompts.py` (rotation, overrides, exhaustion retry) + kind_coverage report |
| FR-10 | **M9** + `tests/test_validator.py` (each check a–e) |
| FR-11 | `tests/test_reflection.py` (uniqueness, 409 window, append-only) |
| FR-12 | `tests/test_search.py` (FTS ranking determinism, LIKE fallback parity) |
| FR-13 | `tests/test_collections.py` (ordering, draw scoping) |
| FR-14 | `tests/test_stats.py` (streaks, coverage, archive candidates) + M6 sub-check |
| FR-15 | `tests/test_api.py` (FastAPI TestClient, error mapping, 409s) |
| FR-16 | `tests/test_cli.py` (Typer CliRunner, exit codes, `--json`) |
| FR-17 | **M7** |
