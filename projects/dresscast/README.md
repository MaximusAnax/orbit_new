# dresscast

Reads the day's hourly weather and assembles complete, ranked outfits from your
own photographed-and-tagged wardrobe — layered so a cold morning and a warm
afternoon are both comfortable, rain-proofed when it matters, occasion-
appropriate, color-coherent, laundry-aware, and never yesterday's outfit — with
the reasoning spelled out hour by hour. When the closet cannot meet the day, it
says exactly what the day demands and what is missing.

The specification is frozen in `docs/`: [SCOPE.md](docs/SCOPE.md) (numbered
FRs), [DATA_MODEL.md](docs/DATA_MODEL.md), [EVALS.md](docs/EVALS.md) and
[REVIEW.md](docs/REVIEW.md) (including the build-stage gate notes).

## Quickstart

```bash
cd projects
uv sync --all-packages

uv run dresscast --help                  # the CLI entry point
uv run dresscast add --name olive-field-jacket --category light_jacket \
    --colors olive --occasions casual,outdoor
uv run dresscast brief --date 2026-04-14        # works on an empty closet
uv run dresscast outfit --date 2026-04-14 --occasion work
uv run dresscast wear latest --rank 1 --date 2026-04-14
uv run dresscast serve --port 8000              # the REST API
```

By default state lives under `~/.dresscast/` (`--db`, `--config` and
`--data-dir` override it). Weather comes from the offline fixture provider —
JSON files named or keyed by date under `<data-dir>/weather/` — until the live
Open-Meteo adapter is explicitly enabled (see below). Every command accepts
`--json` for machine-readable output and `--units f` for Fahrenheit display.

## CLI tour (real output)

Adding a garment defaults everything from the category preset table
(SCOPE.md D1/D11) — a garment takes one line to catalogue:

```console
$ dresscast add --name olive-field-jacket --category light_jacket \
      --colors olive --occasions casual,outdoor
olive-field-jacket  (a8645049-60f4-44b7-8b4e-61501d40b565)
  category    light_jacket   layer role outer
  clo         0.40   formality 3   waterproofness 0   windproofness 0
  colors      olive(main)
  occasions   casual, outdoor
  style tags  -
  laundry     clean, 0 of 30 wears
  overridden  -
```

`brief` answers "what does today demand?" with no wardrobe at all (FR-16):

```console
$ dresscast brief --date 2026-04-14
Day brief for 2026-04-14  (wear window 07:00-22:00, met 1.6)
  feels like 3.7°C to 18.0°C; the day asks for 0.60-1.77 clo
  layer archetypes: base+mid, base+mid+shell, base+2mid+insulated shell

   hour     feels  req clo  archetype                    rain
  07:00     3.7°C     1.77  base+2mid+insulated shell    -
  08:00     4.9°C     1.67  base+mid+shell               -
  ...
  17:00    18.0°C     0.60  base+mid                     -
  21:00    14.8°C     0.87  base+mid                     -

  ! Coldest wear-window hour feels like 3.7°C at 07:00 — cover hands, head and neck.
```

`outfit` is the core loop (FR-8): ranked complete outfits, a smoothed layer
plan for the day, deterministically attached accessories, and classified
reasoning:

```console
$ dresscast outfit --date 2026-04-14 --occasion work
Recommendation 5a224930-f175-4609-a730-9dfae026d6f0 for 2026-04-14 — occasion work, k=3, met 1.6, window 07:00-22:00

  #1  score 1.0000   thermal 1.000 protection 1.000 color 1.000 style 1.000 variety 1.000
      base         white-oxford-shirt
      mid_1        green-lambswool-sweater
      outer        navy-wool-coat
      bottom       gray-thick-trousers
      footwear     brown-leather-boots
      accessory_1  gray-wool-hat
      accessory_2  black-leather-gloves
      - [day_thermal] Feels like 3.73°C at 07:00 and 18.00°C at 17:00 — the day asks for 1.77 clo down to 0.61 clo.
      - [layer_change] 11:00 — shed the green-lambswool-sweater. You are carrying the green-lambswool-sweater from here on.
      - [layer_change] 13:00 — shed the navy-wool-coat; put on the green-lambswool-sweater. You are carrying the navy-wool-coat from here on.
      - [layer_change] 15:00 — shed the green-lambswool-sweater. You are carrying the green-lambswool-sweater and navy-wool-coat from here on.
      - [cold_extremities] 07:00 feels like 3.73°C — taking the black-leather-gloves and gray-wool-hat; no scarf in the wardrobe.
      - [palette] Palette: 4 neutral and 1 accent colour (green) — harmony 1.00.
      - [variety] Nothing in this outfit has been worn before — fully fresh.
      ! note {'kind': 'advisory_gap', 'accessory_class': 'scarf', 'trigger': 'minimum feels-like 3.7°C is below 5°C'}
  ...
```

Wearing what it suggested advances the laundry counters transactionally
(FR-12/FR-3), and a dirty garment is never recommended again until washed:

```console
$ dresscast wear latest --rank 1 --date 2026-04-14
Logged 2026-04-14 (recommendation) as 85aeea59-6904-42b4-9c72-51c25c1616af:
  gray-wool-hat                1/999 clean
  black-leather-gloves         1/999 clean
  white-oxford-shirt           1/2 clean
  gray-thick-trousers          1/5 clean
  brown-leather-boots          1/999 clean
  green-lambswool-sweater      1/5 clean
  navy-wool-coat               1/30 clean
```

