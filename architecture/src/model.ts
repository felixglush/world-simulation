import rawModel from "./model.json";
import rawSources from "./source-index.json";
import rawRuns from "./demo-runs.json";

export type Mode = "jev+llm" | "llm" | "rules";
export type MessageKind = "public" | "command" | "private" | "audit";
export interface SourceRef {
  path: string;
  symbol: string;
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
}
export interface Connection {
  id: string;
  source: string;
  target: string;
  label: string;
  kind: MessageKind;
  contract: string;
  description: string;
  example: unknown;
  when: string;
  failure: string;
}
export interface Source extends SourceRef {
  startLine: number;
  endLine: number;
  code: string;
  fileHash: string;
  fields: { name: string; type: string }[];
}
export const model = rawModel as {
  components: Component[];
  connections: Connection[];
  services: { id: string; title: string; subtitle: string; color: string }[];
  views: { id: string; title: string; description: string; nodes: string[] }[];
  contracts: Record<string, SourceRef>;
};
export const sourceIndex = rawSources as {
  revision: string;
  simulatorVersion: string;
  sources: Record<string, Source>;
};
export const byId = Object.fromEntries(
  model.components.map((node) => [node.id, node]),
);
export const sourceFor = (ref: SourceRef) =>
  sourceIndex.sources[`${ref.path}:${ref.symbol}`];
export const sourceUrl = (source: Source) =>
  `https://github.com/felixglush/world-simulation/blob/${sourceIndex.revision}/${source.path}#L${source.startLine}`;
export const colors: Record<MessageKind, string> = {
  public: "#009cac",
  command: "#8163c6",
  private: "#d58832",
  audit: "#7890a6",
};
export const kindLabels: Record<MessageKind, string> = {
  public: "Public evidence",
  command: "Command / control",
  private: "Private world facts",
  audit: "Audit / persistence",
};

// JSONL payloads deliberately remain flexible: the record envelope is validated on import.
export interface RunRecord {
  record_type: string;
  sequence: number;
  turn: number;
  event_type: string;
  evidence: any;
  decision: any;
  consequence: any;
  metadata?: any;
  run_id?: string;
  schema_version?: number;
  simulator_version?: string;
  event_count?: number;
  metrics?: any;
  status?: string;
}
export interface DemoRun {
  id: string;
  title: string;
  description: string;
  provenance: string;
  records: RunRecord[];
}
export const demoRuns = rawRuns as unknown as DemoRun[];
export const eventsOf = (run: DemoRun) =>
  run.records.filter((record) => record.record_type === "event");
export const format = (value: unknown) => JSON.stringify(value, null, 2) ?? "—";

export function visibleComponents(view: string, mode: Mode) {
  const ids = model.views.find((item) => item.id === view)!.nodes;
  return model.components.filter(
    (node) =>
      ids.includes(node.id) &&
      !(node.id === "jev" && mode !== "jev+llm") &&
      !(node.id === "captain" && mode === "rules") &&
      !(node.id === "rules" && mode !== "rules"),
  );
}
export function visibleConnections(
  ids: string[],
  mode: Mode,
  privateFlows: boolean,
) {
  return model.connections.filter(
    (edge) =>
      ids.includes(edge.source) &&
      ids.includes(edge.target) &&
      (privateFlows || edge.kind !== "private") &&
      !(edge.id === "direct-routing" && mode === "jev+llm"),
  );
}

const worldFields = new Set(
  sourceFor(model.contracts.StationState).fields.map((field) => field.name),
);
export function worldAt(
  events: RunRecord[],
  index: number,
): Record<string, any> {
  let state: Record<string, any> = {};
  for (const event of events.slice(0, index + 1)) {
    if (event.event_type === "world_initialized") {
      state = Object.fromEntries(
        Object.entries(event.consequence?.state ?? {}).filter(([key]) =>
          worldFields.has(key),
        ),
      );
    }
    if (event.event_type === "world_transition") {
      for (const [key, , after] of event.consequence?.changes ?? []) {
        if (worldFields.has(key)) state[key] = after;
      }
    }
  }
  return state;
}

