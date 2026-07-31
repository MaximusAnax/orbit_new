# NewsAlpha — Evals

## What this product lives or dies on

1. **Extraction + linking correctness (hard part A, FR-4/5):** typed events
   with the right stage and attributes, linked to the right assets in the
   right roles, in the presence of negation, rumor, historical references,
   metaphor, duplicates, all-caps wire headlines, venue/asset collision, and
   ticker/common-word ambiguity. A system that turns "denied merger talks"
   into M&A bullishness, links a story about fruit to `eq:AAPL`, or emits a
   bullish `eq:COIN` signal every time a token lists on Coinbase, is actively
   harmful.
2. **Honest signal quality (hard part B, FR-6/10/15):** signals whose
   direction and confidence are recoverable from subsequent (fixture) market
   data through a leak-free harness — and a harness that finds *nothing* when
   the event–date association is destroyed. The owner's brief demands the
   backtest "measures signal quality honestly"; the placebo gate is that
   promise, enforced.

Everything else (CRUD, digest ordering, watchlist, CLI rendering, revision
bookkeeping) is covered by ordinary tests, not eval gates.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with offline adapters (`FixtureNewsFeed`, `FixtureMarketData`,
in-memory store). No network, no clock (all `as_of`/timestamps come from
fixtures), the only randomness is committed seeds. M-numbers map to gates in
`evals/test_gates.py`; test names reference FR ids.

Unless stated otherwise, metrics run the *real pipeline end-to-end* on the
fixture corpus: `pipeline.ingest(articles, datasets, as_of)` → clusters,
events, links, signals, briefs → `backtest.run(...)` on the fixture bars.

### The DEV / VAL split (anti-overfit, applies to M1–M3)

Every truth event is realized through exactly one of **two disjoint
paraphrase template families**:

- **DEV** (~14 templates, 32 truth events) — the family patterns may be
  authored against.
- **VAL** (~14 templates, 76 truth events, **plus all 40 hand-authored
  adversarial articles, all 50 trap mentions, and all 40 role decisions**) —
  the family the gates score.

`generate_articles.py --check-disjoint` asserts, and CI re-checks, that the
set of trigger-bearing n-grams (3-grams spanning any trigger lexeme) used by
VAL templates is **disjoint** from DEV's, and that the two families differ in
sentence frame (active/passive), argument order, and distractor style. A
pattern set memorised from DEV therefore cannot fire on VAL.

**M1a, M1b, M1c, M2a, M2b and M3 are computed on VAL only and gated there.**
DEV values are reported beside them, and the *transfer gap* (DEV − VAL) is
itself gated: a large gap is the signature of fixture overfitting.

**Residual risk, stated plainly:** both families are authored by the same
person in the same repo, so this is *structural* distance, not blindness. A
sufficiently determined implementer can still tune to VAL. The remaining
defences are non-statistical: FR-3 requires every `EventPattern` to carry a
non-empty `real_world_example` (a real headline it generalizes from,
auditable in review), and no pattern may name a fixture-only entity. Passing
these gates is evidence the extractor generalizes across phrasings; it is
**not** evidence it transfers to live RSS text, and this document does not
claim otherwise.

### M1 — Event typing, stage and attributes (capability A, VAL)

A predicted cluster `P` **aligns** to a truth cluster `T` iff
`|P ∩ T| > |P|/2` and `|P ∩ T| > |T|/2` (majority overlap — unique by
construction). A predicted event matches truth iff its cluster aligns to the
truth cluster and the event type matches. Majority alignment rather than set
equality keeps M1 a measure of *typing*, not of clustering; exact cluster
reconstruction is covered separately by the ordinary test
`test_fr2_exact_cluster_reconstruction` (US-3), which requires 100 % exact
recovery on the corpus.

```
For each type t:  P_t = TP_t/(TP_t+FP_t),  R_t = TP_t/(TP_t+FN_t),  F1_t = 2·P_t·R_t/(P_t+R_t)
M1a       = mean of F1_t over the 7 EventTypes        (F1_t = 0 on zero denominators)
M1a-floor = min over types of F1_t
M1a-transfer = M1a(DEV) − M1a(VAL)
M1b = over correctly-typed events: fraction where stage AND every annotated
      attribute match truth (polarity/venue exact; USD amounts and
      percentages exact after numeric normalization)
M1c = the same fraction over ALL VAL truth events (a missed event counts as a
      failure) — the unconditional variant, so M1b cannot be inflated by an
      implementation that misses exactly the hard cases
```

