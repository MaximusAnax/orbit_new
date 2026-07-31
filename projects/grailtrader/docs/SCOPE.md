# GrailTrader — Scope

## One-liner

A single-user advisor that treats designer clothing like a tradable asset
class: it builds per-brand / designer-era / category price indices from
secondhand-marketplace sold listings, ingests typed fashion events (designer
departure or appointment, collab announcement, celebrity co-sign, runway
reception, brand scandal), maps each event to an expected index impact that
decays over time, and issues buy / sell / hold advice per portfolio garment —
with confidence, plain-language rationale, and a leak-free backtest that
proves (or disproves) its own directional skill against naive baselines.

## Problem statement

The secondhand designer market behaves like a thinly traded asset market:
prices for "archive" pieces rise and fall on news. When Phoebe Philo's
departure from Céline was announced (December 2017), resale demand and prices
for Philo-era pieces surged — The RealReal's resale reporting documented the
"Old Céline" effect. Slimane-era Dior Homme and founder-era Helmut Lang carry
durable archive premiums precisely because those eras are closed. After
Virgil Abloh's death (November 2021), StockX resale prices for Off-White and
his Louis Vuitton collabs spiked within days and then decayed over weeks. The
Balenciaga campaign scandal (November 2022) and the adidas–Yeezy split
(October 2022) moved resale demand the other way. The owner wants to trade
this: track a garment portfolio, watch the news, and get told when a piece is
worth buying more of, selling into a spike, or holding.

Doing this honestly has two hard parts:

- **A. A trustworthy price index from hostile data.** Marketplace listings
  are heterogeneous (no two grails are identical), sparse (a stratum may see
  a handful of sales a week or none), polluted (fakes listed far below
  market, mislabeled items far above, aspirational asking prices that never
  sell), and condition-confounded (a "very worn" price is not comparable to
  "new with tags"). A naive average of listing prices produces an index that
  is mostly noise and outliers; every downstream signal inherits that
  garbage. The index layer must recover the true level of each
  (brand, era, category) stratum from this data, measurably.
- **B. An event-impact advisor whose skill is measured, not asserted.**
  Typed events must map to index adjustments with the right direction,
  rough magnitude, persistence split (how much of the move is permanent
  archive re-rating vs transient hype), and decay — and the buy/sell/hold
  decisions derived from them must beat naive baselines on directional
  accuracy in a backtest that is provably leak-free (advice at week *t* uses
  only data through week *t*; a placebo run with scrambled event dates must
  show no skill). Confidence must be calibrated: high-confidence calls must
  hit more often than low-confidence ones, measurably.

Both get first-class eval gates (EVALS.md). Per the workspace rule that
finance safeguards are implemented behavior: every advice rendering carries
confidence, its driving events, a fee/illiquidity note, and a
collectibles-not-securities footer, enforced by a frame check that blocks
persistence on failure (FR-9, gate M5 = 1.0). This market is unregulated,
illiquid, and authenticity-risky; the product says so on every output.

## Target user

The owner: one person who collects archive/designer pieces (think Helmut
Lang, Raf Simons, Margiela, Rick Owens, Philo-era Céline), follows fashion
news casually, and wants (1) a mark-to-market view of what the closet is
worth, and (2) a nudge when news makes a piece worth acting on. Single local
profile; no accounts, no multi-tenancy, no marketplace transactions — the
tool never buys or sells anything. Weekly cadence: indices and advice are
computed at ISO-week grain; this is a research tool, not a sniping bot.

## User stories & acceptance criteria

**US-1 — My closet, marked to market.** As the user, I register garments
(brand, designer era, category, condition, size, acquisition price and date)
and see each one's current fair value and unrealized gain/loss.
*Accept:* `portfolio add/list/show` work; fair value =
`acquisition_price × I_s(t) / I_s(t_acq)` on the most specific stratum index
available at both dates (FR-7); when no such index exists the valuation is
explicitly "unavailable: <reason>", never a guess; unknown brand/era/category
values are rejected at entry with the nearest-match suggestion.

**US-2 — Indices I can inspect.** As the user, I load marketplace listings
and build weekly price indices per (brand, era, category) stratum, and I can
see for any week the level, sample size, and what was excluded as an outlier.
*Accept:* `listings load && index build` runs end-to-end on the committed
fixture scenario with zero configuration; every index point stores
`n_sales` and `n_excluded`; sold prices only enter the index (asking prices
never do); rebuilding is deterministic and idempotent; index fidelity meets
the M1 gates on the fixture scenario.

