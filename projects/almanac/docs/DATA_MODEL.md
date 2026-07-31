# Almanac — Data Model

Two kinds of data:

- **Committed datasets** under `projects/almanac/data/` — the theme
  taxonomy, prompt bank, scheduler parameters, misattribution dataset, and
  starter pack. Source of truth is the JSON file; `almanac init` loads them
  into read-only SQLite tables for uniform querying and validates floors
  (SCOPE.md FR-3/FR-9). Edited in git only.
- **User data** in SQLite (default `~/.almanac/almanac.db`, override
  `ALMANAC_DB_PATH`) — entries, tags, collections, surfacings, reflections,
  attribution flags, config, and the derived scheduler-state cache. An
  in-memory repository implements the same interface for tests/evals.

Conventions: ids are 26-char ULID strings minted by an injected id factory;
`*_at` fields are ISO-8601 UTC timestamps supplied by the caller;
`*_on` fields are ISO local calendar dates (`YYYY-MM-DD`) — the scheduler's
unit of time (SCOPE.md D14). All Pydantic v2 models live in
`src/almanac/models.py`; enums are string-valued.

## Entities

### Entry (user data — the quote or idea)

| field | type | notes |
|---|---|---|
| id | str (ULID) | PK |
| kind | enum `quote \| idea` | required |
| text | str | required, 1–2,000 chars, stored verbatim |
| normalized_hash | str | SHA-256 hex of NFKC → casefold → strip punctuation/whitespace (FR-1); indexed |
| author | str \| None | free text; None common for `idea` |
| source | str \| None | free text, e.g. book + locator |
| url | str \| None | where it came from |
| note | str \| None | the personal capture note, 0–2,000 chars |
| pinned | bool | default false |
| status | enum `active \| archived` | default `active` |
| captured_on | date | drives novelty age (FR-7); defaults to created_at's date at the edge |
| created_at / updated_at | datetime | |

Invariants: `text` immutable after any surfacing exists (edits before first
surfacing allowed; after that, corrections go through archive + re-add, so
surfacing snapshots never dangle semantically). `normalized_hash` is
soft-unique among non-archived entries: collisions are allowed but always
reported (FR-1). Archived entries are excluded from scheduling, search
defaults, and duplicate warnings, and keep all history.

### Tag / EntryTag (user data)

`Tag`: `id` ULID PK, `name` str unique — normalized casefold, spaces →
hyphens, 1–40 chars. `EntryTag`: (`entry_id`, `tag_id`) composite PK, FKs.
Tags are free-form; deleting a tag (CLI `tags` has no delete in MVP — tags
are garbage-collected when their last entry link is removed) never touches
entries.

### Theme (committed — `data/themes.json`)

| field | type | notes |
|---|---|---|
| id | str slug | PK, one of the 16 fixed ids (SCOPE.md D8) |
| name | str | display name |
| description | str | one sentence |
| lexicon | list[LexiconTerm] | `{term: str, weight: float}`; terms are words or multi-word phrases, matched stemmed (FR-3) |

Invariants: exactly 16 themes; ids stable forever (surfacing history and
templates reference them); every theme's lexicon has ≥ 8 terms. `general`
is **not** a Theme row — it is a reserved template-pool id (see
PromptTemplate).

### EntryTheme (user data)

(`entry_id`, `theme_id`) composite PK; `source` enum `user \| suggested` —
`suggested` means the user accepted a suggester proposal (provenance for
later lexicon tuning). Max 3 themes per entry (checked at service layer).

### PromptTemplate (committed — `data/prompts.json`)

| field | type | notes |
|---|---|---|
| id | str slug | PK, stable forever (e.g. `mortality_act_02`) |
| theme_id | str | a Theme id or the literal `general` |
| kind | enum `reflect \| act \| reframe \| connect` | |
| template | str | text with slots from {`{author}`, `{theme_name}`, `{source}`, `{text_short}`}; ≤ 300 chars |

