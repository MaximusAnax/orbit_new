# Web demo

One app, twelve products, one port.

```bash
cd projects
uv sync --all-packages
uv run python web/seed.py          # fill every project with demo data
cd web && npm install && npm run build
cd .. && uv run python web/server.py
```

Then open <http://127.0.0.1:8000>.

## How it fits together

`server.py` mounts each project's `create_app()` under `/api/<slug>` — 288 routes
in a single process — and serves the built UI from `dist/`. Without it, demoing
would mean twelve uvicorn processes on twelve ports.

`seed.py` fills every project with demo data **through its own CLI**, so it
exercises the documented paths rather than writing to the databases directly. It
also generates dresscast forecasts for today onward, because that project's
offline weather adapter reads one fixture per date and the committed fixtures are
dated for eval scenarios.

## Developing

```bash
uv run python web/server.py --reload   # API on :8000
cd web && npm run dev                  # UI on :5173, proxies /api to :8000
```

## Layout

```
web/
  server.py          gateway: mounts the twelve APIs, serves the UI
  seed.py            demo data for every project
  src/
    app.tsx          shell: launcher, routing, theme, per-project accent
    lib/api.ts       one client per project; structured errors
    lib/hooks.ts     useQuery / useMutation with explicit loading+empty+error
    ui/tokens.css    design tokens, both themes
    ui/kit.tsx       the shared component kit
    projects/        one module per project UI
```

## Conventions for a project screen

- Compose from `ui/kit` — a screen should be composition, not CSS.
- Route every fetch through `Async`, which forces loading, empty and error to be
  handled. A blank panel on failure is the UI equivalent of a swallowed exception.
- Show the product's hard part. Ethos renders every citation marker and its source
  because unfabricated citations are what that product lives on.
- Accent colour comes from the shell via `--accent`; never hard-code a hue.
