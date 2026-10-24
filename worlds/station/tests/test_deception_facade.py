"""Deception configured and exercised through the supported facade boundary."""

from dataclasses import replace

import pytest

from station_control.facade import SimulationFacade
from station_control.governors import TradeCommand
from station_control.scenarios import create_world
from station_control.trade import PartLot, observe_world


def simulation():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


def test_deception_configuration_cannot_rewrite_causes_after_simulation_starts():
    facade = simulation()
    facade.run(turns=1)
    before = facade.state
    with pytest.raises(ValueError):
        facade.configure_deception(sensor_drift_per_turn=2)
    assert facade.state is before


def test_unavailable_calibration_feedback_stops_repeating_unsupported_action():
    from station_control.domain import ScheduledEvent

    initial = replace(
        create_world("normal", 0), scheduled_events=(ScheduledEvent(1, "sensor_fault", "sensor_a"),)
    )
    facade = SimulationFacade(
        initial,
        station_lots=(
            PartLot("feed", 2, 10, "station", resource="oxygen_feedstock", unit="canisters"),
        ),
    )
    facade.create_decision_system("station", kind="investigation")
    result = facade.run(turns=8)
    rejected_calibrations = [
        item for item in result.decisions if item.command.kind == "calibrate" and not item.accepted
    ]
    assert len(rejected_calibrations) == 1
    assert any(item.code == "sensor_calibration_unavailable" for item in result.events)
    assert sum(item.rejection == "repair_not_needed" for item in result.decisions) == 1
    assert any(item.kind == "consumption" for item in result.events)


def test_backup_stress_parameters_can_activate_and_observe_actual_backup_operation():
    initial = replace(create_world("normal", 0), scheduled_events=(), leak_active=True, parts=1)
    facade = SimulationFacade(initial)
    facade.create_decision_system(
        "station", kind="investigation", stress_method="backup", feedstock_target=0
    )
    result = facade.run(turns=8)
    assert any(item.command.kind == "backup" and item.accepted for item in result.decisions)
    assert any(item.kind == "inspection" and item.method == "backup" for item in result.events)
    assert all(item.rejection != "backup_not_operating" for item in result.decisions)


def test_foreign_trade_provenance_stays_private_when_cargo_is_resold():
    facade = simulation()
    facade.create_world(
        "supplier",
        lots=(PartLot("feed", 3, 10, "supplier", resource="oxygen_feedstock", unit="canisters"),),
    )
    facade.create_world("buyer")
    facade.create_decision_system(
        "buyer",
        commands=(
            (
                1,
                TradeCommand(
                    "purchase",
                    "private-order",
                    buyer_id="buyer",
                    seller_id="supplier",
                    batch_id="feed",
                    quantity=2,
                ),
            ),
        ),
    )
    result = facade.run(turns=4)
    local = observe_world(result.state, "buyer")
    station = observe_world(result.state, "station")
    assert local.local_lots[0].shipment_id is not None
    resale = next(lot for lot in station.offers if lot.seller_world == "buyer")
    assert resale.shipment_id is None
    assert resale.contract_id is None


def test_investigator_recovers_using_another_suppliers_same_batch_label():
    facade = SimulationFacade(
        replace(
            create_world("normal", 0), scheduled_events=(), parts=0, leak_active=True, credits=150
        )
    )
    facade.create_world(
        "a",
        lots=(
            PartLot(
                "shared", 2, 15, "a", latent_defect=True, defect_after_turns=1, failure_load="peak"
            ),
        ),
    )
    facade.create_world("b", lots=(PartLot("shared", 2, 25, "b"),))
    facade.create_decision_system("station", kind="investigation", feedstock_target=0)
    result = facade.run(turns=40)
    assert result.state.station.crew_alive
    assert not result.state.station.leak_active
    assert result.state.installed_part.origin_world == "b"
    assert all(item.accepted for item in result.decisions)
    quarantines = [item for item in result.events if item.kind == "quarantine"]
    assert quarantines
    bad_contracts = {item.contract_id for item in result.state.contracts if item.seller_id == "a"}
    assert all(item.contract_id in bad_contracts for item in quarantines)


def test_clean_assay_never_certifies_another_delivery_with_the_same_batch_label():
    from station_control.governors import ScriptedGovernor
    from station_control.investigation import InvestigationGovernor

    class PurchasingInspector:
        purchases = ScriptedGovernor(
            (
                (
                    1,
                    TradeCommand("purchase", "clean", seller_id="a", batch_id="shared", quantity=2),
                ),
                (2, TradeCommand("purchase", "poor", seller_id="b", batch_id="shared", quantity=2)),
            )
        )
        investigation = InvestigationGovernor()

        def decide(self, observation):
            if observation.turn <= 2:
                return self.purchases.decide(observation)
            return self.investigation.decide(observation)

    facade = SimulationFacade(replace(create_world("normal", 0), scheduled_events=()))
    for world_id, yield_percent in (("a", 100), ("b", 0)):
        facade.create_world(
            world_id,
            lots=(
                PartLot(
                    "shared",
                    2,
                    10,
                    world_id,
                    resource="oxygen_feedstock",
                    unit="canisters",
                    yield_percent=yield_percent,
                ),
            ),
        )
    facade.create_decision_system("station", provider=PurchasingInspector())
    result = facade.run(turns=15)
    assays = {
        item.shipment_id: item.measured_value for item in result.events if item.kind == "assay"
    }
    assert assays == {"shipment:clean": 100, "shipment:poor": 0}
    consumed = [item for item in result.events if item.kind == "consumption"]
    assert consumed and all(item.shipment_id == "shipment:clean" for item in consumed)
    quarantines = [item for item in result.events if item.kind == "quarantine"]
    assert [item.shipment_id for item in quarantines] == ["shipment:poor"]
    assert not any(
        lot.quantity for lot in result.state.station_lots if lot.resource == "oxygen_feedstock"
    )
