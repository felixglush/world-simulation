"""The farm loop: every farmer perceives each day, thinks when the gate opens, then acts."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
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
            score_messages=False,
            conversation=True,
        )
        for farmer_id in sorted(world.farmers)
    }


HOURS = 10
MAX_THINKS_PER_DAY = 4  # model-driven reviews a farmer gets each day; messages wait for the next


def run_farm(
    world: FarmWorld,
    chat: ChatModel,
    *,
    days: int,
    reflection_threshold: int = REFLECTION_THRESHOLD,
    sink: Callable[[dict[str, Any]], None] | None = None,
) -> FarmResult:
    """Each day has ten working hours. A farmer's decision is a queue of work that runs hour by
    hour; they are asked again when the queue is empty or a message reaches them, and a
    message arrives in the hour it is sent. `sink` receives every log row as it happens: a "start"
    snapshot, each hour's events, and the day snapshot when the day ends."""
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"A farm run lasts between 1 and {MAX_DAYS} days")
    rec = Recorder(chat)
    agents = make_agents(world, reflection_threshold)
    router = FarmRouter({f.id: f.community for f in world.farmers.values()})
    perception = {farmer_id: Perception() for farmer_id in agents}
    queue: dict[str, list[tuple[str, dict[str, Any]]]] = {f: [] for f in agents}
    busy = {f: 0 for f in agents}
    held: dict[str, list[Any]] = {f: [] for f in agents}  # delivered but not yet read
    start = world.day
    flushed = [0, 0]  # rows already sent to the sink: agent events, world events

    def flush() -> None:
        if sink is None:
            return
        for row in _rows(rec.events[flushed[0] :], world.events[flushed[1] :], start):
            sink(row)
        flushed[:] = [len(rec.events), len(world.events)]

    if sink is not None:
        sink({**world.snapshot(), "type": "start", "source": "world"})
    for _ in range(days):
        tick = world.day - start
        rested: set[str] = set()  # asked and had nothing to do: not asked again today
        thinks = {f: 0 for f in agents}
        for hour in range(HOURS):
            world.hour = rec.hour = hour
            inboxes = router.deliver_due(tick)
            for message in sum(inboxes.values(), []):
                rec.emit(
                    tick,
                    message.sender,
                    "message_delivered",
                    id=message.message_id,
                    to=message.recipient,
                )
            for farmer_id in _order(agents, tick + hour):
                held[farmer_id].extend(inboxes.get(farmer_id, ()))
                can_think = thinks[farmer_id] < MAX_THINKS_PER_DAY
                woken = bool(held[farmer_id]) and can_think
                inbox = tuple(held[farmer_id]) if woken else ()
                if busy[farmer_id] > 0 and not woken:
                    busy[farmer_id] -= 1
                    continue
                if woken or (can_think and not queue[farmer_id] and farmer_id not in rested):
                    force = None if queue[farmer_id] else "idle"
                    thinks[farmer_id] += 1
                    held[farmer_id] = []
                    pause = busy[farmer_id]
                    decision = _think(
                        world, router, agents, perception, queue, farmer_id, tick, inbox, force, rec
                    )
                    if decision is not None:
                        queue[farmer_id] = list(decision.actions)
                        _speak(router, agents[farmer_id], farmer_id, decision, tick, rec)
                        busy[farmer_id] = 0
                    if not queue[farmer_id]:
                        rested.add(farmer_id)
                    elif decision is None and pause > 0:
                        busy[farmer_id] = pause - 1
                        continue
                busy[farmer_id] = _work(world, agents[farmer_id], farmer_id, queue, tick, rec)
            flush()
        busy = {f: 0 for f in agents}
        rec.hour = None
        world.end_day()
        flush()
    return FarmResult(
        world,
        tuple(rec.events),
        tuple(rec.calls),
        router.sent,
    )


def _think(
    world: FarmWorld,
    router: FarmRouter,
    agents: dict[str, FarmerAgent],
    perception: dict[str, Perception],
    queue: dict[str, list[tuple[str, dict[str, Any]]]],
    farmer_id: str,
    tick: int,
    inbox: tuple[Any, ...],
    force: str | None,
    rec: Recorder,
) -> FarmDecision | None:
    seen = world.view(farmer_id)
    view = perception[farmer_id].view(tick, seen, inbox)
    seen["directory"] = router.directory(farmer_id)
    seen["channels"] = router.channels_for(farmer_id)
    seen["tick"] = tick
    seen["hour"] = world.hour
    seen["queued_actions"] = [name for name, _ in queue[farmer_id]]
    return agents[farmer_id].step(view, seen, rec, force=force)


def _work(
    world: FarmWorld,
    agent: FarmerAgent,
    farmer_id: str,
    queue: dict[str, list[tuple[str, dict[str, Any]]]],
    tick: int,
    rec: Recorder,
) -> int:
    """Run queued actions until one takes time; return the extra hours it keeps the farmer."""
    farmer = world.farmers[farmer_id]
    while queue[farmer_id]:
        name, params = queue[farmer_id].pop(0)
        before = farmer.labor
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
        spent = before - farmer.labor
        if name == "rest" and result.ok:
            return HOURS  # resting holds the farmer until a message wakes them or the day ends
        if result.ok and spent > 0:
            return max(0, math.ceil(spent - 1e-9) - 1)
    return 0


def _order(agents: dict[str, FarmerAgent], tick: int) -> list[str]:
    """Rotate who goes first each day so no farmer always wins a same-day conflict."""
    ids = sorted(agents)
    shift = tick % len(ids)
    return ids[shift:] + ids[:shift]


def _speak(
    router: FarmRouter,
    agent: FarmerAgent,
    farmer_id: str,
    decision: FarmDecision,
    tick: int,
    rec: Recorder,
) -> None:
    rec.emit(tick, farmer_id, "decision", rationale=decision.rationale)
    for to, text, invite, leave in decision.messages:
        message, rejection = router.send(farmer_id, to, text, tick, invite, leave)
        if message is None:
            rec.emit(tick, farmer_id, "message_rejected", reason=rejection, to=to)
            agent.record_result(tick, f"My message to {to} was not delivered: {rejection}.")
            continue
        agent.note_sent(f"to {to}: {message.text}")
        agent.memory.add(
            tick, "message_out", f"I told {to}: {message.text}", 4, source=message.message_id
        )
        rec.emit(tick, farmer_id, "message_sent", id=message.message_id, to=to, text=message.text)


def _rows(
    agent_events: Sequence[dict[str, Any]], world_events: Sequence[dict[str, Any]], start_day: int
) -> list[dict[str, Any]]:
    rows = [{**e, "day": start_day + e["tick"], "source": "agent"} for e in agent_events]
    return rows + [{**e, "source": "world"} for e in world_events]


def merged_log(result: FarmResult, start_day: int) -> list[dict[str, Any]]:
    """One ordered log: agent thinking, then what the world did, then the day's snapshot."""
    rows = _rows(result.events, result.world.events, start_day)

    def when(event: dict[str, Any]) -> tuple[int, int, int]:
        if event["type"] == "day":
            return event["day"], HOURS, 0
        hour = event.get("hour", 0)
        return event["day"], hour, 0 if event["source"] == "agent" else 1

    return sorted(rows, key=when)
