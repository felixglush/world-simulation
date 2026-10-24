"""Farmer-to-farmer messages: plain claims with a delivery delay and per-day limits."""

from __future__ import annotations

from station_control.society.messages import Message

MAX_CHARS = 300
MAX_PER_DAY = 2
MAX_PER_PAIR = 30
LATENCY_SAME_COMMUNITY = 1
LATENCY_OTHER_COMMUNITY = 2


class FarmRouter:
    def __init__(self, communities: dict[str, str]) -> None:
        self._community = communities
        self._sent: list[Message] = []
        self._pending: list[Message] = []
        self._per_day: dict[tuple[str, int], int] = {}
        self._per_pair: dict[tuple[str, str], int] = {}

    @property
    def sent(self) -> tuple[Message, ...]:
        return tuple(self._sent)

    def directory(self, farmer: str) -> dict[str, str]:
        return {other: c for other, c in sorted(self._community.items()) if other != farmer}

    def send(self, sender: str, to: str, text: str, tick: int) -> tuple[Message | None, str | None]:
        if to not in self._community or to == sender:
            return None, "unknown_recipient"
        if not isinstance(text, str) or not text.strip():
            return None, "empty_message"
        if self._per_day.get((sender, tick), 0) >= MAX_PER_DAY:
            return None, "rate_limited"
        if self._per_pair.get((sender, to), 0) >= MAX_PER_PAIR:
            return None, "conversation_cap"
        far = self._community[sender] != self._community[to]
        latency = LATENCY_OTHER_COMMUNITY if far else LATENCY_SAME_COMMUNITY
        message = Message(
            f"msg-{len(self._sent) + 1}",
            sender,
            sender,
            to,
            text.strip()[:MAX_CHARS],
            tick,
            tick + latency,
        )
        self._sent.append(message)
        self._pending.append(message)
        self._per_day[(sender, tick)] = self._per_day.get((sender, tick), 0) + 1
        self._per_pair[(sender, to)] = self._per_pair.get((sender, to), 0) + 1
        return message, None

    def deliver_due(self, tick: int) -> dict[str, list[Message]]:
        due = [m for m in self._pending if m.deliver_at <= tick]
        self._pending = [m for m in self._pending if m.deliver_at > tick]
        inbox: dict[str, list[Message]] = {}
        for message in due:
            inbox.setdefault(message.recipient, []).append(message)
        return inbox
