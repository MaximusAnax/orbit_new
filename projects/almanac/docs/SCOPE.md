# Almanac — Scope

## One-liner

A personal quote-and-idea almanac: capture quotes and ideas with source,
author, tags, and notes; every day a deterministic, seedable scheduler
surfaces one card — balancing new captures against spaced re-exposure of
long-unseen entries, never repeating within a window, keeping pinned
favorites in rotation — and pairs it with a theme-matched application prompt
("Tonight, before bed, ask yourself…", "Today, when X happens, I will Y…").
The user logs how it landed, and that reflection feeds the schedule.

## Problem statement

The owner saves quotes and ideas — in notes apps, screenshots, book margins —
and never sees them again. Saving is easy; *returning* is the unsolved part.
Static daily-quote products (a calendar page, The Daily Stoic's fixed 366-day
loop) show everyone the same sequence and know nothing about what you saved
or what resonated. Readwise's Daily Review is the closest prior art —
resurfacing your own highlights on a spaced schedule — but it is a hosted
service, its scheduling is opaque, and it stops at *re-reading*: it never
asks you to do anything with the idea, and it has no feedback channel other
than thumbs. Meanwhile a century of memory research says re-exposure only
pays when it is *spaced* (Ebbinghaus 1885; Cepeda et al. 2006), and a
parallel literature says ideas change behavior only when converted into
concrete if-then plans (implementation intentions — Gollwitzer 1999) or
deliberate reflection (the Stoic evening review — Seneca, *De Ira* 3.36).

Almanac closes the loop: capture → schedule → surface with an application
prompt → reflect → the reflection reshapes the schedule.

The hard parts are:

- **A. The daily scheduler.** A pure, seedable function from (library state,
  history, date, seed) to today's card(s) that simultaneously: balances a
  target share of never-seen entries against spaced review of seen ones;
  spaces each entry's re-exposures on a per-entry expanding interval driven
  by reflection grades; enforces a hard no-repeat cooldown window; caps the
  interval of pinned favorites so they keep returning; never starves an
  entry forever; degrades gracefully on tiny libraries and sporadic usage;
  and is byte-for-byte reproducible from a seed. Any single property is
  easy; the product lives on all of them holding at once, which is why the
  eval suite simulates whole years of usage (EVALS M1–M7).
- **B. Application-prompt pairing.** Each surfacing must carry a prompt that
  actually fits the entry's theme (a mortality quote gets a mortality
  prompt, not a generic platitude), rotates through distinct prompt kinds
  (reflect / act / reframe / connect) without repeating itself, adapts the
  kind to the last reflection, and — when the optional LLM personalizer is
  on — can never be corrupted by it (validator + fallback, EVALS M8–M9).

## Target user

The owner: one person, terminal and/or local API, checking in for two
minutes a day (often less; sometimes skipping days). Single local profile,
one SQLite file, no accounts, no sync, no notifications — the tool is
pull-based (`almanac today`); wiring it to cron or an OS notifier is the
user's business, not this pass's.

## User stories & acceptance criteria

**US-1 — Capture in ten seconds.** As a user, I add a quote or idea from the
CLI with author, source, tags, and a personal note, and the tool suggests
themes and warns me about famous misattributions.
*Accept:* `almanac add` persists the entry and prints suggested themes (from
the deterministic keyword suggester, FR-3) for confirmation; a near-duplicate
(same normalized text) triggers a warning naming the existing entry; adding
"insanity is doing the same thing…" attributed to Einstein prints the
misattribution note with its Quote Investigator reference (FR-2); total
round-trip < 1 s offline.

**US-2 — A daily card worth opening.** As a user, `almanac today` shows me
one card: the quote/idea, its author and source, my note, and one
application prompt matched to its theme.
*Accept:* the card is materialized once per date and identical on re-invocation
(FR-8, M1f); the prompt's theme is one of the entry's themes or `general`
only when the entry has none (M1e gate = 1.0); output renders in plain text
with a `--json` variant.

**US-3 — New things show up soon; old things come back.** As a user, a quote
I captured this week appears within days, while things I saved months ago
keep resurfacing at growing intervals instead of vanishing.
*Accept:* on the eval simulations, debut latency p50 ≤ 10 days and p95 ≤ 45
days (M2); realized novelty share tracks the 0.35 target within ±0.12 over
28-day windows (M3); review gaps track each entry's scheduled interval with
mean relative error ≤ 0.25 (M4); no active entry is starved past the
forcing horizon (FR-7 step 2, checked inside M2).

