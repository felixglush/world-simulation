from bakery_world.actions import site_facts
from bakery_world.basics import LocationId
from bakery_world.catalog import CATALOG
from bakery_world.machines import LocationState, StaffState
from bakery_world.managed import ManagedWorld
from bakery_world.model import rota_hours
from bakery_world.rota import describe

WEEK = 7


def _shop_week(mw: ManagedWorld, loc: LocationId) -> dict:
    days = [s for r in list(mw.reports)[-WEEK:] for s in r.locations if s.location_id == loc]
    items = [i for d in days for i in d.items.values()]
    revenue = sum(d.revenue_cents for d in days)
    labor = sum(d.wages_cents + d.payroll_oncost_cents for d in days)
    stars = [r for day, where, r in mw.reviews if where == loc and day >= mw.day - 28]
    return {
        "revenue_cents": revenue,
        "units_sold": sum(i.sold + i.day_old_sold for i in items),
        "units_wasted": sum(i.wasted for i in items),
        "stockouts": sum(i.first_stockout_hour is not None for i in items),
        "walkaways": sum(d.walkaways for d in days),
        "labor_pct": round(100 * labor / revenue, 1) if revenue else None,
        "review_stars_28d": round(sum(stars) / len(stars), 2) if stars else None,
    }


def dashboard(mw: ManagedWorld) -> dict:
    b = mw.world.business
    return {
        "date": mw.date(),
        "day": mw.day,
        "locations": {
            loc.id: {"state": loc.state.value} | _shop_week(mw, loc.id)
            for loc in sorted(b.locations.values(), key=lambda x: x.id)
            if loc.state is not LocationState.CLOSED
        },
        "cash_cents": b.cash_cents,
        "debt_cents": b.debt_cents,
        "credit_drawn_cents": b.credit_drawn_cents,
        "credit_limit_cents": b.credit_limit_cents,
        "manager_hours_left": mw.manager_hours,
    }


def open_events(mw: ManagedWorld) -> list[dict]:
    return [
        {
            "event": cb.id,
            "kind": cb.kind.value,
            "state": cb.state.value,
            "summary": cb.summary,
            "options": dict(cb.spec.options) if cb.deadline_day else {},
            "respond_by": cb.deadline_day,
        }
        for cb in mw.open_curveballs()
    ]


def candidates(mw: ManagedWorld) -> list[dict]:
    return [
        {
            "candidate": c.id,
            "name": c.staff.name,
            "role": c.staff.role.value,
            "location": c.staff.location_id,
            "state": c.staff.state.value,
            "skill_estimate": c.skill_estimate,
            "posted_wage_cents": c.staff.wage_cents_per_hour,
            "available_until_day": c.expires_day,
        }
        for c in sorted(mw.candidates.values(), key=lambda c: c.id)
        if c.arrives_day <= mw.day and c.staff.state is not StaffState.GONE
    ]


def sites_scouted(mw: ManagedWorld) -> list[dict]:
    return [site_facts(s) for s in mw.sites.values()]


def menu(mw: ManagedWorld, location: LocationId) -> dict[str, dict]:
    loc = mw.world.business.locations[location]
    return {
        item: {
            "state": e.state.value,
            "price_cents": e.price_cents,
            "bake_plan": list(e.bake_plan),
            "made_to_order": CATALOG[item].made_to_order,
        }
        for item, e in loc.menu.items()
    }


def staff(mw: ManagedWorld) -> list[dict]:
    working = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)
    return [
        {
            "staff": s.id,
            "name": s.name,
            "role": s.role.value,
            "location": s.location_id,
            "state": s.state.value,
            "wage_cents_per_hour": s.wage_cents_per_hour,
            "hired_day": s.hired_day,
            "owner": s.owner,
            "rota": describe(s.rota),
            "weekly_hours": rota_hours(s.rota),
        }
        for s in sorted(mw.world.business.staff.values(), key=lambda s: s.id)
        if s.state in working
    ]


def price_list(mw: ManagedWorld) -> dict[str, dict[str, int]]:
    market = mw.world.ingredient_prices.on(mw.day)
    return {
        s.id: {ing.value: round(market[ing] * m) for ing, m in s.markup.items()}
        for s in mw.world.business.suppliers.values()
    }


def sales_by_hour(mw: ManagedWorld, location: LocationId, weeks: int = 4) -> list[int]:
    hours = [0] * 24
    for r in list(mw.reports)[-weeks * WEEK :]:
        for shop in r.locations:
            if shop.location_id == location:
                for sale in shop.sales:
                    hours[sale.hour] += sum(line.price_cents for line in sale.lines)
    return hours
