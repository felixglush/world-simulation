import math
import random
from dataclasses import dataclass, replace
from datetime import date

from bakery_world.basics import (
    Cents,
    CompetitorId,
    CustomerId,
    Ingredient,
    ItemId,
    LocationId,
    NeighborhoodId,
    Role,
    Segment,
    StaffId,
    date_of,
)
from bakery_world.catalog import CATALOG, OPENING_MENU, STARTING_MENU
from bakery_world.exogenous import MeanRevertingPrices, SeasonalFootTraffic, SeasonalWeather
from bakery_world.machines import (
    CompetitorState,
    CustomerState,
    LeaseState,
    LocationState,
    MenuState,
    StaffState,
)
from bakery_world.model import (
    MARKET_WAGE_CENTS,
    Business,
    Competitor,
    Customer,
    Equipment,
    Lease,
    Location,
    MenuEntry,
    Neighborhood,
    Staff,
    Supplier,
    World,
)
from bakery_world.params import draw_hidden_params

HOME_NEIGHBORHOOD = NeighborhoodId("market")
HOME_LOCATION = LocationId("shop1")

SEGMENT_VISITS_PER_WEEK: dict[Segment, float] = {
    Segment.COMMUTER: 2.2,
    Segment.OFFICE: 1.8,
    Segment.STUDENT: 1.2,
    Segment.FAMILY: 0.8,
    Segment.RETIREE: 1.3,
}

SEGMENT_LOYALTY_ENROLL = 0.35


def _hours(weights: dict[int, float]) -> tuple[float, ...]:
    return tuple(weights.get(h, 0.0) for h in range(24))


OFFICE_TRAFFIC = _hours(
    {6: 1, 7: 4, 8: 7, 9: 6, 10: 4, 11: 6, 12: 9, 13: 8, 14: 5, 15: 4, 16: 4, 17: 4, 18: 2}
)
MARKET_QUIET_AFTERNOON = _hours(
    {6: 1, 7: 4, 8: 7, 9: 6, 10: 4, 11: 6, 12: 9, 13: 8, 14: 5, 15: 2, 16: 1, 17: 1}
)
MARKET_BUSY_AFTERNOON = _hours(
    {6: 1, 7: 4, 8: 7, 9: 6, 10: 4, 11: 6, 12: 9, 13: 8, 14: 6, 15: 7, 16: 7, 17: 6, 18: 3}
)
COMMUTE_TRAFFIC = _hours(
    {6: 3, 7: 8, 8: 10, 9: 6, 10: 4, 11: 4, 12: 5, 13: 4, 14: 3, 15: 3, 16: 4, 17: 6, 18: 3}
)
HOME_TRAFFIC = _hours({7: 3, 8: 7, 9: 9, 10: 9, 11: 7, 12: 5, 13: 4, 14: 4, 15: 5, 16: 4, 17: 3})
CAMPUS_TRAFFIC = _hours({8: 6, 9: 8, 10: 7, 11: 6, 12: 8, 13: 6, 14: 5, 15: 5, 16: 4, 17: 3, 18: 2})

S = Segment
# id, name, x, y, population, income, rent per m2 per month, mix (C, F, S, R, O)
_TOWN = [
    ("market", "Market Square", 0.0, 0.0, 9000, 1.15, 4700, (0.30, 0.10, 0.10, 0.10, 0.40)),
    ("station", "Station Quarter", 0.7, 0.4, 10000, 1.0, 5200, (0.45, 0.15, 0.10, 0.05, 0.25)),
    ("riverside", "Riverside Lofts", -0.8, 0.5, 8000, 1.3, 5600, (0.30, 0.15, 0.10, 0.05, 0.40)),
    ("oakwood", "Oakwood", -1.6, -0.9, 14000, 1.0, 3200, (0.20, 0.50, 0.05, 0.15, 0.10)),
    ("campus", "Campus Heights", 1.9, -0.7, 9000, 0.7, 2800, (0.10, 0.05, 0.65, 0.05, 0.15)),
    ("maple", "Maple Grove", -2.6, 0.9, 7000, 1.1, 3000, (0.10, 0.30, 0.05, 0.50, 0.05)),
    ("eastgate", "Eastgate", 2.8, 1.4, 12000, 0.9, 3400, (0.35, 0.35, 0.10, 0.10, 0.10)),
    ("harbor", "Harbor Flats", 0.3, -2.1, 8000, 0.8, 2600, (0.25, 0.25, 0.20, 0.15, 0.15)),
]
_TRAFFIC = {
    "market": OFFICE_TRAFFIC,
    "station": COMMUTE_TRAFFIC,
    "riverside": COMMUTE_TRAFFIC,
    "campus": CAMPUS_TRAFFIC,
}
_ORDER = (S.COMMUTER, S.FAMILY, S.STUDENT, S.RETIREE, S.OFFICE)

