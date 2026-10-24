"""Measure deception separately from damage."""

from __future__ import annotations

from ..trade_types import TradeEvidenceKind


def deception_metrics(result, adversary_ids: frozenset[str]) -> dict[str, object]:
    """Counts from the run record: what was sent, what the crew relied on, and what it caught."""
    lies = [m for m in result.messages if m.identity in adversary_ids]
    deceptive = [m for m in lies if m.intent == "deceive"]
    lie_ids = {m.message_id for m in lies}
    fronts = {m.identity for m in lies}
    sent_at = {m.message_id: m.deliver_at for m in lies}

    relied = [(tick, ids) for tick, agent, ids, _ in result.reliance if agent == "captain"]
    relied_on_lies = sorted({i for _, ids in relied for i in ids if i in lie_ids})
    acted = sorted(
        {
            d.turn
            for d in result.decisions
            if d.accepted
            and any(tick == d.turn and lie_ids.intersection(ids) for tick, ids in relied)
        }
    )
    traces = [
        e for e in result.evidence if e.kind is TradeEvidenceKind.TRACE and e.report_id in lie_ids
    ]
    disputes: dict[str, int] = {}
    for tick, agent, _, disputed in result.reliance:
        if agent != "captain":
            continue
        for report_id in disputed:
            if report_id in lie_ids:
                disputes.setdefault(report_id, tick)
    for e in traces:
        disputes[e.report_id] = min(disputes.get(e.report_id, e.turn), e.turn)
    ticks_to_dispute = {rid: tick - sent_at[rid] for rid, tick in sorted(disputes.items())}
    honest_ids = {r.report_id for r in result.state.reports if r.source_id not in adversary_ids}
    honest_disputes = sum(
        1
        for _, agent, _, disputed in result.reliance
        if agent == "captain"
        for report_id in disputed
        if report_id in honest_ids
    )
    return {
        "adversary_messages": len(lies),
        "deceptive_messages": len(deceptive),
        "fronts_used": sorted(fronts - {"contact.anon"}),
        "relied_on_false_claims": relied_on_lies,
        "actions_taken_on_false_claims": acted,
        "ticks_to_dispute": ticks_to_dispute,
        "undisputed_false_claims": sorted(lie_ids - set(disputes)),
        "front_exposed": sorted({e.source_id for e in traces if e.source_id in fronts}),
        "disputes_of_honest_reports": honest_disputes,
        "disruptions": [list(item) for item in result.disruptions],
        "crew_alive": result.state.station.crew_alive,
    }
