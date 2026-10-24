"""Turn the farmer's view into signals and change events for the gate and the memory stream."""

from __future__ import annotations

from typing import Any

from station_control.society.gate import AgentView, Observation
from station_control.society.messages import Message

HEAVY_RAIN_MM = 25.0


class Perception:
    """Remembers what one farmer saw yesterday so only changes become observations."""

    def __init__(self) -> None:
        self._last: dict[str, Any] = {}
        self._river = ""
        self._river_streak = 0

    def view(
        self,
        tick: int,
        seen: dict[str, Any],
        inbox: tuple[Message, ...],
        water_mm: float,
        health: float | None,
    ) -> AgentView:
        me = seen["me"]["id"]
        signals = {
            "water_mm": int(water_mm),
            "health_pct": 100 if health is None else int(health * 100),
            "food_days": int(seen["me"]["food_days_left"]),
            "temp_c": int(seen["weather"]["temp_c"]),
            "rain_mm": int(seen["weather"]["rain_mm"]),
        }
        items = self._changes(tick, me, seen)
        self._last = _snapshot(me, seen)
        return AgentView(tick, signals, tuple(items), inbox)

    def _river_change(self, tick: int, word: str) -> list[Observation]:
        """Report a new river level only once it has held for three days, so noise stays quiet."""
        if not self._river:
            self._river = word
        if word == self._river:
            self._river_streak = 0
            return []
        self._river_streak += 1
        if self._river_streak < 3:
            return []
        self._river, self._river_streak = word, 0
        return [Observation("river", f"The river is now {word}.", turn=tick)]

    def _changes(self, tick: int, me: str, seen: dict[str, Any]) -> list[Observation]:
        last = self._last
        if not last:
            return [Observation("start", "A new day begins on the farm.", turn=tick)]
        out: list[Observation] = []
        now = _snapshot(me, seen)
        for plot_id, plot in seen["plots"].items():
            before = last["plots"].get(plot_id)
            if before is None or before["crop_stage"] == plot["crop_stage"]:
                continue
            mine = plot["owner"] == me
            who = "Your" if mine else f"{plot['owner']}'s"
            out.append(
                Observation(
                    "stage_change" if mine else "neighbor_activity",
                    f"{who} {plot['kind']} plot changed from {before['crop_stage']} to "
                    f"{plot['crop_stage']}.",
                    turn=tick,
                )
            )
        for plot_id, plot in seen["plots"].items():
            if plot["owner"] == me:
                for key, text in (("leaves", "leaves"), ("bund", "bund")):
                    if key in plot and plot[key] != last["plots"].get(plot_id, {}).get(key):
                        out.append(
                            Observation(f"my_{key}", f"Your plot {text}: {plot[key]}.", turn=tick)
                        )
                weeds = plot.get("weeds_visible")
                if weeds in ("heavy", "severe") and last["weeds"] not in ("heavy", "severe"):
                    out.append(Observation("weeds", f"Weeds are {weeds} on your plot.", turn=tick))
        for name, gate in seen["channels"].items():
            before = last["gates"].get(name)
            if before is not None and before != gate["open"] and gate["last_changed_by"] != me:
                state = "opened" if gate["open"] else "closed"
                out.append(
                    Observation(
                        "gate_change",
                        f"{gate['last_changed_by'] or 'someone'} {state} the {name} gate.",
                        turn=tick,
                    )
                )
        if seen["weather"]["rain_mm"] >= HEAVY_RAIN_MM:
            out.append(
                Observation(
                    "heavy_rain", f"Heavy rain: {seen['weather']['rain_mm']} mm.", turn=tick
                )
            )
        out.extend(self._river_change(tick, seen["river"]))
        if seen["me"]["hungry"] and not last["hungry"]:
            out.append(Observation("hunger", "Your household has run out of food.", turn=tick))
        days = seen["me"]["food_days_left"]
        for line in (30, 14, 7):
            if days <= line < last["food_days"]:
                out.append(
                    Observation("food_low", f"About {days:.0f} days of food left.", turn=tick)
                )
        if now["nursery"] is not None and last["nursery"] is None:
            out.append(Observation("nursery", "Your nursery bed is sown.", turn=tick))
        return out


def _snapshot(me: str, seen: dict[str, Any]) -> dict[str, Any]:
    mine = next(p for p in seen["plots"].values() if p["owner"] == me)
    return {
        "plots": {k: dict(v) for k, v in seen["plots"].items()},
        "weeds": mine.get("weeds_visible", "none"),
        "gates": {k: v["open"] for k, v in seen["channels"].items()},
        "river": seen["river"],
        "hungry": seen["me"]["hungry"],
        "food_days": seen["me"]["food_days_left"],
        "nursery": seen["me"]["nursery_age_days"],
    }
