from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum, auto


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class Transition:
    day: int
    entity: str
    entity_id: str
    event: str
    before: str
    after: str


def advance[S, E](table: Mapping[tuple[S, E], S], state: S, event: E) -> S:
    try:
        return table[(state, event)]
    except KeyError:
        raise IllegalTransition(f"{event} is not allowed in {state}") from None


@dataclass(frozen=True)
class Machine[S: Enum, E: Enum]:
    entity: str
    table: Mapping[tuple[S, E], S]
    terminal: frozenset[S]
    states: type[S]
    events: type[E]


def step[S: Enum, E: Enum](
    log: list[Transition], day: int, machine: Machine[S, E], entity_id: str, state: S, event: E
) -> S:
    after = advance(machine.table, state, event)
    log.append(Transition(day, machine.entity, entity_id, event.name, state.name, after.name))
    return after


class LocationState(StrEnum):
    SCOUTED = auto()
    LEASED = auto()
    FITTING_OUT = auto()
    TRADING = auto()
    CLOSING = auto()
    CLOSED = auto()


class LocationEvent(StrEnum):
    SIGN_LEASE = auto()
    PASS_ON_SITE = auto()
    START_FITOUT = auto()
    WALK_AWAY = auto()
    OPEN_DOORS = auto()
    ANNOUNCE_CLOSING = auto()
    SHUT_DOORS = auto()


class LeaseState(StrEnum):
    ACTIVE = auto()
    RENEWAL_WINDOW = auto()
    RENEWED = auto()
    EXPIRED = auto()


class LeaseEvent(StrEnum):
    OPEN_WINDOW = auto()
    RENEW = auto()
    START_NEW_TERM = auto()
    LAPSE = auto()
    TERMINATE = auto()


class StaffState(StrEnum):
    CANDIDATE = auto()
    INTERVIEWED = auto()
    OFFERED = auto()
    EMPLOYED = auto()
    SICK = auto()
    ON_NOTICE = auto()
    GONE = auto()


class StaffEvent(StrEnum):
    INTERVIEW = auto()
    OFFER = auto()
    ACCEPT = auto()
    REJECT = auto()
    DECLINE = auto()
    FALL_SICK = auto()
    RECOVER = auto()
    GIVE_NOTICE = auto()
    RETRACT_NOTICE = auto()
    LEAVE = auto()


class EquipmentState(StrEnum):
    WORKING = auto()
    WORN = auto()
    BROKEN = auto()
    REPAIRING = auto()
    REPLACED = auto()


class EquipmentEvent(StrEnum):
    WEAR_OUT = auto()
    SERVICE = auto()
    BREAK_DOWN = auto()
    START_REPAIR = auto()
    FINISH_REPAIR = auto()
    REPLACE = auto()


class MenuState(StrEnum):
    CATALOG = auto()
    TRIAL = auto()
    ON_MENU = auto()
    RETIRED = auto()


class MenuEvent(StrEnum):
    START_TRIAL = auto()
    ADD_TO_MENU = auto()
    DROP_AFTER_TRIAL = auto()
    RETIRE = auto()


class CustomerState(StrEnum):
    NEW = auto()
    REGULAR = auto()
    LAPSED = auto()
    CHURNED = auto()


class CustomerEvent(StrEnum):
    VISIT_REPEATEDLY = auto()
    GO_QUIET = auto()
    RETURN = auto()
    GIVE_UP = auto()


class CurveballState(StrEnum):
    FORESHADOWED = auto()
    ACTIVE = auto()
    AWAITING_RESPONSE = auto()
    RESOLVED = auto()
    EXPIRED = auto()


class CurveballEvent(StrEnum):
    BEGIN = auto()
    FIZZLE = auto()
    ASK_FOR_RESPONSE = auto()
    RUN_ITS_COURSE = auto()
    RESPOND = auto()
    TIME_OUT = auto()


