import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from bakery_world import machines as mc
from bakery_world.basics import (
    Account,
    Category,
    Cents,
    CustomerId,
    Ingredient,
    ItemId,
    LocationId,
    Payment,
    Role,
    Segment,
    StaffId,
)
from bakery_world.catalog import CATALOG
from bakery_world.machines import (
    CustomerState,
    EquipmentState,
    LeaseState,
    LocationState,
    MenuState,
    StaffState,
    Transition,
    step,
)
from bakery_world.model import (
    MARKET_WAGE_CENTS,
    Business,
    Customer,
    LedgerEntry,
    Location,
    ShiftTime,
    Staff,
    Vacancy,
    World,
    rota_hours,
)
from bakery_world.rota import PRE_OPEN_BAKE_HOURS, assign_rota, on_leave, standard_shift, worked
from bakery_world.town import HIRE_NAMES, proximity, spawn_customer

MONTHLY_OVERHEAD_CENTS: dict[Account, int] = {
    Account.UTILITIES: 60_000,
    Account.INSURANCE: 35_000,
    Account.SOFTWARE_AND_TERMINAL: 18_000,
    Account.REPAIRS_RESERVE: 25_000,
}
DAILY_OVERHEAD_CENTS = {a: round(m * 12 / 365) for a, m in MONTHLY_OVERHEAD_CENTS.items()}
PAYROLL_ONCOST_RATE = 0.12
PACKAGING_CENTS: dict[Category, int] = {
    Category.BREAD: 25,
    Category.PASTRY: 18,
    Category.CAKE: 35,
    Category.DRINK: 30,
    Category.SAVORY: 18,
}
LOAN_APR = 0.07
CREDIT_APR = 0.11
CARD_SHARE: dict[Segment, float] = {
    Segment.COMMUTER: 0.88,
    Segment.OFFICE: 0.92,
    Segment.STUDENT: 0.9,
    Segment.FAMILY: 0.75,
    Segment.RETIREE: 0.55,
}

BETA = 1.6
CARD_FEE = 0.02
DAY_OLD_DISCOUNT = 0.5
DAY_OLD_QUALITY = 0.75
COUNTER_CUSTOMERS_PER_HOUR = 50.0
BAKER_SERVE_SPEED = 0.3
CLEANER_SERVE_SPEED = 0.5
HELP_UNTIL_HOUR = 12
BAKE_DEADLINE_HOUR = 13
VACANCY_DAYS = 10
NOTICE_DAYS = 14
OVEN_REPAIR_CENTS = 40_000
OVEN_REPAIR_DAYS = 2
OVEN_WEAR_PER_FULL_DAY = 0.0035
WORN_OVEN_CAPACITY = 0.85
STOCKOUT_SUBSTITUTE_PROB = 0.5
# a customer let down (walked out of the queue, or what they came for was sold out) comes
# less often, more so each time, and slowly forgives the shop over good visits
LETDOWN_VISIT_FACTOR = 0.75
LETDOWN_SUBSTITUTED = 0.5
LETDOWN_RECOVERY = 0.7
LAPSE_AFTER_DAYS = 30
CHURN_AFTER_LAPSED_DAYS = 90
NEW_QUIET_AFTER_DAYS = 60
REGULAR_VISITS = 4
STATE_VISIT_FACTOR = {CustomerState.NEW: 1.0, CustomerState.REGULAR: 1.0, CustomerState.LAPSED: 0.1}
BAKERY_GOER_SHARE = 0.053
AWARENESS_FROM_WORD_OF_MOUTH = 0.015
AWARENESS_FROM_MARKETING = 0.009
AWARENESS_HALF_VISITS = 60
AWARENESS_FORGET = 0.0015
MARKETING_SCALE_CENTS = 100_000
NEUTRAL_MORALE = 0.6
HOURS_CUT_UNNOTICED = 0.2
DIRT_PER_VISIT = 0.0005
CLEAN_PER_HOUR = 0.05
SLACK_CLEANING = 0.1
DIRTY_SATISFACTION = 0.35
DIRTY_UTILITY = 0.3
BAKER_HANDS_ON = 0.8
STRAIN_FREE_LOAD = 0.85
MAX_BAKE_LOAD = 2.0
STRAIN_QUALITY = 0.4
MIN_RUSHED_QUALITY = 0.6
STRAIN_SATISFACTION = 0.2
HOURS_CUT_MORALE = 0.5
OVERTIME_MORALE = 0.5
# sickness is unpaid (statutory sick pay starts after waiting days, longer than most absences)
SICK_RATE = 0.022
QUIT_FLOOR = 0.5
QUIT_MORALE = 3.0
SERVICE_WEIGHT = 0.4
BOREDOM_PENALTY = 0.15
MORALE_EFFORT_FLOOR = 0.64
FATIGUE_RETENTION = 0.93
BOREDOM_RETENTION = 0.05
FATIGUE_CAP = 0.8
# regulars remember a shop's price board: a dearer shop than usual gets fewer visits
PRICE_VISIT_SENSITIVITY = 1.0
PRICE_MEMORY = 1 / 10
PRICE_UNNOTICED = 0.02
# paying more than the going rate sours a visit a little
PRICE_SATISFACTION = 0.6
SHOP_LOYALTY = 3
BUSY_WALKAWAY_SHARE = 0.08
BUSY_STARS = 0.8
DIRTY_STARS = 2.5
UNEVEN_STARS = 1.5
# the shop's recent review stars (about the last 90 days) bring or keep away newcomers
STAR_MEMORY = 1 / 90
NEUTRAL_STARS = 3.5
STARS_PULL = 0.1
# every drink is hot: more wanted on a cold day, less on a hot one
DRINK_WARMTH_PER_DEGREE = 0.012
MILD_TEMP_C = 14.0
MAX_DRINK_WARMTH = 0.3

