"""Public mission-runner behavior across routing, actions, and replay logging."""

from dataclasses import asdict

import pytest

from station_control import ScenarioFamily
from station_control.application import (
    ControllerMode,
    EvidenceAccess,
    MissionConfig,
    MissionStatus,
    run_mission,
)
from station_control.controllers import (
    ActionRequest,
    ActionRequestKind,
    CaptainDecision,
    DispatchJudgment,
    NoulOutcome,
    ProviderError,
    ProviderErrorCode,
    Subsystem,
)
from station_control.domain import StationState
from station_control.scenarios import ScenarioDefinition, ScenarioEventSpec


class InspectingCaptain:
    def __init__(self, action=None):
        self.contexts = []
        self.action = action or ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system")

    def decide(self, context):
        self.contexts.append(context)
        return CaptainDecision(self.action, rationale="check observed condition")


class UncertainDispatcher:
    def __init__(self, judgment=None):
        self.contexts = []
        self.judgment = judgment or DispatchJudgment(
            subsystem=Subsystem.LIFE_SUPPORT,
            safeguard_request=NoulOutcome.UNCERTAIN,
            diagnosis_supported=NoulOutcome.UNCERTAIN,
            urgency=0,
            rationale="the report is unclear",
        )

    def classify(self, context):
        self.contexts.append(context)
        return self.judgment


class FailingCaptain:
    def decide(self, context):
        raise ProviderError(ProviderErrorCode.BUDGET_EXHAUSTED)


class RepairWorkflowCaptain:
    def __init__(self):
        self.contexts = []

    def decide(self, context):
        self.contexts.append(context)
        evidence = context.evidence
        inspection = next((item for item in evidence if item.kind == "inspection"), None)
        repair_complete = next((item for item in evidence if item.kind == "repair_complete"), None)
        if repair_complete:
            return CaptainDecision(
                ActionRequest(
                    ActionRequestKind.CLOSE,
                    reason="repair completion was observed",
                    evidence_sequences=(repair_complete.sequence,),
                )
            )
        if inspection and "active oxygen leak" in inspection.message.lower():
            return CaptainDecision(
                ActionRequest(ActionRequestKind.ASSIGN_REPAIR, target="oxygen_system")
            )
        return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system"))


class ClarifyingCaptain:
    def __init__(self):
        self.contexts = []

    def decide(self, context):
        self.contexts.append(context)
        if not any(item.kind == "clarification" for item in context.evidence):
            return CaptainDecision(ActionRequest(ActionRequestKind.REQUEST_CLARIFICATION))
        return CaptainDecision(
            ActionRequest(ActionRequestKind.DEFER, follow_up_turn=context.station.turn + 1)
        )


class UnsupportedCloseCaptain:
    def decide(self, context):
        active_leak = next(
            (
                item
                for item in context.evidence
                if item.kind == "inspection" and "active oxygen leak" in item.message.lower()
            ),
            None,
        )
        if active_leak is None:
            return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system"))
        return CaptainDecision(
            ActionRequest(
                ActionRequestKind.CLOSE,
                reason="the inspection was reviewed",
                evidence_sequences=(active_leak.sequence,),
            )
        )


class CherryPickingCaptain:
    def __init__(self):
        self.contexts = []

    def decide(self, context):
        self.contexts.append(context)
        inspections = [item for item in context.evidence if item.kind == "inspection"]
        if not inspections:
            return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="sensor_a"))
        if len(inspections) == 1:
            return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system"))
        return CaptainDecision(
            ActionRequest(
                ActionRequestKind.CLOSE,
                reason="one inspection was normal",
                evidence_sequences=(inspections[0].sequence,),
            )
        )


class DeferringCaptain:
    def __init__(self):
        self.turns = []

    def decide(self, context):
        self.turns.append(context.station.turn)
        return CaptainDecision(
            ActionRequest(ActionRequestKind.DEFER, follow_up_turn=context.station.turn + 1)
        )


