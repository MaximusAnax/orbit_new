/**
 * The shell: launcher, project navigation, theme, and per-project accent.
 *
 * Routing is hash-based (`#/ethos`) so the gateway needs no history fallback
 * beyond serving index.html, and a project's screens never have to know where
 * they are mounted.
 */

import { useEffect, useMemo, useState } from "react";
import { gateway, type ProjectInfo } from "./lib/api";
import { useQuery, useStored } from "./lib/hooks";
import { Async, Button, Pill, State } from "./ui/kit";
import { SCREENS } from "./projects/registry";
import "./app.css";

/** One accent per project — same system, twelve identities. */
const ACCENT: Record<string, string> = {
  ethos: "#7c5cc4",
  almanac: "#8a661b",
  flowlist: "#2f7d8f",
  chessmentor: "#5f6b7a",
  dresscast: "#3d7a5c",
  pointsmax: "#a9702a",
  newsalpha: "#3b6ea5",
  tickerpress: "#41708a",
  grailtrader: "#9c4f6b",
  datasweep: "#6b7b3a",
  formcoach: "#b1573a",
  voicekin: "#7a5aa0",
};

function useHashRoute(): [string, (slug: string) => void] {
  const [hash, setHash] = useState(() => window.location.hash.replace(/^#\/?/, ""));
  useEffect(() => {
    const onChange = () => setHash(window.location.hash.replace(/^#\/?/, ""));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = (slug: string) => {
    window.location.hash = slug ? `/${slug}` : "/";
  };
  return [hash.split("/")[0] ?? "", go];
}

function ThemeToggle() {
  const [theme, setTheme] = useStored<"light" | "dark" | "auto">("theme", "auto");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);
  const next = theme === "auto" ? "light" : theme === "light" ? "dark" : "auto";
  const label = theme === "auto" ? "Auto" : theme === "light" ? "Light" : "Dark";
  return (
    <Button variant="ghost" size="sm" onClick={() => setTheme(next)}>
      {label} theme
    </Button>
  );
}

function Launcher({ projects, onOpen }: { projects: ProjectInfo[]; onOpen: (slug: string) => void }) {
  return (
    <div className="launcher">
      <header className="launcher-head">
        <h1>Twelve products</h1>
        <p className="lede">
          Each one is a working tool with its own engine, data and evaluation suite. Pick one to try
          it.
        </p>
      </header>
      <div className="launcher-grid">
        {projects.map((p) => {
          const ready = Boolean(SCREENS[p.slug]);
          return (
            <button
              key={p.slug}
              className="tile"
              style={{ ["--accent" as string]: ACCENT[p.slug] ?? "var(--accent)" }}
              onClick={() => onOpen(p.slug)}
            >
              <span className="tile-rule" aria-hidden="true" />
              <span className="tile-name">{p.name}</span>
              <span className="tile-tagline">{p.tagline}</span>
              <span className="tile-foot">
                {ready ? (
                  <Pill tone="accent">Open</Pill>
                ) : (
                  <Pill>API only</Pill>
                )}
                <span className="tile-routes num">{p.routes} endpoints</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function App() {
  const [slug, go] = useHashRoute();
  const projects = useQuery(() => gateway.projects(), []);

  const active = useMemo(
    () => projects.data?.projects.find((p) => p.slug === slug),
    [projects.data, slug],
  );

  useEffect(() => {
    const root = document.documentElement;
    if (active) root.style.setProperty("--accent", ACCENT[active.slug] ?? "#8a661b");
    else root.style.removeProperty("--accent");
    document.title = active ? `${active.name} — Projects` : "Projects";
  }, [active]);

  const Screen = slug ? SCREENS[slug] : undefined;

  return (
    <div className="shell">
      <nav className="topbar">
        <button className="brand" onClick={() => go("")}>
          Projects
        </button>
        {active ? (
          <>
            <span className="crumb-sep" aria-hidden="true">
              /
            </span>
            <span className="crumb">{active.name}</span>
          </>
        ) : null}
        <div className="topbar-spacer" />
        {active ? (
          <a className="topbar-link" href={`/api/${active.slug}/docs`} target="_blank" rel="noreferrer">
            API docs
          </a>
        ) : null}
        <ThemeToggle />
      </nav>

      <main className="main">
        <Async
          query={projects}
          emptyWhen={(d) => d.projects.length === 0}
          empty={{
            title: "No projects are mounted",
            detail: "Start the gateway with `uv run python web/server.py` from projects/.",
          }}
        >
          {(data) => {
            if (!slug) return <Launcher projects={data.projects} onOpen={go} />;
            if (!active) {
              return (
                <State
                  kind="error"
                  title={`No project called “${slug}”`}
                  detail="It may have failed to mount — check the gateway output."
                  action={<Button onClick={() => go("")}>Back to all projects</Button>}
                />
              );
            }
            if (!Screen) {
              return (
                <State
                  kind="empty"
                  title={`${active.name} has no screens yet`}
                  detail={`Its ${active.routes} endpoints are live — explore them in the API docs while the UI is built.`}
                  action={
                    <a className="btn btn-default btn-md" href={`/api/${active.slug}/docs`}
                       target="_blank" rel="noreferrer">
                      Open API docs
                    </a>
                  }
                />
              );
            }
            return <Screen />;
          }}
        </Async>
      </main>
    </div>
  );
}