Cluster recoverability is a committed property of the fixtures, not a hope:
`generate_articles.py` asserts (and records in comments) that under FR-2's
pinned tokenization **every intra-cluster article pair has Jaccard ≥ 0.72**
and **every inter-cluster pair ≤ 0.45**, giving any faithful FR-2
implementation ≥ 0.12 of slack on both sides of the 0.60 threshold.

### M2 — Asset linking (capability A, VAL)

Truth links are (event, asset) pairs in `articles_truth.json` (role ignored
here — roles are M3). The **trap subset is 50 annotated mentions** whose
surface form matches a gazetteer alias:

- **28 truth *no-link*:** lowercase "near the end" ×3, apple-the-fruit ×2,
  "growth hack"/"hackathon"/"life hack" ×3, lowercase
  "meta"/"oracle"/"shell"/"visa" ×5, "one of the"/"at one point" ×3,
  "coin flip"/"the coin" ×2, **all-caps wire headlines** where NEAR/ONE/APE/
  COIN appear as ordinary English words ×5, ticker-like tokens inside quoted
  product names ×2, "ICP" as a non-crypto acronym ×3.
- **22 truth *link*:** NEAR-the-protocol with context ×3, "Meta" the company
  ×3, `$COIN` cashtag ×2, `NASDAQ: AAPL` prefix ×2, parenthesized ticker
  after a name ×2, all-caps headlines where alias + context still resolves
  ×3, "Oracle Corp" ×2, "Shell plc" ×2, "APE" with crypto context ×3.

```
M2a = F1 over predicted vs truth (event, asset) links, VAL corpus-wide
M2b = (# trap mentions with the correct link / no-link decision) / 50
```

### M3 — Role assignment (capability A, VAL)

**40 role decisions**, all in VAL:

- **28 M&A acquirer/target decisions** from the 14 `mna` truth events that
  link both parties — 15 from active constructions, 13 from passive or
  target-first constructions ("B to be acquired by A", "takeover bid for B");
- **4 abstention decisions** from the 4 symmetric-construction `mna` events
  ("merger between A and B", "A-B merger talks", "merger of equals"): the
  decision is correct iff the system assigns *no* acquirer and *no* target
  and emits no `mna` signal (FR-5 fallback);
- **8 venue-vs-subject decisions** from `listing`/`delisting` events, of
  which 4 name a venue that is itself a gazetteer asset (Coinbase → `eq:COIN`
  ×2, Nasdaq → `eq:NDAQ`, NYSE → `eq:ICE`): the decision is correct iff the
  venue gets `role = venue`, the listed asset gets `subject`, and no signal
  is emitted for the venue asset.

```
M3 = (# decisions where the predicted role assignment == truth) / 40
```

Role errors are direction errors (target bullish vs acquirer bearish) or
outright fabricated signals (a bullish `eq:COIN` on every Coinbase listing),
which is why this gates separately.

### M4 — Direction hit rate (capability B, full corpus)

Backtest over all directional signals produced from the corpus (N ≈ 112 after
exclusions; `unclear` resolutions never become signals). Entry and AR per
FR-10; hit is measured at each signal's own horizon; superseded signals are
included. M4/M5/M6 run on DEV+VAL for statistical power — the fixture-overfit
failure mode they face is different from M1–M3's, since market ground truth
comes from a planted-effect table authored independently of `priors.json`.

```
M4        = mean over the 5 committed market seeds of
            (# directional signals with sign(AR_h) == dir_sign) / N_directional
M4-transfer = M4(DEV events) − M4(VAL events)
```

The fixture market data contains *planted* post-entry effects (see fixture
strategy), so M4 measures whether the whole chain — extraction → linking →
role → polarity → stage → scoring → entry timing → benchmark subtraction —
recovers effects that are truly there. An extraction, role, or polarity error
flips or misses a planted effect and lands as a miss; a harness that forgets
the benchmark leg inflates its noise (benchmark σ is 1.0 %/3.0 % per bar
against idiosyncratic 0.6 %/1.6 %) and drops well below the gate.

