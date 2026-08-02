# Ethos — Data Model

Two storage classes, per workspace conventions:

- **Committed data** (read-only at runtime, versioned in git under
  `projects/ethos/data/`): the knowledge corpus — traditions, topics, sources,
  passages, positions — plus router configuration, safeguard texts, and the
  stopword list. Loaded and validated into SQLite at `ethos init`
  (`ethos corpus validate` re-checks without loading); the files remain the
  source of truth.
- **SQLite** (user state, default `~/.ethos/ethos.db`, path via
  `ETHOS_DB_PATH`; in-memory backend for evals; both backends covered by the
  store-contract tests): questions, answers, corpus metadata. Repository
  pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/ethos/models.py`; the store maps them to
the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers (the
engine never reads the clock). There is no randomness in the engine, so no
seed fields exist anywhere.

## `corpus_version` — what it covers and how it is computed

`corpus_version` covers **every committed file that can change a rendered
answer**, not just the corpus proper. An earlier draft hashed only
`data/corpus/`, which left `safeguards.json` (rendered verbatim into answers),
`router.json` (τ, κ, BM25 parameters — they determine which topic you get) and
`stopwords.txt` (determines every routing score) unversioned and outside the
stale-corpus guard.

```
files  = sorted(relative paths of every file under data/, POSIX order)
digest = SHA-256 over the concatenation, for each path p in files, of:
             p.encode("utf-8") + b"\n"
           + canonical_bytes(p)  + b"\n"
