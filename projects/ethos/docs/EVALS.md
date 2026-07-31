# Ethos — Evals

## What this product lives or dies on

1. **Routing with honest abstention** (FR-3/FR-4): plain-language moral
   questions — direct, colloquial, and oblique scenario phrasings — must land
   on the right topic in the fixed taxonomy, and questions the corpus cannot
   answer must be refused rather than force-matched. The refusals that matter
   are *moral questions outside the taxonomy* (gene editing, workplace
   surveillance, abortion), which are rich in the exact vocabulary the router
   indexes. If routing is unreliable the product degrades into a topic
   browser; if it stretches matches it becomes a confident mis-quoter, which
   is worse.
2. **Citation-faithful composition** (FR-1, FR-6/7/8/9): every rendered answer
   must be complete (every requested tradition that has a position, well-formed
   perspective, correct agreement map, correct not-covered/filtered-out
   distinction), every quotation byte-faithful to a real committed passage with
   a correct locator and source line, and the pipeline must be tamper-proof
   against its own polish adapter. A single fabricated or corrupted citation
   destroys the product's reason to exist.

Everything else (CRUD, history, CLI rendering) is covered by ordinary tests,
not eval gates.

## Design rules this suite follows

These are the three rules the first draft of this file broke, stated up front
so the implementer does not silently un-fix them:

- **R1 — No instrument grades itself.** The citation-integrity metric (M3) may
  not share code, models, or a loader with the verifier it certifies (FR-8).
  It re-parses the *printed answer* and resolves it against the *raw corpus
  files*. Enforced by `test_independent_checker_isolation`, which AST-scans
  `evals/independent_check.py` and fails if it imports anything outside
  `json`, `pathlib`, `re`, and `sys`.
- **R2 — Every exact gate must be provably able to fail.** Any gate whose
  threshold is `= 1.0`, `= 0.0`, or boolean has a committed negative-control
  artefact that it must reject (`evals/fixtures/negative_controls/`). A suite
  that has never been observed red is not evidence.
- **R3 — Gates check substance, not only shape.** A corpus that satisfies
  every structural floor with ten passages, one stance and one reading link is
  a cheaper way to pass than doing the work; C11–C16 close that door.

## Metrics

All metrics live in `evals/metrics.py` (except M3's checker, deliberately
isolated) and run exclusively against committed fixtures with offline
components (`LexicalRouter`, `NullPolisher`, the eval-only `FaultyPolisher`,
in-memory store). No network, no clock, no randomness. Metric ids map to gates
in `evals/test_gates.py`; test names reference FR ids.

**Scoring convention:** in every routing metric, an abstained question counts
as a **miss** (never as "not applicable").

### M1 — Routing accuracy (capability 1)

Fixture tiers, per topic: 4 direct + 3 colloquial + 3 oblique (visible) = 240
over 24 topics, plus 2 held-out oblique per topic = 48, plus 20 dual-home
questions.

```
M1-direct = #(top1 = truth) / 96          direct tier
M1-coll   = #(top1 = truth) / 72          colloquial tier
M1b       = #(top1 = truth) / 72          oblique tier, visible during lexicon authoring
M1b'      = #(top1 = truth) / 48          oblique tier, hash-frozen holdout
M1gap     = M1b − M1b'                    overfit gap
M1a       = #(top1 = truth) / 240         composite (reported, not gated)
M1c       = #(truth ∈ top3) / 240         recall@3
M1d       = #(both truth topics ∈ top3) / 20   dual-home questions
```

**Why per-tier gates.** A single composite hides the tier that matters. The
direct tier is 40% of the set and is near-canonical phrasing matched against
`question_forms` that the same author wrote into the router — it is close to
free, so a composite of 0.85 is reachable with a colloquial tier well below
0.85 and nobody notices. Per-tier gates price each tier at what it is worth:
a direct-tier miss is a wiring bug (≥ 0.97), the colloquial tier is the
realistic middle case (≥ 0.85), the oblique tier is the honest hard case
(≥ 0.70).

**Why a holdout and a gap.** M1b is authored by the same person who authors
the lexicons, so a failing oblique question can always be "fixed" by adding
its distinctive noun as a weight-2 keyword. Neither code review nor a wider
lexical ban is a real control (a wider ban would also outlaw the scenario
vocabulary that *is* the craft — see § rejected controls). The mechanical
control is `oblique_holdout.json`: 48 questions authored **last**, hash-frozen
in `evals/baselines.json` before any τ/κ tuning or lexicon iteration, and
never consulted while editing `data/`. M1b′ gates its absolute level (≥ 0.55)
and **M1gap ≤ 0.15 gates the difference** — memorisation raises M1b without
raising M1b′ and fails the gap gate. Any post-freeze edit to `topics.json`
requires a REVIEW.md entry recording M1b/M1b′ before and after, and C19 fails
the suite until `evals/baselines.json` is regenerated.

### M2 — Abstention quality (capability 1)

Over the 40 out-of-scope questions (of which ≥ 25 are *near-miss* moral
questions outside the taxonomy) and the 260 in-scope questions (240 tiered +
20 dual-home):

