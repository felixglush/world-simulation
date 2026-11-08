import math
from dataclasses import dataclass, field
from pathlib import Path

from bakery_world.actions import ActionRefused, minimum_wage, perform
from bakery_world.basics import Category, Ingredient, ItemId, LocationId, Role
from bakery_world.catalog import CATALOG
from bakery_world.curveballs import Curveball, Kind
from bakery_world.engine import PACKAGING_CENTS, market_wage
from bakery_world.history import generate_history
from bakery_world.machines import CustomerState, LocationState, MenuState, StaffState
from bakery_world.managed import ManagedWorld
from bakery_world.management import manage
from bakery_world.model import ShiftTime
from bakery_world.observe import candidates, price_list, sales_by_hour, sites_scouted
from bakery_world.observe import staff as staff_list
from bakery_world.rota import SHIFT_HOURS, standard_shift
from bakery_world.town import HOME_LOCATION, proximity

QUARTER_WEEKS = 13
HOURS_EVERY_WEEKS = 4
FOOD_COST_TARGET = 0.30
MAX_PRICE_MOVE = 0.10
PAR_PERCENTILE, PAR_WEEKS = 80, 4
EXTEND_SHARE, TRIM_SHARE = 0.5, 0.12
HIRE_RETRY_RAISE = 1.08
COUNTER_COVER = 1.0
WEEK_SHIFTS = 5
WORKING = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)


def _try(mw: ManagedWorld, name: str, **args: object) -> object | None:
    try:
        return perform(mw, name, **args)
    except ActionRefused:
        return None


def _trading(mw: ManagedWorld) -> list[LocationId]:
    locs = mw.world.business.locations.values()
    return sorted(loc.id for loc in locs if loc.state is LocationState.TRADING)


def _round25(cents: float) -> int:
    return max(25, round(cents / 25) * 25)


