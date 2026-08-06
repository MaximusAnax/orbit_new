/**
 * Almanac — a commonplace book that shows its working.
 *
 * Saving a quote is easy; *returning* to it is the unsolved part, so the whole
 * screen is built around the scheduler's decision rather than around a list of
 * saved text. Every card states which of the FR-7 branches produced it, when
 * the entry was last seen, how long its interval is and when it comes back —
 * and each reflection grade shows what it will do to that interval *before*
 * you press it, because the reflection loop is the only control the user has.
 *
 * Provenance is treated the same way: the source line is never quietly empty,
 * misattribution flags render as a footnote on the card rather than a badge in
 * a corner, and the misattribution dataset can be queried directly from any
 * entry so "we checked and found nothing" is a visible answer.
 */

import { useState } from "react";
import { client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import type { QueryState } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Facts, Field, Input, Meter, Page, Panel, Pill, Select,
  Split, Stat, StatRow, State, Table, TextArea,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./almanac.css";

const api = client("almanac");

/* ------------------------------------------------------------------ shapes */

type Grade = "applied" | "resonated" | "flat";

type Health = {
  status: string;
  version: string;
  scheduler_version: string;
  today: string;
  seed: number;
  batch_k: number;
};

type Entry = {
  id: string;
  kind: "quote" | "idea";
  text: string;
  normalized_hash: string;
  author: string | null;
  source: string | null;
  url: string | null;
  note: string | null;
  pinned: boolean;
  status: string;
  captured_on: string;
  created_at: string;
  updated_at: string;
};

type Surfacing = {
  id: string;
  entry_id: string;
  on_date: string;
  slot: number;
  kind: "daily" | "extra";
  select_pool: string;
  prompt_template_id: string;
  prompt_kind: string;
  prompt_text: string;
  personalized: boolean;
  personalize_fell_back: boolean;
  relaxed_cooldown: boolean;
  prompt_recency_relaxed: boolean;
  filter_theme_id: string | null;
  filter_collection_id: string | null;
  scheduler_version: string;
  seed: number;
  created_at: string;
};

type AttributionFlag = {
  misattribution_id: string;
  verdict: "misattributed" | "disputed" | "unverified";
  likely_origin: string | null;
  note: string | null;
  reference_url: string | null;
};

type Card = {
  surfacing: Surfacing;
  entry: Entry;
  themes: string[];
  tags: string[];
  attribution_flags: AttributionFlag[];
};

type TodayResponse = { on_date: string; materialized_now: boolean; cards: Card[] };

type SchedulerState = {
  entry_id: string;
  exposure_count: number;
  last_surfaced_on: string | null;
  interval_days: number;
  flat_streak: number;
};

type Reflection = {
  id: string;
  surfacing_id: string;
  entry_id: string;
  grade: Grade;
  text: string | null;
  logged_at: string;
};

type EntryDetail = {
  entry: Entry;
  tags: string[];
  themes: string[];
  state: SchedulerState;
  surfacings: Surfacing[];
  reflections: Reflection[];
  attribution_flags: AttributionFlag[];
};

type EntriesResponse = { total: number; entries: Entry[] };
type Theme = { id: string; name: string; description: string };

type Capacity = {
  k: number;
  pinned_rescue_load: number;
  capture_rate: number;
  review_capacity: number;
  review_demand: number;
  stretch_lambda: number | null;
  sustainable_library: number | null;
  recommended_k: number | null;
  advisory: string | null;
};

type Stats = {
  on_date: string;
  total_entries: number;
  active_entries: number;
  archived_entries: number;
  by_kind: Record<string, number>;
  pinned_count: number;
  coverage: number;
  exposure_histogram: { exposures: number; entries: number }[];
  open_streak: number;
  reflect_streak: number;
  novelty_share: number;
  rho: number;
  contested_slots: number;
  pinned_status: { entry_id: string; excerpt: string; days_since_seen: number | null; guarantee_days: number }[];
  archive_candidates: { entry_id: string; excerpt: string; flat_streak: number }[];
  capacity: Capacity;
};

/* ------------------------------------------------- the scheduler, in words */

/** The nine FR-7 branches. `select_pool` is the scheduler's own stamp — it keeps
 *  no hidden counters, so this is the whole explanation, not a reconstruction. */
const POOL: Record<string, { label: string; why: string }> = {
  pinned_rescue: {
    label: "pinned rescue",
    why: "Pinned and unseen for longer than the guarantee allows, so the rescue rule claimed this slot ahead of every other pool. Pinning is a promise the scheduler keeps deliberately, not by luck.",
  },
  forced_novelty: {
    label: "forced novelty",
    why: "Captured long enough ago that starvation aging fired: the only branch permitted to exceed the novelty quota, and the thing that bounds how long a bulk import takes to drain.",
  },
  novelty: {
    label: "novelty · contested",
    why: "Both pools had supply and the realized novelty share was under target, so a never-seen entry took the contested slot.",
  },
  review: {
    label: "review · contested",
    why: "Both pools had supply and novelty was already at target, so the most overdue seen entry took the contested slot.",
  },
  novelty_only: {
    label: "novelty only",
    why: "Nothing was due for review, so the slot went uncontested to the highest-priority never-seen entry. This card is a debut.",
  },
  review_only: {
    label: "review only",
    why: "No never-seen entries were eligible, so the most overdue entry took the slot uncontested.",
  },
  not_due: {
    label: "not due",
    why: "Nothing was new and nothing had come due, so the closest-to-due seen entry was surfaced. This is the only branch that shows an entry before its interval has elapsed.",
  },
  relaxed: {
    label: "relaxed cooldown",
    why: "The library is too small to honour the no-repeat window, so the least recently seen entry was reused — and the surfacing records that it was, rather than pretending the window held.",
  },
  extra: {
    label: "extra draw",
    why: "You asked for this one. It counts as an exposure and starts the cooldown, but it never consumed a daily slot and never enters the novelty statistic.",
  },
};

/** FR-6 interval multipliers, committed as `sched-1`. The API reports the params
 *  version at /health but does not serve the table, so the projection below is
 *  labelled as a projection and the server still has the last word. */
const GRADE_MULT: Record<Grade, number> = { resonated: 1.25, applied: 1.5, flat: 3.0 };
const NO_GRADE_MULT = 1.9;
const CLAMP = { lo: 10, hi: 60, hiFlat: 240, demoteStreak: 2, archiveStreak: 3 };

const GRADES: { id: Grade; blurb: string }[] = [
  { id: "applied", blurb: "I did something with it" },
  { id: "resonated", blurb: "It landed, no action yet" },
  { id: "flat", blurb: "Nothing — push it away" },
];

const GRADE_TONE: Record<Grade, "ok" | "accent" | "warn"> = {
  applied: "ok",
  resonated: "accent",
  flat: "warn",
};

type FoldRow = { grade: string; mult: number };

const FOLD_ROWS: FoldRow[] = [
  { grade: "resonated", mult: GRADE_MULT.resonated },
  { grade: "applied", mult: GRADE_MULT.applied },
  { grade: "none (never reflected)", mult: NO_GRADE_MULT },
  { grade: "flat", mult: GRADE_MULT.flat },
];

const FOLD_COLUMNS: Column<FoldRow>[] = [
  { key: "grade", header: "Grade", render: (r) => r.grade },
  { key: "mult", header: "Multiplier", numeric: true, render: (r) => <span className="num">×{r.mult}</span> },
  {
    key: "next",
    header: "From 10 d",
    numeric: true,
    render: (r) => <span className="num">{Math.min(CLAMP.hi, Math.round(10 * r.mult))} d</span>,
  },
];

function projectInterval(current: number, grade: Grade, flatStreak: number) {
  const demoted = grade === "flat" && flatStreak + 1 >= CLAMP.demoteStreak;
  const raw = Math.round(current * GRADE_MULT[grade]);
  const next = demoted ? CLAMP.hiFlat : Math.min(CLAMP.hi, Math.max(CLAMP.lo, raw));
  return { next, demoted };
}

/* ------------------------------------------------------------------ dates */

function dayDiff(later: string, earlier: string): number {
  const ms = Date.parse(`${later}T00:00:00Z`) - Date.parse(`${earlier}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}

function addDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function ago(date: string, today: string): string {
  const n = dayDiff(today, date);
  if (n === 0) return "today";
  if (n === 1) return "yesterday";
  if (n < 0) return `in ${-n} d`;
  return `${n} d ago`;
}

function excerpt(text: string, max = 96): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

/* ------------------------------------------------------------- provenance */

function FlagNote({ flags }: { flags: AttributionFlag[] }) {
  if (flags.length === 0) return null;
  return (
    <div className="flags">
      {flags.map((f) => (
        <div className="flag" key={f.misattribution_id}>
          <Pill tone={f.verdict === "misattributed" ? "bad" : "warn"}>{f.verdict}</Pill>
          <div className="flag-body">
            {f.note ? <p>{f.note}</p> : null}
            {f.likely_origin ? (
              <p className="flag-origin">Likely origin: {f.likely_origin}</p>
            ) : null}
            {f.reference_url ? (
              <a href={f.reference_url} target="_blank" rel="noreferrer">
                {f.reference_url}
              </a>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function Cite({ entry }: { entry: Entry }) {
  return (
    <p className="qc-cite">
      <span className="qc-author">{entry.author ?? "Unattributed"}</span>
      {entry.source ? (
        <span className="qc-source">{entry.source}</span>
      ) : (
        <span className="qc-source qc-source-none">no source recorded</span>
      )}
      {entry.url ? (
        <a className="qc-url" href={entry.url} target="_blank" rel="noreferrer">
          source link
        </a>
      ) : null}
    </p>
  );
}

/* ---------------------------------------------------------------- reflect */

function ReflectForm({
  surfacing,
  state,
  logged,
  onLogged,
}: {
  surfacing: Surfacing;
  state: SchedulerState;
  logged: Reflection | undefined;
  onLogged: () => void;
}) {
  const [grade, setGrade] = useState<Grade | null>(null);
  const [note, setNote] = useState("");
  const post = useMutation((g: Grade, text: string) =>
    api.post<Reflection>(`/surfacings/${surfacing.id}/reflection`, {
      grade: g,
      text: text.trim() ? text.trim() : null,
    }),
  );

  if (logged) {
    const { next, demoted } = projectInterval(state.interval_days, logged.grade, state.flat_streak);
    return (
      <div className="reflected">
        <span className="qc-label">Reflection logged</span>
        <p>
          <Pill tone={GRADE_TONE[logged.grade]}>{logged.grade}</Pill>{" "}
          {logged.text ? <span className="reflected-text">“{logged.text}”</span> : null}
        </p>
        <p className="fine">
          The interval is still <span className="num">{state.interval_days} d</span>; the grade is
          applied at the <em>next</em> surfacing, which projects to{" "}
          <span className="num">{next} d</span>
          {demoted ? " — a demotion out of rotation" : ""}.
        </p>
      </div>
    );
  }

  return (
    <form
      className="reflect"
      onSubmit={(e) => {
        e.preventDefault();
        if (!grade) return;
        void post.run(grade, note).then((r) => {
          if (r) onLogged();
        });
      }}
    >
      <fieldset className="grades">
        <legend className="qc-label">How did it land? Each grade moves the schedule</legend>
        {GRADES.map((g) => {
          const { next, demoted } = projectInterval(state.interval_days, g.id, state.flat_streak);
          return (
            <label key={g.id} className={`grade${grade === g.id ? " on" : ""}`}>
              <input
                type="radio"
                name={`grade-${surfacing.id}`}
                value={g.id}
                checked={grade === g.id}
                onChange={() => setGrade(g.id)}
              />
              <span className="grade-name">{g.id}</span>
              <span className="grade-blurb">{g.blurb}</span>
              <span className="grade-effect num">
                ×{GRADE_MULT[g.id]} → {next} d{demoted ? " (demoted)" : ""}
              </span>
            </label>
          );
        })}
      </fieldset>
      <Field label="Note (optional)">
        <TextArea
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What you did with it, or why it fell flat"
        />
      </Field>
      <div className="reflect-actions">
        <Button type="submit" variant="primary" size="sm" pending={post.pending} disabled={!grade}>
          Log reflection
        </Button>
        <span className="fine">
          Never reflecting is itself a signal: ×{NO_GRADE_MULT} on the next interval.
        </span>
      </div>
      <ErrorNote error={post.error} />
    </form>
  );
}

/* ------------------------------------------------------------- today card */

function QuoteCard({
  card,
  today,
  onReflected,
}: {
  card: Card;
  today: string;
  onReflected: () => void;
}) {
  const { surfacing: s, entry } = card;
  const detail = useQuery(() => api.get<EntryDetail>(`/entries/${entry.id}`), [entry.id]);
  const pool = POOL[s.select_pool] ?? {
    label: s.select_pool,
    why: "The scheduler stamped a branch this screen does not have copy for.",
  };

  return (
    <article className="quote-card">
      <header className="qc-head">
        <div className="qc-marks">
          <Pill tone="accent">{pool.label}</Pill>
          <Pill>{s.kind === "extra" ? "extra draw" : `daily · slot ${s.slot}`}</Pill>
          {s.relaxed_cooldown ? <Pill tone="warn">cooldown relaxed</Pill> : null}
          {s.personalize_fell_back ? <Pill tone="warn">prompt fell back</Pill> : null}
          {entry.pinned ? <Pill tone="ok">pinned</Pill> : null}
        </div>
        <span className="qc-date num">{s.on_date}</span>
      </header>

      <blockquote className="qc-text">{entry.text}</blockquote>
      <Cite entry={entry} />
      <FlagNote flags={card.attribution_flags} />

      {entry.note ? (
        <p className="qc-note">
          <span className="qc-label">Your note</span>
          {entry.note}
        </p>
      ) : null}

      {card.themes.length || card.tags.length ? (
        <div className="qc-tags">
          {card.themes.map((t) => (
            <Pill key={t}>{t.replace(/_/g, " ")}</Pill>
          ))}
          {card.tags.map((t) => (
            <span className="tag mono" key={t}>
              #{t}
            </span>
          ))}
        </div>
      ) : null}

      <div className="qc-prompt">
        <span className="qc-label">Prompt · {s.prompt_kind}</span>
        <p className="qc-prompt-text">{s.prompt_text}</p>
        <span className="fine mono">
          {s.prompt_template_id}
          {s.prompt_recency_relaxed ? " · reuse window relaxed" : ""}
          {s.personalized ? " · personalized" : ""}
        </span>
      </div>

      <div className="qc-why">
        <span className="qc-label">Why this card, today</span>
        <p className="qc-why-lede">{pool.why}</p>
        <Async query={detail}>
          {(d) => {
            const prior = d.surfacings
              .filter((x) => x.id !== s.id && x.created_at <= s.created_at)
              .sort((a, b) => a.created_at.localeCompare(b.created_at))
              .at(-1);
            const due = d.state.last_surfaced_on
              ? addDays(d.state.last_surfaced_on, d.state.interval_days)
              : null;
            const logged = d.reflections.find((r) => r.surfacing_id === s.id);
            return (
              <>
                <Facts
                  items={[
                    [
                      "Seen before this",
                      prior ? (
                        <>
                          {prior.on_date} · {ago(prior.on_date, today)}{" "}
                          <span className="fine mono">via {prior.select_pool}</span>
                        </>
                      ) : (
                        "never — this is its first exposure"
                      ),
                    ],
                    [
                      "Exposures",
                      <span className="num">
                        {d.state.exposure_count}× since capture on {d.entry.captured_on}
                      </span>,
                    ],
                    [
                      "Interval",
                      <span className="num">
                        {d.state.interval_days} d
                        {d.state.flat_streak > 0 ? ` · flat streak ${d.state.flat_streak}` : ""}
                      </span>,
                    ],
                    [
                      "Comes back",
                      due ? (
                        <span className="num">
                          {due} ({ago(due, today)})
                        </span>
                      ) : (
                        "once it has been seen once"
                      ),
                    ],
                    [
                      "Reproducible from",
                      <span className="mono">
                        seed {s.seed} · {s.scheduler_version} · {s.id}
                      </span>,
                    ],
                  ]}
                />
                <ReflectForm
                  surfacing={s}
                  state={d.state}
                  logged={logged}
                  onLogged={() => {
                    detail.reload();
                    onReflected();
                  }}
                />
              </>
            );
          }}
        </Async>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------- the budget */

function SchedulePanel({ stats }: { stats: Stats }) {
  const c = stats.capacity;
  const lambda = c.stretch_lambda;
  const lambdaTone = lambda === null ? undefined : lambda > 3 ? "bad" : lambda > 1 ? "warn" : "ok";
  const noveltyOff = Math.abs(stats.novelty_share - stats.rho);

  return (
    <>
      <StatRow>
        <Stat
          label="Active entries"
          value={stats.active_entries}
          sub={`${stats.archived_entries} archived · ${stats.pinned_count} pinned`}
        />
        <Stat
          label="Ever surfaced"
          value={`${(stats.coverage * 100).toFixed(0)}%`}
          sub={`of ${stats.active_entries} active`}
        />
        <Stat
          label="Cards a day"
          value={c.k}
          sub={c.recommended_k ? `recommended ${c.recommended_k}` : "the whole budget"}
        />
        <Stat
          label="Streaks"
          value={`${stats.open_streak}d`}
          sub={`reflect ${stats.reflect_streak}d`}
        />
      </StatRow>

      <div className="meters">
        <Meter
          value={stats.novelty_share}
          max={1}
          tone={stats.contested_slots === 0 ? undefined : noveltyOff <= 0.12 ? "ok" : "warn"}
          label={
            <>
              <span>Novelty share (target {stats.rho})</span>
              <span className="num">
                {stats.contested_slots === 0
                  ? "no contested slots yet"
                  : stats.novelty_share.toFixed(2)}
              </span>
            </>
          }
        />
        <Meter
          value={stats.coverage}
          max={1}
          label={
            <>
              <span>Library seen at least once</span>
              <span className="num">{(stats.coverage * 100).toFixed(1)}%</span>
            </>
          }
        />
      </div>

      <Facts
        items={[
          ["Pinned rescue load r", <span className="num">{c.pinned_rescue_load.toFixed(3)}/day</span>],
          ["Capture rate A", <span className="num">{c.capture_rate.toFixed(3)}/day</span>],
          [
            "Review capacity C = k − r − A",
            <span className={`num${c.review_capacity <= 0 ? " tone-warn" : ""}`}>
              {c.review_capacity.toFixed(3)}/day
            </span>,
          ],
          ["Review demand L = Σ 1/I", <span className="num">{c.review_demand.toFixed(3)}/day</span>],
          [
            "Stretch λ = L / C",
            lambda === null ? (
              <span className="fine">n/a — no review history yet</span>
            ) : (
              <span className={`num${lambdaTone ? ` tone-${lambdaTone}` : ""}`}>
                {lambda.toFixed(2)}
              </span>
            ),
          ],
          [
            "Sustainable library N*",
            c.sustainable_library === null ? (
              <span className="fine">n/a</span>
            ) : (
              <span className="num">{Math.round(c.sustainable_library)} entries</span>
            ),
          ],
        ]}
      />

      {c.review_capacity <= 0 ? (
        <p className="advisory">
          Review capacity is negative: new captures are consuming every slot, so nothing seen can be
          re-served until the backlog drains. The bound is stated rather than hidden — when λ rises
          every entry's gap dilates by the same factor, so relative spacing is preserved exactly.
        </p>
      ) : null}
      {c.advisory ? <p className="advisory">{c.advisory}</p> : null}

      <div className="histogram">
        <span className="qc-label">Exposure histogram</span>
        {stats.exposure_histogram.map((b) => (
          <Meter
            key={b.exposures}
            value={b.entries}
            max={stats.active_entries || 1}
            label={
              <>
                <span>seen {b.exposures}×</span>
                <span className="num">{b.entries}</span>
              </>
            }
          />
        ))}
      </div>

      {stats.pinned_status.length > 0 ? (
        <div className="listing">
          <span className="qc-label">Pinned guarantee</span>
          {stats.pinned_status.map((p) => (
            <p key={p.entry_id}>
              <span className="num">
                {p.days_since_seen === null ? "never seen" : `${p.days_since_seen} d`}
              </span>{" "}
              / {p.guarantee_days} d — {excerpt(p.excerpt, 60)}
            </p>
          ))}
        </div>
      ) : null}

      {stats.archive_candidates.length > 0 ? (
        <div className="listing">
          <span className="qc-label">Archive candidates (never archived for you)</span>
          {stats.archive_candidates.map((a) => (
            <p key={a.entry_id}>
              <span className="num">{a.flat_streak}× flat</span> — {excerpt(a.excerpt, 60)}
            </p>
          ))}
        </div>
      ) : null}
    </>
  );
}

/* ---------------------------------------------------------- entry details */

const HISTORY_COLUMNS: Column<Surfacing>[] = [
  { key: "date", header: "Date", render: (s) => <span className="num">{s.on_date}</span>, width: "7rem" },
  {
    key: "pool",
    header: "Branch",
    render: (s) => (
      <>
        <Pill tone={s.kind === "extra" ? "neutral" : "accent"}>
          {POOL[s.select_pool]?.label ?? s.select_pool}
        </Pill>
        {s.relaxed_cooldown ? <Pill tone="warn">relaxed</Pill> : null}
      </>
    ),
  },
  { key: "prompt", header: "Prompt", render: (s) => <span className="mono">{s.prompt_kind}</span> },
];

function EntryDetailPane({
  entryId,
  today,
  onChanged,
}: {
  entryId: string;
  today: string;
  onChanged: () => void;
}) {
  const detail = useQuery(() => api.get<EntryDetail>(`/entries/${entryId}`), [entryId]);
  const pin = useMutation(async (id: string, next: boolean): Promise<string | null> => {
    if (next) {
      const r = await api.post<{ entry: Entry; warning: string | null }>(`/entries/${id}/pin`);
      return r.warning ?? null;
    }
    await api.del<Entry>(`/entries/${id}/pin`);
    return null;
  });
  const check = useMutation((text: string, author: string | null) =>
    api.get<AttributionFlag[]>("/attribution/check", { text, author: author ?? undefined }),
  );

  return (
    <Async query={detail}>
      {(d) => {
        const due = d.state.last_surfaced_on
          ? addDays(d.state.last_surfaced_on, d.state.interval_days)
          : null;
        const graded = new Map(d.reflections.map((r) => [r.surfacing_id, r]));
        return (
          <div className="detail">
            <blockquote className="detail-text">{d.entry.text}</blockquote>
            <Cite entry={d.entry} />
            <FlagNote flags={d.attribution_flags} />

            <div className="detail-actions">
              <Button
                size="sm"
                pending={pin.pending}
                onClick={() =>
                  void pin.run(d.entry.id, !d.entry.pinned).then(() => {
                    detail.reload();
                    onChanged();
                  })
                }
              >
                {d.entry.pinned ? "Unpin" : "Pin — guarantee its return"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                pending={check.pending}
                onClick={() => void check.run(d.entry.text, d.entry.author)}
              >
                Check attribution
              </Button>
            </div>
            {pin.data ? <p className="advisory">{pin.data}</p> : null}
            <ErrorNote error={pin.error} />
            <ErrorNote error={check.error} />
            {check.data ? (
              check.data.length > 0 ? (
                <FlagNote flags={check.data} />
              ) : (
                <p className="fine">
                  No match in the committed misattribution dataset for this text and claimed author.
                  That is a checked negative, not an unchecked one.
                </p>
              )
            ) : null}

            <Facts
              items={[
                ["Kind", `${d.entry.kind} · ${d.entry.status}`],
                ["Captured", `${d.entry.captured_on} · ${ago(d.entry.captured_on, today)}`],
                ["Themes", d.themes.length ? d.themes.map((t) => t.replace(/_/g, " ")).join(", ") : "none"],
                ["Tags", d.tags.length ? d.tags.map((t) => `#${t}`).join(" ") : "none"],
                ["Exposures", <span className="num">{d.state.exposure_count}</span>],
                [
                  "Last surfaced",
                  d.state.last_surfaced_on ? (
                    <span className="num">
                      {d.state.last_surfaced_on} · {ago(d.state.last_surfaced_on, today)}
                    </span>
                  ) : (
                    "never"
                  ),
                ],
                [
                  "Interval / next due",
                  <span className="num">
                    {d.state.interval_days} d{due ? ` → ${due} (${ago(due, today)})` : " · awaiting debut"}
                  </span>,
                ],
                [
                  "Flat streak",
                  <span className={`num${d.state.flat_streak >= CLAMP.archiveStreak ? " tone-warn" : ""}`}>
                    {d.state.flat_streak}
                  </span>,
                ],
                ["Entry id", <code className="mono">{d.entry.id}</code>],
                [
                  "Normalized hash",
                  <code className="mono">{d.entry.normalized_hash.slice(0, 16)}…</code>,
                ],
              ]}
            />

            {d.surfacings.length > 0 ? (
              <div className="history">
                <span className="qc-label">Surfacing history</span>
                <Table
                  columns={[
                    ...HISTORY_COLUMNS,
                    {
                      key: "grade",
                      header: "Reflection",
                      render: (s: Surfacing) => {
                        const r = graded.get(s.id);
                        return r ? (
                          <Pill tone={GRADE_TONE[r.grade]}>{r.grade}</Pill>
                        ) : (
                          <span className="fine">none (×{NO_GRADE_MULT})</span>
                        );
                      },
                    },
                  ]}
                  rows={[...d.surfacings].sort((a, b) => b.created_at.localeCompare(a.created_at))}
                  rowKey={(s) => s.id}
                  caption="Every time this entry was surfaced, and how it landed"
                />
              </div>
            ) : (
              <p className="fine">Never surfaced yet — it is waiting in the novelty pool.</p>
            )}

            {d.reflections.length > 0 ? (
              <div className="journal">
                <span className="qc-label">Journal</span>
                {d.reflections.map((r) => (
                  <p key={r.id}>
                    <Pill tone={GRADE_TONE[r.grade]}>{r.grade}</Pill>{" "}
                    <span className="fine mono">{r.logged_at.slice(0, 10)}</span>{" "}
                    {r.text ? <span>“{r.text}”</span> : null}
                  </p>
                ))}
              </div>
            ) : null}
          </div>
        );
      }}
    </Async>
  );
}

