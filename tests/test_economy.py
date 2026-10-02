"""Behavioral contracts for finite production and resource trade."""

from dataclasses import replace

from station_control.economy import (
    ICE_MOON_RECIPE,
    ProductionRecipe,
    ResourcePriceRule,
    ResourceStock,
    advance_economy,
    produce_inventory,
    stock_price,
)
from station_control.scenarios import create_world
from station_control.trade import (
    PartLot,
    WorldInventory,
    advance_world,
    create_world_state,
    observe_world,
    purchase_lot,
    repair_with_batch,
)


def inventory_by_resource(stocks: tuple[ResourceStock, ...]) -> dict[str, ResourceStock]:
    return {stock.resource: stock for stock in stocks}


def test_production_conserves_inventory_and_records_each_movement() -> None:
    before = (
        ResourceStock("ice", "tonnes", 10, 20),
        ResourceStock("water", "tonnes", 1, 8),
    )
    recipe = ProductionRecipe("melt_ice", (("ice", 2),), (("water", 2),))

    result = produce_inventory(before, recipe, world_id="ice_moon", turn=3)

    after = inventory_by_resource(result.inventory)
    assert result.accepted
    assert {resource: stock.quantity for resource, stock in after.items()} == {
        "ice": 8,
        "water": 3,
    }
    assert {entry.resource: entry.delta for entry in result.ledger} == {
        "ice": -2,
        "water": 2,
    }
    assert all(
        after[resource].quantity - stock.quantity
        == sum(entry.delta for entry in result.ledger if entry.resource == resource)
        for resource, stock in inventory_by_resource(before).items()
    )


def test_production_rejects_exhausted_inputs_without_partial_changes() -> None:
    before = (
        ResourceStock("water", "tonnes", 1, 8),
        ResourceStock("food", "crates", 0, 10),
    )
    recipe = ProductionRecipe("grow_food", (("water", 2),), (("food", 3),))

    result = produce_inventory(before, recipe, world_id="agricultural_world")

    assert not result.accepted
    assert result.rejection == "insufficient_inputs"
    assert result.inventory == before
    assert result.ledger == ()


def test_production_rejects_full_output_storage_without_consuming_inputs() -> None:
    before = (
        ResourceStock("water", "tonnes", 8, 12),
        ResourceStock("food", "crates", 5, 5),
    )
    recipe = ProductionRecipe("grow_food", (("water", 2),), (("food", 1),))

    result = produce_inventory(before, recipe, world_id="agricultural_world")

    assert not result.accepted
    assert result.rejection == "storage_capacity"
    assert result.inventory == before
    assert result.ledger == ()


def test_stock_price_is_bounded_and_falls_as_stock_rises() -> None:
    prices = [stock_price(stock, 50, 4, 2, 12) for stock in (0, 10, 25, 50, 100)]

    assert all(2 <= price <= 12 for price in prices)
    assert prices == sorted(prices, reverse=True)
    assert prices[3] == 4


def test_station_production_converts_metal_into_parts_and_updates_station_stock() -> None:
    station = replace(create_world("normal", 0), scheduled_events=(), parts=0)
    state = create_world_state(station, industrial_parts=0, reliable_parts=0)
    state = replace(
        state,
        station_lots=(
            PartLot("metal-stock", 2, 1, "station", resource="metal", unit="kg"),
            PartLot("parts-stock", 0, 1, "station"),
        ),
        station_resource_capacities=(("metal", 10), ("parts", 10)),
    )

    advanced = advance_economy(
        state,
        productions=(
            ("station", ProductionRecipe("forge_parts", (("metal", 1),), (("parts", 2),))),
        ),
    )

    metal = sum(lot.quantity for lot in advanced.state.station_lots if lot.resource == "metal")
    parts = sum(lot.quantity for lot in advanced.state.station_lots if lot.resource == "parts")
    assert (metal, parts, advanced.state.station.parts) == (1, 2, 2)