```
M2a      = #(abstains) / 40                     correct refusals
M2a_near = #(abstains) / #near-miss (≥25)       correct refusals, hard subset
M2b      = #(abstains) / 260                    false refusals; lower is better
```

M2a and M2b gate as a pair — either alone is trivially gameable. More
importantly, the *fixture set* is what makes the pair non-trivial: an
out-of-scope set made of "what's the capital of Mongolia?" scores 0 under
BM25, so the null rule `abstain iff s1 = 0` would score 0.90/0.00 and pass
both gates with no router at all. Gate **C20** forbids that composition: ≥ 25
of the 40 out-of-scope questions must have a top raw score `s1` above the
**median s1 of the in-scope direct tier**. Those questions can only be refused
by FR-4's coverage signal κ, which is the mechanism under test. The near-miss
items are drawn from the two families SCOPE non-goal 7 excludes: AI training
on artists' work, gene editing embryos, workplace surveillance, data privacy,
climate duties to future generations, organ markets, abortion, sexuality,
gambling, immigration.

τ and κ are committed in `data/router.json` with a mandatory `tuning_note` and
are tuned **only on the direct and colloquial tiers**; the oblique tiers and
the out-of-scope set are hash-frozen holdouts (C19). `run.py --sweep` prints
the (τ, κ) tradeoff curve over M2a/M2b/M1 so the chosen point is inspectable.

### M3 — Citation integrity, independently measured (capability 2)

**Answer set:** the render of every in-scope fixture question that routes
(polish off) plus, for each of the 24 topics, three forced-topic renders (all
traditions / a 3-tradition filter / a 1-tradition filter) — 312 renders.
Composition runs with **FR-8 acceptance bypassed** (`compose_unverified`), so
M3 scores *composition* and M4 scores *the verifier*: they are separate
instruments and neither is downstream of the other.

**Measurement is independent by construction (R1).**
`evals/independent_check.py` imports only `json`, `pathlib`, `re`, `sys`. It:

1. loads `data/corpus/*.json` with stdlib `json` into plain dicts — no
   Pydantic, no `ethos.*` import, no normalization of any kind;
2. re-parses the **plain-text render** per the grammar in DATA_MODEL
   § Plain-text render (not the `AnswerBody` envelope), extracting every quote
   block, paraphrase block, citation line and the `Citations:` table;
3. for each rendered citation, asserts with **raw string equality** (no NFC,
   no strip, no case folding):
   - the quoted text equals exactly one passage dict's `"text"` value (or, for
     a paraphrase block, its `"paraphrase"` value with the exact label line
     present and `"text"` null);
   - the printed locator equals that passage's `"locator"`;
   - the printed source line equals the string rebuilt from that passage's
     source dict (`"{title}, trans. {translator} ({year})"`, or
     `"{title} — {edition_note}"` for `reference_only`);
   - the marker appears exactly once in the `Citations:` table and its
     `passage_id`/`source_id` agree;
4. independently recomputes, from the raw corpus, how many citations the
   rendered positions *should* have produced; any shortfall counts as failed
   citations in the numerator's complement.

```
M3 = #(citations passing all four checks) / max(#rendered citations, #expected citations)
```

**On the baseline.** Honestly stated: for a correct composer M3 is 1.0 *by
construction* — the composer copies `passage.text` verbatim — so there is no
naive implementation that scores meaningfully lower on this answer set, and
the earlier draft's "≈ 0.55" was a number measured on a different
configuration (verification disabled, adversarial polish) which belongs to M4.
M3's value is not discrimination, it is **independence**: it is the only check
in the suite that would survive a shared-mode failure — a loader that
NFC-folded quote punctuation or normalized the en-dash in `Ketubot 16b–17a`
would leave composer and verifier in perfect agreement and only M3 would move.
Its falsifiability is demonstrated, not assumed: `negative_controls/` commits
`broken_corpus_word_swap`, `broken_corpus_locator_mutation`, and
`broken_corpus_translator_year`, and `test_negative_controls.py` asserts M3
drops below 1.0 for each (their measured values are recorded in
`evals/baselines.json`).

### M4 — Tamper detection (capability 2)

`evals/faulty_polisher.py` implements `ProsePolisher` with 50 scripted cases
over fixture answers: **20 clean** (paraphrases mutable regions only,
preserves every immutable region byte-for-byte) and **30 mutated** (one
mutation each, covering all 25 classes at least once).

Mutations are specified as **structured region operations**
(`{op, region, marker?, find, replace}`) applied to the parsed envelope — not
as committed byte offsets, which silently become no-ops or drift into other
regions after a `composer_version` bump. The harness asserts every mutated
render **differs** from its clean render, so a stale fixture is diagnosed as a
fixture bug rather than passing as a verifier success.

```
M4a (recall) = #(mutated cases rejected by FR-8) / 30
M4b (FPR)    = #(clean cases rejected)          / 20
```

Every class maps to a named FR-8 check, and every check is exercised:

