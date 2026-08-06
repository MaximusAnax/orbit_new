# Web demo

Twelve products, one app, one port.

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

Starlette does not run a mounted sub-application's lifespan, so the gateway's own
lifespan enters each child's. Three projects (flowlist, newsalpha, tickerpress)
build their service there and would otherwise serve requests with a `None`
service. A project that fails to mount is reported through `/api/projects` rather
than taking the demo down.

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

## Verifying

`e2e.mjs` drives each project's primary user story in a real browser against the
real API — this is the frontend translation of the projects' eval discipline. A
screen that renders but doesn't work fails, and so does one that works while
logging console errors, failing structural accessibility checks, or scrolling the
page sideways.

```bash
uv run python web/server.py --port 8077 &   # from projects/
cd web && npm run build
node e2e.mjs                    # all twelve
node e2e.mjs datasweep          # one
THEME=dark node e2e.mjs         # both themes are designed, so both are checked
SHOTS=1 node e2e.mjs            # screenshots to /tmp/shots
```

Each story asserts on real content — 8 traditions with 18 citations for ethos, 64
squares and a move round-tripping for chessmentor, an upload producing issues and
a review decision for datasweep — so it cannot pass on an empty screen.

## Layout

```
web/
  server.py          gateway: mounts the twelve APIs, serves the UI
  seed.py            demo data for every project
  e2e.mjs            per-project user stories + a11y, layout and theme checks
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
- Show the product's hard part. Ethos renders every citation to its source
  because unfabricated citations are what that product lives on; newsalpha leads
  with a placebo backtest because a signal that can't beat noise is the finding.
- Never render a claim the API didn't make. DataSweep shows the value it found
  and the rule that acted on it, because the engine returns no replacement value —
  an arrow to a blank would assert a deletion that never happened.
- Accent colour comes from the shell via `--accent`; never hard-code a hue.
  Semantic colours (`--ok`, `--warn`, `--bad`) are for state only, so a gate
  result is never confusable with branding.

## Known gaps

- **Single user, no auth.** Deliberate: this is a local demo. Hosting it would
  need auth plus per-user isolation across twelve SQLite databases.
- **One writer at a time.** Under heavy concurrent load a project's SQLite
  connection can block; the demo is single-user, so this is not addressed.
- **voicekin has no seeded profile.** Enrolment needs real consent recordings, so
  its screen documents the consent model rather than faking a voice.
