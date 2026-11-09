import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import Pool

from bakery_world.basics import Account
from bakery_world.books import DAYS_PER_MONTH, OPERATING, days_in_months, monthly_by_account
from bakery_world.history import HISTORY_DAYS, found, live
from bakery_world.town import HOME_LOCATION

Series = list[float]


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SeedCalibration:
    seed: int
    revenue: Series
    operating_profit: Series
    checks: tuple[Check, ...]
    seconds: float

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def trailing3(series: Series, month: int) -> float:
    return sum(series[month - 3 : month]) / 3


def _growth(rev: Series, op: Series) -> Check:
    ratio = rev[11] / rev[1]
    return Check("year 1 grows", ratio >= 1.4, f"month 12 / month 2 = {ratio:.2f} (need >= 1.40)")


def _flat(rev: Series, op: Series) -> Check:
    means = [trailing3(rev, m) for m in range(13, 25)]
    centre = sum(means) / len(means)
    worst = max(abs(m / centre - 1) for m in means)
    return Check("year 2 flat", worst <= 0.08, f"worst trailing-3 gap {worst:.1%} (need <= 8%)")


def _decline(rev: Series, op: Series) -> Check:
    peak = max(trailing3(rev, m) for m in range(3, 37))
    drop = 1 - trailing3(rev, 36) / peak
    return Check(
        "year 3 declines", 0.10 <= drop <= 0.25, f"months 34-36 {drop:.1%} below peak (10-25%)"
    )


# the handoff is judged on the last quarter: one month's sales swing by about 5%
def _handoff_revenue(rev: Series, op: Series) -> Check:
    level = trailing3(rev, 36)
    return Check("handoff revenue", 32_000 <= level <= 42_000, f"${level:,.0f} ($32k-$42k)")


def _handoff_profit(rev: Series, op: Series) -> Check:
    level = trailing3(op, 36)
    return Check("handoff profit", -1_000 <= level <= 4_000, f"${level:,.0f} (-$1k to $4k)")


# the arc is judged on revenue with the shop's own seasons taken out, the handoff as booked
SHAPE: tuple[Callable[[Series, Series], Check], ...] = (_growth, _flat, _decline)
LEVEL: tuple[Callable[[Series, Series], Check], ...] = (_handoff_revenue, _handoff_profit)


def deseasonalise(series: Series) -> Series:
    """Divide each month by its calendar month's mean ratio to a centred 12-month average.

    The shop's realised seasons, not foot traffic's: a shop short of bakers or counter hours
    cannot follow a December peak, so traffic's swing would overstate it."""
    ratios: dict[int, list[float]] = {}
    for m in range(6, len(series) - 6):
        centred = (sum(series[m - 6 : m + 6]) + sum(series[m - 5 : m + 7])) / 24
        ratios.setdefault(m % 12, []).append(series[m] / centred)
    index = {k: sum(v) / len(v) for k, v in ratios.items()}
    mean = sum(index.values()) / len(index)
    return [x / (index[m % 12] / mean) for m, x in enumerate(series)]


def shop_months(seed: int) -> tuple[Series, Series]:
    founding = found(seed)
    live(founding, HISTORY_DAYS)
    books = monthly_by_account(founding.world.business.ledger)
    revenue, profit = [], []
    for month, days in sorted(days_in_months(HISTORY_DAYS).items()):
        accounts = books.get((month, HOME_LOCATION), {})
        scale = DAYS_PER_MONTH / days / 100
        revenue.append(accounts.get(Account.SALES, 0) * scale)
        profit.append(sum(v for a, v in accounts.items() if a in OPERATING) * scale)
    return revenue, profit


def calibrate_seed(seed: int) -> SeedCalibration:
    start = time.perf_counter()
    rev, op = shop_months(seed)
    adjusted = deseasonalise(rev)
    checks = tuple(check(adjusted, op) for check in SHAPE) + tuple(
        check(rev, op) for check in LEVEL
    )
    return SeedCalibration(seed, rev, op, checks, time.perf_counter() - start)


def calibrate(seeds: list[int], jobs: int = 1) -> list[SeedCalibration]:
    if jobs <= 1:
        return [calibrate_seed(s) for s in seeds]
    with Pool(jobs) as pool:
        return pool.map(calibrate_seed, seeds)


def report(result: SeedCalibration) -> str:
    lines = [f"seed {result.seed}: {'PASS' if result.passed else 'FAIL'} ({result.seconds:.1f}s)"]
    for year in range(3):
        months = range(12 * year, 12 * year + 12)
        lines.append(
            f"  y{year + 1} revenue $k "
            + " ".join(f"{result.revenue[m] / 1000:5.1f}" for m in months)
        )
        lines.append(
            f"  y{year + 1} op profit $k"
            + " ".join(f"{result.operating_profit[m] / 1000:5.1f}" for m in months)
        )
    for c in result.checks:
        lines.append(f"  {'ok  ' if c.passed else 'FAIL'} {c.name}: {c.detail}")
    return "\n".join(lines)
