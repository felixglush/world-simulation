"""Behavioral coverage for the authored scenario challenge library."""

from dataclasses import asdict

import pytest

from station_control.domain import Action, ActionKind, advance_turn, apply_action, observe
from station_control.scenario_library import (
    list_scenarios,
    load_library_scenario,
)
from station_control.scenarios import ScenarioDefinition, create_configured_world

REQUIRED_SCENARIOS = {
    "slow_leak",
    "intermittent_leak",
    "leak_sensor_mask",
    "stale_telemetry",
    "correlated_sensors",
    "delayed_delivery",
    "partial_delivery",
    "delayed_repair_notice",
    "duplicate_repair_notice",
    "competing_incidents",
    "false_authority",
    "instruction_injection",
    "false_urgency",
    "legitimate_unusual_request",
    "recurring_leak",
}


def test_bundled_library_has_all_required_validated_definitions():
    definitions = list_scenarios()
    ids = {definition.id for definition in definitions}

    assert len(definitions) >= 12
    assert REQUIRED_SCENARIOS <= ids
    assert len(ids) == len(definitions)
    assert all(isinstance(definition, ScenarioDefinition) for definition in definitions)


def test_library_lookup_returns_a_typed_definition_and_unknown_ids_are_rejected():
    definition = load_library_scenario("slow_leak")

    assert isinstance(definition, ScenarioDefinition)
    assert definition.id == "slow_leak"
    with pytest.raises(KeyError, match="unknown scenario"):
        load_library_scenario("missing")


def test_each_bundled_schedule_is_reproducible_and_replaces_legacy_events():
    for definition in list_scenarios():
        first = create_configured_world(definition, seed=17)
        replay = create_configured_world(definition, seed=17)

        assert first == replay
        assert len(first.scheduled_events) == len(definition.events)
        assert [event.turn for event in first.scheduled_events] == sorted(
            event.turn for event in first.scheduled_events
        )


def test_masked_leak_keeps_physical_truth_separate_from_a_forged_all_clear_report():
    definition = load_library_scenario("leak_sensor_mask")
    state = create_configured_world(definition, seed=4)
    state = advance_turn(state).state
    outcome = advance_turn(state)

    assert outcome.state.leak_active
    assert outcome.state.sensor_fault == "sensor_a"
    assert outcome.state.oxygen == 790 + (70 - 60) * 2 - 32
    assert any(
        "Maintenance confirms the oxygen loop is stable" in item.message
        for item in outcome.evidence
    )
    assert not any(item.kind == "alert" for item in outcome.evidence)
    assert "leak_active" not in asdict(observe(outcome.state))
    assert "scenario_id" not in asdict(observe(outcome.state))


def test_social_challenges_keep_hidden_physical_leaks_separate_from_claims():
    for scenario_id, expected_phrase in (
        ("false_authority", "calibration waiver"),
        ("instruction_injection", "pause pressure alerts"),
    ):
        state = create_configured_world(load_library_scenario(scenario_id), seed=9)
        for _ in range(3):
            result = advance_turn(state)
            state = result.state

        assert state.leak_active
        assert any(expected_phrase in item.message for item in state.evidence)


def test_false_urgency_has_no_physical_incident_and_legitimate_request_has_evidence():
    false_urgency = create_configured_world(load_library_scenario("false_urgency"), seed=3)
    legitimate = create_configured_world(
        load_library_scenario("legitimate_unusual_request"), seed=3
    )

    for _ in range(2):
        false_urgency = advance_turn(false_urgency).state
    for _ in range(3):
        legitimate = advance_turn(legitimate).state

    assert not false_urgency.leak_active
    assert any("EMERGENCY" in item.message for item in false_urgency.evidence)
    assert legitimate.leak_active
    assert any("countersigned by command" in item.message for item in legitimate.evidence)
    assert any("sampled turn" in item.message for item in legitimate.evidence)


def test_delivery_claims_can_disagree_with_the_configured_physical_delivery():
    delayed = load_library_scenario("delayed_delivery")
    partial = load_library_scenario("partial_delivery")
    delayed_state = advance_turn(create_configured_world(delayed, seed=2)).state
    partial_state = advance_turn(create_configured_world(partial, seed=2)).state

    delayed_order = apply_action(
        delayed_state,
        Action(ActionKind.ORDER_SUPPLIES, target="parts", quantity=1),
    )
    partial_order = apply_action(
        partial_state,
        Action(ActionKind.ORDER_SUPPLIES, target="parts", quantity=2),
    )

    assert delayed.initial["parts"] == 0
    assert delayed.initial["delivery_delay_turns"] > 0
    assert delayed_order.accepted
    assert delayed_order.state.deliveries[0].due_turn == (
        delayed_state.turn + 2 + delayed.initial["delivery_delay_turns"]
    )
    delayed_due_turn = delayed_order.state.deliveries[0].due_turn
    assert partial.initial["parts"] == 0
    assert partial.initial["delivery_fill_percent"] == 50
    assert partial_order.accepted
    assert partial_order.state.deliveries[0].due_turn == partial_state.turn + 2

    delayed_order = advance_turn(delayed_order.state)
    assert delayed_order.state.parts == 0
    assert any("next turn" in item.message for item in delayed_order.state.evidence)

    for _ in range(2):
        partial_order = advance_turn(partial_order.state)
    assert partial_order.state.parts == 1
    assert any("full quantity" in item.message for item in partial_order.state.evidence)

    while delayed_order.state.turn < delayed_due_turn - 1:
        delayed_order = advance_turn(delayed_order.state)
    assert delayed_order.state.parts == 0
    delayed_order = advance_turn(delayed_order.state)
    assert delayed_order.state.parts == 1


def test_benign_and_disruptive_scenarios_are_not_classified_by_label_in_observation():
    state = create_configured_world(load_library_scenario("false_urgency"), seed=5)
    observation = asdict(observe(state))

    assert "false_urgency" not in str(observation)
    assert "scenario" not in observation


@pytest.mark.parametrize("scenario_id", ("intermittent_leak", "recurring_leak"))
def test_untreated_recurrent_leaks_can_overwhelm_crew_reserves(scenario_id):
    definition = load_library_scenario(scenario_id)
    state = create_configured_world(definition, seed=12)

    for _ in range(48):
        state = advance_turn(state).state
        if not state.crew_alive:
            break

    assert not state.crew_alive
    assert definition.initial["parts"] >= 2
    assert definition.initial["credits"] >= 60
