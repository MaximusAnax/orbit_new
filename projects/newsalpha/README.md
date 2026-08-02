# NewsAlpha

Single-user decision support for equities and crypto: ingest financial news,
extract **typed events** (earnings surprise, guidance change, M&A, regulatory
action, listing/delisting, protocol hack), link them to the right assets in the
right **roles**, score each link as a signal (direction, magnitude, confidence,
horizon), render a plain-language brief — and prove the signal quality with a
leak-free event-study backtest.

Decision **support**, not advice: the signal schema has no imperative field, and
every brief passes a framing check (forbidden imperative lexicon, four required
sections, verbatim not-advice footer) before it can be persisted. A brief that
fails the check is never produced — the pipeline raises.

Scope, data model and eval design are frozen in `docs/`.

## Quickstart

```bash
cd projects
uv sync --all-packages

uv run newsalpha init                 # create the DB, load + validate the datasets
uv run newsalpha ingest               # read the committed fixture corpus, recompute
uv run newsalpha watch add cx:SOL     # optional: build a watchlist
uv run newsalpha digest --all-assets  # ranked triage of the last 5 days

uv run pytest newsalpha/ -q                    # tests + enforced eval gates
uv run python newsalpha/evals/run.py           # the eval scorecard
uv run ruff check newsalpha/                   # lint
```

`ingest` with no arguments reads `evals/fixtures/articles.jsonl` (223 committed
articles) and anchors `--as-of` on the corpus's own latest publication, so the
whole flow works with zero configuration. Point `--path` at your own JSONL, or
`--feed rss` at real feeds, when you are ready.

## CLI

```
newsalpha init [--reset]
newsalpha ingest [--feed fixture|rss] [--path F] [--since T] [--until T] [--as-of T]
newsalpha articles list [--domain --since --limit] | show <id>
newsalpha events   list [--type --asset --stage --since] | show <id>
newsalpha signals  list [--asset --min-confidence --direction --include-superseded]
                 | show <id> | revisions <id>
newsalpha brief <signal-id>
newsalpha digest [--date D] [--all-assets] [--include-superseded]
newsalpha assets list [--kind --query] | show <asset-id>
newsalpha watch add|remove <asset-id> | list
newsalpha prices load [--source fixture|live] [--dir D] [--start --end] [--assets A,B]
newsalpha backtest run [--start --end --min-confidence] | placebo [--seed S] | show <run-id>
```

Every list/show command takes `--json`. Failures print `error [<code>]: …` to
stderr and exit non-zero: `2` invalid request, `3` invalid dataset, `4` not
found, `5` conflict, `6` frame-check failure, `7` feed/market data unavailable.

### Real output

```
$ uv run newsalpha ingest
ingest as_of 2026-05-06T19:08:00Z
metric             count
-----------------  -----
articles           223
articles_excluded  0
clusters           160
events             108
signals_new        117
revisions_new      0
briefs             117

$ uv run newsalpha digest --all-assets
digest 2026-05-06  (all assets, latest only)
#   asset    type               stage      dir      mag       conf  bars  score    summary
--  -------  -----------------  ---------  -------  --------  ----  ----  -------  ------------------------------
1   cx:RUNE  hack_exploit       confirmed  bearish  major     0.95  5     -0.0428  THORChain (RUNE) was reported to have suffered an exploit or breach.
2   cx:EGLD  listing            confirmed  bullish  major     0.63  5     +0.0284  MultiversX (EGLD) was announced for listing on a new venue.
3   cx:EOS   delisting          confirmed  bearish  major     0.51  5     -0.0257  EOS (EOS) was announced for removal from a venue.
4   eq:BA    earnings_surprise  confirmed  bearish  moderate  0.87  20    -0.0173  The Boeing Company (BA) reported quarterly results below expectations.

$ uv run newsalpha events show 3faef139fd587ab0
3faef139fd587ab0  hack_exploit  stage=confirmed  2026-05-03
cluster: 18c72bd5706fb6e7   extraction_confidence: 0.95
attributes: {"amount_usd": 400000000.0, "vector": "flash loan"}

evidence
article           start  end  quote
----------------  -----  ---  ---------------
d73d92ccc613ab63  56     71   Attackers stole
2e93f046b7113301  56     71   Attackers stole
7caf4eeb103f7535  56     71   Attackers stole

links
asset    role     conf  quote
-------  -------  ----  ------
cx:RUNE  subject  0.95  (RUNE)

$ uv run newsalpha prices load && uv run newsalpha backtest run
loaded 22616 bars for 133 assets from fixture
backtest 030723776f6c9ee8  (real)  as_of 2026-07-31T17:17:03Z
N=117  excluded=0  superseded=0
hit_rate=0.829  mean_ar=-0.79%  IC=0.731

bucket  n   hit
------  --  -----
lo      40  0.800
mid     46  0.870
hi      31  0.806

event type         n   hit    mean AR  IC
-----------------  --  -----  -------  -----
delisting          12  1.000  -4.06%   0.255
earnings_surprise  18  0.556  -0.51%   0.412
guidance_change    14  0.929  -0.62%   0.742
hack_exploit       14  0.929  -4.20%   0.627
listing            14  0.786  +2.48%   0.395
mna                29  0.897  +0.27%   0.722
regulatory_action  16  0.750  -0.59%   0.694

$ uv run newsalpha backtest placebo --seed 20260731
backtest 5a37415930d5f6a1  (placebo)  as_of 2026-07-31T17:17:04Z
N=117  excluded=0  superseded=0
hit_rate=0.402  mean_ar=+0.24%  IC=-0.181
```

