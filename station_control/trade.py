"""In-memory trade domain for the station and neighboring industrial world."""

from __future__ import annotations

import re
from dataclasses import replace

from .domain import (
    Action,
    ActionKind,
    StationState,
    advance_turn,
    apply_action,
)
from .scenarios import ScenarioFamily, create_world
from .trade_types import (
    InstalledPart as InstalledPart,
)
from .trade_types import (
    LedgerEntry as LedgerEntry,
)
from .trade_types import (
    PartLot as PartLot,
)
from .trade_types import (
    PublicContract as PublicContract,
)
from .trade_types import (
    PublicLot as PublicLot,
)
from .trade_types import (
    PublicShipment as PublicShipment,
)
from .trade_types import (
    PublicWorldView as PublicWorldView,
)
from .trade_types import (
    Shipment as Shipment,
)
from .trade_types import (
    TradeContract as TradeContract,
)
from .trade_types import (
    TradeEvidence as TradeEvidence,
)
from .trade_types import (
    TradeEvidenceKind as TradeEvidenceKind,
)
from .trade_types import (
    TradeReport as TradeReport,
)
from .trade_types import (
    TradeResult as TradeResult,
)
from .trade_types import (
    WorldAdvanceResult as WorldAdvanceResult,
)
from .trade_types import (
    WorldInventory as WorldInventory,
)
from .trade_types import (
    WorldState as WorldState,
)
from .trade_types import (
    record_evidence as _emit_evidence,
)
from .trade_types import (
    reject_trade as _rejected,
)


def create_world_state(
    station: StationState | None = None,
    *,
    industrial_parts: int = 2,
    reliable_parts: int = 2,
    industrial_credits: int = 1000,
    bad_unit_price: int = 15,
    reliable_unit_price: int = 30,
    travel_turns: int = 2,
    shipment_capacity: int = 3,
    defect_after_turns: int = 3,
) -> WorldState:
    _validate_nonnegative_integer("industrial_parts", industrial_parts, maximum=1000)
    _validate_nonnegative_integer("reliable_parts", reliable_parts, maximum=1000)
    _validate_nonnegative_integer("industrial_credits", industrial_credits, maximum=1_000_000_000)
    _validate_bounded_positive("bad_unit_price", bad_unit_price, maximum=1_000_000)
    _validate_bounded_positive("reliable_unit_price", reliable_unit_price, maximum=1_000_000)
    if station is None:
        station = create_world(ScenarioFamily.NORMAL, seed=0)
    if not isinstance(station, StationState):
        raise ValueError("station must be a StationState")
    _validate_nonnegative_integer("station.parts", station.parts, maximum=1000)
    _validate_nonnegative_integer("station.credits", station.credits, maximum=1_000_000_000)
    if type(travel_turns) is not int or not 1 <= travel_turns <= 10:
        raise ValueError("travel_turns must be an integer from 1 to 10")
    if type(shipment_capacity) is not int or not 1 <= shipment_capacity <= 100:
        raise ValueError("shipment_capacity must be an integer from 1 to 100")
    if type(defect_after_turns) is not int or not 1 <= defect_after_turns <= 100:
        raise ValueError("defect_after_turns must be an integer from 1 to 100")
    if station.parts:
        station_lots = (PartLot("station-stock", station.parts, 0, "station"),)
    else:
        station_lots = ()
    return WorldState(
        station=station,
        inventories=(
            WorldInventory(
                "industrial",
                industrial_credits,
                (
                    PartLot(
                        "industrial-batch-a",
                        industrial_parts,
                        bad_unit_price,
                        "industrial",
                        True,
                        defect_after_turns,
                    ),
                    PartLot(
                        "industrial-batch-b",
                        reliable_parts,
                        reliable_unit_price,
                        "industrial",
                    ),
                ),
            ),
        ),
        station_lots=station_lots,
        travel_turns=travel_turns,
        shipment_capacity=shipment_capacity,
        defect_after_turns=defect_after_turns,
    )