Invariants (enforced by a data-validation test): every theme has ≥ 6
templates covering all 4 kinds ≥ 1 each; `general` has ≥ 8 covering all 4
kinds; `act` templates contain the literal scaffold "When " and " I will "
(FR-10e); `reflect`/`connect` templates end with "?"; no template contains
an unfillable slot name.

### Surfacing (user data — immutable card snapshot)

| field | type | notes |
|---|---|---|
| id | str (ULID) | PK |
| entry_id | str | FK → Entry |
| on_date | date | the calendar date surfaced |
| slot | int | 0…k−1 for `daily`; 0 for `extra` |
| kind | enum `daily \| extra` | extra = on-demand draw (FR-8) |
| prompt_template_id | str | FK → PromptTemplate |
| prompt_kind | enum | copied from the template at render time |
| prompt_text | str | the text actually shown (post-personalizer-or-fallback) |
| personalized | bool | live personalizer produced the text |
| personalize_fell_back | bool | validator rejected the personalizer output (FR-10) |
| relaxed_cooldown | bool | tiny-library fallback fired (FR-7) |
| filter_theme_id / filter_collection_id | str \| None | for `extra` draws only |
| scheduler_version | str | `params_version` from scheduler.json |
| seed | int | seed in effect when selected |
| created_at | datetime | |

Invariants: **append-only, never mutated or deleted.** Unique
(`on_date`, `slot`) among `kind = daily` rows; unique (`on_date`,
`entry_id`) across *all* kinds (one exposure per entry per day). Daily
materialization dates are strictly increasing: inserting a `daily` row with
`on_date` earlier than the max existing `daily` `on_date` is rejected
(FR-8). `prompt_text` is a snapshot — later template edits don't rewrite
history.

### Reflection (user data — append-only journal)

| field | type | notes |
|---|---|---|
| id | str (ULID) | PK |
| surfacing_id | str | FK → Surfacing, **unique** (one reflection per surfacing) |
| entry_id | str | FK → Entry (denormalized for journal queries) |
| grade | enum `applied \| resonated \| flat` | drives FR-6 |
| text | str \| None | the "how I applied it" note, ≤ 4,000 chars |
| logged_at | datetime | |

Invariants: append-only, immutable. Insertion allowed only while
`surfacing_id` is the entry's most recent surfacing (service-enforced,
409 otherwise) — this keeps the FR-6 fold unambiguous: by the time an entry
is surfaced again, its previous surfacing's grade is final (`none` if
absent).

### SchedulerState (derived — materialized cache of the FR-6 fold)

| field | type | notes |
|---|---|---|
| entry_id | str | PK, FK → Entry |
| exposure_count | int | # surfacings (daily + extra) |
| last_surfaced_on | date \| None | |
| interval_days | int | current interval; meaningless until exposure_count ≥ 1 |
| flat_streak | int | consecutive `flat` grades |

**Derived, never authoritative.** Definition — for entry e, order its
surfacings s₁…sₙ by (`on_date`, `created_at`); let gᵢ = grade of the
reflection on sᵢ, or `none` if absent. Then:

```
state after s₁:  exposure_count = 1, last = s₁.on_date, I = I0 = 3, flat_streak = 0
state after sᵢ (i ≥ 2):
    I ← clamp(round_half_up(I × m(g_{i−1})), lo, hi)   # multipliers/caps: FR-6 table
    flat_streak ← per FR-6 table on g_{i−1}
    exposure_count = i, last = sᵢ.on_date
```

Due-ness at date D uses `I_eff = min(I, 21)` when the entry is pinned,
else `I`. Invariant (eval-gated, M7): for every entry, the cached row
equals the fold recomputed from the event log; `almanac init --rebuild`
(and the memory repo on load) recompute it wholesale. Note the fold reads
g_{i−1} *at the moment sᵢ exists* — legal because Reflection's
insertion-window invariant freezes g_{i−1} before sᵢ can be created.

### Collection / CollectionEntry (user data)

`Collection`: `id` ULID PK, `name` str unique (1–80 chars), `description`
str | None, `created_at`. `CollectionEntry`: (`collection_id`, `entry_id`)
composite PK, `position` int — unique (`collection_id`, `position`),
contiguous from 0, reassigned on remove/reorder. Deleting a collection
deletes only its membership rows. Archived entries may remain members
(rendered struck-through; excluded from `draw --collection`).

