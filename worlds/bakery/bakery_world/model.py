from collections.abc import Callable
from dataclasses import dataclass, field

from bakery_world.basics import (
    Account,
    Category,
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
)
from bakery_world.exogenous import FootTraffic, IngredientPrices, Weather
from bakery_world.machines import (
    CompetitorState,
    CustomerState,
    EquipmentState,
    LeaseState,
    LocationState,
    MenuState,
    StaffState,
    Transition,
)


@dataclass(frozen=True)
class Neighborhood:
    id: NeighborhoodId
    name: str
    x_km: float
    y_km: float
    population: int
    income_index: float
    rent_cents_per_m2_month: Cents
    segment_mix: dict[Segment, float]
    hourly_traffic: tuple[float, ...]


@dataclass(frozen=True)
class Recipe:
    item_id: ItemId
    name: str
    category: Category
    grams: dict[Ingredient, int]
    labor_minutes: float
    oven_minutes: int
    batch_size: int
    shelf_life_days: int
    typical_price_cents: Cents

    @property
    def made_to_order(self) -> bool:
        return self.batch_size == 0


@dataclass
class MenuEntry:
    state: MenuState
    price_cents: Cents
    bake_plan: list[int]
    discount_pct: int = 0
    discount_until_day: int = 0

    def price_on(self, day: int) -> Cents:
        if day < self.discount_until_day:
            return Cents(round(self.price_cents * (100 - self.discount_pct) / 100))
        return self.price_cents


@dataclass
class Equipment:
    id: str
    state: EquipmentState = EquipmentState.WORKING
    wear: float = 0.0
    repair_done_day: int = 0


@dataclass
class Lease:
    state: LeaseState
    monthly_rent_cents: Cents
    end_day: int


@dataclass
class Location:
    id: LocationId
    neighborhood_id: NeighborhoodId
    size_m2: int
    ovens: list[Equipment]
    state: LocationState
    lease: Lease
    menu: dict[ItemId, MenuEntry]
    open_hour: int
    close_hour: int
    reputation: float
    awareness: float
    stock: dict[ItemId, dict[int, int]] = field(default_factory=dict)
    marketing_cents_per_month: Cents = Cents(0)
    menu_changed_day: int = 0
    campaign_cents_per_month: float = 0.0
    price_index: float = 1.0
    cleanliness: float = 1.0
    stars: float = 3.5


@dataclass(frozen=True)
class ShiftTime:
    start_hour: int
    end_hour: int

    @property
    def hours(self) -> int:
        return self.end_hour - self.start_hour

    def __str__(self) -> str:
        return f"{self.start_hour:02d}-{self.end_hour:02d}"


Rota = tuple[ShiftTime | None, ...]


def rota_hours(rota: Rota) -> int:
    return sum(s.hours for s in rota if s)


@dataclass
class Staff:
    id: StaffId
    name: str
    role: Role
    state: StaffState
    location_id: LocationId
    wage_cents_per_hour: Cents
    skill: float
    speed: float
    reliability: float
    morale: float
    state_until_day: int = 0
    hired_day: int = 0
    owner: bool = False
    rota: Rota = ()
    contract_hours: int = 0


@dataclass
class Customer:
    id: CustomerId
    neighborhood_id: NeighborhoodId
    segment: Segment
    state: CustomerState
    loyalty_id: str | None
    base_visit_rate: float
    created_day: int
    state_since_day: int
    satisfaction: float = 0.7
    last_visit_day: int | None = None
    recent_visits: list[int] = field(default_factory=list)
    fatigue: dict[ItemId, float] = field(default_factory=dict)
    letdowns: float = 0.0


@dataclass
class Competitor:
    id: CompetitorId
    name: str
    neighborhood_id: NeighborhoodId
    state: CompetitorState
    price_index: float
    appeal: dict[Segment, float]


@dataclass(frozen=True)
class HiddenParams:
    elasticity: dict[Segment, float]
    taste: dict[Segment, dict[Category, float]]
    item_appeal: dict[ItemId, float]
    outside_option: float
    walkaway_wait_min: float
    monthly_lapse_rate: float
    new_customer_rate: float
    reputation_drift: float
    wage_morale_sensitivity: float
    quit_hazard: float


@dataclass
class Vacancy:
    role: Role
    location_id: LocationId
    fill_day: int
    rota: Rota = ()


@dataclass(frozen=True)
class LedgerEntry:
    day: int
    account: Account
    amount_cents: int
    location_id: LocationId | None
    memo: str = ""


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    markup: dict[Ingredient, float]


@dataclass
class Business:
    cash_cents: Cents
    debt_cents: Cents
    credit_limit_cents: Cents
    credit_drawn_cents: Cents
    locations: dict[LocationId, Location]
    staff: dict[StaffId, Staff]
    transitions: list[Transition] = field(default_factory=list)
    vacancies: list[Vacancy] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    suppliers: dict[str, Supplier] = field(default_factory=dict)
    buy_from: dict[Ingredient, str] = field(default_factory=dict)
    auto_hire: bool = True


@dataclass
class World:
    day: int
    town: dict[NeighborhoodId, Neighborhood]
    params: HiddenParams
    business: Business
    customers: list[Customer]
    competitors: dict[CompetitorId, Competitor]
    weather: Weather
    ingredient_prices: IngredientPrices
    foot_traffic: FootTraffic
    market_wage_index: float = 1.0
    word_of_mouth: float = 1.0
    menu_boredom: float = 0.0
    segment_shift: dict[tuple[NeighborhoodId, Segment], float] = field(default_factory=dict)
    daily_hooks: list[Callable[["World"], None]] = field(default_factory=list)
    seed: int = 0


MARKET_WAGE_CENTS: dict[Role, Cents] = {
    Role.BAKER: Cents(2200),
    Role.COUNTER: Cents(1700),
    Role.CLEANER: Cents(1500),
}
