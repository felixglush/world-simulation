"""Behavioral coverage for the bounded adversary and mission integration."""

from dataclasses import replace

import pytest

from station_control.adversary import (
    AdversaryAction,
    AdversaryActionKind,
    AdversaryDecision,
    AdversaryMode,
    adversary_context,
    apply_adversary_action,
    eligible_adversary_actions,
)
from station_control.application import (
    ControllerMode,
    EvidenceAccess,
    MissionConfig,
    run_mission,
)
from station_control.controllers import (
    ActionRequest,
    ActionRequestKind,
    CaptainDecision,
    ProviderError,
    ProviderErrorCode,
)
from station_control.domain import Delivery, ScheduledEvent, advance_turn
from station_control.scenarios import ScenarioFamily, create_world


class ScriptedAdversary:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.contexts = []

    def decide(self, context):
        self.contexts.append(context)
        if self.decisions:
            return self.decisions.pop(0)
        return AdversaryDecision(AdversaryAction(AdversaryActionKind.WAIT))


class CapturingCaptain:
    def __init__(self):
        self.contexts = []

    def decide(self, context):
        self.contexts.append(context)
        return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system"))


class BudgetFailingAdversary:
    def __init__(self):
        self.calls = 0

    def decide(self, context):
        self.calls += 1
        raise ProviderError(
            ProviderErrorCode.BUDGET_EXHAUSTED,
            {
                "provider": "offline-test",
                "model": "offline/adversary",
                "calls": 0,
                "request_made": False,
                "latency_ms": 0,
            },
        )


def test_adversary_is_off_by_default_and_has_a_finite_disruption_budget():
    config = MissionConfig()

    assert config.adversary_mode is AdversaryMode.OFF
    assert config.adversary_disruption_budget == 3
    ordinary = run_mission(MissionConfig(duration_turns=1))
    assert not any("adversary" in event["event_type"] for event in ordinary.events)


@pytest.mark.parametrize("budget", (-1, 7, True))
def test_mission_rejects_nonbounded_adversary_disruption_budgets(budget):
    with pytest.raises(ValueError, match="Adversary disruption budget"):
        MissionConfig(adversary_disruption_budget=budget)


def test_llm_adversary_requires_its_provider():
    with pytest.raises(ValueError, match="requires an adversary provider"):
        run_mission(MissionConfig(duration_turns=1, adversary_mode=AdversaryMode.LLM))


def test_current_adversary_view_omits_the_authored_future_event_schedule():
    state = create_world(ScenarioFamily.LEAK, seed=19)
    context = adversary_context(state, disruption_budget_remaining=3)

    assert context.turn == 0
    assert context.leak_active is False
    assert not hasattr(context, "scheduled_events")
    assert AdversaryActionKind.START_SILENT_LEAK in {
        action.kind for action in context.allowed_actions
    }
    assert AdversaryActionKind.WAIT in {action.kind for action in context.allowed_actions}


def test_adversary_actions_reuse_silent_leak_and_sensor_fault_world_mechanics():
    clear = create_world(ScenarioFamily.NORMAL, seed=2)
    leak = apply_adversary_action(clear, AdversaryAction(AdversaryActionKind.START_SILENT_LEAK), 1)
    assert leak.accepted
    assert leak.state.scheduled_events[-1] == ScheduledEvent(1, "leak_start", message="")
    leak_turn = advance_turn(leak.state)
    assert leak_turn.state.leak_active
    assert leak_turn.evidence == ()

    masked = apply_adversary_action(
        clear,
        AdversaryAction(AdversaryActionKind.MASK_SENSOR, target="sensor_a"),
        1,
    )
    assert masked.accepted
    assert masked.state.scheduled_events[-1] == ScheduledEvent(
        1, "sensor_fault", target="sensor_a", message=""
    )
    masked_turn = advance_turn(masked.state)
    assert masked_turn.state.sensor_fault == "sensor_a"
    assert masked_turn.evidence == ()


def test_delivery_delay_changes_only_the_earliest_pending_delivery_by_one_turn():
    state = replace(
        create_world(ScenarioFamily.NORMAL, seed=3),
        deliveries=(
            Delivery(due_turn=3, supply="oxygen", quantity=1),
            Delivery(due_turn=6, supply="parts", quantity=2),
        ),
    )

    result = apply_adversary_action(
        state, AdversaryAction(AdversaryActionKind.DELAY_PENDING_DELIVERY), 1
    )

    assert result.accepted
    assert [item.due_turn for item in result.state.deliveries] == [4, 6]
    assert result.state.deliveries[1] == state.deliveries[1]


