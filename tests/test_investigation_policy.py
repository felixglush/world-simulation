"""Public-evidence behavior for the parameterized investigation governor."""

from dataclasses import replace

import pytest

from station_control.domain import SensorReading
from station_control.governors import TradeCommand
from station_control.investigation import InvestigationGovernor
from station_control.trade import (
    PublicContract,
    PublicLot,
    PublicWorldView,
    TradeEvidence,
    TradeEvidenceKind,
)


def lot(
    batch_id,
    quantity,
    unit_price=20,
    *,
    resource="parts",
    unit=None,
    seller="industrial",
    contract_id=None,
    shipment_id=None,
):
    metadata = {}
    if contract_id is not None:
        metadata["contract_id"] = contract_id
    if shipment_id is not None:
        metadata["shipment_id"] = shipment_id
    return PublicLot(
        batch_id,
        quantity,
        unit_price,
        seller,
        resource,
        unit or ("canisters" if resource == "oxygen_feedstock" else "parts"),
        seller,
        **metadata,
    )


def evidence(
    kind,
    *,
    sequence,
    turn=1,
    asset="oxygen_system",
    batch=None,
    code=None,
    method=None,
    measured=None,
    unit=None,
    report=None,
    upstream=None,
    source=None,
    load=None,
    contract=None,
    shipment=None,
):
    return TradeEvidence(
        sequence=sequence,
        turn=turn,
        kind=TradeEvidenceKind(kind),
        message="public observation",
        world_id="station",
        asset_id=asset,
        batch_id=batch,
        contract_id=contract,
        shipment_id=shipment,
        finding_code=code,
        source_id=source,
        report_id=report,
        upstream_report_id=upstream,
        method=method,
        operating_load=load,
        measured_value=measured,
        measured_unit=unit,
    )


def view(**changes):
    base = PublicWorldView(
        world_id="station",
        turn=1,
        credits=500,
        parts=0,
        available_crew=3,
        crew_alive=True,
        offers=(
            lot("part-cheap", 2, 20),
            lot("part-reliable", 2, 40, seller="reliable_world"),
            lot("feedstock-first", 2, 12, resource="oxygen_feedstock"),
            lot("feedstock-second", 2, 18, resource="oxygen_feedstock", seller="ice_world"),
        ),
    )
    return replace(base, **changes)


def part_history(*items):
    return (
        PublicContract("part-order", "station", "industrial", "part-cheap", 2, 20, "settled"),
        *items,
    )


def completed_repair(turn=3, sequence=1, batch="part-cheap"):
    return evidence(
        "repair_complete", sequence=sequence, turn=turn, batch=batch, code="repair_complete"
    )


def failed(turn=4, sequence=2):
    return evidence("failure", sequence=sequence, turn=turn, code="oxygen_leak_detected")


def test_first_purchase_chooses_cheapest_external_parts_and_preserves_emergency_reserve():
    governor = InvestigationGovernor(emergency_reserve=60)
    observation = view(
        credits=100,
        offers=(
            lot("own", 2, 1, seller="station"),
            lot("too-expensive", 2, 30),
            lot("affordable", 2, 20, seller="reliable_world"),
        ),
    )

    command = governor.decide(observation)

    assert command == TradeCommand(
        "purchase",
        command_id=command.command_id,
        seller_id="reliable_world",
        batch_id="affordable",
        quantity=2,
    )
    assert len(command.command_id) <= 64
    assert governor.decide(observation).command_id == command.command_id

    below_reserve = governor.decide(replace(observation, credits=99))
    assert below_reserve is None


def test_arrived_initial_parts_are_repaired_using_the_configured_initial_mode():
    governor = InvestigationGovernor(initial_repair_mode="stabilize")
    observation = view(
        contracts=part_history(),
        local_lots=(lot("part-cheap", 2),),
        turn=4,
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.repair_mode) == (
        "repair",
        "part-cheap",
        "stabilize",
    )


