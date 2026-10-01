/** Versioned, renderer-independent interchange format for codebase architecture. */
export interface SourceRef {
  path: string;
  symbol: string;
}
export interface Source extends SourceRef {
  startLine: number;
  endLine: number;
  code: string;
  language: string;
  fileHash?: string;
  url?: string;
  fields: { name: string; type: string }[];
}
export interface Component {
  id: string;
  title: string;
  service: string;
  icon: string;
  summary: string;
  responsibilities: string[];
  sources: SourceRef[];
  state: string;
  stateTypes: string[];
  modes?: string[];
  color?: string;
}
export interface Connection {
  id: string;
  source: string;
  target: string;
  label: string;
  kind: string;
  contract: string;
  description: string;
  example: unknown;
  when: string;
  failure: string;
  modes?: string[];
}
export interface ArchitectureDocument {
  modeLabel?: string;
  schemaVersion: 1;
  id: string;
  title: string;
  version: string;
  repositoryUrl?: string;
  description: string;
  components: Component[];
  connections: Connection[];
  services: {
    id: string;
    title: string;
    subtitle: string;
    color: string;
    external?: boolean;
  }[];
  views: { id: string; title: string; description: string; nodes: string[] }[];
  contracts: Record<string, SourceRef>;
  sources: Record<string, Source>;
  messageKinds: Record<
    string,
    { label: string; color: string; private?: boolean }
  >;
  modes: { id: string; title: string }[];
  defaults: { view: string; mode: string; allView: string };
}
export interface DecisionHighlight {
  actor: string;
  tone: string;
  label: string;
  outcome: string;
  color: string;
  background: string;
}
export interface EventTrace {
  title: string;
  body: string;
  nodes: string[];
  edges: string[];
  private: boolean;
}
export interface ReplayEvent {
  sequence: number;
  tick: number | string;
  type: string;
  payload: unknown;
  raw: unknown;
  trace: EventTrace;
  decision: DecisionHighlight | null;
  keyEvent: boolean;
}
export interface ReplayRun {
  id: string;
  title: string;
  description: string;
  provenance: string;
  badge: string;
  mode?: string;
  events: ReplayEvent[];
  data: unknown;
}
export interface StateSnapshot {
  label: string;
  value: unknown;
  fields?: Record<string, unknown>;
  previous?: Record<string, unknown>;
}
export interface RecordedIO {
  sequence: number;
  tick: number | string;
  input: unknown;
  output: unknown;
  note: string;
}
export interface MetricPanel {
  title: string;
  note: string;
  values: {
    id: string;
    title: string;
    value: string | number;
    delta?: number;
  }[];
  flags: { label: string; tone: "healthy" | "warning" }[];
}
/** An adapter interprets runtime evidence; the renderer never reads domain log fields. */
export interface ReplayAdapter {
  runs: ReplayRun[];
  labels: {
    tick: string;
    action: string;
    region: string;
    private: string;
    public: string;
    decisionHelp: string;
  };
  defaults: { view: string; component?: string; cursor: number };
  import?: {
    accept: string;
    label: string;
    parse: (text: string, filename: string) => ReplayRun;
  };
  serialize?: (run: ReplayRun) => {
    text: string;
    filename: string;
    mimeType: string;
  };
  snapshot: (id: string, run: ReplayRun, cursor: number) => StateSnapshot;
  metrics: (run: ReplayRun, cursor: number) => MetricPanel;
  /** undefined = not supported for this component; null = no record yet. */
  recordedIO: (
    id: string,
    run: ReplayRun,
    cursor: number,
  ) => RecordedIO | null | undefined;
}
export interface ArchitectureProject {
  document: ArchitectureDocument;
  replay?: ReplayAdapter;
}
export type Mode = string;
export type MessageKind = string;
export type DemoRun = ReplayRun;
export type RunRecord = ReplayEvent;
