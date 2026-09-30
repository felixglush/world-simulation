"""Mission evaluation behavior from deterministic world and event records."""

from dataclasses import replace

from station_control.domain import Evidence
from station_control.evaluation import MissionEvaluation, evaluate_mission
from station_control.scenarios import ScenarioFamily, create_world


def test_evaluation_reports_evidenced_metrics_and_names_unsupported_metrics():
    initial = create_world(ScenarioFamily.NORMAL, seed=0)
    repair = Evidence(sequence=3, turn=1, kind="repair_complete", message="fixed")
    middle = replace(
        initial,
        turn=1,
        oxygen=250,
        parts=1,
        backup_oxygen=90,
        credits=80,
        evidence=(repair,),
    )
    final = replace(
        middle,
        turn=2,
        oxygen=251,
        backup_oxygen=60,
        credits=45,
        crew_alive=False,
        evidence=(repair,),
    )
    events = [
        {"event_type": "incident_opened", "turn": 0, "decision": {"incident_id": 12}},
        {
            "event_type": "follow_up_scheduled",
            "turn": 0,
            "decision": {"incident_id": 12, "follow_up_turn": 1},
        },
        {
            "event_type": "follow_up_due",
            "turn": 1,
            "decision": {"incident_id": 12, "scheduled_turn": 1},
        },
        {
            "event_type": "action",
            "decision": {"incident_id": 12, "kind": "inspect"},
            "consequence": {"accepted": True, "rejection": None},
        },
        {
            "event_type": "action",
            "decision": {"incident_id": 12, "kind": "assign_repair"},
            "consequence": {"accepted": False, "rejection": "insufficient_parts"},
        },
        {
            "event_type": "action",
            "decision": {"incident_id": 12, "kind": "inspect"},
            "consequence": {"accepted": False, "rejection": "crew_unavailable"},
        },
        {
            "event_type": "action",
            "decision": {"incident_id": 12, "kind": "assign_repair"},
            "consequence": {"accepted": True, "rejection": None},
        },
        {
            "event_type": "action",
            "decision": {"incident_id": 12, "kind": "request_clarification"},
            "consequence": {"accepted": True, "rejection": None},
        },
        {"event_type": "incident_opened", "turn": 1, "decision": {"incident_id": 13}},
        {"event_type": "incident_closed", "turn": 2, "decision": {"incident_id": 13}},
    ]

    result = evaluate_mission((initial, middle, final), events)

    assert isinstance(result, MissionEvaluation)
    assert result.metrics == {
        "crew_survived": False,
        "turns_completed": 2,
        "turns_below_critical_oxygen": 1,
        "oxygen_consumed": 120,
        "parts_consumed": 1,
        "inspections": 1,
        "clarification_requests": 1,
        "repair_completions": 1,
        "backup_oxygen_used": 60,
        "credits_spent": 55,
        "invalid_actions": 2,
        "unresolved_incidents": 1,
        "forgotten_incidents": 1,
        "incident_resolution_turns": 1,
        "mean_incident_resolution_turns": 1.0,
        "model_calls": 0,
        "model_latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "model_cost": 0,
    }
    assert result.unavailable == (
        "critical_reports_missed",
        "unsupported_diagnoses_accepted",
        "unnecessary_escalations",
    )
    assert not set(result.unavailable) & result.metrics.keys()


def test_evaluation_reads_repair_completion_from_world_evidence_records():
    events = [
        {
            "event_type": "world_evidence",
            "evidence": {
                "sequence": 9,
                "turn": 5,
                "kind": "repair_complete",
                "message": "fixed",
            },
        }
    ]

    result = evaluate_mission((), events)

    assert result.metrics["repair_completions"] == 1
    assert "crew_survived" not in result.metrics
    assert "turns_completed" not in result.metrics


def test_provider_usage_aggregates_reported_calls_and_marks_unknown_cost_unavailable():
    initial = create_world(ScenarioFamily.NORMAL, seed=0)
    final = replace(initial, turn=2)
    events = [
        {
            "event_type": "dispatch",
            "decision": {
                "metadata": {
                    "calls": 1,
                    "request_made": True,
                    "latency_ms": 12.5,
                    "input_tokens": 35,
                    "output_tokens": 8,
                    "cost_usd": 0.002,
                }
            },
        },
        {
            "event_type": "captain_decision",
            "decision": {
                "metadata": {
                    "calls": 1,
                    "request_made": True,
                    "latency_ms": 4.5,
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "cost_usd": None,
                }
            },
        },
    ]

    result = evaluate_mission((initial, final), events)

    assert result.metrics["model_calls"] == 2
    assert result.metrics["model_latency_ms"] == 17.0
    assert result.metrics["input_tokens"] == 40
    assert result.metrics["output_tokens"] == 10
    assert "model_cost" not in result.metrics
    assert "model_cost" in result.unavailable


def test_failed_network_call_contributes_latency_but_not_missing_usage_as_zero():
    result = evaluate_mission(
        (),
        (
            {
                "event_type": "provider_failure",
                "consequence": {
                    "metadata": {
                        "calls": 1,
                        "request_made": True,
                        "latency_ms": 8.0,
                        "input_tokens": None,
                        "output_tokens": None,
                        "cost_usd": None,
                    }
                },
            },
        ),
    )

    assert result.metrics["model_calls"] == 1
    assert result.metrics["model_latency_ms"] == 8.0
    assert "input_tokens" not in result.metrics
    assert "output_tokens" not in result.metrics
    assert "model_cost" not in result.metrics
    assert {"input_tokens", "output_tokens", "model_cost"} <= set(result.unavailable)


def test_evaluation_keeps_unsupported_scores_out_of_metrics():
    result = evaluate_mission((), ())

    assert result.metrics["model_calls"] == 0
    assert result.metrics["model_latency_ms"] == 0
    assert result.metrics["model_cost"] == 0
    assert "critical_reports_missed" not in result.metrics
    assert "critical_reports_missed" in result.unavailable
    assert "model_cost" not in result.unavailable


def test_provider_attempt_is_counted_once_when_proposal_and_action_share_metadata():
    metadata = {
        "calls": 1,
        "request_made": True,
        "latency_ms": 5,
        "input_tokens": 10,
        "output_tokens": 3,
        "cost_usd": 0.01,
    }
    events = [
        {
            "event_type": kind,
            "turn": 1,
            "decision": {"incident_id": 1, "kind": "inspect", "metadata": metadata},
            "consequence": {"accepted": True},
        }
        for kind in ("captain_decision", "action")
    ]
    metrics = evaluate_mission((), events).metrics
    assert metrics["model_calls"] == 1
    assert metrics["model_cost"] == 0.01
    assert metrics["inspections"] == 1
