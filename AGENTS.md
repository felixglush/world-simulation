# Working on Station Control

Read README.md for the product contract. Setup and operating commands are below.

## Keep these instructions current

Update this file in the same change whenever setup, dependencies, entry points, CLI flags, defaults, model configuration, verification, or cleanup commands change. Check the instructions against the implementation and CLI help. Replace obsolete commands and verify affected examples offline where possible. Record any checks that could not run. Keep product descriptions in README.md and operating instructions here.

For testing and test-first implementation, use the $tdd skill at .agents/skills/tdd/SKILL.md.
For architecture design or review, use $clean-architecture at .agents/skills/clean-architecture/SKILL.md.

Use fake providers for routine tests. Live model experiments require explicit budgets and recorded model/configuration metadata.

## Environment commands

- Install: bash scripts/setup-cloud.sh
- Verify dependencies offline: uv run --frozen python scripts/check_environment.py
- Require injected API key: uv run --frozen python scripts/check_environment.py --require-api-key
- Lint: uv run --frozen ruff check .
- Format check: uv run --frozen ruff format --check .
- Tests: uv run --frozen pytest

Pause for user review before commits, pushes, cloud publication, or paid experiments unless separately authorized.

# Setup and operating commands

## Development setup

Use Python 3.12 and the locked dependencies:

```bash
bash scripts/setup-cloud.sh
```

Use this same command as the Codex Cloud install command. It installs uv 0.12.21 when needed, syncs dependencies, and runs offline SDK, credential-handling tests, lint, and formatting checks. The simulator, mission, adapter, and CLI checks run offline with fake model transports.

The CLI defaults `run` and `rerun` to the AI captain (`llm`). A live mission requires `OPENROUTER_API_KEY`, a captain model, `--max-calls`, and `--max-output-tokens-per-call`; if any are missing, it stops before creating a run log and never switches to rules. Select models at invocation with `--captain-model`, `--jev-model`, and `--adversary-model`. Each flag overrides its corresponding `CAPTAIN_MODEL`, `JEV_MODEL`, or `ADVERSARY_MODEL` environment variable; omitted flags use those variables as defaults. The `jev+llm` controller requires a Jev model, and `--adversary llm` requires an adversary model. Model flags alone do not enable a role. Supply credentials through the environment and allow HTTPS access to `openrouter.ai`. All model adapters use OpenRouter. `.env.example` is not automatically loaded. Select captain and adversary models with tool calling.

The checker reads `OPENROUTER_API_KEY` from the process environment and never prints it. To require that the cloud value is present:

```bash
uv run --frozen python scripts/check_environment.py --require-api-key
```

This checks injection and SDK construction, not whether the API accepts the credential. Without the flag, setup can run without a key. No model requests are sent.

The project's TDD skill and supporting guides are stored in `.agents/skills/tdd` for local and cloud tasks. Personal skills on your Mac are not synced to cloud environments. See [Codex Cloud skill availability](https://learn.chatgpt.com/docs/environments/cloud-environments#current-limitations).

## Run, replay, and compare missions

