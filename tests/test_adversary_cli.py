"""Adversary configuration, JSONL, replay, and provider wiring through the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2

from station_control import cli
from station_control.persistence import read_run_log


def _completion(name: str, arguments: str = "{}") -> dict[str, Any]:
    return {
        "id": "fake-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "adversary-actual",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "The selected move is useful in the current state.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 45, "completion_tokens": 8, "total_tokens": 53},
    }


def _install_adversary_transport(
    monkeypatch,
    responses: list[dict[str, Any]],
    request_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    real_provider = cli.OpenRouterAdversaryProvider

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request_urls is not None:
            request_urls.append(str(request.url))
        requests.append(json.loads(request.content))
        index = len(requests) - 1
        return httpx2.Response(200, json=responses[index])

    def build(**kwargs):
        client = httpx2.Client(transport=httpx2.MockTransport(respond))
        return real_provider(**kwargs, http_client=client)

    monkeypatch.setattr(cli, "OpenRouterAdversaryProvider", build)
    return requests


def _set_adversary_environment(monkeypatch, *, captain: str | None = None) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-do-not-log")
    monkeypatch.setenv("ADVERSARY_MODEL", "adversary-requested")
    monkeypatch.delenv("CAPTAIN_MODEL", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    if captain is not None:
        monkeypatch.setenv("CAPTAIN_MODEL", captain)


def _events(path: Path, event_type: str) -> list[dict[str, Any]]:
    return [record for record in read_run_log(path)[1:-1] if record["event_type"] == event_type]


def test_rules_with_llm_adversary_logs_replays_and_reruns_saved_configuration(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _set_adversary_environment(monkeypatch)
    monkeypatch.setenv("ADVERSARY_BASE_URL", "https://adversary.test/api/v1")
    request_urls: list[str] = []
    requests = _install_adversary_transport(
        monkeypatch,
        [
            _completion("start_silent_leak"),
            _completion("wait"),
        ],
        request_urls,
    )
    original = tmp_path / "adversary.jsonl"
    rerun = tmp_path / "rerun.jsonl"

    result = cli.main(
        [
            "run",
            "--controller",
            "rules",
            "--adversary",
            "llm",
            "--adversary-budget",
            "2",
            "--scenario",
            "normal",
            "--turns",
            "1",
            "--max-calls",
            "3",
            "--max-output-tokens-per-call",
            "48",
            "--output",
            str(original),
        ]
    )
    assert result == 0, capsys.readouterr().err

    original_records = read_run_log(original)
    original_metadata = original_records[0]["metadata"]
    assert original_metadata["controller"] == "rules"
    assert original_metadata["adversary"] == "llm"
    assert original_metadata["adversary_disruption_budget"] == 2
    assert original_metadata["provider_configuration"]["adversary_base_url"] == (
        "https://adversary.test/api/v1"
    )
    assert original_metadata["models"] == {
        "captain": None,
        "jev": None,
        "adversary": "adversary-requested",
    }
    first_decision = _events(original, "adversary_decision")[0]
    assert first_decision["decision"]["action"]["kind"] == "start_silent_leak"
    assert first_decision["consequence"]["accepted"] is True
    assert requests[0]["model"] == "adversary-requested"
    assert request_urls[0] == "https://adversary.test/api/v1/chat/completions"

    requests_before_replay = len(requests)
    replay_result = cli.main(["replay", str(original)])
    assert replay_result == 0
    replay_output = capsys.readouterr().out
    assert "adversary_decision" in replay_output
    assert "start_silent_leak" in replay_output
    assert len(requests) == requests_before_replay

    rerun_result = cli.main(
        [
            "rerun",
            str(original),
            "--controller",
            "rules",
            "--turns",
            "1",
            "--max-calls",
            "3",
            "--max-output-tokens-per-call",
            "48",
            "--output",
            str(rerun),
        ]
    )
    assert rerun_result == 0, capsys.readouterr().err
    rerun_records = read_run_log(rerun)
    assert rerun_records[0]["run_id"] != original_records[0]["run_id"]
    assert rerun_records[0]["metadata"]["adversary"] == "llm"
    assert rerun_records[0]["metadata"]["adversary_disruption_budget"] == 2
    assert _events(rerun, "adversary_decision")[0]["decision"]["action"]["kind"] == "wait"
    assert len(requests) == 2

    disabled = tmp_path / "adversary-disabled.jsonl"
    monkeypatch.delenv("ADVERSARY_MODEL")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    disabled_result = cli.main(
        [
            "rerun",
            str(original),
            "--controller",
            "rules",
            "--adversary",
            "off",
            "--turns",
            "1",
            "--output",
            str(disabled),
        ]
    )
    assert disabled_result == 0
    disabled_records = read_run_log(disabled)
    assert disabled_records[0]["metadata"]["adversary"] == "off"
    assert _events(disabled, "adversary_decision") == []
    assert len(requests) == 2


def test_missing_adversary_model_fails_before_reserving_output(tmp_path: Path, monkeypatch, capsys):
    _set_adversary_environment(monkeypatch)
    monkeypatch.delenv("ADVERSARY_MODEL")
    output = tmp_path / "must-not-exist.jsonl"

    result = cli.main(
        [
            "run",
            "--controller",
            "rules",
            "--adversary",
            "llm",
            "--max-calls",
            "3",
            "--max-output-tokens-per-call",
            "48",
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert "ADVERSARY_MODEL" in capsys.readouterr().err
    assert not output.exists()


def test_live_adversary_requires_output_token_limit_before_reserving_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _set_adversary_environment(monkeypatch)
    output = tmp_path / "no-output-limit.jsonl"

    result = cli.main(
        [
            "run",
            "--controller",
            "rules",
            "--adversary",
            "llm",
            "--max-calls",
            "3",
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert "--max-output-tokens-per-call" in capsys.readouterr().err
    assert not output.exists()


def test_new_missions_default_to_adversary_off_with_three_disruption_points(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ADVERSARY_MODEL", raising=False)
    output = tmp_path / "offline-default.jsonl"

    result = cli.main(
        [
            "run",
            "--controller",
            "rules",
            "--turns",
            "1",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    metadata = read_run_log(output)[0]["metadata"]
    assert metadata["adversary"] == "off"
    assert metadata["adversary_disruption_budget"] == 3
    assert metadata["models"]["adversary"] is None


def test_adversary_and_captain_share_one_call_budget(tmp_path: Path, monkeypatch) -> None:
    _set_adversary_environment(monkeypatch, captain="captain-requested")
    adversary_requests = _install_adversary_transport(monkeypatch, [_completion("wait")])
    captain_requests: list[httpx2.Request] = []
    real_captain_provider = cli.OpenRouterCaptainProvider

    def captain_respond(request: httpx2.Request) -> httpx2.Response:
        captain_requests.append(request)
        return httpx2.Response(500, json={"error": "the shared budget should block this call"})

    def build_captain(**kwargs):
        client = httpx2.Client(transport=httpx2.MockTransport(captain_respond))
        return real_captain_provider(**kwargs, http_client=client)

    monkeypatch.setattr(cli, "OpenRouterCaptainProvider", build_captain)
    scenario = tmp_path / "turn_one_leak.yaml"
    scenario.write_text(
        "schema_version: 1\n"
        "id: turn_one_leak\n"
        "description: Leak starts on the first turn.\n"
        "initial:\n  oxygen: 800\n"
        "events:\n  - turn: 1\n    kind: leak_start\n",
        encoding="utf-8",
    )
    output = tmp_path / "shared-budget.jsonl"

    result = cli.main(
        [
            "run",
            "--controller",
            "llm",
            "--adversary",
            "llm",
            "--scenario-file",
            str(scenario),
            "--turns",
            "1",
            "--max-calls",
            "1",
            "--max-output-tokens-per-call",
            "48",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert len(adversary_requests) == 1
    assert captain_requests == []
    provider_failures = _events(output, "provider_failure")
    assert provider_failures
    assert provider_failures[0]["consequence"]["provider"] == "captain"
    assert provider_failures[0]["consequence"]["code"] == "budget_exhausted"


def test_adversary_constructor_failure_closes_captain_and_hides_details(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _set_adversary_environment(monkeypatch, captain="captain-requested")

    class FakeCaptain:
        closed = False

        def close(self) -> None:
            self.closed = True

    captain = FakeCaptain()
    monkeypatch.setattr(cli, "OpenRouterCaptainProvider", lambda **_: captain)

    def fail_constructor(**_):
        raise RuntimeError("test-key-do-not-log")

    monkeypatch.setattr(cli, "OpenRouterAdversaryProvider", fail_constructor)
    output = tmp_path / "constructor-failure.jsonl"

    result = cli.main(
        [
            "run",
            "--controller",
            "llm",
            "--adversary",
            "llm",
            "--turns",
            "1",
            "--max-calls",
            "3",
            "--max-output-tokens-per-call",
            "48",
            "--output",
            str(output),
        ]
    )

    assert result == 2
    error = capsys.readouterr().err
    assert "test-key-do-not-log" not in error
    assert captain.closed is True
    assert read_run_log(output)[-1]["status"] == "failed"
