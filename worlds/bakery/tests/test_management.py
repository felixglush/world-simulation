import json
import pickle
import sqlite3
from dataclasses import dataclass, field

import pytest

from bakery_world.actions import ACTIONS, ActionRefused, describe_actions, perform
from bakery_world.baselines import Founder, Freeze, Heuristic
from bakery_world.basics import Cents, date_of
from bakery_world.curveballs import Curveball
from bakery_world.machines import LocationState, StaffState
from bakery_world.managed import ManagedWorld
from bakery_world.management import advance_day, load, manage
from bakery_world.observe import candidates, dashboard, open_events
from bakery_world.town import HOME_LOCATION


@pytest.fixture
def mw(seed1_history) -> ManagedWorld:
    world = load(seed1_history.handoff)
    world.world.business.credit_limit_cents = Cents(50_000_000)
    return world


def frozen(mw: ManagedWorld) -> bytes:
    return pickle.dumps((mw.world, mw.candidates, mw.sites, mw.assets, mw.par_rules))


@pytest.mark.parametrize(
    "name, args",
    [
        ("set_price", {"location": "shop1", "item": "croissant", "price_cents": -100}),
        ("set_price", {"location": "shop1", "item": "unicorn", "price_cents": 400}),
        ("set_price", {"location": "shop1", "item": "eclair", "price_cents": 400}),
        ("set_price", {"location": "shop9", "item": "croissant", "price_cents": 400}),
        ("set_price", {"location": "shop1", "item": "croissant"}),
        ("set_hours", {"location": "shop2", "open_hour": 7, "close_hour": 18}),
        ("add_menu_item", {"location": "shop1", "item": "croissant"}),
        ("hire", {"candidate": "s99", "wage_cents_per_hour": 2000}),
        ("respond_to_event", {"event": "event01", "option": "accept"}),
        ("set_bake_plan", {"location": "shop1", "item": "latte", "weekday_units": [1] * 7}),
        ("set_wage", {"staff": "owner", "wage_cents_per_hour": 3000}),
        ("post_job", {"location": "shop1", "role": "baker", "wage_cents_per_hour": 1100}),
        ("launch_rocket", {}),
    ],
)
def test_refused_actions_change_nothing(mw: ManagedWorld, name: str, args: dict) -> None:
    before = frozen(mw)
    with pytest.raises(ActionRefused):
        perform(mw, name, **args)
    assert frozen(mw) == before
    assert json.loads(mw.log.lines[-1])["kind"] == "refusal"


def test_a_fourth_location_is_refused(mw: ManagedWorld) -> None:
    for nb in ("station", "oakwood"):
        perform(mw, "scout_site", neighborhood=nb)
        perform(mw, "sign_lease", site=f"site-{nb}")
    perform(mw, "scout_site", neighborhood="campus")
    before = frozen(mw)
    with pytest.raises(ActionRefused, match="already has 3"):
        perform(mw, "sign_lease", site="site-campus")
    assert frozen(mw) == before


def test_a_hire_goes_through_interview_and_offer(mw: ManagedWorld) -> None:
    perform(mw, "post_job", location="shop1", role="counter", wage_cents_per_hour=2500)
    for _ in range(7):
        advance_day(mw)
    applicant = candidates(mw)[0]["candidate"]
    with pytest.raises(ActionRefused, match="must be interviewed"):
        perform(mw, "hire", candidate=applicant, wage_cents_per_hour=2500)
    perform(mw, "interview", candidate=applicant)
    result = perform(mw, "hire", candidate=applicant, wage_cents_per_hour=4000)
    assert result.data["hired"]
    assert mw.world.business.staff[applicant].state is StaffState.EMPLOYED
    path = [(t.before, t.after) for t in mw.world.business.transitions if t.entity_id == applicant]
    assert path == [
        ("CANDIDATE", "INTERVIEWED"),
        ("INTERVIEWED", "OFFERED"),
        ("OFFERED", "EMPLOYED"),
    ]


def test_a_low_offer_is_declined(mw: ManagedWorld) -> None:
    perform(mw, "post_job", location="shop1", role="baker", wage_cents_per_hour=2000)
    for _ in range(7):
        advance_day(mw)
    applicant = candidates(mw)[0]["candidate"]
    perform(mw, "interview", candidate=applicant)
    result = perform(mw, "hire", candidate=applicant, wage_cents_per_hour=1750)
    assert not result.data["hired"]
    assert applicant not in mw.world.business.staff


def test_a_new_site_trades_only_after_fit_out(mw: ManagedWorld) -> None:
    perform(mw, "scout_site", neighborhood="station")
    opened = perform(mw, "sign_lease", site="site-station").data
    shop = mw.world.business.locations[opened["location"]]
    states = []
    while mw.day <= opened["opens_day"]:
        states.append((mw.day, shop.state))
        advance_day(mw)
    assert all(s is LocationState.FITTING_OUT for d, s in states if d < opened["opens_day"])
    assert shop.state is LocationState.TRADING
    assert shop.menu and dashboard(mw)["locations"][shop.id]["state"] == "trading"


