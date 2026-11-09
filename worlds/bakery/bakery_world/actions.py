import math
from collections.abc import Callable
from dataclasses import dataclass, field

from bakery_world import machines as mc
from bakery_world.basics import (
    Account,
    Cents,
    Ingredient,
    ItemId,
    LocationId,
    NeighborhoodId,
    Role,
    StaffId,
)
from bakery_world.catalog import CATALOG
from bakery_world.curveballs import Curveball, respond
from bakery_world.engine import BAKERY_GOER_SHARE, market_wage, post
from bakery_world.founder import TRIAL_PLAN_UNITS
from bakery_world.machines import CurveballState, LocationState, MenuState, StaffState
from bakery_world.managed import (
    CHANNELS,
    FITOUT_LIFE_DAYS,
    OVEN_COST_CENTS,
    OVEN_LIFE_DAYS,
    Asset,
    Candidate,
    Channel,
    LogKind,
    ManagedWorld,
    ParRule,
    Site,
    let_go,
    shut_location,
)
from bakery_world.model import Equipment, Lease, Location, MenuEntry, ShiftTime, Staff, rota_hours
from bakery_world.records import MIN_WAGE_EFFECTIVE_DAY, MIN_WAGE_NEW_CENTS, MIN_WAGE_OLD_CENTS
from bakery_world.rota import (
    MAX_DAY_HOURS,
    MAX_WEEK_HOURS,
    PRE_OPEN_BAKE_HOURS,
    WEEKDAYS,
    assign_rota,
    counter_gaps,
    describe,
    shift_window,
)
from bakery_world.town import _TOWN as TOWN_ROWS
from bakery_world.town import HIRE_NAMES, HOME_LOCATION

TRIAL_DAYS = 7
MAX_LOCATIONS = 3
MAX_MENU_ITEMS = 22
MIN_MENU_ITEMS = 3
RESEARCH_CENTS = 150_000
CANDIDATE_DAYS = 21
CV_NOISE, INTERVIEW_NOISE = 0.15, 0.04
NEW_SHOP_LEASE_YEARS = 5
NEW_SHOP_BAKE_SHARE = 0.5
FIRE_MORALE_HIT = 0.08
ACTIVE_MENU = (MenuState.ON_MENU, MenuState.TRIAL)
WORKING = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)
NEIGHBORHOODS = tuple(row[0] for row in TOWN_ROWS)


class ActionRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ActionResult:
    message: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Param:
    name: str
    type: type
    summary: str
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] | None = None
    length: int | None = None
    items: type = int

    def schema(self) -> dict:
        kinds = {int: "integer", str: "string", list: "array"}
        out: dict = {"type": kinds[self.type], "description": self.summary}
        bounds = {"minimum": self.minimum, "maximum": self.maximum}
        bounds = {k: v for k, v in bounds.items() if v is not None}
        if self.type is list:
            out |= {"items": {"type": kinds[self.items]} | bounds}
            out |= {"minItems": self.length, "maxItems": self.length}
        else:
            out |= bounds
        if self.choices:
            out["enum"] = list(self.choices)
        return out

    def check(self, value: object) -> None:
        def bounded(v: object) -> None:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ActionRefused(f"{self.name} must be a whole number, not {v!r}")
            if self.minimum is not None and v < self.minimum:
                raise ActionRefused(f"{self.name} must be at least {self.minimum}, not {v}")
            if self.maximum is not None and v > self.maximum:
                raise ActionRefused(f"{self.name} must be at most {self.maximum}, not {v}")

        if self.type is str:
            if not isinstance(value, str):
                raise ActionRefused(f"{self.name} must be text, not {value!r}")
            if self.choices and value not in self.choices:
                raise ActionRefused(f"{self.name} must be one of {', '.join(self.choices)}")
        elif self.type is list:
            kind = "numbers" if self.items is int else "texts"
            if not isinstance(value, list) or len(value) != self.length:
                raise ActionRefused(f"{self.name} must be a list of {self.length} {kind}")
            for v in value:
                if self.items is int:
                    bounded(v)
                elif not isinstance(v, str):
                    raise ActionRefused(f"{self.name} must be a list of {self.length} {kind}")
        else:
            bounded(value)


@dataclass(frozen=True)
class Action:
    name: str
    summary: str
    params: tuple[Param, ...]
    fn: Callable[..., ActionResult]

    def schema(self) -> dict:
        return {
            "name": self.name,
            "summary": self.summary,
            "arguments": {p.name: p.schema() for p in self.params},
            "required": [p.name for p in self.params],
        }


