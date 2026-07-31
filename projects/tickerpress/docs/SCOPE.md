# TickerPress — Scope

## One-liner

A single-user news watcher that ingests finance/news feeds, detects genuine
appearances of the companies on your watchlist (robust to "Apple the company
vs. apple the fruit"), collapses syndicated copies of the same story into one,
scores how central the company is to each story, and delivers ranked digests
and alerts with links — each story reaching you exactly once.

## Problem statement

The owner wants to know when companies they care about show up in the news,
without reading feeds all day. The naive version of this tool — grep the feed
for "Apple" — is worse than useless: it pages you for apple-pie recipes,
Amazon-rainforest reports, and travel-visa policy changes; it delivers the
same wire story five times because five outlets republished it; and it can't
tell a story *about* Tesla from a market roundup that mentions Tesla in a
list of twelve tickers. Commercial news-analytics products (RavenPack,
Thomson Reuters News Analytics) exist precisely because these three problems
are hard; the personal-scale version must solve the same three problems at
small scale, deterministically, and explainably.

The hard parts are:

- **A. Company-mention detection with disambiguation.** Alias and ticker
  matching that accepts "Meta fined by EU regulator" and rejects
  "meta-analysis of statin trials"; accepts "(NASDAQ: AAPL)" always and
  "Apple growers brace for frost" never. This is entity linking against a
  fixed alias table: surface matching plus a deterministic
  context-compatibility score. Miss real mentions and the product is blind;
  hallucinate mentions and it gets muted within a week. A secondary facet of
  the same hard part is **relevance**: ranking a headline appearance above a
  passing list mention so digests lead with what matters.
- **B. Syndication dedup + exactly-once delivery.** The same wire story
  appears in multiple feeds with edited headlines, added outlet boilerplate,
  dropped paragraphs, and tracking-parameter-laden URLs. The engine must
  cluster copies into one story (without merging two *different* Apple
  stories from the same day) and guarantee that a given (company, story)
  reaches a delivery channel exactly once, across digests, alerts, and
  re-runs.

Both get first-class eval gates (see EVALS.md).

## Target user

The owner: one person tracking a personal watchlist (order of 5–50
companies) across a handful of feeds, driving the tool from a terminal and
reading digests in a terminal, file, email inbox, or chat webhook. Single
local profile; no accounts, no multi-tenancy. **Finance safeguard (per
CONVENTIONS.md):** the product delivers links and factual metadata only — it
computes no sentiment, no price targets, no buy/sell language anywhere; the
digest/alert templates are fixed and contain no generated commentary, and
every rendered digest carries an "informational only — not investment
advice" footer as template text (decision D14). This is implemented
behavior, not a disclaimer: there is no code path that emits an opinion.

## User stories & acceptance criteria

**US-1 — Build a watchlist in one minute.** As the user, I add a company by
ticker and name; sensible aliases (ticker, cashtag, legal-suffix-stripped
short name) are generated automatically, and I can add or remove aliases and
tune per-company terms later.
*Accept:* `company add TSLA --name "Tesla, Inc."` creates the company plus
generated aliases `TSLA` (ticker), `$TSLA` (cashtag), `Tesla, Inc.`
(legal_name, strong), `Tesla` (short_name, weak); duplicate surface forms per
company are rejected; alias edits take effect on the next ingest; `company
show` lists every alias with kind, strength, and prior.

**US-2 — Ingest feeds into a permanent archive.** As the user, I register
feeds and run `ingest`; new items are parsed, normalized, and archived;
re-running is idempotent and one broken feed never aborts the run.
*Accept:* RSS 2.0 and Atom fixture feeds both parse; re-ingesting identical
feed bytes creates zero new articles (identity = feed + guid); a feed
returning malformed XML records an error in the IngestRun while the other
feeds complete; every archived article retains url, canonical_url, title,
normalized text, published_at, and content hash.

**US-3 — Only real mentions.** As the user, when I watch Apple, Amazon,
Visa, Ford, Shell, Meta, Target, Oracle, and Caterpillar, articles about
fruit, rainforests, travel documents, Harrison Ford, seashells,
meta-analyses, target audiences, Greek oracles, and insects do not appear in
my results.
*Accept:* mention F1 and the three ambiguous-subset F1 gates (M1, M1_amb,
M1_amb_base, M1_amb_abl) in EVALS.md pass; every accepted **and** rejected
candidate stores its full feature breakdown and score; `explain <article>`
shows exactly why each candidate was accepted or rejected, with no hidden
state.

**US-4 — One story, once.** As the user, when five outlets run the same wire
story about Tesla, I see it once, with a note that it appeared in five
outlets — but two *different* Tesla stories from the same day stay separate.
*Accept:* dedup pairwise precision/recall meet the M2 gates; zero
must-not-merge trap pairs merge (M2_trap = 0) and the near-threshold subset
still binds (M2_near); the story representative is the earliest-published
copy; the digest line for a multi-copy story cites one link and the copy
count.

**US-5 — A ranked digest.** As the user, I run `digest run` (or cron does)
and get one message grouped by company, most relevant story first, each item
carrying title, link, outlet, published time, and relevance — and companies
with nothing new are simply absent.
*Accept:* relevance ordering accuracy meets the M3 gate and the M3_cov
coverage gate; digest items are ordered (relevance desc, published_at desc,
story id asc) within a company and companies by ticker asc; an empty digest
sends nothing and records nothing; stories below the company's
`min_relevance` are excluded.

**US-6 — Never the same story twice.** As the user, no (company, story) ever
reaches the same channel twice — not across consecutive digests, not when I
re-run ingest, not when a story arrives via alert first.
*Accept:* the exactly-once scenario (M4) scores 1.0: re-ingest + second
digest produces zero deliveries; a story alerted on a channel is excluded
from that channel's next digest for that company; a failed send does not
consume the story (it is re-eligible).

**US-7 — Immediate alerts for the big stuff.** As the user, I can flag a
company `alert` (or `both`) so that a story whose relevance reaches the
alert threshold is delivered during ingest instead of waiting for the
digest.
*Accept:* with `mode=alert, alert_min_relevance=60`, a headline-level story
produces exactly one alert on the configured channel during `ingest`; a
passing mention produces none; alert deliveries appear in the same ledger
as digests.

**US-8 — Live feeds when I turn them on.** As the user, I can point a feed
at a real RSS URL; polling is polite (conditional GET) and nothing about
tests or evals changes.
*Accept:* the live adapter activates only when `TICKERPRESS_ALLOW_NETWORK=1`
and the feed URL scheme is http(s); it sends `If-None-Match`/
`If-Modified-Since` from stored `etag`/`last_modified` and treats 304 as
"no new items"; the offline suite never touches the network (enforced by
eval hermeticity, M5).

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-5/6/8 are
hard part A; FR-7/11 are hard part B.

- **FR-1 Watchlist management.** Companies keyed by ticker (uppercase,
  `^[A-Z]{1,5}(\.[A-Z])?$` — dots for share classes like `BRK.B`), with
  name, delivery `mode` (`digest|alert|both|mute`), `min_relevance`
  (default 20), `alert_min_relevance` (default 60), and per-company
  `context_terms`/`anti_terms` lists. Aliases carry surface text, `kind`
  (`legal_name|short_name|ticker_symbol|cashtag|nickname`), `strength`
  (`strong|weak`), and `prior` (0–0.3). On `company add`, generated aliases
  are created: the ticker (ticker_symbol), `$TICKER` (cashtag), the given
  name (legal_name), and a short name produced by stripping legal suffixes
  from a committed suffix list derived from the ISO 20275 Entity Legal
  Forms vocabulary (Inc, Corp, Corporation, Co, Ltd, PLC, SA, NV, AG, SE,
  Holdings, Group, …) — suppressible with `--no-auto-alias`. Duplicate
  case-folded surface forms per company are rejected.
- **FR-2 Feed registry & fetching.** Feeds have unique name and url,
  `enabled` flag, and per-feed HTTP state (`etag`, `last_modified`,
  `last_polled_at`, `last_status`). All fetching goes through the
  `FeedSource` adapter; `FetchResult.status ∈ {ok, not_modified, error}`.
  Ingest iterates enabled feeds **in ascending feed id order**, continues
  past per-feed errors, and records per-feed outcomes in the IngestRun.
- **FR-3 Feed parsing & normalization (pure).** `engine/feedparse.py`
  parses raw bytes for an RSS 2.0 and Atom (RFC 4287) subset: item identity
  from `guid`/`atom:id`, else `sha256(link + "\n" + title)` (real feeds
  omit guid); fields `link`, `title`, `description`, `content:encoded` /
  `atom:content`, `pubDate` (RFC 822 dates via
  `email.utils.parsedate_to_datetime`) / `atom:updated`/`published`
  (RFC 3339). Items missing both link and title are skipped with a recorded
  reason. Items are processed **in document order** within a feed.
  Normalization: HTML tag strip (stdlib `html.parser`), entity decode,
  Unicode NFC (UAX #15), curly-quote/dash folding, whitespace collapse;
  deterministic sentence split (`[.!?]` + space + uppercase, with a
  committed abbreviation list); tokenization on Unicode word boundaries
  where `-`, `/`, and `.` (outside a committed abbreviation) are token
  separators, preserving character offsets. So `Meta-analysis` tokenizes as
  `Meta`, `analysis` and `Meta` is matchable. Missing `pubDate` falls back
  to the ingest `now` with `published_source="fallback"`.
- **FR-4 Article archive & identity.** Articles are unique per
  `(feed_id, item_guid)` and append-only: rows are immutable after insert
  except the one-time `story_id` assignment; re-seeing the same
  (feed, guid) updates only `last_seen_at` (first-ingested content wins —
  documented limitation D16). `canonical_url` = lowercase scheme+host,
  default port dropped, fragment dropped, tracking params removed
  (committed list: `utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`,
  `ref`, `cmpid`), remaining query params sorted. `content_sha256` is over
  normalized `title + "\n" + summary + "\n" + content`.
- **FR-5 Candidate mention scanning (hard part A).** The watchlist compiles
  into a deterministic multi-pattern matcher (regex alternation with
  token-boundary anchoring; Aho–Corasick 1975 is the named scale path if
  watchlists grow). Matching rules by alias kind:
  - *legal_name / short_name / nickname:* whole-phrase token match against
    the stored capitalization, or its ALL-CAPS form inside an all-caps run
    (≥3 consecutive uppercase tokens, e.g. shouty headlines); lowercase
    surfaces never match.
  - *ticker_symbol:* exact uppercase token. Length-1 tickers (`F`, `V`)
    **never** match bare — only as cashtag or exchange-qualified. Tickers
    whose lowercase form appears in the committed common-words list
    (`data/common_words.txt`, ~2,000 entries: `all`, `cat`, `meta`, `key`,
    `so`, `on`, `it`, …) are demoted to weak candidates.
  - *cashtag:* `$TICKER` (the StockTwits convention, later adopted by
    Twitter) — always a strong candidate.
  - *exchange-qualified:* the newswire parenthetical patterns
    `(NASDAQ: AAPL)`, `(NYSE:F)`, and Reuters-RIC-style `(AAPL.O)` —
    always strong (committed regex set).
  Every hit yields a candidate with company, alias, field
  (`title|summary|content`), char offsets, and surface text. Candidates are
  emitted in a deterministic order: field (`title`, `summary`, `content`),
  then ascending `char_start`, then ascending `alias_id`.
- **FR-6 Disambiguation scoring (hard part A).** Strong candidates are
  accepted with score 1.0. Each weak candidate scores

  ```
  S = prior
      + 0.50 · coref_strong          # a strong mention of the same company
                                     # accepted anywhere in this article —
                                     # "one sense per discourse"
                                     # (Gale, Church & Yarowsky 1992)
      + 0.10 · case_signal           # capitalized as stored AND not
                                     # sentence-initial AND not in an
                                     # all-caps run
      + 0.10 · min(3, window_cues)   # distinct corporate-cue lemmas within
                                     # ±12 tokens (data/cues_corporate.txt)
      − 0.15 · min(3, window_antis)  # distinct anti-cues within ±12 tokens
                                     #   (data/cues_anti.txt)
      + 0.05 · min(3, doc_cues)      # distinct cues elsewhere in article
      − 0.10 · min(2, doc_antis)     # distinct anti-cues elsewhere
      + 0.15 · min(2, ctx_terms)     # company context_terms in article
                                     #   (e.g. Apple: iPhone, Cupertino)
      − 0.20 · min(2, anti_terms)    # company anti_terms in article
                                     #   (e.g. Apple: orchard, fruit, cider)
      − 0.20 · hyphen_compound       # surface immediately followed by
                                     # "-" + lowercase word ("Meta-analysis")
      − 0.20 · allcaps_run           # word-collision ticker inside an
                                     # all-caps run ("ALL OPTIONS OPEN")
  ```

  clamped to [0, 1]; accept iff `S ≥ θ`. **θ is a single committed engine
  default (0.35). There is no per-alias or per-company threshold override**
  — `Mention.threshold` records the θ in force at scoring time purely as
  provenance, so rows scored under different `engine_version`s remain
  interpretable. `prior` encodes how often the surface means the company in
  news — the "commonness" prior of entity-linking practice (Milne & Witten
  2008).

  Scoring rules that fix every ambiguity an implementer would otherwise
  guess at:

  1. **Fields are sentence containers.** `title` is treated as a single
     sentence; `summary` and `content` are split by FR-3. A token at
     position 0 of any sentence — including the title — is
     *sentence-initial*, so `case_signal = 0` for it.
  2. **Windows never cross fields.** The ±12-token cue/anti window is
     clipped to the mention's own field. `doc_cues`/`doc_antis` count
     distinct lemmas anywhere in `title + summary + content` that are *not*
     already counted in the window (window and doc counts are disjoint by
     position).
  3. **Lexicons are scored independently.** The global cue/anti lexicons and
     the per-company `context_terms`/`anti_terms` are separate features; a
     token present in both contributes to both. Matching for all four is
     case-folded whole-token (multi-word terms match as token sequences).
  4. **`coref_strong` is computed after all strong candidates in the article
     are resolved**, so scoring order within an article never matters.
  5. Every candidate — accepted or rejected — is persisted with its full
     feature vector, score, θ, and engine version.

  Worked examples the fixtures pin down (each is a named case in
  `tests/test_disambig.py`; feature vectors are exact):

  | Case | Features | S | Outcome |
  |---|---|---|---|
  | "Apple opens flagship store in Mumbai" (title mention; summary says Cupertino; no finance cues anywhere) | prior .25, case_signal 0 (title-initial), window_cues 0, doc_cues 0, ctx_terms 1 | .25 + .15 = **.40** | accept (≥ .35) |
  | "Apple growers brace for frost" (title mention; body has *harvest*, *orchard*) | prior .25, case_signal 0, window_antis 2 (*growers*, *frost*), doc_antis 2, anti_terms 2 | .25 − .30 − .20 − .40 = −.65 → **0** | reject |
  | "Meta-analysis finds statin benefit overstated" | prior .25, case_signal 0, hyphen_compound 1, window_antis 1 (*statin*), anti_terms 2 | .25 − .20 − .15 − .40 = −.50 → **0** | reject |
  | "Apple beats March-quarter estimates" (title) with "Apple Inc. (NASDAQ: AAPL)" in the summary | prior .25, coref_strong 1, case_signal 0, window_cues 2, doc_cues 3, ctx_terms 1 | 1.25 → **1.0** (clamped) | accept |

  The Mumbai case accepts *only* because of a per-company context term; it
  is one of the ≤5 fixture positives permitted to depend on that (EVALS §4),
  which is why the ablation gate M1_amb_abl is set below 1.0.
- **FR-7 Syndication dedup (hard part B).** Per article: normalized dedup
  text = case-folded, punctuation-stripped `title + summary + content`;
  shingle set = contiguous 3-token w-shingles; similarity = exact Jaccard
  resemblance `J(a,b) = |S_a ∩ S_b| / |S_a ∪ S_b|` (Broder 1997). At
  ingest, each new article is compared against all articles whose
  `published_at` lies within ±7 days (the dedup window). Fast paths: equal
  `canonical_url` or equal `content_sha256` ⇒ same story, **and these
  lookups are window-limited too** (same ±7-day bound), so a year-old
  identical URL does not resurrect a story. Assignment: join the story of
  the argmax-J article if `J ≥ 0.60`, else open a new story.

  Determinism rules (load-bearing for M5):
  - Candidate articles are compared in ascending article id; ties on J
    resolve to the candidate with the **smallest article id**, and the new
    article joins that candidate's story.
  - When both fast paths fire against different articles, `canonical_url`
    wins over `content_sha256`; within a fast path, smallest article id
    wins. Fast-path joins record `dedup_similarity = 1.0`.
  - Ingest processes feeds by ascending feed id (FR-2) and items in
    document order (FR-3), so article ids — and therefore every tie-break —
    are a pure function of the fixture bytes.

  Stories never merge retroactively (decision D6); representative =
  earliest `published_at`, tie-broken by smallest article id (re-pointed if
  an earlier-published copy arrives later — the only permitted story
  mutation). MinHash/SimHash (Broder et al. 1998; Charikar 2002; Manku,
  Jain & Das Sarma, WWW 2007) are the named scale path; at personal volume
  exact Jaccard is affordable and exactly deterministic.
- **FR-8 Relevance scoring (hard part A, ranking facet).** For article `a`
  and company `c` with accepted mentions `M` (|M| = n ≥ 1):

  ```
  title_hit = 1 if any m ∈ M is in the title else 0
  lede_hit  = 1 if any m ∈ M is in the first sentence of the summary,
              or in the first ceil(0.25 · content_token_count) tokens
              of the content field, else 0
  relevance = round_half_up(100 · (0.50·title_hit + 0.25·lede_hit
                                   + 0.25·min(1, n/4)))
  ```

  `round_half_up(x) = floor(x + 0.5)` — **not** Python's built-in `round`,
  which is banker's rounding and would map 62.5 → 62 and 12.5 → 12. The
  attainable relevance values are therefore exactly
  `{6,13,19,25,31,38,44,50,56,63,69,75,81,88,94,100}`; EVALS §4 relies on
  that set. `content_token_count` is the stored per-article token count
  **of the content field only** (0 when `content` is NULL, in which case only the
  summary-sentence path can set `lede_hit`). `Article.token_count` remains
  the cross-field total and is reporting metadata only.
  Story-level relevance for `c` = max over the story's member articles (a
  syndicated copy that drops the headline must not depress the story). The
  0–100 scale and headline-dominance shape follow the construction of the
  RavenPack relevance score and Thomson Reuters News Analytics relevance
  field; lede weighting follows the inverted-pyramid convention of news
  writing (the lede carries the story). An `appearances` row exists iff
  n ≥ 1.
- **FR-9 Digest composition.** `compose_digest(candidate items, now)` is a
  pure function. Selection: appearances whose company mode ∈
  `{digest, both}`, story relevance ≥ company `min_relevance`, and
  (channel, company, story) not yet delivered on the target channel.
  Ordering: companies by ticker asc; within a company, stories by
  (relevance desc, story `first_published_at` desc, story id asc). Each
  item renders: representative title as a link, outlet (feed name),
  published timestamp, relevance, matched-alias list, and "+N other
  outlets" when the story has N+1 copies. The rendered body is fixed
  Markdown from a committed template — factual fields only — ending with
  the informational-only footer (D14). An empty selection composes
  nothing: no Delivery row, no notifier call.
  **`dry_run` semantics:** a dry run composes the body and returns/prints
  it, and persists **nothing** — no Delivery row, no DeliveryItem rows —
  and calls no notifier. It therefore never consumes a story and never
  appears in the ledger or audit history.
- **FR-10 Alerts.** During ingest, after scoring, every (company, story)
  where mode ∈ `{alert, both}` and story relevance ≥ `alert_min_relevance`
  and the pair is undelivered on the alert channel composes a single-story
  alert message (same template family) and delivers it immediately. The
  alert channel is `ingest --alert-channel console|file|email|webhook`
  (default `console`; the API takes `alert_channel` on `/ingest/runs`). Alerts
  and digests share one ledger: a story alerted on a channel never appears
  in that channel's digest for that company; different channels each
  receive the story once.
- **FR-11 Exactly-once delivery ledger (hard part B).** Deliveries are
  append-only. A Delivery (channel, kind, status ∈
  `composed|sent|failed`, body) owns DeliveryItems (channel denormalized,
  company, story, cited article, relevance, `counted` flag). `counted` is
  set to 1 in the same transaction that marks the Delivery `sent`; a
  partial unique index on `(channel, company_ticker, story_id) WHERE
  counted = 1` makes double-delivery a constraint violation, not a code
  -review hope. The undelivered query filters on counted items only, so a
  `failed` send releases its stories for retry. Compose→persist→send→mark
  is the required order; a crash between persist and send leaves status
  `composed` (uncounted, hence re-eligible) with the body retained for
  audit.
- **FR-12 Notifier adapters.** `Notifier` protocol with
  `deliver(message) -> None` (raises on failure). Offline: `ConsoleNotifier`
  (stdout), `FileNotifier` (writes `outbox/<utc-ts>-<kind>-<id>.md`,
  deterministic name). Live: `EmailNotifier` (stdlib `smtplib`, STARTTLS;
  env `TICKERPRESS_SMTP_HOST/PORT/USERNAME/PASSWORD`,
  `TICKERPRESS_EMAIL_FROM/TO`), `WebhookNotifier` (HTTP POST of
  `{"text": body, "items": [...]}` — Slack-incoming-webhook-compatible
  shape; env `TICKERPRESS_WEBHOOK_URL`). Live notifiers activate only when
  their env vars are set; tests and evals use offline notifiers
  exclusively.
- **FR-13 Explainability.** `explain <article-id> [--company T]` (CLI and
  API) returns, per candidate: surface, alias, kind, field, offsets, every
  feature value from FR-6, score, threshold, accepted; plus the article's
  story membership (the J value against the story it joined, or "new
  story") and the relevance component breakdown. Everything shown is read
  from persisted rows — no recomputation, no hidden state.
- **FR-14 API.** FastAPI app per the sketch below; thin — validation,
  service calls, serialization only.
- **FR-15 CLI.** Typer app per the sketch below; same service layer as the
  API; `--now ISO` accepted wherever time matters (defaults to
  `SystemClock`); `--json` on list/show commands.
- **FR-16 Determinism & hermeticity.** Time is always an input (Clock
  port; engine functions take `now`); no randomness anywhere in the
  pipeline; given the same DB state, fixture bytes, watchlist, and `now`,
  ingest and digest produce byte-identical mention rows, story
  assignments, and digest bodies — **including across processes with
  different `PYTHONHASHSEED` values** (no iteration over unordered sets or
  dicts may reach an output; every such iteration is sorted by an explicit
  key). Tests and evals run entirely on offline adapters — no network, no
  wall clock.

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision). The API is
   shaped so a later reader UI is a pure client.
2. **No full-article scraping.** The engine sees feed-provided text only
   (title + description + `content:encoded`). Fetching article pages and
   extracting body text (Readability/trafilatura-style) is deferred: it is
   brittle, ToS-fraught, and unnecessary for detection on finance feeds,
   which carry meaningful summaries.
3. **No sentiment, no price data, no trading signals.** Deliberate product
   boundary and the finance safeguard (D14). TickerPress says *that* and
   *where* a company appeared — never what to do about it.
4. **No ML/embeddings/LLMs.** Disambiguation is a deterministic,
   inspectable evidence score over committed lexicons (D2). An
   entity-linking model would be strictly harder to eval hermetically and
   impossible to explain to its single user.
5. **No entity discovery.** Only watchlist companies are detected; "trending
   companies you don't follow" is out.
6. **English-only lexicons.** Cue/anti-cue lists are English; non-English
   feeds will under-match (documented assumption D15).
7. **No built-in scheduler/daemon.** `tickerpress ingest && tickerpress
   digest run` is designed to be cron-driven; the app never sleeps or polls
   on its own.
8. **No retroactive re-scan or story merging.** Alias edits apply to future
   ingests; stories never merge after creation (D6). A `rescan` command is
   the first post-MVP candidate.
9. **No push-notification service, no SMS.** Live channels are email and
   webhook only.
10. **No per-alias or per-company scoring thresholds.** θ is one committed
    engine constant (FR-6). Per-surface tuning is expressible today through
    `prior`, `context_terms`, and `anti_terms`; a second tuning axis would
    add storage, CLI, API, and eval surface for a knob a single user does
    not need.
11. **Deferred live adapters:** live RSS polling (FR-2/US-8) ships but is
    env-gated and untested against real networks in CI; email/webhook
    notifiers likewise. Everything gated runs identically through offline
    twins.

## Architecture

```
projects/tickerpress/
  src/tickerpress/
    engine/
      feedparse.py   # FR-3: RSS/Atom subset parser, bytes → FeedItem (pure)
      normalize.py   # FR-3: HTML strip, NFC, sentence split, tokenize w/ offsets
      watchlist.py   # FR-1/5: alias validation, suffix stripping, matcher compilation
      detect.py      # FR-5: candidate scanning (token-boundary, case rules, patterns)
      disambig.py    # FR-6: feature extraction + scoring + accept/reject
      dedup.py       # FR-7: shingles, Jaccard, story assignment decision
      relevance.py   # FR-8: relevance components + score; story rollup
      digest.py      # FR-9/10: pure digest/alert selection, ordering, Markdown render
      pipeline.py    # ingest orchestration as pure functions over fetched items + now
    adapters/
      clock.py           # Clock protocol; SystemClock, FixedClock
      feeds.py           # FeedSource protocol + FetchResult/FeedItem types
      feeds_fixture.py   #   offline: FixtureFeedSource (file:// paths → bytes)
      feeds_rss.py       #   live: LiveRssFeedSource (httpx, conditional GET)
      notify.py          # Notifier protocol + ComposedMessage type
      notify_console.py  #   offline: ConsoleNotifier
      notify_file.py     #   offline: FileNotifier (outbox dir)
      notify_email.py    #   live: EmailNotifier (smtplib)
      notify_webhook.py  #   live: WebhookNotifier (httpx POST)
    store/           # Repository protocol; SQLiteRepository (stdlib sqlite3).
                     #   InMemoryRepository = SQLiteRepository(":memory:") — a
                     #   factory, not a second backend, so constraint semantics
                     #   (incl. the partial unique index) are identical everywhere.
    resources.py     # loads data/ lexicons via importlib.resources into a frozen
                     #   Lexicons dataclass; the ONLY module that reads data/
    services.py      # composition layer: builds Lexicons + adapters + repository,
                     #   passes them into engine functions; used by both api/ and cli/
    api/             # FastAPI app
    cli/             # Typer app
  data/              # committed lexicons: cues_corporate.txt, cues_anti.txt,
                     #   common_words.txt, legal_suffixes.txt, abbreviations.txt,
                     #   tracking_params.txt, digest_template.md
  evals/             # fixtures/, metrics.py, run.py, test_gates.py
```

**Engine purity and lexicon loading (CONVENTIONS.md):** no module under
`engine/` opens a file, reads a clock, or touches the network. `resources.py`
loads every `data/*.txt` file once via `importlib.resources` into an immutable
`Lexicons` dataclass (`corporate_cues: frozenset[str]`, `anti_cues:
frozenset[str]`, `common_words: frozenset[str]`, `legal_suffixes: tuple[str,
...]`, `abbreviations: frozenset[str]`, `tracking_params: tuple[str, ...]`,
`digest_template: str`). `services.py` constructs it and passes it as an
explicit argument into `detect`, `disambig`, `normalize`, `watchlist`, and
`digest`. Engine functions therefore remain pure functions of their inputs,
and tests can pass synthetic `Lexicons` without touching the filesystem.

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated) |
|---|---|---|
| `FeedSource.fetch(feed: Feed) -> FetchResult(status, raw_bytes, etag, last_modified)` | `FixtureFeedSource` — resolves `file://` URLs to committed XML bytes; `etag` unused; fully deterministic | `LiveRssFeedSource` — httpx GET with `If-None-Match`/`If-Modified-Since` (RFC 9110 conditional requests), 304 → `not_modified`; activates only when `TICKERPRESS_ALLOW_NETWORK=1` and scheme is http(s); min poll spacing 15 min enforced from `last_polled_at` |
| `Notifier.deliver(message: ComposedMessage) -> None` (raises `DeliveryError` on failure) | `ConsoleNotifier` (stdout), `FileNotifier` (outbox dir, deterministic filenames) | `EmailNotifier` — smtplib + STARTTLS, env-configured; `WebhookNotifier` — POST Slack-compatible JSON, env-configured |
| `Clock.now() -> datetime` | `FixedClock(iso)` in tests/evals | `SystemClock` in real CLI/API use |

