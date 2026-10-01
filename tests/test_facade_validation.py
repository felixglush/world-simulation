"""Failure behavior for parameter-driven simulation composition."""

from dataclasses import replace

import pytest

from station_control.economy import ProductionRecipe, ResourcePriceRule
from station_control.facade import SimulationFacade
from station_control.governors import TradeCommand
from station_control.scenarios import create_world
from station_control.trade import PartLot, observe_world


def facade():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


class RecordingPolicy:
    def __init__(self):
        self.world_ids = []

    def decide(self, observation):
        self.world_ids.append(observation.world_id)
        return None


@pytest.mark.parametrize(
    "commands",
    [
        None,
        (None,),
        ((1, TradeCommand("wait"), "extra"),),
        ((1, object()),),
        ((True, TradeCommand("wait")),),
    ],
)
def test_malformed_scripted_schedules_fail_without_registering_a_policy(commands):
    simulation = facade()
    simulation.create_world("forge")

    with pytest.raises(ValueError, match="Scripted commands"):
        simulation.create_decision_system("forge", commands=commands)

    policy = simulation.create_decision_system("forge")
    assert policy.decide(observe_world(simulation.state, "forge")) is None


def test_duplicate_scripted_turns_fail_without_registering_a_policy():
    simulation = facade()
    simulation.create_world("forge")
    commands = ((1, TradeCommand("wait")), (1, TradeCommand("wait")))

    with pytest.raises(ValueError, match="one scripted command per turn"):
        simulation.create_decision_system("forge", commands=commands)

    simulation.create_decision_system("forge")


@pytest.mark.parametrize(
    ("kind", "world_id", "parameters"),
    [
        ("scripted", "forge", {"resource_targets": (("water", 1),)}),
        ("scripted", "forge", {"cash_reserve": 1}),
        ("scripted", "forge", {"strategy": "cheapest"}),
        ("recovery", "station", {"resource_targets": (("water", 1),)}),
        ("recovery", "station", {"cash_reserve": 1}),
        ("recovery", "station", {"strategy": "cheapest"}),
    ],
)
def test_non_resource_policy_rejects_resource_parameters_without_registration(
    kind, world_id, parameters
):
    simulation = facade()
    if world_id != "station":
        simulation.create_world(world_id)

    with pytest.raises(ValueError, match="parameters"):
        simulation.create_decision_system(world_id, kind=kind, **parameters)

    simulation.create_decision_system(world_id, kind=kind)


@pytest.mark.parametrize(
    "parameters",
    [
        {"commands": ((1, TradeCommand("wait")),)},
        {"resource_targets": (("water", 1),)},
        {"cash_reserve": 1},
        {"strategy": "cheapest"},
    ],
)
def test_injected_policy_cannot_be_combined_with_builtin_policy_parameters(parameters):
    simulation = facade()
    simulation.create_world("forge")
    provider = RecordingPolicy()

    with pytest.raises(ValueError, match="provider or policy parameters"):
        simulation.create_decision_system("forge", provider=provider, **parameters)

    policy = simulation.create_decision_system("forge")
    assert policy is not provider


@pytest.mark.parametrize(
    "station_lots",
    [
        (PartLot("negative-price", 1, -1, "station"),),
        (PartLot("negative-quantity", -1, 1, "station"),),
        (
            PartLot("water-tonnes", 1, 1, "station", resource="water", unit="tonnes"),
            PartLot("water-litres", 1, 1, "station", resource="water", unit="litres"),
        ),
        (
            PartLot("same-batch", 1, 1, "station"),
            PartLot("same-batch", 1, 1, "station"),
        ),
    ],
)
def test_invalid_station_initial_lots_are_rejected(station_lots):
    with pytest.raises(ValueError):
        SimulationFacade(
            replace(create_world("normal", 0), scheduled_events=(), parts=0),
            station_lots=station_lots,
        )


@pytest.mark.parametrize(
    "configuration",
    [
        {"resource_capacities": (("water", True),)},
        {
            "resource_capacities": (("water", 1),),
            "resource_reserves": (("water", 2),),
        },
        {
            "lots": (PartLot("water", 1, 1, "candidate", resource="water", unit="tonnes"),),
            "price_rules": (ResourcePriceRule([], "tonnes", 1, 1, 1, 1),),
        },
        {"production_recipe": ProductionRecipe("broken", None, (("water", 1),))},
        {"production_recipe": ProductionRecipe("missing-stock", (), (("water", 1),))},
    ],
)
def test_invalid_world_economy_configuration_is_atomic(configuration):
    simulation = facade()
    simulation.create_world("existing")
    provider = RecordingPolicy()
    simulation.create_decision_system("existing", provider=provider)
    before = simulation.state

    with pytest.raises(ValueError):
        simulation.create_world("candidate", **configuration)

    assert simulation.state is before
    with pytest.raises(ValueError, match="Create the world"):
        simulation.create_decision_system("candidate")

    simulation.create_world("candidate")
    simulation.create_decision_system("candidate")
    simulation.run(turns=1)
    assert provider.world_ids == ["existing"]
