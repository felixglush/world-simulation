"""Cause-and-effect checks for the bakery engine.

Each check states one claim a bakery owner would agree with, runs the real engine
functions (paired runs with common random numbers, or generated inputs on pure
helpers, or exhaustive enumeration of the state machines) and returns a verdict:

    holds      the sign (and the magnitude, where the claim gives a range) is right
    fails      the sign is wrong, an identity is broken, or a state rule is broken
    magnitude  the sign is right but the measured size is outside the claim's range

Run from the repository root:

    PYTHONPATH=worlds/bakery uv run --frozen python worlds/bakery/verify/causal.py

The command exits 1 when any verdict differs from KNOWN below, so a new break and a
fixed finding both show up. See .claude/skills/bakery-causality/SKILL.md.
"""

import argparse
import math
import os
import pickle
import random
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from itertools import product
from pathlib import Path

from bakery_world import engine as en
from bakery_world import exogenous as ex
from bakery_world import machines as mc
from bakery_world import records as rec
from bakery_world import rota
from bakery_world.basics import Account, Ingredient, ItemId, Role
from bakery_world.catalog import CATALOG
from bakery_world.machines import CompetitorState, LocationState, StaffState
from bakery_world.model import ShiftTime, Staff, World
from bakery_world.town import HOME_LOCATION, RIVAL_ID, build_world

REPO = Path(__file__).resolve().parents[3]
CACHE = REPO / "runs" / "bakery" / "verify"

HOLDS, FAILS, MAGNITUDE = "holds", "fails", "magnitude"

# Verdicts at the sync commit. A finding stays here as a finding; when the engine
# changes, the run says which verdicts moved.
KNOWN: dict[str, str] = {
    "P1": MAGNITUDE,
    "P2": HOLDS,
    "P3": HOLDS,
    "Q1": HOLDS,
    "Q2": HOLDS,
    "S1": HOLDS,
    "S2": HOLDS,
    "S3": MAGNITUDE,
    "K1": HOLDS,
    "K2": HOLDS,
    "K3": HOLDS,
    "B1": HOLDS,
    "B2": HOLDS,
    "B3": HOLDS,
    "R1": HOLDS,
    "H1": HOLDS,
    "W1": HOLDS,
    "W2": HOLDS,
    "W3": HOLDS,
    "O1": HOLDS,
    "I1": HOLDS,
    "I2": HOLDS,
    "L1": MAGNITUDE,
    "M1": HOLDS,
    "C1": HOLDS,
    "E1": HOLDS,
    "E2": MAGNITUDE,
    "A1": HOLDS,
    "A2": HOLDS,
    "A3": HOLDS,
    "SM1": HOLDS,
    "SM2": HOLDS,
    "SM3": HOLDS,
    "SM4": HOLDS,
    "BK1": FAILS,
    "BK2": HOLDS,
    "AB1": HOLDS,
}


@dataclass
class Result:
    id: str
    claim: str
    verdict: str
    measured: str
    expected: str
    where: str
    detail: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Config:
    seeds: tuple[int, ...]
    days: int
    draws: int


CHECKS: dict[str, Callable[[Config], Result]] = {}


def check(fn: Callable[[Config], Result]) -> Callable[[Config], Result]:
    CHECKS[fn.__name__.split("_")[1].upper()] = fn
    return fn


# ---------------------------------------------------------------- engine harness


def idle(world: World, day: int) -> None:
    return None


def fresh(seed: int) -> World:
    return build_world(seed, established=True)


def shop(world: World):
    return world.business.locations[HOME_LOCATION]


def run(world: World, days: int, seed: int, each: Callable | None = None) -> list[en.DayReport]:
    """Run the real engine; the day rng is the same for both arms of a pair."""
    rng, out = random.Random(seed + 100), []
    for _ in range(days):
        report = en.simulate_day(world, rng)
        out.append(report)
        if each:
            each(world, report)
    return out


def home_days(reports: list[en.DayReport]) -> list[en.LocationDay]:
    return [d for r in reports for d in r.locations if d.location_id == HOME_LOCATION]


def units(days: list[en.LocationDay]) -> int:
    return sum(len(s.lines) for d in days for s in d.sales)


def revenue(days: list[en.LocationDay]) -> int:
    return sum(d.revenue_cents for d in days)


def visits(days: list[en.LocationDay]) -> int:
    return sum(d.visits for d in days)


_PAIRED: dict[tuple, list] = {}


def paired(
    cfg: Config, tweak: Callable[[World], None], days: int | None = None, base=None, key=None
):
    """For each seed, run the same world twice: as is (or with `base`) and with `tweak`."""
    days = days or cfg.days
    if key and (key, days, cfg.seeds) in _PAIRED:
        return _PAIRED[(key, days, cfg.seeds)]
    out = []
    for seed in cfg.seeds:
        a, b = fresh(seed), fresh(seed)
        if base:
            base(a)
            base(b)
        tweak(b)
        out.append((a, home_days(run(a, days, seed)), b, home_days(run(b, days, seed))))
    if key:
        _PAIRED[(key, days, cfg.seeds)] = out
    return out


def pct(x: float) -> str:
    return f"{100 * x:+.1f}%"


def share_true(flags: list[bool]) -> float:
    return sum(flags) / max(1, len(flags))


def staff_of(world: World, role: Role) -> list[Staff]:
    return sorted(
        (s for s in world.business.staff.values() if s.role is role and s.state.name != "GONE"),
        key=lambda s: s.id,
    )


def scale_prices(factor: float, items: tuple[str, ...] | None = None):
    def tweak(world: World) -> None:
        for item, entry in shop(world).menu.items():
            if items is None or item in items:
                entry.price_cents = round(entry.price_cents * factor)

    return tweak


def arc(new: float, old: float) -> float:
    return (new - old) / ((new + old) / 2)


# ---------------------------------------------------------------- price


@check
def check_p1(cfg: Config) -> Result:
    """All prices +10% for 56 days: units fall, elasticity -0.5 to -2."""
    pairs = paired(cfg, scale_prices(1.1), days=56, key="prices x1.1")
    es = [arc(units(db), units(da)) / arc(1.1, 1.0) for _, da, _, db in pairs]
    e = sum(es) / len(es)
    sign_ok = all(x < 0 for x in es)
    verdict = FAILS if not sign_ok else HOLDS if -2.0 <= e <= -0.5 else MAGNITUDE
    return Result(
        "P1",
        "Raising every price 10% lowers units sold; own-price elasticity -0.5 to -2",
        verdict,
        f"elasticity {e:.2f} (per seed {min(es):.2f}..{max(es):.2f})",
        "< 0, in [-2.0, -0.5]",
        "engine._run_location weights (BETA, elas*price/typical); engine._visitors price_f",
    )


@check
def check_p2(cfg: Config) -> Result:
    """Revenue moves as (1 + e): up when demand is inelastic, by less than the price rise."""
    pairs = paired(cfg, scale_prices(1.1), days=56, key="prices x1.1")
    dq = [units(db) / units(da) - 1 for _, da, _, db in pairs]
    dr = [revenue(db) / revenue(da) - 1 for _, da, _, db in pairs]
    consistent = all(abs((1 + q) * 1.1 - 1 - r) < 0.02 for q, r in zip(dq, dr, strict=True))
    below = all(r < 0.10 for r in dr)
    r = sum(dr) / len(dr)
    return Result(
        "P2",
        "A 10% price rise changes revenue by (1+dQ)(1.1)-1 and by less than +10%",
        HOLDS if consistent and below else FAILS,
        f"revenue {pct(r)}, units {pct(sum(dq) / len(dq))}",
        "revenue change < +10% and = (1+dQ)*1.1-1 within 2 pts",
        "engine._run_location revenue += o.price",
    )


