/**
 * GrailTrader — designer resale priced like a market.
 *
 * The product's hard part is not building an index; it is refusing to fool
 * yourself with one built from three listings. So this screen never shows a
 * price without the sample it rests on: every valuation carries the number of
 * clean sold comps in its stratum that week, how many the fence threw out, and
 * how stale the series is against the week you are standing in. A piece whose
 * stratum has no index is shown as unvalued rather than guessed at.
 *
 * Advice is rendered the same way. The engine emits rationale codes
 * (`driver:event:<id>`, `prior:<key>`, `mod:z=…`), so each call is unpacked
 * back into the events that drove it, the impact prior those events triggered,
 * and the modifiers that scaled the confidence — plus the exact frame-checked
 * text that was persisted, footer and all.
 */

import { useMemo, useState } from "react";
import { client } from "../lib/api";
import { useQuery } from "../lib/hooks";
import type { QueryState } from "../lib/hooks";
import {
  Async, Button, Facts, Field, Meter, Page, Panel, Pill, Select, Split, Stat, StatRow, State, Table,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./grailtrader.css";

const api = client("grailtrader");

/* The engine's own constants live in data/advisor_config.json and the API serves
   no config endpoint, so they are mirrored here — and the screen prints the
   config version it is assuming, so a drift is visible rather than silent. */
const MIN_SALES = 5; // index.min_sales — a week below this is never published
const THIN_SALES = 10; // twice the floor: what this screen is willing to call thin
const STALE_MAX_WEEKS = 8; // index.stale_max_weeks — past this the index is unusable
const WINDOW_WEEKS = 26;

/* ------------------------------------------------------------------- types */

type Health = {
  status: string;
  config_version: string;
  brands: number;
  listings: number;
  index_points: number;
  events: number;
  garments: number;
};

type Garment = {
  id: string;
  label: string;
  brand_id: string;
  era_id: string;
  category: string;
  condition: string;
  anchor_condition: string;
  size: string | null;
  status: "owned" | "watching" | "sold_archived";
  acquisition_price: number | null;
  acquired_on: string | null;
  reference_price: number | null;
  reference_date: string | null;
  added_at: string;
  notes: string;
};

type ValuationMethod = "repeat_sales" | "comp_based" | "unavailable";

type Valuation = {
  garment_id: string;
  as_of_week: string;
  valuation_method: ValuationMethod;
  fair_value: number | null;
  reason: string | null;
  stratum_id: string | null;
  level_usd: number | null;
  anchor_price: number | null;
  unrealized_gain: number | null;
};

type PortfolioValue = {
  as_of_week: string;
  total_fair_value: number;
  total_anchor_price: number;
  unavailable: number;
  garments: Valuation[];
};

type IndexPoint = {
  stratum_id: string;
  week: string;
  level_usd: number | null;
  index_value: number;
  n_sales: number;
  n_excluded: number;
  built_as_of: string;
};

type Listing = {
  id: string;
  external_id: string;
  category: string;
  condition: string;
  platform_label: string;
  sold_at: string | null;
  sold_price: number | null;
};

type IndexSeries = {
  stratum_id: string;
  points: IndexPoint[];
  excluded?: Record<string, Listing[]>;
};

type AdviceAction = "buy" | "sell" | "hold";

type Advice = {
  id: string;
  garment_id: string;
  as_of_week: string;
  stratum_id: string;
  action: AdviceAction;
  is_candidate: boolean;
  horizon_weeks: number;
  expected_return: number | null;
  confidence: number | null;
  fair_value: number | null;
  fair_value_method: ValuationMethod;
  rationale_codes: string[];
  rendered_text: string;
  frame_checked: boolean;
  config_version: string;
  inputs_hash: string;
  created_as_of: string;
};

type AdviceBatch = { as_of_week: string; counts: Record<string, number>; advice: Advice[] };

type FashionEvent = {
  id: string;
  event_type: string;
  brand_id: string;
  era_id: string | null;
  attributes: Record<string, unknown>;
  occurred_on: string;
  source: string;
  source_refs: string[];
  status: string;
  corroboration: number;
  notes: string;
};

type ImpactPrior = {
  key: string;
  target_stratum: string;
  direction: string;
  permanent_pct: number;
  transient_pct: number;
  half_life_weeks: number;
  base_conf: number;
  rationale?: string;
  source_note?: string;
  retirement_age_weeks?: number;
};

type EventDetail = {
  event: FashionEvent;
  targets: { kind: string; stratum: string }[];
  priors: ImpactPrior[];
  retirement_age_weeks: number;
  source_refs: string[];
  corroboration: number;
};

type Desk = {
  health: Health;
  value: PortfolioValue;
  garments: Garment[];
  advice: AdviceBatch;
  events: FashionEvent[];
  series: Record<string, IndexSeries>;
};

/* --------------------------------------------------------------- formatting */

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function money(n: number | null | undefined): string {
  return n == null ? "—" : USD.format(n);
}

function signedMoney(n: number | null | undefined): string {
  if (n == null) return "—";
  return n > 0 ? `+${USD.format(n)}` : USD.format(n);
}

function pct(n: number | null | undefined, digits = 1): string {
  if (n == null) return "—";
  return `${n > 0 ? "+" : ""}${(n * 100).toFixed(digits)}%`;
}

function gainTone(n: number | null | undefined): "ok" | "bad" | undefined {
  if (n == null || n === 0) return undefined;
  return n > 0 ? "ok" : "bad";
}

function fmtDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

function weeksBetween(from: string, to: string): number | null {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((b - a) / (7 * 86_400_000));
}

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function words(id: string): string {
  return id.replace(/[_:]/g, " ").replace(/-/g, " ");
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${Math.abs(n) === 1 ? one : many}`;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/* ------------------------------------------------------- rationale decoding */

type Codes = { drivers: string[]; priors: string[]; mods: [string, string][]; flags: string[] };

/** The engine's audit trail is a list of codes; this turns it back into prose. */
function decode(codes: string[]): Codes {
  const drivers: string[] = [];
  const priors: string[] = [];
  const mods: [string, string][] = [];
  const flags: string[] = [];
  for (const code of codes) {
    if (code.startsWith("driver:event:")) drivers.push(code.slice("driver:event:".length));
    else if (code.startsWith("prior:")) priors.push(code.slice("prior:".length));
    else if (code.startsWith("mod:")) {
      const body = code.slice("mod:".length);
      const eq = body.indexOf("=");
      if (eq > 0) mods.push([body.slice(0, eq), body.slice(eq + 1)]);
      else flags.push(code);
    } else flags.push(code);
  }
  return { drivers, priors, mods, flags };
}

const MOD_LABEL: Record<string, string> = {
  lambda: "Scope weight λ",
  z: "Evidence strength z",
  conf_event: "Event confidence",
  q_index: "Index quality q",
  src: "Source factor",
};

const MOD_NOTE: Record<string, string> = {
  lambda: "how squarely the event's scope lands on this piece's stratum",
  z: "the modeled move measured against this stratum's own weekly noise, at this horizon",
  conf_event: "reliability of the source times its corroboration count",
  q_index: "staleness discount on the index — 1.0 fresh, 0.8 up to 2 weeks old, 0.5 up to 8",
  src: "news, social or manual entry are not weighted equally",
};

const FLAG_NOTE: Record<string, string> = {
  "hold:no_active_events":
    "No confirmed event is currently active on this piece's brand, era or category — so there is nothing to trade on, and the engine says so instead of inventing a reason.",
  "hold:below_threshold":
    "An event is active, but the modeled move does not clear the action threshold — which the config pins equal to the round-trip fee the advice also quotes, so no trade smaller than its own friction can ever be recommended.",
};

const ACTION_TONE: Record<AdviceAction, "ok" | "bad" | "neutral"> = {
  buy: "ok",
  sell: "bad",
  hold: "neutral",
};

const METHOD_TONE: Record<ValuationMethod, "ok" | "warn" | "neutral"> = {
  repeat_sales: "ok",
  comp_based: "neutral",
  unavailable: "warn",
};

const METHOD_NOTE: Record<ValuationMethod, string> = {
  repeat_sales: "index moved from the price you paid",
  comp_based: "typical comp for the stratum, condition-adjusted",
  unavailable: "no usable index — deliberately unpriced",
};

const REASON_NOTE: Record<string, string> = {
  no_index: "no index exists for this stratum yet",
  stale_index: `the newest index week is more than ${STALE_MAX_WEEKS} weeks old`,
  no_index_at_anchor: "the index does not reach back to the week you bought it",
};

/** Pull one labelled line out of the engine's rendered advice, or nothing. */
function lineAfter(text: string, marker: string): string | null {
  const at = text.indexOf(marker);
  if (at < 0) return null;
  const end = text.indexOf("\n", at);
  return text.slice(at + marker.length, end < 0 ? undefined : end).trim() || null;
}

const DISCLAIMER =
  "GrailTrader models a collectibles market — unregulated, illiquid, with authenticity risk. This is information about a model, not investment advice.";

/* ------------------------------------------------------------ sample quality */

type Sample = {
  point: IndexPoint | undefined;
  weeks: number;
  medianSales: number | null;
  /** Per-week exclusions; a listing inside the 4-week window is counted each week. */
  fenced: number;
  /** Distinct listings the fence removed — the number a human means by "how many". */
  fencedListings: number;
  staleWeeks: number | null;
  tone: "ok" | "warn" | "bad";
  label: string;
};

function fencedListingsOf(series: IndexSeries): Listing[] {
  const seen = new Map<string, Listing>();
  for (const listings of Object.values(series.excluded ?? {})) {
    for (const listing of listings) if (!seen.has(listing.id)) seen.set(listing.id, listing);
  }
  return [...seen.values()];
}

function sampleOf(series: IndexSeries | undefined, asOfWeek: string): Sample | null {
  if (!series || series.points.length === 0) return null;
  const point = series.points[series.points.length - 1];
  const staleWeeks = weeksBetween(point.week, asOfWeek);
  const medianSales = median(series.points.map((p) => p.n_sales));
  const fenced = series.points.reduce((sum, p) => sum + p.n_excluded, 0);
  const fencedListings = fencedListingsOf(series).length;
  let tone: "ok" | "warn" | "bad" = "ok";
  let label = "well sampled";
  if (point.n_sales < MIN_SALES) {
    tone = "bad";
    label = "below the floor";
  } else if (point.n_sales < THIN_SALES) {
    tone = "warn";
    label = "thin";
  }
  if (staleWeeks != null && staleWeeks > STALE_MAX_WEEKS) {
    tone = "bad";
    label = "stale";
  } else if (staleWeeks != null && staleWeeks > 0 && tone === "ok") {
    tone = "warn";
    label = `${staleWeeks} wk stale`;
  }
  return { point, weeks: series.points.length, medianSales, fenced, fencedListings, staleWeeks, tone, label };
}

/* ----------------------------------------------------------------- sparkline */

function Spark({ points, title }: { points: IndexPoint[]; title: string }) {
  if (points.length < 2) {
    return (
      <p className="gt-spark-none">
        {points.length === 0 ? "No index weeks in this window." : "One index week only — too few points to draw a line."}
      </p>
    );
  }
  const W = 640;
  const PAD = 8;
  const LINE_H = 96;
  const BAR_TOP = 112;
  const BAR_H = 34;
  const H = BAR_TOP + BAR_H;

  const values = points.map((p) => p.index_value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (points.length - 1);
  const y = (v: number) => 6 + (LINE_H - 12) * (1 - (v - lo) / span);

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.index_value).toFixed(1)}`)
    .join(" ");
  const area = `${path} L${x(points.length - 1).toFixed(1)},${LINE_H} L${x(0).toFixed(1)},${LINE_H} Z`;

  const maxSales = Math.max(...points.map((p) => p.n_sales), MIN_SALES * 2);
  const barW = Math.max(2, (W - 2 * PAD) / points.length - 2);
  const floorY = BAR_TOP + BAR_H - (MIN_SALES / maxSales) * BAR_H;

  const first = points[0];
  const last = points[points.length - 1];
  const medianSales = median(points.map((p) => p.n_sales)) ?? 0;

  return (
    <svg className="gt-spark" viewBox={`0 0 ${W} ${H}`} role="img">
      <title>
        {`${title}: index ${first.index_value.toFixed(1)} on ${first.week} to ${last.index_value.toFixed(1)} on ${last.week}, ` +
          `across ${points.length} weekly points with a median of ${medianSales} clean sales per week ` +
          `(the engine will not publish a week under ${MIN_SALES}).`}
      </title>
      <path className="gt-spark-fill" d={area} />
      <path className="gt-spark-line" d={path} />
      <circle className="gt-spark-dot" cx={x(points.length - 1)} cy={y(last.index_value)} r={4} />
      <rect
        className="gt-spark-floorzone"
        x={PAD}
        y={floorY}
        width={W - 2 * PAD}
        height={BAR_TOP + BAR_H - floorY}
      />
      {points.map((p, i) => {
        const h = Math.max(1, (p.n_sales / maxSales) * BAR_H);
        return (
          <rect
            key={p.week}
            className={p.n_excluded > 0 ? "gt-spark-bar fenced" : "gt-spark-bar"}
            x={x(i) - barW / 2}
            y={BAR_TOP + BAR_H - h}
            width={barW}
            height={h}
          />
        );
      })}
      <line className="gt-spark-floor" x1={PAD} x2={W - PAD} y1={floorY} y2={floorY} />
    </svg>
  );
}