def _location(mw: ManagedWorld, location: str, *states: LocationState) -> Location:
    loc = mw.world.business.locations.get(LocationId(location))
    if loc is None:
        raise ActionRefused(f"there is no location {location!r}")
    allowed = states or (LocationState.TRADING,)
    if loc.state not in allowed:
        wanted = " or ".join(s.value for s in allowed)
        raise ActionRefused(f"{location} is {loc.state.value}, it must be {wanted}")
    return loc


def _entry(loc: Location, item: str, baked: bool = False) -> MenuEntry:
    entry = loc.menu.get(ItemId(item))
    if entry is None or entry.state not in ACTIVE_MENU:
        raise ActionRefused(f"{item} is not on the menu at {loc.id}")
    if baked and CATALOG[ItemId(item)].made_to_order:
        raise ActionRefused(f"{item} is made to order, it has no bake plan")
    return entry


def _staff(mw: ManagedWorld, staff: str) -> Staff:
    s = mw.world.business.staff.get(StaffId(staff))
    if s is None or s.state not in WORKING:
        raise ActionRefused(f"{staff!r} is not on the staff")
    if s.owner:
        raise ActionRefused("the founder's own pay and job are not the manager's to change")
    return s


def minimum_wage(mw: ManagedWorld) -> Cents:
    new = mw.day >= MIN_WAGE_EFFECTIVE_DAY
    return Cents(MIN_WAGE_NEW_CENTS if new else MIN_WAGE_OLD_CENTS)


def _wage(mw: ManagedWorld, wage: int) -> Cents:
    floor = minimum_wage(mw)
    if wage < floor:
        raise ActionRefused(f"${wage / 100:.2f} is below the minimum wage of ${floor / 100:.2f}")
    return Cents(wage)


def headroom(mw: ManagedWorld) -> int:
    b = mw.world.business
    return b.cash_cents + b.credit_limit_cents - b.credit_drawn_cents


def _afford(mw: ManagedWorld, cents: int) -> None:
    if headroom(mw) < cents:
        raise ActionRefused(f"cash and unused credit cannot cover ${cents / 100:,.0f}")


def _step(mw: ManagedWorld, machine: mc.Machine, entity_id: str, state, event):
    return mc.step(mw.world.business.transitions, mw.day, machine, entity_id, state, event)


def set_price(mw: ManagedWorld, location: str, item: str, price_cents: int) -> ActionResult:
    entry = _entry(_location(mw, location), item)
    typical = CATALOG[ItemId(item)].typical_price_cents
    if not 0.3 * typical <= price_cents <= 3 * typical:
        raise ActionRefused(f"{item} must be priced between 30% and 300% of ${typical / 100:.2f}")
    old, entry.price_cents = entry.price_cents, Cents(price_cents)
    return ActionResult(f"{item} at {location}: ${old / 100:.2f} -> ${price_cents / 100:.2f}")


def add_menu_item(mw: ManagedWorld, location: str, item: str) -> ActionResult:
    loc = _location(mw, location)
    key = ItemId(item)
    if key in loc.menu and loc.menu[key].state in ACTIVE_MENU:
        raise ActionRefused(f"{item} is already on the menu at {location}")
    if sum(e.state in ACTIVE_MENU for e in loc.menu.values()) >= MAX_MENU_ITEMS:
        raise ActionRefused(f"the menu already has {MAX_MENU_ITEMS} items")
    recipe = CATALOG[key]
    plan = [0 if recipe.made_to_order else TRIAL_PLAN_UNITS] * 7
    entry = MenuEntry(MenuState.CATALOG, recipe.typical_price_cents, plan)
    entry.state = _step(mw, mc.MENU_ITEM, item, entry.state, mc.MenuEvent.START_TRIAL)
    loc.menu[key] = entry
    loc.menu_changed_day = mw.day
    mw.trials[(loc.id, key)] = mw.day + TRIAL_DAYS
    return ActionResult(
        f"{recipe.name} on trial at {location} for {TRIAL_DAYS} days at "
        f"${recipe.typical_price_cents / 100:.2f}, {TRIAL_PLAN_UNITS} a day"
    )


