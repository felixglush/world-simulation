"""`society` command: event-gated talking agents, offline by default."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..deception_mission import DECEPTION_STORIES
from ..provider_support import CallBudget
from .llm import DEFAULT_MODELS, FakeChat, OpenRouterChat
from .offline import ScriptedModels
from .story import SocietyConfig, run_story, society_summary

ROLES = tuple(DEFAULT_MODELS)


class SocietyCLIError(ValueError):
    """A safe, user-facing command-line error."""


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _turns(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 336:
        raise argparse.ArgumentTypeError("must be from 1 to 336")
    return parsed


def add_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        "society",
        help="Run a deception story with a captain, a supplier, and an adversary that talk.",
    )
    parser.add_argument("--story", choices=DECEPTION_STORIES, default="supply_chain")
    parser.add_argument("--turns", type=_turns, default=40)
    parser.add_argument("--adversary", choices=("on", "off"), default="on")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call OpenRouter models. Needs --max-calls; OPENROUTER_API_KEY is optional.",
    )
    parser.add_argument("--max-calls", type=_positive, help="Finite shared call budget (live).")
    parser.add_argument("--max-output-tokens-per-call", type=_positive, default=700)
    parser.add_argument("--reflection-threshold", type=_positive, default=50)
    parser.add_argument("--output", type=Path, help="Write the event log here as JSONL.")
    for role, model in DEFAULT_MODELS.items():
        parser.add_argument(
            f"--{role}-model", metavar="MODEL", help=f"Model for the {role} role (default {model})."
        )


def run(arguments: argparse.Namespace) -> int:
    models = {
        role: getattr(arguments, f"{role}_model") or default
        for role, default in DEFAULT_MODELS.items()
    }
    if arguments.output is not None and arguments.output.exists():
        raise SocietyCLIError(f"Refusing to overwrite {arguments.output}")
    chat = None
    budget_info: dict[str, int] = {}
    try:
        if arguments.live:
            if arguments.max_calls is None:
                raise SocietyCLIError("Live agents require a finite budget: set --max-calls.")
            budget = CallBudget(arguments.max_calls, arguments.max_output_tokens_per_call)
            budget_info = {
                "max_calls": arguments.max_calls,
                "max_output_tokens_per_call": arguments.max_output_tokens_per_call,
            }
            chat = OpenRouterChat(models, budget)
        else:
            chat = FakeChat(ScriptedModels())
        outcome = run_story(
            chat,
            SocietyConfig(
                story=arguments.story,
                turns=arguments.turns,
                adversary=arguments.adversary == "on",
                reflection_threshold=arguments.reflection_threshold,
            ),
        )
    finally:
        close = getattr(chat, "close", None)
        if close is not None:
            close()
    summary = society_summary(
        outcome, models if arguments.live else {"offline": "scripted"}, budget_info
    )
    summary["mode"] = "live" if arguments.live else "offline"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with arguments.output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "run", "summary": summary}, sort_keys=True) + "\n")
            for event in outcome["result"].log:
                handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0
