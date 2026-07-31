# Ethos — Data Model

Two storage classes, per workspace conventions:

- **Committed corpus** (read-only at runtime, versioned in git under
  `projects/ethos/data/`): the knowledge base — traditions, topics, sources,
  passages, positions — plus router configuration, safeguard texts, and the
  stopword list. Loaded and validated into SQLite at `ethos init`
  (`ethos corpus validate` re-checks without loading); the files remain the
  source of truth. `corpus_version` = SHA-256 over the canonical JSON
  (sorted keys, `\n` line endings, UTF-8) of every file under `data/corpus/`,
  concatenated in sorted path order.
- **SQLite** (user state, default `~/.ethos/ethos.db`, path via
  `ETHOS_DB_PATH`; in-memory backend for tests): questions, answers,
  bookmarks, corpus metadata. Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/ethos/models.py`; the store maps them to
the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers (the
engine never reads the clock). There is no randomness in the engine, so no
seed fields exist anywhere.

> Example-record caveat: quotation texts shown below are illustrative of the
> named editions; during curation every quote is transcribed verbatim from
> the cited public-domain edition and `transcription_checked` is set only
> after that check (SCOPE D20).

## Enumerations

| Enum | Values |
|---|---|
| `Family` | `abrahamic`, `dharmic`, `east_asian`, `greco_roman`, `modern_western` |
| `Stance` | `obligatory`, `encouraged`, `permitted`, `context_dependent`, `discouraged`, `forbidden`, `contested`, `reframed` |
| `PassageRole` | `core`, `supporting`, `complicating` |
| `License` | `public_domain`, `cc0`, `cc_by`, `reference_only` |
| `LocatorScheme` | `chapter_verse`, `book_section`, `part_question_article`, `bekker`, `stephanus`, `academy_ed`, `sutta_ref`, `folio`, `hadith_ref`, `section` |
| `ReadingKind` | `sep`, `iep`, `book`, `article`, `primary_translation` |
| `QuestionOutcome` | `answered`, `refused_out_of_scope` |
| `SafeguardKind` | `crisis_resources`, `not_medical_advice`, `not_legal_advice` |

`LocatorScheme` regexes (validated by FR-1; anchored, illustrative not
exhaustive):

| Scheme | Example locator | Regex |
|---|---|---|
| `chapter_verse` | `Matthew 5:37`, `Qur'an 17:23–24`, `Gita 2.47` | `^[1-3]?\s?[A-Za-z''\- ]+ \d+[:.]\d+([–-]\d+)?$` |
| `book_section` | `Analects XIII.18`, `Meditations IX.1` | `^[A-Za-z ]+ [IVXLC]+\.\d+([–-]\d+)?$` |
| `part_question_article` | `ST II-II, Q.110, art.3` | `^ST [I]+(-[I]+)?, Q\.\d+, art\.\d+$` |
| `bekker` | `NE II.6, 1106b36` | `^[A-Za-z ]+ [IVX]+\.\d+, \d{3,4}[ab]\d{1,2}$` |
| `stephanus` | `Crito 51b` | `^[A-Za-z ]+ \d{1,3}[a-e]$` |
| `academy_ed` | `Groundwork 4:421` | `^[A-Za-z ]+ \d:\d{3}([–-]\d{3})?$` |
| `sutta_ref` | `MN 58`, `AN 3.65`, `DN 31`, `Dhp 129–130` | `^(DN\|MN\|SN\|AN\|Dhp\|Snp) \d+(\.\d+)?([–-]\d+)?$` |
| `folio` | `Shabbat 31a`, `Ketubot 16b–17a` | `^[A-Za-z ]+ \d+[ab]([–-]\d+[ab])?$` |
| `hadith_ref` | `Sahih Muslim 2605` | `^[A-Za-z\- ]+ \d+[a-z]?$` |
| `section` | `Enchiridion 1`, `Utilitarianism ch.2` | `^[A-Za-z ]+ (ch\.)?\d+$` |

