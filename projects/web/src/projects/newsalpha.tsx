/**
 * NewsAlpha — news turned into typed events, scored signals, and a backtest
 * that is allowed to say the signals are worthless.
 *
 * The product's hard part is honesty about predictive value, so this screen is
 * built to resist reading a signal as a trade idea:
 *
 *  - The verification panel comes *first*. Before a single signal is shown, the
 *    page states what the measurement says — real run against placebo run,
 *    calibration by confidence bucket, and per-event-type samples flagged when
 *    they are too thin to conclude anything.
 *  - A signal is never reduced to one number. The expected abnormal return is
 *    drawn as a band around zero with its horizon; the ranking `score` is shown
 *    only in the detail pane, labelled as an ordering device.
 *  - Every reading links back down the chain that produced it: signal → prior
 *    and modifiers → event, its attributes and stage → the asset link and its
 *    role → the quoted character span → the article, with that span highlighted
 *    in place. Offsets index `title + "\n" + body`, verified against the corpus.
 *  - The brief is rendered section by section, including its uncertainty note
 *    and its verbatim not-advice footer, taken from the API's own text.
 *
 * Every section renders through `Async`. The two enrichment queries (the asset
 * gazetteer used for names, and the watchlist) deliberately use `ErrorNote`
 * instead: wrapping them in `Async` would let a failed lookup hide the signals
 * they merely annotate. Their failures are still shown, never swallowed.
 */

