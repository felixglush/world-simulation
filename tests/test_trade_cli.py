"""Process-level proof of the offline trade and delayed-defect scenario."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_offline_trade_story_is_deterministic_and_recovers() -> None:
    env = os.environ.copy()
    for key in ("OPENROUTER_API_KEY", "CAPTAIN_MODEL", "JEV_MODEL", "ADVERSARY_MODEL"):
        env.pop(key, None)
    runs = [
        subprocess.run(
            [sys.executable, "-m", "station_control", "trade", "--turns", "20"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        for _ in range(2)
    ]
    assert runs[0].returncode == 0, runs[0].stderr
    assert runs[1].returncode == 0, runs[1].stderr
    assert runs[0].stdout == runs[1].stdout
    result = json.loads(runs[0].stdout)
    assert result["controller"] == "scripted"
    assert result["model_calls"] == 0
    assert result["crew_alive"]
    assert result["repairs_completed"] == 2
    assert not result["leak_active"]
    kinds = [event["kind"] for event in result["events"]]
    for kind in ("purchase", "arrival", "repair_complete", "failure", "inspection", "quarantine"):
        assert kind in kinds
    assert kinds.index("repair_complete") < kinds.index("failure") < kinds.index("inspection")
    assert "defect_after_cycles" not in runs[0].stdout


def test_trade_cli_rejects_invalid_horizon() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "station_control", "trade", "--turns", "0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_offline_economy_story_produces_ships_and_settles_across_four_worlds():
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    run = subprocess.run(
        [sys.executable, "-m", "station_control", "trade", "--economy", "--turns", "20"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["economy"]
    assert set(result["worlds"]) == {"station", "industrial", "ice_moon", "agricultural_world"}
    assert result["model_calls"] == 0
    assert result["crew_alive"]
    assert result["production_batches_completed"] > 0
    assert result["contracts_settled"] > 0
    assert result["revenue"]["ice_moon"] > 0
    assert result["revenue"]["agricultural_world"] > 0
    assert any(value > 0 for value in result["expenditure"].values())
    assert {event["kind"] for event in result["events"]} >= {"purchase", "arrival", "settlement"}
