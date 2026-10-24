"""One world, three roles: agents perceive each tick and think only when their gate opens."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace

from ..adversary import (
    DEFAULT_ADVERSARY_DISRUPTIONS,
    AdversaryAction,
    adversary_context,
    apply_adversary_action,
)
from ..trade import WorldAdvanceResult, WorldState, advance_world, observe_world
from ..trade_mission import GovernorDecision, execute_command, rejection_feedback
from ..trade_types import PublicWorldView, TradeEvidence, TradeEvidenceKind
from .agents import Agent, Decision, OutgoingMessage, Recorder
from .gate import AgentView, Observation, public_agent_view
from .messages import Message, MessageRouter
from .tools import build_attestation, recall, verify_sender

ADVERSARY = "adversary"


@dataclass(frozen=True, slots=True)
class Seat:
    """Where an agent lives: a world it governs, or None for the adversary."""

    agent: Agent
    world_id: str | None
    source_id: str


@dataclass(frozen=True, slots=True)
class SocietyResult:
    state: WorldState
    evidence: tuple[TradeEvidence, ...]
    decisions: tuple[GovernorDecision, ...]
    messages: tuple[Message, ...]
    log: tuple[dict[str, object], ...]
    calls: tuple[dict[str, object], ...]
    reliance: tuple[tuple[int, str, tuple[str, ...], tuple[str, ...]], ...]
    disruptions: tuple[tuple[int, str, str | None, bool], ...]


def run_society(
    state: WorldState,
    seats: tuple[Seat, ...],
    router: MessageRouter,
    rec: Recorder,
    *,
    turns: int,
    disruption_budget: int = DEFAULT_ADVERSARY_DISRUPTIONS,
) -> SocietyResult:
    if type(turns) is not int or not 1 <= turns <= 336:
        raise ValueError("Society duration must be between 1 and 336 turns")
    ordered = tuple(sorted(seats, key=lambda seat: seat.agent.agent_id))
    seen: dict[str, set[tuple[object, ...]]] = {s.agent.agent_id: set() for s in ordered}
    evidence: list[TradeEvidence] = []
    decisions: list[GovernorDecision] = []
    reliance: list[tuple[int, str, tuple[str, ...], tuple[str, ...]]] = []
    disruptions: list[tuple[int, str, str | None, bool]] = []
    crew_notes: list[Observation] = []
    answers: dict[str, list[Observation]] = {}
    for _ in range(turns):
        advanced: WorldAdvanceResult = advance_world(state)
        state = advanced.state
        evidence.extend(advanced.evidence)
        if not state.station.crew_alive:
            break
        tick = state.station.turn
        inboxes, delivered = router.deliver_due(tick)
        for message in delivered:
            rec.emit(
                tick,
                message.sender,
                "message_delivered",
                id=message.message_id,
                to=message.recipient,
            )
        notes, crew_notes = tuple(crew_notes), []

        proposals: list[tuple[Seat, Decision | None]] = []
        for seat in ordered:
            agent = seat.agent
            inbox = tuple(inboxes.get(agent.agent_id, ()))
            if seat.world_id is None:
                view = _adversary_view(state, tick, notes, inbox, disruption_budget, router)
                world = _adversary_world(state, disruption_budget, router)
            else:
                public = observe_world(state, seat.world_id)
                view = public_agent_view(public, seen[agent.agent_id], inbox)
                world = _crew_world(public, router)
            waiting = answers.pop(agent.agent_id, [])
            if waiting:
                view = replace(view, items=(*view.items, *waiting))
            proposals.append((seat, agent.step(view, world, rec)))

        for seat, decision in proposals:
            if decision is None:
                continue
            agent = seat.agent
            if seat.world_id is None:
                state, spent = _apply_adversary(
                    state, agent, decision, disruption_budget, disruptions, rec
                )
                disruption_budget -= spent
            else:
                state = _apply_crew(state, seat, decision, decisions, evidence, crew_notes, rec)
                if decision.relied_on or decision.disputes:
                    reliance.append((tick, agent.agent_id, decision.relied_on, decision.disputes))
            _lookup(state, seat, decision, router, answers, rec)
            outgoing = list(decision.messages)
            for attestation in decision.attestations:
                text = (
                    build_attestation(
                        attestation.kind, attestation.batch_id, observe_world(state, seat.world_id)
                    )
                    if seat.world_id
                    else None
                )
                if text is None:
                    rec.emit(
                        tick, agent.agent_id, "attestation_rejected", batch=attestation.batch_id
                    )
                    continue
                outgoing.append(
                    OutgoingMessage(attestation.recipient, seat.source_id, text, "inform")
                )
            for out in outgoing:
                state, message, rejection = router.send(
                    state,
                    agent.agent_id,
                    out.identity or seat.source_id,
                    out.recipient,
                    out.text,
                    out.intent,
                    out.echoes if seat.world_id is None else None,
                )
                if message is None:
                    rec.emit(
                        tick, agent.agent_id, "message_rejected", reason=rejection, to=out.recipient
                    )
                    continue
                agent.note_sent(f"to {message.recipient} as {message.identity}: {message.text}")
                agent.memory.add(
                    tick,
                    "message_out",
                    f"I told {message.recipient} (as {message.identity}): {message.text}",
                    4,
                    source=message.message_id,
                )
                rec.emit(
                    tick,
                    agent.agent_id,
                    "message_sent",
                    id=message.message_id,
                    to=message.recipient,
                    identity=message.identity,
                    intent=message.intent,
                    text=message.text,
                )
                if seat.world_id is not None:
                    crew_notes.append(
                        Observation(
                            "crew_message",
                            f"Crew told {message.recipient}: {message.text}",
                            turn=tick,
                        )
                    )
    return SocietyResult(
        state,
        tuple(evidence),
        tuple(decisions),
        router.sent,
        tuple(rec.events),
        tuple(rec.calls),
        tuple(reliance),
        tuple(disruptions),
    )


def _apply_crew(
    state: WorldState,
    seat: Seat,
    decision: Decision,
    decisions: list[GovernorDecision],
    evidence: list[TradeEvidence],
    crew_notes: list[Observation],
    rec: Recorder,
) -> WorldState:
    command = decision.command
    world_id = seat.world_id
    tick = state.station.turn
    if command is None or world_id is None:
        return state
    if command.buyer_id != world_id or (command.kind != "purchase" and world_id != "station"):
        decisions.append(GovernorDecision(tick, world_id, command, False, "unauthorized_world"))
        return state
    result = execute_command(state, world_id, command)
    if result is None:
        decisions.append(GovernorDecision(tick, world_id, command, False, "invalid_command"))
        return state
    state = result.state
    evidence.extend(result.evidence)
    if not result.accepted:
        state, feedback = rejection_feedback(state, world_id, command, result)
        evidence.extend(feedback)
    decisions.append(GovernorDecision(tick, world_id, command, result.accepted, result.rejection))
    outcome = "accepted" if result.accepted else f"rejected ({result.rejection})"
    seat.agent.record_result(tick, f"I ordered {command.kind}: {outcome}.")
    rec.emit(
        tick,
        seat.agent.agent_id,
        "action",
        command=asdict(command),
        accepted=result.accepted,
        rejection=result.rejection,
    )
    crew_notes.append(Observation("crew_action", f"Crew {command.kind} {outcome}", turn=tick))
    for item in result.evidence:
        if item.kind is TradeEvidenceKind.TRACE and item.report_id:
            crew_notes.append(Observation("crew_traced_report", item.message, item.report_id, tick))
    return state


def _apply_adversary(
    state: WorldState,
    agent: Agent,
    decision: Decision,
    budget: int,
    disruptions: list[tuple[int, str, str | None, bool]],
    rec: Recorder,
) -> tuple[WorldState, int]:
    action = decision.adversary_action or AdversaryAction("wait")
    result = apply_adversary_action(state.station, action, budget)
    tick = state.station.turn
    kind = str(action.kind)
    rec.emit(
        tick,
        agent.agent_id,
        "action",
        action_kind=kind,
        target=action.target,
        accepted=result.accepted,
        rejection=result.rejection,
    )
    outcome = "scheduled" if result.accepted else f"rejected ({result.rejection})"
    agent.record_result(tick, f"I chose {kind}: {outcome}.")
    spent = 0
    if result.accepted:
        state = replace(state, station=result.state)
        if kind != "wait":
            spent = 1
            disruptions.append((tick, kind, action.target, True))
    return state, spent


def _lookup(
    state: WorldState,
    seat: Seat,
    decision: Decision,
    router: MessageRouter,
    answers: dict[str, list[Observation]],
    rec: Recorder,
) -> None:
    """Answer information lookups from the world's registry or the agent's own memory."""
    tick = state.station.turn
    agent = seat.agent
    for item in decision.lookups:
        if item.tool == "verify_sender" and seat.world_id is not None:
            answer = verify_sender(router.registry, item.arg, tick)
        elif item.tool == "recall":
            answer = recall(agent.memory, item.arg, tick)
        else:
            continue
        answers.setdefault(agent.agent_id, []).append(answer)
        rec.emit(tick, agent.agent_id, "lookup", tool=item.tool, arg=item.arg, code=answer.code)


def _crew_world(view: PublicWorldView, router: MessageRouter) -> dict[str, object]:
    return {
        "world_id": view.world_id,
        "turn": view.turn,
        "credits": view.credits,
        "parts": view.parts,
        "oxygen_sensors": [asdict(s) for s in view.oxygen_sensors],
        "backup_oxygen": view.backup_oxygen,
        "backup_active": view.backup_active,
        "available_crew": view.available_crew,
        "repair_turns_remaining": view.repair_turns_remaining,
        "operating_load": view.operating_load,
        "offers": [asdict(x) for x in view.offers],
        "local_lots": [asdict(x) for x in view.local_lots],
        "contracts": [asdict(x) for x in view.contracts],
        "shipments": [asdict(x) for x in view.shipments],
        "evidence": [
            {
                "turn": e.turn,
                "code": e.code,
                "message": e.message,
                "source": e.source_id,
                "report_id": e.report_id,
                "batch_id": e.batch_id,
            }
            for e in view.evidence[-10:]
        ],
        "directory": router.directory_for(view.world_id),
    }


def _adversary_view(
    state: WorldState,
    tick: int,
    notes: tuple[Observation, ...],
    inbox: tuple[Message, ...],
    budget: int,
    router: MessageRouter,
) -> AgentView:
    station = state.station
    signals = {
        "oxygen": station.oxygen,
        "leak_active": int(station.leak_active),
        "repair_turns_remaining": station.repair_turns_remaining,
        "pending_deliveries": len(station.deliveries),
        "disruption_budget": budget,
    }
    return AgentView(tick, signals, notes, inbox)


def _adversary_world(state: WorldState, budget: int, router: MessageRouter) -> dict[str, object]:
    context = adversary_context(state.station, budget)
    data = json.loads(json.dumps(asdict(context), default=str))
    data["directory"] = sorted(router.recipients())
    data["known_reports"] = [
        {"id": r.report_id, "source": r.source_id, "to": r.recipient_world, "text": r.message}
        for r in state.reports
        if r.receipt_turn <= state.station.turn + 1
    ][-12:]
    return data
