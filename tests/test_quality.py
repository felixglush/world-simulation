"""Behavioral contract for private cargo assays and feedstock conversion."""

from dataclasses import replace

import pytest

from station_control.quality import assay_batch, consume_feedstock
from station_control.scenarios import ScenarioFamily, create_world
from station_control.trade import (
    PartLot,
    TradeEvidenceKind,
    advance_world,
    create_world_state,
    observe_world,
    purchase_lot,
    quarantine_batch,
)


def station_state(**changes):
    return replace(create_world(ScenarioFamily.NORMAL, seed=0), scheduled_events=(), **changes)


def feedstock(
    batch_id="oxygen-batch",
    *,
    quantity=1,
    yield_percent=100,
    contract_id=None,
    shipment_id=None,
    unit="canisters",
    resource="oxygen_feedstock",
):
    return PartLot(
        batch_id=batch_id,
        quantity=quantity,
        unit_price=1,
        origin_world="industrial",
        contract_id=contract_id,
        shipment_id=shipment_id,
        resource=resource,
        unit=unit,
        yield_percent=yield_percent,
    )


def state_with_lots(*lots, oxygen=700, oxygen_capacity=1000, available_crew=4, crew_alive=True):
    station = replace(
        station_state(),
        oxygen=oxygen,
        oxygen_capacity=oxygen_capacity,
        available_crew=available_crew,
        crew_alive=crew_alive,
        parts=sum(lot.quantity for lot in lots if lot.resource == "parts"),
    )
    return replace(create_world_state(station), station_lots=tuple(lots))


def ledger_since(state, previous):
    return state.ledger[len(previous.ledger) :]


def test_assay_consumes_one_private_sample_spends_one_crew_and_emits_scoped_measurement():
    lot = feedstock(
        quantity=2,
        yield_percent=50,
        contract_id="contract:oxygen-1",
        shipment_id="shipment:oxygen-1",
    )
    state = state_with_lots(lot)

    result = assay_batch(state, lot.batch_id)

    assert result.accepted
    assert result.state.station_lots[0].quantity == 1
    assert result.state.station.available_crew == state.station.available_crew - 1
    assert result.state.station.oxygen == state.station.oxygen
    assert result.evidence == result.state.evidence[-1:]
    evidence = result.evidence[0]
    assert evidence.kind is TradeEvidenceKind.ASSAY
    assert evidence.finding_code == "cargo_quality_measured"
    assert evidence.measured_value == 50
    assert evidence.measured_unit == "percent"
    assert "50%" in evidence.message
    assert evidence.batch_id == lot.batch_id
    assert evidence.contract_id == lot.contract_id
    assert evidence.shipment_id == lot.shipment_id
    entries = ledger_since(result.state, state)
    assert [(entry.account, entry.resource, entry.delta) for entry in entries] == [
        ("inventory:station:oxygen-batch:shipment:oxygen-1", "oxygen_feedstock", -1),
        ("sample:oxygen-batch", "oxygen_feedstock", 1),
    ]
    assert all(entry.reference_id == lot.contract_id for entry in entries)