SEGMENT_WEEKDAY: dict[Segment, tuple[float, ...]] = {
    Segment.COMMUTER: (1.25, 1.25, 1.25, 1.25, 1.2, 0.5, 0.3),
    Segment.OFFICE: (1.3, 1.3, 1.3, 1.3, 1.2, 0.4, 0.2),
    Segment.STUDENT: (1.1, 1.1, 1.1, 1.1, 1.0, 0.9, 0.7),
    Segment.FAMILY: (0.7, 0.7, 0.7, 0.8, 0.9, 1.9, 1.3),
    Segment.RETIREE: (1.1, 1.1, 1.1, 1.1, 1.0, 0.8, 0.8),
}
BASKET_SIZE_WEIGHTS: dict[Segment, tuple[float, float, float]] = {
    Segment.COMMUTER: (0.40, 0.40, 0.20),
    Segment.OFFICE: (0.30, 0.40, 0.30),
    Segment.STUDENT: (0.50, 0.35, 0.15),
    Segment.FAMILY: (0.25, 0.35, 0.40),
    Segment.RETIREE: (0.40, 0.40, 0.20),
}


@dataclass(frozen=True)
class SaleLine:
    item_id: ItemId
    price_cents: Cents
    day_old: bool


@dataclass(frozen=True)
class Sale:
    hour: int
    minute: int
    customer_id: CustomerId
    loyalty_id: str | None
    payment: Payment
    lines: tuple[SaleLine, ...]


@dataclass(frozen=True)
class Shift:
    staff_id: StaffId
    start_hour: int
    end_hour: int
    pay_cents: Cents
    leave: bool = False

    @property
    def hours(self) -> int:
        return self.end_hour - self.start_hour


@dataclass
class ItemDay:
    baked: int = 0
    sold: int = 0
    day_old_sold: int = 0
    wasted: int = 0
    unmet: int = 0
    first_stockout_hour: int | None = None


@dataclass(frozen=True)
class LocationDay:
    location_id: LocationId
    open_hour: int
    close_hour: int
    visits: int
    arrivals_after_close: int
    new_customers: int
    sales: tuple[Sale, ...]
    items: dict[ItemId, ItemDay]
    walkaways: int
    walkaways_by_hour: tuple[int, ...]
    bake_strain: float
    wait_minutes_by_hour: tuple[float, ...]
    shifts: tuple[Shift, ...]
    ingredient_grams: dict[Ingredient, int]
    revenue_cents: Cents
    ingredient_cost_cents: Cents
    packaging_cents: Cents
    wages_cents: Cents
    payroll_oncost_cents: Cents
    rent_cents: Cents
    card_fees_cents: Cents
    repairs_cents: Cents
    overhead_cents: Cents

    @property
    def costs_cents(self) -> int:
        return (
            self.ingredient_cost_cents
            + self.packaging_cents
            + self.wages_cents
            + self.payroll_oncost_cents
            + self.rent_cents
            + self.card_fees_cents
            + self.repairs_cents
            + self.overhead_cents
        )


@dataclass(frozen=True)
class DayReport:
    day: int
    weekday: int
    temp_c: float
    rain: bool
    locations: tuple[LocationDay, ...]
    interest_cents: Cents
    prices_paid: dict[Ingredient, int]
    cash_cents: Cents
    transitions: tuple[Transition, ...]


Policy = Callable[[World, int], None]


@dataclass
class _Offer:
    item_id: ItemId
    category: Category
    price: int
    typical: int
    day_old: bool
    quality: float


@dataclass(frozen=True)
class _OnShift:
    staff: Staff
    shift: ShiftTime

    def covers(self, hour: int) -> bool:
        return self.shift.start_hour <= hour < self.shift.end_hour


@dataclass
class _Roster:
    bakers: list[_OnShift] = field(default_factory=list)
    counters: list[_OnShift] = field(default_factory=list)
    cleaners: list[_OnShift] = field(default_factory=list)
    leave: list[_OnShift] = field(default_factory=list)

    @property
    def everyone(self) -> list[_OnShift]:
        return self.bakers + self.counters + self.cleaners


def _poisson(rng: random.Random, lam: float) -> int:
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def stream(world: World, *name: object) -> random.Random:
    """A per-day random stream of its own, so staff and equipment events do not shift with
    how many customers came (and paired runs keep them in step)."""
    return random.Random(":".join(str(x) for x in (world.seed, *name, world.day)))


def _roster(world: World, loc: Location) -> _Roster:
    working = (StaffState.EMPLOYED, StaffState.ON_NOTICE)
    by_role: dict[Role, list[_OnShift]] = {r: [] for r in Role}
    here = [s for s in world.business.staff.values() if s.location_id == loc.id]
    leave = []
    for s in sorted(here, key=lambda s: s.id):
        shift = worked(s, loc, world.day % 7)
        if shift and s.state in working:
            if on_leave(s, world.day):
                leave.append(_OnShift(s, shift))
            else:
                by_role[s.role].append(_OnShift(s, shift))
    roster = _Roster(by_role[Role.BAKER], by_role[Role.COUNTER], by_role[Role.CLEANER], leave)
    owner = next((s for s in here if s.owner and s.state in working), None)
    if owner and not roster.bakers:
        roster.counters = [on for on in roster.counters if on.staff is not owner]
        roster.bakers.append(_OnShift(owner, standard_shift(Role.BAKER, loc)))
    return roster


