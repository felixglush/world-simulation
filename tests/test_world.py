"""Behavioral contract for the deterministic life-support world boundary."""

from dataclasses import asdict, replace

import pytest

from station_control import (
    Action,
    ActionKind,
    ScenarioFamily,
    advance_turn,
    apply_action,
    create_world,
    observe,
)


@pytest.mark.parametrize("family", tuple(ScenarioFamily))
def test_seeded_scenario_is_replayable_and_observation_hides_world_truth(family):
    first = create_world(family, seed=27)
    replay = create_world(family, seed=27)

    assert first == replay
    observation = observe(first)
    assert observation == observe(replay)
    assert len(observation.oxygen_sensors) == 2
    assert observation.available_crew == 6
    observed_fields = asdict(observation)
    assert "scenario_family" not in observed_fields
    assert "leak_active" not in observed_fields
    assert "sensor_fault" not in observed_fields
    assert "oxygen" not in observed_fields


def test_scheduled_events_repeat_independently_of_agent_actions():
    original = create_world(ScenarioFamily.MISLEADING_REPORT, seed=4)
    alternate = create_world(ScenarioFamily.MISLEADING_REPORT, seed=4)
    original_events = []
    alternate_events = []

    for _ in range(5):
        alternate = apply_action(alternate, Action(ActionKind.ACTIVATE_BACKUP)).state
        original_turn = advance_turn(original)
        alternate_turn = advance_turn(alternate)
        original_events.extend(original_turn.evidence)
        alternate_events.extend(alternate_turn.evidence)
        original = original_turn.state
        alternate = alternate_turn.state

    def public_transition(event):
        return event.turn, event.kind, event.message

    assert list(map(public_transition, original_events)) == list(
        map(public_transition, alternate_events)
    )
    assert [event.sequence for event in original_events] == sorted(
        event.sequence for event in original_events
    )


def test_normal_world_generates_more_oxygen_than_six_crew_consume():
    state = create_world(ScenarioFamily.NORMAL, seed=0)
    before = observe(state)

    after = observe(advance_turn(state).state)

    assert after.oxygen_sensors[0].oxygen > before.oxygen_sensors[0].oxygen
    assert after.oxygen_sensors[0].oxygen == after.oxygen_sensors[1].oxygen
    assert after.turn == before.turn + 1


def test_leak_consumes_oxygen_until_a_timed_repair_finishes():
    state = create_world(ScenarioFamily.LEAK, seed=3)
    for _ in range(4):
        if state.leak_active:
            break
        state = advance_turn(state).state
    assert state.leak_active
    oxygen_before_repair = state.oxygen

    started = apply_action(state, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))
    assert started.accepted
    assert observe(started.state).available_crew == 5
    assert started.state.leak_active
    assert started.state.oxygen == oxygen_before_repair
    repeated = apply_action(started.state, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))
    assert not repeated.accepted
    assert repeated.rejection == "repair_in_progress"

    first_work_turn = advance_turn(started.state).state
    assert first_work_turn.leak_active
    assert first_work_turn.repair_turns_remaining == 1
    assert first_work_turn.oxygen < oxygen_before_repair

    completed = advance_turn(first_work_turn).state
    assert not completed.leak_active
    assert completed.repair_turns_remaining == 0
    assert observe(completed).available_crew == 6
    assert any(item.kind == "repair_complete" for item in observe(completed).evidence)


def test_sensor_fault_is_detectable_by_independent_reading_and_inspection():
    state = create_world(ScenarioFamily.FAULTY_SENSOR, seed=9)
    for _ in range(4):
        readings = observe(state).oxygen_sensors
        if readings[0].oxygen != readings[1].oxygen:
            break
        state = advance_turn(state).state

    readings = observe(state).oxygen_sensors
    assert readings[0].oxygen != readings[1].oxygen
    stuck_sensor = min(readings, key=lambda reading: reading.oxygen).sensor
    inspected = apply_action(state, Action(ActionKind.INSPECT, target=stuck_sensor))
    assert inspected.accepted
    assert inspected.evidence[0].kind == "inspection"
    assert "calibration fault" in inspected.evidence[0].message


def test_misleading_report_does_not_change_actual_leak_or_sensor_reading():
    state = create_world(ScenarioFamily.MISLEADING_REPORT, seed=12)
    for _ in range(4):
        state = advance_turn(state).state
        if any(item.kind == "report" for item in state.evidence):
            break

    observation = observe(state)
    reports = [item.message.lower() for item in observation.evidence if item.kind == "report"]
    assert state.leak_active
    assert reports
    assert any("sensor" in report for report in reports)
    assert observation.oxygen_sensors[0].oxygen < 700


