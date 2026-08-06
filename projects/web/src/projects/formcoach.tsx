/**
 * FormCoach — a mesocycle that progresses on evidence, and form review that
 * states what it measured, what it could not see, and nothing about your body.
 *
 * The hard part of this product is honest limits, so the screen leads with them
 * rather than tucking them into fine print:
 *
 *  - The disclaimer is a gate, not a banner. Until it is acknowledged the API
 *    answers `409 disclaimer_not_acknowledged`, and the UI says so in those words.
 *  - Every next-session prescription shows the single progression clause that
 *    fired (L1–L6 loaded, B1–B6 bodyweight) next to its rationale and the load
 *    it implies — "why this weight" is the answer, not a number alone.
 *  - Form findings are rendered in three buckets, never two: fault, ok, and
 *    *not assessed with a reason*. A clip below the visibility floor comes back
 *    rejected with the frame count that failed it, and that is shown as a
 *    designed outcome rather than an error.
 *  - Logging a painful set surfaces the stop-and-refer recommendation verbatim.
 *    It names no cause, and neither does this screen.
 *
 * API note: there is no `GET /programs` list route, so the active program is
 * discovered by walking the sequential ids until one 404s.
 */

import { useCallback, useMemo, useState } from "react";
import { ApiError, client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import type { QueryState } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Field, Grid, Input, Meter, Page, Panel, Pill,
  Select, Split, Stat, StatRow, State, Table, Tabs,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./formcoach.css";

const api = client("formcoach");

/* ------------------------------------------------------------------- shapes */

type Profile = {
  goal: string;
  experience: string;
  days_per_week: number;
  equipment: string[];
  emphasized_muscles: string[];
  unit: string;
  disclaimer_acknowledged_at: string | null;
  pain_flags: string[];
  updated_at: string;
};

type Exercise = {
  id: string;
  name: string;
  primary_muscles: string[];
  pattern: string;
  analyzable: boolean;
  form_profile_id: string | null;
};

type Program = {
  id: number;
  created_at: string;
  seed: number;
  goal: string;
  experience: string;
  days_per_week: number;
  emphasized_muscles: string[];
  equipment: string[];
  target_muscles: string[];
  weeks: number;
  split: string;
  status: string;
  weekly_set_targets: Record<string, number[]>;
};

type Prescription = {
  position: number;
  exercise_id: string;
  sets: number;
  rep_low: number;
  rep_high: number;
  target_rir: number;
  rest_s: number;
  load_note: string | null;
};

type Session = {
  id: number | null;
  week: number;
  day_index: number;
  name: string;
  prescriptions: Prescription[];
};

type NextPrescription = Prescription & {
  action: string;
  clause: string;
  suggested_load_kg: number | null;
  target_reps: number;
  substituted_for: string | null;
  dropped_reason: string | null;
  rationale: string;
};

type NextSession = {
  program_id: number | null;
  week: number;
  day_index: number;
  name: string;
  prescriptions: NextPrescription[];
  dropped: { exercise_id: string; reason: string }[];
};

type SetOut = {
  exercise_id: string;
  set_index: number;
  weight_kg: number;
  reps: number;
  rir: number | null;
  pain_flag: boolean;
  e1rm_kg: number | null;
};

type WorkoutCreated = {
  workout: { id: number | null; performed_at: string; sets: SetOut[] };
  pain_flagged: string[];
  recommendation: string | null;
};

type VolumeRow = {
  muscle: string;
  effective_sets: number;
  direct_sets: number;
  band: string;
  is_target: boolean;
  mv: number;
  mev: number;
  mav: number;
  mrv: number;
};

type VolumeReport = { iso_week: string; rows: VolumeRow[] };

type Finding = {
  fault_id: string;
  status: string;
  not_assessed_reason: string | null;
  measured: number | null;
  threshold: number;
  severity: string;
  frame: number | null;
  cue: string | null;
};

type Rep = {
  rep_index: number;
  start_frame: number;
  extremum_frame: number;
  end_frame: number;
  metrics: Record<string, number>;
  score: number;
  findings: Finding[];
};

type Analysis = {
  id: number;
  created_at: string;
  analysis_kind: string;
  exercise_id: string;
  form_profile_id: string;
  source_ref: string;
  pose_source: string;
  view: string;
  view_inferred: boolean;
  fps: number | null;
  frames_total: number;
  frames_valid: number;
  status: string;
  reject_reason: string | null;
  rep_count: number | null;
  clip_score: number | null;
};

type Correction = { fault_id: string; severity: string; occurrences: number; cue: string };

type AnalysisDetail = Analysis & { reps: Rep[]; corrections: Correction[] };

const SEVERITY_RANK: Record<string, number> = { major: 0, moderate: 1, minor: 2 };

/**
 * `POST /form/analyses` returns the ordered correction list; re-reading the same
 * analysis with `GET /form/analyses/{id}` returns `corrections: []` even when the
 * reps carry faults. Rather than showing a clip as clean the second time it is
 * opened, rebuild the list from the findings it already returned — same faults,
 * same cues, same severity-then-frequency ordering the engine uses.
 */
function deriveCorrections(reps: Rep[]): Correction[] {
  const found = new Map<string, Correction>();
  for (const rep of reps) {
    for (const finding of rep.findings) {
      if (finding.status !== "fault") continue;
      const existing = found.get(finding.fault_id);
      if (existing) existing.occurrences += 1;
      else
        found.set(finding.fault_id, {
          fault_id: finding.fault_id,
          severity: finding.severity,
          occurrences: 1,
          cue: finding.cue ?? "",
        });
    }
  }
  return [...found.values()].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9) ||
      b.occurrences - a.occurrences,
  );
}

/* ------------------------------------------------------------------ helpers */

