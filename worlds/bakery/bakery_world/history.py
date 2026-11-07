import random
from collections.abc import Callable
from dataclasses import dataclass

from bakery_world.decay import DecayHook, Force, ForceId, draw_forces
from bakery_world.engine import DayReport, simulate_day
from bakery_world.founder import Founder, Quirks
from bakery_world.habits import HabitId, apply_habits, draw_habits
from bakery_world.model import World
from bakery_world.town import build_world

HISTORY_DAYS = 1095


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
    world.daily_hooks.append(hook)
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
