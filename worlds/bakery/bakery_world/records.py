import hashlib
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import NamedTuple

from bakery_world import machines as mc
from bakery_world.basics import (
    START_DATE,
    Account,
    Ingredient,
    ItemId,
    LocationId,
    StaffId,
    date_of,
)
from bakery_world.books import OPERATING, SECTION, Section, monthly_by_account
from bakery_world.catalog import CATALOG
from bakery_world.decay import FORCES, ForceId
from bakery_world.engine import LOAN_APR, DayReport, LocationDay
from bakery_world.founder import (
    LOAN_PRINCIPAL_PER_MONTH_CENTS,
    SECOND_SHOP,
    SECOND_SHOP_BREAK_MONTHS,
)
from bakery_world.habits import HabitId
from bakery_world.model import LedgerEntry, Location, World
from bakery_world.town import COMPETITORS, HOME_LOCATION, OWNER_NAME, RIVAL_ID, SHOP_NAME

TABLES: dict[str, tuple[str, ...]] = {
    "sales": (
        "sale_id INTEGER PRIMARY KEY",
        "day INTEGER",
        "date TEXT",
        "hour INTEGER",
        "location TEXT",
        "loyalty_id TEXT",
        "payment TEXT",
        "total_cents INTEGER",
    ),
    "sale_items": (
        "sale_id INTEGER",
        "item TEXT",
        "quantity INTEGER",
        "unit_price_cents INTEGER",
        "day_old INTEGER",
    ),
    "bake_log": (
        "day INTEGER",
        "date TEXT",
        "location TEXT",
        "item TEXT",
        "baked INTEGER",
        "sold INTEGER",
        "day_old_sold INTEGER",
        "wasted INTEGER",
        "first_stockout_hour INTEGER",
    ),
    "ledger": (
        "entry_id INTEGER PRIMARY KEY",
        "day INTEGER",
        "date TEXT",
        "location TEXT",
        "account TEXT",
        "section TEXT",
        "amount_cents INTEGER",
        "memo TEXT",
    ),
    "monthly_pnl": (
        "month TEXT",
        "location TEXT",
        "revenue_cents INTEGER",
        "cost_of_sales_cents INTEGER",
        "operating_expense_cents INTEGER",
        "operating_profit_cents INTEGER",
        "finance_cents INTEGER",
        "one_off_cents INTEGER",
        "net_profit_cents INTEGER",
    ),
    "shifts": (
        "day INTEGER",
        "date TEXT",
        "location TEXT",
        "staff_id TEXT",
        "role TEXT",
        "hours INTEGER",
        "pay_cents INTEGER",
    ),
    "staff": (
        "staff_id TEXT PRIMARY KEY",
        "name TEXT",
        "role TEXT",
        "location TEXT",
        "hired_date TEXT",
        "left_date TEXT",
        "status TEXT",
        "wage_cents_per_hour INTEGER",
    ),
    "staff_wages": ("staff_id TEXT", "effective_date TEXT", "wage_cents_per_hour INTEGER"),
    "invoices": (
        "invoice_id INTEGER",
        "date TEXT",
        "supplier TEXT",
        "location TEXT",
        "ingredient TEXT",
        "kg REAL",
        "price_cents_per_kg INTEGER",
        "amount_cents INTEGER",
    ),
    "suppliers": (
        "supplier TEXT",
        "supplier_name TEXT",
        "month TEXT",
        "ingredient TEXT",
        "list_price_cents_per_kg INTEGER",
    ),
    "menu_history": (
        "day INTEGER",
        "date TEXT",
        "location TEXT",
        "item TEXT",
        "item_name TEXT",
        "change TEXT",
        "status TEXT",
        "price_cents INTEGER",
    ),
    "reviews": (
        "review_id INTEGER PRIMARY KEY",
        "date TEXT",
        "location TEXT",
        "rating INTEGER",
        "text TEXT",
    ),
    "recipes": (
        "item TEXT",
        "name TEXT",
        "category TEXT",
        "ingredient TEXT",
        "grams_per_unit INTEGER",
        "labor_minutes_per_unit REAL",
        "shelf_life_days INTEGER",
    ),
    "documents": (
        "doc_id INTEGER PRIMARY KEY",
        "date TEXT",
        "kind TEXT",
        "title TEXT",
        "body TEXT",
    ),
}