## Committed corpus entities

### Tradition — `data/corpus/traditions.json` → table `tradition`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `confucianism`; the 10 ids are fixed (SCOPE D2) |
| `name` | str | display name |
| `family` | Family | |
| `order` | int | unique, 1–10; canonical presentation order (invariant) |
| `era` | str | e.g. `"c. 551–479 BCE onward"` |
| `summary` | str | 2–3 neutral sentences (AAR religious-literacy register) |
| `key_concepts` | list[{`term`, `gloss`}] | 3–6 entries, e.g. `ren` — "humaneness, the cardinal Confucian virtue" |

Invariants: exactly 10 rows; `order` values are 1..10 with no gaps.

### Topic — `data/corpus/topics.json` → table `topic`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `honesty_and_deception`; ≥ 24 rows (floor) |
| `title` | str | e.g. `"Honesty and deception"` |
| `description` | str | 2–3 sentences; part of the router topic document (FR-3) |
| `question_forms` | list[str] | 3–6 canonical phrasings, part of the topic document |
| `keywords` | list[{`term`, `weight`}] | lexicon; `term` may be multi-word (phrase bonus); `weight` int 1–3; must include scenario vocabulary, not just synonyms (SCOPE D9) |
| `related_topics` | list[str] | topic ids; must resolve; not self |
| `sensitive` | bool | |
| `safeguard_ids` | list[str] | required non-empty iff `sensitive`; must resolve |

Invariants: ≥ 24 topics; every topic has ≥ 6 positions (coverage floor);
no keyword term consists only of stopwords; `question_forms` non-empty.

### Safeguard — `data/safeguards.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `crisis_resources` |
| `kind` | SafeguardKind | |
| `text` | str | rendered verbatim; treated as an immutable region by FR-8 |

Invariant: `crisis_resources` exists and names 988 Suicide & Crisis Lifeline
(US), Befrienders Worldwide (befrienders.org), and findahelpline.com.

