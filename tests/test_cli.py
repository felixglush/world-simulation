"""End-to-end behavior of the offline Station Control command line."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from station_control import cli
from station_control.persistence import RunLogError, RunLogWriter

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("CAPTAIN_MODEL", None)
    env.pop("JEV_MODEL", None)
    env.pop("CAPTAIN_BASE_URL", None)
    env.pop("JEV_BASE_URL", None)
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


def test_default_run_is_offline_and_writes_replayable_metadata(tmp_path: Path) -> None:
    output = tmp_path / "baseline.jsonl"
    result = run_cli(
        "run",
        "--scenario",
        "leak",
        "--seed",
        "41",
        "--turns",
        "3",
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert "rules" in result.stdout.lower()
    assert "OPENROUTER_API_KEY" not in result.stdout + result.stderr
    records = records_at(output)
    assert records[0]["record_type"] == "run_start"
    assert records[0]["schema_version"] == 1
    assert records[0]["simulator_version"]
    metadata = records[0]["metadata"]
    assert metadata["scenario"] == "leak"
    assert metadata["seed"] == 41
    assert metadata["controller"] == "rules"
    assert metadata["instructions"]
    assert metadata["rubrics"]
    assert records[-1]["record_type"] == "run_end"
    assert all(record["record_type"] == "event" for record in records[1:-1])
    assert records[-1]["event_count"] == len(records) - 2


def test_replay_connects_observed_evidence_decision_and_consequence(tmp_path: Path) -> None:
    output = tmp_path / "mission.jsonl"
    saved = run_cli(
        "run",
        "--scenario",
        "misleading_report",
        "--seed",
        "8",
        "--turns",
        "4",
        "--output",
        str(output),
    )
    assert saved.returncode == 0, saved.stderr

    replay = run_cli("replay", str(output))

    assert replay.returncode == 0, replay.stderr
    assert 'Scenario: "misleading_report"' in replay.stdout
    assert "Evidence:" in replay.stdout
    assert "Decision:" in replay.stdout
    assert "Consequence:" in replay.stdout
    assert "World state (debug):" in replay.stdout
    assert "Turn " in replay.stdout


def test_rerun_reuses_scenario_and_seed_but_writes_a_separate_run(tmp_path: Path) -> None:
    original = tmp_path / "original.jsonl"
    rerun = tmp_path / "rerun.jsonl"
    first = run_cli(
        "run",
        "--scenario",
        "faulty_sensor",
        "--seed",
        "23",
        "--turns",
        "3",
        "--output",
        str(original),
    )
    assert first.returncode == 0, first.stderr
    original_bytes = original.read_bytes()

    repeated = run_cli("rerun", str(original), "--turns", "1", "--output", str(rerun))

    assert repeated.returncode == 0, repeated.stderr
    assert original.read_bytes() == original_bytes
    original_start = records_at(original)[0]
    rerun_start = records_at(rerun)[0]
    assert rerun_start["run_id"] != original_start["run_id"]
    assert rerun_start["metadata"]["scenario"] == "faulty_sensor"
    assert rerun_start["metadata"]["seed"] == 23
    assert rerun_start["metadata"]["duration_turns"] == 1


def test_same_seed_keeps_external_evidence_fixed_across_offline_configs(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.jsonl"
    ablation = tmp_path / "ablation.jsonl"
    first = run_cli(
        "run",
        "--scenario",
        "leak",
        "--seed",
        "101",
        "--turns",
        "5",
        "--output",
        str(baseline),
    )
    second = run_cli(
        "run",
        "--scenario",
        "leak",
        "--seed",
        "101",
        "--turns",
        "5",
        "--inspection-budget",
        "0",
        "--output",
        str(ablation),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr

    def external_evidence(path: Path) -> list[dict[str, object]]:
        return [
            record
            for record in records_at(path)[1:-1]
            if record["event_type"] == "world_evidence"
            and record["evidence"]["kind"] in {"alert", "report"}
        ]

    assert external_evidence(baseline) == external_evidence(ablation)
    assert records_at(baseline)[0]["metadata"]["inspection_budget_per_turn"] == 1
    assert records_at(ablation)[0]["metadata"]["inspection_budget_per_turn"] == 0


def test_live_controller_requires_explicit_finite_budget_and_configuration(tmp_path: Path) -> None:
    no_key = run_cli(
        "run",
        "--controller",
        "llm",
        "--max-calls",
        "2",
        "--max-output-tokens-per-call",
        "64",
        "--output",
        str(tmp_path / "without-key.jsonl"),
    )
    assert no_key.returncode != 0
    assert "OPENROUTER_API_KEY" in no_key.stderr
    assert "Traceback" not in no_key.stderr

    missing_budget = run_cli(
        "run",
        "--controller",
        "llm",
        "--output",
        str(tmp_path / "without-budget.jsonl"),
        environment={
            "OPENROUTER_API_KEY": "offline-test-key",
            "CAPTAIN_MODEL": "offline/test-model",
        },
    )
    assert missing_budget.returncode != 0
    assert "budget" in missing_budget.stderr.lower()
    assert "offline-test-key" not in missing_budget.stdout + missing_budget.stderr


@pytest.mark.parametrize(
    ("controller", "environment", "expected_message"),
    [
        (
            "llm",
            {"OPENROUTER_API_KEY": "offline-test-key"},
            "CAPTAIN_MODEL",
        ),
        (
            "jev+llm",
            {
                "OPENROUTER_API_KEY": "offline-test-key",
                "CAPTAIN_MODEL": "offline/captain",
            },
            "JEV_MODEL",
        ),
    ],
)
def test_live_controller_requires_each_selected_model_before_output(
    tmp_path: Path,
    controller: str,
    environment: dict[str, str],
    expected_message: str,
) -> None:
    output = tmp_path / f"{controller.replace('+', '-')}.jsonl"
    result = run_cli(
        "run",
        "--controller",
        controller,
        "--max-calls",
        "2",
        "--max-output-tokens-per-call",
        "64",
        "--output",
        str(output),
        environment=environment,
    )

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert not output.exists()


def test_existing_output_is_preserved_and_rejected_before_live_setup(tmp_path: Path) -> None:
    output = tmp_path / "already-there.jsonl"
    output.write_text("keep this file\n", encoding="utf-8")

    result = run_cli(
        "run",
        "--controller",
        "jev+llm",
        "--max-calls",
        "2",
        "--max-output-tokens-per-call",
        "64",
        "--output",
        str(output),
        environment={
            "OPENROUTER_API_KEY": "offline-test-key",
            "CAPTAIN_MODEL": "offline/captain",
            "JEV_MODEL": "offline/jev",
        },
    )

    assert result.returncode != 0
    assert "already exists" in result.stderr.lower()
    assert output.read_text(encoding="utf-8") == "keep this file\n"
    assert "offline-test-key" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("contents", "expected_error"),
    [
        ("{broken json\n", "Malformed"),
        (
            '{"record_type":"run_start","schema_version":99,"run_id":"x",'
            '"metadata":{}}\n{"record_type":"run_end","run_id":"x",'
            '"event_count":0}\n',
            "schema version",
        ),
        (
            '{"record_type":"run_start","schema_version":1,"run_id":"x","metadata":{}}\n',
            "run_end",
        ),
        (
            '{"record_type":"run_start","schema_version":1,"run_id":"x",'
            '"simulator_version":"0.1.0","metadata":{}}\n'
            '{"record_type":"event","sequence":0,"turn":2,"event_type":"x"}\n'
            '{"record_type":"event","sequence":1,"turn":1,"event_type":"x"}\n'
            '{"record_type":"run_end","schema_version":1,"run_id":"x",'
            '"status":"completed","event_count":2}\n',
            "backwards",
        ),
        (
            '{"record_type":"run_start","schema_version":1,"run_id":"x",'
            '"simulator_version":"0.1.0","metadata":{}}\n'
            '{"record_type":"event","sequence":0,"turn":2,"event_type":"x"}\n'
            '{"record_type":"run_end","schema_version":1,"run_id":"x",'
            '"status":"completed","turn":1,"event_count":1}\n',
            "Run end turn",
        ),
    ],
)
def test_replay_rejects_malformed_incompatible_incomplete_or_reordered_logs(
    tmp_path: Path, contents: str, expected_error: str
) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(contents, encoding="utf-8")

    result = run_cli("replay", str(path))

    assert result.returncode != 0
    assert expected_error.lower() in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_replay_of_missing_file_fails_with_a_safe_message(tmp_path: Path) -> None:
    result = run_cli("replay", str(tmp_path / "missing.jsonl"))

    assert result.returncode != 0
    assert "does not exist" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_jev_constructor_failure_closes_captain_and_records_failed_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    class FakeCaptain:
        closed: list[bool] = []

        def __init__(self, **_options: object) -> None:
            pass

        def close(self) -> None:
            self.closed.append(True)

    captain_type = FakeCaptain

    def fail_to_create_dispatcher(**_options: object) -> object:
        raise RuntimeError("simulated adapter setup failure")

    monkeypatch.setattr(cli, "OpenRouterCaptainProvider", captain_type)
    monkeypatch.setattr(cli, "JevDispatchProvider", fail_to_create_dispatcher)

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setenv("CAPTAIN_MODEL", "fake/captain")
    monkeypatch.setenv("JEV_MODEL", "fake/jev")
    output = tmp_path / "failed-run.jsonl"
    exit_code = cli.main(
        [
            "run",
            "--controller",
            "jev+llm",
            "--max-calls",
            "2",
            "--max-output-tokens-per-call",
            "64",
            "--output",
            str(output),
        ]
    )

    assert captain_type.closed == [True]
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Mission could not complete" in captured.err
    assert "test-only" not in captured.out + captured.err
    assert "test-only" not in output.read_text(encoding="utf-8")
    assert records_at(output)[-1]["status"] == "failed"


def test_writer_rejects_backwards_turns_before_creating_a_replayable_log(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backwards.jsonl"
    writer = RunLogWriter(
        path,
        {"scenario": "normal", "seed": 1, "controller": "rules"},
        run_id="test-run",
        simulator_version="0.1.0",
    )
    writer.write_event(
        {
            "record_type": "event",
            "sequence": 0,
            "turn": 2,
            "event_type": "turn",
            "evidence": None,
            "decision": None,
            "consequence": None,
        }
    )

    with pytest.raises(RunLogError, match="cannot move backwards"):
        writer.write_event(
            {
                "record_type": "event",
                "sequence": 1,
                "turn": 1,
                "event_type": "turn",
                "evidence": None,
                "decision": None,
                "consequence": None,
            }
        )
    writer.close_incomplete()


def test_old_simulator_log_replays_but_cannot_be_rerun(tmp_path: Path) -> None:
    source = tmp_path / "old-run.jsonl"
    output = tmp_path / "new-run.jsonl"
    records = [
        {
            "record_type": "run_start",
            "schema_version": 1,
            "run_id": "old-run",
            "simulator_version": "0.0.9",
            "metadata": {"scenario": "normal", "seed": 3, "controller": "rules"},
        },
        {
            "record_type": "run_end",
            "schema_version": 1,
            "run_id": "old-run",
            "status": "completed",
            "turn": 1,
            "event_count": 0,
            "debug_snapshots": [],
        },
    ]
    source.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    replay = run_cli("replay", str(source))
    rerun = run_cli("rerun", str(source), "--output", str(output))

    assert replay.returncode == 0, replay.stderr
    assert rerun.returncode != 0
    assert "cannot rerun simulator version" in rerun.stderr.lower()
    assert not output.exists()


def test_replay_escapes_terminal_controls_in_saved_metadata(tmp_path: Path) -> None:
    path = tmp_path / "terminal-controls.jsonl"
    records = [
        {
            "record_type": "run_start",
            "schema_version": 1,
            "run_id": "safe-id",
            "simulator_version": "0.1.0",
            "metadata": {
                "scenario": "\u001b[31mred",
                "seed": 1,
                "controller": "rules",
            },
        },
        {
            "record_type": "run_end",
            "schema_version": 1,
            "run_id": "safe-id",
            "status": "completed",
            "turn": 0,
            "event_count": 0,
            "debug_snapshots": [],
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    replay = run_cli("replay", str(path))

    assert replay.returncode == 0, replay.stderr
    assert "\u001b" not in replay.stdout
    assert "\\u001b[31mred" in replay.stdout
