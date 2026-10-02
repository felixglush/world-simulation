"""Caller-visible validation behavior for simulation composition."""

from dataclasses import replace

import pytest

from station_control.economy import ProductionRecipe
from station_control.facade import SimulationFacade
from station_control.scenarios import create_world
from station_control.trade import observe_world


def facade():
    return SimulationFacade(replace(create_world("normal", 0), scheduled_events=(), parts=0))


def test_malformed_scripted_schedule_does_not_register_a_policy():
    simulation = facade()
    simulation.create_world("forge")

    with pytest.raises(ValueError, match="Scripted commands"):
        simulation.create_decision_system("forge", commands=((1, object()),))

    simulation.create_decision_system("forge")
    result = simulation.run(turns=1)

    assert result.state.station.turn == 1


def test_invalid_economy_configuration_does_not_register_a_world_or_policy():
    simulation = facade()
    before = simulation.state

    with pytest.raises(ValueError, match="Invalid production configuration"):
        simulation.create_world(
            "candidate",
            production_recipe=ProductionRecipe("missing-stock", (), (("water", 1),)),
        )

    assert simulation.state is before
    with pytest.raises(ValueError, match="Create the world"):
        simulation.create_decision_system("candidate")

    simulation.create_world("candidate")
    simulation.create_decision_system("candidate")
    result = simulation.run(turns=1)

    assert observe_world(result.state, "candidate").world_id == "candidate"
