# NewsAlpha — Scope

## One-liner

A single-user decision-support tool that ingests financial news (equities +
crypto), extracts *typed* events (earnings surprise, M&A, guidance change,
regulatory action, exchange listing/delisting, protocol hack), links them to
the right assets in the right roles, scores each linked event as a signal
(direction, magnitude, confidence, horizon), renders a plain-language brief
("what happened, why it matters, what to watch") — and proves its own signal
quality with a leak-free event-study backtest.

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
  the opposite way from the acquirer's; in a listing, Coinbase is the venue,
  not the subject). A system that links a story about eating an apple to
  `eq:AAPL`, or reads "denied merger talks" as M&A bullishness, is worse than
  no system.
- **B. Signals whose quality is measured honestly.** Direction, magnitude,
  confidence, and horizon must be grounded in the event-study literature *and
  scoped to what the product's own entry rule can actually capture*, and the
  backtest harness that scores them must be provably leak-free: entry strictly
  after the last evidence publication, abnormal returns against a benchmark,
  and a placebo mode that must show *no* skill when event–date association is
  destroyed. Confidence must be calibrated — a 0.9-confidence signal must hit
  more often than a 0.5 one, measurably.

Both get first-class eval gates (EVALS.md). Decision **support**, not advice:
the output schema has no buy/sell field, every brief carries an uncertainty
note and a not-advice footer, and a framing check that rejects imperative
trade language is *implemented behavior* with a 1.0 eval gate — per the
workspace rule that finance safeguards are behavior, not disclaimers.

### How the owner's idea is interpreted (explicit, for sign-off)

"AI trading assistant" is delivered this pass as **deterministic information
extraction + templated interpretation**, not as an LLM. CONVENTIONS.md §3
*would* permit an LLM behind a provider interface with a deterministic offline
implementation, so hermetic evals alone do not force the exclusion. The honest
reasons are: (a) the 2–4k-line budget is fully consumed by extraction,
linking, scoring, and an honest backtest; (b) every claim the product makes
must be traceable to a quoted character span, which a pattern engine gives by
construction; (c) an eval gate that a fixture-only LLM passes says nothing
about the live adapter. `engine/extract.py` and `engine/brief.py` are the
sanctioned future seams (non-goal 3). "Blockchain" is read as *asset class*,
not infrastructure — no chain is written to. Both readings are owner-visible
here and in REVIEW.md.

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
never an error; re-running `ingest` with no new articles produces zero new
signal revisions and a byte-identical digest (FR-15).

**US-2 — Explain this headline.** As the user, I can open any article and
see exactly what the system extracted: event type, stage
(rumored/confirmed/denied), attributes, and each linked asset with its role
and the evidence span (quoted text + character offsets) that justified it.
*Accept:* every event stores ≥ 1 evidence span per trigger and per asset
link; `events show <id>` prints them; extraction meets the M1a gate
(macro-F1 ≥ 0.80 on the held-out paraphrase family) and linking the M2 gates
(F1 ≥ 0.85, trap accuracy ≥ 0.90) on the annotated fixture corpus.

**US-3 — One story, one event; later news updates it.** As the user, when
Reuters, CoinDesk, and a blog all cover the same hack, I see one event with
corroboration count 3 — not three signals. When the second and third outlets
only arrive in tomorrow's ingest, the signal's confidence *rises* instead of
being frozen at Monday's value.
*Accept:* exact duplicates are dropped by content hash; near-duplicates
cluster by shingle similarity within a 48-hour window (FR-2); at most one
event per (cluster, event type); corroboration raises confidence by emitting
a **new signal revision** (FR-15) whose `rationale_codes` record the new
corroboration count, while the previous revision is retained unmodified for
backtest honesty; the digest shows only the latest revision.

**US-4 — Signals that admit uncertainty.** As the user, every signal tells
me direction (bullish/bearish/unclear), magnitude bucket, a confidence in
[0.05, 0.95], and a horizon in trading bars — and never tells me to trade.
*Accept:* the Signal schema contains no imperative field; confidence is
capped at 0.95 by code; with all other factors equal a `rumored` event scores
at exactly half the confidence of a `confirmed` one (`f_stage` is the *only*
stage effect on confidence, FR-6); a denied M&A rumor produces a *bearish*
target signal and *no* acquirer signal, verified by unit tests; the framing
gate M8 = 1.0.

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
*Accept:* entry is strictly after the signal's `observed_at` (first available
bar with date > that date — invariant, asserted in code); abnormal return is
computed against the asset-kind benchmark **at matching calendar dates**;
M4 (hit ≥ 0.72), M5 (IC ≥ 0.35), M6 (calibration separation ≥ 0.15, with
bucket occupancy), M7 (placebo |mean hit − 0.5| ≤ 0.035, |mean IC| ≤ 0.06)
and the denominator gates G1/G2 all gate; signals with missing bars are
excluded with a named reason and counted in the run report, never silently
dropped.

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
`NEWSALPHA_LIVE=1`; live adapters are never imported on the test/eval path
(asserted by a hermeticity test); credentials/config documented in README; a
live fetch failure degrades to a clear error, never to silently stale
analysis; `backtest placebo` is fully defined on live data (FR-10) with no
reference to eval fixtures.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-4/5 are
hard part A; FR-6/10 are hard part B. FR-15 is the temporal spine that makes
daily incremental use coherent.

