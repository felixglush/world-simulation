import json
import random

import pytest

from bakery_world import machines as mc
from bakery_world.basics import ItemId, Role
from bakery_world.catalog import CATALOG
from bakery_world.cli import main
from bakery_world.engine import DayReport, run_days, simulate_day
from bakery_world.machines import CustomerState, LocationState, StaffState
from bakery_world.model import World
from bakery_world.town import HOME_LOCATION, build_world

SHOP = HOME_LOCATION


def idle(world: World, day: int) -> None:
    return None


def run(seed: int, days: int, tweak=None) -> tuple[World, list[DayReport]]:
    world = build_world(seed)
    if tweak:
        tweak(world)
    return world, run_days(world, days, idle, random.Random(seed + 100))


def shop(world: World):
    return world.business.locations[SHOP]


def item_units_and_revenue(reports: list[DayReport], item: str) -> tuple[int, int]:
    lines = [
        line
        for r in reports
        for s in r.locations[0].sales
        for line in s.lines
        if line.item_id == item
    ]
    return len(lines), sum(line.price_cents for line in lines)


def test_same_seed_gives_identical_reports():
    _, a = run(5, 30)
    _, b = run(5, 30)
    assert a == b


def test_different_seeds_differ():
    _, a = run(5, 30)
    _, b = run(6, 30)
    assert a != b


def test_raising_a_price_lowers_that_items_units_but_not_to_zero_revenue():
    def triple_latte(world: World) -> None:
        shop(world).menu[ItemId("latte")].price_cents *= 3

    _, base = run(2, 30)
    _, dear = run(2, 30, triple_latte)
    base_units, base_rev = item_units_and_revenue(base, "latte")
    dear_units, dear_rev = item_units_and_revenue(dear, "latte")
    assert dear_units < 0.7 * base_units
    assert 0 < dear_rev < 3 * base_rev


def test_shop_with_no_bakers_bakes_nothing_but_still_sells_drinks():
    def sack_bakers(world: World) -> None:
        for s in world.business.staff.values():
            if s.role is Role.BAKER:
                s.state = StaffState.GONE

    _, reports = run(1, 10, sack_bakers)
    items = [i for r in reports for i in r.locations[0].items.items()]
    assert all(stat.baked == 0 for _, stat in items)
    sold = {k for k, stat in items if stat.sold}
    assert sold and all(CATALOG[k].made_to_order for k in sold)


def test_closing_at_15_removes_afternoon_sales_that_18_has():
    def close_early(world: World) -> None:
        shop(world).close_hour = 15

    _, late = run(3, 14)
    _, early = run(3, 14, close_early)

    def afternoon(reports):
        return sum(len(s.lines) for r in reports for s in r.locations[0].sales if s.hour >= 15)

    assert afternoon(late) > 0
    assert afternoon(early) == 0
    assert sum(r.locations[0].arrivals_after_close for r in early) > 0


def test_regulars_who_stop_visiting_become_lapsed():
    def shut_shop(world: World) -> None:
        loc = shop(world)
        loc.state = mc.step(
            [], 0, mc.LOCATION, loc.id, loc.state, mc.LocationEvent.ANNOUNCE_CLOSING
        )
        loc.state = mc.step([], 0, mc.LOCATION, loc.id, loc.state, mc.LocationEvent.SHUT_DOORS)
        for c in world.customers:
            c.state = CustomerState.REGULAR
            c.last_visit_day = 0

    world, reports = run(1, 31, shut_shop)
    assert all(not r.locations for r in reports)
    assert {c.state for c in world.customers} == {CustomerState.LAPSED}
    assert any(t.event == "GO_QUIET" and t.before == "REGULAR" for t in world.business.transitions)


def test_lapsed_customers_churn_after_90_days():
    def shut_shop(world: World) -> None:
        shop(world).state = LocationState.CLOSED
        for c in world.customers:
            c.state = CustomerState.LAPSED
            c.state_since_day = 0

    world, _ = run(1, 91, shut_shop)
    assert {c.state for c in world.customers} == {CustomerState.CHURNED}


def test_cash_change_equals_revenue_minus_costs():
    world = build_world(4)
    rng = random.Random(1)
    for _ in range(5):
        before = world.business.cash_cents
        report = simulate_day(world, rng)
        day = report.locations[0]
        assert day.revenue_cents == sum(line.price_cents for s in day.sales for line in s.lines)
        assert report.cash_cents - before == day.revenue_cents - day.costs_cents
        assert day.card_fees_cents == round(day.revenue_cents * 0.02)
        assert day.wages_cents > 0 and day.rent_cents > 0 and day.ingredient_cost_cents > 0


