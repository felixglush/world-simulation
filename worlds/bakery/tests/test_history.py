import json
import sqlite3
from pathlib import Path

import pytest

from bakery_world.basics import date_of
from bakery_world.calibrate import SHAPE, deseasonalise
from bakery_world.catalog import CATALOG
from bakery_world.cli import main
from bakery_world.decay import FORCES, Force, ForceId, draw_forces
from bakery_world.habits import HABITS, HabitId
from bakery_world.history import HISTORY_DAYS, found, generate_history, live, resume
from bakery_world.machines import CompetitorState
from bakery_world.town import RIVAL_ID

SEED = 1


def shop_revenue(
    seed: int, forces: tuple[Force, ...], days: int, after: int, launches: bool = True
) -> int:
    founding = found(seed, forces=forces)
    if not launches:
        founding.founder._launch_items = lambda world, day: None
    total = []

    def add(report) -> None:
        if report.day >= after:
            total.extend(s.revenue_cents for s in report.locations if s.location_id == "shop1")

    live(founding, days, add)
    return sum(total)


def early(fid: ForceId) -> Force:
    return Force(fid, onset_day=30, ramp_days=90, strength=FORCES[fid].strength[1])


@pytest.fixture(scope="module")
def baseline_revenue() -> int:
    return shop_revenue(SEED, (), 240, 120)


SLOW_FORCES = (ForceId.MENU_FATIGUE, ForceId.QUALITY_SLIP)


@pytest.mark.parametrize("fid", [f for f in ForceId if f not in SLOW_FORCES])
def test_each_decay_force_lowers_shop_revenue(fid: ForceId, baseline_revenue: int) -> None:
    assert shop_revenue(SEED, (early(fid),), 240, 120) < 0.99 * baseline_revenue


@pytest.fixture(scope="module")
def steady_menu_revenue() -> int:
    return shop_revenue(SEED, (), 360, 240, launches=False)


@pytest.mark.parametrize("fid", SLOW_FORCES)
def test_slow_forces_lower_revenue_once_new_items_stop(fid: ForceId, steady_menu_revenue) -> None:
    bored = shop_revenue(SEED, (early(fid),), 360, 240, launches=False)
    assert bored < 0.97 * steady_menu_revenue


def test_competitor_entry_opens_the_rival_only_when_the_force_acts() -> None:
    with_rival = found(SEED, forces=(early(ForceId.COMPETITOR_ENTRY),))
    without = found(SEED, forces=())
    live(with_rival, 40)
    live(without, 40)
    assert with_rival.world.competitors[RIVAL_ID].state is CompetitorState.OPEN
    assert without.world.competitors[RIVAL_ID].state is CompetitorState.RUMORED


def test_forces_differ_in_mix_by_seed_but_not_in_count() -> None:
    def dominant(seed: int) -> ForceId:
        def share(f: Force) -> float:
            low, high = FORCES[f.id].strength
            return (f.strength - low) / (high - low)

        return max(draw_forces(seed), key=share).id

    assert len({dominant(s) for s in range(1, 9)}) >= 3
    assert all(len(draw_forces(s)) == len(ForceId) for s in range(1, 9))
    assert ForceId.COMPETITOR_ENTRY not in {
        f.id for f in draw_forces(SEED, frozenset({ForceId.COMPETITOR_ENTRY}))
    }


@pytest.fixture
def history(seed1_history):
    return seed1_history


def rows(path: Path, sql: str, *args) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        found_rows = conn.execute(sql, args).fetchall()
    conn.close()
    return found_rows


def test_history_writes_three_years_of_dated_records(history) -> None:
    first, last = rows(history.records, "SELECT MIN(date), MAX(date) FROM sales")[0]
    assert first == "2023-01-02"
    assert last == date_of(HISTORY_DAYS - 1).isoformat()
    months = rows(history.records, "SELECT COUNT(DISTINCT month) FROM monthly_pnl")[0][0]
    assert months == 36


