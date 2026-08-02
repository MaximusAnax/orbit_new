# Almanac

A personal quote-and-idea almanac. Capture what you save — quotes, ideas,
source, author, tags, your own note — and every day a deterministic, seedable
scheduler surfaces one card, balancing new captures against spaced re-exposure
of long-unseen entries, never repeating inside a ten-day window, guaranteeing
pinned favourites come back. Each card carries a theme-matched **application
prompt** ("When X happens, I will Y…", "Tonight, review the day as…"). You log
how it landed, and that reflection reshapes the schedule.

Saving is easy; *returning* is the unsolved part. Almanac closes the loop:
capture → schedule → surface with a prompt → reflect → the reflection reshapes
the schedule.

Local, offline, one SQLite file, no accounts, no sync, no notifications. See
[`docs/SCOPE.md`](docs/SCOPE.md) for the full design, the capacity identity the
scheduler is bounded by, and the research the four prompt kinds are grounded in.

---

## Quickstart

```bash
cd projects
uv sync --all-packages

uv run almanac init --seed 7          # create the DB, load the committed datasets
uv run almanac import --starter       # ~87 public-domain quotes, pre-tagged
uv run almanac today                  # today's card
uv run almanac reflect --last --grade applied --text "did it before lunch"
uv run almanac stats
```

The database lives at `~/.almanac/almanac.db`; `ALMANAC_DB_PATH` (or `--db`)
overrides it.

---

## CLI

```
almanac init [--seed N] [--rebuild]      almanac stats [--date D] [--json]
almanac add "TEXT" [...]                 almanac today [--date D] [--json]
almanac import FILE | --starter          almanac draw [--theme T | --collection ID]
almanac export [--out FILE]              almanac reflect [ID | --last] --grade G [--text N]
almanac list | search | show             almanac edit | pin | unpin | archive | restore
almanac tags | themes                    almanac collection create|list|show|add|rm|delete
almanac check-attribution [...]          almanac config get|set KEY [VALUE]
```

Every read command takes `--json`. Errors print to stderr and exit non-zero.

### Capture, with theme suggestions and misattribution flags

```console
$ almanac add "The impediment to action advances action. What stands in the way becomes the way." \
    --author "Marcus Aurelius" --source "Meditations (Long trans., 1862)" \
    --tag stoicism --note "For the weeks when the blocker is the work." --yes
Added 01KYWHZDS2CPFPZ1YEAX7GB5SD  [quote]
  The impediment to action advances action. What stands in the way
  becomes the way.
  suggested themes:
  1. decision_and_action (Decision & Action)  score 3
  2. adversity_and_resilience (Adversity & Resilience)  score 2
  3. creativity_and_craft (Creativity & Craft)  score 1
  themes set: decision_and_action
  tags: stoicism
```

Themes are **proposed, never silently assigned** (FR-3): without `--yes` the CLI
prints the top three and asks. A near-duplicate (same normalized text) warns and
asks before adding. A known misattribution is flagged as a footnote and never
blocks:

```console
$ almanac add "Insanity is doing the same thing over and over again and expecting different results." \
    --author "Albert Einstein" --yes
Added 01KYWFB1265EZR7SZ04QSHMHP1  [quote]
  [attribution: misattributed] No Einstein source exists; the earliest
  documented appearance is a 1981 Narcotics Anonymous text.
  Likely origin: 1981 Narcotics Anonymous approval-draft pamphlet
  (per Quote Investigator).
    https://quoteinvestigator.com/2017/03/23/same/
```

### The daily card

```console
$ almanac today
--------------------------------------------------------------------
2026-07-31  card 1/1  [daily:novelty_only]

  "A foolish consistency is the hobgoblin of little minds."
    -- Ralph Waldo Emerson (Self-Reliance (1841))

  themes: humility, learning_and_growth   tags: transcendentalism

  PROMPT (reflect)
  Tonight, find one thing you were confidently wrong about today.
  What would it have taken to notice at the time?

  entry 01KYWHZACEJM1TZKQQ7HPM595K   surfacing 01KYWHZE7A7E06HAJGA9RNMC9S
  reflect with: almanac reflect --last --grade applied|resonated|flat
--------------------------------------------------------------------
```

`[daily:novelty_only]` is the scheduler's decision provenance — which of the six
FR-7 branches produced this pick. It is stored on the surfacing and is what the
novelty controller reads back, so the scheduler keeps no hidden counters.
Re-running `almanac today` returns the identical card: a date is materialized at
most once.

### Reflection reshapes the schedule

```console
$ almanac reflect --last --grade applied --text "Rewrote the migration plan instead of routing around it."
logged applied on 01KYWHZE7A7E06HAJGA9RNMC9S
  A foolish consistency is the hobgoblin of little minds.
  the next surfacing will apply this grade; current interval 10d, flat streak 0
```

