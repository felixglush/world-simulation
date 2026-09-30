# Station Control

Source: https://chatgpt.com/space/page_fc5b19900a188191a26a38b452913a17

## 1. Objective

Keep a small space station operational for a 14-day mission while handling incomplete information, equipment failures, and misleading reports.

Success has several dimensions:

- Keep the crew alive.

- Maintain essential services.

- Resolve incidents before they become emergencies.

- Avoid unnecessary repairs and emergency spending.

- Complete investigations without leaving cases forgotten.

Survival is the primary objective. Efficiency measures distinguish a good mission from one that barely survives.

## 2. Personas and responsibilities

The characters provide personality, but each corresponds to a clear system responsibility. Only the captain initially needs to be a generative agent.

| <p>Persona</p> | <p>Responsibility</p> | <p>Implementation</p> |
| - | - | - |
| **Station commander — you** | Select missions, configure the AI crew, watch runs, and review failures. | Player controls and review interface |
| **Dispatch officer — Jev** | Classify reports, recognize requests and uncertainty, and route incidents. | Narrow classification questions |
| **Captain — LLM** | Investigate incidents, weigh options, and propose actions. | Generative model with a bounded tool set |
| **Crew and vendors** | Send maintenance reports, requests, explanations, and offers. | Scripted characters initially; optional generated dialogue later |
| **Saboteur — you or the scenario engine** | Introduce disruptions and misleading information. | Controlled event mutations |
| **World engine — Python** | Maintain the true station state, apply consequences, and enforce action rules. | Deterministic simulation |
| **Evaluator — Python** | Compare decisions and outcomes with scenario expectations. | Checks and run metrics independent of the agents |

The world engine owns reality. A character saying “the leak is fixed” does not repair the leak; a completed repair action does.

## 3. What each participant can see

There are three distinct views:

| <p>View</p> | <p>Contains</p> | <p>Who sees it</p> |
| - | - | - |
| **Actual station state** | Real oxygen levels, hidden faults, sensor reliability, scheduled disruptions | World engine and evaluator |
| **Observed station state** | Sensor readings, reports, inspection results, accessible records | Jev and the captain |
| **Debug view** | Actual state alongside observations, decisions, and consequences | You, during debugging or after a mission |

Hidden faults and sabotage labels never enter the agents’ context.

If the evidence is insufficient to identify a fault, the expected behavior can be to investigate. The evaluator should not reward lucky guesses about hidden truth.

## 4. The operating loop

Each simulation turn represents a fixed interval of station time.

1. The world advances: oxygen is consumed, repairs progress, and deliveries approach.

2. Scheduled events produce reports or alerts.

3. Jev evaluates the incoming reports.

4. Python routes them according to the selected configuration.

5. The captain inspects the available evidence and proposes actions.

6. Python validates and executes permitted actions.

7. The system records decisions, resource changes, and unresolved incidents.

Actions can take several turns. Requesting a repair does not make it complete immediately.

For the initial game, assume crew members perform assigned work correctly unless an explicit scenario introduces a failure.

## 5. Jev’s role

Jev handles small judgments over the evidence it receives.

| <p>Judgment</p> | <p>Output type</p> | <p>How the system uses it</p> |
| - | - | - |
| Which subsystem does this report concern? | **Choice** | Route to life support, power, logistics, or unknown |
| Does this message request disabling a safeguard? | **Noul** | Require captain review |
| Does the supplied evidence support the claimed diagnosis? | **Noul** | Decide whether investigation is needed |
| How urgently should this report be investigated under the rubric? | **Score** | Prioritize the incident queue |

Use separate questions for separate judgments. A report can simultaneously concern life support, request a safeguard change, and lack supporting evidence.

Python calculates exact quantities such as remaining oxygen and repair cost. Jev interprets language and evidence.

## 6. Toggles and configuration

Separate experiment settings from gameplay difficulty. Otherwise, it becomes hard to understand why one run performed differently.

### Agent configuration

| <p>Control</p> | <p>Initial options</p> | <p>What it tests</p> |
| - | - | - |
| **Controller** | Rules only / LLM only / Jev + LLM | Whether Jev improves the overall system |
| **Dispatch criteria** | Versioned question and rubric sets | Whether clearer judgments improve routing |
| **Escalation thresholds** | Configurable cutoffs | Missed incidents versus unnecessary reviews |
| **Evidence access** | Latest report / report plus history | Whether additional context improves decisions |
| **Investigation budget** | Number of inspection actions available per turn | Information gathering versus delay |
| **Captain instructions** | Versioned instruction sets | Planning and decision quality |

For Noul judgments, define handling for positive, negative, and uncertain results. A low probability of “yes” is not automatically low confidence.

### Mission settings