canonical_bytes(p) = json.dumps(json.load(p), sort_keys=True, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8")   # *.json
                   = raw file bytes                                       # everything else
corpus_version = digest.hexdigest()
```

Recomputing the digest is the stale-corpus guard (CorpusMeta below) and is
also recorded in `evals/baselines.json`, so any edit to any of these files
forces a deliberate baseline re-derivation (EVALS C19).

## Loader contract (FR-1 layer 2)

The loader is **byte-preserving**. For every string field of every corpus
record, `loaded_value.encode("utf-8") == raw_json_string_value.encode("utf-8")`.
No NFC/NFKC, no dash or quote folding, no whitespace collapsing, no `.strip()`,
at load time or anywhere between the file and the rendered citation.
Normalization (FR-2) exists solely for router documents and queries.

This is a gate (C1), not a note, because it is the one failure that both the
engine and a naively-written metric would agree about: if the loader folded
`Ketubot 16b–17a` to `16b-17a`, composer and verifier would still match, every
internal check would pass, and only the printed citation would be wrong. The
M3 checker therefore reads the raw files independently (EVALS M3).

Two further character constraints, enforced by C1, keep the two parseable
serializations unambiguous:

- no corpus string field may contain `[[` or `]]` (envelope sentinels);
- `Passage.text` and `Passage.paraphrase` may not contain U+201C/U+201D
  (`“`/`”`, the plain-text render's quote delimiters). Curated quotations use
  `‘ ’` for inner quotation, as the Analects example below does.

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

### Locator regexes — two distinct pattern sets

The two sets exist for different jobs and must not be shared; conflating them
was a real defect in the first draft (an unanchored `section` pattern matches
ordinary prose like "ch. 2").

**(1) Validation patterns — anchored, permissive, used by C5** to check that a
passage's locator is well formed for its source's scheme:

| Scheme | Example locator | Validation regex (anchored) |
|---|---|---|
| `chapter_verse` | `Matthew 5:37`, `Qur'an 17:23–24`, `Gita 2.47` | `^[1-3]?\s?[A-Za-z'’\- ]+ \d+[:.]\d+([–-]\d+)?$` |
| `book_section` | `Analects XIII.18`, `Meditations IX.1` | `^[A-Za-z ]+ [IVXLC]+\.\d+([–-]\d+)?$` |
| `part_question_article` | `ST II-II, Q.110, art.3` | `^ST [I]+(-[I]+)?, Q\.\d+, art\.\d+$` |
| `bekker` | `NE II.6, 1106b36` | `^[A-Za-z ]+ [IVX]+\.\d+, \d{3,4}[ab]\d{1,2}$` |
| `stephanus` | `Crito 51b` | `^[A-Za-z ]+ \d{1,3}[a-e]$` |
| `academy_ed` | `Groundwork 4:421` | `^[A-Za-z ]+ \d:\d{3}([–-]\d{3})?$` |
| `sutta_ref` | `MN 58`, `AN 3.65`, `Dhp 129–130` | `^(DN\|MN\|SN\|AN\|Dhp\|Snp) \d+(\.\d+)?([–-]\d+)?$` |
| `folio` | `Shabbat 31a`, `Ketubot 16b–17a` | `^[A-Za-z ]+ \d+[ab]([–-]\d+[ab])?$` |
| `hadith_ref` | `Sahih Muslim 2605` | `^[A-Za-z\- ]+ \d+[a-z]?$` |
| `section` | `Enchiridion 1`, `Utilitarianism ch.2` | `^[A-Za-z ]+ (ch\.)?\d+$` |

**(2) Prose-locator scan patterns — unanchored, deliberately narrow, used only
by FR-8(e)** on *changed spans of mutable prose*. They must fire on a
polisher inventing a citation and stay quiet on ordinary polished English:

```
PROSE_LOCATOR_SCAN = [
  r"\b\d+:\d+(?:[–-]\d+)?\b",                       # 5:37, 17:23–24, 4:421
  r"\b[IVXLC]{1,5}\.\d+(?:[–-]\d+)?\b",             # XIII.18, IX.1
  r"\bQ\.\d+,\s*art\.\d+\b",                        # Q.110, art.3
  r"\b\d{3,4}[ab]\d{1,2}\b",                        # 1106b36
  r"\b(?:DN|MN|SN|AN|Dhp|Snp)\s\d+(?:\.\d+)?\b",    # MN 58
  r"\b\d{1,3}[ab]\b(?=\s|,|\.|$)",                  # 31a, 16b
]
```

Note what is deliberately absent: bare `ch. 2`, bare `[A-Za-z ]+ \d+`, and
bare Roman numerals. Those appear in legitimate prose, and because check (e)
only scans *changed* spans, a curated `intra_tradition_note` that says "later
Confucians read XIII.18 narrowly" survives polish untouched and is never
scanned. FR-8(e) therefore needs no authoring constraint on curated prose.

## Committed corpus entities

### Tradition — `data/corpus/traditions.json` → table `tradition`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `confucianism`; the 10 ids are fixed (SCOPE D2) |
| `name` | str | display name; also the rendered perspective header (FR-8(h)) |
| `family` | Family | |
| `order` | int | unique, 1–10; canonical presentation order (invariant) |
| `era` | str | e.g. `"c. 551–479 BCE onward"` |
| `summary` | str | 2–3 neutral sentences (AAR religious-literacy register) |
| `key_concepts` | list[{`term`, `gloss`}] | 3–6 entries |

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
| `sensitive` | bool | true ⇒ `crisis_resources ∈ safeguard_ids` (invariant) |
| `safeguard_ids` | list[str] | may be non-empty on non-sensitive topics too (e.g. `not_legal_advice`); every id must resolve |

Invariants: ≥ 24 topics; every topic has ≥ 6 positions and ≥ 3 distinct
position stances (≤ 2 topics may be listed in
`evals/fixtures/gate_exceptions.json → near_unanimous_topics`, and those still
need ≥ 2); no keyword term consists only of stopwords; `question_forms`
non-empty.

### Safeguard — `data/safeguards.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `crisis_resources` |
| `kind` | SafeguardKind | |
| `text` | str | rendered verbatim, first; immutable region under FR-8(d) |

Invariant: `crisis_resources` exists and names 988 Suicide & Crisis Lifeline
(US), Befrienders Worldwide (befrienders.org), and findahelpline.com. Covered
by `corpus_version`.

### Source — `data/corpus/sources.json` → table `source`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `analects-legge` |
| `title` | str | e.g. `The Analects of Confucius` |
| `author` | str \| null | null for anonymous/composite works |
| `composed_era` | str | e.g. `"5th–3rd c. BCE"` |
| `translator` | str \| null | required non-null when `license != reference_only` |
| `translation_year` | int \| null | same requirement |
| `edition_note` | str | e.g. `"Chinese Classics vol. I, 2nd ed."` |
| `license` | License | `reference_only` sources can never be quoted (invariant) |
| `locator_scheme` | LocatorScheme | validates all child passages' locators |
| `url` | str \| null | stable public copy; required when `license != reference_only` |

Derived (never stored, computed identically by the composer, the verifier and
the independent M3 checker):