class ClosingFromDelayedRepairNoticeCaptain:
    def __init__(self):
        self.notice_contexts = []

    def decide(self, context):
        if (
            context.incident.kind == "report"
            and "repair completed at turn" in context.incident.message
        ):
            self.notice_contexts.append(context)
            return CaptainDecision(
                ActionRequest(
                    ActionRequestKind.CLOSE,
                    reason="the delayed maintenance notice says the repair is complete",
                    evidence_sequences=(context.incident.sequence,),
                )
            )

        inspections = [item for item in context.evidence if item.kind == "inspection"]
        if not inspections:
            return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system"))
        latest = inspections[-1]
        if "active oxygen leak" in latest.message.lower():
            return CaptainDecision(
                ActionRequest(ActionRequestKind.ASSIGN_REPAIR, target="oxygen_system")
            )
        return CaptainDecision(
            ActionRequest(
                ActionRequestKind.CLOSE,
                reason="current inspection shows the system is stable",
                evidence_sequences=(latest.sequence,),
            )
        )


class EmptyReasonCaptain:
    def __init__(self):
        self.inspection_sequence = None

    def decide(self, context):
        inspection = next((item for item in context.evidence if item.kind == "inspection"), None)
        if inspection is None:
            return CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system"))
        self.inspection_sequence = inspection.sequence
        return CaptainDecision(
            ActionRequest(
                ActionRequestKind.CLOSE,
                reason=" ",
                evidence_sequences=(inspection.sequence,),
            )
        )


class LowUrgencyDispatcher(UncertainDispatcher):
    def __init__(self):
        super().__init__(
            DispatchJudgment(
                subsystem=Subsystem.LIFE_SUPPORT,
                safeguard_request=NoulOutcome.NO,
                diagnosis_supported=NoulOutcome.YES,
                urgency=0,
            )
        )


@pytest.mark.parametrize("family", tuple(ScenarioFamily))
def test_rules_controller_completes_a_fourteen_day_mission_with_crew_alive(family):
    result = run_mission(
        MissionConfig(
            scenario=family,
            seed=27,
            duration_turns=14 * 24,
            controller_mode=ControllerMode.RULES,
        )
    )

    assert result.status is MissionStatus.COMPLETED
    assert result.evaluation.metrics["crew_survived"] is True
    assert result.evaluation.metrics["turns_completed"] == 14 * 24
    assert "unsupported_diagnoses_accepted" in result.evaluation.unavailable
    assert any(snapshot.leak_active for snapshot in result.debug_snapshots) is (
        family in {ScenarioFamily.LEAK, ScenarioFamily.MISLEADING_REPORT}
    )


def test_inspection_budget_improves_survival_on_held_out_leak_seeds():
    held_out_seeds = (101, 103, 107)

    with_inspection = [
        run_mission(
            MissionConfig(
                scenario=ScenarioFamily.LEAK,
                seed=seed,
                duration_turns=40,
                inspection_budget_per_turn=1,
            )
        ).evaluation.metrics["crew_survived"]
        for seed in held_out_seeds
    ]
    without_inspection = [
        run_mission(
            MissionConfig(
                scenario=ScenarioFamily.LEAK,
                seed=seed,
                duration_turns=40,
                inspection_budget_per_turn=0,
            )
        ).evaluation.metrics["crew_survived"]
        for seed in held_out_seeds
    ]

    assert all(with_inspection)
    assert not any(without_inspection)


def test_delayed_repair_notice_cannot_close_a_recurrent_leak_incident():
    captain = ClosingFromDelayedRepairNoticeCaptain()
    definition = ScenarioDefinition(
        id="recurrent_leak_with_delayed_notice",
        description="A later leak starts before an earlier repair notice arrives.",
        initial={"repair_notice_delay_turns": 3},
        events=(
            ScenarioEventSpec(turn=1, kind="leak_start"),
            ScenarioEventSpec(turn=5, kind="leak_start"),
        ),
    )

    result = run_mission(
        MissionConfig(
            scenario_definition=definition,
            seed=4,
            duration_turns=7,
            controller_mode=ControllerMode.LLM,
        ),
        captain=captain,
    )

    assert captain.notice_contexts
    notice = captain.notice_contexts[0].incident
    notice_record = next(
        event
        for event in result.events
        if event["event_type"] == "world_evidence"
        and event["evidence"]["sequence"] == notice.sequence
    )
    assert notice_record["turn"] == 7
    assert notice.kind == "report"
    assert next(
        snapshot for snapshot in result.debug_snapshots if snapshot.turn == notice_record["turn"]
    ).leak_active
    rejected = [
        event
        for event in result.events
        if event["event_type"] == "action"
        and event["decision"]["incident_id"] == notice.sequence
        and event["decision"]["kind"] == "close"
    ]
    assert rejected
    assert rejected[0]["consequence"] == {
        "accepted": False,
        "rejection": "resolution_evidence_required",
    }
    assert not any(
        event["event_type"] == "incident_closed"
        and event["decision"]["incident_id"] == notice.sequence
        for event in result.events
    )


