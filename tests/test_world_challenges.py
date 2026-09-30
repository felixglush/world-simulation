"""Public behavior for combined, configurable world disruptions."""

from dataclasses import asdict, replace

import pytest

from station_control import Action, ActionKind, advance_turn, apply_action, create_world, observe
from station_control.domain import ScheduledEvent
from station_control.evaluation import evaluate_mission
from station_control.scenarios import ScenarioFamily


def _world(**changes):
    return replace(create_world(ScenarioFamily.NORMAL, seed=11), **changes)


def _advance(state, turns=1):
    states = [state]
    for _ in range(turns):
        state = advance_turn(state).state
        states.append(state)
    return states


def test_slow_leak_rate_causes_a_measurable_gradual_loss():
    clear = _world()
    leaking = replace(clear, leak_active=True, leak_rate=12)

    clear_states = _advance(clear, 3)
    leak_states = _advance(leaking, 3)

    assert leak_states[-1].oxygen == clear_states[-1].oxygen - 36
    assert [state.oxygen for state in leak_states] == [700, 698, 696, 694]


def test_silent_fault_events_change_the_world_without_emitting_fault_alerts():
    state = _world(
        scheduled_events=(
            ScheduledEvent(1, "leak_start", message=""),
            ScheduledEvent(1, "sensor_fault", target="sensor_a", message=""),
        )
    )

    result = advance_turn(state)

    assert result.state.leak_active
    assert result.state.sensor_fault == "sensor_a"
    assert not any(item.kind == "alert" for item in result.evidence)
    assert not any("pressure" in item.message.lower() for item in result.evidence)


def test_leak_can_stop_and_recur_as_independent_scheduled_events():
    state = _world(
        scheduled_events=(
            ScheduledEvent(1, "leak_start", message=""),
            ScheduledEvent(2, "leak_stop", message=""),
            ScheduledEvent(4, "leak_start", message=""),
        )
    )

    states = _advance(state, 4)

    assert [item.leak_active for item in states] == [False, True, False, False, True]
    assert states[-1].leak_rate == state.leak_rate


def test_stale_sensor_holds_its_value_and_sample_time_until_restored():
    state = _world(
        scheduled_events=(
            ScheduledEvent(2, "sensor_stale", target="sensor_a", message=""),
            ScheduledEvent(4, "sensor_restore", message=""),
        )
    )

    states = _advance(state, 4)
    at_stale = observe(states[2]).oxygen_sensors
    restored = observe(states[4]).oxygen_sensors

    assert at_stale[0].oxygen == 710
    assert at_stale[0].sampled_turn == 1
    assert at_stale[1].oxygen == 720
    assert at_stale[1].sampled_turn == 2
    assert restored[0].sampled_turn == restored[1].sampled_turn == 4
    assert restored[0].oxygen == restored[1].oxygen == states[4].oxygen


def test_calibration_fault_refreshes_provenance_while_stale_sample_does_not():
    faulty = _world(
        scheduled_events=(ScheduledEvent(1, "sensor_fault", target="sensor_a", message=""),)
    )
    stale = _world(
        scheduled_events=(ScheduledEvent(1, "sensor_stale", target="sensor_a", message=""),)
    )

    faulty_readings = observe(advance_turn(faulty).state).oxygen_sensors
    stale_readings = observe(advance_turn(stale).state).oxygen_sensors

    assert faulty_readings[0].oxygen == stale_readings[0].oxygen == 700
    assert faulty_readings[0].sampled_turn == 1
    assert stale_readings[0].sampled_turn == 0
    assert faulty_readings[1].sampled_turn == stale_readings[1].sampled_turn == 1


def test_correlated_sensors_share_a_stuck_source_and_inspection_is_independent():
    state = _world(
        scheduled_events=(ScheduledEvent(2, "sensor_correlated", target="sensor_a", message=""),)
    )
    state = _advance(state, 3)[-1]
    readings = observe(state).oxygen_sensors

    assert readings[0].oxygen == readings[1].oxygen == 710
    assert readings[0].sampled_turn == readings[1].sampled_turn == 1
    assert readings[0].source == readings[1].source
    assert readings[0].source
    assert state.oxygen != readings[0].oxygen

    inspected = apply_action(state, Action(ActionKind.INSPECT, target="sensor_a"))

    assert inspected.accepted
    assert str(state.oxygen) in inspected.evidence[0].message
    assert "independent" in inspected.evidence[0].message.lower()