def remove_menu_item(mw: ManagedWorld, location: str, item: str) -> ActionResult:
    loc = _location(mw, location)
    entry = _entry(loc, item)
    if sum(e.state in ACTIVE_MENU for e in loc.menu.values()) <= MIN_MENU_ITEMS:
        raise ActionRefused(f"the menu must keep at least {MIN_MENU_ITEMS} items")
    event = (
        mc.MenuEvent.RETIRE if entry.state is MenuState.ON_MENU else mc.MenuEvent.DROP_AFTER_TRIAL
    )
    entry.state = _step(mw, mc.MENU_ITEM, item, entry.state, event)
    mw.trials.pop((loc.id, ItemId(item)), None)
    mw.par_rules.pop((loc.id, ItemId(item)), None)
    return ActionResult(f"{item} taken off the menu at {location}")


def set_bake_plan(
    mw: ManagedWorld, location: str, item: str, weekday_units: list[int]
) -> ActionResult:
    loc = _location(mw, location)
    entry = _entry(loc, item, baked=True)
    entry.bake_plan = list(weekday_units)
    mw.par_rules.pop((loc.id, ItemId(item)), None)
    return ActionResult(f"{item} at {location} will bake {weekday_units} (Monday to Sunday)")


def par_plan(mw: ManagedWorld, loc: LocationId, item: ItemId, rule: ParRule) -> list[int] | None:
    plan = []
    for weekday in range(7):
        seen = list(mw.demand.get((loc, item, weekday), ()))[-rule.lookback_weeks :]
        if not seen:
            return None
        ranked = sorted(seen)
        plan.append(
            ranked[min(len(ranked) - 1, math.ceil(rule.percentile / 100 * len(ranked)) - 1)]
        )
    return plan


def apply_par_rules(mw: ManagedWorld) -> None:
    for (loc_id, item), rule in mw.par_rules.items():
        entry = mw.world.business.locations[loc_id].menu[item]
        plan = par_plan(mw, loc_id, item, rule)
        if plan is not None:
            entry.bake_plan = plan


def set_par_rule(
    mw: ManagedWorld, location: str, item: str, percentile: int, lookback_weeks: int
) -> ActionResult:
    loc = _location(mw, location)
    entry = _entry(loc, item, baked=True)
    rule = ParRule(percentile, lookback_weeks)
    mw.par_rules[(loc.id, ItemId(item))] = rule
    plan = par_plan(mw, loc.id, ItemId(item), rule)
    if plan is not None:
        entry.bake_plan = plan
    shown = plan if plan is not None else "kept until there is a week of demand to read"
    return ActionResult(
        f"{item} at {location} bakes the {percentile}th percentile of the last "
        f"{lookback_weeks} weeks' demand each weekday, recomputed weekly; plan {shown}"
    )


def set_hours(mw: ManagedWorld, location: str, open_hour: int, close_hour: int) -> ActionResult:
    loc = _location(mw, location, LocationState.TRADING, LocationState.FITTING_OUT)
    if close_hour - open_hour < 4:
        raise ActionRefused("the shop must open for at least 4 hours")
    loc.open_hour, loc.close_hour = open_hour, close_hour
    gaps = counter_gaps(list(mw.world.business.staff.values()), loc)
    return ActionResult(
        f"{location} opens {open_hour}:00 to {close_hour}:00 from today; staff rotas are "
        "unchanged and shifts outside the hours they may work are cut to fit" + _gap_note(gaps),
        {"counter_uncovered_hours": gaps},
    )


def _shift(day: str, text: str) -> ShiftTime | None:
    if text == "off":
        return None
    start, sep, end = text.partition("-")
    if not (sep and start.isdigit() and end.isdigit()):
        raise ActionRefused(f"{day}: {text!r} must be 'off' or start-end hours such as '07-15'")
    return ShiftTime(int(start), int(end))


def set_rota(mw: ManagedWorld, staff: str, shifts: list[str]) -> ActionResult:
    s = _staff(mw, staff)
    loc = mw.world.business.locations[s.location_id]
    low, high = shift_window(s.role, loc)
    rota = []
    for day, text in zip(WEEKDAYS, shifts, strict=True):
        shift = _shift(day, text)
        if shift and shift.start_hour >= shift.end_hour:
            raise ActionRefused(f"{day}: {text} ends before it starts")
        if shift and not low <= shift.start_hour < shift.end_hour <= high:
            raise ActionRefused(
                f"{day}: {text} is outside {low:02d}-{high:02d}, the hours a {s.role.value} can "
                f"work while {loc.id} opens {loc.open_hour:02d}-{loc.close_hour:02d}"
            )
        if shift and shift.hours > MAX_DAY_HOURS:
            raise ActionRefused(f"{day}: {shift.hours} hours is more than {MAX_DAY_HOURS} a day")
        rota.append(shift)
    total, before = rota_hours(tuple(rota)), rota_hours(s.rota)
    if total > MAX_WEEK_HOURS:
        raise ActionRefused(f"that is {total} hours a week, more than {MAX_WEEK_HOURS}")
    s.rota = tuple(rota)
    gaps = counter_gaps(list(mw.world.business.staff.values()), loc)
    return ActionResult(
        f"{s.name}: {' '.join(describe(s.rota))} from today, {total} hours a week (was {before})"
        + _gap_note(gaps),
        {"weekly_hours": total, "counter_uncovered_hours": gaps},
    )


