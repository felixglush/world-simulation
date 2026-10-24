"""A farmer is a society Agent whose reply is a list of farm actions and messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from station_control.society.agents import Agent, parse_plan
from station_control.society.gate import Plan

from .world import ACTIONS

MAX_ACTIONS = 6
_PARAMS = ("plot", "channel", "open", "kg", "mm", "units", "variety")


@dataclass(frozen=True, slots=True)
class FarmDecision:
    actions: tuple[tuple[str, dict[str, Any]], ...] = ()
    messages: tuple[tuple[str, str], ...] = ()
    rationale: str = ""


class FarmerAgent(Agent):
    def _parse(self, data: dict[str, object], tick: int) -> tuple[FarmDecision, Plan | None]:
        actions = []
        for item in data.get("actions") if isinstance(data.get("actions"), list) else []:
            if not isinstance(item, dict) or item.get("name") not in ACTIONS:
                raise ValueError("unlisted action")
            actions.append((item["name"], {k: item[k] for k in _PARAMS if k in item}))
        messages = []
        for item in data.get("messages") if isinstance(data.get("messages"), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("invalid message")
            messages.append((str(item.get("to", "")), item["text"]))
        plan = parse_plan(data.get("plan"), data.get("expect"), tick)
        decision = FarmDecision(
            tuple(actions[:MAX_ACTIONS]), tuple(messages[:2]), str(data.get("rationale", ""))[:300]
        )
        return decision, plan
