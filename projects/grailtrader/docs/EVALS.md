# GrailTrader — Evals

## What this product lives or dies on

1. **Index fidelity from hostile listings (hard part A, FR-3/4):** the
   per-stratum indices must recover the true price level from sparse,
   heterogeneous, outlier-polluted sold listings. Every valuation and every
   advice is computed *on* the index; if the index is noise, the advisor is
   a random-number generator with a confident tone.
2. **Honest event-driven advice (hard part B, FR-6/8/10):** buy/sell/hold
   decisions derived from typed events and decay-shaped impact priors must
   beat naive baselines on directional accuracy, with calibrated
   confidence, through a backtest that is provably leak-free — a placebo
   run with scrambled event dates must show no skill. The owner's brief is
   "predict how prices will be affected, advise buy/sell/hold"; these
   gates are that promise, enforced.

Everything else (CRUD, condition alias mapping, CLI rendering, gazetteer
validation) is covered by ordinary tests, not eval gates.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against
committed fixtures with offline adapters (`FixtureListingsFeed`,
`FixtureNewsFeed`, `FixtureSocialFeed`, in-memory store). No network, no
clock (all timestamps come from fixtures), the only randomness is the
committed placebo seed. M-numbers map to gates in `evals/test_gates.py`;
test names reference FR ids.

M1 exercises `index build` alone. M2–M4 run the real pipeline end-to-end:
ingest listings + events → build indices → weekly backtest replay
(FR-10) over the eval portfolio, scoring realized returns against the
**planted truth index** (`reference: "truth"`), which the advisor never
sees. M5 checks every advice rendered during the replay plus adversarial
cases.

### M1 — Index fidelity (capability A)

**M1a — scale-aligned median absolute percentage error.** For each leaf
stratum s, over the weeks where the engine wrote an index point, align
scale (base-week noise must not count as tracking error), then compare to
the planted latent truth `T`:

```
α_s  = exp( mean over eligible weeks of ln(T_s,t / R_s,t) )
M1a  = median over all eligible (s, t) cells of | α_s · R_s,t / T_s,t − 1 |
```

**M1b — outlier fence quality.** Planted outliers (fakes, mislabels) are
listed by id in `listings_truth.json`:

```
M1b-recall = (# planted outliers excluded by the FR-3 fence in ≥ 1 window
              they fall in) / (# planted outliers in eligible windows)
M1b-false  = (# clean sold listings excluded in any window) / (# clean
              sold listings in eligible windows)
```

A fence that is too loose lets fakes drag the index (shows up in M1a
too); one that is too tight starves windows below `min_sales` and creates
staleness. Both failure directions are separately visible.

### M2 — Advisor directional skill (capability B)

Backtest replay over weeks 12–156 of the scenario, eval portfolio of 56
garments (one per leaf stratum). Actionable = advice with
`action ∈ {buy, sell}`; entry at week t+1; realized return from the truth
index at the advice's own horizon (FR-10). Exclusions
(`insufficient_future_index` at scenario edge, etc.) are counted and
asserted ≤ 5 % of actionable advice.

```
M2a = (# actionable advice with sign(realized) == sign(action)) / N_actionable
       (buy → realized > 0; sell → realized < 0)
M2b = mean(realized | buy) − mean(realized | sell)
```

The fixture scenario plants true impact paths with a 2-week diffusion lag
(sellers reprice slowly), so genuine buy windows (event known, index not
yet moved) and sell windows (transient priced, decay ahead) exist by
construction. An index error, a wrong impact scope, a broken decay curve,
or a leaky replay all land here as misses.

### M3 — Confidence calibration separation (capability B)

Fixed-edge confidence buckets over actionable advice: lo < 0.45 ≤ mid
< 0.70 ≤ hi. Fixture composition guarantees ≥ 25 actionable decisions per
bucket: fizzled co-signs/collabs (planted zero impact) and sparse-stratum
(stale-index, q = 0.5) decisions populate lo; fresh-index,
manual-confirmed departures/scandals populate hi.

```
M3 = hit_rate(hi bucket) − hit_rate(lo bucket)
```

True separation is ≈ 0.22 by construction: 4/12 co-signs and 2/8 collabs
fizzle (the event happens, the market never moves — real co-signs often do
nothing, which is why their prior base_conf is 0.50), and sparse strata
genuinely track worse. A confidence formula that drops `c_event` or
`q_index` collapses the buckets.

### M4 — Placebo honesty (capability B)

