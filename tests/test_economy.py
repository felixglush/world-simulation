"""Behavioral contracts for finite production and stock-sensitive prices."""

from dataclasses import replace

import pytest

from station_control.domain import Delivery
from station_control.economy import (
    AGRICULTURAL_WORLD_RECIPE,
    ICE_MOON_RECIPE,
    RESOURCE_PRICE_RULES,
    ProductionRecipe,
    ResourceGovernor,
    ResourcePriceRule,
    ResourceStock,
    advance_economy,
    initial_resource_inventory,
    produce_inventory,
    stock_price,
)
from station_control.scenarios import create_world
from station_control.trade import (
    LedgerEntry,
    PartLot,
    PublicContract,
    PublicLot,
    PublicShipment,
    PublicWorldView,
    TradeEvidence,
    TradeEvidenceKind,
    WorldInventory,
    advance_world,
    create_world_state,
    observe_world,
    purchase_lot,
    repair_with_batch,
)


def inventory_by_resource(stocks: tuple[ResourceStock, ...]) -> dict[str, ResourceStock]:
    return {stock.resource: stock for stock in stocks}


def test_production_consumes_inputs_and_records_balanced_resource_movements() -> None:
    before = (
        ResourceStock("ice", "tonnes", 10, 20),
        ResourceStock("water", "tonnes", 1, 8),
    )
    recipe = ProductionRecipe("melt_ice", (("ice", 2),), (("water", 2),))

    result = produce_inventory(before, recipe, world_id="ice_moon", turn=3)

    after = inventory_by_resource(result.inventory)
    prior = inventory_by_resource(before)
    assert result.accepted
    assert after["ice"].quantity == 8
    assert after["water"].quantity == 3
    assert after["ice"].unit == after["water"].unit == "tonnes"
    assert all(isinstance(entry, LedgerEntry) for entry in result.ledger)
    assert {entry.resource: entry.delta for entry in result.ledger} == {"ice": -2, "water": 2}
    assert all(entry.account.startswith("inventory:ice_moon:") for entry in result.ledger)
    assert all(entry.turn == 3 for entry in result.ledger)
    assert all(entry.reference_id == "melt_ice" for entry in result.ledger)
    assert all(
        after[resource].quantity - prior[resource].quantity
        == sum(entry.delta for entry in result.ledger if entry.resource == resource)
        for resource in prior
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


def test_stock_price_is_bounded_integer_and_falls_as_stock_rises() -> None:
    prices = [stock_price(stock, 50, 4, 2, 12) for stock in (0, 10, 25, 50, 100)]

    assert all(isinstance(price, int) and 2 <= price <= 12 for price in prices)
    assert prices == sorted(prices, reverse=True)
    assert prices[3] == 4


def test_ice_moon_and_agricultural_world_start_with_finite_unit_stocks() -> None:
    ice_moon = inventory_by_resource(initial_resource_inventory("ice_moon"))
    agriculture = inventory_by_resource(initial_resource_inventory("agricultural_world"))

    assert ice_moon["ice"] == ResourceStock("ice", "tonnes", 100, 160)
    assert ice_moon["water"] == ResourceStock("water", "tonnes", 0, 80)
    assert agriculture["water"] == ResourceStock("water", "tonnes", 20, 100)
    assert agriculture["fertilizer"] == ResourceStock("fertilizer", "tonnes", 12, 60)
    assert agriculture["food"] == ResourceStock("food", "crates", 0, 100)


def test_authored_world_recipes_produce_water_and_food_from_finite_inputs() -> None:
    ice = produce_inventory(
        initial_resource_inventory("ice_moon"),
        ICE_MOON_RECIPE,
        world_id="ice_moon",
        turn=1,
    )
    farm = produce_inventory(
        initial_resource_inventory("agricultural_world"),
        AGRICULTURAL_WORLD_RECIPE,
        world_id="agricultural_world",
        turn=1,
    )

    assert ice.accepted
    assert inventory_by_resource(ice.inventory)["ice"].quantity == 96
    assert inventory_by_resource(ice.inventory)["water"].quantity == 4
    assert farm.accepted
    assert inventory_by_resource(farm.inventory)["water"].quantity == 17
    assert inventory_by_resource(farm.inventory)["fertilizer"].quantity == 11
    assert inventory_by_resource(farm.inventory)["food"].quantity == 4


def test_economy_advance_updates_resource_lots_and_shared_ledger() -> None:
    station = replace(create_world("normal", 0), scheduled_events=())
    state = create_world_state(station, industrial_parts=0, reliable_parts=0)
    stocks = initial_resource_inventory("ice_moon")
    ice_moon = WorldInventory(
        "ice_moon",
        100,
        tuple(
            PartLot(
                f"ice-moon-{stock.resource}",
                stock.quantity,
                1,
                "ice_moon",
                resource=stock.resource,
                unit=stock.unit,
            )
            for stock in stocks
        ),
        resource_capacities=tuple((stock.resource, stock.capacity) for stock in stocks),
        resource_reserves=(("water", 4),),
    )
    state = replace(state, inventories=state.inventories + (ice_moon,))

    advanced = advance_economy(
        state,
        productions=(("ice_moon", ICE_MOON_RECIPE),),
        price_rules=(("ice_moon", RESOURCE_PRICE_RULES[0]),),
    )

    produced_world = next(
        item for item in advanced.state.inventories if item.world_id == "ice_moon"
    )
    totals = {
        resource: sum(lot.quantity for lot in produced_world.lots if lot.resource == resource)
        for resource in ("ice", "water")
    }
    assert advanced.state.station.turn == state.station.turn + 1
    assert totals == {"ice": 96, "water": 4}
    production_ledger = [
        item for item in advanced.state.ledger if item.kind.startswith("production")
    ]
    assert all(isinstance(entry, LedgerEntry) for entry in production_ledger)
    assert {(entry.resource, entry.delta, entry.reference_id) for entry in production_ledger} == {
        ("ice", -4, "melt_ice"),
        ("water", 4, "melt_ice"),
    }


def test_station_production_can_convert_metal_into_parts_and_sync_station_inventory() -> None:
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
    assert {
        (entry.resource, entry.delta)
        for entry in advanced.state.ledger
        if entry.kind.startswith("production")
    } == {
        ("metal", -1),
        ("parts", 2),
    }


def test_legacy_parts_delivery_is_counted_separately_from_station_water_lots() -> None:
    station = replace(
        create_world("normal", 0),
        scheduled_events=(),
        parts=0,
        deliveries=(Delivery(due_turn=1, supply="parts", quantity=1),),
    )
    state = create_world_state(station, industrial_parts=0, reliable_parts=0)
    state = replace(
        state,
        station_lots=(PartLot("station-water", 4, 1, "station", resource="water", unit="tonnes"),),
    )

    advanced = advance_world(state).state

    parts = sum(lot.quantity for lot in advanced.station_lots if lot.resource == "parts")
    water = sum(lot.quantity for lot in advanced.station_lots if lot.resource == "water")
    assert (parts, water, advanced.station.parts) == (1, 4, 1)


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


def resource_trade_state(*, reserve: int = 4):
    station = replace(create_world("normal", 0), scheduled_events=(), parts=0)
    state = create_world_state(station, industrial_parts=0, reliable_parts=0)
    ice_moon = WorldInventory(
        "ice_moon",
        100,
        (
            PartLot(
                "ice-water",
                10,
                5,
                "ice_moon",
                resource="water",
                unit="tonnes",
            ),
        ),
        resource_capacities=(("water", 20),),
        resource_reserves=(("water", reserve),),
    )
    return replace(
        state,
        inventories=state.inventories + (ice_moon,),
        station_resource_capacities=(("water", 4),),
    )


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
            for lot in next(i for i in booked.inventories if i.world_id == "ice_moon").lots
        )
        == 7
    )

    rule = ResourcePriceRule("water", "tonnes", 10, 5, 1, 20)
    advanced = advance_economy(
        booked,
        price_rules=(("ice_moon", rule),),
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


def test_resource_purchase_cannot_break_seller_reserve_or_overbook_destination_storage() -> None:
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


def test_resource_purchase_rejects_unit_mismatch_with_existing_destination_stock() -> None:
    state = resource_trade_state()
    state = replace(
        state,
        station_lots=(PartLot("station-water", 0, 5, "station", resource="water", unit="litres"),),
    )

    result = purchase_lot(
        state,
        command_id="wrong-water-unit",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=1,
    )

    assert not result.accepted
    assert result.rejection == "unit_mismatch"
    assert result.state == state


def test_resource_purchase_rejects_unit_mismatch_with_incoming_cargo() -> None:
    state = resource_trade_state()
    litre_supplier = WorldInventory(
        "litre_supplier",
        100,
        (PartLot("litre-water", 2, 5, "litre_supplier", resource="water", unit="litres"),),
        resource_capacities=(("water", 10),),
    )
    state = replace(state, inventories=state.inventories + (litre_supplier,))

    tonnes_purchase = purchase_lot(
        state,
        command_id="tonnes-water-order",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=1,
    )
    mixed_unit_purchase = purchase_lot(
        tonnes_purchase.state,
        command_id="litres-water-order",
        batch_id="litre-water",
        buyer_id="station",
        seller_id="litre_supplier",
        quantity=1,
    )

    assert tonnes_purchase.accepted
    assert not mixed_unit_purchase.accepted
    assert mixed_unit_purchase.rejection == "unit_mismatch"
    assert mixed_unit_purchase.state == tonnes_purchase.state


def test_resource_purchase_respects_explicit_parts_storage_capacity() -> None:
    station = replace(create_world("normal", 100), scheduled_events=(), parts=0)
    state = create_world_state(station, industrial_parts=2, reliable_parts=0)
    state = replace(state, station_resource_capacities=(("parts", 1),))

    result = purchase_lot(
        state,
        command_id="parts-capacity-order",
        batch_id="industrial-batch-a",
        buyer_id="station",
        seller_id="industrial",
        quantity=2,
    )

    assert not result.accepted
    assert result.rejection == "storage_capacity"
    assert result.state == state


def test_market_quotes_each_repeated_batch_once_at_the_purchasable_lot_quantity() -> None:
    state = resource_trade_state()
    ice_moon = next(item for item in state.inventories if item.world_id == "ice_moon")
    split_batch = replace(
        ice_moon,
        lots=(
            PartLot("ice-water", 2, 5, "ice_moon", resource="water", unit="tonnes"),
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
    too_large = purchase_lot(
        state,
        command_id="split-batch-overbuy",
        batch_id="ice-water",
        buyer_id="station",
        seller_id="ice_moon",
        quantity=3,
    )

    assert len(offers) == 1
    assert offers[0].quantity == 2
    assert not too_large.accepted
    assert too_large.rejection == "insufficient_stock"
    assert too_large.state == state


def test_market_quote_uses_first_sellable_duplicate_batch_lot() -> None:
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


def public_resource_view(
    world_id: str,
    *,
    offers: tuple[PublicLot, ...],
    local_lots: tuple[PublicLot, ...] = (),
    credits: int = 100,
    contracts: tuple[PublicContract, ...] = (),
    shipments: tuple[PublicShipment, ...] = (),
    evidence: tuple[TradeEvidence, ...] = (),
) -> PublicWorldView:
    return PublicWorldView(
        world_id=world_id,
        turn=5,
        credits=credits,
        parts=0,
        offers=offers,
        local_lots=local_lots,
        contracts=contracts,
        shipments=shipments,
        evidence=evidence,
    )


def test_reserve_governor_buys_shortage_while_income_governor_waits_for_price() -> None:
    offer = PublicLot("water-batch", 5, 30, "ice_moon", "water", "tonnes", "ice_moon")
    observation = public_resource_view("farm", offers=(offer,))

    reserve = ResourceGovernor("farm", (("water", 5),), 0, "reserve").decide(observation)
    income = ResourceGovernor(
        "farm", (("water", 5),), 0, "income", (("water", "tonnes", 10),)
    ).decide(observation)

    assert reserve is not None
    assert reserve.kind == "purchase"
    assert reserve.buyer_id == "farm"
    assert reserve.seller_id == "ice_moon"
    assert reserve.quantity == 3
    assert income is None


def test_income_governor_buys_a_target_resource_when_its_public_price_is_favorable() -> None:
    offer = PublicLot("water-batch", 5, 8, "ice_moon", "water", "tonnes", "ice_moon")
    observation = public_resource_view("farm", offers=(offer,))

    command = ResourceGovernor(
        "farm", (("water", 5),), 0, "income", (("water", "tonnes", 10),)
    ).decide(observation)

    assert command is not None
    assert command.batch_id == "water-batch"
    assert command.quantity == 3


def test_reliable_governor_uses_public_partner_history_and_ignores_faulty_batches() -> None:
    offers = (
        PublicLot("bad-water", 5, 2, "bad_supplier", "water", "tonnes", "bad_supplier"),
        PublicLot("good-water", 5, 9, "good_supplier", "water", "tonnes", "good_supplier"),
    )
    contracts = (
        PublicContract(
            "bad-contract",
            "station",
            "bad_supplier",
            "bad-water",
            1,
            2,
            "settled",
            "water",
            "tonnes",
        ),
    )
    shipment = PublicShipment(
        "good-shipment",
        "good-contract",
        "good_supplier",
        "station",
        "good-water",
        3,
        "delivered",
        2,
        3,
        "water",
        "tonnes",
    )
    finding = TradeEvidence(
        1,
        4,
        TradeEvidenceKind.INSPECTION,
        "Public test finding.",
        "station",
        batch_id="bad-water",
        contract_id="bad-contract",
        finding_code="material_defect_confirmed",
    )
    observation = public_resource_view(
        "station", offers=offers, contracts=contracts, shipments=(shipment,), evidence=(finding,)
    )

    command = ResourceGovernor("station", (("water", 3),), 0, "reliable").decide(observation)

    assert command is not None
    assert command.seller_id == "good_supplier"
    assert command.batch_id == "good-water"


def test_resource_governor_counts_pending_contracts_and_preserves_cash_reserve() -> None:
    offer = PublicLot("water-batch", 5, 8, "ice_moon", "water", "tonnes", "ice_moon")
    pending = PublicContract(
        "pending-water", "farm", "ice_moon", "water-batch", 3, 8, "in_transit", "water", "tonnes"
    )
    observation = public_resource_view("farm", offers=(offer,), credits=100, contracts=(pending,))

    covered = ResourceGovernor("farm", (("water", 3),), 0, "reserve").decide(observation)
    cash_limited = ResourceGovernor("farm", (("water", 5),), 93, "reserve").decide(
        public_resource_view("farm", offers=(offer,), credits=100)
    )

    assert covered is None
    assert cash_limited is None


@pytest.mark.parametrize(
    ("stock", "target_stock", "base_price", "floor", "ceiling"),
    ((0, 20, 8, 2, 15), (40, 20, 8, 2, 15)),
)
def test_stock_price_never_exceeds_configured_bounds(
    stock: int, target_stock: int, base_price: int, floor: int, ceiling: int
) -> None:
    price = stock_price(stock, target_stock, base_price, floor, ceiling)

    assert floor <= price <= ceiling


def test_stock_price_rejects_a_ceiling_above_the_trade_price_limit() -> None:
    with pytest.raises(ValueError, match="invalid stock pricing parameters"):
        stock_price(10, 10, 1_000_001, 1, 1_000_001)


def test_stock_price_accepts_the_trade_price_limit() -> None:
    assert stock_price(1, 1, 1_000_000, 1_000_000, 1_000_000) == 1_000_000


@pytest.mark.parametrize("quantity", (1.5, True))
def test_production_rejects_non_integer_resource_quantities(quantity) -> None:
    before = (
        ResourceStock("ice", "tonnes", quantity, 10),
        ResourceStock("water", "tonnes", 0, 10),
    )
    recipe = ProductionRecipe("melt_ice", (("ice", 1),), (("water", 1),))

    result = produce_inventory(before, recipe, world_id="ice_moon")

    assert not result.accepted
    assert result.rejection == "invalid_inventory"
    assert result.inventory == before
    assert result.ledger == ()


@pytest.mark.parametrize("capacity", (10.5, True))
def test_production_rejects_non_integer_storage_capacity(capacity) -> None:
    before = (ResourceStock("ice", "tonnes", 1, capacity),)
    recipe = ProductionRecipe("melt_ice", (("ice", 1),), (("water", 1),))

    result = produce_inventory(before, recipe, world_id="ice_moon")

    assert not result.accepted
    assert result.rejection == "invalid_inventory"
    assert result.inventory == before
    assert result.ledger == ()


@pytest.mark.parametrize("quantity", (1.5, True))
def test_production_rejects_non_integer_recipe_quantities(quantity) -> None:
    before = (
        ResourceStock("ice", "tonnes", 5, 10),
        ResourceStock("water", "tonnes", 0, 10),
    )
    recipe = ProductionRecipe("melt_ice", (("ice", quantity),), (("water", 1),))

    result = produce_inventory(before, recipe, world_id="ice_moon")

    assert not result.accepted
    assert result.rejection == "invalid_recipe"
    assert result.inventory == before
    assert result.ledger == ()


@pytest.mark.parametrize(
    "argument",
    (
        (1.5, 20, 8, 2, 15),
        (True, 20, 8, 2, 15),
        (10, 20.5, 8, 2, 15),
        (10, 20, 8.5, 2, 15),
        (10, 20, 8, 2.5, 15),
        (10, 20, 8, 2, 15.5),
    ),
)
def test_stock_price_rejects_non_integer_inputs(argument) -> None:
    with pytest.raises(ValueError, match="invalid stock pricing parameters"):
        stock_price(*argument)
