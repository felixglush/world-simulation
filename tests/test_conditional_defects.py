"""Failures depend on exposure and repairs remove their actual causes."""

from dataclasses import replace

from station_control.scenarios import create_world
from station_control.trade import (
    PartLot,
    advance_world,
    create_world_state,
    inspect_installed_batch,
    repair_with_batch,
)


def initial(*, defective=True, residual=0):
    station = replace(create_world("normal", 0), scheduled_events=(), parts=2, leak_active=True)
    state = create_world_state(station)
    return replace(
        state,
        station_lots=(
            PartLot(
                "local-a",
                1,
                10,
                "station",
                latent_defect=defective,
                defect_after_turns=2 if defective else 0,
                failure_load="peak",
            ),
            PartLot("local-b", 1, 10, "station"),
        ),
        residual_damage_after_turns=residual,
    )


def advance(state, turns):
    for _ in range(turns):
        state = advance_world(state).state
    return state


def repaired(*, defective=True, residual=0, mode="full"):
    return advance(
        repair_with_batch(
            initial(defective=defective, residual=residual), "local-a", mode=mode
        ).state,
        2,
    )


def test_conditional_defect_survives_routine_inspection_and_fails_only_after_peak_exposure():
    state = advance(repaired(), 10)
    assert not state.station.leak_active
    checked = inspect_installed_batch(state)
    assert checked.evidence[-1].code == "installed_batch_traced"
    assert checked.evidence[-1].method == "routine"
    under_load = replace(checked.state, operating_load="peak")
    once = advance(under_load, 1)
    assert not once.station.leak_active
    twice = advance(once, 1)
    assert twice.station.leak_active
    failure = next(item for item in reversed(twice.evidence) if item.kind == "failure")
    assert failure.batch_id is None
    assert failure.operating_load == "peak"
    inspected = inspect_installed_batch(twice)
    assert inspected.evidence[-1].code == "material_defect_confirmed"


def test_relevant_stress_test_exposes_failure_before_uncontrolled_peak_operation():
    state = repaired()
    first = inspect_installed_batch(state, method="peak")
    assert first.accepted
    assert not first.state.station.leak_active
    second = inspect_installed_batch(first.state, method="peak")
    assert second.accepted
    assert second.state.station.leak_active
    assert second.evidence[-1].code == "material_defect_confirmed"
    assert second.state.station.available_crew == state.station.available_crew - 2


def test_healthy_part_passes_same_stress_test_without_false_accusation():
    state = repaired(defective=False)
    tested = inspect_installed_batch(state, method="peak")
    assert tested.accepted
    assert not tested.state.station.leak_active
    assert tested.evidence[-1].code == "installed_batch_traced"


def test_stabilization_leaves_residual_damage_that_returns_under_load():
    state = advance(repaired(defective=False, residual=2, mode="stabilize"), 8)
    assert not state.station.leak_active
    failed = advance(replace(state, operating_load="peak"), 2)
    assert failed.station.leak_active
    finding = inspect_installed_batch(failed)
    assert finding.evidence[-1].code == "residual_damage_confirmed"
    assert finding.evidence[-1].batch_id is None
    fully_repaired = advance(repair_with_batch(finding.state, "local-b", mode="full").state, 2)
    assert not advance(fully_repaired, 8).station.leak_active
    assert fully_repaired.residual_damage_after_turns == 0


def test_replacing_faulty_part_removes_cause_without_a_scheduled_recurrence():
    state = advance(replace(repaired(), operating_load="peak"), 2)
    fixed = advance(repair_with_batch(state, "local-b").state, 2)
    assert not advance(fixed, 20).station.leak_active


def test_invalid_or_unstaffed_stress_inspection_is_atomic():
    state = repaired()
    rejected = inspect_installed_batch(state, method="magic")
    assert not rejected.accepted
    assert rejected.state is state
    no_crew = replace(state, station=replace(state.station, available_crew=0))
    denied = inspect_installed_batch(no_crew, method="peak")
    assert not denied.accepted
    assert denied.state is no_crew


def test_backup_failure_requires_actual_backup_operation():
    state = repaired()
    state = replace(state, installed_part=replace(state.installed_part, failure_load="backup"))
    assert not advance(replace(state, operating_load="peak"), 5).station.leak_active
    backed = replace(state, station=replace(state.station, backup_active=True, backup_oxygen=120))
    assert advance(backed, 2).station.leak_active


def test_fatal_oxygen_transition_stops_subsequent_defect_exposure_and_failure_events():
    state = repaired()
    doomed = replace(
        state,
        station=replace(state.station, oxygen=1, leak_active=True),
        installed_part=replace(state.installed_part, failure_load="any", defect_after_turns=1),
    )
    advanced = advance_world(doomed)
    assert not advanced.state.station.crew_alive
    assert advanced.state.installed_part.operating_turns == doomed.installed_part.operating_turns
    assert not any(item.kind == "failure" for item in advanced.evidence)


def test_inspection_reports_both_physical_causes_without_assigning_all_damage_to_supplier():
    state = repaired(defective=True, residual=1, mode="stabilize")
    state = replace(state, installed_part=replace(state.installed_part, defect_after_turns=1))
    failed = advance(replace(state, operating_load="peak"), 1)
    findings = inspect_installed_batch(failed).evidence
    assert {item.code for item in findings} == {
        "material_defect_confirmed",
        "residual_damage_confirmed",
    }
    residual = next(item for item in findings if item.code == "residual_damage_confirmed")
    assert residual.batch_id is None
