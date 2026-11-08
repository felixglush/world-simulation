import argparse
import json
import os
import random
import sys
import tempfile
from collections.abc import Callable, Sequence
from multiprocessing import Pool
from pathlib import Path

from bakery_world.baselines import POLICIES, run_seed, table
from bakery_world.calibrate import calibrate
from bakery_world.calibrate import report as calibration_report
from bakery_world.engine import DayReport, run_days
from bakery_world.history import generate_history
from bakery_world.town import build_world

MAX_DAYS = 3650


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
    cal = sub.add_parser("calibrate", help="check each seed's history against the envelope")
    cal.add_argument("--seeds", type=_seed_range, required=True, help="e.g. 1-8")
    cal.add_argument("--jobs", type=_bounded_int(1, 64), default=min(8, os.cpu_count() or 1))
    base = sub.add_parser("baselines", help="score the four baseline policies per seed")
    base.add_argument("--seeds", type=_seed_range, required=True, help="e.g. 1-5")
    base.add_argument("--weeks", type=_bounded_int(1, 52), default=52)
    base.add_argument("--jobs", type=_bounded_int(1, 64), default=min(8, os.cpu_count() or 1))
    base.add_argument("--history-dir", type=Path, help="reuse or keep histories here")
    base.add_argument("--out", type=Path, help="write each run's log and records here")
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


def _simulate(args: argparse.Namespace) -> int:
    world = build_world(args.seed)
    rng = random.Random(f"{args.seed}:days")
    for report in run_days(world, args.days, lambda world, day: None, rng):
        print(json.dumps(day_summary(report)))
    return 0


def _history(args: argparse.Namespace) -> int:
    result = generate_history(args.seed, args.out)
    print(f"seed {result.seed}: {result.days} days in {result.seconds:.1f}s")
    for path in (result.records, result.hidden, result.handoff):
        print(f"  {path}")
    print(f"  content hash {result.content_hash}")
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
}


if __name__ == "__main__":
    sys.exit(main())
