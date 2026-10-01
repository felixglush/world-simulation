"""Small deterministic production and pricing rules for finite world inventories."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .governors import TradeCommand
from .trade import MAX_TRADE_QUANTITY, MAX_UNIT_PRICE, LedgerEntry

if TYPE_CHECKING:
    from .trade import PartLot, PublicWorldView, WorldAdvanceResult, WorldState


@dataclass(frozen=True, slots=True)
class ResourceStock:
    """A finite quantity stored in an explicit unit, such as tonnes or crates."""

    resource: str
    unit: str
    quantity: int
    capacity: int


@dataclass(frozen=True, slots=True)
class ProductionRecipe:
    """A deterministic conversion of finite inputs into finite outputs."""

    recipe_id: str
    inputs: tuple[tuple[str, int], ...]
    outputs: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ProductionResult:
    inventory: tuple[ResourceStock, ...]
    ledger: tuple[LedgerEntry, ...]
    rejection: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejection is None


@dataclass(frozen=True, slots=True)
class ResourcePriceRule:
    resource: str
    unit: str
    target_stock: int
    base_price: int
    floor: int
    ceiling: int


@dataclass(frozen=True, slots=True)
class ResourceGovernor:
    """Choose bounded resource purchases from one world's public market view."""

    world_id: str
    reserves: tuple[tuple[str, int], ...]
    cash_reserve: int = 0
    strategy: str = "reserve"
    reference_prices: tuple[tuple[str, str, int], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.world_id, str) or not self.world_id:
            raise ValueError("A resource governor needs a world ID")
        if not isinstance(self.strategy, str) or self.strategy not in {
            "reserve",
            "income",
            "reliable",
        }:
            raise ValueError("Unknown resource governor strategy")
        if type(self.cash_reserve) is not int or self.cash_reserve < 0:
            raise ValueError("Cash reserve must be a nonnegative integer")
        if not isinstance(self.reserves, tuple):
            raise ValueError("Resource targets must be a tuple")
        if any(not isinstance(item, tuple) or len(item) != 2 for item in self.reserves):
            raise ValueError("Resource targets require name/quantity pairs")
        names: set[str] = set()
        for resource, target in self.reserves:
            if (
                not isinstance(resource, str)
                or not resource
                or resource in names
                or type(target) is not int
                or target < 0
            ):
                raise ValueError("Resource targets must have unique names and nonnegative integers")
            names.add(resource)
        if not isinstance(self.reference_prices, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 3
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], str)
            or not item[1]
            or type(item[2]) is not int
            or item[2] <= 0
            for item in self.reference_prices
        ):
            raise ValueError("Reference prices require resource, unit, and positive integer price")
        if len({(resource, unit) for resource, unit, _ in self.reference_prices}) != len(
            self.reference_prices
        ):
            raise ValueError("Reference prices must be unique per resource and unit")

    def decide(self, observation: PublicWorldView) -> TradeCommand | None:
        if observation.world_id != self.world_id or not observation.crew_alive:
            return None

        current = {
            resource: sum(
                lot.quantity for lot in observation.local_lots if lot.resource == resource
            )
            for resource, _ in self.reserves
        }
        pending = {
            resource: sum(
                contract.quantity
                for contract in observation.contracts
                if contract.buyer_id == self.world_id
                and contract.resource == resource
                and contract.status in {"booked", "in_transit"}
            )
            for resource, _ in self.reserves
        }
        bad_batches, bad_sellers = _known_unreliable_partners(observation)
        on_time_sellers = {
            shipment.origin
            for shipment in observation.shipments
            if shipment.status == "delivered"
            and shipment.arrival_turn is not None
            and shipment.arrival_turn <= observation.turn
        }
        late_sellers = {
            shipment.origin
            for shipment in observation.shipments
            if shipment.status == "in_transit"
            and shipment.arrival_turn is not None
            and shipment.arrival_turn < observation.turn
        }
        base_prices = {(resource, unit): price for resource, unit, price in self.reference_prices}

        for resource, target in self.reserves:
            shortage = target - current[resource] - pending[resource]
            if shortage <= 0:
                continue
            known_units = {
                lot.unit for lot in observation.local_lots if lot.resource == resource
            } | {
                contract.unit
                for contract in observation.contracts
                if contract.buyer_id == self.world_id
                and contract.resource == resource
                and contract.status in {"booked", "in_transit"}
            }
            candidates = []
            for offer in observation.offers:
                seller = offer.seller_world or offer.origin_world
                if (
                    offer.resource != resource
                    or not seller
                    or seller == self.world_id
                    or type(offer.quantity) is not int
                    or offer.quantity <= 0
                    or type(offer.unit_price) is not int
                    or offer.unit_price <= 0
                    or (known_units and offer.unit not in known_units)
                ):
                    continue
                if self.strategy == "income" and offer.unit_price > base_prices.get(
                    (resource, offer.unit), 0
                ):
                    continue
                if self.strategy == "reliable" and (
                    offer.batch_id in bad_batches or seller in bad_sellers or seller in late_sellers
                ):
                    continue
                reliability_rank = 0 if seller in on_time_sellers else 1
                candidates.append(
                    (reliability_rank, offer.unit_price, seller, offer.batch_id, offer)
                )
            if not candidates:
                continue
            if self.strategy == "reliable":
                selected = min(candidates, key=lambda row: row[:4])[-1]
            else:
                selected = min(candidates, key=lambda row: row[1:4])[-1]
            seller = selected.seller_world or selected.origin_world
            spendable = max(0, observation.credits - self.cash_reserve)
            quantity = min(
                shortage,
                selected.quantity,
                MAX_TRADE_QUANTITY,
                spendable // selected.unit_price,
            )
            if quantity <= 0:
                continue
            command_identity = _command_fragment(f"{self.world_id}:{resource}")
            command_id = f"resource:{command_identity}:{observation.turn}"[:64]
            return TradeCommand(
                "purchase",
                command_id,
                buyer_id=self.world_id,
                seller_id=seller,
                batch_id=selected.batch_id,
                quantity=quantity,
            )
        return None


