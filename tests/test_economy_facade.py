"""Economy worlds and policy instances assembled entirely from facade parameters."""

from dataclasses import replace

from station_control.economy import ProductionRecipe, ResourcePriceRule
from station_control.facade import SimulationFacade
from station_control.governors import TradeCommand
from station_control.scenarios import create_world
from station_control.trade import PartLot, observe_world


def simulation():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


def water_lot(world_id, quantity=8):
    return PartLot(f"{world_id}-water", quantity, 2, world_id, resource="water", unit="tonnes")


def test_custom_resource_world_preserves_private_reserve_and_moves_real_commodity():
    facade = simulation()
    facade.create_world(
        "cistern",
        credits=20,
        lots=(water_lot("cistern"),),
        resource_capacities=(("water", 10),),
        resource_reserves=(("water", 5),),
    )
    local = observe_world(facade.state, "cistern")
    station = observe_world(facade.state, "station")
    assert local.local_lots[0].quantity == 8
    assert station.offers[0].quantity == 3
    facade.create_decision_system(
        "station",
        commands=(
            (
                1,
                TradeCommand(
                    "purchase",
                    "water-order",
                    seller_id="cistern",
                    batch_id="cistern-water",
                    quantity=3,
                ),
            ),
        ),
    )
    result = facade.run(turns=4)
    assert result.state.station.parts == 0
    assert sum(lot.quantity for lot in result.state.station_lots if lot.resource == "water") == 3
    assert result.state.contracts[0].resource == "water"
    assert result.state.contracts[0].status == "settled"
    assert observe_world(result.state, "cistern").credits == 26


def test_production_and_prices_are_configurable_for_arbitrary_world_ids():
    facade = simulation()
    facade.create_world(
        "orchard",
        lots=(
            water_lot("orchard", 8),
            PartLot("orchard-food", 0, 4, "orchard", resource="food", unit="crates"),
        ),
        resource_capacities=(("water", 10), ("food", 20)),
        production_recipe=ProductionRecipe("local_harvest", (("water", 2),), (("food", 3),)),
        price_rules=(ResourcePriceRule("food", "crates", 6, 4, 1, 10),),
    )
    result = facade.run(turns=2)
    stocks = {lot.resource: lot.quantity for lot in result.state.inventories[0].lots}
    assert stocks == {"water": 4, "food": 6}
    assert (
        next(lot.unit_price for lot in result.state.inventories[0].lots if lot.resource == "food")
        == 4
    )
    assert (
        sum(
            entry.delta
            for entry in result.state.ledger
            if entry.kind == "production_output" and entry.resource == "food"
        )
        == 6
    )


def test_price_configuration_is_local_to_each_world():
    facade = simulation()
    for identity, base in (("left", 2), ("right", 4)):
        facade.create_world(
            identity,
            lots=(water_lot(identity, 10),),
            resource_capacities=(("water", 20),),
            price_rules=(ResourcePriceRule("water", "tonnes", 10, base, 1, 8),),
        )
    facade.run(turns=1)
    prices = {
        offer.seller_world: offer.unit_price
        for offer in observe_world(facade.state, "station").offers
    }
    assert prices == {"left": 2, "right": 4}


def test_resource_governors_have_independent_identity_and_parameters():
    facade = simulation()
    facade.create_world("a", credits=100)
    facade.create_world("b", credits=100)
    facade.create_world(
        "cistern", lots=(water_lot("cistern"),), resource_capacities=(("water", 10),)
    )
    first = facade.create_decision_system(
        "a", kind="resources", resource_targets=(("water", 3),), cash_reserve=0, strategy="reserve"
    )
    second = facade.create_decision_system(
        "b",
        kind="resources",
        resource_targets=(("water", 3),),
        cash_reserve=100,
        strategy="reserve",
    )
    assert first is not second
    result = facade.run(turns=1)
    assert [contract.buyer_id for contract in result.state.contracts] == ["a"]