The placebo run is the point: the same harness, with entries displaced by a
hash-derived per-signal offset, finds no skill. Numbers above come from the
committed fixture market series — they measure whether the pipeline recovers
effects that are *planted*, and are **not** a forecast of live hit rates.

A brief, in full:

```
$ uv run newsalpha brief <signal-id>
THORChain (cx:RUNE) | hack_exploit | bearish | major | confidence 0.95 | 5 bars

What happened
THORChain (RUNE) was reported to have suffered an exploit or breach (confirmed).
Details: amount $400.00 million; vector flash loan. Evidence: "Attackers stole"
-- sec.example; "Attackers stole" -- globenewswire.example.

Why it matters
Tokens of exploited protocols have historically dropped sharply on the day of
disclosure, roughly 10% to 25%, with a smaller continued decline over the
following days … The announcement-window move documented in the literature for
this event type runs -25.0% to -10.0%; the band scored here is the post-entry
part only, -6.5% to -2.5% over 5 trading bars measured from the first bar after
the evidence was published. …

What to watch
- an official post-mortem naming the vector
- a reimbursement or treasury backstop plan
- TVL and exchange-deposit outflows
- whether the stolen funds move or are frozen

Uncertainty
Confidence 0.95 at the confirmed stage, from 3 independent source(s) over a
5-bar horizon. Most of the announcement move was already in the price before the
first bar this signal can be measured from … What would change this reading:
recovery of the funds, a credible reimbursement plan, or the loss proving
smaller than first reported.

This is information, not investment advice. NewsAlpha describes evidence and
uncertainty; it does not recommend trades.
```

## API

`uvicorn newsalpha.api.app:app` (binds localhost; single local profile, no auth).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | version + loaded dataset sizes |
| POST | `/ingest` | `{as_of, since?, until?, feed, path?}` → article/cluster/event/signal/brief counts |
| GET | `/articles`, `/articles/{id}` | ingested articles (`since`, `domain`, `limit`) |
| GET | `/events`, `/events/{id}` | derived events; the detail view adds evidence spans and asset links |
| GET | `/signals`, `/signals/{id}` | latest revision per key (`asset`, `direction`, `min_confidence`, `since`, `include_superseded`) |
| GET | `/signals/{id}/revisions` | the full revision chain for that signal's key |
| GET | `/briefs/{signal_id}` | the brief attached to one revision |
| GET | `/digest` | ranked triage (`date`, `watchlist_only`, `include_superseded`) |
| GET | `/assets`, `/assets/{id}` | the committed gazetteer (`kind`, `q`) |
| GET/PUT/DELETE | `/watchlist`, `/watchlist/{asset_id}` | watchlist management |
| POST | `/prices/load` | `{source, assets?, start, end, directory?}` → bar count |
| POST | `/backtests`, GET `/backtests`, `/backtests/{run_id}` | run and inspect backtests (`placebo_seed` for a placebo run) |

Every failure returns `{"code", "message", "details?"}` with the status from the
error catalog: `404` unknown_article / unknown_event / unknown_signal /
unknown_brief / unknown_backtest / unknown_asset, `409` conflict, `422`
invalid_request, `500` dataset_invalid / frame_check_failed, `503`
feed_unavailable / market_data_unavailable.

