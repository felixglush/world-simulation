import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto

from bakery_world import machines as mc
from bakery_world.basics import Segment
from bakery_world.model import World
from bakery_world.town import COMPETITORS, HOME_NEIGHBORHOOD, OWNER_ID, OWNER_SPEED, RIVAL_ID

Range = tuple[float, float]


class ForceId(StrEnum):
    FOUNDER_FATIGUE = auto()
    MENU_FATIGUE = auto()
    FEW_NEW_CUSTOMERS = auto()
    QUALITY_SLIP = auto()
    COMPETITOR_ENTRY = auto()
    NEIGHBORHOOD_SHIFT = auto()


@dataclass(frozen=True)
class Force:
    id: ForceId
    onset_day: int
    ramp_days: int
    strength: float

    def level(self, day: int) -> float:
        if day < self.onset_day:
            return 0.0
        # creeping decay: slow at first, then gathering pace
        return min(1.0, (day - self.onset_day + 1) / self.ramp_days) ** 2

    def intensity(self, day: int) -> float:
        return self.strength * self.level(day)


def _founder_fatigue(world: World, force: Force) -> None:
    owner = world.business.staff.get(OWNER_ID)
    if owner is None or force.level(world.day) == 0:
        return
    tired = force.intensity(world.day)
    owner.speed = OWNER_SPEED * (1 - tired)
    owner.morale = 0.85 - 0.6 * tired


def _menu_fatigue(world: World, force: Force) -> None:
    world.menu_boredom = force.intensity(world.day)


def _few_new_customers(world: World, force: Force) -> None:
    world.word_of_mouth = 1 - force.intensity(world.day)


def _quality_slip(world: World, force: Force) -> None:
    world.market_wage_index = 1 + force.intensity(world.day)


RIVAL_APPEAL = next(dict(zip(Segment, a, strict=True)) for i, *_, a in COMPETITORS if i == RIVAL_ID)


def _competitor_entry(world: World, force: Force) -> None:
    rival = world.competitors[RIVAL_ID]
    if force.level(world.day) == 0 or rival.state is mc.CompetitorState.CLOSED:
        return
    if rival.state is mc.CompetitorState.RUMORED:
        rival.state = mc.step(
            world.business.transitions,
            world.day,
            mc.COMPETITOR,
            rival.id,
            rival.state,
            mc.CompetitorEvent.OPEN_DOORS,
        )
    grown = force.intensity(world.day)
    rival.appeal = {seg: base * grown for seg, base in RIVAL_APPEAL.items()}


def _neighborhood_shift(world: World, force: Force) -> None:
    if force.level(world.day) > 0:
        world.segment_shift[(HOME_NEIGHBORHOOD, Segment.OFFICE)] = 1 - force.intensity(world.day)


@dataclass(frozen=True)
class ForceSpec:
    onset_day: Range
    ramp_days: Range
    strength: Range
    act: Callable[[World, Force], None]


FORCES: dict[ForceId, ForceSpec] = {
    ForceId.FOUNDER_FATIGUE: ForceSpec((430, 490), (450, 650), (0.02, 0.13), _founder_fatigue),
    ForceId.MENU_FATIGUE: ForceSpec((440, 500), (450, 650), (0.05, 0.9), _menu_fatigue),
    ForceId.FEW_NEW_CUSTOMERS: ForceSpec((420, 520), (450, 650), (0.03, 0.5), _few_new_customers),
    ForceId.QUALITY_SLIP: ForceSpec((380, 460), (550, 750), (0.02, 0.18), _quality_slip),
    ForceId.COMPETITOR_ENTRY: ForceSpec((590, 650), (350, 480), (0.07, 1.0), _competitor_entry),
    ForceId.NEIGHBORHOOD_SHIFT: ForceSpec(
        (700, 760), (300, 420), (0.03, 0.55), _neighborhood_shift
    ),
}


SEVERITY = (0.62, 0.65)
MIX = (0.25, 1.0)


def draw_forces(seed: int, disabled: frozenset[ForceId] = frozenset()) -> tuple[Force, ...]:
    """Each town gets a different mix of causes but a similar total severity."""
    rng = random.Random(f"{seed}:forces")
    timing = {
        fid: (rng.uniform(*s.onset_day), rng.uniform(*s.ramp_days)) for fid, s in FORCES.items()
    }
    mix = {fid: rng.uniform(*MIX) for fid in FORCES}
    scale = rng.uniform(*SEVERITY) * len(mix) / sum(mix.values())
    drawn = []
    for fid, spec in FORCES.items():
        lo, hi = spec.strength
        onset, ramp = timing[fid]
        share = min(1.0, mix[fid] * scale)
        drawn.append(Force(fid, round(onset), round(ramp), round(lo + share * (hi - lo), 3)))
    return tuple(f for f in drawn if f.id not in disabled)


@dataclass(frozen=True)
class DecayHook:
    forces: tuple[Force, ...]

    def __call__(self, world: World) -> None:
        for force in self.forces:
            FORCES[force.id].act(world, force)

    def get(self, fid: ForceId) -> Force | None:
        return next((f for f in self.forces if f.id == fid), None)

    def active(self, fid: ForceId, day: int) -> bool:
        force = self.get(fid)
        return force is not None and force.level(day) > 0
