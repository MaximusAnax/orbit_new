/**
 * flowlist — a playlist reordered so it flows, shown as a claim you can check.
 *
 * The engine's hard part is not producing *an* order; it is producing one that
 * is demonstrably better and defensible seam by seam. So the screen never shows
 * a reordered list on its own. It shows:
 *
 *   1. the stored order scored (POST /playlists/{id}/score) — the baseline,
 *      rendered before anything is reordered, so the problem is visible first;
 *   2. the flowed order beside it, with each track's original position, so the
 *      move itself is legible;
 *   3. both orders' seams as a strip of bars, which is where "5 cliffs became 0"
 *      lands in one glance;
 *   4. one seam at a time in full: Camelot relation, tempo delta, energy and
 *      loudness deltas, and the weighted component scores that summed to it —
 *      the `flowlist explain` output, clickable.
 *
 * Everything the playlist view needs is loaded by one awaited chain rather than
 * three parallel queries. That is deliberate: flowlist's API serialises every
 * request behind a threading.RLock taken in a sync generator dependency, and
 * FastAPI may run that generator's exit on a different threadpool worker than
 * its entry — which raises "cannot release un-acquired lock" and leaves the
 * lock held forever. Concurrent requests hit it within a few page loads; one
 * request in flight does not. See the report accompanying this screen.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Facts, Field, Input, Meter, Page, Panel, Pill,
  Select, Split, Stat, StatRow, State, Table,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./flowlist.css";

const api = client("flowlist");

/** The engine's own thresholds (README, "How the score works"). */
const SEAMLESS = 0.7;
const CLIFF = 0.4;

/* ------------------------------------------------------------------- types */

type Features = {
  bpm: number | null;
  key_pc: number | null;
  mode: number | null;
  energy: number | null;
  danceability: number | null;
  loudness_db: number | null;
};

type Track = {
  id: string;
  title: string;
  artist: string;
  album: string | null;
  duration_ms: number | null;
};

type PlaylistSummary = {
  id: string;
  name: string;
  source: string;
  source_ref: string | null;
  applied_run_id: string | null;
  created_at: string;
  entries: number;
};

type PlaylistEntry = {
  entry_id: string;
  position: number;
  track: Track;
  features: Features;
  feature_sources: Record<string, string>;
};

type PlaylistDetail = PlaylistSummary & {
  tracks: PlaylistEntry[];
  coverage: { tracks: number; full: number; fields: string[]; missing: Record<string, number> };
};

type Components = {
  key: number | null;
  bpm: number | null;
  energy: number | null;
  loudness: number | null;
  danceability: number | null;
};

type Transition = {
  score: number;
  components: Components;
  weights: Record<string, number>;
  key_relation: string | null;
  camelot_from: string | null;
  camelot_to: string | null;
  bpm_from: number | null;
  bpm_to: number | null;
  bpm_delta_pct: number | null;
  bpm_folded: boolean;
  energy_delta: number | null;
  loudness_delta_db: number | null;
  flags: string[];
  from_id: string;
  to_id: string;
};

type FlowReport = {
  order: string[];
  total: number;
  mean: number;
  min_score: number;
  seamless: number;
  cliffs: number;
  weights: Record<string, number>;
  profile: string;
  transitions: Transition[];
};

type Run = {
  id: string;
  playlist_id: string;
  created_at: string;
  engine_version: string;
  algorithm: string;
  seed: number;
  score_mean_before: number;
  score_mean_after: number;
  score_min_before: number;
  score_min_after: number;
  score_total_before: number;
  score_total_after: number;
  seamless_before: number;
  seamless_after: number;
  cliff_before: number;
  cliff_after: number;
};

type RunSummary = {
  id: string;
  playlist_id: string;
  created_at: string;
  seed: number;
  algorithm: string;
  profile: string;
  score_mean_before: number;
  score_mean_after: number;
  applied: boolean;
};