### M5 — Information coefficient (capability B)

```
M5 = mean over the 5 market seeds of the Spearman rank correlation between
     signal.score and realized AR at the signal's own horizon, over the same
     N_directional signals
```

Planted post-entry effect magnitudes differ by type/role/stage/asset kind
(−5.0 % to +4.5 %), and `score` encodes the priors' post-entry midpoints ×
confidence, so a correct implementation shows strong rank agreement
(≈ 0.50); shuffled or constant scores show ≈ 0. Kind-differentiated priors
(FR-3 resolution) are load-bearing here: a kind-blind prior table ranks a
crypto listing and an index add identically and loses ≈ 0.10 of IC.

### M6 — Confidence calibration separation (capability B)

Fixed-edge confidence buckets: `lo < 0.45 ≤ mid < 0.70 ≤ hi`.

```
M6           = mean over the 5 market seeds of [hit_rate(hi) − hit_rate(lo)]
M6-occupancy = min bucket count over the three buckets   (sub-gate, see below)
```

**Bucket occupancy is part of the gate.** `M6` is undefined for an empty
bucket, and a confidence function that refuses to spread mass across buckets
is *itself* a calibration failure — so the gate **fails** (never passes
vacuously) if any bucket holds < 20 directional signals. The fixture
composition puts ≈ 32 signals in lo, ≈ 35 in mid, ≈ 45 in hi under the
reference formula.

True separation is ≈ 0.21 by construction: rumor-stage events fizzle 60 % of
the time (no effect), t3-only single-source stories are planted as false
reports 50 % of the time, and acquirer legs / equity index adds sit at the
noise floor — so low-confidence signals genuinely hit less often. A
confidence formula that ignores stage, tier or corroboration collapses the
buckets.

### M7 — Placebo honesty (capability B)

The same backtest with displaced entries (FR-10). Offsets are **hash-derived
per signal** — `sha256(placebo_seed | signal_id | attempt)` — never drawn
from a shared sequential stream, so one extra or missing signal cannot
re-roll every other signal's displacement, and the realization is stable
under the implementation variation M1's 0.80 gate tolerates.

```
Realizations = 5 market seeds × 3 placebo seeds (20260731, 20260801, 20260802) = 15
M7a = | mean over realizations of hit_rate_placebo − 0.5 |
M7b = | mean over realizations of IC_placebo |
```

Both are absolute values **of the mean**, not means of absolute values, so a
clean harness's noise cancels while a leaky harness's bias does not.

A harness with look-ahead leakage, same-day entry, or window-selection bugs
shows spurious skill that survives date displacement; a clean harness
measures pure noise. Complemented by two plain pytest canaries:

- `test_fr10_entry_strictly_after_publication` — constructs a bar dated on
  `date(observed_at)` and asserts the harness never reads it;
- `test_fr10_announcement_bar_not_captured` — the generator plants the *full
  announcement jump* on the publication-date bar (see fixture strategy), so
  any harness that enters one bar early captures a ±5–20 % move. `run.py`
  reports an **announcement-bar capture rate** which must be exactly 0.

### M8 — Framing compliance (safeguard, FR-7)

M8 is a **verdict-match** metric, not a violation count, so
expected-violation scenarios contribute correctly:

- every brief rendered in the eval run (N ≈ 112) — expected verdict **PASS**:
  the brief was persisted, the independent reference checker finds zero
  forbidden-lexicon matches outside quoted+attributed evidence, all four
  sections are non-empty, and the footer is present verbatim;
- the **16 `frame_cases.json` scenarios** with hand-authored expected
  verdicts:
  - **9 expected-VIOLATION** (bare imperatives in a template slot, footer
    tampering, an emptied section, an unattributed forbidden quote): correct
    iff the pipeline **raises**, **no brief is persisted**, *and* the
    reference checker independently flags the rendered text;
  - **7 expected-PASS** (forbidden words inside quoted + attributed evidence;
    slot values containing "buyout", "sell-off", "buyer", "sell-side"):
    correct iff both the engine and the reference checker accept.

```
M8 = (# scenarios whose observed verdict equals the expected verdict) / (N + 16)
```