@check
def check_p3(cfg: Config) -> Result:
    """One item +20%: its units fall more than the menu's (customers switch)."""
    item = "croissant"
    pairs = paired(cfg, scale_prices(1.2, (item,)), days=28)

    def item_units(days: list[en.LocationDay]) -> int:
        return sum(1 for d in days for s in d.sales for ln in s.lines if ln.item_id == item)

    own = [arc(item_units(db), item_units(da)) / arc(1.2, 1.0) for _, da, _, db in pairs]
    rest = [arc(units(db) - item_units(db), units(da) - item_units(da)) for _, da, _, db in pairs]
    e = sum(own) / len(own)
    ok = all(x < 0 for x in own) and sum(rest) / len(rest) > -0.01
    verdict = FAILS if not ok else HOLDS if -4.0 <= e <= -0.5 else MAGNITUDE
    return Result(
        "P3",
        "Raising one item's price cuts its own units (elasticity -0.5 to -4) and not the rest",
        verdict,
        f"croissant elasticity {e:.2f}; other units {pct(sum(rest) / len(rest))}",
        "own < 0 in [-4, -0.5]; others >= -1%",
        "engine._run_location weight_cache, _pick",
    )


# ---------------------------------------------------------------- bake quantity


def scale_plans(factor: float):
    def tweak(world: World) -> None:
        for entry in shop(world).menu.values():
            entry.bake_plan = [round(u * factor) for u in entry.bake_plan]

    return tweak


def waste_and_unmet(days: list[en.LocationDay]) -> tuple[int, int, int]:
    wasted = sum(i.wasted for d in days for i in d.items.values())
    unmet = sum(i.unmet for d in days for i in d.items.values())
    baked = sum(i.baked for d in days for i in d.items.values())
    return wasted, unmet, baked


@check
def check_q1(cfg: Config) -> Result:
    """Bake plan x0.7, x1, x1.3: waste rises and unmet demand falls with quantity."""
    rows = []
    for seed in cfg.seeds:
        row = []
        for f in (0.7, 1.0, 1.3):
            w = fresh(seed)
            scale_plans(f)(w)
            row.append(waste_and_unmet(home_days(run(w, cfg.days, seed))))
        rows.append(row)
    waste_up = [r[0][0] <= r[1][0] <= r[2][0] for r in rows]
    unmet_down = [r[0][1] >= r[1][1] >= r[2][1] for r in rows]
    tot = [[sum(r[k][j] for r in rows) for j in range(3)] for k in range(3)]
    ok = (
        tot[0][0] < tot[1][0] < tot[2][0]
        and tot[0][1] > tot[1][1] > tot[2][1]
        and share_true(waste_up) >= 0.8
        and share_true(unmet_down) >= 0.8
    )
    return Result(
        "Q1",
        "Baking more raises waste and lowers stockouts (unmet requests)",
        HOLDS if ok else FAILS,
        f"waste {tot[0][0]}/{tot[1][0]}/{tot[2][0]}, unmet {tot[0][1]}/{tot[1][1]}/{tot[2][1]}"
        " at plan x0.7/x1/x1.3",
        "waste increasing, unmet decreasing (>=80% of seeds each)",
        "engine._bake, engine._age_stock, engine._run_location qty/unmet",
    )


@check
def check_q2(cfg: Config) -> Result:
    """Waste share of baked units is 5-15% at the starting plan."""
    tot = [0, 0]
    for seed in cfg.seeds:
        wasted, _, baked = waste_and_unmet(home_days(run(fresh(seed), cfg.days, seed)))
        tot[0] += wasted
        tot[1] += baked
    share = tot[0] / tot[1]
    return Result(
        "Q2",
        "Waste is 5-15% of units baked at the shop's own plan",
        HOLDS if 0.05 <= share <= 0.15 else MAGNITUDE,
        f"waste {100 * share:.1f}% (established world, {cfg.days} days)",
        "5-15%",
        "engine._age_stock",
    )


# ---------------------------------------------------------------- counter


@check
def check_s1(cfg: Config) -> Result:
    """Generated inputs: wait rises with arrivals, falls with capacity; walkaway rises with wait."""
    rng, bad = random.Random(11), []
    for _ in range(cfg.draws * 50):
        a1, a2 = sorted(rng.randint(0, 300) for _ in range(2))
        c1, c2 = sorted(rng.uniform(0.5, 200) for _ in range(2))
        if en._wait_minutes(a1, c1) > en._wait_minutes(a2, c1) + 1e-9:
            bad.append(f"wait({a1},{c1:.1f}) > wait({a2},{c1:.1f})")
        if en._wait_minutes(a1, c1) < en._wait_minutes(a1, c2) - 1e-9:
            bad.append(f"wait({a1},{c1:.1f}) < wait({a1},{c2:.1f})")
        w1, w2 = sorted(rng.uniform(0, 80) for _ in range(2))
        t1, t2 = sorted(rng.uniform(5, 15) for _ in range(2))
        p = en._walkaway_probability
        if p(w1, t1) > p(w2, t1) + 1e-12 or p(w1, t1) < p(w1, t2) - 1e-12:
            bad.append(f"walkaway not monotone at wait {w1:.1f}/{w2:.1f}, threshold {t1:.1f}")
        if not 0 <= p(w1, t1) <= 0.95:
            bad.append(f"walkaway({w1:.1f},{t1:.1f}) = {p(w1, t1)} outside [0, 0.95]")
    if en._wait_minutes(5, 0) != math.inf or en._walkaway_probability(math.inf, 8) != 1.0:
        bad.append("no counter capacity does not mean everyone walks away")
    return Result(
        "S1",
        "Queue wait rises with arrivals and falls with counter capacity; walkaways rise with wait",
        HOLDS if not bad else FAILS,
        f"{cfg.draws * 50} generated draws, {len(bad)} violations",
        "0 violations",
        "engine._wait_minutes, engine._walkaway_probability",
        bad[:3],
    )


@check
def check_s2(cfg: Config) -> Result:
    """Paired runs: one counter worker fewer raises wait and walkaways."""

    def drop_counter(world: World) -> None:
        staff_of(world, Role.COUNTER)[0].state = StaffState.GONE

    pairs = paired(cfg, drop_counter)
    walk = [(sum(d.walkaways for d in da), sum(d.walkaways for d in db)) for _, da, _, db in pairs]

    def wait(days):
        hours = [w for d in days for w in d.wait_minutes_by_hour if 0 < w < 60]
        return sum(hours) / max(1, len(hours))

    waits = [(wait(da), wait(db)) for _, da, _, db in pairs]
    n = len(waits)
    ok = all(b > a for a, b in walk) and all(b > a for a, b in waits)
    wa, wb = sum(a for a, _ in walk), sum(b for _, b in walk)
    return Result(
        "S2",
        "Losing a counter worker lengthens the queue and loses more walkaways",
        HOLDS if ok else FAILS,
        f"walkaways {wa} -> {wb} ({pct(wb / max(1, wa) - 1)}); mean wait in staffed hours "
        f"{sum(a for a, _ in waits) / n:.1f} -> {sum(b for _, b in waits) / n:.1f} min",
        "both higher on every seed",
        "engine._counter_capacity -> _wait_minutes -> _walkaway_probability",
    )


@check
def check_s3(cfg: Config) -> Result:
    """Walkaways are 1-10% of arrivals at the starting staffing."""
    walk = arrivals = unstaffed = 0
    for seed in cfg.seeds:
        world = fresh(seed)
        rng, days = random.Random(seed + 100), []
        for _ in range(cfg.days):
            roster = en._roster(world, shop(world))
            cap = en._counter_capacity(shop(world), roster.counters)
            d = home_days([en.simulate_day(world, rng)])[0]
            days.append(d)
            unstaffed += sum(
                w
                for h, w in enumerate(d.walkaways_by_hour)
                if d.open_hour <= h < d.close_hour and cap[h] == 0
            )
        walk += sum(d.walkaways for d in days)
        arrivals += visits(days)
    share = walk / arrivals
    return Result(
        "S3",
        "Walkaways (balking at the queue) are 1-10% of arrivals with the shop's own staffing",
        HOLDS if 0.01 <= share <= 0.10 else MAGNITUDE,
        f"{100 * share:.1f}% of arrivals walk away; {100 * unstaffed / arrivals:.1f} pts of it in "
        "open hours with no counter worker on the rota",
        "1-10%",
        "engine._walkaway_probability (walkaway_wait_min 7.5-10), COUNTER_CUSTOMERS_PER_HOUR",
    )


# ---------------------------------------------------------------- cleanliness