Feed *parsing* is shared pure engine code (`feedparse.py`); only the fetch
differs between adapters, so fixture and live paths exercise identical logic.

### API sketch (FastAPI)

```
GET  /health
GET  /companies                       POST /companies
GET  /companies/{ticker}              PATCH /companies/{ticker}   DELETE /companies/{ticker}
POST /companies/{ticker}/aliases      DELETE /companies/{ticker}/aliases/{alias_id}
GET  /feeds                           POST /feeds
PATCH /feeds/{id}                     DELETE /feeds/{id}
POST /ingest/runs                     # {now?, feed_id?, deliver_alerts=true, alert_channel="console"} → run summary
GET  /ingest/runs?limit=              GET /ingest/runs/{id}
GET  /articles?company=&since=&until=&min_relevance=&limit=
GET  /articles/{id}                   GET /articles/{id}/explain?company=
GET  /stories?company=&since=&limit=  GET /stories/{id}            # members + per-company relevance
POST /digests                         # {channel, now?, dry_run=false} → digest | 204 when empty
GET  /digests?channel=&limit=         GET /digests/{id}            # incl. body_text
GET  /deliveries?company=&story_id=&channel=
```

`POST /companies/{ticker}/aliases` body: `{"text": str, "kind":
"legal_name|short_name|ticker_symbol|cashtag|nickname", "strength":
"strong|weak" | null, "prior": float | null}` — null strength/prior take the
kind defaults of DATA_MODEL §2.2. There is no threshold field (FR-6).
`POST /digests` with `dry_run=true` returns `200` with the rendered body and
writes nothing (FR-9); with an empty selection it returns `204` in both
modes.

