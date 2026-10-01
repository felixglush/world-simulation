"""Hidden sensor drift changes station observations until calibration."""

from dataclasses import asdict, replace

from station_control.scenarios import create_world
from station_control.sensors import calibrate_sensors
from station_control.trade import (
    TradeEvidenceKind,
    advance_world,
    create_world_state,
    observe_world,
)


def reading(state, sensor):
    return next(item for item in state.station.sensor_readings if item.sensor == sensor)


def test_real_turns_apply_private_drift_and_station_calibration_only_resets_sensor_a():
    station = replace(create_world("normal", seed=0), scheduled_events=())
    world = replace(create_world_state(station), sensor_drift_per_turn=4, sensor_drift_limit=20)

    public = asdict(observe_world(world, "station"))
    assert "sensor_drift_per_turn" not in public
    assert "sensor_drift_bias" not in public
    assert reading(world, "sensor_a").oxygen == reading(world, "sensor_b").oxygen

    first = advance_world(world).state
    second = advance_world(first).state
    assert reading(first, "sensor_a").oxygen - reading(first, "sensor_b").oxygen == 4
    assert reading(second, "sensor_a").oxygen - reading(second, "sensor_b").oxygen == 8
    assert second.station.oxygen == reading(second, "sensor_b").oxygen
    assert second.sensor_drift_bias == 8
    sensor_b_before = reading(second, "sensor_b")
    source_before = reading(second, "sensor_a").source

    calibrated = calibrate_sensors(second)

    assert calibrated.accepted
    assert calibrated.state.sensor_drift_bias == 0
    assert calibrated.state.sensor_drift_per_turn == 0
    assert calibrated.state.station.oxygen == second.station.oxygen
    assert calibrated.state.station.available_crew == second.station.available_crew - 1
    assert reading(calibrated.state, "sensor_a").oxygen == second.station.oxygen
    assert reading(calibrated.state, "sensor_a").sampled_turn == second.station.turn
    assert reading(calibrated.state, "sensor_a").source == source_before
    assert reading(calibrated.state, "sensor_b") == sensor_b_before
    assert calibrated.evidence[0].kind is TradeEvidenceKind.CALIBRATION
    assert calibrated.evidence[0].finding_code == "sensor_drift_confirmed"

    later = advance_world(calibrated.state).state
    assert reading(later, "sensor_a").oxygen == reading(later, "sensor_b").oxygen
    assert later.sensor_drift_bias == 0


def test_capacity_clamped_drift_calibration_reports_no_observed_discrepancy_and_resets_cause():
    station = replace(
        create_world("normal", seed=0),
        scheduled_events=(),
        oxygen=1000,
    )
    configured = replace(
        create_world_state(station),
        sensor_drift_per_turn=4,
        sensor_drift_bias=6,
        sensor_drift_limit=20,
    )
    world = advance_world(configured).state
    assert world.station.oxygen == 1000
    assert world.sensor_drift_bias == 10
    assert reading(world, "sensor_a").oxygen == reading(world, "sensor_b").oxygen == 1000
    sensor_b_before = reading(world, "sensor_b")
    oxygen_before = world.station.oxygen

    calibrated = calibrate_sensors(world)

    assert calibrated.accepted
    assert calibrated.state.sensor_drift_bias == 0
    assert calibrated.state.sensor_drift_per_turn == 0
    assert calibrated.state.station.oxygen == oxygen_before
    assert calibrated.state.station.available_crew == world.station.available_crew - 1
    assert reading(calibrated.state, "sensor_a").oxygen == oxygen_before
    assert reading(calibrated.state, "sensor_a").sampled_turn == world.station.turn
    assert reading(calibrated.state, "sensor_b") == sensor_b_before
    assert calibrated.evidence[0].finding_code == "sensor_calibration_verified"
