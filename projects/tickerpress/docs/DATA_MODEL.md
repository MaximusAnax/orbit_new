# TickerPress — Data Model

All domain models are Pydantic v2 classes in `engine/models.py`; the store
maps persistent entities to SQLite (stdlib `sqlite3`). Times are ISO-8601 UTC
strings supplied by callers via the Clock port (the engine never reads the
clock). Character offsets always refer to the **normalized** field text
(post FR-3 normalization) of the named field — never to raw feed bytes.
Integer ids are SQLite rowids; the company key is its ticker.

## 1. Entity overview

```
Company 1 ──< Alias
Feed    1 ──< Article >── 1 Story          (article.story_id set once at ingest)
Article 1 ──< Mention  (all candidates, accepted and rejected)
(Article × Company) ──  Appearance         (exists iff ≥1 accepted mention)
IngestRun                                  (append-only log of ingest passes)
Delivery 1 ──< DeliveryItem                (append-only exactly-once ledger)
```

Committed lexicon files in `data/` (`cues_corporate.txt`,
`common_words.txt`, `legal_suffixes.txt`, `abbreviations.txt`,
`tracking_params.txt`, `digest_template.md`) are engine data, not DB rows;
they version with the code and are covered by `engine_version`.

## 2. Entities

### 2.1 Company

The watchlist entry. One row per tracked company.

| Field | Type | Notes |
|---|---|---|
| `ticker` | str, PK | uppercase, `^[A-Z]{1,5}(\.[A-Z])?$` (share classes: `BRK.B`) |
| `name` | str, required | display/legal name as entered |
| `mode` | enum `digest \| alert \| both \| mute` | default `digest`; `mute` = keep detecting/archiving, never deliver |
| `min_relevance` | int 0–100 | default 20; digest inclusion floor (story-level) |
| `alert_min_relevance` | int 0–100 | default 60; alert trigger floor (story-level) |
| `context_terms` | list[str] (JSON) | company-specific positive evidence, case-folded (e.g. Apple: `iphone`, `cupertino`, `app store`) |
| `anti_terms` | list[str] (JSON) | company-specific negative evidence (e.g. Apple: `orchard`, `fruit`, `cider`, `harvest`) |
| `created_at` | datetime | injected via Clock |

**Invariants:** ticker validated + uppercased on write. Deleting a company
cascades to its aliases, mentions, and appearances; articles, stories, and
deliveries are never deleted (the ledger is history — a re-added company
will not re-receive stories already delivered, by design).

### 2.2 Alias

One matchable surface form. Generated rows (ticker, cashtag, legal name,
suffix-stripped short name) are created by `company add` (SCOPE FR-1).

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `company_ticker` | str, FK Company | cascade delete |
| `text` | str, required | surface form with intended capitalization (`Apple`, `AAPL`, `$AAPL`, `Apple Inc.`) |
| `kind` | enum `legal_name \| short_name \| ticker_symbol \| cashtag \| nickname` | drives matching rules (SCOPE FR-5) |
| `strength` | enum `strong \| weak` | strong ⇒ auto-accept; weak ⇒ FR-6 scoring |
| `prior` | float 0–0.3 | commonness prior for weak scoring; defaults by kind (below) |
| `generated` | bool | true for auto-created aliases |
| `created_at` | datetime | |

Kind defaults applied at creation (all overridable):

| kind | default strength | default prior |
|---|---|---|
| `legal_name` | strong | — |
| `ticker_symbol` | strong, **demoted to weak if its lowercase form is in `data/common_words.txt`** (`ALL`, `CAT`, `META`, …) | 0.05 when weak |
| `cashtag` | strong | — |
| `short_name` | weak | 0.25 |
| `nickname` | weak | 0.10 |

**Invariants:** `(company_ticker, casefold(text))` unique. `text` non-empty,
no leading/trailing whitespace. Length-1 `ticker_symbol` aliases are stored
but the matcher never fires them bare (cashtag/exchange-parens only) —
enforced in `detect.py`, asserted by tests. Alias edits affect future
ingests only; existing Mention rows keep their `alias_id` (FK is
`ON DELETE SET NULL` so history survives alias cleanup).

### 2.3 Feed

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `name` | str, required | unique; used as "outlet" in digests |
| `url` | str, required | unique; `file://` → FixtureFeedSource, `http(s)://` → live adapter |
| `enabled` | bool | default true |
| `etag` | str \| None | live HTTP state (RFC 9110 conditional GET) |
| `last_modified` | str \| None | live HTTP state, raw header value |
| `last_polled_at` | datetime \| None | set from injected now on each fetch attempt |
| `last_status` | enum `ok \| not_modified \| error` \| None | last FetchResult status |
| `created_at` | datetime | |