def _gap_note(gaps: dict[str, list[str]]) -> str:
    if not gaps:
        return ""
    return "; no counter worker on the rota " + ", ".join(
        f"{day} {' '.join(spans)}" for day, spans in gaps.items()
    )


def _next_staff_seq(mw: ManagedWorld) -> int:
    ids = list(mw.world.business.staff) + list(mw.candidates)
    return 1 + max((int(i[1:]) for i in ids if i[1:].isdigit()), default=-1)


def post_job(mw: ManagedWorld, location: str, role: str, wage_cents_per_hour: int) -> ActionResult:
    loc = _location(mw, location, LocationState.TRADING, LocationState.FITTING_OUT)
    wage = _wage(mw, wage_cents_per_hour)
    job = Role(role)
    market = market_wage(mw.world, job)
    rng = mw.rng("job", mw.jobs_posted)
    mw.jobs_posted += 1
    count = 2 + (wage >= market) + (wage >= 1.1 * market)
    lift = 0.25 * min(0.3, max(-0.2, wage / market - 1))
    arrivals = []
    for _ in range(count):
        seq = _next_staff_seq(mw)
        arrives = mw.day + rng.randint(1, 7)
        staff = Staff(
            id=StaffId(f"s{seq}"),
            name=HIRE_NAMES[seq % len(HIRE_NAMES)],
            role=job,
            state=StaffState.CANDIDATE,
            location_id=loc.id,
            wage_cents_per_hour=wage,
            skill=round(min(1.0, max(0.1, rng.uniform(0.35, 0.75) + lift)), 3),
            speed=rng.uniform(0.9, 1.1),
            reliability=rng.uniform(0.7, 0.98),
            morale=0.7,
        )
        mw.candidates[staff.id] = Candidate(
            staff,
            mw.day,
            arrives,
            arrives + CANDIDATE_DAYS,
            Cents(round(market * rng.uniform(0.92, 1.08))),
            rng.gauss(0, CV_NOISE),
            rng.gauss(0, INTERVIEW_NOISE),
        )
        arrivals.append(arrives)
    return ActionResult(
        f"{role} job at {location} posted at ${wage / 100:.2f} an hour; "
        f"{count} applicants will arrive over the next week",
        {"applicants": count},
    )


def _candidate(mw: ManagedWorld, candidate: str, *states: StaffState) -> Candidate:
    c = mw.candidates.get(StaffId(candidate))
    if c is None or c.arrives_day > mw.day or c.staff.state is StaffState.GONE:
        raise ActionRefused(f"there is no applicant {candidate!r}")
    if c.staff.state not in states:
        raise ActionRefused(f"{candidate} is {c.staff.state.value}, it must be {states[0].value}")
    return c


def interview(mw: ManagedWorld, candidate: str) -> ActionResult:
    c = _candidate(mw, candidate, StaffState.CANDIDATE)
    if mw.manager_hours < 1:
        raise ActionRefused("no manager hours left this week")
    mw.manager_hours -= 1
    c.staff.state = _step(mw, mc.STAFF, c.id, c.staff.state, mc.StaffEvent.INTERVIEW)
    return ActionResult(
        f"interviewed {c.staff.name}: skill about {c.skill_estimate:.2f} (give or take "
        f"{2 * INTERVIEW_NOISE:.2f})",
        {"skill_estimate": c.skill_estimate},
    )