class CompetitorState(StrEnum):
    RUMORED = auto()
    OPEN = auto()
    CLOSED = auto()


class CompetitorEvent(StrEnum):
    OPEN_DOORS = auto()
    RUMOR_FADES = auto()
    SHUT_DOWN = auto()


class RunState(StrEnum):
    ONBOARDING = auto()
    WEEKLY_TURN = auto()
    SIMULATING = auto()
    INTERRUPTED = auto()
    SCORED = auto()
    BANKRUPT = auto()
    STOPPED = auto()


class RunEvent(StrEnum):
    START = auto()
    SUBMIT_TURN = auto()
    FINISH_WEEK = auto()
    INTERRUPT = auto()
    RESUME = auto()
    FINISH_YEAR = auto()
    GO_BANKRUPT = auto()
    STOP = auto()


L, LE = LocationState, LocationEvent
LOCATION = Machine(
    "location",
    {
        (L.SCOUTED, LE.SIGN_LEASE): L.LEASED,
        (L.SCOUTED, LE.PASS_ON_SITE): L.CLOSED,
        (L.LEASED, LE.START_FITOUT): L.FITTING_OUT,
        (L.LEASED, LE.WALK_AWAY): L.CLOSED,
        (L.FITTING_OUT, LE.OPEN_DOORS): L.TRADING,
        (L.FITTING_OUT, LE.WALK_AWAY): L.CLOSED,
        (L.TRADING, LE.ANNOUNCE_CLOSING): L.CLOSING,
        (L.CLOSING, LE.SHUT_DOORS): L.CLOSED,
    },
    frozenset({L.CLOSED}),
    L,
    LE,
)

A, AE = LeaseState, LeaseEvent
LEASE = Machine(
    "lease",
    {
        (A.ACTIVE, AE.OPEN_WINDOW): A.RENEWAL_WINDOW,
        (A.ACTIVE, AE.TERMINATE): A.EXPIRED,
        (A.RENEWAL_WINDOW, AE.RENEW): A.RENEWED,
        (A.RENEWAL_WINDOW, AE.LAPSE): A.EXPIRED,
        (A.RENEWED, AE.START_NEW_TERM): A.ACTIVE,
    },
    frozenset({A.EXPIRED}),
    A,
    AE,
)

T, TE = StaffState, StaffEvent
STAFF = Machine(
    "staff",
    {
        (T.CANDIDATE, TE.INTERVIEW): T.INTERVIEWED,
        (T.CANDIDATE, TE.REJECT): T.GONE,
        (T.INTERVIEWED, TE.OFFER): T.OFFERED,
        (T.INTERVIEWED, TE.REJECT): T.GONE,
        (T.OFFERED, TE.ACCEPT): T.EMPLOYED,
        (T.OFFERED, TE.DECLINE): T.GONE,
        (T.EMPLOYED, TE.FALL_SICK): T.SICK,
        (T.EMPLOYED, TE.GIVE_NOTICE): T.ON_NOTICE,
        (T.SICK, TE.RECOVER): T.EMPLOYED,
        (T.SICK, TE.GIVE_NOTICE): T.ON_NOTICE,
        (T.ON_NOTICE, TE.RETRACT_NOTICE): T.EMPLOYED,
        (T.ON_NOTICE, TE.LEAVE): T.GONE,
    },
    frozenset({T.GONE}),
    T,
    TE,
)

Q, QE = EquipmentState, EquipmentEvent
EQUIPMENT = Machine(
    "equipment",
    {
        (Q.WORKING, QE.WEAR_OUT): Q.WORN,
        (Q.WORKING, QE.BREAK_DOWN): Q.BROKEN,
        (Q.WORN, QE.SERVICE): Q.WORKING,
        (Q.WORN, QE.BREAK_DOWN): Q.BROKEN,
        (Q.WORN, QE.REPLACE): Q.REPLACED,
        (Q.BROKEN, QE.START_REPAIR): Q.REPAIRING,
        (Q.BROKEN, QE.REPLACE): Q.REPLACED,
        (Q.REPAIRING, QE.FINISH_REPAIR): Q.WORKING,
        (Q.REPAIRING, QE.REPLACE): Q.REPLACED,
    },
    frozenset({Q.REPLACED}),
    Q,
    QE,
)

