# TickerPress — EVALS

## 1. What this product lives or dies on

Two capabilities, per SCOPE.md:

- **H1 — Mention disambiguation (+ relevance ranking).** Accept real company
  appearances, reject the fruit/rainforest/travel-visa/meta-analysis traps,
  and rank a headline story above a passing list mention. A tool that pages
  you about apple harvests gets muted in a week; one that misses the Tesla
  recall is pointless. Precision and recall both matter, and the ambiguous
  cases are where the product earns its existence.
- **H2 — Syndication dedup + exactly-once delivery.** One wire story in five
  outlets must reach the user once — clustered correctly (never merging two
  *different* same-company stories) and never re-delivered across digests,
  alerts, re-ingests, or retries.

Everything else (feed parsing plumbing, CLI/API mechanics, watchlist CRUD)
is covered by ordinary pytest unit/integration tests, not eval gates.

All evals are hermetic: offline adapters only (`FixtureFeedSource`,
`FileNotifier`/stub notifiers), committed fixtures,
`FixedClock("2026-03-02T13:00:00Z")`, no network, no randomness anywhere in
the pipeline (the fixture *generator* is seeded; the engine is
randomness-free).

## 2. Ground truth, and how we avoid circularity

Three truth sources, none produced by the system under test:

- **Mention & tier truth is hand-authored.** Every base article is written
  by hand as natural news prose, and `labels/mentions.json` records, per
  (base article, company): present or absent, tier
  (`primary | context | passing`), and an `ambiguous` flag. Labels are
  editorial judgments made while writing the article — not derived from any
  matcher.
- **Dedup truth is by construction.** Syndicated variants are produced from
  base articles by a committed, seeded generator (`generate.py --seed
  4242`) whose group assignments *are* the truth: base + its variants =
  one story; everything else = different stories. Additionally,
  `labels/no_link.json` hand-flags 15 must-not-merge pairs — distinct
  stories deliberately written to be confusable (same company, same topic,
  same week).
- **Delivery truth is the scenario script.** Exactly-once assertions check
  ledger states the scenario makes fully predictable.

Circularity controls: the corpus is hand-written text, never generated from
the engine's cue lexicons; the generator's only text operations are
mechanical (headline swap from hand-written alternates, boilerplate append,
sentence drop, URL decoration) and it shares no code with the engine — its
only "linguistic" step is a plain regex surface scan over the alias list to
avoid dropping sentences that contain labeled surfaces. Because the global
cue/anti lexicons (`data/cues_corporate.txt` etc.) are product data
co-developed with these fixtures, the trap set is built to punish lexicon
gaming in both directions: trap articles that *contain* finance cue words in
non-company senses ("apple futures on the produce exchange", "visa fees
rose"), and positive articles with *zero* finance cues ("Apple opens
flagship store in Mumbai"). Stuffing the cue list cannot raise precision and
recall simultaneously.

Variant label inheritance is safe by constraint: alternate headlines are
hand-written per base article and must preserve each labeled company's
presence-in-title status; sentence drops never touch a sentence containing
any watchlist surface; boilerplate contains no watchlist surfaces. So every
variant inherits its base's (presence, tier) labels unchanged.

## 3. Metrics

Vocabulary: `A` = all archived fixture articles (102), `W` = the eval
watchlist (12 companies), pair universe `U = A × W`. `truth(a,c) ∈
{absent, passing, context, primary}` from labels (variants inherit).
`pred(a,c) = 1` iff an Appearance row exists (≥1 accepted mention).
`rel(a,c)` = the stored article-level relevance.

### M1 — mention_f1 (H1)

```
TP = #{(a,c) : truth ≠ absent ∧ pred = 1}
FP = #{(a,c) : truth = absent ∧ pred = 1}
FN = #{(a,c) : truth ≠ absent ∧ pred = 0}
precision = TP/(TP+FP)    recall = TP/(TP+FN)
M1 = 2·precision·recall / (precision + recall)
```

Pair-level F1 is the standard NER/entity-linking evaluation convention
(CoNLL-2003 shared task lineage), applied at article granularity because
delivery is per-article, not per-token.

### M1_amb — ambiguous-subset F1 (H1, the headline gate)

Same formula restricted to the ambiguous pair subset
`U_amb = {(a,c) : article a contains ≥1 weak surface of c}`, where "weak
surface" is determined by a label-independent regex surface scan (weak
aliases + word-collision tickers from the committed common-words list) —
computable without running the engine, so the subset cannot shift when the
engine changes. This subset contains every fruit/rainforest/visa trap and
every weak-alias positive: it is exactly the hard part.