**US-3 — Events in, impacts visible.** As the user, I ingest typed fashion
events from the fixture feeds or add one manually
(`events add --type designer_departure --brand celine --era philo ...`), and
I can see which strata each event touches and its modeled impact path
(permanent + transient components, half-life).
*Accept:* all six event types round-trip through `events add/list/show`;
scope resolution follows the FR-5 table exactly; `events show` prints the
resolved target strata and the prior row applied (with its source note);
re-ingesting the same feed is a no-op.

**US-4 — Advice I can interrogate.** As the user, I run `advise` and get,
per portfolio garment: buy / sell / hold, the chosen horizon, expected move,
confidence, and a rationale naming every driving event and every modifier
applied.
*Accept:* every advice stores machine-readable rationale codes plus rendered
text; the action follows the FR-8 decision rule exactly (thresholds and
formulas are data, not code constants); a garment on a stale index
(staleness > 8 weeks) always gets `hold` with `hold:stale_index`; a quiet
market (no active events) always gets `hold` with `hold:no_active_events` —
"do nothing" is a first-class answer.

**US-5 — Prove it works (or doesn't).** As the user, I can run a backtest
over the fixture historical scenario and see directional hit rate, the
buy-minus-sell realized spread, and per-confidence-bucket hit rates — and a
placebo run with scrambled event dates that shows no skill.
*Accept:* the backtest replays week by week using only strictly-prior data;
entry is the week after the advice week (asserted invariant); M2 (hit rate ≥
0.70, spread ≥ 8 pts), M3 (calibration separation ≥ 0.12), and M4 (placebo
|hit − 0.5| ≤ 0.06) all gate; excluded decisions are counted and reasoned,
never silently dropped.

**US-6 — Safeguards I can't turn off.** As the user, every advice rendering
tells me its confidence, its assumptions (fees, illiquidity, authenticity
risk), and that this is a collectibles market, not investment advice.
*Accept:* the frame check (forbidden certainty lexicon, required sections,
verbatim footer) runs before persistence and raises on failure — a
non-compliant advice cannot exist in the store (CHECK constraint); gate
M5 = 1.0 including adversarial cases where user-supplied garment titles
contain forbidden words (allowed only inside quoted context).

**US-7 — Go live when I'm ready.** As the user, I can import my own sold
comps from CSV, pull active eBay listings, and turn fashion-news RSS into
*candidate* events that I confirm before they affect anything.
*Accept:* `CsvListingsFeed` maps documented columns; `EbayListingsFeed`
activates only when `GRAILTRADER_EBAY_CLIENT_ID/SECRET` are set and imports
ask-only listings (which never enter the index — diagnostics only);
`RssNewsFeed` activates only when `GRAILTRADER_NEWS_FEEDS` is set and queues
keyword-matched candidate events as `pending` for `events review` — a
pending event never influences impacts or advice; live adapters are never
imported on the test/eval path.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-4 is
hard part A; FR-6/8/10 are hard part B.

- **FR-1 Reference data & init.** `grailtrader init` loads and validates the
  committed datasets (`data/brands.json`, `impact_priors.json`,
  `conditions.json`, `advice_templates.json`, `advisor_config.json`):
  brand ids unique; era ids unique within brand, eras non-overlapping in
  time with at most one open era (end = null) per brand; every
  (event_type × attribute combination reachable per the FR-5 table) resolves
  to exactly one prior row; every prior has non-empty `rationale` and
  `source_note`; condition multipliers strictly decreasing from `new` to
  `poor`; every advice template contains all required sections and zero
  forbidden-lexicon words; config values within documented ranges.
  Validation failure aborts init naming the offending record.
- **FR-2 Listings ingestion & normalization.** The `ListingsFeed` adapter
  yields `RawListing`s (source platform, external id, brand ref, era ref,
  category, platform condition label, listed/sold timestamps, ask price,
  sold price, currency). Ingestion maps platform condition labels to the
  canonical five-grade scale via the committed alias table
  (`conditions.json`; unmapped labels are rejected with the label named),
  resolves brand/era against the gazetteer (unresolvable rows are skipped
  and counted in the ingest report — never guessed), requires USD, and is
  idempotent: listing ids are content-derived, so re-ingesting a feed
  changes nothing. Listings are append-only. `status = sold` requires
  `sold_price` and `sold_at ≥ listed_at`; all prices > 0.
- **FR-3 Outlier fence.** Within each index window (FR-4), a sold listing is
  excluded iff `|log(p_adj) − median_w| > max(3.5 · σ̂_w, ln 2.2)` where
  `p_adj` is the condition-adjusted price, `median_w` the window median of
  log adjusted prices, and `σ̂_w = 1.4826 · MAD` of log adjusted prices in
  the window. The MAD term adapts to genuinely volatile strata; the
  `ln 2.2` floor keeps the fence meaningful in small windows. Exclusions are
  counted per index point (`n_excluded`) and inspectable
  (`index show --excluded`). This is the fake/mislabel defense: a
  counterfeit listed at 0.2× or a mislabeled grail at 3× falls outside the
  fence; ordinary composition noise stays inside (gate M1b).
- **FR-4 Index construction (hard part A).** For each leaf stratum
  s = (brand, era, category) and ISO week t:
  `level_usd(s, t) = median of condition-adjusted sold prices`
  (`p_adj = sold_price / condition_multiplier[grade]`, i.e. every sale is
  restated as an excellent-condition-equivalent price) over the trailing
  4-week window (weeks t−3…t), after the FR-3 fence, requiring ≥ 5
  surviving sales; otherwise no index point is written for that week
  (staleness is computed at query time as the distance to the last written
  point — never fabricated). `index(s, t) = 100 · level_usd(s, t) /
  level_usd(s, t_base)` where t_base is the stratum's first eligible week.
  Parent strata (brand, era) and (brand) are sample-count-weighted
  geometric means of their children's index values (weights = trailing
  8-week sale counts; children enter at the parent's base week, carried to
  nearest earlier value when needed). Index build is a pure function of
  (listings, as_of, config) and rebuild replaces derived rows idempotently.
