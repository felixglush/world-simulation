"""Evidence-driven station investigation and replenishment policy.

The governor is deliberately stateless: every decision is derived from the caller's
bounded public view and the constructor's immutable policy parameters.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .governors import TradeCommand
from .trade import PublicLot, PublicWorldView, TradeEvidence

_ASSET = "oxygen_system"
_PARTS = "parts"
_FEEDSTOCK = "oxygen_feedstock"
_CANISTERS = "canisters"
_MAX_QUANTITY = 3
_MATERIAL_DEFECT = "material_defect_confirmed"
_RESIDUAL_DAMAGE = "residual_damage_confirmed"
_AMBIGUOUS_SHIPMENT = object()


@dataclass(frozen=True, slots=True)
class InvestigationGovernor:
    """Choose bounded station actions using only public evidence and offers."""

    emergency_reserve: int = 60
    stress_method: str = "peak"
    stress_cycles: int = 2
    initial_repair_mode: str = "full"
    feedstock_target: int = 2

    def __post_init__(self) -> None:
        if type(self.emergency_reserve) is not int or self.emergency_reserve < 0:
            raise ValueError("emergency_reserve must be a nonnegative integer")
        if type(self.stress_cycles) is not int or not 0 <= self.stress_cycles <= 20:
            raise ValueError("stress_cycles must be an integer from 0 to 20")
        if not isinstance(self.stress_method, str) or self.stress_method not in {
            "routine",
            "peak",
            "backup",
        }:
            raise ValueError("stress_method must be routine, peak, or backup")
        if not isinstance(self.initial_repair_mode, str) or self.initial_repair_mode not in {
            "full",
            "stabilize",
        }:
            raise ValueError("initial_repair_mode must be full or stabilize")
        if (
            type(self.feedstock_target) is not int
            or not 0 <= self.feedstock_target <= _MAX_QUANTITY
        ):
            raise ValueError("feedstock_target must be an integer from 0 to 3")

    def decide(self, observation: PublicWorldView) -> TradeCommand | None:
        """Return one safe command, or ``None`` when public evidence needs no action."""
        if (
            not isinstance(observation, PublicWorldView)
            or observation.world_id != "station"
            or not observation.crew_alive
            or observation.available_crew <= 0
        ):
            return None

        history = _ordered(observation.evidence)
        if observation.repair_turns_remaining > 0 or _repair_pending(history):
            return None

        failure = _latest_unresolved_failure(history)
        if failure is not None:
            finding = _finding_after_failure(history, failure)
            if finding is None:
                return self._command(
                    observation,
                    "inspect",
                    discriminator=f"failure-{failure.sequence}",
                    method="routine" if self.stress_method == "backup" else self.stress_method,
                )
            diagnosis_action = self._respond_to_finding(observation, history, finding)
            return diagnosis_action

        calibration = (
            None if _calibration_unavailable(history) else _sensor_disagreement(observation)
        )
        if calibration:
            return self._command(
                observation,
                "calibrate",
                discriminator=f"{calibration[0]}-{calibration[1]}-{calibration[2]}",
            )

        part_lots = _lots_for(observation.local_lots, _PARTS)
        part_orders = _contracts_for(observation, _PARTS)
        if not part_orders and not part_lots:
            initial = self._purchase_offer(
                observation,
                resource=_PARTS,
                quantity=2,
                excluded_batches=set(),
                prefix="initial-parts",
            )
            if initial is not None:
                return initial

        if (
            not any(_kind(item, "repair_complete") for item in history)
            and not _repair_not_needed(history)
            and part_lots
        ):
            selected = min(part_lots, key=lambda item: (item.unit_price, item.batch_id))
            return self._command(
                observation,
                "repair",
                discriminator=f"initial-{selected.batch_id}",
                batch_id=selected.batch_id,
                repair_mode=self.initial_repair_mode,
            )

        latest_repair = _latest_repair_complete(history)
        if latest_repair is not None:
            passes = _stress_passes(history, latest_repair, self.stress_method)
            if len(passes) < self.stress_cycles:
                stress_available = True
                if self.stress_method == "backup":
                    if not observation.backup_active and observation.backup_oxygen > 0:
                        return self._command(
                            observation,
                            "backup",
                            discriminator=f"backup-for-stress-{latest_repair.sequence}",
                        )
                    if not observation.backup_active or observation.backup_oxygen <= 0:
                        stress_available = False
                if stress_available:
                    return self._command(
                        observation,
                        "inspect",
                        discriminator=f"stress-{latest_repair.sequence}-{len(passes) + 1}",
                        method=self.stress_method,
                    )
            if (
                len(passes) >= self.stress_cycles
                and passes
                and observation.operating_load != "peak"
                and not any(
                    _kind(item, "load_change") and item.operating_load == "peak" for item in history
                )
            ):
                return self._command(
                    observation,
                    "load",
                    discriminator=f"peak-after-{latest_repair.sequence}",
                    operating_load="peak",
                )

        feedstock_action = self._feedstock_action(observation, history)
        if feedstock_action is not None:
            return feedstock_action

        return self._trace_action(observation, history)

    def _respond_to_finding(
        self,
        observation: PublicWorldView,
        history: tuple[TradeEvidence, ...],
        finding: TradeEvidence,
    ) -> TradeCommand | None:
        parts = _lots_for(observation.local_lots, _PARTS)
        known_bad = _known_defective_parts(history)

        if finding.finding_code == _MATERIAL_DEFECT and finding.batch_id:
            affected = tuple(lot for lot in parts if _finding_matches_lot(finding, lot))
            bad_quantity = sum(lot.quantity for lot in affected)
            if bad_quantity:
                shipment_id = _quarantine_shipment(finding, affected, parts)
                if shipment_id is not _AMBIGUOUS_SHIPMENT:
                    return self._command(
                        observation,
                        "quarantine",
                        discriminator=(
                            f"bad-{finding.batch_id}-"
                            f"{shipment_id or finding.contract_id or 'local'}"
                        ),
                        batch_id=finding.batch_id,
                        quantity=min(_MAX_QUANTITY, bad_quantity),
                        **_shipment_argument(shipment_id),
                    )

        good_parts = tuple(lot for lot in parts if not _lot_is_known_bad(lot, known_bad))
        if good_parts:
            selected = min(
                good_parts,
                key=lambda item: (
                    item.unit_price,
                    item.batch_id,
                    item.shipment_id or "",
                    item.contract_id or "",
                ),
            )
            return self._command(
                observation,
                "repair",
                discriminator=(
                    f"recovery-{finding.sequence}-{selected.batch_id}-"
                    f"{selected.shipment_id or selected.contract_id or 'local'}"
                ),
                batch_id=selected.batch_id,
                repair_mode="full",
                **_shipment_argument(selected.shipment_id),
            )

        excluded_batches: set[str] = set()
        excluded_supplier_batches: set[tuple[str, str]] = set()
        for batch_id, shipment_id, contract_id in known_bad:
            seller = _delivery_seller(observation, batch_id, shipment_id, contract_id)
            if seller is None:
                excluded_batches.add(batch_id)
            else:
                excluded_supplier_batches.add((seller, batch_id))
        if finding.finding_code == _MATERIAL_DEFECT and finding.batch_id:
            seller = _delivery_seller(
                observation, finding.batch_id, finding.shipment_id, finding.contract_id
            )
            if seller is None:
                excluded_batches.add(finding.batch_id)
            else:
                excluded_supplier_batches.add((seller, finding.batch_id))
        pending = _pending_purchase(_contracts_for(observation, _PARTS))
        if pending:
            return None
        return self._purchase_offer(
            observation,
            resource=_PARTS,
            quantity=1,
            excluded_batches=excluded_batches,
            excluded_supplier_batches=excluded_supplier_batches,
            prefix="replacement-parts",
            reserve_floor=0,
        )

    def _feedstock_action(
        self, observation: PublicWorldView, history: tuple[TradeEvidence, ...]
    ) -> TradeCommand | None:
        if self.feedstock_target == 0:
            return None

        stock = tuple(
            lot for lot in _lots_for(observation.local_lots, _FEEDSTOCK) if lot.unit == _CANISTERS
        )
        assays = {
            _delivery_key(
                item.batch_id,
                getattr(item, "shipment_id", None),
                getattr(item, "contract_id", None),
            ): item
            for item in history
            if _kind(item, "assay") and item.batch_id is not None
        }
        deliveries = sorted(
            {_public_delivery_key(lot) for lot in stock},
            key=lambda key: (key[0], key[1] or "", key[2] or ""),
        )
        for delivery in deliveries:
            batch_id, shipment_id, contract_id = delivery
            batch_lots = tuple(lot for lot in stock if _public_delivery_key(lot) == delivery)
            quantity = sum(lot.quantity for lot in batch_lots)
            if quantity <= 0:
                continue
            assay = assays.get(delivery)
            if assay is None:
                return self._command(
                    observation,
                    "assay",
                    discriminator=f"feedstock-{batch_id}-{shipment_id or 'local'}",
                    batch_id=batch_id,
                    **_shipment_argument(shipment_id),
                )
            measured = assay.measured_value
            measured_unit = assay.measured_unit
            if type(measured) is not int or measured_unit != "percent":
                continue
            if measured < 100:
                return self._command(
                    observation,
                    "quarantine",
                    discriminator=f"poor-feedstock-{batch_id}-{shipment_id or 'local'}",
                    batch_id=batch_id,
                    quantity=min(_MAX_QUANTITY, quantity),
                    **_shipment_argument(shipment_id),
                )
            return self._command(
                observation,
                "consume",
                discriminator=f"clean-feedstock-{batch_id}-{shipment_id or 'local'}",
                batch_id=batch_id,
                quantity=min(_MAX_QUANTITY, self.feedstock_target, quantity),
                **_shipment_argument(shipment_id),
            )

        contracts = tuple(
            contract
            for contract in _contracts_for(observation, _FEEDSTOCK)
            if contract.unit == _CANISTERS
        )
        if _pending_purchase(contracts):
            return None
        if any(_assay_is_clean(item) for item in history):
            return None
        poor_deliveries = {
            _delivery_key(item.batch_id, item.shipment_id, item.contract_id)
            for item in history
            if item.batch_id is not None and _assay_is_poor(item)
        }
        if len(poor_deliveries) >= 2:
            return None
        excluded_batches: set[str] = set()
        excluded_supplier_batches = {
            (contract.seller_id, contract.batch_id)
            for contract in contracts
            if contract.seller_id and contract.batch_id
        }
        for item in history:
            if not _kind(item, "assay") or item.batch_id is None:
                continue
            seller = _delivery_seller(
                observation, item.batch_id, item.shipment_id, item.contract_id
            )
            if seller is None:
                excluded_batches.add(item.batch_id)
            else:
                excluded_supplier_batches.add((seller, item.batch_id))
        return self._purchase_offer(
            observation,
            resource=_FEEDSTOCK,
            quantity=self.feedstock_target,
            excluded_batches=excluded_batches,
            excluded_supplier_batches=excluded_supplier_batches,
            prefix="feedstock",
            unit=_CANISTERS,
        )

    def _purchase_offer(
        self,
        observation: PublicWorldView,
        *,
        resource: str,
        quantity: int,
        excluded_batches: set[str],
        prefix: str,
        unit: str | None = None,
        reserve_floor: int | None = None,
        excluded_supplier_batches: set[tuple[str, str]] | None = None,
    ) -> TradeCommand | None:
        if quantity < 1:
            return None
        floor = self.emergency_reserve if reserve_floor is None else reserve_floor
        eligible = tuple(
            offer
            for offer in observation.offers
            if offer.resource == resource
            and (unit is None or offer.unit == unit)
            and offer.seller_world
            and offer.seller_world != observation.world_id
            and offer.batch_id not in excluded_batches
            and (offer.seller_world, offer.batch_id) not in (excluded_supplier_batches or set())
            and offer.quantity >= quantity
            and type(offer.unit_price) is int
            and offer.unit_price > 0
            and observation.credits - offer.unit_price * quantity >= floor
        )
        if not eligible:
            return None
        offer = min(eligible, key=lambda item: (item.unit_price, item.batch_id, item.seller_world))
        return self._command(
            observation,
            "purchase",
            discriminator=f"{prefix}-{offer.batch_id}",
            seller_id=offer.seller_world,
            batch_id=offer.batch_id,
            quantity=quantity,
        )

    def _trace_action(
        self, observation: PublicWorldView, history: tuple[TradeEvidence, ...]
    ) -> TradeCommand | None:
        roots = {
            item.report_id
            for item in history
            if item.report_id is not None and _kind(item, "report")
        }
        one_hop = {
            item.upstream_report_id
            for item in history
            if (
                _kind(item, "trace")
                and item.report_id in roots
                and item.upstream_report_id is not None
            )
        }
        visible = roots | one_hop
        traced = {
            item.report_id
            for item in history
            if _kind(item, "trace") and item.report_id is not None
        }
        candidate = next(
            (report_id for report_id in sorted(visible) if report_id not in traced), None
        )
        if candidate is None:
            return None
        return self._command(
            observation,
            "trace",
            discriminator=f"report-{candidate}",
            report_id=candidate,
        )

    def _command(self, observation: PublicWorldView, kind: str, *, discriminator: str, **values):
        identity = re.sub(r"[^A-Za-z0-9._:-]+", "-", discriminator).strip("-.:_") or "action"
        prefix = f"inv-{kind}-{observation.turn}-"
        digest = hashlib.sha256(discriminator.encode("utf-8")).hexdigest()[:10]
        room = 64 - len(prefix) - len(digest) - 1
        command_id = f"{prefix}{identity[:room]}-{digest}"
        return TradeCommand(kind, command_id=command_id, **values)


def _ordered(evidence: tuple[TradeEvidence, ...]) -> tuple[TradeEvidence, ...]:
    return tuple(sorted(evidence, key=lambda item: (item.turn, item.sequence)))


def _kind(item: TradeEvidence, kind: str) -> bool:
    return item.kind == kind


def _latest_repair_complete(evidence: tuple[TradeEvidence, ...]) -> TradeEvidence | None:
    return next(
        (
            item
            for item in reversed(evidence)
            if _kind(item, "repair_complete") and item.asset_id == _ASSET
        ),
        None,
    )


def _repair_pending(evidence: tuple[TradeEvidence, ...]) -> bool:
    assigned = next(
        (
            item
            for item in reversed(evidence)
            if _kind(item, "repair_assigned") and item.asset_id == _ASSET
        ),
        None,
    )
    completed = _latest_repair_complete(evidence)
    return assigned is not None and (completed is None or _after(assigned, completed))


def _latest_unresolved_failure(evidence: tuple[TradeEvidence, ...]) -> TradeEvidence | None:
    completion = _latest_repair_complete(evidence)
    failure = next(
        (item for item in reversed(evidence) if _kind(item, "failure") and item.asset_id == _ASSET),
        None,
    )
    return (
        failure
        if failure is not None and (completion is None or _after(failure, completion))
        else None
    )


def _finding_after_failure(
    evidence: tuple[TradeEvidence, ...], failure: TradeEvidence
) -> TradeEvidence | None:
    findings = tuple(
        item
        for item in evidence
        if _kind(item, "inspection")
        and item.asset_id == _ASSET
        and item.finding_code in {_MATERIAL_DEFECT, _RESIDUAL_DAMAGE}
        and _after(item, failure)
    )
    if not findings:
        return None
    material = next(
        (item for item in reversed(findings) if item.finding_code == _MATERIAL_DEFECT),
        None,
    )
    return material or findings[-1]


def _known_defective_parts(
    evidence: tuple[TradeEvidence, ...],
) -> set[tuple[str, str | None, str | None]]:
    return {
        _delivery_key(item.batch_id, item.shipment_id, item.contract_id)
        for item in evidence
        if _kind(item, "inspection")
        and item.asset_id == _ASSET
        and item.finding_code == _MATERIAL_DEFECT
        and item.batch_id is not None
    }


def _finding_matches_lot(finding: TradeEvidence, lot: PublicLot) -> bool:
    if lot.batch_id != finding.batch_id:
        return False
    if finding.shipment_id is not None:
        return lot.shipment_id == finding.shipment_id and (
            finding.contract_id is None or lot.contract_id == finding.contract_id
        )
    if finding.contract_id is not None:
        return lot.contract_id == finding.contract_id
    return lot.shipment_id is None and lot.contract_id is None


def _quarantine_shipment(
    finding: TradeEvidence,
    affected: tuple[PublicLot, ...],
    parts: tuple[PublicLot, ...],
) -> str | None | object:
    if finding.shipment_id is not None:
        return finding.shipment_id
    shipments = {lot.shipment_id for lot in affected if lot.shipment_id is not None}
    if len(shipments) == 1 and all(lot.shipment_id in shipments for lot in affected):
        return next(iter(shipments))
    if shipments:
        return _AMBIGUOUS_SHIPMENT
    affected_deliveries = {_public_delivery_key(lot) for lot in affected}
    has_other_delivery = any(
        lot.batch_id == finding.batch_id and _public_delivery_key(lot) not in affected_deliveries
        for lot in parts
    )
    if has_other_delivery:
        return _AMBIGUOUS_SHIPMENT
    return None


def _lot_is_known_bad(
    lot: PublicLot,
    known_bad: set[tuple[str, str | None, str | None]],
) -> bool:
    for batch_id, shipment_id, contract_id in known_bad:
        if lot.batch_id != batch_id:
            continue
        if shipment_id is not None:
            if lot.shipment_id == shipment_id and (
                contract_id is None or lot.contract_id == contract_id
            ):
                return True
        elif contract_id is not None:
            if lot.contract_id == contract_id:
                return True
        elif lot.shipment_id is None and lot.contract_id is None:
            return True
    return False


def _delivery_seller(
    observation: PublicWorldView,
    batch_id: str,
    shipment_id: str | None,
    contract_id: str | None,
) -> str | None:
    if contract_id is not None:
        contract = next(
            (item for item in observation.contracts if item.contract_id == contract_id), None
        )
        if contract is not None and contract.seller_id:
            return contract.seller_id
    if shipment_id is not None:
        shipment = next(
            (item for item in observation.shipments if item.shipment_id == shipment_id), None
        )
        if shipment is not None and shipment.origin:
            return shipment.origin
    lot = None
    if shipment_id is not None or contract_id is not None:
        lot = next(
            (
                item
                for item in observation.local_lots
                if item.batch_id == batch_id
                and (shipment_id is None or item.shipment_id == shipment_id)
                and (contract_id is None or item.contract_id == contract_id)
            ),
            None,
        )
    if lot is not None:
        seller = lot.seller_world or lot.origin_world
        if seller:
            return seller
    return None


def _assay_is_clean(item: TradeEvidence) -> bool:
    return (
        _kind(item, "assay")
        and type(item.measured_value) is int
        and item.measured_value >= 100
        and item.measured_unit == "percent"
    )


def _assay_is_poor(item: TradeEvidence) -> bool:
    return (
        _kind(item, "assay")
        and type(item.measured_value) is int
        and item.measured_value < 100
        and item.measured_unit == "percent"
    )


def _after(left: TradeEvidence, right: TradeEvidence) -> bool:
    return (left.turn, left.sequence) > (right.turn, right.sequence)


def _stress_passes(
    evidence: tuple[TradeEvidence, ...], repair: TradeEvidence, method: str
) -> tuple[TradeEvidence, ...]:
    return tuple(
        item
        for item in evidence
        if _kind(item, "inspection")
        and item.asset_id == _ASSET
        and item.method == method
        and _after(item, repair)
    )


def _lots_for(lots: tuple[PublicLot, ...], resource: str) -> tuple[PublicLot, ...]:
    return tuple(
        lot
        for lot in lots
        if lot.resource == resource and type(lot.quantity) is int and lot.quantity > 0
    )


def _delivery_key(batch_id: str, shipment_id: str | None, contract_id: str | None):
    return batch_id, shipment_id, contract_id


def _public_delivery_key(lot: PublicLot):
    return _delivery_key(
        lot.batch_id,
        getattr(lot, "shipment_id", None),
        getattr(lot, "contract_id", None),
    )


def _shipment_argument(shipment_id: str | None) -> dict[str, str]:
    return {"shipment_id": shipment_id} if shipment_id is not None else {}


def _contracts_for(observation: PublicWorldView, resource: str):
    return tuple(
        contract
        for contract in observation.contracts
        if contract.buyer_id == observation.world_id and contract.resource == resource
    )


def _pending_purchase(contracts) -> bool:
    return any(contract.status in {"booked", "in_transit"} for contract in contracts)


def _sensor_disagreement(observation: PublicWorldView) -> tuple[str, str, int] | None:
    sensors = observation.oxygen_sensors
    pairs = []
    for left_index, left in enumerate(sensors):
        if left.sampled_turn != observation.turn or not left.source:
            continue
        for right in sensors[left_index + 1 :]:
            if (
                right.sampled_turn != observation.turn
                or not right.source
                or right.source == left.source
                or abs(left.oxygen - right.oxygen) < 10
            ):
                continue
            sources = tuple(sorted((left.source, right.source)))
            pairs.append((sources[0], sources[1], abs(left.oxygen - right.oxygen)))
    if not pairs:
        return None
    calibration_turn = max(
        (item.turn for item in observation.evidence if _kind(item, "calibration")),
        default=-1,
    )
    if calibration_turn >= observation.turn:
        return None
    return min(pairs)


def _calibration_unavailable(evidence: tuple[TradeEvidence, ...]) -> bool:
    rejection = next(
        (
            item
            for item in reversed(evidence)
            if _kind(item, "command_rejected")
            and item.finding_code == "sensor_calibration_unavailable"
        ),
        None,
    )
    if rejection is None:
        return False
    later_calibration = next(
        (item for item in reversed(evidence) if _kind(item, "calibration")),
        None,
    )
    return later_calibration is None or not _after(later_calibration, rejection)


def _repair_not_needed(evidence: tuple[TradeEvidence, ...]) -> bool:
    return any(
        _kind(item, "command_rejected") and item.finding_code == "repair_not_needed"
        for item in evidence
    )


__all__ = ["InvestigationGovernor"]
