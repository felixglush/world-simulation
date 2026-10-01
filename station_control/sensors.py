"""Station sensor drift and calibration rules for the trade-world domain."""

from __future__ import annotations

from dataclasses import replace

from .trade_types import (
    TradeEvidenceKind,
    TradeResult,
    WorldState,
)
from .trade_types import (
    record_evidence as _emit_evidence,
)
from .trade_types import (
    reject_trade as _rejected,
)


def advance_sensor_drift(state: WorldState) -> WorldState:
    """Apply the configured slow offset to a freshly refreshed independent sensor A.

    The physical station advance refreshes healthy readings before this transition.
    Rebuild the displayed sensor A value from physical oxygen plus the cumulative
    offset instead of layering it over a possibly biased reading. Each invocation
    advances the offset once, so callers apply it once per physical turn. Other
    sensor modes retain their original readings and metadata.
    """
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return state
    if state.station.sensor_mode != "independent" or state.station.sensor_fault is not None:
        return state

    sensor_a = next(
        (reading for reading in state.station.sensor_readings if reading.sensor == "sensor_a"),
        None,
    )
    if sensor_a is None:
        return state

    drift_limit = max(0, state.sensor_drift_limit)
    current_bias = max(0, state.sensor_drift_bias)
    drift_per_turn = max(0, state.sensor_drift_per_turn)
    bias = min(drift_limit, current_bias + drift_per_turn)
    oxygen = min(
        state.station.oxygen_capacity,
        max(0, state.station.oxygen + bias),
    )
    updated_readings = tuple(
        replace(reading, oxygen=oxygen) if reading.sensor == "sensor_a" else reading
        for reading in state.station.sensor_readings
    )
    if bias == state.sensor_drift_bias and updated_readings == state.station.sensor_readings:
        return state
    return replace(
        state,
        sensor_drift_bias=bias,
        station=replace(state.station, sensor_readings=updated_readings),
    )


def calibrate_sensors(state: WorldState) -> TradeResult:
    """Spend crew to compare a healthy independent pair and recalibrate sensor A."""
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if state.station.available_crew <= 0:
        return _rejected(state, "crew_unavailable")
    if state.station.sensor_mode != "independent" or state.station.sensor_fault is not None:
        return _rejected(state, "sensor_mode_incompatible")

    sensor_a = next(
        (reading for reading in state.station.sensor_readings if reading.sensor == "sensor_a"),
        None,
    )
    sensor_b = next(
        (reading for reading in state.station.sensor_readings if reading.sensor == "sensor_b"),
        None,
    )
    if sensor_a is None or sensor_b is None:
        return _rejected(state, "sensor_unavailable")

    oxygen = min(
        state.station.oxygen_capacity,
        max(0, state.station.oxygen),
    )
    updated_readings = tuple(
        replace(reading, oxygen=oxygen, sampled_turn=state.station.turn)
        if reading.sensor == "sensor_a"
        else reading
        for reading in state.station.sensor_readings
    )
    station = replace(
        state.station,
        available_crew=state.station.available_crew - 1,
        sensor_readings=updated_readings,
    )
    measured_offset = sensor_a.oxygen - oxygen
    drift_confirmed = measured_offset != 0
    updated = replace(
        state,
        station=station,
        sensor_drift_bias=0,
        sensor_drift_per_turn=0,
    )
    finding_code = "sensor_drift_confirmed" if drift_confirmed else "sensor_calibration_verified"
    message = (
        f"Independent calibration measured sensor_a at {sensor_a.oxygen} units against "
        f"{oxygen} units on the physical gauge and confirmed a {measured_offset:+d} unit offset; "
        "sensor_a was recalibrated."
        if drift_confirmed
        else f"Independent calibration measured sensor_a at {sensor_a.oxygen} units against "
        f"{oxygen} units on the physical gauge; no discrepancy was observed and sensor_a was "
        "recalibrated."
    )
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.CALIBRATION,
        message,
        "station",
        asset_id="sensor_a",
        finding_code=finding_code,
    )
    return TradeResult(updated, True, evidence=evidence)
