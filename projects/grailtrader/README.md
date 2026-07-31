# GrailTrader

A single-user advisor that treats designer clothing like a tradable asset class.
It builds weekly price indices per **(brand, designer era, category)** from
secondhand-marketplace *sold* listings, ingests typed fashion events (departure,
appointment, collab, celebrity co-sign, runway reception, scandal), maps each
event to an expected index impact that decays over time, and issues **buy / sell /
hold** advice per garment — with confidence, plain-language rationale, and a
leak-free backtest that measures its own directional skill against naive
baselines.

It never buys, sells, bids or connects to a marketplace account. Every rendered
advice carries its confidence, its driving events, a fee/illiquidity note and a
collectibles-not-securities footer, enforced by a frame check that blocks
persistence on failure — this is information about a model, not investment
advice.

Two things this product lives or dies on, and both are gated by the eval suite:

1. **A trustworthy index from hostile data** (FR-3/FR-4). Listings are sparse,
   heterogeneous, condition-confounded and polluted with fakes and mislabels. The
   index recovers each stratum's true level to a median error of **2.7 %** on the
   committed fixtures — against **8.9 %** for the naive weekly mean of raw prices.
2. **Event-driven advice whose skill is measured, not asserted** (FR-6/FR-8/FR-10).
   Directional hit rate **0.83** over 489 actionable calls, versus **0.49** for
   θ-momentum and **0.58** for buy-on-any-event; three seeded placebo runs with
   scrambled event dates land at **0.51 ± 0.01**, i.e. no skill, which is what a
   leak-free harness must show.

---

## Quickstart

```bash
cd projects
uv sync --all-packages
export GRAILTRADER_DB=/tmp/grailtrader-demo.db     # default: ~/.grailtrader/grailtrader.db

uv run grailtrader init --reset
uv run grailtrader listings load --path grailtrader/examples/sample_listings.jsonl
uv run grailtrader events   ingest --path grailtrader/examples/sample_events.jsonl
uv run grailtrader index build
```

```
validated datasets (config 1.0.0): 29 brands / 69 eras materialised, 15 impact priors, 10 advice templates — database reset
ingested 3250 · duplicates 0 · unresolved 0 · non-USD 0 · invalid 0
created 2 · corroborated 0 · unchanged 0 · totals: confirmed=2
built 11 strata (5 leaf) as of 2026-06-29T23:59:59Z: 858 weekly points, 263 sales fenced out
```

`examples/` is a small synthetic sample on real archive brands so the walkthrough
runs with zero configuration (`examples/generate_sample.py` regenerates it). The
eval fixtures under `evals/fixtures/` are much larger and deliberately use
*fictional* brands, because they carry scandal and death events.

**Inspect an index** — every point stores its sample size and how many sales the
FR-3 fence removed:

```bash
uv run grailtrader index show celine/philo/outerwear --weeks 6
```

```
week        index  level_usd  n_sales  n_excluded
----------  -----  ---------  -------  ----------
2026-05-25  81.62  $2,245.22  31       1
2026-06-01  87.21  $2,398.73  31       1
2026-06-08  87.21  $2,398.73  31       1
2026-06-15  88.36  $2,430.53  31       1
2026-06-22  93.30  $2,566.28  32       0
2026-06-29  85.41  $2,349.32  32       0
```

**Register the closet and mark it to market** (FR-7 always names its method):

```bash
uv run grailtrader portfolio add --label "HL astro moto jacket" \
    --brand "Helmut Lang" --era helmut --category outerwear \
    --condition excellent --price 840 --date 2025-03-03
uv run grailtrader portfolio value
```

```
label                 stratum                       anchor     fair value  gain     method
--------------------  ----------------------------  ---------  ----------  -------  ------------
HL astro moto jacket  helmut-lang/helmut/outerwear  $840.00    $742.08     $-97.92  repeat_sales
Raf archive knit      raf-simons/raf/knitwear       $600.00    $714.00     $114.00  repeat_sales
Philo-era wool coat   celine/philo/outerwear        $1,650.00  $1,558.70   $-91.30  repeat_sales

total fair value $3,014.78 vs anchors $3,090.00 ($-75.22); 0 unavailable
```

**Ask for advice** a week after the sample's planted house closure:

```bash
uv run grailtrader advise --as-of 2025-10-20
```

