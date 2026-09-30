"""Offline integration tests for the adversary's OpenRouter tool boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx2
import pytest

from station_control.adversary import (
    ADVERSARY_ACTION_CATALOG,
    AdversaryAction,
    AdversaryActionKind,
    AdversaryContext,
    AdversaryDecision,
    AdversaryDelivery,
)
from station_control.adversary_provider import OpenRouterAdversaryProvider
from station_control.controllers import ProviderError, ProviderErrorCode
from station_control.domain import SensorReading
from station_control.providers import CallBudget


def _completion(
    name: str = "mask_sensor",
    arguments: str = '{"target":"sensor_a"}',
    *,
    calls: list[dict[str, Any]] | None = None,
    model: str = "adversary-actual",
) -> dict[str, Any]:
    tool_calls = calls
    if tool_calls is None:
        tool_calls = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ]
    return {
        "id": "fake-completion",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "This disrupts the station's independent readings.",
                    "tool_calls": tool_calls,
                },
            }
        ],
        "usage": {"prompt_tokens": 61, "completion_tokens": 14, "total_tokens": 75},
    }


def _context() -> AdversaryContext:
    actions = tuple(
        replace(item, target_options=("sensor_a",))
        if item.kind is AdversaryActionKind.MASK_SENSOR
        else item
        for item in ADVERSARY_ACTION_CATALOG
        if item.kind in {AdversaryActionKind.WAIT, AdversaryActionKind.MASK_SENSOR}
    )
    return AdversaryContext(
        turn=4,
        oxygen=693,
        oxygen_capacity=1000,
        generation_rate=70,
        consumption_rate=60,
        leak_active=False,
        leak_rate=36,
        repair_turns_remaining=0,
        sensor_mode="independent",
        sensor_fault=None,
        sensor_readings=(
            SensorReading("sensor_a", 693, sampled_turn=4, source="sensor_a"),
            SensorReading("sensor_b", 693, sampled_turn=4, source="sensor_b"),
        ),
        backup_oxygen=120,
        backup_active=False,
        parts=2,
        credits=100,
        available_crew=4,
        pending_deliveries=(AdversaryDelivery("oxygen", 2, 7),),
        disruption_budget_remaining=2,
        allowed_actions=actions,
    )


def _provider(
    handler: Callable[[httpx2.Request], httpx2.Response],
    budget: CallBudget | None = None,
) -> OpenRouterAdversaryProvider:
    return OpenRouterAdversaryProvider(
        api_key="test-key-do-not-log",
        model="adversary-requested",
        budget=budget or CallBudget(max_calls=4, max_output_tokens_per_call=96),
        base_url="https://openrouter.test/api/v1",
        timeout=1.0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _reply(payload: dict[str, Any], status: int = 200):
    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=payload)

    return handle


def test_provider_sends_current_truth_and_only_eligible_fixed_tools() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion())

    provider = _provider(handle, CallBudget(max_calls=1, max_output_tokens_per_call=72))
    try:
        decision = provider.decide(_context())
    finally:
        provider.close()

    assert isinstance(decision, AdversaryDecision)
    assert decision.action == AdversaryAction(AdversaryActionKind.MASK_SENSOR, target="sensor_a")
    assert decision.rationale == "This disrupts the station's independent readings."
    assert decision.metadata["model"] == "adversary-actual"
    assert decision.metadata["max_output_tokens"] == 72
    body = requests[0]
    assert body["tool_choice"] == "required"
    assert body["max_tokens"] == 72
    tool_names = [item["function"]["name"] for item in body["tools"]]
    assert tool_names == ["wait", "mask_sensor"]
    sensor_schema = body["tools"][1]["function"]["parameters"]["properties"]["target"]
    assert sensor_schema["enum"] == ["sensor_a"]
    user_payload = json.loads(body["messages"][1]["content"])
    truth = user_payload["current_truth"]
    assert set(truth) == {
        "turn",
        "oxygen",
        "oxygen_capacity",
        "generation_rate",
        "consumption_rate",
        "leak_active",
        "leak_rate",
        "repair_turns_remaining",
        "sensor_mode",
        "sensor_fault",
        "sensor_readings",
        "backup_oxygen",
        "backup_active",
        "parts",
        "credits",
        "available_crew",
        "pending_deliveries",
    }
    assert truth["oxygen"] == 693
    assert truth["leak_active"] is False
    assert truth["repair_turns_remaining"] == 0
    assert truth["sensor_mode"] == "independent"
    assert truth["sensor_fault"] is None
    assert truth["sensor_readings"][0]["sampled_turn"] == 4
    assert truth["pending_deliveries"] == [{"supply": "oxygen", "quantity": 2, "due_turn": 7}]
    assert user_payload["disruption_budget_remaining"] == 2
    assert "scheduled_events" not in json.dumps(user_payload)
    system_prompt = body["messages"][0]["content"].lower()
    assert "maximize station disruption" in system_prompt
    assert "wait" in system_prompt


@pytest.mark.parametrize(
    "payload",
    [
        _completion(name="start_silent_leak", arguments="{}"),
        _completion(arguments='{"target":"sensor_b"}'),
        _completion(arguments='{"target":"sensor_a","quantity":99}'),
        _completion(calls=[]),
        _completion(
            calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "wait", "arguments": "{}"},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "wait", "arguments": "{}"},
                },
            ]
        ),
    ],
    ids=("ineligible-action", "ineligible-target", "extra-argument", "no-tool-call", "two-calls"),
)
def test_malformed_or_ineligible_tool_responses_never_become_actions(
    payload: dict[str, Any],
) -> None:
    provider = _provider(_reply(payload))
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_context())
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_budget_exhaustion_does_not_make_a_second_request() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_completion("wait", "{}"))

    provider = _provider(handle, CallBudget(max_calls=1, max_output_tokens_per_call=32))
    try:
        provider.decide(_context())
        with pytest.raises(ProviderError) as error:
            provider.decide(_context())
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.BUDGET_EXHAUSTED
    assert len(requests) == 1


def test_transport_failure_hides_credentials_and_provider_details() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("test-key-do-not-log leaked by remote")

    provider = _provider(fail)
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_context())
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.PROVIDER_UNAVAILABLE
    assert "test-key-do-not-log" not in str(error.value)
    assert error.value.__cause__ is None