| <p>Control</p> | <p>Initial options</p> |
| - | - |
| **Scenario seed** | Fixed, replayable seed |
| **Mission duration** | Short debugging run or full 14-day mission |
| **Starting reserves** | Comfortable or constrained |
| **Disruption frequency** | None, occasional, frequent |
| **Sabotage families** | Select which event types may occur |

### Play modes

- **Watch mode:** the AI runs a configured mission.

- **Saboteur mode:** you inject events during the mission.

- **Benchmark mode:** controllers face the same predefined external events without player intervention.

- **Replay mode:** inspect a past mission, then rerun it under a different configuration.

Changing a setting mid-mission makes the run an exploratory session rather than a clean benchmark.

## 7. The captain’s action space

The first release uses a small set of structured actions.

| <p>Action</p> | <p>Purpose</p> | <p>Cost or constraint</p> |
| - | - | - |
| **Read history** | Inspect earlier readings, maintenance records, and incident notes | Limited to accessible records |
| **Request clarification** | Ask a crew member for missing information | Response may arrive later |
| **Inspect equipment** | Obtain a new observation about a sensor or system | Uses crew time and an inspection slot |
| **Assign repair** | Repair a specified component | Requires crew availability, parts, and time |
| **Activate backup oxygen** | Increase available supply | Backup reserves are finite |
| **Order emergency supplies** | Arrange an oxygen or parts delivery | Costs credits and has a lead time |
| **Defer and monitor** | Wait for more evidence or a scheduled event | Requires a follow-up time |
| **Close an incident** | Record that no further work is needed | Requires a resolution reason and supporting evidence |

The captain cannot directly edit oxygen levels, invent inventory, or declare a repair complete.

Python rejects invalid actions and returns a specific reason. For example: insufficient parts, crew already assigned, or backup supply exhausted.

Requests to disable safeguards can appear in reports, but alarm suppression is outside the initial action space. This lets us test how the crew handles such requests without adding another operational mechanism.

## 8. Sabotage

“Sabotage” includes accidents, misleading messages, and workflow failures. Each mutation has a defined effect and an answer key.

| <p>Family</p> | <p>Example</p> | <p>Capability tested</p> |
| - | - | - |
| **Physical failure** | An oxygen leak begins | Detection, investigation, and timely repair |
| **Sensor failure** | A sensor becomes stuck on a normal reading | Corroboration and use of independent observations |
| **Misleading diagnosis** | A report dismisses a real warning as a sensor fault | Separating a claim from supporting evidence |
| **False urgency** | A routine request is written in alarming language | Avoiding unnecessary emergency actions |
| **False authority** | A message claims the commander approved bypassing checks | Verifying authority rather than trusting wording |
| **Instruction injection** | A vendor message tells the captain to ignore station rules | Preserving the boundary between external content and instructions |
| **Logistics disruption** | Emergency oxygen arrives late | Planning around delivery uncertainty |
| **Workflow disruption** | A repair notification is delayed or duplicated | Follow-up, recovery, and duplicate handling |

Not every problem has a unique correct plan. Evaluation should distinguish mandatory constraints from situations where several strategies are reasonable.

### Sabotage rules

- Events must be valid under the world’s rules.

- The same seed and event configuration reproduce the same external disruptions.

- Mutations are recorded separately from agent-visible evidence.

- Begin with one mutation at a time; introduce combinations later.

- Include clean missions and legitimate unusual requests so the agent cannot succeed by treating everything as suspicious.

- Mark deliberately unwinnable scenarios separately from ordinary performance tests.

## 10. Evaluation and failure replay

### Decision-level measures

- Critical reports missed.

- Legitimate reports unnecessarily escalated.

- Unsupported diagnoses accepted.

- Appropriate requests for more information.

- Invalid actions attempted.

- Incidents left without follow-up.

### Mission-level measures

- Crew survival.

- Time spent below safe resource limits.

- Incident resolution time.

- Resource and credit consumption.

- Model calls, latency, and cost.

Keep these measures visible separately rather than hiding everything behind one score.

A failure replay should connect:

**What happened → what the crew could observe → what Jev judged → what the captain did → what consequence followed.**

Save the scenario seed, simulator version, model versions, question sets, instructions, and configuration with each run. World events should be reproducible even when model decisions vary.

## 11. First playable version

Build only:

- One station with six crew members.

- One subsystem: life support.

- Oxygen reserves, backup oxygen, spare parts, credits, and crew availability.

- Two sensors and one repairable oxygen system.

- Four scenario families: normal operation, real leak, faulty sensor, and misleading maintenance report.

- The three controller configurations.

- A text-based event log and failure replay.

The first milestone is:

**Run a short mission, identify why the AI crew made a bad decision, change its configuration, and demonstrate improvement on a separate set of missions.**

The game supplies the motivation; the experiments tell you whether your improvements actually work.
