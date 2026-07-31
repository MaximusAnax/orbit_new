# NewsAlpha — Data Model

Three storage classes:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/newsalpha/data/`): asset gazetteer (`assets.json`), extraction
  patterns + suppression lexicons + frame-check lexicon (`patterns.json`),
  scoring priors (`priors.json`), brief templates (`templates.json`), source
  tiers (`source_tiers.json`), live crypto benchmark basket
  (`benchmarks.json`). Loaded and validated into memory at `newsalpha init`;
  the files remain the source of truth.
- **Durable SQLite rows** — append-only, never updated or deleted: `article`,
  `signal`, `signal_key_alias`, `brief`, `price_bar`, `backtest_run`,
  `backtest_result`, `watchlist_item` (watchlist rows are the single
  exception: they are deletable).
- **Derived SQLite rows** — deleted and re-inserted by every active-window
  recompute (FR-15): `cluster`, `event`, `event_link`. They are a
  deterministic view of the append-only articles, so nothing is lost by
  re-deriving them; every signal carries its own `event_snapshot` so it stays
  auditable independently.

Default DB `~/.newsalpha/newsalpha.db`, path via `NEWSALPHA_DB`; an in-memory
backend is used by tests and evals. Repository pattern; stdlib `sqlite3`.

All models are Pydantic v2 in `src/newsalpha/models.py`; the store maps them
to the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers (the
engine never reads the clock). JSON-typed columns hold canonical
(sorted-keys, compact) serializations so byte-identity is well defined.

## Id scheme (FR-14/FR-15)

All primary ids are content-derived: `hex16(x) = sha256(x)[:16]` over a
canonical natural-key string.

| Entity | Id derivation |
|---|---|
| Article | `hex16(source_domain + "\|" + external_id + "\|" + content_hash)` |
| Cluster | `hex16("cluster\|" + article_id of the member minimizing (published_at, article_id))` — over *final* membership, which FR-2's transitive closure makes order-independent |
| Event | `hex16("event\|" + cluster_id + "\|" + event_type)` |
| Signal key | `hex16("sigkey\|" + event_type + "\|" + asset_id + "\|" + role + "\|" + event_date)` (`event_date` = UTC date of the cluster's earliest article) |
| Signal | `hex16("signal\|" + signal_key + "\|" + revision)` |
| Brief | `= signal_id` (1:1 with a signal *revision*) |
| BacktestRun | `hex16("bt\|" + canonical params JSON + "\|" + as_of)` |

Because cluster identity depends only on final membership, batch and
incremental ingests of the same articles produce the same cluster, event and
link ids (FR-14 D1). Signal *keys* may differ across partitionings only via
the key-continuity alias (FR-15); their latest scored tuples must not.

## Enumerations

| Enum | Values |
|---|---|
| `AssetKind` | `equity`, `crypto`, `index` |
| `EventType` | `earnings_surprise`, `guidance_change`, `mna`, `regulatory_action`, `listing`, `delisting`, `hack_exploit` |
| `Stage` | `rumored`, `confirmed`, `denied` |
| `LinkRole` | `subject`, `acquirer`, `target`, `venue`, `mentioned` |
| `Direction` | `bullish`, `bearish`, `unclear` |
| `Magnitude` | `minor`, `moderate`, `major` |
| `SourceTierName` | `t1_official`, `t2_wire`, `t3_other` |
| `FeedKind` | `fixture`, `rss` |
| `BarSource` | `fixture`, `live` |
| `ExclusionReason` | `insufficient_bars`, `unknown_asset_bars`, `benchmark_gap`, `zero_abnormal_return`, `estimated_publish_time`, `placebo_no_clean_window` |

There is deliberately no `unclear_direction` exclusion: an `unclear`
resolution emits no signal at all (FR-6), so there is nothing to exclude. The
"resolve hard cases as unclear to shrink the denominator" dodge is closed by
EVALS.md's **G1** floor on `N_directional`, not by an exclusion code. Every
abstention is instead recorded on the derived event's `notes`
(`score:unclear_abstain`, `link:mna_role_unresolved`, `link:ambiguous_subject`)
so it is visible and countable.

`partnership` is deliberately absent from `EventType` (SCOPE non-goal 11).

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
| `benchmark_id` | str FK → asset | `idx:US` for equities, `idx:CX` for crypto; null for indexes (invariant: set iff `kind != index`) |

~150 US large-cap equities + ~50 top crypto assets + 2 benchmark indexes.
Editing this file (then `init`) is the supported way to extend the universe.
Venue surfaces that are also assets (`Coinbase` → `eq:COIN`, `Nasdaq` →
`eq:NDAQ`, `NYSE` → `eq:ICE`) are ordinary gazetteer entries; FR-5's venue
precedence rule — not the gazetteer — keeps them out of the `subject` role on
listing/delisting events.

```json
{"id": "cx:NEAR", "kind": "crypto", "symbol": "NEAR", "name": "NEAR Protocol",
 "aliases": ["NEAR Protocol", "NEAR"], "ambiguous": true,
 "context_keywords": ["protocol", "blockchain", "token", "crypto", "layer-1", "staking"],
 "benchmark_id": "idx:CX"}
