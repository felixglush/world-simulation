"""Lookups and attestations: information tools whose answers come from the world, not the model."""

from __future__ import annotations

from ..trade_types import PublicWorldView, TradeEvidenceKind
from .gate import Observation
from .memory import MemoryStream

MAX_LOOKUPS_PER_TICK = 2
ATTESTATION_KINDS = ("sale", "no_certificate", "recall")
LOOKUP_TOOLS = ("verify_sender", "recall")


def verify_sender(registry: frozenset[str], identity: str, tick: int) -> Observation:
    """Registry lookup: is this identity a listed party? A listed channel can still be hijacked."""
    if identity in registry:
        text = (
            f"{identity} is a registered party. A registered channel can still carry false claims."
        )
        return Observation("sender_registered", text, turn=tick)
    return Observation(
        "sender_unregistered", f"{identity} is not in the registry of known parties.", turn=tick
    )


def recall(memory: MemoryStream, query: str, tick: int) -> Observation:
    found = memory.retrieve(query, tick, limit=5)
    body = " | ".join(f"{r.record_id}@{r.tick}: {r.text}" for r in found) or "nothing found"
    return Observation("recall_result", f"Memory for '{query[:80]}': {body}", turn=tick)


def build_attestation(kind: str, batch_id: str, view: PublicWorldView) -> str | None:
    """A factual statement built from the attester's own public view; None if it cannot be made.

    The attester cannot add claims: every fact comes from its contracts, shipments, or evidence."""
    sold = [
        c
        for c in view.contracts
        if c.batch_id == batch_id and c.seller_id == view.world_id and c.resource == "parts"
    ]
    if not sold:
        return None
    if kind == "sale":
        parts = []
        for contract in sold:
            shipment = next(
                (s for s in view.shipments if s.contract_id == contract.contract_id), None
            )
            state = f", shipment {shipment.status}" if shipment else ""
            parts.append(
                f"{contract.quantity} of {batch_id} to {contract.buyer_id} under "
                f"{contract.contract_id} ({contract.status}{state})"
            )
        return "[ATTESTED] " + view.world_id + " sold " + "; ".join(parts) + "."
    if kind == "no_certificate":
        claims = sorted(
            {
                f"{e.report_id} from {e.source_id}"
                for e in view.evidence
                if e.kind is TradeEvidenceKind.REPORT and e.batch_id == batch_id and e.report_id
            }
        )
        seen = "; ".join(claims) if claims else "none"
        return (
            f"[ATTESTED] {view.world_id} issued no qualification certificate for {batch_id}. "
            f"Claims about it visible to me: {seen}."
        )
    if kind == "recall":
        return (
            f"[ATTESTED] Recall notice: {view.world_id} recalls {batch_id}; "
            "quarantine unused stock."
        )
    return None
