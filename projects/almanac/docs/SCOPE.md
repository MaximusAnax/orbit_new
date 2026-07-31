# Almanac — Scope

## One-liner

A personal quote-and-idea almanac: capture quotes and ideas with source,
author, tags, and notes; every day a deterministic, seedable scheduler
surfaces one card — balancing new captures against spaced re-exposure of
long-unseen entries, never repeating within a window, guaranteeing pinned
favorites come back — and pairs it with a theme-matched application prompt
("Tonight, before bed, ask yourself…", "When X happens, I will Y…").
The user logs how it landed, and that reflection reshapes the schedule.

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
  history, date, seed) to today's card(s) that simultaneously: holds a
  target share of never-seen entries against spaced review of seen ones;
  spaces each entry's re-exposures on a per-entry expanding interval driven
  by reflection grades; enforces a hard no-repeat cooldown window;
  guarantees pinned favorites return inside a bounded gap; never starves an
  entry; degrades *predictably* when demand exceeds one card a day;
  degrades gracefully on tiny libraries and sporadic usage; and is
  byte-for-byte reproducible from a seed. Any single property is easy; the
  product lives on all of them holding at once under a fixed daily budget,
  which is why the eval suite simulates whole years of usage (EVALS M1–M7).
- **B. Application-prompt pairing.** Each surfacing must carry a prompt that
  actually fits the entry's theme (a mortality quote gets a mortality
  prompt, not a generic platitude), rotates through distinct prompt kinds
  (reflect / act / reframe / connect) without repeating itself, adapts the
  kind to the last reflection, and — when the optional LLM personalizer is
  on — cannot be *malformed* by it (validator + fallback, EVALS M8–M9).

## Target user

The owner: one person, terminal and/or local API, checking in for two
minutes a day (often less; sometimes skipping days). Single local profile,
one SQLite file, no accounts, no sync, no notifications — the tool is
pull-based (`almanac today`); wiring it to cron or an OS notifier is the
user's business, not this pass's.

## The capacity identity (read this before the FRs)

Almanac serves **k cards per materialized day** (default k = 1). Every
promise below is bounded by that budget, and the design states the bound
rather than pretending it away. Per materialized day:

```
slots                 = k
pinned-rescue load    r  = n_pinned / P_rescue          (FR-7 step 1)
debut (novelty) load  = capture rate A, which must satisfy A <= rho * k
                        for the never-seen pool to stay bounded
review capacity       C  = k - r - A
review demand         L  = sum over active seen entries of 1 / I_eff(e)
stretch factor        lambda = L / C
sustainable library   N* ~= C * mean(I_eff)
```

**Consequence, stated as product behaviour, not as a bug:** when
`lambda > 1` the scheduler cannot honour every entry's nominal interval.
It does not drop entries and it does not surface them early. Because the
review pool is served by *maximum overdue ratio* `O(e) = (D - last)/I_eff`,
all realized gaps stretch by the same factor: `realized gap ~= lambda *
I_eff(e)`. Relative spacing — the thing the reflection loop controls — is
preserved exactly; absolute spacing is uniformly dilated. This is what
EVALS M4b measures (dispersion of `O` at service time), and it is why M4
is *not* an absolute-lateness metric.

With the committed defaults (k = 1, rho = 0.35, hi = 60, n_pinned = 8) a
saturated library of roughly 35–45 entries runs at `lambda ~= 1`; the eval
scenarios run at `lambda ~= 2–3`, which is the realistic regime.
`almanac stats` reports `lambda` and tells the user to raise `k` when it
exceeds 3 (FR-14). The tool never changes `k` by itself (non-goal 12).

## User stories & acceptance criteria

**US-1 — Capture in ten seconds.** As a user, I add a quote or idea from the
CLI with author, source, tags, and a personal note, and the tool suggests
themes and warns me about famous misattributions.
*Accept:* `almanac add` persists the entry and prints suggested themes (from
the deterministic keyword suggester, FR-3) for confirmation; a near-duplicate
(same normalized text) triggers a warning naming the existing entry; adding
"insanity is doing the same thing…" attributed to Einstein prints the
misattribution note with its Quote Investigator reference (FR-2).

**US-2 — A daily card worth opening.** As a user, `almanac today` shows me
one card: the quote/idea, its author and source, my note, and one
application prompt matched to its theme.
*Accept:* the card is materialized once per date and identical on re-invocation
(FR-8, M1f); the prompt's theme is one of the entry's themes or `general`
only when the entry has none (M1e, gate = 0 violations); output renders in
plain text with a `--json` variant.

