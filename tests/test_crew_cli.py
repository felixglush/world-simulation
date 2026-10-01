"""Process-level coverage for live crew control of deception stories."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_crew_governor import scripted_intervention
from test_providers import _captain_payload, _jev_payload

ROOT = Path(__file__).resolve().parents[1]


def _fake_provider_server() -> tuple[ThreadingHTTPServer, threading.Thread, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            requests.append(body)
            if "messages" in body:
                payload = json.loads(body["messages"][1]["content"])
                name, arguments = scripted_intervention(payload["world"])
                response = _captain_payload(
                    model="captain-returned",
                    name=name,
                    arguments=json.dumps(arguments),
                )
            else:
                turn = body["state"]["world"]["turn"]
                response = _jev_payload(model="jev-returned", urgency=3 if turn <= 22 else 0)
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, requests


@pytest.mark.parametrize(
    ("controller", "jev_model"),
    (("llm", None), ("jev+llm", "jev-live-test")),
)
def test_live_crew_runs_full_supply_chain_through_real_cli_and_local_providers(
    controller: str, jev_model: str | None
) -> None:
    server, thread, requests = _fake_provider_server()
    host, port = server.server_address
    env = os.environ.copy()
    env.update(
        {
            "OPENROUTER_API_KEY": "test-secret-do-not-print",
            "CAPTAIN_BASE_URL": f"http://{host}:{port}/api/v1",
            "JEV_BASE_URL": f"http://{host}:{port}/api",
        }
    )
    for key in ("CAPTAIN_MODEL", "JEV_MODEL", "ADVERSARY_MODEL"):
        env.pop(key, None)
    command = [
        sys.executable,
        "-m",
        "station_control",
        "trade",
        "--deception",
        "supply_chain",
        "--controller",
        controller,
        "--captain-model",
        "captain-live-test",
        "--max-calls",
        "120",
        "--max-output-tokens-per-call",
        "512",
        "--turns",
        "40",
    ]
    if jev_model is not None:
        command.extend(("--jev-model", jev_model))
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["controller"] == controller
    assert summary["policy"] == "crew"
    assert summary["deception"] == "supply_chain"
    assert summary["turns_completed"] == 40
    assert summary["crew_alive"]
    assert not summary["leak_active"]
    assert summary["repairs_completed"] >= 2
    assert summary["original_sources_discovered"] == 1
    assert summary["material_findings"] >= 1
    assert summary["failures_observed"] >= 1
    codes = {item["finding_code"] for item in summary["events"]}
    assert codes >= {
        "material_defect_confirmed",
        "sensor_drift_confirmed",
        "report_origin_traced",
        "cargo_quality_measured",
    }
    consumed = [item for item in summary["events"] if item["kind"] == "consumption"]
    assert consumed and all(item["batch_id"] == "ice-feed-b" for item in consumed)
    assert all(item["accepted"] for item in summary["decisions"])
    assert summary["models"] == {
        "captain": "captain-live-test",
        "jev": jev_model,
        "adversary": None,
    }
    assert summary["call_budget"] == {
        "max_calls": 120,
        "max_output_tokens_per_call": 512,
    }
    assert requests
    jev_requests = [body for body in requests if "messages" not in body]
    assert bool(jev_requests) == (controller == "jev+llm")
    captain_turns = [
        json.loads(body["messages"][1]["content"])["world"]["turn"]
        for body in requests
        if "messages" in body
    ]
    assert 23 in captain_turns
    assert not any(24 <= turn <= 39 for turn in captain_turns)
    assert 40 in captain_turns
    assert "test-secret-do-not-print" not in result.stdout + result.stderr
    for private in ("latent_defect", "yield_percent", "sensor_drift_per_turn"):
        assert private not in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--controller", "llm"), "Live crew control requires --deception"),
        (
            ("--deception", "supply_chain", "--policy", "trust", "--controller", "llm"),
            "Live crew control requires --policy investigate",
        ),
        (
            ("--deception", "supply_chain", "--controller", "llm"),
            "Live agents require a finite call budget",
        ),
    ],
)
def test_live_crew_rejects_unsupported_settings_before_provider_configuration(
    args, message
) -> None:
    env = os.environ.copy()
    env["OPENROUTER_API_KEY"] = "test-secret-do-not-print"
    result = subprocess.run(
        [sys.executable, "-m", "station_control", "trade", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert "test-secret-do-not-print" not in result.stdout + result.stderr