def fit_rotas(mw: ManagedWorld, loc: LocationId) -> None:
    """Standard shifts, with counter starts spread evenly from opening to the last full shift."""
    shop = mw.world.business.locations[loc]
    here = [s for s in staff_list(mw) if s["location"] == loc and not s["owner"]]
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
    posted: dict[tuple[LocationId, Role], int] = field(default_factory=dict)

    def weekly(self, mw: ManagedWorld, week: int) -> None:
        if week == 0:
            self.headcount = self._headcount(mw)
            self.cheapest_suppliers(mw)
        for loc in _trading(mw):
            self.par_rules(mw, loc)
            if week % QUARTER_WEEKS == 0:
                self.reprice(mw, loc)
            if week and week % HOURS_EVERY_WEEKS == 0:
                self.fit_hours(mw, loc)
            fit_rotas(mw, loc)
        self.staff_up(mw)

    def interrupt(self, mw: ManagedWorld, event: Curveball) -> None:
        if event.kind in SENSIBLE:
            _try(mw, "respond_to_event", event=event.id, option=SENSIBLE[event.kind])

    def _headcount(self, mw: ManagedWorld) -> dict[tuple[LocationId, Role], int]:
        left = {
            t.entity_id
            for t in mw.world.business.transitions
            if t.day >= mw.handoff_day and t.after == StaffState.GONE.name
        }
        counts: dict[tuple[LocationId, Role], int] = {}
        for s in mw.world.business.staff.values():
            if s.state in WORKING or s.id in left:
                key = (s.location_id, s.role)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def wanted(self, mw: ManagedWorld, loc: LocationId, role: Role, base: int) -> int:
        if role is not Role.COUNTER:
            return base
        shop = mw.world.business.locations[loc]
        hours = 7 * (shop.close_hour - shop.open_hour) * COUNTER_COVER
        return max(base, math.ceil(hours / (WEEK_SHIFTS * SHIFT_HOURS[role])))

    def cheapest_suppliers(self, mw: ManagedWorld) -> None:
        prices = price_list(mw)
        for ing in Ingredient:
            offers = [(p[ing.value], sid) for sid, p in prices.items() if ing.value in p]
            _try(mw, "choose_supplier", ingredient=ing.value, supplier=min(offers)[1])

    def par_rules(self, mw: ManagedWorld, loc: LocationId) -> None:
        menu = mw.world.business.locations[loc].menu
        for item, e in menu.items():
            baked = not CATALOG[item].made_to_order
            if baked and e.state is MenuState.ON_MENU and (loc, item) not in mw.par_rules:
                _try(
                    mw,
                    "set_par_rule",
                    location=loc,
                    item=item,
                    percentile=PAR_PERCENTILE,
                    lookback_weeks=PAR_WEEKS,
                )

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
        shop = mw.world.business.locations[loc]
        sales = sales_by_hour(mw, loc, 4)
        peak = max(sales) or 1
        open_hour, close_hour = shop.open_hour, shop.close_hour
        if sales[close_hour - 1] >= EXTEND_SHARE * peak and close_hour < 20:
            close_hour += 1
        elif sales[close_hour - 1] < TRIM_SHARE * peak and close_hour - open_hour > 8:
            close_hour -= 1
        if sales[open_hour] >= EXTEND_SHARE * peak and open_hour > 6:
            open_hour -= 1
        elif sales[open_hour] < TRIM_SHARE * peak and close_hour - open_hour > 8:
            open_hour += 1
        if (open_hour, close_hour) != (shop.open_hour, shop.close_hour):
            _try(mw, "set_hours", location=loc, open_hour=open_hour, close_hour=close_hour)

    def going_wage(self, mw: ManagedWorld, role: Role) -> int:
        wages = sorted(
            s["wage_cents_per_hour"] for s in staff_list(mw) if s["role"] == role and not s["owner"]
        )
        going = wages[len(wages) // 2] if wages else 0
        return max(minimum_wage(mw), going)

    def skill(self, mw: ManagedWorld, candidate: dict) -> float:
        return candidate["skill_estimate"]

    def staff_up(self, mw: ManagedWorld) -> None:
        staying = (StaffState.EMPLOYED.value, StaffState.SICK.value)
        for (loc, role), base in sorted(self.headcount.items()):
            want = self.wanted(mw, loc, role, base)
            if mw.world.business.locations[loc].state not in (
                LocationState.TRADING,
                LocationState.FITTING_OUT,
            ):
                continue
            have = sum(
                s["location"] == loc
                and s["role"] == role
                and s["state"] in staying
                and not s["owner"]
                for s in staff_list(mw)
            )
            if have >= want:
                self.posted.pop((loc, role), None)
                continue
            pool = [c for c in candidates(mw) if c["location"] == loc and c["role"] == role]
            for c in pool:
                if c["state"] == StaffState.CANDIDATE.value:
                    _try(mw, "interview", candidate=c["candidate"])
            seen = [
                c
                for c in candidates(mw)
                if c["location"] == loc and c["role"] == role and c["state"] == "interviewed"
            ]
            wage = self.posted.get((loc, role), self.going_wage(mw, role))
            for c in sorted(seen, key=lambda c: -self.skill(mw, c)):
                result = _try(mw, "hire", candidate=c["candidate"], wage_cents_per_hour=wage)
                if result and result.data.get("hired"):
                    have += 1
                    if have >= want:
                        break
            if have < want and not pool:
                wage = round(wage * HIRE_RETRY_RAISE) if (loc, role) in self.posted else wage
                if _try(
                    mw,
                    "post_job",
                    location=loc,
                    role=role.value,
                    wage_cents_per_hour=min(6000, wage),
                ):
                    self.posted[(loc, role)] = min(6000, wage)


MARKET_PREMIUM = 1.05
MENU_REFRESH_WEEKS = 8
HOUR_TRAFFIC_SHARE = 0.3
PRICE_BAND = (0.9, 1.35)
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
            self.drop_slow_baker(mw)
        if week == EXPANSION_WEEK:
            self.consider_expansion(mw)
        for loc in _trading(mw):
            self.par_rules(mw, loc)
            if week == 0:
                self.true_hours(mw, loc)
                self.best_prices(mw, loc)
            if week % MENU_REFRESH_WEEKS == 0:
                self.refresh_menu(mw, loc)
            self.top_up_awareness(mw, loc)
            fit_rotas(mw, loc)
        self.pay_market(mw)
        self.staff_up(mw)

    def drop_slow_baker(self, mw: ManagedWorld) -> None:
        habit = mw.hidden["habits"].get("slow_loyal_baker")
        if habit:
            _try(mw, "fire", staff=habit["details"]["staff_id"])

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
        if mw.world.menu_boredom <= 0:
            return
        shop = mw.world.business.locations[loc]
        appeal = mw.world.params.item_appeal
        on = {i for i, e in shop.menu.items() if e.state in (MenuState.ON_MENU, MenuState.TRIAL)}
        fresh = [i for i in CATALOG if i not in on and CATALOG[i].category is not Category.DRINK]
        if fresh:
            best = max(fresh, key=lambda i: (appeal[i], i))
            _try(mw, "add_menu_item", location=loc, item=best)

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
