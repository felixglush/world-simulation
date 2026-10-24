"""Per-agent memory stream: observe, merge routine runs, retrieve, and reflect."""

from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass, field
from typing import Protocol

RECENCY_DECAY = 0.995
REFLECTION_THRESHOLD = 50
REFLECTION_WINDOW = 100
EMBEDDING_DIMENSIONS = 64
_WORD = re.compile(r"[a-z0-9]+")
MAX_TEXT_CHARS = 400

# Fixed importance for structured evidence, so the model only scores free text.
_EVIDENCE_IMPORTANCE = (
    ("failure", 9),
    ("critical", 9),
    ("alert", 8),
    ("inspection", 7),
    ("material_defect", 8),
    ("residual_damage", 8),
    ("trace", 7),
    ("origin_traced", 7),
    ("provenance", 7),
    ("quarantine", 6),
    ("assay", 6),
    ("repair_complete", 6),
    ("repair_not_needed", 4),
    ("command_rejected", 4),
    ("unverified_report", 5),
    ("report", 5),
    ("arrival", 4),
    ("calibration", 5),
    ("purchase", 3),
    ("settlement", 3),
)


def evidence_importance(code: str) -> int:
    lowered = code.lower()
    return next((score for needle, score in _EVIDENCE_IMPORTANCE if needle in lowered), 3)


class Embedder(Protocol):
    def embed(self, text: str) -> tuple[float, ...]: ...


class HashingEmbedder:
    """Deterministic bag-of-words embedding; no network and no extra dependency."""

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in _WORD.findall(text.lower()):
            vector[zlib.crc32(word.encode()) % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return tuple(value / norm for value in vector) if norm else tuple(vector)


@dataclass(slots=True)
class MemoryRecord:
    record_id: str
    tick: int
    kind: str
    text: str
    importance: int
    source: str = ""
    cites: tuple[str, ...] = ()
    last_access: int = 0
    vector: tuple[float, ...] = field(default=(), repr=False)


@dataclass(slots=True)
class _Run:
    start: int
    end: int
    first: int
    last: int
    direction: int


class MemoryStream:
    """Append-only records for one agent; the owner and every source stay on the record."""

    def __init__(
        self,
        owner: str,
        embedder: Embedder | None = None,
        *,
        reflection_threshold: int = REFLECTION_THRESHOLD,
        run_tolerance: int = 10,
    ) -> None:
        self.owner = owner
        self._embedder = embedder or HashingEmbedder()
        self._records: list[MemoryRecord] = []
        self._runs: dict[str, _Run] = {}
        self._importance_since_reflection = 0
        self._reflection_threshold = reflection_threshold
        self._run_tolerance = run_tolerance

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records)

    def ids(self) -> set[str]:
        return {record.record_id for record in self._records}

    def add(
        self,
        tick: int,
        kind: str,
        text: str,
        importance: int,
        *,
        source: str = "",
        cites: tuple[str, ...] = (),
    ) -> MemoryRecord:
        text = text[:MAX_TEXT_CHARS]
        record = MemoryRecord(
            f"{self.owner}-{len(self._records) + 1}",
            tick,
            kind,
            text,
            max(1, min(10, importance)),
            source,
            cites,
            tick,
            self._embedder.embed(text),
        )
        self._records.append(record)
        if kind != "reflection":
            self._importance_since_reflection += record.importance
        return record

    def observe_signal(self, tick: int, name: str, value: int) -> None:
        """Merge a routine reading into a run; a break in the run becomes one record."""
        run = self._runs.get(name)
        if run is not None:
            delta = value - run.last
            direction = (delta > 0) - (delta < 0)
            same = direction == 0 or run.direction in (0, direction)
            if abs(delta) <= self._run_tolerance and same:
                run.end, run.last = tick, value
                run.direction = run.direction or direction
                return
            self._flush(name)
        self._runs[name] = _Run(tick, tick, value, value, 0)

    def flush(self) -> None:
        for name in sorted(self._runs):
            self._flush(name)

    def trend_summary(self) -> list[str]:
        """Current runs for the prompt, without closing them."""
        return [_describe(name, run) for name, run in sorted(self._runs.items())]

    def _flush(self, name: str) -> None:
        run = self._runs.pop(name, None)
        if run is not None:
            self.add(run.end, "observation", _describe(name, run), 1, source=f"signal:{name}")

    def retrieve(self, query: str, tick: int, limit: int = 8) -> list[MemoryRecord]:
        """Rank by recency, importance, and relevance, each scaled to 0-1 and equally weighted."""
        if not self._records:
            return []
        query_vector = self._embedder.embed(query)
        recency = [RECENCY_DECAY ** max(0, tick - record.last_access) for record in self._records]
        importance = [float(record.importance) for record in self._records]
        relevance = [
            sum(a * b for a, b in zip(query_vector, record.vector, strict=False))
            for record in self._records
        ]
        totals = [
            sum(parts)
            for parts in zip(_scale(recency), _scale(importance), _scale(relevance), strict=True)
        ]
        ranked = sorted(range(len(self._records)), key=lambda i: (-totals[i], i))[:limit]
        chosen = [self._records[i] for i in ranked]
        for record in chosen:
            record.last_access = tick
        return chosen

    def reflection_due(self) -> bool:
        return self._importance_since_reflection >= self._reflection_threshold

    def recent(self, count: int = REFLECTION_WINDOW) -> list[MemoryRecord]:
        return self._records[-count:]

    def reflect(self, tick: int, insights: list[tuple[str, tuple[str, ...]]]) -> list[MemoryRecord]:
        """Store insights that cite only records this agent holds; drop the rest."""
        known = self.ids()
        stored = [
            self.add(tick, "reflection", text, 8, cites=tuple(cites))
            for text, cites in insights
            if cites and all(cite in known for cite in cites)
        ]
        self._importance_since_reflection = 0
        return stored


def _describe(name: str, run: _Run) -> str:
    if run.start == run.end:
        return f"{name} {run.last} at tick {run.end}"
    verb = (
        "steady at"
        if run.first == run.last
        else "falling to"
        if run.last < run.first
        else "rising to"
    )
    return f"{name} {run.first} {verb} {run.last}, ticks {run.start} to {run.end}"


def _scale(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return [0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]