def test_records_reconcile_with_the_ledger(history) -> None:
    def ledger(account: str) -> int:
        sql = "SELECT SUM(amount_cents) FROM ledger WHERE account = ?"
        return rows(history.records, sql, account)[0][0]

    sales = rows(history.records, "SELECT SUM(total_cents) FROM sales")[0][0]
    items = rows(history.records, "SELECT SUM(quantity * unit_price_cents) FROM sale_items")[0][0]
    invoiced = rows(history.records, "SELECT SUM(amount_cents) FROM invoices")[0][0]
    wages = rows(history.records, "SELECT SUM(pay_cents) FROM shifts")[0][0]
    assert sales == items == ledger("sales")
    assert invoiced == -ledger("ingredients")
    assert wages == -ledger("wages")


def test_hidden_facts_never_reach_the_records(history) -> None:
    hidden = json.loads(history.hidden.read_text())
    secrets = [h.description for h in HABITS.values()]
    secrets += [f.value for f in ForceId] + [h.value for h in HabitId]
    secrets += [repr(v) for v in hidden["params"].values() if isinstance(v, float)]
    secrets += [repr(v) for group in hidden["params"]["taste"].values() for v in group.values()]
    blob = history.records.read_bytes()
    assert not [s for s in secrets if s.encode() in blob]
    columns = {
        c[1]
        for (t,) in rows(history.records, "SELECT name FROM sqlite_master WHERE type='table'")
        for c in rows(history.records, f"PRAGMA table_info({t})")
    }
    assert not columns & {"skill", "morale", "speed", "elasticity", "reputation", "awareness"}
    assert not [c for c in columns if "clean" in c or "strain" in c or "walk" in c]
    assert set(hidden) >= {"seed", "params", "forces", "habits"}
    assert all(h["description"] for h in hidden["habits"].values())


def test_sales_carry_the_minute_of_the_hour(history) -> None:
    times = rows(history.records, "SELECT hour, time FROM sales")
    assert all(time[:2] == f"{hour:02d}" and len(time) == 5 for hour, time in times)
    assert len({time[3:] for _, time in times}) == 60
    shifts = rows(history.records, "SELECT start_hour, end_hour, hours FROM shifts LIMIT 500")
    assert all(end - start == hours > 0 for start, end, hours in shifts)


def test_recipes_joined_with_supplier_prices_give_a_cost_per_item(history) -> None:
    sql = (
        "SELECT r.item, SUM(r.grams_per_unit * p.price) / 1000.0, MAX(r.labor_minutes_per_unit) "
        "FROM recipes r JOIN (SELECT ingredient, MIN(list_price_cents_per_kg) AS price "
        "FROM suppliers WHERE month = '2025-12' GROUP BY ingredient) p USING (ingredient) "
        "GROUP BY r.item"
    )
    cost = {item: (cents, labor) for item, cents, labor in rows(history.records, sql)}
    assert len(cost) == len(CATALOG)
    flour, butter = (
        rows(
            history.records,
            "SELECT MIN(list_price_cents_per_kg) FROM suppliers "
            "WHERE month = '2025-12' AND ingredient = ?",
            ing,
        )[0][0]
        for ing in ("flour", "butter")
    )
    assert cost["croissant"][0] == pytest.approx((60 * flour + 35 * butter) / 1000)
    assert cost["croissant"][1] == CATALOG["croissant"].labor_minutes > 0
    assert 0 < cost["croissant"][0] < CATALOG["croissant"].typical_price_cents


