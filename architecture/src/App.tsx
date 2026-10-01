import { Button } from "./components/ui/button";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { ExternalLink, GitBranch, Network, Play, Search } from "lucide-react";
import { ComponentBrowser } from "./ComponentBrowser";
import { CanvasToolbar } from "./CanvasToolbar";
import { Graph } from "./Graph";
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
import type { DemoRun, Mode } from "./core/project";

export default function App({
  project,
  initialRunId,
}: {
  project: ArchitectureProject;
  initialRunId?: string;
}) {
  return (
    <ProjectProvider project={project}>
      <ReactFlowProvider>
        <Explorer
          key={`${project.document.id}:${project.document.version}`}
          initialRunId={initialRunId}
        />
      </ReactFlowProvider>
    </ProjectProvider>
  );
}
function Explorer({ initialRunId }: { initialRunId?: string }) {
  const { project, model, byId } = useProject();
  const adapter = project.replay;
  const demoRuns = adapter?.runs ?? [];
  const initialRun = demoRuns.find((run) => run.id === initialRunId);
  const canReplay = Boolean(adapter && demoRuns.length);
  const [view, setView] = useState(
    initialRun ? adapter!.defaults.view : model.defaults.view,
  );
  const [mode, setMode] = useState<Mode>(
    initialRun?.mode ?? model.defaults.mode,
  );
  const [selected, setSelected] = useState<Selection | null>(null);
  const [tab, setTab] = useState<InspectorTab>(
    initialRun ? "state" : "overview",
  );
  const [query, setQuery] = useState("");
  const [privateFlows, setPrivateFlows] = useState(true);
  const [labels, setLabels] = useState(false);
  const [follow, setFollow] = useState(true);
  const [sidebar, setSidebar] = useState(false);
  const [replay, setReplay] = useState(Boolean(initialRun));
  const [runs, setRuns] = useState<DemoRun[]>(demoRuns);
  const [run, setRun] = useState<DemoRun | undefined>(
    initialRun ?? demoRuns[0],
  );
  const [cursor, setCursor] = useState(
    initialRun
      ? Math.max(
          0,
          Math.min(adapter!.defaults.cursor, initialRun.events.length - 1),
        )
      : 0,
  );
  const [playing, setPlaying] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [hasReplayed, setHasReplayed] = useState(Boolean(initialRun));
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
    },
    [mode, replay, model, byId],
  );
  const clear = useCallback(() => setSelected(null), []);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSidebar(false);
        setSelected(null);
        return;
      }
      if (
        (event.target as HTMLElement).matches("input,textarea,select") ||
        (event.target as HTMLElement).closest("[role=tablist]")
      )
        return;
      if (event.key === "/") {
        event.preventDefault();
        setSidebar(true);
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
    setTab("state");
    if (next.mode) setMode(next.mode);
  }
  function closeReplay() {
    setReplay(false);
    setPlaying(false);
    if (tab === "event") setTab("overview");
  }
  function startReplay() {
    if (!adapter || !run) return;
    setReplay(true);
    setPlaying(false);
    if (!hasReplayed) {
      setCursor(
        Math.max(0, Math.min(adapter.defaults.cursor, run.events.length - 1)),
      );
      setHasReplayed(true);
      if (selected) setTab("state");
    }
    setMode(run.mode ?? model.defaults.mode);
    setSidebar(false);
  }
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
          Source snapshot <code>v{model.version}</code>
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
          className="browse-trigger"
          aria-label="Browse components"
          aria-expanded={sidebar}
          onClick={() => setSidebar(!sidebar)}
        >
          <Search size={16} /> <span>Find a component</span>
          <kbd>/</kbd>
        </button>
        <div className="toolbar-spacer" />
        <Button
          disabled={!canReplay}
          className="walkthrough-button"
          onClick={replay ? closeReplay : startReplay}
        >
          <Play size={14} />
          {replay
            ? "Hide replay"
            : hasReplayed
              ? "Resume replay"
              : (adapter?.labels.action ?? "No recorded runs")}
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
        {sidebar && (
          <ComponentBrowser
            query={query}
            setQuery={setQuery}
            selected={selected}
            onInspect={inspect}
            onClose={() => setSidebar(false)}
          />
        )}
        <main className="canvas-area">
          <CanvasToolbar
            view={view}
            onView={(id) => {
              setView(id);
              setSelected(null);
            }}
            mode={mode}
            onMode={(id) => {
              setMode(id);
              setSelected(null);
            }}
            replay={replay}
            labels={labels}
            setLabels={setLabels}
            privateFlows={privateFlows}
            setPrivateFlows={setPrivateFlows}
            follow={follow}
            setFollow={setFollow}
            showDetails={showDetails}
            setShowDetails={setShowDetails}
          />
          <div className="canvas-heading">
            <div>
              <div className="eyebrow">
                {replay ? "FIELD NOTES / REPLAY" : "FIELD NOTES / ARCHITECTURE"}
              </div>
              <h2>{viewInfo.title}</h2>
              <p>{replay ? trace?.title : viewInfo.description}</p>
            </div>
            <span className="canvas-hint">
              Select a component to look inside <span>↗</span>
            </span>
          </div>
          <Graph
            view={view}
            showDetails={showDetails}
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
          onClose={closeReplay}
        />
      )}
    </div>
  );
}