def test_deceptive_report_is_fixed_public_evidence_and_requires_a_current_leak():
    clear = create_world(ScenarioFamily.NORMAL, seed=5)
    rejected = apply_adversary_action(
        clear, AdversaryAction(AdversaryActionKind.DECEPTIVE_REPORT), 1
    )
    assert not rejected.accepted
    assert rejected.rejection == "no_active_leak"
    assert rejected.state == clear

    leaking = replace(clear, leak_active=True)
    accepted = apply_adversary_action(
        leaking, AdversaryAction(AdversaryActionKind.DECEPTIVE_REPORT), 1
    )
    report = advance_turn(accepted.state).evidence
    assert accepted.accepted
    assert len(report) == 1
    assert report[0].kind == "report"
    assert "oxygen loop is stable" in report[0].message


@pytest.mark.parametrize(
    ("action", "budget", "expected_rejection"),
    [
        (AdversaryAction("unknown_action"), 1, "invalid_action"),
        (AdversaryAction(AdversaryActionKind.START_SILENT_LEAK), 0, "disruption_budget_exhausted"),
        (
            AdversaryAction(AdversaryActionKind.MASK_SENSOR, target="sensor_c"),
            1,
            "invalid_target",
        ),
    ],
)
def test_invalid_or_over_budget_proposals_are_rejected_without_mutating_world(
    action, budget, expected_rejection
):
    state = create_world(ScenarioFamily.NORMAL, seed=9)

    result = apply_adversary_action(state, action, budget)

    assert not result.accepted
    assert result.rejection == expected_rejection
    assert result.state == state


def test_catalog_removes_ineligible_actions_and_revalidates_provider_choices():
    state = replace(create_world(ScenarioFamily.NORMAL, seed=11), leak_active=True)
    catalog = eligible_adversary_actions(state, 3)
    assert AdversaryActionKind.START_SILENT_LEAK not in {item.kind for item in catalog}
    assert AdversaryActionKind.DECEPTIVE_REPORT in {item.kind for item in catalog}

    duplicate_leak = apply_adversary_action(
        state, AdversaryAction(AdversaryActionKind.START_SILENT_LEAK), 3
    )
    assert not duplicate_leak.accepted
    assert duplicate_leak.rejection == "leak_already_active"
    assert duplicate_leak.state == state


def test_one_proposal_runs_before_each_advance_and_budget_stops_later_provider_calls():
    adversary = ScriptedAdversary(
        AdversaryDecision(
            AdversaryAction(AdversaryActionKind.START_SILENT_LEAK),
            rationale="start it quietly",
            metadata={
                "calls": 1,
                "request_made": True,
                "latency_ms": 4,
                "input_tokens": 12,
                "output_tokens": 3,
                "cost_usd": 0.01,
            },
        )
    )
    result = run_mission(
        MissionConfig(
            duration_turns=3,
            adversary_mode=AdversaryMode.LLM,
            adversary_disruption_budget=1,
        ),
        adversary=adversary,
    )

    assert len(adversary.contexts) == 1
    assert adversary.contexts[0].turn == 0
    assert result.debug_snapshots[1].leak_active
    audits = [event for event in result.events if event["event_type"] == "adversary_decision"]
    assert [event["turn"] for event in audits] == [0]
    assert audits[0]["consequence"]["status"] == "scheduled"
    assert result.evaluation.metrics["adversary_disruptions"] == 1
    assert result.evaluation.metrics["model_calls"] == 1
    assert result.evaluation.metrics["model_latency_ms"] == 4
    assert result.evaluation.metrics["input_tokens"] == 12
    assert result.evaluation.metrics["output_tokens"] == 3
    assert result.evaluation.metrics["model_cost"] == 0.01


