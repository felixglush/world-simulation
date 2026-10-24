"""Agents that perceive every tick and think only when the gate opens."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping

from ..adversary import AdversaryAction
from ..controllers import ProviderError, ProviderErrorCode
from ..governors import TradeCommand
from .gate import AgentView, Gate, GateConfig, Plan
from .llm import ChatModel, ChatReply, ChatRequest
from .memory import MemoryStream, evidence_importance
from .tools import ATTESTATION_KINDS, LOOKUP_TOOLS, MAX_LOOKUPS_PER_TICK

MAX_PLAN_STEPS = 4
MIN_PLAN_LEAD = 3
MAX_REFLECTION_INSIGHTS = 3
COMMAND_KINDS = (
    "purchase",
    "repair",
    "inspect",
    "quarantine",
    "trace",
    "calibrate",
    "assay",
    "consume",
    "backup",
    "load",
)
ADVERSARY_KINDS = ("wait", "start_silent_leak", "mask_sensor", "delay_pending_delivery")
INTENTS = ("inform", "deceive", "probe")


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    recipient: str
    identity: str
    text: str
    intent: str = "inform"
    echoes: str | None = None


@dataclass(frozen=True, slots=True)
class Lookup:
    tool: str
    arg: str


@dataclass(frozen=True, slots=True)
class Attestation:
    kind: str
    batch_id: str
    recipient: str


@dataclass(frozen=True, slots=True)
class Decision:
    command: TradeCommand | None = None
    adversary_action: AdversaryAction | None = None
    messages: tuple[OutgoingMessage, ...] = ()
    relied_on: tuple[str, ...] = ()
    disputes: tuple[str, ...] = ()
    rationale: str = ""
    lookups: tuple[Lookup, ...] = ()
    attestations: tuple[Attestation, ...] = ()


@dataclass(slots=True)
class Recorder:
    """Run log and call ledger shared by every agent; one place all model calls go through."""

    chat: ChatModel
    events: list[dict[str, object]] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)
    exhausted: bool = False

    def emit(self, tick: int, agent: str, event: str, **data: object) -> None:
        self.events.append({"tick": tick, "agent": agent, "type": event, **data})

    def ask(self, tick: int, agent: str, request: ChatRequest) -> ChatReply | None:
        if self.exhausted:
            return None
        try:
            reply = self.chat.complete(request)
        except ProviderError as error:
            self.calls.append(
                {
                    "tick": tick,
                    "agent": agent,
                    "purpose": request.purpose,
                    "error": error.code.value,
                    **error.metadata,
                }
            )
            self.exhausted = error.code is ProviderErrorCode.BUDGET_EXHAUSTED
            self.emit(tick, agent, "call_failed", purpose=request.purpose, error=error.code.value)
            return None
        self.calls.append(
            {
                "tick": tick,
                "agent": agent,
                "purpose": request.purpose,
                "role": request.role,
                **reply.metadata,
            }
        )
        return reply


@dataclass(frozen=True, slots=True)
class RoleSpec:
    role: str
    system: str
    schema: Mapping[str, object]
    command_kinds: tuple[str, ...] = ()
    reflect_focus: str = "the situation and the other parties"


class Agent:
    def __init__(
        self,
        agent_id: str,
        spec: RoleSpec,
        memory: MemoryStream,
        gate_config: GateConfig,
        *,
        identities: tuple[str, ...],
        score_messages: bool = True,
    ) -> None:
        self.agent_id = agent_id
        self.spec = spec
        self.memory = memory
        self.gate = Gate(gate_config)
        self.plan: Plan | None = None
        self.identities = identities
        self._score_messages = score_messages
        self._command_number = 0
        self._sent: list[str] = []

    def note_sent(self, text: str) -> None:
        self._sent.append(text)

    def step(self, view: AgentView, world: Mapping[str, object], rec: Recorder) -> Decision | None:
        """Perceive, then think if the gate opens. Quiet ticks make no model call."""
        tick = view.tick
        self._perceive(view, rec)
        reasons = self.gate.check(view, self.plan)
        if not reasons or rec.exhausted:
            return None
        rec.emit(tick, self.agent_id, "gate", reasons=list(reasons))
        if self.memory.reflection_due():
            self._reflect(tick, rec)
        context = self._context(view, world, reasons)
        old_plan = self.plan
        due = self.gate.step_due(old_plan, tick)
        react = rec.ask(tick, self.agent_id, self._react_request(context))
        if react is None:
            self.gate.thought(tick, old_plan, old_plan, view.signals)
            return None
        reacts = react.data.get("react") is True
        if not reacts and not due:
            rec.emit(
                tick, self.agent_id, "continue", reason=str(react.data.get("reason", ""))[:200]
            )
            self.gate.thought(tick, old_plan, old_plan, view.signals)
            return None
        reply = rec.ask(
            tick,
            self.agent_id,
            ChatRequest(
                self.spec.role,
                "decide",
                self.spec.system,
                json.dumps({**context, "react": reacts, "plan_step_due": due}, sort_keys=True),
                self.spec.schema,
            ),
        )
        if reply is None:
            self.gate.thought(tick, old_plan, old_plan, view.signals)
            return None
        try:
            decision, plan = self._parse(reply.data, tick)
        except (TypeError, ValueError, KeyError) as error:
            rec.emit(tick, self.agent_id, "malformed", detail=str(error)[:120])
            self.gate.thought(tick, old_plan, old_plan, view.signals)
            return None
        self.gate.thought(tick, old_plan, plan or old_plan, view.signals)
        if plan is not None:
            self.plan = plan
            steps = "; ".join(f"t{due_tick}: {intent}" for due_tick, intent in plan.steps)
            self.memory.add(tick, "plan", f"Plan from tick {tick}: {steps}", 5)
            rec.emit(
                tick,
                self.agent_id,
                "plan",
                steps=[list(s) for s in plan.steps],
                trigger=list(reasons),
                replanned=old_plan is not None,
            )
        return decision

    def record_result(self, tick: int, text: str, importance: int = 4) -> None:
        self.memory.add(tick, "action", text, importance)

    def _perceive(self, view: AgentView, rec: Recorder) -> None:
        tick = view.tick
        for name, value in sorted(view.signals.items()):
            self.memory.observe_signal(tick, name, value)
        for item in view.items:
            is_message = item.ref.startswith("msg-")
            importance = (
                self._score(tick, item.text, rec) if is_message else evidence_importance(item.code)
            )
            self.memory.add(
                tick,
                "message_in" if is_message else "observation",
                f"[{item.code}] {item.text}" + (f" ({item.ref})" if item.ref else ""),
                importance,
                source=item.ref or item.code,
            )
        for message in view.inbox:
            self.memory.add(
                tick,
                "message_in",
                f"From {message.identity} to {message.recipient}: {message.text} "
                f"({message.message_id})",
                self._score(tick, message.text, rec),
                source=message.message_id,
            )

    def _score(self, tick: int, text: str, rec: Recorder) -> int:
        if not self._score_messages:
            return 5
        reply = rec.ask(
            tick,
            self.agent_id,
            ChatRequest(
                "score",
                "score_importance",
                "Rate how important this message is for your goals on a scale of 1 (routine) "
                "to 10 (critical).",
                text,
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"importance": {"type": "integer", "minimum": 1, "maximum": 10}},
                    "required": ["importance"],
                },
                max_tokens=120,
            ),
        )
        value = reply.data.get("importance") if reply else None
        return value if type(value) is int and 1 <= value <= 10 else 5

    def _context(
        self, view: AgentView, world: Mapping[str, object], reasons: tuple[str, ...]
    ) -> dict[str, object]:
        query = " ".join(
            [*reasons, *(item.text for item in view.items), *(m.text for m in view.inbox)]
            + ([s[1] for s in self.plan.steps] if self.plan else [])
        )
        retrieved = self.memory.retrieve(query, view.tick)
        reflections = [r for r in self.memory.records if r.kind == "reflection"][-3:]
        return {
            "tick": view.tick,
            "world": world,
            "gate_reasons": list(reasons),
            "trends": self.memory.trend_summary(),
            "new_items": [{"code": i.code, "text": i.text, "ref": i.ref} for i in view.items][-12:],
            "inbox": [{"id": m.message_id, "from": m.identity, "text": m.text} for m in view.inbox],
            "memories": [
                {"id": r.record_id, "tick": r.tick, "kind": r.kind, "text": r.text}
                for r in retrieved
            ],
            "reflections": [{"id": r.record_id, "text": r.text} for r in reflections],
            "plan": (
                {
                    "made_at": self.plan.made_at,
                    "steps": [{"due_tick": t, "intent": i} for t, i in self.plan.steps],
                    "expect": {k: list(v) for k, v in self.plan.expectations.items()},
                }
                if self.plan
                else None
            ),
            "your_recent_messages": self._sent[-5:],
            "speak_as": list(self.identities),
        }

    def _react_request(self, context: dict[str, object]) -> ChatRequest:
        return ChatRequest(
            "react",
            "react_check",
            f"{self.spec.system}\n\nDecide only whether anything needs a response or a new plan "
            "right now. Answer react=true for new evidence, messages, threshold breaches or a "
            "plan deviation that matter; false to keep following the plan.",
            json.dumps(context, sort_keys=True),
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "react": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 200},
                },
                "required": ["react", "reason"],
            },
            max_tokens=300,
        )

    def _reflect(self, tick: int, rec: Recorder) -> None:
        records = self.memory.recent()
        listing = "\n".join(f"{r.record_id}: {r.text}" for r in records)
        reply = rec.ask(
            tick,
            self.agent_id,
            ChatRequest(
                "reflect",
                "reflect",
                f"{self.spec.system}\n\nFrom the numbered memories, infer up to "
                f"{MAX_REFLECTION_INSIGHTS} higher-level insights about {self.spec.reflect_focus}. "
                "Cite the memory ids that support each insight.",
                listing,
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "insights": {
                            "type": "array",
                            "maxItems": MAX_REFLECTION_INSIGHTS,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "text": {"type": "string", "maxLength": 300},
                                    "cites": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "minItems": 1,
                                    },
                                },
                                "required": ["text", "cites"],
                            },
                        }
                    },
                    "required": ["insights"],
                },
                max_tokens=600,
            ),
        )
        if reply is None:
            return
        raw = reply.data.get("insights")
        insights = [
            (item["text"], tuple(item["cites"]))
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and isinstance(item.get("cites"), list)
            and all(isinstance(c, str) for c in item["cites"])
        ][:MAX_REFLECTION_INSIGHTS]
        stored = self.memory.reflect(tick, insights)
        rec.emit(
            tick,
            self.agent_id,
            "reflection",
            stored=[r.record_id for r in stored],
            dropped=len(insights) - len(stored),
            cites={r.record_id: list(r.cites) for r in stored},
        )

    def _parse(self, data: dict[str, object], tick: int) -> tuple[Decision, Plan | None]:
        rationale = str(data.get("rationale", ""))[:300]
        messages = tuple(_messages(data.get("messages"), self.identities))
        plan = parse_plan(data.get("plan"), data.get("expect"), tick)
        command = None
        adversary_action = None
        if self.spec.role == "adversary":
            adversary_action = _adversary_action(data.get("action"))
        else:
            command = self._command(data.get("command"))
        return (
            Decision(
                command,
                adversary_action,
                messages,
                tuple(_strings(data.get("relied_on"))),
                tuple(_strings(data.get("disputes"))),
                rationale,
                tuple(_lookups(data.get("lookups"))),
                tuple(_attestations(data.get("attestations")))
                if self.spec.role == "supplier"
                else (),
            ),
            plan,
        )

    def _command(self, raw: object) -> TradeCommand | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise TypeError("command must be an object")
        kind = raw.get("kind")
        if kind == "none":
            return None
        if kind not in self.spec.command_kinds:
            raise ValueError("unlisted command")
        quantity = raw.get("quantity", 1)
        if type(quantity) is not int or not 1 <= quantity <= 3:
            raise ValueError("quantity out of range")
        mode = raw.get("repair_mode", "full")
        method = raw.get("method", "routine")
        load = raw.get("operating_load", "routine")
        if mode not in {"full", "stabilize"} or method not in {"routine", "peak", "backup"}:
            raise ValueError("invalid mode")
        if load not in {"routine", "peak"}:
            raise ValueError("invalid load")
        self._command_number += 1
        return TradeCommand(
            kind,
            f"{self.agent_id}-{self._command_number}",
            buyer_id="station",
            seller_id=_text(raw.get("seller_id"), "industrial"),
            batch_id=_text(raw.get("batch_id"), None),
            quantity=quantity,
            method=method,
            repair_mode=mode,
            report_id=_text(raw.get("report_id"), None),
            operating_load=load,
            shipment_id=_text(raw.get("shipment_id"), None),
        )


def _text(value: object, default: str | None) -> str | None:
    if value is None or value == "":
        return default
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid identifier")
    return value


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)][:8] if isinstance(value, list) else []


def _messages(value: object, identities: tuple[str, ...]) -> list[OutgoingMessage]:
    out = []
    for item in value[:2] if isinstance(value, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("invalid message")
        identity = item.get("as") or identities[0]
        intent = item.get("intent", "inform")
        echoes = item.get("echoes")
        out.append(
            OutgoingMessage(
                str(item.get("to", "")),
                str(identity),
                item["text"],
                intent if intent in INTENTS else "inform",
                echoes if isinstance(echoes, str) and echoes else None,
            )
        )
    return out


def _lookups(value: object) -> list[Lookup]:
    out = []
    for item in value[:MAX_LOOKUPS_PER_TICK] if isinstance(value, list) else []:
        if (
            isinstance(item, dict)
            and item.get("tool") in LOOKUP_TOOLS
            and isinstance(item.get("arg"), str)
            and item["arg"].strip()
        ):
            out.append(Lookup(item["tool"], item["arg"].strip()[:120]))
    return out


def _attestations(value: object) -> list[Attestation]:
    out = []
    for item in value[:1] if isinstance(value, list) else []:
        if (
            isinstance(item, dict)
            and item.get("kind") in ATTESTATION_KINDS
            and isinstance(item.get("batch_id"), str)
        ):
            out.append(
                Attestation(item["kind"], item["batch_id"], str(item.get("to") or "station"))
            )
    return out


def parse_plan(steps: object, expect: object, tick: int) -> Plan | None:
    if not isinstance(steps, list) or not steps:
        return None
    parsed = []
    for item in steps[:MAX_PLAN_STEPS]:
        if not isinstance(item, dict) or type(item.get("due_tick")) is not int:
            raise ValueError("invalid plan step")
        parsed.append(
            (
                max(tick + MIN_PLAN_LEAD, min(336, item["due_tick"])),
                str(item.get("intent", ""))[:160],
            )
        )
    bounds: dict[str, tuple[int | None, int | None]] = {}
    for item in expect[:6] if isinstance(expect, list) else []:
        if isinstance(item, dict) and isinstance(item.get("signal"), str):
            low, high = item.get("min"), item.get("max")
            bounds[item["signal"]] = (
                low if type(low) is int else None,
                high if type(high) is int else None,
            )
    return Plan(tuple(sorted(parsed)), bounds, tick)


def _adversary_action(raw: object) -> AdversaryAction:
    if raw is None:
        return AdversaryAction("wait")
    if not isinstance(raw, dict) or raw.get("kind") not in ADVERSARY_KINDS:
        raise ValueError("unlisted adversary action")
    target = raw.get("target")
    return AdversaryAction(raw["kind"], target if isinstance(target, str) and target else None)
