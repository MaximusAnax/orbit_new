/**
 * TickerPress — watchlist news with the duplicates collapsed and the
 * coincidences thrown out.
 *
 * The product is judged on precision, so this screen is built around the two
 * places precision is won and lost:
 *
 *  - **Dedup.** A story is a cluster, never a headline. Every story shows how
 *    many syndicated copies it absorbed, from which outlets, and the exact
 *    Jaccard resemblance that pulled each copy in against τ = 0.60.
 *  - **Disambiguation.** Every candidate the matcher scored is persisted with
 *    its feature vector, so the screen shows the rejections too — "Apple
 *    growers brace for frost" scoring 0.00 against θ = 0.35 is the feature,
 *    not a missing row. The match ledger prices out each feature's signed
 *    contribution using the committed FR-6 weights.
 *
 * The engine exposes rejections only through /articles/{id}/explain, so the
 * ledger sweeps explain for every article in its own query — the story list
 * paints first and the evidence layers in on top.
 */

import { useMemo, useState } from "react";
import { client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, Empty, ErrorNote, Facts, Field, Page, Panel, Pill, Select, Stat,
  StatRow, Table, Tabs,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./tickerpress.css";

const api = client("tickerpress");

/** Committed engine constants (SCOPE FR-6, FR-7) — shown, never re-derived. */
const THETA = 0.35;
const TAU = 0.6;

/* ------------------------------------------------------------------- types */

type Alias = {
  id: number;
  text: string;
  kind: string;
  strength: "strong" | "weak";
  prior: number;
  generated: boolean;
};
type Company = {
  ticker: string;
  name: string;
  mode: string;
  min_relevance: number;
  alert_min_relevance: number;
  context_terms: string[];
  anti_terms: string[];
  aliases?: Alias[];
};
type Feed = { id: number; name: string; url: string; enabled: boolean; last_status: string | null };
type Article = {
  id: number;
  feed_id: number;
  url: string;
  canonical_url: string;
  title: string;
  summary: string;
  published_at: string;
  story_id: number;
  dedup_similarity: number | null;
};
type Story = {
  id: number;
  first_published_at: string;
  representative_article_id: number;
  article_count: number;
};
type StoryDetail = Story & { members: Article[]; relevance: Record<string, number> };
type Mention = {
  id: number;
  company_ticker: string;
  alias_text: string | null;
  alias_kind: string | null;
  field: "title" | "summary" | "content";
  char_start: number;
  char_end: number;
  surface: string;
  matched_via: "alias" | "cashtag" | "exchange_qualified";
  strength: "strong" | "weak";
  features: Record<string, number>;
  score: number;
  threshold: number;
  accepted: boolean;
};
type Appearance = {
  article_id: number;
  company_ticker: string;
  mention_count: number;
  title_hit: boolean;
  lede_hit: boolean;
  relevance: number;
};
type Explain = {
  article_id: number;
  title: string;
  story_id: number;
  dedup_similarity: number | null;
  candidates: Mention[];
  appearances: Appearance[];
};
type Health = { articles: number; stories: number; feeds: number; companies: number };
type IngestRun = {
  id: number;
  status: string;
  articles_new: number;
  candidates_total: number;
  mentions_accepted: number;
  stories_new: number;
  alerts_sent: number;
  engine_version: string;
};
type Delivery = { id: number; channel: string; subject: string; status: string; created_at: string };
type DigestItem = {
  company_ticker: string;
  story_id: number;
  article_id: number;
  relevance: number;
  title: string;
  url: string;
  outlet: string;
  copy_count: number;
  matched_surfaces: string[];
};
type DigestOut = { dry_run: boolean; delivery: Delivery | null; body_text: string; items: DigestItem[] };

/* ------------------------------------------------------- feature economics */

/** FR-6 weights, verbatim. Rendering the signed terms is how "why" stays honest. */
const FEATURE_WEIGHTS: { key: string; label: string; weight: number; cap?: number }[] = [
  { key: "prior", label: "alias prior", weight: 1 },
  { key: "coref_strong", label: "strong mention elsewhere", weight: 0.5 },
  { key: "case_signal", label: "capitalised mid-sentence", weight: 0.1 },
  { key: "window_cues", label: "corporate cues nearby", weight: 0.1, cap: 3 },
  { key: "window_antis", label: "anti-cues nearby", weight: -0.15, cap: 3 },
  { key: "doc_cues", label: "corporate cues in article", weight: 0.05, cap: 3 },
  { key: "doc_antis", label: "anti-cues in article", weight: -0.1, cap: 2 },
  { key: "ctx_terms", label: "your context terms", weight: 0.15, cap: 2 },
  { key: "anti_terms", label: "your anti-terms", weight: -0.2, cap: 2 },
  { key: "hyphen_compound", label: "hyphen compound", weight: -0.2 },
  { key: "allcaps_run", label: "all-caps run", weight: -0.2 },
];

type Contribution = { key: string; label: string; value: number };

function contributions(features: Record<string, number>): Contribution[] {
  const out: Contribution[] = [];
  for (const w of FEATURE_WEIGHTS) {
    const raw = features[w.key];
    if (raw === undefined || raw === 0) continue;
    const capped = w.cap === undefined ? raw : Math.min(w.cap, raw);
    out.push({ key: w.key, label: w.label, value: Number((w.weight * capped).toFixed(4)) });
  }
  return out.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
}

const signed = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(2)}`;

/* ------------------------------------------------------------------ helpers */

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleString("en-GB", {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  })} UTC`;
}