The in-memory multi-world prototype has an offline scripted trade story. It prints
public evidence and decisions as JSON, makes no model calls, and does not write or
change the versioned mission-log contract:

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade --turns 20
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade --economy --turns 20
```

The horizon includes purchase, shipment, repair, later failure, batch investigation,
quarantine, and replacement. Verify with `uv run --frozen pytest tests/test_trade.py
tests/test_trade_mission.py tests/test_trade_cli.py`.

The economy variant creates a station, industrial supplier, ice moon, and agricultural
world through `SimulationFacade`. It runs finite production, stock-based prices, and
independent deterministic resource policies. Credits, resource movement, expenditures,
and revenue come from the trade ledger. Configure custom worlds with `create_world`
(lots, capacities, sale reserves, recipe, and price rules) and their policies with
`create_decision_system` (scripted commands, resource targets, cash reserve, strategy,
or an injected fake policy). Policies receive only local public views and market offers.
Verify with `uv run --frozen pytest tests/test_economy.py tests/test_economy_facade.py
tests/test_facade.py tests/test_facade_validation.py tests/test_trade_cli.py`.

The deception prototype keeps hidden part triggers, cargo yields, residual damage,
sensor drift, and report ancestry separate from governor observations. Configure
bounded authored conditions with `SimulationFacade.configure_deception` before the
first turn; compose a parameterized evidence-driven policy with
`create_decision_system(kind="investigation")`. Its inspections are scoped to the
chosen load, assays consume samples, and tracing discloses one provenance hop at a
time. Reports remain claims and do not repair equipment or verify quality.
The authored stories begin with a maintenance fault. The investigation policy stocks
parts and attempts an initial repair; on a healthy station it may receive one public
`repair_not_needed` result before continuing other duties.

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade --deception supply_chain --turns 40
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade --deception incomplete_repair --turns 40
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade --deception resource_diversion --turns 40
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade --deception benign --turns 40
```

`--deception` and `--economy` are exclusive. The default deception policy is
`investigate`; `--policy trust` is a deliberately unsafe scripted comparison and
makes no claim about AI performance. The common trade CLI default remains 20 turns;
use the explicit 40-turn horizon above to include recovery, source tracing, and later
recurrence checks. The domain API defaults the deception story to 40 turns. These
rules-based commands print public evidence and decisions without writing mission
artifacts or changing the existing versioned JSONL contract.

For a live crew on a deception story, select `--controller llm` for the captain or
`--controller jev+llm` for Jev plus the captain. The trade controller defaults to
`rules`, preserving the existing no-model behavior. Live crew control requires
`--deception` and `--policy investigate`; it cannot be combined with `--economy` or
the `trust` baseline. Configure `OPENROUTER_API_KEY`, explicit model IDs, and finite
`--max-calls` and `--max-output-tokens-per-call` budgets. Both providers share the call
budget. The JSON summary includes resolved model IDs and the configured budget, never
the credential. For example:

```bash
export OPENROUTER_API_KEY=...
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control trade \
  --deception supply_chain --controller jev+llm \
  --captain-model "<captain-model-id>" --jev-model typesafe/jev-1.13 \
  --max-calls 120 --max-output-tokens-per-call 512 --turns 40
```

Verify with `uv run --frozen pytest tests/test_deception.py tests/test_conditional_defects.py
tests/test_quality.py tests/test_sensor_drift.py tests/test_deception_facade.py
tests/test_deception_cli.py tests/test_crew_governor.py tests/test_crew_cli.py`. The
process tests run all four stories twice for deterministic output and cover the benign
counterpart and real credit exhaustion in the unsafe diversion baseline. The crew CLI
process test starts a local fake-provider HTTP server and requires loopback network
access.

Run a short AI-led mission with the default `llm` controller, replacing the model placeholder. The CLI saves a versioned JSONL run under `runs/` unless `--output` names another path:

```bash
export OPENROUTER_API_KEY=...
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario leak --seed 41 --turns 48 --captain-model "<captain-model-id>" \
  --max-calls 40 --max-output-tokens-per-call 256
```

For offline runs and rules benchmarks, select rules explicitly with `--controller rules`. With the adversary off, these runs need no key or model budget and make no model requests.

Also use `--adversary off` for a fully offline run. An AI adversary still makes model requests when the captain uses rules. Run these commands from the repository root after setup:

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control scenarios
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario leak --controller rules --adversary off --seed 41 --turns 48 \
  --output runs/leak-41-offline.jsonl
```

Use a fresh output path each time. To combine YAML presets in the same station:

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario false_authority --scenario partial_delivery --scenario-spacing 8 \
  --controller rules --adversary off --seed 101 --turns 48 \
  --output runs/parallel-offline.jsonl
```

