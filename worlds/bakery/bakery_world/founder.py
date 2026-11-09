import math
import random
from collections import deque
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
    date_of,
)
from bakery_world.catalog import CATALOG
from bakery_world.decay import DecayHook, ForceId
from bakery_world.engine import (
    DayReport,
    ingredient_cost,
    market_wage,
    post,
    prices_paid,
)
from bakery_world.machines import EquipmentState, LeaseState, LocationState, MenuState, StaffState
from bakery_world.model import Equipment, Lease, Location, MenuEntry, Vacancy, World
from bakery_world.town import HOME_LOCATION

COST_PLUS_MARKUP = 2.0
COST_PLUS_FLAT_CENTS = 200
PRICE_SANITY_BAND = (0.8, 1.25)
DRINK_LABOR_MINUTES = 1.5
PLAN_HEADROOM = 1.1
PLAN_WEEKS = 4
OPEN_HOUR, CLOSE_HOUR = 7, 15
RAISE = 0.03
REVIEW_DAYS = (365,)
RAISE_DAYS = (365, 730)
MARKETING_BY_YEAR_CENTS = (150_000, 50_000, 50_000)
LOAN_PRINCIPAL_PER_MONTH_CENTS = 75_000
CASH_FLOOR_CENTS = 500_000
CASH_COMFORT_CENTS = 3_000_000
TRIAL_DAYS = 14
OVEN_SERVICE_CENTS = 18_000
SERVICED_OVEN_WEAR = 0.2
TRIAL_PLAN_UNITS = 10
LAUNCHES: tuple[tuple[int, ItemId], ...] = tuple(
    (d, ItemId(i))
    for d, i in (
        (45, "cinnamon"),
        (90, "choccake"),
        (150, "scone"),
        (210, "focaccia"),
        (270, "rye"),
        (330, "carrotcake"),
    )
)

SECOND_SHOP = LocationId("shop2")
SECOND_SHOP_NEIGHBORHOOD = NeighborhoodId("maple")
SECOND_SHOP_M2 = 55
SECOND_SHOP_SIGN_DAY = (390, 420)
SECOND_SHOP_FITOUT_DAYS = 30
SECOND_SHOP_OPEN_MONTHS = (6, 8)
SECOND_SHOP_FITOUT_CENTS = 3_200_000
SECOND_SHOP_LEASE_YEARS = 3
SECOND_SHOP_BREAK_MONTHS = 3
SECOND_SHOP_MARKETING_CENTS = 80_000
SECOND_SHOP_STAFF = (Role.BAKER, Role.COUNTER, Role.COUNTER)
SECOND_SHOP_BAKE_SHARE = 0.3


CATERING_CLIENTS = (
    "Kestrel Mutual staff breakfast",
    "Market Square traders' meeting",
    "a christening at St Anne's",
    "Campus Heights open day",
    "a retirement party at the library",
)
HISTORY_CATERING_ORDERS = 3
CATERING_COST_SHARE = 0.4


@dataclass(frozen=True)
class CateringOrder:
    day: int
    client: str
    units: int
    value_cents: Cents


def draw_catering(rng: random.Random, first: int, last: int, count: int) -> list[CateringOrder]:
    orders = []
    for day in sorted(rng.randint(first, last) for _ in range(count)):
        units = rng.randrange(60, 200, 10)
        orders.append(CateringOrder(day, rng.choice(CATERING_CLIENTS), units, Cents(units * 450)))
    return orders


def deliver_catering(world: World, day: int, order: CateringOrder, loc: LocationId) -> None:
    memo = f"Catering: {order.client}, {order.units} pastries and coffee"
    post(world.business, day, Account.CATERING, order.value_cents, loc, memo)
    cost = round(order.value_cents * CATERING_COST_SHARE)
    post(world.business, day, Account.CATERING_COSTS, -cost, loc, f"{memo} (ingredients, labour)")