TEMPLATES: dict[str, tuple[str, ...]] = {
    "review_5": (
        "Best {item} in town. The staff remember my order.",
        "Lovely little bakery. The {item} was still warm.",
        "Worth the walk. Get the {item}.",
    ),
    "review_4": (
        "Good {item}, friendly counter. A bit pricey.",
        "Solid neighbourhood bakery. {item} was very good.",
        "Nice spot for a coffee and a {item}.",
    ),
    "review_3": (
        "Fine. The {item} was okay, nothing special.",
        "Decent but inconsistent. Some days great, some days not.",
        "Average {item}. Coffee was alright.",
    ),
    "review_2": (
        "Queue was out the door and moved slowly. Gave up on the {item}.",
        "Came at lunch and half the shelf was empty.",
        "The {item} tasted like yesterday's.",
    ),
    "review_1": (
        "Waited forever and they had run out of {item}. Not again.",
        "Stale {item}, rude wait. Disappointing.",
    ),
    "review_wait": (
        "Long wait at the counter this morning.",
        "Only one person serving and a long line.",
    ),
    "review_sold_out": (
        "Sold out of {item} before noon.",
        "No {item} left by the time I got there.",
    ),
    "note_open_y1": (
        "Opening day. {shop} is real. Doors at seven, a queue of six people, and I burned the "
        "first tray of croissants. Sold out of {item} by eleven. Tired and very happy.",
    ),
    "note_month_y1": (
        "{month}: about ${revenue}k through the till, {trend} on last month. People are starting "
        "to come back by name. I think the {item} is what brings them.",
        "End of {month}. Roughly ${revenue}k, {trend}. Still learning the ovens. Regulars are "
        "asking for more {item}; I'll keep baking extra.",
        "{month} done. ${revenue}k, {trend}. Good feeling in the shop. More new faces every week.",
    ),
    "note_month_y2": (
        "{month}: ${revenue}k, {trend}. Steady. Same faces, which is good.",
        "{month}. About ${revenue}k, {trend}. Can't complain.",
        "{month}: ${revenue}k. {trend}. Busy mornings, quiet after.",
    ),
    "note_month_y3": (
        "{month}. ${revenue}k. {trend}.",
        "{month}: ${revenue}k, {trend}. Long month.",
        "{month}. ${revenue}k. Tired.",
    ),
    "note_launch_y1": (
        "Trying {item} from today. If it sells I'll keep it. Customers keep asking for something "
        "new and this one smells incredible.",
        "Started {item} on trial. Fingers crossed. The early test batches went in minutes.",
    ),
    "note_launch_y2": ("{item} on trial from today. Let's see.",),
    "note_review": (
        "Went through all the prices today against what flour and butter cost now. Moved a few "
        "up. I'm not doing this again for a while; people hate price changes.",
    ),
    "note_raise": (
        "Gave everyone a 3% raise. They've earned it.",
        "3% raise for the team today. Good people are hard to find.",
    ),
    "note_shop2_sign": (
        "Signed for a second shop in Maple Grove. {m2} square metres, rent ${rent} a month. "
        "Everyone says it's quiet there but that means no competition. Exciting.",
    ),
    "note_shop2_open": (
        "Maple Grove is open. Slow first day, but it's a start. Running between two shops is "
        "harder than I thought.",
    ),
    "note_shop2_close": (
        "Closed Maple Grove today. Paid to get out of the lease. Never filled up. Lesson learned, "
        "expensively.",
    ),
    "note_rival_rumor": (
        "Heard a chain is looking at the old bank building on the square. Probably nothing.",
    ),
    "note_rival_open": (
        "{rival} opened across the square. Cheap coffee, loud music. Different crowd from ours.",
    ),
    "note_rival_dismiss": (
        "{rival} is mostly students on laptops. Not our customers. Not worried.",
    ),
    "note_hybrid": (
        "Read that {employer} is going hybrid. Offices will still be full most days, I'm sure.",
    ),
    "note_staff_left": (
        "{name} left today. Have to find someone new. Again.",
        "Lost {name}. Short-handed until we hire.",
    ),
    "note_belief_cheap_croissant": (
        "Croissant price is exactly right. It's the thing people come in for; never touch it.",
    ),
    "note_belief_early_week_sourdough": (
        "Mondays and Tuesdays everyone wants sourdough. Keeping the extra loaves on those days.",
    ),
    "note_belief_early_close": (
        "Nobody is around after three. Closing at 15:00 is the right call and I get my afternoons.",
    ),
    "note_belief_slow_loyal_baker": (
        "{name} is the heart of the kitchen. Slow but careful. I'd never let them go.",
    ),
    "note_belief_dear_butter": (
        "Hartley's butter is worth it. You can taste the difference. Cheaper butter would show.",
    ),
    "note_belief_unpriced_flour_spike": (
        "Flour's up again. It'll come back down. Not raising bread prices over this.",
    ),
    "note_belief_weather": ("Quiet. It's the weather. Always quiet when it's like this.",),
    "news_rival_rumor": (
        "{rival}, the regional coffee-and-bakery chain, has applied for permits on the former "
        "bank building in {area}. A spokesperson said the company is 'excited about the "
        "neighbourhood' and expects to open later this year with student discounts and a "
        "commuter loyalty card.",
    ),
    "news_rival_open": (
        "{rival} opened its {area} branch this week with queues for its two-for-one coffee "
        "offer. The chain targets students and commuters.",
    ),
    "news_hybrid": (
        "{employer}, the largest office employer in {area}, will move to hybrid working, with "
        "most staff in the office two days a week. The firm is also subletting two floors.",
    ),
    "news_min_wage": (
        "The state minimum wage will rise from ${old} to ${new} an hour on {effective}. Small "
        "employers in food service say the change will add to already rising costs.",
    ),
    "news_flour": (
        "A poor wheat harvest has pushed wholesale flour prices sharply higher. Millers expect "
        "elevated prices for at least a year.",
    ),
    "note_oven_noise": (
        "{oven} has been making a grinding noise on the early bake. Probably nothing, but it "
        "takes longer to come up to heat than it used to.",
        "The fan in {oven} is rattling again. Need to get someone to look at it one of these days.",
    ),
    "letter_oven_broken": (
        "{oven} failed during the morning bake. The engineer can repair it in about {days} days "
        "for ${repair}, or supply a new oven next morning for ${replace}.",
    ),
    "letter_notice": (
        "I've been offered a job at {rival} for better pay, and I'm going to take it. Please "
        "take this as my notice; my last day will be {last}. Thank you for everything. {name}",
    ),
    "letter_ingredient": (
        "Dear customer, owing to {reason}, we expect our {ingredient} prices to rise by around "
        "{pct}% from {start} for {weeks} weeks or so. We will keep you informed. {supplier}",
    ),
    "news_chain_lease": (
        "{chain}, the coffee and pastry chain, has signed a lease on a unit on Corn Street, "
        "about 300 metres from {area}, and expects to open in {month}.",
    ),
    "news_chain_open": (
        "{chain} opened its Corn Street branch this week, offering a loyalty card and "
        "a pastry with every large coffee before nine.",
    ),
    "news_road_works": (
        "The council will close the north side of {area} to traffic for resurfacing and new "
        "drains from {start} for about six weeks. Traders expect fewer passers-by.",
    ),
    "letter_landlord": (
        "Your lease of {address} ends on {end}. We are willing to renew for five years at "
        "${rent} a month, up from ${old}. Please let us know by {deadline}.",
    ),
    "letter_inspection_fail": (
        "Food hygiene inspection, {date}: cleaning schedules not kept, grease build-up behind "
        "the ovens and in the extraction hood. A fixed penalty of ${fine} is payable.",
    ),
    "letter_inspection_pass": (
        "Food hygiene inspection, {date}: premises clean and records in order. No action.",
    ),
    "news_viral_good": (
        "A short video of {shop}'s {item} coming out of the oven has been shared tens of "
        "thousands of times this week.",
    ),
    "news_viral_bad": (
        "A post calling {shop}'s {item} 'stale and overpriced' has been shared thousands of "
        "times this week, with many replies agreeing.",
    ),
    "review_viral_good": ("I came because of the video and the {item} was every bit as good.",),
    "review_viral_bad": ("Saw the post and tried the {item}. They were right, sadly.",),
    "letter_catering": (
        "We'd like {units} pastries with coffee for {client} on {delivery}. At your list prices "
        "that comes to ${value}. Can you do it?",
    ),
    "lease": (
        "COMMERCIAL LEASE\nPremises: {address}\nTenant: {shop} ({owner})\nTerm: {start} to "
        "{end}\nRent: ${rent} per month, payable monthly in advance.\nBreak: tenant may "
        "surrender the lease on payment of {break_months} months' rent.\nUse: bakery and "
        "cafe.",
    ),
    "loan": (
        "EQUIPMENT LOAN AGREEMENT\nBorrower: {shop} ({owner})\nPrincipal: ${principal}\n"
        "Interest: {apr}% per year on the outstanding balance, charged daily.\nRepayment: "
        "${repay} principal on the first of each month until repaid.\nSecurity: ovens, mixers "
        "and counter equipment at {address}.\nRevolving credit line: up to ${credit} at "
        "{credit_apr}% per year.",
    ),
    "supplier_contract": (
        "SUPPLY AGREEMENT\nSupplier: {supplier}\nCustomer: {shop}\nGoods: {goods}\nPricing: "
        "supplier's published monthly list price.\nDelivery: weekly; invoiced weekly, payment "
        "within 14 days.\nTerm: two years from {start}, renewing yearly unless cancelled with "
        "60 days' notice.",
    ),
}