const nowIso = () => new Date().toISOString();

const words = (value: string) => value.replace(/_/g, " ");

function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function num(value: number, digits = 0): string {
  return value.toFixed(digits);
}

const ACTION_TONE: Record<string, "ok" | "warn" | "bad" | "neutral" | "accent"> = {
  increase_load: "ok",
  add_set: "ok",
  add_reps: "ok",
  progress_variation: "accent",
  hold: "neutral",
  decrease_load: "warn",
  deload_recommend: "warn",
};

const BAND_PILL: Record<string, "ok" | "warn" | "bad" | "neutral" | "accent"> = {
  below_mev: "warn",
  mev_mav: "ok",
  mav_mrv: "accent",
  above_mrv: "bad",
};

const BAND_METER: Record<string, "ok" | "warn" | "bad"> = {
  below_mev: "warn",
  mev_mav: "ok",
  mav_mrv: "ok",
  above_mrv: "bad",
};

const SEVERITY_TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = {
  major: "bad",
  moderate: "warn",
  minor: "neutral",
};

/** Why a fault was left unscored. The engine never guesses in these cases. */
const NOT_ASSESSED: Record<string, string> = {
  view_mismatch: "this camera view cannot show it",
  keypoints_not_visible: "the joints it needs were not visible often enough",
  phase_not_shown: "the phase it lives in is not in the frame",
  needs_multi_frame: "a single photo cannot establish it",
};

/** The FR-6 ladder. Exactly one clause fires per exercise; the first match wins. */
const LOADED_LADDER: [string, string][] = [
  ["L1", "Estimated 1RM fell more than 5% for two sessions running — deload to 90%."],
  ["L2", "A set finished 2+ reps under the range — repeat the load."],
  ["L3", "Every set hit the top of the range at or above target RIR — add load."],
  ["L4", "Average RIR sat more than one below target — take 5% off."],
  ["L5", "Average RIR sat more than one above target — add 2.5%."],
  ["L6", "Reps and effort are on target, or there is no history yet — hold."],
];

const BODYWEIGHT_LADDER: [string, string][] = [
  ["B1", "Total reps fell more than 10% for two sessions running — take an easier week."],
  ["B2", "A set finished 2+ reps under the range — repeat the session."],
  ["B3", "Topped out and a harder variation exists — step up and restart at the bottom."],
  ["B4", "Topped out with no harder variation — add a set."],
  ["B5", "Five sets at the top of the range is the ceiling — hold."],
  ["B6", "Otherwise — add a rep."],
];

const FIXTURE_DIR = "/home/user/orbit_new/projects/formcoach/evals/fixtures/poses";

const PRESETS: { label: string; exercise: string; view: string; file: string; note: string }[] = [
  {
    label: "Squat from the side",
    exercise: "barbell-back-squat",
    view: "side_left",
    file: "squat_side_02.keypoints.json",
    note: "Depth and trunk lean are measurable here. Knee valgus is not — the side view cannot show it.",
  },
  {
    label: "The same squat from the front",
    exercise: "barbell-back-squat",
    view: "front",
    file: "squat_front_03.keypoints.json",
    note: "Now lateral shift is measurable and depth is not. The assessable set follows the camera.",
  },
  {
    label: "Push-up, view left to the engine",
    exercise: "push-up",
    view: "side",
    file: "pushup_side_01.keypoints.json",
    note: "Declared only as “side”; FR-7 resolves which side and marks the view as inferred.",
  },
  {
    label: "A clip it refuses to read",
    exercise: "barbell-deadlift",
    view: "side_right",
    file: "invalid_03.keypoints.json",
    note: "Below the visibility floor. Expect a rejection with the frame count, not a score.",
  },
];

/** The newest program, or null when none exist. */
async function findProgram(): Promise<Program | null> {
  const programs = await api.get<Program[]>("/programs");
  return programs.length ? programs[programs.length - 1] : null;
}

type Boot = { profile: Profile | null; exercises: Exercise[]; program: Program | null };

async function bootstrap(): Promise<Boot> {
  const exercises = await api.get<Exercise[]>("/exercises");
  let profile: Profile | null = null;
  try {
    profile = await api.get<Profile>("/profile");
  } catch (err) {
    if (!(err instanceof ApiError) || err.code !== "profile_not_found") throw err;
  }
  const program = profile ? await findProgram() : null;
  return { profile, exercises, program };
}

/* -------------------------------------------------------------- safety panel */

