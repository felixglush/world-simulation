"""Messages are claims: they become unverified reports and never change world state."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..deception import TradeReport, validate_reports
from ..trade_types import WorldState

MAX_MESSAGE_CHARS = 300
MAX_MESSAGES_PER_AGENT_PER_TICK = 2
MAX_MESSAGES_PER_PAIR = 12
DELIVERY_LATENCY = 1
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    sender: str
    identity: str
    recipient: str
    text: str
    sent_at: int
    deliver_at: int
    intent: str = "inform"
    upstream: str | None = None


class MessageRouter:
    """Validate, rate-limit, and deliver messages as reports or mailbox entries."""

    def __init__(
        self,
        agent_worlds: dict[str, str],
        identities: dict[str, frozenset[str]],
        mailboxes: dict[str, frozenset[str]],
        registry: frozenset[str] = frozenset(),
    ) -> None:
        """`agent_worlds` maps agent to home world; `identities` maps agent to the source IDs it
        may speak as; `mailboxes` maps agent to non-world recipient IDs it receives for."""
        self._worlds = agent_worlds
        self._identities = identities
        self._mailboxes = mailboxes
        self.registry = registry
        self._sent: list[Message] = []
        self._per_tick: dict[tuple[str, int], int] = {}
        self._per_pair: dict[tuple[str, str], int] = {}
        self._pending: list[Message] = []

    @property
    def sent(self) -> tuple[Message, ...]:
        return tuple(self._sent)

    def recipients(self) -> set[str]:
        return {*self._worlds.values(), *(r for ids in self._mailboxes.values() for r in ids)}

    def directory_for(self, world_id: str) -> list[str]:
        """Recipients a world can see: other worlds, registered parties, and anyone who has
        already written to it. Unlisted mailbox identities stay hidden until they speak."""
        spoken = {m.identity for m in self._sent if m.recipient == world_id}
        mailbox_ids = {r for ids in self._mailboxes.values() for r in ids}
        visible = {*self._worlds.values()} - {world_id}
        visible |= {r for r in mailbox_ids if r in self.registry or r in spoken}
        return sorted(visible)

    def owner_of(self, recipient: str) -> str | None:
        """The agent that reads messages addressed to this recipient, if any."""
        for agent, world in self._worlds.items():
            if world == recipient:
                return agent
        for agent, ids in self._mailboxes.items():
            if recipient in ids:
                return agent
        return None

    def send(
        self,
        state: WorldState,
        sender: str,
        identity: str,
        recipient: str,
        text: str,
        intent: str = "inform",
        upstream: str | None = None,
    ) -> tuple[WorldState, Message | None, str | None]:
        """Queue one message; returns (state, message, rejection)."""
        tick = state.station.turn
        if not isinstance(text, str) or not text.strip():
            return state, None, "empty_message"
        if identity not in self._identities.get(sender, frozenset()) or not _IDENTITY.fullmatch(
            identity
        ):
            return state, None, "identity_not_permitted"
        if recipient not in self.recipients() or recipient == self._worlds.get(sender):
            return state, None, "unknown_recipient"
        if self._per_tick.get((sender, tick), 0) >= MAX_MESSAGES_PER_AGENT_PER_TICK:
            return state, None, "rate_limited"
        pair = (sender, recipient)
        if self._per_pair.get(pair, 0) >= MAX_MESSAGES_PER_PAIR:
            return state, None, "conversation_cap"
        if len(state.reports) >= 250:
            return state, None, "report_capacity"
        message = Message(
            f"msg-{len(self._sent) + 1}",
            sender,
            identity,
            recipient,
            text.strip()[:MAX_MESSAGE_CHARS],
            tick,
            tick + DELIVERY_LATENCY,
            intent if intent in {"inform", "deceive", "probe"} else "inform",
            upstream,
        )
        report = None
        if recipient in self._worlds.values():
            report = TradeReport(
                message.message_id,
                identity,
                recipient,
                message.text,
                tick,
                message.deliver_at,
                upstream_report_id=upstream,
            )
            try:
                validate_reports(
                    state.reports + (report,),
                    {"station", *self._worlds.values(), *(w.world_id for w in state.inventories)},
                )
            except ValueError:
                return state, None, "invalid_echo" if upstream else "invalid_report"
        self._sent.append(message)
        self._per_tick[(sender, tick)] = self._per_tick.get((sender, tick), 0) + 1
        self._per_pair[pair] = self._per_pair.get(pair, 0) + 1
        if report is not None:
            state = replace(state, reports=state.reports + (report,))
        self._pending.append(message)
        return state, message, None

    def deliver_due(self, tick: int) -> tuple[dict[str, list[Message]], list[Message]]:
        """Messages due now: mailbox inboxes by reading agent, and every delivered message.

        Messages to a world arrive as report evidence in that world's public view instead."""
        due = [m for m in self._pending if m.deliver_at <= tick]
        self._pending = [m for m in self._pending if m.deliver_at > tick]
        inbox: dict[str, list[Message]] = {}
        for message in due:
            if message.recipient in self._worlds.values():
                continue
            owner = self.owner_of(message.recipient)
            if owner is not None:
                inbox.setdefault(owner, []).append(message)
        return inbox, due
