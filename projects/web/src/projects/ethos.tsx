/**
 * Ethos — ask a moral question, read how ten traditions answer it.
 *
 * The product's hard part is that citations cannot be fabricated, so the UI
 * makes provenance visible rather than decorative: every claim carries its
 * marker, every marker resolves to a quoted passage with its source and locator,
 * and the answer's verification status is stated on the page.
 *
 * Honest abstention is the other half — when the router declines, that is a
 * result to show clearly, not an error to bury.
 */

import { useState } from "react";
import { client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Facts, Field, Page, Panel, Pill, State, Table, TextArea,
} from "../ui/kit";
import "./ethos.css";

const api = client("ethos");

type Quote = {
  marker: string;
  passage_id: string;
  text: string;
  citation: string;
  context_note?: string | null;
};
type Reason = { text: string; marker?: string | null };
type Reading = { label?: string; citation?: string; url?: string | null } | string;
type Perspective = {
  tradition_id: string;
  tradition_name: string;
  position_id: string;
  stance: string;
  summary: string;
  reasoning: Reason[];
  quotes: Quote[];
  intra_tradition_note?: string | null;
  further_reading: Reading[];
};
type AnswerBody = {
  safeguards: { text?: string; label?: string }[];
  routing: { topic_id: string; confidence: number; alternates: { topic_id: string; score: number }[] };
  perspectives: Perspective[];
  not_covered: string[];
  agreement_map?: Record<string, string[]> | null;
};
type AskResult = {
  outcome: string;
  question: { id: number; text: string; routing: { confidence: number; abstained: boolean } };
  answer?: {
    id: number;
    topic_id: string;
    verified: boolean;
    corpus_version: string;
    body: AnswerBody;
  } | null;
  refusal?: { reason?: string; message?: string } | null;
};
type Topic = { id: string; title: string; summary?: string; position_count?: number };

const STANCE_TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = {
  obligatory: "ok",
  encouraged: "ok",
  permitted: "neutral",
  context_dependent: "warn",
  discouraged: "warn",
  forbidden: "bad",
};

const SUGGESTIONS = [
  "what do I owe my aging parents?",
  "is it wrong to walk away from a promise?",
  "when is it right to forgive someone who isn't sorry?",
  "should I tell a hard truth that will hurt someone?",
];

function readingText(entry: Reading): { label: string; url?: string } {
  if (typeof entry === "string") return { label: entry };
  return { label: entry.label ?? entry.citation ?? "Further reading", url: entry.url ?? undefined };
}