### CLI sketch (Typer)

```
tickerpress init                                          # create DB schema; validate that every
                                                          #   data/ lexicon loads and print entry
                                                          #   counts + sha256 (lexicons are files,
                                                          #   never DB rows); idempotent
tickerpress company add TICKER --name NAME [--alias TEXT ...] [--mode digest|alert|both|mute]
                       [--min-relevance N] [--alert-min-relevance N] [--no-auto-alias]
tickerpress company list | show TICKER | set TICKER [--mode ...] | remove TICKER
tickerpress alias add TICKER TEXT [--kind ...] [--strength strong|weak] [--prior P]
tickerpress alias rm TICKER ALIAS_ID
tickerpress term add TICKER --context WORD | --anti WORD          # per-company lexicon terms
tickerpress feed add URL --name NAME | feed list | feed rm ID | feed enable|disable ID
tickerpress ingest [--feed NAME] [--now ISO] [--no-alerts] [--alert-channel console|file|email|webhook]
tickerpress articles list [--company T] [--since ISO] [--min-relevance N] [--limit N]
tickerpress articles show ID
tickerpress stories list [--company T] [--since ISO]      | stories show ID
tickerpress explain ARTICLE_ID [--company T]
tickerpress digest run [--channel console|file|email|webhook] [--now ISO] [--dry-run]
tickerpress digest list | show ID
```