@check
def check_k1(cfg: Config) -> Result:
    """Generated inputs: cleanliness rises with cleaner hours and falls with visits."""
    rng, bad = random.Random(12), []
    worlds = [fresh(seed) for seed in cfg.seeds]
    for _ in range(cfg.draws * 10):
        world = rng.choice(worlds)
        loc = shop(world)
        loc.cleanliness = rng.uniform(0, 1)
        start = loc.cleanliness
        cleaner = staff_of(world, Role.CLEANER)[0]
        visits_n = rng.randint(0, 600)
        h1, h2 = sorted(rng.randint(0, 8) for _ in range(2))
        out = []
        for hours in (h1, h2):
            loc.cleanliness = start
            roster = en._Roster(cleaners=[en._OnShift(cleaner, ShiftTime(10, 10 + hours))])
            en._tidy(loc, roster, [0] * 24, [0.0] * 24, visits_n)
            out.append(loc.cleanliness)
        loc.cleanliness = start
        en._tidy(loc, en._Roster(), [0] * 24, [0.0] * 24, visits_n + 100)
        busier = loc.cleanliness
        loc.cleanliness = start
        en._tidy(loc, en._Roster(), [0] * 24, [0.0] * 24, visits_n)
        if out[0] > out[1] + 1e-12 or (h2 > h1 and out[1] < 1 and out[1] <= out[0]):
            bad.append(f"{h2}h of cleaning leaves it no cleaner than {h1}h")
        if busier > loc.cleanliness + 1e-12:
            bad.append("more visits leave the shop cleaner")
        if not all(0 <= x <= 1 for x in out):
            bad.append("cleanliness left [0, 1]")
    return Result(
        "K1",
        "More cleaner hours keep the shop cleaner; more visits make it dirtier",
        HOLDS if not bad else FAILS,
        f"{cfg.draws * 10} generated draws, {len(bad)} violations",
        "0 violations",
        "engine._tidy",
        bad[:3],
    )


def _stars_run(world: World, days: int, seed: int):
    stars, clean = [], []

    def each(w: World, report: en.DayReport) -> None:
        loc = shop(w)
        day = home_days([report])[0]
        rng = random.Random(f"stars:{seed}:{report.day}")
        stars.extend(r.rating for r in rec.draw_reviews(rng, loc, _many_reviews(day)))
        clean.append(loc.cleanliness)

    reports = run(world, days, seed, each)
    return stars, clean, home_days(reports)


def _many_reviews(day: en.LocationDay) -> en.LocationDay:
    # 50x the visits so each day draws enough reviews to compare means; ratings are per review
    return replace(day, visits=day.visits * 50)


@check
def check_k2(cfg: Config) -> Result:
    """No cleaner for 8 weeks: dirtier shop, lower stars, fewer visits in the last 4 weeks."""
    rows = []
    for seed in cfg.seeds:
        a, b = fresh(seed), fresh(seed)
        for s in staff_of(b, Role.CLEANER):
            s.state = StaffState.GONE
        sa, ca, _ = _stars_run(a, 56, seed)
        sb, cb, _ = _stars_run(b, 56, seed)
        rows.append((sum(sa) / len(sa), sum(sb) / len(sb), ca[-1], cb[-1]))
    clean_ok = all(r[3] < r[2] for r in rows)
    stars_ok = all(r[1] < r[0] for r in rows)
    sa = sum(r[0] for r in rows) / len(rows)
    sb = sum(r[1] for r in rows) / len(rows)
    return Result(
        "K2",
        "Without a cleaner the shop gets dirtier and review stars fall",
        HOLDS if clean_ok and stars_ok else FAILS,
        f"cleanliness after 56 d {sum(r[2] for r in rows) / len(rows):.2f} -> "
        f"{sum(r[3] for r in rows) / len(rows):.2f}; mean stars {sa:.2f} -> {sb:.2f}",
        "both lower on every seed",
        "engine._tidy, records.draw_reviews (DIRTY_STARS * grime)",
    )


@check
def check_k3(cfg: Config) -> Result:
    """A dirty shop loses visits over time (satisfaction -> reputation -> visits)."""

    def dirty(world: World) -> None:
        for s in staff_of(world, Role.CLEANER):
            s.state = StaffState.GONE
        shop(world).cleanliness = 0.3

    pairs = paired(cfg, dirty, days=56)
    late = [(visits(da[28:]), visits(db[28:])) for _, da, _, db in pairs]
    rep = [(shop(a).reputation, shop(b).reputation) for a, _, b, _ in pairs]
    ok = all(b < a for a, b in late) and all(b < a for a, b in rep)
    va, vb = sum(a for a, _ in late), sum(b for _, b in late)
    return Result(
        "K3",
        "A dirty shop lowers satisfaction and reputation, so visits fall",
        HOLDS if ok else FAILS,
        f"visits days 28-56 {pct(vb / va - 1)}; reputation "
        f"{sum(a for a, _ in rep) / len(rep):.3f} -> {sum(b for _, b in rep) / len(rep):.3f}",
        "both lower on every seed",
        "engine._run_location (DIRTY_SATISFACTION, DIRTY_UTILITY), reputation drift, _visitors",
    )


# ---------------------------------------------------------------- bakers


def _plan_world(world: World, rng: random.Random):
    loc = shop(world)
    for entry in loc.menu.values():
        entry.bake_plan = [rng.randint(0, 80) for _ in range(7)]
    return world, loc


@check
def check_b1(cfg: Config) -> Result:
    """Generated inputs: more baker hours never bake fewer units and never more strain."""
    rng, bad = random.Random(13), []
    worlds = [fresh(seed) for seed in cfg.seeds]
    for _ in range(cfg.draws * 10):
        world, loc = _plan_world(rng.choice(worlds), rng)
        bakers = staff_of(world, Role.BAKER)
        for b in bakers:
            b.speed, b.skill, b.morale = rng.uniform(0.5, 1.2), rng.uniform(0, 1), rng.random()
        h1, h2 = sorted(rng.randint(1, 10) for _ in range(2))
        weekday = rng.randrange(7)
        out = []
        for hours in (h1, h2):
            roster = en._Roster(bakers=[en._OnShift(b, ShiftTime(5, 5 + hours)) for b in bakers])
            fresh_units, _, strain = en._bake(loc, roster, weekday, {}, {})
            out.append((sum(fresh_units.values()), strain))
        if out[0][0] > out[1][0]:
            bad.append(f"{h1}h bakes {out[0][0]} but {h2}h bakes {out[1][0]}")
        if out[0][1] < out[1][1] - 1e-12:
            bad.append(f"{h1}h strain {out[0][1]:.2f} < {h2}h strain {out[1][1]:.2f}")
    return Result(
        "B1",
        "More baker hours bake at least as many units with no more strain",
        HOLDS if not bad else FAILS,
        f"{cfg.draws * 10} generated rosters and plans, {len(bad)} violations",
        "0 violations",
        "engine._bake",
        bad[:3],
    )


@check
def check_b2(cfg: Config) -> Result:
    """Rushed bakes lose quality; quality has a floor."""
    world = fresh(1)
    loc = shop(world)
    fresh_units = {i: 10 for i, e in loc.menu.items() if not CATALOG[i].made_to_order}
    prev, bad = 2.0, []
    for k in range(0, 121):
        strain = k / 100
        q = min(o.quality for o in en._build_offers(loc, 0, fresh_units, {}, strain))
        if q > prev + 1e-12:
            bad.append(f"quality rises at strain {strain}")
        if q < en.MIN_RUSHED_QUALITY - 1e-12:
            bad.append(f"quality {q} below floor at strain {strain}")
        prev = q
    q0 = min(o.quality for o in en._build_offers(loc, 0, fresh_units, {}, 0.0))
    q1 = min(o.quality for o in en._build_offers(loc, 0, fresh_units, {}, 1.0))
    if not q1 < q0:
        bad.append("strain 1.0 does not lower quality")
    return Result(
        "B2",
        "A rushed (strained) bake lowers the quality of what is on the shelf",
        HOLDS if not bad else FAILS,
        f"quality {q0:.2f} at strain 0, {q1:.2f} at strain 1",
        "non-increasing in strain, >= MIN_RUSHED_QUALITY",
        "engine._build_offers (STRAIN_QUALITY)",
        bad[:3],
    )


