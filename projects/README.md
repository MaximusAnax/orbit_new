# Projects

Twelve independent products, each scoped (requirements, data model, eval plan) and
adversarially reviewed before implementation. Engineering rules:
[CONVENTIONS.md](CONVENTIONS.md). The pre-existing Orbit app lives at the repo root
(`orbit-backend/`, `orbit-ios/`) and is not part of this workspace.

## Status

All twelve pass verification — tests, eval gates, lint, and CLI — in one sweep:

```
cd projects && uv sync --all-packages && uv run python verify_all.py
```

| Project | What it does | Tests | Gates | Lines |
|---------|--------------|------:|------:|------:|
| [formcoach](formcoach/) | Science-based training programs; form review from pose keypoints | 598 | 10 | 15,957 |
| [flowlist](flowlist/) | Reorders a playlist so consecutive tracks transition seamlessly | 422 | 17 | 13,305 |
| [dresscast](dresscast/) | Weather-aware outfits assembled from your catalogued wardrobe | 409 | 30 | 16,739 |
| [datasweep](datasweep/) | Background, non-destructive cleaner for messy tabular files | 408 | 14 | 15,449 |
| [tickerpress](tickerpress/) | Tracks watchlist companies across finance news, deduped and ranked | 377 | 27 | 14,643 |
| [chessmentor](chessmentor/) | Opponent that calibrates to your level and coaches your weaknesses | 373 | 16 | 18,759 |
| [almanac](almanac/) | Quote bank with spaced resurfacing and application prompts | 368 | 19 | 13,145 |
| [pointsmax](pointsmax/) | Highest-value redemption path for credit-card points | 353 | 12 | 18,910 |
| [newsalpha](newsalpha/) | News-driven decision support across equities and crypto | 329 | 21 | 17,403 |
| [ethos](ethos/) | Moral questions answered across ten traditions, with real citations | 299 | 35 | 8,008 |
| [grailtrader](grailtrader/) | Buy/sell/hold guidance for second-hand designer clothing | 297 | 30 | 17,084 |
| [voicekin](voicekin/) | Consent-gated personal voice profiles for smart-home speech | 285 | 11 | 14,467 |
| **Total** | | **4,518** | **242** | **183,869** |

Every project ships a pure domain engine, offline-first adapters with live
counterparts, SQLite and in-memory repositories, a FastAPI app, a Typer CLI, and an
eval suite whose gates are enforced by `pytest`.

## What each one has to get right

Each project names the single capability it lives or dies on, and gates it. The
metric below is the one guarding that capability.

| Project | The hard part | Guarded by |
|---------|---------------|------------|
| formcoach | A specific, correct cue from noisy joint keypoints | fault-detection F1 0.957 (≥ 0.80) |
| flowlist | Near-optimal ordering — a travelling-salesman problem | 1.000 of exact optimum (≥ 0.97) |
| chessmentor | Estimating strength fast, and naming *why* a move was bad | 98.6 Elo error after 5 games (≤ 150) |
| datasweep | Knowing what *not* to touch; a confident wrong fix corrupts data | auto-fix precision 0.946, zero edits on traps |
| ethos | Citations that cannot be fabricated; honest abstention | citation integrity 1.0, tamper recall 1.0 |
| voicekin | The consent gate is only as strong as the voice matching | 0 of 330 impostor grants accepted |
| tickerpress | Apple the company vs. the fruit; one story across five outlets | ambiguous-mention accuracy 0.984 |
| newsalpha | Not fooling yourself with look-ahead in the backtest | hit rate 0.798 (≥ 0.72), leak canary 0/585 |
| pointsmax | Genuinely optimal transfer/redemption route, not a plausible one | 1.000 match with brute-forced optimum |
| grailtrader | A clean index from listings that lie (fakes, mislabels) | directional hits 0.834, placebo 0.017 |
| dresscast | One outfit across a cold morning and a warm afternoon | layering advantage +0.222 (≥ +0.20) |
| almanac | One slot a day without older entries disappearing forever | mix balance 0.029 (≤ 0.12), 0 violations |