def test_unsold_items_are_wasted_after_their_shelf_life_and_sold_day_old_at_half_price():
    world = build_world(1)
    menu = shop(world).menu
    croissant, sourdough = ItemId("croissant"), ItemId("sourdough")
    menu[croissant].bake_plan = [60] * 7
    menu[sourdough].bake_plan = [60] * 7
    rng = random.Random(7)
    day0 = simulate_day(world, rng).locations[0]
    day1 = simulate_day(world, rng).locations[0]
    assert CATALOG[croissant].shelf_life_days == 1
    assert CATALOG[sourdough].shelf_life_days == 2
    unsold = day0.items[croissant].baked - day0.items[croissant].sold
    assert unsold > 0
    assert day1.items[croissant].wasted == unsold
    assert day1.items[sourdough].day_old_sold > 0
    day_old = [s_ for s in day1.sales for s_ in s.lines if s_.day_old]
    assert day_old
    for line in day_old:
        assert line.price_cents == round(menu[line.item_id].price_cents * 0.5)


def test_selling_out_records_the_first_stockout_hour_and_unmet_demand():
    def scarce(world: World) -> None:
        shop(world).menu[ItemId("croissant")].bake_plan = [3] * 7

    _, reports = run(1, 5, scarce)
    stats = [r.locations[0].items[ItemId("croissant")] for r in reports]
    assert all(s.baked == 3 for s in stats)
    assert all(s.first_stockout_hour is not None for s in stats)
    assert sum(s.unmet for s in stats) > 0


def test_no_working_ovens_means_nothing_is_baked():
    def break_ovens(world: World) -> None:
        for oven in shop(world).ovens:
            oven.state = mc.EquipmentState.REPLACED

    _, reports = run(1, 3, break_ovens)
    assert all(i.baked == 0 for r in reports for i in r.locations[0].items.values())


def test_no_counter_staff_means_every_visitor_walks_away():
    def sack_counters(world: World) -> None:
        for s in world.business.staff.values():
            if s.role is Role.COUNTER:
                s.state = StaffState.GONE

    _, reports = run(1, 3, sack_counters)
    for r in reports:
        day = r.locations[0]
        assert day.visits > 0 and day.walkaways == day.visits and not day.sales


def test_a_resigned_worker_leaves_and_is_replaced_in_the_same_role():
    world = build_world(1)
    baker = next(s for s in world.business.staff.values() if s.role is Role.BAKER)
    baker.state = StaffState.ON_NOTICE
    baker.state_until_day = 0
    before = len(world.business.staff)
    run_days(world, 12, idle, random.Random(1))
    assert baker.state is StaffState.GONE
    hires = [s for s in list(world.business.staff.values())[before:]]
    assert [h.role for h in hires] == [Role.BAKER]
    assert hires[0].state is StaffState.EMPLOYED
    path = [t.event for t in world.business.transitions if t.entity_id == hires[0].id]
    assert path == ["INTERVIEW", "OFFER", "ACCEPT"]


def test_new_customers_become_regular_after_four_visits_in_30_days():
    world, _ = run(1, 60)
    regulars = [c for c in world.customers if c.state is CustomerState.REGULAR]
    assert regulars
    visits = [t for t in world.business.transitions if t.event == "VISIT_REPEATEDLY"]
    assert visits


def test_starting_shop_revenue_is_in_the_expected_band():
    _, reports = run(1, 60)
    mean = sum(r.locations[0].revenue_cents for r in reports) / 60 / 100
    assert 1200 <= mean <= 1800


def test_run_days_calls_policy_before_each_day():
    seen = []
    world = build_world(1)
    run_days(world, 3, lambda w, d: seen.append((d, w.day)), random.Random(1))
    assert seen == [(0, 0), (1, 1), (2, 2)]


def test_cli_prints_one_json_line_per_day(capsys):
    assert main(["simulate", "--seed", "1", "--days", "3"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["day"] for r in rows] == [0, 1, 2]
    assert set(rows[0]) == {
        "day",
        "revenue_cents",
        "units_sold",
        "waste_units",
        "walkaways",
        "cash_cents",
    }


@pytest.mark.parametrize("args", [["--seed", "1", "--days", "0"], ["--seed", "x", "--days", "5"]])
def test_cli_rejects_bad_arguments(args):
    with pytest.raises(SystemExit):
        main(["simulate", *args])