def hire(mw: ManagedWorld, candidate: str, wage_cents_per_hour: int) -> ActionResult:
    c = _candidate(mw, candidate, StaffState.INTERVIEWED)
    wage = _wage(mw, wage_cents_per_hour)
    s = c.staff
    s.state = _step(mw, mc.STAFF, s.id, s.state, mc.StaffEvent.OFFER)
    if wage < c.reservation_cents:
        s.state = _step(mw, mc.STAFF, s.id, s.state, mc.StaffEvent.DECLINE)
        return ActionResult(f"{s.name} declined ${wage / 100:.2f} an hour", {"hired": False})
    s.state = _step(mw, mc.STAFF, s.id, s.state, mc.StaffEvent.ACCEPT)
    s.wage_cents_per_hour, s.hired_day = wage, mw.day
    assign_rota(mw.world.business.staff, s, mw.world.business.locations[s.location_id])
    mw.world.business.staff[s.id] = s
    del mw.candidates[s.id]
    return ActionResult(
        f"{s.name} hired as {s.role.value} at {s.location_id} for ${wage / 100:.2f} an hour, "
        "starting tomorrow",
        {"hired": True, "staff": s.id},
    )


def set_wage(mw: ManagedWorld, staff: str, wage_cents_per_hour: int) -> ActionResult:
    s = _staff(mw, staff)
    wage = _wage(mw, wage_cents_per_hour)
    old, s.wage_cents_per_hour = s.wage_cents_per_hour, wage
    return ActionResult(f"{s.name}: ${old / 100:.2f} -> ${wage / 100:.2f} an hour")


def fire(mw: ManagedWorld, staff: str) -> ActionResult:
    s = _staff(mw, staff)
    let_go(mw, s, "dismissal, two weeks' pay")
    for other in mw.world.business.staff.values():
        if other.location_id == s.location_id and other.state in WORKING and not other.owner:
            other.morale = max(0.05, other.morale - FIRE_MORALE_HIT)
    return ActionResult(f"{s.name} let go with two weeks' pay")


def choose_supplier(mw: ManagedWorld, ingredient: str, supplier: str) -> ActionResult:
    b = mw.world.business
    ing = Ingredient(ingredient)
    if supplier not in b.suppliers or ing not in b.suppliers[supplier].markup:
        raise ActionRefused(f"{supplier} does not sell {ingredient}")
    old, b.buy_from[ing] = b.buy_from[ing], supplier
    return ActionResult(f"{ingredient} now bought from {b.suppliers[supplier].name} (was {old})")


def buy_market_research(mw: ManagedWorld, neighborhood: str) -> ActionResult:
    _afford(mw, RESEARCH_CENTS)
    nb = mw.world.town[NeighborhoodId(neighborhood)]
    rng = mw.rng("research", neighborhood, len(mw.research))
    traffic = sum(nb.hourly_traffic) / 75
    visits = nb.population * BAKERY_GOER_SHARE * traffic * rng.uniform(0.85, 1.15)
    raw = {
        seg: max(0.0, share * mw.world.segment_shift.get((nb.id, seg), 1.0) + rng.gauss(0, 0.04))
        for seg, share in nb.segment_mix.items()
    }
    total = sum(raw.values())
    found = {
        "neighborhood": neighborhood,
        "daily_bakery_visits_estimate": round(visits),
        "segment_mix_estimate": {seg.value: round(v / total, 2) for seg, v in raw.items()},
        "income_index_estimate": round(nb.income_index * rng.uniform(0.92, 1.08), 2),
    }
    post(
        mw.world.business,
        mw.day,
        Account.MARKETING,
        -RESEARCH_CENTS,
        None,
        f"market research: {nb.name}",
    )
    mw.research[nb.id] = found
    return ActionResult(f"market research for {nb.name} delivered", found)


def scout_site(mw: ManagedWorld, neighborhood: str) -> ActionResult:
    nb = mw.world.town[NeighborhoodId(neighborhood)]
    taken = [
        loc
        for loc in mw.world.business.locations.values()
        if loc.neighborhood_id == nb.id and loc.state is not LocationState.CLOSED
    ]
    if taken:
        raise ActionRefused(f"there is already a shop in {nb.name}")
    if mw.manager_hours < 1:
        raise ActionRefused("no manager hours left this week")
    mw.manager_hours -= 1
    rng = mw.rng("site", neighborhood)
    size = rng.randrange(45, 101, 5)
    site = Site(
        f"site-{neighborhood}",
        nb.id,
        size,
        Cents(round(size * nb.rent_cents_per_m2_month * rng.uniform(0.9, 1.1))),
        Cents(int(round(size * rng.uniform(50_000, 70_000), -3))),
        rng.randint(28, 42),
    )
    mw.sites[site.id] = site
    return ActionResult(
        f"{site.id}: {size} m2 in {nb.name}, ${site.rent_cents_per_month / 100:,.0f} a month, "
        f"fit-out ${site.fitout_cents / 100:,.0f} over {site.fitout_days} days",
        site_facts(site),
    )