**US-4 — No déjà vu.** As a user, I never see the same entry twice within a
ten-day window, and never twice in the same day's batch.
*Accept:* zero cooldown or same-day-duplicate violations across all
simulated years (M1a/M1b gate = 1.0); the only exception is the tiny-library
fallback, which picks the least-recently-seen entry and marks the surfacing
`relaxed_cooldown = true` (asserted in scenario S3).

**US-5 — Favorites stay alive; duds die.** As a user, pinning an entry
guarantees it keeps coming back, and entries that repeatedly do nothing for
me fade out and get suggested for archiving.
*Accept:* every pinned active entry resurfaces within 45 days in steady
state (M5 gate = 1.0); entries with three consecutive `flat` reflections are
listed as archive candidates by `almanac stats` and their subsequent
exposure rate falls to ≤ 0.5× that of resonating entries (M6).

**US-6 — Reflection feeds the machine.** As a user, after a card I log
`applied` / `resonated` / `flat` with an optional note, and the schedule
visibly responds; my reflections are browsable per entry as a journal.
*Accept:* `almanac reflect --last --grade applied --text "…"` appends an
immutable reflection tied to the surfacing (FR-11); the entry's next
interval follows the FR-6 table exactly (unit-tested per grade);
`almanac show <id>` lists the entry's full surfacing + reflection history;
reflecting on a surfacing after the entry has been surfaced again is
rejected with a clear error.

**US-7 — Find it again.** As a user, I can full-text search everything I
ever saved (text, author, source, my notes), browse by tag or theme, and
curate named collections.
*Accept:* `almanac search "attention"` returns entries matching stemmed
full-text search ranked deterministically (FR-12); tag and theme filters
compose with status/pinned filters; collections hold ordered entries and
`almanac draw --collection <id>` pulls an extra card from one (FR-13) —
extra draws count as exposures and start the cooldown but never disturb the
daily slot accounting.

**US-8 — Personalization that can't lie.** As a user, I can switch on the
LLM personalizer to tailor prompts to my note and source, and nothing
structural can break: a bad LLM output is silently replaced by the template
rendering.
*Accept:* the validator's tamper-detection recall is 1.0 with zero false
positives on clean outputs (M9 gate); a fallen-back surfacing records
`personalize_fell_back = true`; the personalizer is never active in tests
or evals.

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-6/FR-7/
FR-8 are hard part A; FR-3/FR-9/FR-10 are hard part B.

- **FR-1 Capture.** Create an entry with `kind` (quote | idea), required
  `text`, optional `author`, `source`, `url`, tags, themes, personal `note`.
  Normalization: text stored verbatim; a `normalized_hash` (NFKC → casefold
  → strip punctuation/whitespace → SHA-256) is computed for duplicate
  detection. A hash collision with an existing non-archived entry is a
  *warning* (response carries `duplicate_of`), not a block; CLI asks for
  confirmation unless `--yes`. Tags are normalized (casefold, spaces →
  hyphens) and created on first use.
- **FR-2 Attribution check.** On capture (and on demand via
  `check-attribution`), the `AttributionChecker` adapter matches
  (normalized text, claimed author) against the committed misattribution
  dataset (`data/misattributions.json`, curated from Quote Investigator and
  Wikiquote "Misattributed" sections; DATA_MODEL.md). A match yields an
  `AttributionFlag` on the entry: verdict (misattributed | disputed |
  unverified), likely origin, note, reference URL. Flags are informational
  — they never block capture and render as a footnote on cards. Matching is
  fuzzy only in the deterministic sense: normalized-token containment of
  the dataset's `pattern` in the entry text plus claimed-author match.
- **FR-3 Theme taxonomy & suggestion (hard part B).** The fixed 16-theme
  taxonomy (`data/themes.json`; D8) with per-theme keyword lexicons. The
  deterministic suggester scores each theme as the sum of lexicon-keyword
  hits (stemmed token and phrase matches, weights in the lexicon) over the
  entry's text + tags + note, and returns the top 3 with scores; ties break
  lexicographically by theme id. Suggested themes are *proposed*, never
  silently assigned: CLI prompts to confirm (or `--yes` accepts top-1 when
  score > 0); API returns suggestions in the create response and the client
  PATCHes them on. Entries may have 0–3 themes. Accuracy is gated (EVALS
  M8) on a hand-labeled held-out fixture.
- **FR-4 Curation.** Edit entry fields; pin/unpin; archive/restore.
  Archived entries never surface, never appear in default listings, keep
  their history. Pinned entries participate normally but with a capped
  effective interval (FR-6); pinning is orthogonal to themes/tags.
