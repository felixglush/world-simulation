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
    "counter_never_unstaffed": Mutant(
        ("S3",),
        E,
        "COUNTER_CUSTOMERS_PER_HOUR * sum(_effort(on.staff) for on in counters if on.covers(h))",
        "COUNTER_CUSTOMERS_PER_HOUR * max(1.0, sum(_effort(on.staff) for on in "
        "counters if on.covers(h)))",
        note="magnitude moves: a phantom worker covers empty hours",
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
        R,
        "drag = (0.8 if busy else 0) + DIRTY_STARS * grime",
        "drag = (0.8 if busy else 0) - DIRTY_STARS * grime",
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
    "quits_x4": Mutant(
        ("W2",),
        E,
        "params.quit_hazard * 4 * (1 - s.morale) ** 2",
        "params.quit_hazard * 16 * (1 - s.morale) ** 2",
        note="magnitude moves: food-service turnover",
    ),
    "quits_ignore_morale": Mutant(
        ("W3",),
        E,
        "params.quit_hazard * 4 * (1 - s.morale) ** 2",
        "params.quit_hazard * 4 * (1 - 0.6) ** 2",
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
    "strong_seasons": Mutant(
        ("E2",),
        X,
        "SEASONAL_AMPLITUDE = 0.015",
        "SEASONAL_AMPLITUDE = 0.08",
        note="magnitude moves: an 8% seasonal swing in foot traffic",
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
    "empty_shelf_defects": Mutant(
        ("BK1",),
        E,
        "        if not lines:\n            target = min(target, 0.3)\n",
        "        if not lines:\n            target = min(target, 0.3)\n"
        "            if misses:\n                c.base_visit_rate *= 0.7\n",
        note="what-if: a customer who leaves empty-handed after a stockout comes 30% less often",
    ),
    "overwork_hurts": Mutant(
        ("BK2",),
        E,
        "max(0.0, cut - HOURS_CUT_UNNOTICED)",
        "max(0.0, abs(cut) - 0.1)",
        note="what-if: hours far from contract either way lower morale",
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
