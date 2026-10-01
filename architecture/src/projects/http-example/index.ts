import code from "./handler.go?raw";
import type { ArchitectureProject } from "../../core/types";

const path = "architecture/src/projects/http-example/handler.go";
const ref = { path, symbol: "Health" };
/** Deliberately has no message schema, metrics, state projection, or AI decisions. */
export const httpExampleProject: ArchitectureProject = {
  document: {
    schemaVersion: 1,
    id: "http-example",
    title: "HTTP HEALTH",
    version: "1",
    description:
      "A Go handler and external caller. The walkthrough is illustrative, not captured execution.",
    modes: [{ id: "default", title: "Default" }],
    defaults: { view: "all", allView: "all", mode: "default" },
    services: [
      {
        id: "client",
        title: "External client",
        subtitle: "Outside this repository",
        color: "#3568ae",
        external: true,
      },
      { id: "server", title: "HTTP service", subtitle: "Go", color: "#287c62" },
    ],
    views: [
      {
        id: "all",
        title: "Request path",
        description: "Client calls the health handler",
        nodes: ["routing-extent", "boundary-server"],
      },
    ],
    components: [
      {
        id: "routing-extent",
        service: "client",
        title: "Client",
        icon: "unknown",
        summary: "Send an HTTP request.",
        responsibilities: ["Request health status"],
        sources: [],
        state: "External state is not indexed.",
        stateTypes: [],
      },
      {
        id: "boundary-server",
        service: "server",
        title: "Health handler",
        icon: "code",
        summary: "Respond with status 200 and ok.",
        responsibilities: ["Write an HTTP response"],
        sources: [ref],
        state: "Stateless handler; response state belongs to the HTTP writer.",
        stateTypes: [],
      },
    ],
    connections: [
      {
        id: "request",
        source: "routing-extent",
        target: "boundary-server",
        label: "HTTP call",
        kind: "call",
        description:
          "The caller invokes Health through an HTTP server; route registration is outside this example.",
        when: "When dispatched by the server.",
        failure: "Write errors are ignored by this minimal example.",
      },
    ],
    messageKinds: { call: { label: "Calls", color: "#287c62" } },
    contracts: {},
    sources: {
      [`${path}:Health`]: {
        ...ref,
        language: "go",
        startLine: 5,
        endLine: 9,
        code: code.split("\n").slice(4, 9).join("\n"),
        fields: [],
      },
    },
  },
  replay: {
    defaults: { view: "all", cursor: 0 },
    labels: {
      tick: "Step",
      action: "Walk through a request",
      region: "HTTP walkthrough",
      private: "Internal",
      public: "Example event",
      decisionHelp: "",
    },
    runs: [
      {
        id: "health-example",
        title: "Health request",
        description:
          "Illustrative steps derived from the handler; no server is started.",
        provenance: "Illustrative flow · not recorded execution",
        badge: "Illustrative",
        data: null,
        events: [
          {
            sequence: 0,
            tick: "request",
            type: "request",
            payload: { method: "GET" },
            raw: null,
            keyEvent: true,
            decision: null,
            trace: {
              title: "Request dispatched",
              body: "Illustrative call into the handler.",
              nodes: ["routing-extent", "boundary-server"],
              edges: ["request"],
              private: false,
            },
          },
          {
            sequence: 1,
            tick: "response",
            type: "response",
            payload: { status: 200, body: "ok" },
            raw: null,
            keyEvent: true,
            decision: null,
            trace: {
              title: "Response written",
              body: "Expected successful response from Health; not observed runtime state.",
              nodes: ["boundary-server"],
              edges: [],
              private: false,
            },
          },
        ],
      },
    ],
  },
};