## Key design decisions & assumptions

1. **This is entity linking against a closed alias table, solved with
   deterministic evidence scoring, not ML.** The framing is standard NED:
   a surface-form (alias) table, a commonness prior per surface
   (Milne & Witten, CIKM 2008 "Learning to link with Wikipedia"), and
   context-compatibility evidence — here a hand-auditable linear score over
   committed lexicons rather than learned features. Rationale: hermetic
   evals (CONVENTIONS.md), explainability to a single user who must trust
   the tool, and tunability (the user can add an anti-term the moment a
   false positive appears). Yarowsky-style decision-list WSD (Yarowsky
   1995) is the closest classical relative of the feature design.
2. **Alias strength classes carry the precision burden.** Strong surfaces
   (legal names, non-word tickers, cashtags, exchange parens) are accepted
   outright — these conventions exist in newswire style precisely to be
   unambiguous (AP-style `(NASDAQ: AAPL)`, Reuters RIC `(AAPL.O)`,
   StockTwits `$AAPL`). Weak surfaces (short names, word-collision
   tickers) must earn acceptance through context. The common-words
   demotion list is data, so `META` and `ALL` are weak while `AAPL` and
   `MSFT` are strong without code changes.
3. **"One sense per discourse" is the highest-weight feature.** Gale,
   Church & Yarowsky (1992) observed that a surface form keeps one sense
   within a document with very high probability; for us, an article that
   says "Apple Inc." once licenses every bare "Apple" in it (+0.50, an
   immediate accept at any prior). This single feature converts most
   hard cases into easy ones and is why the feature exists before any cue
   counting.
