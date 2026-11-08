import shutil
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bakery_world import machines as mc
from bakery_world.actions import apply_par_rules, open_doors
from bakery_world.basics import Account, Cents, date_of
from bakery_world.catalog import CATALOG
from bakery_world.curveballs import Curveball, draw_curveballs, morning
from bakery_world.engine import DayReport, post, simulate_day
from bakery_world.founder import repay_loan, service_ovens
from bakery_world.history import hidden_facts, resume
from bakery_world.machines import LocationState, MenuState, StaffState
from bakery_world.managed import (
    CHANNELS,
    MANAGER_HOURS_PER_WEEK,
    LogKind,
    ManagedWorld,
    home_assets,
)
from bakery_world.observe import dashboard
from bakery_world.records import Recorder, draw_reviews
from bakery_world.score import diagnostics, home_prices, net_worth
from bakery_world.town import HOME_LOCATION

DAYS_PER_WEEK = 7
DEMAND_WEEKS = 8


class Policy(Protocol):
    name: str
    keeps_founder: bool
    auto_hire: bool

    def weekly(self, world: ManagedWorld, week: int) -> None: ...

    def interrupt(self, world: ManagedWorld, event: Curveball) -> None: ...


@dataclass(frozen=True)
class RunResult:
    seed: int
    policy: str
    weeks: int
    bankrupt: bool
    net_worth_start: int
    net_worth_end: int
    diagnostics: dict
    log: list[str]
    seconds: float
    world: ManagedWorld

    @property
    def net_worth_change(self) -> int:
        return self.net_worth_end - self.net_worth_start


def load(source: Path | str, keeps_founder: bool = False, auto_hire: bool = False) -> ManagedWorld:
    path = Path(source)
    handoff = path / "handoff.pickle" if path.is_dir() else path
    founding = resume(handoff)
    world = founding.world
    mw = ManagedWorld(founding, hidden_facts(founding), keeps_founder=keeps_founder)
    mw.handoff_day = world.day
    mw.review_rng = mw.rng("reviews")
    mw.curveballs = draw_curveballs(
        founding.seed, world.business.locations[HOME_LOCATION].lease.end_day
    )
    mw.assets = home_assets(world)
    mw.handoff_prices = home_prices(mw)
    working = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)
    mw.handoff_headcount = sum(s.state in working for s in world.business.staff.values())
    world.business.auto_hire = auto_hire
    for (loc, item, weekday), sold in founding.founder.sold.items():
        mw.demand[(loc, item, weekday)] = deque(sold, maxlen=DEMAND_WEEKS)
    return mw


def _monthly(mw: ManagedWorld) -> None:
    world, day = mw.world, mw.day
    for loc in world.business.locations.values():
        if loc.state is LocationState.TRADING:
            spend = loc.marketing_cents_per_month
            post(world.business, day, Account.MARKETING, -spend, loc.id, "local advertising")
            service_ovens(world, day, loc)
    repay_loan(world, day)


def _standing(mw: ManagedWorld) -> None:
    day, b = mw.day, mw.world.business
    if not mw.keeps_founder and date_of(day).day == 1:
        _monthly(mw)
    for (loc_id, item), until in list(mw.trials.items()):
        entry = b.locations[loc_id].menu[item]
        if day >= until and entry.state is MenuState.TRIAL:
            entry.state = mc.step(
                b.transitions, day, mc.MENU_ITEM, item, entry.state, mc.MenuEvent.ADD_TO_MENU
            )
            del mw.trials[(loc_id, item)]
    for loc_id, opens in list(mw.openings.items()):
        loc = b.locations[loc_id]
        if loc.state is LocationState.FITTING_OUT:
            post(
                b,
                day,
                Account.RENT,
                -round(loc.lease.monthly_rent_cents * 12 / 365),
                loc_id,
                "rent during fit-out",
            )
            if day >= opens:
                open_doors(mw, loc)
                del mw.openings[loc_id]
    for key, stock in list(mw.ad_stock.items()):
        mw.ad_stock[key] = stock * (1 - CHANNELS[key[1]].daily_decay)
    for loc in b.locations.values():
        loc.campaign_cents_per_month = sum(
            stock * CHANNELS[ch].daily_decay * 30
            for (lid, ch), stock in mw.ad_stock.items()
            if lid == loc.id
        )
    for cid, c in list(mw.candidates.items()):
        if day >= c.expires_day and c.staff.state is not StaffState.GONE:
            c.staff.state = mc.step(
                b.transitions, day, mc.STAFF, cid, c.staff.state, mc.StaffEvent.REJECT
            )
        if c.staff.state is StaffState.GONE:
            del mw.candidates[cid]
    if (day - mw.handoff_day) % DAYS_PER_WEEK == 0:
        apply_par_rules(mw)


def _overdraft(mw: ManagedWorld) -> None:
    b = mw.world.business
    if b.cash_cents < 0:
        draw = min(b.credit_limit_cents - b.credit_drawn_cents, -b.cash_cents)
        if draw > 0:
            b.credit_drawn_cents = Cents(b.credit_drawn_cents + draw)
            post(b, mw.day - 1, Account.CREDIT_DRAWN, draw, None, "overdraft on credit line")


