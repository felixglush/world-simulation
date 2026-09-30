"""Deterministic captain strategy for offline comparison missions."""

from .controllers import (
    ActionRequest,
    ActionRequestKind,
    CaptainContext,
    CaptainDecision,
    PublicEvidence,
    StationView,
)


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
        message = recent.message.lower()
        if "active oxygen leak" in message:
            return ActionRequest(ActionRequestKind.ASSIGN_REPAIR, target="oxygen_system")
        if "calibration fault" in message:
            return ActionRequest(
                ActionRequestKind.DEFER,
                follow_up_turn=min(station.turn + 4, duration),
            )
        if "within calibration range" in message or "operating normally" in message:
            return ActionRequest(
                ActionRequestKind.CLOSE,
                reason="inspection produced supporting evidence",
                evidence_sequences=(recent.sequence,),
            )
    if recent.kind == "action" and "repair assigned" in recent.message.lower():
        follow_up = min(station.turn + 1, duration)
        if follow_up > station.turn:
            return ActionRequest(ActionRequestKind.DEFER, follow_up_turn=follow_up)
    combined = " ".join(item.message.lower() for item in evidence)
    if ("critical" in combined or "exhausted" in combined) and station.backup_oxygen > 0:
        if not any("backup oxygen activated" in item.message.lower() for item in evidence):
            return ActionRequest(ActionRequestKind.ACTIVATE_BACKUP)
    if (
        len(station.oxygen_sensors) == 2
        and station.oxygen_sensors[0].oxygen != station.oxygen_sensors[1].oxygen
    ):
        sensor = min(station.oxygen_sensors, key=lambda reading: reading.oxygen).sensor
        return ActionRequest(ActionRequestKind.INSPECT, target=sensor)
    return ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system")