4. **Score weights and θ are committed engine data; the global lexicons are
   frozen before fixtures are authored.** The weights in FR-6 are initial
   values and EVALS.md gates the *outcome*, not the constants — but that
   only works if the lexicons cannot be turned into a memorization table for
   the corpus. Hence D19: `data/cues_corporate.txt` and `data/cues_anti.txt`
   are authored first from general finance/news vocabulary, their sha256
   hashes are committed to `evals/fixtures/lexicon_manifest.json`, and two
   gates (M6_lex specificity, M1_amb_abl ablation) keep them general.
   Weight/θ tuning against fixture *outcomes* remains allowed; growing the
   lexicons to fit individual fixture articles does not.
5. **Dedup = w-shingling + exact Jaccard resemblance (Broder 1997), sized
   honestly.** MinHash/SimHash exist to approximate Jaccard at web scale
   (Manku, Jain & Das Sarma, WWW 2007, ran SimHash over 8B pages); a
   personal tool ingesting a few hundred items a week inside a ±7-day
   window can afford the exact computation and gains bit-for-bit
   determinism. 3-token shingles + τ = 0.60 tolerate headline rewrites,
   outlet boilerplate, and dropped paragraphs while keeping distinct
   same-topic stories apart; both τ and window are committed defaults.
6. **No retroactive story merges.** Incremental argmax assignment can, in
   principle, let a bridging article arrive that would have joined two
   existing stories. Merging then would rewrite history that the delivery
   ledger already references — an exactly-once violation waiting to
   happen. We accept occasional split stories (the article joins the
   argmax side only) as the safer failure mode; the eval trap set checks
   over-merging, the more damaging direction.
