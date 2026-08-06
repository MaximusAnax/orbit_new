/**
 * PointsMax — what your points are worth, and the exact sequence that gets you there.
 *
 * Two things make this product hard, so the screen is built around both.
 *
 * 1. A plan has to be *executable*. Not "transfer to Flying Blue" but a numbered
 *    sequence with the real edge, the real ratio, the increment it quantises to,
 *    the fee, the posting time, and whether the move can ever be undone. Each
 *    step is rendered with its mechanics visible, and recording one is gated on
 *    the same confirmation the API demands — when the engine refuses, the refusal
 *    is the result, shown verbatim.
 *
 * 2. A plan has to be *honestly compared to paying cash*. The engine returns four
 *    integers per plan; three of them subtract to the fourth. The arithmetic is
 *    laid out as arithmetic rather than summarised as a verdict, so "$1,298 ahead"
 *    can be checked against its terms. When the numbers say pay cash, the page
 *    says pay cash.
 *
 * The third thread is refusal to invent: award availability comes from a pinned,
 * versioned snapshot, seat counts are quoted as seat counts, and the dataset's
 * version, hash and age sit on the page rather than in a footnote.
 */

import { useMemo, useState } from "react";
import { client } from "../lib/api";
import type { ApiError } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, Facts, Field, Input, Page, Panel, Pill, Select, Stat, StatRow, State, Table, Tabs,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./pointsmax.css";

const api = client("pointsmax");

/* ------------------------------------------------------------------- types */

type Value = {
  program_id: string;
  points: number;
  baseline_value_cents: number;
  baseline_cpp_milli: number;
  as_of: string;
  cash_floor_cents: number;
  cash_floor_option_id: string | null;
  travel_floor_cents: number;
  travel_floor_option_id: string | null;
};
type Balance = { program_id: string; program_name: string; points: number; value: Value };
type Card = {
  id: string;
  issuer: string;
  name: string;
  program_id: string;
  enables_transfer: boolean;
  annual_fee_cents: number;
};
type Wallet = {
  cards: Card[];
  balances: Balance[];
  baseline_total_cents: number;
  cash_floor_total_cents: number;
  travel_floor_total_cents: number;
};

type Edge = {
  id: string;
  from_program: string;
  to_program: string;
  ratio_from: number;
  ratio_to: number;
  min_from: number;
  increment_from: number;
  fee_mcpp: number;
  fee_cap_cents: number | null;
  time_days: number;
  bonus_per_from: number | null;
  bonus_to: number | null;
  valid_to: string | null;
};
type Option = {
  id: string;
  program_id: string;
  method: string;
  cpp_milli: number;
  min_points: number;
  increment: number;
  is_cash: boolean;
  requires_card: string | null;
  valid_to: string | null;
};
type Program = {
  id: string;
  name: string;
  kind: string;
  cpp_milli: number;
  as_of: string;
  active_edges_out: Edge[];
  active_edges_in: Edge[];
  active_options: Option[];
};

type World = {
  version: string;
  as_of: string;
  content_hash: string;
  valuations_as_of: string;
  days_old: number;
  stale: boolean;
  stale_days: number;
  counts: Record<string, number>;
};

type Goal = {
  id: number;
  kind: string;
  status: string;
  raw_text: string | null;
  origin_city: string | null;
  dest_city: string | null;
  cabin: string | null;
  round_trip: boolean | null;
  passengers: number | null;
  city: string | null;
  nights: number | null;
  travel_window_start: string | null;
  travel_window_end: string | null;
  book_by: string | null;
  created_at: string;
};

type Caveat = { code: string; params: Record<string, unknown>; text: string };
type Step = {
  id: number | null;
  seq: number;
  kind: string;
  hop_index: number;
  from_program: string | null;
  to_program: string | null;
  edge_id: string | null;
  offer_id: string | null;
  cashout_id: string | null;
  points_sent: number;
  points_delivered: number | null;
  fees_cents: number;
  eta_days: number;
  irreversible: boolean;
  explanation: string;
  executed_at: string | null;
};
type Plan = {
  id: number;
  plan_set_id: number | null;
  rank: number;
  is_comparator: boolean;
  gross_value_cents: number;
  cash_outlay_cents: number;
  points_cost_cents: number;
  net_value_cents: number;
  cash_received_cents: number | null;
  realized_cpp_milli: number | null;
  points_spent: Record<string, number>;
  feasible_in_days: number;
  signature: string;
  caveats: Caveat[];
  steps: Step[];
  disclaimer: string;
};
type PlanSet = {
  id: number;
  goal_id: number;
  world_version: string;
  world_hash: string;
  today: string;
  params: Record<string, number>;
  expansions: number;
  verdict: string;
  recommended_plan_id: number | null;
  disclaimer: string;
  created_at: string;
  plans: Plan[];
};
type ExecuteOut = { step: Step; entries: unknown[]; balances: Record<string, number> };

