# GrailTrader — Evals

## What this product lives or dies on

1. **Index fidelity from hostile listings (hard part A, FR-3/4):** the
   per-stratum indices must recover the true price level from sparse,
   heterogeneous, outlier-polluted sold listings — including on the sparse
   strata and inside the event windows, not just on quiet dense weeks. Every
   valuation and every advice is computed *on* the index; if the index is
   noise, the advisor is a random-number generator with a confident tone.
2. **Honest event-driven advice (hard part B, FR-6/8/10):** buy/sell/hold
   decisions derived from typed events and decay-shaped impact priors must
   beat naive baselines on directional accuracy, at a volume that makes the
   product useful, with confidence that is ordered *and* does not overstate
   itself, through a backtest that is provably leak-free.

Everything else (CRUD, condition alias mapping, CLI rendering, gazetteer
validation) is covered by ordinary tests, not eval gates.

## What these gates do and do not certify

Stated plainly because the docs previously implied more than the suite can
deliver (SCOPE US-5 and non-goal 12 now match this paragraph):

- **Scenario A** is drawn from the engine's own model family: the generator
  uses the same condition multipliers the engine reads, log-normal item noise
  the fence's assumptions suit, and impact truths drawn ±25 % around the same
  case-literature anchors the shipped priors encode. Passing A therefore
  certifies **pipeline correctness, coverage, decay/timing handling,
  calibration ordering, leak-freedom and framing under approximately-right
  priors**. It does *not* certify real-world predictive skill, and hermetically
  cannot.
- **Scenario B** deliberately breaks the match: the world's condition
  multipliers differ from the committed ones by ±12 %, item noise is
  heavier-tailed (Student-t, ν = 4), and three events move *opposite* to their
  prior. Passing B certifies **graceful degradation** — skill drops but
  survives, and confidence does not overstate the hit rate it achieves.
- **Real skill is only measurable on the user's own data**, via the same
  `backtest` command with `reference: "recovered"`. The scorecard prints this
  caveat verbatim.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with offline adapters (`FixtureListingsFeed`, `FixtureNewsFeed`,
`FixtureSocialFeed`, in-memory store). No network, no clock (all timestamps
come from fixtures), the only randomness is the committed placebo seeds.
M-numbers map to gates in `evals/test_gates.py`; test names reference FR ids.

M0 and M1 exercise ingest + `index build`. M2–M5 run the real pipeline
end-to-end: ingest listings + events → build indices → weekly backtest replay
(FR-10) over the eval portfolio, scoring realized returns against the
**planted truth index** (`reference: "truth"`), which the advisor never sees.
M6 repeats the essential subset on scenario B.

**Denominator rule (applies to every metric):** a metric whose population
falls below its documented floor is a **gate failure**, never a pass and
never a division by zero. `evals/run.py` prints `n` beside every rate, and
`test_gates.py` asserts the floor before asserting the rate.

**Populations.** `candidates` = advice with `is_candidate = true`
(`|r̂| ≥ theta`, whether or not it cleared `conf_min`) — this is what
calibration is scored over. `actionable` = candidates whose action is `buy`
or `sell`. Out-of-window decisions (`t + 1 + H > end_week`) are reported
separately and are in **no** metric's numerator or denominator.

### M0 — Coverage and activity floors (anti-abstention)

Without these, an implementation that indexes only ultra-dense windows and
issues a handful of near-certain calls passes every skill metric while being
useless. All floors are derived from the generator and recorded in
`coverage_truth.json`; the numbers in brackets are what a reference
implementation produces.

```
M0a index coverage = |cells the engine published| / |truth-eligible cells|
     truth-eligible = (leaf stratum, week) cells where the generator's own
     clean-sale count in the trailing window is >= min_sales
M0b N_actionable            (scenario A, real run)
M0c min over buckets of n_candidates(bucket)
M0d regimes: n(sell whose drivers are all bullish events)   [sell-into-decay]
             n(buy issued within 3 weeks of a driver's week) [phase-in]
M0e N_actionable in each placebo run
M0f baselines land inside their documented bands (table below)
```

M0d is what makes the one-liner's "sell into a spike" auditable: an advisor
whose sells all come from scandals and panned shows would satisfy M2b while
never demonstrating decay-side selling.

### M1 — Index fidelity (capability A)