def _record(mw: ManagedWorld, report: DayReport) -> None:
    t = mw.totals
    for shop in report.locations:
        loc = mw.world.business.locations[shop.location_id]
        for item, d in shop.items.items():
            if not CATALOG[item].made_to_order:
                key = (shop.location_id, item, report.weekday)
                mw.demand.setdefault(key, deque(maxlen=DEMAND_WEEKS)).append(d.sold + d.unmet)
                t["baked"] += d.baked
                t["wasted"] += d.wasted
                t["item_days"] += d.baked > 0
                t["stockouts"] += d.first_stockout_hour is not None
        t["walkaways"] += shop.walkaways
        reviews = draw_reviews(mw.review_rng, loc, shop)
        for review in reviews:
            mw.reviews.append((report.day, shop.location_id, review.rating))
            t["stars"] += review.rating
            t["reviews"] += 1
        if mw.recorder:
            mw.recorder.insert_reviews(report.day, shop.location_id, reviews)
    mw.reports.append(report)
    if mw.recorder:
        mw.recorder.on_report(report, reviews=False)


def advance_day(mw: ManagedWorld) -> list[Curveball]:
    founding, b = mw.founding, mw.world.business
    marks = len(b.transitions), len(b.ledger)
    asked = morning(mw)
    _standing(mw)
    if mw.keeps_founder:
        founding.founder(mw.world, mw.day)
    report = simulate_day(mw.world, founding.rng)
    if mw.keeps_founder:
        founding.founder.observe(report)
    else:
        _overdraft(mw)
    _record(mw, report)
    fresh = b.transitions[marks[0] :]
    mw.totals["leavers"] += sum(
        t.entity == mc.STAFF.entity and t.after == StaffState.GONE.name and t.entity_id in b.staff
        for t in fresh
    )
    mw.log_transitions(fresh)
    if mw.recorder:
        mw.recorder.append_ledger(b.ledger[marks[1] :])
    return asked


def insolvent(mw: ManagedWorld) -> bool:
    b = mw.world.business
    return b.cash_cents + b.credit_limit_cents - b.credit_drawn_cents < 0


def _open_records(
    source: Path, out_dir: Path | None, mw: ManagedWorld
) -> sqlite3.Connection | None:
    records = source / "records.sqlite" if source.is_dir() else source.parent / "records.sqlite"
    if out_dir is None or not records.exists():
        return None
    target = out_dir / "records.sqlite"
    shutil.copyfile(records, target)
    conn = sqlite3.connect(target)
    mw.recorder = Recorder(conn, mw.world, mw.seed, resume=True)
    return conn


def manage(
    source: Path | str, policy: Policy, weeks: int = 52, out_dir: Path | str | None = None
) -> RunResult:
    started = time.perf_counter()
    source = Path(source)
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    mw = load(source, policy.keeps_founder, policy.auto_hire)
    stream = (out / "run.jsonl").open("w") if out else None
    mw.log.stream = stream
    conn = _open_records(source, out, mw)
    run, ev = mc.RunState.ONBOARDING, mc.RunEvent
    run_log: list = []

    def advance(event: mc.RunEvent) -> None:
        nonlocal run
        run = mc.step(run_log, mw.day, mc.RUN, policy.name, run, event)
        mw.log_transitions(run_log[-1:])

    start_nw = net_worth(mw)
    mw.note(LogKind.START, seed=mw.seed, policy=policy.name, weeks=weeks, net_worth_cents=start_nw)
    advance(ev.START)
    bankrupt, week = False, 0
    for week in range(weeks):
        mw.manager_hours = MANAGER_HOURS_PER_WEEK
        mw.note(LogKind.WEEK, week=week, dashboard=dashboard(mw))
        policy.weekly(mw, week)
        advance(ev.SUBMIT_TURN)
        for _ in range(DAYS_PER_WEEK):
            asked = advance_day(mw)
            if insolvent(mw):
                bankrupt = True
                break
            for cb in asked:
                advance(ev.INTERRUPT)
                mw.note(LogKind.INTERRUPT, event=cb.id, event_kind=cb.kind)
                policy.interrupt(mw, cb)
                advance(ev.RESUME)
        if bankrupt:
            advance(ev.GO_BANKRUPT)
            mw.note(LogKind.BANKRUPT, cash_cents=mw.world.business.cash_cents)
            break
        advance(ev.FINISH_YEAR if week == weeks - 1 else ev.FINISH_WEEK)
        if conn:
            conn.commit()
    end_nw = net_worth(mw, bankrupt)
    diag = diagnostics(mw)
    mw.note(LogKind.FINISH, net_worth_cents=end_nw, bankrupt=bankrupt, diagnostics=diag)
    if conn:
        mw.recorder.refresh_summaries()
        conn.commit()
        conn.close()
    if stream:
        stream.close()
    return RunResult(
        mw.seed,
        policy.name,
        week + 1,
        bankrupt,
        start_nw,
        end_nw,
        diag,
        mw.log.lines,
        round(time.perf_counter() - started, 2),
        mw,
    )