function host(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** Bounded-concurrency fan-out — the explain sweep is 52 tiny reads, not one. */
async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      out[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length || 1) }, worker));
  return out;
}

/* --------------------------------------------------------------- small bits */

/** A proportion with the threshold that decides it drawn on the same axis. */
function ScoreBar({
  value,
  marker,
  tone,
}: {
  value: number;
  marker?: number;
  tone: "ok" | "warn" | "bad";
}) {
  const clamp = (n: number) => Math.max(0, Math.min(100, n * 100));
  return (
    <span className="score-bar" aria-hidden="true">
      <span className={`score-fill bar-${tone}`} style={{ width: `${clamp(value)}%` }} />
      {marker === undefined ? null : (
        <span className="score-mark" style={{ left: `${clamp(marker)}%` }} />
      )}
    </span>
  );
}

function TermList({ terms, tone }: { terms: string[]; tone: "ok" | "bad" }) {
  if (terms.length === 0) return <span className="term-none">—</span>;
  return (
    <span className="term-list">
      {terms.map((t) => (
        <span className={`term term-${tone} mono`} key={t}>
          {t}
        </span>
      ))}
    </span>
  );
}

/* ------------------------------------------------------------ ledger shapes */

type LedgerRow = { mention: Mention; articleId: number; articleTitle: string; storyId: number };
type StorySignal = { relevance: Record<string, number>; rejected: string[] };
type Ledger = { rows: LedgerRow[]; byStory: Record<number, StorySignal> };

type Board = {
  companies: Company[];
  feeds: Feed[];
  stories: Story[];
  articles: Article[];
  matchedBy: Record<number, string[]>;
};

type StoryRow = {
  story: Story;
  title: string;
  members: Article[];
  outlets: string[];
  tickers: string[];
};

/* -------------------------------------------------------------- the screen */