| # | Mutation class | Caught by |
|---|---|---|
| 1 | `invented_passage_id` | (a)+(f) |
| 2 | `altered_locator` | (c) |
| 3 | `paraphrased_quote` | (b) |
| 4 | `truncated_quote` | (b) |
| 5 | `word_swap_in_quote` | (b) |
| 6 | `homoglyph_in_quote` | (b) |
| 7 | `dropped_marker` | (a) |
| 8 | `duplicated_marker` | (a) |
| 9 | `deleted_quote_kept_marker` | (a) |
| 10 | `injected_extra_quote` | (a) |
| 11 | `invented_locator_in_prose` | (e) |
| 12 | `bulk_prose_injection` (moral claim added to a summary) | (e) length bound |
| 13 | `altered_source_line` | (c) |
| 14 | `dropped_paraphrase_label` | (f) |
| 15 | `flipped_is_paraphrase` | (f) |
| 16 | `swapped_marker_pairing` (C1's text under C2's locator) | (f) |
| 17 | `altered_further_reading` | (g) |
| 18 | `swapped_tradition_header` | (h) |
| 19 | `reordered_perspectives` | (h) |
| 20 | `altered_stance` | (i) |
| 21 | `altered_agreement_map` | (i) |
| 22 | `altered_not_covered` | (i) |
| 23 | `altered_context_note` | (i) |
| 24 | `altered_safeguard_block` | (d) |
| 25 | `unparseable_envelope` (dropped close sentinel) | envelope parse-back (FR-9) |

**M4b is not vacuous.** Clean cases preserve immutable regions, which makes
(a)–(d) and (f)–(i) trivially satisfied, so the only check that can raise a
false positive is (e). ≥ 5 of the 20 clean cases must therefore contain
mutable prose that **legitimately names a work and a number** (e.g. an
`intra_tradition_note` reading "later Confucians read XIII.18 narrowly", a
summary mentioning "ch. 2"), preserved verbatim by the paraphrase. Those cases
price the diff-scoping of check (e): a check that scanned whole regions rather
than changed spans, or that reused the anchored validation regexes unanchored,
would reject them and M4b would be non-zero. ≥ 3 further clean cases must
paraphrase a region to within 30–40% length change, pricing the length bound.

The same test asserts every rejected case falls back to the deterministic
render byte-identically with `polish_fell_back = true` and `verified = true`
on what is persisted.

### M5 — Answer completeness & well-formedness (capability 2)

**Answer set:** 24 topics × 3 render variants (all traditions / a 3-tradition
filter / a 1-tradition filter) = 72 answers. Expected sections = Σ over
answers of #(requested traditions that have a position on the topic).

A section is **well-formed** iff it has a stance from the enum, a non-empty
summary, ≥ 1 quote block whose marker resolves *or* ≥ 1 explicitly-labeled
paraphrase citation, a locator + source line per citation, and ≥ 1
further-reading entry. An answer additionally passes iff:

- `agreement_map` partitions exactly the rendered tradition ids;
- `not_covered` = requested traditions with no position on the topic (**not**
  "the 10 traditions minus the rendered ones" — under a filter that would tell
  the user nine traditions have nothing to say, which is false and is exactly
  the misleading comparative claim the product exists to avoid);
- `filtered_out` = traditions with a position that the filter excluded, and is
  empty in the unfiltered variant;
- every `complicating` passage and `intra_tradition_note` the corpus declares
  for a rendered position actually appears in the render;
- in the unfiltered variant, the set of rendered stances equals the corpus's
  stance set for that topic (≥ 3 by C12).

```
M5 = #(well-formed sections in answers whose answer-level checks pass) / #expected
```

**Honest note on what M5 adds.** For the unfiltered variant the section-level
half of M5 is largely implied by C3/C7/C8 — it is retained as regression
detection, not as evidence of capability. The parts that can genuinely fail
are the filtered variants (the `not_covered`/`filtered_out` split, agreement
map over a subset, canonical ordering of a subset) and the corpus-declared
content checks, none of which the C-gates imply. Its baseline is a composer
that renders only the first requested tradition (`baseline_first_tradition_only`),
which scores ≈ 0.14. The earlier draft's "≈ 0.6 first-draft corpus" baseline
is deleted: that corpus could not be committed without C3/C7 failing the suite
first, so the number was unreproducible by anyone.

## Corpus and fixture gates (pytest, boolean)

Each is a separate pytest asserting a property of the committed data; all must
pass. C1–C10 are structural (FR-1 layers 1–2); **C11–C16 are the substance
gates** (FR-1 layer 3) that exist because a degenerate corpus — 10 passages
reused as the sole `core` reference of 144 positions, every stance
`forbidden`, every summary a name-substituted template, one SEP link per
tradition — satisfies C1–C10, M3, M5 and D0 completely, and is the cheapest
possible corpus to author.