- **FR-5 Import/export.** `almanac import FILE` accepts (a) the native JSON
  export format and (b) a documented CSV (`text, author, source, url,
  tags, note` header) — covering Readwise-style CSV exports by column
  mapping. Import applies FR-1 normalization + duplicate detection per row
  and prints a report (created / duplicates skipped / errors with row
  numbers). `almanac export` writes the full library (entries, tags,
  themes, collections, surfacings, reflections) as one JSON document.
  `almanac import --starter` loads the committed starter pack
  (`data/starter_quotes.json`, ~50 public-domain quotes, pre-1929 authors,
  pre-tagged with themes) so the product is useful on day one.
- **FR-6 Scheduling state & interval update (hard part A).** Scheduling
  state per entry is *derived*: a pure left-fold over the entry's ordered
  surfacing and reflection events (DATA_MODEL.md §SchedulerState). State:
  `exposure_count`, `last_surfaced_on`, `interval_days`, `flat_streak`.
  After the debut surfacing, `interval_days := I0 = 3`. Each subsequent
  surfacing first *applies the grade of the previous surfacing's
  reflection* (or `none` if the user never reflected) to the interval:

  | grade of prior surfacing | multiplier | clamp | flat_streak |
  |---|---|---|---|
  | `applied`   | × 2.3 | [2, 120] | reset to 0 |
  | `resonated` | × 1.6 | [2, 120] | reset to 0 |
  | `none` (no reflection) | × 1.4 | [2, 120] | unchanged |
  | `flat`      | × 3.0 | [2, 180] | += 1 |

  Rounding is half-up to whole days. Note the deliberate inversion of SM-2
  (D1): here a *negative* signal lengthens the interval (deprioritize the
  entry), because the objective is portfolio value delivered, not per-item
  retention. Pinned entries use effective interval
  `I_eff = min(interval_days, 21)` in all due-ness computations.
  `flat_streak ≥ 3` marks the entry an archive candidate (surfaced via
  FR-14; never auto-archived). The fold is exercised directly by tests and
  by the replay gate (M7): a materialized `scheduler_state` cache row must
  always equal the fold of the event log.
- **FR-7 Daily selection (hard part A).** Pure function
  `select(library, history, date D, seed, params) -> [slot picks]` for
  slots `t = 0 … k−1` (batch size k, default 1, max 5), computed
  sequentially so earlier slots affect later ones:
  1. *Eligibility:* entry active, and its last exposure (daily or extra
     draw) is ≥ W = 10 days before D (`D − last_surfaced_on ≥ W`); entries
     picked earlier today are ipso facto ineligible.
  2. *Pool choice:* N-pool = eligible entries with `exposure_count = 0`;
     R-pool = eligible entries with `exposure_count ≥ 1` and
     `D ≥ last_surfaced_on + I_eff` (due). If any N-pool entry has
     `age = D − captured_on > S = 90` days, the slot is **forced novelty**
     (starvation aging, D5). Otherwise compute the realized novelty share σ
     among `kind = daily` surfacings in the trailing H = 28 days including
     today's earlier slots (σ = 0 with no history): pick N-pool if
     `σ < ρ = 0.35` and N-pool is non-empty; else R-pool if non-empty; else
     N-pool if non-empty; else the *not-yet-due* eligible reviews ranked by
     overdue ratio; else the **tiny-library fallback**: among all active
     entries not picked today, choose min `last_surfaced_on` (ties by id)
     and mark the surfacing `relaxed_cooldown = true`.
  3. *Priority:* within N-pool,
     `n(e) = exp(−age/τ) + (age/S)²` with τ = 7 — U-shaped so this week's
     captures surface hot while months-old never-seen entries climb back
     up. Within R-pool, overdue ratio
     `O(e) = (D − last_surfaced_on) / I_eff`.
  4. *Jitter & tie-break:* score = priority +
     `0.05 · u64(SHA-256(seed ‖ D ‖ t ‖ entry_id)[:8]) / 2^64`; argmax
     wins; exact ties break by ascending entry id. The per-entry hash makes
     selection order-independent and stateless-deterministic (D4).
  All parameters (W, ρ, H, S, τ, I0, multipliers, caps, k) live in
  `data/scheduler.json` with a `params_version`; every surfacing stamps the
  `scheduler_version` and `seed` used.