def write(kind: str, rng: random.Random, **facts: object) -> str:
    return rng.choice(TEMPLATES[kind]).format(**facts)


def _iso(day: int) -> str:
    return date_of(day).isoformat()


def _dollars(cents: int) -> str:
    return f"{cents / 100:,.0f}"


EMPLOYER = "Kestrel Mutual"
SHOP_ADDRESS = "14 Market Square"
MIN_WAGE_OLD_CENTS = 1500
MIN_WAGE_NEW_CENTS = 1750
MIN_WAGE_EFFECTIVE_DAY = (date(2026, 4, 1) - START_DATE).days
MIN_WAGE_ANNOUNCED_DAY = 1040
REVIEWS_PER_VISIT = 0.0015
EARLY_STOCKOUT_HOUR = 12
RAISE_NOTE_MIN_STAFF = 3


def _snapshot(world: World) -> dict[tuple[LocationId, ItemId], tuple[str, int]]:
    return {
        (loc.id, item): (entry.state.value, entry.price_cents)
        for loc in world.business.locations.values()
        for item, entry in loc.menu.items()
    }


class Review(NamedTuple):
    rating: int
    text: str
    facts: dict


@dataclass(frozen=True)
class TextItem:
    key: str
    kind: str
    facts: dict
    draft: str