def test_wait_is_a_valid_zero_disruption_proposal_and_keeps_the_adversary_budget():
    wait = AdversaryDecision(
        AdversaryAction(AdversaryActionKind.WAIT),
        rationale="wait for a better opportunity",
        metadata={"calls": 1, "request_made": True},
    )
    adversary = ScriptedAdversary(wait, wait)

    result = run_mission(
        MissionConfig(
            duration_turns=2,
            adversary_mode=AdversaryMode.LLM,
            adversary_disruption_budget=1,
        ),
        adversary=adversary,
    )

    assert len(adversary.contexts) == 2
    assert [context.disruption_budget_remaining for context in adversary.contexts] == [1, 1]
    audits = [event for event in result.events if event["event_type"] == "adversary_decision"]
    assert [event["consequence"]["status"] for event in audits] == ["waited", "waited"]
    assert result.evaluation.metrics["adversary_disruptions"] == 0
    assert result.evaluation.metrics["model_calls"] == 2


def test_ineligible_provider_choice_is_audited_as_rejected_without_world_change():
    adversary = ScriptedAdversary(
        AdversaryDecision(
            AdversaryAction(AdversaryActionKind.DECEPTIVE_REPORT),
            rationale="try to make the crew trust a false report",
        )
    )

    result = run_mission(
        MissionConfig(
            duration_turns=1,
            adversary_mode=AdversaryMode.LLM,
            adversary_disruption_budget=1,
        ),
        adversary=adversary,
    )

    audit = next(event for event in result.events if event["event_type"] == "adversary_decision")
    assert audit["decision"]["action"]["kind"] == "deceptive_report"
    assert audit["consequence"]["status"] == "rejected"
    assert audit["consequence"]["rejection"] == "no_active_leak"
    assert result.evaluation.metrics["adversary_disruptions"] == 0
    assert result.evaluation.metrics["adversary_rejected_actions"] == 1
    assert not any(
        event["event_type"] == "world_evidence"
        and event["evidence"]["message"].startswith("Maintenance confirms")
        for event in result.events
    )


@pytest.mark.parametrize("evidence_access", tuple(EvidenceAccess))
def test_private_adversary_context_and_rationale_never_enter_captain_history(evidence_access):
    private_rationale = "actual leak truth known only to adversary"
    adversary = ScriptedAdversary(
        AdversaryDecision(
            AdversaryAction(AdversaryActionKind.START_SILENT_LEAK),
            rationale=private_rationale,
        ),
        AdversaryDecision(
            AdversaryAction(AdversaryActionKind.DECEPTIVE_REPORT),
            rationale=private_rationale,
        ),
    )
    captain = CapturingCaptain()
    result = run_mission(
        MissionConfig(
            duration_turns=2,
            controller_mode=ControllerMode.LLM,
            evidence_access=evidence_access,
            adversary_mode=AdversaryMode.LLM,
            adversary_disruption_budget=2,
        ),
        captain=captain,
        adversary=adversary,
    )

    assert len(captain.contexts) == 1
    assert any("oxygen loop is stable" in item.message for item in captain.contexts[0].evidence)
    assert private_rationale not in repr(captain.contexts)
    assert not any(item.kind == "adversary_decision" for item in captain.contexts[0].evidence)
    audit = next(event for event in result.events if event["event_type"] == "adversary_decision")
    assert audit["evidence"]["leak_active"] is False
    assert audit["decision"]["rationale"] == private_rationale
    assert audit["consequence"]["status"] == "scheduled"


def test_provider_budget_failure_is_audited_and_stops_future_calls():
    adversary = BudgetFailingAdversary()
    result = run_mission(
        MissionConfig(
            duration_turns=3,
            adversary_mode=AdversaryMode.LLM,
            adversary_disruption_budget=3,
        ),
        adversary=adversary,
    )

    assert adversary.calls == 1
    failures = [event for event in result.events if event["event_type"] == "provider_failure"]
    assert len(failures) == 1
    assert failures[0]["decision"]["provider"] == "adversary"
    assert failures[0]["consequence"]["code"] == "budget_exhausted"
    assert failures[0]["evidence"]["leak_active"] is False
    assert result.evaluation.metrics["model_calls"] == 0
    assert result.evaluation.metrics["adversary_disruptions"] == 0
    assert result.evaluation.metrics["model_latency_ms"] == 0
    assert result.evaluation.metrics["input_tokens"] == 0
    assert result.evaluation.metrics["output_tokens"] == 0
    assert result.evaluation.metrics["model_cost"] == 0