- **FR-5 Event ingestion & scope resolution.** The `NewsFeed` and
  `SocialFeed` adapters emit typed `FashionEvent`s (six types, attributes
  per the typology table below). Events are append-only with
  content-derived ids (idempotent re-ingest); `occurred_on` is
  caller-supplied (the engine never reads the clock). Manual entry
  (`events add`) is a first-class source. Live RSS produces
  `status = pending` candidates that require `events review --confirm`
  before entering the active set; fixture and manual events are `confirmed`
  on arrival. Scope resolution maps each confirmed event to target stratum
  prefixes per the typology table (e.g. a departure targets `brand/era`; a
  scandal targets `brand`); an event applies to a garment iff one of its
  target prefixes is a prefix of the garment's stratum path.
- **FR-6 Impact model (hard part B).** Each confirmed event, via its prior
  row (`impact_priors.json`, keyed by event type + attributes), defines a
  cumulative log-impact path at age `a` weeks:
  `m_e(a) = ln(1 + P_e + T_e · 0.5^(a / h_e))` for a ≥ 0, where `P_e` is
  the permanent component (signed fraction), `T_e` the transient component
  (signed), and `h_e` the transient half-life in weeks — the
  permanent-plus-decaying-transient form of advertising adstock (Broadbent
  1979) applied to price level instead of awareness. Overlapping events on
  the same stratum combine log-additively. Celebrity co-sign transients
  scale by tier (`a_list` 1.0, `b_list` 0.5, `niche` 0.25). An event is
  *active* while `age ≤ 26 weeks` or `|m_e(age)| > 0.005`. All prior values
  are editable data with rationale + source notes; the engine never
  hard-codes an expected move.
- **FR-7 Garment valuation.** `fair_value(g, t) = acquisition_price ·
  I_s(t) / I_s(t_acq)` where s is the most specific ancestor stratum of g's
  path with an index point at (or carried ≤ 8 weeks to) both t_acq and t —
  single-asset repeat-sales logic: the garment's own acquisition price is
  the anchor and the stratum index supplies the appreciation path
  (Bailey–Muth–Nourse 1963 / Case–Shiller 1989, applied in reverse to mark
  one asset to market). Condition is assumed constant while held; a user
  condition edit rescales fair value by the multiplier ratio. No qualifying
  stratum → valuation unavailable with a named reason. `portfolio value`
  also reports the leaf stratum's `level_usd` ("typical
  excellent-condition price") for context.
