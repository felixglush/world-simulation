import * as runtime from "./runtime";
import type {
  ArchitectureProject,
  ReplayRun,
  ReplayEvent,
  DecisionHighlight,
} from "../../core/types";
const palette = {
  malicious: ["#b33e32", "#fff0ec"],
  benign: ["#20754a", "#edf8f0"],
  unknown: ["#756342", "#f7f3e9"],
  captain: ["#315aba", "#eef3ff"],
  jev: ["#7950ad", "#f6efff"],
};
const raw = (run: ReplayRun) => run.data as runtime.DemoRun;
const normalize = (run: runtime.DemoRun): ReplayRun => ({
  id: run.id,
  title: run.title,
  description: run.description,
  provenance: run.provenance,
  badge: run.id.startsWith("import-") ? "Imported run" : "Scripted AI demo",
  mode: ["jev+llm", "llm", "rules"].includes(
    run.records[0]?.metadata?.controller,
  )
    ? run.records[0].metadata.controller
    : "jev+llm",
  data: run,
  events: runtime.eventsOf(run).map((event): ReplayEvent => {
    const marker = runtime.decisionHighlight(event);
    const decision: DecisionHighlight | null = marker
      ? {
          ...marker,
          color: palette[marker.tone][0],
          background: palette[marker.tone][1],
        }
      : null;
    return {
      sequence: event.sequence,
      tick: event.turn,
      type: event.event_type,
      raw: event,
      payload: event.decision ?? event.evidence ?? event.consequence,
      trace: runtime.eventTrace(event),
      decision,
      keyEvent:
        event.event_type !== "world_transition" ||
        ["scheduled_event", "oxygen", "repairs", "captain_action"].includes(
          event.consequence?.phase,
        ),
    };
  }),
});
const actors: Record<string, string> = {
  adversary: "adversary_decision",
  jev: "dispatch",
  captain: "captain_decision",
  rules: "captain_decision",
};
export const stationProject: ArchitectureProject = {
  document: {
    ...runtime.model,
    schemaVersion: 1,
    modeLabel: "Controller mode",
    id: "station-control",
    title: "STATION CONTROL",
    version: runtime.sourceIndex.simulatorVersion,
    repositoryUrl: "https://github.com/felixglush/world-simulation",
    description:
      "Logical service boundaries. One Python process. Only model calls cross HTTPS.",
    modes: [
      { id: "jev+llm", title: "Jev + AI captain" },
      { id: "llm", title: "AI captain only" },
      { id: "rules", title: "Rules baseline" },
    ],
    defaults: { view: "crew", mode: "jev+llm", allView: "all" },
    components: runtime.model.components.map((c) => ({
      ...c,
      icon: c.icon === "captain" ? "user" : c.icon,
      ...(c.id === "jev"
        ? { modes: ["jev+llm"] }
        : c.id === "captain"
          ? { modes: ["jev+llm", "llm"] }
          : c.id === "rules"
            ? { modes: ["rules"] }
            : {}),
      color: c.id === "adversary" ? "#d58832" : undefined,
    })),
    connections: runtime.model.connections.map((e) => ({
      ...e,
      ...(e.id === "direct-routing" ? { modes: ["llm", "rules"] } : {}),
    })),
    services: runtime.model.services.map((s) => ({
      ...s,
      external: s.id === "external",
    })),
    sources: Object.fromEntries(
      Object.entries(runtime.sourceIndex.sources).map(([key, s]) => [
        key,
        { ...s, language: "python", url: runtime.sourceUrl(s) },
      ]),
    ),
    messageKinds: Object.fromEntries(
      Object.entries(runtime.kindLabels).map(([key, label]) => [
        key,
        {
          label,
          color: runtime.colors[key as runtime.MessageKind],
          private: key === "private",
        },
      ]),
    ),
  },
  replay: {
    runs: runtime.demoRuns.map(normalize),
    labels: {
      tick: "Turn",
      action: "Walk through a turn",
      region: "Turn walkthrough",
      private: "Private audit fact",
      public: "Crew / application event",
      decisionHelp:
        "Malicious = disruptive action type · benign = wait · acceptance shown separately",
    },
    defaults: { view: "overview", component: "world", cursor: 1 },
    import: {
      accept: ".jsonl,.ndjson,.json",
      label: "Load JSONL",
      parse: (text, name) => normalize(runtime.parseRun(text, name)),
    },
    serialize: (run) => ({
      text:
        raw(run)
          .records.map((r) => JSON.stringify(r))
          .join("\n") + "\n",
      filename: `${run.id}.jsonl`,
      mimeType: "application/x-ndjson",
    }),
    snapshot: (id, run, cursor) => {
      const events = runtime.eventsOf(raw(run)),
        snapshot = runtime.componentSnapshot(id, raw(run), events, cursor);
      return {
        ...snapshot,
        ...(["world", "adversary-validation"].includes(id)
          ? {
              fields: runtime.worldAt(events, cursor),
              previous: runtime.worldAt(events, cursor - 1),
            }
          : {}),
      };
    },
    recordedIO: (id, run, cursor) => {
      if (!actors[id]) return undefined;
      const e = runtime
        .eventsOf(raw(run))
        .slice(0, cursor + 1)
        .findLast((e) => e.event_type === actors[id]);
      return e
        ? {
            sequence: e.sequence,
            tick: e.turn,
            input: e.evidence,
            output: e.decision,
            note: "Input is the evidence/context saved in the log and may omit provider request fields. Output is the persisted decision shape; it may flatten the typed result or add audit identifiers.",
          }
        : null;
    },
    metrics: (run, cursor) => {
      const events = runtime.eventsOf(raw(run)),
        state = runtime.worldAt(events, cursor),
        before = runtime.worldAt(events, cursor - 1);
      return {
        title: "WORLD STATE",
        note: "Audit perspective · crew AIs receive public projections only.",
        values: [
          ["oxygen", "Oxygen"],
          ["backup_oxygen", "Backup"],
          ["parts", "Spare parts"],
          ["repair_turns_remaining", "Repair turns"],
        ].map(([id, title]) => ({
          id,
          title,
          value: state[id] ?? "—",
          delta:
            typeof state[id] === "number" && typeof before[id] === "number"
              ? state[id] - before[id]
              : 0,
        })),
        flags: [
          {
            label: state.leak_active ? "Leak active" : "No active leak",
            tone: state.leak_active ? "warning" : "healthy",
          },
          {
            label: state.crew_alive ? "Crew alive" : "Crew lost",
            tone: state.crew_alive ? "healthy" : "warning",
          },
        ],
      };
    },
  },
};
