"""Offline trade missions: policies propose, the domain owns every transition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

from .governors import GovernorPolicy, TradeCommand
from .scenarios import create_world
from .trade import (
    TradeEvidence,
    WorldAdvanceResult,
    WorldState,
    advance_world,
    inspect_installed_batch,
    observe_world,
    purchase_parts,
    quarantine_batch,
    repair_with_batch,
)


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    turn: int
    world_id: str
    command: TradeCommand | None
    accepted: bool
    rejection: str | None = None


@dataclass(frozen=True, slots=True)
class TradeMissionResult:
    state: WorldState
    events: tuple[TradeEvidence, ...]
    decisions: tuple[GovernorDecision, ...]


def run_trade_mission(
    state: WorldState,
    governors: tuple[tuple[str, GovernorPolicy], ...],
    *,
    turns: int = 20,
    advance: Callable[[WorldState], WorldAdvanceResult] = advance_world,
) -> TradeMissionResult:
    """Advance time, observe all governors, then resolve proposals in world-ID order."""
    if type(turns) is not int or not 1 <= turns <= 336:
        raise ValueError("Trade mission duration must be between 1 and 336 turns")
    identities = [world_id for world_id, _ in governors]
    if len(identities) != len(set(identities)):
        raise ValueError("Each world must have one governor")
    if len({id(policy) for _, policy in governors}) != len(governors):
        raise ValueError("Each world must have an independent governor instance")
    events = []
    decisions = []
    for _ in range(turns):
        advanced = advance(state)
        state = advanced.state
        events.extend(advanced.evidence)
        if not state.station.crew_alive:
            break
        # Materialize every observation before any proposal is applied.
        snapshots = [
            (world_id, policy, observe_world(state, world_id))
            for world_id, policy in sorted(governors, key=lambda item: item[0])
        ]
        proposals = []
        for world_id, policy, snapshot in snapshots:
            try:
                proposals.append((world_id, policy.decide(snapshot)))
            except Exception:
                decisions.append(
                    GovernorDecision(
                        state.station.turn, world_id, None, False, "governor_unavailable"
                    )
                )
        for world_id, command in proposals:
            if command is None:
                continue
            if not isinstance(command, TradeCommand):
                decisions.append(
                    GovernorDecision(state.station.turn, world_id, None, False, "invalid_command")
                )
                continue
            if command.buyer_id != world_id or (
                command.kind != "purchase" and world_id != "station"
            ):
                decisions.append(
                    GovernorDecision(
                        state.station.turn, world_id, command, False, "unauthorized_world"
                    )
                )
                continue
            if command.kind == "purchase":
                result = purchase_parts(
                    state,
                    command_id=command.command_id,
                    quantity=command.quantity,
                    batch_id=command.batch_id or "industrial-batch-a",
                    buyer_id=world_id,
                    seller_id=command.seller_id,
                )
            elif command.kind == "repair":
                result = repair_with_batch(state, command.batch_id)
            elif command.kind == "inspect":
                result = inspect_installed_batch(state)
            elif command.kind == "quarantine":
                result = quarantine_batch(state, command.batch_id, quantity=command.quantity)
            else:
                decisions.append(
                    GovernorDecision(
                        state.station.turn, world_id, command, False, "invalid_command"
                    )
                )
                continue
            state = result.state
            events.extend(result.evidence)
            decisions.append(
                GovernorDecision(
                    state.station.turn, world_id, command, result.accepted, result.rejection
                )
            )
    return TradeMissionResult(state, tuple(events), tuple(decisions))


def run_trade_story(*, turns: int = 20) -> TradeMissionResult:
    from .facade import SimulationFacade
    from .trade import PartLot

    station = replace(create_world("normal", 0), parts=0, leak_active=True, scheduled_events=())
    simulation = SimulationFacade(station)
    simulation.create_world(
        "industrial",
        credits=1000,
        lots=(
            PartLot(
                "industrial-batch-a", 2, 15, "industrial", latent_defect=True, defect_after_turns=3
            ),
            PartLot("industrial-batch-b", 2, 30, "industrial"),
        ),
    )
    simulation.create_decision_system("station", kind="recovery")
    simulation.create_decision_system("industrial")
    return simulation.run(turns=turns)


def trade_story_summary(result: TradeMissionResult) -> dict[str, object]:
    return {
        "controller": "scripted",
        "model_calls": 0,
        "turns_completed": result.state.station.turn,
        "crew_alive": result.state.station.crew_alive,
        "repairs_completed": result.state.station.repairs_completed,
        "leak_active": result.state.station.leak_active,
        "events": [asdict(event) for event in result.events],
        "decisions": [asdict(decision) for decision in result.decisions],
    }