@check
def check_b3(cfg: Config) -> Result:
    """Paired runs: bakers on 4-hour shifts bake less, strain more, and get lower stars."""

    def short(world: World) -> None:
        for s in staff_of(world, Role.BAKER):
            s.rota = tuple(ShiftTime(x.start_hour, x.start_hour + 3) if x else None for x in s.rota)

    rows, pairs = [], []
    for seed in cfg.seeds:
        a, b = fresh(seed), fresh(seed)
        short(b)
        sa, _, da = _stars_run(a, cfg.days, seed)
        sb, _, db = _stars_run(b, cfg.days, seed)
        rows.append((sum(sa) / len(sa), sum(sb) / len(sb)))
        pairs.append((a, da, b, db))
    baked = [(waste_and_unmet(da)[2], waste_and_unmet(db)[2]) for _, da, _, db in pairs]
    strain = [
        (sum(d.bake_strain for d in da), sum(d.bake_strain for d in db)) for _, da, _, db in pairs
    ]
    ok = (
        all(b < a for a, b in baked)
        and all(b > a for a, b in strain)
        and all(b < a for a, b in rows)
    )
    return Result(
        "B3",
        "Cutting baker hours bakes less, strains the bake and lowers stars",
        HOLDS if ok else FAILS,
        f"baked {sum(a for a, _ in baked)} -> {sum(b for _, b in baked)}; mean strain "
        f"{sum(a for a, _ in strain) / len(strain) / cfg.days:.2f} -> "
        f"{sum(b for _, b in strain) / len(strain) / cfg.days:.2f}; stars "
        f"{sum(a for a, _ in rows) / len(rows):.2f} -> {sum(b for _, b in rows) / len(rows):.2f}",
        "baked lower, strain higher, stars lower on every seed",
        "engine._bake (BAKER_HANDS_ON, labor_minutes), records.draw_reviews (UNEVEN_STARS)",
    )


# ---------------------------------------------------------------- customers


@check
def check_r1(cfg: Config) -> Result:
    """Satisfied customers come back more, lapse less, and lift reputation and awareness."""

    def set_sat(v: float):
        def tweak(world: World) -> None:
            for c in world.customers:
                c.satisfaction = v

        return tweak

    pairs = paired(cfg, set_sat(0.9), base=set_sat(0.4), days=cfg.days)

    def lapses(world: World) -> int:
        return sum(t.event == "GO_QUIET" for t in world.business.transitions)

    rows = [
        (
            visits(da),
            visits(db),
            lapses(a),
            lapses(b),
            shop(a).reputation,
            shop(b).reputation,
            shop(a).awareness,
            shop(b).awareness,
        )
        for a, da, b, db in pairs
    ]
    ok = all(r[1] > r[0] and r[3] < r[2] and r[5] > r[4] and r[7] >= r[6] for r in rows)
    va, vb = sum(r[0] for r in rows), sum(r[1] for r in rows)
    return Result(
        "R1",
        "Happier customers visit more, lapse less, and raise reputation and word of mouth",
        HOLDS if ok else FAILS,
        f"satisfaction 0.4 -> 0.9: visits {pct(vb / va - 1)}, lapses "
        f"{sum(r[2] for r in rows)} -> {sum(r[3] for r in rows)}",
        "visits up, lapses down, reputation up, awareness not down on every seed",
        "engine._visitors (0.5+0.7*satisfaction), _advance_customers, _update_awareness",
    )


# ---------------------------------------------------------------- staff


class _Threshold(random.Random):
    """An rng whose first draw is u and later draws never fire, to read a hazard off the code."""

    def __init__(self, u: float) -> None:
        super().__init__(0)
        self.u, self.calls = u, 0

    def random(self) -> float:
        self.calls += 1
        return self.u if self.calls == 1 else 1 - 1e-12


def _daily_quit(world: World, s: Staff) -> float:
    """The engine's own probability that s gives notice today, found by bisection on the draw
    `_staff_day` compares with it (state, morale and the log are restored after each call)."""
    business, loc = world.business, world.business.locations[s.location_id]
    staff, log = business.staff, len(business.transitions)
    keep = (s.state, s.morale, s.skill, s.state_until_day)
    business.staff = {s.id: s}
    low, high = 0.0, 1.0
    try:
        for _ in range(40):
            mid = (low + high) / 2
            s.state, s.morale = StaffState.EMPLOYED, keep[1]
            en._staff_day(world, loc, en._Roster(), [0] * 24, [0.0] * 24, _Threshold(mid))
            if s.state is StaffState.ON_NOTICE:
                low = mid
            else:
                high = mid
    finally:
        business.staff = staff
        business.transitions[log:] = []
        s.state, s.morale, s.skill, s.state_until_day = keep
    return high


def _annual_quit(world: World, s: Staff) -> float:
    return 1 - (1 - _daily_quit(world, s)) ** 365


MORALE_DAYS = 90  # morale moves 4% of the gap a day, so 90 days is 97% of the way there
_MORALE: dict[tuple, list] = {}


def _morale_worlds(cfg: Config, key: str, tweak: Callable[[World], None] | None) -> list[World]:
    """Worlds after MORALE_DAYS with no rehiring, one per seed; cached per arm."""
    if (key, cfg.seeds) not in _MORALE:
        worlds = []
        for seed in cfg.seeds:
            w = fresh(seed)
            w.business.auto_hire = False
            if tweak:
                tweak(w)
            run(w, MORALE_DAYS, seed)
            worlds.append(w)
        _MORALE[(key, cfg.seeds)] = worlds
    return _MORALE[(key, cfg.seeds)]


def _morale_run(cfg: Config, key: str, tweak: Callable[[World], None]):
    rows = []
    for a, b in zip(
        _morale_worlds(cfg, "base", None), _morale_worlds(cfg, key, tweak), strict=True
    ):
        ids = [s.id for s in a.business.staff.values() if not s.owner]
        pa = [a.business.staff[i] for i in ids if a.business.staff[i].state.name != "GONE"]
        pb = [b.business.staff[i] for i in ids if b.business.staff[i].state.name != "GONE"]
        rows.append(
            (
                sum(s.morale for s in pa) / len(pa),
                sum(s.morale for s in pb) / len(pb),
                sum(_annual_quit(a, s) for s in pa) / len(pa),
                sum(_annual_quit(b, s) for s in pb) / len(pb),
            )
        )
    return rows


@check
def check_h1(cfg: Config) -> Result:
    """Cutting staff hours well below contract lowers morale and raises the quit rate."""

    def halve(world: World) -> None:
        for s in world.business.staff.values():
            s.rota = tuple(x if x and d < 2 else None for d, x in enumerate(s.rota))

    rows = _morale_run(cfg, "two shifts", halve)
    ok = all(r[1] < r[0] and r[3] > r[2] for r in rows)
    n = len(rows)
    return Result(
        "H1",
        "Hours cut well below contract lower morale and raise quits",
        HOLDS if ok else FAILS,
        f"morale {sum(r[0] for r in rows) / n:.2f} -> {sum(r[1] for r in rows) / n:.2f}; "
        f"implied annual quit rate {100 * sum(r[2] for r in rows) / n:.0f}% -> "
        f"{100 * sum(r[3] for r in rows) / n:.0f}% after {MORALE_DAYS} days on two shifts a week",
        "both worse on every seed",
        "engine._staff_day (HOURS_CUT_MORALE, quit_hazard*(QUIT_FLOOR+QUIT_MORALE*(1-morale)^2))",
    )


def _wage(factor: float):
    def tweak(world: World) -> None:
        for s in world.business.staff.values():
            s.wage_cents_per_hour = round(en.market_wage(world, s.role) * factor)

    return tweak