ICE_MOON_RECIPE = ProductionRecipe("melt_ice", (("ice", 4),), (("water", 4),))
AGRICULTURAL_WORLD_RECIPE = ProductionRecipe(
    "grow_food", (("water", 3), ("fertilizer", 1)), (("food", 4),)
)

RESOURCE_PROFILES: tuple[tuple[str, tuple[ResourceStock, ...]], ...] = (
    (
        "station",
        (
            ResourceStock("water", "tonnes", 10, 100),
            ResourceStock("food", "crates", 0, 40),
        ),
    ),
    ("industrial", (ResourceStock("fertilizer", "tonnes", 50, 100),)),
    (
        "ice_moon",
        (
            ResourceStock("ice", "tonnes", 100, 160),
            ResourceStock("water", "tonnes", 0, 80),
        ),
    ),
    (
        "agricultural_world",
        (
            ResourceStock("water", "tonnes", 20, 100),
            ResourceStock("fertilizer", "tonnes", 12, 60),
            ResourceStock("food", "crates", 0, 100),
        ),
    ),
)
RESOURCE_PRICE_RULES: tuple[ResourcePriceRule, ...] = (
    ResourcePriceRule("water", "tonnes", 40, 10, 2, 30),
    ResourcePriceRule("fertilizer", "tonnes", 30, 5, 2, 20),
    ResourcePriceRule("food", "crates", 30, 4, 1, 15),
)


def initial_resource_inventory(world_id: str) -> tuple[ResourceStock, ...]:
    """Return the authored finite stocks for one supported world."""
    for identity, inventory in RESOURCE_PROFILES:
        if identity == world_id:
            return inventory
    raise ValueError(f"unknown world: {world_id}")


