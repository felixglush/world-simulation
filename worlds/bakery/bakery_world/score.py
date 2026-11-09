from collections.abc import Callable

from bakery_world.basics import Category, Ingredient, ItemId, LocationId, Role, StaffId
from bakery_world.books import OPERATING
from bakery_world.catalog import CATALOG
from bakery_world.machines import CurveballState, LocationState, StaffState
from bakery_world.managed import ManagedWorld
from bakery_world.town import HOME_LOCATION

GOING_CONCERN_MULTIPLE = 2
GOING_CONCERN_WEEKS = 26
WINDOW_DAYS = 7 * GOING_CONCERN_WEEKS
LIQUIDATION_SHARE = 0.3
WORKING = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)


def operating_profit(mw: ManagedWorld, location: LocationId, days: int = WINDOW_DAYS) -> int:
    cutoff, total = mw.day - days, 0
    for e in reversed(mw.world.business.ledger):
        if e.day < cutoff:
            break
        if e.location_id == location and e.account in OPERATING:
            total += e.amount_cents
    return total


def book_value(mw: ManagedWorld) -> int:
    return sum(a.book_value(mw.day) for a in mw.assets)


def going_concern(mw: ManagedWorld) -> int:
    return sum(
        max(0, round(GOING_CONCERN_MULTIPLE * operating_profit(mw, loc.id) * 365 / WINDOW_DAYS))
        for loc in mw.world.business.locations.values()
        if loc.state is LocationState.TRADING
    )


def net_worth(mw: ManagedWorld, bankrupt: bool = False) -> int:
    b = mw.world.business
    claims = b.cash_cents - b.debt_cents - b.credit_drawn_cents
    if bankrupt:
        return round(claims + LIQUIDATION_SHARE * book_value(mw))
    return claims + book_value(mw) + going_concern(mw)


def _bread_price(mw: ManagedWorld, prices: dict[ItemId, int]) -> float:
    bread = [i for i in prices if CATALOG[i].category is Category.BREAD]
    return sum(prices[i] for i in bread) / max(1, len(bread))


def home_prices(mw: ManagedWorld) -> dict[ItemId, int]:
    home = mw.world.business.locations[HOME_LOCATION]
    return {i: e.price_cents for i, e in home.menu.items() if e.state.name in ("ON_MENU", "TRIAL")}


def _habit_rules() -> dict[str, Callable[[ManagedWorld, dict], bool]]:
    def home(mw: ManagedWorld):
        return mw.world.business.locations[HOME_LOCATION]

    def croissant(mw: ManagedWorld, d: dict) -> bool:
        e = home(mw).menu.get(ItemId("croissant"))
        return e is None or e.price_cents >= 0.95 * CATALOG[ItemId("croissant")].typical_price_cents

    def sourdough(mw: ManagedWorld, d: dict) -> bool:
        e = home(mw).menu.get(ItemId("sourdough"))
        if e is None or e.state.name == "RETIRED":
            return True
        early, mid = sum(e.bake_plan[:2]) / 2, sum(e.bake_plan[2:5]) / 3
        return early <= 1.15 * max(1, mid)

    def close(mw: ManagedWorld, d: dict) -> bool:
        return home(mw).close_hour >= 17

    def baker(mw: ManagedWorld, d: dict) -> bool:
        staff = mw.world.business.staff
        if staff[StaffId(d["staff_id"])].state not in WORKING:
            return True
        others = [
            s
            for s in staff.values()
            if s.role is Role.BAKER
            and s.state in WORKING
            and s.id != d["staff_id"]
            and s.location_id == HOME_LOCATION
        ]
        return len(others) >= 2

    def butter(mw: ManagedWorld, d: dict) -> bool:
        return mw.world.business.buy_from[Ingredient.BUTTER] != d["supplier"]

    def flour(mw: ManagedWorld, d: dict) -> bool:
        before = {i: p for i, p in mw.handoff_prices.items() if i in home_prices(mw)}
        now = {i: p for i, p in home_prices(mw).items() if i in before}
        return _bread_price(mw, now) >= 1.05 * _bread_price(mw, before)

    return {
        "cheap_croissant": croissant,
        "early_week_sourdough": sourdough,
        "early_close": close,
        "slow_loyal_baker": baker,
        "dear_butter": butter,
        "unpriced_flour_spike": flour,
    }


HABIT_RULES = _habit_rules()


def habits_fixed(mw: ManagedWorld) -> dict[str, bool]:
    return {
        hid: HABIT_RULES[hid](mw, h["details"]) for hid, h in sorted(mw.hidden["habits"].items())
    }


def diagnostics(mw: ManagedWorld) -> dict:
    t = mw.totals
    asked = [c for c in mw.curveballs if c.deadline_day is not None]
    handled = [c for c in asked if c.state is CurveballState.RESOLVED]
    fixed = habits_fixed(mw)
    headcount = max(1, mw.handoff_headcount)
    return {
        "waste_pct": round(100 * t["wasted"] / max(1, t["baked"]), 1),
        "stockout_rate": round(t["stockouts"] / max(1, t["item_days"]), 3),
        "walkaways": t["walkaways"],
        "staff_turnover": round(t["leavers"] / headcount, 2),
        "review_stars": round(t["stars"] / t["reviews"], 2) if t["reviews"] else None,
        "habits_fixed": f"{sum(fixed.values())}/{len(fixed)}",
        "habits": fixed,
        "curveballs_handled": f"{len(handled)}/{len(asked)}",
    }
