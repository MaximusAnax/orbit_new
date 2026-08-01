# PointsMax

A single-user credit-card points and rewards maximizer. Tell it what cards you
hold, what balances you have, and what you want ("round-trip business NYC→Paris
in October", "maximize cash"), and a deterministic graph search over a
versioned rewards-world dataset returns ranked, step-by-step redemption plans
with honest math — including when the right answer is "pay cash and keep your
points".

There is no LLM in the loop. Transfers are irreversible, so the core is exact
integer arithmetic plus exhaustive-but-bounded search, gated by an eval suite
that checks every answer against an independent brute-force oracle.

The scope, data model, eval plan and scoping-review resolutions are frozen in
[`docs/`](docs/): [SCOPE.md](docs/SCOPE.md) (FR-1 … FR-16),
[DATA_MODEL.md](docs/DATA_MODEL.md), [EVALS.md](docs/EVALS.md),
[REVIEW.md](docs/REVIEW.md).

## Quickstart

```bash
cd projects
uv sync --all-packages
uv run pointsmax init
```

```
database: /home/you/.pointsmax/pointsmax.db
world:    v1.0.0 (as of 2026-07-01), hash 8291a3851bc3
loaded:   19 programs, 15 cards, 47 transfer edges, 15 cashout options, 19 valuations,
          45 award offers, 102 reference fares, 14 gazetteer cities
world validation: OK
```

Add the cards you hold and your balances, then ask what your points are worth:

```bash
uv run pointsmax wallet add-card chase_sapphire_reserve
uv run pointsmax wallet add-card amex_platinum
uv run pointsmax wallet set chase_ur 210000
uv run pointsmax wallet set amex_mr 130000
uv run pointsmax wallet show --today 2026-07-31
```

```
cards held:
  amex_platinum                    The Platinum Card (Amex)
  chase_sapphire_reserve           Sapphire Reserve (Chase)

program                    points     baseline   cash floor  travel floor
amex_mr                   130,000    $2,600.00      $780.00     $1,300.00
chase_ur                  210,000    $4,305.00    $2,100.00     $3,150.00
TOTAL                                $6,905.00    $2,880.00     $4,450.00
```

Three numbers, never one (FR-13): the **baseline** valuation, the **cash floor**
(the best *liquid* cash-out — statement credit or bank deposit only) and the
**travel floor** (the best guaranteed redemption, portal included). The portal
is never reported as cash.

Then plan a trip:

```bash
uv run pointsmax goal add "round-trip business NYC to Paris in October"
uv run pointsmax plan 1 --today 2026-07-31 --top 2
```

```
plan set 1 for goal 1 — verdict: book_with_points
  world v1.0.0 (8291a3851bc3), today 2026-07-31, 37159 search expansions

  #1  net $1,298.00   3.08 cpp   plan 1
     gross $4,200.00 - outlay $502.00 - points cost $2,400.00 = $1,298.00
     1. Transfer 120,000 Amex Membership Rewards to Air France-KLM Flying Blue (1:1, instant, no fee).
     2. Book Air France-KLM Flying Blue business NYC->PAR (2026-10): 60,000 points plus $251.00 in taxes and fees.
     3. Book Air France-KLM Flying Blue business PAR->NYC (2026-10): 60,000 points plus $251.00 in taxes and fees.
     ! irreversible_transfer: Transferring 120,000 points over amex_mr__flying_blue cannot be undone.
     ! seats_limited: flying_blue_bus_nyc_par_202610 shows only 2 seat(s) for 1 passenger(s).
     ! seats_limited: flying_blue_bus_par_nyc_202610 shows only 2 seat(s) for 1 passenger(s).

  #2  net $1,221.00   2.93 cpp   plan 2
     gross $4,200.00 - outlay $379.00 - points cost $2,600.00 = $1,221.00
     1. Transfer 70,000 Amex Membership Rewards to Air Canada Aeroplan (1:1, instant, no fee).
     2. Transfer 60,000 Amex Membership Rewards to Air France-KLM Flying Blue (1:1, instant, no fee).
     3. Book Air Canada Aeroplan business NYC->PAR (2026-10): 70,000 points plus $128.00 in taxes and fees.
     4. Book Air France-KLM Flying Blue business PAR->NYC (2026-10): 60,000 points plus $251.00 in taxes and fees.
     ! irreversible_transfer: Transferring 70,000 points over amex_mr__aeroplan cannot be undone.
     ! irreversible_transfer: Transferring 60,000 points over amex_mr__flying_blue cannot be undone.

  estimates from a versioned dataset, not financial advice; verify ratios and pricing with the program before moving points
```

When you actually perform a step, record it — irreversible steps are
confirmation-gated:

```bash
uv run pointsmax apply 1 --step 1 --yes-irreversible --at 2026-08-02T09:15:00Z
```

```
step 1 recorded at 2026-08-02T09:15:00Z
  amex_mr                  -120,000 ->       10,000  (transfer_out)
  flying_blue              +120,000 ->      120,000  (transfer_in)
  transfers are final; verify the posting in the program's account.
```

Balances are an append-only ledger, so replaying it from zero always reproduces
the current state (`pointsmax wallet ledger`).

## CLI

```
pointsmax init                                        create the DB, load + validate the world
pointsmax world info | validate
pointsmax profile show | set [--home-city NYC] [--default-pax N] [--name NAME]
pointsmax cards list [--issuer chase]
pointsmax wallet show [--today D] | add-card ID | remove-card ID
pointsmax wallet set PROGRAM POINTS | adjust PROGRAM DELTA [--reason R] | ledger [--program P]
pointsmax value PROGRAM POINTS [--today D]            baseline, cash floor, travel floor
pointsmax goal add "<free text>" [--today D]
pointsmax goal add --kind flight --from NYC --to PAR --cabin business --rt --month 2026-10
                   [--pax N] [--book-by DATE]
pointsmax goal add --kind stay --city PAR --nights 3 --month 2026-10
pointsmax goal add --kind cash [--program chase_ur]
pointsmax goal list | show ID | drop ID
pointsmax plan GOAL_ID [--top 5] [--today YYYY-MM-DD] [--max-hops 2]
pointsmax show PLAN_ID
pointsmax apply PLAN_ID --step N [--yes-irreversible] [--at TS]
```

Global options: `--db PATH` (default `~/.pointsmax/pointsmax.db`, also settable
via `POINTSMAX_DB`) and `--world DIR`. Exit codes: `0` success, `1` a domain
refusal (unknown program, declined confirmation, precondition failure), `2` a
usage error.

## API

```bash
uv run uvicorn pointsmax.api:app --reload
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness plus the loaded world version and hash |
| GET | `/world` | version, `as_of`, entity counts, staleness |
| POST | `/world/validate` | re-run the seven FR-1 invariants |
| GET, PUT | `/profile` | `display_name`, `home_city`, `default_passengers` |
| GET | `/cards` | card-product catalog (`?issuer=`) |
| GET | `/programs`, `/programs/{id}` | program + valuation + *your* active edges and options |
| GET | `/wallet` | cards held, balances, baseline / cash-floor / travel-floor totals |
| POST, DELETE | `/wallet/cards`, `/wallet/cards/{id}` | hold or drop a card product |
| PUT | `/wallet/balances/{program_id}` | absolute balance → a `set` ledger entry |
| POST | `/wallet/balances/{program_id}/adjust` | relative correction → an `adjust` entry |
| GET | `/wallet/ledger` | append-only ledger (`?program=&limit=`) |
| GET | `/valuations`, `/valuations/{id}?points=` | the FR-13 three numbers |
| POST | `/goals` | structured `GoalSpec` **or** `{"text": …}` (offline parser) |
| GET, PATCH | `/goals`, `/goals/{id}` | listing and status transitions |
| POST | `/goals/{id}/plans` | compute and persist a ranked `PlanSet` |
| GET | `/goals/{id}/plans?latest=1`, `/plan-sets/{id}`, `/plans/{id}` | stored plans |
| POST | `/plans/{id}/steps/{seq}/execute` | record a performed step → ledger entries |

Every failure returns one envelope — `{"code", "message", "detail"}` — with the
code mapped to a status in `src/pointsmax/api/errors.py`:

| Code | Status | Meaning |
|---|---|---|
| `not_found` | 404 | unknown goal, plan set, plan, step or held card |
| `unknown_entity`, `invalid_goal`, `goal_parse_failed`, `search_budget_exceeded` | 422 | the request names something the world does not know, or the search hit its explicit budget |
| `duplicate_card`, `invalid_transition`, `negative_balance`, `ledger_chain_broken`, `world_pin_mismatch`, `step_out_of_order`, `step_already_executed`, `insufficient_balance` | 409 | state conflicts |
| `confirmation_required` | 428 | an irreversible step needs `confirm_irreversible: true` |

`today` and `at` are request fields everywhere; the engine never reads a clock.

## Evals

```bash
cd projects
uv run python pointsmax/evals/run.py            # scorecard; exits non-zero on any gate failure
uv run pytest pointsmax/                        # tests + evals/test_gates.py
uv run python pointsmax/evals/run.py --regen    # re-run both generators and diff the fixtures
```

The suite measures the two capabilities the product lives or dies on — optimal,
constraint-correct search and exact value accounting with honest warnings:

| Metric | What it measures | Gate |
|---|---|---|
| M1a / M1b | the top plan's objective equals an independent brute-force oracle's, over 52 small + 12 stress scenarios | `= 1.0` |
| M2 | every emitted plan re-validates against a third implementation that reads the raw world files | `= 1.0` |
| M3 | ranked canonical forms, comparator flags, verdict and caveat params all match | `= 1.0` |
| M4 | 30 hand-computed accounting cases match to the cent | `= 1.0` |
| M5 | 60 committed utterances parse to the right `GoalSpec` or `ParseError` | `≥ 0.90` |
| M6 | 12 goals run against the *shipped* `data/world/` inside the expansion budget, valid and byte-reproducible | `= 1.0` |
| M7 | 200 seeded random scenarios match their committed oracle answers | `= 1.0` |
| D0 | determinism, ledger replay, world content-hash pin on execute | pass / fail |

Ground truth never comes from the engine: search optima come from
`evals/oracle.py` (exhaustive, unpruned, sharing no code with `src/`),
accounting truth is hand-computed with the arithmetic written into each case's
`rationale`, caveat truth is re-derived from the FR-10 table in
`evals/expected_caveats.py`, and parser truth is by construction. Every naive
baseline in `evals/baselines.py` is measured live and its ceiling asserted, so
fixture drift that erodes the engine-versus-baseline gap fails the suite instead
of passing quietly.

Regenerating the fixtures (all committed, all deterministic):

```bash
cd projects/pointsmax/evals
python make_worlds.py                 # the five fixture worlds
python author_fixtures.py             # accounting, parser, M6 and D0 fixtures
python generate_search_cases.py       # oracle + 4 self-checks -> search_cases.json
python generate_random_cases.py       # 10 seeds x 20 -> random_cases.json
```

## Layout

```
src/pointsmax/
  models.py            Pydantic v2 entities + enums (docs/DATA_MODEL.md)
  engine/              pure domain logic — no network, no clock, no filesystem
    money.py           integer money/points primitives (FR-8)
    world.py           FR-1 validation, content hash, FR-3 card gating
    goals.py           FR-4 goal construction, FR-6 offer matching
    parser.py          FR-5 rule-based natural-language parsing
    value.py           FR-8 portfolio-delta accounting, FR-13 valuation
    search.py          FR-7 booking sets, candidate lattice, branch and bound
    plan.py            FR-9 canonical order/ranking, FR-10 caveats + templates
    advisor.py         orchestration: (world, wallet, goal, today) -> PlanSet
    execution.py       FR-11 execution preconditions and ledger effects
  adapters/            provider interfaces + offline and live implementations
  store/               repository interface, SQLite and in-memory backends
  service.py           composition layer shared by the API and the CLI
  api/                 FastAPI app, request/response schemas, error catalog
  cli/                 Typer app
data/world/            the committed, versioned rewards dataset
evals/                 oracle, metrics, baselines, fixtures, scorecard, gates
```

## The rewards world

`data/world/` is a committed, versioned snapshot: 19 programs, 47 transfer
edges, 15 card products, 45 award offers, 102 reference fares and a gazetteer.
Every record carries a `source_note`; `version.json` pins a semver, an `as_of`
date and a sha256 content hash over the other files. The loader re-computes
that hash and runs every FR-1 invariant — unique ids, referential integrity,
transfer divisibility, one valuation per program, no value-increasing edge,
reference-fare coverage, gazetteer resolution — before the engine sees it.

The dataset is fixture-grade but modelled on real mid-2026 mechanics: Chase
transfers gated on holding a premium Sapphire/Ink card, the Amex excise-tax
offset fee (0.06¢/pt capped at $99) on transfers to US airlines, Marriott's
3:1 airline transfers in 3,000-point increments with +5,000 miles per 60,000,
and 2-day posting times on the slow edges. Valuations are modelled on Frequent
Miler's Reasonable Redemption Values and TPG's monthly valuations. Staleness is
surfaced as a caveat rather than silently fixed.

## Live adapters

Offline implementations are the default and are what the tests and evals
exercise. Live adapters live in `*_live.py` modules, are never imported by the
offline path, and activate only when their configuration is present.

| Adapter | Env vars |
|---|---|
| `LiveWorldProvider` (`adapters/world_provider_live.py`) | `POINTSMAX_WORLD_FEED_URL`, optional `POINTSMAX_WORLD_FEED_TOKEN` |
| `HttpAwardSource` (`adapters/award_source_live.py`) | `POINTSMAX_AWARD_FEED_URL`, optional `POINTSMAX_SEATSAERO_KEY` |
| `HttpFareReference` (`adapters/fare_reference_live.py`) | `POINTSMAX_FARE_FEED_URL`, optional `POINTSMAX_AMADEUS_KEY` |
| `LLMGoalParser` (`adapters/goal_parser_llm.py`) | `ANTHROPIC_API_KEY` plus the `llm` extra (`uv pip install 'pointsmax[llm]'`) |

Each raises a clear error when its configuration or optional dependency is
missing. The three HTTP adapters consume the same JSON schema as the committed
files, so no third-party response shape is invented. `LLMGoalParser` only turns
free text into the same validated `GoalSpec` the rule parser produces — it never
ranks, values or explains anything, and is never on the eval path.

## Safeguards

Not financial advice, implemented as behaviour rather than prose (FR-16): every
plan set carries the fixed disclaimer string; transfer and award-booking steps
are flagged irreversible and refuse to execute without explicit confirmation;
plans pin the world content hash and refuse to execute against different data;
stale valuations raise a `stale_world` caveat; and there is no card-acquisition
surface anywhere in the product.
