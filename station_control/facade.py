"""Parameter-driven assembly of worlds and independent decision systems."""

from __future__ import annotations

import re
from dataclasses import replace

from .domain import StationState
from .governors import GovernorPolicy, RecoveryGovernor, ScriptedGovernor, TradeCommand
from .trade import PartLot, WorldInventory, create_world_state
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
    ) -> None:
        base = create_world_state(
            station,
            travel_turns=travel_turns,
            shipment_capacity=shipment_capacity,
            industrial_parts=0,
            reliable_parts=0,
            industrial_credits=0,
        )
        self.state = replace(base, inventories=())
        self._governors: dict[str, GovernorPolicy] = {}

    def create_world(
        self,
        world_id: str,
        *,
        credits: int = 100,
        lots: tuple[PartLot, ...] = (),
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
        if not isinstance(lots, tuple) or any(not isinstance(lot, PartLot) for lot in lots):
            raise ValueError("World lots must be a tuple of PartLot values")
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
        inventory = WorldInventory(world_id, credits, lots)
        self.state = replace(self.state, inventories=self.state.inventories + (inventory,))

    def create_decision_system(
        self,
        world_id: str,
        *,
        kind: str = "scripted",
        commands: tuple[tuple[int, TradeCommand], ...] = (),
        provider: GovernorPolicy | None = None,
    ) -> GovernorPolicy:
        if world_id != "station" and not any(
            world.world_id == world_id for world in self.state.inventories
        ):
            raise ValueError("Create the world before its decision system")
        if world_id in self._governors:
            raise ValueError("A world already has a decision system")
        if kind not in {"scripted", "recovery"}:
            raise ValueError("Unsupported decision system")
        if provider is not None:
            if (
                commands
                or kind != "scripted"
                or not callable(getattr(provider, "decide", None))
                or any(provider is existing for existing in self._governors.values())
            ):
                raise ValueError("Supply either a policy provider or policy parameters")
            policy = provider
        elif kind == "recovery":
            if world_id != "station" or commands:
                raise ValueError("Recovery policy controls station equipment")
            policy = RecoveryGovernor()
        else:
            if any(
                type(turn) is not int
                or not 1 <= turn <= 336
                or not isinstance(command, TradeCommand)
                for turn, command in commands
            ):
                raise ValueError("Scripted commands require valid turns and typed commands")
            if len({turn for turn, _ in commands}) != len(commands):
                raise ValueError("Only one scripted command per turn is allowed")
            policy = ScriptedGovernor(commands)
        self._governors[world_id] = policy
        return policy

    def run(self, *, turns: int = 20) -> TradeMissionResult:
        result = run_trade_mission(self.state, tuple(self._governors.items()), turns=turns)
        self.state = result.state
        return result