**M1a — scale-aligned percentage error, with tails.** For each stratum s
(leaf **and** the era/brand parents), over the weeks where the engine wrote
an index point, align scale (base-week noise must not count as tracking
error), then compare to the planted latent truth `T`:

```
α_s      = exp( mean over eligible weeks of ln(T_s,t / R_s,t) )
err(s,t) = | α_s · R_s,t / T_s,t − 1 |
M1a      = median over all eligible (s, t) cells of err
M1a-p90  = 90th percentile over all eligible cells of err
M1a-dense  = max over dense leaf strata of  median_t err(s,t)
M1a-sparse = max over sparse leaf strata of median_t err(s,t)
```

Parent truth comes from the generator: it chain-links the *truth* leaf levels
with the *true* sale counts, entirely on the construction side, so the FR-4
parent implementation is graded rather than trusted. Without parent cells a
wrong or discontinuous parent index would pass everything, since FR-7/FR-8
fall back to parents exactly on the sparse strata.

The three tail statistics exist because a median over ~3,000 quiet dense
cells cannot see failure on the cells that matter — the 6 sparse strata and
the ~10 % of cells inside a planted impact window. Splitting the per-stratum
bound by density class is necessary: a 5-sale window is intrinsically ~3×
noisier than a 45-sale one, so one bound would either be vacuous for dense
strata or unachievable for sparse ones.

**M1b — outlier fence mechanism.** Planted outliers are listed by id in
`listings_truth.json`:

```
M1b-recall = (# planted fakes/mislabels excluded by the FR-3 fence in >= 1
              window they fall in) / (# planted fakes/mislabels in eligible windows)
M1b-false  = (# clean sold listings excluded in any window) / (# clean sold
              listings in eligible windows)
```

This is a **mechanism gate, not a discrimination gate**: planted fakes
(0.15–0.30×, |ln| = 1.20–1.90) and mislabels (3.0–4.5×, |ln| = 1.10–1.50)
sit outside the fixture's effective cutoff of 0.7885 in log (the
`fence_floor_log` term binds, since 3.5 × 0.20 = 0.70 < 0.7885), so
essentially any sane fence passes recall — what the gate actually proves is
that the fence runs and is not degenerate. `M1b-false` is the load-bearing
half: it kills the "exclude everything" fence, which would also starve
windows below `min_sales` and blow up M0a.

**Diagnostic D-fence (printed, ungated).** ~0.5 % of listings are planted in
an *ambiguous* band at 0.40–0.60× latent (|ln| = 0.51–0.92), straddling the
cutoff, plus near-boundary clean sales. The scorecard reports what fraction
of each the fence excluded. Gating this would demand discrimination no
price-only rule can deliver (SCOPE non-goal 3); printing it keeps the
product's real limit visible.

### M2 — Advisor directional skill (capability B)

Backtest replay over weeks 12–120 of scenario A, eval portfolio of 36
garments (one per leaf stratum). Entry at week t+1; realized return from the
truth index at the advice's own horizon (FR-10).

```
M2a = (# actionable with sign(realized) == sign(r̂)) / N_actionable
M2b = mean(realized | buy) − mean(realized | sell)              [raw]
M2b-by-H = the same spread computed within each H* ∈ {4, 12, 26}   [reported]
```

M2b is reported both raw and per-horizon because raw mixes unannualized
4-week and 26-week returns, and biasing horizon choice long would inflate
`mean(realized | buy)` without extra skill. The gate stays on raw (horizon
choice is separately constrained by M2a, which scores each advice at its own
horizon), with the per-horizon split printed so the mix is visible.

The fixture plants true impact paths with a 2-week diffusion lag (sellers
reprice slowly), so genuine buy windows (event known, index not yet moved)
and sell windows (transient priced, decay ahead) exist by construction. An
index error, a wrong impact scope, a broken decay curve, a mis-specified λ
dilution, or a leaky replay all land here as misses.

Exclusions: `insufficient_future_index` / `stale_stratum` / `no_index` are
asserted ≤ 5 % of actionable advice. `out_of_window` is *not* in that budget
(it is a scenario-edge property, not a failure), and the generator places no
impact-bearing event after week 90 so that even H* = 26 advice on the last
event grades inside the window.

### M3 — Confidence quality (capability B)