**Invariants:** `etag`/`last_modified`/`last_polled_at`/`last_status` are the
only mutable fields, updated once per fetch. Deleting a feed is allowed only
when it has no articles (otherwise disable it) — archive integrity.

### 2.4 IngestRun (append-only)

One `ingest` invocation.

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `started_at` / `finished_at` | datetime | injected via Clock |
| `status` | enum `succeeded \| partial \| failed` | `partial` = ≥1 feed errored, others completed |
| `feed_results` | JSON | per feed: `{feed_id, status, items_seen, items_new, skipped, error}` |
| `articles_new` | int | |
| `candidates_total` | int | mentions scanned (accepted + rejected) |
| `mentions_accepted` | int | |
| `stories_new` | int | |
| `alerts_sent` | int | |
| `engine_version` | str | package version |

**Invariants:** append-only; never updated after `finished_at` set. Counter
fields must reconcile with rows created by the run (checked by tests).

### 2.5 Article (append-only, story-assignment excepted)

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `feed_id` | int, FK Feed | |
| `item_guid` | str | RSS `guid` / `atom:id`, else `sha256(link + "\n" + title)` |
| `url` | str | item link as published |
| `canonical_url` | str | per SCOPE FR-4 canonicalization |
| `title` | str | normalized text |
| `summary` | str | normalized `description` text; `""` if absent |
| `content` | str \| None | normalized `content:encoded` / `atom:content` when present |
| `published_at` | datetime | from feed, else ingest `now` |
| `published_source` | enum `feed \| fallback` | |
| `first_seen_at` | datetime | ingest `now` |
| `last_seen_at` | datetime | bumped when the same (feed, guid) is re-seen |
| `content_sha256` | str (64 hex) | over normalized `title\nsummary\ncontent` |
| `token_count` | int | total tokens across fields (lede rule input) |
| `story_id` | int, FK Story | assigned exactly once, during the same ingest transaction that inserts the row |
| `dedup_similarity` | float \| None | the Jaccard J against the argmax article when joining an existing story (1.0 for canonical-URL/content-hash fast paths); None when this article opened a new story. Explain (FR-13) reads this, never recomputes |

**Invariants:** `(feed_id, item_guid)` unique. All fields immutable after
insert except `last_seen_at`. `story_id` NOT NULL — every archived article
belongs to exactly one story before its transaction commits. Re-seen items
with changed content keep the first-ingested version (SCOPE D16).

### 2.6 Story

A deduplicated news story; a cluster of syndicated copies.

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `created_at` | datetime | ingest `now` of the first member |
| `first_published_at` | datetime | min over members' `published_at`; maintained on member add |
| `representative_article_id` | int, FK Article | earliest `published_at`, tie → smallest article id |

**Invariants:** stories never merge or split (SCOPE D6).
`representative_article_id` and `first_published_at` are the only mutable
fields, re-pointed only when a new member has strictly earlier
`published_at`. `article_count` is derived (`COUNT(*)` over members), never
stored. Story-level relevance per company is derived: `MAX(relevance)` over
member appearances (query/view, not a table).

### 2.7 Mention

One candidate detection — accepted or rejected. The explain surface and the
eval substrate.

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `article_id` | int, FK Article | cascade delete (only via company delete paths — articles themselves persist) |
| `company_ticker` | str, FK Company | cascade delete |
| `alias_id` | int \| None, FK Alias | `ON DELETE SET NULL`; None also for pattern hits whose alias was later removed |
| `field` | enum `title \| summary \| content` | |
| `char_start` / `char_end` | int | offsets into the normalized field text; `0 ≤ start < end` |
| `surface` | str | exact matched text |
| `matched_via` | enum `alias \| cashtag \| exchange_qualified` | which recognizer fired |
| `strength` | enum `strong \| weak` | effective strength at match time |
| `features` | JSON | full FR-6 feature vector, e.g. `{"prior":0.25,"coref_strong":0,"case_signal":1,"window_cues":1,"window_antis":0,"doc_cues":2,"doc_antis":0,"ctx_terms":1,"anti_terms":0,"hyphen_compound":0,"allcaps_run":0}` |
| `score` | float [0,1] | 1.0 for strong |
| `threshold` | float | θ in effect (default 0.35) |
| `accepted` | bool | `score ≥ threshold` |
| `engine_version` | str | |

