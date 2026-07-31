# TickerPress

A single-user news watcher for a stock watchlist. It ingests RSS/Atom feeds,
detects **genuine** company appearances (accepting "Meta fined by EU
regulator", rejecting "meta-analysis of statin trials"), collapses syndicated
copies of one wire story into a single story, scores how central the company
is to each story, and delivers ranked digests and alerts — each (company,
story) reaching a channel **exactly once**, enforced by a database constraint.

Informational only — TickerPress delivers links and factual metadata. It
computes no sentiment, no price targets, and has no code path that emits an
opinion (SCOPE decision D14).

The two hard parts, both gated by evals:

- **Mention disambiguation** — deterministic entity linking against your alias
  table: strong surfaces (`Apple Inc.`, `(NASDAQ: AAPL)`, `$AAPL`) accept
  outright; weak surfaces (`Apple`, word-collision tickers like `META`, `CAT`,
  `ALL`) earn acceptance through an inspectable evidence score (coreference,
  case, cue/anti-cue lexicons, per-company terms). Every candidate — accepted
  or rejected — is persisted with its full feature vector, so `explain` shows
  exactly why, with no hidden state.
- **Syndication dedup + exactly-once delivery** — 3-token shingles + exact
  Jaccard resemblance cluster edited copies (τ = 0.60) without merging distinct
  same-company stories; an append-only delivery ledger with a partial unique
  index makes double delivery an `IntegrityError`, not a code-review hope.

No ML, no network in tests or evals, no wall-clock reads in the engine — time
and seeds are explicit inputs, and two runs under different `PYTHONHASHSEED`
values produce byte-identical archives (eval M5).

## Quickstart

From `projects/` (the uv workspace root):

```bash
uv sync --all-packages
uv run tickerpress init                      # create the archive, verify lexicons
uv run tickerpress company add AAPL --name "Apple Inc."
uv run tickerpress term add AAPL --context iphone --anti orchard
uv run tickerpress feed add "file://$PWD/tickerpress/evals/fixtures/feeds/wire_one.xml" --name "Wire One"
uv run tickerpress ingest
uv run tickerpress digest run --channel console
```

The archive defaults to `~/.tickerpress/tickerpress.db` (`--db` or
`TICKERPRESS_DB` override it); the file channel writes to `./outbox`
(`--outbox` / `TICKERPRESS_OUTBOX`).

## CLI tour (real output)

`company add` generates the four standard aliases (US-1):

```
$ tickerpress company add AAPL --name "Apple Inc."
added AAPL — Apple Inc. (mode=digest)
  alias   1  AAPL                     ticker_symbol  strong prior=0.00
  alias   2  $AAPL                    cashtag        strong prior=0.00
  alias   3  Apple Inc.               legal_name     strong prior=0.00
  alias   4  Apple                    short_name     weak   prior=0.25
```

`ingest` fetches every enabled feed (fixture feeds shown), archives, dedups,
scores, and fires alerts; one broken feed never aborts the run:

```
$ tickerpress ingest --now 2026-03-02T13:00:00Z
run 1 succeeded: 37 new articles, 32 new stories, 23 candidates (16 accepted), 0 alerts
  feed 1: ok seen=19 new=19 skipped=0
  feed 2: ok seen=18 new=18 skipped=0
```

`digest run` composes one ranked message per channel — companies by ticker,
stories by relevance — and records it in the exactly-once ledger. Re-running
delivers nothing new:

```
$ tickerpress digest run --channel file --now 2026-03-02T13:05:00Z
delivery 1 sent on file: 3 item(s) — TickerPress digest — 2026-03-02
```

The rendered body (fixed template, factual fields only):

```markdown
# TickerPress digest — 2026-03-02

## AAPL — Apple Inc.
- [Apple beats March-quarter estimates on services strength](https://wireone.example.com/apple-services-quarter)
  — Wire One, 2026-03-01 21:30 UTC, relevance 94, matched: Apple, Apple Inc., AAPL
- [Smartphone upgrade cycles keep lengthening](https://techledger.example.com/upgrade-cycles-lengthen)
  — Tech Ledger, 2026-02-25 10:15 UTC, relevance 50, matched: Apple — +1 other outlet

---
Informational only — links to third-party news coverage. Not investment advice.
```

`explain` reads the persisted feature vectors — never recomputes:

```
$ tickerpress explain 2
article 2: Apple beats March-quarter estimates on services strength
  story 2: new story (1 copies)
  [ACCEPT] AAPL 'Apple' (title 0:5, alias, weak) score=1.000 threshold=0.35
      alias 4 'Apple' (short_name)
      features: allcaps_run=0, anti_terms=0, case_signal=0, coref_strong=1, ctx_terms=1,
                doc_antis=0, doc_cues=20, hyphen_compound=0, prior=0.25, window_antis=0, window_cues=4
  [ACCEPT] AAPL 'Apple Inc.' (summary 0:10, alias, strong) score=1.000 threshold=0.35
  [ACCEPT] AAPL 'AAPL' (summary 20:24, exchange_qualified, strong) score=1.000 threshold=0.35
  relevance AAPL = 94 (0.50·title 1 + 0.25·lede 1 + 0.25·min(1, 3/4))
```

Other commands: `company list|show|set|remove`, `alias add|rm`,
`feed list|rm|enable|disable`, `articles list|show`, `stories list|show`,
`digest list|show` (with `--json` on list/show commands and `--now ISO`
wherever time matters). Errors exit non-zero.

## API

```bash
uv run uvicorn tickerpress.api:app
```

Same service layer as the CLI. Endpoints (SCOPE FR-14):

| Area | Endpoints |
|---|---|
| Health | `GET /health` |
| Watchlist | `GET/POST /companies`, `GET/PATCH/DELETE /companies/{ticker}`, `POST /companies/{ticker}/aliases`, `DELETE /companies/{ticker}/aliases/{alias_id}` |
| Feeds | `GET/POST /feeds`, `PATCH/DELETE /feeds/{id}` |
| Ingest | `POST /ingest/runs` (`{now?, feed_id?, deliver_alerts, alert_channel}`), `GET /ingest/runs?limit=`, `GET /ingest/runs/{id}` |
| Archive | `GET /articles?company=&since=&until=&min_relevance=&limit=`, `GET /articles/{id}`, `GET /articles/{id}/explain?company=` |
| Stories | `GET /stories?company=&since=&limit=`, `GET /stories/{id}` |
| Digests | `POST /digests` (`{channel, now?, dry_run}` — `204` when empty; `dry_run=true` renders and persists nothing), `GET /digests?channel=&limit=`, `GET /digests/{id}` |
| Ledger | `GET /deliveries?company=&story_id=&channel=` |

Errors use one JSON envelope: `{"error": {"code": ..., "message": ...}}` with
`404 not_found`, `409 conflict`, `422 validation_error`, and
`503 channel_unavailable` (a live channel whose env vars are unset).

## Evals

```bash
uv run python tickerpress/evals/run.py   # scorecard; exit 0 iff every gate passes
uv run pytest tickerpress/               # unit + integration tests AND eval gates
```

The suite is hermetic: committed fixture feeds (108 archived items from 68
hand-written base articles across 6 feeds), frozen truth labels, a seeded
generator whose byte-identical regeneration is itself a test, and
`FixedClock("2026-03-02T13:00:00Z")`. Gates (EVALS.md §5), all currently
passing:

| Gate | Threshold | Current |
|---|---|---|
| M1 mention F1 | ≥ 0.92 | 1.000 |
| M1_amb ambiguous-subset F1 | ≥ 0.85 | 1.000 |
| M1_amb_base (base articles only) | ≥ 0.82 | 1.000 |
| M1_amb_abl (per-company terms ablated) | ≥ 0.75 | 0.984 |
| M2 dedup pairwise precision / recall | ≥ 0.95 / ≥ 0.85 | 1.000 / 0.917 |
| M2_trap must-not-merge pairs merged | = 0 | 0 |
| M2_near near-band pairs with J ≥ 0.40 | ≥ 4 of 5 | 5 |
| M3 relevance ordering | ≥ 0.95 | 1.000 |
| M3_cov observed pairs / traps present | ≥ 55 / 12 | 226 / 12 |
| M4 exactly-once scenario | = 1.0 | 1.0 |
| M5 determinism (cross-`PYTHONHASHSEED` + goldens) | = 1.0 | 1.0 |
| M6_lex lexicon freeze + specificity | = 1.0 | 1.0 |

The scorecard also prints a live-computed naive baseline (whole-word grep,
URL-equality dedup, mention-count relevance) and asserts each gate's margin
over it, so an accidentally easy corpus fails CI. Thresholds and their
rationale live in `docs/EVALS.md`; build-stage changes are recorded in
`docs/REVIEW.md`.

## Live adapters

Everything runs offline by default; live adapters activate only through
environment variables and are exercised by tests against stubs, never the
network:

| Adapter | Activation |
|---|---|
| `LiveRssFeedSource` (conditional GET, 15-min poll spacing) | `TICKERPRESS_ALLOW_NETWORK=1` + an `http(s)://` feed URL + the `live` extra (httpx; `uv add --package tickerpress httpx` or install `tickerpress[live]`) |
| `EmailNotifier` (SMTP + STARTTLS) | `TICKERPRESS_SMTP_HOST`, `TICKERPRESS_SMTP_PORT` (default 587), `TICKERPRESS_SMTP_USERNAME`/`TICKERPRESS_SMTP_PASSWORD` (optional), `TICKERPRESS_EMAIL_FROM`, `TICKERPRESS_EMAIL_TO` |
| `WebhookNotifier` (Slack-compatible POST) | `TICKERPRESS_WEBHOOK_URL` + the `live` extra |

`tickerpress ingest && tickerpress digest run` is designed to be cron-driven;
the app never sleeps or polls on its own.

## Layout

```
src/tickerpress/
  engine/      # pure: feed parsing, normalization, detection, disambiguation,
               #   dedup, relevance, digest composition — no I/O, no clock
  adapters/    # Clock, FeedSource, Notifier protocols + offline/live impls
  store/       # Repository protocol; SQLite (in-memory = sqlite ":memory:")
  api/         # FastAPI app (thin)
  cli/         # Typer app (thin, same service layer)
  resources.py # the ONLY module that reads data/ lexicons
  services.py  # composition layer used by both API and CLI
data/          # committed lexicons (frozen; hashed in the eval manifest)
evals/         # fixtures, metrics, scorecard runner, pytest-enforced gates
docs/          # SCOPE.md, DATA_MODEL.md, EVALS.md, REVIEW.md
```