function PerspectiveCard({ p }: { p: Perspective }) {
  const [openQuotes, setOpenQuotes] = useState(true);
  return (
    <article className="persp">
      <header className="persp-head">
        <h3>{p.tradition_name}</h3>
        <Pill tone={STANCE_TONE[p.stance] ?? "neutral"}>{p.stance.replace(/_/g, " ")}</Pill>
      </header>

      <p className="persp-summary">{p.summary}</p>

      {p.reasoning.length > 0 ? (
        <ul className="persp-reasons">
          {p.reasoning.map((r, i) => (
            <li key={i}>
              {r.text}
              {r.marker ? <sup className="marker">{r.marker}</sup> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {p.intra_tradition_note ? (
        <p className="persp-note">
          <span className="persp-note-label">Within this tradition</span>
          {p.intra_tradition_note}
        </p>
      ) : null}

      {p.quotes.length > 0 ? (
        <div className="persp-quotes">
          <button className="quotes-toggle" onClick={() => setOpenQuotes((v) => !v)}
                  aria-expanded={openQuotes}>
            {openQuotes ? "Hide" : "Show"} {p.quotes.length} cited{" "}
            {p.quotes.length === 1 ? "passage" : "passages"}
          </button>
          {openQuotes
            ? p.quotes.map((q) => (
                <figure className="quote" key={q.marker}>
                  <span className="quote-marker">{q.marker}</span>
                  <blockquote>{q.text}</blockquote>
                  <figcaption>
                    {q.citation}
                    {q.context_note ? <span className="quote-context">{q.context_note}</span> : null}
                  </figcaption>
                </figure>
              ))
            : null}
        </div>
      ) : null}

      {p.further_reading.length > 0 ? (
        <div className="persp-reading">
          <span className="persp-reading-label">Further reading</span>
          <ul>
            {p.further_reading.map((entry, i) => {
              const { label, url } = readingText(entry);
              return (
                <li key={i}>
                  {url ? (
                    <a href={url} target="_blank" rel="noreferrer">
                      {label}
                    </a>
                  ) : (
                    label
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

function AnswerView({ result }: { result: AskResult }) {
  if (result.outcome !== "answered" || !result.answer) {
    const reason = result.refusal?.message ?? result.refusal?.reason;
    return (
      <Panel title="No confident match">
        <State
          kind="empty"
          title="Ethos declined to answer this one"
          detail={
            reason ??
            "The question didn't map onto a topic the corpus covers. Abstaining is a designed outcome — a confident answer here would be invented rather than sourced."
          }
        />
      </Panel>
    );
  }

  const { body, verified, topic_id } = result.answer;
  const covered = body.perspectives.length;

  return (
    <div className="answer">
      <Panel
        title={topic_id.replace(/_/g, " ")}
        hint={`${covered} ${covered === 1 ? "tradition" : "traditions"} answer this topic`}
        actions={
          verified ? (
            <Pill tone="ok">citations verified</Pill>
          ) : (
            <Pill tone="bad">verification failed</Pill>
          )
        }
      >
        <Facts
          items={[
            ["Routing confidence", <span className="num">{(result.question.routing.confidence * 100).toFixed(0)}%</span>],
            [
              "Also considered",
              body.routing.alternates.length
                ? body.routing.alternates.slice(0, 3).map((a) => a.topic_id.replace(/_/g, " ")).join(", ")
                : "nothing close",
            ],
            ...(body.not_covered.length
              ? [["Silent on this", body.not_covered.join(", ")] as [string, string]]
              : []),
          ]}
        />
        {body.safeguards.length > 0 ? (
          <div className="safeguards">
            {body.safeguards.map((s, i) => (
              <p key={i}>{s.text ?? s.label}</p>
            ))}
          </div>
        ) : null}
      </Panel>

      <div className="persp-list">
        {body.perspectives.map((p) => (
          <PerspectiveCard key={p.position_id} p={p} />
        ))}
      </div>
    </div>
  );
}

export default function Ethos() {
  const [text, setText] = useState("");
  const ask = useMutation((question: string) =>
    api.post<AskResult>("/questions", { text: question }),
  );
  const topics = useQuery(() => api.get<Topic[]>("/topics"), []);

  const submit = (question: string) => {
    if (!question.trim()) return;
    setText(question);
    void ask.run(question);
  };

  return (
    <Page
      title="Ethos"
      lede="Ask a moral question and read how different traditions answer it — each position grounded in a named primary text, never a paraphrase presented as a source."
    >
      <Panel>
        <form
          className="ask-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit(text);
          }}
        >
          <Field label="Your question">
            <TextArea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. what do I owe my aging parents?"
              rows={2}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(text);
              }}
            />
          </Field>
          <div className="ask-actions">
            <Button type="submit" variant="primary" pending={ask.pending} disabled={!text.trim()}>
              Ask
            </Button>
            <span className="ask-hint">⌘/Ctrl + Enter</span>
          </div>
        </form>

        {!ask.data && !ask.pending ? (
          <div className="suggestions">
            <span className="suggestions-label">Try one</span>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="suggestion" onClick={() => submit(s)}>
                {s}
              </button>
            ))}
          </div>
        ) : null}

        <ErrorNote error={ask.error} />
      </Panel>

      {ask.pending ? <State kind="loading" title="Consulting the corpus…" /> : null}
      {ask.data && !ask.pending ? <AnswerView result={ask.data} /> : null}

      {!ask.data && !ask.pending ? (
        <Panel title="What the corpus covers" hint="Twenty-four topics across ten traditions">
          <Async query={topics} emptyWhen={(t) => t.length === 0}
                 empty={{ title: "No topics loaded", detail: "Run the seeder to initialise the corpus." }}>
            {(list) => (
              <Table
                columns={[
                  { key: "title", header: "Topic", render: (t) => t.title ?? t.id.replace(/_/g, " ") },
                  {
                    key: "summary",
                    header: "What it asks",
                    render: (t) => <span className="topic-summary">{t.summary ?? "—"}</span>,
                  },
                  {
                    key: "n",
                    header: "Positions",
                    numeric: true,
                    render: (t) => t.position_count ?? "—",
                    width: "6rem",
                  },
                ]}
                rows={list}
                rowKey={(t) => t.id}
                onRowClick={(t) => submit(`What should I think about ${(t.title ?? t.id).toLowerCase()}?`)}
                caption="Topics covered by the corpus"
              />
            )}
          </Async>
        </Panel>
      ) : null}
    </Page>
  );
}