def test_income_policy_uses_custom_world_price_parameters_for_new_resources():
    facade = simulation()
    facade.create_world(
        "refiner",
        lots=(PartLot("refiner-ore", 0, 7, "refiner", resource="ore", unit="tonnes"),),
        resource_capacities=(("ore", 20),),
        price_rules=(ResourcePriceRule("ore", "tonnes", 10, 7, 2, 20),),
    )
    facade.create_world(
        "mine",
        lots=(PartLot("mine-ore", 20, 6, "mine", resource="ore", unit="tonnes"),),
        resource_capacities=(("ore", 30),),
    )
    facade.create_decision_system(
        "refiner",
        kind="resources",
        resource_targets=(("ore", 5),),
        cash_reserve=0,
        strategy="income",
    )
    result = facade.run(turns=1)
    assert len(result.state.contracts) == 1
    assert result.state.contracts[0].buyer_id == "refiner"
    assert result.state.contracts[0].unit_price == 6
    assert result.state.contracts[0].quantity == 3


def test_default_station_can_create_resource_policy_with_free_initial_parts():
    facade = SimulationFacade()
    facade.create_decision_system("station", kind="resources", resource_targets=(("parts", 2),))
    assert facade.run(turns=1).state.station.parts >= 0


def test_explicit_parts_storage_rejects_oversized_legacy_delivery_at_bootstrap():
    import pytest

    from station_control.domain import Delivery

    initial = replace(create_world("normal", 0), parts=0, deliveries=(Delivery(1, "parts", 2),))
    with pytest.raises(ValueError, match="storage"):
        SimulationFacade(initial, station_resource_capacities=(("parts", 1),))


def test_legacy_delivery_reserves_space_against_new_parts_purchase():
    from station_control.domain import Delivery
    from station_control.trade import purchase_parts

    initial = replace(create_world("normal", 0), parts=0, deliveries=(Delivery(2, "parts", 1),))
    facade = SimulationFacade(initial, station_resource_capacities=(("parts", 2),))
    facade.create_world("industrial", lots=(PartLot("parts", 3, 10, "industrial"),))
    result = purchase_parts(facade.state, command_id="extra", batch_id="parts", quantity=2)
    assert not result.accepted
    assert result.rejection == "storage_capacity"
    assert result.state is facade.state


def test_station_production_reserves_storage_for_legacy_parts_arrival():
    from station_control.domain import Delivery

    initial = replace(create_world("normal", 0), parts=0, deliveries=(Delivery(2, "parts", 1),))
    facade = SimulationFacade(
        initial,
        station_lots=(PartLot("metal", 2, 5, "station", resource="metal", unit="tonnes"),),
        station_resource_capacities=(("parts", 2),),
    )
    from station_control.economy import advance_economy

    result = advance_economy(
        facade.state,
        productions=(("station", ProductionRecipe("forge", (("metal", 1),), (("parts", 2),))),),
    )
    assert result.state.station.parts == 0
    assert not any(entry.kind == "production_output" for entry in result.state.ledger)
    arrived = advance_economy(result.state)
    assert arrived.state.station.parts == 1
    assert sum(lot.quantity for lot in arrived.state.station_lots if lot.resource == "parts") == 1


def test_direct_domain_rejects_impossible_storage_before_delivering_or_accounting():
    import pytest

    from station_control.domain import Delivery
    from station_control.trade import advance_world, create_world_state

    initial = replace(create_world("normal", 0), parts=0, deliveries=(Delivery(1, "parts", 2),))
    state = replace(create_world_state(initial), station_resource_capacities=(("parts", 1),))
    with pytest.raises(ValueError, match="storage"):
        advance_world(state)
    assert state.station.parts == 0
    assert not state.ledger


def test_over_limit_price_rule_rejects_world_without_registering_it():
    import pytest

    facade = simulation()
    before = facade.state
    with pytest.raises(ValueError, match="pricing"):
        facade.create_world(
            "unpriced",
            lots=(water_lot("unpriced"),),
            price_rules=(ResourcePriceRule("water", "tonnes", 10, 1_500_000, 1, 1_500_000),),
        )
    assert facade.state is before
    facade.create_world("unpriced", lots=(water_lot("unpriced"),))