### AttributionFlag (user data)

| field | type | notes |
|---|---|---|
| id | str (ULID) | PK |
| entry_id | str | FK → Entry |
| misattribution_id | str | FK → MisattributionRecord (null for live-adapter flags) |
| verdict | enum `misattributed \| disputed \| unverified` | |
| note | str | human-readable explanation |
| reference_url | str \| None | QI / Wikiquote link |
| checked_at | datetime | |

Unique (`entry_id`, `misattribution_id`). Re-running a check upserts by
that key. Flags never block anything (FR-2).

### MisattributionRecord (committed — `data/misattributions.json`)

| field | type | notes |
|---|---|---|
| id | str slug | PK, e.g. `insanity-einstein` |
| pattern | str | normalized token phrase that must appear (containment match) in the normalized entry text |
| claimed_authors | list[str] | normalized author names that trigger the flag (empty = any author) |
| verdict | enum as above | |
| likely_origin | str | best-known true origin, e.g. "1981 Narcotics Anonymous pamphlet (per Quote Investigator)" |
| note | str | one-to-three sentences |
| reference_url | str | QI or Wikiquote citation |

~50 records at launch; every record carries a real reference URL
(curation protocol: only cases QI or Wikiquote documents explicitly).
Match rule (FR-2): flag iff `pattern` ⊆ normalized entry tokens
(contiguous) AND (claimed_authors empty OR normalized entry author ∈
claimed_authors).

### SchedulerParams (committed — `data/scheduler.json`)

Single object: `params_version` (str, bumped on any change), `W` = 10,
`rho` = 0.35, `H` = 28, `S` = 90, `tau` = 7, `I0` = 3, `k` = 1,
`multipliers` = {applied: 2.3, resonated: 1.6, none: 1.4, flat: 3.0},
`clamp` = {lo: 2, hi: 120, hi_flat: 180}, `pinned_cap` = 21,
`jitter` = 0.05, `prompt_reuse_window` = 6. Loaded read-only; user
overrides for `k` and `seed` live in Config, not here.

### Config (user data — key/value)

`key` str PK, `value` str (JSON-encoded). Keys: `seed` (int; set at init,
default 0; changeable via `almanac config set seed`), `batch_k` (1–5,
default from params), `schema_version`, `data_versions` (loaded dataset
versions). Every Surfacing stamps the seed/params in effect, so config
changes are auditable and never rewrite history.

## Relationships (summary)

```
Entry 1—* EntryTag *—1 Tag            Entry 1—* EntryTheme *—1 Theme
Entry 1—* Surfacing 1—0..1 Reflection Entry 1—0..1 SchedulerState (derived)
Entry 1—* AttributionFlag *—0..1 MisattributionRecord
Collection 1—* CollectionEntry *—1 Entry
Surfacing *—1 PromptTemplate          PromptTemplate *—1 Theme (or 'general')
```

## SQLite mapping