**Invariants:** written once at ingest, never updated. Recomputable: given
the article's normalized text, the watchlist state at ingest time, and
`engine_version`, the row set is exactly reproducible (eval M5 checks this).
`accepted == (score >= threshold)` is a CHECK-level truth, asserted in
tests.

### 2.8 Appearance

Rollup of accepted mentions per (article, company): the unit relevance is
computed on.

| Field | Type | Notes |
|---|---|---|
| `article_id` | int, FK Article | composite PK with `company_ticker` |
| `company_ticker` | str, FK Company | cascade delete |
| `mention_count` | int ≥ 1 | accepted mentions only |
| `title_hit` | bool | FR-8 component |
| `lede_hit` | bool | FR-8 component |
| `relevance` | int 0–100 | `round(100·(0.5·title_hit + 0.25·lede_hit + 0.25·min(1, n/4)))` |

**Invariants:** row exists iff the article has ≥ 1 accepted mention for the
company; components stored so `relevance` is auditable (evals recompute from
components and from mentions; both must match). Written once at ingest.

### 2.9 Delivery (append-only)

One composed message on one channel.

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `channel` | enum `console \| file \| email \| webhook` | |
| `kind` | enum `digest \| alert` | |
| `created_at` | datetime | injected `now` |
| `status` | enum `composed \| sent \| failed` | see lifecycle below |
| `subject` | str | e.g. `TickerPress digest — 2026-03-02` / `Alert: TSLA — <headline>` |
| `body_text` | str | full rendered Markdown (audit copy; FileNotifier writes the same bytes) |
| `error` | str \| None | set iff status = failed |

### 2.10 DeliveryItem (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `delivery_id` | int, FK Delivery | |
| `channel` | enum | denormalized from Delivery (enables the partial unique index) |
| `company_ticker` | str | no FK cascade — ledger history survives company deletion |
| `story_id` | int, FK Story | |
| `article_id` | int, FK Article | the representative cited in the message |
| `relevance` | int 0–100 | story-level relevance at compose time |
| `counted` | bool | 0 at compose; set 1 in the same transaction that marks the Delivery `sent` |

**Invariants (the exactly-once contract, SCOPE FR-11):**

- Partial unique index `(channel, company_ticker, story_id) WHERE counted=1`
  — a double-delivery is a constraint violation.
- Lifecycle: insert Delivery(`composed`) + items(`counted=0`) in one
  transaction → `Notifier.deliver` → on success, one transaction sets
  status=`sent` and all items `counted=1`; on failure, status=`failed`
  (items stay uncounted ⇒ stories re-eligible).
- The undelivered query considers only `counted=1` items.
- Rows are never updated after the terminal transaction, never deleted.

## 3. Artifact & interchange schemas

### 3.1 Digest body (rendered from `data/digest_template.md`)

Fixed Markdown; factual fields only; footer is part of the template
(SCOPE D14). Item ordering per FR-9.

```markdown
# TickerPress digest — 2026-03-02

## AAPL — Apple Inc.
- [Apple beats March-quarter estimates on services strength](https://wireone.example.com/apple-q2)
  — Wire One, 2026-03-01 21:30 UTC, relevance 94, matched: Apple, Apple Inc., AAPL — +3 other outlets
- [Apple opens flagship store in Mumbai](https://techledger.example.com/apple-mumbai)
  — Tech Ledger, 2026-03-01 09:10 UTC, relevance 45, matched: Apple

## TSLA — Tesla, Inc.
- [Tesla recalls 12,000 vehicles over seatbelt fault](https://bizdaily.example.com/tesla-recall)
  — Biz Daily, 2026-03-02 06:05 UTC, relevance 75, matched: Tesla — +1 other outlet

---
Informational only — links to third-party news coverage. Not investment advice.
```

Alert bodies use the same item line for a single story, subject
`Alert: <TICKER> — <representative title>`, same footer.

### 3.2 Webhook payload (live `WebhookNotifier`)

```json
{"text": "<body_text>",
 "items": [{"ticker": "AAPL", "story_id": 41, "url": "https://…", "title": "…",
            "relevance": 93, "published_at": "2026-03-01T21:30:00Z"}]}
```

Slack-incoming-webhook-compatible: Slack renders `text`; richer consumers
read `items`.

### 3.3 Outbox files (`FileNotifier`)

