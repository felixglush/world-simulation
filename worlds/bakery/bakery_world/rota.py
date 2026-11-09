from bakery_world.basics import Role, StaffId
from bakery_world.machines import StaffState
from bakery_world.model import Location, Rota, ShiftTime, Staff, rota_hours

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SHIFT_HOURS: dict[Role, int] = {Role.BAKER: 8, Role.COUNTER: 8, Role.CLEANER: 4}
PRE_OPEN_BAKE_HOURS = 2
MAX_DAY_HOURS = 10
MAX_WEEK_HOURS = 48
DAYS_OFF: tuple[tuple[int, int], ...] = ((6, 2), (5, 1), (6, 4), (5, 0), (6, 3), (5, 2), (6, 0))
ON_ROTA = (StaffState.EMPLOYED, StaffState.SICK, StaffState.ON_NOTICE)
# four weeks' paid leave a year, taken a day at a time: one calendar day in 13, staggered so
# colleagues with nearby staff numbers are off on different days
LEAVE_EVERY_DAYS = 13
LEAVE_STAGGER = 5


def shift_window(role: Role, loc: Location) -> tuple[int, int]:
    early = PRE_OPEN_BAKE_HOURS if role is Role.BAKER else 0
    return max(0, loc.open_hour - early), loc.close_hour


def standard_shift(role: Role, loc: Location, late: bool = False) -> ShiftTime:
    low, high = shift_window(role, loc)
    length = min(SHIFT_HOURS[role], high - low)
    if role is Role.CLEANER or (late and role is Role.COUNTER):
        return ShiftTime(high - length, high)
    return ShiftTime(low, low + length)


def worked(staff: Staff, loc: Location, weekday: int) -> ShiftTime | None:
    shift = staff.rota[weekday] if staff.rota else None
    if shift is None:
        return None
    low, high = shift_window(staff.role, loc)
    start, end = max(low, shift.start_hour), min(high, shift.end_hour)
    return ShiftTime(start, end) if start < end else None


def assign_rota(staff: dict[StaffId, Staff], member: Staff, loc: Location) -> None:
    """Give a new starter the standard shift on the days fewest colleagues in the role are off."""
    team = [
        s
        for s in staff.values()
        if s.id != member.id
        and s.location_id == loc.id
        and s.role is member.role
        and s.state in ON_ROTA
        and s.rota
    ]
    off = [sum(s.rota[d] is None for s in team) for d in range(7)]
    days = min(DAYS_OFF, key=lambda pair: sum(off[d] for d in pair))
    starts = [next(x.start_hour for x in s.rota if x) for s in team if any(s.rota)]
    late = sum(h == loc.open_hour for h in starts) > sum(h != loc.open_hour for h in starts)
    shift = standard_shift(member.role, loc, late)
    member.rota = tuple(None if d in days else shift for d in range(7))
    member.contract_hours = rota_hours(member.rota)


def parse_rota(texts: tuple[str, ...]) -> Rota:
    return tuple(None if t == "off" else ShiftTime(*(int(h) for h in t.split("-"))) for t in texts)


def describe(rota: Rota) -> list[str]:
    return [str(s) if s else "off" for s in rota]


def on_leave(staff: Staff, day: int) -> bool:
    if staff.owner or not staff.id[1:].isdigit():
        return False
    return (day + LEAVE_STAGGER * int(staff.id[1:])) % LEAVE_EVERY_DAYS == 0


def counter_gaps(staff: list[Staff], loc: Location) -> dict[str, list[str]]:
    """Open hours, per weekday, that no counter worker on the rota covers."""
    gaps = {}
    for d, day in enumerate(WEEKDAYS):
        shifts = [
            worked(s, loc, d)
            for s in staff
            if s.location_id == loc.id and s.role is Role.COUNTER and s.state in ON_ROTA
        ]
        spans: list[list[int]] = []
        for h in range(loc.open_hour, loc.close_hour):
            if any(x and x.start_hour <= h < x.end_hour for x in shifts):
                continue
            if spans and spans[-1][1] == h:
                spans[-1][1] = h + 1
            else:
                spans.append([h, h + 1])
        if spans:
            gaps[day] = [str(ShiftTime(a, b)) for a, b in spans]
    return gaps
