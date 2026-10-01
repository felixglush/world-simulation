import { readFileSync } from "node:fs";
const model = JSON.parse(
  readFileSync(new URL("../src/model.json", import.meta.url), "utf8"),
);
const sources = JSON.parse(
  readFileSync(new URL("../src/source-index.json", import.meta.url), "utf8"),
).sources;
const ids = new Set(model.components.map((node: { id: string }) => node.id));
if (ids.size !== model.components.length)
  throw new Error("Duplicate component IDs");
const edges = new Set();
for (const edge of model.connections) {
  if (edges.has(edge.id) || !ids.has(edge.source) || !ids.has(edge.target))
    throw new Error(`Invalid connection ${edge.id}`);
  if (!model.contracts[edge.contract])
    throw new Error(`Missing contract ${edge.contract}`);
  edges.add(edge.id);
}
for (const node of model.components) {
  for (const ref of node.sources)
    if (!sources[`${ref.path}:${ref.symbol}`])
      throw new Error(`Missing code for ${node.id}`);
  for (const state of node.stateTypes)
    if (!model.contracts[state]) throw new Error(`Missing state type ${state}`);
}
for (const view of model.views)
  if (view.nodes.some((id: string) => !ids.has(id)))
    throw new Error(`Broken view ${view.id}`);
console.log(
  `Verified ${ids.size} components, ${edges.size} messages, and their source/state contracts.`,
);
