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

Every gated metric reads its truth from a **committed file under
`evals/fixtures/labels/`**. Nothing gated derives truth from the system
under test, and nothing gated derives truth from a file the engine can also
edit (that was the loophole this section previously had).

| Truth artifact | Produced by | Consumed by |
|---|---|---|
| `labels/mentions.json` | hand-authored while writing each article | M1, M1_amb*, M3, expected-digest derivation |
| `labels/weak_surfaces.json` | frozen snapshot of the weak-surface inventory at fixture-authoring time | the `U_amb` subset definition |
| `labels/story_groups.json` | emitted once by `generate.py --seed 4242`, hand-reviewed, committed | M2 `same_true` |
| `labels/no_link.json` | 15 hand-flagged must-not-merge pairs, each tagged `near` or `far` | M2_trap, M2_near |
| `labels/expected_digest.json` | derived by `derive_expected.py` from labels + watchlist, hand-reviewed, committed | M4 step 1 |
| `lexicon_manifest.json` | sha256 of each frozen `data/` lexicon | M6_lex |
| `golden/` | sha256 of the step-1 digest body and of the article/mention dump | M5 |

**Label schema (`mentions.json`).** Per (base article id, ticker):

```json
{"base_id": 31, "ticker": "AAPL", "present": true, "tier": "primary",
 "ambiguous": true, "weak_only": true, "cue_free": false,
 "mention_count": 3, "title_hit": true, "lede_hit": true}
```

`tier ∈ {primary, context, passing}` (only when `present`); `ambiguous` =
the company is reachable in this article only through surfaces that require
judgement; `weak_only` = no strong surface for this company anywhere in the
article; `cue_free` = no global corporate cue lemma occurs in the article
(so acceptance must come from per-company `context_terms`); `mention_count`,
`title_hit`, `lede_hit` are hand-counted placement facts. These three are
what make the expected digest selection derivable without running the engine
(§ M4).

**Variant label inheritance is safe by constraint.** The generator's only
text operations are mechanical (headline swap from hand-written alternates,
outlet prefix, boilerplate append, sentence drop, URL decoration, pubDate
jitter, guid scheme change) and it shares no code with the engine — its only
"linguistic" step is a plain regex surface scan over the alias list. The
constraints:

- alternate headlines preserve, for every labeled company, both
  presence-in-title **and the number of surfaces in the title**;
- sentence drops never touch a sentence containing any watchlist surface;
- appended boilerplate and decorated URLs contain no watchlist surface.

Therefore every variant inherits its base's `present`, `tier`,
`mention_count`, `title_hit`, and `lede_hit` unchanged. A fixture self-check
(§6) re-runs the generator and fails if the committed feeds drift from it.

**Circularity and gaming controls.** The corpus is hand-written text, never
generated from the engine's cue lexicons. Three specific channels are closed:

1. *Lexicon memorization.* `data/cues_corporate.txt` and `data/cues_anti.txt`
   are authored **before** the corpus (SCOPE D19), hashed into
   `lexicon_manifest.json`, and gated by **M6_lex**: no lexicon entry may
   occur in exactly one fixture base article. Single-article entries are the
   signature of encoding a fixture's identity into the lexicon ("Mumbai",
   "flagship" as cues; "frost", "statin" as anti-cues), and they are the one
   thing that raises precision and recall simultaneously. *The earlier claim
   that "stuffing the cue list cannot raise precision and recall
   simultaneously" was false and is withdrawn* — it holds only for generic
   vocabulary, which is exactly what M6_lex now enforces.
2. *Per-company term memorization.* `context_terms`/`anti_terms` are capped
   at 8 per company (realistic one-minute user tuning) and, additionally,
   **M1_amb_abl** re-runs the whole ambiguous gate with every company's term
   lists emptied. The general mechanism (coreference, case, global cues,
   hyphen/all-caps structure) must carry a stated floor on its own.
3. *Subset shaping.* `U_amb` is defined from a frozen file, not from
   `data/common_words.txt`, so editing engine data cannot move traps out of
   the hard subset (see M1_amb).