```

### EventPattern — `data/patterns.json` (loaded at init; no table)

Top-level file also carries the shared lexicons: `negation_cues`,
`hedge_cues`, `historical_guards`, `metaphor_stoplists` (per type),
`venue_lexicon` (surface → optional gazetteer asset id),
`abbreviations` (sentence splitter), and `forbidden_lexicon` (frame check,
FR-7).

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `mna-agree-acquire` |
| `event_type` | EventType | |
| `triggers` | list[str] | word-boundary, case-folded lexemes/phrases (`agreed to acquire`, `takeover bid`) |
| `required_context` | list[str] | optional same-sentence terms; empty = none |
| `attribute_extractors` | dict[str, str] | attribute name → regex with one capture group (`premium_pct`: `(\d{1,3})\s?% premium`) |
| `polarity` | str \| null | fixed polarity this pattern implies (`beat`, `cut`, `favorable`…), when not regex-extracted |
| `real_world_example` | str | **required, non-empty** — a real headline phrasing this pattern generalizes from. The anti-overfit maintainer rule (SCOPE FR-3.2, EVALS.md): a pattern that can only cite a fixture template is a pattern tuned to the fixtures |
| `notes` | str | maintainer guidance |

Invariants (checked at init): every `event_type` has ≥ 2 patterns; every
regex compiles and has ≤ 1 capture group; trigger sets are disjoint across
event types; `real_world_example` non-empty.

```json
{"id": "hack-funds-drained", "event_type": "hack_exploit",
 "triggers": ["exploited", "drained", "bridge hack", "funds stolen"],
 "required_context": [],
 "attribute_extractors": {"amount_usd": "\\$([0-9][0-9,.]*)\\s?(?:million|billion)?"},
 "polarity": null,
 "real_world_example": "Ronin bridge hacked for $625 million in crypto's biggest exploit",
 "notes": "metaphor stoplist covers growth hack / hackathon / life hack"}