## Process

Every project went through the same loop, and none of it was optional:

1. **Scope** — numbered, individually testable requirements; non-goals stated as
   plainly as goals.
2. **Critique** — two independent adversarial reviews per project: one attacking
   design rigor and fidelity to the original idea, one trying to prove the eval plan
   could certify a broken product. **284 findings** were raised and dispositioned,
   each recorded in the project's `docs/REVIEW.md` with its resolution or a reasoned
   rejection.
3. **Build** — pure engine first, then adapters, store, API and CLI. External
   services sit behind provider interfaces with deterministic offline
   implementations, so evals are hermetic: no network, no wall clock, seeded RNG.
4. **Harden** — an adversarial pass that must *empirically demonstrate each gate can
   fail*: mutate the engine, confirm the guarding metric drops below its threshold,
   revert, confirm recovery. Numbers recorded in `docs/REVIEW.md`. Lowering a gate to
   pass was forbidden; the few threshold changes each carry a worked impossibility
   proof.
5. **Verify** — `verify_all.py` runs every suite, scorecard, linter and CLI across
   the workspace and exits non-zero on any failure.

### What the process actually caught

The two review passes exist because both found real problems that would otherwise
have shipped:

- **Metrics grading themselves.** chessmentor's throttle-fidelity gate — the one
  whose purpose is to make a fake difficulty ladder impossible — scored the throttle
  using metadata the throttle itself wrote. A throttle that skipped blunder injection
  but recorded a convincing story passed it and all 15 related tests; proven by
  mutation, fixed with an independent re-search. ethos had the same shape in its
  citation checker and now re-parses rendered output against raw corpus files.
- **Gates a dumb baseline could pass.** flowlist's optimality gate was cleared by a
  construction-only greedy baseline. The fixture was hardened — seed-searched for
  genuinely difficult instances — until greedy fails at 0.9646 against the 0.97 gate
  while the real optimizer scores 1.000.
- **A systemic production bug.** Eight of twelve projects opened their SQLite
  connection with `check_same_thread=True` while FastAPI dispatches handlers to
  threadpool workers, so every DB-touching endpoint crashed under concurrency.
  Sequential requests masked it (anyio reuses idle workers LIFO) and API tests
  injected in-memory repositories, so the served path had zero real coverage. Found
  by auditing all twelve after the first two instances, each reproduced with a
  failing test before being fixed.

### Honest limits

Recorded in each project's `docs/REVIEW.md` rather than glossed:

- **formcoach** validates form analysis entirely against a synthetic pose generator;
  no real labelled clips exist yet, and its transfer metric prints `NOT AVAILABLE`
  rather than a fabricated number.
- **voicekin**'s speaker verification is measured on synthetic voices sharing the
  embedder's own feature family, so real-voice error rates are unproven.
- **ethos**'s quoted passages could not be diffed against canonical copies — outbound
  access to gutenberg.org and wikisource.org is blocked — so locators are
  structurally verified but not externally validated.
- Several projects' detection gates saturate at 1.000 on their committed fixtures:
  they guard against regression, not against near-miss implementations.
- Live adapters (streaming metadata, real RSS, market data, TTS, pose estimation) are
  credential-gated and exercised structurally only. The first live run is the real
  test.

## Layout

```
projects/
  CONVENTIONS.md      binding engineering rules
  verify_all.py       workspace-wide verification (doubles as CI)
  <project>/
    docs/             SCOPE, DATA_MODEL, EVALS, REVIEW, FR_COVERAGE
    src/<project>/    engine · adapters · store · api · cli
    tests/
    evals/            fixtures, metrics, run.py scorecard, test_gates.py
```

## Running things

```bash
cd projects
uv sync --all-packages              # install all twelve
uv run python verify_all.py         # everything (chessmentor's evals take ~30 min)
uv run python verify_all.py flowlist   # one project
uv run pytest flowlist/             # its tests + eval gates
uv run python flowlist/evals/run.py # its scorecard
uv run flowlist --help              # its CLI
```