| Gate | Property |
|---|---|
| C1 | All files parse against the Pydantic schemas (strict); the loader is byte-preserving for every string field; no corpus string contains `[[`/`]]`; no `text`/`paraphrase` contains `“`/`”` |
| C2 | `(topic_id, tradition_id)` unique across positions |
| C3 | Every position: ≥ 1 `core` passage ref, ≥ 1 further-reading entry |
| C4 | Every quoted passage's source: license ∈ {public_domain, cc0, cc_by}, named translator, translation year, URL |
| C5 | Every locator matches its source scheme's **anchored validation** regex |
| C6 | Every quote ≤ 90 words with `transcription_checked = true`; paraphrase-only passages belong to `reference_only` sources and vice versa |
| C7 | Floors: 10 traditions; ≥ 24 topics; ≥ 6 positions/topic; ≥ 12 positions/tradition |
| C8 | All cross-references resolve (passage→source, position→topic/tradition/passage, topic→safeguard/related) |
| C9 | Fixture lexical integrity (below) |
| C10 | No keyword term is all-stopwords; `Tradition.order` is 1..10; `sensitive` ⇒ `crisis_resources ∈ safeguard_ids` |
| C11 | Passage reuse: no passage is `core` in > 3 positions; distinct cited passages ≥ 6 × #topics |
| C12 | Stance diversity: ≥ 3 distinct stances per topic (≤ 2 topics allow-listed in `gate_exceptions.json` may have 2) |
| C13 | Summary distinctness: within a topic, pairwise stemmed-token Jaccard < 0.5; globally no pair shares > 70% of stemmed tokens |
| C14 | Anti-proof-texting (SCOPE D11): ≥ 30% of positions carry a `complicating` ref; every safeguard-carrying topic's positions carry one or an `intra_tradition_note` |
| C15 | Grounding provenance: ≥ 80% of positions have ≥ 1 quoted `core` passage; `reference_only` ≤ 15% of cited passages |
| C16 | Reading-list breadth: ≥ 120 distinct entries; none in > 10% of positions |
| C17 | **Safeguard matrix** (FR-10, below) |
| C18 | Normalization conformance (FR-2): `stem(voc[i]) == output[i]` for all 500 committed Porter pairs; committed NFKC/casefold cases produce the recorded tokens |
| C19 | **Baselines & freezes current** (below) |
| C20 | Out-of-scope composition: ≥ 25 OOS questions have `s1` above the median `s1` of the in-scope direct tier |

**C9 — fixture lexical integrity.** Define a *distinctive token* as a stemmed
content token whose document frequency across the 24 topic documents is ≤ 3
(0 counts as distinctive). Then:

- every fixture question's truth topic(s) exist;
- **oblique tiers** (visible and holdout): no distinctive token of the
  question may appear in its own topic's `title`, `description`,
  `question_forms`, or **weight-3** keyword terms. Weight-1/2 terms are
  deliberately *not* constrained — see § rejected controls.
- **colloquial tier**: stemmed-token Jaccard against every `question_form` of
  its own topic < 0.5, so a colloquial question cannot be a stopword-level
  restatement of a phrase already in the router document;
- ≥ 30% of `oblique_holdout.json` entries carry `origin: "observed"` with a
  source recorded in `rationale` (a phrasing the owner actually wrote or found
  in the wild, lightly anonymised) rather than `origin: "authored"`. C9 is a
  floor on lexical difficulty, not a licence for contorted English: the
  authoring protocol rejects any question the target user would not plausibly
  type.

**C17 — safeguard matrix.** For each of the two `sensitive` topics
(`suicide_and_self_harm`, `euthanasia_and_end_of_life`) and one
`not_legal_advice` topic (`capital_punishment`), render across
{no filter, 1-tradition filter, 3-tradition filter} × {polish off,
FaultyPolisher clean, FaultyPolisher mutated → fallback} × {routed via the
topic's first direct fixture question, `--topic` forced} × {text render,
`AnswerBody` JSON} = 36 cells each, and assert in every cell that (i)
`safeguards` is non-empty, (ii) each text byte-equals `data/safeguards.json`,
(iii) it is first in render order, (iv) the kinds equal the topic's
`safeguard_ids`. The remaining safeguard-carrying topics run a reduced 4-cell
matrix (no filter × {polish off, mutated → fallback} × {text, JSON}). This
replaces the earlier "cannot be disabled by any flag (tested)" claim, which
was gated nowhere and covered by one golden file.

**C19 — baselines & freezes current.** `evals/baselines.json` is committed and
regenerated only by `python evals/run.py --write-baselines`. It records:
`corpus_version`; the SHA-256 of `data/router.json` and `data/stopwords.txt`;
the SHA-256 of every fixture file (with `oblique_holdout.json` and
`oos_questions.json` marked `frozen: true`); the τ/κ values and their
`tuning_note`; and, for every metric, the baseline implementation id and its
**measured** value. C19 recomputes all of it and fails if any hash differs or
any baseline differs by > ±0.02. Consequences, all intended: editing the
stopword list, moving τ, adding a fixture, or touching the corpus fails the
suite until the baselines are re-derived in the same commit — which is the
only mechanism that keeps the numbers in the gate table below honest.

## Negative controls (R2)

`evals/fixtures/negative_controls/` commits one minimal broken artefact per
exact gate, and `evals/test_negative_controls.py` asserts each gate **rejects**
its artefact. Without this, a validator that returns `True` on empty input, a
regex never wired into its loop, or a metric with a zero denominator is
indistinguishable from a healthy one — and every exact gate in this suite
(C1–C20 boolean, M3 = 1.0, M4a = 1.0, M4b = 0.0, M5 = 1.0, D0 byte-identical)
has that failure mode.

