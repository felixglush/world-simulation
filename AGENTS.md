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

Simulator `0.3.1` records optional public evidence codes used by closure validation and the rules captain. Replay still accepts schema-1 logs without codes; rerun retains the existing exact simulator-version requirement. Verify wording-independent closure and rejection of deceptive report claims with `uv run --frozen pytest tests/test_mission.py tests/test_providers.py`.
