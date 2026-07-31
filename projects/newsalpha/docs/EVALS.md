# NewsAlpha — Evals

## What this product lives or dies on

1. **Extraction + linking correctness (hard part A, FR-4/5):** typed events
   with the right stage and attributes, linked to the right assets in the
   right roles, in the presence of negation, rumor, historical references,
   metaphor, duplicates, and ticker/common-word ambiguity. A system that
   turns "denied merger talks" into M&A bullishness or links a story about
   fruit to `eq:AAPL` is actively harmful.
2. **Honest signal quality (hard part B, FR-6/10):** signals whose direction
   and confidence are recoverable from subsequent (fixture) market data
   through a leak-free harness — and a harness that finds *nothing* when the
   event–date association is destroyed. The owner's brief demands the
   backtest "measures signal quality honestly"; the placebo gate is that
   promise, enforced.

Everything else (CRUD, digest ordering, watchlist, CLI rendering) is covered
by ordinary tests, not eval gates.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with offline adapters (`FixtureNewsFeed`, `FixtureMarketData`,
in-memory store). No network, no clock (all `as_of`/timestamps come from
fixtures), the only randomness is the committed placebo seed. M-numbers map
to gates in `evals/test_gates.py`; test names reference FR ids.

Unless stated otherwise, metrics run the *real pipeline end-to-end* on the
fixture corpus: `pipeline.ingest(articles, datasets, as_of)` → events,
links, signals, briefs → `backtest.run(...)` on the fixture bars.

### M1 — Event typing and attributes (capability A)

Predicted events are compared to truth at the (cluster, event_type) level
over the whole corpus (truth clusters are defined in `articles_truth.json`;
a predicted event matches truth iff its cluster's member articles equal the
truth cluster and the type matches).

```
For each type t:  P_t = TP_t/(TP_t+FP_t),  R_t = TP_t/(TP_t+FN_t),  F1_t = 2·P_t·R_t/(P_t+R_t)
M1a = mean of F1_t over the 8 EventTypes           (F1_t = 0 on zero denominators)
M1a-floor (sub-gate) = min over types of F1_t
M1b = over correctly-typed events: fraction where stage AND every annotated
      attribute match truth (polarity/venue exact; USD amounts and
      percentages exact after numeric normalization)
```

### M2 — Asset linking (capability A)

Truth links are (event, asset) pairs in `articles_truth.json` (role
ignored here — roles are M3). The trap subset is 45 annotated mentions
whose surface form matches a gazetteer alias: 25 with truth *no-link*
("near the end", apple the fruit, "growth hack" mentioning ONE as a word,
lowercase "meta"), 20 with truth *link* that requires disambiguation
(NEAR-the-protocol with context, "Meta" the company, `$COIN`).

```
M2a = F1 over predicted vs truth (event, asset) links, corpus-wide
M2b = (# trap mentions with correct link/no-link decision) / 45
```

### M3 — Role assignment (capability A)

Over the 24 signal-bearing role decisions on `mna` truth events that link
both parties (12 events × {acquirer, target}; ≥ 8 decisions come from
passive or target-first constructions — "B to be acquired by A"):

```
M3 = (# decisions where predicted role == truth role) / 24
```

Role errors are direction errors (target bullish vs acquirer bearish), which
is why this gates separately.

### M4 — Direction hit rate (capability B)

Backtest over all directional signals produced from the corpus (~105;
`unclear` and backtest-excluded signals are out, and exclusions are
asserted to be ≤ 5 % of signals). Entry and AR per FR-10; hit is measured
at each signal's own horizon.

```
M4 = (# directional signals with sign(AR_h) == dir_sign) / N_directional
```

The fixture market data contains *planted* abnormal effects (see fixture
strategy), so M4 measures whether the whole chain — extraction → linking →
role → polarity → scoring → entry timing — recovers effects that are truly
there. An extraction, role, or polarity error flips or misses a planted
effect and lands as a miss.

