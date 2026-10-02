"""Equipment failures follow operating conditions and repairs remove their causes."""

from dataclasses import replace

from station_control.domain import Action, ActionKind, apply_action
from station_control.scenarios import create_world
from station_control.trade import (
    TradeEvidenceKind,
    TradeReport,
    advance_world,
    create_world_state,
    inspect_installed_batch,
    purchase_lot,
    quarantine_batch,
    repair_with_batch,
    set_operating_load,
)


def station_state(**changes):
    station = replace(create_world("normal", seed=0), scheduled_events=())
    return replace(station, **changes)


def advance(state, turns):
    for _ in range(turns):
        state = advance_world(state).state
    return state


def test_purchase_to_stress_investigation_and_replacement_removes_defect_and_residual_damage():
    state = create_world_state(
        station_state(leak_active=True),
        defect_after_turns=2,
        travel_turns=2,
    )
    state = replace(state, residual_damage_after_turns=2)
    seller = state.inventories[0]
    bad_lot = replace(seller.lots[0], failure_load="peak")
    state = replace(state, inventories=(replace(seller, lots=(bad_lot, *seller.lots[1:])),))
    purchase = purchase_lot(
        state,
        command_id="bad-order",
        batch_id="industrial-batch-a",
        quantity=2,
    )
    arrived = advance(purchase.state, 3)
    bad_lot = next(lot for lot in arrived.station_lots if lot.batch_id == "industrial-batch-a")
    assert bad_lot.contract_id == purchase.contract_id
    assert bad_lot.shipment_id == purchase.shipment_id

    assigned = repair_with_batch(arrived, "industrial-batch-a", mode="stabilize")
    installed = advance(assigned.state, 2)
    assert installed.installed_part.batch_id == "industrial-batch-a"
    assert installed.residual_damage_after_turns == 2

    routine = inspect_installed_batch(installed)
    assert routine.evidence[0].method == "routine"
    assert routine.evidence[0].code == "installed_batch_traced"
    assert not routine.state.station.leak_active
    assert routine.state.installed_part.operating_turns == 0

    peak = set_operating_load(routine.state, "peak").state
    first_stress = inspect_installed_batch(peak, method="peak")
    assert first_stress.accepted
    assert not first_stress.state.station.leak_active
    second_stress = inspect_installed_batch(first_stress.state, method="peak")
    assert second_stress.state.station.leak_active
    assert any(item.kind is TradeEvidenceKind.FAILURE for item in second_stress.evidence)
    assert {
        item.code for item in second_stress.evidence if item.kind is TradeEvidenceKind.INSPECTION
    } == {"material_defect_confirmed", "residual_damage_confirmed"}
    findings = [
        item for item in second_stress.evidence if item.kind is TradeEvidenceKind.INSPECTION
    ]
    assert next(item for item in findings if item.code == "material_defect_confirmed").batch_id == (
        "industrial-batch-a"
    )
    assert (
        next(item for item in findings if item.code == "residual_damage_confirmed").batch_id is None
    )

    quarantined = quarantine_batch(second_stress.state, "industrial-batch-a")
    assert quarantined.accepted
    assert (
        sum(
            lot.quantity
            for lot in quarantined.state.station_lots
            if lot.batch_id == "industrial-batch-a"
        )
        == 0
    )
    replacement = purchase_lot(
        quarantined.state,
        command_id="good-order",
        batch_id="industrial-batch-b",
        quantity=1,
    )
    arrived_good = advance(replacement.state, 3)
    completed = advance(repair_with_batch(arrived_good, "industrial-batch-b").state, 2)
    assert completed.installed_part.batch_id == "industrial-batch-b"
    assert completed.installed_part.contract_id == replacement.contract_id
    assert completed.residual_damage_after_turns == 0

    operated = advance(completed, 6)
    assert not operated.station.leak_active
    verified = inspect_installed_batch(operated, method="peak")
    assert verified.accepted
    assert not verified.state.station.leak_active
    assert verified.evidence[0].method == "peak"
    assert verified.evidence[0].code == "installed_batch_traced"


def test_backup_sensitive_defect_fails_only_when_the_backup_is_actually_operating():
    state = create_world_state(station_state(leak_active=True), defect_after_turns=1)
    seller = state.inventories[0]
    bad_lot = replace(seller.lots[0], failure_load="backup")
    state = replace(state, inventories=(replace(seller, lots=(bad_lot, *seller.lots[1:])),))
    bought = purchase_lot(state, command_id="backup-order", batch_id=bad_lot.batch_id, quantity=1)
    arrived = advance(bought.state, 3)
    installed = advance(repair_with_batch(arrived, bad_lot.batch_id).state, 2)

    peak = advance(set_operating_load(installed, "peak").state, 3)
    assert not peak.station.leak_active
    assert peak.installed_part.operating_turns == 0

    backup_station = replace(peak.station, backup_oxygen=120)
    backup = apply_action(backup_station, Action(ActionKind.ACTIVATE_BACKUP))
    assert backup.accepted
    failed = advance_world(replace(peak, station=backup.state))
    assert failed.state.station.leak_active
    failure = next(item for item in failed.evidence if item.kind is TradeEvidenceKind.FAILURE)
    assert failure.operating_load == "backup"
    assert failed.state.installed_part.defect_confirmed


def test_fatal_oxygen_turn_stops_defect_exposure_sensor_drift_and_due_report_publication():
    state = create_world_state(station_state(leak_active=True), defect_after_turns=1)
    bought = purchase_lot(state, command_id="terminal-order", quantity=1)
    installed = advance(
        repair_with_batch(advance(bought.state, 3), "industrial-batch-a").state,
        2,
    )
    current_turn = installed.station.turn
    report = TradeReport(
        "due-report",
        "outside.qa",
        "station",
        "Inspection passed.",
        current_turn,
        current_turn + 1,
    )
    at_risk = replace(
        installed,
        station=replace(installed.station, oxygen=1, leak_active=True),
        sensor_drift_per_turn=4,
        sensor_drift_bias=2,
        reports=(report,),
    )
    operating_turns = at_risk.installed_part.operating_turns

    terminal = advance_world(at_risk)

    assert not terminal.state.station.crew_alive
    assert terminal.state.installed_part.operating_turns == operating_turns
    assert terminal.state.sensor_drift_bias == 2
    assert all(
        item.kind not in {TradeEvidenceKind.FAILURE, TradeEvidenceKind.REPORT}
        for item in terminal.evidence
    )
