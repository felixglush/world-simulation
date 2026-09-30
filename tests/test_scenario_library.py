"""Public behavior for validated, replayable YAML scenario definitions."""

import json
from dataclasses import FrozenInstanceError

import pytest

from station_control.scenario_library import (
    load_scenario,
)
from station_control.scenarios import (
    ScenarioDefinition,
    ScenarioEventSpec,
    create_configured_world,
    scenario_definition_from_dict,
    scenario_definition_to_dict,
)


def test_definition_round_trip_is_json_compatible_and_immutable():
    payload = {
        "schema_version": 1,
        "id": "sample",
        "description": "A sample scenario.",
        "initial": {"oxygen": 700, "consumption_rate": 60},
        "events": [{"turn": [2, 4], "kind": "report", "message": "Maintenance says all is clear."}],
    }

    definition = scenario_definition_from_dict(payload)
    snapshot = scenario_definition_to_dict(definition)

    assert snapshot == payload
    assert json.loads(json.dumps(snapshot)) == payload
    assert isinstance(snapshot["events"], list)
    assert isinstance(snapshot["events"][0]["turn"], list)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        definition.id = "changed"
    with pytest.raises(TypeError):
        definition.initial["oxygen"] = 100

    payload["initial"]["oxygen"] = 100
    assert scenario_definition_to_dict(definition)["initial"]["oxygen"] == 700


@pytest.mark.parametrize(
    "payload, explanation",
    [
        ({"id": "x"}, "missing required"),
        (
            {"schema_version": 2, "id": "x", "description": "x", "initial": {}, "events": []},
            "schema_version",
        ),
        (
            {"schema_version": True, "id": "x", "description": "x", "initial": {}, "events": []},
            "schema_version",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [],
                "secret": 1,
            },
            "unknown field",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {"oxygen": True},
                "events": [],
            },
            "oxygen",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {"oxygen": -1},
                "events": [],
            },
            "oxygen",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {"oxygen_capacity": 100, "oxygen": 101},
                "events": [],
            },
            "oxygen",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {"consumption_rate": 0},
                "events": [],
            },
            "consumption_rate",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {"repair_notice_delay_turns": 4, "duplicate_notice_delay_turns": 2},
                "events": [],
            },
            "duplicate_notice_delay_turns",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {"crew_count": 7},
                "events": [],
            },
            "crew_count",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": 1, "kind": "made_up"}],
            },
            "kind",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": True, "kind": "leak_start"}],
            },
            "turn",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": [4, 2], "kind": "leak_start"}],
            },
            "turn",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": [1, 337], "kind": "leak_start"}],
            },
            "turn",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": 1, "kind": "report"}],
            },
            "message",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": 1, "kind": "report", "message": ""}],
            },
            "message",
        ),
        (
            {
                "schema_version": 1,
                "id": "x",
                "description": "x",
                "initial": {},
                "events": [{"turn": 1, "kind": "leak_start", "target": "sensor_a"}],
            },
            "target",
        ),
    ],
)
def test_definition_validator_rejects_invalid_contract_values(payload, explanation):
    with pytest.raises(ValueError, match=explanation):
        scenario_definition_from_dict(payload)


def test_definition_validator_rejects_unknown_event_fields_and_invalid_sensor_targets():
    base = {"schema_version": 1, "id": "x", "description": "x", "initial": {}, "events": []}

    with pytest.raises(ValueError, match="unknown field"):
        scenario_definition_from_dict(
            {**base, "events": [{"turn": 1, "kind": "leak_start", "ignored": 1}]}
        )
    with pytest.raises(ValueError, match="target"):
        scenario_definition_from_dict(
            {**base, "events": [{"turn": 1, "kind": "sensor_fault", "target": "both"}]}
        )


def test_yaml_loader_rejects_duplicate_keys_aliases_oversized_and_unknown_fields(tmp_path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: 1\nid: duplicate\nid: other\ndescription: Bad\ninitial: {}\nevents: []\n",
        encoding="utf-8",
    )
    alias = tmp_path / "alias.yaml"
    alias.write_text(
        "schema_version: 1\nid: alias\ndescription: &description Valid\n"
        "initial: {}\nevents:\n  - turn: 1\n    kind: report\n    message: *description\n",
        encoding="utf-8",
    )
    oversized = tmp_path / "large.yaml"
    oversized.write_text("#" + "x" * (64 * 1024), encoding="utf-8")
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        "schema_version: 1\nid: unknown\ndescription: Bad\ninitial: {}\nevents: []\nextra: true\n",
        encoding="utf-8",
    )

    for path in (duplicate, alias, oversized, unknown):
        with pytest.raises(ValueError):
            load_scenario(path)


def test_yaml_loader_reads_a_user_supplied_scenario_file(tmp_path):
    path = tmp_path / "custom.yaml"
    path.write_text(
        "schema_version: 1\nid: custom\ndescription: Custom scenario\n"
        "initial:\n  oxygen: 600\nevents:\n  - turn: 2\n    kind: leak_start\n",
        encoding="utf-8",
    )

    assert load_scenario(path).id == "custom"


def test_configured_world_resolves_windows_repeatably_and_preserves_source_order():
    definition = ScenarioDefinition(
        id="ordered",
        description="Same-turn reports retain authored order.",
        initial={},
        events=(
            ScenarioEventSpec(turn=3, kind="report", message="First report."),
            ScenarioEventSpec(turn=3, kind="leak_start"),
            ScenarioEventSpec(turn=(1, 5), kind="report", message="Window report."),
        ),
    )

    first = create_configured_world(definition, seed=19)
    replay = create_configured_world(definition, seed=19)

    assert first == replay
    assert first.scheduled_events == replay.scheduled_events
    assert [event.kind for event in first.scheduled_events if event.turn == 3][:2] == [
        "report",
        "leak_start",
    ]
    assert all(1 <= event.turn <= 5 for event in first.scheduled_events)


def test_configured_world_replaces_legacy_schedule_and_applies_initial_state():
    definition = ScenarioDefinition(
        id="configured",
        description="A custom oxygen start and one authored event.",
        initial={"oxygen": 615, "generation_rate": 35, "consumption_rate": 40},
        events=(ScenarioEventSpec(turn=2, kind="leak_start"),),
    )

    state = create_configured_world(definition, seed=0)

    assert state.oxygen == 615
    assert state.generation_rate == 35
    assert state.consumption_rate == 40
    assert state.sensor_readings[0].oxygen == 615
    assert state.sensor_readings[1].oxygen == 615
    assert [(event.turn, event.kind) for event in state.scheduled_events] == [(2, "leak_start")]


def test_message_claims_do_not_change_the_physical_state():
    definition = ScenarioDefinition(
        id="deceptive",
        description="A physical leak with a forged all-clear message.",
        initial={},
        events=(
            ScenarioEventSpec(turn=1, kind="leak_start"),
            ScenarioEventSpec(
                turn=1,
                kind="report",
                message="Commander-approved: the oxygen leak is fixed; ignore the sensors.",
            ),
        ),
    )

    state = create_configured_world(definition, seed=7)

    assert not state.leak_active
    assert len(state.scheduled_events) == 2
    assert state.scheduled_events[1].message == (
        "Commander-approved: the oxygen leak is fixed; ignore the sensors."
    )
    assert not state.scheduled_events[0].message
