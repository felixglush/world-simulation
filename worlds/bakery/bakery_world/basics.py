from datetime import date, timedelta
from enum import StrEnum, auto
from typing import NewType

START_DATE = date(2023, 1, 2)


def date_of(day: int) -> date:
    return START_DATE + timedelta(days=day)


Cents = NewType("Cents", int)
LocationId = NewType("LocationId", str)
StaffId = NewType("StaffId", str)
ItemId = NewType("ItemId", str)
CustomerId = NewType("CustomerId", str)
CompetitorId = NewType("CompetitorId", str)
NeighborhoodId = NewType("NeighborhoodId", str)


class Segment(StrEnum):
    COMMUTER = auto()
    FAMILY = auto()
    STUDENT = auto()
    RETIREE = auto()
    OFFICE = auto()


class Ingredient(StrEnum):
    FLOUR = auto()
    BUTTER = auto()
    EGGS = auto()
    SUGAR = auto()
    DAIRY = auto()
    COFFEE = auto()
    CHOCOLATE = auto()
    FRUIT = auto()


class Category(StrEnum):
    BREAD = auto()
    PASTRY = auto()
    CAKE = auto()
    DRINK = auto()
    SAVORY = auto()


class Role(StrEnum):
    BAKER = auto()
    COUNTER = auto()
    CLEANER = auto()


class Payment(StrEnum):
    CARD = auto()
    CASH = auto()


class Account(StrEnum):
    SALES = auto()
    INGREDIENTS = auto()
    PACKAGING = auto()
    WAGES = auto()
    PAYROLL_ONCOSTS = auto()
    RENT = auto()
    CARD_FEES = auto()
    REPAIRS = auto()
    UTILITIES = auto()
    INSURANCE = auto()
    SOFTWARE_AND_TERMINAL = auto()
    REPAIRS_RESERVE = auto()
    MARKETING = auto()
    INTEREST = auto()
    FIT_OUT = auto()
    LEASE_BREAK = auto()
    LOAN_REPAYMENT = auto()
    CREDIT_DRAWN = auto()
    CREDIT_REPAID = auto()