def _effort(s: Staff) -> float:
    mood = MORALE_EFFORT_FLOOR + (1 - MORALE_EFFORT_FLOOR) * s.morale / NEUTRAL_MORALE
    return s.speed * (0.7 + 0.3 * s.skill) * mood


def market_wage(world: World, role: Role) -> Cents:
    return Cents(round(MARKET_WAGE_CENTS[role] * world.market_wage_index))


def _counter_staff(roster: _Roster, h: int) -> float:
    staff = sum(_effort(on.staff) for on in roster.counters if on.covers(h))
    if staff:
        return staff
    # with nobody on the counter, a baker (before noon) or the cleaner serves between tasks
    return sum(
        BAKER_SERVE_SPEED * _effort(on.staff)
        for on in roster.bakers
        if on.covers(h) and h < HELP_UNTIL_HOUR
    ) + sum(CLEANER_SERVE_SPEED * _effort(on.staff) for on in roster.cleaners if on.covers(h))


def _counter_capacity(loc: Location, roster: _Roster) -> list[float]:
    return [
        COUNTER_CUSTOMERS_PER_HOUR * _counter_staff(roster, h)
        if loc.open_hour <= h < loc.close_hour
        else 0.0
        for h in range(24)
    ]


def _wait_minutes(arrivals: int, capacity: float) -> float:
    if capacity <= 0:
        return math.inf
    rho = arrivals / capacity
    unit = 30 / capacity
    if rho < 0.95:
        return unit * rho / (1 - rho)
    return unit * 19 + (rho - 0.95) * 60


def _walkaway_probability(wait: float, threshold: float) -> float:
    if wait == math.inf:
        return 1.0
    return min(0.95, max(0.0, (wait - 0.5 * threshold) / (2 * threshold)))


def _shifts(roster: _Roster) -> tuple[Shift, ...]:
    return tuple(
        Shift(
            on.staff.id,
            on.shift.start_hour,
            on.shift.end_hour,
            Cents(on.staff.wage_cents_per_hour * on.shift.hours),
            on in roster.leave,
        )
        for on in roster.everyone + roster.leave
    )


def _use(grams: dict[Ingredient, int], item: ItemId, units: int) -> None:
    for ing, g in CATALOG[item].grams.items():
        grams[ing] = grams.get(ing, 0) + g * units


def ingredient_cost(grams: dict[Ingredient, int], prices: dict[Ingredient, int]) -> int:
    return sum(round(g * prices[ing] / 1000) for ing, g in grams.items())


def post(
    business: Business,
    day: int,
    account: Account,
    amount_cents: int,
    location_id: LocationId | None = None,
    memo: str = "",
) -> None:
    if amount_cents == 0:
        return
    business.cash_cents = Cents(business.cash_cents + amount_cents)
    business.ledger.append(LedgerEntry(day, account, amount_cents, location_id, memo))


def prices_paid(world: World, day: int) -> dict[Ingredient, int]:
    business = world.business
    markup = {
        ing: business.suppliers[sid].markup.get(ing, 1.0) for ing, sid in business.buy_from.items()
    }
    return {
        ing: round(p * markup.get(ing, 1.0)) for ing, p in world.ingredient_prices.on(day).items()
    }


def _age_stock(loc: Location, items: dict[ItemId, ItemDay]) -> dict[ItemId, dict[int, int]]:
    old: dict[ItemId, dict[int, int]] = {}
    for item, lots in loc.stock.items():
        shelf = CATALOG[item].shelf_life_days
        for age, qty in lots.items():
            if age + 1 >= shelf:
                items.setdefault(item, ItemDay()).wasted += qty
            else:
                old.setdefault(item, {})[age + 1] = qty
    return old


