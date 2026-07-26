# Orbit Backend

FastAPI service for Orbit — voice capture, extraction pipeline, and relationship memory API.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `AUTH_DISABLED=true` for local dev without Supabase JWT.

## Run

```bash
# AUTH_DISABLED=true in .env for local playground / iOS without JWT (Auth deferred this build)
uvicorn app.main:app --reload --port 8000
```

- **Playground UI:** http://127.0.0.1:8000/playground/
- **API docs:** http://127.0.0.1:8000/docs
- **Health:** http://127.0.0.1:8000/health

## Tests

```bash
pytest
```