`<outbox_dir>/<YYYYMMDDTHHMMSSZ>-<kind>-<delivery_id>.md`, content =
`body_text` exactly (byte-identical to the DB audit copy; asserted in
tests). Timestamp is the injected `now` — deterministic under `FixedClock`.

## 4. Storage mapping (SQLite)

Default `~/.tickerpress/tickerpress.db`; tests use `:memory:` or tmp path.
WAL mode, foreign keys ON, all writes transactional. Ingest wraps each
article's insert + story assignment + mentions + appearances in one
transaction; readers never observe a half-scored article.

```sql
CREATE TABLE companies (
  ticker              TEXT PRIMARY KEY CHECK (ticker GLOB '[A-Z]*'),
  name                TEXT NOT NULL,
  mode                TEXT NOT NULL DEFAULT 'digest'
                        CHECK (mode IN ('digest','alert','both','mute')),
  min_relevance       INTEGER NOT NULL DEFAULT 20 CHECK (min_relevance BETWEEN 0 AND 100),
  alert_min_relevance INTEGER NOT NULL DEFAULT 60 CHECK (alert_min_relevance BETWEEN 0 AND 100),
  context_terms       TEXT NOT NULL DEFAULT '[]',   -- JSON list
  anti_terms          TEXT NOT NULL DEFAULT '[]',   -- JSON list
  created_at          TEXT NOT NULL
);

CREATE TABLE aliases (
  id             INTEGER PRIMARY KEY,
  company_ticker TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  text           TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN
                   ('legal_name','short_name','ticker_symbol','cashtag','nickname')),
  strength       TEXT NOT NULL CHECK (strength IN ('strong','weak')),
  prior          REAL NOT NULL DEFAULT 0.0 CHECK (prior BETWEEN 0.0 AND 0.3),
  generated      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  UNIQUE (company_ticker, text COLLATE NOCASE)
);

CREATE TABLE feeds (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL UNIQUE,
  url            TEXT NOT NULL UNIQUE,
  enabled        INTEGER NOT NULL DEFAULT 1,
  etag           TEXT,
  last_modified  TEXT,
  last_polled_at TEXT,
  last_status    TEXT CHECK (last_status IN ('ok','not_modified','error')),
  created_at     TEXT NOT NULL
);

CREATE TABLE ingest_runs (
  id                INTEGER PRIMARY KEY,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  status            TEXT NOT NULL CHECK (status IN ('succeeded','partial','failed')),
  feed_results      TEXT NOT NULL DEFAULT '[]',    -- JSON
  articles_new      INTEGER NOT NULL DEFAULT 0,
  candidates_total  INTEGER NOT NULL DEFAULT 0,
  mentions_accepted INTEGER NOT NULL DEFAULT 0,
  stories_new       INTEGER NOT NULL DEFAULT 0,
  alerts_sent       INTEGER NOT NULL DEFAULT 0,
  engine_version    TEXT NOT NULL
);

CREATE TABLE stories (
  id                        INTEGER PRIMARY KEY,
  created_at                TEXT NOT NULL,
  first_published_at        TEXT NOT NULL,
  representative_article_id INTEGER NOT NULL      -- FK enforced app-side to break
                                                  -- the story↔article insert cycle
);

CREATE TABLE articles (
  id               INTEGER PRIMARY KEY,
  feed_id          INTEGER NOT NULL REFERENCES feeds(id),
  item_guid        TEXT NOT NULL,
  url              TEXT NOT NULL,
  canonical_url    TEXT NOT NULL,
  title            TEXT NOT NULL,
  summary          TEXT NOT NULL DEFAULT '',
  content          TEXT,
  published_at     TEXT NOT NULL,
  published_source TEXT NOT NULL CHECK (published_source IN ('feed','fallback')),
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  content_sha256   TEXT NOT NULL,
  token_count      INTEGER NOT NULL,
  story_id         INTEGER NOT NULL REFERENCES stories(id),
  dedup_similarity REAL CHECK (dedup_similarity BETWEEN 0.0 AND 1.0),
  UNIQUE (feed_id, item_guid)
);
CREATE INDEX idx_articles_story     ON articles(story_id);
CREATE INDEX idx_articles_published ON articles(published_at);   -- dedup window scans
CREATE INDEX idx_articles_canonical ON articles(canonical_url);  -- dedup fast path

CREATE TABLE mentions (
  id             INTEGER PRIMARY KEY,
  article_id     INTEGER NOT NULL REFERENCES articles(id),
  company_ticker TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  alias_id       INTEGER REFERENCES aliases(id) ON DELETE SET NULL,
  field          TEXT NOT NULL CHECK (field IN ('title','summary','content')),
  char_start     INTEGER NOT NULL CHECK (char_start >= 0),
  char_end       INTEGER NOT NULL CHECK (char_end > char_start),
  surface        TEXT NOT NULL,
  matched_via    TEXT NOT NULL CHECK (matched_via IN
                   ('alias','cashtag','exchange_qualified')),
  strength       TEXT NOT NULL CHECK (strength IN ('strong','weak')),
  features       TEXT NOT NULL DEFAULT '{}',       -- JSON feature vector
  score          REAL NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
  threshold      REAL NOT NULL,
  accepted       INTEGER NOT NULL,
  engine_version TEXT NOT NULL,
  CHECK (accepted = (score >= threshold))
);
CREATE INDEX idx_mentions_article ON mentions(article_id, company_ticker);
CREATE INDEX idx_mentions_company ON mentions(company_ticker, accepted);

CREATE TABLE appearances (
  article_id     INTEGER NOT NULL REFERENCES articles(id),
  company_ticker TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  mention_count  INTEGER NOT NULL CHECK (mention_count >= 1),
  title_hit      INTEGER NOT NULL,
  lede_hit       INTEGER NOT NULL,
  relevance      INTEGER NOT NULL CHECK (relevance BETWEEN 0 AND 100),
  PRIMARY KEY (article_id, company_ticker)
);
CREATE INDEX idx_appearances_company ON appearances(company_ticker, relevance);

CREATE TABLE deliveries (
  id         INTEGER PRIMARY KEY,
  channel    TEXT NOT NULL CHECK (channel IN ('console','file','email','webhook')),
  kind       TEXT NOT NULL CHECK (kind IN ('digest','alert')),
  created_at TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('composed','sent','failed')),
  subject    TEXT NOT NULL,
  body_text  TEXT NOT NULL,
  error      TEXT,
  CHECK ((status = 'failed') = (error IS NOT NULL))
);

CREATE TABLE delivery_items (
  id             INTEGER PRIMARY KEY,
  delivery_id    INTEGER NOT NULL REFERENCES deliveries(id),
  channel        TEXT NOT NULL,
  company_ticker TEXT NOT NULL,                    -- no FK: ledger outlives companies
  story_id       INTEGER NOT NULL REFERENCES stories(id),
  article_id     INTEGER NOT NULL REFERENCES articles(id),
  relevance      INTEGER NOT NULL,
  counted        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_ledger_exactly_once
  ON delivery_items(channel, company_ticker, story_id) WHERE counted = 1;
```

