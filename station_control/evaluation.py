"""Pure mission-level metrics computed from world snapshots and event records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

from .domain import CRITICAL_OXYGEN, StationState

UNAVAILABLE_METRICS = (
    "critical_reports_missed",
    "unsupported_diagnoses_accepted",
    "unnecessary_escalations",
)


@dataclass(frozen=True, slots=True)
class MissionEvaluation:
    metrics: Mapping[str, int | float | bool]
    unavailable: tuple[str, ...] = ()


def evaluate_mission(
    states: Sequence[StationState], events: Sequence[Mapping[str, object]]
) -> MissionEvaluation:
    """Calculate only outcomes supported by recorded world states and events."""
    metrics: dict[str, int | float | bool] = {}
    unavailable = list(UNAVAILABLE_METRICS)
    final_turn = (
        states[-1].turn
        if states
        else max(
            (turn for event in events if type(turn := event.get("turn")) is int),
            default=0,
        )
    )

    if states:
        initial = states[0]
        final = states[-1]
        metrics.update(
            {
                "crew_survived": final.crew_alive,
                "turns_completed": final.turn,
                "turns_below_critical_oxygen": sum(
                    state.oxygen <= CRITICAL_OXYGEN for state in states[1:]
                ),
                "oxygen_consumed": sum(state.consumption_rate for state in states[1:]),
                "backup_oxygen_used": max(0, initial.backup_oxygen - final.backup_oxygen),
                "credits_spent": max(0, initial.credits - final.credits),
            }
        )
    else:
        unavailable.extend(
            (
                "crew_survived",
                "turns_completed",
                "turns_below_critical_oxygen",
                "oxygen_consumed",
                "backup_oxygen_used",
                "credits_spent",
            )
        )

    inspections = 0
    parts_consumed = 0
    clarification_requests = 0
    invalid_actions = 0
    adversary_disruptions = 0
    adversary_rejected_actions = 0
    open_incidents: dict[int, int | None] = {}
    scheduled_followups: dict[int, int] = {}
    resolution_turns: list[int] = []
    repair_sequences: set[int] = set()
    provider_records: list[tuple[int, Mapping[str, object]]] = []

    for event in events:
        event_type = event.get("event_type")
        decision = event.get("decision")
        decision = decision if isinstance(decision, Mapping) else {}
        consequence = event.get("consequence")
        consequence = consequence if isinstance(consequence, Mapping) else {}

        if event_type == "action":
            accepted = consequence.get("accepted")
            if accepted is False:
                invalid_actions += 1
            if decision.get("kind") == "inspect" and accepted is True:
                inspections += 1
            if decision.get("kind") == "assign_repair" and accepted is True:
                parts_consumed += 1
            if decision.get("kind") == "request_clarification" and accepted is True:
                clarification_requests += 1
        elif event_type == "adversary_decision":
            action = decision.get("action")
            action = action if isinstance(action, Mapping) else {}
            accepted = consequence.get("accepted")
            if accepted is False:
                adversary_rejected_actions += 1
            elif action.get("kind") != "wait" and accepted is True:
                adversary_disruptions += 1
        elif event_type == "incident_opened":
            incident_id = decision.get("incident_id")
            if type(incident_id) is int:
                turn = event.get("turn")
                open_incidents.setdefault(incident_id, turn if type(turn) is int else None)
        elif event_type == "incident_closed":
            incident_id = decision.get("incident_id")
            if type(incident_id) is int:
                opened_turn = open_incidents.pop(incident_id, None)
                closed_turn = event.get("turn")
                if opened_turn is not None and type(closed_turn) is int:
                    resolution_turns.append(max(0, closed_turn - opened_turn))
                scheduled_followups.pop(incident_id, None)
        elif event_type == "follow_up_scheduled":
            incident_id = decision.get("incident_id")
            follow_up_turn = decision.get("follow_up_turn")
            if type(incident_id) is int and type(follow_up_turn) is int:
                scheduled_followups[incident_id] = follow_up_turn
        elif event_type == "follow_up_due":
            incident_id = decision.get("incident_id")
            scheduled_turn = decision.get("scheduled_turn")
            if (
                type(incident_id) is int
                and type(scheduled_turn) is int
                and scheduled_followups.get(incident_id) == scheduled_turn
            ):
                scheduled_followups.pop(incident_id, None)
        elif event_type == "world_evidence":
            evidence = event.get("evidence")
            if isinstance(evidence, Mapping) and evidence.get("kind") == "repair_complete":
                repair_sequences.add(evidence["sequence"])

        metadata = _provider_metadata(event, decision, consequence)
        if metadata is not None and event_type in {
            "dispatch",
            "captain_decision",
            "provider_failure",
            "adversary_decision",
        }:
            calls = _reported_calls(metadata)
            if calls > 0:
                provider_records.append((calls, metadata))

    if states:
        # World evidence is cumulative: the final snapshot already includes every repair.
        repair_sequences.update(
            item.sequence for item in states[-1].evidence if item.kind == "repair_complete"
        )

    evidenced_repairs = len(repair_sequences)
    completed_repairs = states[-1].repairs_completed if states else 0
    metrics.update(
        {
            "inspections": inspections,
            "parts_consumed": parts_consumed,
            "clarification_requests": clarification_requests,
            "repair_completions": max(completed_repairs, evidenced_repairs),
            "invalid_actions": invalid_actions,
            "adversary_disruptions": adversary_disruptions,
            "adversary_rejected_actions": adversary_rejected_actions,
            "unresolved_incidents": len(open_incidents),
            "forgotten_incidents": sum(
                incident_id not in scheduled_followups
                or scheduled_followups[incident_id] <= final_turn
                for incident_id in open_incidents
            ),
            "incident_resolution_turns": sum(resolution_turns),
        }
    )
    if resolution_turns:
        metrics["mean_incident_resolution_turns"] = sum(resolution_turns) / len(resolution_turns)
    else:
        unavailable.append("mean_incident_resolution_turns")

    model_calls = sum(calls for calls, _ in provider_records)
    metrics["model_calls"] = model_calls
    if model_calls == 0:
        metrics.update(
            {
                "model_latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "model_cost": 0,
            }
        )
    else:
        _add_provider_metric(
            metrics, unavailable, provider_records, "latency_ms", "model_latency_ms"
        )
        _add_provider_metric(metrics, unavailable, provider_records, "input_tokens", "input_tokens")
        _add_provider_metric(
            metrics, unavailable, provider_records, "output_tokens", "output_tokens"
        )
        _add_provider_metric(metrics, unavailable, provider_records, "cost_usd", "model_cost")

    return MissionEvaluation(metrics=metrics, unavailable=tuple(unavailable))


def _provider_metadata(
    event: Mapping[str, object],
    decision: Mapping[str, object],
    consequence: Mapping[str, object],
) -> Mapping[str, object] | None:
    candidates = (
        decision.get("metadata"),
        consequence.get("metadata"),
        event.get("metadata"),
    )
    for candidate in candidates:
        if isinstance(candidate, Mapping) and ("calls" in candidate or "request_made" in candidate):
            return candidate
    return None


def _reported_calls(metadata: Mapping[str, object]) -> int:
    if metadata.get("request_made") is False:
        return 0
    calls = metadata.get("calls")
    if type(calls) is int and calls >= 0:
        return calls
    return int(metadata.get("request_made") is True)


def _add_provider_metric(
    metrics: dict[str, int | float | bool],
    unavailable: list[str],
    provider_records: Sequence[tuple[int, Mapping[str, object]]],
    source_key: str,
    metric_key: str,
) -> None:
    values = [metadata.get(source_key) for _, metadata in provider_records]
    valid: list[int | float] = []
    for value in values:
        if type(value) is int and value >= 0:
            valid.append(value)
        elif type(value) is float and isfinite(value) and value >= 0:
            valid.append(value)
        else:
            unavailable.append(metric_key)
            return
    metrics[metric_key] = sum(valid)
