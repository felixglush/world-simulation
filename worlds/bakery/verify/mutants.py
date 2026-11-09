"""Broken variants of the real bakery code, one per check, to show each check can fail.

A source mutant replaces one exact snippet of a bakery_world module's source (it must
occur exactly once) and re-executes the module in its own namespace, so every function
that looks the name up at call time sees the broken version. Nothing is written to
disk. Run one with `causal.py --mutant NAME`, or all with `mutants.py`, which runs each
in a fresh process and checks that its target check changes verdict.
"""

import importlib
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Mutant:
    targets: tuple[str, ...]
    module: str = ""
    old: str = ""
    new: str = ""
    patch: Callable[[], None] | None = None
    note: str = ""
    more: tuple[tuple[str, str], ...] = ()  # further (old, new) edits in the same module


def _gone_can_rejoin() -> None:
    from bakery_world import machines as mc

    T, TE = mc.StaffState, mc.StaffEvent
    mc.STAFF.table[(T.GONE, TE.ACCEPT)] = T.EMPLOYED  # type: ignore[index]


E, R, S, X = (
    "bakery_world.engine",
    "bakery_world.records",
    "bakery_world.score",
    "bakery_world.exogenous",
)

MUTANTS: dict[str, Mutant] = {
    "price_utility_sign": Mutant(
        ("P1",),
        E,
        "math.exp(BETA * (t - elas * o.price / o.typical))",
        "math.exp(BETA * (t + elas * o.price / o.typical))",
        note="dearer items are more wanted",
    ),
    "price_elasticity_x3": Mutant(
        ("P1",),
        E,
        "elas = params.elasticity[c.segment] / nb.income_index",
        "elas = 3 * params.elasticity[c.segment] / nb.income_index",
        note="magnitude moves: three times as price-sensitive buyers",
    ),
    "revenue_ignores_price": Mutant(
        ("P2",),
        E,
        "revenue += o.price",
        "revenue += o.typical",
        note="revenue at list price",
    ),
    "own_price_ignored": Mutant(
        ("P3",),
        E,
        "math.exp(BETA * (t - elas * o.price / o.typical))",
        "math.exp(BETA * (t - elas))",
        note="an item's price no longer moves its own demand",
    ),
    "no_waste": Mutant(
        ("Q1", "Q2"),
        E,
        "if age + 1 >= shelf:",
        "if False:",
        note="stock never goes stale",
    ),
    "wait_falls_with_load": Mutant(
        ("S1",),
        E,
        "return unit * rho / (1 - rho)",
        "return unit * (1 - rho)",
        note="a fuller queue is a shorter wait",
    ),
    "walkaway_ignores_wait": Mutant(
        ("S2",),
        E,
        "if rng.random() < _walkaway_probability(wait, params.walkaway_wait_min):",
        "if rng.random() < 0.0:",
        note="nobody ever walks away",
    ),
    "nobody_walks_away": Mutant(
        ("S3",),
        E,
        "    if wait == math.inf:\n        return 1.0\n"
        "    return min(0.95, max(0.0, (wait - 0.5 * threshold) / (2 * threshold)))",
        "    return 0.0",
        note="magnitude moves: nobody ever walks away",
    ),
    "no_cleaning": Mutant(
        ("K1", "K2"),
        E,
        "CLEAN_PER_HOUR * cleaning",
        "0.0 * cleaning",
        note="cleaner hours clean nothing",
    ),
    "dirt_pleases": Mutant(
        ("K3",),
        E,
        "target -= DIRTY_SATISFACTION * grime + STRAIN_SATISFACTION * strain",
        "target += DIRTY_SATISFACTION * grime - STRAIN_SATISFACTION * strain",
        note="a dirty shop raises satisfaction",
    ),
    "dirty_stars_flip": Mutant(
        ("K2",),
        E,
        "+ DIRTY_STARS * (1 - loc.cleanliness)",
        "- DIRTY_STARS * (1 - loc.cleanliness)",
        note="dirt earns stars",
    ),
    "oven_first_come": Mutant(
        ("B1",),
        E,
        "cut = max(load, oven_need / oven_room if oven_room > 0 else math.inf)",
        "cut = load",
        note="oven time goes to items in menu order, so the last can get none",
    ),
    "strain_improves": Mutant(
        ("B2",),
        E,
        "rushed = max(MIN_RUSHED_QUALITY, 1 - STRAIN_QUALITY * strain)",
        "rushed = max(MIN_RUSHED_QUALITY, 1 + STRAIN_QUALITY * strain)",
        note="rushing improves the bake",
    ),
    "labor_unlimited": Mutant(
        ("B3",),
        E,
        "labor_left = sum(\n        on.shift.hours * 60",
        "labor_left = 1e9 + 0 * sum(\n        on.shift.hours * 60",
        note="baker hours do not limit the bake",
    ),
    "satisfaction_repels": Mutant(
        ("R1",),
        E,
        "* (0.5 + 0.7 * c.satisfaction)",
        "* (1.2 - 0.7 * c.satisfaction)",
        note="happy customers come less",
    ),
    "cut_hours_pleases": Mutant(
        ("H1",),
        E,
        "- HOURS_CUT_MORALE * max(0.0, cut - HOURS_CUT_UNNOTICED)",
        "+ HOURS_CUT_MORALE * max(0.0, cut - HOURS_CUT_UNNOTICED)",
        note="hours cuts raise morale",
    ),
    "low_pay_pleases": Mutant(
        ("W1",),
        E,
        "+ params.wage_morale_sensitivity * wage_gap",
        "- params.wage_morale_sensitivity * wage_gap",
        note="pay cuts raise morale",
    ),
    "quits_x2": Mutant(
        ("W2",),
        E,
        "params.quit_hazard * (QUIT_FLOOR + QUIT_MORALE * (1 - s.morale) ** 2)",
        "params.quit_hazard * 2 * (QUIT_FLOOR + QUIT_MORALE * (1 - s.morale) ** 2)",
        note="magnitude moves: twice the turnover",
    ),
    "quits_ignore_morale": Mutant(
        ("W3",),
        E,
        "params.quit_hazard * (QUIT_FLOOR + QUIT_MORALE * (1 - s.morale) ** 2)",
        "params.quit_hazard * (QUIT_FLOOR + QUIT_MORALE * (1 - 0.6) ** 2)",
        note="pay no longer moves quits",
    ),
    "serve_after_close": Mutant(
        ("O1",),
        E,
        "for h, c in visitors if h in open_hours",
        "for h, c in visitors if True",
        note="the shop serves customers who arrive after it closes",
    ),
    "flour_cost_cut": Mutant(
        ("I1",),
        E,
        "return sum(round(g * prices[ing] / 1000) for ing, g in grams.items())",
        "return sum(round(g * prices[ing] / 1100) for ing, g in grams.items())",
        note="ingredients billed 9% under grams x price",
    ),
    "food_cost_x2": Mutant(
        ("I2",),
        E,
        "return sum(round(g * prices[ing] / 1000) for ing, g in grams.items())",
        "return sum(round(g * prices[ing] / 400) for ing, g in grams.items())",
        note="magnitude moves: ingredients 2.5x dearer",
    ),
    "wages_x07": Mutant(
        ("L1",),
        E,
        "Cents(on.staff.wage_cents_per_hour * on.shift.hours)",
        "Cents(round(0.7 * on.staff.wage_cents_per_hour * on.shift.hours))",
        note="magnitude moves: 30% cheaper labor",
    ),
    "marketing_hurts": Mutant(
        ("M1",),
        E,
        "+ AWARENESS_FROM_MARKETING * advertising",
        "- AWARENESS_FROM_MARKETING * advertising",
        note="advertising lowers awareness",
    ),
    "rival_helps": Mutant(
        ("C1",),
        E,
        "/ (1 + 0.45 * pull[seg])",
        "* (1 + 0.45 * pull[seg])",
        note="a rival opening brings visits",
    ),
    "rain_helps": Mutant(
        ("E1",),
        E,
        "(0.97 if seg is Segment.COMMUTER else 0.94) if rain else 1.0",
        "(1.03 if seg is Segment.COMMUTER else 1.06) if rain else 1.0",
        note="rain brings visits",
    ),
    "weak_seasons": Mutant(
        ("E2",),
        X,
        "MONTH_TRAFFIC = (0.91, 0.95, 0.99, 1.01, 1.02, 1.0, 0.96, 0.95, 1.0, 1.02, 1.04, 1.12)",
        "MONTH_TRAFFIC = (0.98, 0.99, 1.0, 1.0, 1.01, 1.0, 0.99, 0.98, 1.0, 1.0, 1.01, 1.02)",
        note="magnitude moves: a 4% seasonal swing in foot traffic",
    ),
    "cash_leak": Mutant(
        ("A1",),
        E,
        "business.cash_cents = Cents(business.cash_cents + amount_cents)",
        "business.cash_cents = Cents(business.cash_cents + amount_cents + (amount_cents < 0))",
        note="each payment leaks a cent of cash",
    ),
    "card_fee_unposted": Mutant(
        ("A1",),
        E,
        "        Account.CARD_FEES: -fees,\n",
        "",
        note="card fees reported but never posted",
    ),
    "score_cash_flip": Mutant(
        ("A2",),
        S,
        "claims = b.cash_cents - b.debt_cents - b.credit_drawn_cents",
        "claims = -b.cash_cents - b.debt_cents - b.credit_drawn_cents",
        note="more cash lowers net worth",
    ),
    "oncost_10pct": Mutant(
        ("A3",),
        E,
        "oncost = round(wages * PAYROLL_ONCOST_RATE)",
        "oncost = round(wages * 0.1)",
        note="on-costs charged at 10%",
    ),
    "gone_can_rejoin": Mutant(("SM1",), patch=_gone_can_rejoin, note="GONE gets an exit"),
    "gone_paid": Mutant(
        ("SM2",),
        E,
        "working = (StaffState.EMPLOYED, StaffState.ON_NOTICE)",
        "working = (StaffState.EMPLOYED, StaffState.ON_NOTICE, StaffState.GONE)",
        note="leavers stay on the payroll",
    ),
    "no_notice": Mutant(
        ("SM3",),
        E,
        "s.state_until_day = day + NOTICE_DAYS",
        "s.state_until_day = day",
        note="quitters leave the next day",
    ),
    "trade_when_closed": Mutant(
        ("SM4",),
        E,
        "if loc.state is LocationState.TRADING\n    )",
        "if loc.state is not LocationState.CLOSED\n    )",
        more=(
            (
                "if other.state is LocationState.TRADING",
                "if other.state is not LocationState.CLOSED",
            ),
        ),
        note="a scouted or leased shop trades",
    ),
    "bakers_not_needed": Mutant(
        ("BK1", "MX4"),
        E,
        "    labor_left = sum(\n"
        "        on.shift.hours * 60 * BAKER_HANDS_ON * _effort(on.staff) for on in roster.bakers\n"
        "    )\n",
        "    labor_left = 6000.0\n",
        note="the bake gets done whoever is in, so a baker is pure cost",
    ),
    "overtime_free": Mutant(
        ("BK2",),
        E,
        "- OVERTIME_MORALE * max(0.0, -cut)",
        "- OVERTIME_MORALE * 0.0",
        note="hours over contract cost no morale",
    ),
    "leave_halved": Mutant(
        ("AB1",),
        "bakery_world.rota",
        "LEAVE_EVERY_DAYS = 13",
        "LEAVE_EVERY_DAYS = 26",
        note="magnitude moves: two weeks' leave a year",
    ),
    "sick_rare": Mutant(
        ("AB1",),
        E,
        "SICK_RATE = 0.022",
        "SICK_RATE = 0.006",
        note="magnitude moves: sickness a third as common",
    ),
    "drinks_ignore_weather": Mutant(
        ("E3",),
        E,
        "DRINK_WARMTH_PER_DEGREE = 0.012",
        "DRINK_WARMTH_PER_DEGREE = -0.012",
        note="hot drinks sell better on hot days",
    ),
    "stars_repel": Mutant(
        ("ST1",),
        E,
        "max(0.5, 1 + STARS_PULL * (loc.stars - NEUTRAL_STARS))",
        "max(0.5, 1 - STARS_PULL * (loc.stars - NEUTRAL_STARS))",
        note="good reviews keep newcomers away",
    ),
    "hours_ignored": Mutant(
        ("FX6", "MX5"),
        "bakery_world.actions",
        "    loc.open_hour, loc.close_hour = open_hour, close_hour\n",
        "    pass\n",
        note="set_hours changes nothing, so the hours fixes and cuts do nothing",
    ),
    "habit_fixes_ignored": Mutant(
        ("FX1",),
        "bakery_world.actions",
        "    loc.open_hour, loc.close_hour = open_hour, close_hour\n",
        "    pass\n",
        more=(
            (
                "    old, entry.price_cents = entry.price_cents, Cents(price_cents)\n",
                "    old = entry.price_cents\n",
            ),
            (
                "    old, b.buy_from[ing] = b.buy_from[ing], supplier\n",
                "    old = b.buy_from[ing]\n",
            ),
            ("    entry.bake_plan = list(weekday_units)\n", "    pass\n"),
        ),
        note="hours, prices, suppliers and bake plans cannot be changed, so no habit is fixed",
    ),
    "firing_ignored": Mutant(
        ("MX2", "MX3", "MX4"),
        "bakery_world.actions",
        '    let_go(mw, s, "dismissal, two weeks\' pay")\n',
        "    pass\n",
        note="a fired worker stays on",
    ),
    "counter_never_queues": Mutant(
        ("FX2", "MX3"),
        E,
        "COUNTER_CUSTOMERS_PER_HOUR * _counter_staff(roster, h)",
        "COUNTER_CUSTOMERS_PER_HOUR * 9",
        note="the counter serves everyone however many are on it",
    ),
    "dirt_harmless": Mutant(
        ("FX3", "MX2"),
        E,
        "DIRT_PER_VISIT = 0.0005",
        "DIRT_PER_VISIT = 0.0",
        note="the shop never gets dirty, so cleaning buys nothing",
    ),
    "new_items_ignored": Mutant(
        ("FX4",),
        "bakery_world.actions",
        "    loc.menu[key] = entry\n    loc.menu_changed_day = mw.day\n"
        "    mw.trials[(loc.id, key)] = mw.day + TRIAL_DAYS\n",
        "    pass\n",
        note="add_menu_item puts nothing new on sale",
    ),
    "flyers_unseen": Mutant(
        ("FX5",),
        "bakery_world.actions",
        "amount_cents * CHANNELS[ch].efficiency",
        "0.0 * CHANNELS[ch].efficiency",
        note="marketing spend reaches nobody",
    ),
    "price_blind": Mutant(
        ("MX1",),
        E,
        "elas = params.elasticity[c.segment] / nb.income_index",
        "elas = 0.0",
        more=(
            ("PRICE_VISIT_SENSITIVITY = 1.3", "PRICE_VISIT_SENSITIVITY = 0.0"),
            ("PRICE_SATISFACTION = 0.6", "PRICE_SATISFACTION = 0.0"),
        ),
        note="customers ignore prices entirely",
    ),
    "bake_plan_ignored": Mutant(
        ("MX6", "MX7"),
        "bakery_world.actions",
        "    entry.bake_plan = list(weekday_units)\n",
        "    pass\n",
        note="set_bake_plan changes nothing",
    ),
    "pay_irrelevant": Mutant(
        ("MX8",),
        E,
        "+ params.wage_morale_sensitivity * wage_gap",
        "+ 0.0 * wage_gap",
        note="pay below market costs no morale",
    ),
    "menu_cut_ignored": Mutant(
        ("MX9",),
        "bakery_world.actions",
        "    entry.state = _step(mw, mc.MENU_ITEM, item, entry.state, event)\n",
        "    pass\n",
        note="remove_menu_item leaves the item on sale",
    ),
}


