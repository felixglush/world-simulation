"""Command-line composition root for offline missions, logs, and replay."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .adversary import (
    DEFAULT_ADVERSARY_DISRUPTIONS,
    MAX_ADVERSARY_DISRUPTIONS,
    AdversaryMode,
)
from .adversary_provider import DEFAULT_ADVERSARY_BASE_URL, OpenRouterAdversaryProvider
from .application import (
    MAX_INSPECTIONS_PER_TURN,
    MAX_MISSION_TURNS,
    SIMULATOR_VERSION,
    ControllerMode,
    MissionConfig,
    MissionResult,
    mission_metadata,
    run_mission,
)
from .persistence import RunLogError, RunLogWriter, read_run_log, render_run_log
from .providers import (
    DEFAULT_CAPTAIN_BASE_URL,
    DEFAULT_JEV_BASE_URL,
    CallBudget,
    JevDispatchProvider,
    OpenRouterCaptainProvider,
)
from .scenario_library import list_scenarios, load_library_scenario, load_scenario
from .scenarios import ScenarioFamily, compose_scenarios, scenario_definition_from_dict

LIVE_CONTROLLERS = {ControllerMode.LLM.value, ControllerMode.JEV_LLM.value}


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


def _nonblank_model(value: str) -> str:
    parsed = value.strip()
    if not parsed:
        raise argparse.ArgumentTypeError("must not be blank")
    return parsed


def _nonnegative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
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
    defaults = {} if rerun else mission_metadata(MissionConfig())
    scenario_options = parser.add_mutually_exclusive_group()
    scenario_options.add_argument(
        "--scenario",
        action="append",
        default=None,
        metavar="NAME",
        help="Legacy scenario family or YAML scenario-library ID; repeat YAML IDs to compose.",
    )
    scenario_options.add_argument(
        "--scenario-file",
        action="append",
        type=Path,
        default=None,
        metavar="PATH",
        help="Run a validated custom YAML scenario; repeat paths to compose.",
    )
    parser.add_argument(
        "--scenario-spacing",
        type=_nonnegative_integer,
        default=None,
        help="Offset each selected scenario's event turns by this amount (default: 0).",
    )
    parser.add_argument("--seed", type=int, default=defaults.get("seed"), help="Replayable seed.")
    parser.add_argument(
        "--turns",
        type=_bounded_integer(1, MAX_MISSION_TURNS),
        default=defaults.get("duration_turns"),
        help=f"Mission duration in turns (default: {MAX_MISSION_TURNS}).",
    )
    parser.add_argument(
        "--controller",
        choices=[mode.value for mode in ControllerMode],
        default=ControllerMode.LLM.value,
        help="llm is the default; rules is available for explicit offline benchmarks.",
    )
    parser.add_argument(
        "--evidence-access",
        choices=("latest", "history"),
        default=defaults.get("evidence_access"),
        help="Evidence available to the captain.",
    )
    parser.add_argument(
        "--inspection-budget",
        type=_bounded_integer(0, MAX_INSPECTIONS_PER_TURN),
        default=defaults.get("inspection_budget_per_turn"),
        help="Inspection actions available to the captain per turn.",
    )
    parser.add_argument(
        "--escalation-threshold",
        type=_bounded_integer(0, 100),
        default=defaults.get("escalation_threshold"),
        help="Dispatch urgency threshold from 0 to 100.",
    )
    parser.add_argument(
        "--adversary",
        choices=[mode.value for mode in AdversaryMode],
        default=None,
        help="Optional adversary model; off is the default for new runs.",
    )
    parser.add_argument(
        "--adversary-budget",
        type=_bounded_integer(0, MAX_ADVERSARY_DISRUPTIONS),
        default=None,
        help=(
            "Disruption points available to the adversary, from 0 to "
            f"{MAX_ADVERSARY_DISRUPTIONS} (default: {DEFAULT_ADVERSARY_DISRUPTIONS})."
        ),
    )
    parser.add_argument("--output", type=Path, required=rerun, help="Destination JSONL file.")
    parser.add_argument(
        "--max-calls",
        type=_positive_integer,
        help="Required finite total model-call budget shared by all live agents.",
    )
    parser.add_argument(
        "--max-output-tokens-per-call",
        type=_positive_integer,
        help="Required response limit for live captain and adversary calls.",
    )
    parser.add_argument(
        "--captain-model",
        type=_nonblank_model,
        metavar="MODEL",
        help="Captain model; overrides CAPTAIN_MODEL for this run.",
    )
    parser.add_argument(
        "--jev-model",
        type=_nonblank_model,
        metavar="MODEL",
        help="Jev model; overrides JEV_MODEL for this run.",
    )
    parser.add_argument(
        "--adversary-model",
        type=_nonblank_model,
        metavar="MODEL",
        help="Adversary model; overrides ADVERSARY_MODEL for this run.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="station-control",
        description=("Run an AI-led station mission, save JSONL, and replay the evidence chain."),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="Run a new mission (LLM controller by default).")
    _add_config_options(run_parser)

    commands.add_parser("scenarios", help="List legacy and YAML scenario definitions.")

    replay_parser = commands.add_parser("replay", help="Validate and display a saved mission log.")
    replay_parser.add_argument("log", type=Path)

    rerun_parser = commands.add_parser(
        "rerun", help="Run a saved scenario with a new LLM-controlled mission by default."
    )
    rerun_parser.add_argument("log", type=Path)
    _add_config_options(rerun_parser, rerun=True)
    return parser


def _read_live_settings(
    arguments: argparse.Namespace, controller: str, adversary_mode: str = AdversaryMode.OFF.value
) -> dict[str, Any]:
    captain_live = controller in LIVE_CONTROLLERS
    adversary_live = adversary_mode == AdversaryMode.LLM.value
    if not captain_live and not adversary_live:
        return {
            "api_key": None,
            "budget": None,
            "_shared_budget": None,
            "models": {"captain": None, "jev": None, "adversary": None},
        }
    if arguments.max_calls is None or arguments.max_output_tokens_per_call is None:
        raise CLIError(
            "Live agents require a finite call budget: set --max-calls and "
            "--max-output-tokens-per-call."
        )
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise CLIError("OPENROUTER_API_KEY is required for live model agents.")
    captain_model = (
        (arguments.captain_model or os.environ.get("CAPTAIN_MODEL", "")).strip()
        if captain_live
        else ""
    )
    if captain_live and not captain_model:
        raise CLIError(
            "--captain-model or CAPTAIN_MODEL is required for a live captain controller."
        )
    jev_model = (
        (arguments.jev_model or os.environ.get("JEV_MODEL", "")).strip()
        if controller == ControllerMode.JEV_LLM.value
        else ""
    )
    if controller == ControllerMode.JEV_LLM.value and not jev_model:
        raise CLIError("--jev-model or JEV_MODEL is required for the jev+llm controller.")
    adversary_model = (
        (arguments.adversary_model or os.environ.get("ADVERSARY_MODEL", "")).strip()
        if adversary_live
        else ""
    )
    if adversary_live and not adversary_model:
        raise CLIError("--adversary-model or ADVERSARY_MODEL is required for the llm adversary.")
    budget_settings = {
        "max_calls": arguments.max_calls,
        "max_output_tokens_per_call": arguments.max_output_tokens_per_call,
    }
    return {
        "api_key": api_key,
        "budget": budget_settings,
        "_shared_budget": CallBudget(**budget_settings),
        "models": {
            "captain": captain_model or None,
            "jev": jev_model or None,
            "adversary": adversary_model or None,
        },
    }


def _make_providers(controller: str, settings: dict[str, Any]):
    if controller not in LIVE_CONTROLLERS:
        return None, None
    budget = settings["_shared_budget"]
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


def _make_adversary_provider(settings: dict[str, Any]):
    model = settings["models"]["adversary"]
    if model is None:
        return None
    return OpenRouterAdversaryProvider(
        api_key=settings["api_key"],
        model=model,
        budget=settings["_shared_budget"],
        base_url=os.environ.get("ADVERSARY_BASE_URL", DEFAULT_ADVERSARY_BASE_URL),
        timeout=30.0,
    )


def _metadata(config: MissionConfig, settings: dict[str, Any]) -> dict[str, Any]:
    result = mission_metadata(config)
    result["models"] = settings["models"]
    result["call_budget"] = settings["budget"]
    result["provider_configuration"] = {
        "captain_base_url": os.environ.get("CAPTAIN_BASE_URL", DEFAULT_CAPTAIN_BASE_URL),
        "jev_base_url": os.environ.get("JEV_BASE_URL", DEFAULT_JEV_BASE_URL),
        "adversary_base_url": os.environ.get("ADVERSARY_BASE_URL", DEFAULT_ADVERSARY_BASE_URL),
        "provider": "openrouter",
    }
    return result


def _run_config(
    arguments: argparse.Namespace, saved: dict[str, Any] | None = None
) -> MissionConfig:
    defaults = saved or mission_metadata(MissionConfig())
    values = {"controller_mode": arguments.controller}
    scenario_definition = None
    selected_names = arguments.scenario or []
    selected_files = arguments.scenario_file or []
    selected_count = len(selected_names) + len(selected_files)
    if arguments.scenario_spacing is not None and selected_count < 2:
        raise CLIError("--scenario-spacing requires multiple explicit scenario selectors.")

    if selected_count > 1:
        if selected_files:
            try:
                definitions = [load_scenario(path) for path in selected_files]
            except OSError as error:
                raise CLIError(f"Cannot read scenario file: {error}") from None
        else:
            legacy_names = {family.value for family in ScenarioFamily}
            if any(name in legacy_names for name in selected_names):
                raise CLIError(
                    "Multiple scenario selection can compose YAML library IDs or files; "
                    "legacy families are supported singly."
                )
            definitions = []
            for name in selected_names:
                try:
                    definitions.append(load_library_scenario(name))
                except KeyError:
                    raise CLIError(f"Unknown scenario: {name!r}") from None
        scenario_definition = compose_scenarios(
            definitions, spacing=arguments.scenario_spacing or 0
        )
        scenario = scenario_definition.id
    elif selected_files:
        try:
            scenario_definition = load_scenario(selected_files[0])
        except OSError:
            raise CLIError(f"Cannot read scenario file: {selected_files[0]}") from None
        scenario = scenario_definition.id
    elif selected_names:
        try:
            scenario = ScenarioFamily(selected_names[0])
        except ValueError:
            try:
                scenario_definition = load_library_scenario(selected_names[0])
            except KeyError:
                raise CLIError(f"Unknown scenario: {selected_names[0]!r}") from None
            scenario = scenario_definition.id
    elif "scenario_definition" in defaults:
        snapshot = defaults["scenario_definition"]
        if not isinstance(snapshot, dict):
            raise CLIError("Saved scenario definition is missing or invalid")
        scenario_definition = scenario_definition_from_dict(snapshot)
        scenario = scenario_definition.id
    else:
        scenario = defaults.get("scenario", ScenarioFamily.NORMAL.value)
    values["scenario"] = scenario
    if scenario_definition is not None:
        values["scenario_definition"] = scenario_definition

    for argument, field, metadata_key in (
        ("seed", "seed", "seed"),
        ("turns", "duration_turns", "duration_turns"),
        ("evidence_access", "evidence_access", "evidence_access"),
        ("inspection_budget", "inspection_budget_per_turn", "inspection_budget_per_turn"),
        ("escalation_threshold", "escalation_threshold", "escalation_threshold"),
    ):
        value = getattr(arguments, argument)
        if value is None:
            value = defaults.get(metadata_key)
        if value is not None:
            values[field] = value
    adversary_mode = arguments.adversary
    if adversary_mode is None:
        adversary_mode = defaults.get("adversary", AdversaryMode.OFF.value)
    values["adversary_mode"] = adversary_mode
    adversary_budget = arguments.adversary_budget
    if adversary_budget is None:
        adversary_budget = defaults.get(
            "adversary_disruption_budget", DEFAULT_ADVERSARY_DISRUPTIONS
        )
    values["adversary_disruption_budget"] = adversary_budget
    return MissionConfig(**values, run_id=str(uuid4()))


def _evaluation_metrics(result: MissionResult) -> dict[str, Any]:
    evaluation = result.evaluation
    return {
        "measures": evaluation.metrics,
        "unavailable": list(evaluation.unavailable),
    }


def _close_providers(captain: Any, dispatcher: Any, adversary: Any = None) -> None:
    for provider in (adversary, dispatcher, captain):
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass


def _run(arguments: argparse.Namespace, config: MissionConfig) -> int:
    settings = _read_live_settings(
        arguments, config.controller_mode.value, config.adversary_mode.value
    )
    run_id = config.run_id
    assert run_id is not None
    output = arguments.output or Path("runs") / f"{run_id}.jsonl"
    writer = RunLogWriter(
        output,
        _metadata(config, settings),
        run_id=run_id,
        simulator_version=SIMULATOR_VERSION,
    )
    captain = dispatcher = adversary = None
    try:
        # The destination is exclusively reserved before live providers are composed.
        captain, dispatcher = _make_providers(config.controller_mode.value, settings)
        adversary = _make_adversary_provider(settings)
        result = run_mission(
            config,
            captain=captain,
            dispatcher=dispatcher,
            adversary=adversary,
            event_sink=writer.write_event,
        )
        writer.finish(
            status=result.status.value,
            metrics=_evaluation_metrics(result),
            debug_snapshots=result.debug_snapshots,
            turns_completed=result.turns_completed,
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
        _close_providers(captain, dispatcher, adversary)

    print(f"Saved run: {output}")
    print(
        f"Scenario: {_scenario_id(config.scenario)}; seed: {config.seed}; "
        f"controller: {config.controller_mode.value}"
    )
    print(f"Turns completed: {result.turns_completed}; status: {result.status.value}")
    print(f"Metrics: {json.dumps(_evaluation_metrics(result), sort_keys=True)}")
    return 0


def _scenario_id(scenario: ScenarioFamily | str) -> str:
    return scenario.value if isinstance(scenario, ScenarioFamily) else scenario


def _list_scenarios() -> int:
    try:
        definitions = list_scenarios()
    except OSError:
        raise CLIError("Cannot read the YAML scenario library") from None
    print("Legacy scenarios:")
    for scenario in ScenarioFamily:
        print(f"  {scenario.value}")
    print("YAML scenario library:")
    for definition in definitions:
        print(f"  {definition.id}: {definition.description}")
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
        if arguments.command == "scenarios":
            return _list_scenarios()
        if arguments.command == "rerun":
            return _rerun(arguments)
        config = _run_config(arguments)
        return _run(arguments, config)
    except (CLIError, RunLogError, ValueError) as error:
        print(f"station-control: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