```
source_line = f"{title}, trans. {translator} ({translation_year})"   # quotable
            = f"{title} — {edition_note}"                            # reference_only
```

### Passage — `data/corpus/passages/<file>.json` → table `passage`

One file per tradition (`passages/confucianism.json`, …) plus
`passages/shared.json` for texts cited by multiple traditions.

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | e.g. `analects-13-18` |
| `source_id` | str FK → source | |
| `locator` | str | must match the source's **validation** regex (C5) |
| `text` | str \| null | verbatim quote, ≤ 90 words; **null iff** the source has `license = reference_only` |
| `paraphrase` | str \| null | curated paraphrase; **required non-null iff** `text` is null; always rendered under the explicit label line (FR-7) |
| `context_note` | str | 1–2 sentences situating the passage (anti-proof-texting); required; immutable under FR-8(i) |
| `transcription_checked` | bool | must be `true` for every passage with `text` |

Invariants: `id` unique across all passage files; exactly one of
`text`/`paraphrase` is non-null; word count of `text` ≤ 90; no `[[`/`]]`;
no `“`/`”` in `text`/`paraphrase`.

### Position — `data/corpus/positions/<tradition>.json` → table `position`

One file per tradition. Embedded sub-objects: `ReasoningPoint`, `PassageRef`,
`FurtherReading`.

| Field | Type | Notes |
|---|---|---|
| `id` | str PK (slug) | convention `<topic>--<tradition>` |
| `topic_id` | str FK → topic | |
| `tradition_id` | str FK → tradition | unique `(topic_id, tradition_id)` |
| `stance` | Stance | curation judgment (SCOPE D8); immutable under FR-8(i) |
| `summary` | str | 50–120 words, steelmanned, present tense; no other tradition's name appears; **mutable prose** in the envelope |
| `reasoning` | list[ReasoningPoint] | 1–5 points; `{text, passage_id \| null}` — text mutable, the derived marker immutable |
| `passages` | list[PassageRef] | `{passage_id, role, note \| null}`; ≥ 1 with `role = core`; list order is the committed render order within each role |
| `intra_tradition_note` | str \| null | major internal splits; **mutable prose** |
| `further_reading` | list[FurtherReading] | ≥ 1; `{title, author, year \| null, kind, url \| null, note \| null}`; immutable under FR-8(g) |
| `curator_note` | str | what was consulted; never rendered |

Structural invariants: every `passage_id` resolves; each tradition has ≥ 12
positions; each topic has ≥ 6.

**Substance invariants (FR-1 layer 3)** — these exist because the structural
set above is satisfiable by a degenerate corpus (ten passages reused
everywhere, one stance, templated summaries, one reading link per tradition).
All are mechanical, all are gated (EVALS C11–C16), all are also reported by
`ethos corpus stats`:

| # | Invariant | Gate |
|---|---|---|
| 1 | No passage is the `core` reference of more than 3 positions | C11 |
| 2 | Distinct cited passages ≥ 6 × #topics (≥ 144 at 24 topics) | C11 |
| 3 | Every topic has ≥ 3 distinct stances (≤ 2 allow-listed topics: ≥ 2) | C12 |
| 4 | Within a topic, pairwise Jaccard over stemmed content tokens of position summaries < 0.5 | C13 |
| 5 | Globally, no two position summaries share > 70% of their stemmed content tokens | C13 |
| 6 | ≥ 30% of positions carry a `complicating` passage ref | C14 |
| 7 | Every safeguard-carrying topic's positions carry a `complicating` ref or an `intra_tradition_note` | C14 |
| 8 | ≥ 80% of positions have ≥ 1 quoted (non-paraphrase) `core` passage | C15 |
| 9 | `reference_only` passages are ≤ 15% of all cited passages | C15 |
| 10 | ≥ 120 distinct further-reading entries (key: casefolded `author\|title\|year`) | C16 |
| 11 | No further-reading entry appears in > 10% of positions | C16 |

### RouterConfig — `data/router.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `k1`, `b` | float | Okapi BM25 parameters; defaults 1.5, 0.75 |
| `w_phrase` | float | phrase-bonus weight; default 2.0 |
| `tau` | float | abstention score floor τ (FR-4) |
| `kappa` | float | abstention coverage floor κ (FR-4) |
| `tuning_note` | str | required, non-empty: which fixture tiers τ/κ were tuned on, when, and the resulting M2a/M2b/M1 values. C19 pins this file's SHA-256 in `evals/baselines.json`, so τ or κ cannot move without the note and every baseline being re-derived in the same commit |