@dataclass
class _InvoiceLine:
    grams: int = 0
    cents: int = 0


def draw_reviews(rng: random.Random, loc: Location, shop: LocationDay) -> list[Review]:
    count = sum(rng.random() < REVIEWS_PER_VISIT for _ in range(shop.visits))
    sold = [i for i, d in sorted(shop.items.items()) if d.sold + d.day_old_sold]
    early_out = [
        i
        for i, d in sorted(shop.items.items())
        if d.first_stockout_hour is not None and d.first_stockout_hour < EARLY_STOCKOUT_HOUR
    ]
    busy = shop.walkaways > 0.08 * max(1, shop.visits)
    out = []
    for _ in range(count):
        rating = round(rng.gauss(1 + 4 * loc.reputation, 0.9) - (0.8 if busy else 0))
        rating = min(5, max(1, rating))
        item = CATALOG[rng.choice(sold) if sold else ItemId("croissant")].name.lower()
        text = write(f"review_{rating}", rng, item=item)
        facts: dict = {"rating": rating, "item": item, "long_wait": False, "sold_out": None}
        if busy and rng.random() < 0.6:
            text += " " + write("review_wait", rng)
            facts["long_wait"] = True
        elif early_out and rng.random() < 0.3:
            out_item = CATALOG[rng.choice(early_out)].name.lower()
            text += " " + write("review_sold_out", rng, item=out_item)
            facts["sold_out"] = out_item
        out.append(Review(rating, text, facts))
    return out


