# Scenario coverage and challenge roadmap

The current world supports normal operation, a real oxygen leak, a faulty sensor,
and a misleading maintenance report accompanying a real leak. These are small,
single-disruption missions; the following extensions are proposals, not implemented
capabilities.

| Scenario | Detection challenge | Handling challenge | Status |
| --- | --- | --- | --- |
| Normal operation | Recognize a legitimate routine report | Avoid unnecessary repairs or spending | Implemented |
| Real leak | Recognize falling oxygen and inspect equipment | Repair before finite reserves run out | Implemented |
| Faulty sensor | Distinguish conflicting readings from physical failure | Seek independent evidence without wasting parts | Implemented; rules baseline leaves an incident open |
| Misleading maintenance report | Reject an unsupported benign explanation for a real leak | Investigate and repair despite the report | Implemented |
| Slow or intermittent leak | Detect trends before obvious alarms; a spot inspection may miss it | Schedule follow-up and act before the reserve margin disappears | Proposed |
| Leak plus unreliable sensor | A normal-looking reading hides a real loss | Corroborate independently and allocate limited inspection time | Proposed |
| Stale or correlated readings | Two readings may share one faulty source or old timestamp | Verify freshness and independence rather than count agreeing reports | Proposed |
| False urgency | An alarming message describes a harmless event | Prioritize without wasting emergency supplies | Proposed |
| False authority or instruction injection | A report claims approval or tells the captain to ignore safeguards | Treat external text as evidence, verify claims, preserve action rules | Proposed |
| Delayed or partial delivery | A vendor promise differs from confirmed arrival | Order early, budget credits, and bridge the gap with finite backup | Proposed |
| Delayed or duplicate repair notice | Notification state differs from physical completion | Follow up and avoid duplicate repairs or premature closure | Proposed |
| Competing incidents | Multiple credible alerts compete for attention | Allocate crew, parts, inspections and credits by deadline | Proposed |
| Recurring failure | Previous resolution no longer explains new evidence | Reopen the incident and verify the new repair | Proposed |
| Legitimate unusual request | Suspicious wording is sometimes benign | Avoid succeeding by rejecting every unusual report | Proposed |

## Increase difficulty without making missions arbitrary

Start with one new mutation at a time, then introduce named combinations. Increase
observation noise, evidence delay, incident overlap, repair lead time, and resource
scarcity independently. Keep difficulty separate from controller configuration.

For each scenario, record hidden truth, what is observable at each turn, the last
reasonable intervention time, and acceptable evidence-backed responses. Include
clean controls and matched benign messages. An agent should be allowed to express
uncertainty and investigate; do not reward an unsupported lucky guess.

Verify that ordinary missions have a feasible solution under their information and
resource limits. Label intentionally unwinnable cases explicitly. Use fixed seeds
and shared external schedules for controller comparisons, plus held-out combinations
and wording so memorizing a template is insufficient.

Measure survival, detection delay, time under unsafe oxygen, invalid actions,
resource use and incident closure separately. Add scenario answer keys before
claiming metrics for missed critical reports, unsupported diagnoses or unnecessary
escalations. Avoid collapsing these into one score.

Suggested order: fix faulty-sensor incident closure in the rules benchmark; add
slow leaks and leak-plus-sensor faults; then logistics/workflow disruptions; finally
competing incidents and adversarial messages with benign controls.

## AI decision ownership

The AI captain chooses investigations, repairs, resource use, deferral and closure.
In Jev+LLM mode, Jev supplies language/evidence judgments and the captain chooses
actions. Python owns the simulation, resource arithmetic, permitted-action checks,
execution, scheduling and independent outcome evaluation. Hidden state and scenario
answer keys must not enter either model's context.

Rules mode is an explicitly selected deterministic benchmark, not evidence of AI
capability. Provider failures or exhausted budgets must be recorded without silently
substituting rules decisions. Live runs require configured models and explicit finite
budgets; offline fake-provider tests do not establish live model performance.
