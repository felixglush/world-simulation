"""Real Captain/Jev adapters run trade missions through the public facade."""

import json

import httpx2
from test_providers import _captain_payload, _jev_payload

from station_control.deception_mission import create_deception_simulation
from station_control.providers import CallBudget, JevDispatchProvider, OpenRouterCaptainProvider


def scripted_intervention(world):
    turn = world["turn"]

    def shipment(batch):
        return next(item["shipment_id"] for item in world["shipments"] if item["batch_id"] == batch)

    choices = {
        1: (
            "purchase",
            {"seller_id": "industrial", "batch_id": "industrial-batch-a", "quantity": 2},
        ),
        2: ("purchase", {"seller_id": "ice_moon", "batch_id": "ice-feed-a", "quantity": 2}),
        6: ("inspect", {"method": "peak"}),
        7: ("inspect", {"method": "peak"}),
        8: ("quarantine", {"batch_id": "industrial-batch-a", "quantity": 1}),
        9: (
            "purchase",
            {"seller_id": "industrial", "batch_id": "industrial-batch-b", "quantity": 1},
        ),
        10: ("calibrate", {}),
        14: ("inspect", {"method": "peak"}),
        15: ("assay", {"batch_id": "ice-feed-a"}),
        16: ("quarantine", {"batch_id": "ice-feed-a", "quantity": 1}),
        17: ("purchase", {"seller_id": "ice_moon", "batch_id": "ice-feed-b", "quantity": 2}),
        18: ("trace", {"report_id": "maintenance-note"}),
        19: ("trace", {"report_id": "port-manifest"}),
        20: ("assay", {"batch_id": "ice-feed-b"}),
        21: ("consume", {"batch_id": "ice-feed-b", "quantity": 1}),
        22: ("trace", {"report_id": "supplier-check"}),
    }
    if turn in {4, 12}:
        batch = "industrial-batch-a" if turn == 4 else "industrial-batch-b"
        return "repair", {"batch_id": batch, "shipment_id": shipment(batch), "repair_mode": "full"}
    return choices.get(
        turn, ("defer", {"follow_up_turn": turn + 1 if turn < 23 else 40 if turn < 40 else 41})
    )