- **FR-8 Advisor (hard part B).** At week t, for each portfolio garment g
  with usable stratum s (staleness ≤ 8 weeks; else `hold:stale_index`):
  - Baseline `B` = observed index of s at the last week strictly before the
    earliest active event's week (no active events → `hold:no_active_events`;
    no pre-event observation → `hold:no_baseline`).
  - Expected level at horizon H: `ln Î(t+H) = ln B + Σ_e m_e(t + H − t_e)`.
  - Expected forward return `r̂_H = Î(t+H) / O_t − 1`, where `O_t` is the
    current observed index of s. Because sellers reprice slowly (posted-price
    market friction — resale listings sit until edited), `O_t` lags the
    modeled path right after an event; the gap *is* the trade.
  - Signal-to-noise per horizon: `z_H = |ln(1 + r̂_H)| / (σ_w · √H)`, with
    `σ_w = 1.4826 · median|Δ ln I_s|` over the trailing 52 non-stale weekly
    changes (≥ 12 required, else the q_index fallback below applies).
  - Horizon selection: H* = argmax over H ∈ {4, 12, 26} of z_H.
  - Confidence: `conf = clamp((1 − 0.5^(z_H*/0.8)) · q_index · c_event,
    0.05, 0.95)` where `q_index` = 1.0 (staleness 0), 0.8 (1–2 wk), 0.5
    (3–8 wk), and `c_event = Σ_e w_e · base_conf_e · s_e / Σ_e w_e` with
    `w_e = |m_e(t + H* − t_e)|` and source factor `s_e` = 1.0 manual /
    0.95 news / 0.8 social, times a corroboration bump
    `min(1.0, s_e · (1 + 0.05 · min(corroboration − 1, 2)))`.
  - Action: **buy** if `r̂_H* ≥ +0.10` and `conf ≥ 0.55`; **sell** if
    `r̂_H* ≤ −0.10` and `conf ≥ 0.55`; else **hold** with a reason code.
    Thresholds live in `advisor_config.json`; the ±10 % default is sized to
    round-trip friction on real platforms (Grailed ~9 % + payment
    processing, StockX ~12 %, Vestiaire Collective ~15 %, The RealReal
    consignment tiers higher) — advice below friction is noise.
  - Every advice stores rationale codes (`driver:event:<id>`,
    `mod:conf_event=0.70`, `mod:q_index=1.0`, `hold:<reason>`, …), the
    chosen horizon, `r̂`, `conf`, and fair-value context. Advice rows are
    immutable; re-running `advise` for the same (garment, as_of, horizon)
    is a no-op.
