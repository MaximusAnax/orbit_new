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
  offline. Equally important: out-of-scope questions ("should I buy a Tesla",
  "how do I fix this SQL error") must be *refused* with a browsable topic
  list, never answered by stretching the nearest topic. A router that guesses
  is worse than a search box.
- **B. Grounded composition with enforced citation integrity.** Each
  tradition's perspective must be assembled from curated position text and
  verbatim passage quotes with real locators, complete for every tradition
  that has a position, in a neutral side-by-side layout — and the pipeline
  must make hallucinated or corrupted citations *impossible by construction*,
  including when the optional LLM prose-polish adapter is active. A post-render
  verifier is the trust boundary: any answer whose citations do not resolve
  and match byte-for-byte is rejected before persistence.

Both get first-class eval gates (see EVALS.md).

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
perspective has stance + summary + ≥ 1 quotation with locator and source line
+ ≥ 1 further-reading pointer (EVALS M5 gate = 1.0); response is produced in
< 2 s offline; asking about a sensitive topic (e.g. suicide) always renders
the crisis-resources block first (FR-10).

**US-2 — Trustworthy citations.** As a user, every quotation I see is the
verbatim text of a real passage in the corpus, attributed to a named work,
translator, and public-domain edition, with a stable URL — so I can check it.
*Accept:* citation integrity over the full eval answer set is exactly 1.0
(M3 gate); `ethos show`/`GET /passages/{id}` reveals any passage's full
record including source, translator, translation year, license, and URL;
answers that fail verification are never persisted (store-level invariant).

**US-3 — Plain language works.** As a user, I don't need to know the
taxonomy; colloquial and scenario phrasings route correctly.
*Accept:* routing accuracy on the committed 240-question fixture set meets
the M1 gates (acc@1 ≥ 0.85 overall, ≥ 0.70 on the oblique tier, recall@3
≥ 0.95); the answer echoes the matched topic and lists runner-up topics so a
mis-route is visible and correctable via `--topic`.

**US-4 — Honest refusal.** As a user, when I ask something the corpus cannot
answer, the tool says so and shows me what it *can* answer, instead of
forcing a match.
*Accept:* out-of-scope fixture questions are refused at ≥ 0.90 (M2a) while
false refusals of in-scope questions stay ≤ 0.02 (M2b); a refusal response
carries the three nearest topics with scores plus the browse hint; `ethos ask
--topic <id>` always bypasses routing so no question is ever dead-ended.

**US-5 — See the disagreement structure.** As a user, I can see at a glance
where traditions cluster and where they genuinely split.
*Accept:* every answer includes an agreement map that partitions exactly the
rendered traditions by stance (deontic vocabulary, FR-6); no synthesized
"overall answer" or ranking of traditions appears anywhere in the product
(asserted by test); intra-tradition splits are surfaced in the perspective's
own note, never averaged away.

**US-6 — Go deeper.** As a user, each perspective points me to real further
reading (SEP/IEP entries, named books, full primary-text translations), and I
can pull a topic's whole reading list or bookmark an answer to return to.
*Accept:* every position carries ≥ 1 further-reading entry with author/title/
year and kind (corpus gate C3); `ethos reading <topic>` aggregates and
de-duplicates across traditions; bookmarks persist with optional notes and
can be scoped to one tradition's perspective.

**US-7 — History that doesn't rot.** As a user, I can revisit any past
question and see exactly what I was shown, even after the corpus is edited.
*Accept:* answers are immutable snapshots stamped with `corpus_version` and
`composer_version`; re-asking creates a new answer row; rendering a stored
answer is byte-identical across runs (D0 gate); history lists questions with
their routed topic and date.

**US-8 — Optional polish, same ground truth.** As a user, I can turn on the
LLM polish adapter for smoother connective prose, and nothing load-bearing
changes.
*Accept:* quotes, citation markers, locators, further reading, and safeguard
blocks are byte-identical before and after polish (verified, FR-8/FR-9); a
polish output that violates the contract is discarded and the deterministic
render is served, with `polish_fell_back = true` recorded; the tamper
fixtures are detected at recall 1.0 with 0 false positives (M4 gate); polish
is never active in tests or evals.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-3/FR-4 are
hard part A; FR-1 (floors), FR-6/FR-7/FR-8/FR-9 are hard part B.