7. **Relevance mimics the shape of commercial news-analytics relevance.**
   RavenPack publishes a 0–100 relevance where ~100 means the entity is in
   the headline/main text and low values mean passing mentions; TRNA's
   relevance field is the same idea on 0–1. Our formula (FR-8) uses
   headline placement > lede placement > mention count, with the lede rule
   grounded in inverted-pyramid news structure. Story relevance is the max
   over copies so syndication never dilutes ranking.
8. **Exactly-once is a database constraint, not a promise.** The partial
   unique index over counted delivery items (FR-11) is the
   idempotency-key pattern: the ledger key is (channel, company, story).
   Failure semantics are chosen for a personal tool: a failed send
   releases stories (better twice than never — but only after an explicit
   retry), while the sent-marking transaction makes silent duplication
   impossible. Because `InMemoryRepository` is `SQLiteRepository(":memory:")`,
   the constraint is present in every test configuration.
9. **Feed text is the corpus; RSS/Atom identity rules follow the specs.**
   Item identity prefers `guid` (RSS 2.0 spec) / `atom:id` (RFC 4287),
   falling back to a link+title hash because real-world feeds omit guids.
   URL canonicalization strips the Urchin Tracking Module parameter family
   (`utm_*`, Google Analytics lineage) and social click ids — these vary
   per syndication channel and would otherwise defeat both identity and
   the dedup fast path.
