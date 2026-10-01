"""Deception configured and exercised through the supported facade boundary."""

from dataclasses import replace

import pytest

from station_control.deception import TradeReport
from station_control.facade import SimulationFacade
from station_control.governors import TradeCommand
from station_control.scenarios import create_world
from station_control.trade import PartLot, observe_world


def simulation():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


@pytest.mark.parametrize(
    "changes",
    [
        {"failure_load": "magic"},
        {"yield_percent": True},
        {"yield_percent": 101},
        {"yield_percent": 50},
        {"resource": "metals", "latent_defect": True, "defect_after_turns": 2},
        {"resource": "metals", "failure_load": "peak"},
    ],
)
def test_initial_lot_condition_is_validated_before_world_registration(changes):
    facade = simulation()
    before = facade.state
    with pytest.raises(ValueError):
        facade.create_world("supplier", lots=(PartLot("lot", 2, 10, "supplier", **changes),))
    assert facade.state is before
    facade.create_world("supplier")


def test_configured_deception_remains_private_and_traces_are_dispatched_to_correct_world():
    facade = simulation()
    facade.create_world("supplier")
    facade.configure_deception(
        sensor_drift_per_turn=2,
        reports=(
            TradeReport("original", "supplier.qa", "supplier", "Basic check passed.", 0, 1),
            TradeReport(
                "relay",
                "port",
                "station",
                "Shipment is reliable.",
                0,
                2,
                upstream_report_id="original",
            ),
        ),
    )
    facade.create_decision_system(
        "station",
        commands=((2, TradeCommand("trace", report_id="relay")), (3, TradeCommand("calibrate"))),
    )
    facade.create_decision_system("supplier")
    initial = observe_world(facade.state, "station")
    assert "supplier.qa" not in repr(initial)
    result = facade.run(turns=3)
    assert all(decision.accepted for decision in result.decisions)
    assert {item.kind for item in result.events} >= {"report", "trace", "calibration"}
    assert "supplier.qa" in repr(observe_world(result.state, "station"))
    assert all(
        item.source_id != "port" and item.upstream_source_id != "port"
        for item in observe_world(result.state, "supplier").evidence
    )
    assert result.state.sensor_drift_per_turn == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"sensor_drift_per_turn": True},
        {"sensor_drift_per_turn": -1},
        {"sensor_drift_limit": 201},
        {"residual_damage_after_turns": 101},
        {"operating_load": "backup"},
        {"reports": None},
        {"reports": (TradeReport("r", "qa", "missing", "x", 0, 1),)},
    ],
)
def test_deception_configuration_rejects_atomically(settings):
    facade = simulation()
    before = facade.state
    with pytest.raises(ValueError):
        facade.configure_deception(**settings)
    assert facade.state is before


def test_deception_configuration_cannot_rewrite_causes_after_simulation_starts():
    facade = simulation()
    facade.run(turns=1)
    before = facade.state
    with pytest.raises(ValueError):
        facade.configure_deception(sensor_drift_per_turn=2)
    assert facade.state is before


def test_cargo_investigation_and_load_are_available_through_governor_commands():
    facade = simulation()
    facade.create_world(
        "supplier",
        lots=(
            PartLot(
                "feed",
                3,
                10,
                "supplier",
                resource="oxygen_feedstock",
                unit="canisters",
                yield_percent=50,
            ),
        ),
    )
    facade.create_decision_system(
        "station",
        commands=(
            (
                1,
                TradeCommand(
                    "purchase", "feed-order", seller_id="supplier", batch_id="feed", quantity=3
                ),
            ),
            (4, TradeCommand("assay", batch_id="feed")),
            (5, TradeCommand("consume", batch_id="feed", quantity=1)),
            (6, TradeCommand("quarantine", batch_id="feed", quantity=1)),
            (7, TradeCommand("load", operating_load="peak")),
        ),
    )
    result = facade.run(turns=7)
    assert all(decision.accepted for decision in result.decisions)
    assert {item.kind for item in result.events} >= {
        "assay",
        "consumption",
        "quarantine",
        "load_change",
    }
    assert result.state.operating_load == "peak"
    assert not any(lot.quantity for lot in result.state.station_lots)


def test_investigation_system_is_parameterized_and_does_not_accept_settings_for_other_policies():
    facade = simulation()
    policy = facade.create_decision_system(
        "station", kind="investigation", emergency_reserve=35, stress_cycles=3, feedstock_target=0
    )
    assert policy.emergency_reserve == 35
    assert policy.stress_cycles == 3
    other = simulation()
    with pytest.raises(ValueError):
        other.create_decision_system("station", stress_cycles=3)
    other.create_decision_system("station")