def providers(captain_choice, *, max_calls=120, dispatch_failure=False):
    seen = {"captain": [], "jev": []}
    budget = CallBudget(max_calls, 512)

    def captain_http(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        seen["captain"].append(payload)
        name, arguments = captain_choice(payload["world"])
        return httpx2.Response(
            200, json=_captain_payload(name=name, arguments=json.dumps(arguments))
        )

    def jev_http(request):
        body = json.loads(request.content)
        seen["jev"].append(body["state"])
        if dispatch_failure:
            return httpx2.Response(503, json={"error": "unavailable"})
        return httpx2.Response(
            200, json=_jev_payload(urgency=3 if body["state"]["world"]["turn"] <= 22 else 0)
        )

    captain = OpenRouterCaptainProvider(
        api_key="test-only",
        model="captain-test",
        budget=budget,
        http_client=httpx2.Client(transport=httpx2.MockTransport(captain_http)),
    )
    dispatcher = JevDispatchProvider(
        api_key="test-only",
        model="jev-test",
        budget=budget,
        transport=httpx2.MockTransport(jev_http),
    )
    return captain, dispatcher, seen


def mission(captain, dispatcher, turns):
    try:
        return create_deception_simulation(
            policy="crew", captain=captain, dispatcher=dispatcher
        ).run(turns=turns)
    finally:
        captain.close()
        dispatcher.close()


def test_captain_jev_supply_chain_recovers_and_discovers_causes_only_through_public_evidence():
    captain, dispatcher, seen = providers(scripted_intervention)
    result = mission(captain, dispatcher, 40)
    assert result.state.station.crew_alive
    assert not result.state.station.leak_active
    assert result.state.installed_part.batch_id == "industrial-batch-b"
    assert result.state.sensor_drift_per_turn == 0
    assert all(item.accepted for item in result.decisions)
    codes = {item.code for item in result.events}
    assert codes >= {
        "material_defect_confirmed",
        "sensor_drift_confirmed",
        "report_origin_traced",
        "cargo_quality_measured",
    }
    assert {item.batch_id for item in result.events if item.kind == "consumption"} == {"ice-feed-b"}
    assert any(review.phase == "action" for review in result.crew_reviews)
    # A scheduled monitoring window survives routine events and old report history.
    assert not any(23 < payload["world"]["turn"] < 40 for payload in seen["captain"])
    assert any(payload["world"]["turn"] == 40 for payload in seen["captain"])
    assert sum(review.model_calls for review in result.crew_reviews) == len(seen["captain"]) + len(
        seen["jev"]
    )
    for payload in seen["captain"] + seen["jev"]:
        encoded = json.dumps(payload)
        for private in (
            "latent_defect",
            "failure_load",
            "defect_after_turns",
            "yield_percent",
            "sensor_drift_per_turn",
            "residual_damage_after_turns",
        ):
            assert f'"{private}"' not in encoded
    assert all(
        "industrial.qa" not in json.dumps(payload)
        for payload in seen["captain"]
        if payload["world"]["turn"] < 20
    )
    assert any(
        "industrial.qa" in json.dumps(payload)
        for payload in seen["captain"]
        if payload["world"]["turn"] >= 20
    )


def test_unlisted_captain_arguments_cannot_spend_another_worlds_money():
    captain, dispatcher, _ = providers(
        lambda world: (
            "purchase",
            {
                "seller_id": "industrial",
                "batch_id": "industrial-batch-a",
                "quantity": 2,
                "buyer_id": "industrial",
            },
        )
    )
    result = mission(captain, dispatcher, 3)
    assert not result.state.contracts
    assert result.state.station.credits == 200
    assert any(review.provider_error == "malformed_response" for review in result.crew_reviews)


def test_dispatch_failure_routes_to_captain_without_substituting_a_rules_policy():
    captain, dispatcher, seen = providers(
        lambda world: (
            (
                "purchase",
                {"seller_id": "industrial", "batch_id": "industrial-batch-a", "quantity": 1},
            )
            if world["turn"] == 1
            else ("defer", {"follow_up_turn": world["turn"] + 1})
        ),
        dispatch_failure=True,
    )
    result = mission(captain, dispatcher, 4)
    assert result.state.station.parts == 1
    assert seen["captain"]
    assert any(
        review.provider_error == "provider_unavailable" and review.routed
        for review in result.crew_reviews
    )


def test_shared_budget_exhaustion_stops_requests_but_preserves_the_committed_shipment():
    captain, dispatcher, seen = providers(
        lambda world: (
            "purchase",
            {"seller_id": "industrial", "batch_id": "industrial-batch-a", "quantity": 2},
        ),
        max_calls=3,
    )
    result = mission(captain, dispatcher, 5)
    assert len(seen["captain"]) + len(seen["jev"]) == 3
    assert len(result.state.contracts) == 1
    assert result.state.station.parts == 2
    assert result.state.station.credits == 170
    assert any(review.provider_error == "budget_exhausted" for review in result.crew_reviews)


def test_typed_captain_port_cannot_bypass_action_validation():
    from station_control.controllers import ActionRequest, ActionRequestKind, CaptainDecision

    class MalformedCaptain:
        def decide(self, context):
            return CaptainDecision(
                ActionRequest(
                    ActionRequestKind.PURCHASE,
                    seller_id="industrial",
                    batch_id="industrial-batch-a",
                    quantity=True,
                    operating_load="peak",
                )
            )

    result = create_deception_simulation(policy="crew", captain=MalformedCaptain()).run(turns=3)
    assert result.state.station.credits == 200
    assert not result.state.contracts
    assert any(review.provider_error == "malformed_response" for review in result.crew_reviews)


def test_safeguard_requests_and_critical_oxygen_override_deferred_monitoring():
    from station_control.controllers import (
        ActionRequest,
        ActionRequestKind,
        CaptainDecision,
        DispatchJudgment,
        NoulOutcome,
        Subsystem,
    )

    reviewed = []

    class DeferringCaptain:
        def decide(self, context):
            reviewed.append(context.station.turn)
            return CaptainDecision(ActionRequest(ActionRequestKind.DEFER, follow_up_turn=40))

    class QuietDispatch:
        def classify(self, context):
            return DispatchJudgment(
                Subsystem.LIFE_SUPPORT,
                NoulOutcome.YES if context.station.turn == 12 else NoulOutcome.NO,
                NoulOutcome.YES,
                0,
            )

    result = create_deception_simulation(
        policy="crew", captain=DeferringCaptain(), dispatcher=QuietDispatch()
    ).run(turns=20)
    assert reviewed[0] == 1
    assert 12 in reviewed
    assert not any(1 < turn < 12 or 12 < turn < 18 for turn in reviewed)
    assert reviewed[-2:] == [19, 20]
    assert result.state.station.crew_alive