class Recorder:
    def __init__(self, conn: sqlite3.Connection, world: World, seed: int, resume: bool = False):
        self.conn, self.world = conn, world
        self.rng = random.Random(f"{seed}:records" + (":management" if resume else ""))
        self.sale_id = 0
        self.invoice_id = 0
        self.review_id = 0
        self.texts: list[TextItem] = []
        self.menu: dict[tuple[LocationId, ItemId], tuple[str, int]] = {}
        self.wages: dict[StaffId, int] = {}
        self.week: dict[tuple[LocationId, str, Ingredient], _InvoiceLine] = defaultdict(
            _InvoiceLine
        )
        self.raise_days: list[int] = []
        if resume:
            self.sale_id = conn.execute("SELECT MAX(sale_id) FROM sales").fetchone()[0] or 0
            self.invoice_id = (
                conn.execute("SELECT MAX(invoice_id) FROM invoices").fetchone()[0] or 0
            )
            self.review_id = conn.execute("SELECT MAX(review_id) FROM reviews").fetchone()[0] or 0
            self.menu = _snapshot(world)
            self.wages = {
                s.id: s.wage_cents_per_hour
                for s in world.business.staff.values()
                if s.state is not mc.StaffState.GONE
            }
            return
        for table, cols in TABLES.items():
            conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        self._insert("recipes", _recipe_rows())

    def _insert(self, table: str, rows: list[tuple]) -> None:
        if rows:
            marks = ", ".join("?" * len(TABLES[table]))
            self.conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)

    def on_report(self, report: DayReport, reviews: bool = True) -> None:
        for shop in report.locations:
            self._sales(report.day, shop)
            self._bakes(report.day, shop)
            self._shifts(report.day, shop)
            self._buy(shop, report.prices_paid)
            if reviews:
                self._reviews(report.day, shop)
        self._menu(report.day)
        self._wage_changes(report.day)
        if date_of(report.day).day == 1:
            self._price_lists(report.day)
        if report.weekday == 6:
            self._flush_invoices(report.day)

    def _sales(self, day: int, shop: LocationDay) -> None:
        sales, items = [], []
        for s in shop.sales:
            self.sale_id += 1
            lines: dict[tuple[ItemId, int, bool], int] = defaultdict(int)
            for line in s.lines:
                lines[(line.item_id, line.price_cents, line.day_old)] += 1
            total = sum(line.price_cents for line in s.lines)
            sales.append(
                (self.sale_id, day, _iso(day), s.hour, shop.location_id, s.loyalty_id)
                + (s.payment.value, total)
            )
            items += [(self.sale_id, i, q, p, int(old)) for (i, p, old), q in lines.items()]
        self._insert("sales", sales)
        self._insert("sale_items", items)

    def _bakes(self, day: int, shop: LocationDay) -> None:
        rows = [
            (day, _iso(day), shop.location_id, item, d.baked, d.sold, d.day_old_sold, d.wasted)
            + (d.first_stockout_hour,)
            for item, d in sorted(shop.items.items())
            if not CATALOG[item].made_to_order
        ]
        self._insert("bake_log", rows)

    def _shifts(self, day: int, shop: LocationDay) -> None:
        staff = self.world.business.staff
        rows = [
            (day, _iso(day), shop.location_id, s.staff_id, _role(self.world, s.staff_id))
            + (s.hours, s.pay_cents)
            for s in shop.shifts
            if s.staff_id in staff
        ]
        self._insert("shifts", rows)

    def _buy(self, shop: LocationDay, prices: dict[Ingredient, int]) -> None:
        buy_from = self.world.business.buy_from
        for ing, grams in shop.ingredient_grams.items():
            line = self.week[(shop.location_id, buy_from[ing], ing)]
            line.grams += grams
            line.cents += round(grams * prices[ing] / 1000)

    def _flush_invoices(self, day: int) -> None:
        by_invoice: dict[tuple[LocationId, str], list] = defaultdict(list)
        for (loc, supplier, ing), line in sorted(self.week.items()):
            if line.grams:
                by_invoice[(loc, supplier)].append((ing, line))
        rows = []
        for (loc, supplier), lines in by_invoice.items():
            self.invoice_id += 1
            for ing, line in lines:
                kg = round(line.grams / 1000, 2)
                rows.append(
                    (self.invoice_id, _iso(day), supplier, loc, ing.value, kg)
                    + (round(line.cents / kg) if kg else 0, line.cents)
                )
        self._insert("invoices", rows)
        self.week.clear()

    def _reviews(self, day: int, shop: LocationDay) -> None:
        loc = self.world.business.locations[shop.location_id]
        self.insert_reviews(day, shop.location_id, draw_reviews(self.rng, loc, shop))

    def insert_reviews(self, day: int, loc: LocationId, reviews: list[Review]) -> None:
        rows = []
        for r in reviews:
            self.review_id += 1
            rows.append((self.review_id, _iso(day), loc, r.rating, r.text))
            facts = r.facts | {"date": _iso(day), "location": loc}
            self.texts.append(TextItem(f"review:{self.review_id}", "review", facts, r.text))
        self._insert("reviews", rows)

    def append_ledger(self, entries: list[LedgerEntry]) -> None:
        self._insert("ledger", [_ledger_row(e) for e in entries])

    def append_documents(self, docs: list[tuple[int, str, str, str]]) -> None:
        self._insert("documents", [(None, _iso(d), k, t, b) for d, k, t, b in docs])

    def refresh_summaries(self) -> None:
        self._flush_invoices(self.world.day - 1)
        self.refresh_tables()

    def refresh_tables(self) -> None:
        for table, rows in (
            ("monthly_pnl", _pnl_rows(self.world)),
            ("staff", _staff_rows(self.world)),
        ):
            self.conn.execute(f"DELETE FROM {table}")
            self._insert(table, rows)

    def _menu(self, day: int) -> None:
        now = _snapshot(self.world)
        rows = []
        for key in sorted(now.keys() | self.menu.keys()):
            before, after = self.menu.get(key), now.get(key)
            if before == after:
                continue
            change = _menu_change(before, after)
            status, price = after or (mc.MenuState.RETIRED.value, before[1])
            loc, item = key
            rows.append((day, _iso(day), loc, item, CATALOG[item].name, change, status, price))
        self._insert("menu_history", rows)
        self.menu = now

    def _wage_changes(self, day: int) -> None:
        rows, raised = [], 0
        for s in sorted(self.world.business.staff.values(), key=lambda s: s.id):
            if s.state is not mc.StaffState.GONE and self.wages.get(s.id) != s.wage_cents_per_hour:
                raised += s.id in self.wages
                rows.append((s.id, _iso(day), s.wage_cents_per_hour))
                self.wages[s.id] = s.wage_cents_per_hour
        if raised >= RAISE_NOTE_MIN_STAFF:
            self.raise_days.append(day)
        self._insert("staff_wages", rows)

    def _price_lists(self, day: int) -> None:
        market = self.world.ingredient_prices.on(day)
        month = date_of(day).strftime("%Y-%m")
        rows = [
            (s.id, s.name, month, ing.value, round(market[ing] * markup))
            for s in self.world.business.suppliers.values()
            for ing, markup in s.markup.items()
        ]
        self._insert("suppliers", rows)

    def finish(self, founding_facts: "Facts") -> None:
        last = self.world.day - 1
        self._flush_invoices(last)
        world = self.world
        self.append_ledger(world.business.ledger)
        self._insert("monthly_pnl", _pnl_rows(world))
        self._insert("staff", _staff_rows(world))
        docs = _documents(world, founding_facts, self.raise_days, self.rng)
        self._insert("documents", [(n, _iso(d.day), d.kind, d.title, d.body) for n, d in docs])
        self.texts += [
            TextItem(f"document:{n}", d.template, d.facts | {"date": _iso(d.day)}, d.body)
            for n, d in docs
        ]

    def apply_texts(self, texts: dict[str, str]) -> None:
        tables = {
            "review": ("reviews", "text", "review_id"),
            "document": ("documents", "body", "doc_id"),
        }
        for key, text in sorted(texts.items()):
            kind, _, row = key.partition(":")
            table, column, id_column = tables[kind]
            self.conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE {id_column} = ?", (text, int(row))
            )