**US-3 — New things show up soon; old things come back.** As a user, a quote
I captured this week appears within days, a bulk import drains within a
declared horizon, and nothing I saved is ever forgotten.
*Accept:* on the eval simulations, debut latency for the ongoing capture
stream is p50 ≤ 7 days and p95 ≤ 21 days (M2a); every entry of a bulk
day-0 import debuts within its scenario's capacity-derived drain bound
(M2b), and **no** active entry anywhere has a debut latency over 180 days
or fails to debut at all (M2b hard bound); realized novelty share over the
trailing 28 contested slots stays within ±0.12 of the 0.35 target (M3).

**US-4 — No déjà vu.** As a user, I never see the same entry twice within a
ten-day window, and never twice in the same day's batch.
*Accept:* zero cooldown or same-day-duplicate violations across all
simulated years (M1a/M1b, gate = 0); the only exception is the tiny-library
fallback, which picks the least-recently-seen entry and marks the surfacing
`relaxed_cooldown = true` (asserted in scenario S3).

**US-5 — Favorites stay alive; duds die.** As a user, pinning an entry
guarantees it keeps coming back on a bounded schedule, and entries that
repeatedly do nothing for me fade out and get suggested for archiving.
*Accept:* every pinned active entry resurfaces within 45 days for as long
as it stays pinned (M5 gate = 1.0), delivered by the pinned-rescue rule
(FR-7 step 1) rather than by luck; entries with two consecutive `flat`
reflections are demoted to the `hi_flat` interval and effectively leave
rotation, entries with three are listed as archive candidates by
`almanac stats`, and the demoted cohort's exposure rate falls to ≤ 0.35×
that of resonating entries (M6).

**US-6 — Reflection feeds the machine.** As a user, after a card I log
`applied` / `resonated` / `flat` with an optional note, and the schedule
visibly responds from the very first review; my reflections are browsable
per entry as a journal.
*Accept:* `almanac reflect --last --grade applied --text "…"` appends an
immutable reflection tied to the surfacing (FR-11); the entry's next
interval follows the FR-6 table exactly and every branch of that table
produces a *distinct, realizable* interval — no branch is swallowed by the
cooldown floor (unit-tested per grade); `almanac show <id>` lists the
entry's full surfacing + reflection history; reflecting on a surfacing
after the entry has been surfaced again is rejected with a clear error.

**US-7 — Find it again.** As a user, I can full-text search everything I
ever saved (text, author, source, my notes), browse by tag or theme, and
curate named collections.
*Accept:* `almanac search "attention"` returns entries matching stemmed
full-text search ranked deterministically (FR-12); tag and theme filters
compose with status/pinned filters; collections hold ordered entries and
`almanac draw --collection <id>` pulls an extra card from one (FR-13) —
extra draws count as exposures and start the cooldown, but never enter the
novelty-share statistic and never consume a daily slot (M1i).

**US-8 — Personalization that can't malform a prompt.** As a user, I can
switch on the LLM personalizer to tailor prompts to my note and source, and
nothing *structural* can break: an ill-formed LLM output is silently
replaced by the template rendering.
*Accept:* the validator's tamper-detection recall is 1.0 on the committed
malformation set with zero false positives on clean outputs (M9 gate); a
fallen-back surfacing records `personalize_fell_back = true`; the
personalizer is never active in tests or evals. The validator bounds
*form*, not *meaning* — a fluent but wrong-headed rewrite passes, which is
why the personalizer is opt-in and off by default (D7).

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
  M8) on a hand-labeled held-out fixture half that lexicon authors may not
  read (enforcement: EVALS §4).
- **FR-4 Curation.** Edit entry fields; pin/unpin; archive/restore.
  - *Text edits:* free while the entry has no surfacings. Once a surfacing
    exists, an edit to `text` is accepted **iff it leaves
    `normalized_hash` unchanged** (punctuation, casing, whitespace,
    diacritic normalization — the typo-fix case). A semantic text change
    after a surfacing is rejected with a message stating the alternative:
    archive + re-add, which starts a fresh scheduling and reflection
    history. `author`, `source`, `url`, `note`, tags and themes are always
    editable. Rationale: surfacing snapshots quote the entry, so history
    must not silently drift; but a typo fix must not cost a year of
    reflections.
  - *Pin/unpin:* pinned entries get the FR-7 rescue guarantee and a capped
    effective interval (FR-6). `almanac pin` warns when the pinned count
    would exceed `8 * k`, printing the degraded guarantee
    (`P_rescue + ceil(n_pinned / k)` days instead of 45) — see FR-7.
  - *Archive/restore:* archived entries never surface, never appear in
    default listings, are excluded from duplicate warnings, and keep all
    history.
