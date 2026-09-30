"""Scenario-library, custom-file, and snapshot behavior through the CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from station_control import cli
from station_control.controllers import ActionRequest, ActionRequestKind, CaptainDecision

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*arguments: str, environment: dict[str, str] | None = None):
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("CAPTAIN_MODEL", None)
    env.pop("JEV_MODEL", None)
    if environment:
        env.update(environment)
    return subprocess.run(
        [sys.executable, "-m", "station_control", *arguments],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def records_at(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_scenario(
    path: Path,
    *,
    scenario_id: str = "custom_cli_case",
    description: str = "PRIVATE_SCENARIO_DESCRIPTION",
    message: str = "Crew report: a sample is available for review.",
) -> Path:
    path.write_text(
        "\n".join(
            (
                "schema_version: 1",
                f"id: {scenario_id}",
                f"description: {description}",
                "initial:",
                "  oxygen: 680",
                "events:",
                "  - turn: 1",
                "    kind: report",
                f"    message: {json.dumps(message)}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_scenarios_command_lists_legacy_families_and_yaml_library_entries() -> None:
    result = run_cli("scenarios")

    assert result.returncode == 0, result.stderr
    for scenario in (
        "normal",
        "leak",
        "faulty_sensor",
        "misleading_report",
        "slow_leak",
        "instruction_injection",
    ):
        assert scenario in result.stdout


def test_run_accepts_a_scenario_library_id_and_records_its_definition(tmp_path: Path) -> None:
    output = tmp_path / "library-run.jsonl"
    result = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario",
        "instruction_injection",
        "--seed",
        "17",
        "--turns",
        "3",
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    metadata = records_at(output)[0]["metadata"]
    assert metadata["scenario"] == "instruction_injection"
    assert metadata["scenario_definition"]["id"] == "instruction_injection"


def test_run_accepts_custom_yaml_and_saves_the_full_canonical_definition(tmp_path: Path) -> None:
    scenario_file = write_scenario(tmp_path / "custom.yaml")
    output = tmp_path / "custom-run.jsonl"

    result = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario-file",
        str(scenario_file),
        "--seed",
        "21",
        "--turns",
        "2",
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    records = records_at(output)
    metadata = records[0]["metadata"]
    assert metadata["scenario"] == "custom_cli_case"
    definition = metadata["scenario_definition"]
    assert definition["schema_version"] == 1
    assert definition["id"] == "custom_cli_case"
    assert definition["description"] == "PRIVATE_SCENARIO_DESCRIPTION"
    assert definition["initial"]["oxygen"] == 680
    assert definition["events"][0]["message"] == "Crew report: a sample is available for review."
    assert any(
        record["event_type"] == "world_evidence"
        and record["evidence"]["message"] == "Crew report: a sample is available for review."
        for record in records[1:-1]
    )


def test_invalid_scenario_file_fails_before_creating_a_log_or_provider(tmp_path: Path) -> None:
    scenario_file = tmp_path / "invalid.yaml"
    scenario_file.write_text(
        "schema_version: 1\nid: invalid\nevents: [not-a-mapping]\n", encoding="utf-8"
    )
    output = tmp_path / "invalid-run.jsonl"

    result = run_cli(
        "run",
        "--scenario-file",
        str(scenario_file),
        "--output",
        str(output),
        environment={"OPENROUTER_API_KEY": "must-not-be-used"},
    )

    assert result.returncode != 0
    assert "scenario" in result.stderr.lower()
    assert "must-not-be-used" not in result.stdout + result.stderr
    assert not output.exists()


def test_missing_scenario_file_has_a_safe_error_and_creates_no_log(tmp_path: Path) -> None:
    output = tmp_path / "missing-scenario-run.jsonl"

    result = run_cli(
        "run",
        "--scenario-file",
        str(tmp_path / "missing.yaml"),
        "--output",
        str(output),
    )

    assert result.returncode != 0
    assert "scenario file" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()
    assert not output.exists()


def test_scenario_and_scenario_file_options_are_mutually_exclusive(tmp_path: Path) -> None:
    scenario_file = write_scenario(tmp_path / "custom.yaml")
    output = tmp_path / "conflict.jsonl"

    result = run_cli(
        "run",
        "--scenario",
        "normal",
        "--scenario-file",
        str(scenario_file),
        "--output",
        str(output),
    )

    assert result.returncode != 0
    assert "not allowed with argument" in result.stderr.lower()
    assert not output.exists()


@pytest.mark.parametrize("source_change", ("delete", "edit"))
def test_rerun_uses_saved_definition_after_source_file_changes(
    tmp_path: Path, source_change: str
) -> None:
    scenario_file = write_scenario(tmp_path / "source.yaml")
    original = tmp_path / "original.jsonl"
    rerun = tmp_path / "rerun.jsonl"
    saved = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario-file",
        str(scenario_file),
        "--seed",
        "31",
        "--turns",
        "4",
        "--output",
        str(original),
    )
    assert saved.returncode == 0, saved.stderr

    if source_change == "delete":
        scenario_file.unlink()
    else:
        write_scenario(
            scenario_file,
            scenario_id="edited_source",
            description="CHANGED_DESCRIPTION",
            message="The edited source should not replace the saved scenario.",
        )

    repeated = run_cli(
        "rerun",
        str(original),
        "--controller",
        "rules",
        "--output",
        str(rerun),
    )

    assert repeated.returncode == 0, repeated.stderr
    original_records = records_at(original)
    rerun_records = records_at(rerun)
    assert original_records[0]["run_id"] != rerun_records[0]["run_id"]
    assert (
        original_records[0]["metadata"]["scenario_definition"]
        == rerun_records[0]["metadata"]["scenario_definition"]
    )
    assert original_records[0]["metadata"]["seed"] == rerun_records[0]["metadata"]["seed"] == 31
    assert original_records[1:-1] == rerun_records[1:-1]
    assert original_records[-1]["debug_snapshots"] == rerun_records[-1]["debug_snapshots"]


def test_rerun_explicit_seed_and_scenario_override_saved_snapshot(tmp_path: Path) -> None:
    scenario_file = write_scenario(tmp_path / "source.yaml")
    original = tmp_path / "original.jsonl"
    rerun = tmp_path / "overridden.jsonl"
    saved = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario-file",
        str(scenario_file),
        "--seed",
        "37",
        "--turns",
        "2",
        "--output",
        str(original),
    )
    assert saved.returncode == 0, saved.stderr

    repeated = run_cli(
        "rerun",
        str(original),
        "--controller",
        "rules",
        "--seed",
        "83",
        "--scenario",
        "normal",
        "--output",
        str(rerun),
    )

    assert repeated.returncode == 0, repeated.stderr
    metadata = records_at(rerun)[0]["metadata"]
    assert metadata["scenario"] == "normal"
    assert metadata["seed"] == 83
    assert "scenario_definition" not in metadata


def test_rerun_scenario_file_explicitly_replaces_saved_snapshot(tmp_path: Path) -> None:
    original_file = write_scenario(tmp_path / "original.yaml")
    replacement_file = write_scenario(
        tmp_path / "replacement.yaml",
        scenario_id="replacement_case",
        description="A replacement scenario.",
        message="Replacement report.",
    )
    original = tmp_path / "original.jsonl"
    rerun = tmp_path / "replacement-run.jsonl"
    saved = run_cli(
        "run",
        "--controller",
        "rules",
        "--scenario-file",
        str(original_file),
        "--turns",
        "2",
        "--output",
        str(original),
    )
    assert saved.returncode == 0, saved.stderr

    repeated = run_cli(
        "rerun",
        str(original),
        "--controller",
        "rules",
        "--scenario-file",
        str(replacement_file),
        "--output",
        str(rerun),
    )

    assert repeated.returncode == 0, repeated.stderr
    metadata = records_at(rerun)[0]["metadata"]
    assert metadata["scenario"] == "replacement_case"
    assert metadata["scenario_definition"]["id"] == "replacement_case"


def test_default_controller_remains_llm_and_requires_live_configuration_for_yaml(
    tmp_path: Path,
) -> None:
    scenario_file = write_scenario(tmp_path / "custom.yaml")
    output = tmp_path / "default-ai-run.jsonl"

    result = run_cli("run", "--scenario-file", str(scenario_file), "--output", str(output))

    assert result.returncode != 0
    assert "budget" in result.stderr.lower()
    assert not output.exists()


def test_custom_scenario_drives_the_default_ai_without_leaking_its_definition(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    scenario_file = write_scenario(tmp_path / "captain.yaml")
    output = tmp_path / "captain-run.jsonl"

    class CapturingCaptain:
        def __init__(self) -> None:
            self.contexts = []

        def decide(self, context):
            self.contexts.append(context)
            return CaptainDecision(
                ActionRequest(
                    ActionRequestKind.DEFER,
                    follow_up_turn=context.station.turn + 1,
                )
            )

        def close(self) -> None:
            pass

    captain = CapturingCaptain()
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fake-key")
    monkeypatch.setenv("CAPTAIN_MODEL", "offline/fake-captain")
    monkeypatch.setattr(cli, "_make_providers", lambda _controller, _settings: (captain, None))

    exit_code = cli.main(
        [
            "run",
            "--scenario-file",
            str(scenario_file),
            "--turns",
            "2",
            "--max-calls",
            "1",
            "--max-output-tokens-per-call",
            "64",
            "--output",
            str(output),
        ]
    )

    capsys.readouterr()
    assert exit_code == 0
    assert captain.contexts
    assert records_at(output)[0]["metadata"]["controller"] == "llm"
    context_json = json.dumps(asdict(captain.contexts[0]))
    assert "PRIVATE_SCENARIO_DESCRIPTION" not in context_json
    assert "custom_cli_case" not in context_json
    action = next(record for record in records_at(output)[1:-1] if record["event_type"] == "action")
    assert action["decision"]["kind"] == "defer"
    assert action["consequence"]["accepted"] is True