def site_facts(site: Site) -> dict:
    return {
        "site": site.id,
        "neighborhood": site.neighborhood_id,
        "size_m2": site.size_m2,
        "rent_cents_per_month": site.rent_cents_per_month,
        "fitout_cents": site.fitout_cents,
        "fitout_days": site.fitout_days,
    }


def sign_lease(mw: ManagedWorld, site: str) -> ActionResult:
    found = mw.sites.get(site)
    if found is None:
        raise ActionRefused(f"{site!r} has not been scouted")
    b = mw.world.business
    if sum(loc.state is not LocationState.CLOSED for loc in b.locations.values()) >= MAX_LOCATIONS:
        raise ActionRefused(f"the business already has {MAX_LOCATIONS} locations")
    _afford(mw, found.fitout_cents)
    loc_id = LocationId(f"shop{len(b.locations) + 1}")
    first_oven = sum(len(loc.ovens) for loc in b.locations.values()) + 1
    ovens = [Equipment(f"oven{first_oven + n}") for n in range(max(1, found.size_m2 // 30))]
    lease = Lease(
        mc.LeaseState.ACTIVE, found.rent_cents_per_month, mw.day + NEW_SHOP_LEASE_YEARS * 365
    )
    home = b.locations[HOME_LOCATION]
    loc = Location(
        loc_id,
        found.neighborhood_id,
        found.size_m2,
        ovens,
        LocationState.SCOUTED,
        lease,
        {},
        home.open_hour,
        home.close_hour,
        reputation=0.6,
        awareness=0.05,
        menu_changed_day=mw.day,
    )
    for event in (mc.LocationEvent.SIGN_LEASE, mc.LocationEvent.START_FITOUT):
        loc.state = _step(mw, mc.LOCATION, loc_id, loc.state, event)
    b.locations[loc_id] = loc
    post(b, mw.day, Account.FIT_OUT, -found.fitout_cents, loc_id, f"fit-out, {found.id}")
    mw.assets.append(
        Asset(f"{loc_id} fit-out", loc_id, found.fitout_cents, mw.day, FITOUT_LIFE_DAYS)
    )
    mw.assets += [
        Asset(o.id, loc_id, Cents(OVEN_COST_CENTS), mw.day, OVEN_LIFE_DAYS) for o in ovens
    ]
    mw.openings[loc_id] = mw.day + found.fitout_days
    del mw.sites[site]
    return ActionResult(
        f"lease signed: {loc_id} in {found.neighborhood_id} opens in {found.fitout_days} days; "
        "it needs its own staff",
        {"location": loc_id, "opens_day": mw.openings[loc_id]},
    )


def open_doors(mw: ManagedWorld, loc: Location) -> None:
    home = mw.world.business.locations[HOME_LOCATION]
    share = NEW_SHOP_BAKE_SHARE * loc.size_m2 / home.size_m2
    loc.menu = {
        item: MenuEntry(
            MenuState.ON_MENU, e.price_cents, [math.ceil(u * share) for u in e.bake_plan]
        )
        for item, e in home.menu.items()
        if e.state is MenuState.ON_MENU
    }
    loc.state = _step(mw, mc.LOCATION, loc.id, loc.state, mc.LocationEvent.OPEN_DOORS)


def close_location(mw: ManagedWorld, location: str) -> ActionResult:
    loc = _location(mw, location, LocationState.TRADING, LocationState.FITTING_OUT)
    b = mw.world.business
    trading = [x for x in b.locations.values() if x.state is LocationState.TRADING]
    if trading == [loc]:
        raise ActionRefused("cannot close the only trading shop")
    fee = 3 * loc.lease.monthly_rent_cents
    post(b, mw.day, Account.LEASE_BREAK, -fee, loc.id, f"{location} lease surrender")
    shut_location(mw, loc, f"{location} closed")
    return ActionResult(f"{location} closed; lease surrendered for ${fee / 100:,.0f}")


def spend_marketing(
    mw: ManagedWorld, location: str, channel: str, amount_cents: int
) -> ActionResult:
    loc = _location(mw, location, LocationState.TRADING, LocationState.FITTING_OUT)
    _afford(mw, amount_cents)
    ch = Channel(channel)
    post(mw.world.business, mw.day, Account.MARKETING, -amount_cents, loc.id, f"{channel} campaign")
    key = (loc.id, ch)
    mw.ad_stock[key] = mw.ad_stock.get(key, 0.0) + amount_cents * CHANNELS[ch].efficiency
    return ActionResult(f"${amount_cents / 100:,.0f} of {channel} for {location}")


def run_promotion(
    mw: ManagedWorld, location: str, item: str, discount_pct: int, days: int
) -> ActionResult:
    entry = _entry(_location(mw, location), item)
    entry.discount_pct, entry.discount_until_day = discount_pct, mw.day + days
    return ActionResult(f"{item} at {location}: {discount_pct}% off for {days} days")


def draw_credit(mw: ManagedWorld, amount_cents: int) -> ActionResult:
    b = mw.world.business
    if amount_cents > b.credit_limit_cents - b.credit_drawn_cents:
        raise ActionRefused("that is more than the unused credit line")
    b.credit_drawn_cents = Cents(b.credit_drawn_cents + amount_cents)
    post(b, mw.day, Account.CREDIT_DRAWN, amount_cents, None, "drawn on credit line")
    return ActionResult(
        f"drew ${amount_cents / 100:,.0f}; ${b.credit_drawn_cents / 100:,.0f} drawn"
    )


def repay_credit(mw: ManagedWorld, amount_cents: int) -> ActionResult:
    b = mw.world.business
    if amount_cents > b.credit_drawn_cents:
        raise ActionRefused("that is more than is drawn")
    if amount_cents > b.cash_cents:
        raise ActionRefused("not enough cash")
    b.credit_drawn_cents = Cents(b.credit_drawn_cents - amount_cents)
    post(b, mw.day, Account.CREDIT_REPAID, -amount_cents, None, "credit line repaid")
    return ActionResult(
        f"repaid ${amount_cents / 100:,.0f}; ${b.credit_drawn_cents / 100:,.0f} drawn"
    )


def _event(mw: ManagedWorld, event: str) -> Curveball:
    cb = next((c for c in mw.curveballs if c.id == event and c.start_day <= mw.day), None)
    if cb is None:
        raise ActionRefused(f"there is no event {event!r}")
    if cb.state is not CurveballState.AWAITING_RESPONSE:
        raise ActionRefused(f"{event} is {cb.state.value}, it is not awaiting a response")
    return cb


def respond_to_event(mw: ManagedWorld, event: str, option: str) -> ActionResult:
    cb = _event(mw, event)
    if option not in cb.spec.options:
        raise ActionRefused(f"{option!r} is not an option; choose {', '.join(cb.spec.options)}")
    return ActionResult(respond(mw, cb, option))


LOC = Param("location", str, "location id, e.g. shop1")
ITEM = Param("item", str, "catalog item id", choices=tuple(CATALOG))
WAGE = Param("wage_cents_per_hour", int, "hourly wage in cents", 1000, 6000)
AMOUNT = Param("amount_cents", int, "amount in cents", 1, 10_000_000)
NEIGHBORHOOD = Param("neighborhood", str, "neighborhood id", choices=NEIGHBORHOODS)

ACTIONS: dict[str, Action] = {
    a.name: a
    for a in (
        Action(
            "set_price",
            "Set an item's price at a location, effective from today's opening.",
            (LOC, ITEM, Param("price_cents", int, "price in cents", 25, 5000)),
            set_price,
        ),
        Action(
            "add_menu_item",
            f"Put a catalog item on trial for {TRIAL_DAYS} days, then on the menu.",
            (LOC, ITEM),
            add_menu_item,
        ),
        Action("remove_menu_item", "Take an item off the menu.", (LOC, ITEM), remove_menu_item),
        Action(
            "set_bake_plan",
            "Fix how many units to bake each weekday, Monday first.",
            (LOC, ITEM, Param("weekday_units", list, "units per weekday", 0, 400, length=7)),
            set_bake_plan,
        ),
        Action(
            "set_par_rule",
            "Bake a percentile of recent demand per weekday, recomputed weekly.",
            (
                LOC,
                ITEM,
                Param("percentile", int, "demand percentile to bake", 50, 99),
                Param("lookback_weeks", int, "weeks of demand to read", 1, 8),
            ),
            set_par_rule,
        ),
        Action(
            "set_hours",
            "Set opening and closing hours, effective from today.",
            (
                LOC,
                Param("open_hour", int, "opening hour", 5, 12),
                Param("close_hour", int, "closing hour", 12, 22),
            ),
            set_hours,
        ),
        Action(
            "set_rota",
            "Set a staff member's shifts for each weekday, Monday first, from today; pay "
            "follows the hours worked.",
            (
                Param("staff", str, "staff id"),
                Param(
                    "shifts",
                    list,
                    "7 entries, Monday first: 'off' or start-end hours such as '07-15'; bakers "
                    f"may start {PRE_OPEN_BAKE_HOURS} hours before opening; at most "
                    f"{MAX_DAY_HOURS} hours a day and {MAX_WEEK_HOURS} a week",
                    length=7,
                    items=str,
                ),
            ),
            set_rota,
        ),
        Action(
            "post_job",
            "Advertise a job; 2 to 4 applicants arrive over the next week.",
            (LOC, Param("role", str, "role", choices=tuple(Role)), WAGE),
            post_job,
        ),
        Action(
            "interview",
            "Interview an applicant (one manager hour) to sharpen the skill estimate.",
            (Param("candidate", str, "applicant id"),),
            interview,
        ),
        Action(
            "hire",
            "Offer an interviewed applicant a wage; they accept if it meets their reservation.",
            (Param("candidate", str, "applicant id"), WAGE),
            hire,
        ),
        Action(
            "set_wage",
            "Change a staff member's wage.",
            (Param("staff", str, "staff id"), WAGE),
            set_wage,
        ),
        Action(
            "fire",
            "Let a staff member go with two weeks' pay; colleagues' morale drops.",
            (Param("staff", str, "staff id"),),
            fire,
        ),
        Action(
            "choose_supplier",
            "Buy an ingredient from another supplier.",
            (
                Param("ingredient", str, "ingredient", choices=tuple(Ingredient)),
                Param("supplier", str, "supplier id"),
            ),
            choose_supplier,
        ),
        Action(
            "buy_market_research",
            f"Buy noisy traffic and mix estimates (${RESEARCH_CENTS // 100:,}).",
            (NEIGHBORHOOD,),
            buy_market_research,
        ),
        Action(
            "scout_site",
            "Find a site in a neighborhood (one manager hour): size, rent, fit-out cost.",
            (NEIGHBORHOOD,),
            scout_site,
        ),
        Action(
            "sign_lease",
            f"Lease a scouted site for {NEW_SHOP_LEASE_YEARS} years and fit it out; at most "
            f"{MAX_LOCATIONS} open locations.",
            (Param("site", str, "site id"),),
            sign_lease,
        ),
        Action(
            "close_location",
            "Close a location; the lease is surrendered for 3 months' rent.",
            (LOC,),
            close_location,
        ),
        Action(
            "spend_marketing",
            "Spend on a marketing channel; awareness rises and the effect decays.",
            (
                LOC,
                Param("channel", str, "channel", choices=tuple(Channel)),
                Param("amount_cents", int, "spend in cents", 5_000, 1_000_000),
            ),
            spend_marketing,
        ),
        Action(
            "run_promotion",
            "Discount an item for a number of days.",
            (
                LOC,
                ITEM,
                Param("discount_pct", int, "discount percent", 5, 50),
                Param("days", int, "days", 1, 28),
            ),
            run_promotion,
        ),
        Action("draw_credit", "Draw on the credit line.", (AMOUNT,), draw_credit),
        Action("repay_credit", "Repay the credit line.", (AMOUNT,), repay_credit),
        Action(
            "respond_to_event",
            "Choose a response to an event awaiting one.",
            (Param("event", str, "event id"), Param("option", str, "one of the event's options")),
            respond_to_event,
        ),
    )
}


def perform(mw: ManagedWorld, name: str, **args: object) -> ActionResult:
    try:
        action = ACTIONS.get(name)
        if action is None or (name in EXPANSION_ACTIONS and not mw.allow_expansion):
            raise ActionRefused(f"there is no action {name!r}")
        expected = {p.name for p in action.params}
        if set(args) != expected:
            raise ActionRefused(f"{name} takes {', '.join(sorted(expected))}")
        for p in action.params:
            p.check(args[p.name])
        result = action.fn(mw, **args)
    except ActionRefused as refused:
        mw.note(LogKind.REFUSAL, action=name, args=args, reason=refused.reason)
        raise
    mw.note(LogKind.ACTION, action=name, args=args, result=result.message, data=result.data)
    return result


EXPANSION_ACTIONS = frozenset({"scout_site", "sign_lease", "close_location"})


def describe_actions(allow_expansion: bool = False) -> list[dict]:
    return [
        a.schema() for a in ACTIONS.values() if allow_expansion or a.name not in EXPANSION_ACTIONS
    ]
