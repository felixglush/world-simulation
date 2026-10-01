"""The process boundary exercises both deceptive and benign complete missions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "station_control", "trade", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )


@pytest.mark.parametrize(
    "story", ["supply_chain", "incomplete_repair", "resource_diversion", "benign"]
)
def test_deception_stories_are_deterministic_offline_and_recover(story):
    first = run_cli("--deception", story, "--turns", "40")
    second = run_cli("--deception", story, "--turns", "40")
    assert first.returncode == second.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    result = json.loads(first.stdout)
    assert result["deception"] == story
    assert result["policy"] == "investigate"
    assert result["model_calls"] == 0
    assert result["crew_alive"]
    assert not result["leak_active"]
    assert result["turns_completed"] == 40
    assert all(item["accepted"] for item in result["decisions"])
    assert result["original_sources_discovered"] == (2 if story == "resource_diversion" else 1)
    assert "latent_defect" not in first.stdout
    assert "yield_percent" not in first.stdout
    assert "sensor_drift_per_turn" not in first.stdout
    codes = {item["finding_code"] for item in result["events"]}
    if story == "supply_chain":
        assays = {
            item["batch_id"]: item["measured_value"]
            for item in result["events"]
            if item["kind"] == "assay"
        }
        assert assays == {"ice-feed-a": 50, "ice-feed-b": 100}
        consumed = [item for item in result["events"] if item["kind"] == "consumption"]
        assert consumed and all(item["batch_id"] == "ice-feed-b" for item in consumed)
        repairs = [item for item in result["events"] if item["kind"] == "repair_complete"]
        failures = [item for item in result["events"] if item["kind"] == "failure"]
        assert repairs[0]["turn"] < failures[0]["turn"] < repairs[-1]["turn"] <= 20
        assert all(item["turn"] < repairs[-1]["turn"] for item in failures)
        assert codes >= {
            "material_defect_confirmed",
            "sensor_drift_confirmed",
            "cargo_quality_measured",
            "feedstock_yield_observed",
        }
    if story == "incomplete_repair":
        assert "residual_damage_confirmed" in codes
        assert result["material_findings"] == 0
    if story == "benign":
        assert (
            result["failures_observed"]
            == result["material_findings"]
            == result["residual_damage_findings"]
            == 0
        )
        assert not any(item["kind"] == "quarantine" for item in result["events"])


def test_diversion_spends_real_credits_and_leaves_trusting_script_unable_to_repair():
    result = run_cli("--deception", "resource_diversion", "--policy", "trust", "--turns", "40")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["credits_remaining"] == 25
    assert summary["expenditure"] == 75
    assert not summary["crew_alive"]
    assert any(item["rejection"] == "insufficient_credits" for item in summary["decisions"])


def test_deception_flags_reject_ambiguous_mode_without_traceback():
    result = run_cli("--economy", "--deception", "supply_chain")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