def resource_trade_state(*, reserve: int = 4):
    station = replace(create_world("normal", 0), scheduled_events=(), parts=0)
    state = create_world_state(station, industrial_parts=0, reliable_parts=0)
    ice_moon = WorldInventory(
        "ice_moon",
        100,
        (PartLot("ice-water", 10, 5, "ice_moon", resource="water", unit="tonnes"),),
        resource_capacities=(("water", 20),),
        resource_reserves=(("water", reserve),),
    )
    return replace(
        state,
        inventories=state.inventories + (ice_moon,),
        station_resource_capacities=(("water", 4),),
    )


def test_terminal_world_does_not_produce_or_reprice_after_advance_stops() -> None:
    state = resource_trade_state()
    ice_moon = next(item for item in state.inventories if item.world_id == "ice_moon")
    ice_moon = replace(
        ice_moon,
        lots=ice_moon.lots
        + (PartLot("ice-source", 100, 1, "ice_moon", resource="ice", unit="tonnes"),),
        resource_capacities=(("water", 20), ("ice", 160)),
    )
    state = replace(
        state,
        inventories=tuple(
            ice_moon if item.world_id == "ice_moon" else item for item in state.inventories
        ),
    )
    terminal = replace(state, station=replace(state.station, crew_alive=False))

    advanced = advance_economy(
        terminal,
        productions=(("ice_moon", ICE_MOON_RECIPE),),
        price_rules=(("ice_moon", ResourcePriceRule("water", "tonnes", 40, 10, 2, 30)),),
    )

    assert advanced.state == terminal


def test_resource_purchase_moves_stock_credits_and_frozen_cargo_through_trade_lifecycle() -> None:
    state = resource_trade_state()
    offer = next(
        item for item in observe_world(state, "station").offers if item.resource == "water"
    )

    assert (offer.quantity, offer.unit, offer.seller_world) == (6, "tonnes", "ice_moon")
    purchase = purchase_lot(
        state,
        command_id="water-order",
        batch_id=offer.batch_id,
        buyer_id="station",
        seller_id=offer.seller_world,
        quantity=3,
    )
    assert purchase.accepted
    booked = purchase.state
    contract = booked.contracts[-1]
    cargo = booked.shipments[-1].cargo
    assert (contract.resource, contract.unit, contract.total_price) == ("water", "tonnes", 15)
    assert cargo is not None and (cargo.quantity, cargo.unit_price) == (3, 5)
    assert booked.station.credits == 85
    assert (
        sum(
            lot.quantity
            for lot in next(item for item in booked.inventories if item.world_id == "ice_moon").lots
        )
        == 7
    )

    advanced = advance_economy(
        booked,
        price_rules=(("ice_moon", ResourcePriceRule("water", "tonnes", 10, 5, 1, 20)),),
    )
    source_lot = next(
        lot
        for lot in next(i for i in advanced.state.inventories if i.world_id == "ice_moon").lots
        if lot.resource == "water"
    )
    assert source_lot.unit_price == 7
    assert advanced.state.shipments[0].cargo.unit_price == 5

    state = advanced.state
    for _ in range(2):
        state = advance_world(state).state
    station_water = sum(lot.quantity for lot in state.station_lots if lot.resource == "water")
    ice_moon = next(item for item in state.inventories if item.world_id == "ice_moon")
    assert station_water == 3
    assert ice_moon.credits == 115
    assert state.escrow_credits == 0
    assert state.shipments[0].status == "delivered"
    assert any(entry.resource == "water" and entry.kind == "arrival" for entry in state.ledger)
    assert any(entry.resource == "credits" and entry.kind == "settlement" for entry in state.ledger)