- **FR-8 Materialization & idempotence (hard part A).** `POST /today {date}`
  / `almanac today` materializes the daily surfacing set for a date at most
  once: if surfacings for D exist, return them unchanged (byte-identical
  render, M1f). Daily materialization dates are strictly increasing
  (materializing D < an existing daily date is rejected) — the fold's
  arrow of time. Missed days are simply never materialized; there is no
  backfill (non-goal 8): the overdue ratio absorbs gaps naturally. Extra
  draws (`POST /draws`, optional theme/collection filter) materialize a
  `kind = extra` surfacing immediately: same eligibility + cooldown rules
  restricted to the filter, priority = O(e) for seen entries and n(e) for
  unseen, same jitter; they count as exposures in the fold but are excluded
  from the σ novelty-share statistic.
- **FR-9 Prompt selection (hard part B).** For a surfacing of entry e with
  prior exposure count n and last reflection grade g:
  1. *Kind:* base kind = `[reflect, act, reframe, connect][n mod 4]`;
     override — g = `resonated` ⇒ `act` (it landed; convert to an if-then
     plan, D6), g = `applied` ⇒ `connect` (consolidate and generalize);
     otherwise base.
  2. *Candidates:* templates whose theme ∈ e.themes, or theme = `general`
     iff e has no themes; minus the template ids of e's last
     R_p = 6 surfacings; filtered to the chosen kind — if empty, try kinds
     in rotation order from the chosen one; if still empty, drop the
     recency exclusion and retry once.
  3. *Pick:* argmax of jitter `u64(SHA-256(seed ‖ D ‖ entry_id ‖
     template_id)[:8])`, ties by ascending template id.
  4. *Render:* fill slots `{author}` (fallback "the author"),
     `{theme_name}`, `{source}` (fallback "this"), `{text_short}` (first
     ≤ 12 words + "…"). No slot may remain unfilled (validator, FR-10).
  The committed bank (`data/prompts.json`) has ≥ 6 templates per theme
  (≥ 1 per kind) and ≥ 8 `general` ones — ~110 templates, an authoring
  deliverable on par with code (D7).
- **FR-10 Personalization adapter + validation gate (hard part B).**
  `PromptPersonalizer.personalize(card_context) -> str` may rewrite the
  rendered prompt using the entry's text, note, and source. The offline
  default `TemplatePersonalizer` is the identity on the rendered template.
  Every personalizer output — including the identity — passes the
  validator: (a) non-empty, ≤ 400 chars, ≤ 60 words, single line; (b) no
  unfilled `{...}` slots; (c) if the rendered template embedded
  `{text_short}`, the personalized text contains that excerpt verbatim;
  (d) no URLs; (e) kind signature preserved — `reflect`/`connect` prompts
  end with "?", `act` prompts contain the literal scaffold "When " and
  " I will " (the Gollwitzer if-then frame, D6), `reframe` prompts contain
  no "?" outside a final optional question. Validation failure ⇒ discard,
  serve the template rendering, set `personalize_fell_back = true`. The
  live `LLMPersonalizer` activates only when `ALMANAC_LLM_API_KEY` is set
  (`ALMANAC_LLM_MODEL`, `ALMANAC_LLM_BASE_URL`; default provider
  Anthropic); optional extra, never imported by tests or evals.
- **FR-11 Reflection log.** `reflect(surfacing_id, grade ∈ {applied,
  resonated, flat}, text?)` appends an immutable reflection; exactly one
  per surfacing (unique constraint); allowed only while the surfacing is
  the entry's most recent exposure (else 409 — the fold has moved on).
  Reflections are browsable per entry and globally by date range (the
  journal). Grades drive FR-6; `text` is the user's applied-it note
  (generation effect: writing beats re-reading, D6).
- **FR-12 Search & browse.** Full-text search over text, author, source,
  and note via SQLite FTS5 with the porter tokenizer; results ranked by
  FTS5 bm25() with deterministic id tie-break. Listing filters: status,
  pinned, tag, theme, kind; combinable. If the interpreter's SQLite lacks
  FTS5, the repository falls back to a normalized-token LIKE scan (same
  API, documented as slower; determinism preserved).
- **FR-13 Collections.** Named, described, manually ordered sets of
  entries (add/remove/reorder). Collections scope *extra draws* only —
  the daily scheduler always draws from the whole active library
  (deferring per-collection scheduling keeps the fold single-stream,
  non-goal 6).
- **FR-14 Stats.** `almanac stats` reports: library counts by status/kind;
  coverage (% of active entries surfaced ≥ once); exposure histogram;
  current open streak (consecutive materialized days ending today) and
  reflect streak (materialized + ≥ 1 reflection); realized novelty share
  over trailing 28 days vs ρ; pinned entries with days-since-seen; archive
  candidates (flat_streak ≥ 3) with their streaks.
