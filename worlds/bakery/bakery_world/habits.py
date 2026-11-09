import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum, auto

from bakery_world.basics import Cents, Ingredient, ItemId, Role
from bakery_world.engine import market_wage
from bakery_world.exogenous import MeanRevertingPrices, SpikeWindow
from bakery_world.founder import Quirks
from bakery_world.model import World
from bakery_world.town import HOME_NEIGHBORHOOD, MARKET_BUSY_AFTERNOON

MIN_HABITS, MAX_HABITS = 4, 6
FLOUR_SPIKE_START = (820, 900)
FLOUR_SPIKE_DAYS = 420
FLOUR_SPIKE_MULTIPLIER = (1.3, 1.45)


class HabitId(StrEnum):
    CHEAP_CROISSANT = auto()
    EARLY_WEEK_SOURDOUGH = auto()
    EARLY_CLOSE = auto()
    SLOW_LOYAL_BAKER = auto()
    DEAR_BUTTER = auto()
    UNPRICED_FLOUR_SPIKE = auto()


def _cheap_croissant(world: World, quirks: Quirks, rng: random.Random, seed: int) -> dict:
    quirks.price_factor[ItemId("croissant")] = 0.75
    return {"item": "croissant", "price_factor_of_market": 0.75}


def _early_week_sourdough(world: World, quirks: Quirks, rng: random.Random, seed: int) -> dict:
    for weekday in (0, 1):
        quirks.plan_factor[(ItemId("sourdough"), weekday)] = 1.4
    return {"item": "sourdough", "weekdays": ["Monday", "Tuesday"], "plan_factor": 1.4}


def _early_close(world: World, quirks: Quirks, rng: random.Random, seed: int) -> dict:
    home = world.town[HOME_NEIGHBORHOOD]
    world.town[HOME_NEIGHBORHOOD] = replace(home, hourly_traffic=MARKET_BUSY_AFTERNOON)
    return {"neighborhood": HOME_NEIGHBORHOOD, "close_hour": 15}


def _slow_loyal_baker(world: World, quirks: Quirks, rng: random.Random, seed: int) -> dict:
    baker = min(
        (s for s in world.business.staff.values() if s.role is Role.BAKER), key=lambda s: s.id
    )
    baker.speed, baker.skill, baker.reliability, baker.loyal = 0.72, 0.25, 0.97, True
    baker.wage_cents_per_hour = Cents(round(market_wage(world, Role.BAKER) * 1.08))
    return {"staff_id": baker.id, "name": baker.name, "speed": 0.72, "skill": 0.25}


def _dear_butter(world: World, quirks: Quirks, rng: random.Random, seed: int) -> dict:
    world.business.buy_from[Ingredient.BUTTER] = "hartley"
    return {"supplier": "hartley", "cheaper_supplier": "greenfield", "premium": 0.12}


def _unpriced_flour_spike(world: World, quirks: Quirks, rng: random.Random, seed: int) -> dict:
    start = rng.randint(*FLOUR_SPIKE_START)
    spike = SpikeWindow(
        Ingredient.FLOUR,
        start,
        start + FLOUR_SPIKE_DAYS,
        round(rng.uniform(*FLOUR_SPIKE_MULTIPLIER), 2),
    )
    world.ingredient_prices = MeanRevertingPrices(seed, spikes=(spike,))
    return {"start_day": spike.start_day, "end_day": spike.end_day, "multiplier": spike.multiplier}


@dataclass(frozen=True)
class Habit:
    id: HabitId
    description: str
    apply: Callable[[World, Quirks, random.Random, int], dict]


HABITS: dict[HabitId, Habit] = {
    h.id: h
    for h in (
        Habit(
            HabitId.CHEAP_CROISSANT,
            "Croissant priced 25% below what the market bears.",
            _cheap_croissant,
        ),
        Habit(
            HabitId.EARLY_WEEK_SOURDOUGH,
            "Sourdough overbaked on Monday and Tuesday (plan x1.4), so much of it is wasted.",
            _early_week_sourdough,
        ),
        Habit(
            HabitId.EARLY_CLOSE,
            "Shop closes at 15:00 while its own area has heavy afternoon foot traffic.",
            _early_close,
        ),
        Habit(
            HabitId.SLOW_LOYAL_BAKER,
            "A slow, low-skill but loyal baker is kept on above market pay, capping the bake.",
            _slow_loyal_baker,
        ),
        Habit(
            HabitId.DEAR_BUTTER,
            "Butter bought from Hartley Dairy at 12% above Greenfield Creamery's list price.",
            _dear_butter,
        ),
        Habit(
            HabitId.UNPRICED_FLOUR_SPIKE,
            "Flour price spikes in year 3 and the founder never raises bread prices.",
            _unpriced_flour_spike,
        ),
    )
}


def draw_habits(seed: int) -> tuple[HabitId, ...]:
    rng = random.Random(f"{seed}:habits")
    picked = set(rng.sample(list(HABITS), rng.randint(MIN_HABITS, MAX_HABITS)))
    return tuple(h for h in HABITS if h in picked)


def apply_habits(
    world: World, quirks: Quirks, seed: int, habits: tuple[HabitId, ...]
) -> dict[HabitId, dict]:
    rng = random.Random(f"{seed}:habit-details")
    return {hid: HABITS[hid].apply(world, quirks, rng, seed) for hid in habits}