```

### EventPrior — `data/priors.json` (loaded at init; no table)

Resolution key is `event_type.role.polarity` **plus** an asset-kind
qualifier; stage is *never* part of the key.

| Field | Type | Notes |
|---|---|---|
| `key` | str | `event_type.role.polarity`, e.g. `mna.target.*`, `earnings_surprise.subject.miss`. `polarity = *` means "any". **Invariant: no token of the key may be a `Stage` value.** |
| `kind` | `"equity"` \| `"crypto"` \| `"*"` | asset-kind qualifier; resolution is most-specific-wins (a `kind`-specific row beats `kind: "*"`). (`key`, `kind`) is the primary key |
| `direction` | Direction | |
| `magnitude` | Magnitude | invariant: matches the band — \|mid_expected_ar\| < 0.01 minor, 0.01–0.04 moderate, > 0.04 major |
| `expected_ar_lo`, `expected_ar_hi` | float | **post-entry** expected abnormal-return band over `horizon_bars`, signed. `mid_expected_ar` = midpoint, the only band the engine scores. Invariant: `sign(mid_expected_ar) == dir_sign` |
| `announcement_ar_lo`, `announcement_ar_hi` | float | the literature's announcement-window CAR. **Never scored** — rendered into briefs as context (FR-7) so the user sees why the post-entry band is so much smaller |
| `horizon_bars` | int | 1, 5, or 20 |
| `base_conf` | float | 0.05–0.95 |
| `supersession_days` | int | window for FR-15's cross-cluster supersession (default 21) |
| `stage_overrides` | dict[Stage, override] \| null | optional per-stage override of `{direction, magnitude, expected_ar_lo, expected_ar_hi, horizon_bars}`. **The only stage effect on direction/band/horizon.** `direction: "unclear"` means "emit no signal at this stage" |
| `rationale` | str | plain-language base-rate text, rendered verbatim into briefs' `why_it_matters` |
| `source_note` | str | grounding, e.g. `Andrade, Mitchell & Stafford 2001 (JEP): target CAR +16-24%, acquirer ~0 to -1%` |

Init invariants (SCOPE FR-3.3): for every (event_type, signal-bearing role,
polarity reachable from any pattern, kind ∈ {equity, crypto}) exactly one row
resolves; every `Stage` reachable for that event type is covered by the base
row or a `stage_overrides` entry; magnitude and sign invariants hold for the
base row *and* every override.

```json
{"key": "mna.target.*", "kind": "*",
 "direction": "bullish", "magnitude": "moderate",
 "expected_ar_lo": 0.010, "expected_ar_hi": 0.030,
 "announcement_ar_lo": 0.16, "announcement_ar_hi": 0.24,
 "horizon_bars": 20, "base_conf": 0.80, "supersession_days": 21,
 "stage_overrides": {
   "rumored": {"direction": "bullish", "magnitude": "moderate",
               "expected_ar_lo": 0.010, "expected_ar_hi": 0.050, "horizon_bars": 5},
   "denied":  {"direction": "bearish", "magnitude": "moderate",
               "expected_ar_lo": -0.045, "expected_ar_hi": -0.015, "horizon_bars": 5}},
 "rationale": "Announced takeover targets historically jump 16-24% on the day of the announcement; almost all of that is priced before the next open. What remains afterwards is the smaller spread between the trading price and the offer, which closes only if the deal completes.",
 "source_note": "Andrade, Mitchell & Stafford 2001 (JEP): target CAR +16-24%, acquirer ~0 to -1%"}
```

```json
{"key": "listing.subject.*", "kind": "crypto",
 "direction": "bullish", "magnitude": "major",
 "expected_ar_lo": 0.025, "expected_ar_hi": 0.065,
 "announcement_ar_lo": 0.10, "announcement_ar_hi": 0.30,
 "horizon_bars": 5, "base_conf": 0.70, "supersession_days": 21,
 "stage_overrides": {"rumored": {"direction": "bullish", "magnitude": "moderate",
                                 "expected_ar_lo": 0.010, "expected_ar_hi": 0.040,
                                 "horizon_bars": 5}},
 "rationale": "Tokens newly listed on a major exchange have historically shown a large announcement pop followed by several days of continued, smaller drift as access widens.",
 "source_note": "Messari 2021 'Coinbase effect'; Binance-listing event studies"}
