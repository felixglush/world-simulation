import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum, auto

from bakery_world import machines as mc
from bakery_world.basics import Account, Cents, Ingredient, Role, Segment, date_of
from bakery_world.catalog import CATALOG
from bakery_world.engine import post
from bakery_world.exogenous import MeanRevertingPrices, SpikeWindow, TrafficShock
from bakery_world.founder import CateringOrder, deliver_catering, draw_catering
from bakery_world.machines import CompetitorState, CurveballState, EquipmentState, StaffState
from bakery_world.managed import (
    OVEN_COST_CENTS,
    OVEN_LIFE_DAYS,
    Asset,
    LogKind,
    ManagedWorld,
    shut_location,
    write_off,
)
from bakery_world.model import Competitor, Equipment, Location
from bakery_world.records import MIN_WAGE_EFFECTIVE_DAY, SHOP_ADDRESS, Review, write
from bakery_world.town import HOME_LOCATION, HOME_NEIGHBORHOOD, RIVAL_ID, SHOP_NAME

HANDOFF_DAY, LAST_DAY = 1095, 1459
OVEN_REPAIR_DAYS = 5
OVEN_REPAIR_CENTS = 60_000
COUNTER_OFFER_RAISE = 0.15
NOTICE_DAYS = 7
RENT_RISE, NEGOTIATED_RISE = 0.2, 0.1
RENEWAL_YEARS = 5
INSPECTION_FINE_CENTS = 150_000
INSPECTION_LOOKBACK_DAYS = 14
INSPECTION_MIN_CLEANER_HOURS = 30
CATERING_LEAD_DAYS = 5
COUNTER_PRICE = 1.15
CHAIN_ID = "cornercup"
CHAIN_NAME = "Corner Cup"
ROAD_WORKS_DAYS = 42
ROAD_WORKS_TRAFFIC = 0.6
CHAIN_APPEAL = dict(zip(Segment, (0.9, 0.3, 0.8, 0.2, 0.9), strict=True))


class Kind(StrEnum):
    OVEN_BREAKS = auto()
    HEAD_BAKER_QUITS = auto()
    INGREDIENT_SPIKE = auto()
    CHAIN_CAFE = auto()
    ROAD_WORKS = auto()
    LANDLORD_RENT = auto()
    HEALTH_INSPECTION = auto()
    VIRAL_REVIEW = auto()
    CATERING_ORDER = auto()
    MINIMUM_WAGE = auto()


class Start(StrEnum):
    FIZZLE = auto()
    ASK = auto()
    RUN = auto()


@dataclass
class Curveball:
    id: str
    kind: Kind
    start_day: int
    foreshadow_day: int | None = None
    params: dict = field(default_factory=dict)
    state: CurveballState = CurveballState.FORESHADOWED
    deadline_day: int | None = None
    settle_day: int | None = None
    response: str | None = None
    summary: str = ""
    outcome: str = ""

    @property
    def spec(self) -> "KindSpec":
        return KINDS[self.kind]


Act = Callable[[ManagedWorld, Curveball], None]


@dataclass(frozen=True)
class KindSpec:
    urgent: bool
    options: dict[str, str]
    default: str | None
    response_days: int
    begin: Callable[[ManagedWorld, Curveball], Start]
    respond: Callable[[ManagedWorld, Curveball, str], str] | None = None
    foreshadow: Act | None = None
    settle: Act | None = None


def _home(mw: ManagedWorld) -> Location | None:
    loc = mw.world.business.locations.get(HOME_LOCATION)
    return loc if loc and loc.state is mc.LocationState.TRADING else None


def _letter(mw: ManagedWorld, cb: Curveball, kind: str, title: str, template: str, **f) -> None:
    text = write(template, mw.rng("curveball", cb.id, template), **f)
    cb.summary = cb.summary or text
    mw.publish(kind, title, text)


def _dollars(cents: int) -> str:
    return f"{cents / 100:,.0f}"


def _step(mw: ManagedWorld, cb: Curveball, event: mc.CurveballEvent) -> None:
    log = mw.world.business.transitions
    cb.state = mc.step(log, mw.day, mc.CURVEBALL, cb.id, cb.state, event)


# oven


def _oven_foreshadow(mw: ManagedWorld, cb: Curveball) -> None:
    home = _home(mw)
    if home:
        oven = max(home.ovens, key=lambda o: o.wear)
        text = write(
            "note_oven_noise", mw.rng("curveball", cb.id), oven=oven.id.replace("oven", "Oven ")
        )
        mw.publish("notebook", f"Notebook, {mw.date()}", text)


