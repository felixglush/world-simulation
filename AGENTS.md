# Working on Station Control

Read docs/station-control.md for the product contract and README.md for setup.

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