/* ----------------------------------------------------------- evidence blocks */

function DriverCard({ detail, asOfWeek }: { detail: EventDetail; asOfWeek: string }) {
  const e = detail.event;
  const age = weeksBetween(e.occurred_on, asOfWeek);
  const retired = age != null && age > detail.retirement_age_weeks;
  const attrs = Object.entries(e.attributes).map(([k, v]) => `${words(k)} ${words(String(v))}`);

  return (
    <article className="gt-driver">
      <header className="gt-driver-head">
        <h4>{words(e.event_type)}</h4>
        <Pill tone={e.status === "confirmed" ? "ok" : "warn"}>{e.status}</Pill>
        <Pill>{e.source}</Pill>
        {retired ? <Pill tone="warn">past retirement age</Pill> : null}
      </header>

      <p className="gt-driver-scope">
        <strong>{words(e.brand_id)}</strong>
        {e.era_id ? <span className="mono"> {e.era_id}</span> : null}
        {attrs.length ? <span className="gt-driver-attrs"> · {attrs.join(" · ")}</span> : null}
      </p>

      <p className="gt-driver-when">
        {fmtDate(e.occurred_on)}
        {age == null
          ? null
          : age === 0
            ? " · the same week as this call"
            : age > 0
              ? ` · ${plural(age, "week")} before this call`
              : ` · ${plural(-age, "week")} after this call`}
        {` · corroborated by ${plural(detail.corroboration, "source")}`}
        {` · retires at ${detail.retirement_age_weeks} weeks`}
      </p>

      {e.notes ? <blockquote className="gt-driver-note">{e.notes}</blockquote> : null}

      <p className="gt-driver-targets">
        Scope:{" "}
        {detail.targets.map((t) => (
          <span className="mono gt-target" key={`${t.kind}:${t.stratum}`}>
            {t.stratum}
          </span>
        ))}
      </p>

      {detail.priors.map((prior) => (
        <div className="gt-prior" key={prior.key}>
          <p className="gt-prior-head">
            <code className="mono">{prior.key}</code>
            <span className={`gt-prior-dir tone-${prior.direction === "bearish" ? "bad" : "ok"}`}>
              {prior.direction}
            </span>
          </p>
          <p className="gt-prior-nums num">
            permanent {pct(prior.permanent_pct)} · transient {pct(prior.transient_pct)} · half-life{" "}
            {prior.half_life_weeks} wks · base confidence {prior.base_conf.toFixed(2)}
          </p>
          {prior.rationale ? <p className="gt-prior-why">{prior.rationale}</p> : null}
          {prior.source_note ? <p className="gt-prior-source">{prior.source_note}</p> : null}
        </div>
      ))}

      {detail.source_refs.length ? (
        <p className="gt-driver-refs">
          {detail.source_refs.map((ref) =>
            ref.startsWith("http") ? (
              <a key={ref} href={ref} target="_blank" rel="noreferrer" className="mono">
                {ref.replace(/^https?:\/\//, "")}
              </a>
            ) : (
              <span key={ref} className="mono">
                {ref}
              </span>
            ),
          )}
        </p>
      ) : null}
    </article>
  );
}

function CallPanel({
  advice,
  garment,
  sample,
  drivers,
  asOfWeek,
}: {
  advice: Advice | undefined;
  garment: Garment | undefined;
  sample: Sample | null;
  drivers: QueryState<EventDetail[]>;
  asOfWeek: string;
}) {
  if (!advice) {
    return (
      <State
        kind="empty"
        title="No call for this piece"
        detail="The advisor returned no row for it in this week's batch — pick another holding, or move the as-of week."
      />
    );
  }
  const codes = decode(advice.rationale_codes);
  const falsifier = lineAfter(advice.rendered_text, "Falsifier:");
  const fees = lineAfter(advice.rendered_text, "Fees & liquidity:");
  const confidence = advice.confidence;
  // The compliance footer belongs on screen, not folded away inside the disclosure.
  const footerAt = advice.rendered_text.lastIndexOf("GrailTrader models");
  const footer = footerAt >= 0 ? advice.rendered_text.slice(footerAt).trim() : DISCLAIMER;

  return (
    <div className="gt-call">
      <header className="gt-call-head">
        <Pill tone={ACTION_TONE[advice.action]}>{advice.action}</Pill>
        <h3>{garment?.label ?? advice.garment_id}</h3>
        {advice.is_candidate ? <Pill tone="accent">actionable</Pill> : null}
      </header>

      <div className="gt-call-metrics">
        <div className="gt-metric">
          <span className="gt-metric-label">Modeled move</span>
          <span className={`gt-metric-value num${advice.expected_return ? ` tone-${gainTone(advice.expected_return)}` : ""}`}>
            {advice.expected_return == null ? "none modeled" : pct(advice.expected_return)}
          </span>
          <span className="gt-metric-sub">over {advice.horizon_weeks} weeks</span>
        </div>
        <div className="gt-metric">
          <span className="gt-metric-label">Fair value</span>
          <span className="gt-metric-value num">{money(advice.fair_value)}</span>
          <span className="gt-metric-sub">{METHOD_NOTE[advice.fair_value_method]}</span>
        </div>
        <div className="gt-metric">
          <span className="gt-metric-label">Sample behind it</span>
          <span className={`gt-metric-value num tone-${sample?.tone ?? "warn"}`}>
            {sample?.point ? `${sample.point.n_sales}/wk` : "none"}
          </span>
          <span className="gt-metric-sub">
            {sample?.point
              ? `${sample.label} · ${sample.fencedListings} comps fenced out over ${sample.weeks} wks`
              : "no index for this stratum"}
          </span>
        </div>
      </div>

      {confidence == null ? (
        <p className="gt-conf-none">
          No confidence is quoted, because nothing is being predicted — the engine only scores calls it
          actually models.
        </p>
      ) : (
        <Meter
          value={confidence}
          tone={confidence >= 0.62 ? "ok" : confidence >= 0.45 ? "warn" : "bad"}
          label={
            <>
              <span>Confidence in this call</span>
              <span className="num">{confidence.toFixed(2)}</span>
            </>
          }
        />
      )}

      <section className="gt-why">
        <h4 className="gt-sub">Why — the evidence, not a summary of it</h4>
        {codes.flags.map((flag) => (
          <p className="gt-flag" key={flag}>
            {FLAG_NOTE[flag] ?? <code className="mono">{flag}</code>}
          </p>
        ))}
        {codes.drivers.length === 0 ? null : (
          <Async
            query={drivers}
            emptyWhen={(list) => list.length === 0}
            empty={{ title: "Driving events could not be resolved", detail: "The advice names them, but the event records did not come back." }}
          >
            {(list) => (
              <div className="gt-drivers">
                {list.map((d) => (
                  <DriverCard detail={d} asOfWeek={asOfWeek} key={d.event.id} />
                ))}
              </div>
            )}
          </Async>
        )}
      </section>

      {codes.mods.length ? (
        <section className="gt-mods">
          <h4 className="gt-sub">How that confidence was built</h4>
          <ul className="gt-mod-list">
            {codes.mods.map(([key, raw]) => {
              const [value, scope] = raw.split("@");
              return (
                <li className="gt-mod" key={key}>
                  <span className="gt-mod-label">{MOD_LABEL[key] ?? key}</span>
                  <span className="gt-mod-value num">
                    {value}
                    {scope ? <span className="gt-mod-scope mono"> @{scope}</span> : null}
                  </span>
                  <span className="gt-mod-note">{MOD_NOTE[key] ?? "engine modifier"}</span>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      <Facts
        items={[
          ["Falsifier", falsifier ?? "not stated in the rendered advice"],
          ["Fees & liquidity", fees ?? "not stated in the rendered advice"],
          [
            "Frame check",
            advice.frame_checked ? (
              <Pill tone="ok">passed before storage</Pill>
            ) : (
              <Pill tone="bad">not checked</Pill>
            ),
          ],
          [
            "Provenance",
            <span className="mono">
              {advice.id.slice(0, 8)} · inputs {advice.inputs_hash.slice(0, 8)} · config {advice.config_version}
            </span>,
          ],
        ]}
      />

      <details className="gt-rendered">
        <summary>Read the text that was frame-checked and stored</summary>
        <pre>{advice.rendered_text}</pre>
      </details>

      <p className="gt-disclaimer">{footer}</p>
    </div>
  );
}

function IndexPanel({ series, sample, asOfWeek }: { series: IndexSeries | undefined; sample: Sample | null; asOfWeek: string }) {
  if (!series || !sample || !sample.point) {
    return (
      <State
        kind="empty"
        title="No index stands behind this piece"
        detail="Nothing in this stratum has sold in the window, so the engine leaves it unpriced instead of borrowing a number from somewhere else."
      />
    );
  }
  const points = series.points;
  const first = points[0];
  const last = sample.point;
  const change = first.index_value ? last.index_value / first.index_value - 1 : null;
  const recent = [...points].slice(-6).reverse();

  const fenced = fencedListingsOf(series);

  const columns: Column<IndexPoint>[] = [
    { key: "week", header: "Week", render: (p) => <span className="mono">{p.week}</span> },
    { key: "index", header: "Index", numeric: true, render: (p) => p.index_value.toFixed(2) },
    { key: "level", header: "Level", numeric: true, render: (p) => money(p.level_usd) },
    {
      key: "n",
      header: "Clean sales",
      numeric: true,
      render: (p) => (
        <span className={p.n_sales < THIN_SALES ? "tone-warn" : undefined}>{p.n_sales}</span>
      ),
    },
    {
      key: "x",
      header: "Fenced out",
      numeric: true,
      render: (p) => (p.n_excluded ? <span className="tone-warn">{p.n_excluded}</span> : "0"),
    },
  ];

  return (
    <div className="gt-index">
      <div className="gt-index-head">
        <code className="mono gt-stratum">{series.stratum_id}</code>
        <span className={`gt-index-change num tone-${gainTone(change) ?? "warn"}`}>
          {pct(change)} <span className="gt-index-change-sub">over {points.length} weeks</span>
        </span>
      </div>

      <Spark points={points} title={series.stratum_id} />
      <p className="gt-spark-key">
        Line: weekly index level. Bars: clean sold comps behind each week, with the dashed rule at the
        engine's floor of {MIN_SALES}; a highlighted bar is a week where the fence removed something.
      </p>

      <Facts
        items={[
          ["Latest level", <span className="num">{money(last.level_usd)}</span>],
          [
            "Sample",
            <span className="num">
              {last.n_sales} clean sales that week · median {sample.medianSales ?? "—"}/wk over {sample.weeks} weeks
            </span>,
          ],
          [
            "Fence",
            <span className="num">
              {fenced.length} price-implausible listing{fenced.length === 1 ? "" : "s"} removed
              {sample.fenced !== fenced.length ? ` (${sample.fenced} listing-weeks — the window overlaps)` : null}
            </span>,
          ],
          [
            "Freshness",
            <span className="num">
              newest week {last.week}
              {sample.staleWeeks != null && sample.staleWeeks > 0
                ? ` — ${sample.staleWeeks} week${sample.staleWeeks === 1 ? "" : "s"} behind ${asOfWeek}`
                : ` — current as of ${asOfWeek}`}
            </span>,
          ],
          ["Built as of", <span className="mono">{last.built_as_of}</span>],
        ]}
      />

      <Table columns={columns} rows={recent} rowKey={(p) => p.week} caption="Recent index weeks" />

      <details className="gt-fenced">
        <summary>
          What the fence removed ({fenced.length} listing{fenced.length === 1 ? "" : "s"})
        </summary>
        {fenced.length === 0 ? (
          <p className="gt-fenced-none">Nothing was fenced out of this window.</p>
        ) : (
          <>
            <ul className="gt-fenced-list">
              {fenced.slice(0, 8).map((listing) => (
                <li key={listing.id}>
                  <span className="num">{money(listing.sold_price)}</span>
                  <span className="gt-fenced-meta">
                    {listing.condition} · “{listing.platform_label || listing.category}”
                    {listing.sold_at ? ` · sold ${listing.sold_at}` : null}
                  </span>
                  <code className="mono">{listing.external_id}</code>
                </li>
              ))}
            </ul>
            {fenced.length > 8 ? <p className="gt-fenced-more">+{fenced.length - 8} more</p> : null}
          </>
        )}
        <p className="gt-fenced-note">
          The fence removes prices too implausible for the stratum. It cannot tell a fake at half price
          from a genuine grail someone sniped at half price — so these are excluded from the index, not
          judged.
        </p>
      </details>
    </div>
  );
}

/* ------------------------------------------------------------------- screen */

async function loadDesk(asOf: string): Promise<Desk> {
  const at = asOf || undefined;
  const [health, value, portfolio, advice, events] = await Promise.all([
    api.get<Health>("/health"),
    api.get<PortfolioValue>("/portfolio/value", { as_of: at }),
    api.get<{ garments: Garment[] }>("/portfolio"),
    api.post<AdviceBatch>("/advise", at ? { as_of: at } : {}),
    api.get<FashionEvent[]>("/events"),
  ]);

  const strata = [
    ...new Set(value.garments.map((v) => v.stratum_id).filter((s): s is string => Boolean(s))),
  ];
  const series = await Promise.all(
    strata.map((s) =>
      api.get<IndexSeries>(`/index/${s}`, { weeks: WINDOW_WEEKS, to: at, excluded: true }),
    ),
  );

  return {
    health,
    value,
    garments: portfolio.garments,
    advice,
    events,
    series: Object.fromEntries(series.map((s) => [s.stratum_id, s])),
  };
}

export default function GrailTrader() {
  const [asOf, setAsOf] = useState("");
  const [picked, setPicked] = useState<string | null>(null);

  const desk = useQuery(() => loadDesk(asOf), [asOf]);
  const data = desk.data;

  const valuations = useMemo(() => data?.value.garments ?? [], [data]);
  const adviceFor = useMemo(() => {
    const map = new Map<string, Advice>();
    for (const a of data?.advice.advice ?? []) map.set(a.garment_id, a);
    return map;
  }, [data]);

  // Default to something worth looking at: a call the engine was willing to act on.
  const autoId =
    valuations.find((v) => adviceFor.get(v.garment_id)?.is_candidate)?.garment_id ??
    valuations[0]?.garment_id ??
    null;
  const activeId = picked && valuations.some((v) => v.garment_id === picked) ? picked : autoId;

  const activeAdvice = activeId ? adviceFor.get(activeId) : undefined;
  const driverIds = useMemo(
    () => (activeAdvice ? decode(activeAdvice.rationale_codes).drivers : []),
    [activeAdvice],
  );
  const driverKey = driverIds.join("|");
  const drivers = useQuery(
    () => Promise.all(driverIds.map((id) => api.get<EventDetail>(`/events/${id}`))),
    [driverKey],
    { enabled: driverIds.length > 0 },
  );

  return (
    <Page
      title="GrailTrader"
      lede="Designer resale read as a market: a weekly index built only from sold comps, your closet marked against it, and buy / hold / sell calls that name the events behind them. Nothing here is shown without the sample it rests on."
      actions={
        <Button onClick={desk.reload} pending={desk.loading && desk.data !== undefined}>
          Recompute
        </Button>
      }
    >
      <Async query={desk}>
        {(d) => {
          const asOfWeek = d.value.as_of_week || d.advice.as_of_week;
          const priced = d.value.garments.length - d.value.unavailable;
          // The API's anchor total counts pieces it could not value, so differencing the
          // two totals would invent a loss the size of the unpriced piece. Sum the
          // per-piece gains instead — only pieces that actually have both numbers.
          const gain = d.value.garments.reduce((sum, v) => sum + (v.unrealized_gain ?? 0), 0);
          const pricedAnchor = d.value.garments.reduce(
            (sum, v) => sum + (v.unrealized_gain == null ? 0 : v.anchor_price ?? 0),
            0,
          );
          const owned = d.garments.filter((g) => g.status === "owned").length;
          const watching = d.garments.filter((g) => g.status === "watching").length;

          const samples = new Map<string, Sample | null>();
          for (const v of d.value.garments) {
            samples.set(v.garment_id, v.stratum_id ? sampleOf(d.series[v.stratum_id], asOfWeek) : null);
          }
          const sampleSizes = [...samples.values()]
            .map((s) => s?.point?.n_sales)
            .filter((n): n is number => typeof n === "number");
          const fencedTotal = [...samples.values()].reduce((sum, s) => sum + (s?.fencedListings ?? 0), 0);
          const actionable = d.advice.advice.filter((a) => a.is_candidate).length;

          const garmentById = new Map(d.garments.map((g) => [g.id, g] as const));
          const activeValuation = d.value.garments.find((v) => v.garment_id === activeId);
          const activeSample = activeId ? samples.get(activeId) ?? null : null;

          // Weeks worth jumping to: the latest, and the week after each event lands.
          const jumps = [...d.events]
            .sort((a, b) => b.occurred_on.localeCompare(a.occurred_on))
            .map((e) => ({
              value: addDays(e.occurred_on, 7),
              label: `${addDays(e.occurred_on, 7)} — week after ${words(e.event_type)} at ${words(e.brand_id)}`,
            }));

          const columns: Column<Valuation>[] = [
            {
              key: "piece",
              header: "Piece",
              render: (v) => {
                const g = garmentById.get(v.garment_id);
                return (
                  <div className="gt-piece">
                    <span className="gt-piece-label">{g?.label ?? v.garment_id}</span>
                    <span className="gt-piece-sub">
                      {g ? `${g.condition} · ${g.category}` : "unknown garment"}
                      {g?.status === "watching" ? " · watching" : ""}
                    </span>
                  </div>
                );
              },
            },
            {
              key: "stratum",
              header: "Stratum & method",
              render: (v) => (
                <div className="gt-method">
                  <code className="mono">{v.stratum_id ?? "no stratum"}</code>
                  <span className="gt-method-line">
                    <Pill tone={METHOD_TONE[v.valuation_method]}>{words(v.valuation_method)}</Pill>
                    <span className="gt-method-note">
                      {v.reason ? REASON_NOTE[v.reason] ?? words(v.reason) : METHOD_NOTE[v.valuation_method]}
                    </span>
                  </span>
                </div>
              ),
            },
            {
              key: "sample",
              header: "Sample",
              numeric: true,
              render: (v) => {
                const s = samples.get(v.garment_id) ?? null;
                if (!s?.point) return <span className="tone-warn">none</span>;
                return (
                  <div className="gt-sample">
                    <span className={`tone-${s.tone}`}>{s.point.n_sales}/wk</span>
                    <span className="gt-sample-sub">
                      {s.label} · {s.point.n_excluded} fenced
                    </span>
                  </div>
                );
              },
            },
            { key: "anchor", header: "Anchor", numeric: true, render: (v) => money(v.anchor_price) },
            {
              key: "fair",
              header: "Fair value",
              numeric: true,
              render: (v) => (v.fair_value == null ? <span className="tone-warn">unpriced</span> : money(v.fair_value)),
            },
            {
              key: "gain",
              header: "Unrealized",
              numeric: true,
              render: (v) =>
                v.unrealized_gain == null ? (
                  "—"
                ) : (
                  <span className={`tone-${gainTone(v.unrealized_gain) ?? "warn"}`}>
                    {signedMoney(v.unrealized_gain)}
                  </span>
                ),
            },
            {
              key: "call",
              header: "Call",
              render: (v) => {
                const a = adviceFor.get(v.garment_id);
                if (!a) return <span className="gt-muted">—</span>;
                return (
                  <span className="gt-call-cell">
                    <Pill tone={ACTION_TONE[a.action]}>{a.action}</Pill>
                    <span
                      className="num gt-call-conf"
                      title={
                        a.confidence == null
                          ? "no confidence: the engine models nothing here, so it scores nothing"
                          : "confidence in this call"
                      }
                    >
                      {a.confidence == null ? "—" : a.confidence.toFixed(2)}
                    </span>
                  </span>
                );
              },
            },
          ];

          return (
            <>
              <StatRow>
                <Stat
                  label="Portfolio value"
                  value={money(d.value.total_fair_value)}
                  sub={
                    d.value.unavailable > 0
                      ? `week ${asOfWeek} · ${d.value.unavailable} piece${d.value.unavailable === 1 ? "" : "s"} not counted`
                      : `as of week ${asOfWeek}`
                  }
                />
                <Stat
                  label="Unrealized"
                  value={signedMoney(gain)}
                  tone={gainTone(gain)}
                  sub={`on ${money(pricedAnchor)} anchored across ${priced} priced piece${priced === 1 ? "" : "s"}`}
                />
                <Stat
                  label="Pieces tracked"
                  value={d.garments.length}
                  sub={`${owned} owned · ${watching} watching`}
                />
                <Stat
                  label="Priced by the index"
                  value={`${priced} of ${d.value.garments.length}`}
                  tone={d.value.unavailable > 0 ? "warn" : "ok"}
                  sub={
                    d.value.unavailable > 0
                      ? `${d.value.unavailable} left unpriced, not guessed`
                      : "every piece has a usable index"
                  }
                />
                <Stat
                  label="Clean sales / week"
                  value={median(sampleSizes) ?? "—"}
                  tone={
                    sampleSizes.length === 0
                      ? "warn"
                      : (median(sampleSizes) ?? 0) < THIN_SALES
                        ? "warn"
                        : "ok"
                  }
                  sub={`median across ${sampleSizes.length} strata · engine floor ${MIN_SALES}`}
                />
                <Stat
                  label="Fenced out"
                  value={fencedTotal}
                  sub={`implausible comps dropped, last ${WINDOW_WEEKS} wks`}
                />
                <Stat
                  label="Actionable calls"
                  value={`${actionable} of ${d.advice.advice.length}`}
                  tone={actionable > 0 ? "ok" : undefined}
                  sub={`${d.advice.counts.buy ?? 0} buy · ${d.advice.counts.sell ?? 0} sell · ${d.advice.counts.hold ?? 0} hold`}
                />
              </StatRow>

              <Panel
                title="Holdings, marked to market"
                hint="Every row names the method it was priced by and the number of clean sold comps standing behind that price. Select a row to see the call and its evidence."
                actions={
                  <div className="gt-asof">
                    <Field label="As of week" hint="Event impact decays — rewind to the week one landed.">
                      <Select value={asOf} onChange={(e) => setAsOf(e.target.value)}>
                        <option value="">Latest index week ({asOfWeek})</option>
                        {jumps.map((j) => (
                          <option value={j.value} key={j.value}>
                            {j.label}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  </div>
                }
                padded={false}
              >
                {d.value.garments.length === 0 ? (
                  <State
                    kind="empty"
                    title="No pieces registered"
                    detail="Add garments to the closet (portfolio add) and they will be marked against the index here."
                  />
                ) : (
                  <>
                    <Table
                      columns={columns}
                      rows={d.value.garments}
                      rowKey={(v) => v.garment_id}
                      onRowClick={(v) => setPicked(v.garment_id)}
                      selectedKey={activeId ?? undefined}
                      caption="Holdings marked to market"
                    />
                    <p className="gt-legend">
                      Sample is the clean sold comps in that stratum's newest index week. The engine
                      publishes no week under {MIN_SALES} sales; this screen calls anything under{" "}
                      {THIN_SALES} thin, and an index more than {STALE_MAX_WEEKS} weeks behind the
                      as-of week unusable. Fenced comps are price-implausible sales removed before
                      averaging.
                    </p>
                  </>
                )}
              </Panel>

              <Split>
                <Panel
                  title="The call and the evidence behind it"
                  hint="Unpacked from the engine's own rationale codes — no summary, the actual drivers."
                >
                  <CallPanel
                    advice={activeAdvice}
                    garment={activeId ? garmentById.get(activeId) : undefined}
                    sample={activeSample}
                    drivers={drivers}
                    asOfWeek={asOfWeek}
                  />
                </Panel>

                <Panel
                  title="The index behind this valuation"
                  hint={
                    activeValuation?.stratum_id
                      ? `${activeValuation.stratum_id} · last ${WINDOW_WEEKS} weeks`
                      : "no stratum for the selected piece"
                  }
                >
                  <IndexPanel
                    series={activeValuation?.stratum_id ? d.series[activeValuation.stratum_id] : undefined}
                    sample={activeSample}
                    asOfWeek={asOfWeek}
                  />
                </Panel>
              </Split>

              <Panel
                title="The tape"
                hint="Typed events are the only thing that moves a call. A pending event influences nothing until it is confirmed."
                padded={false}
              >
                {d.events.length === 0 ? (
                  <State
                    kind="empty"
                    title="No events ingested"
                    detail="Without events every call is a hold — the advisor has nothing to react to."
                  />
                ) : (
                  <ul className="gt-tape">
                    {[...d.events]
                      .sort((a, b) => b.occurred_on.localeCompare(a.occurred_on))
                      .map((e) => {
                        const age = weeksBetween(e.occurred_on, asOfWeek);
                        const future = age != null && age < 0;
                        return (
                          <li className="gt-tape-row" key={e.id}>
                            <span className="gt-tape-date mono">{e.occurred_on}</span>
                            <span className="gt-tape-what">
                              <span className="gt-tape-title">
                                <strong>{words(e.event_type)}</strong>
                                <span> · {words(e.brand_id)}</span>
                                {e.era_id ? <code className="mono">{e.era_id}</code> : null}
                              </span>
                              {e.notes ? <span className="gt-tape-note">{e.notes}</span> : null}
                            </span>
                            <span className="gt-tape-tags">
                              <Pill tone={e.status === "confirmed" ? "ok" : "warn"}>{e.status}</Pill>
                              <Pill>{e.source}</Pill>
                              <span className="gt-tape-age num">
                                {age == null
                                  ? ""
                                  : age === 0
                                    ? "this week"
                                    : future
                                      ? `${plural(-age, "wk")} ahead of this week`
                                      : `${plural(age, "wk")} before this week`}
                              </span>
                            </span>
                          </li>
                        );
                      })}
                  </ul>
                )}
              </Panel>

              <p className="gt-foot">
                {d.health.listings.toLocaleString()} listings · {d.health.index_points.toLocaleString()}{" "}
                weekly index points · {d.health.brands} brands · engine config{" "}
                <code className="mono">{d.health.config_version}</code>. {DISCLAIMER}
              </p>
            </>
          );
        }}
      </Async>
    </Page>
  );
}
