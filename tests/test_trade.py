"""Behavior matrix for the in-memory trade and latent-defect domain."""

from dataclasses import asdict, replace

import pytest

from station_control.domain import Delivery
from station_control.scenarios import ScenarioFamily, create_world
from station_control.trade import (
    TradeEvidenceKind,
    advance_world,
    create_world_state,
    inspect_installed_batch,
    observe_world,
    purchase_parts,
    quarantine_batch,
    repair_with_batch,
)


def station_state(*, credits=100, parts=0, leak_active=False):
    return replace(
        create_world(ScenarioFamily.NORMAL, seed=0),
        scheduled_events=(),
        credits=credits,
        parts=parts,
        leak_active=leak_active,
    )


def advance(state, count):
    for _ in range(count):
        state = advance_world(state).state
    return state


def assert_credit_conserved(state, initial_total):
    world_credits = state.station.credits + sum(world.credits for world in state.inventories)
    assert world_credits + state.escrow_credits == initial_total


def purchase(state, *, command_id="order-1", batch_id="industrial-batch-a", quantity=1):
    return purchase_parts(
        state,
        command_id=command_id,
        batch_id=batch_id,
        quantity=quantity,
    )


def test_world_views_keep_each_world_private_while_showing_tradeable_batch_refs():
    state = create_world_state(station_state(), industrial_parts=2, reliable_parts=1)

    station = observe_world(state, "station")
    industrial = observe_world(state, "industrial")

    assert {offer.batch_id for offer in industrial.offers} == {
        "industrial-batch-a",
        "industrial-batch-b",
    }
    assert all(not hasattr(offer, "latent_defect") for offer in industrial.offers)
    assert all(not hasattr(offer, "defective") for offer in industrial.offers)
    assert "industrial" not in {lot.origin_world for lot in station.local_lots}
    assert not hasattr(station, "inventories")
    assert not hasattr(industrial, "station")
    assert not any("defect" in str(value).lower() for value in asdict(industrial).values())
    assert station.world_id == "station"
    assert industrial.world_id == "industrial"


def test_purchase_escrows_funds_and_reserves_stock_then_dispatches_arrives_and_settles_once():
    state = create_world_state(station_state(), travel_turns=2)
    initial_credits = state.station.credits + sum(world.credits for world in state.inventories)

    bought = purchase(state, quantity=2)
    duplicate = purchase(bought.state, quantity=2)

    assert bought.accepted
    assert duplicate.accepted and duplicate.duplicate
    assert duplicate.state == bought.state
    assert bought.contract_id == "contract:order-1"
    assert bought.shipment_id == "shipment:order-1"
    assert bought.state.station.credits < state.station.credits
    assert bought.state.escrow_credits > 0
    assert duplicate.state.inventories == bought.state.inventories
    assert_credit_conserved(bought.state, initial_credits)

    departure = advance_world(bought.state)
    assert departure.state.shipments[0].status == "in_transit"
    assert departure.state.shipments[0].departure_turn == 1
    assert departure.state.station.parts == 0
    assert any(item.kind is TradeEvidenceKind.DEPARTURE for item in departure.evidence)

    still_travelling = advance_world(departure.state)
    assert still_travelling.state.station.parts == 0
    assert still_travelling.state.shipments[0].status == "in_transit"

    arrival = advance_world(still_travelling.state)
    assert arrival.state.station.parts == 2
    assert arrival.state.shipments[0].status == "delivered"
    assert arrival.state.contracts[0].status == "settled"
    assert arrival.state.escrow_credits == 0
    assert any(item.kind is TradeEvidenceKind.ARRIVAL for item in arrival.evidence)
    assert any(item.kind is TradeEvidenceKind.SETTLEMENT for item in arrival.evidence)
    assert {entry.reference_id for entry in arrival.state.ledger if entry.kind == "settlement"} == {
        "contract:order-1"
    }
    assert_credit_conserved(arrival.state, initial_credits)

    repeated = advance_world(arrival.state)
    assert repeated.state.station.parts == 2
    assert not any(item.kind is TradeEvidenceKind.SETTLEMENT for item in repeated.evidence)
    assert_credit_conserved(repeated.state, initial_credits)


def test_purchase_tracer_bullet_exposes_only_public_offer_and_escrow_changes():
    state = create_world_state(station_state())
    before = observe_world(state, "station")
    seller_before = observe_world(state, "industrial")

    result = purchase(state, quantity=1)

    buyer_after = observe_world(result.state, "station")
    seller_after = observe_world(result.state, "industrial")
    assert result.accepted
    assert buyer_after.credits < before.credits
    assert buyer_after.parts == before.parts
    assert seller_after.parts == seller_before.parts - 1
    assert result.state.escrow_credits == result.state.contracts[0].total_price
    assert result.evidence[0].contract_id == result.contract_id
    assert result.evidence[0].shipment_id == result.shipment_id
    assert not hasattr(buyer_after, "installed_part")