The full command set (`dresscast <command> --help` for options): `add`, `ls`,
`show`, `edit`, `suggest` (FR-2 attribute suggestions with explicit accept),
`forecast`, `brief`, `outfit`, `explain`, `wear` (incl. same-day `--undo`),
`laundry`, `history`, `serve`, `version`. Every command exits 0 on success and
non-zero with a structured `error [code]` line on failure.

## API summary (FR-17)

`dresscast serve` runs the FastAPI app (interactive docs at `/docs`). Errors
map to 4xx with the documented detail codes (`invalid_params`,
`unknown_garment`, `invalid_transition`, `no_extractor_configured`,
`wardrobe_too_large`, `infeasible_wardrobe`, `forecast_unavailable`).

| Method + path | Purpose |
|---|---|
| `GET /health` | liveness + version |
| `POST /garments` · `GET /garments` · `GET/PATCH /garments/{id}` | wardrobe CRUD and status transitions (FR-1, FR-3) |
| `POST /garments/{id}/photo` | attach a local photo by path, SHA-256 recorded (FR-2) |
| `POST /garments/{id}/suggest` · `GET /garments/{id}/suggestions` | stage extractor proposals (FR-2) |
| `POST /suggestions/{id}/accept` · `POST /suggestions/{id}/reject` | explicit accept with the category cascade, or reject (FR-2) |
| `GET /forecast?date=` | fetch + snapshot the day's hours (FR-4) |
| `GET /brief?date=&met=&window=` | the wardrobe-free day brief (FR-16) |
| `POST /recommendations` · `GET /recommendations/{id}` | assemble and re-read ranked outfits (FR-8, FR-13) |
| `POST /recommendations/{id}/wear` · `POST /wear` · `DELETE /wear/{log_id}` | wear logging and same-day undo (FR-12) |
| `POST /laundry` | reset listed or all dirty garments (FR-3) |

An `infeasible_wardrobe` response carries the FR-16 day brief plus the named
missing capability, so the API tells you what to buy, not just that it failed.

## Tests and evals

```bash
cd projects
uv run pytest dresscast/ -q               # unit + integration tests AND eval gates
uv run python dresscast/evals/run.py      # the full scorecard (hermetic, no network)
uv run ruff check dresscast/              # lint
```

`evals/run.py` prints one line per gated metric — value, gate, live-computed
baseline, margin, PASS/FAIL — plus per-scenario detail, and exits non-zero if
any gate fails. The suite measures the two things the product lives or dies on
(EVALS.md): day-long thermal layering (physics conformance against published
wind-chill/apparent-temperature tables, comfort against the achievable target,
search optimality vs exhaustive enumeration, layering advantage over a
hindsight static dresser) and wardrobe-constrained outfit quality over time
(an independent rule checker at 1.00, palette/style AUC against hand-labelled
outfits, three 14-day rollouts for variety/utilization, per-component lift over
a random-valid baseline, determinism incl. 1-ULP robustness). Gate thresholds
and the two margins re-derived against measured baselines during the build are
documented in EVALS.md §5.4 and REVIEW.md §Build-stage.

## Layout

```
src/dresscast/
  errors.py          structured error codes (FR-17's detail vocabulary)
  engine/            pure domain logic — deterministic, no clock, no I/O
    models.py        Pydantic entities + every named constant and preset table
    comfort.py       feels-like, required clo, achievable band, layer plans, brief
    protection.py    rain/wind/UV rules, S_protect, accessory attachment
    palette.py       colour harmony (Itten zones + the neutral doctrine)
    style.py         formality coherence and tag cohesion
    variety.py       recency decay and the yesterday block
    assemble.py      the hard part: filter → bounded enumeration → MMR → ladder
    explain.py       deterministic classified reasoning lines
  adapters/          provider interfaces, offline default + live implementation
  store/             Repository protocol, SQLite backend, in-memory backend
  services.py        the only layer touching both I/O and the engine
  api/               FastAPI app (thin)
  cli/               Typer CLI (thin)
tests/               pytest suites, test names carry FR ids
evals/               metrics, scorecard runner, pytest-enforced gates, fixtures
```

## Configuration

Config lives in `~/.dresscast/config.toml` (DATA_MODEL.md §5); the database
defaults to `~/.dresscast/dresscast.db` and photos to `~/.dresscast/photos/`.
All keys have defaults and the file may be absent.

## Live adapters (opt-in, never used by tests or evals)

Everything external sits behind a provider interface with a deterministic
offline implementation as the default. Live implementations activate only when
these environment variables are set:

| Variable | Adapter | Meaning |
|---|---|---|
| `DRESSCAST_LIVE_WEATHER=1` | `OpenMeteoWeatherProvider` | Enables live weather. Open-Meteo is free and keyless; a location (lat/lon/timezone) must be configured in `config.toml` with `[weather] provider = "open_meteo"`. Without this flag the provider refuses to fetch. |
| `DRESSCAST_VISION_MODEL` | `VisionAttributeExtractor` | Name of the CLIP-style model used for zero-shot attribute suggestion. Requires the optional `vision` extra; deferred to the next phase (SCOPE.md §Non-goals). |
| `DRESSCAST_VISION_WEIGHTS` | `VisionAttributeExtractor` | Optional pretrained-weights tag passed to the model loader. |

Without a live extractor, `dresscast suggest` exits non-zero and says so;
manual tagging (FR-1) is the product's floor and always works. An offline
`FixtureAttributeExtractor` activates automatically when
`<data-dir>/suggestions.json` exists (a JSON map keyed by photo SHA-256).

No credentials are stored in the repository. Garment photos stay on local disk
and are never transmitted by the offline adapters (SCOPE.md D16).
