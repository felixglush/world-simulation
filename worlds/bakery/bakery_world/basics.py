from enum import StrEnum, auto
from typing import NewType

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