Buckets from `advisor_config.calibration.bucket_edges = [0.45, 0.62]`:
`lo < 0.45 ≤ mid < 0.62 ≤ hi`, computed over **candidates**, not over
actionable advice. This is the fix for the first round's blocker: with
`conf_min = 0.52`, no *actionable* advice can ever sit in a `lo` bucket
defined below it, so the earlier "buckets over actionable advice" definition
made `lo` empty by construction and the metric uncomputable. Scoring every
advice that produced a directional signal — including those held with
`hold:low_confidence` — makes all three buckets populated and is also the
more honest question: *when the model says 0.30, how often is it right?*

```
M3a = hit_rate(hi) − hit_rate(lo)                                 [separation]
M3b = min over buckets b of [ hit_rate(b) − (mean_conf(b) − 0.10) ]  [anti-overclaim]
M3c = hit_rate(hi)                                                [absolute floor]
```

- **M3a** proves confidence carries information. `lo` is populated by
  sparse-stratum and stale-index decisions (`q_index` 0.5/0.8) and by
  social-sourced weak drivers; `hi` requires `c_event ≳ 0.63`, reachable only
  by departures, moderate/severe scandals and predecessor-era appointments on
  a fresh index.
- **M3b** is the gate that ties the *number* to reality: no bucket's realized
  hit rate may fall more than 0.10 below the mean confidence it advertised.
  It is deliberately **one-sided**. The confidence formula is a product of
  three damping factors and is systematically *under*-confident below the
  acting range (a correct implementation shows `lo` at mean conf ≈ 0.32 and
  hit ≈ 0.60); a two-sided |mean_conf − hit| ≤ 0.15 bound, as first proposed,
  would fail a correct implementation for being too humble while adding
  nothing to the safety story. Over-claiming is the failure that matters, and
  M3b catches exactly it.
- **M3c** pins the top bucket in absolute terms, so "hi hits 0.60 while lo
  hits 0.40" cannot pass on separation alone.

### M4 — Placebo honesty (capability B)

The same backtest run with each of three committed seeds
(`20260731`, `20260801`, `20260802`): every event's week is displaced by a
seeded uniform ±[26, 52]-week offset, **redrawn while the displaced active
window overlaps the true active window of any event whose target strata
intersect the displaced event's target strata** — not merely the same event's
own window. Without that tightening a displaced event routinely lands on a
different event's real planted move on the same brand (37 of 44 events are
bullish; brand-scoped events touch every child), manufacturing genuine-looking
placebo hits that eat the tight M4 budget for reasons unrelated to leakage.

```
M4a = max over the three seeds of | hit_rate_placebo − 0.5 |   (actionable)
M4b = max over the three seeds of | spread_placebo |           (buy − sell)
```

Gating the worst of three seeds removes the "tune until the one committed
placebo realization passes" degree of freedom; the cost is two extra replay
pairs, inside the runtime budget.

Placebo is strong against leaks that re-couple decisions to real moves
regardless of the event date — peeking at future listings, anchoring the
baseline on post-event data. It is structurally **weak** against entry-timing
lookahead: under displacement, decisions are uncorrelated with true moves, so
entering at week t instead of t+1 produces little placebo skill. That leak is
therefore canaried directly (T3), and the gate table credits it there rather
than to M4.

### M5 — Advice framing compliance, both directions (safeguard, FR-9)

```
M5a = (# advice rendered during the M2 replay that an independent reference
       checker in evals/metrics.py finds compliant) / N_rendered
M5b = (# frame_cases.json cases whose actual verdict equals the expected
       verdict) / N_cases      # must-block blocked AND must-render rendered
```

`M5a` alone is blind to the other half of FR-9: advice the engine wrongly
*refuses* to render never enters its denominator, so an over-strict substring
matcher that blocks any advice whose garment label contains "guaranteed" —
the exact failure the quoted-context rule exists to prevent, and common in
reality, since real Grailed titles say "guaranteed authentic" — would score
1.0. `M5b` fixes that: `frame_cases.json` carries hand-authored expected
verdicts in **both** directions (≥ 6 must-block, ≥ 4 must-render), and a
wrongly-blocked must-render case is a failure. In addition, 3 of the 36
eval-portfolio garments carry labels with forbidden words in quoted context,
so the M2 replay exercises the quoting path thousands of times rather than
only in 10 hand cases.

The reference checker is re-implemented in `evals/metrics.py` so the gate does
not trust the engine's own `frame.py` to grade itself. This is the workspace
finance-safeguard rule as implemented behavior.

### M6 — Mismatch scenario (validity, capability A + B)

