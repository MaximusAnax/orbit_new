# Orbit Evaluation Harness

Run before merging pipeline or maintenance changes. Gates fail the build on regression.

## Commands

```bash
cd orbit-backend
source .venv/bin/activate
pytest evals/ tests/ -q
python -m evals.run   # prints JSON metric summary
```

## Metrics and gates

| Metric | Gate |
|--------|------|
| Entity precision/recall (stub fixtures) | ≥ 0.85 |
| Ambiguity recall | 1.0 |
| Proposal exact-match F1 | ≥ 0.80 |
| Supersede safety | 100% |
| Skip compliance (no facts for ambiguous persons) | 100% |
| Catchup purity | 100% |
| Maintenance rank stability | snapshot match |

Stub LLM responses are deterministic for CI. Live-LLM scoring is optional (`ORBIT_EVAL_LIVE=1`) and never gates CI.
