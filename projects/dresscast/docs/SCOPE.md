# dresscast — SCOPE

> Revision 2 (post-review). Changes from revision 1 are audited line-by-line in
> `REVIEW.md`. The most consequential: comfort is now scored against an
> **achievable** insulation target (§FR-6/D17), `S_thermal` and `S_protect` have
> explicit formulas (§FR-6/FR-9), accessories are **selected** rather than merely
> advised (§FR-9/D18), reasoning is a first-class FR (§FR-15), a wardrobe-free
> day brief delivers the first clause of the owner's idea (§FR-16), and
> wardrobe import/export was cut to Non-goals to stay inside the line budget.

## One-liner

Reads the day's hourly weather and assembles complete, ranked outfits from your
own photographed-and-tagged wardrobe — layered so a cold morning and a warm
afternoon are both comfortable, rain-proofed when it matters, occasion-
appropriate, color-coherent, laundry-aware, and never yesterday's outfit —
with the reasoning spelled out hour by hour. When the closet cannot meet the
day, it says exactly what the day demands and what is missing.

## Problem statement

"What do I wear today?" is a small daily optimization problem people solve
badly. The failure modes are specific: dressing for the temperature *right
now* instead of the day's range (freezing at 07:30, sweating at 15:00);
grabbing the warm coat on a warm-rain day when what's needed is a light
waterproof shell; discovering at the door that the intended trousers are in
the hamper; wearing the same two outfits on repeat while most of the closet
goes unworn; and pairing items that clash in color or formality.

dresscast treats the day as what it actually is: an hourly sequence of
thermal and precipitation conditions, and the wardrobe as a constrained
inventory. It answers two questions, in this order:

1. **What does today demand?** — a wardrobe-independent hourly profile of
   required insulation, rain-cover class, and advisories (FR-16). This works
   on day one with an empty closet.