def purchase_lot(
    state: WorldState,
    *,
    command_id: str,
    quantity: int,
    batch_id: str = "industrial-batch-a",
    buyer_id: str = "station",
    seller_id: str = "industrial",
) -> TradeResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(command_id, str) or not _COMMAND_ID.fullmatch(command_id):
        return _rejected(state, "invalid_command_id")
    if (
        not isinstance(batch_id, str)
        or not batch_id
        or not isinstance(buyer_id, str)
        or not isinstance(seller_id, str)
    ):
        return _rejected(state, "invalid_purchase")
    if type(quantity) is not int or not 1 <= quantity <= MAX_TRADE_QUANTITY:
        return _rejected(state, "invalid_quantity")

    previous = next((item for item in state.contracts if item.command_id == command_id), None)
    if previous is not None:
        shipment = next(
            item for item in state.shipments if item.contract_id == previous.contract_id
        )
        if (
            previous.buyer_id == buyer_id
            and previous.seller_id == seller_id
            and previous.batch_id == batch_id
            and previous.quantity == quantity
        ):
            return TradeResult(
                state,
                True,
                contract_id=previous.contract_id,
                shipment_id=shipment.shipment_id,
                duplicate=True,
            )
        return _rejected(state, "command_id_conflict")

    if buyer_id == seller_id:
        return _rejected(state, "self_trade")
    if not _world_exists(state, buyer_id) or not _world_exists(state, seller_id):
        return _rejected(state, "unknown_world")
    if quantity > state.shipment_capacity:
        return _rejected(state, "shipment_capacity_exceeded")
    seller_lots = _lots(state, seller_id)
    selected = _available_lot_index(state, seller_id, batch_id)
    if selected is None:
        rejection = (
            "insufficient_stock"
            if any(lot.batch_id == batch_id for lot in seller_lots)
            else "unknown_batch"
        )
        return _rejected(state, rejection)
    lot = seller_lots[selected]
    if (
        not isinstance(lot.failure_load, str)
        or lot.failure_load not in {"any", "routine", "peak", "backup"}
        or type(lot.yield_percent) is not int
        or not 0 <= lot.yield_percent <= 100
    ):
        return _rejected(state, "invalid_lot_condition")
    if any(
        existing.resource == lot.resource and existing.unit != lot.unit
        for existing in _lots(state, buyer_id)
    ) or any(
        shipment.destination == buyer_id
        and shipment.status in {"booked", "in_transit"}
        and _contract(state, shipment.contract_id).resource == lot.resource
        and (
            shipment.cargo.unit
            if shipment.cargo is not None
            else _contract(state, shipment.contract_id).unit
        )
        != lot.unit
        for shipment in state.shipments
    ):
        return _rejected(state, "unit_mismatch")
    if _offer_quantity(state, seller_id, batch_id) < quantity:
        return _rejected(state, "insufficient_stock")
    capacity = _resource_capacity(state, buyer_id, lot.resource)
    if capacity is not None and (
        _stock_quantity(state, buyer_id, lot.resource)
        + incoming_quantity(state, buyer_id, lot.resource)
        + quantity
        > capacity
    ):
        return _rejected(state, "storage_capacity")
    if type(lot.unit_price) is not int or not 1 <= lot.unit_price <= MAX_UNIT_PRICE:
        return _rejected(state, "invalid_offer_price")
    total_price = lot.unit_price * quantity
    if _credits(state, buyer_id) < total_price:
        return _rejected(state, "insufficient_credits")

    contract_id = f"contract:{command_id}"
    shipment_id = f"shipment:{command_id}"
    contract = TradeContract(
        contract_id,
        command_id,
        buyer_id,
        seller_id,
        batch_id,
        quantity,
        lot.unit_price,
        total_price,
        "booked",
        lot.resource,
        lot.unit,
    )
    shipment = Shipment(
        shipment_id,
        contract_id,
        seller_id,
        buyer_id,
        batch_id,
        quantity,
        "booked",
        cargo=replace(lot, quantity=quantity),
    )

    updated = _set_credits(state, buyer_id, _credits(state, buyer_id) - total_price)
    changed_lots = list(_lots(updated, seller_id))
    changed_lots[selected] = replace(lot, quantity=lot.quantity - quantity)
    updated = _set_lots(updated, seller_id, tuple(changed_lots))
    updated = replace(
        updated,
        contracts=updated.contracts + (contract,),
        shipments=updated.shipments + (shipment,),
        escrow_credits=updated.escrow_credits + total_price,
    )
    updated = _append_ledger(
        updated,
        (
            (f"credits:{buyer_id}", "credits", -total_price, "purchase", contract_id),
            (f"escrow:{contract_id}", "credits", total_price, "purchase", contract_id),
            (
                f"inventory:{seller_id}:{lot.resource}:{batch_id}",
                lot.resource,
                -quantity,
                "purchase",
                contract_id,
            ),
            (f"reserved:{shipment_id}", lot.resource, quantity, "purchase", contract_id),
        ),
    )
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.PURCHASE,
        f"Purchased {quantity} {lot.unit} of {lot.resource} from {seller_id}; "
        "funds are held pending delivery.",
        buyer_id,
        batch_id=batch_id,
        contract_id=contract_id,
        shipment_id=shipment_id,
    )
    return TradeResult(
        updated, True, evidence=evidence, contract_id=contract_id, shipment_id=shipment_id
    )