- **FR-5 Import/export.** `almanac import FILE` accepts (a) the native JSON
  export format and (b) a documented CSV (`text, author, source, url,
  tags, note` header) — covering Readwise-style CSV exports by column
  mapping. Import applies FR-1 normalization + duplicate detection per row
  and prints a report (created / duplicates skipped / errors with row
  numbers), followed by the projected drain horizon for the imported batch
  computed from the capacity identity (`S + ceil(B / (k - r))` days) so a
  bulk import sets honest expectations. `almanac export` writes the full
  library (entries, tags, themes, collections, surfacings, reflections) as
  one JSON document. `almanac import --starter` loads the committed starter
  pack (`data/starter_quotes.json`, ~50 public-domain quotes, pre-1929
  authors, pre-tagged with themes) so the product is useful on day one.
- **FR-6 Scheduling state & interval update (hard part A).** Scheduling
  state per entry is *derived*: a pure left-fold over the entry's ordered
  surfacing and reflection events (DATA_MODEL.md §SchedulerState). State:
  `exposure_count`, `last_surfaced_on`, `interval_days`, `flat_streak`.
  After the debut surfacing, `interval_days := I0 = 10`. Each subsequent
  surfacing first *applies the grade of the previous surfacing's
  reflection* (or `none` if the user never reflected):

  | grade of prior surfacing | multiplier | flat_streak |
  |---|---|---|
  | `resonated` | × 1.25 | reset to 0 |
  | `applied`   | × 1.5  | reset to 0 |
  | `none` (no reflection) | × 1.9 | unchanged |
  | `flat`      | × 3.0  | += 1 |

  then, in order:

  ```
  flat_streak := per the table above
  if flat_streak >= 2:  I := hi_flat = 240          # demotion (D1)
  else:                 I := clamp(round_half_up(I * m), lo = 10, hi = 60)
  ```

  Rounding is half-up to whole days. **The clamp floor `lo` equals the
  cooldown `W` = 10 and `I0` = 10, so every interval the table can produce
  is realizable** — no branch is silently swallowed by the cooldown, and
  the four grades produce four distinct first-review intervals
  (13 / 15 / 19 / 30 days). The interval ordering is monotone in how well
  the entry is working: `resonated` (keep it close so the next surfacing,
  which FR-9 turns into an `act` prompt, can convert it to action) <
  `applied` (it produced action; hold a working cadence) < `none` (no
  signal; drift back) < `flat` (recede fast). Note the deliberate
  inversion of SM-2 (D1): a *negative* signal lengthens the interval,
  because the objective is portfolio value delivered, not per-item
  retention.

  Effective interval used everywhere due-ness is computed:

  ```
  I_eff(e) = max(W, min(interval_days, pinned_cap = 21) if e.pinned else interval_days)
  ```

  `flat_streak >= 2` demotes the entry to `hi_flat`; `flat_streak >= 3`
  additionally marks it an archive candidate (surfaced via FR-14; never
  auto-archived). A subsequent non-flat grade resets the streak and the
  entry re-enters normal rotation at `clamp(round(hi_flat * m), 10, 60)`
  = 60 days. The fold is exercised directly by tests and by the replay
  gate (M7): a materialized `scheduler_state` cache row must always equal
  the fold of the event log.

