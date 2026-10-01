"""Parameter-driven assembly of worlds and independent decision systems."""

from __future__ import annotations

import re
from dataclasses import replace
from functools import partial

from .domain import StationState
from .economy import (
    ProductionRecipe,
    ResourcePriceRule,
    ResourceStock,
    advance_economy,
    produce_inventory,
    stock_price,
)
from .governors import GovernorPolicy, RecoveryGovernor, ScriptedGovernor, TradeCommand
from .trade import PartLot, WorldInventory, create_world_state, validate_storage_commitments
from .trade_mission import TradeMissionResult, run_trade_mission

_WORLD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class SimulationFacade:
    """Compose a simulation without duplicating world rules or policy implementations."""

    def __init__(
        self,
        station: StationState | None = None,
        *,
        travel_turns: int = 2,
        shipment_capacity: int = 3,
        station_lots: tuple[PartLot, ...] = (),
        station_resource_capacities: tuple[tuple[str, int], ...] = (),
        station_resource_reserves: tuple[tuple[str, int], ...] = (),
    ) -> None:
        _validate_initial_lots(station_lots, "station")
        base = create_world_state(
            station,
            travel_turns=travel_turns,
            shipment_capacity=shipment_capacity,
            industrial_parts=0,
            reliable_parts=0,
            industrial_credits=0,
        )
        all_station_lots = base.station_lots + station_lots
        if len({lot.batch_id for lot in all_station_lots}) != len(all_station_lots):
            raise ValueError("Station lots require unique batch IDs")
        _validate_economy(
            all_station_lots, station_resource_capacities, station_resource_reserves, None, ()
        )
        self.state = replace(
            base,
            inventories=(),
            station_lots=all_station_lots,
            station=replace(
                base.station,
                parts=sum(lot.quantity for lot in all_station_lots if lot.resource == "parts"),
            ),
            station_resource_capacities=station_resource_capacities,
            station_resource_reserves=station_resource_reserves,
        )
        validate_storage_commitments(self.state)
        self._productions: dict[str, ProductionRecipe] = {}
        self._price_rules: dict[str, tuple[ResourcePriceRule, ...]] = {}
        self._governors: dict[str, GovernorPolicy] = {}

    def create_world(
        self,
        world_id: str,
        *,
        credits: int = 100,
        lots: tuple[PartLot, ...] = (),
        resource_capacities: tuple[tuple[str, int], ...] = (),
        resource_reserves: tuple[tuple[str, int], ...] = (),
        production_recipe: ProductionRecipe | None = None,
        price_rules: tuple[ResourcePriceRule, ...] = (),
    ) -> None:
        if (
            not isinstance(world_id, str)
            or not _WORLD_ID.fullmatch(world_id)
            or world_id == "station"
            or any(world.world_id == world_id for world in self.state.inventories)
        ):
            raise ValueError(
                "World ID must be unique and use letters, digits, dots, dashes or underscores"
            )
        if type(credits) is not int or not 0 <= credits <= 1_000_000_000:
            raise ValueError("World credits must be a nonnegative integer")
        _validate_initial_lots(lots, world_id)
        _validate_economy(
            lots, resource_capacities, resource_reserves, production_recipe, price_rules
        )
        inventory = WorldInventory(world_id, credits, lots, resource_capacities, resource_reserves)
        self.state = replace(self.state, inventories=self.state.inventories + (inventory,))
        if production_recipe is not None:
            self._productions[world_id] = production_recipe
        if price_rules:
            self._price_rules[world_id] = price_rules

    def create_decision_system(
        self,
        world_id: str,
        *,
        kind: str = "scripted",
        commands: tuple[tuple[int, TradeCommand], ...] = (),
        provider: GovernorPolicy | None = None,
        resource_targets: tuple[tuple[str, int], ...] = (),
        cash_reserve: int = 0,
        strategy: str = "reserve",
        reference_prices: tuple[tuple[str, str, int], ...] | None = None,
    ) -> GovernorPolicy:
        if world_id != "station" and not any(
            world.world_id == world_id for world in self.state.inventories
        ):
            raise ValueError("Create the world before its decision system")
        if world_id in self._governors:
            raise ValueError("A world already has a decision system")
        if not isinstance(kind, str) or kind not in {"scripted", "recovery", "resources"}:
            raise ValueError("Unsupported decision system")
        has_resource_parameters = (
            resource_targets != ()
            or cash_reserve != 0
            or strategy != "reserve"
            or reference_prices is not None
        )
        if provider is not None:
            if (
                commands != ()
                or has_resource_parameters
                or kind != "scripted"
                or not callable(getattr(provider, "decide", None))
                or any(provider is existing for existing in self._governors.values())
            ):
                raise ValueError("Supply either a policy provider or policy parameters")
            policy = provider
        elif kind == "resources":
            from .economy import ResourceGovernor

            if commands != ():
                raise ValueError("Resource policies do not use scripted commands")
            if reference_prices is None:
                lots = (
                    self.state.station_lots
                    if world_id == "station"
                    else next(
                        world.lots for world in self.state.inventories if world.world_id == world_id
                    )
                )
                benchmarks = {
                    (lot.resource, lot.unit): lot.unit_price for lot in lots if lot.unit_price > 0
                }
                benchmarks.update(
                    {
                        (rule.resource, rule.unit): rule.base_price
                        for rule in self._price_rules.get(world_id, ())
                    }
                )
                reference_prices = tuple(
                    (resource, unit, price)
                    for (resource, unit), price in sorted(benchmarks.items())
                )
            policy = ResourceGovernor(
                world_id, resource_targets, cash_reserve, strategy, reference_prices
            )
        elif kind == "recovery":
            if has_resource_parameters:
                raise ValueError("Resource policy parameters require a resource decision system")
            if world_id != "station" or commands != ():
                raise ValueError("Recovery policy controls station equipment")
            policy = RecoveryGovernor()
        else:
            if has_resource_parameters:
                raise ValueError("Resource policy parameters require a resource decision system")
            _validate_scripted_commands(commands)
            if len({turn for turn, _ in commands}) != len(commands):
                raise ValueError("Only one scripted command per turn is allowed")
            policy = ScriptedGovernor(commands)
        self._governors[world_id] = policy
        return policy

    def run(self, *, turns: int = 20) -> TradeMissionResult:
        settings = {}
        if self._productions or self._price_rules:
            settings["advance"] = partial(
                advance_economy,
                productions=tuple(self._productions.items()),
                price_rules=tuple(
                    (world_id, rule)
                    for world_id, rules in self._price_rules.items()
                    for rule in rules
                ),
            )
        result = run_trade_mission(
            self.state, tuple(self._governors.items()), turns=turns, **settings
        )
        self.state = result.state
        return result


