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

## Model adapters slice

The first 16 adapter cases failed against placeholder provider behavior. The final
20-case suite uses fake HTTP transports with the installed OpenAI and TypeSafe SDKs.
It checks separate Jev judgments, uncertain/negative Noul outcomes, malformed responses,
unknown/invalid actions, failure sanitization, explicit shared call limits, usage reporting,
and request-attempt metadata on errors.

Two real-runner/real-adapter cases exposed and fixed the handling of valid no-argument
JSON Schema tools (an omitted `required` is empty). Both latest/history cases now complete
inspection → timed repair → evidence-backed closure, and measured model calls match actual
transport requests. Scoped tests, lint, formatting, and offline SDK checks pass.

The captain has an output-token cap; Jev's SDK exposes no documented output-token cap.
Both share a request cap and disable retries. A request cap is not a dollar ceiling.
The configured OpenRouter endpoints were not exercised live.

## CLI and complete MVP verification

The CLI matrix first reached its intended missing-runner RED; independent error-path
cases passed before the runner was complete. The final 18 CLI cases pass through real
subprocess entry points, including offline run/replay/rerun, existing output protection,
malformed/incomplete/version-incompatible logs, ordered events, live configuration gates,
and provider-construction cleanup.

Final aggregate verification: **93 tests pass**, repository-wide Ruff lint and formatting
pass (23 Python files), `git diff --check` passes, and the offline SDK checker passes.
Eight actual CLI runs reproduce the development/held-out inspection ablation in
`docs/mvp-experiment.md`; replay and rerun were also exercised from saved files. Logs are
ignored under `runs/mvp-validation-*.jsonl`. No live model requests were sent.

Docker packaging now includes `station_control/`; a container build/run was not performed.
Git transport was unavailable in this environment, so GitHub trees/commits/stacked PRs
were published through the authenticated connector to the verified checkout origin.
Local and remote commit IDs differ; file trees are the publication parity check.