Scenario B (see fixture table) with generator-side condition multipliers
perturbed ±12 % away from `data/conditions.json`, Student-t(ν = 4) item
noise scaled to σ = 0.24, and 3 of 16 events whose true impact **contradicts**
their prior's direction (a panned show that rallies, an acclaimed appointment
that sinks the brand, a collab that dumps).

```
M6a = M1a computed on scenario B
M6b = M2a computed on scenario B
M6c = M3b computed on scenario B     (anti-overclaim under prior mismatch)
```

**Diagnostic D-contra (printed, ungated):** hit rate and mean confidence over
candidates driven solely by the three contradiction events. The advisor has no
mechanism to detect that a prior is wrong, so gating its accuracy there would
be gating clairvoyance; printing it shows the reader exactly how confidently
wrong the system is when its priors are wrong, which is the honest answer to
"does this transfer?".

### Non-scored enforcement tests

| Test | What it pins |
|---|---|
| `tests/test_determinism_fr14.py` (**T1**) | Ingest scenario A twice into two fresh in-memory stores; canonical JSON exports of listings, index points, events, advice and one backtest run are byte-identical with identical content-derived ids. Re-ingesting into the same store changes nothing; `index build` re-run on unchanged listings is a byte-identical replace. |
| `tests/test_hermeticity_fr14.py` (**T2**) | Monkeypatch `socket.socket` to raise, run `evals/run.py`'s entry point, then assert `sys.modules` contains no `grailtrader.adapters.news_rss` and no network module was touched. Makes the conventions' hermeticity rule mechanical, not declared. |
| `tests/test_backtest_fr10.py` (**T3**) | Leak canaries: `test_fr10_entry_is_next_week`; `test_fr10_advice_ignores_future_listings` (plant a listing sold in week t+1 that would flip the index; advice at t must be unchanged); `test_fr10_prefix_index_equivalence` (the index built from listings with `sold_at ≤ end of week t` equals the full build restricted to weeks ≤ t — the property that lets the replay build once and slice). |
| `tests/test_index_fr4.py` (**T4**) | Parent chain continuity: a child stratum becoming eligible mid-scenario produces no step in the parent's log-change series. |
| `tests/test_events_fr5.py` (**T5**) | Duplicate-feed ingest: the same real-world event from a second `source_ref` yields one row, `corroboration` 1 → 2, and `Σ_e m_e` on every target stratum unchanged. |
| `tests/test_advisor_fr8.py` (**T6**) | All seven `hold:` reason codes are reachable, each by a constructed minimal case. |

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable byte-identically
by the committed seeded generator (`generate_scenario.py --seed 20260731`; CI
re-runs it and diffs against the committed files). **Ground truth never comes
from the engine under evaluation**: listing truth, index truth (leaf *and*
chain-linked parent), coverage truth and the planted impact table all come
from the generator's construction parameters. The planted impact table is
written from the case literature *independently* of `data/impact_priors.json`
— correlated through the literature, never through code — so a wrong prior
cannot manufacture its own passing grade. All fixture brands are fictional
(SCOPE D-14): scandal and death events must not attach to real brands or
people.

### Scenario A — `evals/fixtures/scenario_a/`