- **FR-9 Advice framing safeguard.** Advice text renders only from the
  committed template catalog (`advice_templates.json`) plus event/garment
  fields. The frame check then asserts: zero forbidden-lexicon matches
  ("guaranteed", "can't lose", "sure thing", "risk-free", "will
  definitely", "easy money" — full list committed) outside quoted
  user-supplied titles; all required sections present (action + expected
  move + drivers + confidence + fee/illiquidity note + uncertainty); the
  collectibles-not-advice footer verbatim. Failure raises before
  persistence; the store's CHECK constraint makes a non-checked advice row
  unrepresentable. Gate M5 = 1.0.
- **FR-10 Backtest harness (hard part B).** Replays a scenario week by
  week: at each week t, the advisor runs with listings `sold_at ≤ end of
  week t` and events `occurred_on ≤ end of week t` only (strictly-prior
  data — asserted). Entry is week t+1 ("you act over the following week");
  realized return at the advice's own horizon H*:
  `R = I_ref(t + 1 + H*) / I_ref(t + 1) − 1`, where `I_ref` is the planted
  truth index in eval runs and the recovered index in live runs.
  `hit = (sign(R) == sign(advice))` over actionable (buy/sell) advice.
  Aggregates: N, hit rate, mean realized return per action, buy-minus-sell
  spread, per-confidence-bucket hit rates (< 0.45 / 0.45–0.70 / ≥ 0.70),
  per-driving-event-type breakdown; decisions with insufficient future
  index are excluded with a named reason and counted. Baselines computed in
  the same run: always-hold, 4-week momentum (`buy iff I(t)/I(t−4) − 1 ≥
  +0.10`, mirrored for sell), event-naive (buy for 12 weeks on any
  confirmed event touching the stratum). **Placebo mode:** with a seed,
  every event's week is displaced uniformly ±[26, 52] weeks (redrawn if the
  displaced impact window overlaps the event's true active window),
  everything else identical. Runs are persisted append-only and never
  mutate advice.
- **FR-11 Portfolio CRUD.** Add / edit / remove / list garments with status
  `owned`, `watching`, or `sold_archived` (recording disposal price/date).
  Validation against the gazetteer with nearest-match suggestions.
  Watching garments get the same advice pipeline (buy is actionable; sell
  renders as "avoid / wait").
- **FR-12 API.** FastAPI app per the endpoint sketch below; thin —
  validation, service calls, serialization only.
- **FR-13 CLI.** Typer app per the command sketch below; same services as
  the API; human tables with `--json` escape hatch on list/show commands.
- **FR-14 Determinism & hermeticity.** Engine functions are pure in
  (listings, events, config, as_of); ids are content-derived so replays are
  byte-identical and ingest is idempotent; time is always an input; the
  only randomness is the placebo seed. Tests and evals use
  `FixtureListingsFeed` / `FixtureNewsFeed` / `FixtureSocialFeed` and the
  in-memory store exclusively; live adapters are never imported on that
  path.

### Event typology (FR-5/6, the committed taxonomy)

| Type | Attributes | Target strata | Prior shape (defaults; editable data) |
|---|---|---|---|
| `designer_departure` | `era_id` (the era that closes), `reason ∈ {resignation, ousted, death, house_closure}` | `brand/era` | bullish era re-rating: permanent +12–20 %, transient +10–25 %, h = 6–10 wk by reason (death and closure strongest — supply is permanently finite) |
| `designer_appointment` | `designer`, `acclaim ∈ {acclaimed, neutral, unproven}` | `brand` (current heat) **and** `brand/predecessor-era` (era closes) | brand: acclaimed → permanent +6 %, transient +8 %, h = 8; predecessor era: permanent +6 %, transient +4 %, h = 8 |
| `collab_announcement` | `counterparty` (free text; second gazetteer brand also targeted when resolvable), `fizzled?` (fixture truth only) | `brand` | transient +10 %, permanent +2 %, h = 4 — hype-cycle shaped |
| `celebrity_cosign` | `celebrity`, `tier ∈ {a_list, b_list, niche}`, optional `era_id`, `category` | most specific given (`brand`, `brand/era`, or `brand/era/category`) | transient +10 % · tier scale, permanent 0, h = 3; low base confidence |
| `runway_reception` | `polarity ∈ {acclaimed, panned}` | `brand` | acclaimed: transient +5 %, permanent +2 %, h = 6; panned: transient −4 %, permanent 0, h = 6 |
| `brand_scandal` | `severity ∈ {minor, moderate, severe}` | `brand` | bearish: permanent −5/−10/−15 %, transient −5/−10/−15 %, h = 6/8/10 |

Grounding for the default magnitudes lives in each prior row's
`source_note` (see DATA_MODEL.md): the post-Philo Céline surge (The
RealReal resale reporting, 2018), Off-White/StockX after Abloh's death
(Nov 2021), Supreme × Louis Vuitton (2017) and Nike × Off-White "The Ten"
resale premiums, the Bella Hadid–driven vintage Jean Paul Gaultier revival
(Lyst/Depop search-spike reporting), Lyst Index methodology (brand heat from
search + social moments), Balenciaga campaign scandal demand drop
(StockX/Grailed demand data, Nov 2022), and the adidas–Yeezy split
(Oct 2022). These are directional and order-of-magnitude anchors, not
precise estimates — which is exactly why the advisor's skill is gated by
backtest, not assumed from priors.

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision). Endpoints are
   shaped so a later dashboard is a pure client.
2. **No transactions, ever.** The tool never lists, bids, buys, or sells,
   and never connects to a marketplace account. Permanent boundary.
3. **No authenticity verification.** The outlier fence catches
   *price-implausible* listings; it does not legit-check anything.
   Authentication (Entrupy-style, platform authenticators) is explicitly
   out; the footer names authenticity as a residual risk.
4. **No era inference from garments.** Dating a piece by care tags and
   labels ("tag archaeology", e.g. the well-documented Helmut Lang tag
   generations) is real collector practice but out of scope: era is input
   data supplied by the user or the listing source (D-19).
5. **No NLP event extraction.** Adapters emit *typed* events (locked
   decision). The live RSS adapter does keyword-rule candidate matching
   only, and candidates require human confirmation. An LLM extractor could
   later slot in behind the same `FashionEvent` schema; it would face the
   same eval gates.
6. **No live social-media scraping.** The `SocialFeed` live path is manual
   entry (`events add --source social`); platform APIs (X, Instagram,
   TikTok) are gated, unstable, and against-ToS to scrape. Fixture social
   events model what a future integration would emit.
7. **No sold-price live feed.** No public API exposes secondhand *sold*
   prices reliably (eBay Marketplace Insights is restricted; Grailed,
   Vestiaire, and StockX have no public APIs). Live sold comps arrive via
   CSV import; live eBay Browse API listings are ask-only and used for
   spread diagnostics, never the index. Documented honestly (D-3).
