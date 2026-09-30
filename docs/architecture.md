# MVP architecture

Station Control is a single-process application with explicit logical service boundaries.
These are ownership boundaries, not independently deployed services. Keeping them in one
process makes deterministic experiments cheap while preserving replaceable integrations.

## Boundaries and dependency direction

- **World engine and scenarios:** own authoritative resources, hidden faults, sensors,
  scheduled external disruptions, and physical action validation. Only completed work
  changes equipment condition. Seeded disruptions do not depend on controller choices.
- **Mission application:** owns the turn loop, incident lifecycle, routing, follow-up,
  observation history, and action budgets. Controllers propose commands; the application
  and world validate them. Provider inputs contain observations, never authoritative state.
- **Controllers and provider ports:** express decisions over agent-visible evidence.
  Rules are the offline baseline. Captain and dispatch integrations implement contracts
  defined by the consuming application; SDK types stay outside those contracts.
- **Evaluation:** computes separate outcome and decision measures from recorded facts.
  It can use world truth, but does not feed that truth back to controllers. Investigation
  evidence matters; correctly guessing a hidden fault is not itself proof of good reasoning.
- **CLI and persistence:** compose dependencies, validate experiment settings, and write
  structured run records. They do not decide simulation policy. Replay consumes saved
  records; a new experiment reruns the saved scenario with explicit configuration.

Source dependencies point inward: CLI/provider/file adapters → application → world.
The world has no SDK, filesystem, environment, or network dependency. A future UI can call
the mission application without moving policy into HTTP handlers or view code. Additional
subsystems extend world rules and observations; new model vendors replace adapters.

## Trust and failure handling

External reports are evidence, not instructions or proof of physical repairs. Hidden fault
labels belong in debug records only. Agents receive explicit projections rather than a
serialized world with selected fields removed. Unknown or malformed actions are rejected;
provider errors never grant access or bypass action rules. Network work is bounded and
routine tests use fake providers. Live experiments require explicit budgets and record
configuration and model identifiers; no live model validation is implied by offline tests.