Repeat `--scenario-file` to combine custom files instead. Do not mix file and named selection. The four original scenario shortcuts can only be selected individually. Omit spacing to overlap YAML schedules. Starting settings apply from mission start; spacing shifts events only.

The original scenarios are `normal`, `leak`, `faulty_sensor`, and `misleading_report`. The [YAML scenario library](docs/scenarios.md) adds harder and deceptive missions; list them with `python -m station_control scenarios`, select an ID with `--scenario`, or load a custom file with `--scenario-file`. Repeat either selector to combine YAML scenarios in one shared station; `--scenario-spacing 8` staggers successive event schedules by eight turns. Each log stores the scenario, seed, simulator and controller configuration, instruction and rubric versions, model identifiers, per-turn observed evidence and decisions, consequences, debug state, and evaluation metrics.

Replay validates a completed log and displays each turn's evidence, decision, consequence, and separate world-state debug view:

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control replay runs/RUN_ID.jsonl
```

Logs with the current JSONL schema remain replayable across simulator versions. Rerunning requires the same simulator version because scenario behavior may have changed.

Rerun the saved scenario and seed with a changed setting into a new file. A rerun defaults to the AI controller, including when the saved run used rules; provide the live configuration and budgets above. It inherits the saved adversary mode and disruption allowance. To keep a rerun offline, pass both `--controller rules` and `--adversary off`. Existing output files are never replaced.

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control rerun runs/leak-41-offline.jsonl \
  --controller rules --adversary off --evidence-access history \
  --output runs/leak-41-offline-history.jsonl
```

YAML reruns use the definition saved in the log. They do not reopen the source files. To change scenario spacing, select the source scenarios again.

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control rerun runs/RUN_ID.jsonl \
  --evidence-access history --turns 48 --captain-model "<captain-model-id>" --max-calls 40 \
  --max-output-tokens-per-call 256 --output runs/RUN_ID-history.jsonl
```

For a model-backed run, configure the key in the environment and select models with flags or environment defaults. Pass explicit finite call and output-token budgets. The CLI records the resolved model identifiers and budgets without recording the API key. These model flags also work on `rerun`: current flags take precedence over current environment values; saved model identifiers are retained for audit but are not reused as defaults. Blank model flags are rejected. All enabled model adapters share the call limit; the token limit applies to the captain and adversary because Jev exposes no documented output-token limit. This is not a dollar cap. Live endpoint interoperability was not exercised during offline verification:

```bash
# Configure OPENROUTER_API_KEY first; replace the captain model placeholder.
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario leak --seed 41 --turns 48 --controller jev+llm \
  --captain-model "<captain-model-id>" --jev-model typesafe/jev-1.13 \
  --max-calls 40 --max-output-tokens-per-call 256 --output runs/jev-leak-41.jsonl
```

See [the reproducible offline experiment](docs/mvp-experiment.md) and [service boundaries](docs/architecture.md).

Add `--adversary llm --adversary-budget 3` and select `--adversary-model` (or `ADVERSARY_MODEL`) to introduce
an AI opponent that reads current world state and selects bounded disruptions alongside
your scenarios. Its private context stays separate from captain evidence. It shares the
model-call budget with the crew. See [the adversary action space and run guide](docs/adversary.md).

For an authorized live experiment with an AI captain and adversary:

```bash
# Configure OPENROUTER_API_KEY securely before running; replace both model placeholders.
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario false_authority --scenario partial_delivery --turns 48 \
  --adversary llm --adversary-budget 3 \
  --captain-model "<captain-model-id>" --adversary-model "<adversary-model-id>" \
  --max-calls 120 --max-output-tokens-per-call 512 \
  --output runs/adversary-mission.jsonl