export function eventTrace(event?: RunRecord) {
  if (!event)
    return {
      title: "Mission input",
      body: "Choose a recorded run and follow its events.",
      nodes: ["commander"],
      edges: [],
      private: false,
    };
  const phase = event.consequence?.phase;
  switch (event.event_type) {
    case "world_initialized":
      return {
        title: "Mission initialized",
        body: "The scenario creates the authoritative starting state.",
        nodes: ["scenario", "world"],
        edges: ["initialize"],
        private: true,
      };
    case "adversary_decision":
      return {
        title: "Adversary proposal",
        body: `${event.decision?.action?.kind ?? "wait"} · ${event.consequence?.accepted ? "accepted" : "rejected"}. Private selection and rationale are recorded.`,
        nodes: ["adversary", "adversary-validation"],
        edges: ["adversary-proposal"],
        private: true,
      };
    case "world_transition":
      return {
        title:
          phase === "oxygen"
            ? "Oxygen changes"
            : phase === "repairs"
              ? "Repairs advance"
              : phase === "scheduled_event"
                ? "Scheduled event applied"
                : "World advances",
        body: `${phase ?? "world"}: ${(event.consequence?.changes ?? []).map((change: any[]) => change[0]).join(", ") || "no state change"}.`,
        nodes: phase === "captain_action" ? ["checks", "world"] : ["world"],
        edges:
          phase === "captain_action" ? ["physical-action"] : ["world-audit"],
        private: true,
      };
    case "station_observation":
      return {
        title: "Public readings published",
        body: "Current sensor readings and available resources enter the crew-visible batch.",
        nodes: ["world", "events"],
        edges: ["observations"],
        private: false,
      };
    case "world_evidence":
      return {
        title: "Evidence published",
        body: String(
          event.evidence?.message ?? "The world emits public evidence.",
        ),
        nodes: ["world", "events"],
        edges: ["observations"],
        private: false,
      };
    case "dispatch":
      return {
        title: "Jev classifies the batch",
        body: `Urgency ${event.decision?.urgency}/100 · diagnosis ${event.decision?.diagnosis_supported} · ${event.consequence?.routed ? "route for review" : "monitor"}.`,
        nodes: ["events", "jev", "routing"],
        edges: ["dispatch-context", "judgment"],
        private: false,
      };
    case "captain_decision":
      return {
        title: "Captain proposes an action",
        body: `${event.decision?.kind}: ${event.decision?.rationale ?? "Application validation follows."}`,
        nodes: ["captain", "checks"],
        edges: ["captain-proposal"],
        private: false,
      };
    case "action":
      return {
        title: event.consequence?.accepted
          ? "Action accepted"
          : "Action rejected",
        body: `${event.decision?.kind}${event.decision?.target ? " → " + event.decision.target : ""}. ${event.consequence?.rejection ?? "The outcome becomes a public event."}`,
        nodes: ["checks", "events"],
        edges: ["action-results"],
        private: false,
      };
    case "incident_closed":
      return {
        title: "Incident closed",
        body: String(
          event.decision?.resolution_reason ??
            "Closure was validated against current trusted evidence.",
        ),
        nodes: ["checks", "routing"],
        edges: [],
        private: false,
      };
    case "incident_opened":
      return {
        title: "Incident opened",
        body: `Incident ${event.decision?.incident_id} is now owned by the application.`,
        nodes: ["routing"],
        edges: [],
        private: false,
      };
    case "follow_up_due":
      return {
        title: "Review becomes due",
        body: "The captain may propose one action for this due incident.",
        nodes: ["monitor", "routing", "captain"],
        edges: ["due-review", "captain-context"],
        private: false,
      };
    case "follow_up_scheduled":
      return {
        title: "Follow-up scheduled",
        body: `${event.decision?.follow_up_turn == null ? "No future turn remains" : "Review at turn " + event.decision.follow_up_turn} · ${event.consequence?.reason}.`,
        nodes: ["routing", "monitor"],
        edges: ["monitoring"],
        private: false,
      };
    case "provider_failure":
      return {
        title: "Provider failure recorded",
        body: `${event.consequence?.provider}: ${event.consequence?.code}. Application fallback remains authoritative.`,
        nodes: [
          event.consequence?.provider === "dispatcher"
            ? "jev"
            : event.consequence?.provider === "adversary"
              ? "adversary"
              : "captain",
          "routing",
        ],
        edges: [],
        private: false,
      };
    default:
      return {
        title: event.event_type.replaceAll("_", " "),
        body: "An application event is appended to the audit record.",
        nodes: ["routing", "audit"],
        edges: [],
        private: false,
      };
  }
}

