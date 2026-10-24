"""Seeded climate: a monsoon-driven temperate year with a cold winter and a seasonal river."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

YEAR = 365
MONSOON = range(160, 271)


@dataclass(frozen=True, slots=True)
class Weather:
    day: int
    temp_mean: float
    temp_min: float
    temp_max: float
    rain_mm: float
    river_mm: float
    wind: float


class Climate:
    """Weather is a pure function of (seed, day), so replays never diverge."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def weather(self, day: int) -> Weather:
        doy = day % YEAR
        rng = random.Random(f"{self.seed}:{day}")
        mean = 16 + 12 * math.sin(2 * math.pi * (doy - 110) / YEAR) + rng.gauss(0, 2.0)
        spread = 5 + rng.random() * 3
        rain_chance, rain_scale = (0.45, 18.0) if doy in MONSOON else (0.12, 6.0)
        rain = rng.expovariate(1 / rain_scale) if rng.random() < rain_chance else 0.0
        if doy in MONSOON and rng.random() < 0.004:
            rain = 120 + rng.random() * 60
        # The river swells with the monsoon and shrinks in winter.
        river = 450 + 500 * max(0.0, math.sin(2 * math.pi * (doy - 135) / YEAR)) + rng.gauss(0, 25)
        if doy in MONSOON:
            river += 200
        return Weather(
            day=day,
            temp_mean=round(mean, 1),
            temp_min=round(mean - spread, 1),
            temp_max=round(mean + spread, 1),
            rain_mm=round(rain, 1),
            river_mm=round(max(150.0, river), 1),
            wind=round(rng.random(), 2),
        )