M, ME = MenuState, MenuEvent
MENU_ITEM = Machine(
    "menu_item",
    {
        (M.CATALOG, ME.START_TRIAL): M.TRIAL,
        (M.CATALOG, ME.ADD_TO_MENU): M.ON_MENU,
        (M.TRIAL, ME.ADD_TO_MENU): M.ON_MENU,
        (M.TRIAL, ME.DROP_AFTER_TRIAL): M.RETIRED,
        (M.ON_MENU, ME.RETIRE): M.RETIRED,
    },
    frozenset({M.RETIRED}),
    M,
    ME,
)

C, CE = CustomerState, CustomerEvent
CUSTOMER = Machine(
    "customer",
    {
        (C.NEW, CE.VISIT_REPEATEDLY): C.REGULAR,
        (C.NEW, CE.GO_QUIET): C.LAPSED,
        (C.REGULAR, CE.GO_QUIET): C.LAPSED,
        (C.LAPSED, CE.RETURN): C.REGULAR,
        (C.LAPSED, CE.GIVE_UP): C.CHURNED,
    },
    frozenset({C.CHURNED}),
    C,
    CE,
)

B, BE = CurveballState, CurveballEvent
CURVEBALL = Machine(
    "curveball",
    {
        (B.FORESHADOWED, BE.BEGIN): B.ACTIVE,
        (B.FORESHADOWED, BE.FIZZLE): B.EXPIRED,
        (B.ACTIVE, BE.ASK_FOR_RESPONSE): B.AWAITING_RESPONSE,
        (B.ACTIVE, BE.RUN_ITS_COURSE): B.RESOLVED,
        (B.AWAITING_RESPONSE, BE.RESPOND): B.RESOLVED,
        (B.AWAITING_RESPONSE, BE.TIME_OUT): B.EXPIRED,
    },
    frozenset({B.RESOLVED, B.EXPIRED}),
    B,
    BE,
)

K, KE = CompetitorState, CompetitorEvent
COMPETITOR = Machine(
    "competitor",
    {
        (K.RUMORED, KE.OPEN_DOORS): K.OPEN,
        (K.RUMORED, KE.RUMOR_FADES): K.CLOSED,
        (K.OPEN, KE.SHUT_DOWN): K.CLOSED,
    },
    frozenset({K.CLOSED}),
    K,
    KE,
)

R, RE = RunState, RunEvent
RUN = Machine(
    "run",
    {
        (R.ONBOARDING, RE.START): R.WEEKLY_TURN,
        (R.WEEKLY_TURN, RE.SUBMIT_TURN): R.SIMULATING,
        (R.SIMULATING, RE.FINISH_WEEK): R.WEEKLY_TURN,
        (R.SIMULATING, RE.INTERRUPT): R.INTERRUPTED,
        (R.INTERRUPTED, RE.RESUME): R.SIMULATING,
        (R.SIMULATING, RE.FINISH_YEAR): R.SCORED,
        (R.SIMULATING, RE.GO_BANKRUPT): R.BANKRUPT,
        (R.WEEKLY_TURN, RE.STOP): R.STOPPED,
    },
    frozenset({R.SCORED, R.BANKRUPT, R.STOPPED}),
    R,
    RE,
)

MACHINES: dict[str, Machine] = {
    m.entity: m
    for m in (
        LOCATION,
        LEASE,
        STAFF,
        EQUIPMENT,
        MENU_ITEM,
        CUSTOMER,
        CURVEBALL,
        COMPETITOR,
        RUN,
    )
}