def _oven_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    home = _home(mw)
    usable = (EquipmentState.WORKING, EquipmentState.WORN)
    ovens = [o for o in home.ovens if o.state in usable] if home else []
    if not ovens:
        return Start.FIZZLE
    oven, log, ev = (
        max(ovens, key=lambda o: o.wear),
        mw.world.business.transitions,
        mc.EquipmentEvent,
    )
    for event in (ev.BREAK_DOWN, ev.START_REPAIR):
        oven.state = mc.step(log, mw.day, mc.EQUIPMENT, oven.id, oven.state, event)
    oven.repair_done_day = mw.day + OVEN_REPAIR_DAYS
    cb.params["oven"] = oven.id
    _letter(
        mw,
        cb,
        "letter",
        f"{oven.id} breakdown",
        "letter_oven_broken",
        oven=oven.id,
        days=OVEN_REPAIR_DAYS,
        repair=_dollars(OVEN_REPAIR_CENTS),
        replace=_dollars(OVEN_COST_CENTS),
    )
    return Start.ASK


def _oven_respond(mw: ManagedWorld, cb: Curveball, option: str) -> str:
    home = mw.world.business.locations[HOME_LOCATION]
    oven = next(o for o in home.ovens if o.id == cb.params["oven"])
    b = mw.world.business
    if option == "repair" or oven.state is not EquipmentState.REPAIRING:
        post(b, mw.day, Account.REPAIRS, -OVEN_REPAIR_CENTS, home.id, f"{oven.id} repair")
        return f"{oven.id} repaired, back in service on {date_of(oven.repair_done_day)}"
    oven.state = mc.step(
        b.transitions, mw.day, mc.EQUIPMENT, oven.id, oven.state, mc.EquipmentEvent.REPLACE
    )
    write_off(mw, home.id, oven.id)
    new_id = f"oven{sum(len(loc.ovens) for loc in b.locations.values()) + 1}"
    home.ovens.append(Equipment(new_id))
    post(b, mw.day, Account.EQUIPMENT, -OVEN_COST_CENTS, home.id, f"{new_id} purchase")
    mw.assets.append(Asset(new_id, home.id, Cents(OVEN_COST_CENTS), mw.day, OVEN_LIFE_DAYS))
    return f"{oven.id} replaced by {new_id}, working from tomorrow"


# head baker


def _baker_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    bakers = [
        s
        for s in mw.world.business.staff.values()
        if s.role is Role.BAKER
        and s.location_id == HOME_LOCATION
        and s.state is StaffState.EMPLOYED
        and not s.owner
    ]
    if not bakers:
        return Start.FIZZLE
    baker = max(bakers, key=lambda s: (s.skill * s.speed, s.id))
    log = mw.world.business.transitions
    baker.state = mc.step(log, mw.day, mc.STAFF, baker.id, baker.state, mc.StaffEvent.GIVE_NOTICE)
    baker.state_until_day = mw.day + NOTICE_DAYS
    cb.params |= {"staff": baker.id, "name": baker.name}
    rival = mw.world.competitors[RIVAL_ID].name
    last = date_of(baker.state_until_day).isoformat()
    _letter(
        mw,
        cb,
        "letter",
        f"Notice from {baker.name}",
        "letter_notice",
        rival=rival,
        last=last,
        name=baker.name,
    )
    return Start.ASK


def _baker_respond(mw: ManagedWorld, cb: Curveball, option: str) -> str:
    baker = mw.world.business.staff[cb.params["staff"]]
    if option == "let_go" or baker.state is not StaffState.ON_NOTICE:
        return f"{baker.name} leaves on {date_of(baker.state_until_day)}"
    log = mw.world.business.transitions
    baker.state = mc.step(
        log, mw.day, mc.STAFF, baker.id, baker.state, mc.StaffEvent.RETRACT_NOTICE
    )
    baker.wage_cents_per_hour = Cents(round(baker.wage_cents_per_hour * (1 + COUNTER_OFFER_RAISE)))
    baker.morale = min(1.0, baker.morale + 0.15)
    return f"{baker.name} stays at ${baker.wage_cents_per_hour / 100:.2f} an hour"


# ingredient spike