**Lexicon independence.** The reference checker in `evals/metrics.py` embeds
its own `REFERENCE_FORBIDDEN_LEXICON` as a literal in code and **never loads
`data/patterns.json`** — otherwise shipping a weakened lexicon would defeat
`frame.py` and its grader simultaneously. A companion gate,
`test_gate_m8_lexicon_superset_fr7`, asserts
`patterns.json.forbidden_lexicon ⊇ REFERENCE_FORBIDDEN_LEXICON`.

### G1 / G2 — Denominator integrity (gates, not scores)

Prose assertions are not gates; these are.

```
G1 = N_directional (real run, all seeds identical by construction)
G2 = (# excluded backtest results) / (# considered signals), real run,
     counting every ExclusionReason including unclear-resolution abstentions
G2p = the same ratio for placebo runs (reported; placebo_no_clean_window is
      expected and does not fail G2)
```

Without these, an implementation could raise M4/M5/M6 by marking hard signals
`unclear` or by dropping hard events inside M1a's 0.80 tolerance.

### D0 / D1 — Determinism and replay equivalence (plain pytest, no score)

- **D0 (determinism, FR-14):** run the full pipeline twice from the fixture
  corpus into two fresh in-memory stores; canonical JSON exports of articles,
  clusters, events, links, signals, briefs and one backtest run must be
  byte-identical, with identical content-derived ids. Re-ingesting the corpus
  a second time into the same store must produce zero new signal revisions.
- **D1 (replay equivalence, FR-14/FR-15):** ingest the same corpus (a) as one
  batch and (b) as five chronological daily batches with different `as_of`
  values. Assert clusters, events and links are **byte-identical**, and that
  the latest revision per (event_type, asset_id, role) has an identical
  scored tuple `(direction, magnitude, horizon_bars, round(confidence, 4),
  prior_key)`. Revision counts, `observed_at` and `created_as_of` may differ.
  The corpus includes one deliberately out-of-order arrival (a Reuters
  follow-up whose `published_at` precedes an already-ingested blog post) and
  one late corroboration that must raise confidence via a revision, not
  mutate one.

## Fixture strategy

Everything is committed under `evals/fixtures/`, regenerable
byte-identically by committed seeded scripts (`generate_articles.py --seed
20260731`, `generate_market.py --seeds 1,2,3,4,5`; CI re-runs and diffs them
to prove integrity). **Ground truth never comes from the engine under
evaluation**: article truth comes from the generator's construction
parameters or hand labels; market truth comes from a planted-effect table
written from the event-study literature *independently* of `data/priors.json`
(correlated through the literature, never through code — so a wrong prior
cannot manufacture its own passing grade).

### Truth event composition (108 events, 7 types)

| Type | Total | Breakdown | DEV | VAL |
|---|---|---|---|---|
| `earnings_surprise` | 18 | 11 beat / 7 miss | 6 | 12 |
| `guidance_change` | 14 | 7 raise / 5 cut / 2 withdraw | 5 | 9 |
| `mna` | 20 | 11 confirmed / 6 rumored / 3 denied; 14 resolve both parties, 4 symmetric-construction, 2 target-only | 2 | 18 |
| `regulatory_action` | 16 | 9 adverse / 7 favorable; 8 equity / 8 crypto | 5 | 11 |
| `listing` | 14 | 10 crypto / 4 equity | 5 | 9 |
| `delisting` | 12 | 9 crypto / 3 equity | 4 | 8 |
| `hack_exploit` | 14 | 12 crypto / 2 equity | 5 | 9 |
| **total** | **108** | | **32** | **76** |

The `mna` split is deliberately VAL-heavy: every role decision and every
adversarial construction lives in VAL. Directional signals: 18 + 14 + (16
targets + 11 acquirers) + 16 + 14 + 12 + 14 = **115 scored, ≈ 112 after
exclusions**. Per-type VAL counts are ≥ 8, so the M1a-floor is quantized: at
8 truth events one FN gives F1 = 0.93, two 0.86, three 0.77, four 0.67 — the
0.65 floor therefore tolerates at most four misses in the smallest type, and
this coarseness is why the floor is not set higher.

### Files