Story-level relevance is a query, not a table:

```sql
SELECT a.story_id, ap.company_ticker, MAX(ap.relevance) AS story_relevance
FROM appearances ap JOIN articles a ON a.id = ap.article_id
GROUP BY a.story_id, ap.company_ticker;
```

The undelivered-selection query for a channel joins that view against
`delivery_items … WHERE counted = 1` with `NOT EXISTS`.

## 5. Example records

**Company**

```json
{"ticker":"AAPL","name":"Apple Inc.","mode":"both","min_relevance":20,
 "alert_min_relevance":60,
 "context_terms":["iphone","ipad","cupertino","app store","tim cook"],
 "anti_terms":["orchard","fruit","cider","harvest","pie"],
 "created_at":"2026-02-20T08:00:00Z"}
```

**Aliases** (generated for AAPL)

```json
[{"id":1,"company_ticker":"AAPL","text":"AAPL","kind":"ticker_symbol","strength":"strong","prior":0.0,"generated":true},
 {"id":2,"company_ticker":"AAPL","text":"$AAPL","kind":"cashtag","strength":"strong","prior":0.0,"generated":true},
 {"id":3,"company_ticker":"AAPL","text":"Apple Inc.","kind":"legal_name","strength":"strong","prior":0.0,"generated":true},
 {"id":4,"company_ticker":"AAPL","text":"Apple","kind":"short_name","strength":"weak","prior":0.25,"generated":true}]
```

**Feed**

```json
{"id":3,"name":"Wire One","url":"file:///…/evals/fixtures/feeds/wire_one.xml",
 "enabled":true,"etag":null,"last_modified":null,
 "last_polled_at":"2026-03-02T13:00:00Z","last_status":"ok",
 "created_at":"2026-02-20T08:01:00Z"}
```

**Article**