- **FR-15 API.** FastAPI app per the sketch below; thin — validation,
  service calls, serialization.
- **FR-16 CLI.** Typer app per the sketch below; same service layer as the
  API; every read command has `--json`; `today` renders a plain-text card
  (no color dependencies).
- **FR-17 Determinism & hermeticity.** The engine is pure: no network, no
  filesystem, no clock reads — the current date is always a parameter
  (edges obtain it from the `Clock` adapter). All randomness flows from
  the stored seed through SHA-256 keyed jitter; no `random` module in the
  engine. Identical (state, date, seed) ⇒ byte-identical selections,
  prompts, and renders (M7 gate). Tests and evals use only
  `TemplatePersonalizer`, `LocalAttributionChecker`, `FixedClock`, and the
  in-memory store.

## Non-goals (this pass)

1. **No web or mobile UI** — API + CLI only (workspace-wide decision). The
   card JSON is shaped so a later UI is a pure client.
2. **No notifications/daemon.** Surfacing is pull-based; scheduling
   `almanac today` under cron/launchd/Routines is user wiring, not code.
3. **No LLM-generated quotes, ideas, or advice.** The LLM adapter may only
   rephrase an application prompt under the FR-10 validator; entries and
   templates are user/curated data. No "generate me a quote about X".
4. **No live import integrations.** Readwise/Kindle/Instapaper APIs are
   deferred; their CSV exports import via FR-5 today.
5. **No semantic search or embeddings.** FTS5 + tags/themes only; an
   embedding search adapter would be live-only and is not designed here.
6. **No per-collection scheduling.** Collections scope extra draws only;
   the daily fold stays single-stream.
7. **No multi-user, auth, or sync.** One local profile, one SQLite file.
8. **No backfill of missed days** and no "catch-up" batches; the overdue
   ratio already prioritizes what waited longest.
9. **Attribution checking is best-effort curation, not scholarship.** The
   committed dataset covers ~50 famous misattributions; absence of a flag
   is not verification. The live Wikiquote adapter is optional and
   unevaluated.
10. **No spaced-repetition *testing*** (cloze deletion, recall grading à la
    Anki). Almanac spaces *exposure and application*, not memorization —
    stated because the algorithms are cousins (D1) and the distinction
    drives the FR-6 inversion.
11. **English-only** taxonomy, lexicons, and templates.

## Architecture