- **FR-7 Daily selection (hard part A).** Pure function
  `select(library, history, date D, seed, params) -> [(entry, select_pool)]`
  for slots `t = 0 … k−1` (batch size k, default 1, max 5), computed
  sequentially so earlier slots affect later ones. Every surfacing records
  which branch produced it in `select_pool` (DATA_MODEL.md §Surfacing) —
  this stamp is the scheduler's only "counter", and because it lives in the
  event log the engine stays a pure function of history (D3).

  *Eligibility:* `E` = active entries not already picked today whose last
  exposure (daily or extra draw) is at least `W = 10` days before D
  (never-seen entries are always eligible).

  Branches, in strict precedence order:

  1. **`pinned_rescue`** — if any `e ∈ E` is pinned, has been surfaced
     before, and `D − last_surfaced_on >= P_rescue = 32`, take the one
     with the largest `D − last_surfaced_on` (jitter tie-break). This is
     what *delivers* the pinned promise; it costs `n_pinned / P_rescue`
     slots per day (0.25/day at 8 pinned, k = 1) and bounds the worst-case
     pinned gap at `P_rescue + Δ·ceil(n_pinned / k)` days, where Δ is the
     largest calendar gap between materialized days (Δ = 1 for a daily
     user). At k = 1, ≤ 8 pinned, daily use, that is 40 days — inside the
     45-day promise of US-5. `almanac pin` warns past that budget (FR-4).
  2. **`forced_novelty`** — else, if any never-seen entry in `E` has
     `age = D − captured_on > S = 90` days, the slot goes to the N-pool
     (starvation aging, D5); pick by `n(e)` below. This is the *only*
     mechanism that can exceed the novelty quota, and it is what bounds
     bulk-import drain (US-3, M2b).
  3. **`novelty` / `review`** — else, with `N` = eligible never-seen and
     `R` = eligible seen-and-due (`D >= last_surfaced_on + I_eff`), if
     **both are non-empty** the slot is *contested*: compute the realized
     novelty share `σ` (below) and pick N if `σ < ρ = 0.35`, else R.
  4. **`novelty_only` / `review_only`** — else, if exactly one of N, R is
     non-empty, take it.
  5. **`not_due`** — else, if any eligible seen entry exists (none due),
     take the largest `O(e)`; this is the only path that surfaces an entry
     before its interval elapses, and it is excluded from M4's schedule
     sample.
  6. **`relaxed`** — else, the **tiny-library fallback**: among all active
     entries not picked today choose min `last_surfaced_on` (ties by id),
     and mark the surfacing `relaxed_cooldown = true`. If even that set is
     empty, the slot yields no card.

  *Novelty share σ (resolves the calendar-vs-materialized ambiguity):*
  walk the `kind = daily` surfacings in `(on_date, slot)` order, keep only
  those whose `select_pool ∈ {novelty, review}` (**contested slots** —
  slots where the quota rule actually made a free choice), take the last
  `H = 28` including slots already assigned earlier today, and set
  `σ = count(novelty) / 28` (or `/count` if fewer than 28 exist; `σ = 0`
  when none exist, so a fresh library starts on novelty). Excluding
  forced, single-pool, rescue and fallback slots makes σ a measure of the
  controller's own behaviour rather than of pool supply, and makes the
  controller and EVALS M3 compute *the same statistic* — the bang-bang
  rule then pins σ inside `{9/28, 10/28}` = [0.321, 0.357], i.e. within
  0.03 of ρ.

  *Priorities* (never compared across pools — the pool is chosen first):

  ```
  n(e) = exp(-age / tau) + (age / S)^2        tau = 7, age = max(0, D - captured_on)
  O(e) = (D - last_surfaced_on) / I_eff(e)
  ```

  `n` is U-shaped: this week's captures surface hot, and never-seen entries
  climb back past fresh arrivals as they approach the forcing horizon
  (`n > 1` once `age > S`), so forced drains run oldest-first.
  `O` is a maximum-relative-delay rule; it equalizes `O` across the review
  pool, which is what produces the uniform `lambda` dilation described in
  the capacity identity.

  *Jitter & tie-break:* score = priority +
  `0.05 · u64(SHA-256(seed ‖ D ‖ t ‖ entry_id)[:8]) / 2^64`; argmax wins;
  exact ties break by ascending entry id. The per-entry hash makes
  selection order-independent and stateless-deterministic (D4). 0.05 is
  small relative to every priority gap that carries meaning.

  All parameters (W, ρ, H, S, τ, I0, P_rescue, multipliers, caps, k) live
  in `data/scheduler.json` with a `params_version`; every surfacing stamps
  the `scheduler_version` and `seed` used.

- **FR-8 Materialization & idempotence (hard part A).** `POST /today {date}`
  / `almanac today` materializes the daily surfacing set for a date at most
  once: if surfacings for D exist, return them unchanged (byte-identical
  render, M1f). **Arrow of time, all kinds:** a new surfacing's `on_date`
  must be ≥ the maximum `on_date` over *all* existing surfacings (daily and
  extra), and a `daily` materialization additionally requires
  `on_date > ` the maximum existing `daily` `on_date`. Future-dated
  surfacings are rejected at the edges (`on_date > Clock.today()` → 422).
  Together these keep the FR-6 fold's ordering — and therefore every
  derived scheduler state — append-only. Missed days are simply never
  materialized; there is no backfill (non-goal 8): the overdue ratio
  absorbs gaps naturally.

  *Extra draws* (`POST /draws`, optional theme/collection filter)
  materialize a `kind = extra`, `select_pool = extra` surfacing
  immediately, with `date` defaulting to `Clock.today()` at the edge and
  subject to the same monotonicity rule. Candidate set = eligible active
  entries matching the filter; pick order: never-seen by `n(e)`, else due
  reviews by `O(e)`, else eligible not-due reviews by `O(e)`, else the
  relaxed fallback within the filter; same jitter. Draws count as exposures
  in the FR-6 fold (they start the cooldown and shift subsequent due dates)
  but are **excluded from σ** (they are not daily slots and never carry
  `select_pool ∈ {novelty, review}`) and never occupy a daily slot.

