# Almanac — Evals

## 1. What this product lives or dies on

Two capabilities, per SCOPE.md:

- **H1 — Scheduling quality under a fixed daily budget.** The daily
  scheduler must *simultaneously* hold every promise it makes over months
  of simulated usage: hard no-repeat cooldown, novelty/review balance near
  the target, per-entry spacing that stays proportional to the
  reflection-driven intervals, fast debuts for the ongoing capture stream,
  a bounded drain for bulk imports, no starvation, pinned favorites
  recurring inside their guarantee, duds fading after flat reflections,
  graceful behavior on tiny libraries and sporadic users — all
  byte-reproducible from a seed. Each property is trivial alone; the
  product is the conjunction under one card a day, so the evals simulate
  full years and gate every property on the same timelines.
- **H2 — Application-prompt pairing.** Theme suggestions must be right
  (else prompts are misfiled forever), surfaced prompts must match the
  entry's themes, rotate kinds without repeating templates, and the
  optional personalizer must be structurally unable to *malform* a prompt.

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
  match, forcing compliance, rescue compliance, draw accounting,
  idempotence, replay equality — checkable by definition against the
  produced timeline.
- Truths that are *planted* (M6): each fixture entry carries a hidden
  latent quality (`gem`/`decent`/`dud`) that drives the simulated
  reflection grades; the scheduler never sees it. Responsiveness is
  measured against the plant, not against the engine's own bookkeeping.
- Truths that are *hand-labeled and held out* (M8): theme labels on real
  public-domain quotes, with the gate applied only to the half the lexicon
  authors are barred — procedurally and by an automated hygiene test —
  from tuning against.
- Truths by *construction* (M9): scripted mutations that are invalid by
  the FR-10 contract.
- Contract targets under contention (M2–M5): latency, mix, spacing and
  recurrence measured against numbers **derived in §5 from the capacity
  identity in SCOPE.md**, not from the implementation's output. Two
  live-computed naive baselines (§5) show the gates are not vacuous.

**No post-hoc refitting.** The thresholds in §5 are frozen by this
document. If the build measures a failing value, the fix is the
implementation, or a scope amendment reviewed as a scope change with a new
derivation written into §5 — never a silent threshold edit. `expected.json`
is informational and is never asserted against.

## 3. Metrics

Vocabulary: a *run* is `simulate(scenario, seed)` — day-by-day driving of
the real service (materialize the daily set on each date in the scenario's
usage calendar, apply the persona's reflections and curation actions,
apply the capture stream and draw stream). `T(run)` is the resulting
surfacing/reflection timeline plus per-slot telemetry. Every surfacing
carries `select_pool` (DATA_MODEL.md), which is the basis of several
predicates below. Day numbers are 0-based offsets from the scenario start.

A daily slot is **contested** iff `select_pool ∈ {novelty, review}` —
i.e. both pools were non-empty and no higher-precedence branch fired, so
the quota rule made a free choice.

### M1 — constraint_compliance (H1 + H2, predicate gate)

Over all runs (S1–S4, seed 7), count violations of:

- (a) **cooldown:** entry surfaced with `D − last_surfaced_on < W` without
  `relaxed_cooldown`;
- (b) **same-day duplicate:** entry appearing twice on one date;
- (c) **ineligible:** archived entry surfaced; or a surfacing whose
  `on_date` is earlier than an existing surfacing's; or a `daily` set
  materialized for a date ≤ a previously materialized daily date;
- (d) **prompt reuse:** template id repeating within an entry's last 6
  surfacings while ≥ 1 alternative candidate existed and
  `prompt_recency_relaxed = false`;
- (e) **theme mismatch:** prompt's theme ∉ entry.themes, or theme =
  `general` while entry has ≥ 1 theme;
- (f) **idempotence:** re-invoking materialization for an
  already-materialized date yields anything but the stored,
  byte-identical card set;
- (g) **forcing compliance:** for any slot `t` on any materialized date D,
  if at `t`'s decision point an active never-seen, not-yet-picked-today
  entry had `D − captured_on > S`, then that slot's `select_pool` must be
  `forced_novelty` or `pinned_rescue`. *(This is what makes starvation
  freedom a gate rather than a diagnostic: a scheduler that quietly drops
  old backlog fails here.)*
- (h) **rescue compliance:** for any slot `t`, if at `t`'s decision point
  an eligible pinned active entry had `D − last_surfaced_on ≥ P_rescue`,
  then that slot's `select_pool` must be `pinned_rescue`;