| File | Contents | Ground truth |
|---|---|---|
| `brands_fixture.json` | 8 fictional brands / 18 eras modeled on documented archetypes (founder-era archive house, hype-collab streetwear label, scandal-hit megabrand, quiet heritage brand, …) across 8 categories → **36 leaf strata: 30 dense** (Poisson λ = 7–12 sales/week) **+ 6 sparse** (λ = 0.8–1.6, exercising staleness, `q_index` and the parent-fallback paths). Every sparse leaf sits under a brand/era that receives ≥ 2 events, so the `lo` confidence bucket is populated. 2 brands receive no events at all (hold correctness). | By construction |
| `listings.jsonl` | 120 weeks (Mondays 2023-01-02 … 2025-04-14), ≈ 35,000 sold + ≈ 3,000 active ask-only listings. Sold price = `latent_level × condition_multiplier × lognormal(σ = 0.20)`; condition sampled (new 10 %, excellent 45 %, good 30 %, fair 12 %, poor 3 %); ask-only listings at 1.25–1.45× latent (must never enter the index). Planted pollution: ≈ 2 % fakes at 0.15–0.30× latent, ≈ 1 % mislabels at **3.0–4.5×** (raised from the first draft's 2.5×, which overlapped the fence's own cutoff and made the stated recall margin arithmetically false), ≈ 0.5 % ambiguous at 0.40–0.60× (diagnostic only). | Per-listing construction params + planted-outlier ids and class labels in `listings_truth.json` |
| `index_truth.json` | Weekly latent truth level per leaf stratum (GBM, weekly σ = 2 %, zero drift, category-typical USD anchors, plus planted impact paths phased in linearly over a 2-week diffusion lag) **and** the generator-side chain-linked truth series for every era and brand parent. | By construction |
| `coverage_truth.json` | Per (leaf stratum, week): the generator's clean-sale count in the trailing 4-week window and the `truth_eligible` flag (count ≥ `min_sales`); per stratum, its density class (`dense` / `sparse`); and the reference-implementation counts for every M0 floor. | By construction |
| `events.jsonl` | 44 typed events: **10 departures** (4 resignation / 3 ousted / **2 death** / 1 house_closure), 6 appointments (3 acclaimed / 2 neutral / 1 unproven), 8 collabs (2 fizzle), 10 co-signs (3 a_list / 4 b_list / 3 niche; 3 fizzle), 4 runway (3 acclaimed / 1 panned), 6 scandals (2 minor / 2 moderate / 2 severe). Sources mixed manual/news/social; 4 events are reported twice from distinct domains (corroboration path, T5). Spaced so overlap, decay tails and quiet stretches all occur; **no impact-bearing event after week 90**. The 2 deaths sit on dense strata: their large transient (T = 0.25, h = 6) is what makes the sell-into-decay regime reachable at H* = 26 (M0d). | `impact_truth.json` |
| `impact_truth.json` | The planted impact table (per event: true P, T, half-life, target strata, fizzle flag, diffusion lag) and each event's realized path parameters. Magnitudes are drawn seeded with σ = 25 % around case-literature anchors — departure/death P ≈ +0.20, T ≈ +0.25 (Abloh/StockX); co-sign T ≈ +0.10 · tier, P = 0 (Lyst/Depop search-spike pattern); severe scandal P ≈ −0.15 (Balenciaga 2022 demand drop). | By construction |
| `eval_portfolio.json` | 36 garments — one per leaf stratum, condition `excellent`, acquired in week 8 at the then-truth level (so FR-7 `repeat_sales` tracks the index one-to-one), status `owned`. **3 carry labels containing forbidden-lexicon words** (e.g. `guaranteed authentic AW99 moto`) so the quoting path is exercised on every replay week. | By construction |

### Scenario B — `evals/fixtures/scenario_b/` (mismatch)

Same file set, generated by the same script with `--seed 20260801 --mismatch`:
5 brands / 10 eras / **20 dense leaf strata** (λ = 7–12), 80 weeks, ≈ 15,000
sold listings, 16 events, 20-garment portfolio. Differences that matter:
generator-side condition multipliers perturbed ±12 % from
`data/conditions.json`; item noise Student-t(ν = 4) scaled to σ = 0.24;
**3 events whose planted impact contradicts their prior's direction**, flagged
in `impact_truth.json` as `contradicts_prior: true`. No placebo run on B.

### Shared

`frame_cases.json` — ≥ 10 hand-authored advice-render scenarios with
**expected verdicts in both directions**: ≥ 6 must-block (footer tampering,
section omission, forbidden word emitted by a template rather than quoted) and
≥ 4 must-render (garment label `“guaranteed authentic”`, event note
`“a sure thing, said the seller”`, listing title with `easy money`, a label
whose forbidden word straddles the closing quote). `generate_scenario.py` —
seeded generator emitting both scenarios; committed; re-run and diffed in CI.

### Expected values for a correct implementation

Derivations are reproduced as comments in `generate_scenario.py`.

- **Index.** A 4-week window on a dense stratum holds n = 28–48 surviving
  sales; the sample median of a log-normal with σ = 0.20 has log-scale sd
  ≈ 1.2533 · 0.20/√n = 0.036–0.047, so median |error| = 0.6745 · sd ≈
  0.024–0.032 → **M1a ≈ 0.028**, and p90 = 1.6449 · sd ≈ 0.060–0.078 before
  the two tail contributors: event-window cells (~10 % of the population) where
  a 4-week trailing median lags a 2-week phase-in by up to ≈ 0.10, and sparse
  cells (n ≈ 5–7, sd ≈ 0.095–0.102) → **M1a-p90 ≈ 0.11**, **M1a-dense ≈
  0.032**, **M1a-sparse ≈ 0.069**.
