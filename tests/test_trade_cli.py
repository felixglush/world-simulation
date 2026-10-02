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
    assert all(
        decision["accepted"] and decision["rejection"] is None for decision in result["decisions"]
    )

    events = result["events"]

    def event_index(kind: str, batch_id: str | None = None) -> int:
        return next(
            index
            for index, event in enumerate(events)
            if event["kind"] == kind and (batch_id is None or event["batch_id"] == batch_id)
        )

    story_order = (
        event_index("purchase", "industrial-batch-a"),
        event_index("arrival", "industrial-batch-a"),
        event_index("settlement", "industrial-batch-a"),
        event_index("repair_complete", "industrial-batch-a"),
        event_index("failure"),
        event_index("inspection"),
        event_index("quarantine"),
        event_index("purchase", "industrial-batch-b"),
        event_index("arrival", "industrial-batch-b"),
        event_index("settlement", "industrial-batch-b"),
        event_index("repair_complete", "industrial-batch-b"),
    )
    assert story_order == tuple(sorted(story_order))
    assert events[story_order[5]]["finding_code"] == "material_defect_confirmed"
    assert "latent_defect" not in runs[0].stdout
    assert "defect_after_turns" not in runs[0].stdout


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
