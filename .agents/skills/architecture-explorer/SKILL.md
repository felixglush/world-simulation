---
name: architecture-explorer
description: Create or update a source-backed, interactive architecture overview for a codebase using the reusable React Flow explorer. Use for component maps, service boundaries, code/state inspection, and evidence-backed or illustrative walkthroughs.
---

# Architecture explorer

Use this workflow with Codex, Claude, or another repository-capable agent. The agent
analyzes the repository; the shared canvas renders a versioned document. Do not put
repository-specific logic into the renderer. This repository ships the workflow;
standalone skill distribution and automatic installation into other agents remain
separate packaging work.

## Locate the contract

Read the target repository's instructions. Locate the explorer host and read
`architecture/src/core/types.ts`, `architecture/src/core/validate.ts`, and
`architecture/docs/modularity.md` relative to the host root. These are the canonical
contracts; do not copy a stale shape from this skill. If the host is not installed,
report that prerequisite rather than claiming a finished interactive artifact.

Inspect `src/projects/example/` and `src/projects/http-example/` under that host's
`architecture/` directory for independent adapters. Do not copy Station Control IDs,
AI actors, modes, metrics, or Python generation assumptions into another project.

## Analyze and produce

1. Read entry points, manifests, representative business flows, persistence and
   external integrations. Follow actual calls and data ownership. Choose a useful
   component level rather than turning every source file into a canvas node.
2. Produce an `ArchitectureDocument` in a new `src/projects/<project>/` adapter.
   Give services, components, connections, modes and views stable IDs. Add an
   overview and focused views; keep supporting implementation detail optional.
3. Back component source references with actual code, path, symbol, language and
   matching line ranges. Prefer revision-pinned source URLs when available; bundle
   code for offline inspection. Leave external/unindexed sources empty. Describe
   what is unknown instead of manufacturing definitions or state.
4. Describe each directed connection's kind, trigger and failure behavior from
   evidence. Distinguish calls, dependencies and messages in metadata. Supply
   schemas and examples only where supported; they are optional. Use descriptions
   to cite source paths/symbols and explicitly label any inferred relationship.
5. Describe state ownership independently of replay. A missing state schema or
   snapshot means unavailable evidence, not a stateless component.
6. If useful, supply a normalized `ReplayAdapter`. Recorded traces must identify
   their provenance. If no execution evidence exists, an illustrative flow may
   explain the code path, but its title/badge/provenance must say it is illustrative
   and its narrative must not claim observed execution. Never invent recorded
   AI decisions, state changes, metrics, or complete I/O pairs.
7. Normalize project logs inside the adapter. Map trace nodes/edges to document
   IDs; keep snapshots and metrics at or before the cursor. Supply only supported
   capabilities: state, metrics, recorded I/O, import and export are optional.
   Replay does not start the target application and is not live instrumentation.
8. Wire the adapter through the host composition root (`src/main.tsx`) or pass
   `{ document, replay? }` to `App`. Never branch on project IDs inside shared UI.

Only bundle shareable evidence. Do not include credentials or private logs. Do not
run live models or paid services merely to create a walkthrough.

## Validate and deliver

- Run `validateProject` against the adapter (also enforced by the mounted host).
  This checks references and consistency of trusted typed adapter data, not the
  shape/security of arbitrary uploaded architecture JSON.
- From `architecture/`, run `npm run check`, `npm run format:check`, `npm run build`
  and the relevant browser tests. Add a representative walkthrough for a new
  adapter: navigate a component, inspect code/state/connections, advance any flow,
  and verify unknown/unavailable evidence is presented honestly.
- For shared renderer changes, run all examples, route collision checks, and the
  offline build test. Do not require the target application to start just to view
  its architecture.
- Deliver the bundled `dist/index.html` and source adapter or use the configured
  static hosting workflow if publication is authorized. Report what was validated,
  what evidence is illustrative, and any missing source/runtime coverage.

## Maintain

Update the adapter and evidence when entry points, ownership, boundaries, contracts
or event formats change. Increment document version when replacing structure.
Regenerate source snapshots using that project's extraction process and verify
line ranges against source. The host's `scripts/generate.py` is Station Control
specific; it is not a universal extractor. Keep target repository instructions
pointing agents to this workflow and the appropriate verification commands.
