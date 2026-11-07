from collections import defaultdict
from enum import StrEnum, auto

from bakery_world.basics import Account, LocationId, date_of
from bakery_world.model import LedgerEntry

DAYS_PER_MONTH = 365.25 / 12


class Section(StrEnum):
    REVENUE = auto()
    COST_OF_SALES = auto()
    OPERATING_EXPENSE = auto()
    FINANCE = auto()
    ONE_OFF = auto()
    BALANCE_SHEET = auto()


A = Account
SECTION: dict[Account, Section] = {
    A.SALES: Section.REVENUE,
    A.INGREDIENTS: Section.COST_OF_SALES,
    A.PACKAGING: Section.COST_OF_SALES,
    A.CARD_FEES: Section.COST_OF_SALES,
    A.WAGES: Section.OPERATING_EXPENSE,
    A.PAYROLL_ONCOSTS: Section.OPERATING_EXPENSE,
    A.RENT: Section.OPERATING_EXPENSE,
    A.REPAIRS: Section.OPERATING_EXPENSE,
    A.UTILITIES: Section.OPERATING_EXPENSE,
    A.INSURANCE: Section.OPERATING_EXPENSE,
    A.SOFTWARE_AND_TERMINAL: Section.OPERATING_EXPENSE,
    A.REPAIRS_RESERVE: Section.OPERATING_EXPENSE,
    A.MARKETING: Section.OPERATING_EXPENSE,
    A.INTEREST: Section.FINANCE,
    A.FIT_OUT: Section.ONE_OFF,
    A.LEASE_BREAK: Section.ONE_OFF,
    A.LOAN_REPAYMENT: Section.BALANCE_SHEET,
    A.CREDIT_DRAWN: Section.BALANCE_SHEET,
    A.CREDIT_REPAID: Section.BALANCE_SHEET,
}
OPERATING = frozenset(
    a
    for a, s in SECTION.items()
    if s in (Section.REVENUE, Section.COST_OF_SALES, Section.OPERATING_EXPENSE)
)


def month_key(day: int) -> str:
    return date_of(day).strftime("%Y-%m")


def monthly_by_account(
    ledger: list[LedgerEntry],
) -> dict[tuple[str, LocationId | None], dict[Account, int]]:
    out: dict[tuple[str, LocationId | None], dict[Account, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for e in ledger:
        out[(month_key(e.day), e.location_id)][e.account] += e.amount_cents
    return out


def days_in_months(days: int) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for day in range(days):
        counts[month_key(day)] += 1
    return dict(counts)