- **FR-1 Ingestion & normalization.** The `NewsFeed` adapter yields
  `RawArticle`s (external id/url, source domain, published_at UTC, title,
  body). Normalization: deterministic HTML strip (tag removal + entity
  decode, no external parser), whitespace collapse, Unicode NFKC.
  `content_hash = sha256(normalized_title + "\n" + normalized_body)`.
  Articles are append-only; re-ingesting the same external id or content
  hash is a no-op (idempotent). Sentence segmentation is a committed
  deterministic rule (split on `[.!?]` + space + uppercase/digit, with an
  abbreviation exception list in `data/patterns.json`). An article whose
  `published_at` is older than `as_of − active_window_days` (FR-15) is stored
  with `excluded_from_analysis = true` and takes no part in clustering,
  extraction, or scoring; the field is set once at insert and never changes.

- **FR-2 Clustering & corroboration.** Near-duplicate detection with a fully
  pinned tokenization, so any faithful implementation reproduces the same
  clusters:

  ```
  shingle_input(a) = normalized_title + " " + normalized_body[:400]
  tokens(s)        = casefold(s), replace every maximal run of characters
                     outside [a-z0-9$] with a single space, strip, split(" ")
  shingles(a)      = set of contiguous 5-token windows of tokens(shingle_input(a))
                     (if < 5 tokens: the single tuple of all tokens)
  similar(a, b)    = |shingles(a) ∩ shingles(b)| / |shingles(a) ∪ shingles(b)| ≥ 0.60
                     AND |published_at(a) − published_at(b)| ≤ 48 h
  ```

  Clusters are the **transitive closure** (union-find) of `similar` over all
  in-window articles (FR-15), so membership is independent of ingest order
  and of how articles were partitioned across runs. Cluster identity is a
  pure function of that membership:
  `cluster_id = hex16("cluster|" + article_id of the member minimizing
  (published_at, article_id))`. `corroboration = |distinct source domains in
  cluster|`; `best_tier = max tier over members`. Events attach to clusters,
  never to single articles.

- **FR-3 Reference datasets.** `newsalpha init` loads and validates the
  committed datasets (`data/assets.json`, `patterns.json`, `priors.json`,
  `templates.json`, `source_tiers.json`, `benchmarks.json`). Validation
  aborts init naming the offending record. Checks:
  1. alias uniqueness across assets; every alias flagged `ambiguous` has ≥ 1
     context keyword and vice versa; `id` prefix matches `kind`;
     `benchmark_id` set iff `kind != index`.
  2. every `EventPattern` has a non-empty `real_world_example` (a real
     headline phrasing the pattern generalizes from — the anti-overfit
     maintainer rule, EVALS.md);
     every attribute regex compiles with ≤ 1 capture group; trigger sets are
     disjoint across event types; ≥ 2 patterns per event type.
  3. **prior resolution**: for every (event_type, signal-bearing role,
     polarity value reachable from any pattern, asset kind ∈ {equity,
     crypto}) exactly one prior row resolves under most-specific-wins
     (kind-specific beats `kind: "*"`); no prior key token is a `Stage`
     value; every stage reachable for that event type is covered by the base
     row or a `stage_overrides` entry; `sign(mid_expected_ar) == dir_sign`
     for the base row and every override; `magnitude` matches the band
     thresholds (|mid| < 0.01 minor, 0.01–0.04 moderate, > 0.04 major).
  4. every (event_type, resolved direction) has a template; every template
     has all four sections non-empty and zero forbidden-lexicon matches;
     `templates.json.footer` is present.
  5. `patterns.json.forbidden_lexicon` is a superset of the reference
     lexicon embedded in `evals/metrics.py` (checked by an eval, EVALS M8).

- **FR-4 Event extraction (hard part A).** Sentence-scoped cascaded pattern
  matching (finite-state IE in the FASTUS lineage, Hobbs et al. 1997; MUC
  template filling): each `EventPattern` names an event type, trigger
  lexemes (word-boundary, case-folded), optional required-context terms,
  attribute extractors (regexes for EPS figures, percentages, USD amounts,
  guidance verbs, venue names), and suppressors. Suppression rules, applied
  within the trigger sentence:
  - **negation** cues ("denied", "rejected", "ruled out", "no longer") → for
    `mna` set `stage = denied`; for every other type suppress the event;
  - **hedge** cues ("reportedly", "in talks", "sources say", "rumored",
    "considering", "exploring") → `stage = rumored`;
  - **historical-reference** guard (a year ≥ 1990 and < the article's year in
    the trigger sentence, or "anniversary", "since the") → suppress;
  - **metaphor** guard (per-type stoplist, e.g. "growth hack", "hackathon",
    "life hack" for `hack_exploit`) → suppress.

  Cue precedence inside one sentence: negation > hedge (a sentence carrying
  both is a denial). Every surviving event records type, stage, typed
  attributes, extraction confidence (0.9 if trigger + ≥ 1 attribute matched,
  0.7 trigger-only, +0.05 if all declared attributes filled, cap 0.95), and
  evidence spans (article id, char start/end, quote).

  **Merge rule (one rule, restated identically in DATA_MODEL.md).** At most
  one event per (cluster, event_type). When a cluster has several
  trigger-bearing articles: evidence spans from all of them are appended in
  (published_at, article_id) order; `stage` is taken from the trigger-bearing
  article with the **latest** `published_at` that carries a stage cue (tie:
  largest article_id), so a later confirmation or denial supersedes an
  earlier rumor within the cluster; every other attribute takes the first
  non-null value in (published_at, article_id) order; `extraction_confidence`
  is the maximum over contributing articles.