def purchase_parts(
    state: WorldState,
    *,
    command_id: str,
    quantity: int,
    batch_id: str = "industrial-batch-a",
    buyer_id: str = "station",
    seller_id: str = "industrial",
) -> TradeResult:
    """Compatibility entry point for replacement-part purchases."""
    result = purchase_lot(
        state,
        command_id=command_id,
        quantity=quantity,
        batch_id=batch_id,
        buyer_id=buyer_id,
        seller_id=seller_id,
    )
    if result.accepted:
        contract = next(item for item in result.state.contracts if item.command_id == command_id)
        if contract.resource != "parts":
            return _rejected(state, "not_a_part_lot")
    return result


def advance_world(state: WorldState) -> WorldAdvanceResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return WorldAdvanceResult(state)

    validate_storage_commitments(state)
    before = state
    station_turn = advance_turn(state.station)
    updated = replace(state, station=station_turn.state)
    emitted: list[TradeEvidence] = []

    untracked_delivery = station_turn.state.parts - sum(
        lot.quantity for lot in state.station_lots if lot.resource == "parts"
    )
    if untracked_delivery > 0:
        batch_id = f"station-delivery-{station_turn.state.turn}"
        updated = _set_lots(
            updated,
            "station",
            updated.station_lots + (PartLot(batch_id, untracked_delivery, 0, "station"),),
        )
        updated = _append_ledger(
            updated,
            (
                (
                    f"external:station-delivery:{station_turn.state.turn}",
                    "parts",
                    -untracked_delivery,
                    "legacy_delivery",
                    batch_id,
                ),
                (
                    f"inventory:station:{batch_id}",
                    "parts",
                    untracked_delivery,
                    "legacy_delivery",
                    batch_id,
                ),
            ),
        )

    repair_completed = station_turn.state.repairs_completed > before.station.repairs_completed
    if repair_completed:
        part = updated.pending_repair_part
        if updated.installed_part is not None:
            updated = _append_ledger(
                updated,
                (
                    (
                        "installed:oxygen_system",
                        "parts",
                        -1,
                        "replacement",
                        updated.installed_part.batch_id,
                    ),
                    (
                        f"scrap:{updated.installed_part.batch_id}",
                        "parts",
                        1,
                        "replacement",
                        updated.installed_part.batch_id,
                    ),
                ),
            )
        if part is None:
            updated = replace(updated, installed_part=None)
            batch_id = contract_id = shipment_id = None
        else:
            updated = replace(
                updated,
                installed_part=InstalledPart(
                    batch_id=part.batch_id,
                    origin_world=part.origin_world,
                    contract_id=part.contract_id,
                    shipment_id=part.shipment_id,
                    latent_defect=part.latent_defect,
                    defect_after_turns=part.defect_after_turns,
                    failure_load=part.failure_load,
                ),
                pending_repair_part=None,
            )
            batch_id = part.batch_id
            contract_id = part.contract_id
            shipment_id = part.shipment_id
            updated = _append_ledger(
                updated,
                (
                    ("repair_workshop:oxygen_system", "parts", -1, "repair_complete", batch_id),
                    ("installed:oxygen_system", "parts", 1, "repair_complete", batch_id),
                ),
            )
        if updated.pending_repair_mode == "full":
            updated = replace(
                updated,
                residual_damage_after_turns=0,
                residual_damage_exposure=0,
                residual_damage_confirmed=False,
            )
        completion = next(
            (item for item in station_turn.evidence if item.kind == "repair_complete"), None
        )
        message = (
            completion.message
            if completion is not None
            else "The oxygen system repair is complete."
        )
        updated, item = _emit_evidence(
            updated,
            TradeEvidenceKind.REPAIR_COMPLETE,
            message,
            "station",
            asset_id="oxygen_system",
            batch_id=batch_id,
            contract_id=contract_id,
            shipment_id=shipment_id,
        )
        emitted.extend(item)

    for station_evidence in station_turn.evidence:
        if station_evidence.kind == "repair_complete":
            continue
        updated, item = _emit_evidence(
            updated,
            TradeEvidenceKind.STATION_EVIDENCE,
            station_evidence.message,
            "station",
            asset_id="oxygen_system" if station_evidence.kind in {"alert", "inspection"} else None,
            finding_code=(
                station_evidence.code.value
                if station_evidence.code is not None
                else station_evidence.kind
            ),
        )
        emitted.extend(item)

    # Existing contracts are dispatched first; a new departure cannot arrive on this turn.
    for index, shipment in enumerate(updated.shipments):
        if shipment.status != "booked":
            continue
        departed = replace(
            shipment,
            status="in_transit",
            departure_turn=updated.station.turn,
            arrival_turn=updated.station.turn + updated.travel_turns,
        )
        updated = _replace_shipment(updated, index, departed)
        updated = _replace_contract_status(updated, shipment.contract_id, "in_transit")
        updated = _append_ledger(
            updated,
            (
                (
                    f"reserved:{shipment.shipment_id}",
                    _contract(updated, shipment.contract_id).resource,
                    -shipment.quantity,
                    "departure",
                    shipment.contract_id,
                ),
                (
                    f"transit:{shipment.shipment_id}",
                    _contract(updated, shipment.contract_id).resource,
                    shipment.quantity,
                    "departure",
                    shipment.contract_id,
                ),
            ),
        )
        updated, item = _emit_evidence(
            updated,
            TradeEvidenceKind.DEPARTURE,
            f"Shipment {shipment.shipment_id} departed for {shipment.destination}.",
            shipment.origin,
            batch_id=shipment.batch_id,
            contract_id=shipment.contract_id,
            shipment_id=shipment.shipment_id,
        )
        emitted.extend(item)

    for index, shipment in enumerate(updated.shipments):
        if shipment.status != "in_transit" or shipment.arrival_turn is None:
            continue
        if shipment.arrival_turn > updated.station.turn:
            continue
        contract = _contract(updated, shipment.contract_id)
        source_lot = shipment.cargo or _lot_for_contract(
            updated, shipment.origin, shipment.batch_id, contract
        )
        received_lot = replace(
            source_lot,
            quantity=shipment.quantity,
            contract_id=shipment.contract_id,
            shipment_id=shipment.shipment_id,
        )
        updated = _set_lots(
            updated,
            shipment.destination,
            _lots(updated, shipment.destination) + (received_lot,),
        )
        updated = _replace_shipment(updated, index, replace(shipment, status="delivered"))
        updated = _replace_contract_status(updated, shipment.contract_id, "settled")
        updated = replace(updated, escrow_credits=updated.escrow_credits - contract.total_price)
        updated = _set_credits(
            updated,
            shipment.origin,
            _credits(updated, shipment.origin) + contract.total_price,
        )
        updated = _append_ledger(
            updated,
            (
                (
                    f"transit:{shipment.shipment_id}",
                    contract.resource,
                    -shipment.quantity,
                    "arrival",
                    contract.contract_id,
                ),
                (
                    f"inventory:{shipment.destination}:{shipment.batch_id}:{shipment.shipment_id}",
                    contract.resource,
                    shipment.quantity,
                    "arrival",
                    contract.contract_id,
                ),
                (
                    f"escrow:{contract.contract_id}",
                    "credits",
                    -contract.total_price,
                    "settlement",
                    contract.contract_id,
                ),
                (
                    f"credits:{shipment.origin}",
                    "credits",
                    contract.total_price,
                    "settlement",
                    contract.contract_id,
                ),
            ),
        )
        updated, arrival_events = _emit_evidence(
            updated,
            TradeEvidenceKind.ARRIVAL,
            f"Shipment {shipment.shipment_id} arrived with {shipment.quantity} {contract.unit} "
            f"of {contract.resource}.",
            shipment.destination,
            batch_id=shipment.batch_id,
            contract_id=shipment.contract_id,
            shipment_id=shipment.shipment_id,
        )
        updated, settlement_events = _emit_evidence(
            updated,
            TradeEvidenceKind.SETTLEMENT,
            f"Contract {contract.contract_id} settled on delivery.",
            shipment.origin,
            batch_id=shipment.batch_id,
            contract_id=shipment.contract_id,
            shipment_id=shipment.shipment_id,
        )
        emitted.extend(arrival_events)
        emitted.extend(settlement_events)

    if (
        updated.station.crew_alive
        and updated.station.repair_turns_remaining == 0
        and not repair_completed
    ):
        load = (
            "backup"
            if before.station.backup_active and before.station.backup_oxygen > 0
            else updated.operating_load
        )
        exposed = _apply_equipment_exposure(updated, load)
        updated = exposed.state
        emitted.extend(exposed.evidence)

    from .deception import publish_due_reports
    from .sensors import advance_sensor_drift

    updated = advance_sensor_drift(updated)
    reports = publish_due_reports(updated)
    updated = reports.state
    emitted.extend(reports.evidence)
    return WorldAdvanceResult(updated, tuple(emitted))