- **FR-9 Prompt selection (hard part B).** For a surfacing of entry e with
  prior exposure count n and last reflection grade g:
  1. *Kind:* base kind = `[reflect, act, reframe, connect][n mod 4]`;
     override — g = `resonated` ⇒ `act` (it landed; convert to an if-then
     plan, D6), g = `applied` ⇒ `connect` (consolidate and generalize);
     otherwise base.
  2. *Candidates:* templates whose theme ∈ e.themes, or theme = `general`
     iff e has no themes; minus the template ids of e's last
     `R_p = 6` surfacings; filtered to the chosen kind — if empty, try kinds
     in rotation order from the chosen one; if still empty, drop the
     recency exclusion, retry once, and set
     `prompt_recency_relaxed = true` on the surfacing.
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
  serve the template rendering, set `personalize_fell_back = true`.
  **Scope of the guarantee:** the validator bounds structure, length and
  slot integrity. It cannot detect a fluent, well-formed rewrite that says
  the wrong thing; EVALS M9 documents that boundary with report-only
  off-spec cases. The live `LLMPersonalizer` activates only when
  `ALMANAC_LLM_API_KEY` is set (`ALMANAC_LLM_MODEL`,
  `ALMANAC_LLM_BASE_URL`; default provider Anthropic); optional extra,
  never imported by tests or evals.
- **FR-11 Reflection log.** `reflect(surfacing_id, grade ∈ {applied,
  resonated, flat}, text?)` appends an immutable reflection; exactly one
  per surfacing (unique constraint); allowed only while the surfacing is
  the entry's most recent exposure (else 409 — the fold has moved on).
  Because FR-8 makes all surfacings monotone in `on_date`, "most recent
  exposure" is unambiguous. Reflections are browsable per entry and
  globally by date range (the journal). Grades drive FR-6; `text` is the
  user's applied-it note (generation effect: writing beats re-reading, D6).
- **FR-12 Search & browse.** Full-text search over text, author, source,
  and note via SQLite FTS5 with the porter tokenizer; results ranked by
  FTS5 `bm25()` ascending with entry-id tie-break. Listing filters: status,
  pinned, tag, theme, kind; combinable. If the interpreter's SQLite lacks
  FTS5, the repository falls back to a normalized-token LIKE scan over the
  same four columns, returning **the same result set** (any entry
  containing all query tokens after FR-1 normalization + porter stemming),
  ordered by *descending count of distinct matched query tokens, then
  ascending entry id*. The fallback ordering is documented, deterministic,
  and explicitly **not** bm25 parity — relevance ranking differs, set
  membership does not.
- **FR-13 Collections.** Named, described, manually ordered sets of
  entries (add/remove/reorder). Collections scope *extra draws* only —
  the daily scheduler always draws from the whole active library
  (deferring per-collection scheduling keeps the fold single-stream,
  non-goal 6).
- **FR-14 Stats.** `almanac stats` reports: library counts by status/kind;
  coverage (% of active entries surfaced ≥ once); exposure histogram;
  current open streak (consecutive materialized days ending today) and
  reflect streak (materialized + ≥ 1 reflection); realized novelty share
  over the trailing 28 contested slots vs ρ; pinned entries with
  days-since-seen and the current guarantee bound; archive candidates
  (`flat_streak >= 3`) with their streaks; and the **capacity block** —
  review demand `L`, capacity `C`, stretch `lambda` (median realized `O`
  over the last 90 materialized days), with the advisory "raise batch k to
  N" when `lambda > 3`.
- **FR-15 API.** FastAPI app per the sketch below; thin — validation,
  service calls, serialization.
- **FR-16 CLI.** Typer app per the sketch below; same service layer as the
  API; every read command has `--json`; `today` renders a plain-text card
  (no color dependencies). *Non-functional target (not gated):* every
  command completes well under a second on a ≤ 5,000-entry library, since
  all algorithms are O(n) per invocation. No wall-clock assertion is added
  — CONVENTIONS.md forbids wall-clock dependence in evals and timing
  assertions in unit tests are flaky in CI (D8).
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
12. **No automatic capacity management.** The tool measures and reports the
    stretch factor and recommends a larger `k`, but never changes `k`, ρ,
    the intervals, or the pinned set on the user's behalf. Nor does it
    re-derive parameters from usage — parameter tuning is a reviewed data
    change with the eval gates as guardrails (D15).