| File | Contents | Ground truth |
|---|---|---|
| `articles.jsonl` | ≈ 190 articles: 138 event-bearing (the 108 truth events; 20 of them covered by 2–3 paraphrased near-duplicates from different domains), 30 generated no-event articles (earnings previews, market commentary, company mentions without events), 22 hand-authored no-event traps. Each article carries `family: dev\|val`. Generated from two disjoint template families (≈ 14 templates each) with distractor sentences; realistic tiered source domains (`*.example` TLDs), with ≈ 30 events placed on single t3-only domains. | Construction parameters per generated article; hand labels for hand-authored cases — all in `articles_truth.json` |
| hand-authored subset | 40 articles (all VAL, no template overlap): negations ("denied reports it is in talks to acquire"), historical references ("five years after the 2016 hack"), metaphors ("growth hack", "hackathon"), all-caps wire headlines, venue/asset collisions ("SOL is now available on Coinbase"), symmetric M&A constructions, ambiguity traps for NEAR/ONE/ICP/APE/COIN/Meta/Oracle/Shell/Visa. 18 bear truth events; 22 are no-event traps. | Hand labels |
| `articles_truth.json` | Per truth cluster: member article ids, family, event type, stage, attributes, (asset, role) links; per annotated mention: surface form, span, truth asset-or-none; the 50-mention trap subset; the 40 role decisions; the intra/inter-cluster Jaccard margins asserted by the generator | Same |
| `market/seed_{1..5}/*.csv` | Daily OHLCV for the ~72 fixture assets + `idx:US` + `idx:CX`, 260 calendar days (events occupy days 70–210, leaving room for ±60-bar placebo displacement and 20-bar horizons). Seeded GBM: benchmark daily log-return σ = 1.0 % (US) / 3.0 % (CX), zero drift; asset return = own benchmark return + idiosyncratic noise (σ = **0.6 % equity / 1.6 % crypto**) + planted effects. Equity series skip weekends; crypto series are daily. ≈ 3.8 MB total. | GBM parameters + the planted-effect table |
| `market_truth.json` | The planted-effect table (below) and per-event, per-seed realized draws | By construction |
| `gapped/` | A tiny separate corpus: one equity asset missing 3 bars and one benchmark missing a bar on an entry date, used only by `test_fr10_benchmark_gap_excludes` and `test_fr9_available_bar_rules` — kept out of the main corpus so N stays constant across seeds | Hand-authored |
| `frame_cases.json` | 16 adversarial brief-render scenarios: 9 expected-VIOLATION (bare imperatives, footer tampering, emptied section, unattributed forbidden quote), 7 expected-PASS (forbidden substrings inside quoted+attributed evidence; "buyout"/"sell-off"/"buyer"/"sell-side" slot values) | Hand-authored expected verdicts |
| `generate_articles.py`, `generate_market.py` | Seeded generators; committed; re-run + diffed in CI (`--regen-check`, seconds). `--check-disjoint` enforces the DEV/VAL trigger-n-gram disjointness and the cluster Jaccard margins | — |

### Planted effects: announcement vs post-entry

This is the load-bearing fixture decision. Two effects are planted per event:

1. **Announcement jump**, applied on the bar dated `date(earliest article's
   published_at)` — the day the market actually reacted. Sizes are the
   literature's announcement CARs: M&A target +22 %, hack −15 %, crypto
   listing +12 %, delisting −18 %, earnings ±4 %, guidance +5 %/−6 %,
   regulatory ∓5 % (eq) / ∓8 % (cx). **A correct harness must never see
   this**, because FR-10's entry is strictly after `observed_at` ≥ that date.
   It is the strongest leak canary in the suite: entering one bar early
   multiplies mean |AR| by ≈ 6× and pushes M4 to ≈ 0.97.
