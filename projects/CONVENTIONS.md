# Projects — Engineering Conventions

These conventions bind every project under `projects/`. They exist so that twelve
independently useful products feel like one codebase. The existing Orbit app
(`orbit-backend/`, `orbit-ios/`) predates them and is not governed by this file.

## Cross-cutting decisions (locked with the owner, 2026-07-31)

1. **Stack:** Python 3.11+, one `uv` workspace rooted at `projects/pyproject.toml`.
2. **Deliverable depth (this pass):** domain engine + REST API + CLI + full test
   and eval suites + docs. No web UIs yet.
3. **Integrations:** offline-first. Every external dependency (streaming metadata,
   weather, news feeds, LLMs, TTS, pose estimation, market data) sits behind a
   provider interface with (a) a deterministic offline/fixture implementation used
   by tests and evals, and (b) a live adapter that activates only when credentials
   are configured. Evals must run hermetically — no network, no wall-clock
   dependence, seeded randomness.
4. **Layout:** new projects live in `projects/<slug>/`; Orbit stays where it is.

## Per-project layout

```
projects/<slug>/
  pyproject.toml           # package metadata; workspace member
  README.md                # what it is, quickstart, how to run evals
  docs/
    SCOPE.md               # problem, user stories, numbered FRs, non-goals, architecture
    DATA_MODEL.md          # entities, fields, invariants, storage mapping
    EVALS.md               # metrics with formulas, fixture strategy, gate thresholds
    REVIEW.md              # critique findings from the scoping loop and how each was resolved
  src/<pkg>/
    engine/                # pure domain logic — deterministic, no I/O
    adapters/              # provider interfaces + offline and live implementations
    store/                 # persistence (Repository pattern)
    api/                   # FastAPI app
    cli/                   # Typer CLI
  tests/                   # pytest unit + integration tests
  evals/
    fixtures/              # committed, deterministic fixture data
    metrics.py             # metric implementations
    run.py                 # python -m ... prints a scorecard
    test_gates.py          # pytest-enforced minimum thresholds
```

`<pkg>` is the slug with underscores. Small projects may collapse folders into
modules (`engine.py` instead of `engine/`), but the layering must stay visible.

## Layering rules

- **engine** is pure: given the same inputs (including an explicit `seed` where
  randomness is needed), it returns the same outputs. No network, no filesystem,
  no clock reads — time is always an input.
- **adapters** define a `Protocol`/ABC per external capability. Offline
  implementations are the default and are what evals exercise. Live adapters may
  import optional heavy deps guarded by extras.
- **store** exposes a Repository interface; default backend SQLite (stdlib
  `sqlite3` or SQLAlchemy 2.x), plus an in-memory backend for tests.
- **api** and **cli** are thin: parse/validate, call the engine/services,
  serialize. Business rules never live here.

## Libraries

- Pydantic v2 for all domain models and API schemas.
- FastAPI + uvicorn for APIs; Typer for CLIs.
- pytest (+ pytest-asyncio where needed) for tests.
- ruff for lint + format (config at workspace root).
- Heavy/optional deps (audio analysis, chess engines, pose estimation) go in
  optional extras and must not be required for the core test + eval suites.

## Evals

Every project ships an eval suite modeled on `orbit-backend/evals/`:

- **Metrics measure the hard part.** Each project's EVALS.md names the one or two
  capabilities the product lives or dies on, and the metrics quantify those —
  not incidental plumbing.
- **Gates are enforced.** `evals/test_gates.py` asserts minimum scores so `pytest`
  fails when quality regresses. Thresholds are chosen during scoping and recorded
  in EVALS.md with a rationale; they must be meaningfully above a naive baseline,
  and EVALS.md must state what that baseline scores.
- **Fixtures are committed and deterministic.** Synthetic or curated data checked
  into `evals/fixtures/`. Generation scripts (if any) are committed and seeded.
- **`evals/run.py` prints a human-readable scorecard** with each metric, its gate,
  and pass/fail — runnable with zero configuration.

## Quality bar

- Type hints throughout; `ruff check` clean.
- Every FR in SCOPE.md maps to at least one test or eval case; the mapping is
  auditable (test names reference FR ids).
- No secrets in the repo. Live adapters read credentials from environment
  variables documented in the project README.
- Anything involving a person's likeness (voice), finances, or health carries the
  safeguards named in its SCOPE.md (consent gating, not-financial-advice framing,
  not-medical-advice framing) as *implemented behavior*, not just disclaimers.

## Running things

```bash
cd projects
uv sync --all-packages         # install every workspace member (plain `uv sync` installs only the root)
uv run pytest <slug>/          # one project's tests + eval gates
uv run pytest                  # everything
uv run <slug> --help           # the project's CLI entry point
uv run python <slug>/evals/run.py   # scorecard
uv run ruff check .            # lint
```
