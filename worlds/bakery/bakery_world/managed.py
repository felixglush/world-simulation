import json
import random
from collections import Counter, deque
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum, StrEnum, auto
from typing import IO

from bakery_world import machines as mc
from bakery_world.basics import (
    Account,
    Cents,
    ItemId,
    LocationId,
    NeighborhoodId,
    Role,
    StaffId,
    date_of,
)
from bakery_world.engine import BAKER_SHIFT_HOURS, CLEANER_SHIFT_HOURS, DayReport, post
from bakery_world.history import Founding
from bakery_world.machines import (
    CurveballState,
    LeaseState,
    LocationState,
    StaffState,
    Transition,
)
from bakery_world.model import Location, Staff, World
from bakery_world.records import Recorder

MANAGER_HOURS_PER_WEEK = 10
REPORT_DAYS = 91
OVEN_COST_CENTS = 600_000
OVEN_LIFE_DAYS = 8 * 365
HOME_FITOUT_CENTS = 4_000_000
FITOUT_LIFE_DAYS = 10 * 365
SEVERANCE_WEEKS = 2
WORKING_DAYS_PER_WEEK = 5
SHIFT_HOURS: dict[Role, int] = {
    Role.BAKER: BAKER_SHIFT_HOURS,
    Role.COUNTER: 8,
    Role.CLEANER: CLEANER_SHIFT_HOURS,
}


class Channel(StrEnum):
    FLYERS = auto()
    SOCIAL = auto()
    LOCAL_PAPER = auto()


@dataclass(frozen=True)
class ChannelSpec:
    efficiency: float
    daily_decay: float


CHANNELS: dict[Channel, ChannelSpec] = {
    Channel.FLYERS: ChannelSpec(1.0, 1 / 14),
    Channel.SOCIAL: ChannelSpec(1.3, 1 / 6),
    Channel.LOCAL_PAPER: ChannelSpec(0.8, 1 / 30),
}


@dataclass
class Candidate:
    staff: Staff
    posted_day: int
    arrives_day: int
    expires_day: int
    reservation_cents: Cents
    cv_noise: float
    interview_noise: float

    @property
    def id(self) -> StaffId:
        return self.staff.id

    @property
    def interviewed(self) -> bool:
        return self.staff.state.name != "CANDIDATE"

    @property
    def noise(self) -> float:
        return self.interview_noise if self.interviewed else self.cv_noise

    @property
    def skill_estimate(self) -> float:
        return round(min(1.0, max(0.0, self.staff.skill + self.noise)), 2)


@dataclass(frozen=True)
class Site:
    id: str
    neighborhood_id: NeighborhoodId
    size_m2: int
    rent_cents_per_month: Cents
    fitout_cents: Cents
    fitout_days: int


@dataclass
class Asset:
    name: str
    location_id: LocationId
    cost_cents: Cents
    acquired_day: int
    life_days: int
    written_off_day: int | None = None

    def book_value(self, day: int) -> int:
        if self.written_off_day is not None and day >= self.written_off_day:
            return 0
        used = min(1.0, max(0.0, (day - self.acquired_day) / self.life_days))
        return round(self.cost_cents * (1 - used))


@dataclass(frozen=True)
class ParRule:
    percentile: int
    lookback_weeks: int


class LogKind(StrEnum):
    START = auto()
    WEEK = auto()
    ACTION = auto()
    REFUSAL = auto()
    TRANSITION = auto()
    EVENT = auto()
    DOCUMENT = auto()
    INTERRUPT = auto()
    BANKRUPT = auto()
    TURN = auto()
    TOOL_CALL = auto()
    ANALYST = auto()
    MODEL_CALL = auto()
    STOPPED = auto()
    FINISH = auto()