```json
{"id":117,"feed_id":3,"item_guid":"wireone-2026-4471",
 "url":"https://wireone.example.com/apple-q2?utm_source=rss&utm_medium=feed",
 "canonical_url":"https://wireone.example.com/apple-q2",
 "title":"Apple beats March-quarter estimates on services strength",
 "summary":"Apple Inc. (NASDAQ: AAPL) reported quarterly revenue of $96.4 billion, ahead of analyst estimates, as services growth offset softer iPhone sales. Shares rose 3% in extended trading.",
 "content":null,"published_at":"2026-03-01T21:30:00Z","published_source":"feed",
 "first_seen_at":"2026-03-02T13:00:00Z","last_seen_at":"2026-03-02T13:00:00Z",
 "content_sha256":"4e8a1c…","token_count":41,"story_id":41,
 "dedup_similarity":null}
```

**Story**

```json
{"id":41,"created_at":"2026-03-02T13:00:00Z",
 "first_published_at":"2026-03-01T21:30:00Z","representative_article_id":117}
```

**Mentions** (one accepted strong, one accepted weak with evidence, one
rejected trap from a different article; article 117's third accepted
mention — the exchange-qualified `AAPL` in the summary, `matched_via:
"exchange_qualified"` — is omitted for brevity but accounts for
`mention_count: 3` below)

```json
[{"id":901,"article_id":117,"company_ticker":"AAPL","alias_id":3,"field":"summary",
  "char_start":0,"char_end":10,"surface":"Apple Inc.","matched_via":"alias",
  "strength":"strong","features":{},"score":1.0,"threshold":0.35,"accepted":true,
  "engine_version":"0.1.0"},
 {"id":902,"article_id":117,"company_ticker":"AAPL","alias_id":4,"field":"title",
  "char_start":0,"char_end":5,"surface":"Apple","matched_via":"alias",
  "strength":"weak",
  "features":{"prior":0.25,"coref_strong":1,"case_signal":0,"window_cues":2,
              "window_antis":0,"doc_cues":3,"doc_antis":0,"ctx_terms":1,
              "anti_terms":0,"hyphen_compound":0,"allcaps_run":0},
  "score":1.0,"threshold":0.35,"accepted":true,"engine_version":"0.1.0"},
 {"id":955,"article_id":131,"company_ticker":"AAPL","alias_id":4,"field":"summary",
  "char_start":52,"char_end":57,"surface":"Apple","matched_via":"alias",
  "strength":"weak",
  "features":{"prior":0.25,"coref_strong":0,"case_signal":1,"window_cues":0,
              "window_antis":2,"doc_cues":0,"doc_antis":2,"ctx_terms":0,
              "anti_terms":2,"hyphen_compound":0,"allcaps_run":0},
  "score":0.0,"threshold":0.35,"accepted":false,"engine_version":"0.1.0"}]
```

**Appearance**

```json
{"article_id":117,"company_ticker":"AAPL","mention_count":3,
 "title_hit":true,"lede_hit":true,"relevance":94}
```

(`0.5 + 0.25 + 0.25·min(1, 3/4) = 0.9375 → 94`.)

**Delivery + DeliveryItem**

```json
{"id":12,"channel":"file","kind":"digest","created_at":"2026-03-02T13:05:00Z",
 "status":"sent","subject":"TickerPress digest — 2026-03-02",
 "body_text":"# TickerPress digest — 2026-03-02\n\n## AAPL — Apple Inc.\n- [Apple beats March-quarter estimates…](https://wireone.example.com/apple-q2) — Wire One, 2026-03-01 21:30 UTC, relevance 94, matched: Apple, Apple Inc., AAPL — +3 other outlets\n…\n---\nInformational only — links to third-party news coverage. Not investment advice.\n",
 "error":null}
```

```json
{"id":57,"delivery_id":12,"channel":"file","company_ticker":"AAPL",
 "story_id":41,"article_id":117,"relevance":94,"counted":true}
```

**IngestRun**

```json
{"id":9,"started_at":"2026-03-02T13:00:00Z","finished_at":"2026-03-02T13:00:04Z",
 "status":"succeeded",
 "feed_results":[{"feed_id":3,"status":"ok","items_seen":25,"items_new":18,"skipped":0,"error":null},
                 {"feed_id":4,"status":"ok","items_seen":20,"items_new":11,"skipped":1,"error":null}],
 "articles_new":29,"candidates_total":63,"mentions_accepted":38,
 "stories_new":19,"alerts_sent":1,"engine_version":"0.1.0"}
```
