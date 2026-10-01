"""In-memory trade domain for the station and neighboring industrial world."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from .domain import (
    Action,
    ActionKind,
    SensorReading,
    StationState,
    advance_turn,
    apply_action,
)
from .scenarios import ScenarioFamily, create_world


class TradeEvidenceKind(StrEnum):
    PURCHASE = "purchase"
    DEPARTURE = "departure"
    ARRIVAL = "arrival"
    SETTLEMENT = "settlement"
    REPAIR_ASSIGNED = "repair_assigned"
    REPAIR_COMPLETE = "repair_complete"
    FAILURE = "failure"
    INSPECTION = "inspection"
    QUARANTINE = "quarantine"
    STATION_EVIDENCE = "station_evidence"


@dataclass(frozen=True, slots=True)
class PartLot:
    batch_id: str
    quantity: int
    unit_price: int
    origin_world: str
    latent_defect: bool = False
    defect_after_turns: int = 0
    contract_id: str | None = None
    shipment_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorldInventory:
    world_id: str
    credits: int
    lots: tuple[PartLot, ...]

    @property
    def parts(self) -> int:
        return sum(lot.quantity for lot in self.lots)


@dataclass(frozen=True, slots=True)
class TradeContract:
    contract_id: str
    command_id: str
    buyer_id: str
    seller_id: str
    batch_id: str
    quantity: int
    unit_price: int
    total_price: int
    status: str


@dataclass(frozen=True, slots=True)
class Shipment:
    shipment_id: str
    contract_id: str
    origin: str
    destination: str
    batch_id: str
    quantity: int
    status: str
    departure_turn: int | None = None
    arrival_turn: int | None = None


@dataclass(frozen=True, slots=True)
class InstalledPart:
    batch_id: str
    origin_world: str
    contract_id: str | None
    shipment_id: str | None
    latent_defect: bool
    defect_after_turns: int
    operating_turns: int = 0
    defect_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    sequence: int
    turn: int
    account: str
    resource: str
    delta: int
    kind: str
    reference_id: str


@dataclass(frozen=True, slots=True)
class TradeEvidence:
    sequence: int
    turn: int
    kind: TradeEvidenceKind
    message: str
    world_id: str
    asset_id: str | None = None
    batch_id: str | None = None
    contract_id: str | None = None
    shipment_id: str | None = None
    finding_code: str | None = None

    @property
    def code(self) -> str:
        return self.finding_code or self.kind.value


@dataclass(frozen=True, slots=True)
class WorldState:
    station: StationState
    inventories: tuple[WorldInventory, ...]
    station_lots: tuple[PartLot, ...]
    contracts: tuple[TradeContract, ...] = ()
    shipments: tuple[Shipment, ...] = ()
    installed_part: InstalledPart | None = None
    pending_repair_part: PartLot | None = None
    ledger: tuple[LedgerEntry, ...] = ()
    evidence: tuple[TradeEvidence, ...] = ()
    escrow_credits: int = 0
    travel_turns: int = 2
    shipment_capacity: int = 3
    defect_after_turns: int = 3

    @property
    def pending_repair_batch_id(self) -> str | None:
        return self.pending_repair_part.batch_id if self.pending_repair_part else None


@dataclass(frozen=True, slots=True)
class PublicLot:
    batch_id: str
    quantity: int
    unit_price: int
    origin_world: str


@dataclass(frozen=True, slots=True)
class PublicContract:
    contract_id: str
    buyer_id: str
    seller_id: str
    batch_id: str
    quantity: int
    unit_price: int
    status: str


@dataclass(frozen=True, slots=True)
class PublicShipment:
    shipment_id: str
    contract_id: str
    origin: str
    destination: str
    batch_id: str
    quantity: int
    status: str
    departure_turn: int | None
    arrival_turn: int | None


@dataclass(frozen=True, slots=True)
class PublicWorldView:
    world_id: str
    turn: int
    credits: int
    parts: int
    oxygen_sensors: tuple[SensorReading, ...] = ()
    backup_oxygen: int = 0
    available_crew: int = 0
    crew_alive: bool = True
    repair_turns_remaining: int = 0
    offers: tuple[PublicLot, ...] = ()
    local_lots: tuple[PublicLot, ...] = ()
    contracts: tuple[PublicContract, ...] = ()
    shipments: tuple[PublicShipment, ...] = ()
    evidence: tuple[TradeEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class TradeResult:
    state: WorldState
    accepted: bool
    rejection: str | None = None
    evidence: tuple[TradeEvidence, ...] = ()
    contract_id: str | None = None
    shipment_id: str | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class WorldAdvanceResult:
    state: WorldState
    evidence: tuple[TradeEvidence, ...] = ()


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


def purchase_parts(
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
    if lot.quantity < quantity:
        return _rejected(state, "insufficient_stock")
    if type(lot.unit_price) is not int or not 1 <= lot.unit_price <= 1_000_000:
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
    )
    shipment = Shipment(
        shipment_id,
        contract_id,
        seller_id,
        buyer_id,
        batch_id,
        quantity,
        "booked",
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
                f"inventory:{seller_id}:{batch_id}",
                "parts",
                -quantity,
                "purchase",
                contract_id,
            ),
            (f"reserved:{shipment_id}", "parts", quantity, "purchase", contract_id),
        ),
    )
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.PURCHASE,
        f"Purchased {quantity} part(s) from {seller_id}; funds are held pending delivery.",
        buyer_id,
        batch_id=batch_id,
        contract_id=contract_id,
        shipment_id=shipment_id,
    )
    return TradeResult(
        updated, True, evidence=evidence, contract_id=contract_id, shipment_id=shipment_id
    )


def advance_world(state: WorldState) -> WorldAdvanceResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return WorldAdvanceResult(state)

    before = state
    station_turn = advance_turn(state.station)
    updated = replace(state, station=station_turn.state)
    emitted: list[TradeEvidence] = []

    untracked_delivery = station_turn.state.parts - sum(lot.quantity for lot in state.station_lots)
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
                    "parts",
                    -shipment.quantity,
                    "departure",
                    shipment.contract_id,
                ),
                (
                    f"transit:{shipment.shipment_id}",
                    "parts",
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
        source_lot = _lot_for_contract(updated, shipment.origin, shipment.batch_id, contract)
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
                    "parts",
                    -shipment.quantity,
                    "arrival",
                    contract.contract_id,
                ),
                (
                    f"inventory:{shipment.destination}:{shipment.batch_id}:{shipment.shipment_id}",
                    "parts",
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
            f"Shipment {shipment.shipment_id} arrived with {shipment.quantity} part(s).",
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

    installed = updated.installed_part
    if (
        installed is not None
        and not installed.defect_confirmed
        and updated.station.crew_alive
        and updated.station.repair_turns_remaining == 0
        and not repair_completed
    ):
        operating_turns = installed.operating_turns + 1
        failed = installed.latent_defect and operating_turns >= installed.defect_after_turns
        installed = replace(
            installed,
            operating_turns=operating_turns,
            defect_confirmed=failed,
        )
        updated = replace(
            updated,
            station=replace(updated.station, leak_active=True) if failed else updated.station,
            installed_part=installed,
        )
        if failed:
            updated, item = _emit_evidence(
                updated,
                TradeEvidenceKind.FAILURE,
                "A new oxygen leak appeared during operation and requires investigation.",
                "station",
                asset_id="oxygen_system",
                finding_code="oxygen_leak_detected",
            )
            emitted.extend(item)

    return WorldAdvanceResult(updated, tuple(emitted))


def repair_with_batch(state: WorldState, batch_id: str) -> TradeResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
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


def inspect_installed_batch(state: WorldState) -> TradeResult:
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if state.installed_part is None:
        return _rejected(state, "no_part_installed")
    part = state.installed_part
    if part.defect_confirmed:
        message = (
            f"Inspection traced the oxygen leak to a material defect in installed batch "
            f"{part.batch_id} from {part.origin_world}."
        )
        finding_code = "material_defect_confirmed"
    else:
        message = (
            f"Inspection traced the installed oxygen-system part to batch {part.batch_id} "
            f"from {part.origin_world}; no material defect observed under current conditions."
        )
        finding_code = "installed_batch_traced"
    updated, evidence = _emit_evidence(
        state,
        TradeEvidenceKind.INSPECTION,
        message,
        "station",
        asset_id="oxygen_system",
        batch_id=part.batch_id,
        contract_id=part.contract_id,
        shipment_id=part.shipment_id,
        finding_code=finding_code,
    )
    return TradeResult(updated, True, evidence=evidence)


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
                    "parts",
                    -removed,
                    "quarantine",
                    reference,
                ),
                (f"quarantine:{batch_id}", "parts", removed, "quarantine", reference),
            ),
        )
        updated, event = _emit_evidence(
            updated,
            TradeEvidenceKind.QUARANTINE,
            f"Quarantined {removed} unused part(s) from batch {batch_id}.",
            "station",
            asset_id="oxygen_system",
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

    local_lots = tuple(_public_lot(lot) for lot in _lots(state, world_id) if lot.quantity > 0)
    offers = tuple(
        _public_lot(lot)
        for inventory in state.inventories
        for lot in inventory.lots
        if lot.quantity > 0
    )
    contracts = tuple(
        PublicContract(
            contract.contract_id,
            contract.buyer_id,
            contract.seller_id,
            contract.batch_id,
            contract.quantity,
            contract.unit_price,
            contract.status,
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
    )


MAX_TRADE_QUANTITY = 3
_COMMAND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


def _validate_nonnegative_integer(name: str, value: object, *, maximum: int) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 0 to {maximum}")


def _validate_bounded_positive(name: str, value: object, *, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 to {maximum}")


def _rejected(state: WorldState, reason: str) -> TradeResult:
    return TradeResult(state, False, rejection=reason)


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
            station=replace(state.station, parts=sum(lot.quantity for lot in lots)),
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


def _public_lot(lot: PartLot) -> PublicLot:
    return PublicLot(lot.batch_id, lot.quantity, lot.unit_price, lot.origin_world)


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


def _emit_evidence(
    state: WorldState,
    kind: TradeEvidenceKind,
    message: str,
    world_id: str,
    *,
    asset_id: str | None = None,
    batch_id: str | None = None,
    contract_id: str | None = None,
    shipment_id: str | None = None,
    finding_code: str | None = None,
) -> tuple[WorldState, tuple[TradeEvidence, ...]]:
    evidence = TradeEvidence(
        sequence=len(state.evidence) + 1,
        turn=state.station.turn,
        kind=kind,
        message=message,
        world_id=world_id,
        asset_id=asset_id,
        batch_id=batch_id,
        contract_id=contract_id,
        shipment_id=shipment_id,
        finding_code=finding_code,
    )
    return replace(state, evidence=state.evidence + (evidence,)), (evidence,)


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