def test_invalid_action_has_stable_rejection_and_leaves_state_unchanged():
    state = create_world(ScenarioFamily.NORMAL, seed=0)

    result = apply_action(state, Action("change_oxygen_directly", quantity=900))

    assert not result.accepted
    assert result.rejection == "invalid_action"
    assert result.state == state
    assert result.evidence == ()


def test_inspection_consumes_available_crew_for_this_turn_then_resets():
    state = create_world(ScenarioFamily.NORMAL, seed=0)

    first = apply_action(state, Action(ActionKind.INSPECT, target="oxygen_system"))

    assert first.accepted
    assert observe(first.state).available_crew == 5
    next_turn = advance_turn(first.state).state
    assert observe(next_turn).available_crew == 6
    assert apply_action(next_turn, Action(ActionKind.INSPECT, target="oxygen_system")).accepted


def test_repair_requires_a_spare_part_and_an_unassigned_repair():
    state = create_world(ScenarioFamily.LEAK, seed=3)
    state = replace(state, leak_active=True, parts=0)

    no_parts = apply_action(state, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))

    assert not no_parts.accepted
    assert no_parts.rejection == "insufficient_parts"
    assert no_parts.state == state


def test_crew_capacity_bounds_inspections_and_repair_assignments():
    state = replace(create_world(ScenarioFamily.LEAK, seed=3), leak_active=True)

    for _ in range(6):
        result = apply_action(state, Action(ActionKind.INSPECT, target="oxygen_system"))
        assert result.accepted
        state = result.state

    assert observe(state).available_crew == 0
    exhausted_inspection = apply_action(state, Action(ActionKind.INSPECT, target="oxygen_system"))
    exhausted_repair = apply_action(state, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))
    assert exhausted_inspection.rejection == "crew_unavailable"
    assert exhausted_repair.rejection == "crew_unavailable"
    assert exhausted_inspection.state == state
    assert exhausted_repair.state == state


def test_supply_order_rejects_when_credits_are_insufficient():
    state = replace(create_world(ScenarioFamily.NORMAL, seed=0), credits=19)

    result = apply_action(state, Action(ActionKind.ORDER_SUPPLIES, target="oxygen", quantity=1))

    assert not result.accepted
    assert result.rejection == "insufficient_credits"
    assert result.state == state


def test_crew_loss_is_terminal_for_actions_and_in_progress_work():
    state = replace(
        create_world(ScenarioFamily.LEAK, seed=3),
        leak_active=True,
        oxygen=11,
        generation_rate=0,
        consumption_rate=11,
    )
    repair = apply_action(state, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))
    loss = advance_turn(repair.state)

    assert not loss.state.crew_alive
    assert loss.state.repair_turns_remaining == 1
    assert observe(loss.state).available_crew == 0
    blocked = apply_action(loss.state, Action(ActionKind.INSPECT, target="oxygen_system"))
    assert blocked.rejection == "crew_lost"
    assert blocked.state == loss.state

    terminal_turn = advance_turn(loss.state)
    assert terminal_turn.state == loss.state
    assert terminal_turn.evidence == ()


def test_backup_supply_is_finite_and_cannot_be_activated_twice():
    state = create_world(ScenarioFamily.LEAK, seed=1)
    started = apply_action(state, Action(ActionKind.ACTIVATE_BACKUP))
    assert started.accepted

    duplicate = apply_action(started.state, Action(ActionKind.ACTIVATE_BACKUP))
    assert not duplicate.accepted
    assert duplicate.rejection == "backup_already_active"

    for _ in range(10):
        started = advance_turn(started.state)
    assert observe(started.state).backup_oxygen == 0
    assert not started.state.backup_active
    exhausted = apply_action(started.state, Action(ActionKind.ACTIVATE_BACKUP))
    assert not exhausted.accepted
    assert exhausted.rejection == "backup_empty"


def test_supply_order_is_charged_now_and_delivered_after_a_lead_time():
    state = create_world(ScenarioFamily.NORMAL, seed=0)
    before = observe(state)

    order = apply_action(state, Action(ActionKind.ORDER_SUPPLIES, target="parts", quantity=1))

    assert order.accepted
    assert observe(order.state).parts == before.parts
    assert observe(order.state).credits < before.credits
    first_turn = advance_turn(order.state).state
    assert observe(first_turn).parts == before.parts
    delivered = advance_turn(first_turn)
    assert observe(delivered.state).parts == before.parts + 1
    assert any(item.kind == "arrival" for item in delivered.evidence)


@pytest.mark.parametrize("quantity", [0, -1, 4, True])
def test_supply_orders_reject_unbounded_or_non_integer_quantities(quantity):
    state = create_world(ScenarioFamily.NORMAL, seed=0)

    result = apply_action(
        state, Action(ActionKind.ORDER_SUPPLIES, target="oxygen", quantity=quantity)
    )

    assert not result.accepted
    assert result.rejection == "invalid_quantity"
    assert result.state == state
