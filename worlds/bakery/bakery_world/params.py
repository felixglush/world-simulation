import random

from bakery_world.basics import Category, ItemId, Segment
from bakery_world.catalog import CATALOG
from bakery_world.model import HiddenParams

Range = tuple[float, float]

ELASTICITY: dict[Segment, Range] = {
    Segment.COMMUTER: (0.7, 1.1),
    Segment.FAMILY: (1.1, 1.6),
    Segment.STUDENT: (1.5, 2.1),
    Segment.RETIREE: (1.0, 1.4),
    Segment.OFFICE: (0.6, 1.0),
}

TASTE: dict[Segment, dict[Category, Range]] = {
    Segment.COMMUTER: {
        Category.BREAD: (1.0, 1.4),
        Category.PASTRY: (1.7, 2.3),
        Category.CAKE: (0.8, 1.2),
        Category.DRINK: (2.0, 2.6),
        Category.SAVORY: (1.3, 1.8),
    },
    Segment.FAMILY: {
        Category.BREAD: (1.7, 2.2),
        Category.PASTRY: (1.6, 2.1),
        Category.CAKE: (1.5, 2.0),
        Category.DRINK: (1.0, 1.5),
        Category.SAVORY: (1.0, 1.4),
    },
    Segment.STUDENT: {
        Category.BREAD: (0.7, 1.1),
        Category.PASTRY: (1.7, 2.2),
        Category.CAKE: (1.1, 1.5),
        Category.DRINK: (2.0, 2.5),
        Category.SAVORY: (1.4, 1.8),
    },
    Segment.RETIREE: {
        Category.BREAD: (1.8, 2.3),
        Category.PASTRY: (1.4, 1.8),
        Category.CAKE: (1.4, 1.9),
        Category.DRINK: (1.5, 2.0),
        Category.SAVORY: (1.0, 1.4),
    },
    Segment.OFFICE: {
        Category.BREAD: (0.9, 1.3),
        Category.PASTRY: (1.4, 1.9),
        Category.CAKE: (1.2, 1.7),
        Category.DRINK: (2.0, 2.6),
        Category.SAVORY: (1.7, 2.2),
    },
}

SCALARS: dict[str, Range] = {
    "item_appeal": (0.85, 1.15),
    "outside_option": (0.6, 1.0),
    "walkaway_wait_min": (6.0, 10.0),
    "monthly_lapse_rate": (0.015, 0.03),
    "new_customer_rate": (0.008, 0.015),
    "reputation_drift": (0.015, 0.03),
    "wage_morale_sensitivity": (0.8, 1.6),
    "quit_hazard": (0.0003, 0.0007),
}


def draw_hidden_params(rng: random.Random) -> HiddenParams:
    def u(r: Range) -> float:
        return rng.uniform(*r)

    scalars = {k: u(r) for k, r in SCALARS.items() if k != "item_appeal"}
    return HiddenParams(
        elasticity={s: u(r) for s, r in ELASTICITY.items()},
        taste={s: {c: u(r) for c, r in cats.items()} for s, cats in TASTE.items()},
        item_appeal={ItemId(i): u(SCALARS["item_appeal"]) for i in CATALOG},
        **scalars,
    )
