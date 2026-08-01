# ethos

Ask a moral question in plain language and get faithful, steelmanned answers from
ten religious and philosophical traditions side by side — every position grounded
in named public-domain primary texts, every quotation verbatim and machine-verified
against a committed corpus, every perspective ending in curated further reading.

**Never a verdict. Never a made-up citation.** The corpus is authored and committed;
the engine routes, retrieves and assembles; nothing morally substantive is generated.

```
$ uv run ethos ask "is it wrong to lie to spare someone's feelings?"

=== Honesty and deception ===

Matched topic: Honesty and deception (honesty_and_deception) — confidence 0.71
Other topics considered: Speech and gossip (speech_and_gossip) 6.40; ...

--- Christianity — forbidden ---
Speech is answerable to the God of truth: a disciple's yes should mean yes …
  • Jesus commands plain, reliable speech that needs no oath to prop it up. [C1]
      “But let your communication be, Yea, yea; Nay, nay: for whatsoever is more
      than these cometh of evil.”
      — Matthew 5:37 — The Holy Bible, King James Version, trans. the King James
      translators (1611)
      Context: From the Sermon on the Mount, on oaths …
  Note: Augustine and Aquinas prohibit all lying absolutely; many later Christians …
  Further reading:
      - Sissela Bok, “Lying: Moral Choice in Public and Private Life” (1978) [book]

--- Judaism — context_dependent ---
…

Where traditions agree and differ:
  forbidden: Christianity, Islam, Kantian deontology
  context_dependent: Judaism, Confucianism, Utilitarianism
```

## Quickstart

```bash
cd projects
uv sync --all-packages
uv run ethos init                                  # load + validate the corpus, create the DB
uv run ethos ask "what do I owe my aging parents?"
uv run ethos ask "should I report my father's tax fraud?" --tradition judaism --tradition kantian_deontology
uv run ethos topics                                # what the corpus can answer
uv run ethos topic suicide_and_self_harm           # detail, coverage, reading list
uv run ethos reading forgiveness_and_revenge       # de-duplicated further reading
uv run ethos corpus stats                          # counts, coverage matrix, substance ratios
uv run ethos corpus validate                       # the C-gates, same predicates the evals use
uv run uvicorn ethos.api.app:create_app --factory  # the REST API
```

`ethos ask --json` prints the raw `AnswerBody`; `ethos history` and `ethos show <id>`
replay any past question exactly as it was verified and shown.

## What it does

| | |
|---|---|
| **Corpus** | 154 curated positions over 24 topics x 10 traditions, 249 passages from 40+ named public-domain editions, 142 distinct further-reading entries |
| **Routing** | BM25 (Okapi, k1 = 1.5, b = 0.75) over hand-expanded topic documents plus a phrase bonus, with an IDF-weighted query-coverage floor for abstention. Deterministic, offline, no embeddings |
| **Refusal** | Moral questions outside the taxonomy (gene editing, workplace surveillance, abortion, gambling, immigration policy) are refused with the three nearest topics and a browse hint — never stretched onto the nearest topic |
| **Composition** | Templated assembly of curated data: stance, summary, reasoning points, verbatim quote blocks with locator + source line + context note, agreement map, further reading. No synthesis, no ranking of traditions, no "overall answer" — there is no code path that produces one |
| **Verification** | Every answer is re-verified after composition (FR-8 checks (a)-(i)) against the corpus and the pre-polish envelope; the store refuses to persist an unverified answer |
| **Polish** | An optional LLM adapter may rewrite connective prose only. It is assumed adversarial: output is parsed back, re-verified, and discarded on any violation (`polish_fell_back = true`) |

## Endpoints

```
GET  /health                          GET /corpus/stats
GET  /traditions                      GET /traditions/{id}
GET  /topics?tradition=               GET /topics/{id}       GET /topics/{id}/reading
GET  /passages/{id}
POST /questions                       # {text, asked_at?, traditions?, topic_id?, polish?}
GET  /questions?limit=&offset=        GET /questions/{id}
GET  /answers/{id}                    GET /answers/{id}/text
```

Edge semantics: `polish` without `ETHOS_LLM_API_KEY` is **400 `polish_unavailable`**
(CLI exit 2); an FR-8 failure on the un-polished path is **500 `integrity_failure`**
naming the failing check and passage (CLI exit 3); a refusal is **200** with
`outcome = refused_out_of_scope` — it is a successful answer to a question the corpus
cannot address; `asked_at` is defaulted at the edge, never read by the engine.

## Evals

```bash
uv run python ethos/evals/run.py                    # scorecard
uv run python ethos/evals/run.py --sweep            # (tau, kappa) tradeoff curve
uv run python ethos/evals/run.py --write-baselines  # by hand, in the same commit
uv run pytest ethos/                                # unit + integration + gates
```

Measured on the committed fixtures (baselines are the naive reference implementations
in `evals/metrics.py`, recorded in `evals/baselines.json` and pinned by C19):

