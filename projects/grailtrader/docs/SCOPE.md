# GrailTrader — Scope

## One-liner

A single-user advisor that treats designer clothing like a tradable asset
class: it builds per-brand / designer-era / category price indices from
secondhand-marketplace sold listings, ingests typed fashion events (designer
departure or appointment, collab announcement, celebrity co-sign, runway
reception, brand scandal), maps each event to an expected index impact that
decays over time, and issues buy / sell / hold advice per portfolio garment —
with confidence, plain-language rationale, and a leak-free backtest that
measures its own directional skill against naive baselines.

## Problem statement

The secondhand designer market behaves like a thinly traded asset market:
prices for "archive" pieces rise and fall on news. When Phoebe Philo's
departure from Céline was announced (December 2017), resale demand and prices
for Philo-era pieces surged — The RealReal's resale reporting documented the
"Old Céline" effect. Slimane-era Dior Homme and founder-era Helmut Lang carry
durable archive premiums precisely because those eras are closed. After
Virgil Abloh's death (November 2021), StockX resale prices for Off-White and
his Louis Vuitton collabs spiked within days and then decayed over weeks. The
Balenciaga campaign scandal (November 2022) and the adidas–Yeezy split
(October 2022) moved resale demand the other way. The owner wants to trade
this: track a garment portfolio, watch the news, and get told when a piece is
worth buying more of, selling into a spike, or holding.

Doing this honestly has two hard parts:

- **A. A trustworthy price index from hostile data.** Marketplace listings
  are heterogeneous (no two grails are identical), sparse (a stratum may see
  a handful of sales a week or none), polluted (fakes listed far below
  market, mislabeled items far above, aspirational asking prices that never
  sell), and condition-confounded (a "very worn" price is not comparable to
  "new with tags"). A naive average of listing prices produces an index that
  is mostly noise and outliers; every downstream signal inherits that
  garbage. The index layer must recover the true level of each
  (brand, era, category) stratum from this data, measurably — including on
  the sparse strata and inside the event windows, which is where a
  median-only score would hide failure (EVALS M1a tail gates).
- **B. An event-impact advisor whose skill is measured, not asserted.**
  Typed events must map to index adjustments with the right direction,
  rough magnitude, persistence split (how much of the move is permanent
  archive re-rating vs transient hype), and decay — and the buy/sell/hold
  decisions derived from them must beat naive baselines on directional
  accuracy in a backtest that is provably leak-free (advice at week *t* uses
  only data through week *t*; placebo runs with scrambled event dates must
  show no skill). Confidence must be ordered *and* must not overstate
  itself: in the acting range the realized hit rate may not fall materially
  below the confidence number the product printed.

Both get first-class eval gates (EVALS.md), including floor gates on activity
and coverage so that abstaining from the hard cases is not a way to pass
(EVALS M0). Per the workspace rule that finance safeguards are implemented
behavior: every advice rendering carries confidence, its driving events, a
fee/illiquidity note, and a collectibles-not-securities footer, enforced by a
frame check that blocks persistence on failure (FR-9, gates M5a/M5b = 1.0).
This market is unregulated, illiquid, and authenticity-risky; the product says
so on every output.

## Target user

The owner: one person who collects archive/designer pieces (think Helmut
Lang, Raf Simons, Margiela, Rick Owens, Philo-era Céline), follows fashion
news casually, and wants (1) a mark-to-market view of what the closet is
worth, and (2) a nudge when news makes a piece worth acting on. Single local
profile; no accounts, no multi-tenancy, no marketplace transactions — the
tool never buys or sells anything. Weekly cadence: indices and advice are
computed at ISO-week grain; this is a research tool, not a sniping bot.

## User stories & acceptance criteria

**US-1 — My closet, marked to market.** As the user, I register garments
(brand, designer era, category, condition, size, acquisition price and date)
and see each one's current fair value, the method used to compute it, and the
unrealized gain/loss.
*Accept:* `portfolio add/list/show/value` work; valuation follows FR-7's
two-method ladder and always reports `valuation_method ∈ {repeat_sales,
comp_based, unavailable}` with a reason when unavailable — never a silent
guess; unknown brand/era/category values are rejected at entry with the
nearest-match suggestion.

**US-2 — Indices I can inspect.** As the user, I load marketplace listings
and build weekly price indices per (brand, era, category) stratum plus the
pooled (brand, era) and (brand) parents, and I can see for any week the
level, sample size, and what was excluded as an outlier.
*Accept:* `listings load && index build` runs end-to-end on the committed
fixture scenario with zero configuration; every index point stores `n_sales`
and `n_excluded`; sold prices only enter the index (asking prices never do);
rebuilding is deterministic and idempotent; parent indices are chain-linked
(FR-4) so a child stratum becoming eligible never steps the parent; index
fidelity and coverage meet the M0a/M1 gates on the fixture scenarios.

**US-3 — Events in, impacts visible.** As the user, I ingest typed fashion
events from the fixture feeds or add one manually
(`events add --type designer_departure --brand celine --era philo ...`), and
I can see which strata each event touches, its modeled impact path
(permanent + transient components, half-life), and when it retires.
*Accept:* all six event types round-trip through `events add/list/show`;
scope resolution follows the FR-5 table exactly; `events show` prints the
resolved target strata, the prior row applied (with its source note), the
corroborating sources, and the computed retirement age `A_e` (FR-6);
re-ingesting the same feed is a no-op, and the same real-world event arriving
from a second feed bumps `corroboration` without creating a second event or
changing `Σ m_e`.

**US-4 — Advice I can interrogate.** As the user, I run `advise` and get,
per portfolio garment: buy / sell / hold, the chosen horizon, expected move,
confidence, and a rationale naming every driving event and every modifier
applied.
*Accept:* every advice stores machine-readable rationale codes plus rendered
text; the action follows the FR-8 decision rule exactly (thresholds and
formulas are data, not code constants); each of the seven hold reason codes
is reachable and covered by a test; "do nothing" is a first-class answer.
Re-running `advise` after confirming a new event produces a new, superseding
advice row for the same garment-week (FR-8 identity), and `advice list`
shows the current one by default.

