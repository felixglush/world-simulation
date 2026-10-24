"""Immutable trade state, public projections, and shared transition results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .domain import SensorReading, StationState


@dataclass(frozen=True, slots=True)
class TradeReport:
    report_id: str
    source_id: str
    recipient_world: str
    message: str
    observed_turn: int
    receipt_turn: int
    batch_id: str | None = None
    asset_id: str | None = None
    upstream_report_id: str | None = None


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
    REPORT = "report"
    TRACE = "trace"
    LOAD_CHANGE = "load_change"
    ASSAY = "assay"
    CONSUMPTION = "consumption"
    CALIBRATION = "calibration"
    COMMAND_REJECTED = "command_rejected"


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
    resource: str = "parts"
    unit: str = "parts"
    failure_load: str = "any"
    yield_percent: int = 100


@dataclass(frozen=True, slots=True)
class WorldInventory:
    world_id: str
    credits: int
    lots: tuple[PartLot, ...]
    resource_capacities: tuple[tuple[str, int], ...] = ()
    resource_reserves: tuple[tuple[str, int], ...] = ()

    @property
    def parts(self) -> int:
        return sum(lot.quantity for lot in self.lots if lot.resource == "parts")


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
    resource: str = "parts"
    unit: str = "parts"


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
    cargo: PartLot | None = None


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
    failure_load: str = "any"


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
    source_id: str | None = None
    report_id: str | None = None
    observed_turn: int | None = None
    upstream_source_id: str | None = None
    upstream_report_id: str | None = None
    method: str | None = None
    operating_load: str | None = None
    measured_value: int | None = None
    measured_unit: str | None = None

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
    station_resource_capacities: tuple[tuple[str, int], ...] = ()
    station_resource_reserves: tuple[tuple[str, int], ...] = ()
    operating_load: str = "routine"
    residual_damage_after_turns: int = 0
    residual_damage_exposure: int = 0
    residual_damage_confirmed: bool = False
    pending_repair_mode: str = "full"
    sensor_drift_per_turn: int = 0
    sensor_drift_bias: int = 0
    sensor_drift_limit: int = 200
    reports: tuple[TradeReport, ...] = ()

    @property
    def pending_repair_batch_id(self) -> str | None:
        return self.pending_repair_part.batch_id if self.pending_repair_part else None


@dataclass(frozen=True, slots=True)
class PublicLot:
    batch_id: str
    quantity: int
    unit_price: int
    origin_world: str
    resource: str = "parts"
    unit: str = "parts"
    seller_world: str = ""
    contract_id: str | None = None
    shipment_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublicContract:
    contract_id: str
    buyer_id: str
    seller_id: str
    batch_id: str
    quantity: int
    unit_price: int
    status: str
    resource: str = "parts"
    unit: str = "parts"


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
    resource: str = "parts"
    unit: str = "parts"


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
    operating_load: str = "routine"
    backup_active: bool = False


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


def record_evidence(
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
    source_id: str | None = None,
    report_id: str | None = None,
    observed_turn: int | None = None,
    upstream_source_id: str | None = None,
    upstream_report_id: str | None = None,
    method: str | None = None,
    operating_load: str | None = None,
    measured_value: int | None = None,
    measured_unit: str | None = None,
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
        source_id=source_id,
        report_id=report_id,
        observed_turn=observed_turn,
        upstream_source_id=upstream_source_id,
        upstream_report_id=upstream_report_id,
        method=method,
        operating_load=operating_load,
        measured_value=measured_value,
        measured_unit=measured_unit,
    )
    return replace(state, evidence=state.evidence + (evidence,)), (evidence,)


def reject_trade(state: WorldState, reason: str) -> TradeResult:
    return TradeResult(state=state, accepted=False, rejection=reason)