Grades drive the FR-6 interval table — and note the deliberate inversion of
SM-2: a *negative* signal **lengthens** the interval, because the objective is
value delivered by the portfolio, not retention of each item.

| grade | multiplier | first review at |
|---|---|---|
| `resonated` | × 1.25 | 13 days |
| `applied` | × 1.5 | 15 days |
| `none` (never reflected) | × 1.9 | 19 days |
| `flat` | × 3.0 | 30 days |

Two consecutive `flat` grades demote the entry to a 240-day interval — it
effectively leaves rotation; three list it as an archive candidate. Nothing is
ever archived automatically.

### Stats, including the capacity block

```console
$ almanac stats
almanac stats  2026-07-31
library      88 entries (88 active, 0 archived)
             quotes 85   ideas 3   pinned 0
coverage     1% of active entries surfaced at least once
exposures    0x:87, 1x:1
streaks      open 1d   reflect 1d
novelty      0.000 realized over the last 0 contested slots (target 0.35)

capacity (SCOPE.md capacity identity)
  k=1  rescue load r=0.000/day  capture rate A=3.143/day
  review capacity C=-2.143/day   review demand L=0.100/day
  stretch lambda=n/a (no review history yet)
```

Almanac serves `k` cards a day (default 1) and states the bound rather than
pretending it away. When review demand exceeds capacity the stretch factor
`lambda` rises: every entry's realized gap dilates by the *same* factor, so
relative spacing — the thing your reflections control — is preserved exactly.
Past `lambda > 3` the tool advises raising `k`. It never changes `k` for you.

---

## API

`uvicorn almanac.api:app` (or `from almanac.api import create_app`).

| method | path | notes |
|---|---|---|
| `GET` | `/health` | version, scheduler params version, seed, batch k |
| `POST` | `/entries` | 201 → entry + theme suggestions + attribution flags |
| `GET` | `/entries` | `status, pinned, tag, theme, kind, q, limit, offset` |
| `GET` | `/entries/{id}` | entry + scheduler state + full history |
| `PATCH` | `/entries/{id}` | 422 if a text edit changes `normalized_hash` post-surfacing |
| `POST`/`DELETE` | `/entries/{id}/pin` | pin returns the degraded-guarantee warning past `8·k` |
| `POST` | `/entries/{id}/archive`, `/restore` | |
| `POST` | `/entries/import` | `{format: json\|csv\|starter\|rows, payload?}` → report + drain horizon |
| `GET` | `/entries/export` | the whole library as one JSON document |
| `POST` | `/today` | materialize-if-absent; idempotent, byte-identical on re-read |
| `GET` | `/today?date=` | read-only; 404 if not materialized |
| `POST` | `/draws` | 201 → an extra card; `{date?, theme?, collection_id?}` |
| `GET` | `/surfacings`, `/surfacings/{id}` | `entry_id, from, to` |
| `POST` | `/surfacings/{id}/reflection` | 201; 409 once the entry has been surfaced again |
| `GET` | `/reflections` | the journal, by entry or date range |
| `GET` | `/themes`, `/themes/{id}/templates`, `/tags` | |
| `POST` | `/themes/suggest` | the FR-3 lexicon suggester, top 3 |
| `*` | `/collections…` | CRUD plus `PUT/DELETE /collections/{id}/entries/{eid}` |
| `GET` | `/stats?date=` | including the capacity block |
| `GET` | `/attribution/check?text=&author=` | |

Every non-2xx response is `{"error": {"code": ..., "message": ...}}`:

| status | code | condition |
|---|---|---|
| 404 | `not_found` | unknown entity |
| 404 | `not_materialized` | `GET /today` for a date never materialized |
| 409 | `conflict` | reflection exists, or the entry has been surfaced again |
| 409 | `duplicate` | duplicate collection name or id |
| 409 | `no_candidate` | a draw whose filtered candidate set is empty |
| 422 | `unprocessable` | invariant violation (FR-4 text edit, future date, unknown theme) |
| 422 | `out_of_order` | FR-8 arrow of time (backdated surfacing) |
| 422 | `validation_error` | malformed request body or query |

---

## Evals

```bash
uv run python almanac/evals/run.py     # the scorecard
uv run pytest almanac/                 # unit tests + the gates
```

The suite simulates four scenarios across three seeds — roughly twenty
simulated years — driving the *real* service against an in-memory store with
offline adapters, then measures nine metrics. It is hermetic: no network, no
wall clock, seeded randomness only. See [`docs/EVALS.md`](docs/EVALS.md).

Measured on the committed fixtures (`params_version = sched-1`):