```

### BriefTemplate — `data/templates.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | e.g. `mna-target-bullish` |
| `event_type` | EventType | |
| `direction` | Direction \| null | null = any |
| `what_happened` | str | format string over event/link fields (`{asset_name} {trigger_summary} ({stage})`) |
| `why_it_matters` | str | format string; must include `{prior_rationale}` |
| `what_to_watch` | list[str] | 2–4 type-specific watch items (`definitive agreement or denial`, `filed 8-K`, `official post-mortem`, `reimbursement plan`) |
| `uncertainty_note` | str | format string; must include `{confidence}` and `{falsifier}`; must include `{already_priced_note}` when the prior's announcement band exceeds its post-entry band by > 3× |

Invariants: every (event_type, resolved direction incl. null-fallback)
resolvable; all four sections non-empty; no forbidden-lexicon word appears in
any template (word-boundary match, checked at init). The not-advice footer is
a single constant in `templates.json` (`footer`), rendered verbatim on every
brief.

### SourceTier — `data/source_tiers.json` (loaded at init; no table)

| Field | Type | Notes |
|---|---|---|
| `domain` | str PK | `sec.gov`, `reuters.com`, `coindesk.com`, … |
| `tier` | SourceTierName | unlisted domains default to `t3_other` |

Tier weights (`t1` 1.00, `t2` 0.85, `t3` 0.60) live beside the mapping in
the same file.

### Benchmark basket — `data/benchmarks.json` (loaded at init; no table)

`{"idx:CX": ["cx:BTC", "cx:ETH", "cx:SOL", "cx:XRP", "cx:ADA", "cx:AVAX",
"cx:LINK", "cx:DOT"], "idx:US": ["live_proxy: SPY"]}` — used only by
`LiveMarketData` (FR-9) to synthesize `idx:CX` as the equal-weighted mean of
member daily log returns, so no crypto asset is its own benchmark.

## SQLite entities

### Article — `article` (durable, append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | content-derived (id scheme) |
| `external_id` | str | feed guid or URL; unique with `source_domain` |
| `url` | str \| null | |
| `source_domain` | str | registrable domain, lowercased |
| `tier` | SourceTierName | resolved at ingest from `source_tiers.json` |
| `published_at` | str | ISO UTC |
| `published_at_estimated` | bool | true when the feed lacked a timestamp (signals from such articles are backtest-excluded, SCOPE D-19) |
| `excluded_from_analysis` | bool | set once at insert: true iff `published_at < as_of − active_window_days` (FR-1/FR-15). Such articles are archived only — never clustered, extracted or scored |
| `fetched_at` | str | edge-supplied |
| `title`, `body` | str | normalized (FR-1) |
| `content_hash` | str | sha256 hex of normalized title+body; unique |

Invariants: rows are never updated after insert; unique
(`source_domain`, `external_id`); unique `content_hash`. **Cluster membership
is not stored on the article** — it is derived state, read from `cluster_member`.

```json
{"id": "9f2ab41c77d0e3a1", "external_id": "https://coindesk.example/ronin-2",
 "url": "https://coindesk.example/ronin-2", "source_domain": "coindesk.example",
 "tier": "t2_wire", "published_at": "2026-03-14T08:12:00Z",
 "published_at_estimated": false, "excluded_from_analysis": false,
 "fetched_at": "2026-03-14T09:00:00Z",
 "title": "Nomad-style exploit drains $47 million from HypoBridge",
 "body": "HypoBridge, the cross-chain bridge of the Hypothetica protocol, was exploited early Saturday... The HYPO token fell as holders...",
 "content_hash": "c0ffee…"}
```

### Cluster — `cluster` + `cluster_member` (derived)

`cluster`:

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from final membership (id scheme) |
| `earliest_published_at` | str | min over members; its UTC date is the `event_date` used in signal keys |
| `latest_published_at` | str | max over members; the basis for a revision's `observed_at` |
| `article_count` | int | ≥ 1 |
| `corroboration` | int | distinct source domains (≥ 1) |
| `best_tier` | SourceTierName | max tier across members (t1 > t2 > t3) |

