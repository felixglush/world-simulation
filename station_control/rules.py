"""Deterministic captain strategy for offline comparison missions."""

from .controllers import (
    ActionRequest,
    ActionRequestKind,
    CaptainContext,
    CaptainDecision,
    PublicEvidence,
    StationView,
)
from .domain import Evidence, EvidenceCode


class RulesCaptain:
    """Choose actions using only caller-supplied public evidence and observations."""

    def __init__(self, duration_turns: int) -> None:
        self._duration_turns = duration_turns

    def decide(self, context: CaptainContext) -> CaptainDecision:
        return CaptainDecision(
            _choose_action(
                context.evidence, context.incident, context.station, self._duration_turns
            ),
            rationale="deterministic rules policy v1",
        )


def _choose_action(
    evidence: tuple[PublicEvidence, ...],
    origin: PublicEvidence,
    station: StationView,
    duration: int,
) -> ActionRequest:
    recent = evidence[-1] if evidence else origin
    if recent.kind == "repair_complete":
        return ActionRequest(
            ActionRequestKind.CLOSE,
            reason="repair completion was recorded",
            evidence_sequences=(recent.sequence,),
        )
    if recent.kind == "inspection":
        if recent.code is EvidenceCode.ACTIVE_LEAK:
            return ActionRequest(ActionRequestKind.ASSIGN_REPAIR, target="oxygen_system")
        if recent.code is EvidenceCode.SENSOR_CALIBRATION_FAULT:
            return ActionRequest(
                ActionRequestKind.DEFER,
                follow_up_turn=min(station.turn + 4, duration),
            )
        if recent.code in {EvidenceCode.SENSOR_HEALTHY, EvidenceCode.OXYGEN_HEALTHY}:
            return ActionRequest(
                ActionRequestKind.CLOSE,
                reason="inspection produced supporting evidence",
                evidence_sequences=(recent.sequence,),
            )
    if recent.kind == "action" and recent.code is EvidenceCode.REPAIR_ASSIGNED:
        follow_up = min(station.turn + 1, duration)
        if follow_up > station.turn:
            return ActionRequest(ActionRequestKind.DEFER, follow_up_turn=follow_up)
    codes = {item.code for item in evidence if isinstance(item, Evidence)}
    if (
        codes & {EvidenceCode.OXYGEN_CRITICAL, EvidenceCode.OXYGEN_EXHAUSTED}
        and station.backup_oxygen > 0
    ):
        if EvidenceCode.BACKUP_ACTIVATED not in codes:
            return ActionRequest(ActionRequestKind.ACTIVATE_BACKUP)
    if (
        len(station.oxygen_sensors) == 2
        and station.oxygen_sensors[0].oxygen != station.oxygen_sensors[1].oxygen
    ):
        sensor = min(station.oxygen_sensors, key=lambda reading: reading.oxygen).sensor
        return ActionRequest(ActionRequestKind.INSPECT, target=sensor)
    return ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system")
