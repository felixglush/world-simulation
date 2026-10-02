"""Governor isolation and failure behavior through the trade application."""

from dataclasses import asdict, replace

from station_control.governors import ScriptedGovernor, TradeCommand
from station_control.scenarios import create_world
from station_control.trade import create_world_state
from station_control.trade_mission import run_trade_mission


def initial_world():
    return create_world_state(replace(create_world("normal", 0), scheduled_events=()))


class RecordingGovernor:
    def __init__(self, command=None):
        self.command = command
        self.observations = []

    def decide(self, observation):
        self.observations.append(observation)
        return self.command


def test_governors_share_an_order_independent_start_of_turn_snapshot():
    station = RecordingGovernor(TradeCommand("purchase", "station-order", quantity=1))
    industrial = RecordingGovernor()
    result = run_trade_mission(
        initial_world(), (("station", station), ("industrial", industrial)), turns=4
    )
    reordered = run_trade_mission(
        initial_world(),
        (
            ("industrial", RecordingGovernor()),
            ("station", RecordingGovernor(TradeCommand("purchase", "station-order", quantity=1))),
        ),
        turns=4,
    )
    assert result == reordered
    assert result.decisions[0].accepted
    assert not industrial.observations[0].contracts
    assert not station.observations[0].contracts
    assert industrial.observations[0].credits == 1000
    assert station.observations[0].credits == 100
    for observation in (station.observations[0], industrial.observations[0]):
        assert "defect" not in repr(asdict(observation))
        assert "scheduled_events" not in repr(asdict(observation))


def test_governor_cannot_spend_another_worlds_funds():
    rogue = ScriptedGovernor(((1, TradeCommand("purchase", "stolen-order", buyer_id="station")),))
    result = run_trade_mission(initial_world(), (("industrial", rogue),), turns=1)
    assert not result.state.contracts
    assert result.state.station.credits == 100
    assert result.decisions[0].rejection == "unauthorized_world"


def test_failed_provider_is_sanitized_and_other_governors_continue():
    class UnavailableGovernor:
        def decide(self, observation):
            raise RuntimeError("private provider detail")

    station = ScriptedGovernor(((1, TradeCommand("purchase", "valid-order")),))
    result = run_trade_mission(
        initial_world(), (("industrial", UnavailableGovernor()), ("station", station)), turns=1
    )
    assert result.state.contracts
    assert any(decision.rejection == "governor_unavailable" for decision in result.decisions)
    assert "private provider detail" not in repr(result.decisions)


def test_recovery_policy_waits_for_a_public_offer_without_fallback():
    from station_control.facade import SimulationFacade

    simulation = SimulationFacade(replace(create_world("normal", 0), scheduled_events=()))
    simulation.create_decision_system("station", kind="recovery")
    result = simulation.run(turns=2)
    assert not result.decisions
    assert not result.state.contracts