```
projects/almanac/
  src/almanac/
    models.py               # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/
      normalize.py          # FR-1: NFKC/casefold pipeline, normalized_hash, tokenizer + porter stem
      themes.py             # FR-3: taxonomy load, keyword suggester
      scheduler.py          # FR-6/7/8: state fold, pools, slot rule, priorities, jitter
      prompts.py            # FR-9: kind rotation, candidate filter, seeded pick, slot render
      attribution.py        # FR-2: pure normalized matching against the dataset
      validate.py           # FR-10: personalizer output validator
      service.py            # orchestration: capture, materialize day, draw, reflect (pure; deps injected)
    adapters/
      personalizer.py       # PromptPersonalizer Protocol
      personalizer_null.py  #   offline default: TemplatePersonalizer (identity on rendered template)
      personalizer_llm.py   #   live: LLMPersonalizer (optional extra, env-gated)
      attribution.py        # AttributionChecker Protocol
      attribution_local.py  #   offline default: LocalAttributionChecker (committed dataset)
      attribution_wikiquote.py #  live: WikiquoteAttributionChecker (optional, env-gated)
      clock.py              # Clock Protocol; SystemClock (edges only), FixedClock (tests/evals)
    store/
      repository.py         # Repository Protocol
      sqlite_repo.py        # SQLite (stdlib sqlite3 + FTS5), default ~/.almanac/almanac.db
      memory_repo.py        # in-memory backend for tests/evals
    api/                    # FastAPI app (FR-15)
    cli/                    # Typer app (FR-16)
  data/
    themes.json             # 16 themes + keyword lexicons (FR-3)
    prompts.json            # ~110 prompt templates across 4 kinds (FR-9)
    scheduler.json          # W, ρ, H, S, τ, I0, multipliers, caps, k, params_version (FR-6/7)
    misattributions.json    # ~50 curated misattribution records (FR-2)
    starter_quotes.json     # ~50 public-domain starter entries (FR-5)
  evals/                    # fixtures/, personas.py, faulty_personalizer.py,
                            # simulate.py, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default, tests/evals) | Live (env-gated, optional extra) |
|---|---|---|
| `PromptPersonalizer.personalize(ctx: CardContext) -> str` | `TemplatePersonalizer` — returns the rendered template unchanged; also eval-only `FaultyPersonalizer` (evals/) with scripted mutations for M9 | `LLMPersonalizer` — rewrites the prompt using entry text/note/source via the configured LLM (`ALMANAC_LLM_API_KEY`, `ALMANAC_LLM_MODEL`, `ALMANAC_LLM_BASE_URL`; default provider Anthropic); output always passes the FR-10 validator |
| `AttributionChecker.check(text: str, author: str \| None) -> list[AttributionFlag]` | `LocalAttributionChecker` — deterministic matching over `data/misattributions.json` | `WikiquoteAttributionChecker` — queries the Wikiquote API for the claimed author's Misattributed section (`ALMANAC_WIKIQUOTE = 1`) |
| `Clock.today() -> date` | `FixedClock` (tests/evals; simulation advances it) | `SystemClock` — used only at API/CLI edges to default the `date` parameter; the engine never sees it |

The theme taxonomy, prompt bank, scheduler params, misattribution dataset,
and starter pack are committed data, not adapters; the store follows the
workspace Repository pattern.

### API sketch (FastAPI)

```
GET    /health
POST   /entries                          # create → entry + theme suggestions + attribution flags
GET    /entries?status=&pinned=&tag=&theme=&kind=&q=&limit=&offset=
GET    /entries/{id}                     # includes scheduler state + history summary
PATCH  /entries/{id}
POST   /entries/{id}/pin                 DELETE /entries/{id}/pin
POST   /entries/{id}/archive             POST   /entries/{id}/restore
POST   /entries/import                   # {format: json|csv|starter, payload?} → report
GET    /entries/export
POST   /today                            # {date} materialize-if-absent → card(s)  (FR-8)
GET    /today?date=                      # read-only; 404 if not materialized
POST   /draws                            # {date, theme?, collection_id?} → extra card
GET    /surfacings?entry_id=&from=&to=   GET /surfacings/{id}
POST   /surfacings/{id}/reflection       # {grade, text?}  → 409 if superseded (FR-11)
GET    /reflections?entry_id=&from=&to=
GET    /themes                           GET /themes/{id}/templates
GET    /tags
POST   /collections                      GET /collections        GET /collections/{id}
PATCH  /collections/{id}                 DELETE /collections/{id}
PUT    /collections/{id}/entries/{eid}   DELETE /collections/{id}/entries/{eid}
GET    /stats?date=
GET    /attribution/check?text=&author=
```

### CLI sketch (Typer)

```
almanac init                                      # create DB, load data files, set seed
almanac add "TEXT" [--kind quote|idea] [--author A] [--source S] [--url U]
            [--tag T]... [--theme T]... [--note N] [--yes]
almanac import FILE [--format json|csv] | almanac import --starter
almanac export [--out FILE]
almanac today [--date YYYY-MM-DD] [--json]        # materialize + render card(s)
almanac draw [--theme T | --collection ID]
almanac reflect [SURFACING_ID | --last] --grade applied|resonated|flat [--text N]
almanac list [--status ...] [--tag ...] [--theme ...] [--pinned]
almanac search "QUERY"                            almanac show ENTRY_ID
almanac edit ENTRY_ID [field flags]
almanac pin ID | unpin ID | archive ID | restore ID
almanac tags | themes
almanac collection create NAME [--desc D] | list | show ID |
        add ID ENTRY_ID | rm ID ENTRY_ID | delete ID
