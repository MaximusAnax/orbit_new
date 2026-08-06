/**
 * DataSweep — upload a messy spreadsheet, see exactly what was changed and why.
 *
 * The product's promise is that nothing is altered silently, so the UI is built
 * around evidence: every fix shows its before value, its after value and the
 * proof the engine used to decide. Anything the engine wasn't confident enough
 * to do alone becomes a review decision the user makes.
 *
 * Uploads go to /uploads/clean, which stages the file and hands the existing
 * path-based service a real path — the browser never needs a server filesystem
 * path, and the engine is untouched.
 */

import { useCallback, useRef, useState } from "react";
import { client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Facts, Page, Panel, Pill, Stat, StatRow, State, Table, Tabs,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./datasweep.css";

const api = client("datasweep");

type Run = {
  id: string;
  status: string;
  format: string;
  encoding: string;
  n_rows: number;
  n_cols: number;
  started_at: string;
  finished_at?: string | null;
  issue_counts: Record<string, number>;
  change_counts: Record<string, number>;
  error?: string | null;
};
type Sample = { before?: string | null; after?: string | null; row?: number | null };
type Issue = {
  id: string;
  klass: string;
  col_index: number;
  cell_count: number;
  disposition: string;
  samples: Sample[];
  evidence: Record<string, unknown>;
};
type ReviewItem = {
  id: string;
  rule: string;
  col_index: number;
  description: string;
  affected_cells: number;
  confidence: number;
  status: string;
};
type ColumnProfile = {
  col_index: number;
  name: string;
  original_name?: string | null;
  inferred_type: string;
  type_coverage: number;
  non_null: number;
  null_count: number;
  distinct_count: number;
};
type RunDetail = {
  run: Run;
  columns: ColumnProfile[];
  issues: Issue[];
  review_items: ReviewItem[];
};

const KLASS_LABEL: Record<string, string> = {
  WS: "Whitespace",
  DATE: "Date format",
  TYPE: "Type",
  DUP: "Duplicate rows",
  MISS: "Missing values",
  CAT: "Category spelling",
  OUT: "Outliers",
  ENC: "Encoding",
  STR: "Structure",
};

const DISPOSITION_TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = {
  fixed: "ok",
  flagged: "warn",
  review: "warn",
  reported: "neutral",
  skipped: "neutral",
};

function evidenceProof(evidence: Record<string, unknown>): string | null {
  const proof = evidence.proof ?? evidence.reason ?? evidence.detail;
  return typeof proof === "string" ? proof : null;
}

/** What the fix converted things to, when the engine recorded it. */
function transformation(evidence: Record<string, unknown>): string | null {
  const from = evidence.format_from ?? evidence.from;
  const to = evidence.target ?? evidence.to;
  if (from && to) return `${String(from)} → ${String(to)}`;
  if (to) return `normalised to ${String(to)}`;
  return null;
}

function IssueRow({ issue }: { issue: Issue }) {
  const proof = evidenceProof(issue.evidence);
  const shape = transformation(issue.evidence);
  const rule = issue.evidence.rule;
  // Samples record the offending value only — the engine does not return the
  // replacement here, so rendering an arrow to a blank would claim a deletion
  // that never happened. Show what was found, and what rule acted on it.
  const found = issue.samples.filter((s) => s.before != null);

  return (
    <div className="issue">
      <div className="issue-head">
        <span className="issue-klass">{KLASS_LABEL[issue.klass] ?? issue.klass}</span>
        <Pill tone={DISPOSITION_TONE[issue.disposition] ?? "neutral"}>{issue.disposition}</Pill>
        <span className="issue-count num">
          {issue.cell_count} {issue.cell_count === 1 ? "cell" : "cells"}
        </span>
      </div>

      {found.length ? (
        <div className="found">
          <span className="found-label">Found</span>
          {found.slice(0, 4).map((s, i) => (
            <span className="found-value mono" key={i}>
              {`"${s.before}"`}
              {s.row != null ? <span className="found-row">row {s.row}</span> : null}
            </span>
          ))}
          {found.length > 4 ? <span className="found-more">+{found.length - 4} more</span> : null}
        </div>
      ) : null}

      {shape ? <p className="issue-shape mono">{shape}</p> : null}
      {proof ? <p className="issue-proof">{proof}</p> : null}
      {typeof rule === "string" ? <code className="issue-rule mono">{rule}</code> : null}
    </div>
  );
}

function ReviewQueue({ runId, items, onDecided }: {
  runId: string;
  items: ReviewItem[];
  onDecided: () => void;
}) {
  // The endpoint takes batches of ids; the UI decides one at a time.
  const decide = useMutation((itemId: string, accept: boolean) =>
    api.post(`/runs/${runId}/decisions`, {
      accept: accept ? [itemId] : [],
      reject: accept ? [] : [itemId],
    }),
  );
  const pending = items.filter((i) => i.status === "pending");

  if (items.length === 0) {
    return (
      <State
        kind="empty"
        title="Nothing needs your judgement"
        detail="Every change the engine made cleared its confidence threshold on evidence alone."
      />
    );
  }

  return (
    <div className="review-queue">
      {pending.length === 0 ? (
        <p className="review-done">All {items.length} decisions made.</p>
      ) : null}
      {items.map((item) => (
        <div className={`review-item${item.status !== "pending" ? " decided" : ""}`} key={item.id}>
          <div className="review-body">
            <p className="review-desc">{item.description}</p>
            <div className="review-meta">
              <code className="mono">{item.rule}</code>
              <span>column {item.col_index}</span>
              <span className="num">{Math.round(item.confidence * 100)}% confident</span>
              <span className="num">
                {item.affected_cells} {item.affected_cells === 1 ? "cell" : "cells"}
              </span>
            </div>
          </div>
          {item.status === "pending" ? (
            <div className="review-actions">
              <Button
                size="sm"
                variant="primary"
                pending={decide.pending}
                onClick={() => void decide.run(item.id, true).then(onDecided)}
              >
                Accept
              </Button>
              <Button
                size="sm"
                pending={decide.pending}
                onClick={() => void decide.run(item.id, false).then(onDecided)}
              >
                Reject
              </Button>
            </div>
          ) : (
            <Pill tone={item.status === "accepted" ? "ok" : "neutral"}>{item.status}</Pill>
          )}
        </div>
      ))}
      <ErrorNote error={decide.error} />
    </div>
  );
}

function RunView({ runId }: { runId: string }) {
  const [tab, setTab] = useState("changes");
  const detail = useQuery(() => api.get<RunDetail>(`/runs/${runId}`), [runId]);

  return (
    <Async query={detail}>
      {(d) => {
        const totalIssues = Object.values(d.run.issue_counts).reduce((a, b) => a + b, 0);
        const totalChanges = Object.values(d.run.change_counts).reduce((a, b) => a + b, 0);
        const pendingReviews = d.review_items.filter((i) => i.status === "pending").length;

        return (
          <div className="run-view">
            <StatRow>
              <Stat label="Rows" value={d.run.n_rows} />
              <Stat label="Columns" value={d.run.n_cols} />
              <Stat label="Issues found" value={totalIssues} tone={totalIssues ? "warn" : "ok"} />
              <Stat label="Changes made" value={totalChanges} tone="ok" />
              <Stat
                label="Needs review"
                value={pendingReviews}
                tone={pendingReviews ? "warn" : undefined}
              />
            </StatRow>

            <Panel
              title="What happened to your file"
              hint="Every change carries the evidence the engine used — nothing is altered silently."
              actions={
                <a className="btn btn-default btn-sm" href={`/api/datasweep/runs/${runId}/report`}
                   target="_blank" rel="noreferrer">
                  Full report
                </a>
              }
              padded={false}
            >
              <div className="tabs-wrap">
                <Tabs
                  tabs={[
                    { id: "changes", label: `Changes (${d.issues.length})` },
                    {
                      id: "review",
                      label: pendingReviews
                        ? `Review (${pendingReviews})`
                        : `Review (${d.review_items.length})`,
                    },
                    { id: "columns", label: `Columns (${d.columns.length})` },
                  ]}
                  active={tab}
                  onChange={setTab}
                />
              </div>

              <div className="tab-body">
                {tab === "changes" ? (
                  d.issues.length ? (
                    <div className="issue-list">
                      {d.issues.map((i) => (
                        <IssueRow issue={i} key={i.id} />
                      ))}
                    </div>
                  ) : (
                    <State kind="empty" title="No issues detected" detail="The file was already clean." />
                  )
                ) : null}

                {tab === "review" ? (
                  <ReviewQueue runId={runId} items={d.review_items} onDecided={detail.reload} />
                ) : null}

                {tab === "columns" ? (
                  <Table
                    columns={
                      [
                        { key: "name", header: "Column", render: (c) => (
                          <span>
                            {c.name}
                            {c.original_name && c.original_name !== c.name ? (
                              <span className="renamed"> was “{c.original_name}”</span>
                            ) : null}
                          </span>
                        ) },
                        { key: "type", header: "Inferred type", render: (c) => (
                          <code className="mono">{c.inferred_type}</code>
                        ) },
                        { key: "cov", header: "Type fit", numeric: true,
                          render: (c) => `${Math.round(c.type_coverage * 100)}%` },
                        { key: "nn", header: "Non-null", numeric: true, render: (c) => c.non_null },
                        { key: "null", header: "Missing", numeric: true,
                          render: (c) => (c.null_count ? <span className="tone-warn">{c.null_count}</span> : "0") },
                        { key: "dis", header: "Distinct", numeric: true, render: (c) => c.distinct_count },
                      ] as Column<ColumnProfile>[]
                    }
                    rows={d.columns}
                    rowKey={(c) => String(c.col_index)}
                    caption="Column profiles"
                  />
                ) : null}
              </div>
            </Panel>

            <Panel title="Provenance">
              <Facts
                items={[
                  ["Status", <Pill tone={d.run.status === "succeeded" ? "ok" : "bad"}>{d.run.status}</Pill>],
                  ["Detected format", `${d.run.format} · ${d.run.encoding}`],
                  ["Run id", <code className="mono">{d.run.id.slice(0, 8)}</code>],
                  ["Audit trail", (
                    <a href={`/api/datasweep/runs/${runId}/audit`} target="_blank" rel="noreferrer">
                      every decision, as newline-delimited JSON
                    </a>
                  )],
                ]}
              />
            </Panel>
          </div>
        );
      }}
    </Async>
  );
}

export default function DataSweep() {
  const [runId, setRunId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const runs = useQuery(() => api.get<Run[]>("/runs"), [runId]);

  const upload = useMutation((file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.upload<Run>("/uploads/clean", form);
  });

  const send = useCallback(
    async (file: File) => {
      const run = await upload.run(file);
      if (run) setRunId(run.id);
    },
    [upload],
  );

  return (
    <Page
      title="DataSweep"
      lede="Upload a messy spreadsheet. Every fix is applied with evidence and recorded, so you can see exactly what changed — and undo all of it."
      actions={runId ? <Button onClick={() => setRunId(null)}>Clean another file</Button> : null}
    >
      {!runId ? (
        <Panel>
          <div
            className={`dropzone${dragging ? " dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files[0];
              if (file) void send(file);
            }}
            onClick={() => fileInput.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") fileInput.current?.click();
            }}
          >
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.xlsx,.xls"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void send(file);
              }}
            />
            {upload.pending ? (
              <State kind="loading" title="Cleaning…" />
            ) : (
              <>
                <p className="dropzone-title">Drop a CSV or Excel file here</p>
                <p className="dropzone-hint">or click to choose — nothing leaves your machine</p>
              </>
            )}
          </div>
          <ErrorNote error={upload.error} />
        </Panel>
      ) : (
        <RunView runId={runId} />
      )}

      {!runId ? (
        <Panel title="Earlier runs" hint="Every clean is recorded and reversible" padded={false}>
          <Async
            query={runs}
            emptyWhen={(r) => r.length === 0}
            empty={{ title: "No runs yet", detail: "Clean a file to see it here." }}
          >
            {(list) => (
              <Table
                columns={[
                  {
                    key: "when",
                    header: "Cleaned",
                    render: (r) => new Date(r.started_at).toLocaleString(),
                  },
                  { key: "shape", header: "Shape", numeric: true,
                    render: (r) => `${r.n_rows} × ${r.n_cols}` },
                  { key: "fmt", header: "Format", render: (r) => r.format },
                  {
                    key: "issues",
                    header: "Issues",
                    numeric: true,
                    render: (r) => Object.values(r.issue_counts).reduce((a, b) => a + b, 0),
                  },
                  {
                    key: "status",
                    header: "Status",
                    render: (r) => (
                      <Pill tone={r.status === "succeeded" ? "ok" : "bad"}>{r.status}</Pill>
                    ),
                  },
                ]}
                rows={list}
                rowKey={(r) => r.id}
                onRowClick={(r) => setRunId(r.id)}
                caption="Previous cleaning runs"
              />
            )}
          </Async>
        </Panel>
      ) : null}
    </Page>
  );
}