```sql
-- user data
CREATE TABLE entries (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('quote','idea')),
  text TEXT NOT NULL, normalized_hash TEXT NOT NULL,
  author TEXT, source TEXT, url TEXT, note TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  captured_on TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_entries_hash ON entries(normalized_hash);
CREATE INDEX idx_entries_status ON entries(status, pinned);

CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE entry_tags (entry_id TEXT NOT NULL REFERENCES entries(id),
  tag_id TEXT NOT NULL REFERENCES tags(id), PRIMARY KEY (entry_id, tag_id));
CREATE TABLE entry_themes (entry_id TEXT NOT NULL REFERENCES entries(id),
  theme_id TEXT NOT NULL REFERENCES themes(id),
  source TEXT NOT NULL CHECK (source IN ('user','suggested')),
  PRIMARY KEY (entry_id, theme_id));

CREATE TABLE surfacings (
  id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES entries(id),
  on_date TEXT NOT NULL, slot INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('daily','extra')),
  prompt_template_id TEXT NOT NULL, prompt_kind TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  personalized INTEGER NOT NULL DEFAULT 0,
  personalize_fell_back INTEGER NOT NULL DEFAULT 0,
  relaxed_cooldown INTEGER NOT NULL DEFAULT 0,
  filter_theme_id TEXT, filter_collection_id TEXT,
  scheduler_version TEXT NOT NULL, seed INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (on_date, entry_id));
CREATE UNIQUE INDEX idx_surf_daily_slot ON surfacings(on_date, slot)
  WHERE kind = 'daily';
CREATE INDEX idx_surf_entry ON surfacings(entry_id, on_date);

CREATE TABLE reflections (
  id TEXT PRIMARY KEY,
  surfacing_id TEXT NOT NULL UNIQUE REFERENCES surfacings(id),
  entry_id TEXT NOT NULL REFERENCES entries(id),
  grade TEXT NOT NULL CHECK (grade IN ('applied','resonated','flat')),
  text TEXT, logged_at TEXT NOT NULL);

CREATE TABLE scheduler_state (          -- derived cache; rebuildable
  entry_id TEXT PRIMARY KEY REFERENCES entries(id),
  exposure_count INTEGER NOT NULL, last_surfaced_on TEXT,
  interval_days INTEGER NOT NULL, flat_streak INTEGER NOT NULL);

CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  description TEXT, created_at TEXT NOT NULL);
CREATE TABLE collection_entries (
  collection_id TEXT NOT NULL REFERENCES collections(id),
  entry_id TEXT NOT NULL REFERENCES entries(id), position INTEGER NOT NULL,
  PRIMARY KEY (collection_id, entry_id),
  UNIQUE (collection_id, position));

CREATE TABLE attribution_flags (id TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL REFERENCES entries(id),
  misattribution_id TEXT, verdict TEXT NOT NULL, note TEXT NOT NULL,
  reference_url TEXT, checked_at TEXT NOT NULL,
  UNIQUE (entry_id, misattribution_id));

CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- committed datasets, loaded read-only at init
CREATE TABLE themes (id TEXT PRIMARY KEY, name TEXT NOT NULL,
  description TEXT NOT NULL, lexicon_json TEXT NOT NULL);
CREATE TABLE prompt_templates (id TEXT PRIMARY KEY, theme_id TEXT NOT NULL,
  kind TEXT NOT NULL, template TEXT NOT NULL);
CREATE TABLE misattributions (id TEXT PRIMARY KEY, pattern TEXT NOT NULL,
  claimed_authors_json TEXT NOT NULL, verdict TEXT NOT NULL,
  likely_origin TEXT NOT NULL, note TEXT NOT NULL, reference_url TEXT NOT NULL);

-- search
CREATE VIRTUAL TABLE entries_fts USING fts5(
  text, author, source, note, content='entries', content_rowid='rowid',
  tokenize='porter unicode61');
-- kept in sync by repository code on entry insert/update/archive;
-- LIKE-scan fallback if FTS5 is unavailable (FR-12).
```

## Example records

**Entry** (with themes, tag, and an attribution flag):

```json
{"id": "01J1ZK7Q9GVX4N8B2M5C3T7R6A", "kind": "quote",
 "text": "You have power over your mind - not outside events. Realize this, and you will find strength.",
 "normalized_hash": "d41f9c…", "author": "Marcus Aurelius",
 "source": "Meditations (Long trans., 1862)", "url": null,
 "note": "For the mornings when the inbox sets my mood before I do.",
 "pinned": true, "status": "active", "captured_on": "2026-07-02",
 "created_at": "2026-07-02T08:14:03Z", "updated_at": "2026-07-02T08:14:03Z"}
-- entry_themes: (…R6A, "equanimity_and_anger", "suggested"), (…R6A, "attention_and_presence", "user")
-- entry_tags: (…R6A, "stoicism")
```