- **Volatility, and why the config says `z_half = 0.5`.** Consecutive 4-week
  windows share 3 of 4 weeks of sales, so the sampling errors of adjacent
  medians are ≈ 0.75-correlated and the week-over-week change carries noise sd
  ≈ 0.707 · sd(median) ≈ 0.026–0.033. With the truth's own 2 %/week GBM that
  gives **σ_w ≈ 0.033–0.039** on dense strata and ≈ 0.08–0.12 on sparse ones.
  At `theta = 0.12` the marginal candidate therefore has z ≈ 1.5 on a dense
  stratum, and `z_half = 0.5` is what makes `z_term = 1 − 0.5^(z/z_half)` map
  that to ≈ 0.90 rather than ≈ 0.73 — i.e. the confidence curve is calibrated
  to the noise level the index actually has. `z_half` is config data; changing
  the fixture's density or item σ requires re-deriving it in the same commit.
- **Skill.** Only events whose total modeled move can exceed `theta = 0.12`
  produce candidates on their own — departures (0.22–0.45), moderate/severe
  scandals (0.20/0.30), and stacked overlaps. Co-signs (max ≈ 0.10) and minor
  scandals are context, contributing to `Σ m_e` and diluting `c_event` but
  never trading alone. Planted z-scores put per-type hit probabilities from
  ≈ 0.92 (departures, severe scandals) down to ≈ 0.60 (fizzle-diluted stacked
  collab/appointment calls), giving **M2a ≈ 0.82**, **M2b ≈ +0.18**,
  **M3a ≈ 0.28** (hi ≈ 0.90 vs lo ≈ 0.62), **M3c ≈ 0.90**, and reference
  volumes N_actionable ≈ 620, bucket counts ≈ 240 / 380 / 260 (lo / mid / hi),
  sell-into-decay ≈ 35, phase-in buys ≈ 350.
- **Scenario B.** Sampling sd rises to ≈ 0.056 (σ = 0.24 plus t₄ tail
  inflation ≈ 15 %) and the ±12 % multiplier mismatch adds a composition-driven
  residual of ≈ 0.02–0.03 after α-alignment → **M6a ≈ 0.055**. Three of
  sixteen events contradicting their prior make ≈ 18 % of event-driven
  decisions wrong by construction → **M6b ≈ 0.71**.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| **M0a** index coverage | — | an implementation that raises its effective `min_sales` to 15 scores ≈ 0.45 | **≥ 0.95** | Kills selective indexing: the gate is computed against generator-side eligibility, so skipping hard cells is visible instead of shrinking every other metric's denominator. 0.95 (not 1.0) leaves room for the fence's legitimate clean exclusions pushing a marginal window below `min_sales`. |