10. **Polite polling per HTTP conditional-request semantics.** The live
    feed adapter stores `ETag`/`Last-Modified` per feed and sends
    `If-None-Match`/`If-Modified-Since` (RFC 9110), treating 304 as
    "nothing new" without parsing; minimum poll spacing 15 minutes. This
    is long-standing RSS-community etiquette and keeps the tool a good
    citizen of small blogs' bandwidth.
11. **Mentions (including rejections) are persisted, not just accepted
    results.** Explainability (FR-13) and eval debugging both need the
    losing candidates and their features. Storage cost at personal scale
    is trivial; the rows carry `engine_version` so a future scoring change
    is distinguishable from data corruption.
12. **Time is an input everywhere** (CONVENTIONS.md): the Clock port
    supplies `now` to services; `published_at` fallback, dedup windows,
    digest selection, and outbox filenames all derive from injected time.
    Evals run under `FixedClock`.
13. **SQLite archive, append-only bias.** Articles, mentions, ingest runs,
    deliveries are append-only (articles allow the one-time story
    assignment; stories allow representative re-pointing). The archive is
    the product's memory — "send me a link" implies the link must survive
    feed rot, so title/summary/canonical URL are archived even though full
    text is not fetched.
14. **Finance safeguard as implemented behavior.** Fixed templates with
    factual fields only; no sentiment or advice code path exists; the
    informational-only footer is part of the committed template
    (`data/digest_template.md`), and template rendering is gated by eval
    M4/M5 (byte-identical bodies plus a committed golden hash), so the
    footer cannot silently disappear.
