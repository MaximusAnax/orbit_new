# NewsAlpha — Data Model

Two storage classes, per workspace conventions:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/newsalpha/data/`): asset gazetteer (`assets.json`), extraction
  patterns + suppression lexicons + frame-check lexicon (`patterns.json`),
  scoring priors (`priors.json`), brief templates (`templates.json`), source
  tiers (`source_tiers.json`). Loaded and validated into SQLite at
  `newsalpha init`; the files remain the source of truth.
- **SQLite** (user + pipeline state, default `~/.newsalpha/newsalpha.db`,
  path via `NEWSALPHA_DB`; in-memory backend for tests): articles, clusters,
  events, links, signals, briefs, price bars, backtest runs/results,
  watchlist. Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/newsalpha/models.py`; the store maps them
to the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers (the
engine never reads the clock). JSON-typed columns hold canonical
(sorted-keys, compact) serializations so byte-identity is well defined.

## Id scheme (FR-14)

All primary ids are content-derived: `hex16(x) = sha256(x)[:16]` over a
canonical natural-key string. This makes ingestion idempotent and replays
byte-identical.

| Entity | Id derivation |
|---|---|
| Article | `hex16(source_domain + "|" + external_id + "|" + content_hash)` |
| Cluster | `hex16("cluster|" + earliest member article_id)` |
| Event | `hex16("event|" + cluster_id + "|" + event_type)` |
| Signal | `hex16("signal|" + event_id + "|" + asset_id)` |
| Brief | `= signal_id` (1:1) |
| BacktestRun | `hex16("bt|" + canonical params JSON + "|" + as_of)` |

## Enumerations

| Enum | Values |
|---|---|
| `AssetKind` | `equity`, `crypto`, `index` |
| `EventType` | `earnings_surprise`, `guidance_change`, `mna`, `regulatory_action`, `listing`, `delisting`, `hack_exploit`, `partnership` |
| `Stage` | `rumored`, `confirmed`, `denied` |
| `LinkRole` | `subject`, `acquirer`, `target`, `venue`, `mentioned` |
| `Direction` | `bullish`, `bearish`, `unclear` |
| `Magnitude` | `minor`, `moderate`, `major` |
| `SourceTierName` | `t1_official`, `t2_wire`, `t3_other` |
| `FeedKind` | `fixture`, `rss` |
| `BarSource` | `fixture`, `live` |
| `ExclusionReason` | `insufficient_bars`, `unknown_asset_bars`, `estimated_publish_time`, `unclear_direction` |

## Committed datasets

### Asset — `data/assets.json` → table `asset`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `eq:AAPL`, `cx:BTC`, `idx:US` (kind prefix + canonical symbol; invariant: prefix matches `kind`) |
| `kind` | AssetKind | |
| `symbol` | str | ticker as matched in text (`AAPL`, `BTC`, `BRK.B`) |
| `name` | str | `Apple Inc.`, `Bitcoin` |
| `aliases` | list[str] | case-sensitive surface forms (`Apple`, `iPhone maker`, `NEAR Protocol`); unique across the gazetteer (invariant, checked at init) |
| `ambiguous` | bool | alias/symbol collides with common language (`NEAR`, `ONE`, `ICP`, `APE`, `COIN`, `Meta`, `Oracle`, `Shell`, `Visa`) |
| `context_keywords` | list[str] | required same-sentence evidence when `ambiguous` (invariant: non-empty iff `ambiguous`) — e.g. for `eq:AAPL`: `iPhone`, `Cupertino`, `Inc`, `shares`, `Nasdaq` |
| `benchmark_id` | str FK → asset | `idx:US` for equities, `idx:CX` for crypto; null for indexes (invariant) |

~150 US large-cap equities + ~50 top crypto assets + 2 benchmark indexes.
Editing this file (then `init`) is the supported way to extend the universe.

```json
{"id": "cx:NEAR", "kind": "crypto", "symbol": "NEAR", "name": "NEAR Protocol",
 "aliases": ["NEAR Protocol", "NEAR"], "ambiguous": true,
 "context_keywords": ["protocol", "blockchain", "token", "crypto", "layer-1", "staking"],
 "benchmark_id": "idx:CX"}
```

### EventPattern — `data/patterns.json` (loaded at init; no table)

