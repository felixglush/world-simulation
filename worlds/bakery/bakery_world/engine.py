import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from bakery_world import machines as mc
from bakery_world.basics import (
    Category,
    Cents,
    CustomerId,
    ItemId,
    LocationId,
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
    Customer,
    Location,
    Staff,
    Vacancy,
    World,
)
from bakery_world.town import proximity, spawn_customer

BETA = 1.6
CARD_FEE = 0.02
DAY_OLD_DISCOUNT = 0.5
DAY_OLD_QUALITY = 0.75
COUNTER_CUSTOMERS_PER_HOUR = 36.0
BAKER_SHIFT_HOURS = 8
CLEANER_SHIFT_HOURS = 4
BAKE_DEADLINE_HOUR = 12
PRE_OPEN_BAKE_HOURS = 2
VACANCY_DAYS = 10
NOTICE_DAYS = 14
OVEN_REPAIR_CENTS = 40_000
OVEN_REPAIR_DAYS = 2
OVEN_WEAR_PER_FULL_DAY = 0.0035
WORN_OVEN_CAPACITY = 0.85
STOCKOUT_SUBSTITUTE_PROB = 0.5
LAPSE_AFTER_DAYS = 30
CHURN_AFTER_LAPSED_DAYS = 90
NEW_QUIET_AFTER_DAYS = 60
REGULAR_VISITS = 4
STATE_VISIT_FACTOR = {CustomerState.NEW: 1.0, CustomerState.REGULAR: 1.0, CustomerState.LAPSED: 0.1}

SEGMENT_WEEKDAY: dict[Segment, tuple[float, ...]] = {
    Segment.COMMUTER: (1.25, 1.25, 1.25, 1.25, 1.2, 0.5, 0.3),
    Segment.OFFICE: (1.3, 1.3, 1.3, 1.3, 1.2, 0.4, 0.2),
    Segment.STUDENT: (1.1, 1.1, 1.1, 1.1, 1.0, 0.9, 0.7),
    Segment.FAMILY: (0.7, 0.7, 0.7, 0.8, 0.9, 1.9, 1.3),
    Segment.RETIREE: (1.1, 1.1, 1.1, 1.1, 1.0, 0.8, 0.8),
}
BASKET_SIZE_WEIGHTS: dict[Segment, tuple[float, float, float]] = {
    Segment.COMMUTER: (0.55, 0.30, 0.15),
    Segment.OFFICE: (0.40, 0.35, 0.25),
    Segment.STUDENT: (0.60, 0.30, 0.10),
    Segment.FAMILY: (0.35, 0.35, 0.30),
    Segment.RETIREE: (0.55, 0.30, 0.15),
}


@dataclass(frozen=True)
class SaleLine:
    item_id: ItemId
    price_cents: Cents
    day_old: bool


@dataclass(frozen=True)
class Sale:
    hour: int
    customer_id: CustomerId
    loyalty_id: str | None
    lines: tuple[SaleLine, ...]


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
    revenue_cents: Cents
    ingredient_cost_cents: Cents
    wages_cents: Cents
    rent_cents: Cents
    card_fees_cents: Cents
    repairs_cents: Cents

    @property
    def costs_cents(self) -> int:
        return (
            self.ingredient_cost_cents
            + self.wages_cents
            + self.rent_cents
            + self.card_fees_cents
            + self.repairs_cents
        )


@dataclass(frozen=True)
class DayReport:
    day: int
    weekday: int
    temp_c: float
    rain: bool
    locations: tuple[LocationDay, ...]
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


@dataclass
class _Roster:
    bakers: list[Staff] = field(default_factory=list)
    counters: list[Staff] = field(default_factory=list)
    cleaners: list[Staff] = field(default_factory=list)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def _on_shift(staff: list[Staff], day: int) -> list[Staff]:
    return [s for i, s in enumerate(staff) if day % 7 not in ((2 * i) % 7, (2 * i + 1) % 7)]


def _roster(world: World, loc: Location) -> _Roster:
    working = (StaffState.EMPLOYED, StaffState.ON_NOTICE)
    by_role: dict[Role, list[Staff]] = {r: [] for r in Role}
    for s in sorted(world.business.staff.values(), key=lambda s: s.id):
        if s.location_id == loc.id and s.state in working:
            by_role[s.role].append(s)
    return _Roster(
        _on_shift(by_role[Role.BAKER], world.day),
        _on_shift(by_role[Role.COUNTER], world.day),
        _on_shift(by_role[Role.CLEANER], world.day),
    )


def _effort(s: Staff) -> float:
    return s.speed * (0.7 + 0.3 * s.skill)