def test_documents_cover_the_notebook_contracts_and_news(history) -> None:
    kinds = dict(rows(history.records, "SELECT kind, COUNT(*) FROM documents GROUP BY kind"))
    assert 35 <= kinds["notebook"] <= 50
    assert kinds["supplier_contract"] == 2 and kinds["loan"] == 1 and kinds["lease"] >= 1
    handoff = date_of(HISTORY_DAYS).isoformat()
    lease = rows(history.records, "SELECT body FROM documents WHERE title LIKE '%Market%'")[0][0]
    assert "to 2026-08-01" in lease
    news = rows(history.records, "SELECT date, title, body FROM documents WHERE kind = 'news'")
    wage = next(n for n in news if "Minimum wage" in n[1])
    assert wage[0] < handoff and "April 2026" in wage[2]
    opened = next(n[0] for n in news if "opens" in n[1])
    assert any("plans" in n[1] and n[0] < opened for n in news)
    notes = rows(history.records, "SELECT date, body FROM documents WHERE kind = 'notebook'")
    first_year = [len(b) for d, b in notes if d < "2024-01-01"]
    last_year = [len(b) for d, b in notes if d >= "2025-01-01"]
    assert sum(last_year) / len(last_year) < 0.6 * sum(first_year) / len(first_year)


def test_dearer_butter_supplier_and_its_cheaper_rival_are_both_on_record(tmp_path) -> None:
    result = generate_history(SEED, tmp_path, days=40, habits=(HabitId.DEAR_BUTTER,))
    sql = "SELECT DISTINCT supplier FROM invoices WHERE ingredient = 'butter'"
    invoiced = {s for (s,) in rows(result.records, sql)}
    prices = dict(
        rows(
            result.records,
            "SELECT supplier, list_price_cents_per_kg FROM suppliers "
            "WHERE ingredient = 'butter' AND month = '2023-02'",
        )
    )
    assert invoiced == {"hartley"}
    assert prices["hartley"] > 1.1 * prices["greenfield"]


def test_early_week_sourdough_overbake_shows_in_the_bake_log(tmp_path) -> None:
    result = generate_history(SEED, tmp_path, days=150, habits=(HabitId.EARLY_WEEK_SOURDOUGH,))
    baked_per_sold = dict(
        rows(
            result.records,
            "SELECT day % 7 IN (0, 1), 1.0 * SUM(baked) / SUM(sold) FROM bake_log "
            "WHERE item = 'sourdough' AND day >= 60 AND day % 7 < 5 GROUP BY 1",
        )
    )
    assert baked_per_sold[1] > 1.25 * baked_per_sold[0]


def test_same_seed_writes_byte_identical_records(tmp_path) -> None:
    a = generate_history(3, tmp_path / "a", days=60)
    b = generate_history(3, tmp_path / "b", days=60)
    c = generate_history(4, tmp_path / "c", days=60)
    assert a.content_hash == b.content_hash != c.content_hash
    assert a.records.read_bytes() == b.records.read_bytes()
    assert a.hidden.read_bytes() == b.hidden.read_bytes()


def test_handoff_resumes_exactly_where_the_history_stopped(tmp_path) -> None:
    result = generate_history(SEED, tmp_path, days=60)
    resumed = resume(result.handoff)
    straight = found(SEED)
    live(straight, 60)
    tail = len(straight.world.business.ledger)
    live(resumed, 30)
    live(straight, 30)
    assert resumed.world.day == straight.world.day == 90
    assert resumed.world.business.ledger[tail:] == straight.world.business.ledger[tail:]
    assert resumed.world.business.cash_cents == straight.world.business.cash_cents


def test_envelope_flags_a_history_that_never_declines() -> None:
    rising = [1000.0 * (10 + m) for m in range(36)]
    flat_profit = [1000.0] * 36
    results = {c.name: c.passed for c in (check(rising, flat_profit) for check in SHAPE)}
    assert results["year 1 grows"] is True
    assert results["year 2 flat"] is False
    assert results["year 3 declines"] is False


def test_deseasonalising_removes_a_repeating_calendar_swing() -> None:
    swing = (0.9, 0.95, 1.0, 1.0, 1.0, 0.95, 0.95, 1.0, 1.0, 1.05, 1.05, 1.15)
    seasonal = [1000.0 * swing[m % 12] for m in range(36)]
    assert max(deseasonalise(seasonal)) / min(deseasonalise(seasonal)) == pytest.approx(1.0)


def test_calibrate_cli_rejects_a_malformed_seed_range() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["calibrate", "--seeds", "one-eight"])
    assert exit_info.value.code == 2