def _plain(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(_plain(k)): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class RunLog:
    def __init__(self, stream: IO[str] | None = None) -> None:
        self.lines: list[str] = []
        self.stream = stream

    def write(self, day: int, kind: LogKind, **fields: object) -> None:
        line = json.dumps({"day": day, "kind": kind.value} | _plain(fields), sort_keys=True)
        self.lines.append(line)
        if self.stream:
            self.stream.write(line + "\n")


@dataclass
class ManagedWorld:
    founding: Founding
    hidden: dict
    curveballs: list = field(default_factory=list)
    candidates: dict[StaffId, Candidate] = field(default_factory=dict)
    sites: dict[str, Site] = field(default_factory=dict)
    research: dict[NeighborhoodId, dict] = field(default_factory=dict)
    par_rules: dict[tuple[LocationId, ItemId], ParRule] = field(default_factory=dict)
    demand: dict[tuple[LocationId, ItemId, int], deque[int]] = field(default_factory=dict)
    reports: deque[DayReport] = field(default_factory=lambda: deque(maxlen=REPORT_DAYS))
    reviews: deque[tuple[int, LocationId, int]] = field(default_factory=lambda: deque(maxlen=400))
    ad_stock: dict[tuple[LocationId, Channel], float] = field(default_factory=dict)
    trials: dict[tuple[LocationId, ItemId], int] = field(default_factory=dict)
    openings: dict[LocationId, int] = field(default_factory=dict)
    assets: list[Asset] = field(default_factory=list)
    documents: list[tuple[int, str, str, str]] = field(default_factory=list)
    manager_hours: int = MANAGER_HOURS_PER_WEEK
    keeps_founder: bool = False
    handoff_day: int = 0
    log: RunLog = field(default_factory=RunLog)
    recorder: Recorder | None = None
    allow_expansion: bool = False
    ledger_mark: int = 0
    jobs_posted: int = 0
    totals: Counter = field(default_factory=Counter)
    handoff_prices: dict[ItemId, int] = field(default_factory=dict)
    handoff_headcount: int = 0
    review_rng: random.Random = field(default_factory=random.Random)

    @property
    def world(self) -> World:
        return self.founding.world

    @property
    def seed(self) -> int:
        return self.founding.seed

    @property
    def day(self) -> int:
        return self.world.day

    def rng(self, *stream: object) -> random.Random:
        return random.Random(":".join(str(s) for s in (self.seed, "management", *stream)))

    def note(self, kind: LogKind, **fields: object) -> None:
        self.log.write(self.day, kind, **fields)

    def publish(self, kind: str, title: str, body: str) -> None:
        doc = (self.day, kind, title, body)
        self.documents.append(doc)
        self.note(LogKind.DOCUMENT, doc_kind=kind, title=title, body=body)
        if self.recorder:
            self.recorder.append_documents([doc])

    def log_transitions(self, transitions: list[Transition]) -> None:
        for t in transitions:
            facts = asdict(t)
            self.log.write(facts.pop("day"), LogKind.TRANSITION, **facts)

    def open_curveballs(self) -> list:
        live = (CurveballState.ACTIVE, CurveballState.AWAITING_RESPONSE)
        return [c for c in self.curveballs if c.state in live]

    def date(self) -> str:
        return date_of(self.day).isoformat()


def home_assets(world: World) -> list[Asset]:
    assets = []
    for loc in world.business.locations.values():
        if loc.state.name != "TRADING":
            continue
        assets.append(
            Asset(f"{loc.id} fit-out", loc.id, Cents(HOME_FITOUT_CENTS), 0, FITOUT_LIFE_DAYS)
        )
        assets += [
            Asset(o.id, loc.id, Cents(OVEN_COST_CENTS), 0, OVEN_LIFE_DAYS)
            for o in loc.ovens
            if o.state.name != "REPLACED"
        ]
    return assets


def weekly_hours(staff: Staff) -> int:
    return SHIFT_HOURS[staff.role] * WORKING_DAYS_PER_WEEK


def let_go(mw: ManagedWorld, staff: Staff, memo: str) -> None:
    b, log = mw.world.business, mw.world.business.transitions
    if staff.state is not StaffState.ON_NOTICE:
        staff.state = mc.step(
            log, mw.day, mc.STAFF, staff.id, staff.state, mc.StaffEvent.GIVE_NOTICE
        )
    staff.state = mc.step(log, mw.day, mc.STAFF, staff.id, staff.state, mc.StaffEvent.LEAVE)
    pay = SEVERANCE_WEEKS * weekly_hours(staff) * staff.wage_cents_per_hour
    post(b, mw.day, Account.SEVERANCE, -pay, staff.location_id, f"{memo}: {staff.name}")


def write_off(mw: ManagedWorld, location: LocationId, name: str | None = None) -> None:
    for asset in mw.assets:
        if asset.location_id == location and name in (None, asset.name):
            asset.written_off_day = mw.day


def shut_location(mw: ManagedWorld, loc: Location, memo: str) -> None:
    b, log, day = mw.world.business, mw.world.business.transitions, mw.day
    events = {
        LocationState.TRADING: (mc.LocationEvent.ANNOUNCE_CLOSING, mc.LocationEvent.SHUT_DOORS),
        LocationState.FITTING_OUT: (mc.LocationEvent.WALK_AWAY,),
    }[loc.state]
    for event in events:
        loc.state = mc.step(log, day, mc.LOCATION, loc.id, loc.state, event)
    lease_end = {
        LeaseState.ACTIVE: mc.LeaseEvent.TERMINATE,
        LeaseState.RENEWAL_WINDOW: mc.LeaseEvent.LAPSE,
    }
    if loc.lease.state in lease_end:
        loc.lease.state = mc.step(
            log, day, mc.LEASE, loc.id, loc.lease.state, lease_end[loc.lease.state]
        )
    loc.marketing_cents_per_month = Cents(0)
    loc.campaign_cents_per_month = 0.0
    for s in sorted(b.staff.values(), key=lambda s: s.id):
        if s.location_id == loc.id and s.state is not StaffState.GONE:
            let_go(mw, s, f"{memo}, severance")
    write_off(mw, loc.id)
    mw.openings.pop(loc.id, None)