| Metric | What it measures | Baseline | Value | Gate |
|---|---|---|---|---|
| M1-direct | near-canonical phrasings | 0.677 | **0.990** | ≥ 0.97 |
| M1-coll | informal first-person phrasings | 0.208 | **0.875** | ≥ 0.85 |
| M1b | oblique scenarios (visible) | 0.000 | **0.764** | ≥ 0.70 |
| M1b′ | oblique scenarios (hash-frozen holdout) | 0.000 | **0.646** | ≥ 0.55 |
| M1gap | M1b − M1b′ — the anti-memorisation instrument | — | **0.118** | ≤ 0.15 |
| M1c | recall@3 | 0.417 | **0.950** | ≥ 0.95 |
| M1d | dual-home questions, both homes in top-3 | 0.500 | **0.900** | ≥ 0.85 |
| M2a | out-of-scope refusal | 0.075 | **0.850** | ≥ 0.80 |
| M2a_near | refusal on near-miss moral questions | 0.000 | **0.846** | ≥ 0.68 |
| M2b | false refusal of in-scope questions | 0.000 | **0.023** | ≤ 0.05 |
| M3 | citation integrity, measured independently | — | **1.000** | = 1.0 |
| M4a | tamper detection recall (25 mutation classes) | 0.00 | **1.000** | = 1.0 |
| M4b | tamper false positives on clean polish | 1.00 | **0.000** | = 0.0 |
| M5 | answer completeness over 72 renders | 0.000 | **1.000** | = 1.0 |

Plus C1–C20 (schema, licensing, floors, substance ratios, fixture integrity, the
36-cell safeguard matrix, Porter conformance, freeze currency, out-of-scope
composition) and D0 (byte-identical routing, bodies and renders across hash seeds
and locales, and nine regenerating goldens). The whole suite runs in ~60 s.

Two design rules the suite keeps: **no instrument grades itself** — M3's checker
(`evals/independent_check.py`) imports only `json`, `pathlib`, `re`, `sys`, re-parses
the *printed* answer and resolves it against the *raw* corpus files, enforced by an
AST scan; and **every exact gate is observed failing** — `evals/fixtures/negative_controls/`
commits a broken artefact per gate and `test_negative_controls.py` asserts each one
is rejected.

## Safeguards (implemented behaviour, not a disclaimer)

`suicide_and_self_harm` and `euthanasia_and_end_of_life` carry a crisis-resources
block (988 Suicide & Crisis Lifeline, Befrienders Worldwide, findahelpline.com);
euthanasia adds `not_medical_advice`; law-touching topics carry `not_legal_advice`.
The block is committed data covered by `corpus_version`, rendered **first** in text
and JSON, treated as an immutable region by the verifier, and cannot be suppressed
by any flag — gated as a 36-cell matrix (C17), not a golden file.

This is a reading companion, not a decision oracle and not pastoral, therapeutic,
legal or medical counsel.

## Layout

```
src/ethos/
  models.py          Pydantic v2 domain models (DATA_MODEL.md)
  engine/            PURE: normalize, router, retrieve, compose, envelope, verify
  service.py         ask/render orchestration; owns the store and adapters
  adapters/          TopicRouter + ProsePolisher protocols, offline defaults, live LLM extra
  store/             Repository protocol, SQLite (default) and in-memory backends
  api/  cli/         thin FastAPI and Typer surfaces
data/                corpus/, router.json, safeguards.json, stopwords.txt
evals/               fixtures/, metrics.py, run.py, corpus_gates.py, independent_check.py,
                     faulty_polisher.py, safeguard_matrix.py, negative_controls.py, test_*.py
docs/                SCOPE.md, DATA_MODEL.md, EVALS.md, REVIEW.md (the frozen spec)
```

## Configuration

| Variable | Meaning |
|---|---|
| `ETHOS_DB_PATH` | SQLite file (default `~/.ethos/ethos.db`) |
| `ETHOS_LLM_API_KEY` | enables the live prose-polish adapter; absent ⇒ polish is refused, never silently downgraded |
| `ETHOS_LLM_MODEL`, `ETHOS_LLM_BASE_URL` | optional overrides (default provider Anthropic) |

The live adapter is an optional extra and is never imported on the test or eval path.

## Sources

Quoted text comes only from named public-domain editions: KJV (1611) and JPS Tanakh
(1917); Pickthall's Qur'an (1930); Aquinas (Dominican Fathers, 1911–25); Müller's
Dhammapada (SBE X, 1881); Rhys Davids' Dialogues of the Buddha; Telang's Gita
(SBE VIII, 1882); Bühler's Manusmriti (SBE XXV, 1886); Legge's Chinese Classics;
Long and Carter on the Stoics; Abbott's Kant (1873); Mill, Bentham, Sidgwick; Ross
and Jowett. Where no public-domain English translation exists (most hadith and
Talmud), the corpus **cites without quoting**: locator plus a curated paraphrase
under the exact label `[paraphrase — no public-domain translation quoted]`, capped
by gate C15 at ≤ 15% of cited passages.
