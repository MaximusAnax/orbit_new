# dresscast

Reads the day's hourly weather and assembles complete, ranked outfits from your
own photographed-and-tagged wardrobe — layered so a cold morning and a warm
afternoon are both comfortable, rain-proofed when it matters, occasion-
appropriate, color-coherent, laundry-aware, and never yesterday's outfit — with
the reasoning spelled out hour by hour. When the closet cannot meet the day, it
says exactly what the day demands and what is missing.

The specification is frozen in `docs/`: [SCOPE.md](docs/SCOPE.md) (numbered
FRs), [DATA_MODEL.md](docs/DATA_MODEL.md), [EVALS.md](docs/EVALS.md) and
[REVIEW.md](docs/REVIEW.md).

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
```

## Running things

```bash
cd projects
uv sync --all-packages
uv run pytest dresscast/ -q          # engine tests
uv run ruff check dresscast/         # lint
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
| `DRESSCAST_LIVE_WEATHER=1` | `OpenMeteoWeatherProvider` | Enables live weather. Open-Meteo is free and keyless; a location (lat/lon/timezone) must be configured in `config.toml`. Without this flag the provider refuses to fetch. |
| `DRESSCAST_VISION_MODEL` | `VisionAttributeExtractor` | Name of the CLIP-style model used for zero-shot attribute suggestion. Requires the optional `vision` extra; deferred to the next phase (SCOPE.md §Non-goals). |
| `DRESSCAST_VISION_WEIGHTS` | `VisionAttributeExtractor` | Optional pretrained-weights tag passed to the model loader. |

No credentials are stored in the repository. Garment photos stay on local disk
and are never transmitted by the offline adapters (SCOPE.md D16).