- **FR-1 Corpus schema, loading, validation.** The committed corpus under
  `data/corpus/` (traditions, topics, sources, passages, positions,
  safeguards; schema in DATA_MODEL.md) is parsed into Pydantic models and
  validated at `ethos init` and by `ethos corpus validate`: all
  cross-references resolve; `(topic, tradition)` pairs are unique; every
  position has ≥ 1 `core` passage reference and ≥ 1 further-reading entry;
  every *quoted* passage belongs to a source with license ∈ {public_domain,
  cc0, cc_by}, a named translator, and a translation year; quotes are ≤ 90
  words with `transcription_checked = true`; locators match their source's
  locator-scheme regex. Content floors: all 10 traditions present; ≥ 24
  topics; ≥ 6 positions per topic; ≥ 12 positions per tradition.
  `corpus_version` = SHA-256 over the canonical (sorted-keys, LF) JSON of all
  corpus files; recorded in SQLite and stamped on every answer.
- **FR-2 Text normalization.** One deterministic pipeline shared by index
  build and query processing: Unicode NFKC → casefold → tokenize on
  non-alphanumerics → drop stopwords (committed `data/stopwords.txt`,
  ~250 words in the van Rijsbergen (1979) lineage) → Porter stem (Porter
  1980, implemented to the published algorithm and unit-tested against
  Martin Porter's published vocabulary/output sample pairs). No external NLP
  dependency.
- **FR-3 Lexical topic routing (hard part A).** Each topic contributes one
  *topic document*: title + description + `question_forms` + keyword lexicon
  (each keyword term repeated `weight` times — document expansion by
  anticipated queries, the doc2query idea (Nogueira et al., 2019) done by
  hand). Score of topic T for normalized query Q:
  `S(T) = BM25(Q, doc_T) + Σ_phrase w_phrase · weight(phrase)` where BM25 uses
  the Okapi formulation (Robertson et al., TREC-3, 1994) with k1 = 1.5,
  b = 0.75, `IDF(t) = ln(1 + (N − n_t + 0.5)/(n_t + 0.5))`, and the phrase
  term fires once per multi-word lexicon term whose stemmed token sequence
  occurs contiguously in the stemmed query (`w_phrase` committed in
  `data/router.json`, default 2.0). Output: topics ranked by score,
  descending, ties broken lexicographically by topic id. Fully deterministic;
  index rebuild is byte-stable.
- **FR-4 Confidence, ambiguity, abstention (hard part A).** With top-3 raw
  scores `s1 ≥ s2 ≥ s3`: `confidence = s1 / (s1 + s2 + s3)` (1.0 when only
  one topic scores > 0). **Abstain** iff `s1 < τ`; **ambiguous** iff
  `s1 ≥ τ` and `(s1 − s2)/s1 < δ`. τ and δ live in `data/router.json`
  (defaults recorded there; tuned only on the direct/colloquial fixture
  tiers, never on the oblique tier — see EVALS). An abstention returns
  outcome `refused_out_of_scope` with the 3 nearest topics + scores and a
  browse hint; an ambiguous route answers the top topic and lists the
  alternates prominently. `--topic <id>` (CLI) / `topic_id` (API) bypasses
  routing entirely; this valve means routing failure never blocks access to
  the corpus. This is the reject option of Chow (1970): trade coverage for
  correctness, explicitly.
- **FR-5 Perspective retrieval.** Given (topic, tradition): the unique
  position, its passages ordered `core` → `supporting` → `complicating` and
  within a role by passage id, its further reading in committed order.
  Traditions without a curated position on the topic are listed by id under
  `not_covered` — never improvised, never silently omitted. Tradition
  inclusion respects the caller's filter, else all covered traditions in
  canonical order (`Tradition.order`).
- **FR-6 Deterministic composition (hard part B).** The composer assembles —
  never writes — an `AnswerBody`: optional safeguard blocks; routing echo
  (matched topic, confidence, alternates, ambiguous flag); one perspective
  per tradition (stance label, curated summary, reasoning points with
  optional citation markers, quote blocks, optional intra-tradition note,
  further reading); an agreement map grouping the rendered traditions by
  stance in fixed enum order; `not_covered`; corpus/composer versions.
  Citation markers are `[C1]…[Cn]`, numbered in first-appearance order, each
  resolving through the answer's citation table to a passage id. Stance
  vocabulary (deontic: von Wright 1951; supererogation: Urmson 1958):
  `obligatory, encouraged, permitted, context_dependent, discouraged,
  forbidden, contested, reframed`. Identical inputs give byte-identical
  output (no randomness anywhere in the engine; no seeds needed).
- **FR-7 Citation & reading rendering.** Quote blocks render the passage text
  *verbatim* inside sentinel delimiters (`[[Q:C1]] … [[/Q:C1]]` in the
  internal envelope; typographic quotes in the final text render), followed
  by `locator — Work, trans. Translator (year)` built from the source record,
  plus the curated one-to-two-sentence `context_note` (anti-proof-texting,
  decision D11). Sources with `license = reference_only` (e.g. hadith and
  Talmud material lacking a usable public-domain translation) are cited by
  locator with a curated paraphrase explicitly labeled `paraphrase — no
  public-domain translation quoted`; they never produce quote blocks.
  Further-reading entries render author, title, year, kind (sep/iep/book/
  article/primary_translation), and URL when present.
- **FR-8 Citation verification gate (hard part B).** After every composition
  — polished or not — the verifier checks the envelope: (a) every citation
  marker resolves to exactly one citation-table entry and vice versa
  (multiset equality); (b) every quote block's text is byte-identical
  (after NFC + newline normalization) to the referenced passage's committed
  text; (c) every rendered locator and source line matches the corpus
  record; (d) safeguard blocks required by the topic are present and
  byte-identical to `data/safeguards.json`; (e) no locator-shaped string
  (per-scheme regexes) appears in mutable prose outside the citation table.
  Fail ⇒ the answer is rejected; the store refuses to persist an answer with
  `verified = false` (repository-level invariant). The verifier runs even
  with the null polisher — it also catches composer/corpus drift.
- **FR-9 Optional LLM polish adapter.** `ProsePolisher.polish(envelope) ->
  str` may rewrite *connective prose only* (summary and reasoning phrasing).
  The envelope marks immutable regions (quote blocks, citation markers,
  locator/source lines, further reading, safeguard blocks, headers); the
  live adapter instructs the model to preserve them and the verifier (FR-8)
  enforces it: any violation ⇒ discard polish, serve the deterministic
  render, set `polish_fell_back = true`. Offline default `NullPolisher` is
  the identity. Live `LLMPolisher` activates only when `ETHOS_LLM_API_KEY`
  is set (provider endpoint/model via `ETHOS_LLM_MODEL`,
  `ETHOS_LLM_BASE_URL`; default provider Anthropic); it is an optional
  extra, never imported by tests or evals.
- **FR-10 Sensitive-topic safeguards (implemented behavior).** Topics carry
  `safeguard_ids`. `suicide_and_self_harm` ⇒ the crisis-resources block (988
  Suicide & Crisis Lifeline (US); Befrienders Worldwide, befrienders.org;
  findahelpline.com) renders *first* in every answer, API and CLI, and
  cannot be disabled by any flag (tested). `euthanasia_and_end_of_life` ⇒
  crisis block + not-medical-advice note; topics touching law (e.g.
  `capital_punishment`, `obedience_and_civil_authority`) ⇒ not-legal-advice
  note. Safeguard text is committed data; the verifier treats it as an
  immutable region.
- **FR-11 Question & answer persistence.** Every ask stores a `Question`
  (text, timestamp — supplied by the caller, never read from the clock —
  routing result, outcome) and, when answered, an immutable `Answer`
  snapshot (full `AnswerBody` JSON, options, versions, verification and
  polish flags). Re-asking or re-composing (`POST /questions/{id}/answers`,
  e.g. with a tradition filter or polish toggled) appends a new answer; old
  ones are never mutated. The plain-text render is derived from the stored
  body on demand and is deterministic.
- **FR-12 Bookmarks.** Create/list/delete bookmarks on answers, optionally
  scoped to one tradition's perspective, with a free-text note. Deleting a
  bookmark never touches the answer.
- **FR-13 Browse & corpus stats.** List traditions (with summaries and key
  concepts) and topics (optionally filtered to one tradition); topic detail
  shows description, question forms, covered traditions with stances, and
  the reading list; passage and source detail views; `corpus stats` prints
  counts, the coverage matrix (topics × traditions), and `corpus_version`.
- **FR-14 API.** FastAPI app per the sketch below; thin — validation,
  service calls, serialization only.
- **FR-15 CLI.** Typer app per the sketch below; same service layer as the
  API; `ask` renders a readable plain-text answer (no color/markup
  dependencies), `--json` emits the raw `AnswerBody`.
- **FR-16 Determinism & hermeticity.** The engine is pure: no network, no
  filesystem, no clock reads (timestamps are inputs); no randomness exists
  anywhere in the engine, so no seeds are needed; all ties break
  lexicographically. Tests and evals run exclusively with offline adapters
  (`LexicalRouter`, `NullPolisher` — plus the eval-only `FaultyPolisher`)
  and the in-memory store. Identical inputs ⇒ byte-identical routing,
  answers, and renders.

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
5. **No full-text semantic search over passages.** Routing targets topics
   only; passage discovery happens through positions and browse. The
   embedding router is a live-only extra, never required and never evaluated.
6. **Fixed topic taxonomy (24 at launch).** Modern-technology ethics (AI,
   gene editing, data privacy) are deferred: the classical primary texts do
   not address them directly, and steelmanning tradition positions there
   requires secondary-source scaffolding beyond this pass. Unrouted
   questions are refused, not stretched (FR-4).
7. **One steelmanned mainline position per (topic, tradition)**, with an
   `intra_tradition_note` for major splits (e.g. act vs rule utilitarianism,
   Theravada vs Mahayana emphases). Denominational trees are out.
8. **English public-domain translations only for quoted text.** No original
   languages; no quoting of in-copyright translations (they appear only as
   further reading or `reference_only` citations). Archaic prose (KJV,
   Legge) is an accepted tradeoff — locators let the user find any modern
   edition.
9. **Not advice.** No pastoral, therapeutic, legal, or medical guidance; the
   safeguards of FR-10 are the implemented form of this posture.
10. **No corpus-editing UI.** The corpus is edited in git and checked by
    `ethos corpus validate` plus the eval gates.
11. **No multi-user, auth, or sync.** One local profile, one SQLite file.
12. **Live adapters are optional extras** (LLM polish, embedding router):
    env-gated, excluded from tests and evals, no heavy deps in the core
    install.

## Architecture

```
projects/ethos/
  src/ethos/
    models.py            # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/
      normalize.py       # FR-2: NFKC, casefold, tokenize, stopwords, Porter stemmer
      router.py          # FR-3/4: topic docs, BM25 + phrase bonus, confidence/abstention
      retrieve.py        # FR-5: position/passage/reading retrieval, canonical ordering
      compose.py         # FR-6/7: AnswerBody assembly, markers, agreement map, text render
      verify.py          # FR-8: citation/quote/safeguard verification over the envelope
      service.py         # ask/re-compose orchestration (pure; store + adapters injected)
    adapters/
      router.py          # TopicRouter Protocol
      router_lexical.py  #   offline default: LexicalRouter (wraps engine.router)
      router_embedding.py#   live: EmbeddingRouter (optional extra, env-gated)
      polisher.py        # ProsePolisher Protocol
      polisher_null.py   #   offline default: NullPolisher (identity)
      polisher_llm.py    #   live: LLMPolisher (optional extra, env-gated)
    store/
      repository.py      # Repository Protocol
      sqlite_repo.py     # SQLite backend (stdlib sqlite3), default ~/.ethos/ethos.db
      memory_repo.py     # in-memory backend for tests/evals
    api/                 # FastAPI app (FR-14)
    cli/                 # Typer app (FR-15)
  data/
    corpus/              # committed corpus: traditions.json, topics.json, sources.json,
                         #   passages/<tradition-or-shared>.json, positions/<tradition>.json
    router.json          # k1, b, w_phrase, τ, δ
    safeguards.json      # crisis/not-medical/not-legal block texts
    stopwords.txt        # committed stopword list
  evals/                 # fixtures/, faulty_polisher.py, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, tests/evals) | Live (env-gated, optional extra) |
|---|---|---|
| `TopicRouter.route(question: str, index: TopicIndex) -> RoutingResult(ranked: list[TopicScore], confidence, abstained, ambiguous)` | `LexicalRouter` — FR-3/4 BM25 + lexicon; deterministic | `EmbeddingRouter` — embeds question + topic docs via a configured embedding API, cosine ranking, same τ/δ logic on normalized scores; `ETHOS_EMBEDDINGS_API_KEY` |
| `ProsePolisher.polish(envelope: str) -> str` | `NullPolisher` — identity; also eval-only `FaultyPolisher` (evals/) that applies scripted mutations for M4 | `LLMPolisher` — rewrites mutable prose via the configured LLM (`ETHOS_LLM_API_KEY`, `ETHOS_LLM_MODEL`, `ETHOS_LLM_BASE_URL`; default provider Anthropic); output always passes through FR-8 verification |

The corpus and the store are not adapters: the corpus is committed data
(loaded read-only at init), and persistence follows the workspace Repository
pattern.

### API sketch (FastAPI)

```
GET  /health
GET  /corpus/stats                    # counts, coverage matrix, corpus_version
GET  /traditions                      GET /traditions/{id}
GET  /topics?tradition=               GET /topics/{id}
GET  /topics/{id}/reading             # aggregated, de-duplicated further reading
GET  /positions/{id}                  GET /passages/{id}
GET  /sources                         GET /sources/{id}
POST /questions                       # {text, asked_at, traditions?, topic_id?, polish?}
                                      #   → question + answer, or refusal payload (FR-4)
GET  /questions?limit=&offset=        GET /questions/{id}
GET  /answers/{id}                    # immutable AnswerBody snapshot
GET  /answers/{id}/text               # deterministic plain-text render
POST /questions/{id}/answers          # re-compose with new options → new answer
POST /bookmarks                       GET /bookmarks         DELETE /bookmarks/{id}
```

### CLI sketch (Typer)

```
ethos init                                        # create DB, load + validate corpus
ethos ask "TEXT" [--tradition ID]... [--topic ID] [--polish] [--json]
ethos topics [--tradition ID]                     ethos topic <ID>
ethos traditions                                  ethos tradition <ID>
ethos reading <TOPIC_ID> [--tradition ID]
ethos history [--limit N]                         ethos show <QUESTION_ID> [--tradition ID]
ethos bookmark add <ANSWER_ID> [--tradition ID] [--note TEXT]
ethos bookmark list                               ethos bookmark rm <ID>
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
   courage_and_fear, judging_others`. The count is the scope valve (D17).
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
   labeled as paraphrase (FR-7). Honesty about translation provenance is
   part of citation integrity.
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
   reasoning that supports it.
9. **Lexical routing as the offline default.** BM25 (Robertson et al.,
   Okapi at TREC-3, 1994) over curated, hand-expanded topic documents plus
   Porter stemming (1980) is deterministic, inspectable, dependency-free,
   and hermetic — the workspace requirements. The craft moves into the
   lexicons: keywords must cover scenario vocabulary ("compliment",
   "feelings", "cover for"), not just topic names; the oblique fixture tier
   exists to force exactly that (EVALS). Embedding-based routing is a live
   adapter because it would improve recall on unseen phrasings but cannot
   run hermetically.
10. **Abstention is a feature with a budget.** FR-4 implements Chow's (1970)
    reject option: below τ, refuse; the M2 gates price the tradeoff (≥ 0.90
    correct refusals at ≤ 0.02 false refusals). Guessing wrong on a moral
    question costs trust in a way a refusal does not; and the `--topic`
    bypass plus browse keep refusals cheap for the user.
11. **Anti-proof-texting rules.** Quoting scripture out of context is a
    named, recognized failure mode in biblical studies (proof-texting).
    Structural mitigations: quotes capped at 90 words; every passage carries
    a curated `context_note`; positions may (and where relevant, should)
    cite `complicating` passages that cut against their own summary — e.g.
    a Christianity-on-honesty position quoting Matthew 5:37 *and* noting the
    midwives of Exodus 1; the role vocabulary makes tension visible instead
    of hiding it.
12. **Sensitive-topic safeguards are implemented behavior** (workspace
    quality bar): the crisis-resources block (988 Suicide & Crisis Lifeline;
    Befrienders Worldwide; findahelpline.com) is data, rendered first,
    non-disableable, and verified as an immutable region (FR-8/FR-10);
    euthanasia adds not-medical-advice; law-touching topics add
    not-legal-advice. Tests assert presence and non-suppressibility.
13. **Answers are immutable snapshots.** Corpus edits must not rewrite what
    the user was shown (US-7), so answers embed the full rendered content
    plus `corpus_version` (SHA-256 of canonical corpus JSON) and
    `composer_version` (bumped on template changes, with golden fixtures
    regenerated in the same commit).
14. **Corpus format: JSON, one positions/passages file per tradition.**
    Diff-friendly, stdlib-parseable, and shardable for curation. Files are
    the source of truth; `ethos init` loads them into read-only SQLite
    tables for uniform querying (same pattern as ChessMentor's
    `levels.json`).
15. **English-only UI and corpus.** Locators are edition-independent
    (chapter:verse, Bekker, Academy pagination, folio), so any modern
    translation can be consulted alongside; original-language texts are out
    of scope.
16. **No randomness anywhere.** Routing, composition, and rendering are
    deterministic functions; ties break lexicographically; no seed plumbing
    is needed (unlike sibling projects). Timestamps are caller inputs
    (edges may call `datetime.now`, the engine may not).
17. **Line budget & scope valves.** Estimate: engine ~1,200 lines (router
    ~300, compose ~350, verify ~250, normalize ~180, retrieve/service
    ~120), adapters ~250, store ~450, API ~350, CLI ~350, models ~300 —
    ≈ 3,000 lines, inside the 2–4k mandate. The corpus (~170+ positions,
    ~220 passages, ~40 sources, ~200 reading pointers) is data, not code,
    and is the dominant authoring effort — planned as such. Valves, in
    order: topic count 24 → 18 (floors and fixtures shrink with it, same
    commit); drop the ambiguity flag (keep abstention); drop bookmarks.
18. **Polish is provider-agnostic and structurally distrusted.** The live
    polisher gets an envelope with immutable regions and is *assumed
    adversarial*: FR-8 re-verifies everything after every polish, and M4's
    `FaultyPolisher` (14 scripted mutation classes) gates detection at
    recall 1.0 / FPR 0. Fallback is silent-safe (deterministic render +
    `polish_fell_back` flag), so a flaky LLM can never degrade integrity,
    only prose.
19. **Assumption: single user, no auth**; SQLite at `~/.ethos/ethos.db`
    (path configurable via `ETHOS_DB_PATH`); in-memory store for tests.
20. **Assumption: corpus curation is trusted input.** Evals verify structure,
    floors, licensing, and integrity mechanically; they cannot verify
    theological fidelity offline. That risk is carried by the curation
    protocol (D6), the `transcription_checked` flag (quotes checked against
    the named edition at authoring time), and the complicating-passage
    convention — stated honestly in EVALS.md ("what the evals cannot see").
