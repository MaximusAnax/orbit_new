# Ethos — Evals

## What this product lives or dies on

1. **Routing with honest abstention** (FR-3/FR-4): plain-language moral
   questions — direct, colloquial, and oblique scenario phrasings — must land
   on the right topic in the fixed taxonomy, and out-of-scope questions must
   be refused rather than force-matched. If routing is unreliable the product
   degrades into a topic browser; if it stretches matches it becomes a
   confident mis-quoter, which is worse.
2. **Citation-faithful composition** (FR-1 floors, FR-6/7/8/9): every
   rendered answer must be complete (every covered tradition, well-formed
   perspective, agreement map), every quotation byte-faithful to a real
   committed passage with a correct locator and source line, and the pipeline
   must be tamper-proof against its own polish adapter. A single fabricated
   or corrupted citation destroys the product's reason to exist.

Everything else (CRUD, history, bookmarks, CLI rendering) is covered by
ordinary tests, not eval gates.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with offline components (`LexicalRouter`, `NullPolisher`, the
eval-only `FaultyPolisher`, in-memory store). No network, no clock, no
randomness (the engine has none). Metric M-numbers map to gates in
`evals/test_gates.py`; test names reference FR ids.

### M1 — Routing accuracy (capability 1)

Over the 240 in-scope fixture questions (24 topics × 10; tiers: 4 direct,
3 colloquial, 3 oblique per topic) and the 20 ambiguous questions, with
abstention active (an abstained in-scope question counts as wrong):

```
M1a = #(top1(q) = truth_topic(q)) / 240                       (acc@1, unambiguous set)
M1b = #(top1(q) = truth_topic(q)) / 72       over the oblique tier only
M1c = #(truth_topic(q) ∈ top3(q)) / 240                       (recall@3)
M1d = #(both truth topics ∈ top3(q)) / 20    over the ambiguous set
```

### M2 — Abstention quality (capability 1)

Over the 40 out-of-scope questions and the 240 in-scope questions:

```
M2a = #(router abstains) / 40                (correct refusals)
M2b = #(router abstains) / 240               (false refusals; lower is better)
```

M2a and M2b gate together: either alone is gameable (always-abstain aces
M2a; never-abstain aces M2b). τ and δ are committed in `data/router.json`
and were tuned only on the direct/colloquial tiers (see fixture protocol) —
the oblique tier and the OOS set are effectively held out from tuning.

### M3 — Citation integrity (capability 2)

Answer set: every in-scope and ambiguous fixture question that routes
(polish off), plus one forced-topic render per topic (24). Over all rendered
citations c in all answers:

```
M3 = #(c resolves to exactly one citation-table entry
       ∧ quote text byte-equals (NFC) the committed passage text
       ∧ rendered locator = passage.locator
       ∧ rendered source line matches the source record
       ∧ paraphrase renders carry the paraphrase label) / #citations
```

Gate = 1.0: integrity is the product's contract, the pipeline is
deterministic, and the store already refuses unverified answers — any value
below 1.0 is a real defect, not noise.

### M4 — Tamper detection (capability 2)

`evals/faulty_polisher.py` implements `ProsePolisher` with 40 scripted
cases over fixture answers: 20 **clean** (paraphrases mutable prose only,
preserves all immutable regions byte-for-byte) and 20 **mutated** (one
mutation each, covering all 14 classes at least once):

`invented_passage_id, altered_locator, paraphrased_quote, truncated_quote,
word_swap_in_quote, homoglyph_in_quote, dropped_marker, duplicated_marker,
swapped_tradition_header, invented_locator_in_prose,
deleted_quote_kept_marker, injected_extra_quote, altered_further_reading,
altered_safeguard_block`

```
M4a (recall) = #(mutated cases rejected by FR-8 verify) / 20
M4b (FPR)    = #(clean cases rejected) / 20
```

The same test asserts that every rejected case falls back to the
deterministic render byte-identically with `polish_fell_back = true`.

### M5 — Answer completeness & well-formedness (capability 2)

Over the 24 forced-topic renders. Expected sections =
Σ over topics of #positions(topic). A section is **well-formed** iff it has
a stance from the enum, a non-empty summary, ≥ 1 quote block whose marker
resolves (or, for reference-only-backed positions, ≥ 1 labeled paraphrase
citation), a locator + source line per quote, and ≥ 1 further-reading entry;
additionally the answer's agreement map must partition exactly its rendered
tradition ids, and `not_covered` must equal the set difference of the 10
traditions and the rendered ones.

```
M5 = #(well-formed sections, in answers whose map/not_covered checks pass) / #expected
```

### C0 — Corpus validation gates (pytest, boolean)

Each is a separate pytest asserting a property of the committed corpus
(FR-1); all must pass:

| Gate | Property |
|---|---|
| C1 | All files parse against the Pydantic schemas (strict mode) |
| C2 | `(topic_id, tradition_id)` unique across positions |
| C3 | Every position: ≥ 1 `core` passage ref, ≥ 1 further-reading entry |
| C4 | Every quoted passage's source: license ∈ {public_domain, cc0, cc_by}, named translator, translation year, URL |
| C5 | Every locator matches its source scheme's regex |
| C6 | Every quote ≤ 90 words and `transcription_checked = true`; paraphrase-only passages belong to `reference_only` sources |
| C7 | Floors: 10 traditions; ≥ 24 topics; ≥ 6 positions/topic; ≥ 12 positions/tradition |
| C8 | All cross-references resolve (passage→source, position→topic/tradition/passage, topic→safeguard/related) |
| C9 | Fixture integrity: every fixture question's truth topic exists; oblique-tier questions share zero stemmed content words with their topic's title or its 5 highest-weight keyword terms (mechanical steelman of the tier — `evals/fixtures/validate_fixtures.py`) |
| C10 | No keyword term is all-stopwords; `Tradition.order` is 1..10 |

### D0 — Determinism (plain pytest, no score)

(a) Route + compose the full fixture set twice in fresh processes: routing
JSON, `AnswerBody` JSON, and text renders byte-identical. (b) The 6 golden
answers regenerate byte-identically from the committed corpus
(`evals/fixtures/golden/`). (c) Rebuilding the BM25 index yields an
identical index serialization. Any diff fails the suite (FR-16).

## Fixture strategy

Everything is committed under `evals/fixtures/`. Questions are
**hand-authored** (there is no generator randomness to seed); mechanical
properties are enforced by the committed `validate_fixtures.py`, which runs
inside the suite (gate C9). **Ground truth never comes from the router under
evaluation** — it comes from the authoring protocol below.

| File | Contents | Ground truth |
|---|---|---|
| `routing_questions.json` | 240 entries `{id, text, truth_topic, tier: direct\|colloquial\|oblique, rationale}` — 10 per topic (4/3/3 by tier). Direct: near-canonical phrasings ("Is lying ever acceptable?"). Colloquial: informal, first-person ("told my friend her novel was great, it wasn't — am I a bad person?"). Oblique: scenario descriptions that never use the topic's title words or its top-5 keywords (enforced mechanically, C9) — e.g. "my brother asked if his wedding speech was funny; it put half the room to sleep" → `honesty_and_deception`. | Authoring protocol: (1) target topic fixed first; (2) question drafted per tier rules; (3) `validate_fixtures.py` enforces the oblique lexical constraint; (4) second-pass review confirms no other topic is a more natural home — questions with two defensible homes are moved to the ambiguous set; the `rationale` field records the review judgment. |
| `ambiguous_questions.json` | 20 entries `{id, text, truth_topics: [2 ids], rationale}` — genuinely dual questions ("should I report my father's tax fraud?" → `honesty_and_deception` + `duties_to_parents`). | Same protocol; dual-home judgment is the review outcome, recorded in `rationale`. |
| `oos_questions.json` | 40 entries `{id, text, kind: factual\|technical\|consumer\|small_talk\|non_moral_advice, rationale}` — e.g. "how do I fix a segfault in C?", "what's the capital of Mongolia?", "which laptop should I buy?". No moral-topic vocabulary by review. | By construction: none of the 24 topics addresses them; review confirms no topic is a defensible answer. |
| `polish_cases.json` | 40 entries `{id, question_ref, mode: clean\|mutation_class, params, expected: accept\|reject}` driving `FaultyPolisher`; all 14 mutation classes covered ≥ 1×; mutations are deterministic string operations on the envelope (positions/replacements committed in `params`). | By construction: `expected` follows from the FR-8 contract; clean cases are built by paraphrasing only mutable regions. |
| `golden/` | 6 full `AnswerBody` JSON snapshots + text renders for 6 canonical questions across 6 topics (incl. one sensitive topic exercising the safeguard block and one ambiguous question). Regenerated by `evals/fixtures/regenerate_golden.py` (committed); regeneration must be a no-op diff except when `composer_version` is bumped in the same commit. | The committed snapshot, reviewed at commit time |
| `data/corpus/` (the corpus itself) | The corpus is the fixture for C1–C8, M3, and M5. | Curation protocol (SCOPE D6/D20): steelman drafting, named-edition transcription checks (`transcription_checked`), curator notes; structural truth enforced by C-gates |

Overfitting honesty: the router's lexicons and the fixture questions are
authored by the same person, so tier-1/2 accuracy partly measures authoring
consistency. The oblique tier is the guard: C9 mechanically forbids lexical
overlap with the topic's title and strongest keywords, so M1b can only be
earned by genuine scenario-vocabulary coverage in the lexicons; τ/δ are
tuned on tiers 1–2 only. Lexicon edits that chase a failing oblique question
are visible in review (both files change in one commit).

## Naive baselines and gates