def _apply_equipment_exposure(state: WorldState, load: str) -> WorldAdvanceResult:
    """One actual operating cycle; no authored calendar event can replace its cause."""
    part = state.installed_part
    part_failed = False
    if part is not None and not part.defect_confirmed and part.failure_load in {"any", load}:
        cycles = part.operating_turns + 1
        part_failed = part.latent_defect and cycles >= part.defect_after_turns
        part = replace(part, operating_turns=cycles, defect_confirmed=part_failed)
    damage_exposure = state.residual_damage_exposure
    damage_confirmed = state.residual_damage_confirmed
    if state.residual_damage_after_turns and load in {"peak", "backup"}:
        damage_exposure += 1
        damage_confirmed = damage_exposure >= state.residual_damage_after_turns
    damaged_failure = (
        damage_confirmed and load in {"peak", "backup"} and not state.station.leak_active
    )
    failed = part_failed or damaged_failure
    updated = replace(
        state,
        installed_part=part,
        residual_damage_exposure=damage_exposure,
        residual_damage_confirmed=damage_confirmed,
        station=replace(state.station, leak_active=True) if failed else state.station,
    )
    if failed:
        updated, evidence = _emit_evidence(
            updated,
            TradeEvidenceKind.FAILURE,
            (
                "Oxygen equipment showed an additional malfunction during operation."
                if state.station.leak_active
                else "A new oxygen leak appeared during operation and requires investigation."
            ),
            "station",
            asset_id="oxygen_system",
            finding_code="oxygen_leak_detected",
            operating_load=load,
        )
        return WorldAdvanceResult(updated, evidence)
    return WorldAdvanceResult(updated)


