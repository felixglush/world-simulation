"""`python -m farm_world`: run the farmers live on a finite model budget."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Sequence

from station_control.provider_support import CallBudget
from station_control.society.llm import LUNA, TERRA, OpenRouterChat

from .layout import START_DAY, default_world
from .loop import FarmResult, merged_log, run_farm

DEFAULT_MODELS = {"react": LUNA, "score": LUNA, "farmer": TERRA, "reflect": TERRA}


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="farm-world")
    parser.add_argument("--days", type=_positive, default=60)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--start-day", type=int, default=START_DAY)
    parser.add_argument("--max-calls", type=_positive, required=True, help="Finite model budget.")
    parser.add_argument("--max-output-tokens-per-call", type=_positive, default=1200)
    parser.add_argument("--reflection-threshold", type=_positive, default=50)
    parser.add_argument("--output", type=Path, help="Write the event log here as JSONL.")
    for role, model in DEFAULT_MODELS.items():
        parser.add_argument(f"--{role}-model", metavar="MODEL", help=f"Default {model}.")
    return parser


def summarize(
    result: FarmResult, models: dict[str, str], budget: dict[str, int]
) -> dict[str, object]:
    world = result.world
    actions = Counter(e["name"] for e in result.events if e["type"] == "action" and e["ok"])
    rejected = Counter(e["name"] for e in result.events if e["type"] == "action" and not e["ok"])
    farmers = {}
    for farmer in world.farmers.values():
        plot = world.plots[farmer.plot_id]
        farmers[farmer.id] = {
            "plot_stage": plot.crop.stage if plot.crop else "empty",
            "health": round(plot.crop.health, 2) if plot.crop else None,
            "inventory": {k: round(v) for k, v in sorted(farmer.inventory.items()) if v},
            "hunger_days": farmer.hunger_days,
        }
    gate_changes = Counter(
        e["farmer"] for e in world.events if e["type"] == "action" and e["action"] == "set_gate"
    )
    cross = sum(
        1
        for m in result.messages
        if world.farmers[m.sender].community != world.farmers[m.recipient].community
    )
    cost = sum(float(c.get("cost") or 0) for c in result.calls)
    return {
        "day": world.day,
        "models": models,
        "budget": budget,
        "calls": len(result.calls),
        "cost": round(cost, 4),
        "farmers": farmers,
        "actions": dict(actions),
        "rejected_actions": dict(rejected),
        "messages": len(result.messages),
        "cross_community_messages": cross,
        "gate_changes_by_farmer": dict(gate_changes),
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.output is not None and arguments.output.exists():
        print(f"farm-world: refusing to overwrite {arguments.output}")
        return 2
    models = {
        role: getattr(arguments, f"{role}_model") or default
        for role, default in DEFAULT_MODELS.items()
    }
    budget = CallBudget(arguments.max_calls, arguments.max_output_tokens_per_call)
    chat = OpenRouterChat(models, budget)
    try:
        world = default_world(arguments.seed, arguments.start_day)
        result = run_farm(
            world, chat, days=arguments.days, reflection_threshold=arguments.reflection_threshold
        )
    finally:
        chat.close()
    summary = summarize(
        result,
        models,
        {
            "max_calls": arguments.max_calls,
            "max_output_tokens_per_call": arguments.max_output_tokens_per_call,
        },
    )
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with arguments.output.open("x", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"type": "run", "summary": summary, "start_day": arguments.start_day})
                + "\n"
            )
            for event in merged_log(result, arguments.start_day):
                handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0