SPIKE_SOURCES = {
    Ingredient.BUTTER: "a poor season for dairy herds",
    Ingredient.FLOUR: "a failed wheat harvest",
}


def _spike_foreshadow(mw: ManagedWorld, cb: Curveball) -> None:
    ing = Ingredient(cb.params["ingredient"])
    supplier = mw.world.business.suppliers[mw.world.business.buy_from[ing]]
    _letter(
        mw,
        cb,
        "letter",
        f"{supplier.name}: price notice",
        "letter_ingredient",
        reason=SPIKE_SOURCES[ing],
        ingredient=ing.value,
        pct=round((cb.params["multiplier"] - 1) * 100),
        start=date_of(cb.start_day).isoformat(),
        weeks=cb.params["days"] // 7,
        supplier=supplier.name,
    )


def _spike_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    old = mw.world.ingredient_prices
    window = SpikeWindow(
        Ingredient(cb.params["ingredient"]),
        cb.start_day,
        cb.start_day + cb.params["days"],
        cb.params["multiplier"],
    )
    mw.world.ingredient_prices = MeanRevertingPrices(
        old.seed, old.horizon_days, old.spikes + (window,)
    )
    cb.settle_day = window.end_day
    return Start.RUN


# chain cafe


def _chain_foreshadow(mw: ManagedWorld, cb: Curveball) -> None:
    mw.world.competitors[CHAIN_ID] = Competitor(
        CHAIN_ID,
        CHAIN_NAME,
        HOME_NEIGHBORHOOD,
        CompetitorState.RUMORED,
        cb.params["price_index"],
        {seg: base * cb.params["strength"] for seg, base in CHAIN_APPEAL.items()},
    )
    area = mw.world.town[HOME_NEIGHBORHOOD].name
    month = date_of(cb.start_day).strftime("%B")
    _letter(
        mw,
        cb,
        "news",
        f"{CHAIN_NAME} signs lease near {area}",
        "news_chain_lease",
        chain=CHAIN_NAME,
        area=area,
        month=month,
    )


def _chain_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    chain = mw.world.competitors[CHAIN_ID]
    log = mw.world.business.transitions
    chain.state = mc.step(
        log, mw.day, mc.COMPETITOR, chain.id, chain.state, mc.CompetitorEvent.OPEN_DOORS
    )
    mw.publish(
        "news",
        f"{CHAIN_NAME} opens on Corn Street",
        write("news_chain_open", mw.rng("curveball", cb.id, "open"), chain=CHAIN_NAME),
    )
    return Start.RUN


# road works


def _road_foreshadow(mw: ManagedWorld, cb: Curveball) -> None:
    area = mw.world.town[HOME_NEIGHBORHOOD].name
    _letter(
        mw,
        cb,
        "news",
        f"Road works planned for {area}",
        "news_road_works",
        area=area,
        start=date_of(cb.start_day).isoformat(),
    )


def _road_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    traffic = mw.world.foot_traffic
    shock = TrafficShock(
        HOME_NEIGHBORHOOD, cb.start_day, cb.start_day + ROAD_WORKS_DAYS, ROAD_WORKS_TRAFFIC
    )
    mw.world.foot_traffic = replace(traffic, shocks=traffic.shocks + (shock,))
    cb.settle_day = shock.end_day
    return Start.RUN


# landlord


def _landlord_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    home = _home(mw)
    if not home or home.lease.state is not mc.LeaseState.RENEWAL_WINDOW:
        return Start.FIZZLE
    old = home.lease.monthly_rent_cents
    cb.params |= {"old_rent": old, "asked_rent": round(old * (1 + RENT_RISE))}
    cb.settle_day = home.lease.end_day
    deadline = date_of(mw.day + cb.spec.response_days).isoformat()
    _letter(
        mw,
        cb,
        "letter",
        "Lease renewal",
        "letter_landlord",
        address=SHOP_ADDRESS,
        end=date_of(home.lease.end_day).isoformat(),
        rent=_dollars(cb.params["asked_rent"]),
        old=_dollars(old),
        deadline=deadline,
    )
    return Start.ASK