def _bake(
    loc: Location,
    roster: _Roster,
    weekday: int,
    grams: dict[Ingredient, int],
    items: dict[ItemId, ItemDay],
) -> tuple[dict[ItemId, int], float, float]:
    window_hours = max(0, BAKE_DEADLINE_HOUR - (loc.open_hour - PRE_OPEN_BAKE_HOURS))
    capacity_by_oven = {
        o.id: 60.0 * window_hours * (WORN_OVEN_CAPACITY if o.state is EquipmentState.WORN else 1.0)
        for o in loc.ovens
        if o.state in (EquipmentState.WORKING, EquipmentState.WORN)
    }
    oven_total = sum(capacity_by_oven.values())
    oven_left = oven_total
    labor_left = sum(
        on.shift.hours * 60 * BAKER_HANDS_ON * _effort(on.staff) for on in roster.bakers
    )
    plans = {
        item: entry.bake_plan[weekday]
        for item, entry in sorted(
            loc.menu.items(), key=lambda kv: kv[1].state is not MenuState.ON_MENU
        )
        if entry.state in (MenuState.ON_MENU, MenuState.TRIAL)
        and not CATALOG[item].made_to_order
        and entry.bake_plan[weekday] > 0
    }
    need = sum(units * CATALOG[item].labor_minutes for item, units in plans.items())
    load = need / labor_left if labor_left else 0.0
    oven_need = sum(
        units / CATALOG[item].batch_size * CATALOG[item].oven_minutes
        for item, units in plans.items()
    )
    # an oven shortfall is shared across items like a labor one; each item keeps room for
    # its last part-full batch, so no item is starved by the ones baked before it
    oven_room = oven_total - sum(CATALOG[item].oven_minutes for item in plans)
    cut = max(load, oven_need / oven_room if oven_room > 0 else math.inf)
    fresh: dict[ItemId, int] = {}
    for item, planned in plans.items():
        recipe = CATALOG[item]
        plan = math.floor(planned / cut) if cut > 1 else planned
        by_oven = int(oven_left // recipe.oven_minutes) * recipe.batch_size
        by_labor = int(labor_left // recipe.labor_minutes)
        units = max(0, min(plan, by_oven, by_labor))
        oven_left -= math.ceil(units / recipe.batch_size) * recipe.oven_minutes
        labor_left -= units * recipe.labor_minutes
        fresh[item] = units
        stat = items.setdefault(item, ItemDay())
        stat.baked = units
        if units == 0:
            stat.first_stockout_hour = loc.open_hour
        _use(grams, item, units)
    utilisation = 0.0 if oven_total == 0 else 1 - oven_left / oven_total
    baked = any(fresh.values())
    strain = max(0.0, min(load, MAX_BAKE_LOAD) - STRAIN_FREE_LOAD) if baked else 0.0
    return fresh, utilisation, strain


def _build_offers(
    loc: Location,
    day: int,
    fresh: dict[ItemId, int],
    old: dict[ItemId, dict[int, int]],
    strain: float,
) -> list[_Offer]:
    rushed = max(MIN_RUSHED_QUALITY, 1 - STRAIN_QUALITY * strain)
    offers = []
    for item, entry in loc.menu.items():
        recipe = CATALOG[item]
        if entry.state not in (MenuState.ON_MENU, MenuState.TRIAL):
            continue
        typical, price = recipe.typical_price_cents, entry.price_on(day)
        if recipe.made_to_order:
            offers.append(_Offer(item, recipe.category, price, typical, False, 1.0))
        elif item in fresh:
            offers.append(_Offer(item, recipe.category, price, typical, False, rushed))
        if old.get(item):
            price = round(price * DAY_OLD_DISCOUNT)
            offers.append(_Offer(item, recipe.category, price, typical, True, DAY_OLD_QUALITY))
    return offers


def _pick(rng: random.Random, weights: list[float], w_out: float) -> int | None:
    r = rng.random() * (w_out + sum(weights))
    if r < w_out:
        return None
    r -= w_out
    for i, w in enumerate(weights):
        r -= w
        if r < 0:
            return i
    return len(weights) - 1


def _visit_factors(world: World, day: int, temp: float, rain: bool) -> dict:
    factors = {}
    weekday = day % 7
    for nb in world.town.values():
        pull = {seg: 0.0 for seg in Segment}
        for comp in world.competitors.values():
            if comp.state is mc.CompetitorState.OPEN:
                reach = proximity(world.town[comp.neighborhood_id], nb) / comp.price_index
                for seg in Segment:
                    pull[seg] += comp.appeal[seg] * reach
        traffic = world.foot_traffic.multiplier(nb.id, day)
        for seg in Segment:
            weather = (0.97 if seg is Segment.COMMUTER else 0.94) if rain else 1.0
            if temp < -1 or temp > 30:
                weather *= 0.97
            shift = world.segment_shift.get((nb.id, seg), 1.0)
            factors[(nb.id, seg)] = (
                traffic * shift * SEGMENT_WEEKDAY[seg][weekday] * weather / (1 + 0.45 * pull[seg])
            )
    return factors


def star_drag(loc: Location, walkaways: int, visits: int, strain: float) -> float:
    busy = walkaways > BUSY_WALKAWAY_SHARE * max(1, visits)
    return (BUSY_STARS if busy else 0) + DIRTY_STARS * (1 - loc.cleanliness) + UNEVEN_STARS * strain


def _star_pull(loc: Location) -> float:
    return max(0.5, 1 + STARS_PULL * (loc.stars - NEUTRAL_STARS))


def _visitors(
    world: World, loc: Location, rng: random.Random, weather: tuple
) -> tuple[list[tuple[int, Customer]], int]:
    day, nbs = world.day, world.town
    home = nbs[loc.neighborhood_id]
    factors = _visit_factors(world, day, weather[0], weather[1])
    awareness_f = 0.4 + 1.2 * loc.awareness
    reputation_f = 0.7 + 0.6 * loc.reputation
    cum = {nb.id: _cumulative(nb.hourly_traffic) for nb in nbs.values()}
    shops = [
        nbs[other.neighborhood_id]
        for other in world.business.locations.values()
        if other.state is LocationState.TRADING
    ]
    # customers mostly use their nearest shop, so a second shop takes few of the first's
    prox = {
        nb.id: proximity(nb, home)
        * proximity(nb, home) ** SHOP_LOYALTY
        / sum(proximity(nb, shop) ** SHOP_LOYALTY for shop in shops)
        for nb in nbs.values()
    }
    scale = awareness_f * reputation_f / 7
    gap = loc.price_index - 1
    noticed = math.copysign(max(0.0, abs(gap) - PRICE_UNNOTICED), gap)
    price_f = {
        (nb.id, seg): math.exp(-PRICE_VISIT_SENSITIVITY * e / nb.income_index * noticed)
        for nb in nbs.values()
        for seg, e in world.params.elasticity.items()
    }
    visitors: list[tuple[int, Customer]] = []
    hours = range(24)
    known = 0.0
    for c in world.customers:
        if c.state is CustomerState.CHURNED:
            continue
        reach = prox[c.neighborhood_id]
        known += reach
        p = (
            c.base_visit_rate
            * scale
            * factors[(c.neighborhood_id, c.segment)]
            * price_f[(c.neighborhood_id, c.segment)]
            * reach
            * STATE_VISIT_FACTOR[c.state]
            * (0.5 + 0.7 * c.satisfaction)
            * LETDOWN_VISIT_FACTOR**c.letdowns
        )
        if rng.random() < p:
            visitors.append((rng.choices(hours, cum_weights=cum[c.neighborhood_id])[0], c))
    market = sum(nb.population * prox[nb.id] for nb in nbs.values()) * BAKERY_GOER_SHARE
    untried = max(0.0, loc.awareness * market - known)
    new_count = _poisson(rng, world.params.new_customer_rate * untried * _star_pull(loc))
    nb_list = list(nbs.values())
    nb_weights = [nb.population * prox[nb.id] for nb in nb_list]
    for _ in range(new_count):
        nb = rng.choices(nb_list, weights=nb_weights)[0]
        c = spawn_customer(rng, len(world.customers), nb, day)
        world.customers.append(c)
        visitors.append((rng.choices(hours, cum_weights=cum[nb.id])[0], c))
    return visitors, new_count


def _run_location(
    world: World, loc: Location, rng: random.Random, weather: tuple, prices: dict
) -> LocationDay:
    day, weekday = world.day, world.day % 7
    params = world.params
    log = world.business.transitions
    items: dict[ItemId, ItemDay] = {}
    grams: dict[Ingredient, int] = {}
    roster = _roster(world, loc)
    old = _age_stock(loc, items)
    fresh, oven_use, strain = _bake(loc, roster, weekday, grams, items)
    offers = _build_offers(loc, day, fresh, old, strain)
    qty: list[int | None] = []
    for o in offers:
        if CATALOG[o.item_id].made_to_order:
            qty.append(None)
        elif o.day_old:
            qty.append(sum(old[o.item_id].values()))
        else:
            qty.append(fresh[o.item_id])
    offers_by_item: dict[ItemId, list[int]] = {}
    for i, o in enumerate(offers):
        offers_by_item.setdefault(o.item_id, []).append(i)

    nbs = world.town
    hours = range(24)
    visitors, new_count = _visitors(world, loc, rng, weather)
    open_hours = range(loc.open_hour, loc.close_hour)
    inside = sorted(
        ((h, rng.randrange(60), c) for h, c in visitors if h in open_hours), key=lambda v: v[:2]
    )
    after_close = len(visitors) - len(inside)
    arrivals = [0] * 24
    for h, _, _ in inside:
        arrivals[h] += 1
    capacity = _counter_capacity(loc, roster)
    waits = [_wait_minutes(arrivals[h], capacity[h]) for h in hours]

    weight_cache: dict[tuple, tuple[list[float], list[float]]] = {}
    sales: list[Sale] = []
    walkaways = [0] * 24
    revenue = 0
    card_revenue = 0
    packaging = 0
    satisfactions = []
    grime = 1 - loc.cleanliness
    stale = BOREDOM_PENALTY * _boredom(world, loc)
    moods = [on.staff.morale for on in roster.counters]
    service = SERVICE_WEIGHT * (
        (sum(moods) / len(moods) if moods else NEUTRAL_MORALE) - NEUTRAL_MORALE
    )
    w_out = math.exp(BETA * (params.outside_option + DIRTY_UTILITY * grime))
    chill = DRINK_WARMTH_PER_DEGREE * (MILD_TEMP_C - weather[0])
    warmth = max(-MAX_DRINK_WARMTH, min(MAX_DRINK_WARMTH, chill))
    old_lots = old

    for hour, minute, c in inside:
        wait = waits[hour]
        if rng.random() < _walkaway_probability(wait, params.walkaway_wait_min):
            walkaways[hour] += 1
            c.letdowns += 1
            _update_satisfaction(c, 0.25)
            satisfactions.append(c.satisfaction)
            continue
        nb = nbs[c.neighborhood_id]
        key = (c.segment, nb.id)
        if key not in weight_cache:
            taste = [
                params.taste[c.segment][o.category] * params.item_appeal[o.item_id] * o.quality
                + (warmth if o.category is Category.DRINK else 0.0)
                for o in offers
            ]
            elas = params.elasticity[c.segment] / nb.income_index
            weight_cache[key] = (
                [
                    math.exp(BETA * (t - elas * o.price / o.typical))
                    for t, o in zip(taste, offers, strict=True)
                ],
                taste,
            )
        base_w, tastes = weight_cache[key]
        weights = base_w
        if c.fatigue:
            weights = list(base_w)
            for item, f in c.fatigue.items():
                for i in offers_by_item.get(item, ()):
                    weights[i] = base_w[i] * math.exp(-BETA * tastes[i] * f)
        n_items = rng.choices((1, 2, 3), weights=BASKET_SIZE_WEIGHTS[c.segment])[0]
        lines: list[SaleLine] = []
        misses = 0
        let_down = False
        for _ in range(n_items):
            i = _pick(rng, weights, w_out)
            if i is None:
                break
            if qty[i] == 0:
                items.setdefault(offers[i].item_id, ItemDay()).unmet += 1
                misses += 1
                let_down = let_down or not lines
                if rng.random() >= STOCKOUT_SUBSTITUTE_PROB:
                    break
                avail = [0.0 if q == 0 else w for w, q in zip(weights, qty, strict=True)]
                i = _pick(rng, avail, w_out)
                if i is None:
                    break
            o = offers[i]
            stat = items.setdefault(o.item_id, ItemDay())
            if qty[i] is not None:
                qty[i] -= 1
                if qty[i] == 0 and not o.day_old and stat.first_stockout_hour is None:
                    stat.first_stockout_hour = hour
            if o.day_old:
                lots = old_lots[o.item_id]
                lots[max(a for a, q in lots.items() if q > 0)] -= 1
                stat.day_old_sold += 1
            else:
                stat.sold += 1
            if CATALOG[o.item_id].made_to_order:
                _use(grams, o.item_id, 1)
            lines.append(SaleLine(o.item_id, Cents(o.price), o.day_old))
            revenue += o.price
            packaging += PACKAGING_CENTS[o.category]
            c.fatigue[o.item_id] = min(FATIGUE_CAP, c.fatigue.get(o.item_id, 0.0) + 0.12)
            weights = _refatigue(
                weights, base_w, tastes, offers_by_item[o.item_id], c.fatigue[o.item_id]
            )
        target = 0.85 + service - stale - min(0.3, 0.04 * min(wait, 20)) - 0.15 * misses
        target -= DIRTY_SATISFACTION * grime + STRAIN_SATISFACTION * strain
        if not lines:
            target = min(target, 0.3)
        target -= 0.05 * sum(line.day_old for line in lines)
        if lines:
            dear = sum(
                line.price_cents / CATALOG[line.item_id].typical_price_cents for line in lines
            )
            target -= PRICE_SATISFACTION * max(0.0, dear / len(lines) - 1)
        _update_satisfaction(c, target)
        if let_down:
            c.letdowns += LETDOWN_SUBSTITUTED if lines else 1
        elif lines:
            c.letdowns *= LETDOWN_RECOVERY
        satisfactions.append(c.satisfaction)
        c.last_visit_day = day
        c.recent_visits = [d for d in c.recent_visits if day - d < 30] + [day]
        if c.state is CustomerState.LAPSED:
            c.state = step(log, day, mc.CUSTOMER, c.id, c.state, mc.CustomerEvent.RETURN)
            c.state_since_day = day
        elif c.state is CustomerState.NEW and len(c.recent_visits) >= REGULAR_VISITS:
            c.state = step(log, day, mc.CUSTOMER, c.id, c.state, mc.CustomerEvent.VISIT_REPEATEDLY)
            c.state_since_day = day
        if lines:
            card = rng.random() < CARD_SHARE[c.segment]
            payment = Payment.CARD if card else Payment.CASH
            if card:
                card_revenue += sum(line.price_cents for line in lines)
            served = min(24 * 60 - 1, hour * 60 + minute + round(min(wait, 60.0)))
            sales.append(Sale(served // 60, served % 60, c.id, c.loyalty_id, payment, tuple(lines)))

    # end-of-day stock
    stock: dict[ItemId, dict[int, int]] = {}
    for item, lots in old_lots.items():
        kept = {a: q for a, q in lots.items() if q > 0}
        if kept:
            stock[item] = kept
    for item, units in fresh.items():
        left = units - items[item].sold
        if left > 0:
            stock.setdefault(item, {})[0] = left
    loc.stock = stock

    repairs = _tend_ovens(world, loc, oven_use, stream(world, "ovens", loc.id))
    shifts = _shifts(roster)
    wages = sum(s.pay_cents for s in shifts)
    oncost = round(wages * PAYROLL_ONCOST_RATE)
    rent = round(loc.lease.monthly_rent_cents * 12 / 365)
    fees = round(card_revenue * CARD_FEE)
    ingredients = ingredient_cost(grams, prices)
    charges = {
        Account.SALES: revenue,
        Account.INGREDIENTS: -ingredients,
        Account.PACKAGING: -packaging,
        Account.WAGES: -wages,
        Account.PAYROLL_ONCOSTS: -oncost,
        Account.RENT: -rent,
        Account.CARD_FEES: -fees,
        Account.REPAIRS: -repairs,
    } | {account: -amount for account, amount in DAILY_OVERHEAD_CENTS.items()}
    for account, amount in charges.items():
        post(world.business, day, account, amount, loc.id)

    fresh_offers = [o for o in offers if not o.day_old]
    if fresh_offers:
        board = sum(o.price / o.typical for o in fresh_offers) / len(fresh_offers)
        loc.price_index += PRICE_MEMORY * (board - loc.price_index)
    n = len(satisfactions)
    if n:
        mean_sat = sum(satisfactions) / n
        loc.reputation += params.reputation_drift * min(1.0, n / 60) * (mean_sat - loc.reputation)
    _update_awareness(world, loc, len(inside))
    _tidy(loc, roster, arrivals, capacity, len(inside))
    drag = star_drag(loc, sum(walkaways), len(inside), strain)
    loc.stars += STAR_MEMORY * (min(5.0, max(1.0, 1 + 4 * loc.reputation - drag)) - loc.stars)
    _staff_day(world, loc, roster, arrivals, capacity, stream(world, "staff", loc.id))

    return LocationDay(
        location_id=loc.id,
        open_hour=loc.open_hour,
        close_hour=loc.close_hour,
        visits=len(inside),
        arrivals_after_close=after_close,
        new_customers=new_count,
        sales=tuple(sales),
        items=items,
        walkaways=sum(walkaways),
        walkaways_by_hour=tuple(walkaways),
        bake_strain=round(strain, 3),
        wait_minutes_by_hour=tuple(round(min(w, 60.0), 1) for w in waits),
        shifts=shifts,
        ingredient_grams=grams,
        revenue_cents=Cents(revenue),
        ingredient_cost_cents=Cents(ingredients),
        packaging_cents=Cents(packaging),
        wages_cents=Cents(wages),
        payroll_oncost_cents=Cents(oncost),
        rent_cents=Cents(rent),
        card_fees_cents=Cents(fees),
        repairs_cents=Cents(repairs),
        overhead_cents=Cents(sum(DAILY_OVERHEAD_CENTS.values())),
    )


def _tidy(
    loc: Location, roster: _Roster, arrivals: list[int], capacity: list[float], visits: int
) -> None:
    cleaning = sum(on.shift.hours * _effort(on.staff) for on in roster.cleaners)
    slack = sum(max(0.0, c - a) for c, a in zip(capacity, arrivals, strict=True))
    cleaning += SLACK_CLEANING * slack / COUNTER_CUSTOMERS_PER_HOUR
    dirt = DIRT_PER_VISIT * visits * loc.cleanliness
    loc.cleanliness = min(1.0, max(0.0, loc.cleanliness - dirt + CLEAN_PER_HOUR * cleaning))


def _update_awareness(world: World, loc: Location, visits: int) -> None:
    buzz = (
        visits / (visits + AWARENESS_HALF_VISITS) * (0.6 + 0.4 * loc.reputation) * _star_pull(loc)
    )
    spend = loc.marketing_cents_per_month + loc.campaign_cents_per_month
    advertising = 1 - math.exp(-spend / MARKETING_SCALE_CENTS)
    reach = (
        AWARENESS_FROM_WORD_OF_MOUTH * world.word_of_mouth * buzz
        + AWARENESS_FROM_MARKETING * advertising
    )
    loc.awareness += (1 - loc.awareness) * reach - AWARENESS_FORGET * loc.awareness


def _cumulative(weights: tuple[float, ...]) -> list[float]:
    total, out = 0.0, []
    for w in weights:
        total += w
        out.append(total)
    return out


def _refatigue(
    weights: list[float], base: list[float], tastes: list[float], idx: list[int], fatigue: float
) -> list[float]:
    weights = list(weights)
    for i in idx:
        weights[i] = base[i] * math.exp(-BETA * tastes[i] * fatigue)
    return weights


def _update_satisfaction(c: Customer, target: float) -> None:
    c.satisfaction += 0.35 * (max(0.0, min(1.0, target)) - c.satisfaction)


def _tend_ovens(world: World, loc: Location, utilisation: float, rng: random.Random) -> int:
    day, log, repairs = world.day, world.business.transitions, 0
    ev = mc.EquipmentEvent
    for oven in loc.ovens:
        state = oven.state
        if state is EquipmentState.WORKING or state is EquipmentState.WORN:
            oven.wear = min(1.0, oven.wear + OVEN_WEAR_PER_FULL_DAY * utilisation)
        if state is EquipmentState.WORKING:
            if oven.wear > 0.5:
                oven.state = step(log, day, mc.EQUIPMENT, oven.id, state, ev.WEAR_OUT)
            elif rng.random() < 0.0003:
                oven.state = step(log, day, mc.EQUIPMENT, oven.id, state, ev.BREAK_DOWN)
        elif state is EquipmentState.WORN:
            if rng.random() < 0.002 + 0.01 * oven.wear:
                oven.state = step(log, day, mc.EQUIPMENT, oven.id, state, ev.BREAK_DOWN)
        elif state is EquipmentState.BROKEN:
            oven.state = step(log, day, mc.EQUIPMENT, oven.id, state, ev.START_REPAIR)
            oven.repair_done_day = day + OVEN_REPAIR_DAYS
            repairs += OVEN_REPAIR_CENTS
        elif state is EquipmentState.REPAIRING and day >= oven.repair_done_day:
            oven.state = step(log, day, mc.EQUIPMENT, oven.id, state, ev.FINISH_REPAIR)
            oven.wear = 0.15
    return repairs


def _staff_day(
    world: World,
    loc: Location,
    roster: _Roster,
    arrivals: list[int],
    capacity: list[float],
    rng: random.Random,
) -> None:
    day, log, params = world.day, world.business.transitions, world.params
    ev = mc.StaffEvent
    busy = [arrivals[h] / capacity[h] for h in range(24) if capacity[h] > 0]
    overload = max(0.0, (sum(busy) / len(busy) if busy else 0.0) - 0.85)
    for s in sorted(world.business.staff.values(), key=lambda s: s.id):
        if s.owner or s.location_id != loc.id:
            continue
        if s.state in (StaffState.CANDIDATE, StaffState.GONE):
            continue
        wage_gap = s.wage_cents_per_hour / market_wage(world, s.role) - 1
        cut = 1 - rota_hours(s.rota) / s.contract_hours if s.contract_hours else 0.0
        target = (
            NEUTRAL_MORALE
            + params.wage_morale_sensitivity * wage_gap
            - 0.4 * overload
            - HOURS_CUT_MORALE * max(0.0, cut - HOURS_CUT_UNNOTICED)
            - OVERTIME_MORALE * max(0.0, -cut)
        )
        s.morale = max(0.05, min(1.0, s.morale + 0.04 * (target - s.morale)))
        if s.state is StaffState.EMPLOYED:
            s.skill = min(1.0, s.skill + 0.0003)
            if rng.random() < params.quit_hazard * (QUIT_FLOOR + QUIT_MORALE * (1 - s.morale) ** 2):
                s.state = step(log, day, mc.STAFF, s.id, s.state, ev.GIVE_NOTICE)
                s.state_until_day = day + NOTICE_DAYS
            elif rng.random() < SICK_RATE * (1.4 - s.reliability):
                s.state = step(log, day, mc.STAFF, s.id, s.state, ev.FALL_SICK)
                s.state_until_day = day + rng.randint(1, 4)
        elif s.state is StaffState.SICK and day >= s.state_until_day:
            s.state = step(log, day, mc.STAFF, s.id, s.state, ev.RECOVER)
        elif s.state is StaffState.ON_NOTICE and day >= s.state_until_day:
            s.state = step(log, day, mc.STAFF, s.id, s.state, ev.LEAVE)
            if world.business.auto_hire:
                world.business.vacancies.append(Vacancy(s.role, loc.id, day + VACANCY_DAYS, s.rota))


def hire_now(world: World, rng: random.Random, v: Vacancy) -> Staff:
    day, log, staff = world.day, world.business.transitions, world.business.staff
    seq = len(staff)
    hire = Staff(
        id=StaffId(f"s{seq}"),
        name=HIRE_NAMES[seq % len(HIRE_NAMES)],
        role=v.role,
        state=StaffState.CANDIDATE,
        location_id=v.location_id,
        wage_cents_per_hour=Cents(round(market_wage(world, v.role) * rng.uniform(0.97, 1.05))),
        skill=rng.uniform(0.4, 0.7),
        speed=rng.uniform(0.92, 1.08),
        reliability=rng.uniform(0.7, 0.98),
        morale=0.7,
        hired_day=day,
    )
    for event in (mc.StaffEvent.INTERVIEW, mc.StaffEvent.OFFER, mc.StaffEvent.ACCEPT):
        hire.state = step(log, day, mc.STAFF, hire.id, hire.state, event)
    if v.rota:
        hire.rota, hire.contract_hours = v.rota, rota_hours(v.rota)
    else:
        assign_rota(staff, hire, world.business.locations[v.location_id])
    staff[hire.id] = hire
    return hire


def _fill_vacancies(world: World, rng: random.Random) -> None:
    remaining = []
    for v in world.business.vacancies:
        if v.fill_day > world.day:
            remaining.append(v)
        else:
            hire_now(world, rng, v)
    world.business.vacancies = remaining


def _boredom(world: World, loc: Location) -> float:
    return world.menu_boredom * min(1.0, (world.day - loc.menu_changed_day) / 365)


def _fatigue_retention(world: World) -> float:
    trading = [
        loc for loc in world.business.locations.values() if loc.state is LocationState.TRADING
    ]
    return FATIGUE_RETENTION + BOREDOM_RETENTION * min(
        (_boredom(world, loc) for loc in trading), default=0.0
    )


def _advance_customers(world: World, rng: random.Random) -> None:
    day, log, params = world.day, world.business.transitions, world.params
    ev = mc.CustomerEvent
    hazard = 1 - (1 - params.monthly_lapse_rate) ** (1 / 30)
    keep = _fatigue_retention(world)
    for c in world.customers:
        if c.state is CustomerState.CHURNED:
            continue
        if c.fatigue:
            c.fatigue = {k: v * keep for k, v in c.fatigue.items() if v * keep >= 0.01}
        if c.state is CustomerState.LAPSED:
            if day - c.state_since_day >= CHURN_AFTER_LAPSED_DAYS:
                c.state = step(log, day, mc.CUSTOMER, c.id, c.state, ev.GIVE_UP)
                c.state_since_day = day
            continue
        last = c.last_visit_day if c.last_visit_day is not None else c.created_day
        limit = LAPSE_AFTER_DAYS if c.state is CustomerState.REGULAR else NEW_QUIET_AFTER_DAYS
        if day - last >= limit or rng.random() < hazard * (1.6 - c.satisfaction):
            c.state = step(log, day, mc.CUSTOMER, c.id, c.state, ev.GO_QUIET)
            c.state_since_day = day


def _lease_tick(world: World) -> None:
    for loc in world.business.locations.values():
        lease = loc.lease
        if lease.state is LeaseState.ACTIVE and world.day >= lease.end_day - 90:
            lease.state = step(
                world.business.transitions,
                world.day,
                mc.LEASE,
                loc.id,
                lease.state,
                mc.LeaseEvent.OPEN_WINDOW,
            )


def _charge_interest(business: Business, day: int) -> int:
    owed = business.debt_cents * LOAN_APR + business.credit_drawn_cents * CREDIT_APR
    interest = round(owed / 365)
    post(business, day, Account.INTEREST, -interest)
    return interest


def simulate_day(world: World, rng: random.Random) -> DayReport:
    for hook in world.daily_hooks:
        hook(world)
    day = world.day
    temp, rain = world.weather.on(day)
    prices = prices_paid(world, day)
    log = world.business.transitions
    mark = len(log)
    reports = tuple(
        _run_location(world, loc, rng, (temp, rain), prices)
        for loc in sorted(world.business.locations.values(), key=lambda loc: loc.id)
        if loc.state is LocationState.TRADING
    )
    interest = _charge_interest(world.business, day)
    _advance_customers(world, rng)
    _fill_vacancies(world, stream(world, "hires"))
    _lease_tick(world)
    world.day += 1
    return DayReport(
        day=day,
        weekday=day % 7,
        temp_c=round(temp, 1),
        rain=rain,
        locations=reports,
        interest_cents=Cents(interest),
        prices_paid=prices,
        cash_cents=world.business.cash_cents,
        transitions=tuple(log[mark:]),
    )


def run_days(world: World, days: int, policy: Policy, rng: random.Random) -> list[DayReport]:
    reports = []
    for _ in range(days):
        policy(world, world.day)
        reports.append(simulate_day(world, rng))
    return reports