- **FR-5 Asset linking & roles (hard part A).** Longest-match gazetteer scan
  over the article, three evidence classes:
  - **strong patterns** — cashtags (`$AAPL`), exchange prefixes
    (`NASDAQ: AAPL`, `NYSE: BRK.B`), parenthesized tickers after a name
    match (link confidence 0.95);
  - **plain ticker tokens** — must be an ALL-CAPS standalone token AND the
    asset must not be ambiguity-flagged, or a context keyword must appear in
    the same sentence (0.85);
  - **name/alias matches** — case-sensitive per gazetteer entry ("Near" the
    word never matches "NEAR" the protocol; "Apple" requires a
    company-context keyword — "iPhone", "Cupertino", "shares", "Inc" — in the
    same sentence when flagged) (0.8 with context, 0.7 unflagged).

  **All-caps guard.** Let `caps_ratio(s)` = share of alphabetic tokens of
  length ≥ 2 in sentence `s` that are entirely uppercase. If
  `caps_ratio(s) ≥ 0.7` and `s` has ≥ 4 such tokens (wire-style headlines:
  "APPLE NEAR DEAL TO ACQUIRE …"), then in `s`: plain-ticker evidence is
  **disabled entirely**, and alias matching becomes case-insensitive but
  requires a same-sentence context keyword for *every* asset (flagged or not),
  with link confidence 0.7. Strong patterns are unaffected.

  **Role assignment** per event type, from the trigger sentence:
  - `mna`: `acquirer` = the linked asset preceding the trigger verb in an
    active clause ("A agreed to acquire B") or following "by" in a passive
    one ("B to be acquired by A"); `target` = the other. Additional
    resolvers: "bid/offer for X" and "takeover bid for X" ⇒ `target = X`.
    **Fallback:** if none of these resolves both parties uniquely (symmetric
    constructions — "merger between A and B", "A-B merger talks", "merger of
    equals"), assign **all** linked parties `role = mentioned`, emit no
    signal, and record `link:mna_role_unresolved` on the event. Guessing is
    forbidden: a role error is a sign error on a high-magnitude prior (D-3).
  - `listing` / `delisting`: the venue lexicon (Coinbase, Binance, Kraken,
    NYSE, Nasdaq, …) is scanned **first** and its matched spans are
    *consumed* — no other role may claim an overlapping span. If a venue
    surface is also a gazetteer asset (Coinbase → `eq:COIN`, Nasdaq →
    `eq:NDAQ`, NYSE → `eq:ICE`), it is linked with `role = venue`, which is
    never signal-bearing. `subject` = the unique remaining signal-eligible
    linked asset in the trigger sentence; if zero or ≥ 2 remain, no subject
    link is created, no signal is emitted, and `link:ambiguous_subject` is
    recorded.
  - all other types: `subject` for assets in the trigger sentence,
    `mentioned` for assets appearing only elsewhere in the article.

  Only `subject`/`acquirer`/`target` links produce signals; `mentioned` and
  `venue` never do (precision-first). Every link stores its evidence span and
  link confidence.

- **FR-6 Signal scoring (hard part B).** For each (event, linked asset with
  signal-bearing role), the scorer produces exactly one *scored tuple*, which
  FR-15 turns into a signal revision. Values come from the committed priors
  table (`data/priors.json`) and this formula:

  ```
  prior       = resolve(event_type, role, polarity, asset.kind)   # most-specific-wins
  eff         = prior.stage_overrides[event.stage] if present else prior
                # eff supplies direction, magnitude, expected_ar_lo/hi, horizon_bars
  direction   = eff.direction        (unclear ⇒ no signal is emitted)
  horizon     = eff.horizon_bars     ∈ {1, 5, 20}
  mid_ar      = (eff.expected_ar_lo + eff.expected_ar_hi) / 2     # signed

  confidence  = clamp(prior.base_conf
                      · w_tier      # best tier in cluster: t1 1.00, t2 0.85, t3 0.60
                      · f_stage     # confirmed 1.00, rumored 0.50, denied 0.70
                      · (1 + 0.10 · min(corroboration − 1, 3))
                      · extraction_conf · link_conf,
                      0.05, 0.95)
  score       = mid_ar · confidence          # signed; sign(mid_ar) == dir_sign (FR-3)
  ```

  **Stage is applied exactly once in each channel** and the two channels are
  disjoint: `f_stage` is the *only* stage effect on **confidence**;
  `stage_overrides` is the *only* stage effect on **direction / magnitude /
  band / horizon**. Prior keys never encode stage (FR-3 invariant), so US-4's
  "rumored = half the confidence of confirmed" is exactly true. Polarity
  attributes (beat/miss, raise/cut/withdraw, favorable/adverse) select the
  prior row and therefore flip direction within a type. A denied M&A rumor
  resolves `mna.target.*` → `stage_overrides.denied` (bearish, unwind band)
  and `mna.acquirer.*` → `stage_overrides.denied` (direction `unclear` ⇒ no
  acquirer signal). Rationale codes record every factor applied, e.g.
  `prior:mna.target.*`, `mod:stage_override=denied`, `mod:f_stage=0.70`,
  `mod:tier=t2_wire`, `mod:corroboration=3`, `mod:extraction=0.95`,
  `mod:link=0.85`.

- **FR-7 Briefs & framing safeguard.** Each signal revision renders one Brief
  from the committed template catalog: `what_happened` (event facts + up to
  two quoted evidence spans with source attribution), `why_it_matters` (the
  prior's rationale text including its literature **announcement-window** base
  rate, verbatim from `priors.json.rationale`), `what_to_watch` (type-specific
  watch items, e.g. M&A rumor → "definitive agreement or denial; filed 8-K",
  hack → "official post-mortem, reimbursement plan, TVL outflows"),
  `uncertainty_note` (confidence, stage, and what would falsify the signal —
  and, for types where the announcement band far exceeds the post-entry band,
  the sentence that most of the announcement move is already priced, D-6).
  The **frame check** then asserts: zero forbidden-lexicon matches ("buy",
  "sell", "short it", "you should", "act now", "guaranteed", "can't lose",
  "sure thing" — full list committed in `data/patterns.json`, word-boundary
  matched so "buyout"/"sell-off"/"buyer" do not fire) in template-generated
  text (quoted evidence is exempt but must be inside quotation marks with
  attribution); all four sections non-empty; the not-advice footer present
  verbatim. A brief failing the check is never persisted or emitted — the
  pipeline raises. This is the workspace-mandated finance safeguard as
  implemented behavior; gate M8 = 1.0.

- **FR-8 Digest & triage.** `digest(date, watchlist_only=True,
  include_superseded=False)` returns the **latest revision** of each signal
  whose event date ∈ (date − 5 days, date], filtered to watchlist assets by
  default, **excluding superseded signals** (FR-15) unless
  `include_superseded`, ranked by |score| desc, tie confidence desc, tie
  asset id asc. A signal that supersedes another is annotated
  "supersedes an earlier <stage> signal from <date>". Renders asset, type,
  stage, direction, magnitude, confidence, horizon, one-line summary; empty
  state explicit.

- **FR-9 Market data & calendars.** `MarketData` adapter yields daily OHLCV
  bars. Bars store unique (asset_id, date). Equity assets trade weekdays
  (no holiday table — documented simplification, D-11; all engine rules are
  defined over *available bars*, so real holidays degrade gracefully), crypto
  trades every day. Each asset kind has a benchmark series (`idx:US`,
  `idx:CX`) required before a backtest may run. The live `idx:CX` series is
  the equal-weighted mean of the daily log returns of the 8-asset basket in
  `data/benchmarks.json` (BTC, ETH, SOL, XRP, ADA, AVAX, LINK, DOT) — not BTC
  alone — so no single universe asset is its own benchmark (D-5).

- **FR-10 Backtest harness (hard part B).** Event-study methodology
  (MacKinlay 1997). For each directional signal revision:

  ```
  d_entry = first available bar date for the asset with date > date(signal.observed_at)
            (strictly-after is an asserted invariant — look-ahead prevention)
  d_exit  = the (h − 1)-th available bar date after d_entry, h = signal.horizon_bars
  AR_h    = log(close_a[d_exit] / open_a[d_entry])
          − log(close_b[d_exit] / open_b[d_entry])        # b = asset.benchmark_id
  ```

  The **benchmark leg is evaluated at the same calendar dates** `d_entry` and
  `d_exit` as the asset leg (never at positional offsets into the benchmark's
  own bar sequence); if the benchmark lacks a bar on either date the signal is
  excluded with `benchmark_gap`. Market-adjusted model (Brown & Warner 1985 —
  comparable power to the market model on daily data without estimating beta
  on short histories). `hit = (sign(AR_h) == dir_sign)`; if `AR_h == 0`
  exactly, the signal is excluded with `zero_abnormal_return` rather than
  silently counted as a miss.

  Aggregates per event type and overall: N, N excluded (by reason), hit rate,
  mean AR, Spearman IC between `score` and AR, calibration buckets
  (confidence < 0.45, 0.45–0.70, ≥ 0.70) with per-bucket hit rates and counts.
  Superseded signals are **included** (they were real statements at the time)
  and additionally reported as a breakdown — excluding them would let a
  denominator dodge inflate the hit rate. Backtest scope is the latest
  revision of each signal key.

  **Placebo mode** (product feature, defined without reference to any eval
  fixture): given `placebo_seed`, each signal's entry date is displaced by
  `offset` bars where

  ```
  h32     = int(sha256(f"{placebo_seed}|{signal_id}|{attempt}").hexdigest()[:8], 16)
  offset  = (20 + h32 % 41) · (+1 if (h32 >> 31) & 1 else −1)      # ±[20, 60] bars
  ```

  Offsets are derived per signal from the seed and the signal id — never from
  a shared sequential RNG stream — so one extra or missing signal cannot
  re-roll every other signal's draw. A draw is **re-drawn** (attempt += 1) if
  the displaced window `[d_entry', d_exit']` overlaps the `[entry, entry + 20
  bars]` window of **any event stored in the repository** whose signal-bearing
  assets include this asset, or if it falls outside the asset's available
  bars. After 8 failed attempts the signal is excluded with
  `placebo_no_clean_window`. Everything else is identical to a real run.
  Backtest runs are persisted and never mutate signals.

- **FR-11 Watchlist.** Add/remove/list asset ids; unknown ids rejected with
  the nearest-alias suggestion.
- **FR-12 API.** FastAPI app per the endpoint sketch below; thin —
  validation, service calls, serialization only.
- **FR-13 CLI.** Typer app per the command sketch below; same services as
  the API; human-readable tables, `--json` escape hatch on list/show
  commands.
- **FR-14 Determinism & replay equivalence.** The derived state is a pure
  function of (stored in-window articles, committed datasets, `as_of`); all
  ids are content-derived (DATA_MODEL.md). Time is always an input (`as_of`,
  `published_at`, `observed_at`); the only randomness is the placebo seed,
  and it is hash-derived per signal. Guarantees, both asserted by evals:
  - **D0 (determinism):** two runs of the same ingest partitioning over the
    same articles produce byte-identical canonical exports of every entity.
  - **D1 (replay equivalence):** for the same *final* article set, datasets
    and `as_of`, **all derived state — clusters, events, links, and the
    scored tuple of every signal key — is identical regardless of how the
    articles were partitioned across ingest runs.** Only the revision
    *history* differs (an incremental replay accumulates revisions a single
    batch would not), so signals may differ in `id`, `signal_key`,
    `revision`, `supersedes`, `observed_at`, and `created_as_of`; their
    latest `(direction, magnitude, horizon_bars, confidence, score,
    prior_key)` per (event_type, asset_id, role) must match exactly.

  Tests and evals use `FixtureNewsFeed` + `FixtureMarketData` exclusively; a
  hermeticity test asserts the live adapter modules and `feedparser` are
  absent from `sys.modules` after a full eval run.

- **FR-15 Ingest-run semantics, signal revisions & supersession.** This is
  how the product behaves under *daily incremental* use.

  **Active window.** Each `ingest` run: (1) appends new articles
  (idempotent per FR-1); (2) **recomputes** clustering, extraction, linking,
  and scoring over the *active window* — all stored articles with
  `published_at ≥ as_of − active_window_days` (default 30,
  `NEWSALPHA_ACTIVE_WINDOW_DAYS`) — not only the new batch; (3) writes the
  results. Because the clustering candidate span is ±48 h ≪ 30 days, a new
  article's potential cluster-mates are always inside the window.

  **Derived vs durable rows.** `cluster`, `event`, `event_link` are
  **derived**: the recompute deletes and re-inserts them for the active
  window. This removes the contradiction between append-only events and
  late-arriving articles — events are not history, they are a deterministic
  view of the articles, which *are* history. `article`, `signal`, `brief`,
  `price_bar`, `backtest_run/result`, `watchlist_item` are **durable and
  append-only**.

  **Signal keys and revisions.** A signal's stable identity is
  `signal_key = hex16("sigkey|" + event_type + "|" + asset_id + "|" + role +
  "|" + event_date)` where `event_date` is the UTC date of the earliest
  article in the event's cluster. During recompute, for each key:
  - if no signal exists → insert `revision = 1`;
  - else if the scored tuple `(direction, magnitude, horizon_bars,
    round(confidence, 4), prior_key)` equals the latest revision's → **no-op**
    (this is what makes re-running `ingest` idempotent, US-1);
  - else → insert `revision = r + 1` with `supersedes = <previous id>`.

  Signals are never updated or deleted. Each revision stores an
  `event_snapshot` (event_type, stage, attributes, corroboration, best_tier,
  extraction_confidence, link_confidence, evidence article ids, event_date)
  so it stays auditable after the derived event row is re-derived, and an
  `observed_at` = max `published_at` over the evidence articles behind that
  revision. The backtest anchors entry on `observed_at` (FR-10), so a
  revision is only ever evaluated from after the evidence that produced it —
  corroboration raising confidence cannot leak information backwards.
  "Latest revision" is `max(revision)` per key; nothing is mutated to mark it.

  **Key continuity.** If a recompute produces a key `K2` sharing
  (event_type, asset_id, role) with an existing key `K1` and
  `|event_date(K2) − event_date(K1)| ≤ 2 days`, `K2` is aliased to `K1`
  (earlier-created key wins, recorded in `signal_key_alias`) and its values
  are written as a revision of `K1`. This absorbs the case where a
  late-arriving earlier-dated article moves a cluster's earliest publication
  by a day, instead of emitting a duplicate signal.

  **Supersession (cross-cluster lifecycle).** A signal `S2` supersedes `S1`
  when they share (event_type, asset_id, role), `S2.stage ≠ S1.stage`, and
  `2 days < event_date(S2) − event_date(S1) ≤ supersession_days` (21, per
  event type in `priors.json`). The Monday rumor and the Wednesday denial do
  not share a cluster (they fail the FR-2 similarity test), so this rule —
  not clustering — is what makes a denial demote its own rumor. The
  superseding relation is recorded on the newer signal
  (`supersedes_key = <older signal_key>`); FR-8 hides superseded signals by
  default and annotates the superseding one, so a stale bullish rumor can
  never outrank its own denial in the digest.

### Event typology (FR-4/5/6, the committed taxonomy)

Bands below are **post-entry** expected abnormal returns (D-6) — what the
product's own entry rule can capture — not announcement-window CARs. The
announcement CARs from the literature live in `priors.json.announcement_ar_*`
and are rendered into briefs as context only.

| Type | Trigger examples | Typed attributes | Signal roles | Kind | Prior (confirmed): direction / magnitude / horizon / mid post-entry AR |
|---|---|---|---|---|---|
| `earnings_surprise` | "beat estimates", "missed expectations", "EPS of $X vs $Y expected" | `polarity` beat\|miss, `surprise_pct?` | subject | * | beat: bullish/moderate/20 bars/+2.0 % (PEAD drift); miss: bearish/moderate/20/−2.0 % |
| `guidance_change` | "raised full-year guidance", "cut outlook", "withdrew forecast" | `polarity` raise\|cut\|withdraw | subject | * | raise: bullish/moderate/5/+1.5 %; cut: bearish/moderate/5/−2.0 %; withdraw: bearish/moderate/5/−2.5 % |
| `mna` | "agreed to acquire", "to be acquired by", "merger", "takeover bid" | `stage` rumored\|confirmed\|denied, `premium_pct?`, `deal_value_usd?` | acquirer, target | * | target: bullish/moderate/20/+2.0 % (residual deal spread); acquirer: bearish/minor/5/−0.6 %. Stage overrides — target rumored: bullish/moderate/5/+3.0 %; target denied: bearish/moderate/5/−3.0 %; acquirer denied: **unclear ⇒ no signal** |
| `regulatory_action` | "SEC sued", "fined", "approved", "banned", "investigation" | `polarity` favorable\|adverse, `agency?`, `amount_usd?` | subject | equity / crypto | adverse: bearish/moderate/5/−1.5 % (eq), −3.0 % (cx); favorable: bullish/moderate/5/+1.2 % (eq), +2.5 % (cx) |
| `listing` | "to list", "listed on", "available on Coinbase/Binance" | `venue` | subject (+ venue link, non-signal) | equity / crypto | crypto: bullish/major/5/+4.5 %; equity (index add): bullish/minor/5/+0.5 % |
| `delisting` | "delisted", "to remove", "removal from" | `venue` | subject (+ venue link) | equity / crypto | crypto: bearish/major/5/−5.0 %; equity: bearish/moderate/5/−2.0 % |
| `hack_exploit` | "exploited", "bridge hack", "$X drained", "funds stolen" | `amount_usd?`, `vector?` | subject | equity / crypto | crypto: bearish/major/5/−4.5 %; equity: bearish/moderate/5/−1.25 % |

Articles matching no pattern are archived event-less — "no event" is a
correct and common outcome, not a failure.

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision). Endpoints are
   shaped so a later dashboard is a pure client.