**US-5 — Test it against a world it did not write.** As the user, I can run
a backtest over the fixture historical scenarios and see directional hit
rate, the buy-minus-sell realized spread, per-confidence-bucket hit rates,
and placebo runs with scrambled event dates that show no skill.
*Accept:* the backtest replays week by week using only strictly-prior data;
entry is the week after the advice week (asserted invariant); the M0/M2/M3/M4
gates hold on scenario A and the M6 gates hold on the mismatch scenario B;
excluded decisions are counted and reasoned, never silently dropped.
*Honest limit, stated on the scorecard and in EVALS.md:* fixture gates
certify that the pipeline is correct and leak-free under approximately-right
priors, plus that it degrades gracefully when the world disagrees with those
priors (scenario B). They do **not** and hermetically cannot certify
real-world predictive skill; that is only measurable by running `backtest` on
the user's own imported comps and event history.

**US-6 — Safeguards I can't turn off.** As the user, every advice rendering
tells me its confidence, its assumptions (fees, illiquidity, authenticity
risk), and that this is a collectibles market, not investment advice — and it
does not refuse to render just because my own garment label contains a
salesy word.
*Accept:* the frame check (forbidden certainty lexicon, required sections,
verbatim footer) runs before persistence and raises on failure — a
non-compliant advice cannot exist in the store (CHECK constraint); gates
M5a = 1.0 (nothing non-compliant is rendered) **and** M5b = 1.0 (every
must-block adversarial case is blocked and every must-render case renders),
so over-blocking is a failure too.

**US-7 — Go live when I'm ready.** As the user, I can import my own sold
comps from CSV and turn fashion-news RSS into *candidate* events that I
confirm before they affect anything.
*Accept:* `CsvListingsFeed` maps documented columns (sold and ask-only rows;
ask-only rows never enter the index — diagnostics only); `RssNewsFeed`
activates only when `GRAILTRADER_NEWS_FEEDS` is set and queues keyword-matched
candidate events as `pending` for `events review` — a pending event never
influences impacts or advice; live adapters are never imported on the
test/eval path (enforced by test T2).

## Functional requirements

Each FR is independently testable; test names reference FR ids. FR-3/4 are
hard part A; FR-6/8/10 are hard part B.

- **FR-1 Reference data & init.** `grailtrader init` loads and validates the
  committed datasets (`data/brands.json`, `impact_priors.json`,
  `conditions.json`, `advice_templates.json`, `advisor_config.json`):
  brand ids unique; era ids unique within brand, eras non-overlapping in
  time with at most one open era (end = null) per brand; every
  (event_type × attribute combination reachable per the FR-5 table) resolves
  to exactly one prior row per target; every prior has non-empty `rationale`
  and `source_note`; condition multipliers strictly decreasing from `new` to
  `poor`; every advice template contains all required sections and zero
  forbidden-lexicon words; config values within documented ranges, including
  `theta_buy == theta_sell == fee_assumption_pct` (FR-8/FR-9 coherence).
  Validation failure aborts init naming the offending record.
- **FR-2 Listings ingestion & normalization.** The `ListingsFeed` adapter
  yields `RawListing`s (source platform, external id, brand ref, era ref,
  category, platform condition label, listed/sold timestamps, ask price,
  sold price, currency). Ingestion maps platform condition labels to the
  canonical five-grade scale via the committed alias table
  (`conditions.json`; unmapped labels are rejected with the label named),
  resolves brand/era against the gazetteer (unresolvable rows are skipped
  and counted in the ingest report — never guessed), requires USD, and is
  idempotent: listing ids are content-derived, so re-ingesting a feed
  changes nothing. Listings are append-only. `status = sold` requires
  `sold_price` and `sold_at ≥ listed_at`; `status = active` requires
  `ask_price` and never enters the index; all prices > 0.
- **FR-3 Outlier fence.** Within each index window (FR-4), a sold listing is
  excluded iff `|ln(p_adj) − median_w| > max(fence_sigma · σ̂_w,
  fence_floor_log)` where `p_adj` is the condition-adjusted price, `median_w`
  the window median of `ln p_adj`, and `σ̂_w = 1.4826 · MAD` of `ln p_adj` in
  the window. Defaults `fence_sigma = 3.5`, `fence_floor_log = ln 2.2 =
  0.7885`. The MAD term adapts to genuinely volatile strata; the floor keeps
  the fence meaningful in small windows. Exclusions are counted per index
  point (`n_excluded`) and inspectable (`index show --excluded`). This is the
  *price-implausibility* defense, not an authenticity check: it removes
  listings priced far outside the stratum's own distribution (counterfeits
  dumped at 0.15–0.3×, mislabeled grails at 3×+). Fakes priced near market
  (≈0.5–0.6×, i.e. inside the log floor of 0.7885) survive it by
  construction — see non-goal 3 and the ungated ambiguous-band diagnostic in
  EVALS.