def test_existing_station_delivery_and_trade_arrival_both_remain_in_inventory():
    station = replace(
        station_state(),
        deliveries=(Delivery(due_turn=1, supply="parts", quantity=1),),
    )
    state = create_world_state(station)

    arrived = advance(purchase(state).state, 3)

    assert arrived.station.parts == 2
    assert sum(lot.quantity for lot in arrived.station_lots) == 2
    assert {lot.batch_id for lot in arrived.station_lots if lot.quantity} == {
        "industrial-batch-a",
        "station-delivery-1",
    }


@pytest.mark.parametrize("quantity", [0, -1, 4, True])
def test_purchase_rejects_unbounded_quantities_without_mutating_state(quantity):
    state = create_world_state(station_state())

    result = purchase(state, quantity=quantity)

    assert not result.accepted
    assert result.rejection == "invalid_quantity"
    assert result.state is state
    assert result.evidence == ()


@pytest.mark.parametrize(
    ("state_changes", "batch_id", "quantity", "rejection"),
    [
        (
            {"station": replace(station_state(), credits=0)},
            "industrial-batch-a",
            1,
            "insufficient_credits",
        ),
        ({}, "industrial-batch-missing", 1, "unknown_batch"),
        ({}, "industrial-batch-a", 3, "insufficient_stock"),
        ({"shipment_capacity": 1}, "industrial-batch-a", 2, "shipment_capacity_exceeded"),
    ],
)
def test_purchase_rejects_invalid_stock_funds_and_capacity_atomically(
    state_changes, batch_id, quantity, rejection
):
    base_station = state_changes.get("station", station_state())
    config = {key: value for key, value in state_changes.items() if key != "station"}
    state = create_world_state(base_station, **config)

    result = purchase(state, batch_id=batch_id, quantity=quantity)

    assert not result.accepted
    assert result.rejection == rejection
    assert result.state is state
    assert result.evidence == ()
    assert state.contracts == ()
    assert state.shipments == ()


def test_command_id_cannot_be_reused_for_a_different_purchase():
    state = create_world_state(station_state())
    first = purchase(state, quantity=1)

    conflicting = purchase(first.state, batch_id="industrial-batch-b", quantity=1)

    assert not conflicting.accepted
    assert conflicting.rejection == "command_id_conflict"
    assert conflicting.state is first.state


def test_exhausted_batch_is_out_of_stock_and_zero_price_legacy_stock_cannot_be_sold():
    state = create_world_state(station_state(parts=1))
    bought = purchase(state, quantity=2)

    exhausted = purchase(bought.state, command_id="order-2", quantity=1)
    free_stock = purchase_parts(
        bought.state,
        command_id="order-3",
        quantity=1,
        batch_id="station-stock",
        buyer_id="industrial",
        seller_id="station",
    )

    assert exhausted.rejection == "insufficient_stock"
    assert exhausted.state is bought.state
    assert free_stock.rejection == "invalid_offer_price"
    assert free_stock.state is bought.state


def test_repair_installs_batch_only_after_real_completion_then_starts_latent_clock():
    state = create_world_state(station_state(leak_active=True), defect_after_turns=3)
    bought = purchase(state)
    arrived = advance(bought.state, 3)

    assigned = repair_with_batch(arrived, "industrial-batch-a")
    assert assigned.accepted
    assert assigned.state.installed_part is None
    assert assigned.state.pending_repair_batch_id == "industrial-batch-a"

    first_work = advance_world(assigned.state)
    completed = advance_world(first_work.state)
    assert completed.state.station.leak_active is False
    assert completed.state.station.repair_turns_remaining == 0
    assert completed.state.installed_part.batch_id == "industrial-batch-a"
    assert completed.state.installed_part.operating_turns == 0
    assert any(item.kind is TradeEvidenceKind.REPAIR_COMPLETE for item in completed.evidence)

    after_one = advance_world(completed.state)
    after_two = advance_world(after_one.state)
    assert not after_one.state.station.leak_active
    assert not after_two.state.station.leak_active
    failure = advance_world(after_two.state)
    assert failure.state.station.leak_active
    assert failure.state.installed_part.operating_turns == 3
    failure_event = next(
        item for item in failure.evidence if item.kind is TradeEvidenceKind.FAILURE
    )
    assert failure_event.asset_id == "oxygen_system"
    assert failure_event.batch_id is None
    assert failure_event.contract_id is None