def set_operating_load(state: WorldState, load: str) -> TradeResult:
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(load, str) or load not in {"routine", "peak"}:
        return _rejected(state, "invalid_operating_load")
    updated, evidence = _emit_evidence(
        replace(state, operating_load=load),
        TradeEvidenceKind.LOAD_CHANGE,
        f"Oxygen equipment operating load set to {load}.",
        "station",
        asset_id="oxygen_system",
        operating_load=load,
    )
    return TradeResult(updated, True, evidence=evidence)


def repair_with_batch(state: WorldState, batch_id: str, *, mode: str = "full") -> TradeResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(mode, str) or mode not in {"full", "stabilize"}:
        return _rejected(state, "invalid_repair_mode")
    if not state.station.leak_active:
        return _rejected(state, "repair_not_needed")
    if state.station.repair_turns_remaining > 0:
        return _rejected(state, "repair_in_progress")
    if not isinstance(batch_id, str) or not batch_id:
        return _rejected(state, "invalid_batch_id")
    selected = _available_lot_index(state, "station", batch_id)
    if selected is None:
        return _rejected(state, "batch_not_in_station_inventory")
    if state.station.parts < 1:
        return _rejected(state, "insufficient_parts")

    lot = state.station_lots[selected]
    if lot.resource != "parts":
        return _rejected(state, "batch_not_in_station_inventory")
    if lot.quantity < 1:
        return _rejected(state, "batch_not_in_station_inventory")
    result = apply_action(state.station, Action(ActionKind.ASSIGN_REPAIR, target="oxygen_system"))
    if not result.accepted:
        return _rejected(state, result.rejection or "repair_rejected")
    station_lots = list(state.station_lots)
    station_lots[selected] = replace(lot, quantity=lot.quantity - 1)
    updated = replace(
        state,
        station=result.state,
        station_lots=tuple(station_lots),
        pending_repair_part=replace(lot, quantity=1),
        pending_repair_mode=mode,
    )
    updated = _append_ledger(
        updated,
        (
            (
                f"inventory:station:{batch_id}:{lot.shipment_id or 'local'}",
                "parts",
                -1,
                "repair_assigned",
                lot.contract_id or batch_id,
            ),
            (
                "repair_workshop:oxygen_system",
                "parts",
                1,
                "repair_assigned",
                lot.contract_id or batch_id,
            ),
        ),
    )
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.REPAIR_ASSIGNED,
        result.evidence[0].message,
        "station",
        asset_id="oxygen_system",
        batch_id=lot.batch_id,
        contract_id=lot.contract_id,
        shipment_id=lot.shipment_id,
    )
    return TradeResult(updated, True, evidence=evidence)