almanac stats [--date D]
almanac check-attribution [--entry ID | --text T --author A]
almanac config get|set KEY [VALUE]                # seed, batch k (surfacing stamps make changes auditable)
```

## Key design decisions & assumptions

1. **Spaced re-exposure, adapted — not adopted — from spaced repetition.**
   The spacing effect (Ebbinghaus, *Über das Gedächtnis*, 1885; Cepeda et
   al. 2006 meta-analysis of 254 distributed-practice studies) and
   expanding-interval retrieval (Landauer & Bjork 1978; Pimsleur's 1967
   graduated intervals) motivate per-entry geometric interval growth, and
   the multiplier-on-grade mechanism is SM-2's (Wozniak 1990, SuperMemo;
   Leitner 1972 boxes are the analog ancestor). But Almanac's objective is
   value delivered by the portfolio, not retention of each item — so the
   grade table *inverts* SM-2's failure branch: a `flat` grade lengthens
   the interval (deprioritize) instead of shortening it, and repeated flats
   route to archiving. Non-goal 10 states the boundary.
2. **Re-exposure has an inverted-U, hence the hard cooldown.** Mere
   exposure increases liking (Zajonc 1968) but declines past moderate
   repetition (Berlyne's 1970 two-factor account; Bornstein's 1989
   meta-analysis shows the effect weakening with overexposure). W = 10
   days is the floor under *any* code path except the tiny-library
   fallback, which is explicit and flagged.
3. **Deterministic quota mixing instead of a bandit.** Novelty-vs-review is
   an explore/exploit tradeoff (Robbins 1952), but stochastic bandits are
   unauditable and unseedable in the way CONVENTIONS.md demands. The
   trailing-share rule (pick the pool whose realized share trails its
   target) is a proportional-share scheduler in the lineage of stride
   scheduling (Waldspurger & Weihl, OSDI 1995) and nginx's smooth weighted
   round-robin: deterministic, drift-free, and inspectable from the
   surfacing log alone — no hidden counters, so the engine stays a pure
   function of history (FR-7 step 2).
4. **Stateless seeded jitter.** Variety comes from
   `SHA-256(seed ‖ date ‖ slot ‖ entity_id)` mapped to [0, 0.05) — a pure
   function, order-independent, and reproducible without PRNG state
   plumbing. 0.05 is small enough that a full priority point always
   dominates (a due entry cannot lose to jitter) and large enough to break
   the monotony of exact-tie cohorts (e.g., a batch import).
5. **Starvation freedom by aging.** The forced-novelty rule (any never-seen
   entry older than S = 90 days claims the slot) is the classic aging
   remedy for starvation in priority scheduling (Silberschatz et al.,
   *Operating System Concepts*). The N-pool priority's `(age/S)²` term
   ramps toward it smoothly, so forcing is rare in practice (M2 verifies).
6. **The four prompt kinds are each grounded in a named practice.**
   `reflect` — the Stoic evening review (Seneca, *De Ira* 3.36, nightly
   self-examination; Epictetus, *Enchiridion*) and Pennebaker's expressive
   writing. `act` — implementation intentions: "When situation X arises, I
   will perform response Y" (Gollwitzer 1999); the Gollwitzer & Sheeran
   2006 meta-analysis (94 studies) found a medium-to-large effect
   (d ≈ 0.65) on goal attainment, which is why the FR-10 validator makes
   the "When … I will …" scaffold structural, not stylistic. `reframe` —
   premeditatio malorum (Seneca, *Letters* 91) and cognitive reappraisal
   (Gross 1998). `connect` — elaborative interrogation (Pressley et al.
   1987) and the self-reference effect (Rogers, Kuiper & Kirker 1977).
   The reflection log itself leans on the generation effect (Slamecka &
   Graf 1978) and the testing effect (Roediger & Karpicke 2006): writing
   how you applied an idea beats re-reading it.
7. **Templates over generation.** Prompts are curated data with slot-fill
   rendering; the LLM may only *personalize* under the FR-10 validator
   (assume-adversarial posture, mirroring the workspace's ethos project:
   fallback is silent-safe, so a flaky LLM can degrade style, never
   structure). The prompt bank (~110 templates) is a first-class authoring
   deliverable with floors enforced by a data-validation test.
8. **Sixteen themes, fixed.** courage, discipline_and_habit,
   mortality_and_time, gratitude, honesty_and_integrity,
   attention_and_presence, relationships, adversity_and_resilience,
   creativity_and_craft, humility, purpose_and_ambition,
   simplicity_and_frugality, equanimity_and_anger, generosity_and_service,
   learning_and_growth, decision_and_action — chosen to cover the
   recurring subjects of the aphoristic canon (Stoics, Proverbs, Franklin,
   Emerson, La Rochefoucauld) while staying few enough that every theme
   can carry ≥ 6 quality templates. `general` is a template pool, not an
   assignable theme. Adding a theme is a data change + template authoring,
   no code.
9. **Suggestion, never silent auto-tagging.** The keyword suggester is
   deliberately a transparent lexicon scorer (the craft goes into the
   lexicons, as with ethos's router): wrong suggestions are visible and
   cheap to reject at capture time. A misfiled theme quietly poisons
   prompt pairing forever, which is why confirmation is in the loop and
   accuracy is gated on a *held-out* fixture half (EVALS M8).
10. **Attribution integrity is a real, named problem.** Quotes drift to
    famous names — "Churchillian drift" (Nigel Rees). The committed
    dataset curates canonical cases from Quote Investigator (Garson
    O'Toole) and Wikiquote's Misattributed sections: e.g., "insanity is
    doing the same thing over and over" (attributed Einstein; earliest
    known: 1981 Narcotics Anonymous pamphlet, per QI), "Be the change you
    wish to see in the world" (not verbatim Gandhi; closest verified 1913
    passage differs), "A lie can travel halfway around the world…"
    (attributed Twain; lineage runs to Swift 1710). Flags are footnotes,
    not blocks — the user may keep the folk attribution knowingly.
11. **The almanac format is the precedent.** Daily aphorisms date to Poor
    Richard's Almanack (Franklin, 1732–58); The Daily Stoic (Holiday &
    Hanselman 2016) proves the daily-quote-plus-reflection format; Readwise
    Daily Review proves resurfacing *your own* library. Almanac's deltas:
    local and deterministic, application prompts with behavioral grounding
    (D6), and a reflection loop that actually feeds the scheduler.
12. **Surfacings are immutable snapshots.** A card stores the rendered
    prompt text, template id, seed, `scheduler_version`, and flags — so
    history survives template edits, param tuning, and seed changes, and
    idempotent re-reads are byte-identical (FR-8). Same pattern as ethos
    answers and flowlist runs.
13. **Scheduler state is event-sourced with a materialized cache.** The
    fold (FR-6) is the truth; `scheduler_state` rows are a cache with a
    rebuild command and an equality gate (M7). This makes the engine
    testable as a pure function and makes "the schedule responded to my
    reflection" auditable from the log.
14. **Whole-day granularity; the date is caller input.** The scheduler
    reasons in local calendar days (the user's mental unit); the engine
    never reads a clock (FR-17); edges default the date from the `Clock`
    adapter. Timezone subtleties collapse into "whatever date the caller
    says it is". Daily materialization is monotonic to keep the fold's
    arrow of time (FR-8).
15. **Parameter defaults are committed and versioned** (`scheduler.json`,
    stamped on every surfacing). Defaults: W = 10, ρ = 0.35, H = 28,
    S = 90, τ = 7, I0 = 3, k = 1, caps 120/180, pinned cap 21. ρ = 0.35
    means roughly one new card per three-day rhythm for a daily user —
    enough novelty to make capturing feel rewarded, enough review to make
    the library compound; the eval simulations (EVALS §4) are the tuning
    instrument, and retuning is a data change with M2–M5 as guardrails.
16. **FTS5 with porter tokenizer for search**; bm25() ranking with id
    tie-break keeps it deterministic. No embeddings (non-goal 5). A LIKE
    fallback guards exotic builds lacking FTS5.
17. **IDs are ULIDs** minted at the edges by an injected id factory
    (fixed-sequence factory in tests); the engine treats ids as opaque
    strings and orders by them only for tie-breaks (ULIDs sort by creation
    time, which makes tie-breaks stable and meaningful).
18. **Single user, no auth**; SQLite at `~/.almanac/almanac.db`
    (`ALMANAC_DB_PATH` overrides); env vars documented in README; no
    secrets in the repo.
19. **Public-domain-only committed text.** Starter pack and eval fixtures
    quote only pre-1929 / clearly public-domain authors (Marcus Aurelius
    in Long's 1862 translation, Seneca in Gummere's, Epictetus, Franklin,
    Emerson, Thoreau, La Rochefoucauld, Goethe, Proverbs) — the repo stays
    license-clean.
20. **Line budget & scope valves.** Estimate: engine ~1,150 (scheduler
    ~360, prompts ~230, themes ~140, normalize ~120, attribution ~90,
    validate ~90, service ~120), models ~260, adapters ~180, store ~480,
    API ~330, CLI ~380 ≈ 2,950 lines — inside the 2–4k mandate; data
    authoring (~110 templates, 16 lexicons, ~50 misattributions, ~50
    starter quotes) is budgeted as a parallel deliverable. Valves, in
    order: drop collections (FR-13); drop the Wikiquote live adapter and
    `check-attribution` CLI (keep capture-time flags); reduce themes
    16 → 12 with lexicons/fixtures shrinking in the same commit; drop
    batch k > 1.
21. **Assumptions:** grades are honest self-report (no anti-gaming needed
    for one user); the user materializes at most one daily set per calendar
    date and tolerates no-backfill semantics; library scale ≤ ~5,000
    entries (all algorithms are O(n) per day at worst, fine at this
    scale); reflection text may contain anything — it is never parsed,
    only stored and displayed.