- (i) **draw accounting:** every `kind = extra` surfacing has
  `select_pool = extra` (so no draw can enter a σ window) and, when
  filtered, surfaces an entry carrying the requested theme / belonging to
  the requested collection and active; every `kind = daily` surfacing has
  `select_pool ≠ extra`; and the exposure the draw creates appears in the
  entry's fold (its `last_surfaced_on` advances).

```
M1 = total violations across all runs        gate: == 0
```

Predicates (d) and (e) are computed by `metrics.py` **directly from
`data/prompts.json`, `data/themes.json` and the timeline** — theme
membership, template kind, and the last-6 exclusion are re-derived in the
metric module. `metrics.py` must not import `engine/prompts.py`; otherwise
the predicate would inherit the bug it is supposed to catch. Same
principle as M7c's independent re-fold.

### M2 — debut behaviour (H1)

Split into two metrics because the product makes two different promises
(SCOPE.md US-3), and conflating them is what made the previous single
metric unsatisfiable.

**M2a — stream_debut_latency.** Over S1 + S2, entries whose
`captured_on > day 0` (the ongoing capture stream) and captured ≥ 60 days
before run end (censoring guard):

```
latency(e) = first_surfaced_on - captured_on          if e ever surfaced
           = run_end - captured_on                     otherwise  (never-debut is a
                                                        long latency, not an exclusion)
M2a_p50 = median latency      M2a_p95 = 95th percentile latency
```

**M2b — backlog_drain.** For each scenario's day-0 cohort (entries with
`captured_on == day 0`), with `B` = cohort size, `n_pinned` = pinned count,
`k` = batch size and `d` = materialized days ÷ calendar days:

```
L_bound(scenario) = S + ceil( B / ((k - n_pinned / P_rescue) * d) ) + 10
M2b = fraction of day-0 cohort entries with latency <= L_bound
```

Derivation: once a never-seen entry's age exceeds `S = 90` days the FR-7
forcing rule claims a slot for the N-pool on every materialized day, and
forced picks run oldest-first (`n(e) > 1` for `age > S`), so at most `B`
entries can be queued at that point and they drain at `k` slots per
materialized day minus the pinned-rescue load. The `+10` absorbs jitter
and capture-stream lumpiness. Concrete bounds (computed in §4):
**S1 ≤ 120, S2 ≤ 150, S3 ≤ 20, S4 ≤ 135 days.**

**M2 hard bound (gated with M2b).** Across every scenario and every
cohort, the number of entries captured ≥ 60 days before run end with
`latency > 180` — including never-surfaced ones — must be 0. All four
`L_bound` values sit below 180, so this is a genuine independent check on
the forcing machinery rather than a restatement of M2b.

### M3 — mix_balance (H1)

Over S1 and S2. Take the ordered sequence of **contested slots** in the
run (`select_pool ∈ {novelty, review}`, ordered by `(on_date, slot)`),
discard the first 28 (controller warm-up: before 28 contested slots exist
σ is computed on a short prefix), and slide a window of 28 consecutive
contested slots:

```
sigma_w = count(select_pool == 'novelty' in w) / 28
M3      = max over windows w of | sigma_w - rho |          (rho = 0.35)
```

This is *exactly* the statistic the FR-7 controller regulates — same
population, same window length — so the metric measures the policy rather
than the fixture's pool supply. Forced, single-pool, rescue, fallback and
extra-draw slots never enter a window, which is why forced drains cannot
make the gate unachievable.

**Non-vacuity (gated):** each of S1 and S2 must yield **≥ 60 windows**
(i.e. ≥ 88 contested slots). An empty or short window set is a **FAIL**,
not a pass — otherwise a greedy-novelty policy that empties one pool every
day would be certified without a single measurement. Window counts are
printed in the scorecard.

### M4 — spacing_fidelity (H1)

Over S1 and S4 — S2 is excluded because its declared forced-drain phase
(SCOPE.md D5) suspends reviews by design for ~15 consecutive days, and
gating spacing over a window the design says is a drain would measure the
fixture, not the policy; S2's drain is gated instead by M2b. Sample:
daily surfacings of entries with `exposure_count ≥ 1` at selection (i.e.
reviews), on dates ≥ day 60 (steady state). For each such surfacing s of
entry e:

```
O(s) = (on_date(s) - last_surfaced_on(e, before s)) / I_eff(e, before s)
```

**M4a — early_violations** = number of review surfacings with `O(s) < 1.0`
whose `select_pool ∉ {not_due, relaxed}`. Gate `== 0`. Nothing may be
surfaced before its scheduled interval except via the two branches that
declare they do so.

**M4b — proportional_fidelity.** Sample = the same surfacings, excluding
`select_pool ∈ {pinned_rescue, not_due, relaxed}` (those are governed by
M5 / are declared off-schedule):

```
M4b = Q3(O) / Q1(O)
```