@pytest.mark.parametrize(
    ("operation", "lots", "batch_id", "station_changes", "expected_rejection"),
    [
        ("assay", (), "missing", {}, "batch_not_in_station_inventory"),
        ("assay", (feedstock(quantity=0),), "oxygen-batch", {}, "insufficient_stock"),
        (
            "assay",
            (feedstock(resource="parts", unit="parts"),),
            "oxygen-batch",
            {},
            "not_feedstock",
        ),
        ("assay", (feedstock(),), "", {}, "invalid_batch_id"),
        ("assay", (feedstock(),), "oxygen-batch", {"available_crew": 0}, "crew_unavailable"),
        ("assay", (feedstock(),), "oxygen-batch", {"crew_alive": False}, "crew_lost"),
        ("consume", (), "missing", {}, "batch_not_in_station_inventory"),
        (
            "consume",
            (feedstock(quantity=0),),
            "oxygen-batch",
            {},
            "insufficient_stock",
        ),
        (
            "consume",
            (feedstock(resource="parts", unit="parts"),),
            "oxygen-batch",
            {},
            "not_feedstock",
        ),
        (
            "consume",
            (feedstock(unit="bottles"),),
            "oxygen-batch",
            {},
            "feedstock_unit_mismatch",
        ),
        (
            "assay",
            (feedstock(yield_percent=101),),
            "oxygen-batch",
            {},
            "invalid_feedstock_quality",
        ),
        (
            "consume",
            (feedstock(yield_percent=101),),
            "oxygen-batch",
            {},
            "invalid_feedstock_quality",
        ),
        ("consume", (feedstock(),), "", {}, "invalid_batch_id"),
        ("consume", (feedstock(),), "oxygen-batch", {"crew_alive": False}, "crew_lost"),
        (
            "consume",
            (feedstock(),),
            "oxygen-batch",
            {"available_crew": 0},
            "crew_unavailable",
        ),
    ],
)
def test_invalid_or_unsafe_operations_reject_without_changing_state(
    operation, lots, batch_id, station_changes, expected_rejection
):
    station = replace(station_state(), **station_changes)
    state = replace(
        create_world_state(station),
        station_lots=lots,
        station=replace(
            station,
            parts=sum(lot.quantity for lot in lots if lot.resource == "parts"),
        ),
    )

    if operation == "assay":
        result = assay_batch(state, batch_id)
    else:
        result = consume_feedstock(state, batch_id)

    assert not result.accepted
    assert result.rejection == expected_rejection
    assert result.state is state


@pytest.mark.parametrize("quantity", [0, 4, True, 1.5])
def test_consumption_rejects_quantities_outside_one_to_three_without_mutation(quantity):
    state = state_with_lots(feedstock(quantity=4))

    result = consume_feedstock(state, "oxygen-batch", quantity=quantity)

    assert not result.accepted
    assert result.rejection == "invalid_quantity"
    assert result.state is state


def test_clean_feedstock_produces_one_hundred_oxygen_per_canister():
    clean = feedstock(
        "clean",
        yield_percent=100,
        contract_id="contract:clean",
        shipment_id="shipment:clean",
    )
    state = state_with_lots(clean)

    result = consume_feedstock(state, "clean")

    assert result.accepted
    assert result.state.station.oxygen == state.station.oxygen + 100
    assert result.state.station.available_crew == state.station.available_crew - 1
    rows = ledger_since(result.state, state)
    assert sum(entry.delta for entry in rows if entry.account == "oxygen:station") == 100
    assert sum(entry.delta for entry in rows if entry.account.startswith("yield_loss:")) == 0
    assert sum(entry.delta for entry in rows if entry.account == "overflow:station") == 0


def test_poor_yield_accounts_for_unusable_oxygen_and_capacity_overflow():
    poor = feedstock(
        "mixed-quality",
        quantity=3,
        yield_percent=50,
        contract_id="contract:poor",
        shipment_id="shipment:poor",
    )
    state = state_with_lots(poor, oxygen=950, oxygen_capacity=1000)

    result = consume_feedstock(state, "mixed-quality", quantity=3)

    assert result.accepted
    assert result.state.station_lots[0].quantity == 0
    assert result.state.station.oxygen == 1000
    assert result.state.station.available_crew == state.station.available_crew - 1
    assert len(result.evidence) == 1
    assert "50%" in result.evidence[0].message
    assert result.evidence[0].measured_value == 150
    assert result.evidence[0].measured_unit == "oxygen_units"
    new_entries = ledger_since(result.state, state)
    assert sum(e.delta for e in new_entries if e.account == "oxygen:station") == 50
    assert sum(e.delta for e in new_entries if e.account.startswith("yield_loss:")) == 150
    assert sum(e.delta for e in new_entries if e.account == "overflow:station") == 100
    assert sum(e.delta for e in new_entries if e.resource == "oxygen") == 300
    assert (
        sum(-e.delta for e in new_entries if e.resource == "oxygen_feedstock" and e.delta < 0) == 3
    )
    assert {e.reference_id for e in new_entries} == {"contract:poor"}