type RunEntry = {
  position: number;
  entry_id: string;
  track: Track;
  transition: Transition | null;
};

type RunDetail = {
  run: Run;
  params: { seed: number; profile: string; weights: Record<string, number> };
  applied: boolean;
  entries: RunEntry[];
};

type ImportResponse = { playlist: PlaylistSummary; warnings: string[] };

/** The stored order, its score and its run history — fetched as one chain. */
type View = { playlist: PlaylistDetail; before: FlowReport; runs: RunSummary[] };

type Profile = "neutral" | "build" | "cool";

/* --------------------------------------------------------------- constants */

/** The gateway mounts flowlist on an in-memory store, so a fresh process has no
 *  playlists at all. Rather than dead-end on an empty screen, offer the demo
 *  playlist seed.py builds — imported through the public API, same as any file. */
const DEMO_NAME = "Road Trip";
const DEMO_CSV = [
  "Track Name,Artist Name,Tempo,Key,Mode,Energy,Loudness,Duration (ms)",
  "Golden Hour,Vela Sun,124.0,8,1,0.71,-7.2,214000",
  "Night Drive,Marta Quiet,126.5,9,1,0.76,-6.4,233000",
  "Slow Burn,Cyan Drift,86.0,9,0,0.42,-11.0,258000",
  "Breakline,Ostro,174.0,7,1,0.88,-5.1,191000",
  "Paper Lanterns,Hana Iwai,122.0,8,0,0.55,-8.9,246000",
  "Copper Wire,The Levellers,128.0,10,1,0.81,-6.0,205000",
  "Undertow,Vela Sun,120.0,3,1,0.49,-9.8,269000",
  "Second Wind,Ostro,130.0,10,0,0.85,-5.6,198000",
  "Blue Hour,Hana Iwai,92.0,2,1,0.38,-12.1,281000",
  "Afterglow,Marta Quiet,125.0,8,1,0.73,-7.0,222000",
  "",
].join("\n");

const RELATION_LABEL: Record<string, string> = {
  same_key: "same key",
  relative: "relative major/minor",
  adjacent_fifth: "adjacent fifth/fourth",
  diagonal: "diagonal",
  parallel: "parallel major/minor",
  energy_boost: "energy boost (+2)",
  energy_drop: "energy drop (−2)",
  semitone_lift: "semitone lift",
  clash: "clash",
  unknown: "key unknown",
};

const FLAG_LABEL: Record<string, string> = {
  missing_key: "no key on one side",
  missing_bpm: "no tempo on one side",
  missing_energy: "no energy on one side",
  missing_loudness: "no loudness on one side",
  missing_danceability: "no danceability on one side",
};

const PROFILE_HINT: Record<Profile, string> = {
  neutral: "energy changes cost the same either way",
  build: "energy drops cost 1.5×, so the set climbs",
  cool: "energy rises cost more, so the set winds down",
};

/* -------------------------------------------------------------- formatting */

function tone(score: number): "ok" | "warn" | "bad" {
  if (score >= SEAMLESS) return "ok";
  if (score < CLIFF) return "bad";
  return "warn";
}

