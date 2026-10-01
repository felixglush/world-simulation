"""Adversarial cases for persistent causes and terminal transitions."""

from dataclasses import replace

import pytest

from station_control.scenarios import create_world
from station_control.trade import (
    advance_world,
    create_world_state,
    purchase_parts,
    repair_with_batch,
)


def station(**changes):
    return replace(create_world("normal", 0), scheduled_events=(), **changes)


def advances(state, turns):
    for _ in range(turns):
        state = advance_world(state).state
    return state


def test_unused_defective_inventory_never_causes_an_equipment_failure():
    world = create_world_state(station(parts=0))
    ordered = purchase_parts(world, command_id="unused", quantity=2)
    later = advances(ordered.state, 20)
    assert later.station.parts == 2
    assert not later.station.leak_active
    assert not any(event.kind == "failure" for event in later.evidence)


def test_delayed_repair_notification_does_not_delay_physical_installation_or_usage():
    world = create_world_state(
        station(parts=0, leak_active=True, repair_notice_delay_turns=20),
        defect_after_turns=2,
    )
    ordered = purchase_parts(world, command_id="late-notice", quantity=1)
    arrived = advances(ordered.state, 3)
    assigned = repair_with_batch(arrived, "industrial-batch-a")
    completed = advances(assigned.state, 2)
    assert completed.station.repairs_completed == 1
    assert completed.installed_part.batch_id == "industrial-batch-a"
    assert completed.installed_part.operating_turns == 0
    assert not completed.station.leak_active
    failed = advances(completed, 2)
    assert failed.station.leak_active
    assert any(event.kind == "failure" for event in failed.evidence)


def test_terminal_station_freezes_trade_and_rejects_new_commitments():
    world = create_world_state(station(crew_alive=False, oxygen=0))
    assert advance_world(world).state is world
    rejected = purchase_parts(world, command_id="after-loss", quantity=1)
    assert not rejected.accepted
    assert rejected.state is world


@pytest.mark.parametrize(
    "settings",
    [
        {"industrial_parts": -1},
        {"industrial_credits": -1},
        {"bad_unit_price": 0},
        {"reliable_parts": True},
        {"reliable_unit_price": 1.5},
    ],
)
def test_initial_world_rejects_invalid_stock_and_financial_configuration(settings):
    with pytest.raises(ValueError):
        create_world_state(station(), **settings)