```json
{"id": "01J2AB3C4D5E6F7G8H9J0K1M2N", "kind": "idea",
 "text": "Write the email you're dreading first; every other task is procrastination wearing a costume.",
 "normalized_hash": "9a02e7…", "author": null, "source": null, "url": null,
 "note": "From the 2026-06-30 weekly review.", "pinned": false,
 "status": "active", "captured_on": "2026-06-30",
 "created_at": "2026-06-30T21:40:11Z", "updated_at": "2026-06-30T21:40:11Z"}
-- entry_themes: (…M2N, "decision_and_action", "suggested")
```

**Theme** (excerpt):

```json
{"id": "mortality_and_time", "name": "Mortality & Time",
 "description": "Finitude, urgency, and spending attention like the currency it is.",
 "lexicon": [{"term": "death", "weight": 2.0}, {"term": "mortal", "weight": 2.0},
             {"term": "time", "weight": 1.0}, {"term": "brief", "weight": 1.5},
             {"term": "memento mori", "weight": 3.0}, {"term": "someday", "weight": 1.0},
             {"term": "waste of life", "weight": 2.5}, {"term": "hour", "weight": 0.5}]}
```

**PromptTemplate** (one per kind):

```json
{"id": "mortality_reflect_01", "theme_id": "mortality_and_time", "kind": "reflect",
 "template": "Tonight, review the day as {author} might: which hour was spent as if time were infinite?"}
{"id": "mortality_act_02", "theme_id": "mortality_and_time", "kind": "act",
 "template": "Pick the task you keep deferring. When I sit down at my desk today, I will spend the first 20 minutes on it."}
{"id": "general_reframe_03", "theme_id": "general", "kind": "reframe",
 "template": "Imagine the day going wrong in the exact way {text_short} warns about. What would you do next."}
{"id": "relationships_connect_01", "theme_id": "relationships", "kind": "connect",
 "template": "Who in your life most needs to hear this idea from {source} - and why is it true for them?"}
```

**Surfacing + Reflection:**

```json
{"id": "01J30XYZ0PQR1STU2VWX3YZ4AB", "entry_id": "01J1ZK7Q9GVX4N8B2M5C3T7R6A",
 "on_date": "2026-07-30", "slot": 0, "kind": "daily",
 "prompt_template_id": "equanimity_act_01", "prompt_kind": "act",
 "prompt_text": "When the first frustrating message arrives today, I will name the feeling before replying.",
 "personalized": false, "personalize_fell_back": false, "relaxed_cooldown": false,
 "filter_theme_id": null, "filter_collection_id": null,
 "scheduler_version": "sched-1", "seed": 7, "created_at": "2026-07-30T07:01:22Z"}

{"id": "01J31M2N3P4Q5R6S7T8U9V0WXY",
 "surfacing_id": "01J30XYZ0PQR1STU2VWX3YZ4AB",
 "entry_id": "01J1ZK7Q9GVX4N8B2M5C3T7R6A", "grade": "applied",
 "text": "Named it ('defensiveness'), waited ten minutes, reply came out civil.",
 "logged_at": "2026-07-30T22:05:40Z"}
```

**MisattributionRecord:**

```json
{"id": "insanity-einstein",
 "pattern": "insanity is doing the same thing over and over",
 "claimed_authors": ["albert einstein", "einstein"],
 "verdict": "misattributed",
 "likely_origin": "1981 Narcotics Anonymous approval-draft pamphlet (per Quote Investigator)",
 "note": "No Einstein source exists; earliest documented appearance is a 1981 Narcotics Anonymous text. Widely reassigned to Einstein in the 1980s-90s.",
 "reference_url": "https://quoteinvestigator.com/2017/03/23/same/"}
```

**SchedulerState** (derived, for the Marcus Aurelius entry above after 3
exposures graded resonated, none, applied):

```json
{"entry_id": "01J1ZK7Q9GVX4N8B2M5C3T7R6A", "exposure_count": 3,
 "last_surfaced_on": "2026-07-30", "interval_days": 7, "flat_streak": 0}
```

(I0 = 3 after debut; × 1.6 for `resonated` → 5; × 1.4 for `none` → 7;
the `applied` grade on the 2026-07-30 surfacing multiplies at the *next*
surfacing per the fold. Entry is pinned, so due-ness uses
I_eff = min(7, 21) = 7.)