def test_history_setting_controls_evidence_and_context_never_contains_world_truth():
    captain = InspectingCaptain(ActionRequest(ActionRequestKind.DEFER, follow_up_turn=5))
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.MISLEADING_REPORT,
            seed=12,
            duration_turns=8,
            controller_mode=ControllerMode.LLM,
            evidence_access=EvidenceAccess.HISTORY,
        ),
        captain=captain,
    )

    assert captain.contexts
    context = captain.contexts[0]
    assert all(not isinstance(value, StationState) for value in asdict(context).values())
    assert not hasattr(context.station, "leak_active")
    assert not hasattr(context.station, "sensor_fault")
    assert context.incident in context.evidence
    assert result.debug_snapshots
    assert any(snapshot.leak_active for snapshot in result.debug_snapshots)


def test_latest_access_excludes_prior_incident_history():
    captain = InspectingCaptain(ActionRequest(ActionRequestKind.DEFER, follow_up_turn=8))
    run_mission(
        MissionConfig(
            scenario=ScenarioFamily.MISLEADING_REPORT,
            seed=12,
            duration_turns=8,
            controller_mode=ControllerMode.LLM,
            evidence_access=EvidenceAccess.LATEST,
        ),
        captain=captain,
    )

    assert captain.contexts
    assert all(context.evidence[0] == context.incident for context in captain.contexts)
    assert all(len(context.evidence) <= 2 for context in captain.contexts)


def test_inspect_repair_defer_and_evidence_backed_close_resolves_a_leak():
    captain = RepairWorkflowCaptain()
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.LEAK,
            seed=3,
            duration_turns=12,
            controller_mode=ControllerMode.LLM,
        ),
        captain=captain,
    )

    actions = [
        event["decision"]["kind"]
        for event in result.events
        if event["event_type"] == "action" and event["consequence"]["accepted"] is True
    ]
    assert actions == ["inspect", "assign_repair", "close"]
    assert result.evaluation.metrics["repair_completions"] == 1
    assert result.evaluation.metrics["unresolved_incidents"] == 0
    assert any(event["event_type"] == "incident_closed" for event in result.events)


def test_inspection_confirming_an_active_leak_cannot_support_close():
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.LEAK,
            seed=3,
            duration_turns=6,
            controller_mode=ControllerMode.LLM,
        ),
        captain=UnsupportedCloseCaptain(),
    )

    rejected_closes = [
        event
        for event in result.events
        if event["event_type"] == "action"
        and event["decision"]["kind"] == "close"
        and event["consequence"]["rejection"] == "resolution_evidence_required"
    ]
    assert rejected_closes
    assert not any(event["event_type"] == "incident_closed" for event in result.events)
    assert result.evaluation.metrics["unresolved_incidents"] == 1


def test_irrelevant_normal_sensor_inspection_cannot_close_a_leak_incident():
    captain = CherryPickingCaptain()
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.LEAK,
            seed=3,
            duration_turns=8,
            controller_mode=ControllerMode.LLM,
            evidence_access=EvidenceAccess.HISTORY,
        ),
        captain=captain,
    )

    rejected = [
        event
        for event in result.events
        if event["event_type"] == "action"
        and event["decision"]["kind"] == "close"
        and event["consequence"]["rejection"] == "resolution_evidence_required"
    ]
    assert rejected
    assert result.debug_snapshots[-1].leak_active
    assert result.evaluation.metrics["unresolved_incidents"] == 1


def test_close_requires_a_nonblank_resolution_reason():
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.NORMAL,
            seed=0,
            duration_turns=8,
            controller_mode=ControllerMode.LLM,
        ),
        captain=EmptyReasonCaptain(),
    )

    assert any(
        event["event_type"] == "action"
        and event["decision"]["kind"] == "close"
        and event["consequence"]["rejection"] == "resolution_evidence_required"
        for event in result.events
    )


