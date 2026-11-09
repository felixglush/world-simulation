import math
from dataclasses import dataclass, field
from pathlib import Path

from bakery_world.actions import CASUAL_PREMIUM, ActionRefused, minimum_wage, par_plan, perform
from bakery_world.basics import Category, Ingredient, ItemId, LocationId, Role, StaffId
from bakery_world.catalog import CATALOG
from bakery_world.curveballs import Curveball, Kind
from bakery_world.engine import (
    BAKER_HANDS_ON,
    PACKAGING_CENTS,
    PAYROLL_ONCOST_RATE,
    STRAIN_FREE_LOAD,
    market_wage,
)
from bakery_world.history import generate_history
from bakery_world.machines import CustomerState, LocationState, MenuState, StaffState
from bakery_world.managed import ManagedWorld, ParRule
from bakery_world.management import manage
from bakery_world.model import Location, ShiftTime, Staff
from bakery_world.observe import (
    candidates,
    price_list,
    sales_by_hour,
    sites_scouted,
)
from bakery_world.observe import staff as staff_list
from bakery_world.rota import (
    MAX_DAY_HOURS,
    SHIFT_HOURS,
    WEEKDAYS,
    counter_gaps,
    describe,
    standard_shift,
)
from bakery_world.town import HOME_LOCATION, proximity

QUARTER_WEEKS = 13
HOURS_EVERY_WEEKS = 4
FOOD_COST_TARGET = 0.30
MAX_PRICE_MOVE = 0.10
PAR_PERCENTILE, PAR_WEEKS = 80, 4
EXTEND_SHARE, TRIM_SHARE = 0.5, 0.12
HIRE_RETRY_RAISE = 1.08
RELIEF_HOURS = 40
WEEK_DAYS = 7
ONCOST = 1 + PAYROLL_ONCOST_RATE
HOUR_MARGIN = 0.6  # what is left of a sale after ingredients, packaging and card fees
LOST_VISITS = 2  # a customer who walks out of the queue costs that sale and about one more
PEAK_SHIFT_HOURS = 3
CUSTOMERS_PER_CLEANING_HOUR = 80
STRAIN_OK, BAKE_FIT_STEP = 0.03, 0.93
MENU_REFRESH_WEEKS = 8
FLYER_WEEKS, FLYER_CENTS = 4, 50_000
WORKING = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)


def _try(mw: ManagedWorld, name: str, **args: object) -> object | None:
    try:
        return perform(mw, name, **args)
    except ActionRefused:
        return None


def _trading(mw: ManagedWorld) -> list[LocationId]:
    locs = mw.world.business.locations.values()
    return sorted(loc.id for loc in locs if loc.state is LocationState.TRADING)


def _shift_hours(rota: list[str]) -> int:
    return sum(int(x[3:]) - int(x[:2]) for x in rota if x != "off")


def _give(relief: dict[str, list[str]], day: int, shift: ShiftTime) -> bool:
    """Put a shift on the first relief rota free that day with room in the week."""
    for rota in relief.values():
        if rota[day] == "off" and _shift_hours(rota) + shift.hours <= RELIEF_HOURS:
            rota[day] = str(shift)
            return True
    return False


def _round25(cents: float) -> int:
    return max(25, round(cents / 25) * 25)


def fit_rotas(mw: ManagedWorld, loc: LocationId, keep: frozenset[str] = frozenset()) -> None:
    """Standard shifts, with counter starts spread evenly from opening to the last full shift;
    the rotas of the staff in `keep` are left as they are."""
    shop = mw.world.business.locations[loc]
    here = [
        s
        for s in staff_list(mw)
        if s["location"] == loc and not s["owner"] and s["staff"] not in keep
    ]
    counters = [s["staff"] for s in here if s["role"] == Role.COUNTER.value]
    for s in here:
        shift = standard_shift(Role(s["role"]), shop)
        if s["staff"] in counters and len(counters) > 1:
            room = shop.close_hour - shop.open_hour - shift.hours
            start = shop.open_hour + round(counters.index(s["staff"]) * room / (len(counters) - 1))
            shift = ShiftTime(start, start + shift.hours)
        shifts = ["off" if day == "off" else str(shift) for day in s["rota"]]
        if shifts != s["rota"]:
            _try(mw, "set_rota", staff=s["staff"], shifts=shifts)