### Stopwords — `data/stopwords.txt`

One word per line, lowercase, ~250 entries (van Rijsbergen 1979 lineage).
Invariants: no topic keyword term is composed entirely of stopwords; the
file's SHA-256 is pinned in `evals/baselines.json` (C19), because a silent
edit shifts every routing score and therefore every baseline.

## The Envelope (FR-6/FR-8/FR-9)

The envelope is the **only** serialization the polisher ever sees, and the
verifier's diff basis. It is a deterministic, line-oriented, LF-terminated
UTF-8 rendering of an `AnswerBody`. Regions are delimited by sentinels;
`[[I:…]]` regions are **immutable**, `[[M:…]]` regions are **mutable**.
Sentinels are forbidden inside corpus content (C1), so parsing is unambiguous.

```
[[ETHOS:1]]
[[I:meta]]{"topic_id":"…","corpus_version":"…","composer_version":"1",
           "requested_traditions":["…"],"forced":false}[[/I:meta]]
[[I:safeguard:crisis_resources]]…verbatim text…[[/I:safeguard:crisis_resources]]
[[I:routing]]{"topic_id":"…","confidence":0.65,
              "alternates":[{"topic_id":"…","score":4.1}]}[[/I:routing]]
[[P:kantian_deontology]]
[[I:header:kantian_deontology]]Kantian deontology[[/I:header:kantian_deontology]]
[[I:stance:kantian_deontology]]forbidden[[/I:stance:kantian_deontology]]
[[M:summary:kantian_deontology]]…curated summary prose…[[/M:summary:kantian_deontology]]
[[M:reason:kantian_deontology:1]]…reasoning prose…[[/M:reason:kantian_deontology:1]]
[[I:rmark:kantian_deontology:1]]C1[[/I:rmark:kantian_deontology:1]]
[[I:quote:C1]]…verbatim passage text or paraphrase…[[/I:quote:C1]]
[[I:qmeta:C1]]{"passage_id":"…","is_paraphrase":false,"locator":"Groundwork 4:421",
               "source_line":"…","label":null}[[/I:qmeta:C1]]
[[I:context:C1]]…context_note…[[/I:context:C1]]
[[M:intra:kantian_deontology]]…or omitted when null…[[/M:intra:kantian_deontology]]
[[I:reading:kantian_deontology]][{"author":"…","title":"…","year":2022,
   "kind":"sep","url":"…","note":null}][[/I:reading:kantian_deontology]]
[[/P:kantian_deontology]]
[[I:agreement]]{"forbidden":["christianity","kantian_deontology"],"…":[…]}[[/I:agreement]]
[[I:not_covered]]["stoicism"][[/I:not_covered]]
[[I:filtered_out]][][[/I:filtered_out]]
[[I:citations]]{"C1":{"passage_id":"…","locator":"…","source_id":"…"}}[[/I:citations]]
[[/ETHOS:1]]
```

Rules:

1. **Serialization** `AnswerBody → envelope` is a pure function; region order
   is fixed as above (safeguards → routing → perspectives in canonical order →
   agreement → not_covered → filtered_out → citations). JSON inside `[[I:…]]`
   regions is canonical (sorted keys, `separators=(",",":")`).
2. **Parse-back** `envelope → AnswerBody` is strict: every region must appear
   exactly once, in the serialized order, with a matching close sentinel, and
   `[[I:…]]` payloads must parse as the type they declare. Any deviation —
   including a missing region, a reordered region, an extra region, or
   unparseable JSON — is an **FR-8 violation** (not an exception): discard
   polish, serve the deterministic render, `polish_fell_back = true`.
3. **Verification runs on the parsed-back body and its plain-text render**,
   never on the raw polished string. What is persisted (`body`,
   `rendered_text`) is exactly what was verified — there is no path by which
   prose reaches the user without passing FR-8.
4. Only `[[M:…]]` payloads may differ from the pre-polish envelope. Everything
   else is compared byte-for-byte (FR-8 (a)–(d), (f)–(i)); changed `[[M:…]]`
   spans are additionally scanned per FR-8(e).

## Plain-text render (FR-7)

The render grammar is specified because the M3 checker re-parses it
independently of engine code (EVALS M3). Indentation is significant; lines are
LF-terminated; there is no color or markup.

