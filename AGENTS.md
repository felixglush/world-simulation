# Working on Station Control

Read docs/station-control.md for the product contract and README.md for setup.

Python is the user's primary language. For any other language, explain unfamiliar constructs from a pedagogical angle.

Before implementing complex functionality, enumerate and write end-to-end tests for the success path and every meaningful failure mode. Assert observable outcomes, then implement until they pass.

Use fake providers for routine tests. Live model experiments require explicit budgets and recorded model/configuration metadata.

## Environment commands

- Install: bash scripts/setup-cloud.sh
- Verify dependencies offline: uv run --frozen python scripts/check_environment.py
- Lint: uv run --frozen ruff check .
- Format check: uv run --frozen ruff format --check .
- Behavioral tests, once implemented: uv run --frozen pytest

Pause for user review before commits, pushes, cloud publication, or paid experiments unless separately authorized.
