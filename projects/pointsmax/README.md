# PointsMax

A single-user credit-card points and rewards maximizer. Tell it what cards you
hold, what balances you have, and what you want ("round-trip business NYC→Paris
in October", "maximize cash"), and a deterministic graph search over a
versioned rewards-world dataset returns ranked, step-by-step redemption plans
with honest math — including when the right answer is "pay cash and keep your
points".

The scope, data model, eval plan and scoping-review resolutions are frozen in
[`docs/`](docs/). This README covers how to run what is built.

## Status

Domain models, engine, adapters, store and the engine test suite are
implemented. The REST API, the CLI and the eval suite are the next stage.

## Quickstart

```bash
cd projects
uv sync --all-packages
uv run pytest pointsmax/ -q          # tests
uv run ruff check pointsmax/         # lint
```

```python
from datetime import date

from pointsmax.adapters import CommittedWorldProvider, RuleBasedGoalParser
from pointsmax.engine.advisor import compute_plan_set
from pointsmax.models import Wallet

world = CommittedWorldProvider().load()  # loads + validates data/world/
today = date(2026, 7, 31)

goal = RuleBasedGoalParser(world).parse("round-trip business NYC to Paris in October", today=today)
wallet = Wallet(
    cards=["chase_sapphire_reserve", "amex_platinum", "capital_one_venture_x"],
    balances={"chase_ur": 210_000, "amex_mr": 130_000, "marriott_bonvoy": 150_000},
)

plans = compute_plan_set(world=world, wallet=wallet, goal=goal, today=today)
print(plans.verdict)
for step in plans.plans[0].steps:
    print(step.seq, step.explanation)
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
  store/              repository interface, SQLite and in-memory backends
data/world/            the committed, versioned rewards dataset
```

## The rewards world

`data/world/` is a committed, versioned snapshot: ~19 programs, ~47 transfer
edges, 15 card products, ~45 award offers, reference fares and a gazetteer.
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

Offline implementations are the default and are what the tests exercise. Live
adapters live in `*_live.py` modules, are never imported by the offline path,
and activate only when their configuration is present.

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