Rationale: under the capacity identity (SCOPE.md), when review demand
exceeds capacity the max-overdue-ratio rule equalizes `O` across the
review pool, so every entry's realized gap is the *same* multiple
`lambda` of its own `I_eff`. Relative spacing — the thing the reflection
loop actually controls — is therefore preserved even at `lambda ≈ 3`, and
its fidelity is the *dispersion* of `O`, not the absolute lateness. A
policy that ignores intervals produces `gap ≈ const`, hence `O ∝ 1/I_eff`,
hence dispersion equal to the spread of the interval ladder itself
(≥ 4× — see §5).

Report-only alongside: `lambda` = median `O` (the stretch factor the user
sees in `almanac stats`), the Spearman correlation between each entry's
mean `I_eff` and mean realized gap, and the `not_due` slot share.

### M5 — pinned_recurrence (H1)

Over S1, S2 and S4. Because personas pin and unpin mid-run (§4), the unit
is a **pinned interval**: a maximal `[a, b]` during which an entry was
both pinned and active, intersected with `[day 60, run end]`. Intervals
shorter than 60 days are skipped. For each qualifying interval, let `t₀` =
the entry's last exposure on or before `a` (or `a` if none); the measured
gaps are the consecutive-exposure gaps over `[t₀, b]`. The trailing
partial gap (from the last exposure to `b`) is exempt.

```
M5 = fraction of qualifying pinned intervals whose every measured gap <= 45
```

45 days is the US-5 promise. It is *delivered* by the FR-7 pinned-rescue
branch, not hoped for: worst case is
`P_rescue + Δ · ceil(n_pinned / k)` where Δ is the largest calendar gap
between materialized days — 40 days for S1 (Δ=1, n=8), 36 for S2, 44 for
S4 (Δ=4, n=3). See §4.

### M6 — feedback_responsiveness (H1, planted truth)

Age-matched cohorts, pooled over S1 + S2. **Cohort** = entries captured in
`[day 0, day 90]` that had ≥ 2 exposures by day 165, split by planted
latent quality into `gem` and `dud`. **Horizon** = `[day 165, run end]`.

```
rate(cohort) = mean over cohort entries of (# exposures in horizon)
M6           = rate(dud) / rate(gem)
```

**FAIL conditions (not "pass by emptiness"):** either cohort smaller than
10 entries, or `rate(gem) == 0`. Fixtures guarantee the cohort sizes (§4).

Sub-check (gated with M6): every **active** entry with `flat_streak ≥ 3`
at run end appears in the final `stats` archive-candidate list, and no
entry with `flat_streak < 3` does (fraction correct = 1.0).

Why age-matched: without it the ratio is confounded by cohort composition
(late-captured gems have small intervals and high tail rates, inflating
the denominator for reasons unrelated to feedback).

### M7 — determinism_replay (H1, FR-17)

- (a) run S1 twice with seed 7: full timelines (entry ids, template ids,
  prompt texts, `select_pool`, flags) byte-identical;
- (b) run S1 with seed 8: differs from seed 7 in ≥ 1 selection (proves
  jitter is live);
- (c) at 12 monthly checkpoints in S1, recompute every entry's scheduler
  state by an **independent re-implementation of the FR-6 fold inside
  `metrics.py`** from the event log, and compare to the incrementally
  maintained cache: all equal;
- (d) serialize/reload the store mid-run (SQLite round-trip for this check
  only, tmp path): continuation identical to uninterrupted run.

```
M7 = 1.0 if a–d all hold else 0.0
```

### M8 — theme_suggestion_accuracy (H2, held-out labels)

Over `themes_labeled.json` **held split only** (96 quotes, 6 per theme;
the 96-quote dev split is for lexicon tuning and is reported but ungated):

```
M8_top1 = (# quotes where rank-1 suggestion == label) / 96
M8_hit3 = (# quotes where label in top-3 suggestions) / 96
M8_worst_theme_hit3 = min over the 16 themes of (hit3 within that theme's 6 held quotes)
```

`M8_worst_theme_hit3` exists so a theme that has collapsed entirely
(lexicon never fires, every quote routed elsewhere) fails the gate even
when the aggregate looks healthy. A full 16×16 confusion matrix is printed
by `run.py`.

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

