"""Command-line composition root for offline missions, logs, and replay."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .application import (
    SIMULATOR_VERSION,
    ControllerMode,
    MissionConfig,
    mission_metadata,
    run_mission,
)
from .persistence import RunLogError, RunLogWriter, read_run_log, render_run_log
from .providers import CallBudget, JevDispatchProvider, OpenRouterCaptainProvider
from .scenarios import ScenarioFamily

LIVE_CONTROLLERS = {ControllerMode.LLM.value, ControllerMode.JEV_LLM.value}
DEFAULT_CAPTAIN_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_JEV_BASE_URL = "https://openrouter.ai/api"


class CLIError(ValueError):
    """A safe, user-facing command-line error."""


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("must be an integer") from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return parse


def _add_config_options(parser: argparse.ArgumentParser, *, rerun: bool = False) -> None:
    default = None if rerun else argparse.SUPPRESS
    parser.add_argument(
        "--scenario",
        choices=[scenario.value for scenario in ScenarioFamily],
        default=default if rerun else ScenarioFamily.NORMAL.value,
        help="Scenario family to run.",
    )
    parser.add_argument(
        "--seed", type=int, default=default if rerun else 0, help="Replayable seed."
    )
    parser.add_argument(
        "--turns",
        type=_bounded_integer(1, 14 * 24),
        default=default if rerun else 14 * 24,
        help="Mission duration in turns (default: 336).",
    )
    parser.add_argument(
        "--controller",
        choices=[mode.value for mode in ControllerMode],
        default=default if rerun else ControllerMode.RULES.value,
        help="rules is offline and is the default; model modes need a key, model, and budget.",
    )
    parser.add_argument(
        "--evidence-access",
        choices=("latest", "history"),
        default=default if rerun else "latest",
        help="Evidence available to the captain.",
    )
    parser.add_argument(
        "--inspection-budget",
        type=_bounded_integer(0, 6),
        default=default if rerun else 1,
        help="Inspection actions available to the captain per turn.",
    )
    parser.add_argument(
        "--escalation-threshold",
        type=_bounded_integer(0, 100),
        default=default if rerun else 55,
        help="Dispatch urgency threshold from 0 to 100.",
    )
    parser.add_argument("--output", type=Path, required=rerun, help="Destination JSONL file.")
    parser.add_argument(
        "--max-calls",
        type=_positive_integer,
        help="Required finite total model-call budget for live controllers.",
    )
    parser.add_argument(
        "--max-output-tokens-per-call",
        type=_positive_integer,
        help="Required captain response limit for live controllers.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="station-control",
        description=(
            "Run a deterministic station mission, save JSONL, and replay the evidence chain."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="Run a new mission (rules/offline by default).")
    _add_config_options(run_parser)

    replay_parser = commands.add_parser("replay", help="Validate and display a saved mission log.")
    replay_parser.add_argument("log", type=Path)

    rerun_parser = commands.add_parser("rerun", help="Run a saved scenario again into a new log.")
    rerun_parser.add_argument("log", type=Path)
    _add_config_options(rerun_parser, rerun=True)
    return parser


def _read_live_settings(arguments: argparse.Namespace, controller: str) -> dict[str, Any]:
    if controller not in LIVE_CONTROLLERS:
        return {"budget": None, "models": {"captain": None, "jev": None}}
    if arguments.max_calls is None or arguments.max_output_tokens_per_call is None:
        raise CLIError(
            "Live controllers require a finite call budget: set --max-calls and "
            "--max-output-tokens-per-call."
        )
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise CLIError("OPENROUTER_API_KEY is required for live model controllers.")
    captain_model = os.environ.get("CAPTAIN_MODEL", "").strip()
    if not captain_model:
        raise CLIError("CAPTAIN_MODEL is required for live model controllers.")
    jev_model = os.environ.get("JEV_MODEL", "").strip()
    if controller == ControllerMode.JEV_LLM.value and not jev_model:
        raise CLIError("JEV_MODEL is required for the jev+llm controller.")
    return {
        "api_key": api_key,
        "budget": {
            "max_calls": arguments.max_calls,
            "max_output_tokens_per_call": arguments.max_output_tokens_per_call,
        },
        "models": {"captain": captain_model, "jev": jev_model or None},
    }


def _make_providers(controller: str, settings: dict[str, Any]):
    if controller not in LIVE_CONTROLLERS:
        return None, None
    budget = CallBudget(**settings["budget"])
    api_key = settings["api_key"]
    captain = OpenRouterCaptainProvider(
        api_key=api_key,
        model=settings["models"]["captain"],
        budget=budget,
        base_url=os.environ.get("CAPTAIN_BASE_URL", DEFAULT_CAPTAIN_BASE_URL),
        timeout=30.0,
    )
    try:
        dispatcher = None
        if controller == ControllerMode.JEV_LLM.value:
            dispatcher = JevDispatchProvider(
                api_key=api_key,
                model=settings["models"]["jev"],
                budget=budget,
                base_url=os.environ.get("JEV_BASE_URL", DEFAULT_JEV_BASE_URL),
                timeout=30.0,
            )
    except Exception:
        try:
            captain.close()
        except Exception:
            pass
        raise
    return captain, dispatcher


def _metadata(config: MissionConfig, settings: dict[str, Any]) -> dict[str, Any]:
    result = mission_metadata(config)
    result["models"] = settings["models"]
    result["call_budget"] = settings["budget"]
    result["provider_configuration"] = {
        "captain_base_url": os.environ.get("CAPTAIN_BASE_URL", DEFAULT_CAPTAIN_BASE_URL),
        "jev_base_url": os.environ.get("JEV_BASE_URL", DEFAULT_JEV_BASE_URL),
        "provider": "openrouter",
    }
    result["instructions"] = {"captain_version": config.instruction_version}
    result["rubrics"] = {
        "jev_question_version": config.question_version,
        "jev_rubric_version": config.rubric_version,
    }
    return result


def _run_config(
    arguments: argparse.Namespace, saved: dict[str, Any] | None = None
) -> MissionConfig:
    defaults = saved or {}
    controller = getattr(arguments, "controller", None)
    if controller is None:
        old_controller = defaults.get("controller", ControllerMode.RULES.value)
        # A rerun never silently repeats a paid controller configuration.
        controller = (
            ControllerMode.RULES.value if old_controller in LIVE_CONTROLLERS else old_controller
        )
    values = {
        "scenario": getattr(arguments, "scenario", None) or defaults.get("scenario", "normal"),
        "seed": getattr(arguments, "seed", None)
        if getattr(arguments, "seed", None) is not None
        else defaults.get("seed", 0),
        "duration_turns": getattr(arguments, "turns", None)
        if getattr(arguments, "turns", None) is not None
        else defaults.get("duration_turns", 14 * 24),
        "controller_mode": controller,
        "evidence_access": getattr(arguments, "evidence_access", None)
        or defaults.get("evidence_access", "latest"),
        "inspection_budget_per_turn": getattr(arguments, "inspection_budget", None)
        if getattr(arguments, "inspection_budget", None) is not None
        else defaults.get("inspection_budget_per_turn", 1),
        "escalation_threshold": getattr(arguments, "escalation_threshold", None)
        if getattr(arguments, "escalation_threshold", None) is not None
        else defaults.get("escalation_threshold", 55),
    }
    run_id = str(uuid4())
    return MissionConfig(**values, run_id=run_id)


def _evaluation_metrics(result: Any) -> dict[str, Any]:
    evaluation = result.evaluation
    return {
        "measures": evaluation.metrics,
        "unavailable": list(evaluation.unavailable),
    }


def _close_providers(captain: Any, dispatcher: Any) -> None:
    for provider in (dispatcher, captain):
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass


def _run(arguments: argparse.Namespace, config: MissionConfig) -> int:
    settings = _read_live_settings(arguments, config.controller_mode.value)
    run_id = config.run_id
    assert run_id is not None
    output = arguments.output or Path("runs") / f"{run_id}.jsonl"
    writer = RunLogWriter(
        output,
        _metadata(config, settings),
        run_id=run_id,
        simulator_version=SIMULATOR_VERSION,
    )
    captain = dispatcher = None
    try:
        # The destination is exclusively reserved before live providers are composed.
        captain, dispatcher = _make_providers(config.controller_mode.value, settings)
        result = run_mission(
            config,
            captain=captain,
            dispatcher=dispatcher,
            event_sink=writer.write_event,
        )
        snapshots = [snapshot for snapshot in result.debug_snapshots]
        status = getattr(result.status, "value", result.status)
        turns_completed = _turns_completed(result)
        writer.finish(
            status=str(status),
            metrics=_evaluation_metrics(result),
            debug_snapshots=snapshots,
            turns_completed=turns_completed,
        )
    except RunLogError:
        writer.close_incomplete()
        raise
    except Exception as error:
        safe_code = getattr(getattr(error, "code", None), "value", None)
        failure = (
            {"error": safe_code} if isinstance(safe_code, str) else {"error": "mission_failed"}
        )
        try:
            writer.finish(status="failed", metrics=failure)
        except RunLogError:
            writer.close_incomplete()
        raise CLIError(
            "Mission could not complete; the output path records the failed run."
        ) from None
    finally:
        _close_providers(captain, dispatcher)

    print(f"Saved run: {output}")
    print(
        f"Scenario: {config.scenario.value}; seed: {config.seed}; "
        f"controller: {config.controller_mode.value}"
    )
    turns_completed = _turns_completed(result)
    print(
        f"Turns completed: {turns_completed}; "
        f"status: {getattr(result.status, 'value', result.status)}"
    )
    print(f"Metrics: {json.dumps(_evaluation_metrics(result), sort_keys=True)}")
    return 0


def _turns_completed(result: Any) -> int:
    completed = getattr(result, "turns_completed", None)
    if type(completed) is int:
        return completed
    for record in reversed(result.records):
        if record.get("record_type") == "run_end" and type(record.get("turn")) is int:
            return record["turn"]
    if result.debug_snapshots:
        return max(snapshot.turn for snapshot in result.debug_snapshots)
    return 0


def _rerun(arguments: argparse.Namespace) -> int:
    records = read_run_log(arguments.log)
    saved_version = records[0].get("simulator_version")
    if saved_version != SIMULATOR_VERSION:
        raise RunLogError(
            f"Cannot rerun simulator version {saved_version!r} with current version "
            f"{SIMULATOR_VERSION}; replay remains available."
        )
    source_metadata = records[0].get("metadata")
    if not isinstance(source_metadata, dict):
        raise RunLogError("Run log metadata is missing or invalid")
    config = _run_config(arguments, source_metadata)
    return _run(arguments, config)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "replay":
            print(render_run_log(read_run_log(arguments.log)))
            return 0
        if arguments.command == "rerun":
            return _rerun(arguments)
        config = _run_config(arguments)
        return _run(arguments, config)
    except (CLIError, RunLogError, ValueError) as error:
        print(f"station-control: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