13. **No semantic validation of personalized prompts.** FR-10 guarantees
    well-formedness only (US-8).

## Architecture

```
projects/almanac/
  src/almanac/
    models.py               # Pydantic v2 domain models + enums (DATA_MODEL.md)
    service.py              # orchestration shell: capture, materialize day, draw, reflect,
                            #   curate. Holds the injected Repository + adapters. NOT pure —
                            #   this is the imperative boundary (D11).
    engine/                 # pure: no I/O, no clock, deterministic given inputs
      normalize.py          # FR-1: NFKC/casefold pipeline, normalized_hash, tokenizer + porter stem
      themes.py             # FR-3: taxonomy load, keyword suggester
      scheduler.py          # FR-6/7/8: state fold, pools, slot rule, priorities, jitter
      prompts.py            # FR-9: kind rotation, candidate filter, seeded pick, slot render
      attribution.py        # FR-2: pure normalized matching against the dataset
      validate.py           # FR-10: personalizer output validator
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
    scheduler.json          # W, ρ, H, S, τ, I0, P_rescue, multipliers, caps, k, params_version
    misattributions.json    # ~50 curated misattribution records (FR-2)
    starter_quotes.json     # ~50 public-domain starter entries (FR-5)
  evals/                    # fixtures/, personas.py, faulty_personalizer.py,
                            # simulate.py, metrics.py, run.py, test_gates.py
```

**Purity boundary (D11).** The `engine/` modules listed above are pure
functions — CONVENTIONS.md's "no network, no filesystem, no clock" applies
to them literally and is testable by inspection (no imports of `sqlite3`,
`pathlib`, `datetime.date.today`, `random`, `httpx`). `service.py` sits
*outside* `engine/` precisely because it holds an injected Repository and
therefore performs I/O; it contains no business rules, only sequencing:
load state → call engine → persist result.

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
PATCH  /entries/{id}                     # 422 if text edit changes normalized_hash post-surfacing (FR-4)
POST   /entries/{id}/pin                 DELETE /entries/{id}/pin
POST   /entries/{id}/archive             POST   /entries/{id}/restore
POST   /entries/import                   # {format: json|csv|starter, payload?} → report + drain horizon
GET    /entries/export
POST   /today                            # {date} materialize-if-absent → card(s)  (FR-8)
GET    /today?date=                      # read-only; 404 if not materialized
POST   /draws                            # {date?, theme?, collection_id?} → extra card
                                         #   422 if date < max surfacing on_date, or in the future
GET    /surfacings?entry_id=&from=&to=   GET /surfacings/{id}
POST   /surfacings/{id}/reflection       # {grade, text?}  → 409 if superseded (FR-11)
GET    /reflections?entry_id=&from=&to=
GET    /themes                           GET /themes/{id}/templates
GET    /tags
POST   /collections                      GET /collections        GET /collections/{id}
PATCH  /collections/{id}                 DELETE /collections/{id}
PUT    /collections/{id}/entries/{eid}   DELETE /collections/{id}/entries/{eid}
GET    /stats?date=                      # includes the capacity block (FR-14)
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
almanac edit ENTRY_ID [field flags]               # text edits: FR-4 rule
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
   the interval, two consecutive flats demote the entry to `hi_flat` and it
   effectively leaves rotation, and three route it to archiving. The
   demotion (not just the multiplier) is what makes the feedback loop
   *visible*: with multipliers alone, `applied` × 1.5 and `flat` × 3.0
   differ by less than a factor of two per exposure, which over a
   one-year horizon is one exposure of difference — indistinguishable
   from noise. With the demotion, a dud stops recurring within three
   exposures. EVALS M6 gates exactly this and fails (0.5 vs a 0.35 gate)
   if the demotion is removed. Non-goal 10 states the boundary.
2. **Re-exposure has an inverted-U, hence the hard cooldown.** Mere
   exposure increases liking (Zajonc 1968) but declines past moderate
   repetition (Berlyne's 1970 two-factor account; Bornstein's 1989
   meta-analysis shows the effect weakening with overexposure). W = 10
   days is the floor under *any* code path except the tiny-library
   fallback, which is explicit and flagged. To keep the cooldown from
   silently overriding the reflection loop, `I0` and the interval clamp
   floor both equal W — the cooldown is a *consequence* of the declared
   schedule, never a hidden correction to it.
