import argparse
import json
import os
import random
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict
from multiprocessing import Pool
from pathlib import Path

from bakery_world.baselines import POLICIES, Founder, SeedRow, run_seed, table
from bakery_world.calibrate import calibrate
from bakery_world.calibrate import report as calibration_report
from bakery_world.engine import DayReport, run_days
from bakery_world.history import generate_history
from bakery_world.llm import (
    ANALYST_MODEL,
    MANAGER_MODEL,
    WRITER_MODEL,
    CallBudget,
    Caller,
    CallRecord,
    ChatModel,
    OpenRouterChat,
    Reply,
)
from bakery_world.management import RunResult, manage
from bakery_world.manager import LLMManager
from bakery_world.town import build_world
from bakery_world.writer import Writer

MAX_DAYS = 3650
MAX_CALLS = 100_000


def chat_model(name: str) -> ChatModel:
    return OpenRouterChat(name)


def _bounded_int(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{value} is outside {low}..{high}")
        return value

    return parse


def _seed_range(text: str) -> list[int]:
    low, _, high = text.partition("-")
    try:
        seeds = list(range(int(low), int(high or low) + 1))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a seed or seed range") from None
    if not seeds or seeds[0] < 0:
        raise argparse.ArgumentTypeError(f"{text!r} is an empty or negative range")
    return seeds


def day_summary(report: DayReport) -> dict:
    shops = report.locations
    return {
        "day": report.day,
        "revenue_cents": sum(s.revenue_cents for s in shops),
        "units_sold": sum(i.sold + i.day_old_sold for s in shops for i in s.items.values()),
        "waste_units": sum(i.wasted for s in shops for i in s.items.values()),
        "walkaways": sum(s.walkaways for s in shops),
        "cash_cents": report.cash_cents,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bakery_world")
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate", help="print one JSON line per simulated day")
    sim.add_argument("--seed", type=_bounded_int(0, 2**31 - 1), required=True)
    sim.add_argument("--days", type=_bounded_int(1, MAX_DAYS), required=True)
    hist = sub.add_parser("history", help="write three years of shop records and the handoff")
    hist.add_argument("--seed", type=_bounded_int(0, 2**31 - 1), required=True)
    hist.add_argument("--out", type=Path, required=True)
    hist.add_argument("--text", choices=("template", "model"), default="template")
    hist.add_argument(
        "--max-calls", type=_bounded_int(1, MAX_CALLS), help="needed with --text model"
    )
    hist.add_argument("--writer-model", help=f"default {WRITER_MODEL}")
    cal = sub.add_parser("calibrate", help="check each seed's history against the envelope")
    cal.add_argument("--seeds", type=_seed_range, required=True, help="e.g. 1-8")
    cal.add_argument("--jobs", type=_bounded_int(1, 64), default=min(8, os.cpu_count() or 1))
    base = sub.add_parser("baselines", help="score the four baseline policies per seed")
    base.add_argument("--seeds", type=_seed_range, required=True, help="e.g. 1-5")
    base.add_argument("--weeks", type=_bounded_int(1, 52), default=52)
    base.add_argument("--jobs", type=_bounded_int(1, 64), default=min(8, os.cpu_count() or 1))
    base.add_argument("--history-dir", type=Path, help="reuse or keep histories here")
    base.add_argument("--out", type=Path, help="write each run's log and records here")
    man = sub.add_parser("manage", help="run one policy for the management year and score it")
    man.add_argument("--history", type=Path, required=True, help="a history command's --out")
    man.add_argument("--policy", choices=("llm", *POLICIES), required=True)
    man.add_argument("--weeks", type=_bounded_int(1, 52), default=52)
    man.add_argument("--max-calls", type=_bounded_int(1, MAX_CALLS), help="needed with llm")
    man.add_argument("--out", type=Path, required=True, help="run log and records go here")
    man.add_argument("--manager-model", help=f"default {MANAGER_MODEL}")
    man.add_argument("--analyst-model", help=f"default {ANALYST_MODEL}")
    args = parser.parse_args(argv)
    _validate(parser, args)
    return COMMANDS[args.command](args)


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command == "history":
        if args.text == "model" and args.max_calls is None:
            parser.error("--text model needs --max-calls")
        if args.text == "template" and (args.max_calls or args.writer_model):
            parser.error("--max-calls and --writer-model apply only to --text model")
    if args.command == "manage":
        llm_only = args.max_calls or args.manager_model or args.analyst_model
        if args.policy == "llm" and args.max_calls is None:
            parser.error("--policy llm needs a finite --max-calls")
        if args.policy != "llm" and llm_only:
            parser.error("--max-calls and the model flags apply only to --policy llm")
        for name in ("handoff.pickle", "records.sqlite"):
            if not (args.history / name).exists():
                parser.error(f"{args.history / name} does not exist; run history first")
        if args.out.resolve() == args.history.resolve():
            parser.error("--out must differ from --history")


def _simulate(args: argparse.Namespace) -> int:
    world = build_world(args.seed)
    rng = random.Random(f"{args.seed}:days")
    for report in run_days(world, args.days, lambda world, day: None, rng):
        print(json.dumps(day_summary(report)))
    return 0


def _jsonl_sink(path: Path):
    def sink(record: CallRecord, reply: Reply) -> None:
        with path.open("a") as f:
            f.write(json.dumps(asdict(record), sort_keys=True) + "\n")

    return sink


def _history(args: argparse.Namespace) -> int:
    writer = None
    if args.text == "model":
        args.out.mkdir(parents=True, exist_ok=True)
        caller = Caller(CallBudget(args.max_calls), _jsonl_sink(args.out / "text_calls.jsonl"))
        writer = Writer(caller, chat_model(args.writer_model or WRITER_MODEL))
    result = generate_history(args.seed, args.out, writer=writer)
    print(f"seed {result.seed}: {result.days} days in {result.seconds:.1f}s")
    for path in (result.records, result.hidden, result.handoff):
        print(f"  {path}")
    print(f"  content hash {result.content_hash}")
    if result.text and writer:
        t = result.text
        print(
            f"  text: {t.cached} cached, {t.written} written by {writer.model.name}, "
            f"{t.fell_back} rejected and {t.unattempted} unattempted (templates kept); "
            f"{writer.caller.budget.used} calls"
        )
    return 0


def _usage(result: RunResult) -> str:
    calls = [r for r in map(json.loads, result.log) if r["kind"] == "model_call"]
    cost = sum(c["cost"] or 0 for c in calls)
    tokens_in = sum(c["input_tokens"] for c in calls)
    tokens_out = sum(c["output_tokens"] for c in calls)
    by_role = {role: sum(c["role"] == role for c in calls) for role in ("manager", "analyst")}
    return (
        f"model calls {len(calls)} (manager {by_role['manager']}, analyst {by_role['analyst']}), "
        f"tokens in {tokens_in:,} out {tokens_out:,}, reported cost ${cost:.2f}"
    )


def _manage(args: argparse.Namespace) -> int:
    if args.policy == "llm":
        policy = LLMManager(
            Caller(CallBudget(args.max_calls)),
            chat_model(args.manager_model or MANAGER_MODEL),
            chat_model(args.analyst_model or ANALYST_MODEL),
            records=args.history / "records.sqlite",
            weeks=args.weeks,
        )
    else:
        policy = POLICIES[args.policy]()
    result = manage(args.history, policy, args.weeks, args.out)
    stopped = [r for r in map(json.loads, result.log) if r["kind"] == "stopped"]
    founder = manage(args.history, Founder(), result.weeks)
    rows = [
        SeedRow(r.seed, r.policy, r.bankrupt, r.net_worth_change, score, r.diagnostics, r.seconds)
        for r, score in (
            (result, result.net_worth_change - founder.net_worth_change),
            (founder, 0),
        )
    ]
    print(table(rows))
    print(
        f"weeks run {result.weeks} of {args.weeks}; scored against the founder over the same weeks"
    )
    if stopped:
        print(f"stopped early: {stopped[0]['reason']}")
    if args.policy == "llm":
        print(_usage(result))
    print(f"score {result.policy}: ${rows[0].score / 100:,.0f}")
    print(f"run log {args.out / 'run.jsonl'}")
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    results = calibrate(args.seeds, args.jobs)
    for result in results:
        print(calibration_report(result))
    passed = sum(r.passed for r in results)
    print(f"passed {passed} / {len(results)}")
    return 0 if passed == len(results) else 1


def _baselines(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory() as scratch:
        history = args.history_dir or Path(scratch)
        jobs = [(seed, args.weeks, history, args.out) for seed in args.seeds]
        if args.jobs <= 1:
            per_seed = [run_seed(*job) for job in jobs]
        else:
            with Pool(min(args.jobs, len(jobs))) as pool:
                per_seed = pool.starmap(run_seed, jobs)
    rows = [row for rows in per_seed for row in rows]
    print(table(rows))
    for name in POLICIES:
        mine = [r.score for r in rows if r.policy == name]
        print(f"mean score {name}: ${sum(mine) / len(mine) / 100:,.0f}")
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "simulate": _simulate,
    "history": _history,
    "calibrate": _calibrate,
    "baselines": _baselines,
    "manage": _manage,
}


if __name__ == "__main__":
    sys.exit(main())
