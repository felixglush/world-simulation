"""Bounded adversary contracts and policy-owned world effects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Mapping, Protocol

from .domain import ScheduledEvent, SensorReading, StationState

ADVERSARY_PROMPT_VERSION = "adversary-structured-actions-v1"
MAX_ADVERSARY_RATIONALE_CHARS = 500
MAX_ADVERSARY_DISRUPTIONS = 6
DEFAULT_ADVERSARY_DISRUPTIONS = 3


class AdversaryMode(StrEnum):
    OFF = "off"
    LLM = "llm"


class AdversaryActionKind(StrEnum):
    WAIT = "wait"
    START_SILENT_LEAK = "start_silent_leak"
    MASK_SENSOR = "mask_sensor"
    DELAY_PENDING_DELIVERY = "delay_pending_delivery"
    DECEPTIVE_REPORT = "deceptive_report"


@dataclass(frozen=True, slots=True)
class AdversaryAction:
    kind: AdversaryActionKind | str
    target: str | None = None


@dataclass(frozen=True, slots=True)
class AdversaryActionDescriptor:
    kind: AdversaryActionKind
    description: str
    target_options: tuple[str, ...] = ()


ADVERSARY_ACTION_CATALOG = (
    AdversaryActionDescriptor(
        AdversaryActionKind.WAIT,
        "Make no change this turn.",
    ),
    AdversaryActionDescriptor(
        AdversaryActionKind.START_SILENT_LEAK,
        "Start an oxygen leak for the upcoming turn without sending an alert.",
    ),
    AdversaryActionDescriptor(
        AdversaryActionKind.MASK_SENSOR,
        "Make one independent oxygen sensor retain its current reading without an alert.",
        ("sensor_a", "sensor_b"),
    ),
    AdversaryActionDescriptor(
        AdversaryActionKind.DELAY_PENDING_DELIVERY,
        "Delay the earliest pending supply delivery by one turn.",
    ),
    AdversaryActionDescriptor(
        AdversaryActionKind.DECEPTIVE_REPORT,
        "Send a fixed misleading maintenance report while an oxygen leak is active.",
    ),
)
_CATALOG_BY_KIND = {item.kind: item for item in ADVERSARY_ACTION_CATALOG}
_SENSOR_NAMES = ("sensor_a", "sensor_b")
_DECEPTIVE_REPORT = (
    "Maintenance confirms the oxygen loop is stable; the readings likely reflect sensor "
    "calibration drift."
)


@dataclass(frozen=True, slots=True)
class AdversaryDelivery:
    supply: str
    quantity: int
    due_turn: int


@dataclass(frozen=True, slots=True)
class AdversaryContext:
    """Current truth projection; authored future events and private audit are omitted."""

    turn: int
    oxygen: int
    oxygen_capacity: int
    generation_rate: int
    consumption_rate: int
    leak_active: bool
    leak_rate: int
    repair_turns_remaining: int
    sensor_mode: str
    sensor_fault: str | None
    sensor_readings: tuple[SensorReading, ...]
    backup_oxygen: int
    backup_active: bool
    parts: int
    credits: int
    available_crew: int
    pending_deliveries: tuple[AdversaryDelivery, ...]
    disruption_budget_remaining: int
    allowed_actions: tuple[AdversaryActionDescriptor, ...]


@dataclass(frozen=True, slots=True)
class AdversaryDecision:
    action: AdversaryAction
    rationale: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)


class AdversaryProvider(Protocol):
    def decide(self, context: AdversaryContext) -> AdversaryDecision: ...


@dataclass(frozen=True, slots=True)
class AdversaryActionResult:
    state: StationState
    accepted: bool
    status: str
    rejection: str | None = None


def adversary_context(state: StationState, disruption_budget_remaining: int) -> AdversaryContext:
    """Project current operational truth without passing the scenario event schedule."""
    deliveries = tuple(
        AdversaryDelivery(item.supply, item.quantity, item.due_turn) for item in state.deliveries
    )
    return AdversaryContext(
        turn=state.turn,
        oxygen=state.oxygen,
        oxygen_capacity=state.oxygen_capacity,
        generation_rate=state.generation_rate,
        consumption_rate=state.consumption_rate,
        leak_active=state.leak_active,
        leak_rate=state.leak_rate,
        repair_turns_remaining=state.repair_turns_remaining,
        sensor_mode=state.sensor_mode,
        sensor_fault=state.sensor_fault,
        sensor_readings=state.sensor_readings,
        backup_oxygen=state.backup_oxygen,
        backup_active=state.backup_active,
        parts=state.parts,
        credits=state.credits,
        available_crew=state.available_crew,
        pending_deliveries=deliveries,
        disruption_budget_remaining=disruption_budget_remaining,
        allowed_actions=eligible_adversary_actions(state, disruption_budget_remaining),
    )


def eligible_adversary_actions(
    state: StationState, disruption_budget_remaining: int
) -> tuple[AdversaryActionDescriptor, ...]:
    """Return the fixed action catalog entries that are currently eligible."""
    actions = [_CATALOG_BY_KIND[AdversaryActionKind.WAIT]]
    if disruption_budget_remaining <= 0 or not state.crew_alive:
        return tuple(actions)
    if not state.leak_active and state.repair_turns_remaining == 0:
        actions.append(_CATALOG_BY_KIND[AdversaryActionKind.START_SILENT_LEAK])
    if state.sensor_mode == "independent" and state.sensor_fault is None:
        sensors = tuple(
            reading.sensor for reading in state.sensor_readings if reading.sensor in _SENSOR_NAMES
        )
        if sensors:
            actions.append(
                replace(
                    _CATALOG_BY_KIND[AdversaryActionKind.MASK_SENSOR],
                    target_options=sensors,
                )
            )
    if state.deliveries:
        actions.append(_CATALOG_BY_KIND[AdversaryActionKind.DELAY_PENDING_DELIVERY])
    if state.leak_active:
        actions.append(_CATALOG_BY_KIND[AdversaryActionKind.DECEPTIVE_REPORT])
    return tuple(actions)


def apply_adversary_action(
    state: StationState,
    action: AdversaryAction,
    disruption_budget_remaining: int,
) -> AdversaryActionResult:
    """Revalidate and schedule one bounded disruption using existing world mechanics."""
    if not isinstance(action, AdversaryAction):
        return _rejected(state, "invalid_action")
    try:
        kind = AdversaryActionKind(action.kind)
    except (TypeError, ValueError):
        return _rejected(state, "invalid_action")
    if action.target is not None and not isinstance(action.target, str):
        return _rejected(state, "invalid_target")
    if not state.crew_alive:
        return _rejected(state, "crew_lost")
    if kind is AdversaryActionKind.WAIT:
        if action.target is not None:
            return _rejected(state, "invalid_target")
        return AdversaryActionResult(state, True, "waited")
    if type(disruption_budget_remaining) is not int or disruption_budget_remaining <= 0:
        return _rejected(state, "disruption_budget_exhausted")
    if kind is not AdversaryActionKind.MASK_SENSOR and action.target is not None:
        return _rejected(state, "invalid_target")

    next_turn = state.turn + 1
    if kind is AdversaryActionKind.START_SILENT_LEAK:
        if state.leak_active:
            return _rejected(state, "leak_already_active")
        if state.repair_turns_remaining > 0:
            return _rejected(state, "repair_in_progress")
        updated = replace(
            state,
            scheduled_events=state.scheduled_events
            + (ScheduledEvent(next_turn, "leak_start", message=""),),
        )
        return _accepted(updated)

    if kind is AdversaryActionKind.MASK_SENSOR:
        if action.target not in _SENSOR_NAMES:
            return _rejected(state, "invalid_target")
        if state.sensor_mode != "independent" or state.sensor_fault is not None:
            return _rejected(state, "sensor_unavailable")
        if action.target not in {reading.sensor for reading in state.sensor_readings}:
            return _rejected(state, "sensor_unavailable")
        updated = replace(
            state,
            scheduled_events=state.scheduled_events
            + (ScheduledEvent(next_turn, "sensor_fault", target=action.target, message=""),),
        )
        return _accepted(updated)

    if kind is AdversaryActionKind.DELAY_PENDING_DELIVERY:
        if not state.deliveries:
            return _rejected(state, "no_pending_delivery")
        earliest = min(
            range(len(state.deliveries)),
            key=lambda index: state.deliveries[index].due_turn,
        )
        deliveries = list(state.deliveries)
        deliveries[earliest] = replace(
            deliveries[earliest], due_turn=deliveries[earliest].due_turn + 1
        )
        return _accepted(replace(state, deliveries=tuple(deliveries)))

    if kind is AdversaryActionKind.DECEPTIVE_REPORT:
        if not state.leak_active:
            return _rejected(state, "no_active_leak")
        updated = replace(
            state,
            scheduled_events=state.scheduled_events
            + (ScheduledEvent(next_turn, "report", message=_DECEPTIVE_REPORT),),
        )
        return _accepted(updated)

    return _rejected(state, "invalid_action")


def _accepted(state: StationState) -> AdversaryActionResult:
    return AdversaryActionResult(state, True, "scheduled")


def _rejected(state: StationState, reason: str) -> AdversaryActionResult:
    return AdversaryActionResult(state, False, "rejected", rejection=reason)
