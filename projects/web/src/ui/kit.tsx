/**
 * The shared component kit.
 *
 * Twelve products, six recurring shapes. Everything here is written once and
 * instantiated per project; a screen should be composition, not CSS.
 *
 * The non-negotiable pieces are State and Async: every list, panel and detail
 * view routes its loading / empty / error rendering through them, so no screen
 * can quietly render nothing when a request fails.
 */

import type { ReactNode } from "react";
import type { ApiError } from "../lib/api";
import type { QueryState } from "../lib/hooks";
import "./kit.css";

/* ------------------------------------------------------------------ layout */

export function Page({
  title,
  lede,
  actions,
  children,
}: {
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="page">
      <header className="page-head">
        <div className="page-head-text">
          <h1>{title}</h1>
          {lede ? <p className="lede">{lede}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      <div className="page-body">{children}</div>
    </div>
  );
}

export function Panel({
  title,
  hint,
  actions,
  children,
  padded = true,
}: {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  padded?: boolean;
}) {
  return (
    <section className="panel">
      {title || actions ? (
        <div className="panel-head">
          <div>
            {title ? <h2 className="panel-title">{title}</h2> : null}
            {hint ? <p className="panel-hint">{hint}</p> : null}
          </div>
          {actions ? <div className="panel-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className={padded ? "panel-body" : "panel-body flush"}>{children}</div>
    </section>
  );
}

export function Grid({ min = "18rem", children }: { min?: string; children: ReactNode }) {
  return (
    <div className="grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${min}, 1fr))` }}>
      {children}
    </div>
  );
}

export function Split({ children }: { children: ReactNode }) {
  return <div className="split">{children}</div>;
}

/* ------------------------------------------------------------------ states */

export function State({
  kind,
  title,
  detail,
  action,
}: {
  kind: "loading" | "empty" | "error";
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`state state-${kind}`} role={kind === "error" ? "alert" : undefined}>
      {kind === "loading" ? <span className="spinner" aria-hidden="true" /> : null}
      <p className="state-title">{title}</p>
      {detail ? <p className="state-detail">{detail}</p> : null}
      {action ? <div className="state-action">{action}</div> : null}
    </div>
  );
}

/** Render a query's three states without letting a screen forget one. */
export function Async<T>({
  query,
  empty,
  children,
  emptyWhen,
}: {
  query: QueryState<T>;
  empty?: { title: string; detail?: ReactNode; action?: ReactNode };
  emptyWhen?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (query.error) {
    return (
      <State
        kind="error"
        title={query.error.message}
        detail={<code className="mono">{query.error.code}</code>}
        action={
          query.error.retryable ? (
            <Button onClick={query.reload}>Try again</Button>
          ) : null
        }
      />
    );
  }
  if (query.data === undefined) {
    return <State kind="loading" title={query.loading ? "Loading…" : "Nothing to show"} />;
  }
  if (emptyWhen?.(query.data) && empty) {
    return <State kind="empty" title={empty.title} detail={empty.detail} action={empty.action} />;
  }
  return <>{children(query.data)}</>;
}

export function ErrorNote({ error }: { error: ApiError | undefined }) {
  if (!error) return null;
  return (
    <p className="error-note" role="alert">
      <strong>{error.message}</strong> <code className="mono">{error.code}</code>
    </p>
  );
}

/* ------------------------------------------------------------------ controls */

export function Button({
  children,
  onClick,
  variant = "default",
  type = "button",
  disabled,
  pending,
  size = "md",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "ghost" | "danger";
  type?: "button" | "submit";
  disabled?: boolean;
  pending?: boolean;
  size?: "sm" | "md";
}) {
  return (
    <button
      type={type}
      className={`btn btn-${variant} btn-${size}`}
      onClick={onClick}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
    >
      {pending ? <span className="spinner spinner-sm" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`input ${props.className ?? ""}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`input ${props.className ?? ""}`} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`input textarea ${props.className ?? ""}`} />;
}

/* ------------------------------------------------------------------ display */

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "bad" | "accent";
}) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value num${tone ? ` tone-${tone}` : ""}`}>{value}</span>
      {sub ? <span className="stat-sub">{sub}</span> : null}
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return <div className="stat-row">{children}</div>;
}

export type Column<T> = {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  numeric?: boolean;
  width?: string;
};

/** The Library pattern — it appears in all twelve projects. */
export function Table<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  caption,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string;
  caption?: string;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={c.width ? { width: c.width } : undefined}
                  className={c.numeric ? "num" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = rowKey(row);
            return (
              <tr
                key={key}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={[onRowClick ? "clickable" : "", selectedKey === key ? "selected" : ""]
                  .filter(Boolean)
                  .join(" ")}
                tabIndex={onRowClick ? 0 : undefined}
                onKeyDown={
                  onRowClick
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick(row);
                        }
                      }
                    : undefined
                }
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.numeric ? "num" : undefined}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A labelled key/value list — used by every detail pane. */
export function Facts({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="facts">
      {items.map(([term, value], i) => (
        <div className="fact" key={i}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A proportion rendered as a bar — scores, confidence, coverage. */
export function Meter({
  value,
  max = 1,
  label,
  tone,
}: {
  value: number;
  max?: number;
  label?: ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="meter" title={label ? undefined : `${value} of ${max}`}>
      {label ? <span className="meter-label">{label}</span> : null}
      <span
        className="meter-track"
        role="meter"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
      >
        <span className={`meter-fill${tone ? ` tone-${tone}` : ""}`} style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: ReactNode }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={t.id === active}
          className={`tab${t.id === active ? " active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ title, detail, action }: { title: string; detail?: ReactNode; action?: ReactNode }) {
  return <State kind="empty" title={title} detail={detail} action={action} />;
}