**Honest scope, reported not gated.** The 8 mutation classes mirror the
FR-10 checks one-to-one, so `M9_recall = 1.0` certifies that the declared
rules are *implemented* — it does not show they *suffice*. A separate
report-only set of 10 **off-spec** cases documents the real boundary:
a well-formed but semantically unrelated replacement; a `{text_short}`
excerpt swapped for a different entry's excerpt where check (c) does not
apply (template did not embed the slot); a negated imperative ("When X
happens, I will not…"); a prompt that answers itself. These are expected
to *pass* the validator and are printed as
`validator_boundary: N/10 accepted` — the number is the documentation.
SCOPE.md US-8 and FR-10 say "malform", never "corrupt", for this reason.

### Report-only (printed, not gated)

- `kind_coverage`: fraction of entries with ≥ 4 exposures in S1 that saw
  ≥ 3 distinct prompt kinds (diagnostic for candidate-pool exhaustion).
- `pool_histogram`: share of daily slots by `select_pool`, per scenario —
  the single most useful debugging view of the scheduler.
- `novelty_share_curve`: σ per contested-slot window, as a sparkline row.
- `stretch_lambda`: median `O` per scenario (see M4).
- `seed_sensitivity`: fraction of days on which seeds 7 vs 8 disagree.
- `validator_boundary`: the M9 off-spec acceptance count.
- `M8_dev` + the 16×16 confusion matrix (tuning visibility).

## 4. Fixture strategy

Everything lives under `evals/fixtures/`, committed and deterministic.
Simulation libraries use *synthetic* entry texts (invented one-liners) —
content is irrelevant to H1 and this keeps fixtures license-clean and
small; themes are assigned directly in the fixture (bypassing the
suggester, which is evaluated separately on real quotes in M8).

```
evals/fixtures/
  scenarios.json               # S1-S4 in one file: initial library, capture stream,
                               #   pinned set + pin/unpin events, latent qualities,
                               #   usage calendar, persona params, curation actions,
                               #   draw stream, collections
  themes_labeled.json          # 192 real public-domain quotes (pre-1929 authors),
                               #   hand-labeled primary theme, exactly 12 per theme,
                               #   split dev/held 96/96 (6 per theme per split), marked in-file
  clean_personalizations.json  # 20 valid personalizer outputs for M9 FPR
  faulty_cases.json            # 40 mutation specs + 10 off-spec specs for M9
  generate.py                  # seeded generator (--seed 42) for the synthetic libraries,
                               #   streams and calendars; committed; regeneration is a reviewed change
  expected.json                # generator-measured informational anchors; never asserted against
```

### Scenarios, sized against the capacity identity

Every scenario below satisfies `capture rate ≤ rho · k` per materialized
day, so the never-seen pool stays bounded and forcing is a bounded
correction rather than the steady state. All run at `k = 1`.

| | S1 `steady` | S2 `burst` | S3 `tiny` | S4 `sporadic` |
|---|---|---|---|---|
| purpose | the daily user; primary for M2a/M3/M4/M5/M6 | bulk import drain; M2b, M3, M6 | relaxed-cooldown fallback | sporadic calendar; M4, M5 |
| day-0 cohort | 12 (staggered `captured_on` day −12…−1) | **40** (all `captured_on` = day 0) | 6 | 12 |
| capture stream | **1.5/wk** (3 per 14 days, fixed offsets) = 0.214/day | 1/wk = 0.143/day | none | **0.6/wk** = 0.086/day |
| materialized days | 365 of 365 (Δ=1) | 365 of 365 (Δ=1) | 60 of 60 | **152 of 365** (Mon/Wed/Sat + deterministic 12% miss, Δ≤4) |
| pinned | 8 | 4 | 1 | 3 |
| latent quality mix | 25% gem / 55% decent / 20% dud | same | same | same |
| collections / draws | 2 collections × 8 entries; ~1 draw per 17 days (alternating theme / collection filter) | — | — | — |
| novelty budget `rho·k·d` | 0.35/day ≥ 0.214 ✓ | 0.35/day ≥ 0.143 ✓ | n/a | 0.146/cal-day ≥ 0.086 ✓ |
| `L_bound` (M2b) | 120 | 150 | 20 | 135 |
| M5 worst-case gap | 32 + 1·8 = **40** | 32 + 1·4 = **36** | 32 + 1 = **33** | 32 + 4·3 = **44** |
| expected contested slots | ≈ 220 | ≈ 185 | — | — |

Derivations for the numbers that gates depend on:

- **Contested-slot supply (M3 non-vacuity).** In steady state every
  capture must debut, so the debut rate equals the capture rate `A`;
  contested slots occur when both pools are non-empty and the controller
  spends a share `rho` of them on novelty, hence contested rate ≈
  `A / rho`. S1: `0.214 / 0.35 = 0.61/day → ≈ 220/yr`. S2: the 40-entry
  backlog keeps the N-pool non-empty for the first ~100 days (≈ 88
  contested slots after the 0.125/day rescue load), then trickle supply
  gives `0.143/0.35 = 0.41/day → ≈ 105`. Both exceed the 88 slots (28
  warm-up + 60 windows) the gate requires, with margin ≥ 2×.
- **M2b bounds.** `S + ceil(B / ((k − n_pinned/P_rescue) · d)) + 10`:
  S1 `90 + ceil(12/0.75) + 10 = 116 → 120`;
  S2 `90 + ceil(40/0.875) + 10 = 146 → 150`;
  S4 `90 + ceil(12/(0.906·0.416)) + 10 = 132 → 135`;
  S3 drains inside its 6-entry library in under a week → 20.
  All below the 180 hard bound.
- **M6 cohort sizes.** Entries captured in `[0, 90]`: S1 `12 + 19 = 31`,
  S2 `40 + 13 = 53`; pooled 84. At 25% / 20% that is ≈ 21 gems and ≈ 17
  duds, both above the minimum of 10. `generate.py` additionally
  *stratifies* the latent-quality assignment over the day-0…90 captures so
  the cohort sizes are exact and not left to hash luck; the realized sizes
  are printed.
- **M6 expected level.** With the FR-6 defaults and the S1 persona, the
  per-exposure geometric interval multiplier is 1.54 for gems and 2.40 for
  duds; more decisively, a dud accumulates two consecutive `flat`/`none`
  grades within ~3 exposures and is demoted to `hi_flat = 240`, whose
  realized gap at `lambda ≈ 2.5` exceeds the remaining horizon. A gem
  therefore takes ≈ 2 exposures in `[165, 365]` and a dud ≈ 0–0.4,
  giving `M6 ≈ 0.0–0.2`. **Sensitivity check on the gate:** delete the
  demotion rule and leave the multipliers intact and the same arithmetic
  gives `M6 = 0.5`; make all four multipliers equal and it gives `≈ 1.0`.
  A gate of 0.35 therefore separates "the feedback loop works" from both
  "half of it was removed" and "none of it is wired up".
- **M3 expected level.** The controller is bang-bang on exactly the
  statistic M3 measures, with `rho · H = 9.8`: it picks novelty while the
  window count is ≤ 9 and review at ≥ 10, so `sigma_w ∈ {0.321, 0.357}`
  and `M3 ≈ 0.029` in the regulated regime. The 0.12 gate is ~4× that,
  leaving room for window-boundary effects.
- **M2a expected level.** A fresh capture outranks every backlog entry
  younger than `S` (`n(e)` is U-shaped), so its wait is the time to the
  next novelty slot: geometric with `p ≈ rho · (1 − rescue share) ≈ 0.26`
  in S1 → p50 ≈ 2, p95 ≈ 10, plus interference when two captures land
  close together. Gates of 7 / 21 hold ~2× margin.

### Personas and scripted user behaviour

`evals/personas.py` is deterministic — every decision is a hash of
(scenario, entity id, date) mapped to [0,1):

- **Reflection.** On each surfacing, reflect iff `u < p_reflect` (S1/S2
  diligent 0.8; S3 0.8; S4 sporadic 0.4); grade drawn from the entry's
  planted latent quality via the same hash stream — gem →
  {applied .60, resonated .35, flat .05}, decent → {.25, .50, .25},
  dud → {.05, .15, .80}.
- **Curation (makes M1c and M5 live).** When an active entry first appears
  in the `stats` archive-candidate list on date D, the persona archives it
  on the 7th following materialized day iff `u(hash("archive", …)) < 0.5`.
  S1 additionally pins 3 named entries on day 120 and unpins 2 of the
  original 8 on day 200; S4 pins 1 on day 100. These events are declared
  literally in `scenarios.json`, not sampled. Without them M1c's
  archived-entry predicate checks an empty set and M5 never sees a
  changing pinned set.
- **Extra draws (makes FR-8's draw half live).** S1 issues a draw on each
  materialized date where `u(hash("draw", scenario, date)) < 0.06`
  (≈ 22/yr), alternating a theme filter and a collection filter by the
  parity of the date index. Draws feed M1a/M1b/M1e/M1i, must never appear
  in a σ window (M1i), and their exposures shift subsequent due dates via
  the fold — visible in M4 because the following review's `O` is computed
  from the draw's date.

The scheduler never sees latent quality, and personas never read scheduler
state other than the public `stats` output. That is what makes M6 a real
measurement.

### Held-split hygiene for M8 (enforcement, not an honor system)

The dev/held split is frozen in `themes_labeled.json`. Two mechanisms keep
the held half honest:

1. **Protocol.** Labels were assigned before any lexicon authoring. A
   lexicon change may not cite a held-split quote in its commit message or
   review; REVIEW.md records this rule. `run.py` prints dev-split accuracy
   so tuning has a legitimate feedback channel.
2. **`tests/test_lexicon_hygiene.py` (automated).** Fails if any lexicon
   term matches (after stemming) at least one held-split quote and **zero**
   dev-split quotes, zero starter-pack entries, and zero prompt templates.
   A term with no support anywhere except the held set is the signature of
   held-set tuning. The test prints the offending (term, theme, quote id)
   triples so a legitimate rare term can be justified by adding dev-split
   support rather than by weakening the test.

192 labeled quotes at 12 per theme (6 dev / 6 held) makes a single quote
worth ~1 point of `M8_top1` instead of 2.5, and gives every theme a
per-theme score that `M8_worst_theme_hit3` can gate.

### Ground-truth summary

M1/M7 = predicates on the timeline (M1d/M1e and M7c re-derived
independently of the engine); M2–M5 = contract targets derived in §5 from
the capacity identity; M6 = planted latent quality; M8 = hand labels on a
held-out split; M9 = construction. **No metric's truth is produced by the
code path it gates.**

**S3 (tiny)** additionally asserts, inside M1's run: the fallback picks the
least-recently-surfaced entry, every such surfacing carries
`relaxed_cooldown = true` and `select_pool = relaxed`, and the tool never
returns an empty card while ≥ 1 active entry exists.

## 5. Baselines and gates

Two naive baselines are *computed live in the eval run* by swapping only
the selection policy (same eligibility, cooldown bookkeeping, prompt
selection, personas, telemetry):

- `random_eligible` — uniform seeded choice among eligible entries. The
  honest null model of "just show me something I saved".
- `fifo_rotation` — cycle entries in capture order, skipping cooldown.
  Perfect backlog drain, zero adaptivity — included so no single-trick
  strategy looks good across the gate *set*.

Expected baseline levels, **computed from the same arithmetic as the
gates** (S1 late-run eligible pool ≈ 80 entries; interval ladder spans
10…240 with quartiles ≈ 15 and 60):

| Metric | random_eligible | fifo_rotation | gate |
|---|---|---|---|
| M1 violations | 0 (respects eligibility by construction — M1 is a regression predicate, not a differentiator) | 0 | 0 |
| M2a_p50 / p95 | ≈ 55 / ≈ 240 (no debut preference; expected wait ≈ pool size) | ≈ 90 / ≈ 115 (a stream capture waits behind the whole library) | ≤ 7 / ≤ 21 |
| M2b | ≈ 0.1 (most of the 40-entry cohort never surfaces inside 150 d; many never at all → also fails the 180 bound) | ≈ 1.0 — **its one trick**: 40 entries at 1/day drain by day ~45 | = 1.0 |
| M3 | ≥ 0.30 (contested-slot novelty share tracks pool sizes: ≈ 0.8 early, ≈ 0.05 late) | ≥ 0.30 (after the initial drain almost nothing is novel) | ≤ 0.12 |
| M4a | ≫ 0 (ignores due-ness entirely) | ≫ 0 | = 0 |
| M4b | ≈ 4.0 (`gap ≈ const` ⇒ `O ∝ 1/I_eff` ⇒ Q3/Q1 ≈ I₇₅/I₂₅ ≈ 60/15) | ≈ 4.0 (same identity, `gap` = library size) | ≤ 3.0 |
| M5 | ≈ 0.0 (expected pinned gap ≈ 80 days > 45) | ≈ 0.0 (gap = library size ≈ 90) | = 1.0 |
| M6 | ≈ 1.0 (feedback ignored) | ≈ 1.0 | ≤ 0.60 (amended, REVIEW.md B1) |
| M8_top1 | uniform-random 1/16 = 0.06; majority-theme = 0.06 (exactly balanced fixture) | — | ≥ 0.55 |

| Gate | Threshold | Rationale |
|---|---|---|
| M1 constraint_compliance | violations `== 0` | Every component is a hard product promise (US-2, US-4, US-3's no-starvation, US-5's pinned guarantee, US-7's draw isolation) or an invariant; one violation across ~3 simulated years is a bug, not noise. (g)/(h)/(i) are new gates precisely because they are the properties a degenerate scheduler would otherwise slip past. |
| M2a_p50 / M2a_p95 | ≤ 7 / ≤ 21 days | US-3's "captured this week appears within days". Derived: fresh captures outrank backlog, so the wait is geometric with `p ≈ 0.26`/day → p50 ≈ 2, p95 ≈ 10; the gates hold ~2× margin for capture clustering. Both baselines are an order of magnitude worse. |
| M2b backlog_drain + hard bound | `== 1.0`, and 0 entries with latency > 180 | US-3's bulk-import promise, sized by the forcing rule rather than wished for. `L_bound` is computed per scenario from `S`, `B`, `k` and the rescue load (§4) — it is a property of the declared parameters, not of the implementation. `random_eligible` fails both parts; `fifo_rotation` passes this one and fails M2a/M4b/M5, which is the point of gating a *set*. |
| M3 mix_balance | ≤ 0.12, **and ≥ 60 qualifying windows in each of S1 and S2** | The quota rule's whole job (D3). Measured on exactly the statistic the controller regulates, so the achievable value is ≈ 0.029 (bang-bang bound `1/28`); 0.12 leaves 4× headroom. The window-count clause is what stops a pool-draining policy from passing on an empty measurement set — an empty or short window set is a FAIL. |
| M4a early_violations | `== 0` | "Never before its interval" is a hard rule of FR-7; the two branches that legitimately surface early (`not_due`, `relaxed`) declare themselves in `select_pool` and are excluded. |
| M4b proportional_fidelity | ≤ 3.0 | Spacing is the product's core claim (D1), but at `lambda ≈ 2–3` the honest claim is *proportional*, not absolute: the max-overdue-ratio rule equalizes `O`, so a correct implementation lands at Q3/Q1 ≈ 1.4–2.0 (spread comes from integer days, jitter, clamp edges and cohort age). Any interval-blind policy lands at Q3/Q1 ≈ I₇₅/I₂₅ ≈ 4.0. The gate sits between the two, closer to the baseline than to the target. |
| M5 pinned_recurrence | `== 1.0` | "Respects pinned favorites" is a locked owner decision; every pinned interval, not most. Delivered by the rescue branch with a proven worst case of 40/36/44 days against a 45-day gate, so a failure means the rescue branch is broken — not that the run was unlucky. Baselines score ≈ 0. |
| M6 feedback_responsiveness | ≤ 0.60 (cohorts ≥ 10 each and `rate(gem) > 0`, else FAIL) + archive-candidate sub-check `== 1.0` | The reflection loop must visibly matter. **Amended at build time (REVIEW.md B1); the original 0.35 and its derivation are below.** §4's derivation assumed `lambda ≈ 2.5`, but the fixture sizes declared in the same section produce `lambda ≈ 3.2–3.4`, which pushes each dud's demotion-triggering third exposure from ~day 120 to ~day 170 — just inside the `[165, run end]` horizon instead of just outside it. The replacement derivation is empirical, run at the realized `lambda`, and repeats §4's sensitivity check by removing the mechanisms: correct implementation 0.382–0.516 across seeds 7/8/9, `flat_streak ≥ 2` demotion removed 0.668–0.718, whole grade table flattened 0.797–0.883. The gate sits between the correct implementation's *worst* seed and the half-broken variant's *best* seed, so it still fails both degradations at every seed — the property that made the original a gate rather than a wish. Superseded: "≤ 0.35 … correct implementation ≈ 0.0–0.2; removing only the demotion gives 0.5; removing the whole grade table gives ≈ 1.0." |
| M7 determinism_replay | `== 1.0` | CONVENTIONS.md hermeticity; also what makes every other number trustworthy. |
| M8_top1 (held) | ≥ 0.55 | ~9× the 0.06 majority/uniform baseline on 96 real quotes the lexicons were never tuned on; honest about lexicon limits on metaphorical text (D9 keeps a human in the loop). |
| M8_hit3 (held) | ≥ 0.80 | The CLI shows top-3 for confirmation; the right theme must be on screen 4 times in 5. |
| M8_worst_theme_hit3 | ≥ 0.50 | A collapsed theme (lexicon never fires) is invisible in the aggregate but poisons every prompt for that theme forever; each theme must find its own label in the top 3 for at least 3 of its 6 held quotes. |
| M9_recall | `== 1.0` | The validator is the trust boundary for the LLM adapter (D7); any escaped malformation class is a hole, not a miss. |
| M9_fpr | `== 0.0` | A validator that rejects valid personalizations silently degrades the feature to templates-only; zero tolerance on the committed clean set. |

**Seed robustness.** M2a, M2b, M3, M4b, M5 and M6 are evaluated at seeds
7, 8 and 9, and the **worst** value across the three is the gated one.
Single-seed thresholds can sit one near-tie away from failing; taking the
worst of three costs three simulation passes (< 45 s total) and removes
the question. M1 and M7 run at seed 7 (M7b additionally at seed 8); M8 and
M9 are seed-independent.

Gates are asserted on live-computed values inside the eval run — never
against `expected.json`, and never re-derived from a build-phase
measurement (§2).

## 6. How the suite runs

Mirrors `orbit-backend/evals/` and the sibling projects:

- **`evals/simulate.py`** — the day-by-day driver: builds a memory-repo
  service with `FixedClock`, applies the scenario's capture stream, usage
  calendar, persona, curation actions and draw stream, and returns the
  timeline plus per-slot telemetry (pool sizes at each decision point,
  which is what M1g/M1h check `select_pool` against). Also hosts the two
  baseline selection policies.
- **`evals/metrics.py`** — `MetricResult(name, value, gate, passed,
  detail)`, `EvalReport`, and one function per metric M1–M9 operating on
  timelines; percentile/window/quartile helpers. **Independence rule:**
  the M1d/M1e candidacy predicates and the M7c fold are re-implemented
  here from the committed data files and the timeline; `metrics.py` must
  not import from `almanac.engine` (enforced by an import-check assertion
  in `test_gates.py`).
- **`evals/run.py`** — `python -m` runnable with zero configuration and no
  network. Runs S1–S4 across seeds 7/8/9, computes M1–M9 and the
  report-only diagnostics, prints a scorecard — one line per metric with
  value, gate, PASS/FAIL and detail (per-scenario breakdowns, worst seed,
  baseline comparisons, window counts, cohort sizes) — then the pool
  histogram, the M8 confusion matrix and a JSON summary. Exit code 0 iff
  all gates pass.
- **`evals/test_gates.py`** — pytest gates: one test per gate in §5, test
  names carrying FR ids (e.g. `test_fr7_mix_balance_gate`,
  `test_fr7_pinned_rescue_gate`, `test_fr10_personalizer_tamper_gate`),
  each calling the metric functions directly; plus
  `test_eval_runner_passes` executing `run.py` end-to-end asserting exit
  code 0; plus data-floor tests for the committed datasets (template
  floors per FR-9, misattribution reference URLs per FR-2, theme lexicon
  floors per FR-3, labeled-fixture balance 12/theme and 6/6 split, and
  the `I0 == W`, `lo >= W` parameter constraints of
  DATA_MODEL.md §SchedulerParams).

Runtime budget: 4 scenarios × ≤ 365 simulated days × 3 seeds × O(library)
selection ≈ under 45 s on a laptop; runs in every CI invocation via
`uv run pytest almanac/`.

## 7. FR → test/eval mapping

| FR | Covered by |
|---|---|
| FR-1 | `tests/test_capture.py` (normalization, hash dedupe warning, validation) |
| FR-2 | `tests/test_attribution.py` (match rule, non-blocking) + dataset floor test |
| FR-3 | **M8** + `tests/test_themes.py` (suggester determinism, tie-breaks, cap 3) + `tests/test_lexicon_hygiene.py` |
| FR-4 | `tests/test_curation.py` (pin warning past budget, archive semantics, hash-preserving text edit accepted, semantic edit rejected 422) + **M1c** (archived entries never surface, live via persona archiving) |
| FR-5 | `tests/test_import_export.py` (JSON/CSV/starter, row-error report, drain-horizon line, round-trip) |
| FR-6 | `tests/test_fold.py` (grade table per branch, four distinct first-review intervals, demotion at streak 2, archive flag at 3, clamps, pinned cap, `I_eff` floor) + **M7c** |
| FR-7 | **M1a, M1b, M1g, M1h, M2a, M2b, M3, M4a, M4b, M5, M6** + `tests/test_scheduler.py` (branch precedence, σ over contested slots, rescue, forced novelty, not-due path, relaxed fallback order) |
| FR-8 | **M1c, M1f, M1i** + `tests/test_materialize.py` (idempotence, all-kinds monotonicity, future-date rejection, draw pick order, draws excluded from σ) |
| FR-9 | **M1d, M1e** + `tests/test_prompts.py` (rotation, overrides, exhaustion retry sets `prompt_recency_relaxed`) + `kind_coverage` report |
| FR-10 | **M9** + `tests/test_validator.py` (each check a–e) + the report-only off-spec boundary set |
| FR-11 | `tests/test_reflection.py` (uniqueness, 409 window, append-only) |
| FR-12 | `tests/test_search.py` (FTS bm25 ranking determinism; LIKE fallback returns the identical result *set* and the documented token-count ordering — explicitly not bm25 parity) |
| FR-13 | `tests/test_collections.py` (ordering, draw scoping) + **M1i** |
| FR-14 | `tests/test_stats.py` (streaks, coverage, archive candidates, capacity block and the `lambda > 3` advisory) + **M6** sub-check |
| FR-15 | `tests/test_api.py` (FastAPI TestClient, error mapping, 409s, 422s) |
| FR-16 | `tests/test_cli.py` (Typer CliRunner, exit codes, `--json`) |
| FR-17 | **M7** |