| metric | value | gate | naive baselines |
|---|---|---|---|
| M1 constraint_compliance | 0 violations | `== 0` | 0 / 0 |
| M2a stream debut p50 / p95 | 2 d / 12 d | ≤ 7 / ≤ 21 | 32 / 127, 19 / 55 |
| M2b backlog_drain | 1.00 | `== 1.0` | 0.97, 1.00 |
| M2 hard bound (>180 d) | 0 | `== 0` | 3, 0 |
| M3 mix_balance | 0.029 | ≤ 0.12 | 0.31, 0.35 |
| M3 qualifying windows | 138 | ≥ 60 | — |
| M4a early_violations | 0 | `== 0` | 0, 0 |
| M4b proportional_fidelity | 1.66 | ≤ 3.0 | 2.77, 2.09 |
| M5 pinned_recurrence | 1.00 | `== 1.0` | 0.17, 0.00 |
| M6 feedback_responsiveness | 0.52 | ≤ 0.60 † | 0.82, 0.92 |
| M6 archive sub-check | 1.00 | `== 1.0` | — |
| M7 determinism_replay | 1.00 | `== 1.0` | — |
| M8 top1 / hit3 (held split) | 0.74 / 0.84 | ≥ 0.55 / ≥ 0.80 | 0.06 uniform |
| M8 worst_theme_hit3 | 0.50 | ≥ 0.50 | — |
| M9 recall / fpr | 1.00 / 0.00 | `== 1.0` / `== 0.0` | — |

† the one re-derived threshold; the derivation and its justification are in
[`docs/REVIEW.md`](docs/REVIEW.md) (finding B1).

Baselines are computed live by swapping only the FR-7 *choice* of entry —
`random_eligible` (uniform among eligible) and `fifo_rotation` (capture order,
skipping cooldown). Neither passes the gate *set*: fifo drains a backlog
perfectly and fails debut latency, spacing and the pinned guarantee.

Ground truth never comes from the system under test: M1/M7's predicates are
re-derived inside `evals/metrics.py` from the committed data files (the module
is mechanically barred from importing `almanac.engine`), M6 is measured against
a latent quality planted in the fixture and never shown to the scheduler, M8 is
hand-labelled on a held-out split guarded by `tests/test_lexicon_hygiene.py`,
and M9's malformations are invalid by construction.

---

## Architecture

```
src/almanac/
  models.py       Pydantic v2 domain models and enums
  service.py      orchestration shell: capture, materialize, draw, reflect, curate
  factory.py      composition root — the only place adapters are selected
  engine/         pure: no I/O, no clock, no random. Time and seed are arguments.
    normalize.py  FR-1 canonical form and hashing        stemmer.py  Porter (1980)
    themes.py     FR-3 lexicon suggester                 jitter.py   SHA-256 keyed jitter
    scheduler.py  FR-6/7/8 fold, pools, priorities       prompts.py  FR-9 pairing
    attribution.py FR-2 match rule                       validate.py FR-10 validator
    stats.py      FR-14 report and the capacity identity
  adapters/       Clock, IdFactory, PromptPersonalizer, AttributionChecker
  store/          Repository interface, SQLite (FTS5) + in-memory backends
  api/            FastAPI app (routes.py) — `uvicorn almanac.api:app`
  cli/            Typer app (main.py) and the plain-text renderers
data/             themes, prompts, scheduler params, misattributions, starter pack
evals/            fixtures, personas, simulate, metrics, run, test_gates
```

The `engine/` modules are pure functions of their arguments: identical
`(state, date, seed)` produce byte-identical selections, prompts and renders.
`service.py` sits outside `engine/` precisely because it holds a Repository.

## Live adapters

Both are optional, env-gated, and never imported by tests or evals.

| adapter | activated by | what it needs |
|---|---|---|
| `LLMPersonalizer` | `ALMANAC_LLM_API_KEY` | plus optional `ALMANAC_LLM_MODEL` (default `claude-opus-5`) and `ALMANAC_LLM_BASE_URL` (default `https://api.anthropic.com`). Rewrites the rendered prompt; the output still passes the FR-10 validator, so it can degrade style but never structure. Off by default. |
| `WikiquoteAttributionChecker` | `ALMANAC_WIKIQUOTE=1` | no credentials; queries the public Wikiquote API for the claimed author's "Misattributed" section and chains its findings after the committed dataset. |

Other environment variables: `ALMANAC_DB_PATH` (database location),
`ALMANAC_DATA_DIR` (committed datasets). No secrets are stored in the repo.

The validator bounds *form*, not meaning: a fluent but wrong-headed rewrite
passes every check. That boundary is measured and printed by the eval suite
(`validator_boundary: 10/10 accepted`) rather than papered over, and it is why
the personalizer is opt-in and off by default.
