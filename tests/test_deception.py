"""Report provenance stays private until a station crew traces it."""

from dataclasses import asdict, replace

import pytest

from station_control.deception import trace_report
from station_control.scenarios import create_world
from station_control.trade import (
    TradeEvidenceKind,
    TradeReport,
    advance_world,
    create_world_state,
    observe_world,
)


def test_real_turns_deliver_reports_and_trace_shared_provenance_one_hop_at_a_time():
    station = replace(create_world("normal", seed=0), scheduled_events=(), leak_active=True)
    state = replace(
        create_world_state(station),
        reports=(
            TradeReport("qa", "industrial.qa", "industrial", "Basic check passed.", 0, 1),
            TradeReport(
                "manifest",
                "port.office",
                "station",
                "Cargo is certified reliable.",
                0,
                2,
                upstream_report_id="qa",
            ),
            TradeReport(
                "maintenance",
                "maintenance.office",
                "station",
                "Replacement is sound.",
                0,
                2,
                upstream_report_id="manifest",
            ),
        ),
    )

    first_turn = advance_world(state)
    assert [item.report_id for item in first_turn.evidence] == ["qa"]
    assert first_turn.evidence[0].world_id == "industrial"
    second_turn = advance_world(first_turn.state)
    assert {item.report_id for item in second_turn.evidence} == {"manifest", "maintenance"}
    assert all(item.observed_turn == 0 and item.turn == 2 for item in second_turn.evidence)
    assert second_turn.state.station.leak_active
    assert second_turn.state.ledger == state.ledger

    station_view = observe_world(second_turn.state, "station")
    assert {
        item.source_id for item in station_view.evidence if item.kind is TradeEvidenceKind.REPORT
    } == {
        "port.office",
        "maintenance.office",
    }
    assert all(item.upstream_report_id is None for item in station_view.evidence)
    assert "industrial.qa" not in repr(asdict(station_view))
    assert not trace_report(second_turn.state, "qa").accepted
    assert not trace_report(second_turn.state, "manifest", world_id="industrial").accepted

    first_trace = trace_report(second_turn.state, "maintenance")
    assert first_trace.accepted
    assert first_trace.evidence[0].upstream_report_id == "manifest"
    assert first_trace.evidence[0].upstream_source_id == "port.office"
    repeated_trace = trace_report(first_trace.state, "maintenance")
    assert not repeated_trace.accepted
    assert repeated_trace.state is first_trace.state

    second_trace = trace_report(first_trace.state, "manifest")
    assert second_trace.accepted
    assert second_trace.evidence[0].upstream_report_id == "qa"
    assert second_trace.evidence[0].upstream_source_id == "industrial.qa"
    assert "industrial.qa" in repr(asdict(observe_world(second_trace.state, "station")))
    assert "port.office" not in repr(asdict(observe_world(second_trace.state, "industrial")))


def test_invalid_report_provenance_is_rejected_before_a_turn_publishes_claims():
    station = replace(create_world("normal", seed=0), scheduled_events=())
    report = TradeReport("report-a", "qa.office", "station", "Passed.", 0, 1)
    state = replace(create_world_state(station), reports=(report, report))

    with pytest.raises(ValueError, match="unique"):
        advance_world(state)

    assert state.evidence == ()