type Active = { goal: Goal; set: PlanSet };

/* ----------------------------------------------------------------- format */

const money = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
const pts = (n: number) => n.toLocaleString("en-US");
const cpp = (milli: number | null | undefined) =>
  milli === null || milli === undefined ? "—" : `${(milli / 1000).toFixed(2)}¢`;
const days = (n: number) => (n === 0 ? "instant" : n === 1 ? "1 day" : `${n} days`);

const VERDICTS: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "neutral"; line: string }> = {
  book_with_points: {
    label: "Book with points",
    tone: "ok",
    line: "The best award clears the cost of the points it burns. The sequence below is what to actually do.",
  },
  pay_cash_keep_points: {
    label: "Pay cash, keep your points",
    tone: "warn",
    line: "Every redemption the search found is worth less than the points it would spend. Keeping the balance is the better move — the plans below are shown so you can see why, not so you can book one.",
  },
  cash_plan: {
    label: "Cash out",
    tone: "neutral",
    line: "Ranked by cash actually received, using liquid options only — the travel portal is never counted as cash.",
  },
  insufficient_points: {
    label: "Not enough points",
    tone: "bad",
    line: "No route through the transfer graph is fundable from the balances you hold.",
  },
  no_matching_award: {
    label: "No matching award",
    tone: "bad",
    line: "The pinned dataset holds no award that satisfies this goal. Nothing was invented to fill the gap.",
  },
};

const STEP_KIND: Record<string, string> = {
  transfer: "Transfer",
  book_award: "Book award",
  book_portal: "Book portal",
  redeem_cash: "Cash out",
};

const CAVEAT_TONE: Record<string, "warn" | "bad" | "neutral"> = {
  irreversible_transfer: "bad",
  transfer_time_risk: "warn",
  stranded_points: "warn",
  promo_expiring: "warn",
  below_baseline: "warn",
  stale_world: "warn",
  seats_limited: "neutral",
};

const SUGGESTIONS = [
  "round-trip business NYC to Paris in October",
  "economy NYC to Paris in October",
  "round-trip business SFO to Tokyo in November",
];

/* --------------------------------------------------------------- refusals */

/**
 * The API's refusal envelope is `{code, message, detail}`; the shared client
 * unwraps FastAPI's `detail` first, so the structured code lands in `err.detail`
 * rather than in `err.code`. Dig it out — a refusal that says "Request failed
 * (422)" hides exactly the thing the engine was careful to tell us.
 */
type Refusal = { code: string; message: string; missing: string[]; ambiguous: string[] };

function refusalOf(err: ApiError | undefined): Refusal | undefined {
  if (!err) return undefined;
  const body = err.detail;
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.code === "string") {
      const inner = (b.detail ?? {}) as Record<string, unknown>;
      const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
      return {
        code: b.code,
        message: typeof b.message === "string" ? b.message : err.message,
        missing: list(inner.missing),
        ambiguous: list(inner.ambiguous),
      };
    }
  }
  return { code: err.code, message: err.message, missing: [], ambiguous: [] };
}

