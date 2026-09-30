# Adaptive adversary

An optional AI adversary observes the current station and selects an operation from a
bounded action space. It can run alongside one YAML scenario or a composition of several.
The AI chooses the action; Python decides whether it is valid and owns every state change.

The adversary receives current authoritative resource, fault, sensor, repair, and delivery
facts. It cannot see future scenario events. The captain and Jev continue to receive only
their public observations and evidence. A deceptive report can influence a decision, but
cannot manufacture a completed repair or change the authoritative inspection result.

## Run with an AI adversary

Configure `OPENROUTER_API_KEY`, `CAPTAIN_MODEL`, and `ADVERSARY_MODEL` in the environment.
Select models that support tool calling, then run:

```bash
uv run --frozen python -m station_control run \
  --scenario false_authority --scenario partial_delivery \
  --adversary llm --adversary-budget 3 --turns 48 \
  --max-calls 120 --max-output-tokens-per-call 512 \
  --output runs/adversary-mission.jsonl
```

The captain still defaults to AI. Use `--controller rules --adversary llm` to test an AI
opponent against the offline captain baseline; that combination needs `ADVERSARY_MODEL`
but not `CAPTAIN_MODEL`. `ADVERSARY_BASE_URL` optionally overrides the OpenRouter endpoint.
The adversary is off by default for new runs.

| Action | Effect and eligibility |
| --- | --- |
| `wait` | Leave the world unchanged; costs no disruption points. |
| `start_silent_leak` | Start a leak without a direct alert, when no leak or repair is active. |
| `mask_sensor` | Freeze one sensor at its current reading when sensors are independent and fault-free. |
| `delay_pending_delivery` | Move the earliest pending delivery one turn later. |
| `deceptive_report` | During an active leak, send a fixed misleading maintenance claim as public evidence. |

The disruption budget defaults to three and accepts values from zero to six.
Selecting `llm` still requires valid model configuration and explicit call limits with
zero points, although no adversary requests are made. Use `--adversary off` to disable
the provider entirely.
Each accepted non-wait action costs one disruption point. The agent chooses when to act
and, for masking, which sensor to target. It cannot choose arbitrary mutation amounts or
execute code. The initial catalog uses a fixed deceptive message; generated dialogue is
not part of this slice.

## Timing and limits

Before each world advance, the adversary gets a fresh state projection and its eligible
actions. It can propose at most one action. The world validates the proposal against the
current state and remaining disruption budget. Accepted leak, sensor, and report actions
join the upcoming turn's event schedule; a delay moves an existing delivery's due turn.
Ordinary time, authored scenario events, repairs, and deliveries then proceed, and the
captain can respond. The audit uses the observation turn, starting at turn zero; event
effects appear on the following world advance.

The disruption budget limits accepted sabotage actions. The model-call budget separately
limits requests and is shared with the captain and Jev. Provision enough calls for the
crew to respond; the adversary can consume part of that shared allowance. An invalid
proposal or provider failure does not apply sabotage or trigger a rules-based substitute.
No further adversary calls are made once its disruption points run out or the provider
reports that the shared call budget is exhausted.

Private adversary context, rationale, acceptance or rejection, and budget changes are
recorded separately from public evidence. These audit records are available in the JSONL
log and replay, but are not supplied to the captain.

## Comparing runs

Replay displays the actions that actually happened without calling a model. Rerun starts
a new experiment: the same seed reproduces the authored scenario schedule, but model
decisions can change. Rerun inherits the saved adversary mode and disruption budget; use
`--adversary off` to disable it or `--adversary-budget` to change its allowance. Current
environment model settings and explicit call budgets apply to the new experiment.
Use the adversary-disabled mode when comparing controllers against
an identical external schedule. Adaptive adversary runs test resilience against a reacting
opponent and do not guarantee the mission is winnable.

## Service boundaries

- The adversary policy defines its state projection, eligible action space, and validated
  effects without SDK, filesystem, or network dependencies.
- The mission application owns turn ordering, budget consumption, audit records, and the
  separation between private context and crew evidence.
- The model adapter turns the supplied catalog into tool choices and validates responses.
  It implements an application-owned port; SDK objects stay inside the adapter.
- The CLI wires models and budgets. Persistence stores the existing versioned run format;
  there is no separate adversary service, scheduler, or state store.