15. **Assumption: English feeds.** All lexicons are English; the sentence
    splitter and case rules assume Latin script. Non-English support is a
    lexicon/data project, not a code rewrite, and is out of scope.
16. **Assumption: first-ingested content wins.** Feeds occasionally
    rewrite items in place (developing stories). We archive the first
    version and only bump `last_seen_at` — accepting slightly stale
    summaries in exchange for article immutability. Revisiting requires a
    revisioning design, deferred.
17. **Assumption: personal scale.** ≤ ~50 companies, ≤ ~20 feeds, ≤ ~500
    items/week. Regex-alternation matching, exact Jaccard in a ±7-day
    window, and unindexed feature JSON are all sized to that; the scale
    paths (Aho–Corasick, MinHash) are named where they'd slot in.
18. **Assumption: implementation lands in ~2,500–3,500 lines** across
    engine/adapters/store/api/cli, within the 2–4k mandate.
    **Scope valves, in order — none of these touch a locked decision**
    (alerts, Atom parsing, and the email/webhook notifiers are locked and
    are *not* valves):
    1. Drop the report-only eval outputs except `naive_deltas` (every gate
       is retained).
    2. Trim the API to the read/ingest/digest core: drop `PATCH`/`DELETE`
       endpoints, `GET /ingest/runs/{id}`, and `GET /digests/{id}`. The CLI
       keeps full coverage, and the locked decisions name no API surface.
    3. `explain` renders one human-readable format (drop `--json` on
       `explain` only).
    4. Shrink the fixture corpus toward its asserted floors (EVALS §4) —
       fewer syndication groups, never fewer trap classes.

    If pressure remains after all four, the correct action is to
    re-negotiate a locked decision with the owner and record it as a new
    numbered decision — never to silently drop alerts, Atom, or a live
    notifier.
19. **Lexicon freeze protocol.** The two global lexicons are product data
    that the eval corpus is scored against, so they are a memorization
    channel if left free. Protocol: (a) author `cues_corporate.txt` and
    `cues_anti.txt` from general vocabulary *before* writing fixture
    articles; (b) commit their sha256 to
    `evals/fixtures/lexicon_manifest.json`; (c) any later edit is a
    reviewed change that must update the manifest and re-pass M6_lex
    (no lexicon entry may occur in exactly one fixture base article) and
    M1_amb_abl (the engine must still clear a floor with all per-company
    terms emptied). This is the scoping-time answer to "the gates could be
    passed by memorizing the corpus".
