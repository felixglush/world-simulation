"""Refresh bundled source and deterministic demo runs from the real Python simulator.

The browser replays these facts; it does not reimplement simulation rules or call models.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from station_control.adversary import (  # noqa: E402
    AdversaryAction,
    AdversaryActionKind,
    AdversaryDecision,
    AdversaryMode,
)
from station_control.application import (  # noqa: E402
    SIMULATOR_VERSION,
    ControllerMode,
    EvidenceAccess,
    MissionConfig,
    mission_metadata,
    run_mission,
)
from station_control.controllers import (  # noqa: E402
    ActionRequest,
    ActionRequestKind,
    CaptainDecision,
    DispatchJudgment,
    NoulOutcome,
    Subsystem,
)
from station_control.domain import Evidence, EvidenceCode  # noqa: E402
from station_control.persistence import RunLogWriter, read_run_log  # noqa: E402
from station_control.scenarios import ScenarioDefinition  # noqa: E402

ARCH = ROOT / "architecture"


def source_bundle():
    model = json.loads((ARCH / "src/model.json").read_text())
    refs = [ref for node in model["components"] for ref in node["sources"]]
    refs += list(model["contracts"].values())
    output = {}
    for ref in refs:
        path, symbol = ref["path"], ref["symbol"]
        text = (ROOT / path).read_text()
        tree = ast.parse(text)
        node = next((node for node in tree.body if getattr(node, "name", None) == symbol), None)
        if node is None:
            raise ValueError(f"Missing source symbol: {path}:{symbol}")
        start = min([node.lineno, *[item.lineno for item in getattr(node, "decorator_list", [])]])
        fields = [
            {"name": child.target.id, "type": ast.unparse(child.annotation)}
            for child in getattr(node, "body", [])
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
        ]
        output[f"{path}:{symbol}"] = {
            "path": path,
            "symbol": symbol,
            "startLine": start,
            "endLine": node.end_lineno,
            "code": "\n".join(text.splitlines()[start - 1 : node.end_lineno]),
            "fields": fields,
            "fileHash": hashlib.sha256(text.encode()).hexdigest(),
        }
    return {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "simulatorVersion": SIMULATOR_VERSION,
        "sources": output,
    }


class DemoAdversary:
    def __init__(self, deceptive):
        self.deceptive = deceptive

    def decide(self, context):
        kind = AdversaryActionKind.WAIT
        if context.turn == 0:
            kind = AdversaryActionKind.START_SILENT_LEAK
        elif context.turn == 1 and self.deceptive:
            kind = AdversaryActionKind.DECEPTIVE_REPORT
        return AdversaryDecision(AdversaryAction(kind), rationale="Scripted demo: " + kind.value)


class DemoJev:
    def classify(self, context):
        # Scripted judgment over public inputs only; never consult hidden state.
        evidence = [item for item in context.evidence if isinstance(item, Evidence)]
        latest_finding = next(
            (item for item in reversed(evidence) if item.kind in {"inspection", "repair_complete"}),
            None,
        )
        repaired = latest_finding is not None and (
            latest_finding.kind == "repair_complete"
            or latest_finding.code is EvidenceCode.OXYGEN_HEALTHY
        )
        uncertain = any(
            "blames" in event.payload or "sensor reading" in event.payload
            for event in context.events
        )
        return DispatchJudgment(
            subsystem=Subsystem.LIFE_SUPPORT,
            safeguard_request=NoulOutcome.NO,
            diagnosis_supported=NoulOutcome.UNCERTAIN if uncertain else NoulOutcome.YES,
            urgency=0 if repaired and not uncertain else 75,
            rationale="Scripted demo: verify the diagnosis."
            if uncertain
            else "Scripted demo: maintenance confirmed."
            if repaired
            else "Scripted demo: investigate declining oxygen.",
            metadata={"model": "scripted-demo-jev", "calls": 0},
        )


class DemoCaptain:
    def decide(self, context):
        evidence = [item for item in context.evidence if isinstance(item, Evidence)]
        finding = next(
            (item for item in reversed(evidence) if item.kind in {"inspection", "repair_complete"}),
            None,
        )
        if finding and (
            finding.kind == "repair_complete" or finding.code is EvidenceCode.OXYGEN_HEALTHY
        ):
            action = ActionRequest(
                ActionRequestKind.CLOSE,
                reason="Trusted current evidence supports closure.",
                evidence_sequences=(finding.sequence,),
            )
        elif finding and finding.code is EvidenceCode.ACTIVE_LEAK:
            assigned = any(
                item.code is EvidenceCode.REPAIR_ASSIGNED and item.sequence > finding.sequence
                for item in evidence
            )
            action = (
                ActionRequest(ActionRequestKind.DEFER, follow_up_turn=context.station.turn + 1)
                if assigned
                else ActionRequest(ActionRequestKind.ASSIGN_REPAIR, target="oxygen_system")
            )
        else:
            action = ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system")
        return CaptainDecision(
            action,
            rationale="Scripted demo captain follows public findings.",
            metadata={"model": "scripted-demo-captain", "calls": 0},
        )


def demo_run(deceptive):
    run_id = "demo_deception" if deceptive else "demo_silent_leak"
    config = MissionConfig(
        scenario_definition=ScenarioDefinition(
            id=run_id, description="Offline architecture demonstration", initial={}, events=()
        ),
        seed=41,
        duration_turns=5,
        controller_mode=ControllerMode.JEV_LLM,
        evidence_access=EvidenceAccess.HISTORY,
        adversary_mode=AdversaryMode.LLM,
        run_id=run_id,
    )
    path = ARCH / "test-results" / f"{run_id}.jsonl"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    writer = RunLogWriter(
        path, mission_metadata(config), run_id=run_id, simulator_version=SIMULATOR_VERSION
    )
    result = run_mission(
        config,
        captain=DemoCaptain(),
        dispatcher=DemoJev(),
        adversary=DemoAdversary(deceptive),
        event_sink=writer.write_event,
    )
    assert not any(event["event_type"] == "provider_failure" for event in result.events)
    assert any(event["event_type"] == "incident_closed" for event in result.events)
    writer.finish(
        metrics=dict(result.evaluation.metrics),
        debug_snapshots=result.debug_snapshots,
        turns_completed=result.turns_completed,
        status=result.status.value,
    )
    return {
        "id": run_id,
        "title": "Silent leak + deceptive report" if deceptive else "Silent leak → repair",
        "description": "A scripted adversary hides a leak, then blames the sensor."
        if deceptive
        else "A scripted adversary starts a leak without emitting an alert.",
        "provenance": "Real simulator records · scripted AI providers · zero model calls",
        "records": read_run_log(path),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    bundle = source_bundle()
    source_path = ARCH / "src/source-index.json"
    if args.check:
        saved = json.loads(source_path.read_text())
        if (
            saved["sources"] != bundle["sources"]
            or saved["simulatorVersion"] != bundle["simulatorVersion"]
        ):
            raise SystemExit("Architecture source snapshots are stale; run the generator.")
        expected_runs = [demo_run(False), demo_run(True)]
        saved_runs = json.loads((ARCH / "src/demo-runs.json").read_text())
        if saved_runs != expected_runs:
            raise SystemExit("Architecture demo fixtures are stale; run the generator.")
        print(f"Verified {len(bundle['sources'])} source references and both demo runs.")
        return
    source_path.write_text(json.dumps(bundle, indent=2) + "\n")
    runs = [demo_run(False), demo_run(True)]
    (ARCH / "src/demo-runs.json").write_text(json.dumps(runs, indent=2) + "\n")
    print(
        f"Bundled {len(bundle['sources'])} source symbols and {len(runs)} validated offline runs."
    )


if __name__ == "__main__":
    main()