`cluster_member`: (`cluster_id`, `article_id`) composite PK.

### Event — `event` (derived)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from (cluster, type) |
| `cluster_id` | str FK | unique with `event_type` (invariant: ≤ 1 event per cluster per type) |
| `event_type` | EventType | |
| `stage` | Stage | per FR-4's merge rule: from the **latest** trigger-bearing article carrying a stage cue (tie: largest article_id); negation beats hedge within one sentence |
| `attributes` | JSON | typed per event-type schema (SCOPE typology table); unknown keys rejected; on merge, first non-null in (published_at, article_id) order wins |
| `extraction_confidence` | float | 0.05–0.95 per FR-4; on merge, max over contributing articles |
| `evidence` | JSON list | `{article_id, start, end, quote}`, appended in (published_at, article_id) order; invariant: ≥ 1 entry; quotes must equal `body[start:end]` (checked) |
| `notes` | JSON list[str] | e.g. `link:mna_role_unresolved`, `link:ambiguous_subject` |
| `event_date` | str | UTC date of `cluster.earliest_published_at` |
| `observed_at` | str | `cluster.latest_published_at` — the earliest moment the whole evidence set existed |

**FR-4's merge rule is stated identically here and in SCOPE FR-4; if the two
ever diverge, SCOPE is authoritative and this table is the bug.**

```json
{"id": "77aa0b12cd34ef56", "cluster_id": "1a2b3c4d5e6f7081",
 "event_type": "hack_exploit", "stage": "confirmed",
 "attributes": {"amount_usd": 47000000, "vector": "bridge"},
 "extraction_confidence": 0.95,
 "evidence": [{"article_id": "9f2ab41c77d0e3a1", "start": 0, "end": 76,
               "quote": "HypoBridge, the cross-chain bridge of the Hypothetica protocol, was exploited"}],
 "notes": [], "event_date": "2026-03-14", "observed_at": "2026-03-14T19:40:00Z"}
```

### EventLink — `event_link` (derived)

| Field | Type | Notes |
|---|---|---|
| `event_id` | str FK PK-part | |
| `asset_id` | str FK PK-part | composite PK (event, asset) |
| `role` | LinkRole | |
| `link_confidence` | float | 0.7–0.95 per FR-5 evidence classes (0.7 inside an all-caps sentence) |
| `evidence` | JSON | one `{article_id, start, end, quote}` span |

Invariants:
- an `mna` event has ≤ 1 `acquirer` and ≤ 1 `target`; if both cannot be
  resolved, **neither** exists and every party is `mentioned` (FR-5 fallback);
- a `listing`/`delisting` event has ≤ 1 `venue` and ≤ 1 `subject`; a span
  matched by the venue lexicon is consumed by the venue match and may not
  back any other role's link on that event; if the venue surface is itself a
  gazetteer asset it is linked with `role = venue`, never `subject`;
- `mentioned` and `venue` links never produce a signal (enforced at scoring,
  checked by tests).