```
=== Honesty and deception ===

[!] <safeguard text>                      # zero or more, always first, in safeguard_ids order

Matched topic: Honesty and deception (honesty_and_deception) — confidence 0.65
Other topics considered: Speech and gossip (speech_and_gossip) 4.10; Judging others (judging_others) 2.00

--- Kantian deontology — forbidden ---
<summary paragraph>
  • <reasoning point text> [C1]
      “<verbatim passage text>”
      — Groundwork 4:421 — Fundamental Principles of the Metaphysic of Morals, trans. T. K. Abbott (1873)
      Context: <context_note>
  Note: <intra_tradition_note>            # omitted when null
  Further reading:
      - Johnson & Cureton, “Kant's Moral Philosophy” (2022) [sep] https://plato.stanford.edu/entries/kant-moral/

--- Judaism — context_dependent ---
…
      [paraphrase — no public-domain translation quoted]
      <paraphrase text>
      — Ketubot 16b–17a — The Babylonian Talmud — Vilna edition, no public-domain English translation
      Context: <context_note>

Where traditions agree and differ:
  forbidden: Christianity, Islam, Kantian deontology
  context_dependent: Judaism, Confucianism
No curated position on this topic: Stoicism
Excluded by your filter (these traditions do have positions): —      # omitted when empty

Citations:
  [C1] kant-groundwork-4-421-false-promise — Groundwork 4:421 — kant-groundwork-abbott

Corpus 9f2c1a4e7b02 · composer 1
```

Parsing contract relied on by the M3 checker: a quote block is the `“…”`
delimited text at 6-space indent (single logical line, no interior `“`/`”`);
its citation line is the next line beginning with `      — `; a paraphrase
block is the exact label line `      [paraphrase — no public-domain
translation quoted]` followed by the paraphrase text and its citation line;
the `Citations:` section lists every marker exactly once.

## SQLite user-state entities

### CorpusMeta — table `corpus_meta` (singleton)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | CHECK `id = 1` |
| `corpus_version` | str | SHA-256 hex over all of `data/` (above) |
| `loaded_at` | str | ISO ts (input) |

Invariant: the repository refuses reads/writes of corpus tables when the
recomputed digest over `data/` differs from `corpus_version`, until
`ethos init` reloads (stale-corpus guard). Because the digest now covers
`router.json`, `safeguards.json` and `stopwords.txt`, editing any of them
fires the guard. Tested on both backends.

### Question — table `question` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `text` | str | as typed |
| `asked_at` | str | ISO ts (input; defaulted at the edge, never in the engine) |
| `outcome` | QuestionOutcome | |
| `forced_topic_id` | str \| null | non-null when `--topic`/`topic_id` bypassed routing |
| `routing` | JSON \| null | `{ranked: [{topic_id, score}] (top 5, may be empty), confidence, coverage, abstained}`; stored even for refusals; `null` iff `forced_topic_id` is set |

Invariants: append-only; `outcome = refused_out_of_scope` iff `abstained` and
no forced topic; a refused question has no answer.

### Answer — table `answer` (append-only, immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK autoincr | |
| `question_id` | int FK → question | exactly one answer per answered question |
| `created_at` | str | ISO ts (input) |
| `topic_id` | str | routed or forced topic |
| `options` | JSON | `{traditions: [id] \| null, polish: bool}` |
| `corpus_version` | str | must equal `corpus_meta.corpus_version` at creation |
| `composer_version` | str | e.g. `"1"`; bumped on template changes |
| `polish_used` | bool | live polisher ran and passed verification |
| `polish_fell_back` | bool | polisher ran, failed FR-8 (incl. unparseable envelope), deterministic render served |
| `verified` | bool | **must be `true`**; the repository rejects inserts with `false` (invariant, FR-8; tested on both backends) |
| `body` | JSON | full `AnswerBody` snapshot (below) |
| `rendered_text` | str | the exact verified plain-text render that was shown (US-7); re-rendering `body` under the same `composer_version` must reproduce it byte-for-byte (D0) |

`AnswerBody` JSON structure (schema enforced by Pydantic):