def advance_economy(
    state: WorldState,
    *,
    productions: tuple[tuple[str, ProductionRecipe], ...] = (),
    price_rules: tuple[tuple[str, ResourcePriceRule], ...] = (),
) -> WorldAdvanceResult:
    """Advance trade/delivery first, then produce and reprice before governors observe."""
    from .trade import WorldAdvanceResult, advance_world

    if not state.station.crew_alive:
        return advance_world(state)

    identities = [world_id for world_id, _ in productions]
    if len(identities) != len(set(identities)):
        raise ValueError("Each world may have at most one production recipe")
    price_keys = [(world_id, rule.resource) for world_id, rule in price_rules]
    if len(price_keys) != len(set(price_keys)):
        raise ValueError("Each world may have one price rule per resource")

    advanced = advance_world(state)
    updated = advanced.state
    known_worlds = {"station", *(inventory.world_id for inventory in updated.inventories)}
    if not known_worlds.issuperset(identities + [world_id for world_id, _ in price_rules]):
        raise ValueError("Economy configuration refers to an unknown world")

    for world_id, recipe in sorted(productions, key=lambda item: item[0]):
        stocks = _world_resource_projection(updated, world_id, recipe, price_rules)
        result = produce_inventory(
            stocks,
            recipe,
            world_id=world_id,
            turn=updated.station.turn,
            ledger_sequence=max((entry.sequence for entry in updated.ledger), default=0) + 1,
        )
        if not result.accepted:
            continue
        updated = _apply_resource_projection(updated, world_id, result.inventory, recipe)
        updated = replace(updated, ledger=updated.ledger + result.ledger)

    for world_id, rule in sorted(price_rules, key=lambda item: (item[0], item[1].resource)):
        lots = _economy_lots(updated, world_id)
        resource_lots = [lot for lot in lots if lot.resource == rule.resource]
        if not resource_lots:
            continue
        if any(lot.unit != rule.unit for lot in resource_lots):
            raise ValueError("Price rule unit must match the resource inventory unit")
        total_stock = sum(lot.quantity for lot in resource_lots)
        price = stock_price(
            total_stock,
            rule.target_stock,
            rule.base_price,
            rule.floor,
            rule.ceiling,
        )
        changed = tuple(
            replace(lot, unit_price=price) if lot.resource == rule.resource else lot for lot in lots
        )
        updated = _replace_economy_lots(updated, world_id, changed)

    return WorldAdvanceResult(updated, advanced.evidence)


def produce_inventory(
    inventory: tuple[ResourceStock, ...],
    recipe: ProductionRecipe,
    *,
    world_id: str,
    turn: int = 0,
    ledger_sequence: int = 1,
) -> ProductionResult:
    """Apply one all-or-nothing recipe and record every resource movement."""
    if (
        not isinstance(world_id, str)
        or not world_id
        or type(turn) is not int
        or turn < 0
        or type(ledger_sequence) is not int
        or ledger_sequence < 1
        or not _valid_inventory(inventory)
    ):
        return ProductionResult(inventory, (), "invalid_inventory")

    requirements = _resource_quantities(recipe.inputs)
    outputs = _resource_quantities(recipe.outputs)
    if not recipe.recipe_id or requirements is None or outputs is None or not outputs:
        return ProductionResult(inventory, (), "invalid_recipe")

    stocks = {stock.resource: stock for stock in inventory}
    if any(resource not in stocks for resource in requirements | outputs):
        return ProductionResult(inventory, (), "missing_resource")
    if any(stocks[resource].quantity < quantity for resource, quantity in requirements.items()):
        return ProductionResult(inventory, (), "insufficient_inputs")

    projected = {resource: stock.quantity for resource, stock in stocks.items()}
    for resource, quantity in requirements.items():
        projected[resource] -= quantity
    for resource, quantity in outputs.items():
        projected[resource] += quantity
    if any(projected[resource] > stocks[resource].capacity for resource in outputs):
        return ProductionResult(inventory, (), "storage_capacity")

    updated = tuple(replace(stock, quantity=projected[stock.resource]) for stock in inventory)
    ledger = tuple(
        LedgerEntry(
            sequence=ledger_sequence + index,
            turn=turn,
            account=f"inventory:{world_id}:{resource}",
            resource=resource,
            delta=delta,
            kind=kind,
            reference_id=recipe.recipe_id,
        )
        for index, (kind, resource, delta) in enumerate(
            (kind, resource, -quantity if kind == "production_input" else quantity)
            for kind, amounts in (
                ("production_input", requirements),
                ("production_output", outputs),
            )
            for resource, quantity in sorted(amounts.items())
        )
    )
    return ProductionResult(updated, ledger)


def stock_price(
    stock: int,
    target_stock: int,
    base_price: int,
    floor: int,
    ceiling: int,
) -> int:
    """Return an integer unit price bounded by scarcity and the supplied limits."""
    if (
        any(type(value) is not int for value in (stock, target_stock, base_price, floor, ceiling))
        or stock < 0
        or target_stock <= 0
        or base_price <= 0
        or floor <= 0
        or ceiling < floor
        or ceiling > MAX_UNIT_PRICE
        or not floor <= base_price <= ceiling
    ):
        raise ValueError("invalid stock pricing parameters")
    scarcity_price = base_price * target_stock // max(stock, 1)
    return min(ceiling, max(floor, scarcity_price))


def _known_unreliable_partners(observation: PublicWorldView) -> tuple[set[str], set[str]]:
    contracts = {contract.contract_id: contract for contract in observation.contracts}
    bad_batches: set[str] = set()
    bad_sellers: set[str] = set()
    for evidence in observation.evidence:
        if evidence.finding_code != "material_defect_confirmed":
            continue
        if evidence.batch_id:
            bad_batches.add(evidence.batch_id)
        contract = contracts.get(evidence.contract_id)
        if contract is not None:
            bad_sellers.add(contract.seller_id)
    return bad_batches, bad_sellers