8. **USD only.** No currency conversion; non-USD listings are rejected at
   ingest with a count.
9. **No size-level indices.** Size is stored on garments and listings
   (it matters for liquidity) but is not a stratum dimension — strata would
   become too sparse to index (D-1).
10. **No portfolio-level analytics** (correlation, exposure,
    diversification). Advice is per garment.
11. **No scheduling/daemon mode, no notifications.** The user runs
    `advise` when they want it.

## Architecture

```
projects/grailtrader/
  src/grailtrader/
    models.py            # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/
      conditions.py      # FR-2: condition alias mapping + multipliers
      fence.py           # FR-3: MAD outlier fence
      index.py           # FR-4: stratum resolution, windowed medians, parent geomeans
      events.py          # FR-5: scope resolution, active-event windows
      impact.py          # FR-6: prior lookup, m_e(a) paths, log-additive combination
      valuation.py       # FR-7: repeat-sales-style fair value
      advisor.py         # FR-8: r̂ per horizon, z, confidence, action rule
      frame.py           # FR-9: template render + frame check
      backtest.py        # FR-10: weekly replay, baselines, placebo, aggregates
    adapters/
      listingsfeed.py    # ListingsFeed Protocol
      listings_fixture.py    #   offline: FixtureListingsFeed (JSONL path)
      listings_csv.py        #   live: CsvListingsFeed (documented column map)
      listings_ebay.py       #   live: EbayListingsFeed (Browse API; extra "live")
      newsfeed.py        # NewsFeed Protocol
      news_fixture.py        #   offline: FixtureNewsFeed (JSONL of typed events)
      news_rss.py            #   live: RssNewsFeed (candidate events; extra "live")
      socialfeed.py      # SocialFeed Protocol
      social_fixture.py      #   offline: FixtureSocialFeed (JSONL of typed events)
      social_manual.py       #   live: ManualSocialEntry (thin; used by `events add`)
    store/               # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/                 # FastAPI app
    cli/                 # Typer app
  data/                  # brands.json, impact_priors.json, conditions.json,
                         # advice_templates.json, advisor_config.json
  evals/                 # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default; tests/evals) | Live (env-gated; extra `live`) |
|---|---|---|
| `ListingsFeed.fetch() -> list[RawListing]` | `FixtureListingsFeed(path)` — committed JSONL, deterministic order (sold_at/listed_at, external_id) | `CsvListingsFeed(path)` — user-exported sold comps, documented column map, no network; `EbayListingsFeed` — eBay Browse API (OAuth client-credentials via `GRAILTRADER_EBAY_CLIENT_ID/SECRET`), brand-alias queries, **ask-only** listings flagged so they never enter the index |
| `NewsFeed.fetch(since, until) -> list[FashionEvent]` | `FixtureNewsFeed(path)` — committed JSONL of typed, `confirmed` events | `RssNewsFeed` — feedparser over `GRAILTRADER_NEWS_FEEDS` (e.g. BoF, Vogue Business, Hypebeast, WWD feeds); committed keyword-rule table types candidates; emits `status = pending` events requiring `events review --confirm` |
| `SocialFeed.fetch(since, until) -> list[FashionEvent]` | `FixtureSocialFeed(path)` — committed JSONL (co-signs, runway reception with `source = social`) | `ManualSocialEntry` — programmatic seam used by `events add --source social`; no scraping (non-goal 6) |

The store is the fourth seam: `Repository` protocol with `SQLiteRepository`
(default `~/.grailtrader/grailtrader.db`, path via `GRAILTRADER_DB`) and
`InMemoryRepository` for tests.

### API sketch (FastAPI)

```
GET  /health
POST /listings/load            # {source: fixture|csv|ebay, path?} → {ingested, skipped_unresolved, skipped_currency, duplicates}
GET  /listings?stratum=&status=&limit=          GET /listings/{id}
POST /index/build              # {as_of} → {strata_built, points_written, excluded_total}
GET  /index/{stratum_id}?from=&to=&excluded=    # weekly points incl. n_sales/n_excluded
GET  /strata?level=leaf|era|brand&brand=
POST /events/ingest            # {source: fixture|rss, path?, since?, until?} → counts by status
POST /events                   # manual typed event (validated against typology)
POST /events/{id}/confirm      POST /events/{id}/reject
GET  /events?type=&brand=&status=&since=        GET /events/{id}   # incl. resolved scopes + prior
GET  /brands                   GET /brands/{id}                    # incl. eras
POST /portfolio                GET /portfolio    GET /portfolio/{id}
PATCH /portfolio/{id}          DELETE /portfolio/{id}
GET  /portfolio/{id}/valuation?as_of=
POST /advise                   # {as_of?} → advice batch for all active garments
GET  /advice?garment=&action=&as_of=            GET /advice/{id}
POST /backtests                # {start, end, placebo_seed?} → run + aggregates + baselines
GET  /backtests/{run_id}       GET /backtests?limit=
```

### CLI sketch (Typer)

```
grailtrader init
grailtrader listings load [--source fixture|csv|ebay] [--path F]
grailtrader listings list [--stratum --status] | show <id>
grailtrader index build [--as-of D]
grailtrader index show <stratum-id> [--weeks N] [--excluded] [--json]
grailtrader events ingest [--source fixture|rss] [--path F] [--since --until]
grailtrader events add --type T --brand B [--era E --reason R --tier ... --occurred-on D --source manual|social]
grailtrader events review [--confirm <id> | --reject <id>] | list [--type --brand --status] | show <id>
grailtrader brands list | show <brand-id>
grailtrader portfolio add|edit|remove|list | show <garment-id>
grailtrader portfolio value [--as-of D]
grailtrader advise [--as-of D] ; grailtrader advice list [--action --garment] | show <id>
grailtrader backtest run [--start --end] | placebo --seed S | show <run-id>
```

## Key design decisions & assumptions

1. **Stratified, condition-adjusted median index — not repeat sales, not
   full hedonics.** Repeat-sales indices (Bailey, Muth & Nourse 1963;
   Case & Shiller 1989; the Mei Moses art index) need the same item to sell
   twice, which archive garments rarely do; a full hedonic regression
   (Rosen 1974) needs dense attribute data we don't have. The workable
   middle — used by national statistical offices as the "mix-adjusted
   median" family of house price indices — is stratification by the
   attributes that drive price (brand, designer era, category) plus a fixed
   quality adjustment (condition), with a robust central estimate per cell.
   Size is excluded from strata to keep cells dense (non-goal 9).
2. **Condition adjustment via committed multipliers.** A fixed-coefficient
   hedonic adjustment: each sale is restated as excellent-condition-
   equivalent before aggregation. The five-grade scale and alias table
   mirror real platform taxonomies (Grailed: New/Gently Used/Used/Very
   Worn; Vestiaire: Never worn with tag → Fair condition), so live imports
   map cleanly. Multipliers are editable data; wrong multipliers show up as
   M1 index error, which is the honest failure mode.
3. **Sold prices only; asks are diagnostics.** Asking prices in posted-price
   resale markets are aspirational (listings sit unsold for months);
   treating them as prices inflates any index. Solds enter the index; asks
   feed an ask-over-sold spread diagnostic only. This is also why the live
   eBay adapter (ask-only) can never contaminate the index (non-goal 7).
4. **Robust fence, floored.** `max(3.5σ̂, ln 2.2)` on log deviations: the
   MAD term (Hampel-style robust outlier rule) adapts to stratum
   volatility; the floor stops tiny windows from rejecting everything or
   nothing. Counterfeits priced to move (~0.2×) and mislabeled items (~3×)
   sit far outside; ordinary lognormal composition noise (σ ≈ 0.28 in
   fixtures) stays inside. Gated by M1b (planted-outlier recall ≥ 0.90,
   clean false-exclusion ≤ 5 %).
5. **Designer era is a first-class dimension.** Archive collecting prices
   the era, not just the brand — founder-era Helmut Lang, Slimane-era Dior
   Homme, Philo-era Céline ("Old Céline"), Miyashita-era Number (N)ine.
   Eras are committed gazetteer data with real tenure dates; a departure
   *closing* an era is precisely what makes its supply finite and its
   premium durable, which is why departure priors carry a permanent
   component.
6. **Typed events in, no extraction.** Locked with the owner. This keeps
   the hermetic budget on the two genuinely hard parts instead of
   replicating NewsAlpha's extraction stack. The live-RSS keyword matcher
   only nominates candidates; a pending event has zero effect until
   confirmed — a human is the extraction QA (FR-5).
7. **Impact = permanent + decaying transient.** The adstock model
   (Broadbent 1979) gives the transient the right shape (geometric decay
   with a half-life); the permanent term captures archive re-rating that
   does not decay (era closure, severe scandal stain). Event-study practice
   (MacKinlay 1997) motivates measuring impacts as abnormal moves from a
   pre-event baseline. Two parameters + a half-life per prior row is the
   smallest model that can express both "hype spike" and "regime change" —
   and both are needed: a co-sign is nearly all transient, a death is both.
8. **Priors are case-grounded, editable data — and mistrusted.** Every
   prior row carries `rationale` + `source_note` naming its real-world
   anchor (SCOPE typology table). But resale-market event studies are
   anecdotal compared to equity literature, so the design treats priors as
   hypotheses: the backtest gates directional skill and calibration, and a
   user can retune priors and re-run `backtest` to see whether their
   numbers beat the committed ones.
9. **The advisor trades the gap between modeled path and observed index.**
   Secondhand posted-price markets reprice slowly — sellers must manually
   edit listings, so news diffuses over weeks, not minutes (the fixture
   scenario plants a 2-week phase-in). Right after an event the observed
   index lags the modeled path → buy window; after the transient peaks,
   decay makes the modeled forward path fall below the observed level →
   sell-into-the-spike window. `r̂ = (B · Π(1 + impacts at t+H)) / O_t − 1`
   captures both with one formula (FR-8).
10. **Multi-horizon argmax-z action selection.** A transient co-sign is a
    4-week trade; a departure re-rating is a 12–26-week trade. Evaluating
    r̂ at {4, 12, 26} weeks and acting on the highest signal-to-noise
    horizon lets one rule serve both, and the backtest scores each advice
    at its own horizon (mirroring how NewsAlpha scores signals at their own
    horizon).
11. **Confidence means P(direction correct), capped at 0.95, and is
    calibration-gated.** The z-based core (noise from the stratum's own
    MAD-estimated weekly volatility) is damped by index quality (staleness)
    and event reliability (prior base confidence × source factor ×
    corroboration). Reliability-diagram bucketing (Murphy 1973 tradition)
    with gate M3 keeps it honest. The cap is a safeguard: the system may
    never claim certainty.
12. **Action thresholds sized to friction.** ±10 % expected move, because
    round-trip costs on real platforms (Grailed ~9 % + processing, StockX
    ~12 %, Vestiaire ~15 %, The RealReal consignment up to 50 % on
    low-value items) make smaller edges untradeable. The rendered advice
    always restates the fee assumption (FR-9). Thresholds are config data.
13. **Leak-freedom by construction, proven by placebo.** Strictly-prior
    data at each replay week, entry at week t+1 (both asserted in code),
    and a seeded placebo run (event dates displaced ±26–52 weeks) that must
    show no skill (M4) — Brown & Warner-style simulation honesty applied to
    this domain. The placebo is a CLI feature, not just an eval, so the
    owner can re-verify on future data.
14. **Fixture brands are fictional; the runtime gazetteer is real.**
    Fixtures include scandal and death events, which must not be attached
    to real brands or people; fictional brands modeled on documented
    archetypes (founder-departure archive brand, hype-collab streetwear
    brand, scandal-hit megabrand) keep realism without defamation risk.
    The committed runtime gazetteer contains real brands and real tenure
    dates (factual reference data) for actual use.
15. **Not-financial-advice is enforced behavior** (workspace rule): the
    frame check runs pre-persist and the store cannot represent an
    unchecked advice row. The footer states the three honest caveats of
    this asset class — unregulated market, weeks-to-months illiquidity,
    authenticity risk — alongside the confidence number. Gate M5 = 1.0.
16. **Weekly grain.** Resale sold-comp density does not support daily
    indices even for hot strata (contrast StockX sneakers, which trade
    daily at scale); ISO weeks with a 4-week trailing window is the
    coarsest grain that still resolves hype half-lives of 3–10 weeks.
17. **Assumption: single user, single process.** SQLite, no concurrent
    writers, API binds localhost, no auth.
18. **Assumption: implementation lands in ~2,700–3,500 lines** across
    engine/adapters/store/api/cli, within the 2–4k mandate. Scope valves,
    in order: drop `EbayListingsFeed` (CSV import remains), drop the
    `watching` status, reduce horizons to {4, 12}, shrink the runtime
    gazetteer. The index/impact/advisor/backtest core is not a valve — it
    is the product.
19. **Assumption: era attribution is input data.** Listings and garments
    arrive with brand/era supplied (fixtures by construction, CSV by the
    user, eBay by query mapping). Unresolvable rows are skipped and
    counted, never guessed (FR-2); inference is future work (non-goal 4).
20. **Assumption: events are point-in-time.** Multi-week sagas (a scandal
    that escalates) are modeled as multiple events; the log-additive
    combination handles overlap. Corroboration is a stored integer bumped
    by re-ingest from distinct sources, not a clustering system.