- **FR-4 Index construction (hard part A).**
  - *Leaf strata* s = (brand, era, category), ISO week t:
    `level_usd(s, t) = median of condition-adjusted sold prices`
    (`p_adj = sold_price / condition_multiplier[grade]`, i.e. every sale is
    restated as an excellent-condition-equivalent price) over the trailing
    `window_weeks = 4` window (weeks t−3…t), after the FR-3 fence, requiring
    ≥ `min_sales = 5` surviving sales; otherwise no index point is written
    for that week (staleness is computed at query time as the distance to
    the last written point — never fabricated).
    `index(s, t) = 100 · level_usd(s, t) / level_usd(s, t_base)` where
    `t_base` is the stratum's first eligible week.
  - *Parent strata* are **chain-linked**, not averaged levels. For a parent
    p — children of a (brand, era) parent are its leaves; children of a
    (brand) parent are its (brand, era) strata:
    - `t_base(p)` = the first week at which any child has a point;
      `I_p(t_base) = 100`, `level_usd = null`.
    - At week t, let `t_prev` = p's last published week and
      `C(t)` = children with published points at **both** `t_prev` and `t`.
      If `C(t) = ∅`, p publishes no point at t (`t_prev` is unchanged for the
      next attempt).
    - `ln I_p(t) = ln I_p(t_prev) + Σ_{c∈C(t)} w_c(t)·(ln I_c(t) − ln I_c(t_prev))
      / Σ_{c∈C(t)} w_c(t)`, where `w_c(t)` is c's surviving-sale count over
      the trailing `parent_weight_window_weeks = 8` weeks (for a (brand)
      parent, the sum over that era's leaves).
    - `n_sales(p, t) = Σ_{c∈C(t)} n_sales(c, t)`; `n_excluded(p, t) = 0`.
    - A child publishing for the first time at t has no point at `t_prev`, so
      it is excluded from `C(t)` and contributes only from its **second**
      point onward. This is what makes the parent continuous: no rebased
      child level ever enters the parent as a level.
  - Index build is a pure function of (listings, as_of, config) and rebuild
    replaces derived rows idempotently.
- **FR-5 Event ingestion, identity & scope resolution.** The `NewsFeed` and
  `SocialFeed` adapters emit typed `FashionEvent`s (six types, attributes per
  the typology table below). `occurred_on` is caller-supplied (the engine
  never reads the clock). Manual entry (`events add`) is a first-class
  source. Live RSS produces `status = pending` candidates that require
  `events review --confirm` before entering the active set; fixture and
  manual events are `confirmed` on arrival.
  - *Identity (deduplication across feeds).* An event's id is derived from
    `event_type | brand_id | era_id | iso_week(occurred_on) | identity_attrs`
    where `identity_attrs` are the *factual* discriminators only —
    `designer` (appointment), `counterparty` (collab), `celebrity`
    (co-sign) — and never the *judgement* attributes (`reason`, `acclaim`,
    `severity`, `polarity`), on which independent feeds routinely disagree.
    `source_ref` is **not** in the key. The same real-world event arriving
    from a second feed therefore resolves to the same row: its `source_refs`
    list gains an entry, `corroboration` becomes the number of distinct
    source_ref registrable domains seen (manual counts as the domain
    `manual`), and first-seen wins on `occurred_on` and the judgement
    attributes. `Σ m_e` is unchanged by duplicate ingest (asserted by test).
  - *Scope resolution.* Each confirmed event maps to target stratum prefixes
    per the typology table (a departure targets `brand/era`; a scandal
    targets `brand`). An event applies to a garment iff one of its target
    prefixes is a prefix of the garment's **leaf** stratum path.
- **FR-6 Impact model (hard part B).** Each confirmed event, via its prior
  row (`impact_priors.json`, keyed by event type + attributes), defines a
  cumulative log-impact path at age `a` weeks (a = whole weeks since the
  event's week):
  `m_e(a) = ln(1 + P_e + T_e · 0.5^(a / h_e))` for a ≥ 0, where `P_e` is the
  permanent component (signed fraction), `T_e` the transient component
  (signed), and `h_e` the transient half-life in weeks — the
  permanent-plus-decaying-transient form of advertising adstock (Broadbent
  1979) applied to price level instead of awareness. Overlapping events on
  the same stratum combine log-additively. Celebrity co-sign transients
  scale by tier (`a_list` 1.0, `b_list` 0.5, `niche` 0.25) before the path is
  built.
  - *Retirement (conjunctive, bounded).* An event's active age limit is
    ```
    A_e = active_max_weeks                                     if |T_e| ≤ active_transient_min
    A_e = min(active_max_weeks, h_e · log2(|T_e| / active_transient_min))   otherwise
    ```
    and the event is **active at week t iff `0 ≤ age_e(t) ≤ A_e`**. With the
    defaults (`active_max_weeks = 26`, `active_transient_min = 0.005`) every
    prior in the catalog retires at 26 weeks. The rule is deliberately a
    conjunction of "young enough" and "transient still alive": under a
    disjunction, any event with a permanent component would stay active
    forever, `hold:no_active_events` would be unreachable, and the FR-8
    baseline would recede indefinitely into the past, turning ordinary
    stratum drift into standing spurious signal.
  - *Re-anchoring.* Once an event retires, its permanent component is
    considered **absorbed into the observed index**: FR-8 takes its baseline
    `B` from a week after every retired event, and only active events
    contribute to `Σ m_e`. A retired event is therefore never double-counted.
  - Within an active event's contribution, `m_e` is evaluated at the
    *future* age `t + H − t_e` even when that age exceeds `A_e` — by then
    the transient has decayed to noise and only `P_e` remains, which is the
    intended forward path.
  - All prior values are editable data with rationale + source notes; the
    engine never hard-codes an expected move.
- **FR-7 Garment valuation.** Every garment carries an *anchor pair*
  `(anchor_price, anchor_date, anchor_condition)`: for `owned` /
  `sold_archived` garments that is `(acquisition_price, acquired_on,
  condition at entry)`; for `watching` garments it is `(reference_price,
  reference_date, condition at entry)` — the price the user saw and the day
  they saw it. Valuation at week t tries two methods in order and always
  reports which one it used:
  1. **`repeat_sales`** — `fair_value = anchor_price · I_s(t)/I_s(t_anchor) ·
     (condition_multiplier[current] / condition_multiplier[anchor])`, where
     s is the most specific ancestor of the garment's leaf path with
     published points within `stale_max_weeks` of **both** t and `t_anchor`
     (each carried back to the nearest earlier published point). This is
     single-asset repeat-sales logic: the garment's own transaction price is
     the anchor and the stratum index supplies the appreciation path
     (Bailey–Muth–Nourse 1963 / Case–Shiller 1989, applied in reverse to mark
     one asset to market).
  2. **`comp_based`** — if no stratum covers `t_anchor` (the common case for
     a closet acquired before the comp history starts), and the garment's
     **leaf** stratum has a point within `stale_max_weeks` of t, then
     `fair_value = level_usd(leaf, t) · condition_multiplier[current]`,
     labeled *comp-based, not repeat-sales* in every rendering. Parent strata
     carry no `level_usd`, so this method needs the leaf.
  3. Otherwise `valuation_method = unavailable` with a named reason
     (`no_index`, `stale_index`, `no_index_at_anchor`).
  `portfolio value` also reports the leaf stratum's `level_usd` ("typical
  excellent-condition price") for context.
- **FR-8 Advisor (hard part B).** At week t, for each portfolio garment g
  with `status ∈ {owned, watching}`:
  - **Stratum selection.** `s` = the most specific ancestor of g's leaf path
    (`brand/era/category` → `brand/era` → `brand`) with a published index
    point within `stale_max_weeks` of t. No point anywhere on the path →
    `hold:no_index`; points exist but all staler than `stale_max_weeks` →
    `hold:stale_index`. (Unlike FR-7 method 1, advice imposes no requirement
    at the anchor date.)
  - **Event matching and scope dilution.** Active events (FR-6) are matched
    against g's **leaf** path per FR-5, but their impacts are applied to the
    index of `s`. When `s` is a *proper ancestor* of an event's target
    stratum, the event moves only part of `s`, so its contribution is diluted
    by its weight share
    `λ_e = (Σ w_c over children of s inside the event's target subtree) /
    (Σ w_c over all children of s)`, using the same trailing-8-week
    surviving-sale weights as FR-4's parent chaining. `λ_e = 1` when `s` is
    at or below the event's target. `λ_e` is recorded as a rationale code.
  - **Baseline** `B` = the observed index of `s` at the last published week
    strictly before the earliest active event's week, carried back to the
    nearest earlier published point within `stale_max_weeks`. No active
    events → `hold:no_active_events`; no such observation → `hold:no_baseline`.
  - **Expected level at horizon H:** `ln Î(t+H) = ln B + Σ_e λ_e · m_e(t + H − t_e)`.
  - **Expected forward return** `r̂_H = Î(t+H) / O_t − 1`, where `O_t` is the
    current observed index of `s` (its latest published point ≤ t). Because
    sellers reprice slowly (posted-price market friction — resale listings
    sit until edited), `O_t` lags the modeled path right after an event; the
    gap *is* the trade.
  - **Volatility.** Over the trailing 52 weeks, take every consecutive pair
    of published points for `s` at weeks `t_i < t_{i+1}` with gap
    `g_i = t_{i+1} − t_i` and form the per-week change
    `Δ_i = (ln I_s(t_{i+1}) − ln I_s(t_i)) / √g_i` (random-walk scaling, so
    gappy sparse strata are usable). `σ_w = 1.4826 · median|Δ_i|`. Fewer than
    `sigma_min_changes = 12` such pairs → `hold:insufficient_history`. No
    fabricated σ, no silent fallback.
  - **Signal-to-noise per horizon:** `z_H = |ln(1 + r̂_H)| / (σ_w · √H)`.
  - **Horizon selection:** `H* = argmax` over `H ∈ horizons_weeks = {4, 12,
    26}` of `z_H` (ties → smallest H).
  - **Confidence:**
    `conf = clamp(z_term · q_index · c_event, conf_floor, conf_cap)` with
    `z_term = 1 − 0.5^(z_H*/z_half)`, `z_half = 0.5` (chosen so the curve is
    calibrated to the index noise the fixtures actually exhibit — derivation
    in EVALS.md; it is config data and must be re-derived if index density
    or item noise changes);
    `q_index` = 1.0 / 0.8 / 0.5 for staleness of 0 / 1–2 / 3–8 weeks; and
    ```
    c_event = Σ_e w_e · base_conf_e · f_e / Σ_e w_e
    w_e     = λ_e · |m_e(t + H* − t_e)|
    f_e     = min(1.0, s_e · (1 + corroboration_step · min(corroboration_e − 1,
                                                          corroboration_max_steps)))
    ```
    where `s_e` is the source factor (manual 1.0 / news 0.95 / social 0.8).
    The source factor appears **once**: corroboration can lift a news or
    social event's factor back toward 1.0 but never above it.
    Note the ceiling `conf ≤ c_event ≤ max_e base_conf_e` (since
    `z_term < 1` and `q_index ≤ 1`): an event family whose
    `base_conf ≤ conf_min` can never produce actionable advice on its own.
    That is deliberate — celebrity co-signs (0.50), runway reception
    (0.50–0.52) and unproven/neutral appointments (0.46/0.52) are context,
    not trades. They still shape `r̂`, they dilute `c_event` when they
    overlap a stronger driver, and they populate the lower calibration
    buckets (EVALS M3).
  - **Action:** **buy** if `r̂_H* ≥ +theta_buy` and `conf ≥ conf_min`;
    **sell** if `r̂_H* ≤ −theta_sell` and `conf ≥ conf_min`; else **hold**
    with a reason code (`hold:below_threshold` or `hold:low_confidence`).
    `theta_buy = theta_sell = 0.12 = fee_assumption_pct` (D-12): the modeled
    move must at least cover the one-way friction of acting on it, so no
    rendered advice ever quotes an expected move smaller than its own stated
    fee assumption. `conf_min = 0.52`, set just below the weakest *tradable*
    prior family so that the sell-into-decay leg is structurally reachable
    (the decay of a large transient at H* = 26 yields z ≈ 0.8, hence
    conf ≈ 0.53; a higher `conf_min` would make "sell into the spike" — a
    promise in the product's one-liner — unreachable in principle, which
    EVALS M0d would then expose as a zero count). Thresholds live in
    `advisor_config.json`.
  - **Candidate flag.** An advice is a *directional candidate* iff
    `|r̂_H*| ≥ theta` (i.e. it cleared the magnitude test), whether or not it
    cleared `conf_min`. Candidates carry `r̂` and `conf` and are the
    population EVALS M3 calibrates over; actionable advice (buy/sell) is the
    subset that also cleared `conf_min`.
  - **Identity and supersession.** An advice row is keyed on
    `(garment_id, as_of_week, inputs_hash)` where `inputs_hash` is a hash of
    the exact inputs — sorted active confirmed event ids with their weeks and
    corroboration, `stratum_id`, the index's `built_as_of`, and
    `config_version`. Advice rows are immutable and append-only; the row with
    the greatest `created_as_of` for a `(garment, as_of_week)` is the
    **current** one and earlier rows are retained for audit. Re-running
    `advise` with unchanged inputs is a byte-identical no-op; re-running after
    a new event is confirmed writes a new current row (the horizon is an
    *output*, so it can never be part of the key).
  - Every advice stores rationale codes (`driver:event:<id>`,
    `prior:<key>`, `mod:lambda=<λ>@<event id>`, `mod:z=<z>@h<H>`,
    `mod:conf_event=…`, `mod:q_index=…`, `mod:src=…`, `hold:<reason>`), the
    chosen horizon, `r̂`, `conf`, and fair-value context with its method.
- **FR-9 Advice framing safeguard.** Advice text renders only from the
  committed template catalog (`advice_templates.json`) plus event/garment
  fields. The frame check then asserts, **in both directions**:
  - zero forbidden-lexicon matches ("guaranteed", "can't lose", "sure
    thing", "risk-free", "will definitely", "easy money" — full list
    committed) in *generated* text; user-supplied strings (garment `label`,
    garment/event `notes`, listing `title`) are rendered only inside
    typographic quotes and are exempt from the lexicon check inside those
    quotes. A real listing that says "guaranteed authentic" must still
    produce advice — over-blocking is a failure (M5b), not caution;
  - all required sections present (action + expected move + drivers +
    confidence + valuation context with method + fee/illiquidity note +
    uncertainty/falsifier);
  - the collectibles-not-advice footer present verbatim;
  - the fee note renders `fee_assumption_pct` (0.12) and, because
    `theta == fee_assumption_pct`, every actionable advice's expected move is
    ≥ the friction it quotes.
  Failure raises before persistence; the store's CHECK constraint makes a
  non-checked advice row unrepresentable. Gates M5a = M5b = 1.0.
- **FR-10 Backtest harness (hard part B).** Replays a scenario week by week:
  at each week t, the advisor runs with listings `sold_at ≤ end of week t`
  and events `occurred_on ≤ end of week t` only (strictly-prior data —
  asserted). Entry is week t+1 ("you act over the following week"); realized
  return at the advice's own horizon H*:
  `R = I_ref(t + 1 + H*) / I_ref(t + 1) − 1`, where `I_ref` is the planted
  truth index in eval runs and the recovered index in live runs.
  `hit = (sign(R) == sign(r̂))` and is computed for **every directional
  candidate**, so calibration can be scored below the action threshold.
  - *Grading window.* A candidate at week t with horizon H is *in window*
    iff `t + 1 + H ≤ end_week`. Out-of-window candidates are recorded with
    `excluded_reason = out_of_window`, reported separately, and excluded from
    both numerator and denominator of every rate — they are a property of the
    scenario's edge, not a failure, and they do **not** count against the
    `insufficient_future_index` budget.
  - *Aggregates.* N candidates, N actionable, N excluded by reason, hit rate,
    mean realized return per action, buy-minus-sell spread (raw and split by
    H* ∈ {4, 12, 26}), per-confidence-bucket hit rate and mean confidence
    (edges `< 0.45 / 0.45–0.62 / ≥ 0.62`, over **candidates**, not over
    actionable advice — with `conf_min = 0.52` a bucket below it would
    otherwise be empty by construction), per-driving-event-type breakdown,
    and regime counters (sells whose drivers are all bullish
    events = sell-into-decay; buys issued within 3 weeks of a driving event =
    phase-in buys).
  - *Baselines*, computed in the same run over the same weeks and garments:
    always-hold; `theta`-momentum (`buy iff I(t)/I(t−4) − 1 ≥ theta_buy`,
    mirrored for sell); event-naive (buy for 12 weeks on any confirmed event
    touching the stratum).
  - *Placebo mode:* with a seed, every event's week is displaced uniformly
    over ±[26, 52] weeks; a draw is **redrawn while the displaced active
    window overlaps the true active window of *any* event whose target strata
    intersect the displaced event's target strata** (not merely its own), so
    placebo decisions cannot ride real planted moves. Everything else is
    identical. Runs are persisted append-only and never mutate advice.
- **FR-11 Portfolio CRUD.** Add / edit / remove / list garments with status
  `owned`, `watching`, or `sold_archived`. `owned`/`sold_archived` require
  `acquisition_price` + `acquired_on` (and `disposed_price` + `disposed_on`
  when archived); `watching` requires `reference_price` + `reference_date`
  instead and has null acquisition fields. Validation against the gazetteer
  with nearest-match suggestions. Removal is a soft delete (`deleted_at`
  set): the garment leaves all listings and the advise pipeline, and its
  historical advice rows stay readable. Watching garments get the same
  pipeline (buy is actionable; sell renders as "avoid / wait").
- **FR-12 API.** FastAPI app per the endpoint sketch below; thin —
  validation, service calls, serialization only.
- **FR-13 CLI.** Typer app per the command sketch below; same services as
  the API; human tables with `--json` escape hatch on list/show commands.
- **FR-14 Determinism & hermeticity.** Engine functions are pure in
  (listings, events, config, as_of); ids are content-derived so replays are
  byte-identical and ingest is idempotent; money entering an id hash is
  serialized as integer cents (no float formatting ambiguity); time is always
  an input; the only randomness is the placebo seeds. Tests and evals use
  `FixtureListingsFeed` / `FixtureNewsFeed` / `FixtureSocialFeed` and the
  in-memory store exclusively; live adapters are never imported on that path,
  and test T2 enforces it mechanically (sockets patched to raise, then
  `sys.modules` asserted free of `news_rss`).

### Event typology (FR-5/6, the committed taxonomy)

| Type | Attributes | Target strata | Prior shape (defaults; editable data) |
|---|---|---|---|
| `designer_departure` | `era_id` (the era that closes), `reason ∈ {resignation, ousted, death, house_closure}` | `brand/era` | bullish era re-rating: permanent +12–20 %, transient +10–25 %, h = 6–10 wk by reason (death and closure strongest — supply is permanently finite); `base_conf` 0.70–0.78 |
| `designer_appointment` | `designer`, `acclaim ∈ {acclaimed, neutral, unproven}` | `brand` (current heat) **and** `brand/predecessor-era` (era closes) | brand: acclaimed → permanent +6 %, transient +8 %, h = 8, `base_conf` 0.62 (neutral 0.52, unproven 0.46); predecessor era: permanent +6 %, transient +4 %, h = 8, `base_conf` 0.64 |
| `collab_announcement` | `counterparty` (free text; second gazetteer brand also targeted when resolvable), `fizzled?` (fixture truth only) | `brand` | transient +10 %, permanent +2 %, h = 4 — hype-cycle shaped; `base_conf` 0.60 |
| `celebrity_cosign` | `celebrity`, `tier ∈ {a_list, b_list, niche}`, optional `era_id`, `category` | most specific given (`brand`, `brand/era`, or `brand/era/category`) | transient +10 % · tier scale, permanent 0, h = 3; `base_conf` 0.50 — below `conf_min`, so co-signs never trade alone |
| `runway_reception` | `polarity ∈ {acclaimed, panned}` | `brand` | acclaimed: transient +5 %, permanent +2 %, h = 6, `base_conf` 0.52; panned: transient −4 %, permanent 0, h = 6, `base_conf` 0.50 |
| `brand_scandal` | `severity ∈ {minor, moderate, severe}` | `brand` | bearish: permanent −5/−10/−15 %, transient −5/−10/−15 %, h = 6/8/10; `base_conf` 0.58/0.68/0.74 |

Grounding for the default magnitudes lives in each prior row's
`source_note` (see DATA_MODEL.md): the post-Philo Céline surge (The
RealReal resale reporting, 2018), Off-White/StockX after Abloh's death
(Nov 2021), Supreme × Louis Vuitton (2017) and Nike × Off-White "The Ten"
resale premiums, the Bella Hadid–driven vintage Jean Paul Gaultier revival
(Lyst/Depop search-spike reporting), Lyst Index methodology (brand heat from
search + social moments), Balenciaga campaign scandal demand drop
(StockX/Grailed demand data, Nov 2022), and the adidas–Yeezy split
(Oct 2022). These are directional and order-of-magnitude anchors, not
precise estimates — which is exactly why the advisor's behaviour is gated by
backtest (including a scenario whose truth contradicts these priors,
EVALS M6), not assumed from priors.

## Non-goals (this pass)

1. **No web UI** — API + CLI only (workspace-wide decision). Endpoints are
   shaped so a later dashboard is a pure client.
2. **No transactions, ever.** The tool never lists, bids, buys, or sells,
   and never connects to a marketplace account. Permanent boundary.
3. **No authenticity verification.** The FR-3 fence catches
   *price-implausible* listings; it does not legit-check anything, and it
   provably cannot separate a fake priced at 0.5× from a genuine grail
   sniped at 0.5× (EVALS reports that band as a diagnostic, ungated).
   Authentication (Entrupy-style, platform authenticators) is explicitly
   out; the footer names authenticity as a residual risk.
4. **No era inference from garments.** Dating a piece by care tags and
   labels ("tag archaeology", e.g. the well-documented Helmut Lang tag
   generations) is real collector practice but out of scope: era is input
   data supplied by the user or the listing source (D-19).
5. **No NLP event extraction.** Adapters emit *typed* events (locked
   decision). The live RSS adapter does keyword-rule candidate matching
   only, and candidates require human confirmation. An LLM extractor could
   later slot in behind the same `FashionEvent` schema; it would face the
   same eval gates.
6. **No live social-media scraping.** The `SocialFeed` live path is manual
   entry (`events add --source social`); platform APIs (X, Instagram,
   TikTok) are gated, unstable, and against-ToS to scrape. Fixture social
   events model what a future integration would emit.
7. **No live marketplace API at all this pass.** No public API exposes
   secondhand *sold* prices reliably (eBay Marketplace Insights is
   restricted; Grailed, Vestiaire, and StockX have no public APIs), and an
   ask-only eBay Browse adapter would add OAuth plumbing for data that by
   design can never enter the index. It is cut (D-18 valve 1 exercised to
   pay for the fixes in this revision). Live comps arrive via `CsvListingsFeed`
   from a user export — sold rows feed the index, ask-only rows feed the
   ask-over-sold spread diagnostic.
8. **USD only.** No currency conversion; non-USD listings are rejected at
   ingest with a count.
9. **No size-level indices.** Size is stored on garments and listings
   (it matters for liquidity) but is not a stratum dimension — strata would
   become too sparse to index (D-1).
10. **No portfolio-level analytics** (correlation, exposure,
    diversification). Advice is per garment.
11. **No scheduling/daemon mode, no notifications.** The user runs
    `advise` when they want it.
12. **No claim of real-world predictive skill from the fixture gates.**
    The eval suite certifies pipeline correctness, leak-freedom, framing,
    and graceful degradation under prior/world mismatch. Measuring genuine
    skill requires the user's own history through the same `backtest`
    command (US-5).

## Architecture

```
projects/grailtrader/
  src/grailtrader/
    models.py            # Pydantic v2 domain models + enums (DATA_MODEL.md)
    engine/
      conditions.py      # FR-2: condition alias mapping + multipliers
      fence.py           # FR-3: MAD outlier fence
      index.py           # FR-4: strata, windowed medians, parent chain-linking
      events.py          # FR-5: identity/dedup, scope resolution, retirement
      impact.py          # FR-6: prior lookup, m_e(a) paths, log-additive combination
      valuation.py       # FR-7: repeat-sales + comp-based ladder
      advisor.py         # FR-8: stratum pick, λ dilution, r̂, z, confidence, action
      frame.py           # FR-9: template render + two-directional frame check
      backtest.py        # FR-10: weekly replay, baselines, placebo, aggregates
    adapters/
      listingsfeed.py    # ListingsFeed Protocol
      listings_fixture.py    #   offline: FixtureListingsFeed (JSONL path)
      listings_csv.py        #   live: CsvListingsFeed (documented column map, no network)
      newsfeed.py        # NewsFeed Protocol
      news_fixture.py        #   offline: FixtureNewsFeed (JSONL of typed events)
      news_rss.py            #   live: RssNewsFeed (candidate events; extra "live")
      socialfeed.py      # SocialFeed Protocol
      social_fixture.py      #   offline: FixtureSocialFeed (JSONL of typed events)
      social_manual.py       #   live: ManualSocialEntry (thin; used by `events add`)
    store/               # Repository protocol; SQLiteRepository (stdlib sqlite3) + InMemoryRepository
    api/                 # FastAPI app
    cli/                 # Typer app
  data/                  # brands.json, impact_priors.json, conditions.json,
                         # advice_templates.json, advisor_config.json
  evals/                 # fixtures/ (scenario_a, scenario_b), metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default; tests/evals) | Live (env-gated; extra `live`) |
|---|---|---|
| `ListingsFeed.fetch() -> list[RawListing]` | `FixtureListingsFeed(path)` — committed JSONL, deterministic order (sold_at/listed_at, external_id) | `CsvListingsFeed(path)` — user-exported sold + ask-only comps, documented column map, no network |
| `NewsFeed.fetch(since, until) -> list[FashionEvent]` | `FixtureNewsFeed(path)` — committed JSONL of typed, `confirmed` events | `RssNewsFeed` — feedparser over `GRAILTRADER_NEWS_FEEDS` (e.g. BoF, Vogue Business, Hypebeast, WWD feeds); committed keyword-rule table types candidates; emits `status = pending` events requiring `events review --confirm` |
| `SocialFeed.fetch(since, until) -> list[FashionEvent]` | `FixtureSocialFeed(path)` — committed JSONL (co-signs, runway reception with `source = social`) | `ManualSocialEntry` — programmatic seam used by `events add --source social`; no scraping (non-goal 6) |

The store is the fourth seam: `Repository` protocol with `SQLiteRepository`
(default `~/.grailtrader/grailtrader.db`, path via `GRAILTRADER_DB`) and
`InMemoryRepository` for tests.

### API sketch (FastAPI)

```
GET  /health
POST /listings/load            # {source: fixture|csv, path} → {ingested, skipped_unresolved, skipped_currency, duplicates}
GET  /listings?stratum=&status=&limit=          GET /listings/{id}
POST /index/build              # {as_of} → {strata_built, points_written, excluded_total}
GET  /index/{stratum_id}?from=&to=&excluded=    # weekly points incl. n_sales/n_excluded
GET  /strata?level=leaf|era|brand&brand=
POST /events/ingest            # {source: fixture|rss, path?, since?, until?} → counts by status + corroboration bumps
POST /events                   # manual typed event (validated against typology)
POST /events/{id}/confirm      POST /events/{id}/reject
GET  /events?type=&brand=&status=&since=        GET /events/{id}   # incl. resolved scopes, prior, A_e, source_refs
GET  /brands                   GET /brands/{id}                    # incl. eras
POST /portfolio                GET /portfolio    GET /portfolio/{id}
PATCH /portfolio/{id}          DELETE /portfolio/{id}              # soft delete
GET  /portfolio/{id}/valuation?as_of=                              # incl. valuation_method
POST /advise                   # {as_of?} → advice batch for all active garments
GET  /advice?garment=&action=&as_of=&history=false                 GET /advice/{id}
POST /backtests                # {start, end, placebo_seed?} → run + aggregates + baselines
GET  /backtests/{run_id}       GET /backtests?limit=
```

### CLI sketch (Typer)

```
grailtrader init [--reset]
grailtrader listings load [--source fixture|csv] [--path F]
grailtrader listings list [--stratum --status] | show <id>
grailtrader index build [--as-of D]
grailtrader index show <stratum-id> [--weeks N] [--excluded] [--json]
grailtrader events ingest [--source fixture|rss] [--path F] [--since --until]
grailtrader events add --type T --brand B [--era E --reason R --tier ... --occurred-on D --source manual|social]
grailtrader events review [--confirm <id> | --reject <id>] | list [--type --brand --status] | show <id>
grailtrader brands list | show <brand-id>
grailtrader portfolio add|edit|remove|list | show <garment-id>
grailtrader portfolio value [--as-of D]
grailtrader advise [--as-of D] ; grailtrader advice list [--action --garment --history] | show <id>
grailtrader backtest run [--start --end] | placebo --seed S | show <run-id>
```

## Key design decisions & assumptions

1. **Stratified, condition-adjusted median index — not repeat sales, not
   full hedonics.** Repeat-sales indices (Bailey, Muth & Nourse 1963;
   Case & Shiller 1989; the Mei Moses art index) need the same item to sell
   twice, which archive garments rarely do; a full hedonic regression
   (Rosen 1974) needs dense attribute data we don't have. The workable
   middle — used by national statistical offices as the "mix-adjusted
   median" family of house price indices — is stratification by the
   attributes that drive price (brand, designer era, category) plus a fixed
   quality adjustment (condition), with a robust central estimate per cell.
   Size is excluded from strata to keep cells dense (non-goal 9).
2. **Condition adjustment via committed multipliers.** A fixed-coefficient
   hedonic adjustment: each sale is restated as excellent-condition-
   equivalent before aggregation. The five-grade scale and alias table
   mirror real platform taxonomies (Grailed: New/Gently Used/Used/Very
   Worn; Vestiaire: Never worn with tag → Fair condition), so live imports
   map cleanly. Multipliers are editable data; wrong multipliers show up as
   M1 index error — and scenario B deliberately perturbs the *world's*
   multipliers away from the committed ones to price that failure mode.
3. **Sold prices only; asks are diagnostics.** Asking prices in posted-price
   resale markets are aspirational (listings sit unsold for months);
   treating them as prices inflates any index. Solds enter the index; asks
   feed an ask-over-sold spread diagnostic only.
4. **Robust fence, floored — and honest about its ceiling.**
   `max(3.5σ̂, ln 2.2)` on log deviations: the MAD term (Hampel-style robust
   outlier rule) adapts to stratum volatility; the floor stops tiny windows
   from rejecting everything or nothing. At the fixtures' clean log-noise
   σ = 0.20 the floor binds (3.5 × 0.20 = 0.70 < 0.7885), so the effective
   cutoff is 0.7885: counterfeits dumped at 0.15–0.30× (|ln| = 1.20–1.90)
   and mislabels at ≥ 3× (|ln| ≥ 1.10) are excluded, while clean sales are
   excluded at a rate of ≈ 1 in 10,000 (3.94σ). A fake priced at 0.5×
   (|ln| = 0.69) is *inside* the fence and stays there; no price-only rule
   can do better (non-goal 3). Gated by M1b as a mechanism gate, with the
   ambiguous 0.40–0.60× band reported ungated.
5. **Designer era is a first-class dimension.** Archive collecting prices
   the era, not just the brand — founder-era Helmut Lang, Slimane-era Dior
   Homme, Philo-era Céline ("Old Céline"), Miyashita-era Number (N)ine.
   Eras are committed gazetteer data with real tenure dates; a departure
   *closing* an era is precisely what makes its supply finite and its
   premium durable, which is why departure priors carry a permanent
   component.
6. **Typed events in, no extraction.** Locked with the owner. This keeps
   the hermetic budget on the two genuinely hard parts. The live-RSS keyword
   matcher only nominates candidates; a pending event has zero effect until
   confirmed — a human is the extraction QA (FR-5).
7. **Impact = permanent + decaying transient, with bounded retirement.** The
   adstock model (Broadbent 1979) gives the transient the right shape; the
   permanent term captures archive re-rating that does not decay. Event-study
   practice (MacKinlay 1997) motivates measuring impacts as abnormal moves
   from a pre-event baseline. Retirement is conjunctive and re-anchoring is
   explicit (FR-6) so the permanent term is counted exactly once: while the
   event is active it is modeled; afterwards it lives in the observed index
   and the baseline moves past it.
8. **Priors are case-grounded, editable data — and mistrusted.** Every
   prior row carries `rationale` + `source_note` naming its real-world
   anchor. Resale-market event studies are anecdotal compared to equity
   literature, so the design treats priors as hypotheses: scenario B plants
   three events whose true direction contradicts their prior and gates that
   the advisor does not stamp them high-confidence, and a user can retune
   priors and re-run `backtest` on their own data.
9. **The advisor trades the gap between modeled path and observed index.**
   Secondhand posted-price markets reprice slowly — sellers must manually
   edit listings, so news diffuses over weeks, not minutes (the fixture
   scenario plants a 2-week phase-in). Right after an event the observed
   index lags the modeled path → buy window; after the transient peaks,
   decay makes the modeled forward path fall below the observed level →
   sell-into-the-spike window. One formula captures both (FR-8), and the
   backtest counts each regime separately (M0d) so a system that only ever
   sells on scandals cannot claim the sell-into-decay behaviour.
10. **Multi-horizon argmax-z action selection.** A transient co-sign is a
    4-week trade; a departure re-rating is a 12–26-week trade. Evaluating
    r̂ at {4, 12, 26} weeks and acting on the highest signal-to-noise
    horizon lets one rule serve both; the backtest scores each advice at its
    own horizon and reports the spread per horizon so a long-horizon bias
    is visible (EVALS M2b).
11. **Confidence is a reliability score that may not overstate itself.**
    The z-based core (noise from the stratum's own MAD-estimated weekly
    volatility) is damped by index quality (staleness) and event reliability
    (prior base confidence × source factor with corroboration). In the
    **acting range** (`conf ≥ conf_min`) it is intended to approximate
    P(direction correct) and is gated as such: M3b requires
    `hit_rate(bucket) ≥ mean(conf in bucket) − 0.10`, and M3c requires the
    high bucket to hit ≥ 0.80. Below the acting range the product form is
    deliberately conservative (three multiplied damping factors), so only
    its *ordering* is gated (M3a separation) — a two-sided calibration bound
    there would fail a correct implementation for being too humble. The cap
    at 0.95 is a safeguard: the system may never claim certainty.
12. **Action thresholds equal the stated friction.** `theta = 0.12 =
    fee_assumption_pct`: one-way seller cost on real platforms is ≈ 12 %
    (Grailed ~9 % + ~3 % payment processing; StockX ~9–12 % + ~3 %;
    Vestiaire ~15 %; The RealReal consignment higher on low-value items).
    Setting the action threshold equal to the fee assumption means every
    rendered buy/sell quotes a modeled move at least as large as the
    friction it also quotes — the previous draft's 10 % threshold against a
    20 % fee note contradicted itself on the page. Both numbers are config
    data; changing one without the other fails `init` validation (FR-1).
13. **Leak-freedom by construction, proven by placebo and canaries.**
    Strictly-prior data at each replay week and entry at week t+1 (both
    asserted in code); three seeded placebo runs (event dates displaced
    ±26–52 weeks, redrawn away from *any* overlapping true impact window)
    that must show no skill (M4, gated on the worst seed); plus targeted
    pytest canaries for the leaks a placebo is structurally weak against
    (entry-timing lookahead, future-listing peeking). Brown & Warner-style
    simulation honesty applied to this domain. The placebo is a CLI feature,
    not just an eval, so the owner can re-verify on future data.
14. **Fixture brands are fictional; the runtime gazetteer is real.**
    Fixtures include scandal and death events, which must not be attached
    to real brands or people; fictional brands modeled on documented
    archetypes keep realism without defamation risk. The committed runtime
    gazetteer contains real brands and real tenure dates (factual reference
    data) for actual use.
15. **Not-financial-advice is enforced behavior** (workspace rule): the
    frame check runs pre-persist and the store cannot represent an
    unchecked advice row. The footer states the three honest caveats of
    this asset class — unregulated market, weeks-to-months illiquidity,
    authenticity risk — alongside the confidence number. Because the check
    is two-directional (M5a and M5b), "block everything" is not a way to
    pass it.
16. **Weekly grain.** Resale sold-comp density does not support daily
    indices even for hot strata; ISO weeks with a 4-week trailing window is
    the coarsest grain that still resolves hype half-lives of 3–10 weeks.
17. **Assumption: single user, single process.** SQLite, no concurrent
    writers, API binds localhost, no auth.
18. **Assumption: implementation lands in ~3,000–3,800 lines** across
    engine/adapters/store/api/cli (evals/fixtures generator excluded from
    that count, ~500 more). Valve 1 (drop the eBay adapter) is already spent
    on this revision's fixes. Remaining scope valves, in order: drop the
    `watching` status and its reference-anchor fields; reduce horizons to
    {4, 12}; shrink the runtime gazetteer; drop the `comp_based` valuation
    method (leaving `unavailable`). The index/impact/advisor/backtest core
    is not a valve — it is the product.
19. **Assumption: era attribution is input data.** Listings and garments
    arrive with brand/era supplied (fixtures by construction, CSV by the
    user). Unresolvable rows are skipped and counted, never guessed (FR-2);
    inference is future work (non-goal 4).
20. **Assumption: events are point-in-time, deduplicated on facts.**
    Multi-week sagas (a scandal that escalates) are modeled as multiple
    events; the log-additive combination handles overlap. Corroboration is a
    stored integer = the number of distinct source domains that reported the
    same fact-identical event (FR-5), not a clustering system — which is why
    judgement attributes are kept out of the id and duplicate reporting
    raises confidence instead of doubling the modeled move.
