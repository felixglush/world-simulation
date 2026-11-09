import random

from bakery_world.basics import Category, ItemId, Segment
from bakery_world.catalog import CATALOG
from bakery_world.model import HiddenParams

Range = tuple[float, float]

ELASTICITY: dict[Segment, Range] = {
    Segment.COMMUTER: (1.04, 1.31),
    Segment.FAMILY: (1.61, 1.94),
    Segment.STUDENT: (2.15, 2.55),
    Segment.RETIREE: (1.43, 1.68),
    Segment.OFFICE: (0.925, 1.18),
}

TASTE: dict[Segment, dict[Category, Range]] = {
    Segment.COMMUTER: {
        Category.BREAD: (1.1, 1.3),
        Category.PASTRY: (1.85, 2.15),
        Category.CAKE: (0.9, 1.1),
        Category.DRINK: (2.15, 2.45),
        Category.SAVORY: (1.43, 1.68),
    },
    Segment.FAMILY: {
        Category.BREAD: (1.83, 2.08),
        Category.PASTRY: (1.73, 1.98),
        Category.CAKE: (1.62, 1.88),
        Category.DRINK: (1.12, 1.38),
        Category.SAVORY: (1.1, 1.3),
    },
    Segment.STUDENT: {
        Category.BREAD: (0.8, 1),
        Category.PASTRY: (1.83, 2.08),
        Category.CAKE: (1.2, 1.4),
        Category.DRINK: (2.12, 2.38),
        Category.SAVORY: (1.5, 1.7),
    },
    Segment.RETIREE: {
        Category.BREAD: (1.92, 2.17),
        Category.PASTRY: (1.5, 1.7),
        Category.CAKE: (1.52, 1.77),
        Category.DRINK: (1.62, 1.88),
        Category.SAVORY: (1.1, 1.3),
    },
    Segment.OFFICE: {
        Category.BREAD: (1, 1.2),
        Category.PASTRY: (1.52, 1.77),
        Category.CAKE: (1.32, 1.57),
        Category.DRINK: (2.15, 2.45),
        Category.SAVORY: (1.83, 2.08),
    },
}

SCALARS: dict[str, Range] = {
    "item_appeal": (0.93, 1.07),
    "outside_option": (0.65, 0.85),
    "walkaway_wait_min": (7.5, 10.0),
    "monthly_lapse_rate": (0.0205, 0.0245),
    "new_customer_rate": (0.03, 0.04),
    "reputation_drift": (0.015, 0.03),
    "wage_morale_sensitivity": (0.8, 1.6),
    "quit_hazard": (0.0011, 0.0023),
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