### M2 — dedup_pairwise (H2)

Pairwise clustering scores (Bagga & Baldwin 1998, the pairwise coreference
convention). Over all unordered article pairs:

```
same_true(a,b)  = 1 iff a,b are in the same generator group
same_pred(a,b)  = 1 iff story_id(a) = story_id(b)
P_pair = #{same_pred ∧ same_true} / #{same_pred}
R_pair = #{same_pred ∧ same_true} / #{same_true}          (72 true pairs)
M2_f1  = 2·P_pair·R_pair/(P_pair+R_pair)
M2_trap = #{(a,b) ∈ no_link.json : same_pred(a,b)}         (15 flagged pairs)
```

### M3 — relevance_ordering (H1, ranking facet)

Tier-separation accuracy (a pairwise-AUC-style score). Let

```
O = {((a,c),(b,c)) : pred(a,c)=pred(b,c)=1 ∧ tier(a,c) > tier(b,c)}
    with tier order primary > context > passing, same company c
M3 = ( #{pairs with rel(a,c) > rel(b,c)} + 0.5·#{rel(a,c) = rel(b,c)} ) / |O|
```

Pairs where either appearance is missing are excluded here (they are
already punished by M1). The fixture set guarantees |O| ≥ 60 and includes
≥12 designed count-vs-placement traps (see §4).

### M4 — delivery_exactly_once (H2)

A scripted scenario over the full fixture corpus; `M4 = 1.0` iff every
check passes, else `0.0` (each failing check is named in the scorecard):

1. Fresh store → ingest all feeds (FixedClock t₀) → `digest run
   --channel file` produces one Delivery whose item set equals the
   expected selection (computed from labels + thresholds) — non-empty.
2. Re-ingest the same fixture bytes → 0 new articles; `digest run` again →
   no Delivery row is created, no notifier call.
3. Alert flow: TSLA is configured `mode=both` in `watchlist.json`; with
   `alert_channel=file`, its relevance-≥60 story produced exactly one
   alert during step 1's ingest, and that story is absent from TSLA's
   section of the step-1 file-channel digest (shared ledger).
4. Failure release: with a notifier stub that raises, `digest run` records
   status `failed` (items uncounted); the next `digest run` with a working
   notifier delivers exactly those stories once.
5. Constraint proof: inserting a second `counted=1` item for an already
   delivered (channel, company, story) raises `IntegrityError` (the
   partial unique index is live).

### M5 — determinism (FR-16)

Two end-to-end runs from empty stores with identical inputs and
`FixedClock`: dumps of `articles` (incl. `story_id`), `stories`,
`mentions`, `appearances`, and every Delivery `body_text` must be
byte-identical; `explain` output for three designated articles must be
byte-identical.

```
M5 = 1.0 if all identity checks pass else 0.0
```

### Report-only metrics (printed, not gated)

- Per-company precision/recall table (localizes a broken alias config).
- Score histograms: accepted vs rejected candidate scores; relevance by
  hand tier (visual check of tier separation).
- Cluster-size distribution vs true group sizes.
- `naive_deltas` — each gated metric minus its live-computed naive value.

## 4. Fixture strategy

Everything lives in `evals/fixtures/`, deterministic and committed. All
articles are synthetic, hand-written in wire-service register about
invented events; company names/tickers are real (that is the point of the
product) but every headline, quote, number, and URL is invented, and outlet
names are fictional (`wireone.example.com`, …). No scraped text.

