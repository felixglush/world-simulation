"""Persistent-cause and terminal-state integration tests."""

from dataclasses import replace

from station_control.scenarios import create_world
from station_control.trade import (
    TradeEvidenceKind,
    advance_world,
    create_world_state,
    purchase_parts,
)


def station(**changes):
    return replace(create_world("normal", 0), scheduled_events=(), **changes)


def advances(state, turns):
    for _ in range(turns):
        state = advance_world(state).state
    return state


def test_uninstalled_defective_inventory_never_causes_an_equipment_failure():
    world = create_world_state(station(parts=0))
    ordered = purchase_parts(world, command_id="unused", quantity=2)

    later = advances(ordered.state, 20)

    assert later.station.parts == 2
    assert not later.station.leak_active
    assert not any(event.kind is TradeEvidenceKind.FAILURE for event in later.evidence)


def test_terminal_station_freezes_trade_and_rejects_new_commitments():
    world = create_world_state(station(crew_alive=False, oxygen=0))

    advanced = advance_world(world)
    rejected = purchase_parts(world, command_id="after-loss", quantity=1)

    assert advanced.state is world
    assert not rejected.accepted
    assert rejected.rejection == "crew_lost"
    assert rejected.state is world
    assert rejected.evidence == ()