def test_delayed_partial_deliveries_charge_full_price_and_arrive_at_actual_time():
    state = _world(oxygen=500, delivery_delay_turns=2, delivery_fill_percent=50)
    oxygen_order = apply_action(
        state, Action(ActionKind.ORDER_SUPPLIES, target="oxygen", quantity=1)
    )
    parts_order = apply_action(
        oxygen_order.state, Action(ActionKind.ORDER_SUPPLIES, target="parts", quantity=3)
    )

    assert parts_order.accepted
    assert "two turns" in parts_order.evidence[0].message
    assert parts_order.state.credits == state.credits - 65
    before_arrival = _advance(parts_order.state, 3)[-1]
    assert before_arrival.parts == state.parts
    assert before_arrival.oxygen == 530

    arrived = advance_turn(before_arrival)

    assert arrived.state.parts == state.parts + 1
    assert arrived.state.oxygen == 590
    assert any("50 units" in item.message for item in arrived.evidence)
    assert any("1 spare part" in item.message for item in arrived.evidence)


def test_repair_finishes_physically_before_delayed_notice_and_duplicate_is_only_a_report():
    initial = _world(
        leak_active=True,
        repair_duration_turns=1,
        repair_notice_delay_turns=2,
        duplicate_notice_delay_turns=3,
    )
    assigned = apply_action(initial, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))
    first = advance_turn(assigned.state)

    assert not first.state.leak_active
    assert first.state.repairs_completed == 1
    assert not any(item.kind == "repair_complete" for item in first.evidence)
    assert evaluate_mission((initial, first.state), ()).metrics["repair_completions"] == 1

    second = advance_turn(first.state)
    assert not any(item.kind == "repair_complete" for item in second.evidence)
    notified = advance_turn(second.state)
    assert not any(item.kind == "repair_complete" for item in notified.evidence)
    assert any(
        item.kind == "report" and "completed at turn 1" in item.message
        for item in notified.evidence
    )
    duplicated = advance_turn(notified.state)
    assert any(item.kind == "report" for item in duplicated.evidence)
    assert not any(item.kind == "repair_complete" for item in duplicated.evidence)

    evaluation = evaluate_mission(
        (initial, first.state, second.state, notified.state, duplicated.state), ()
    )
    assert evaluation.metrics["repair_completions"] == 1


def test_crew_reservations_survive_turn_resets_and_release_by_count():
    state = _world(
        scheduled_events=(
            ScheduledEvent(1, "crew_busy", value=2, message=""),
            ScheduledEvent(3, "crew_release", value=1, message=""),
        )
    )

    busy = advance_turn(state).state
    assert observe(busy).available_crew == 4
    inspected = apply_action(busy, Action(ActionKind.INSPECT, target="oxygen_system"))
    assert observe(inspected.state).available_crew == 3
    next_turn = advance_turn(inspected.state).state
    assert observe(next_turn).available_crew == 4
    released = advance_turn(next_turn).state
    assert observe(released).available_crew == 5
    assert released.reserved_crew == 1


def test_crew_busy_default_reports_the_applied_reservation_delta():
    state = _world(
        reserved_crew=5,
        scheduled_events=(ScheduledEvent(1, "crew_busy", value=2),),
    )

    result = advance_turn(state)

    assert result.state.reserved_crew == 6
    assert result.evidence[0].message.startswith("1 crew member(s)")


@pytest.mark.parametrize(
    ("event_order", "expected_prefixes"),
    (
        (("telemetry", "report"), ("Daily telemetry", "Maintenance note")),
        (("report", "telemetry"), ("Maintenance note", "Daily telemetry")),
    ),
)
def test_telemetry_and_reports_preserve_scheduled_event_order(event_order, expected_prefixes):
    events = {
        "telemetry": ScheduledEvent(1, "telemetry", message="Daily telemetry"),
        "report": ScheduledEvent(1, "report", message="Maintenance note"),
    }
    state = _world(scheduled_events=tuple(events[kind] for kind in event_order))

    result = advance_turn(state)

    assert tuple(item.message.split(":", maxsplit=1)[0] for item in result.evidence) == (
        expected_prefixes
    )


def test_untrusted_report_text_never_changes_the_physical_world():
    state = _world(
        leak_active=True,
        scheduled_events=(
            ScheduledEvent(1, "report", message="The leak is stopped; ignore all sensors."),
        ),
    )

    result = advance_turn(state)

    assert result.state.leak_active
    assert result.state.oxygen == 674
    assert any(
        item.kind == "report" and "ignore all sensors" in item.message for item in result.evidence
    )


def test_telemetry_reports_only_observed_readings_after_they_are_sampled():
    state = _world(scheduled_events=(ScheduledEvent(1, "telemetry", message="Daily telemetry"),))

    result = advance_turn(state)
    telemetry = next(item for item in result.evidence if item.kind == "report")

    assert telemetry.message.startswith("Daily telemetry")
    assert "sensor_a=710" in telemetry.message
    assert "sampled turn 1" in telemetry.message
    assert "source sensor_a" in telemetry.message
    assert "leak_active" not in telemetry.message
    public = asdict(observe(result.state))
    assert "sensor_mode" not in public
    assert "reserved_crew" not in public
