"""Assemble the deception stories with a captain, a supplier governor, and an adversary."""

from __future__ import annotations

from dataclasses import dataclass

from ..deception_mission import create_deception_simulation
from ..trade_types import WorldState
from .agents import Agent, Recorder
from .llm import ChatModel
from .loop import Seat, SocietyResult, run_society
from .messages import MessageRouter
from .metrics import deception_metrics
from .roles import (
    ADVERSARY,
    ADVERSARY_GATE,
    ADVERSARY_ID,
    CAPTAIN,
    CAPTAIN_GATE,
    CAPTAIN_ID,
    SUPPLIER,
    SUPPLIER_GATE,
    SUPPLIER_ID,
    new_stream,
)

ADVERSARY_ALIAS = "contact.anon"
FRONTS = ("industrial.qa-desk", "port.dispatch")
CAPTAIN_SOURCE = "station.captain"
SUPPLIER_SOURCE = "industrial.governor"
# Listed parties. The QA desk front is not listed; the dispatch channel is listed but hijacked.
REGISTRY = frozenset(
    {
        CAPTAIN_SOURCE,
        SUPPLIER_SOURCE,
        "industrial.qa",
        "port.office",
        "maintenance.office",
        "port.dispatch",
    }
)


@dataclass(frozen=True, slots=True)
class SocietyConfig:
    story: str = "supply_chain"
    turns: int = 40
    adversary: bool = True
    score_messages: bool = True
    reflection_threshold: int = 50


def run_story(
    chat: ChatModel, config: SocietyConfig = SocietyConfig(), *, state: WorldState | None = None
) -> dict[str, object]:
    """Run one story and return the result with its deception metrics."""
    state = state or create_deception_simulation(config.story, policy="trust").state
    threshold = config.reflection_threshold
    captain = Agent(
        CAPTAIN_ID,
        CAPTAIN,
        new_stream(CAPTAIN_ID, threshold),
        CAPTAIN_GATE,
        identities=(CAPTAIN_SOURCE,),
        score_messages=config.score_messages,
    )
    supplier = Agent(
        SUPPLIER_ID,
        SUPPLIER,
        new_stream(SUPPLIER_ID, threshold),
        SUPPLIER_GATE,
        identities=(SUPPLIER_SOURCE,),
        score_messages=config.score_messages,
    )
    seats = [
        Seat(captain, "station", CAPTAIN_SOURCE),
        Seat(supplier, "industrial", SUPPLIER_SOURCE),
    ]
    worlds = {CAPTAIN_ID: "station", SUPPLIER_ID: "industrial"}
    identities = {
        CAPTAIN_ID: frozenset({CAPTAIN_SOURCE}),
        SUPPLIER_ID: frozenset({SUPPLIER_SOURCE}),
    }
    mailboxes: dict[str, frozenset[str]] = {}
    agents = {CAPTAIN_ID: captain, SUPPLIER_ID: supplier}
    if config.adversary:
        speak = (ADVERSARY_ALIAS, *FRONTS)
        adversary = Agent(
            ADVERSARY_ID,
            ADVERSARY,
            new_stream(ADVERSARY_ID, threshold),
            ADVERSARY_GATE,
            identities=speak,
            score_messages=config.score_messages,
        )
        seats.append(Seat(adversary, None, ADVERSARY_ALIAS))
        identities[ADVERSARY_ID] = frozenset(speak)
        mailboxes[ADVERSARY_ID] = frozenset(speak)
        agents[ADVERSARY_ID] = adversary
    router = MessageRouter(worlds, identities, mailboxes, REGISTRY)
    recorder = Recorder(chat)
    result = run_society(state, tuple(seats), router, recorder, turns=config.turns)
    adversary_ids = frozenset(identities.get(ADVERSARY_ID, frozenset()))
    return {
        "config": config,
        "result": result,
        "agents": agents,
        "metrics": deception_metrics(result, adversary_ids),
    }


def society_summary(
    outcome: dict[str, object], models: dict[str, str], budget: dict[str, int]
) -> dict[str, object]:
    result: SocietyResult = outcome["result"]  # type: ignore[assignment]
    config: SocietyConfig = outcome["config"]  # type: ignore[assignment]
    calls = result.calls
    by_purpose: dict[str, int] = {}
    for call in calls:
        key = f"{call['agent']}:{call['purpose']}"
        by_purpose[key] = by_purpose.get(key, 0) + 1
    costs = [c["cost_usd"] for c in calls if isinstance(c.get("cost_usd"), (int, float))]
    return {
        "story": config.story,
        "turns_requested": config.turns,
        "turns_completed": result.state.station.turn,
        "crew_alive": result.state.station.crew_alive,
        "adversary": config.adversary,
        "models": models,
        "call_budget": budget,
        "model_calls": len(calls),
        "calls_by_purpose": dict(sorted(by_purpose.items())),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "messages": [
            {
                "id": m.message_id,
                "from": m.identity,
                "to": m.recipient,
                "at": m.sent_at,
                "intent": m.intent,
                "text": m.text,
            }
            for m in result.messages
        ],
        "metrics": outcome["metrics"],
        "memory": {
            agent_id: {
                "records": len(agent.memory.records),
                "reflections": sum(r.kind == "reflection" for r in agent.memory.records),
            }
            for agent_id, agent in outcome["agents"].items()  # type: ignore[union-attr]
        },
    }
