"""A farmer is a society Agent whose reply is a list of farm actions and messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from station_control.society.agents import Agent, parse_plan
from station_control.society.gate import Plan

from .world import ACTION_PARAMS

MAX_ACTIONS = 6
MAX_DAYS = 730


@dataclass(frozen=True, slots=True)
class FarmDecision:
    actions: tuple[tuple[str, dict[str, Any]], ...] = ()
    messages: tuple[tuple[str, str, tuple[str, ...], bool], ...] = ()
    rationale: str = ""


class FarmerAgent(Agent):
    def _parse(self, data: dict[str, object], tick: int) -> tuple[FarmDecision, Plan | None]:
        actions = []
        for item in data.get("actions") if isinstance(data.get("actions"), list) else []:
            if not isinstance(item, dict) or item.get("name") not in ACTION_PARAMS:
                raise ValueError("unlisted action")
            # A flat schema invites unrelated fields; keep only the ones this action takes.
            allowed = ACTION_PARAMS[item["name"]]
            actions.append((item["name"], {k: v for k, v in item.items() if k in allowed}))
        messages = []
        for item in data.get("messages") if isinstance(data.get("messages"), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("invalid message")
            invite = item.get("invite")
            invited = tuple(str(i) for i in invite[:11]) if isinstance(invite, list) else ()
            messages.append(
                (str(item.get("to", "")), item["text"], invited, item.get("leave") is True)
            )
        plan = parse_plan(data.get("plan"), data.get("expect"), tick, horizon=MAX_DAYS)
        decision = FarmDecision(
            tuple(actions[:MAX_ACTIONS]), tuple(messages[:2]), str(data.get("rationale", ""))[:300]
        )
        return decision, plan
