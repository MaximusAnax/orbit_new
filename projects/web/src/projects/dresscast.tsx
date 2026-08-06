/**
 * DressCast — what to wear today, and why.
 *
 * The product's hard part is not picking clothes; it is *justifying* them. A
 * cold morning that becomes a warm afternoon has no single right outfit, only a
 * right plan: which garments, worn in which layers, shed at which hour, carried
 * from then on. So this screen never shows a garment without the reasoning that
 * put it there — the hourly demand curve (required clo from feels-like), the
 * ensemble that tracks it, the rain window that forces cover, and the engine's
 * own classified reasoning lines.
 *
 * The closet is a constraint, not a catalogue: dirty garments are excluded by
 * the engine, wearing an outfit advances the laundry counters, and the next
 * recommendation changes because of it. That loop is on the page.
 *
 * The API wraps errors as {error: {code, message, detail}}, which the shared
 * client cannot decode (it expects FastAPI's `detail`), so `decode()` restores
 * the structured code/message before Async renders it — an infeasible wardrobe
 * must say *what is missing*, not "[object Object]".
 */

import { useState } from "react";
import { ApiError, client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import type { QueryState } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Field, Input, Meter, Page, Panel, Pill, Select, Split, Stat, StatRow,
  Table, Tabs,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./dresscast.css";

const api = client("dresscast");

/* ------------------------------------------------------------------ types */

type GarmentColor = { name: string; hue: number | null; neutral: boolean; role: "main" | "accent" };

type Garment = {
  id: string;
  name: string;
  category: string;
  layer_role: string;
  accessory_class: string | null;
  clo: number;
  waterproofness: number;
  windproofness: number;
  formality: number;
  colors: GarmentColor[];
  occasions: string[];
  style_tags: string[];
  wears_before_laundry: number;
  wears_since_wash: number;
  status: string;
};

type Advisory = { kind: string; text: string; value: number };

type BriefHour = {
  hour: number;
  temp_c: number;
  wind_kmh: number;
  precip_prob: number;
  uv_index: number;
  bare_feels_c: number;
  required_clo: number;
  archetype: string;
  rain_required: boolean;
};

type DayBrief = {
  date: string;
  wear_window: [number, number];
  met: number;
  hours: BriefHour[];
  required_clo_min: number;
  required_clo_max: number;
  bare_feels_min: number;
  bare_feels_max: number;
  archetype_range: string[];
  advisories: Advisory[];
};

type HourPlan = {
  hour: number;
  precip_prob: number;
  precip_mmh: number;
  feels_c: number;
  required_clo: number;
  target_clo: number;
  clamped: string | null;
  worn_slots: string[];
  carried_slots: string[];
  ensemble_clo: number;
  in_band: boolean;
  rain_cover_on: boolean;
  notes: string[];
};

type ReasonLine = { class: string; text: string };
type Accessory = { garment_id: string; class: string; trigger: string };
type Compromise = { rule: string; detail: string };
type OutfitNote = { kind: string } & Record<string, unknown>;

type ScoredOutfit = {
  id: string;
  rank: number;
  score_total: number;
  scores: {
    thermal: number;
    protection: number;
    color: number;
    style: number;
    variety: number;
    weights: Record<string, number>;
  };
  items: { slot: string; garment_id: string }[];
  hour_plan: HourPlan[];
  reasoning: ReasonLine[];
  accessories: Accessory[];
  notes: OutfitNote[];
  compromises: Compromise[];
};

type Recommendation = {
  id: string;
  date: string;
  outfits: ScoredOutfit[];
  compromises: Compromise[];
};

type WearLog = { id: string; date: string };

/* ------------------------------------------------------------- formatting */

const OCCASIONS = ["work", "casual", "outdoor", "sport", "formal"];
const ACTIVITY = [
  { met: 1.2, label: "Mostly seated" },
  { met: 1.6, label: "Walking about" },
  { met: 2.2, label: "On the move" },
];

const SLOT_ORDER = [
  "base", "mid", "mid_1", "mid_2", "mid_3", "outer", "full_body", "leg_base", "bottom", "footwear",
];
const SLOT_LABEL: Record<string, string> = {
  base: "Base",
  mid: "Mid",
  mid_1: "Mid",
  mid_2: "Second mid",
  mid_3: "Third mid",
  outer: "Outer",
  full_body: "Full body",
  leg_base: "Leg base",
  bottom: "Bottom",
  footwear: "Footwear",
};
const CLASS_LABEL: Record<string, string> = {
  day_thermal: "The day",
  layer_change: "Layer change",
  wardrobe_limit: "Wardrobe limit",
  cold_extremities: "Extremities",
  rain: "Rain",
  wind: "Wind",
  uv: "UV",
  palette: "Colour",
  style: "Style",
  variety: "Variety",
  occasion: "Occasion",
};
const CLASS_TONE: Record<string, "neutral" | "ok" | "warn" | "accent"> = {
  day_thermal: "accent",
  layer_change: "accent",
  wardrobe_limit: "warn",
  cold_extremities: "warn",
  rain: "warn",
  wind: "warn",
  uv: "warn",
};

function slotRank(slot: string): number {
  const i = SLOT_ORDER.indexOf(slot);
  if (i >= 0) return i;
  return 100 + Number(slot.split("_")[1] ?? 0);
}
function slotLabel(slot: string): string {
  return SLOT_LABEL[slot] ?? (slot.startsWith("accessory") ? "Accessory" : slot.replace(/_/g, " "));
}
function pad2(n: number): string {
  return String(n).padStart(2, "0");
}
function clock(hour: number): string {
  return `${pad2(hour)}:00`;
}
function deg(c: number): string {
  return `${Math.round(c)}°`;
}
function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** Garment colours are data, so the swatch is derived from the stored hue. */
const PALE = /white|cream|ivory|oat|sand|stone|beige|light|pale|khaki/i;
const DEEP = /black|charcoal|navy|indigo|dark|slate|forest|burgundy/i;
function swatch(c: GarmentColor): string {
  if (c.hue == null) return "var(--rule-strong)";
  const light = PALE.test(c.name) ? 78 : DEEP.test(c.name) ? 27 : 47;
  return `hsl(${c.hue} ${c.neutral ? 8 : 38}% ${light}%)`;
}

/** Consecutive items sharing a key, so "07:00–09:00" is one row, not three. */
function runsOf<T>(items: T[], key: (x: T) => string): { key: string; items: T[] }[] {
  const out: { key: string; items: T[] }[] = [];
  for (const item of items) {
    const k = key(item);
    const last = out[out.length - 1];
    if (last && last.key === k) last.items.push(item);
    else out.push({ key: k, items: [item] });
  }
  return out;
}

/**
 * The dresscast API returns {error:{code,message,detail}}; the shared client
 * only understands FastAPI's {detail:…}, so an untranslated error would reach
 * the screen as "[object Object]". Restore it before Async renders it.
 */
function decode<T>(q: QueryState<T>): QueryState<T> {
  const err = q.error;
  if (!err) return q;
  const body = err.detail;
  const inner =
    body && typeof body === "object"
      ? (body as { error?: { code?: string; message?: string; detail?: unknown } }).error
      : undefined;
  if (!inner?.code) return q;
  return { ...q, error: new ApiError(err.status, inner.code, inner.message ?? err.message, inner.detail) };
}

/* ------------------------------------------------------------------ pieces */

function Swatches({ colors }: { colors: GarmentColor[] }) {
  return (
    <span className="dc-swatches">
      {colors.map((c) => (
        <span
          key={c.name}
          className={`dc-swatch${c.role === "accent" ? " accent" : ""}`}
          style={{ background: swatch(c) }}
          title={`${c.name}${c.neutral ? " (neutral)" : ""} · ${c.role}`}
        />
      ))}
    </span>
  );
}

function OutfitPiece({
  slot,
  garment,
  garmentId,
  trigger,
}: {
  slot: string;
  garment: Garment | undefined;
  garmentId: string;
  trigger?: string;
}) {
  if (!garment) {
    return (
      <li className="outfit-item missing">
        <span className="dc-slot">{slotLabel(slot)}</span>
        <span className="dc-piece-name">
          not in the wardrobe list <code className="mono">{garmentId.slice(0, 8)}</code>
        </span>
      </li>
    );
  }
  return (
    <li className="outfit-item">
      <span className="dc-slot">{slotLabel(slot)}</span>
      <span className="dc-piece">
        <span className="dc-piece-name">
          <Swatches colors={garment.colors} />
          {garment.name}
        </span>
        <span className="dc-piece-meta">
          <code className="mono">{garment.category}</code>
          <span className="num">{garment.clo.toFixed(2)} clo</span>
          {garment.waterproofness > 0 ? <Pill tone="ok">waterproof {garment.waterproofness}</Pill> : null}
          {garment.windproofness > 0 ? <Pill>windproof {garment.windproofness}</Pill> : null}
          <span className="num">
            {garment.wears_since_wash}/{garment.wears_before_laundry} wears
          </span>
        </span>
        {trigger ? <span className="dc-piece-why">Taken because {trigger}.</span> : null}
      </span>
    </li>
  );
}

/**
 * The hard part, drawn: for every hour, what the day asks for (the marker) and
 * what you are actually wearing (the bar) — plus what is in your bag, which is
 * the whole point of a layering plan.
 */
function DayStrip({ plan, cloOf }: { plan: HourPlan[]; cloOf: (slot: string) => number }) {
  const carried = plan.map((h) => h.carried_slots.reduce((sum, s) => sum + cloOf(s), 0));
  const ceiling =
    Math.max(...plan.map((h, i) => Math.max(h.target_clo, h.ensemble_clo + carried[i]))) * 1.1 || 1;
  const pct = (v: number) => `${Math.min(100, (v / ceiling) * 100)}%`;

  return (
    <div className="dc-strip-wrap">
      <div
        className="dc-strip"
        role="img"
        aria-label={`Hour by hour from ${clock(plan[0].hour)} to ${clock(plan[plan.length - 1].hour)}: insulation worn against the insulation the hour requires.`}
      >
        {plan.map((h, i) => {
          const shed = h.notes.includes("shed_layer");
          const added = h.notes.includes("add_layer");
          const rainy = h.rain_cover_on || h.notes.includes("rain_cover_required");
          return (
            <div
              className="dc-col"
              key={h.hour}
              title={`${clock(h.hour)} — feels like ${h.feels_c.toFixed(1)}°C, asks for ${h.target_clo.toFixed(2)} clo, wearing ${h.ensemble_clo.toFixed(2)} clo${carried[i] ? `, carrying ${carried[i].toFixed(2)} clo` : ""}${rainy ? `, rain cover required (${Math.round(h.precip_prob * 100)}%)` : ""}`}
            >
              <span
                className={`dc-rain${rainy ? " required" : ""}`}
                style={{ opacity: Math.max(0.05, h.precip_prob) }}
              />
              <span className="dc-bar">
                <span
                  className={`dc-worn${h.in_band ? "" : " off"}`}
                  style={{ height: pct(h.ensemble_clo) }}
                />
                <span className="dc-carried" style={{ height: pct(carried[i]) }} />
                <span className="dc-target" style={{ bottom: pct(h.target_clo) }} />
              </span>
              <span className="dc-change" aria-hidden="true">
                {shed ? "▼" : added ? "▲" : ""}
              </span>
              <span className="dc-hour num">{pad2(h.hour)}</span>
              <span className="dc-feels num">{deg(h.feels_c)}</span>
            </div>
          );
        })}
      </div>
      <ul className="dc-legend">
        <li>
          <span className="dc-key worn" /> worn
        </li>
        <li>
          <span className="dc-key carried" /> carried
        </li>
        <li>
          <span className="dc-key target" /> what the hour asks for
        </li>
        <li>
          <span className="dc-key rain" /> rain cover needed
        </li>
        <li>▼ shed a layer · ▲ put one back on</li>
        <li className="num">scale 0–{ceiling.toFixed(2)} clo</li>
      </ul>
    </div>
  );
}

function PlanSegments({ plan, nameOf }: { plan: HourPlan[]; nameOf: (slot: string) => string }) {
  const runs = runsOf(plan, (h) => h.worn_slots.join("|"));
  return (
    <ol className="dc-segs">
      {runs.map((run) => {
        const first = run.items[0];
        const last = run.items[run.items.length - 1];
        const worn = [...first.worn_slots].sort((a, b) => slotRank(a) - slotRank(b));
        const carrying = [...last.carried_slots].sort((a, b) => slotRank(a) - slotRank(b));
        const rainy = run.items.some((h) => h.rain_cover_on || h.notes.includes("rain_cover_required"));
        const cold = run.items.some((h) => !h.in_band);
        return (
          <li className="dc-seg" key={run.key + first.hour}>
            <div className="dc-seg-when">
              <span className="num">
                {clock(first.hour)}
                {last.hour !== first.hour ? `–${clock(last.hour)}` : ""}
              </span>
              <span className="dc-seg-feels num">
                feels {deg(first.feels_c)}
                {last.feels_c !== first.feels_c ? ` → ${deg(last.feels_c)}` : ""}
              </span>
            </div>
            <div className="dc-seg-body">
              <div className="dc-seg-wear">
                {worn.map((s) => (
                  <Pill key={s}>{nameOf(s)}</Pill>
                ))}
                {rainy ? <Pill tone="warn">rain cover on</Pill> : null}
                {cold ? <Pill tone="warn">outside the comfort band</Pill> : null}
              </div>
              {carrying.length ? (
                <p className="dc-seg-carry">
                  In the bag: {carrying.map((s) => nameOf(s)).join(", ")}
                </p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Why({ outfit }: { outfit: ScoredOutfit }) {
  const s = outfit.scores;
  const parts: [string, number][] = [
    ["Thermal", s.thermal],
    ["Protection", s.protection],
    ["Colour", s.color],
    ["Style", s.style],
    ["Variety", s.variety],
  ];
  return (
    <div className="dc-why">
      <div className="dc-scores">
        {parts.map(([label, value]) => (
          <Meter
            key={label}
            value={value}
            tone={value >= 0.9 ? "ok" : value < 0.6 ? "warn" : undefined}
            label={
              <>
                <span>
                  {label}
                  <span className="dc-weight"> ×{(s.weights[label.toLowerCase().replace("colour", "color")] ?? 0).toFixed(2)}</span>
                </span>
                <span className="num">{value.toFixed(2)}</span>
              </>
            }
          />
        ))}
      </div>

      <ul className="dc-reasons">
        {outfit.reasoning.map((r, i) => (
          <li className="dc-reason" key={i}>
            <Pill tone={CLASS_TONE[r.class] ?? "neutral"}>{CLASS_LABEL[r.class] ?? r.class.replace(/_/g, " ")}</Pill>
            <span>{r.text}</span>
          </li>
        ))}
      </ul>

      {outfit.notes.length ? (
        <ul className="dc-gaps">
          {outfit.notes.map((n, i) => (
            <li key={i}>
              {n.kind === "advisory_gap" ? (
                <>
                  Nothing in the closet covers <strong>{String(n.accessory_class)}</strong> — {String(n.trigger)}.
                </>
              ) : (
                <>
                  {n.kind.replace(/_/g, " ")}:{" "}
                  {Object.entries(n)
                    .filter(([k]) => k !== "kind")
                    .map(([k, v]) => `${k} ${String(v)}`)
                    .join(", ")}
                </>
              )}
            </li>
          ))}
        </ul>
      ) : null}

      {outfit.compromises.length ? (
        <ul className="dc-gaps">
          {outfit.compromises.map((c, i) => (
            <li key={i}>
              Relaxed <code className="mono">{c.rule}</code> — {c.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------- the screen */

function DayView({
  rec,
  wardrobe,
  onWorn,
}: {
  rec: Recommendation;
  wardrobe: Garment[];
  onWorn: () => void;
}) {
  const [rank, setRank] = useState(1);
  const wear = useMutation((recommendationId: string, r: number) =>
    api.post<WearLog>(`/recommendations/${recommendationId}/wear`, { rank: r, date: rec.date }),
  );
  const undo = useMutation((logId: string) => api.del<void>(`/wear/${logId}`));

  const byId = new Map(wardrobe.map((g) => [g.id, g]));
  const outfit = rec.outfits.find((o) => o.rank === rank) ?? rec.outfits[0];
  const triggers = new Map(outfit.accessories.map((a) => [a.garment_id, a.trigger]));
  const bySlot = new Map<string, Garment>();
  for (const item of outfit.items) {
    const g = byId.get(item.garment_id);
    if (g) bySlot.set(item.slot, g);
  }
  const pieces = [...outfit.items].sort((a, b) => slotRank(a.slot) - slotRank(b.slot));
  const nameOf = (slot: string) => bySlot.get(slot)?.name ?? slotLabel(slot);
  const cloOf = (slot: string) => bySlot.get(slot)?.clo ?? 0;

  return (
    <>
      {rec.compromises.length ? (
        <ul className="dc-gaps">
          {rec.compromises.map((c, i) => (
            <li key={i}>
              To fill the day at all the engine relaxed <code className="mono">{c.rule}</code> — {c.detail}
            </li>
          ))}
        </ul>
      ) : null}

      <Tabs
        tabs={rec.outfits.map((o) => ({
          id: String(o.rank),
          label: (
            <>
              #{o.rank} <span className="num">{o.score_total.toFixed(2)}</span>
            </>
          ),
        }))}
        active={String(rank)}
        onChange={(id) => setRank(Number(id))}
      />

      <Split>
        <ul className="outfit">
          {pieces.map((item) => (
            <OutfitPiece
              key={item.slot}
              slot={item.slot}
              garmentId={item.garment_id}
              garment={byId.get(item.garment_id)}
              trigger={triggers.get(item.garment_id)}
            />
          ))}
        </ul>
        <Why outfit={outfit} />
      </Split>

      <section className="dc-plan">
        <h3 className="dc-h3">The plan, hour by hour</h3>
        <p className="dc-plan-lede">
          One outfit cannot fit a {deg(Math.min(...outfit.hour_plan.map((h) => h.feels_c)))}→
          {deg(Math.max(...outfit.hour_plan.map((h) => h.feels_c)))} day. This one is worn in stages —
          the bar is what you have on, the marker is what the hour actually demands.
        </p>
        <DayStrip plan={outfit.hour_plan} cloOf={cloOf} />
        <PlanSegments plan={outfit.hour_plan} nameOf={nameOf} />
      </section>

      <div className="dc-commit">
        {wear.data ? (
          <>
            <p className="dc-logged">
              Logged for {wear.data.date}. Laundry counters advanced, and tomorrow&apos;s
              recommendation will avoid repeating it.
            </p>
            <Button
              size="sm"
              pending={undo.pending}
              onClick={() => {
                const id = wear.data?.id;
                if (!id) return;
                void undo.run(id).then(() => {
                  wear.reset();
                  onWorn();
                });
              }}
            >
              Undo
            </Button>
          </>
        ) : (
          <Button
            variant="primary"
            pending={wear.pending}
            onClick={() => void wear.run(rec.id, outfit.rank).then(onWorn)}
          >
            I wore outfit #{outfit.rank}
          </Button>
        )}
        <ErrorNote error={wear.error ?? undo.error} />
      </div>
    </>
  );
}

export default function DressCast() {
  const [date, setDate] = useState(todayISO());
  const [occasion, setOccasion] = useState("work");
  const [met, setMet] = useState(1.6);
  const [nonce, setNonce] = useState(0);

  const wardrobe = decode(useQuery(() => api.get<Garment[]>("/garments"), [nonce]));
  const brief = decode(useQuery(() => api.get<DayBrief>("/brief", { date, met }), [date, met]));
  const rec = decode(
    useQuery(
      () => api.post<Recommendation>("/recommendations", { date, occasion, met, k: 3 }),
      [date, occasion, met, nonce],
    ),
  );

  const wash = useMutation(() => api.post<unknown>("/laundry", { all_dirty: true }));
  const dirty = (wardrobe.data ?? []).filter((g) => g.status !== "clean");
  const reload = () => setNonce((n) => n + 1);

  const columns: Column<Garment>[] = [
    {
      key: "name",
      header: "Garment",
      render: (g) => (
        <span className="dc-piece-name">
          <Swatches colors={g.colors} />
          {g.name}
        </span>
      ),
    },
    { key: "role", header: "Layer", render: (g) => slotLabel(g.layer_role) },
    { key: "cat", header: "Category", render: (g) => <code className="mono">{g.category}</code> },
    { key: "clo", header: "Clo", numeric: true, render: (g) => g.clo.toFixed(2) },
    {
      key: "occ",
      header: "Occasions",
      render: (g) => <span className="dc-occ">{g.occasions.join(", ")}</span>,
    },
    {
      key: "laundry",
      header: "Laundry",
      width: "9rem",
      render: (g) => (
        <Meter
          value={g.wears_since_wash}
          max={Math.max(1, Math.min(g.wears_before_laundry, 30))}
          tone={g.status === "clean" ? undefined : "bad"}
          label={
            <>
              <span>{g.status}</span>
              <span className="num">
                {g.wears_since_wash}/{g.wears_before_laundry}
              </span>
            </>
          }
        />
      ),
    },
  ];

  return (
    <Page
      title="DressCast"
      lede="Today's weather, your actual closet, and a layering plan that survives a cold morning turning into a warm afternoon — with the reasoning behind every garment."
      actions={
        <div className="dc-controls">
          <Field label="Date">
            <Input
              type="date"
              value={date}
              onChange={(e) => {
                if (e.target.value) setDate(e.target.value);
              }}
            />
          </Field>
          <Field label="Occasion">
            <Select value={occasion} onChange={(e) => setOccasion(e.target.value)}>
              {OCCASIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Activity" hint="met rate — how hard your body is working">
            <Select value={met} onChange={(e) => setMet(Number(e.target.value))}>
              {ACTIVITY.map((a) => (
                <option key={a.met} value={a.met}>
                  {a.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      }
    >
      <Panel
        title="What today demands"
        hint="Computed from the hourly forecast alone — this answer exists before the closet does."
      >
        <Async query={brief}>
          {(b) => {
            const rain = b.hours.filter((h) => h.rain_required);
            const peakPop = Math.max(...b.hours.map((h) => h.precip_prob));
            const peakWind = Math.max(...b.hours.map((h) => h.wind_kmh));
            const peakUv = Math.max(...b.hours.map((h) => h.uv_index));
            const archetypes = runsOf(b.hours, (h) => h.archetype);
            return (
              <>
                <StatRow>
                  <Stat
                    label="Feels like"
                    value={`${deg(b.bare_feels_min)} → ${deg(b.bare_feels_max)}`}
                    sub={`swing of ${(b.bare_feels_max - b.bare_feels_min).toFixed(1)}°C`}
                  />
                  <Stat
                    label="Asks for"
                    value={`${b.required_clo_min.toFixed(2)}–${b.required_clo_max.toFixed(2)}`}
                    sub={`clo, at met ${b.met}`}
                  />
                  <Stat
                    label="Rain"
                    value={`${Math.round(peakPop * 100)}%`}
                    tone={rain.length ? "warn" : undefined}
                    sub={
                      rain.length
                        ? `cover needed ${clock(rain[0].hour)}–${clock(rain[rain.length - 1].hour)}`
                        : "no cover needed"
                    }
                  />
                  <Stat label="Wind" value={`${Math.round(peakWind)}`} sub="km/h peak" />
                  <Stat label="UV peak" value={peakUv.toFixed(1)} tone={peakUv >= 6 ? "warn" : undefined} />
                  <Stat
                    label="Wear window"
                    value={`${pad2(b.wear_window[0])}–${pad2(b.wear_window[1])}`}
                    sub={`${b.hours.length} hours`}
                  />
                </StatRow>

                <div className="dc-arche">
                  {archetypes.map((run) => (
                    <span className="dc-arche-run" key={run.key + run.items[0].hour}>
                      <span className="num">
                        {pad2(run.items[0].hour)}–{pad2(run.items[run.items.length - 1].hour)}
                      </span>
                      <strong>{run.key}</strong>
                    </span>
                  ))}
                </div>

                {b.advisories.length ? (
                  <ul className="dc-advisories">
                    {b.advisories.map((a) => (
                      <li key={a.kind}>
                        <Pill tone="warn">{CLASS_LABEL[a.kind] ?? a.kind.replace(/_/g, " ")}</Pill>
                        <span>{a.text}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </>
            );
          }}
        </Async>
      </Panel>

      <Panel
        title="Today's outfit"
        hint="Ranked complete outfits — every slot filled, only clean garments, nothing you wore yesterday."
      >
        <Async
          query={rec}
          emptyWhen={(r) => r.outfits.length === 0}
          empty={{
            title: "No complete outfit for this day",
            detail: "The engine could not fill every required slot from what is clean.",
          }}
        >
          {(r) => (
            <Async
              query={wardrobe}
              emptyWhen={(g) => g.length === 0}
              empty={{ title: "The closet is empty", detail: "Add garments before asking for an outfit." }}
            >
              {(gs) => <DayView rec={r} wardrobe={gs} onWorn={reload} />}
            </Async>
          )}
        </Async>
      </Panel>

      <Panel
        title="Your closet"
        hint="Dirty garments are excluded from every recommendation until they are washed."
        actions={
          <>
            <span className="dc-dirty num">{dirty.length} dirty</span>
            <Button
              size="sm"
              pending={wash.pending}
              disabled={dirty.length === 0}
              onClick={() => void wash.run().then(reload)}
            >
              Laundry day
            </Button>
          </>
        }
        padded={false}
      >
        <Async
          query={wardrobe}
          emptyWhen={(g) => g.length === 0}
          empty={{ title: "No garments yet", detail: "Catalogue the closet to get recommendations." }}
        >
          {(gs) => (
            <Table
              columns={columns}
              rows={[...gs].sort((a, b) => slotRank(a.layer_role) - slotRank(b.layer_role))}
              rowKey={(g) => g.id}
              caption="Every garment, its warmth and its laundry state"
            />
          )}
        </Async>
      </Panel>
      <ErrorNote error={wash.error} />
    </Page>
  );
}