def test_public_repair_not_needed_feedback_stops_retry_and_mission_continues_to_assay():
    from dataclasses import replace

    from station_control.scenarios import create_world
    from station_control.trade import PartLot, create_world_state, observe_world
    from station_control.trade_mission import run_trade_mission

    state = create_world_state(
        replace(create_world("normal", 0), scheduled_events=()),
    )
    state = replace(
        state,
        station_lots=state.station_lots
        + (
            PartLot(
                "feedstock-local",
                2,
                0,
                "station",
                resource="oxygen_feedstock",
                unit="canisters",
            ),
        ),
    )
    governor = InvestigationGovernor()

    result = run_trade_mission(state, (("station", governor),), turns=2)
    observation = observe_world(result.state, "station")

    assert any(
        item.kind == "command_rejected" and item.finding_code == "repair_not_needed"
        for item in observation.evidence
    )
    assert [decision.command.kind for decision in result.decisions] == ["repair", "assay"]
    assert result.decisions[0].rejection == "repair_not_needed"
    assert result.decisions[1].accepted


def test_unverified_claims_and_other_asset_findings_do_not_close_a_same_turn_failure():
    governor = InvestigationGovernor(stress_method="peak")
    observation = view(
        turn=9,
        contracts=part_history(),
        evidence=(
            completed_repair(turn=3, sequence=1),
            failed(turn=9, sequence=2),
            evidence(
                "report",
                sequence=3,
                turn=9,
                batch="part-cheap",
                code="material_defect_confirmed",
                report="claim-1",
            ),
            evidence(
                "inspection",
                sequence=4,
                turn=9,
                asset="other_system",
                batch="part-cheap",
                code="material_defect_confirmed",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.method) == ("inspect", "peak")


def test_same_turn_oxygen_material_finding_closes_failure_and_quarantines_only_unused_bad_stock():
    governor = InvestigationGovernor()
    observation = view(
        turn=9,
        contracts=part_history(),
        local_lots=(lot("part-cheap", 1), lot("part-good", 1)),
        evidence=(
            completed_repair(turn=3, sequence=1),
            failed(turn=9, sequence=2),
            evidence(
                "inspection",
                sequence=3,
                turn=9,
                batch="part-cheap",
                code="material_defect_confirmed",
                method="peak",
            ),
            evidence(
                "inspection",
                sequence=4,
                turn=9,
                code="residual_damage_confirmed",
                method="peak",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.quantity) == (
        "quarantine",
        "part-cheap",
        1,
    )

    after_quarantine = replace(
        observation,
        turn=10,
        local_lots=(lot("part-good", 1),),
        evidence=observation.evidence
        + (evidence("quarantine", sequence=4, turn=10, batch="part-cheap"),),
    )
    repair = governor.decide(after_quarantine)
    assert repair is not None
    assert (repair.kind, repair.batch_id, repair.repair_mode) == ("repair", "part-good", "full")


def test_material_defect_quarantine_and_repair_are_scoped_to_the_delivered_part():
    governor = InvestigationGovernor()
    finding = evidence(
        "inspection",
        sequence=3,
        turn=9,
        batch="shared-parts",
        contract="parts-contract-one",
        shipment="parts-shipment-one",
        code="material_defect_confirmed",
        method="peak",
    )
    contracts = (
        PublicContract(
            "parts-contract-one", "station", "industrial", "shared-parts", 1, 15, "settled"
        ),
        PublicContract(
            "parts-contract-two", "station", "reliable_world", "shared-parts", 1, 30, "settled"
        ),
    )
    history = (completed_repair(turn=3, sequence=1), failed(turn=9, sequence=2), finding)
    observation = view(
        turn=9,
        contracts=contracts,
        local_lots=(
            lot(
                "shared-parts",
                1,
                contract_id="parts-contract-one",
                shipment_id="parts-shipment-one",
            ),
            lot(
                "shared-parts",
                1,
                30,
                seller="reliable_world",
                contract_id="parts-contract-two",
                shipment_id="parts-shipment-two",
            ),
        ),
        evidence=history,
    )

    quarantine = governor.decide(observation)
    assert quarantine is not None
    assert (
        quarantine.kind,
        quarantine.batch_id,
        quarantine.shipment_id,
        quarantine.quantity,
    ) == ("quarantine", "shared-parts", "parts-shipment-one", 1)

    repaired = governor.decide(
        replace(
            observation,
            turn=10,
            local_lots=(observation.local_lots[1],),
            evidence=history
            + (
                evidence(
                    "quarantine",
                    sequence=4,
                    turn=10,
                    batch="shared-parts",
                    contract="parts-contract-one",
                    shipment="parts-shipment-one",
                ),
            ),
        )
    )
    assert repaired is not None
    assert (repaired.kind, repaired.batch_id, repaired.shipment_id) == (
        "repair",
        "shared-parts",
        "parts-shipment-two",
    )


def test_unscoped_material_finding_never_quarantines_a_provenanced_same_batch_lot():
    governor = InvestigationGovernor()
    observation = view(
        turn=9,
        contracts=part_history(),
        local_lots=(
            lot("shared-parts", 1),
            lot(
                "shared-parts",
                1,
                30,
                seller="reliable_world",
                contract_id="good-contract",
                shipment_id="good-shipment",
            ),
        ),
        evidence=(
            completed_repair(turn=3, sequence=1),
            failed(turn=9, sequence=2),
            evidence(
                "inspection",
                sequence=3,
                turn=9,
                batch="shared-parts",
                code="material_defect_confirmed",
                method="peak",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.shipment_id) == (
        "repair",
        "shared-parts",
        "good-shipment",
    )


def test_part_replacement_can_reuse_batch_id_from_a_different_supplier():
    governor = InvestigationGovernor()
    observation = view(
        turn=9,
        contracts=(
            PublicContract(
                "bad-parts-contract", "station", "industrial", "shared-parts", 1, 15, "settled"
            ),
        ),
        offers=(lot("shared-parts", 2, 30, seller="reliable_world"),),
        evidence=(
            failed(turn=9, sequence=2),
            evidence(
                "inspection",
                sequence=3,
                turn=9,
                batch="shared-parts",
                contract="bad-parts-contract",
                shipment="bad-parts-shipment",
                code="material_defect_confirmed",
                method="peak",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.seller_id, command.quantity) == (
        "purchase",
        "shared-parts",
        "reliable_world",
        1,
    )


def test_residual_damage_is_repaired_with_remaining_good_stock_without_batch_accusation():
    governor = InvestigationGovernor()
    observation = view(
        turn=9,
        contracts=part_history(),
        local_lots=(lot("part-good", 1),),
        evidence=(
            completed_repair(turn=3, sequence=1),
            failed(turn=9, sequence=2),
            evidence(
                "inspection",
                sequence=3,
                turn=9,
                code="residual_damage_confirmed",
                method="peak",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.repair_mode) == ("repair", "part-good", "full")
    assert command.kind != "quarantine"


def test_waiting_for_replacement_after_a_known_failure_does_not_activate_peak_load():
    governor = InvestigationGovernor(stress_cycles=1, feedstock_target=0)
    observation = view(
        turn=6,
        contracts=part_history(
            PublicContract(
                "replacement-order",
                "station",
                "reliable_world",
                "part-reliable",
                1,
                30,
                "in_transit",
            )
        ),
        evidence=(
            completed_repair(turn=3, sequence=1),
            evidence(
                "inspection", sequence=2, turn=4, code="installed_batch_traced", method="peak"
            ),
            failed(turn=5, sequence=3),
            evidence(
                "inspection",
                sequence=4,
                turn=5,
                batch="part-cheap",
                code="material_defect_confirmed",
                method="peak",
            ),
        ),
    )

    assert governor.decide(observation) is None


def test_stress_inspections_are_bounded_since_latest_repair_and_pause_during_repair():
    governor = InvestigationGovernor(stress_cycles=2, feedstock_target=0)
    repair = completed_repair(turn=5, sequence=10)
    one_pass = evidence(
        "inspection", sequence=11, turn=6, code="installed_batch_traced", method="peak"
    )
    two_passes = evidence(
        "inspection", sequence=12, turn=7, code="installed_batch_traced", method="peak"
    )
    old_pass = evidence(
        "inspection", sequence=2, turn=2, code="installed_batch_traced", method="peak"
    )

    first = governor.decide(view(turn=5, contracts=part_history(), evidence=(repair, old_pass)))
    second = governor.decide(
        view(turn=6, contracts=part_history(), evidence=(repair, old_pass, one_pass))
    )
    third = governor.decide(
        view(
            turn=7,
            contracts=part_history(),
            operating_load="peak",
            evidence=(
                repair,
                old_pass,
                one_pass,
                evidence("load_change", sequence=12, turn=7, load="peak"),
            ),
        )
    )
    peak = governor.decide(
        view(
            turn=8,
            contracts=part_history(),
            operating_load="routine",
            evidence=(repair, old_pass, one_pass, two_passes),
        )
    )
    done = governor.decide(
        view(
            turn=9,
            contracts=part_history(),
            operating_load="peak",
            evidence=(
                repair,
                old_pass,
                one_pass,
                two_passes,
                evidence("load_change", sequence=13, turn=9, load="peak"),
            ),
        )
    )
    pending = governor.decide(
        view(
            turn=6,
            repair_turns_remaining=1,
            contracts=part_history(),
            evidence=(repair, old_pass),
        )
    )

    assert first is not None and (first.kind, first.method) == ("inspect", "peak")
    assert second is not None and (second.kind, second.method) == ("inspect", "peak")
    assert third is not None and (third.kind, third.method) == ("inspect", "peak")
    assert peak is not None and (peak.kind, peak.operating_load) == ("load", "peak")
    assert done is None
    assert pending is None


def test_backup_stress_activates_available_backup_before_inspecting():
    governor = InvestigationGovernor(stress_method="backup", stress_cycles=1, feedstock_target=0)
    history = (completed_repair(turn=5, sequence=10),)
    inactive = view(
        turn=6,
        contracts=part_history(),
        backup_active=False,
        backup_oxygen=25,
        evidence=history,
    )

    activation = governor.decide(inactive)
    stress = governor.decide(replace(inactive, turn=7, backup_active=True, evidence=history))

    assert activation is not None and activation.kind == "backup"
    assert stress is not None and (stress.kind, stress.method) == ("inspect", "backup")


def test_empty_backup_does_not_repeat_unsupported_stress_or_count_a_pass():
    governor = InvestigationGovernor(stress_method="backup", stress_cycles=1, feedstock_target=0)
    observation = view(
        turn=6,
        contracts=part_history(),
        backup_active=False,
        backup_oxygen=0,
        evidence=(completed_repair(turn=5, sequence=10),),
    )

    first = governor.decide(observation)
    later = governor.decide(replace(observation, turn=7))

    assert first is None
    assert later is None


def test_backup_stress_diagnoses_an_already_observed_failure_routinely():
    governor = InvestigationGovernor(stress_method="backup", feedstock_target=0)
    command = governor.decide(
        view(
            turn=8,
            contracts=part_history(),
            backup_active=False,
            backup_oxygen=0,
            evidence=(completed_repair(turn=3, sequence=1), failed(turn=8, sequence=2)),
        )
    )

    assert command is not None and (command.kind, command.method) == ("inspect", "routine")


def test_benign_stress_pass_allows_one_peak_load_change():
    governor = InvestigationGovernor(stress_cycles=1, feedstock_target=0)
    observation = view(
        turn=6,
        contracts=part_history(),
        evidence=(
            completed_repair(turn=5, sequence=10),
            evidence(
                "inspection", sequence=11, turn=6, code="installed_batch_traced", method="peak"
            ),
        ),
    )

    command = governor.decide(observation)
    assert command is not None
    assert (command.kind, command.operating_load) == ("load", "peak")

    after_load = replace(
        observation,
        turn=7,
        operating_load="peak",
        evidence=observation.evidence
        + (evidence("load_change", sequence=12, turn=7, load="peak"),),
    )
    assert governor.decide(after_load) is None


def test_sensor_calibration_requires_fresh_independent_sources_and_runs_once_per_sample():
    governor = InvestigationGovernor(feedstock_target=0)
    observation = view(
        contracts=part_history(),
        oxygen_sensors=(
            SensorReading("sensor_a", 500, sampled_turn=8, source="source-a"),
            SensorReading("sensor_b", 489, sampled_turn=8, source="source-b"),
        ),
        turn=8,
    )

    command = governor.decide(observation)
    calibrated = replace(
        observation,
        evidence=(evidence("calibration", sequence=1, turn=8, asset="sensor_a"),),
    )
    assert command is not None and command.kind == "calibrate"
    assert governor.decide(calibrated) is None

    stale = replace(
        observation,
        oxygen_sensors=(
            SensorReading("sensor_a", 500, sampled_turn=7, source="source-a"),
            SensorReading("sensor_b", 489, sampled_turn=8, source="source-b"),
        ),
    )
    shared = replace(
        observation,
        oxygen_sensors=(
            SensorReading("sensor_a", 500, sampled_turn=8, source="shared"),
            SensorReading("sensor_b", 489, sampled_turn=8, source="shared"),
        ),
    )
    below_threshold = replace(
        observation,
        oxygen_sensors=(
            SensorReading("sensor_a", 500, sampled_turn=8, source="source-a"),
            SensorReading("sensor_b", 491, sampled_turn=8, source="source-b"),
        ),
    )
    assert governor.decide(stale) is None
    assert governor.decide(shared) is None
    assert governor.decide(below_threshold) is None


def test_rejected_incompatible_calibration_is_not_repeated_and_other_maintenance_continues():
    governor = InvestigationGovernor(feedstock_target=2)
    observation = view(
        contracts=part_history(),
        oxygen_sensors=(
            SensorReading("sensor_a", 500, sampled_turn=8, source="source-a"),
            SensorReading("sensor_b", 489, sampled_turn=8, source="source-b"),
        ),
        local_lots=(lot("feedstock-local", 1, resource="oxygen_feedstock"),),
        turn=8,
        evidence=(
            evidence(
                "command_rejected",
                sequence=3,
                turn=8,
                code="sensor_calibration_unavailable",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id) == ("assay", "feedstock-local")


def test_feedstock_is_assayed_once_and_clean_remainder_is_consumed():
    governor = InvestigationGovernor(feedstock_target=2)
    unsampled = view(
        contracts=(
            *part_history(),
            PublicContract(
                "feed-order",
                "station",
                "ice_world",
                "feedstock-first",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        local_lots=(lot("feedstock-first", 2, resource="oxygen_feedstock"),),
    )
    assay = governor.decide(unsampled)
    assert assay is not None and (assay.kind, assay.batch_id) == ("assay", "feedstock-first")

    measured_clean = replace(
        unsampled,
        local_lots=(lot("feedstock-first", 1, resource="oxygen_feedstock"),),
        evidence=(
            evidence(
                "assay",
                sequence=1,
                batch="feedstock-first",
                code="cargo_quality_measured",
                measured=100,
                unit="percent",
            ),
        ),
    )
    consume = governor.decide(measured_clean)
    assert consume is not None
    assert (consume.kind, consume.batch_id, consume.quantity) == ("consume", "feedstock-first", 1)

    exhausted = replace(measured_clean, local_lots=())
    assert governor.decide(exhausted) is None


def test_clean_assay_for_one_shipment_does_not_certify_another_same_batch_delivery():
    governor = InvestigationGovernor()
    assay_from_first_delivery = evidence(
        "assay",
        sequence=1,
        batch="shared-feedstock",
        contract="contract-one",
        shipment="shipment-one",
        code="cargo_quality_measured",
        measured=100,
        unit="percent",
    )
    observation = view(
        contracts=(
            *part_history(),
            PublicContract(
                "contract-two",
                "station",
                "ice_world",
                "shared-feedstock",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        local_lots=(
            lot(
                "shared-feedstock",
                1,
                resource="oxygen_feedstock",
                contract_id="contract-two",
                shipment_id="shipment-two",
            ),
        ),
        evidence=(assay_from_first_delivery,),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.shipment_id) == (
        "assay",
        "shared-feedstock",
        "shipment-two",
    )


def test_feedstock_consumption_uses_only_the_assayed_delivery_quantity():
    governor = InvestigationGovernor()
    observation = view(
        contracts=(
            *part_history(),
            PublicContract(
                "contract-one",
                "station",
                "ice_world",
                "shared-feedstock",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
            PublicContract(
                "contract-two",
                "station",
                "industrial",
                "shared-feedstock",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        local_lots=(
            lot(
                "shared-feedstock",
                1,
                resource="oxygen_feedstock",
                contract_id="contract-one",
                shipment_id="shipment-one",
            ),
            lot(
                "shared-feedstock",
                2,
                resource="oxygen_feedstock",
                contract_id="contract-two",
                shipment_id="shipment-two",
            ),
        ),
        evidence=(
            evidence(
                "assay",
                sequence=1,
                batch="shared-feedstock",
                contract="contract-one",
                shipment="shipment-one",
                code="cargo_quality_measured",
                measured=100,
                unit="percent",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.shipment_id, command.quantity) == (
        "consume",
        "shared-feedstock",
        "shipment-one",
        1,
    )


def test_poor_assay_quarantines_only_the_assayed_delivery_remainder():
    governor = InvestigationGovernor()
    observation = view(
        contracts=(
            *part_history(),
            PublicContract(
                "contract-one",
                "station",
                "ice_world",
                "shared-feedstock",
                3,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
            PublicContract(
                "contract-two",
                "station",
                "industrial",
                "shared-feedstock",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        local_lots=(
            lot(
                "shared-feedstock",
                2,
                resource="oxygen_feedstock",
                contract_id="contract-one",
                shipment_id="shipment-one",
            ),
            lot(
                "shared-feedstock",
                3,
                resource="oxygen_feedstock",
                contract_id="contract-two",
                shipment_id="shipment-two",
            ),
        ),
        evidence=(
            evidence(
                "assay",
                sequence=1,
                batch="shared-feedstock",
                contract="contract-one",
                shipment="shipment-one",
                code="cargo_quality_measured",
                measured=75,
                unit="percent",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.shipment_id, command.quantity) == (
        "quarantine",
        "shared-feedstock",
        "shipment-one",
        2,
    )


def test_poor_assay_quarantines_remainder_then_buys_a_different_batch_with_reserve():
    governor = InvestigationGovernor(emergency_reserve=60)
    poor = view(
        credits=200,
        contracts=(
            *part_history(),
            PublicContract(
                "feed-order",
                "station",
                "industrial",
                "feedstock-first",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        local_lots=(lot("feedstock-first", 1, resource="oxygen_feedstock"),),
        evidence=(
            evidence(
                "assay",
                sequence=1,
                batch="feedstock-first",
                code="cargo_quality_measured",
                measured=72,
                unit="percent",
            ),
        ),
    )

    quarantine = governor.decide(poor)
    assert quarantine is not None
    assert (quarantine.kind, quarantine.batch_id, quarantine.quantity) == (
        "quarantine",
        "feedstock-first",
        1,
    )

    replacement = governor.decide(
        replace(
            poor,
            turn=2,
            local_lots=(),
            evidence=poor.evidence
            + (evidence("quarantine", sequence=2, turn=2, asset=None, batch="feedstock-first"),),
        )
    )
    assert replacement is not None
    assert (replacement.kind, replacement.batch_id, replacement.quantity) == (
        "purchase",
        "feedstock-second",
        2,
    )


def test_feedstock_replacement_can_reuse_batch_id_from_a_different_supplier():
    governor = InvestigationGovernor()
    observation = view(
        contracts=(
            *part_history(),
            PublicContract(
                "poor-feedstock-contract",
                "station",
                "industrial",
                "shared-feedstock",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        offers=(
            lot(
                "shared-feedstock",
                2,
                15,
                resource="oxygen_feedstock",
                seller="ice_world",
            ),
        ),
        evidence=(
            evidence(
                "assay",
                sequence=1,
                batch="shared-feedstock",
                contract="poor-feedstock-contract",
                shipment="poor-feedstock-shipment",
                code="cargo_quality_measured",
                measured=70,
                unit="percent",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.seller_id, command.quantity) == (
        "purchase",
        "shared-feedstock",
        "ice_world",
        2,
    )


def test_two_failed_feedstock_deliveries_bound_quality_replacement_attempts():
    governor = InvestigationGovernor()
    observation = view(
        contracts=(
            *part_history(),
            PublicContract(
                "feed-contract-one",
                "station",
                "industrial",
                "feedstock-one",
                2,
                12,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
            PublicContract(
                "feed-contract-two",
                "station",
                "ice_world",
                "feedstock-two",
                2,
                15,
                "settled",
                "oxygen_feedstock",
                "canisters",
            ),
        ),
        evidence=(
            evidence(
                "assay",
                sequence=1,
                batch="feedstock-one",
                contract="feed-contract-one",
                shipment="feed-shipment-one",
                measured=70,
                unit="percent",
            ),
            evidence(
                "assay",
                sequence=2,
                batch="feedstock-two",
                contract="feed-contract-two",
                shipment="feed-shipment-two",
                measured=80,
                unit="percent",
            ),
        ),
    )

    assert governor.decide(observation) is None


def test_emergency_replacement_can_spend_the_preserved_reserve():
    governor = InvestigationGovernor(emergency_reserve=60)
    observation = view(
        credits=60,
        contracts=part_history(),
        offers=(lot("part-reliable", 2, 30, seller="reliable_world"),),
        evidence=(
            failed(turn=9, sequence=2),
            evidence(
                "inspection",
                sequence=3,
                turn=9,
                batch="part-cheap",
                code="material_defect_confirmed",
                method="peak",
            ),
        ),
    )

    command = governor.decide(observation)

    assert command is not None
    assert (command.kind, command.batch_id, command.quantity) == (
        "purchase",
        "part-reliable",
        1,
    )
    assert command.command_id.startswith("inv-purchase-")


def test_long_public_batch_ids_still_produce_distinct_bounded_command_ids():
    governor = InvestigationGovernor()
    prefix = "batch-" + "x" * 35
    first = governor.decide(view(offers=(lot(prefix + "a", 2),)))
    second = governor.decide(view(offers=(lot(prefix + "b", 2),)))

    assert first is not None and second is not None
    assert len(first.command_id) <= 64 and len(second.command_id) <= 64
    assert first.command_id != second.command_id


def test_visible_reports_are_traced_one_hop_at_lowest_priority():
    governor = InvestigationGovernor(feedstock_target=0)
    report = evidence(
        "report", sequence=1, turn=1, asset=None, report="report-a", source="source-a"
    )
    observation = view(contracts=part_history(), evidence=(report,))
    command = governor.decide(observation)

    assert command is not None and (command.kind, command.report_id) == ("trace", "report-a")

    traced = replace(
        observation,
        turn=2,
        evidence=(
            report,
            evidence(
                "trace",
                sequence=2,
                turn=2,
                asset=None,
                report="report-a",
                upstream="report-b",
                source="source-a",
            ),
        ),
    )
    second = governor.decide(traced)
    assert second is not None and (second.kind, second.report_id) == ("trace", "report-b")

    complete = replace(
        traced,
        turn=3,
        evidence=traced.evidence
        + (
            evidence(
                "trace",
                sequence=4,
                turn=3,
                asset=None,
                report="report-b",
                upstream="report-c",
                source="source-b",
            ),
        ),
    )
    assert governor.decide(complete) is None


def test_each_governor_instance_derives_progress_from_its_own_public_view():
    first = InvestigationGovernor()
    second = InvestigationGovernor()
    progressed = view(contracts=part_history(), local_lots=(lot("part-cheap", 2),))
    fresh = view()

    assert first.decide(progressed).kind == "repair"
    independent = second.decide(fresh)
    assert independent is not None and independent.kind == "purchase"


@pytest.mark.parametrize(
    "parameters",
    [
        {"emergency_reserve": -1},
        {"stress_cycles": True},
        {"stress_method": "secret"},
        {"stress_method": []},
        {"initial_repair_mode": "secret"},
        {"initial_repair_mode": []},
        {"feedstock_target": 4},
    ],
)
def test_policy_rejects_invalid_bounded_parameters(parameters):
    with pytest.raises(ValueError):
        InvestigationGovernor(**parameters)