| Artefact | Must be rejected by |
|---|---|
| `unresolvable_passage_ref/` | C8 |
| `quote_95_words/` | C6 |
| `position_without_reading/` | C3 |
| `pd_source_null_translator/` | C4 |
| `locator_violates_scheme/` | C5 |
| `topic_with_5_positions/`, `nine_traditions/` | C7 |
| `loader_folds_endash/` (corpus whose locator contains a character the loader would fold) | C1 |
| `passage_core_in_9_positions/` | C11 |
| `all_forbidden_topic/` | C12 |
| `templated_summaries/` | C13 |
| `no_complicating_passages/` | C14 |
| `paraphrase_only_corpus/` | C15 |
| `one_reading_per_tradition/` | C16 |
| `oblique_question_with_title_word/` | C9 |
| `oos_set_all_zero_score/` | C20 |
| `stemmer_off_by_one/` (a deliberately wrong stem in a fixture pair) | C18 |
| `broken_corpus_word_swap/`, `broken_corpus_locator_mutation/`, `broken_corpus_translator_year/` | M3 < 1.0 |
| `answer_with_duplicated_marker/` | M4a / FR-8(a) |
| `answer_missing_reading/` | M5 < 1.0 |

Cost: ~20 tiny JSON artefacts and ~80 lines of test. It is the difference
between "all gates pass" meaning something and meaning nothing.

## D0 — Determinism (plain pytest, no score)

(a) Route + compose the full fixture set in **three fresh subprocesses**:
`PYTHONHASHSEED=0`, `PYTHONHASHSEED=1`, and `PYTHONHASHSEED=0 LC_ALL=C` (the
default run uses `en_US.UTF-8`). Routing JSON, `AnswerBody` JSON, and text
renders must be byte-identical across all three. Pinning the seeds explicitly
matters in both directions: Python randomises it per process by default (so a
dict-ordering bug is caught only probabilistically) and some CI harnesses pin
it (so the check silently becomes a no-op). The locale run exists because the
corpus is full of non-ASCII and lexicographic tie-breaking is load-bearing.

(b) The 9 golden answers regenerate byte-identically from the committed
corpus. `evals/fixtures/regenerate_golden.py` runs in `--check` mode inside
the suite and in `--write` mode only by hand. If `composer_version` differs
from the value embedded in the goldens, the suite fails unless *all* of: every
golden's content changed, every golden's embedded `composer_version` equals
the code constant, and `--check` reports a clean tree. "Regenerated in the same
commit" is thereby enforced rather than trusted.

(c) Rebuilding the BM25 index yields an identical index serialization.

(d) For every stored answer whose `composer_version` equals the current
constant, re-rendering `body` reproduces `rendered_text` byte-for-byte (US-7).

Golden coverage (9 files, each an `AnswerBody` JSON + its text render): 4
ordinary topics, 1 sensitive topic (safeguard block), 1 dual-home question, 1
**tradition-filtered** render, 1 **polish-fallback** render, 1 **refusal**
payload. The last three exist because no metric's answer set covered them.

## Fixture strategy

Everything is committed under `evals/fixtures/`. Questions are
**hand-authored** (there is no generator randomness to seed); mechanical
properties are enforced by the C9 predicate in `evals/corpus_gates.py`, which
runs inside the suite. **Ground truth never comes from the router under
evaluation** — it comes from the authoring protocol below.