def _recipe_rows() -> list[tuple]:
    return [
        (r.item_id, r.name, r.category.value, ing.value, grams, r.labor_minutes)
        + (r.shelf_life_days,)
        for r in CATALOG.values()
        for ing, grams in r.grams.items()
    ]


def _ledger_row(e: LedgerEntry) -> tuple:
    return (None, e.day, _iso(e.day), e.location_id, e.account.value) + (
        SECTION[e.account].value,
        e.amount_cents,
        e.memo,
    )


def _role(world: World, sid: StaffId) -> str:
    s = world.business.staff[sid]
    return "owner" if s.owner else s.role.value


def _menu_change(before: tuple[str, int] | None, after: tuple[str, int] | None) -> str:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "price" if before[0] == after[0] else "status"


def _pnl_rows(world: World) -> list[tuple]:
    rows = []
    for (month, loc), accounts in sorted(
        monthly_by_account(world.business.ledger).items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
    ):
        by = defaultdict(int)
        for account, cents in accounts.items():
            by[SECTION[account]] += cents
        operating = sum(c for a, c in accounts.items() if a in OPERATING)
        net = operating + by[Section.FINANCE] + by[Section.ONE_OFF]
        rows.append(
            (month, loc, by[Section.REVENUE], by[Section.COST_OF_SALES])
            + (by[Section.OPERATING_EXPENSE], operating, by[Section.FINANCE])
            + (by[Section.ONE_OFF], net)
        )
    return rows


def _staff_rows(world: World) -> list[tuple]:
    left = {
        t.entity_id: t.day
        for t in world.business.transitions
        if t.entity == mc.STAFF.entity and t.after == mc.StaffState.GONE.name
    }
    return [
        (s.id, s.name, _role(world, s.id), s.location_id, _iso(s.hired_day))
        + (_iso(left[s.id]) if s.id in left else None, s.state.value, s.wage_cents_per_hour)
        for s in sorted(world.business.staff.values(), key=lambda s: s.id)
    ]


@dataclass(frozen=True)
class Facts:
    habits: dict[HabitId, dict]
    force_onsets: dict[ForceId, int]
    opening_debt_cents: int
    credit_limit_cents: int
    credit_apr: float

    def onset(self, fid: ForceId) -> int:
        low, high = FORCES[fid].onset_day
        return self.force_onsets.get(fid, round((low + high) / 2))


def _first(world: World, entity: str, entity_id: str, event: str) -> int | None:
    return next(
        (
            t.day
            for t in world.business.transitions
            if t.entity == entity and t.entity_id == entity_id and t.event == event
        ),
        None,
    )


class Document(NamedTuple):
    day: int
    kind: str
    title: str
    body: str
    template: str
    facts: dict