@check
def check_w1(cfg: Config) -> Result:
    """Pay below market lowers morale and raises quits."""
    rows = _morale_run(cfg, "wage 0.9", _wage(0.9))
    ok = all(r[1] < r[0] and r[3] > r[2] for r in rows)
    n = len(rows)
    return Result(
        "W1",
        "Pay below the market wage lowers morale and raises quits",
        HOLDS if ok else FAILS,
        f"wage -10%: morale {sum(r[0] for r in rows) / n:.2f} -> {sum(r[1] for r in rows) / n:.2f}",
        "lower morale, higher quits on every seed",
        "engine._staff_day (wage_morale_sensitivity * wage_gap)",
    )


@check
def check_w2(cfg: Config) -> Result:
    """Annual staff turnover at market pay is 30-60% (a small bakery, below fast food)."""
    rows = _morale_run(cfg, "wage 1.0", _wage(1.0))
    rate = sum(r[2] for r in rows) / len(rows)
    return Result(
        "W2",
        "At market pay, annual staff turnover is 30-60%",
        HOLDS if 0.30 <= rate <= 0.60 else MAGNITUDE,
        f"{100 * rate:.0f}% a year (engine quit hazard at morale after {MORALE_DAYS} days)",
        "30-60%",
        "params.SCALARS quit_hazard; engine._staff_day QUIT_FLOOR, QUIT_MORALE (read by bisection)",
    )


@check
def check_w3(cfg: Config) -> Result:
    """Quit elasticity to the wage is -0.5 to -4."""
    base = _morale_run(cfg, "wage 1.0", _wage(1.0))
    low = _morale_run(cfg, "wage 0.9", _wage(0.9))
    qa = sum(r[3] for r in base) / len(base)
    qb = sum(r[3] for r in low) / len(low)
    e = arc(qb, qa) / arc(0.9, 1.0)
    return Result(
        "W3",
        "A 10% pay cut raises quits with an elasticity of -0.5 to -4",
        HOLDS if -4 <= e <= -0.5 else MAGNITUDE if e < 0 else FAILS,
        f"elasticity {e:.1f} (annual quits {100 * qa:.0f}% -> {100 * qb:.0f}%)",
        "[-4, -0.5]",
        "engine._staff_day",
    )


@check
def check_ab1(cfg: Config) -> Result:
    """Sickness takes 2-4% of rota'd shifts and paid leave about 4 weeks a year; an absent
    person adds no capacity and is paid only for leave."""
    shifts = sick = leave = 0
    bad = []
    for seed in cfg.seeds:
        world = fresh(seed)
        world.business.auto_hire = False
        rng = random.Random(seed + 100)
        for _ in range(cfg.days * 4):
            loc = shop(world)
            roster = en._roster(world, loc)
            working = {on.staff.id for on in roster.bakers + roster.counters + roster.cleaners}
            ill = {s.id for s in world.business.staff.values() if s.state is StaffState.SICK}
            for s in world.business.staff.values():
                if s.state.name != "GONE" and rota.worked(s, loc, world.day % 7):
                    shifts += 1
                    sick += s.id in ill
            d = home_days([en.simulate_day(world, rng)])[0]
            for sh in d.shifts:
                leave += sh.leave
                if sh.leave and (sh.staff_id in working or sh.pay_cents <= 0):
                    bad.append(f"{sh.staff_id} on leave but working or unpaid")
            if ill & {sh.staff_id for sh in d.shifts}:
                bad.append("a sick worker was paid")
    s_share, l_share = sick / shifts, leave / shifts
    ok = 0.02 <= s_share <= 0.04 and 0.07 <= l_share <= 0.085
    return Result(
        "AB1",
        "Sickness takes 2-4% of shifts, paid leave about 4 weeks a year; absences are not covered",
        FAILS if bad else HOLDS if ok else MAGNITUDE,
        f"sick {100 * s_share:.1f}% and paid leave {100 * l_share:.1f}% of {shifts} rota'd "
        f"shifts; {len(bad)} violations",
        "sick 2-4%, leave 7-8.5% (4 of 52 weeks); absent staff add no capacity, sick unpaid",
        "engine._staff_day SICK_RATE, engine._roster, rota.on_leave, engine._shifts",
        bad[:3],
    )


# ---------------------------------------------------------------- opening hours


@check
def check_o1(cfg: Config) -> Result:
    """Closing at 14:00: no one is served who arrived after close, morning sales unchanged."""

    def early(world: World) -> None:
        shop(world).close_hour = 14

    pairs = paired(cfg, early)
    bad, morning = [], []
    for _, da, _, db in pairs:
        for d in db:
            for s in d.sales:
                if s.hour * 60 + s.minute > 14 * 60 + 60:
                    bad.append(f"sale served at {s.hour}:{s.minute:02d} after a 14:00 close")
        early_a = sum(1 for d in da for s in d.sales if s.hour < 12)
        early_b = sum(1 for d in db for s in d.sales if s.hour < 12)
        morning.append(early_b / max(1, early_a) - 1)
    after = sum(d.arrivals_after_close for _, _, _, db in pairs for d in db)
    ua = sum(units(da) for _, da, _, _ in pairs)
    ub = sum(units(db) for _, _, _, db in pairs)
    ok = not bad and after > 0 and ub < ua and all(abs(m) < 0.05 for m in morning)
    return Result(
        "O1",
        "Closing early loses the afternoon's sales and leaves morning sales as they were",
        HOLDS if ok else FAILS,
        f"units {pct(ub / ua - 1)}; morning sales {pct(sum(morning) / len(morning))}; "
        f"{after} arrivals after close",
        "no sale after close+60 min, fewer units, morning within 5%",
        "engine._run_location open_hours/inside, arrivals_after_close",
        bad[:3],
    )


# ---------------------------------------------------------------- costs


@check
def check_i1(cfg: Config) -> Result:
    """Flour +20% raises ingredient cost by grams x price and changes nothing else."""
    bad, rows = [], []
    for seed in cfg.seeds:
        a, b = fresh(seed), fresh(seed)
        prices = b.ingredient_prices
        base_on = prices.on

        def dear(day: int, base_on=base_on):
            row = dict(base_on(day))
            row[Ingredient.FLOUR] = round(row[Ingredient.FLOUR] * 1.2)
            return row

        b.ingredient_prices = type("Dear", (), {"on": staticmethod(dear)})()
        ra, rb = run(a, cfg.days, seed), run(b, cfg.days, seed)
        for x, y in zip(ra, rb, strict=True):
            for la, lb in zip(x.locations, y.locations, strict=True):
                if la.sales != lb.sales or la.ingredient_grams != lb.ingredient_grams:
                    bad.append(f"seed {seed} day {x.day}: sales or grams differ")
                want = sum(
                    round(g * y.prices_paid[i] / 1000) for i, g in lb.ingredient_grams.items()
                )
                if lb.ingredient_cost_cents != want:
                    bad.append(
                        f"seed {seed} day {x.day}: cost {lb.ingredient_cost_cents} != {want}"
                    )
                flour = lb.ingredient_grams.get(Ingredient.FLOUR, 0)
                if flour >= 1000 and lb.ingredient_cost_cents <= la.ingredient_cost_cents:
                    bad.append(f"seed {seed} day {x.day}: dearer flour, cheaper ingredients")
        rows.append(
            sum(d.ingredient_cost_cents for d in home_days(rb))
            / sum(d.ingredient_cost_cents for d in home_days(ra))
            - 1
        )
    return Result(
        "I1",
        "Dearer flour raises the cost of goods by grams x price and moves no sale",
        HOLDS if not bad else FAILS,
        f"flour +20%: ingredient cost {pct(sum(rows) / len(rows))}, {len(bad)} violations",
        "exact: cost = sum(grams x price / 1000); sales identical",
        "engine.ingredient_cost, engine.prices_paid, engine._run_location",
        bad[:3],
    )


def _shares(cfg: Config) -> tuple[float, float]:
    food = labor = rev = 0
    for seed in cfg.seeds:
        for d in home_days(run(fresh(seed), cfg.days, seed)):
            food += d.ingredient_cost_cents + d.packaging_cents
            labor += d.wages_cents + d.payroll_oncost_cents
            rev += d.revenue_cents
    return food / rev, labor / rev


@check
def check_i2(cfg: Config) -> Result:
    food, _ = _shares(cfg)
    return Result(
        "I2",
        "Food and packaging cost is 25-35% of sales",
        HOLDS if 0.25 <= food <= 0.35 else MAGNITUDE,
        f"{100 * food:.0f}% (established world)",
        "25-35%",
        "engine.ingredient_cost, PACKAGING_CENTS, catalog grams",
    )