def _landlord_respond(mw: ManagedWorld, cb: Curveball, option: str) -> str:
    home = mw.world.business.locations[HOME_LOCATION]
    if option == "leave":
        cb.params["new_rent"] = None
        return f"the lease will end on {date_of(home.lease.end_day)} and the shop will close"
    rise = RENT_RISE
    if option == "negotiate" and mw.rng("curveball", cb.id, "negotiate").random() < 0.5:
        rise = NEGOTIATED_RISE
    cb.params["new_rent"] = round(cb.params["old_rent"] * (1 + rise))
    log = mw.world.business.transitions
    home.lease.state = mc.step(
        log, mw.day, mc.LEASE, home.id, home.lease.state, mc.LeaseEvent.RENEW
    )
    return f"renewed for {RENEWAL_YEARS} years at ${_dollars(cb.params['new_rent'])} a month"


def _landlord_settle(mw: ManagedWorld, cb: Curveball) -> None:
    home = mw.world.business.locations[HOME_LOCATION]
    lease, log = home.lease, mw.world.business.transitions
    if lease.state is mc.LeaseState.RENEWED:
        lease.state = mc.step(
            log, mw.day, mc.LEASE, home.id, lease.state, mc.LeaseEvent.START_NEW_TERM
        )
        lease.monthly_rent_cents = Cents(cb.params["new_rent"])
        lease.end_day += RENEWAL_YEARS * 365
    elif home.state is mc.LocationState.TRADING:
        shut_location(mw, home, "lease ended")


# health inspection


def _inspection_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    home = _home(mw)
    if not home:
        return Start.FIZZLE
    cleaners = {s.id for s in mw.world.business.staff.values() if s.role is Role.CLEANER}
    hours = sum(
        shift.hours
        for r in list(mw.reports)[-INSPECTION_LOOKBACK_DAYS:]
        for shop in r.locations
        if shop.location_id == home.id
        for shift in shop.shifts
        if shift.staff_id in cleaners
    )
    date = mw.date()
    if hours >= INSPECTION_MIN_CLEANER_HOURS:
        _letter(mw, cb, "letter", "Food hygiene inspection", "letter_inspection_pass", date=date)
        return Start.RUN
    home.reputation = max(0.0, home.reputation - 0.04)
    cb.params["cleaner_hours"] = hours
    _letter(
        mw,
        cb,
        "letter",
        "Food hygiene inspection",
        "letter_inspection_fail",
        date=date,
        fine=_dollars(INSPECTION_FINE_CENTS),
    )
    return Start.ASK


def _inspection_respond(mw: ManagedWorld, cb: Curveball, option: str) -> str:
    post(
        mw.world.business,
        mw.day,
        Account.FINES,
        -INSPECTION_FINE_CENTS,
        HOME_LOCATION,
        "hygiene penalty",
    )
    return f"penalty of ${_dollars(INSPECTION_FINE_CENTS)} paid"


# viral review


def _viral_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    home = _home(mw)
    if not home:
        return Start.FIZZLE
    good = cb.params["good"]
    item = CATALOG[cb.params["item"]].name.lower()
    if good:
        home.reputation = min(1.0, home.reputation + 0.05)
        home.awareness += 0.1 * (1 - home.awareness)
    else:
        home.reputation = max(0.0, home.reputation - 0.06)
        home.awareness += 0.03 * (1 - home.awareness)
    tone = "good" if good else "bad"
    rng = mw.rng("curveball", cb.id)
    rating = 5 if good else 1
    review = write(f"review_viral_{tone}", rng, item=item)
    mw.reviews.append((mw.day, home.id, rating))
    if mw.recorder:
        mw.recorder.insert_reviews(mw.day, home.id, [Review(rating, review, {})])
    _letter(
        mw, cb, "news", f"{SHOP_NAME} goes viral", f"news_viral_{tone}", shop=SHOP_NAME, item=item
    )
    return Start.RUN


# catering


def _catering_begin(mw: ManagedWorld, cb: Curveball) -> Start:
    if not _home(mw):
        return Start.FIZZLE
    order = cb.params["order"]
    _letter(
        mw,
        cb,
        "letter",
        "Catering enquiry",
        "letter_catering",
        units=order.units,
        client=order.client,
        delivery=date_of(order.day).isoformat(),
        value=_dollars(order.value_cents),
    )
    return Start.ASK


def _catering_respond(mw: ManagedWorld, cb: Curveball, option: str) -> str:
    order: CateringOrder = cb.params["order"]
    if option == "decline":
        return "order declined"
    if option == "counter":
        if mw.rng("curveball", cb.id, "counter").random() >= 0.5:
            return "the client turned down the higher price"
        order = replace(order, value_cents=Cents(round(order.value_cents * COUNTER_PRICE)))
        cb.params["order"] = order
    cb.settle_day = order.day
    return f"order for ${_dollars(order.value_cents)} accepted, delivery {date_of(order.day)}"


