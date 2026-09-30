"""Mission orchestration public API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum, StrEnum
from typing import Callable, Mapping
from uuid import uuid4

from .controllers import (
    ActionDescriptor,
    ActionRequest,
    ActionRequestKind,
    CaptainContext,
    CaptainDecision,
    CaptainProvider,
    DispatchContext,
    DispatchJudgment,
    DispatchProvider,
    NoulOutcome,
    ProviderError,
    ProviderErrorCode,
    PublicEvidence,
    StationView,
    Subsystem,
    WorkflowEvidence,
)
from .domain import (
    Action,
    ActionKind,
    Evidence,
    SensorReading,
    StationObservation,
    StationState,
    advance_turn,
    apply_action,
    observe,
)
from .evaluation import MissionEvaluation, evaluate_mission
from .scenarios import (
    ScenarioDefinition,
    ScenarioFamily,
    create_configured_world,
    create_world,
    scenario_definition_to_dict,
)

SIMULATOR_VERSION = "0.2.0"
MAX_MISSION_TURNS = 14 * 24
MAX_INSPECTIONS_PER_TURN = 6
QUESTION_VERSION = "jev-questions-v1"
RUBRIC_VERSION = "jev-rubric-v1"
INSTRUCTION_VERSION = "captain-instructions-v1"


class ControllerMode(StrEnum):
    RULES = "rules"
    LLM = "llm"
    JEV_LLM = "jev+llm"


class EvidenceAccess(StrEnum):
    LATEST = "latest"
    HISTORY = "history"


class MissionStatus(StrEnum):
    COMPLETED = "completed"
    CREW_LOST = "crew_lost"


@dataclass(frozen=True, slots=True)
class MissionConfig:
    scenario: ScenarioFamily | str = ScenarioFamily.NORMAL
    seed: int = 0
    duration_turns: int = MAX_MISSION_TURNS
    controller_mode: ControllerMode | str = ControllerMode.RULES
    evidence_access: EvidenceAccess | str = EvidenceAccess.LATEST
    inspection_budget_per_turn: int = 1
    escalation_threshold: int = 55
    run_id: str | None = None
    question_version: str = QUESTION_VERSION
    rubric_version: str = RUBRIC_VERSION
    instruction_version: str = INSTRUCTION_VERSION
    scenario_definition: ScenarioDefinition | None = None

    def __post_init__(self) -> None:
        try:
            if self.scenario_definition is None:
                object.__setattr__(self, "scenario", ScenarioFamily(self.scenario))
            elif not isinstance(self.scenario_definition, ScenarioDefinition):
                raise ValueError("Scenario definition must be a validated ScenarioDefinition")
            else:
                object.__setattr__(self, "scenario", self.scenario_definition.id)
            object.__setattr__(self, "controller_mode", ControllerMode(self.controller_mode))
            object.__setattr__(self, "evidence_access", EvidenceAccess(self.evidence_access))
        except ValueError as error:
            raise ValueError(f"Unsupported mission configuration: {error}") from error
        if type(self.seed) is not int:
            raise ValueError("Mission seed must be an integer")
        if (
            type(self.duration_turns) is not int
            or not 1 <= self.duration_turns <= MAX_MISSION_TURNS
        ):
            raise ValueError(f"Mission duration must be between 1 and {MAX_MISSION_TURNS} turns")
        if (
            type(self.inspection_budget_per_turn) is not int
            or not 0 <= self.inspection_budget_per_turn <= MAX_INSPECTIONS_PER_TURN
        ):
            raise ValueError(
                f"Inspection budget must be between 0 and {MAX_INSPECTIONS_PER_TURN} per turn"
            )
        if type(self.escalation_threshold) is not int or not 0 <= self.escalation_threshold <= 100:
            raise ValueError("Escalation threshold must be an integer from 0 to 100")
        if self.run_id is not None and (not isinstance(self.run_id, str) or not self.run_id):
            raise ValueError("Run ID must be a nonempty string")
        if self.question_version != QUESTION_VERSION:
            raise ValueError(f"Unsupported Jev question version: {self.question_version!r}")
        if self.rubric_version != RUBRIC_VERSION:
            raise ValueError(f"Unsupported Jev rubric version: {self.rubric_version!r}")
        if self.instruction_version != INSTRUCTION_VERSION:
            raise ValueError(
                f"Unsupported captain instruction version: {self.instruction_version!r}"
            )


@dataclass(frozen=True, slots=True)
class DebugSnapshot:
    turn: int
    oxygen: int
    crew_alive: bool
    leak_active: bool
    sensor_fault: str | None
    backup_oxygen: int
    parts: int
    credits: int
    oxygen_sensors: tuple[SensorReading, ...]


@dataclass(frozen=True, slots=True)
class MissionResult:
    run_id: str
    status: MissionStatus
    turns_completed: int
    events: tuple[dict[str, object], ...]
    evaluation: MissionEvaluation
    debug_snapshots: tuple[DebugSnapshot, ...]


EventSink = Callable[[dict[str, object]], None]


def mission_metadata(config: MissionConfig) -> dict[str, object]:
    """Return the versioned, serializable settings used to compose a mission."""
    metadata: dict[str, object] = {
        "scenario": (
            config.scenario.value
            if isinstance(config.scenario, ScenarioFamily)
            else config.scenario
        ),
        "seed": config.seed,
        "duration_turns": config.duration_turns,
        "controller": config.controller_mode.value,
        "evidence_access": config.evidence_access.value,
        "inspection_budget_per_turn": config.inspection_budget_per_turn,
        "escalation_threshold": config.escalation_threshold,
        "question_version": config.question_version,
        "rubric_version": config.rubric_version,
        "instruction_version": config.instruction_version,
    }
    if config.scenario_definition is not None:
        metadata["scenario_definition"] = scenario_definition_to_dict(config.scenario_definition)
    return metadata


def run_mission(
    config: MissionConfig,
    *,
    captain: CaptainProvider | None = None,
    dispatcher: DispatchProvider | None = None,
    event_sink: EventSink | None = None,
) -> MissionResult:
    """Run one bounded station mission with caller-supplied controller ports."""
    if config.controller_mode in {ControllerMode.LLM, ControllerMode.JEV_LLM} and captain is None:
        raise ValueError(
            f"Controller mode {config.controller_mode.value!r} requires a captain provider"
        )
    if config.controller_mode is ControllerMode.JEV_LLM and dispatcher is None:
        raise ValueError("Controller mode 'jev+llm' requires a dispatch provider")

    run_id = config.run_id or str(uuid4())
    state = (
        create_configured_world(config.scenario_definition, config.seed)
        if config.scenario_definition is not None
        else create_world(config.scenario, config.seed)
    )
    states = [state]
    public_history: list[PublicEvidence] = list(state.evidence)
    events: list[dict[str, object]] = []
    incidents: dict[int, _Incident] = {}

    def emit(
        event_type: str,
        turn: int,
        *,
        evidence: object = None,
        decision: object = None,
        consequence: object = None,
    ) -> dict[str, object]:
        record = {
            "record_type": "event",
            "sequence": len(events),
            "turn": turn,
            "event_type": event_type,
            "evidence": _json_value(evidence),
            "decision": _json_value(decision),
            "consequence": _json_value(consequence),
        }
        events.append(record)
        if event_sink is not None:
            event_sink(dict(record))
        return record

    for _ in range(config.duration_turns):
        if not state.crew_alive:
            break
        turn_result = advance_turn(state)
        state = turn_result.state
        states.append(state)

        for item in turn_result.evidence:
            public_history.append(item)
            emit("world_evidence", item.turn, evidence=item)
            _register_evidence(item, incidents, emit)
        for incident in incidents.values():
            if incident.open and incident.pending_clarification_turn == state.turn:
                response = WorkflowEvidence(
                    sequence=f"clarification-{incident.id}-{state.turn}",
                    turn=state.turn,
                    kind="clarification",
                    message=(
                        "Maintenance cannot independently confirm the diagnosis. "
                        "Inspect the oxygen system and compare the sensor readings."
                    ),
                )
                incident.related.append(response)
                public_history.append(response)
                incident.pending_clarification_turn = None
                emit(
                    "clarification_received",
                    state.turn,
                    evidence=response,
                    decision={"incident_id": incident.id},
                    consequence={"source": "scripted_maintenance"},
                )
        if not state.crew_alive:
            break

        inspections_used = 0
        due_incidents = sorted(
            (
                incident
                for incident in incidents.values()
                if incident.open
                and incident.next_turn is not None
                and incident.next_turn <= state.turn
            ),
            key=lambda item: (item.next_turn or 0, item.id),
        )
        for incident in due_incidents:
            station = _station_view(observe(state))
            emit(
                "follow_up_due",
                state.turn,
                evidence=incident.origin,
                decision={"incident_id": incident.id, "scheduled_turn": incident.next_turn},
                consequence={"status": "reviewed"},
            )
            if config.controller_mode is ControllerMode.JEV_LLM and not incident.dispatched:
                if _dispatch(incident, station, config, dispatcher, emit):
                    incident.dispatched = True
                else:
                    _schedule_follow_up(
                        incident,
                        state.turn,
                        config.duration_turns,
                        emit,
                        reason="low_priority_monitoring",
                        delay=4,
                    )
                    continue

            if config.controller_mode is ControllerMode.RULES:
                decision = _rules_decision(incident, station, config.duration_turns)
                rationale = "deterministic rules policy v1"
                provider_metadata: Mapping[str, object] = {}
            else:
                assert captain is not None
                context = _captain_context(
                    incident,
                    station,
                    tuple(public_history),
                    config,
                    inspections_remaining=config.inspection_budget_per_turn - inspections_used,
                )
                try:
                    decision = captain.decide(context)
                except ProviderError as error:
                    _provider_failure(
                        emit, "captain", incident, state.turn, error.code.value, error.metadata
                    )
                    _schedule_follow_up(
                        incident, state.turn, config.duration_turns, emit, reason="provider_failure"
                    )
                    continue
                except Exception:
                    _provider_failure(
                        emit,
                        "captain",
                        incident,
                        state.turn,
                        ProviderErrorCode.PROVIDER_UNAVAILABLE.value,
                        {},
                    )
                    _schedule_follow_up(
                        incident, state.turn, config.duration_turns, emit, reason="provider_failure"
                    )
                    continue
                if not isinstance(decision, CaptainDecision):
                    _provider_failure(
                        emit,
                        "captain",
                        incident,
                        state.turn,
                        ProviderErrorCode.MALFORMED_RESPONSE.value,
                        {},
                    )
                    _schedule_follow_up(
                        incident, state.turn, config.duration_turns, emit, reason="provider_failure"
                    )
                    continue
                rationale = decision.rationale
                provider_metadata = decision.metadata
                decision = decision.action
                emit(
                    "captain_decision",
                    state.turn,
                    evidence=context.evidence,
                    decision=_request_record(incident.id, decision, rationale, provider_metadata),
                    consequence={"accepted_as_proposal": True},
                )

            state, inspection_attempted = _perform_action(
                decision,
                incident,
                state,
                config,
                inspections_remaining=config.inspection_budget_per_turn - inspections_used,
                rationale=rationale,
                metadata=provider_metadata,
                emit=emit,
                public_history=public_history,
            )
            states[-1] = state
            inspections_used += int(inspection_attempted)

    status = MissionStatus.COMPLETED if state.crew_alive else MissionStatus.CREW_LOST
    evaluation = evaluate_mission(states, events)
    return MissionResult(
        run_id=run_id,
        status=status,
        turns_completed=state.turn,
        events=tuple(events),
        evaluation=evaluation,
        debug_snapshots=tuple(_debug_snapshot(item) for item in states),
    )


@dataclass(slots=True)
class _Incident:
    id: int
    origin: Evidence
    related: list[PublicEvidence]
    created_turn: int
    next_turn: int | None
    open: bool = True
    dispatched: bool = False
    pending_clarification_turn: int | None = None


_ACTION_DESCRIPTORS = {
    ActionRequestKind.READ_HISTORY: ActionDescriptor(
        ActionRequestKind.READ_HISTORY,
        "Read evidence available under the configured history policy.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ActionRequestKind.REQUEST_CLARIFICATION: ActionDescriptor(
        ActionRequestKind.REQUEST_CLARIFICATION,
        "Request a clarification and check again on a later turn.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ActionRequestKind.INSPECT: ActionDescriptor(
        ActionRequestKind.INSPECT,
        "Inspect a sensor or the oxygen system; consumes an inspection slot.",
        {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["oxygen_system", "sensor_a", "sensor_b"],
                }
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    ),
    ActionRequestKind.ASSIGN_REPAIR: ActionDescriptor(
        ActionRequestKind.ASSIGN_REPAIR,
        "Assign a crew member to repair the oxygen system.",
        {
            "type": "object",
            "properties": {"target": {"type": "string", "enum": ["oxygen_system"]}},
            "required": ["target"],
            "additionalProperties": False,
        },
    ),
    ActionRequestKind.ACTIVATE_BACKUP: ActionDescriptor(
        ActionRequestKind.ACTIVATE_BACKUP,
        "Activate the finite backup oxygen reserve.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ActionRequestKind.ORDER_SUPPLIES: ActionDescriptor(
        ActionRequestKind.ORDER_SUPPLIES,
        "Order a bounded amount of oxygen or spare parts.",
        {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["oxygen", "parts"]},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["target", "quantity"],
            "additionalProperties": False,
        },
    ),
    ActionRequestKind.DEFER: ActionDescriptor(
        ActionRequestKind.DEFER,
        "Monitor the incident and choose a future follow-up turn.",
        {
            "type": "object",
            "properties": {"follow_up_turn": {"type": "integer", "minimum": 1}},
            "required": ["follow_up_turn"],
            "additionalProperties": False,
        },
    ),
    ActionRequestKind.CLOSE: ActionDescriptor(
        ActionRequestKind.CLOSE,
        "Close only with a reason and cited inspection or repair evidence.",
        {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "minLength": 1},
                "evidence_sequences": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["reason", "evidence_sequences"],
            "additionalProperties": False,
        },
    ),
}


def _dispatch(
    incident: _Incident,
    station: StationView,
    config: MissionConfig,
    dispatcher: DispatchProvider | None,
    emit: Callable[..., dict[str, object]],
) -> bool:
    assert dispatcher is not None
    context = DispatchContext(
        report=incident.origin,
        station=station,
        evidence=(
            tuple(incident.related)
            if config.evidence_access is EvidenceAccess.HISTORY
            else (incident.origin,)
        ),
        question_version=config.question_version,
        rubric_version=config.rubric_version,
    )
    try:
        judgment = dispatcher.classify(context)
        if not isinstance(judgment, DispatchJudgment):
            raise ValueError("malformed dispatch result")
        subsystem = Subsystem(judgment.subsystem)
        safeguard = NoulOutcome(judgment.safeguard_request)
        diagnosis = NoulOutcome(judgment.diagnosis_supported)
        urgency = judgment.urgency
        if type(urgency) is not int or not 0 <= urgency <= 100:
            raise ValueError("invalid urgency score")
    except ProviderError as error:
        _provider_failure(
            emit, "dispatcher", incident, station.turn, error.code.value, error.metadata
        )
        return True
    except Exception:
        _provider_failure(
            emit,
            "dispatcher",
            incident,
            station.turn,
            ProviderErrorCode.MALFORMED_RESPONSE.value,
            {},
        )
        return True

    routed = (
        incident.origin.kind == "alert"
        or subsystem is Subsystem.UNKNOWN
        or safeguard is not NoulOutcome.NO
        or diagnosis is not NoulOutcome.YES
        or urgency >= config.escalation_threshold
    )
    emit(
        "dispatch",
        station.turn,
        evidence=incident.origin,
        decision={
            "incident_id": incident.id,
            "subsystem": subsystem.value,
            "safeguard_request": safeguard.value,
            "diagnosis_supported": diagnosis.value,
            "urgency": urgency,
            "rationale": judgment.rationale[:500],
            "metadata": judgment.metadata,
        },
        consequence={"routed": routed},
    )
    return routed


def _captain_context(
    incident: _Incident,
    station: StationView,
    full_history: tuple[PublicEvidence, ...],
    config: MissionConfig,
    *,
    inspections_remaining: int,
) -> CaptainContext:
    if config.evidence_access is EvidenceAccess.HISTORY:
        evidence = full_history
    else:
        evidence = (incident.origin,)
        if incident.related and incident.related[-1].sequence != incident.origin.sequence:
            evidence += (incident.related[-1],)
    catalog = tuple(
        descriptor
        for kind, descriptor in _ACTION_DESCRIPTORS.items()
        if not (
            kind is ActionRequestKind.READ_HISTORY
            and config.evidence_access is EvidenceAccess.LATEST
        )
        and not (kind is ActionRequestKind.INSPECT and inspections_remaining <= 0)
        and not (kind is ActionRequestKind.ACTIVATE_BACKUP and station.backup_oxygen <= 0)
    )
    return CaptainContext(
        incident=incident.origin,
        station=station,
        evidence=tuple(evidence),
        allowed_actions=catalog,
        inspection_budget_remaining=inspections_remaining,
        instruction_version=config.instruction_version,
    )


def _register_evidence(
    evidence: Evidence,
    incidents: dict[int, _Incident],
    emit: Callable[..., dict[str, object]],
) -> None:
    if evidence.kind in {"alert", "report"}:
        existing = next(
            (
                item
                for item in incidents.values()
                if item.open and item.created_turn == evidence.turn
            ),
            None,
        )
        if existing is not None:
            existing.related.append(evidence)
            emit(
                "incident_updated",
                evidence.turn,
                evidence=evidence,
                decision={"incident_id": existing.id},
                consequence={"status": "additional_evidence"},
            )
            return
        incident = _Incident(
            id=evidence.sequence,
            origin=evidence,
            related=[evidence],
            created_turn=evidence.turn,
            next_turn=evidence.turn,
        )
        incidents[incident.id] = incident
        emit(
            "incident_opened",
            evidence.turn,
            evidence=evidence,
            decision={"incident_id": incident.id},
            consequence={"status": "open"},
        )
    elif evidence.kind in {"inspection", "action", "repair_complete", "arrival"}:
        for incident in incidents.values():
            if incident.open:
                incident.related.append(evidence)
                if evidence.kind == "repair_complete":
                    incident.next_turn = evidence.turn
                emit(
                    "incident_updated",
                    evidence.turn,
                    evidence=evidence,
                    decision={"incident_id": incident.id},
                    consequence={"status": "new_evidence"},
                )


def _rules_decision(incident: _Incident, station: StationView, duration: int) -> ActionRequest:
    recent = incident.related[-1] if incident.related else incident.origin
    if recent.kind == "repair_complete":
        return ActionRequest(
            ActionRequestKind.CLOSE,
            reason="repair completion was recorded",
            evidence_sequences=(recent.sequence,),
        )
    if recent.kind == "inspection":
        message = recent.message.lower()
        if "active oxygen leak" in message:
            return ActionRequest(ActionRequestKind.ASSIGN_REPAIR, target="oxygen_system")
        if "calibration fault" in message:
            return ActionRequest(
                ActionRequestKind.DEFER,
                follow_up_turn=min(station.turn + 4, duration),
            )
        if "within calibration range" in message or "operating normally" in message:
            return ActionRequest(
                ActionRequestKind.CLOSE,
                reason="inspection produced supporting evidence",
                evidence_sequences=(recent.sequence,),
            )
    if recent.kind == "action" and "repair assigned" in recent.message.lower():
        follow_up = min(station.turn + 1, duration)
        if follow_up > station.turn:
            return ActionRequest(ActionRequestKind.DEFER, follow_up_turn=follow_up)
    combined = " ".join(item.message.lower() for item in incident.related)
    if ("critical" in combined or "exhausted" in combined) and station.backup_oxygen > 0:
        if not any("backup oxygen activated" in item.message.lower() for item in incident.related):
            return ActionRequest(ActionRequestKind.ACTIVATE_BACKUP)
    if (
        len(station.oxygen_sensors) == 2
        and station.oxygen_sensors[0].oxygen != station.oxygen_sensors[1].oxygen
    ):
        sensor = min(station.oxygen_sensors, key=lambda reading: reading.oxygen).sensor
        return ActionRequest(ActionRequestKind.INSPECT, target=sensor)
    return ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system")


def _perform_action(
    value: object,
    incident: _Incident,
    state: StationState,
    config: MissionConfig,
    *,
    inspections_remaining: int,
    rationale: str,
    metadata: Mapping[str, object],
    emit: Callable[..., dict[str, object]],
    public_history: list[PublicEvidence],
) -> tuple[StationState, bool]:
    request = value if isinstance(value, ActionRequest) else ActionRequest("invalid_action")
    kind = _request_kind(request)
    inspect_attempt = kind is ActionRequestKind.INSPECT
    decision = _request_record(incident.id, request, rationale, metadata)

    def reject(code: str) -> tuple[StationState, bool]:
        emit(
            "action",
            state.turn,
            evidence=incident.origin,
            decision=decision,
            consequence={"accepted": False, "rejection": code},
        )
        _schedule_follow_up(
            incident, state.turn, config.duration_turns, emit, reason="action_rejected"
        )
        return state, inspect_attempt

    if kind is None:
        return reject("invalid_action")
    if inspect_attempt and inspections_remaining <= 0:
        return reject("inspection_budget_exhausted")
    if kind is ActionRequestKind.READ_HISTORY:
        if config.evidence_access is EvidenceAccess.LATEST:
            return reject("history_access_limited")
        emit(
            "action",
            state.turn,
            evidence=incident.related,
            decision=decision,
            consequence={"accepted": True, "records_returned": len(incident.related)},
        )
        _schedule_follow_up(
            incident, state.turn, config.duration_turns, emit, reason="history_read"
        )
        return state, False
    if kind in {ActionRequestKind.REQUEST_CLARIFICATION, ActionRequestKind.DEFER}:
        follow_up = request.follow_up_turn
        if kind is ActionRequestKind.REQUEST_CLARIFICATION and follow_up is None:
            follow_up = _next_turn(state.turn, config.duration_turns)
        if (
            type(follow_up) is not int
            or follow_up <= state.turn
            or follow_up > config.duration_turns
        ):
            return reject("invalid_follow_up_turn")
        emit(
            "action",
            state.turn,
            evidence=incident.origin,
            decision=decision,
            consequence={
                "accepted": True,
                "follow_up_turn": follow_up,
                "response_pending": kind is ActionRequestKind.REQUEST_CLARIFICATION,
            },
        )
        _set_follow_up(incident, state.turn, follow_up, emit, reason=kind.value)
        if kind is ActionRequestKind.REQUEST_CLARIFICATION:
            incident.pending_clarification_turn = follow_up
        return state, False
    if kind is ActionRequestKind.CLOSE:
        reason = request.reason.strip() if isinstance(request.reason, str) else ""
        support = {
            item.sequence: item
            for item in incident.related
            if isinstance(item, Evidence) and _resolves_latest_evidence(incident, item)
        }
        cited = tuple(sequence for sequence in request.evidence_sequences if sequence in support)
        if not reason or not cited:
            return reject("resolution_evidence_required")
        emit(
            "action",
            state.turn,
            evidence=[support[sequence] for sequence in cited],
            decision=decision,
            consequence={"accepted": True, "resolution_evidence": list(cited)},
        )
        incident.open = False
        incident.next_turn = None
        emit(
            "incident_closed",
            state.turn,
            evidence=[support[sequence] for sequence in cited],
            decision={"incident_id": incident.id, "resolution_reason": reason[:500]},
            consequence={"status": "closed"},
        )
        return state, False

    domain_kind = {
        ActionRequestKind.INSPECT: ActionKind.INSPECT,
        ActionRequestKind.ASSIGN_REPAIR: ActionKind.ASSIGN_REPAIR,
        ActionRequestKind.ACTIVATE_BACKUP: ActionKind.ACTIVATE_BACKUP,
        ActionRequestKind.ORDER_SUPPLIES: ActionKind.ORDER_SUPPLIES,
    }.get(kind)
    if domain_kind is None:
        return reject("invalid_action")
    if request.target is not None and not isinstance(request.target, str):
        return reject("invalid_target")
    if request.quantity is not None and type(request.quantity) is not int:
        return reject("invalid_quantity")

    result = apply_action(state, Action(domain_kind, request.target, request.quantity))
    incident.related.extend(result.evidence)
    public_history.extend(result.evidence)
    emit(
        "action",
        state.turn,
        evidence=result.evidence or incident.origin,
        decision=decision,
        consequence={"accepted": result.accepted, "rejection": result.rejection},
    )
    if result.accepted:
        delay = (
            2 if kind in {ActionRequestKind.ASSIGN_REPAIR, ActionRequestKind.ORDER_SUPPLIES} else 1
        )
        _schedule_follow_up(
            incident,
            state.turn,
            config.duration_turns,
            emit,
            reason="physical_action",
            delay=delay,
        )
    else:
        _schedule_follow_up(
            incident, state.turn, config.duration_turns, emit, reason="action_rejected"
        )
    return result.state, inspect_attempt


def _schedule_follow_up(
    incident: _Incident,
    turn: int,
    duration: int,
    emit: Callable[..., dict[str, object]],
    *,
    reason: str,
    delay: int = 1,
) -> None:
    due = _next_turn(turn, duration, delay)
    _set_follow_up(incident, turn, due, emit, reason=reason)


def _resolves_latest_evidence(incident: _Incident, cited: Evidence) -> bool:
    relevant = [
        item
        for item in incident.related
        if isinstance(item, Evidence)
        and item.kind in {"alert", "report", "inspection", "repair_complete"}
    ]
    if not relevant or relevant[-1].sequence != cited.sequence:
        return False
    if cited.kind == "repair_complete":
        return True
    message = cited.message.lower()
    if cited.kind != "inspection":
        return False
    origin = incident.origin.message.lower()
    if "sensors are reporting different levels" in origin:
        return "calibration fault" in message
    return "oxygen system operating normally" in message


def _set_follow_up(
    incident: _Incident,
    turn: int,
    due: int | None,
    emit: Callable[..., dict[str, object]],
    *,
    reason: str,
) -> None:
    incident.next_turn = due
    emit(
        "follow_up_scheduled",
        turn,
        evidence=incident.origin,
        decision={"incident_id": incident.id, "follow_up_turn": due},
        consequence={"reason": reason},
    )


def _request_record(
    incident_id: int,
    request: ActionRequest,
    rationale: str,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    return {
        "incident_id": incident_id,
        "kind": str(request.kind),
        "target": request.target,
        "quantity": request.quantity,
        "follow_up_turn": request.follow_up_turn,
        "reason": request.reason,
        "evidence_sequences": list(request.evidence_sequences),
        "rationale": str(rationale)[:500],
        "metadata": _safe_metadata(metadata),
    }


def _provider_failure(
    emit: Callable[..., dict[str, object]],
    provider: str,
    incident: _Incident,
    turn: int,
    code: str,
    metadata: Mapping[str, object],
) -> None:
    emit(
        "provider_failure",
        turn,
        evidence=incident.origin,
        decision={"incident_id": incident.id},
        consequence={
            "provider": provider,
            "code": code,
            "metadata": _safe_metadata(metadata),
        },
    )


def _request_kind(request: ActionRequest) -> ActionRequestKind | None:
    try:
        return ActionRequestKind(request.kind)
    except (TypeError, ValueError):
        return None


def _next_turn(turn: int, duration: int, delay: int = 1) -> int | None:
    due = turn + delay
    return due if due <= duration else None


def _station_view(observation: StationObservation) -> StationView:
    return StationView(
        turn=observation.turn,
        oxygen_sensors=observation.oxygen_sensors,
        backup_oxygen=observation.backup_oxygen,
        parts=observation.parts,
        credits=observation.credits,
        available_crew=observation.available_crew,
    )


def _debug_snapshot(state: StationState) -> DebugSnapshot:
    return DebugSnapshot(
        turn=state.turn,
        oxygen=state.oxygen,
        crew_alive=state.crew_alive,
        leak_active=state.leak_active,
        sensor_fault=state.sensor_fault,
        backup_oxygen=state.backup_oxygen,
        parts=state.parts,
        credits=state.credits,
        oxygen_sensors=state.sensor_readings,
    )


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "provider",
        "model",
        "prompt_version",
        "question_version",
        "rubric_version",
        "instruction_version",
        "request_made",
        "latency_ms",
        "calls",
        "cost_usd",
        "input_tokens",
        "output_tokens",
    }
    return {str(key): _json_value(value) for key, value in metadata.items() if key in allowed}


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