The trap set additionally punishes bag-of-cues shortcuts in both directions:
trap articles that *contain* finance cue words in non-company senses ("apple
futures on the produce exchange", "visa fees rose"), and positive articles
with *zero* finance cues ("Apple opens flagship store in Mumbai").

## 3. Metrics

Vocabulary: `A` = all archived fixture articles (108), `A_base` = the 68
hand-authored base articles, `W` = the eval watchlist (12 companies), pair
universe `U = A × W`. `truth(a,c) ∈ {absent, passing, context, primary}` from
labels (variants inherit). `pred(a,c) = 1` iff an Appearance row exists (≥1
accepted mention). `rel(a,c)` = the stored article-level relevance.

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

Same formula restricted to `U_amb`. **`U_amb` is defined by a committed
file, not by engine data:** `labels/weak_surfaces.json` is a frozen
inventory, snapshotted at fixture-authoring time, of every weak surface in
the eval watchlist —

```json
{"AAPL": ["Apple"], "META": ["Meta", "META"], "CAT": ["Caterpillar", "CAT"],
 "ALL": ["Allstate", "ALL"], "V": [], "F": ["Ford"], "...": []}
```

— i.e. every weak alias plus every ticker whose lowercase form was in
`data/common_words.txt` when the fixtures were written. `U_amb = {(a,c) :
article a's normalized text contains ≥1 surface from weak_surfaces[c],
matched by a plain regex whole-token scan}`. Because the file is frozen,
editing `common_words.txt` (which *does* change engine behavior) can no
longer move a trap out of the hard subset and into the forgiving M1
denominator. `run.py` prints a **report-only diff** between the frozen
inventory and the live `common_words.txt`, so intentional lexicon evolution
is visible.

This subset contains every fruit/rainforest/visa trap and every weak-alias
positive: it is exactly the hard part.

### M1_amb_base — ambiguous-subset F1 over base articles only (H1)

Identical formula with `A` replaced by `A_base`. Rationale (a real weakness
of the 108-item number): variant inheritance re-counts the same
disambiguation decision on near-identical text up to 5×, so a failure on a
size-5 group's base costs 5 FNs while the same error on a singleton costs 1.
The base-level metric is the unweighted view — one vote per independent
authoring decision — and is gated separately so group-size weighting cannot
flatter or punish the engine. Base-level M1 is printed report-only alongside.

### M1_amb_abl — ablated ambiguous F1 (H1, anti-memorization)

`M1_amb` recomputed on a run where **every company's `context_terms` and
`anti_terms` are set to `[]`** (the watchlist is otherwise identical; the
engine, lexicons, and fixtures are untouched). This isolates the general
mechanism from per-company term lists. It is expected to be lower than
M1_amb — at most 5 fixture positives are permitted to be `cue_free` and
therefore to depend on a context term (§4) — but it must clear its floor,
which is what makes "the scorer generalizes" a measured claim rather than an
assertion.

### M2 — dedup_pairwise (H2)

Pair-counting (pairwise link) clustering scores — the pair-decision
convention used throughout coreference and clustering evaluation; note this
is *not* B-CUBED (Bagga & Baldwin 1998), which counts per-mention. Over all
unordered article pairs:

```
same_true(a,b)  = 1 iff group_of(a) = group_of(b)      # labels/story_groups.json
same_pred(a,b)  = 1 iff story_id(a) = story_id(b)
P_pair = #{same_pred ∧ same_true} / #{same_pred}        (0.0 by definition if
                                                         #{same_pred} = 0)
R_pair = #{same_pred ∧ same_true} / #{same_true}        (72 true pairs)
M2_f1  = 2·P_pair·R_pair/(P_pair+R_pair)                (0.0 if P+R = 0)
M2_trap  = #{(a,b) ∈ no_link.json : same_pred(a,b)}     (15 flagged pairs)
M2_near  = #{(a,b) ∈ no_link.json with band="near" : J(a,b) ≥ 0.40}
```

`same_true` reads `labels/story_groups.json` — a committed first-class truth
file, not `expected.json` (which stays informational). `M2_near` exists
because "must-not-merge pairs, all far below τ" is a vacuous gate: five of
the fifteen no-link pairs are authored to sit in the J ∈ [0.40, 0.60) band
(same press release quoted by two genuinely different stories), and the gate
requires the engine to actually compute them near the boundary and still keep
them apart. `run.py` prints the observed J for all 15 pairs.

### M3 — relevance_ordering (H1, ranking facet)

Tier-separation accuracy (a pairwise-AUC-style score). Let

```
O = {((a,c),(b,c)) : pred(a,c)=pred(b,c)=1 ∧ tier(a,c) > tier(b,c)}
    with tier order primary > context > passing, same company c
M3 = ( #{pairs with rel(a,c) > rel(b,c)} + 0.5·#{rel(a,c) = rel(b,c)} ) / |O|
```

Conditioning on predicted appearances means M3's denominator shrinks when
detection misses things — and the M1 gates tolerate ~12 misses, which would
be enough to curate the 12 designed count-vs-placement traps out of `O` and
score ~1.0 on the easy remainder. Two counter-measures:

- **M3_cov (gated).** `run.py` asserts the **observed** `|O| ≥ 55` and that
  all **12 designated count-vs-placement trap pairs** (listed by
  (base_id, ticker) pair in `labels/mentions.json` via a
  `"ordering_trap_group": <id>` field) are present in `O`. If either fails,
  M3 is reported as FAILED regardless of its value.
- **M3_strict (report-only).** The same score computed over *truth* pairs
  (both tiers labeled present) where a missing predicted appearance scores 0.
  Printing it makes the coupling between detection recall and ranking visible
  instead of exploitable.

### M4 — delivery_exactly_once (H2)

A scripted scenario over the full fixture corpus; `M4 = 1.0` iff every
check passes, else `0.0` (each failing check is named in the scorecard).

**The expected step-1 selection is a committed fixture**,
`labels/expected_digest.json` — a list of `{"ticker", "group_id"}` pairs —
derived by `evals/fixtures/derive_expected.py`, which imports nothing from
`src/tickerpress` and reads only `mentions.json`, `story_groups.json`, and
`watchlist.json`:

```
label_relevance(base, ticker) = round_half_up(100·(0.50·title_hit + 0.25·lede_hit
                                                   + 0.25·min(1, mention_count/4)))
label_story_relevance(group, ticker) = max over the group's members
expected = {(ticker, group) : mode(ticker) ∈ {digest, both}
                            ∧ label_story_relevance ≥ min_relevance(ticker)
                            ∧ (ticker, group) not already alerted on this channel}
```

For this to be well-defined the corpus obeys a **band rule** (§4): no
labeled story's `label_story_relevance` for a watching company may fall
within ±6 points of that company's `min_relevance` or `alert_min_relevance`.
Given the attainable relevance values (DATA_MODEL §2.8) the rule forbids
`{19, 25}` around a floor of 20, `{44, 50, 56}` around 50, and `{56, 63}`
around 60 — so the nearest permitted value is ≥7 points from any floor,
every labeled story is decidably in or out, and the selection is stable
under a ±1-mention detection difference. `test_gates.py` asserts (a) the committed
file equals a fresh derivation (drift check) and (b) the engine's step-1
item set equals the committed file.

Checks:

1. Fresh store → ingest all feeds (FixedClock t₀) → `digest run --channel
   file` produces exactly one Delivery whose item set, mapped from story ids
   to group ids, equals `labels/expected_digest.json` — and is non-empty.
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
6. Dry run is inert: `digest run --dry-run` on a fresh store renders a
   non-empty body and creates **zero** `deliveries`/`delivery_items` rows;
   a subsequent real `digest run` still delivers the full expected set.

### M5 — determinism (FR-16)

Two end-to-end runs from empty stores with identical inputs and
`FixedClock`. **The second run executes in a subprocess launched with a
different explicit `PYTHONHASHSEED`** (run A: `PYTHONHASHSEED=0`, run B:
`PYTHONHASHSEED=12345`), and the comparison happens across the process
boundary on serialized dumps. A same-process double run cannot see
hash-seed-dependent iteration order — the most common class of Python
nondeterminism, and precisely what M5 exists to catch.

Compared artifacts: JSON dumps of `articles` (incl. `story_id`), `stories`,
`mentions`, `appearances`, every Delivery `body_text`, and `explain` output
for three designated articles. In addition, the sha256 of the step-1 digest
`body_text` and of the article+mention dump are committed under
`evals/fixtures/golden/` and asserted, which also catches unintended fixture
or template drift between commits (and pins the D14 footer).

```
M5 = 1.0 if all identity checks (cross-process + golden hashes) pass else 0.0
```

### M6_lex — lexicon specificity & freeze (H1, anti-memorization)

```
manifest_ok  = sha256(f) == lexicon_manifest[f] for every frozen data/ lexicon
df(e)        = #{base articles whose normalized text contains entry e}
singletons   = #{e ∈ cues_corporate ∪ cues_anti : df(e) = 1}
M6_lex = 1.0 if manifest_ok ∧ singletons = 0 else 0.0
```

Entries with `df(e) = 0` are fine — general vocabulary the corpus happens not
to exercise. Entries with `df(e) = 1` are the memorization signature and must
be removed or generalized; if the entry is genuinely useful, the corpus (a
reviewed change) must exercise it at least twice. A lexicon edit that fails
`manifest_ok` is not a test failure to be worked around: it means the
manifest must be updated deliberately, and M6_lex/M1_amb_abl re-run.
`run.py` lists the offending entries.

### Report-only outputs (printed, not gated)

- Per-company precision/recall table (localizes a broken alias config).
- Base-level M1 alongside the 108-item M1 (group-size weighting made visible).
- M3_strict (see M3).
- Score histograms: accepted vs rejected candidate scores; relevance by
  hand tier.
- Cluster-size distribution vs true group sizes; observed J for all 15
  no-link pairs.
- Frozen-`weak_surfaces.json` vs live-`common_words.txt` diff.
- Component agreement: % of detected positive pairs whose stored
  (`title_hit`, `lede_hit`) match the labels, and mean |Δ mention_count|.
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
  corpus/articles.json    # 68 hand-authored base articles: id, title, alt_titles[],
                          #   sentences[], dateline, feed assignment, url, guid style
  labels/
    mentions.json         # per (base_id, ticker): present, tier, ambiguous, weak_only,
                          #   cue_free, mention_count, title_hit, lede_hit,
                          #   ordering_trap_group?
    weak_surfaces.json    # frozen weak-surface inventory → defines U_amb
    story_groups.json     # article id → group id (generator output, reviewed) → M2 truth
    no_link.json          # 15 must-not-merge pairs, each band: "near" | "far"
    expected_digest.json  # derived step-1 digest selection (ticker, group_id)
  feeds/
    wire_one.xml  wire_two.xml  biz_daily.xml          # RSS 2.0
    tech_ledger.xml  market_minute.xml                 # RSS 2.0
    global_desk.atom.xml                               # Atom (RFC 4287)
  golden/
    digest_body.sha256  archive_dump.sha256            # M5 golden hashes
  lexicon_manifest.json   # sha256 of each frozen data/ lexicon (SCOPE D19)
  generate.py             # seeded (--seed 4242): builds variants, writes the six feed
                          #   XML files AND labels/story_groups.json
  derive_expected.py      # labels + watchlist → labels/expected_digest.json (no engine imports)
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
one-minute user tuning, committed as part of the fixture watchlist. Delivery
config: all companies `min_relevance = 20` except `GOOGL` at 50 (so the
non-default path is exercised); `TSLA` is `mode = both` with
`alert_min_relevance = 60`; one company is `mode = mute` to exercise
suppression.

**Base corpus (68 hand-authored articles):**

- 22 *positive-unambiguous*: finance/corporate news with strong surfaces
  (legal names, `(NASDAQ: AAPL)`, cashtags), several multi-company
  (earnings roundups, a Visa/Mastercard-style settlement piece).
- 20 *positive-ambiguous*: the company appears only via weak surfaces —
  including cue-free corporate news ("Apple opens flagship store in
  Mumbai"), weak-alias-only headlines ("Meta fined by EU privacy
  regulator"), and word-collision tickers in legitimate use ("ALL" in a
  markets table paragraph). **At most 5 of these may be labeled
  `cue_free`** (acceptance depending on a per-company context term); the
  rest must be reachable through coreference, case, and global cues, which
  is what makes the M1_amb_abl floor attainable and meaningful.
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

**Syndication variants (40, from 20 groups):** 8 groups of size 2, 8 of
size 3, 4 of size 5 (base + variants) ⇒ **72 true same-story pairs** and
108 archived items. Generator ops per variant (seeded, recorded in
`expected.json`): pick an alternate hand-written headline; prepend an outlet
prefix ("Biz Daily |"); append outlet boilerplate (fixed template, no
watchlist surfaces); drop 0–2 surface-free sentences; decorate the URL
(different host, `utm_*` noise); vary guid scheme (one feed omits guids
entirely, exercising the link+title-hash fallback); jitter `pubDate` by
+0–36 h. Ten same-story pairs share an identical canonical URL across feeds
(the naive baseline's only recall). Variants are placed on different feeds
than their base. The 15 `no_link.json` pairs include earnings *preview* vs
earnings *results* (same company, same week), two different same-day Tesla
stories, and two outlets' independent takes on one event; **5 of them are
authored into the J ∈ [0.40, 0.60) band** (`band: "near"`) by sharing a
quoted press-release paragraph, so M2_trap binds close to τ rather than
trivially.

**Relevance band rule (makes M4 decidable).** No labeled story's
label-derived story relevance for a watching company may land within ±6 of
that company's `min_relevance` or `alert_min_relevance`. With floors 20, 50,
60 this forbids story relevances in {19,25} (min 20), {44,50,56} (GOOGL's
min 50), and {56,63} (TSLA's alert 60). Authors pick placement/count
combinations accordingly; `derive_expected.py` asserts the rule and fails
fixture generation otherwise.

**Asserted fixture floors.** `run.py` hard-asserts these *before* computing
any metric; a violation is an eval error, not a gate failure, because it
means the corpus no longer supports the gates:

| Quantity | Floor |
|---|---|
| archived items / base articles | = 108 / = 68 |
| positive pairs (truth ≠ absent) over `A` | ≥ 88 |
| positive pairs over `A_base` | ≥ 50 |
| \|U_amb\| / \|U_amb ∩ A_base\| | ≥ 58 / ≥ 40 |
| positive pairs in U_amb | ≥ 34, of which ≥ 26 `weak_only` |
| negative (trap) pairs in U_amb | ≥ 18 |
| `cue_free` positives | ≤ 5 |
| true same-story pairs | = 72 |
| no-link pairs / of band `near` | = 15 / = 5 |
| observed \|O\| (M3_cov) | ≥ 55 |
| designed ordering traps present in O | = 12 |

Tier labels are distributed so `|O| ≥ 55`, including the 12 designed
count-vs-placement traps: a market roundup naming Apple three times in the
body (`context`), a list mention "Apple, Amazon and Tesla also fell"
(`passing`, 1 mention each), and a short `primary` piece whose only mention
is the headline — a mention-count ranker misorders all three.

**Feed files** are the generator's output: valid RSS 2.0 / Atom documents
with fixed `pubDate`s in Feb–Mar 2026, all inside the ±7-day dedup window
relative to each other where groups require it. Regenerating
(`python evals/fixtures/generate.py --seed 4242`) is a reviewed change, and
a fixture self-check test (§6) re-runs it into a temp dir and asserts
byte-identity with the committed `feeds/*.xml` and `labels/story_groups.json`
— so a hand-edit to a feed file can never silently desynchronize the corpus
from its truth.

## 5. Baselines and gates

The naive baseline — implemented *live* in `evals/metrics.py` (~40 lines)
— is the honest afternoon script: whole-word alias matching
(case-insensitive for names/nicknames, exact-case for tickers, cashtags
literal), dedup by identical canonical URL, relevance = accepted mention
count. `run.py` prints its actual live-computed numbers alongside the
gates.

| Metric | Naive baseline (provisional estimate) | Why |
|---|---|---|
| M1 | ≈ 0.72–0.80 | recall 1.0, but every trap article fires, plus incidental hits: lowercase "price target"/"shell company"/"meta-analysis" match TGT/SHEL/META name aliases; "ALL"/"CAT" fire in caps headlines — ≈40–60 FP pairs against ≈91 TPs |
| M1_amb / M1_amb_base | ≈ 0.50–0.60 | all FPs live in the ambiguous subset by construction; subset precision ≈ 0.4 |
| M1_amb_abl | ≈ 0.50–0.60 | the naive matcher never used per-company terms, so ablation does not change it |
| M2_f1 | ≈ 0.24 | only the 10 identical-URL pairs of 72 recovered (P=1.0, R≈0.14) |
| M3 | ≈ 0.65–0.75 | mention count misorders every designed count-vs-placement trap |
| M4 | 0.0 | no ledger ⇒ steps 2/3/4/6 fail |
| M5, M6_lex | — | not meaningful for the baseline |

**These estimates are provisional and must be replaced by measured values
in this table when the fixtures land** (CONVENTIONS.md requires EVALS.md to
state what the baseline scores). Prose margins are not self-enforcing, so
`test_gates.py` additionally asserts the margin at run time:

```
gate(M1)          − naive_live(M1)          ≥ 0.10
gate(M1_amb)      − naive_live(M1_amb)      ≥ 0.10
gate(M1_amb_base) − naive_live(M1_amb_base) ≥ 0.10
gate(M1_amb_abl)  − naive_live(M1_amb_abl)  ≥ 0.10
gate(M3)          − naive_live(M3)          ≥ 0.10
gate(M2 R_pair)   − naive_live(R_pair)      ≥ 0.50
```

(No margin assertion on M2 `P_pair`: the naive URL-only clusterer trivially
scores 1.0 there. `P_pair`'s job is to block over-merging, and `R_pair`
carries the margin.) If the finished corpus turns out easier than estimated
— say naive M1 lands at 0.88 — the margin assertion fails and forces either
harder fixtures or a higher gate, instead of letting a 16-point margin
quietly become 4.

Gates (asserted on live-computed values):

| Gate | Threshold | Rationale |
|---|---|---|
| M1 mention_f1 | ≥ 0.92 | Strong-surface positives are near-free; the evidence scorer must then hold precision on traps without dropping weak positives. Naive ≈ 0.76 — the ~16-point margin *is* the disambiguation engine. Not higher: a few fixtures are written to be genuinely borderline and may legitimately fall either side. |
| M1_amb ambiguous F1 | ≥ 0.85 | The headline gate — scored only where disambiguation actually decides. Naive ≈ 0.55. Blocks the trivial precision play: rejecting all weak candidates loses every `weak_only` positive (≥26 of the ≥34 in-subset positives), dropping subset recall below 0.5 and failing loudly. |
| M1_amb_base | ≥ 0.82 | Same subset over the 68 bases only — one vote per independent authoring decision, so a single failure is not amplified 5× by syndication (nor hidden by it). Set 3 points below M1_amb because the sample is thinner (≥40 pairs) and each error costs ~2.5 points. |
| M1_amb_abl | ≥ 0.70 | With all per-company terms emptied, the general mechanism must still clear a bar 15 points above naive. Set below M1_amb because ≤5 fixture positives are legitimately `cue_free` and will be missed; a run that memorized the corpus in per-company terms collapses far below 0.70. |
| M2 P_pair | ≥ 0.95 | Over-merging destroys trust (two different stories delivered as one) and silently suppresses deliveries via the ledger; τ=0.60 on 3-shingles leaves margin. Defined as 0.0 when nothing is clustered, so the never-cluster degenerate fails here too. |
| M2 R_pair | ≥ 0.85 | Heavily edited copies (headline swap + boilerplate + 2 dropped sentences) must still cluster; a few extreme edits may fall below τ — that costs duplicates in a digest, annoying but honest. Naive R ≈ 0.14. |
| M2_trap merged no-link pairs | = 0 | Hand-vetted distinct-story pairs may never merge. Zero, not small: any violation is a threshold/shingling design bug, not noise. |
| M2_near | ≥ 4 of 5 | At least 4 of the 5 `near`-band no-link pairs must compute J ≥ 0.40 (while still < 0.60 by M2_trap). Keeps the trap gate binding just under τ instead of testing pairs that are trivially dissimilar; a normalization change that pushes everything to J ≈ 0 fails here. |
| M3 relevance_ordering | ≥ 0.90 | Placement-weighted scoring should order essentially all tier pairs; ties among identically-shaped articles cost half-credit and justify the 10% slack. Naive ≈ 0.70. |
| M3_cov coverage | \|O\| ≥ 55 ∧ 12/12 traps in O | Stops M3 from being inflated by detection misses that quietly delete the hard ordering pairs from its denominator. Failing coverage fails M3. |
| M4 delivery_exactly_once | = 1.0 | The product's promise ("send me a link" — once). Any failing step is a broken invariant, not a quality regression. |
| M5 determinism | = 1.0 | CONVENTIONS.md hermeticity; what makes every other number trustworthy. Cross-process with differing `PYTHONHASHSEED`, plus committed golden hashes. |
| M6_lex lexicon specificity | = 1.0 | Closes the memorization channel: frozen lexicon hashes match the manifest, and no cue/anti entry occurs in exactly one fixture article. Without this, M1/M1_amb are passable by encoding fixture identities into `data/`. |

**Anti-gaming summary.** M1 alone is gamed by accepting everything
(precision dies); M1_amb by rejecting all weak candidates (its recall dies);
the pair pins both directions. Per-article lexicon memorization would raise
both together — that is closed by M6_lex (global lexicons) and M1_amb_abl
(per-company terms), with the freeze protocol (SCOPE D19) preventing the
lexicons from being grown after the corpus exists. Subset shaping is closed
by defining `U_amb` from a frozen file. M2 `P_pair` alone is gamed by never
clustering — `R_pair` blocks it, and `P_pair` is defined 0.0 on an empty
prediction set; `R_pair` alone by clustering everything — `P_pair` and
`M2_trap` block it; `M2_trap` alone is gamed by a corpus of trivially
distant pairs — `M2_near` blocks it. M3 cannot be gamed by constant scores
(ties earn 0.5 < 0.90) nor by suppressing hard pairs (M3_cov). M4 and M5 are
mechanical invariants. Margin assertions prevent an accidentally-easy corpus
from turning any gate vacuous.

## 6. How the suite runs

Mirrors `orbit-backend/evals/` and the sibling projects:

- **`evals/metrics.py`** — `MetricResult(name, value, gate, passed,
  detail)`, `EvalReport`, pair-universe construction, the frozen
  weak-surface subset for `U_amb`, pair-counting clustering scores, the
  lexicon-specificity scan, and the naive baseline implementation.
- **`evals/run.py`** — `python -m` runnable with zero configuration and no
  network. Builds a fresh store (tmp SQLite), loads `watchlist.json`,
  registers the six fixture feeds (`file://` URLs → `FixtureFeedSource`),
  asserts the §4 fixture floors, runs ingest + digest under
  `FixedClock("2026-03-02T13:00:00Z")` with offline notifiers, executes the
  M4 scenario, the M1_amb_abl ablation run, and the M5 cross-process double
  run, computes all metrics, and prints a scorecard — one line per metric
  with value, gate, PASS/FAIL, and detail (per-company table, failing pair
  examples, offending lexicon entries, naive deltas) — then a JSON summary.
  Exit code 0 iff all gates pass.
- **`evals/test_gates.py`** — one pytest test per gate row in §5, names
  carrying FR ids: `test_fr6_mention_f1_gate`,
  `test_fr6_ambiguous_f1_gate`, `test_fr6_ambiguous_f1_base_gate`,
  `test_fr6_ambiguous_f1_ablated_gate`, `test_fr6_lexicon_specificity_gate`,
  `test_fr7_dedup_precision_gate`, `test_fr7_dedup_recall_gate`,
  `test_fr7_no_link_zero_merges`, `test_fr7_no_link_near_band`,
  `test_fr8_relevance_ordering_gate`, `test_fr8_ordering_coverage_gate`,
  `test_fr11_exactly_once_gate`, `test_fr16_determinism_gate`; plus
  `test_naive_margins` (the §5 margin assertions),
  `test_fixtures_regenerate_identically` (re-runs `generate.py --seed 4242`
  into a tmp dir and asserts byte-identity with committed `feeds/*.xml` and
  `labels/story_groups.json`), `test_expected_digest_derivation_matches`
  (re-derives `expected_digest.json`), `test_golden_hashes`, and
  `test_eval_runner_passes` executing `run.py` end-to-end and asserting exit
  code 0. `uv run pytest tickerpress/` therefore fails on any quality
  regression, fixture drift, or lexicon drift.

Runtime budget: 108 items × 12 companies, pure-string pipeline, exact
Jaccard over a ≤108-article window; three full pipeline runs (main,
ablation, M5 subprocess) — full suite well under 25 s on a laptop; runs in
every CI invocation.

## 7. FR → test/eval mapping

| FR | Covered by |
|---|---|
| FR-1 | `tests/test_watchlist.py` (ticker validation, generated aliases, suffix strip, duplicate rejection) |
| FR-2 | `tests/test_feeds.py` (FetchResult statuses, per-feed error isolation, ascending-feed-id order, conditional-GET state on a stubbed live adapter) |
| FR-3 | `tests/test_feedparse.py` (RSS/Atom subset, guid fallback, RFC 822/3339 dates, malformed-item skip; normalization, hyphen/slash tokenization, offset stability) |
| FR-4 | `tests/test_archive.py` (feed+guid uniqueness, canonical URL table-driven cases, immutability, first-version-wins) |
| FR-5 | **M1, M1_amb, M1_amb_base** + `tests/test_detect.py` (case rules per kind, 1-char ticker never bare, common-word demotion, cashtag/exchange patterns, all-caps runs, candidate emission order) |
| FR-6 | **M1, M1_amb, M1_amb_base, M1_amb_abl, M6_lex** + `tests/test_disambig.py` (each feature in isolation on constructed snippets; the four SCOPE worked examples as named cases with exact feature vectors; title-initial rule; window-field clipping; θ boundary; no per-alias threshold) |
| FR-7 | **M2 (P/R), M2_trap, M2_near** + `tests/test_dedup.py` (shingle math on known strings, window edges, window-limited fast paths, J tie-break to smallest article id, representative re-pointing) |
| FR-8 | **M3, M3_cov** + `tests/test_relevance.py` (component truth tables, `content_token_count` lede rule incl. NULL content, formula values, story-level max rollup) |
| FR-9 | `tests/test_digest.py` (selection filters, ordering law, template render golden file, quiet-when-empty, dry-run persists nothing) |
| FR-10 | **M4 step 3** + `tests/test_alerts.py` (threshold boundary, mode matrix, ledger sharing) |
| FR-11 | **M4, M5** + `tests/test_ledger.py` (lifecycle transactions, failure release, partial-index violation, in-memory backend has the same constraint) |
| FR-12 | `tests/test_notifiers.py` (console/file byte-identity with body_text; live adapters: env-gating + payload shape against stubs) |
| FR-13 | `tests/test_explain.py` (feature completeness, rejected candidates shown, read-only — no recomputation) |
| FR-14 | `tests/test_api.py` (FastAPI TestClient, error-code mapping, 204-on-empty digest, alias POST schema, dry-run response) |
| FR-15 | `tests/test_cli.py` (CliRunner, exit codes, `--json`, `--now` injection, `init` lexicon report) |
| FR-16 | **M5** (cross-process, golden hashes) |