```
{
  "safeguards":   [{id, kind, text}],                    # possibly empty; first in render order
  "routing":      {topic_id, confidence, alternates: [{topic_id, score}], forced}
                  # forced asks: {topic_id, confidence: null, alternates: [], forced: true}
  "perspectives": [                                      # canonical Tradition.order, rendered set only
    { "tradition_id": ..., "tradition_name": ...,        # name is the header (FR-8(h))
      "position_id": ..., "stance": ...,                 # stance immutable (FR-8(i))
      "summary": ...,                                    # mutable prose
      "reasoning": [{text, marker: "C3" | null}],        # text mutable; marker immutable
      "quotes": [                                        # immutable regions
        {marker, passage_id, text | paraphrase, is_paraphrase, label, locator,
         source_line, context_note}
      ],
      "intra_tradition_note": ... | null,                # mutable prose
      "further_reading": [{author, title, year, kind, url, note}]   # immutable, committed order
    }
  ],
  "not_covered":  [tradition_id],   # requested ∧ no position on this topic
  "filtered_out": [tradition_id],   # has a position ∧ excluded by the caller's filter
  "agreement_map": {stance: [tradition_id]},             # partitions the rendered perspectives exactly
  "citations":    {marker: {passage_id, locator, source_id}},
  "corpus_version": ..., "composer_version": ...
}
```

Invariants (all verified by FR-8 before persistence):

- rows immutable once inserted;
- the set of distinct markers appearing in `reasoning` + `quotes` equals the
  key set of `citations`; each key has exactly one quote block; markers are
  `C1..Cn` in first-appearance order;
- `agreement_map` values partition the `perspectives` tradition ids exactly;
- `not_covered` and `filtered_out` are disjoint, and
  `not_covered ∪ filtered_out ∪ rendered = requested ∪ (traditions with a
  position on the topic)`;
- `quotes[].text` byte-equals (NFC + newline normalized; byte-exact on the
  null-polisher path) the committed passage text, and every `locator`,
  `source_line`, `label`, `is_paraphrase`, and `context_note` comes from the
  same passage record;
- `further_reading` byte-equals the position record in committed order;
- `safeguards` is non-empty whenever the topic declares `safeguard_ids`, its
  entries' `text` byte-equal `data/safeguards.json`, and it renders first.

## Relationships (summary)

