"""Cargo quality is discovered, sampled, and accounted for at the station."""

from dataclasses import replace

from station_control.quality import assay_batch, consume_feedstock
from station_control.scenarios import create_world
from station_control.trade import (
    PartLot,
    TradeEvidenceKind,
    advance_world,
    create_world_state,
    observe_world,
    purchase_lot,
)


def delivered_feedstock(batch_id, *, quantity, yield_percent):
    station = replace(create_world("normal", seed=0), scheduled_events=())
    state = create_world_state(
        station,
        industrial_parts=0,
        reliable_parts=0,
        travel_turns=2,
    )
    seller = state.inventories[0]
    lot = PartLot(
        batch_id,
        quantity,
        5,
        "industrial",
        resource="oxygen_feedstock",
        unit="canisters",
        yield_percent=yield_percent,
    )
    state = replace(state, inventories=(replace(seller, lots=(lot,)),))
    public_offer = observe_world(state, "station")
    purchased = purchase_lot(
        state,
        command_id=f"order-{batch_id}",
        batch_id=batch_id,
        quantity=quantity,
    )
    arrived = purchased.state
    for _ in range(3):
        arrived = advance_world(arrived).state
    return public_offer, purchased, arrived


def ledger_since(state, previous):
    return state.ledger[len(previous.ledger) :]


def test_poor_cargo_assay_and_consumption_account_for_sample_yield_loss_and_overflow():
    offer, purchase, delivered = delivered_feedstock(
        "poor-oxygen",
        quantity=3,
        yield_percent=50,
    )
    assert any(lot.batch_id == "poor-oxygen" for lot in offer.offers)
    assert all(not hasattr(lot, "yield_percent") for lot in offer.offers)
    station_view = observe_world(delivered, "station")
    assert all(not hasattr(lot, "yield_percent") for lot in station_view.local_lots)
    assert all(not hasattr(shipment, "yield_percent") for shipment in station_view.shipments)

    state = replace(delivered, station=replace(delivered.station, oxygen=950))
    assay = assay_batch(state, "poor-oxygen")
    assert assay.accepted
    assert (
        next(lot for lot in assay.state.station_lots if lot.batch_id == "poor-oxygen").quantity == 2
    )
    assert assay.state.station.available_crew == state.station.available_crew - 1
    assert assay.state.station.oxygen == state.station.oxygen
    assert assay.evidence[0].kind is TradeEvidenceKind.ASSAY
    assert assay.evidence[0].finding_code == "cargo_quality_measured"
    assert assay.evidence[0].measured_value == 50
    assert assay.evidence[0].contract_id == purchase.contract_id
    assert assay.evidence[0].shipment_id == purchase.shipment_id

    consumed = consume_feedstock(assay.state, "poor-oxygen", quantity=2)
    assert consumed.accepted
    assert (
        next(lot for lot in consumed.state.station_lots if lot.batch_id == "poor-oxygen").quantity
        == 0
    )
    assert consumed.state.station.oxygen == 1000
    assert consumed.state.station.available_crew == state.station.available_crew - 2
    assert consumed.evidence[0].kind is TradeEvidenceKind.CONSUMPTION
    assert consumed.evidence[0].measured_value == 100
    assert consumed.evidence[0].measured_unit == "oxygen_units"

    entries = ledger_since(consumed.state, state)
    assert sum(entry.delta for entry in entries if entry.account == "sample:poor-oxygen") == 1
    assert sum(entry.delta for entry in entries if entry.account == "oxygen:station") == 50
    assert sum(entry.delta for entry in entries if entry.account == "yield_loss:poor-oxygen") == 100
    assert sum(entry.delta for entry in entries if entry.account == "overflow:station") == 50
    assert sum(entry.delta for entry in entries if entry.resource == "oxygen") == 200
    assert {entry.reference_id for entry in entries} == {purchase.contract_id}


def test_clean_cargo_assay_and_consumption_produce_full_yield_without_losses():
    _, purchase, delivered = delivered_feedstock(
        "clean-oxygen",
        quantity=2,
        yield_percent=100,
    )
    assayed = assay_batch(delivered, "clean-oxygen")
    state = assayed.state
    consumed = consume_feedstock(state, "clean-oxygen")

    assert assayed.accepted and consumed.accepted
    assert assayed.evidence[0].measured_value == 100
    assert consumed.state.station.oxygen == state.station.oxygen + 100
    assert (
        next(lot for lot in consumed.state.station_lots if lot.batch_id == "clean-oxygen").quantity
        == 0
    )
    assert consumed.state.station.available_crew == delivered.station.available_crew - 2
    entries = ledger_since(consumed.state, delivered)
    assert sum(entry.delta for entry in entries if entry.account == "sample:clean-oxygen") == 1
    assert sum(entry.delta for entry in entries if entry.account == "oxygen:station") == 100
    assert sum(entry.delta for entry in entries if entry.account.startswith("yield_loss:")) == 0
    assert sum(entry.delta for entry in entries if entry.account == "overflow:station") == 0
    assert {entry.reference_id for entry in entries} == {purchase.contract_id}
