import { validateProject } from "./validate";
export { validateProject, validateRun } from "./validate";
import { createContext, useContext, useMemo, type ReactNode } from "react";
import type {
  ArchitectureProject,
  SourceRef,
  Source,
  ReplayEvent,
  DecisionHighlight,
} from "./types";
export type * from "./types";
export const format = (value: unknown) => JSON.stringify(value, null, 2) ?? "—";
export const eventsOf = (run?: import("./types").ReplayRun) =>
  run?.events ?? [];
export const eventTrace = (event?: ReplayEvent) =>
  event?.trace ?? {
    title: "Run input",
    body: "Choose a recorded run.",
    nodes: [],
    edges: [],
    private: false,
  };
export const decisionHighlight = (event?: ReplayEvent) =>
  event?.decision ?? null;
export const decisionStyle = (marker?: DecisionHighlight | null) =>
  marker
    ? ({
        "--decision-color": marker.color,
        "--decision-bg": marker.background,
      } as import("react").CSSProperties)
    : undefined;

function bindProject(project: ArchitectureProject) {
  validateProject(project);
  const model = project.document;
  const byId = Object.fromEntries(model.components.map((c) => [c.id, c]));
  const colors = Object.fromEntries(
    Object.entries(model.messageKinds).map(([id, k]) => [id, k.color]),
  );
  const kindLabels = Object.fromEntries(
    Object.entries(model.messageKinds).map(([id, k]) => [id, k.label]),
  );
  const sourceFor = (ref: SourceRef) =>
    model.sources[`${ref.path}:${ref.symbol}`];
  const sourceUrl = (source: Source) => source.url;
  const visibleComponents = (view: string, mode: string) =>
    model.components.filter(
      (c) =>
        model.views.find((v) => v.id === view)?.nodes.includes(c.id) &&
        (!c.modes || c.modes.includes(mode)),
    );
  const visibleConnections = (
    ids: string[],
    mode: string,
    privateFlows: boolean,
  ) =>
    model.connections.filter(
      (e) =>
        ids.includes(e.source) &&
        ids.includes(e.target) &&
        (!e.modes || e.modes.includes(mode)) &&
        (privateFlows || !model.messageKinds[e.kind].private),
    );
  return {
    project,
    model,
    byId,
    colors,
    kindLabels,
    sourceFor,
    sourceUrl,
    visibleComponents,
    visibleConnections,
  };
}
const Context = createContext<ReturnType<typeof bindProject> | null>(null);
export function ProjectProvider({
  project,
  children,
}: {
  project: ArchitectureProject;
  children: ReactNode;
}) {
  const value = useMemo(() => bindProject(project), [project]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useProject() {
  const value = useContext(Context);
  if (!value) throw new Error("Explorer requires a ProjectProvider.");
  return value;
}
