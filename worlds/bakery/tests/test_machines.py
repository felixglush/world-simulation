import pytest

from bakery_world import machines as m
from bakery_world.machines import MACHINES, IllegalTransition, Transition, advance, step

KE, BE, CE, QE = m.CompetitorEvent, m.CurveballEvent, m.CustomerEvent, m.EquipmentEvent
AE, LE, ME, RE, TE = m.LeaseEvent, m.LocationEvent, m.MenuEvent, m.RunEvent, m.StaffEvent

LEGAL_PATHS = {
    "location": [LE.SIGN_LEASE, LE.START_FITOUT, LE.OPEN_DOORS]
    + [LE.ANNOUNCE_CLOSING, LE.SHUT_DOORS],
    "lease": [AE.OPEN_WINDOW, AE.RENEW, AE.START_NEW_TERM, AE.OPEN_WINDOW, AE.LAPSE],
    "staff": [
        TE.INTERVIEW,
        TE.OFFER,
        TE.ACCEPT,
        TE.FALL_SICK,
        TE.RECOVER,
        TE.GIVE_NOTICE,
        TE.LEAVE,
    ],
    "equipment": [QE.WEAR_OUT, QE.BREAK_DOWN, QE.START_REPAIR, QE.FINISH_REPAIR, QE.BREAK_DOWN]
    + [QE.REPLACE],
    "menu_item": [ME.START_TRIAL, ME.ADD_TO_MENU, ME.RETIRE],
    "customer": [CE.VISIT_REPEATEDLY, CE.GO_QUIET, CE.RETURN, CE.GO_QUIET, CE.GIVE_UP],
    "curveball": [BE.BEGIN, BE.ASK_FOR_RESPONSE, BE.TIME_OUT],
    "competitor": [KE.OPEN_DOORS, KE.SHUT_DOWN],
    "run": [RE.START, RE.SUBMIT_TURN, RE.FINISH_WEEK, RE.SUBMIT_TURN, RE.INTERRUPT]
    + [RE.RESUME, RE.FINISH_YEAR],
}


def test_every_machine_has_a_legal_path():
    assert set(LEGAL_PATHS) == set(MACHINES)


@pytest.mark.parametrize("name", MACHINES)
def test_terminal_states_have_no_exits(name):
    machine = MACHINES[name]
    sources = {state for state, _ in machine.table}
    assert machine.terminal.isdisjoint(sources)
    assert set(machine.states) - machine.terminal <= sources


@pytest.mark.parametrize("name", MACHINES)
def test_every_pair_outside_the_table_raises(name):
    machine = MACHINES[name]
    illegal = [
        (s, e) for s in machine.states for e in machine.events if (s, e) not in machine.table
    ]
    assert illegal
    for state, event in illegal:
        with pytest.raises(IllegalTransition):
            advance(machine.table, state, event)


@pytest.mark.parametrize("name", MACHINES)
def test_legal_path_walks_from_first_state_to_a_terminal_state(name):
    machine = MACHINES[name]
    state = next(iter(machine.states))
    for event in LEGAL_PATHS[name]:
        state = advance(machine.table, state, event)
    assert state in machine.terminal


def test_step_appends_to_the_log():
    log: list[Transition] = []
    c = MACHINES["customer"]
    after = step(log, 7, c, "c1", c.states.NEW, CE.VISIT_REPEATEDLY)
    assert after == c.states.REGULAR
    assert log == [Transition(7, "customer", "c1", "VISIT_REPEATEDLY", "NEW", "REGULAR")]


def test_step_on_illegal_pair_logs_nothing():
    log: list[Transition] = []
    s = MACHINES["staff"]
    with pytest.raises(IllegalTransition):
        step(log, 1, s, "s1", s.states.GONE, TE.ACCEPT)
    assert log == []
