"""Authored reports carry claims; only investigations disclose their provenance."""

from __future__ import annotations

import re
from dataclasses import replace

from .trade_types import TradeReport as TradeReport
from .trade_types import TradeResult, WorldAdvanceResult, WorldState


def validate_reports(reports: tuple[TradeReport, ...], world_ids: set[str]) -> None:
    """An authored finite provenance graph, with no executable scenario expressions."""
    identity = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
    if not isinstance(reports, tuple) or any(
        not isinstance(report, TradeReport) for report in reports
    ):
        raise ValueError("Reports must be typed immutable records")
    if len(reports) > 256:
        raise ValueError("Reports require bounded unique IDs")
    for report in reports:
        if (
            any(
                not isinstance(value, str) or not identity.fullmatch(value)
                for value in (report.report_id, report.source_id)
            )
            or not isinstance(report.recipient_world, str)
            or report.recipient_world not in world_ids
            or not isinstance(report.message, str)
            or not 1 <= len(report.message) <= 1000
            or type(report.observed_turn) is not int
            or type(report.receipt_turn) is not int
            or not 0 <= report.observed_turn <= report.receipt_turn <= 336
            or report.receipt_turn == 0
            or any(
                value is not None and (not isinstance(value, str) or not identity.fullmatch(value))
                for value in (report.batch_id, report.asset_id, report.upstream_report_id)
            )
        ):
            raise ValueError("Invalid report configuration")
    if len({report.report_id for report in reports}) != len(reports):
        raise ValueError("Reports require bounded unique IDs")
    indexed = {report.report_id: report for report in reports}
    for report in reports:
        seen = {report.report_id}
        current = report
        while current.upstream_report_id is not None:
            upstream = indexed.get(current.upstream_report_id)
            if upstream is None or upstream.report_id in seen:
                raise ValueError("Report provenance must be an acyclic known chain")
            if (
                upstream.observed_turn > current.observed_turn
                or upstream.receipt_turn > current.receipt_turn
            ):
                raise ValueError("Report provenance cannot refer to future observations")
            if any(
                left is not None and right is not None and left != right
                for left, right in (
                    (current.asset_id, upstream.asset_id),
                    (current.batch_id, upstream.batch_id),
                )
            ):
                raise ValueError("Report provenance must concern the same asset and batch")
            seen.add(upstream.report_id)
            current = upstream


def publish_due_reports(state: WorldState) -> WorldAdvanceResult:
    from .trade_types import TradeEvidenceKind
    from .trade_types import record_evidence as _emit_evidence

    if not state.station.crew_alive:
        return WorldAdvanceResult(state)
    validate_reports(state.reports, {"station", *(world.world_id for world in state.inventories)})
    published = {item.report_id for item in state.evidence if item.kind == TradeEvidenceKind.REPORT}
    emitted = []
    updated = state
    for report in sorted(state.reports, key=lambda item: item.report_id):
        if report.receipt_turn != state.station.turn or report.report_id in published:
            continue
        updated, evidence = _emit_evidence(
            updated,
            TradeEvidenceKind.REPORT,
            report.message,
            report.recipient_world,
            source_id=report.source_id,
            report_id=report.report_id,
            observed_turn=report.observed_turn,
            asset_id=report.asset_id,
            batch_id=report.batch_id,
            finding_code="unverified_report",
        )
        emitted.extend(evidence)
    return WorldAdvanceResult(updated, tuple(emitted))


def trace_report(state: WorldState, report_id: str, *, world_id: str = "station") -> TradeResult:
    from .trade_types import TradeEvidenceKind
    from .trade_types import record_evidence as _emit_evidence
    from .trade_types import reject_trade as _rejected

    if not state.station.crew_alive:
        return _rejected(state, "crew_lost")
    if not isinstance(report_id, str) or not report_id:
        return _rejected(state, "invalid_report_id")
    validate_reports(state.reports, {"station", *(world.world_id for world in state.inventories)})
    if any(
        item.world_id == world_id
        and item.kind == TradeEvidenceKind.TRACE
        and item.report_id == report_id
        for item in state.evidence
    ):
        return _rejected(state, "report_already_traced")
    visible = {
        value
        for item in state.evidence
        if item.world_id == world_id
        for value in (item.report_id, item.upstream_report_id)
        if value is not None
    }
    report = next((item for item in state.reports if item.report_id == report_id), None)
    if report is None or report_id not in visible or report.receipt_turn > state.station.turn:
        return _rejected(state, "report_not_visible")
    if world_id == "station" and state.station.available_crew <= 0:
        return _rejected(state, "crew_unavailable")
    upstream = next(
        (item for item in state.reports if item.report_id == report.upstream_report_id), None
    )
    if report.upstream_report_id is not None and (
        upstream is None or upstream.receipt_turn > state.station.turn
    ):
        return _rejected(state, "report_source_unavailable")
    updated = (
        replace(
            state, station=replace(state.station, available_crew=state.station.available_crew - 1)
        )
        if world_id == "station"
        else state
    )
    message = (
        f"Report {report.report_id} repeats {upstream.report_id} from {upstream.source_id}; "
        "this is a shared source, not independent verification."
        if upstream
        else f"Report {report.report_id} originates at {report.source_id}; claim unverified."
    )
    updated, evidence = _emit_evidence(
        updated,
        TradeEvidenceKind.TRACE,
        message,
        world_id,
        asset_id=report.asset_id,
        batch_id=report.batch_id,
        report_id=report.report_id,
        source_id=report.source_id,
        observed_turn=report.observed_turn,
        upstream_source_id=upstream.source_id if upstream else None,
        upstream_report_id=upstream.report_id if upstream else None,
        finding_code="report_provenance_traced" if upstream else "report_origin_traced",
    )
    return TradeResult(updated, True, evidence=evidence)