2. **No trade execution, brokerage/exchange integration, position tracking,
   or sizing advice — ever.** Permanent product boundary, not a deferral:
   the tool ranks things to look into; it does not touch money.
3. **No LLM anywhere.** Extraction is a deterministic pattern engine; briefs
   are templated. `extract.py` and `brief.py` are the seams where an
   LLM-assisted implementation could later slot in behind the same Event and
   Brief schemas; it is out of scope this pass (see "How the owner's idea is
   interpreted") and would still have to pass the same eval gates.
4. **No general sentiment scoring.** Event-typed extraction only; tone
   scoring without an event type is exactly the noise this product exists to
   filter (Loughran & McDonald 2011 show why generic sentiment fails on
   finance text).
5. **No intraday data.** Daily bars; horizons in bars. Consequence stated
   honestly: most of an announcement move happens before the next open, so
   the priors' *scored* bands are post-entry drift only (D-6), and the
   backtest measures only what a daily-cadence user could see from the next
   bar onward.
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
11. **No `partnership` event type.** Cut during scoping on domain grounds,
    not only budget: the alliance-announcement literature puts the effect at
    ≈ +0.6 % *at announcement* (Chan, Kensinger, Keown & Martin 1997, *JFE*),
    which leaves a post-entry drift indistinguishable from noise under this
    product's entry rule — it would ship a type whose signals the backtest
    can never validate. Its patterns/priors/templates are out; partnership
    language simply produces no event.
12. **No re-analysis of articles older than the active window** (default 30
    days). Such articles are archived and flagged, never clustered or scored
    (FR-1/FR-15).

## Architecture

```
projects/newsalpha/
  src/newsalpha/
    models.py          # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/
      normalize.py     # FR-1: HTML strip, NFKC, hashing, sentence segmentation
      cluster.py       # FR-2: shingling, Jaccard, union-find, corroboration
      extract.py       # FR-4: pattern engine, suppressors, stages, attributes, merge
      link.py          # FR-5: gazetteer scan, all-caps guard, venue precedence, roles
      score.py         # FR-6: prior resolution, stage overrides, modifier formula
      revise.py        # FR-15: signal keys, key continuity, revisions, supersession
      brief.py         # FR-7: template rendering
      frame.py         # FR-7: forbidden-lexicon + required-section frame check
      digest.py        # FR-8: window filter, supersession filter, ranking
      backtest.py      # FR-10: entry/exit resolution, AR, hits, IC, buckets, placebo
      pipeline.py      # FR-14/15: active-window recompute (articles, datasets, as_of)
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
  data/                # assets.json, patterns.json, priors.json, templates.json,
                       # source_tiers.json, benchmarks.json
  evals/               # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, evals/tests) | Live (env-gated, extra `live`) |
|---|---|---|
| `NewsFeed.fetch(since: datetime, until: datetime) -> list[RawArticle]` | `FixtureNewsFeed(path)` — reads a committed JSONL corpus; deterministic order (published_at, external_id) | `RSSNewsFeed` — feedparser over the comma-separated URLs in `NEWSALPHA_FEEDS`; entry link/guid → external_id, entry summary/content → body; activates only when the env var is set |
| `MarketData.daily_bars(asset_id: str, start: date, end: date) -> list[Bar]` | `FixtureMarketData(dir)` — committed per-asset CSVs incl. `idx:US`, `idx:CX` | `LiveMarketData` — routes by asset kind: Stooq free CSV endpoint for equities (no key), CoinGecko `/market_chart` for crypto (`NEWSALPHA_COINGECKO_KEY` optional, raises limits); `idx:CX` is synthesized from the `data/benchmarks.json` basket (FR-9); activates only when `NEWSALPHA_LIVE=1` |

The store is the third seam per conventions: `Repository` protocol with
`SQLiteRepository` (default `~/.newsalpha/newsalpha.db`, path via
`NEWSALPHA_DB`) and `InMemoryRepository` for tests.

### API sketch (FastAPI)

```
GET  /health
POST /ingest                    # {as_of, since?, feed?: fixture|rss, path?} → counts {articles, clusters, events, signals_new, revisions_new, briefs}
GET  /articles?since=&domain=&limit=       GET /articles/{id}
GET  /events?type=&asset=&stage=&since=    GET /events/{id}    # incl. evidence spans + links
GET  /signals?asset=&min_confidence=&direction=&since=&include_superseded=false
GET  /signals/{id}                          GET /signals/{id}/revisions   # full revision chain by signal_key
GET  /briefs/{signal_id}
GET  /digest?date=&watchlist_only=true&include_superseded=false
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
newsalpha signals list [--asset --min-confidence --direction --include-superseded] | show <id>
newsalpha signals revisions <signal-id>             # the full revision chain for its key
newsalpha brief <signal-id>
newsalpha digest [--date D] [--all-assets] [--include-superseded]
newsalpha assets list [--kind --query] | show <asset-id>
newsalpha watch add|remove <asset-id> | list
newsalpha prices load [--source fixture|live] [--start --end]
newsalpha backtest run [--start --end --min-confidence] | placebo [--seed S] | show <run-id>
```

## Key design decisions & assumptions

1. **Deterministic pattern extraction, not ML/LLM.** Cascaded finite-state
   information extraction in the FASTUS lineage (Hobbs et al. 1997) filling
   MUC-style event templates. Rationale: inspectable evidence spans on every
   claim, hermetic evals, and a 2–4k-line budget. The Loughran & McDonald
   (2011, *J. Finance*) result — general-purpose word lists misclassify
   financial text — is why every lexicon here is finance-specific committed
   data. Anti-overfit guard: each pattern must cite the real-world headline
   phrasing it generalizes from (FR-3), and the gated extraction metrics run
   on a paraphrase family the patterns were not developed against (EVALS.md).
2. **Event typology and enrichment mirror commercial news analytics.**
   RavenPack and Thomson Reuters News Analytics ship typed event categories
   with relevance, novelty, and source-rank fields; our cluster-based
   novelty (one event per cluster/type), corroboration count, and source
   tiers are the open, inspectable version of ENS/relevance/source-rank.
3. **Precision over recall in linking, and abstention over guessing.** A
   missed link costs one opportunity; a false link produces a confidently
   wrong brief. Hence: `mentioned` and `venue` never signal; ambiguous
   aliases require same-sentence context; plain tickers must be ALL-CAPS
   standalone tokens *except* in all-caps headlines where that evidence class
   is disabled entirely; unresolvable M&A roles and ambiguous listing subjects
   emit **no signal** rather than a guess. Ticker/common-word collision
   (NEAR, ONE, ICP, APE, COIN, Meta, Oracle, Shell) and venue/asset collision
   (Coinbase = `eq:COIN`, Nasdaq = `eq:NDAQ`) are known failure modes of
   financial NER and get dedicated fixture traps and the M2b/M3 gates.
4. **Scoring priors are literature-grounded data, split into two bands.**
   Each `priors.json` row carries `announcement_ar_lo/hi` (the literature's
   announcement-window CAR — rendered verbatim into briefs, never scored) and
   `expected_ar_lo/hi` (**post-entry** drift, the only band the engine
   scores), plus `rationale` + `source_note`. Grounding: earnings surprise →
   post-earnings announcement drift (Ball & Brown 1968; Bernard & Thomas
   1989) justifies both the 20-bar horizon and a post-entry band far smaller
   than the announcement jump; M&A → target announcement CARs of roughly
   +16–24 % and acquirer CARs ≈ 0 to −1 % (Andrade, Mitchell & Stafford 2001,
   *JEP*) justify the asymmetric roles, while the *post-entry* target band is
   the residual deal spread to completion, not the jump; guidance → price
   reaction to management forecasts (Skinner 1994; Anilowski, Feng & Skinner
   2007); regulatory enforcement → large reputational penalties beyond fines
   (Karpoff, Lee & Martin 2008, *JFQA*); crypto exchange listing → the
   documented "Coinbase effect" of double-digit announcement returns with
   measurable multi-day continuation (Messari 2021; Binance-listing studies);
   hacks → sharp multi-day drawdowns of exploited protocols' tokens
   (Ronin/Axie 2022 as exemplar). Priors are editable data; the engine never
   hard-codes an expected return.
5. **Market-adjusted abnormal returns, not the market model, with date-aligned
   benchmarks.** Brown & Warner (1985) show market-adjusted returns have
   power comparable to beta-adjusted models in daily event studies, and beta
   estimation on short histories is noise. Benchmarks per asset kind:
   `idx:US` (equities), `idx:CX` (crypto). The benchmark leg is read at the
   asset's own entry/exit *calendar dates*, so a gap in either series
   excludes the signal instead of silently comparing different weeks. The
   live `idx:CX` proxy is an 8-asset basket (FR-9), not BTC, because BTC is
   itself a universe asset and would give `AR ≡ 0`; `AR == 0` is in any case
   an explicit exclusion, never a coin-flip miss.
6. **Strictly-after entry, and priors scoped to what that entry can capture.**
   Entry = first available bar with date > `observed_at`; the harness asserts
   it. This kills look-ahead bias by construction and forces an honest
   consequence: the scored band must be the *post-entry* drift, not the
   announcement CAR. Scoring the announcement CAR would have made the digest
   ranking structurally dominated by M&A and hacks whose headline moves the
   user can never realize, and would have let the eval suite certify
   magnitudes that live data would immediately falsify. The fixture generator
   plants the announcement jump on the publication-date bar — which a correct
   harness must never touch — so the same design choice doubles as the
   strongest leak canary in the suite (EVALS.md).
7. **Placebo runs are a first-class feature, not just an eval.** Randomized
   event dates are the standard robustness check in the event-study tradition
   (Brown & Warner's simulation methodology). The re-draw rule is stated
   purely in product terms — avoid the windows of *events stored in the
   repository* — so `backtest placebo` works identically on live data; the
   eval instantiation additionally verifies displacement against the fixture's
   planted-effect table. Offsets are hash-derived per signal so the placebo
   realization is stable under small implementation differences.
8. **Confidence means P(direction correct), is capped at 0.95, and counts
   stage exactly once.** The modifier formula (tier, stage, corroboration,
   extraction, link) is multiplicative and committed; stage affects
   confidence only through `f_stage` and direction/band only through
   `stage_overrides`, so the two channels cannot double-count. Calibration is
   measured with fixed-edge buckets and gated (M6, with a bucket-occupancy
   sub-gate) in the reliability-diagram tradition (Murphy 1973; Brier 1950).
9. **Framing is enforced, not requested.** The frame check (forbidden
   imperative lexicon, required sections, verbatim not-advice footer) runs on
   every brief before persistence and raises on failure. Matching is
   word-boundary, so "buyout"/"sell-off"/"buyer" pass while bare imperatives
   fail. The eval grader embeds its own copy of the lexicon rather than
   loading `data/patterns.json`, so weakening the shipped lexicon cannot
   weaken the grader.
10. **Rumor / confirmed / denied is a stage, not a filter — and stages
    supersede.** Rumors are tradable information with lower base rates —
    merger-arb practice prices deal risk, so `rumored` halves confidence
    rather than suppressing, and `denied` M&A emits a bearish target signal
    (unwind of the rumor premium) and no acquirer signal. Because the
    lifecycle spans clusters, FR-15's supersession rule — not clustering —
    demotes a stale rumor once its denial arrives.
11. **Calendars: equities trade weekdays, crypto every day; no holiday
    table.** All engine rules are defined over *available bars*, so a
    real-world holiday shifts entry by a day rather than breaking anything;
    the weekday rule is only load-bearing inside the fixture generator, and
    one deliberately-gapped fixture series pins the gap behaviour.
    Documented simplification, revisit if live use shows drift.
12. **Content-derived ids everywhere** (sha256-prefix of natural keys, see
    DATA_MODEL.md). Cluster identity is derived from *final membership*
    (which is order-independent by FR-2's transitive closure), not from
    arrival order, which is what makes FR-14's replay equivalence hold.
13. **Dedup via shingling** (Broder 1997) with a fully pinned tokenization,
    so the fixture's committed similarity margins (intra-cluster ≥ 0.72,
    inter-cluster ≤ 0.45) are meaningful for any faithful implementation.
    Exact Jaccard is fine at personal-tool scale; MinHash is deliberately
    omitted.
14. **Briefs are templates + quoted evidence, never generation.**
    Deterministic, hermetic, and every sentence traceable to either a
    committed template, a committed prior rationale, or a quoted span of the
    source article.
15. **The fixture corpus is synthetic-but-realistic, fully annotated by
    construction, and split into two disjoint paraphrase families** so the
    gated extraction/linking metrics score phrasing the patterns were not
    written against (EVALS.md). Real scraped articles are avoided for
    licensing and determinism.
16. **Signals are versioned, never mutated; analysis is append-only.** A
    re-score appends a revision rather than rewriting one, so the backtest
    still evaluates what the system said at the time it said it, while the
    digest can show the current view. Derived rows (clusters/events/links)
    are recomputed rather than versioned, because they are a pure view of
    append-only articles and every signal carries its own event snapshot.
17. **Assumption: single user, single process.** SQLite with no concurrent
    writers; API and CLI share one DB file; no auth on the API (binds
    localhost by default). The active-window recompute is a full pass over
    ≤ ~1,000 articles — milliseconds at this scale.
18. **Assumption: sizing.** Estimated `src/newsalpha/` 2,900–3,300 lines and
    `evals/` (generators, metrics, run, gates) 700–850 lines — 3,600–4,150
    total, the upper half of the 2–4k mandate. `tests/` (~800 lines) and
    committed JSON/CSV data are excluded from the count per convention, but
    the **data and fixture build is the largest single work item** (~900
    lines of curated JSON across the gazetteer, patterns, priors and
    templates, plus two paraphrase template families and three seeded market
    series) and is therefore given its own scope valves. Valves, in order:
    - **V1** shrink the gazetteer to ~80 equities + ~30 crypto;
    - **V2** drop `delisting` (folds into `listing` with a polarity attribute);
    - **V3** cut the market fixture from 3 seeds to 1, re-deriving and
      recording the widened M4/M6/M7 thresholds in the same commit;
    - **V4** shrink per-type truth event counts proportionally (floor: 8 per
      type in the gated VAL family), re-deriving every baseline in the same
      commit per EVALS.md's re-derivation rule.

    Not valves — they are the product: extraction/linking/role logic, the
    strictly-after entry rule, the placebo mode, the frame check, the
    DEV/VAL paraphrase split.
19. **Assumption: live feeds are RSS/Atom with usable timestamps.** Feeds
    lacking `published` fall back to fetch time (recorded as such, flagged
    `published_at_estimated` — such articles still extract, but their signals
    are excluded from backtests with `estimated_publish_time`, since entry
    timing would be untrustworthy).