/* ----------------------------------------------------------------- library */

function Library({ today, themes }: { today: string; themes: QueryState<Theme[]> }) {
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [theme, setTheme] = useState("");
  const [kind, setKind] = useState("");
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const list = useQuery(
    () =>
      api.get<EntriesResponse>("/entries", {
        q: q || undefined,
        theme: theme || undefined,
        kind: kind || undefined,
        pinned: pinnedOnly ? true : undefined,
        limit: 60,
      }),
    [q, theme, kind, pinnedOnly, nonce],
  );

  const columns: Column<Entry>[] = [
    {
      key: "text",
      header: "Entry",
      render: (e) => (
        <>
          <span className="row-text">{excerpt(e.text)}</span>
          <span className="row-cite">
            {e.author ?? "unattributed"}
            {e.source ? ` · ${e.source}` : " · no source"}
          </span>
        </>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      width: "6rem",
      render: (e) => (
        <>
          <Pill>{e.kind}</Pill>
          {e.pinned ? <Pill tone="ok">pinned</Pill> : null}
        </>
      ),
    },
    {
      key: "captured",
      header: "Captured",
      width: "7rem",
      numeric: true,
      render: (e) => e.captured_on,
    },
  ];

  return (
    <>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(draft.trim());
        }}
      >
        <Field label="Full-text search" hint="Stemmed FTS over text, author, source and your notes">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. mind, death, friendship"
            type="search"
          />
        </Field>
        <Field label="Theme">
          <div className="filter-async">
            <Async query={themes}>
              {(list_) => (
                <Select value={theme} onChange={(e) => setTheme(e.target.value)}>
                  <option value="">All themes</option>
                  {list_.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </Select>
              )}
            </Async>
          </div>
        </Field>
        <Field label="Kind">
          <Select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Quotes and ideas</option>
            <option value="quote">Quotes</option>
            <option value="idea">Ideas</option>
          </Select>
        </Field>
        <div className="filter-foot">
          <label className="check">
            <input
              type="checkbox"
              checked={pinnedOnly}
              onChange={(e) => setPinnedOnly(e.target.checked)}
            />
            Pinned only
          </label>
          <Button type="submit" size="sm">
            Search
          </Button>
        </div>
      </form>

      <Split>
        <Async
          query={list}
          emptyWhen={(d) => d.entries.length === 0}
          empty={{
            title: "Nothing matches",
            detail: "Search is stemmed full text — try a shorter word, or clear the filters.",
          }}
        >
          {(d) => (
            <div className="lib-table">
              <p className="fine">
                Showing <span className="num">{d.entries.length}</span> of{" "}
                <span className="num">{d.total}</span> matching entries. Pick one to see its
                schedule and history.
              </p>
              <Table
                columns={columns}
                rows={d.entries}
                rowKey={(e) => e.id}
                onRowClick={(e) => setSelected(e.id)}
                selectedKey={selected ?? undefined}
                caption="The library"
              />
            </div>
          )}
        </Async>

        <Panel title="Entry" hint={selected ? "Scheduler state and full history" : undefined}>
          {selected ? (
            <EntryDetailPane
              entryId={selected}
              today={today}
              onChanged={() => setNonce((n) => n + 1)}
            />
          ) : (
            <State
              kind="empty"
              title="No entry selected"
              detail="Choose a row to see when it was last surfaced, what interval it is on, when it comes back, and every reflection ever logged against it."
            />
          )}
        </Panel>
      </Split>
    </>
  );
}