Top-level file also carries the shared lexicons: `negation_cues`,
`hedge_cues`, `historical_guards`, `metaphor_stoplists` (per type),
`venue_lexicon`, `abbreviations` (sentence splitter), and
`forbidden_lexicon` (frame check, FR-7).

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `mna-agree-acquire` |
| `event_type` | EventType | |
| `triggers` | list[str] | word-boundary, case-folded lexemes/phrases (`agreed to acquire`, `takeover bid`) |
| `required_context` | list[str] | optional same-sentence terms; empty = none |
| `attribute_extractors` | dict[str, str] | attribute name → regex with one capture group (`premium_pct`: `(\d{1,3})\s?% premium`) |
| `polarity` | str \| null | fixed polarity this pattern implies (`beat`, `cut`, `favorable`…), when not regex-extracted |
| `notes` | str | maintainer guidance |

Invariants (checked at init): every `event_type` has ≥ 2 patterns; every
regex compiles and has ≤ 1 capture group; trigger sets are disjoint across
event types.

```json
{"id": "hack-funds-drained", "event_type": "hack_exploit",
 "triggers": ["exploited", "drained", "bridge hack", "funds stolen"],
 "required_context": [],
 "attribute_extractors": {"amount_usd": "\\$([0-9][0-9,.]*)\\s?(?:million|billion)?"},
 "polarity": null, "notes": "metaphor stoplist covers growth hack / hackathon / life hack"}
```

### EventPrior — `data/priors.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `key` | str PK | `event_type.role.polarity`, e.g. `mna.target.confirmed`, `earnings_surprise.subject.miss`; `*` polarity allowed |
| `direction` | Direction | |
| `magnitude` | Magnitude | derived from the AR band: minor < 2 %, moderate 2–8 %, major > 8 % (invariant, checked at init) |
| `expected_ar_lo`, `expected_ar_hi` | float | expected abnormal-return band (signed, e.g. 0.16–0.24 for M&A target); `mid_expected_ar` = midpoint, used in `score` |
| `horizon_bars` | int | 1, 5, or 20 |
| `base_conf` | float | 0.05–0.95 |
| `rationale` | str | plain-language base-rate text, rendered verbatim into briefs' `why_it_matters` |
| `source_note` | str | grounding, e.g. `Andrade, Mitchell & Stafford 2001 (JEP): target CAR +16-24%, acquirer ~0 to -1%` |

Invariant: every (event_type, signal-bearing role, polarity value reachable
from any pattern) resolves to exactly one prior (checked at init).

```json
{"key": "earnings_surprise.subject.beat", "direction": "bullish",
 "magnitude": "moderate", "expected_ar_lo": 0.02, "expected_ar_hi": 0.06,
 "horizon_bars": 20, "base_conf": 0.75,
 "rationale": "Positive earnings surprises have historically been followed by continued drift in the same direction over the following weeks, not just a one-day pop.",
 "source_note": "Ball & Brown 1968; Bernard & Thomas 1989 (post-earnings announcement drift)"}
```

### BriefTemplate — `data/templates.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `mna-target-confirmed` |
| `event_type` | EventType | |
| `direction` | Direction \| null | null = any |
| `what_happened` | str | format string over event/link fields (`{asset_name} {trigger_summary} ({stage})`) |
| `why_it_matters` | str | format string; must include `{prior_rationale}` |
| `what_to_watch` | list[str] | 2–4 type-specific watch items (`definitive agreement or denial`, `filed 8-K`, `official post-mortem`, `reimbursement plan`) |
| `uncertainty_note` | str | format string; must include `{confidence}` and `{falsifier}` |

Invariants: every (event_type, direction incl. null-fallback) resolvable;
all four sections non-empty; no forbidden-lexicon word appears in any
template (checked at init). The not-advice footer is a single constant in
`templates.json` (`footer`), rendered verbatim on every brief.

### SourceTier — `data/source_tiers.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `domain` | str PK | `sec.gov`, `reuters.com`, `coindesk.com`, … |
| `tier` | SourceTierName | unlisted domains default to `t3_other` |

Tier weights (`t1` 1.00, `t2` 0.85, `t3` 0.60) live beside the mapping in
the same file.

## SQLite entities

### Article — `article` (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived (id scheme) |
| `external_id` | str | feed guid or URL; unique with `source_domain` |
| `url` | str \| null | |
| `source_domain` | str | registrable domain, lowercased |
| `tier` | SourceTierName | resolved at ingest from `source_tiers.json` |
| `published_at` | str | ISO UTC |
| `published_at_estimated` | bool | true when the feed lacked a timestamp (signals from such articles are backtest-excluded, SCOPE D-19) |
| `fetched_at` | str | edge-supplied |
| `title`, `body` | str | normalized (FR-1) |
| `content_hash` | str | sha256 hex of normalized title+body; unique |
| `cluster_id` | str FK → cluster | assigned by FR-2 |

