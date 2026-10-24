"""Public behavior for running several authored scenarios in one mission."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from station_control import scenarios
from station_control.domain import advance_turn
from station_control.scenario_library import list_scenarios
from station_control.scenarios import (
    ScenarioDefinition,
    ScenarioEventSpec,
    create_configured_world,
    scenario_definition_from_dict,
    scenario_definition_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def definition(
    scenario_id: str,
    *,
    initial: dict[str, int] | None = None,
    events: tuple[ScenarioEventSpec, ...] = (),
) -> ScenarioDefinition:
    return scenario_definition_from_dict(
        {
            "schema_version": 1,
            "id": scenario_id,
            "description": f"Definition for {scenario_id}.",
            "initial": initial or {},
            "events": [
                {
                    "turn": list(event.turn) if isinstance(event.turn, tuple) else event.turn,
                    "kind": event.kind,
                    **({"target": event.target} if event.target is not None else {}),
                    **({"message": event.message} if event.message is not None else {}),
                    **({"value": event.value} if event.value is not None else {}),
                }
                for event in events
            ],
        }
    )


def run_cli(*arguments: str):
    environment = os.environ.copy()
    for name in ("OPENROUTER_API_KEY", "CAPTAIN_MODEL", "JEV_MODEL"):
        environment.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", "station_control", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )


def records_at(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_scenario(
    path: Path,
    scenario_id: str,
    *,
    oxygen: int,
    event_turn: int | list[int],
    event_kind: str,
    event_message: str | None = None,
) -> Path:
    turn = json.dumps(event_turn)
    message = f"    message: {json.dumps(event_message)}\n" if event_message is not None else ""
    path.write_text(
        f"schema_version: 1\nid: {scenario_id}\ndescription: {scenario_id} source.\n"
        f"initial:\n  oxygen: {oxygen}\nevents:\n  - turn: {turn}\n"
        f"    kind: {event_kind}\n{message}",
        encoding="utf-8",
    )
    return path


def test_composed_fault_and_deceptive_report_run_in_one_world_with_shared_resources() -> None:
    silent_fault = definition(
        "silent_fault",
        initial={"oxygen": 790, "parts": 2, "leak_rate": 32},
        events=(
            ScenarioEventSpec(turn=1, kind="leak_start", message=""),
            ScenarioEventSpec(turn=1, kind="sensor_fault", target="sensor_a", message=""),
        ),
    )
    deceptive_report = definition(
        "deceptive_report",
        initial={"oxygen": 820, "credits": 75},
        events=(ScenarioEventSpec(turn=1, kind="misleading_report"),),
    )

    composed = scenarios.compose_scenarios((silent_fault, deceptive_report))
    state = create_configured_world(composed, seed=9)
    turn = advance_turn(state)

    assert composed.id == "composite"
    assert composed.initial == {"oxygen": 820, "parts": 2, "leak_rate": 32, "credits": 75}
    assert composed.description == "Composition: silent_fault@+0, deceptive_report@+0"
    assert turn.state.leak_active
    assert turn.state.sensor_fault == "sensor_a"
    assert turn.state.oxygen == 798  # One reserve evolves once under the shared leak.
    assert turn.state.parts == 2
    assert turn.state.credits == 75
    assert [item.kind for item in turn.evidence] == ["report"]
    assert "stable" in turn.evidence[0].message.lower()


def test_composition_offsets_windows_and_preserves_selection_order_for_ties() -> None:
    first = definition(
        "first_case",
        initial={"oxygen": 810},
        events=(
            ScenarioEventSpec(turn=2, kind="sensor_fault", target="sensor_a", message=""),
            ScenarioEventSpec(turn=(4, 6), kind="telemetry"),
        ),
    )
    second = definition(
        "second_case",
        initial={"parts": 3},
        events=(
            ScenarioEventSpec(turn=1, kind="sensor_fault", target="sensor_b", message=""),
            ScenarioEventSpec(turn=2, kind="report", message="A second report."),
        ),
    )

    composed = scenarios.compose_scenarios((first, second), spacing=1)
    state = create_configured_world(composed, seed=14)
    at_turn_two = advance_turn(advance_turn(state).state).state
    at_turn_three = advance_turn(at_turn_two)

    assert [event.turn for event in composed.events] == [2, (4, 6), 2, 3]
    assert composed.initial == {"oxygen": 810, "parts": 3}
    assert at_turn_two.sensor_fault == "sensor_b"  # Later selected definition wins the tie.
    assert any(item.message == "A second report." for item in at_turn_three.evidence)


def test_composition_revalidates_merged_initial_values_shifted_bounds_and_event_count() -> None:
    too_much_oxygen = definition("oxygen_source", initial={"oxygen": 900})
    too_small_capacity = definition("capacity_source", initial={"oxygen_capacity": 700})
    with pytest.raises(ValueError, match="initial.oxygen cannot exceed"):
        scenarios.compose_scenarios((too_much_oxygen, too_small_capacity))

    edge_event = definition(
        "edge_source", events=(ScenarioEventSpec(turn=(330, 336), kind="telemetry"),)
    )
    shifted = definition("shift_source", events=(ScenarioEventSpec(turn=1, kind="telemetry"),))
    with pytest.raises(ValueError, match="window must be ordered integers"):
        scenarios.compose_scenarios((shifted, edge_event), spacing=1)

    many_first = definition(
        "many_first", events=tuple(ScenarioEventSpec(turn=1, kind="telemetry") for _ in range(128))
    )
    many_second = definition(
        "many_second", events=tuple(ScenarioEventSpec(turn=1, kind="telemetry") for _ in range(129))
    )
    with pytest.raises(ValueError, match="at most 256 items"):
        scenarios.compose_scenarios((many_first, many_second))

    for invalid_spacing in (-1, True, 1.5):
        with pytest.raises(ValueError, match="nonnegative integer"):
            scenarios.compose_scenarios((too_much_oxygen,), spacing=invalid_spacing)


def test_all_bundled_yaml_scenarios_compose_and_keep_every_authored_event() -> None:
    definitions = list_scenarios()

    assert len(definitions) == 15
    composed = scenarios.compose_scenarios(definitions)
    snapshot = scenario_definition_to_dict(composed)

    assert composed.id == "composite"
    assert len(composed.events) == sum(len(item.events) for item in definitions)
    assert len(snapshot["description"]) <= 512


def test_repeated_library_ids_run_together_and_save_full_composition_snapshot(
    tmp_path: Path,
) -> None:
    output = tmp_path / "library-composition.jsonl"
    result = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario",
        "leak_sensor_mask",
        "--scenario",
        "false_urgency",
        "--scenario-spacing",
        "3",
        "--seed",
        "12",
        "--turns",
        "5",
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    metadata = records_at(output)[0]["metadata"]
    snapshot = metadata["scenario_definition"]
    assert metadata["scenario"] == "composite"
    assert snapshot["id"] == "composite"
    assert snapshot["initial"]["oxygen"] == 820
    assert snapshot["initial"]["leak_rate"] == 32
    assert any(
        event["kind"] == "sensor_fault" and event["turn"] == 2 for event in snapshot["events"]
    )
    assert any(event["kind"] == "report" and event["turn"] == 5 for event in snapshot["events"])
    emitted = [
        record["evidence"]
        for record in records_at(output)[1:-1]
        if record["event_type"] == "world_evidence"
    ]
    assert any(item["turn"] == 2 and item["kind"] == "report" for item in emitted)
    assert any(item["turn"] == 5 and item["kind"] == "report" for item in emitted)


def test_repeated_files_save_exact_composition_and_rerun_after_sources_are_deleted(
    tmp_path: Path,
) -> None:
    first_file = write_scenario(
        tmp_path / "first.yaml",
        "first_source",
        oxygen=770,
        event_turn=[1, 2],
        event_kind="telemetry",
    )
    second_file = write_scenario(
        tmp_path / "second.yaml",
        "second_source",
        oxygen=740,
        event_turn=1,
        event_kind="report",
        event_message="A saved second-source report.",
    )
    original = tmp_path / "files-composition.jsonl"
    rerun = tmp_path / "files-composition-rerun.jsonl"
    saved = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario-file",
        str(first_file),
        "--scenario-file",
        str(second_file),
        "--scenario-spacing",
        "2",
        "--seed",
        "28",
        "--turns",
        "4",
        "--output",
        str(original),
    )
    assert saved.returncode == 0, saved.stderr
    first_records = records_at(original)
    original_snapshot = first_records[0]["metadata"]["scenario_definition"]
    assert original_snapshot["initial"]["oxygen"] == 740
    assert original_snapshot["events"][0]["turn"] == [1, 2]
    assert original_snapshot["events"][1]["turn"] == 3
    first_file.unlink()
    second_file.unlink()

    repeated = run_cli("rerun", str(original), "--controller", "rules", "--output", str(rerun))

    assert repeated.returncode == 0, repeated.stderr
    rerun_records = records_at(rerun)
    assert rerun_records[0]["metadata"]["scenario_definition"] == original_snapshot
    assert rerun_records[0]["metadata"]["seed"] == first_records[0]["metadata"]["seed"] == 28
    assert rerun_records[1:-1] == first_records[1:-1]
    assert rerun_records[-1]["debug_snapshots"] == first_records[-1]["debug_snapshots"]

    saved_definition = scenario_definition_from_dict(original_snapshot)
    original_window_turn = create_configured_world(saved_definition, 28).scheduled_events[0].turn
    changed_seed = next(
        seed
        for seed in range(29, 200)
        if create_configured_world(saved_definition, seed).scheduled_events[0].turn
        != original_window_turn
    )
    reseeded = tmp_path / "files-composition-reseeded.jsonl"
    reseed_result = run_cli(
        "rerun",
        str(original),
        "--controller",
        "rules",
        "--seed",
        str(changed_seed),
        "--output",
        str(reseeded),
    )
    assert reseed_result.returncode == 0, reseed_result.stderr
    reseeded_records = records_at(reseeded)
    assert reseeded_records[0]["metadata"]["scenario_definition"] == original_snapshot
    assert reseeded_records[0]["metadata"]["seed"] == changed_seed
    assert any(
        record["turn"] != original_window_turn
        and record["evidence"]["message"].startswith("Telemetry:")
        for record in reseeded_records[1:-1]
        if record["event_type"] == "world_evidence"
    )


def test_spacing_requires_multiple_explicit_yaml_sources_including_on_rerun(tmp_path: Path) -> None:
    one_file = write_scenario(
        tmp_path / "one.yaml",
        "one_source",
        oxygen=700,
        event_turn=1,
        event_kind="telemetry",
    )
    rejected = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario-file",
        str(one_file),
        "--scenario-spacing",
        "0",
        "--output",
        str(tmp_path / "single-with-spacing.jsonl"),
    )
    assert rejected.returncode != 0
    assert "requires multiple explicit scenario selectors" in rejected.stderr.lower()

    source = tmp_path / "source.jsonl"
    created = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario",
        "slow_leak",
        "--turns",
        "1",
        "--output",
        str(source),
    )
    assert created.returncode == 0, created.stderr
    rerun = run_cli(
        "rerun",
        str(source),
        "--controller",
        "rules",
        "--scenario-spacing",
        "2",
        "--output",
        str(tmp_path / "rerun-with-spacing.jsonl"),
    )
    assert rerun.returncode != 0
    assert "requires multiple explicit scenario selectors" in rerun.stderr.lower()


def test_multiple_legacy_families_are_rejected_with_yaml_guidance_and_default_ai_stays_fail_closed(
    tmp_path: Path,
) -> None:
    legacy = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario",
        "normal",
        "--scenario",
        "leak",
        "--output",
        str(tmp_path / "legacy-composition.jsonl"),
    )
    assert legacy.returncode != 0
    assert "compose yaml library ids or files" in legacy.stderr.lower()
    assert "legacy families are supported singly" in legacy.stderr.lower()

    ai = run_cli(
        "run",
        "--scenario",
        "leak_sensor_mask",
        "--scenario",
        "false_urgency",
        "--output",
        str(tmp_path / "default-ai-composition.jsonl"),
    )
    assert ai.returncode != 0
    assert "budget" in ai.stderr.lower()
    assert not (tmp_path / "default-ai-composition.jsonl").exists()