| File | Contents | Ground truth |
|---|---|---|
| `routing_questions.json` | 240 entries `{id, text, truth_topic, tier, rationale}` — 10 per topic (4 direct / 3 colloquial / 3 oblique). Direct: near-canonical ("Is lying ever acceptable?"). Colloquial: informal first-person, constrained by C9's Jaccard rule. Oblique: scenario descriptions constrained by C9's distinctive-token rule. | Authoring protocol: (1) target topic fixed first; (2) question drafted per tier rules; (3) the C9 predicate enforces the lexical constraints; (4) second-pass review confirms no other topic is a more natural home — questions with two defensible homes move to the dual-home set; `rationale` records the judgment. |
| `oblique_holdout.json` | 48 entries `{id, text, truth_topic, origin: authored\|observed, rationale}` — 2 per topic, same constraints, **authored last and hash-frozen before any lexicon or τ/κ tuning**; ≥ 30% `observed`. | Same protocol; freeze recorded in `baselines.json` (C19). |
| `ambiguous_questions.json` | 20 entries `{id, text, truth_topics: [2 ids], rationale}` — genuinely dual-home ("should I report my father's tax fraud?" → `honesty_and_deception` + `duties_to_parents`). Used by M1d (both in top-3) and included in M2b's denominator. | Same protocol; dual-home judgment recorded in `rationale`. |
| `oos_questions.json` | 40 entries `{id, text, kind, rationale}`, `kind ∈ {moral_out_of_taxonomy, factual, technical, consumer, small_talk, non_moral_advice}`. **≥ 25 are `moral_out_of_taxonomy`** — near-miss moral questions from the two excluded families (SCOPE non-goal 7): AI training on artists' work, gene editing, workplace surveillance, data privacy, climate duties, organ markets, abortion, sexuality, gambling, immigration. Hash-frozen with the holdout. | By construction: no topic in the taxonomy addresses them; review confirms no topic is a defensible answer. C20 mechanically enforces that ≥ 25 of them are lexically *hard*, not trivially foreign. |
| `polish_cases.json` | 50 entries `{id, question_ref, mode: clean\|<class>, ops: [{op, region, marker?, find, replace}], expected}` driving `FaultyPolisher`; all 25 classes ≥ 1×; ≥ 5 clean cases carry curated work+number prose; ≥ 3 clean cases change a region's length by 30–40%. | By construction: `expected` follows from the FR-8 contract; the harness asserts each mutated render differs from its clean render. |
| `stemmer/voc.txt`, `stemmer/output.txt` | 500 Porter vocabulary/output pairs, a deterministic sorted-order slice of Martin Porter's published sample, with provenance and licence in a header comment. Plus `normalization_cases.json`: NFKC/casefold cases the corpus actually contains (curly apostrophes in "Qur'an", en-dashes in locator ranges, ligatures). | The published algorithm's own sample; committed so the promise is hermetic rather than a download. |
| `negative_controls/` | ~20 minimal broken artefacts (table above). | By construction — each is authored to violate exactly one gate. |
| `gate_exceptions.json` | `{near_unanimous_topics: [≤2 topic ids]}` — the only sanctioned escape from C12, committed and reviewable. | Explicit curator judgment. |
| `golden/` | 9 `AnswerBody` snapshots + text renders (coverage listed under D0). | The committed snapshot, reviewed at commit time. |
| `data/**` (the corpus and config) | The corpus is the fixture for C1–C20, M3, M5. | Curation protocol (SCOPE D6/D20); structural truth by C1–C10, substance truth by C11–C16. |

## Naive baselines and gates

Baselines are computed by reference implementations committed in
`evals/metrics.py` and **recorded as measured values** in
`evals/baselines.json`, which C19 keeps current (see above). `run.py` prints
the baseline column for **every** metric, not just M1.

- `baseline_title_overlap` — rank topics by count of shared stemmed tokens
  with the topic's title + description; ties lexicographic; never abstains.
  (Note it scores over the description, which C9 constrains only for
  *distinctive* tokens — hence its oblique-tier baseline is measured, not
  asserted to be near-random.)
- `baseline_s1_zero_abstain` — full BM25 ranking, `abstain iff s1 = 0`; the
  honest null abstention rule.
- `baseline_no_verifier` — pipeline with FR-8 disabled (M4a).
- `baseline_reject_all` — verifier rejects everything (M4b).
- `baseline_first_tradition_only` — composer renders only the first requested
  tradition's perspective while computing the agreement map as if unfiltered
  (M5).

Values marked ≈ are the design expectations to be replaced by the measured
numbers at the first `--write-baselines` run; C19 then keeps them true.

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1-direct | title/description overlap | ≈ 0.55 | **≥ 0.97** | Near-canonical phrasings against `question_forms` the same author wrote: this tier is nearly free, so a miss is a wiring bug, not a hard case. |
| M1-coll | same | ≈ 0.40 | **≥ 0.85** | The realistic middle case — informal first-person phrasing is what the target user types. C9 forbids it from being a stopword-level restatement of a `question_form`. |
| M1b oblique (visible) | same | ≈ 0.20 | **≥ 0.70** | The honest hard case: scenario phrasings with no distinctive overlap with the topic's title, description, forms or strongest keywords. ≥ 51/72 is reachable only through deliberate scenario vocabulary in weight-1/2 lexicon terms — the exact craft the product needs. |
| M1b′ oblique (holdout) | same | ≈ 0.20 | **≥ 0.55** | Same task, never seen while authoring lexicons or tuning τ/κ. Lower gate because it is genuinely held out. |
| M1gap = M1b − M1b′ | — | — | **≤ 0.15** | The anti-memorisation instrument. Lexicon stuffing raises M1b and not M1b′; nothing else in the suite can detect it. |
| M1a composite | title/description overlap | ≈ 0.40 | *report only* | Deliberately un-gated: a composite lets a strong direct tier hide a weak colloquial one. Kept in the scorecard for context. |
| M1c recall@3 | same | ≈ 0.55 | **≥ 0.95** | The answer prints top-3 alternates, so recall@3 is the "user can self-correct" bound (≤ 12 misses over 240). Abstentions count as misses. |
| M1d dual-home both-in-top3 | same | ≈ 0.15 | **≥ 0.85** | Dual-home questions must surface both homes (≥ 17/20). Abstentions count as misses, so refusing the hard questions is not free. |
| M2a OOS refusal | `abstain iff s1 = 0` | ≈ 0.35 | **≥ 0.80** | With ≥ 25 near-miss items (C20), the null rule refuses only the lexically foreign ones. 32/40 requires the coverage signal to actually work. |
| M2a_near (hard subset) | same | ≈ 0.05 | **≥ 0.68** | The subset that is the point: moral questions the taxonomy does not carry. ≥ 17/25 without a semantic model is a real bar. |
| M2b false refusal (over 260) | same | 0.00 | **≤ 0.05** | The null rule aces this alone, which is why it only counts paired with M2a. ≤ 13/260 keeps the reject option from eating coverage; widened from an earlier ≤ 0.02 because a hardened OOS set forces a higher κ and the two were not jointly satisfiable. |
| M3 citation integrity | *(none meaningful — 1.0 by construction; see § M3)* | 1.0; negative controls 0.94–0.99 (recorded) | **= 1.0** | The product's non-negotiable contract, measured by an independent reader over raw files (R1) and proven falsifiable by committed broken corpora (R2). |
| M4a tamper recall | no verifier | 0.00 | **= 1.0** | Each of the 25 classes models a real LLM failure; missing one leaves a hallucination channel open. Classes 12–23 and 25 were added because FR-8's original five checks could not detect them (and two of the original 14 — `altered_further_reading`, `swapped_tradition_header` — had no check at all). |
| M4b tamper FPR | reject-all | 1.00 | **= 0.0** | Clean paraphrase-only polish must survive, or polish is dead weight. The ≥ 5 work+number clean cases make this a real constraint on check (e), not a tautology. |
| M5 completeness | first-tradition-only composer | ≈ 0.14 | **= 1.0** | Deterministic structural property over 72 renders including filtered variants; the filtered half is what can actually fail. |
| C1–C20 | — | fail on any draft or degenerate corpus | **all pass** | Schema, licensing, floors, substance, fixture-integrity, safeguard, normalization and freeze invariants; boolean by nature; each proven able to fail by a negative control. |
| D0 | — | — | **byte-identical** | Hermeticity rule (FR-15), across hash seeds and locales. |

