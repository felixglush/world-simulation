# Station Control

A Python simulation for testing an AI crew's decisions under equipment failures and misleading reports. See [the project specification](docs/station-control.md).

## Development setup

Use Python 3.12 and the locked dependencies:

```bash
bash scripts/setup-cloud.sh
```

Use this same command as the Codex Cloud install command. It installs uv 0.12.21 when needed, syncs dependencies, and runs offline SDK, credential-handling tests, lint, and formatting checks. The simulator and mission tests are still to be built.

For live experiments, supply `OPENROUTER_API_KEY` through the environment and allow HTTPS access to `openrouter.ai`. Both Jev and the captain use OpenRouter. `.env.example` documents the planned model configuration; it is not automatically loaded. Select a captain model with tool calling when implementing its adapter.

The checker reads `OPENROUTER_API_KEY` from the process environment and never prints it. To require that the cloud value is present:

```bash
uv run --frozen python scripts/check_environment.py --require-api-key
```

This checks injection and SDK construction, not whether the API accepts the credential. Without the flag, setup can run without a key. No model requests are sent.

The project's TDD skill and supporting guides are stored in `.agents/skills/tdd` for local and cloud tasks. Personal skills on your Mac are not synced to cloud environments. See [Codex Cloud skill availability](https://learn.chatgpt.com/docs/environments/cloud-environments#current-limitations).

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