def _catering_settle(mw: ManagedWorld, cb: Curveball) -> None:
    home = _home(mw)
    working = (EquipmentState.WORKING, EquipmentState.WORN)
    if home and any(o.state in working for o in home.ovens):
        deliver_catering(mw.world, mw.day, cb.params["order"], home.id)
        cb.outcome += "; delivered"
    elif home:
        home.reputation = max(0.0, home.reputation - 0.03)
        cb.outcome += "; could not bake the order, client let down"


def _run(mw: ManagedWorld, cb: Curveball) -> Start:
    return Start.RUN


KINDS: dict[Kind, KindSpec] = {
    Kind.OVEN_BREAKS: KindSpec(
        True,
        {
            "repair": f"repair in {OVEN_REPAIR_DAYS} days for ${_dollars(OVEN_REPAIR_CENTS)}",
            "replace": f"new oven tomorrow for ${_dollars(OVEN_COST_CENTS)}",
        },
        "repair",
        1,
        _oven_begin,
        _oven_respond,
        foreshadow=_oven_foreshadow,
    ),
    Kind.HEAD_BAKER_QUITS: KindSpec(
        True,
        {
            "counter_offer": f"raise their pay {COUNTER_OFFER_RAISE:.0%} and they stay",
            "let_go": f"they leave in {NOTICE_DAYS} days",
        },
        "let_go",
        3,
        _baker_begin,
        _baker_respond,
    ),
    Kind.INGREDIENT_SPIKE: KindSpec(False, {}, None, 0, _spike_begin, foreshadow=_spike_foreshadow),
    Kind.CHAIN_CAFE: KindSpec(False, {}, None, 0, _chain_begin, foreshadow=_chain_foreshadow),
    Kind.ROAD_WORKS: KindSpec(False, {}, None, 0, _road_begin, foreshadow=_road_foreshadow),
    Kind.LANDLORD_RENT: KindSpec(
        True,
        {
            "accept": f"renew at {RENT_RISE:.0%} more rent",
            "negotiate": f"even chance of {NEGOTIATED_RISE:.0%} instead, otherwise {RENT_RISE:.0%}",
            "leave": "let the lease end and close the shop on the end date",
        },
        "accept",
        30,
        _landlord_begin,
        _landlord_respond,
        settle=_landlord_settle,
    ),
    Kind.HEALTH_INSPECTION: KindSpec(
        True,
        {"pay": f"pay the ${_dollars(INSPECTION_FINE_CENTS)} penalty"},
        "pay",
        7,
        _inspection_begin,
        _inspection_respond,
    ),
    Kind.VIRAL_REVIEW: KindSpec(False, {}, None, 0, _viral_begin),
    Kind.CATERING_ORDER: KindSpec(
        True,
        {
            "accept": "bake it at the quoted price",
            "decline": "turn it down",
            "counter": f"ask {COUNTER_PRICE - 1:.0%} more; even chance they agree",
        },
        "decline",
        3,
        _catering_begin,
        _catering_respond,
        settle=_catering_settle,
    ),
    Kind.MINIMUM_WAGE: KindSpec(False, {}, None, 0, _run),
}


