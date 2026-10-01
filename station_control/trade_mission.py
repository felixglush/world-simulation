"""Offline trade missions: policies propose, the domain owns every transition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

from .governors import GovernorPolicy, TradeCommand
from .scenarios import create_world
from .trade import (
    TradeEvidence,
    WorldAdvanceResult,
    WorldState,
    activate_world_backup,
    advance_world,
    inspect_installed_batch,
    observe_world,
    purchase_lot,
    quarantine_batch,
    repair_with_batch,
    set_operating_load,
)


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    turn: int
    world_id: str
    command: TradeCommand | None
    accepted: bool
    rejection: str | None = None


@dataclass(frozen=True, slots=True)
class TradeMissionResult:
    state: WorldState
    events: tuple[TradeEvidence, ...]
    decisions: tuple[GovernorDecision, ...]


def run_trade_mission(
    state: WorldState,
    governors: tuple[tuple[str, GovernorPolicy], ...],
    *,
    turns: int = 20,
    advance: Callable[[WorldState], WorldAdvanceResult] = advance_world,
) -> TradeMissionResult:
    """Advance time, observe all governors, then resolve proposals in world-ID order."""
    if type(turns) is not int or not 1 <= turns <= 336:
        raise ValueError("Trade mission duration must be between 1 and 336 turns")
    identities = [world_id for world_id, _ in governors]
    if len(identities) != len(set(identities)):
        raise ValueError("Each world must have one governor")
    if len({id(policy) for _, policy in governors}) != len(governors):
        raise ValueError("Each world must have an independent governor instance")
    from .deception import trace_report
    from .quality import assay_batch, consume_feedstock
    from .sensors import calibrate_sensors
    from .trade_types import TradeEvidenceKind, record_evidence

    events = []
    decisions = []
    for _ in range(turns):
        advanced = advance(state)
        state = advanced.state
        events.extend(advanced.evidence)
        if not state.station.crew_alive:
            break
        # Materialize every observation before any proposal is applied.
        snapshots = [
            (world_id, policy, observe_world(state, world_id))
            for world_id, policy in sorted(governors, key=lambda item: item[0])
        ]
        proposals = []
        for world_id, policy, snapshot in snapshots:
            try:
                proposals.append((world_id, policy.decide(snapshot)))
            except Exception:
                decisions.append(
                    GovernorDecision(
                        state.station.turn, world_id, None, False, "governor_unavailable"
                    )
                )
        for world_id, command in proposals:
            if command is None:
                continue
            if not isinstance(command, TradeCommand):
                decisions.append(
                    GovernorDecision(state.station.turn, world_id, None, False, "invalid_command")
                )
                continue
            if command.buyer_id != world_id or (
                command.kind != "purchase" and world_id != "station"
            ):
                decisions.append(
                    GovernorDecision(
                        state.station.turn, world_id, command, False, "unauthorized_world"
                    )
                )
                continue
            if command.kind == "purchase":
                result = purchase_lot(
                    state,
                    command_id=command.command_id,
                    quantity=command.quantity,
                    batch_id=command.batch_id or "industrial-batch-a",
                    buyer_id=world_id,
                    seller_id=command.seller_id,
                )
            elif command.kind == "repair":
                result = repair_with_batch(
                    state,
                    command.batch_id,
                    mode=command.repair_mode,
                    shipment_id=command.shipment_id,
                )
            elif command.kind == "inspect":
                result = inspect_installed_batch(state, method=command.method)
            elif command.kind == "quarantine":
                result = quarantine_batch(
                    state,
                    command.batch_id,
                    quantity=command.quantity,
                    shipment_id=command.shipment_id,
                )
            elif command.kind == "trace":
                result = trace_report(state, command.report_id, world_id=world_id)
            elif command.kind == "calibrate":
                result = calibrate_sensors(state)
            elif command.kind == "assay":
                result = assay_batch(state, command.batch_id, shipment_id=command.shipment_id)
            elif command.kind == "consume":
                result = consume_feedstock(
                    state,
                    command.batch_id,
                    quantity=command.quantity,
                    shipment_id=command.shipment_id,
                )
            elif command.kind == "backup":
                result = activate_world_backup(state)
            elif command.kind == "load":
                result = set_operating_load(state, command.operating_load)
            else:
                decisions.append(
                    GovernorDecision(
                        state.station.turn, world_id, command, False, "invalid_command"
                    )
                )
                continue
            state = result.state
            events.extend(result.evidence)
            if not result.accepted:
                state, feedback = record_evidence(
                    state,
                    TradeEvidenceKind.COMMAND_REJECTED,
                    f"{command.kind} command rejected: {result.rejection}.",
                    world_id,
                    finding_code=(
                        "sensor_calibration_unavailable"
                        if command.kind == "calibrate"
                        and result.rejection == "sensor_mode_incompatible"
                        else "repair_not_needed"
                        if command.kind == "repair" and result.rejection == "repair_not_needed"
                        else "command_rejected"
                    ),
                )
                events.extend(feedback)
            decisions.append(
                GovernorDecision(
                    state.station.turn, world_id, command, result.accepted, result.rejection
                )
            )
    return TradeMissionResult(state, tuple(events), tuple(decisions))


def run_trade_story(*, turns: int = 20) -> TradeMissionResult:
    from .facade import SimulationFacade
    from .trade import PartLot

    station = replace(create_world("normal", 0), parts=0, leak_active=True, scheduled_events=())
    simulation = SimulationFacade(station)
    simulation.create_world(
        "industrial",
        credits=1000,
        lots=(
            PartLot(
                "industrial-batch-a", 2, 15, "industrial", latent_defect=True, defect_after_turns=3
            ),
            PartLot("industrial-batch-b", 2, 30, "industrial"),
        ),
    )
    simulation.create_decision_system("station", kind="recovery")
    simulation.create_decision_system("industrial")
    return simulation.run(turns=turns)


def trade_story_summary(result: TradeMissionResult) -> dict[str, object]:
    return {
        "controller": "scripted",
        "model_calls": 0,
        "turns_completed": result.state.station.turn,
        "crew_alive": result.state.station.crew_alive,
        "repairs_completed": result.state.station.repairs_completed,
        "leak_active": result.state.station.leak_active,
        "events": [asdict(event) for event in result.events],
        "decisions": [asdict(decision) for decision in result.decisions],
    }


def run_economy_story(*, turns: int = 20) -> TradeMissionResult:
    """Assemble four complementary economies using only facade parameters."""
    from .economy import (
        AGRICULTURAL_WORLD_RECIPE,
        ICE_MOON_RECIPE,
        RESOURCE_PRICE_RULES,
        initial_resource_inventory,
    )
    from .facade import SimulationFacade
    from .trade import PartLot

    prices = {rule.resource: rule.base_price for rule in RESOURCE_PRICE_RULES}

    def lots_for(world_id, stocks):
        return tuple(
            PartLot(
                f"{world_id}-{stock.resource}",
                stock.quantity,
                prices.get(stock.resource, 1),
                world_id,
                resource=stock.resource,
                unit=stock.unit,
            )
            for stock in stocks
        )

    station_stocks = initial_resource_inventory("station")
    station = replace(create_world("normal", 0), credits=500, parts=0, scheduled_events=())
    simulation = SimulationFacade(
        station,
        station_lots=lots_for("station", station_stocks),
        station_resource_capacities=tuple(
            (stock.resource, stock.capacity) for stock in station_stocks
        ),
        station_resource_reserves=(("water", 5), ("food", 12)),
    )
    recipes = {"ice_moon": ICE_MOON_RECIPE, "agricultural_world": AGRICULTURAL_WORLD_RECIPE}
    reserves = {
        "industrial": (("fertilizer", 10),),
        "ice_moon": (("water", 8),),
        "agricultural_world": (("food", 8),),
    }
    for world_id, credits in (("industrial", 300), ("ice_moon", 200), ("agricultural_world", 500)):
        stocks = initial_resource_inventory(world_id)
        resources = {stock.resource for stock in stocks}
        simulation.create_world(
            world_id,
            credits=credits,
            lots=lots_for(world_id, stocks),
            resource_capacities=tuple((stock.resource, stock.capacity) for stock in stocks),
            resource_reserves=reserves[world_id],
            production_recipe=recipes.get(world_id),
            price_rules=tuple(rule for rule in RESOURCE_PRICE_RULES if rule.resource in resources),
        )
    simulation.create_decision_system(
        "station",
        kind="resources",
        resource_targets=(("food", 12),),
        cash_reserve=100,
        strategy="reliable",
    )
    simulation.create_decision_system(
        "industrial",
        kind="resources",
        resource_targets=(("food", 6),),
        cash_reserve=100,
        strategy="reserve",
    )
    simulation.create_decision_system(
        "agricultural_world",
        kind="resources",
        resource_targets=(("water", 30), ("fertilizer", 15)),
        cash_reserve=50,
        strategy="income",
    )
    simulation.create_decision_system("ice_moon")
    return simulation.run(turns=turns)


def economy_story_summary(result: TradeMissionResult) -> dict[str, object]:
    summary = trade_story_summary(result)
    worlds = ("station", *(inventory.world_id for inventory in result.state.inventories))
    ledger = result.state.ledger
    production_batches = {
        (entry.turn, world_id, entry.reference_id)
        for entry in ledger
        if entry.kind == "production_output"
        for world_id in worlds
        if entry.account.startswith(f"inventory:{world_id}:")
    }
    summary.update(
        {
            "economy": True,
            "worlds": list(worlds),
            "production_batches_completed": len(production_batches),
            "contracts_settled": sum(
                contract.status == "settled" for contract in result.state.contracts
            ),
            "expenditure": {
                world_id: sum(
                    -entry.delta
                    for entry in ledger
                    if entry.account == f"credits:{world_id}"
                    and entry.resource == "credits"
                    and entry.kind == "purchase"
                    and entry.delta < 0
                )
                for world_id in worlds
            },
            "revenue": {
                world_id: sum(
                    entry.delta
                    for entry in ledger
                    if entry.account == f"credits:{world_id}"
                    and entry.resource == "credits"
                    and entry.kind == "settlement"
                    and entry.delta > 0
                )
                for world_id in worlds
            },
        }
    )
    return summary