```
evals/fixtures/
  watchlist.json          # 12 companies + aliases + per-company context/anti terms
  corpus/articles.json    # 62 hand-authored base articles: id, title, alt_titles[],
                          #   sentences[], dateline, feed assignment, url, guid style
  labels/
    mentions.json         # per (base_id, ticker): present, tier, ambiguous
    no_link.json          # 15 must-not-merge article-id pairs
  feeds/
    wire_one.xml  wire_two.xml  biz_daily.xml          # RSS 2.0
    tech_ledger.xml  market_minute.xml                 # RSS 2.0
    global_desk.atom.xml                               # Atom (RFC 4287)
  generate.py             # seeded (--seed 4242): builds variants, distributes all
                          #   102 items into the six feed XML files
  expected.json           # generator-recorded counts (informational only —
                          #   gates always assert live-computed values)
```

**Eval watchlist (12 companies)** — chosen so every ambiguity class is
exercised: `AAPL` Apple (fruit), `AMZN` Amazon (rainforest), `META` Meta
(meta-analysis + `META` common-word ticker), `TSLA` Tesla (Nikola Tesla),
`F` Ford (Harrison Ford + 1-char ticker), `V` Visa (travel documents +
1-char ticker), `TGT` Target ("price target", "target audience"), `ORCL`
Oracle (Delphi), `SHEL` Shell (shell scripts, seashells, "shell company"),
`CAT` Caterpillar (insect, CT-scan, common-word ticker), `ALL` Allstate
(the word "all", all-caps headlines), `GOOGL` Alphabet (the alphabet).
Per-company `context_terms`/`anti_terms` are ≤8 entries each — realistic
one-minute user tuning, committed as part of the fixture watchlist.

**Base corpus (62 hand-authored articles):**

- 22 *positive-unambiguous*: finance/corporate news with strong surfaces
  (legal names, `(NASDAQ: AAPL)`, cashtags), several multi-company
  (earnings roundups, a Visa/Mastercard-style settlement piece).
- 14 *positive-ambiguous*: the company appears only via weak surfaces —
  including cue-free corporate news ("Apple opens flagship store in
  Mumbai"), weak-alias-only headlines ("Meta fined by EU privacy
  regulator"), and word-collision tickers in legitimate use ("ALL" in a
  markets table paragraph).
- 16 *negative traps*: fruit-harvest report (with commodity-futures
  language — finance cues in the wrong sense), Amazon-basin deforestation,
  US travel-visa policy ("visa fees rose"), Harrison Ford film review,
  shell-scripting tutorial, "shell companies" money-laundering piece
  (SHEL must not match), marketing piece on target audiences, an equity
  note using "price target" heavily, Oracle-of-Delphi archaeology,
  Nikola Tesla museum story, meta-analysis health study, caterpillar
  ecology, "CAT scan" medical article, literacy piece on the alphabet,
  and a Fed headline "FED SAYS ALL OPTIONS REMAIN OPEN" (all-caps run).
- 10 *negative-clean*: ordinary news containing no watchlist surface at
  all — any reported pair is a false positive.

This yields ≈47 positive (article, company) pairs among bases; with
variant inheritance, ≈85 positive pairs over the 102-item corpus, ≈34 of
them in `U_amb`. Tier labels are distributed so |O| ≥ 60, including the
count-vs-placement traps: a market-roundup naming Apple three times in the
body (labeled `context`), a list mention "Apple, Amazon and Tesla also
fell" (labeled `passing`, 1 mention each), and a short `primary` piece
whose only mention is the headline — a mention-count ranker misorders all
three.

**Syndication variants (40, from 20 groups):** 8 groups of size 2, 8 of
size 3, 4 of size 5 (base + variants) ⇒ 72 true same-story pairs.
Generator ops per variant (seeded, recorded in `expected.json`): pick an
alternate hand-written headline; prepend an outlet prefix ("Biz Daily |");
append outlet boilerplate (fixed template, no watchlist surfaces); drop
0–2 surface-free sentences; decorate the URL (different host, `utm_*`
noise); vary guid scheme (one feed omits guids entirely, exercising the
link+title-hash fallback); jitter `pubDate` by +0–36 h. Ten same-story
pairs share an identical canonical URL across feeds (the naive baseline's
only recall). Variants are placed on different feeds than their base. The
15 `no_link.json` pairs include: earnings *preview* vs earnings *results*
(same company, same week), two different same-day Tesla stories, and two
different outlets' original (non-syndicated) takes on the same event —
below τ by construction but topically adjacent.

