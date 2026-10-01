"""Behavioral coverage for private sensor drift and its calibration workflow."""

from dataclasses import asdict, replace

import pytest

from station_control.domain import SensorReading, advance_turn
from station_control.scenarios import create_world
from station_control.sensors import advance_sensor_drift, calibrate_sensors
from station_control.trade import (
    TradeEvidenceKind,
    create_world_state,
    observe_world,
)


def _station(**changes):
    station = replace(create_world("normal", seed=0), scheduled_events=())
    return replace(station, **changes)


def _world(**drift):
    return replace(create_world_state(_station()), **drift)


def _advance_physical_sensors(world):
    """Refresh the physical readings before applying the independent drift layer."""
    return replace(world, station=advance_turn(world.station).state)


def _reading(world, sensor):
    return next(item for item in world.station.sensor_readings if item.sensor == sensor)


def test_new_world_sensors_agree_and_private_drift_settings_stay_out_of_public_view():
    world = _world()

    assert _reading(world, "sensor_a").oxygen == world.station.oxygen
    assert _reading(world, "sensor_b").oxygen == world.station.oxygen

    public = observe_world(world, "station")
    public_data = asdict(public)
    assert "sensor_drift_per_turn" not in public_data
    assert "sensor_drift_bias" not in public_data
    assert "sensor_drift_limit" not in public_data


def test_sensor_a_drift_accumulates_gradually_and_caps_without_changing_truth_or_sensor_b():
    world = _world(sensor_drift_per_turn=3, sensor_drift_limit=7)
    expected_biases = (3, 6, 7, 7)

    for expected_bias in expected_biases:
        fresh = _advance_physical_sensors(world)
        evidence_before = fresh.evidence
        world = advance_sensor_drift(fresh)

        assert world.sensor_drift_bias == expected_bias
        assert world.station.oxygen == fresh.station.oxygen
        assert _reading(world, "sensor_a").oxygen == min(
            fresh.station.oxygen_capacity, fresh.station.oxygen + expected_bias
        )
        assert _reading(world, "sensor_a").sampled_turn == _reading(fresh, "sensor_a").sampled_turn
        assert _reading(world, "sensor_a").source == _reading(fresh, "sensor_a").source
        assert _reading(world, "sensor_b") == _reading(fresh, "sensor_b")
        assert world.evidence == evidence_before
        assert not any(item.finding_code == "sensor_drift_confirmed" for item in world.evidence)


def test_sensor_a_reading_is_clamped_to_physical_capacity():
    world = _world(
        station=_station(oxygen=995),
        sensor_drift_per_turn=20,
        sensor_drift_limit=20,
    )
    fresh = _advance_physical_sensors(world)

    drifted = advance_sensor_drift(fresh)

    assert drifted.station.oxygen == drifted.station.oxygen_capacity
    assert _reading(drifted, "sensor_a").oxygen == drifted.station.oxygen_capacity


@pytest.mark.parametrize(
    ("mode", "fault"),
    [("correlated", None), ("stale", "sensor_a"), ("independent", "sensor_a")],
)
def test_drift_does_not_overwrite_correlated_stale_or_faulted_readings(mode, fault):
    original_a = SensorReading("sensor_a", 701, sampled_turn=2, source="shared:sensor_a")
    original_b = SensorReading("sensor_b", 701, sampled_turn=2, source="shared:sensor_a")
    station = _station(
        turn=5,
        oxygen=700,
        sensor_mode=mode,
        sensor_fault=fault,
        sensor_readings=(original_a, original_b),
    )
    world = replace(create_world_state(station), sensor_drift_per_turn=3, sensor_drift_bias=1)

    result = advance_sensor_drift(world)

    assert result.station.sensor_readings == station.sensor_readings
    assert result.sensor_drift_bias == world.sensor_drift_bias


def test_terminal_world_does_not_accumulate_or_apply_sensor_drift():
    world = _world(
        sensor_drift_per_turn=3,
        sensor_drift_bias=8,
        station=_station(crew_alive=False, oxygen=0),
    )

    assert advance_sensor_drift(world) is world


def test_calibration_confirms_and_clears_real_drift_then_stops_future_drift():
    world = _world(sensor_drift_per_turn=4, sensor_drift_limit=8)
    biased = advance_sensor_drift(_advance_physical_sensors(world))
    biased = advance_sensor_drift(_advance_physical_sensors(biased))
    sensor_b_before = _reading(biased, "sensor_b")
    calibrated = calibrate_sensors(biased)

    assert calibrated.accepted
    assert calibrated.state.sensor_drift_bias == 0
    assert calibrated.state.sensor_drift_per_turn == 0
    assert calibrated.state.station.available_crew == biased.station.available_crew - 1
    assert _reading(calibrated.state, "sensor_a").oxygen == biased.station.oxygen
    assert _reading(calibrated.state, "sensor_a").sampled_turn == biased.station.turn
    assert _reading(calibrated.state, "sensor_a").source == _reading(biased, "sensor_a").source
    assert _reading(calibrated.state, "sensor_b") == sensor_b_before
    assert calibrated.evidence[0].kind is TradeEvidenceKind.CALIBRATION
    assert calibrated.evidence[0].finding_code == "sensor_drift_confirmed"

    later = advance_sensor_drift(_advance_physical_sensors(calibrated.state))
    assert _reading(later, "sensor_a").oxygen == later.station.oxygen
    assert later.sensor_drift_bias == 0