def test_actions_describe_their_arguments() -> None:
    schemas = {s["name"]: s for s in describe_actions()}
    assert set(schemas) == set(ACTIONS)
    price = schemas["set_price"]["arguments"]["price_cents"]
    assert price["type"] == "integer" and price["minimum"] > 0
    assert schemas["set_bake_plan"]["arguments"]["weekday_units"]["minItems"] == 7


def test_the_lease_renewal_falls_inside_the_year(mw: ManagedWorld) -> None:
    end = mw.world.business.locations[HOME_LOCATION].lease.end_day
    assert mw.day + 180 <= end <= mw.day + 240
    assert date_of(end).isoformat() == "2026-08-01"


@dataclass
class Recording(Freeze):
    name: str = "recording"
    events: list[str] = field(default_factory=list)

    def interrupt(self, world: ManagedWorld, event: Curveball) -> None:
        self.events.append(event.id)
        assert any(e["event"] == event.id for e in open_events(world))


def test_curveballs_arrive_on_the_same_days_for_every_policy(seed1_history) -> None:
    def timings(policy) -> list[tuple]:
        run = manage(seed1_history.handoff, policy, weeks=3)
        return [(c.id, c.kind, c.start_day, c.foreshadow_day) for c in run.world.curveballs]

    assert timings(Freeze()) == timings(Heuristic()) == timings(Founder())


def test_an_urgent_curveball_interrupts_the_week(seed1_history) -> None:
    mw = load(seed1_history.handoff)
    first = min((c for c in mw.curveballs if c.spec.urgent), key=lambda c: c.start_day)
    policy = Recording()
    manage(seed1_history.handoff, policy, weeks=(first.start_day - mw.day) // 7 + 1)
    assert policy.events[:1] == [first.id]


def test_the_founder_scores_zero_against_itself(seed1_history) -> None:
    a = manage(seed1_history.handoff, Founder(), weeks=4)
    b = manage(seed1_history.handoff, Founder(), weeks=4)
    assert a.net_worth_change - b.net_worth_change == 0
    assert a.log == b.log


def test_the_run_log_is_deterministic(seed1_history) -> None:
    a = manage(seed1_history.handoff, Heuristic(), weeks=3)
    b = manage(seed1_history.handoff, Heuristic(), weeks=3)
    assert a.log == b.log
    kinds = {json.loads(line)["kind"] for line in a.log}
    assert {"action", "transition", "week"} <= kinds


@dataclass
class Spendthrift(Freeze):
    name: str = "spendthrift"
    auto_hire: bool = False

    def weekly(self, world: ManagedWorld, week: int) -> None:
        for channel in ("flyers", "social", "local_paper"):
            try:
                perform(
                    world,
                    "spend_marketing",
                    location="shop1",
                    channel=channel,
                    amount_cents=1_000_000,
                )
            except ActionRefused:
                world.world.business.cash_cents = Cents(world.world.business.cash_cents - 1_000_000)


def test_bankruptcy_ends_the_run(seed1_history) -> None:
    run = manage(seed1_history.handoff, Spendthrift(), weeks=52)
    assert run.bankrupt and run.weeks < 52
    assert json.loads(run.log[-2])["kind"] == "bankrupt"


def test_management_days_are_appended_to_the_records(seed1_history, tmp_path) -> None:
    manage(seed1_history.records.parent, Heuristic(), weeks=2, out_dir=tmp_path)
    with sqlite3.connect(tmp_path / "records.sqlite") as conn:
        last_sale = conn.execute("SELECT MAX(date) FROM sales").fetchone()[0]
        ledger = conn.execute("SELECT MAX(date) FROM ledger").fetchone()[0]
        bakes = conn.execute("SELECT COUNT(*) FROM bake_log WHERE day >= 1095").fetchone()[0]
    conn.close()
    assert last_sale == ledger == date_of(1095 + 13).isoformat()
    assert bakes > 0
    assert (tmp_path / "run.jsonl").read_text().count("\n") > 10


@dataclass
class OneCampaign(Freeze):
    name: str = "one campaign"

    def weekly(self, world: ManagedWorld, week: int) -> None:
        if week == 0:
            perform(
                world, "spend_marketing", location="shop1", channel="flyers", amount_cents=50_000
            )


def test_ledger_entries_from_weekly_actions_reach_the_records(seed1_history, tmp_path) -> None:
    manage(seed1_history.records.parent, OneCampaign(), weeks=1, out_dir=tmp_path)
    with sqlite3.connect(tmp_path / "records.sqlite") as conn:
        rows = conn.execute(
            "SELECT amount_cents FROM ledger WHERE day >= 1095 AND memo = 'flyers campaign'"
        ).fetchall()
    conn.close()
    assert rows == [(-50_000,)]