# item id: units per weekday (Mon..Sun) for the starting shop
DEFAULT_BAKE_PLAN: dict[str, tuple[int, ...]] = {
    "sourdough": (21, 20, 23, 24, 24, 17, 11),
    "baguette": (12, 13, 15, 16, 17, 14, 8),
    "croissant": (27, 29, 32, 32, 33, 19, 11),
    "painchoc": (32, 32, 35, 33, 37, 22, 12),
    "cinnamon": (26, 26, 27, 28, 28, 19, 10),
    "danish": (30, 33, 33, 34, 36, 22, 12),
    "muffin": (47, 41, 51, 46, 50, 26, 15),
    "cookie": (41, 40, 46, 48, 50, 30, 14),
    "choccake": (21, 23, 23, 22, 26, 18, 10),
    "quiche": (30, 33, 36, 35, 34, 14, 8),
    "cheesetwist": (27, 29, 31, 32, 32, 15, 7),
}

COMPETITORS = [
    ("crumb", "Crumb & Co", "station", CompetitorState.OPEN, 1.05, (1.0, 0.8, 0.9, 0.6, 1.0)),
    ("megamart", "MegaMart Bakery Aisle", "eastgate", CompetitorState.OPEN, 0.7, (0.5,) * 5),
    (
        "patisserie",
        "Maison Lune",
        "riverside",
        CompetitorState.RUMORED,
        1.4,
        (0.7, 0.4, 0.3, 0.6, 1.1),
    ),
    (
        "grind",
        "Daily Grind Bakehouse",
        "market",
        CompetitorState.RUMORED,
        0.9,
        (1.0, 0.2, 1.2, 0.1, 0.4),
    ),
]
RIVAL_ID = CompetitorId("grind")


def proximity(a: Neighborhood, b: Neighborhood) -> float:
    if a.id == b.id:
        return 1.0
    return 0.8 * math.exp(-math.hypot(a.x_km - b.x_km, a.y_km - b.y_km) / 1.2)


def spawn_customer(
    rng: random.Random,
    seq: int,
    nb: Neighborhood,
    day: int,
    state: CustomerState = CustomerState.NEW,
) -> Customer:
    seg = rng.choices(list(nb.segment_mix), weights=list(nb.segment_mix.values()))[0]
    enrolled = rng.random() < SEGMENT_LOYALTY_ENROLL
    return Customer(
        id=CustomerId(f"c{seq}"),
        neighborhood_id=nb.id,
        segment=seg,
        state=state,
        loyalty_id=f"L{seq:06d}" if enrolled else None,
        base_visit_rate=SEGMENT_VISITS_PER_WEEK[seg] * rng.uniform(0.5, 1.5),
        created_day=day,
        state_since_day=day,
    )


def _staff(rng: random.Random, seq: int, role: Role, name: str, loc: LocationId) -> Staff:
    market = MARKET_WAGE_CENTS[role]
    return Staff(
        id=StaffId(f"s{seq}"),
        name=name,
        role=role,
        state=StaffState.EMPLOYED,
        location_id=loc,
        wage_cents_per_hour=Cents(round(market * rng.uniform(0.97, 1.05))),
        skill=rng.uniform(0.5, 0.8),
        speed=rng.uniform(0.92, 1.08),
        reliability=rng.uniform(0.7, 0.98),
        morale=rng.uniform(0.55, 0.8),
    )


STARTING_STAFF = (
    (Role.BAKER, "Marta"),
    (Role.BAKER, "Idris"),
    (Role.COUNTER, "Joss"),
    (Role.COUNTER, "Priya"),
    (Role.COUNTER, "Theo"),
    (Role.CLEANER, "Bea"),
)
OPENING_STAFF = (
    (Role.BAKER, "Marta"),
    (Role.BAKER, "Idris"),
    (Role.COUNTER, "Joss"),
    (Role.COUNTER, "Priya"),
    (Role.CLEANER, "Bea"),
)
HIRE_NAMES = (
    "Sana",
    "Owen",
    "Lucia",
    "Ravi",
    "Greta",
    "Tomasz",
    "Imani",
    "Felix",
    "Noor",
    "Callum",
    "Yuki",
    "Dara",
    "Mateo",
    "Elsie",
    "Kofi",
    "Hanna",
)

SHOP_NAME = "Larkspur Bakery"
OWNER_ID = StaffId("owner")
OWNER_NAME = "Ada Brennan"
OWNER_SPEED = 1.15
OWNER_WAGE_CENTS = Cents(900)
OPENING_CUSTOMERS = 800
OPENING_AWARENESS = 0.12
OPENING_BAKE_SHARE = 0.35
HOME_LEASE_END_DAY = (date(2026, 8, 1) - date_of(0)).days
CREDIT_LIMIT_CENTS = Cents(8_000_000)

G = Ingredient
SUPPLIERS = (
    Supplier(
        "millbrook", "Millbrook Wholesale", {i: 1.0 for i in (G.FLOUR, G.SUGAR, G.EGGS, G.FRUIT)}
    ),
    Supplier("hartley", "Hartley Dairy", {G.BUTTER: 1.12, G.DAIRY: 1.04}),
    Supplier("greenfield", "Greenfield Creamery", {G.BUTTER: 1.0, G.DAIRY: 1.0}),
    Supplier("beanbarrow", "Bean & Barrow Roasters", {G.COFFEE: 1.0, G.CHOCOLATE: 1.0}),
)
BUY_FROM: dict[Ingredient, str] = {
    G.FLOUR: "millbrook",
    G.SUGAR: "millbrook",
    G.EGGS: "millbrook",
    G.FRUIT: "millbrook",
    G.BUTTER: "greenfield",
    G.DAIRY: "greenfield",
    G.COFFEE: "beanbarrow",
    G.CHOCOLATE: "beanbarrow",
}