```
Tradition (10) ──< Position >── Topic (≥24)      unique (topic, tradition)
Position ──< PassageRef >── Passage ──▶ Source   (≥1 core per position;
                                                  ≤3 positions per core passage)
Position ──< FurtherReading (embedded, ≥1)
Topic ──< safeguard_ids ──▶ Safeguard
Question (append-only) ──< Answer (0..1, immutable, verified=true)
Answer.body.citations ──▶ Passage                (verified at compose time)
data/**  : source of truth, hashed into corpus_version
SQLite corpus tables : read-only load, guarded against staleness
Coverage matrix, reading aggregation : derived, never stored
Plain-text render : derived AND stored (rendered_text) — US-7
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

> Weight-1 and weight-2 keyword terms are the scenario-vocabulary channel the
> oblique tiers are designed to exercise (SCOPE D9). Fixture gate C9
> constrains oblique questions against the topic's title, description,
> question forms and **weight-3** terms only — deliberately leaving this
> channel open, because filling it is the craft, not the cheat. The
> anti-memorisation instrument is the held-out oblique tier and its gap gate
> (EVALS M1b′), not a wider ban.

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

`passage` (from `passages/confucianism.json`) — note `‘ ’` for inner
quotation, since `“ ”` are reserved as render delimiters:

```json
{
  "id": "analects-13-18", "source_id": "analects-legge",
  "locator": "Analects XIII.18",
  "text": "The Duke of She informed Confucius, saying, ‘Among us here there are those who may be styled upright in their conduct. If their father have stolen a sheep, they will bear witness to the fact.’ Confucius said, ‘Among us, in our part of the country, those who are upright are different from this. The father conceals the misconduct of the son, and the son conceals the misconduct of the father. Uprightness is to be found in this.’",
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
    {"text": "Trustworthiness is repeatedly named among the essentials of character and government; without it a person cannot get on.", "passage_id": "analects-2-22"},
    {"text": "Relational duties can override third-party claims to disclosure, as in the case of the sheep-stealing father.", "passage_id": "analects-13-18"},
    {"text": "Speech should be measured and slow to outrun action — glibness is a vice even when accurate.", "passage_id": "analects-4-24"}
  ],
  "passages": [
    {"passage_id": "analects-2-22", "role": "core", "note": null},
    {"passage_id": "analects-13-18", "role": "core", "note": "the classic hard case"},
    {"passage_id": "analects-4-24", "role": "supporting", "note": null},
    {"passage_id": "analects-15-8", "role": "complicating", "note": "the upright officer who does not conceal"}
  ],
  "intra_tradition_note": "Later Confucians (notably Zhu Xi's school) read XIII.18 narrowly, as protecting the parent-child bond, not licensing deception generally; Mencius extends the priority of familial devotion.",
  "further_reading": [
    {"author": "Mark Csikszentmihalyi", "title": "Confucius", "year": 2020, "kind": "sep", "url": "https://plato.stanford.edu/entries/confucius/", "note": "survey with a section on the upright-father case"},
    {"author": "Daniel K. Gardner", "title": "Confucianism: A Very Short Introduction", "year": 2014, "kind": "book", "url": null, "note": null}
  ],
  "curator_note": "Drafted from Legge's Analects (II.22, IV.24, XIII.18, XV.8) with Csikszentmihalyi (SEP) for the reception history."
}
```

> The `intra_tradition_note` above contains "read XIII.18 narrowly", which
> matches the *validation* pattern for `book_section`. This is exactly why
> FR-8(e) scans only **changed** spans of mutable prose using the narrower
> prose-scan set: curated prose that names a locator is normal and must pass;
> a polisher that *introduces* one must not.

`question` + `answer` (abridged):

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
    "confidence": 0.65, "coverage": 0.71, "abstained": false
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
  "rendered_text": "=== Honesty and deception ===\n\nMatched topic: …",
  "body": {
    "safeguards": [],
    "routing": {"topic_id": "honesty_and_deception", "confidence": 0.65,
                "alternates": [{"topic_id": "speech_and_gossip", "score": 4.1},
                               {"topic_id": "judging_others", "score": 2.0}],
                "forced": false},
    "perspectives": [
      {"tradition_id": "kantian_deontology", "tradition_name": "Kantian deontology",
       "position_id": "honesty_and_deception--kantian_deontology",
       "stance": "forbidden",
       "summary": "…",
       "reasoning": [{"text": "A maxim of lying cannot be universalized without destroying the practice of assertion it depends on.", "marker": "C1"}],
       "quotes": [{"marker": "C1", "passage_id": "kant-groundwork-4-421-false-promise",
                   "text": "…", "is_paraphrase": false, "label": null,
                   "locator": "Groundwork 4:421",
                   "source_line": "Fundamental Principles of the Metaphysic of Morals, trans. T. K. Abbott (1873)",
                   "context_note": "…"}],
       "intra_tradition_note": null,
       "further_reading": [{"author": "Robert Johnson & Adam Cureton", "title": "Kant's Moral Philosophy", "year": 2022, "kind": "sep", "url": "https://plato.stanford.edu/entries/kant-moral/", "note": null}]}
    ],
    "not_covered": [],
    "filtered_out": [],
    "agreement_map": {
      "forbidden": ["christianity", "islam", "buddhism", "stoicism", "kantian_deontology"],
      "context_dependent": ["judaism", "hinduism", "confucianism", "utilitarianism"],
      "reframed": ["virtue_ethics"]
    },
    "citations": {"C1": {"passage_id": "kant-groundwork-4-421-false-promise",
                          "locator": "Groundwork 4:421", "source_id": "kant-groundwork-abbott"}},
    "corpus_version": "9f2c…e1", "composer_version": "1"
  }
}
```

`router.json` and one `safeguards.json` entry:

```json
{
  "k1": 1.5, "b": 0.75, "w_phrase": 2.0, "tau": 6.0, "kappa": 0.42,
  "tuning_note": "τ and κ swept jointly on the direct+colloquial tiers only (168 questions) on 2026-08-xx; oblique tiers and oos_questions.json were hash-frozen beforehand and not inspected. Chosen point: M1-direct 0.98, M1-coll 0.89, M2a 0.85, M2b 0.03. See evals/baselines.json."
}
```

```json
{
  "id": "crisis_resources", "kind": "crisis_resources",
  "text": "If you are thinking about suicide or self-harm, please reach out now: call or text 988 (Suicide & Crisis Lifeline, US), or find local helplines at befrienders.org or findahelpline.com. What follows are scholarly summaries of how traditions have discussed this topic — it is not counseling."
}
```