def inspect_installed_batch(state: WorldState, *, method: str = "routine") -> TradeResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(method, str) or method not in {"routine", "peak", "backup"}:
        return _rejected(state, "invalid_inspection_method")
    if state.installed_part is None:
        return _rejected(state, "no_part_installed")
    if state.station.available_crew <= 0:
        return _rejected(state, "crew_unavailable")
    if state.station.repair_turns_remaining:
        return _rejected(state, "repair_in_progress")
    if method == "backup" and not (state.station.backup_active and state.station.backup_oxygen > 0):
        return _rejected(state, "backup_not_operating")
    updated = replace(
        state, station=replace(state.station, available_crew=state.station.available_crew - 1)
    )
    stress = (
        _apply_equipment_exposure(updated, method)
        if method != "routine"
        else WorldAdvanceResult(updated)
    )
    updated = stress.state
    part = updated.installed_part
    assert part is not None
    if part.defect_confirmed:
        message = (
            f"Inspection found a material defect in installed batch "
            f"{part.batch_id} from {part.origin_world}."
        )
        finding_code = "material_defect_confirmed"
        batch_id, contract_id, shipment_id = part.batch_id, part.contract_id, part.shipment_id
    elif updated.residual_damage_confirmed:
        message = "Inspection found residual oxygen-system damage after stabilization."
        finding_code = "residual_damage_confirmed"
        batch_id = contract_id = shipment_id = None
    else:
        message = (
            f"Inspection traced the installed oxygen-system part to batch {part.batch_id} "
            f"from {part.origin_world}; no material defect observed under {method} conditions."
        )
        finding_code = "installed_batch_traced"
        batch_id, contract_id, shipment_id = part.batch_id, part.contract_id, part.shipment_id
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.INSPECTION,
        message,
        "station",
        asset_id="oxygen_system",
        batch_id=batch_id,
        contract_id=contract_id,
        shipment_id=shipment_id,
        finding_code=finding_code,
        method=method,
        operating_load=method,
    )
    if part.defect_confirmed and updated.residual_damage_confirmed:
        updated, residual = _emit_evidence(
            updated,
            TradeEvidenceKind.INSPECTION,
            "Inspection also found residual oxygen-system damage after stabilization.",
            "station",
            asset_id="oxygen_system",
            finding_code="residual_damage_confirmed",
            method=method,
            operating_load=method,
        )
        evidence += residual
    return TradeResult(updated, True, evidence=stress.evidence + evidence)


def quarantine_batch(state: WorldState, batch_id: str, *, quantity: int = 1) -> TradeResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if type(quantity) is not int or not 1 <= quantity <= MAX_TRADE_QUANTITY:
        return _rejected(state, "invalid_quantity")
    if not isinstance(batch_id, str) or not batch_id:
        return _rejected(state, "invalid_batch_id")
    available = sum(
        lot.quantity for lot in state.station_lots if lot.batch_id == batch_id and lot.quantity > 0
    )
    if available < quantity:
        return _rejected(state, "batch_not_in_station_inventory")

    updated = state
    remaining = quantity
    emitted: list[TradeEvidence] = []
    for index, lot in enumerate(updated.station_lots):
        if lot.batch_id != batch_id or lot.quantity <= 0 or remaining <= 0:
            continue
        removed = min(lot.quantity, remaining)
        changed = list(updated.station_lots)
        changed[index] = replace(lot, quantity=lot.quantity - removed)
        updated = _set_lots(updated, "station", tuple(changed))
        reference = lot.contract_id or batch_id
        updated = _append_ledger(
            updated,
            (
                (
                    f"inventory:station:{batch_id}:{lot.shipment_id or 'local'}",
                    lot.resource,
                    -removed,
                    "quarantine",
                    reference,
                ),
                (f"quarantine:{batch_id}", lot.resource, removed, "quarantine", reference),
            ),
        )
        updated, event = _emit_evidence(
            updated,
            TradeEvidenceKind.QUARANTINE,
            f"Quarantined {removed} unused {lot.unit} of {lot.resource} from batch {batch_id}.",
            "station",
            asset_id="oxygen_system" if lot.resource == "parts" else None,
            batch_id=batch_id,
            contract_id=lot.contract_id,
            shipment_id=lot.shipment_id,
        )
        emitted.extend(event)
        remaining -= removed
    return TradeResult(updated, True, evidence=tuple(emitted))