```

Disruption points range from 0 to 6; the default is 3. They limit accepted sabotage actions, not model requests. Live settings are still required when the adversary is enabled with zero points. Use `--adversary off` to disable it. Current model flags or environment values supply rerun models; saved model IDs are not reused as defaults.

For a controlled comparison, disable the adaptive adversary and keep the scenario definition and seed fixed across controller configurations so the external events match. Adaptive adversary choices can differ across runs with the same seed. Use one seed set for tuning, then report results on a separate held-out set; keep every run in its own output file. For example, use seeds `1, 2, 3` while adjusting settings and seeds `101, 102, 103` for the held-out comparison. Add the explicit budgets above to every live-controller invocation. No benchmark service or shared mutable run state is needed.

## Optional Docker checks

Cloud setup runs directly in its provided environment. Docker gives local development an additional Linux verification path:

```bash
docker build --pull -t world-simulation:dev .
docker run --rm world-simulation:dev
docker run --rm world-simulation:dev uv run --frozen pytest
docker run --rm world-simulation:dev uv run --frozen ruff check .
docker run --rm world-simulation:dev uv run --frozen ruff format --check .
```

`docker build` packages the dependencies and scripts into an image. `docker run --rm` starts a temporary container and removes it when the command exits. These checks need no API key. Rebuild after changing files; the image contains a snapshot of the checkout.

## Worktree cleanup

Archive completed Codex-managed worktrees through Codex's archive action. It saves a recoverable Git snapshot before removing the checkout. Export any needed ignored files, such as `runs/`, first; they are not included in that snapshot.

Docker check containers remove themselves with `--rm`. We have no background services or per-worktree Docker resources yet; add a scoped teardown command when those are introduced.

## Structured evidence compatibility

Simulator `0.3.2` records optional public evidence codes used by closure validation and the rules captain. Replay still accepts schema-1 logs without codes; rerun retains the existing exact simulator-version requirement. Verify wording-independent closure and rejection of deceptive report claims with `uv run --frozen pytest tests/test_mission.py tests/test_providers.py`.

Full audit verification: `uv run --frozen pytest tests/test_mission.py tests/test_adversary.py tests/test_persistence.py tests/test_cli.py`. Private world-transition records must stay out of crew provider contexts.

In `jev+llm`, allow up to two Jev calls per turn (world/observation batch and action-result batch) in addition to captain/adversary calls. All roles share `--max-calls`; exhaustion conservatively routes events to captain review and remains logged. Jev question/rubric and captain instruction versions are v2 for public-event processing. Use `uv run --frozen pytest tests/test_mission.py tests/test_providers.py` to verify classification, safety fallback, projections, and shared budget without paid calls.

## Interactive architecture explorer

Keep `architecture/` current in every change to components, service boundaries, message
contracts, state fields, incident routing, controller modes, or event recording.
Update `architecture/src/model.json`, regenerate the source snapshots and scripted run
fixtures with `uv run --frozen python architecture/scripts/generate.py`, and verify the
affected canvas views, component Code/State tabs, and run walkthrough. Source hyperlinks
are pinned to the generator's Git revision; regenerate after committing runtime changes
so those links address the matching source. The bundled code is available offline.

Use the repository-installed PR Lens guidance at `.agents/skills/pr-lens/SKILL.md` for
source-backed walkthroughs; this explorer uses React Flow and runs locally.
See `architecture/README.md` for setup, generation, browser testing, and standalone builds.
Verify with the generator's `--check`, then `npm run check`, `npm run build`, and `npm test`
inside `architecture/`. Use `npm run format:check` for hand-maintained frontend files.
Demo providers are scripted and make zero model calls; label this distinction in the UI.
Preserve the README infographic's picture style when updating architecture documentation.
Stop the optional Vite development server with Ctrl-C when finished.

The architecture UI uses Vercel AI Elements for source/JSON viewing, shadcn/ui
(Radix) for inspector/replay controls, and Tailwind via the Vite plugin. Component
sources live under `architecture/src/components`; registry configuration is in
`architecture/components.json`. Preserve the offline Python/JSON-only Shiki setup,
source-file line offsets, keyboard tabs, and clipboard error feedback when updating
components. Run the browser suite after changes to these interactions.