The same backtest with `placebo_seed = 20260731`: every event's week is
displaced by a seeded uniform ±[26, 52]-week offset (redrawn while the
displaced active window overlaps the event's true active window; FR-10).
The advisor now acts on confidently wrong premises.

```
M4a = | hit_rate_placebo − 0.5 |        (over actionable advice)
M4b = | spread_placebo |                (buy-minus-sell, as M2b)
```

A replay that peeks at future listings, enters at week t instead of t+1,
or anchors baselines on post-event data shows spurious skill that
survives date displacement; a clean harness measures noise. Complemented
by plain pytest leak canaries: `test_fr10_entry_is_next_week` and
`test_fr10_advice_ignores_future_listings` (constructs a listing sold in
week t+1 that would flip the index and asserts advice at t is unchanged).

### M5 — Advice framing compliance (safeguard, FR-9)

Over every advice rendered during the M2 replay (~thousands, mostly holds)
plus the 10 adversarial scenarios in `frame_cases.json` (garment labels
and event notes containing forbidden words — must pass only inside quoted
context; footer-tampering; missing-section attempts):

```
M5 = (# advice where an independent reference checker in evals/metrics.py
      finds: zero forbidden-lexicon matches outside quoted user-supplied
      text, all required sections present, fee note present, footer
      verbatim) / N
```

The reference checker is re-implemented in `evals/metrics.py` so the gate
does not trust the engine's own `frame.py` to grade itself. This is the
workspace finance-safeguard rule as implemented behavior.

### D0 — Determinism (plain pytest, no score)

Ingest the fixture scenario twice into two fresh in-memory stores;
canonical JSON exports of listings, index points, events, advice, and one
backtest run must be byte-identical with identical content-derived ids
(FR-14). Re-ingesting into the same store must change nothing
(idempotency), and `index build` re-run on unchanged listings must be a
byte-identical replace.

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable
byte-identically by the committed seeded generator
(`generate_scenario.py --seed 20260731`; CI re-runs it and diffs against
the committed files). **Ground truth never comes from the engine under
evaluation**: listing truth and index truth come from the generator's
construction parameters; the planted impact table is written from the
case literature (Philo/Céline, Abloh/StockX, Supreme×LV, Balenciaga 2022,
adidas–Yeezy) *independently* of `data/impact_priors.json` — correlated
through the literature, never through code — so a wrong prior cannot
manufacture its own passing grade. All fixture brands are fictional
(SCOPE D-14): scandal and death events must not attach to real brands or
people.

| File | Contents | Ground truth |
|---|---|---|
| `brands_fixture.json` | 10 fictional brands / 22 eras modeled on documented archetypes (founder-era archive house, hype-collab streetwear label, scandal-hit megabrand, quiet heritage brand, …), 8 categories → 56 leaf strata: 48 dense (Poisson λ = 6–14 sales/week) + 8 sparse (λ = 0.5–1.5, exercising staleness and q_index paths). 2 brands receive no events at all (hold correctness). | By construction |
| `listings.jsonl` | 156 weeks (Mondays 2023-01-02 … 2025-12-22), ~26,000 sold + ~2,500 active ask-only listings. Sold price = latent_level × condition_multiplier × lognormal item factor (σ = 0.28); condition sampled (new 10 %, excellent 45 %, good 30 %, fair 12 %, poor 3 %); ask-only listings at 1.25–1.45× latent (must never enter the index). Planted pollution: ~2 % fakes at 0.15–0.30× latent, ~1 % mislabels at 2.5–4×. | Per-listing construction params + planted-outlier ids in `listings_truth.json` |
| `index_truth.json` | Weekly latent truth level per leaf stratum: GBM with weekly σ = 2 %, zero drift, category-typical USD anchors, plus planted impact paths phased in linearly over a 2-week diffusion lag. | By construction |
| `events.jsonl` | 44 typed events across the six types: 8 departures (3 resignation / 2 ousted / 2 death / 1 house_closure), 6 appointments, 8 collabs (2 fizzle), 12 co-signs (4 a_list / 5 b_list / 3 niche; 4 fizzle), 6 runway (4 acclaimed / 2 panned), 4 scandals (1 minor / 2 moderate / 1 severe); sources mixed manual/news/social; spaced so overlap, decay tails, and quiet stretches all occur. | `impact_truth.json` |
| `impact_truth.json` | The planted impact table (per event: true P, T, half-life, target strata, fizzle flag, diffusion lag) and each event's realized path parameters. Magnitudes are drawn seeded with σ = 25 % around case-literature anchors — e.g. departure/death P ≈ +0.20, T ≈ +0.25 (Abloh/StockX); co-sign T ≈ +0.10 · tier, P = 0 (Lyst/Depop search-spike pattern); severe scandal P ≈ −0.15 (Balenciaga 2022 demand drop). | By construction |
| `eval_portfolio.json` | 56 garments — one per leaf stratum, condition `excellent`, acquired in week 8 at the then-truth level (so FR-7 fair value tracks the index one-to-one), status `owned`. | By construction |
| `frame_cases.json` | 10 adversarial advice-render scenarios: labels like `"guaranteed grail — will definitely moon"`, event notes with forbidden phrases, footer tampering, section omission. | Hand-authored expected verdicts |
| `generate_scenario.py` | Seeded generator; committed; re-run + diffed in CI (runs in seconds). | — |

Expected values for a correct implementation, derivable from construction
(derivations reproduced as comments in `generate_scenario.py`): with
window n ≈ 24–56 sold and item σ = 0.28, the median's sampling error puts
M1a ≈ 0.035–0.045; planted z-scores (impact magnitude ÷ σ_w√H) put per-type
hit probabilities from ≈ 0.95 (departures, scandals) down to ≈ 0.55–0.65
(fizzle-diluted co-signs/collabs that still clear thresholds), giving
M2a ≈ 0.82, M2b ≈ +0.18, M3 ≈ 0.22.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1a index error | weekly arithmetic mean of raw sold prices, no condition adjustment, no fence, no trailing window | ≈ 0.12–0.15 (fakes and small-n means; mean vs median alone roughly doubles error at σ = 0.28) | **≤ 0.06** | Correct implementation lands ≈ 0.04 by the sampling-error derivation; 0.06 leaves room for window-lag error during planted moves while sitting at half the baseline. Lower is better; the gate is an upper bound. |
| M1b-recall / M1b-false | no fence | 0.00 recall / 0.00 false | **≥ 0.90 / ≤ 0.05** | Planted fakes (0.15–0.3×) and mislabels (2.5–4×) sit ≥ 4σ outside clean noise, so ≥ 0.90 requires the fence to actually run; ≤ 0.05 false-exclusion stops the degenerate "exclude everything" fence, which would also blow up staleness. |
| M2a directional hit | 4-week momentum (buy iff trailing 4-wk return ≥ +10 %, mirrored sell) | ≈ 0.52 (fires after spikes, exactly when decay begins) | **≥ 0.70** | Planted effects make ≈ 0.82 achievable end-to-end; every index, scope, decay, or timing error costs hits. 0.70 is ≈ 8 binomial SE above coin-flip at N ≈ 600 and decisively above both naive event responses. |
| (second baseline, same gate) | event-naive: buy for 12 weeks on any confirmed event touching the stratum | ≈ 0.60 (right on bullish events pre-peak; wrong on scandals, panned shows, fizzles, and all decay phases) | — | Shows the advisor must model *direction, decay, and pricing state*, not merely react to event existence. Both baselines are computed inside every backtest run (FR-10) and printed in the scorecard. |
| M2b buy−sell spread | always-hold / either naive | ≈ 0.00 (hold) / ≈ +0.02 (naives have no sell leg to speak of) | **≥ +0.08** | Requires both legs to work: buys must capture phase-in upside and sells must capture decay. 0.08 is ≈ half the constructed spread (≈ 0.18), robust to draw noise. |
| M3 calibration separation | confidence without `c_event`/`q_index` modifiers (z-term only) | ≈ 0.05 | **≥ 0.12** | True separation ≈ 0.22 by construction (fizzles + sparse strata populate lo). 0.12 proves confidence carries information while tolerating bucket-edge noise; ≥ 25 actionable per bucket guaranteed by fixture composition. |
| M4a placebo \|hit − 0.5\| | leaky harness (entry at week t; baseline anchored post-event) | ≥ 0.12 spurious | **≤ 0.06** | At N ≈ 500+ actionable placebo decisions, SE ≈ 0.022; 0.06 ≈ 2.7 SE on the committed seed. A leak that survives ±26–52-week displacement exceeds it. |
| M4b placebo \|spread\| | same | ≥ 0.06 spurious | **≤ 0.04** | Same argument on the return spread; the committed seed realizes ≈ 0.01. |
| M5 framing compliance | templates without frame check, naive substring matching | ≈ 0.90 (adversarial cases break it) | **= 1.0** | Workspace finance-safeguard rule: partial credit is meaningless — one certainty-claiming or footer-less advice is a product failure. Deterministic templates make exactness fair. |

If fixture composition changes, every baseline number in this table must
be re-derived in the same commit (checked in review).

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python grailtrader/evals/run.py   # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest grailtrader/               # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads the fixture scenario with offline
  adapters into an in-memory store, builds indices, runs the weekly
  backtest replay (real + placebo + the three baselines), computes M1–M5,
  prints the table with actual values (including baseline columns), exits
  non-zero on any gate failure. `--regen-check` re-runs
  `generate_scenario.py` and diffs its output against the committed
  fixtures (also wired into CI; seconds).
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1a_index_fidelity_fr4`, `test_gate_m1b_fence_fr3`,
  `test_gate_m2a_hit_rate_fr8_fr10`, `test_gate_m2b_spread_fr8`,
  `test_gate_m3_calibration_fr8`, `test_gate_m4_placebo_fr10`,
  `test_gate_m5_framing_fr9`); names reference FR ids so the FR → test
  mapping is auditable. D0 and the leak canaries live in ordinary tests
  (`tests/test_determinism_fr14.py`, `tests/test_backtest_fr10.py`).
- `evals/metrics.py` — pure metric functions (including the independent
  frame-check reference implementation) shared by both entry points.
- **Runtime budget:** median arithmetic over ~28k listings × 56 strata and
  a 156-week × 56-garment replay ×2 (real + placebo) — whole suite ≤ 60 s.
- Hermetic: no network, no wall clock, seeded randomness only (the placebo
  seed); `EbayListingsFeed` and `RssNewsFeed` are never imported on the
  eval path.