Invariants: append-only (never updated after insert, except `cluster_id`
assignment during the same ingest transaction); unique
(`source_domain`, `external_id`); unique `content_hash`.

```json
{"id": "9f2ab41c77d0e3a1", "external_id": "https://coindesk.example/ronin-2",
 "url": "https://coindesk.example/ronin-2", "source_domain": "coindesk.example",
 "tier": "t2_wire", "published_at": "2026-03-14T08:12:00Z",
 "published_at_estimated": false, "fetched_at": "2026-03-14T09:00:00Z",
 "title": "Nomad-style exploit drains $47 million from HypoBridge",
 "body": "HypoBridge, the cross-chain bridge of the Hypothetica protocol, was exploited early Saturday... The HYPO token fell as holders...",
 "content_hash": "c0ffee…", "cluster_id": "1a2b3c4d5e6f7081"}
```

### Cluster — `cluster`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from earliest member article id |
| `earliest_published_at` | str | = event time for attached events |
| `article_count` | int | maintained on merge (≥ 1) |
| `corroboration` | int | distinct source domains (≥ 1; derived, stored for query) |
| `best_tier` | SourceTierName | max tier across members (t1 > t2 > t3) |

### Event — `event` (append-only; merge appends evidence only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from (cluster, type) |
| `cluster_id` | str FK | unique with `event_type` (invariant: ≤ 1 event per cluster per type) |
| `event_type` | EventType | |
| `stage` | Stage | |
| `attributes` | JSON | typed per event-type schema (SCOPE typology table); unknown keys rejected |
| `extraction_confidence` | float | 0.05–0.95 per FR-4 rules |
| `evidence` | JSON list | `{article_id, start, end, quote}`; invariant: ≥ 1 entry; quotes must equal `body[start:end]` (checked) |
| `event_time` | str | = cluster `earliest_published_at` |

```json
{"id": "77aa0b12cd34ef56", "cluster_id": "1a2b3c4d5e6f7081",
 "event_type": "hack_exploit", "stage": "confirmed",
 "attributes": {"amount_usd": 47000000, "vector": "bridge"},
 "extraction_confidence": 0.95,
 "evidence": [{"article_id": "9f2ab41c77d0e3a1", "start": 0, "end": 62,
               "quote": "HypoBridge, the cross-chain bridge of the Hypothetica protocol, was exploited"}],
 "event_time": "2026-03-14T08:12:00Z"}
```

### EventLink — `event_link`

| Field | Type | Notes |
|---|---|---|
| `event_id` | str FK PK-part | |
| `asset_id` | str FK PK-part | composite PK (event, asset) |
| `role` | LinkRole | |
| `link_confidence` | float | 0.7–0.95 per FR-5 evidence classes |
| `evidence` | JSON | one `{article_id, start, end, quote}` span |

Invariants: an `mna` event has ≤ 1 `acquirer` and ≤ 1 `target`; a
`listing`/`delisting` event has ≤ 1 `venue`; `mentioned` links never have a
signal (enforced at scoring, checked by tests).

### Signal — `signal` (immutable)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from (event, asset) |
| `event_id` | str FK | unique with `asset_id` |
| `asset_id` | str FK | |
| `role` | LinkRole | `subject` \| `acquirer` \| `target` only (invariant) |
| `direction` | Direction | |
| `magnitude` | Magnitude | |
| `confidence` | float | invariant: `0.05 ≤ confidence ≤ 0.95` (CHECK constraint) |
| `horizon_bars` | int | 1 \| 5 \| 20 |
| `expected_ar_lo`, `expected_ar_hi` | float | copied from the prior at creation (priors may evolve; signals must not) |
| `score` | float | `dir_sign · mid_expected_ar · confidence`; 0 when `unclear`; stored (derived, but frozen with the signal) |
| `prior_key` | str | e.g. `mna.target.rumored` |
| `rationale_codes` | JSON list[str] | every factor applied: `prior:mna.target.confirmed`, `mod:stage=rumored`, `mod:tier=t2_wire`, `mod:corroboration=3`, `mod:extraction=0.95`, `mod:link=0.85` |
| `created_as_of` | str | the ingest `as_of` |

Invariant: rows are never updated or deleted; re-ingest that would produce
the same id is a no-op (idempotency).

```json
{"id": "d00d1e55aa11bb22", "event_id": "77aa0b12cd34ef56", "asset_id": "cx:HYPO",
 "role": "subject", "direction": "bearish", "magnitude": "major",
 "confidence": 0.68, "horizon_bars": 5,
 "expected_ar_lo": -0.20, "expected_ar_hi": -0.10, "score": -0.102,
 "prior_key": "hack_exploit.subject.*",
 "rationale_codes": ["prior:hack_exploit.subject.*", "mod:stage=confirmed",
                     "mod:tier=t2_wire", "mod:corroboration=2",
                     "mod:extraction=0.95", "mod:link=0.85"],
 "created_as_of": "2026-03-14T09:00:00Z"}
```

