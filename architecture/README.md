# Station Control architecture explorer

A React Flow canvas for the current Python implementation, with service boundaries,
typed messages, source code, state schemas, and recorded-run playback.
Vercel AI Elements supplies the syntax-highlighted code and JSON viewers;
shadcn/ui (Radix) supplies inspector tabs, replay buttons, and status badges.
Tailwind CSS styles these shared components alongside the custom diagram layout. These are
logical boundaries inside one process; only model requests cross an HTTPS boundary.

## Open the explorer

[Hosted canvas](https://felixglush.github.io/world-simulation/) ·
[Example flow](https://felixglush.github.io/world-simulation/?run=demo_deception)

The example opens a paused recorded run. Use **Play run**, **Next step**, or
**Next decision** to follow adversary actions, Jev classifications, captain decisions,
and world changes. Demo AI responses are scripted; no live simulator or model API
starts. Code, state, schemas, and example logs are bundled. Imported logs stay in
browser memory. Hosted links require the initial Pages setup below.

### GitHub Pages

Select **Settings → Pages → Build and deployment → Source → GitHub Actions**.
The integration preparing this change could not enable Pages (GitHub HTTP 403).
Then run **Actions → Architecture canvas → Run workflow**. Before this stack merges,
the workflow runs on pushes to `codex/architecture-github-pages`; rerun its latest
workflow after enabling Pages. If the `github-pages` environment restricts deployments,
allow that branch. After merging, use `main` and remove the temporary feature branch
from the workflow triggers, deploy condition, and environment rules.

The workflow checks, tests, and builds before deployment. Pull requests only validate
and upload the downloadable `architecture-offline` HTML artifact; they never deploy.
Changes to `architecture/` on `main` automatically update the site. The artifact can
also be opened without a server. Only bundled source and scripted fixtures are
published; local `runs/` and credentials are excluded.

`?run=<bundled-run-id>` opens a paused replay; unknown IDs show the overview. This
works with any project adapter and can be combined with `?project=example&run=<id>`.

### Local development

Use Node 24 and npm 11:

```bash
cd architecture
npm ci
npm run dev
```

Open http://127.0.0.1:4173. Stop the server with Ctrl-C.

For a portable, offline HTML file:

```bash
npm run build
```

Open `dist/index.html` directly in a browser. Everything needed for the canvas,
source inspector, and demo runs is bundled in that single file. GitHub source links
require a connection; browsing the bundled code does not.

## Explore and follow a run

- Drag to pan, scroll to zoom, or use the fit/focus controls.
- Use one canvas with four **Focus** presets: Everything, Crew response, World &
  adversary, and Logging & evaluation. The old System map / All components split
  is now **Display → Implementation details**.
- Open **Find a component** (or press `/`) to search for a component, symbol, or
  message. The browser closes after selection; the selected component is revealed
  without changing your focus preset. Press Escape to close the browser/inspector.
- **Display** contains controller mode, message labels, private flows, and replay
  following. Labels are hidden until a connection is focused, or you enable them.
- **Walk through a turn** opens the replay notebook. Hide/reopen preserves the run
  and position. **Event journal** expands the full timeline; **Recorded message**
  expands the payload and grows the bottom panel to give code roughly 10–20 visible
  lines, depending on the viewport. Closing it restores the compact panel. Following
  reveals actors outside the current focus preset.
  The inspector opens only when you select a component or connection.
- Click a component for its responsibilities and connections. **Code** opens actual
  Python definitions with line numbers; **State** shows state ownership, schemas, and
  values at the selected replay event. Click the code icon for direct source access.
- The **I/O** tab groups incoming and outgoing message contracts with Python field
  types, source definitions, and illustrative example payloads. For a connection,
  it explicitly shows output from its sender → input to its receiver. Contracts
  cover all configured modes; examples show selected fields rather than complete
  serialized instances. AI actor panels also show the latest recorded input and
  decision at or before the replay cursor. Saved evidence may omit request fields,
  and persisted decisions may flatten result types or include audit identifiers.
- Click a connection for its contract fields, payload example, timing, and failure policy.
- Choose **Walk through a turn** to follow adversarial input, world changes, public
  events, Jev classification, captain proposals, validation, repair, and closure.
  Play, step, or scrub; **Follow event** moves the camera along the active path.
- Actor decisions get labeled canvas badges, colored journal entries, and a review
  banner: red for disruptive adversary choices, green for benign waits, blue for
  captain decisions, and purple for Jev assessments. Accepted/rejected outcomes are
  shown separately; unknown action types remain unclassified. These are reviewer
  annotations based on recorded action types, not Jev verdicts or proof of impact.
- Use **Previous/Next decision**, **Decisions only**, or **Pause at decisions** to
  focus a review. Normal playback gives decisions twice the viewing time of other
  events. In the current action catalog, the adversary's benign choice is `wait`.
- Load a completed simulator 0.3.2+ schema-1 JSONL run to inspect your own data.
  Files stay in the browser; there is no upload or model request. The 5 MB limit
  keeps this small documentation viewer responsive.

The two bundled demonstrations execute the **real Python simulator with scripted AI
providers**. Their decisions are illustrative, not outputs from live models.
World state is reconstructed from authoritative initialization and transition records.
Incident state is explicitly labeled as a partial projection of the audit log;
provider panels show recorded requests/results, not persistent model memory.
Private audit information is shown for review and never claimed to be crew-visible.
This is a recorded-run player, not a second implementation of the simulator.

## Maintain and verify

`src/model.json` owns the human-authored components, logical services, views, message
contracts, and examples. `src/projects/station-control/` projects audit records into generic display state.
`Graph.tsx`, `Inspector.tsx`, and `RunPlayer.tsx` render those models independently.

When Python interfaces or behavior change, update the model and run from the repository
root with the locked Python dependencies installed:

```bash
uv run --frozen python architecture/scripts/generate.py
uv run --frozen python architecture/scripts/generate.py --check
```

The generator extracts actual Python symbols and annotated fields into
`src/source-index.json` and records two zero-call demonstrations in
`src/demo-runs.json`. Do not hand-edit these generated files. Commit runtime source
changes before generating the final source index so GitHub permalinks match its
recorded revision. `--check` detects stale source and demo fixtures.

Inside `architecture/`:

```bash
npm run check
npm run format:check
npm run build
npx playwright install chromium
npm test
```

For an existing system Chromium, use `CHROMIUM_PATH=/usr/bin/chromium npm test`.
Browser checks cover navigation, contracts, actual code/state, playback, JSONL import,
mobile layout, and opening the standalone HTML without a server.

The repository-installed [PR Lens skill](../.agents/skills/pr-lens/SKILL.md) informed
the source references, message walkthroughs, and boundaries. Its upstream provenance
and MIT license are recorded beside the skill. No PR Lens hosted renderer is needed.

## UI component maintenance

Official registry sources are installed in `src/components/ai-elements` and
`src/components/ui`; `components.json` configures their aliases. `SourceBlock.tsx`
composes Vercel's CodeBlock, header, actions, and copy button for source and payloads.
The source snapshot retains original Python line numbers. Tabs support arrow-key
navigation without advancing the run player.

Install additional components selectively with `npx shadcn@latest add <component>`.
The AI Elements code block was installed from
`https://elements.ai-sdk.dev/api/registry/code-block.json`. Local adaptations use
Shiki's JavaScript engine with bundled Python/JSON/JavaScript/TypeScript grammars and two GitHub themes,
and key the token cache by full source text. Preserve these when refreshing from
the registry: importing the full language loader inflates the offline artifact.
`src/ui.css` defines shared design tokens and CSS layer ordering; the existing
light diagram style remains in `src/styles.css`. No Vercel deployment is required.

## Reuse in another codebase

See the [modularity review and extension contract](docs/modularity.md).
`App` accepts an `ArchitectureProject` rather than importing project data. Shared
canvas, inspector, I/O, and playback components depend on `src/core/types.ts`.
A project supplies a versioned architecture document and an optional replay adapter.
The TypeScript job-queue example at `/?project=example` exercises the same UI;
`/?project=static-example` demonstrates operation without any runtime recordings.
`npm run check` enforces the shared UI → contracts dependency boundary.

## Presentation design

Inspired by [PR Lens](https://prlens.dev/)'s diagram-first canvas and progressive
inspection, with an original field-notebook treatment: warm paper, muted ink,
serif section titles, numbered replay notes, and color concentrated on decisions.
No remote fonts, assets, or PR Lens runtime are required. `CanvasToolbar` owns focus
and display controls; `ComponentBrowser` owns the searchable drawer; `Graph` and
`RunPlayer` retain rendering and replay responsibilities. `notebook.css` is the
presentation skin over shared explorer controls. Project metadata marks optional
infrastructure with `Component.detail: "implementation"`; shared UI uses no actor IDs.

### Layout and routing

[ELK.js](https://github.com/kieler/elkjs) supplies grouped, layered layout and
orthogonal obstacle-aware routes, following [React Flow's layout guidance](https://reactflow.dev/learn/layouting/layouting).
React Flow renders the resulting node positions, edge bend points, and matching
connection handles. Service boundaries remain grouped, with room reserved for
connections and labels. The full map is larger; use focus presets and zoom to read
individual components. Crossing arrows can still occur, but routes must not cross
component interiors.

`src/diagram/layout.ts` adapts project-neutral metadata to ELK.
`useDiagramLayout.ts` caches topology results and ignores obsolete asynchronous
results when filters change quickly. Replay color changes reuse the layout;
revealing previously hidden nodes requires a new layout. Replay framing includes
routed paths, and initial fitting waits for the viewport. Geometry/browser checks
in `tests/routing.spec.ts` cover modes, focus presets, cycles, parallel links,
self-loops, actual SVG/card collisions, and initial fitting.

ELK is bundled into the standalone HTML: there is no runtime layout service, CDN,
or worker download. The offline artifact is larger (about 2.9 MB before compression).

## Agent-authored architecture

Codex and Claude agents can use the repository's
[architecture explorer skill](../.agents/skills/architecture-explorer/SKILL.md)
to inspect a codebase and produce its document and optional replay adapter.
The renderer does not depend on which agent performs the analysis. See the
[extension contract](docs/modularity.md) for supported capabilities and limits.
Try `?project=http-example` for a Go handler with schema-less calls and an
illustrative flow without metrics or state projections. Standalone skill/plugin
distribution remains separate packaging work.
