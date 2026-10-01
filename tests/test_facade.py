"""Configurable worlds and independent policies through the composition facade."""

from dataclasses import replace

import pytest

from station_control.facade import SimulationFacade
from station_control.governors import TradeCommand
from station_control.scenarios import create_world
from station_control.trade import PartLot, observe_world


def facade():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


def test_custom_world_and_its_decision_system_participate_in_real_trade():
    simulation = facade()
    simulation.create_world("forge", credits=50, lots=(PartLot("forge-lot", 2, 7, "forge"),))
    simulation.create_decision_system("forge")
    simulation.create_decision_system(
        "station",
        commands=(
            (
                1,
                TradeCommand(
                    "purchase",
                    "from-forge",
                    seller_id="forge",
                    batch_id="forge-lot",
                    quantity=2,
                ),
            ),
        ),
    )
    result = simulation.run(turns=4)
    assert result.state.station.parts == 2
    assert result.state.station.credits == 86
    assert observe_world(result.state, "forge").credits == 64
    assert result.state.contracts[0].status == "settled"
    assert simulation.state is result.state


def test_each_created_decision_system_is_a_distinct_instance():
    simulation = facade()
    simulation.create_world("a")
    simulation.create_world("b")
    first = simulation.create_decision_system("a")
    second = simulation.create_decision_system("b")
    assert first is not second
    assert first.decide(observe_world(simulation.state, "a")) is None


def test_injected_fake_policy_receives_only_its_own_world():
    class FakePolicy:
        def __init__(self):
            self.views = []

        def decide(self, observation):
            self.views.append(observation)
            return None

    simulation = facade()
    simulation.create_world("custom", credits=42)
    fake = FakePolicy()
    simulation.create_decision_system("custom", provider=fake)
    simulation.run(turns=2)
    assert [view.world_id for view in fake.views] == ["custom", "custom"]
    assert all(view.credits == 42 and not hasattr(view, "inventories") for view in fake.views)


@pytest.mark.parametrize(
    "world_id,credits", [("", 1), ("station", 100), ("invalid id", 1), ("x", -1)]
)
def test_world_configuration_is_validated_before_changing_simulation(world_id, credits):
    simulation = facade()
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.create_world(world_id, credits=credits)
    assert simulation.state is before


def test_unknown_world_policy_and_duplicate_world_fail_without_replacing_existing_world():
    simulation = facade()
    with pytest.raises(ValueError):
        simulation.create_decision_system("missing")
    simulation.create_world("forge", credits=17)
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.create_world("forge", credits=100)
    assert simulation.state is before
    with pytest.raises(ValueError):
        simulation.create_decision_system("forge", kind="unavailable")


def test_facade_rejects_shared_mutable_provider_between_worlds():
    class MemoryPolicy:
        def __init__(self):
            self.memory = []

        def decide(self, observation):
            self.memory.append(observation)
            return None

    simulation = facade()
    simulation.create_world("a")
    simulation.create_world("b")
    provider = MemoryPolicy()
    simulation.create_decision_system("a", provider=provider)
    with pytest.raises(ValueError):
        simulation.create_decision_system("b", provider=provider)
    simulation.create_decision_system("b")
    simulation.run(turns=1)
    assert [view.world_id for view in provider.memory] == ["a"]
