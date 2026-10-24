"""The farm loop: every farmer perceives each day, thinks when the gate opens, then acts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from station_control.society.agents import Recorder
from station_control.society.llm import ChatModel
from station_control.society.roles import new_stream

from .agent import MAX_DAYS, FarmDecision, FarmerAgent
from .perception import Perception
from .roles import FARMER_GATE, farmer_spec
from .router import FarmRouter
from .world import FarmWorld

REFLECTION_THRESHOLD = 50


@dataclass(frozen=True, slots=True)
class FarmResult:
    world: FarmWorld
    events: tuple[dict[str, Any], ...]
    calls: tuple[dict[str, Any], ...]
    messages: tuple[Any, ...]


def make_agents(
    world: FarmWorld, reflection_threshold: int = REFLECTION_THRESHOLD
) -> dict[str, FarmerAgent]:
    return {
        farmer_id: FarmerAgent(
            farmer_id,
            farmer_spec(farmer_id),
            new_stream(farmer_id, reflection_threshold),
            FARMER_GATE,
            identities=(farmer_id,),
            score_messages=True,
        )
        for farmer_id in sorted(world.farmers)
    }


def run_farm(
    world: FarmWorld,
    chat: ChatModel,
    *,
    days: int,
    reflection_threshold: int = REFLECTION_THRESHOLD,
) -> FarmResult:
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"A farm run lasts between 1 and {MAX_DAYS} days")
    rec = Recorder(chat)
    agents = make_agents(world, reflection_threshold)
    router = FarmRouter({f.id: f.community for f in world.farmers.values()})
    perception = {farmer_id: Perception() for farmer_id in agents}
    start = world.day
    for _ in range(days):
        tick = world.day - start
        inboxes = router.deliver_due(tick)
        for message in sum(inboxes.values(), []):
            rec.emit(
                tick,
                message.sender,
                "message_delivered",
                id=message.message_id,
                to=message.recipient,
            )
        proposals: list[tuple[str, FarmDecision | None]] = []
        for farmer_id in _order(agents, tick):
            agent = agents[farmer_id]
            seen = world.view(farmer_id)
            view = perception[farmer_id].view(tick, seen, tuple(inboxes.get(farmer_id, ())))
            seen["directory"] = router.directory(farmer_id)
            seen["tick"] = tick
            proposals.append((farmer_id, agent.step(view, seen, rec)))
        for farmer_id, decision in proposals:  # already in today's rotated order
            if decision is not None:
                _apply(world, router, agents[farmer_id], farmer_id, decision, tick, rec)
        world.end_day()
    return FarmResult(
        world,
        tuple(rec.events),
        tuple(rec.calls),
        router.sent,
    )


def _order(agents: dict[str, FarmerAgent], tick: int) -> list[str]:
    """Rotate who goes first each day so no farmer always wins a same-day conflict."""
    ids = sorted(agents)
    shift = tick % len(ids)
    return ids[shift:] + ids[:shift]


def _apply(
    world: FarmWorld,
    router: FarmRouter,
    agent: FarmerAgent,
    farmer_id: str,
    decision: FarmDecision,
    tick: int,
    rec: Recorder,
) -> None:
    rec.emit(tick, farmer_id, "decision", rationale=decision.rationale)
    for name, params in decision.actions:
        result = world.act(farmer_id, name, **params)
        rec.emit(
            tick,
            farmer_id,
            "action",
            name=name,
            params=params,
            ok=result.ok,
            message=result.message,
        )
        verb = "I did" if result.ok else "I could not do"
        agent.record_result(tick, f"{verb} {name}: {result.message}", 4 if result.ok else 5)
    for to, text in decision.messages:
        message, rejection = router.send(farmer_id, to, text, tick)
        if message is None:
            rec.emit(tick, farmer_id, "message_rejected", reason=rejection, to=to)
            agent.record_result(tick, f"My message to {to} was not delivered: {rejection}.")
            continue
        agent.note_sent(f"to {to}: {message.text}")
        agent.memory.add(
            tick, "message_out", f"I told {to}: {message.text}", 4, source=message.message_id
        )
        rec.emit(tick, farmer_id, "message_sent", id=message.message_id, to=to, text=message.text)


def merged_log(result: FarmResult, start_day: int) -> list[dict[str, Any]]:
    """One ordered log: agent thinking, then what the world did, then the day's snapshot."""
    rows = [{**e, "day": start_day + e["tick"], "source": "agent"} for e in result.events]
    rows += [{**e, "source": "world"} for e in result.world.events]

    def phase(event: dict[str, Any]) -> int:
        if event["source"] == "agent":
            return 0
        return 2 if event["type"] == "day" else 1

    return sorted(rows, key=lambda e: (e["day"], phase(e)))