### Scope valve obligation

SCOPE D17's first valve is topic count 24 → 18. Dropping six topics raises the
uniform-random baseline from 0.042 to 0.056, removes confusable neighbours
(raising acc@1 for free), and shrinks every tier — so leaving these thresholds
untouched would silently *lower* the bar under schedule pressure. Firing the
valve therefore requires, in the same commit:

1. re-deriving every baseline (`run.py --write-baselines`) — C19 forces this
   anyway;
2. raising M1-direct, M1-coll, M1b and M1b′ by the measured increase in their
   `baseline_title_overlap` values;
3. keeping fixture density: ≥ 10 questions per surviving topic with ≥ 3
   oblique + 2 holdout, and moving ≥ 2 questions per *dropped* topic into
   `oos_questions.json` as near-miss items — dropped topics are excellent
   near-miss material, and this keeps C20 satisfied as the set shrinks.

### Rejected controls (and why)

Two mechanisms proposed against lexicon overfitting are deliberately **not**
adopted, recorded here so they are not re-litigated silently:

- **Extending C9's exclusion to every keyword term regardless of weight.** The
  scenario-vocabulary channel (weight-1/2 terms like "compliment", "cover
  for", "spare feelings") *is* the craft this product is supposed to
  demonstrate — SCOPE D9 names it as the hard part of lexical routing. Banning
  it makes M1b ≥ 0.70 unreachable for any lexical router and converts the tier
  into a test of constrained writing. The held-out tier and the gap gate
  detect memorisation without banning vocabulary, which is the honest
  instrument.
- **A leave-one-keyword-term-out "memorised term" metric.** Redundant with the
  gap gate for the same failure mode, and it mislabels good curation: a single
  term that is genuinely the only right hook for one scenario is exactly what
  a well-authored lexicon contains.

## FR → gate/test mapping (auditable, per CONVENTIONS)

| FR | Primary eval gate | Additional tests |
|---|---|---|
| FR-1 corpus schema/validation/version | C1–C8, C11–C16, C19 | `test_fr1_corpus_version_covers_data_dir`, `test_fr1_loader_byte_preserving`, negative controls |
| FR-2 normalization | C18 | `test_fr2_stopwords_pinned`, `test_fr2_nfkc_cases` |
| FR-3 lexical routing | M1-direct, M1-coll, M1b, M1b′, M1gap, M1c, M1d, C9 | `test_fr3_bm25_scores_known_case`, `test_fr3_phrase_bonus`, `test_fr3_tie_break_lexicographic`, D0(c) |
| FR-4 confidence/coverage/abstention | M2a, M2a_near, M2b, C20 | `test_fr4_confidence_zero_when_s1_zero`, `test_fr4_forced_topic_bypasses_router`, `test_fr4_refusal_payload_shape` |
| FR-5 perspective retrieval | M5 (filtered variants) | `test_fr5_not_covered_vs_filtered_out`, `test_fr5_passage_role_ordering` |
| FR-6 composition | M5, D0(a)(b) | `test_fr6_marker_numbering_first_appearance`, `test_fr6_agreement_map_partitions`, `test_fr6_no_overall_answer_path` (non-goal 2) |
| FR-7 citation & reading rendering | M3 | `test_fr7_paraphrase_label_rendered`, `test_fr7_source_line_forms` |
| FR-8 verification | M4a, M4b | one test per check (a)–(i); negative controls |
| FR-9 polish adapter & envelope | M4a (incl. `unparseable_envelope`), M4b | `test_fr9_envelope_roundtrip`, `test_fr9_fallback_sets_flag`, `test_fr9_llm_adapter_not_imported_on_eval_path` |
| FR-10 safeguards | C17 | `test_fr10_safeguard_first_in_render_order` |
| FR-11 persistence | D0(d) | `test_fr11_verified_false_rejected` (both backends), `test_fr11_answer_immutable`, `test_fr11_stale_corpus_guard` |
| FR-12 browse & stats | — | `test_fr12_coverage_matrix`, `test_fr12_stats_reports_substance_ratios`, `test_fr12_reading_aggregation_dedup` |
| FR-13 API | — | `test_fr13_edge_polish_unavailable_400`, `test_fr13_integrity_failure_500`, `test_fr13_asked_at_defaulted_at_edge`, `test_fr13_refusal_is_200` |
| FR-14 CLI | — | `test_fr14_ask_render_matches_stored_text`, `test_fr14_json_flag_emits_answerbody`, `test_fr14_exit_codes` |
| FR-15 determinism/hermeticity | D0(a)–(d) | `test_fr15_engine_has_no_io_imports` (AST scan of `engine/`) |

## How the suite runs

```bash
cd projects
uv run python ethos/evals/run.py        # scorecard: metric | baseline | value | gate | PASS/FAIL
uv run python ethos/evals/run.py --sweep            # (τ, κ) tradeoff curve
uv run python ethos/evals/run.py --write-baselines  # regenerate evals/baselines.json (by hand)
uv run pytest ethos/                    # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: validates the corpus (C-gates), builds the
  index, runs M1–M5, C-gates and D0 with offline components, prints every
  metric with its recorded baseline and gate, exits non-zero on any failure.
- `evals/test_gates.py` — one pytest per gate, names referencing FR ids
  (`test_gate_m1b_oblique_fr3`, `test_gate_m1gap_holdout_fr3`,
  `test_gate_m2a_near_refusal_fr4`, `test_gate_m3_citation_integrity_fr7`,
  `test_gate_m4_tamper_fr8_fr9`, `test_gate_m5_completeness_fr5_fr6`,
  `test_gate_c17_safeguard_matrix_fr10`, `test_gate_c18_normalization_fr2`,
  `test_gate_c19_baselines_current_fr1`, `test_gate_d0_determinism_fr15`, …).
- `evals/metrics.py` — metric functions + the five baseline implementations.
- `evals/independent_check.py` — the M3 checker; stdlib-only by gate (R1).
- `evals/corpus_gates.py` — C1–C20 predicates, shared by `run.py`, the gate
  tests and `ethos corpus validate`.
- `evals/faulty_polisher.py` — the M4 adversarial `ProsePolisher`.
- `evals/test_negative_controls.py` — R2.
- **Runtime budget:** everything is lexical and in-memory; ≈ 1,000 routings
  (including baselines), ≈ 600 compositions + verifications, the C17 matrix,
  and the three-way determinism run target **< 90 s** on a laptop.
- Hermetic: no network, no wall clock (timestamps come from fixtures), no
  randomness; the live LLM adapter is never imported on the eval path.

## What the evals cannot see (stated honestly)

Offline gates verify *structure, licensing, substance ratios, citation
integrity, routing, and non-suppressibility* — they cannot verify:

- **Theological and philosophical fidelity.** Whether a summary steelmans the
  tradition, whether a stance label is the best scholarly judgment, whether a
  `complicating` passage genuinely complicates. C12–C15 force *variety* and
  *grounding*, not *truth*. That risk is carried by the curation protocol
  (SCOPE D6/D20: Rapoport/Dennett steelmanning, AAR descriptive register,
  named-edition transcription with `transcription_checked`, curator notes) and
  by ordinary review of corpus diffs.
- **Transcription against the printed page.** M3 proves the render matches the
  committed file; only a human with the edition open proves the committed file
  matches the edition. `transcription_checked` is a claim, not a measurement.
- **True holdout discipline.** `oblique_holdout.json` is hash-frozen and gated
  (C19), and the gap gate prices memorisation — but the same person authored
  the questions and the lexicons, and no mechanism can prove a human did not
  look. The freeze makes tampering *visible* (the hash changes, the baselines
  must be re-derived, REVIEW.md must record the before/after); it does not
  make it impossible.
- **Whether the 24 topics are the right 24.** Coverage of the moral questions
  people actually ask is a product judgment (SCOPE D3, non-goal 7), measured
  only indirectly: the near-miss out-of-scope set proves the excluded families
  are *refused honestly*, not that excluding them was right.

The evals make the *mechanical* failure modes — fabricated citations,
corrupted quotes, missing coverage, silent verdict-synthesis, suppressed
safeguards, degenerate corpora, and metrics that cannot fail — impossible. The
human failure modes are documented, reviewable, and deliberately kept out of
the gates rather than laundered into fake metrics.