import { useMemo, useState } from "react";
import { client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Facts, Field, Input, Meter, Page, Panel, Pill, Select, Stat,
  StatRow, State, Table, Tabs,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./newsalpha.css";

const api = client("newsalpha");

/* ------------------------------------------------------------------ types */

type Direction = "bullish" | "bearish" | "unclear";
type Magnitude = "minor" | "moderate" | "major";
type Stage = "rumored" | "confirmed" | "denied";
type Tier = "t1_official" | "t2_wire" | "t3_other";

type Span = { article_id: string; start: number; end: number; quote: string };

type EventSnapshot = {
  event_type: string;
  stage: Stage;
  attributes: Record<string, unknown>;
  corroboration: number;
  best_tier: Tier;
  extraction_confidence: number;
  link_confidence: number;
  evidence_article_ids: string[];
  event_date: string;
};

type Signal = {
  id: string;
  signal_key: string;
  revision: number;
  supersedes: string | null;
  supersedes_key: string | null;
  event_id: string;
  asset_id: string;
  role: string;
  direction: Direction;
  magnitude: Magnitude;
  confidence: number;
  horizon_bars: number;
  expected_ar_lo: number;
  expected_ar_hi: number;
  score: number;
  prior_key: string;
  rationale_codes: string[];
  event_snapshot: EventSnapshot;
  observed_at: string;
  created_as_of: string;
};
type SignalList = { count: number; include_superseded: boolean; signals: Signal[] };
type RevisionList = { signal_key: string; count: number; revisions: Signal[] };

type Brief = {
  signal_id: string;
  template_id: string;
  what_happened: string;
  why_it_matters: string;
  what_to_watch: string[];
  uncertainty_note: string;
  rendered_text: string;
  frame_checked: boolean;
};

type EventLink = {
  asset_id: string;
  asset_name?: string | null;
  role: string;
  link_confidence: number;
  evidence: Span;
};
type EventRow = {
  id: string;
  cluster_id: string;
  event_type: string;
  stage: Stage;
  attributes: Record<string, unknown>;
  extraction_confidence: number;
  evidence: Span[];
  notes: string[];
  event_date: string;
  observed_at: string;
  links: EventLink[];
};
type EventList = { count: number; events: EventRow[] };

type Article = {
  id: string;
  url: string | null;
  source_domain: string;
  tier: Tier;
  published_at: string;
  published_at_estimated: boolean;
  excluded_from_analysis: boolean;
  title: string;
  body: string;
};

type Asset = { id: string; kind: string; symbol: string; name: string; benchmark_id: string | null };
type AssetList = { count: number; assets: Asset[] };

type WatchlistResponse = { count: number; items: { asset_id: string; added_at: string }[] };
type WatchToggle = { asset_id: string; added: boolean; removed: boolean };

type Bucket = { n: number; hit: number | null };
type Aggregates = {
  n: number;
  n_excluded: number;
  excluded_by_reason: Record<string, number>;
  n_superseded: number;
  hit_rate: number | null;
  mean_ar: number | null;
  ic_spearman: number | null;
  buckets: Record<string, Bucket>;
};
type BacktestRun = {
  id: string;
  params: { start: string; end: string; min_confidence: number; placebo_seed: number | null };
  as_of: string;
  aggregates: Aggregates;
  per_type: Record<string, Aggregates>;
};
type BacktestList = { count: number; runs: BacktestRun[] };
type BacktestResponse = { run: BacktestRun };
type PricesLoaded = { source: string; assets: number; bars: number };

/* ------------------------------------------------------------------ labels */

const TYPE_LABEL: Record<string, string> = {
  earnings_surprise: "Earnings surprise",
  guidance_change: "Guidance change",
  mna: "M&A",
  regulatory_action: "Regulatory action",
  listing: "Listing",
  delisting: "Delisting",
  hack_exploit: "Hack / exploit",
};

const DIRECTION_TONE: Record<Direction, "ok" | "bad" | "warn"> = {
  bullish: "ok",
  bearish: "bad",
  unclear: "warn",
};

/** Stage is about evidential certainty, not desirability. */
const STAGE_TONE: Record<string, "neutral" | "warn" | "bad"> = {
  confirmed: "neutral",
  rumored: "warn",
  denied: "bad",
};

const TIER_LABEL: Record<string, string> = {
  t1_official: "official",
  t2_wire: "wire",
  t3_other: "other",
};

/** What each rationale code contributed, in the product's own vocabulary. */
const MOD_LABEL: Record<string, string> = {
  kind: "asset class",
  f_stage: "stage factor",
  tier: "best source tier",
  corroboration: "independent sources",
  extraction: "extraction confidence",
  link: "asset-link confidence",
};

const EXCLUSION_LABEL: Record<string, string> = {
  insufficient_bars: "not enough bars after entry",
  unknown_asset_bars: "no price series for the asset",
  benchmark_gap: "benchmark series had a gap",
  zero_abnormal_return: "abnormal return was exactly zero",
  estimated_publish_time: "publication time was estimated",
  placebo_no_clean_window: "placebo found no clean window",
};

/* ------------------------------------------------------------------ format */

function pctOf(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "+" : "-"}${(Math.abs(v) * 100).toFixed(digits)}%`;
}

function fixed(v: number | null | undefined, digits = 3): string {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

function shiftDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function attrText(value: unknown): string {
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** The digest ranking, verbatim: |score| desc, then confidence desc, then asset id. */
function byRank(a: Signal, b: Signal): number {
  const s = Math.abs(b.score) - Math.abs(a.score);
  if (s !== 0) return s;
  const c = b.confidence - a.confidence;
  if (c !== 0) return c;
  return a.asset_id.localeCompare(b.asset_id);
}

/** The not-advice footer, taken verbatim from the rendered brief rather than invented. */
function footerOf(text: string): string | null {
  const parts = text.trim().split(/\n{2,}/);
  const last = parts[parts.length - 1] ?? "";
  return /advice/i.test(last) ? last : null;
}

/* ------------------------------------------------------------------ pieces */

/**
 * An expected-return band drawn around zero. A range with a visible zero line
 * refuses the single-number reading that a bare score invites.
 */
function Band({ lo, hi, domain }: { lo: number; hi: number; domain: number }) {
  const span = Math.max(domain, 0.01);
  const at = (v: number) => Math.max(0, Math.min(100, ((v + span) / (2 * span)) * 100));
  const left = at(Math.min(lo, hi));
  const right = at(Math.max(lo, hi));
  const tone = hi <= 0 ? "bad" : lo >= 0 ? "ok" : "mixed";
  return (
    <span
      className="band"
      role="img"
      aria-label={`expected abnormal return between ${pctOf(lo)} and ${pctOf(hi)}`}
    >
      <span className="band-axis" aria-hidden="true" />
      <span className="band-zero" aria-hidden="true" />
      <span
        className={`band-fill band-${tone}`}
        style={{ left: `${left}%`, width: `${Math.max(right - left, 2)}%` }}
      />
    </span>
  );
}

function BandCell({ s, domain }: { s: Signal; domain: number }) {
  return (
    <div className="band-cell">
      <span className="band-nums num">
        {pctOf(s.expected_ar_lo, 1)} … {pctOf(s.expected_ar_hi, 1)}
      </span>
      <Band lo={s.expected_ar_lo} hi={s.expected_ar_hi} domain={domain} />
      <span className="band-horizon">over {s.horizon_bars} bars</span>
    </div>
  );
}

/** The scoring audit trail: the prior it started from and every modifier applied. */
function RationaleCodes({ codes }: { codes: string[] }) {
  return (
    <ul className="rationale">
      {codes.map((code) => {
        const cut = code.indexOf(":");
        const kind = cut === -1 ? code : code.slice(0, cut);
        const rest = cut === -1 ? "" : code.slice(cut + 1);
        if (kind === "prior") {
          return (
            <li className="rat rat-prior" key={code}>
              <span className="rat-k">base rate</span>
              <code className="rat-v mono">{rest}</code>
            </li>
          );
        }
        const eq = rest.indexOf("=");
        const key = eq === -1 ? rest : rest.slice(0, eq);
        const value = eq === -1 ? "" : rest.slice(eq + 1);
        return (
          <li className="rat" key={code}>
            <span className="rat-k">{MOD_LABEL[key] ?? key}</span>
            <code className="rat-v mono">{value || "—"}</code>
          </li>
        );
      })}
    </ul>
  );
}

/** The article, with the span the engine actually quoted highlighted in place. */
function ArticleView({ span }: { span: Span }) {
  const article = useQuery(() => api.get<Article>(`/articles/${span.article_id}`), [span.article_id]);
  return (
    <Async query={article}>
      {(a) => {
        const text = `${a.title}\n${a.body}`;
        const aligned = text.slice(span.start, span.end) === span.quote;
        return (
          <div className="article">
            <div className="article-meta">
              <code className="mono">{a.source_domain}</code>
              <Pill tone={a.tier === "t1_official" ? "ok" : "neutral"}>
                {TIER_LABEL[a.tier] ?? a.tier}
              </Pill>
              <span className="num">{a.published_at}</span>
              {a.published_at_estimated ? <Pill tone="warn">estimated time</Pill> : null}
              {a.excluded_from_analysis ? <Pill tone="bad">excluded</Pill> : null}
            </div>
            {aligned ? (
              <p className="article-text">
                {text.slice(0, span.start)}
                <mark className="hl">{text.slice(span.start, span.end)}</mark>
                {text.slice(span.end)}
              </p>
            ) : (
              <>
                <p className="np-warn">
                  The stored offsets {span.start}–{span.end} do not line up with this article's
                  text, so the span is shown unhighlighted rather than highlighted in the wrong
                  place.
                </p>
                <p className="article-text">{text}</p>
              </>
            )}
          </div>
        );
      }}
    </Async>
  );
}

/**
 * The evidence layer under one event: what was extracted, which assets it was
 * linked to and in what role, and the quoted span behind each claim.
 */
function EventEvidence({ eventId }: { eventId: string }) {
  const [open, setOpen] = useState<Span | null>(null);
  const detail = useQuery(() => api.get<EventRow>(`/events/${eventId}`), [eventId]);

  return (
    <Async query={detail}>
      {(e) => {
        const attrs = Object.entries(e.attributes);
        return (
          <div className="ev-block">
            <div className="ev-head">
              <Pill tone="accent">{TYPE_LABEL[e.event_type] ?? e.event_type}</Pill>
              <Pill tone={STAGE_TONE[e.stage] ?? "neutral"}>{e.stage}</Pill>
              <span className="num ev-date">{e.event_date}</span>
              <code className="mono ev-id">{e.id}</code>
            </div>

            {attrs.length ? (
              <Facts
                items={attrs.map(([k, v]) => [
                  k.replace(/_/g, " "),
                  <span className="num">{attrText(v)}</span>,
                ])}
              />
            ) : (
              <p className="np-note">No attributes were extracted for this event.</p>
            )}

            {e.notes.length ? (
              <ul className="ev-notes">
                {e.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            ) : null}

            <h3 className="np-h4">Linked assets and roles</h3>
            {e.links.length ? (
              <ul className="links-list">
                {e.links.map((l) => (
                  <li className="link-row" key={`${l.asset_id}-${l.role}`}>
                    <code className="mono">{l.asset_id}</code>
                    <span className="link-name">{l.asset_name ?? ""}</span>
                    <Pill tone={l.role === "subject" ? "accent" : "neutral"}>{l.role}</Pill>
                    <span className="num link-conf">link {l.link_confidence.toFixed(2)}</span>
                    <button
                      className="ev-quote-btn"
                      onClick={() => setOpen(l.evidence)}
                      aria-expanded={open === l.evidence}
                    >
                      “{l.evidence.quote}”
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="np-note">
                The list endpoint returns links only on the detail view; this event has none
                recorded.
              </p>
            )}

            <h3 className="np-h4">
              Trigger evidence
              <span className="np-h4-sub">
                {e.evidence.length} span{e.evidence.length === 1 ? "" : "s"} · extraction{" "}
                {e.extraction_confidence.toFixed(2)}
              </span>
            </h3>
            <ul className="ev-list">
              {e.evidence.map((sp, i) => (
                <li className="ev" key={`${sp.article_id}-${i}`}>
                  <button
                    className="ev-quote-btn"
                    onClick={() => setOpen(open === sp ? null : sp)}
                    aria-expanded={open === sp}
                  >
                    “{sp.quote}”
                  </button>
                  <span className="ev-meta mono">
                    {sp.article_id} · chars {sp.start}–{sp.end}
                  </span>
                </li>
              ))}
            </ul>

            {open ? (
              <div className="article-wrap">
                <div className="article-head">
                  <span className="np-h4">Source article</span>
                  <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
                    Close
                  </Button>
                </div>
                <ArticleView span={open} />
              </div>
            ) : (
              <p className="np-note">Select a quote to read it inside its source article.</p>
            )}
          </div>
        );
      }}
    </Async>
  );
}

/** The brief — where the reasoning lives, uncertainty note and footer included. */
function BriefView({ signalId }: { signalId: string }) {
  const brief = useQuery(() => api.get<Brief>(`/briefs/${signalId}`), [signalId]);
  return (
    <Async query={brief}>
      {(b) => {
        const footer = footerOf(b.rendered_text);
        return (
          <div className="brief">
            <div className="brief-flags">
              <Pill tone={b.frame_checked ? "ok" : "bad"}>
                {b.frame_checked ? "frame check passed" : "frame check failed"}
              </Pill>
              <code className="mono brief-tpl">{b.template_id}</code>
            </div>

            <section className="brief-sec">
              <h3 className="np-h4">What happened</h3>
              <p className="brief-p">{b.what_happened}</p>
            </section>

            <section className="brief-sec">
              <h3 className="np-h4">Why it matters</h3>
              <p className="brief-p">{b.why_it_matters}</p>
            </section>

            <section className="brief-sec">
              <h3 className="np-h4">What to watch</h3>
              <ul className="brief-list">
                {b.what_to_watch.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </section>

            <section className="brief-sec brief-uncertain">
              <h3 className="np-h4">Uncertainty</h3>
              <p className="brief-p">{b.uncertainty_note}</p>
            </section>

            {footer ? <p className="brief-foot">{footer}</p> : null}
          </div>
        );
      }}
    </Async>
  );
}

/** Corroboration arriving later raises confidence through a new revision. */
function Revisions({ signalId, current }: { signalId: string; current: string }) {
  const chain = useQuery(() => api.get<RevisionList>(`/signals/${signalId}/revisions`), [signalId]);
  return (
    <Async query={chain}>
      {(c) => (
        <div className="rev-block">
          <ul className="rev-list">
            {c.revisions.map((r) => (
              <li className={`rev${r.id === current ? " rev-current" : ""}`} key={r.id}>
                <span className="rev-n num">r{r.revision}</span>
                <span className="num">conf {r.confidence.toFixed(2)}</span>
                <Pill tone={DIRECTION_TONE[r.direction]}>{r.direction}</Pill>
                <span className="num rev-src">
                  {r.event_snapshot.corroboration} source
                  {r.event_snapshot.corroboration === 1 ? "" : "s"}
                </span>
                <code className="mono rev-id">{r.id}</code>
              </li>
            ))}
          </ul>
          <p className="np-note">
            {c.count === 1
              ? "One revision. No later corroboration has changed this reading, and the earlier value would have been kept unmodified if it had."
              : `${c.count} revisions. Earlier revisions are retained unmodified so the backtest scores what was actually knowable at the time.`}
          </p>
        </div>
      )}
    </Async>
  );
}

/* ------------------------------------------------------------------ detail */

function SignalDetail({
  signal,
  asset,
  watched,
  onWatch,
  watchPending,
  watchError,
}: {
  signal: Signal;
  asset: Asset | undefined;
  watched: boolean;
  onWatch: () => void;
  watchPending: boolean;
  watchError: ReturnType<typeof useMutation<[string, boolean], WatchToggle>>["error"];
}) {
  const snap = signal.event_snapshot;
  const domain = Math.max(Math.abs(signal.expected_ar_lo), Math.abs(signal.expected_ar_hi)) * 1.35;

  return (
    <Panel
      title={asset ? `${asset.name} (${asset.symbol})` : signal.asset_id}
      hint={
        <>
          <code className="mono">{signal.asset_id}</code>
          {asset ? ` · ${asset.kind}` : null}
          {asset?.benchmark_id ? ` · benchmark ${asset.benchmark_id}` : null}
        </>
      }
      actions={
        <Button size="sm" pending={watchPending} onClick={onWatch}>
          {watched ? "Watching" : "Add to watchlist"}
        </Button>
      }
    >
      <ErrorNote error={watchError} />

      <div className="np-pills">
        <Pill tone={DIRECTION_TONE[signal.direction]}>{signal.direction}</Pill>
        <Pill>{signal.magnitude}</Pill>
        <Pill tone="accent">{TYPE_LABEL[snap.event_type] ?? snap.event_type}</Pill>
        <Pill tone={STAGE_TONE[snap.stage] ?? "neutral"}>{snap.stage}</Pill>
        <Pill>role {signal.role}</Pill>
        <Pill>revision {signal.revision}</Pill>
      </div>

      <div className="reading">
        <div className="reading-item">
          <span className="reading-label">Expected abnormal return</span>
          <span className="reading-value num">
            {pctOf(signal.expected_ar_lo, 1)} … {pctOf(signal.expected_ar_hi, 1)}
          </span>
          <Band lo={signal.expected_ar_lo} hi={signal.expected_ar_hi} domain={domain} />
          <span className="reading-note">
            A range over {signal.horizon_bars} trading bars, measured from the first bar after the
            evidence was published — not a point forecast, and not a target.
          </span>
        </div>
        <div className="reading-item">
          <span className="reading-label">Confidence</span>
          <span className="reading-value num">{signal.confidence.toFixed(2)}</span>
          <Meter value={signal.confidence} label={<span>0.05 floor · 0.95 cap</span>} />
          <span className="reading-note">
            {snap.corroboration} independent source{snap.corroboration === 1 ? "" : "s"}, best tier{" "}
            {TIER_LABEL[snap.best_tier] ?? snap.best_tier}. Confidence is how much the evidence
            supports the reading, not a probability of profit.
          </span>
        </div>
      </div>

      <div className="np-split">
        <div className="np-col">
          <h3 className="np-h3">Brief</h3>
          <BriefView signalId={signal.id} />
        </div>

        <div className="np-col">
          <h3 className="np-h3">How this reading was built</h3>
          <RationaleCodes codes={signal.rationale_codes} />
          <Facts
            items={[
              ["Prior", <code className="mono">{signal.prior_key}</code>],
              [
                "Ranking score",
                <span className="num" title="band midpoint × confidence">
                  {signal.score.toFixed(5)} — ordering only
                </span>,
              ],
              ["Extraction confidence", <span className="num">{snap.extraction_confidence.toFixed(2)}</span>],
              ["Asset-link confidence", <span className="num">{snap.link_confidence.toFixed(2)}</span>],
              ["Event date", <span className="num">{snap.event_date}</span>],
              ["Observed at", <span className="num">{signal.observed_at}</span>],
              ["Computed as of", <span className="num">{signal.created_as_of}</span>],
              ["Signal id", <code className="mono">{signal.id}</code>],
              ["Signal key", <code className="mono">{signal.signal_key}</code>],
            ]}
          />
          <p className="np-note">
            Point-in-time: the reading is a pure function of the articles stored on or before the
            as-of time above, the committed datasets, and nothing else.
          </p>

          <h3 className="np-h3">Evidence</h3>
          <EventEvidence eventId={signal.event_id} />

          <h3 className="np-h3">Revision history</h3>
          <Revisions signalId={signal.id} current={signal.id} />
        </div>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------ verification */

function RunCard({ run, kind }: { run: BacktestRun; kind: "real" | "placebo" }) {
  const a = run.aggregates;
  return (
    <div className={`bt-card bt-${kind}`}>
      <div className="bt-card-head">
        <h3 className="np-h4">{kind === "real" ? "Real run" : "Placebo run"}</h3>
        <code className="mono">{run.id}</code>
      </div>
      <StatRow>
        <Stat label="Measured" value={a.n} sub={`${a.n_excluded} excluded`} />
        <Stat label="Hit rate" value={a.hit_rate === null ? "—" : a.hit_rate.toFixed(3)} />
        <Stat label="Mean AR" value={pctOf(a.mean_ar)} />
        <Stat label="IC (Spearman)" value={fixed(a.ic_spearman)} />
      </StatRow>
      <p className="bt-window num">
        {run.params.start} → {run.params.end} · min confidence{" "}
        {run.params.min_confidence.toFixed(2)}
        {run.params.placebo_seed !== null ? ` · seed ${run.params.placebo_seed}` : ""}
      </p>
      {a.n_excluded > 0 ? (
        <ul className="bt-excl">
          {Object.entries(a.excluded_by_reason).map(([reason, n]) => (
            <li key={reason}>
              <span className="num">{n}</span> {EXCLUSION_LABEL[reason] ?? reason}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Calibration({ a }: { a: Aggregates }) {
  const order = ["lo", "mid", "hi"];
  const buckets = order.map((k) => ({ k, b: a.buckets[k] })).filter((x) => x.b !== undefined);
  if (!buckets.length) return null;
  const hits = buckets.map((x) => x.b.hit);
  let monotonic = true;
  for (let i = 1; i < hits.length; i += 1) {
    const prev = hits[i - 1];
    const cur = hits[i];
    if (prev !== null && prev !== undefined && cur !== null && cur !== undefined && cur < prev) {
      monotonic = false;
    }
  }
  const thin = buckets.filter((x) => x.b.n < 20);

  return (
    <div className="bt-cal">
      <h3 className="np-h4">
        Calibration by confidence bucket
        <span className="np-h4-sub">a higher-confidence signal should hit more often</span>
      </h3>
      <div className="bt-buckets">
        {buckets.map(({ k, b }) => (
          <div className="bucket" key={k}>
            <span className="bucket-k">{k}</span>
            <Meter
              value={b.hit ?? 0}
              label={
                <>
                  <span>hit</span>
                  <span className="num">{b.hit === null ? "—" : b.hit.toFixed(3)}</span>
                </>
              }
            />
            <span className="bucket-n num">n = {b.n}</span>
          </div>
        ))}
      </div>
      <p className={monotonic ? "np-note" : "np-warn"}>
        {monotonic
          ? "Hit rate does not fall as confidence rises in this window, which is the least a calibrated score must do."
          : "Hit rate is not monotonic in confidence here: a higher-confidence bucket does not beat the one below it. Read the confidence as ordering evidence strength, not as a rate."}
        {thin.length
          ? ` Buckets ${thin.map((x) => x.k).join(", ")} hold fewer than 20 observations — too few to conclude anything from.`
          : ""}
      </p>
    </div>
  );
}

function PerType({ a }: { a: Record<string, Aggregates> }) {
  const rows = Object.entries(a)
    .map(([type, agg]) => ({ type, ...agg }))
    .sort((x, y) => y.n - x.n);
  if (!rows.length) return null;

  const columns: Column<(typeof rows)[number]>[] = [
    { key: "type", header: "Event type", render: (r) => TYPE_LABEL[r.type] ?? r.type },
    { key: "n", header: "N", numeric: true, render: (r) => r.n },
    {
      key: "hit",
      header: "Hit rate",
      numeric: true,
      render: (r) => (r.hit_rate === null ? "—" : r.hit_rate.toFixed(3)),
    },
    {
      key: "ar",
      header: "Mean AR",
      numeric: true,
      render: (r) => (
        <span className={r.mean_ar === null ? "" : r.mean_ar >= 0 ? "tone-ok" : "tone-bad"}>
          {pctOf(r.mean_ar)}
        </span>
      ),
    },
    { key: "ic", header: "IC", numeric: true, render: (r) => fixed(r.ic_spearman) },
    {
      key: "read",
      header: "How to read it",
      render: (r) =>
        r.n < 20 ? (
          <Pill tone="warn">too few to conclude</Pill>
        ) : (
          <span className="np-muted">enough to look at</span>
        ),
    },
  ];

  return (
    <div className="bt-pertype">
      <h3 className="np-h4">By event type</h3>
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.type}
        caption="Backtest aggregates per event type"
      />
    </div>
  );
}

function Verification({ start, end }: { start: string; end: string }) {
  const runs = useQuery(() => api.get<BacktestList>("/backtests", { limit: 50 }), []);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const s = from || start;
  const e = to || end;

  const runBacktest = useMutation((seed: number | null) =>
    api.post<BacktestResponse>("/backtests", {
      start: s,
      end: e,
      min_confidence: 0,
      placebo_seed: seed,
    }),
  );
  const loadPrices = useMutation(() =>
    api.post<PricesLoaded>("/prices/load", {
      source: "fixture",
      start: shiftDays(s || "2026-01-01", -200),
      end: shiftDays(e || "2026-12-31", 90),
    }),
  );

  const ready = Boolean(s && e);

  return (
    <Panel
      title="Does any of this predict anything?"
      hint="Run the event study before reading a single signal. The placebo run uses the same harness with the event–date association destroyed; if it finds skill, the harness is leaking and the real numbers mean nothing."
    >
      <div className="bt-controls">
        <Field label="Window start">
          <Input type="date" value={s} onChange={(ev) => setFrom(ev.target.value)} />
        </Field>
        <Field label="Window end">
          <Input type="date" value={e} onChange={(ev) => setTo(ev.target.value)} />
        </Field>
        <div className="bt-buttons">
          <Button
            variant="primary"
            disabled={!ready}
            pending={runBacktest.pending}
            onClick={() => void runBacktest.run(null).then(() => runs.reload())}
          >
            Run backtest
          </Button>
          <Button
            disabled={!ready}
            pending={runBacktest.pending}
            onClick={() => void runBacktest.run(20260731).then(() => runs.reload())}
          >
            Run placebo
          </Button>
          <Button
            variant="ghost"
            pending={loadPrices.pending}
            onClick={() => void loadPrices.run()}
          >
            Load fixture prices
          </Button>
        </div>
      </div>
      <ErrorNote error={runBacktest.error} />
      <ErrorNote error={loadPrices.error} />
      {loadPrices.data ? (
        <p className="np-note num">
          Loaded {loadPrices.data.bars.toLocaleString()} bars for {loadPrices.data.assets} assets
          from the committed {loadPrices.data.source} series. Re-run the backtest to use them.
        </p>
      ) : null}

      <Async
        query={runs}
        emptyWhen={(d) => d.runs.length === 0}
        empty={{
          title: "No backtest has been run",
          detail:
            "Until one has, nothing on this page has been checked against price history. Run it — including the placebo — and let it say what it says.",
        }}
      >
        {(d) => {
          const real = d.runs.find((r) => r.params.placebo_seed === null);
          const placebo = d.runs.find((r) => r.params.placebo_seed !== null);
          const rh = real?.aggregates.hit_rate ?? null;
          const ph = placebo?.aggregates.hit_rate ?? null;
          const skillSurvives = rh !== null && ph !== null && ph >= rh - 0.05;
          const allExcluded = real ? real.aggregates.n === 0 && real.aggregates.n_excluded > 0 : false;

          return (
            <div className="bt">
              {allExcluded ? (
                <p className="np-warn">
                  The latest real run measured nothing: all{" "}
                  <span className="num">{real?.aggregates.n_excluded}</span> signals were excluded.
                  That is the honest result of running a backtest with no price history loaded —
                  load the fixture bars above and run it again.
                </p>
              ) : null}

              <div className="bt-grid">
                {real ? (
                  <RunCard run={real} kind="real" />
                ) : (
                  <State
                    kind="empty"
                    title="No real run in this list"
                    detail="Only placebo runs exist so far."
                  />
                )}
                {placebo ? (
                  <RunCard run={placebo} kind="placebo" />
                ) : (
                  <State
                    kind="empty"
                    title="No placebo run"
                    detail="Without one, the real numbers above are unverified: nothing rules out the harness leaking future information."
                  />
                )}
              </div>

              {real && placebo ? (
                <p className={skillSurvives ? "np-warn" : "np-verdict"}>
                  {skillSurvives
                    ? `The placebo scores ${ph?.toFixed(3)} against the real run's ${rh?.toFixed(3)}. Skill that survives the destruction of the event–date association is not skill — treat these signals as unproven until the harness is explained.`
                    : `Real hit rate ${rh?.toFixed(3)}, placebo ${ph?.toFixed(3)}; IC ${fixed(real.aggregates.ic_spearman)} against ${fixed(placebo.aggregates.ic_spearman)}. The skill disappears when the event–date association is destroyed, which is what a leak-free harness must show.`}{" "}
                  A single placebo seed is noisy — the shipped eval gate averages five.
                </p>
              ) : null}

              {real ? <Calibration a={real.aggregates} /> : null}
              {real ? <PerType a={real.per_type} /> : null}

              <p className="np-note">
                These numbers come from the committed fixture market series. They measure whether the
                pipeline recovers effects that were deliberately planted; they are not a forecast of
                live hit rates.
              </p>
            </div>
          );
        }}
      </Async>
    </Panel>
  );
}