def _counter_capacity(loc: Location, counters: list[Staff]) -> list[float]:
    open_len = loc.close_hour - loc.open_hour
    if open_len <= 0:
        return [0.0] * 24
    cover = min(8, open_len) / open_len
    per_hour = COUNTER_CUSTOMERS_PER_HOUR * cover * sum(_effort(s) for s in counters)
    return [per_hour if loc.open_hour <= h < loc.close_hour else 0.0 for h in range(24)]


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


def _wages(loc: Location, roster: _Roster) -> int:
    open_len = loc.close_hour - loc.open_hour
    if open_len <= 0:
        return 0
    hours = {
        Role.BAKER: BAKER_SHIFT_HOURS,
        Role.COUNTER: min(8, open_len),
        Role.CLEANER: CLEANER_SHIFT_HOURS,
    }
    return sum(
        s.wage_cents_per_hour * hours[s.role]
        for s in roster.bakers + roster.counters + roster.cleaners
    )


def _ingredient_cost(item: ItemId, units: int, prices: dict) -> int:
    grams = CATALOG[item].grams
    return round(units * sum(g * prices[ing] for ing, g in grams.items()) / 1000)


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
    loc: Location, roster: _Roster, weekday: int, prices: dict, items: dict[ItemId, ItemDay]
) -> tuple[dict[ItemId, int], int, float]:
    window_hours = max(0, BAKE_DEADLINE_HOUR - (loc.open_hour - PRE_OPEN_BAKE_HOURS))
    capacity_by_oven = {
        o.id: 60.0 * window_hours * (WORN_OVEN_CAPACITY if o.state is EquipmentState.WORN else 1.0)
        for o in loc.ovens
        if o.state in (EquipmentState.WORKING, EquipmentState.WORN)
    }
    oven_total = sum(capacity_by_oven.values())
    oven_left = oven_total
    labor_left = sum(BAKER_SHIFT_HOURS * 60 * _effort(b) for b in roster.bakers)
    fresh: dict[ItemId, int] = {}
    cost = 0
    ordered = sorted(loc.menu.items(), key=lambda kv: kv[1].state is not MenuState.ON_MENU)
    for item, entry in ordered:
        recipe = CATALOG[item]
        plan = entry.bake_plan[weekday]
        if entry.state not in (MenuState.ON_MENU, MenuState.TRIAL) or recipe.made_to_order:
            continue
        if plan <= 0:
            continue
        by_oven = int(oven_left // recipe.oven_minutes) * recipe.batch_size
        by_labor = int(labor_left // recipe.labor_minutes)
        units = min(plan, by_oven, by_labor)
        if units <= 0:
            continue
        oven_left -= math.ceil(units / recipe.batch_size) * recipe.oven_minutes
        labor_left -= units * recipe.labor_minutes
        fresh[item] = units
        items.setdefault(item, ItemDay()).baked = units
        cost += _ingredient_cost(item, units, prices)
    utilisation = 0.0 if oven_total == 0 else 1 - oven_left / oven_total
    return fresh, cost, utilisation


def _build_offers(
    loc: Location, fresh: dict[ItemId, int], old: dict[ItemId, dict[int, int]]
) -> list[_Offer]:
    offers = []
    for item, entry in loc.menu.items():
        recipe = CATALOG[item]
        if entry.state not in (MenuState.ON_MENU, MenuState.TRIAL):
            continue
        typical = recipe.typical_price_cents
        if recipe.made_to_order or fresh.get(item, 0) > 0:
            offers.append(_Offer(item, recipe.category, entry.price_cents, typical, False, 1.0))
        if old.get(item):
            price = round(entry.price_cents * DAY_OLD_DISCOUNT)
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
            weather = (0.92 if seg is Segment.COMMUTER else 0.85) if rain else 1.0
            if temp < 2 or temp > 30:
                weather *= 0.9
            factors[(nb.id, seg)] = (
                traffic * SEGMENT_WEEKDAY[seg][weekday] * weather / (1 + 0.45 * pull[seg])
            )
    return factors


def _run_location(world: World, loc: Location, rng: random.Random, weather: tuple, prices: dict):
    day, weekday = world.day, world.day % 7
    params = world.params
    log = world.business.transitions
    items: dict[ItemId, ItemDay] = {}
    roster = _roster(world, loc)
    old = _age_stock(loc, items)
    fresh, bake_cost, oven_use = _bake(loc, roster, weekday, prices, items)
    offers = _build_offers(loc, fresh, old)
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

    # who comes
    nbs = world.town
    home = nbs[loc.neighborhood_id]
    factors = _visit_factors(world, day, weather[0], weather[1])
    awareness_f = 0.4 + 1.2 * loc.awareness
    reputation_f = 0.7 + 0.6 * loc.reputation
    cum = {nb.id: _cumulative(nb.hourly_traffic) for nb in nbs.values()}
    prox = {nb.id: proximity(nb, home) for nb in nbs.values()}
    scale = awareness_f * reputation_f / 7
    visitors: list[tuple[int, Customer]] = []
    hours = range(24)
    for c in world.customers:
        if c.state is CustomerState.CHURNED:
            continue
        p = (
            c.base_visit_rate
            * scale
            * factors[(c.neighborhood_id, c.segment)]
            * prox[c.neighborhood_id]
            * STATE_VISIT_FACTOR[c.state]
            * (0.5 + 0.7 * c.satisfaction)
        )
        if rng.random() < p:
            visitors.append((rng.choices(hours, cum_weights=cum[c.neighborhood_id])[0], c))
    arrival_rate = sum(
        loc.awareness * nb.population * prox[nb.id] * params.new_customer_rate
        for nb in nbs.values()
    )
    new_count = _poisson(rng, arrival_rate)
    nb_list = list(nbs.values())
    nb_weights = [nb.population * prox[nb.id] for nb in nb_list]
    for _ in range(new_count):
        nb = rng.choices(nb_list, weights=nb_weights)[0]
        c = spawn_customer(rng, len(world.customers), nb, day)
        world.customers.append(c)
        visitors.append((rng.choices(hours, cum_weights=cum[nb.id])[0], c))

    open_hours = range(loc.open_hour, loc.close_hour)
    inside = [(h, c) for h, c in visitors if h in open_hours]
    after_close = len(visitors) - len(inside)
    rng.shuffle(inside)
    inside.sort(key=lambda hc: hc[0])
    arrivals = [0] * 24
    for h, _ in inside:
        arrivals[h] += 1
    capacity = _counter_capacity(loc, roster.counters)
    waits = [_wait_minutes(arrivals[h], capacity[h]) for h in hours]

    weight_cache: dict[tuple, tuple[list[float], list[float]]] = {}
    sales: list[Sale] = []
    walkaways = [0] * 24
    revenue = 0
    drink_cost = 0
    satisfactions = []
    cleaner_penalty = 0.0 if roster.cleaners else 0.04
    w_out = math.exp(BETA * params.outside_option)
    old_lots = old

    for hour, c in inside:
        wait = waits[hour]
        if rng.random() < _walkaway_probability(wait, params.walkaway_wait_min):
            walkaways[hour] += 1
            _update_satisfaction(c, 0.25)
            satisfactions.append(c.satisfaction)
            continue
        nb = nbs[c.neighborhood_id]
        key = (c.segment, nb.id)
        if key not in weight_cache:
            taste = [
                params.taste[c.segment][o.category] * params.item_appeal[o.item_id] * o.quality
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
        for _ in range(n_items):
            i = _pick(rng, weights, w_out)
            if i is None:
                break
            if qty[i] == 0:
                items.setdefault(offers[i].item_id, ItemDay()).unmet += 1
                misses += 1
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
                drink_cost += _ingredient_cost(o.item_id, 1, prices)
            lines.append(SaleLine(o.item_id, Cents(o.price), o.day_old))
            revenue += o.price
            c.fatigue[o.item_id] = min(0.8, c.fatigue.get(o.item_id, 0.0) + 0.12)
            weights = _refatigue(
                weights, base_w, tastes, offers_by_item[o.item_id], c.fatigue[o.item_id]
            )
        target = 0.85 - min(0.3, 0.04 * min(wait, 20)) - 0.15 * misses - cleaner_penalty
        if not lines:
            target = min(target, 0.3)
        target -= 0.05 * sum(line.day_old for line in lines)
        _update_satisfaction(c, target)
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
            sales.append(Sale(hour, c.id, c.loyalty_id, tuple(lines)))

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

    # money
    repairs = _tend_ovens(world, loc, oven_use, rng)
    wages = _wages(loc, roster)
    rent = round(loc.lease.monthly_rent_cents * 12 / 365)
    fees = round(revenue * CARD_FEE)
    ingredient_cost = bake_cost + drink_cost
    world.business.cash_cents = Cents(
        world.business.cash_cents + revenue - ingredient_cost - wages - rent - fees - repairs
    )

    # slow variables
    n = len(satisfactions)
    if n:
        mean_sat = sum(satisfactions) / n
        loc.reputation += params.reputation_drift * min(1.0, n / 60) * (mean_sat - loc.reputation)
    target_aw = 0.25 + 0.55 * min(1.0, len(inside) / 200) * (0.6 + 0.4 * loc.reputation)
    loc.awareness += 0.02 * (target_aw - loc.awareness)
    _staff_day(world, loc, roster, arrivals, capacity, rng)

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
        revenue_cents=Cents(revenue),
        ingredient_cost_cents=Cents(ingredient_cost),
        wages_cents=Cents(wages),
        rent_cents=Cents(rent),
        card_fees_cents=Cents(fees),
        repairs_cents=Cents(repairs),
    )


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
        if s.location_id != loc.id or s.state in (StaffState.CANDIDATE, StaffState.GONE):
            continue
        wage_gap = s.wage_cents_per_hour / MARKET_WAGE_CENTS[s.role] - 1
        target = 0.6 + params.wage_morale_sensitivity * wage_gap - 0.4 * overload
        s.morale = max(0.05, min(1.0, s.morale + 0.04 * (target - s.morale)))
        if s.state is StaffState.EMPLOYED:
            s.skill = min(1.0, s.skill + 0.0003)
            if rng.random() < params.quit_hazard * 4 * (1 - s.morale) ** 2:
                s.state = step(log, day, mc.STAFF, s.id, s.state, ev.GIVE_NOTICE)
                s.state_until_day = day + NOTICE_DAYS
            elif rng.random() < 0.006 * (1.4 - s.reliability):
                s.state = step(log, day, mc.STAFF, s.id, s.state, ev.FALL_SICK)
                s.state_until_day = day + rng.randint(1, 4)
        elif s.state is StaffState.SICK and day >= s.state_until_day:
            s.state = step(log, day, mc.STAFF, s.id, s.state, ev.RECOVER)
        elif s.state is StaffState.ON_NOTICE and day >= s.state_until_day:
            s.state = step(log, day, mc.STAFF, s.id, s.state, ev.LEAVE)
            world.business.vacancies.append(Vacancy(s.role, loc.id, day + VACANCY_DAYS))


def _fill_vacancies(world: World, rng: random.Random) -> None:
    day, log, staff = world.day, world.business.transitions, world.business.staff
    ev, remaining = mc.StaffEvent, []
    for v in world.business.vacancies:
        if v.fill_day > day:
            remaining.append(v)
            continue
        seq = len(staff)
        market = MARKET_WAGE_CENTS[v.role]
        hire = Staff(
            id=StaffId(f"s{seq}"),
            name=f"Hire {seq}",
            role=v.role,
            state=StaffState.CANDIDATE,
            location_id=v.location_id,
            wage_cents_per_hour=Cents(round(market * rng.uniform(0.97, 1.05))),
            skill=rng.uniform(0.3, 0.8),
            speed=rng.uniform(0.85, 1.15),
            reliability=rng.uniform(0.7, 0.98),
            morale=0.7,
        )
        for event in (ev.INTERVIEW, ev.OFFER, ev.ACCEPT):
            hire.state = step(log, day, mc.STAFF, hire.id, hire.state, event)
        staff[hire.id] = hire
    world.business.vacancies = remaining


def _advance_customers(world: World, rng: random.Random) -> None:
    day, log, params = world.day, world.business.transitions, world.params
    ev = mc.CustomerEvent
    hazard = 1 - (1 - params.monthly_lapse_rate) ** (1 / 30)
    for c in world.customers:
        if c.state is CustomerState.CHURNED:
            continue
        if c.fatigue:
            c.fatigue = {k: v * 0.93 for k, v in c.fatigue.items() if v * 0.93 >= 0.01}
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


def simulate_day(world: World, rng: random.Random) -> DayReport:
    day = world.day
    temp, rain = world.weather.on(day)
    prices = world.ingredient_prices.on(day)
    log = world.business.transitions
    mark = len(log)
    reports = tuple(
        _run_location(world, loc, rng, (temp, rain), prices)
        for loc in sorted(world.business.locations.values(), key=lambda loc: loc.id)
        if loc.state is LocationState.TRADING
    )
    _advance_customers(world, rng)
    _fill_vacancies(world, rng)
    _lease_tick(world)
    world.day += 1
    return DayReport(
        day=day,
        weekday=day % 7,
        temp_c=round(temp, 1),
        rain=rain,
        locations=reports,
        cash_cents=world.business.cash_cents,
        transitions=tuple(log[mark:]),
    )


def run_days(world: World, days: int, policy: Policy, rng: random.Random) -> list[DayReport]:
    reports = []
    for _ in range(days):
        policy(world, world.day)
        reports.append(simulate_day(world, rng))
    return reports
