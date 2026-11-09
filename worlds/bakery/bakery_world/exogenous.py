import math
import random
from dataclasses import dataclass
from typing import Protocol

from bakery_world.basics import Ingredient, NeighborhoodId, date_of


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
    Ingredient.FLOUR: 220,
    Ingredient.BUTTER: 1700,
    Ingredient.EGGS: 700,
    Ingredient.SUGAR: 250,
    Ingredient.DAIRY: 230,
    Ingredient.COFFEE: 4100,
    Ingredient.CHOCOLATE: 2500,
    Ingredient.FRUIT: 800,
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
        self.seed, self.horizon_days, self.spikes = seed, horizon_days, spikes
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


# mid-month foot traffic, January first: a December peak, a January dip, a softer summer
MONTH_TRAFFIC = (0.91, 0.95, 0.99, 1.01, 1.02, 1.0, 0.96, 0.95, 1.0, 1.02, 1.04, 1.12)
WEEKDAY_TRAFFIC = (1.0, 1.0, 1.0, 1.02, 1.05, 1.05, 0.88)


def season(day: int) -> float:
    months = (date_of(day).timetuple().tm_yday - 15) / (365 / 12)
    i, frac = int(math.floor(months)) % 12, months - math.floor(months)
    return MONTH_TRAFFIC[i] + frac * (MONTH_TRAFFIC[(i + 1) % 12] - MONTH_TRAFFIC[i])


@dataclass(frozen=True)
class SeasonalFootTraffic:
    shocks: tuple[TrafficShock, ...] = ()

    def multiplier(self, neighborhood: NeighborhoodId, day: int) -> float:
        value = WEEKDAY_TRAFFIC[day % 7] * season(day)
        for s in self.shocks:
            if s.start_day <= day < s.end_day and s.neighborhood in (None, neighborhood):
                value *= s.multiplier
        return value
