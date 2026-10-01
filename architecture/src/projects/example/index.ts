import code from "./job-queue.ts?raw";
import { processJob } from "./job-queue";
import type {
  ArchitectureProject,
  ArchitectureDocument,
  Source,
  ReplayRun,
} from "../../core/types";
const path = "architecture/src/projects/example/job-queue.ts";
function source(symbol: string, fields: Source["fields"]): Source {
  const lines = code.split("\n");
  const startLine =
    lines.findIndex(
      (line) =>
        line.includes(`interface ${symbol} `) ||
        line.includes(`function ${symbol}(`),
    ) + 1;
  const endLine = symbol === "processJob" ? startLine + 2 : startLine;
  return {
    path,
    symbol,
    language: "typescript",
    startLine,
    endLine,
    code: lines.slice(startLine - 1, endLine).join("\n"),
    fields,
  };
}
const sources = [
  source("Job", [
    { name: "id", type: "string" },
    { name: "text", type: "string" },
  ]),
  source("Result", [
    { name: "id", type: "string" },
    { name: "characters", type: "number" },
  ]),
  source("processJob", []),
];
const ref = (symbol: string) => ({ path, symbol });
const input = { id: "job-1", text: "hello" },
  output = processJob(input);
const document: ArchitectureDocument = {
  schemaVersion: 1,
  id: "job-queue",
  title: "JOB QUEUE",
  version: "1",
  description:
    "An independent TypeScript worker example. Jobs enter a queue and produce character counts.",
  modes: [{ id: "normal", title: "Worker enabled" }],
  defaults: { view: "pipeline", allView: "pipeline", mode: "normal" },
  services: [
    {
      id: "pipeline",
      title: "Job processing",
      subtitle: "TypeScript module",
      color: "#287c62",
    },
  ],
  views: [
    {
      id: "pipeline",
      title: "Processing pipeline",
      description: "Job → worker → result",
      nodes: ["queue", "worker", "results"],
    },
  ],
  components: [
    {
      id: "queue",
      title: "Job queue",
      icon: "layers",
      summary: "Receive a job.",
      sources: [ref("Job")],
      state: "Waiting jobs.",
      stateTypes: ["Job"],
    },
    {
      id: "worker",
      title: "Worker",
      icon: "gear",
      summary: "Count characters in a job.",
      sources: [ref("processJob")],
      state: "Completed job count.",
      stateTypes: ["Result"],
    },
    {
      id: "results",
      title: "Results",
      icon: "database",
      summary: "Store job results.",
      sources: [ref("Result")],
      state: "Completed results.",
      stateTypes: ["Result"],
    },
  ].map((c) => ({ ...c, service: "pipeline", responsibilities: [c.summary] })),
  contracts: { Job: ref("Job"), Result: ref("Result") },
  sources: Object.fromEntries(sources.map((s) => [`${s.path}:${s.symbol}`, s])),
  messageKinds: {
    job: { label: "Work request", color: "#287c62" },
    result: { label: "Work result", color: "#3568ae" },
  },
  connections: [
    {
      id: "submit",
      source: "queue",
      target: "worker",
      label: "Job",
      kind: "job",
      contract: "Job",
      example: input,
    },
    {
      id: "complete",
      source: "worker",
      target: "results",
      label: "Result",
      kind: "result",
      contract: "Result",
      example: output,
    },
  ].map((e) => ({
    ...e,
    description: `Pass ${e.contract} to the receiver.`,
    when: "During the example run.",
    failure: "The caller handles failures.",
  })),
};
const run: ReplayRun = {
  id: "job-example",
  title: "Count hello",
  description: "A deterministic local example.",
  provenance: "Example TypeScript execution",
  badge: "Example run",
  mode: "normal",
  data: { input, output },
  events: [
    {
      sequence: 0,
      tick: 0,
      type: "queued",
      payload: input,
      raw: { queued: input },
      keyEvent: true,
      decision: null,
      trace: {
        title: "Job queued",
        body: "The queue accepts a job.",
        nodes: ["queue"],
        edges: [],
        private: false,
      },
    },
    {
      sequence: 1,
      tick: 1,
      type: "completed",
      payload: output,
      raw: { input, output },
      keyEvent: true,
      decision: {
        actor: "worker",
        tone: "success",
        label: "Worker · Completed",
        outcome: "Counted five characters",
        color: "#287c62",
        background: "#edf8f0",
      },
      trace: {
        title: "Job completed",
        body: "The worker counted the input characters.",
        nodes: ["queue", "worker", "results"],
        edges: ["submit", "complete"],
        private: false,
      },
    },
  ],
};
export const exampleProject: ArchitectureProject = {
  document,
  replay: {
    runs: [run],
    labels: {
      tick: "Step",
      action: "Walk through a job",
      region: "Job walkthrough",
      private: "Internal event",
      public: "Application event",
      decisionHelp: "Completed work is highlighted.",
    },
    defaults: { view: "pipeline", component: "worker", cursor: 0 },
    snapshot: (_id, _run, cursor) => ({
      label: "Worker state",
      value: { completed: cursor },
      fields: { completed: cursor },
      previous: { completed: Math.max(0, cursor - 1) },
    }),
    metrics: (_run, cursor) => ({
      title: "JOB METRICS",
      note: "Values from the example worker.",
      values: [{ id: "completed", title: "Completed", value: cursor }],
      flags: [],
    }),
    recordedIO: (id, _run, cursor) =>
      id !== "worker"
        ? undefined
        : cursor === 0
          ? null
          : {
              sequence: 1,
              tick: 1,
              input,
              output,
              note: "Complete example function input and return value.",
            },
  },
};
export const staticExampleProject: ArchitectureProject = {
  document: {
    ...document,
    id: "job-queue-static",
    title: "JOB QUEUE · STATIC",
  },
};
