import type { ArchitectureProject, SourceRef } from "./types";
export function validateProject(project: ArchitectureProject) {
  const d = project.document;
  const fail = (message: string): never => {
    throw new Error(`Invalid architecture project: ${message}`);
  };
  if (d.schemaVersion !== 1) fail("unsupported schema version");
  function ids(items: { id: string }[], kind: string) {
    const set = new Set(items.map((x) => x.id));
    if (set.size !== items.length) fail(`duplicate ${kind} IDs`);
    return set;
  }
  const components = ids(d.components, "component"),
    services = ids(d.services, "service"),
    modes = ids(d.modes, "mode"),
    views = ids(d.views, "view"),
    edges = ids(d.connections, "connection");
  if (
    !views.has(d.defaults.view) ||
    !views.has(d.defaults.allView) ||
    !modes.has(d.defaults.mode)
  )
    fail("invalid defaults");
  const ref = (r: SourceRef) => {
    if (!d.sources[`${r.path}:${r.symbol}`])
      fail(`missing source ${r.path}:${r.symbol}`);
  };
  const contract = (name: string) => {
    if (!d.contracts[name]) fail(`missing contract ${name}`);
    ref(d.contracts[name]);
  };
  const checkModes = (values?: string[]) =>
    values?.forEach((mode) => {
      if (!modes.has(mode)) fail(`unknown mode ${mode}`);
    });
  Object.values(d.contracts).forEach(ref);
  d.components.forEach((c) => {
    if (!services.has(c.service)) fail(`unknown service ${c.service}`);
    c.sources.forEach(ref);
    c.stateTypes.forEach(contract);
    checkModes(c.modes);
  });
  d.connections.forEach((e) => {
    if (!components.has(e.source) || !components.has(e.target))
      fail(`broken connection ${e.id}`);
    if (!d.messageKinds[e.kind]) fail(`unknown message kind ${e.kind}`);
    contract(e.contract);
    checkModes(e.modes);
  });
  d.views.forEach((v) =>
    v.nodes.forEach((id) => {
      if (!components.has(id)) fail(`unknown view component ${id}`);
    }),
  );
  const replay = project.replay;
  if (replay) {
    if (
      !views.has(replay.defaults.view) ||
      (replay.defaults.component && !components.has(replay.defaults.component))
    )
      fail("invalid replay defaults");
    replay.runs.forEach((run) => validateRun(project, run));
  }
  return { components, edges };
}
export function validateRun(
  project: ArchitectureProject,
  run: import("./types").ReplayRun,
) {
  const d = project.document,
    components = new Set(d.components.map((x) => x.id)),
    edges = new Set(d.connections.map((x) => x.id));
  if (!run.events.length)
    throw new Error("A replay must contain at least one event.");
  if (run.mode && !d.modes.some((m) => m.id === run.mode))
    throw new Error("Unknown replay mode.");
  const sequences = new Set<number>();
  for (const event of run.events) {
    if (!Number.isInteger(event.sequence) || sequences.has(event.sequence))
      throw new Error("Invalid replay event sequence.");
    sequences.add(event.sequence);
    if (
      event.trace.nodes.some((id) => !components.has(id)) ||
      event.trace.edges.some((id) => !edges.has(id)) ||
      (event.decision && !components.has(event.decision.actor))
    )
      throw new Error("Replay references an unknown component or connection.");
  }
  return run;
}