2. **Post-entry drift**, applied from the entry bar onward — the only thing
   the product scores. The generator applies the same anchor rule as FR-10
   (first bar after the cluster's *latest* evidence publication).

| Type / role / stage | Kind | Post-entry mean | Bars | Notes |
|---|---|---|---|---|
| earnings beat / miss | * | +2.0 % / −2.0 % | 1–20 | PEAD-shaped, spread over the window |
| guidance raise / cut / withdraw | * | +1.5 % / −2.0 % / −2.5 % | 1–5 | |
| mna target, confirmed | * | +2.0 % | 1–20 | residual deal spread to completion |
| mna target, rumored | * | +3.0 % | 1–5 | **60 % fizzle** (no effect at all) |
| mna target, denied | * | −3.0 % | 1–5 | rumour-premium unwind |
| mna acquirer, confirmed / rumored | * | −0.6 % / −0.4 % | 1–5 | noise-floor by design |
| regulatory adverse / favorable | equity | −1.5 % / +1.2 % | 1–5 | |
| regulatory adverse / favorable | crypto | −3.0 % / +2.5 % | 1–5 | |
| listing | crypto / equity | +4.5 % / +0.5 % | 1–5 | equity = index add, noise floor |
| delisting | crypto / equity | −5.0 % / −2.0 % | 1–5 | |
| hack_exploit | crypto / equity | −4.5 % / −1.25 % | 1–5 | |
| **any event on a t3-only single-source cluster** | * | **50 % chance of no effect at all** | | low-tier sources report things that do not pan out — this is what `w_tier` is supposed to capture |

Each event's realized effect is drawn seeded with σ = 25 % of the mean
magnitude. Derivations are reproduced in comments in `generate_market.py`.

**Why the fixture noise is below realistic vol — stated honestly.** The
fixture is a *measurement instrument*, not a market simulator. At realistic
single-name idiosyncratic vol (≈ 1.2 % equity / 3.5 % crypto per bar) these
literature-scaled post-entry effects give a true hit rate of ≈ 0.65, and
separating a correct pipeline from a broken one at 4σ would need N ≈ 600
signals — outside the MVP envelope. Halving the noise preserves every
*relative* property the metrics test (direction recovery, rank order,
calibration ordering) while making the gates decisive at N ≈ 112. The
consequence: **M4 ≈ 0.82 on fixtures is evidence that the pipeline recovers
effects that are there — it is not a forecast of live hit rates**, and no
document, brief or scorecard in this project may present it as one.

Expected values for a correct implementation, derivable from the planted
z-scores (effect mean ÷ idiosyncratic σ at horizon): per-type hit
probabilities range from ≈ 0.93 (delisting, denied M&A, guidance cut) down to
≈ 0.65 (equity index adds, acquirer legs, the rumor population including
fizzles), giving **M4 ≈ 0.82, M5 ≈ 0.50, M6 ≈ 0.21**.

**Placebo contamination bound.** FR-10's re-draw rule avoids the windows of
events *known to the store*, so extraction misses (allowed by M1a ≥ 0.80)
leave a few planted windows unavoided. With ~108 planted events over ~72
assets and 260 bars, planted-window coverage per asset is ≈ 1.5 × 21 / 260 ≈
12 %; at ≥ 0.85 detection recall the unavoided share is ≈ 1.8 %, biasing the
placebo hit rate by ≤ 0.005 — an order of magnitude below the M7a gate.
`evals/metrics.py` additionally verifies realized placebo windows against
`market_truth.json` and reports the actual overlap count.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1a event macro-F1 (VAL) | one keyword per type, per article, no suppressors/clustering ("acquire"→mna, "hack"→hack_exploit) | ≈ 0.45 (fires on negations, historical refs, metaphors; duplicates triple-count) | **≥ 0.80** | Fixtures are constructed so each trap defeats exactly the naive shortcut; a correct pattern engine with suppressors scores ≈ 0.90 on VAL. 0.80 tolerates a handful of cross-family paraphrase misses. |
| M1a-floor min per-type F1 (VAL) | same | ≈ 0.20 | **≥ 0.65** | Stops one dead type hiding behind the mean. Coarse by necessity: the smallest VAL type has 8 events, where F1 quantizes to 0.93 / 0.86 / 0.77 / 0.67 at 1–4 misses. |
| M1a-transfer = M1a(DEV) − M1a(VAL) | a pattern set memorised from DEV | ≥ 0.40 | **≤ 0.10** | The direct overfit detector. A generalizing extractor scores within noise across families; a memorised one collapses on VAL. |
| M1b stage+attributes, conditional (VAL) | majority polarity per type, stage always `confirmed` | ≈ 0.52 | **≥ 0.85** | Attributes drive polarity → direction; stage drives confidence and the M&A override. Both are deterministic template fills; 0.85 allows edge-case regex misses only. |
| M1c stage+attributes, unconditional (VAL) | same | ≈ 0.30 | **≥ 0.70** | Closes M1b's selection bias: an implementation that misses exactly the hard events is scored on the easy survivors under M1b but not under M1c. 0.70 ≈ 0.85 × 0.82 achievable recall. |
| M2a link F1 (VAL) | link every alias occurrence, any case, no context rules | ≈ 0.70 (recall ≈ 0.95, precision ≈ 0.55) | **≥ 0.85** | Precision-first design (SCOPE D-3) must show up as measurably better linking overall, not only on traps. |
| M2b trap accuracy (50 traps) | same case-insensitive matcher | **22/50 = 0.44** (correct on all 22 true-link traps, wrong on all 28 no-link traps) | **≥ 0.90** | Traps are the product's credibility: 0.90 (≤ 5 misses of 50) requires case rules, ambiguity flags, context keywords and the all-caps guard all working. |
| M3 role accuracy (40 decisions) | "first linked asset in the trigger sentence takes the signal-bearing role" | **19/40 = 0.475** (right on the 15 active constructions and 4 of 8 venue decisions; wrong on the 13 passive/target-first, all 4 abstentions, and the 4 venue-is-an-asset cases) | **≥ 0.85** | A role error is a sign error on a high-magnitude prior, and a venue error fabricates a signal for an asset the article is not about. ≥ 0.85 (≤ 6 misses of 40) requires active/passive handling, the symmetric-construction fallback, and venue precedence. |
| M4 direction hit rate (mean of 5 market seeds) | always-bullish | ≈ 0.47; coin flip 0.50 | **≥ 0.72** | Planted effects make ≈ 0.82 achievable end-to-end; every extraction/link/role/polarity/timing error costs hits. Per-seed SE ≈ 0.036, so the 5-seed mean has SE ≈ 0.016: the gate sits ≈ 6.5 SE below the correct value and ≈ 13 SE above coin flip. Also fails a pipeline that forgets the benchmark leg. |
| M4-transfer = M4(DEV) − M4(VAL) | memorised patterns | ≥ 0.25 | **≤ 0.10** | Second overfit detector, downstream of extraction. |
| M5 information coefficient (mean of 5 seeds) | constant or shuffled scores | ≈ 0.00 | **≥ 0.35** | Requires magnitude/confidence structure, not just direction: a sign-only scorer with random magnitudes scores ≈ 0.22 on these fixtures; kind-differentiated priors and the post-entry bands are needed to clear 0.35 (correct ≈ 0.50). |
| M6 calibration separation (mean of 5 seeds) | confidence without stage/tier/corroboration modifiers (extraction_conf only) | ≈ 0.04 (buckets barely differ) | **≥ 0.12** | True separation ≈ 0.21 by construction (60 % rumor fizzles, 50 % t3 false reports, noise-floor minors in lo). Per-seed SE of the difference ≈ 0.095 → 5-seed SE ≈ 0.042, so the gate sits ≈ 2.1 SE below the correct value. This is the thinnest margin in the suite; the documented remedy if it proves flaky is **more market seeds, never a lower gate**. |
| M6-occupancy min bucket count | any degenerate confidence function | 0 | **≥ 20** | A confidence function that puts everything in one bucket makes M6 undefined; the gate must fail, not pass vacuously. |
| M7a placebo \|mean hit − 0.5\| (15 realizations) | leaky harness (entry at the publication-date bar; window anchored to planted dates) | ≥ 0.12 (systematic bias, survives averaging) | **≤ 0.035** | Per-realization SE ≈ 0.047 → 15-realization SE ≈ 0.012; the gate is ≈ 2.9 SE, so a clean harness passes reliably instead of playing a one-seed lottery, while the leak's bias fails by > 7 SE. |
| M7b placebo \|mean IC\| (15 realizations) | same | ≥ 0.15 | **≤ 0.06** | Spearman null SE ≈ 1/√112 ≈ 0.095 → 15-realization SE ≈ 0.024; gate ≈ 2.5 SE. Absolute value **of the mean**, so clean noise cancels and bias does not. |
| M8 framing verdict accuracy | templates without a frame check, naive substring matching | ≈ 0.84 (fails all 9 expected-violation cases and the "buyout"/"sell-off" briefs) | **= 1.0** | This is the finance safeguard (workspace rule): partial credit is meaningless — one imperative emitted, or one violation not caught, is a product failure. Deterministic templates make exactness fair. |
| M8-lexicon superset | a weakened shipped lexicon | fails | **must hold** | Keeps the grader's independence data-level, not just code-path-level. |
| G1 N_directional | shrink the denominator by resolving hard cases `unclear` | ≈ 70 | **≥ 100** | 115 are scored by construction; the floor allows the ≤ 5 % exclusion budget plus a handful of extraction misses, and nothing more. |
| G2 exclusion rate (real run) | same | ≈ 0.35 | **≤ 0.05** | Every ExclusionReason counted, including unclear-resolution abstentions, reported per reason in the scorecard. |

If fixture composition changes, every baseline number in this table must be
re-derived in the same commit (checked in review). This applies in particular
to scope valve V3 (fewer market seeds) and V4 (smaller per-type counts) in
SCOPE D-18.

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python newsalpha/evals/run.py     # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest newsalpha/                 # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads the fixture corpus, runs the full
  pipeline with offline adapters into an in-memory store, computes M1–M8 +
  G1/G2 + D0/D1, and prints the table with actual values, per-seed spreads,
  DEV/VAL splits, per-reason exclusion counts, and the announcement-bar
  capture rate; exits non-zero on any gate failure. `--regen-check` re-runs
  both generators, diffs their output against the committed fixtures, and
  re-runs `--check-disjoint` (also wired into CI; seconds).
- `evals/test_gates.py` — one pytest per gate, names referencing FR ids:
  `test_gate_m1a_event_typing_fr4`, `test_gate_m1a_transfer_fr4`,
  `test_gate_m1c_attributes_unconditional_fr4`,
  `test_gate_m2b_link_traps_fr5`, `test_gate_m3_roles_fr5`,
  `test_gate_m4_hit_rate_fr6_fr10`, `test_gate_m4_transfer_fr6`,
  `test_gate_m5_ic_fr6`, `test_gate_m6_calibration_fr6`,
  `test_gate_m6_bucket_occupancy_fr6`, `test_gate_m7_placebo_fr10`,
  `test_gate_m8_framing_fr7`, `test_gate_m8_lexicon_superset_fr7`,
  `test_gate_g1_n_directional_fr10`, `test_gate_g2_exclusion_rate_fr10`.
- Ordinary (non-gated) tests that cover the rest of the FR surface, named the
  same way: `test_fr2_exact_cluster_reconstruction`,
  `test_fr4_merge_stage_latest_wins`, `test_fr5_mna_symmetric_abstains`,
  `test_fr5_venue_precedence_coinbase`, `test_fr5_all_caps_guard`,
  `test_fr8_supersession_denial_outranks_rumor` (a rumor article and a
  three-days-later denial in separate clusters: the digest must show the
  denial's bearish signal and hide the rumor),
  `test_fr9_available_bar_rules`, `test_fr10_benchmark_gap_excludes`,
  `test_fr10_entry_strictly_after_publication`,
  `test_fr10_announcement_bar_not_captured`,
  `test_fr15_corroboration_emits_revision`, `test_fr15_key_continuity_alias`,
  `test_fr14_d0_determinism`, `test_fr14_d1_replay_equivalence`.
- `evals/metrics.py` — pure metric functions, including the frame-check
  reference implementation with its **embedded** forbidden lexicon.
- **Hermeticity, enforced not asserted in prose:**
  `test_hermetic_no_live_adapters` runs the full eval and asserts
  `feedparser`, `newsalpha.adapters.newsfeed_rss` and
  `newsalpha.adapters.marketdata_live` are absent from `sys.modules`; the
  eval `conftest.py` additionally monkeypatches `socket.socket` to raise.
- **Runtime budget:** text processing runs once over ~190 articles; the
  backtest runs 5 times (market seeds) plus 15 placebo realizations over
  ≈ 112 signals and ~94k bars. Whole suite ≤ 90 s. No long-running
  calibration step exists in this project.
