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


@dataclass(frozen=True, slots=True)
class Delivery:
    due_turn: int
    supply: str
    quantity: int


@dataclass(frozen=True, slots=True)
class StationState:
    """Authoritative world state; pass `observe(state)` to decision makers."""

    turn: int
    scenario_family: str
    seed: int
    oxygen: int
    oxygen_capacity: int
    generation_rate: int
    consumption_rate: int
    leak_rate: int
    leak_active: bool
    backup_oxygen: int
    backup_capacity: int
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

    remaining_events = []
    for event in state.scheduled_events:
        if event.turn != turn:
            remaining_events.append(event)
            continue
        if event.kind == "leak_start":
            next_state = replace(next_state, leak_active=True)
            next_state, item = _emit(
                next_state,
                "alert",
                "Life-support pressure has dropped below its normal range.",
            )
            emitted.append(item)
        elif event.kind == "sensor_fault":
            next_state = replace(next_state, sensor_fault=event.target)
            next_state, item = _emit(
                next_state,
                "alert",
                "The independent oxygen sensors are reporting different levels.",
            )
            emitted.append(item)
        elif event.kind == "misleading_report":
            next_state, item = _emit(
                next_state,
                "report",
                "Maintenance says the oxygen loop is stable and blames the sensor reading.",
            )
            emitted.append(item)
        elif event.kind == "routine_report":
            next_state, item = _emit(
                next_state,
                "report",
                "Routine maintenance reports the oxygen system operating normally.",
            )
            emitted.append(item)
    next_state = replace(next_state, scheduled_events=tuple(remaining_events))

    deliveries = []
    for delivery in next_state.deliveries:
        if delivery.due_turn != turn:
            deliveries.append(delivery)
            continue
        if delivery.supply == "parts":
            next_state = replace(next_state, parts=next_state.parts + delivery.quantity)
            detail = f"{delivery.quantity} spare part(s) arrived."
        else:
            delivered_oxygen = min(
                delivery.quantity * OXYGEN_ORDER_SIZE,
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
            next_state = replace(next_state, leak_active=False)
            next_state, item = _emit(
                next_state,
                "repair_complete",
                "The oxygen system repair is complete; the leak has stopped.",
            )
            emitted.append(item)

    sensor_readings = tuple(
        SensorReading(
            sensor=sensor,
            oxygen=(
                next_state.sensor_stuck_reading
                if next_state.sensor_fault == sensor
                else next_state.oxygen
            ),
        )
        for sensor in ("sensor_a", "sensor_b")
    )
    next_state = replace(
        next_state,
        sensor_readings=sensor_readings,
        available_crew=(
            next_state.crew_count - int(next_state.repair_turns_remaining > 0)
            if next_state.crew_alive
            else 0
        ),
    )
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
    elif state.sensor_fault == action.target:
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
    updated = replace(
        state,
        parts=state.parts - 1,
        available_crew=state.available_crew - 1,
        repair_turns_remaining=REPAIR_DURATION_TURNS,
    )
    return _accepted(updated, "Oxygen system repair assigned; completion takes two turns.")


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
        due_turn=state.turn + ORDER_LEAD_TURNS,
        supply=action.target,
        quantity=action.quantity,
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