function RefusalNote({ error, what }: { error: ApiError | undefined; what: string }) {
  const r = refusalOf(error);
  if (!r) return null;
  return (
    <div className="pm-refusal" role="alert">
      <div className="pm-refusal-head">
        <span className="pm-refusal-title">{what}</span>
        <code className="mono pm-refusal-code">{r.code}</code>
      </div>
      <p className="pm-refusal-msg">{r.message}</p>
      {r.missing.length > 0 ? (
        <p className="pm-refusal-fields">
          Missing:{" "}
          {r.missing.map((f) => (
            <code className="mono" key={f}>
              {f}
            </code>
          ))}
        </p>
      ) : null}
      {r.ambiguous.length > 0 ? (
        <p className="pm-refusal-fields">
          Ambiguous:{" "}
          {r.ambiguous.map((f) => (
            <code className="mono" key={f}>
              {f}
            </code>
          ))}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------- plan pieces */

function goalLabel(g: Goal): string {
  if (g.raw_text) return g.raw_text;
  if (g.kind === "flight") {
    return `${g.round_trip ? "Round-trip" : "One-way"} ${g.cabin ?? "any cabin"} ${g.origin_city ?? "?"}→${
      g.dest_city ?? "?"
    }`;
  }
  if (g.kind === "stay") return `${g.nights ?? "?"} nights in ${g.city ?? "?"}`;
  return "Maximise cash";
}

function routeOf(plan: Plan, names: Record<string, string>): string {
  const hops: string[] = [];
  for (const s of plan.steps) {
    if (s.kind === "transfer" && s.to_program) hops.push(names[s.to_program] ?? s.to_program);
    else if (s.kind === "book_portal" && s.from_program)
      hops.push(`${names[s.from_program] ?? s.from_program} portal`);
    else if (s.kind === "redeem_cash" && s.from_program)
      hops.push(`${names[s.from_program] ?? s.from_program} cash-out`);
  }
  const unique = Array.from(new Set(hops));
  return unique.length ? unique.join(" + ") : "book direct";
}

/** The four integers, laid out so the subtraction can be checked by eye. */
function Equation({ plan }: { plan: Plan }) {
  const isCash = plan.cash_received_cents !== null;
  const terms: { label: string; cents: number; op?: string }[] = [
    { label: isCash ? "Cash received" : "Cash fare avoided", cents: plan.gross_value_cents },
    { label: "Cash you still pay", cents: plan.cash_outlay_cents, op: "−" },
    { label: "Points burned, at baseline", cents: plan.points_cost_cents, op: "−" },
  ];
  const tone = plan.net_value_cents > 0 ? "ok" : plan.net_value_cents < 0 ? "bad" : undefined;
  return (
    <div className="pm-eq">
      {terms.map((t) => (
        <div className="pm-eq-cell" key={t.label}>
          {t.op ? (
            <span className="pm-eq-op" aria-hidden="true">
              {t.op}
            </span>
          ) : null}
          <span className="pm-eq-term">
            <span className="pm-eq-label">{t.label}</span>
            <span className="num pm-eq-value">{money(t.cents)}</span>
          </span>
        </div>
      ))}
      <div className="pm-eq-cell">
        <span className="pm-eq-op" aria-hidden="true">
          =
        </span>
        <span className="pm-eq-term pm-eq-total">
          <span className="pm-eq-label">
            {isCash ? "Net vs keeping the points" : "Net vs paying cash"}
          </span>
          <span className={`num pm-eq-value${tone ? ` tone-${tone}` : ""}`}>
            {plan.net_value_cents > 0 ? "+" : ""}
            {money(plan.net_value_cents)}
          </span>
        </span>
      </div>
    </div>
  );
}

function StepRow({ step, names }: { step: Step; names: Record<string, string> }) {
  const done = step.executed_at !== null;
  const meta: string[] = [];
  if (step.kind === "transfer" && step.points_delivered !== null) {
    meta.push(`sends ${pts(step.points_sent)} → receives ${pts(step.points_delivered)}`);
  } else if (step.points_sent > 0) {
    meta.push(`${pts(step.points_sent)} points`);
  }
  if (step.fees_cents > 0) meta.push(`fee ${money(step.fees_cents)}`);
  else meta.push("no fee");
  meta.push(`posts ${days(step.eta_days)}`);
  const ref = step.edge_id ?? step.offer_id ?? step.cashout_id;

  return (
    <li className={`pm-step${done ? " pm-step-done" : ""}`}>
      <span className="pm-step-seq num" aria-hidden="true">
        {step.seq}
      </span>
      <div className="pm-step-body">
        <div className="pm-step-head">
          <Pill tone={step.kind === "transfer" ? "accent" : "neutral"}>
            {STEP_KIND[step.kind] ?? step.kind}
          </Pill>
          {step.irreversible ? <Pill tone="bad">irreversible</Pill> : null}
          {done ? <Pill tone="ok">recorded</Pill> : null}
        </div>
        <p className="pm-step-text">{step.explanation}</p>
        <p className="pm-step-meta">
          {step.from_program ? (
            <span className="mono">
              {names[step.from_program] ?? step.from_program}
              {step.to_program ? ` → ${names[step.to_program] ?? step.to_program}` : ""}
            </span>
          ) : null}
          {meta.map((m) => (
            <span key={m}>{m}</span>
          ))}
          {ref ? <code className="mono pm-step-ref">{ref}</code> : null}
        </p>
      </div>
    </li>
  );
}

function Caveats({ caveats }: { caveats: Caveat[] }) {
  if (caveats.length === 0) {
    return <p className="pm-none">No caveats — the engine found nothing it needed to warn about.</p>;
  }
  return (
    <ul className="pm-caveats">
      {caveats.map((c, i) => (
        <li className={`pm-caveat pm-caveat-${CAVEAT_TONE[c.code] ?? "neutral"}`} key={`${c.code}-${i}`}>
          <code className="mono pm-caveat-code">{c.code}</code>
          <span className="pm-caveat-text">{c.text}</span>
        </li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------- plan detail */

function PlanDetail({
  plan,
  set,
  names,
  onExecuted,
}: {
  plan: Plan;
  set: PlanSet;
  names: Record<string, string>;
  onExecuted: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const exec = useMutation((planId: number, seq: number, confirm: boolean) =>
    api.post<ExecuteOut>(`/plans/${planId}/steps/${seq}/execute`, { confirm_irreversible: confirm }),
  );

  const next = plan.steps.find((s) => s.executed_at === null);
  const recommended = set.recommended_plan_id === plan.id;
  const spent = Object.entries(plan.points_spent);

  return (
    <div className="pm-detail">
      <div className="pm-detail-head">
        <h3>
          Plan {plan.rank} · {routeOf(plan, names)}
        </h3>
        <div className="pm-chips">
          {recommended ? <Pill tone="ok">recommended</Pill> : null}
          {plan.is_comparator ? <Pill tone="warn">baseline comparator</Pill> : null}
          <Pill tone="neutral">{plan.steps.length} steps</Pill>
        </div>
      </div>

      <Equation plan={plan} />

      <Facts
        items={[
          [
            "Realized value",
            <span className="num">
              {cpp(plan.realized_cpp_milli)} per point
              {plan.cash_received_cents !== null
                ? ` · ${money(plan.cash_received_cents)} in hand`
                : ""}
            </span>,
          ],
          [
            "Points spent",
            spent.length ? (
              <span className="pm-chips">
                {spent.map(([p, n]) => (
                  <span className="pm-chip" key={p}>
                    <span className="mono">{names[p] ?? p}</span>{" "}
                    <span className="num">{pts(n)}</span>
                  </span>
                ))}
              </span>
            ) : (
              "none"
            ),
          ],
          [
            "Ready in",
            <span className="num">
              {plan.feasible_in_days === 0 ? "today — every hop posts instantly" : days(plan.feasible_in_days)}
            </span>,
          ],
          ["Plan signature", <code className="mono pm-hash">{plan.signature.slice(0, 16)}</code>],
        ]}
      />

      <div className="pm-section">
        <h3 className="pm-section-title">The sequence</h3>
        <ol className="pm-steps">
          {plan.steps.map((s) => (
            <StepRow key={s.seq} step={s} names={names} />
          ))}
        </ol>
      </div>

      <div className="pm-section">
        <h3 className="pm-section-title">
          Caveats <span className="pm-section-note">deterministic, one per triggered condition</span>
        </h3>
        <Caveats caveats={plan.caveats} />
      </div>

      <div className="pm-gate">
        <div className="pm-gate-text">
          <h3 className="pm-section-title">Record a step you have actually performed</h3>
          {next ? (
            <p className="pm-gate-hint">
              Next up is step {next.seq}: {next.explanation}
              {next.irreversible
                ? " The API refuses this without explicit confirmation — try it without ticking the box and it will say so."
                : ""}
            </p>
          ) : (
            <p className="pm-gate-hint">Every step in this plan has been recorded.</p>
          )}
        </div>
        {next ? (
          <div className="pm-gate-controls">
            <label className="pm-check">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>I understand step {next.seq} cannot be undone</span>
            </label>
            <Button
              variant="primary"
              pending={exec.pending}
              onClick={() => {
                void exec.run(plan.id, next.seq, confirmed).then((ok) => {
                  if (ok) {
                    setConfirmed(false);
                    onExecuted();
                  }
                });
              }}
            >
              Record step {next.seq}
            </Button>
          </div>
        ) : null}
      </div>
      <RefusalNote error={exec.error} what="The engine refused to record this step" />
      {exec.data ? (
        <p className="pm-ok" role="status">
          Step {exec.data.step.seq} recorded at{" "}
          <span className="mono">{exec.data.step.executed_at ?? "—"}</span>. Balances now:{" "}
          {Object.entries(exec.data.balances)
            .map(([p, n]) => `${names[p] ?? p} ${pts(n)}`)
            .join(", ")}
          .
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------- plan set */

function PlanSetView({
  active,
  names,
  onExecuted,
}: {
  active: Active;
  names: Record<string, string>;
  onExecuted: () => void;
}) {
  const { set, goal } = active;
  const [picked, setPicked] = useState<number | null>(null);
  const verdict = VERDICTS[set.verdict] ?? {
    label: set.verdict,
    tone: "neutral" as const,
    line: "",
  };
  const selectedId = picked ?? set.recommended_plan_id ?? set.plans[0]?.id ?? null;
  const selected = set.plans.find((p) => p.id === selectedId);
  const best = set.plans[0];

  const columns: Column<Plan>[] = [
    { key: "rank", header: "#", render: (p) => <span className="num">{p.rank}</span>, width: "3rem" },
    {
      key: "route",
      header: "Route",
      render: (p) => (
        <span className="pm-route">
          <span>{routeOf(p, names)}</span>
          {p.is_comparator ? <Pill tone="warn">comparator</Pill> : null}
          {p.id === set.recommended_plan_id ? <Pill tone="ok">pick</Pill> : null}
        </span>
      ),
    },
    {
      key: "net",
      header: "Net vs cash",
      numeric: true,
      render: (p) => (
        <span className={p.net_value_cents >= 0 ? "tone-ok" : "tone-bad"}>
          {p.net_value_cents > 0 ? "+" : ""}
          {money(p.net_value_cents)}
        </span>
      ),
    },
    { key: "cpp", header: "Realized", numeric: true, render: (p) => cpp(p.realized_cpp_milli) },
    { key: "out", header: "Out of pocket", numeric: true, render: (p) => money(p.cash_outlay_cents) },
    {
      key: "ready",
      header: "Ready",
      numeric: true,
      render: (p) => (p.feasible_in_days === 0 ? "today" : `${p.feasible_in_days}d`),
    },
  ];

  return (
    <>
      <Panel
        title={goalLabel(goal)}
        hint={`Goal #${goal.id} · ${goal.kind}${
          goal.travel_window_start
            ? ` · travel ${goal.travel_window_start} to ${goal.travel_window_end}`
            : ""
        }${goal.passengers ? ` · ${goal.passengers} pax` : ""}`}
        actions={<Pill tone={verdict.tone}>{set.verdict}</Pill>}
      >
        <div className={`pm-verdict pm-verdict-${verdict.tone}`}>
          <h3>{verdict.label}</h3>
          <p>{verdict.line}</p>
          {best ? (
            <p className="pm-verdict-num">
              Best plan nets{" "}
              <strong className="num">
                {best.net_value_cents > 0 ? "+" : ""}
                {money(best.net_value_cents)}
              </strong>{" "}
              against a <span className="num">{money(best.gross_value_cents)}</span> cash fare, at{" "}
              <span className="num">{cpp(best.realized_cpp_milli)}</span> per point.
            </p>
          ) : null}
        </div>

        <div className="pm-prov">
          <span>
            world <code className="mono">{set.world_version}</code>
          </span>
          <span>
            hash <code className="mono">{set.world_hash.slice(0, 12)}</code>
          </span>
          <span>
            today <span className="num">{set.today}</span>
          </span>
          <span>
            <span className="num">{pts(set.expansions)}</span> search expansions
          </span>
          <span>
            max hops <span className="num">{set.params.max_hops ?? "—"}</span>
          </span>
        </div>

        <Table
          columns={columns}
          rows={set.plans}
          rowKey={(p) => String(p.id)}
          onRowClick={(p) => setPicked(p.id)}
          selectedKey={selectedId === null ? undefined : String(selectedId)}
          caption="Ranked plans for this goal"
        />
        <p className="pm-disclaimer">{set.disclaimer}</p>
      </Panel>

      {selected ? (
        <Panel title="Plan detail" hint="Every step as the engine would have you execute it">
          {/* keyed so the irreversibility confirmation never carries across plans */}
          <PlanDetail key={selected.id} plan={selected} set={set} names={names} onExecuted={onExecuted} />
        </Panel>
      ) : (
        <Panel title="Plan detail">
          <State kind="empty" title="This plan set has no plans" detail="Nothing was feasible for this goal." />
        </Panel>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ screen */

export default function PointsMax() {
  const wallet = useQuery(() => api.get<Wallet>("/wallet"), []);
  const world = useQuery(() => api.get<World>("/world"), []);
  const programs = useQuery(() => api.get<Program[]>("/programs"), []);
  const goals = useQuery(() => api.get<Goal[]>("/goals"), []);

  const [text, setText] = useState(SUGGESTIONS[0]);
  const [topK, setTopK] = useState("5");
  const [maxHops, setMaxHops] = useState("2");
  const [asOf, setAsOf] = useState("");
  const [active, setActive] = useState<Active | undefined>(undefined);
  const [tab, setTab] = useState("balances");

  const names = useMemo(() => {
    const out: Record<string, string> = {};
    for (const p of programs.data ?? []) out[p.id] = p.name;
    return out;
  }, [programs.data]);

  const planParams = () => ({
    top_k: Number(topK),
    max_hops: Number(maxHops),
    ...(asOf ? { today: asOf } : {}),
  });

  const compose = useMutation(async (body: Record<string, unknown>) => {
    const goal = await api.post<Goal>("/goals", { ...body, ...(asOf ? { today: asOf } : {}) });
    const set = await api.post<PlanSet>(`/goals/${goal.id}/plans`, planParams());
    return { goal, set };
  });

  const open = useMutation(async (goal: Goal) => {
    const sets = await api.get<PlanSet[]>(`/goals/${goal.id}/plans`, { latest: 1 });
    const set = sets[0] ?? (await api.post<PlanSet>(`/goals/${goal.id}/plans`, planParams()));
    return { goal, set };
  });

  const refresh = () => {
    wallet.reload();
    if (!active) return;
    void api.get<PlanSet>(`/plan-sets/${active.set.id}`).then((set) => {
      setActive({ goal: active.goal, set });
    });
  };

  const submitText = (value: string) => {
    if (!value.trim()) return;
    setText(value);
    void compose.run({ text: value }).then((r) => {
      if (r) {
        setActive(r);
        goals.reload();
      }
    });
  };

  const submitCash = () => {
    void compose.run({ kind: "cash" }).then((r) => {
      if (r) {
        setActive(r);
        goals.reload();
      }
    });
  };

  const pending = compose.pending || open.pending;

  const balanceColumns: Column<Balance>[] = [
    {
      key: "program",
      header: "Program",
      render: (b) => (
        <span>
          {b.program_name}
          <br />
          <code className="mono pm-sub">{b.program_id}</code>
        </span>
      ),
    },
    { key: "points", header: "Points", numeric: true, render: (b) => pts(b.points) },
    {
      key: "baseline",
      header: "Baseline",
      numeric: true,
      render: (b) => (
        <span>
          {money(b.value.baseline_value_cents)}
          <br />
          <span className="pm-sub">{cpp(b.value.baseline_cpp_milli)}/pt</span>
        </span>
      ),
    },
    {
      key: "cash",
      header: "Cash floor",
      numeric: true,
      render: (b) => (
        <span>
          {money(b.value.cash_floor_cents)}
          <br />
          <code className="mono pm-sub">{b.value.cash_floor_option_id ?? "none"}</code>
        </span>
      ),
    },
    {
      key: "travel",
      header: "Travel floor",
      numeric: true,
      render: (b) => (
        <span>
          {money(b.value.travel_floor_cents)}
          <br />
          <code className="mono pm-sub">{b.value.travel_floor_option_id ?? "none"}</code>
        </span>
      ),
    },
  ];

  const edgeColumns: Column<Edge>[] = [
    {
      key: "to",
      header: "Partner",
      render: (e) => (
        <span>
          {names[e.to_program] ?? e.to_program}
          <br />
          <code className="mono pm-sub">{e.id}</code>
        </span>
      ),
    },
    {
      key: "ratio",
      header: "Ratio",
      numeric: true,
      render: (e) => `${e.ratio_from}:${e.ratio_to}`,
    },
    {
      key: "grain",
      header: "Min / increment",
      numeric: true,
      render: (e) => `${pts(e.min_from)} / ${pts(e.increment_from)}`,
    },
    {
      key: "fee",
      header: "Fee",
      numeric: true,
      render: (e) =>
        e.fee_mcpp === 0
          ? "none"
          : `${(e.fee_mcpp / 1000).toFixed(2)}¢/pt${
              e.fee_cap_cents !== null ? ` cap ${money(e.fee_cap_cents)}` : ""
            }`,
    },
    { key: "time", header: "Posts", numeric: true, render: (e) => days(e.time_days) },
    {
      key: "bonus",
      header: "Tier bonus",
      render: (e) =>
        e.bonus_per_from && e.bonus_to
          ? `+${pts(e.bonus_to)} per ${pts(e.bonus_per_from)}`
          : "—",
    },
  ];

  const goalColumns: Column<Goal>[] = [
    { key: "id", header: "#", numeric: true, render: (g) => g.id, width: "3rem" },
    { key: "what", header: "Goal", render: (g) => goalLabel(g) },
    { key: "kind", header: "Kind", render: (g) => <Pill tone="neutral">{g.kind}</Pill> },
    { key: "status", header: "Status", render: (g) => <Pill tone={g.status === "planned" ? "ok" : "neutral"}>{g.status}</Pill> },
  ];

  return (
    <Page
      title="PointsMax"
      lede="A deterministic search over a versioned rewards dataset: which points to move, where, in what order — and the honest arithmetic against simply paying cash."
    >
      <Async query={wallet}>
        {(w) => {
          const totalPoints = w.balances.reduce((n, b) => n + b.points, 0);
          const spread = w.baseline_total_cents - w.cash_floor_total_cents;
          const walletPrograms = new Set(w.balances.map((b) => b.program_id));
          const edges = (programs.data ?? [])
            .filter((p) => walletPrograms.has(p.id))
            .flatMap((p) => p.active_edges_out);
          return (
            <>
              <StatRow>
                <Stat
                  label="Baseline value"
                  value={money(w.baseline_total_cents)}
                  sub={`${pts(totalPoints)} points, ${w.balances.length} programs`}
                />
                <Stat
                  label="Cash floor"
                  value={money(w.cash_floor_total_cents)}
                  sub="best liquid cash-out"
                />
                <Stat
                  label="Travel floor"
                  value={money(w.travel_floor_total_cents)}
                  sub="best guaranteed redemption"
                />
                <Stat
                  label="Upside over cash"
                  value={money(spread)}
                  sub="what a good redemption has to earn"
                  tone="ok"
                />
              </StatRow>

              <Panel
                title="Wallet"
                hint="Three numbers, never one. The travel portal is a floor, not cash — it is never reported as cash."
                actions={
                  <div className="pm-chips">
                    {w.cards.map((c) => (
                      <span className="pm-chip" key={c.id}>
                        {c.name}
                        <span className="pm-sub"> · {c.issuer}</span>
                        {c.enables_transfer ? <Pill tone="accent">transfers</Pill> : null}
                      </span>
                    ))}
                  </div>
                }
              >
                <Tabs
                  tabs={[
                    { id: "balances", label: "Balances" },
                    { id: "partners", label: `Transfer partners you can use${edges.length ? ` (${edges.length})` : ""}` },
                  ]}
                  active={tab}
                  onChange={setTab}
                />
                {tab === "balances" ? (
                  <Table
                    columns={balanceColumns}
                    rows={w.balances}
                    rowKey={(b) => b.program_id}
                    caption="Balances with baseline, cash floor and travel floor"
                  />
                ) : (
                  <Async
                    query={programs}
                    emptyWhen={() => edges.length === 0}
                    empty={{
                      title: "No active transfer edges",
                      detail: "Every edge from your programs is gated on a card you do not hold, or its promo window has closed.",
                    }}
                  >
                    {() => (
                      <>
                        <p className="pm-note">
                          Only edges your cards actually unlock, at the ratios and increments the dataset
                          records. Card gating is applied before search, so a plan can never route through a
                          partner you cannot reach.
                        </p>
                        <Table
                          columns={edgeColumns}
                          rows={edges}
                          rowKey={(e) => e.id}
                          caption="Active transfer edges out of your programs"
                        />
                      </>
                    )}
                  </Async>
                )}
              </Panel>
            </>
          );
        }}
      </Async>

      <Panel title="What do you want?" hint="Plain words in, a structured goal out — or a refusal naming exactly what it could not resolve.">
        <form
          className="pm-compose"
          onSubmit={(e) => {
            e.preventDefault();
            submitText(text);
          }}
        >
          <Field label="Your goal">
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="round-trip business NYC to Paris in October"
            />
          </Field>
          <div className="pm-compose-row">
            <Field label="Plan as of" hint="blank = today">
              <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
            </Field>
            <Field label="Plans to rank">
              <Select value={topK} onChange={(e) => setTopK(e.target.value)}>
                <option value="3">3</option>
                <option value="5">5</option>
                <option value="8">8</option>
              </Select>
            </Field>
            <Field label="Transfer hops" hint="2 allows bank → hotel → airline">
              <Select value={maxHops} onChange={(e) => setMaxHops(e.target.value)}>
                <option value="1">1</option>
                <option value="2">2</option>
                <option value="3">3</option>
              </Select>
            </Field>
            <div className="pm-compose-actions">
              <Button type="submit" variant="primary" pending={compose.pending} disabled={!text.trim()}>
                Search
              </Button>
              <Button onClick={submitCash} disabled={pending}>
                Maximise cash
              </Button>
            </div>
          </div>
        </form>

        <div className="pm-suggest">
          <span className="pm-suggest-label">Try</span>
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="pm-pick" onClick={() => submitText(s)}>
              {s}
            </button>
          ))}
          <button type="button" className="pm-pick" onClick={() => submitText("go somewhere nice")}>
            go somewhere nice <span className="pm-sub">(watch it refuse)</span>
          </button>
        </div>

        <RefusalNote error={compose.error} what="The parser could not build a goal" />
        <RefusalNote error={open.error} what="Could not load plans for that goal" />

        <div className="pm-section">
          <h3 className="pm-section-title">Earlier goals</h3>
          <Async
            query={goals}
            emptyWhen={(g) => g.length === 0}
            empty={{ title: "No goals yet", detail: "Ask for something above and it will be saved here." }}
          >
            {(list) => (
              <Table
                columns={goalColumns}
                rows={[...list].reverse().slice(0, 8)}
                rowKey={(g) => String(g.id)}
                selectedKey={active ? String(active.goal.id) : undefined}
                onRowClick={(g) => {
                  void open.run(g).then((r) => {
                    if (r) setActive(r);
                  });
                }}
                caption="Goals saved on this profile"
              />
            )}
          </Async>
        </div>
      </Panel>

      {pending ? <State kind="loading" title="Searching the transfer graph…" detail="Exhaustive within an explicit expansion budget — no sampling, no guessing." /> : null}

      {/* keyed on the plan set so a new search never leaves a stale row selected */}
      {active && !pending ? (
        <PlanSetView key={active.set.id} active={active} names={names} onExecuted={refresh} />
      ) : null}

      <Panel
        title="What this will not promise"
        hint="The refusals are part of the answer, not a disclaimer at the bottom"
      >
        <ul className="pm-limits">
          <li>
            <strong>Award seats are quoted, never claimed.</strong> Availability comes from a committed
            snapshot. When an offer shows fewer seats than you need, the plan carries a{" "}
            <code className="mono">seats_limited</code> caveat with the seat count — the engine will not
            assert a seat it cannot verify, and it will not silently drop the plan either.
          </li>
          <li>
            <strong>Transfers are final.</strong> Every transfer and award booking is flagged irreversible
            and refuses to execute without explicit confirmation — the API answers{" "}
            <code className="mono">confirmation_required</code> (428) rather than guessing your intent.
          </li>
          <li>
            <strong>Plans are pinned to the data that produced them.</strong> Each plan set stores the world
            version and content hash; executing against a different dataset is refused with a re-plan hint.
          </li>
          <li>
            <strong>Valuations age.</strong> A dataset older than its staleness threshold raises a{" "}
            <code className="mono">stale_world</code> caveat instead of being quietly refreshed.
          </li>
        </ul>
        <Async query={world}>
          {(w) => (
            <Facts
              items={[
                [
                  "Dataset",
                  <span>
                    <code className="mono">v{w.version}</code> as of{" "}
                    <span className="num">{w.as_of}</span> ·{" "}
                    <code className="mono pm-hash">{w.content_hash.slice(0, 12)}</code>
                  </span>,
                ],
                [
                  "Age",
                  <span className="num">
                    {w.days_old} days{" "}
                    {w.stale ? (
                      <Pill tone="bad">stale · over {w.stale_days}d</Pill>
                    ) : (
                      <Pill tone="ok">within {w.stale_days}d</Pill>
                    )}
                  </span>,
                ],
                [
                  "Coverage",
                  <span className="num">
                    {w.counts.programs} programs · {w.counts.transfer_edges} transfer edges ·{" "}
                    {w.counts.award_offers} award offers · {w.counts.reference_fares} reference fares
                  </span>,
                ],
                ["Valuations as of", <span className="num">{w.valuations_as_of}</span>],
              ]}
            />
          )}
        </Async>
      </Panel>
    </Page>
  );
}