### M5 — Information coefficient (capability B)

```
M5 = Spearman rank correlation between signal.score and realized AR at the
     signal's own horizon, over the same N_directional signals
```

Planted effect magnitudes differ by type/role/stage (−18 % to +22 %), and
`score` encodes the priors' expected-AR midpoints × confidence, so a correct
implementation shows strong rank agreement (~0.5–0.65); shuffled or
constant scores show ≈ 0.

### M6 — Confidence calibration separation (capability B)

Fixed-edge confidence buckets: lo < 0.45 ≤ mid < 0.70 ≤ hi. Fixture
composition guarantees ≥ 15 directional signals per bucket (rumors,
partnerships, t3-only sources, and acquirer legs populate lo; confirmed
multi-source t1/t2 events populate hi).

```
M6 = hit_rate(hi bucket) − hit_rate(lo bucket)
```

True separation is ≈ 0.30 by construction: 40 % of rumor-stage events are
planted as *fizzles* (no effect — the rumor never materializes), and
minor-magnitude effects sit near the noise floor, so low-confidence signals
genuinely hit less often. A confidence formula that ignores stage/tier
collapses the buckets.

### M7 — Placebo honesty (capability B)

The same backtest with `placebo_seed = 20260731`: each signal's entry is
displaced by a seeded uniform ±[20, 60]-bar offset, re-drawn if the
displaced window overlaps any planted event window for that asset (FR-10).

```
M7a = |hit_rate_placebo − 0.5|
M7b = |IC_placebo|
```

A harness with look-ahead leakage, same-day entry, or window-selection bugs
shows spurious skill that survives date displacement; a clean harness
measures pure noise. Complemented by a plain pytest leak canary
(`test_fr10_entry_strictly_after_publication`) that constructs a bar dated
on the publication date and asserts the harness never uses it.

### M8 — Framing compliance (capability B safeguard, FR-7)

Over every brief rendered in the eval run (~105) plus the 12 adversarial
`frame_cases.json` scenarios (evidence quotes containing "investors should
buy", slot values containing "buyout"/"sell-off" — word-boundary matching
must pass these while catching bare imperatives):

```
M8 = (# briefs where an independent reference checker in evals/metrics.py
      finds: zero forbidden-lexicon matches outside quoted+attributed
      evidence, all four sections non-empty, footer present verbatim) / N
```

The reference checker is re-implemented in `evals/metrics.py` (simple,
independent code path) so the gate does not trust the engine's own
`frame.py` to grade itself.

### D0 — Determinism (plain pytest, no score)

Run the full pipeline twice from the fixture corpus into two fresh
in-memory stores; canonical JSON exports of articles, clusters, events,
links, signals, briefs, and one backtest run must be byte-identical, with
identical content-derived ids (FR-14). Re-ingesting the corpus a second
time into the same store must change nothing (idempotency).

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable
byte-identically by committed seeded scripts (`generate_articles.py`,
`generate_market.py`, both `--seed 20260731`; CI re-runs and diffs them to
prove integrity). **Ground truth never comes from the engine under
evaluation**: article truth comes from the generator's construction
parameters or hand labels; market truth comes from a planted-effect table
that is written from the event-study literature *independently* of
`data/priors.json` (correlated through the literature, never through code —
so a wrong prior cannot manufacture its own passing grade).

