import math
import random

from bakery_world.basics import (
    Cents,
    CompetitorId,
    CustomerId,
    LocationId,
    NeighborhoodId,
    Role,
    Segment,
    StaffId,
)
from bakery_world.catalog import CATALOG, STARTING_MENU
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
COMMUTE_TRAFFIC = _hours(
    {6: 3, 7: 8, 8: 10, 9: 6, 10: 4, 11: 4, 12: 5, 13: 4, 14: 3, 15: 3, 16: 4, 17: 6, 18: 3}
)
HOME_TRAFFIC = _hours({7: 3, 8: 7, 9: 9, 10: 9, 11: 7, 12: 5, 13: 4, 14: 4, 15: 5, 16: 4, 17: 3})
CAMPUS_TRAFFIC = _hours({8: 6, 9: 8, 10: 7, 11: 6, 12: 8, 13: 6, 14: 5, 15: 5, 16: 4, 17: 3, 18: 2})

S = Segment
# id, name, x, y, population, income, rent per m2 per month, mix (C, F, S, R, O)
_TOWN = [
    ("market", "Market Square", 0.0, 0.0, 9000, 1.15, 9500, (0.30, 0.10, 0.10, 0.10, 0.40)),
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
]


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
        skill=rng.uniform(0.4, 0.9),
        speed=rng.uniform(0.85, 1.15),
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


def build_world(seed: int) -> World:
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
    home = town[HOME_NEIGHBORHOOD]
    params = draw_hidden_params(rng)

    menu = {
        item: MenuEntry(
            MenuState.ON_MENU,
            CATALOG[item].typical_price_cents,
            list(DEFAULT_BAKE_PLAN.get(item, (0,) * 7)),
        )
        for item in STARTING_MENU
    }
    shop = Location(
        id=HOME_LOCATION,
        neighborhood_id=HOME_NEIGHBORHOOD,
        size_m2=90,
        ovens=[Equipment("oven1"), Equipment("oven2", wear=0.15), Equipment("oven3", wear=0.3)],
        state=LocationState.TRADING,
        lease=Lease(LeaseState.ACTIVE, Cents(90 * home.rent_cents_per_m2_month), 5 * 365),
        menu=menu,
        open_hour=7,
        close_hour=18,
        reputation=0.65,
        awareness=0.62,
    )
    staff = {}
    for seq, (role, name) in enumerate(STARTING_STAFF):
        member = _staff(rng, seq, role, name, HOME_LOCATION)
        staff[member.id] = member

    weights = [n.population * (0.15 + proximity(home, n)) for n in town.values()]
    sizes = list(town.values())
    customers = [
        spawn_customer(rng, seq, rng.choices(sizes, weights=weights)[0], 0) for seq in range(3000)
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
            cash_cents=Cents(4_000_000),
            debt_cents=Cents(2_500_000),
            credit_limit_cents=Cents(6_000_000),
            credit_drawn_cents=Cents(0),
            locations={shop.id: shop},
            staff=staff,
        ),
        customers=customers,
        competitors=competitors,
        weather=SeasonalWeather(seed),
        ingredient_prices=MeanRevertingPrices(seed),
        foot_traffic=SeasonalFootTraffic(),
    )