export function componentSnapshot(
  id: string,
  run: DemoRun,
  events: RunRecord[],
  cursor: number,
) {
  const past = events.slice(0, cursor + 1);
  const last = (type: string) =>
    past.findLast((event) => event.event_type === type);
  if (id === "world" || id === "adversary-validation")
    return {
      label: "Authoritative world state · reconstructed from recorded deltas",
      value: worldAt(events, cursor),
    };
  if (id === "adversary")
    return {
      label: "Last recorded private context and proposal · no future schedule",
      value: last("adversary_decision") ?? { status: "Not called yet" },
    };
  if (id === "jev" || id === "events")
    return {
      label:
        "Latest recorded batch and assessment · request data, not persistent model memory",
      value: last("dispatch") ?? { status: "No assessment yet" },
    };
  if (id === "captain" || id === "rules")
    return {
      label: "Latest recorded proposal · provider owns no incident state",
      value: last("captain_decision")?.decision ??
        last("action")?.decision ?? { status: "No decision yet" },
    };
  if (id === "checks")
    return {
      label: "Last recorded action result",
      value: last("action") ?? { status: "No action yet" },
    };
  if (["routing", "monitor", "loop"].includes(id)) {
    const incidents: Record<string, any> = {};
    for (const event of past) {
      const key = event.decision?.incident_id;
      if (key == null) continue;
      if (event.event_type === "incident_opened")
        incidents[key] = { id: key, open: true, created_turn: event.turn };
      if (incidents[key] && event.event_type === "follow_up_scheduled")
        incidents[key].next_turn = event.decision.follow_up_turn;
      if (incidents[key] && event.event_type === "incident_closed") {
        incidents[key].open = false;
        incidents[key].next_turn = null;
      }
    }
    return {
      label:
        "Incident projection derived from audit records · not a complete memory snapshot",
      value: incidents,
    };
  }
  if (id === "audit")
    return {
      label: "Append-only log progress at this event",
      value: {
        events_recorded: cursor + 1,
        last_sequence: events[cursor]?.sequence,
        last_turn: events[cursor]?.turn,
      },
    };
  if (id === "evaluation")
    return {
      label: "Independent completed-run metrics",
      value:
        cursor === events.length - 1
          ? run.records.at(-1)?.metrics
          : { status: "Evaluation occurs after the final event" },
    };
  if (id === "budget")
    return {
      label: "Recorded request usage · demo providers make zero model calls",
      value: {
        calls: past.reduce(
          (sum, event) =>
            sum +
            (event.decision?.metadata?.calls ??
              event.consequence?.metadata?.calls ??
              0),
          0,
        ),
      },
    };
  if (id === "api")
    return {
      label: "External service",
      value: {
        status: "No remote state snapshot is available in the repository",
      },
    };
  return {
    label: "Recorded mission configuration",
    value: run.records[0]?.metadata ?? {},
  };
}

export function parseRun(text: string, filename: string): DemoRun {
  if (text.length > 5_000_000)
    throw new Error("Use a JSONL run smaller than 5 MB.");
  let records: RunRecord[];
  try {
    records = text
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch {
    throw new Error(
      "This file is not valid JSONL. Each line must contain one JSON record.",
    );
  }
  if (
    records.some(
      (record) =>
        !record || typeof record !== "object" || Array.isArray(record),
    )
  )
    throw new Error("Every JSONL record must be an object.");
  const start = records[0],
    end = records.at(-1);
  if (
    start?.record_type !== "run_start" ||
    start.schema_version !== 1 ||
    end?.record_type !== "run_end" ||
    end.run_id !== start.run_id
  )
    throw new Error("Choose a completed Station Control schema-1 JSONL run.");
  const events = records.slice(1, -1);
  let previous = -1;
  if (
    events.length !== end.event_count ||
    events.some((record, i) => {
      const invalid =
        record.record_type !== "event" ||
        record.sequence !== i ||
        !Number.isInteger(record.turn) ||
        record.turn < previous ||
        typeof record.event_type !== "string";
      previous = record.turn;
      return invalid;
    })
  )
    throw new Error("The event sequence or turn order is invalid.");
  if (
    events[0]?.event_type !== "world_initialized" ||
    typeof events[0].consequence?.state?.oxygen !== "number"
  )
    throw new Error(
      "State playback requires world_initialized and transition records from simulator 0.3.2 or later.",
    );
  for (const event of events) {
    if (
      event.event_type === "world_transition" &&
      (!Array.isArray(event.consequence?.changes) ||
        event.consequence.changes.some(
          (change: unknown) =>
            !Array.isArray(change) ||
            change.length !== 3 ||
            !worldFields.has(change[0]),
        ))
    )
      throw new Error("A world transition contains invalid state changes.");
  }
  return {
    id: `import-${start.run_id}`,
    title: filename,
    description: "Your completed run; interpreted locally in this browser.",
    provenance: "Imported JSONL · no upload or model requests",
    records,
  };
}