3. **Deterministic quota mixing instead of a bandit.** Novelty-vs-review is
   an explore/exploit tradeoff (Robbins 1952), but stochastic bandits are
   unauditable and unseedable in the way CONVENTIONS.md demands. The
   trailing-share rule is a proportional-share scheduler in the lineage of
   lottery/stride scheduling (Waldspurger & Weihl, *Lottery Scheduling*,
   OSDI '94; stride scheduling in Waldspurger & Weihl 1995,
   MIT/LCS/TM-528) and nginx's smooth weighted round-robin: deterministic,
   drift-free, and inspectable from the surfacing log alone. Measuring the
   share over **contested slots only** (FR-7) is the load-bearing detail:
   a share measured over all slots is corrupted by supply droughts and
   forced drains, so the controller would chase a target it cannot hit and
   the metric would measure the fixture rather than the policy.
4. **Stateless seeded jitter.** Variety comes from
   `SHA-256(seed ‖ date ‖ slot ‖ entity_id)` mapped to [0, 0.05) — a pure
   function, order-independent, and reproducible without PRNG state
   plumbing. 0.05 is small enough that a full priority point always
   dominates and large enough to break the monotony of exact-tie cohorts
   (e.g., a batch import).
5. **Starvation freedom by aging, with a *stated* drain cost.** The
   forced-novelty rule (any never-seen entry older than S = 90 days claims
   the slot) is the classic aging remedy for starvation in priority
   scheduling (Silberschatz et al., *Operating System Concepts*). It is
   not rare — a bulk import guarantees it fires — so the design owns the
   consequence: during a forced drain, reviews are suspended and the
   novelty share is *supposed* to be 1.0. Those slots are stamped
   `forced_novelty`, excluded from σ, and excluded from EVALS M3, and the
   drain length itself is gated instead (M2b), computed from the capacity
   identity rather than measured post-hoc.
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
   Graf 1978) and the testing effect (Roediger & Karpicke 2006).
7. **Templates over generation.** Prompts are curated data with slot-fill
   rendering; the LLM may only *personalize* under the FR-10 validator
   (assume-adversarial posture, mirroring the workspace's ethos project:
   fallback is silent-safe, so a flaky LLM can degrade style, never
   structure). The guarantee is about form, not truth (non-goal 13), which
   is why the feature is off by default. The prompt bank (~110 templates)
   is a first-class authoring deliverable with floors enforced by a
   data-validation test.
8. **Sixteen themes, fixed.** courage, discipline_and_habit,
   mortality_and_time, gratitude, honesty_and_integrity,
   attention_and_presence, relationships, adversity_and_resilience,
   creativity_and_craft, humility, purpose_and_ambition,
   simplicity_and_frugality, equanimity_and_anger, generosity_and_service,
   learning_and_growth, decision_and_action — chosen to cover the
   recurring subjects of the aphoristic canon (Stoics, Proverbs, Franklin,
   Emerson, La Rochefoucauld) while staying few enough that every theme
   can carry ≥ 6 quality templates and ≥ 12 labeled eval quotes.
   `general` is a template pool, not an assignable theme. Adding a theme is
   a data change + template and label authoring, no code.