@dataclass
class Freeze:
    name: str = "freeze"
    keeps_founder: bool = False
    auto_hire: bool = True

    def weekly(self, world: ManagedWorld, week: int) -> None:
        return None

    def interrupt(self, world: ManagedWorld, event: Curveball) -> None:
        return None


@dataclass
class Founder(Freeze):
    name: str = "founder"
    keeps_founder: bool = True


SENSIBLE: dict[Kind, str] = {
    Kind.OVEN_BREAKS: "repair",
    Kind.HEAD_BAKER_QUITS: "counter_offer",
    Kind.LANDLORD_RENT: "negotiate",
    Kind.HEALTH_INSPECTION: "pay",
    Kind.CATERING_ORDER: "accept",
}


@dataclass
class Heuristic:
    """A careful manager working only from what the shop's records show."""

    name: str = "heuristic"
    keeps_founder: bool = False
    auto_hire: bool = False
    headcount: dict[tuple[LocationId, Role], int] = field(default_factory=dict)
    posted: dict[tuple[LocationId, Role, bool], int] = field(default_factory=dict)
    paid: dict[Role, int] = field(default_factory=dict)
    short: set[tuple[LocationId, Role]] = field(default_factory=set)
    trimmed: dict[LocationId, set[int]] = field(default_factory=dict)
    usual: dict[str, list[bool]] = field(default_factory=dict)
    bake_fit: dict[LocationId, float] = field(default_factory=dict)
    patterns: dict[tuple[LocationId, Role], list[list[str]]] = field(default_factory=dict)

    def weekly(self, mw: ManagedWorld, week: int) -> None:
        if week == 0:
            self.headcount = self._headcount(mw)
            self.cheapest_suppliers(mw)
        for loc in _trading(mw):
            if week % QUARTER_WEEKS == 0:
                self.reprice(mw, loc)
            if week and week % HOURS_EVERY_WEEKS == 0:
                self.fit_hours(mw, loc)
            if week % MENU_REFRESH_WEEKS == 0:
                self.refresh_menu(mw, loc)
            if week % FLYER_WEEKS == 0:
                self.advertise(mw, loc)
            self.fit_team(mw, loc)
            self.plan_bakes(mw, loc)
        self.staff_up(mw)

    def refresh_menu(self, mw: ManagedWorld, loc: LocationId) -> None:
        """Every couple of months, try the baked item off the menu with the best margin at its
        usual price, so regulars have something new."""
        shop = mw.world.business.locations[loc]
        on = {i for i, e in shop.menu.items() if e.state in (MenuState.ON_MENU, MenuState.TRIAL)}
        fresh = [i for i in CATALOG if i not in on and CATALOG[i].category is not Category.DRINK]
        if fresh:
            best = max(
                fresh, key=lambda i: (CATALOG[i].typical_price_cents - self.unit_cost(mw, i), i)
            )
            _try(mw, "add_menu_item", location=loc, item=best)

    def advertise(self, mw: ManagedWorld, loc: LocationId) -> None:
        _try(mw, "spend_marketing", location=loc, channel="flyers", amount_cents=FLYER_CENTS)

    def interrupt(self, mw: ManagedWorld, event: Curveball) -> None:
        if event.kind in SENSIBLE:
            _try(mw, "respond_to_event", event=event.id, option=SENSIBLE[event.kind])

    def _headcount(self, mw: ManagedWorld) -> dict[tuple[LocationId, Role], int]:
        """The contracted team the shop was handed, the founder aside."""
        left = {
            t.entity_id
            for t in mw.world.business.transitions
            if t.day >= mw.handoff_day and t.after == StaffState.GONE.name
        }
        counts: dict[tuple[LocationId, Role], int] = {}
        for s in sorted(mw.world.business.staff.values(), key=lambda s: s.id):
            if not s.owner and not s.casual and (s.state in WORKING or s.id in left):
                key = (s.location_id, s.role)
                counts[key] = counts.get(key, 0) + 1
                self.patterns.setdefault(key, []).append(describe(s.rota))
        return counts

    def fit_team(self, mw: ManagedWorld, loc: LocationId) -> None:
        """Standard shifts for the contracted team, then relief for booked leave and for the
        counter hours nobody covers."""
        casuals = frozenset(s["staff"] for s in staff_list(mw) if s["casual"])
        fit_rotas(mw, loc, casuals)
        relief = {
            role: {s["staff"]: ["off"] * 7 for s in self.team(mw, loc, role) if s["casual"]}
            for role in Role
        }
        short = self.cover_leave(mw, loc, Role.BAKER, relief[Role.BAKER])
        self.mark_short(loc, Role.BAKER, short)
        self.mark_short(loc, Role.COUNTER, self.cover_counter(mw, loc, relief[Role.COUNTER]))
        self.mark_short(loc, Role.CLEANER, self.cover_cleaning(mw, loc, relief[Role.CLEANER]))
        for rotas in relief.values():
            for sid, rota in rotas.items():
                if describe(mw.world.business.staff[StaffId(sid)].rota) != rota:
                    _try(mw, "set_rota", staff=sid, shifts=rota)

    def cover_cleaning(
        self, mw: ManagedWorld, loc: LocationId, relief: dict[str, list[str]]
    ) -> bool:
        """About one cleaning hour per CUSTOMERS_PER_CLEANING_HOUR customers a week; the days
        the contracted cleaner does not work go to relief, spread through the week."""
        shop = mw.world.business.locations[loc]
        recent = [d for r in list(mw.reports)[-WEEK_DAYS:] for d in r.locations]
        visits = sum(d.visits for d in recent if d.location_id == loc)
        team = [s for s in self.team(mw, loc, Role.CLEANER) if not s["casual"]]
        shift = standard_shift(Role.CLEANER, shop)
        days = min(6, math.ceil(visits / CUSTOMERS_PER_CLEANING_HOUR / shift.hours))
        worked = {d for s in team for d, x in enumerate(s["rota"]) if x != "off"}
        extra = days - len(worked)
        short = False
        for d in sorted(
            range(7), key=lambda d: (d in worked, -min(abs(d - w) for w in worked or [d]))
        ):
            if extra <= 0 or d in worked:
                break
            short |= not _give(relief, d, shift)
            extra -= 1
        return short

    def mark_short(self, loc: LocationId, role: Role, short: bool) -> None:
        if short:
            self.short.add((loc, role))
        else:
            self.short.discard((loc, role))

    def team(self, mw: ManagedWorld, loc: LocationId, role: Role) -> list[dict]:
        return [
            s
            for s in staff_list(mw)
            if s["location"] == loc and s["role"] == role.value and not s["owner"]
        ]

    def wanted(self, mw: ManagedWorld, loc: LocationId, role: Role, base: int) -> int:
        """Contracted staff wanted in a role; relief is hired separately when short."""
        return base

    def peaks(self, mw: ManagedWorld, loc: LocationId) -> dict[int, ShiftTime]:
        """Per weekday, the span of hours where the queue turned away enough custom, over the
        last four weeks, to pay a second pair of hands at the counter."""
        shop = mw.world.business.locations[loc]
        recent = list(mw.reports)[-4 * WEEK_DAYS :]
        days = [d for r in recent for d in r.locations if d.location_id == loc]
        basket = sum(d.revenue_cents for d in days) / max(1, sum(d.visits for d in days))
        hour_cost = self.going_wage(mw, Role.COUNTER) * CASUAL_PREMIUM * ONCOST
        walked = [[0.0] * 24 for _ in range(WEEK_DAYS)]
        seen = [0] * WEEK_DAYS
        for r in recent:
            for d in r.locations:
                if d.location_id == loc:
                    seen[r.day % WEEK_DAYS] += 1
                    for h, n in enumerate(d.walkaways_by_hour):
                        walked[r.day % WEEK_DAYS][h] += n
        out = {}
        for wd in range(WEEK_DAYS):
            busy = [
                h
                for h in range(shop.open_hour, shop.close_hour)
                if walked[wd][h] / max(1, seen[wd]) * basket * HOUR_MARGIN * LOST_VISITS
                >= hour_cost
            ]
            if busy:
                start = min(busy[0], shop.close_hour - PEAK_SHIFT_HOURS)
                end = max(busy[-1] + 1, start + PEAK_SHIFT_HOURS)
                out[wd] = ShiftTime(max(shop.open_hour, start), min(shop.close_hour, end))
        return out

    def leave_gaps(self, team: list[dict], shop: Location) -> dict[int, ShiftTime]:
        """Per weekday this week, the counter hours a colleague's booked leave leaves empty."""
        out = {}
        for d, day in enumerate(WEEKDAYS):
            away = [s for s in team if day in s["booked_leave"]]
            if not away:
                continue
            here = [s["rota"][d] for s in team if s not in away and s["rota"][d] != "off"]
            hours = [
                h
                for s in away
                for h in range(int(s["rota"][d][:2]), int(s["rota"][d][3:]))
                if shop.open_hour <= h < shop.close_hour
                and not any(int(x[:2]) <= h < int(x[3:]) for x in here)
            ]
            if hours:
                out[d] = ShiftTime(min(hours), min(max(hours) + 1, min(hours) + MAX_DAY_HOURS))
        return out

    def cover_leave(
        self, mw: ManagedWorld, loc: LocationId, role: Role, relief: dict[str, list[str]]
    ) -> bool:
        """Give a colleague's booked leave day to a contracted teammate who is off that day and
        has room in their contract, or else to relief staff. Everyone is back on their usual
        days the week after. True when nobody could take a day."""
        team = [s for s in self.team(mw, loc, role) if not s["casual"]]
        for s in team:
            self.usual.setdefault(s["staff"], [x != "off" for x in s["rota"]])
        want = {
            s["staff"]: [x if self.usual[s["staff"]][d] else "off" for d, x in enumerate(s["rota"])]
            for s in team
        }
        room = {s["staff"]: s["contract_hours"] for s in team}
        short = False
        for s in team:
            for day in s["booked_leave"]:
                d = WEEKDAYS.index(day)
                shift = want[s["staff"]][d]
                if shift == "off":
                    continue
                free = [
                    o["staff"]
                    for o in team
                    if want[o["staff"]][d] == "off"
                    and day not in o["booked_leave"]
                    and _shift_hours(want[o["staff"]]) + _shift_hours([shift]) <= room[o["staff"]]
                ]
                if free:
                    want[min(free, key=lambda sid: _shift_hours(want[sid]))][d] = shift
                else:
                    start, end = (int(x) for x in shift.split("-"))
                    short |= not _give(relief, d, ShiftTime(start, end))
        for s in team:
            if want[s["staff"]] != s["rota"]:
                _try(mw, "set_rota", staff=s["staff"], shifts=want[s["staff"]])
        return short

    def cover_counter(
        self, mw: ManagedWorld, loc: LocationId, relief: dict[str, list[str]]
    ) -> bool:
        """Fill the counter hours the contracted rota leaves open, first with a teammate on a
        day off who has room in their contract, then with relief; relief also covers booked
        leave and doubles up at the hours a lone server keeps losing the queue. True when
        relief could not take it all."""
        everyone = list(mw.world.business.staff.values())
        shop = mw.world.business.locations[loc]
        gaps = counter_gaps([s for s in everyone if not s.casual], shop)
        team = [s for s in self.team(mw, loc, Role.COUNTER) if not s["casual"]]
        leave, peaks = self.leave_gaps(team, shop), self.peaks(mw, loc)
        short = False
        for d, day in enumerate(WEEKDAYS):
            need = None
            if day in gaps:
                spans = gaps[day]
                start, end = int(spans[0][:2]), int(spans[-1][3:])
                need = ShiftTime(start, min(end, start + MAX_DAY_HOURS))
                short |= end - start > MAX_DAY_HOURS
                free = [
                    s
                    for s in team
                    if s["rota"][d] == "off"
                    and day not in s["booked_leave"]
                    and s["weekly_hours"] + need.hours <= s["contract_hours"]
                ]
                if free:
                    s = min(free, key=lambda s: (s["weekly_hours"], s["staff"]))
                    shifts = [str(need) if i == d else x for i, x in enumerate(s["rota"])]
                    if _try(mw, "set_rota", staff=s["staff"], shifts=shifts):
                        s["rota"], s["weekly_hours"] = shifts, s["weekly_hours"] + need.hours
                        need = None
            for extra in (leave.get(d), peaks.get(d)):
                if extra is None:
                    continue
                if need is None:
                    need = extra
                    continue
                joined = ShiftTime(
                    min(need.start_hour, extra.start_hour), max(need.end_hour, extra.end_hour)
                )
                need = joined if joined.hours <= MAX_DAY_HOURS else need
            if need is not None:
                short |= not _give(relief, d, need)
        return short

    def took_on(self, mw: ManagedWorld, loc: LocationId, role: Role, staff: str) -> None:
        """A contracted newcomer works the days of a colleague who has left or is leaving."""
        team = [
            s.rota
            for s in mw.world.business.staff.values()
            if s.location_id == loc
            and s.role is role
            and s.state in (StaffState.EMPLOYED, StaffState.SICK)
            and s.id != staff
            and not s.casual
        ]
        held = [{d for d, x in enumerate(r) if x} for r in team]
        for pattern in self.patterns.get((loc, role), []):
            if {d for d, x in enumerate(pattern) if x != "off"} not in held:
                _try(mw, "set_rota", staff=staff, shifts=pattern)
                return

    def cheapest_suppliers(self, mw: ManagedWorld) -> None:
        prices = price_list(mw)
        for ing in Ingredient:
            offers = [(p[ing.value], sid) for sid, p in prices.items() if ing.value in p]
            _try(mw, "choose_supplier", ingredient=ing.value, supplier=min(offers)[1])

    def plan_bakes(self, mw: ManagedWorld, loc: LocationId) -> None:
        """Bake toward the 80th percentile of recent demand, but no more than the bakers on
        the rota can make without rushing; how much they manage is judged from how strained
        last week's bakes were."""
        shop = mw.world.business.locations[loc]
        rule = ParRule(PAR_PERCENTILE, PAR_WEEKS)
        plans = {}
        for item, e in sorted(shop.menu.items()):
            if e.state in (MenuState.ON_MENU, MenuState.TRIAL) and not CATALOG[item].made_to_order:
                plans[item] = par_plan(mw, loc, item, rule) or list(e.bake_plan)
        recent = [d for r in list(mw.reports)[-WEEK_DAYS:] for d in r.locations]
        strain = [d.bake_strain for d in recent if d.location_id == loc]
        fit = self.bake_fit.get(loc, 1.0)
        if strain and sum(strain) / len(strain) > STRAIN_OK:
            fit *= BAKE_FIT_STEP
        elif strain:
            fit = min(1.0, fit / BAKE_FIT_STEP)
        self.bake_fit[loc] = fit
        bakers = self.team(mw, loc, Role.BAKER)
        for d, day in enumerate(WEEKDAYS):
            hours = sum(
                _shift_hours([s["rota"][d]]) for s in bakers if day not in s["booked_leave"]
            )
            room = hours * 60 * BAKER_HANDS_ON * STRAIN_FREE_LOAD * fit
            need = sum(plan[d] * CATALOG[item].labor_minutes for item, plan in plans.items())
            if need > room:
                for plan in plans.values():
                    plan[d] = math.floor(plan[d] * room / need)
        for item, plan in plans.items():
            if plan != shop.menu[item].bake_plan:
                _try(mw, "set_bake_plan", location=loc, item=item, weekday_units=plan)

    def unit_cost(self, mw: ManagedWorld, item: ItemId) -> float:
        prices = price_list(mw)
        b = mw.world.business
        per_kg = {ing: prices[b.buy_from[ing]][ing.value] for ing in Ingredient}
        recipe = CATALOG[item]
        return (
            sum(g * per_kg[i] / 1000 for i, g in recipe.grams.items())
            + PACKAGING_CENTS[recipe.category]
        )

    def reprice(self, mw: ManagedWorld, loc: LocationId) -> None:
        """Raise prices toward the food-cost target; a cut would only add demand to serve."""
        for item, e in mw.world.business.locations[loc].menu.items():
            if e.state is not MenuState.ON_MENU:
                continue
            target = self.unit_cost(mw, item) / FOOD_COST_TARGET
            price = _round25(min(e.price_cents * (1 + MAX_PRICE_MOVE), max(e.price_cents, target)))
            if price != e.price_cents:
                _try(mw, "set_price", location=loc, item=item, price_cents=price)

    def fit_hours(self, mw: ManagedWorld, loc: LocationId) -> None:
        """Widen toward a busy edge hour and trim a quiet one, counting an hour as busy only
        when its margin would also pay the counter worker who staffs it. An hour once trimmed
        is not tried again."""
        shop = mw.world.business.locations[loc]
        sales = sales_by_hour(mw, loc, 4)
        peak = max(sales) or 1
        hour_cost = 4 * WEEK_DAYS * self.going_wage(mw, Role.COUNTER) * ONCOST / HOUR_MARGIN
        tried = self.trimmed.setdefault(loc, set())

        def busy(h: int, beyond: int) -> bool:
            return beyond not in tried and sales[h] >= max(EXTEND_SHARE * peak, hour_cost)

        def quiet(h: int) -> bool:
            return sales[h] < max(TRIM_SHARE * peak, hour_cost)

        open_hour, close_hour = shop.open_hour, shop.close_hour
        if busy(close_hour - 1, close_hour) and close_hour < 20:
            close_hour += 1
        elif quiet(close_hour - 1) and close_hour - open_hour > 8:
            close_hour -= 1
            tried.add(close_hour)
        if busy(open_hour, open_hour - 1) and open_hour > 6:
            open_hour -= 1
        elif quiet(open_hour) and close_hour - open_hour > 8:
            tried.add(open_hour)
            open_hour += 1
        if (open_hour, close_hour) != (shop.open_hour, shop.close_hour):
            _try(mw, "set_hours", location=loc, open_hour=open_hour, close_hour=close_hour)

    def going_wage(self, mw: ManagedWorld, role: Role) -> int:
        wages = sorted(
            s["wage_cents_per_hour"] for s in staff_list(mw) if s["role"] == role and not s["owner"]
        )
        if wages:
            self.paid[role] = wages[len(wages) // 2]
        return max(minimum_wage(mw), self.paid.get(role, 0))

    def skill(self, mw: ManagedWorld, candidate: dict) -> float:
        return candidate["skill_estimate"]

    def staff_up(self, mw: ManagedWorld) -> None:
        """Keep the contracted team at its size, and take on one more relief hand wherever
        relief could not cover the week."""
        staying = (StaffState.EMPLOYED.value, StaffState.SICK.value)
        for role in Role:
            self.going_wage(mw, role)  # remember the pay for a role that later empties
        for (loc, role), base in sorted(self.headcount.items()):
            if mw.world.business.locations[loc].state not in (
                LocationState.TRADING,
                LocationState.FITTING_OUT,
            ):
                continue
            team = [s for s in self.team(mw, loc, role) if s["state"] in staying]
            contracted = sum(not s["casual"] for s in team)
            want = self.wanted(mw, loc, role, base)
            if contracted < want:
                self.recruit(mw, loc, role, want - contracted, casual=False)
                continue
            self.posted.pop((loc, role, False), None)
            if (loc, role) in self.short:
                self.recruit(mw, loc, role, 1, casual=True)
            else:
                self.posted.pop((loc, role, True), None)

    def recruit(self, mw: ManagedWorld, loc: LocationId, role: Role, n: int, casual: bool) -> None:
        pool = [c for c in candidates(mw) if c["location"] == loc and c["role"] == role]
        for c in pool:
            if c["state"] == StaffState.CANDIDATE.value:
                _try(mw, "interview", candidate=c["candidate"])
        seen = [
            c
            for c in candidates(mw)
            if c["location"] == loc and c["role"] == role and c["state"] == "interviewed"
        ]
        key = (loc, role, casual)
        premium = CASUAL_PREMIUM if casual else 1.0
        wage = self.posted.get(key, round(self.going_wage(mw, role) * premium))
        action = "hire_casual" if casual else "hire"
        for c in sorted(seen, key=lambda c: -self.skill(mw, c)):
            result = _try(mw, action, candidate=c["candidate"], wage_cents_per_hour=wage)
            if result and result.data.get("hired"):
                if not casual:
                    self.took_on(mw, loc, role, result.data["staff"])
                n -= 1
                if n <= 0:
                    return
        if not pool:
            wage = round(wage * HIRE_RETRY_RAISE) if key in self.posted else wage
            wage = min(6000, max(minimum_wage(mw), wage))
            if _try(mw, "post_job", location=loc, role=role.value, wage_cents_per_hour=wage):
                self.posted[key] = wage


MARKET_PREMIUM = 1.05
# both chosen by a sweep on seeds 1-3 (traffic share 0.2 / 0.3 / 0.4; board price 1.00 / 1.08 /
# 1.15 / cost-plus): a longer day and a small premium the oven can still feed pay best
HOUR_TRAFFIC_SHARE = 0.2
PRICE_BAND = (1.08, 1.35)
AWARENESS_TARGET = 0.8
TOP_UP_CENTS = 60_000
NEW_SHOP_RAMP = 0.6
EXPANSION_WEEK = 4
COST_OF_SALES = 0.3
OVERHEAD = 200_000


@dataclass
class Oracle(Heuristic):
    """Reads the hidden truth: habits, elasticities, traffic, true skills and market wages."""

    name: str = "oracle"

    def weekly(self, mw: ManagedWorld, week: int) -> None:
        if week == 0:
            self.headcount = self._headcount(mw)
            self.cheapest_suppliers(mw)
        self.replace_slow_baker(mw)
        if week == EXPANSION_WEEK:
            self.consider_expansion(mw)
        for loc in _trading(mw):
            if week == 0:
                self.true_hours(mw, loc)
                self.best_prices(mw, loc)
            if week % MENU_REFRESH_WEEKS == 0:
                self.refresh_menu(mw, loc)
            if week % FLYER_WEEKS == 0:
                self.advertise(mw, loc)
            self.top_up_awareness(mw, loc)
            self.fit_team(mw, loc)
            self.plan_bakes(mw, loc)
        self.pay_market(mw)
        self.staff_up(mw)

    def slow_baker(self, mw: ManagedWorld) -> Staff | None:
        habit = mw.hidden["habits"].get("slow_loyal_baker")
        s = habit and mw.world.business.staff.get(habit["details"]["staff_id"])
        return s if s and s.state in WORKING else None

    def wanted(self, mw: ManagedWorld, loc: LocationId, role: Role, base: int) -> int:
        slow = self.slow_baker(mw)
        extra = role is Role.BAKER and slow is not None and slow.location_id == loc
        return super().wanted(mw, loc, role, base) + extra

    def replace_slow_baker(self, mw: ManagedWorld) -> None:
        """Hire first, then hand the slow baker's days to the newcomer and let her go."""
        slow = self.slow_baker(mw)
        if slow is None:
            return
        fresh = [
            s
            for s in mw.world.business.staff.values()
            if s.role is Role.BAKER
            and s.location_id == slow.location_id
            and s.state is StaffState.EMPLOYED
            and s.hired_day >= mw.handoff_day
        ]
        if fresh:
            _try(mw, "set_rota", staff=fresh[0].id, shifts=describe(slow.rota))
            _try(mw, "fire", staff=slow.id)

    def pay_market(self, mw: ManagedWorld) -> None:
        for s in staff_list(mw):
            if s["owner"]:
                continue
            fair = round(market_wage(mw.world, Role(s["role"])) * MARKET_PREMIUM)
            if s["wage_cents_per_hour"] < fair:
                _try(mw, "set_wage", staff=s["staff"], wage_cents_per_hour=min(6000, fair))

    def going_wage(self, mw: ManagedWorld, role: Role) -> int:
        return max(minimum_wage(mw), round(market_wage(mw.world, role) * MARKET_PREMIUM))

    def skill(self, mw: ManagedWorld, candidate: dict) -> float:
        return mw.candidates[candidate["candidate"]].staff.skill

    def _traffic(self, mw: ManagedWorld, loc: LocationId) -> list[float]:
        town = mw.world.town
        home = town[mw.world.business.locations[loc].neighborhood_id]
        weights = [0.0] * 24
        for nb in town.values():
            total = sum(nb.hourly_traffic) or 1
            reach = nb.population * proximity(nb, home)
            for h, w in enumerate(nb.hourly_traffic):
                weights[h] += reach * w / total
        return weights

    def true_hours(self, mw: ManagedWorld, loc: LocationId) -> None:
        weights = self._traffic(mw, loc)
        peak = max(weights)
        busy = [h for h, w in enumerate(weights) if w >= HOUR_TRAFFIC_SHARE * peak]
        _try(
            mw,
            "set_hours",
            location=loc,
            open_hour=max(6, busy[0]),
            close_hour=min(20, busy[-1] + 1),
        )

    def price_factor(self, mw: ManagedWorld, loc: LocationId) -> float:
        world = mw.world
        home = world.town[world.business.locations[loc].neighborhood_id]
        weight = elastic = 0.0
        for c in world.customers:
            if c.state is CustomerState.CHURNED:
                continue
            nb = world.town[c.neighborhood_id]
            w = c.base_visit_rate * proximity(nb, home)
            weight += w
            elastic += w * world.params.elasticity[c.segment] / nb.income_index
        low, high = PRICE_BAND
        return min(high, max(low, weight / elastic))

    def best_prices(self, mw: ManagedWorld, loc: LocationId) -> None:
        k = self.price_factor(mw, loc)
        for item, e in mw.world.business.locations[loc].menu.items():
            if e.state in (MenuState.ON_MENU, MenuState.TRIAL):
                typical = CATALOG[item].typical_price_cents
                price = _round25(max(k * typical, 2 * self.unit_cost(mw, item)))
                _try(mw, "set_price", location=loc, item=item, price_cents=price)

    def refresh_menu(self, mw: ManagedWorld, loc: LocationId) -> None:
        """A new item only while regulars are actually tiring of the menu."""
        if mw.world.menu_boredom > 0:
            super().refresh_menu(mw, loc)

    def top_up_awareness(self, mw: ManagedWorld, loc: LocationId) -> None:
        if mw.world.business.locations[loc].awareness < AWARENESS_TARGET:
            _try(mw, "spend_marketing", location=loc, channel="flyers", amount_cents=TOP_UP_CENTS)

    def reach(self, mw: ManagedWorld, nb_id: str) -> float:
        town = mw.world.town
        site = town[nb_id]
        return sum(nb.population * nb.income_index * proximity(nb, site) for nb in town.values())

    def site_profit_per_month(self, mw: ManagedWorld, site: dict) -> float:
        home = mw.world.business.locations[HOME_LOCATION]
        days = list(mw.reports)[-28:]
        home_rev = sum(
            s.revenue_cents for r in days for s in r.locations if s.location_id == home.id
        )
        monthly = home_rev * 30.4 / max(1, len(days))
        ratio = self.reach(mw, site["neighborhood"]) / self.reach(mw, home.neighborhood_id)
        revenue = monthly * ratio * NEW_SHOP_RAMP * min(1.0, site["size_m2"] / home.size_m2 * 1.5)
        crew = {Role.BAKER: 1, Role.COUNTER: 2, Role.CLEANER: 1}
        wages = sum(
            n * market_wage(mw.world, r) * SHIFT_HOURS[r] * 22 * 1.12 for r, n in crew.items()
        )
        return (1 - COST_OF_SALES) * revenue - wages - site["rent_cents_per_month"] - OVERHEAD

    def consider_expansion(self, mw: ManagedWorld) -> None:
        if not mw.reports:
            return
        for nb in sorted(mw.world.town):
            _try(mw, "scout_site", neighborhood=nb)
        best = None
        for site in sites_scouted(mw):
            profit = self.site_profit_per_month(mw, site)
            months = (365 - site["fitout_days"]) / 30.4
            if profit * months >= site["fitout_cents"] and (best is None or profit > best[0]):
                best = (profit, site)
        if best is None:
            return
        result = _try(mw, "sign_lease", site=best[1]["site"])
        if result:
            loc = LocationId(result.data["location"])
            crew = {Role.BAKER: 1, Role.COUNTER: 2, Role.CLEANER: 1}
            self.headcount |= {(loc, role): n for role, n in crew.items()}


POLICIES = {p.name: p for p in (Freeze, Founder, Heuristic, Oracle)}


@dataclass(frozen=True)
class SeedRow:
    seed: int
    policy: str
    bankrupt: bool
    net_worth_change: int
    score: int
    diagnostics: dict
    seconds: float


def run_seed(
    seed: int, weeks: int, history_dir: Path, out_dir: Path | None = None
) -> list[SeedRow]:
    source = history_dir / f"seed-{seed}"
    if not (source / "handoff.pickle").exists():
        generate_history(seed, source)
    results = {}
    for name, policy in POLICIES.items():
        out = out_dir / f"seed-{seed}" / name if out_dir else None
        results[name] = manage(source, policy(), weeks, out)
    founder = results["founder"].net_worth_change
    return [
        SeedRow(
            seed,
            name,
            r.bankrupt,
            r.net_worth_change,
            r.net_worth_change - founder,
            r.diagnostics,
            r.seconds,
        )
        for name, r in results.items()
    ]


def table(rows: list[SeedRow]) -> str:
    head = (
        f"{'seed':>4} {'policy':<9} {'NW change $':>12} {'score $':>10} {'waste%':>6} "
        f"{'stockout':>8} {'walkaway':>8} {'turnover':>8} {'stars':>5} {'habits':>6} {'events':>6}"
    )
    lines = [head]
    for r in rows:
        d = r.diagnostics
        lines.append(
            f"{r.seed:>4} {r.policy + ('*' if r.bankrupt else ''):<9} "
            f"{r.net_worth_change / 100:>12,.0f} {r.score / 100:>10,.0f} {d['waste_pct']:>6} "
            f"{d['stockout_rate']:>8} {d['walkaways']:>8} {d['staff_turnover']:>8} "
            f"{d['review_stars'] or '-':>5} {d['habits_fixed']:>6} {d['curveballs_handled']:>6}"
        )
    return "\n".join(lines)
