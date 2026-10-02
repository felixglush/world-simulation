"""Configurable worlds and independent policies through the composition facade."""

from dataclasses import replace

import pytest

from station_control.facade import SimulationFacade
from station_control.governors import TradeCommand
from station_control.scenarios import create_world
from station_control.trade import PartLot, observe_world


def facade():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


def test_configured_worlds_trade_with_independent_public_policies():
    class PassivePolicy:
        def __init__(self):
            self.views = []

        def decide(self, observation):
            self.views.append(observation)
            return None

    simulation = facade()
    simulation.create_world("forge", credits=50, lots=(PartLot("forge-lot", 2, 7, "forge"),))
    forge_policy = PassivePolicy()
    simulation.create_decision_system("forge", provider=forge_policy)
    with pytest.raises(ValueError):
        simulation.create_decision_system("station", provider=forge_policy)
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
    assert simulation.state == result.state

    assert [view.world_id for view in forge_policy.views] == ["forge"] * 4
    assert all(
        all(lot.origin_world == view.world_id for lot in view.local_lots)
        and not hasattr(view, "inventories")
        for view in forge_policy.views
    )


def test_duplicate_world_cannot_replace_an_existing_world():
    simulation = facade()
    simulation.create_world("forge", credits=17, lots=(PartLot("forge-lot", 1, 7, "forge"),))
    before = simulation.state

    with pytest.raises(ValueError):
        simulation.create_world("forge", credits=100)
    assert simulation.state == before

    simulation.create_decision_system("forge")
    result = simulation.run(turns=1)
    view = observe_world(result.state, "forge")
    assert view.credits == 17
    assert [(lot.batch_id, lot.quantity) for lot in view.local_lots] == [("forge-lot", 1)]