def test_inspection_after_failure_finds_the_installed_batch_source_but_before_failure_is_scoped():
    state = create_world_state(station_state(leak_active=True), defect_after_turns=1)
    arrived = advance(purchase(state).state, 3)
    assigned = repair_with_batch(arrived, "industrial-batch-a")
    completed = advance(assigned.state, 2)

    pre_failure = inspect_installed_batch(completed)
    assert pre_failure.accepted
    assert pre_failure.evidence[0].kind is TradeEvidenceKind.INSPECTION
    assert pre_failure.evidence[0].batch_id == "industrial-batch-a"
    assert "no material defect observed" in pre_failure.evidence[0].message.lower()
    assert not any("defect confirmed" in item.message.lower() for item in pre_failure.evidence)

    failed = advance_world(pre_failure.state).state
    investigated = inspect_installed_batch(failed)
    finding = investigated.evidence[0]
    assert investigated.accepted
    assert finding.kind is TradeEvidenceKind.INSPECTION
    assert finding.world_id == "station"
    assert finding.asset_id == "oxygen_system"
    assert finding.batch_id == "industrial-batch-a"
    assert finding.contract_id == "contract:order-1"
    assert finding.shipment_id == "shipment:order-1"
    assert finding.code == "material_defect_confirmed"


def test_quarantine_and_reliable_replacement_remove_the_later_failure_cause():
    state = create_world_state(station_state(leak_active=True), defect_after_turns=2)
    arrived_bad = advance(purchase(state, quantity=2).state, 3)
    completed_bad_repair = advance(
        repair_with_batch(arrived_bad, "industrial-batch-a").state,
        2,
    )
    failed = advance(completed_bad_repair, 2)
    inspected = inspect_installed_batch(failed)
    assert inspected.evidence[0].code == "material_defect_confirmed"

    quarantined = quarantine_batch(inspected.state, "industrial-batch-a", quantity=1)
    assert quarantined.accepted
    assert quarantined.state.station.parts == 0
    assert any(item.kind is TradeEvidenceKind.QUARANTINE for item in quarantined.evidence)

    replacement = purchase(
        quarantined.state,
        command_id="order-2",
        batch_id="industrial-batch-b",
    )
    arrived_good = advance(replacement.state, 3)
    started = repair_with_batch(arrived_good, "industrial-batch-b")
    completed_good_repair = advance(started.state, 2)
    assert completed_good_repair.station.leak_active is False
    assert completed_good_repair.installed_part.batch_id == "industrial-batch-b"

    operated = advance(completed_good_repair, 6)
    assert operated.station.leak_active is False
    inspected_good = inspect_installed_batch(operated)
    assert "no material defect observed" in inspected_good.evidence[0].message.lower()


def test_repeated_purchases_of_one_batch_keep_shipment_provenance_separate_for_repairs():
    state = create_world_state(station_state(leak_active=True), defect_after_turns=1)
    first = purchase(state, command_id="order-first", batch_id="industrial-batch-a")
    second = purchase(
        first.state,
        command_id="order-second",
        batch_id="industrial-batch-a",
    )
    arrived = advance(second.state, 3)

    first_repair = repair_with_batch(arrived, "industrial-batch-a")
    first_complete = advance(first_repair.state, 2)
    assert first_complete.installed_part.contract_id == "contract:order-first"
    assert first_complete.installed_part.shipment_id == "shipment:order-first"

    failed = advance(first_complete, 1)
    assert failed.station.leak_active
    assert inspect_installed_batch(failed).evidence[0].contract_id == "contract:order-first"
    second_repair = repair_with_batch(failed, "industrial-batch-a")
    second_complete = advance(second_repair.state, 2)
    assert second_complete.installed_part.contract_id == "contract:order-second"
    assert second_complete.installed_part.shipment_id == "shipment:order-second"


def test_repair_inspection_and_quarantine_failures_preserve_the_complete_world_state():
    state = create_world_state(station_state())

    no_leak = repair_with_batch(state, "industrial-batch-a")
    no_installed = inspect_installed_batch(state)
    no_local_stock = quarantine_batch(state, "industrial-batch-a")

    assert no_leak.rejection == "repair_not_needed"
    assert no_installed.rejection == "no_part_installed"
    assert no_local_stock.rejection == "batch_not_in_station_inventory"
    assert no_leak.state is state
    assert no_installed.state is state
    assert no_local_stock.state is state
    assert no_leak.evidence == no_installed.evidence == no_local_stock.evidence == ()


@pytest.mark.parametrize("travel_turns", [0, -1, 11, True])
def test_world_rejects_unbounded_route_configuration(travel_turns):
    with pytest.raises(ValueError, match="travel_turns"):
        create_world_state(station_state(), travel_turns=travel_turns)