@check
def check_l1(cfg: Config) -> Result:
    _, labor = _shares(cfg)
    return Result(
        "L1",
        "Labor (wages and on-costs) is 25-35% of sales",
        HOLDS if 0.25 <= labor <= 0.35 else MAGNITUDE,
        f"{100 * labor:.0f}% (established world; the handoff year runs higher, see BK1)",
        "25-35%",
        "engine._shifts, PAYROLL_ONCOST_RATE, model.MARKET_WAGE_CENTS, town.STARTING_STAFF",
    )


# ---------------------------------------------------------------- marketing, rivals, weather


@check
def check_m1(cfg: Config) -> Result:
    """Generated inputs: awareness rises with spend, with diminishing returns, and stays below 1."""
    rng, bad = random.Random(14), []
    world = fresh(1)
    loc = shop(world)
    for _ in range(cfg.draws * 50):
        aw, v = rng.uniform(0, 0.99), rng.randint(0, 400)
        s1, s2, s3 = sorted(rng.uniform(0, 500_000) for _ in range(3))
        out = []
        for spend in (s1, s2, s3):
            loc.awareness, loc.marketing_cents_per_month = aw, spend
            en._update_awareness(world, loc, v)
            out.append(loc.awareness)
        if not out[0] <= out[1] <= out[2]:
            bad.append(f"awareness not increasing in spend at {s1:.0f}/{s2:.0f}/{s3:.0f}")
        slope1 = (out[1] - out[0]) / max(1e-9, s2 - s1)
        slope2 = (out[2] - out[1]) / max(1e-9, s3 - s2)
        if slope2 > slope1 + 1e-12:
            bad.append("marketing has increasing returns")
        if out[2] >= 1:
            bad.append("awareness reached 1")
    pairs = paired(cfg, lambda w: setattr(shop(w), "marketing_cents_per_month", 200_000), days=56)
    lift = [visits(db[28:]) / visits(da[28:]) - 1 for _, da, _, db in pairs]
    if not all(x > 0 for x in lift):
        bad.append("$2,000 a month of marketing does not raise visits")
    return Result(
        "M1",
        "Marketing raises awareness with diminishing returns, and awareness brings visits",
        HOLDS if not bad else FAILS,
        f"{cfg.draws * 50} draws; $2,000/month lifts visits in weeks 5-8 by "
        f"{pct(sum(lift) / len(lift))}",
        "monotone, concave, < 1; visits up",
        "engine._update_awareness (1-exp(-spend/MARKETING_SCALE_CENTS)), _visitors awareness_f",
        bad[:3],
    )


@check
def check_c1(cfg: Config) -> Result:
    """A rival café opening near the shop takes visits."""

    def rival(world: World) -> None:
        comp = world.competitors[RIVAL_ID]
        comp.state = CompetitorState.OPEN
        from bakery_world.decay import RIVAL_APPEAL

        comp.appeal = dict(RIVAL_APPEAL)

    pairs = paired(cfg, rival)
    drop = [visits(db) / visits(da) - 1 for _, da, _, db in pairs]
    d = sum(drop) / len(drop)
    ok = all(x < 0 for x in drop)
    return Result(
        "C1",
        "A competitor opening nearby takes some of the shop's visits (5-30% at full strength)",
        (HOLDS if -0.30 <= d <= -0.05 else MAGNITUDE) if ok else FAILS,
        f"visits {pct(d)} with the rival fully open",
        "< 0, in [-30%, -5%]",
        "engine._visit_factors pull / (1 + 0.45 * pull), decay._competitor_entry",
    )


@check
def check_e1(cfg: Config) -> Result:
    """Rain and extreme temperatures lower visit propensity for every segment."""
    world = fresh(1)
    bad = []
    for day in range(0, 364, 7):
        dry = en._visit_factors(world, day, 15.0, False)
        wet = en._visit_factors(world, day, 15.0, True)
        cold = en._visit_factors(world, day, -5.0, False)
        for key in dry:
            if not (wet[key] < dry[key] and cold[key] < dry[key]):
                bad.append(f"day {day} {key}: rain or cold does not lower visits")
    return Result(
        "E1",
        "Rain and extreme cold or heat lower visits",
        HOLDS if not bad else FAILS,
        f"{52 * len(dry)} day x segment cells, {len(bad)} violations",
        "0 violations",
        "engine._visit_factors weather",
        bad[:3],
    )


