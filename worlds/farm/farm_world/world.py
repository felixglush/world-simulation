"""The authoritative farm world: terrain, water network, plots, farmers, actions, and the day step.

Agents propose actions through `act`; this module validates them and owns the outcome.
Ground truth (soil nitrogen, pest pressure, vernalization, sterility) never leaves this module;
farmers see only `view`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import crops
from .crops import CropState
from .weather import Climate, Weather

DAILY_LABOR = 10.0
HOUSEHOLD_KG_PER_DAY = 2.4
PLOT_AREA_HA = 0.6
MANURE_NITROGEN = 25.0
WELL_MM_PER_DAY = {"riverbend": 100.0, "highfield": 150.0}
RIVER_DATUM_MM = 4500.0  # river surface elevation when the river stands at 600 mm


@dataclass(slots=True)
class Plot:
    id: str
    crop_kind: str  # "rice" paddy or "wheat" field
    owner: str
    community: str
    x: int
    y: int
    w: int
    h: int
    elevation_mm: float
    bund_mm: float
    water_mm: float = 0.0
    nitrogen: float = 50.0
    weeds: float = 0.1
    pest: float = 0.0
    puddled: bool = False
    tilled: bool = False
    bund_integrity: float = 1.0
    spill_to: str | None = None
    crop: CropState | None = None
    wet_days: int = 0
    harvest_work: float = 0.0
    since_rain: int = 99

    @property
    def holds(self) -> float:
        return self.bund_mm if self.bund_integrity >= 0.5 else min(self.bund_mm, 20.0)


@dataclass(slots=True)
class Channel:
    id: str
    src: str
    dst: str
    capacity_mm: float
    gate_open: bool = False
    silt: float = 0.0
    last_flow: float = 0.0
    last_changed_by: str = ""
    path: tuple[tuple[int, int], ...] = ()


CANAL_STORAGE_MM = 60.0
CANAL_SEEPAGE = 0.15


@dataclass(slots=True)
class CanalNode:
    id: str
    elevation_mm: float
    water_mm: float = 0.0


@dataclass(slots=True)
class Farmer:
    id: str
    community: str
    plot_id: str
    home: tuple[int, int]
    inventory: dict[str, float] = field(default_factory=dict)
    labor: float = DAILY_LABOR
    household: int = 4
    at: tuple[int, int] = (0, 0)
    activity: str = "rest"
    did: str = "rest"
    labor_left: float = DAILY_LABOR
    nursery: dict[str, float] | None = None
    scouted: dict[str, dict[str, Any]] = field(default_factory=dict)
    hunger_days: int = 0

    def has(self, item: str) -> float:
        return self.inventory.get(item, 0.0)

    def add(self, item: str, qty: float) -> None:
        self.inventory[item] = round(self.inventory.get(item, 0.0) + qty, 3)


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


ACTION_PARAMS: dict[str, dict[str, str]] = {
    "rest": {},
    "sow_nursery": {"kg": "num:20:500"},
    "puddle": {"plot": "str"},
    "transplant": {"plot": "str"},
    "till": {"plot": "str"},
    "sow": {"plot": "str", "variety": "variety", "kg": "num:50:500"},
    "weed": {"plot": "str"},
    "fertilize": {"plot": "str", "units": "num:0.5:20"},
    "scout": {"plot": "str"},
    "treat": {"plot": "str"},
    "set_gate": {"channel": "str", "open": "bool"},
    "clear_canal": {"channel": "str"},
    "repair_bund": {"plot": "str"},
    "pump": {"plot": "str", "mm": "num:1:150"},
    "harvest": {"plot": "str"},
    "thresh": {"kg": "num:1:100000"},
    "dry": {"kg": "num:1:100000"},
    "mill": {"item": "item", "kg": "num:1:100000"},
    "save_seed": {"item": "item", "kg": "num:1:100000"},
}
ACTIONS = tuple(ACTION_PARAMS)
MILLABLE = ("paddy_dry", "wheat_grain")


def check_params(name: str, params: dict[str, Any]) -> str | None:
    """The authoritative parameter check: right names, right types, finite and in range."""
    allowed = ACTION_PARAMS[name]
    for key, value in params.items():
        kind = allowed.get(key)
        if kind is None:
            return f"{name} takes {sorted(allowed) or 'no parameters'}, not {key}"
        if kind == "str" and not (isinstance(value, str) and 0 < len(value) <= 64):
            return f"{key} must be a short string"
        if kind == "bool" and type(value) is not bool:
            return f"{key} must be true or false"
        if kind == "variety" and value not in ("winter", "spring"):
            return "variety must be winter or spring"
        if kind == "item" and value not in MILLABLE:
            return f"{key} must be one of {MILLABLE}"
        if kind.startswith("num"):
            _, low, high = kind.split(":")
            number = isinstance(value, (int, float)) and type(value) is not bool
            if not number or not math.isfinite(value) or not float(low) <= value <= float(high):
                return f"{key} must be a number from {low} to {high}"
    return None


class FarmWorld:
    def __init__(
        self,
        seed: int,
        start_day: int,
        plots: list[Plot],
        farmers: list[Farmer],
        canals: list[CanalNode],
        channels: list[Channel],
        tiles: list[list[str]],
    ) -> None:
        self.climate = Climate(seed)
        self.day = start_day
        self.plots = {p.id: p for p in plots}
        self.farmers = {f.id: f for f in farmers}
        self.canal_nodes = {c.id: c for c in canals}
        self.channels = {c.id: c for c in channels}
        self.tiles = tiles
        self.well_used: dict[str, float] = defaultdict(float)
        self.weather: Weather = self.climate.weather(start_day)
        self.events: list[dict[str, Any]] = []
        self._rng_seed = seed
        for farmer in self.farmers.values():
            farmer.at = farmer.home

    # ---- actions -------------------------------------------------------------------------

    def act(self, farmer_id: str, name: str, **params: Any) -> ActionResult:
        farmer = self.farmers.get(farmer_id)
        if farmer is None:
            return ActionResult(False, f"unknown farmer {farmer_id}")
        if name not in ACTIONS:
            return ActionResult(False, f"unknown action {name}")
        error = check_params(name, params)
        try:
            result = (
                ActionResult(False, error)
                if error
                else getattr(self, f"_do_{name}")(farmer, **params)
            )
        except TypeError:
            result = ActionResult(
                False, f"{name} is missing a parameter: {sorted(ACTION_PARAMS[name])}"
            )
        self.events.append(
            {
                "type": "action" if result.ok else "rejected",
                "day": self.day,
                "farmer": farmer_id,
                "action": name,
                "params": params,
                "message": result.message,
            }
        )
        if result.ok:
            farmer.activity = name
        return result

    def _spend(self, farmer: Farmer, cost: float) -> str | None:
        if farmer.labor + 1e-9 < cost:
            return f"needs {cost:g} labor, {farmer.labor:g} left today"
        farmer.labor = round(farmer.labor - cost, 3)
        return None

    def _own_plot(self, farmer: Farmer, plot: str | None) -> Plot | str:
        found = self.plots.get(plot or farmer.plot_id)
        if found is None:
            return f"unknown plot {plot}"
        if found.owner != farmer.id:
            return f"{found.id} belongs to {found.owner}"
        farmer.at = (found.x + found.w // 2, found.y + found.h // 2)
        return found

    def _do_rest(self, farmer: Farmer) -> ActionResult:
        farmer.at = farmer.home
        return ActionResult(True, "rested")

    def _do_sow_nursery(self, farmer: Farmer, kg: float = 30.0) -> ActionResult:
        if farmer.plot_id not in self.plots or self.plots[farmer.plot_id].crop_kind != "rice":
            return ActionResult(False, "only rice farmers keep a nursery")
        if farmer.nursery:
            return ActionResult(False, "a nursery bed is already sown")
        if farmer.has("rice_seed") < kg:
            return ActionResult(False, f"only {farmer.has('rice_seed'):g} kg rice seed")
        if error := self._spend(farmer, 2):
            return ActionResult(False, error)
        farmer.add("rice_seed", -kg)
        farmer.nursery = {"sown": self.day, "kg": kg}
        return ActionResult(True, f"sowed a nursery bed with {kg:g} kg seed")

    def _do_puddle(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if found.crop_kind != "rice":
            return ActionResult(False, "only paddies are puddled")
        if found.crop is not None:
            return ActionResult(False, "the paddy has a crop in it")
        if found.water_mm < 30:
            return ActionResult(
                False, f"puddling needs 30 mm of water, there is {found.water_mm:.0f}"
            )
        if error := self._spend(farmer, 6):
            return ActionResult(False, error)
        found.puddled = True
        found.weeds = 0.05
        return ActionResult(True, "puddled and levelled the paddy; seepage is now low")

    def _do_transplant(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if found.crop_kind != "rice" or found.crop is not None:
            return ActionResult(False, "needs an empty paddy")
        if not found.puddled:
            return ActionResult(False, "puddle the paddy first")
        if found.water_mm < 20:
            return ActionResult(False, "the paddy needs standing water to transplant")
        if not farmer.nursery:
            return ActionResult(False, "no nursery bed")
        age = int(self.day - farmer.nursery["sown"])
        if age < 18:
            return ActionResult(False, f"seedlings are {age} days old; wait until about 18-35")
        if error := self._spend(farmer, 8):
            return ActionResult(False, error)
        state = CropState("rice", self.day, density=min(1.0, farmer.nursery["kg"] / 30.0))
        state.seedling_age = age
        state.health = 1.0 if age <= 35 else 0.85 if age <= 45 else 0.7
        found.crop = state
        farmer.nursery = None
        return ActionResult(True, f"transplanted {age}-day-old seedlings")

    def _do_till(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if found.crop_kind != "wheat" or found.crop is not None:
            return ActionResult(False, "needs an empty wheat field")
        if error := self._spend(farmer, 5):
            return ActionResult(False, error)
        found.tilled = True
        found.weeds = 0.05
        return ActionResult(True, "ploughed and harrowed the field")

    def _do_sow(
        self, farmer: Farmer, plot: str | None = None, variety: str = "winter", kg: float = 70.0
    ) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if found.crop_kind != "wheat" or found.crop is not None:
            return ActionResult(False, "needs an empty wheat field")
        if variety not in ("winter", "spring"):
            return ActionResult(False, "variety must be winter or spring")
        if not found.tilled:
            return ActionResult(False, "till the field first")
        if farmer.has("wheat_seed") < kg:
            return ActionResult(False, f"only {farmer.has('wheat_seed'):g} kg wheat seed")
        if error := self._spend(farmer, 3):
            return ActionResult(False, error)
        farmer.add("wheat_seed", -kg)
        found.crop = CropState("wheat", self.day, variety=variety, density=min(1.0, kg / 70.0))
        return ActionResult(True, f"sowed {variety} wheat")

    def _do_weed(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if error := self._spend(farmer, 3):
            return ActionResult(False, error)
        found.weeds = min(found.weeds, 0.05)
        return ActionResult(True, "weeded the plot")

    def _do_fertilize(
        self, farmer: Farmer, plot: str | None = None, units: float = 1.0
    ) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if units <= 0 or farmer.has("manure") < units:
            return ActionResult(False, f"only {farmer.has('manure'):g} manure")
        if error := self._spend(farmer, 0.5 * units):
            return ActionResult(False, error)
        farmer.add("manure", -units)
        found.nitrogen += MANURE_NITROGEN * units
        return ActionResult(True, f"spread {units:g} manure")

    def _do_scout(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if error := self._spend(farmer, 1):
            return ActionResult(False, error)
        farmer.scouted[found.id] = {
            "day": self.day,
            "pests": _level(found.pest),
            "weeds": _level(found.weeds),
            "soil_nitrogen": "low"
            if found.nitrogen < 40
            else "ok"
            if found.nitrogen < 120
            else "rich",
        }
        return ActionResult(True, "scouted the plot", dict(farmer.scouted[found.id]))

    def _do_treat(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if farmer.has("pest_control") < 1:
            return ActionResult(False, "no pest control left")
        if error := self._spend(farmer, 2):
            return ActionResult(False, error)
        farmer.add("pest_control", -1)
        found.pest *= 0.2
        return ActionResult(True, "treated the plot for pests and disease")

    def _do_set_gate(self, farmer: Farmer, channel: str, open: bool) -> ActionResult:  # noqa: A002
        found = self.channels.get(channel)
        if found is None:
            return ActionResult(False, f"unknown channel {channel}")
        if error := self._spend(farmer, 0.5):
            return ActionResult(False, error)
        found.gate_open = bool(open)
        found.last_changed_by = farmer.id
        if found.path:
            farmer.at = found.path[len(found.path) // 2]
        return ActionResult(True, f"{channel} gate {'opened' if open else 'closed'}")

    def _do_clear_canal(self, farmer: Farmer, channel: str) -> ActionResult:
        found = self.channels.get(channel)
        if found is None:
            return ActionResult(False, f"unknown channel {channel}")
        if error := self._spend(farmer, 4):
            return ActionResult(False, error)
        found.silt = min(found.silt, 0.05)
        if found.path:
            farmer.at = found.path[len(found.path) // 2]
        return ActionResult(True, f"cleared silt from {channel}")

    def _do_repair_bund(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        if error := self._spend(farmer, 3):
            return ActionResult(False, error)
        found.bund_integrity = 1.0
        return ActionResult(True, "rebuilt the bund")

    def _do_pump(self, farmer: Farmer, plot: str | None = None, mm: float = 40.0) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        used = self.well_used[farmer.community]
        if mm <= 0 or used + mm > WELL_MM_PER_DAY[farmer.community]:
            left = WELL_MM_PER_DAY[farmer.community] - used
            return ActionResult(False, f"your community's well has {left:g} mm left today")
        if error := self._spend(farmer, math.ceil(mm / 25)):
            return ActionResult(False, error)
        self.well_used[farmer.community] += mm
        found.water_mm = min(found.water_mm + mm, found.holds)
        return ActionResult(True, f"pumped {mm:g} mm from your community well")

    def _do_harvest(self, farmer: Farmer, plot: str | None = None) -> ActionResult:
        found = self._own_plot(farmer, plot)
        if isinstance(found, str):
            return ActionResult(False, found)
        crop = found.crop
        if crop is None or crop.stage != "mature":
            return ActionResult(False, "the crop is not ripe")
        # Harvest is a multi-day job: labor carries over until the plot is cut.
        cost = 9 + (4 if found.crop_kind == "rice" and found.water_mm > 30 else 0)
        remaining = cost - found.harvest_work
        if farmer.labor <= 0:
            return ActionResult(False, "no labor left today")
        done = min(remaining, farmer.labor)
        farmer.labor = round(farmer.labor - done, 3)
        found.harvest_work += done
        if found.harvest_work + 1e-9 < cost:
            share = found.harvest_work / cost
            return ActionResult(
                True, f"cut part of the crop ({share:.0%} done)", {"progress": share}
            )
        found.harvest_work = 0.0
        wet_loss = 0.92 if found.crop_kind == "rice" and found.water_mm > 30 else 1.0
        kg = round(crops.grain_yield(crop) * PLOT_AREA_HA * wet_loss, 1)
        item = "paddy_wet" if found.crop_kind == "rice" else "wheat_sheaves"
        farmer.add(item, kg)
        found.crop = None
        found.puddled = False
        found.tilled = False
        found.weeds = 0.1
        found.pest *= 0.3
        return ActionResult(True, f"harvested {kg:g} kg of {item}", {"kg": kg, "item": item})

    def _process(
        self,
        farmer: Farmer,
        source: str,
        target: str,
        per_labor: float,
        yield_rate: float,
        kg: float,
        *,
        rainy_blocks: bool = False,
    ) -> ActionResult:
        kg = min(kg, farmer.has(source))
        if kg <= 0:
            return ActionResult(False, f"no {source}")
        if rainy_blocks and self.weather.rain_mm > 2:
            return ActionResult(False, "it is raining; the grain cannot dry today")
        kg = min(kg, per_labor * farmer.labor)
        if kg <= 0:
            return ActionResult(False, "no labor left today")
        if error := self._spend(farmer, kg / per_labor):
            return ActionResult(False, error)
        farmer.add(source, -kg)
        farmer.add(target, kg * yield_rate)
        farmer.at = farmer.home
        return ActionResult(True, f"{source} -> {target}: {kg:g} kg in", {"kg": kg})

    def _do_thresh(self, farmer: Farmer, kg: float = 1e9) -> ActionResult:
        return self._process(farmer, "wheat_sheaves", "wheat_grain", 100, 0.95, kg)

    def _do_dry(self, farmer: Farmer, kg: float = 1e9) -> ActionResult:
        return self._process(farmer, "paddy_wet", "paddy_dry", 400, 0.9, kg, rainy_blocks=True)

    def _do_mill(self, farmer: Farmer, item: str | None = None, kg: float = 1e9) -> ActionResult:
        item = item or ("paddy_dry" if farmer.has("paddy_dry") > 0 else "wheat_grain")
        if item == "paddy_dry":
            return self._process(farmer, "paddy_dry", "rice", 100, 0.65, kg)
        return self._process(farmer, "wheat_grain", "flour", 100, 0.75, kg)

    def _do_save_seed(self, farmer: Farmer, item: str, kg: float = 1e9) -> ActionResult:
        seed = "rice_seed" if item == "paddy_dry" else "wheat_seed"
        return self._process(farmer, item, seed, 200, 1.0, kg)

    # ---- the day step --------------------------------------------------------------------

    def end_day(self) -> None:
        weather = self.weather
        self._water_network(weather)
        for plot in self.plots.values():
            self._plot_day(plot, weather)
        for farmer in self.farmers.values():
            self._household_day(farmer)
        self.events.append(self.snapshot())
        self.day += 1
        self.weather = self.climate.weather(self.day)
        self.well_used.clear()

    def _river_head(self, weather: Weather) -> float:
        return RIVER_DATUM_MM + weather.river_mm - 600.0

    def _head(self, node: str, weather: Weather) -> float:
        if node == "river":
            return self._river_head(weather)
        if node == "drain":
            return 3000.0
        if node in self.plots:
            plot = self.plots[node]
            return plot.elevation_mm + plot.water_mm
        canal = self.canal_nodes[node]
        return canal.elevation_mm + canal.water_mm

    def _water_network(self, weather: Weather) -> None:
        for plot in self.plots.values():
            plot.water_mm += weather.rain_mm
            plot.wet_days = plot.wet_days + 1 if weather.rain_mm >= 2 else 0
            plot.since_rain = 0 if weather.rain_mm >= 2 else plot.since_rain + 1
        for channel in self.channels.values():
            channel.last_flow = 0.0
            if not channel.gate_open:
                continue
            drop = self._head(channel.src, weather) - self._head(channel.dst, weather)
            if drop <= 0:
                continue
            if channel.src == "river":
                available = 1e9
            elif channel.src in self.plots:
                available = self.plots[channel.src].water_mm
            else:
                available = self.canal_nodes[channel.src].water_mm
            flow = min(channel.capacity_mm * (1 - channel.silt), drop / 2, available)
            if flow <= 0:
                continue
            if channel.src in self.plots:
                self.plots[channel.src].water_mm -= flow
            elif channel.src in self.canal_nodes:
                self.canal_nodes[channel.src].water_mm -= flow
            if channel.dst in self.plots:
                self.plots[channel.dst].water_mm += flow
            elif channel.dst in self.canal_nodes:
                self.canal_nodes[channel.dst].water_mm += flow
            channel.last_flow = round(flow, 1)
            if channel.src == "river":
                channel.silt = min(0.9, channel.silt + (0.004 if weather.river_mm > 900 else 0.001))
        for canal in self.canal_nodes.values():
            canal.water_mm = min(CANAL_STORAGE_MM, canal.water_mm) * (1 - CANAL_SEEPAGE)
        for plot in self.plots.values():
            self._overflow(plot)

    def _overflow(self, plot: Plot) -> None:
        excess = plot.water_mm - plot.holds
        if excess <= 0:
            return
        plot.water_mm = plot.holds
        if plot.crop_kind == "rice" and excess > 40:
            plot.bund_integrity = max(0.0, plot.bund_integrity - 0.25)
        lower = self.plots.get(plot.spill_to or "")
        if lower is not None:
            lower.water_mm += excess
            self._overflow(lower)

    def _plot_day(self, plot: Plot, weather: Weather) -> None:
        crop = plot.crop
        stage = crop.stage if crop else None
        loss = crops.evapotranspiration(plot.crop_kind, stage, weather.temp_mean)
        if plot.crop_kind == "rice":
            plot.water_mm = max(0.0, plot.water_mm - loss - (2.0 if plot.puddled else 6.0))
        else:
            plot.water_mm = max(0.0, plot.water_mm - loss)
            if plot.water_mm > crops.FIELD_CAPACITY:
                plot.water_mm -= 0.4 * (plot.water_mm - crops.FIELD_CAPACITY)
        plot.nitrogen += 0.2
        wet = plot.water_mm > 50 if plot.crop_kind == "rice" else weather.rain_mm >= 2
        weed_rate = 0.004 if plot.crop_kind == "rice" and plot.water_mm > 50 else 0.012
        plot.weeds = min(1.0, plot.weeds + weed_rate * (1 if weather.temp_mean > 8 else 0.2))
        if plot.crop_kind == "rice":
            humid = plot.crop is not None and plot.since_rain <= 2
            warm = 22 <= weather.temp_mean <= 32
            grows = warm and humid
            plot.pest = min(1.0, max(0.0, plot.pest + (0.015 if grows else -0.006)))
        else:
            cool_wet = wet and 10 <= weather.temp_mean <= 22 and plot.wet_days >= 2
            plot.pest = min(1.0, max(0.0, plot.pest + (0.04 if cool_wet and plot.crop else -0.01)))
        if crop is not None:
            plot.nitrogen -= crops.grow(
                crop, weather, plot.water_mm, plot.nitrogen, plot.weeds, plot.pest
            )
            plot.nitrogen = max(0.0, plot.nitrogen)

    def _household_day(self, farmer: Farmer) -> None:
        for item, rate in crops.GRAIN_SPOIL.items():
            if farmer.has(item):
                farmer.inventory[item] = round(farmer.inventory[item] * (1 - rate), 3)
        need = HOUSEHOLD_KG_PER_DAY * farmer.household / 4
        for item in sorted(("rice", "flour"), key=farmer.has, reverse=True):
            take = min(need, farmer.has(item))
            farmer.add(item, -take)
            need -= take
        farmer.hunger_days = farmer.hunger_days + 1 if need > 0.01 else 0
        farmer.did, farmer.labor_left = farmer.activity, farmer.labor
        farmer.labor = DAILY_LABOR * (0.7 if farmer.hunger_days else 1.0)
        farmer.activity = "rest"

    # ---- observation ---------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Ground truth for one day, for the viewer and the audit; never shown to a farmer."""
        return {
            "type": "day",
            "day": self.day,
            "weather": {
                "temp": self.weather.temp_mean,
                "rain": self.weather.rain_mm,
                "river": self.weather.river_mm,
            },
            "plots": {
                p.id: {
                    "water": round(p.water_mm, 1),
                    "crop": p.crop.crop if p.crop else None,
                    "stage": p.crop.stage if p.crop else "fallow",
                    "health": round(p.crop.health, 2) if p.crop else None,
                    "bund": p.bund_integrity,
                    "puddled": p.puddled,
                    "tilled": p.tilled,
                }
                for p in self.plots.values()
            },
            "channels": {
                c.id: {"open": c.gate_open, "flow": c.last_flow, "silt": round(c.silt, 2)}
                for c in self.channels.values()
            },
            "farmers": {
                f.id: {"at": list(f.at), "activity": f.did, "labor": f.labor_left}
                for f in self.farmers.values()
            },
        }

    def view(self, farmer_id: str) -> dict[str, Any]:
        """What one farmer can see and feel today. Hides exact nitrogen, pests and cold hours."""
        farmer = self.farmers[farmer_id]
        weather = self.weather
        plots = {p.id: self._plot_view(p, farmer) for p in self.plots.values()}
        food = farmer.has("rice") + farmer.has("flour")
        return {
            "date": _date(self.day),
            "weather": {
                "temp_c": weather.temp_mean,
                "low_c": weather.temp_min,
                "rain_mm": weather.rain_mm,
                "wind": "strong" if weather.wind > 0.7 else "light",
            },
            "river": _river_word(weather.river_mm),
            "labor_left": farmer.labor,
            "me": {
                "id": farmer.id,
                "community": farmer.community,
                "plot": farmer.plot_id,
                "inventory": {k: round(v, 1) for k, v in sorted(farmer.inventory.items()) if v},
                "food_days_left": round(food / (HOUSEHOLD_KG_PER_DAY * farmer.household / 4), 1),
                "hungry": farmer.hunger_days > 0,
                "nursery_age_days": (
                    int(self.day - farmer.nursery["sown"]) if farmer.nursery else None
                ),
            },
            "plots": plots,
            "channels": {
                c.id: {
                    "open": c.gate_open,
                    "silty": c.silt > 0.4,
                    "last_changed_by": c.last_changed_by,
                }
                for c in self.channels.values()
            },
            "well_water_left_mm": WELL_MM_PER_DAY[farmer.community]
            - self.well_used[farmer.community],
            "neighbors": sorted(
                f.id
                for f in self.farmers.values()
                if f.community == farmer.community and f.id != farmer.id
            ),
        }

    def _plot_view(self, plot: Plot, viewer: Farmer) -> dict[str, Any]:
        crop = plot.crop
        mine = plot.owner == viewer.id
        seen: dict[str, Any] = {
            "owner": plot.owner,
            "kind": plot.crop_kind,
            "crop_stage": crop.stage if crop else "empty",
            "water_depth_cm" if plot.crop_kind == "rice" else "soil": (
                round(plot.water_mm / 10, 1)
                if plot.crop_kind == "rice"
                else "dry"
                if plot.water_mm < 30
                else "moist"
                if plot.water_mm < 100
                else "wet"
            ),
            "bund": "broken" if plot.bund_integrity < 0.5 else "ok",
        }
        if crop is not None:
            seen["leaves"] = (
                "healthy" if crop.health > 0.85 else "yellowing" if crop.health > 0.5 else "dying"
            )
            seen["days_since_planting"] = int(self.day - crop.planted_day)
        if mine:
            seen["weeds_visible"] = _level(plot.weeds)
            seen["puddled" if plot.crop_kind == "rice" else "tilled"] = (
                plot.puddled if plot.crop_kind == "rice" else plot.tilled
            )
            if plot.id in viewer.scouted:
                seen["last_scout"] = viewer.scouted[plot.id]
        return seen


_MONTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_MONTH_NAMES = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _date(day: int) -> str:
    left = day % 365
    for name, length in zip(_MONTH_NAMES, _MONTHS, strict=True):
        if left < length:
            return f"{name} {left + 1}"
        left -= length
    return "Dec 31"


def _level(value: float) -> str:
    return (
        "none" if value < 0.1 else "some" if value < 0.35 else "heavy" if value < 0.7 else "severe"
    )


def _river_word(mm: float) -> str:
    return "low" if mm < 500 else "normal" if mm < 900 else "high" if mm < 1300 else "flooding"


def community_members(world: FarmWorld) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for farmer in world.farmers.values():
        groups[farmer.community].append(farmer.id)
    return dict(groups)