**Feed files** are the generator's output: valid RSS 2.0 / Atom documents
with fixed `pubDate`s in Feb–Mar 2026, all inside the ±7-day dedup window
relative to each other where groups require it. Regenerating
(`python evals/fixtures/generate.py --seed 4242`) is a reviewed change.

**Ground truth summary:** M1/M1_amb/M3 truth = hand labels; M2 truth =
generator groups + hand no-link pairs; M4 truth = scenario script; M5
truth = identity. No metric's truth is produced by the system under test.

## 5. Baselines and gates

The naive baseline — implemented *live* in `evals/metrics.py` (~40 lines)
— is the honest afternoon script: whole-word alias matching
(case-insensitive for names/nicknames, exact-case for tickers, cashtags
literal), dedup by identical canonical URL, relevance = accepted mention
count. `run.py` prints its actual live-computed numbers alongside the
gates.

| Metric | Naive baseline | Why |
|---|---|---|
| M1 | ≈ 0.72–0.80 | recall 1.0, but every trap article fires, plus incidental hits: lowercase "price target"/"shell company"/"meta-analysis" match TGT/SHEL/META name aliases; "ALL"/"CAT" fire in caps headlines — ≈40–60 FP pairs against 85 TPs |
| M1_amb | ≈ 0.50–0.60 | all FPs live in the ambiguous subset by construction; subset precision ≈ 0.4 |
| M2_f1 | ≈ 0.24 | only the 10 identical-URL pairs of 72 recovered (P=1.0, R≈0.14) |
| M3 | ≈ 0.65–0.75 | mention count misorders every designed count-vs-placement trap |
| M4 | 0.0 | no ledger ⇒ step-2/3/4 checks fail |
| M5 | — | not meaningful for the baseline |

Gates (asserted on live-computed values):

| Gate | Threshold | Rationale |
|---|---|---|
| M1 mention_f1 | ≥ 0.92 | Strong-surface positives are near-free; the evidence scorer must then hold precision on traps without dropping weak positives. Naive sits ≈ 0.76 — the ~16-point margin *is* the disambiguation engine. Not higher: a few fixtures are written to be genuinely borderline (cue-free weak mentions with no strong coreference) and may legitimately fall either side. |
| M1_amb ambiguous F1 | ≥ 0.85 | The headline gate — scored only where disambiguation actually decides. Naive ≈ 0.55. Also blocks the trivial precision play: rejecting all weak candidates loses every weak-only positive (≈20 of the ≈34 ambiguous pairs — the rest also carry strong surfaces), dropping subset recall below 0.5 and failing the gate loudly. |
| M2 P_pair | ≥ 0.95 | Over-merging destroys trust (two different stories delivered as one) and silently suppresses deliveries via the ledger; τ=0.60 on 3-shingles leaves margin. |
| M2 R_pair | ≥ 0.85 | Heavily edited copies (headline swap + boilerplate + 2 dropped sentences) must still cluster; a few extreme edits may fall below τ — that costs duplicates in a digest, annoying but honest. Naive R ≈ 0.14. |
| M2_trap merged no-link pairs | = 0 | Hand-vetted distinct-story pairs may never merge. Zero, not small: any violation is a threshold/shingling design bug, not noise. |
| M3 relevance_ordering | ≥ 0.90 | Placement-weighted scoring should order essentially all tier pairs; ties among identically-shaped articles cost half-credit and justify the 10% slack. Naive ≈ 0.70. |
| M4 delivery_exactly_once | = 1.0 | The product's promise ("send me a link" — once). Any failing step is a broken invariant, not a quality regression. |
| M5 determinism | = 1.0 | CONVENTIONS.md hermeticity; what makes every other number trustworthy. |

Anti-gaming note: M1 alone is gamed by accepting everything (precision
dies), M1_amb by rejecting all weak candidates (its recall dies) — the
pair pins both directions. M2 P_pair alone is gamed by never clustering —
R_pair blocks it; R_pair alone by clustering everything — P_pair and
M2_trap block it. M3 cannot be gamed by constant scores (ties earn 0.5 →
0.5 < 0.90).