## Evals

```bash
uv run python newsalpha/evals/run.py               # scorecard, exit 1 on any FAIL
uv run python newsalpha/evals/run.py --regen-check # re-derive the fixtures and diff
uv run pytest newsalpha/evals -q                   # the same gates as pytest
```

The scorecard prints every metric with its gate, its **live-computed** naive
baseline and PASS/FAIL, and takes about 15 seconds; the whole `pytest` suite
(327 tests, gates included) runs in well under a minute. Current values on the
committed fixtures:

| Metric | Value | Gate | Naive baseline |
|---|---|---|---|
| M1a event macro-F1 (VAL) | 1.0000 | ≥ 0.80 | 0.1473 |
| M1a-floor min per-type F1 | 1.0000 | ≥ 0.65 | 0.0000 |
| M1a-transfer (DEV − VAL) | 0.0000 | ≤ 0.10 | ≥ 0.40 |
| M1b stage+attributes, conditional | 1.0000 | ≥ 0.85 | 0.0000 |
| M1c stage+attributes, unconditional | 1.0000 | ≥ 0.70 | 0.0000 |
| M2a link F1 (VAL) | 1.0000 | ≥ 0.85 | 0.5890 |
| M2b trap accuracy (50 mentions) | 1.0000 | ≥ 0.90 | 0.4400 |
| M3 role accuracy (40 decisions) | 1.0000 | ≥ 0.85 | 0.4500 |
| M4 direction hit rate (5 seeds) | 0.7983 | ≥ 0.72 | 0.4410 |
| M4-transfer (DEV − VAL) | 0.0273 | ≤ 0.10 | ≥ 0.25 |
| M5 information coefficient | 0.7087 | ≥ 0.35 | −0.0075 |
| M6 calibration separation | 0.1645 | ≥ 0.12 | 0.0000 |
| M6-occupancy min bucket n | 31 | ≥ 20 | 0 |
| M7a placebo \|mean hit − 0.5\| | 0.0020 | ≤ 0.035 | 0.2983 |
| M7b placebo \|mean IC\| | 0.0045 | ≤ 0.06 | 0.7087 |
| M8 framing verdict accuracy | 1.0000 | = 1.0 | 0.8271 |
| M8-lexicon shipped ⊇ reference | holds | must hold | fails |
| G1 N_directional | 117 | ≥ 100 | 64 |
| G2 exclusion rate | 0.0000 | ≤ 0.05 | 0.3419 |
| Leak canary: announcement-bar capture | 0/585 | = 0 | ≈ 1 one bar early |

### Fixtures

Everything under `evals/fixtures/` is committed and regenerable byte-identically
by the seeded generators beside it:

| File | Contents |
|---|---|
| `generate_articles.py` | seeded corpus generator (`--check-disjoint`, `--regen-check`) |
| `articles.jsonl` | 223 articles: 108 truth events (32 DEV / 76 VAL), 40 hand-authored adversarial articles, 30 generated no-event articles |
| `articles_truth.json` | per-event labels, 50 annotated trap mentions, 40 role decisions, arrival batches, cluster Jaccard margins |
| `generate_market.py` | seeded market generator from a literature-scaled planted-effect table |
| `market/seed_{1..5}/*.csv` | daily OHLCV for 131 assets + `idx:US` + `idx:CX`, 252 calendar days |
| `market_truth.json` | the planted-effect table and every per-seed realized draw |
| `gapped/` | a deliberately holed series for the FR-9/FR-10 gap tests |
| `frame_cases.json` | 16 adversarial brief scenarios with hand-authored expected verdicts |

Ground truth never comes from the system under evaluation: article labels are the
generator's construction parameters, market labels are a planted-effect table
written from the event-study literature independently of `data/priors.json`, and
the frame-check grader embeds its own copy of the forbidden lexicon. Two
paraphrase families (DEV / VAL) with **disjoint trigger-bearing 3-grams** carry
the truth events; only VAL is gated, and the DEV − VAL transfer gap is itself a
gate.

The suite is hermetic: offline adapters only, `socket.socket` monkeypatched to
raise, and a test that asserts `feedparser` and both live adapter modules stay
out of `sys.modules` after a full eval run.

## Layout

