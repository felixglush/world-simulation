import { Button } from "./components/ui/button";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import {
  BookOpen,
  ChevronRight,
  Compass,
  ExternalLink,
  GitBranch,
  Layers3,
  LockKeyhole,
  Menu,
  Network,
  PanelLeftClose,
  Play,
  Search,
} from "lucide-react";
import { Graph, icons } from "./Graph";
import { Inspector } from "./Inspector";
import type { InspectorTab, Selection } from "./Inspector";
import { RunPlayer } from "./RunPlayer";
import {
  ProjectProvider,
  useProject,
  eventTrace,
  decisionHighlight,
  eventsOf,
} from "./core/project";
import type { ArchitectureProject } from "./core/types";
import type { DemoRun, MessageKind, Mode } from "./core/project";

export default function App({ project }: { project: ArchitectureProject }) {
  return (
    <ProjectProvider project={project}>
      <ReactFlowProvider>
        <Explorer key={`${project.document.id}:${project.document.version}`} />
      </ReactFlowProvider>
    </ProjectProvider>
  );
}
function Explorer() {
  const { project, model, byId, kindLabels, visibleComponents } = useProject();
  const adapter = project.replay;
  const demoRuns = adapter?.runs ?? [];
  const canReplay = Boolean(adapter && demoRuns.length);
  const [view, setView] = useState(model.defaults.view);
  const [mode, setMode] = useState<Mode>(model.defaults.mode);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [tab, setTab] = useState<InspectorTab>("overview");
  const [query, setQuery] = useState("");
  const [privateFlows, setPrivateFlows] = useState(true);
  const [labels, setLabels] = useState(true);
  const [follow, setFollow] = useState(true);
  const [sidebar, setSidebar] = useState(false);
  const [replay, setReplay] = useState(false);
  const [runs, setRuns] = useState<DemoRun[]>(demoRuns);
  const [run, setRun] = useState<DemoRun | undefined>(demoRuns[0]);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const events = useMemo(() => eventsOf(run), [run]);
  const trace = useMemo(
    () => (replay ? eventTrace(events[cursor]) : null),
    [replay, events, cursor],
  );
  const inspect = useCallback(
    (kind: "component" | "message", id: string, code = false) => {
      setSelected({ kind, id });
      setTab(code ? "code" : "overview");
      setSidebar(false);
      setPlaying(false);
      const ids =
        kind === "component"
          ? [id]
          : model.connections
              .filter((edge) => edge.id === id)
              .flatMap((edge) => [edge.source, edge.target]);
      const compatible = model.modes.find((m) =>
        ids.every((id) => !byId[id].modes || byId[id].modes!.includes(m.id)),
      );
      if (
        !replay &&
        compatible &&
        ids.some((id) => byId[id].modes && !byId[id].modes!.includes(mode))
      )
        setMode(compatible.id);
      if (
        ids.some(
          (id) => !visibleComponents(view, mode).some((node) => node.id === id),
        )
      )
        setView(model.defaults.allView);
    },
    [view, mode, replay, model, byId, visibleComponents],
  );
  const clear = useCallback(() => setSelected(null), []);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (
        (event.target as HTMLElement).matches("input,textarea,select") ||
        (event.target as HTMLElement).closest("[role=tablist]")
      )
        return;
      if (event.key === "/") {
        event.preventDefault();
        setSidebar(true);
        search.current?.focus();
      }
      if (event.key === "Escape") {
        setSelected(null);
        setSidebar(false);
      }
      if (replay && event.key === "ArrowRight") {
        event.preventDefault();
        setPlaying(false);
        setCursor((index) => Math.min(events.length - 1, index + 1));
      }
      if (replay && event.key === "ArrowLeft") {
        event.preventDefault();
        setPlaying(false);
        setCursor((index) => Math.max(0, index - 1));
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [replay, events.length]);
  function chooseRun(next: DemoRun) {
    setRuns((list) =>
      list.some((item) => item.id === next.id)
        ? list.map((item) => (item.id === next.id ? next : item))
        : [...list, next],
    );
    setRun(next);
    setCursor(0);
    setPlaying(false);
    setSelected(
      adapter?.defaults.component
        ? { kind: "component", id: adapter.defaults.component }
        : null,
    );
    setTab("state");
    if (next.mode) setMode(next.mode);
  }
  function startReplay() {
    if (!adapter || !run) return;
    setReplay(true);
    setPlaying(false);
    setView(adapter.defaults.view);
    setMode(run.mode ?? model.defaults.mode);
    setCursor(Math.min(adapter.defaults.cursor, run.events.length - 1));
    setSelected(
      adapter.defaults.component
        ? { kind: "component", id: adapter.defaults.component }
        : null,
    );
    setTab("state");
    setPrivateFlows(true);
    setSidebar(false);
  }
  const matches = model.components.filter((node) =>
    [node.title, node.summary, ...node.sources.map((source) => source.symbol)]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const messageMatches = query
    ? model.connections.filter((edge) =>
        `${edge.label} ${edge.description}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      )
    : [];
  const viewInfo = model.views.find((item) => item.id === view)!;
  return (
    <div className={`app ${replay ? "replay-open" : ""}`}>
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">
            <Network size={25} />
          </span>
          <div>
            <span>{model.title}</span>
            <h1>Architecture explorer</h1>
          </div>
        </div>
        <div className="header-center">
          <span className="live-dot" />
          Current implementation <code>v{model.version}</code>
        </div>
        {model.repositoryUrl && (
          <a
            className="repository-link"
            href={model.repositoryUrl}
            target="_blank"
            rel="noreferrer"
          >
            <GitBranch size={16} />
            Repository
            <ExternalLink size={13} />
          </a>
        )}
      </header>
      <div className="workspace-toolbar">
        <button
          className="mobile-browse icon-button"
          aria-label="Browse components"
          onClick={() => setSidebar(!sidebar)}
        >
          <Menu size={21} />
        </button>
        <div className="workspace-tabs">
          <button
            className={!replay ? "selected" : ""}
            onClick={() => {
              setReplay(false);
              setPlaying(false);
              if (tab === "event") setTab("overview");
            }}
          >
            <Compass size={16} />
            Architecture
          </button>
          <button
            disabled={!canReplay}
            className={replay ? "selected" : ""}
            onClick={startReplay}
          >
            <Play size={15} />
            Run replay
          </button>
        </div>
        <div className="toolbar-spacer" />
        <label className="mode-selector">
          {model.modeLabel ?? "Configuration"}
          <select
            aria-label={model.modeLabel ?? "Configuration"}
            value={mode}
            disabled={replay}
            onChange={(event) => {
              setMode(event.target.value as Mode);
              setSelected(null);
            }}
          >
            {model.modes.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </select>
        </label>
        <Button
          disabled={!canReplay}
          className="walkthrough-button"
          onClick={startReplay}
        >
          <Play size={14} />
          {adapter?.labels.action ?? "No recorded runs"}
        </Button>
      </div>
      <div className="workspace">
        {sidebar && (
          <button
            aria-label="Close component browser"
            className="sidebar-backdrop"
            onClick={() => setSidebar(false)}
          />
        )}
        <nav
          className={`sidebar ${sidebar ? "mobile-open" : ""}`}
          aria-label="Architecture navigation"
        >
          <div className="sidebar-top">
            <span className="eyebrow">EXPLORE THE SYSTEM</span>
            <button
              className="mobile-browse icon-button"
              aria-label="Close navigation"
              onClick={() => setSidebar(false)}
            >
              <PanelLeftClose size={17} />
            </button>
          </div>
          <div className="search-field">
            <Search size={15} />
            <input
              ref={search}
              type="search"
              aria-label="Find a component or message"
              placeholder="Find component, message…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <kbd>/</kbd>
          </div>
          {!query && (
            <>
              <div className="nav-heading">VIEWS</div>
              <div className="view-list">
                {model.views.map((item) => (
                  <button
                    key={item.id}
                    aria-pressed={view === item.id}
                    className={view === item.id ? "active" : ""}
                    onClick={() => {
                      setView(item.id);
                      setSelected(null);
                      setSidebar(false);
                    }}
                  >
                    <Layers3 size={15} />
                    <span>{item.title}</span>
                    <ChevronRight size={13} />
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="nav-heading">
            {query ? "SEARCH RESULTS" : "COMPONENTS"}{" "}
            <span>{matches.length}</span>
          </div>
          <div className="component-list">
            {model.services.map((service) => {
              const members = matches.filter(
                (node) => node.service === service.id,
              );
              return members.length ? (
                <div key={service.id}>
                  <div className="service-heading">
                    <i style={{ background: service.color }} />
                    {service.title}
                  </div>
                  {members.map((node) => {
                    const Icon =
                      icons[node.icon as keyof typeof icons] ?? Network;
                    return (
                      <button
                        key={node.id}
                        aria-label={`Inspect ${node.title}`}
                        className={selected?.id === node.id ? "active" : ""}
                        onClick={() => inspect("component", node.id)}
                      >
                        <Icon size={15} />
                        <span>{node.title}</span>
                        {selected?.id === node.id && <ChevronRight size={12} />}
                      </button>
                    );
                  })}
                </div>
              ) : null;
            })}
            {messageMatches.map((edge) => (
              <button
                className="search-message"
                key={edge.id}
                onClick={() => inspect("message", edge.id)}
              >
                <span>
                  {edge.label}
                  <small>
                    {byId[edge.source].title} → {byId[edge.target].title}
                  </small>
                </span>
              </button>
            ))}
            {!matches.length && !messageMatches.length && (
              <p className="empty">No matching components or messages.</p>
            )}
          </div>
          <div className="sidebar-footer">
            <LockKeyhole size={15} />
            <p>{model.description}</p>
          </div>
        </nav>
        <main className="canvas-area">
          <div className="canvas-heading">
            <div>
              <div className="eyebrow">
                {replay ? "FOLLOW THE RUN" : "COMPONENTS & CONNECTIONS"}
              </div>
              <h2>{viewInfo.title}</h2>
              <p>{replay ? trace?.title : viewInfo.description}</p>
            </div>
            <div className="canvas-options">
              {replay && (
                <label>
                  <input
                    type="checkbox"
                    checked={follow}
                    onChange={(event) => setFollow(event.target.checked)}
                  />
                  Follow event
                </label>
              )}
              <label>
                <input
                  type="checkbox"
                  checked={labels}
                  onChange={(event) => setLabels(event.target.checked)}
                />
                Message labels
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={privateFlows}
                  onChange={(event) => setPrivateFlows(event.target.checked)}
                />
                Show private flows
              </label>
            </div>
          </div>
          <Graph
            view={view}
            mode={mode}
            privateFlows={privateFlows}
            labels={labels}
            follow={follow}
            selected={selected}
            activeNodes={trace?.nodes ?? []}
            decision={replay ? decisionHighlight(events[cursor]) : null}
            activeEdges={trace?.edges ?? []}
            onSelect={inspect}
            onClear={clear}
          />
          <div className="legend">
            {(Object.keys(kindLabels) as MessageKind[]).map((kind) => (
              <span key={kind}>
                <i style={{ background: model.messageKinds[kind].color }} />
                {kindLabels[kind]}
              </span>
            ))}
            <span className="legend-note">
              <BookOpen size={12} />
              Click a component for code & state
            </span>
          </div>
        </main>
        {selected && (
          <Inspector
            selected={selected}
            tab={tab}
            setTab={setTab}
            onClose={clear}
            onSelect={inspect}
            run={run}
            events={events}
            cursor={replay ? cursor : 0}
            replay={replay}
          />
        )}
      </div>
      {replay && run && adapter && (
        <RunPlayer
          run={run}
          runs={runs}
          onRun={chooseRun}
          events={events}
          cursor={cursor}
          setCursor={setCursor}
          playing={playing}
          setPlaying={setPlaying}
          onClose={() => {
            setReplay(false);
            setPlaying(false);
            if (tab === "event") setTab("overview");
          }}
        />
      )}
      <footer className="app-footer">
        <span>
          <i />
          Read-only architecture map · Source-linked components
        </span>
        <span>
          React Flow <b>·</b> Offline-ready <b>·</b> No model requests
        </span>
      </footer>
    </div>
  );
}