/* ------------------------------------------------------------------ events */

function EventsTable({
  query,
  picked,
  onPick,
}: {
  query: ReturnType<typeof useQuery<EventList>>;
  picked: string | null;
  onPick: (id: string) => void;
}) {
  const columns: Column<EventRow>[] = [
    { key: "date", header: "Date", numeric: true, render: (e) => e.event_date },
    {
      key: "type",
      header: "Event",
      render: (e) => (
        <span className="ev-type">
          {TYPE_LABEL[e.event_type] ?? e.event_type}
          <Pill tone={STAGE_TONE[e.stage] ?? "neutral"}>{e.stage}</Pill>
        </span>
      ),
    },
    {
      key: "attrs",
      header: "Extracted attributes",
      render: (e) => {
        const entries = Object.entries(e.attributes);
        if (!entries.length) return <span className="np-muted">none</span>;
        return (
          <span className="attr-inline mono">
            {entries.map(([k, v]) => `${k.replace(/_/g, " ")} ${attrText(v)}`).join(" · ")}
          </span>
        );
      },
    },
    {
      key: "quote",
      header: "Trigger quote",
      render: (e) => <span className="ev-inline-quote">“{e.evidence[0]?.quote ?? "—"}”</span>,
    },
    { key: "src", header: "Sources", numeric: true, render: (e) => e.evidence.length },
    {
      key: "conf",
      header: "Extraction",
      numeric: true,
      render: (e) => e.extraction_confidence.toFixed(2),
    },
  ];

  return (
    <Async
      query={query}
      emptyWhen={(d) => d.events.length === 0}
      empty={{
        title: "No events extracted",
        detail: "The corpus has been ingested but nothing matched a pattern.",
      }}
    >
      {(d) => (
        <Table
          columns={columns}
          rows={d.events}
          rowKey={(e) => e.id}
          onRowClick={(e) => onPick(e.id)}
          selectedKey={picked ?? undefined}
          caption="Typed events extracted from the corpus"
        />
      )}
    </Async>
  );
}