| File | Contents | Ground truth |
|---|---|---|
| `articles.jsonl` | ~180 articles: ~130 event-bearing (92 truth events across 8 types — earnings 14 (9 beat/5 miss), guidance 10 (5 raise/4 cut/1 withdraw), mna 16 (8 confirmed/5 rumored/3 denied; 12 link both parties), regulatory 12 (7 adverse/5 favorable), listing 10, delisting 6, hack 12, partnership 12), 15 of them covered by 2–3 paraphrased near-duplicates from different domains; ~30 no-event articles (earnings previews, market commentary, company mentions without events); ~20 hand-authored adversarial articles (negations — "denied reports it is in talks to acquire", historical references — "five years after the 2016 hack", metaphors — "growth hack", "hackathon", ambiguity traps for NEAR/ONE/ICP/APE/COIN/Meta/Oracle/Shell). Generated from ~40 parameterized newswire-style templates with distractor sentences; realistic tiered source domains (`*.example` TLDs). | Construction parameters per generated article; hand labels for hand-authored cases — all recorded in `articles_truth.json` |
| `articles_truth.json` | Per truth cluster: member article ids, event type, stage, attributes, (asset, role) links; per annotated mention: surface form, span, truth asset-or-none; the 45-mention trap subset ids; the 24 role decisions | Same |
| `market/*.csv` | Daily OHLCV for the ~70 fixture assets + `idx:US` + `idx:CX`, 420 calendar days covering all event dates. Generated as seeded GBM: benchmark daily log-return σ = 1.0 % (US) / 3.0 % (CX), zero drift; asset return = own benchmark return + idiosyncratic noise (σ = 1.2 % equity / 3.5 % crypto) + planted effects. Equity series skip weekends; crypto series are daily. | GBM parameters + planted effects (below) |
| `market_truth.json` | The planted-effect table and per-event draws. Mean planted AR at the entry bar (first bar after publication — the generator applies the same FR-10 rule): earnings beat +3.5 % then +2.5 % drift over bars 2–20 (PEAD-shaped), miss mirrored; guidance raise +5 % / cut −6 % / withdraw −7 %; mna target confirmed +22 %, rumored +12 % *with 60 % materialization* (40 % of rumors fizzle: zero effect), denied −6 % (unwind), acquirer −1.5 %; regulatory ∓5 % equity / ∓8 % crypto; listing +12 % over bars 1–5 (crypto) / +2 % (equity); delisting −18 %; hack −15 % at entry, −5 % more over bars 2–5. Each event's realized effect is drawn seeded with σ = 25 % of the mean magnitude. | By construction (the table + recorded draws) |
| `frame_cases.json` | 12 adversarial brief-render scenarios: evidence quotes containing imperative advice, slot values containing forbidden-word substrings ("buyout", "sell-off"), missing-section attempts, footer-tampering attempts | Hand-authored expected verdicts |
| `generate_articles.py`, `generate_market.py` | Seeded generators; committed; re-run + diffed in CI (each runs in seconds) | — |