## 6. How the suite runs

Mirrors `orbit-backend/evals/` and the sibling projects:

- **`evals/metrics.py`** — `MetricResult(name, value, gate, passed,
  detail)`, `EvalReport`, pair-universe construction, the label-independent
  weak-surface scan for `U_amb`, pairwise clustering scores, the naive
  baseline implementation.
- **`evals/run.py`** — `python -m` runnable with zero configuration and no
  network. Builds a fresh store (tmp SQLite), loads `watchlist.json`,
  registers the six fixture feeds (`file://` URLs → `FixtureFeedSource`),
  runs ingest + digest under `FixedClock("2026-03-02T13:00:00Z")` with
  offline notifiers, executes the M4 scenario and the M5 double-run,
  computes all metrics, and prints a scorecard — one line per metric with
  value, gate, PASS/FAIL, and detail (per-company table, failing pair
  examples, naive deltas) — then a JSON summary. Exit code 0 iff all gates
  pass.
- **`evals/test_gates.py`** — one pytest test per gate row in §5, names
  carrying FR ids: `test_fr6_mention_f1_gate`,
  `test_fr6_ambiguous_f1_gate`, `test_fr7_dedup_precision_gate`,
  `test_fr7_dedup_recall_gate`, `test_fr7_no_link_zero_merges`,
  `test_fr8_relevance_ordering_gate`, `test_fr11_exactly_once_gate`,
  `test_fr16_determinism_gate`; plus `test_eval_runner_passes` executing
  `run.py` end-to-end and asserting exit code 0. `uv run pytest
  tickerpress/` therefore fails on any quality regression.

Runtime budget: 102 items × 12 companies, pure-string pipeline, exact
Jaccard over a ≤102-article window — full suite (including the double run)
well under 15 s on a laptop; runs in every CI invocation.

## 7. FR → test/eval mapping

| FR | Covered by |
|---|---|
| FR-1 | `tests/test_watchlist.py` (ticker validation, generated aliases, suffix strip, duplicate rejection) |
| FR-2 | `tests/test_feeds.py` (FetchResult statuses, per-feed error isolation, conditional-GET state on a stubbed live adapter) |
| FR-3 | `tests/test_feedparse.py` (RSS/Atom subset, guid fallback, RFC 822/3339 dates, malformed-item skip; normalization + offset stability) |
| FR-4 | `tests/test_archive.py` (feed+guid uniqueness, canonical URL table-driven cases, immutability, first-version-wins) |
| FR-5 | **M1, M1_amb** + `tests/test_detect.py` (case rules per kind, 1-char ticker never bare, common-word demotion, cashtag/exchange patterns, all-caps runs) |
| FR-6 | **M1, M1_amb** + `tests/test_disambig.py` (each feature in isolation on constructed snippets; the SCOPE worked examples as named cases; θ boundary) |
| FR-7 | **M2 (P/R), M2_trap** + `tests/test_dedup.py` (shingle math on known strings, window edges, fast paths, representative re-pointing) |
| FR-8 | **M3** + `tests/test_relevance.py` (component truth tables, formula values, story-level max rollup) |
| FR-9 | `tests/test_digest.py` (selection filters, ordering law, template render golden file, quiet-when-empty) |
| FR-10 | **M4 step 3** + `tests/test_alerts.py` (threshold boundary, mode matrix, ledger sharing) |
| FR-11 | **M4, M5** + `tests/test_ledger.py` (lifecycle transactions, failure release, partial-index violation) |
| FR-12 | `tests/test_notifiers.py` (console/file byte-identity with body_text; live adapters: env-gating + payload shape against stubs) |
| FR-13 | `tests/test_explain.py` (feature completeness, rejected candidates shown, read-only — no recomputation) |
| FR-14 | `tests/test_api.py` (FastAPI TestClient, error-code mapping, 204-on-empty digest) |
| FR-15 | `tests/test_cli.py` (CliRunner, exit codes, `--json`, `--now` injection) |
| FR-16 | **M5** |