```
src/newsalpha/
  models.py          # Pydantic v2 domain models + enums (DATA_MODEL.md)
  datasets.py        # committed dataset loading + FR-3 validation
  errors.py          # the error catalog both edges map from
  factory.py         # shared wiring: datasets, repository, fixture defaults
  service.py         # orchestration: repository + adapters + engine
  engine/            # pure domain logic: no clock, no filesystem, no network
    normalize.py     #   FR-1  HTML strip, NFKC, hashing, sentence segmentation
    cluster.py       #   FR-2  shingling, Jaccard, union-find, corroboration
    extract.py       #   FR-4  pattern engine, suppressors, stages, attributes, merge
    link.py          #   FR-5  gazetteer scan, all-caps guard, venue precedence, roles
    score.py         #   FR-6  prior resolution, stage overrides, modifier formula
    revise.py        #   FR-15 signal keys, key continuity, revisions, supersession
    brief.py         #   FR-7  template rendering
    frame.py         #   FR-7  forbidden-lexicon + required-section frame check
    digest.py        #   FR-8  window filter, supersession filter, ranking
    backtest.py      #   FR-10 entry/exit, abnormal returns, IC, buckets, placebo
    pipeline.py      #   FR-14/15 active-window recompute
  adapters/          # NewsFeed + MarketData: offline (default) and live (env-gated)
  store/             # Repository protocol, SQLite (default) + in-memory backends
  api/               # FastAPI app + request/response schemas
  cli/               # Typer app
data/                # assets.json, patterns.json, priors.json, templates.json,
                     # source_tiers.json, benchmarks.json — the committed datasets
evals/               # fixtures/, metrics.py, run.py, test_gates.py
```

Extending the universe or the pattern/prior/template catalogs is a **data edit**
(then `init` re-validates), never a code change.

## Configuration & live adapters

| Variable | Used by | Meaning |
|---|---|---|
| `NEWSALPHA_DB` | store | SQLite path; default `~/.newsalpha/newsalpha.db`. `--db :memory:` for a throwaway store |
| `NEWSALPHA_ACTIVE_WINDOW_DAYS` | pipeline | active-window size for the recompute (default 30) |
| `NEWSALPHA_FEEDS` | `RSSNewsFeed` | comma-separated RSS/Atom URLs. **The live feed activates only when this is set** |
| `NEWSALPHA_USER_AGENT` | `RSSNewsFeed` | optional User-Agent override |
| `NEWSALPHA_LIVE` | `LiveMarketData` | set to `1` to enable live price loads. **Nothing live runs without it** |
| `NEWSALPHA_COINGECKO_KEY` | `LiveMarketData` | optional CoinGecko demo key; raises rate limits |
| `NEWSALPHA_HTTP_TIMEOUT` | `LiveMarketData` | request timeout in seconds (default 20) |

To go live:

```bash
export NEWSALPHA_FEEDS="https://feeds.reuters.com/reuters/businessNews,https://www.coindesk.com/arc/outboundfeeds/rss/"
export NEWSALPHA_LIVE=1
uv pip install feedparser          # the only extra dependency, needed by RSSNewsFeed
uv run newsalpha ingest --feed rss
uv run newsalpha prices load --source live --start 2026-01-01 --end 2026-07-31
```

`LiveMarketData` routes by asset kind — Stooq's free CSV endpoint for equities
(no key), CoinGecko for crypto — and synthesizes `idx:CX` as the equal-weighted
mean of the `data/benchmarks.json` basket, so no crypto asset is its own
benchmark. No secrets are stored in the repository, a live fetch failure raises a
clear error rather than degrading to silently stale analysis, and neither live
module is imported on the offline path.

## Determinism

The derived state is a pure function of (stored in-window articles, committed
datasets, `as_of`). All ids are content-derived, time is always an input, and the
only randomness is the placebo seed, which is hash-derived per signal. Two
guarantees are asserted by tests, on the committed eval corpus:

- **D0 determinism** — two runs over the same articles produce byte-identical
  canonical exports and identical ids; re-ingesting writes zero new revisions.
- **D1 replay equivalence** — one batch and five chronological arrival batches of
  the same articles produce byte-identical clusters, events and links, and an
  identical latest scored tuple per (event type, asset, role). Only the revision
  *history* differs, and a late corroboration raises confidence through a new
  revision rather than mutating one.