def _documents(
    world: World, facts: Facts, raise_days: list[int], rng: random.Random
) -> list[tuple[int, Document]]:
    last = world.day - 1
    b = world.business
    home = b.locations[HOME_LOCATION]
    rival = next(name for i, name, *_ in COMPETITORS if i == RIVAL_ID)
    area = world.town[home.neighborhood_id].name
    docs: list[Document] = []

    def doc(day: int, kind: str, title: str, template: str, **f: object) -> None:
        if 0 <= day <= last or kind != "news":
            docs.append(Document(day, kind, title, write(template, rng, **f), template, f))

    doc(
        -30,
        "lease",
        f"Lease: {SHOP_ADDRESS}",
        "lease",
        address=SHOP_ADDRESS,
        shop=SHOP_NAME,
        owner=OWNER_NAME,
        start=_iso(0),
        end=_iso(home.lease.end_day),
        rent=_dollars(home.lease.monthly_rent_cents),
        break_months=6,
    )
    if SECOND_SHOP in b.locations:
        shop2 = b.locations[SECOND_SHOP]
        signed = _first(world, mc.LOCATION.entity, SECOND_SHOP, "SIGN_LEASE") or 0
        doc(
            signed,
            "lease",
            "Lease: 3 Orchard Parade, Maple Grove",
            "lease",
            address="3 Orchard Parade, Maple Grove",
            shop=SHOP_NAME,
            owner=OWNER_NAME,
            start=_iso(signed),
            end=_iso(shop2.lease.end_day),
            rent=_dollars(shop2.lease.monthly_rent_cents),
            break_months=SECOND_SHOP_BREAK_MONTHS,
        )
    doc(
        -20,
        "loan",
        "Equipment loan agreement",
        "loan",
        shop=SHOP_NAME,
        owner=OWNER_NAME,
        principal=_dollars(facts.opening_debt_cents),
        apr=round(LOAN_APR * 100, 1),
        repay=_dollars(LOAN_PRINCIPAL_PER_MONTH_CENTS),
        address=SHOP_ADDRESS,
        credit=_dollars(facts.credit_limit_cents),
        credit_apr=round(facts.credit_apr * 100, 1),
    )
    for sid in ("millbrook", b.buy_from[Ingredient.BUTTER]):
        supplier = b.suppliers[sid]
        goods = ", ".join(sorted(i.value for i in supplier.markup))
        doc(
            -15,
            "supplier_contract",
            f"Supply agreement: {supplier.name}",
            "supplier_contract",
            supplier=supplier.name,
            shop=SHOP_NAME,
            goods=goods,
            start=_iso(0),
        )

    opened = _first(world, mc.COMPETITOR.entity, RIVAL_ID, "OPEN_DOORS")
    rumor = (opened if opened is not None else facts.onset(ForceId.COMPETITOR_ENTRY)) - (
        rng.randint(45, 90)
    )
    doc(rumor, "news", f"{rival} plans {area} branch", "news_rival_rumor", rival=rival, area=area)
    if opened is not None:
        doc(opened, "news", f"{rival} opens in {area}", "news_rival_open", rival=rival, area=area)
    hybrid = facts.onset(ForceId.NEIGHBORHOOD_SHIFT) - rng.randint(10, 30)
    doc(hybrid, "news", f"{EMPLOYER} goes hybrid", "news_hybrid", employer=EMPLOYER, area=area)
    effective = date_of(MIN_WAGE_EFFECTIVE_DAY)
    doc(
        MIN_WAGE_ANNOUNCED_DAY,
        "news",
        "Minimum wage to rise",
        "news_min_wage",
        old=f"{MIN_WAGE_OLD_CENTS / 100:.2f}",
        new=f"{MIN_WAGE_NEW_CENTS / 100:.2f}",
        effective=f"{effective.day} {effective:%B %Y}",
    )
    spike = facts.habits.get(HabitId.UNPRICED_FLOUR_SPIKE)
    if spike:
        doc(spike["start_day"] + 3, "news", "Flour prices jump", "news_flour")

    for day, template, f, text in _notebook(world, facts, raise_days, opened, rumor, hybrid, rng):
        docs.append(Document(day, "notebook", f"Notebook, {_iso(day)}", text, template, f))
    ordered = sorted(docs, key=lambda d: (d.day, d.kind, d.title))
    return list(enumerate(ordered, start=1))


def _phase(day: int) -> str:
    return f"y{min(3, day // 365 + 1)}"


MOODS = {"y1": "hopeful and energetic", "y2": "steady", "y3": "tired and terse"}