/** Deltas are the whole argument, so they always carry their sign. */
function signed(value: number, digits: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function num(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : value.toFixed(digits);
}

function relation(t: Transition): string {
  const key = t.key_relation ?? "unknown";
  return RELATION_LABEL[key] ?? key;
}

/* ------------------------------------------------------------------ pieces */

/**
 * Every seam of one ordering as one bar each — the shape of the playlist.
 * A run's value reads off this instantly: a strip pocked with short red bars
 * becomes an even green one.
 */
function SeamStrip({
  label,
  transitions,
  selected,
  onSelect,
  scale,
}: {
  label: string;
  transitions: Transition[];
  selected: string | null;
  onSelect: (t: Transition) => void;
  scale?: boolean;
}) {
  return (
    <div className="strip">
      <span className="strip-label">
        <span>{label}</span>
        {scale ? (
          <span className="strip-scale">dashed line = seamless ({SEAMLESS.toFixed(2)})</span>
        ) : null}
      </span>
      <div className="strip-bars">
        {transitions.map((t, i) => (
          <button
            type="button"
            key={t.to_id}
            className={`seam-bar tone-${tone(t.score)}${selected === t.to_id ? " selected" : ""}`}
            onClick={() => onSelect(t)}
            aria-label={`Seam ${i + 1} of ${transitions.length}: ${relation(t)}, score ${t.score.toFixed(2)}`}
            title={`${relation(t)} · ${t.score.toFixed(2)}`}
          >
            <span className="seam-fill" style={{ height: `${Math.max(4, t.score * 100)}%` }} />
          </button>
        ))}
      </div>
    </div>
  );
}

/** The score cell shared by both order tables. */
function Seam({ t }: { t: Transition | null }) {
  if (!t) return <span className="seam-none">start of set</span>;
  return (
    <span className="seam">
      <span className={`seam-score num tone-${tone(t.score)}`}>{t.score.toFixed(2)}</span>
      <span className="seam-rel">{relation(t)}</span>
    </span>
  );
}

/** One transition, argued in full — the UI form of `flowlist explain`. */
function SeamDetail({ t, from, to }: { t: Transition; from: string; to: string }) {
  const parts: [string, number | null, number][] = [
    ["Key", t.components.key, t.weights.key ?? 0],
    ["Tempo", t.components.bpm, t.weights.bpm ?? 0],
    ["Energy", t.components.energy, t.weights.energy ?? 0],
    ["Loudness", t.components.loudness, t.weights.loudness ?? 0],
  ];

  return (
    <div className="seam-detail">
      <div className="seam-head">
        <p className="seam-pair">
          <span className="seam-track">{from}</span>
          <span className="seam-arrow" aria-hidden="true">→</span>
          <span className="seam-track">{to}</span>
        </p>
        <span className={`seam-total num tone-${tone(t.score)}`}>{t.score.toFixed(3)}</span>
      </div>

      <Facts
        items={[
          [
            "Harmonic",
            <span>
              <code className="mono">{t.camelot_from ?? "?"}</code>
              <span aria-hidden="true"> → </span>
              <code className="mono">{t.camelot_to ?? "?"}</code>
              <span className="seam-note"> {relation(t)}</span>
            </span>,
          ],
          [
            "Tempo",
            t.bpm_delta_pct == null ? (
              <span className="seam-note">not comparable</span>
            ) : (
              <span className="num">
                {num(t.bpm_from)} → {num(t.bpm_to)} BPM ({signed(t.bpm_delta_pct, 1)}%)
                {t.bpm_folded ? <Pill tone="accent">half/double time</Pill> : null}
              </span>
            ),
          ],
          [
            "Energy",
            t.energy_delta == null ? (
              <span className="seam-note">unknown</span>
            ) : (
              <span className="num">{signed(t.energy_delta, 2)}</span>
            ),
          ],
          [
            "Loudness",
            t.loudness_delta_db == null ? (
              <span className="seam-note">unknown</span>
            ) : (
              <span className="num">{signed(t.loudness_delta_db, 1)} dB</span>
            ),
          ],
        ]}
      />

      <div className="components">
        {parts.map(([label, value, weight]) => (
          <Meter
            key={label}
            value={value ?? 0}
            tone={value == null ? undefined : tone(value)}
            label={
              <>
                <span>
                  {label} <span className="weight num">×{weight.toFixed(2)}</span>
                </span>
                <span className="num">{value == null ? "n/a" : value.toFixed(2)}</span>
              </>
            }
          />
        ))}
      </div>

      {t.flags.length ? (
        <p className="seam-flags">
          {t.flags.map((f) => (
            <Pill tone="warn" key={f}>
              {FLAG_LABEL[f] ?? f}
            </Pill>
          ))}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ screen */

export default function Flowlist() {
  const playlists = useQuery(() => api.get<PlaylistSummary[]>("/playlists"), []);
  const [chosen, setChosen] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile>("neutral");
  const [seed, setSeed] = useState("7");
  const [run, setRun] = useState<RunDetail | null>(null);
  const [seam, setSeam] = useState<Transition | null>(null);

  const list = playlists.data ?? [];
  const playlistId = chosen ?? list[0]?.id ?? null;

  // One awaited chain, never a parallel burst — see the file header.
  const view = useQuery<View>(
    async () => {
      const playlist = await api.get<PlaylistDetail>(`/playlists/${playlistId}`);
      const before = await api.post<FlowReport>(`/playlists/${playlistId}/score`, { profile });
      const runs = await api.get<RunSummary[]>("/runs", { playlist_id: playlistId });
      return { playlist, before, runs };
    },
    [playlistId, profile],
    { enabled: playlistId !== null },
  );

  const reorder = useMutation((body: Record<string, unknown>) =>
    api.post<RunDetail>(`/playlists/${playlistId}/reorder`, body),
  );
  const openRun = useMutation((runId: string) => api.get<RunDetail>(`/runs/${runId}`));
  const apply = useMutation((runId: string) => api.post(`/runs/${runId}/apply`));
  const seedDemo = useMutation(() =>
    api.post<ImportResponse>("/playlists/import", {
      name: DEMO_NAME,
      format: "csv",
      content: DEMO_CSV,
    }),
  );

  // An empty store is a dead end, not a state worth showing off; import the demo
  // playlist once so the screen has something real to argue about.
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || !playlists.data || playlists.data.length > 0) return;
    seeded.current = true;
    // Only reload the list: selecting the new id here would enable the view
    // query while that reload is still in flight, and two concurrent requests
    // is precisely what wedges this API.
    void seedDemo.run().then((r) => {
      if (r) playlists.reload();
    });
    // Runs once, when the list is known to be empty.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playlists.data]);

  // Entry ids survive a reorder, so the stored playlist is the lookup table for
  // "where was this track before" and for the features the run payload omits.
  const byEntry = useMemo(() => {
    const map = new Map<string, PlaylistEntry>();
    for (const e of view.data?.playlist.tracks ?? []) map.set(e.entry_id, e);
    return map;
  }, [view.data]);

  const beforeSeams = useMemo(() => {
    const map = new Map<string, Transition>();
    for (const t of view.data?.before.transitions ?? []) map.set(t.to_id, t);
    return map;
  }, [view.data]);

  const activeRun = run && run.run.playlist_id === playlistId ? run : null;

  const afterSeams = useMemo(
    () => (activeRun?.entries ?? []).flatMap((e) => (e.transition ? [e.transition] : [])),
    [activeRun],
  );

  // Camelot labels only exist on transitions; between the two orderings every
  // entry is named, so read them off rather than re-deriving the wheel here.
  const camelot = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of [...(view.data?.before.transitions ?? []), ...afterSeams]) {
      if (t.camelot_from) map.set(t.from_id, t.camelot_from);
      if (t.camelot_to) map.set(t.to_id, t.camelot_to);
    }
    return map;
  }, [view.data, afterSeams]);

  const busy = reorder.pending || openRun.pending || apply.pending;

  const storedColumns: Column<PlaylistEntry>[] = [
    { key: "pos", header: "#", numeric: true, width: "2.5rem", render: (e) => e.position + 1 },
    {
      key: "track",
      header: "Track",
      render: (e) => (
        <span className="tk">
          <span className="tk-title">{e.track.title}</span>
          <span className="tk-artist">{e.track.artist}</span>
        </span>
      ),
    },
    {
      key: "key",
      header: "Key",
      width: "3.5rem",
      render: (e) => <code className="mono">{camelot.get(e.entry_id) ?? "—"}</code>,
    },
    { key: "bpm", header: "BPM", numeric: true, width: "4rem", render: (e) => num(e.features.bpm) },
    {
      key: "seam",
      header: "Transition in",
      width: "10rem",
      render: (e) => <Seam t={beforeSeams.get(e.entry_id) ?? null} />,
    },
  ];

  const flowedColumns: Column<RunEntry>[] = [
    { key: "pos", header: "#", numeric: true, width: "2.5rem", render: (e) => e.position + 1 },
    {
      key: "track",
      header: "Track",
      render: (e) => (
        <span className="tk">
          <span className="tk-title">{e.track.title}</span>
          <span className="tk-artist">{e.track.artist}</span>
        </span>
      ),
    },
    {
      key: "was",
      header: "Was",
      numeric: true,
      width: "4.5rem",
      render: (e) => {
        const was = byEntry.get(e.entry_id);
        if (!was) return "—";
        const move = was.position - e.position;
        return (
          <span className="was">
            <span className="num">{was.position + 1}</span>
            {move === 0 ? (
              <span className="move stay">held</span>
            ) : (
              <span className="move num">
                {move > 0 ? "↑" : "↓"}
                {Math.abs(move)}
              </span>
            )}
          </span>
        );
      },
    },
    {
      key: "key",
      header: "Key",
      width: "3.5rem",
      render: (e) => <code className="mono">{camelot.get(e.entry_id) ?? "—"}</code>,
    },
    {
      key: "bpm",
      header: "BPM",
      numeric: true,
      width: "4rem",
      render: (e) => num(byEntry.get(e.entry_id)?.features.bpm ?? null),
    },
    {
      key: "seam",
      header: "Transition in",
      width: "10rem",
      render: (e) => <Seam t={e.transition} />,
    },
  ];

  const runColumns: Column<RunSummary>[] = [
    { key: "when", header: "Run", render: (r) => new Date(r.created_at).toLocaleTimeString() },
    { key: "seed", header: "Seed", numeric: true, render: (r) => r.seed },
    { key: "profile", header: "Arc", render: (r) => r.profile },
    {
      key: "mean",
      header: "Mean seam",
      numeric: true,
      render: (r) => (
        <span className="num">
          {r.score_mean_before.toFixed(3)}
          <span aria-hidden="true"> → </span>
          <strong>{r.score_mean_after.toFixed(3)}</strong>
        </span>
      ),
    },
    {
      key: "delta",
      header: "Gain",
      numeric: true,
      render: (r) => (
        <span className={`num tone-${r.score_mean_after >= r.score_mean_before ? "ok" : "bad"}`}>
          {signed(r.score_mean_after - r.score_mean_before, 3)}
        </span>
      ),
    },
    { key: "applied", header: "", render: (r) => (r.applied ? <Pill tone="ok">applied</Pill> : null) },
  ];

  return (
    <Page
      title="Flowlist"
      lede="The tracks are fixed; only the order is in play. Flowlist scores every seam of the order you already have, searches for the order that plays best, and shows you both — because a reorder you cannot check is just a shuffle."
      actions={
        <>
          {list.length > 1 ? (
            <Field label="Playlist">
              <Select
                value={playlistId ?? ""}
                onChange={(e) => {
                  setChosen(e.target.value);
                  setRun(null);
                  setSeam(null);
                }}
              >
                {list.map((p) => (
                  <option value={p.id} key={p.id}>
                    {p.name} ({p.entries})
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Field label="Arc" hint={PROFILE_HINT[profile]}>
            <Select
              value={profile}
              // A run is only valid for the arc it was searched under, so
              // changing the arc drops it rather than pairing the old run's
              // numbers with a freshly rescored baseline.
              onChange={(e) => {
                setProfile(e.target.value as Profile);
                setRun(null);
                setSeam(null);
              }}
            >
              <option value="neutral">neutral</option>
              <option value="build">build</option>
              <option value="cool">cool</option>
            </Select>
          </Field>
          <Field label="Seed" hint="same seed, same order">
            <Input
              type="number"
              value={seed}
              className="seed-input"
              onChange={(e) => setSeed(e.target.value)}
            />
          </Field>
          <Button
            variant="primary"
            pending={reorder.pending}
            disabled={playlistId === null || busy}
            onClick={() =>
              void reorder.run({ seed: Number(seed) || 0, profile }).then((d) => {
                if (d) {
                  setRun(d);
                  setSeam(null);
                  view.reload(); // the run is appended to the history below
                }
              })
            }
          >
            Reorder for flow
          </Button>
        </>
      }
    >
      <Async
        query={playlists}
        emptyWhen={(p) => p.length === 0}
        empty={{
          title: seedDemo.pending ? "Importing the demo playlist…" : "No playlists yet",
          detail:
            "flowlist is mounted on an in-memory store here, so imports do not survive a gateway restart. Import the demo playlist to see a reorder argued end to end.",
          action: (
            <Button
              variant="primary"
              pending={seedDemo.pending}
              onClick={() => void seedDemo.run().then(() => playlists.reload())}
            >
              Import “{DEMO_NAME}”
            </Button>
          ),
        }}
      >
        {() => (
          <Async query={view}>
            {(v) => (
              <>
                <ErrorNote error={seedDemo.error} />
                <ErrorNote error={reorder.error} />
                <ErrorNote error={openRun.error} />
                <ErrorNote error={apply.error} />

                <Panel
                  title="Does the reorder actually help?"
                  hint={`A seam scoring ≥ ${SEAMLESS.toFixed(2)} is seamless; below ${CLIFF.toFixed(2)} it is a cliff. Each bar is one transition — click it for the argument.`}
                  actions={
                    activeRun ? (
                      <>
                        <Pill tone="neutral">seed {activeRun.run.seed}</Pill>
                        <Pill tone="neutral">{activeRun.run.algorithm}</Pill>
                        <a
                          className="btn btn-default btn-sm"
                          href={`/api/flowlist/runs/${activeRun.run.id}/export?format=m3u`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Export m3u
                        </a>
                        <Button
                          size="sm"
                          pending={apply.pending}
                          disabled={activeRun.applied || busy}
                          onClick={() =>
                            void apply.run(activeRun.run.id).then(() => {
                              setRun({ ...activeRun, applied: true });
                              view.reload();
                            })
                          }
                        >
                          {activeRun.applied ? "Applied" : "Apply to playlist"}
                        </Button>
                      </>
                    ) : null
                  }
                >
                  <StatRow>
                    <Stat
                      label="Mean seam"
                      value={(activeRun?.run.score_mean_after ?? v.before.mean).toFixed(3)}
                      tone={activeRun ? "ok" : undefined}
                      sub={
                        activeRun
                          ? `was ${activeRun.run.score_mean_before.toFixed(3)} · ${signed(activeRun.run.score_mean_after - activeRun.run.score_mean_before, 3)}`
                          : "the order as imported"
                      }
                    />
                    <Stat
                      label="Weakest seam"
                      value={(activeRun?.run.score_min_after ?? v.before.min_score).toFixed(3)}
                      tone={tone(activeRun?.run.score_min_after ?? v.before.min_score)}
                      sub={
                        activeRun
                          ? `was ${activeRun.run.score_min_before.toFixed(3)} · ${signed(activeRun.run.score_min_after - activeRun.run.score_min_before, 3)}`
                          : "the worst moment in the set"
                      }
                    />
                    <Stat
                      label="Seamless"
                      value={activeRun?.run.seamless_after ?? v.before.seamless}
                      tone="ok"
                      sub={
                        activeRun
                          ? `was ${activeRun.run.seamless_before} · ${signed(activeRun.run.seamless_after - activeRun.run.seamless_before, 0)}`
                          : `of ${v.before.transitions.length} transitions`
                      }
                    />
                    <Stat
                      label="Cliffs"
                      value={activeRun?.run.cliff_after ?? v.before.cliffs}
                      tone={(activeRun?.run.cliff_after ?? v.before.cliffs) > 0 ? "bad" : "ok"}
                      sub={
                        activeRun
                          ? `was ${activeRun.run.cliff_before} · ${signed(activeRun.run.cliff_after - activeRun.run.cliff_before, 0)}`
                          : `of ${v.before.transitions.length} transitions`
                      }
                    />
                  </StatRow>

                  <div className="strips">
                    <SeamStrip
                      label="As imported"
                      transitions={v.before.transitions}
                      selected={seam?.to_id ?? null}
                      onSelect={setSeam}
                      scale
                    />
                    {activeRun ? (
                      <SeamStrip
                        label="Flowed"
                        transitions={afterSeams}
                        selected={seam?.to_id ?? null}
                        onSelect={setSeam}
                      />
                    ) : (
                      <p className="strip-empty">
                        Reorder for flow to draw the second strip against this one.
                      </p>
                    )}
                  </div>
                </Panel>

                {seam ? (
                  <Panel
                    title="Why this seam scores what it does"
                    hint="Weighted components, summed. This is what the engine optimised — not a description written afterwards."
                    actions={
                      <Button size="sm" variant="ghost" onClick={() => setSeam(null)}>
                        Close
                      </Button>
                    }
                  >
                    <SeamDetail
                      t={seam}
                      from={byEntry.get(seam.from_id)?.track.title ?? "—"}
                      to={byEntry.get(seam.to_id)?.track.title ?? "—"}
                    />
                  </Panel>
                ) : null}

                <Split>
                  <Panel
                    title="Stored order"
                    hint="As imported — what a straight playthrough actually gives you."
                    padded={false}
                  >
                    <Table
                      columns={storedColumns}
                      rows={v.playlist.tracks}
                      rowKey={(e) => e.entry_id}
                      selectedKey={seam?.to_id}
                      onRowClick={(e) => setSeam(beforeSeams.get(e.entry_id) ?? null)}
                      caption="Tracks in stored order with the transition into each"
                    />
                  </Panel>

                  <Panel
                    title="Flowed order"
                    hint={
                      activeRun
                        ? `Run ${activeRun.run.id.slice(0, 8)} — the same tracks, resequenced. Nothing is written until you apply it.`
                        : "Nothing is written until you apply a run."
                    }
                    padded={false}
                  >
                    {activeRun ? (
                      <Table
                        columns={flowedColumns}
                        rows={activeRun.entries}
                        rowKey={(e) => e.entry_id}
                        selectedKey={seam?.to_id}
                        onRowClick={(e) => setSeam(e.transition)}
                        caption="Tracks in the reordered sequence with the transition into each"
                      />
                    ) : (
                      <State
                        kind="empty"
                        title="No reorder yet"
                        detail="Reordering is deterministic given the playlist, the weights and the seed, so the same button always returns the same answer."
                      />
                    )}
                  </Panel>
                </Split>

                <Panel
                  title="Runs"
                  hint="Every reorder is kept, so one can be compared against another."
                  padded={false}
                >
                  {v.runs.length ? (
                    <Table
                      columns={runColumns}
                      rows={v.runs}
                      rowKey={(r) => r.id}
                      selectedKey={activeRun?.run.id}
                      onRowClick={(r) =>
                        void openRun.run(r.id).then((d) => {
                          if (d) {
                            setRun(d);
                            setSeam(null);
                          }
                        })
                      }
                      caption="Stored reorder runs for this playlist"
                    />
                  ) : (
                    <State
                      kind="empty"
                      title="No runs for this playlist yet"
                      detail="Reorder for flow to record one."
                    />
                  )}
                </Panel>
              </>
            )}
          </Async>
        )}
      </Async>
    </Page>
  );
}