### Source — `data/corpus/sources.json` → table `source`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `analects-legge` |
| `title` | str | e.g. `The Analects of Confucius` |
| `author` | str \| null | null for anonymous/composite works (Qur'an, Dhammapada) |
| `composed_era` | str | e.g. `"5th–3rd c. BCE"` |
| `translator` | str \| null | required non-null when `license != reference_only` |
| `translation_year` | int \| null | same requirement |
| `edition_note` | str | e.g. `"Chinese Classics vol. I, 2nd ed."` |
| `license` | License | `reference_only` sources can never be quoted (invariant) |
| `locator_scheme` | LocatorScheme | validates all child passages' locators |
| `url` | str \| null | stable public copy (Gutenberg, sacred-texts.com, ctext.org); required when `license != reference_only` |

### Passage — `data/corpus/passages/<file>.json` → table `passage`

One file per tradition (`passages/confucianism.json`, …) plus
`passages/shared.json` for texts cited by multiple traditions (e.g. Hebrew
Bible passages used by both Judaism and Christianity positions).

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `analects-13-18` |
| `source_id` | str FK → source | |
| `locator` | str | must match the source's locator-scheme regex (invariant) |
| `text` | str \| null | verbatim quote, ≤ 90 words; **null iff** the source has `license = reference_only` (invariant) |
| `paraphrase` | str \| null | curated paraphrase; **required non-null iff** `text` is null; rendered with the explicit `paraphrase` label (FR-7) |
| `context_note` | str | 1–2 sentences situating the passage (anti-proof-texting, SCOPE D11); required |
| `transcription_checked` | bool | must be `true` for every passage with `text` (invariant; set only after checking against the named edition) |

Invariants: `id` unique across all passage files; exactly one of
`text`/`paraphrase` is non-null; word count of `text` ≤ 90.

### Position — `data/corpus/positions/<tradition>.json` → table `position`

One file per tradition. Embedded sub-objects: `ReasoningPoint`,
`PassageRef`, `FurtherReading`.

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | convention `<topic>--<tradition>`, e.g. `honesty_and_deception--confucianism` |
| `topic_id` | str FK → topic | |
| `tradition_id` | str FK → tradition | unique `(topic_id, tradition_id)` (invariant) |
| `stance` | Stance | curation judgment (SCOPE D8) |
| `summary` | str | 50–120 words, steelmanned, present tense, no evaluation of other traditions (invariant: no other tradition's name appears) |
| `reasoning` | list[ReasoningPoint] | 1–5 points; `{text: str, passage_id: str \| null}` — when non-null the composer emits a citation marker at that point |
| `passages` | list[PassageRef] | `{passage_id, role: PassageRole, note: str \| null}`; ≥ 1 with `role = core` (invariant); order within the list is the committed render order within each role |
| `intra_tradition_note` | str \| null | major internal splits (e.g. act vs rule utilitarianism) |
| `further_reading` | list[FurtherReading] | ≥ 1 (invariant); `{title, author, year: int \| null, kind: ReadingKind, url: str \| null, note: str \| null}` |
| `curator_note` | str | what was consulted; not rendered to the user |

Invariants: every `passage_id` resolves; a position's passages all belong to
sources whose license permits their use (quote vs reference-only handled at
the passage level); each tradition has ≥ 12 positions (floor); each topic has
≥ 6 (floor).

### RouterConfig — `data/router.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `k1`, `b` | float | Okapi BM25 parameters; defaults 1.5, 0.75 |
| `w_phrase` | float | phrase-bonus weight; default 2.0 |
| `tau` | float | abstention floor τ (FR-4); tuned on direct/colloquial fixture tiers only |
| `delta` | float | ambiguity margin δ; default 0.15 |

### Stopwords — `data/stopwords.txt`

One word per line, lowercase, ~250 entries (van Rijsbergen 1979 lineage).
Invariant: no topic keyword term is composed entirely of stopwords.

## SQLite user-state entities

### CorpusMeta — table `corpus_meta` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1` |
| `corpus_version` | str | SHA-256 hex of the loaded corpus |
| `loaded_at` | str | ISO ts (input) |

Invariant: the repository refuses reads/writes of corpus tables when the
files' recomputed hash differs from `corpus_version` until `ethos init`
reloads (stale-corpus guard).

### Question — table `question` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `text` | str | as typed |
| `asked_at` | str | ISO ts (input) |
| `outcome` | QuestionOutcome | |
| `forced_topic_id` | str \| null | non-null when `--topic` bypassed routing |
| `routing` | JSON | `{ranked: [{topic_id, score}] (top 5), confidence, abstained, ambiguous}` — stored even for refusals; `null` iff `forced_topic_id` set |

Invariants: append-only; `outcome = refused_out_of_scope` iff `abstained`
and no forced topic; a refused question has no answers.

### Answer — table `answer` (append-only, immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `question_id` | int FK → question | |
| `created_at` | str | ISO ts (input) |
| `topic_id` | str | routed or forced topic |
| `options` | JSON | `{traditions: [id] \| null, polish: bool}` |
| `corpus_version` | str | snapshot stamp (must equal `corpus_meta.corpus_version` at creation — invariant) |
| `composer_version` | str | e.g. `"1"`; bumped on template changes |
| `polish_used` | bool | live polisher actually ran and passed verification |
| `polish_fell_back` | bool | polisher ran, failed FR-8, deterministic render served |
| `verified` | bool | **must be `true`**; the repository rejects inserts with `false` (invariant, FR-8) |
| `body` | JSON | full `AnswerBody` snapshot (below); the plain-text render is derived from it on demand, never stored |

`AnswerBody` JSON structure (schema enforced by Pydantic):

```
{
  "safeguards":      [{id, kind, text}],                  # possibly empty; first in render order
  "routing":         {topic_id, confidence, alternates: [{topic_id, score}], ambiguous},
  "perspectives": [                                        # canonical Tradition.order
    { "tradition_id": ..., "position_id": ..., "stance": ...,
      "summary": ...,                                      # mutable prose (polishable)
      "reasoning": [{text, marker: "C3" | null}],          # text mutable; markers immutable
      "quotes": [                                          # immutable regions
        {marker, passage_id, text | paraphrase, is_paraphrase, locator, source_line, context_note}
      ],
      "intra_tradition_note": ... | null,                  # mutable prose
      "further_reading": [{title, author, year, kind, url, note}]   # immutable
    }
  ],
  "not_covered":     [tradition_id],
  "agreement_map":   {stance: [tradition_id]},             # partitions perspectives exactly
  "citations":       {marker: {passage_id, locator, source_id}},
  "corpus_version":  ..., "composer_version": ...
}
```

Invariants: rows immutable once inserted; `citations` keys equal the multiset
of markers appearing in `reasoning` + `quotes` (verified, FR-8);
`agreement_map` values partition the `perspectives` tradition ids exactly;
`quotes[].text` byte-equals (NFC) the committed passage text;
`safeguards` non-empty whenever the topic is sensitive, and their `text`
byte-equals `data/safeguards.json`.

### Bookmark — table `bookmark`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `answer_id` | int FK → answer | |
| `tradition_id` | str \| null | scope to one perspective; must be present in that answer's body (invariant) |
| `note` | str \| null | |
| `created_at` | str | ISO ts (input) |

Invariant: deleting a bookmark never cascades to the answer.

## Relationships (summary)

```
Tradition (10) ──< Position >── Topic (≥24)      unique (topic, tradition)
Position ──< PassageRef >── Passage ──▶ Source   (≥1 core per position)
Position ──< FurtherReading (embedded, ≥1)
Topic ──< safeguard_ids ──▶ Safeguard
Question (append-only) ──< Answer (append-only, immutable, verified=true)
Answer.body.citations ──▶ Passage                (verified at compose time)
Bookmark ──▶ Answer (0..1 tradition scope)
Corpus files: source of truth; SQLite corpus tables: read-only load
Plain-text render, reading lists, coverage matrix: derived, never stored
```

## Example records

`tradition` (from `traditions.json`):

```json
{
  "id": "confucianism", "name": "Confucianism", "family": "east_asian",
  "order": 6, "era": "c. 551–479 BCE (Confucius) onward",
  "summary": "A Chinese ethical tradition centered on the cultivation of character within relationships — family, community, state. Moral life is learned through ritual propriety and the practice of humaneness rather than derived from abstract rules.",
  "key_concepts": [
    {"term": "ren", "gloss": "humaneness; the cardinal virtue of benevolent regard for others"},
    {"term": "li", "gloss": "ritual propriety; the forms through which respect is enacted"},
    {"term": "xin", "gloss": "trustworthiness in word and deed"},
    {"term": "shu", "gloss": "reciprocity: do not impose on others what you would not wish for yourself"}
  ]
}
```

`topic` (from `topics.json`, keywords abridged):

```json
{
  "id": "honesty_and_deception", "title": "Honesty and deception",
  "description": "Whether and when it is wrong to lie, deceive, or shade the truth — including white lies told to spare feelings, deception to protect someone from harm, and honesty that wounds.",
  "question_forms": [
    "Is it wrong to lie?",
    "Is a white lie acceptable?",
    "Should I tell the truth even if it hurts someone?",
    "Is it ok to deceive someone to protect them?"
  ],
  "keywords": [
    {"term": "lie", "weight": 3}, {"term": "lying", "weight": 3},
    {"term": "white lie", "weight": 3}, {"term": "honesty", "weight": 3},
    {"term": "truth", "weight": 2}, {"term": "deceive", "weight": 3},
    {"term": "spare feelings", "weight": 2}, {"term": "compliment", "weight": 1},
    {"term": "cover for", "weight": 2}, {"term": "little fib", "weight": 2},
    {"term": "brutal honesty", "weight": 2}, {"term": "pretend", "weight": 1}
  ],
  "related_topics": ["promise_keeping_and_oaths", "speech_and_gossip"],
  "sensitive": false, "safeguard_ids": []
}
```

`source` (from `sources.json`):

```json
{
  "id": "analects-legge", "title": "The Analects of Confucius",
  "author": "Confucius (compiled by disciples)",
  "composed_era": "5th–3rd c. BCE",
  "translator": "James Legge", "translation_year": 1861,
  "edition_note": "The Chinese Classics, vol. I",
  "license": "public_domain", "locator_scheme": "book_section",
  "url": "https://ctext.org/analects"
}
```

`passage` (from `passages/confucianism.json`):

```json
{
  "id": "analects-13-18", "source_id": "analects-legge",
  "locator": "Analects XIII.18",
  "text": "The Duke of She informed Confucius, saying, 'Among us here there are those who may be styled upright in their conduct. If their father have stolen a sheep, they will bear witness to the fact.' Confucius said, 'Among us, in our part of the country, those who are upright are different from this. The father conceals the misconduct of the son, and the son conceals the misconduct of the father. Uprightness is to be found in this.'",
  "paraphrase": null,
  "context_note": "Confucius is contrasting mechanical truth-telling with the deeper integrity of sustaining family bonds; the passage is a locus classicus for the tension between honesty and relational duty, not a license for deceit.",
  "transcription_checked": true
}
```

`passage` (reference-only, from `passages/judaism.json`):

```json
{
  "id": "ketubot-16b-bride", "source_id": "talmud-bavli-ref",
  "locator": "Ketubot 16b–17a",
  "text": null,
  "paraphrase": "The schools of Hillel and Shammai debate how to praise a bride: Shammai holds one describes her as she is; Hillel holds one praises every bride as beautiful and graceful. The law follows Hillel — kind speech may bend literal accuracy for the sake of another's joy.",
  "context_note": "A foundational rabbinic case for permitted benevolent untruth; later halakhic discussion (e.g. Bava Metzia 23b–24a) enumerates narrow categories where deviation from truth is allowed, especially for peace.",
  "transcription_checked": false
}
```

`position` (from `positions/confucianism.json`, abridged):

```json
{
  "id": "honesty_and_deception--confucianism",
  "topic_id": "honesty_and_deception", "tradition_id": "confucianism",
  "stance": "context_dependent",
  "summary": "Trustworthiness (xin) is a cardinal virtue: a person whose word cannot be relied on has no standing, and sincerity is the ground of all relationships. Yet truth-telling is not an isolated rule; it serves the web of relational duties. Where blunt disclosure would betray a deeper obligation — as between father and son — uprightness may require discretion rather than testimony. The cultivated person weighs speech by what sustains humaneness and trust, not by literal completeness alone.",
  "reasoning": [
    {"text": "Trustworthiness is repeatedly named among the essentials of character and government; without it a person 'cannot get on'.", "passage_id": "analects-2-22"},
    {"text": "Relational duties can override third-party claims to disclosure, as in the case of the sheep-stealing father.", "passage_id": "analects-13-18"},
    {"text": "Speech should be measured and slow to outrun action — glibness is a vice even when accurate.", "passage_id": "analects-4-24"}
  ],
  "passages": [
    {"passage_id": "analects-2-22", "role": "core", "note": null},
    {"passage_id": "analects-13-18", "role": "core", "note": "the classic hard case"},
    {"passage_id": "analects-4-24", "role": "supporting", "note": null}
  ],
  "intra_tradition_note": "Later Confucians (notably Zhu Xi's school) read XIII.18 narrowly, as protecting the parent-child bond, not licensing deception generally; Mencius extends the priority of familial devotion.",
  "further_reading": [
    {"title": "Confucius", "author": "Mark Csikszentmihalyi", "year": 2020, "kind": "sep", "url": "https://plato.stanford.edu/entries/confucius/", "note": "survey with a section on the upright-father case"},
    {"title": "Confucianism: A Very Short Introduction", "author": "Daniel K. Gardner", "year": 2014, "kind": "book", "url": null, "note": null}
  ],
  "curator_note": "Drafted from Legge's Analects (II.22, IV.24, XIII.18) with Csikszentmihalyi (SEP) for the reception history."
}
```

`question` + `answer` (abridged body):

```json
{
  "id": 41, "text": "was it wrong to tell my friend I loved her novel? I didn't.",
  "asked_at": "2026-07-31T21:04:12Z", "outcome": "answered",
  "forced_topic_id": null,
  "routing": {
    "ranked": [
      {"topic_id": "honesty_and_deception", "score": 11.2},
      {"topic_id": "speech_and_gossip", "score": 4.1},
      {"topic_id": "judging_others", "score": 2.0}
    ],
    "confidence": 0.65, "abstained": false, "ambiguous": false
  }
}
```

```json
{
  "id": 57, "question_id": 41, "created_at": "2026-07-31T21:04:12Z",
  "topic_id": "honesty_and_deception",
  "options": {"traditions": null, "polish": false},
  "corpus_version": "9f2c…e1", "composer_version": "1",
  "polish_used": false, "polish_fell_back": false, "verified": true,
  "body": {
    "safeguards": [],
    "routing": {"topic_id": "honesty_and_deception", "confidence": 0.65,
                "alternates": [{"topic_id": "speech_and_gossip", "score": 4.1}],
                "ambiguous": false},
    "perspectives": [
      {"tradition_id": "kantian_deontology",
       "position_id": "honesty_and_deception--kantian_deontology",
       "stance": "forbidden",
       "summary": "…",
       "reasoning": [{"text": "A maxim of lying cannot be universalized without destroying the practice of assertion it depends on.", "marker": "C1"}],
       "quotes": [{"marker": "C1", "passage_id": "kant-groundwork-4-421-false-promise",
                   "text": "…", "is_paraphrase": false,
                   "locator": "Groundwork 4:421",
                   "source_line": "Fundamental Principles of the Metaphysic of Morals, trans. T. K. Abbott (1873)",
                   "context_note": "…"}],
       "intra_tradition_note": null,
       "further_reading": [{"title": "Kant's Moral Philosophy", "author": "Robert Johnson & Adam Cureton", "year": 2022, "kind": "sep", "url": "https://plato.stanford.edu/entries/kant-moral/", "note": null}]}
    ],
    "not_covered": [],
    "agreement_map": {
      "forbidden": ["christianity", "buddhism", "stoicism", "kantian_deontology", "islam"],
      "context_dependent": ["judaism", "hinduism", "confucianism", "utilitarianism"],
      "reframed": ["virtue_ethics"]
    },
    "citations": {"C1": {"passage_id": "kant-groundwork-4-421-false-promise",
                          "locator": "Groundwork 4:421", "source_id": "kant-groundwork-abbott"}},
    "corpus_version": "9f2c…e1", "composer_version": "1"
  }
}
```

`bookmark`:

```json
{
  "id": 5, "answer_id": 57, "tradition_id": "confucianism",
  "note": "the sheep-stealing father — reread Gardner ch. 4",
  "created_at": "2026-07-31T21:20:00Z"
}
```

`router.json` and `safeguards.json`:

```json
{ "k1": 1.5, "b": 0.75, "w_phrase": 2.0, "tau": 6.0, "delta": 0.15 }
```

```json
{
  "id": "crisis_resources", "kind": "crisis_resources",
  "text": "If you are thinking about suicide or self-harm, please reach out now: call or text 988 (Suicide & Crisis Lifeline, US), or find local helplines at befrienders.org or findahelpline.com. What follows are scholarly summaries of how traditions have discussed this topic — it is not counseling."
}
```