9. **Suggestion, never silent auto-tagging.** The keyword suggester is
   deliberately a transparent lexicon scorer (the craft goes into the
   lexicons, as with ethos's router): wrong suggestions are visible and
   cheap to reject at capture time. A misfiled theme quietly poisons
   prompt pairing forever, which is why confirmation is in the loop and
   accuracy is gated on a *held-out* fixture half whose contents lexicon
   authors are procedurally and mechanically barred from tuning against
   (EVALS §4 hygiene rule + `test_lexicon_hygiene`).
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
    prompt text, template id, seed, `scheduler_version`, `select_pool`, and
    flags — so history survives template edits, param tuning, and seed
    changes; idempotent re-reads are byte-identical (FR-8); and the
    scheduler's own decisions are auditable after the fact without
    re-running it.
13. **Scheduler state is event-sourced with a materialized cache.** The
    fold (FR-6) is the truth; `scheduler_state` rows are a cache with a
    rebuild command and an equality gate (M7c). This makes the engine
    testable as a pure function and makes "the schedule responded to my
    reflection" auditable from the log.
14. **Whole-day granularity; the date is caller input.** The scheduler
    reasons in local calendar days (the user's mental unit); the engine
    never reads a clock (FR-17); edges default the date from the `Clock`
    adapter. Timezone subtleties collapse into "whatever date the caller
    says it is". All surfacings are monotone in `on_date` to keep the
    fold's arrow of time (FR-8).
15. **Parameter defaults are committed and versioned** (`scheduler.json`,
    stamped on every surfacing). Defaults: `W = 10`, `I0 = 10`,
    `lo = 10`, `hi = 60`, `hi_flat = 240`, multipliers
    {resonated 1.25, applied 1.5, none 1.9, flat 3.0}, `pinned_cap = 21`,
    `P_rescue = 32`, `rho = 0.35`, `H = 28`, `S = 90`, `tau = 7`,
    `k = 1`, `jitter = 0.05`, `prompt_reuse_window = 6`. ρ = 0.35 means
    roughly one new card per three contested slots — enough novelty to
    make capturing feel rewarded, enough review to make the library
    compound. `hi = 60` bounds how far a well-liked entry can drift away;
    `hi_flat = 240` is effectively "not this year" for a demoted one.
    Retuning is a reviewed data change with M2–M6 as guardrails and a
    `params_version` bump.
16. **FTS5 with porter tokenizer for search**; bm25() ranking with id
    tie-break keeps it deterministic. No embeddings (non-goal 5). A LIKE
    fallback guards exotic builds lacking FTS5 with equal recall and
    documented (non-bm25) ordering — FR-12.
17. **IDs are ULIDs** minted at the edges by an injected id factory
    (fixed-sequence factory in tests); the engine treats ids as opaque
    strings and orders by them only for tie-breaks (ULIDs sort by creation
    time, which makes tie-breaks stable and meaningful).
18. **Single user, no auth**; SQLite at `~/.almanac/almanac.db`
    (`ALMANAC_DB_PATH` overrides); env vars documented in README; no
    secrets in the repo.
19. **Public-domain-only committed text.** Starter pack and the labeled
    theme fixture quote only pre-1929 / clearly public-domain authors
    (Marcus Aurelius in Long's 1862 translation, Seneca in Gummere's,
    Epictetus, Franklin, Emerson, Thoreau, La Rochefoucauld, Goethe,
    Proverbs) — the repo stays license-clean. Simulation libraries use
    invented one-liners (content is irrelevant to scheduling).
20. **Line budget & scope valves.** *Accounting basis: hand-written Python
    under `src/`, `tests/`, and `evals/`. Committed JSON data
    (~110 templates, 16 lexicons, ~50 misattributions, ~50 starter quotes,
    192 labeled quotes, scenario specs) is a parallel authoring
    deliverable and is not counted.*

    | area | lines |
    |---|---|
    | `engine/` (scheduler ~380, prompts ~230, themes ~140, normalize ~120, attribution ~90, validate ~90) | ~1,050 |
    | `models.py` + `service.py` | ~400 |
    | `adapters/` | ~180 |
    | `store/` | ~470 |
    | `api/` | ~330 |
    | `cli/` | ~380 |
    | `tests/` (17 modules) | ~800 |
    | `evals/` (simulate ~230, metrics ~300, personas ~90, run ~130, test_gates ~70) | ~820 |
    | **total** | **~4,430** |

    That is above the 4,000-line ceiling, so the valves below are
    *expected* to be used, in this order, until the build lands under it:
    1. drop the `WikiquoteAttributionChecker` live adapter and the
       `check-attribution` CLI command, keeping capture-time flags (−90);
    2. drop the `fifo_rotation` eval baseline, keeping `random_eligible`
       (−70, and the gate set still defeats a single-trick policy because
       M2a/M2b/M4b/M5 pull in opposite directions);
    3. fold the report-only diagnostics into `run.py` string formatting
       instead of separate metric functions (−60);
    4. drop collections (FR-13 → non-goal), which also removes the S1
       collection-draw stream (−230);
    5. reduce themes 16 → 12, with lexicons, templates and the labeled
       fixture shrinking in the same commit (−120);
    6. drop batch `k > 1`, fixing k = 1 (−60).

    Valves 1–3 (−220) are near-certain; 1–4 (−450) brings the estimate to
    ~3,980. Any valve used must be recorded in REVIEW.md at build time.
21. **Assumptions:** grades are honest self-report (no anti-gaming needed
    for one user); the user materializes at most one daily set per calendar
    date and tolerates no-backfill semantics; library scale ≤ ~5,000
    entries (all algorithms are O(n) per day at worst, fine at this
    scale); reflection text may contain anything — it is never parsed,
    only stored and displayed; the user's capture rate stays at or below
    `rho * k` per day in the long run, and when it does not, the
    forced-novelty rule plus the FR-14 advisory are the declared response.