def observe_world(state: WorldState, world_id: str) -> PublicWorldView:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not _world_exists(state, world_id):
        raise ValueError(f"Unknown world: {world_id!r}")
    if world_id == "station":
        credits = state.station.credits
        parts = state.station.parts
        sensors = state.station.sensor_readings
        backup = state.station.backup_oxygen
        crew = state.station.available_crew
        alive = state.station.crew_alive
        repair_remaining = state.station.repair_turns_remaining
    else:
        inventory = _inventory(state, world_id)
        assert inventory is not None
        credits = inventory.credits
        parts = inventory.parts
        sensors = ()
        backup = 0
        crew = 0
        alive = True
        repair_remaining = 0

    local_lots = tuple(
        _public_lot(lot, world_id) for lot in _lots(state, world_id) if lot.quantity > 0
    )
    seller_ids = sorted(("station", *(inventory.world_id for inventory in state.inventories)))
    market_offers = []
    seen_offers: set[tuple[str, str]] = set()
    for seller_id in seller_ids:
        for lot in _lots(state, seller_id):
            if lot.quantity <= 0:
                continue
            key = (seller_id, lot.batch_id)
            quantity = _offer_quantity(state, seller_id, lot.batch_id)
            if key in seen_offers or quantity <= 0:
                continue
            seen_offers.add(key)
            market_offers.append(_public_lot(lot, seller_id, quantity))
    offers = tuple(market_offers)
    contracts = tuple(
        PublicContract(
            contract.contract_id,
            contract.buyer_id,
            contract.seller_id,
            contract.batch_id,
            contract.quantity,
            contract.unit_price,
            contract.status,
            contract.resource,
            contract.unit,
        )
        for contract in state.contracts
        if world_id in {contract.buyer_id, contract.seller_id}
    )
    shipments = tuple(
        PublicShipment(
            shipment.shipment_id,
            shipment.contract_id,
            shipment.origin,
            shipment.destination,
            shipment.batch_id,
            shipment.quantity,
            shipment.status,
            shipment.departure_turn,
            shipment.arrival_turn,
            _contract(state, shipment.contract_id).resource,
            _contract(state, shipment.contract_id).unit,
        )
        for shipment in state.shipments
        if world_id in {shipment.origin, shipment.destination}
    )
    evidence = [item for item in state.evidence if item.world_id == world_id]
    if world_id == "station":
        known_station_events = {
            (item.turn, item.message, item.code)
            for item in evidence
            if item.kind is TradeEvidenceKind.STATION_EVIDENCE
        }
        next_sequence = max((item.sequence for item in state.evidence), default=0) + 1
        for item in state.station.evidence:
            if item.kind == "repair_complete":
                continue
            code = item.code.value if item.code is not None else item.kind
            if (item.turn, item.message, code) in known_station_events:
                continue
            evidence.append(
                TradeEvidence(
                    sequence=next_sequence,
                    turn=item.turn,
                    kind=TradeEvidenceKind.STATION_EVIDENCE,
                    message=item.message,
                    world_id="station",
                    finding_code=code,
                )
            )
            next_sequence += 1
    return PublicWorldView(
        world_id=world_id,
        turn=state.station.turn,
        credits=credits,
        parts=parts,
        oxygen_sensors=sensors,
        backup_oxygen=backup,
        available_crew=crew,
        crew_alive=alive,
        repair_turns_remaining=repair_remaining,
        offers=offers,
        local_lots=local_lots,
        contracts=contracts,
        shipments=shipments,
        evidence=tuple(sorted(evidence, key=lambda item: (item.turn, item.sequence))),
        operating_load=state.operating_load if world_id == "station" else "routine",
    )


MAX_TRADE_QUANTITY = 3
MAX_UNIT_PRICE = 1_000_000
_COMMAND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


def _validate_nonnegative_integer(name: str, value: object, *, maximum: int) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 0 to {maximum}")