def _command_fragment(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
    )[:38]


def _valid_inventory(inventory: tuple[ResourceStock, ...]) -> bool:
    seen: set[str] = set()
    for stock in inventory:
        if (
            not isinstance(stock.resource, str)
            or not stock.resource
            or not isinstance(stock.unit, str)
            or not stock.unit
            or stock.resource in seen
            or type(stock.quantity) is not int
            or type(stock.capacity) is not int
            or stock.quantity < 0
            or stock.capacity < 0
            or stock.quantity > stock.capacity
        ):
            return False
        seen.add(stock.resource)
    return True


def _resource_quantities(entries: tuple[tuple[str, int], ...]) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for resource, quantity in entries:
        if (
            not isinstance(resource, str)
            or not resource
            or type(quantity) is not int
            or quantity <= 0
        ):
            return None
        totals[resource] = totals.get(resource, 0) + quantity
    return totals


def _economy_lots(state: WorldState, world_id: str) -> tuple[PartLot, ...]:
    if world_id == "station":
        return state.station_lots
    inventory = next(item for item in state.inventories if item.world_id == world_id)
    return inventory.lots


def _world_resource_projection(
    state: WorldState,
    world_id: str,
    recipe: ProductionRecipe,
    price_rules: tuple[tuple[str, ResourcePriceRule], ...],
) -> tuple[ResourceStock, ...]:
    lots = _economy_lots(state, world_id)
    resources = sorted({resource for resource, _ in recipe.inputs + recipe.outputs})
    capacities = (
        dict(state.station_resource_capacities)
        if world_id == "station"
        else dict(
            next(
                item for item in state.inventories if item.world_id == world_id
            ).resource_capacities
        )
    )
    rules = {rule.resource: rule for rule_world, rule in price_rules if rule_world == world_id}
    from .trade import incoming_quantity

    incoming = {resource: incoming_quantity(state, world_id, resource) for resource in resources}
    result = []
    for resource in resources:
        matching = [lot for lot in lots if lot.resource == resource]
        units = {lot.unit for lot in matching}
        rule = rules.get(resource)
        if not units and rule is not None:
            units.add(rule.unit)
        unit = next(iter(units)) if len(units) == 1 else ""
        result.append(
            ResourceStock(
                resource,
                unit,
                sum(lot.quantity for lot in matching),
                max(0, capacities.get(resource, 1000) - incoming[resource]),
            )
        )
    return tuple(result)


def _apply_resource_projection(
    state: WorldState,
    world_id: str,
    stocks: tuple[ResourceStock, ...],
    recipe: ProductionRecipe,
) -> WorldState:
    from .trade import PartLot

    lots = list(_economy_lots(state, world_id))
    for stock in stocks:
        indexes = [index for index, lot in enumerate(lots) if lot.resource == stock.resource]
        current = sum(lots[index].quantity for index in indexes)
        if stock.quantity < current:
            remaining = current - stock.quantity
            for index in indexes:
                lot = lots[index]
                used = min(lot.quantity, remaining)
                lots[index] = replace(lot, quantity=lot.quantity - used)
                remaining -= used
                if remaining == 0:
                    break
        elif stock.quantity > current:
            quantity = stock.quantity - current
            batch_id = f"production:{world_id}:{recipe.recipe_id}:{stock.resource}"
            existing = next(
                (index for index, lot in enumerate(lots) if lot.batch_id == batch_id), None
            )
            if existing is None:
                lots.append(
                    PartLot(
                        batch_id,
                        quantity,
                        1,
                        world_id,
                        resource=stock.resource,
                        unit=stock.unit,
                    )
                )
            else:
                lots[existing] = replace(
                    lots[existing], quantity=lots[existing].quantity + quantity
                )
    return _replace_economy_lots(state, world_id, tuple(lots))


def _replace_economy_lots(
    state: WorldState, world_id: str, lots: tuple[PartLot, ...]
) -> WorldState:
    if world_id == "station":
        return replace(
            state,
            station_lots=lots,
            station=replace(
                state.station,
                parts=sum(lot.quantity for lot in lots if lot.resource == "parts"),
            ),
        )
    inventories = tuple(
        replace(inventory, lots=lots) if inventory.world_id == world_id else inventory
        for inventory in state.inventories
    )
    return replace(state, inventories=inventories)