/* -------------------------------------------------------------------- page */

const TOP_N = 25;

export default function NewsAlpha() {
  const [view, setView] = useState("signals");
  const [direction, setDirection] = useState("");
  const [minConf, setMinConf] = useState("");
  const [eventType, setEventType] = useState("");
  const [superseded, setSuperseded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [pickedSignal, setPickedSignal] = useState<string | null>(null);
  const [pickedEvent, setPickedEvent] = useState<string | null>(null);

  const signals = useQuery(
    () =>
      api.get<SignalList>("/signals", {
        direction: direction || undefined,
        min_confidence: minConf || undefined,
        include_superseded: superseded ? true : undefined,
      }),
    [direction, minConf, superseded],
  );
  const events = useQuery(() => api.get<EventList>("/events"), []);
  const assets = useQuery(() => api.get<AssetList>("/assets"), []);
  const watchlist = useQuery(() => api.get<WatchlistResponse>("/watchlist"), []);

  const watchToggle = useMutation((assetId: string, on: boolean) =>
    on
      ? api.put<WatchToggle>(`/watchlist/${encodeURIComponent(assetId)}`)
      : api.del<WatchToggle>(`/watchlist/${encodeURIComponent(assetId)}`),
  );

  const assetMap = useMemo(() => {
    const m = new Map<string, Asset>();
    for (const a of assets.data?.assets ?? []) m.set(a.id, a);
    return m;
  }, [assets.data]);

  const watched = useMemo(
    () => new Set((watchlist.data?.items ?? []).map((i) => i.asset_id)),
    [watchlist.data],
  );

  const all = useMemo(() => signals.data?.signals ?? [], [signals.data]);
  const ranked = useMemo(
    () =>
      all
        .filter((s) => !eventType || s.event_snapshot.event_type === eventType)
        .slice()
        .sort(byRank),
    [all, eventType],
  );
  const rows = showAll ? ranked : ranked.slice(0, TOP_N);

  const selected = rows.find((s) => s.id === pickedSignal) ?? rows[0];
  const domain = useMemo(() => {
    const m = rows.reduce(
      (acc, s) => Math.max(acc, Math.abs(s.expected_ar_lo), Math.abs(s.expected_ar_hi)),
      0.02,
    );
    return m * 1.15;
  }, [rows]);

  const dates = useMemo(
    () => all.map((s) => s.event_snapshot.event_date).sort(),
    [all],
  );
  const winStart = dates[0] ?? "";
  const winEnd = dates[dates.length - 1] ?? "";

  const bullish = all.filter((s) => s.direction === "bullish").length;
  const bearish = all.filter((s) => s.direction === "bearish").length;
  const confLo = all.length ? Math.min(...all.map((s) => s.confidence)) : 0;
  const confHi = all.length ? Math.max(...all.map((s) => s.confidence)) : 0;
  const filtered = Boolean(direction || minConf || eventType || superseded);

  const columns: Column<Signal>[] = [
    {
      key: "asset",
      header: "Asset",
      render: (s) => {
        const a = assetMap.get(s.asset_id);
        return (
          <span className="asset-cell">
            <span className="asset-sym">{a?.symbol ?? s.asset_id}</span>
            <span className="asset-name">{a?.name ?? s.asset_id}</span>
            {watched.has(s.asset_id) ? <span className="watch-dot" title="on your watchlist" /> : null}
          </span>
        );
      },
    },
    {
      key: "event",
      header: "Event",
      render: (s) => (
        <span className="event-cell">
          <span className="event-type">
            {TYPE_LABEL[s.event_snapshot.event_type] ?? s.event_snapshot.event_type}
          </span>
          <span className="event-tags">
            <Pill tone={STAGE_TONE[s.event_snapshot.stage] ?? "neutral"}>
              {s.event_snapshot.stage}
            </Pill>
            {s.role !== "subject" ? <Pill>{s.role}</Pill> : null}
            <span className="num event-date">{s.event_snapshot.event_date}</span>
          </span>
        </span>
      ),
    },
    {
      key: "dir",
      header: "Direction",
      render: (s) => (
        <span className="dir-cell">
          <Pill tone={DIRECTION_TONE[s.direction]}>{s.direction}</Pill>
          <span className="dir-mag">{s.magnitude}</span>
        </span>
      ),
    },
    {
      key: "band",
      header: "Expected abnormal return",
      width: "15rem",
      render: (s) => <BandCell s={s} domain={domain} />,
    },
    {
      key: "conf",
      header: "Confidence",
      width: "9rem",
      render: (s) => (
        <div className="conf-cell">
          <Meter
            value={s.confidence}
            label={
              <>
                <span>conf</span>
                <span className="num">{s.confidence.toFixed(2)}</span>
              </>
            }
          />
          <span className="conf-src num">
            {s.event_snapshot.corroboration} src ·{" "}
            {TIER_LABEL[s.event_snapshot.best_tier] ?? s.event_snapshot.best_tier}
          </span>
        </div>
      ),
    },
  ];

  return (
    <Page
      title="NewsAlpha"
      lede="Financial news turned into typed events, linked to assets in explicit roles, and scored as signals — each one traceable to the quoted span that produced it, and each one measured by a backtest that is allowed to say it is worthless. Information, not investment advice."
    >
      <StatRow>
        <Stat
          label="Signals"
          value={signals.data?.count ?? "—"}
          sub={superseded ? "all revisions" : "latest revision per key"}
        />
        <Stat label="Typed events" value={events.data?.count ?? "—"} sub="from the ingested corpus" />
        <Stat
          label="Bullish / bearish"
          value={all.length ? `${bullish} / ${bearish}` : "—"}
          sub="no imperative field exists"
        />
        <Stat
          label="Confidence range"
          value={all.length ? `${confLo.toFixed(2)}–${confHi.toFixed(2)}` : "—"}
          sub="capped at 0.95 by construction"
        />
      </StatRow>

      <Verification start={winStart} end={winEnd} />

      <Panel
        title="Signals"
        hint="Ranked the way the digest ranks: |score| descending, then confidence, then asset id. Ranking is triage order, not a recommendation."
        actions={
          ranked.length > TOP_N ? (
            <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>
              {showAll ? `Show top ${TOP_N}` : `Show all ${ranked.length}`}
            </Button>
          ) : null
        }
        padded={false}
      >
        <div className="np-tabs">
          <Tabs
            tabs={[
              { id: "signals", label: `Signals (${ranked.length})` },
              { id: "events", label: `Events (${events.data?.count ?? "…"})` },
            ]}
            active={view}
            onChange={setView}
          />
        </div>

        {view === "signals" ? (
          <>
            <div className="np-filters">
              <Field label="Direction">
                <Select value={direction} onChange={(e) => setDirection(e.target.value)}>
                  <option value="">Any</option>
                  <option value="bullish">Bullish</option>
                  <option value="bearish">Bearish</option>
                  <option value="unclear">Unclear</option>
                </Select>
              </Field>
              <Field label="Minimum confidence">
                <Select value={minConf} onChange={(e) => setMinConf(e.target.value)}>
                  <option value="">Any</option>
                  <option value="0.3">0.30 and up</option>
                  <option value="0.5">0.50 and up</option>
                  <option value="0.7">0.70 and up</option>
                </Select>
              </Field>
              <Field label="Event type">
                <Select value={eventType} onChange={(e) => setEventType(e.target.value)}>
                  <option value="">Any</option>
                  {Object.entries(TYPE_LABEL).map(([id, label]) => (
                    <option value={id} key={id}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Revisions" hint="superseded readings are kept, never rewritten">
                <Select
                  value={superseded ? "all" : "latest"}
                  onChange={(e) => setSuperseded(e.target.value === "all")}
                >
                  <option value="latest">Latest only</option>
                  <option value="all">Include superseded</option>
                </Select>
              </Field>
              <ErrorNote error={assets.error} />
              <ErrorNote error={watchlist.error} />
            </div>

            <Async query={signals}>
              {(d) =>
                d.signals.length === 0 && filtered ? (
                  <State
                    kind="empty"
                    title="No signals match these filters"
                    detail="The corpus holds signals, but none at this direction, confidence or event type."
                    action={
                      <Button
                        onClick={() => {
                          setDirection("");
                          setMinConf("");
                          setEventType("");
                          setSuperseded(false);
                        }}
                      >
                        Clear filters
                      </Button>
                    }
                  />
                ) : d.signals.length === 0 ? (
                  <>
                    <p className="np-note np-pad">
                      No signals were scored from this corpus. The extracted events are shown
                      instead — the layer signals are built from.
                    </p>
                    <EventsTable query={events} picked={pickedEvent} onPick={setPickedEvent} />
                  </>
                ) : (
                  <Table
                    columns={columns}
                    rows={rows}
                    rowKey={(s) => s.id}
                    onRowClick={(s) => setPickedSignal(s.id)}
                    selectedKey={selected?.id}
                    caption="Scored signals, ranked for triage"
                  />
                )
              }
            </Async>
          </>
        ) : (
          <EventsTable query={events} picked={pickedEvent} onPick={setPickedEvent} />
        )}
      </Panel>

      {view === "signals" && selected ? (
        <SignalDetail
          signal={selected}
          asset={assetMap.get(selected.asset_id)}
          watched={watched.has(selected.asset_id)}
          watchPending={watchToggle.pending}
          watchError={watchToggle.error}
          onWatch={() =>
            void watchToggle
              .run(selected.asset_id, !watched.has(selected.asset_id))
              .then(() => watchlist.reload())
          }
        />
      ) : null}

      {view === "events" && pickedEvent ? (
        <Panel title="Event evidence" hint="Every claim resolves to a quoted character span.">
          <EventEvidence eventId={pickedEvent} />
        </Panel>
      ) : null}

      {view === "events" && !pickedEvent ? (
        <Panel>
          <State
            kind="empty"
            title="Select an event"
            detail="Pick a row to see its attributes, the assets it was linked to and in what role, and the quoted spans behind each."
          />
        </Panel>
      ) : null}
    </Page>
  );
}