def _validate_economy(lots, capacities, reserves, recipe, price_rules) -> None:
    """Validate configuration before it changes authoritative state or orchestration."""
    for values in (capacities, reserves):
        if (
            not isinstance(values, tuple)
            or any(
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not item[0]
                or type(item[1]) is not int
                or not 0 <= item[1] <= 1_000_000
                for item in values
            )
            or len(dict(values)) != len(values)
        ):
            raise ValueError(
                "Resource limits require unique names and nonnegative integer quantities"
            )
    capacity_map = dict(capacities)
    if any(quantity > capacity_map.get(resource, 1000) for resource, quantity in reserves):
        raise ValueError("Resource reserves cannot exceed storage capacity")
    totals = {}
    units = {}
    for lot in lots:
        if (
            not isinstance(lot.resource, str)
            or not lot.resource
            or not isinstance(lot.unit, str)
            or not lot.unit
            or type(lot.quantity) is not int
            or lot.quantity < 0
            or (lot.resource in units and units[lot.resource] != lot.unit)
        ):
            raise ValueError("Resource lots require nonnegative quantities and consistent units")
        units[lot.resource] = lot.unit
        totals[lot.resource] = totals.get(lot.resource, 0) + lot.quantity
    stocks = tuple(
        ResourceStock(resource, units[resource], quantity, capacity_map.get(resource, 1000))
        for resource, quantity in totals.items()
    )
    if any(stock.quantity > stock.capacity for stock in stocks):
        raise ValueError("Initial resource quantity exceeds storage capacity")
    if recipe is not None:
        if not isinstance(recipe, ProductionRecipe):
            raise ValueError("Production requires a typed recipe")
        if (
            not isinstance(recipe.recipe_id, str)
            or not recipe.recipe_id
            or not _valid_recipe_entries(recipe.inputs)
            or not _valid_recipe_entries(recipe.outputs)
        ):
            raise ValueError("Invalid production configuration")
        result = produce_inventory(stocks, recipe, world_id="configuration")
        if result.rejection in {"invalid_inventory", "invalid_recipe", "missing_resource"}:
            raise ValueError("Invalid production configuration")
    if not isinstance(price_rules, tuple) or any(
        not isinstance(rule, ResourcePriceRule) for rule in price_rules
    ):
        raise ValueError("Prices require typed resource price rules")
    seen_price_resources = set()
    for rule in price_rules:
        if (
            not isinstance(rule.resource, str)
            or not rule.resource
            or not isinstance(rule.unit, str)
            or not rule.unit
        ):
            raise ValueError("Price rules require a resource and unit")
        if rule.resource in seen_price_resources:
            raise ValueError("Only one price rule per resource is allowed")
        seen_price_resources.add(rule.resource)
        if units.get(rule.resource) != rule.unit:
            raise ValueError("Price rule must match a local resource and its unit")
        stock_price(
            totals[rule.resource], rule.target_stock, rule.base_price, rule.floor, rule.ceiling
        )


def _validate_initial_lots(lots: tuple[PartLot, ...], world_id: str) -> None:
    if not isinstance(lots, tuple) or any(not isinstance(lot, PartLot) for lot in lots):
        raise ValueError("Initial lots must be a tuple of PartLot values")
    batches = set()
    for lot in lots:
        if (
            not isinstance(lot.batch_id, str)
            or not lot.batch_id
            or lot.batch_id in batches
            or lot.origin_world != world_id
            or type(lot.quantity) is not int
            or not 0 <= lot.quantity <= 1000
            or type(lot.unit_price) is not int
            or not 1 <= lot.unit_price <= 1_000_000
            or type(lot.latent_defect) is not bool
            or type(lot.defect_after_turns) is not int
            or not 0 <= lot.defect_after_turns <= 100
            or (lot.latent_defect and lot.defect_after_turns == 0)
        ):
            raise ValueError("Invalid initial world lot")
        batches.add(lot.batch_id)


def _validate_scripted_commands(commands: tuple[tuple[int, TradeCommand], ...]) -> None:
    if not isinstance(commands, tuple):
        raise ValueError("Scripted commands require valid turns and typed commands")
    for entry in commands:
        if (
            not isinstance(entry, tuple)
            or len(entry) != 2
            or type(entry[0]) is not int
            or not 1 <= entry[0] <= 336
            or not isinstance(entry[1], TradeCommand)
        ):
            raise ValueError("Scripted commands require valid turns and typed commands")


def _valid_recipe_entries(entries) -> bool:
    return isinstance(entries, tuple) and all(
        isinstance(entry, tuple)
        and len(entry) == 2
        and isinstance(entry[0], str)
        and bool(entry[0])
        and type(entry[1]) is int
        and entry[1] > 0
        for entry in entries
    )