/* -------------------------------------------------------------------- page */

export default function Almanac() {
  const health = useQuery(() => api.get<Health>("/health"), []);
  const todayQ = useQuery(() => api.get<TodayResponse>("/today"), []);
  const stats = useQuery(() => api.get<Stats>("/stats"), []);
  const themes = useQuery(() => api.get<Theme[]>("/themes"), []);

  const [drawn, setDrawn] = useState<Card[]>([]);
  const [drawTheme, setDrawTheme] = useState("");

  const materialize = useMutation(() => api.post<TodayResponse>("/today", {}));
  const draw = useMutation((themeId: string) =>
    api.post<Card>("/draws", themeId ? { theme: themeId } : {}),
  );

  const today = health.data?.today ?? stats.data?.on_date ?? new Date().toISOString().slice(0, 10);
  const refresh = () => {
    stats.reload();
    todayQ.reload();
  };

  return (
    <Page
      title="Almanac"
      lede="A commonplace book that resurfaces what you saved on a spaced schedule. Every card states which branch of the scheduler produced it, when the entry was last seen and when it comes back — and your reflection is the control that moves it."
      actions={
        <Async query={health}>
          {(h) => (
            <>
              <Pill tone="accent">k = {h.batch_k}/day</Pill>
              <Pill>
                <span className="mono">
                  seed {h.seed} · {h.scheduler_version}
                </span>
              </Pill>
            </>
          )}
        </Async>
      }
    >
      <Panel
        title={`Today · ${todayQ.data?.on_date ?? today}`}
        hint="A date is materialized at most once: re-opening this page returns the identical card, prompt included."
      >
        {/* GET /today 404s with `not_materialized` before the day is opened. That
            is a product state, not a failure, so it gets an action rather than
            an error banner; everything else routes through Async. */}
        {todayQ.error?.code === "not_materialized" ? (
          <State
            kind="empty"
            title="Today has not been opened yet"
            detail="Materializing the date runs the scheduler once and freezes the result — the same card comes back on every later read."
            action={
              <Button
                variant="primary"
                pending={materialize.pending}
                onClick={() => void materialize.run().then(() => todayQ.reload())}
              >
                Open today
              </Button>
            }
          />
        ) : (
          <Async
            query={todayQ}
            emptyWhen={(d) => d.cards.length === 0}
            empty={{
              title: "No card today",
              detail:
                "The scheduler found no eligible entry — every active entry is inside its cooldown window, or the library is empty.",
            }}
          >
            {(d) => (
              <div className="cards">
                {d.cards.map((c) => (
                  <QuoteCard key={c.surfacing.id} card={c} today={today} onReflected={refresh} />
                ))}
              </div>
            )}
          </Async>
        )}

        <ErrorNote error={materialize.error} />

        {drawn.length > 0 ? (
          <div className="cards">
            {drawn.map((c) => (
              <QuoteCard key={c.surfacing.id} card={c} today={today} onReflected={refresh} />
            ))}
          </div>
        ) : null}

        <form
          className="draw"
          onSubmit={(e) => {
            e.preventDefault();
            void draw.run(drawTheme).then((card) => {
              if (card) {
                setDrawn((prev) => [card, ...prev]);
                refresh();
              }
            });
          }}
        >
          <Field label="Draw an extra card" hint="Counts as an exposure and starts the cooldown, but never consumes the daily slot and never enters the novelty statistic.">
            <div className="filter-async">
              <Async query={themes}>
                {(list) => (
                  <Select value={drawTheme} onChange={(e) => setDrawTheme(e.target.value)}>
                    <option value="">Any theme</option>
                    {list.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </Select>
                )}
              </Async>
            </div>
          </Field>
          <Button type="submit" size="sm" pending={draw.pending}>
            Draw
          </Button>
          <ErrorNote error={draw.error} />
        </form>
      </Panel>

      <Split>
        <Panel
          title="The schedule, stated"
          hint="Almanac serves k cards a day and reports the bound rather than pretending it away."
        >
          <Async query={stats}>{(s) => <SchedulePanel stats={s} />}</Async>
        </Panel>

        <Panel title="What a reflection does" hint="FR-6: a negative signal lengthens the interval">
          <Table
            columns={FOLD_COLUMNS}
            rows={FOLD_ROWS}
            rowKey={(r) => r.grade}
            caption="The FR-6 interval table"
          />
          <p className="fine">
            The inversion of SM-2 is deliberate: a flat grade pushes an entry <em>away</em>, because
            the objective is value delivered by the whole portfolio, not retention of each item.
            Intervals are clamped to {CLAMP.lo}–{CLAMP.hi} d; {CLAMP.demoteStreak} consecutive flats
            demote an entry to {CLAMP.hiFlat} d and it effectively leaves rotation,{" "}
            {CLAMP.archiveStreak} list it as an archive candidate. Nothing is ever archived for you.
          </p>
          <Async query={health}>
            {(h) => (
              <p className="fine">
                Multipliers are the committed <code className="mono">sched-1</code> table; the API
                reports the params version (<code className="mono">{h.scheduler_version}</code>) but
                does not serve the numbers, so the per-grade previews on a card are projections —
                the server applies the real fold at the next surfacing.
                {h.scheduler_version !== "sched-1" ? (
                  <strong> Server params differ from this table.</strong>
                ) : null}
              </p>
            )}
          </Async>
        </Panel>
      </Split>

      <Panel
        title="Library"
        hint="Everything saved, with the scheduler state that decides when each one returns."
      >
        <Library today={today} themes={themes} />
      </Panel>
    </Page>
  );
}