2. **What, from this closet, meets it?** — **the hard part**: *outfit assembly
   under constraints*. Pick garments for each layer slot such that the
   ensemble, **with layers added or shed over the day**, tracks the
   hour-by-hour required insulation (heat-balance model in clo units, on
   feels-like temperatures), satisfies precipitation/wind protection rules,
   fits the occasion, coheres in color and formality, uses only clean
   garments, and differs from what was worn recently (FR-8). It returns the
   top-k complete outfits, each with a smoothed hourly layer plan ("wear all
   three layers until 11:00, carry the coat after that") and reasoning.

Everything external sits behind offline-first adapters: hourly weather comes
from a deterministic fixture provider by default (live Open-Meteo later), and
garment attributes come from manual tagging (an optional vision extractor can
pre-fill suggestions later). The engine is pure and deterministic.

## Target user

The repo owner: a single user running the tool locally. One wardrobe, one
home location, no accounts, no multi-tenancy, no web UI. API and CLI only.
Garment photos are ordinary image files on disk; the phone-camera capture
flow is out of scope (see Non-goals).

## User stories

- **US-1: Catalog my closet fast.** As the owner, I can add a garment in
  under a minute with a photo and manual tags, with sensible defaults doing
  most of the work.
  *Acceptance:* `dresscast add --photo coat.jpg` prompts for name, category,
  and colors; category selection auto-fills clo (warmth), layer role,
  formality, and wears-before-laundry from the preset table (§Design-D1,
  D11), each overridable; the garment then appears in `dresscast ls`.
- **US-2: Photo → suggested tags.** As the owner, I can ask the attribute
  extractor to propose attributes from a garment's photo and accept or reject
  the suggestion; manual tagging always works without it.
  *Acceptance:* `dresscast suggest <garment>` stores an AttributeSuggestion
  and prints proposed fields with confidences; `--accept` merges only the
  proposed fields I confirm, applying the category cascade of FR-2 and
  reporting every field it rewrote; with no extractor configured the command
  says so and exits non-zero without touching the garment.
- **US-3: What do I wear today?** As the owner, one command gives me ranked
  complete outfits for today with reasoning.
  *Acceptance:* `dresscast outfit --date 2026-07-31 --occasion work` prints
  k=3 complete outfits (every required slot filled), each with a total score,
  component breakdown, an hourly layer plan, and classified reasoning lines;
  re-running the identical command against the identical stored wardrobe,
  forecast snapshot and history reproduces byte-identical output (FR-19).
- **US-4: Cold morning, warm afternoon.** As the owner, on a large-diurnal-
  range day I get a layered outfit plus a plan telling me when to shed
  layers, instead of one compromise ensemble — and the plan is wearable, not
  a coat that goes on and off six times.
  *Acceptance:* on a fixture day spanning 6→18 °C, the top outfit contains a
  removable mid and/or outer layer; its hourly plan shows ≥ 2 distinct
  configurations and ≤ 3 configuration changes, compressed into contiguous
  segments; every wear-window hour's ensemble clo is within the acceptable
  band of that hour's **achievable target** (§Design-D5/D17), and the
  reasoning names each change with a clock time and says what is carried.
- **US-5: Rain handled, not overdressed.** As the owner, on rain days the
  outfit includes adequate rain cover matched to intensity, including the
  warm-rain trap (waterproof shell, *not* the warm coat).
  *Acceptance:* on a fixture day at 22→30 °C with a 14:00–17:00 downpour,
  the top outfit's plan keeps a waterproofness ≥ 2 layer on during the rain
  hours, and its insulation during those hours sits at the **minimum
  achievable given that mandatory cover** (§Design-D17) — it does not add a
  single clo more than the rain forces; the reasoning names the rain window
  and the covering garment.
- **US-6: Dress for the occasion.** As the owner, I can ask for `work`,
  `casual`, `sport`, `outdoor`, or `formal` and get outfits whose items all
  fit the occasion and sit within one formality step of each other.
  *Acceptance:* `dresscast outfit --occasion work` never includes a garment
  that does not list `work` in its occasions; max−min formality across core
  items ≤ 1; sneakers tagged only `casual,sport` never appear.
- **US-7: Laundry-aware.** As the owner, dirty garments are never suggested,
  wearing something advances it toward the hamper, and laundry day resets it.
  *Acceptance:* after `dresscast wear` logs a shirt for its 2nd wear (its
  `wears_before_laundry`=2), the shirt's status is `dirty` and it is absent
  from the next recommendation; `dresscast laundry --all` returns all dirty
  items to `clean` with `wears_since_wash`=0.
- **US-8: Fresh outfits, whole closet.** As the owner, I never get
  yesterday's exact outfit again today, recently worn items are deprioritized,
  and over weeks my whole eligible wardrobe gets used.
  *Acceptance:* with yesterday's wear logged, today's recommendations never
  contain an identical core-item set; in the committed 14-day rollout evals,
  no core-item set repeats within 3 days and ≥ 60% of eligible garments are
  worn at least once (EVALS.md M6).
- **US-9: Tell me what the day needs, even before the closet is catalogued.**
  As the owner, I can ask what today demands without any wardrobe at all, and
  when my closet cannot meet the day I am told precisely what is missing.
  *Acceptance:* `dresscast brief --date D` prints the hourly required-clo
  profile, the layer archetype bracketing it, the rain-cover class per hour,
  and the advisories, on an empty database; a recommendation that ends in
  `infeasible_wardrobe` carries the same brief payload plus the named missing
  capability.

## Functional requirements

Each FR is independently testable; test/eval names reference FR ids (mapping
table in EVALS.md §7).

- **FR-1 Garment creation and validation (manual tagging).** The system
  creates/updates garment records with the full attribute set (DATA_MODEL.md
  §2.1): category, layer role, clo, waterproofness 0–3, windproofness 0–2,
  formality 1–5, colors, style tags, occasions, wears-before-laundry, photo
  reference. Validation: clo within the category's allowed range
  (§Design-D1 table ±0.15, absolute bounds [0.0, 1.5]); occasions non-empty
  for non-accessory garments; colors well-formed per DATA_MODEL.md §2.1;
  names unique case-insensitively. Choosing a category with a warmth level
  0–5 instead of an explicit clo maps to the preset table deterministically.
  Any field the user sets explicitly (rather than accepting the preset
  default) is recorded in the garment's `overridden_fields` set; this is what
  FR-2's cascade honours.
- **FR-2 Photo attach, attribute suggestion, and the accept cascade.** A
  photo file can be attached to a garment (copied under the data directory,
  SHA-256 recorded). `suggest` invokes the `AttributeExtractor` adapter,
  persisting an append-only AttributeSuggestion (proposed fields + per-field
  confidence, source, status `pending`). Suggestions never mutate a garment
  without explicit acceptance. Manual tagging (FR-1) is fully functional with
  no extractor configured.
  **Accept semantics (exact).** The accept request names the payload keys to
  merge. Merging is a three-step transaction:
  1. Merge each named key's value onto the garment.
  2. **Category cascade.** If `category` is among the merged keys, re-derive
     `clo`, `layer_role`, `formality` and `wears_before_laundry` from the new
     category's D1/D11 preset, *skipping any field present in the garment's
     `overridden_fields`* (an explicitly user-set clo is never silently
     rewritten by a category suggestion).
  3. Re-validate the whole record against FR-1. If validation fails — most
     commonly because a user-overridden `clo` now sits outside ±0.15 of the
     new category's preset — the transaction rolls back, the suggestion stays
     `pending`, and the API/CLI returns `invalid_params` naming the offending
     field and both values. The user's remedy is to accept `category` and
     `clo` together, or to edit `clo` first.
  On success, `accepted_fields` lists **both** the explicitly accepted keys
  and every field the cascade re-derived, tagged with which, so DATA_MODEL.md
  §2.2's provenance claim holds for derived values too.
- **FR-3 Laundry lifecycle.** Garment status ∈ {clean, dirty, in_laundry,
  retired} with enforced transitions (DATA_MODEL.md §2.1). Logging a wear
  increments `wears_since_wash` for each worn garment (transactionally with
  the WearLog) and flips status to `dirty` when `wears_since_wash ≥
  wears_before_laundry`. A laundry event resets listed (or `--all` dirty)
  garments to clean/0. Retired garments are excluded from every
  recommendation and report. Manual `mark dirty` is allowed at any time.
- **FR-4 Forecast acquisition and snapshot.** A `WeatherProvider` adapter
  returns a local-day forecast for (location, date): per hour `temp_c`,
  `wind_kmh`, `humidity_pct`, `precip_prob` (0–1), `precip_mmh`, `uv_index`.
  Fetches are persisted as append-only ForecastSnapshots with provider
  provenance; recommendations reference a specific snapshot. The fixture
  provider is deterministic (committed JSON keyed by date); the live
  Open-Meteo provider maps documented API fields (§Architecture-Adapters)
  and activates only when explicitly enabled with a configured location.
  Validation: **23–25 hourly rows** (a normal day has 24; a DST transition in
  the configured timezone produces 23 or 25 and is accepted), rows ordered by
  a contiguous `seq` covering the whole local day with no gaps, fields within
  physical ranges. A repeated wall-clock `hour` (the autumn DST fold) is
  legal; `seq` is the ordering key everywhere in the engine.
- **FR-5 Feels-like computation.** The engine computes per-hour feels-like
  deterministically and **continuously** (no step discontinuities, §Design-D4):

  ```
  wind_delta(T, v)     = WCT(T, v) − T        if v > 4.8 km/h else 0
      WCT = 13.12 + 0.6215·T − 11.37·v^0.16 + 0.3965·T·v^0.16      (JAG/TI 2001)
  heat_delta(T, rh, v) = AT(T, rh, v) − T
      AT  = T + 0.33·e − 0.70·ws − 4.00,  e = (rh/100)·6.105·exp(17.27·T/(237.7+T)) hPa,
            ws = v/3.6 m/s                                          (Steadman 1984)
  ramp_cold(T) = 1.0 if T ≤ 10;  0.0 if T ≥ 14;  else (14 − T)/4
  ramp_heat(T) = 0.0 if T ≤ 24;  1.0 if T ≥ 26;  else (T − 24)/2
  feels(T, v, rh) = T + ramp_cold(T)·wind_delta(T, v) + ramp_heat(T)·heat_delta(T, rh, v)
  ```

  The two ramps have disjoint support, so exactly one (or neither) is active.
  Inside each formula's published validity range the result is unchanged, so
  chart conformance is preserved (EVALS.md M1); outside it the delta is
  faded rather than switched. `feels` is Lipschitz-3 in T (EVALS.md M1
  continuity family).
  A worn windproof layer attenuates the wind input: effective wind =
  `v × {1.0, 0.6, 0.3}` for outermost-worn-layer windproofness {0, 1, 2}.
  Two named quantities follow, and the docs never conflate them:
  - **`bare_feels_c(h)`** — `feels` at windproofness 0 (unattenuated wind).
    Configuration-independent. This is the value used by every rule that must
    be evaluated *before* an outfit exists: HC-7's leg-base gate, FR-9's
    cold-extremity and UV advisories, FR-16's day brief, and D12's slot
    targets.
  - **`feels_c(h, config)`** — `feels` at that configuration's effective
    wind. Used only by FR-6/FR-7's per-hour configuration selection and
    scoring.
  Both are stored per hour in the plan (DATA_MODEL.md §2.5).
- **FR-6 Thermal requirement, achievable target, and thermal score.**
  1. **Required insulation** (physical demand, wardrobe-independent):
     `required_clo(T_feels, met) = clamp((34 − T_feels)/(7.66 × met) − 0.7,
     0.0, 4.5)`, constants per §Design-D3; `met` is a request parameter
     (default 1.6).
  2. **Achievable band** (§Design-D17). For the request's filtered candidate
     lists the engine computes, exactly and in O(n):
     ```
     for f in 1..5:  A_f = core candidates with formality ∈ {f, f+1}     # satisfies HC-4
     ceiling      = max over f of Icl(warmest slot-complete pick from A_f)
     floor(h)     = min over f of Icl(coolest slot-complete pick from A_f,
                                      plus the cheapest adequate *worn* cover in A_f
                                      when hour h mandates worn cover per FR-9)
     ```
     ("Slot-complete" = one base + one bottom, or one `full_body`, plus one
     footwear; mids/outer/leg_base included for the ceiling only, subject to
     HC-7 for leg_base.) These bounds are exact with respect to HC-1…HC-7;
     HC-8 can exclude at most one specific core set, a difference below the
     model's resolution and documented as accepted.
  3. **Target and score.**
     ```
     target_clo(h, config) = clamp(required_clo(feels_c(h, config), met),
                                   floor(h), ceiling)
     dev_h  = Icl(config_h) − target_clo(h, config_h)
     s_h    = max(0, 1 − max(0, |dev_h| − 0.25) / 0.75)
     in_band_h = |dev_h| ≤ 0.25
     ```
     When the clamp is active the hour is scored on *best available* and the
     plan entry carries the note `wardrobe_floor` or `wardrobe_ceiling` with
     the signed shortfall `required_clo − target_clo`; FR-15 emits a
     `wardrobe_limit` line ("nothing in your closet reaches 07:00's 2.56 clo
     — you are 0.52 clo short"). This is what makes a −6 °C morning and a
     33 °C afternoon scoreable at all: absolute in-band comfort is
     unreachable on both tails of the fixture wardrobe (EVALS.md §5.2), so
     the objective grades *how close to the best the closet allows*, and the
     shortfall is surfaced rather than hidden.
  4. **Day aggregation (this is `S_thermal`).**
     ```
     w_h        = EXPOSURE_COMMUTE (3.0) if h ∈ commute_hours else 1.0
     S_thermal  = 0.75 · (Σ_h w_h·s_h / Σ_h w_h) + 0.25 · min_h s_h
     ```
     over wear-window hours only. `commute_hours` defaults to
     `{7, 8, 9, 17, 18, 19} ∩ wear_window` (config `[defaults] commute_hours`,
     overridable per request). Rationale (§Design-D18): a plain mean lets a
     freezing 07:00 be washed out by twelve indoor-ish afternoon hours —
     exactly the failure the product exists to fix — so exposure-weight the
     hours the user is actually outside, and reserve a quarter of the score
     for the worst hour of the day.
- **FR-7 Ensemble insulation, configurations, and plan smoothing.**
  Ensemble intrinsic insulation from worn garments: `Icl = 0.835 × Σ clo_i +
  0.161` (0 when no garments) — the ASHRAE/McCullough regression.
  Configuration set: base top, bottom, leg base, and footwear always worn;
  mids removable LIFO ordered by descending clo (thickest is outermost, shed
  first; ties break on garment id ascending); outer independently on/off —
  giving ≤ 2×(m+1) ≤ 6 configurations, totally ordered in Icl.
  **Selection (exact algorithm).** Let `best(h)` = the feasible configuration
  minimising `|Icl(c) − target_clo(h, c)|` (feasible = rain-hour cover per
  FR-9 stays on; ties break on fewer worn layers, then garment-id tuple).
  The plan is then produced by a single deterministic left-to-right pass:
  ```
  cur = best(h0); changes = 0; dwell = 1
  for each subsequent wear-window hour h:
      cand = best(h)
      gain = |dev(cur, h)| − |dev(cand, h)|
      if cand ≠ cur and gain > CONFIG_SWITCH_HYSTERESIS (0.10 clo)
         and dwell ≥ MIN_DWELL_HOURS (2) and changes < MAX_CONFIG_CHANGES (3):
          cur = cand; changes += 1; dwell = 1
      else: dwell += 1
      plan[h] = cur
  ```
  The plan is then compressed into contiguous segments for presentation. Any
  layer that is worn earlier and not worn later carries the `carrying` note
  for the hours it is off, because a shed layer must be carried all day —
  the plan says so. Accessories are excluded from `Icl` (§Design-D6); their
  role is FR-9's.
- **FR-8 Outfit assembly under constraints — THE HARD PART.** Given a
  wardrobe, a forecast snapshot, request params (date, occasion, wear
  window, met, commute hours, k, seed), and wear history, the engine returns
  the top-k complete outfits maximizing
  `total = 0.40·S_thermal + 0.15·S_protect + 0.15·S_color + 0.15·S_style +
  0.15·S_variety`, subject to hard constraints HC-1…HC-8:
  - **HC-1 slot coverage:** exactly one base top and one bottom (or one
    `full_body` dress replacing both), exactly one footwear, 0–2 mids, 0–1
    outer, 0–1 leg base. Accessories are **not** enumeration variables; 0–4
    are attached deterministically after selection (FR-9).
  - **HC-2 cleanliness:** every item has status `clean`.
  - **HC-3 occasion:** every core (non-accessory) item lists the requested
    occasion.
  - **HC-4 formality coherence:** max − min formality over core items ≤ 1.
  - **HC-5 no duplicate garment within an outfit.**
  - **HC-6 rain cover:** every hour with `precip_prob ≥ 0.5` has a
    feasible configuration carrying adequate cover for its intensity class
    (FR-9; the deterministically selected umbrella counts where FR-9 allows
    it), and the hourly plan uses one.
  - **HC-7 leg base gating:** a `leg_base` item is allowed only when
    `min over wear-window hours of bare_feels_c ≤ 0 °C`.
  - **HC-8 no-repeat:** the core-item set differs from every outfit worn
    yesterday.
  All eight constraints, all FR-9 protection rules, and all advisories are
  evaluated over **wear-window hours only**; hours outside the window are
  never read.
  Search: per-slot candidate filtering, capped enumeration with an
  admissible partial-score bound, then greedy selection of k outfits with
  pairwise core-item Jaccard ≤ 0.5 (MMR-style diversity), deterministic
  tie-breaks (§Design-D12). Ranks are emitted in non-increasing `score_total`
  order. Returning `1 ≤ n < k` outfits when the feasible set is smaller than
  k is legal and is recorded as a `partial_k` **note** (not a compromise);
  see FR-14. Wardrobe cap 500 garments with a clear error.
- **FR-9 Weather protection, protection score, and accessory attachment.**
  All rules evaluate over wear-window hours only.
  **Rain.** Hours with `precip_prob ≥ 0.5` are *rain hours* (hard, HC-6);
  `0.3 ≤ p < 0.5` hours are *soft-rain hours*. Adequacy by intensity class:
  `< 2.5 mm/h` (light) needs waterproofness ≥ 1 worn or a valid umbrella;
  `2.5–10` (moderate) needs ≥ 2 worn or a valid umbrella; `> 10` (heavy)
  needs waterproofness ≥ 2 **worn** — umbrella insufficient. An umbrella is
  invalid in any hour with `wind_kmh ≥ 35`.
  **Protection score (exact).** Per wear-window hour, starting from 1.0 and
  clamped to [0, 1]:
  | Trigger | Penalty |
  |---|---|
  | rain hour, worn config lacks adequate cover (reachable only under R3, FR-14) | −0.50 |
  | soft-rain hour, worn config lacks at least light-class cover | −0.25 |
  | `wind_kmh ≥ 30` and outermost worn layer windproofness = 0 | −0.20 |
  | `wind_kmh ≥ 30` and outermost worn layer windproofness = 1 | −0.10 |

  `S_protect = Σ_h w_h·p_h / Σ_h w_h`, using FR-6's exposure weights `w_h`.
  **Accessory attachment (deterministic, post-assembly, outside the search).**
  After the k core outfits are selected, each is given accessories by rule —
  this is what makes the cold-extremity and UV rules real rather than
  decorative, at zero cost to the search space:
  | Class | Trigger | 
  |---|---|
  | umbrella | some rain or soft-rain hour's adequacy depends on it, and `wind_kmh < 35` in every such hour |
  | gloves, hat, scarf | `min bare_feels_c over the wear window < 5 °C` |
  | sunglasses | `max uv_index ≥ 6` over `[10:00, 16:00] ∩ wear_window` |

  A class is filled from clean, non-retired accessories of that class whose
  `occasions` include the request occasion and whose formality lies within
  HC-4's spread of the outfit's core items, choosing
  `min by (|formality − median core formality|, garment_id)`. If a triggered
  class has no eligible candidate, no item is attached and FR-15 emits the
  advisory line naming the gap ("no gloves in the wardrobe; 07:00 feels like
  −6 °C"). At most 4 accessories; when more classes trigger, priority is
  umbrella > gloves > hat > scarf > sunglasses. Attached accessories occupy
  `accessory_1..accessory_4` in ascending garment-id order (DATA_MODEL.md
  §2.6), which is what makes FR-19's byte-identity guarantee hold. Accessory
  attachment never changes `Icl`, never changes ranking, and never enters
  `S_color`, `S_style` or `S_variety`.
- **FR-10 Color and style scoring.** `S_color`: mean pairwise harmony over
  core items' main colors using the hue-zone table in §Design-D8 (neutrals
  score 1.0 with anything), minus 0.15 if distinct non-neutral hue families
  (30° buckets over all listed colors) exceed 3. `S_style` = 0.6 ×
  formality tightness (1.0 if spread 0, 0.7 if spread 1) + 0.4 × tag
  cohesion (fraction of core-item pairs sharing ≥ 1 style tag). Both are
  computed over core items only.
- **FR-11 Variety and history.** `S_variety = 1 − mean_i 2^(−d_i / 3)` over
  core items, `d_i` = days since item i was last worn (term 0 if never
  worn); yesterday-identical sets are hard-blocked (HC-8). Wear history
  comes from WearLogs (FR-12); the half-life (3 days) is a named constant.
  History comparisons use the `layer_role` **recorded on the WearLogItem at
  log time** (DATA_MODEL.md §2.7), not the garment's current role, so
  re-tagging a garment can never rewrite history.
- **FR-12 Wear logging.** A recommended outfit (`--rank r`) or an arbitrary
  item list can be logged as worn on a date; multiple logs per date are
  allowed (e.g., gym + work). Logging drives FR-3 counters and FR-11
  history in one transaction, snapshotting each item's `layer_role`. A
  mistaken log can be undone on the same calendar day only, reversing the
  counter/status side effects in one transaction (DATA_MODEL.md §2.7);
  after that day logs are immutable.
- **FR-13 Recommendation persistence.** Every recommendation run is
  append-only: params, seed, engine version, forecast snapshot id, a
  `wardrobe_hash` (FR-19), the ranked outfits with full score breakdowns,
  hourly plans, classified reasoning lines, notes and any compromises.
  Stored plans are self-contained (they snapshot the numbers used), so later
  wardrobe edits never corrupt history.
- **FR-14 Graceful degradation.** The relaxation ladder fires **only when
  zero outfits satisfy HC-1…HC-8**. Returning `1 ≤ n < k` outfits is *not*
  a degradation: it is recorded as the note `{"kind": "partial_k", "n": n,
  "reason": "..."}` and no relaxation is applied. When the feasible set is
  empty the engine relaxes in a fixed lexicographic ladder, re-testing
  feasibility after each step and stopping at the earliest step that
  succeeds — R1 drop HC-8 (variety); R2 widen HC-4 to spread ≤ 2; R3 accept
  one-level-lower rain cover for *moderate*-intensity hours (never for
  heavy) — recording each applied relaxation as a structured `compromise`.
  HC-1, HC-2, HC-3 and HC-5 are never relaxed. If the ladder is exhausted
  the result is a structured `infeasible_wardrobe` error naming the missing
  capability (e.g., "no waterproofness ≥ 2 garment for the 6 heavy-rain
  hours 12:00–17:00") **and carrying the full FR-16 day brief as its
  payload**, so the user learns what the day demands even when the closet
  cannot serve it.
- **FR-15 Explanation generation.** Each returned outfit carries an ordered
  list of reasoning entries `{class, text}`. Rendering is pure deterministic
  template substitution over engine-computed numbers — no free text, no
  garment `notes` passthrough, identical inputs → identical strings. The
  line classes and their emission rules are exhaustive:
  | Class | Emitted | Content |
  |---|---|---|
  | `day_thermal` | always, once | wear-window `bare_feels_c` range and the required-clo range |
  | `layer_change` | once per plan segment boundary | clock time, layer added/shed, and what is carried |
  | `wardrobe_limit` | once per maximal run of clamped hours | floor/ceiling, the clock range, and the signed clo shortfall |
  | `rain` | once per contiguous rain/soft-rain window | window clock range, intensity class, covering garment (or the umbrella) |
  | `wind` | iff the FR-9 wind penalty fired | max wind, whether the outer blocks it |
  | `cold_extremities` | iff `min bare_feels_c < 5 °C` | the attached accessories, or the named gap |
  | `uv` | iff `max uv_index ≥ 6` in window | peak UV and the attached/absent sun accessory |
  | `palette` | always, once | neutral/hue summary and the three-family penalty if applied |
  | `variety` | always, once | days since last wear of the least-fresh core item |
  | `compromise` | once per applied relaxation, plus once for a `partial_k` note | which rule and why |
  **Invariant (gated, EVALS.md M4):** a line of class `c` appears **iff** the
  independent checker finds class `c`'s trigger fired. An unexplained rain
  window and an advisory line for a rule that did not fire are both
  violations.
- **FR-16 Day brief (wardrobe-free).** Given only a forecast snapshot and
  request params, the engine produces a `DayBrief` — no wardrobe required,
  works on an empty database:
  per wear-window hour, `bare_feels_c`, `required_clo` at windproofness 0,
  the bracketing layer archetype (`base` < 0.55 clo | `base+mid` 0.55–1.10 |
  `base+mid+shell` 1.10–1.70 | `base+2mid+insulated shell` > 1.70, thresholds
  named constants), and the rain-cover class from FR-9's intensity bands;
  plus the day-level advisories (cold extremities, wind, UV) and the
  required-clo range. Exposed as `GET /brief?date=` and `dresscast brief`.
  This delivers the first clause of the owner's idea ("recommendations for
  what kinds of clothes to wear") independently of the closet, and is the
  payload of FR-14's `infeasible_wardrobe`. Implementation is a thin reader
  over `comfort.py` + `protection.py`, which already exist.
- **FR-17 API.** A FastAPI app exposes the endpoints in §Architecture-API
  with Pydantic schemas; engine/store errors map to 4xx with structured
  detail codes (`infeasible_wardrobe`, `unknown_garment`,
  `invalid_transition`, `no_extractor_configured`, `wardrobe_too_large`,
  `invalid_params`).
- **FR-18 CLI.** A Typer CLI exposes the commands in §Architecture-CLI;
  every command exits 0 on success, non-zero on failure, and supports
  `--json`. Temperatures display in °C by default with `--units f`
  conversion at the presentation layer only.
- **FR-19 Determinism and quantization.** For fixed (wardrobe state,
  forecast snapshot, wear history, params), recommendation output is
  byte-identical across runs. The engine never reads the clock, filesystem,
  or network; dates and "today" are inputs supplied by the CLI/API edge.
  Cross-platform reproducibility is achieved by **quantizing at the
  boundary**, because `v**0.16` and `exp()` are libm-dependent in the last
  ULP:
  - every score component and `score_total` is rounded to `SCORE_DP = 6`
    decimals **inside the engine, before ranking**, so ranking and
    tie-breaking read the rounded values and a 1-ULP difference can never
    reorder outfits;
  - every physical float written into `hour_plan` and the brief is rounded to
    `PLAN_DP = 3` decimals at the same point;
  - `wardrobe_hash` = SHA-256 of
    `json.dumps(records, sort_keys=True, separators=(',',':'))` over **all
    non-retired garments** (not the request-filtered subset, so the hash is
    request-independent), each record restricted to the field list in
    DATA_MODEL.md §2.4 with floats pre-rounded to `PLAN_DP`.
  Comparison for determinism excludes the non-deterministic-by-design fields
  `id`, `created_at`, `fetched_at`, `snapshot_id` (EVALS.md M7).
  `seed` is accepted, validated and persisted for forward compatibility but
  **has no effect in this pass** (§Design-D12); no RNG exists on the core
  path.

## Non-goals (this pass)

- **No wardrobe import/export.** *(Cut in revision 2.)* A JSON round-trip
  (~210 lines across code and tests, two endpoints, two CLI commands) is
  convenience, not capability, and it was the cheapest way to buy back the
  budget the two blockers' fixes consumed. `sqlite3 .dump` of the single-user
  database is an adequate stopgap; the interchange schema is a first
  candidate for the next pass.
- **No web or mobile UI** (workspace-wide decision). Photos are image files
  referenced by path; there is no camera capture flow, background removal,
  or gallery. CLI + API only.
- **Live vision extractor deferred.** This pass ships the
  `AttributeExtractor` protocol, the deterministic fixture implementation,
  and the human-confirmation flow. The live implementation (zero-shot
  CLIP-style classifier or hosted vision-language model, optional extra) is
  next phase. Manual tagging is the product's floor and is fully supported.
- **Live weather is shipped but best-effort and never eval-gated.** All
  tests and evals run against the fixture provider (CONVENTIONS.md §3);
  Open-Meteo mapping is unit-tested against a committed sample response,
  no network.
- **No learned personalization.** Wear logs are collected now precisely so a
  future phase can learn preferences ("you never wear the orange sweater"),
  but this pass is rule-grounded only — rules first, ML later, so there is
  a baseline to evaluate against.
- **No fit/size/body modeling, no shopping or purchase suggestions, no
  social features.**
- **No indoor comfort modeling.** The tool optimizes for outdoor exposure
  during the wear window at a configurable metabolic rate, with commute
  hours weighted (§Design-D18); indoors you shed layers, which the layer
  plan already supports.
- **No fabric physiology beyond the ordinal attributes.** Moisture-vapor
  resistance (ISO 11092 Ret), breathability, and wicking are out of scope;
  waterproofness/windproofness are user-tagged ordinals (§Design-D7). A
  consequence, stated explicitly: a waterproof shell worn in a warm downpour
  is scored as fully protective and only as over-insulated as its clo makes
  it — the real discomfort of a non-breathable shell at 28 °C is not
  modelled. FR-6's floor clamp keeps that hour scoreable and honest (the
  outfit is at the minimum the rain allows) rather than silently penalised.
- **No precipitation-type distinction.** Snow, sleet, and rain all count as
  precipitation with intensity in mm/h liquid-equivalent; a snow-specific
  traction/footwear rule is a future refinement.
- **No multi-segment days or travel mode.** One occasion per request (run
  twice for office-then-dinner); one home location in config.
- **No multi-user, auth, or hosting concerns.**
- **No real personal photos or branded garment data committed to the
  repo.** All fixtures are synthetic wardrobes and synthetic weather
  (EVALS.md §4).

## Architecture

Package `dresscast` at `projects/dresscast/src/dresscast/`. Layering per
CONVENTIONS.md: pure engine, Protocol-based adapters, Repository store, thin
API/CLI. A thin `services.py` orchestrates adapters + store + engine (the
only layer touching both I/O and the engine); API and CLI call services only.

### Engine modules (`engine/`, pure, deterministic)

- `models.py` — Pydantic domain models: `Garment`, `HourlyWeather`,
  `DayForecast`, `RequestParams`, `LayerConfig`, `HourPlanEntry`,
  `ScoredOutfit`, `Recommendation`, `DayBrief`, `Compromise`, `Note`,
  `ReasonLine`; all named constants (weights, bands, thresholds, half-life,
  `EXPOSURE_COMMUTE`, `CONFIG_SWITCH_HYSTERESIS`, `MIN_DWELL_HOURS`,
  `MAX_CONFIG_CHANGES`, `SCORE_DP`, `PLAN_DP`) in one place.
- `comfort.py` — feels-like with ramps and the bare/config split (FR-5),
  `required_clo` (FR-6), achievable floor/ceiling (FR-6/D17), ensemble
  insulation, configuration enumeration, per-hour selection with hysteresis
  and segment compression (FR-7), `S_thermal` (FR-6), and `day_brief`
  (FR-16): `hourly_plan(outfit, forecast, params) -> list[HourPlanEntry]`,
  `thermal_score(plan, params) -> float`,
  `achievable_band(candidates, forecast, params) -> Band`,
  `day_brief(forecast, params) -> DayBrief`.
- `protection.py` — rain/wind/UV/cold-extremity rules, `S_protect`, and
  accessory attachment (FR-9): `rain_hours(forecast, window)`,
  `cover_adequate(config, hour, umbrella) -> bool`,
  `protect_score(outfit, plan, params) -> tuple[float, list[Trigger]]`,
  `attach_accessories(outfit, wardrobe, triggers) -> list[Garment]`.
- `palette.py` — color harmony (FR-10): hue-zone pairwise table, hue-family
  counting, `color_score(outfit) -> float`.
- `style.py` — formality coherence and tag cohesion (FR-10), occasion
  filtering helpers (HC-3, HC-4).
- `variety.py` — recency penalty and repeat detection (FR-11, HC-8):
  `variety_score(outfit, history, today) -> float`.
- `assemble.py` — the hard part (FR-8, FR-14): per-slot candidate
  filtering, bounded enumeration, hard-constraint checking, scalarized
  scoring with quantization, MMR diversity selection, `partial_k` notes,
  relaxation ladder:
  `recommend(wardrobe, forecast, history, params) -> Recommendation`.
- `explain.py` — deterministic template rendering of classified reasoning
  lines and the hourly plan table (FR-15).

### Adapter interfaces (`adapters/`)

Each is a `typing.Protocol`; offline implementations are the defaults used
by tests and evals.

- **`WeatherProvider`** — `get_day(location: Location, date: date) ->
  DayForecast` (23–25 `HourlyWeather` rows; raises `ForecastUnavailable`).
  - `FixtureWeatherProvider` (offline, default): reads committed JSON
    scenario files keyed by ISO date from a configured directory
    (`evals/fixtures/weather/` for evals; a user-suppliable path in
    production). Deterministic, no network.
  - `OpenMeteoWeatherProvider` (live): Open-Meteo forecast API (free, no
    API key) — `GET /v1/forecast` with `hourly=temperature_2m,
    relative_humidity_2m,precipitation_probability,precipitation,
    wind_speed_10m,uv_index&timezone=<tz>`; activates only when
    `DRESSCAST_LIVE_WEATHER=1` and a location (lat/lon/tz) is configured.
    Provider `apparent_temperature` is stored for display comparison only;
    the engine always computes its own feels-like (FR-5) so results are
    deterministic and testable.
- **`AttributeExtractor`** — `extract(photo_path: str) ->
  AttributeSuggestionPayload | None` (proposed category, colors, style
  tags, formality; per-field confidence 0–1).
  - `FixtureAttributeExtractor` (offline, default): committed JSON map
    keyed by photo SHA-256 (falling back to basename); deterministic; used
    by tests/evals of the suggestion flow.
  - `VisionAttributeExtractor` (live, next phase, optional extra
    `vision`): zero-shot open-vocabulary classification (CLIP-style,
    Radford et al. 2021) over the category/color/style vocabularies, or a
    hosted vision-language model; credentials/weights via documented env
    vars. Never required by core tests or evals; suggestions always pass
    through human confirmation (FR-2).

### Store (`store/`)

`Repository` protocol with `SqliteRepository` (stdlib `sqlite3`, schema in
DATA_MODEL.md §4) and `InMemoryRepository` for tests. Operations: garment
CRUD + state transitions, suggestion append/resolve (with the FR-2 cascade
applied atomically), snapshot append + read, recommendation append + read,
wear-log append (with counter side effects and `layer_role` snapshotting in
the same transaction), laundry append, history queries (`last_worn_dates`,
`worn_yesterday_sets`), and `schema_version` read/migrate. No engine logic.

### API (FastAPI, `api/`)

```
GET    /health                                  -> {status, version}
POST   /garments                                -> 201 Garment            (FR-1)
GET    /garments?status=&occasion=&category=    -> [Garment]
GET    /garments/{id}                           -> Garment
PATCH  /garments/{id}                           -> Garment                (FR-1; includes status transitions, FR-3)
POST   /garments/{id}/photo                     -> Garment                (FR-2; multipart upload)
POST   /garments/{id}/suggest                   -> 201 AttributeSuggestion (FR-2)
POST   /suggestions/{id}/accept                 -> Garment                (FR-2; body: {fields: [...]})
POST   /suggestions/{id}/reject                 -> AttributeSuggestion
GET    /forecast?date=YYYY-MM-DD                -> ForecastSnapshot       (FR-4)
GET    /brief?date=YYYY-MM-DD&met=&window=      -> DayBrief               (FR-16; no wardrobe needed)
POST   /recommendations                         -> 201 Recommendation     (FR-8; body: {date, occasion, wear_window?, met?, commute_hours?, k?, seed?})
GET    /recommendations/{id}                    -> Recommendation (outfits, plans, reasoning)
POST   /recommendations/{id}/wear               -> 201 WearLog            (FR-12; body: {rank, date?})
POST   /wear                                    -> 201 WearLog            (FR-12; body: {garment_ids, date?})
POST   /laundry                                 -> 201 LaundryEvent       (FR-3; body: {garment_ids | all_dirty: true})
```

There is no `weights` field: objective weights are engine constants in this
pass (§Design-D13). They are copied into the stored `params` for audit.

### CLI (Typer, `cli/`)

```
dresscast add [--photo PATH] [--category C] [--warmth 0-5 | --clo F] [...]   # FR-1/2
dresscast ls [--status clean|dirty] [--occasion O] [--category C]
dresscast show GARMENT
dresscast edit GARMENT [--clo F] [--formality N] [--colors ...] [...]        # FR-1
dresscast suggest GARMENT [--accept | --accept-fields f1,f2]                 # FR-2
dresscast forecast [--date YYYY-MM-DD]                                       # FR-4
dresscast brief [--date D] [--met 1.6] [--window 07:00-22:00]                # FR-16 (US-9)
dresscast outfit [--date D] [--occasion work] [--k 3] [--seed 7]
                 [--met 1.6] [--window 07:00-22:00] [--commute 7-9,17-19]    # FR-8 (US-3/4/5/6)
dresscast explain REC [--rank 1]                                             # stored reasoning + hourly plan
dresscast wear [REC --rank 1 | --items id,id,...] [--date D] [--undo LOG]    # FR-12
dresscast laundry [--all | GARMENT...]                                       # FR-3
dresscast history [--days 14]                                                # FR-11 view
dresscast serve [--port 8000]                                                # uvicorn wrapper
```

All commands accept `--db PATH` (default `~/.dresscast/dresscast.db`),
`--config PATH` (default `~/.dresscast/config.toml`, DATA_MODEL.md §5), and
`--json`. `--date` defaults to today read at the CLI edge (never inside the
engine, FR-19). `--seed` is accepted and persisted but has no effect in this
pass (FR-19).

### Size budget (implementation phase)

Revision 1's budget was understated: it allotted 1,250 lines to "tests +
evals" while EVALS.md requires an independent constraint checker, a
brute-force reference, live baselines, a rollout simulator, and sixteen test
modules. The honest split, after cutting wardrobe import/export (revision 1's
FR-3) and trimming the API to what the CLI actually needs:

| Area | Lines | Notes |
|---|---|---|
| `engine/models.py` | 240 | models + every named constant |
| `engine/comfort.py` | 300 | feels-like + ramps, required, achievable band, configs, smoothing, brief |
| `engine/protection.py` | 150 | rules, `S_protect`, accessory attachment |
| `engine/palette.py` | 90 | |
| `engine/style.py` | 70 | |
| `engine/variety.py` | 60 | |
| `engine/assemble.py` | 280 | filtering, bounded enumeration, MMR, ladder |
| `engine/explain.py` | 120 | 10 line classes |
| **engine subtotal** | **1,310** | the hard part gets the deepest treatment |
| `adapters/` | 250 | weather protocol + fixture + Open-Meteo mapping; extractor protocol + fixture |
| `store/` | 320 | repository, SQLite schema + migrations, in-memory |
| `services.py` | 110 | |
| `api/` | 210 | 15 endpoints |
| `cli/` | 300 | 13 commands |
| `tests/` | 780 | 17 modules (EVALS.md §7) |
| `evals/` | 700 | metrics 450, run 110, test_gates 140 |
| **Total** | **≈ 3,980** | |

If the build exceeds 4,000 lines, cut in this order and record it in
REVIEW.md: (1) reduce `OpenMeteoWeatherProvider` to a pure mapping function
plus its sample-response test (−60); (2) drop `dresscast history` (−40);
(3) drop the `in_laundry` intermediate status, going straight
`dirty → clean` (−30); (4) reduce the rollout evals from three sequences to
two (−40).

## Key design decisions and assumptions

- **D1 — Warmth is measured in clo, taken from the standard tables.** The
  clo is the standard unit of clothing insulation (Gagge, Burton & Bazett,
  1941): 1 clo = 0.155 m²·K/W, the ensemble keeping a resting person
  comfortable at 21 °C. Per-garment values come from the ASHRAE Standard 55
  / ASHRAE Fundamentals ch. 9 / ISO 9920 garment tables; the locked
  "warmth rating" attribute *is* the garment's clo, entered directly or via
  a warmth level 0–5 that maps to the category preset. Category presets
  (defaults; user-overridable within ±0.15):

  | Category | clo | Role | Source |
  |---|---|---|---|
  | t-shirt | 0.08 | base | ASHRAE 55 |
  | long-sleeve tee / polo | 0.12 / 0.17 | base | ASHRAE 55 |
  | short-sleeve shirt | 0.19 | base | ASHRAE 55 |
  | long-sleeve shirt | 0.25 | base | ASHRAE 55 |
  | flannel shirt | 0.34 | base or mid | ASHRAE 55 |
  | thin sweater / cardigan | 0.25 | mid | ASHRAE 55 |
  | thick sweater / hoodie | 0.36 | mid | ASHRAE 55 |
  | fleece | 0.30 | mid | practice-calibrated |
  | blazer / suit jacket | 0.36 | mid | ASHRAE 55 |
  | rain shell (unlined) | 0.25 | outer | practice-calibrated |
  | light jacket | 0.40 | outer | practice-calibrated |
  | wool coat | 0.60 | outer | practice-calibrated (ISO 9920-consistent) |
  | parka / down jacket | 0.70 | outer | ISO 9920-consistent |
  | thermal top / bottoms | 0.20 / 0.15 | mid / leg_base | ASHRAE 55 (long underwear) |
  | thin trousers / thick trousers / jeans | 0.15 / 0.24 / 0.24 | bottom | ASHRAE 55 |
  | shorts | 0.08 | bottom | ASHRAE 55 |
  | skirt (thin/thick) | 0.14 / 0.23 | bottom | ASHRAE 55 |
  | dress (light/long-sleeve) | 0.23 / 0.33 | full_body | ASHRAE 55 |
  | shoes / sneakers / sandals / boots | 0.02 / 0.02 / 0.02 / 0.10 | footwear | ASHRAE 55, hosiery included |
  | hat / gloves / scarf / umbrella / sunglasses | 0.0 (advisory) | accessory | see D6 |

  **Socks are not independently catalogued.** The footwear presets already
  include hosiery (boots 0.10 = boots + calf socks; shoes/sneakers 0.02
  include ankle socks), matching how ASHRAE's ensemble rows are used. There
  is therefore no socks category, no socks slot, and no socks term in any
  worked example. *(Revision 2: revision 1 had socks both in the preset table
  and folded into footwear, and its worked example double-counted them.)*
  "Practice-calibrated" rows are outerwear values consistent with ISO 9920
  ensemble tables where ASHRAE's garment list is sparse; they are pinned by
  the eval golden table so they cannot drift silently.
- **D2 — Ensemble insulation uses the ASHRAE summation regression.**
  `Icl = 0.835 × Σ Iclu,i + 0.161` clo (McCullough, Jones & Huck's
  regression as adopted in ASHRAE Fundamentals / ISO 9920), which corrects
  the simple sum for garment overlap and compression; defined as 0 for the
  empty set (the regression's intercept models a minimal clothed body, not
  bare skin). Assumption: the regression is accurate enough (±0.1 clo)
  across our ensembles; evals pin hand-computed cases.
- **D3 — Required insulation from a heat-balance-lite model, with its
  residuals stated.** `required_clo(T_feels, met) = (34 − T_feels)/(7.66·met)
  − 0.7`, clamped to [0, 4.5]. Derivation, in the spirit of ISO 11079's IREQ
  and Fanger's comfort equation (ISO 7730): required total insulation ≈
  (T_skin − T_air)/(0.155 · H_dry), with comfort mean skin temperature 34 °C,
  metabolic heat 58.15 W/m² per met (ASHRAE definition), a 0.85 factor for
  the fraction of metabolic heat lost as dry heat through clothing, hence
  0.155 × 58.15 × 0.85 ≈ 7.66; subtracting the still-air boundary-layer
  insulation Ia ≈ 0.7 clo (ISO 9920) converts total to intrinsic.
  **Calibration, honestly stated** (all three pinned by EVALS.md M1's
  calibration family at ±0.30 clo, arithmetic in each fixture's `rationale`):

  | Anchor | Model | Published | Residual |
  |---|---|---|---|
  | 21.0 °C, met 1.0 — *the definition of 1 clo* | 0.997 | 1.000 | −0.003 |
  | 21.75 °C, met 1.1 — ASHRAE 55 winter comfort-zone centre, 1.0 clo ensemble | 0.754 | 1.000 | **−0.246** |
  | 24.5 °C, met 1.1 — ASHRAE 55 summer comfort-zone centre, 0.5 clo ensemble | 0.427 | 0.500 | −0.073 |

  The two ASHRAE-55 zone anchors and the definitional anchor are **not
  simultaneously satisfiable** under this one-parameter form (fitting the
  winter zone would require 7.66 → 6.55, which then returns 1.29 clo at the
  definitional point). We prioritise the definitional anchor and accept a
  ≤ 0.30 clo lean at the indoor winter operating point — plausible since the
  ASHRAE zones assume still indoor air at PMV ±0.5 while our Ia is a
  still-air value applied to outdoor exposure. This is a stated model
  limitation, not a hidden one, and M1 fails if it ever grows.
  *(Revision 1 claimed 0.72 clo at 22 °C/1.1 met "matched ASHRAE 55's
  canonical winter operating point". It does not — that point is 1.0 clo.
  The claim is withdrawn; the value 0.7242 is now correctly labelled as
  spec-pinning, not external truth.)*
  Default met = 1.6 (assumption: a mixed urban wear-window of walking ~2.0
  met and standing/transit ~1.2 met; ASHRAE met table). Validated operating
  range at met 1.6: `required_clo` is monotone and well-behaved everywhere,
  but *absolute* in-band comfort against a realistic personal wardrobe is
  only reachable for roughly −3 °C to +25 °C bare feels-like (EVALS.md §5.2
  works the arithmetic). Outside that range D17's achievable clamp takes
  over — this is a deliberate design decision, not an unnoticed limit.
- **D4 — Feels-like uses the operational formulas, ramped at their validity
  edges, and wind acts through it exactly once.** Wind chill: the 2001
  JAG/TI formula adopted by the US NWS and Environment Canada (validity
  T ≤ 10 °C, v > 4.8 km/h). Heat: Steadman's (1984) apparent temperature as
  used by the Australian Bureau of Meteorology (non-radiant form), applied
  at T ≥ 26 °C. Revision 1 *switched* at those edges, which made feels-like
  discontinuous — at 45 km/h wind, T = 10.0 gave 5.72 °C and T = 10.1 gave
  10.1 °C, a 4.4 °C jump for a 0.1 °C input change that moved required clo by
  0.36 and could flip an entire hourly plan; four of the twelve scenarios
  cross that edge. FR-5 therefore *fades* each delta over a ramp (10→14 °C
  cold, 24→26 °C hot). Both formulas are defined only inside their validity
  ranges, so blending outside them is a modelling choice, not an error; it is
  labelled as a deviation and pinned by M1's continuity family (Lipschitz-3,
  measured worst case 2.41 °C per °C at the cold edge). To avoid
  double-counting wind we do *not* also modulate the D3 boundary-layer term;
  instead a windproof outermost layer attenuates the wind-chill input wind
  ({1.0, 0.6, 0.3} for windproofness 0/1/2 — calibration constants reflecting
  that laminate shells largely stop convective stripping of the boundary
  layer). This makes required clo config-dependent, which is exactly the real
  phenomenon ("windbreaker weather") — and is precisely why FR-5 also defines
  the config-independent `bare_feels_c` for every rule that must be evaluated
  before an outfit exists.
- **D5 — Comfort band ±0.25 clo, hour score linear to zero at ±1.0.**
  ±0.25 clo ≈ ±3.1 °C at met 1.6 (via D3's slope 1/(7.66·met)). Grounding:
  ASHRAE 55's indoor comfort zone is roughly ±1.5–2 °C (≈ ±0.2 clo) for a
  given ensemble; we widen slightly for outdoor, transient exposure per
  the adaptive-comfort literature (de Dear & Brager's adaptive model,
  adopted as ASHRAE 55's adaptive method). |dev| = 1.0 clo (≈ 12 °C
  mismatch at met 1.6) scores zero: that is coat-missing-in-winter
  territory. Deviation is signed in reports (too warm vs too cold) but
  scored symmetrically; asymmetric discomfort weighting is a possible
  refinement once wear feedback exists. The band is applied to the
  *achievable target* (D17), never to the raw requirement.
- **D6 — Slots follow the mountaineers' three-layer doctrine.** Base
  (moisture/next-to-skin), mid (insulation), shell/outer (wind + rain) —
  the layering system as codified in *Mountaineering: The Freedom of the
  Hills* — extended with bottom, leg base, footwear, full-body (dress), and
  accessory roles to cover a whole wardrobe. Configurations model what people
  actually do (carry the coat, tie the sweater): outer independently on/off,
  mids shed outermost-first (LIFO by clo descending). Accessories contribute
  advisory and protective value, not ensemble clo: their tabulated insulation
  (~0.02–0.05) is below the model's resolution, but their protective role
  (rain, UV, extremities) is rule-relevant, so they enter FR-9, not FR-7.
  Assumption: 0–2 mids suffice for a personal wardrobe's realistic stacks —
  with the D1 presets that ceiling is Icl ≈ 2.04 clo for the eval's small
  wardrobe, which is *below* what a −6 °C morning demands (2.56 clo). That
  gap is real and is handled by D17, not papered over. *(Revision 1 asserted
  "base + 2 mids + shell covers ~2.0+ clo ensembles" without checking it
  against D3's winter output.)*
- **D7 — Precipitation semantics follow NWS PoP language, WMO/Met Office
  intensity classes, and hydrostatic-head practice.** Probability: NWS
  categorical PoP language puts "likely" at ≥ 60% and "chance" at 30–50%;
  we draw the hard line at 0.5 (miss a "likely" rain and the product has
  failed; 0.3–0.49 is a soft penalty with the magnitude given in FR-9).
  Intensity: light < 2.5 mm/h, moderate 2.5–10, heavy > 10 (WMO / UK Met
  Office). Waterproofness ordinal ↔ industry hydrostatic-head ratings
  (ISO 811): 0 = untreated; 1 ≈ DWR/water-resistant (~1,500 mm); 2 ≈
  waterproof taped shell (≥ 5,000 mm); 3 ≈ storm shell (≥ 10,000 mm).
  Umbrellas: valid cover for light/moderate rain but not heavy rain nor
  wind ≥ 35 km/h (≈ Beaufort 5, where umbrella use becomes difficult).
- **D8 — Color harmony from the Itten wheel plus the stylists' neutral
  doctrine.** Each color is (name, hue 0–360 or null, neutral flag). The
  neutral set follows menswear/capsule-wardrobe practice: black, white, gray,
  navy, beige/tan/khaki, denim-blue, olive — neutrals pair with anything
  (score 1.0). Non-neutral pairs score by circular hue distance Δh on Itten's
  12-hue wheel (*The Art of Color*): Δh ≤ 15 monochromatic 0.90; 15–45
  analogous 0.85; 45–105 clash 0.35; 105–150 triadic-zone 0.70; 150–180
  complementary 0.80. The "no more than three colors" rule of thumb becomes:
  > 3 distinct non-neutral 30° hue families → −0.15 on `S_color`. Known
  limitation (accepted): no lightness/saturation axis, so "two slightly
  different reds" lands in the monochromatic bucket; the golden eval set
  (EVALS.md M5) therefore tests hue-level judgments only, and requires that
  at least a third of its labelled pairs turn on something *other* than the
  Δh bucket, so M5 cannot degenerate into a unit test of this table.
- **D9 — Formality is a 5-step ladder mapped to dress codes.** 1 =
  athleisure/beach, 2 = casual, 3 = smart casual, 4 = business casual/
  business, 5 = formal. Hard rule HC-4 (spread ≤ 1) encodes the classic
  mismatch failure (running shoes with suit trousers spans 1↔4). Occasions
  are a separate, user-owned tag list on each garment (HC-3 filters on it);
  formality enforces *internal* coherence, occasions enforce *external* fit.
  There is deliberately **no** occasion→formality band: the two mechanisms
  together are sufficient, and a third would be a redundant place for rules
  to disagree. *(Revision 1's config key `[occasions.*] formality_band` was
  consumed by nothing and is deleted.)* Default occasion vocabulary: casual,
  work, sport, outdoor, formal (config-extensible).
- **D10 — Variety is exponential recency decay with a hard yesterday
  block.** Wearing an item today halves its penalty every 3 days
  (half-life constant, assumption tuned for a ~50–150 item wardrobe);
  identical core sets on consecutive days are blocked outright (HC-8) — the
  owner's stated requirement "don't repeat yesterday" is a constraint, not a
  preference. Because HC-8 is a hard constraint, the *evals* must not gate on
  it as if it measured the variety score (EVALS.md M6 gates a 3-day repeat
  window and a novel-item rate instead, which HC-8 does not imply).
  Utilization is measured in the rollout evals rather than optimized
  directly — recency decay plus MMR diversity is the mechanism; the eval
  verifies it suffices, against a live-computed baseline.
- **D11 — Laundry thresholds follow garment-care guidance.** Default
  `wears_before_laundry` by category: tees/base layers 1–2, shirts 2,
  knitwear 5, jeans/trousers 5, shorts/skirts 3, outerwear 30, footwear/
  accessories uncounted (999) — consistent with American Cleaning
  Institute-style wash-frequency guidance. Auto-dirty at threshold is
  deliberate: a tool that recommends a shirt already worn twice silently
  loses trust. Manual override always available (FR-3).
- **D12 — Assembly is filter → bounded enumeration → scalarized scoring →
  MMR, not ILP and not learned.** Hard constraints are filters, soft
  qualities are a weighted scalarization — the standard multi-criteria
  pattern that keeps every outfit's score explainable component-by-component
  (a must for FR-15). Search: per-slot candidate lists (occasion +
  cleanliness + role filtered) are capped at 40 per slot when larger (kept by
  slot-local thermal plausibility: |clo − slot target| ascending, id
  tie-break; slot targets derived from the day's `bare_feels_c` required-clo
  range, hence config-independent and computable before any outfit exists);
  enumeration over base × bottom × footwear × mid-subsets (≤ 2) × outer ×
  leg-base proceeds with an admissible optimistic bound (thermal bounded by
  best-case config coverage; other components bounded by 1.0) pruning partial
  outfits that cannot beat the current k-th best. Accessories are *not*
  enumerated (FR-9), which keeps the space at
  `|base|·|bottom|·|foot|·(1+m+C(m,2))·(1+|outer|)·(1+|legbase|)` — 9,900 for
  the eval's small wardrobe, and ≪ 100k after caps for a 150-item closet.
  Per-outfit cost is ≤ 6 configs × ≤ 16 hours with `required_clo` memoised
  per (hour, windproofness) — ~90 lookups per outfit. Diversity: greedy
  MMR-style selection (Carbonell & Goldstein 1998) with a Jaccard ≤ 0.5
  overlap cap. Determinism: no randomness on the core path; ties break on
  (rounded score desc, sorted garment-id tuple asc); `seed` is accepted and
  persisted for forward compatibility but has **no effect in this pass** —
  it exists for the post-MVP `--surprise` jitter and for API stability, and
  no RNG is constructed.
- **D13 — Objective weights: thermal 0.40, protection 0.15, color 0.15,
  style 0.15, variety 0.15.** Thermal dominates because being cold or wet
  is the failure the product exists to prevent (and protection's hard core
  is already a constraint, HC-6); the four remaining qualities are
  deliberately equal — with a personal wardrobe the data cannot justify
  finer distinctions yet. Weights are engine constants in `models.py`, **not
  overridable per request in this pass** (a per-request weights field would
  make every eval gate conditional on a request parameter); they are copied
  into the stored `params` so a historical recommendation is reproducible if
  the constants later change.
- **D14 — Offline-first adapters, snapshots, and human-in-the-loop
  extraction.** Weather: fixture provider is the default; Open-Meteo chosen
  for the live adapter because it is free, keyless, and serves all required
  hourly variables — activation is an explicit opt-in
  (`DRESSCAST_LIVE_WEATHER=1` + configured location), satisfying
  CONVENTIONS.md §3 even though no credential exists. Every fetch is
  snapshotted append-only so recommendations are reproducible after the fact.
  Extraction: suggestions are always staged and human-confirmed (FR-2) — a
  mistagged clo poisons every future recommendation, so no adapter writes
  garment attributes directly, and FR-2's cascade never overwrites a value
  the user set by hand.
- **D15 — Units and time.** SI internally: °C, km/h wind (converted to m/s
  inside Steadman's AT), mm/h precipitation, hours local to the configured
  timezone, ordered by `seq` (DST-safe). °F is a CLI display conversion only
  (FR-18). Time is always an input; the engine is clock-free (FR-19). Default
  wear window 07:00–22:00 with commute hours 07–09 and 17–19 weighted ×3
  (D18); both configurable per request.
- **D16 — Trust boundaries.** No likeness/finance/health surface: garment
  photos are the owner's own property, stay on local disk, and are never
  transmitted by offline adapters; the live vision extractor (next phase)
  must document exactly what leaves the machine before it can be enabled.
- **D17 — Comfort is scored against what the closet can achieve, not against
  physics alone.** *(New in revision 2 — this is the fix for the calibration
  blocker.)* Working D1's presets through D2 and D3: a complete, tight
  personal wardrobe reaches Icl ∈ [0.311, 2.040] clo, so with D5's ±0.25 band
  an outfit can be *absolutely* in band only when `required_clo ∈ [0.061,
  2.290]` — bare feels-like roughly −3 °C to +25 °C at met 1.6. Below and
  above that, **no outfit anyone could assemble is in band**, and a raw
  deviation score would be flat-zero across every candidate, giving the
  engine no gradient and the eval no signal on exactly the days that matter
  most. FR-6 therefore clamps the per-hour target into the achievable band
  and scores relative to it, while continuing to report the raw
  `required_clo` and the signed shortfall, and FR-15 tells the user in words.
  Consequences, all deliberate: a −6 °C morning is scored on "you are wearing
  the warmest thing you own" (and told you are 0.52 clo short); a 28 °C
  downpour where HC-6 mandates a shell is scored on "you are wearing the
  least insulation the rain allows" rather than punished for the shell's
  0.52 clo. EVALS.md gates the maximality property directly (M2c = 1.00 on
  clamped hours) so "clamped" can never become "anything goes".
- **D18 — Hours are not equally important, and the worst hour is not
  averageable away.** *(New in revision 2.)* The default wear window is 15
  hours, most of which a commuter spends indoors; a plain mean over them lets
  a freezing 07:30 be washed out by an agreeable 14:00 — the precise failure
  the problem statement says the product exists to fix. `S_thermal` therefore
  weights commute hours ×3 and reserves 0.25 of the score for the day's worst
  hour (FR-6.4). `S_protect` uses the same weights, so a soaking commute
  costs more than a soaking lunch break. Both the window and the commute
  hours are request parameters, so a user who works outdoors sets them flat.
