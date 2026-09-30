"""Application-facing controller contracts and bounded decision DTOs.

Provider adapters implement these contracts. They receive only observations and
selected public evidence, never the authoritative ``StationState``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Protocol

from .domain import Evidence, SensorReading

QUESTION_VERSION = "jev-questions-v1"
RUBRIC_VERSION = "jev-rubric-v1"
INSTRUCTION_VERSION = "captain-instructions-v1"


class Subsystem(StrEnum):
    LIFE_SUPPORT = "life_support"
    POWER = "power"
    LOGISTICS = "logistics"
    UNKNOWN = "unknown"


class NoulOutcome(StrEnum):
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


class ActionRequestKind(StrEnum):
    READ_HISTORY = "read_history"
    REQUEST_CLARIFICATION = "request_clarification"
    INSPECT = "inspect"
    ASSIGN_REPAIR = "assign_repair"
    ACTIVATE_BACKUP = "activate_backup"
    ORDER_SUPPLIES = "order_supplies"
    DEFER = "defer"
    CLOSE = "close"


class ProviderErrorCode(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    INVALID_INPUT = "invalid_input"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class ProviderError(Exception):
    """Sanitized provider failure that contains no prompt or credential data."""

    def __init__(
        self,
        code: ProviderErrorCode | str,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self.code = ProviderErrorCode(code)
        self.metadata = dict(metadata or {})
        super().__init__(self.code.value)


ProviderMetadata = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class WorkflowEvidence:
    """App-generated public response with an ID outside the world's sequence."""

    sequence: str
    turn: int
    kind: str
    message: str


PublicEvidence = Evidence | WorkflowEvidence


@dataclass(frozen=True, slots=True)
class StationView:
    """Public station values supplied to decision makers; contains no history."""

    turn: int
    oxygen_sensors: tuple[SensorReading, ...]
    backup_oxygen: int
    parts: int
    credits: int
    available_crew: int


@dataclass(frozen=True, slots=True, init=False)
class ActionDescriptor:
    """An action contract whose nested schema cannot be changed by consumers."""

    kind: ActionRequestKind
    description: str
    _json_schema: Mapping[str, object] = field(repr=False)

    def __init__(
        self,
        kind: ActionRequestKind,
        description: str,
        json_schema: Mapping[str, object] | None = None,
    ) -> None:
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "description", description)
        object.__setattr__(
            self, "_json_schema", deepcopy(json_schema if json_schema is not None else {})
        )

    @property
    def json_schema(self) -> Mapping[str, object]:
        return deepcopy(self._json_schema)


@dataclass(frozen=True, slots=True)
class DispatchContext:
    report: Evidence
    station: StationView
    evidence: tuple[PublicEvidence, ...]
    question_version: str
    rubric_version: str


@dataclass(frozen=True, slots=True)
class DispatchJudgment:
    subsystem: Subsystem
    safeguard_request: NoulOutcome
    diagnosis_supported: NoulOutcome
    urgency: int
    rationale: str = ""
    metadata: ProviderMetadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionRequest:
    kind: ActionRequestKind
    target: str | None = None
    quantity: int | None = None
    follow_up_turn: int | None = None
    reason: str | None = None
    evidence_sequences: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class CaptainContext:
    incident: Evidence
    station: StationView
    evidence: tuple[PublicEvidence, ...]
    allowed_actions: tuple[ActionDescriptor, ...]
    inspection_budget_remaining: int
    instruction_version: str


@dataclass(frozen=True, slots=True)
class CaptainDecision:
    action: ActionRequest
    rationale: str = ""
    metadata: ProviderMetadata = field(default_factory=dict)


class DispatchProvider(Protocol):
    def classify(self, context: DispatchContext) -> DispatchJudgment: ...


class CaptainProvider(Protocol):
    def decide(self, context: CaptainContext) -> CaptainDecision: ...
