"""Crop physiology: thermal-time stages, water and nitrogen limits, and yield losses.

Daily biomass growth is a potential rate scaled by temperature, water, nitrogen, weeds, pests
and health. Stage-specific damage (flowering sterility, waterlogging, lodging, shattering) is
tracked separately and applied to the grain at harvest. Units: mm of water, kg/ha of
biomass, kg/ha of nitrogen, degree-days above a base temperature.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .weather import Weather


@dataclass(frozen=True, slots=True)
class CropSpec:
    name: str
    base_temp: float
    stages: tuple[tuple[str, float], ...]  # (stage, thermal time at which it begins)
    stage_growth: dict[str, float]  # share of potential growth by stage
    stage_kc: dict[str, float]  # crop coefficient for evapotranspiration
    potential: float  # kg/ha/day of biomass at full growth
    harvest_index: float
    nitrogen_demand: float  # kg/ha/day while growing


RICE = CropSpec(
    "rice",
    10.0,
    (
        ("tillering", 0),
        ("panicle_init", 450),
        ("flowering", 700),
        ("grain_fill", 900),
        ("mature", 1500),
    ),
    {"tillering": 0.6, "panicle_init": 1.0, "flowering": 1.0, "grain_fill": 0.9, "mature": 0.0},
    {"tillering": 1.05, "panicle_init": 1.2, "flowering": 1.2, "grain_fill": 1.0, "mature": 0.8},
    200.0,
    0.45,
    0.9,
)
WHEAT = CropSpec(
    "wheat",
    0.0,
    (
        ("emergence", 0),
        ("tillering", 200),
        ("stem_elongation", 700),
        ("heading", 1200),
        ("grain_fill", 1450),
        ("mature", 2200),
    ),
    {
        "emergence": 0.3,
        "tillering": 0.6,
        "stem_elongation": 1.0,
        "heading": 1.0,
        "grain_fill": 0.9,
        "mature": 0.0,
    },
    {
        "emergence": 0.4,
        "tillering": 0.8,
        "stem_elongation": 1.1,
        "heading": 1.15,
        "grain_fill": 1.0,
        "mature": 0.6,
    },
    75.0,
    0.42,
    0.5,
)
SPECS = {"rice": RICE, "wheat": WHEAT}

VERNALIZATION_DAYS = 40  # days of 0-8 C a winter wheat needs before it can head
FIELD_CAPACITY = 120.0  # mm of plant-available water a wheat soil holds
WATERLOG_MARGIN = 25.0  # mm of ponding above field capacity that suffocates wheat roots
GRAIN_SPOIL = {"paddy_wet": 0.02, "wheat_sheaves": 0.005, "paddy_dry": 0.0005, "rice": 0.0005}


@dataclass(slots=True)
class CropState:
    crop: str
    planted_day: int
    variety: str = "standard"  # wheat: "winter" or "spring"
    gdd: float = 0.0
    biomass: float = 0.0
    health: float = 1.0
    vernalization: float = 0.0
    sterility: float = 0.0
    lodged: bool = False
    waterlog_days: int = 0
    dry_days: int = 0
    days_mature: int = 0
    seedling_age: int = 0  # rice: days in the nursery before transplanting
    density: float = 1.0  # share of the recommended seed rate that was sown
    stage: str = ""
    history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stage = SPECS[self.crop].stages[0][0]


def stage_for(spec: CropSpec, gdd: float) -> str:
    current = spec.stages[0][0]
    for name, start in spec.stages:
        if gdd >= start:
            current = name
    return current


def temperature_factor(crop: str, temp: float) -> float:
    low, opt_low, opt_high, high = (10, 25, 32, 38) if crop == "rice" else (0, 12, 22, 32)
    if temp <= low or temp >= high:
        return 0.0
    if temp < opt_low:
        return (temp - low) / (opt_low - low)
    if temp > opt_high:
        return (high - temp) / (high - opt_high)
    return 1.0


def evapotranspiration(crop: str, stage: str | None, temp: float) -> float:
    """Daily water loss in mm for a standing crop (bare soil loses a quarter of that)."""
    spec = SPECS[crop]
    kc = spec.stage_kc.get(stage or "", 0.25)
    if crop == "rice":
        return kc * (0.15 * max(temp, 0) + 1.2)
    return kc * max(0.5, 0.18 * max(temp, 0) + 0.8)


def water_factor(crop: str, water_mm: float, dry_days: int) -> float:
    if crop == "rice":
        return min(1.0, 0.35 + water_mm / 23.0) * max(0.2, 1 - 0.12 * dry_days)
    return min(1.0, water_mm / 60.0)


def nitrogen_factor(nitrogen: float) -> float:
    return min(1.0, nitrogen / 40.0)


def grow(
    crop: CropState,
    weather: Weather,
    water_mm: float,
    nitrogen: float,
    weeds: float,
    pest: float,
) -> float:
    """Advance one day. Returns the nitrogen taken up."""
    spec = SPECS[crop.crop]
    temp = weather.temp_mean
    if crop.crop == "wheat" and crop.variety == "winter" and 0 <= temp <= 8:
        crop.vernalization += 1

    progress = max(0.0, min(temp, 35 if crop.crop == "rice" else 30) - spec.base_temp)
    # A winter wheat that has not vernalized stalls before heading.
    heading_start = dict(spec.stages).get("heading", float("inf"))
    needs_cold = crop.crop == "wheat" and crop.variety == "winter"
    if needs_cold and crop.vernalization < VERNALIZATION_DAYS:
        progress = min(progress, max(0.0, heading_start - 1 - crop.gdd))
    crop.gdd += progress
    crop.stage = stage_for(spec, crop.gdd)

    if crop.stage == "mature":
        crop.days_mature += 1
        return 0.0

    if water_mm <= 0:
        crop.dry_days += 1
    else:
        crop.dry_days = 0

    factors = min(water_factor(crop.crop, water_mm, crop.dry_days), nitrogen_factor(nitrogen))
    growth = (
        spec.potential
        * spec.stage_growth[crop.stage]
        * temperature_factor(crop.crop, temp)
        * factors
        * (1 - 0.6 * weeds)
        * (1 - 0.5 * pest)
        * crop.health
        * crop.density
    )
    crop.biomass += growth

    if crop.stage == "flowering" and crop.crop == "rice":
        if water_mm < 10:
            crop.sterility += 0.06
        if weather.temp_min < 15:
            crop.sterility += 0.05
        if weather.temp_max > 35:
            crop.sterility += 0.05
    if crop.stage == "heading" and crop.crop == "wheat" and weather.temp_min < 2:
        crop.sterility += 0.06
    crop.sterility = min(0.7, crop.sterility)

    if crop.crop == "rice" and crop.gdd < 150 and water_mm > 90:
        crop.health -= 0.05  # seedlings drowned under deep water
    if crop.crop == "wheat":
        if water_mm > FIELD_CAPACITY + WATERLOG_MARGIN:
            crop.waterlog_days += 1
            crop.health -= 0.04
        if (
            nitrogen > 160
            and weather.rain_mm > 30
            and crop.stage in ("heading", "grain_fill")
            and weather.wind > 0.6
        ):
            crop.lodged = True
    if pest > 0.7:
        crop.health -= 0.02
    if crop.dry_days >= 6:
        crop.health -= 0.03
    crop.health = max(0.0, crop.health)
    return min(nitrogen, spec.nitrogen_demand * spec.stage_growth[crop.stage])


def grain_yield(crop: CropState) -> float:
    """Kilograms of grain per hectare if harvested now."""
    spec = SPECS[crop.crop]
    if crop.stage != "mature":
        return 0.0
    shatter = min(0.6, max(0, crop.days_mature - 10) * 0.015)
    spring_penalty = 0.8 if crop.crop == "wheat" and crop.variety == "spring" else 1.0
    return (
        crop.biomass
        * spec.harvest_index
        * (1 - crop.sterility)
        * (0.75 if crop.lodged else 1.0)
        * (1 - shatter)
        * spring_penalty
    )
