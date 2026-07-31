# NewsAlpha

Single-user decision support for equities and crypto: ingest financial news,
extract **typed events** (earnings surprise, guidance change, M&A, regulatory
action, listing/delisting, protocol hack), link them to the right assets in the
right **roles**, score each link as a signal (direction, magnitude, confidence,
horizon), render a plain-language brief — and prove the signal quality with a
leak-free event-study backtest.

Decision **support**, not advice: the signal schema has no imperative field, and
every brief passes a framing check (forbidden imperative lexicon, four required
sections, verbatim not-advice footer) before it can be persisted.

Scope, data model and eval design are frozen in `docs/`.

## Quickstart

```bash
cd projects
uv run pytest newsalpha/ -q          # tests (+ eval gates, once the eval suite lands)
uv run ruff check newsalpha/         # lint
```

## Layout

```
src/newsalpha/
  models.py          # Pydantic v2 domain models + enums (DATA_MODEL.md)
  datasets.py        # committed dataset loading + FR-3 validation
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
  service.py         # orchestration: repository + adapters + engine
data/                # assets.json, patterns.json, priors.json, templates.json,
                     # source_tiers.json, benchmarks.json — the committed datasets
```

Extending the universe or the pattern/prior/template catalogs is a **data edit**
(then `init` re-validates), never a code change.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `NEWSALPHA_DB` | store | SQLite path; default `~/.newsalpha/newsalpha.db` |
| `NEWSALPHA_ACTIVE_WINDOW_DAYS` | pipeline | active-window size for the recompute (default 30) |
| `NEWSALPHA_FEEDS` | `RSSNewsFeed` | comma-separated RSS/Atom URLs. **The live feed activates only when this is set.** |
| `NEWSALPHA_USER_AGENT` | `RSSNewsFeed` | optional User-Agent override |
| `NEWSALPHA_LIVE` | `LiveMarketData` | set to `1` to enable live price loads. **Nothing live runs without it.** |
| `NEWSALPHA_COINGECKO_KEY` | `LiveMarketData` | optional CoinGecko demo key; raises rate limits |
| `NEWSALPHA_HTTP_TIMEOUT` | `LiveMarketData` | request timeout in seconds (default 20) |

No secrets are stored in the repository. A live fetch failure raises a clear
error — the tool never degrades to silently stale analysis.

**Optional dependency.** `RSSNewsFeed` needs `feedparser` (`uv pip install
feedparser`); it is imported lazily and raises a named error when absent.
`LiveMarketData` needs nothing beyond the stdlib. Neither live module is
imported on the offline path — a test asserts they stay out of `sys.modules`
after a full offline run.

## Determinism

The derived state is a pure function of (stored in-window articles, committed
datasets, `as_of`). All ids are content-derived, time is always an input, and
the only randomness is the placebo seed, which is hash-derived per signal. Two
guarantees are asserted by tests:

- **D0 determinism** — two runs over the same articles produce byte-identical
  exports and identical ids; re-ingesting writes zero new revisions.
- **D1 replay equivalence** — one batch and five daily batches of the same
  articles produce byte-identical clusters, events and links, and an identical
  latest scored tuple per (event type, asset, role). Only the revision *history*
  differs.