```
garment           action  H*  r_hat   conf  fair value  method        why
----------------  ------  --  ------  ----  ----------  ------------  ---------------------
0ba5bba83fd3964e  hold    4   -       -     $782.58     repeat_sales  hold:no_active_events
37c71f01948e2613  buy     4   +23.0%  0.71  $617.02     repeat_sales  1 drivers
995a67f45f8d593f  hold    4   -       -     $1,576.52   repeat_sales  hold:no_active_events
```

`--text` prints what actually gets persisted — and what the FR-9 frame check
verified before it could be stored:

```
Consider buying: “Raf archive knit”
Action: buy · Modeled move: +23.0% over 4 weeks

Why now:
- designer departure at Raf Simons (house closure), Raf Simons mainline — “The label's own line is wound down; the era is closed.” [scope raf-simons/raf], 1 weeks ago, scope weight 1.00: A house shutting down freezes the whole archive: nothing further will ever be produced, so the re-rating is large and durable while the news itself decays slowly.

Valuation: $617.02 · Method: repeat_sales · Typical excellent-condition comp for the leaf stratum: $772.46

Confidence: 0.71 · Falsifier: weakens if the market has already repriced this event, or if this piece's size and condition make it slow to move

Fees & liquidity: acting costs roughly 12% one way in platform and payment fees, and this market clears over weeks to months, not days.

GrailTrader models a collectibles market — unregulated, illiquid (sales take weeks to months), with authenticity risk. This is information about that model, not investment advice.
```

**Backtest it, then try to break it.** The replay uses only strictly-prior data
and enters the week *after* the advice week; the placebo run displaces every
event by ±26–52 weeks, so any skill it shows is leakage:

```bash
uv run grailtrader backtest run --start 2025-04-07
uv run grailtrader backtest placebo --seed 20260731
```

```
run 45cc89cd01921dd6 (2025-04-07 .. 2026-06-29)
decisions 195 · candidates 5 · actionable 5 · hit rate 1.000
mean realized buy +0.1205 / sell +0.0000 · spread +0.0000
buckets: lo n=0 hit=0.00 conf=0.00 · mid n=0 hit=0.00 conf=0.00 · hi n=5 hit=1.00 conf=0.71
regimes: sell-into-decay 0 · phase-in buys 4
baselines: always_hold n=195 hit=n/a spread=+0.0000 · theta_momentum n=13 hit=0.231 spread=-0.0763 · event_naive n=24 hit=0.417 spread=+0.0000

run 83e74003e6e7a1f8 (2025-01-06 .. 2026-06-29)
PLACEBO seed 20260731 — event dates scrambled
decisions 234 · candidates 24 · actionable 24 · hit rate 0.500
```