def _validate_bounded_positive(name: str, value: object, *, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 to {maximum}")


def _world_exists(state: WorldState, world_id: str) -> bool:
    return world_id == "station" or _inventory(state, world_id) is not None


def _inventory(state: WorldState, world_id: str) -> WorldInventory | None:
    return next((item for item in state.inventories if item.world_id == world_id), None)


def _lots(state: WorldState, world_id: str) -> tuple[PartLot, ...]:
    if world_id == "station":
        return state.station_lots
    inventory = _inventory(state, world_id)
    return inventory.lots if inventory is not None else ()


def _credits(state: WorldState, world_id: str) -> int:
    if world_id == "station":
        return state.station.credits
    inventory = _inventory(state, world_id)
    return inventory.credits if inventory is not None else 0


def _set_credits(state: WorldState, world_id: str, credits: int) -> WorldState:
    if world_id == "station":
        return replace(state, station=replace(state.station, credits=credits))
    inventories = tuple(
        replace(inventory, credits=credits) if inventory.world_id == world_id else inventory
        for inventory in state.inventories
    )
    return replace(state, inventories=inventories)


def _set_lots(state: WorldState, world_id: str, lots: tuple[PartLot, ...]) -> WorldState:
    if world_id == "station":
        return replace(
            state,
            station_lots=lots,
            station=replace(
                state.station,
                parts=sum(lot.quantity for lot in lots if lot.resource == "parts"),
            ),
        )
    inventories = tuple(
        replace(inventory, lots=lots) if inventory.world_id == world_id else inventory
        for inventory in state.inventories
    )
    return replace(state, inventories=inventories)


def _available_lot_index(state: WorldState, world_id: str, batch_id: str) -> int | None:
    return next(
        (
            index
            for index, lot in enumerate(_lots(state, world_id))
            if lot.batch_id == batch_id and lot.quantity > 0
        ),
        None,
    )


def _resource_capacities(state: WorldState, world_id: str) -> tuple[tuple[str, int], ...]:
    if world_id == "station":
        return state.station_resource_capacities
    inventory = _inventory(state, world_id)
    return inventory.resource_capacities if inventory is not None else ()


def _resource_reserves(state: WorldState, world_id: str) -> tuple[tuple[str, int], ...]:
    if world_id == "station":
        return state.station_resource_reserves
    inventory = _inventory(state, world_id)
    return inventory.resource_reserves if inventory is not None else ()


def _resource_capacity(state: WorldState, world_id: str, resource: str) -> int | None:
    capacities = dict(_resource_capacities(state, world_id))
    if resource == "parts":
        return capacities.get(resource)
    return capacities.get(resource, 1000)


def _resource_reserve(state: WorldState, world_id: str, resource: str) -> int:
    return dict(_resource_reserves(state, world_id)).get(resource, 0)


def _stock_quantity(state: WorldState, world_id: str, resource: str) -> int:
    return sum(lot.quantity for lot in _lots(state, world_id) if lot.resource == resource)


def validate_storage_commitments(state: WorldState) -> None:
    """Reject explicit storage limits that cannot hold stock and committed arrivals."""
    for world_id in ("station", *(world.world_id for world in state.inventories)):
        for resource, capacity in _resource_capacities(state, world_id):
            if (
                _stock_quantity(state, world_id, resource)
                + incoming_quantity(state, world_id, resource)
                > capacity
            ):
                raise ValueError(f"Committed {resource} exceeds {world_id} storage capacity")


def incoming_quantity(state: WorldState, world_id: str, resource: str) -> int:
    """Stock already committed by trade or existing station delivery orders."""
    legacy = (
        sum(
            delivery.quantity * max(0, min(100, delivery.fill_percent)) // 100
            for delivery in state.station.deliveries
            if delivery.supply == "parts" and delivery.due_turn > state.station.turn
        )
        if world_id == "station" and resource == "parts"
        else 0
    )
    return legacy + sum(
        shipment.quantity
        for shipment in state.shipments
        if shipment.destination == world_id
        and shipment.status in {"booked", "in_transit"}
        and _contract(state, shipment.contract_id).resource == resource
    )


def _offer_quantity(state: WorldState, world_id: str, batch_id: str) -> int:
    lots = _lots(state, world_id)
    selected = next((lot for lot in lots if lot.batch_id == batch_id), None)
    if selected is None:
        return 0
    remaining = max(
        0,
        _stock_quantity(state, world_id, selected.resource)
        - _resource_reserve(state, world_id, selected.resource),
    )
    for lot in lots:
        if lot.resource != selected.resource or lot.quantity <= 0:
            continue
        available = min(lot.quantity, remaining)
        if lot.batch_id == batch_id:
            return available
        remaining -= available
    return 0


def _public_lot(lot: PartLot, seller_world: str, quantity: int | None = None) -> PublicLot:
    return PublicLot(
        lot.batch_id,
        lot.quantity if quantity is None else quantity,
        lot.unit_price,
        lot.origin_world,
        lot.resource,
        lot.unit,
        seller_world,
    )


def _append_ledger(
    state: WorldState,
    rows: tuple[tuple[str, str, int, str, str], ...],
) -> WorldState:
    entries = tuple(
        LedgerEntry(
            sequence=len(state.ledger) + index + 1,
            turn=state.station.turn,
            account=account,
            resource=resource,
            delta=delta,
            kind=kind,
            reference_id=reference_id,
        )
        for index, (account, resource, delta, kind, reference_id) in enumerate(rows)
    )
    return replace(state, ledger=state.ledger + entries)


def _replace_shipment(state: WorldState, index: int, shipment: Shipment) -> WorldState:
    shipments = list(state.shipments)
    shipments[index] = shipment
    return replace(state, shipments=tuple(shipments))


def _replace_contract_status(state: WorldState, contract_id: str, status: str) -> WorldState:
    contracts = tuple(
        replace(contract, status=status) if contract.contract_id == contract_id else contract
        for contract in state.contracts
    )
    return replace(state, contracts=contracts)


def _contract(state: WorldState, contract_id: str) -> TradeContract:
    return next(item for item in state.contracts if item.contract_id == contract_id)


def _lot_for_contract(
    state: WorldState, seller_id: str, batch_id: str, contract: TradeContract
) -> PartLot:
    lots = _lots(state, seller_id)
    return next(
        lot for lot in lots if lot.batch_id == batch_id and lot.unit_price == contract.unit_price
    )