def test_defer_records_and_runs_the_follow_up_at_the_requested_turn():
    captain = DeferringCaptain()
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.NORMAL,
            seed=0,
            duration_turns=8,
            controller_mode=ControllerMode.LLM,
        ),
        captain=captain,
    )

    schedules = [event for event in result.events if event["event_type"] == "follow_up_scheduled"]
    assert captain.turns[:2] == [schedules[0]["turn"], schedules[0]["turn"] + 1]
    assert schedules[0]["consequence"]["reason"] == "defer"


def test_clarification_returns_a_delayed_public_response_before_follow_up():
    captain = ClarifyingCaptain()
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.NORMAL,
            seed=0,
            duration_turns=8,
            controller_mode=ControllerMode.LLM,
            evidence_access=EvidenceAccess.LATEST,
        ),
        captain=captain,
    )

    responses = [
        event for event in result.events if event["event_type"] == "clarification_received"
    ]
    assert responses
    assert responses[0]["evidence"]["kind"] == "clarification"
    assert "cannot independently confirm" in responses[0]["evidence"]["message"]
    assert any(
        item.kind == "clarification" for context in captain.contexts for item in context.evidence
    )


def test_low_urgency_report_stays_monitored_without_being_forgotten_or_auto_closed():
    dispatcher = LowUrgencyDispatcher()
    captain = InspectingCaptain()
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.NORMAL,
            seed=0,
            duration_turns=8,
            controller_mode=ControllerMode.JEV_LLM,
            escalation_threshold=55,
        ),
        captain=captain,
        dispatcher=dispatcher,
    )

    assert dispatcher.contexts
    assert not captain.contexts
    assert not any(event["event_type"] == "incident_closed" for event in result.events)
    assert result.evaluation.metrics["unresolved_incidents"] == 1
    assert any(
        event["consequence"]["reason"] == "low_priority_monitoring"
        for event in result.events
        if event["event_type"] == "follow_up_scheduled"
    )


def test_jev_uncertainty_routes_a_report_even_when_urgency_is_below_threshold():
    dispatcher = UncertainDispatcher()
    captain = InspectingCaptain(ActionRequest(ActionRequestKind.DEFER, follow_up_turn=6))

    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.NORMAL,
            seed=0,
            duration_turns=8,
            controller_mode=ControllerMode.JEV_LLM,
            escalation_threshold=100,
        ),
        captain=captain,
        dispatcher=dispatcher,
    )

    assert dispatcher.contexts
    assert captain.contexts
    assert any(record["event_type"] == "dispatch" for record in result.events)


def test_zero_inspection_budget_blocks_a_provider_inspection_and_logs_the_reason():
    captain = InspectingCaptain()

    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.LEAK,
            seed=3,
            duration_turns=6,
            controller_mode=ControllerMode.LLM,
            inspection_budget_per_turn=0,
        ),
        captain=captain,
    )

    assert captain.contexts
    assert all(
        ActionRequestKind.INSPECT not in {action.kind for action in context.allowed_actions}
        for context in captain.contexts
    )
    assert result.evaluation.metrics["inspections"] == 0
    assert any(
        record["consequence"] == {"accepted": False, "rejection": "inspection_budget_exhausted"}
        for record in result.events
    )


def test_provider_budget_failure_fails_closed_and_is_recorded_without_stopping_mission():
    result = run_mission(
        MissionConfig(
            scenario=ScenarioFamily.LEAK,
            seed=3,
            duration_turns=6,
            controller_mode=ControllerMode.LLM,
        ),
        captain=FailingCaptain(),
    )

    assert result.status is MissionStatus.COMPLETED
    failures = [record for record in result.events if record["event_type"] == "provider_failure"]
    assert failures
    assert failures[0]["consequence"]["provider"] == "captain"
    assert failures[0]["consequence"]["code"] == "budget_exhausted"
    assert result.evaluation.metrics["invalid_actions"] == 0
    assert not any(record["event_type"] == "captain_decision" for record in result.events)
    assert not any(record["event_type"] == "action" for record in result.events)


def test_event_sink_receives_same_ordered_event_records_as_the_result():
    written = []
    result = run_mission(
        MissionConfig(scenario=ScenarioFamily.NORMAL, seed=0, duration_turns=4),
        event_sink=written.append,
    )

    assert written == list(result.events)
    assert [event["sequence"] for event in result.events] == list(range(len(result.events)))
    turns = [event["turn"] for event in result.events]
    assert turns == sorted(turns)
    assert result.turns_completed == 4