Baselines are computed by two reference implementations committed in
`evals/metrics.py`: `baseline_title_overlap` (rank topics by count of shared
stemmed tokens between the question and the topic's title + description
only; ties lexicographic; never abstains) and, for M3/M4, the pipeline with
FR-8 verification disabled. Baseline numbers below are the measured targets
to record at first eval run; ≈ values are design estimates and must be
re-derived in the same commit if fixture composition changes.

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1a acc@1 | title/description overlap | ≈ 0.40 (uniform random = 0.04) | **≥ 0.85** | Direct+colloquial tiers should be near-perfect for a real lexicon+BM25 router (that is 70% of the set); 0.85 leaves room for ~14 hard colloquial/oblique misses while sitting far above anything achievable without curated lexicons and phrase weighting. |
| M1b oblique acc@1 | same | ≈ 0.05 (C9 removes title/keyword overlap, so the baseline is near-random) | **≥ 0.70** | The honest hard case: scenario phrasings with zero strong-keyword overlap. 0.70 (≥ 51/72) is achievable only through deliberate scenario vocabulary in lexicons — the exact craft the product needs — while conceding that lexical routing has a ceiling here (embeddings are the live upgrade path). |
| M1c recall@3 | same | ≈ 0.55 | **≥ 0.95** | The UI shows top-3 alternates, so recall@3 is the "user can self-correct" bound; a correct router should almost never miss top-3 on in-scope questions (≤ 12 misses allowed). |
| M1d ambiguous both-in-top3 | same | ≈ 0.15 | **≥ 0.85** | Dual-topic questions must surface both homes (≥ 17/20); this fails routers that over-commit to a single dominant lexicon hit. |
| M2a OOS refusal | never abstain (τ = 0) | 0.00 | **≥ 0.90** | Refusing ≥ 36/40 clearly-out-of-scope questions is the floor for "honest front door"; 100% is not demanded because a few OOS items are adversarially near moral vocabulary. |
| M2b false refusal | always abstain scores 1.00 (and 0 on M1) | n/a (paired gate) | **≤ 0.02** | ≤ 4/240 in-scope refusals keeps the reject option from eating coverage; paired with M2a it pins τ into a genuinely useful band (Chow-style tradeoff made explicit). |
| M3 citation integrity | pipeline with FR-8 verification disabled, adversarial polish active | ≈ 0.55 on the M4 answer set; 1.0 only by luck otherwise | **= 1.0** | Deterministic pipeline over committed data — exactness is fair, and anything less is a defect (same rationale as ChessMentor M8). This is the product's non-negotiable contract. |
| M4a tamper recall | no verifier | 0.00 | **= 1.0** | Every one of the 14 mutation classes models a real LLM failure (paraphrased quotes, invented locators, dropped markers); missing any one leaves a hallucination channel open. |
| M4b tamper FPR | reject everything scores recall 1.0 | 1.00 for reject-all | **= 0.0** | Clean paraphrase-only polish must survive, or the polish feature is dead weight and the verifier is a tautology. |
| M5 completeness | first-draft corpus without floors/C-gates | ≈ 0.6 (typical draft-corpus audit: missing readings, missing quotes on reference-only positions) | **= 1.0** | Deterministic structural property of committed data + composer; the gate is what makes the floors real rather than aspirational. |
| C1–C10 | — | fail on any draft corpus | **all pass** | Schema/licensing/floor/fixture invariants; boolean by nature. |
| D0 | — | — | **byte-identical** | Workspace hermeticity rule (FR-16). |

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python ethos/evals/run.py        # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest ethos/                    # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: validates the corpus (C-gates), builds the
  router index, runs M1–M5 and D0 with offline components, prints the table
  with actual values (including the two baseline rows for M1a/M1b for
  context), exits non-zero on any gate failure.
- `evals/test_gates.py` — one pytest per gate
  (`test_gate_m1a_routing_acc_fr3`, `test_gate_m1b_oblique_fr3`,
  `test_gate_m2a_refusal_fr4`, `test_gate_m3_citation_integrity_fr8`,
  `test_gate_m4_tamper_fr8_fr9`, `test_gate_m5_completeness_fr6`,
  `test_gate_c4_licensing_fr1`, `test_gate_d0_determinism_fr16`, …); names
  reference FR ids so the FR → test mapping is auditable.
- `evals/metrics.py` — pure metric functions + the two baseline
  implementations, shared by both entry points.
- `evals/faulty_polisher.py` — the M4 adversarial `ProsePolisher`; imported
  only by the eval path.
- **Runtime budget:** everything is lexical and in-memory; the full suite
  (≈ 300 routings, ≈ 290 compositions + verifications, corpus validation,
  determinism double-run) targets **< 60 s** on a laptop.
- Hermetic: no network, no wall clock (timestamps come from fixtures), no
  randomness; the live LLM/embedding adapters are never imported on the eval
  path.

## What the evals cannot see (stated honestly)

Offline gates verify *structure, licensing, integrity, and routing* — they
cannot verify that a summary is theologically faithful, that a stance label
is the best scholarly judgment, or that a quote's transcription matches the
printed edition. Those risks are carried by the curation protocol (SCOPE
D6: steelman drafting per Rapoport/Dennett, AAR-style descriptive register;
D20: named-edition transcription with `transcription_checked`; complicating
passages; curator notes recording sources consulted) and by ordinary review
of corpus diffs. The evals make the *mechanical* failure modes — fabricated
citations, corrupted quotes, missing coverage, silent verdict-synthesis —
impossible; the human failure modes are documented, reviewable, and
deliberately kept out of the gates rather than laundered into fake metrics.