def test_consumption_uses_duplicate_batch_lots_in_fifo_provenance_order():
    first = feedstock(
        quantity=1,
        yield_percent=50,
        contract_id="contract:first",
        shipment_id="shipment:first",
    )
    second = feedstock(
        quantity=2,
        yield_percent=50,
        contract_id="contract:second",
        shipment_id="shipment:second",
    )
    state = state_with_lots(first, second)

    result = consume_feedstock(state, "oxygen-batch", quantity=2)

    assert result.accepted
    assert [lot.quantity for lot in result.state.station_lots] == [0, 1]
    assert result.state.station.oxygen == state.station.oxygen + 100
    assert result.state.station.available_crew == state.station.available_crew - 1
    assert [event.shipment_id for event in result.evidence] == [
        "shipment:first",
        "shipment:second",
    ]
    assert [event.contract_id for event in result.evidence] == [
        "contract:first",
        "contract:second",
    ]
    assert all(event.kind is TradeEvidenceKind.CONSUMPTION for event in result.evidence)
    assert all(event.finding_code == "feedstock_yield_observed" for event in result.evidence)
    assert [event.measured_value for event in result.evidence] == [50, 50]
    assert all(event.measured_unit == "oxygen_units" for event in result.evidence)
    assert "50%" in result.evidence[0].message
    assert "50%" in result.evidence[1].message
    new_entries = ledger_since(result.state, state)
    assert sum(e.delta for e in new_entries if e.account == "oxygen:station") == 100
    assert sum(e.delta for e in new_entries if e.account.startswith("yield_loss:")) == 100
    assert sum(e.delta for e in new_entries if e.account == "overflow:station") == 0
    assert sum(e.delta for e in new_entries if e.resource == "oxygen") == 200
    assert {e.reference_id for e in new_entries} == {
        "contract:first",
        "contract:second",
    }

    repeated = consume_feedstock(result.state, "oxygen-batch", quantity=1)
    assert repeated.accepted
    assert [lot.quantity for lot in repeated.state.station_lots] == [0, 0]
    assert repeated.evidence[0].shipment_id == "shipment:second"


def test_named_shipment_scopes_assay_and_consumption_for_same_batch_quality_lots():
    first = feedstock(
        quantity=2,
        yield_percent=100,
        contract_id="contract:first",
        shipment_id="shipment:first",
    )
    second = feedstock(
        quantity=2,
        yield_percent=0,
        contract_id="contract:second",
        shipment_id="shipment:second",
    )
    state = state_with_lots(first, second)

    assayed_first = assay_batch(state, "oxygen-batch", shipment_id="shipment:first")

    assert assayed_first.accepted
    assert [lot.quantity for lot in assayed_first.state.station_lots] == [1, 2]
    assert assayed_first.evidence[0].shipment_id == "shipment:first"
    assert assayed_first.evidence[0].contract_id == "contract:first"
    assert assayed_first.evidence[0].measured_value == 100
    assert assayed_first.evidence[0].measured_unit == "percent"

    consumed_first = consume_feedstock(
        assayed_first.state,
        "oxygen-batch",
        quantity=1,
        shipment_id="shipment:first",
    )

    assert consumed_first.accepted
    assert [lot.quantity for lot in consumed_first.state.station_lots] == [0, 2]
    assert consumed_first.state.station.oxygen == state.station.oxygen + 100
    assert consumed_first.evidence[0].shipment_id == "shipment:first"
    assert consumed_first.evidence[0].contract_id == "contract:first"
    assert consumed_first.evidence[0].measured_value == 100
    assert consumed_first.evidence[0].measured_unit == "oxygen_units"

    assayed_second = assay_batch(
        consumed_first.state,
        "oxygen-batch",
        shipment_id="shipment:second",
    )

    assert assayed_second.accepted
    assert [lot.quantity for lot in assayed_second.state.station_lots] == [0, 1]
    assert assayed_second.evidence[0].shipment_id == "shipment:second"
    assert assayed_second.evidence[0].contract_id == "contract:second"
    assert assayed_second.evidence[0].measured_value == 0
    assert assayed_second.evidence[0].measured_unit == "percent"


