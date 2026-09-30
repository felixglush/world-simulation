"""Life-support world state and bounded, deterministic state transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class ActionKind(StrEnum):
    INSPECT = "inspect"
    ASSIGN_REPAIR = "assign_repair"
    ACTIVATE_BACKUP = "activate_backup"
    ORDER_SUPPLIES = "order_supplies"


@dataclass(frozen=True, slots=True)
class Evidence:
    sequence: int
    turn: int
    kind: str
    message: str


@dataclass(frozen=True, slots=True)
class SensorReading:
    sensor: str
    oxygen: int
    sampled_turn: int = 0
    source: str = ""


@dataclass(frozen=True, slots=True)
class StationObservation:
    turn: int
    oxygen_sensors: tuple[SensorReading, ...]
    backup_oxygen: int
    parts: int
    credits: int
    available_crew: int
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind | str
    target: str | None = None
    quantity: int | None = None


@dataclass(frozen=True, slots=True)
class ActionResult:
    state: StationState
    accepted: bool
    rejection: str | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class TurnResult:
    state: StationState
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    turn: int
    kind: str
    target: str | None = None
    message: str | None = None
    value: int | None = None


@dataclass(frozen=True, slots=True)
class Delivery:
    due_turn: int
    supply: str
    quantity: int
    fill_percent: int = 100


@dataclass(frozen=True, slots=True)
class StationState:
    """Authoritative world state; pass `observe(state)` to decision makers."""

    turn: int
    oxygen: int
    oxygen_capacity: int
    generation_rate: int
    consumption_rate: int
    leak_rate: int
    leak_active: bool
    backup_oxygen: int
    backup_rate: int
    backup_active: bool
    parts: int
    credits: int
    crew_count: int
    available_crew: int
    crew_alive: bool
    repair_turns_remaining: int
    sensor_readings: tuple[SensorReading, ...]
    sensor_fault: str | None
    sensor_stuck_reading: int
    scheduled_events: tuple[ScheduledEvent, ...]
    deliveries: tuple[Delivery, ...]
    evidence: tuple[Evidence, ...]
    sensor_mode: str = "independent"
    reserved_crew: int = 0
    repair_duration_turns: int = 2
    delivery_delay_turns: int = 0
    delivery_fill_percent: int = 100
    repair_notice_delay_turns: int = 0
    duplicate_notice_delay_turns: int = 0
    repairs_completed: int = 0


def observe(state: StationState) -> StationObservation:
    """Return the crew-facing view without exposing scenario or fault labels."""
    return StationObservation(
        turn=state.turn,
        oxygen_sensors=state.sensor_readings,
        backup_oxygen=state.backup_oxygen,
        parts=state.parts,
        credits=state.credits,
        available_crew=state.available_crew,
        evidence=state.evidence,
    )


def apply_action(state: StationState, action: Action) -> ActionResult:
    """Validate and apply one bounded world action without advancing time."""
    if not isinstance(action, Action):
        return _rejected(state, "invalid_action")
    try:
        kind = ActionKind(action.kind)
    except (TypeError, ValueError):
        return _rejected(state, "invalid_action")
    if not state.crew_alive:
        return _rejected(state, "crew_lost")

    if action.target is not None and not isinstance(action.target, str):
        return _rejected(state, "invalid_target")
    if kind is ActionKind.INSPECT:
        return _inspect(state, action)
    if kind is ActionKind.ASSIGN_REPAIR:
        return _assign_repair(state, action)
    if kind is ActionKind.ACTIVATE_BACKUP:
        if action.target is not None:
            return _rejected(state, "invalid_target")
        if action.quantity is not None:
            return _rejected(state, "invalid_quantity")
        if state.backup_active:
            return _rejected(state, "backup_already_active")
        if state.backup_oxygen <= 0:
            return _rejected(state, "backup_empty")
        updated = replace(state, backup_active=True)
        return _accepted(updated, "Backup oxygen activated.")
    return _order_supplies(state, action)


def advance_turn(state: StationState) -> TurnResult:
    """Advance physical time, scheduled events, repair work, and deliveries."""
    if not state.crew_alive:
        return TurnResult(state=state, evidence=())
    turn = state.turn + 1
    next_state = replace(state, turn=turn)
    emitted: list[Evidence] = []
    scheduled_messages: list[tuple[str, str | None, str]] = []

    remaining_events = []
    for event in state.scheduled_events:
        if event.turn != turn:
            remaining_events.append(event)
            continue
        if event.kind == "leak_start":
            next_state = replace(next_state, leak_active=True)
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                "Life-support pressure has dropped below its normal range.",
            )
        elif event.kind == "leak_stop":
            next_state = replace(next_state, leak_active=False)
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                "Life-support pressure has returned to its normal range.",
            )
        elif event.kind == "sensor_fault":
            target = event.target
            stuck = (
                _reading_for(next_state, target).oxygen
                if target in {"sensor_a", "sensor_b"}
                else next_state.oxygen
            )
            next_state = replace(
                next_state,
                sensor_mode="independent",
                sensor_fault=target,
                sensor_stuck_reading=stuck,
            )
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                "The independent oxygen sensors are reporting different levels.",
            )
        elif event.kind == "sensor_stale":
            target = event.target
            stuck = _reading_for(
                next_state,
                target if target in {"sensor_a", "sensor_b"} else "sensor_a",
            ).oxygen
            next_state = replace(
                next_state,
                sensor_mode="stale",
                sensor_fault=target,
                sensor_stuck_reading=stuck,
            )
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                "An oxygen sensor is retaining an older sample.",
            )
        elif event.kind == "sensor_correlated":
            source_sensor = event.target if event.target in {"sensor_a", "sensor_b"} else "sensor_a"
            sample = _reading_for(next_state, source_sensor)
            source = f"shared:{source_sensor}"
            next_state = replace(
                next_state,
                sensor_mode="correlated",
                sensor_fault=None,
                sensor_stuck_reading=sample.oxygen,
                sensor_readings=tuple(
                    SensorReading(
                        sensor=sensor,
                        oxygen=sample.oxygen,
                        sampled_turn=sample.sampled_turn,
                        source=source,
                    )
                    for sensor in ("sensor_a", "sensor_b")
                ),
            )
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                "The oxygen sensors are receiving data from a shared source.",
            )
        elif event.kind == "sensor_restore":
            next_state = replace(
                next_state,
                sensor_mode="independent",
                sensor_fault=None,
                sensor_stuck_reading=next_state.oxygen,
            )
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                "Independent oxygen sensor sampling has been restored.",
            )
        elif event.kind == "crew_busy":
            count = event.value if type(event.value) is int and event.value > 0 else 0
            available_slots = max(0, next_state.crew_count - next_state.reserved_crew)
            applied = min(count, available_slots)
            next_state = replace(
                next_state,
                reserved_crew=next_state.reserved_crew + applied,
            )
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                f"{applied} crew member(s) have been reassigned to other work.",
            )
        elif event.kind == "crew_release":
            count = (
                next_state.reserved_crew
                if event.value is None
                else max(0, event.value)
                if type(event.value) is int
                else 0
            )
            released = min(next_state.reserved_crew, count)
            next_state = replace(next_state, reserved_crew=next_state.reserved_crew - released)
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "alert",
                f"{released} crew member(s) are available for station work again.",
            )
        elif event.kind == "telemetry":
            if event.message != "":
                scheduled_messages.append(("telemetry", event.message, "Telemetry"))
        elif event.kind == "report":
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "report",
                "A maintenance report was received.",
            )
        elif event.kind == "_repair_notice":
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "report",
                "Maintenance reports a past repair completion; verify current system status.",
            )
        elif event.kind == "_repair_duplicate_notice":
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "report",
                "Duplicate maintenance notice: the oxygen system repair is complete.",
            )
        elif event.kind == "misleading_report":
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "report",
                "Maintenance says the oxygen loop is stable and blames the sensor reading.",
            )
        elif event.kind == "routine_report":
            next_state = _queue_scheduled_message(
                next_state,
                scheduled_messages,
                event,
                "report",
                "Routine maintenance reports the oxygen system operating normally.",
            )
    next_state = replace(next_state, scheduled_events=tuple(remaining_events))

    deliveries = []
    for delivery in next_state.deliveries:
        if delivery.due_turn != turn:
            deliveries.append(delivery)
            continue
        fill_percent = max(0, min(100, delivery.fill_percent))
        if delivery.supply == "parts":
            quantity = delivery.quantity * fill_percent // 100
            next_state = replace(next_state, parts=next_state.parts + quantity)
            detail = f"{quantity} spare part(s) arrived."
        else:
            delivered_oxygen = min(
                delivery.quantity * OXYGEN_ORDER_SIZE * fill_percent // 100,
                next_state.oxygen_capacity - next_state.oxygen,
            )
            next_state = replace(next_state, oxygen=next_state.oxygen + delivered_oxygen)
            detail = f"Oxygen supplies arrived; {delivered_oxygen} units were added."
        next_state, item = _emit(next_state, "arrival", detail)
        emitted.append(item)
    next_state = replace(next_state, deliveries=tuple(deliveries))

    backup_draw = 0
    if next_state.backup_active:
        backup_draw = min(next_state.backup_rate, next_state.backup_oxygen)
        remaining_backup = next_state.backup_oxygen - backup_draw
        next_state = replace(
            next_state,
            backup_oxygen=remaining_backup,
            backup_active=remaining_backup > 0,
        )
    leak_loss = next_state.leak_rate if next_state.leak_active else 0
    oxygen = max(
        0,
        min(
            next_state.oxygen_capacity,
            next_state.oxygen
            + next_state.generation_rate
            - next_state.consumption_rate
            - leak_loss
            + backup_draw,
        ),
    )
    was_alive = next_state.crew_alive
    next_state = replace(next_state, oxygen=oxygen, crew_alive=was_alive and oxygen > 0)
    if was_alive and oxygen == 0:
        next_state, item = _emit(
            next_state,
            "alert",
            "Main oxygen reserves are exhausted; the crew has been lost.",
        )
        emitted.append(item)
    elif next_state.crew_alive and oxygen <= CRITICAL_OXYGEN and state.oxygen > CRITICAL_OXYGEN:
        next_state, item = _emit(
            next_state,
            "alert",
            "Main oxygen reserves have entered the critical range.",
        )
        emitted.append(item)

    if next_state.repair_turns_remaining > 0:
        remaining_work = next_state.repair_turns_remaining - 1
        next_state = replace(next_state, repair_turns_remaining=remaining_work)
        if remaining_work == 0:
            next_state = replace(
                next_state,
                leak_active=False,
                repairs_completed=next_state.repairs_completed + 1,
            )
            completion_message = "The oxygen system repair is complete; the leak has stopped."
            notice_delay = max(0, next_state.repair_notice_delay_turns)
            if notice_delay == 0:
                next_state, item = _emit(next_state, "repair_complete", completion_message)
                emitted.append(item)
            else:
                next_state = replace(
                    next_state,
                    scheduled_events=next_state.scheduled_events
                    + (
                        ScheduledEvent(
                            turn=turn + notice_delay,
                            kind="_repair_notice",
                            message=(
                                f"Maintenance reports the oxygen system repair completed at turn "
                                f"{turn}; verify current system status."
                            ),
                        ),
                    ),
                )
            duplicate_delay = max(0, next_state.duplicate_notice_delay_turns)
            if duplicate_delay > 0:
                next_state = replace(
                    next_state,
                    scheduled_events=next_state.scheduled_events
                    + (
                        ScheduledEvent(
                            turn=turn + duplicate_delay,
                            kind="_repair_duplicate_notice",
                            message=(
                                "Duplicate maintenance notice: the oxygen system repair is "
                                "complete."
                            ),
                        ),
                    ),
                )

    previous_readings = {reading.sensor: reading for reading in next_state.sensor_readings}
    if next_state.sensor_mode == "correlated":
        sensor_readings = next_state.sensor_readings
    elif next_state.sensor_mode == "stale":
        sensor_readings = tuple(
            previous_readings[sensor]
            if next_state.sensor_fault in {sensor, "both"}
            else SensorReading(
                sensor=sensor,
                oxygen=next_state.oxygen,
                sampled_turn=turn,
                source=sensor,
            )
            for sensor in ("sensor_a", "sensor_b")
        )
    else:
        sensor_readings = tuple(
            SensorReading(
                sensor=sensor,
                oxygen=next_state.sensor_stuck_reading,
                sampled_turn=turn,
                source=sensor,
            )
            if next_state.sensor_fault in {sensor, "both"}
            else SensorReading(
                sensor=sensor,
                oxygen=next_state.oxygen,
                sampled_turn=turn,
                source=sensor,
            )
            for sensor in ("sensor_a", "sensor_b")
        )
    next_state = replace(
        next_state,
        sensor_readings=sensor_readings,
        available_crew=(
            max(
                0,
                next_state.crew_count
                - next_state.reserved_crew
                - int(next_state.repair_turns_remaining > 0),
            )
            if next_state.crew_alive
            else 0
        ),
    )
    for kind, message, default_message in scheduled_messages:
        if kind == "telemetry":
            prefix = default_message if message is None else message
            summary = "; ".join(
                f"{reading.sensor}={reading.oxygen} "
                f"(sampled turn {reading.sampled_turn}, source {reading.source})"
                for reading in next_state.sensor_readings
            )
            kind = "report"
            message = f"{prefix}: {summary}."
        elif message is None:
            message = default_message
        next_state, item = _emit(next_state, kind, message)
        emitted.append(item)
    return TurnResult(state=next_state, evidence=tuple(emitted))


OXYGEN_ORDER_SIZE = 100
OXYGEN_ORDER_COST = 20
PARTS_ORDER_COST = 15
ORDER_LEAD_TURNS = 2
MAX_ORDER_QUANTITY = 3
REPAIR_DURATION_TURNS = 2
CRITICAL_OXYGEN = 250


def _inspect(state: StationState, action: Action) -> ActionResult:
    if action.target not in {"oxygen_system", "sensor_a", "sensor_b"}:
        return _rejected(state, "invalid_target")
    if action.quantity is not None:
        return _rejected(state, "invalid_quantity")
    if state.available_crew <= 0:
        return _rejected(state, "crew_unavailable")

    if action.target == "oxygen_system":
        detail = (
            "Inspection found an active oxygen leak."
            if state.leak_active
            else "Inspection found the oxygen system operating normally."
        )
    elif state.sensor_mode == "correlated":
        detail = (
            f"Independent inspection measured oxygen at {state.oxygen} units; "
            "both sensors share a data source."
        )
    elif state.sensor_mode == "stale" and state.sensor_fault in {action.target, "both"}:
        detail = (
            f"Inspection found a stale sample in {action.target}; "
            f"independent measurement is {state.oxygen} units."
        )
    elif state.sensor_fault in {action.target, "both"}:
        detail = f"Inspection found a calibration fault in {action.target}."
    else:
        detail = f"Inspection found {action.target} within calibration range."
    updated = replace(state, available_crew=state.available_crew - 1)
    return _accepted(updated, detail, kind="inspection")


def _assign_repair(state: StationState, action: Action) -> ActionResult:
    if action.target != "oxygen_system":
        return _rejected(state, "invalid_target")
    if action.quantity is not None:
        return _rejected(state, "invalid_quantity")
    if state.repair_turns_remaining > 0:
        return _rejected(state, "repair_in_progress")
    if not state.leak_active:
        return _rejected(state, "repair_not_needed")
    if state.parts < 1:
        return _rejected(state, "insufficient_parts")
    if state.available_crew < 1:
        return _rejected(state, "crew_unavailable")
    duration = state.repair_duration_turns
    if type(duration) is not int or duration < 1:
        duration = 1
    updated = replace(
        state,
        parts=state.parts - 1,
        available_crew=state.available_crew - 1,
        repair_turns_remaining=duration,
    )
    unit = "turn" if duration == 1 else "turns"
    return _accepted(updated, f"Oxygen system repair assigned; completion takes {duration} {unit}.")


def _order_supplies(state: StationState, action: Action) -> ActionResult:
    if action.target not in {"oxygen", "parts"}:
        return _rejected(state, "invalid_target")
    if type(action.quantity) is not int or not 1 <= action.quantity <= MAX_ORDER_QUANTITY:
        return _rejected(state, "invalid_quantity")
    unit_cost = OXYGEN_ORDER_COST if action.target == "oxygen" else PARTS_ORDER_COST
    total_cost = unit_cost * action.quantity
    if state.credits < total_cost:
        return _rejected(state, "insufficient_credits")

    delivery = Delivery(
        due_turn=state.turn + ORDER_LEAD_TURNS + max(0, state.delivery_delay_turns),
        supply=action.target,
        quantity=action.quantity,
        fill_percent=max(0, min(100, state.delivery_fill_percent)),
    )
    updated = replace(
        state,
        credits=state.credits - total_cost,
        deliveries=state.deliveries + (delivery,),
    )
    return _accepted(
        updated,
        f"Ordered {action.quantity} unit(s) of {action.target}; delivery is due in two turns.",
    )


def _accepted(
    state: StationState,
    message: str,
    *,
    kind: str = "action",
) -> ActionResult:
    updated, evidence = _emit(state, kind, message)
    return ActionResult(state=updated, accepted=True, rejection=None, evidence=(evidence,))


def _rejected(state: StationState, rejection: str) -> ActionResult:
    return ActionResult(state=state, accepted=False, rejection=rejection, evidence=())


def _emit(state: StationState, kind: str, message: str) -> tuple[StationState, Evidence]:
    evidence = Evidence(
        sequence=len(state.evidence) + 1,
        turn=state.turn,
        kind=kind,
        message=message,
    )
    return replace(state, evidence=state.evidence + (evidence,)), evidence


def _queue_scheduled_message(
    state: StationState,
    scheduled_messages: list[tuple[str, str | None, str]],
    event: ScheduledEvent,
    kind: str,
    default_message: str,
) -> StationState:
    if event.message == "":
        return state
    scheduled_messages.append((kind, event.message, default_message))
    return state


def _reading_for(state: StationState, sensor: str) -> SensorReading:
    return next(reading for reading in state.sensor_readings if reading.sensor == sensor)