def _owner(loc: LocationId) -> Staff:
    return Staff(
        id=OWNER_ID,
        name=OWNER_NAME,
        role=Role.COUNTER,
        state=StaffState.EMPLOYED,
        location_id=loc,
        wage_cents_per_hour=OWNER_WAGE_CENTS,
        skill=0.85,
        speed=OWNER_SPEED,
        reliability=1.0,
        morale=0.85,
        owner=True,
    )


@dataclass(frozen=True)
class Start:
    staff: tuple[tuple[Role, str], ...]
    menu: tuple[ItemId, ...]
    customers: int
    reach_floor: float
    awareness: float
    reputation: float
    close_hour: int
    bake_share: float
    home_traffic: tuple[float, ...]
    cash: Cents
    debt: Cents


ESTABLISHED = Start(
    STARTING_STAFF,
    STARTING_MENU,
    3000,
    0.15,
    0.62,
    0.65,
    18,
    1.0,
    OFFICE_TRAFFIC,
    Cents(4_000_000),
    Cents(2_500_000),
)
OPENING = Start(
    OPENING_STAFF,
    OPENING_MENU,
    OPENING_CUSTOMERS,
    0.0,
    OPENING_AWARENESS,
    0.6,
    15,
    OPENING_BAKE_SHARE,
    MARKET_QUIET_AFTERNOON,
    Cents(10_000_000),
    Cents(4_500_000),
)


def build_world(seed: int, established: bool = False) -> World:
    rng = random.Random(seed)
    town = {
        NeighborhoodId(i): Neighborhood(
            id=NeighborhoodId(i),
            name=name,
            x_km=x,
            y_km=y,
            population=pop,
            income_index=inc,
            rent_cents_per_m2_month=Cents(rent),
            segment_mix=dict(zip(_ORDER, mix, strict=True)),
            hourly_traffic=_TRAFFIC.get(i, HOME_TRAFFIC),
        )
        for i, name, x, y, pop, inc, rent, mix in _TOWN
    }
    start = ESTABLISHED if established else OPENING
    town[HOME_NEIGHBORHOOD] = replace(town[HOME_NEIGHBORHOOD], hourly_traffic=start.home_traffic)
    home = town[HOME_NEIGHBORHOOD]
    params = draw_hidden_params(rng)

    menu = {
        item: MenuEntry(
            MenuState.ON_MENU,
            CATALOG[item].typical_price_cents,
            [round(u * start.bake_share) for u in DEFAULT_BAKE_PLAN.get(item, (0,) * 7)],
        )
        for item in start.menu
    }
    shop = Location(
        id=HOME_LOCATION,
        neighborhood_id=HOME_NEIGHBORHOOD,
        size_m2=90,
        ovens=[Equipment("oven1"), Equipment("oven2", wear=0.15), Equipment("oven3", wear=0.3)],
        state=LocationState.TRADING,
        lease=Lease(
            LeaseState.ACTIVE, Cents(90 * home.rent_cents_per_m2_month), HOME_LEASE_END_DAY
        ),
        menu=menu,
        open_hour=7,
        close_hour=start.close_hour,
        reputation=start.reputation,
        awareness=start.awareness,
    )
    staff = {}
    for seq, (role, name) in enumerate(start.staff):
        member = _staff(rng, seq, role, name, HOME_LOCATION)
        staff[member.id] = member
    if not established:
        staff[OWNER_ID] = _owner(HOME_LOCATION)

    weights = [n.population * (start.reach_floor + proximity(home, n)) for n in town.values()]
    sizes = list(town.values())
    customers = [
        spawn_customer(rng, seq, rng.choices(sizes, weights=weights)[0], 0)
        for seq in range(start.customers)
    ]

    competitors = {
        CompetitorId(i): Competitor(
            CompetitorId(i),
            name,
            NeighborhoodId(nb),
            state,
            price,
            dict(zip(_ORDER, appeal, strict=True)),
        )
        for i, name, nb, state, price, appeal in COMPETITORS
    }
    return World(
        day=0,
        town=town,
        params=params,
        business=Business(
            cash_cents=start.cash,
            debt_cents=start.debt,
            credit_limit_cents=CREDIT_LIMIT_CENTS,
            credit_drawn_cents=Cents(0),
            locations={shop.id: shop},
            staff=staff,
            suppliers={s.id: s for s in SUPPLIERS},
            buy_from=dict(BUY_FROM),
        ),
        customers=customers,
        competitors=competitors,
        weather=SeasonalWeather(seed),
        ingredient_prices=MeanRevertingPrices(seed),
        foot_traffic=SeasonalFootTraffic(),
    )