export default function TickerPress() {
  const [selected, setSelected] = useState<number | null>(null);
  const [ticker, setTicker] = useState("");
  const [view, setView] = useState("all");
  const [ledgerTab, setLedgerTab] = useState("all");

  const overview = useQuery(async () => {
    const [health, runs, deliveries] = await Promise.all([
      api.get<Health>("/health"),
      api.get<IngestRun[]>("/ingest/runs", { limit: 1 }),
      api.get<Delivery[]>("/deliveries"),
    ]);
    return { health, run: runs[0] ?? null, deliveries };
  }, []);

  const board = useQuery<Board>(async () => {
    const companies = await api.get<Company[]>("/companies");
    const [feeds, stories, articles] = await Promise.all([
      api.get<Feed[]>("/feeds"),
      api.get<Story[]>("/stories", { limit: 500 }),
      api.get<Article[]>("/articles", { limit: 500 }),
    ]);
    const matchedBy: Record<number, string[]> = {};
    await Promise.all(
      companies.map(async (c) => {
        const hits = await api.get<Story[]>("/stories", { company: c.ticker, limit: 500 });
        for (const s of hits) (matchedBy[s.id] ||= []).push(c.ticker);
      }),
    );
    return { companies, feeds, stories, articles, matchedBy };
  }, []);

  // Rejections and per-company relevance live only behind explain, so sweep it.
  const ledger = useQuery<Ledger>(async () => {
    const articles = await api.get<Article[]>("/articles", { limit: 500 });
    const explains = await mapLimit(articles, 8, (a) =>
      api.get<Explain>(`/articles/${a.id}/explain`),
    );
    const rows: LedgerRow[] = [];
    const byStory: Record<number, StorySignal> = {};
    for (const e of explains) {
      const sig = (byStory[e.story_id] ||= { relevance: {}, rejected: [] });
      for (const m of e.candidates) {
        rows.push({ mention: m, articleId: e.article_id, articleTitle: e.title, storyId: e.story_id });
        if (!m.accepted && !sig.rejected.includes(m.company_ticker)) sig.rejected.push(m.company_ticker);
      }
      for (const ap of e.appearances) {
        const best = sig.relevance[ap.company_ticker] ?? 0;
        sig.relevance[ap.company_ticker] = Math.max(best, ap.relevance);
      }
    }
    rows.sort(
      (a, b) =>
        Number(a.mention.accepted) - Number(b.mention.accepted) || a.mention.score - b.mention.score,
    );
    return { rows, byStory };
  }, []);

  const digest = useMutation(() =>
    api.post<DigestOut>("/digests", { channel: "console", dry_run: true }),
  );

  const feedName = useMemo(() => {
    const names = new Map((board.data?.feeds ?? []).map((f) => [f.id, f.name]));
    return (id: number) => names.get(id) ?? `feed ${id}`;
  }, [board.data]);

  /** A company's min_relevance is what decides whether a match reaches a digest. */
  const floors = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of board.data?.companies ?? []) m.set(c.ticker, c.min_relevance);
    return m;
  }, [board.data]);

  const rows = useMemo<StoryRow[]>(() => {
    const b = board.data;
    if (!b) return [];
    const byStory = new Map<number, Article[]>();
    for (const a of b.articles) {
      const list = byStory.get(a.story_id);
      if (list) list.push(a);
      else byStory.set(a.story_id, [a]);
    }
    return b.stories.map((story) => {
      const members = (byStory.get(story.id) ?? []).slice().sort(
        (x, y) => x.published_at.localeCompare(y.published_at) || x.id - y.id,
      );
      const rep = members.find((m) => m.id === story.representative_article_id) ?? members[0];
      return {
        story,
        title: rep?.title ?? `story ${story.id}`,
        members,
        outlets: [...new Set(members.map((m) => feedName(m.feed_id)))],
        tickers: b.matchedBy[story.id] ?? [],
      };
    });
  }, [board.data, feedName]);

  const signals = ledger.data?.byStory;

  const visible = useMemo(() => {
    return rows.filter((r) => {
      if (ticker && !r.tickers.includes(ticker)) return false;
      if (view === "clustered") return r.members.length > 1;
      if (view === "matched") return r.tickers.length > 0;
      if (view === "rejected") return (signals?.[r.story.id]?.rejected.length ?? 0) > 0;
      return true;
    });
  }, [rows, ticker, view, signals]);

  // Open on something that shows both hard parts: a cluster that matched.
  const activeId = useMemo(() => {
    if (selected !== null) return selected;
    const best =
      rows.find((r) => r.members.length > 1 && r.tickers.length > 0) ??
      rows.find((r) => r.members.length > 1) ??
      rows[0];
    return best ? best.story.id : null;
  }, [selected, rows]);

  const detail = useQuery<{ story: StoryDetail; explains: Explain[] } | null>(async () => {
    if (activeId === null) return null;
    const story = await api.get<StoryDetail>(`/stories/${activeId}`);
    const explains = await mapLimit(story.members ?? [], 4, (m) =>
      api.get<Explain>(`/articles/${m.id}/explain`),
    );
    return { story, explains };
  }, [activeId]);

  const ledgerRows = useMemo(() => {
    const all = ledger.data?.rows ?? [];
    if (ledgerTab === "accepted") return all.filter((r) => r.mention.accepted);
    if (ledgerTab === "rejected") return all.filter((r) => !r.mention.accepted);
    return all;
  }, [ledger.data, ledgerTab]);

  const acceptedCount = (ledger.data?.rows ?? []).filter((r) => r.mention.accepted).length;
  const rejectedCount = (ledger.data?.rows ?? []).length - acceptedCount;

  const ledgerColumns: Column<LedgerRow>[] = [
    {
      key: "verdict",
      header: "Verdict",
      width: "6.5rem",
      render: (r) => (
        <Pill tone={r.mention.accepted ? "ok" : "bad"}>
          {r.mention.accepted ? "accepted" : "rejected"}
        </Pill>
      ),
    },
    {
      key: "surface",
      header: "Surface",
      render: (r) => (
        <span className="led-surface">
          <span className="mono">{r.mention.company_ticker}</span>
          <span className="led-text">“{r.mention.surface}”</span>
          <span className="led-where mono">
            {r.mention.strength} · {r.mention.field} @{r.mention.char_start}
          </span>
        </span>
      ),
    },
    {
      key: "score",
      header: "Score / θ",
      numeric: true,
      width: "8.5rem",
      render: (r) => (
        <span className="led-score">
          <span className="num">
            {r.mention.score.toFixed(2)} / {r.mention.threshold.toFixed(2)}
          </span>
          <ScoreBar
            value={r.mention.score}
            marker={r.mention.threshold}
            tone={r.mention.accepted ? "ok" : "bad"}
          />
        </span>
      ),
    },
    {
      key: "why",
      header: "Decided by",
      render: (r) => {
        const terms = contributions(r.mention.features);
        if (terms.length === 0)
          return <span className="led-why">strong surface — accepted outright</span>;
        return (
          <span className="led-why">
            {terms.slice(0, 3).map((c) => (
              <span key={c.key} className={c.value >= 0 ? "led-pos" : "led-neg"}>
                {c.label} <span className="num">{signed(c.value)}</span>
              </span>
            ))}
          </span>
        );
      },
    },
    {
      key: "article",
      header: "In",
      render: (r) => <span className="led-article">{r.articleTitle}</span>,
    },
  ];

  return (
    <Page
      title="TickerPress"
      lede="Three wire feeds, one watchlist. Syndicated copies of a wire story collapse into a single entry, and a company only appears when the matcher can defend the mention — the orchards, recipes and meta-analyses are scored and thrown out, on the record."
      actions={
        <Button variant="primary" pending={digest.pending} onClick={() => void digest.run()}>
          Preview digest
        </Button>
      }
    >
      <Async query={overview}>
        {({ health, run, deliveries }) => (
          <StatRow>
            <Stat label="Articles ingested" value={health.articles} sub={`${health.feeds} feeds`} />
            <Stat
              label="Stories"
              value={health.stories}
              sub={`${health.articles - health.stories} syndicated copies collapsed`}
            />
            <Stat
              label="Candidates scored"
              value={run ? run.candidates_total : "—"}
              sub={`θ = ${THETA.toFixed(2)}`}
            />
            <Stat
              label="Mentions accepted"
              value={run ? run.mentions_accepted : "—"}
              tone="ok"
              sub={run ? `${run.candidates_total - run.mentions_accepted} rejected as coincidence` : undefined}
            />
            <Stat
              label="Delivered"
              value={deliveries.length}
              sub="exactly-once ledger"
            />
          </StatRow>
        )}
      </Async>

      {digest.error ? (
        <Panel title="Digest">
          <ErrorNote error={digest.error} />
        </Panel>
      ) : null}

      {digest.data ? (
        <Panel
          title="Digest preview"
          hint="Dry run — composed but not delivered, so the exactly-once ledger is untouched."
          actions={<Button size="sm" variant="ghost" onClick={digest.reset}>Dismiss</Button>}
        >
          {digest.data.items.length === 0 ? (
            <Empty
              title="Nothing new to send"
              detail="Every eligible (company, story) pair either sits below its min_relevance or has already been delivered on this channel."
            />
          ) : (
            <>
              <Table
                caption="Digest items"
                columns={[
                  {
                    key: "company",
                    header: "Company",
                    width: "6rem",
                    render: (i: DigestItem) => <span className="mono">{i.company_ticker}</span>,
                  },
                  {
                    key: "rel",
                    header: "Relevance",
                    numeric: true,
                    width: "6rem",
                    render: (i: DigestItem) => i.relevance,
                  },
                  {
                    key: "title",
                    header: "Story",
                    render: (i: DigestItem) => (
                      <a href={i.url} target="_blank" rel="noreferrer">
                        {i.title}
                      </a>
                    ),
                  },
                  {
                    key: "outlet",
                    header: "Outlet",
                    render: (i: DigestItem) => (
                      <span className="dig-outlet">
                        {i.outlet}
                        {i.copy_count > 1 ? (
                          <Pill tone="accent">+{i.copy_count - 1} other outlet</Pill>
                        ) : null}
                      </span>
                    ),
                  },
                  {
                    key: "surfaces",
                    header: "Matched",
                    render: (i: DigestItem) => (
                      <span className="mono dig-surfaces">{i.matched_surfaces.join(", ")}</span>
                    ),
                  },
                ]}
                rows={digest.data.items}
                rowKey={(i) => `${i.company_ticker}-${i.story_id}`}
                onRowClick={(i) => setSelected(i.story_id)}
              />
              <pre className="digest-body mono">{digest.data.body_text}</pre>
            </>
          )}
        </Panel>
      ) : null}

      <div className="split">
        <Panel
          title="Stories"
          hint={
            board.data
              ? `${rows.length} stories from ${board.data.articles.length} articles — ${
                  rows.filter((r) => r.members.length > 1).length
                } absorbed a syndicated copy`
              : "Clusters, not headlines"
          }
          actions={
            <div className="tp-filters">
              <Field label="Company">
                <Select value={ticker} onChange={(e) => setTicker(e.target.value)}>
                  <option value="">All</option>
                  {(board.data?.companies ?? []).map((c) => (
                    <option key={c.ticker} value={c.ticker}>
                      {c.ticker}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Show">
                <Select value={view} onChange={(e) => setView(e.target.value)}>
                  <option value="all">Everything</option>
                  <option value="clustered">Clustered copies</option>
                  <option value="matched">Matched a company</option>
                  <option value="rejected" disabled={!signals}>
                    Match rejected
                  </option>
                </Select>
              </Field>
            </div>
          }
        >
          <Async
            query={board}
            emptyWhen={(b) => b.stories.length === 0}
            empty={{
              title: "No stories yet",
              detail: "Add a feed and run an ingest — stories are created at ingest time.",
            }}
          >
            {() =>
              visible.length === 0 ? (
                <Empty
                  title="No story matches that filter"
                  detail="Widen the filter — precision means most of the wire is correctly ignored."
                />
              ) : (
                <div className="story-list">
                  {visible.map((r) => {
                    const sig = signals?.[r.story.id];
                    const active = r.story.id === activeId;
                    return (
                      <button
                        type="button"
                        key={r.story.id}
                        className={`story${active ? " active" : ""}`}
                        aria-pressed={active}
                        onClick={() => setSelected(r.story.id)}
                      >
                        <span className="story-title">{r.title}</span>
                        <span className="story-meta">
                          <span className="mono">{fmtWhen(r.story.first_published_at)}</span>
                          <span className="story-outlets">{r.outlets.join(" · ")}</span>
                        </span>
                        <span className="story-tags">
                          {r.members.length > 1 ? (
                            <Pill tone="accent">{r.members.length} copies merged</Pill>
                          ) : null}
                          {r.tickers.map((t) => {
                            const rel = sig?.relevance[t];
                            const below = rel !== undefined && rel < (floors.get(t) ?? 0);
                            return (
                              <Pill key={t} tone={below ? "warn" : "ok"}>
                                {t}
                                {rel === undefined ? "" : ` · ${rel}`}
                                {below ? " · below floor" : ""}
                              </Pill>
                            );
                          })}
                          {(sig?.rejected ?? [])
                            .filter((t) => !r.tickers.includes(t))
                            .map((t) => (
                              <Pill key={t} tone="bad">
                                {t} rejected
                              </Pill>
                            ))}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )
            }
          </Async>
        </Panel>

        <Panel title="Evidence" hint="Why this is one story, and who it is really about.">
          <Async
            query={detail}
            emptyWhen={(d) => d === null}
            empty={{ title: "Pick a story", detail: "Select a story to see its cluster and its match evidence." }}
          >
            {(d) =>
              d === null ? null : (
                <StoryEvidence
                  key={d.story.id}
                  story={d.story}
                  explains={d.explains}
                  companies={board.data?.companies ?? []}
                  feedName={feedName}
                />
              )
            }
          </Async>
        </Panel>
      </div>

      <Panel
        title="Match ledger"
        hint="Every candidate the matcher scored — accepted and rejected — with the feature terms that decided it. Nothing is discarded silently."
        actions={
          <Tabs
            tabs={[
              { id: "all", label: `All ${ledger.data ? ledger.data.rows.length : ""}`.trim() },
              { id: "accepted", label: `Accepted ${ledger.data ? acceptedCount : ""}`.trim() },
              { id: "rejected", label: `Rejected ${ledger.data ? rejectedCount : ""}`.trim() },
            ]}
            active={ledgerTab}
            onChange={setLedgerTab}
          />
        }
      >
        <Async
          query={ledger}
          emptyWhen={(l) => l.rows.length === 0}
          empty={{
            title: "No candidates scored",
            detail: "No watchlist alias appeared in any ingested article.",
          }}
        >
          {() =>
            ledgerRows.length === 0 ? (
              <Empty title="Nothing in this view" />
            ) : (
              <Table
                caption="Scored mention candidates"
                columns={ledgerColumns}
                rows={ledgerRows}
                rowKey={(r) => String(r.mention.id)}
                onRowClick={(r) => setSelected(r.storyId)}
              />
            )
          }
        </Async>
      </Panel>

      <Panel
        title="Watchlist"
        hint="Aliases are everything the matcher may see; anti-terms are what it must refuse. Strong surfaces accept outright, weak ones have to earn it."
      >
        <Async
          query={board}
          emptyWhen={(b) => b.companies.length === 0}
          empty={{ title: "No companies watched", detail: "Add a company to start matching." }}
        >
          {(b) => (
            <Table
              caption="Watched companies"
              columns={[
                {
                  key: "ticker",
                  header: "Ticker",
                  width: "5.5rem",
                  render: (c: Company) => <span className="mono">{c.ticker}</span>,
                },
                { key: "name", header: "Name", render: (c: Company) => c.name },
                {
                  key: "mode",
                  header: "Delivery",
                  width: "10rem",
                  render: (c: Company) => (
                    <span className="wl-mode">
                      <Pill>{c.mode}</Pill>
                      <span className="num wl-thresh">
                        ≥{c.min_relevance} · alert ≥{c.alert_min_relevance}
                      </span>
                    </span>
                  ),
                },
                {
                  key: "aliases",
                  header: "Aliases",
                  render: (c: Company) => (
                    <span className="alias-list">
                      {(c.aliases ?? []).map((a) => (
                        <span key={a.id} className={`alias alias-${a.strength}`}>
                          <span className="mono">{a.text}</span>
                          <span className="alias-kind">
                            {a.strength === "strong" ? "strong" : `weak · prior ${a.prior.toFixed(2)}`}
                          </span>
                        </span>
                      ))}
                    </span>
                  ),
                },
                {
                  key: "ctx",
                  header: "Context terms",
                  render: (c: Company) => <TermList terms={c.context_terms} tone="ok" />,
                },
                {
                  key: "anti",
                  header: "Anti-terms",
                  render: (c: Company) => <TermList terms={c.anti_terms} tone="bad" />,
                },
                {
                  key: "hits",
                  header: "Stories",
                  numeric: true,
                  width: "5rem",
                  render: (c: Company) =>
                    Object.values(b.matchedBy).filter((t) => t.includes(c.ticker)).length,
                },
              ]}
              rows={b.companies}
              rowKey={(c) => c.ticker}
            />
          )}
        </Async>
      </Panel>
    </Page>
  );
}

/* ------------------------------------------------------------ the drill-down */

function StoryEvidence({
  story,
  explains,
  companies,
  feedName,
}: {
  story: StoryDetail;
  explains: Explain[];
  companies: Company[];
  feedName: (id: number) => string;
}) {
  const [expanded, setExpanded] = useState(false);
  const members = (story.members ?? [])
    .slice()
    .sort((a, b) => a.published_at.localeCompare(b.published_at) || a.id - b.id);
  const byArticle = new Map(explains.map((e) => [e.article_id, e]));
  // Rejections first: a suppressed match is the thing worth reading.
  const candidates = explains
    .flatMap((e) => e.candidates.map((m) => ({ mention: m, articleId: e.article_id })))
    .sort(
      (a, b) =>
        Number(a.mention.accepted) - Number(b.mention.accepted) ||
        a.mention.score - b.mention.score ||
        a.mention.id - b.mention.id,
    );
  const shown = expanded ? candidates : candidates.slice(0, 4);
  const appearances = explains.flatMap((e) => e.appearances);
  const relRows = Object.entries(story.relevance ?? {}).map(([t, value]) => ({
    ticker: t,
    value,
    appearance: appearances
      .filter((a) => a.company_ticker === t)
      .sort((a, b) => b.relevance - a.relevance)[0],
    company: companies.find((c) => c.ticker === t),
  }));

  return (
    <div className="evidence">
      <Facts
        items={[
          ["Story", <span className="mono">#{story.id}</span>],
          ["First published", fmtWhen(story.first_published_at)],
          [
            "Copies",
            `${story.article_count} ${story.article_count === 1 ? "article" : "articles"} from ${
              new Set(members.map((m) => m.feed_id)).size
            } outlet(s)`,
          ],
        ]}
      />

      <section className="ev-block">
        <h3>Cluster</h3>
        <p className="ev-note">
          3-token shingles, exact Jaccard resemblance. A copy joins the story at{" "}
          <span className="mono">J ≥ {TAU.toFixed(2)}</span>; below that it opens its own.
        </p>
        <div className="member-list">
          {members.map((m) => {
            const sim = byArticle.get(m.id)?.dedup_similarity ?? m.dedup_similarity;
            return (
              <div className="member" key={m.id}>
                <div className="member-head">
                  <span className="member-outlet">{feedName(m.feed_id)}</span>
                  {m.id === story.representative_article_id ? (
                    <Pill tone="accent">representative</Pill>
                  ) : null}
                  {sim === null ? (
                    <Pill>opened the story</Pill>
                  ) : (
                    <Pill tone="ok">J = {sim.toFixed(3)}</Pill>
                  )}
                </div>
                <p className="member-title">{m.title}</p>
                {sim === null ? null : <ScoreBar value={sim} marker={TAU} tone="ok" />}
                <p className="member-foot">
                  <span className="mono">{fmtWhen(m.published_at)}</span>
                  <a href={m.canonical_url} target="_blank" rel="noreferrer" className="mono">
                    {host(m.canonical_url)}
                  </a>
                </p>
              </div>
            );
          })}
        </div>
      </section>

      <section className="ev-block">
        <h3>Mention candidates</h3>
        {candidates.length === 0 ? (
          <p className="ev-none">
            No watchlist alias occurs anywhere in this story, so nothing was scored — it never
            reaches a digest.
          </p>
        ) : (
          <div className="cand-list">
            {shown.map(({ mention: m, articleId }) => {
              const terms = contributions(m.features);
              const total = terms.reduce((sum, c) => sum + c.value, 0);
              return (
                <div className={`cand ${m.accepted ? "cand-ok" : "cand-bad"}`} key={m.id}>
                  <div className="cand-head">
                    <Pill tone={m.accepted ? "ok" : "bad"}>
                      {m.accepted ? "accepted" : "rejected"}
                    </Pill>
                    <span className="cand-surface">“{m.surface}”</span>
                    <span className="cand-score num">
                      {m.score.toFixed(2)} vs θ {m.threshold.toFixed(2)}
                    </span>
                  </div>
                  <ScoreBar value={m.score} marker={m.threshold} tone={m.accepted ? "ok" : "bad"} />
                  <p className="cand-where mono">
                    {m.company_ticker} · {m.strength} {m.matched_via.replace(/_/g, " ")}
                    {m.alias_kind ? ` · ${m.alias_kind.replace(/_/g, " ")}` : ""} · {m.field} @
                    {m.char_start}–{m.char_end} · article #{articleId}
                  </p>
                  {terms.length === 0 ? (
                    <p className="cand-why">
                      Strong surface — accepted outright, no evidence score needed.
                    </p>
                  ) : (
                    <ul className="contribs">
                      {terms.map((c) => (
                        <li className={`contrib ${c.value >= 0 ? "pos" : "neg"}`} key={c.key}>
                          <span>{c.label}</span>
                          <span className="num">{signed(c.value)}</span>
                        </li>
                      ))}
                      <li className="contrib total">
                        <span>
                          total
                          {total < 0 ? " (clamped to 0)" : ""}
                        </span>
                        <span className="num">{total.toFixed(2)}</span>
                      </li>
                    </ul>
                  )}
                </div>
              );
            })}
            {candidates.length > shown.length || expanded ? (
              <Button size="sm" variant="ghost" onClick={() => setExpanded(!expanded)}>
                {expanded
                  ? "Show fewer"
                  : `Show all ${candidates.length} scored candidates`}
              </Button>
            ) : null}
          </div>
        )}
      </section>

      <section className="ev-block">
        <h3>Relevance</h3>
        {relRows.length === 0 ? (
          <p className="ev-none">No company appears in this story, so it carries no relevance.</p>
        ) : (
          <div className="rel-list">
            {relRows.map((r) => {
              const floor = r.company?.min_relevance ?? 0;
              const clears = r.value >= floor;
              return (
                <div className="rel" key={r.ticker}>
                  <div className="rel-head">
                    <span className="mono">{r.ticker}</span>
                    <span className="num rel-value">{r.value}</span>
                    <Pill tone={clears ? "ok" : "warn"}>
                      {clears ? "clears digest floor" : `below floor ${floor}`}
                    </Pill>
                  </div>
                  <ScoreBar value={r.value / 100} marker={floor / 100} tone={clears ? "ok" : "warn"} />
                  {r.appearance ? (
                    <p className="rel-formula mono">
                      0.50·title {r.appearance.title_hit ? 1 : 0} + 0.25·lede{" "}
                      {r.appearance.lede_hit ? 1 : 0} + 0.25·min(1, {r.appearance.mention_count}/4)
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
