import math
import random
from dataclasses import dataclass
from typing import Protocol

from bakery_world.basics import Ingredient, NeighborhoodId


class Weather(Protocol):
    def on(self, day: int) -> tuple[float, bool]: ...


class IngredientPrices(Protocol):
    def on(self, day: int) -> dict[Ingredient, int]: ...


class FootTraffic(Protocol):
    def multiplier(self, neighborhood: NeighborhoodId, day: int) -> float: ...


def _day_rng(seed: int, stream: str, day: int) -> random.Random:
    return random.Random(f"{seed}:{stream}:{day}")


@dataclass(frozen=True)
class SeasonalWeather:
    seed: int

    def on(self, day: int) -> tuple[float, bool]:
        rng = _day_rng(self.seed, "weather", day)
        season = math.sin(2 * math.pi * (day - 100) / 365)
        temp = 11 + 10 * season + rng.gauss(0, 3)
        rain = rng.random() < 0.22 - 0.07 * season
        return temp, rain


BASE_CENTS_PER_KG: dict[Ingredient, int] = {
    Ingredient.FLOUR: 120,
    Ingredient.BUTTER: 1100,
    Ingredient.EGGS: 400,
    Ingredient.SUGAR: 150,
    Ingredient.DAIRY: 130,
    Ingredient.COFFEE: 2400,
    Ingredient.CHOCOLATE: 1500,
    Ingredient.FRUIT: 450,
}


@dataclass(frozen=True)
class SpikeWindow:
    ingredient: Ingredient
    start_day: int
    end_day: int
    multiplier: float


class MeanRevertingPrices:
    def __init__(
        self,
        seed: int,
        horizon_days: int = 2000,
        spikes: tuple[SpikeWindow, ...] = (),
        reversion: float = 0.03,
        volatility: float = 0.025,
    ) -> None:
        rng = random.Random(f"{seed}:ingredient-prices")
        self._by_day: list[dict[Ingredient, int]] = []
        log_dev = {ing: 0.0 for ing in BASE_CENTS_PER_KG}
        for day in range(horizon_days):
            row = {}
            for ing, base in BASE_CENTS_PER_KG.items():
                log_dev[ing] += -reversion * log_dev[ing] + rng.gauss(0, volatility)
                spike = 1.0
                for w in spikes:
                    if w.ingredient == ing and w.start_day <= day < w.end_day:
                        spike *= w.multiplier
                row[ing] = round(base * math.exp(log_dev[ing]) * spike)
            self._by_day.append(row)

    def on(self, day: int) -> dict[Ingredient, int]:
        return self._by_day[day]


@dataclass(frozen=True)
class TrafficShock:
    neighborhood: NeighborhoodId | None
    start_day: int
    end_day: int
    multiplier: float


WEEKDAY_TRAFFIC = (1.0, 1.0, 1.0, 1.02, 1.05, 1.05, 0.88)


@dataclass(frozen=True)
class SeasonalFootTraffic:
    shocks: tuple[TrafficShock, ...] = ()

    def multiplier(self, neighborhood: NeighborhoodId, day: int) -> float:
        value = WEEKDAY_TRAFFIC[day % 7] * (1 + 0.06 * math.sin(2 * math.pi * (day - 120) / 365))
        for s in self.shocks:
            if s.start_day <= day < s.end_day and s.neighborhood in (None, neighborhood):
                value *= s.multiplier
        return value
