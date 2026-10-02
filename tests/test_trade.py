"""Behavioral integration tests for trade and latent defects."""

from dataclasses import asdict, replace

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


def station_state(
    *,
    credits=100,
    parts=0,
    leak_active=False,
    repair_notice_delay_turns=0,
    deliveries=(),
):
    return replace(
        create_world(ScenarioFamily.NORMAL, seed=0),
        scheduled_events=(),
        deliveries=deliveries,
        credits=credits,
        parts=parts,
        leak_active=leak_active,
        repair_notice_delay_turns=repair_notice_delay_turns,
    )


def advance(state, count):
    for _ in range(count):
        state = advance_world(state).state
    return state


def assert_credit_conserved(state, initial_total):
    world_credits = state.station.credits + sum(world.credits for world in state.inventories)
    assert world_credits + state.escrow_credits == initial_total


def purchase(
    state,
    *,
    command_id="order-1",
    batch_id="industrial-batch-a",
    quantity=1,
):
    return purchase_parts(
        state,
        command_id=command_id,
        batch_id=batch_id,
        quantity=quantity,
    )


def test_public_world_views_show_tradeable_stock_without_private_defect_data():
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


def test_trade_orders_settle_once_and_preserve_same_batch_provenance_through_repair():
    station = station_state(
        leak_active=True,
        deliveries=(Delivery(due_turn=1, supply="parts", quantity=1),),
    )
    state = create_world_state(station, industrial_parts=2, reliable_parts=1)
    initial_credits = state.station.credits + sum(world.credits for world in state.inventories)
    buyer_before = observe_world(state, "station")
    seller_before = observe_world(state, "industrial")

    first = purchase(state, command_id="order-1")
    conflict = purchase(first.state, command_id="order-1", batch_id="industrial-batch-b")
    duplicate = purchase(first.state, command_id="order-1")
    second = purchase(first.state, command_id="order-2")

    assert first.accepted
    assert conflict.rejection == "command_id_conflict"
    assert conflict.state is first.state
    assert duplicate.accepted and duplicate.duplicate
    assert duplicate.state is first.state
    assert len(first.state.contracts) == 1
    assert second.accepted
    assert len(second.state.contracts) == 2
    assert second.state.escrow_credits == sum(
        contract.total_price for contract in second.state.contracts
    )
    assert_credit_conserved(second.state, initial_credits)

    buyer_after = observe_world(second.state, "station")
    seller_after = observe_world(second.state, "industrial")
    assert buyer_after.credits < buyer_before.credits
    assert buyer_after.parts == buyer_before.parts
    assert seller_after.parts == seller_before.parts - 2
    assert {offer.batch_id for offer in seller_after.offers} == {"industrial-batch-b"}

    sold_out = purchase(second.state, command_id="order-sold-out")
    assert sold_out.rejection == "insufficient_stock"
    assert sold_out.state is second.state

    departure = advance_world(second.state)
    assert departure.state.station.parts == 1
    assert {shipment.status for shipment in departure.state.shipments} == {"in_transit"}
    assert sum(item.kind is TradeEvidenceKind.DEPARTURE for item in departure.evidence) == 2

    still_travelling = advance_world(departure.state)
    assert still_travelling.state.station.parts == 1
    assert {shipment.status for shipment in still_travelling.state.shipments} == {"in_transit"}

    arrival = advance_world(still_travelling.state)
    assert arrival.state.station.parts == 3
    assert {shipment.status for shipment in arrival.state.shipments} == {"delivered"}
    assert {contract.status for contract in arrival.state.contracts} == {"settled"}
    assert arrival.state.escrow_credits == 0
    assert {lot.batch_id for lot in arrival.state.station_lots if lot.quantity} == {
        "industrial-batch-a",
        "station-delivery-1",
    }
    purchased_lots = [
        lot for lot in arrival.state.station_lots if lot.batch_id == "industrial-batch-a"
    ]
    assert {lot.contract_id for lot in purchased_lots} == {
        "contract:order-1",
        "contract:order-2",
    }
    assert {lot.shipment_id for lot in purchased_lots} == {
        "shipment:order-1",
        "shipment:order-2",
    }
    assert sum(item.kind is TradeEvidenceKind.ARRIVAL for item in arrival.evidence) == 2
    assert sum(item.kind is TradeEvidenceKind.SETTLEMENT for item in arrival.evidence) == 2
    assert {entry.reference_id for entry in arrival.state.ledger if entry.kind == "settlement"} == {
        "contract:order-1",
        "contract:order-2",
    }
    assert observe_world(arrival.state, "industrial").credits > seller_before.credits
    assert_credit_conserved(arrival.state, initial_credits)

    repeated = advance_world(arrival.state)
    assert repeated.state.station.parts == arrival.state.station.parts
    assert not any(item.kind is TradeEvidenceKind.SETTLEMENT for item in repeated.evidence)
    assert_credit_conserved(repeated.state, initial_credits)

    repair = repair_with_batch(arrival.state, "industrial-batch-a")
    completed_repair = advance(repair.state, 2)
    assert repair.accepted
    assert completed_repair.station.leak_active is False
    assert completed_repair.installed_part.contract_id == "contract:order-1"
    assert completed_repair.installed_part.shipment_id == "shipment:order-1"
    assert_credit_conserved(completed_repair, initial_credits)