Expected values for a correct implementation, derivable from the planted
z-scores (effect mean ÷ idiosyncratic σ at horizon): per-type hit
probabilities range from ≈ 0.99 (hack, mna target confirmed) down to
≈ 0.60–0.70 (acquirer legs, partnerships, rumor population including
fizzles), giving overall M4 ≈ 0.85, M5 ≈ 0.55, M6 ≈ 0.30. These derivations
are reproduced in comments in `generate_market.py`.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1a event macro-F1 | one keyword per type, per article, no suppressors/clustering ("acquire"→mna, "hack"→hack_exploit) | ≈ 0.45 (fires on negations, historical refs, metaphors; duplicates triple-count) | **≥ 0.80**, floor F1_t **≥ 0.65** | Fixtures are constructed so each trap defeats exactly the naive shortcut; a correct pattern engine with suppressors scores ≈ 0.9. 0.80 tolerates a handful of paraphrase misses; the floor stops one dead type hiding behind the mean. |
| M1b stage+attributes | majority polarity per type, stage always `confirmed` | ≈ 0.55 | **≥ 0.85** | Attributes drive polarity → direction; stage drives confidence. Both are deterministic template fills on constructed text; 0.85 allows edge-case regex misses only. |
| M2a link F1 | link every alias occurrence, any case, no context rules | ≈ 0.70 (recall ≈ 0.95, precision ≈ 0.55) | **≥ 0.85** | Precision-first design (SCOPE D-3) must show up as measurably better linking overall, not only on traps. |
| M2b trap accuracy | same | ≈ 0.38 (15/45: links everything, right only on the 20 true-link traps minus case errors) | **≥ 0.90** | Traps are the product's credibility: 0.90 (≤ 4 misses of 45) requires case rules, ambiguity flags, and context keywords all working. |
| M3 role accuracy | first-linked-asset = acquirer | ≈ 0.55 (passive/target-first constructions are ≥ 8/24 by construction) | **≥ 0.85** | A role error is a sign error on a major-magnitude signal; ≥ 0.85 (≤ 3 misses) requires real active/passive handling, while allowing rare exotic syntax. |
| M4 direction hit rate | always-bullish | ≈ 0.53 (planted-positive fraction among the ~105 directional truths); coin flip 0.50 | **≥ 0.75** | Planted effects make ≈ 0.85 achievable end-to-end; every extraction/link/role/polarity error costs hits. 0.75 sits ≈ 4σ above coin-flip at N ≈ 105 (SE ≈ 0.049) and fails a pipeline whose extraction is right but whose scoring/timing is broken, or vice versa. |
| M5 information coefficient | constant or shuffled scores | ≈ 0.00 | **≥ 0.35** | Requires magnitude/confidence structure, not just direction: a sign-only scorer with random magnitudes scores ≈ 0.25 on these fixtures; the priors' rank structure is needed to clear 0.35 (correct ≈ 0.55). |
| M6 calibration separation | confidence without stage/tier/corroboration modifiers (extraction_conf only) | ≈ 0.05 (buckets barely differ) | **≥ 0.15** | True separation ≈ 0.30 by construction (rumor fizzles + noise-floor minors in lo bucket). 0.15 proves confidence *means something* while tolerating bucket-boundary noise. ≥ 15 signals per bucket guaranteed by fixture composition. |
| M7a placebo \|hit − 0.5\| | leaky harness (entry at publication-date bar; window anchored to planted dates) | ≥ 0.12 spurious | **≤ 0.06** | 0.06 ≈ 1.3 binomial SE at N ≈ 100, verified on the committed seed; a leak that survives date displacement exceeds it. |
| M7b placebo \|IC\| | same | ≥ 0.15 spurious | **≤ 0.08** | Same argument for rank correlation (SE ≈ 1/√N ≈ 0.10; the committed seed realizes ≈ 0.03). |
| M8 framing compliance | templates without frame check, naive substring matching | ≈ 0.90 (the 12 adversarial cases break it) | **= 1.0** | This is the finance safeguard (workspace rule): partial credit is meaningless — one imperative emitted is a product failure. Deterministic templates make exactness fair. |

If fixture composition changes, every baseline number in this table must be
re-derived in the same commit (checked in review).

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python newsalpha/evals/run.py     # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest newsalpha/                 # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads the fixture corpus and bars, runs the
  full pipeline with offline adapters into an in-memory store, computes
  M1–M8 + D0, prints the table with actual values, exits non-zero on any
  gate failure. `--regen-check` re-runs both generators and diffs their
  output against the committed fixtures (also wired into CI; seconds).
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1a_event_typing_fr4`, `test_gate_m2b_link_traps_fr5`,
  `test_gate_m3_roles_fr5`, `test_gate_m4_hit_rate_fr6_fr10`,
  `test_gate_m7_placebo_fr10`, `test_gate_m8_framing_fr7`, …); names
  reference FR ids so the FR → test mapping is auditable.
- `evals/metrics.py` — pure metric functions (including the independent
  frame-check reference implementation) shared by both entry points.
- **Runtime budget:** pure text processing and arithmetic over ~180
  articles and ~30k bars — whole suite ≤ 30 s. No long-running calibration
  step exists in this project.
- Hermetic: no network, no wall clock, seeded randomness only (the placebo
  seed); `RSSNewsFeed` and `LiveMarketData` are never imported on the eval
  path.
