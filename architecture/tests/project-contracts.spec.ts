import { test, expect } from "@playwright/test";
import { validateProject, validateRun } from "../src/core/validate";
import type { ArchitectureProject, ReplayRun } from "../src/core/types";
function project(): ArchitectureProject {
  return {
    document: {
      schemaVersion: 1,
      id: "minimal",
      title: "Minimal",
      version: "1",
      description: "",
      components: [
        {
          id: "a",
          service: "s",
          title: "A",
          icon: "unknown",
          summary: "",
          responsibilities: [],
          sources: [],
          state: "",
          stateTypes: [],
        },
      ],
      services: [{ id: "s", title: "S", subtitle: "", color: "#000" }],
      connections: [],
      views: [{ id: "all", title: "All", description: "", nodes: ["a"] }],
      contracts: {},
      sources: {},
      messageKinds: {},
      modes: [{ id: "default", title: "Default" }],
      defaults: { view: "all", allView: "all", mode: "default" },
    },
  };
}
const run = (): ReplayRun => ({
  id: "r",
  title: "R",
  description: "",
  provenance: "",
  badge: "",
  data: null,
  events: [
    {
      sequence: 0,
      tick: 0,
      type: "sample",
      payload: null,
      raw: null,
      keyEvent: true,
      decision: null,
      trace: {
        title: "Sample",
        body: "",
        nodes: ["a"],
        edges: [],
        private: false,
      },
    },
  ],
});
test("project contracts reject unresolved references and invalid replay defaults", () => {
  expect(() => validateProject(project())).not.toThrow();
  const invalid = project();
  invalid.document.components[0].sources = [
    { path: "missing.ts", symbol: "Missing" },
  ];
  expect(() => validateProject(invalid)).toThrow("missing source");
  const badView = project();
  badView.document.views[0].nodes.push("missing");
  expect(() => validateProject(badView)).toThrow("unknown view component");
  const badVersion = project();
  Object.assign(badVersion.document, { schemaVersion: 2 });
  expect(() => validateProject(badVersion)).toThrow(
    "unsupported schema version",
  );
});
test("normalized replay rejects empty runs and unknown highlights", () => {
  expect(() => validateRun(project(), run())).not.toThrow();
  expect(() => validateRun(project(), { ...run(), events: [] })).toThrow(
    "at least one event",
  );
  const invalid = run();
  invalid.events[0].trace.nodes = ["missing"];
  expect(() => validateRun(project(), invalid)).toThrow("unknown component");
});