def test_unfunded_purchase_is_rejected_without_mutating_the_world():
    state = create_world_state(station_state(credits=0))

    rejected = purchase(state)

    assert not rejected.accepted
    assert rejected.rejection == "insufficient_credits"
    assert rejected.state is state
    assert rejected.evidence == ()
    assert not state.contracts
    assert not state.shipments


def test_legacy_station_stock_with_no_offer_price_cannot_be_sold():
    state = create_world_state(station_state(parts=1))

    rejected = purchase_parts(
        state,
        command_id="legacy-sale",
        quantity=1,
        batch_id="station-stock",
        buyer_id="industrial",
        seller_id="station",
    )

    assert not rejected.accepted
    assert rejected.rejection == "invalid_offer_price"
    assert rejected.state is state


def test_repair_without_an_active_leak_leaves_available_parts_untouched():
    state = create_world_state(station_state(parts=1))

    rejected = repair_with_batch(state, "station-stock")

    assert not rejected.accepted
    assert rejected.rejection == "repair_not_needed"
    assert rejected.state is state
    assert state.station.parts == 1
    assert state.pending_repair_batch_id is None
    assert state.installed_part is None


def test_defective_part_is_traced_quarantined_and_replaced_after_delayed_notice():
    state = create_world_state(
        station_state(leak_active=True, repair_notice_delay_turns=6),
        defect_after_turns=2,
    )
    initial_credits = state.station.credits + sum(world.credits for world in state.inventories)
    ordered = purchase(state, quantity=2)
    arrived = advance(ordered.state, 3)
    assigned = repair_with_batch(arrived, "industrial-batch-a")

    assert ordered.accepted
    assert assigned.accepted
    assert assigned.state.installed_part is None
    assert assigned.state.pending_repair_batch_id == "industrial-batch-a"

    first_work = advance_world(assigned.state)
    completed = advance_world(first_work.state)
    assert completed.state.station.repairs_completed == 1
    assert completed.state.station.repair_turns_remaining == 0
    assert completed.state.installed_part.batch_id == "industrial-batch-a"
    assert completed.state.installed_part.operating_turns == 0
    assert not completed.state.station.leak_active
    assert any(item.kind is TradeEvidenceKind.REPAIR_COMPLETE for item in completed.evidence)
    assert not any(
        item.kind is TradeEvidenceKind.STATION_EVIDENCE
        and "maintenance reports" in item.message.lower()
        for item in observe_world(completed.state, "station").evidence
    )

    first_operation = advance_world(completed.state)
    assert not first_operation.state.station.leak_active
    assert first_operation.state.installed_part.operating_turns == 1
    failure = advance_world(first_operation.state)
    assert failure.state.station.leak_active
    assert failure.state.installed_part.operating_turns == 2

    after_notice = advance(failure.state, 4)
    assert any(
        item.kind is TradeEvidenceKind.STATION_EVIDENCE
        and "maintenance reports" in item.message.lower()
        for item in observe_world(after_notice, "station").evidence
    )
    assert sum(item.kind is TradeEvidenceKind.FAILURE for item in after_notice.evidence) == 1

    inspected = inspect_installed_batch(after_notice)
    finding = inspected.evidence[0]
    assert inspected.accepted
    assert finding.code == "material_defect_confirmed"
    assert finding.batch_id == "industrial-batch-a"
    assert finding.contract_id == "contract:order-1"
    assert finding.shipment_id == "shipment:order-1"

    quarantined = quarantine_batch(inspected.state, "industrial-batch-a")
    assert quarantined.accepted
    assert quarantined.state.station.parts == 0
    assert quarantined.evidence[0].batch_id == "industrial-batch-a"

    replacement = purchase(
        quarantined.state,
        command_id="order-2",
        batch_id="industrial-batch-b",
    )
    assert replacement.accepted
    arrived_good = advance(replacement.state, 3)
    good_repair = repair_with_batch(arrived_good, "industrial-batch-b")
    assert good_repair.accepted
    completed_good_repair = advance(good_repair.state, 2)
    assert completed_good_repair.station.leak_active is False
    assert completed_good_repair.installed_part.batch_id == "industrial-batch-b"
    assert completed_good_repair.installed_part.contract_id == "contract:order-2"
    assert completed_good_repair.installed_part.shipment_id == "shipment:order-2"

    operated = advance(completed_good_repair, 6)
    healthy_inspection = inspect_installed_batch(operated)
    assert operated.station.leak_active is False
    assert sum(item.kind is TradeEvidenceKind.FAILURE for item in operated.evidence) == 1
    assert healthy_inspection.evidence[0].code == "installed_batch_traced"
    assert "no material defect observed" in healthy_inspection.evidence[0].message.lower()
    assert_credit_conserved(operated, initial_credits)
