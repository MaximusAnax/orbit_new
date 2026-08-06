# Projects

A monorepo of twelve independent products. Each was scoped — requirements, data
model, evaluation plan — and reviewed by adversarial critics before any code was
written, then built engine-first and verified against enforced quality gates.

Full detail: [projects/README.md](projects/README.md).
Engineering rules: [projects/CONVENTIONS.md](projects/CONVENTIONS.md).

## Quick start

```bash
cd projects
uv sync --all-packages          # install all twelve
uv run ethos ask "what do I owe my aging parents?"
uv run python verify_all.py     # run every test, gate, linter and CLI
```

Each project is a local CLI with an optional REST API. No accounts, no API keys,
no network on the default path — each keeps its own SQLite database under your
home directory.

## The twelve

| Project | What it does |
|---------|--------------|
| [formcoach](projects/formcoach/) | Science-based training programs; form review from pose keypoints |
| [flowlist](projects/flowlist/) | Reorders a playlist so consecutive tracks transition seamlessly |
| [chessmentor](projects/chessmentor/) | Opponent that calibrates to your level and coaches your weaknesses |
| [datasweep](projects/datasweep/) | Background, non-destructive cleaner for messy tabular files |
| [ethos](projects/ethos/) | Moral questions answered across ten traditions, with real citations |
| [voicekin](projects/voicekin/) | Consent-gated personal voice profiles for smart-home speech |
| [tickerpress](projects/tickerpress/) | Tracks watchlist companies across finance news, deduped and ranked |
| [newsalpha](projects/newsalpha/) | News-driven decision support across equities and crypto |
| [pointsmax](projects/pointsmax/) | Highest-value redemption path for credit-card points |
| [grailtrader](projects/grailtrader/) | Buy/sell/hold guidance for second-hand designer clothing |
| [dresscast](projects/dresscast/) | Weather-aware outfits assembled from your catalogued wardrobe |
| [almanac](projects/almanac/) | Quote bank with spaced resurfacing and application prompts |

All twelve pass verification in a single sweep: **4,518 tests, 242 enforced eval
gates**, lint and CLI clean.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