(Arithmetic check, per the FR-6 formula: 0.90 base × 0.85 tier × 1.00 stage
× 1.10 corroboration × 0.95 extraction × 0.85 link = 0.679 → 0.68;
score = mid(−0.20, −0.10) × 0.68 = −0.102. Example records must stay
formula-verifiable.)

### Brief — `brief` (immutable, 1:1 with signal)

| Field | Type | Notes |
|---|---|---|
| `signal_id` | str PK FK | |
| `template_id` | str | provenance |
| `what_happened`, `why_it_matters`, `what_to_watch`, `uncertainty_note` | str / JSON list / str | the four sections; all non-empty (invariant) |
| `rendered_text` | str | full plain-text rendering incl. footer |
| `frame_checked` | bool | CHECK `frame_checked = 1` — a failing brief cannot exist in the store (FR-7 safeguard) |

```json
{"signal_id": "d00d1e55aa11bb22", "template_id": "hack-subject-bearish",
 "what_happened": "Hypothetica (HYPO): bridge exploit, ~$47M drained (confirmed). \"HypoBridge, the cross-chain bridge of the Hypothetica protocol, was exploited\" — coindesk.example.",
 "why_it_matters": "Tokens of exploited protocols have historically sold off sharply in the days after an exploit, with the drawdown scaling with the amount lost and the protocol's TVL.",
 "what_to_watch": ["official post-mortem", "reimbursement or treasury backstop plan", "TVL outflows", "exchange deposit freezes"],
 "uncertainty_note": "Confidence 0.68. This weakens if funds are recovered or a credible reimbursement plan lands quickly.",
 "rendered_text": "…\n\nThis is information, not investment advice. NewsAlpha describes evidence and uncertainty; it does not recommend trades.",
 "frame_checked": true}
```

### PriceBar — `price_bar`

| Field | Type | Notes |
|---|---|---|
| `asset_id` | str FK PK-part | |
| `date` | str PK-part | ISO date; unique (asset_id, date) |
| `open`, `high`, `low`, `close` | float | invariant: `low ≤ open, close ≤ high`; all > 0 |
| `volume` | float | ≥ 0 |
| `source` | BarSource | fixture bars are never overwritten by live loads (invariant) |

```json
{"asset_id": "cx:HYPO", "date": "2026-03-16", "open": 3.10, "high": 3.14,
 "low": 2.55, "close": 2.61, "volume": 8412000.0, "source": "fixture"}
```

### BacktestRun — `backtest_run` / BacktestResult — `backtest_result`

`backtest_run`:

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from canonical params + as_of |
| `params` | JSON | `{start, end, min_confidence, placebo_seed: int \| null}` |
| `as_of` | str | |
| `aggregates` | JSON | overall + per-event-type: `{n, n_excluded, hit_rate, mean_ar, ic_spearman, buckets: {lo: {n, hit}, mid: …, hi: …}}` |

`backtest_result` (one row per considered signal):

| Field | Type | Notes |
|---|---|---|
| `run_id` | str FK PK-part | |
| `signal_id` | str FK PK-part | |
| `entry_date` | str \| null | first bar strictly after publication (invariant: `entry_date > date(published_at)` when set); the *displaced* date in placebo runs |
| `ar` | JSON | `{"1": float, "5": float, "20": float}` for horizons with enough bars |
| `hit` | bool \| null | at the signal's own horizon |
| `excluded_reason` | ExclusionReason \| null | set ⇔ `hit` is null; excluded rows are counted in `aggregates.n_excluded` |

Invariant: runs and results are append-only; a run never mutates signals or
bars.

### WatchlistItem — `watchlist_item`

| Field | Type | Notes |
|---|---|---|
| `asset_id` | str PK FK → asset | |
| `added_at` | str | edge-supplied |

## Relationships (summary)

```
asset 1—* event_link *—1 event *—1 cluster 1—* article
asset 1—* signal    *—1 event
signal 1—1 brief
asset 1—* price_bar
backtest_run 1—* backtest_result *—1 signal
asset 1—* watchlist_item (0/1 per asset)
asset.benchmark_id —* asset (idx rows)
```

Deletion policy: nothing user-facing deletes domain rows; `init --reset`
recreates the DB wholesale. Watchlist rows are the only deletable entity.
