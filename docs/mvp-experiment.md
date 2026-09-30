# Reproduce the first offline experiment

The MVP can expose a failure, replay the evidence/action/consequence chain, change a
configuration, and check that change on separate seeds. This is a deterministic rules
ablation; it does not establish improved performance of a live language model.

Use misleading-maintenance-report seeds 1 and 2 for development. Disable inspections
in a 48-turn mission, replay the failure, then rerun with one inspection slot per turn:

```bash
uv run --frozen python -m station_control run --scenario misleading_report --seed 1 --turns 48 --inspection-budget 0 --output runs/dev-1-no-inspections.jsonl
uv run --frozen python -m station_control replay runs/dev-1-no-inspections.jsonl
uv run --frozen python -m station_control rerun runs/dev-1-no-inspections.jsonl --inspection-budget 1 --output runs/dev-1-inspections.jsonl
```

Check the same fixed change on held-out seeds 101 and 202 (do not tune against them):

```bash
for seed in 101 202; do
  uv run --frozen python -m station_control run --scenario misleading_report --seed "$seed" --turns 48 --inspection-budget 0 --output "runs/heldout-$seed-no-inspections.jsonl"
  uv run --frozen python -m station_control rerun "runs/heldout-$seed-no-inspections.jsonl" --inspection-budget 1 --output "runs/heldout-$seed-inspections.jsonl"
done
```

Observed during implementation through the public mission API and verified again with actual CLI subprocess runs:

| Seeds | Inspection slots/turn | Crew survives | Repairs completed | Unresolved incidents |
| --- | ---: | --- | ---: | ---: |
| Development 1, 2 | 0 | No (turn 33 / 32) | 0 | 4 / 3 |
| Development 1, 2 | 1 | Yes, all 48 turns | 1 | 0 |
| Held-out 101, 202 | 0 | No (turn 33 / 32) | 0 | 4 / 3 |
| Held-out 101, 202 | 1 | Yes, all 48 turns | 1 | 0 |

The failure has a concrete explanation: without an inspection slot the rules controller
cannot obtain evidence for a repair, the leak continues, and finite backup oxygen only
delays crew loss. Enabling inspection permits an evidence-backed repair. The seed fixes
external disruptions; action-generated records and outcomes are expected to differ.

Each file contains versioned configuration, public evidence, decisions, consequences,
independent measures, and separately labeled debug snapshots. Output files are exclusive;
choose a fresh path to repeat the commands. Live model modes require a key, explicit
models and call/token budgets. No live model calls were made during MVP verification.

Three judgment metrics remain explicitly unavailable: critical reports missed, unsupported
diagnoses accepted, and unnecessary escalations. Those need scenario answer-key annotations.
Reported model costs/usage are also marked unavailable when the provider omits them.
