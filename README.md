# Station Control

A Python simulation for testing an AI crew's decisions under equipment failures and misleading reports. See [the project specification](docs/station-control.md).

## Development setup

Use Python 3.12 and the locked dependencies:

```bash
bash scripts/setup-cloud.sh
```

Use this same command as the Codex Cloud install command. It installs uv 0.12.21 when needed, syncs dependencies, and runs offline SDK, credential-handling tests, lint, and formatting checks. The simulator, mission, adapter, and CLI checks run offline with fake model transports.

The CLI defaults `run` and `rerun` to the AI captain (`llm`). A live mission requires `OPENROUTER_API_KEY`, `CAPTAIN_MODEL`, `--max-calls`, and `--max-output-tokens-per-call`; if any are missing, it stops before creating a run log and never switches to rules. The `jev+llm` controller also requires `JEV_MODEL`. Supply credentials through the environment and allow HTTPS access to `openrouter.ai`. Both Jev and the captain use OpenRouter. `.env.example` documents the planned model configuration; it is not automatically loaded. Select a captain model with tool calling for live missions.

The checker reads `OPENROUTER_API_KEY` from the process environment and never prints it. To require that the cloud value is present:

```bash
uv run --frozen python scripts/check_environment.py --require-api-key
```

This checks injection and SDK construction, not whether the API accepts the credential. Without the flag, setup can run without a key. No model requests are sent.

The project's TDD skill and supporting guides are stored in `.agents/skills/tdd` for local and cloud tasks. Personal skills on your Mac are not synced to cloud environments. See [Codex Cloud skill availability](https://learn.chatgpt.com/docs/environments/cloud-environments#current-limitations).

## Run, replay, and compare missions

Run a short AI-led mission with the default `llm` controller. The CLI saves a versioned JSONL run under `runs/` unless `--output` names another path:

```bash
export OPENROUTER_API_KEY=...
export CAPTAIN_MODEL=...
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario leak --seed 41 --turns 48 \
  --max-calls 40 --max-output-tokens-per-call 256
```

For offline runs and rules benchmarks, select rules explicitly with `--controller rules`. These runs need no key or model budget and make no model requests.

The original scenarios are `normal`, `leak`, `faulty_sensor`, and `misleading_report`. The [YAML scenario library](docs/scenarios.md) adds harder and deceptive missions; list them with `python -m station_control scenarios`, select an ID with `--scenario`, or load a custom file with `--scenario-file`. Repeat either selector to combine YAML scenarios in one shared station; `--scenario-spacing 8` staggers successive event schedules by eight turns. Each log stores the scenario, seed, simulator and controller configuration, instruction and rubric versions, model identifiers, per-turn observed evidence and decisions, consequences, debug state, and evaluation metrics.

Replay validates a completed log and displays each turn's evidence, decision, consequence, and separate world-state debug view:

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control replay runs/RUN_ID.jsonl
```

Logs with the current JSONL schema remain replayable across simulator versions. Rerunning requires the same simulator version because scenario behavior may have changed.

Rerun the saved scenario and seed with a changed setting into a new file. A rerun defaults to the AI controller, including when the saved run used rules; provide the live configuration and budgets above. To keep a rerun offline, pass `--controller rules` explicitly. Existing output files are never replaced.

```bash
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control rerun runs/RUN_ID.jsonl \
  --evidence-access history --turns 48 --max-calls 40 \
  --max-output-tokens-per-call 256 --output runs/RUN_ID-history.jsonl
```

For a model-backed run, configure the key and selected model identifiers in the environment and pass explicit finite call and output-token budgets. The CLI records model identifiers and budgets without recording the API key. Both adapters share the call limit; the token limit applies to the captain because Jev exposes no documented output-token limit. This is not a dollar cap. Live endpoint interoperability was not exercised during offline verification:

```bash
# Configure OPENROUTER_API_KEY, CAPTAIN_MODEL, and JEV_MODEL in the environment first.
UV_CACHE_DIR=/tmp/station-uv-cache uv run --frozen python -m station_control run \
  --scenario leak --seed 41 --turns 48 --controller jev+llm \
  --max-calls 40 --max-output-tokens-per-call 256 --output runs/jev-leak-41.jsonl
```

See [the reproducible offline experiment](docs/mvp-experiment.md) and [service boundaries](docs/architecture.md).

For a controlled comparison, keep the scenario and seed fixed across controller configurations so the external events match. Use one seed set for tuning, then report results on a separate held-out set; keep every run in its own output file. For example, use seeds `1, 2, 3` while adjusting settings and seeds `101, 102, 103` for the held-out comparison. Add the explicit budgets above to every live-controller invocation. No benchmark service or shared mutable run state is needed.

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