def apply(name: str) -> None:
    m = MUTANTS[name]
    if m.patch:
        m.patch()
        return
    module = importlib.import_module(m.module)
    source = Path(module.__file__).read_text()
    for old, new in ((m.old, m.new), *m.more):
        if source.count(old) != 1:
            raise SystemExit(
                f"mutant {name}: snippet found {source.count(old)} times in {m.module}"
            )
        source = source.replace(old, new)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    if m.module == "bakery_world.catalog":
        # recipes are frozen into CATALOG dicts imported by value elsewhere
        for mod in ("bakery_world.engine", "bakery_world.actions", "bakery_world.founder"):
            importlib.import_module(mod).CATALOG.update(module.CATALOG)


def main() -> int:
    here = Path(__file__).parent
    names = sys.argv[1:] or list(MUTANTS)
    missed = []
    for name in names:
        m = MUTANTS[name]
        t = time.perf_counter()
        cmd = [
            sys.executable,
            str(here / "causal.py"),
            "--mutant",
            name,
            "--only",
            ",".join(m.targets),
        ]
        out = subprocess.run(cmd, capture_output=True, text=True)
        moved = [ln.split()[0] for ln in out.stdout.splitlines() if "<-- CHANGED" in ln]
        caught = out.returncode == 1 and set(moved) & set(m.targets)
        if not caught:
            missed.append(name)
        print(
            f"{'caught' if caught else 'MISSED':<7} {name:<26} {','.join(m.targets):<8} "
            f"moved {','.join(moved) or '-':<8} {time.perf_counter() - t:5.1f}s  {m.note}"
        )
        if out.returncode not in (0, 1):
            print(out.stderr[-2000:])
    print(
        f"\n{len(names) - len(missed)}/{len(names)} mutants caught"
        + (f"; missed: {', '.join(missed)}" if missed else "")
    )
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
