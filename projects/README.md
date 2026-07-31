# Projects

A monorepo of independent products, each fully scoped (requirements, data model,
eval plan) before implementation. Engineering rules: [CONVENTIONS.md](CONVENTIONS.md).
The pre-existing Orbit app lives at the repo root (`orbit-backend/`, `orbit-ios/`)
and is not part of this workspace.

| Slug | Product | Original idea |
|------|---------|---------------|
| [formcoach](formcoach/) | AI workout coach | Science-based workouts with exercise media, goal/muscle targeting, and a form-fix feature (pose-keypoint analysis) |
| [flowlist](flowlist/) | Playlist reorganizer | Reorders a playlist so as many songs as possible transition seamlessly into each other |
| [chessmentor](chessmentor/) | Adaptive chess | Chess engine that calibrates CPU difficulty to the player over a few games and coaches on weaknesses |
| [datasweep](datasweep/) | Auto data cleaner | Cleans data files in the background |
| [ethos](ethos/) | Moral perspectives | Ask a moral question, get answers grounded in different religious texts and philosophies, with citations for further reading |
| [voicekin](voicekin/) | Voice for smart home | Consent-gated voice profiles so a smart home device can sound like you or a loved one (generic device adapter) |
| [tickerpress](tickerpress/) | Finance-news tracker | Tracks chosen companies' appearances in finance news and sends article links |
| [newsalpha](newsalpha/) | Trading assistant | Scrapes news, extracts relevant events, and interprets them into stock/crypto decision support |
| [pointsmax](pointsmax/) | Rewards maximizer | Understands your cards/points and desired use case, then guides redemptions to maximize value |
| [grailtrader](grailtrader/) | Clothing investment advisor | Treats second-hand designer clothing like a tradable market: track signals, predict price moves, advise buy/sell/hold |
| [dresscast](dresscast/) | Weather outfit generator | Analyzes the day's weather and assembles outfit recommendations from your photographed wardrobe |
| [almanac](almanac/) | Quote & idea almanac | Save quotes/ideas, resurface a daily quote, and find ways to apply them to daily life |

## Process

Each project went through the same loop before any code was written:

1. **Draft** — SCOPE.md (numbered functional requirements), DATA_MODEL.md, EVALS.md.
2. **Critique** — two independent adversarial reviews: design rigor (product
   completeness, data-model soundness, architecture) and eval validity (do the
   metrics measure the hard part; are the gates non-vacuous and ungameable).
3. **Revise** — critiques resolved or explicitly rejected with reasons, recorded
   in each project's docs/REVIEW.md.
4. **Build** — engine + API + CLI + tests, with eval gates enforced in pytest.