def test_resource_purchase_preserves_seller_reserves_and_buyer_storage() -> None:
    state = resource_trade_state()
    reserve_limited = resource_trade_state(reserve=8)

    exceeds_reserve = purchase_lot(
        reserve_limited,
        command_id="too-much-water",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=3,
    )
    fits_once = purchase_lot(
        state,
        command_id="first-water-order",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=3,
    )
    exceeds_capacity = purchase_lot(
        fits_once.state,
        command_id="second-water-order",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=2,
    )

    assert not exceeds_reserve.accepted
    assert exceeds_reserve.rejection == "insufficient_stock"
    assert exceeds_reserve.state == reserve_limited
    assert fits_once.accepted
    assert not exceeds_capacity.accepted
    assert exceeds_capacity.rejection == "storage_capacity"
    assert exceeds_capacity.state == fits_once.state


def test_resource_purchase_rejects_units_conflicting_with_existing_or_incoming_stock() -> None:
    existing_stock = resource_trade_state()
    existing_stock = replace(
        existing_stock,
        station_lots=(PartLot("station-water", 0, 5, "station", resource="water", unit="litres"),),
    )
    rejected_existing = purchase_lot(
        existing_stock,
        command_id="wrong-water-unit",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=1,
    )

    incoming_stock = resource_trade_state()
    litre_supplier = WorldInventory(
        "litre_supplier",
        100,
        (PartLot("litre-water", 2, 5, "litre_supplier", resource="water", unit="litres"),),
        resource_capacities=(("water", 10),),
    )
    incoming_stock = replace(
        incoming_stock, inventories=incoming_stock.inventories + (litre_supplier,)
    )
    tonnes_purchase = purchase_lot(
        incoming_stock,
        command_id="tonnes-water-order",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=1,
    )
    rejected_incoming = purchase_lot(
        tonnes_purchase.state,
        command_id="litres-water-order",
        batch_id="litre-water",
        buyer_id="station",
        seller_id="litre_supplier",
        quantity=1,
    )

    assert not rejected_existing.accepted
    assert rejected_existing.rejection == "unit_mismatch"
    assert rejected_existing.state == existing_stock
    assert tonnes_purchase.accepted
    assert not rejected_incoming.accepted
    assert rejected_incoming.rejection == "unit_mismatch"
    assert rejected_incoming.state == tonnes_purchase.state


def test_market_quotes_a_repeated_batch_once_from_its_first_sellable_lot() -> None:
    state = resource_trade_state()
    ice_moon = next(item for item in state.inventories if item.world_id == "ice_moon")
    split_batch = replace(
        ice_moon,
        lots=(
            PartLot("ice-water", 0, 99, "ice_moon", resource="water", unit="tonnes"),
            PartLot("ice-water", 8, 5, "ice_moon", resource="water", unit="tonnes"),
        ),
    )
    state = replace(
        state,
        inventories=tuple(
            split_batch if inventory.world_id == "ice_moon" else inventory
            for inventory in state.inventories
        ),
    )

    offers = tuple(
        offer
        for offer in observe_world(state, "station").offers
        if offer.seller_world == "ice_moon" and offer.batch_id == "ice-water"
    )
    purchase = purchase_lot(
        state,
        command_id="first-sellable-water-order",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=3,
    )

    assert len(offers) == 1
    assert (offers[0].quantity, offers[0].unit_price) == (4, 5)
    assert purchase.accepted
    assert purchase.state.contracts[-1].unit_price == offers[0].unit_price


def test_repair_cannot_consume_a_non_part_resource_lot() -> None:
    station = replace(create_world("normal", 0), scheduled_events=(), parts=1, leak_active=True)
    state = create_world_state(station, industrial_parts=0, reliable_parts=0)
    state = replace(
        state,
        station_lots=state.station_lots
        + (PartLot("water-batch", 1, 5, "ice_moon", resource="water", unit="tonnes"),),
    )

    result = repair_with_batch(state, "water-batch")

    assert not result.accepted
    assert result.rejection == "batch_not_in_station_inventory"
    assert result.state == state
