"""Offline deceptive and benign stories built through the parameterized facade."""

from __future__ import annotations

from dataclasses import replace

from .deception import TradeReport
from .facade import SimulationFacade
from .governors import TradeCommand
from .scenarios import create_world
from .trade import PartLot
from .trade_mission import TradeMissionResult, trade_story_summary

DECEPTION_STORIES = ("supply_chain", "incomplete_repair", "resource_diversion", "benign")


def create_deception_simulation(
    story: str = "supply_chain", *, policy: str = "investigate"
) -> SimulationFacade:
    if story not in DECEPTION_STORIES or policy not in {"investigate", "trust"}:
        raise ValueError("Unknown deception story or policy")
    diversion = story == "resource_diversion"
    faulty_parts = story in {"supply_chain", "resource_diversion"}
    station = replace(
        create_world("normal", 0),
        scheduled_events=(),
        parts=0,
        leak_active=True,
        credits=100 if diversion else 200,
    )
    simulation = SimulationFacade(station)
    simulation.create_world(
        "industrial",
        credits=1000,
        lots=(
            PartLot(
                "industrial-batch-a",
                2,
                15,
                "industrial",
                latent_defect=faulty_parts,
                defect_after_turns=8 if diversion else 2 if faulty_parts else 0,
                failure_load="any" if diversion else "peak",
            ),
            PartLot("industrial-batch-b", 2, 30, "industrial"),
        ),
    )
    simulation.create_world(
        "ice_moon",
        credits=1000,
        lots=(
            PartLot(
                "ice-feed-a",
                2,
                10,
                "ice_moon",
                resource="oxygen_feedstock",
                unit="canisters",
                yield_percent=50 if story == "supply_chain" else 100,
            ),
            PartLot("ice-feed-b", 2, 20, "ice_moon", resource="oxygen_feedstock", unit="canisters"),
            PartLot("port-panels", 6, 15, "ice_moon", resource="panels", unit="panels"),
        ),
    )
    reports = (
        TradeReport(
            "supplier-check",
            "industrial.qa",
            "industrial",
            "The batch passed a basic routine check.",
            0,
            1,
            batch_id="industrial-batch-a",
            asset_id="oxygen_system",
        ),
        TradeReport(
            "port-manifest",
            "port.office",
            "station",
            "The listed parts are certified for operation.",
            0,
            3,
            batch_id="industrial-batch-a",
            asset_id="oxygen_system",
            upstream_report_id="supplier-check",
        ),
        TradeReport(
            "maintenance-note",
            "maintenance.office",
            "station",
            "Supplier and port agree: the replacement is reliable.",
            0,
            3,
            batch_id="industrial-batch-a",
            asset_id="oxygen_system",
            upstream_report_id="port-manifest",
        ),
    )
    if diversion:
        reports += (
            TradeReport(
                "port-request",
                "port.office",
                "station",
                "Urgent: purchase three panel units for port refurbishment.",
                0,
                1,
            ),
        )
    simulation.configure_deception(
        reports=reports,
        sensor_drift_per_turn=2 if story == "supply_chain" else 0,
        residual_damage_after_turns=2 if story == "incomplete_repair" else 0,
    )
    if policy == "investigate":
        simulation.create_decision_system(
            "station",
            kind="investigation",
            emergency_reserve=60,
            initial_repair_mode="stabilize" if story == "incomplete_repair" else "full",
            feedstock_target=2 if story in {"supply_chain", "benign"} else 0,
        )
    else:
        # Deliberately unsafe scripted baseline. This is not an AI performance claim.
        commands = (
            (
                1,
                TradeCommand(
                    "purchase", "trusted-parts", batch_id="industrial-batch-a", quantity=2
                ),
            ),
            (
                4,
                TradeCommand(
                    "repair",
                    batch_id="industrial-batch-a",
                    repair_mode="stabilize" if story == "incomplete_repair" else "full",
                ),
            ),
            (6, TradeCommand("inspect")),
            (7, TradeCommand("load", operating_load="peak")),
        )
        if story in {"supply_chain", "benign"}:
            commands += (
                (
                    8,
                    TradeCommand(
                        "purchase",
                        "trusted-feedstock",
                        seller_id="ice_moon",
                        batch_id="ice-feed-a",
                        quantity=2,
                    ),
                ),
                (11, TradeCommand("consume", batch_id="ice-feed-a", quantity=2)),
            )
        if diversion:
            commands = (
                (
                    1,
                    TradeCommand(
                        "purchase", "panel-1", seller_id="ice_moon", batch_id="port-panels"
                    ),
                ),
                (
                    2,
                    TradeCommand(
                        "purchase", "panel-2", seller_id="ice_moon", batch_id="port-panels"
                    ),
                ),
                (
                    3,
                    TradeCommand(
                        "purchase", "panel-3", seller_id="ice_moon", batch_id="port-panels"
                    ),
                ),
                (
                    4,
                    TradeCommand(
                        "purchase", "parts-after-panels", batch_id="industrial-batch-a", quantity=2
                    ),
                ),
                (7, TradeCommand("repair", batch_id="industrial-batch-a")),
                (18, TradeCommand("purchase", "emergency-parts", batch_id="industrial-batch-b")),
            )
        simulation.create_decision_system("station", commands=commands)
    simulation.create_decision_system("industrial")
    simulation.create_decision_system("ice_moon")
    return simulation


def run_deception_story(
    *, story: str = "supply_chain", policy: str = "investigate", turns: int = 40
) -> TradeMissionResult:
    return create_deception_simulation(story, policy=policy).run(turns=turns)


def deception_story_summary(
    result: TradeMissionResult, *, story: str, policy: str
) -> dict[str, object]:
    summary = trade_story_summary(result)
    summary.update(
        {
            "controller": "rules" if policy == "investigate" else "scripted",
            "deception": story,
            "policy": policy,
            "credits_remaining": result.state.station.credits,
            "expenditure": sum(
                -entry.delta
                for entry in result.state.ledger
                if entry.account == "credits:station" and entry.kind == "purchase"
            ),
            "failures_observed": sum(item.kind == "failure" for item in result.events),
            "material_findings": sum(
                item.code == "material_defect_confirmed" for item in result.events
            ),
            "residual_damage_findings": sum(
                item.code == "residual_damage_confirmed" for item in result.events
            ),
            "original_sources_discovered": len(
                {item.source_id for item in result.events if item.code == "report_origin_traced"}
            ),
        }
    )
    return summary
