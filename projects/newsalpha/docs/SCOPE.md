# NewsAlpha — Scope

## One-liner

A single-user decision-support tool that ingests financial news (equities +
crypto), extracts *typed* events (earnings surprise, M&A, guidance change,
regulatory action, exchange listing/delisting, protocol hack, partnership),
links them to the right assets with the right roles, scores each linked event
as a signal (direction, magnitude, confidence, horizon), renders a
plain-language brief ("what happened, why it matters, what to watch") — and
proves its own signal quality with a leak-free event-study backtest.

## Problem statement

The owner reads financial news and wants help turning headlines into
decisions worth investigating — but news is noisy, duplicated across outlets,
full of rumor, negation ("denies acquisition talks"), and ambiguity ("NEAR",
"ONE", "Apple"). Generic sentiment tools mislabel finance text (a "liability"
line item is not bad news), and paid news-analytics products (RavenPack,
Refinitiv/TRNA) are enterprise-priced black boxes. The owner asked for a
system that scrapes news, identifies the relevant information, and both gives
and interprets it — an AI trading *assistant*, where "blockchain" means crypto
is a first-class asset class alongside stocks.

The hard parts are:

- **A. Extraction + linking that is right, not just plausible.** Turning raw
  article text into a typed event with correct attributes (beat vs miss,
  raise vs cut, rumored vs confirmed vs denied) and linking it to the correct
  assets in the correct *roles* (in an acquisition, the target's signal points
  the opposite way from the acquirer's). A system that links a story about
  eating an apple to `eq:AAPL`, or reads "denied merger talks" as M&A
  bullishness, is worse than no system.
- **B. Signals whose quality is measured honestly.** Direction, magnitude,
  confidence, and horizon must be grounded in the event-study literature, and
  the backtest harness that scores them must be provably leak-free: entry
  strictly after publication, abnormal returns against a benchmark, and a
  placebo mode that must show *no* skill when event–date association is
  destroyed. Confidence must be calibrated — a 0.9-confidence signal must hit
  more often than a 0.5 one, measurably.

Both get first-class eval gates (EVALS.md). Decision **support**, not advice:
the output schema has no buy/sell field, every brief carries an uncertainty
note and a not-advice footer, and a framing check that rejects imperative
trade language is *implemented behavior* with a 1.0 eval gate — per the
workspace rule that finance safeguards are behavior, not disclaimers.

## Target user

The owner: one person who checks markets around once a day, holds US
large-cap equities and major crypto, and wants a morning triage ("what
happened overnight that touches my watchlist, ranked, explained") plus a way
to test whether the system's signals are worth listening to. Single local
profile; no accounts, no multi-tenancy, no brokerage connection — ever.
Daily bars only; this is a daily-cadence research tool, not an intraday
trading terminal.

## User stories & acceptance criteria

**US-1 — Morning digest.** As the user, I run one command and get a ranked
digest of events from the last few days that touch my watchlist: asset,
event type, direction, confidence, one-line summary.
*Accept:* `newsalpha ingest && newsalpha digest` works end-to-end on the
committed fixture feed with zero configuration; digest ranking follows the
documented formula exactly (rank = |score| desc, tie: confidence desc, then
asset id); an empty news day renders an explicit "no notable events" state,
never an error; re-running is idempotent (no duplicate events or signals).

**US-2 — Explain this headline.** As the user, I can open any article and
see exactly what the system extracted: event type, stage
(rumored/confirmed/denied), attributes, and each linked asset with its role
and the evidence span (quoted text + character offsets) that justified it.
*Accept:* every event stores ≥ 1 evidence span per trigger and per asset
link; `events show <id>` prints them; extraction meets the M1 gate
(macro-F1 ≥ 0.80) and linking the M2 gates (F1 ≥ 0.85, trap accuracy ≥ 0.90)
on the annotated fixture corpus.

**US-3 — One story, one event.** As the user, when Reuters, CoinDesk, and a
blog all cover the same hack, I see one event with corroboration count 3 —
not three signals.
*Accept:* exact duplicates are dropped by content hash; near-duplicates
cluster by title/lead shingle similarity within a 48-hour window; at most
one event per (cluster, event type); corroboration (distinct source domains)
raises confidence via the documented formula, and the fixture near-dup
clusters resolve correctly in ordinary tests.

**US-4 — Signals that admit uncertainty.** As the user, every signal tells
me direction (bullish/bearish/unclear), magnitude bucket, a confidence in
[0.05, 0.95], and a horizon in trading bars — and never tells me to trade.
*Accept:* the Signal schema contains no imperative field; confidence is
capped at 0.95 by code; rumored events score at half the confidence of
confirmed ones; denied M&A produces a *bearish* target signal (rumor
unwind), verified by unit tests; the framing gate M8 = 1.0.

**US-5 — Brief me properly.** As the user, each signal has a brief with
"what happened" (facts + quoted evidence), "why it matters" (the historical
base-rate rationale behind the prior, with its source), "what to watch"
(event-type-specific falsifiers/confirmers), and an uncertainty note.
*Accept:* all four sections present and non-empty for every persisted brief;
brief text comes only from the committed template catalog plus event fields
and quoted evidence — never free generation; a brief that fails the frame
check is never persisted (pipeline error instead); M8 gate = 1.0.

**US-6 — Prove it works (or doesn't).** As the user, I can run a backtest
over stored signals and committed price history and see per-event-type hit
rates, mean abnormal returns, and rank correlation — and a placebo run that
shows the harness finds nothing when dates are scrambled.
*Accept:* entry is strictly after publication (first available bar with
date > published date — invariant, asserted in code); abnormal return is
computed against the asset-kind benchmark; M4 (hit ≥ 0.75), M5 (IC ≥ 0.35),
M6 (calibration separation ≥ 0.15), and M7 (placebo shows |hit − 0.5| ≤ 0.06,
|IC| ≤ 0.08) all gate; signals with missing bars are excluded *and counted*
in the run report, never silently dropped.

**US-7 — My universe, my watchlist.** As the user, I can browse the asset
directory (~150 US large-caps + ~50 major crypto), add/remove watchlist
entries, and extend the universe by editing a data file.
*Accept:* `assets list/show`, `watch add/remove/list` work; gazetteer
validation at `init` rejects duplicate primary aliases and ambiguous aliases
lacking context keywords; digest defaults to watchlist-only with an
`--all-assets` escape hatch.

**US-8 — Go live when I'm ready.** As the user, I can point the tool at real
RSS feeds and real daily price data with environment variables, and the same
pipeline runs on live data.
*Accept:* `RSSNewsFeed` activates only when `NEWSALPHA_FEEDS` is set;
`LiveMarketData` (Stooq for equities, CoinGecko for crypto) only when
`NEWSALPHA_LIVE=1`; live adapters are never imported on the test/eval path;
credentials/config documented in README; a live fetch failure degrades to a
clear error, never to silently stale analysis.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-4/5 are
hard part A; FR-6/9/10 are hard part B.

- **FR-1 Ingestion & normalization.** The `NewsFeed` adapter yields
  `RawArticle`s (external id/url, source domain, published_at UTC, title,
  body). Normalization: deterministic HTML strip (tag removal + entity
  decode, no external parser), whitespace collapse, Unicode NFKC.
  `content_hash = sha256(normalized_title + "\n" + normalized_body)`.
  Articles are append-only; re-ingesting the same external id or content
  hash is a no-op (idempotent). Sentence segmentation is a committed
  deterministic rule (split on `[.!?]` + space + uppercase/digit, with an
  abbreviation exception list in `data/patterns.json`).
- **FR-2 Clustering & corroboration.** Near-duplicate detection over
  (title + first 400 chars of body): 5-gram word shingles, Jaccard
  similarity ≥ 0.6 (Broder 1997 resemblance), only within a ±48 h
  published_at window. Union-find with deterministic ordering
  (published_at asc, then article id) fixes cluster identity; the cluster id
  derives from its earliest article. `corroboration = |distinct source
  domains in cluster|`. Events attach to clusters, never to single articles.
- **FR-3 Reference datasets.** `newsalpha init` loads and validates the
  committed datasets (`data/assets.json`, `patterns.json`, `priors.json`,
  `templates.json`, `source_tiers.json`): alias uniqueness across assets;
  every alias flagged `ambiguous` has ≥ 1 context keyword; every
  (event_type, role) emitted by any pattern has exactly one prior; every
  (event_type, direction) has a template; every template contains all four
  required sections and zero forbidden-lexicon words. Validation failure
  aborts init with the offending record named.
- **FR-4 Event extraction (hard part A).** Sentence-scoped cascaded pattern
  matching (finite-state IE in the FASTUS lineage, Hobbs et al. 1997; MUC
  template filling): each `EventPattern` names an event type, trigger
  lexemes (word-boundary, case-folded), optional required-context terms,
  attribute extractors (regexes for EPS figures, percentages, USD amounts,
  guidance verbs, venue names), and suppressors. Suppression rules, applied
  within the trigger sentence: **negation** cues ("denied", "rejected",
  "ruled out", "no longer") → for M&A/partnership set `stage = denied`, for
  other types suppress; **hedge** cues ("reportedly", "in talks", "sources
  say", "rumored", "considering", "exploring") → `stage = rumored`;
  **historical-reference** guard (a year ≥ 1990 and < the article's year in
  the trigger sentence, or "anniversary", "since the") → suppress;
  **metaphor** guard (per-type stoplist, e.g. "growth hack", "hackathon",
  "life hack" for `hack_exploit`) → suppress. Every surviving event records
  type, stage, typed attributes, extraction confidence (0.9 if trigger + ≥ 1
  attribute matched, 0.7 trigger-only, +0.05 if all attributes filled,
  pre-product cap 0.95), and evidence spans (article id, char start/end,
  quote). At most one event per (cluster, type); later corroborating
  articles merge attributes (first non-null wins, evidence appended).
- **FR-5 Asset linking & roles (hard part A).** Longest-match gazetteer scan
  over the article, three evidence classes: **strong patterns** — cashtags
  (`$AAPL`), exchange prefixes (`NASDAQ: AAPL`, `NYSE: BRK.B`),
  parenthesized tickers after a name match; **plain ticker tokens** — must
  be an ALL-CAPS standalone token AND the asset must not be
  ambiguity-flagged, or a context keyword must appear in the same sentence;
  **name/alias matches** — case-sensitive per gazetteer entry ("Near" the
  word never matches "NEAR" the protocol; "Apple" requires a company-context
  keyword — "iPhone", "Cupertino", "shares", "Inc" — in the same sentence
  when flagged). Role assignment per event type from the trigger sentence:
  for M&A, `acquirer` = the linked asset preceding the trigger verb in an
  active clause ("A agreed to acquire B") and following "by" in a passive
  one ("B to be acquired by A"), `target` = the other; for
  listing/delisting, `venue` is matched from the venue lexicon (Coinbase,
  Binance, Kraken, NYSE, Nasdaq) and the listed asset gets `subject`; all
  other types assign `subject` to assets in the trigger sentence and
  `mentioned` to assets appearing only elsewhere in the article. Only
  `subject`/`acquirer`/`target` links produce signals; `mentioned` never
  does (precision-first). Every link stores its evidence span and a link
  confidence (strong pattern 0.95, plain ticker 0.85, alias + context 0.8,
  unflagged alias 0.7).
- **FR-6 Signal scoring (hard part B).** For each (event, linked asset with
  signal-bearing role), exactly one immutable Signal:
  `direction ∈ {bullish, bearish, unclear}`, `magnitude ∈ {minor, moderate,
  major}`, `horizon_bars ∈ {1, 5, 20}`, and `confidence` — all derived from
  the committed priors table (`data/priors.json`, keyed by event type +
  role + polarity attributes) and the documented modifier formula:

  ```
  confidence = clamp(base_conf(type, role)
                     · w_tier            # best tier in cluster: t1 1.00, t2 0.85, t3 0.60
                     · f_stage           # confirmed 1.00, rumored 0.50, denied 0.70
                     · (1 + 0.10 · min(corroboration − 1, 3))
                     · extraction_conf · link_conf,
                     0.05, 0.95)
  score = mid(expected_ar_lo, expected_ar_hi) · confidence
          (the prior's AR band is signed, so score carries direction;
           dir_sign := +1 bullish, −1 bearish; score = 0 when unclear)
  ```

  Polarity attributes flip direction within a type (beat/miss, raise/cut,
  approval/enforcement); a denied M&A rumor produces a *bearish minor*
  target signal (rumor-premium unwind). Rationale codes (e.g.
  `prior:mna.target.definitive`, `mod:stage=rumored`, `mod:tier=t2`) record
  every factor applied. Unique (event_id, asset_id); re-running the
  pipeline never mutates an existing signal.
- **FR-7 Briefs & framing safeguard.** Each signal renders one Brief from
  the committed template catalog: `what_happened` (event facts + up to two
  quoted evidence spans with source attribution), `why_it_matters` (the
  prior's rationale text including its literature base rate, verbatim from
  `priors.json.rationale`), `what_to_watch` (type-specific watch items from
  the template, e.g. M&A rumor → "definitive agreement or denial; filed
  8-K", hack → "official post-mortem, reimbursement plan, TVL outflows"),
  `uncertainty_note` (confidence, stage, and what would falsify the
  signal). The **frame check** then asserts: zero forbidden-lexicon matches
  ("buy", "sell", "short it", "you should", "act now", "guaranteed",
  "can't lose", "sure thing" — full list committed in `data/patterns.json`)
  in template-generated text (quoted evidence is exempt but must be inside
  quotation marks with attribution); all four sections non-empty; the
  not-advice footer present verbatim. A brief failing the check is never
  persisted or emitted — the pipeline raises. This is the workspace-mandated
  finance safeguard as implemented behavior; gate M8 = 1.0.
- **FR-8 Digest & triage.** `digest(date, watchlist_only=True)` returns
  signals whose event time ∈ (date − 5 days, date], filtered to watchlist
  assets by default, ranked by |score| desc, tie confidence desc, tie
  asset id asc. Renders asset, type, stage, direction, magnitude,
  confidence, horizon, one-line summary; empty state explicit.
- **FR-9 Market data & calendars.** `MarketData` adapter yields daily OHLCV
  bars. Bars store unique (asset_id, date). Equity assets trade weekdays
  (no holiday table — documented simplification, see D-11; all engine rules
  are defined over *available bars*, so real holidays degrade gracefully),
  crypto trades every day. Each asset kind has a benchmark series
  (`idx:US`, `idx:CX`) required before a backtest may run.
- **FR-10 Backtest harness (hard part B).** Event-study methodology
  (MacKinlay 1997): for each directional signal, `entry` = the first
  available bar for that asset with `date > published date (UTC)` —
  strictly-after is an asserted invariant (look-ahead bias prevention).
  For horizon h: `AR_h = log(close[entry + h − 1] / open[entry]) −
  log(close_bm[entry + h − 1] / open_bm[entry])` using the market-adjusted
  model (Brown & Warner 1985 — comparable power to the market model on
  daily data without estimating beta on short histories). `hit` =
  `sign(AR_h) == dir_sign` at the signal's own horizon. Aggregates per
  event type and overall: N, hit rate, mean AR, Spearman IC between score
  and AR, calibration buckets (confidence < 0.45, 0.45–0.70, ≥ 0.70) with
  per-bucket hit rates. Signals with insufficient bars are excluded with a
  named reason and counted in the run report. **Placebo mode**: with a
  seed, each signal's entry is displaced by a uniform ±[20, 60]-bar offset
  (re-drawn if the displaced window overlaps any real fixture event window
  for that asset); everything else identical. Backtest runs are persisted
  and never mutate signals.
- **FR-11 Watchlist.** Add/remove/list asset ids; unknown ids rejected with
  the nearest-alias suggestion.
- **FR-12 API.** FastAPI app per the endpoint sketch below; thin —
  validation, service calls, serialization only.
- **FR-13 CLI.** Typer app per the command sketch below; same services as
  the API; human-readable tables, `--json` escape hatch on list/show
  commands.
- **FR-14 Determinism & hermeticity.** The pipeline is a pure function of
  (articles, datasets, as_of); all ids are content-derived (see
  DATA_MODEL.md), so replays are byte-identical and re-ingest is
  idempotent. Time is always an input (`as_of`, `published_at`); the only
  randomness is the placebo seed. Tests and evals use `FixtureNewsFeed` +
  `FixtureMarketData` exclusively; live adapters live behind an extra and
  are never imported on that path.

### Event typology (FR-4/5/6, the committed taxonomy)

| Type | Trigger examples | Typed attributes | Signal-bearing roles | Prior (confirmed) |
|---|---|---|---|---|
| `earnings_surprise` | "beat estimates", "missed expectations", "EPS of $X vs $Y expected" | `polarity` beat\|miss, `surprise_pct?` | subject | beat: bullish/moderate/20 bars (PEAD drift); miss: bearish/moderate/20 |
| `guidance_change` | "raised full-year guidance", "cut outlook", "withdrew forecast" | `polarity` raise\|cut\|withdraw | subject | raise: bullish/moderate/5; cut/withdraw: bearish/moderate/5 |
| `mna` | "agreed to acquire", "to be acquired by", "merger", "takeover bid" | `stage` rumored\|confirmed\|denied, `premium_pct?`, `deal_value_usd?` | acquirer, target | target: bullish/major/1; acquirer: bearish/minor/5 |
| `regulatory_action` | "SEC sued", "fined", "approved", "banned", "investigation" | `polarity` favorable\|adverse, `agency?`, `amount_usd?` | subject | adverse: bearish/moderate/5; favorable: bullish/moderate/5 |
| `listing` | "to list", "listed on", "available on Coinbase/Binance" | `venue` | subject (+ venue link, non-signal) | bullish/major/5 (crypto), bullish/minor/5 (equity index add) |
| `delisting` | "delisted", "to remove", "removal from" | `venue` | subject | bearish/major/5 |
| `hack_exploit` | "exploited", "bridge hack", "$X drained", "funds stolen" | `amount_usd?`, `vector?` | subject | bearish/major/5 |
| `partnership` | "partnership with", "strategic alliance", "collaboration" | `counterparty_text` | subject | bullish/minor/5, low base confidence |

Articles matching no pattern are archived event-less — "no event" is a
correct and common outcome, not a failure.

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision). Endpoints are
   shaped so a later dashboard is a pure client.
2. **No trade execution, brokerage/exchange integration, position tracking,
   or sizing advice — ever.** Permanent product boundary, not a deferral:
   the tool ranks things to look into; it does not touch money.
3. **No LLM anywhere.** Extraction is a deterministic pattern engine; briefs
   are templated. The `extract.py` seam is where an LLM-assisted extractor
   could later slot in behind the same Event schema, but it is out of scope
   and would still have to pass the same eval gates.
4. **No general sentiment scoring.** Event-typed extraction only; tone
   scoring without an event type is exactly the noise this product exists to
   filter (Loughran & McDonald 2011 show why generic sentiment fails on
   finance text).
5. **No intraday data.** Daily bars; horizons in bars. Consequence stated
   honestly: much of an announcement move happens before the next open, and
   the backtest measures only what a daily-cadence user could see from the
   next bar onward.
6. **No social-media ingestion** (X/Reddit/Telegram), no on-chain analytics,
   no order books, no options/derivatives, non-English sources excluded.
7. **Universe is the committed gazetteer** (~150 US large-caps + ~50 major
   crypto assets). Extending it is a data edit, not a code change; full
   exchange coverage and automatic symbology sync (CUSIP/FIGI) are out.
8. **Live adapters are thin.** RSS polling (no scraping of paywalled or
   JS-rendered pages), Stooq/CoinGecko daily closes. Scheduling/daemon mode
   is out — the user runs `ingest` when they want it.
9. **No portfolio-level analytics** (correlation, exposure, risk). Signals
   are per-event, per-asset.
10. **No push notifications or email.**

## Architecture

```
projects/newsalpha/
  src/newsalpha/
    models.py          # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/
      normalize.py     # FR-1: HTML strip, NFKC, hashing, sentence segmentation
      cluster.py       # FR-2: shingling, Jaccard, union-find, corroboration
      extract.py       # FR-4: pattern engine, suppressors, stages, attributes, evidence
      link.py          # FR-5: gazetteer scan, disambiguation, role assignment
      score.py         # FR-6: priors + modifier formula, rationale codes
      brief.py         # FR-7: template rendering
      frame.py         # FR-7: forbidden-lexicon + required-section frame check
      digest.py        # FR-8: window filter + ranking
      calendar.py      # FR-9: per-kind calendars, entry-bar resolution
      backtest.py      # FR-10: AR/CAR, hits, IC, calibration buckets, placebo
      pipeline.py      # FR-14: pure orchestration (articles, datasets, as_of) → outputs
    adapters/
      newsfeed.py      # NewsFeed Protocol
      newsfeed_fixture.py   #   offline: FixtureNewsFeed (JSONL path)
      newsfeed_rss.py       #   live: RSSNewsFeed (feedparser; extra "live")
      marketdata.py    # MarketData Protocol
      marketdata_fixture.py #   offline: FixtureMarketData (committed CSVs)
      marketdata_live.py    #   live: LiveMarketData = Stooq (eq) + CoinGecko (cx)
    store/             # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/               # FastAPI app
    cli/               # Typer app
  data/                # assets.json, patterns.json, priors.json, templates.json, source_tiers.json
  evals/               # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated, extra `live`) |
|---|---|---|
| `NewsFeed.fetch(since: datetime, until: datetime) -> list[RawArticle]` | `FixtureNewsFeed(path)` — reads a committed JSONL corpus; deterministic order (published_at, external_id) | `RSSNewsFeed` — feedparser over the comma-separated URLs in `NEWSALPHA_FEEDS`; entry link/guid → external_id, entry summary/content → body; activates only when the env var is set |
| `MarketData.daily_bars(asset_id: str, start: date, end: date) -> list[Bar]` | `FixtureMarketData(dir)` — committed per-asset CSVs incl. `idx:US`, `idx:CX` | `LiveMarketData` — routes by asset kind: Stooq free CSV endpoint for equities (no key), CoinGecko `/market_chart` for crypto (`NEWSALPHA_COINGECKO_KEY` optional, raises limits); activates only when `NEWSALPHA_LIVE=1` |

The store is the third seam per conventions: `Repository` protocol with
`SQLiteRepository` (default `~/.newsalpha/newsalpha.db`, path via
`NEWSALPHA_DB`) and `InMemoryRepository` for tests.

### API sketch (FastAPI)

```
GET  /health
POST /ingest                    # {as_of, since?, feed?: fixture|rss, path?} → counts {articles, clusters, events, signals, briefs}
GET  /articles?since=&domain=&limit=       GET /articles/{id}
GET  /events?type=&asset=&stage=&since=    GET /events/{id}    # incl. evidence spans + links
GET  /signals?asset=&min_confidence=&direction=&since=         GET /signals/{id}
GET  /briefs/{signal_id}
GET  /digest?date=&watchlist_only=true
GET  /assets?kind=&q=                      GET /assets/{id}
GET  /watchlist    PUT /watchlist/{asset_id}    DELETE /watchlist/{asset_id}
POST /prices/load               # {source: fixture|live, assets?, start, end} → bar counts
POST /backtests                 # {start, end, min_confidence?, placebo_seed?} → run + aggregates
GET  /backtests/{run_id}        GET /backtests?limit=
```

### CLI sketch (Typer)

```
newsalpha init                                      # create DB, load + validate datasets
newsalpha ingest [--feed fixture|rss] [--path F] [--since T] [--until T] [--as-of T]
newsalpha articles list [--domain --since] | show <id>
newsalpha events list [--type --asset --stage] | show <id>      # show prints evidence spans
newsalpha signals list [--asset --min-confidence --direction] | show <id>
newsalpha brief <signal-id>
newsalpha digest [--date D] [--all-assets]
newsalpha assets list [--kind --query] | show <asset-id>
newsalpha watch add|remove <asset-id> | list
newsalpha prices load [--source fixture|live] [--start --end]
newsalpha backtest run [--start --end --min-confidence] | placebo [--seed S] | show <run-id>
```

## Key design decisions & assumptions

1. **Deterministic pattern extraction, not ML/LLM.** Cascaded finite-state
   information extraction in the FASTUS lineage (Hobbs et al. 1997) filling
   MUC-style event templates. Rationale: hermetic evals (workspace rule),
   inspectable evidence spans on every claim, and a 2–4k-line budget. The
   Loughran & McDonald (2011, *J. Finance*) result — general-purpose word
   lists misclassify financial text — is why every lexicon here is
   finance-specific committed data, not a generic NLP resource.
2. **Event typology and enrichment mirror commercial news analytics.**
   RavenPack and Thomson Reuters News Analytics ship typed event categories
   with relevance, novelty, and source-rank fields; our cluster-based
   novelty (one event per cluster/type), corroboration count, and source
   tiers are the open, inspectable version of ENS/relevance/source-rank.
3. **Precision over recall in linking.** A missed link costs one
   opportunity; a false link produces a confidently wrong brief. Hence:
   `mentioned` role never signals, ambiguous aliases require same-sentence
   context, plain tickers must be ALL-CAPS tokens, and the M2 trap gate
   (0.90) is stricter than the overall F1 gate. Ticker/common-word collision
   (NEAR, ONE, ICP, APE, COIN, Meta, Oracle, Shell) is a known failure mode
   of financial NER and gets dedicated fixture traps.
4. **Scoring priors are literature-grounded data.** Every `priors.json` row
   carries a `rationale` + `source_note`: earnings surprise → post-earnings
   announcement drift (Ball & Brown 1968; Bernard & Thomas 1989) justifies
   the 20-bar horizon; M&A → target announcement CARs of roughly +16–24 %
   and acquirer CARs ≈ 0 to −1 % (Andrade, Mitchell & Stafford 2001, *JEP*)
   justify the asymmetric roles; guidance → price reaction to management
   forecasts (Skinner 1994; Anilowski, Feng & Skinner 2007); regulatory
   enforcement → large reputational penalties beyond fines (Karpoff, Lee &
   Martin 2008, *JFQA*); partnerships → small positive alliance-announcement
   ARs ≈ +0.6 % (Chan, Kensinger, Keown & Martin 1997, *JFE*) justify
   minor/low-confidence; crypto exchange listing → the documented "Coinbase
   effect" of double-digit short-run abnormal returns around listing
   announcements (Messari 2021 analysis; Binance-listing studies);
   hacks → sharp drawdowns of exploited protocols' tokens (Ronin/Axie 2022
   as exemplar). Priors are editable data; the engine never hard-codes an
   expected return.
5. **Market-adjusted abnormal returns, not the market model.** Brown &
   Warner (1985) show market-adjusted returns (`AR = R − R_benchmark`) have
   power comparable to beta-adjusted models in daily event studies, and
   beta estimation on short/synthetic histories is noise. Benchmarks per
   asset kind: `idx:US` (equities), `idx:CX` (crypto); live proxies SPY and
   BTC, documented in README.
6. **Strictly-after entry rule.** Entry = first available bar with
   date > publication date; the harness asserts it. This kills look-ahead
   bias by construction and honestly concedes the overnight gap (non-goal
   5): the tool measures what a daily-cadence user could act on, which is
   exactly the PEAD-style drift literature's tradable window.
7. **Placebo runs are a first-class feature, not just an eval.** Randomized
   event dates are the standard robustness check in the event-study
   tradition (Brown & Warner's simulation methodology); exposing
   `backtest placebo` in the CLI lets the owner re-verify harness honesty
   on any future data, and M7 gates it in CI.
8. **Confidence means P(direction correct) and is capped at 0.95.** The
   modifier formula (tier, stage, corroboration, extraction, link) is
   multiplicative and committed; calibration is measured with fixed-edge
   buckets and gated (M6) in the reliability-diagram tradition (Murphy
   1973; Brier 1950). The 0.95 cap is a safeguard: the system may never
   claim certainty.
9. **Framing is enforced, not requested.** The frame check (forbidden
   imperative lexicon, required sections, verbatim not-advice footer) runs
   on every brief before persistence and raises on failure. This implements
   the workspace finance-safeguard rule and keeps the product on the
   information side of the information-vs-personalized-advice line that
   investment-adviser regulation draws.
10. **Rumor / confirmed / denied is a stage, not a filter.** Rumors are
    tradable information with lower base rates — merger-arb practice prices
    deal risk, so `rumored` halves confidence rather than suppressing, and
    `denied` M&A emits a bearish target signal (unwind of the rumor
    premium). Calibration (M6) is what keeps stages honest.
11. **Calendars: equities trade weekdays, crypto every day; no holiday
    table.** All engine rules are defined over *available bars* ("first bar
    after date d"), so a real-world holiday shifts entry by a day rather
    than breaking anything; the weekday rule is only load-bearing inside
    the fixture generator. Documented simplification, revisit if live use
    shows drift.
12. **Content-derived ids everywhere** (sha256-prefix of natural keys, see
    DATA_MODEL.md) make ingestion idempotent and replays byte-identical —
    determinism (FR-14) falls out of the id scheme instead of being bolted
    on.
13. **Dedup via shingling** (Broder 1997): 5-gram word shingles, Jaccard
    ≥ 0.6, ±48 h window. Exact Jaccard is fine at personal-tool scale
    (hundreds of articles/day); MinHash is deliberately omitted.
14. **Briefs are templates + quoted evidence, never generation.** Same
    rationale as ChessMentor's advice catalog: deterministic, hermetic,
    and every sentence traceable to either a committed template, a
    committed prior rationale, or a quoted span of the source article.
15. **The fixture corpus is synthetic-but-realistic and fully annotated by
    construction** (EVALS.md). Real scraped articles are avoided in the
    repo for licensing and determinism; realism is achieved by modeling
    templates on real newswire phrasing and committing hand-authored
    adversarial cases.
16. **Signals are immutable; analysis is append-only.** Re-running the
    pipeline can add new events/signals but never rewrites history — the
    backtest must evaluate what the system said at the time, or it is
    dishonest by construction.
17. **Assumption: single user, single process.** SQLite with no concurrent
    writers; API and CLI share one DB file; no auth on the API (binds
    localhost by default).
18. **Assumption: implementation lands in ~2,800–3,600 lines** across
    engine/adapters/store/api/cli, within the 2–4k mandate. Scope valves,
    in order: drop `partnership` extraction (degrades to no-event), shrink
    the gazetteer, fix backtest horizons to the signal's own horizon only.
    The extraction/linking/backtest core is not a valve — it is the
    product.
19. **Assumption: live feeds are RSS/Atom with usable timestamps.** Feeds
    lacking `published` fall back to fetch time (recorded as such,
    flagged `published_at_estimated` — such articles still extract but
    their signals are excluded from backtests, since entry timing would be
    untrustworthy).