def service_ovens(world: World, day: int, loc: Location) -> None:
    for oven in loc.ovens:
        if oven.state is EquipmentState.WORN:
            oven.state = mc.step(
                world.business.transitions,
                day,
                mc.EQUIPMENT,
                oven.id,
                oven.state,
                mc.EquipmentEvent.SERVICE,
            )
            oven.wear = SERVICED_OVEN_WEAR
            post(
                world.business,
                day,
                Account.REPAIRS,
                -OVEN_SERVICE_CENTS,
                loc.id,
                f"{oven.id} service",
            )


def repay_loan(world: World, day: int) -> None:
    business = world.business
    repay = min(business.debt_cents, LOAN_PRINCIPAL_PER_MONTH_CENTS)
    business.debt_cents = Cents(business.debt_cents - repay)
    post(business, day, Account.LOAN_REPAYMENT, -repay, None, "equipment loan principal")


def treasury(world: World, day: int) -> None:
    b = world.business
    if b.cash_cents < CASH_FLOOR_CENTS:
        draw = min(b.credit_limit_cents - b.credit_drawn_cents, CASH_FLOOR_CENTS - b.cash_cents)
        if draw > 0:
            b.credit_drawn_cents = Cents(b.credit_drawn_cents + draw)
            post(b, day, Account.CREDIT_DRAWN, draw, None, "drawn on credit line")
    elif b.cash_cents > CASH_COMFORT_CENTS and b.credit_drawn_cents > 0:
        repay = min(b.credit_drawn_cents, b.cash_cents - CASH_COMFORT_CENTS)
        b.credit_drawn_cents = Cents(b.credit_drawn_cents - repay)
        post(b, day, Account.CREDIT_REPAID, -repay, None, "credit line repaid")


@dataclass
class Quirks:
    price_factor: dict[ItemId, float] = field(default_factory=dict)
    plan_factor: dict[tuple[ItemId, int], float] = field(default_factory=dict)


@dataclass(frozen=True)
class Expansion:
    sign_day: int
    fitout_day: int
    open_day: int
    close_day: int


def _round25(cents: float) -> Cents:
    return Cents(max(25, round(cents / 25) * 25))


def unit_cost_cents(item: ItemId, prices: dict[Ingredient, int], world: World) -> float:
    recipe = CATALOG[item]
    if recipe.made_to_order:
        labor = DRINK_LABOR_MINUTES * market_wage(world, Role.COUNTER) / 60
    else:
        labor = recipe.labor_minutes * market_wage(world, Role.BAKER) / 60
    return ingredient_cost(dict(recipe.grams), prices) + labor


def cost_plus_price(item: ItemId, prices: dict[Ingredient, int], world: World) -> Cents:
    typical = CATALOG[item].typical_price_cents
    raw = COST_PLUS_MARKUP * unit_cost_cents(item, prices, world) + COST_PLUS_FLAT_CENTS
    low, high = PRICE_SANITY_BAND
    return _round25(min(high * typical, max(low * typical, raw)))