@pytest.mark.parametrize("operation", ["assay", "consume"])
def test_named_unknown_shipment_rejects_without_sampling_or_consuming_another_delivery(operation):
    first = feedstock(
        quantity=2,
        yield_percent=100,
        contract_id="contract:first",
        shipment_id="shipment:first",
    )
    second = feedstock(
        quantity=2,
        yield_percent=0,
        contract_id="contract:second",
        shipment_id="shipment:second",
    )
    state = state_with_lots(first, second)

    if operation == "assay":
        result = assay_batch(state, "oxygen-batch", shipment_id="shipment:missing")
    else:
        result = consume_feedstock(
            state,
            "oxygen-batch",
            quantity=1,
            shipment_id="shipment:missing",
        )

    assert not result.accepted
    assert result.rejection
    assert result.state is state


@pytest.mark.parametrize(("operation", "shipment_id"), [("assay", ""), ("consume", 42)])
def test_named_shipment_id_must_be_a_nonempty_string_without_state_changes(operation, shipment_id):
    state = state_with_lots(feedstock(quantity=2))

    if operation == "assay":
        result = assay_batch(state, "oxygen-batch", shipment_id=shipment_id)
    else:
        result = consume_feedstock(
            state,
            "oxygen-batch",
            quantity=1,
            shipment_id=shipment_id,
        )

    assert not result.accepted
    assert result.rejection == "invalid_shipment_id"
    assert result.state is state


def test_assay_sample_and_quarantine_remove_distinct_feedstock_stock():
    lot = feedstock(
        quantity=2,
        yield_percent=50,
        contract_id="contract:oxygen",
        shipment_id="shipment:oxygen",
    )
    state = state_with_lots(lot)

    assayed = assay_batch(state, lot.batch_id)
    quarantined = quarantine_batch(assayed.state, lot.batch_id, quantity=1)
    after_quarantine = consume_feedstock(quarantined.state, lot.batch_id)

    assert assayed.accepted and assayed.state.station_lots[0].quantity == 1
    assert quarantined.accepted and quarantined.state.station_lots[0].quantity == 0
    assert after_quarantine.rejection == "insufficient_stock"
    assert after_quarantine.state is quarantined.state
    quarantine_rows = ledger_since(quarantined.state, assayed.state)
    assert {entry.resource for entry in quarantine_rows} == {"oxygen_feedstock"}
    assert all(entry.reference_id == lot.contract_id for entry in quarantine_rows)


def test_delivered_quality_stays_private_in_public_lot_and_shipment_views():
    state = create_world_state(station_state())
    inventory = state.inventories[0]
    hidden_lot = feedstock(
        "private-quality",
        quantity=1,
        yield_percent=50,
    )
    state = replace(
        state,
        inventories=(replace(inventory, lots=inventory.lots + (hidden_lot,)),),
    )

    offered = observe_world(state, "industrial")
    purchased = purchase_lot(
        state,
        command_id="quality-delivery",
        quantity=1,
        batch_id="private-quality",
    )
    delivered = purchased.state
    for _ in range(3):
        delivered = advance_world(delivered).state
    received_lot = next(lot for lot in delivered.station_lots if lot.batch_id == "private-quality")
    public_station = observe_world(delivered, "station")

    assert purchased.accepted
    assert received_lot.yield_percent == 50
    assert all(not hasattr(lot, "yield_percent") for lot in offered.offers)
    assert all(not hasattr(lot, "yield_percent") for lot in public_station.local_lots)
    assert all(not hasattr(shipment, "yield_percent") for shipment in public_station.shipments)