### Signal — `signal` (durable, append-only, versioned)

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `hex16("signal\|" + signal_key + "\|" + revision)` |
| `signal_key` | str | stable identity across revisions (id scheme); indexed |
| `revision` | int | ≥ 1; unique with `signal_key` |
| `supersedes` | str \| null | id of the previous revision of the same key |
| `supersedes_key` | str \| null | signal_key of an **older, different-stage** signal this one supersedes (FR-15); null otherwise |
| `event_id` | str | the derived event this revision was scored from (may later be re-derived; the snapshot below is the durable record) |
| `asset_id` | str FK | |
| `role` | LinkRole | `subject` \| `acquirer` \| `target` only (invariant) |
| `direction` | Direction | invariant: never `unclear` — an `unclear` resolution emits no signal at all |
| `magnitude` | Magnitude | |
| `confidence` | float | invariant: `0.05 ≤ confidence ≤ 0.95` (CHECK constraint) |
| `horizon_bars` | int | 1 \| 5 \| 20 |
| `expected_ar_lo`, `expected_ar_hi` | float | **post-entry** band copied from the resolved prior/override at creation (priors may evolve; signals must not) |
| `score` | float | `mid_expected_ar · confidence`; stored (derived, but frozen with the revision) |
| `prior_key` | str | e.g. `mna.target.*` (never contains a stage token) |
| `rationale_codes` | JSON list[str] | every factor applied: `prior:mna.target.*`, `mod:stage_override=denied`, `mod:f_stage=0.70`, `mod:tier=t2_wire`, `mod:corroboration=3`, `mod:extraction=0.95`, `mod:link=0.85` |
| `event_snapshot` | JSON | `{event_type, stage, attributes, corroboration, best_tier, extraction_confidence, link_confidence, evidence_article_ids, event_date}` — makes the revision auditable after the derived event row is re-derived |
| `observed_at` | str | max `published_at` over the evidence articles behind this revision; the backtest entry anchor (FR-10) |
| `created_as_of` | str | the ingest `as_of` that produced this revision |

Invariants: rows are never updated or deleted. A recompute that reproduces
the latest revision's scored tuple `(direction, magnitude, horizon_bars,
round(confidence, 4), prior_key)` writes nothing (idempotency, US-1). The
latest revision of a key is `max(revision)`; no row is mutated to flag it.

`signal_key_alias` (durable): (`from_key` PK, `to_key`, `created_as_of`) —
records FR-15's key-continuity absorption when a late, earlier-dated article
shifts a cluster's `event_date` by ≤ 2 days.

```json
{"id": "d00d1e55aa11bb22", "signal_key": "5b71c0a9e4d2f318", "revision": 2,
 "supersedes": "8c40aa17b9e0d251", "supersedes_key": null,
 "event_id": "77aa0b12cd34ef56", "asset_id": "cx:HYPO", "role": "subject",
 "direction": "bearish", "magnitude": "major", "confidence": 0.68,
 "horizon_bars": 5, "expected_ar_lo": -0.065, "expected_ar_hi": -0.025,
 "score": -0.0306, "prior_key": "hack_exploit.subject.*",
 "rationale_codes": ["prior:hack_exploit.subject.*", "mod:kind=crypto",
                     "mod:f_stage=1.00", "mod:tier=t2_wire",
                     "mod:corroboration=2", "mod:extraction=0.95", "mod:link=0.85"],
 "event_snapshot": {"event_type": "hack_exploit", "stage": "confirmed",
                    "attributes": {"amount_usd": 47000000, "vector": "bridge"},
                    "corroboration": 2, "best_tier": "t2_wire",
                    "extraction_confidence": 0.95, "link_confidence": 0.85,
                    "evidence_article_ids": ["9f2ab41c77d0e3a1", "3ac9017fbe2d4406"],
                    "event_date": "2026-03-14"},
 "observed_at": "2026-03-14T19:40:00Z", "created_as_of": "2026-03-15T07:00:00Z"}
