# MVP verification record

Routine verification uses deterministic scenarios and fake external providers. No live
model experiment is part of this implementation's acceptance evidence.

## World slice

The initial 18-case behavioral matrix was run against importable public API stubs.
All cases failed at the unimplemented world entry point, rather than during collection.
The matrix covers scenario replay and observation projection, time-delayed repair,
finite backup oxygen, crew occupancy, supply lead time, misleading reports, sensor
inspection, and rejected commands leaving state unchanged.

Command: `uv run --frozen pytest tests/test_world.py -q`.

In the managed environment, set `UV_CACHE_DIR=/tmp/station-uv-cache` because the
inherited home cache is read-only. This changes cache placement, not dependencies.

Final world verification: 21 world cases and the six existing environment checks pass.
A second RED/GREEN cycle fixed terminal crew loss: dead crew cannot act or finish work,
and the terminal world no longer advances. Crew exhaustion, duplicate repair assignment,
and insufficient-credit rejection are also covered. Ruff lint and formatting checks pass.
