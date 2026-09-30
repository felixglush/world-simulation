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

## Independent evaluation slice

A three-case matrix first failed at the importable `evaluate_mission` stub. The final
five-case matrix verifies resource/action/incident measures, repair evidence deduplication,
missing state history, and model accounting with explicitly unknown cost or usage.
All five cases pass, alongside the world/setup tests; scoped lint and format checks pass.

The evaluator consumes authoritative snapshots and recorded actions rather than controller
self-assessment. Critical-report misses, unsupported diagnoses, and unnecessary escalation
remain explicitly unavailable until scenario answer-key annotations are added. Model costs
and token totals are unavailable when any attempted request lacks those measurements.

## Mission orchestration slice

The first ten mission cases failed at the runner stub, then passed with the real world
and controller ports. The expanded 18-case matrix covers full 14-day rules missions,
observed-only contexts, latest/history access, uncertain dispatch routing, inspection
budgets, deferred clarification, monitored low-priority incidents, repair follow-up,
and evidence-backed closure. Regression cases reject closing on active-leak evidence,
irrelevant healthy-sensor evidence, stale evidence, or missing reasons.

Integration review added two further RED/GREEN fixes: malformed unhashable action targets
now return a domain rejection, and model attempts recorded in proposal and action events
are counted once. The mission/world/evaluation/setup checks pass with lint and formatting.
