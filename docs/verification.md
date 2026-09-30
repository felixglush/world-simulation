# Verification

Run the locked checks from the repository root:

```bash
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen python scripts/check_environment.py
```

If the inherited uv cache is read-only, set `UV_CACHE_DIR=/tmp/station-uv-cache`.

The suite covers deterministic world transitions, physical command rejection, terminal
crew loss, observed-only controller contexts, incident follow-up and evidence-backed
closure, independent evaluation, real SDK adapters through fake HTTP transports, and
CLI subprocess run/replay/rerun. Tests preserve the data-access and resource boundaries
while permitting internal refactoring.

[The offline experiment](mvp-experiment.md) reproduces a failed mission and a configuration
improvement on separate seeds. It is a rules ablation, not evidence of improved live-model
performance. Logs contain configuration, evidence, decisions, consequences, independent
measures, and separately labeled debug snapshots.

Routine verification sends no model requests. Live OpenRouter endpoint compatibility and
Docker execution are not established by these checks. A request cap is not a dollar cap;
the captain supports an output-token cap, while Jev exposes no documented equivalent.
Critical-report misses, unsupported diagnoses, and unnecessary escalation remain explicitly
unavailable until scenario answer-key annotations are implemented. Model usage/cost stays
unknown when the provider does not report it.