@check
def check_e2(cfg: Config) -> Result:
    """Seasonal swing in visit propensity (month mean, max/min - 1) is 10-40%."""
    world = fresh(1)
    months: dict[int, list[float]] = {}
    for day in range(365):
        temp, rain = world.weather.on(day)
        f = en._visit_factors(world, day, temp, rain)
        months.setdefault(day * 12 // 365, []).append(sum(f.values()) / len(f))
    means = [sum(v) / len(v) for v in months.values()]
    swing = max(means) / min(means) - 1
    return Result(
        "E2",
        "Visits swing by season (best month vs worst month 10-40%)",
        HOLDS if 0.10 <= swing <= 0.40 else MAGNITUDE,
        f"{100 * swing:.1f}% best vs worst month (traffic amplitude "
        f"{100 * ex.SEASONAL_AMPLITUDE:.1f}%, rain only)",
        "10-40%",
        "exogenous.SeasonalFootTraffic SEASONAL_AMPLITUDE, SeasonalWeather; engine._visit_factors",
    )


# ---------------------------------------------------------------- accounting


def handoff(seed: int):
    """A real ManagedWorld at handoff, cached under runs/bakery/verify (gitignored)."""
    from bakery_world.history import HISTORY_DAYS, found, live
    from bakery_world.management import load

    path = CACHE / f"seed-{seed}.pickle"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        founding = found(seed)
        live(founding, HISTORY_DAYS)
        path.write_bytes(pickle.dumps(founding, protocol=pickle.HIGHEST_PROTOCOL))
    return path, load


def _random_action(mw, rng: random.Random) -> None:
    from bakery_world.actions import ActionRefused, perform

    b = mw.world.business
    staff = [s.id for s in b.staff.values() if s.state in rota.ON_ROTA and not s.owner]
    menu = [i for i, e in b.locations[HOME_LOCATION].menu.items() if e.state.name == "ON_MENU"]
    item = rng.choice(menu)
    wage = rng.randint(1750, 3000)
    options = {
        "set_price": dict(
            location="shop1",
            item=item,
            price_cents=round(CATALOG[ItemId(item)].typical_price_cents * rng.uniform(0.6, 1.6)),
        ),
        "spend_marketing": dict(
            location="shop1",
            channel=rng.choice(["flyers", "social", "local_paper"]),
            amount_cents=rng.randint(5_000, 200_000),
        ),
        "draw_credit": dict(amount_cents=rng.randint(1, 500_000)),
        "repay_credit": dict(amount_cents=rng.randint(1, 500_000)),
        "run_promotion": dict(
            location="shop1", item=item, discount_pct=rng.randint(5, 50), days=rng.randint(1, 28)
        ),
        "post_job": dict(
            location="shop1",
            role=rng.choice(["baker", "counter", "cleaner"]),
            wage_cents_per_hour=wage,
        ),
        "buy_market_research": dict(neighborhood="station"),
        "set_hours": dict(
            location="shop1", open_hour=rng.randint(6, 9), close_hour=rng.randint(14, 19)
        ),
    }
    if staff:
        options["set_wage"] = dict(staff=rng.choice(staff), wage_cents_per_hour=wage)
        options["fire"] = dict(staff=rng.choice(staff))
    name = rng.choice(sorted(options))
    try:
        perform(mw, name, **options[name])
    except ActionRefused:
        pass


@check
def check_a1(cfg: Config) -> Result:
    """Generated actions: every day, cash change = sum of that day's ledger entries, and each
    shop's P&L lines equal what it posted."""
    from bakery_world.management import advance_day

    path, load = handoff(1)
    mw = load(path)
    rng, bad = random.Random(15), []
    days = max(28, cfg.draws)
    for _ in range(days):
        b = mw.world.business
        cash, mark = b.cash_cents, len(b.ledger)
        for _ in range(rng.randint(0, 3)):
            _random_action(mw, rng)
        advance_day(mw)
        new = b.ledger[mark:]
        if b.cash_cents - cash != sum(e.amount_cents for e in new):
            bad.append(
                f"day {mw.day - 1}: cash moved {b.cash_cents - cash}, ledger "
                f"{sum(e.amount_cents for e in new)}"
            )
        report = mw.reports[-1]
        for d in report.locations:
            posted = sum(
                e.amount_cents
                for e in new
                if e.location_id == d.location_id
                and e.day == report.day
                and e.account
                in en.DAILY_OVERHEAD_CENTS.keys()
                | {
                    Account.SALES,
                    Account.INGREDIENTS,
                    Account.PACKAGING,
                    Account.WAGES,
                    Account.PAYROLL_ONCOSTS,
                    Account.RENT,
                    Account.CARD_FEES,
                    Account.REPAIRS,
                }
                and not e.memo
            )
            if posted != d.revenue_cents - d.costs_cents:
                bad.append(
                    f"day {report.day}: P&L {d.revenue_cents - d.costs_cents} != posted {posted}"
                )
    return Result(
        "A1",
        "Cash changes by exactly cash in minus cash out, and each shop's P&L is what it posted",
        HOLDS if not bad else FAILS,
        f"{days} managed days with random actions, {len(bad)} violations",
        "exact",
        "engine.post, engine._run_location charges, management.advance_day, actions.*",
        bad[:3],
    )


@check
def check_a2(cfg: Config) -> Result:
    """Score: net worth rises one for one with cash and never falls when profit rises."""
    from bakery_world.score import going_concern, net_worth

    path, load = handoff(1)
    rng, bad = random.Random(16), []
    for _ in range(cfg.draws):
        mw = load(path)
        b = mw.world.business
        before, x = net_worth(mw), rng.randint(1, 5_000_000)
        b.cash_cents += x
        if net_worth(mw) - before != x:
            bad.append(f"cash +{x} moved net worth by {net_worth(mw) - before}")
        gc = going_concern(mw)
        en.post(b, mw.day - 1, Account.SALES, x, HOME_LOCATION)
        if going_concern(mw) < gc or net_worth(mw) < before + 2 * x:
            bad.append(f"sale +{x} did not raise net worth by at least the cash")
        en.post(b, mw.day - 1, Account.WAGES, -2 * x, HOME_LOCATION)
        if going_concern(mw) > gc:
            bad.append(f"wages +{2 * x} raised the going concern")
    return Result(
        "A2",
        "Net worth moves one for one with cash and is monotone in operating profit",
        HOLDS if not bad else FAILS,
        f"{cfg.draws} generated amounts, {len(bad)} violations",
        "exact / monotone",
        "score.net_worth, score.going_concern, score.operating_profit",
        bad[:3],
    )


@check
def check_a3(cfg: Config) -> Result:
    """Generated: the day's P&L components add up and costs are never negative."""
    bad = []
    for seed in cfg.seeds:
        for d in home_days(run(fresh(seed), cfg.days, seed)):
            parts = (
                d.ingredient_cost_cents,
                d.packaging_cents,
                d.wages_cents,
                d.payroll_oncost_cents,
                d.rent_cents,
                d.card_fees_cents,
                d.repairs_cents,
                d.overhead_cents,
            )
            if min(parts) < 0:
                bad.append("a cost line is negative")
            if d.revenue_cents != sum(ln.price_cents for s in d.sales for ln in s.lines):
                bad.append("revenue is not the sum of sale lines")
            if d.wages_cents != sum(s.pay_cents for s in d.shifts):
                bad.append("wages are not the sum of shifts")
            if d.payroll_oncost_cents != round(d.wages_cents * en.PAYROLL_ONCOST_RATE):
                bad.append("on-costs are not 12% of wages")
    return Result(
        "A3",
        "Revenue is the sum of sale lines, wages the sum of shifts, on-costs 12% of wages",
        HOLDS if not bad else FAILS,
        f"{len(cfg.seeds) * cfg.days} shop days, {len(bad)} violations",
        "exact",
        "engine._run_location, engine._shifts",
        bad[:3],
    )


# ---------------------------------------------------------------- state machines


def _reach(machine: mc.Machine, start) -> set:
    seen, todo = {start}, [start]
    while todo:
        s = todo.pop()
        for (a, _), b in machine.table.items():
            if a is s and b not in seen:
                seen.add(b)
                todo.append(b)
    return seen


@check
def check_sm1(cfg: Config) -> Result:
    """Exhaustive: every machine's terminal states are final, every state is reachable from the
    first and can reach a terminal state, and every (state, event) pair is either listed or
    refused."""
    bad = []
    for name, m in mc.MACHINES.items():
        first = next(iter(m.states))
        reach = _reach(m, first)
        for s in m.states:
            if s not in reach:
                bad.append(f"{name}: {s.name} unreachable from {first.name}")
            if s in m.terminal and any(a is s for a, _ in m.table):
                bad.append(f"{name}: terminal {s.name} has an exit")
            if s not in m.terminal and not (_reach(m, s) & m.terminal):
                bad.append(f"{name}: {s.name} can never finish")
        for s, e in product(m.states, m.events):
            try:
                after = mc.advance(m.table, s, e)
            except mc.IllegalTransition:
                continue
            if after not in m.states:
                bad.append(f"{name}: {s.name}+{e.name} leaves the machine")
    pairs = sum(len(m.states) * len(m.events) for m in mc.MACHINES.values())
    return Result(
        "SM1",
        "State machines: no exit from a final state, no dead or unreachable state",
        HOLDS if not bad else FAILS,
        f"{len(mc.MACHINES)} machines, {pairs} (state, event) pairs enumerated, {len(bad)} faults",
        "0 faults",
        "machines.MACHINES, machines.advance",
        bad[:3],
    )


@check
def check_sm2(cfg: Config) -> Result:
    """Generated staff states: nobody is paid on a day they are not employed or on notice."""
    rng, bad = random.Random(17), []
    for seed in cfg.seeds:
        world = fresh(seed)
        states = list(StaffState)
        for day in range(cfg.days):
            for s in world.business.staff.values():
                if rng.random() < 0.2:
                    s.state = rng.choice(states)
            on_day = {s.id: s.state for s in world.business.staff.values()}
            report = en.simulate_day(world, random.Random(seed * 1000 + day))
            for d in report.locations:
                for shift in d.shifts:
                    if on_day[shift.staff_id] not in (StaffState.EMPLOYED, StaffState.ON_NOTICE):
                        bad.append(f"{shift.staff_id} paid while {on_day[shift.staff_id].name}")
    return Result(
        "SM2",
        "Only employed staff (or staff working their notice) work and get paid",
        HOLDS if not bad else FAILS,
        f"{len(cfg.seeds) * cfg.days} days with random staff states, {len(bad)} violations",
        "0 violations",
        "engine._roster, engine._shifts",
        bad[:3],
    )


@check
def check_sm3(cfg: Config) -> Result:
    """Every quit the engine makes is notice first, then leaving NOTICE_DAYS later."""
    bad, quits = [], 0
    for seed in cfg.seeds:
        world = fresh(seed)
        world.params = replace(world.params, quit_hazard=0.02)
        run(world, cfg.days * 2, seed)
        notice = {}
        for t in world.business.transitions:
            if t.entity != "staff":
                continue
            if t.event == "GIVE_NOTICE":
                notice[t.entity_id] = t.day
                quits += 1
            if t.event == "LEAVE":
                start = notice.get(t.entity_id)
                if start is None or t.day - start < en.NOTICE_DAYS:
                    bad.append(
                        f"{t.entity_id} left on day {t.day} without {en.NOTICE_DAYS} days' notice"
                    )
    return Result(
        "SM3",
        "A quit is notice, then leaving after the notice period",
        HOLDS if not bad and quits else FAILS,
        f"{quits} quits at a raised hazard, {len(bad)} violations",
        "0 violations",
        "engine._staff_day GIVE_NOTICE/LEAVE",
        bad[:3],
    )


@check
def check_sm4(cfg: Config) -> Result:
    """Exhaustive over location states: only a trading shop sells, pays wages or posts costs."""
    bad = []
    for state in LocationState:
        world = fresh(1)
        shop(world).state = state
        cash = world.business.cash_cents
        report = en.simulate_day(world, random.Random(1))
        traded = any(d.location_id == HOME_LOCATION for d in report.locations)
        moved = world.business.cash_cents - cash + report.interest_cents
        if (state is LocationState.TRADING) != traded:
            bad.append(f"{state.name}: traded={traded}")
        if state is not LocationState.TRADING and moved:
            bad.append(f"{state.name}: cash moved {moved}")
    return Result(
        "SM4",
        "A shop that is not trading sells nothing and pays nothing in the engine",
        HOLDS if not bad else FAILS,
        f"{len(LocationState)} location states, {len(bad)} violations",
        "0 violations",
        "engine.simulate_day (state is TRADING)",
        bad[:3],
    )


# ---------------------------------------------------------------- the baker question


BAKER_SEED = 3  # two ordinary bakers at handoff (seeds 1 and 2 plant a slow, overpaid one)


def _baker_runs(seed: int):
    from bakery_world.actions import perform
    from bakery_world.baselines import Freeze
    from bakery_world.management import manage

    path, load = handoff(seed)

    @dataclass
    class FireBaker(Freeze):
        name: str = "fire_baker"
        target: str = ""
        rota: tuple[str, ...] = ()

        def weekly(self, mw, week: int) -> None:
            if week == 0:
                perform(mw, "fire", staff=self.target)
                if self.rota:
                    other = staff_of(mw.world, Role.BAKER)[0]
                    perform(mw, "set_rota", staff=other.id, shifts=list(self.rota))

    mw0 = load(path)
    bakers = [s.id for s in staff_of(mw0.world, Role.BAKER)]
    base = manage(path, Freeze(), 52)
    arms = {"freeze": base}
    for b in bakers:
        arms[f"fire {b}"] = manage(path, FireBaker(target=b), 52)
    arms[f"fire {bakers[-1]}, other 6x8h"] = manage(
        path, FireBaker(target=bakers[-1], rota=("05-13",) * 6 + ("off",)), 52
    )
    return base, arms


def _ledger(r) -> dict:
    acc: dict = {}
    for e in r.world.world.business.ledger:
        if e.day >= r.world.handoff_day:
            acc[e.account] = acc.get(e.account, 0) + e.amount_cents
    return acc


@check
def check_bk1(cfg: Config) -> Result:
    """Firing one of two bakers in a ~$30-40k/month shop does not pay over 52 weeks."""
    base, arms = _baker_runs(BAKER_SEED)
    a0 = _ledger(base)
    detail, gains = [], []
    for name, r in arms.items():
        if name == "freeze":
            continue
        a = _ledger(r)

        def d(acct, a=a):
            return (a.get(acct, 0) - a0.get(acct, 0)) / 100

        gain = (r.net_worth_change - base.net_worth_change) / 100
        if "6x8h" not in name:
            gains.append(gain)
        detail.append(
            f"{name}: net worth {gain:+,.0f}; sales {d(Account.SALES):+,.0f}, wages "
            f"{d(Account.WAGES):+,.0f}, on-costs {d(Account.PAYROLL_ONCOSTS):+,.0f}, ingredients "
            f"{d(Account.INGREDIENTS):+,.0f}, packaging {d(Account.PACKAGING):+,.0f}, severance "
            f"{d(Account.SEVERANCE):+,.0f}"
        )
    sales = a0.get(Account.SALES, 0) / 100 / 12
    labor = -(a0.get(Account.WAGES, 0) + a0.get(Account.PAYROLL_ONCOSTS, 0)) / 100 / 12
    detail.append(
        f"freeze: sales ${sales:,.0f}/month, labor ${labor:,.0f}/month "
        f"({100 * labor / sales:.0f}% of sales)"
    )
    return Result(
        "BK1",
        "Firing either of two bakers does not pay over 52 weeks",
        HOLDS if max(gains) <= 0 else FAILS,
        f"net worth vs freeze when firing each baker and changing nothing else: "
        f"{', '.join(f'{g:+,.0f}' for g in gains)} $ (seed {BAKER_SEED})",
        "<= 0 for every baker (the 6x8h arm is shown, not judged)",
        "actions.fire, managed.let_go, engine._roster/_bake, score.net_worth",
        detail,
    )


@check
def check_bk2(cfg: Config) -> Result:
    """A baker made to work 48 hours a week (sole baker) loses morale."""

    def overwork(world: World) -> None:
        s = staff_of(world, Role.BAKER)[0]
        s.rota = (ShiftTime(5, 13),) * 6 + (None,)

    rows = []
    for seed, a, b in zip(
        cfg.seeds,
        _morale_worlds(cfg, "base", None),
        _morale_worlds(cfg, "baker 48h", overwork),
        strict=True,
    ):
        sid = staff_of(fresh(seed), Role.BAKER)[0].id
        rows.append((a.business.staff[sid].morale, b.business.staff[sid].morale))
    ok = all(b < a - 0.01 for a, b in rows)
    n = len(rows)
    return Result(
        "BK2",
        "A baker put on 48 hours a week, 8 over contract, ends up with lower morale",
        HOLDS if ok else FAILS,
        f"morale after {MORALE_DAYS} days {sum(a for a, _ in rows) / n:.3f} (40 h) vs "
        f"{sum(b for _, b in rows) / n:.3f} (48 h)",
        "lower by > 0.01 on every seed",
        "engine._staff_day (OVERTIME_MORALE * hours over contract)",
    )


# ---------------------------------------------------------------- driver


# checks that share cached runs go to the same worker
GROUPS = (("P1", "P2"), ("H1", "W1", "W2", "W3", "BK2"), ("A1", "A2"))


def _run_group(ids: list[str], cfg: Config) -> list[tuple[Result, float]]:
    out = []
    for cid in ids:
        t = time.perf_counter()
        out.append((CHECKS[cid](cfg), time.perf_counter() - t))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="comma-separated check ids, e.g. P1,S2")
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--draws", type=int, default=40)
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--mutant", help="run under a broken variant from verify/mutants.py")
    args = ap.parse_args(argv)
    cfg = Config(tuple(range(1, args.seeds + 1)), args.days, args.draws)
    jobs = args.jobs
    if args.mutant:
        import mutants

        mutants.apply(args.mutant)
        jobs = 1
    ids = args.only.upper().split(",") if args.only else list(CHECKS)
    started, moved = time.perf_counter(), []
    if {"A1", "A2"} & set(ids):
        handoff(1)
    if "BK1" in ids:
        handoff(BAKER_SEED)
    tasks = [[c for c in g if c in ids] for g in GROUPS]
    grouped = {c for g in GROUPS for c in g}
    tasks = [t for t in tasks if t] + [[c] for c in ids if c not in grouped]
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as pool:
            done = list(pool.map(_run_group, tasks, [cfg] * len(tasks)))
    else:
        done = [_run_group(t, cfg) for t in tasks]
    results = {r.id: (r, t) for group in done for r, t in group}
    print(f"{'id':<4} {'verdict':<9} {'known':<9} claim / measured / expected / code")
    for cid in ids:
        r, t = results[cid]
        flag = "" if r.verdict == KNOWN[cid] else "  <-- CHANGED"
        if flag:
            moved.append(cid)
        print(f"{r.id:<4} {r.verdict:<9} {KNOWN[cid]:<9} {r.claim}{flag}")
        print(f"{'':<24} measured: {r.measured}   [{t:.1f}s]")
        print(f"{'':<24} expected: {r.expected}")
        print(f"{'':<24} code:     {r.where}")
        for line in r.detail:
            print(f"{'':<26} - {line}")
    print(
        f"\n{len(ids)} checks in {time.perf_counter() - started:.0f}s with {jobs} jobs; "
        f"{'verdicts as known' if not moved else 'changed: ' + ', '.join(moved)}"
    )
    return 1 if moved else 0


if __name__ == "__main__":
    sys.exit(main())
