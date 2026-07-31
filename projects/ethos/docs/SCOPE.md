# Ethos — Scope

## One-liner

Ask a moral question in plain language ("is it wrong to lie to spare someone's
feelings?") and get faithful, steelmanned answers from ten religious and
philosophical traditions side by side — every position grounded in named
public-domain primary texts, every quotation verbatim and machine-verified
against a committed corpus, every perspective ending in curated further
reading. Never a verdict, never a made-up citation.

## Problem statement

When the owner wonders about a real moral question — white lies, eating
animals, revenge, what he owes his parents — the available tools are all bad
in different ways. Search engines return SEO listicles. LLM chatbots return a
fluent synthesis that flattens traditions into mush and, notoriously,
fabricates citations (the failure mode that got lawyers sanctioned in *Mata v.
Avianca* (S.D.N.Y. 2023) is structural, not incidental). Primary texts are
freely available — the Sacred Books of the East, Project Gutenberg, the
Internet Sacred Text Archive — but they are indexed by book and verse, not by
moral question. There is no tool that takes a question-shaped input and
returns a comparative, citation-faithful, tradition-respecting answer.

Ethos is that tool: a curated comparative-ethics reference with a
question-shaped front door. The corpus is authored and committed; the engine
routes, retrieves, and assembles; nothing morally substantive is ever
generated.

The hard parts are:

- **A. Topic routing with honest abstention.** Free-text moral questions —
  direct ("Is lying wrong?"), colloquial ("told my friend her novel was great,
  it wasn't — bad person?"), or oblique scenario descriptions — must map to
  the right canonical topic in a fixed taxonomy, deterministically and
  offline. Equally important: questions the corpus cannot answer must be
  *refused* with a browsable topic list, never answered by stretching the
  nearest topic. The hard refusals are not "what's the capital of Mongolia" —
  they are *moral questions outside the taxonomy* ("is gene editing embryos
  wrong?", "do we owe future generations a livable climate?"), which are rich
  in exactly the vocabulary the router indexes. A router that guesses is worse
  than a search box.
- **B. Grounded composition with enforced citation integrity.** Each
  tradition's perspective must be assembled from curated position text and
  verbatim passage quotes with real locators, complete for every tradition
  that has a position, in a neutral side-by-side layout — and the pipeline
  must make hallucinated or corrupted citations *impossible by construction*,
  including when the optional LLM prose-polish adapter is active. A post-render
  verifier is the trust boundary: any answer whose citations do not resolve
  and match byte-for-byte is rejected before persistence.

Both get first-class eval gates (see EVALS.md), and both are measured by
instruments that are independent of the code they grade (EVALS M3, M1b′).

## Target user

The owner: one reflective adult using a terminal, asking a few questions a
week and reading the results like a well-edited reference page. Single local
profile; no accounts, no sync. The tool is a *reading companion*, not a
decision oracle, not pastoral or therapeutic counseling — that posture is
implemented behavior (neutral presentation, no verdicts, crisis-resource
safeguards on sensitive topics), not a disclaimer.

## User stories & acceptance criteria

**US-1 — Ask and compare.** As a user, I ask a moral question from the CLI and
get, in one screen-scrollable response, each tradition's position side by
side: a stance label, a steelmanned summary, the tradition's own reasoning,
verbatim quoted passages with citations, and further reading.
*Accept:* for a routed topic, every tradition with a curated position appears
(≥ 6 per topic by corpus floor), in the fixed canonical order; each
perspective has stance + summary + ≥ 1 quotation **or explicitly-labeled
paraphrase citation** with locator and source line + ≥ 1 further-reading
pointer (EVALS M5 gate = 1.0, over unfiltered *and* filtered renders);
response is produced in < 2 s offline; asking about a topic carrying
safeguards always renders the safeguard block first (FR-10, gate C17).

**US-2 — Trustworthy citations.** As a user, every quotation I see is the
verbatim text of a real passage in the corpus, attributed to a named work,
translator, and public-domain edition, with a stable URL — so I can check it.
*Accept:* citation integrity over the eval answer set is exactly 1.0 as
measured by an **independent checker** that re-parses the printed answer and
resolves it against the raw corpus files without touching engine code (M3);
`ethos show`/`GET /passages/{id}` reveals any passage's full record including
source, translator, translation year, license, and URL; answers that fail
verification are never persisted (store-level invariant, both backends).

**US-3 — Plain language works.** As a user, I don't need to know the
taxonomy; colloquial and scenario phrasings route correctly.
*Accept:* per-tier routing gates on the committed fixtures are met (direct
≥ 0.97, colloquial ≥ 0.85, oblique ≥ 0.70, held-out oblique ≥ 0.55, and the
visible-minus-held-out gap ≤ 0.15 — M1); recall@3 ≥ 0.95; the answer echoes
the matched topic and lists runner-up topics with scores so a mis-route is
visible and correctable via `--topic`.

**US-4 — Honest refusal.** As a user, when I ask something the corpus cannot
answer — including a genuine moral question outside the 24 topics — the tool
says so and shows me what it *can* answer, instead of forcing a match.
*Accept:* out-of-scope fixture questions are refused at ≥ 0.80 overall and
≥ 0.68 on the near-miss moral subset (M2a / M2a_near) while false refusals of
in-scope questions stay ≤ 0.05 (M2b); a refusal response carries the three
nearest topics with scores plus the browse hint; `ethos ask --topic <id>`
always bypasses routing so no question is ever dead-ended.

**US-5 — See the disagreement structure.** As a user, I can see at a glance
where traditions cluster and where they genuinely split.
*Accept:* every answer includes an agreement map that partitions exactly the
rendered traditions by stance (deontic vocabulary, FR-6); no synthesized
"overall answer" or ranking of traditions appears anywhere in the product
(asserted by test); intra-tradition splits are surfaced in the perspective's
own note, never averaged away; every answer prints the top-3 alternate topics
with scores, so dual-home questions ("should I report my father's tax
fraud?") show both homes rather than hiding one (M1d ≥ 0.85).

**US-6 — Go deeper.** As a user, each perspective points me to real further
reading (SEP/IEP entries, named books, full primary-text translations), and I
can pull a topic's whole reading list.
*Accept:* every position carries ≥ 1 further-reading entry with author/title/
year and kind (gate C3), and the reading lists are genuinely varied rather
than one link per tradition (gate C16: ≥ 120 distinct entries, none used by
> 10% of positions); `ethos reading <topic>` aggregates and de-duplicates
across traditions.

**US-7 — History that doesn't rot.** As a user, I can revisit any past
question and see exactly what I was shown, even after the corpus is edited or
the templates change.
*Accept:* answers are immutable snapshots stamped with `corpus_version` and
`composer_version` **and carry the exact plain-text render that was verified
and shown** (`Answer.rendered_text`); re-asking creates a new question row;
re-rendering an answer whose `composer_version` equals the current constant is
byte-identical to the stored text (D0), and an answer from an older
`composer_version` is served from `rendered_text` with a one-line note rather
than re-rendered under new templates.

**US-8 — Optional polish, same ground truth.** As a user, I can turn on the
LLM polish adapter for smoother connective prose, and nothing load-bearing
changes.
*Accept:* quotes, citation markers, locators, source lines, context notes,
stance labels, agreement map, further reading, tradition headers, and
safeguard blocks are byte-identical before and after polish (verified,
FR-8/FR-9); a polish output that violates the contract — or that fails to
parse back into an `AnswerBody` — is discarded and the deterministic render is
served, with `polish_fell_back = true` recorded; the tamper fixtures are
detected at recall 1.0 with 0 false positives (M4 gate); polish is never
active in tests or evals except through the eval-only `FaultyPolisher`.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-3/FR-4 are
hard part A; FR-1 (floors and substance invariants), FR-6/FR-7/FR-8/FR-9 are
hard part B.

- **FR-1 Corpus schema, loading, validation.** The committed corpus under
  `data/corpus/` (traditions, topics, sources, passages, positions; schema in
  DATA_MODEL.md) is parsed into Pydantic models and validated at `ethos init`
  and by `ethos corpus validate`. Validation has three layers, all enforced
  identically by the CLI and by the eval C-gates:

  1. **Structural** — all cross-references resolve; `(topic, tradition)` pairs
     are unique; every position has ≥ 1 `core` passage reference and ≥ 1
     further-reading entry; locators match their source's anchored
     locator-scheme regex; every *quoted* passage belongs to a source with
     license ∈ {`public_domain`, `cc0`, `cc_by`}, a named translator, a
     translation year and a URL; quotes are ≤ 90 words with
     `transcription_checked = true`; paraphrase-only passages belong to
     `reference_only` sources.
  2. **Loader fidelity** — the loader is **byte-preserving**: for every string
     field of every corpus record, the loaded value byte-equals the raw JSON
     string value. No normalization (NFC/NFKC, dash folding, whitespace
     collapsing, smart-quote substitution) happens at load time, anywhere.
     Additionally no corpus string field may contain the envelope sentinels
     `[[` or `]]`, and `Passage.text`/`Passage.paraphrase` may not contain
     U+201C/U+201D (the render's quote delimiters) — both keep the envelope
     and the printed render unambiguously parseable.
  3. **Content floors and substance invariants** — all 10 traditions present;
     ≥ 24 topics; ≥ 6 positions per topic; ≥ 12 positions per tradition; plus
     the anti-degeneracy invariants that keep the corpus from satisfying the
     floors trivially: no passage is the `core` reference of more than 3
     positions; distinct cited passages ≥ 6 × #topics; every topic carries
     ≥ 3 distinct stance values (≤ 2 named exception topics may carry 2);
     within a topic, pairwise stemmed-token Jaccard between position summaries
     < 0.5, and globally no two summaries share > 70% of their stemmed tokens;
     ≥ 30% of positions carry a `complicating` passage and every
     safeguard-carrying topic's positions carry a `complicating` passage or an
     `intra_tradition_note`; ≥ 80% of positions have ≥ 1 quoted (non-paraphrase)
     `core` passage and `reference_only` passages are ≤ 15% of all cited
     passages; ≥ 120 distinct further-reading entries with none appearing in
     > 10% of positions.

  `corpus_version` = SHA-256 over **every answer-determining committed file**
  — everything under `data/` (`corpus/**`, `router.json`, `safeguards.json`,
  `stopwords.txt`) — computed as described in DATA_MODEL; recorded in SQLite
  and stamped on every answer. The stale-corpus guard fires when any of them
  changes.

- **FR-2 Text normalization.** One deterministic pipeline shared by index
  build and query processing: Unicode NFKC → casefold → tokenize on
  non-alphanumerics → drop stopwords (committed `data/stopwords.txt`, ~250
  words in the van Rijsbergen (1979) lineage) → Porter stem (Porter 1980,
  implemented to the published algorithm). No external NLP dependency.
  Conformance is gated, not asserted: `evals/fixtures/stemmer/{voc.txt,
  output.txt}` commits a deterministic 500-pair slice (sorted order, not
  sampled) of Martin Porter's published vocabulary/output sample with its
  provenance and licence in a header comment, and gate C18 requires
  `stem(voc[i]) == output[i]` for every pair, plus a committed NFKC/casefold
  fixture covering the non-ASCII the corpus actually contains (curly
  apostrophes in "Qur'an", en-dashes in locator ranges, ligatures in older
  public-domain transcriptions). **Normalization never touches corpus content:**
  it applies only to router documents and queries. Passage text, locators, and
  source lines pass through the pipeline unmodified end to end.

- **FR-3 Lexical topic routing (hard part A).** Each topic contributes one
  *topic document*: title + description + `question_forms` + keyword lexicon
  (each keyword term repeated `weight` times — document expansion by
  anticipated queries, the doc2query idea (Nogueira et al., 2019) done by
  hand). Score of topic T for normalized query Q:
  `S(T) = BM25(Q, doc_T) + Σ_phrase w_phrase · weight(phrase)` where BM25 uses
  the Okapi formulation (Robertson et al., TREC-3, 1994) with k1 = 1.5,
  b = 0.75, `IDF(t) = ln(1 + (N − n_t + 0.5)/(n_t + 0.5))` over the N = 24
  topic documents, and the phrase term fires once per multi-word lexicon term
  whose stemmed token sequence occurs contiguously in the stemmed query
  (`w_phrase` committed in `data/router.json`, default 2.0). A query term
  occurring in no topic document (n_t = 0) takes `IDF = ln(1 + (N + 0.5)/0.5)`
  — the maximum — which is what FR-4's coverage signal exploits. Output:
  topics ranked by score, descending, ties broken lexicographically by topic
  id; topics scoring 0 are omitted from the ranking. Fully deterministic;
  index rebuild is byte-stable.

- **FR-4 Confidence, coverage, abstention (hard part A).** With top-3 raw
  scores `s1 ≥ s2 ≥ s3` (missing entries = 0):

  - `confidence = s1 / (s1 + s2 + s3)` when `s1 > 0`; `confidence = 0.0` when
    `s1 = 0` (all-stopword or wholly unmatched queries — the formula's 0/0
    case is defined away, not left to the implementer).
  - **Query coverage.** Let `Q*` be the distinct stemmed content terms of the
    query. `coverage(Q, T) = Σ_{t ∈ Q* ∩ terms(doc_T)} IDF(t) / Σ_{t ∈ Q*} IDF(t)`,
    and `0.0` when `Q*` is empty. Because unseen terms carry maximum IDF, a
    question built from vocabulary the taxonomy has never heard of ("gene",
    "embryo", "surveillance") scores low coverage even when it also contains
    high-scoring generic moral words ("wrong", "consent", "harm"). This is the
    simplest form of the unmatched-query-term signal from query-performance
    prediction (Cronen-Townsend et al., SIGIR 2002) and it is what makes
    near-miss moral refusals possible at all — a threshold on `s1` alone
    cannot separate them (EVALS M2, gate C20).
  - **Abstain** iff `s1 < τ` **or** `coverage(Q, T1) < κ`. τ and κ live in
    `data/router.json` with a committed `tuning_note`; they are tuned only on
    the direct and colloquial fixture tiers — the oblique tiers and the
    out-of-scope set are hash-frozen holdouts (EVALS C19).
  - An abstention returns outcome `refused_out_of_scope` with the 3 nearest
    topics + scores and a browse hint. Every answered question likewise prints
    its top-3 alternates with scores (US-5) — there is no separate "ambiguous"
    flag (non-goal 5).
  - `--topic <id>` (CLI) / `topic_id` (API) bypasses routing entirely; this
    valve means routing failure never blocks access to the corpus. This is the
    reject option of Chow (1970): trade coverage for correctness, explicitly.

- **FR-5 Perspective retrieval.** Given (topic, requested traditions): for
  each requested tradition with a position, that unique position, its passages
  ordered `core` → `supporting` → `complicating` and within a role by the
  committed list order, its further reading in committed order. The requested
  set is the caller's filter, else all 10 traditions in canonical
  `Tradition.order`. Two disjoint lists are returned alongside the rendered
  perspectives and are **never conflated**:
  - `not_covered` = requested traditions with **no curated position** on the
    topic ("the corpus has nothing here");
  - `filtered_out` = traditions that **have** a position on the topic but were
    excluded by the caller's filter ("you asked me to leave these out").

  In an unfiltered ask `filtered_out` is always empty. Nothing is improvised
  and nothing is silently omitted.

- **FR-6 Deterministic composition (hard part B).** The composer assembles —
  never writes — an `AnswerBody`: safeguard blocks (first); routing echo
  (matched topic, confidence, alternates, or the forced-topic form); one
  perspective per rendered tradition in canonical order (tradition header,
  stance label, curated summary, reasoning points with optional citation
  markers, quote blocks, optional intra-tradition note, further reading); an
  agreement map grouping the rendered traditions by stance in fixed enum
  order; `not_covered`; `filtered_out`; corpus/composer versions. Citation
  markers are `[C1]…[Cn]`, numbered in first-appearance order over the render,
  each resolving through the answer's citation table to exactly one passage
  id. Stance vocabulary (deontic: von Wright 1951; supererogation: Urmson
  1958): `obligatory, encouraged, permitted, context_dependent, discouraged,
  forbidden, contested, reframed`. Identical inputs give byte-identical output
  (no randomness anywhere in the engine; no seeds needed). The composer also
  emits the **envelope** (DATA_MODEL § Envelope), the single serialization
  that the polisher may see and the verifier checks.

- **FR-7 Citation & reading rendering.** Quote blocks render the passage text
  *verbatim* inside sentinel delimiters in the envelope (`[[I:quote:C1]] …
  [[/I:quote:C1]]`) and inside typographic quotes `“…”` in the plain-text
  render, followed by `— locator — Work, trans. Translator (year)` built from
  the source record, plus the curated one-to-two-sentence `context_note`
  (anti-proof-texting, decision D11). Sources with `license = reference_only`
  (e.g. hadith and Talmud material lacking a usable public-domain translation)
  are cited by locator with a curated paraphrase preceded by the exact label
  line `[paraphrase — no public-domain translation quoted]`; they never
  produce quote blocks, their source line is `Work — edition_note`, and
  gate C15 caps how much of the corpus may rest on them. Further-reading
  entries render author, title, year, kind (sep/iep/book/article/
  primary_translation), and URL when present. The full render grammar is
  specified in DATA_MODEL § Plain-text render, because the M3 checker parses
  it.

- **FR-8 Citation verification gate (hard part B).** After every composition —
  polished or not — the verifier runs over the **parsed-back** `AnswerBody`
  (not the raw envelope string) and its plain-text render, comparing against
  the corpus and against the pre-polish envelope:

  | id | check |
  |---|---|
  | (a) | **Marker correspondence.** The set of distinct markers appearing anywhere in the body equals the set of citation-table keys; every table key has **exactly one** quote block; every reasoning marker is a table key; markers are `C1..Cn` with no gaps, in first-appearance order. (Stated as sets plus a per-key quote count — the earlier "multiset equality" wording was ill-defined, since a marker legitimately appears in both a reasoning point and its quote block.) |
  | (b) | **Quote fidelity.** Every quote block's text equals the referenced passage's committed text after NFC + newline normalization; on the NullPolisher path it must be byte-identical without normalization. |
  | (c) | **Locator & source line.** Every rendered locator equals `passage.locator` byte-for-byte and every rendered source line equals the string derived from the source record. |
  | (d) | **Safeguards.** Every safeguard required by the topic is present, byte-identical to `data/safeguards.json`, and first in render order. |
  | (e) | **Polish-introduced text.** For each mutable region, changed spans are computed against the pre-polish envelope (deterministic token-level diff). Reject if any changed span matches the *prose-locator scan* pattern set (DATA_MODEL — deliberately narrower than the anchored validation regexes, and applied only to changed text so curated prose that legitimately says "read XIII.18 narrowly" passes), or if a mutable region's character length changes by more than ±40% (bulk rewriting / content injection). |
  | (f) | **Per-marker triple consistency.** For each marker, the quote/paraphrase text, `is_paraphrase`, the paraphrase label line, the locator, and the source line all come from the *same* passage record — so a correct quote printed under another passage's locator, a dropped paraphrase label, or a flipped `is_paraphrase` are all caught. |
  | (g) | **Further reading.** Each perspective's further-reading entries byte-equal the position's committed entries, in committed order. |
  | (h) | **Perspective identity.** Each perspective's `tradition_id` and rendered header match the corpus record, and perspectives appear in canonical `Tradition.order` restricted to the rendered set. |
  | (i) | **Structural immutables.** `stance`, `agreement_map`, `not_covered`, `filtered_out`, `context_note`, and the routing echo byte-equal their pre-polish values. |

  Fail ⇒ the answer is rejected; the store refuses to persist an answer with
  `verified = false` (repository-level invariant, tested on both backends).
  The verifier runs even with the null polisher — it also catches
  composer/corpus drift. Every mutation class in `polish_cases.json` maps to a
  named check and every check is exercised by ≥ 1 class (EVALS M4 table).

- **FR-9 Optional LLM polish adapter.** `ProsePolisher.polish(envelope: str)
  -> str` may rewrite *mutable regions only* (summary, reasoning text,
  intra-tradition note). The envelope grammar (DATA_MODEL § Envelope) marks
  every region immutable (`[[I:…]]`) or mutable (`[[M:…]]`); the live adapter
  instructs the model to preserve the immutable ones and FR-8 enforces it.
  Polish output is parsed back into an `AnswerBody` by the strict envelope
  parser; **an unparseable or structurally altered envelope is an FR-8
  violation**, not an exception. Any violation ⇒ discard polish, serve the
  deterministic render, set `polish_fell_back = true`. Verification and
  persistence always operate on the parsed-back body and its render, so no
  prose can reach the user that was not verified. Offline default
  `NullPolisher` is the identity. Live `LLMPolisher` activates only when
  `ETHOS_LLM_API_KEY` is set (`ETHOS_LLM_MODEL`, `ETHOS_LLM_BASE_URL`; default
  provider Anthropic); it is an optional extra, never imported by tests or
  evals.

- **FR-10 Sensitive-topic safeguards (implemented behavior).** Topics carry
  `safeguard_ids`. `suicide_and_self_harm` and `euthanasia_and_end_of_life`
  are `sensitive = true` and must include `crisis_resources` (euthanasia adds
  `not_medical_advice`); law-touching topics (`capital_punishment`,
  `obedience_and_civil_authority`, `theft_and_property`, `usury_and_lending`)
  carry `not_legal_advice`. The block renders **first** in every answer, API
  and CLI, text and JSON, and **cannot be suppressed by any option**. This is
  gated as a matrix, not a golden file (C17): for every safeguard-carrying
  topic, across {no filter, single-tradition filter, multi-tradition filter} ×
  {polish off, FaultyPolisher clean, FaultyPolisher mutated → fallback} ×
  {routed, `--topic` forced} × {text render, `AnswerBody` JSON}, the block is
  present, byte-identical to `data/safeguards.json`, first, and of the kinds
  the topic declares. Safeguard text is committed data covered by
  `corpus_version` and treated as an immutable region by FR-8(d).

- **FR-11 Question & answer persistence.** Every ask stores a `Question`
  (text, timestamp — supplied by the caller, never read from the clock —
  routing result, outcome) and, when answered, an immutable `Answer` snapshot:
  full `AnswerBody` JSON, the exact verified `rendered_text`, options,
  versions, verification and polish flags. Rows are never mutated; re-asking
  the same text (with or without different options) creates a new question and
  a new answer. Rendering a stored answer whose `composer_version` matches the
  current constant re-derives byte-identically from the body (D0); for an
  older `composer_version` the stored `rendered_text` is served verbatim with
  a one-line note (US-7).

- **FR-12 Browse & corpus stats.** List traditions (with summaries and key
  concepts) and topics (optionally filtered to one tradition); topic detail
  shows description, question forms, covered traditions with stances, and the
  reading list; passage detail view; `corpus stats` prints counts, the
  coverage matrix (topics × traditions), the substance statistics that FR-1
  layer 3 gates (passage reuse, stance spread, paraphrase share, reading-list
  breadth), and `corpus_version`.

- **FR-13 API.** FastAPI app per the sketch below; thin — validation, service
  calls, serialization only. Edge semantics are specified below the sketch.

- **FR-14 CLI.** Typer app per the sketch below; same service layer as the
  API; `ask` renders a readable plain-text answer (no color/markup
  dependencies), `--json` emits the raw `AnswerBody`.

- **FR-15 Determinism & hermeticity.** The engine is pure: no network, no
  filesystem, no clock reads (timestamps are inputs); no randomness exists
  anywhere in the engine, so no seeds are needed; all ties break
  lexicographically. Evals run exclusively with offline components
  (`LexicalRouter`, `NullPolisher`, the eval-only `FaultyPolisher`) and the
  in-memory store; **store-contract tests exercise both backends** (the
  `verified = false` rejection and the stale-corpus guard are SQLite-level
  invariants and must be tested there). Identical inputs ⇒ byte-identical
  routing, answers, and renders, across process boundaries, hash seeds, and
  locales (EVALS D0).

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision). The
   `AnswerBody` JSON is shaped so a later web page is a pure client.
2. **No verdicts, no synthesis, no ranking of traditions — ever.** This is a
   product commitment, not a deferral: the composer has no code path that
   produces an "overall answer" (asserted by test).
3. **No generated moral content.** The LLM adapter may only rephrase
   connective prose under FR-8 verification; positions, quotes, citations,
   and reading lists are curated data. There is no "ask the LLM what
   Buddhism thinks" fallback, even when the corpus has no position.
4. **No chat.** Single-shot question → answer, plus browse. No multi-turn
   dialogue, no follow-up context, no memory of previous questions inside a
   new one.
5. **No ambiguity flag.** An earlier draft carried a δ margin and an
   `ambiguous` boolean. It is cut: the mechanism was unmeasurable without a
   paired metric pinning δ (an implementation with δ = 0 passed every gate),
   and ~90% of its user value is delivered for free by always printing the
   top-3 alternates with scores (US-5, FR-4). Dual-home questions remain a
   *measured* routing property (M1d: both truth topics in top-3 ≥ 0.85).
6. **No full-text semantic search over passages, and no embedding router.**
   Routing targets topics only; passage discovery happens through positions
   and browse. An embedding-based router would need a network adapter that
   could never be evaluated hermetically, so it is not built this pass — only
   the `TopicRouter` Protocol, so it can be added without a rewrite.
7. **Fixed topic taxonomy (24 at launch); out-of-taxonomy moral questions are
   refused, not stretched.** Two families are deliberately excluded and both
   are named to the user in the refusal path and the browse view:
   (a) *modern-technology and bioethics* (AI training on creative work, gene
   editing, data privacy, workplace surveillance, climate duties to future
   generations, organ markets) — the classical primary texts do not address
   them directly and steelmanning tradition positions there needs secondary
   scaffolding beyond this pass; (b) *contested modern moral-political
   questions* (abortion, sexuality and same-sex relationships, gambling,
   immigration) — these are among the most-asked moral questions, and that is
   exactly why they are excluded at launch: each requires per-tradition
   internal-dispute mapping (multiple live positions per tradition, not one
   steelmanned mainline) that non-goal 8's one-position-per-cell model cannot
   represent without misrepresenting the traditions. Both families are the
   *source material* for the near-miss out-of-scope eval set (EVALS M2), so
   the refusal behaviour on them is measured, not assumed.
8. **One steelmanned mainline position per (topic, tradition)**, with an
   `intra_tradition_note` for major splits (e.g. act vs rule utilitarianism,
   Theravada vs Mahayana emphases). Denominational trees are out.
9. **English public-domain translations only for quoted text.** No original
   languages; no quoting of in-copyright translations (they appear only as
   further reading or `reference_only` citations). Archaic prose (KJV,
   Legge) is an accepted tradeoff — locators let the user find any modern
   edition.
10. **Not advice.** No pastoral, therapeutic, legal, or medical guidance; the
    safeguards of FR-10 are the implemented form of this posture.
11. **No corpus-editing UI.** The corpus is edited in git and checked by
    `ethos corpus validate` plus the eval gates.
12. **No multi-user, auth, or sync.** One local profile, one SQLite file.
13. **No bookmarks and no answer re-composition endpoint.** Both were in an
    earlier draft. History plus `ethos show` covers revisiting an answer, and
    a re-ask with different options is one command. The ~200 lines they cost
    are spent instead on the things the review showed were missing and are
    load-bearing: the independent M3 checker, the negative-control fixtures,
    the held-out oblique tier, the safeguard matrix, and FR-8 checks (f)–(i).
14. **Live adapters are optional extras** (LLM polish only): env-gated,
    excluded from tests and evals, no heavy deps in the core install.

## Architecture

```
projects/ethos/
  src/ethos/
    models.py            # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/              # PURE: no I/O, no clock, no network (CONVENTIONS)
      normalize.py       # FR-2: NFKC, casefold, tokenize, stopwords, Porter stemmer
      router.py          # FR-3/4: topic docs, BM25 + phrase bonus, confidence/coverage/τ/κ
      retrieve.py        # FR-5: position/passage/reading retrieval, canonical ordering
      compose.py         # FR-6/7: AnswerBody assembly, markers, agreement map, text render
      envelope.py        # FR-9: AnswerBody ⇄ envelope serialize/parse (deterministic)
      verify.py          # FR-8: checks (a)–(i) over the parsed-back body + render
    service.py           # application service: ask/render orchestration; owns the
                         #   store + adapters. NOT in engine/ — it performs I/O.
    adapters/
      router.py          # TopicRouter Protocol
      router_lexical.py  #   offline default: LexicalRouter (wraps engine.router)
      polisher.py        # ProsePolisher Protocol
      polisher_null.py   #   offline default: NullPolisher (identity)
      polisher_llm.py    #   live: LLMPolisher (optional extra, env-gated)
    store/
      repository.py      # Repository Protocol
      sqlite_repo.py     # SQLite backend (stdlib sqlite3), default ~/.ethos/ethos.db
      memory_repo.py     # in-memory backend for tests/evals
    api/                 # FastAPI app (FR-13)
    cli/                 # Typer app (FR-14)
  data/
    corpus/              # traditions.json, topics.json, sources.json,
                         #   passages/<tradition|shared>.json, positions/<tradition>.json
    router.json          # k1, b, w_phrase, tau, kappa, tuning_note
    safeguards.json      # crisis/not-medical/not-legal block texts
    stopwords.txt        # committed stopword list
  evals/                 # fixtures/, faulty_polisher.py, metrics.py, corpus_gates.py,
                         #   independent_check.py, baselines.json, run.py, test_gates.py
```

`service.py` sits outside `engine/` deliberately: it writes to the repository,
which is filesystem I/O, and CONVENTIONS forbids that inside `engine/`. The
engine returns composed objects; the service persists them.

### Adapter interfaces

| Interface | Offline (default, tests/evals) | Live (env-gated, optional extra) |
|---|---|---|
| `TopicRouter.route(question: str, index: TopicIndex) -> RoutingResult(ranked: list[TopicScore], confidence, coverage, abstained)` | `LexicalRouter` — FR-3/4 BM25 + lexicon + coverage; deterministic | *(none this pass — non-goal 6; the Protocol exists so an embedding router can be added later)* |
| `ProsePolisher.polish(envelope: str) -> str` | `NullPolisher` — identity; also eval-only `FaultyPolisher` (evals/) applying scripted region operations for M4 | `LLMPolisher` — rewrites mutable regions via the configured LLM (`ETHOS_LLM_API_KEY`, `ETHOS_LLM_MODEL`, `ETHOS_LLM_BASE_URL`; default provider Anthropic); output always parsed back and re-verified (FR-8/FR-9) |

The corpus and the store are not adapters: the corpus is committed data
(loaded read-only at init), and persistence follows the workspace Repository
pattern.

### API sketch (FastAPI)

```
GET  /health
GET  /corpus/stats                    # counts, coverage matrix, substance stats, corpus_version
GET  /traditions                      GET /traditions/{id}
GET  /topics?tradition=               GET /topics/{id}
GET  /topics/{id}/reading             # aggregated, de-duplicated further reading
GET  /passages/{id}
POST /questions                       # {text, asked_at?, traditions?, topic_id?, polish?}
                                      #   → question + answer, or refusal payload (FR-4)
GET  /questions?limit=&offset=        GET /questions/{id}
GET  /answers/{id}                    # immutable AnswerBody snapshot
GET  /answers/{id}/text               # the verified render (stored)
```

**Edge semantics** (specified so no implementer has to guess):

1. `polish = true` with no `ETHOS_LLM_API_KEY` configured ⇒ **HTTP 400**,
   error code `polish_unavailable`. Never a silent fall-through to
   `NullPolisher`. CLI equivalent: exit code 2 with the same message.
2. FR-8 verification failure **on the NullPolisher path** ⇒ **HTTP 500**,
   error code `integrity_failure`, body naming the failing check id and
   passage/marker. This is a corpus or composer bug, not user error; nothing
   is persisted. CLI equivalent: exit code 3. (Polished-path failures are not
   errors: they fall back per FR-9 and return 200 with
   `polish_fell_back = true`.)
3. `asked_at` is **optional** at the API and CLI edge and defaulted there with
   `datetime.now(UTC)`; the engine never reads a clock (FR-15).
4. Forced-topic asks (`topic_id` / `--topic`) carry
   `AnswerBody.routing = {topic_id, forced: true, confidence: null,
   alternates: []}`, and `Question.routing = null`.
5. A refusal is **HTTP 200** with `outcome = refused_out_of_scope` and the
   refusal payload (nearest 3 topics with scores, browse hint, and the
   non-goal-7 note naming the excluded families) — a refusal is a successful
   answer to a question the corpus cannot address, not an error.

### CLI sketch (Typer)

```
ethos init                                        # create DB, load + validate corpus
ethos ask "TEXT" [--tradition ID]... [--topic ID] [--polish] [--json]
ethos topics [--tradition ID]                     ethos topic <ID>
ethos traditions                                  ethos tradition <ID>
ethos reading <TOPIC_ID> [--tradition ID]
ethos history [--limit N]                         ethos show <QUESTION_ID> [--tradition ID]
ethos corpus stats                                ethos corpus validate
```

## Key design decisions & assumptions

1. **Curation over generation (locked with the owner).** The corpus is the
   product; the engine is retrieval + assembly. This is the
   retrieval-augmented pattern (Lewis et al., NeurIPS 2020) taken to its
   deterministic limit: the "generator" is a template assembler, so every
   output sentence is *attributable to an identified source* in the sense of
   the AIS framework (Rashkin et al., 2021) by construction. Hallucinated
   citations — the *Mata v. Avianca* failure mode — are prevented
   structurally (FR-8), not by prompt engineering.
2. **The ten traditions (locked list)** span the major living religious
   families and the standard normative-ethics triad — deontology,
   consequentialism, virtue ethics — that has organized the field since
   Anscombe's "Modern Moral Philosophy" (1958): Christianity, Islam, Judaism
   (Abrahamic); Buddhism, Hinduism (Dharmic); Confucianism (East Asian);
   Stoicism (Greco-Roman); Kantian deontology, utilitarianism, virtue ethics
   (modern Western frameworks; virtue ethics anchored in Aristotle). Each
   tradition record carries a neutral summary and key concepts (e.g. ahimsa,
   phronesis, ren, zakat) with one-line glosses.
3. **Topic taxonomy: 24 topics, chosen where the primary texts genuinely
   speak.** Selection criteria: (a) an enduring moral question a person
   actually asks; (b) ≥ 6 traditions with citable primary-text material;
   (c) answerable without modern secondary scaffolding. Launch list:
   `honesty_and_deception, promise_keeping_and_oaths, killing_and_self_defense,
   war_and_peace, capital_punishment, suicide_and_self_harm,
   euthanasia_and_end_of_life, animals_and_diet, wealth_and_generosity,
   theft_and_property, usury_and_lending, forgiveness_and_revenge,
   anger_and_hatred, duties_to_parents, marriage_and_fidelity, divorce,
   hospitality_and_strangers, speech_and_gossip, intoxicants_and_sobriety,
   pride_and_humility, envy_and_contentment, obedience_and_civil_authority,
   courage_and_fear, judging_others`. What is *excluded* is a product
   statement, not an oversight — see non-goal 7. The count is the scope valve
   (D17), and firing it carries an eval obligation (EVALS § scope valve).
4. **Public-domain translation policy.** Quoted text comes only from named
   public-domain (or CC0/CC-BY) editions, recorded per source with
   translator and year: Bible — KJV (1611) and, for the Judaism voice,
   JPS Tanakh (1917); Qur'an — Pickthall, *The Meaning of the Glorious
   Koran* (1930; US public domain); Aquinas — *Summa Theologica*, trans.
   Fathers of the English Dominican Province (1911–1925); Augustine — *On
   Lying* in the Nicene and Post-Nicene Fathers (Schaff ed., 1887);
   Dhammapada — Müller (Sacred Books of the East X, 1881); Pali suttas —
   T.W. & C.A.F. Rhys Davids, *Dialogues of the Buddha* (1899–1921);
   Bhagavad Gita — Telang (SBE VIII, 1882); Manusmriti — Bühler (SBE XXV,
   1886); Analects/Mencius/Great Learning — Legge, *Chinese Classics*
   (1861–1895); Epictetus — Carter (1758)/Long (1877); Marcus Aurelius —
   Long (1862); Seneca — Gummere (1917–1925) and Stewart, *Of Anger* (1900);
   Kant — Abbott, *Fundamental Principles of the Metaphysic of Morals* and
   *On a Supposed Right to Tell Lies from Benevolent Motives* (1873);
   Bentham — *IPML* (1789/1823); Mill — *Utilitarianism* (1863), *On
   Liberty* (1859); Sidgwick — *Methods of Ethics*; Aristotle — Ross,
   *Nicomachean Ethics* (1925, now US public domain) or Peters (1893);
   Plato — Jowett (1871). Stable URLs point at Project Gutenberg,
   sacred-texts.com, and the Chinese Text Project.
5. **Where no public-domain translation exists, cite — don't quote.**
   Hadith (e.g. Sahih Muslim 2605 on the permitted untruths for
   reconciliation) and most Talmud (e.g. Ketubot 16b–17a, Hillel vs Shammai
   on praising the bride; Bava Metzia 23b–24a on permitted deviations from
   truth) have no usable PD English rendering, so their sources carry
   `license = reference_only`: locator + curated paraphrase, explicitly
   labeled (FR-7). Honesty about translation provenance is part of citation
   integrity — and because a paraphrase is authored prose rather than a
   transcribed quotation, FR-1 layer 3 caps it: ≥ 80% of positions must rest
   on a real quoted `core` passage and `reference_only` passages may be ≤ 15%
   of all cited passages. Without that cap the product's premise (verbatim
   quotation from named editions) could evaporate while every gate stayed
   green.
6. **Steelman curation protocol.** Every position is drafted from within the
   tradition's own reasoning, following the principle of interpretive
   charity and Rapoport's rules as codified by Dennett (*Intuition Pumps*,
   2013: restate the position so its holders would accept it). Presentation
   follows the American Academy of Religion's religious-literacy guidelines
   (Moore, 2010): descriptive, not devotional and not debunking. Every
   position records a `curator_note` naming what was consulted; major
   internal disagreement goes in `intra_tradition_note` (e.g. Augustine and
   Aquinas's absolute prohibition of lying vs. broader Christian views;
   act vs rule utilitarianism).
7. **No synthesis, by design.** Comparative-religion scholarship warns
   against flattening traditions into one perennial message (Prothero, *God
   Is Not One*, 2010 — the corrective to the perennialism of Huston Smith's
   *The World's Religions*, 1958). The agreement map therefore only *groups
   stances*; it never merges texts or emits a combined answer. Genuine
   convergences (e.g. the reciprocity rule in Matthew 7:12, Analects
   15.23–24 (shu), and Shabbat 31a (Hillel)) surface naturally as stance
   clusters plus per-tradition passages.
8. **Stance vocabulary from deontic logic.** `obligatory / permitted /
   forbidden` are the classical deontic categories (von Wright, "Deontic
   Logic", *Mind*, 1951); `encouraged` covers the supererogatory (Urmson,
   "Saints and Heroes", 1958); `discouraged` is its mirror;
   `context_dependent` marks positions whose verdict is explicitly
   circumstance-relative (e.g. Manusmriti 4.138 on pleasant truth);
   `contested` marks deep internal splits; `reframed` marks traditions that
   reject the question's framing (e.g. virtue ethics answering "what would
   the practically wise person do" rather than "is X permitted" — Aristotle,
   NE II.6, VI.5). The stance is a curation judgment, recorded with the
   reasoning that supports it — and FR-1 layer 3 requires ≥ 3 distinct
   stances per topic, because a taxonomy in which every tradition forbids
   everything is not a comparative reference, it is a template.
9. **Lexical routing as the offline default.** BM25 (Robertson et al.,
   Okapi at TREC-3, 1994) over curated, hand-expanded topic documents plus
   Porter stemming (1980) is deterministic, inspectable, dependency-free,
   and hermetic — the workspace requirements. The craft moves into the
   lexicons: keywords must cover scenario vocabulary ("compliment",
   "feelings", "cover for"), not just topic names; the oblique fixture tiers
   exist to force exactly that (EVALS).
10. **Abstention needs two signals, not one.** A single score threshold τ
    cannot separate "moral question about a topic we do not carry" from
    "moral question about a topic we do carry, phrased unusually" — both
    score well on generic moral vocabulary. FR-4 therefore pairs τ with an
    IDF-weighted query-coverage floor κ, which fires precisely when the
    query's *distinctive* terms are absent from the taxonomy. This is what
    makes Chow's (1970) reject option implementable here, and the eval set is
    composed to prove it (≥ 25 of the 40 out-of-scope questions must
    out-score the median in-scope direct question — gate C20 — so a null
    `abstain iff s1 = 0` rule cannot pass M2).
11. **Anti-proof-texting rules, now gated.** Quoting scripture out of context
    is a named failure mode in biblical studies. Structural mitigations:
    quotes capped at 90 words; every passage carries a curated
    `context_note`; positions should cite `complicating` passages that cut
    against their own summary — e.g. a Christianity-on-honesty position
    quoting Matthew 5:37 *and* noting the midwives of Exodus 1. Previously
    this was a convention with no enforcement; FR-1 layer 3 now requires
    ≥ 30% of positions to carry a `complicating` passage and every
    safeguard-carrying topic's positions to carry one or an
    `intra_tradition_note`.
12. **Sensitive-topic safeguards are implemented behavior** (workspace
    quality bar): the crisis-resources block (988 Suicide & Crisis Lifeline;
    Befrienders Worldwide; findahelpline.com) is data, rendered first,
    non-suppressible, and verified as an immutable region (FR-8/FR-10). The
    claim "cannot be disabled by any flag" is now a matrix gate (C17) rather
    than a sentence.
13. **Answers are immutable snapshots that include their render.** Corpus
    edits must not rewrite what the user was shown (US-7), so answers embed
    the full `AnswerBody`, the exact verified `rendered_text`, plus
    `corpus_version` (SHA-256 over all of `data/`) and `composer_version`
    (bumped on template changes, with golden fixtures regenerated in the same
    commit). Storing the render is what makes US-7 literally true across a
    `composer_version` bump; it also gives the M3 checker a printed artifact
    to parse independently.
14. **Corpus format: JSON, one positions/passages file per tradition.**
    Diff-friendly, stdlib-parseable, and shardable for curation. Files are
    the source of truth; `ethos init` loads them into read-only SQLite
    tables for uniform querying. The loader is byte-preserving by contract
    and by gate (FR-1 layer 2) — a loader that silently NFC-folds a quote or
    normalizes an en-dash in a locator would make the corpus and the printed
    citation disagree with the printed edition while every internal check
    still passed, which is the exact failure the product exists to prevent.
15. **English-only UI and corpus.** Locators are edition-independent
    (chapter:verse, Bekker, Academy pagination, folio), so any modern
    translation can be consulted alongside; original-language texts are out
    of scope.
16. **No randomness anywhere.** Routing, composition, and rendering are
    deterministic functions; ties break lexicographically; no seed plumbing
    is needed. Timestamps are caller inputs (edges may call `datetime.now`,
    the engine may not). Determinism is gated across process boundaries with
    differing `PYTHONHASHSEED` and locale (EVALS D0).
17. **Line budget & scope valves.** Estimate: models ~260; engine ~1,160
    (normalize ~190, router ~300, retrieve ~90, compose ~300, envelope ~130,
    verify ~260 — verify grew with FR-8 (f)–(i)); service ~120; adapters ~130
    core + ~90 optional extra; store ~320; API ~220; CLI ~280 ⇒ **src ≈ 2,500**
    (+90 extra). Tests ~480, evals ~880 (metrics ~300, corpus_gates ~200,
    independent_check ~110, faulty_polisher ~110, test_gates ~110, run ~50)
    ⇒ **total ≈ 3,950**, inside the 2–4k mandate. The corpus (~150–200
    positions, ~220 passages, ~40 sources, ~180 further-reading references of
    which ≥ 120 distinct) is data, not code, and is the dominant authoring
    effort — planned as such.
    Bookmarks, the re-compose endpoint, the ambiguity flag, and the embedding
    router were cut to pay for the eval rework (non-goals 5, 6, 13). Valves,
    in order, each with the eval obligation attached: topic count 24 → 18
    (fixtures shrink with it, all baselines re-derived and the M1 thresholds
    raised by the measured baseline delta, same commit — EVALS § scope valve);
    then drop `ethos reading` aggregation; then drop the live LLMPolisher
    (the trust boundary is exercised by `FaultyPolisher` regardless).
18. **Polish is provider-agnostic and structurally distrusted.** The live
    polisher gets an envelope with explicitly marked immutable regions and is
    *assumed adversarial*: FR-8 re-verifies everything after every polish over
    the parsed-back body, and M4's `FaultyPolisher` (25 mutation classes, each
    mapped to a named FR-8 check) gates detection at recall 1.0 / FPR 0.
    Fallback is silent-safe (deterministic render + `polish_fell_back` flag),
    so a flaky LLM can never degrade integrity, only prose.
19. **Assumption: single user, no auth**; SQLite at `~/.ethos/ethos.db`
    (path configurable via `ETHOS_DB_PATH`); in-memory store for evals; both
    backends covered by the store-contract tests.
20. **Assumption: corpus curation is trusted input — but not unboundedly.**
    Evals verify structure, licensing, floors, substance ratios, and citation
    integrity mechanically; they cannot verify theological fidelity offline.
    That residual risk is carried by the curation protocol (D6), the
    `transcription_checked` flag, the complicating-passage convention, and
    curator notes — stated honestly in EVALS.md ("what the evals cannot see").
    What is *no longer* carried by trust alone is corpus degeneracy: FR-1
    layer 3 exists because a corpus of ten passages, one stance, and one
    reading link per tradition would otherwise have satisfied every gate.