function SafetyPanel({
  profile,
  nameOf,
  onChange,
}: {
  profile: Profile;
  nameOf: (id: string) => string;
  onChange: () => void;
}) {
  const acknowledge = useMutation(() =>
    api.post<Profile>("/profile/acknowledge-disclaimer", { acknowledged_at: nowIso() }),
  );
  const clearFlag = useMutation((exerciseId: string) =>
    api.del<Profile>(
      `/profile/pain-flags/${encodeURIComponent(exerciseId)}?cleared_at=${encodeURIComponent(nowIso())}`,
    ),
  );
  const acknowledged = Boolean(profile.disclaimer_acknowledged_at);

  return (
    <Panel
      title="Not a medical device"
      hint="These limits are enforced by the engine, not asserted by the copy."
      actions={
        acknowledged ? (
          <Pill tone="ok">gate cleared</Pill>
        ) : (
          <Pill tone="bad">programming locked</Pill>
        )
      }
    >
      <div className="limits">
        <article className="limit">
          <h3>It never says what is wrong with you</h3>
          <p>
            Form reports name movement faults — depth, trunk lean, valgus, shift. A set logged
            with pain returns a stop-and-refer recommendation and flags the exercise. It does not
            offer a cause, and no cue or error message is allowed to contain diagnosis vocabulary.
          </p>
        </article>
        <article className="limit">
          <h3>It refuses clips it cannot read</h3>
          <p>
            Below the visibility floor an analysis comes back <code className="mono">rejected</code>{" "}
            with the reason and the count of usable frames. A refusal is a result here, not a
            failure to render.
          </p>
        </article>
        <article className="limit">
          <h3>It only judges what the camera showed</h3>
          <p>
            Faults the declared view or the visible joints cannot support come back{" "}
            <code className="mono">not_assessed</code> with a reason, and are excluded from the
            score rather than assumed to be fine.
          </p>
        </article>
      </div>

      {acknowledged ? (
        <div className="gate gate-ok">
          <div className="gate-text">
            <strong>Disclaimer acknowledged {shortDate(profile.disclaimer_acknowledged_at)}.</strong>{" "}
            Program generation is unlocked. Before that,{" "}
            <code className="mono">POST /programs</code> answers{" "}
            <code className="mono">409 disclaimer_not_acknowledged</code> — the gate lives in the
            API, so no client can route around it.
          </div>
        </div>
      ) : (
        <div className="gate gate-block">
          <div className="gate-text">
            <strong>FormCoach will not write you a program yet.</strong> Training advice from this
            tool is not medical advice and it cannot assess injury. Acknowledge that and program
            generation unlocks; until then <code className="mono">POST /programs</code> returns{" "}
            <code className="mono">409 disclaimer_not_acknowledged</code>.
          </div>
          <Button
            variant="primary"
            pending={acknowledge.pending}
            onClick={() => {
              void acknowledge.run().then((result) => {
                if (result) onChange();
              });
            }}
          >
            I understand — this is not medical advice
          </Button>
          <ErrorNote error={acknowledge.error} />
        </div>
      )}

      <div className="flags">
        <h3 className="sub">Pain flags</h3>
        {profile.pain_flags.length === 0 ? (
          <p className="muted">
            Nothing is flagged. Logging a set with pain flags that exercise, returns the
            stop-and-refer recommendation, and substitutes the movement out of later prescriptions
            until you clear the flag by hand — there is no automatic expiry.
          </p>
        ) : (
          <ul className="flag-list">
            {profile.pain_flags.map((id) => (
              <li className="flag" key={id}>
                <span className="flag-name">
                  {nameOf(id)} <code className="mono">{id}</code>
                </span>
                <span className="flag-note">
                  Substituted out of every new prescription until cleared.
                </span>
                <Button
                  variant="danger"
                  size="sm"
                  pending={clearFlag.pending}
                  onClick={() => {
                    void clearFlag.run(id).then((result) => {
                      if (result) onChange();
                    });
                  }}
                >
                  Clear flag
                </Button>
              </li>
            ))}
          </ul>
        )}
        <ErrorNote error={clearFlag.error} />
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ mesocycle */

function SessionCard({
  session,
  columns,
}: {
  session: Session;
  columns: Column<Prescription>[];
}) {
  const totalSets = session.prescriptions.reduce((sum, p) => sum + p.sets, 0);
  return (
    <article className="session">
      <header className="session-head">
        <h3>{session.name}</h3>
        <span className="session-day">Day {session.day_index + 1}</span>
        <Pill>{totalSets} sets</Pill>
      </header>
      <Table
        columns={columns}
        rows={session.prescriptions}
        rowKey={(p) => `${session.week}-${session.day_index}-${p.position}`}
        caption={`${session.name}, week ${session.week}`}
      />
    </article>
  );
}

function Mesocycle({
  program,
  nameOf,
}: {
  program: Program;
  nameOf: (id: string) => string;
}) {
  const [week, setWeek] = useState(1);
  const sessions = useQuery(
    () =>
      Promise.all(
        Array.from({ length: program.days_per_week }, (_, day) =>
          api.get<Session>(`/programs/${program.id}/sessions/${week}/${day}`),
        ),
      ),
    [program.id, week],
  );

  const columns = useMemo<Column<Prescription>[]>(
    () => [
      {
        key: "position",
        header: "#",
        numeric: true,
        width: "2.2rem",
        render: (p) => p.position + 1,
      },
      {
        key: "movement",
        header: "Movement",
        render: (p) => (
          <span className="mv">
            <span className="mv-name">{nameOf(p.exercise_id)}</span>
            <code className="mv-id mono">{p.exercise_id}</code>
          </span>
        ),
      },
      { key: "sets", header: "Sets", numeric: true, render: (p) => p.sets },
      {
        key: "reps",
        header: "Reps",
        numeric: true,
        render: (p) => `${p.rep_low}–${p.rep_high}`,
      },
      { key: "rir", header: "RIR", numeric: true, render: (p) => num(p.target_rir) },
      { key: "rest", header: "Rest", numeric: true, render: (p) => `${p.rest_s}s` },
    ],
    [nameOf],
  );

  const isDeload = week === program.weeks;

  return (
    <Panel
      title="The mesocycle"
      hint={`${words(program.split)} · ${program.days_per_week} days a week · ${program.weeks} weeks · seed ${program.seed}`}
      actions={isDeload ? <Pill tone="warn">deload week</Pill> : null}
    >
      <Tabs
        tabs={Array.from({ length: program.weeks }, (_, i) => ({
          id: String(i + 1),
          label: i + 1 === program.weeks ? `Week ${i + 1} · deload` : `Week ${i + 1}`,
        }))}
        active={String(week)}
        onChange={(id) => setWeek(Number(id))}
      />

      <Async
        query={sessions}
        emptyWhen={(rows) => rows.length === 0}
        empty={{ title: "No sessions in this week" }}
      >
        {(rows) => {
          const rirs = Array.from(
            new Set(rows.flatMap((s) => s.prescriptions.map((p) => p.target_rir))),
          ).sort((a, b) => a - b);
          return (
            <>
              <p className="week-note">
                {isDeload ? (
                  <>
                    Week {week} halves every set target and backs the intensity off to RIR{" "}
                    <strong>{rirs.map((r) => num(r)).join(", ")}</strong>. The deload is prescribed,
                    not earned — it is in the plan from the day it is generated.
                  </>
                ) : (
                  <>
                    Week {week} runs at RIR <strong>{rirs.map((r) => num(r)).join(", ")}</strong> —
                    the ramp tightens from 3 down to 1 across the four accumulation weeks, so the
                    same sets get progressively harder before the deload.
                  </>
                )}
              </p>
              <Grid min="25rem">
                {rows.map((session) => (
                  <SessionCard
                    key={`${session.week}-${session.day_index}`}
                    session={session}
                    columns={columns}
                  />
                ))}
              </Grid>
            </>
          );
        }}
      </Async>
    </Panel>
  );
}

/* --------------------------------------------------------- weekly set targets */

type TargetRow = { muscle: string; weeks: number[] };

function WeeklyTargets({ program }: { program: Program }) {
  const rows = useMemo<TargetRow[]>(
    () =>
      Object.entries(program.weekly_set_targets).map(([muscle, weeks]) => ({
        muscle,
        weeks,
      })),
    [program.weekly_set_targets],
  );

  const columns = useMemo<Column<TargetRow>[]>(() => {
    const first: Column<TargetRow> = {
      key: "muscle",
      header: "Muscle",
      render: (row) => (
        <span className="muscle-cell">
          {words(row.muscle)}
          {program.emphasized_muscles.includes(row.muscle) ? (
            <Pill tone="accent">emphasis</Pill>
          ) : null}
        </span>
      ),
    };
    const weeks = Array.from({ length: program.weeks }, (_, i) => ({
      key: `w${i + 1}`,
      header: i + 1 === program.weeks ? `W${i + 1} deload` : `W${i + 1}`,
      numeric: true,
      render: (row: TargetRow) => (
        <span className={i + 1 === program.weeks ? "deload-cell" : undefined}>
          {row.weeks[i] ?? "—"}
        </span>
      ),
    }));
    return [first, ...weeks];
  }, [program.emphasized_muscles, program.weeks]);

  const flat = rows.every((r) => r.weeks.slice(0, program.weeks - 1).every((v) => v === r.weeks[0]));

  return (
    <Panel
      title="Why these sets"
      hint="Weekly effective-set targets, per muscle, for the whole mesocycle"
    >
      <p className="muted">
        Targets come from committed per-muscle volume landmarks (MV / MEV / MAV / MRV), not from a
        template. Every target muscle is hit at least twice a week, and week {program.weeks} halves
        the lot.{" "}
        {flat
          ? `Weeks 1–${program.weeks - 1} are flat here because the weekly set budget binds from
             week one at ${program.days_per_week} days — the accumulation ramp only has headroom at
             five and six days.`
          : "The accumulation weeks ramp because the weekly budget leaves headroom at this many days."}
      </p>
      <Table
        columns={columns}
        rows={rows}
        rowKey={(row) => row.muscle}
        caption="Weekly effective-set targets per muscle"
      />
    </Panel>
  );
}

/* ------------------------------------------------------------- next + why */

function NextSessionPanel({
  query,
  nameOf,
}: {
  query: QueryState<NextSession | null>;
  nameOf: (id: string) => string;
}) {
  return (
    <Panel
      title="What you do next, and why"
      hint="One clause fires per exercise — the first that matches — and it is reported with the load it implies"
    >
      <Async
        query={query}
        emptyWhen={(value) => value === null}
        empty={{
          title: "The mesocycle is finished",
          detail: (
            <>
              <code className="mono">GET /programs/&#123;id&#125;/next</code> answers{" "}
              <code className="mono">404 program_complete</code> rather than inventing a week six.
            </>
          ),
        }}
      >
        {(value) => {
          if (!value) return null;
          const data = value;
          const fired = new Set(data.prescriptions.map((p) => p.clause));
          const usesBodyweight = data.prescriptions.some((p) => p.clause.startsWith("B"));
          return (
            <>
              <div className="next-head">
                <div>
                  <h3 className="next-title">{data.name}</h3>
                  <p className="muted">
                    Week {data.week}, day {data.day_index + 1} of the mesocycle
                  </p>
                </div>
                <Pill tone="accent">RIR {num(data.prescriptions[0]?.target_rir ?? 0)}</Pill>
              </div>

              {data.dropped.length > 0 ? (
                <div className="dropped" role="status">
                  <strong>Dropped from this session.</strong> No substitute in your equipment list
                  could cover it:
                  <ul>
                    {data.dropped.map((d) => (
                      <li key={d.exercise_id}>
                        {nameOf(d.exercise_id)} — {words(d.reason)}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <ol className="next-list">
                {data.prescriptions.map((p) => (
                  <li className="next-item" key={p.position}>
                    <div className="next-item-head">
                      <span className="next-name">{nameOf(p.exercise_id)}</span>
                      <Pill tone={ACTION_TONE[p.action] ?? "neutral"}>{words(p.action)}</Pill>
                      <code className="clause mono">{p.clause}</code>
                    </div>
                    {p.substituted_for ? (
                      <p className="sub-note">
                        Substituted for <strong>{nameOf(p.substituted_for)}</strong>, which is
                        flagged for pain.
                      </p>
                    ) : null}
                    <p className="next-meta mono">
                      {p.sets} × {p.target_reps} reps @ RIR {num(p.target_rir)} · rest {p.rest_s}s ·{" "}
                      {p.suggested_load_kg != null
                        ? `suggested ${num(p.suggested_load_kg, 1)} kg`
                        : "load your call"}
                    </p>
                    <p className="next-why">{p.rationale}</p>
                  </li>
                ))}
              </ol>

              <details className="ladder-wrap">
                <summary>The whole ladder, and which rungs fired</summary>
                <div className="ladder">
                  <h4>Loaded movements</h4>
                  {LOADED_LADDER.map(([code, text]) => (
                    <div className={`ladder-row${fired.has(code) ? " fired" : ""}`} key={code}>
                      <code className="mono">{code}</code>
                      <span>{text}</span>
                    </div>
                  ))}
                  <h4>Bodyweight movements</h4>
                  {BODYWEIGHT_LADDER.map(([code, text]) => (
                    <div className={`ladder-row${fired.has(code) ? " fired" : ""}`} key={code}>
                      <code className="mono">{code}</code>
                      <span>{text}</span>
                    </div>
                  ))}
                  {usesBodyweight ? null : (
                    <p className="muted">
                      No bodyweight clause fired this session — every movement in it takes external
                      load.
                    </p>
                  )}
                </div>
              </details>
            </>
          );
        }}
      </Async>
    </Panel>
  );
}

/* --------------------------------------------------------------------- logging */

function LogPanel({
  profile,
  exercises,
  preferred,
  nameOf,
  onLogged,
}: {
  profile: Profile;
  exercises: Exercise[];
  preferred: string[];
  nameOf: (id: string) => string;
  onLogged: () => void;
}) {
  const [chosen, setChosen] = useState("");
  const [setCount, setSetCount] = useState(3);
  const [weight, setWeight] = useState(60);
  const [reps, setReps] = useState(8);
  const [rir, setRir] = useState(2);
  const [pain, setPain] = useState(false);

  // `preferred` arrives with the next-session request, after first paint.
  const exerciseId = chosen || preferred[0] || exercises[0]?.id || "";

  const log = useMutation(() =>
    api.post<WorkoutCreated>("/workouts", {
      performed_at: nowIso(),
      unit: profile.unit,
      sets: Array.from({ length: setCount }, () => ({
        exercise_id: exerciseId,
        weight,
        reps,
        rir,
        pain_flag: pain,
      })),
    }),
  );

  const rest = exercises.filter((e) => !preferred.includes(e.id));

  return (
    <Panel
      title="Log a set block"
      hint="Logs are the only input the progression ladder reads — nothing here is inferred"
    >
      <form
        className="log-form"
        onSubmit={(event) => {
          event.preventDefault();
          void log.run().then((result) => {
            if (result) onLogged();
          });
        }}
      >
        <Field label="Exercise">
          <Select value={exerciseId} onChange={(e) => setChosen(e.target.value)}>
            {preferred.length > 0 ? (
              <optgroup label="In your next session">
                {preferred.map((id) => (
                  <option key={id} value={id}>
                    {nameOf(id)}
                  </option>
                ))}
              </optgroup>
            ) : null}
            <optgroup label="Everything else">
              {rest.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </optgroup>
          </Select>
        </Field>

        <div className="log-row">
          <Field label="Sets">
            <Input
              type="number"
              min={1}
              max={10}
              value={setCount}
              onChange={(e) => setSetCount(Number(e.target.value))}
            />
          </Field>
          <Field label={`Weight (${profile.unit})`} hint="stored in kg">
            <Input
              type="number"
              min={0}
              step={0.5}
              value={weight}
              onChange={(e) => setWeight(Number(e.target.value))}
            />
          </Field>
          <Field label="Reps">
            <Input
              type="number"
              min={1}
              max={50}
              value={reps}
              onChange={(e) => setReps(Number(e.target.value))}
            />
          </Field>
          <Field label="RIR" hint="reps in reserve">
            <Input
              type="number"
              min={0}
              max={10}
              step={0.5}
              value={rir}
              onChange={(e) => setRir(Number(e.target.value))}
            />
          </Field>
        </div>

        <label className="log-check">
          <input type="checkbox" checked={pain} onChange={(e) => setPain(e.target.checked)} />
          <span>
            <strong>This hurt.</strong> Flags the exercise, returns a stop-and-refer
            recommendation, and substitutes it out of later prescriptions until you clear it.
          </span>
        </label>

        <div className="log-actions">
          <Button type="submit" variant="primary" pending={log.pending}>
            Log {setCount} {setCount === 1 ? "set" : "sets"}
          </Button>
          <span className="muted">Workouts are append-only; nothing is edited after the fact.</span>
        </div>
        <ErrorNote error={log.error} />
      </form>

      {log.data ? (
        <div className="log-result">
          {log.data.pain_flagged.length > 0 && log.data.recommendation ? (
            <div className="rec" role="alert">
              <Pill tone="bad">flagged</Pill>
              <p className="rec-text">{log.data.recommendation}</p>
              <p className="rec-note">
                That is the whole answer. It refers you on and names no cause — the engine has no
                opinion about what is happening in your body, and it is not permitted to invent one.
              </p>
            </div>
          ) : null}
          <Table
            columns={[
              { key: "ex", header: "Exercise", render: (s: SetOut) => nameOf(s.exercise_id) },
              { key: "n", header: "Set", numeric: true, render: (s: SetOut) => s.set_index + 1 },
              {
                key: "load",
                header: "kg",
                numeric: true,
                render: (s: SetOut) => num(s.weight_kg, 1),
              },
              { key: "reps", header: "Reps", numeric: true, render: (s: SetOut) => s.reps },
              {
                key: "rir",
                header: "RIR",
                numeric: true,
                render: (s: SetOut) => (s.rir == null ? "—" : num(s.rir)),
              },
              {
                key: "e1rm",
                header: "e1RM",
                numeric: true,
                render: (s: SetOut) => (s.e1rm_kg == null ? "—" : num(s.e1rm_kg, 1)),
              },
            ]}
            rows={log.data.workout.sets}
            rowKey={(s) => `${s.exercise_id}-${s.set_index}`}
            caption="Sets just logged"
          />
        </div>
      ) : null}
    </Panel>
  );
}

/* ---------------------------------------------------------------------- volume */

function VolumePanel({ stamp }: { stamp: number }) {
  const volume = useQuery(
    () => api.get<VolumeReport>("/analytics/volume", { as_of: nowIso() }),
    [stamp],
  );

  const columns = useMemo<Column<VolumeRow>[]>(
    () => [
      {
        key: "muscle",
        header: "Muscle",
        render: (row) => (
          <span className="muscle-cell">
            {words(row.muscle)}
            {row.is_target ? <Pill tone="accent">target</Pill> : null}
          </span>
        ),
      },
      {
        key: "band",
        header: "vs landmarks",
        render: (row) => (
          <div className="vol-cell">
            <Meter
              value={row.effective_sets}
              max={Math.max(row.mrv, row.effective_sets)}
              tone={BAND_METER[row.band] ?? "ok"}
            />
            <span className="vol-marks mono">
              MEV {row.mev} · MAV {row.mav} · MRV {row.mrv}
            </span>
          </div>
        ),
      },
      {
        key: "eff",
        header: "Eff.",
        numeric: true,
        render: (row) => num(row.effective_sets, 1),
      },
      { key: "direct", header: "Direct", numeric: true, render: (row) => row.direct_sets },
      {
        key: "status",
        header: "Band",
        render: (row) => <Pill tone={BAND_PILL[row.band] ?? "neutral"}>{words(row.band)}</Pill>,
      },
    ],
    [],
  );

  return (
    <Panel title="This week against the landmarks" hint="Effective sets counted from your logs">
      <Async
        query={volume}
        emptyWhen={(report) => report.rows.length === 0}
        empty={{ title: "No volume rows returned" }}
      >
        {(report) => (
          <>
            <p className="muted">
              ISO week <code className="mono">{report.iso_week}</code>. Indirect work counts as half
              a set, which is why effective sets and direct sets disagree. Below MEV is not a
              failure — it is a week that has not happened yet.
            </p>
            <Table
              columns={columns}
              rows={report.rows}
              rowKey={(row) => row.muscle}
              caption="Weekly volume against MEV, MAV and MRV"
            />
          </>
        )}
      </Async>
    </Panel>
  );
}

/* ----------------------------------------------------------------- form review */

function FindingRow({ finding }: { finding: Finding }) {
  const label = words(finding.fault_id);
  if (finding.status === "not_assessed") {
    return (
      <li className="finding finding-na">
        <span className="finding-name">{label}</span>
        <span className="finding-body">
          not assessed —{" "}
          {NOT_ASSESSED[finding.not_assessed_reason ?? ""] ?? words(finding.not_assessed_reason ?? "")}
          <code className="mono">{finding.not_assessed_reason}</code>
        </span>
      </li>
    );
  }
  if (finding.status === "ok") {
    return (
      <li className="finding finding-ok">
        <span className="finding-name">{label}</span>
        <span className="finding-body">
          within limits —{" "}
          <span className="mono">
            {finding.measured == null ? "—" : num(finding.measured, 3)} vs {num(finding.threshold, 3)}
          </span>
        </span>
      </li>
    );
  }
  return (
    <li className="finding finding-fault">
      <span className="finding-name">
        {label} <Pill tone={SEVERITY_TONE[finding.severity] ?? "neutral"}>{finding.severity}</Pill>
      </span>
      <span className="finding-body">
        <span className="mono">
          measured {finding.measured == null ? "—" : num(finding.measured, 3)} vs threshold{" "}
          {num(finding.threshold, 3)}
          {finding.frame == null ? "" : `, frame ${finding.frame}`}
        </span>
        {finding.cue ? <span className="cue">{finding.cue}</span> : null}
      </span>
    </li>
  );
}

function AnalysisView({
  detail,
  nameOf,
}: {
  detail: AnalysisDetail;
  nameOf: (id: string) => string;
}) {
  const usable = detail.frames_total > 0 ? detail.frames_valid / detail.frames_total : 0;
  const corrections = detail.corrections.length
    ? detail.corrections
    : deriveCorrections(detail.reps);
  const firstRep = detail.reps[0];
  const assessable = firstRep ? firstRep.findings.filter((f) => f.status !== "not_assessed").length : 0;
  const blind = firstRep ? firstRep.findings.length - assessable : 0;

  return (
    <div className="analysis">
      <header className="an-head">
        <div>
          <h3>{nameOf(detail.exercise_id)}</h3>
          <p className="muted mono">
            profile {detail.form_profile_id} · {detail.analysis_kind} · pose from{" "}
            {detail.pose_source}
          </p>
        </div>
        <div className="an-pills">
          <Pill tone={detail.status === "completed" ? "ok" : "bad"}>{detail.status}</Pill>
          <Pill>{words(detail.view)}</Pill>
          {detail.view_inferred ? <Pill tone="warn">view inferred</Pill> : null}
        </div>
      </header>

      <StatRow>
        <Stat
          label="Usable frames"
          value={`${detail.frames_valid}/${detail.frames_total}`}
          sub={`${num(usable * 100)}% · ${detail.fps == null ? "unknown" : num(detail.fps)} fps`}
          tone={usable < 0.7 ? "bad" : undefined}
        />
        <Stat label="Reps segmented" value={detail.rep_count ?? "—"} />
        <Stat
          label="Clip score"
          value={detail.clip_score == null ? "—" : num(detail.clip_score, 1)}
          sub="out of 100"
        />
        <Stat
          label="Checks in scope"
          value={detail.status === "completed" ? `${assessable}/${assessable + blind}` : "—"}
          sub={blind > 0 ? `${blind} out of this view's reach` : "everything assessable"}
        />
      </StatRow>

      {detail.status === "rejected" ? (
        <div className="rejected" role="status">
          <strong>Refused: {words(detail.reject_reason ?? "unknown")}.</strong> Only{" "}
          {detail.frames_valid} of {detail.frames_total} frames were usable, which is under the
          floor this profile needs. It returns the reason and stops. Guessing a rep count and a
          score from an unreadable clip would be worse than saying nothing.
        </div>
      ) : null}

      {corrections.length > 0 ? (
        <div className="corrections">
          <h4>Corrections, most important first</h4>
          <ol>
            {corrections.map((c) => (
              <li key={c.fault_id}>
                <span className="correction-head">
                  <Pill tone={SEVERITY_TONE[c.severity] ?? "neutral"}>{c.severity}</Pill>
                  <strong>{words(c.fault_id)}</strong>
                  <span className="muted">
                    ×{c.occurrences} {c.occurrences === 1 ? "rep" : "reps"}
                  </span>
                </span>
                <span className="correction-cue">{c.cue}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : detail.status === "completed" ? (
        <p className="muted">
          Nothing crossed a threshold on the checks this view supports. That is not the same as
          &ldquo;good everywhere&rdquo; — see what was out of scope below.
        </p>
      ) : null}

      {detail.reps.length > 0 ? (
        <details className="reps">
          <summary>Per-rep measurements ({detail.reps.length} reps)</summary>
          {detail.reps.map((rep) => (
            <section className="rep" key={rep.rep_index}>
              <header className="rep-head">
                <strong>Rep {rep.rep_index + 1}</strong>
                <span className="mono muted">
                  frames {rep.start_frame} → {rep.extremum_frame} → {rep.end_frame}
                </span>
                <span className="rep-score num">{num(rep.score)}/100</span>
              </header>
              <ul className="findings">
                {rep.findings.map((f) => (
                  <FindingRow finding={f} key={f.fault_id} />
                ))}
              </ul>
            </section>
          ))}
        </details>
      ) : null}
    </div>
  );
}

function FormReview({ nameOf, analyzable }: { nameOf: (id: string) => string; analyzable: Exercise[] }) {
  const [exerciseId, setExerciseId] = useState(PRESETS[0].exercise);
  const [view, setView] = useState(PRESETS[0].view);
  const [dir, setDir] = useState(FIXTURE_DIR);
  const [file, setFile] = useState(PRESETS[0].file);
  const [note, setNote] = useState<string | null>(PRESETS[0].note);
  const [selected, setSelected] = useState<number | null>(null);
  const [stamp, setStamp] = useState(0);

  const analyses = useQuery(() => api.get<Analysis[]>("/form/analyses"), [stamp]);
  const detail = useQuery(
    () =>
      selected == null
        ? Promise.resolve(null)
        : api.get<AnalysisDetail>(`/form/analyses/${selected}`),
    [selected],
  );

  const analyze = useMutation(() =>
    api.post<AnalysisDetail>("/form/analyses", {
      exercise_id: exerciseId,
      created_at: nowIso(),
      source: `${dir.replace(/\/+$/, "")}/${file}`,
      view,
    }),
  );

  const listColumns = useMemo<Column<Analysis>[]>(
    () => [
      { key: "id", header: "#", numeric: true, width: "2.5rem", render: (a) => a.id },
      { key: "ex", header: "Movement", render: (a) => nameOf(a.exercise_id) },
      { key: "view", header: "View", render: (a) => words(a.view) },
      {
        key: "status",
        header: "Outcome",
        render: (a) =>
          a.status === "completed" ? (
            <Pill tone="ok">completed</Pill>
          ) : (
            <Pill tone="bad">{words(a.reject_reason ?? "rejected")}</Pill>
          ),
      },
      { key: "reps", header: "Reps", numeric: true, render: (a) => a.rep_count ?? "—" },
      {
        key: "score",
        header: "Score",
        numeric: true,
        render: (a) => (a.clip_score == null ? "—" : num(a.clip_score, 1)),
      },
    ],
    [nameOf],
  );

  return (
    <Panel
      title="Form review"
      hint="Hand it pose keypoints; it reports the measurement, the threshold, the frame — or why it will not say"
    >
      <Split>
        <form
          className="fx-form"
          onSubmit={(event) => {
            event.preventDefault();
            void analyze.run().then((result) => {
              if (result) {
                setSelected(result.id);
                setStamp((n) => n + 1);
              }
            });
          }}
        >
          <div className="fx-presets">
            <span className="fx-presets-label">Try one</span>
            {PRESETS.map((preset) => (
              <button
                type="button"
                className={`preset${file === preset.file ? " active" : ""}`}
                key={preset.file}
                onClick={() => {
                  setExerciseId(preset.exercise);
                  setView(preset.view);
                  setFile(preset.file);
                  setNote(preset.note);
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>
          {note ? <p className="preset-note">{note}</p> : null}

          <Field label="Movement" hint="only three movements have a form profile">
            <Select
              value={exerciseId}
              onChange={(e) => {
                setExerciseId(e.target.value);
                setNote(null);
              }}
            >
              {analyzable.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Declared camera view" hint="“side” lets the engine resolve which side">
            <Select value={view} onChange={(e) => setView(e.target.value)}>
              <option value="side">side (let it resolve)</option>
              <option value="side_left">side_left</option>
              <option value="side_right">side_right</option>
              <option value="front">front</option>
            </Select>
          </Field>

          <Field label="Keypoint directory on the server" hint="the offline fixture corpus">
            <Input value={dir} onChange={(e) => setDir(e.target.value)} spellCheck={false} />
          </Field>

          <Field label="Keypoint file">
            <Input
              value={file}
              onChange={(e) => {
                setFile(e.target.value);
                setNote(null);
              }}
              spellCheck={false}
            />
          </Field>

          <Button type="submit" variant="primary" pending={analyze.pending}>
            Analyse the clip
          </Button>
          <ErrorNote error={analyze.error} />
          <p className="muted">
            Pose estimation sits behind an adapter with an offline default, so the browser hands
            over a path to pre-extracted COCO-17 keypoints rather than video.
          </p>
        </form>

        <div className="fx-result">
          <Async
            query={detail}
            emptyWhen={(value) => value === null}
            empty={{
              title: "Nothing analysed yet",
              detail: "Pick a clip on the left, or open a previous analysis below.",
            }}
          >
            {(value) => (value ? <AnalysisView detail={value} nameOf={nameOf} /> : null)}
          </Async>
        </div>
      </Split>

      <Async
        query={analyses}
        emptyWhen={(rows) => rows.length === 0}
        empty={{
          title: "No analyses stored",
          detail: "Every analysis is kept, including the refusals.",
        }}
      >
        {(rows) => (
          <Table
            columns={listColumns}
            rows={rows}
            rowKey={(a) => String(a.id)}
            onRowClick={(a) => {
              setSelected(a.id);
              setNote(null);
            }}
            selectedKey={selected == null ? undefined : String(selected)}
            caption="Stored form analyses"
          />
        )}
      </Async>
    </Panel>
  );
}

/* ------------------------------------------------------------------- assembly */

function Coach({ boot, reload }: { boot: Boot; reload: () => void }) {
  const { profile, exercises, program } = boot;
  const [stamp, setStamp] = useState(0);

  const nameOf = useCallback(
    (id: string) => exercises.find((e) => e.id === id)?.name ?? id,
    [exercises],
  );
  const refresh = useCallback(() => {
    setStamp((n) => n + 1);
    reload();
  }, [reload]);

  const generate = useMutation(() =>
    api.post<Program>("/programs", { as_of: nowIso(), seed: 20260731 }),
  );

  const analyzable = useMemo(() => exercises.filter((e) => e.analyzable), [exercises]);

  // Lifted out of the panel so the log form can offer today's movements first.
  const programId = program?.id ?? null;
  const next = useQuery<NextSession | null>(async () => {
    if (programId == null) return null;
    try {
      return await api.get<NextSession>(`/programs/${programId}/next`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "program_complete") return null;
      throw err;
    }
  }, [programId, stamp]);

  const nextIds = useMemo(
    () => Array.from(new Set(next.data?.prescriptions.map((p) => p.exercise_id) ?? [])),
    [next.data],
  );

  if (!profile) {
    return (
      <Panel title="No profile yet">
        <State
          kind="empty"
          title="FormCoach has nothing to program against"
          detail={
            <>
              The API answers <code className="mono">404 profile_not_found</code> until a profile
              exists. Create one with <code className="mono">PUT /profile</code> — goal, experience,
              days a week and the equipment you actually have.
            </>
          }
        />
      </Panel>
    );
  }

  const acknowledged = Boolean(profile.disclaimer_acknowledged_at);

  return (
    <>
      <SafetyPanel profile={profile} nameOf={nameOf} onChange={refresh} />

      {program ? (
        <>
          <StatRow>
            <Stat label="Split" value={words(program.split)} sub={`seed ${program.seed}`} />
            <Stat
              label="Weeks"
              value={program.weeks}
              sub={`${program.weeks - 1} accumulation + 1 deload`}
            />
            <Stat label="Days a week" value={program.days_per_week} sub={words(program.goal)} />
            <Stat
              label="Target muscles"
              value={program.target_muscles.length}
              sub="each hit at least twice a week"
            />
            <Stat
              label="Status"
              value={words(program.status)}
              sub={`generated ${shortDate(program.created_at)}`}
              tone={program.status === "active" ? "ok" : undefined}
            />
          </StatRow>

          <Mesocycle program={program} nameOf={nameOf} />
          <NextSessionPanel query={next} nameOf={nameOf} />
          <WeeklyTargets program={program} />

          <Split>
            <VolumePanel stamp={stamp} />
            <LogPanel
              profile={profile}
              exercises={exercises}
              preferred={nextIds}
              nameOf={nameOf}
              onLogged={refresh}
            />
          </Split>
        </>
      ) : (
        <Panel title="No program yet">
          <State
            kind="empty"
            title="Nothing has been generated for this profile"
            detail={
              acknowledged
                ? "Generate a five-week mesocycle from your profile, the volume landmarks and the split templates."
                : "Acknowledge the disclaimer above first — program generation is gated on it."
            }
            action={
              <Button
                variant="primary"
                disabled={!acknowledged}
                pending={generate.pending}
                onClick={() => {
                  void generate.run().then((result) => {
                    if (result) refresh();
                  });
                }}
              >
                Generate a mesocycle
              </Button>
            }
          />
          <ErrorNote error={generate.error} />
        </Panel>
      )}

      <FormReview nameOf={nameOf} analyzable={analyzable} />
    </>
  );
}

export default function FormCoach() {
  const boot = useQuery<Boot>(bootstrap, []);

  return (
    <Page
      title="FormCoach"
      lede="A five-week mesocycle built from per-muscle volume landmarks, an RIR ramp and your own logs — plus form review from pose keypoints that tells you what it measured, what it could not see, and nothing at all about your body."
      actions={<Pill tone="warn">not a medical device</Pill>}
    >
      <Async query={boot}>{(data) => <Coach boot={data} reload={boot.reload} />}</Async>
    </Page>
  );
}