class Founder:
    def __init__(self, seed: int, hook: DecayHook, quirks: Quirks | None = None) -> None:
        rng = random.Random(f"{seed}:founder")
        sign = rng.randint(*SECOND_SHOP_SIGN_DAY)
        fitout = sign + 5
        opened = fitout + SECOND_SHOP_FITOUT_DAYS
        self.expansion = Expansion(
            sign, fitout, opened, opened + round(30.4 * rng.uniform(*SECOND_SHOP_OPEN_MONTHS))
        )
        self.hook = hook
        self.quirks = quirks or Quirks()
        self.demand: dict[tuple[LocationId, ItemId, int], deque[int]] = {}
        self.recent_prices: deque[dict[Ingredient, int]] = deque(maxlen=30)
        self.catering = draw_catering(
            random.Random(f"{seed}:catering-history"), 150, 1050, HISTORY_CATERING_ORDERS
        )

    def __call__(self, world: World, day: int) -> None:
        self.recent_prices.append(prices_paid(world, day))
        if day == 0:
            self._open(world)
        if date_of(day).day == 1:
            self._month_start(world, day)
        if day in REVIEW_DAYS:
            self._review_prices(world)
        if day in RAISE_DAYS and not self._tired(day):
            self._give_raises(world)
        self._launch_items(world, day)
        self._expand(world, day)
        for loc in world.business.locations.values():
            if loc.state is LocationState.TRADING:
                self._plan_bakes(loc, day)
        for order in self.catering:
            if order.day == day:
                deliver_catering(world, day, order, HOME_LOCATION)
        self._treasury(world, day)

    def observe(self, report: DayReport) -> None:
        for shop in report.locations:
            for item, stat in shop.items.items():
                key = (shop.location_id, item, report.weekday)
                self.demand.setdefault(key, deque(maxlen=PLAN_WEEKS)).append(stat.sold + stat.unmet)

    def _tired(self, day: int) -> bool:
        return self.hook.active(ForceId.FOUNDER_FATIGUE, day)

    def _price(self, world: World, item: ItemId) -> Cents:
        if item in self.quirks.price_factor:
            return _round25(CATALOG[item].typical_price_cents * self.quirks.price_factor[item])
        return cost_plus_price(item, self._mean_prices(), world)

    def _mean_prices(self) -> dict[Ingredient, int]:
        rows = self.recent_prices
        return {ing: round(sum(r[ing] for r in rows) / len(rows)) for ing in rows[0]}

    def _open(self, world: World) -> None:
        for loc in world.business.locations.values():
            loc.open_hour, loc.close_hour = OPEN_HOUR, CLOSE_HOUR
            for item, entry in loc.menu.items():
                entry.price_cents = self._price(world, item)

    def _review_prices(self, world: World) -> None:
        for loc in world.business.locations.values():
            for item, entry in loc.menu.items():
                entry.price_cents = Cents(max(entry.price_cents, self._price(world, item)))

    def _give_raises(self, world: World) -> None:
        for s in world.business.staff.values():
            if not s.owner and s.state is not StaffState.GONE:
                s.wage_cents_per_hour = Cents(round(s.wage_cents_per_hour * (1 + RAISE)))

    def _marketing(self, loc: Location, day: int) -> Cents:
        if self.hook.active(ForceId.FEW_NEW_CUSTOMERS, day):
            return Cents(0)
        if loc.id == SECOND_SHOP:
            return Cents(SECOND_SHOP_MARKETING_CENTS)
        return Cents(MARKETING_BY_YEAR_CENTS[min(2, day // 365)])

    def _month_start(self, world: World, day: int) -> None:
        business = world.business
        for loc in business.locations.values():
            spend = self._marketing(loc, day) if loc.state is LocationState.TRADING else 0
            loc.marketing_cents_per_month = Cents(spend)
            post(business, day, Account.MARKETING, -spend, loc.id, "local advertising")
            service_ovens(world, day, loc)
        repay_loan(world, day)

    def _treasury(self, world: World, day: int) -> None:
        treasury(world, day)

    def _plan_bakes(self, loc: Location, day: int) -> None:
        weekday = day % 7
        for item, entry in loc.menu.items():
            history = self.demand.get((loc.id, item, weekday))
            if CATALOG[item].made_to_order or not history:
                continue
            factor = PLAN_HEADROOM * self.quirks.plan_factor.get((item, weekday), 1.0)
            planned = math.ceil(sum(history) / len(history) * factor)
            entry.bake_plan[weekday] = max(math.ceil(CATALOG[item].batch_size / 2), planned)

    def _launch_items(self, world: World, day: int) -> None:
        loc = world.business.locations[HOME_LOCATION]
        log = world.business.transitions
        for launch_day, item in LAUNCHES:
            if day == launch_day and item not in loc.menu and not self._tired(day):
                entry = MenuEntry(
                    MenuState.CATALOG, self._price(world, item), [TRIAL_PLAN_UNITS] * 7
                )
                entry.state = mc.step(
                    log, day, mc.MENU_ITEM, item, entry.state, mc.MenuEvent.START_TRIAL
                )
                loc.menu[item] = entry
                loc.menu_changed_day = day
            entry = loc.menu.get(item)
            if entry and entry.state is MenuState.TRIAL and day == launch_day + TRIAL_DAYS:
                entry.state = mc.step(
                    log, day, mc.MENU_ITEM, item, entry.state, mc.MenuEvent.ADD_TO_MENU
                )

    def _expand(self, world: World, day: int) -> None:
        plan, b = self.expansion, world.business
        log, ev = b.transitions, mc.LocationEvent
        if day == plan.sign_day:
            home = b.locations[HOME_LOCATION]
            rent = SECOND_SHOP_M2 * world.town[SECOND_SHOP_NEIGHBORHOOD].rent_cents_per_m2_month
            shop = Location(
                id=SECOND_SHOP,
                neighborhood_id=SECOND_SHOP_NEIGHBORHOOD,
                size_m2=SECOND_SHOP_M2,
                ovens=[Equipment("oven4")],
                state=LocationState.SCOUTED,
                lease=Lease(LeaseState.ACTIVE, Cents(rent), day + SECOND_SHOP_LEASE_YEARS * 365),
                menu={
                    item: MenuEntry(
                        MenuState.ON_MENU,
                        e.price_cents,
                        [math.ceil(u * SECOND_SHOP_BAKE_SHARE) for u in e.bake_plan],
                    )
                    for item, e in home.menu.items()
                    if e.state is MenuState.ON_MENU
                },
                open_hour=OPEN_HOUR,
                close_hour=CLOSE_HOUR,
                reputation=0.6,
                awareness=0.05,
                menu_changed_day=day,
            )
            shop.state = mc.step(log, day, mc.LOCATION, shop.id, shop.state, ev.SIGN_LEASE)
            b.locations[shop.id] = shop
        elif day == plan.fitout_day:
            shop = b.locations[SECOND_SHOP]
            shop.state = mc.step(log, day, mc.LOCATION, shop.id, shop.state, ev.START_FITOUT)
            post(b, day, Account.FIT_OUT, -SECOND_SHOP_FITOUT_CENTS, shop.id, "Maple Grove fit-out")
            for role in SECOND_SHOP_STAFF:
                b.vacancies.append(Vacancy(role, shop.id, plan.open_day - 1))
        elif day == plan.open_day:
            shop = b.locations[SECOND_SHOP]
            shop.state = mc.step(log, day, mc.LOCATION, shop.id, shop.state, ev.OPEN_DOORS)
        elif day == plan.close_day:
            self._close_second_shop(world, day)

    def _close_second_shop(self, world: World, day: int) -> None:
        b = world.business
        log, shop = b.transitions, b.locations[SECOND_SHOP]
        for event in (mc.LocationEvent.ANNOUNCE_CLOSING, mc.LocationEvent.SHUT_DOORS):
            shop.state = mc.step(log, day, mc.LOCATION, shop.id, shop.state, event)
        shop.lease.state = mc.step(
            log, day, mc.LEASE, shop.id, shop.lease.state, mc.LeaseEvent.TERMINATE
        )
        shop.marketing_cents_per_month = Cents(0)
        fee = SECOND_SHOP_BREAK_MONTHS * shop.lease.monthly_rent_cents
        post(b, day, Account.LEASE_BREAK, -fee, shop.id, "Maple Grove lease surrender")
        b.vacancies = [v for v in b.vacancies if v.location_id != shop.id]
        for s in b.staff.values():
            if s.location_id != shop.id or s.state is StaffState.GONE:
                continue
            if s.state is not StaffState.ON_NOTICE:
                s.state = mc.step(log, day, mc.STAFF, s.id, s.state, mc.StaffEvent.GIVE_NOTICE)
            s.state = mc.step(log, day, mc.STAFF, s.id, s.state, mc.StaffEvent.LEAVE)
