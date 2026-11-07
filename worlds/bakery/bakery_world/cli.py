import argparse
import json
import random
import sys
from collections.abc import Callable, Sequence

from bakery_world.engine import DayReport, run_days
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
    args = parser.parse_args(argv)

    world = build_world(args.seed)
    rng = random.Random(f"{args.seed}:days")
    for report in run_days(world, args.days, lambda world, day: None, rng):
        print(json.dumps(day_summary(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
