"""Public governor contracts and deterministic policies with independent contexts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .trade import PublicWorldView


@dataclass(frozen=True, slots=True)
class TradeCommand:
    kind: str
    command_id: str = ""
    buyer_id: str = "station"
    seller_id: str = "industrial"
    batch_id: str | None = None
    quantity: int = 1
    method: str = "routine"
    repair_mode: str = "full"
    report_id: str | None = None
    operating_load: str = "routine"
    shipment_id: str | None = None


class GovernorPolicy(Protocol):
    """One independent governor receives only its local public observation."""

    def decide(self, observation: PublicWorldView) -> TradeCommand | None: ...


@dataclass(frozen=True, slots=True)
class ScriptedGovernor:
    """Author a deterministic sequence without giving the policy private state."""

    commands: tuple[tuple[int, TradeCommand], ...] = ()

    def decide(self, observation: PublicWorldView) -> TradeCommand | None:
        return next((command for turn, command in self.commands if turn == observation.turn), None)


class RecoveryGovernor:
    """Follow the authored story using deliveries and findings, never hidden defects."""

    def decide(self, observation: PublicWorldView) -> TradeCommand | None:
        kinds = {item.kind for item in observation.evidence}
        lots = {}
        for lot in observation.local_lots:
            if lot.resource == "parts":
                lots[lot.batch_id] = lots.get(lot.batch_id, 0) + lot.quantity
        if "purchase" not in kinds:
            available = tuple(
                offer
                for offer in observation.offers
                if offer.quantity >= 2
                and offer.resource == "parts"
                and offer.seller_world != observation.world_id
            )
            if not available:
                return None
            offer = min(available, key=lambda item: (item.unit_price, item.batch_id))
            return TradeCommand(
                "purchase",
                "initial-parts",
                seller_id=offer.seller_world,
                batch_id=offer.batch_id,
                quantity=2,
            )
        if "repair_assigned" not in kinds:
            available = next(
                (lot for lot in observation.local_lots if lot.quantity and lot.resource == "parts"),
                None,
            )
            if available:
                return TradeCommand("repair", batch_id=available.batch_id)
        if "failure" in kinds and "inspection" not in kinds:
            return TradeCommand("inspect")
        finding = next(
            (item for item in reversed(observation.evidence) if item.kind == "inspection"), None
        )
        if finding is None:
            return None
        if "quarantine" not in kinds and lots.get(finding.batch_id, 0):
            return TradeCommand(
                "quarantine", batch_id=finding.batch_id, quantity=lots[finding.batch_id]
            )
        replacement = next(
            (
                contract
                for contract in observation.contracts
                if contract.batch_id != finding.batch_id and contract.resource == "parts"
            ),
            None,
        )
        if replacement is None:
            offer = next(
                (
                    item
                    for item in observation.offers
                    if item.batch_id != finding.batch_id
                    and item.quantity
                    and item.resource == "parts"
                    and item.seller_world != observation.world_id
                ),
                None,
            )
            if offer:
                return TradeCommand(
                    "purchase",
                    "replacement-parts",
                    seller_id=offer.seller_world,
                    batch_id=offer.batch_id,
                )
        elif lots.get(replacement.batch_id, 0):
            return TradeCommand("repair", batch_id=replacement.batch_id)
        return None
