"""Bounded Jev and captain control for the station's public trade world."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Mapping

from .controllers import (
    INSTRUCTION_VERSION,
    QUESTION_VERSION,
    RUBRIC_VERSION,
    ActionDescriptor,
    ActionRequest,
    ActionRequestKind,
    CaptainContext,
    CaptainDecision,
    CaptainProvider,
    DispatchContext,
    DispatchJudgment,
    DispatchProvider,
    ProviderError,
    ProviderErrorCode,
    PublicEvent,
    StationView,
    dispatch_requires_review,
)
from .domain import CRITICAL_OXYGEN, Evidence, EvidenceCode
from .governors import TradeCommand
from .trade import PublicWorldView
from .trade_types import TradeEvidence, TradeEvidenceKind

_MAX_HISTORY = 24
_MAX_TURNS = 336
_PURCHASE_MAX = 3


@dataclass(frozen=True, slots=True)
class CrewReview:
    """Safe per-provider-call audit data; prompts and model rationale are omitted."""

    world_id: str
    turn: int
    phase: str
    routed: bool
    provider_error: str | None = None
    model_calls: int = 0
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class CrewGovernor:
    """Run public event triage, then propose one whitelisted station action."""

    def __init__(
        self,
        captain: CaptainProvider,
        dispatcher: DispatchProvider | None = None,
        *,
        escalation_threshold: int = 55,
        history_limit: int = _MAX_HISTORY,
    ) -> None:
        if not callable(getattr(captain, "decide", None)):
            raise ValueError("Crew control requires a CaptainProvider")
        if dispatcher is not None and not callable(getattr(dispatcher, "classify", None)):
            raise ValueError("Crew control requires a DispatchProvider")
        if type(escalation_threshold) is not int or not 0 <= escalation_threshold <= 100:
            raise ValueError("Escalation threshold must be from 0 to 100")
        if type(history_limit) is not int or not 1 <= history_limit <= _MAX_HISTORY:
            raise ValueError(f"History limit must be from 1 to {_MAX_HISTORY}")
        self._captain = captain
        self._dispatcher = dispatcher
        self._escalation_threshold = escalation_threshold
        self._history_limit = history_limit
        self._observed_turn = -1
        self._seen_this_turn: set[tuple[object, ...]] = set()
        self._observation_classified_turn = -1
        self._pending_route = False
        self._review_due = 1
        self._providers_stopped = False
        self._command_number = 0
        self._reviews: list[CrewReview] = []

    def decide(self, observation: PublicWorldView) -> TradeCommand | None:
        """Classify newly observed evidence and route it to the captain when needed."""
        if not isinstance(observation, PublicWorldView) or observation.world_id != "station":
            self._record_local_failure("world", getattr(observation, "turn", 0))
            return None

        view = self._bounded_view(observation)
        fresh = self._fresh_events(observation)
        observation_event = None
        if self._observation_classified_turn != view.turn:
            observation_event = _observation_event(view, phase="world")
            self._mark_seen(observation_event)
            self._observation_classified_turn = view.turn
        events = (*fresh, *((observation_event,) if observation_event is not None else ()))
        pending_route = self._pending_route
        self._pending_route = False
        routed = pending_route
        if events and not self._providers_stopped:
            routed = self._classify(view, events, phase="world") or routed
        due = view.turn >= self._review_due
        critical = _has_critical_public_condition(view) or any(_is_alert(item) for item in events)
        if self._review_due == view.turn:
            self._review_due = _MAX_TURNS + 1
        if due:
            routed = True
        if critical:
            routed = True
        if not routed:
            return None
        if self._providers_stopped:
            return None
        return self._captain_command(view, events)

    def observe_action_result(self, observation: PublicWorldView) -> None:
        """Classify a just-applied action result; any captain response waits a turn."""
        if not isinstance(observation, PublicWorldView) or observation.world_id != "station":
            self._record_local_failure("action", getattr(observation, "turn", 0))
            self._pending_route = True
            return
        view = self._bounded_view(observation)
        self._review_due = min(self._review_due, view.turn + 1)
        fresh = self._fresh_events(observation)
        result_event = _observation_event(view, phase="action result")
        if _event_key(result_event) not in self._seen_this_turn:
            self._mark_seen(result_event)
            events = (*fresh, result_event)
        else:
            events = fresh
        if events and not self._providers_stopped and self._dispatcher is not None:
            self._pending_route = (
                self._classify(view, events, phase="action") or self._pending_route
            )

    def drain_reviews(self) -> tuple[CrewReview, ...]:
        reviews = tuple(self._reviews)
        self._reviews.clear()
        return reviews

    def _classify(
        self,
        view: PublicWorldView,
        events: tuple[TradeEvidence, ...],
        *,
        phase: str,
    ) -> bool:
        if self._dispatcher is None:
            return False
        anchor = _as_evidence(events[-1])
        context = DispatchContext(
            report=anchor,
            station=_station_view(view),
            evidence=tuple(_as_evidence(item) for item in view.evidence),
            question_version=QUESTION_VERSION,
            rubric_version=RUBRIC_VERSION,
            events=tuple(_public_event(item) for item in events[-self._history_limit :]),
            world=view,
        )
        try:
            judgment = self._dispatcher.classify(context)
        except ProviderError as error:
            self._append_review(
                error.code.value,
                error.metadata,
                world_id=view.world_id,
                turn=view.turn,
                phase=phase,
                routed=True,
            )
            if error.code is ProviderErrorCode.BUDGET_EXHAUSTED:
                self._providers_stopped = True
            return True
        except Exception:
            self._append_review(
                ProviderErrorCode.PROVIDER_UNAVAILABLE.value,
                {},
                world_id=view.world_id,
                turn=view.turn,
                phase=phase,
                routed=True,
            )
            return True

        metadata = judgment.metadata if isinstance(judgment, DispatchJudgment) else {}
        try:
            routed = dispatch_requires_review(
                judgment,
                has_alert=_has_critical_public_condition(view)
                or any(_is_alert(item) for item in events),
                escalation_threshold=self._escalation_threshold,
            )
        except Exception:
            self._append_review(
                ProviderErrorCode.MALFORMED_RESPONSE.value,
                metadata,
                world_id=view.world_id,
                turn=view.turn,
                phase=phase,
                routed=True,
            )
            return True
        self._append_review(
            None,
            metadata,
            world_id=view.world_id,
            turn=view.turn,
            phase=phase,
            routed=routed,
        )
        return routed

    def _captain_command(
        self, view: PublicWorldView, current: tuple[TradeEvidence, ...]
    ) -> TradeCommand | None:
        incident = _synthetic_incident(view)
        context = CaptainContext(
            incident=incident,
            station=_station_view(view),
            evidence=tuple(_as_evidence(item) for item in view.evidence),
            allowed_actions=_ACTION_CATALOG,
            inspection_budget_remaining=max(0, view.available_crew),
            instruction_version=INSTRUCTION_VERSION,
            events=tuple(_public_event(item) for item in current[-self._history_limit :]),
            world=view,
        )
        try:
            decision = self._captain.decide(context)
        except ProviderError as error:
            self._append_review(
                error.code.value,
                error.metadata,
                world_id=view.world_id,
                turn=view.turn,
                phase="captain",
                routed=True,
            )
            if error.code is ProviderErrorCode.BUDGET_EXHAUSTED:
                self._providers_stopped = True
            else:
                self._review_due = min(self._review_due, view.turn + 1)
            return None
        except Exception:
            self._append_review(
                ProviderErrorCode.PROVIDER_UNAVAILABLE.value,
                {},
                world_id=view.world_id,
                turn=view.turn,
                phase="captain",
                routed=True,
            )
            self._review_due = min(self._review_due, view.turn + 1)
            return None

        metadata = decision.metadata if isinstance(decision, CaptainDecision) else {}
        try:
            command = _command_from_decision(decision, view, self._next_command_id())
        except (TypeError, ValueError):
            self._append_review(
                ProviderErrorCode.MALFORMED_RESPONSE.value,
                metadata,
                world_id=view.world_id,
                turn=view.turn,
                phase="captain",
                routed=True,
            )
            self._review_due = min(self._review_due, view.turn + 1)
            return None
        if decision.action.kind is ActionRequestKind.DEFER:
            self._review_due = decision.action.follow_up_turn or self._review_due
        self._append_review(
            None,
            metadata,
            world_id=view.world_id,
            turn=view.turn,
            phase="captain",
            routed=True,
        )
        return command

    def _bounded_view(self, observation: PublicWorldView) -> PublicWorldView:
        return replace(observation, evidence=observation.evidence[-self._history_limit :])

    def _fresh_events(self, observation: PublicWorldView) -> tuple[TradeEvidence, ...]:
        if self._observed_turn < 0:
            fresh = observation.evidence
            self._observed_turn = observation.turn
            self._seen_this_turn.clear()
        elif observation.turn > self._observed_turn:
            self._observed_turn = observation.turn
            self._seen_this_turn.clear()
            fresh = tuple(item for item in observation.evidence if item.turn == observation.turn)
        else:
            fresh = tuple(
                item
                for item in observation.evidence
                if item.turn == observation.turn and _event_key(item) not in self._seen_this_turn
            )
        for item in fresh:
            self._mark_seen(item)
        return tuple(fresh)

    def _mark_seen(self, item: TradeEvidence) -> None:
        if item.turn == self._observed_turn:
            self._seen_this_turn.add(_event_key(item))

    def _next_command_id(self) -> str:
        self._command_number += 1
        return f"crew-{self._command_number}"

    def _record_local_failure(self, phase: str, turn: object) -> None:
        self._reviews.append(
            CrewReview(
                world_id="station",
                turn=turn if type(turn) is int else 0,
                phase=phase,
                routed=True,
                provider_error=ProviderErrorCode.INVALID_INPUT.value,
            )
        )

    def _append_review(
        self,
        error: str | None,
        metadata: Mapping[str, object],
        *,
        world_id: str,
        turn: int,
        phase: str,
        routed: bool,
    ) -> None:
        if not isinstance(metadata, Mapping):
            metadata = {}
        request_made = metadata.get("request_made")
        count = metadata.get("calls")
        if type(count) is int and count >= 0:
            model_calls = count
        elif type(request_made) is bool:
            model_calls = int(request_made)
        else:
            model_calls = 0
        cost = metadata.get("cost_usd")
        self._reviews.append(
            CrewReview(
                world_id=world_id,
                turn=turn,
                phase=phase,
                routed=routed,
                provider_error=error,
                model_calls=model_calls,
                provider=_safe_string(metadata.get("provider")),
                model=_safe_string(metadata.get("model")),
                input_tokens=_safe_count(metadata.get("input_tokens")),
                output_tokens=_safe_count(metadata.get("output_tokens")),
                cost_usd=(
                    float(cost)
                    if type(cost) in (int, float) and isfinite(cost) and cost >= 0
                    else None
                ),
            )
        )


def _command_from_decision(
    decision: object, view: PublicWorldView, command_id: str
) -> TradeCommand | None:
    if type(decision) is not CaptainDecision or type(decision.action) is not ActionRequest:
        raise ValueError("malformed captain response")
    request = decision.action
    if not isinstance(request.kind, ActionRequestKind):
        raise ValueError("unlisted captain action")
    descriptor = _ACTION_CATALOG_BY_KIND.get(request.kind)
    if descriptor is None:
        raise ValueError("unlisted captain action")
    _validate_request_fields(request, descriptor)
    if request.kind is ActionRequestKind.DEFER:
        if (
            request.follow_up_turn is None
            or request.follow_up_turn <= view.turn
            or request.follow_up_turn > _MAX_TURNS
        ):
            raise ValueError("invalid follow-up turn")
        return None

    batch_id = request.batch_id
    if request.kind is ActionRequestKind.PURCHASE:
        offer = next(
            (
                item
                for item in view.offers
                if item.batch_id == request.batch_id
                and item.seller_world == request.seller_id
                and item.seller_world != "station"
            ),
            None,
        )
        if (
            offer is None
            or request.quantity is None
            or request.quantity > min(_PURCHASE_MAX, offer.quantity)
        ):
            raise ValueError("purchase must name an available public offer")
        return TradeCommand(
            "purchase",
            command_id,
            buyer_id="station",
            seller_id=offer.seller_world,
            batch_id=offer.batch_id,
            quantity=request.quantity,
        )
    if request.kind in {
        ActionRequestKind.REPAIR,
        ActionRequestKind.ASSAY,
        ActionRequestKind.CONSUME,
        ActionRequestKind.QUARANTINE,
    } and not any(item.batch_id == batch_id for item in view.local_lots):
        raise ValueError("action must name a public local batch")
    match request.kind:
        case ActionRequestKind.REPAIR:
            return TradeCommand(
                "repair",
                command_id,
                buyer_id="station",
                batch_id=request.batch_id,
                shipment_id=request.shipment_id,
                repair_mode=request.repair_mode,
            )
        case ActionRequestKind.INSPECT:
            return TradeCommand("inspect", command_id, buyer_id="station", method=request.method)
        case ActionRequestKind.ACTIVATE_BACKUP:
            return TradeCommand("backup", command_id, buyer_id="station")
        case ActionRequestKind.ASSAY:
            return TradeCommand(
                "assay",
                command_id,
                buyer_id="station",
                batch_id=request.batch_id,
                shipment_id=request.shipment_id,
            )
        case ActionRequestKind.CONSUME:
            return TradeCommand(
                "consume",
                command_id,
                buyer_id="station",
                batch_id=request.batch_id,
                quantity=request.quantity,
                shipment_id=request.shipment_id,
            )
        case ActionRequestKind.QUARANTINE:
            return TradeCommand(
                "quarantine",
                command_id,
                buyer_id="station",
                batch_id=request.batch_id,
                quantity=request.quantity,
                shipment_id=request.shipment_id,
            )
        case ActionRequestKind.TRACE:
            return TradeCommand(
                "trace", command_id, buyer_id="station", report_id=request.report_id
            )
        case ActionRequestKind.CALIBRATE:
            return TradeCommand("calibrate", command_id, buyer_id="station")
        case ActionRequestKind.SET_LOAD:
            return TradeCommand(
                "load", command_id, buyer_id="station", operating_load=request.operating_load
            )
        case _:
            raise ValueError("unlisted captain action")


def _validate_request_fields(request: ActionRequest, descriptor: ActionDescriptor) -> None:
    schema = descriptor.json_schema
    properties = schema["properties"]
    required = set(schema["required"])
    values = {
        "target": request.target,
        "quantity": request.quantity,
        "follow_up_turn": request.follow_up_turn,
        "reason": request.reason,
        "evidence_sequences": request.evidence_sequences,
        "seller_id": request.seller_id,
        "batch_id": request.batch_id,
        "shipment_id": request.shipment_id,
        "method": request.method,
        "repair_mode": request.repair_mode,
        "report_id": request.report_id,
        "operating_load": request.operating_load,
    }
    for name, value in values.items():
        if name == "evidence_sequences" and type(value) is tuple and not value:
            continue
        if name not in properties:
            if value is not None:
                raise ValueError("unlisted action arguments")
            continue
        if value is None:
            if name in required:
                raise ValueError(f"required action argument {name} is missing")
            continue
        property_schema = properties[name]
        if property_schema["type"] == "string":
            if not isinstance(value, str):
                raise ValueError(f"invalid {name}")
            if len(value) < property_schema.get("minLength", 0):
                raise ValueError(f"invalid {name}")
            if "enum" in property_schema and value not in property_schema["enum"]:
                raise ValueError(f"invalid {name}")
        elif property_schema["type"] == "integer":
            if type(value) is not int:
                raise ValueError(f"invalid {name}")
            if value < property_schema.get("minimum", value):
                raise ValueError(f"invalid {name}")
            if value > property_schema.get("maximum", value):
                raise ValueError(f"invalid {name}")
        else:
            raise ValueError("unlisted action arguments")


def _event_key(item: TradeEvidence) -> tuple[object, ...]:
    return (
        item.turn,
        item.kind.value,
        item.message,
        item.finding_code,
        item.world_id,
        item.asset_id,
        item.batch_id,
        item.contract_id,
        item.shipment_id,
        item.report_id,
        item.source_id,
        item.upstream_report_id,
    )


def _is_alert(item: TradeEvidence) -> bool:
    return item.kind in {
        TradeEvidenceKind.FAILURE,
        TradeEvidenceKind.COMMAND_REJECTED,
    } or item.finding_code in {
        "material_defect_confirmed",
        "residual_damage_confirmed",
        "sensor_drift_confirmed",
        "oxygen_critical",
        "oxygen_exhausted",
        "active_leak",
    }


def _has_critical_public_condition(view: PublicWorldView) -> bool:
    return any(reading.oxygen <= CRITICAL_OXYGEN for reading in view.oxygen_sensors)


def _as_evidence(item: TradeEvidence) -> Evidence:
    try:
        code = EvidenceCode(item.finding_code) if item.finding_code is not None else None
    except ValueError:
        code = None
    return Evidence(item.sequence, item.turn, item.kind.value, item.message, code)


def _public_event(item: TradeEvidence) -> PublicEvent:
    return PublicEvent(item.sequence, item.turn, item.kind.value, item.message)


def _station_view(view: PublicWorldView) -> StationView:
    return StationView(
        turn=view.turn,
        oxygen_sensors=view.oxygen_sensors,
        backup_oxygen=view.backup_oxygen,
        parts=view.parts,
        credits=view.credits,
        available_crew=view.available_crew,
    )


def _synthetic_incident(view: PublicWorldView) -> Evidence:
    return Evidence(
        0,
        view.turn,
        "mission_priorities",
        "Mission priorities: protect crew survival, restore reliable station services, "
        "verify claims with public evidence, and conserve station credits.",
    )


def _observation_event(view: PublicWorldView, *, phase: str) -> TradeEvidence:
    return TradeEvidence(
        sequence=0,
        turn=view.turn,
        kind=TradeEvidenceKind.STATION_EVIDENCE,
        message=f"Public station {phase} observation at turn {view.turn}.",
        world_id="station",
        finding_code="public_observation",
    )


def _safe_string(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 160 else None


def _safe_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _schema(
    properties: Mapping[str, object] | None = None, required: tuple[str, ...] = ()
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required),
        "additionalProperties": False,
    }


_ACTION_CATALOG = (
    ActionDescriptor(
        ActionRequestKind.PURCHASE,
        (
            "Buy a public market offer for the station. The buyer and command ID are assigned "
            "by station operations."
        ),
        _schema(
            {
                "seller_id": {"type": "string", "minLength": 1},
                "batch_id": {"type": "string", "minLength": 1},
                "quantity": {"type": "integer", "minimum": 1, "maximum": _PURCHASE_MAX},
            },
            ("seller_id", "batch_id", "quantity"),
        ),
    ),
    ActionDescriptor(
        ActionRequestKind.REPAIR,
        (
            "Start a two-turn repair with an available delivered part batch. Reserve crew and "
            "confirm the batch is in station inventory."
        ),
        _schema(
            {
                "batch_id": {"type": "string", "minLength": 1},
                "shipment_id": {"type": "string", "minLength": 1},
                "repair_mode": {"type": "string", "enum": ["full", "stabilize"]},
            },
            ("batch_id", "repair_mode"),
        ),
    ),
    ActionDescriptor(
        ActionRequestKind.INSPECT,
        (
            "Inspect the installed oxygen-system part under routine, peak, or active-backup "
            "conditions. A stressed inspection can reveal a load-dependent failure."
        ),
        _schema({"method": {"type": "string", "enum": ["routine", "peak", "backup"]}}, ("method",)),
    ),
    ActionDescriptor(
        ActionRequestKind.ACTIVATE_BACKUP,
        "Activate the station backup oxygen supply while the primary system is being assessed.",
        _schema(),
    ),
    ActionDescriptor(
        ActionRequestKind.ASSAY,
        (
            "Consume one delivered feedstock sample to measure its public quality. This does "
            "not change the full batch's quality."
        ),
        _schema(
            {
                "batch_id": {"type": "string", "minLength": 1},
                "shipment_id": {"type": "string", "minLength": 1},
            },
            ("batch_id",),
        ),
    ),
    ActionDescriptor(
        ActionRequestKind.CONSUME,
        "Consume oxygen feedstock already in station inventory. Only oxygen feedstock is accepted.",
        _schema(
            {
                "batch_id": {"type": "string", "minLength": 1},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 3},
                "shipment_id": {"type": "string", "minLength": 1},
            },
            ("batch_id", "quantity"),
        ),
    ),
    ActionDescriptor(
        ActionRequestKind.QUARANTINE,
        (
            "Quarantine unused material from a delivered station batch so it cannot be "
            "selected for later use."
        ),
        _schema(
            {
                "batch_id": {"type": "string", "minLength": 1},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 3},
                "shipment_id": {"type": "string", "minLength": 1},
            },
            ("batch_id", "quantity"),
        ),
    ),
    ActionDescriptor(
        ActionRequestKind.TRACE,
        (
            "Trace one publicly visible report to its immediate source. Further provenance "
            "requires another trace action."
        ),
        _schema({"report_id": {"type": "string", "minLength": 1}}, ("report_id",)),
    ),
    ActionDescriptor(
        ActionRequestKind.CALIBRATE,
        "Use crew to compare independent oxygen sensors and recalibrate sensor A.",
        _schema(),
    ),
    ActionDescriptor(
        ActionRequestKind.SET_LOAD,
        "Set station operating load to routine or peak for the next station turn.",
        _schema(
            {"operating_load": {"type": "string", "enum": ["routine", "peak"]}}, ("operating_load",)
        ),
    ),
    ActionDescriptor(
        ActionRequestKind.DEFER,
        "Defer the next review until the specified mission turn.",
        _schema(
            {"follow_up_turn": {"type": "integer", "minimum": 1, "maximum": _MAX_TURNS}},
            ("follow_up_turn",),
        ),
    ),
)
_ACTION_CATALOG_BY_KIND = {action.kind: action for action in _ACTION_CATALOG}