def draw_curveballs(seed: int, lease_end_day: int) -> list[Curveball]:
    """Timing and size are fixed per seed, so every policy meets the same events."""
    rng = random.Random(f"{seed}:curveballs")

    def day(low: int, high: int) -> int:
        return rng.randint(low, high)

    catering = draw_catering(rng, HANDOFF_DAY + 15, LAST_DAY - 20, 1)[0]
    spike_ing = rng.choice((Ingredient.BUTTER, Ingredient.FLOUR))
    drawn = [
        Curveball("", Kind.OVEN_BREAKS, day(1110, 1440)),
        Curveball("", Kind.HEAD_BAKER_QUITS, day(1110, 1440)),
        Curveball(
            "",
            Kind.INGREDIENT_SPIKE,
            day(1130, 1360),
            params={
                "ingredient": spike_ing.value,
                "multiplier": round(rng.uniform(1.3, 1.6), 2),
                "days": 7 * rng.randint(8, 12),
            },
        ),
        Curveball(
            "",
            Kind.CHAIN_CAFE,
            day(1160, 1420),
            params={"strength": round(rng.uniform(0.5, 0.8), 2), "price_index": 0.85},
        ),
        Curveball("", Kind.ROAD_WORKS, day(1125, 1400)),
        Curveball("", Kind.LANDLORD_RENT, lease_end_day - 75),
        Curveball("", Kind.HEALTH_INSPECTION, day(1110, 1440)),
        Curveball(
            "",
            Kind.VIRAL_REVIEW,
            day(1110, 1440),
            params={
                "good": rng.random() < 0.5,
                "item": rng.choice(("croissant", "sourdough", "cinnamon")),
            },
        ),
        Curveball(
            "",
            Kind.CATERING_ORDER,
            catering.day - CATERING_LEAD_DAYS,
            params={"order": catering},
        ),
        Curveball("", Kind.MINIMUM_WAGE, MIN_WAGE_EFFECTIVE_DAY),
    ]
    taken: set[int] = set()
    for cb in drawn:
        while cb.spec.urgent and cb.start_day in taken:
            cb.start_day += 1
        if cb.spec.urgent:
            taken.add(cb.start_day)
        lead = FORESHADOW_LEAD.get(cb.kind)
        if lead:
            cb.foreshadow_day = cb.start_day - rng.randint(*lead)
        if cb.kind is Kind.CATERING_ORDER:
            cb.params["order"] = replace(catering, day=cb.start_day + CATERING_LEAD_DAYS)
    for n, cb in enumerate(sorted(drawn, key=lambda c: (c.start_day, c.kind)), start=1):
        cb.id = f"event{n:02d}"
    return sorted(drawn, key=lambda c: c.id)


FORESHADOW_LEAD: dict[Kind, tuple[int, int]] = {
    Kind.OVEN_BREAKS: (10, 20),
    Kind.INGREDIENT_SPIKE: (14, 28),
    Kind.CHAIN_CAFE: (56, 56),
    Kind.ROAD_WORKS: (18, 25),
}


def morning(mw: ManagedWorld) -> list[Curveball]:
    """Advance every curveball for the day; return the urgent ones now awaiting a response."""
    ev, asked = mc.CurveballEvent, []
    for cb in mw.curveballs:
        spec = cb.spec
        if cb.foreshadow_day == mw.day and spec.foreshadow:
            spec.foreshadow(mw, cb)
        if cb.start_day == mw.day and cb.state is CurveballState.FORESHADOWED:
            start = spec.begin(mw, cb)
            if start is Start.FIZZLE:
                _step(mw, cb, ev.FIZZLE)
                cb.outcome = "did not happen"
            else:
                _step(mw, cb, ev.BEGIN)
            if start is Start.ASK:
                _step(mw, cb, ev.ASK_FOR_RESPONSE)
                cb.deadline_day = mw.day + spec.response_days
                if spec.urgent:
                    asked.append(cb)
            elif start is Start.RUN and cb.settle_day is None:
                _step(mw, cb, ev.RUN_ITS_COURSE)
            mw.note(
                LogKind.EVENT, event=cb.id, event_kind=cb.kind, state=cb.state, summary=cb.summary
            )
        if cb.state is CurveballState.AWAITING_RESPONSE and mw.day >= cb.deadline_day:
            cb.outcome = "no response; " + spec.respond(mw, cb, spec.default)
            cb.response = spec.default
            _step(mw, cb, ev.TIME_OUT)
            mw.note(
                LogKind.EVENT, event=cb.id, event_kind=cb.kind, state=cb.state, outcome=cb.outcome
            )
        if cb.settle_day == mw.day:
            if spec.settle:
                spec.settle(mw, cb)
            if cb.state is CurveballState.ACTIVE:
                _step(mw, cb, ev.RUN_ITS_COURSE)
            mw.note(
                LogKind.EVENT, event=cb.id, event_kind=cb.kind, state=cb.state, outcome=cb.outcome
            )
    return asked


def respond(mw: ManagedWorld, cb: Curveball, option: str) -> str:
    cb.outcome = cb.spec.respond(mw, cb, option)
    cb.response = option
    _step(mw, cb, mc.CurveballEvent.RESPOND)
    mw.note(LogKind.EVENT, event=cb.id, event_kind=cb.kind, state=cb.state, outcome=cb.outcome)
    return cb.outcome
