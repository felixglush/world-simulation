"""The gate decides when an agent spends a model call. It reads only the agent's own view."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..trade_types import PublicWorldView, TradeEvidence
from .messages import Message

HEARTBEAT_TICKS = 6


@dataclass(frozen=True, slots=True)
class Observation:
    """One item an agent perceived this tick."""

    code: str
    text: str
    ref: str = ""
    turn: int = 0


@dataclass(frozen=True, slots=True)
class AgentView:
    """Everything the gate and the memory may see; built from one agent's own view."""

    tick: int
    signals: dict[str, int]
    items: tuple[Observation, ...] = ()
    inbox: tuple[Message, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[tuple[int, str], ...] = ()
    expectations: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    made_at: int = 0

    def due_step(self, tick: int, acted: set[int]) -> tuple[int, str] | None:
        return next((s for s in self.steps if s[0] <= tick and s[0] not in acted), None)


@dataclass(frozen=True, slots=True)
class GateConfig:
    thresholds: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    heartbeat: int = HEARTBEAT_TICKS


class Gate:
    def __init__(self, config: GateConfig) -> None:
        self._config = config
        self._last_think = 0
        self._started = False
        self._acted_steps: set[int] = set()
        self._breached: set[str] = set()
        self._deviating: set[str] = set()

    def check(self, view: AgentView, plan: Plan | None) -> tuple[str, ...]:
        """Reasons the gate opens this tick, in a fixed order."""
        if not isinstance(view, AgentView):
            raise TypeError("The gate reads an AgentView only")
        reasons: list[str] = []
        if not self._started:
            reasons.append("start")
        if view.items:
            reasons.append("new_evidence")
        if view.inbox or any(item.ref.startswith("msg-") for item in view.items):
            reasons.append("message")
        crossed = self._outside(view.signals, self._config.thresholds)
        if crossed - self._breached:
            reasons.append("threshold")
        self._breached = crossed
        if plan is not None:
            deviating = self._outside(view.signals, plan.expectations)
            if deviating - self._deviating:
                reasons.append("plan_deviation")
            self._deviating = deviating
            if plan.due_step(view.tick, self._acted_steps) is not None:
                reasons.append("plan_due")
        if view.tick - self._last_think >= self._config.heartbeat:
            reasons.append("heartbeat")
        return tuple(reasons)

    def step_due(self, plan: Plan | None, tick: int) -> bool:
        return plan is not None and plan.due_step(tick, self._acted_steps) is not None

    def thought(
        self,
        tick: int,
        plan: Plan | None,
        new_plan: Plan | None = None,
        signals: dict[str, int] | None = None,
    ) -> None:
        """Record that the agent thought this tick and consume any step that came due.

        Readings already outside a freshly adopted plan's bounds are acknowledged, so the same
        breach does not reopen the gate every tick."""
        self._last_think = tick
        self._started = True
        if plan is not None:
            self._acted_steps.update(s[0] for s in plan.steps if s[0] <= tick)
        self._deviating = (
            self._outside(signals or {}, new_plan.expectations) if new_plan is not None else set()
        )

    @staticmethod
    def _outside(
        signals: dict[str, int], bounds: dict[str, tuple[int | None, int | None]]
    ) -> set[str]:
        outside = set()
        for name, (low, high) in bounds.items():
            value = signals.get(name)
            if value is not None and (
                (low is not None and value < low) or (high is not None and value > high)
            ):
                outside.add(name)
        return outside


def public_agent_view(
    view: PublicWorldView, seen: set[tuple[object, ...]], inbox: tuple[Message, ...] = ()
) -> AgentView:
    """Project a public world view; this is the only path crew and governors take."""
    if not isinstance(view, PublicWorldView):
        raise TypeError("Crew and governor views come from PublicWorldView only")
    signals = {f"oxygen.{reading.sensor}": reading.oxygen for reading in view.oxygen_sensors}
    signals.update(
        {
            "backup_oxygen": view.backup_oxygen,
            "credits": view.credits,
            "parts": view.parts,
            "repair_turns_remaining": view.repair_turns_remaining,
        }
    )
    items = []
    for evidence in view.evidence:
        key = _evidence_key(evidence)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            Observation(evidence.code, evidence.message, evidence.report_id or "", evidence.turn)
        )
    return AgentView(view.turn, signals, tuple(items), inbox)


def _evidence_key(evidence: TradeEvidence) -> tuple[object, ...]:
    return (evidence.turn, evidence.kind, evidence.code, evidence.report_id, evidence.message)
