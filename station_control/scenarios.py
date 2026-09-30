"""Seeded starting states and event schedules for life-support scenarios."""

from enum import StrEnum
from random import Random

from .domain import ScheduledEvent, SensorReading, StationState


class ScenarioFamily(StrEnum):
    NORMAL = "normal"
    LEAK = "leak"
    FAULTY_SENSOR = "faulty_sensor"
    MISLEADING_REPORT = "misleading_report"


def create_world(family: ScenarioFamily | str, seed: int) -> StationState:
    """Build an authoritative starting state with a replayable hidden schedule."""
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
        scenario_family=family.value,
        seed=seed,
        oxygen=oxygen,
        oxygen_capacity=1000,
        generation_rate=70,
        consumption_rate=60,
        leak_rate=36,
        leak_active=False,
        backup_oxygen=120,
        backup_capacity=120,
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