def test_captain_cannot_mutate_action_catalog_for_later_decisions():
    class MutatingCaptain:
        def __init__(self):
            self.targets = []

        def decide(self, context):
            descriptor = next(
                item for item in context.allowed_actions if item.kind is ActionRequestKind.INSPECT
            )
            schema = descriptor.json_schema
            self.targets.append(list(schema["properties"]["target"]["enum"]))
            schema["properties"]["target"]["enum"].clear()
            return CaptainDecision(
                ActionRequest(ActionRequestKind.DEFER, follow_up_turn=context.station.turn + 1)
            )

    captain = MutatingCaptain()
    run_mission(
        MissionConfig(
            scenario=ScenarioFamily.LEAK,
            seed=3,
            duration_turns=6,
            controller_mode=ControllerMode.LLM,
        ),
        captain=captain,
    )
    assert len(captain.targets) > 1
    assert all(targets == ["oxygen_system", "sensor_a", "sensor_b"] for targets in captain.targets)


def test_dispatch_metadata_does_not_leak_unknown_fields():
    dispatcher = UncertainDispatcher(
        DispatchJudgment(
            Subsystem.LIFE_SUPPORT,
            NoulOutcome.NO,
            NoulOutcome.YES,
            0,
            metadata={"api_key": "PRIVATE_CREDENTIAL", "prompt": "PRIVATE_PROMPT", "calls": 1},
        )
    )
    result = run_mission(
        MissionConfig(duration_turns=6, controller_mode=ControllerMode.JEV_LLM),
        captain=InspectingCaptain(),
        dispatcher=dispatcher,
    )
    serialized = str(result.events)
    assert "PRIVATE_CREDENTIAL" not in serialized
    assert "PRIVATE_PROMPT" not in serialized
    dispatch = next(event for event in result.events if event["event_type"] == "dispatch")
    assert dispatch["decision"]["metadata"] == {"calls": 1}


@pytest.mark.parametrize("field, value", [("rationale", None), ("metadata", None)])
def test_malformed_dispatch_result_routes_to_captain_without_crashing(field, value):
    from dataclasses import replace

    dispatcher = UncertainDispatcher()
    dispatcher.judgment = replace(dispatcher.judgment, **{field: value})
    captain = InspectingCaptain()
    result = run_mission(
        MissionConfig(duration_turns=6, controller_mode=ControllerMode.JEV_LLM),
        captain=captain,
        dispatcher=dispatcher,
    )
    assert captain.contexts
    assert any(
        event["event_type"] == "provider_failure"
        and event["consequence"]["code"] == "malformed_response"
        for event in result.events
    )


def test_event_sink_cannot_change_mission_history_or_evaluation():
    config = MissionConfig(
        scenario=ScenarioFamily.LEAK, seed=3, duration_turns=8, run_id="sink-test"
    )
    expected = run_mission(config)

    def modifying_sink(record):
        if isinstance(record["decision"], dict):
            record["decision"].clear()
        if isinstance(record["consequence"], dict):
            record["consequence"].clear()
        if isinstance(record["evidence"], dict):
            record["evidence"]["message"] = "changed by sink"

    actual = run_mission(config, event_sink=modifying_sink)
    assert actual.events == expected.events
    assert actual.evaluation == expected.evaluation


@pytest.mark.parametrize(
    "field, value",
    [
        ("action", None),
        ("metadata", None),
        ("rationale", None),
        ("action", ActionRequest(ActionRequestKind.CLOSE, evidence_sequences=None)),
        ("action", ActionRequest(ActionRequestKind.CLOSE, evidence_sequences=({},))),
    ],
)
def test_malformed_captain_decision_is_recorded_and_followed_up(field, value):
    from dataclasses import replace

    class MalformedCaptain:
        def decide(self, context):
            proposal = CaptainDecision(ActionRequest(ActionRequestKind.INSPECT, "oxygen_system"))
            return replace(proposal, **{field: value})

    result = run_mission(
        MissionConfig(duration_turns=6, controller_mode=ControllerMode.LLM),
        captain=MalformedCaptain(),
    )
    failures = [event for event in result.events if event["event_type"] == "provider_failure"]
    assert failures
    assert all(event["consequence"]["code"] == "malformed_response" for event in failures)
    assert not any(event["event_type"] == "action" for event in result.events)
    assert any(
        event["event_type"] == "follow_up_scheduled"
        and event["consequence"]["reason"] == "provider_failure"
        for event in result.events
    )
