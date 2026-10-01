"""Station-only cargo quality operations."""

from __future__ import annotations

from dataclasses import replace

from .trade import (
    PartLot,
    TradeEvidenceKind,
    TradeResult,
    WorldState,
    _append_ledger,
    _emit_evidence,
    _rejected,
    _set_lots,
)

_FEEDSTOCK = "oxygen_feedstock"
_FEEDSTOCK_UNIT = "canisters"


def assay_batch(state: WorldState, batch_id: str) -> TradeResult:
    """Consume one station sample and publish its measured feedstock yield."""
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(batch_id, str) or not batch_id:
        return _rejected(state, "invalid_batch_id")
    if state.station.available_crew < 1:
        return _rejected(state, "crew_unavailable")

    matching = tuple(lot for lot in state.station_lots if lot.batch_id == batch_id)
    if not matching:
        return _rejected(state, "batch_not_in_station_inventory")
    available = tuple(lot for lot in matching if lot.quantity > 0)
    if not available:
        return _rejected(state, "insufficient_stock")
    rejection = _feedstock_rejection(available)
    if rejection is not None:
        return _rejected(state, rejection)

    lot = available[0]
    if not _valid_yield(lot):
        return _rejected(state, "invalid_feedstock_quality")

    index = next(index for index, candidate in enumerate(state.station_lots) if candidate is lot)
    changed_lots = list(state.station_lots)
    changed_lots[index] = replace(lot, quantity=lot.quantity - 1)
    updated = _set_lots(state, "station", tuple(changed_lots))
    updated = replace(
        updated,
        station=replace(
            updated.station,
            available_crew=updated.station.available_crew - 1,
        ),
    )
    reference = lot.contract_id or lot.batch_id
    updated = _append_ledger(
        updated,
        (
            (
                _inventory_account(lot),
                _FEEDSTOCK,
                -1,
                TradeEvidenceKind.ASSAY.value,
                reference,
            ),
            (
                f"sample:{lot.batch_id}",
                _FEEDSTOCK,
                1,
                TradeEvidenceKind.ASSAY.value,
                reference,
            ),
        ),
    )
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.ASSAY,
        f"Assay measured oxygen-feedstock yield at {lot.yield_percent}% for batch {lot.batch_id}.",
        "station",
        batch_id=lot.batch_id,
        contract_id=lot.contract_id,
        shipment_id=lot.shipment_id,
        finding_code="cargo_quality_measured",
        measured_value=lot.yield_percent,
        measured_unit="percent",
    )
    return TradeResult(updated, True, evidence=evidence)


def consume_feedstock(state: WorldState, batch_id: str, *, quantity: int = 1) -> TradeResult:
    """Convert FIFO station feedstock into oxygen, preserving quality provenance."""
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(batch_id, str) or not batch_id:
        return _rejected(state, "invalid_batch_id")
    if type(quantity) is not int or not 1 <= quantity <= 3:
        return _rejected(state, "invalid_quantity")
    if state.station.available_crew < 1:
        return _rejected(state, "crew_unavailable")

    matching = tuple(lot for lot in state.station_lots if lot.batch_id == batch_id)
    if not matching:
        return _rejected(state, "batch_not_in_station_inventory")
    available = tuple(lot for lot in matching if lot.quantity > 0)
    if not available:
        return _rejected(state, "insufficient_stock")
    rejection = _feedstock_rejection(available)
    if rejection is not None:
        return _rejected(state, rejection)
    if sum(lot.quantity for lot in available) < quantity:
        return _rejected(state, "insufficient_stock")
    if any(not _valid_yield(lot) for lot in available):
        return _rejected(state, "invalid_feedstock_quality")

    remaining = quantity
    room = max(0, state.station.oxygen_capacity - state.station.oxygen)
    added_oxygen = 0
    changed_lots = list(state.station_lots)
    ledger_rows: list[tuple[str, str, int, str, str]] = []
    emitted = []
    evidence_state = state
    for index, lot in enumerate(state.station_lots):
        if lot.batch_id != batch_id or lot.quantity <= 0 or remaining <= 0:
            continue
        consumed = min(lot.quantity, remaining)
        potential = consumed * 100
        produced = potential * lot.yield_percent // 100
        unusable = potential - produced
        delivered = min(produced, room)
        overflow = produced - delivered
        room -= delivered
        added_oxygen += delivered
        changed_lots[index] = replace(lot, quantity=lot.quantity - consumed)
        remaining -= consumed

        reference = lot.contract_id or lot.batch_id
        ledger_rows.extend(
            (
                (_inventory_account(lot), _FEEDSTOCK, -consumed, "consumption", reference),
                (f"consumed:{_FEEDSTOCK}", _FEEDSTOCK, consumed, "consumption", reference),
                ("oxygen:station", "oxygen", delivered, "consumption", reference),
                (f"yield_loss:{lot.batch_id}", "oxygen", unusable, "consumption", reference),
                ("overflow:station", "oxygen", overflow, "consumption", reference),
            )
        )
        evidence_state, evidence = _emit_evidence(
            evidence_state,
            TradeEvidenceKind.CONSUMPTION,
            f"Consumed {consumed} oxygen-feedstock canister(s) from batch {lot.batch_id} at "
            f"{lot.yield_percent}% yield: {produced} oxygen units produced, {unusable} unusable, "
            f"{delivered} added to station reserves, {overflow} overflowed.",
            "station",
            batch_id=lot.batch_id,
            contract_id=lot.contract_id,
            shipment_id=lot.shipment_id,
            finding_code="feedstock_yield_observed",
            measured_value=produced,
            measured_unit="oxygen_units",
        )
        emitted.extend(evidence)

    updated = _set_lots(evidence_state, "station", tuple(changed_lots))
    updated = replace(
        updated,
        station=replace(
            updated.station,
            oxygen=updated.station.oxygen + added_oxygen,
            available_crew=updated.station.available_crew - 1,
        ),
    )
    updated = _append_ledger(updated, tuple(ledger_rows))
    return TradeResult(updated, True, evidence=tuple(emitted))


def _feedstock_rejection(lots: tuple[PartLot, ...]) -> str | None:
    if any(lot.resource != _FEEDSTOCK for lot in lots):
        return "not_feedstock"
    if any(lot.unit != _FEEDSTOCK_UNIT for lot in lots):
        return "feedstock_unit_mismatch"
    return None


def _valid_yield(lot: PartLot) -> bool:
    return type(lot.yield_percent) is int and 0 <= lot.yield_percent <= 100


def _inventory_account(lot: PartLot) -> str:
    return f"inventory:station:{lot.batch_id}:{lot.shipment_id or 'local'}"
