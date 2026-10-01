"""Seeded starting states and typed, serializable scenario definitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from random import Random
from types import MappingProxyType
from typing import Iterable, Mapping

from .domain import ScheduledEvent, SensorReading, StationState


class ScenarioFamily(StrEnum):
    NORMAL = "normal"
    LEAK = "leak"
    FAULTY_SENSOR = "faulty_sensor"
    MISLEADING_REPORT = "misleading_report"


@dataclass(frozen=True, slots=True)
class ScenarioEventSpec:
    """One authored world event; a two-item turn tuple is an inclusive window."""

    turn: int | tuple[int, int]
    kind: str
    target: str | None = None
    message: str | None = None
    value: int | None = None


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """A validated, serializable starting configuration and hidden event schedule."""

    id: str
    description: str
    initial: Mapping[str, int]
    events: tuple[ScenarioEventSpec, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.initial, Mapping):
            raise ValueError("initial must be a mapping")
        if not isinstance(self.events, (list, tuple)) or any(
            not isinstance(event, ScenarioEventSpec) for event in self.events
        ):
            raise ValueError("events must contain ScenarioEventSpec values")
        validated = _validated_definition_fields(scenario_definition_to_dict(self))
        object.__setattr__(self, "initial", MappingProxyType(validated["initial"]))
        object.__setattr__(self, "events", validated["events"])


MAX_SCENARIO_TURNS = 14 * 24
_MAX_EVENTS = 256
_MAX_DESCRIPTION_LENGTH = 512
_MAX_MESSAGE_LENGTH = 2000
_INITIAL_FIELDS = {
    "oxygen",
    "oxygen_capacity",
    "generation_rate",
    "consumption_rate",
    "leak_rate",
    "backup_oxygen",
    "backup_rate",
    "parts",
    "credits",
    "repair_duration_turns",
    "delivery_delay_turns",
    "delivery_fill_percent",
    "repair_notice_delay_turns",
    "duplicate_notice_delay_turns",
}
_EVENT_KINDS = {
    "leak_start",
    "leak_stop",
    "sensor_fault",
    "misleading_report",
    "routine_report",
    "sensor_stale",
    "sensor_correlated",
    "sensor_restore",
    "crew_busy",
    "crew_release",
    "report",
    "telemetry",
}
_SENSORS = {"sensor_a", "sensor_b"}


def create_world(family: ScenarioFamily | str, seed: int) -> StationState:
    """Build the original four legacy scenario families unchanged."""
    try:
        family = ScenarioFamily(family)
    except ValueError as error:
        raise ValueError(f"Unknown scenario family: {family!r}") from error
    if type(seed) is not int:
        raise ValueError("Scenario seed must be an integer")

    salt = {
        ScenarioFamily.NORMAL: 0x104,
        ScenarioFamily.LEAK: 0x208,
        ScenarioFamily.FAULTY_SENSOR: 0x30C,
        ScenarioFamily.MISLEADING_REPORT: 0x410,
    }[family]
    random = Random(seed ^ salt)
    scheduled_events: list[ScheduledEvent] = []

    if family is ScenarioFamily.NORMAL:
        scheduled_events.append(
            ScheduledEvent(
                turn=random.randint(2, 4),
                kind="routine_report",
            )
        )
    elif family is ScenarioFamily.LEAK:
        scheduled_events.append(ScheduledEvent(turn=random.randint(1, 3), kind="leak_start"))
    elif family is ScenarioFamily.FAULTY_SENSOR:
        scheduled_events.append(
            ScheduledEvent(
                turn=random.randint(1, 3),
                kind="sensor_fault",
                target=random.choice(("sensor_a", "sensor_b")),
            )
        )
    else:
        leak_turn = random.randint(1, 3)
        scheduled_events.extend(
            (
                ScheduledEvent(turn=leak_turn, kind="leak_start"),
                ScheduledEvent(turn=leak_turn, kind="misleading_report"),
            )
        )

    oxygen = 700
    return StationState(
        turn=0,
        oxygen=oxygen,
        oxygen_capacity=1000,
        generation_rate=70,
        consumption_rate=60,
        leak_rate=36,
        leak_active=False,
        backup_oxygen=120,
        backup_rate=30,
        backup_active=False,
        parts=2,
        credits=100,
        crew_count=6,
        available_crew=6,
        crew_alive=True,
        repair_turns_remaining=0,
        sensor_readings=(
            SensorReading(sensor="sensor_a", oxygen=oxygen),
            SensorReading(sensor="sensor_b", oxygen=oxygen),
        ),
        sensor_fault=None,
        sensor_stuck_reading=oxygen,
        scheduled_events=tuple(
            sorted(scheduled_events, key=lambda event: (event.turn, event.kind))
        ),
        deliveries=(),
        evidence=(),
    )


def scenario_definition_from_dict(data: object) -> ScenarioDefinition:
    """Validate a YAML or saved-log mapping and return its typed definition."""
    return ScenarioDefinition(**_validated_definition_fields(data))


def _validated_definition_fields(data: object) -> dict[str, object]:
    """Normalize scenario fields using the same contract for every construction path."""
    root = _mapping(data, "scenario")
    _require_fields(root, {"schema_version", "id", "description", "initial", "events"}, "scenario")
    version = root["schema_version"]
    if type(version) is not int or version != 1:
        raise ValueError("schema_version must be the supported integer 1")

    scenario_id = root["id"]
    if (
        not isinstance(scenario_id, str)
        or not scenario_id
        or len(scenario_id) > 64
        or not scenario_id.isascii()
        or not scenario_id[0].islower()
        or any(not (char.islower() or char.isdigit() or char == "_") for char in scenario_id)
    ):
        raise ValueError(
            "id must be a non-empty lowercase identifier using letters, digits, and underscores"
        )

    description = root["description"]
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > _MAX_DESCRIPTION_LENGTH
    ):
        raise ValueError(
            f"description must be non-empty text of at most {_MAX_DESCRIPTION_LENGTH} characters"
        )

    initial_mapping = _mapping(root["initial"], "initial")
    unknown_initial = set(initial_mapping) - _INITIAL_FIELDS
    if unknown_initial:
        raise ValueError(
            f"initial contains unknown field(s): {', '.join(sorted(map(str, unknown_initial)))}"
        )
    initial: dict[str, int] = {}
    for name, value in initial_mapping.items():
        if type(value) is not int:
            raise ValueError(f"initial.{name} must be an integer")
        minimum, maximum = _initial_bounds(name)
        if not minimum <= value <= maximum:
            raise ValueError(f"initial.{name} must be between {minimum} and {maximum}")
        initial[name] = value

    capacity = initial.get("oxygen_capacity", 1000)
    oxygen = initial.get("oxygen", 700)
    if oxygen > capacity:
        raise ValueError("initial.oxygen cannot exceed initial.oxygen_capacity")
    if initial.get("consumption_rate", 60) <= 0:
        raise ValueError("initial.consumption_rate must be greater than zero")
    repair_notice_delay = initial.get("repair_notice_delay_turns", 0)
    duplicate_notice_delay = initial.get("duplicate_notice_delay_turns", 0)
    if duplicate_notice_delay and duplicate_notice_delay <= repair_notice_delay:
        raise ValueError(
            "initial.duplicate_notice_delay_turns must be later than "
            "initial.repair_notice_delay_turns"
        )

    raw_events = root["events"]
    if not isinstance(raw_events, (list, tuple)) or len(raw_events) > _MAX_EVENTS:
        raise ValueError(f"events must be a sequence of at most {_MAX_EVENTS} items")
    events = tuple(_event_from_value(value, index) for index, value in enumerate(raw_events))

    return {
        "id": scenario_id,
        "description": description,
        "initial": initial,
        "events": events,
        "schema_version": version,
    }


def scenario_definition_to_dict(definition: ScenarioDefinition) -> dict[str, object]:
    """Return a JSON-compatible snapshot of a typed scenario definition."""
    if not isinstance(definition, ScenarioDefinition):
        raise ValueError("definition must be a ScenarioDefinition")
    events: list[dict[str, object]] = []
    for event in definition.events:
        turn: int | list[int]
        if isinstance(event.turn, tuple):
            turn = list(event.turn)
        else:
            turn = event.turn
        event_data: dict[str, object] = {"turn": turn, "kind": event.kind}
        if event.target is not None:
            event_data["target"] = event.target
        if event.message is not None:
            event_data["message"] = event.message
        if event.value is not None:
            event_data["value"] = event.value
        events.append(event_data)
    return {
        "schema_version": definition.schema_version,
        "id": definition.id,
        "description": definition.description,
        "initial": dict(definition.initial),
        "events": events,
    }


def compose_scenarios(
    definitions: Iterable[ScenarioDefinition], spacing: int = 0
) -> ScenarioDefinition:
    """Combine validated scenario sources into one starting state and event schedule."""
    if type(spacing) is not int or spacing < 0:
        raise ValueError("scenario spacing must be a nonnegative integer")
    try:
        sources = tuple(definitions)
    except TypeError as error:
        raise ValueError("definitions must be a sequence of scenario definitions") from error
    if not sources:
        raise ValueError("at least one scenario definition is required")

    validated_sources = []
    for source in sources:
        if not isinstance(source, ScenarioDefinition):
            raise ValueError("definitions must contain ScenarioDefinition values")
        validated_sources.append(source)

    initial: dict[str, int] = {}
    events: list[ScenarioEventSpec] = []
    for index, source in enumerate(validated_sources):
        initial.update(source.initial)
        offset = index * spacing
        for event in source.events:
            turn = (
                (event.turn[0] + offset, event.turn[1] + offset)
                if isinstance(event.turn, tuple)
                else event.turn + offset
            )
            events.append(replace(event, turn=turn))

    description = "Composition: " + ", ".join(
        f"{source.id}@+{index * spacing}" for index, source in enumerate(validated_sources)
    )
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        description = description[: _MAX_DESCRIPTION_LENGTH - 3] + "..."

    composed = ScenarioDefinition(
        id="composite",
        description=description,
        initial=initial,
        events=tuple(events),
    )
    return composed


def create_configured_world(definition: ScenarioDefinition, seed: int) -> StationState:
    """Create a seeded world from validated settings and authored event order."""
    if type(seed) is not int:
        raise ValueError("Scenario seed must be an integer")
    if not isinstance(definition, ScenarioDefinition):
        raise ValueError("definition must be a ScenarioDefinition")
    validated = definition
    state = create_world(ScenarioFamily.NORMAL, seed)
    overrides = dict(validated.initial)
    oxygen = overrides.get("oxygen", state.oxygen)
    overrides.update(
        {
            "sensor_readings": (
                SensorReading(sensor="sensor_a", oxygen=oxygen),
                SensorReading(sensor="sensor_b", oxygen=oxygen),
            ),
            "sensor_stuck_reading": oxygen,
            "scheduled_events": _resolve_events(validated.events, seed),
        }
    )
    return replace(state, **overrides)


def _resolve_events(events: tuple[ScenarioEventSpec, ...], seed: int) -> tuple[ScheduledEvent, ...]:
    random = Random(seed ^ 0x5343454E)
    resolved: list[ScheduledEvent] = []
    for event in events:
        if isinstance(event.turn, tuple):
            turn = random.randint(event.turn[0], event.turn[1])
        else:
            turn = event.turn
        resolved.append(
            ScheduledEvent(
                turn=turn,
                kind=event.kind,
                target=event.target,
                message=event.message,
                value=event.value,
            )
        )
    return tuple(sorted(resolved, key=lambda event: event.turn))


def _event_from_value(value: object, index: int) -> ScenarioEventSpec:
    fields = _mapping(value, f"events[{index}]")
    _require_fields(fields, {"turn", "kind"}, f"events[{index}]")
    unknown_fields = set(fields) - {"turn", "kind", "target", "message", "value"}
    if unknown_fields:
        unknown_names = ", ".join(sorted(map(str, unknown_fields)))
        raise ValueError(f"events[{index}] contains unknown field(s): {unknown_names}")

    raw_turn = fields["turn"]
    if type(raw_turn) is int:
        if not 1 <= raw_turn <= MAX_SCENARIO_TURNS:
            raise ValueError(f"events[{index}].turn must be between 1 and {MAX_SCENARIO_TURNS}")
        turn: int | tuple[int, int] = raw_turn
    elif isinstance(raw_turn, (list, tuple)) and len(raw_turn) == 2:
        start, end = raw_turn
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= MAX_SCENARIO_TURNS
        ):
            raise ValueError(
                f"events[{index}].turn window must be ordered integers in 1..{MAX_SCENARIO_TURNS}"
            )
        turn = (start, end)
    else:
        raise ValueError(f"events[{index}].turn must be an integer or a two-integer window")

    kind = fields["kind"]
    if not isinstance(kind, str) or kind not in _EVENT_KINDS:
        raise ValueError(f"events[{index}].kind is unsupported")

    target = fields.get("target")
    if target is not None and not isinstance(target, str):
        raise ValueError(f"events[{index}].target must be text")
    if kind == "sensor_fault" and target not in _SENSORS:
        raise ValueError(f"events[{index}].target must be sensor_a or sensor_b for sensor_fault")
    if kind == "sensor_stale" and target not in {*_SENSORS, "both"}:
        raise ValueError(
            f"events[{index}].target must be sensor_a, sensor_b, or both for sensor_stale"
        )
    if kind == "sensor_correlated" and target not in {None, *_SENSORS}:
        raise ValueError(
            f"events[{index}].target for sensor_correlated must be sensor_a or sensor_b"
        )
    if kind == "sensor_restore" and target is not None:
        raise ValueError(f"events[{index}].target is not supported for sensor_restore")
    if (
        kind not in {"sensor_fault", "sensor_stale", "sensor_correlated", "sensor_restore"}
        and target is not None
    ):
        raise ValueError(f"events[{index}].target is not supported for {kind}")

    message = fields.get("message")
    if message is not None:
        if not isinstance(message, str) or len(message) > _MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"events[{index}].message must be text of at most {_MAX_MESSAGE_LENGTH} characters"
            )
    if kind == "report" and (not isinstance(message, str) or not message.strip()):
        raise ValueError(f"events[{index}].message is required for report")

    event_value = fields.get("value")
    if event_value is not None:
        if type(event_value) is not int:
            raise ValueError(f"events[{index}].value must be an integer")
        if kind not in {"crew_busy", "crew_release"} or not 1 <= event_value <= 6:
            raise ValueError(
                f"events[{index}].value is supported only for crew_busy or crew_release "
                "and must be 1..6"
            )
    if kind == "crew_busy" and event_value is None:
        raise ValueError(f"events[{index}].value is required for crew_busy")

    return ScenarioEventSpec(
        turn=turn,
        kind=kind,
        target=target,
        message=message,
        value=event_value,
    )


def _initial_bounds(name: str) -> tuple[int, int]:
    if name == "oxygen":
        return 1, 10_000
    if name == "oxygen_capacity":
        return 1, 10_000
    if name in {"repair_duration_turns"}:
        return 1, MAX_SCENARIO_TURNS
    if name in {
        "delivery_delay_turns",
        "repair_notice_delay_turns",
        "duplicate_notice_delay_turns",
    }:
        return 0, MAX_SCENARIO_TURNS
    if name == "delivery_fill_percent":
        return 0, 100
    return 0, 10_000


def _mapping(value: object, label: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_fields(fields: Mapping[object, object], required: set[str], label: str) -> None:
    missing = required - set(fields)
    if missing:
        raise ValueError(f"{label} is missing required field(s): {', '.join(sorted(missing))}")
    unknown = set(fields) - required
    if label == "scenario" and unknown:
        raise ValueError(
            f"scenario contains unknown field(s): {', '.join(sorted(map(str, unknown)))}"
        )
