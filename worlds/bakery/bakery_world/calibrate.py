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
    drop = 1 - rev[35] / peak
    return Check(
        "year 3 declines", 0.10 <= drop <= 0.25, f"month 36 {drop:.1%} below peak (10-25%)"
    )


def _handoff_revenue(rev: Series, op: Series) -> Check:
    return Check("handoff revenue", 32_000 <= rev[35] <= 42_000, f"${rev[35]:,.0f} ($32k-$42k)")


def _handoff_profit(rev: Series, op: Series) -> Check:
    return Check("handoff profit", -1_000 <= op[35] <= 4_000, f"${op[35]:,.0f} (-$1k to $4k)")


ENVELOPE: tuple[Callable[[Series, Series], Check], ...] = (
    _growth,
    _flat,
    _decline,
    _handoff_revenue,
    _handoff_profit,
)


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
    checks = tuple(check(rev, op) for check in ENVELOPE)
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
