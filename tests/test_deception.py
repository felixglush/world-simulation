"""Claims remain separate from physical truth and provenance is investigated."""

from dataclasses import asdict, replace

from station_control.deception import TradeReport, publish_due_reports, trace_report
from station_control.scenarios import create_world
from station_control.trade import create_world_state, observe_world


def report_world():
    initial = replace(create_world("normal", 0), scheduled_events=(), leak_active=True)
    return replace(
        create_world_state(initial),
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


def test_report_delivery_keeps_observation_time_and_does_not_change_reality():
    state = replace(report_world(), station=replace(report_world().station, turn=2))
    delivered = publish_due_reports(state)
    assert len(delivered.evidence) == 2
    assert delivered.state.station == state.station
    assert delivered.state.ledger == state.ledger
    assert {item.observed_turn for item in delivered.evidence} == {0}
    assert {item.turn for item in delivered.evidence} == {2}
    assert all(item.kind == "report" for item in delivered.evidence)
    assert delivered.state.station.leak_active
    assert publish_due_reports(delivered.state).state == delivered.state


def test_apparently_independent_reports_do_not_disclose_hidden_shared_source():
    state = replace(report_world(), station=replace(report_world().station, turn=2))
    delivered = publish_due_reports(state).state
    station = observe_world(delivered, "station")
    assert {item.source_id for item in station.evidence if item.kind == "report"} == {
        "port.office",
        "maintenance.office",
    }
    assert "industrial.qa" not in repr(asdict(station))
    assert all(item.upstream_report_id is None for item in station.evidence)


def test_tracing_one_hop_at_a_time_reveals_shared_upstream_evidence():
    state = replace(report_world(), station=replace(report_world().station, turn=2))
    state = publish_due_reports(state).state
    denied = trace_report(state, "qa")
    assert not denied.accepted
    assert denied.state is state
    first = trace_report(state, "maintenance")
    assert first.accepted
    assert first.evidence[-1].upstream_report_id == "manifest"
    assert first.evidence[-1].upstream_source_id == "port.office"
    second = trace_report(first.state, "manifest")
    assert second.accepted
    assert second.evidence[-1].upstream_report_id == "qa"
    assert second.evidence[-1].upstream_source_id == "industrial.qa"
    assert "industrial.qa" in repr(asdict(observe_world(second.state, "station")))
    assert "port.office" not in repr(asdict(observe_world(second.state, "industrial")))


def test_report_and_trace_are_world_scoped_and_do_not_release_future_information():
    state = report_world()
    assert not publish_due_reports(state).evidence
    assert not trace_report(state, "manifest").accepted
    at_two = publish_due_reports(replace(state, station=replace(state.station, turn=2))).state
    denied = trace_report(at_two, "manifest", world_id="industrial")
    assert not denied.accepted
    assert denied.state is at_two


def test_trace_requires_available_crew_and_terminal_world_does_not_publish():
    state = report_world()
    state = publish_due_reports(replace(state, station=replace(state.station, turn=2))).state
    exhausted = replace(state, station=replace(state.station, available_crew=0))
    denied = trace_report(exhausted, "manifest")
    assert not denied.accepted
    assert denied.state is exhausted
    dead = replace(state, station=replace(state.station, crew_alive=False))
    assert not trace_report(dead, "manifest").accepted
    assert publish_due_reports(dead).state is dead


def test_report_configuration_rejects_cycles_missing_sources_and_future_provenance():
    import pytest

    from station_control.deception import validate_reports

    worlds = {"station", "industrial"}
    valid = report_world().reports
    validate_reports(valid, worlds)
    invalid = (
        valid + (valid[0],),
        (replace(valid[0], upstream_report_id="maintenance"), *valid[1:]),
        (replace(valid[0], upstream_report_id="missing"), *valid[1:]),
        (replace(valid[0], receipt_turn=3), *valid[1:]),
        (replace(valid[0], report_id=[]), *valid[1:]),
        (replace(valid[0], recipient_world=[]), *valid[1:]),
        (replace(valid[0], observed_turn=True), *valid[1:]),
    )
    for reports in invalid:
        with pytest.raises(ValueError):
            validate_reports(reports, worlds)


def test_duplicate_report_graph_is_rejected_before_any_claims_are_published():
    import pytest

    state = report_world()
    state = replace(
        state, station=replace(state.station, turn=2), reports=state.reports + (state.reports[1],)
    )
    with pytest.raises(ValueError, match="unique"):
        publish_due_reports(state)
    assert not state.evidence


def test_repeated_trace_cannot_spend_crew_again_on_unchanged_provenance():
    state = report_world()
    state = publish_due_reports(replace(state, station=replace(state.station, turn=2))).state
    once = trace_report(state, "manifest")
    assert once.accepted
    twice = trace_report(once.state, "manifest")
    assert not twice.accepted
    assert twice.state is once.state