(Five calls on a three-garment sample is a demo, not evidence. The statistically
meaningful numbers are the eval suite's, below.)

---

## CLI

`uv run grailtrader --help` for the full tree. Every `list`/`show` command takes
`--json`; every failure exits non-zero with a message on stderr.

| Command | What it does |
|---|---|
| `init [--reset]` | Validate the five committed datasets and materialise the gazetteer (FR-1) |
| `listings load --path F [--source fixture\|csv]` | Ingest a feed; idempotent (content-derived ids) |
| `listings list [--stratum --status --limit] \| show <id>` | Inspect stored listings |
| `index build [--as-of D]` | Rebuild every leaf + chain-linked parent series (FR-4) |
| `index show <stratum> [--weeks N] [--excluded]` | Weekly points with `n_sales` / `n_excluded` |
| `index strata [--level leaf\|era\|brand] [--brand B]` | What the index covers |
| `events ingest [--source fixture\|rss] [--path F] [--social]` | Typed events in; RSS yields *pending* candidates |
| `events add --type T --brand B [--era --reason --tier …]` | Manual typed entry (a first-class source) |
| `events list \| show <id> \| review [--confirm\|--reject <id>]` | Scopes, priors, sources, retirement age `A_e` |
| `brands list \| show <brand-id>` | The gazetteer and its designer eras |
| `portfolio add \| edit \| remove \| list \| show <id>` | Garment CRUD; removal is a soft delete |
| `portfolio value [--as-of D]` | Mark to market, always naming the valuation method |
| `advise [--as-of D] [--text]` | Buy/sell/hold per garment (FR-8), rendered and frame-checked |
| `advice list [--action --garment --history] \| show <id>` | Current advice by default; `--history` shows superseded rows |
| `backtest run [--start --end] \| placebo --seed S \| show <id> \| list` | Weekly replay, baselines, placebo (FR-10) |

Global: `--db PATH` (or `GRAILTRADER_DB`) selects the SQLite file;
`GRAILTRADER_DATA_DIR` overrides the committed dataset directory.

---

## API

```bash
uv run uvicorn grailtrader.api:app --port 8000    # docs at /docs
```

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | store counts + config version |
| POST | `/listings/load` | `{source, path}` → ingest counts |
| GET | `/listings`, `/listings/{id}` | filters: `stratum`, `status`, `limit` |
| POST | `/index/build` | `{as_of}` → strata/points/exclusions |
| GET | `/index/{stratum_id}` | `from`, `to`, `weeks`, `excluded=true` |
| GET | `/strata` | `level=leaf\|era\|brand`, `brand` |
| POST | `/events/ingest` | `{source, path, since, until}` |
| POST | `/events` | manual typed event — **201** created, **200** when it corroborates an existing one |
| POST | `/events/{id}/confirm`, `/events/{id}/reject` | the only permitted status transition |
| GET | `/events`, `/events/{id}` | detail carries resolved scopes, priors, `A_e`, `source_refs` |
| GET | `/brands`, `/brands/{id}` | gazetteer incl. eras |
| POST/GET | `/portfolio` | **201** on create |
| GET/PATCH/DELETE | `/portfolio/{id}` | `DELETE` is a soft delete and returns the row |
| GET | `/portfolio/value`, `/portfolio/{id}/valuation` | `as_of`; always reports `valuation_method` |
| POST | `/advise` | `{as_of}` → the batch plus per-action counts |
| GET | `/advice`, `/advice/{id}` | `garment`, `action`, `as_of`, `history` |
| POST/GET | `/backtests`, `/backtests/{run_id}` | **201** on create; aggregates + baselines |

Errors share one shape — `{"error": {"code", "message", "detail"}}` — with a
stable catalog: `not_found` (404), `precondition_failed` / `conflict` (409),
`unknown_reference` (422, carries `suggestion`), `unmapped_condition_label`
(422), `invalid_request` (422), `feed_unreadable` (422), `feed_unavailable`
(503), `frame_check_failed` / `dataset_invalid` (500).

The service binds localhost and has no auth: single user, single process
(SCOPE assumption 17).

---

## Evals

```bash
uv run python grailtrader/evals/run.py                # scorecard; exit 1 on any FAIL
uv run python grailtrader/evals/run.py --regen-check  # fixtures still match their generator
uv run pytest grailtrader/                            # unit + integration tests + the gates
```

The suite runs on two committed, seeded scenarios with the offline adapters and
the in-memory store: no network, no wall clock, randomness only from the three
committed placebo seeds. **Ground truth never comes from the system under
evaluation** — latent levels, planted impact paths, chain-linked parent truth,
clean-sale counts and outlier labels all come from `generate_scenario.py`'s
construction parameters, and the FR-9 compliance check is re-implemented
independently in `evals/metrics.py` so the engine cannot grade itself.

**29/29 gates pass in ~45 s** (the whole `pytest grailtrader/` suite, 294 tests including the gates, takes ~75 s). Headline numbers (full table on the scorecard):

| Metric | Measured | Gate | Naive baseline |
|---|---|---|---|
| M0a index coverage | 1.000 | ≥ 0.95 | raising `min_sales` to 15 → ≈ 0.45 |
| M0b actionable calls | 489 | ≥ 400 | selective abstention → 12 |
| M0d sell-into-decay / phase-in buys | 94 / 165 | ≥ 15 / ≥ 100 | scandal-only seller → 0 |
| M1a index error (median) | 0.0269 | ≤ 0.05 | naive weekly mean 0.0891 |
| M1a-p90 / dense / sparse | 0.073 / 0.043 / 0.072 | ≤ 0.14 / 0.06 / 0.12 | — |
| M1b fence recall / false-exclusion | 0.994 / 0.0001 | ≥ 0.90 / ≤ 0.05 | no fence → 0 / 0 |
| M2a directional hit rate | 0.834 | ≥ 0.70 | θ-momentum 0.493, event-naive 0.581 |
| M2b buy−sell spread | +0.108 | ≥ +0.08 | always-hold 0.000 |
| M3a/M3b/M3c calibration | 0.244 / 0.241 / 0.904 | ≥ 0.15 / ≥ 0 / ≥ 0.80 | z-term only ≈ 0.06 |
| M4a/M4b placebo (worst of 3 seeds) | 0.017 / 0.001 | ≤ 0.07 / ≤ 0.04 | leaky harness ≥ 0.10 |
| M5a/M5b framing, both directions | 1.000 / 1.000 | = 1.0 | over-strict matcher ≈ 0.60 |
| M6a/M6b/M6c mismatch scenario | 0.025 / 0.736 / 0.169 | ≤ 0.09 / ≥ 0.60 / ≥ 0 | — |

Two diagnostics are printed and deliberately **not** gated: `D-fence` (what the
price fence does with the ambiguous 0.40–0.60× band — no price-only rule can
separate a fake at 0.5× from a sniped grail) and `D-contra` (how confidently
wrong the advisor is on scenario B's three events whose true impact contradicts
their prior: hit 0.82, mean confidence 0.58 — it has no mechanism to detect a
wrong prior, and the scorecard says so).

**What these gates do and do not certify.** Scenario A is drawn from the engine's
own model family, so passing it certifies pipeline correctness, coverage,
decay/timing handling, calibration ordering, leak-freedom and framing. Scenario B
(perturbed condition multipliers, Student-t noise, three prior-contradicting
events) certifies graceful degradation. **Neither certifies real-world predictive
skill, and hermetically cannot.** That is only measurable by running
`grailtrader backtest run` on your own imported comps and event history.

---

## Live adapters

Everything above runs offline. Three seams activate only when you configure them,
and none of them is imported on the test/eval path (enforced by test T2):

| Seam | Activate with | Notes |
|---|---|---|
| `CsvListingsFeed` | `listings load --source csv --path comps.csv` | Your own marketplace export. Columns (aliases accepted): `external_id`, `brand`, `era`, `category`, `condition`, `status`, `listed_at`, `sold_at`, `sold_price`, `ask_price`, `currency`, `size`, `title`. Sold rows feed the index; ask-only rows are diagnostics and never enter it. USD only. |
| `RssNewsFeed` | `GRAILTRADER_NEWS_FEEDS="https://…,https://…"` plus the optional `feedparser` dependency | Keyword-rule candidate matching over fashion-news RSS. Everything it emits is `status = pending` and influences nothing until `events review --confirm`. No NLP extraction: a human is the QA. |
| `ManualSocialEntry` | `events add --source social …` | The social seam is manual entry by design — no platform scraping. |

No credentials are stored in the repo; the only environment variables are
`GRAILTRADER_DB`, `GRAILTRADER_DATA_DIR` and `GRAILTRADER_NEWS_FEEDS`.

---

## Layout

```
src/grailtrader/
  models.py        Pydantic v2 domain models + enums (DATA_MODEL.md)
  weeks.py ids.py  ISO-week arithmetic; content-derived ids (money as integer cents)
  datasets.py      loads + caches the five committed datasets
  engine/          pure, deterministic domain logic — no I/O, no clock
    strata conditions ingest fence index events impact valuation advisor frame backtest
  adapters/        one Protocol per capability + offline (default) and live implementations
  store/           Repository protocol; SQLite (stdlib sqlite3) + in-memory
  service.py       the one orchestration seam the API and CLI share
  api/  cli/       thin surfaces: parse, delegate, serialize
data/              brands, impact priors, conditions, advice templates, advisor config
evals/             generate_scenario.py, fixtures/, metrics.py, run.py, test_gates.py
docs/              SCOPE.md, DATA_MODEL.md, EVALS.md, REVIEW.md
```

Everything tunable is **data**, not code: the fence constants, the window, the
horizons, `theta`, `conf_min`, `z_half`, the q-index staleness ladder and the
impact priors all live in `data/*.json`, each prior carrying a `rationale` and a
`source_note`. `init` re-validates them, including the coherence rule
`theta_buy == theta_sell == fee_assumption_pct` — so no advice can ever quote an
expected move smaller than the friction it also quotes.

## Limits worth knowing

- **No authenticity verification.** The fence catches *price-implausible*
  listings; it provably cannot separate a fake at 0.5× from a genuine grail
  sniped at 0.5×. The footer names authenticity as a residual risk.
- **Sold prices only.** Asking prices are aspirational and never enter an index.
- **USD only**, weekly grain, no size-level strata, no transactions, no
  notifications, no portfolio-level analytics.
- **Era is input data**, not inferred from tags.
- A `pending` event influences nothing until you confirm it.
