import json
import pickle
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from bakery_world.basics import Cents, date_of
from bakery_world.decay import FORCES, DecayHook, Force, ForceId, draw_forces
from bakery_world.engine import CREDIT_APR, DayReport, simulate_day
from bakery_world.founder import Founder, Quirks
from bakery_world.habits import HABITS, HabitId, apply_habits, draw_habits
from bakery_world.machines import StaffState
from bakery_world.model import World
from bakery_world.records import (
    MIN_WAGE_EFFECTIVE_DAY,
    MIN_WAGE_NEW_CENTS,
    Facts,
    Recorder,
    content_hash,
)
from bakery_world.town import build_world
from bakery_world.writer import CACHE_FILE, Outcome, Writer

HISTORY_DAYS = 1095


@dataclass(frozen=True)
class WageFloor:
    from_day: int
    cents_per_hour: Cents

    def __call__(self, world: World) -> None:
        if world.day < self.from_day:
            return
        for s in world.business.staff.values():
            if not s.owner and s.state is not StaffState.GONE:
                s.wage_cents_per_hour = Cents(max(s.wage_cents_per_hour, self.cents_per_hour))


@dataclass
class Founding:
    seed: int
    world: World
    founder: Founder
    hook: DecayHook
    habits: dict[HabitId, dict]
    rng: random.Random


def found(
    seed: int,
    disabled: frozenset[ForceId] = frozenset(),
    forces: tuple[Force, ...] | None = None,
    habits: tuple[HabitId, ...] | None = None,
) -> Founding:
    world = build_world(seed)
    hook = DecayHook(draw_forces(seed, disabled) if forces is None else forces)
    world.daily_hooks += [hook, WageFloor(MIN_WAGE_EFFECTIVE_DAY, Cents(MIN_WAGE_NEW_CENTS))]
    quirks = Quirks()
    details = apply_habits(world, quirks, seed, draw_habits(seed) if habits is None else habits)
    founder = Founder(seed, hook, quirks)
    return Founding(seed, world, founder, hook, details, random.Random(f"{seed}:days"))


def live(
    founding: Founding, days: int, on_report: Callable[[DayReport], None] | None = None
) -> None:
    world, founder = founding.world, founding.founder
    for _ in range(days):
        founder(world, world.day)
        report = simulate_day(world, founding.rng)
        founder.observe(report)
        if on_report:
            on_report(report)


@dataclass(frozen=True)
class HistoryResult:
    seed: int
    days: int
    records: Path
    hidden: Path
    handoff: Path
    content_hash: str
    seconds: float
    text: Outcome | None = None


def hidden_facts(founding: Founding) -> dict:
    def share(f: Force) -> float:
        low, high = FORCES[f.id].strength
        return round((f.strength - low) / (high - low), 3)

    forces = [
        asdict(f) | {"onset_date": date_of(f.onset_day).isoformat(), "share": share(f)}
        for f in founding.hook.forces
    ]
    return {
        "seed": founding.seed,
        "params": asdict(founding.world.params),
        "forces": forces,
        "dominant_force": max(forces, key=lambda f: f["share"])["id"] if forces else None,
        "habits": {
            hid.value: {"description": HABITS[hid].description, "details": details}
            for hid, details in founding.habits.items()
        },
        "second_shop": asdict(founding.founder.expansion),
    }


def generate_history(
    seed: int,
    out_dir: Path | str,
    days: int = HISTORY_DAYS,
    habits: tuple[HabitId, ...] | None = None,
    writer: Writer | None = None,
) -> HistoryResult:
    started = time.perf_counter()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {name: out / name for name in ("records.sqlite", "hidden.json", "handoff.pickle")}
    paths["records.sqlite"].unlink(missing_ok=True)
    founding = found(seed, habits=habits)
    world = founding.world
    facts = Facts(
        habits=founding.habits,
        force_onsets={f.id: f.onset_day for f in founding.hook.forces},
        opening_debt_cents=world.business.debt_cents,
        credit_limit_cents=world.business.credit_limit_cents,
        credit_apr=CREDIT_APR,
    )
    conn = sqlite3.connect(paths["records.sqlite"])
    recorder = Recorder(conn, world, seed)
    live(founding, days, recorder.on_report)
    recorder.finish(facts)
    text = writer.write(recorder.texts, out / CACHE_FILE) if writer else None
    if text:
        recorder.apply_texts(text.texts)
    conn.commit()
    conn.close()
    paths["hidden.json"].write_text(json.dumps(hidden_facts(founding), indent=2, sort_keys=True))
    paths["handoff.pickle"].write_bytes(pickle.dumps(founding, protocol=pickle.HIGHEST_PROTOCOL))
    return HistoryResult(
        seed,
        days,
        paths["records.sqlite"],
        paths["hidden.json"],
        paths["handoff.pickle"],
        content_hash(paths["records.sqlite"]),
        round(time.perf_counter() - started, 2),
        text,
    )


def resume(handoff: Path | str) -> Founding:
    return pickle.loads(Path(handoff).read_bytes())