| **M0b** N_actionable | selective abstention: ~12 calls in 2.3 years | 12 | **≥ 400** | Reference implementation lands ≈ 620; M2a's "8 binomial SE above coin-flip" argument silently assumes an N of this order. Below 400 the product is not an advisor. |
| **M0c** min bucket n | abstention leaves `lo` empty | 0 | **≥ 25** | An empty or undersized bucket is a FAIL, never 0/0 → pass. 25 keeps a bucket hit rate's SE ≤ 0.10. |
| **M0d** regime counts | scandal-only seller | 0 sell-into-decay | **≥ 15 sell-into-decay, ≥ 100 phase-in buys** | Makes the one-liner's two named regimes auditable from rationale codes. 15 is conservative against the reference ≈ 35: sell-into-decay is reachable only where the transient is large (the 2 death events at H* = 26), which is a real structural property, not a fixture accident. |
| **M0e** N_actionable placebo | — | — | **≥ 400 per seed** | Makes M4's SE argument hold: at N = 400, SE(hit) = 0.025. |
| **M0f** baseline bands | — | — | **M1a-naive ∈ [0.08, 0.20]; θ-momentum hit ∈ [0.42, 0.58]; event-naive hit ∈ [0.50, 0.66]; always-hold \|spread\| ≤ 0.02** | Fixture drift that pushed event-naive to 0.68 would make the M2a gate near-vacuous. Asserting the baselines mechanically means any such drift fails CI instead of passing quietly. |
| **M1a** index error (median) | weekly arithmetic mean of raw sold prices: no condition adjustment, no fence, no trailing window | ≈ 0.12 (composition swings across a 1.25→0.35 multiplier range dominate; fakes drag the mean) | **≤ 0.05** | Correct implementation lands ≈ 0.028 by the sampling-error derivation; 0.05 leaves room for window-lag error during planted moves at well under half the baseline. Lower is better; the gate is an upper bound. |
| **M1a-p90** | same | ≈ 0.35 | **≤ 0.14** | Derived above (≈ 0.11 expected). Catches a fence that eats genuine repricing sales during a planted move — a failure the median cannot see and that M1b-false (capped over *all* clean listings) barely registers. |
| **M1a-dense / M1a-sparse** (max over strata of the per-stratum median) | same | ≈ 0.13 / ≈ 0.22 | **≤ 0.06 / ≤ 0.12** | Split by density class because a 5-sale window is intrinsically ≈ 3× noisier than a 45-sale one; one bound would be either vacuous for dense strata or unachievable for sparse ones. Guarantees no single stratum — in particular no sparse stratum, on the weeks it is indexed — is silently sacrificed to a good median. |
| **M1b-recall / M1b-false** | no fence | 0.00 / 0.00 | **≥ 0.90 / ≤ 0.05** | Mechanism gate: with the floor cutoff at 0.7885 log and planted outliers at \|ln\| ≥ 1.10, recall proves the fence runs; ≤ 0.05 false-exclusion (expected ≈ 0.0001) kills the degenerate "exclude everything" fence. Discrimination near the boundary is reported ungated (D-fence) because no price-only rule can deliver it (non-goal 3). |
| **M2a** directional hit | θ-momentum (buy iff trailing 4-wk return ≥ `theta_buy`, mirrored sell) | ≈ 0.50 (fires after spikes, exactly when decay begins) | **≥ 0.70** | Planted effects make ≈ 0.82 achievable end-to-end; every index, scope, λ-dilution, decay or timing error costs hits. At N ≈ 620, 0.70 is ≈ 10 binomial SE above coin-flip and decisively above both naive event responses. |
| (second baseline, same gate) | event-naive: buy for 12 weeks on any confirmed event touching the stratum | ≈ 0.58 (right on bullish events pre-peak; wrong on scandals, panned shows, fizzles and every decay phase) | — | Shows the advisor must model *direction, decay and pricing state*, not merely react to event existence. Both baselines are computed inside every backtest run (FR-10), printed in the scorecard and band-asserted by M0f. |
| **M2b** buy−sell spread | always-hold / either naive | 0.00 / ≈ +0.02 | **≥ +0.08** | Requires both legs: buys must capture phase-in upside and sells must capture decay. 0.08 is ≈ half the constructed spread (≈ 0.18), robust to draw noise. Per-horizon split printed (not gated) so a long-horizon bias is visible. |
| **M3a** calibration separation | confidence without `c_event`/`q_index` (z-term only) | ≈ 0.06 | **≥ 0.15** | Constructed separation ≈ 0.28 (hi ≈ 0.90, lo ≈ 0.62). 0.15 proves confidence carries information while tolerating bucket-edge noise; ≥ 25 candidates per bucket is enforced by M0c. |
| **M3b** anti-overclaim | a system that stamps 0.85 on everything | ≈ −0.25 | **≥ 0** (i.e. `hit_rate(b) ≥ mean_conf(b) − 0.10` for every bucket) | This is the gate that gives the printed confidence number meaning, which SCOPE D-11 promises and separation alone never delivers. One-sided by design: the formula is a product of three damping factors and is systematically under-confident below the acting range (reference `lo`: mean conf ≈ 0.32, hit ≈ 0.62), so a two-sided bound would fail a correct implementation. Over-claiming is the failure that matters on a product with a finance-safeguard rule. |
| **M3c** hi-bucket floor | — | — | **≥ 0.80** | `hi` is populated by decisions whose planted per-type hit probability is ≈ 0.92, so 0.80 is derivable and forecloses "hi hits 0.60, lo hits 0.40" passing on separation alone. |
| **M4a** placebo \|hit − 0.5\| | harness that anchors the baseline on post-event data, or peeks at future listings | ≥ 0.10 spurious on the committed seeds (these leaks re-couple decisions to real moves regardless of the displaced date) | **≤ 0.07** (worst of 3 seeds) | At N ≥ 400 actionable placebo decisions, SE ≈ 0.025, so 0.07 ≈ 2.8 SE per seed and ≈ 1.5 % false-failure risk across three. An entry-timing leak is *not* reliably visible here — it is canaried by T3 instead, and this row no longer claims otherwise. |
| **M4b** placebo \|spread\| | same | ≥ 0.06 spurious | **≤ 0.04** (worst of 3 seeds) | Realized-return spread under the null has SE ≈ 0.006, so 0.04 is ≈ 6 SE; the committed seeds realize ≈ 0.01. |
| **M5a** rendered compliance | templates without frame check | ≈ 0.90 | **= 1.0** | Workspace finance-safeguard rule: one certainty-claiming or footer-less advice is a product failure. Deterministic templates make exactness fair. |
| **M5b** verdict agreement | over-strict substring matcher (blocks quoted user text) | ≈ 0.60 (passes every must-block, fails every must-render) | **= 1.0** | The other half of FR-9. Without it, refusing to render is a free way to score M5a = 1.0 while breaking US-6 for any user whose listing title says "guaranteed authentic". |
| **M6a** index error, scenario B | naive mean, scenario B | ≈ 0.16 | **≤ 0.09** | Expected ≈ 0.055 (heavier tails + ±12 % multiplier mismatch). Looser than M1a by exactly the derived mismatch penalty; a pipeline that only works when it wrote the world's parameters fails here. |
| **M6b** directional hit, scenario B | θ-momentum, scenario B | ≈ 0.50 | **≥ 0.60** | Expected ≈ 0.71: three of sixteen events contradict their prior, so ≈ 18 % of event-driven decisions are wrong by construction. Requiring 0.60 proves skill degrades gracefully rather than collapsing or, worse, being an artifact of the matched generative family. |
| **M6c** anti-overclaim, scenario B | — | — | **≥ 0** (same form as M3b) | The safety-relevant property under mismatch: when the world disagrees with the priors, the printed confidence must still not exceed the realized hit rate by more than 0.10. |