def test_fake_policy_receives_only_observations_and_can_investigate_without_hidden_config():
    class Inspector:
        def __init__(self):
            self.views = []

        def decide(self, observation):
            self.views.append(observation)
            if any(item.kind == "report" for item in observation.evidence):
                return TradeCommand("trace", report_id="local-report")
            return None

    facade = simulation()
    facade.configure_deception(
        reports=(
            TradeReport("local-report", "maintenance", "station", "Equipment is sound.", 0, 1),
        ),
        sensor_drift_per_turn=3,
    )
    inspector = Inspector()
    facade.create_decision_system("station", provider=inspector)
    result = facade.run(turns=1)
    assert result.decisions[0].accepted
    assert len(inspector.views) == 1
    assert not hasattr(inspector.views[0], "reports")
    assert not hasattr(inspector.views[0], "sensor_drift_per_turn")


def test_malformed_proposals_do_not_allow_new_investigation_actions_on_other_worlds():
    facade = simulation()
    facade.create_world("supplier")
    facade.create_decision_system(
        "supplier", commands=((1, TradeCommand("calibrate", buyer_id="supplier")),)
    )
    result = facade.run(turns=1)
    assert result.decisions[0].rejection == "unauthorized_world"
    assert not any(item.kind == "calibration" for item in result.events)


def test_unavailable_calibration_feedback_stops_repeating_unsupported_action():
    from station_control.domain import ScheduledEvent

    initial = replace(
        create_world("normal", 0), scheduled_events=(ScheduledEvent(1, "sensor_fault", "sensor_a"),)
    )
    facade = SimulationFacade(initial)
    facade.create_decision_system("station", kind="investigation", feedstock_target=0)
    result = facade.run(turns=8)
    rejected_calibrations = [
        item for item in result.decisions if item.command.kind == "calibrate" and not item.accepted
    ]
    assert len(rejected_calibrations) == 1
    assert any(item.code == "sensor_calibration_unavailable" for item in result.events)


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


def test_delivery_provenance_is_local_and_is_not_leaked_through_foreign_resale_offers():
    from station_control.trade import advance_world, purchase_lot

    facade = simulation()
    facade.create_world(
        "supplier",
        lots=(PartLot("feed", 3, 10, "supplier", resource="oxygen_feedstock", unit="canisters"),),
    )
    facade.create_world("buyer")
    ordered = purchase_lot(
        facade.state,
        command_id="private-buyer-order",
        buyer_id="buyer",
        seller_id="supplier",
        batch_id="feed",
        quantity=2,
    ).state
    for _ in range(3):
        ordered = advance_world(ordered).state
    local = observe_world(ordered, "buyer")
    station = observe_world(ordered, "station")
    assert local.local_lots[0].shipment_id is not None
    resale = next(lot for lot in station.offers if lot.seller_world == "buyer")
    assert resale.shipment_id is None
    assert resale.contract_id is None


def test_same_batch_label_from_two_worlds_preserves_investigated_shipment_scope():
    facade = simulation()
    facade.state = replace(facade.state, station=replace(facade.state.station, leak_active=True))
    facade.create_world(
        "a",
        lots=(
            PartLot(
                "shared", 2, 15, "a", latent_defect=True, defect_after_turns=1, failure_load="peak"
            ),
        ),
    )
    facade.create_world("b", lots=(PartLot("shared", 2, 25, "b"),))
    facade.create_decision_system(
        "station",
        commands=(
            (1, TradeCommand("purchase", "from-a", seller_id="a", batch_id="shared", quantity=2)),
            (2, TradeCommand("purchase", "from-b", seller_id="b", batch_id="shared", quantity=1)),
            (4, TradeCommand("repair", batch_id="shared", shipment_id="shipment:from-a")),
            (6, TradeCommand("load", operating_load="peak")),
            (7, TradeCommand("inspect")),
            (8, TradeCommand("quarantine", batch_id="shared", shipment_id="shipment:from-a")),
            (9, TradeCommand("repair", batch_id="shared", shipment_id="shipment:from-b")),
        ),
    )
    result = facade.run(turns=15)
    assert all(item.accepted for item in result.decisions)
    assert not result.state.station.leak_active
    assert result.state.installed_part.shipment_id == "shipment:from-b"
    quarantines = [item for item in result.events if item.kind == "quarantine"]
    assert [item.shipment_id for item in quarantines] == ["shipment:from-a"]
