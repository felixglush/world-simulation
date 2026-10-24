"""Farmer-to-farmer messages: plain claims with a delivery delay and per-day limits."""

from __future__ import annotations

import re

from station_control.society.messages import Message

MAX_CHARS = 300
MAX_PER_DAY = 2
MAX_PER_PAIR = 30
LATENCY_SAME_COMMUNITY = 1
LATENCY_OTHER_COMMUNITY = 2
MAX_MEMBERS = 12
_CHANNEL = re.compile(r"#[a-z][a-z0-9_]{0,23}")


class FarmRouter:
    def __init__(self, communities: dict[str, str]) -> None:
        self._community = communities
        self._sent: list[Message] = []
        self._pending: list[Message] = []
        self._per_day: dict[tuple[str, int], int] = {}
        self._per_pair: dict[tuple[str, str], int] = {}
        # Every community starts with a channel everyone in it can read; farmers make more.
        self._channels: dict[str, list[str]] = {}
        for farmer, community in sorted(communities.items()):
            self._channels.setdefault(f"#{community.lower()}", []).append(farmer)

    @property
    def sent(self) -> tuple[Message, ...]:
        return tuple(self._sent)

    def directory(self, farmer: str) -> dict[str, str]:
        return {other: c for other, c in sorted(self._community.items()) if other != farmer}

    def channels_for(self, farmer: str) -> dict[str, list[str]]:
        return {n: list(m) for n, m in sorted(self._channels.items()) if farmer in m}

    def send(
        self,
        sender: str,
        to: str,
        text: str,
        tick: int,
        invite: tuple[str, ...] = (),
        leave: bool = False,
    ) -> tuple[Message | None, str | None]:
        """A direct message to one farmer, or a post to a channel (`#name`) and all its members.

        Posting to a name nobody has used creates the channel with the sender in it. Members can
        invite others when they post, and leave after posting. Each copy travels on its own.
        """
        channel = to.startswith("#")
        if channel and not (to in self._channels or _CHANNEL.fullmatch(to)):
            return None, "bad_channel_name"
        if not channel and (to not in self._community or to == sender):
            return None, "unknown_recipient"
        if channel and to in self._channels and sender not in self._channels[to]:
            return None, "not_a_member"
        if not isinstance(text, str) or not text.strip():
            return None, "empty_message"
        if self._per_day.get((sender, tick), 0) >= MAX_PER_DAY:
            return None, "rate_limited"
        if self._per_pair.get((sender, to), 0) >= MAX_PER_PAIR:
            return None, "conversation_cap"
        text = text.strip()[:MAX_CHARS]
        if channel:
            members = self._channels.setdefault(to, [sender])
            for other in invite:
                if other in self._community and other not in members and len(members) < MAX_MEMBERS:
                    members.append(other)
            recipients = [m for m in members if m != sender]
            if not recipients:
                return None, "nobody_to_hear"
            text = f"[{to}] {text}"
            if leave and sender in members:
                members.remove(sender)
        else:
            recipients = [to]
        first: Message | None = None
        for recipient in recipients:
            far = self._community[sender] != self._community[recipient]
            latency = LATENCY_OTHER_COMMUNITY if far else LATENCY_SAME_COMMUNITY
            message = Message(
                f"msg-{len(self._sent) + 1}", sender, sender, recipient, text, tick, tick + latency
            )
            self._sent.append(message)
            self._pending.append(message)
            first = first or message
        self._per_day[(sender, tick)] = self._per_day.get((sender, tick), 0) + 1
        self._per_pair[(sender, to)] = self._per_pair.get((sender, to), 0) + 1
        return first, None

    def deliver_due(self, tick: int) -> dict[str, list[Message]]:
        due = [m for m in self._pending if m.deliver_at <= tick]
        self._pending = [m for m in self._pending if m.deliver_at > tick]
        inbox: dict[str, list[Message]] = {}
        for message in due:
            inbox.setdefault(message.recipient, []).append(message)
        return inbox