def _notebook(
    world: World,
    facts: Facts,
    raise_days: list[int],
    rival_open: int | None,
    rumor: int,
    hybrid: int,
    rng: random.Random,
) -> list[tuple[int, str, dict, str]]:
    last = world.day - 1
    b = world.business
    rival = next(name for i, name, *_ in COMPETITORS if i == RIVAL_ID)
    notes: list[tuple[int, str, dict]] = [
        (0, "note_open_y1", {"item": "croissants", "shop": SHOP_NAME})
    ]
    for t in b.transitions:
        if t.entity == mc.MENU_ITEM.entity and t.event == "START_TRIAL":
            kind = "note_launch_y1" if t.day < 365 else "note_launch_y2"
            notes.append((t.day, kind, {"item": CATALOG[ItemId(t.entity_id)].name.lower()}))
    staff_left = [
        t
        for t in b.transitions
        if t.entity == mc.STAFF.entity
        and t.after == mc.StaffState.GONE.name
        and b.staff[StaffId(t.entity_id)].location_id == HOME_LOCATION
    ]
    for t in staff_left[:3]:
        notes.append((t.day, "note_staff_left", {"name": b.staff[StaffId(t.entity_id)].name}))
    notes += [(365, "note_review", {})] + [(d, "note_raise", {}) for d in raise_days]
    if SECOND_SHOP in b.locations:
        shop2 = b.locations[SECOND_SHOP]
        for event, kind in (
            ("SIGN_LEASE", "note_shop2_sign"),
            ("OPEN_DOORS", "note_shop2_open"),
            ("SHUT_DOORS", "note_shop2_close"),
        ):
            day = _first(world, mc.LOCATION.entity, SECOND_SHOP, event)
            if day is not None:
                rent = _dollars(shop2.lease.monthly_rent_cents)
                notes.append((day, kind, {"m2": shop2.size_m2, "rent": rent}))
    notes.append((rumor + rng.randint(5, 20), "note_rival_rumor", {}))
    if rival_open is not None:
        notes.append((rival_open + 1, "note_rival_open", {"rival": rival}))
        notes.append((rival_open + rng.randint(25, 45), "note_rival_dismiss", {"rival": rival}))
    notes.append((hybrid + rng.randint(1, 5), "note_hybrid", {"employer": EMPLOYER}))
    belief_day = {
        HabitId.CHEAP_CROISSANT: rng.randint(100, 200),
        HabitId.EARLY_WEEK_SOURDOUGH: rng.randint(120, 250),
        HabitId.EARLY_CLOSE: rng.randint(60, 150),
        HabitId.SLOW_LOYAL_BAKER: rng.randint(400, 500),
        HabitId.DEAR_BUTTER: rng.randint(200, 300),
    }
    for hid, details in facts.habits.items():
        day = details["start_day"] + 40 if hid is HabitId.UNPRICED_FLOUR_SPIKE else belief_day[hid]
        notes.append((day, f"note_belief_{hid.value}", {"name": details.get("name", "")}))
    notes.append((rng.randint(1000, 1060), "note_belief_weather", {}))
    notes += _reflections(world, rng)
    out = [
        (d, k, f | {"mood": MOODS[_phase(d)]}, write(k, rng, **f))
        for d, k, f in sorted(notes, key=lambda n: (n[0], n[1]))
    ]
    return [note for note in out if 0 <= note[0] <= last]


REFLECTION_MONTHS = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 27, 30, 33, 35)


def _reflections(world: World, rng: random.Random) -> list[tuple[int, str, dict]]:
    books = monthly_by_account(world.business.ledger)
    months = sorted({m for m, _ in books})
    revenue = [books.get((m, HOME_LOCATION), {}).get(Account.SALES, 0) for m in months]
    out = []
    for n in REFLECTION_MONTHS:
        if n >= len(months):
            continue
        first_next = date_of(0).replace(year=2023 + n // 12, month=n % 12 + 1, day=1)
        day = (first_next - date_of(0)).days + rng.randint(0, 3)
        prev, now = revenue[n - 2], revenue[n - 1]
        trend = "up" if now > prev * 1.03 else "down" if now < prev * 0.97 else "about the same"
        label = date_of(day - 10).strftime("%B")
        out.append(
            (
                day,
                f"note_month_{_phase(day - 10)}",
                {
                    "month": label,
                    "revenue": round(now / 100_000),
                    "trend": trend,
                    "item": rng.choice(("croissant", "sourdough", "pain au chocolat")),
                },
            )
        )
    return out


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(path) as conn:
        for table in TABLES:
            digest.update(table.encode())
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                digest.update(repr(row).encode())
    conn.close()
    return digest.hexdigest()