```

Arithmetic check (FR-6 formula, kept verifiable in review):
`base_conf 0.90 × w_tier 0.85 × f_stage 1.00 × (1 + 0.10 × min(2−1, 3)) 1.10
× extraction 0.95 × link 0.85 = 0.6794 → 0.68`;
`score = mid(−0.065, −0.025) × 0.68 = −0.045 × 0.68 = −0.0306`.

### Brief — `brief` (durable, immutable, 1:1 with a signal revision)

| Field | Type | Notes |
|---|---|---|
| `signal_id` | str PK FK | one brief per revision |
| `template_id` | str | provenance |
| `what_happened`, `why_it_matters`, `what_to_watch`, `uncertainty_note` | str / str / JSON list / str | the four sections; all non-empty (invariant) |
| `rendered_text` | str | full plain-text rendering incl. footer |
| `frame_checked` | bool | CHECK `frame_checked = 1` — a failing brief cannot exist in the store (FR-7 safeguard) |

```json
{"signal_id": "d00d1e55aa11bb22", "template_id": "hack-subject-bearish",
 "what_happened": "Hypothetica (HYPO): bridge exploit, ~$47M drained (confirmed). \"HypoBridge, the cross-chain bridge of the Hypothetica protocol, was exploited\" — coindesk.example.",
 "why_it_matters": "Tokens of exploited protocols have historically sold off sharply on the day of disclosure (roughly -10% to -25%), with a smaller continued decline over the following days as the scale of the loss is confirmed.",
 "what_to_watch": ["official post-mortem", "reimbursement or treasury backstop plan", "TVL outflows", "exchange deposit freezes"],
 "uncertainty_note": "Confidence 0.68. Most of the disclosure-day drop is already in the price by the time this brief is read; the band above covers only what came after. This weakens if funds are recovered or a credible reimbursement plan lands quickly.",
 "rendered_text": "…\n\nThis is information, not investment advice. NewsAlpha describes evidence and uncertainty; it does not recommend trades.",
 "frame_checked": true}
```

### PriceBar — `price_bar` (durable)

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

### BacktestRun — `backtest_run` / BacktestResult — `backtest_result` (durable)

`backtest_run`:

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | derived from canonical params + as_of |
| `params` | JSON | `{start, end, min_confidence, placebo_seed: int \| null}` |
| `as_of` | str | |
| `aggregates` | JSON | overall + per-event-type: `{n, n_excluded, excluded_by_reason: {…}, n_superseded, hit_rate, mean_ar, ic_spearman, buckets: {lo: {n, hit}, mid: …, hi: …}}` |

`backtest_result` (one row per considered signal revision):

| Field | Type | Notes |
|---|---|---|
| `run_id` | str FK PK-part | |
| `signal_id` | str FK PK-part | the latest revision of its key |
| `entry_date` | str \| null | first available bar strictly after `date(signal.observed_at)` (invariant: `entry_date > date(observed_at)` when set); the *displaced* date in placebo runs |
| `exit_date` | str \| null | the `(h − 1)`-th available bar after `entry_date` |
| `ar` | float \| null | abnormal return at the signal's own horizon, benchmark read at `entry_date`/`exit_date` |
| `hit` | bool \| null | null iff `excluded_reason` is set |
| `excluded_reason` | ExclusionReason \| null | set ⇔ `hit` is null; counted in `aggregates.excluded_by_reason` |
| `placebo_attempts` | int | re-draws used (0 in real runs; ≤ 8 in placebo runs) |

Invariant: runs and results are append-only; a run never mutates signals or
bars. Superseded signals are evaluated like any other and additionally
counted in `aggregates.n_superseded`.

### WatchlistItem — `watchlist_item` (durable, deletable)

| Field | Type | Notes |
|---|---|---|
| `asset_id` | str PK FK → asset | |
| `added_at` | str | edge-supplied |

## Relationships (summary)

```
article *—* cluster            (via cluster_member; derived)
cluster 1—* event              (≤ 1 per event_type; derived)
event   1—* event_link *—1 asset   (derived)
signal_key 1—* signal (revisions, durable)   signal *—1 asset
signal  1—1 brief
signal  0..1—1 signal (supersedes: previous revision of same key)
signal  0..1—1 signal_key (supersedes_key: older, different-stage lifecycle entry)
asset   1—* price_bar
backtest_run 1—* backtest_result *—1 signal
asset   1—* watchlist_item (0/1 per asset)
asset.benchmark_id —* asset (idx rows)
```

Deletion policy: derived rows (`cluster`, `cluster_member`, `event`,
`event_link`) are deleted and re-inserted for the active window on every
ingest and by no other path. Durable rows are never deleted; `init --reset`
recreates the DB wholesale. Watchlist rows are the only user-deletable
entity.