def test_benign_calibration_physically_resets_configured_drift_without_claiming_offset():
    world = _world(sensor_drift_per_turn=2, sensor_drift_limit=20)
    calibrated = calibrate_sensors(world)

    assert calibrated.accepted
    assert calibrated.state.sensor_drift_bias == 0
    assert calibrated.state.sensor_drift_per_turn == 0
    assert calibrated.evidence[0].kind is TradeEvidenceKind.CALIBRATION
    assert calibrated.evidence[0].finding_code == "sensor_calibration_verified"
    assert "no discrepancy was observed" in calibrated.evidence[0].message
    assert _reading(calibrated.state, "sensor_a").oxygen == world.station.oxygen
    assert _reading(calibrated.state, "sensor_a").sampled_turn == world.station.turn


def test_calibration_reports_observed_offset_and_resets_hidden_drift_at_capacity():
    world = _world(
        station=_station(oxygen=995),
        sensor_drift_per_turn=20,
        sensor_drift_limit=20,
    )
    fresh = _advance_physical_sensors(world)
    biased = advance_sensor_drift(fresh)
    assert biased.sensor_drift_bias == 20
    assert biased.station.oxygen == biased.station.oxygen_capacity
    assert _reading(biased, "sensor_a").oxygen == biased.station.oxygen
    sensor_b_before = _reading(biased, "sensor_b")

    calibrated = calibrate_sensors(biased)

    assert calibrated.accepted
    assert calibrated.evidence[0].finding_code == "sensor_calibration_verified"
    assert calibrated.state.sensor_drift_bias == 0
    assert calibrated.state.sensor_drift_per_turn == 0
    assert _reading(calibrated.state, "sensor_a").oxygen == biased.station.oxygen
    assert _reading(calibrated.state, "sensor_a").sampled_turn == biased.station.turn
    assert _reading(calibrated.state, "sensor_a").source == _reading(biased, "sensor_a").source
    assert _reading(calibrated.state, "sensor_b") == sensor_b_before

    later = advance_sensor_drift(_advance_physical_sensors(calibrated.state))
    assert _reading(later, "sensor_a").oxygen == _reading(later, "sensor_b").oxygen


@pytest.mark.parametrize(
    ("mode", "fault"),
    [("correlated", None), ("stale", "sensor_a"), ("independent", "sensor_a")],
)
def test_calibration_rejects_non_independent_or_faulted_sensors_unchanged(mode, fault):
    station = _station(sensor_mode=mode, sensor_fault=fault)
    world = replace(create_world_state(station), sensor_drift_per_turn=3, sensor_drift_bias=6)

    result = calibrate_sensors(world)

    assert not result.accepted
    assert result.rejection == "sensor_mode_incompatible"
    assert result.state is world
    assert result.evidence == ()


def test_benign_calibration_with_no_configured_drift_changes_only_crew_and_evidence():
    world = _world()
    sensor_b_before = _reading(world, "sensor_b")

    calibrated = calibrate_sensors(world)

    assert calibrated.accepted
    assert calibrated.state.sensor_drift_bias == 0
    assert calibrated.state.sensor_drift_per_turn == 0
    assert calibrated.state.station.available_crew == world.station.available_crew - 1
    assert _reading(calibrated.state, "sensor_a").oxygen == world.station.oxygen
    assert _reading(calibrated.state, "sensor_a").sampled_turn == world.station.turn
    assert _reading(calibrated.state, "sensor_b") == sensor_b_before
    assert calibrated.evidence[0].finding_code == "sensor_calibration_verified"


@pytest.mark.parametrize(
    ("station_changes", "expected_rejection"),
    [({"available_crew": 0}, "crew_unavailable"), ({"crew_alive": False}, "crew_lost")],
)
def test_calibration_rejections_leave_world_unchanged(station_changes, expected_rejection):
    world = _world(
        sensor_drift_per_turn=3,
        sensor_drift_bias=6,
        station=_station(**station_changes),
    )

    result = calibrate_sensors(world)

    assert not result.accepted
    assert result.rejection == expected_rejection
    assert result.state is world
    assert result.evidence == ()


def test_real_trade_advance_applies_drift_once_per_turn_and_calibration_removes_cause():
    from station_control.trade import advance_world

    world = _world(sensor_drift_per_turn=4, sensor_drift_limit=20)
    once = advance_world(world).state
    assert _reading(once, "sensor_a").oxygen - _reading(once, "sensor_b").oxygen == 4
    twice = advance_world(once).state
    assert _reading(twice, "sensor_a").oxygen - _reading(twice, "sensor_b").oxygen == 8
    calibrated = calibrate_sensors(twice).state
    later = advance_world(calibrated).state
    assert _reading(later, "sensor_a").oxygen == _reading(later, "sensor_b").oxygen