If fixture composition changes, every baseline number, every construction
estimate and `z_half` must be re-derived in the same commit; M0f makes the
baseline half of that mechanical.

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python grailtrader/evals/run.py   # scorecard: metric | n | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest grailtrader/               # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads scenario A with offline adapters into
  an in-memory store, builds indices, runs the weekly backtest replay (real +
  3 placebo seeds + the three baselines), then scenario B, computes M0–M6,
  prints the table with actual values, `n` for every rate, the baseline
  columns, the two ungated diagnostics (D-fence, D-contra) and the validity
  caveat, and exits non-zero on any gate failure. `--regen-check` re-runs
  `generate_scenario.py` for both scenarios and diffs against the committed
  fixtures (also wired into CI).
- `evals/test_gates.py` — one pytest per gate, names referencing FR ids:
  `test_gate_m0_floors_fr4_fr8`, `test_gate_m1a_index_fidelity_fr4`,
  `test_gate_m1a_tails_fr4`, `test_gate_m1b_fence_fr3`,
  `test_gate_m2a_hit_rate_fr8_fr10`, `test_gate_m2b_spread_fr8`,
  `test_gate_m3_calibration_fr8`, `test_gate_m4_placebo_fr10`,
  `test_gate_m5_framing_fr9`, `test_gate_m6_mismatch_fr4_fr8`. Each asserts
  its population floor before its rate.
- `evals/metrics.py` — pure metric functions (including the independent
  frame-check reference implementation) shared by both entry points.
- **Runtime budget:** ≈ 38k listings × 36 strata, a 108-week × 36-garment
  replay ×4 (real + 3 placebo), plus scenario B — whole suite **≤ 90 s**. The
  replay builds the index **once** and slices it per week: every FR-4 quantity
  is backward-looking (trailing window, base week, chain-linked parents), so
  the index built from listings with `sold_at ≤ end of week t` equals the full
  build restricted to weeks ≤ t. T3 asserts that equivalence on sampled weeks.
  (It holds because ingest is append-only and windows key on `sold_at`; with
  real late-arriving comps it is only approximate, which is why live backtests
  rebuild per week.)
- Hermetic: no network, no wall clock, seeded randomness only (the three
  placebo seeds and the two generator seeds); `RssNewsFeed` is never imported
  on the eval path, enforced by T2.
