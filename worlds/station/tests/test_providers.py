"""Offline integration tests at the model-provider HTTP boundaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx2
import pytest

from station_control.application import (
    ControllerMode,
    EvidenceAccess,
    MissionConfig,
    MissionStatus,
    run_mission,
)
from station_control.controllers import (
    ActionDescriptor,
    ActionRequestKind,
    CaptainContext,
    DispatchContext,
    Evidence,
    NoulOutcome,
    ProviderError,
    ProviderErrorCode,
    SensorReading,
    StationView,
    Subsystem,
)
from station_control.providers import CallBudget, JevDispatchProvider, OpenRouterCaptainProvider
from station_control.scenarios import ScenarioFamily
from station_control.trade import create_world_state


def _jev_payload(
    *,
    model: str = "jev-actual",
    subsystem: str = "life_support",
    safeguard: float = 0.1,
    diagnosis: float = 0.9,
    urgency: float = 1.6,
) -> dict[str, Any]:
    return {
        "model": model,
        "answers": {
            "subsystem": {
                "type": "choice",
                "choice": subsystem,
                "confidence": 0.88,
                "probabilities": {
                    "life_support": 0.88,
                    "power": 0.04,
                    "logistics": 0.04,
                    "unknown": 0.04,
                },
            },
            "safeguard_request": {"type": "noul", "noul": safeguard},
            "diagnosis_supported": {"type": "noul", "noul": diagnosis},
            "urgency": {
                "type": "score",
                "score": urgency,
                "confidence": 0.8,
                "legend": {
                    str(index): label
                    for index, label in enumerate(
                        ("routine", "low", "moderate", "high", "emergency")
                    )
                },
                "probabilities": {str(index): 0.2 for index in range(5)},
            },
        },
        "usage": {"input_tokens": 35, "output_tokens": 8},
    }


def _captain_payload(
    *,
    model: str = "captain-actual",
    name: str = "inspect",
    arguments: str = '{"target":"oxygen_system"}',
    content: str = "Check the oxygen system first.",
    cost: float | None = 0.0002,
) -> dict[str, Any]:
    usage: dict[str, Any] = {"prompt_tokens": 42, "completion_tokens": 13, "total_tokens": 55}
    if cost is not None:
        usage["cost"] = cost
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
                    "content": content,
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
        "usage": usage,
    }


def _reply(
    payload: dict[str, Any], status: int = 200
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=payload)

    return handle


def _dispatch_context() -> DispatchContext:
    report = Evidence(12, 4, "maintenance", "Pressure is falling near the oxygen manifold.")
    return DispatchContext(
        report=report,
        station=StationView(
            4, (SensorReading("sensor_a", 68), SensorReading("sensor_b", 70)), 12, 2, 30, 4
        ),
        evidence=(report,),
        question_version="jev-event-questions-v2",
        rubric_version="jev-event-rubric-v2",
    )


def _captain_context(action: ActionDescriptor | None = None) -> CaptainContext:
    incident = Evidence(12, 4, "maintenance", "Pressure is falling near the oxygen manifold.")
    return CaptainContext(
        incident=incident,
        station=StationView(
            4, (SensorReading("sensor_a", 68), SensorReading("sensor_b", 70)), 12, 2, 30, 4
        ),
        evidence=(incident,),
        allowed_actions=(
            action
            if action is not None
            else ActionDescriptor(
                ActionRequestKind.INSPECT,
                "Inspect accessible life-support equipment.",
                {
                    "type": "object",
                    "properties": {"target": {"type": "string", "enum": ["oxygen_system"]}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
        ),
        inspection_budget_remaining=1,
        instruction_version="captain-event-instructions-v2",
    )


def _jev(
    handler: Callable[[httpx2.Request], httpx2.Response], budget: CallBudget | None = None
) -> JevDispatchProvider:
    return JevDispatchProvider(
        api_key="test-key-do-not-log",
        model="jev-requested",
        base_url="https://typesafe.test/api",
        timeout=1.0,
        budget=budget or CallBudget(max_calls=4, max_output_tokens_per_call=128),
        transport=httpx2.MockTransport(handler),
    )


def _captain(
    handler: Callable[[httpx2.Request], httpx2.Response], budget: CallBudget | None = None
) -> OpenRouterCaptainProvider:
    return OpenRouterCaptainProvider(
        api_key="test-key-do-not-log",
        model="captain-requested",
        base_url="https://openrouter.test/api/v1",
        timeout=1.0,
        budget=budget or CallBudget(max_calls=4, max_output_tokens_per_call=96),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.25, NoulOutcome.NO),
        (0.5, NoulOutcome.UNCERTAIN),
        (0.75, NoulOutcome.YES),
    ],
)
def test_jev_preserves_negative_uncertain_and_positive_noul_outcomes(
    probability: float, expected: NoulOutcome
) -> None:
    provider = _jev(_reply(_jev_payload(safeguard=probability, diagnosis=probability)))
    try:
        result = provider.classify(_dispatch_context())
    finally:
        provider.close()

    assert result.safeguard_request is expected
    assert result.diagnosis_supported is expected


def test_jev_asks_four_distinct_questions_and_reports_actual_usage_and_versions() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_jev_payload())

    provider = _jev(handle)
    try:
        result = provider.classify(_dispatch_context())
    finally:
        provider.close()

    body = requests[0]
    assert set(body["questions"]) == {
        "subsystem",
        "safeguard_request",
        "diagnosis_supported",
        "urgency",
    }
    assert {key: body["questions"][key]["type"] for key in body["questions"]} == {
        "subsystem": "choice",
        "safeguard_request": "noul",
        "diagnosis_supported": "noul",
        "urgency": "score",
    }
    assert body["state"]["report"]["message"] == _dispatch_context().report.message
    assert result.subsystem is Subsystem.LIFE_SUPPORT
    assert result.urgency == 40
    assert result.metadata["model"] == "jev-actual"
    assert result.metadata["input_tokens"] == 35
    assert result.metadata["output_tokens"] == 8
    assert result.metadata["cost_usd"] is None
    assert result.metadata["calls"] == 1
    assert result.metadata["timeout_seconds"] == 1.0
    assert result.metadata["question_version"] == "jev-event-questions-v2"
    assert result.metadata["rubric_version"] == "jev-event-rubric-v2"
    assert result.metadata["latency_ms"] >= 0


@pytest.mark.parametrize(
    "payload",
    [
        _jev_payload(subsystem="unknown-label"),
        {
            **_jev_payload(),
            "answers": {
                key: value
                for key, value in _jev_payload()["answers"].items()
                if key != "diagnosis_supported"
            },
        },
        _jev_payload(safeguard=1.2),
        _jev_payload(urgency=5.0),
    ],
)
def test_jev_rejects_malformed_or_out_of_range_judgments_without_provider_details(
    payload: dict[str, Any],
) -> None:
    provider = _jev(_reply(payload))
    try:
        with pytest.raises(ProviderError) as error:
            provider.classify(_dispatch_context())
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE
    assert "test-key-do-not-log" not in str(error.value)


def test_jev_failure_is_sanitized_and_not_retried() -> None:
    requests = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(503, json={"error": "private upstream detail test-key-do-not-log"})

    provider = _jev(handle)
    try:
        with pytest.raises(ProviderError) as error:
            provider.classify(_dispatch_context())
    finally:
        provider.close()

    assert requests == 1
    assert error.value.code is ProviderErrorCode.PROVIDER_UNAVAILABLE
    assert error.value.metadata["provider"] == "typesafe"
    assert error.value.metadata["model"] == "jev-requested"
    assert error.value.metadata["calls"] == 1
    assert error.value.metadata["request_made"] is True
    assert error.value.metadata["latency_ms"] >= 0
    assert "private upstream detail" not in str(error.value)
    assert "test-key-do-not-log" not in str(error.value)


def test_captain_receives_only_allowed_tools_and_returns_a_validated_action() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_captain_payload())

    provider = _captain(handle)
    try:
        result = provider.decide(_captain_context())
    finally:
        provider.close()

    body = requests[0]
    assert body["model"] == "captain-requested"
    assert body["max_tokens"] == 96
    assert body["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in body["tools"]] == ["inspect"]
    serialized_prompt = json.dumps(body["messages"])
    assert "leak_active" not in serialized_prompt
    assert "scenario_family" not in serialized_prompt
    assert "seed" not in serialized_prompt
    assert result.action.kind is ActionRequestKind.INSPECT
    assert result.action.target == "oxygen_system"
    assert result.metadata["model"] == "captain-actual"
    assert result.metadata["input_tokens"] == 42
    assert result.metadata["output_tokens"] == 13
    assert result.metadata["cost_usd"] == 0.0002
    assert result.metadata["calls"] == 1
    assert result.metadata["max_output_tokens"] == 96
    assert result.metadata["timeout_seconds"] == 1.0
    assert result.metadata["instruction_version"] == "captain-event-instructions-v2"
    assert result.metadata["latency_ms"] >= 0


def test_captain_projection_includes_only_public_sensor_reading_provenance() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_captain_payload())

    context = _captain_context()
    station = replace(
        context.station,
        oxygen_sensors=(
            SensorReading("sensor_a", 68, sampled_turn=3, source="sensor_a"),
            SensorReading("sensor_b", 70, sampled_turn=3, source="sensor_b"),
        ),
    )
    provider = _captain(handle)
    try:
        provider.decide(replace(context, station=station))
    finally:
        provider.close()

    serialized_prompt = requests[0]["messages"][1]["content"]
    assert '"sampled_turn":3' in serialized_prompt
    assert '"source":"sensor_a"' in serialized_prompt
    assert '"source":"sensor_b"' in serialized_prompt
    assert "PRIVATE_SCENARIO_DESCRIPTION" not in serialized_prompt
    assert "scenario_definition" not in serialized_prompt


def test_captain_accepts_no_argument_action_schema() -> None:
    action = ActionDescriptor(
        ActionRequestKind.READ_HISTORY,
        "Read accessible history.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_captain_payload(name="read_history", arguments="{}"))

    provider = _captain(handle)
    try:
        result = provider.decide(_captain_context(action))
    finally:
        provider.close()

    schema = requests[0]["tools"][0]["function"]["parameters"]
    assert result.action.kind is ActionRequestKind.READ_HISTORY
    assert result.action.target is None
    assert schema["properties"] == {}
    assert "required" not in schema


def test_captain_rejects_union_property_schema_before_network_io() -> None:
    action = ActionDescriptor(
        ActionRequestKind.INSPECT,
        "Inspect accessible life-support equipment.",
        {
            "type": "object",
            "properties": {"target": {"type": ["string", "null"]}},
            "required": ["target"],
            "additionalProperties": False,
        },
    )
    requests = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(200, json=_captain_payload())

    provider = _captain(handle)
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_captain_context(action))
    finally:
        provider.close()

    assert requests == 0
    assert error.value.code is ProviderErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        (
            ActionDescriptor(
                ActionRequestKind.ORDER_SUPPLIES,
                "Order bounded supplies.",
                {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "enum": ["oxygen", "parts"]},
                        "quantity": {"type": "integer", "minimum": 1, "maximum": 3},
                    },
                    "required": ["target", "quantity"],
                    "additionalProperties": False,
                },
            ),
            {"target": "oxygen", "quantity": True},
        ),
        (
            ActionDescriptor(
                ActionRequestKind.CLOSE,
                "Close with cited evidence.",
                {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "minLength": 1},
                        "evidence_sequences": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["reason", "evidence_sequences"],
                    "additionalProperties": False,
                },
            ),
            {"reason": "Verified repair", "evidence_sequences": [True]},
        ),
    ],
)
def test_captain_rejects_boolean_as_integer_action_value(
    action: ActionDescriptor, arguments: dict[str, Any]
) -> None:
    provider = _captain(
        _reply(_captain_payload(name=action.kind.value, arguments=json.dumps(arguments)))
    )
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_captain_context(action))
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("unknown_action", '{"target":"oxygen_system"}'),
        ("inspect", '{"target":"oxygen_secret"}'),
        ("inspect", '{"target":"oxygen_system","extra":"unexpected"}'),
        ("inspect", "not-json"),
    ],
)
def test_captain_rejects_unknown_tools_and_invalid_payloads(name: str, arguments: str) -> None:
    provider = _captain(_reply(_captain_payload(name=name, arguments=arguments)))
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_captain_context())
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE
    assert "test-key-do-not-log" not in str(error.value)


def test_captain_rejects_unstructured_completion() -> None:
    payload = _captain_payload()
    payload["choices"][0]["message"]["tool_calls"] = []
    provider = _captain(_reply(payload))
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_captain_context())
    finally:
        provider.close()

    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_captain_input_size_limit_fails_before_network_io() -> None:
    requests = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(200, json=_captain_payload())

    context = _captain_context()
    large_incident = Evidence(12, 4, "maintenance", "x" * 40_001)
    context = CaptainContext(
        incident=large_incident,
        station=context.station,
        evidence=(large_incident,),
        allowed_actions=context.allowed_actions,
        inspection_budget_remaining=context.inspection_budget_remaining,
        instruction_version=context.instruction_version,
    )
    provider = _captain(handle)
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(context)
    finally:
        provider.close()

    assert requests == 0
    assert error.value.code is ProviderErrorCode.INVALID_INPUT


def test_captain_provider_failure_is_sanitized_and_not_retried() -> None:
    requests = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(503, json={"error": "private upstream detail test-key-do-not-log"})

    provider = _captain(handle)
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_captain_context())
    finally:
        provider.close()

    assert requests == 1
    assert error.value.code is ProviderErrorCode.PROVIDER_UNAVAILABLE
    assert error.value.metadata["provider"] == "openrouter"
    assert error.value.metadata["model"] == "captain-requested"
    assert error.value.metadata["calls"] == 1
    assert error.value.metadata["request_made"] is True
    assert error.value.metadata["latency_ms"] >= 0
    assert "private upstream detail" not in str(error.value)
    assert "test-key-do-not-log" not in str(error.value)


def test_shared_budget_stops_before_a_second_network_call() -> None:
    requests = 0
    budget = CallBudget(max_calls=1, max_output_tokens_per_call=80)

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        if request.url.host == "openrouter.test":
            return httpx2.Response(200, json=_captain_payload())
        return httpx2.Response(200, json=_jev_payload())

    captain = _captain(handle, budget)
    jev = _jev(handle, budget)
    try:
        captain.decide(_captain_context())
        with pytest.raises(ProviderError) as error:
            jev.classify(_dispatch_context())
    finally:
        captain.close()
        jev.close()

    assert requests == 1
    assert error.value.code is ProviderErrorCode.BUDGET_EXHAUSTED
    assert error.value.metadata["calls"] == 0
    assert error.value.metadata["request_made"] is False


@pytest.mark.parametrize("evidence_access", [EvidenceAccess.LATEST, EvidenceAccess.HISTORY])
def test_openrouter_captain_repairs_leak_through_real_mission_loop(
    evidence_access: EvidenceAccess,
) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        observed = json.loads(body["messages"][1]["content"])
        requests.append({"body": body, "observed": observed})
        evidence = observed["accessible_evidence"]
        completed = next((item for item in evidence if item["kind"] == "repair_complete"), None)
        inspection = any(
            item["kind"] == "inspection" and "active oxygen leak" in item["message"].lower()
            for item in evidence
        )
        if completed is not None:
            name = "close"
            arguments = {
                "reason": "repair completion was observed",
                "evidence_sequences": [completed["sequence"]],
            }
        elif inspection:
            name = "assign_repair"
            arguments = {"target": "oxygen_system"}
        else:
            name = "inspect"
            arguments = {"target": "oxygen_system"}
        return httpx2.Response(
            200,
            json=_captain_payload(name=name, arguments=json.dumps(arguments)),
        )

    provider = _captain(
        handle,
        CallBudget(max_calls=4, max_output_tokens_per_call=96),
    )
    try:
        result = run_mission(
            MissionConfig(
                scenario=ScenarioFamily.LEAK,
                seed=3,
                duration_turns=12,
                controller_mode=ControllerMode.LLM,
                evidence_access=evidence_access,
            ),
            captain=provider,
        )
    finally:
        provider.close()

    decisions = [
        event["decision"] for event in result.events if event["event_type"] == "captain_decision"
    ]
    assert [decision["kind"] for decision in decisions] == [
        "inspect",
        "assign_repair",
        "close",
    ]
    assert len(requests) == 3
    assert result.evaluation.metrics["model_calls"] == len(requests)
    for request in requests:
        assert all(
            "type" in property_schema
            for tool in request["body"]["tools"]
            for property_schema in tool["function"]["parameters"]["properties"].values()
        )

    assignment = next(
        event
        for event in result.events
        if event["event_type"] == "action" and event["decision"]["kind"] == "assign_repair"
    )
    repair_complete = next(
        event
        for event in result.events
        if event["event_type"] == "world_evidence"
        and event["evidence"]["kind"] == "repair_complete"
    )
    follow_up = next(
        event
        for event in result.events
        if event["event_type"] == "follow_up_scheduled"
        and event["turn"] == assignment["turn"]
        and event["consequence"]["reason"] == "physical_action"
    )
    close = next(event for event in result.events if event["event_type"] == "incident_closed")
    accepted_actions = [event for event in result.events if event["event_type"] == "action"]
    assert all(event["consequence"]["accepted"] is True for event in accepted_actions)
    assert follow_up["decision"]["follow_up_turn"] == assignment["turn"] + 2
    assert repair_complete["turn"] == follow_up["decision"]["follow_up_turn"]
    assert close["turn"] == repair_complete["turn"]
    assert close["evidence"][0]["sequence"] == decisions[-1]["evidence_sequences"][0]
    assert not any(event["event_type"] == "provider_failure" for event in result.events)
    assert result.status is MissionStatus.COMPLETED
    assert result.debug_snapshots[-1].crew_alive is True
    assert result.debug_snapshots[-1].leak_active is False
    assert (
        next(
            snapshot for snapshot in result.debug_snapshots if snapshot.turn == assignment["turn"]
        ).leak_active
        is True
    )
    assert (
        next(
            snapshot
            for snapshot in result.debug_snapshots
            if snapshot.turn == repair_complete["turn"]
        ).leak_active
        is False
    )


@pytest.mark.parametrize("ambiguous", ["duplicate_arguments", "multiple_choices"])
def test_captain_rejects_ambiguous_decisions(ambiguous):
    payload = _captain_payload()
    if ambiguous == "duplicate_arguments":
        payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
            '{"target":"sensor_a","target":"oxygen_system"}'
        )
    else:
        payload["choices"].append(payload["choices"][0])
    provider = _captain(_reply(payload))
    try:
        with pytest.raises(ProviderError) as error:
            provider.decide(_captain_context())
    finally:
        provider.close()
    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE
    assert error.value.metadata["request_made"] is True


@pytest.mark.parametrize("max_calls", [2, 3])
def test_event_screening_and_captain_actions_share_budget_through_real_adapters(max_calls):
    jev_requests = []
    captain_requests = []

    def classify(request):
        jev_requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_jev_payload(urgency=4.0))

    def decide(request):
        captain_requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_captain_payload())

    budget = CallBudget(max_calls=max_calls, max_output_tokens_per_call=96)
    jev = _jev(classify, budget)
    captain = _captain(decide, budget)
    try:
        result = run_mission(
            MissionConfig(
                scenario=ScenarioFamily.NORMAL,
                duration_turns=1,
                controller_mode=ControllerMode.JEV_LLM,
            ),
            dispatcher=jev,
            captain=captain,
        )
    finally:
        jev.close()
        captain.close()

    assert len(captain_requests) == 1
    assert len(jev_requests) == max_calls - 1
    assert result.evaluation.metrics["model_calls"] == max_calls
    assert [event["event_type"] for event in jev_requests[0]["state"]["public_events"]] == [
        "station_observation"
    ]
    observed = json.loads(captain_requests[0]["messages"][1]["content"])
    assert observed["public_events"] == jev_requests[0]["state"]["public_events"]
    observation = json.loads(jev_requests[0]["state"]["public_events"][0]["payload"])
    assert "evidence" not in observation["evidence"]
    if max_calls == 3:
        assert [event["event_type"] for event in jev_requests[1]["state"]["public_events"]] == [
            "action"
        ]
        assert not any(event["event_type"] == "provider_failure" for event in result.events)
    else:
        failure = next(
            event for event in result.events if event["event_type"] == "provider_failure"
        )
        assert failure["consequence"]["code"] == "budget_exhausted"
        assert failure["consequence"]["metadata"]["request_made"] is False
        assert [event["event_type"] for event in failure["evidence"]] == ["action"]


def test_providers_reject_authoritative_world_state_before_http_io() -> None:
    requests = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(200, json=_jev_payload())

    world_state = create_world_state()
    jev = _jev(handle)
    captain = _captain(handle)
    try:
        with pytest.raises(ProviderError) as jev_error:
            jev.classify(replace(_dispatch_context(), world=world_state))
        with pytest.raises(ProviderError) as captain_error:
            captain.decide(replace(_captain_context(), world=world_state))
    finally:
        jev.close()
        captain.close()

    assert requests == 0
    assert jev_error.value.code is ProviderErrorCode.INVALID_INPUT
    assert jev_error.value.metadata["request_made"] is False
    assert captain_error.value.code is ProviderErrorCode.INVALID_INPUT
    assert captain_error.value.metadata["request_made"] is False
